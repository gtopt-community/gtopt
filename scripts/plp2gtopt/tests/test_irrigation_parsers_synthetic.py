# -*- coding: utf-8 -*-

"""Self-contained tests for the Maule and Laja convention parsers.

The data-driven tests in ``test_maule.py`` / ``test_laja.py`` read the
``plp_2y`` case of the external case repository (``GTOPT_CASES_DIR``),
which is not available everywhere, so they skip without it.  These tests build small synthetic
``plpmaulen.dat`` / ``plplajam.dat`` files that follow the PLP layout
field by field, so the parsers are exercised in every checkout.  All
numbers are made up; only the file structure mirrors PLP.
"""

from pathlib import Path

import pytest

from plp2gtopt.laja_parser import LajaParser
from plp2gtopt.maule_parser import MauleParser

# ---------------------------------------------------------------------------
# Maule
# ---------------------------------------------------------------------------

_MAULE_HEAD = """\
# Archivo de convenio del Maule (synthetic)
# Nombre Central Embalse Maule
'MAULE_X'
# Nombre Central Laguna Invernada
'INVER_X'
# Nombre Central Embalse Melado
'MELADO_X'
# Nombre Central Embalse Colbun
'COLBUN_X'
# Numero Caudales Hoya Intermedia
2
# Nombre Caudales Hoya Intermedia
'HOYA_A'
'HOYA_B'
# VEmbalseUtilMin (en 10^3 m3)
1000
# VReservaExtraord (en 10^3 m3)
100000
# VReservaOrdinaria (en 10^3 m3)
400000
# VDerRiegoTempMax (en 10^3 m3)
700000
# VDerElectAnuMax (en 10^3 m3)
200000
# VCompElecMax (en 10^3 m3)
300000
# VGastoElecMenIni (en 10^3 m3)
500
# VGastoElecAnuIni (en 10^3 m3)
150000
# VGastoRiegoIni (en 10^3 m3)
120000
# VGastoRExtElecIni (en 10^3 m3)
10
# VGastoRExtRiegoIni (en 10^3 m3)
20
# VDerRExtElecIni (en 10^3 m3)
30.0
# VDerRExtRiegoIni (en 10^3 m3)
40.0
# VCompElecIni (en 10^3 m3)
50.0
# VEconInverIni (en 10^3 m3)
60.0
# GastoElecMenMax (en m3/seg)
21
# GastoElecDiaMax (en m3/seg)
27
# Modulacion porcentual de GastoElecDiaMax mensual en reserva (%)
100 90 80 70 60 50 40 0 0 0 0 10
# Descuenta caudales de derechos electricos del balance en Armerillo
.TRUE.
# Gasto Riego Maximo [m3/seg]
180
# Caudal Maule Minimo para embalsar
220 T F
# Valor Riego servido Conv Maule & Res 105
900 950
# Costo Riego no servido Conv Maule & Res 105
800 850
# Porcentaje de riego mensuales Maule
30 10 0 0 0 20 60 80 90 90 70 30
# Agnos iniciales de porcentajes de riego mensuales Maule manuales
2
# Porcentajes de riego mensuales Maule manuales
35 15 0 0 0 25 65 75 65 60 58 42
36 16 0 0 0 26 66 76 66 61 59 43
# Ano desde donde se aplica modulacion automatica (0 -> desactivado)
3
# Factor de caudales futuros en disponibilidad de riego
0.75
# Modulacion automatica afecta tambien a la Res 105
T
# Volumen acumulado de derechos de riego para terminar la temporada
1000 2000 0 0 0 3000 4000 5000 6000 7000 8000 9000
# Dias acumulados para terminar la temporada de riego
60 30 0 0 0 270 240 210 180 150 120 90
# Porcentaje Caudal de derechos entrantes electricos en Reserva Maule
25
# Porcentaje Caudal de derechos entrantes riego en Reserva Maule
75
# Caudales de riego mensuales Res 105
70 35 35 35 35 55 130 170 190 190 170 110
# Agnos iniciales de porcentajes de riego mensuales Caud Res 105 manuales
1
# Caud Res 105 de riego mensuales manuales
75 36 36 36 36 56 131 151 131 121 101 71
# Numero de retiros de riego
3
# Nombres de centrales de retiro
'RetA'
'RetB'
'RetC'
# Porcentajes de retiro
50.0 30.0 20.0
# Holgura en retiro de riego
F T .TRUE.
# Bocatoma Canelon
'BocaX'
# Costo Obra Civil Canelon, por m3/seg
12
# Indice de la Extraccion de Colbun con cota disp. 425
3
# Volumen Cota Disponibildad 425 (10^3 m3)
900000
# Economias Invernada: uso en Reserva Ordinaria, acumulables, Costo
T F 0.25
"""

_MAULE_TAIL = """\
# Costo regla no embalsar Invernada, Costo regla mantener cota
1400  900
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="ascii")
    return path


@pytest.fixture(name="maule_cfg")
def fixture_maule_cfg(tmp_path):
    """Parse the full synthetic Maule file (with the optional cost line)."""
    parser = MauleParser(_write(tmp_path, "plpmaulen.dat", _MAULE_HEAD + _MAULE_TAIL))
    parser.parse()
    return parser


class TestMauleParserSynthetic:
    """Field-by-field checks of MauleParser on a synthetic file."""

    def test_names_and_basins(self, maule_cfg):
        cfg = maule_cfg.config
        assert cfg["central_maule"] == "MAULE_X"
        assert cfg["central_invernada"] == "INVER_X"
        assert cfg["central_melado"] == "MELADO_X"
        assert cfg["central_colbun"] == "COLBUN_X"
        assert cfg["intermediate_basins"] == ["HOYA_A", "HOYA_B"]

    def test_volumes_converted_to_hm3(self, maule_cfg):
        cfg = maule_cfg.config
        assert cfg["v_util_min"] == pytest.approx(1.0)
        assert cfg["v_reserva_extraord"] == pytest.approx(100.0)
        assert cfg["v_reserva_ordinaria"] == pytest.approx(400.0)
        assert cfg["v_der_riego_temp_max"] == pytest.approx(700.0)
        assert cfg["v_der_elect_anu_max"] == pytest.approx(200.0)
        assert cfg["v_comp_elec_max"] == pytest.approx(300.0)
        assert cfg["v_gasto_elec_men_ini"] == pytest.approx(0.5)
        assert cfg["v_gasto_elec_anu_ini"] == pytest.approx(150.0)
        assert cfg["v_gasto_riego_ini"] == pytest.approx(120.0)
        assert cfg["v_gasto_rext_elec_ini"] == pytest.approx(0.01)
        assert cfg["v_gasto_rext_riego_ini"] == pytest.approx(0.02)
        assert cfg["v_der_rext_elec_ini"] == pytest.approx(0.03)
        assert cfg["v_der_rext_riego_ini"] == pytest.approx(0.04)
        assert cfg["v_comp_elec_ini"] == pytest.approx(0.05)
        assert cfg["v_econ_inver_ini"] == pytest.approx(0.06)
        assert cfg["v_cota_425"] == pytest.approx(900.0)
        assert cfg["vol_acum_riego_temp"][0] == pytest.approx(1.0)
        assert cfg["vol_acum_riego_temp"][-1] == pytest.approx(9.0)

    def test_flows_flags_and_costs(self, maule_cfg):
        cfg = maule_cfg.config
        assert cfg["gasto_elec_men_max"] == pytest.approx(21.0)
        assert cfg["gasto_elec_dia_max"] == pytest.approx(27.0)
        assert cfg["mod_elec_reserva"][1] == pytest.approx(90.0)
        assert len(cfg["mod_elec_reserva"]) == 12
        assert cfg["descuenta_elec_armerillo"] is True
        assert cfg["gasto_riego_max"] == pytest.approx(180.0)
        assert cfg["caudal_min_embalsa"] == pytest.approx(220.0)
        assert cfg["flag_embalsa_1"] is True
        assert cfg["flag_embalsa_2"] is False
        assert cfg["valor_riego_maule"] == pytest.approx(900.0)
        assert cfg["valor_riego_res105"] == pytest.approx(950.0)
        assert cfg["costo_riego_ns_maule"] == pytest.approx(800.0)
        assert cfg["costo_riego_ns_res105"] == pytest.approx(850.0)
        assert cfg["costo_canelon"] == pytest.approx(12.0)
        assert cfg["econ_inver_uso_reserva"] is True
        assert cfg["econ_inver_acumulables"] is False
        assert cfg["econ_inver_costo"] == pytest.approx(0.25)

    def test_monthly_schedules(self, maule_cfg):
        cfg = maule_cfg.config
        assert cfg["pct_riego_mensual"][6] == pytest.approx(60.0)
        assert cfg["n_agnos_pct_manual"] == 2
        # One 12-value row per manual year (PLP reads NYear rows).
        assert len(cfg["pct_riego_manual"]) == 2
        assert cfg["pct_riego_manual"][0][0] == pytest.approx(35.0)
        assert cfg["pct_riego_manual"][1][0] == pytest.approx(36.0)
        assert cfg["ano_mod_auto"] == 3
        assert cfg["factor_caudales_futuros"] == pytest.approx(0.75)
        assert cfg["mod_auto_res105"] is True
        assert cfg["dias_acum_temp"] == [
            60,
            30,
            0,
            0,
            0,
            270,
            240,
            210,
            180,
            150,
            120,
            90,
        ]
        assert cfg["pct_elec_reserva"] == pytest.approx(25.0)
        assert cfg["pct_riego_reserva"] == pytest.approx(75.0)
        assert cfg["caudal_res105"][7] == pytest.approx(170.0)
        assert cfg["n_agnos_res105_manual"] == 1
        assert len(cfg["caudal_res105_manual"]) == 1
        assert cfg["caudal_res105_manual"][0][0] == pytest.approx(75.0)

    def test_districts(self, maule_cfg):
        districts = maule_cfg.config["districts"]
        assert [d["name"] for d in districts] == ["RetA", "RetB", "RetC"]
        assert [d["percentage"] for d in districts] == pytest.approx([50, 30, 20])
        assert [d["has_slack"] for d in districts] == [False, True, True]
        assert maule_cfg.config["bocatoma_canelon"] == "BocaX"
        assert maule_cfg.config["idx_extrac_colbun_425"] == 3

    def test_optional_cost_line_present(self, maule_cfg):
        cfg = maule_cfg.config
        assert cfg["costo_embalsar"] == pytest.approx(1400.0)
        assert cfg["costo_no_embalsar"] == pytest.approx(900.0)
        assert cfg["penalizador_1"] == pytest.approx(1400.0)
        assert cfg["penalizador_2"] == pytest.approx(900.0)

    def test_single_item_appended(self, maule_cfg):
        items = maule_cfg.get_all()
        assert len(items) == 1
        assert items[0]["name"] == "maule_convention"
        assert items[0]["central_colbun"] == "COLBUN_X"

    def test_optional_cost_line_missing_uses_defaults(self, tmp_path):
        parser = MauleParser(_write(tmp_path, "plpmaulen.dat", _MAULE_HEAD))
        parser.parse()
        cfg = parser.config
        assert cfg["costo_embalsar"] == pytest.approx(1500.0)
        assert cfg["costo_no_embalsar"] == pytest.approx(1000.0)
        assert cfg["penalizador_1"] == pytest.approx(1500.0)

    def test_optional_cost_line_single_value(self, tmp_path):
        text = _MAULE_HEAD + "# Penalizadores Convenio\n1234\n"
        parser = MauleParser(_write(tmp_path, "plpmaulen.dat", text))
        parser.parse()
        assert parser.config["costo_embalsar"] == pytest.approx(1234.0)
        assert parser.config["costo_no_embalsar"] == pytest.approx(1000.0)

    def test_empty_file_raises(self, tmp_path):
        parser = MauleParser(_write(tmp_path, "plpmaulen.dat", "# only comments\n"))
        with pytest.raises(ValueError, match="empty or malformed"):
            parser.parse()


# ---------------------------------------------------------------------------
# Laja
# ---------------------------------------------------------------------------

_LAJA_HEAD = """\
# Archivo con la definicion del convenio de riego Laja (synthetic)
# Nombre Central Lago Laja
'LAGO_X'
# Numero Caudales Hoya Intermedia
2
# Nombre Caudales Hoya Intermedia
'AFL_A'
'AFL_B'
# Volumen maximo Lago Laja
5000.0
# Numero de colchones
3
# Volumen util colchones
1000.0\t500.0\t3500.0
# Derecho base y factores incrementales de derechos de riego por colchon
500.00\t0.10\t0.30\t0.20
# Derecho base y factores incrementales de derechos electricos por colchon
10.00\t0.05\t0.15\t0.60
# Derecho base y factores incrementales de derechos mixtos por colchon
20.00\t1.00\t0.00\t0.00
# Derecho maximo (riego, electrico, mixto y anticipos)
4000 1100 25 4500
# Mes inicio temporada de riego y anticipos
8    5
# Caudal Maximo [m3/s] derechos riego, electrico, mixtos y anticipados
900 800 700 50
# Costos de riego no servido y uso derechos riego, electrico, mixtos y anticipos
1000.0 0.5    1100.0 0.2    2.0
# Factor mensual de costo de riego no servido
1.0 0.0 0.0 0.0 0.0 0.1 0.2 0.5 1.5 1.5 1.2 1.0
# Factor mensual de costo de derechos de riego
1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 0.9
# Factor mensual de costo de derechos electricos
1.1 0.0 0.0 0.0 0.0 0.0 0.0 0.0 1.5 1.5 1.2 1.1
# Factor mensual de costo de derechos mixto
1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 0.8
# Factor mensual de costo de gasto anticipado
1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 0.7
# Factor maximo de uso mensual de derechos de riego
1.0 0.0 0.0 0.0 0.0 0.0 0.0 1.0 1.0 1.0 1.0 1.0
# Factor maximo de uso mensual de derechos electricos
1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 0.6
# Factor maximo de uso mensual de derechos mixtos
1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 0.5
# Factor maximo de uso mensual de derechos anticipados
0.0 0.0 0.0 0.0 0.0 1.0 1.0 1.0 0.0 0.0 0.0 0.0
# Derechos iniciales disponibles para riego, elect, mixto y anticipos
200   100   5     7
# Numero de retiros de riego
3
# Nombre Retiro 1
'RetUno'
# Nombre inyeccion retiro 1 (empty -> no injection)
''
# Factor Costo, Porcentajes de retiro 1oReg, 2oReg, Emer, Saltos
1.500 0.400 0.000 0.400 0.000
# Nombre Retiro 2
'RetDos'
# Nombre inyeccion retiro 2
'INY_DOS'
# Factor Costo, Porcentajes de retiro 1oReg, 2oReg, Emer, Saltos
1.000 0.600 1.000 0.600 0.000
# Nombre Retiro 3
'RetTres'
# Nombre inyeccion retiro 3 (unquoted token -> no injection)
none
# Factor Costo, Porcentajes de retiro 1oReg, 2oReg, Emer, Saltos
0.200 0.000 0.000 0.000 1.000
# Filtraciones historicas
45.0
# Caudales de demanda de riego por defecto por regantes
80.0 50.0 1.0 6.0
# Curva variacion estacional primeros regantes
1.0 0.0 0.0 0.0 0.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0
# Curva variacion estacional segundos regantes
0.2 0.0 0.0 0.0 0.0 0.3 0.6 0.8 1.0 1.0 0.8 0.5
# Curva variacion estacional primeros regantes EMERGENCIAS
0.1 0.0 0.0 0.0 0.0 0.3 0.6 0.8 1.0 1.0 0.8 0.5
# Curva variacion estacional Saltos del Laja
0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.5 1.0 1.0 0.0
# Volumen muerto
12.5
"""

_LAJA_NO_OVERRIDES = """\
# Retiro manuales de riego por etapa por retiro (m3/s)
# Etapas
0
# Caudales forzados en El Toro por etapa (m3/s)
# Etapas
0
"""

_LAJA_WITH_OVERRIDES = """\
# Retiro manuales de riego por etapa por retiro (m3/s)
# Etapas
2
# Etapa   1oReg     2oReg     Emer      Saltos
3 10.0 20.0 30.0 40.0
4 11.0 21.0 31.0 41.0
# Caudales forzados en El Toro por etapa (m3/s)
# Etapas
1
# NEtapa  QGxElToro
5 99.5
"""


@pytest.fixture(name="laja_parser")
def fixture_laja_parser(tmp_path):
    """Parse the synthetic Laja file without manual/forced overrides."""
    parser = LajaParser(
        _write(tmp_path, "plplajam.dat", _LAJA_HEAD + _LAJA_NO_OVERRIDES)
    )
    parser.parse()
    return parser


class TestLajaParserSynthetic:
    """Field-by-field checks of LajaParser on a synthetic file."""

    def test_reservoir_and_zones(self, laja_parser):
        cfg = laja_parser.config
        assert cfg["central_laja"] == "LAGO_X"
        assert cfg["intermediate_basins"] == ["AFL_A", "AFL_B"]
        assert cfg["vol_max"] == pytest.approx(5000.0)
        assert cfg["zone_widths"] == pytest.approx([1000.0, 500.0, 3500.0])
        assert cfg["vol_muerto"] == pytest.approx(12.5)

    def test_rights_bases_and_factors(self, laja_parser):
        cfg = laja_parser.config
        assert cfg["irr_base"] == pytest.approx(500.0)
        assert cfg["irr_factors"] == pytest.approx([0.1, 0.3, 0.2])
        assert cfg["elec_base"] == pytest.approx(10.0)
        assert cfg["elec_factors"] == pytest.approx([0.05, 0.15, 0.6])
        assert cfg["mixed_base"] == pytest.approx(20.0)
        assert cfg["mixed_factors"] == pytest.approx([1.0, 0.0, 0.0])

    def test_limits_and_season(self, laja_parser):
        cfg = laja_parser.config
        assert (cfg["max_irr"], cfg["max_elec"]) == pytest.approx((4000, 1100))
        assert (cfg["max_mixed"], cfg["max_anticipated"]) == pytest.approx((25, 4500))
        assert cfg["mes_inicio_riego"] == 8
        assert cfg["mes_inicio_anticipos"] == 5
        assert cfg["qmax_irr"] == pytest.approx(900.0)
        assert cfg["qmax_elec"] == pytest.approx(800.0)
        assert cfg["qmax_mixed"] == pytest.approx(700.0)
        assert cfg["qmax_anticipated"] == pytest.approx(50.0)

    def test_costs_follow_plp_column_order(self, laja_parser):
        """CRiegoNS then the four POSITIVE usage costs (leelajam.f)."""
        cfg = laja_parser.config
        assert cfg["cost_irr_ns"] == pytest.approx(1000.0)
        assert cfg["cost_irr_uso"] == pytest.approx(0.5)
        assert cfg["cost_elec_uso"] == pytest.approx(1100.0)
        assert cfg["cost_mixed_uso"] == pytest.approx(0.2)
        assert cfg["cost_antic_uso"] == pytest.approx(2.0)
        assert "cost_elec_ns" not in cfg

    def test_monthly_arrays(self, laja_parser):
        cfg = laja_parser.config
        keys = [
            "monthly_cost_irr_ns",
            "monthly_cost_irr",
            "monthly_cost_elec",
            "monthly_cost_mixed",
            "monthly_cost_anticipated",
            "monthly_usage_irr",
            "monthly_usage_elec",
            "monthly_usage_mixed",
            "monthly_usage_anticipated",
            "seasonal_1o_reg",
            "seasonal_2o_reg",
            "seasonal_emergencia",
            "seasonal_saltos",
        ]
        for key in keys:
            assert len(cfg[key]) == 12, key
        assert cfg["monthly_cost_irr"][-1] == pytest.approx(0.9)
        assert cfg["monthly_cost_mixed"][-1] == pytest.approx(0.8)
        assert cfg["monthly_cost_anticipated"][-1] == pytest.approx(0.7)
        assert cfg["monthly_usage_elec"][-1] == pytest.approx(0.6)
        assert cfg["monthly_usage_mixed"][-1] == pytest.approx(0.5)
        assert cfg["seasonal_emergencia"][0] == pytest.approx(0.1)
        assert cfg["seasonal_saltos"][9] == pytest.approx(1.0)

    def test_initial_rights_and_demands(self, laja_parser):
        cfg = laja_parser.config
        assert cfg["ini_irr"] == pytest.approx(200.0)
        assert cfg["ini_elec"] == pytest.approx(100.0)
        assert cfg["ini_mixed"] == pytest.approx(5.0)
        assert cfg["ini_anticipated"] == pytest.approx(7.0)
        assert cfg["filtracion_laja"] == pytest.approx(45.0)
        assert cfg["demand_1o_reg"] == pytest.approx(80.0)
        assert cfg["demand_2o_reg"] == pytest.approx(50.0)
        assert cfg["demand_emergencia"] == pytest.approx(1.0)
        assert cfg["demand_saltos"] == pytest.approx(6.0)

    def test_districts_and_injection_variants(self, laja_parser):
        districts = laja_parser.config["districts"]
        assert [d["name"] for d in districts] == ["RetUno", "RetDos", "RetTres"]
        # '' -> None, quoted name -> name, unquoted token -> None
        assert [d["injection"] for d in districts] == [None, "INY_DOS", None]
        assert districts[0]["cost_factor"] == pytest.approx(1.5)
        assert districts[1]["pct_2o_reg"] == pytest.approx(1.0)
        assert districts[1]["pct_emergencia"] == pytest.approx(0.6)
        assert districts[2]["pct_saltos"] == pytest.approx(1.0)
        assert districts[2]["pct_1o_reg"] == pytest.approx(0.0)

    def test_no_overrides(self, laja_parser):
        assert laja_parser.config["manual_withdrawals"] == []
        assert laja_parser.config["forced_flows"] == []
        items = laja_parser.get_all()
        assert len(items) == 1
        assert items[0]["name"] == "laja_convention"

    def test_manual_withdrawals_and_forced_flows(self, tmp_path):
        parser = LajaParser(
            _write(tmp_path, "plplajam.dat", _LAJA_HEAD + _LAJA_WITH_OVERRIDES)
        )
        parser.parse()
        cfg = parser.config
        assert cfg["manual_withdrawals"] == [
            {
                "stage": 3,
                "q_1o_reg": 10.0,
                "q_2o_reg": 20.0,
                "q_emergencia": 30.0,
                "q_saltos": 40.0,
            },
            {
                "stage": 4,
                "q_1o_reg": 11.0,
                "q_2o_reg": 21.0,
                "q_emergencia": 31.0,
                "q_saltos": 41.0,
            },
        ]
        assert cfg["forced_flows"] == [{"stage": 5, "flow": 99.5}]

    def test_empty_file_raises(self, tmp_path):
        parser = LajaParser(_write(tmp_path, "plplajam.dat", "\n# nothing\n"))
        with pytest.raises(ValueError, match="empty or malformed"):
            parser.parse()
