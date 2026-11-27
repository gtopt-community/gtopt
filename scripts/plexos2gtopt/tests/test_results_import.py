# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of ``plexos2gtopt --results`` (:mod:`plexos2gtopt.results_import`).

Main test: the round trip ``gtopt results -> gtopt2plexos --results ->
plexos2gtopt --results`` reproduces the original streams (reservoir
volumes in the PLEXOS volume unit the converted case uses).
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.testing.results_roundtrip import STREAMS, check_layout, stream_diffs
from gtopt_shared.testing.roundtrip import gtopt_binary, solve_cost
from gtopt_shared.testing.tiny_cases import tiny_planning, write_tiny_results
from gtopt2plexos.results import SOLUTION_DIR, PlexosResultsWriter
from gtopt2plexos.writer import PlexosWriter
from plexos2gtopt.main import main
from plexos2gtopt.plexos_solution import PlexosSolution, csv_table_source
from plexos2gtopt.results_import import (
    PlexosResultsImporter,
    import_plexos_results,
    open_results,
)

#: Volume factor CMD / hm3 of the tiny reservoir (fcr 0.0036).
K_TINY = (1.0 / 24.0) / 0.0036

#: What gtopt2plexos exports.
PLEXOS_STREAMS = (
    *(
        s
        for s in STREAMS
        if s[1] not in ("water_value_dual", "energy_dual") and s[0] != "Turbine"
    ),
    ("Reservoir", "energy_sol", "reservoir_array"),
)


def single_scenario(planning: dict[str, Any]) -> dict[str, Any]:
    """``planning`` cut to scenario 1 (PLEXOS carries one scenario)."""
    p = copy.deepcopy(planning)
    p["simulation"]["scenario_array"] = [{"uid": 1, "probability_factor": 1.0}]
    sysm = p["system"]
    for fl in sysm["flow_array"]:
        fl["discharge"] = fl["discharge"][:1]
    for prof in sysm["capacity_profile_array"]:
        prof["profile"] = prof["profile"][:1]
    return p


def _export(
    planning: dict[str, Any], tmp_path: Path, results: Path
) -> tuple[GtoptCase, Path]:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "c.json").write_text(json.dumps(planning), encoding="utf-8")
    case = GtoptCase.load(src / "c.json")
    writer = PlexosWriter(case, tmp_path / "plexos")
    writer.write()
    PlexosResultsWriter(
        case, results, writer.out_dir / SOLUTION_DIR, writer=writer
    ).write()
    return case, writer.out_dir


def _convert(px: Path, out: Path, *extra: str) -> GtoptCase:
    main(
        [
            "-i",
            str(px),
            "-o",
            str(out),
            "--block-layout",
            str(px / "block_layout.csv"),
            "--line-losses-mode",
            "none",
            "--no-reservoir-flow-estimate",
            *extra,
        ]
    )
    return GtoptCase.load(out / f"{px.name}.json")


def _assert_roundtrip(
    orig: GtoptCase, orig_out: Path, back: GtoptCase, back_out: Path, scale: float
) -> None:
    diffs = stream_diffs(
        orig, orig_out, back, back_out, PLEXOS_STREAMS, scale={"Reservoir": scale}
    )
    for (cls, fld, name), d in diffs.items():
        assert not math.isnan(d), f"{cls}/{fld} {name}: missing"
        assert d <= 1e-3 * max(scale, 1.0), f"{cls}/{fld} {name}: {d}"


def test_roundtrip_synthetic(tmp_path: Path) -> None:
    """gtopt2plexos solution tables read back by plexos2gtopt --results."""
    res = tmp_path / "res"
    write_tiny_results(res, "hive")
    # tiny_results has no per-block reservoir volume: derive it.
    vol = pd.DataFrame(
        [(1, 1 + (b > 2), b, 1, 500.0 - 10 * b) for b in (1, 2, 3, 4)],
        columns=["scenario", "stage", "block", "uid", "value"],
    )
    vol.to_parquet(res / "Reservoir" / "energy_sol.parquet", index=False)
    case, px = _export(single_scenario(tiny_planning()), tmp_path, res)
    back = _convert(px, tmp_path / "back", "--results")
    out = tmp_path / "back" / "results_plexos"
    check_layout(out)
    _assert_roundtrip(case, res, back, out, K_TINY)
    sol = pd.read_csv(out / "solution.csv")
    assert sol["obj_value"].iloc[0] > 0


def test_multi_scenario_samples(tmp_path: Path) -> None:
    """Scenario s of a multi-scenario case reads PLEXOS sample s."""
    res = write_tiny_results(tmp_path / "res", "hive")
    case, px = _export(tiny_planning(), tmp_path, res)
    sol = open_results(px / SOLUTION_DIR)
    assert sol is not None
    out = tmp_path / "imp"
    imp = import_plexos_results(sol, tiny_planning(), out, tmp_path)
    check_layout(out)
    diffs = stream_diffs(
        case, res, GtoptCase(imp.planning, tmp_path), out, PLEXOS_STREAMS[:-1]
    )
    assert max(diffs.values()) <= 1e-3, diffs


def test_mean_sample_fallback_and_warnings(tmp_path: Path) -> None:
    """No per-sample data: every scenario gets sample 0; unknown objects warn."""
    tables = {
        "t_class": "class_id,name\n1,System\n2,Generator\n3,Node\n4,Storage\n",
        "t_collection": (
            "collection_id,parent_class_id,child_class_id,name\n"
            "1,1,2,Generators\n2,1,3,Nodes\n3,1,4,Storages\n"
        ),
        "t_property": (
            "property_id,collection_id,name,unit_id\n1,1,Generation,0\n"
            "2,2,Price,0\n3,3,End Volume,0\n4,3,Shadow Price,0\n"
            "5,3,Initial Volume,0\n"
        ),
        "t_object": (
            "object_id,class_id,name\n1,1,System\n2,2,TERMO\n3,2,GHOST\n"
            "4,3,Sur220\n5,3,NONODE\n6,4,LAGO\n7,4,NOSTORE\n"
        ),
        "t_membership": (
            "membership_id,parent_class_id,child_class_id,collection_id,"
            "parent_object_id,child_object_id\n1,1,2,1,1,2\n2,1,2,1,1,3\n"
            "3,1,3,2,1,4\n4,1,3,2,1,5\n5,1,4,3,1,6\n6,1,4,3,1,7\n"
        ),
        "t_key": (
            "key_id,membership_id,model_id,phase_id,property_id,period_type_id,"
            "band_id,sample_id,timeslice_id\n"
            "1,1,1,3,1,0,1,0,0\n2,2,1,3,1,0,1,0,0\n3,3,1,3,2,0,1,0,0\n"
            "4,4,1,3,2,0,1,0,0\n5,5,1,3,3,0,1,0,0\n6,6,1,3,3,0,1,0,0\n"
            "7,5,1,3,4,0,1,0,0\n8,5,1,3,5,0,1,0,0\n"
        ),
        "t_data_0": (
            "key_id,period_id,value\n1,1,10\n1,2,20\n1,9,99\n2,1,1\n3,1,60\n"
            "4,1,1\n5,1,400\n5,2,390\n5,3,380\n5,4,370\n6,1,1\n7,1,3.5\n8,1,450\n"
        ),
    }
    sol = PlexosSolution(csv_table_source(tables))
    imp = PlexosResultsImporter(sol, tiny_planning(), tmp_path).run()
    text = "\n".join(imp.warnings)
    for needle in ("GHOST", "NONODE", "NOSTORE", "sample-0 mean", "9 periods"):
        assert needle in text, needle
    gen = imp.get_blocks("Generator", "generation_sol", 4)
    assert gen is not None and gen[:, 1].tolist() == [20.0, 20.0]
    eini = imp.stage_data[("Reservoir", "eini_sol")][1]
    assert eini[:, 0].tolist() == [450.0, 450.0]
    wv = imp.get_blocks("Reservoir", "water_value_dual", 1)
    assert wv is not None and wv[0, 0] == 3.5
    # Initial Volume absent: the planning eini, then the first block
    assert (
        PlexosResultsImporter._initial({"eini": 7.0}, None, np.ones((2, 4)))[0] == 7.0
    )
    assert PlexosResultsImporter._initial({}, None, 2 * np.ones((2, 4)))[0] == 2.0


def test_empty_solution_and_open_errors(tmp_path: Path) -> None:
    """A solution without the read properties; bad --results paths."""
    sol = PlexosSolution(csv_table_source({}))
    with pytest.raises(ValueError, match="none of the read"):
        PlexosResultsImporter(sol, tiny_planning()).run()
    with pytest.raises(FileNotFoundError):
        open_results(tmp_path)  # a dir without t_*.csv
    with pytest.raises(FileNotFoundError):
        open_results(tmp_path / "missing.accdb")
    bad_zip = tmp_path / "RES.zip"
    bad_zip.write_bytes(b"not a zip")
    with pytest.raises(FileNotFoundError):
        open_results(bad_zip)


def test_cli_results_only_and_errors(tmp_path: Path) -> None:
    """--results-only, the plexos_solution auto-discovery and error exits."""
    res = write_tiny_results(tmp_path / "res", "hive")
    _case, px = _export(single_scenario(tiny_planning()), tmp_path, res)
    back = tmp_path / "back"
    _convert(px, back)
    base = ["-i", str(px), "-o", str(back)]
    main(
        [*base, "--results", "--results-only", "--results-output", str(tmp_path / "o")]
    )
    check_layout(tmp_path / "o")
    with pytest.raises(SystemExit) as exc:
        main([*base, "--results-only"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main([*base, "--results", str(tmp_path / "nope"), "--results-only"])
    assert exc.value.code == 1
    (px / SOLUTION_DIR).rename(px / "moved")
    with pytest.raises(SystemExit) as exc:
        main([*base, "--results", "--results-only"])
    assert exc.value.code == 1


@pytest.mark.skipif(gtopt_binary() is None, reason="gtopt binary not available")
def test_roundtrip_real_gtopt_run(tmp_path: Path) -> None:
    """A real gtopt run -> gtopt2plexos --results -> plexos2gtopt --results."""
    gtopt = gtopt_binary()
    assert gtopt is not None
    planning = single_scenario(tiny_planning())
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case_json = case_dir / "tiny.json"
    case_json.write_text(json.dumps(planning), encoding="utf-8")
    out = tmp_path / "gtopt_out"
    solve_cost(gtopt, case_json, out, method="monolithic")
    case, px = _export(planning, tmp_path, out)
    back = _convert(px, tmp_path / "back", "--results")
    imported = tmp_path / "back" / "results_plexos"
    check_layout(imported)
    _assert_roundtrip(case, out, back, imported, K_TINY)
