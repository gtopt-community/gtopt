# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--bess-reserve native``: CEN BESS SSCC rows -> Battery.reserve_sustain_hours.

CEN's PCP bundles carry, per reserve-providing BESS, ``UPStorageBound``
(``r_up - eta_d E <= 0``), ``DOWNStorageBound`` (``E + eta_c r_dn <=
Cap``) and the ``*_CF_GEN_COMP`` / ``*_CF_LOAD_COMP`` AUX-battery
equalities.  The native mapping recovers ``T`` from the bounds, makes
the four families inactive and restores gross provision factors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plexos2gtopt._bess_reserve import (
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
from plexos2gtopt.plexos_xml import (
    PlexosCollection,
    PlexosDataRow,
    PlexosDb,
    PlexosMembership,
    PlexosObject,
    PlexosProperty,
)

_BAT, _CON = 7, 70
_COLL = 90
_P_ENERGY, _P_RAISE, _P_LOWER = 1, 2, 3


def _db(up_energy: float = -0.95, dn_lower: float = 0.95) -> PlexosDb:
    db = PlexosDb(path=Path("synthetic.xml"))
    db.classes_by_name = {"Battery": _BAT, "Constraint": _CON}
    db.classes_by_id = {v: k for k, v in db.classes_by_name.items()}
    db.objects = [
        PlexosObject(object_id=1, class_id=_BAT, name="BAT_X"),
        PlexosObject(object_id=2, class_id=_CON, name="UPStorageBound_BAT_X"),
        PlexosObject(object_id=3, class_id=_CON, name="DOWNStorageBound_BAT_X"),
    ]
    db.collections = [
        PlexosCollection(
            collection_id=_COLL,
            parent_class_id=_BAT,
            child_class_id=_CON,
            name="Constraints",
        )
    ]
    db.memberships = [
        PlexosMembership(
            membership_id=10, collection_id=_COLL, parent_object_id=1, child_object_id=2
        ),
        PlexosMembership(
            membership_id=11, collection_id=_COLL, parent_object_id=1, child_object_id=3
        ),
    ]
    db.properties = [
        PlexosProperty(
            property_id=_P_ENERGY, collection_id=_COLL, name="Energy Coefficient"
        ),
        PlexosProperty(
            property_id=_P_RAISE,
            collection_id=_COLL,
            name="Regulation Raise Reserve Provision Coefficient",
        ),
        PlexosProperty(
            property_id=_P_LOWER,
            collection_id=_COLL,
            name="Regulation Lower Reserve Provision Coefficient",
        ),
    ]
    db.data_rows = [
        PlexosDataRow(
            data_id=1, membership_id=10, property_id=_P_ENERGY, value=up_energy
        ),
        PlexosDataRow(data_id=2, membership_id=10, property_id=_P_RAISE, value=1.0),
        PlexosDataRow(data_id=3, membership_id=11, property_id=_P_ENERGY, value=1.0),
        PlexosDataRow(
            data_id=4, membership_id=11, property_id=_P_LOWER, value=dn_lower
        ),
    ]
    return db


def test_sustain_hours_recovers_one_hour() -> None:
    """eta = 0.95 and coefficients 0.95 -> T = 1 h (CEN's convention)."""
    t_h = sustain_hours_from_bounds(_db(), "BAT_X", eta_c=0.95, eta_d=0.95)
    assert t_h == pytest.approx(1.0)
    assert sustain_hours_from_bounds(_db(), "BAT_Y", 0.95, 0.95) is None


def test_sustain_hours_two_hours() -> None:
    """r_up <= 0.475 E with eta_d = 0.95 -> T = 2 h."""
    t_h = sustain_hours_from_bounds(
        _db(up_energy=-0.475, dn_lower=1.9), "BAT_X", eta_c=0.95, eta_d=0.95
    )
    assert t_h == pytest.approx(2.0)


def test_apply_native_rewrites_case() -> None:
    uc = UserConstraintSpec
    case = PlexosCase(
        bundle=BundleSpec(),
        batteries=(
            BatterySpec(
                object_id=1,
                name="BAT_X",
                bus_name="n1",
                input_efficiency=0.95,
                output_efficiency=0.95,
            ),
            BatterySpec(object_id=9, name="BAT_Z", bus_name="n1"),
        ),
        user_constraints=(
            uc(name="UPStorageBound_BAT_X", expression="x <= 0"),
            uc(name="DOWNStorageBound_BAT_X", expression="x <= 580"),
            uc(name="BAT_X_CF_GEN_COMP", expression="x = 0"),
            uc(name="BAT_X_CF_LOAD_COMP", expression="x = 0"),
            uc(name="CSF_RS_MIN_BAT_X", expression="x >= 0"),
            uc(name="BAT_Z_CF_GEN_COMP", expression="z = 0"),
        ),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="BAT_X_gen",
                reserve_zones=("CSF_RS_BESS",),
                urmax=100.0,
                ur_provision_factor_profile=(0.35, 0.2),
                name="provision_BAT_X_gen__CSF_RS_BESS",
            ),
        ),
    )
    out = apply_native_bess_reserve(case, bundle=None, db=_db())  # type: ignore[arg-type]
    bats = {b.name: b for b in out.batteries}
    assert bats["BAT_X"].reserve_sustain_hours == pytest.approx(1.0)
    assert bats["BAT_Z"].reserve_sustain_hours is None
    active = {u.name: u.active for u in out.user_constraints}
    assert active["UPStorageBound_BAT_X"] is False
    assert active["DOWNStorageBound_BAT_X"] is False
    assert active["BAT_X_CF_GEN_COMP"] is False
    assert active["BAT_X_CF_LOAD_COMP"] is False
    assert active["CSF_RS_MIN_BAT_X"] is None  # untouched
    assert active["BAT_Z_CF_GEN_COMP"] is None  # BAT_Z has no bounds
    (rp,) = out.reserve_provisions
    assert rp.ur_provision_factor_profile == (1.0, 1.0)
