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


__all__ = [
    "POINT_STORAGE_INTEGRATION",
    "POINT_STORAGE_OPTIONS",
    "UC_PERIOD_CLASSES",
    "commitment_period_class",
    "point_storage_options",
]
