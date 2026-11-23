# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`plexos2gtopt._bess_reserve`."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from plexos2gtopt import _bess_reserve
from plexos2gtopt._bess_reserve import (
    _battery_coefficients,
    apply_native_bess_reserve,
    sustain_hours_from_bounds,
)
from plexos2gtopt.entities import (
    BatterySpec,
    BundleSpec,
    PlexosCase,
    ReserveProvisionSpec,
    UserConstraintSpec,
)
from plexos2gtopt.plexos_xml import PlexosDb
from plexos2gtopt.tests.test_bess_reserve_native import _db


def _case(**kw: object) -> PlexosCase:
    base: dict[str, object] = {
        "bundle": BundleSpec(),
        "batteries": (
            BatterySpec(
                object_id=1,
                name="BAT_X",
                bus_name="n1",
                input_efficiency=0.95,
                output_efficiency=0.95,
            ),
        ),
    }
    base.update(kw)
    return PlexosCase(**base)  # type: ignore[arg-type]


def test_coefficients_without_collection_is_empty() -> None:
    db = _db()
    db.collections = []
    assert not _battery_coefficients(db, "UPStorageBound_BAT_X")


def test_coefficients_missing_constraint_is_empty() -> None:
    assert not _battery_coefficients(_db(), "UPStorageBound_NOPE")


def test_rows_by_membership_cache_is_reused() -> None:
    db = _db()
    first = _bess_reserve._rows_by_membership(db)
    db.data_rows = []
    assert _bess_reserve._rows_by_membership(db) is first


def test_mismatched_up_down_warns_and_uses_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        t_h = sustain_hours_from_bounds(
            _db(up_energy=-0.95, dn_lower=1.9), "BAT_X", eta_c=0.95, eta_d=0.95
        )
    assert t_h == pytest.approx(1.0)
    assert "sustain differ" in caplog.text


def test_zero_efficiency_falls_back_to_unity() -> None:
    # eta_d = 0 -> treat as 1: T = 1 * 1 / 0.95.
    t_h = sustain_hours_from_bounds(_db(), "BAT_X", eta_c=0.0, eta_d=0.0)
    assert t_h == pytest.approx(1.0 / 0.95)


def test_down_only_bound() -> None:
    db = _db(up_energy=0.0, dn_lower=1.9)
    assert sustain_hours_from_bounds(db, "BAT_X", 0.95, 0.95) == pytest.approx(2.0)


def test_apply_no_batteries_returns_same_case() -> None:
    case = _case(batteries=())
    assert apply_native_bess_reserve(case, bundle=None, db=_db()) is case  # type: ignore[arg-type]


def test_apply_no_native_returns_same_case() -> None:
    case = _case(batteries=(BatterySpec(object_id=9, name="BAT_Z", bus_name="n1"),))
    assert apply_native_bess_reserve(case, bundle=None, db=_db()) is case  # type: ignore[arg-type]


def test_apply_loads_xml_when_db_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    def _fake_load(path: Path) -> PlexosDb:
        seen.append(path)
        return _db()

    monkeypatch.setattr(_bess_reserve, "load_xml", _fake_load)
    bundle = SimpleNamespace(xml_path=Path("x.xml"))
    case = _case(
        user_constraints=(
            UserConstraintSpec(
                name="UPStorageBound_BAT_X", expression="x <= 0", active=False
            ),
        ),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="BAT_X_gen",
                reserve_zones=("Z",),
                dr_provision_factor_profile=(0.5,),
                name="other_BAT_X",
            ),
            ReserveProvisionSpec(
                generator_name="BAT_X_gen",
                reserve_zones=("Z",),
                dr_provision_factor_profile=(0.5, 0.25),
                name="provision_BAT_X",
            ),
        ),
    )
    out = apply_native_bess_reserve(case, bundle=bundle)  # type: ignore[arg-type]
    assert seen == [Path("x.xml")]
    # Already-inactive constraint is left as is (no double tagging).
    (uc,) = out.user_constraints
    assert uc.active is False and "bess-reserve" not in uc.description
    other, native = out.reserve_provisions
    assert other.dr_provision_factor_profile == (0.5,)
    assert native.dr_provision_factor_profile == (1.0, 1.0)
