# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Native reserve builder: PLEXOS sentinels, scaling and validation.

PLEXOS "no value" sentinels (``±1e30``) in dated rows, RHS profiles and
decision-variable bounds; contingency DVs in GW (``k_C = 1000``);
definitions written ``>=`` or with non-unit partners; equality detection
without DB rows; and the explicit errors that replaced ``assert``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from plexos2gtopt._native_reserve import (
    _FLOOR_TAG,
    Row,
    _compress,
    _finite,
    dated_row,
    hourly_values,
    parse_expression,
    resolve_dated,
)
from plexos2gtopt.entities import (
    DecisionVariableSpec,
    InertiaCreditSpec,
    UserConstraintSpec,
)
from plexos2gtopt.tests.test_native_reserve import _dv, _uc
from plexos2gtopt.tests.test_native_reserve_extras import (
    _DAY1,
    _builder,
    _db,
    _inertia_dvs,
    _inertia_ucs,
    _row,
    _rp,
)

_BIG = 1.0e30


# ── sentinels in dated values (P0-1) ───────────────────────────────────────
def test_resolve_dated_skips_sentinel_rows() -> None:
    rows = [
        _row(data_id=1, value=5.0),
        _row(data_id=2, value=_BIG, date_from=datetime(2026, 1, 1)),
    ]
    # The dated 1e30 row is "no value": the undated row applies.
    assert resolve_dated(rows, _DAY1) == 5.0
    assert dated_row(rows, _DAY1) is rows[0]
    assert resolve_dated([_row(data_id=3, value=-_BIG)], _DAY1) is None


def test_hourly_values_ignore_sentinel_tags() -> None:
    rows = [_row(data_id=1, value=2.0), _row(data_id=2, value=-_BIG, timeslice="H2")]
    assert hourly_values(rows, _DAY1, 3, 0.0) == (2.0, 2.0, 2.0)


def test_rhs_profile_sentinel_replaced_with_warning() -> None:
    spec = UserConstraintSpec(
        name="Z",
        expression=f"1 * {_rp('p1')} >= 10",
        rhs_profile=(5.0,) * 23 + (_BIG,),
    )
    b = _builder([spec])
    values = b._rhs(b.rows["Z"])
    assert values == (5.0,) * 23 + (10.0,)
    assert any("'no limit' RHS sentinel" in w for w in b.warnings)


def test_rhs_dated_value_differs_from_extracted() -> None:
    b = _builder([_uc("Z", f"1 * {_rp('p1')} >= 10")], _db({"Z": {"RHS": 12.0}}))
    assert b._rhs(b.rows["Z"])[0] == 12.0
    assert any("RHS 12 at the horizon start" in w for w in b.warnings)


def test_finite_and_compress_helpers() -> None:
    assert _finite(None) is None
    assert _finite(_BIG) is None
    assert _finite(-_BIG) is None
    assert _finite(3.0) == 3.0
    assert _compress(()) is None


# ── contingency zone fixtures ──────────────────────────────────────────────
def _contingency_ucs(k_c: float) -> list[UserConstraintSpec]:
    return [
        _uc("Prov", f"1 * {_rp('provision_G1')} - 1 * {_dv('R')} >= 0"),
        _uc("Calc", f"1 * {_dv('R')} - 1.25 * {_dv('C')} >= 10"),
        _uc("Cmp", f'{k_c:g} * {_dv("C")} - 1 * generator("G1").generation >= 0'),
    ]


def test_contingency_dv_in_gw_is_scaled_once() -> None:
    """``1000·C >= p`` (C in GW): ``R >= 10 + 1.25·p/1000`` exactly."""
    dvs = (DecisionVariableSpec(name="R"), DecisionVariableSpec(name="C"))
    b = _builder(_contingency_ucs(1000.0), dvs=dvs)
    b.build()
    assert len(b.new_zones) == 1
    zone = b.new_zones[0]
    (plane,) = zone.urreq_planes
    (cand,) = zone.urcontingencies
    assert cand.generators == ("G1",)
    # Candidate in MW (k_C·C = p); the plane carries c/k_C.
    assert cand.generator_coefs == (pytest.approx(1.0),)
    assert plane.contingency_coef == pytest.approx(1.25 / 1000.0)
    assert plane.alpha == pytest.approx(10.0)


def test_contingency_free_lower_bound_has_no_floor_plane() -> None:
    """A ``-1e30`` lower bound is "no bound": no floor plane at 1e30."""
    dvs = (
        DecisionVariableSpec(name="R"),
        DecisionVariableSpec(name="C", lower_bound=-_BIG, upper_bound=_BIG),
    )
    b = _builder(_contingency_ucs(1.0), dvs=dvs)
    b.build()
    assert len(b.new_zones) == 1
    zone = b.new_zones[0]
    assert [p.source for p in zone.urreq_planes] == ["Calc"]
    assert not any(p.source.endswith(_FLOOR_TAG) for p in zone.urreq_planes)
    # A finite bound does give the floor plane.
    dvs = (
        DecisionVariableSpec(name="R"),
        DecisionVariableSpec(name="C", lower_bound=300.0),
    )
    b = _builder(_contingency_ucs(1.0), dvs=dvs)
    b.build()
    floor = b.new_zones[0].urreq_planes[1]
    assert floor.source == f"Calc{_FLOOR_TAG}"
    assert floor.alpha == pytest.approx(10.0 + 1.25 * 300.0)


# ── sentinel DV bounds in inertia rows (P0-2) ──────────────────────────────
def test_inertia_definition_with_no_limit_upper_bound() -> None:
    """``I <= 1e30`` is unbounded: the row still defines inertia."""
    dvs = tuple(
        replace(d, upper_bound=_BIG) if d.name == "I_NR" else d for d in _inertia_dvs()
    )
    b = _builder(_inertia_ucs(), dvs=dvs)
    assert b._inertia_definitions() == [{"uc_G1": 5.0, "uc_G2": 3.0, "uc_NOPE": 2.0}]
    # A finite upper bound marks a selector, not an inertia aggregate.
    dvs = tuple(
        replace(d, upper_bound=9.0) if d.name == "I_NR" else d for d in _inertia_dvs()
    )
    assert not _builder(_inertia_ucs(), dvs=dvs)._inertia_definitions()


def test_credit_with_no_limit_upper_bound_is_not_a_selector() -> None:
    """``y <= 1e30`` is not a ``[0, U]`` selector: no 1e30-sized credit."""
    dvs = tuple(
        replace(d, upper_bound=_BIG) if d.name == "Y" else d for d in _inertia_dvs()
    )
    b = _builder(_inertia_ucs(), dvs=dvs)
    b.inertia_minima()
    assert "NR_ok" not in {z.name for z in b.inertia_zones}
    assert any("Y has no 'Σ u − k·y >= 0' commit row" in w for w in b.warnings)


# ── definitions (P1-6) ─────────────────────────────────────────────────────
def test_generation_definition_warnings() -> None:
    g = _dv("Generation_SEN")
    ucs = [
        _uc(
            "Generation_Calculation",
            f'0.95 * generator("G1").generation + 1 * generator("G2").generation'
            f" - 1000 * {g} >= 0",
        )
    ]
    b = _builder(ucs, _db({"Generation_Calculation": {"Sense": 1.0, "RHS": 0.0}}))
    found = b.definition("Generation_SEN", ("generator",), unit_partners=True)
    assert found is not None
    assert found.coef == -1000.0
    text = "\n".join(b.warnings)
    assert "defines Generation_SEN with '>=' (not a PLEXOS equality)" in text
    assert "1 term(s) of the Generation_SEN definition with |coef| != 1" in text
    # A PLEXOS equality (Sense 0) written '>=' with unit partners: silent.
    ucs = [
        _uc(
            "Generation_Calculation",
            f'1 * generator("G1").generation - 1000 * {g} >= 0',
        )
    ]
    b = _builder(ucs, _db({"Generation_Calculation": {"RHS": 0.0}}))
    assert b.definition("Generation_SEN", ("generator",), unit_partners=True)
    assert not b.warnings


# ── equality without DB rows (P1-13) ───────────────────────────────────────
def test_is_equality_falls_back_to_extracted_operator() -> None:
    ucs = [
        _uc("EqRow", f"1 * {_rp('p1')} = 5"),
        _uc("GeRow", f"1 * {_rp('p1')} >= 5"),
    ]
    b = _builder(ucs, _db({"Other": {"RHS": 0.0}}))
    assert b._is_equality(b.rows["EqRow"])
    assert not b._is_equality(b.rows["GeRow"])
    assert any("EqRow: no Sense / RHS rows" in w for w in b.warnings)
    # Without any DB the fallback is silent.
    b = _builder(ucs)
    assert b._is_equality(b.rows["EqRow"])
    assert not b.warnings


# ── explicit errors instead of assert (P1-10) ──────────────────────────────
def test_definitions_without_tail_raise() -> None:
    x = _dv("X")
    expr = f"1 * {_rp('p1')} - 1 * {x} >= 0"
    b = _builder([_uc("D", expr)], _db({"D": {"RHS": 0.0}}))
    parsed = parse_expression(expr)
    assert parsed is not None
    broken = replace(b.rows["D"].spec, expression="no tail here")
    b.rows["D"] = Row(broken, *parsed)
    with pytest.raises(ValueError, match="D: expression has no"):
        b.definitions()


def test_inertia_credit_requires_a_credit() -> None:
    """``InertiaCreditSpec.credit`` is never null (gtopt requires it)."""
    with pytest.raises(ValueError, match="'y': credit is required"):
        InertiaCreditSpec(
            name="y",
            generators=("G",),
            units=1.0,
            credit=None,  # type: ignore[arg-type]
        )
