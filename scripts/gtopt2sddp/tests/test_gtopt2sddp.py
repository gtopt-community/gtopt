# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for ``gtopt2sddp``: layout, warnings and the round trip through ``sddp2gtopt``."""

from __future__ import annotations

import copy
import runpy
import json
from pathlib import Path

import pytest

from gtopt_shared.case_compare import compare_cases
from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.testing.roundtrip import gtopt_binary, run_module, solve_cost
from gtopt_shared.testing.tiny_cases import (
    cascade_planning,
    tiny_planning,
    write_tiny_case,
)
from gtopt2sddp.fixed_width import Column, fmt_fixed7, fmt_num, header, record
from gtopt2sddp.main import main
from gtopt2sddp.writer import SddpWriter
from sddp2gtopt.sddp_dat_parsers import header_spans


def _export(planning: dict, tmp_path: Path, **kw: object) -> SddpWriter:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "c.json").write_text(json.dumps(planning), encoding="utf-8")
    writer = SddpWriter(GtoptCase.load(src / "c.json"), tmp_path / "psr", **kw)  # type: ignore[arg-type]
    writer.write()
    return writer


def _read_back(psr_dir: Path, out: Path) -> GtoptCase:
    proc = run_module("sddp2gtopt", "-i", psr_dir, "-o", out)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return GtoptCase.load(out / f"{out.name}.json")


def test_fmt_num() -> None:
    assert fmt_num(None, 5) == ""
    assert fmt_num(float("nan"), 5) == ""
    assert fmt_num(3, 4) == "3"
    assert fmt_num(True, 4) == "1"
    assert fmt_num(100.0, 8) == "100.0"
    assert fmt_num(1.0 / 3.0, 8) == "0.333333"
    assert float(fmt_num(123456.789, 7)) == pytest.approx(123457.0)
    with pytest.raises(ValueError):
        fmt_num(123456, 3)


def test_fmt_fixed7() -> None:
    for v in (0.0, 1.5, 20.0, 12345.6, 999999.0):
        text = fmt_fixed7(v)
        assert len(text) == 7
        assert float(text) == pytest.approx(v, rel=1e-4)
    assert fmt_fixed7(-5.0) == "0.00000"
    with pytest.raises(ValueError):
        fmt_fixed7(1e9)


def test_header_record_align_with_reader() -> None:
    cols = [Column("Num", 5), Column("Nombre", 12, text=True), Column("PotIns", 10)]
    head = header(cols, lead="!")
    line = record(cols, [7, "A_LONG_PLANT_NAME", 12.5], lead=" ")
    spans = {
        sp.label: (sp.num_start, sp.end)
        for sp in header_spans(head.replace("!", " ", 1))
    }
    a, b = spans["PotIns"]
    assert float(line[a:b]) == 12.5
    start = next(sp.start for sp in header_spans(head) if sp.label == "Nombre")
    assert line[start : start + 12].strip() == "A_LONG_PLANT"


def test_writes_files(tmp_path: Path) -> None:
    writer = _export(tiny_planning(), tmp_path, start_month=4)
    psr = tmp_path / "psr"
    for name in (
        "sddp.dat",
        "sistem.dat",
        "dbus.dat",
        "volt.dat",
        "busconnection.dat",
        "dcirc.dat",
        "mcirc.dat",
        "duvame002.dat",
        "deme002gt.dat",
        "dbf002gt.dat",
        "ctermigt.dat",
        "mtermigt.dat",
        "rgme002gt.dat",
        "chidrogt.dat",
        "hinflw.dat",
        "cgndgt.dat",
        "gndsitegt.dat",
        "blrenw002gt.dat",
        "cbatterygt.dat",
    ):
        assert (psr / name).is_file(), name
    assert not writer.warnings
    sddp = (psr / "sddp.dat").read_text()
    assert "NUMERO DE SERIES             2" in sddp
    assert "MES/SEMANA INICIAL           4" in sddp


def test_roundtrip_is_lossless(tmp_path: Path) -> None:
    _export(tiny_planning(), tmp_path, start_month=4)
    back = _read_back(tmp_path / "psr", tmp_path / "back")
    orig = GtoptCase.load(tmp_path / "src" / "c.json")
    assert not compare_cases(orig, back)


def test_per_unit_case_keeps_reactance(tmp_path: Path) -> None:
    planning = copy.deepcopy(tiny_planning())
    for b in planning["system"]["bus_array"]:
        del b["voltage"]
    line = planning["system"]["line_array"][0]
    del line["voltage"]
    line["reactance"] = 0.0421
    writer = _export(planning, tmp_path, start_month=4)
    assert not any("floored" in w for w in writer.warnings)
    back = _read_back(tmp_path / "psr", tmp_path / "back")
    assert back.elements("line_array")[0]["reactance"] == pytest.approx(0.0421)
    assert back.elements("line_array")[0]["voltage"] == pytest.approx(1.0)


def test_lossy_features_warn(tmp_path: Path) -> None:
    planning = copy.deepcopy(tiny_planning())
    sysm = planning["system"]
    sysm["line_array"][0]["tmax_ba"] = [[90.0, 90.0], [80.0, 80.0]]
    sysm["demand_array"].append({"uid": 3, "name": "extra", "bus": 2, "lmax": 5.0})
    sysm["junction_array"][0]["drain"] = False
    sysm["reservoir_array"][0]["efin"] = 400.0
    sysm["battery_array"][0]["pmax_charge"] = 20.0
    planning["simulation"]["scenario_array"][0]["probability_factor"] = 0.7
    planning["simulation"]["scenario_array"][1]["probability_factor"] = 0.3
    writer = _export(planning, tmp_path, start_month=4)
    text = "\n".join(writer.warnings)
    for needle in (
        "asymmetric limits",
        "merged",
        "PSR always allows spill",
        "efin",
        "charge and discharge limits differ",
        "probabilities become equal",
    ):
        assert needle in text, needle


def test_rejects_uneven_blocks(tmp_path: Path) -> None:
    planning = tiny_planning()
    sim = planning["simulation"]
    sim["block_array"].append({"uid": 5, "duration": 1.0})
    sim["stage_array"][1]["count_block"] = 3
    with pytest.raises(ValueError, match="same number of blocks"):
        _export(planning, tmp_path)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = write_tiny_case(tmp_path / "src")
    assert main([str(case), "-o", str(tmp_path / "out"), "--sfx", "ab"]) == 0
    assert (tmp_path / "out" / "ctermiab.dat").is_file()
    assert "wrote" in capsys.readouterr().out
    assert main([str(case), "-o", str(tmp_path / "o2"), "--sfx", "abc"]) == 1


@pytest.mark.integration
def test_roundtrip_same_cost(tmp_path: Path) -> None:
    gtopt = gtopt_binary()
    if gtopt is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    _export(tiny_planning(), tmp_path, start_month=4)
    _read_back(tmp_path / "psr", tmp_path / "back")
    cost_a = solve_cost(gtopt, tmp_path / "src" / "c.json", tmp_path / "sa")
    cost_b = solve_cost(gtopt, tmp_path / "back" / "back.json", tmp_path / "sb")
    assert cost_b == pytest.approx(cost_a, rel=1e-7)


def test_cascade_warnings(tmp_path: Path) -> None:
    writer = _export(cascade_planning(), tmp_path, start_month=4)
    psr = tmp_path / "psr"
    assert (psr / "gtopt2sddp.json").is_file()
    meta = json.loads((psr / "gtopt2sddp.json").read_text())
    assert "BAJO" in meta["hydro_plants"]
    text = "\n".join(writer.warnings)
    for needle in (
        "turbine PAS: junction unknown",
        "more than two outgoing waterways; LAGO_ext dropped",
        "flow PAS_Q: no junction",
        "time-varying emin/emax",
        "hydro generator BAJO: gcost is 0",
        "production factor varies",
        "line limits vary within a stage",
        "retired mid-horizon",
        "no voltage in a case with kV buses",
        "floored by PSR",
        "demand lost: unknown bus",
        "BESS2: no bus",
    ):
        assert needle in text, needle


def test_waterway_turbine_and_edge_cases(tmp_path: Path) -> None:
    planning = cascade_planning()
    sim = planning["simulation"]
    sim["stage_array"][1]["month"] = "june"
    sysm = planning["system"]
    # BAJO's turbine sits on a waterway; MAR is a plant fed by an outlet
    sysm["junction_array"] += [
        {"uid": 3, "name": "MAR", "drain": True, "drain_capacity": 10.0},
        {"uid": 4, "name": "SECO"},
    ]
    sysm["waterway_array"] += [
        {
            "uid": 4,
            "name": "BAJO_gen",
            "junction_a": "BAJO",
            "junction_b": "MAR",
            "fmax": 40.0,
        },
        {
            "uid": 5,
            "name": "SECO_out",
            "junction_a": "SECO",
            "junction_b": "MAR",
            "fmax": 7.0,
            "fmin": 1.0,
        },
    ]
    bajo = sysm["turbine_array"][1]
    del bajo["junction_a"]
    del bajo["capacity"]
    bajo["waterway"] = "BAJO_gen"
    bajo["production_factor"] = 1.0
    sysm["turbine_array"].append({"uid": 5, "name": "dup", "junction_a": "LAGO"})
    sysm["reservoir_array"] += [
        {"uid": 2, "name": "nowhere", "junction": "zzz", "emax": 1.0},
        {"uid": 3, "name": "LAGO2", "junction": "LAGO", "emax": 1.0},
        {"uid": 4, "name": "FLAT", "junction": "SECO", "emin": 5.0, "emax": 5.0},
    ]
    lago = sysm["reservoir_array"][0]
    lago["flow_conversion_rate"] = 0.001
    lago["efin_cost"] = 3.0
    lago["spillway_cost"] = 7.0
    sysm["junction_array"][1]["drain_cost"] = 2.0
    sysm["flow_array"] += [
        {"uid": 3, "name": "out", "junction": "LAGO", "direction": -1},
        {
            "uid": 4,
            "name": "wiggle",
            "junction": "SECO",
            "discharge": [[1.0, 2.0], [3.0, 3.0]],
        },
    ]
    sysm["generator_array"][2]["gcost"] = -1.0
    sysm["generator_array"].append(
        {"uid": 9, "name": "ZERO", "bus": 1, "pmax": 0.0, "gcost": 10.0}
    )
    sysm["generator_array"].append(
        {"uid": 10, "name": "ORPHAN", "bus": 77, "pmax": 5.0, "gcost": 10.0}
    )
    sysm["generator_array"][0]["pmin"] = [[0.0, 10.0], [5.0, 5.0]]
    sysm["generator_array"][0]["gcost"] = [[50.0, 60.0], [55.0, 55.0]]
    sysm["generator_array"][0]["startup_cost"] = 3.0
    sysm["line_array"].append(
        {"uid": 4, "name": "dead", "bus_a": 1, "bus_b": 2, "active": 0.0}
    )
    sysm["line_array"].append({"uid": 5, "name": "bad", "bus_a": 1, "bus_b": 55})
    sysm["line_array"][1]["reactance"] = "rx"
    sysm["demand_array"][0]["fcost"] = 2000.0
    sysm["demand_array"][1]["ecost"] = 5.0
    sysm["demand_array"][1]["fcost"] = 1500.0
    sysm["battery_array"][0]["emax"] = [160.0, 150.0]
    writer = _export(planning, tmp_path)
    text = "\n".join(writer.warnings)
    for needle in (
        "stage 2 is june",
        "reservoir nowhere: junction unknown",
        "junction LAGO: several reservoirs",
        "junction LAGO: several turbines",
        "waterway SECO_out: fmin > 0",
        "withdrawals (direction -1)",
        "inflows vary within a stage",
        "flow_conversion_rate 0.001",
        "efin_cost is not exported",
        "VMax <= VMin",
        "drain_capacity is not exported",
        "spill costs differ",
        "negative O&M clipped",
        "ZERO has pmax <= 0",
        "ORPHAN: bus 77",
        "pmin varies within a stage",
        "gcost varies within a stage",
        "startup_cost is not exported",
        "line dead: never active",
        "line bad: unknown bus",
        "time-varying R/X",
        "per-demand fail costs differ",
        "ecost is not exported",
        "time-varying emax",
    ):
        assert needle in text, needle


def test_long_names_stay_unique(tmp_path: Path) -> None:
    planning = tiny_planning()
    gens = planning["system"]["generator_array"]
    gens[0]["name"] = "THERMAL_PLANT_ALPHA"
    gens.append(dict(gens[0], uid=7, name="THERMAL_PLANT_ALPHB"))
    writer = _export(planning, tmp_path, start_month=4)
    assert writer.names.map["THERMAL_PLAN"] == "THERMAL_PLANT_ALPHA"
    assert writer.names.map["THERMAL_PL_2"] == "THERMAL_PLANT_ALPHB"


def test_rejects_zero_duration(tmp_path: Path) -> None:
    planning = tiny_planning()
    planning["simulation"]["block_array"][0]["duration"] = 0.0
    with pytest.raises(ValueError, match="duration"):
        _export(planning, tmp_path)


def test_python_m_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = write_tiny_case(tmp_path / "src")
    monkeypatch.setattr(
        "sys.argv", ["gtopt2sddp", str(case), "-o", str(tmp_path / "out"), "-v"]
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt2sddp", run_name="__main__")
    assert exc.value.code == 0
    planning = tiny_planning()
    planning["simulation"]["block_array"][0]["duration"] = 0.0
    (tmp_path / "bad.json").write_text(json.dumps(planning), encoding="utf-8")
    assert main([str(tmp_path / "bad.json"), "-o", str(tmp_path / "o")]) == 1
