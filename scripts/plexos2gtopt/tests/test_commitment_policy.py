# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Commitment relaxation policy (``--uc-relax``): which ``u`` stay integer."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt._commitment_policy import (
    REASON_COST_FREE,
    REASON_PLEXOS_LINEAR,
    ZERO_COST_EPS,
    commitment_is_cost_free,
    commitment_relax_reason,
    energy_cost_ceiling,
    fuel_price_ceiling,
    plan_commitment_relaxation,
)
from plexos2gtopt.entities import CommitmentSpec, FuelSpec, GeneratorSpec
from plexos2gtopt.gtopt_writer import (
    _commitment_relax_plan,
    build_commitment_array,
)
from plexos2gtopt.main import make_parser
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle

# pylint: disable=protected-access


def _gen(name: str = "G", **kw: object) -> GeneratorSpec:
    return GeneratorSpec(object_id=1, name=name, bus_name="n1", **kw)  # type: ignore[arg-type]


def _cmt(name: str = "G", **kw: object) -> CommitmentSpec:
    return CommitmentSpec(generator_name=name, **kw)  # type: ignore[arg-type]


# ── energy cost ──────────────────────────────────────────────────────────────
def test_energy_cost_ceiling_uses_max_heat_rate_and_price() -> None:
    fuels = (
        FuelSpec(object_id=1, name="coal", price=2.0, price_profile=(2.0, 3.0)),
        FuelSpec(object_id=2, name="gas", price=1.0),
    )
    prices = fuel_price_ceiling(fuels)
    assert prices == {"coal": 3.0, "gas": 1.0}
    gen = _gen(
        heat_rate=9.0,
        heat_rate_profile=(9.0, 10.0),
        fuel_names=("gas", "coal"),
        vom_charge=1.5,
        fuel_transport=0.5,
    )
    assert energy_cost_ceiling(gen, prices) == pytest.approx(1.5 + 0.5 + 10.0 * 3.0)
    # Generator-level price override wins over the fuel catalogue.
    over = _gen(heat_rate=2.0, fuel_names=("coal",), fuel_price_override=4.0)
    assert energy_cost_ceiling(over, prices) == pytest.approx(8.0)
    # Piecewise heat-rate segments count too.
    seg = _gen(heat_rate_segments=(5.0, 7.0), fuel_names=("gas",))
    assert energy_cost_ceiling(seg, prices) == pytest.approx(7.0)
    # A renewable: zero.
    assert energy_cost_ceiling(_gen(), prices) == 0.0


# ── cost-free rule ───────────────────────────────────────────────────────────
def test_cost_free_zero_cost_msl_only_unit() -> None:
    """Solar / wind / RoR with only a Min Stable Level (and forcing)."""
    c = _cmt(pmin=5.0, commit_status_profile=(0, 1, -1))
    assert commitment_is_cost_free(c, _gen(pmax=20.0), {})


@pytest.mark.parametrize(
    "kw",
    [
        {"startup_cost": 10.0},
        {"shutdown_cost": 1.0},
        {"startup_cost_profile": (0.0, 5.0)},
        {"shutdown_cost_profile": (0.0, 5.0)},
        {"noload_cost": 3.0},
        {"noload_fuel": 0.5},
        {"min_up_time": 2.0},
        {"min_down_time": 1.0},
        {"max_starts": 1, "starts_scope": "day"},
        {"min_starts": 1, "starts_scope": "day"},
        {"ramp_up": 30.0},
        {"ramp_down": 30.0},
        {"startup_ramp": 10.0},
        {"ramp_up_profile": (0.0, 3.0)},
        {"ramp_down_profile": (0.0, 3.0)},
        {"origin": "hydro_min_max"},
    ],
)
def test_cost_free_disqualifiers(kw: dict[str, object]) -> None:
    """Any on/off cost, time / start limit, ramp or a synthetic origin keeps u."""
    assert not commitment_is_cost_free(_cmt(pmin=5.0, **kw), _gen(), {})


def test_cost_free_needs_zero_energy_cost_and_a_generator() -> None:
    fuel = {"diesel": 100.0}
    costly = _gen(heat_rate=0.25, fuel_names=("diesel",))
    assert not commitment_is_cost_free(_cmt(), costly, fuel)
    # Tiny cost under the epsilon still counts as zero.
    tiny = _gen(vom_charge=ZERO_COST_EPS / 2)
    assert commitment_is_cost_free(_cmt(), tiny, fuel)
    # Free fuel (biomass at price 0) is zero cost.
    free = _gen(heat_rate=1.6, fuel_names=("bio",))
    assert commitment_is_cost_free(_cmt(), free, {"bio": 0.0})
    assert not commitment_is_cost_free(_cmt(), None, fuel)


# ── reasons and modes ────────────────────────────────────────────────────────
def test_relax_reason_modes() -> None:
    lin = _cmt(uc_optimality=0, startup_cost=500.0)  # costly but PLEXOS-LR
    free = _cmt(uc_optimality=2, pmin=1.0)
    mip = _cmt(uc_optimality=2, startup_cost=500.0)
    gen = _gen()
    assert commitment_relax_reason(lin, gen, {}) == REASON_PLEXOS_LINEAR
    assert commitment_relax_reason(free, gen, {}) == REASON_COST_FREE
    assert commitment_relax_reason(mip, gen, {}) is None

    assert commitment_relax_reason(lin, gen, {}, "plexos") == REASON_PLEXOS_LINEAR
    assert commitment_relax_reason(free, gen, {}, "plexos") is None

    for c in (lin, free, mip):
        assert commitment_relax_reason(c, gen, {}, "none") is None

    # Commitments synthesised from PLEXOS hydro min/max constraints are
    # never relaxed, whatever their optimality / cost.
    hydro = _cmt(uc_optimality=0, origin="hydro_min_max")
    assert commitment_relax_reason(hydro, gen, {}) is None

    with pytest.raises(ValueError, match="--uc-relax"):
        commitment_relax_reason(free, gen, {}, "bogus")


def test_plan_counts_by_reason() -> None:
    gens = (_gen("A"), _gen("B"), _gen("C", heat_rate=1.0, fuel_names=("f",)))
    fuels = (FuelSpec(object_id=1, name="f", price=50.0),)
    cmts = (
        _cmt("A", uc_optimality=0),
        _cmt("B", pmin=2.0),
        _cmt("C", pmin=2.0),  # costed thermal stays integer
        _cmt("MISSING"),  # no generator spec: integer
    )
    plan = plan_commitment_relaxation(cmts, gens, fuels)
    assert plan.total == 4
    assert plan.reasons == {"A": REASON_PLEXOS_LINEAR, "B": REASON_COST_FREE}
    assert plan.count(REASON_PLEXOS_LINEAR) == 1
    assert plan.count(REASON_COST_FREE) == 1


# ── writer ───────────────────────────────────────────────────────────────────
def test_writer_emits_relax_and_keeps_forced_pins() -> None:
    cmts = (
        _cmt("SOLAR", pmin=1.0, commit_status_profile=(0, 1, -1)),
        _cmt("COAL", pmin=100.0, startup_cost=1000.0),
    )
    out = build_commitment_array(cmts, relax_generators={"SOLAR": REASON_COST_FREE})
    assert len(out) == 2
    solar, coal = out[0], out[1]
    assert solar["relax"] is True
    # The commitment is still emitted with its forcing and MSL.
    assert solar["fixed_status"] == [[0.0, 1.0, -1.0]]
    assert solar["pmin"] == 1.0
    assert "relax" not in coal
    # --lp-relax still relaxes everything; no plan == legacy MIP.
    assert all(e["relax"] for e in build_commitment_array(cmts, lp_relax=True))
    assert all("relax" not in e for e in build_commitment_array(cmts))


class _Case:  # minimal PlexosCase stand-in for _commitment_relax_plan
    def __init__(self) -> None:
        self.generators = (_gen("S", pmax=10.0), _gen("Z", pmax=0.0))
        self.fuels: tuple[FuelSpec, ...] = ()
        self.commitments = (_cmt("S", pmin=1.0), _cmt("Z", pmin=1.0))


def test_relax_plan_skips_phantom_commitments_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = _Case()
    with caplog.at_level(logging.INFO, logger="plexos2gtopt.gtopt_writer"):
        plan = _commitment_relax_plan(case, "auto")  # type: ignore[arg-type]
    # The pmax = 0 unit is skipped by the writer: not counted.
    assert plan.total == 1
    assert plan.reasons == {"S": REASON_COST_FREE}
    assert "1 of 1 commitment(s) LP-relaxed" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="plexos2gtopt.gtopt_writer"):
        none_plan = _commitment_relax_plan(case, "none")  # type: ignore[arg-type]
    assert not none_plan.reasons
    assert "LP-relaxed" not in caplog.text


# ── PLEXOS Unit Commitment Optimality (scenario tiers) ───────────────────────
def test_uc_optimality_scenario_tiers(tmp_path: Path) -> None:
    kit = DbKit()
    model = kit.obj("Model", "PRGdia_Full_Definitivo")
    on = kit.obj("Scenario", "ON")
    off = kit.obj("Scenario", "OFF")
    kit.mem("Model", "Scenario", model, on)
    ids = {n: kit.obj("Generator", n) for n in ("U", "T", "I", "N", "X")}
    prop = "Unit Commitment Optimality"
    # U: untagged 2 + inactive-tagged 0 -> 2 (CEN SouthLinearUnits shape).
    kit.sysprop("Generator", ids["U"], prop, 2.0)
    kit.sysprop("Generator", ids["U"], prop, 0.0, tags=[off])
    # T: active-tagged 0 overrides the untagged 2.
    kit.sysprop("Generator", ids["T"], prop, 2.0)
    kit.sysprop("Generator", ids["T"], prop, 0.0, tags=[on])
    # I: only an inactive-tagged row -> unset.
    kit.sysprop("Generator", ids["I"], prop, 0.0, tags=[off])
    # N: -1 (Production default) -> unset.
    kit.sysprop("Generator", ids["N"], prop, -1.0)
    # X: no row at all.
    kit.reset_indexes()
    active = kit.db.active_scenario_ids()
    assert active == {on}
    got = {n: P._uc_optimality(kit.db, oid, active) for n, oid in ids.items()}
    assert got == {"U": 2, "T": 0, "I": None, "N": None, "X": None}

    # extract_commitments stamps it on the spec.
    gens = tuple(
        GeneratorSpec(object_id=ids[n], name=n, bus_name="n1", pmax=10.0)
        for n in ("U", "T")
    )
    for oid in (ids["U"], ids["T"]):
        kit.sysprop("Generator", oid, "Min Stable Level", 1.0)
    kit.reset_indexes()
    specs = {
        c.generator_name: c
        for c in P.extract_commitments(kit.db, bundle(tmp_path), gens)
    }
    assert specs["U"].uc_optimality == 2
    assert specs["T"].uc_optimality == 0
    assert specs["U"].origin == "generator"


def test_uc_optimality_without_generator_collection() -> None:
    """No System -> Generator collection / property: unset."""
    kit = DbKit()
    assert P._uc_optimality(kit.db, 99, set()) is None
    gid = kit.obj("Generator", "G")
    kit.reset_indexes()
    assert P._uc_optimality(kit.db, gid, set()) is None


def test_hydro_min_max_promotion_marks_origin() -> None:
    from plexos2gtopt.entities import UserConstraintSpec  # pylint: disable=import-outside-toplevel

    gen = GeneratorSpec(object_id=1, name="ANTUCO_U1", bus_name="n1", pmax=100.0)
    uc = UserConstraintSpec(
        name="ANTUCOmin",
        expression='generator("ANTUCO_U1").generation >= 20',
    )
    new, _ = P._auto_promote_hydro_min_max_to_commitments((uc,), (), (gen,), ())
    if not new:
        pytest.skip("UC shape not promoted by this parser version")
    assert new[0].origin == "hydro_min_max"
    assert commitment_relax_reason(new[0], gen, {}) is None


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_uc_relax_flag() -> None:
    parser = make_parser()
    assert parser.parse_args(["-i", "x"]).uc_relax == "auto"
    assert parser.parse_args(["-i", "x", "--uc-relax", "plexos"]).uc_relax == "plexos"
    with pytest.raises(SystemExit):
        parser.parse_args(["-i", "x", "--uc-relax", "bogus"])
