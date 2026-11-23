# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of :func:`plexos2gtopt.parsers.extract_user_constraints`.

Each test declares only the PLEXOS tables the branch needs through the
in-memory :class:`DbKit` builder.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt.entities import ReserveSpec, UserConstraintSpec
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


class UcKit(DbKit):
    """DbKit with System->Constraint helpers."""

    def __init__(self) -> None:
        super().__init__()
        self.sys_coll = self.coll("System", "Constraint", "Constraints")
        self.p_sense = self.prop(self.sys_coll, "Sense")
        self.p_rhs = self.prop(self.sys_coll, "RHS")

    def con(
        self,
        name: str,
        sense: float | None = -1.0,
        rhs: float | None = 10.0,
        **props: float,
    ) -> int:
        """Add a Constraint with Sense / RHS / extra System properties."""
        oid = self.obj("Constraint", name)
        mid = self.sys_mid("Constraint", oid)
        if sense is not None:
            self.data(mid, self.p_sense, sense)
        if rhs is not None:
            self.data(mid, self.p_rhs, rhs)
        for pname, val in props.items():
            self.data(mid, self.prop(self.sys_coll, pname.replace("_", " ")), val)
        return oid

    def term(
        self, parent: str, parent_oid: int, con: int, prop: str, value: float
    ) -> int:
        """Coefficient membership ``parent -> Constraint``."""
        return self.rel(
            parent, parent_oid, "Constraint", con, "Constraints", {prop: value}
        )


def _ucs(kit: DbKit, tmp_path: Path, **kw: object) -> dict[str, UserConstraintSpec]:
    b = kw.pop("bundle", None) or bundle(tmp_path)
    out = P.extract_user_constraints(kit.db, b, **kw)  # type: ignore[arg-type]
    return {u.name: u for u in out}


# --------------------------------------------------------------------------
# early returns
# --------------------------------------------------------------------------


def test_no_system_collection_or_props(tmp_path: Path) -> None:
    """Missing System->Constraints or Sense/RHS properties -> empty."""
    kit = DbKit()
    kit.obj("Constraint", "C", sys_member=False)
    assert not P.extract_user_constraints(kit.db, bundle(tmp_path))
    kit = DbKit()
    kit.obj("Constraint", "C")
    assert not P.extract_user_constraints(kit.db, bundle(tmp_path))


def test_invalid_period_rhs_mode(tmp_path: Path) -> None:
    """An unknown ``period_rhs_mode`` is rejected."""
    kit = UcKit()
    kit.con("C")
    with pytest.raises(ValueError, match="period_rhs_mode"):
        P.extract_user_constraints(kit.db, bundle(tmp_path), period_rhs_mode="bogus")


# --------------------------------------------------------------------------
# Include in ST Schedule resolution
# --------------------------------------------------------------------------


def test_include_st_rows_and_inactive_scenarios(tmp_path: Path) -> None:
    """Constraints without rows or only inactive-scenario rows stay active."""
    kit = UcKit()
    gen = kit.obj("Generator", "G")
    p_inc = kit.prop(kit.sys_coll, "Include in ST Schedule")
    scen = kit.obj("Scenario", "S_OFF")
    a = kit.con("A")
    b = kit.con("B")
    c = kit.con("C")
    kit.data(kit.sys_mid("Constraint", b), p_inc, 0.0, tags=[scen])
    kit.data(kit.sys_mid("Constraint", c), p_inc, 0.0)
    for con in (a, b, c):
        kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    out = _ucs(kit, tmp_path)
    assert out["A"].active is not False
    assert out["B"].active is not False
    assert out["C"].active is False


# --------------------------------------------------------------------------
# index builders
# --------------------------------------------------------------------------


def test_unhandled_kinds_and_fuel_offtake_dangling(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unmapped coefficient kinds warn; dangling Gen->Fuel rows skipped."""
    kit = UcKit()
    gen = kit.obj("Generator", "G")
    fuel = kit.obj("Fuel", "Gas_A")
    kit.sysprop("Generator", gen, "Heat Rate", 8.0)
    kit.mem("Generator", "Fuel", gen, fuel, coll_name="Fuels")
    kit.mem("Generator", "Fuel", 999, fuel, coll_name="Fuels")
    kit.mem("Generator", "Fuel", gen, 998, coll_name="Fuels")
    con = kit.con("Fuel_Cap", rhs=100.0)
    kit.term("Fuel", fuel, con, "Offtake Coefficient", 1.0)
    kit.term("Generator", gen, con, "Weird Coefficient", 1.0)
    with caplog.at_level(logging.WARNING, logger=P.logger.name):
        out = _ucs(kit, tmp_path)
    assert "no gtopt mapping" in caplog.text
    assert 'generator("G").generation' in out["Fuel_Cap"].expression


def test_reserve_provision_expansion(tmp_path: Path) -> None:
    """Reserve Provision Coefficient expands over eligible providers."""
    kit = UcKit()
    g1 = kit.obj("Generator", "G1")
    g2 = kit.obj("Generator", "G2")
    g3 = kit.obj("Generator", "G3")
    up = kit.obj("Reserve", "CPF_RS")
    dn = kit.obj("Reserve", "CPF_LW")
    for g in (g1, g2, g3):
        kit.mem("Reserve", "Generator", up, g, coll_name="Generators")
    kit.mem("Reserve", "Generator", dn, g1, coll_name="Generators")
    kit.mem("Reserve", "Generator", dn, 999, coll_name="Generators")
    kit.mem("Reserve", "Generator", 998, g1, coll_name="Generators")
    con = kit.con("RSV", sense=1.0, rhs=5.0)
    kit.term("Reserve", up, con, "Provision Coefficient", 1.0)
    kit.term("Reserve", dn, con, "Provision Coefficient", 2.0)
    out = _ucs(
        kit,
        tmp_path,
        emitted_names={
            "Generator": frozenset({"G1", "G2"}),
            "ReserveProvision": frozenset({"provision_G1", "provision_G3"}),
        },
        pmax_by_gen={"G1": 10.0, "G2": 0.0},
        unusable_provisions=frozenset({"provision_G2"}),
    )
    expr = out["RSV"].expression
    assert 'reserve_provision("provision_G1").up' in expr
    assert 'reserve_provision("provision_G1").dn' in expr
    assert "G2" not in expr and "G3" not in expr


def test_reserve_provision_zone_less_and_offline(tmp_path: Path) -> None:
    """Offline providers and zone-less provisions are dropped."""
    kit = UcKit()
    g1 = kit.obj("Generator", "G1")
    g2 = kit.obj("Generator", "G2")
    g3 = kit.obj("Generator", "G3")
    up = kit.obj("Reserve", "CPF_RS")
    for g in (g1, g2, g3):
        kit.mem("Reserve", "Generator", up, g, coll_name="Generators")
    con = kit.con("RSV", sense=1.0, rhs=5.0)
    kit.term("Reserve", up, con, "Provision Coefficient", 1.0)
    out = _ucs(
        kit,
        tmp_path,
        pmax_by_gen={"G1": 10.0, "G2": 0.0, "G3": 10.0},
        pmax_profiles_by_gen={"G2": (0.0, 0.0)},
        unusable_provisions=frozenset({"provision_G3"}),
    )
    expr = out["RSV"].expression
    assert 'reserve_provision("provision_G1").up' in expr
    assert "G2" not in expr and "G3" not in expr


def test_definition_decision_variables(tmp_path: Path) -> None:
    """``Decision Variable -> Definition`` memberships add a value term."""
    kit = UcKit()
    dv1 = kit.obj("Decision Variable", "DV1")
    dv2 = kit.obj("Decision Variable", "DV2")
    gen = kit.obj("Generator", "G")
    con = kit.con("DEF", sense=0.0, rhs=0.0)
    kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    for dv in (dv1, dv2, 999):
        kit.mem("Decision Variable", "Constraint", dv, con, coll_name="Definition")
    out = _ucs(kit, tmp_path, emitted_names={"DecisionVariable": frozenset({"DV1"})})
    expr = out["DEF"].expression
    assert 'decision_variable("DV1").value' in expr
    assert "DV2" not in expr


def test_hydro_maxrampday_rhs(tmp_path: Path) -> None:
    """A Hydro_MaxRampDay.csv entry supplies the per-day RHS."""
    kit = UcKit()
    gen = kit.obj("Generator", "G")
    con = kit.con("PLANTmaxramp", rhs=None)
    kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    body = "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\nPLANTmaxramp,2026,1,1,1,2\n"
    out = _ucs(kit, tmp_path, bundle=bundle(tmp_path, {"Hydro_MaxRampDay.csv": body}))
    uc = out["PLANTmaxramp"]
    assert len(uc.rhs_profile) == 24


def test_constraint_without_system_membership_and_unknown_sense(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No System membership -> skipped; unknown Sense -> skipped."""
    kit = UcKit()
    gen = kit.obj("Generator", "G")
    orphan = kit.obj("Constraint", "ORPHAN", sys_member=False)
    weird = kit.con("WEIRD", sense=7.0)
    kit.term("Generator", gen, orphan, "Generation Coefficient", 1.0)
    kit.term("Generator", gen, weird, "Generation Coefficient", 1.0)
    with caplog.at_level(logging.DEBUG, logger=P.logger.name):
        assert not _ucs(kit, tmp_path)
    assert "unknown Sense" in caplog.text


def test_timeslice_failure_and_date_overlay(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed timeslice falls back to scalar; dated rows overlay."""
    kit = UcKit()
    kit.db.horizon_start = datetime(2026, 4, 22)
    gen = kit.obj("Generator", "G")
    bad = kit.con("BAD_TS", rhs=None)
    kit.data(kit.sys_mid("Constraint", bad), kit.p_rhs, 10.0, timeslice="Q9")
    dated = kit.con("DATED", rhs=None)
    mid = kit.sys_mid("Constraint", dated)
    kit.data(mid, kit.p_rhs, 50.0)
    kit.data(
        mid,
        kit.p_rhs,
        20.0,
        date_from=datetime(2026, 4, 22, 6),
        date_to=datetime(2026, 4, 22, 12),
    )
    for con in (bad, dated):
        kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    with caplog.at_level(logging.WARNING, logger=P.logger.name):
        out = _ucs(kit, tmp_path)
    assert "timeslice expansion failed" in caplog.text
    assert out["BAD_TS"].expression.endswith("<= 10")
    assert out["DATED"].rhs_profile


def test_period_rhs_multiple_and_drop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Several period RHS warn; ``drop`` mode skips the constraint."""
    kit = UcKit()
    gen = kit.obj("Generator", "G")
    con = kit.con("WEEKLY", rhs=None, RHS_Week=7.0, RHS_Month=30.0)
    kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    with caplog.at_level(logging.WARNING, logger=P.logger.name):
        out = _ucs(kit, tmp_path)
    assert "several period RHS" in caplog.text
    assert "WEEKLY" in out
    assert not _ucs(kit, tmp_path, period_rhs_mode="drop")


def test_reserve_requirement_profile_rhs(tmp_path: Path) -> None:
    """Reserve Provision terms + reserve profiles yield a per-block RHS."""
    kit = UcKit()
    g1 = kit.obj("Generator", "G1")
    up = kit.obj("Reserve", "CPF_RS")
    dn = kit.obj("Reserve", "CPF_LW")
    other = kit.obj("Reserve", "UNLISTED")
    flat = kit.obj("Reserve", "FLAT_RS")
    for r in (up, dn, other, flat):
        kit.mem("Reserve", "Generator", r, g1, coll_name="Generators")
    con = kit.con("RSV", sense=1.0, rhs=0.0)
    for r, coef in ((up, 1.0), (dn, 1.0), (other, 1.0), (flat, 1.0)):
        kit.term("Reserve", r, con, "Provision Coefficient", coef)
    reserves = (
        ReserveSpec(object_id=up, name="CPF_RS", ur_requirement=(1.0, 2.0)),
        ReserveSpec(object_id=dn, name="CPF_LW", dr_requirement=(3.0,)),
        ReserveSpec(object_id=flat, name="FLAT_RS"),
    )
    out = _ucs(kit, tmp_path, reserves=reserves)
    prof = out["RSV"].rhs_profile
    assert prof[:2] == (4.0, 2.0)


# --------------------------------------------------------------------------
# battery / reserve memberships
# --------------------------------------------------------------------------


def test_bess_reserve_expansion_and_battery_members(tmp_path: Path) -> None:
    """Reserve->Battery providers, AUX redirect and allowed-provision filter."""
    kit = UcKit()
    b1 = kit.obj("Battery", "B1")
    b1_aux = kit.obj("Battery", "B1_AUX")
    b2 = kit.obj("Battery", "B2")
    z_up = kit.obj("Reserve", "ZONE_X")  # no _LW/_RS tag -> "up"
    z_dn = kit.obj("Reserve", "CPF_LW_BESS")
    z_rs = kit.obj("Reserve", "CSF_RS_BESS")
    z_none = kit.obj("Reserve", "NOBATS")
    for z in (z_up, z_dn, z_rs):
        kit.mem("Reserve", "Battery", z, b1_aux, coll_name="Batteries")
        kit.mem("Reserve", "Battery", z, b1, coll_name="Batteries")
    kit.mem("Reserve", "Battery", z_up, 999, coll_name="Batteries")
    kit.mem("Reserve", "Battery", 998, b1, coll_name="Batteries")
    con = kit.con("BESS_RSV", sense=1.0, rhs=1.0)
    for z in (z_up, z_dn, z_rs, z_none):
        kit.term("Reserve", z, con, "Provision Coefficient", 1.0)
    for b in (b1_aux, b2):
        kit.mem("Battery", "Constraint", b, con, coll_name="Constraints")
    kit.mem("Battery", "Constraint", 997, con, coll_name="Constraints")
    out = _ucs(
        kit,
        tmp_path,
        emitted_names={
            "Battery": frozenset({"B1", "B2"}),
            "ReserveProvision": frozenset(
                {"provision_B1_gen__ZONE_X", "provision_B1_gen__CPF_LW_BESS"}
            ),
        },
    )
    expr = out["BESS_RSV"].expression
    assert 'reserve_provision("provision_B1_gen__ZONE_X").up' in expr
    assert 'reserve_provision("provision_B1_gen__CPF_LW_BESS").dn' in expr
    assert "CSF_RS_BESS" not in expr


def test_drop_batteries_drops_battery_terms(tmp_path: Path) -> None:
    """``drop_batteries`` silently removes Battery coefficient terms."""
    kit = UcKit()
    bat = kit.obj("Battery", "B")
    gen = kit.obj("Generator", "G")
    con = kit.con("MIX")
    kit.term("Battery", bat, con, "Generation Coefficient", 1.0)
    kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    out = _ucs(kit, tmp_path, drop_batteries=True)
    assert "battery(" not in out["MIX"].expression


def test_always_on_commitment_terms(tmp_path: Path) -> None:
    """Always-on renewables: status absorbed into the RHS, startup dropped."""
    kit = UcKit()
    g = kit.obj("Generator", "G")
    h = kit.obj("Generator", "H")
    con = kit.con("ON", sense=-1.0, rhs=3.0)
    kit.term("Generator", g, con, "Units Generating Coefficient", 1.0)
    kit.term("Generator", g, con, "Units Started Coefficient", 1.0)
    kit.term("Generator", h, con, "Generation Coefficient", 1.0)
    out = _ucs(
        kit,
        tmp_path,
        emitted_names={
            "Generator": frozenset({"G", "H"}),
            "Commitment": frozenset(),
        },
        always_on_gens=frozenset({"G"}),
    )
    assert out["ON"].expression == '1 * generator("H").generation <= 2'


def test_zone_less_direct_provision_dropped(tmp_path: Path) -> None:
    """A direct provision term on a zone-less provision is dropped."""
    kit = UcKit()
    g = kit.obj("Generator", "G")
    h = kit.obj("Generator", "H")
    con = kit.con("ZL", sense=1.0, rhs=1.0)
    kit.term("Generator", g, con, "Raise Reserve Provision Coefficient", 1.0)
    kit.term("Generator", h, con, "Generation Coefficient", 1.0)
    out = _ucs(kit, tmp_path, unusable_provisions=frozenset({"provision_G"}))
    assert "reserve_provision" not in out["ZL"].expression


def test_fuel_offtake_filters(tmp_path: Path) -> None:
    """Fuel offtake drops un-emitted and fully-offline generators."""
    kit = UcKit()
    fuel = kit.obj("Fuel", "Gas_A")
    names = ("G1", "G2", "G3", "G4")
    ids = {n: kit.obj("Generator", n) for n in names}
    for gid in ids.values():
        kit.mem("Generator", "Fuel", gid, fuel, coll_name="Fuels")
    con = kit.con("OFFTAKE", rhs=100.0)
    kit.term("Fuel", fuel, con, "Offtake Coefficient", 1.0)
    out = _ucs(
        kit,
        tmp_path,
        emitted_names={"Generator": frozenset({"G1", "G2", "G3"})},
        heat_rate_by_gen={"G1": 8.0, "G2": 9.0, "G3": 7.0, "G4": 6.0},
        pmax_by_gen={"G1": 10.0, "G2": 0.0, "G3": 0.0},
        pmax_profiles_by_gen={"G3": (0.0, 5.0)},
    )
    expr = out["OFFTAKE"].expression
    assert 'generator("G1")' in expr and 'generator("G3")' in expr
    assert "G2" not in expr and "G4" not in expr


def test_index_builders_without_properties(tmp_path: Path) -> None:
    """Collections present but properties missing are tolerated."""
    kit = UcKit()
    gen = kit.obj("Generator", "G")
    kit.coll("Fuel", "Constraint", "Constraints")
    kit.coll("Reserve", "Constraint", "Constraints")
    con = kit.con("C")
    kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    fuel = kit.obj("Fuel", "F")
    kit.term("Fuel", fuel, con, "Offtake Coefficient", 0.0)  # prop, no gen map
    assert "C" in _ucs(kit, tmp_path)


# --------------------------------------------------------------------------
# derived coefficients
# --------------------------------------------------------------------------


def test_derived_coefficient_modes(tmp_path: Path) -> None:
    """Sent-out / per-capacity / curtailed / available / ramp modes."""
    kit = UcKit()
    names = ("SO", "PC", "PC0", "CU", "AV", "AV0", "RA", "RA0", "SKIP")
    ids = {n: kit.obj("Generator", n) for n in names}
    con = kit.con("DERIVED", sense=-1.0, rhs=100.0)
    kit.term("Generator", ids["SO"], con, "Generation Sent Out Coefficient", 2.0)
    kit.term("Generator", ids["PC"], con, "Capacity Factor Coefficient", 10.0)
    kit.term("Generator", ids["PC0"], con, "Capacity Factor Coefficient", 10.0)
    kit.term("Generator", ids["CU"], con, "Generation Curtailed Coefficient", 1.0)
    kit.term("Generator", ids["AV"], con, "Available Capacity Coefficient", 1.0)
    kit.term("Generator", ids["AV0"], con, "Available Capacity Coefficient", 1.0)
    kit.term("Generator", ids["RA"], con, "Ramp Up Coefficient", 1.0)
    kit.term("Generator", ids["RA0"], con, "Ramp Down Coefficient", 1.0)
    kit.term("Generator", ids["SKIP"], con, "Generation Sent Out Coefficient", 1.0)
    out = _ucs(
        kit,
        tmp_path,
        emitted_names={"Generator": frozenset(set(names) - {"SKIP"})},
        pmax_by_gen={
            "SO": 1.0,
            "PC": 5.0,
            "PC0": 0.0,
            "AV": 4.0,
            "AV0": 0.0,
            "RA": 3.0,
            "RA0": 0.0,
        },
        pmax_profiles_by_gen={"CU": (6.0, 8.0)},
    )
    uc = out["DERIVED"]
    expr = uc.expression
    assert '2 * generator("SO").generation' in expr
    assert '2 * generator("PC").generation' in expr
    assert "PC0" not in expr and "AV" not in expr and "SKIP" not in expr
    assert '- 1 * generator("CU").generation' in expr
    assert 'generator("RA").generation_prev' in expr
    assert "RA0" not in expr
    assert uc.rhs_profile


# --------------------------------------------------------------------------
# RHS-shape and inactivity edge cases
# --------------------------------------------------------------------------


def test_rhs_sources_without_values(tmp_path: Path) -> None:
    """RHS Custom / period properties without data leave the UC skipped."""
    kit = UcKit()
    kit.db.horizon_start = datetime(2026, 4, 22)
    gen = kit.obj("Generator", "G")
    kit.prop(kit.sys_coll, "RHS Custom")
    kit.prop(kit.sys_coll, "RHS Week")
    con = kit.con("NORHS", rhs=None)
    kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    old = kit.con("OLDDATED", rhs=None)
    kit.data(
        kit.sys_mid("Constraint", old),
        kit.p_rhs,
        1e30,
        date_from=datetime(2026, 4, 22, 3),
        date_to=datetime(2026, 4, 22, 4),
    )
    kit.term("Generator", gen, old, "Generation Coefficient", 1.0)
    out = _ucs(kit, tmp_path)
    assert "NORHS" not in out


def test_daily_rhs_on_line_and_period_rhs_on_provision(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Daily RHS on a non-generation LHS; period RHS with no period unit."""
    kit = UcKit()
    line = kit.obj("Line", "L")
    gen = kit.obj("Generator", "G")
    daily = kit.con("DAILY_LINE", rhs=None, RHS_Day=5.0)
    kit.term("Line", line, daily, "Flow Coefficient", 1.0)
    weekly = kit.con("WEEKLY_RSV", rhs=None, RHS_Week=5.0)
    kit.term("Generator", gen, weekly, "Raise Reserve Provision Coefficient", 1.0)
    with caplog.at_level(logging.DEBUG, logger=P.logger.name):
        out = _ucs(kit, tmp_path)
    assert "DAILY_LINE" in out
    assert "WEEKLY_RSV" not in out


def test_hard_list_override_and_structural_infeasibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard-listed shutoff artifacts stay active; GE rows with c<=0 inactive."""
    monkeypatch.setattr(P, "_load_plexos_hard_uc_list", lambda: frozenset({"SHUT"}))
    kit = UcKit()
    gen = kit.obj("Generator", "G")
    shut = kit.con("SHUT", sense=0.0, rhs=0.0)
    kit.term("Generator", gen, shut, "Generation Coefficient", 1.0)
    infeas = kit.con("INFEAS", sense=1.0, rhs=5.0)
    kit.term("Generator", gen, infeas, "Generation Coefficient", -1.0)
    out = _ucs(kit, tmp_path)
    assert out["SHUT"].active is not False
    assert out["INFEAS"].active is False


def test_reserve_terms_without_profiles_keep_scalar(tmp_path: Path) -> None:
    """Reserve Provision terms whose reserves carry no profile keep the scalar."""
    kit = UcKit()
    g1 = kit.obj("Generator", "G1")
    rsv = kit.obj("Reserve", "FLAT_RS")
    kit.mem("Reserve", "Generator", rsv, g1, coll_name="Generators")
    con = kit.con("RSV", sense=1.0, rhs=7.0)
    kit.term("Reserve", rsv, con, "Provision Coefficient", 1.0)
    out = _ucs(kit, tmp_path, reserves=(ReserveSpec(object_id=rsv, name="FLAT_RS"),))
    assert not out["RSV"].rhs_profile


def test_unresolved_reference_without_close_match(tmp_path: Path) -> None:
    """An unknown reference with no near name raises without a hint."""
    kit = UcKit()
    gen = kit.obj("Generator", "QQQQ")
    con = kit.con("C")
    kit.term("Generator", gen, con, "Generation Coefficient", 1.0)
    with pytest.raises(P.UnresolvedConstraintReferenceError) as err:
        P.extract_user_constraints(
            kit.db, bundle(tmp_path), emitted_names={"Generator": frozenset({"ZZ"})}
        )
    assert "closest emitted name" not in str(err.value)
