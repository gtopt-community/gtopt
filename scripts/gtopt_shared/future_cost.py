"""FutureCost (FCF boundary cuts) helpers shared by every gtopt tool.

The ``FutureCost`` system element (``system.future_cost_array``) is the ONE
place where the future-cost-function boundary cuts are configured, for every
planning method.  The per-method keys that preceded it survive only as
deprecated aliases that gtopt (``resolve_future_cost_aliases`` in
``source/future_cost_aliases.cpp``) folds into the element at planning load:

* ``sddp_options.boundary_*`` / ``missing_cut_var_mode`` — honoured only when
  ``options.method`` is ``sddp`` or ``cascade``;
* ``monolithic_options.boundary_*`` — honoured only for ``monolithic`` (the
  default method);
* ``simulation.boundary_cuts_file`` / ``boundary_cuts_valuation`` — always.

A key of the other method is ignored.  The same field with different values
in the element and an alias (or in two aliases) is an error.

Writers use :func:`set_future_cost_fields`; readers that consume existing
JSON use :func:`effective_future_cost` (non-mutating) or
:func:`resolve_legacy_future_cost_keys` (in-place migration).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

#: Fields of the C++ ``FutureCost`` element
#: (``include/gtopt/json/json_future_cost.hpp``).
FUTURE_COST_FIELDS: tuple[str, ...] = (
    "uid",
    "name",
    "active",
    "description",
    "cuts_file",
    "scale_alpha",
    "mean_shift",
    "single_cut_equality",
    "sharing",
    "mode",
    "iteration_limit",
    "missing_cut_var_mode",
    "valuation",
    "use_user_alpha",
    "user_alpha_uid",
)

_SDDP_METHODS = frozenset({"sddp", "cascade"})
_MONOLITHIC_METHODS = frozenset({"monolithic"})


@dataclass(frozen=True)
class LegacyAlias:
    """One deprecated key and the FutureCost field it maps to."""

    section: str  # "sddp_options" | "monolithic_options" | "simulation"
    key: str
    target: str
    methods: frozenset[str] | None  # None = any method

    @property
    def dotted(self) -> str:
        """``section.key`` as written in the deprecation messages."""
        return f"{self.section}.{self.key}"


#: Every deprecated boundary-cut key, in the order gtopt folds them
#: (method-neutral ``simulation`` keys first).
LEGACY_ALIASES: tuple[LegacyAlias, ...] = (
    LegacyAlias("simulation", "boundary_cuts_file", "cuts_file", None),
    LegacyAlias("simulation", "boundary_cuts_valuation", "valuation", None),
    LegacyAlias("sddp_options", "boundary_cuts_file", "cuts_file", _SDDP_METHODS),
    LegacyAlias("sddp_options", "boundary_cuts_mode", "mode", _SDDP_METHODS),
    LegacyAlias("sddp_options", "boundary_cut_sharing_mode", "sharing", _SDDP_METHODS),
    LegacyAlias(
        "sddp_options", "boundary_cuts_mean_shift", "mean_shift", _SDDP_METHODS
    ),
    LegacyAlias(
        "sddp_options", "boundary_max_iterations", "iteration_limit", _SDDP_METHODS
    ),
    LegacyAlias(
        "sddp_options",
        "missing_cut_var_mode",
        "missing_cut_var_mode",
        _SDDP_METHODS,
    ),
    LegacyAlias(
        "monolithic_options", "boundary_cuts_file", "cuts_file", _MONOLITHIC_METHODS
    ),
    LegacyAlias(
        "monolithic_options", "boundary_cuts_mode", "mode", _MONOLITHIC_METHODS
    ),
    LegacyAlias(
        "monolithic_options",
        "boundary_cut_sharing_mode",
        "sharing",
        _MONOLITHIC_METHODS,
    ),
    LegacyAlias(
        "monolithic_options",
        "boundary_max_iterations",
        "iteration_limit",
        _MONOLITHIC_METHODS,
    ),
)

#: ``(section, key)`` of every deprecated boundary-cut key.
LEGACY_KEYS: frozenset[tuple[str, str]] = frozenset(
    (a.section, a.key) for a in LEGACY_ALIASES
)


@dataclass
class AliasReport:
    """Outcome of :func:`resolve_legacy_future_cost_keys`."""

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    element: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """True when no conflicting values were found."""
        return not self.errors


def planning_method(planning: dict[str, Any]) -> str:
    """The planning method (``options.method``), default ``monolithic``."""
    opts = planning.get("options") or {}
    method = opts.get("method") if isinstance(opts, dict) else None
    return str(method) if method else "monolithic"


def _is_active(element: dict[str, Any]) -> bool:
    active = element.get("active")
    return not (active is False or active == 0)


def find_future_cost(planning: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first active ``future_cost_array`` element, or ``None``."""
    system = planning.get("system")
    if not isinstance(system, dict):
        return None
    for element in system.get("future_cost_array") or []:
        if isinstance(element, dict) and _is_active(element):
            return element
    return None


def ensure_future_cost(planning: dict[str, Any]) -> dict[str, Any]:
    """Return the first active FutureCost element, creating ``{uid, name}``.

    The created element is ``{"uid": 1, "name": "fcf"}`` (or the next free
    uid when inactive elements already exist), matching gtopt.
    """
    found = find_future_cost(planning)
    if found is not None:
        return found
    system = planning.setdefault("system", {})
    arr = system.setdefault("future_cost_array", [])
    uid = 1
    for element in arr:
        if isinstance(element, dict) and isinstance(element.get("uid"), int):
            uid = max(uid, element["uid"] + 1)
    element = {"uid": uid, "name": "fcf"}
    arr.append(element)
    return element


def set_future_cost_fields(
    planning: dict[str, Any],
    fields: dict[str, Any],
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Write FutureCost ``fields`` (``None`` values skipped) into the element.

    Creates the element when absent.  With ``overwrite=False`` a field the
    element already carries is left untouched.  Returns the element.
    """
    unknown = set(fields) - set(FUTURE_COST_FIELDS)
    if unknown:
        raise KeyError(f"not FutureCost fields: {sorted(unknown)}")
    element = ensure_future_cost(planning)
    for key, value in fields.items():
        if value is None:
            continue
        if not overwrite and key in element:
            continue
        element[key] = value
    return element


def legacy_keys_present(planning: dict[str, Any]) -> list[str]:
    """Dotted names of every deprecated boundary-cut key present."""
    found: list[str] = []
    for alias in LEGACY_ALIASES:
        section = _section(planning, alias.section)
        if section is not None and section.get(alias.key) is not None:
            found.append(alias.dotted)
    return found


def _section(planning: dict[str, Any], name: str) -> dict[str, Any] | None:
    if name == "simulation":
        sec = planning.get("simulation")
    else:
        opts = planning.get("options")
        sec = opts.get(name) if isinstance(opts, dict) else None
    return sec if isinstance(sec, dict) else None


def resolve_legacy_future_cost_keys(planning: dict[str, Any]) -> AliasReport:
    """Fold the deprecated keys into the FutureCost element, IN PLACE.

    Mirrors gtopt's ``resolve_future_cost_aliases``: honoured aliases are
    moved into the first active element (created when needed), keys of the
    other method are dropped, and every legacy key is removed.  Conflicts
    (different values for the same field) are reported in
    :attr:`AliasReport.errors`; the conflicting legacy key is left in place.
    """
    report = AliasReport()
    method = planning_method(planning)
    origin: dict[str, str] = {}

    for alias in LEGACY_ALIASES:
        section = _section(planning, alias.section)
        if section is None or section.get(alias.key) is None:
            continue
        value = section[alias.key]
        if alias.methods is not None and method not in alias.methods:
            report.warnings.append(
                f"Option '{alias.dotted}' is ignored under method={method} "
                "(a method never reads another method's options) — "
                "configure the boundary cuts on the FutureCost element "
                "(system.future_cost_array) instead"
            )
            del section[alias.key]
            continue
        element = ensure_future_cost(planning)
        new_key = f"future_cost_array[uid={element.get('uid')}].{alias.target}"
        if element.get(alias.target) is not None:
            if element[alias.target] != value:
                report.errors.append(
                    f"FutureCost: conflicting values for '{alias.target}': "
                    f"{element[alias.target]!r} (from "
                    f"{origin.get(alias.target, new_key)}) vs {value!r} "
                    f"(from deprecated {alias.dotted})"
                )
                continue
        else:
            element[alias.target] = value
            origin[alias.target] = f"deprecated {alias.dotted}"
        report.warnings.append(
            f"Deprecated option '{alias.dotted}' → use '{new_key}' (FutureCost element)"
        )
        del section[alias.key]

    report.element = find_future_cost(planning)
    return report


def effective_future_cost(planning: dict[str, Any]) -> dict[str, Any]:
    """The FutureCost configuration gtopt will use, without mutating input.

    Returns the resolved element (a copy) or ``{}`` when none applies.
    Conflicts are ignored here (the element value wins); use
    :func:`resolve_legacy_future_cost_keys` to detect them.
    """
    work = copy.deepcopy(planning)
    report = resolve_legacy_future_cost_keys(work)
    return dict(report.element) if report.element is not None else {}


__all__ = [
    "AliasReport",
    "FUTURE_COST_FIELDS",
    "LEGACY_ALIASES",
    "LEGACY_KEYS",
    "LegacyAlias",
    "effective_future_cost",
    "ensure_future_cost",
    "find_future_cost",
    "legacy_keys_present",
    "planning_method",
    "resolve_legacy_future_cost_keys",
    "set_future_cost_fields",
]
