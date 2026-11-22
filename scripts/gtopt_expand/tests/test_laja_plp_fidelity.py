# -*- coding: utf-8 -*-

"""PLP-fidelity ladder of the Laja agreement expansion (no external data).

Each test pins one rule of PLP's ``genpdlajam.f`` (CEN65) against what
``LajaAgreement`` emits for the CEN ``plplajam.dat`` committed in
``scripts/cases/plp_min_irrigation`` (verbatim plp_2y file), with
hand-computed expectations.  Stages are the 12 months April..March.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gtopt_expand.laja_agreement import LajaAgreement
from plp2gtopt.laja_parser import LajaParser
from plp2gtopt.main import make_parser

_PLPLAJAM = (
    Path(__file__).resolve().parents[2]
    / "cases"
    / "plp_min_irrigation"
    / "plplajam.dat"
)

# Calendar months of the 12 hydrological stages (April .. March).
_CAL_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


class _Stages:
    def get_all(self) -> list[dict[str, Any]]:
        return [
            {"number": i + 1, "month": m, "year": 1, "duration": 744.0}
            for i, m in enumerate(_CAL_MONTHS)
        ]


@pytest.fixture(name="cfg")
def _cfg() -> dict[str, Any]:
    parser = LajaParser(_PLPLAJAM)
    parser.parse()
    return dict(parser.config)


@pytest.fixture(name="laja")
def _laja(cfg) -> LajaAgreement:
    return LajaAgreement(cfg, stage_parser=_Stages())


def _flow(laja: LajaAgreement, name: str) -> dict[str, Any]:
    return next(fr for fr in laja.flow_rights if fr["name"] == name)


def _volume(laja: LajaAgreement, name: str) -> dict[str, Any]:
    return next(vr for vr in laja.volume_rights if vr["name"] == name)


def _sched(value: Any) -> list[float]:
    """Flatten a per-stage ``[[v], ...]`` schedule (scalar -> 12 copies)."""
    if isinstance(value, list):
        return [row[0] if isinstance(row, list) else row for row in value]
    return [float(value)] * 12


def _evaluate(rule: dict[str, Any], volume: float) -> float:
    """RightBoundRule semantics: last segment with start <= V, then cap."""
    active = rule["segments"][0]
    for seg in rule["segments"]:
        if seg["volume"] <= volume:
            active = seg
    value = max(active["constant"] + active["slope"] * volume, 0.0)
    return min(value, rule.get("cap", value))


class TestZones:
    """Cushions 1200/170/530/3682, half-open (genpdlajam.f:578-595)."""

    @pytest.mark.parametrize(
        ("volume", "riego", "elec", "mixto"),
        [
            # zone 1: 570 + 0*V ; elec 0.05*V ; mixto 30*FMixto(1)=30
            (0.0, 570.0, 0.0, 30.0),
            (1199.999, 570.0, 59.99995, 30.0),
            # zone 2 starts AT 1200: +30 transfer to riego (1 - FMixto)
            (1200.0, 600.0, 60.0, 0.0),
            (1369.999, 667.9996, 68.49995, 0.0),
            # zone 3: 570 + 68 + 30 ; elec 60 + 8.5
            (1370.0, 668.0, 68.5, 0.0),
            # zone 4: 880 ; elec 68.5 + 212
            (1900.0, 880.0, 280.5, 0.0),
            (3000.0, 1155.0, 995.5, 0.0),
            # vol max: riego 880 + 0.25*3682 ; elec 2673.8 -> max 1200
            (5582.0, 1800.5, 1200.0, 0.0),
        ],
    )
    def test_rights_by_zone(self, laja, volume, riego, elec, mixto):
        """INICIOTEMP provisioning (genpdlajam.f:624-649)."""
        assert _evaluate(
            _volume(laja, "laja_vol_der_riego")["bound_rule"], volume
        ) == pytest.approx(riego)
        assert _evaluate(
            _volume(laja, "laja_vol_der_electrico")["bound_rule"], volume
        ) == pytest.approx(elec)
        assert _evaluate(
            _volume(laja, "laja_vol_der_mixto")["bound_rule"], volume
        ) == pytest.approx(mixto)

    def test_caps_are_plp_maxima(self, laja):
        """MIN(DerX, DerXMax): 5000 / 1200 / 30 (plplajam.dat)."""
        assert _volume(laja, "laja_vol_der_riego")["bound_rule"]["cap"] == 5000
        assert _volume(laja, "laja_vol_der_electrico")["bound_rule"]["cap"] == 1200
        assert _volume(laja, "laja_vol_der_mixto")["bound_rule"]["cap"] == 30


class TestLedgers:
    """Season ledgers, resets and the anticipado debit."""

    def test_reset_months(self, laja):
        """INICIOTEMP = hydro month 9 (December); INICIOANTIC = 6 (Sep)."""
        for name in (
            "laja_vol_der_riego",
            "laja_vol_der_electrico",
            "laja_vol_der_mixto",
        ):
            assert _volume(laja, name)["reset_month"] == "december"
        antic = _volume(laja, "laja_vol_gasto_anticipado")
        assert antic["reset_month"] == "september"
        assert antic["reset_value"] == 0  # IVGAF_F = 0 (genpdlajam.f:656-660)

    def test_initial_rights(self, laja):
        """Derechos iniciales: riego 234, elec 145, mixto 0, antic 0."""
        assert _volume(laja, "laja_vol_der_riego")["eini"] == pytest.approx(234.0)
        assert _volume(laja, "laja_vol_der_electrico")["eini"] == pytest.approx(145.0)
        assert _volume(laja, "laja_vol_der_mixto")["eini"] == pytest.approx(0.0)
        assert _volume(laja, "laja_vol_gasto_anticipado")["eini"] == pytest.approx(0.0)

    def test_december_debit_links_the_anticipado(self, laja):
        """DerRiego(Dec) -= IVGAF (genpdlajam.f:234-239), as a row."""
        names = [vr["name"] for vr in laja.volume_rights]
        riego = _volume(laja, "laja_vol_der_riego")
        assert riego["reset_debit_right"] == "laja_vol_gasto_anticipado"
        # The debit row references the counter's eini column: it must be
        # built first.
        assert names.index("laja_vol_gasto_anticipado") < names.index(
            "laja_vol_der_riego"
        )

    def test_anticipado_counter_has_no_outflow(self, laja):
        """IVGAF = prev + dur*IQGAH (R12): extraction pinned to 0."""
        assert _volume(laja, "laja_vol_gasto_anticipado")["fmax"] == 0

    def test_rights_ledgers_allow_free_disposal(self, laja):
        """Depletion ledgers forfeit freely; the anticipado counter can't.

        Dropping unused rights never helps (value >= 0), but dropping the
        up-counter would erase the December debit.
        """
        for name in (
            "laja_vol_der_riego",
            "laja_vol_der_electrico",
            "laja_vol_der_mixto",
        ):
            assert _volume(laja, name)["forfeit"] is True
        assert "forfeit" not in _volume(laja, "laja_vol_gasto_anticipado")

    def test_ledgers_are_sddp_state_variables(self, laja):
        """All four ledgers carry state across phases (cuts see them).

        The link is broken only at the reset stage, where eini is the
        provision (C++: test_irrigation_reset_cross_phase.cpp).
        """
        for vr in laja.volume_rights:
            assert vr["use_state_variable"] is True


class TestCosts:
    """Usage and deficit costs (plplajam.dat; genpdlajam.f:163-165)."""

    def test_electric_usage_cost_monthly(self, laja):
        """CQVar(IQDE) = 1150 x FactMen: a COST (negative uvalue)."""
        factors = [1.1, 0, 0, 0, 0, 0, 0, 0, 1.5, 1.5, 1.2, 1.1]
        expected = [-1150.0 * f for f in factors]
        got = _sched(_flow(laja, "laja_der_electrico")["uvalue"])
        assert got == pytest.approx(expected)

    def test_mixed_and_anticipado_usage_costs(self, laja):
        """CQVar(IQDM) = 0.1, CQVar(IQGA) = 1.0 (flat factors)."""
        assert _flow(laja, "laja_der_mixto")["uvalue"] == pytest.approx(-0.1)
        assert _flow(laja, "laja_gasto_anticipado")["uvalue"] == pytest.approx(-1.0)

    def test_riego_rights_are_free(self, laja):
        """CQVar(IQDR) = 0: no uvalue, no fcost on the riego rights."""
        riego = _flow(laja, "laja_der_riego")
        assert "uvalue" not in riego
        assert "fcost" not in riego

    @pytest.mark.parametrize(
        ("district", "factor"),
        [("RIEGZACO", 1.5), ("RieTucapel", 1.0), ("RieSaltos", 0.2)],
    )
    def test_deficit_costs_pinned(self, laja, district, factor):
        """fcost = 1100 x district factor x monthly NS factor (pinned)."""
        monthly = [1.0, 0, 0, 0, 0, 0.1, 0.2, 0.5, 1.5, 1.5, 1.2, 1.0]
        expected = [1100.0 * factor * m for m in monthly]
        categories = [
            fr for fr in laja.flow_rights if fr["name"].startswith(f"{district}_")
        ]
        assert categories  # zero-share categories are not emitted
        for fr in categories:
            assert _sched(fr["fcost"]) == pytest.approx(expected), fr["name"]

    def test_pin_cli_flag(self):
        """--no-pin-agreement-costs reaches the options (default pinned)."""
        parser = make_parser()
        assert parser.parse_args(["in"]).pin_agreement_costs is True
        args = parser.parse_args(["in", "--no-pin-agreement-costs"])
        assert args.pin_agreement_costs is False


class TestSeasonAndWithdrawals:
    """Monthly usage factors and demands."""

    def test_riego_usage_window(self, laja):
        """FactUso riego 1,0,0,0,0,0,0,1,1,1,1,1 x QMax 1000."""
        usage = [1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        got = _sched(_flow(laja, "laja_der_riego")["fmax"])
        assert got == pytest.approx([1000.0 * u for u in usage])

    def test_anticipado_unusable_in_cen_data(self, laja):
        """QMax anticipado = 0 in plplajam.dat."""
        assert _sched(_flow(laja, "laja_gasto_anticipado")["fmax"]) == [0.0] * 12

    def test_first_regantes_demand(self, laja):
        """QPRiego = 90 x %1oReg x seasonal (RIEGZACO 37.2 %)."""
        seasonal = [1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1]
        got = _sched(_flow(laja, "RIEGZACO_1o_reg")["target"])
        assert got == pytest.approx([90.0 * 0.372 * s for s in seasonal])

    def test_manual_withdrawal_replaces_stage_demand(self, cfg):
        """NumRetiros row overrides that stage's QPRiego."""
        cfg["manual_withdrawals"] = [
            {
                "stage": 6,
                "q_1o_reg": 10.0,
                "q_2o_reg": 0.0,
                "q_emergencia": 0.0,
                "q_saltos": 0.0,
            }
        ]
        cfg["enable_netted_targets"] = False
        laja = LajaAgreement(cfg, stage_parser=_Stages())
        got = _sched(_flow(laja, "RIEGZACO_1o_reg")["target"])
        assert got[5] == pytest.approx(10.0 * 0.372)
        assert got[6] == pytest.approx(90.0 * 0.372)


class TestDroppedFieldsAndModes:
    """Unmodelled PLP inputs warn; 1958 economies only in 1958 mode."""

    def test_forced_flows_warn(self, cfg, caplog):
        cfg["forced_flows"] = [{"stage": 1, "flow": 30.0}]
        with caplog.at_level("WARNING"):
            LajaAgreement(cfg, stage_parser=_Stages())
        assert "NumCaudalToro" in caplog.text

    def test_ini_econ_dropped_in_2017_mode(self, cfg, caplog):
        cfg["ini_econ_endesa"] = 12.0
        with caplog.at_level("WARNING"):
            laja = LajaAgreement(cfg, stage_parser=_Stages())
        assert "DROPPED" in caplog.text
        assert all("econ" not in vr["name"] for vr in laja.volume_rights)

    def test_no_economy_accounts_by_default(self, laja):
        assert all("econ" not in vr["name"] for vr in laja.volume_rights)
