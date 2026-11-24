# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Uplift of published non-convex pricing examples.

Two kinds of checks:

* **Published prices in, published uplift out** (no solver): the dispatch
  and the prices of each paper are written as a synthetic results
  directory and ``compute_uplift`` must reproduce the paper's uplift.  This
  validates the tool's uplift definition (make-whole + lost opportunity,
  i.e. Gribik-Hogan-Pope's ``π*(p) − π(p, y)``) independently of gtopt's
  pricing LP.
* **gtopt goldens** (``cases/uc_pricing_lit_*/output``): the uplift of the
  prices gtopt actually produced in each mode.

Sources (parameters typed into ``cases/uc_pricing_lit_*``; see
``test/source/test_pricing_literature.cpp`` for the price checks):

* Hogan & Ring, "On Minimum-Uplift Pricing for Electricity Markets", 2003,
  adapted Scarf example table —
  https://lmpmarketdesign.com/papers/Hogan_Ring_minuplift_031903.pdf ;
  O'Neill et al., EJOR 164(1):269-285, 2005, doi:10.1016/j.ejor.2003.12.011.
* Hua & Baldick, IEEE TPWRS 32(5):3814-3823, 2017,
  doi:10.1109/TPWRS.2016.2637718 (arXiv:1605.05002), Tables 2 and 5.
* PJM, "Proposed Enhancements to Energy Price Formation", 15 Nov 2017,
  Appendix B, Tables 3-4 and 6-7.
* Eldridge, O'Neill & Hobbs, EPRG WP 1840, 2018, Appendix A.2.

Expected differences pinned here (never faked as agreement):

* The self-schedule DP ignores ramp limits (``_selfsched`` docstring), so
  on Hua & Baldick Example 2 it overstates unit 2's lost opportunity: a
  price-taking unit 2 "could" start in hour 3 at 100 MW, which its 60 MW
  start-up ramp forbids.
* PJM's per-unit uplift of the flexible units G1 / G3 comes from PJM's
  settlement model (profit maximisation subject to the system
  constraints), not from a per-unit lost-opportunity cost; G1 / G3 carry
  no commitment, so the tool does not report them.
* Hua & Baldick's CHPq pays only "qualified" (dispatched) units; the tool
  reports every commitment, so unit 2's lost opportunity at the CHPq price
  shows up (2000 $) although the paper excludes it by design.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from gtopt_pricing_uplift import compute_uplift

from .conftest import CASES

# ── synthetic results directories ─────────────────────────────────────────


def _load_case(name: str) -> dict[str, Any]:
    path = CASES / name / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _write_long(path: Path, rows: dict[tuple[int, int], float]) -> None:
    """One output stream in the long layout, keyed by (block, uid)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['"scenario","stage","block","uid","value"']
    lines += [f"1,1,{b},{u},{v!r}" for (b, u), v in sorted(rows.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _results(
    root: Path,
    planning: dict[str, Any],
    *,
    prices: dict[tuple[int, int], float],
    gen: dict[tuple[int, int], float],
    status: dict[tuple[int, int], float],
) -> Path:
    """Results dir with prices / dispatch / commitment (start-ups derived)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "planning.json").write_text(json.dumps(planning), encoding="utf-8")
    _write_long(root / "Bus" / "balance_dual.csv", prices)
    _write_long(root / "Generator" / "generation_sol.csv", gen)
    _write_long(root / "Commitment" / "status_sol.csv", status)
    return root


def _by_unit(rep: Any) -> dict[str, Any]:
    return {u.generator: u for u in rep.units}


# ── Hua & Baldick (2017) Example 1 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("price", "u1", "u2"),
    [
        (50.0, 100.0, 1900.0),  # LMP
        (12.0, 1430.0, 0.0),  # CHP
    ],
)
def test_hua_baldick_ex1_published(
    tmp_path: Path, price: float, u1: float, u2: float
) -> None:
    """Table 2: unit 1 at 35 MW (start-up 100), unit 2 off."""
    rep = compute_uplift(
        _results(
            tmp_path,
            _load_case("uc_pricing_lit_hb1_fixed"),
            prices={(1, 1): price},
            gen={(1, 1): 35.0, (1, 2): 0.0},
            status={(1, 1): 1.0, (1, 2): 0.0},
        )
    )
    by = _by_unit(rep)
    assert by["unit1"].total == pytest.approx(u1)
    assert by["unit2"].total == pytest.approx(u2)
    assert rep.total == pytest.approx(u1 + u2)


def test_hua_baldick_ex1_chpq_unit2_not_qualified(tmp_path: Path) -> None:
    """CHPq π = 52: U1 = 30 matches; unit 2 is excluded by the paper."""
    rep = compute_uplift(
        _results(
            tmp_path,
            _load_case("uc_pricing_lit_hb1_fast_start"),
            prices={(1, 1): 52.0},
            gen={(1, 1): 35.0, (1, 2): 0.0},
            status={(1, 1): 1.0, (1, 2): 0.0},
        )
    )
    by = _by_unit(rep)
    assert by["unit1"].make_whole == pytest.approx(30.0)
    assert by["unit1"].lost_opportunity == pytest.approx(0.0)
    # Expected difference: 50·(52 − 10) − 100 the paper does not pay.
    assert by["unit2"].lost_opportunity == pytest.approx(2000.0)


# ── Hua & Baldick (2017) Example 2 (ramp-coupled) ─────────────────────────

_HB2_GEN = {
    (1, 1): 70.0,
    (2, 1): 40.0,
    (3, 1): 70.0,
    (1, 2): 0.0,
    (2, 2): 60.0,
    (3, 2): 100.0,
}
_HB2_STATUS = {
    (1, 1): 1.0,
    (2, 1): 1.0,
    (3, 1): 1.0,
    (1, 2): 0.0,
    (2, 2): 1.0,
    (3, 2): 1.0,
}


@pytest.mark.parametrize(
    ("pi3", "u1", "u2_paper", "u2_tool"),
    [
        # LMP: unit 2's 560 make-whole; ramp-free DP agrees (off is best).
        (60.0, 0.0, 560.0, 560.0),
        # aCHP1: U2 = 160 make-whole; the DP adds 200 of "LOC" from a
        # ramp-infeasible start in hour 3 at 100 MW (800 − 600).
        (64.0, 120.0, 160.0, 360.0),
        # CHP: U2 = 0; the same ramp-infeasible self-schedule earns 360.
        (65.6, 168.0, 0.0, 360.0),
    ],
)
def test_hua_baldick_ex2_published(
    tmp_path: Path, pi3: float, u1: float, u2_paper: float, u2_tool: float
) -> None:
    """Table 5: prices (60, 60, π3) on the Table 4 dispatch."""
    rep = compute_uplift(
        _results(
            tmp_path,
            _load_case("uc_pricing_lit_hb2_fixed"),
            prices={(1, 1): 60.0, (2, 1): 60.0, (3, 1): pi3},
            gen=_HB2_GEN,
            status=_HB2_STATUS,
        )
    )
    by = _by_unit(rep)
    assert by["unit1"].total == pytest.approx(u1)  # match
    # Make-whole always matches; only the LOC part overshoots.
    assert by["unit2"].make_whole == pytest.approx(min(u2_paper, 560.0))
    assert by["unit2"].total == pytest.approx(u2_tool)
    if u2_tool != u2_paper:
        assert by["unit2"].total > u2_paper  # conservative (high) estimate


# ── PJM (2017) Appendix B ─────────────────────────────────────────────────


def _pjm_planning(example: int) -> dict[str, Any]:
    plan = _load_case("uc_pricing_lit_pjm1_fixed")
    if example == 2:
        plan = copy.deepcopy(plan)
        gens = {g["name"]: g for g in plan["system"]["generator_array"]}
        gens["g2"]["gcost"] = 10  # Table 5: both block units at 10 $/MWh
        gens["g4"]["gcost"] = 10
    return plan


@pytest.mark.parametrize(
    ("example", "lmp2", "lmp3", "g4_on", "g2_uplift", "g4_profit"),
    [
        (1, 50.0, 32.5, False, 2600.0, 0.0),  # Table 3 restricted
        (1, 76.0, 35.0, False, 0.0, 0.0),  # Table 4 integer relaxation
        (2, -15.0, 30.0, True, 2600.0, 1900.0),  # Table 6 restricted
        (2, 11.0, 40.4, True, 0.0, 2940.0),  # Table 7 integer relaxation
    ],
)
def test_pjm_block_units_published(
    tmp_path: Path,
    example: int,
    lmp2: float,
    lmp3: float,
    g4_on: bool,
    g2_uplift: float,
    g4_profit: float,
) -> None:
    """Uplift / net revenue of the block-loaded G2 (bus 2) and G4 (bus 3).

    G2 / G4 uplift and net revenue match PJM's tables; the flexible units'
    uplift (Ex. 1: 619 + 31, Ex. 2: 135 + 541) is outside the tool's scope.
    """
    g4 = 100.0 if g4_on else 0.0
    rep = compute_uplift(
        _results(
            tmp_path,
            _pjm_planning(example),
            prices={(1, 1): 0.0, (1, 2): lmp2, (1, 3): lmp3},
            gen={(1, 2): 100.0, (1, 4): g4},
            status={(1, 1): 1.0, (1, 2): 1.0 if g4_on else 0.0},
        )
    )
    by = _by_unit(rep)
    assert by["g2"].total == pytest.approx(g2_uplift)
    assert by["g4"].total == pytest.approx(0.0)
    assert by["g4"].profit == pytest.approx(g4_profit)


# ── Scarf / Hogan & Ring (2003) minimum-uplift table ──────────────────────

# demand, (Smokestack, High Tech, Med Tech) unit outputs, MinUP uplift.
# The MinUP price is the exact convex-hull price (44/7, 101/16 or 7) —
# the table rounds it to 3 decimals.
_SCARF = [
    (1, [], [1], [], 44 / 7, 25.714),
    (10, [], [7], [3], 44 / 7, 2.143),
    (22, [15], [7], [], 44 / 7, 3.714),
    (40, [16], [7, 7, 7], [3], 101 / 16, 2.438),
    (61, [16, 16, 15], [7, 7], [], 101 / 16, 3.875),
    (88, [16, 16, 16, 16], [7, 7, 7], [3], 101 / 16, 2.438),
    (145, [16] * 6, [7] * 5, [6, 6, 2], 7.0, 0.0),
]


@pytest.mark.parametrize(("demand", "ss", "ht", "mt", "price", "uplift"), _SCARF)
def test_scarf_hogan_ring_min_uplift(
    tmp_path: Path,
    demand: int,
    ss: list[float],
    ht: list[float],
    mt: list[float],
    price: float,
    uplift: float,
) -> None:
    """Every row: total uplift at the MinUP price = the published value."""
    plan = _load_case("uc_pricing_lit_scarf_relaxed")
    plan["system"]["demand_array"][0]["lmax"] = [[float(demand)]]
    gen: dict[tuple[int, int], float] = {}
    status: dict[tuple[int, int], float] = {}
    for offset, outputs in ((0, ss), (6, ht), (11, mt)):
        for i, p in enumerate(outputs):
            gen[(1, offset + i + 1)] = p
            status[(1, offset + i + 1)] = 1.0
    rep = compute_uplift(
        _results(tmp_path, plan, prices={(1, 1): price}, gen=gen, status=status)
    )
    assert rep.total == pytest.approx(uplift, abs=1e-3)


# ── Eldridge, O'Neill & Hobbs (2018) replicated example ───────────────────


def test_eldridge_oneill_hobbs_lost_opportunity(tmp_path: Path) -> None:
    """N < 5: four Gen1 at 25 MW, λ = 25 → the idle Gen1 loses 250 $."""
    system = {
        "bus_array": [{"uid": 1, "name": "b1"}],
        "generator_array": [
            {"uid": i, "name": f"gen1_{i}", "bus": "b1", "pmax": 25, "gcost": 15}
            for i in range(1, 6)
        ],
        "commitment_array": [
            {"uid": i, "name": f"gen1_{i}_uc", "generator": f"gen1_{i}", "pmin": 25}
            for i in range(1, 6)
        ],
    }
    plan = {
        "options": {"model_options": {"pricing_mode": "fixed"}},
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1}],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": system,
    }
    on = {(1, i): (25.0 if i < 5 else 0.0) for i in range(1, 6)}
    status = {(1, i): (1.0 if i < 5 else 0.0) for i in range(1, 6)}
    fixed = compute_uplift(
        _results(tmp_path / "lmp", plan, prices={(1, 1): 25.0}, gen=on, status=status)
    )
    assert fixed.lost_opportunity == pytest.approx(250.0)  # published
    assert fixed.make_whole == pytest.approx(0.0)
    # Derived (aCHP not tabulated): λ = 15 makes Gen1 indifferent.
    relaxed = compute_uplift(
        _results(tmp_path / "chp", plan, prices={(1, 1): 15.0}, gen=on, status=status)
    )
    assert relaxed.total == pytest.approx(0.0)


# ── gtopt goldens (cases/uc_pricing_lit_*) ────────────────────────────────


def _golden(name: str) -> Any:
    case = CASES / name
    return compute_uplift(case / "output", planning=case / f"{name}.json")


@pytest.mark.parametrize(
    ("case", "mode", "total"),
    [
        # Scarf demand 61: IP price 3 → make-whole = Σ start-up prices
        # 3·53 + 2·23 = 205 (Hogan & Ring "IP uplift"); convex hull 6.3125 →
        # 3.875 (Hogan & Ring MinUP uplift).
        ("uc_pricing_lit_scarf_fixed", "fixed", 205.0),
        ("uc_pricing_lit_scarf_relaxed", "relaxed", 3.875),
        # Hua & Baldick Ex. 1: LMP 100 + 1900, CHP 1430, CHPq 30 (+2000
        # for the non-qualified unit 2, see module docstring).
        ("uc_pricing_lit_hb1_fixed", "fixed", 2000.0),
        ("uc_pricing_lit_hb1_relaxed", "relaxed", 1430.0),
        ("uc_pricing_lit_hb1_fast_start", "fast_start", 2030.0),
        # Hua & Baldick Ex. 2: LMP → unit 2's 560 make-whole.
        ("uc_pricing_lit_hb2_fixed", "fixed", 560.0),
        # PJM Ex. 1: restricted → G2 make-whole 2600; fast-start → 0.
        ("uc_pricing_lit_pjm1_fixed", "fixed", 2600.0),
        ("uc_pricing_lit_pjm1_fast_start", "fast_start", 0.0),
    ],
)
def test_goldens_literature_uplift(case: str, mode: str, total: float) -> None:
    rep = _golden(case)
    assert rep.pricing_mode == mode
    assert rep.pricing_fallbacks == 0
    assert rep.total == pytest.approx(total, abs=1e-6)


def test_goldens_hb2_relaxed_between_lmp_and_chp() -> None:
    """gtopt's relaxed hour-3 price lies between LMP (60) and aCHP1 (64)."""
    rep = _golden("uc_pricing_lit_hb2_relaxed")
    assert rep.prices.ref_max is not None
    assert 60.0 < rep.prices.ref_max < 64.0
