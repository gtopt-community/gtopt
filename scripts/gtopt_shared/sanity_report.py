# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Post-conversion data-quality (sanity) report for a gtopt case.

Every converter (``plp2gtopt``, ``plexos2gtopt``, ``sddp2gtopt``,
``pp2gtopt``, ``psse2gtopt``) calls :func:`run_sanity_report` on the
planning it just wrote.  The report never changes the case: it reads
the planning JSON plus its input tables (Parquet / CSV) and flags data
that is legal but almost certainly wrong.

Categories
----------

``line_impedance``
    Per kV class: count, median / p5 / p95 of ``x = |X|/V²`` and
    ``r = R/V²`` (``V`` = ``Line.voltage``, 1 when unset — the exact
    quantity gtopt compares with ``dc_line_reactance_threshold``), and
    the number of lines below that tie threshold.  The kV class comes
    from the bus voltages, else the line voltage, else a standard kV
    level embedded in the line name.
``line_x_outlier``
    Lines whose ``x`` is more than ``line_x_outlier_factor`` times off
    the median of their kV class (mistyped kV, impedance read in the
    wrong unit — the Guatemala ``dcirc.dat`` %-vs-Ω bug).
``line_below_tie``
    Lines below the tie threshold: gtopt turns them into
    zero-impedance ties (``θ_a = θ_b``).
``line_xr``
    ``X/R`` above ``line_xr_max``.
``bus_kv``
    Bus voltages that are not a standard kV level; those equal to the
    numeric suffix of the bus name (``ESC-692`` at 692 kV) are tagged
    as mistyped.
``zero_rated_units``
    Generators / batteries with ``pmax`` (or ``capacity``) <= 0 over
    the whole horizon (informative: retired / not-yet-built units).
``zero_rated_committed``
    The subset that still carries commitment binaries or a ``pmin > 0``
    (dead binaries or an infeasible floor).
``profile_range``
    Generator / demand / capacity profiles outside ``[0, 1]``.
``reservoir_floor``
    Reservoirs with ``emin`` 0 / unset whose volume drives an agreement
    ``bound_rule`` with a reserve level (an agreement floor).
``reward_vs_fail_cost``
    Water-right rewards (``uvalue > 0``, ``fcost < 0``,
    ``hydro_use_value``) above the demand fail cost (phantom reward).
``reward_vs_coupled_penalty``
    A rewarded right tied by a user constraint (inline or PAMPL) to a
    right whose shortfall penalty (``fcost > 0`` or ``uvalue < 0``) is
    below the reward: the LP books shortfall to collect the reward
    (Maule Res. 105, review R3).

The C++ ``validate_planning`` runs the same checks on inline values at
load time (``options.sanity_options``); this module also sees the
file-backed schedules.

CLI: ``python -m gtopt_shared.sanity_report case.json
[--sanity-report out.json]``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from gtopt_shared.field_sched import StageSchedResolver

logger = logging.getLogger(__name__)

__all__ = [
    "STANDARD_KV_LEVELS",
    "Finding",
    "SanityReport",
    "SanityThresholds",
    "add_sanity_report_argument",
    "build_sanity_report",
    "log_sanity_report",
    "main",
    "mistyped_kv",
    "run_sanity_report",
    "write_sanity_report",
]

#: Common nominal transmission / distribution voltages [kV] worldwide.
STANDARD_KV_LEVELS: frozenset[float] = frozenset(
    {
        0.4,
        4.16,
        6.6,
        6.9,
        10.0,
        11.0,
        12.0,
        12.47,
        13.2,
        13.8,
        15.0,
        20.0,
        22.0,
        23.0,
        24.0,
        25.0,
        33.0,
        34.5,
        44.0,
        46.0,
        50.0,
        60.0,
        63.0,
        66.0,
        69.0,
        100.0,
        110.0,
        115.0,
        120.0,
        132.0,
        138.0,
        150.0,
        154.0,
        161.0,
        220.0,
        230.0,
        275.0,
        330.0,
        345.0,
        380.0,
        400.0,
        500.0,
        525.0,
        735.0,
        765.0,
    }
)

#: C++ ``PlanningOptionsLP::default_dc_line_reactance_threshold``.
DEFAULT_TIE_THRESHOLD = 1e-6

_INT_GROUP = re.compile(r"\d+")


@dataclass(frozen=True)
class SanityThresholds:
    """Tunable thresholds (defaults mirror C++ ``SanityOptions``)."""

    reward_fail_cost_ratio: float = 1.0
    profile_tolerance: float = 1e-6
    line_x_outlier_factor: float = 50.0
    line_x_min_class_size: int = 5
    line_xr_max: float = 100.0
    max_offenders: int = 5


@dataclass
class Finding:
    """One category: how many elements it flags and the worst ones."""

    category: str
    message: str
    count: int = 0
    offenders: list[dict[str, Any]] = field(default_factory=list)

    def add(self, score: float, **item: Any) -> None:
        """Record one offender (``score`` orders the top list)."""
        self.count += 1
        self.offenders.append({"score": score, **item})

    def sort(self) -> None:
        """Order offenders worst-first."""
        self.offenders.sort(key=lambda o: -float(o["score"]))


@dataclass
class SanityReport:
    """Whole-case report: findings plus the per-kV impedance table."""

    case: str
    findings: dict[str, Finding] = field(default_factory=dict)
    line_classes: list[dict[str, Any]] = field(default_factory=list)
    tie_threshold: float = DEFAULT_TIE_THRESHOLD

    def finding(self, category: str, message: str) -> Finding:
        """Return (creating on first use) the finding for ``category``."""
        if category not in self.findings:
            self.findings[category] = Finding(category, message)
        return self.findings[category]

    @property
    def flagged(self) -> dict[str, Finding]:
        """Categories with at least one offender."""
        return {k: f for k, f in self.findings.items() if f.count}

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view."""
        return {
            "case": self.case,
            "tie_threshold": self.tie_threshold,
            "line_classes": self.line_classes,
            "findings": {k: asdict(f) for k, f in self.findings.items()},
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_active(elem: Mapping[str, Any]) -> bool:
    active = elem.get("active", True)
    if isinstance(active, bool):
        return active
    if isinstance(active, (int, float)):
        return active != 0
    return True  # schedules / files: assume active somewhere


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


class _Values:
    """Global min / max of any schedule field (inline or file-backed)."""

    def __init__(self, resolver: StageSchedResolver | None) -> None:
        self.resolver = resolver

    def extrema(
        self, class_name: str, elem: Mapping[str, Any], key: str
    ) -> tuple[float, float] | None:
        value = elem.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            if self.resolver is None:
                return None
            uid = elem.get("uid")
            lo = self.resolver.per_stage(class_name, value, uid, reduce="min")
            hi = self.resolver.per_stage(class_name, value, uid, reduce="max")
            los = [v for v in lo if v is not None]
            his = [v for v in hi if v is not None]
            if not los or not his:
                return None
            return min(los), max(his)
        leaves = list(_leaves(value))
        if not leaves:
            return None
        return min(leaves), max(leaves)

    def magnitude(
        self, class_name: str, elem: Mapping[str, Any], key: str
    ) -> float | None:
        ext = self.extrema(class_name, elem, key)
        if ext is None:
            return None
        return max(abs(ext[0]), abs(ext[1]))


def _leaves(value: Any) -> Iterable[float]:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        else:
            num = _num(item)
            if num is not None:
                yield num


def _ref_key(ref: Any) -> str | int | None:
    if isinstance(ref, bool):
        return None
    if isinstance(ref, (int, float)):
        return int(ref)
    if isinstance(ref, str):
        return ref
    return None


def _index(arr: Sequence[Mapping[str, Any]]) -> dict[str | int, Mapping[str, Any]]:
    out: dict[str | int, Mapping[str, Any]] = {}
    for elem in arr:
        if "uid" in elem and _ref_key(elem["uid"]) is not None:
            key = _ref_key(elem["uid"])
            assert key is not None
            out[key] = elem
        if isinstance(elem.get("name"), str):
            out[elem["name"]] = elem
    return out


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an ascending sequence."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _is_standard_kv(kv: float) -> bool:
    return any(abs(kv - s) <= 0.005 * s for s in STANDARD_KV_LEVELS)


def mistyped_kv(kv: float, bus_name: str) -> bool:
    """True when ``kv`` is non-standard and copies the name's numeric suffix.

    PSR-style bus names end in ``-<class><index>`` (``ESC-692`` = 69 kV
    bus #2); some sources copy that suffix as the voltage (692.).
    """
    if kv <= 0.0 or _is_standard_kv(kv):
        return False
    groups = _INT_GROUP.findall(bus_name or "")
    return bool(groups) and len(groups[-1]) >= 3 and float(groups[-1]) == kv


def _kv_from_name(name: str) -> float | None:
    """The first integer standard kV level embedded in a name.

    Scans left to right so a trailing element index (``GSUESC230A_1``,
    ``TICTIC230A_190``) never wins over the voltage tag.
    """
    for group in _INT_GROUP.findall(name or ""):
        if len(group) >= 2 and float(int(group)) in STANDARD_KV_LEVELS:
            return float(int(group))
    return None


def _kv_label(kv: float) -> str:
    return f"{kv:g} kV"


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def _line_class(
    line: Mapping[str, Any],
    buses: Mapping[str | int, Mapping[str, Any]],
    v_line: float | None,
) -> tuple[str, str]:
    """Return ``(kv_class, source)`` for a line."""

    def side_kv(side: str) -> float | None:
        key = _ref_key(line.get(side))
        bus = buses.get(key) if key is not None else None
        kv = _num(bus.get("voltage")) if bus else None
        return kv if kv and kv > 1.0 else None  # 1.0 = p.u. placeholder

    va, vb = side_kv("bus_a"), side_kv("bus_b")
    if va and vb and abs(va - vb) > 0.05 * max(va, vb):
        return "transformer", "bus"
    if va or vb:
        kv = va or vb
        assert kv is not None
        return _kv_label(kv), "bus"
    if v_line and v_line > 1.0:
        return _kv_label(v_line), "line"
    name_kv = _kv_from_name(str(line.get("name", "")))
    if name_kv:
        return _kv_label(name_kv), "name"
    return "p.u.", "none"


def _check_lines(
    report: SanityReport,
    system: Mapping[str, Any],
    values: _Values,
    th: SanityThresholds,
) -> None:
    buses = _index(system.get("bus_array", []) or [])
    rows: list[dict[str, Any]] = []
    xr = report.finding("line_xr", f"lines with X/R > {th.line_xr_max:g}")
    below = report.finding(
        "line_below_tie",
        f"lines with |X|/V² below the tie threshold {report.tie_threshold:g} "
        "(become zero-impedance ties)",
    )
    for line in system.get("line_array", []) or []:
        if not _is_active(line):
            continue
        x = values.magnitude("Line", line, "reactance")
        if x is None or x <= 0.0:
            continue  # DC link / explicit tie
        r = values.magnitude("Line", line, "resistance")
        v_line = values.magnitude("Line", line, "voltage")
        v = v_line if v_line and v_line > 0 else 1.0
        x_eff = x / (v * v)
        r_eff = (r / (v * v)) if r is not None else None
        kv_class, source = _line_class(line, buses, v_line)
        name = str(line.get("name", line.get("uid")))
        rows.append(
            {"name": name, "kv_class": kv_class, "source": source, "x": x_eff}
            | ({"r": r_eff} if r_eff is not None else {})
        )
        if r and r > 0 and x / r > th.line_xr_max:
            xr.add(x / r, name=name, kv_class=kv_class, xr=x / r)
        if x_eff < report.tie_threshold:
            below.add(1.0 / x_eff, name=name, kv_class=kv_class, x=x_eff)

    outlier = report.finding(
        "line_x_outlier",
        f"lines whose |X|/V² is more than {th.line_x_outlier_factor:g}x off "
        "their kV-class median (mistyped kV or impedance units)",
    )
    by_class: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_class.setdefault(row["kv_class"], []).append(row)
    for cls in sorted(by_class, key=_class_sort_key):
        members = by_class[cls]
        xs = sorted(m["x"] for m in members)
        rs = sorted(m["r"] for m in members if "r" in m)
        med = _percentile(xs, 0.5)
        entry: dict[str, Any] = {
            "kv_class": cls,
            "count": len(members),
            "kv_source": sorted({m["source"] for m in members}),
            "x_median": med,
            "x_p5": _percentile(xs, 0.05),
            "x_p95": _percentile(xs, 0.95),
            "below_tie": sum(1 for v in xs if v < report.tie_threshold),
        }
        if rs:
            entry |= {
                "r_median": _percentile(rs, 0.5),
                "r_p5": _percentile(rs, 0.05),
                "r_p95": _percentile(rs, 0.95),
            }
        report.line_classes.append(entry)
        if len(members) < max(th.line_x_min_class_size, 2) or med <= 0.0:
            continue
        for m in members:
            ratio = m["x"] / med
            if (
                ratio > th.line_x_outlier_factor
                or ratio < 1.0 / th.line_x_outlier_factor
            ):
                outlier.add(
                    max(ratio, 1.0 / ratio),
                    name=m["name"],
                    kv_class=cls,
                    x=m["x"],
                    class_median=med,
                    ratio=ratio,
                )


def _class_sort_key(cls: str) -> tuple[int, float, str]:
    if cls.endswith(" kV"):
        return (0, -float(cls[:-3]), cls)
    return (1, 0.0, cls)


def _check_bus_kv(report: SanityReport, system: Mapping[str, Any]) -> None:
    fnd = report.finding(
        "bus_kv",
        "buses whose voltage is not a standard kV level "
        "(mistyped = equals the bus-name numeric suffix)",
    )
    for bus in system.get("bus_array", []) or []:
        kv = _num(bus.get("voltage"))
        if kv is None or kv <= 1.0 or _is_standard_kv(kv):
            continue  # unset, per-unit placeholder (1.0) or standard
        name = str(bus.get("name", bus.get("uid")))
        typo = mistyped_kv(kv, name)
        fnd.add(2.0 if typo else 1.0, name=name, kv=kv, mistyped=typo)


def _check_units(
    report: SanityReport, system: Mapping[str, Any], values: _Values
) -> None:
    zero_rated = report.finding(
        "zero_rated_units",
        "units with pmax/capacity <= 0 over the whole horizon",
    )
    dead_uc = report.finding(
        "zero_rated_committed",
        "zero-rated units that still carry commitment binaries or a pmin > 0",
    )

    def flag(kind: str, elem: Mapping[str, Any], why: list[str]) -> None:
        name = str(elem.get("name", elem.get("uid")))
        zero_rated.add(1.0, name=name, kind=kind)
        if why:
            dead_uc.add(float(len(why)), name=name, kind=kind, reasons=why)

    committed: set[str | int] = set()
    for arr in ("commitment_array", "simple_commitment_array"):
        for c in system.get(arr, []) or []:
            key = _ref_key(c.get("generator"))
            if key is not None and _is_active(c):
                committed.add(key)
    for gen in system.get("generator_array", []) or []:
        if not _is_active(gen):
            continue
        pmax = values.extrema("Generator", gen, "pmax")
        if pmax is not None:
            zero = pmax[1] <= 0.0
        else:
            cap = values.extrema("Generator", gen, "capacity")
            expcap = values.extrema("Generator", gen, "expcap")
            zero = cap is not None and cap[1] <= 0.0 and not (expcap and expcap[1] > 0)
        if not zero:
            continue
        why = []
        if gen.get("uid") in committed or gen.get("name") in committed:
            why.append("commitment")
        pmin = values.extrema("Generator", gen, "pmin")
        if pmin and pmin[1] > 0.0:
            why.append("pmin > 0")
        flag("Generator", gen, why)
    for bat in system.get("battery_array", []) or []:
        if not _is_active(bat):
            continue
        pc = values.extrema("Battery", bat, "pmax_charge")
        pd_ = values.extrema("Battery", bat, "pmax_discharge")
        if not (pc and pd_ and pc[1] <= 0.0 and pd_[1] <= 0.0):
            continue
        why = ["commitment"] if bat.get("commitment") else []
        for key in ("pmin_charge", "pmin_discharge"):
            ext = values.extrema("Battery", bat, key)
            if ext and ext[1] > 0.0:
                why.append(f"{key} > 0")
        flag("Battery", bat, why)


def _check_profiles(
    report: SanityReport,
    system: Mapping[str, Any],
    values: _Values,
    th: SanityThresholds,
) -> None:
    fnd = report.finding(
        "profile_range", "profiles with values outside [0, 1] (per-unit factors)"
    )
    tol = th.profile_tolerance
    for arr, cls in (
        ("generator_profile_array", "GeneratorProfile"),
        ("demand_profile_array", "DemandProfile"),
        ("capacity_profile_array", "CapacityProfile"),
    ):
        for prof in system.get(arr, []) or []:
            if not _is_active(prof):
                continue
            ext = values.extrema(cls, prof, "profile")
            if ext is None:
                continue
            lo, hi = ext
            if hi <= 1.0 + tol and lo >= -tol:
                continue
            fnd.add(
                max(hi - 1.0, -lo),
                name=str(prof.get("name", prof.get("uid"))),
                kind=cls,
                min=lo,
                max=hi,
            )


def _check_reservoir_floor(
    report: SanityReport, system: Mapping[str, Any], values: _Values
) -> None:
    fnd = report.finding(
        "reservoir_floor",
        "reservoirs with emin = 0/unset whose volume drives an agreement "
        "bound_rule with a reserve level",
    )
    reservoirs = _index(system.get("reservoir_array", []) or [])
    refs: dict[int, tuple[Mapping[str, Any], float, list[str]]] = {}
    for arr, cls in (
        ("flow_right_array", "FlowRight"),
        ("volume_right_array", "VolumeRight"),
    ):
        for right in system.get(arr, []) or []:
            rule = right.get("bound_rule")
            if not _is_active(right) or not isinstance(rule, Mapping):
                continue
            if rule.get("axis", "reservoir_volume") != "reservoir_volume":
                continue
            levels = [
                v
                for seg in rule.get("segments", []) or []
                if (v := _num(seg.get("volume"))) is not None
            ]
            level = max(levels, default=0.0)
            key = _ref_key(rule.get("reservoir"))
            res = reservoirs.get(key) if key is not None else None
            if level <= 0.0 or res is None:
                continue
            _, old, who = refs.get(id(res), (res, 0.0, []))
            refs[id(res)] = (
                res,
                max(old, level),
                [*who, f"{cls} '{right.get('name')}'"],
            )
    for res, level, who in refs.values():
        if not _is_active(res):
            continue
        if res.get("emin") is not None:
            ext = values.extrema("Reservoir", res, "emin")
            if ext is None or ext[1] > 0.0:
                continue  # has a floor (or one we cannot read)
        fnd.add(level, name=str(res.get("name")), reserve_level=level, rights=who)


def _reference_fail_cost(planning: Mapping[str, Any], values: _Values) -> float | None:
    model = (planning.get("options", {}) or {}).get("model_options", {}) or {}
    glob = _num(model.get("demand_fail_cost"))
    if glob and glob > 0:
        return glob
    best: float | None = None
    for dem in (planning.get("system", {}) or {}).get("demand_array", []) or []:
        ext = values.extrema("Demand", dem, "fcost")
        if ext and ext[0] > 0:
            best = ext[0] if best is None else min(best, ext[0])
    return best


def _check_rewards(
    report: SanityReport,
    planning: Mapping[str, Any],
    values: _Values,
    th: SanityThresholds,
) -> None:
    fail = _reference_fail_cost(planning, values)
    fnd = report.finding(
        "reward_vs_fail_cost",
        "water-right rewards above "
        f"{th.reward_fail_cost_ratio:g} x demand fail cost "
        f"({'n/a' if fail is None else f'{fail:g}'}) — phantom reward",
    )
    if fail is None:
        return
    limit = th.reward_fail_cost_ratio * fail
    system = planning.get("system", {}) or {}
    for fr in system.get("flow_right_array", []) or []:
        if not _is_active(fr):
            continue
        name = str(fr.get("name", fr.get("uid")))
        uv = values.extrema("FlowRight", fr, "uvalue")
        if uv and uv[1] > limit:
            fnd.add(uv[1], name=name, field="uvalue", value=uv[1])
        fc = values.extrema("FlowRight", fr, "fcost")
        if fc and -fc[0] > limit:
            fnd.add(-fc[0], name=name, field="fcost", value=fc[0])
    model = (planning.get("options", {}) or {}).get("model_options", {}) or {}
    huv = _num(model.get("hydro_use_value"))
    if huv is not None and huv > limit:
        fnd.add(huv, name="model_options", field="hydro_use_value", value=huv)


_STATEMENT_SPLIT = re.compile(r";")
_COMMENT = re.compile(r"#[^\n]*")
_CONSTRAINT_NAME = re.compile(r"constraint\s+([A-Za-z_][\w.]*)")


def _constraint_texts(
    system: Mapping[str, Any], case_dir: str | Path | None, input_root: Path | None
) -> list[tuple[str, str]]:
    """``(name, expression)`` of every user constraint, inline and PAMPL.

    PAMPL / JSON constraint files are resolved like the C++ loader
    (``input_directory`` first, then the case directory).  PAMPL text is
    split on ``;`` with comments stripped; ``param`` statements never
    reference rights, so they are harmless.
    """
    out: list[tuple[str, str]] = []
    for uc in system.get("user_constraint_array", []) or []:
        if uc.get("active", True) is False:
            continue
        out.append((str(uc.get("name", uc.get("uid"))), str(uc.get("expression", ""))))
    files = list(system.get("user_constraint_files", []) or [])
    if system.get("user_constraint_file"):
        files.append(system["user_constraint_file"])
    for raw in files:
        path = Path(str(raw))
        candidates = [path] if path.is_absolute() else []
        for base in (input_root, Path(case_dir) if case_dir is not None else None):
            if base is not None and not path.is_absolute():
                candidates.append(base / path)
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            continue
        text = found.read_text(encoding="utf-8", errors="replace")
        if found.suffix == ".json":
            for uc in json.loads(text):
                out.append(
                    (str(uc.get("name", uc.get("uid"))), str(uc.get("expression", "")))
                )
            continue
        for stmt in _STATEMENT_SPLIT.split(_COMMENT.sub("", text)):
            match = _CONSTRAINT_NAME.search(stmt)
            if match:
                out.append((match.group(1), stmt))
    return out


def _mentions(expr: str, right: Mapping[str, Any]) -> bool:
    name = right.get("name")
    needles = [f"flow_right('{name}')", f'flow_right("{name}")']
    if _ref_key(right.get("uid")) is not None:
        needles.append(f"flow_right({_ref_key(right.get('uid'))})")
    return any(n in expr for n in needles)


def _check_coupled_rewards(
    report: SanityReport,
    planning: Mapping[str, Any],
    values: _Values,
    th: SanityThresholds,
    case_dir: str | Path | None,
) -> None:
    """Reward on a right above the shortfall penalty of a coupled right.

    Maule Res. 105 (review R3): ``maule_resolucion_105`` rewards 1100 while
    ``deficit_Melado`` (``uvalue = -1000`` on a target-0 right) is tied to
    it by ``dist_retiro_Melado``: the LP raises the rewarded flow and books
    the matching amount as shortfall, ~-2.1 G$ of phantom reward on plp_2y.
    """
    fnd = report.finding(
        "reward_vs_coupled_penalty",
        f"water-right rewards above {th.reward_fail_cost_ratio:g} x the "
        "shortfall penalty of a right coupled to them by a user constraint "
        "— the LP books shortfall to collect the reward (phantom reward)",
    )
    system = planning.get("system", {}) or {}
    rights = [r for r in system.get("flow_right_array", []) or [] if _is_active(r)]
    rewards: list[tuple[Mapping[str, Any], float]] = []
    penalties: list[tuple[Mapping[str, Any], float]] = []
    for right in rights:
        uv = values.extrema("FlowRight", right, "uvalue")
        fc = values.extrema("FlowRight", right, "fcost")
        if uv and uv[1] > 0.0:
            rewards.append((right, uv[1]))
        pens = [-uv[1]] if uv and uv[1] < 0.0 else []
        pens += [fc[0]] if fc and fc[0] > 0.0 else []
        if pens:
            penalties.append((right, min(pens)))
    if not rewards or not penalties:
        return
    input_root = values.resolver.input_root if values.resolver else None
    for cname, expr in _constraint_texts(system, case_dir, input_root):
        for right, reward in rewards:
            if not _mentions(expr, right):
                continue
            for other, pen in penalties:
                if other is right or reward <= th.reward_fail_cost_ratio * pen:
                    continue
                if _mentions(expr, other):
                    fnd.add(
                        reward - pen,
                        name=str(right.get("name")),
                        reward=reward,
                        penalised=str(other.get("name")),
                        penalty=pen,
                        constraint=cname,
                    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def build_sanity_report(
    planning: Mapping[str, Any],
    case_dir: str | Path | None = None,
    *,
    thresholds: SanityThresholds | None = None,
    case: str | None = None,
) -> SanityReport:
    """Run every check on ``planning`` (input tables under ``case_dir``)."""
    th = thresholds or SanityThresholds()
    system = planning.get("system", {}) or {}
    model = (planning.get("options", {}) or {}).get("model_options", {}) or {}
    tie = _num(model.get("dc_line_reactance_threshold"))
    report = SanityReport(
        case=case or str(system.get("name") or "case"),
        tie_threshold=DEFAULT_TIE_THRESHOLD if tie is None else tie,
    )
    resolver = (
        StageSchedResolver.from_planning(planning, case_dir)
        if case_dir is not None
        else None
    )
    values = _Values(resolver)
    _check_lines(report, system, values, th)
    _check_bus_kv(report, system)
    _check_units(report, system, values)
    _check_profiles(report, system, values, th)
    _check_reservoir_floor(report, system, values)
    _check_rewards(report, planning, values, th)
    _check_coupled_rewards(report, planning, values, th, case_dir)
    for fnd in report.findings.values():
        fnd.sort()
    return report


def _describe(item: Mapping[str, Any]) -> str:
    extras = ", ".join(
        f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
        for k, v in item.items()
        if k not in ("score", "name") and v not in (None, [], "")
    )
    return f"{item.get('name')} ({extras})" if extras else str(item.get("name"))


def log_sanity_report(
    report: SanityReport,
    log: logging.Logger | None = None,
    *,
    max_offenders: int = SanityThresholds.max_offenders,
    min_class_size: int = SanityThresholds.line_x_min_class_size,
) -> None:
    """Concise log summary: per-kV table, then one line per flagged category."""
    log = log or logger
    small = [c for c in report.line_classes if c["count"] < min_class_size]
    for cls in report.line_classes:
        if cls["count"] < min_class_size:
            continue
        log.info(
            "sanity: lines %-12s n=%-5d x(|X|/V²) median=%.3g p5=%.3g p95=%.3g"
            " below_tie=%d%s",
            cls["kv_class"],
            cls["count"],
            cls["x_median"],
            cls["x_p5"],
            cls["x_p95"],
            cls["below_tie"],
            f" r median={cls['r_median']:.3g}" if "r_median" in cls else "",
        )
    if small:
        log.info(
            "sanity: lines in %d small kV class(es) (< %d lines): %d line(s), "
            "%d below tie",
            len(small),
            min_class_size,
            sum(c["count"] for c in small),
            sum(c["below_tie"] for c in small),
        )
    flagged = report.flagged
    if not flagged:
        log.info("sanity: %s — no data-quality issue found", report.case)
        return
    for fnd in flagged.values():
        top = "; ".join(_describe(o) for o in fnd.offenders[:max_offenders])
        more = fnd.count - min(fnd.count, max_offenders)
        log.warning(
            "sanity: %d %s — top: %s%s",
            fnd.count,
            fnd.message,
            top,
            f" (+{more} more)" if more else "",
        )
        for item in fnd.offenders[max_offenders:]:
            log.debug("sanity: %s: %s", fnd.category, _describe(item))


def write_sanity_report(report: SanityReport, path: str | Path) -> Path:
    """Write the report as JSON; return the path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return out


def run_sanity_report(
    planning_or_path: Mapping[str, Any] | str | Path,
    case_dir: str | Path | None = None,
    *,
    json_path: str | Path | None = None,
    log: logging.Logger | None = None,
    thresholds: SanityThresholds | None = None,
) -> SanityReport | None:
    """Converter hook: build, log and optionally write the report.

    Accepts the planning dict or the path of the written JSON (then
    ``case_dir`` defaults to its directory).  Never raises: a failure is
    logged as a warning and ``None`` returned, so a sanity-report bug
    cannot break a conversion.
    """
    log = log or logger
    try:
        if isinstance(planning_or_path, (str, Path)):
            path = Path(planning_or_path)
            planning = json.loads(path.read_text(encoding="utf-8"))
            case_dir = path.parent if case_dir is None else case_dir
            case = path.stem
        else:
            planning = planning_or_path
            case = None
        report = build_sanity_report(
            planning, case_dir, thresholds=thresholds, case=case
        )
        th = thresholds or SanityThresholds()
        log_sanity_report(
            report,
            log,
            max_offenders=th.max_offenders,
            min_class_size=th.line_x_min_class_size,
        )
        if json_path:
            log.info(
                "sanity: report written to %s", write_sanity_report(report, json_path)
            )
        return report
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.warning("sanity: report skipped (%s: %s)", type(exc).__name__, exc)
        return None


def add_sanity_report_argument(parser: argparse.ArgumentParser) -> None:
    """Install ``--sanity-report PATH`` on a converter parser."""
    parser.add_argument(
        "--sanity-report",
        dest="sanity_report",
        metavar="PATH",
        default=None,
        help=(
            "write the post-conversion data-quality report (line impedance "
            "per kV, zero-rated units, profiles outside [0,1], reservoir "
            "floors, rewards vs fail cost, mistyped kV) to PATH as JSON; "
            "the summary is always logged"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: report on an existing case (read-only)."""
    parser = argparse.ArgumentParser(
        prog="python -m gtopt_shared.sanity_report",
        description="Data-quality report for a gtopt planning JSON.",
    )
    parser.add_argument("planning", type=Path, help="planning JSON file")
    add_sanity_report_argument(parser)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="list every offender"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    report = run_sanity_report(args.planning, json_path=args.sanity_report)
    return 0 if report is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
