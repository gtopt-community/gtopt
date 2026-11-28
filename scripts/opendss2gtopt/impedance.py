# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Series impedance and shunt capacitance of OpenDSS lines.

Lines take their phase impedance from one of four sources (the last
one set wins, as in OpenDSS): a ``LineCode``, the line's own sequence
data (``r1 x1 r0 x0 c1 c0``), its own matrices (``rmatrix xmatrix
cmatrix``) or a ``LineGeometry`` (conductor positions + ``WireData``).
For geometries the primitive impedances follow the modified Carson
equations and the shunt capacitances the potential-coefficient method
(Kersting, *Distribution System Modeling and Analysis*, ch. 4-5), with
Kron reduction of the neutral conductors.

Everything returned here is the TOTAL impedance of the line in ohm and
the total capacitance in farad (length already applied).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .dss_parser import DssCircuit, DssObject
from .dss_values import (
    DssValueError,
    length_factor_m,
    to_bool,
    to_float,
    to_int,
    to_matrix,
)

MILE_M = 1609.344
FT_M = 0.3048

# OpenDSS defaults (per unit length, units "none").
_DEFAULT_SEQ = {"r1": 0.058, "x1": 0.1206, "r0": 0.1784, "x0": 0.4047}
_DEFAULT_C = {"c1": 3.4, "c0": 1.6}  # nF per unit length


@dataclass
class LineImpedance:
    """Phase-frame data of one line (totals, length included)."""

    nphases: int
    z: np.ndarray  # complex (n, n) ohm
    c: np.ndarray  # real (n, n) farad
    normamps: float
    emergamps: float

    def z1(self) -> complex:
        """Positive-sequence equivalent ``Zs - Zm`` (averaged), ohm."""
        return complex(seq_from_matrix(self.z))

    def c1(self) -> float:
        """Positive-sequence capacitance ``Cs - Cm`` (averaged), farad."""
        return float(seq_from_matrix(self.c).real)


def seq_from_matrix(m: np.ndarray) -> complex:
    """Average diagonal minus average off-diagonal (OpenDSS pos-seq rule)."""
    n = m.shape[0]
    zs = complex(np.trace(m)) / n
    if n == 1:
        return zs
    off = (complex(np.sum(m)) - complex(np.trace(m))) / (n * (n - 1))
    return zs - off


def _seq_matrix(z1: complex, z0: complex, n: int) -> np.ndarray:
    if n == 1:
        return np.array([[z1]], dtype=complex)
    zs = (2 * z1 + z0) / 3
    zm = (z0 - z1) / 3
    m = np.full((n, n), zm, dtype=complex)
    np.fill_diagonal(m, zs)
    return m


def _float_prop(obj: DssObject, key: str, default: float) -> float:
    raw = obj.get(key)
    return default if raw is None else to_float(raw)


def _units(obj: DssObject) -> str:
    raw = obj.get("units")
    return "none" if raw is None else raw.strip().strip("\"'").lower()


def _from_sequence(
    obj: DssObject, n: int, freq: float
) -> tuple[np.ndarray, np.ndarray]:
    z1 = complex(_float_prop(obj, "r1", _DEFAULT_SEQ["r1"]), 0.0)
    z1 += 1j * _float_prop(obj, "x1", _DEFAULT_SEQ["x1"])
    z0 = complex(_float_prop(obj, "r0", _DEFAULT_SEQ["r0"]), 0.0)
    z0 += 1j * _float_prop(obj, "x0", _DEFAULT_SEQ["x0"])
    w = 2 * math.pi * freq
    if obj.get("b1") is not None:
        c1 = to_float(obj.get("b1") or "0") * 1e-6 / w
    else:
        c1 = _float_prop(obj, "c1", _DEFAULT_C["c1"]) * 1e-9
    if obj.get("b0") is not None:
        c0 = to_float(obj.get("b0") or "0") * 1e-6 / w
    else:
        c0 = _float_prop(obj, "c0", _DEFAULT_C["c0"]) * 1e-9
    if n == 1:
        z0, c0 = z1, c1
    z = _seq_matrix(z1, z0, n)
    c = _seq_matrix(complex(c1), complex(c0), n).real
    return z, c


def _from_matrices(obj: DssObject, n: int) -> tuple[np.ndarray, np.ndarray]:
    r = (
        np.array(to_matrix(obj.get("rmatrix") or "0", n))
        if obj.get("rmatrix")
        else None
    )
    x = (
        np.array(to_matrix(obj.get("xmatrix") or "0", n))
        if obj.get("xmatrix")
        else None
    )
    if r is None and x is None:
        raise DssValueError(f"{obj.cls}.{obj.name}: no rmatrix/xmatrix")
    r = np.zeros((n, n)) if r is None else r
    x = np.zeros((n, n)) if x is None else x
    raw_c = obj.get("cmatrix")
    c = np.array(to_matrix(raw_c, n)) * 1e-9 if raw_c else np.zeros((n, n))
    return r + 1j * x, c


def linecode_data(
    code: DssObject, freq: float
) -> tuple[int, np.ndarray, np.ndarray, str]:
    """Per-unit-length ``(n, Z, C, units)`` of a LineCode."""
    n = to_int(code.get("nphases") or "3")
    if code.get("rmatrix") is not None or code.get("xmatrix") is not None:
        z, c = _from_matrices(code, n)
    else:
        z, c = _from_sequence(code, n, freq)
    if to_bool(code.get("kron") or "n") and n > 1:
        z = kron(z, [n - 1])
        c = kron(c.astype(complex), [n - 1]).real
        n -= 1
    return n, z, c, _units(code)


def kron(m: np.ndarray, drop: list[int]) -> np.ndarray:
    """Kron-reduce matrix *m* eliminating the indices in *drop*."""
    keep = [i for i in range(m.shape[0]) if i not in drop]
    a = m[np.ix_(keep, keep)]
    b = m[np.ix_(keep, drop)]
    cc = m[np.ix_(drop, keep)]
    d = m[np.ix_(drop, drop)]
    return a - b @ np.linalg.solve(d, cc)


def _wire_value(wire: DssObject, key: str, unit_key: str, default: float) -> float:
    """A WireData length quantity in feet (``gmrac``/``radius``/``diam``)."""
    raw = wire.get(key)
    if raw is None:
        return default
    unit = (wire.get(unit_key) or "ft").strip().lower()
    return to_float(raw) * length_factor_m(unit) / FT_M


def _wire_rac_per_mile(wire: DssObject) -> float:
    raw = wire.get("rac") or wire.get("rdc")
    if raw is None:
        raise DssValueError(f"wiredata.{wire.name}: no rac")
    unit = (wire.get("runits") or "none").strip().lower()
    if unit == "none":
        return to_float(raw)
    return to_float(raw) / length_factor_m(unit) * MILE_M


def _wire_radius_ft(wire: DssObject) -> float:
    if wire.get("radius") is not None:
        return _wire_value(wire, "radius", "radunits", 0.0)
    if wire.get("diam") is not None:
        return _wire_value(wire, "diam", "radunits", 0.0) / 2.0
    raise DssValueError(f"wiredata.{wire.name}: no radius/diam")


def geometry_data(
    circuit: DssCircuit, geom: DssObject, freq: float, rho: float
) -> tuple[int, np.ndarray, np.ndarray]:
    """Per-mile ``(n, Z, C)`` of a LineGeometry (modified Carson)."""
    nconds = to_int(geom.get("nconds") or str(len(geom.windings)))
    nph = to_int(geom.get("nphases") or str(nconds))
    conds = geom.windings[:nconds]
    if len(conds) < nconds:
        raise DssValueError(f"linegeometry.{geom.name}: {len(conds)} of {nconds} conds")
    xs, hs, gmr, rac, rad = [], [], [], [], []
    for cd in conds:
        unit = (cd.get("units") or geom.get("units") or "ft").strip().lower()
        f_ft = length_factor_m(unit) / FT_M
        xs.append(to_float(cd.get("x", "0")) * f_ft)
        hs.append(to_float(cd.get("h", "0")) * f_ft)
        wire = circuit.get("wiredata", (cd.get("wire") or "").strip("\"'"))
        if wire is None:
            raise DssValueError(
                f"linegeometry.{geom.name}: unknown wire {cd.get('wire')}"
            )
        r_ft = _wire_radius_ft(wire)
        gmr.append(_wire_value(wire, "gmrac", "gmrunits", 0.7788 * r_ft))
        rac.append(_wire_rac_per_mile(wire))
        rad.append(r_ft)
    n = nconds
    re_term = 0.00158836 * freq
    k = 0.00202237 * freq
    const = 7.6786 + 0.5 * math.log(rho / freq)
    z = np.zeros((n, n), dtype=complex)
    p = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                z[i, i] = rac[i] + re_term + 1j * k * (math.log(1 / gmr[i]) + const)
                p[i, i] = 11.17689 * math.log(2 * hs[i] / rad[i])
            else:
                dij = math.hypot(xs[i] - xs[j], hs[i] - hs[j])
                sij = math.hypot(xs[i] - xs[j], hs[i] + hs[j])
                z[i, j] = re_term + 1j * k * (math.log(1 / dij) + const)
                p[i, j] = 11.17689 * math.log(sij / dij)
    reduce = to_bool(geom.get("reduce") or "n")
    if nph < n and reduce:
        drop = list(range(nph, n))
        z = kron(z, drop)
        p = kron(p.astype(complex), drop).real
        n = nph
    c = np.linalg.inv(p) * 1e-6  # uF/mile -> F/mile
    return n, z, c


def line_impedance(circuit: DssCircuit, line: DssObject, freq: float) -> LineImpedance:
    """Total phase impedance and capacitance of one Line."""
    length = _float_prop(line, "length", 1.0)
    line_units = _units(line)
    normamps = _float_prop(line, "normamps", 400.0)
    emergamps = _float_prop(line, "emergamps", 600.0)
    src = line.zsource or ("linecode" if line.get("linecode") else "seq")
    if src == "linecode":
        code = circuit.get("linecode", (line.get("linecode") or "").strip("\"'"))
        if code is None:
            raise DssValueError(
                f"line.{line.name}: unknown linecode {line.get('linecode')}"
            )
        n, z, c, code_units = linecode_data(code, freq)
        if line_units != "none" and code_units != "none":
            length = length * length_factor_m(line_units) / length_factor_m(code_units)
        if line.get("normamps") is None and code.get("normamps") is not None:
            normamps = to_float(code.get("normamps") or "400")
        if line.get("emergamps") is None and code.get("emergamps") is not None:
            emergamps = to_float(code.get("emergamps") or "600")
    elif src == "geometry":
        geom = circuit.get("linegeometry", (line.get("geometry") or "").strip("\"'"))
        if geom is None:
            raise DssValueError(
                f"line.{line.name}: unknown geometry {line.get('geometry')}"
            )
        rho = to_float(line.get("rho") or "100")
        n, z, c = geometry_data(circuit, geom, freq, rho)
        length = length * length_factor_m(line_units) / MILE_M
        if line.get("normamps") is None and geom.get("normamps") is not None:
            normamps = to_float(geom.get("normamps") or "400")
    elif src == "matrix":
        n = to_int(line.get("phases") or "3")
        z, c = _from_matrices(line, n)
    else:
        n = to_int(line.get("phases") or "3")
        z, c = _from_sequence(line, n, freq)
    phases = to_int(line.get("phases") or str(n))
    if phases < n:
        z = z[:phases, :phases]
        c = c[:phases, :phases]
        n = phases
    return LineImpedance(
        nphases=n, z=z * length, c=c * length, normamps=normamps, emergamps=emergamps
    )
