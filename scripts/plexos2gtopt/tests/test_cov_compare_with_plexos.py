# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""End-to-end + edge coverage for :mod:`plexos2gtopt.compare_with_plexos`.

A small synthetic PLEXOS solution database (served through a fake
``mdb-export``) and a matching gtopt case directory (bundle JSON +
output parquets) drive :func:`main` through every Step, then focused
tests cover the error / fallback branches of the individual helpers.
"""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from plexos2gtopt import compare_with_plexos as cwp

# ---------------------------------------------------------------------------
# Synthetic PLEXOS solution database
# ---------------------------------------------------------------------------

#: period_id -> duration (hours)
DURATIONS = {1: 2, 2: 3}

P = cwp  # short alias for the PLEXOS property constants


@dataclass
class PObj:
    """One PLEXOS object with per-period property series."""

    name: str
    class_id: str
    category: str = ""
    props: dict[int, list[float]] = field(default_factory=dict)


def _default_objects() -> list[PObj]:
    return [
        PObj(
            "T1",
            "2",
            "Thermal Gen N. Zone",
            {
                P.PLEXOS_PROP_GENERATOR_GENERATION: [100.0, 80.0],
                P.PLEXOS_PROP_GENERATOR_SRMC: [50.0, 60.0],
                P.PLEXOS_PROP_GENERATOR_GENCOST: [10000.0, 14400.0],
                P.PLEXOS_PROP_GENERATOR_STARTUP_COST: [500.0, 0.0],
                P.PLEXOS_PROP_GENERATOR_UNITS_GENERATING: [1.0, 0.0],
            },
        ),
        PObj(
            "H1_U1",
            "2",
            "Hydro Gen Group A",
            {
                P.PLEXOS_PROP_GENERATOR_GENERATION: [50.0, 50.0],
                P.PLEXOS_PROP_GENERATOR_SRMC: [0.0, 0.0],
                P.PLEXOS_PROP_GENERATOR_UNITS_GENERATING: [0.0, 1.0],
            },
        ),
        PObj(
            "S1_FV",
            "2",
            "Solar Farms",
            {P.PLEXOS_PROP_GENERATOR_GENERATION: [20.0, 0.0]},
        ),
        PObj(
            "W1",
            "2",
            "Wind Farms",
            {P.PLEXOS_PROP_GENERATOR_GENERATION: [10.0, 10.0]},
        ),
        PObj(
            "P_ONLY",
            "2",
            "Termicas Ficticias",
            {
                P.PLEXOS_PROP_GENERATOR_GENERATION: [5.0, 5.0],
                P.PLEXOS_PROP_GENERATOR_SRMC: [90.0, 90.0],
                P.PLEXOS_PROP_GENERATOR_UNITS_GENERATING: [1.0, 1.0],
            },
        ),
        PObj(
            "B1",
            "22",
            props={
                P.PLEXOS_PROP_NODE_LOAD: [80.0, 80.0],
                P.PLEXOS_PROP_NODE_PRICE: [40.0, 45.0],
                P.PLEXOS_PROP_NODE_UNSERVED: [0.0, 1.0],
            },
        ),
        PObj(
            "B2",
            "22",
            props={
                P.PLEXOS_PROP_NODE_LOAD: [70.0, 70.0],
                P.PLEXOS_PROP_NODE_PRICE: [42.0, 50.0],
            },
        ),
        PObj("B_PONLY", "22", props={P.PLEXOS_PROP_NODE_PRICE: [1.0, 1.0]}),
        PObj(
            "L1",
            "24",
            props={
                P.PLEXOS_PROP_LINE_FLOW: [60.0, -30.0],
                P.PLEXOS_PROP_LINE_EXPORT_LIMIT: [100.0, 100.0],
                P.PLEXOS_PROP_LINE_IMPORT_LIMIT: [100.0, 100.0],
            },
        ),
        PObj(
            "L_HOT",
            "24",
            props={
                P.PLEXOS_PROP_LINE_FLOW: [150.0, 120.0],
                P.PLEXOS_PROP_LINE_EXPORT_LIMIT: [76.0, 76.0],
                P.PLEXOS_PROP_LINE_IMPORT_LIMIT: [76.0, 76.0],
            },
        ),
        PObj(
            "L_IDLE",
            "24",
            props={
                P.PLEXOS_PROP_LINE_EXPORT_LIMIT: [50.0, 50.0],
                P.PLEXOS_PROP_LINE_IMPORT_LIMIT: [50.0, 50.0],
            },
        ),
        PObj(
            "BAT1",
            "7",
            props={
                P.PLEXOS_PROP_BATTERY_LOAD: [10.0, 0.0],
                P.PLEXOS_PROP_BATTERY_GENERATION: [0.0, 8.0],
            },
        ),
        PObj("BAT_PONLY", "7", props={P.PLEXOS_PROP_BATTERY_LOAD: [1.0, 1.0]}),
        PObj(
            "RES1",
            "8",
            props={
                P.PLEXOS_PROP_STORAGE_INITIAL_VOLUME: [1000.0, 1000.0],
                P.PLEXOS_PROP_STORAGE_END_VOLUME: [900.0, 850.0],
                P.PLEXOS_PROP_STORAGE_SOC: [950.0, 880.0],
                P.PLEXOS_PROP_STORAGE_SHADOW_PRICE: [12.0, 14.0],
            },
        ),
        PObj(
            "RES_PONLY",
            "8",
            props={
                P.PLEXOS_PROP_STORAGE_END_VOLUME: [5.0, 5.0],
                P.PLEXOS_PROP_STORAGE_SHADOW_PRICE: [3.0, 3.0],
            },
        ),
        PObj(
            "REG",
            "200",
            props={
                P.PLEXOS_PROP_REGION_LOAD: [160.0, 160.0],
                P.PLEXOS_PROP_REGION_GENERATION: [170.0, 150.0],
                P.PLEXOS_PROP_REGION_LOSSES: [2.0, 3.0],
            },
        ),
    ]


def build_tables(objs: list[PObj]) -> dict[str, list[dict[str, str]]]:
    """Expand :class:`PObj` rows into the PLEXOS solution tables."""
    cats: dict[str, int] = {}
    t_object, t_membership, t_key, t_data = [], [], [], []
    kid = 0
    for oid, obj in enumerate(objs, start=1):
        cat_id = 0
        if obj.category:
            cat_id = cats.setdefault(obj.category, 100 + len(cats))
        t_object.append(
            {
                "object_id": str(oid),
                "class_id": obj.class_id,
                "name": obj.name,
                "category_id": str(cat_id),
            }
        )
        mid = 1000 + oid
        t_membership.append(
            {
                "membership_id": str(mid),
                "parent_object_id": "0",
                "child_object_id": str(oid),
            }
        )
        for pid, series in obj.props.items():
            kid += 1
            t_key.append(
                {"key_id": str(kid), "membership_id": str(mid), "property_id": str(pid)}
            )
            for period, val in enumerate(series, start=1):
                t_data.append(
                    {"key_id": str(kid), "period_id": str(period), "value": str(val)}
                )
    # Malformed / orphan rows every reader must tolerate.
    t_key.append({"key_id": "x", "membership_id": "1", "property_id": "2"})
    t_key.append({"key_id": "9999", "membership_id": "424242", "property_id": "2"})
    t_data.append({"key_id": "x", "period_id": "1", "value": "1"})
    t_data.append({"key_id": "1", "period_id": "z", "value": "1"})
    t_data.append({"key_id": "9998", "period_id": "1", "value": "1"})
    t_phase_3 = [
        {"interval_id": str(iv), "period_id": str(pe)}
        for pe, dur in DURATIONS.items()
        for iv in range(dur)
    ] + [{"interval_id": "99", "period_id": "bad"}]
    return {
        "t_object": t_object,
        "t_membership": t_membership,
        "t_key": t_key,
        "t_data_0": t_data,
        "t_phase_3": t_phase_3,
        "t_category": [
            {"category_id": str(cid), "name": name} for name, cid in cats.items()
        ],
    }


def _csv(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    headers = list(rows[0])
    out = io.StringIO()
    out.write(",".join(headers) + "\n")
    for r in rows:
        out.write(",".join(str(r.get(h, "")) for h in headers) + "\n")
    return out.getvalue()


class FakeMdb:
    """Serve ``mdb-export <accdb> <table>`` from in-memory tables."""

    def __init__(self, tables: dict[str, list[dict[str, str]]]) -> None:
        self.tables = tables
        self.calls: list[list[str]] = []

    def check_output(self, cmd: list[str], **_kw: Any) -> str:
        self.calls.append(cmd)
        assert cmd[0] == "mdb-export"
        return _csv(self.tables.get(cmd[2], []))

    def run(self, cmd: list[str], **kw: Any) -> Any:
        self.calls.append(cmd)
        if cmd[0] == "mdb-export":
            return subprocess.CompletedProcess(cmd, 0, self.check_output(cmd, **kw), "")
        raise FileNotFoundError(cmd[0])


@pytest.fixture(name="mdb")
def _mdb(monkeypatch: pytest.MonkeyPatch) -> FakeMdb:
    fake = FakeMdb(build_tables(_default_objects()))
    monkeypatch.setattr(subprocess, "check_output", fake.check_output)
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


# ---------------------------------------------------------------------------
# Synthetic gtopt case
# ---------------------------------------------------------------------------

_KEY = {"scene": 0, "phase": 0, "scenario": 1, "stage": 1}


def _write_pq(path: Path, rows: list[tuple[int, int, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = [{**_KEY, "block": b, "uid": u, "value": v} for u, b, v in rows]
    schema = pa.schema(
        [(k, pa.int64()) for k in (*_KEY, "block", "uid")] + [("value", pa.float64())]
    )
    pq.write_table(pa.Table.from_pylist(recs, schema=schema), path)


UC_EXPR = '1 * generator("T1").generation + 1.0 * generator("H1_U1").generation <= 100'


def bundle_dict() -> dict[str, Any]:
    return {
        "simulation": {
            "block_array": [
                {"uid": 1, "duration": 2.0},
                {"uid": 2, "duration": 3.0},
            ],
        },
        "system": {
            "generator_array": [
                {"uid": 1, "name": "T1"},
                {"uid": 2, "name": "H1_U1"},
                {"uid": 3, "name": "S1_FV"},
                {"uid": 4, "name": "W1"},
                {"uid": 5, "name": "G_ONLY"},
            ],
            "demand_array": [{"uid": 1, "name": "d1"}, {"uid": 2, "name": "d2"}],
            "bus_array": [
                {"uid": 1, "name": "B1"},
                {"uid": 2, "name": "B2"},
                {"uid": 3, "name": "B_GONLY"},
            ],
            "line_array": [
                {
                    "uid": 1,
                    "name": "L1",
                    "tmax_ab": 100.0,
                    "tmax_ba": [[100.0, 90.0]],
                    "resistance": 0.01,
                    "voltage": 1.0,
                },
                {
                    "uid": 2,
                    "name": "L_HOT",
                    "tmax_ab": 380.0,
                    "tmax_normal_ab": 152.0,
                    "overload_penalty": 10.0,
                    "enforce_level": 0,
                    "resistance": 0.02,
                    "active": [1],
                },
                {"uid": 3, "name": "L_GONLY", "tmax_ab": 10.0, "active": "x"},
                {"uid": 4, "name": "L_IDLE", "tmax_ab": 50.0, "active": 0},
            ],
            "battery_array": [{"uid": 1, "name": "BAT1"}],
            "reservoir_array": [
                {"uid": 1, "name": "RES1", "emax": [[1200.0, 1100.0]]},
                {"uid": 2, "name": "RES_GONLY", "emax": 10.0},
            ],
            "user_constraint_array": [
                {"uid": 1, "name": "UC_CAP", "expression": UC_EXPR},
                {"uid": 2, "name": "UC_FLOOR", "expression": "unparseable"},
            ],
            "commitment_array": [
                {"uid": 1, "generator": "T1", "startup_cost": 400.0},
                {"uid": 2, "generator": "H1_U1", "startup_cost": "bad"},
                {"generator": "nouid"},
            ],
        },
    }


def write_case(case_dir: Path) -> Path:
    """Write the synthetic gtopt case; return ``case_dir``."""
    out = case_dir / "output"
    out.mkdir(parents=True)
    (case_dir / "PCP_case.json").write_text(json.dumps(bundle_dict()))
    (case_dir / "case.provenance.json").write_text("{}")
    (case_dir / "boundary_cuts.csv").write_text("scene,rhs,RES1\n1,100,-2.5\n")
    (out / "solution.csv").write_text("scene,phase,obj_value\n0,0,30000\n\n")
    (out / "planning.json").write_text(
        json.dumps(
            {
                "system": {
                    "user_constraint_array": [
                        {"uid": 1, "name": "UC_CAP", "penalty": 100.0},
                        {"uid": 2, "name": "UC_FLOOR", "penalty": "bad"},
                        {"uid": 3, "name": "Gas_MaxOpDay_X"},
                    ],
                    "commitment_array": [
                        {"uid": 1, "startup_cost": 400.0, "shutdown_cost": 50.0},
                        {"uid": 2},
                    ],
                }
            }
        )
    )
    (out / "solver_status.json").write_text(
        json.dumps({"solver": "highs", "method": "monolithic"})
    )
    logs = out / "logs"
    logs.mkdir()
    (logs / "gtopt_1.log").write_text(
        "[2026-01-01 00:00:00.000] start\n"
        "avg LP size : 1,000 vars, 2,000 rows\n"
        "[2026-01-01 00:01:30.500] done\n"
    )
    (logs / "cplex_1.log").write_text(
        "Reduced MIP has 10 binaries, 2 generals\n   12   5   1.50%\n   20   1  0.10%\n"
    )
    _write_pq(
        out / "Generator" / "generation_sol.parquet",
        [
            (1, 1, 90.0),
            (1, 2, 90.0),
            (2, 1, 60.0),
            (2, 2, 40.0),
            (3, 1, 20.0),
            (4, 1, 10.0),
            (5, 2, 4.0),
            (99, 1, 8.0),  # synthetic battery companion generator
        ],
    )
    _write_pq(
        out / "Generator" / "srmc_sol.parquet",
        [(1, 1, 50.0), (1, 2, 61.0), (5, 2, 30.0)],
    )
    _write_pq(
        out / "Demand" / "load_sol.parquet",
        [(1, 1, 80.0), (1, 2, 80.0), (2, 1, 70.0), (2, 2, 70.0), (50, 1, 10.0)],
    )
    _write_pq(out / "Demand" / "fail_sol.parquet", [(1, 2, 1.0), (50, 1, 3.0)])
    _write_pq(out / "Battery" / "finp_sol.parquet", [(1, 1, 9.0)])
    _write_pq(out / "Battery" / "fout_sol.parquet", [(1, 2, 7.0)])
    _write_pq(
        out / "Line" / "flow_sol.parquet",
        [(1, 1, 55.0), (1, 2, -35.0), (2, 1, 160.0), (2, 2, 100.0), (3, 1, 2.0)],
    )
    _write_pq(out / "Line" / "loss_sol.parquet", [(1, 1, 0.5)])
    _write_pq(
        out / "Bus" / "balance_dual.parquet",
        [(1, 1, 41.0), (1, 2, 44.0), (2, 1, 40.0), (2, 2, 55.0), (3, 1, 9.0)],
    )
    _write_pq(out / "Reservoir" / "eini_sol.parquet", [(1, 1, 1000.0)])
    _write_pq(out / "Reservoir" / "efin_sol.parquet", [(1, 1, 960.0), (1, 2, 870.0)])
    _write_pq(
        out / "Reservoir" / "water_value_dual.parquet",
        [(1, 1, 11.0), (1, 2, 15.0), (7, 1, 1.0)],
    )
    _write_pq(
        out / "Commitment" / "status_sol.parquet",
        [(1, 1, 1.0), (1, 2, 1.0), (2, 2, 1.0), (9, 1, 1.0)],
    )
    _write_pq(out / "Commitment" / "startup_sol.parquet", [(1, 1, 1.0), (9, 1, 1.0)])
    _write_pq(out / "Commitment" / "shutdown_sol.parquet", [(2, 2, 1.0)])
    _write_pq(
        out / "UserConstraint" / "slack_sol.parquet",
        [(1, 1, 2.0), (1, 2, 0.0), (2, 1, 1.0), (3, 1, 4.0), (8, 1, 1.0)],
    )
    _write_pq(out / "UserConstraint" / "slack_pos_sol.parquet", [])
    return case_dir


_LOG = (
    "Tasks: 1   Cols 3,289   Ints 256   Rows 4,956   Nzs 14,023\n"
    "    Best Integer Solution:.....         2.8000000000e+004\n"
    "    Best Bound:.....         2.7000000000e+004\n"
    "    Linear Relaxation:.....   N/A\n"
    '<-- Model "PRG" Completed. Time: 01:02:03.5\n'
)


def write_res_zip(path: Path, log: str = _LOG, nested: bool = True) -> Path:
    """RES bundle: outer zip with the .accdb and a nested Solution.zip log."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("Model ( PRG ) Log.txt", log)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Model PRG Solution/Model PRG Solution.accdb", b"fake accdb")
        if nested:
            zf.writestr("Model PRG Solution/Model PRG Solution.zip", inner.getvalue())
        else:
            zf.writestr("Model ( PRG ) Log.txt", log)
    return path


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


def _run_main(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    rc = cwp.main([*argv, "--width", "220"])
    return rc, capsys.readouterr().out


def test_main_full_report_then_cache_hit(
    tmp_path: Path, mdb: FakeMdb, capsys: pytest.CaptureFixture[str]
) -> None:
    case = write_case(tmp_path / "case")
    res = write_res_zip(tmp_path / "RES20260101.zip")
    argv = [
        "--gtopt-case",
        str(case),
        "--plexos-res-zip",
        str(res),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--top-uc",
        "5",
    ]
    rc, out = _run_main(argv, capsys)
    assert rc == 0
    for needle in (
        "Cost totals",
        "Solution totals",
        "Problem size",
        "Saved PLEXOS extract",
        "UC_CAP",
    ):
        assert needle in out, needle
    n_calls = len(mdb.calls)
    rc, out = _run_main(argv, capsys)
    assert rc == 0
    assert "Using cached PLEXOS extract" in out
    # The cached run skips the per-table extraction.
    assert len(mdb.calls) - n_calls < n_calls


def test_main_requires_plexos_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out = _run_main(["--gtopt-case", str(tmp_path)], capsys)
    assert rc == 2
    assert "No PLEXOS source" in out


def test_main_log_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "Log.txt"
    log.write_text(_LOG)
    rc, out = _run_main(["--plexos-log", str(log)], capsys)
    assert rc == 0
    assert "No gtopt run available" in out


def test_main_log_without_objectives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "Log.txt"
    log.write_text("nothing useful\n")
    rc, out = _run_main(["--plexos-log", str(log)], capsys)
    assert rc == 1
    assert "No objective lines" in out


# ---------------------------------------------------------------------------
# Alternative layouts through main()
# ---------------------------------------------------------------------------


def _sparse_case(case_dir: Path) -> Path:
    """Case with the optional outputs removed / swapped for legacy forms."""
    write_case(case_dir)
    out = case_dir / "output"
    bundle = bundle_dict()
    for ln in bundle["system"]["line_array"]:
        ln.pop("resistance", None)
    (case_dir / "PCP_case.json").write_text(json.dumps(bundle))
    for rel in (
        "Line/flow_sol.parquet",
        "Line/loss_sol.parquet",
        "Bus/balance_dual.parquet",
        "Reservoir/water_value_dual.parquet",
        "Generator/srmc_sol.parquet",
        "solver_status.json",
    ):
        (out / rel).unlink()
    _write_pq(out / "Line" / "flowp_sol.parquet", [(1, 1, 55.0)])
    _write_pq(out / "Line" / "flown_sol.parquet", [(1, 2, 35.0)])
    (out / "solution.csv").write_text("scene,phase,obj_value\n0,0,40000\n")
    for sub in ("Commitment", "UserConstraint"):
        for f in (out / sub).iterdir():
            f.unlink()
        (out / sub).rmdir()
    (case_dir / "boundary_cuts.csv").unlink()
    return case_dir


def test_main_sparse_case_xz_single_zip(
    tmp_path: Path, mdb: FakeMdb, capsys: pytest.CaptureFixture[str]
) -> None:
    import lzma  # pylint: disable=import-outside-toplevel

    case = _sparse_case(tmp_path / "case")
    plain = write_res_zip(
        tmp_path / "plain.zip", log=_LOG.replace("Best Bound", "Other"), nested=False
    )
    res = tmp_path / "RES20260101.zip.xz"
    with lzma.open(res, "wb") as fh:
        fh.write(plain.read_bytes())
    rc, out = _run_main(
        [
            "--gtopt-case",
            str(case),
            "--plexos-res-zip",
            str(res),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--no-cache",
            "--no-uc-drilldown",
        ],
        capsys,
    )
    assert rc == 0
    assert "Step 5 (per-bus LMP) skipped" in out
    assert "Step 8b (reservoir water value) skipped" in out
    assert "gtopt objective > PLEXOS" in out


@pytest.mark.parametrize(("obj", "needle"), [(28100, "Within 2%"), (20000, "<")])
def test_main_objective_verdicts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    obj: float,
    needle: str,
) -> None:
    case = tmp_path / "case"
    (case / "output").mkdir(parents=True)
    (case / "output" / "solution.csv").write_text(f"obj_value\n{obj}\n")
    log = tmp_path / "Log.txt"
    log.write_text(_LOG)
    rc, out = _run_main(
        ["--gtopt-case", str(case), "--plexos-log", str(log), "--no-uc-drilldown"],
        capsys,
    )
    assert rc == 0
    assert needle in out


def test_main_gtopt_side_unavailable_and_uc_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "Log.txt"
    log.write_text(_LOG.replace("Best Bound", "Nope"))

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise ValueError("bad slack table")

    monkeypatch.setattr(cwp, "load_uc_penalty_breakdown", _boom)
    rc, out = _run_main(
        ["--gtopt-case", str(tmp_path), "--plexos-log", str(log)], capsys
    )
    assert rc == 0
    assert "gtopt side unavailable" in out
    assert "UC drilldown unavailable" in out


def test_main_step3_errors_are_reported(
    tmp_path: Path, mdb: FakeMdb, capsys: pytest.CaptureFixture[str]
) -> None:
    case = write_case(tmp_path / "case")
    res = write_res_zip(tmp_path / "RES.zip")
    mdb.tables["t_phase_3"] = []
    rc, out = _run_main(
        [
            "--gtopt-case",
            str(case),
            "--plexos-res-zip",
            str(res),
            "--cache-dir",
            str(tmp_path / "c"),
        ],
        capsys,
    )
    assert rc == 0
    assert "Step 3 skipped" in out


def test_main_step3_mdb_failure_is_reported(
    tmp_path: Path,
    mdb: FakeMdb,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del mdb
    case = write_case(tmp_path / "case")
    res = write_res_zip(tmp_path / "RES.zip")

    def _fail(cmd: list[str], **_kw: Any) -> Any:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", _fail)
    _rc, out = _run_main(
        [
            "--gtopt-case",
            str(case),
            "--plexos-res-zip",
            str(res),
            "--cache-dir",
            str(tmp_path / "c2"),
        ],
        capsys,
    )
    assert "Step 3-10 skipped" in out


def test_main_gtopt_output_bundle_and_date(
    tmp_path: Path,
    mdb: FakeMdb,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del mdb
    case = write_case(tmp_path / "run")
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (case / "PCP_case.json").rename(bundle_dir / "PCP_case.json")
    (case / "boundary_cuts.csv").rename(bundle_dir / "boundary_cuts.csv")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    pcp = tmp_path / "xdg" / "gtopt" / "cen2gtopt" / "pcp_archive" / "PCP"
    pcp.mkdir(parents=True)
    write_res_zip(pcp / "RES20260101.zip")
    rc, out = _run_main(
        [
            "--gtopt-output",
            str(case / "output"),
            "--gtopt-bundle",
            str(bundle_dir),
            "--gtopt-case",
            str(case),
            "--date",
            "20260101",
            "--plexos-res-zip",
            str(pcp / "RES20260101.zip"),
        ],
        capsys,
    )
    assert rc == 0
    assert "--gtopt-case ignored" in out
    assert "--plexos-res-zip ignored" in out
    assert "Solution totals" in out


def test_resolve_inputs_bundle_warnings(tmp_path: Path) -> None:
    buf = io.StringIO()
    con = cwp.Console(file=buf, width=200)
    args = cwp.make_parser().parse_args(["--gtopt-bundle", str(tmp_path)])
    assert cwp._resolve_inputs(args, con) == (None, None)
    args = cwp.make_parser().parse_args(
        ["--gtopt-case", str(tmp_path), "--gtopt-bundle", str(tmp_path)]
    )
    assert cwp._resolve_inputs(args, con)[0] == tmp_path
    text = buf.getvalue()
    assert "without --gtopt-output" in text
    assert "--gtopt-bundle ignored" in text


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_cache_path_and_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = tmp_path / "RES.zip"
    res.write_bytes(b"x")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    path = cwp._plexos_cache_path(res)
    assert path.parent == tmp_path / "xdg" / "gtopt" / "plexos_compare"
    assert cwp._load_plexos_cache(path) is None
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a pickle")
    assert cwp._load_plexos_cache(path) is None
    cwp._save_plexos_cache(path, {"_version": -1})
    assert cwp._load_plexos_cache(path) is None
    cwp._save_plexos_cache(path, {"_version": cwp._PLEXOS_CACHE_VERSION, "x": 1})
    assert cwp._load_plexos_cache(path) == {
        "_version": cwp._PLEXOS_CACHE_VERSION,
        "x": 1,
    }


def test_log_parsing_edges(tmp_path: Path) -> None:
    log = tmp_path / "log.txt"
    log.write_text("Best Bound:..... N/A\nBest Integer Solution: 0\n")
    assert cwp.parse_plexos_log(log) == {"Best Integer Solution": 0.0}
    stats = cwp.compute_plexos_problem_stats(log)
    assert stats["rows"] == 0.0 and "gap_pct" not in stats


def test_find_log_errors(tmp_path: Path) -> None:
    flat = tmp_path / "flat.zip"
    with zipfile.ZipFile(flat, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(FileNotFoundError, match="no Log.txt found in"):
        cwp.find_plexos_log_in_res_bundle(flat)
    nested = tmp_path / "nested.zip"
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("other.txt", "x")
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("M Solution.zip", inner.getvalue())
    with pytest.raises(FileNotFoundError, match="inside"):
        cwp.find_plexos_log_in_res_bundle(nested)
    with pytest.raises(FileNotFoundError, match="no .accdb"):
        cwp._extract_accdb_from_res_zip(nested, tmp_path)


def test_parse_gtopt_solution_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cwp.parse_gtopt_solution(tmp_path)
    sol = tmp_path / "output" / "solution.csv"
    sol.parent.mkdir()
    sol.write_text("obj_value\n")
    with pytest.raises(ValueError, match="no data rows"):
        cwp.parse_gtopt_solution(tmp_path)
    sol.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="missing 'obj_value'"):
        cwp.parse_gtopt_solution(tmp_path)


def test_find_case_bundle_edges(tmp_path: Path) -> None:
    (tmp_path / "planning.json").write_text("{}")
    assert cwp._find_case_bundle(tmp_path) is None
    (tmp_path / "dir.json").mkdir()
    assert cwp._find_case_bundle(tmp_path) is None
    with pytest.raises(FileNotFoundError):
        cwp._load_case_bundle(tmp_path)
    with pytest.raises(FileNotFoundError):
        cwp.compute_gtopt_energy_totals(tmp_path)
    assert cwp._compute_plexos_line_losses_analytic_mwh({}, tmp_path) == 0.0
    assert not cwp.compute_uc_block_drilldown(tmp_path, tmp_path, {"x": {}})


def test_block_durations_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _missing(cmd: list[str], **_kw: Any) -> Any:
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", _missing)
    with pytest.raises(RuntimeError, match="cannot read t_phase_3"):
        cwp._plexos_block_durations_from_accdb(tmp_path / "x.accdb")


def test_plexos_extractors_without_layout(mdb: FakeMdb, tmp_path: Path) -> None:
    mdb.tables["t_phase_3"] = []
    accdb = tmp_path / "x.accdb"
    for fn in (
        cwp.compute_plexos_energy_totals,
        cwp.compute_plexos_per_unit_srmc,
        cwp.compute_plexos_per_bus_lmp,
        cwp.compute_plexos_per_line,
        cwp.compute_plexos_reservoir_volumes,
        cwp.compute_plexos_reservoir_water_value,
        cwp.compute_plexos_battery_operation,
        cwp.compute_plexos_commitment,
    ):
        assert not fn(accdb), fn.__name__


# ---------------------------------------------------------------------------
# gtopt-side readers: missing / malformed inputs
# ---------------------------------------------------------------------------


def _con() -> tuple[Any, io.StringIO]:
    buf = io.StringIO()
    return cwp.Console(file=buf, width=250), buf


def test_problem_stats_degenerate_logs(tmp_path: Path) -> None:
    out = tmp_path / "output"
    logs = out / "logs"
    logs.mkdir(parents=True)
    (out / "solver_status.json").write_text("{not json")
    (logs / "gtopt_1.log").write_text(
        "[2026-13-01 00:00:00.000] a\n[2026-13-01 00:00:01.000] b\n"
    )
    (logs / "cplex_1.log").write_text("nothing useful\n")
    stats = cwp.compute_gtopt_problem_stats(tmp_path)
    assert stats["solver"] == "" and stats["run_s"] == 0.0
    assert stats["int_vars"] == 0.0 and "gap_pct" not in stats
    (logs / "gtopt_1.log").write_text("[2026-01-01 00:00:00.000] only one\n")
    assert cwp.compute_gtopt_problem_stats(tmp_path)["cols"] == 0.0
    con, buf = _con()
    zero = {"run_s": 0.0, "rows": 0.0, "cols": 0.0, "int_vars": 0.0}
    cwp._render_problem_size_compare(zero, zero, con)
    assert "Final MIP gap" not in buf.getvalue()


def test_energy_helpers_missing_inputs(tmp_path: Path) -> None:
    assert cwp._sum_real_generator_mwh(tmp_path, {}, {}) == (0.0, 0.0)
    assert (
        cwp._sum_consumer_demand_field_mwh(tmp_path, {}, {}, "Demand/load_sol.parquet")
        == 0.0
    )
    _write_pq(tmp_path / "output" / "Demand" / "load_sol.parquet", [(1, 1, 1.0)])
    assert (
        cwp._sum_consumer_demand_field_mwh(
            tmp_path, {"system": {}}, {}, "Demand/load_sol.parquet"
        )
        == 0.0
    )


def test_energy_totals_commitment_edges(tmp_path: Path) -> None:
    case = write_case(tmp_path / "case")
    commit = case / "output" / "Commitment"
    (commit / "startup_sol.parquet").unlink()
    _write_pq(commit / "shutdown_sol.parquet", [])
    tot = cwp.compute_gtopt_energy_totals(case)
    assert tot["startup_cost_usd"] == 0.0 and tot["shutdown_cost_usd"] == 0.0
    (case / "output" / "planning.json").write_text("{bad")
    assert cwp.compute_gtopt_energy_totals(case)["startup_cost_usd"] == 0.0


def test_gtopt_readers_without_parquets(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "PCP_case.json").write_text(json.dumps(bundle_dict()))
    assert not cwp.compute_gtopt_generation_by_technology(case)
    vols = cwp.compute_gtopt_reservoir_volumes(case)
    assert all(v["efin"] == 0.0 for v in vols.values())
    assert not cwp.compute_gtopt_battery_operation(case)
    assert not cwp.compute_gtopt_commitment(case)
    assert not cwp.load_uc_penalty_breakdown(case, 1.0)


def test_generation_by_technology_heuristics(tmp_path: Path) -> None:
    case = tmp_path / "case"
    bundle = bundle_dict()
    bundle["system"]["generator_array"] = [
        {"uid": i, "name": n}
        for i, n in enumerate(("BAT_A", "X_CS", "Y_EOL", "Z_U12", "COAL"), start=1)
    ]
    case.mkdir()
    (case / "PCP_case.json").write_text(json.dumps(bundle))
    _write_pq(
        case / "output" / "Generator" / "generation_sol.parquet",
        [(u, 1, 1.0) for u in range(1, 6)],
    )
    tech = cwp.compute_gtopt_generation_by_technology(case)
    assert set(tech) == {"battery", "solar", "wind", "hydro", "thermal"}


def test_commitment_skips_nameless_generator(tmp_path: Path) -> None:
    case = write_case(tmp_path / "case")
    bundle = bundle_dict()
    bundle["system"]["commitment_array"] = [
        {"uid": 1, "generator": ""},
        {"uid": 2, "generator": "H1_U1"},
    ]
    (case / "PCP_case.json").write_text(json.dumps(bundle))
    assert set(cwp.compute_gtopt_commitment(case)) == {"H1_U1"}


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def test_render_srmc_and_lmp_edges() -> None:
    con, buf = _con()
    units = {"A": (10.0, 5.0), "B": (10.0, 7.0)}
    cwp._render_srmc_compare(units, {"A": (10.0, 6.0), "B": (10.0, 9.0)}, con, top_n=1)
    cwp._render_lmp_compare({"B1": 0.0, "B2": 10.0}, {"B1": 5.0, "B2": 12.0}, con)
    assert "1 more co-dispatched" in buf.getvalue()


def test_render_line_compare_branches() -> None:
    con, buf = _con()
    plexos = {
        "SOFT": {"energy_mwh": 10.0, "max_flow": 0.0, "active": 1.0},
        "OFF": {"energy_mwh": 0.0, "max_flow": 50.0, "active": 0.0},
    }
    gtopt = {
        "SOFT": {"energy_mwh": 12.0, "max_flow": 500.0, "is_soft": 1.0},
        "OFF": {"energy_mwh": 0.0, "max_flow": 40.0, "active": 1.0},
    }
    cwp._render_line_compare(plexos, gtopt, con)
    text = buf.getvalue()
    assert "no cap violations" in text
    assert "OFF" in text
    con, buf = _con()
    cwp._render_line_compare({}, {}, con)
    assert "Energy stats" not in buf.getvalue()


def test_render_water_value_and_commitment_edges() -> None:
    con, buf = _con()
    cwp._render_water_value_compare({"A": 1.0}, {"B": 2.0}, con)
    assert buf.getvalue() == ""
    uc = {"on_hours": 2.0, "startups": 1.0, "shutdowns": 0.0, "startup_cost_usd": 9.0}
    zero = {**uc, "startup_cost_usd": 0.0}
    cwp._render_commitment_compare({"P": uc, "C": uc}, {"G": zero, "C": zero}, con)
    text = buf.getvalue()
    assert "gtopt-only committable" in text
    assert "paid $0 in startup costs" in text


def test_render_uc_helpers_edges(tmp_path: Path) -> None:
    con, buf = _con()
    cwp._render_uc_drilldown({}, 1.0, 5, con)
    cwp._render_uc_block_drilldown({}, tmp_path, con)
    row: dict[str, float | str] = {
        "block": 1,
        "hours": 1.0,
        "rhs": 5.0,
        "op": ">=",
        "plexos_lhs": 4.0,
        "gtopt_lhs": 6.0,
        "slack": 1.0,
    }
    cwp._render_uc_block_drilldown(
        {"UC_X": [row, {**row, "block": 2}]}, tmp_path, con, max_blocks_per_uc=1
    )
    text = buf.getvalue()
    assert "No user-constraint slack" in text
    assert "1 more blocks" in text
    assert cwp._categorize_uc("DAM_X") == "discharge limit"
    assert cwp._categorize_uc("zzz") == "other"
    assert cwp._parse_uc_expression("x <= 1") is None


def test_render_report_without_plexos_objective() -> None:
    con, buf = _con()
    cwp._render_report({"Best Bound": 1.0}, {"sum_obj": 2.0, "rows": 1.0}, con)
    assert "gtopt sum(obj_value)" in buf.getvalue()


# ---------------------------------------------------------------------------
# Boundary cuts / reservoir emax / UC block drilldown
# ---------------------------------------------------------------------------


def test_boundary_cut_and_emax_edges(tmp_path: Path) -> None:
    hdr = tmp_path / "hdr"
    hdr.mkdir()
    (hdr / "boundary_cuts.csv").write_text("scene,rhs,R1\n")
    assert not cwp._read_boundary_cut_coeffs(None, hdr)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "boundary_cuts.csv").write_text("scene,rhs,R1,R2\n1,0,x,-3\n")
    assert cwp._read_boundary_cut_coeffs(bad) == {"R2": 3.0}

    d = tmp_path / "emax"
    d.mkdir()
    (d / "a.json").write_text("{bad")
    (d / "b.json").write_text(
        json.dumps({"system": {"reservoir_array": [{"name": "R"}]}})
    )
    (d / "planning.json").write_text("{}")
    (d / "x.provenance.json").write_text("{}")
    (d / "dir.json").mkdir()
    assert not cwp._read_reservoir_emax(None, d, tmp_path / "missing.json")
    (d / "c.json").write_text(
        json.dumps({"system": {"reservoir_array": [{"name": "R", "emax": [[]]}]}})
    )
    assert not cwp._read_reservoir_emax(d / "c.json")


def test_uc_block_drilldown_edges(
    tmp_path: Path, mdb: FakeMdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mdb
    case = write_case(tmp_path / "case")
    accdb = tmp_path / "x.accdb"
    assert not cwp.compute_uc_block_drilldown(case, accdb, {})
    assert not cwp.compute_uc_block_drilldown(case, accdb, {"UC_FLOOR": {}})
    bundle = bundle_dict()
    bundle["system"]["user_constraint_array"] = [
        {
            "uid": 1,
            "name": "UC_GE",
            "expression": '1 * generator("T1").generation >= 50',
        },
    ]
    (case / "PCP_case.json").write_text(json.dumps(bundle))
    monkeypatch.setattr(
        cwp, "_pull_plexos_gen_by_period", lambda *_a: {"T1": {1: 100.0, 2: 10.0}}
    )
    _write_pq(case / "output" / "UserConstraint" / "slack_sol.parquet", [])
    out = cwp.compute_uc_block_drilldown(case, accdb, {"UC_GE": {}})
    assert [r["block"] for r in out["UC_GE"]] == [2]
    (case / "output" / "Generator" / "generation_sol.parquet").unlink()
    assert not cwp.compute_uc_block_drilldown(case, accdb, {"UC_GE": {}})


# ---------------------------------------------------------------------------
# --date / --gtopt-output resolution
# ---------------------------------------------------------------------------


def test_resolve_res_zip_for_date_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="YYYYMMDD"):
        cwp._resolve_res_zip_for_date("2026")
    with pytest.raises(FileNotFoundError, match="no PLEXOS RES zip"):
        cwp._resolve_res_zip_for_date("20260101")
    pcp = cwp._default_pcp_archive_dir() / "PCP"
    pcp.mkdir(parents=True)
    with zipfile.ZipFile(pcp / "PLEXOS20260101.zip", "w") as zf:
        zf.writestr("DATOS20260101.zip", b"d")
    with pytest.raises(FileNotFoundError, match="does not contain"):
        cwp._resolve_res_zip_for_date("20260101")
    with zipfile.ZipFile(pcp / "PLEXOS20260101.zip", "w") as zf:
        zf.writestr("RES20260101.zip", b"r")
    first = cwp._resolve_res_zip_for_date("20260101")
    assert first.read_bytes() == b"r"
    assert cwp._resolve_res_zip_for_date("20260101") == first


def test_find_bundle_json_and_stitch(tmp_path: Path) -> None:
    bdir = tmp_path / "bundle"
    bdir.mkdir()
    (bdir / "a.provenance.json").write_text("{}")
    (bdir / "planning.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="no PLEXOS-source JSON"):
        cwp._find_bundle_json(bdir)
    (bdir / "z.json").write_text("{}")
    assert cwp._find_bundle_json(bdir).name == "z.json"

    out = tmp_path / "out"
    with pytest.raises(FileNotFoundError, match="not a directory"):
        cwp._stitch_gtopt_case_dir(out, None)
    out.mkdir()
    with pytest.raises(FileNotFoundError, match="no solution.csv"):
        cwp._stitch_gtopt_case_dir(out, None)
    (out / "solution.csv").write_text("obj_value\n1\n")
    stitched = cwp._stitch_gtopt_case_dir(out, None)
    assert (stitched / "output" / "solution.csv").is_file()
    with pytest.raises(FileNotFoundError, match="--gtopt-bundle"):
        cwp._stitch_gtopt_case_dir(out, tmp_path / "nope")
