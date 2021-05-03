# This file is part of QuTiP: Quantum Toolbox in Python.
#
#    Copyright (c) 2011 and later, Paul D. Nation and Robert J. Johansson.
#    All rights reserved.
#
#    Redistribution and use in source and binary forms, with or without
#    modification, are permitted provided that the following conditions are
#    met:
#
#    1. Redistributions of source code must retain the above copyright notice,
#       this list of conditions and the following disclaimer.
#
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#
#    3. Neither the name of the QuTiP: Quantum Toolbox in Python nor the names
#       of its contributors may be used to endorse or promote products derived
#       from this software without specific prior written permission.
#
#    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
#    PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
#    HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#    SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
#    LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#    DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
#    THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#    OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
###############################################################################
from itertools import product
from functools import reduce
from operator import mul

import warnings
import numpy as np
import pytest

import qutip
from qutip_qip.circuit import Gate, QubitCircuit
from qutip_qip.operations.gates import gate_sequence_product
from qutip_qip.device import (
    DispersiveCavityQED, LinearSpinChain, CircularSpinChain, SCQubits
)

_tol = 2.e-2

_x = Gate("X", targets=[0])
_z = Gate("Z", targets=[0])
_y = Gate("Y", targets=[0])
_snot = Gate("SNOT", targets=[0])
_rz = Gate("RZ", targets=[0], arg_value=np.pi/2, arg_label=r"\pi/2")
_rx = Gate("RX", targets=[0], arg_value=np.pi/2, arg_label=r"\pi/2")
_ry = Gate("RY", targets=[0], arg_value=np.pi/2, arg_label=r"\pi/2")
_iswap = Gate("ISWAP", targets=[0, 1])
_cnot = Gate("CNOT", targets=[0], controls=[1])
_sqrt_iswap = Gate("SQRTISWAP", targets=[0, 1])


single_gate_tests = [
    pytest.param(2, [_z], id="Z"),
    pytest.param(2, [_x], id="X"),
    pytest.param(2, [_y], id="Y"),
    pytest.param(2, [_snot], id="SNOT"),
    pytest.param(2, [_rz], id="RZ"),
    pytest.param(2, [_rx], id="RX"),
    pytest.param(2, [_ry], id="RY"),
    pytest.param(2, [_iswap], id="ISWAP"),
    pytest.param(2, [_sqrt_iswap], id="SQRTISWAP", marks=pytest.mark.skip),
    pytest.param(2, [_cnot], id="CNOT"),
]


def _ket_expaned_dims(qubit_state, expanded_dims):
    all_qubit_basis = list(product([0, 1], repeat=len(expanded_dims)))
    old_dims = qubit_state.dims[0]
    expanded_qubit_state = np.zeros(
        reduce(mul, expanded_dims, 1), dtype=np.complex128)
    for basis_state in all_qubit_basis:
        old_ind = np.ravel_multi_index(basis_state, old_dims)
        new_ind = np.ravel_multi_index(basis_state, expanded_dims)
        expanded_qubit_state[new_ind] = qubit_state[old_ind, 0]
    expanded_qubit_state.reshape((reduce(mul, expanded_dims, 1), 1))
    return qutip.Qobj(
        expanded_qubit_state, dims=[expanded_dims, [1]*len(expanded_dims)])


device_lists_analytic = [
    pytest.param(DispersiveCavityQED, {"g":0.1}, id = "DispersiveCavityQED"),
    pytest.param(LinearSpinChain, {}, id = "LinearSpinChain"),
    pytest.param(CircularSpinChain, {}, id = "CircularSpinChain"),
]

device_lists_numeric = device_lists_analytic + [
    # Does not support global phase
    pytest.param(SCQubits, {}, id = "SCQubits"),
]


@pytest.mark.parametrize(("num_qubits", "gates"), single_gate_tests)
@pytest.mark.parametrize(("device_class", "kwargs"), device_lists_analytic)
def test_device_against_gate_sequence(
    num_qubits, gates, device_class, kwargs):
    circuit = QubitCircuit(num_qubits)
    for gate in gates:
        circuit.add_gate(gate)
    U_ideal = gate_sequence_product(circuit.propagators())

    device = device_class(num_qubits)
    U_physical = gate_sequence_product(device.run(circuit))
    assert (U_ideal - U_physical).norm() < _tol


@pytest.mark.parametrize(("num_qubits", "gates"), single_gate_tests)
@pytest.mark.parametrize(("device_class", "kwargs"), device_lists_analytic)
def test_analytical_evolution(num_qubits, gates, device_class, kwargs):
    circuit = QubitCircuit(num_qubits)
    for gate in gates:
        circuit.add_gate(gate)
    state = qutip.rand_ket(2**num_qubits)
    state.dims = [[2]*num_qubits, [1]*num_qubits]
    ideal = gate_sequence_product([state] + circuit.propagators())
    device = device_class(num_qubits)
    operators = device.run_state(init_state=state, qc=circuit, analytical=True)
    result = gate_sequence_product(operators)
    assert abs(qutip.metrics.fidelity(result, ideal) - 1) < _tol


@pytest.mark.filterwarnings("ignore:Not in the dispersive regime")
@pytest.mark.parametrize(("num_qubits", "gates"), single_gate_tests)
@pytest.mark.parametrize(("device_class", "kwargs"), device_lists_numeric)
def test_numerical_evolution(
    num_qubits, gates, device_class, kwargs):
    num_qubits = 2
    circuit = QubitCircuit(num_qubits)
    for gate in gates:
        circuit.add_gate(gate)
    device = device_class(num_qubits, **kwargs)
    device.load_circuit(circuit)

    state = qutip.rand_ket(2**num_qubits)
    state.dims = [[2]*num_qubits, [1]*num_qubits]
    target = gate_sequence_product([state] + circuit.propagators())
    if isinstance(device, DispersiveCavityQED):
        num_ancilla = len(device.dims)-num_qubits
        ancilla_indices = slice(0, num_ancilla)
        extra = qutip.basis(device.dims[ancilla_indices], [0]*num_ancilla)
        init_state = qutip.tensor(extra, state)
    elif isinstance(device, SCQubits):
        # expand to 3-level represetnation
        init_state = _ket_expaned_dims(state, device.dims)
    else:
        init_state = state
    options = qutip.Options(store_final_state=True, nsteps=50_000)
    result = device.run_state(init_state=init_state,
                              analytical=False,
                              options=options)
    numerical_result = result.final_state
    if isinstance(device, DispersiveCavityQED):
        target = qutip.tensor(extra, target)
    elif isinstance(device, SCQubits):
        target = _ket_expaned_dims(target, device.dims)
    assert _tol > abs(1 - qutip.metrics.fidelity(numerical_result, target))


circuit = QubitCircuit(3)
circuit.add_gate("RX", targets=[0], arg_value=np.pi/2)
circuit.add_gate("RZ", targets=[2], arg_value=np.pi)
circuit.add_gate("CNOT", targets=[0], controls=[1])
circuit.add_gate("ISWAP", targets=[2, 1])
circuit.add_gate("Y", targets=[2])
circuit.add_gate("Z", targets=[0])
circuit.add_gate("CNOT", targets=[0], controls=[2])
circuit.add_gate("Z", targets=[1])
circuit.add_gate("X", targets=[1])

from copy import deepcopy
circuit2 = deepcopy(circuit)
circuit2.add_gate("SQRTISWAP", targets=[0, 2])  # supported only by SpinChain


@pytest.mark.filterwarnings("ignore:Not in the dispersive regime")
@pytest.mark.parametrize(("circuit", "device_class", "kwargs"), [
    pytest.param(circuit, DispersiveCavityQED, {"g":0.1}, id = "DispersiveCavityQED"),
    pytest.param(circuit2, LinearSpinChain, {}, id = "LinearSpinChain"),
    pytest.param(circuit2, CircularSpinChain, {}, id = "CircularSpinChain"),
    # The length of circuit is limited for SCQubits due to leakage
    pytest.param(circuit, SCQubits, {"omega_single":[0.003]*3}, id = "SCQubits"),
])
@pytest.mark.parametrize(("schedule_mode"), ["ASAP", "ALAP", None])
def test_numerical_circuit(circuit, device_class, kwargs, schedule_mode):
    num_qubits = circuit.N
    with warnings.catch_warnings(record=True):
        device = device_class(circuit.N, **kwargs)
    device.load_circuit(circuit, schedule_mode=schedule_mode)

    state = qutip.rand_ket(2**num_qubits)
    state.dims = [[2]*num_qubits, [1]*num_qubits]
    target = gate_sequence_product([state] + circuit.propagators())
    if isinstance(device, DispersiveCavityQED):
        num_ancilla = len(device.dims)-num_qubits
        ancilla_indices = slice(0, num_ancilla)
        extra = qutip.basis(device.dims[ancilla_indices], [0]*num_ancilla)
        init_state = qutip.tensor(extra, state)
    elif isinstance(device, SCQubits):
        # expand to 3-level represetnation
        init_state = _ket_expaned_dims(state, device.dims)
    else:
        init_state = state
    options = qutip.Options(store_final_state=True, nsteps=50_000)
    result = device.run_state(init_state=init_state,
                              analytical=False,
                              options=options)
    if isinstance(device, DispersiveCavityQED):
        target = qutip.tensor(extra, target)
    elif isinstance(device, SCQubits):
        target = _ket_expaned_dims(target, device.dims)
    assert _tol > abs(1 - qutip.metrics.fidelity(result.final_state, target))
