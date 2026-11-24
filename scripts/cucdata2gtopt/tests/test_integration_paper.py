# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Replicate Table I (IUC column) of Morales-Espana & Tejada-Arango (2019).

End-to-end: CUC-data workbook -> ``cucdata2gtopt`` -> gtopt MIP solve
(relative gap 1e-4, as the paper's 0.01 %) -> objective vs the paper.

The workbooks have no licence, so they are never part of gtopt: the test
uses the download cache (``$GTOPT_CUCDATA_DIR`` / ``~/.cache/gtopt/cucdata``,
filled by ``cucdata2gtopt --fetch-only``) and skips when the data is not
cached and cannot be downloaded (``GTOPT_OFFLINE=1``) or when no gtopt
binary is found.  The 118-bus cases (540 binaries x 24 h) only run with
``GTOPT_CUC_118=1``.
"""

from __future__ import annotations

import os
import resource
import subprocess
from pathlib import Path

import pytest

from cucdata2gtopt import CaseOptions, build_case, read_workbook, write_case
from cucdata2gtopt._source import DataUnavailableError, system_workbook
from gtopt_shared.gtopt_binary import find_gtopt_binary
from gtopt_shared.solution_csv import parse_solution_csv

pytestmark = [pytest.mark.integration, pytest.mark.slow]

#: Paper Table I, IUC objective [M$] (Gurobi, relative gap 0.01 %).
PAPER_IUC: dict[tuple[int, float], float] = {
    (39, 10.0): 1.0070,
    (39, 5.0): 0.9901,
    (118, 5.0): 14.4787,
    (118, 2.5): 13.9725,
}

#: Objective [M] of the *published* GAMS model on the published data,
#: reproduced by an independent transcription of
#: StarGenLite_Clustered-UC.gms solved with CPLEX (gap 1e-4).  The paper's
#: Table I values are higher by an amount proportional to the reserve
#: level (see docs/scripts/cucdata2gtopt.md): the code behind Table I is
#: not the published one.
GAMS_IUC: dict[tuple[int, float], float] = {
    (39, 10.0): 0.9808228,
    (39, 5.0): 0.9771855,
}

#: Both solves stop at a 1e-4 relative gap.
REL_TOL = 2e-4
#: Without a GAMS reference value: the documented paper gap stays < 5 %.
PAPER_REL_TOL = 0.05


def _core_dumps() -> None:
    try:
        resource.setrlimit(
            resource.RLIMIT_CORE, (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
        )
    except (ValueError, OSError):
        pass


def _solve(case_json: Path, out_dir: Path, gtopt: str) -> float:
    proc = subprocess.run(
        [
            gtopt,
            case_json.name,
            "--mip-gap",
            "1e-4",
            "--set",
            f"output_directory={out_dir}",
        ],
        cwd=case_json.parent,
        capture_output=True,
        text=True,
        check=False,
        preexec_fn=_core_dumps,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    row = parse_solution_csv((out_dir / "solution.csv").read_text(encoding="utf-8"))
    assert row.get("status") == "0", row
    return float(row["obj_value"])


@pytest.mark.parametrize(("system", "reserve"), sorted(PAPER_IUC))
def test_iuc_objective_matches_paper(
    system: int, reserve: float, tmp_path: Path
) -> None:
    if system == 118 and os.environ.get("GTOPT_CUC_118", "") in ("", "0"):
        pytest.skip("118-bus IUC is long; set GTOPT_CUC_118=1 to run it")
    gtopt = find_gtopt_binary()
    if gtopt is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    try:
        workbook = system_workbook(system)
    except DataUnavailableError as exc:
        pytest.skip(f"CUC-data not cached and not downloadable: {exc}")

    case = build_case(
        read_workbook(workbook), CaseOptions(system=system, reserve_pct=reserve)
    )
    case_json = write_case(case, tmp_path)
    objective = _solve(case_json, tmp_path / "out", gtopt) / 1e6
    paper = PAPER_IUC[(system, reserve)]
    print(
        f"IUC {system}-bus reserve {reserve} %: gtopt {objective:.6f} M, "
        f"paper {paper} M ({100 * (objective / paper - 1):+.2f} %)"
    )
    reference = GAMS_IUC.get((system, reserve))
    if reference is not None:
        assert objective == pytest.approx(reference, rel=REL_TOL)
    assert objective == pytest.approx(paper, rel=PAPER_REL_TOL)


@pytest.mark.parametrize(("system", "reserve"), sorted(GAMS_IUC))
def test_block_mode_relaxes_iuc(system: int, reserve: float, tmp_path: Path) -> None:
    """``--mode block`` (non-chronological stage) is a relaxation of the IUC.

    gtopt's block commitment keeps ``Pmin u <= g <= Pmax u`` per hour but
    drops start-up / shut-down / no-load costs, min up / down and ramps, so
    its objective is below the IUC (39-bus: -12.5 % / -12.1 %, mostly the
    dropped no-load cost) -- far from the paper's CCUC error (-0.7 %).
    """
    gtopt = find_gtopt_binary()
    if gtopt is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    try:
        workbook = system_workbook(system)
    except DataUnavailableError as exc:
        pytest.skip(f"CUC-data not cached and not downloadable: {exc}")
    case = build_case(
        read_workbook(workbook),
        CaseOptions(system=system, reserve_pct=reserve, mode="block"),
    )
    objective = _solve(write_case(case, tmp_path), tmp_path / "out", gtopt) / 1e6
    reference = GAMS_IUC[(system, reserve)]
    print(
        f"block {system}-bus reserve {reserve} %: {objective:.6f} M "
        f"({100 * (objective / reference - 1):+.2f} % vs IUC)"
    )
    assert objective <= reference * (1 + REL_TOL)
