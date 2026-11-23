# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case tests for the pp2gtopt network builders."""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn
import pytest

from gtopt_shared.time_grid import TimeGrid
from pp2gtopt import convert as cv
from pp2gtopt.convert import TimeSeriesData


# ── MATPOWER loading / PST sign fix ─────────────────────────────────────


def test_load_matpower_m_file_uses_m2ppc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pandapower.converter.matpower.from_mpc  # noqa: PLC0415,F401  pylint: disable=import-outside-toplevel,unused-import

    from_mpc = sys.modules["pandapower.converter.matpower.from_mpc"]
    ppc = {"marker": True}
    seen: list[str] = []
    monkeypatch.setattr(from_mpc, "_m2ppc", lambda p: seen.append(p) or ppc)
    import pandapower.converter.pypower.from_ppc  # noqa: PLC0415,F401  pylint: disable=import-outside-toplevel,unused-import

    from_ppc_mod = sys.modules["pandapower.converter.pypower.from_ppc"]
    monkeypatch.setattr(from_ppc_mod, "from_ppc", lambda p, f_hz: pn.case9())
    path = tmp_path / "case.m"
    path.write_text("function mpc = case\n")
    net = cv.load_network(path)
    assert seen == [str(path)]
    assert len(net.bus) == 9


def _trafo_net(shift: float) -> pp.pandapowerNet:
    net = pp.create_empty_network()
    hv = pp.create_bus(net, vn_kv=230.0)
    lv = pp.create_bus(net, vn_kv=110.0)
    for _ in range(2):
        pp.create_transformer_from_parameters(
            net,
            hv,
            lv,
            sn_mva=100,
            vn_hv_kv=230,
            vn_lv_kv=110,
            vkr_percent=0.1,
            vk_percent=10,
            pfe_kw=0,
            i0_percent=0,
            shift_degree=shift,
        )
    return net


def test_fix_swapped_phase_shift() -> None:
    assert cv._fix_swapped_phase_shift(pn.case9(), {}) == 0
    net = _trafo_net(5.0)
    bus = [[0, 0, 0, 0, 0, 0, 0, 0, 0, 230.0], [1, 0, 0, 0, 0, 0, 0, 0, 0, 110.0]]
    row = [0] * 11
    branch = [
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 5.0, 1.0],  # lv -> hv: swapped by from_ppc
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 5.0, 1.0],  # parallel twin
        [0, 1, 0, 0, 0, 0, 0, 0, 0, 5.0, 1.0],  # hv -> lv: not swapped
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0.0, 1.0],  # no shift
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 5.0, 0.0],  # out of service
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 5.0, 1.0],  # third: nothing left to fix
        row,
    ]
    assert cv._fix_swapped_phase_shift(net, {"bus": bus, "branch": branch}) == 2
    assert net.trafo["shift_degree"].tolist() == [-5.0, -5.0]


def test_fix_swapped_phase_shift_opposite_parallel_shifts() -> None:
    """Parallel swapped shifters with opposite angles: the second row
    must not re-flip the trafo already corrected by the first row."""
    net = _trafo_net(5.0)
    net.trafo.at[1, "shift_degree"] = -5.0
    bus = [[0, 0, 0, 0, 0, 0, 0, 0, 0, 230.0], [1, 0, 0, 0, 0, 0, 0, 0, 0, 110.0]]
    branch = [
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 5.0, 1.0],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, -5.0, 1.0],
    ]
    assert cv._fix_swapped_phase_shift(net, {"bus": bus, "branch": branch}) == 2
    assert net.trafo["shift_degree"].tolist() == [-5.0, 5.0]


# ── generators / demands / branches ─────────────────────────────────────


def test_ext_grid_nan_pmin_and_sgen_variants() -> None:
    net = pn.case9()
    net.ext_grid.loc[0, "min_p_mw"] = math.nan
    pp.create_sgen(net, 4, p_mw=0.0, max_p_mw=60.0, min_p_mw=math.nan, name="w")
    pp.create_sgen(net, 4, p_mw=0.0, max_p_mw=0.0, name="zero")
    keyed = cv._build_generators_keyed(net)
    assert keyed[0][1]["pmin"] == 0.0
    sgens = [e for (kind, _i), e in keyed if kind == "sgen"]
    assert len(sgens) == 1
    assert sgens[0]["pmin"] == 0.0 and sgens[0]["pmax"] == 60.0


def test_out_of_service_elements_are_skipped() -> None:
    net = pn.case9()
    net.load.loc[0, "in_service"] = False
    net.line.loc[0, "in_service"] = False
    assert len(cv._build_demands(net)) == 2
    assert len(cv._build_physical_lines(net, 100.0)) == len(net.line) - 1


def test_transformer_variants() -> None:
    net = _trafo_net(0.0)
    hv, lv = 0, 1
    net.trafo.loc[0, "in_service"] = False
    net.trafo.loc[1, "max_loading_percent"] = 1e9  # -> unlimited
    net.trafo.loc[1, "tap_pos"] = "bad"  # unparsable tap data
    pp.create_transformer_from_parameters(
        net, hv, lv, 100, 230, 110, vkr_percent=0.0, vk_percent=0.0,
        pfe_kw=0, i0_percent=0,
    )  # fmt: skip
    trafos = cv._build_transformers(net, 100.0)
    assert len(trafos) == 1
    assert trafos[0]["tmax_ab"] == cv._TMAX_UNLIMITED
    assert "tap_ratio" not in trafos[0]


def test_get_tap_ratio_invalid() -> None:
    assert cv._get_tap_ratio(pd.Series({"tap_neutral": "x"})) == 1.0


def test_impedance_branches() -> None:
    net = _trafo_net(0.0)
    net.impedance = pd.DataFrame(
        {
            "from_bus": [0, 0, 0],
            "to_bus": [1, 1, 1],
            "xft_pu": [0.2, 0.0, 0.3],
            "sn_mva": [100.0, 100.0, 50.0],
            "in_service": [True, True, False],
        }
    )
    entries = cv._build_impedances(net, 100.0)
    assert [e["reactance"] for e in entries] == [0.2]
    assert entries[0]["name"] == "z1_2"
    net2 = pp.create_empty_network()
    del net2["impedance"]
    assert not cv._build_impedances(net2, 100.0)


# ── storage ─────────────────────────────────────────────────────────────


def test_build_batteries_edge_values(caplog) -> None:
    net = pn.case9()
    net.storage = pd.DataFrame(
        {
            "bus": [4, 4],
            "max_e_mwh": [0.0, 40.0],
            "min_p_mw": [None, "x"],
            "soc_percent": [None, "y"],
            "name": ["empty", "odd"],
            "in_service": [True, True],
        }
    )
    with caplog.at_level(logging.WARNING):
        batts = cv._build_batteries(net)
    assert "no max_e_mwh" in caplog.text
    assert len(batts) == 1
    b = batts[0]
    assert b["name"] == "odd"
    assert b["pmax_discharge"] == b["pmax_charge"] == 10.0  # emax / 4
    assert b["eini"] == pytest.approx(20.0)  # soc fallback 50 %


# ── time series ─────────────────────────────────────────────────────────


def test_timeseries_zeroes_pmin_of_profiled_generator(tmp_path: Path, caplog) -> None:
    net = pn.case9()
    net.gen.loc[0, "min_p_mw"] = 20.0
    ts = TimeSeriesData(
        grid=TimeGrid(n_hours=2),
        load_mw={0: np.array([10.0, 20.0])},
        gen_factor={("gen", 0): np.array([0.5, 1.0])},
    )
    with caplog.at_level(logging.INFO):
        data = cv.convert(tmp_path / "x.json", net=net, timeseries=ts)
    gen = next(g for g in data["system"]["generator_array"] if g["uid"] == 2)
    assert gen["pmin"] == 0.0
    assert "set to 0 so the availability profile can curtail it" in caplog.text


def test_sddp_options_warns_when_iterating_without_state(caplog) -> None:
    opts = cv._sddp_options(False, 5, 0.01)
    assert opts["max_iterations"] == 5
    assert "will not improve" in caplog.text
