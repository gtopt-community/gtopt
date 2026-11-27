# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of ``sddp2gtopt --results`` (:mod:`sddp2gtopt.results_import`).

Main test: the round trip ``gtopt results -> gtopt2sddp --results ->
sddp2gtopt --results`` reproduces the original streams.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.results_reader import GtoptResults
from gtopt_shared.testing.results_roundtrip import STREAMS, check_layout, stream_diffs
from gtopt_shared.testing.roundtrip import gtopt_binary, solve_cost
from gtopt_shared.testing.tiny_cases import write_tiny_case, write_tiny_results
from gtopt2sddp.results import write_results
from gtopt2sddp.writer import SddpWriter
from sddp2gtopt.main import main
from sddp2gtopt.results_import import (
    SddpResultsImporter,
    import_sddp_results,
    read_graf,
)

#: What gtopt2sddp exports (no battery state / charge / dual).
SDDP_STREAMS = tuple(
    s
    for s in STREAMS
    if (s[0], s[1])
    not in {
        ("Battery", "energy_sol"),
        ("Battery", "finp_sol"),
        ("Battery", "energy_dual"),
    }
)


def _export(tmp_path: Path, results: Path) -> tuple[GtoptCase, Path]:
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    psr = tmp_path / "psr"
    writer = SddpWriter(case, psr)
    writer.write()
    write_results(case, results, psr, writer)
    return case, psr


def _convert(psr: Path, out: Path, *extra: str) -> GtoptCase:
    main(["-i", str(psr), "-o", str(out), "--no-color", *extra])
    return GtoptCase.load(out / f"{out.name}.json")


def _assert_roundtrip(
    orig: GtoptCase,
    orig_out: Path,
    back: GtoptCase,
    back_out: Path,
    streams: tuple[tuple[str, str, str], ...] = SDDP_STREAMS,
) -> None:
    for (cls, fld, name), d in stream_diffs(
        orig, orig_out, back, back_out, streams
    ).items():
        assert not math.isnan(d), f"{cls}/{fld} {name}: missing"
        assert d <= 1e-3, f"{cls}/{fld} {name}: {d}"
    a = GtoptResults(orig_out, orig.grid)
    b = GtoptResults(back_out, back.grid)
    for fld in ("efin_sol", "eini_sol"):
        np.testing.assert_allclose(
            a.stage_of("Reservoir", fld, 1), b.stage_of("Reservoir", fld, 1), atol=1e-4
        )


@pytest.fixture(name="psr_dir")
def _psr_dir(tmp_path: Path) -> tuple[GtoptCase, Path, Path]:
    res = write_tiny_results(tmp_path / "res", "hive")
    case, psr = _export(tmp_path, res)
    return case, res, psr


def test_roundtrip_synthetic(
    psr_dir: tuple[GtoptCase, Path, Path], tmp_path: Path
) -> None:
    """gtopt2sddp results read back by sddp2gtopt --results match."""
    case, res, psr = psr_dir
    back = _convert(psr, tmp_path / "back", "--results")
    out = tmp_path / "back" / "results_sddp"
    check_layout(out)
    _assert_roundtrip(case, res, back, out)
    sol = pd.read_csv(out / "solution.csv")
    assert len(sol) == 4 and (sol["obj_value"] > 0).all()


def test_results_only_and_errors(
    psr_dir: tuple[GtoptCase, Path, Path], tmp_path: Path
) -> None:
    """--results-only against the converted JSON; error exits."""
    _case, _res, psr = psr_dir
    back = tmp_path / "back"
    _convert(psr, back)
    main(["-i", str(psr), "-o", str(back), "--results", str(psr), "--results-only"])
    check_layout(back / "results_sddp")
    with pytest.raises(SystemExit) as exc:
        main(["-i", str(psr), "-o", str(back), "--results-only"])
    assert exc.value.code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(
            ["-i", str(psr), "-o", str(back), "--results", str(empty), "--results-only"]
        )
    assert exc.value.code == 1
    with pytest.raises(SystemExit) as exc:
        _convert(psr, tmp_path / "c", "--results", str(empty))
    assert exc.value.code == 1


def test_read_graf_variants(tmp_path: Path) -> None:
    """Header detection, per-stage flag, bad rows, missing header."""
    f = tmp_path / "x.csv"
    f.write_text(
        "Varies per block?       ,0,Unit,hm3,2,4,2030\n"
        "Varies per sequence?    ,1\n"
        "# of agents             ,2\n"
        "Stag,Seq.,Blck,A           ,B\n"
        "   1,   1,   1,  1.5,\n"
        "   x,   1,   1,  9,9\n"
        "   1\n"
        "   2,   1,   1,  bad,2\n",
        encoding="latin-1",
    )
    g = read_graf(f)
    assert g.unit == "hm3" and not g.per_block
    assert g.agents["A"] == {
        (1, 1, 1): 1.5,
        (2, 1, 1): pytest.approx(float("nan"), nan_ok=True),
    }
    assert g.agents["B"] == {(2, 1, 1): 2.0}
    f.write_text("nothing here\n", encoding="latin-1")
    assert not read_graf(f).agents


def test_unmatched_agents_and_spill(
    psr_dir: tuple[GtoptCase, Path, Path], tmp_path: Path
) -> None:
    """Unknown agents warn; spill goes to the s_ waterway or the drain."""
    case, _res, psr = psr_dir
    planning = case.planning
    src = tmp_path / "edited"
    shutil.copytree(psr, src)
    head = (
        "Varies per block?       ,1,Unit,{u},2,4,2030\n"
        "Varies per sequence?    ,1\n"
        "# of agents             ,1\n"
    )
    for name, unit, agent in (
        ("gerter.csv", "GWh", "GHOST"),
        ("gerbat.csv", "GWh", "NOBAT"),
        ("cmgbus.csv", "$/MWh", "NOBUS"),
        ("cirflw.csv", "MW", "NOLINE"),
        ("qturbi.csv", "m3/s", "NOTUR"),
        ("qverti.csv", "m3/s", "NOSPILL"),
        ("volfin.csv", "hm3", "NORES"),
        ("valagu.csv", "k$/hm3", "NORES2"),
    ):
        (src / name).write_text(
            head.format(u=unit)
            + f"Stag,Seq.,Blck,{agent}\n   1,   1,   1, 5\n   9,   1,   1, 5\n   1,   1,   7, 5\n",
            encoding="latin-1",
        )
    res = SddpResultsImporter(src, planning, tmp_path).run()
    text = "\n".join(res.warnings)
    for needle in (
        "GHOST",
        "NOBAT",
        "NOBUS",
        "NOLINE",
        "NOTUR",
        "NOSPILL",
        "NORES",
        "NORES2",
        "outside the case",
    ):
        assert needle in text, needle
    # spill to a waterway named s_<plant>, else the j_<plant> drain
    planning["system"]["waterway_array"] = [
        {"uid": 3, "name": "s_LAGO", "junction_a": "LAGO"}
    ]
    (src / "qverti.csv").write_text(
        head.format(u="m3/s")
        + "Stag,Seq.,Blck,LAGO\n   1,   1,   1, 5\n   1,   1,   2, 0\n",
        encoding="latin-1",
    )
    res = SddpResultsImporter(src, planning, tmp_path).run()
    spill = res.get_blocks("Waterway", "flow_sol", 3)
    assert spill is not None and spill[0, 0] == 5.0
    del planning["system"]["waterway_array"]
    res = SddpResultsImporter(src, planning, tmp_path).run()
    drain = res.get_blocks("Junction", "drain_sol", 1)
    assert drain is not None and drain[0, 0] == 5.0
    # a turbine found through its generator (no tb_ name)
    planning["system"]["turbine_array"][0]["name"] = "T1"
    (src / "qturbi.csv").write_text(
        head.format(u="m3/s") + "Stag,Seq.,Blck,LAGO\n   1,   1,   1, 7\n",
        encoding="latin-1",
    )
    res = SddpResultsImporter(src, planning, tmp_path).run()
    tur = res.get_blocks("Turbine", "flow_sol", 1)
    assert tur is not None and tur[0, 0] == 7.0
    planning["system"]["turbine_array"][0]["generator"] = "nobody"
    res = SddpResultsImporter(src, planning, tmp_path).run()
    assert any("has no turbine" in w for w in res.warnings)


def test_no_result_files(tmp_path: Path) -> None:
    """A directory without graf files is an error."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    with pytest.raises(FileNotFoundError):
        import_sddp_results(tmp_path, case.planning, tmp_path / "out")


@pytest.mark.skipif(gtopt_binary() is None, reason="gtopt binary not available")
def test_roundtrip_real_gtopt_run(tmp_path: Path) -> None:
    """A real gtopt run -> gtopt2sddp --results -> sddp2gtopt --results."""
    gtopt = gtopt_binary()
    assert gtopt is not None
    case_json = write_tiny_case(tmp_path / "case")
    out = tmp_path / "gtopt_out"
    solve_cost(gtopt, case_json, out, method="monolithic")
    case, psr = _export(tmp_path, out)
    back = _convert(psr, tmp_path / "back", "--results")
    imported = tmp_path / "back" / "results_sddp"
    check_layout(imported)
    # valagu is a stage mean: compare the per-block water value elsewhere
    streams = tuple(s for s in SDDP_STREAMS if s[1] != "water_value_dual")
    _assert_roundtrip(case, out, back, imported, streams)
    obj_a = pd.read_csv(out / "solution.csv")["obj_value"].sum()
    sol_b = pd.read_csv(imported / "solution.csv")
    obj_b = sol_b[sol_b["phase"] == sol_b["phase"].min()]["obj_value"].sum()
    assert obj_b == pytest.approx(obj_a, rel=1e-4)
