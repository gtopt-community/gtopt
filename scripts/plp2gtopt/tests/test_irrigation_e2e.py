# -*- coding: utf-8 -*-

"""End-to-end tests of the Laja / Maule irrigation agreements.

The real chain, no shortcuts::

    PLP .dat fixture (scripts/cases/plp_min_irrigation)
      -> plp2gtopt --expand-water-rights   (gtopt_expand in-process)
      -> gtopt (strict PAMPL resolver: every reference must resolve)
      -> solve -> assertions on the written results

The fixture is a synthetic 12-month / 2-hydrology system with the
smallest topology carrying both agreements (``plplajam.dat`` and
``plpmaulen.dat`` are the CEN plp_2y files verbatim).  Expected values
are hand-computed from those files and cite the PLP routine they encode
(CEN65 ``genpdlajam.f`` / ``genpdmaulen.f`` / ``genpdmaule.f``).

Skipped when no gtopt binary is found (``GTOPT_BIN``, build dir, PATH).
The LP solver follows ``GTOPT_SOLVER`` / gtopt's auto-detection.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).resolve().parents[2] / "cases" / "plp_min_irrigation"
_TOL = 1e-4

# Hydrological stage order of the fixture (plpeta months 1..12 = Apr..Mar).
_NOV, _DEC = 8, 9

# Laja zone rule for the riego rights incl. the +30 hm3 mixed transfer
# above the first cushion — plplajam.dat: base 570, factors
# 0 / 0.40 / 0.40 / 0.25 over cushions 1200 / 170 / 530 / 3682 hm3,
# DerMixtoBase 30 with FMixto = 1 / 0 / 0 / 0 (genpdlajam.f:624-649,
# zones half-open: VolUtil - start < width, genpdlajam.f:578-595).
_LAJA_RIEGO_CAP = 5000.0


def _laja_riego_rule(volume: float, base: float = 570.0) -> float:
    if volume < 1200.0:
        return base + 0.0 * volume + 30.0 * (1.0 - 1.0)
    if volume < 1370.0:
        return base + 0.40 * (volume - 1200.0) + 30.0
    if volume < 1900.0:
        return base + 68.0 + 0.40 * (volume - 1370.0) + 30.0
    return base + 68.0 + 212.0 + 0.25 * (volume - 1900.0) + 30.0


# Res. 105 district shares (plpmaulen.dat "Porcentaje de retiro",
# genpdmaulen.f entregas de riego): retiro_k = PRet_k * qa105.
_RES105_SHARES = {
    "CMNA": 0.1266,
    "CMNB": 0.147,
    "Maitenes": 0.1049,
    "MauleSur": 0.1197,
    "Melado": 0.1265,
    "Sur123SCDZ": 0.3427,
    "MolinosOtros": 0.0326,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_gtopt() -> str | None:
    # Reuse the shared locator from conftest (GTOPT_BIN / build / PATH).
    from plp2gtopt.tests.conftest import (  # pylint: disable=import-outside-toplevel
        _find_gtopt_binary,
    )

    return _find_gtopt_binary()


@pytest.fixture(scope="module", name="gtopt")
def _gtopt_fixture() -> str:
    binary = _find_gtopt()
    if binary is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN or build gtopt)")
    return binary


def _convert(
    dst: Path,
    variant: str,
    *,
    method: str = "monolithic",
    patch: Callable[[Path], None] | None = None,
) -> Path:
    """Copy the fixture, drop one agreement for single variants, convert."""
    src = dst / "plp"
    src.mkdir(parents=True)
    for dat in _FIXTURE.glob("*.dat"):
        shutil.copy(dat, src / dat.name)
    if variant == "laja":
        (src / "plpmaulen.dat").unlink()
    elif variant == "maule":
        (src / "plplajam.dat").unlink()
    if patch is not None:
        patch(src)
    out = dst / "case"
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "plp2gtopt.main",
            str(src),
            "-o",
            str(out),
            "-M",
            method,
            "--expand-water-rights",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, f"plp2gtopt failed:\n{res.stdout}\n{res.stderr}"
    return out


def _gtopt_logs(case: Path) -> str:
    chunks = []
    for log in sorted((case / "results" / "logs").glob("gtopt_*.log")):
        chunks.append(log.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _run_gtopt(gtopt: str, case: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [gtopt, f"{case.name}.json", *extra],
        cwd=case,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _solve(gtopt: str, case: Path, *extra: str) -> "_Results":
    res = _run_gtopt(gtopt, case, *extra)
    assert res.returncode == 0, (
        f"gtopt failed (rc={res.returncode}):\n{res.stdout}\n{res.stderr}\n"
        f"{_gtopt_logs(case)[-4000:]}"
    )
    return _Results(case)


class _Results:
    """Name-keyed access to the parquet results of one solved case."""

    def __init__(self, case: Path):
        self.case = case
        self.root = case / "results"
        planning = json.loads((self.root / "planning.json").read_text("utf-8"))
        self.system: dict[str, Any] = planning["system"]
        self.stage_month = {
            s["uid"]: s.get("month") for s in planning["simulation"]["stage_array"]
        }
        self.scenarios = sorted(
            s["uid"] for s in planning["simulation"]["scenario_array"]
        )
        self.stages = sorted(self.stage_month)

    def frame(self, cls: str, field: str, array: str) -> pd.DataFrame:
        names = {e["uid"]: e["name"] for e in self.system.get(array, [])}
        path = self.root / cls / f"{field}.parquet"
        if not path.exists():
            return pd.DataFrame(columns=["scenario", "stage", "uid", "value", "name"])
        df = pd.read_parquet(path)
        df["name"] = df["uid"].map(names)
        return df

    def values(self, cls: str, field: str, array: str, name: str) -> pd.Series:
        """Stage-indexed value per (scenario, stage); missing (zero) -> 0."""
        df = self.frame(cls, field, array)
        df = df[df["name"] == name]
        full = pd.MultiIndex.from_product(
            [self.scenarios, self.stages], names=["scenario", "stage"]
        )
        return (
            df.groupby(["scenario", "stage"])["value"]
            .sum()
            .reindex(full, fill_value=0.0)
            .astype(float)
        )

    def flow_right(self, name: str) -> pd.Series:
        return self.values("FlowRight", "flow_sol", "flow_right_array", name)

    def turbine(self, name: str) -> pd.Series:
        return self.values("Turbine", "flow_sol", "turbine_array", name)

    def reservoir_efin(self, name: str) -> pd.Series:
        return self.values("Reservoir", "efin_sol", "reservoir_array", name)

    def volume_right(self, field: str, name: str) -> pd.Series:
        return self.values("VolumeRight", field, "volume_right_array", name)


def _assert_series_close(lhs: pd.Series, rhs: pd.Series, what: str) -> None:
    diff = (lhs - rhs).abs()
    assert diff.max() <= _TOL * max(1.0, rhs.abs().max()), (
        f"{what}: max |lhs-rhs| = {diff.max():.6g}\n"
        f"{pd.concat([lhs, rhs], axis=1, keys=['lhs', 'rhs'])}"
    )


def _pampl_constraints(case: Path) -> list[str]:
    names: list[str] = []
    for pampl in sorted(case.glob("*.pampl")):
        names += re.findall(
            r"^constraint\s+(\w+)", pampl.read_text("utf-8"), flags=re.M
        )
    return names


# ---------------------------------------------------------------------------
# Solved cases (module scope: each variant converts + solves once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", name="solved")
def _solved_fixture(gtopt, tmp_path_factory) -> dict[str, _Results]:
    cache: dict[str, _Results] = {}
    for variant in ("both", "laja", "maule"):
        case = _convert(tmp_path_factory.mktemp(f"irr_{variant}"), variant)
        cache[variant] = _solve(gtopt, case)
    return cache


# ---------------------------------------------------------------------------
# Common: strict PAMPL resolution, optimality, sign invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["both", "laja", "maule"])
class TestChainCommon:
    """Every variant converts, resolves strictly and solves to optimality."""

    def test_optimal(self, solved, variant):
        status = pd.read_csv(solved[variant].root / "solution.csv")
        assert (status["status_name"] == "optimal").all(), status

    def test_expected_agreements_present(self, solved, variant):
        pampls = {p.name for p in solved[variant].case.glob("*.pampl")}
        expected = {"both": {"laja.pampl", "maule.pampl"}}.get(
            variant, {f"{variant}.pampl"}
        )
        assert pampls == expected

    def test_every_pampl_constraint_is_assembled(self, gtopt, solved, variant):
        """Each PAMPL constraint becomes LP rows (strict resolver, no drop)."""
        case = solved[variant].case
        lp_stem = case / "e2e_model"
        res = _run_gtopt(
            gtopt,
            case,
            "--lp-only",
            "--lp-file",
            str(lp_stem),
            "--lp-compression",
            "none",
        )
        assert res.returncode == 0, res.stdout + res.stderr
        lp_text = "\n".join(
            p.read_text("utf-8", errors="replace") for p in case.glob("e2e_model*.lp")
        ).lower()
        missing = [
            n
            for n in _pampl_constraints(case)
            if f"{n.lower()}_constraint" not in lp_text
        ]
        assert not missing, f"PAMPL constraints without LP rows: {missing}"

    def test_no_negative_water_values(self, solved, variant):
        """Agreements must not make storage look worthless-or-worse."""
        wv = solved[variant].frame("Reservoir", "water_value_dual", "reservoir_array")
        assert not wv.empty
        assert wv["value"].min() >= -_TOL, wv.nsmallest(5, "value")

    def test_ledger_duals_non_negative(self, solved, variant):
        """Remaining-rights ledgers price as resources (dual >= 0)."""
        dual = solved[variant].frame("VolumeRight", "energy_dual", "volume_right_array")
        if dual.empty:
            pytest.skip("no binding ledger in this variant")
        assert dual["value"].min() >= -_TOL, dual.nsmallest(5, "value")


# ---------------------------------------------------------------------------
# Laja
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["both", "laja"])
class TestLajaE2E:
    """Laja rules on the solved results (genpdlajam.f)."""

    def test_partition_sums_rights_categories(self, solved, variant):
        """qgt = qdr + qde + qdm + qga (genpdlajam.f:70-76)."""
        r = solved[variant]
        total = (
            r.flow_right("laja_der_riego")
            + r.flow_right("laja_der_electrico")
            + r.flow_right("laja_der_mixto")
            + r.flow_right("laja_gasto_anticipado")
        )
        _assert_series_close(r.flow_right("laja_q_turbinado"), total, "partition")

    def test_partition_anchored_to_eltoro_turbine(self, solved, variant):
        """Every turbined m3/s at El Toro is charged to one category."""
        r = solved[variant]
        _assert_series_close(
            r.flow_right("laja_q_turbinado"), r.turbine("ELTORO"), "anchor"
        )
        assert r.turbine("ELTORO").sum() > 1.0

    def test_riego_charging_capped_by_deliveries(self, solved, variant):
        """qdr + qdm + qga <= delivered irrigation (genpdlajam.f:290-296)."""
        r = solved[variant]
        charged = (
            r.flow_right("laja_der_riego")
            + r.flow_right("laja_der_mixto")
            + r.flow_right("laja_gasto_anticipado")
        )
        delivered = sum(
            r.flow_right(f"{district}_{cat}")
            for district in ("RIEGZACO", "RieTucapel", "RieSaltos")
            for cat in ("1o_reg", "2o_reg", "emergencia", "saltos")
        )
        assert (charged - delivered).max() <= _TOL * 100.0

    def test_cap_row_has_no_seepage_term(self, solved, variant):
        """PLP's qdefm cap carries no filtration term (item 5)."""
        text = (solved[variant].case / "laja.pampl").read_text("utf-8")
        row = re.search(r"constraint laja_retiro_maximo.*?;", text, flags=re.S)
        assert row is not None
        assert not re.search(r"seepage|filtr|filt_", row.group(0), flags=re.I)

    def test_december_provision_from_zone_rule(self, solved, variant):
        """Dec eini(riego) = min(cap, rule(V)) - anticipado (Nov efin).

        Monolithic single phase: the rule state is the phase-start volume
        (ELTORO Vini = 3000 hm3): 570 + 0.40*170 + 0.40*530 +
        0.25*(3000-1900) + 30 = 1155 hm3 (genpdlajam.f:624-649).  The
        anticipado is not usable in the CEN data (QMax = 0), so no debit.
        """
        r = solved[variant]
        eini = r.volume_right("eini_sol", "laja_vol_der_riego")
        antic = r.volume_right("efin_sol", "laja_vol_gasto_anticipado")
        expected = min(_LAJA_RIEGO_CAP, _laja_riego_rule(3000.0))
        assert expected == pytest.approx(1155.0)
        for sc in r.scenarios:
            assert eini[(sc, _DEC)] == pytest.approx(
                expected - antic[(sc, _NOV)], abs=1e-3
            )

    def test_riego_rights_unusable_outside_season(self, solved, variant):
        """Monthly usage factor 0 (May..Oct) forbids qdr (plplajam.dat)."""
        r = solved[variant]
        qdr = r.flow_right("laja_der_riego")
        for stage in range(2, 8):  # May .. Oct
            assert qdr.xs(stage, level="stage").abs().max() <= _TOL


_ANTIC_BASE = 3000.0


def _enable_anticipado(src: Path) -> None:
    """QMax anticipado 0 -> 1000, ample riego base, no electric Sep/Oct.

    With electric rights unusable in September/October, El Toro releases
    made to serve irrigation must be charged to the mixed (30 hm3) or the
    anticipated account.  DerRiegoBase 570 -> _ANTIC_BASE makes the
    December provision non-binding, so anticipating is worth its usage
    cost and the December debit is exercised.
    """
    path = src / "plplajam.dat"
    text = path.read_text("latin-1")
    assert "570.00\t0.00\t0.40" in text
    text = text.replace("570.00\t0.00\t0.40", f"{_ANTIC_BASE:.2f}\t0.00\t0.40", 1)
    assert "1000 1000 1000 0" in text
    text = text.replace("1000 1000 1000 0", "1000 1000 1000 1000", 1)
    idx = text.find("uso mensual de derechos electricos")
    assert idx > 0
    line_start = text.find("\n", idx) + 1
    line_end = text.find("\n", line_start)
    factors = text[line_start:line_end].split()
    factors[5] = factors[6] = "0.00"  # September, October
    text = text[:line_start] + "  ".join(factors) + text[line_end:]
    path.write_text(text, "latin-1")


@pytest.fixture(scope="module", name="antic")
def _antic_fixture(gtopt, tmp_path_factory) -> _Results:
    case = _convert(
        tmp_path_factory.mktemp("irr_antic"), "laja", patch=_enable_anticipado
    )
    return _solve(gtopt, case)


class TestLajaAnticipadoDebit:
    """December debit of the anticipated account (genpdlajam.f:234-239)."""

    def test_anticipado_only_in_its_window(self, antic):
        """Usage factor 1 only Sep..Nov (plplajam.dat), reset in September."""
        qga = antic.flow_right("laja_gasto_anticipado")
        outside = [s for s in antic.stages if s not in (6, 7, 8)]
        for stage in outside:
            assert qga.xs(stage, level="stage").abs().max() <= _TOL

    def test_anticipado_is_used(self, antic):
        assert antic.flow_right("laja_gasto_anticipado").max() > 1.0

    def test_counter_equals_cumulative_usage(self, antic):
        """IVGAF = sum qga * dur (R12): the up-counter has no outflow.

        Before the fix the ledger kept a free extraction column and the
        LP drained the counter to 0 before December, dodging the debit.
        """
        qga = antic.flow_right("laja_gasto_anticipado")
        counter = antic.volume_right("efin_sol", "laja_vol_gasto_anticipado")
        hours = {6: 720.0, 7: 744.0, 8: 720.0}  # Sep, Oct, Nov
        for sc in antic.scenarios:
            cumulative = 0.0
            for stage, dur in hours.items():
                cumulative += qga[(sc, stage)] * dur * 0.0036
                assert counter[(sc, stage)] == pytest.approx(cumulative, abs=1e-3)

    def test_december_provision_debits_anticipado(self, antic):
        """eini(Dec) = rule(V) - IVGAF(Nov) — the up-counter is debited."""
        eini = antic.volume_right("eini_sol", "laja_vol_der_riego")
        used = antic.volume_right("efin_sol", "laja_vol_gasto_anticipado")
        for sc in antic.scenarios:
            assert used[(sc, _NOV)] > 0.0
            # base 3000: 3000 + 68 + 212 + 0.25*(3000-1900) + 30 = 3585
            provision = min(_LAJA_RIEGO_CAP, _laja_riego_rule(3000.0, _ANTIC_BASE))
            assert provision == pytest.approx(3585.0)
            assert eini[(sc, _DEC)] == pytest.approx(
                provision - used[(sc, _NOV)], abs=1e-3
            )


# ---------------------------------------------------------------------------
# Maule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["both", "maule"])
class TestMauleE2E:
    """Maule rules on the solved results (genpdmaule.f / genpdmaulen.f)."""

    def test_cipreses_turbines(self, solved, variant):
        """Invernada releases reach CIPRESES (no phantom anchor, item 1)."""
        assert solved[variant].turbine("CIPRESES").sum() > 1.0

    def test_invernada_partition_anchored_to_cipreses(self, solved, variant):
        r = solved[variant]
        total = r.flow_right("invernada_deficit") + r.flow_right(
            "invernada_sin_deficit"
        )
        _assert_series_close(total, r.turbine("CIPRESES"), "invernada anchor")

    def test_ordinary_reserve_used_below_581(self, solved, variant):
        """LMAULE in the 129..581 hm3 ordinary zone -> qmor > 0 (item 2)."""
        r = solved[variant]
        vol = r.reservoir_efin("LMAULE")
        qmor = r.flow_right("maule_gasto_ordinario_riego")
        assert vol.min() < 581.0
        assert qmor.max() > 1.0

    def test_res105_district_split(self, solved, variant):
        """retiro_k = PRet_k * qa105; Melado retiro + deficit (genpdmaulen.f)."""
        r = solved[variant]
        qa105 = r.flow_right("maule_resolucion_105")
        assert qa105.max() > 1.0
        for district, share in _RES105_SHARES.items():
            delivered = r.flow_right(f"retiro_{district}")
            if district == "Melado":
                delivered = delivered + r.flow_right("deficit_Melado")
            _assert_series_close(delivered, share * qa105, f"retiro_{district}")

    def test_res105_districts_anchored_to_physical_offtake(self, solved, variant):
        r = solved[variant]
        for district in _RES105_SHARES:
            if district == "MolinosOtros":
                continue  # terminal district: no diversion central
            _assert_series_close(
                r.flow_right(f"Rie{district}_irrigation_right"),
                r.flow_right(f"retiro_{district}"),
                f"anchor {district}",
            )

    def test_res105_bounded_by_resolution_caudal(self, solved, variant):
        """qa105 <= QR105 of the month (plpmaulen.dat Res. 105 caudal)."""
        r = solved[variant]
        fr = next(
            e
            for e in r.system["flow_right_array"]
            if e["name"] == "maule_resolucion_105"
        )
        caps = [row[0] for row in fr["fmax"]]
        qa105 = r.flow_right("maule_resolucion_105")
        for stage, cap in zip(r.stages, caps):
            assert qa105.xs(stage, level="stage").max() <= cap + _TOL


# ---------------------------------------------------------------------------
# SDDP layout: per-phase provisioning
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", name="sddp")
def _sddp_fixture(gtopt, tmp_path_factory) -> _Results:
    case = _convert(tmp_path_factory.mktemp("irr_sddp"), "both", method="sddp")
    return _solve(gtopt, case, "--set", "sddp_options.max_iterations=4")


class TestSddpPerPhaseProvision:
    """Per-stage phases: the season provision follows each scenario's state."""

    def test_december_provision_uses_phase_start_volume(self, sddp):
        """VolLajaM = VolIni(IVol) at INICIOTEMP (genpdlajam.f:575-576).

        The provision is rebuilt from the stage-start ELTORO volume of the
        SAME scenario (end of November), not the case Vini and not the
        stage's own end volume.
        """
        v_nov = sddp.reservoir_efin("ELTORO")
        eini = sddp.volume_right("eini_sol", "laja_vol_der_riego")
        efin = sddp.volume_right("efin_sol", "laja_vol_der_riego")
        used = sddp.flow_right("laja_der_riego")
        hours_dec = 744.0
        for sc in sddp.scenarios:
            expected = min(_LAJA_RIEGO_CAP, _laja_riego_rule(v_nov[(sc, _NOV)]))
            provision = eini[(sc, _DEC)]
            if provision == 0.0:  # eini not written for this phase
                provision = efin[(sc, _DEC)] + used[(sc, _DEC)] * hours_dec * 0.0036
            assert provision == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# Broken references fail loudly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "pampl", "good", "bad"),
    [
        ("laja", "laja.pampl", "turbine('ELTORO')", "turbine('NO_SUCH_TURBINE')"),
        (
            "maule",
            "maule.pampl",
            "flow_right('maule_resolucion_105')",
            "flow_right('no_such_right')",
        ),
    ],
)
def test_broken_pampl_reference_fails(gtopt, tmp_path, variant, pampl, good, bad):
    """A dangling element reference aborts the build (strict resolver)."""
    case = _convert(tmp_path, variant)
    path = case / pampl
    text = path.read_text("utf-8")
    assert good in text
    path.write_text(text.replace(good, bad, 1), "utf-8")
    res = _run_gtopt(gtopt, case, "--lp-only")
    output = res.stdout + res.stderr + _gtopt_logs(case)
    assert res.returncode != 0, output[-2000:]
    token = re.search(r"'([^']+)'", bad)
    assert token is not None
    assert token.group(1) in output
