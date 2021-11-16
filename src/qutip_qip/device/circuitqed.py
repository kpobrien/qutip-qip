from copy import deepcopy

import numpy as np

from qutip import qeye, tensor, destroy, basis
from .processor import Model
from .modelprocessor import ModelProcessor, _to_array
from ..transpiler import to_chain_structure
from ..compiler import SCQubitsCompiler
from ..noise import ZZCrossTalk
from ..operations import expand_operator


__all__ = ["SCQubits"]


class SCQubits(ModelProcessor):
    """
    A chain of superconducting qubits with fixed frequency.
    Single-qubit control is realized by rotation around the X and Y axis
    while two-qubit gates are implemented with Cross Resonance gates.
    A 3-level system is used to simulate the superconducting qubit system,
    in order to simulation leakage.
    Various types of interaction can be realized on a superconducting
    system, as a demonstration and
    for simplicity, we only use a ZX Hamiltonian for
    the two-qubit interaction.
    For details see https://arxiv.org/abs/2005.12667 and
    https://journals.aps.org/pra/abstract/10.1103/PhysRevA.101.052308.

    Parameters
    ----------
    num_qubits: int
        The number of qubits in the system.
    dims: list, optional
        The dimension of each component system.
        Default value is a qubit system of ``dim=[2,2,2,...,2]``.
    zz_crosstalk: bool, optional
        If ZZ cross-talk is included.
    **params:
        Hardware parameters. See :obj:`SCQubitsModel`.
    """

    def __init__(self, num_qubits, dims=None, zz_crosstalk=False, **params):
        if dims is None:
            dims = [3] * num_qubits
        model = SCQubitsModel(
            num_qubits=num_qubits,
            dims=dims,
            zz_crosstalk=zz_crosstalk,
            **params,
        )
        super(SCQubits, self).__init__(model=model)
        self.native_gates = ["RX", "RY", "CNOT"]
        self._default_compiler = SCQubitsCompiler
        self.pulse_mode = "continuous"

    def topology_map(self, qc):
        return to_chain_structure(qc)


class SCQubitsModel(Model):
    """
    The physical model for superconducting-qubit model processor
    (:obj:`.SCQubits`) with fixed frequency.

    Parameters
    ----------
    num_qubits: int
        The number of qubits.
    dims: list, optional
        The dimension of each component system.
        Default value is a qubit system of ``dim=[2,2,2,...,2]``.
    zz_crosstalk: bool, optional
        If ZZ cross-talk is included.
    **params:
        Keyword arguments for hardware parameters, in the unit of GHz.
        Each should be given as list:

        - wq : list, optional
            Qubits bare frequency, default 5.15 and 5.09
            for each pair of superconducting qubits,
            default ``[5.15, 5.09, 5.15, ...]``.
        - wr : list, optional
            Resonator bare frequency, default ``[5.96]*num_qubits``.
        - g : list, optional
            The coupling strength between the resonator and the qubits,
            default ``[0.1]*(num_qubits - 1)``.
        - alpha : list, optional
            Anharmonicity for each superconducting qubit,
            default ``[-0.3]*num_qubits``.
        - omega_single : list, optional
            Control strength for single-qubit gate,
            default ``[0.01]*num_qubits``.
        - omega_cr : list, optional
            Control strength for cross resonance gate,
            default ``[0.01]*num_qubits``.
        - t1 : float or list, optional
            Characterize the amplitude damping for each qubit.
        - t2 : list of list, optional
            Characterize the total dephasing for each qubit.
    """

    def __init__(self, num_qubits, dims=None, zz_crosstalk=False, **params):
        self.num_qubits = num_qubits
        self.dims = dims
        self.params = {
            "wq": np.array(
                ((5.15, 5.09) * int(np.ceil(self.num_qubits / 2)))[
                    : self.num_qubits
                ]
            ),
            "wr": 5.96,
            "alpha": -0.3,
            "g": 0.1,
            "omega_single": 0.01,
            "omega_cr": 0.01,
        }
        self.params.update(deepcopy(params))
        self._compute_params()
        self._drift = []
        self._set_up_drift()
        self._controls = self._set_up_controls()
        self._noise = []
        if zz_crosstalk:
            self._noise.append(ZZCrossTalk(self.params))

    def _set_up_drift(self):
        for m in range(self.num_qubits):
            destroy_op = destroy(self.dims[m])
            coeff = 2 * np.pi * self.params["alpha"][m] / 2.0
            self._drift.append(
                (coeff * destroy_op.dag() ** 2 * destroy_op ** 2, [m])
            )

    @property
    def _old_index_label_map(self):
        num_qubits = self.num_qubits
        return (
            ["sx" + str(i) for i in range(num_qubits)]
            + ["sz" + str(i) for i in range(num_qubits)]
            + ["zx" + str(i) + str(i + 1) for i in range(num_qubits)]
            + ["zx" + str(i + 1) + str(i) for i in range(num_qubits)]
        )

    def _set_up_controls(self):
        """
        Setup the operators.
        We use 2π σ/2 as the single-qubit control Hamiltonian and
        -2πZX/4 as the two-qubit Hamiltonian.
        """
        num_qubits = self.num_qubits
        dims = self.dims
        controls = {}

        for m in range(num_qubits):
            destroy_op = destroy(dims[m])
            op = destroy_op + destroy_op.dag()
            controls["sx" + str(m)] = (2 * np.pi / 2 * op, [m])

        for m in range(num_qubits):
            destroy_op = destroy(dims[m])
            op = destroy_op * (-1.0j) + destroy_op.dag() * 1.0j
            controls["sy" + str(m)] = (2 * np.pi / 2 * op, [m])

        for m in range(num_qubits - 1):
            # For simplicity, we neglect leakage in two-qubit gates.
            d1 = dims[m]
            d2 = dims[m + 1]
            # projector to the 0 and 1 subspace
            projector1 = (
                basis(d1, 0) * basis(d1, 0).dag()
                + basis(d1, 1) * basis(d1, 1).dag()
            )
            projector2 = (
                basis(d2, 0) * basis(d2, 0).dag()
                + basis(d2, 1) * basis(d2, 1).dag()
            )
            destroy_op1 = destroy(d1)
            # Notice that this is actually 2πZX/4
            z = (
                projector1
                * (-destroy_op1.dag() * destroy_op1 * 2 + qeye(d1))
                / 2
                * projector1
            )
            destroy_op2 = destroy(d2)
            x = projector2 * (destroy_op2.dag() + destroy_op2) / 2 * projector2
            controls["zx" + str(m) + str(m + 1)] = (
                2 * np.pi * tensor([z, x]),
                [m, m + 1],
            )
            controls["zx" + str(m + 1) + str(m)] = (
                2 * np.pi * tensor([x, z]),
                [m, m + 1],
            )
        return controls

    def _compute_params(self):
        """
        Compute the dressed frequency and the interaction strength.
        """
        num_qubits = self.num_qubits
        for name in ["alpha", "omega_single", "omega_cr"]:
            self.params[name] = _to_array(self.params[name], num_qubits)
        self.params["wr"] = _to_array(self.params["wr"], num_qubits - 1)
        self.params["g"] = _to_array(self.params["g"], 2 * (num_qubits - 1))
        g = self.params["g"]
        wq = self.params["wq"]
        wr = self.params["wr"]
        alpha = self.params["alpha"]
        # Dressed qubit frequency
        wq_dr = []
        for i in range(num_qubits):
            tmp = wq[i]
            if i != 0:
                tmp += g[2 * i - 1] ** 2 / (wq[i] - wr[i - 1])
            if i != (num_qubits - 1):
                tmp += g[2 * i] ** 2 / (wq[i] - wr[i])
            wq_dr.append(tmp)
        self.params["wq_dressed"] = wq_dr
        # Dressed resonator frequency
        wr_dr = []
        for i in range(num_qubits - 1):
            tmp = wr[i]
            tmp -= g[2 * i] ** 2 / (wq[i] - wr[i] + alpha[i])
            tmp -= g[2 * i + 1] ** 2 / (wq[i + 1] - wr[i] + alpha[i])
            wr_dr.append(tmp)
        self.params["wr_dressed"] = wr_dr
        # Effective qubit coupling strength
        J = []
        for i in range(num_qubits - 1):
            tmp = (
                g[2 * i]
                * g[2 * i + 1]
                * (wq[i] + wq[i + 1] - 2 * wr[i])
                / 2
                / (wq[i] - wr[i])
                / (wq[i + 1] - wr[i])
            )
            J.append(tmp)
        self.params["J"] = J
        # Effective ZX strength
        zx_coeff = []
        omega_cr = self.params["omega_cr"]
        for i in range(num_qubits - 1):
            tmp = (
                J[i]
                * omega_cr[i]
                * (
                    1 / (wq[i] - wq[i + 1] + alpha[i])
                    - 1 / (wq[i] - wq[i + 1])
                )
            )
            zx_coeff.append(tmp)
        for i in range(num_qubits - 1, 0, -1):
            tmp = (
                J[i - 1]
                * omega_cr[i]
                * (
                    1 / (wq[i] - wq[i - 1] + alpha[i])
                    - 1 / (wq[i] - wq[i - 1])
                )
            )
            zx_coeff.append(tmp)
        # Times 2 because we use -2πZX/4 as operators
        self.params["zx_coeff"] = np.asarray(zx_coeff) * 2

    def get_control_latex(self):
        """
        Get the labels for each Hamiltonian.
        It is used in the method method :meth:`.Processor.plot_pulses`.
        It is a 2-d nested list, in the plot,
        a different color will be used for each sublist.
        """
        num_qubits = self.num_qubits
        labels = [
            {f"sx{n}": r"$\sigma_x" + f"^{n}$" for n in range(num_qubits)},
            {f"sy{n}": r"$\sigma_y" + f"^{n}$" for n in range(num_qubits)},
        ]
        label_zx = {}
        for m in range(num_qubits - 1):
            label_zx[f"zx{m}{m+1}"] = r"$ZX^{" + f"{m}{m+1}" + r"}$"
            label_zx[f"zx{m+1}{m}"] = r"$ZX^{" + f"{m+1}{m}" + r"}$"

        labels.append(label_zx)
        return labels
