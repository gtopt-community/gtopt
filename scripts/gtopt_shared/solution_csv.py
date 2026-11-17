# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read gtopt ``solution.csv`` and report its objective in physical units.

Single source of truth for every Python consumer of ``solution.csv``
(``gtopt_results_summary``, ``gtopt_timeseries_export``, ``run_gtopt``,
the GUI service).

Objective convention
--------------------
The C++ writer (``include/gtopt/solution_csv.hpp``) emits one row per
``(scene, phase)`` cell.  ``obj_value`` is the cell objective in
**physical cost units** (LP-raw objective × ``scale_objective``) for every
planning method, and the trailing ``scale_objective`` column records the
factor.  Consumers must therefore **never** multiply ``obj_value`` again.

Older outputs are classified by their header (see :func:`resolve_objective`):

==========================  =================  ===========================
header                      gtopt writer       ``obj_value`` is
==========================  =================  ===========================
has ``scale_objective``     2026-11 →          physical; factor in column
has ``solve_ticks``         2026-07 → 2026-11  physical (factor unknown)
neither (incl. key,value)   → 2026-07          assumed LP-raw (see below)
==========================  =================  ===========================

``get_obj_value()`` switched to physical units on 2026-04-27 (commit
``2720feb6``), so legacy files written between 2026-04-27 and 2026-07-12
are already physical but carry no marker that distinguishes them from the
older LP-raw files; the legacy branch assumes LP-raw (the convention of
every pre-April golden and tool) — pass ``scale_objective=1`` to read such
a file as physical.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

OBJ_VALUE = "obj_value"
SCALE_OBJECTIVE = "scale_objective"
#: First column of the effort telemetry the writer appended in 2026-07,
#: after the switch to physical ``obj_value``.
_PHYSICAL_ERA_MARKER = "solve_ticks"

#: C++ defaults (``PlanningOptionsLP``): monolithic LPs are scaled by 1000,
#: SDDP / cascade cells by 1.
DEFAULT_SCALE_OBJECTIVE_MONOLITHIC = 1000.0
DEFAULT_SCALE_OBJECTIVE_DECOMPOSED = 1.0

#: How the objective was interpreted (``SolutionObjective.convention``).
CONVENTION_COLUMN = "column"
CONVENTION_PHYSICAL = "physical"
CONVENTION_LEGACY_RAW = "legacy-raw"


class ScaleObjectiveMismatchError(ValueError):
    """An explicit ``scale_objective`` disagrees with the output's column."""


@dataclass(frozen=True)
class SolutionObjective:
    """The objective of one ``solution.csv`` row.

    Attributes:
        value: Objective in physical cost units.
        raw: LP-raw objective (``value / scale_objective``), or ``None``
            when the factor is unknown.
        scale_objective: The objective scale factor, or ``None`` if unknown.
        convention: One of ``CONVENTION_*`` — how ``obj_value`` was read.
    """

    value: float
    raw: float | None
    scale_objective: float | None
    convention: str


def parse_solution_rows(rows: list[list[str]]) -> dict[str, str]:
    """Flatten parsed ``solution.csv`` records into ``{column: value}``.

    Supports the current columnar layout (header + one row per cell; the
    first data row — scene/phase order — is returned) and the legacy
    two-column ``key,value`` layout.
    """
    rows = [r for r in rows if r and any(f.strip() for f in r)]
    if not rows:
        return {}
    header = [f.strip() for f in rows[0]]
    if OBJ_VALUE in header and len(header) > 2:
        if len(rows) < 2:
            return {}
        return {
            name: value.strip()
            for name, value in zip(header, rows[1], strict=False)
            if name
        }
    return {r[0].strip(): r[1].strip() for r in rows if len(r) >= 2}


def parse_solution_csv(text: str) -> dict[str, str]:
    """Parse ``solution.csv`` text; see :func:`parse_solution_rows`."""
    return parse_solution_rows(list(csv.reader(io.StringIO(text))))


def default_scale_objective(options: Mapping[str, Any] | None) -> float | None:
    """``scale_objective`` a case was solved with, from its planning options.

    Reads ``model_options.scale_objective`` (or the legacy flat
    ``scale_objective``); otherwise returns the C++ per-method default.
    Returns ``None`` when *options* is ``None``.
    """
    if options is None:
        return None
    model_options = options.get("model_options") or {}
    for src in (model_options, options):
        value = src.get(SCALE_OBJECTIVE)
        if value is not None:
            return float(value)
    method = str(options.get("method", "monolithic")).lower()
    if method in ("sddp", "cascade"):
        return DEFAULT_SCALE_OBJECTIVE_DECOMPOSED
    return DEFAULT_SCALE_OBJECTIVE_MONOLITHIC


def _to_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def resolve_objective(
    solution: Mapping[str, Any],
    *,
    scale_objective: float | None = None,
    fallback_scale: float | None = None,
) -> SolutionObjective | None:
    """Return the physical objective of a flattened ``solution.csv`` row.

    Args:
        solution: ``{column: value}`` as from :func:`parse_solution_csv`.
        scale_objective: Explicit factor (e.g. a CLI ``--scale-objective``).
            For outputs with a ``scale_objective`` column it only
            *validates*: a disagreement raises
            :class:`ScaleObjectiveMismatchError` rather than silently
            reporting a cost for a differently-scaled run.  For legacy
            outputs it is the factor used to convert LP-raw to physical.
        fallback_scale: Factor to use when neither the column nor
            *scale_objective* is available (e.g. from
            :func:`default_scale_objective` on the case JSON).

    Returns ``None`` when ``obj_value`` is missing or not a finite number.
    """
    obj = _to_float(solution.get(OBJ_VALUE))
    if obj is None:
        return None

    column = _to_float(solution.get(SCALE_OBJECTIVE))
    if column is not None:
        if scale_objective is not None and not math.isclose(
            float(scale_objective), column, rel_tol=1e-9
        ):
            raise ScaleObjectiveMismatchError(
                f"scale_objective={scale_objective:g} was given, but the "
                f"solution.csv was written with scale_objective={column:g}. "
                "Its obj_value is already in physical units; drop the "
                "override (it is only needed for legacy outputs)."
            )
        raw = obj / column if column else None
        return SolutionObjective(obj, raw, column, CONVENTION_COLUMN)

    scale = scale_objective if scale_objective is not None else fallback_scale
    if _PHYSICAL_ERA_MARKER in solution:
        raw = obj / scale if scale else None
        return SolutionObjective(obj, raw, scale, CONVENTION_PHYSICAL)

    # Legacy layout: obj_value assumed LP-raw (module docstring).
    if scale is None:
        scale = DEFAULT_SCALE_OBJECTIVE_MONOLITHIC
    return SolutionObjective(obj * scale, obj, scale, CONVENTION_LEGACY_RAW)
