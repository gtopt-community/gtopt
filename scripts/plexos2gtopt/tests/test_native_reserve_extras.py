# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--reserve-model native``: equality balances, definitions, inertia minima.

Unit coverage of the CEN PCP mappings added on top of the CPF planes:
PLEXOS Sense 0 (``requirement_sense: "eq"`` + surplus cost), the
``CSF_*_Def`` definition rows, timesliced RHS values, the
``InertiaNRMin_*`` minima with their ``InertiaCommitGuacolda*`` credits
and the opt-in ``SpecialCPFNorth`` zones.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from plexos2gtopt._native_reserve import (
    DatedIndex,
    NativeReserveOptions,
    _Builder,
    _same_units,
    apply_native_reserve,
    hourly_values,
    parse_expression,
    slice_matcher,
)
from plexos2gtopt.entities import (
    BatterySpec,
    BundleSpec,
    CommitmentSpec,
    DecisionVariableSpec,
    GeneratorSpec,
    InertiaCreditSpec,
    InertiaZoneSpec,
    PlexosCase,
    ReservePlaneSpec,
    ReserveProvisionSpec,
    ReserveSpec,
    UserConstraintSpec,
)
from plexos2gtopt.gtopt_writer import (
    build_battery_array,
    build_inertia_zone_array,
    build_reserve_provision_array,
    build_reserve_zone_array,
)
from plexos2gtopt.plexos_xml import PlexosDataRow, PlexosDb
from plexos2gtopt.tests.cov_parsers_kit import DbKit
from plexos2gtopt.tests.test_native_reserve import _case, _dv, _uc, _zone

_DAY1 = datetime(2026, 11, 7)
_OPTS = NativeReserveOptions()


def _u(gen: str) -> str:
    return f'commitment("uc_{gen}").status'


def _rp(name: str, side: str = "up") -> str:
    return f'reserve_provision("{name}").{side}'


def _db(constraints: dict[str, dict[str, Any]]) -> PlexosDb:
    """DB with ``{constraint: {"Sense": v | None, "RHS": v}}`` rows."""
    k = DbKit()
    k.db.horizon_start = _DAY1
    for name, props in constraints.items():
        cid = k.obj("Constraint", name)
        for prop, value in props.items():
            if value is not None:
                k.sysprop("Constraint", cid, prop, value)
    return k.db


def _builder(
    ucs: list[UserConstraintSpec],
    db: PlexosDb | None = None,
    dvs: tuple[DecisionVariableSpec, ...] = (),
    **kw: Any,
) -> _Builder:
    case = PlexosCase(
        bundle=BundleSpec(bundle_date="2026-11-07"),
        generators=(
            GeneratorSpec(object_id=1, name="G1", bus_name="b", pmin=10.0),
            GeneratorSpec(object_id=2, name="G2", bus_name="b"),
        ),
        commitments=(
            CommitmentSpec(generator_name="G1", pmin_profile=(20.0,)),
            CommitmentSpec(generator_name="G2"),
        ),
        decision_variables=dvs,
        user_constraints=tuple(ucs),
    )
    return _Builder(replace(case, **kw), _OPTS, DatedIndex(db))


def _row(**kw: Any) -> PlexosDataRow:
    return PlexosDataRow(membership_id=1, property_id=1, **kw)


# ── timeslices ─────────────────────────────────────────────────────────────
def test_slice_matcher_or_parts() -> None:
    match = slice_matcher("M4-9,H10-17;M1-3,H9-19;M10-12,H9-19")
    assert match is not None
    assert match(_DAY1, 9)  # November, hour 9
    assert not match(_DAY1, 8)
    assert match(datetime(2026, 6, 1), 10)
    assert not match(datetime(2026, 6, 1), 9)
    assert slice_matcher("Peak") is None
    assert slice_matcher(" ; ") is None


def test_hourly_values_timeslice_overrides() -> None:
    rows = [
        _row(data_id=1, value=1.0),
        _row(data_id=2, value=5.0, timeslice="H2-3"),
        _row(data_id=3, value=7.0, timeslice="H3"),  # later id wins on H3
        _row(data_id=4, value=9.0, timeslice="Peak"),  # unknown: ignored
        _row(
            data_id=5,
            value=8.0,
            timeslice="H4",
            date_from=datetime(2026, 12, 1),
        ),  # outside its date window
    ]
    assert hourly_values(rows, _DAY1, 5, 0.0) == (1.0, 5.0, 7.0, 1.0, 1.0)


def test_dated_index_sense() -> None:
    idx = DatedIndex(_db({"EQ": {"RHS": 0.0}, "GE": {"Sense": 1.0, "RHS": 0.0}}))
    assert idx.sense("EQ") == 0.0  # unset Sense = equality
    assert idx.sense("GE") == 1.0
    assert idx.sense("NOPE") is None
    assert [r.value for r in idx.prop_rows("GE", "Sense")] == [1.0]


# ── equality balances (Sense 0) ────────────────────────────────────────────
def test_sense_zero_plane_balance_has_no_surplus_cost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A plane zone's R absorbs any excess: gtopt builds no surplus column,
    so the PLEXOS ``*_Slack`` cost is not emitted with ``"eq"``."""
    db = _db(
        {
            "CPF_Up10MinProvision": {"Sense": 0.0},
            "CPF_DownMinProvision": {"Sense": None, "RHS": 0.0},
        }
    )
    case = apply_native_reserve(_case(), _OPTS, db)
    up10 = _zone(case, "CPF10s")
    assert up10.requirement_sense == "eq"
    assert up10.ursurplus_cost is None
    down = _zone(case, "CPF5mDown")
    assert down.requirement_sense == "eq"
    assert down.drsurplus_cost is None
    assert "CPF_Up10MinProvision: surplus (−Slack) cost dropped (a plane" in (
        caplog.text
    )


def test_sense_ge_drops_surplus_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    db = _db({"CPF_Up10MinProvision": {"Sense": 1.0}})
    case = apply_native_reserve(_case(), _OPTS, db)
    assert _zone(case, "CPF10s").requirement_sense == ""
    assert "surplus (−Slack) cost dropped (a plane" in caplog.text


def test_unpriced_negative_term_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    ucs = [
        replace(
            uc,
            expression=uc.expression.replace(" >= 0", f" - 1 * {_dv('Free')} >= 0"),
        )
        if uc.name == "CPF_Up10MinProvision"
        else uc
        for uc in _case().user_constraints
    ]
    apply_native_reserve(_case(ucs), _OPTS)
    assert "dropped -1·Free" in caplog.text


# ── CSF_*_Def definitions ──────────────────────────────────────────────────
def test_definitions_rewritten_only_when_db_says_so() -> None:
    x = _dv("X")
    ucs = [
        _uc("D_ok", f"1 * {_rp('p1')} + 1 * {_rp('p2')} - 1 * {x} >= 0").__class__(
            name="D_ok",
            expression=f"1 * {_rp('p1')} + 1 * {_rp('p2')} - 1 * {x} >= 0",
            rhs_profile=(5.0,) * 24,
            description="CSF def",
        ),
        _uc("D_pos", f"1 * {_rp('p1')} + 1 * {x} >= 0"),
        _uc(
            "D_extra",
            f'1 * {_rp("p1")} - 1 * {x} + 1 * generator("G1").generation >= 0',
        ),
        _uc("D_mixed", f"1 * {_rp('p1')} + 1 * {_rp('p2', 'dn')} - 1 * {x} >= 0"),
        _uc("D_nodb", f"1 * {_rp('p1')} - 1 * {x} >= 0"),
        _uc("D_rhs", f"1 * {_rp('p1')} - 1 * {x} >= 0"),
        _uc("D_same", f"1 * {_rp('p1')} - 1 * {x} <= 0"),
        _uc("D_taken", f"1 * {_rp('p1')} - 1 * {x} >= 0"),
    ]
    db = _db(
        {
            "D_ok": {"RHS": 0.0},
            "D_pos": {"RHS": 0.0},
            "D_extra": {"RHS": 0.0},
            "D_mixed": {"RHS": 0.0},
            "D_rhs": {"RHS": 3.0},
            "D_same": {"Sense": -1.0, "RHS": 0.0},
            "D_taken": {"RHS": 0.0},
        }
    )
    b = _builder(ucs, db)
    b.replaced["D_taken"] = "elsewhere"
    b.definitions()
    assert set(b.rewritten) == {"D_ok"}
    ok = b.rewritten["D_ok"]
    assert ok.expression.endswith(f"- 1 * {x} = 0")
    assert ok.rhs_profile == ()
    assert ok.description.startswith("CSF def [")
    assert "definition of X" in ok.description
    out = {u.name: u for u in b.user_constraints()}
    assert out["D_ok"] is ok
    assert out["D_taken"].active is False


# ── inertia minima + credits ───────────────────────────────────────────────
def _inertia_ucs() -> list[UserConstraintSpec]:
    return [
        _uc(
            "Inertia_e2",
            f"5 * {_u('G1')} + 3 * {_u('G2')} + 2 * {_u('NOPE')}"
            f" - 1000 * {_dv('I_NR')} >= 0",
        ),
        # Not inertia definitions: a non-status term / a bounded selector.
        _uc(
            "Not_def",
            f'5 * {_u("G1")} + 1 * generator("G1").generation - 1 * {_dv("J")} >= 0',
        ),
        _uc("Not_def2", f"9 * {_u('G1')} - 1 * {_dv('S')} >= 0"),
        _uc(
            "NR_ok",
            f"5 * {_u('G1')} + 3 * {_u('G2')} + 200 * {_dv('Y')} >= 700",
            penalty=10.0,
        ),
        _uc("Commit_Y", f"1 * {_u('G1')} + 1 * {_u('G2')} - 2 * {_dv('Y')} >= 0"),
        _uc("NR_nocommit", f"5 * {_u('G1')} + 100 * {_dv('Z')} >= 10"),
        _uc("NR_badcommit", f"5 * {_u('G1')} + 100 * {_dv('W')} >= 10"),
        _uc("Commit_W", f"1 * {_u('G1')} + 2 * {_u('G2')} - 1 * {_dv('W')} >= 0"),
        _uc("NR_extracommit", f"5 * {_u('G1')} + 100 * {_dv('V')} >= 10"),
        _uc(
            "Commit_V",
            f'1 * {_u("G1")} - 1 * {_dv("V")} + 1 * generator("G1").generation >= 0',
        ),
        _uc("NR_zero", f"5 * {_u('G1')} >= 0"),
        _uc("NR_unmapped", f"5 * {_u('G1')} + 2 * {_u('NOPE')} >= 10"),
        _uc("NR_foreign", f"7 * {_u('G9')} >= 5"),
        _uc("NR_le", f"5 * {_u('G1')} <= 5"),
        _uc("NR_negy", f"5 * {_u('G1')} - 1 * {_dv('Y')} >= 5"),
    ]


def _inertia_dvs() -> tuple[DecisionVariableSpec, ...]:
    return (
        DecisionVariableSpec(name="I_NR"),
        DecisionVariableSpec(name="Y", lower_bound=0.0, upper_bound=1.0),
        DecisionVariableSpec(name="W", lower_bound=0.0, upper_bound=1.0),
        DecisionVariableSpec(name="S", upper_bound=1.0),
        DecisionVariableSpec(name="V", lower_bound=0.0, upper_bound=1.0),
    )


def test_inertia_minima_and_credits() -> None:
    b = _builder(_inertia_ucs(), dvs=_inertia_dvs())
    b.inertia_minima()
    zones = {z.name: z for z in b.inertia_zones}
    assert set(zones) == {"NR_ok", "NR_unmapped"}
    ok = zones["NR_ok"]
    assert ok.requirement == 700.0
    assert ok.cost == 10.0
    (credit,) = ok.requirement_credits
    assert credit.name == "Y"
    assert credit.generators == ("G1", "G2")
    assert credit.units == pytest.approx(2.0)
    assert credit.credit == pytest.approx(200.0)
    assert credit.integer is None
    assert zones["NR_unmapped"].cost is None
    assert b.replaced["Commit_Y"] == "InertiaZone 'NR_ok' credit"
    for name in ("NR_nocommit", "NR_badcommit", "NR_extracommit", "NR_zero"):
        assert name not in b.replaced
    text = "\n".join(b.warnings)
    for dv in ("Z", "W", "V"):
        assert f"{dv} has no 'Σ u − k·y >= 0' commit row" in text
    assert "commitment(uc_NOPE).status not mapped" in text
    # P_ref = the commitment floor (20 MW) for G1, 1 MW for G2.
    prov = {p.generator: p for p in b.inertia_provisions}
    assert prov["G1"].provision_max == pytest.approx(20.0)
    assert prov["G1"].provision_factor == pytest.approx(5.0 / 20.0)
    assert prov["G2"].provision_max == pytest.approx(1.0)


def test_add_inertia_provision_share_and_rename() -> None:
    b = _builder([])
    b._add_inertia_provision("G1", "Z1", 5.0)
    b._add_inertia_provision("G1", "Z1", 5.0)  # same zone: unchanged
    b._add_inertia_provision("G1", "Z2", 5.0)  # shared
    b._add_inertia_provision("G1", "Z3", 7.0)  # other H·S: own provision
    names = [(p.name, p.inertia_zones) for p in b.inertia_provisions]
    assert names == [
        ("inertia_G1", ("Z1", "Z2")),
        ("inertia_G1__Z3", ("Z3",)),
    ]


def test_same_units() -> None:
    parsed = parse_expression(f"5 * {_u('A')} + 3 * {_u('B')} >= 0")
    assert parsed is not None
    units = list(parsed[0])
    assert _same_units(units, {"uc_A": 5.0, "uc_B": 3.0})
    assert not _same_units(units, {"uc_A": 5.0})
    assert not _same_units(units, {"uc_A": 5.0, "uc_B": 4.0})


# ── northern specials (opt-in) ─────────────────────────────────────────────
def test_north_special_planes() -> None:
    ucs = [
        _uc(
            "SpecialCPFNorth",
            f"1 * {_rp('p1')} - 1 * {_dv('NR')} >= 0",
            penalty=500.0,
            active=False,
        ),
        _uc(
            "SpecialCPFNorth_Definition",
            f"1 * {_dv('NR')} - 1 * {_dv('Tx')} >= 7",
            active=False,
        ),
        _uc(
            "SpecialCSFNorth",
            f"1 * {_rp('p1')} + 1 * {_rp('p2', 'dn')} >= 0",
            active=False,
        ),
        _uc("SpecialCTFNorthLW", f"1 * {_rp('p2', 'dn')} - 1 * {_dv('Q')} >= 2"),
        _uc("SpecialCPFSouth", f"1 * {_rp('p1')} >= 1"),
        _uc("Unparsed", "sum(g in G, generator(g).generation) >= 0"),
    ]
    b = _builder(ucs, _db({"SpecialCPFNorth": {"Sense": 0.0}}))
    b.north_special()
    zones = {z.name: z for z in b.new_zones}
    assert set(zones) == {"SpecialCPFNorth", "SpecialCTFNorthLW"}
    cpf = zones["SpecialCPFNorth"]
    assert cpf.urreq_planes[0].alpha == pytest.approx(7.0)
    assert cpf.urcost == 500.0
    assert cpf.requirement_sense == "eq"
    ctf = zones["SpecialCTFNorthLW"]
    assert ctf.drreq_planes[0].alpha == pytest.approx(2.0)  # Q: no definition
    assert ctf.urreq_planes == ()
    assert ctf.requirement_sense == ""
    assert b.zone_factors["p1"]["SpecialCPFNorth"] == (1.0, 0.0)
    assert b.zone_factors["p2"]["SpecialCTFNorthLW"] == (0.0, 1.0)


def test_north_special_flag_through_apply() -> None:
    ucs = [
        *_case().user_constraints,
        _uc("SpecialCPFNorth", f"1 * {_rp('provision_G1')} >= 4", active=False),
    ]
    off = apply_native_reserve(_case(ucs), _OPTS)
    assert all(z.name != "SpecialCPFNorth" for z in off.reserves)
    on = apply_native_reserve(_case(ucs), replace(_OPTS, north_special=True))
    assert _zone(on, "SpecialCPFNorth").urreq_planes[0].alpha == pytest.approx(4.0)


# ── writer ─────────────────────────────────────────────────────────────────
_LAYOUT = ((1, 2), (3, 4))


def test_writer_inertia_zone_credits() -> None:
    zone = InertiaZoneSpec(
        name="Z",
        requirement=(1.0, 3.0, 5.0, 7.0),
        cost=10.0,
        requirement_credits=(
            InertiaCreditSpec(
                name="y",
                generators=("G",),
                units=1.0,
                credit=(2.0, 2.0, 4.0, 4.0),
                integer=True,
            ),
        ),
    )
    out = build_inertia_zone_array((zone,), _LAYOUT)[0]
    assert out["requirement"] == [[2.0, 6.0]]
    assert out["cost"] == 10.0
    assert out["requirement_credits"] == [
        {
            "name": "y",
            "generators": ["G"],
            "units": 1.0,
            "credit": [[2.0, 4.0]],
            "integer": True,
        }
    ]


def test_writer_equality_down_zone() -> None:
    zone = ReserveSpec(
        object_id=0,
        name="R",
        drreq_planes=(ReservePlaneSpec(alpha=1.0),),
        requirement_sense="eq",
        drsurplus_cost=2.0,
    )
    out = build_reserve_zone_array((zone,))[0]
    assert out["requirement_sense"] == "eq"
    assert out["drsurplus_cost"] == 2.0
    assert "ursurplus_cost" not in out


def test_writer_battery_emax_schedule_and_usage() -> None:
    bat = BatterySpec(
        object_id=1,
        name="B",
        bus_name="n",
        emax=200.0,
        emax_profile=(150.0, 150.0, 200.0, 180.0),
        reserve_energy_usage_up=0.3,
        reserve_energy_usage_down=0.1,
    )
    flat = build_battery_array((bat,))[0]
    assert flat["emax"] == [[150.0, 150.0, 200.0, 180.0]]
    assert flat["reserve_energy_usage_up"] == 0.3
    assert flat["reserve_energy_usage_down"] == 0.1
    blocks = build_battery_array((bat,), _LAYOUT)[0]
    assert blocks["emax"] == [[150.0, 180.0]]  # min per block


def test_writer_response_caps() -> None:
    prov = ReserveProvisionSpec(
        generator_name="G",
        name="p",
        urmax=10.0,
        urmax_response=(5.0, 5.0, 6.0, 6.0),
        drmax=10.0,
        drmax_charge_response=(3.0,) * 4,
        ur_energy_usage=0.3,
    )
    out = build_reserve_provision_array((prov,), _LAYOUT)[0]
    assert out["urmax_response"] == [[5.0, 6.0]]
    assert out["drmax_charge_response"] == 3.0  # constant: a scalar
    assert "urmax_charge_response" not in out
    assert out["ur_energy_usage"] == 0.3
    assert "dr_energy_usage" not in out


# ── superseded extractor zones ─────────────────────────────────────────────
def test_fixed_zone_supersedes_extractor_zones() -> None:
    """The row's Reserve members are retired; a lone-zone provision keeps it.

    ``CSF_UpMinProvision = 1·CSF_RS + 1·CSF_RS_BESS`` becomes the only zone
    with the requirement: ``CSF_RS`` is dropped (and removed from the
    provisions), ``CSF_RS_BESS`` stays requirement-free because a
    provision outside the row still lists it as its only zone, and
    ``CTF_RS`` (not a member) is untouched.
    """
    k = DbKit()
    k.db.horizon_start = _DAY1
    cid = k.obj("Constraint", "CSF_UpMinProvision")
    k.sysprop("Constraint", cid, "RHS", 20.0)
    for name, coef in (("CSF_RS", 1.0), ("CSF_RS_BESS", 1.0), ("CTF_RS", 0.0)):
        rid = k.obj("Reserve", name)
        k.rel(
            "Reserve",
            rid,
            "Constraint",
            cid,
            "Constraints",
            {"Provision Coefficient": coef},
        )
    req = (20.0,) * 12 + (25.0,) * 12
    reserves = (
        ReserveSpec(
            object_id=1,
            name="CSF_RS",
            ur_requirement=req,
            ur_min_provision=18.0,
            urcost=794.0,
        ),
        ReserveSpec(object_id=2, name="CSF_RS_BESS"),
        ReserveSpec(object_id=3, name="CTF_RS", ur_requirement=(5.0,) * 24),
    )
    provisions = (
        ReserveProvisionSpec(
            generator_name="G1",
            reserve_zones=("CSF_RS", "CTF_RS"),
            ur_zone_factors=(1.0, 1.0),
            dr_zone_factors=(0.0, 0.0),
        ),
        ReserveProvisionSpec(generator_name="G2", reserve_zones=("CSF_RS",)),
        ReserveProvisionSpec(
            generator_name="B1_gen",
            name="provision_B1_gen__CSF_RS_BESS",
            reserve_zones=("CSF_RS_BESS",),
        ),
    )
    b = _builder(
        [
            _uc(
                "CSF_UpMinProvision",
                f"1 * {_rp('provision_G1')} + 1 * {_rp('provision_G2')} = 20",
                rhs_profile=req,
            )
        ],
        k.db,
        reserves=reserves,
        reserve_provisions=provisions,
    )
    b.fixed_zones()
    assert b.retired == {
        "CSF_RS": "CSF_UpMinProvision",
        "CSF_RS_BESS": "CSF_UpMinProvision",
    }
    provs = {p.generator_name: p for p in b.provisions()}
    assert provs["G1"].reserve_zones == ("CTF_RS", "CSF_UpMinProvision")
    assert provs["G1"].ur_zone_factors == (1.0, 1.0)
    assert provs["G1"].dr_zone_factors == (0.0, 0.0)
    assert provs["G2"].reserve_zones == ("CSF_UpMinProvision",)
    assert provs["B1_gen"].reserve_zones == ("CSF_RS_BESS",)
    zones = {z.name: z for z in b.reserves(tuple(provs.values()))}
    assert "CSF_RS" not in zones
    bess = zones["CSF_RS_BESS"]
    assert bess.ur_requirement == ()
    assert bess.urcost == 0.0
    assert "CSF_UpMinProvision" in bess.description
    assert zones["CTF_RS"].ur_requirement == (5.0,) * 24
    native = zones["CSF_UpMinProvision"]
    assert native.ur_requirement == req
    assert native.requirement_sense == "eq"
