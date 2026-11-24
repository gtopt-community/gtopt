# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Unit tests for tools/check_equilibration_invariance.py (comparison half)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_equilibration_invariance as cei  # pylint: disable=wrong-import-position

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def _write_run(
    path: Path,
    bounds: list[tuple[float, float]],
    cuts: list[tuple[int, float, float, float]],
    obj_rows: list[tuple[int, float]],
) -> Path:
    """Fake gtopt output dir: (iteration, rhs, alpha w, efin coef) cuts."""
    path.mkdir(parents=True)
    history = [{"lower_bound": lb, "upper_bound": ub} for lb, ub in bounds]
    (path / "solver_status.json").write_text(
        json.dumps({"history": history}), encoding="utf-8"
    )
    lines = ["scene,phase,obj_value"] + [f"0,{p},{v}" for p, v in obj_rows]
    (path / "solution.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = [
        {
            "type": 0,
            "phase": 1,
            "scene": 0,
            "iteration": it,
            "extra": 0,
            "rhs": rhs,
            "coeffs": [
                {"cls": "Reservoir", "var": "efin", "uid": 1, "val": c},
                {"cls": "Sddp", "var": "alpha", "uid": 0, "val": w},
            ],
        }
        for it, rhs, w, c in cuts
    ]
    (path / "cuts").mkdir()
    pq.write_table(pa.Table.from_pylist(rows), path / "cuts" / "sddp_cuts.parquet")
    return path


def test_close_and_rel_diff() -> None:
    assert cei.close(1000.0, 1000.0005, 1e-6)
    assert not cei.close(1000.0, 1000.01, 1e-6)
    assert cei.close(0.0, 1e-7, 1e-6)  # unit floor on the scale
    assert cei.rel_diff(2.0, 1.0) == pytest.approx(0.5)


def test_scaled_cut_rows_compare_equal(tmp_path: Path) -> None:
    # Same physical cut written with α coefficient 1 and 1000 (LP row
    # scaled by 1000): a and β must match after α normalisation.
    ref = _write_run(
        tmp_path / "a",
        [(1.0, 5.0), (4.0, 4.0)],
        [(1, 100.0, 1.0, 45.0), (2, 90.0, 1.0, 40.0)],
        [(1, 4.0), (2, 99.0)],
    )
    var = _write_run(
        tmp_path / "b",
        [(1.0, 5.0), (4.0, 4.0)],
        [(1, 100000.0, 1000.0, 45000.0), (2, 1.0, 1.0, 1.0)],
        [(1, 4.0), (2, 7.0)],  # later phases are ignored under SDDP
    )
    assert cei.main(["--variant", f"eq=none={ref}", "--variant", f"x={var}"]) == 0


def test_bound_and_slope_mismatch_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ref = _write_run(tmp_path / "a", [(1.0, 5.0)], [(1, 100.0, 1.0, 45.0)], [(1, 5.0)])
    var = _write_run(tmp_path / "b", [(1.1, 5.0)], [(1, 100.0, 1.0, 46.0)], [(1, 5.0)])
    assert cei.main(["--variant", f"r={ref}", "--variant", f"v={var}"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] v iter 0 LB" in out
    assert "[FAIL] v cut slopes" in out
    assert out.rstrip().splitlines()[-1].startswith("FAIL:")


def test_monolithic_sums_all_rows(tmp_path: Path) -> None:
    for name, rows in (("a", [(1, 3.0), (2, 4.0)]), ("b", [(1, 2.0), (2, 5.0)])):
        d = tmp_path / name
        d.mkdir()
        lines = ["scene,phase,obj_value"] + [f"0,{p},{v}" for p, v in rows]
        (d / "solution.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    args = ["--method", "monolithic"]
    args += ["--variant", f"a={tmp_path / 'a'}", "--variant", f"b={tmp_path / 'b'}"]
    assert cei.main(args) == 0


def test_usage_errors(tmp_path: Path) -> None:
    assert cei.main(["--variant", "nodir"]) == 2
    assert cei.main(["--variant", f"a={tmp_path / 'missing'}", "--variant", "b=x"]) == 2
    (tmp_path / "only").mkdir()
    assert cei.main(["--variant", f"a={tmp_path / 'only'}"]) == 2
