# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Parser for OpenDSS ``.dss`` scripts.

A small, self-contained interpreter of the OpenDSS command language that
records the circuit definition (it never solves anything):

* comments ``!`` and ``//`` to end of line, ``/* ... */`` blocks;
* continuation lines starting with ``~`` or ``more`` / ``m``;
* ``New``/``Edit`` with ``Class.name`` or ``object=Class.name``, named and
  positional properties (the positional index continues after the last
  named property, as in OpenDSS), ``like=`` copies;
* property assignments ``Class.name.prop=value``;
* ``Redirect`` / ``Compile`` (paths relative to the including file),
  ``Set``, ``Clear``; all other commands (``Solve``, ``Show``, ...) are
  recorded in :attr:`DssCircuit.commands` and otherwise ignored;
* transformer winding properties (``wdg``/``bus``/``conn``/``kV``/``kVA``/
  ``tap``/``%R`` and their array forms ``buses``/``kVs``/...).

Values stay raw strings; :mod:`opendss2gtopt.dss_values` interprets them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .dss_values import split_items

logger = logging.getLogger(__name__)

# Positional property order of the classes the converter reads (the
# OpenDSS file format; inherited PD/PC properties are appended).
_PD_TAIL = ["normamps", "emergamps", "faultrate", "pctperm", "repair"]
_PD_TAIL += ["basefreq", "enabled", "like"]
_PC_TAIL = ["spectrum", "basefreq", "enabled", "like"]

PROPERTY_ORDER: dict[str, list[str]] = {
    "vsource": (
        "bus1 basekv pu angle frequency phases mvasc3 mvasc1 x1r1 x0r0 isc3 "
        "isc1 r1 x1 r0 x0 scantype sequence bus2 z1 z0 z2 puz1 puz0 puz2 "
        "basemva yearly daily duty model puzideal"
    ).split()
    + _PC_TAIL,
    "line": (
        "bus1 bus2 linecode length phases r1 x1 r0 x0 c1 c0 rmatrix xmatrix "
        "cmatrix switch rg xg rho geometry units spacing wires earthmodel "
        "cncables tscables b1 b0 seasons ratings linetype"
    ).split()
    + _PD_TAIL,
    "linecode": (
        "nphases r1 x1 r0 x0 c1 c0 units rmatrix xmatrix cmatrix basefreq "
        "normamps emergamps faultrate pctperm repair kron rg xg rho neutral "
        "b1 b0 seasons ratings linetype like"
    ).split(),
    "transformer": (
        "phases windings wdg bus conn kv kva tap %r rneut xneut buses conns "
        "kvs kvas taps xhl xht xlt xscarray thermal n m flrise hsrise "
        "%loadloss %noloadloss normhkva emerghkva sub maxtap mintap numtaps "
        "subname %imag ppm_antifloat %rs bank xfmrcode xrconst x12 x13 x23 "
        "leadlag"
    ).split()
    + _PD_TAIL,
    "load": (
        "phases bus1 kv kw pf model yearly daily duty growth conn kvar rneut "
        "xneut status class vminpu vmaxpu vminnorm vminemerg xfkva "
        "allocationfactor kva %mean %stddev cvrwatts cvrvars kwh kwhdays "
        "cfactor cvrcurve numcust zipv %seriesrl relweight vlowpu"
    ).split()
    + _PC_TAIL,
    "generator": (
        "phases bus1 kv kw pf kvar model vminpu vmaxpu yearly daily duty "
        "dispmode dispvalue conn rneut xneut status class vpu maxkvar minkvar "
        "pvfactor forceon kva mva"
    ).split()
    + _PC_TAIL,
    "pvsystem": (
        "phases bus1 kv irradiance pmpp %pmpp temperature pf conn kvar kva "
        "%cutin %cutout effcurve p-tcurve %r %x model vminpu vmaxpu balanced "
        "limitcurrent yearly daily duty tyearly tdaily tduty class"
    ).split()
    + _PC_TAIL,
    "storage": (
        "phases bus1 kv conn kw kvar pf kva %cutin %cutout effcurve "
        "varfollowinverter kvarmax kvarmaxabs wattpriority pfpriority "
        "%pminnovars %pminkvarmax kwrated %kwrated kwhrated kwhstored %stored "
        "%reserve state %discharge %charge %effcharge %effdischarge %idlingkw"
    ).split()
    + _PC_TAIL,
    "capacitor": (
        "bus1 bus2 phases kvar kv conn cmatrix cuf r xl harm numsteps states"
    ).split()
    + _PD_TAIL,
    "regcontrol": (
        "transformer winding vreg band ptratio ctprim r x bus delay reversible "
        "revvreg revband revr revx tapdelay debugtrace maxtapchange "
        "inversetime tapwinding vlimit ptphase"
    ).split()
    + ["basefreq", "enabled", "like"],
    "capcontrol": (
        "element terminal capacitor type ptratio ctratio onsetting offsetting "
        "delay voltoverride vmax vmin delayoff deadtime"
    ).split()
    + ["basefreq", "enabled", "like"],
    "loadshape": (
        "npts interval mult hour mean stddev csvfile sngfile dblfile action "
        "qmult useactual pmax qmax sinterval minterval pbase qbase pmult"
    ).split()
    + ["like"],
    "wiredata": (
        "rdc rac runits gmrac gmrunits radius radunits normamps emergamps diam"
    ).split()
    + ["like"],
    "linegeometry": (
        "nconds nphases cond wire x h units normamps emergamps reduce spacing wires"
    ).split()
    + ["like"],
}

# Transformer properties that address the active winding.
_WINDING_PROPS = {"bus", "conn", "kv", "kva", "tap", "%r", "rneut", "xneut"}
_WINDING_ARRAYS = {
    "buses": "bus",
    "conns": "conn",
    "kvs": "kv",
    "kvas": "kva",
    "taps": "tap",
    "%rs": "%r",
}
_WINDING_DEFAULT = {"conn": "wye", "kv": "12.47", "kva": "1000", "tap": "1"}
_WINDING_DEFAULT |= {"%r": "0.2"}

# Line properties reset by ``switch=yes`` (the OpenDSS 1 mΩ switch model).
_SWITCH_PROPS = {"r1": "1", "x1": "1", "r0": "1", "x0": "1", "c1": "1.1"}
_SWITCH_PROPS |= {"c0": "1", "length": "0.001", "units": "none"}
_LINE_Z_PROPS = {"r1", "x1", "r0", "x0", "c1", "c0", "b1", "b0"}
_LINE_Z_PROPS |= {"rmatrix", "xmatrix", "cmatrix", "geometry", "spacing"}

# Command verbs (prefix-matched like OpenDSS: ``calcv`` = calcvoltagebases).
_VERBS = [
    "new",
    "edit",
    "redirect",
    "compile",
    "set",
    "clear",
    "solve",
    "calcvoltagebases",
    "buscoords",
    "show",
    "export",
    "plot",
    "dump",
    "help",
    "batchedit",
]


class DssParseError(ValueError):
    """A malformed OpenDSS command."""


@dataclass
class DssObject:
    """One circuit element or library object, properties as raw strings."""

    cls: str
    name: str
    props: dict[str, str] = field(default_factory=dict)
    windings: list[dict[str, str]] = field(default_factory=list)
    active_winding: int = 0
    last_index: int = -1
    # Order in which line impedance sources were set (last one wins).
    zsource: str = ""

    def get(self, key: str, default: str | None = None) -> str | None:
        """Raw value of a property (lower-case key), or *default*."""
        return self.props.get(key, default)


@dataclass
class DssCircuit:
    """The parsed circuit: objects by class then lower-case name."""

    name: str = ""
    objects: dict[str, dict[str, DssObject]] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)

    def of(self, cls: str) -> dict[str, DssObject]:
        """Objects of a class (empty dict when none)."""
        return self.objects.get(cls, {})

    def get(self, cls: str, name: str) -> DssObject | None:
        """One object by class and (case-insensitive) name."""
        return self.of(cls).get(name.lower())


def _canon_class(cls: str) -> str:
    low = cls.strip().lower()
    return "vsource" if low == "circuit" else low


def _canon_prop(cls: str, prop: str) -> str:
    low = prop.strip().lower()
    order = PROPERTY_ORDER.get(cls)
    if order is None or low in order or low in _WINDING_ARRAYS:
        return low
    if low in ("like", "x12", "x13", "x23", "ppm", "kvar"):
        return low
    matches = [p for p in order if p.startswith(low)]
    return matches[0] if len(matches) == 1 else low


def strip_comment(line: str) -> str:
    """Drop ``!`` / ``//`` comments outside quotes and brackets."""
    depth = 0
    quote = ""
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and (c == "!" or line.startswith("//", i)):
            return line[:i]
        i += 1
    return line


def tokenize(text: str) -> list[tuple[str | None, str]]:
    """Split a command line into ``(name, value)`` pairs (name may be None)."""
    out: list[tuple[str | None, str]] = []
    i, n = 0, len(text)

    def read_value(pos: int) -> tuple[str, int]:
        while pos < n and text[pos] in " \t,":
            pos += 1
        if pos >= n:
            return "", pos
        c = text[pos]
        if c in "[({\"'":
            close = {"[": "]", "(": ")", "{": "}", '"': '"', "'": "'"}[c]
            end = text.find(close, pos + 1)
            if end < 0:
                raise DssParseError(f"unbalanced '{c}' in: {text}")
            return text[pos : end + 1], end + 1
        start = pos
        while pos < n and text[pos] not in " \t,=":
            pos += 1
        return text[start:pos], pos

    while i < n:
        while i < n and text[i] in " \t,":
            i += 1
        if i >= n:
            break
        tok, j = read_value(i)
        k = j
        while k < n and text[k] in " \t":
            k += 1
        if k < n and text[k] == "=" and text[i] not in "[({\"'":
            val, j = read_value(k + 1)
            out.append((tok, val))
        else:
            out.append((None, tok))
        i = j
    return out


class DssParser:
    """Interpret OpenDSS scripts into a :class:`DssCircuit`."""

    def __init__(self) -> None:
        self.circuit = DssCircuit()
        self._active: DssObject | None = None

    # -- file handling -----------------------------------------------------
    def parse_file(self, path: str | Path) -> DssCircuit:
        """Parse a script file (and everything it redirects to)."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"OpenDSS script not found: {p}")
        self.circuit.files.append(p)
        self.parse_text(p.read_text(encoding="utf-8", errors="replace"), p.parent)
        return self.circuit

    def parse_text(self, text: str, base_dir: Path | None = None) -> DssCircuit:
        """Parse script text; relative redirects resolve against *base_dir*."""
        base = base_dir or Path.cwd()
        for cmd in _logical_lines(text):
            self._execute(cmd, base)
        return self.circuit

    # -- commands ------------------------------------------------------------
    def _execute(self, cmd: str, base: Path) -> None:
        tokens = tokenize(cmd)
        if not tokens:
            return
        name, val = tokens[0]
        if name is not None:
            # ``Class.obj.prop=value`` (or a bare ``prop=value`` on the
            # active object).
            self._assign(name, val, tokens[1:])
            return
        verb = _match_verb(val)
        rest = tokens[1:]
        if verb in ("new", "edit"):
            self._new_or_edit(verb, rest)
        elif verb in ("redirect", "compile"):
            if not rest:
                raise DssParseError(f"{verb} needs a file name")
            target = Path(rest[0][1].strip("\"'"))
            if not target.is_absolute():
                target = base / target
            if not target.is_file():
                # OpenDSS is case-insensitive on Windows; retry ignoring case.
                target = _case_insensitive(target)
            self.parse_file(target)
        elif verb == "set":
            for key, value in rest:
                if key is not None:
                    self.circuit.options[key.lower()] = value
        elif verb == "clear":
            self.circuit = DssCircuit(files=self.circuit.files)
            self._active = None
        else:
            self.circuit.commands.append(cmd.strip())

    def _assign(self, name: str, val: str, more: list[tuple[str | None, str]]) -> None:
        parts = name.split(".")
        if len(parts) >= 3:
            cls = _canon_class(parts[0])
            obj = self.circuit.get(cls, ".".join(parts[1:-1]))
            if obj is None:
                logger.warning("assignment to undefined %s", name)
                return
            self._set_props(obj, [(parts[-1], val)] + more)
        elif self._active is not None:
            self._set_props(self._active, [(name, val)] + more)
        else:
            self.circuit.commands.append(f"{name}={val}")

    def _new_or_edit(self, verb: str, rest: list[tuple[str | None, str]]) -> None:
        if not rest:
            raise DssParseError(f"{verb} without an object")
        key, spec = rest[0]
        if key is not None and key.lower() != "object":
            raise DssParseError(f"{verb}: expected Class.name, got {key}={spec}")
        if "." not in spec:
            raise DssParseError(f"{verb}: expected Class.name, got '{spec}'")
        cls_raw, obj_name = spec.split(".", 1)
        cls = _canon_class(cls_raw)
        if cls_raw.lower() == "circuit":
            # OpenDSS names the circuit's own Vsource "source".
            self.circuit.name = obj_name
            obj_name = "source"
        lname = obj_name.lower()
        objs = self.circuit.objects.setdefault(cls, {})
        if verb == "new" or lname not in objs:
            obj = DssObject(cls=cls, name=obj_name)
            if cls == "transformer":
                obj.windings = [dict(_WINDING_DEFAULT) for _ in range(2)]
            if cls_raw.lower() == "circuit":
                obj.props["bus1"] = "sourcebus"
            objs[lname] = obj
        else:
            obj = objs[lname]
        self._active = obj
        self._set_props(obj, rest[1:])

    def _set_props(self, obj: DssObject, pairs: list[tuple[str | None, str]]) -> None:
        order = PROPERTY_ORDER.get(obj.cls, [])
        for key, value in pairs:
            if key is None:
                idx = obj.last_index + 1
                if idx >= len(order):
                    logger.warning("%s.%s: extra value '%s'", obj.cls, obj.name, value)
                    continue
                prop = order[idx]
            else:
                prop = _canon_prop(obj.cls, key)
                if prop in order:
                    idx = order.index(prop)
                else:
                    idx = obj.last_index
            obj.last_index = idx
            self._set_one(obj, prop, value)

    def _set_one(self, obj: DssObject, prop: str, value: str) -> None:
        if prop == "like":
            self._copy_like(obj, value)
            return
        if obj.cls == "transformer" and self._set_winding(obj, prop, value):
            return
        if obj.cls == "line":
            if prop == "switch" and value.strip().lower()[:1] in ("y", "t"):
                obj.props.update(_SWITCH_PROPS)
                obj.props.pop("linecode", None)
                obj.zsource = "seq"
            elif prop == "linecode":
                obj.zsource = "linecode"
            elif prop in ("r1", "x1", "r0", "x0", "c1", "c0", "b1", "b0"):
                obj.zsource = "seq"
            elif prop in ("rmatrix", "xmatrix", "cmatrix"):
                obj.zsource = "matrix"
            elif prop in ("geometry", "spacing"):
                obj.zsource = "geometry"
        if obj.cls == "linegeometry" and prop in ("cond", "wire", "x", "h", "units"):
            conds = obj.windings  # reuse the per-item list for conductors
            if prop == "cond":
                k = int(float(value)) - 1
                while len(conds) <= k:
                    conds.append({})
                obj.active_winding = k
            if prop != "cond":
                if not conds:
                    conds.append({})
                conds[obj.active_winding][prop] = value
            return
        obj.props[prop] = value

    def _set_winding(self, obj: DssObject, prop: str, value: str) -> bool:
        if prop == "windings":
            n = int(float(value))
            while len(obj.windings) < n:
                obj.windings.append(dict(_WINDING_DEFAULT))
            del obj.windings[n:]
            obj.props[prop] = value
            return True
        if prop == "wdg":
            k = int(float(value)) - 1
            while len(obj.windings) <= k:
                obj.windings.append(dict(_WINDING_DEFAULT))
            obj.active_winding = k
            return True
        if prop in _WINDING_PROPS:
            obj.windings[obj.active_winding][prop] = value
            if prop == "%r":
                obj.props.pop("%loadloss", None)
            return True
        if prop in _WINDING_ARRAYS:
            items = split_items(value)
            while len(obj.windings) < len(items):
                obj.windings.append(dict(_WINDING_DEFAULT))
            for w, item in zip(obj.windings, items):
                w[_WINDING_ARRAYS[prop]] = item
            if prop == "%rs":
                obj.props.pop("%loadloss", None)
            return True
        if prop == "%loadloss":
            half = str(float(value) / 2.0)
            for w in obj.windings[:2]:
                w["%r"] = half
            obj.props[prop] = value
            return True
        if prop == "x12":
            obj.props["xhl"] = value
            return True
        if prop == "x13":
            obj.props["xht"] = value
            return True
        if prop == "x23":
            obj.props["xlt"] = value
            return True
        return False

    def _copy_like(self, obj: DssObject, other_name: str) -> None:
        other = self.circuit.get(obj.cls, other_name.strip("\"'"))
        if other is None:
            logger.warning("%s.%s: like=%s not found", obj.cls, obj.name, other_name)
            return
        obj.props.update(other.props)
        obj.windings = [dict(w) for w in other.windings]
        obj.zsource = other.zsource


def _match_verb(token: str) -> str:
    low = token.lower()
    for verb in _VERBS:
        if verb == low:
            return verb
    matches = [v for v in _VERBS if v.startswith(low)]
    return matches[0] if len(matches) >= 1 and len(low) >= 2 else low


def _case_insensitive(path: Path) -> Path:
    parent = path.parent
    if parent.is_dir():
        for cand in parent.iterdir():
            if cand.name.lower() == path.name.lower():
                return cand
    raise FileNotFoundError(f"OpenDSS script not found: {path}")


def _logical_lines(text: str) -> list[str]:
    """Join continuation lines and drop comments; one entry per command."""
    out: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw
        if in_block:
            end = line.find("*/")
            if end < 0:
                continue
            line = line[end + 2 :]
            in_block = False
        if line.lstrip().startswith("/*"):
            if "*/" not in line:
                in_block = True
            continue
        line = strip_comment(line).strip()
        if not line:
            continue
        head = line.split(None, 1)[0].lower()
        if line.startswith("~") or head in ("more", "m"):
            body = line[1:] if line.startswith("~") else line.split(None, 1)[1:]
            body_s = body if isinstance(body, str) else (body[0] if body else "")
            if not out:
                raise DssParseError(f"continuation without a command: {raw}")
            out[-1] = out[-1] + " " + body_s
            continue
        out.append(line)
    return out


def parse_dss(path: str | Path) -> DssCircuit:
    """Parse an OpenDSS master script into a :class:`DssCircuit`."""
    return DssParser().parse_file(path)
