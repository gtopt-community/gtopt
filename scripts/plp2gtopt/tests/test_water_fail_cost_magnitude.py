"""Integration / magnitude tests for ``--auto-water-fail-cost``.

These tests validate the **magnitude claim** at the heart of the
unified water-shortfall pricing helper: under the legacy paths
``Reservoir.efin_cost`` is orders of magnitude too cheap relative to
the energy-equivalent value of stored water, and ``FlowRight.fail_cost``
is a single hard-coded number for every FR irrespective of the
central's own ``Rendi``.

The new pipeline replaces both with a single principled formula
(see :mod:`gtopt_shared.water_values`) — these tests run plp2gtopt on
the published CEN 2-year PLP case ``plp_2y`` (external case repository,
``GTOPT_CASES_DIR``; see ``tools/get_cases.sh``) and confirm that

* the cascade lost production factor (``lost_pf``) of every reservoir
  matches the reference table below, end to end through ``efin_cost``
  (``anchor × lost_pf × 1e6/3600``) on a copy of the case WITHOUT the
  boundary-cut planos;
* with the planos present, ``efin_cost`` is the boundary-cut lower-bound
  water value read back from the emitted ``boundary_cuts.csv`` (the
  2026-06-16 OVERWRITE rule), in the same ``$/hm³`` unit gtopt loads;
* the legacy path produces values *orders of magnitude smaller*.

The case is skipped when ``GTOPT_CASES_DIR`` does not provide it.

``efin_cost`` is only emitted for a SOFT terminal floor, so every
conversion here passes ``--soft-storage-bounds --no-cuts-govern-terminal``
(the defaults since 2026-07 are PLP's hard ``vol_end >= efin`` bound and
cut-governed terminal volumes, both of which emit no ``efin_cost``).
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gtopt_shared.testing.external_cases import (
    external_case_path,
    missing_case_reason,
)
from gtopt_shared.water_values import WaterValueResolver, cut_lower_bound

# Reference cascade ``lost_pf`` per reservoir on plp_2y, in MW/(m³/s).
#
# ``WaterValueResolver.cascade_lost_pf`` walks ``ser_hid`` (plpcnfce.dat)
# from the reservoir's own central, summing ``max_rendi`` of every
# ``embalse`` (start only) / ``serie`` unit with ``bus > 0``; it skips
# ``pasada`` units and bus-0 transit nodes (``B_*``, irrigation ``Rie*``
# withdrawals) and stops at the next downstream ``embalse``.
# ``max_rendi = max(plpcnfce Rendi, plpcenre.dat efficiency @ vmax)``;
# a "cenre lift" marks a reservoir whose plpcenre curve at full volume
# exceeds its static Rendi.  Derivation (Rendi values from plpcnfce.dat):
#
#   reservoir  | lost_pf  | chain (contributing units)
#   -----------|---------:|----------------------------------------------
#   LMAULE     | 8.770    | LMAULE bus=0 (skip) → LOS_CONDORES 6.50 →
#              |          | ISLA 0.81 → CURILLINQUE 1.01 → LOMA_ALTA 0.45
#              |          | → stop at PEHUENCHE (embalse)
#   CIPRESES   | 5.054    | CIPRESES 2.784 → ISLA 0.81 → CURILLINQUE 1.01
#              |          | → LOMA_ALTA 0.45 → stop at PEHUENCHE
#   ELTORO     | 6.627930 | ELTORO 5.027930 (cenre lift of Rendi 4.80) →
#              |          | ANTUCO 1.60
#   COLBUN     | 1.942115 | COLBUN 1.612115 (cenre lift of 1.53) →
#              |          | MACHICURA 0.33
#   CANUTILLAR | 2.074676 | CANUTILLAR 2.074676 (cenre lift of 2.00)
#   RALCO      | 1.745756 | RALCO 1.745756 (cenre lift of 1.575) → stop at
#              |          | PANGUE (embalse)
#   PEHUENCHE  | 1.780    | PEHUENCHE 1.78 (downstream nodes all bus=0)
#   PANGUE     | 1.230    | PANGUE 0.80 → ANGOSTURA 0.43
#   PILMAIQUEN | 0.905    | PILMAIQUEN 0.27 → RUCATAYO 0.315 → LOS_LAGOS 0.32
#   RAPEL      | 0.640    | RAPEL 0.64 (no downstream unit)
#
# The expected ``efin_cost`` is ``anchor × lost_pf × 1e6/3600`` $/hm³ with
# ``anchor = 0.75·avg(termica gcost) + 0.25·min(falla gcost)`` read back
# from the emitted JSON (≈ 215.11 $/MWh on plp_2y), so the table pins the
# cascade / cenre-lift logic independently of the anchor formula.
_REFERENCE_RESERVOIRS_LOST_PF = {
    "LMAULE": 8.770,
    "CIPRESES": 5.054,
    "ELTORO": 6.627930,
    "COLBUN": 1.942115,
    "CANUTILLAR": 2.074676,
    "RALCO": 1.745756,
    "PEHUENCHE": 1.780,
    "PANGUE": 1.230,
    "PILMAIQUEN": 0.905,
    "RAPEL": 0.640,
}

# Relative tolerance — absorbs the 2-d.p. round applied by
# ``fail_cost`` / ``efin_cost`` and the 6-d.p. table above.  Tight enough
# to catch material logic regressions in the cascade / cenre-lift logic.
_TOL_REL = 1e-3

_PLP_2Y_REL = "chile/medium_long_term/plp_2y/source"

# The boundary-cut planos files; dropped from the case copy used to
# exercise the no-cut (``anchor × lost_pf``) path end to end.
_PLANOS_STEMS = ("plpplem1.dat", "plpplem2.dat", "plpplaem1.dat", "plpplaem2.dat")


def _plp_2y_dir() -> Path | None:
    """Return the plp_2y PLP source directory if it has the required files.

    plp2gtopt reads ``.dat`` or ``.dat.xz`` transparently; either is fine.
    """
    case_dir = external_case_path(_PLP_2Y_REL)
    if case_dir is None or not case_dir.is_dir():
        return None
    for required in ("plpcnfce.dat", "plpcenre.dat", "plpblo.dat"):
        if (
            not (case_dir / required).exists()
            and not (case_dir / f"{required}.xz").exists()
        ):
            return None
    return case_dir


def _copy_without_planos(case_dir: Path, dest: Path) -> Path:
    """Copy *case_dir* to *dest* minus the boundary-cut planos files."""
    dest.mkdir(parents=True, exist_ok=True)
    for src in case_dir.iterdir():
        if not src.is_file():
            continue
        if any(src.name in (stem, f"{stem}.xz") for stem in _PLANOS_STEMS):
            continue
        shutil.copy2(src, dest / src.name)
    return dest


def _convert(case_dir: Path, output_dir: Path, *, extra_flags: list[str]) -> dict:
    """Run plp2gtopt on a PLP case and return the parsed planning JSON.

    Always uses ``-F csv -t 1y --first-scenario --no-check`` to keep the
    run fast and avoid downstream checks that depend on solver plugins,
    plus ``--soft-storage-bounds --no-cuts-govern-terminal`` so the soft,
    priced terminal slack (``efin_cost``) these tests pin is emitted (see
    the module docstring).

    The returned dict carries a ``_output_dir`` key pointing at the
    conversion's output directory so callers can inspect side
    artifacts (``boundary_cuts.csv``, parquet schedules, …).
    """
    cmd = [
        sys.executable,
        "-m",
        "plp2gtopt.main",
        str(case_dir),
        "-o",
        str(output_dir),
        "-F",
        "csv",
        "-t",
        "1y",
        "--first-scenario",
        "--no-check",
        "--soft-storage-bounds",
        "--no-cuts-govern-terminal",
        *extra_flags,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert result.returncode == 0, (
        f"plp2gtopt failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Filter out auxiliary fragments that share the .json extension.
    _aux = {
        "laja_water_rights.json",
        "maule_water_rights.json",
        "ror_promoted.json",
        # State snapshot — written at run end by main.py, not part
        # of the converter's planning artefact set.
        "plp2gtopt_state.json",
    }
    json_files = [p for p in output_dir.glob("*.json") if p.name not in _aux]
    assert len(json_files) == 1, f"Expected 1 main JSON file, got {json_files}"
    with open(json_files[0], encoding="utf-8") as f:
        planning = json.load(f)
    planning["_output_dir"] = str(output_dir)
    return planning


def _read_boundary_cut_values(output_dir: Path, *, pre_divided: bool) -> dict:
    """Compute per-reservoir cut **lower-bound** water value from the CSV.

    Mirrors :meth:`PlanosParser.lower_bound_water_value_by_reservoir`
    (with ``num_scenarios=None``) from the emitted ``boundary_cuts.csv``
    — the file gtopt actually loads — so the test pins that the
    ``efin_cost`` overwrite and the loaded cuts share one ``$/hm³``
    unit.  The lower bound is ``-max(signed coeff)`` positive-floored
    (see :func:`gtopt_shared.water_values.cut_lower_bound`).

    Under the legacy ``combined`` / ``separated`` modes the CSV
    gradients are pre-divided by ``NVarPhi`` (``pre_divided=True``) and
    are multiplied back by ``NVarPhi`` = ``max(scene)``.  The default
    ``phi_expectation`` mode writes the CSV raw (``pre_divided=False``).
    """
    csv_path = output_dir / "boundary_cuts.csv"
    if not csv_path.exists():
        return {}
    signed: dict[str, list[float]] = {}
    max_scene = 1
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Reservoir columns are everything after the fixed header fields.
        cols = [
            c
            for c in reader.fieldnames or []
            if c not in ("name", "iteration", "scene", "rhs")
        ]
        for row in reader:
            try:
                max_scene = max(max_scene, int(row.get("scene", 1)))
            except (TypeError, ValueError):
                pass
            for c in cols:
                try:
                    v = float(row[c])
                except (TypeError, ValueError, KeyError):
                    continue
                if v != 0.0:
                    signed.setdefault(c, []).append(v)
    nvarphi = float(max_scene if pre_divided else 1)
    out: dict[str, float] = {}
    for c, vals in signed.items():
        lb = cut_lower_bound([v * nvarphi for v in vals])
        if lb is not None:
            out[c] = lb
    return out


def _resolved_anchor(planning: dict) -> float:
    """Recover the anchor (in ``$/MWh``) from the emitted JSON.

    Every turbine-linked generator with ``pmin > 0`` keeps its ``pmin``
    as a soft floor priced at ``pmin_fcost == anchor`` exactly, so the
    anchor is read directly off the first positive ``pmin_fcost``.
    """
    for gen in planning.get("system", {}).get("generator_array", []):
        pmin_fcost = gen.get("pmin_fcost")
        if isinstance(pmin_fcost, (int, float)) and pmin_fcost > 0:
            return float(pmin_fcost)
    raise AssertionError("No generator carries a positive pmin_fcost (anchor)")


def _reservoir(planning: dict, name: str) -> dict:
    reservoirs = planning.get("system", {}).get("reservoir_array", [])
    match = next((r for r in reservoirs if r.get("name") == name), None)
    assert match is not None, f"Reservoir {name} not found in planning"
    return match


@pytest.mark.integration
@pytest.mark.skipif(_plp_2y_dir() is None, reason=missing_case_reason(_PLP_2Y_REL))
class TestAutoWaterFailCostMagnitude:
    """End-to-end magnitude tests on the plp_2y case.

    Runs plp2gtopt three times — ``--auto-water-fail-cost`` with and
    without the boundary-cut planos, and the legacy path — and verifies
    the reference table, the cut overwrite and the legacy magnitude gap.
    """

    @pytest.fixture(scope="class")
    def planning_auto(self, tmp_path_factory):
        case_dir = _plp_2y_dir()
        assert case_dir is not None  # gated by skipif
        out = tmp_path_factory.mktemp("plp2y_auto")
        return _convert(case_dir, out, extra_flags=["--auto-water-fail-cost"])

    @pytest.fixture(scope="class")
    def planning_auto_nocuts(self, tmp_path_factory):
        case_dir = _plp_2y_dir()
        assert case_dir is not None
        src = _copy_without_planos(case_dir, tmp_path_factory.mktemp("plp2y_src"))
        out = tmp_path_factory.mktemp("plp2y_auto_nocuts")
        return _convert(src, out, extra_flags=["--auto-water-fail-cost"])

    @pytest.fixture(scope="class")
    def planning_legacy(self, tmp_path_factory):
        case_dir = _plp_2y_dir()
        assert case_dir is not None
        out = tmp_path_factory.mktemp("plp2y_legacy")
        return _convert(case_dir, out, extra_flags=["--no-auto-water-fail-cost"])

    # ------------------------------------------------------------------
    # Auto pipeline without cuts — cascade lost_pf reference table
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "name,lost_pf",
        list(_REFERENCE_RESERVOIRS_LOST_PF.items()),
    )
    def test_reservoir_efin_cost_matches_reference(
        self, planning_auto_nocuts, name, lost_pf
    ):
        """No boundary cuts ⇒ ``efin_cost = anchor × lost_pf × 1e6/3600``."""
        assert not (
            Path(planning_auto_nocuts["_output_dir"]) / "boundary_cuts.csv"
        ).exists()
        anchor = _resolved_anchor(planning_auto_nocuts)
        expected = anchor * lost_pf * 1e6 / 3600.0
        actual = _reservoir(planning_auto_nocuts, name).get("efin_cost")
        assert actual is not None, f"Reservoir {name} has no efin_cost"
        assert actual == pytest.approx(expected, rel=_TOL_REL), (
            f"{name}: efin_cost={actual} vs anchor×lost_pf={expected} "
            f"(anchor={anchor:.2f}, lost_pf={lost_pf})"
        )

    # ------------------------------------------------------------------
    # Auto pipeline with cuts — boundary-cut overwrite
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("name", list(_REFERENCE_RESERVOIRS_LOST_PF))
    def test_reservoir_efin_cost_from_boundary_cuts(self, planning_auto, name):
        """Cut-covered reservoir ⇒ ``efin_cost`` is the cut lower bound.

        Semantics (2026-06-16): the boundary-cut value **OVERWRITES** the
        anchor-derived formula.  It is the cut lower-bound water value of
        the emitted ``boundary_cuts.csv`` (``$/hm³``, the unit gtopt
        loads), un-discounted by the last stage's ``discount_factor``.
        """
        fcf = (planning_auto["system"].get("future_cost_array") or [{}])[0]
        cut_values = _read_boundary_cut_values(
            Path(planning_auto["_output_dir"]),
            pre_divided=fcf.get("mode") in ("combined", "separated"),
        )
        assert name in cut_values, f"{name} not covered by the plp_2y cuts"
        stages = planning_auto["simulation"]["stage_array"]
        last_df = float(stages[-1].get("discount_factor", 1.0)) or 1.0
        expected = cut_values[name] / last_df
        actual = _reservoir(planning_auto, name).get("efin_cost")
        assert actual is not None, f"Reservoir {name} has no efin_cost"
        assert actual == pytest.approx(expected, rel=_TOL_REL), (
            f"{name}: efin_cost={actual} vs boundary-cut value {expected} "
            f"(csv lower bound {cut_values[name]} / discount {last_df})"
        )

    def test_hydro_pmin_kept_as_soft_floor(self, planning_auto):
        """Hydro ``pmin`` is a soft floor priced at the water-value anchor.

        The legacy path converted generator ``pmin`` into a
        ``*_pmin_as_flow_right`` FlowRight; the current pipeline keeps
        ``pmin`` on the generator and adds ``pmin_fcost == anchor``
        ($/MWh) so the floor is soft.  Assert every priced generator
        carries the same anchor and no ``*_pmin_as_flow_right`` FlowRight
        survives anywhere.
        """
        anchor = _resolved_anchor(planning_auto)
        gens = planning_auto.get("system", {}).get("generator_array", [])
        priced = [
            g
            for g in gens
            if isinstance(g.get("pmin_fcost"), (int, float)) and g.get("pmin_fcost") > 0
        ]
        assert priced, "No generator carries a positive pmin_fcost"
        for gen in priced:
            assert gen["pmin_fcost"] == pytest.approx(anchor, rel=_TOL_REL), (
                f"{gen.get('name')}: pmin_fcost={gen['pmin_fcost']} != anchor={anchor}"
            )

        frs = planning_auto.get("system", {}).get("flow_right_array", [])
        leftover = [
            fr["name"] for fr in frs if "_pmin_as_flow_right" in fr.get("name", "")
        ]
        assert not leftover, f"Unexpected gen-pmin FlowRights survived: {leftover}"

    # ------------------------------------------------------------------
    # Legacy path — magnitude justification
    # ------------------------------------------------------------------
    def test_legacy_efin_cost_is_orders_of_magnitude_smaller(
        self, planning_legacy, planning_auto_nocuts
    ):
        """Legacy ``efin_cost`` (vrebemb / CVert, capped at 500 $/hm³ by
        ``--vert-cost-cap``) sits far below the energy-equivalent value of
        stored water that the auto pipeline produces.

        This is the regression the helper closes: under the legacy
        cascade an LP can manufacture fictitious water through the slack
        at a deep discount relative to the demand-fail penalty.
        """
        reservoirs = planning_legacy.get("system", {}).get("reservoir_array", [])
        any_legacy = [r.get("efin_cost") for r in reservoirs if r.get("efin_cost")]
        assert any_legacy, "Legacy run produced no efin_cost values"
        max_legacy = max(any_legacy)
        assert max_legacy < 10_000.0, (
            f"Legacy max efin_cost {max_legacy} unexpectedly large; "
            "auto-pipeline magnitude justification may be invalid"
        )
        # LMAULE's auto value: anchor (≈215 $/MWh) × lost_pf 8.77 × 1e6/3600
        # ≈ 524 k $/hm³ — three orders of magnitude above the legacy cap.
        lmaule_auto = _reservoir(planning_auto_nocuts, "LMAULE").get("efin_cost")
        assert isinstance(lmaule_auto, (int, float))
        ratio = lmaule_auto / max(max_legacy, 1e-9)
        assert ratio > 100.0, (
            f"auto/legacy ratio = {ratio:.1f}× — expected >100× discount"
        )


@pytest.mark.integration
def test_reference_table_matches_resolver_on_plp_2y():
    """The reference ``lost_pf`` table is what the resolver computes on
    the parsed plp_2y data (keeps the derivation table honest)."""
    case_dir = _plp_2y_dir()
    if case_dir is None:
        pytest.skip(missing_case_reason(_PLP_2Y_REL))
    from plp2gtopt.plp_parser import PLPParser  # noqa: PLC0415

    parser = PLPParser({"input_dir": case_dir})
    parser.parse_all()
    centrals = parser.parsed_data["central_parser"]
    resolver = WaterValueResolver(
        central_parser=centrals,
        cenre_parser=parser.parsed_data.get("cenre_parser"),
        options={"auto_water_fail_cost": True},
    )
    numbers = {c["name"]: c["number"] for c in centrals.centrals}
    for name, lost_pf in _REFERENCE_RESERVOIRS_LOST_PF.items():
        assert resolver.cascade_lost_pf(numbers[name]) == pytest.approx(
            lost_pf, rel=1e-6
        ), name


# ---------------------------------------------------------------------------
# Synthetic threshold parametrisation (Python-only — no PLP case required)
# ---------------------------------------------------------------------------


# These tests verify the *formula* of the auto pipeline against the
# threshold values quoted in the design note, without needing a full
# Planning JSON or solver plugin to be available.  They duplicate
# :mod:`test_water_value` checks at coarser granularity — the unit
# tests cover correctness on inputs; here we cover the thresholds the
# design note calls out as governing slack satisfaction in the LP.


class _CP:  # pragma: no cover - tiny test helper
    def __init__(self, centrals):
        self.centrals = list(centrals)


def _cascade10_resolver(*, anchor_override: float | None = None):
    """Build a small resolver with a production-scale cascade.

    Single embalse with cascade ending at one bus>0 turbine of rendi
    10.0 — matches the design note's synthetic cascade case with
    cascade_rendi = 10.0.
    """
    centrals = [
        # Reservoir (transit, bus=0).
        {
            "number": 1,
            "name": "TEST_RES",
            "type": "embalse",
            "bus": 0,
            "ser_hid": 2,
            "efficiency": 0.0,
            "emax": 100.0,
        },
        # Single turbine downstream.
        {
            "number": 2,
            "name": "TEST_TURB",
            "type": "serie",
            "bus": 1,
            "ser_hid": 0,
            "efficiency": 10.0,
            "emax": 0.0,
        },
        {
            "number": 99,
            "name": "FALLA_T4",
            "type": "falla",
            "bus": 0,
            "ser_hid": 0,
            "efficiency": 0.0,
            "emax": 0.0,
            "gcost": 568.4,
        },
    ]
    options: dict = {"auto_water_fail_cost": True}
    if anchor_override is not None:
        options["water_fail_cost"] = anchor_override
    return WaterValueResolver(
        central_parser=_CP(centrals),
        cenre_parser=None,
        options=options,
    )


@pytest.mark.parametrize(
    "anchor_override,expected_efin",
    [
        # Below threshold: anchor × cascade × 277.78 < demand_fail_cost ×
        # cascade × 277.78.  The 360 anchor produces ~1.0M $/hm³ which is
        # below the 1.578M $/hm³ break-even — the LP would manufacture
        # fictitious water via the slack.
        pytest.param(360.0, 1_000_000.0, id="below_threshold"),
        # Auto-derived value: 569.4 × 10 × 277.78 ≈ 1.58M $/hm³ — strictly
        # above the 1.578M break-even (max(falla.gcost) × cascade × 277.78).
        pytest.param(569.4, 1_581_667.0, id="auto_proposed"),
        # User-quoted threshold: 568 × 10 × 277.78 ≈ 1.578M (no +1).
        pytest.param(568.0, 1_577_777.0, id="exact_threshold"),
        # Way above — slack always satisfied.
        pytest.param(1000.0, 2_777_777.0, id="above_threshold"),
        # Zero anchor: any slack is "free", LP fully violates.
        pytest.param(0.0, 0.0, id="zero_cost"),
    ],
)
def test_efin_cost_threshold_formula(anchor_override, expected_efin):
    """Sweep the anchor and verify ``efin_cost(cascade=10)`` follows the
    documented formula (anchor × 10 × 1e6/3600).

    The qualitative claim — that anchors above ``demand_fail_cost ×
    cascade × 1e6/3600`` price-dominate the slack and below it the LP
    manufactures fictitious water — is documented in the helper module
    and checked end-to-end by the magnitude class above when the PLP
    case is on disk.
    """
    resolver = _cascade10_resolver(anchor_override=anchor_override)
    assert resolver.water_fail_cost == pytest.approx(anchor_override, abs=0.01)
    cost = resolver.efin_cost(10.0)
    assert cost == pytest.approx(expected_efin, abs=200.0)


@pytest.mark.parametrize(
    "anchor_override,expected_fail_cost",
    [
        pytest.param(300.0, 3_000.0, id="below_threshold"),
        pytest.param(569.4, 5_694.0, id="auto_proposed"),
        pytest.param(568.0, 5_680.0, id="exact_threshold"),
        pytest.param(1000.0, 10_000.0, id="above_threshold"),
        pytest.param(0.0, 0.0, id="zero_cost"),
    ],
)
def test_fail_cost_threshold_formula(anchor_override, expected_fail_cost):
    """Symmetric sweep for ``FlowRight.fail_cost = anchor × own_rendi``."""
    resolver = _cascade10_resolver(anchor_override=anchor_override)
    cost = resolver.fail_cost(10.0)  # rendi = 10 to mirror the cascade test
    assert cost == pytest.approx(expected_fail_cost, abs=1.0)
