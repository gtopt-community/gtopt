"""``--horizon-days N``: cut a multi-day PLEXOS case to its first N days.

A CEN PCP bundle is a 7-day problem: its storage targets, fuel budgets
and future-cost (FCF) cut all refer to the END OF THE WEEK.  Converting
only the first N days (``--horizon-mode hourly --horizon-days N``) moves
the end of the horizon to the end of day N, so every end-of-horizon
quantity must be re-anchored there.  This module does that as one
post-build pass over the planning dict (:func:`apply_horizon`), and
records every decision in a :class:`HorizonReport` that goes to the log
and to the provenance sidecar.

End-of-horizon targets (``--horizon-end-targets``)
--------------------------------------------------

``auto`` (default)
    ``plexos`` when the PLEXOS solution (RES bundle) is available,
    ``eini`` otherwise (with a warning).  Applied only when the horizon
    is actually truncated; a full-horizon run keeps the converter's
    input-driven targets.
``plexos``
    ``efin`` = the PLEXOS state at the end of day N: Storage ``End
    Volume`` for reservoirs (and gas storages / LNG tanks when pinned),
    Battery ``Energy`` for batteries.  Elements absent from the solution
    fall back to ``eini`` (warned).
``eini``
    ``efin = eini`` (return to the start state).
``free``
    no ``efin`` at all (only the FCF cut, if kept, values the end state).

Every target is clamped into the element's ``[emin, emax]`` at the last
block, so a rounding excess (PLEXOS 1263.2846 vs ``emax`` 1263.2845)
cannot make the LP infeasible.

Reservoirs keep their ``efin_cost`` if the converter set one (else the
target is a hard ``vol_end >= efin``, as in the hand-made day-1 cases).
Batteries always get ``daily_cycle = false`` + ``use_state_variable =
true`` and a SOFT target (``efin_cost`` = the writer's battery
penalty): gtopt's default daily cycle is a HARD ``energy[N] == eini``
close, infeasible for a battery that self-discharges and cannot recharge
(PCP 2024-12-15 day 1, BAT_ALFALFAL_VR2).

LNG tanks (``lng_terminal_array`` elements, ``*_GNL*`` reservoirs and
reservoirs whose turbines feed fuel-burning generators) are NOT pinned
by default: their end level is the take-or-pay decision of the study, and
the converter's gas terminal value prices it.  ``--horizon-pin-lng-tanks``
pins them like reservoirs.

Multi-day constraints (``--horizon-constraint-rules FAMILY=RULE,...``)
---------------------------------------------------------------------

=================  ===========  ============================================
family             default      rules
=================  ===========  ============================================
fuel_max_offtake   prorate      ``prorate``: the parser's weekly cap x days
                                overlapping the horizon / 7 (as emitted);
                                ``full``: the whole weekly budget available
                                in N days (x full_days / N); ``drop``.
fuel_min_offtake   prorate      take-or-pay floor x N / window (as emitted);
                                ``drop``.  A full weekly floor in one day
                                would force a week of gas into a day.
period_rhs         prorate      PLEXOS ``RHS Week/Month/Year`` stage rows,
                                RHS x horizon hours / period hours (as
                                emitted); ``drop`` deactivates them.
day_indexed        keep         per-day rows (``Gas_MaxOpDay`` Day_X
                                budgets): day d keeps PLEXOS Day_X of the
                                same day, the days past N simply vanish;
                                ``drop`` deactivates them.
=================  ===========  ============================================

The rules act only on a truncated horizon.

Boundary / FCF cut (``--horizon-boundary-cut``)
-----------------------------------------------

The cut is the end-of-WEEK future cost.  ``auto`` drops it on a
truncated horizon whose targets are pinned (``plexos``/``eini``: the
target already carries the end state, and an end-of-week water value on
top would reward hoarding above it) and keeps it with ``free`` targets
(then it is the only terminal valuation).  Rescaling it is not
meaningful: its slopes are marginal water values ($/volume, horizon
independent to first order) and its intercept is a constant that does
not change the decisions.  ``keep`` / ``drop`` force either.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .plexos_solution import PlexosSolution

logger = logging.getLogger(__name__)

END_TARGET_MODES: tuple[str, ...] = ("auto", "plexos", "eini", "free")
BOUNDARY_CUT_MODES: tuple[str, ...] = ("auto", "keep", "drop")

#: family -> allowed rules (the first one is the default).
CONSTRAINT_FAMILIES: dict[str, tuple[str, ...]] = {
    "fuel_max_offtake": ("prorate", "full", "drop"),
    "fuel_min_offtake": ("prorate", "drop"),
    "period_rhs": ("prorate", "drop"),
    "day_indexed": ("keep", "drop"),
}
DEFAULT_CONSTRAINT_RULES: dict[str, str] = {
    fam: rules[0] for fam, rules in CONSTRAINT_FAMILIES.items()
}

#: (system array, PLEXOS class, solution property, family tag).
_STORAGE_FAMILIES: tuple[tuple[str, str, str, str], ...] = (
    ("reservoir_array", "Storage", "End Volume", "reservoir"),
    ("lng_terminal_array", "Storage", "End Volume", "lng_tank"),
    ("gas_storage_array", "Gas Storage", "End Volume", "gas_storage"),
    ("battery_array", "Battery", "Energy", "battery"),
)


def parse_constraint_rules(spec: str | None) -> dict[str, str]:
    """``"fuel_max_offtake=full,period_rhs=drop"`` -> the full rule map."""
    rules = dict(DEFAULT_CONSTRAINT_RULES)
    if not spec:
        return rules
    for item in str(spec).split(","):
        item = item.strip()
        if not item:
            continue
        fam, sep, rule = item.partition("=")
        fam, rule = fam.strip(), rule.strip()
        if not sep or fam not in CONSTRAINT_FAMILIES:
            raise ValueError(
                f"--horizon-constraint-rules: bad item {item!r} (families: "
                f"{', '.join(CONSTRAINT_FAMILIES)})"
            )
        if rule not in CONSTRAINT_FAMILIES[fam]:
            raise ValueError(
                f"--horizon-constraint-rules: {fam} takes "
                f"{'/'.join(CONSTRAINT_FAMILIES[fam])}, not {rule!r}"
            )
        rules[fam] = rule
    return rules


@dataclass(frozen=True)
class HorizonOptions:
    """What ``--horizon-days`` and its companions asked for."""

    horizon_days: int
    full_days: int | None = None
    end_targets: str = "auto"
    boundary_cut: str = "auto"
    rules: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_CONSTRAINT_RULES)
    )
    pin_lng_tanks: bool = False

    @property
    def truncated(self) -> bool:
        """The converted horizon is shorter than the PLEXOS one."""
        return self.full_days is not None and 0 < self.horizon_days < self.full_days

    @property
    def needs_solution(self) -> bool:
        """The end targets want the PLEXOS solution."""
        return self.end_targets == "plexos" or (
            self.end_targets == "auto" and self.truncated
        )

    @classmethod
    def from_options(
        cls, options: Mapping[str, Any], *, horizon_days: int, full_days: int | None
    ) -> HorizonOptions:
        """Build from the converter options dict."""
        end_targets = str(options.get("horizon_end_targets") or "auto")
        if end_targets not in END_TARGET_MODES:
            raise ValueError(f"horizon_end_targets must be one of {END_TARGET_MODES}")
        boundary_cut = str(options.get("horizon_boundary_cut") or "auto")
        if boundary_cut not in BOUNDARY_CUT_MODES:
            raise ValueError(
                f"horizon_boundary_cut must be one of {BOUNDARY_CUT_MODES}"
            )
        return cls(
            horizon_days=int(horizon_days),
            full_days=full_days,
            end_targets=end_targets,
            boundary_cut=boundary_cut,
            rules=parse_constraint_rules(options.get("horizon_constraint_rules")),
            pin_lng_tanks=bool(options.get("horizon_pin_lng_tanks", False)),
        )


@dataclass
class HorizonReport:
    """Everything :func:`apply_horizon` decided, for the log + provenance."""

    horizon_days: int
    full_days: int | None
    truncated: bool
    end_targets: str = "input"
    boundary_cut: str = "keep"
    rules: dict[str, str] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        """Log a warning and keep it in the report."""
        logger.warning("horizon: %s", msg)
        self.warnings.append(msg)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready view (provenance sidecar)."""
        return asdict(self)


def resolve_end_targets(opts: HorizonOptions, has_solution: bool) -> str:
    """The effective target mode: ``plexos|eini|free`` or ``input`` (no-op)."""
    mode = opts.end_targets
    if mode == "auto":
        if not opts.truncated:
            return "input"
        return "plexos" if has_solution else "eini"
    return mode


def resolve_boundary_cut(opts: HorizonOptions, end_mode: str) -> str:
    """``keep`` or ``drop`` the FCF boundary cut."""
    if opts.boundary_cut != "auto":
        return opts.boundary_cut
    if opts.truncated and end_mode in ("plexos", "eini"):
        return "drop"
    return "keep"


def solution_end_states(
    solution: PlexosSolution, day: int
) -> dict[tuple[str, str], float]:
    """``{(PLEXOS class, object): state at the end of day}`` for storages."""
    wanted = {(cls, prop) for _arr, cls, prop, _tag in _STORAGE_FAMILIES}
    out: dict[tuple[str, str], float] = {}
    for key, series in solution.fetch(sorted(wanted)).items():
        v = solution.end_of_day(series, day)
        if v is not None:
            out[(key.class_name, key.object_name)] = v
    return out


def lng_tank_names(system: Mapping[str, Any]) -> set[str]:
    """Storages that are LNG tanks, not water reservoirs.

    ``lng_terminal_array`` elements, reservoirs named ``*_GNL*``, and
    reservoirs whose turbines feed a fuel-burning (``heat_rate``)
    generator -- the CEN "LNG as Storage" idiom.
    """
    thermal = {
        g.get("name") for g in system.get("generator_array", []) if g.get("heat_rate")
    }
    names = {t.get("name") for t in system.get("lng_terminal_array", [])}
    names |= {
        t.get("main_reservoir")
        for t in system.get("turbine_array", [])
        if t.get("generator") in thermal and t.get("main_reservoir")
    }
    names |= {
        r.get("name")
        for r in system.get("reservoir_array", [])
        if "_GNL" in str(r.get("name", "")).upper()
    }
    return {n for n in names if n}


def _last_value(value: Any) -> float | None:
    """Last numeric entry of a scalar / (nested) per-block profile."""
    while isinstance(value, list):
        if not value:
            return None
        value = value[-1]
    return float(value) if isinstance(value, (int, float)) else None


def _clamp(entry: Mapping[str, Any], value: float) -> tuple[float, bool]:
    lo = _last_value(entry.get("emin"))
    hi = _last_value(entry.get("emax"))
    out = value
    if hi is not None and 0.0 < hi < out:
        out = hi
    if lo is not None and out < lo:
        out = lo
    return out, out != value


def _battery_efin_cost() -> float:
    from .gtopt_writer import (  # pylint: disable=import-outside-toplevel
        _BATTERY_EFIN_COST,
    )

    return float(_BATTERY_EFIN_COST)


def apply_end_targets(
    system: dict[str, Any],
    mode: str,
    states: Mapping[tuple[str, str], float],
    report: HorizonReport,
    *,
    day: int,
    pin_lng_tanks: bool = False,
) -> None:
    """Set ``efin`` on every storage element per ``mode`` (in place)."""
    if mode == "input":
        return
    lng = lng_tank_names(system)
    bat_cost = _battery_efin_cost()
    for array, plexos_cls, _prop, tag in _STORAGE_FAMILIES:
        for entry in system.get(array, []) or []:
            name = str(entry.get("name", ""))
            is_lng = tag == "lng_tank" or name in lng
            family = "lng_tank" if is_lng else tag
            old = entry.get("efin")
            rec: dict[str, Any] = {
                "array": array,
                "element": name,
                "family": family,
                "eini": entry.get("eini"),
                "old_efin": old,
            }
            if tag == "battery":
                # Never leave a battery on gtopt's hard daily-cycle close.
                entry["daily_cycle"] = False
                entry["use_state_variable"] = True
            if is_lng and not pin_lng_tanks:
                rec.update(new_efin=old, source="lng-unpinned")
                report.targets.append(rec)
                continue
            if mode == "free":
                entry.pop("efin", None)
                entry.pop("efin_cost", None)
                rec.update(new_efin=None, source="free")
                report.targets.append(rec)
                continue
            source = mode
            target: float | None = None
            if mode == "plexos":
                target = states.get((plexos_cls, name))
                if target is None:
                    source = "eini-fallback"
                    report.warn(
                        f"{family} {name}: no PLEXOS {plexos_cls} state at the "
                        f"end of day {day}; efin = eini"
                    )
            if target is None:
                eini = entry.get("eini")
                target = float(eini) if isinstance(eini, (int, float)) else 0.0
            target, clamped = _clamp(entry, target)
            entry["efin"] = target
            if tag == "battery" and target > 0.0 and not entry.get("efin_cost"):
                entry["efin_cost"] = bat_cost
            rec.update(new_efin=target, source=source, clamped=clamped)
            report.targets.append(rec)
            desc = entry.get("description")
            if isinstance(desc, str):
                entry["description"] = (
                    f"{desc}; --horizon-days {day}: efin = {target:.6g} "
                    f"({source}; was {old})"
                )


def _uc_family(uc: Mapping[str, Any]) -> str | None:
    if uc.get("scope") == "stage":
        return "period_rhs"
    directive = uc.get("directive") or {}
    if directive.get("kind") == "daily_budget" or (
        uc.get("daily_sum") and isinstance(uc.get("rhs"), list)
    ):
        return "day_indexed"
    return None


def apply_constraint_rules(
    system: dict[str, Any], opts: HorizonOptions, report: HorizonReport
) -> None:
    """Prorate / restore / deactivate the multi-day families (in place)."""
    if not opts.truncated:
        return
    assert opts.full_days is not None
    scale_full = opts.full_days / opts.horizon_days
    for fuel in system.get("fuel_array", []) or []:
        for fam, key in (
            ("fuel_max_offtake", "max_offtake"),
            ("fuel_min_offtake", "min_offtake"),
        ):
            value = fuel.get(key)
            if not isinstance(value, (int, float)):
                continue
            rule = opts.rules[fam]
            new: float | None = float(value)
            if rule == "full":
                new = float(value) * scale_full
                fuel[key] = new
            elif rule == "drop":
                fuel.pop(key, None)
                fuel.pop(f"{key}_cost", None)
                new = None
            if value or rule != "prorate":
                report.constraints.append(
                    {
                        "family": fam,
                        "element": fuel.get("name"),
                        "rule": rule,
                        "old": value,
                        "new": new,
                    }
                )
    for uc in system.get("user_constraint_array", []) or []:
        uc_fam = _uc_family(uc)
        if uc_fam is None:
            continue
        rule = opts.rules[uc_fam]
        if rule == "drop":
            uc["active"] = False
        report.constraints.append(
            {"family": uc_fam, "element": uc.get("name"), "rule": rule}
        )


def apply_horizon(
    planning: dict[str, Any],
    opts: HorizonOptions,
    solution: PlexosSolution | None,
) -> HorizonReport:
    """Re-anchor ``planning`` at the end of day ``opts.horizon_days``.

    Mutates the planning dict; the caller drops the FCF cut when
    ``report.boundary_cut == "drop"``.
    """
    report = HorizonReport(
        horizon_days=opts.horizon_days,
        full_days=opts.full_days,
        truncated=opts.truncated,
        rules=dict(opts.rules) if opts.truncated else {},
    )
    system = planning.setdefault("system", {})
    mode = resolve_end_targets(opts, solution is not None)
    if opts.end_targets == "auto" and mode == "eini":
        report.warn(
            "no PLEXOS solution (RES bundle) for the end-of-day "
            f"{opts.horizon_days} storage states; targets fall back to efin = eini"
        )
    states: dict[tuple[str, str], float] = {}
    if mode == "plexos":
        if solution is None:
            report.warn(
                "--horizon-end-targets plexos without a PLEXOS solution; "
                "targets fall back to efin = eini"
            )
        else:
            states = solution_end_states(solution, opts.horizon_days)
    report.end_targets = mode
    apply_end_targets(
        system,
        mode,
        states,
        report,
        day=opts.horizon_days,
        pin_lng_tanks=opts.pin_lng_tanks,
    )
    apply_constraint_rules(system, opts, report)
    report.boundary_cut = resolve_boundary_cut(opts, mode)
    if report.boundary_cut == "drop":
        system.pop("future_cost_array", None)
    log_report(report)
    return report


def log_report(report: HorizonReport) -> None:
    """One INFO summary + per-element DEBUG lines."""
    if not report.truncated and report.end_targets == "input":
        return
    by_source: dict[str, int] = {}
    for rec in report.targets:
        key = f"{rec['family']}:{rec['source']}"
        by_source[key] = by_source.get(key, 0) + 1
    by_rule: dict[str, int] = {}
    for rec in report.constraints:
        key = f"{rec['family']}={rec['rule']}"
        by_rule[key] = by_rule.get(key, 0) + 1
    logger.info(
        "horizon: %d of %s day(s); end targets=%s %s; constraints %s; boundary cut %s",
        report.horizon_days,
        report.full_days if report.full_days is not None else "?",
        report.end_targets,
        dict(sorted(by_source.items())),
        dict(sorted(by_rule.items())) or "{}",
        report.boundary_cut,
    )
    for rec in report.targets:
        logger.debug("horizon target: %s", rec)


__all__ = [
    "BOUNDARY_CUT_MODES",
    "CONSTRAINT_FAMILIES",
    "DEFAULT_CONSTRAINT_RULES",
    "END_TARGET_MODES",
    "HorizonOptions",
    "HorizonReport",
    "apply_constraint_rules",
    "apply_end_targets",
    "apply_horizon",
    "lng_tank_names",
    "parse_constraint_rules",
    "resolve_boundary_cut",
    "resolve_end_targets",
    "solution_end_states",
]
