# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of ``plp2gtopt --results`` (:mod:`plp2gtopt.results_import`).

The main test is the round trip ``gtopt results -> gtopt2plp --results
-> plp2gtopt --results``: the imported gtopt output must match the
original stream by stream (to the two decimals PLP writes).
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.results_reader import GtoptResults
from gtopt_shared.testing.results_roundtrip import check_layout, stream_diffs
from gtopt_shared.testing.roundtrip import gtopt_binary, solve_cost
from gtopt_shared.testing.tiny_cases import write_tiny_case, write_tiny_results
from gtopt2plp.results import write_results
from gtopt2plp.writer import PlpWriter
from plp2gtopt.main import main
from plp2gtopt.results_import import (
    PlpResultsImporter,
    import_plp_results,
    read_plp_csv,
)

#: PLP writes 2 decimals; water values 2 decimals of $/dam3 (= 10 $/hm3).
TOL = {"water_value_dual": 10.0}


def _export(tmp_path: Path, results: Path) -> tuple[GtoptCase, Path]:
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    plp = tmp_path / "plp"
    writer = PlpWriter(case, plp)
    writer.write()
    write_results(case, results, plp, writer)
    return case, plp


def _convert(plp: Path, out: Path, *extra: str) -> GtoptCase:
    main(["-i", str(plp), "-o", str(out), "--no-color", "-y", "all", *extra])
    return GtoptCase.load(out / f"{out.name}.json")


def _assert_roundtrip(
    orig: GtoptCase, orig_out: Path, back: GtoptCase, back_out: Path
) -> None:
    diffs = stream_diffs(orig, orig_out, back, back_out)
    for (cls, fld, name), d in diffs.items():
        assert not math.isnan(d), f"{cls}/{fld} {name}: missing"
        assert d <= TOL.get(fld, 0.02), f"{cls}/{fld} {name}: {d}"
    a = GtoptResults(orig_out, orig.grid)
    b = GtoptResults(back_out, back.grid)
    for fld in ("efin_sol", "eini_sol"):
        np.testing.assert_allclose(
            a.stage_of("Reservoir", fld, 1), b.stage_of("Reservoir", fld, 1), atol=1e-4
        )


@pytest.fixture(name="plp_dir")
def _plp_dir(tmp_path: Path) -> tuple[GtoptCase, Path, Path]:
    res = write_tiny_results(tmp_path / "res", "hive")
    case, plp = _export(tmp_path, res)
    return case, res, plp


def test_roundtrip_synthetic(
    plp_dir: tuple[GtoptCase, Path, Path], tmp_path: Path
) -> None:
    """gtopt2plp results read back by plp2gtopt --results match the original."""
    case, res, plp = plp_dir
    back = _convert(plp, tmp_path / "back", "--results")
    out = tmp_path / "back" / "results_plp"
    check_layout(out)
    _assert_roundtrip(case, res, back, out)
    sol = pd.read_csv(out / "solution.csv")
    assert set(zip(sol["scene"], sol["phase"], strict=True)) == {
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    }
    meta = json.loads((out / "solver_status.json").read_text())
    assert meta["source"] == "plp" and meta["warnings"] == []


def test_results_only_and_output_dir(
    plp_dir: tuple[GtoptCase, Path, Path], tmp_path: Path
) -> None:
    """--results-only reuses the converted JSON; --results-output relocates."""
    _case, _res, plp = plp_dir
    back = tmp_path / "back"
    _convert(plp, back)
    assert not (back / "results_plp").exists()
    dest = tmp_path / "imported"
    main(
        [
            "-i",
            str(plp),
            "-o",
            str(back),
            "--results",
            str(plp),
            "--results-only",
            "--results-output",
            str(dest),
        ]
    )
    check_layout(dest)


def test_cli_errors(plp_dir: tuple[GtoptCase, Path, Path], tmp_path: Path) -> None:
    """--results-only without --results; a directory without PLP output."""
    _case, _res, plp = plp_dir
    with pytest.raises(SystemExit) as exc:
        main(["-i", str(plp), "-o", str(tmp_path / "b"), "--results-only"])
    assert exc.value.code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    _convert(plp, tmp_path / "b")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "-i",
                str(plp),
                "-o",
                str(tmp_path / "b"),
                "--results",
                str(empty),
                "--results-only",
            ]
        )
    assert exc.value.code == 1
    with pytest.raises(SystemExit) as exc:
        _convert(plp, tmp_path / "c", "--results", str(empty))
    assert exc.value.code == 1


def test_unmatched_and_edge_rows(
    plp_dir: tuple[GtoptCase, Path, Path], tmp_path: Path
) -> None:
    """Unknown names, simulations and blocks warn; MEDIA rows are skipped."""
    case, _res, plp = plp_dir
    src = tmp_path / "edited"
    shutil.copytree(plp, src)
    with (src / "plpcen.csv").open("a", encoding="latin-1") as fh:
        fh.write("Sim  1,   1,02 Bloques,  99,GHOST,Ter,   1,X,0,7,0,0,0,0,0,0,0\n")
        fh.write("Sim  9,   1,02 Bloques,   4,TERMO,Ter,   2,X,0,7,0,0,0,0,0,0,0\n")
        fh.write("Sim  1,  77,02 Bloques,   4,TERMO,Ter,   2,X,0,7,0,0,0,0,0,0,0\n")
        fh.write("Sim  1, bad,02 Bloques,   4,TERMO,Ter,   2,X,0,7,0,0,0,0,0,0,0\n")
        fh.write(",,,\n")
    with (src / "plpbar.csv").open("a", encoding="latin-1") as fh:
        fh.write("Sim  1,   1,02 Bloques, 9,NOWHERE,1,1,0,0,0,0,0\n")
    with (src / "plplin.csv").open("a", encoding="latin-1") as fh:
        fh.write("Sim  1,   1,02 Bloques, 9,NOLINE,1,2,5,0,0,0,1,0,0,0,0,0\n")
    with (src / "plpemb.csv").open("a", encoding="latin-1") as fh:
        fh.write("Sim  1,   1,02 Bloques, 9,NORES, 0.10E+07,1,1,0,0,0,0,0,0,0,0\n")
    with (src / "plpess.csv").open("a", encoding="latin-1") as fh:
        fh.write("Sim  1,   1, NOESS,1,0,1,0,0,1,  0,0,0,1,0,0,0\n")
    with (src / "plpfal.csv").open("a", encoding="latin-1") as fh:
        fh.write("Sim  1,   1,02 Bloques,  11,FALLA_X,Fal,   7,LONELY,3.0,0.1\n")
    planning = case.planning
    res = PlpResultsImporter(src, planning).run()
    text = "\n".join(res.warnings)
    for needle in ("GHOST", "Sim  9", "block 77", "NOWHERE", "NOLINE", "NORES"):
        assert needle in text, needle
    assert "NOESS" in text and "LONELY" in text
    gen = res.get_blocks("Generator", "generation_sol", 4)
    assert gen is not None and gen[0, 0] == pytest.approx(11.0)


def test_cascade_spill_seepage_and_split_demands(tmp_path: Path) -> None:
    """Spill to a ``_ver`` waterway, seepage, several demands on a bus."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    planning = case.planning
    sysm = planning["system"]
    sysm["waterway_array"] = [
        {"uid": 5, "name": "LAGO_ver_1_2", "junction_a": "LAGO"},
        {"uid": 6, "name": "LAGO_filt", "junction_a": "LAGO"},
    ]
    sysm["reservoir_seepage_array"] = [
        {"uid": 1, "name": "f", "waterway": "LAGO_filt", "reservoir": "LAGO"}
    ]
    sysm["demand_array"].append({"uid": 9, "name": "extra", "bus": 2, "lmax": 50.0})
    src = tmp_path / "plp"
    src.mkdir()
    (src / "plpemb.csv").write_text(
        "Hidro,Bloque,TipoEtapa,EmbNum,EmbNom,EmbFac,EmbVini,EmbVfin,EmbQgen,"
        "EmbQver,EmbQdef,EmbPsom,EmbPsom2,EmbAflu,EmbQFil,EmbQReb\n"
        "Sim  1,   1,02 Bloques, 1,LAGO, 0.10E+07,500,450,10,4,0,0,1.5,0,2,0\n"
        "MEDIA ,   1,02 Bloques, 1,LAGO, 0.10E+07,500,450,10,4,0,0,1.5,0,2,0\n",
        encoding="latin-1",
    )
    (src / "plpbar.csv").write_text(
        "Hidro,Bloque,TipoEtapa,BarNum,BarNom,CMgBar,DemBarP\nSim  1,   1,x,2,Sur220,60,150\n",
        encoding="latin-1",
    )
    res = PlpResultsImporter(src, planning).run()
    spill = res.get_blocks("Waterway", "flow_sol", 5)
    seep = res.get_blocks("Waterway", "flow_sol", 6)
    assert spill is not None and spill[0, 0] == 4.0
    assert seep is not None and seep[0, 0] == 2.0
    tur = res.get_blocks("Turbine", "flow_sol", 1)
    assert tur is not None and tur[0, 0] == 10.0
    assert any("2 demands" in w for w in res.warnings)  # Sur220 lmax is a list
    # without the waterways: the junction drain, and a seepage warning
    del sysm["waterway_array"]
    res = PlpResultsImporter(src, planning).run()
    drain = res.get_blocks("Junction", "drain_sol", 1)
    assert drain is not None and drain[0, 0] == 4.0
    assert any("seepage has no waterway" in w for w in res.warnings)
    sysm["reservoir_array"][0]["junction"] = "NOPE"
    res = PlpResultsImporter(src, planning).run()
    assert any("spill has no waterway" in w for w in res.warnings)


def test_bus_without_demand_and_battery_central(tmp_path: Path) -> None:
    """A loaded bus without demand warns; a BAT central fills Battery/fout_sol."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    planning = case.planning
    planning["system"]["demand_array"] = []
    for sc in planning["simulation"]["scenario_array"]:
        sc["hydrology"] = None
    src = tmp_path / "plp"
    src.mkdir()
    (src / "plpbar.csv").write_text(
        "Hidro,Bloque,TipoEtapa,BarNum,BarNom,CMgBar,DemBarP\nSim  2,   1,x,1,Norte220,50,10\n",
        encoding="latin-1",
    )
    (src / "plpfal.csv").write_text(
        "Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,CenPgen,CenEgen\n"
        "Sim  1,   1,x,1,F,Fal,2,Sur220,1.0,0\n",
        encoding="latin-1",
    )
    (src / "plpcen.csv").write_text(
        "Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,CenQgen,CenPgen\n"
        "Sim  1,   2,x,5,BESS1,BAT,2,Sur220,3,3\n"
        "Sim  1,   2,x,5,NOBAT,BAT,2,Sur220,3,3\n",
        encoding="latin-1",
    )
    out = tmp_path / "out"
    res = import_plp_results(src, planning, out)
    assert any("has load but no demand" in w for w in res.warnings)
    assert any("without a plpbar.csv row" in w for w in res.warnings)
    assert any("NOBAT" in w for w in res.warnings)
    fout = res.get_blocks("Battery", "fout_sol", 5)
    assert fout is not None and fout[0, 1] == 3.0
    dual = GtoptResults(out, res.grid).block_of("Bus", "balance_dual", 1)
    assert dual[1, 0] == 50.0  # Sim 2 = second scenario (no hydrology)


def test_read_plp_csv_compressed(tmp_path: Path) -> None:
    """Compressed variants are found; blank lines skipped."""
    import gzip  # pylint: disable=import-outside-toplevel

    with gzip.open(tmp_path / "plpbar.csv.gz", "wt", encoding="latin-1") as fh:
        fh.write("Hidro , Bloque\n\nSim  1 , 1\n")
    rows = list(read_plp_csv(tmp_path / "plpbar.csv.gz"))
    assert rows == [{"Hidro": "Sim  1", "Bloque": "1"}]


@pytest.mark.skipif(gtopt_binary() is None, reason="gtopt binary not available")
def test_roundtrip_real_gtopt_run(tmp_path: Path) -> None:
    """A real gtopt run -> gtopt2plp --results -> plp2gtopt --results."""
    gtopt = gtopt_binary()
    assert gtopt is not None
    case_json = write_tiny_case(tmp_path / "case")
    out = tmp_path / "gtopt_out"
    solve_cost(gtopt, case_json, out, method="monolithic")
    case, plp = _export(tmp_path, out)
    back = _convert(plp, tmp_path / "back", "--results")
    imported = tmp_path / "back" / "results_plp"
    check_layout(imported)
    _assert_roundtrip(case, out, back, imported)
    # same total objective (PLP's operating cost = gtopt's objective here)
    obj_a = pd.read_csv(out / "solution.csv")["obj_value"].sum()
    sol_b = pd.read_csv(imported / "solution.csv")
    obj_b = sol_b[sol_b["phase"] == sol_b["phase"].min()]["obj_value"].sum()
    assert obj_b == pytest.approx(obj_a, rel=1e-4)


def test_plpidsim_maps_simulations(tmp_path: Path) -> None:
    """Sim N reads the scenario of plpidsim.dat's stage-1 hydrology."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    planning = case.planning
    for k, sc in enumerate(planning["simulation"]["scenario_array"]):
        sc["hydrology"] = 4 + k  # hydrology classes 5 and 6 (1-based)
    inp = tmp_path / "inp"
    inp.mkdir()
    (inp / "plpidsim.dat").write_text(
        "# idsim\n# n\n      2     1\n# Mes Etapa SimulInd\n   001   001   6   5\n",
        encoding="latin-1",
    )
    (inp / "plpbar.csv").write_text(
        "Hidro,Bloque,TipoEtapa,BarNum,BarNom,CMgBar,DemBarP\n"
        "Sim  1,   1,x,1,Norte220,11,0\nSim  2,   1,x,1,Norte220,22,0\n",
        encoding="latin-1",
    )
    res = PlpResultsImporter(inp, planning, inp).run()
    dual = res.get_blocks("Bus", "balance_dual", 1)
    assert dual is not None
    assert dual[:, 0].tolist() == [22.0, 11.0]  # sim 2 -> hydro 5 -> scenario 1
    # without plpidsim: Sim N is hydrology N -> no scenario matches
    res = PlpResultsImporter(inp, planning).run()
    assert any("simulations not in the case" in w for w in res.warnings)
