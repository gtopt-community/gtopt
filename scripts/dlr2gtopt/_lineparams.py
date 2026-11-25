# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Line parameters for IEEE 738: CEN technical sheet first, then defaults.

Each parameter of a line (voltage, conductor diameter and resistance,
sub-conductors per phase, design temperature, conductor height above
ground) is taken, in order of precedence, from:

1. the command line (explicit options always win);
2. the CEN Infotecnica technical sheet ("ficha tecnica general") of the
   line section, where the field is present and certified;
3. the per-voltage-class defaults of ``data/line_defaults.json`` (or a
   file given with ``--line-defaults``), documented there with their
   sources.

Every value carries its provenance (``cen``, ``conductor-library``,
``estimated``, ``default`` or ``option``) so reports can flag lines that
rest on assumptions.  The conductor height has no CEN field: it always
comes from the defaults, by voltage class.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from dlr2gtopt._ieee738 import Conductor

# Outside diameter [m] of common conductors (manufacturer catalogues; the
# names are the usual code words).  Used when the CEN sheet names one.
CONDUCTOR_DIAMETERS: dict[str, float] = {
    "flint": 0.02517,  # AAAC 740.8 kcmil (375.4 mm2)
    "cairo": 0.01988,  # AAAC 465.4 kcmil
    "darien": 0.02296,  # AAAC 559.5 kcmil
    "elgin": 0.02371,  # AAAC 652.4 kcmil
    "greeley": 0.02855,  # AAAC 927.2 kcmil
    "drake": 0.02814,  # ACSR 795 kcmil 26/7
    "condor": 0.02776,  # ACSR 795 kcmil 54/7
    "rail": 0.02959,  # ACSR 954 kcmil 45/7
    "cardinal": 0.03038,  # ACSR 954 kcmil 54/7
    "grosbeak": 0.02515,  # ACSR 636 kcmil 26/7
    "hawk": 0.02179,  # ACSR 477 kcmil 26/7
    "linnet": 0.01831,  # ACSR 336.4 kcmil 26/7
    "bluejay": 0.03198,  # ACSR 1113 kcmil 45/7
    "pheasant": 0.03510,  # ACSR 1272 kcmil 54/19
    "marigold": 0.03078,  # AAC 1113 kcmil
    "coreopsis": 0.03693,  # AAC 1590 kcmil
}

# Stranded round-wire conductors: D ~= 1.15 * sqrt(4 A / pi) for the total
# cross-section A (Flint 375.4 mm2 -> 25.2 mm; Drake 468.5 mm2 -> 28.2 mm).
STRANDING_FACTOR = 1.15
ALPHA_AL = 0.00403  # 1/K, aluminium (AAC, ACSR, ACAR)
ALPHA_AAAC = 0.0036  # 1/K, aluminium alloy 6201 (AAAC)


@dataclass
class LineParams:
    """IEEE 738 inputs of one line, with the source of each field."""

    voltage_kv: float
    diameter: float
    r20: float  # per sub-conductor, ohm/m
    alpha_r: float
    ac_factor: float
    emissivity: float
    absorptivity: float
    subconductors: int
    tmax_conductor: float
    conductor_height: float
    conductor_type: str = ""
    source: dict[str, str] = field(default_factory=dict)

    def conductor(self) -> Conductor:
        """The :class:`Conductor` for the IEEE 738 evaluation."""
        return Conductor(
            diameter=self.diameter,
            r20=self.r20,
            alpha_r=self.alpha_r,
            ac_factor=self.ac_factor,
            emissivity=self.emissivity,
            absorptivity=self.absorptivity,
            subconductors=self.subconductors,
        )

    def uses_assumptions(self) -> list[str]:
        """Fields that do not come from CEN data or an explicit option."""
        return sorted(
            k for k, v in self.source.items() if v in ("default", "estimated")
        )

    def to_json(self) -> dict[str, Any]:
        """Plain dict (``line-params`` output)."""
        return asdict(self)


def load_defaults(path: Path | None = None) -> dict[str, Any]:
    """The per-voltage-class defaults table."""
    if path is not None:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    text = (
        resources.files("dlr2gtopt")
        .joinpath("data/line_defaults.json")
        .read_text(encoding="utf-8")
    )
    return dict(json.loads(text))


def voltage_class(defaults: dict[str, Any], kv: float) -> dict[str, Any]:
    """Defaults of the nearest voltage class."""
    classes = defaults["classes"]
    key = min(classes, key=lambda k: abs(float(k) - kv))
    return dict(classes[key])


def _num(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", "."))
    return float(m.group(0)) if m else None


# Ficha concept prefixes ("1.16 Tipo de conductor ...") -> field.
_FICHA_FIELDS = {
    "1.1 ": "voltage_kv",
    "1.3 ": "r20_ohm_km",
    "1.9 temperatura de diseno": "design_temp",
    "1.15 ": "section_mm2",
    "1.16 ": "conductor_type",
    "1.17 ": "subconductors",
}


def _plain(s: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def parse_ficha_general(data: dict[str, Any]) -> dict[str, Any]:
    """Fields of a CEN "ficha tecnica general" (``fichas-tecnicas/general``)
    reply.  Rejected entries (``estado_certificacion_nombre`` "Rechazado")
    are skipped."""
    out: dict[str, Any] = {}
    for item in data.values():
        if not isinstance(item, dict):
            continue
        name = _plain(item.get("nombre_concepto") or "")
        if not name or item.get("estado_certificacion_nombre") == "Rechazado":
            continue
        raw = item.get("valor_numerico")
        text = str(item.get("valor_texto") or "")
        for prefix, key in _FICHA_FIELDS.items():
            if not name.startswith(prefix) or key in out:
                continue
            if key == "conductor_type":
                if text.strip():
                    out[key] = text.strip().strip("-").strip()
                continue
            val = float(raw) if isinstance(raw, (int, float)) else _num(text)
            if val is not None and val > 0:
                out[key] = int(val) if key == "subconductors" else val
    return out


def conductor_family(conductor_type: str) -> str:
    """``aaac``, ``acsr``, ``acar``, ``aac`` or ``""`` from a type string."""
    t = conductor_type.lower()
    for fam in ("aaac", "acsr", "acar", "aac"):
        if fam in t:
            return fam
    return ""


def library_diameter(conductor_type: str) -> float | None:
    """Diameter of a named conductor found in the type string."""
    t = conductor_type.lower()
    for name, d in CONDUCTOR_DIAMETERS.items():
        if re.search(rf"\b{name}\b", t):
            return d
    return None


def resolve(
    ficha: dict[str, Any] | None,
    defaults: dict[str, Any],
    kv: float | None = None,
    overrides: dict[str, Any] | None = None,
) -> LineParams:
    """Line parameters from a parsed ficha, the defaults and overrides."""
    f = ficha or {}
    src: dict[str, str] = {}
    volt = kv if kv is not None else f.get("voltage_kv")
    if volt is None:
        raise ValueError("line params: no voltage (ficha or --kv)")
    src["voltage_kv"] = "option" if kv is not None else "cen"
    d = voltage_class(defaults, float(volt))

    def pick(key: str, cen_key: str | None, dflt_key: str) -> Any:
        if cen_key is not None and cen_key in f:
            src[key] = "cen"
            return f[cen_key]
        src[key] = "default"
        return d[dflt_key]

    ctype = str(pick("conductor_type", "conductor_type", "conductor_type"))
    nsub = int(pick("subconductors", "subconductors", "subconductors"))
    tmax = float(pick("tmax_conductor", "design_temp", "tmax_conductor"))
    height = float(pick("conductor_height", None, "conductor_height"))
    lib = library_diameter(ctype)
    if lib is not None:
        diameter = lib
        src["diameter"] = (
            "conductor-library" if src["conductor_type"] == "cen" else "default"
        )
    elif "section_mm2" in f:
        diameter = STRANDING_FACTOR * math.sqrt(4 * f["section_mm2"] / math.pi) / 1000
        src["diameter"] = "estimated"
    else:
        diameter = float(d["diameter"])
        src["diameter"] = "default"
    if "r20_ohm_km" in f:
        r20 = f["r20_ohm_km"] * nsub / 1000.0
        src["r20"] = "cen"
    else:
        r20 = float(d["r20"])
        src["r20"] = "default"
    fam = conductor_family(ctype)
    alpha = ALPHA_AAAC if fam == "aaac" else ALPHA_AL if fam else float(d["alpha_r"])
    src["alpha_r"] = "estimated" if fam else "default"
    for k in ("ac_factor", "emissivity", "absorptivity"):
        src[k] = "default"
    p = LineParams(
        voltage_kv=float(volt),
        diameter=diameter,
        r20=r20,
        alpha_r=alpha,
        ac_factor=float(d["ac_factor"]),
        emissivity=float(d["emissivity"]),
        absorptivity=float(d["absorptivity"]),
        subconductors=nsub,
        tmax_conductor=tmax,
        conductor_height=height,
        conductor_type=ctype,
        source=src,
    )
    for k, v in (overrides or {}).items():
        if v is not None:
            setattr(p, k, v)
            src[k] = "option"
    return p


def read_line_params(path: Path) -> LineParams:
    """A ``line-params`` JSON written by :meth:`LineParams.to_json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return LineParams(**data)
