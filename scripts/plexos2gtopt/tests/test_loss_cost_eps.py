"""Unit tests for the ``--loss-cost-eps`` CLI / writer plumbing.

Pins three invariants:
  1) DEFAULT (``loss_cost_eps = None``): the field is NOT emitted in
     ``options.model_options`` — gtopt's built-in 0.1 $/MWh applies.
  2) EXPLICIT (any value, including ``0`` to disable): the field IS
     emitted in ``options.model_options`` with the requested value.
  3) Per-line ``Line.loss_cost_eps`` is left to the C++ side — the
     writer emits the global default; per-line overrides are JSON-
     editable post hoc (matches the per-line ``loss_envelope`` /
     ``loss_pwl_layout`` plumbing convention).
"""

from __future__ import annotations

from plexos2gtopt.entities import BundleSpec
from plexos2gtopt.gtopt_writer import build_options


def test_loss_cost_eps_default_omits_field() -> None:
    """Default ``loss_cost_eps = None`` must NOT emit the field."""
    bundle = BundleSpec(bundle_date="2026-01-01")
    opts = build_options(bundle)
    mo = opts["model_options"]
    assert "loss_cost_eps" not in mo, (
        "unset loss_cost_eps must omit the field so gtopt's 0.1 default"
        f" applies; got mo={mo!r}"
    )


def test_loss_cost_eps_zero_explicit_emits_field() -> None:
    """Explicit ``loss_cost_eps = 0.0`` disables ε — must be emitted."""
    bundle = BundleSpec(bundle_date="2026-01-01")
    opts = build_options(bundle, loss_cost_eps=0.0)
    mo = opts["model_options"]
    assert mo.get("loss_cost_eps") == 0.0


def test_loss_cost_eps_positive_emits_field() -> None:
    """Positive ``loss_cost_eps`` lands in ``model_options.loss_cost_eps``."""
    bundle = BundleSpec(bundle_date="2026-01-01")
    opts = build_options(bundle, loss_cost_eps=1e-6)
    mo = opts["model_options"]
    assert mo.get("loss_cost_eps") == 1e-6


def test_loss_cost_eps_via_build_planning() -> None:
    """``build_planning`` plumbs ``loss_cost_eps`` into model_options."""
    # Build a minimal PlexosCase via the existing fixture helpers
    # — we only need the options block, so a hollow bundle suffices.
    from plexos2gtopt.entities import PlexosCase

    bundle = BundleSpec(bundle_date="2026-01-01")
    case = PlexosCase(bundle=bundle)
    from plexos2gtopt.gtopt_writer import build_planning

    planning = build_planning(
        case,
        name="test_planning",
        loss_cost_eps=2.5e-7,
    )
    assert planning["options"]["model_options"]["loss_cost_eps"] == 2.5e-7

    # Default plumbing (``loss_cost_eps=None``) must omit the field.
    planning_default = build_planning(case, name="test_planning")
    assert "loss_cost_eps" not in planning_default["options"]["model_options"]
