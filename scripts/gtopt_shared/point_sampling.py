# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Converter defaults for point-sampled (Chebyshev) stages.

``Stage.sampling = "point"`` stages hold point samples at Chebyshev nodes
(``Block.time``) with quadrature weights as block durations.  Every
converter that emits them (``plexos2gtopt`` typical weeks / days,
``tcheby2gtopt``) writes :data:`POINT_STORAGE_OPTIONS` into
``model_options``: batteries AND reservoirs integrate their balance with
``cc``, the Clenshaw-Curtis rule (``e_k = e_{k-1} + w_k f_k`` with the
node's CC weight: one node per row, positive weights, the closed-period
total is the quadrature that also integrates energy and cost).  On the
FY28 AEMO ISP year it was 3.8-4.5x faster than ``lg4_pos`` (no envelope
rows, 22 % fewer nonzeros) and closer to the 1 h reference.  ``lg4_pos``
/ ``trapezoid`` stay selectable (``--point-storage-integration``).
Block stages ignore the option (they always use the block recursion).
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path

#: storage-integration rule for batteries and reservoirs on point stages
POINT_STORAGE_INTEGRATION = "cc"

#: ``model_options`` the converters emit when any stage is point-sampled
POINT_STORAGE_OPTIONS: dict[str, str] = {
    "battery_storage_integration": POINT_STORAGE_INTEGRATION,
    "reservoir_storage_integration": POINT_STORAGE_INTEGRATION,
}


def point_storage_options(rule: str = POINT_STORAGE_INTEGRATION) -> dict[str, str]:
    """``model_options`` entries for point stages: ``rule`` for batteries
    and reservoirs; ``"none"`` emits nothing (the previous behaviour:
    gtopt's per-type defaults, lg4_pos reservoirs / block batteries)."""
    if rule == "none":
        return {}
    return {
        "battery_storage_integration": rule,
        "reservoir_storage_integration": rule,
    }


#: per-unit commitment grid classes on point (Chebyshev) stages:
#: (class, min(min_up_time, min_down_time) threshold [h], grid [h]).
#: The binaries of a unit live on its own uniform grid of that many
#: hours aligned to the stage (window) start, never on the nodes.
UC_PERIOD_CLASSES: tuple[tuple[str, float, float], ...] = (
    ("slow", 8.0, 4.0),
    ("medium", 4.0, 3.0),
    ("fast", 0.0, 1.0),
)


def commitment_period_class(
    min_up_time: float | None,
    min_down_time: float | None,
    time_resolution: float | None = None,
    override: float | None = None,
) -> tuple[str, float]:
    """(class, ``commitment_period`` [h]) of a unit on a point stage.

    Precedence: ``override`` (user table) > the source model's own UC
    resolution ``time_resolution`` > the class rule on
    m = min(MUT, MDT) over the set ones: m ≥ 8 h → slow, 4 h grid;
    m ≥ 4 h → medium, 3 h; otherwise (or none set) fast, 1 h.  Never
    below 1 h (the C++ floor on point stages)."""
    if override is not None:
        return "override", max(float(override), 1.0)
    if time_resolution is not None and time_resolution > 0.0:
        return "source", max(float(time_resolution), 1.0)
    times = [t for t in (min_up_time, min_down_time) if t is not None and t > 0.0]
    m = min(times) if times else 0.0
    return next(
        (name, hours) for name, threshold, hours in UC_PERIOD_CLASSES if m >= threshold
    )


#: the study's daily commitment grid (``--commitment-breaks study``):
#: 23:30-03:30, 03:30-07:30, 07:30-10:30 sunrise, 10:30-14:30 midday,
#: 14:30-17:30 sunset, 17:30-23:30 peak (hourly samples 18..23)
STUDY_COMMITMENT_BREAKS: tuple[float, ...] = (3.5, 7.5, 10.5, 14.5, 17.5, 23.5)

_MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)


def check_commitment_breaks(breaks: Iterable[float]) -> list[float]:
    """Validated daily cell boundaries (hours of the day): at least one,
    each in [0, 24), strictly increasing -- the same rule as gtopt's
    ``validate_planning``."""
    out = [float(b) for b in breaks]
    if not out:
        raise ValueError("commitment breaks: need at least one boundary")
    for i, b in enumerate(out):
        if not 0.0 <= b < 24.0:
            raise ValueError(f"commitment breaks: {b:g} is not an hour in [0, 24)")
        if i and b <= out[i - 1]:
            raise ValueError(
                f"commitment breaks: must be strictly increasing ({b:g} after "
                f"{out[i - 1]:g})"
            )
    return out


def parse_commitment_breaks(spec: str) -> list[float]:
    """``"3.5,7.5,10.5"`` (commas, semicolons or blanks) or ``"study"``
    (:data:`STUDY_COMMITMENT_BREAKS`) -> validated breaks."""
    text = spec.strip()
    if text.lower() == "study":
        return list(STUDY_COMMITMENT_BREAKS)
    parts = [p for p in text.replace(";", ",").replace(" ", ",").split(",") if p]
    try:
        values = [float(p) for p in parts]
    except ValueError as err:
        raise ValueError(f"commitment breaks: cannot parse {spec!r}") from err
    return check_commitment_breaks(values)


def _month_number(token: str) -> int:
    t = token.strip().lower()
    if t.isdigit() and 1 <= int(t) <= 12:
        return int(t)
    if t[:3] in _MONTHS:
        return _MONTHS.index(t[:3]) + 1
    raise ValueError(f"commitment breaks file: bad month {token!r}")


def read_commitment_breaks_file(path: Path | str) -> dict[int, list[float]]:
    """Per-month daily commitment grids from a CSV: one row per month,
    ``month,breaks`` with the month as 1..12 or a name (``jan``...) and
    the breaks either in one field (``"3.5 7.5 10.5"`` / ``3.5;7.5``) or
    in the following columns.  A header row (first field ``month``),
    blank lines and ``#`` comments are skipped."""
    table: dict[int, list[float]] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            cells = [c.strip() for c in row if c.strip()]
            if not cells or cells[0].startswith("#"):
                continue
            if cells[0].lower() == "month":
                continue
            month = _month_number(cells[0])
            if month in table:
                raise ValueError(f"commitment breaks file: month {month} twice")
            if len(cells) < 2:
                raise ValueError(f"commitment breaks file: month {month} has no breaks")
            table[month] = parse_commitment_breaks(",".join(cells[1:]))
    if not table:
        raise ValueError(f"commitment breaks file {path}: no rows")
    return table


def commitment_breaks_options(breaks: list[float] | None) -> dict[str, list[float]]:
    """``model_options`` entry of the planning-wide daily grid (empty
    when unset)."""
    return {"commitment_breaks": list(breaks)} if breaks else {}


def stage_commitment_fields(
    start_clock: float,
    month: int | None = None,
    table: Mapping[int, list[float]] | None = None,
    default: list[float] | None = None,
) -> dict[str, object]:
    """Stage members of the daily commitment grid: ``start_hour`` (the
    clock hour of the stage start, mod 24) and -- when ``table`` has the
    stage's ``month`` -- the stage's own ``commitment_breaks``.  A month
    missing from the table falls back to ``default`` (written once as
    ``model_options.commitment_breaks``); without a default it is an
    error (the stage would silently lose its daily grid)."""
    out: dict[str, object] = {"start_hour": round(float(start_clock) % 24.0, 9)}
    if table:
        if month is not None and month in table:
            out["commitment_breaks"] = list(table[month])
        elif default is None:
            raise ValueError(
                f"commitment breaks file: no row for month {month} and no "
                "--commitment-breaks default"
            )
    return out


__all__ = [
    "POINT_STORAGE_INTEGRATION",
    "POINT_STORAGE_OPTIONS",
    "STUDY_COMMITMENT_BREAKS",
    "UC_PERIOD_CLASSES",
    "check_commitment_breaks",
    "commitment_breaks_options",
    "commitment_period_class",
    "parse_commitment_breaks",
    "point_storage_options",
    "read_commitment_breaks_file",
    "stage_commitment_fields",
]
