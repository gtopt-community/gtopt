# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for ``gtopt2plp``: files, warnings and the round trip through ``plp2gtopt``."""

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
from gtopt2plp.main import main
from gtopt2plp.writer import PlpWriter, hydro_month

MANDATORY = (
    "plpblo.dat",
    "plpeta.dat",
    "plpbar.dat",
    "plpcnfli.dat",
    "plpcnfce.dat",
    "plpdem.dat",
    "plpcosce.dat",
    "plpmance.dat",
    "plpmanli.dat",
    "plpaflce.dat",
    "plpextrac.dat",
    "plpmanem.dat",
)


def _export(
    planning: dict, tmp_path: Path, start_month: int | None = None
) -> PlpWriter:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "c.json").write_text(json.dumps(planning), encoding="utf-8")
    writer = PlpWriter(
        GtoptCase.load(src / "c.json"), tmp_path / "plp", start_month=start_month
    )
    writer.write()
    return writer


def _read_back(plp_dir: Path, out: Path, flags: list[str]) -> GtoptCase:
    proc = run_module("plp2gtopt", "-i", plp_dir, "-o", out, "--no-color", *flags)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return GtoptCase.load(out / f"{out.name}.json")


def test_hydro_month() -> None:
    assert hydro_month(4) == 1
    assert hydro_month(12) == 9
    assert hydro_month(1) == 10
    assert hydro_month(3) == 12


def test_writes_mandatory_files(tmp_path: Path) -> None:
    writer = _export(tiny_planning(), tmp_path)
    for name in MANDATORY:
        assert (tmp_path / "plp" / name).is_file(), name
    assert (tmp_path / "plp" / "plpess.dat").is_file()
    assert (tmp_path / "plp" / "centipo.csv").read_text().splitlines() == [
        "Central,Tipo_T",
        "PV1,SOL",
    ]
    assert not writer.warnings
    cnfce = (tmp_path / "plp" / "plpcnfce.dat").read_text()
    # header: 6 centrals (1 embalse, 0 serie, 2 fallas, 1 pasada, 1 bateria)
    assert "     6   1   0   2   1   1" in cnfce
    order = [
        cnfce.index(f"'{n}'")
        for n in ("LAGO", "PV1", "TERMO", "BESS1", "FALLA_Norte220")
    ]
    assert order == sorted(order)
    meta = json.loads((tmp_path / "plp" / "gtopt2plp.json").read_text())
    assert meta["plp2gtopt_flags"] == ["-y", "all"]
    assert meta["gtopt_set"] == ["model_options.strict_storage_emin=true"]


def test_roundtrip_is_lossless(tmp_path: Path) -> None:
    writer = _export(tiny_planning(), tmp_path)
    back = _read_back(tmp_path / "plp", tmp_path / "back", writer.plp2gtopt_flags())
    orig = GtoptCase.load(tmp_path / "src" / "c.json")
    assert not compare_cases(orig, back)


def test_lossy_features_warn(tmp_path: Path) -> None:
    planning = copy.deepcopy(tiny_planning())
    sysm = planning["system"]
    sysm["demand_array"].append({"uid": 3, "name": "extra", "bus": 2, "lmax": 5.0})
    sysm["generator_array"][0]["gcost"] = [[50.0, 60.0], [55.0, 55.0]]
    sysm["generator_array"][0]["expcap"] = 10.0
    sysm["battery_array"][0]["pmax_charge"] = 20.0
    sysm["battery_array"][0]["eini"] = 50.0
    sysm["junction_array"][0]["drain"] = False
    sysm["flow_array"].append(
        {"uid": 2, "name": "out", "junction": "LAGO", "direction": -1, "discharge": 1.0}
    )
    sysm["user_constraint_array"] = [{"uid": 1, "name": "uc", "expression": "x <= 1"}]
    planning["simulation"]["scenario_array"][0]["probability_factor"] = 0.7
    planning["simulation"]["scenario_array"][1]["probability_factor"] = 0.3
    planning["simulation"]["annual_discount_rate"] = 0.1
    writer = _export(planning, tmp_path)
    text = "\n".join(writer.warnings)
    for needle in (
        "demands on one bus are merged",
        "gcost varies within a stage",
        "expcap",
        "charge and discharge limits differ",
        "eini",
        "direction -1",
        "user_constraint_array",
        "-p 0.7,0.3",
        "-d 0.1",
    ):
        assert needle in text, needle
    assert writer.plp2gtopt_flags() == ["-y", "all", "-p", "0.7,0.3", "-d", "0.1"]


def test_requires_contiguous_blocks(tmp_path: Path) -> None:
    planning = tiny_planning()
    planning["simulation"]["stage_array"][1]["first_block"] = 3
    with pytest.raises(ValueError, match="contiguous"):
        _export(planning, tmp_path)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = write_tiny_case(tmp_path / "src")
    assert main([str(case), "-o", str(tmp_path / "out")]) == 0
    assert "read back with: plp2gtopt" in capsys.readouterr().out
    planning = tiny_planning()
    planning["system"]["generator_array"][0]["expcap"] = 1.0
    (tmp_path / "src" / "lossy.json").write_text(json.dumps(planning), encoding="utf-8")
    assert (
        main(
            [
                str(tmp_path / "src" / "lossy.json"),
                "-o",
                str(tmp_path / "o2"),
                "--strict",
            ]
        )
        == 2
    )


@pytest.mark.integration
def test_roundtrip_same_cost(tmp_path: Path) -> None:
    gtopt = gtopt_binary()
    if gtopt is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    writer = _export(tiny_planning(), tmp_path)
    _read_back(tmp_path / "plp", tmp_path / "back", writer.plp2gtopt_flags())
    sets = tuple(writer.gtopt_set())
    cost_a = solve_cost(gtopt, tmp_path / "src" / "c.json", tmp_path / "sa")
    cost_b = solve_cost(
        gtopt, tmp_path / "back" / "back.json", tmp_path / "sb", sets=sets
    )
    assert cost_b == pytest.approx(cost_a, rel=1e-7)


def test_cascade_side_files_and_warnings(tmp_path: Path) -> None:
    writer = _export(cascade_planning(), tmp_path)
    plp = tmp_path / "plp"
    for name in ("plpcenre.dat", "plpfilemb.dat", "plpmat.dat", "plpess.dat"):
        assert (plp / name).is_file(), name
    cnfce = (plp / "plpcnfce.dat").read_text()
    # 1 embalse (LAGO), 1 serie (BAJO), 2 pasadas (PAS, PV1), 1 bateria
    assert "'BAJO'" in cnfce
    assert "'PAS'" in cnfce
    extrac = (plp / "plpextrac.dat").read_text()
    assert "'LAGO'" in extrac
    assert "'BAJO'" in extrac
    filemb = (plp / "plpfilemb.dat").read_text()
    assert "'LAGO'" in filemb
    assert "'BAJO'" in filemb
    assert "'LAGO'" in (plp / "plpcenre.dat").read_text()
    assert "'LAGO'" in (plp / "plpmanem.dat").read_text()
    assert "'Norte_Centro'" in (plp / "plpmanli.dat").read_text()
    text = "\n".join(writer.warnings)
    for needle in (
        "time-varying production factor",
        "flow capacity is not a PLP field",
        "both a spill waterway and a drain",
        "fmin/fcost are lost",
        "emin > 0 is not exported",
        "BESS2: no bus",
        "demand lost: unknown bus",
        "bus Centro: voltage is taken",
        "several loss allocation modes",
        "tcost is not exported",
    ):
        assert needle in text, needle


def test_cascade_arc_edge_cases(tmp_path: Path) -> None:
    planning = cascade_planning()
    sysm = planning["system"]
    sysm["junction_array"][0]["drain"] = False
    sysm["waterway_array"][1]["fcost"] = 0.0
    sysm["waterway_array"].append(
        {"uid": 4, "name": "ghost", "junction_a": "nowhere", "junction_b": "BAJO"}
    )
    sysm["waterway_array"].append(
        {"uid": 5, "name": "BAJO_out", "junction_a": "BAJO", "junction_b": "LAGO"}
    )
    sysm["junction_array"][1]["drain"] = False
    sysm["turbine_array"].append({"uid": 4, "name": "loose"})
    sysm["turbine_array"].append(
        {"uid": 5, "name": "dup", "generator": "TERMO", "junction_a": "LAGO"}
    )
    sysm["turbine_array"][1]["junction_b"] = "unknown_j"
    sysm["reservoir_array"][0]["flow_conversion_rate"] = 0.001
    sysm["reservoir_array"][0]["soft_emin"] = 50.0
    sysm["reservoir_array"][0]["efin"] = 400.0
    sysm["line_array"][0]["reactance"] = 0.0
    sysm["line_array"][1]["voltage"] = [110.0, 110.0]
    sysm["bus_array"].append({"uid": 0, "name": "B0"})
    sysm["demand_array"].append(
        {"uid": 4, "name": "dup_bus", "bus": 1, "lmax": 1.0, "fcost": 500.0}
    )
    sysm["demand_array"][0]["emin"] = 1.0
    sysm["generator_array"][0]["pmin"] = [[0.0, 10.0], [0.0, 0.0]]
    writer = _export(planning, tmp_path, start_month=1)
    text = "\n".join(writer.warnings)
    for needle in (
        "waterway ghost: unknown junction",
        "turbine loose: neither junction nor flow",
        "junction LAGO: several turbines",
        "'unknown_j' unknown; arc sent to the sea",
        "flow_conversion_rate 0.001",
        "soft_emin is not exported",
        "reactance 0 is read back by PLP as a DC line",
        "time-varying voltage",
        "uid <= 0 is dropped by PLP",
        "different fail costs",
        "demand Norte220: emin is not exported",
    ):
        assert needle in text, needle
    assert "'Norte_Sur'" in (tmp_path / "plp" / "plpcnfli.dat").read_text()


def test_no_central_raises(tmp_path: Path) -> None:
    planning = tiny_planning()
    for key in list(planning["system"]):
        if key.endswith("_array") and key not in ("bus_array",):
            planning["system"][key] = []
    with pytest.raises(ValueError, match="at least one central"):
        _export(planning, tmp_path)
    (tmp_path / "empty.json").write_text(json.dumps(planning), encoding="utf-8")
    assert main([str(tmp_path / "empty.json"), "-o", str(tmp_path / "o")]) == 1


def test_python_m_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = write_tiny_case(tmp_path / "src")
    monkeypatch.setattr(
        "sys.argv", ["gtopt2plp", str(case), "-o", str(tmp_path / "out"), "-v"]
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt2plp", run_name="__main__")
    assert exc.value.code == 0
