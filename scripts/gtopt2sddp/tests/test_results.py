# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of the PSR SDDP results export (:mod:`gtopt2sddp.results`)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.testing.roundtrip import gtopt_binary
from gtopt_shared.testing.tiny_cases import (
    tiny_planning,
    write_tiny_case,
    write_tiny_results,
)
from gtopt2sddp.main import main
from gtopt2sddp.results import SddpResultsWriter, write_results
from gtopt2sddp.writer import SddpWriter
from sddp2gtopt.dat_parsers import (
    _parse_psr_block_csv_profiles,
    parse_final_volumes,
    parse_water_values,
)


def _graf(path: Path) -> tuple[list[str], dict[tuple[int, int, int], list[float]]]:
    """(header lines, {(stag, seq, blck): values}) of a graf CSV."""
    lines = path.read_text(encoding="latin-1").splitlines()
    k = next(i for i, ln in enumerate(lines) if ln.startswith("Stag"))
    rows = {}
    for ln in lines[k + 1 :]:
        cells = [c.strip() for c in ln.split(",")]
        rows[int(cells[0]), int(cells[1]), int(cells[2])] = [
            float(c) for c in cells[3:]
        ]
    return lines[: k + 1], rows


@pytest.fixture(name="exported")
def _exported(tmp_path: Path) -> Path:
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    out = tmp_path / "psr"
    write_results(case, write_tiny_results(tmp_path / "res", "hive"), out)
    return out


def test_files_and_header(exported: Path) -> None:
    """Every file, the PSR graf header and the agent columns."""
    names = {p.name for p in exported.iterdir()}
    assert names == {
        "gerter.csv",
        "gerhid.csv",
        "gergnd.csv",
        "gerbat.csv",
        "cmgbus.csv",
        "cmgdem.csv",
        "demxba.csv",
        "defbus.csv",
        "defcit.csv",
        "cirflw.csv",
        "qturbi.csv",
        "qverti.csv",
        "volfin.csv",
        "valagu.csv",
    }
    head, rows = _graf(exported / "gerter.csv")
    assert head[0].replace(" ", "") == "Variesperblock?,1,Unit,GWh,2,4,2030"
    assert head[1].replace(" ", "") == "Variespersequence?,1"
    assert head[2].replace(" ", "") == "#ofagents,1"
    assert head[3].split(",")[3].strip() == "TERMO"
    # stage-major, then series, then block of the stage
    assert list(rows) == [
        (1, 1, 1),
        (1, 1, 2),
        (1, 2, 1),
        (1, 2, 2),
        (2, 1, 1),
        (2, 1, 2),
        (2, 2, 1),
        (2, 2, 2),
    ]
    # TERMO = 10 s + b MW; stage 2 block 1 = global block 3 (12 h)
    assert rows[2, 2, 1][0] == pytest.approx((20 + 3) * 12 / 1000)


def test_values(exported: Path) -> None:
    """Units: GWh energies, $/MWh prices, MW flows, hm3, k$/hm3."""
    _, cmg = _graf(exported / "cmgbus.csv")
    assert cmg[1, 1, 2] == pytest.approx([52.0, 62.0])
    _, hyd = _graf(exported / "gerhid.csv")
    assert hyd[1, 1, 1] == pytest.approx([50 * 10 / 1000])
    _, gnd = _graf(exported / "gergnd.csv")
    assert gnd[1, 1, 1] == pytest.approx([5 * 10 / 1000])
    _, bat = _graf(exported / "gerbat.csv")
    assert bat[1, 1, 2] == pytest.approx([3 * 14 / 1000])
    _, flw = _graf(exported / "cirflw.csv")
    assert flw[2, 1, 2] == pytest.approx([20.0])
    _, dem = _graf(exported / "demxba.csv")
    # bus 2, scenario 2, block 4: served 44 + unserved 2 MW over 12 h
    assert dem[2, 2, 2][1] == pytest.approx((44 + 2) * 12 / 1000)
    _, dfb = _graf(exported / "defbus.csv")
    assert dfb[2, 2, 2] == pytest.approx([0.0, 0.024])
    _, dfc = _graf(exported / "defcit.csv")
    assert dfc[2, 2, 2] == pytest.approx([0.024])
    assert dfc[1, 1, 1] == pytest.approx([0.0])
    _, sys_cmg = _graf(exported / "cmgdem.csv")
    # demand-weighted: bus 1 30 MW at 51, bus 2 (11+5+20-3=33 MW) at 61
    assert sys_cmg[1, 1, 1][0] == pytest.approx((30 * 51 + 33 * 61) / 63)
    _, qt = _graf(exported / "qturbi.csv")
    assert qt[1, 1, 1] == pytest.approx([25.0])
    _, qv = _graf(exported / "qverti.csv")
    assert qv[2, 1, 1] == pytest.approx([1.0])  # drain in block 3
    head, vol = _graf(exported / "volfin.csv")
    assert head[0].replace(" ", "").startswith("Variesperblock?,0,Unit,hm3")
    assert vol == {
        (1, 1, 1): [450.0],
        (1, 2, 1): [480.0],
        (2, 1, 1): [400.0],
        (2, 2, 1): [300.0],
    }
    _, wv = _graf(exported / "valagu.csv")
    assert wv[1, 2, 1] == pytest.approx([2.0])  # 2000 $/hm3 -> k$/hm3


def test_sddp2gtopt_reads_files(exported: Path) -> None:
    """sddp2gtopt's PSR CSV readers parse the exported files."""
    prof = _parse_psr_block_csv_profiles(exported / "gerter.csv")
    assert list(prof) == ["TERMO"] and len(prof["TERMO"]) == 8
    assert parse_final_volumes(exported / "volfin.csv") == {"LAGO": 300.0}
    assert parse_water_values(exported / "valagu.csv") == {"LAGO": pytest.approx(1.5)}


def test_agents_match_input_export(tmp_path: Path) -> None:
    """The agent names are those written to the .dat files."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    out = tmp_path / "psr"
    w = SddpWriter(case, out)
    w.write()
    write_results(case, write_tiny_results(tmp_path / "res", "csv"), out, w)
    assert w.agents["thermal", 4] == "TERMO"
    assert w.agents["renewable", 8] == "PV1"
    assert w.agents["hydro", 1] == "LAGO"
    assert w.agents["line", 1] == "Norte_Sur"
    assert w.agents["battery", 5] == "BESS1"
    assert "TERMO" in (out / "ctermigt.dat").read_text(encoding="latin-1")
    assert (out / "gtopt2sddp.json").exists()


def test_waterway_outlet_and_spill(tmp_path: Path) -> None:
    """A turbine on a waterway reads its flow there; other arcs spill."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    p = tiny_planning()
    s = p["system"]
    s["junction_array"].append({"uid": 2, "name": "MAR", "drain": True})
    s["waterway_array"] = [
        {"uid": 1, "name": "LAGO_tur", "junction_a": "LAGO", "junction_b": "MAR"},
        {"uid": 2, "name": "LAGO_ver", "junction_a": "LAGO", "junction_b": "MAR"},
    ]
    s["turbine_array"][0] = {
        "uid": 1,
        "name": "LAGO",
        "generator": "LAGO",
        "waterway": "LAGO_tur",
        "production_factor": 2.0,
    }
    d = tmp_path / "case"
    d.mkdir()
    (d / "c.json").write_text(json.dumps(p), encoding="utf-8")
    case = GtoptCase.load(d / "c.json")
    res = write_tiny_results(tmp_path / "res", "parquet")
    (res / "Turbine" / "flow_sol.parquet").unlink()
    (res / "Waterway").mkdir()
    cells = [(sc, (b + 1) // 2, b) for sc in (1, 2) for b in (1, 2, 3, 4)]
    pd.DataFrame(
        [(*c, 1, 7.0) for c in cells] + [(*c, 2, 0.5) for c in cells],
        columns=["scenario", "stage", "block", "uid", "value"],
    ).to_parquet(res / "Waterway" / "flow_sol.parquet")
    out = tmp_path / "psr"
    write_results(case, res, out)
    _, qt = _graf(out / "qturbi.csv")
    _, qv = _graf(out / "qverti.csv")
    assert qt[1, 1, 1][0] == pytest.approx(7.0)
    assert qv[1, 1, 1][0] == pytest.approx(0.5)


def test_no_results(tmp_path: Path) -> None:
    """An output directory without results is an error."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no gtopt results"):
        SddpResultsWriter(case, tmp_path / "empty", tmp_path / "psr").write()


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--results`` adds the result files; ``--results-only`` skips .dat."""
    case = write_tiny_case(tmp_path / "case")
    res = write_tiny_results(tmp_path / "res", "wide")
    a = tmp_path / "a"
    assert main([str(case), "-o", str(a), "--results", str(res)]) == 0
    assert (a / "ctermigt.dat").exists() and (a / "cmgbus.csv").exists()
    b = tmp_path / "b"
    assert main([str(case), "-o", str(b), "--results", str(res), "--results-only"]) == 0
    assert (b / "cmgbus.csv").exists() and not (b / "ctermigt.dat").exists()
    assert "PSR result file" in capsys.readouterr().out
    assert main([str(case), "-o", str(b), "--results-only"]) == 1
    (tmp_path / "none").mkdir()
    assert main([str(case), "-o", str(b), "--results", str(tmp_path / "none")]) == 1


@pytest.mark.integration
def test_solved_tiny_case(tmp_path: Path) -> None:
    """Solve the tiny case, export PSR results and check the balances."""
    gtopt = gtopt_binary()
    if gtopt is None:
        pytest.skip("gtopt binary not found")
    case_json = write_tiny_case(tmp_path / "case")
    out_dir = tmp_path / "gt"
    proc = subprocess.run(
        [
            gtopt,
            str(case_json),
            "--set",
            f"input_directory={case_json.parent}",
            "-d",
            str(out_dir),
            "--solver",
            "highs",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    psr = tmp_path / "psr"
    write_results(GtoptCase.load(case_json), out_dir, psr)
    files = {f: _graf(psr / f)[1] for f in ("gerter.csv", "gerhid.csv", "gergnd.csv")}
    _, bat = _graf(psr / "gerbat.csv")
    _, dem = _graf(psr / "demxba.csv")
    _, dfc = _graf(psr / "defcit.csv")
    _, cmg = _graf(psr / "cmgbus.csv")
    for key, d in dem.items():
        gen = sum(sum(rows[key]) for rows in files.values()) + sum(bat[key])
        # served energy = generation + battery discharge - charge (lossless);
        # the tiny case charges only in stage 2 block 1, so check the rest
        if key not in ((2, 1, 1), (2, 2, 1)):
            assert gen == pytest.approx(sum(d) - dfc[key][0], abs=1e-3)
    assert cmg[1, 1, 1] == pytest.approx([50.0, 50.0], abs=0.01)
    assert cmg[2, 1, 2] == pytest.approx([1000.0, 1000.0], abs=0.01)
    assert parse_final_volumes(psr / "volfin.csv")
