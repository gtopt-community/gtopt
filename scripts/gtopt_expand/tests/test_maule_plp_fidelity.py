# -*- coding: utf-8 -*-

"""PLP-fidelity tests of the Maule agreement expansion.

Each test pins one row/rule of PLP's ``genpdmaule.f`` / ``FijaMaule``
(CEN65 sources) against the entities that ``MauleAgreement`` emits.
The configs are synthetic so the suite runs without external cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gtopt_expand.maule_agreement import MauleAgreement


class _Stages:
    """Minimal stage-parser stand-in (``get_all()`` of stage dicts)."""

    def __init__(self, stages: list[dict[str, Any]]):
        self._stages = stages

    def get_all(self) -> list[dict[str, Any]]:
        """Return the stage dicts."""
        return self._stages


def _cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "central_maule": "LMAULE",
        "central_invernada": "CIPRESES",
        "central_melado": "PEHUENCHE",
        "central_colbun": "COLBUN",
        "v_util_min": 0.0,
        "v_reserva_extraord": 129.0,
        "v_reserva_ordinaria": 452.0,
        "v_der_riego_temp_max": 800.0,
        "v_der_elect_anu_max": 250.0,
        "v_comp_elec_max": 350.0,
        "gasto_elec_men_max": 25.0,
        "gasto_elec_dia_max": 30.0,
        "gasto_riego_max": 200.0,
        "caudal_min_embalsa": 250.0,
        "flag_embalsa_1": True,
        "flag_embalsa_2": False,
        "mod_elec_reserva": [100] * 7 + [0] * 5,
        # hydro months Apr..Mar (plpmaulen order)
        "pct_riego_mensual": [40, 20, 0, 0, 0, 30, 70, 90, 100, 100, 80, 40],
        "pct_elec_reserva": 20.0,
        "pct_riego_reserva": 80.0,
        "caudal_res105": [80, 40, 40, 40, 40, 60, 140, 180, 200, 200, 180, 120],
        "v_gasto_elec_men_ini": 0.0,
        "v_gasto_elec_anu_ini": 184.049,
        "v_gasto_riego_ini": 133.406,
        "v_gasto_rext_elec_ini": 0.0,
        "v_gasto_rext_riego_ini": 0.0,
        "v_der_rext_elec_ini": 0.0,
        "v_der_rext_riego_ini": 0.0,
        "v_comp_elec_ini": 0.0,
        "v_econ_inver_ini": 0.0,
        "valor_riego_maule": 1100.0,
        "valor_riego_res105": 1100.0,
        "costo_riego_ns_maule": 1000.0,
        "costo_riego_ns_res105": 1000.0,
        "costo_embalsar": 1500.0,
        "costo_no_embalsar": 1000.0,
        "bocatoma_canelon": "BCanelon",
        "costo_canelon": 10.0,
        "intermediate_basins": ["B_LaMina", "COLBUN"],
        "districts": [
            {"name": "RieCMNA", "percentage": 87.35, "has_slack": False},
            {"name": "RieMelado", "percentage": 12.65, "has_slack": True},
        ],
    }
    cfg.update(overrides)
    return cfg


def _stages(months: list[int], year: int = 1) -> _Stages:
    return _Stages(
        [
            {"number": i + 1, "month": m, "year": year, "duration": 744.0}
            for i, m in enumerate(months)
        ]
    )


def _by_name(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in entities}


def _pampl(agreement: MauleAgreement, tmp_path: Path) -> str:
    name = agreement.generate_pampl(tmp_path)
    return (tmp_path / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Item 1 — La Invernada level rule (PLP IQNINV / IQRNIN)
# ---------------------------------------------------------------------------


class TestInvernadaLevelRule:
    """genpdmaule.f:433-460 + FijaMaule :876-911."""

    def test_no_soft_balance_and_no_turbine_tax(self, tmp_path):
        """The old soft `invernada_balance` (every turbined m3/s taxed
        ~1000 through embalsar/no_embalsar) must be gone."""
        agreement = MauleAgreement(_cfg())
        text = _pampl(agreement, tmp_path)
        assert "constraint invernada_balance" not in text
        for uc in agreement.user_constraints:
            assert "penalty" not in uc
            assert uc["name"] != "invernada_balance"

    def test_relax_invern_deactivates_rule(self, tmp_path):
        """RelaxInvern = T: PLP's IQNINV becomes `<=` with qninv free
        below, which deactivates IQRNIN — nothing is emitted."""
        agreement = MauleAgreement(
            _cfg(flag_embalsa_1=True, inflow_ref_invernada="CIPRESES")
        )
        names = _by_name(agreement.flow_rights)
        assert "invernada_embalsar" not in names
        assert "invernada_no_embalsar" not in names
        text = _pampl(agreement, tmp_path)
        assert "constraint invernada_nivel" not in text

    def test_hard_rule_in_deficit_stages(self, tmp_path):
        """RelaxInvern = F: hard IQNINV + IQRNIN rows on the deficit
        stages only; storing natural inflow costs CostoEmbalsar,
        releasing more than the inflow is free (qhein)."""
        # Calendar months: June (pct 0 -> not deficit), January
        # (pct 100, no hoya -> deficit).
        agreement = MauleAgreement(
            _cfg(
                flag_embalsa_1=False,
                inflow_ref_invernada="CIPRESES",
                seepage_ref_invernada="CIPRESES_seepage_1",
                q_hoya_inter_maule=[0.0, 0.0],
                q_afl_invernada=[5.0, 5.0],
            ),
            stage_parser=_stages([6, 1]),
        )
        frs = _by_name(agreement.flow_rights)
        assert frs["invernada_embalsar"]["uvalue"] == pytest.approx(-1500.0)
        assert frs["invernada_no_embalsar"]["uvalue"] == pytest.approx(-1000.0)
        assert "uvalue" not in frs["invernada_holgura_superior"]
        assert "fcost" not in frs["invernada_holgura_superior"]
        # IQNINV is substituted: no free-sign carrier column.
        assert "invernada_caudal_natural" not in frs
        # Gating: June -> qisd open, qidn closed; January the opposite.
        assert frs["invernada_deficit"]["fmax"] == [[0.0], [200.0]]
        assert frs["invernada_sin_deficit"]["fmax"] == [[200.0], [0.0]]

        text = _pampl(agreement, tmp_path)
        nivel = text[text.index("constraint invernada_nivel") :]
        nivel = nivel[: nivel.index(";")]
        assert "for(stage in {2})" in nivel
        assert "penalty" not in nivel
        # deficit + embalsar + filt = QAfl + no_embalsar + holgura
        lhs, rhs = nivel.split(":", 1)[1].split("=")
        assert "invernada_deficit" in lhs and "invernada_embalsar" in lhs
        assert "seepage('CIPRESES_seepage_1').flow" in lhs
        assert "flow('CIPRESES').flow" in rhs
        assert "invernada_holgura_superior" in rhs

    def test_deficit_classification_uses_gasto_maule_min(self):
        """Second PLP test: QHoyaInter + QAflInvern < friego·GastoMauleMin
        also forbids storing, even when the hoya covers QRiego."""
        agreement = MauleAgreement(
            _cfg(
                flag_embalsa_1=False,
                inflow_ref_invernada="CIPRESES",
                # January pct 100: QRiego 200 covered by hoya 300 ->
                # QDef 0; but 300 + 5 >= 250 -> may store.  Second
                # stage: hoya 210 + 5 < 250 -> deficit.
                q_hoya_inter_maule=[300.0, 210.0],
                q_afl_invernada=[5.0, 5.0],
            ),
            stage_parser=_stages([1, 1]),
        )
        frs = _by_name(agreement.flow_rights)
        assert frs["invernada_deficit"]["fmax"] == [[0.0], [200.0]]


# ---------------------------------------------------------------------------
# Item 2 — ordinary-regime reserves (FijaMaule :802-810, :977-987)
# ---------------------------------------------------------------------------


class TestOrdinaryReserve:
    """Rights 20/80 of (V_util − 129) provisioned below 581 hm³."""

    def test_provisioned_below_normal_regime(self, tmp_path):
        agreement = MauleAgreement(
            _cfg(v_ini_maule=400.0, inflow_ref_maule="LMAULE"),
            stage_parser=_stages([4, 5]),
        )
        vrs = _by_name(agreement.volume_rights)
        riego = vrs["maule_vol_reserva_ord_riego"]
        elec = vrs["maule_vol_reserva_ord_elec"]
        assert riego["eini"] == pytest.approx(0.8 * (400.0 - 129.0))
        assert elec["eini"] == pytest.approx(0.2 * (400.0 - 129.0))
        # Re-provisioned each January from the Maule volume.
        assert riego["reset_month"] == "january"
        seg = riego["bound_rule"]["segments"][1]
        assert seg["volume"] == pytest.approx(129.0)
        assert seg["slope"] == pytest.approx(0.8)
        assert seg["constant"] == pytest.approx(-0.8 * 129.0)
        assert riego["emax"] == pytest.approx(0.8 * 452.0)
        # Rights accrue 80 % / 20 % of the Maule natural inflow.
        text = _pampl(agreement, tmp_path)
        assert (
            "volume_right('maule_vol_reserva_ord_riego').saving <= 0.8 *"
            " flow('LMAULE').flow"
        ) in text
        assert (
            "volume_right('maule_vol_reserva_ord_elec').saving <= 0.2 *"
            " flow('LMAULE').flow"
        ) in text

    def test_normal_regime_zeroes_reserve(self):
        agreement = MauleAgreement(_cfg(v_ini_maule=900.0), stage_parser=_stages([4]))
        vrs = _by_name(agreement.volume_rights)
        assert vrs["maule_vol_reserva_ord_riego"]["eini"] == pytest.approx(0.0)
        assert vrs["maule_vol_reserva_ord_elec"]["eini"] == pytest.approx(0.0)

    def test_january_rule_zero_in_normal_regime_and_capped(self):
        """The January re-provision never exceeds emax (= p * 452).

        A rule ``p * (V - 129)`` without a normal-regime segment fixes the
        reset ``eini`` above ``emax`` once V > 581 -- the plp_2y SDDP run
        went infeasible in every scene at the January phase.  PLP zeroes
        the reserve ledgers in the normal regime (FijaMaule).
        """

        def rule(segs, v):
            active = [sg for sg in segs if sg["volume"] <= v][-1]
            return active["constant"] + active["slope"] * v

        agreement = MauleAgreement(_cfg(v_ini_maule=400.0), stage_parser=_stages([1]))
        vrs = _by_name(agreement.volume_rights)
        for name in ("maule_vol_reserva_ord_riego", "maule_vol_reserva_ord_elec"):
            vr = vrs[name]
            segs = vr["bound_rule"]["segments"]
            for v in (0.0, 100.0, 129.0, 400.0, 580.9, 581.0, 900.0, 1450.0):
                assert -1e-9 <= rule(segs, v) <= vr["emax"] + 1e-9, (name, v)
            assert rule(segs, 900.0) == pytest.approx(0.0)

    def test_existing_rights_kept_minus_used(self):
        agreement = MauleAgreement(
            _cfg(
                v_ini_maule=400.0,
                v_der_rext_riego_ini=100.0,
                v_gasto_rext_riego_ini=30.0,
            ),
            stage_parser=_stages([4]),
        )
        vrs = _by_name(agreement.volume_rights)
        assert vrs["maule_vol_reserva_ord_riego"]["eini"] == pytest.approx(70.0)

    def test_no_flow_ratio_rows(self, tmp_path):
        """The non-PLP maule_pct_ordinario_* UCs are gone."""
        agreement = MauleAgreement(_cfg(v_ini_maule=400.0))
        text = _pampl(agreement, tmp_path)
        assert "maule_pct_ordinario" not in text
        assert all(
            "pct_ordinario" not in uc["name"] for uc in agreement.user_constraints
        )


# ---------------------------------------------------------------------------
# Item 3 — up-counters become remaining-rights ledgers
# ---------------------------------------------------------------------------


class TestRightsLedgers:
    """eini = cap − used; never the raw used value (genpdmaule.f:269-351)."""

    def test_eini_is_remaining_not_used(self):
        agreement = MauleAgreement(_cfg(), stage_parser=_stages([4, 5]))
        vrs = _by_name(agreement.volume_rights)
        anu = vrs["maule_vol_gasto_elec_anual"]
        assert anu["eini"] == pytest.approx(250.0 - 184.049)
        assert anu["eini"] != pytest.approx(184.049)
        riego = vrs["maule_vol_gasto_riego_temp"]
        assert riego["eini"] == pytest.approx(800.0 - 133.406)

    def test_monthly_cap_is_volume_not_rate(self):
        """VDerElecMenMax = 25 m³/s × HorasMesHid × 0.0036 hm³."""
        agreement = MauleAgreement(
            _cfg(v_gasto_elec_men_ini=10.0), stage_parser=_stages([4, 5, 2])
        )
        men = _by_name(agreement.volume_rights)["maule_vol_gasto_elec_mensual"]
        assert men["emax"] == [
            [pytest.approx(25.0 * 720 * 0.0036)],
            [pytest.approx(25.0 * 744 * 0.0036)],
            [pytest.approx(25.0 * 672 * 0.0036)],
        ]
        assert men["eini"] == pytest.approx(25.0 * 720 * 0.0036 - 10.0)
        assert men["reset_monthly"] is True

    def test_depletion_ledgers_allow_free_disposal(self):
        """Every Maule ledger is a depletion ledger: forfeit = true.

        Free disposal keeps the state value >= 0 (no degenerate cap
        vertex; plp_2y SDDP cuts carried negative ledger slopes without it).
        """
        agreement = MauleAgreement(_cfg(), stage_parser=_stages([4, 5]))
        vrs = _by_name(agreement.volume_rights)
        for name in (
            "maule_vol_gasto_elec_mensual",
            "maule_vol_gasto_elec_anual",
            "maule_vol_gasto_riego_temp",
            "maule_vol_compensacion_elec",
            "maule_vol_reserva_ord_elec",
            "maule_vol_reserva_ord_riego",
        ):
            if name in vrs:
                assert vrs[name].get("forfeit") is True, name
        assert all(
            vr.get("forfeit") is True
            for vr in agreement.volume_rights
            if "saving_rate" not in vr and "reset_value" not in vr
        )


# ---------------------------------------------------------------------------
# Item 8 — manual years (PRiegoMauleAnual / QRiego105Anual)
# ---------------------------------------------------------------------------


class TestManualYears:
    """genpdmaule.f:1679-1736: NYear manual rows override the default
    monthly arrays for the first NYear plpeta years."""

    def test_manual_rows_apply_to_their_year_only(self):
        manual_pct = [40, 20, 0, 0, 0, 30, 70, 80, 70, 65, 63, 47]
        manual_105 = [80, 40, 40, 40, 40, 60, 140, 160, 140, 130, 110, 80]
        stages = _Stages(
            [
                {"number": 1, "month": 1, "year": 1, "duration": 744.0},
                {"number": 2, "month": 1, "year": 2, "duration": 744.0},
            ]
        )
        agreement = MauleAgreement(
            _cfg(
                n_agnos_pct_manual=1,
                pct_riego_manual=[manual_pct],
                n_agnos_res105_manual=1,
                caudal_res105_manual=[manual_105],
            ),
            stage_parser=stages,
        )
        frs = _by_name(agreement.flow_rights)
        # January = hydro month 10: manual 65 % (year 1), default 100 %.
        assert frs["maule_gasto_normal_riego"]["fmax"] == [[130.0], [200.0]]
        # Res 105 January: manual 130 (year 1), default 200 (year 2).
        assert frs["maule_resolucion_105"]["fmax"] == [[130.0], [200.0]]


class TestCota425:
    """genpdmaule.f:1052-1063: the IExtr425 COLBUN extraction is open
    (ExtrMax) only while V_COLBUN >= Vol425."""

    def test_rule_emitted_with_resolved_waterway(self, tmp_path):
        agreement = MauleAgreement(
            _cfg(
                v_cota_425=1047.9,
                idx_extrac_colbun_425=2,
                extraction_ref_425="COLBUN_extrac_1_28_26",
                qmax_425=50.0,
            )
        )
        fr = _by_name(agreement.flow_rights)["maule_cota_425"]
        assert fr["bound_rule"]["reservoir"] == "COLBUN"
        seg = fr["bound_rule"]["segments"]
        assert seg[0]["constant"] == 0
        assert seg[1]["volume"] == pytest.approx(1047.9)
        assert seg[1]["constant"] == pytest.approx(50.0)
        text = _pampl(agreement, tmp_path)
        assert (
            "waterway('COLBUN_extrac_1_28_26').flow <="
            " flow_right('maule_cota_425').flow"
        ) in text

    def test_unresolved_waterway_warns(self, caplog):
        with caplog.at_level("WARNING"):
            agreement = MauleAgreement(_cfg(v_cota_425=1047.9))
        assert "maule_cota_425" not in _by_name(agreement.flow_rights)
        assert "Cota 425" in caplog.text
