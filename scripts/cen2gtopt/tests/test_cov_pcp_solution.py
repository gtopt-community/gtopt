# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Offline tests for :mod:`cen2gtopt.pcp_solution` with a fake mdbtools runner."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import _res_summary, pcp_solution as ps
from cen2gtopt.pcp_archive import PcpFile
from cen2gtopt.tests.cov_plexos_helpers import write_zip, zip_bytes

DATE = "2026-05-06"
YMD = "20260506"
PID_SOL = "Modelos/Model PRGdia_Full_Definitivo_PID Solution"
PCP_SOL = "Model PRGdia_Full_Definitivo Solution"


# ---------------------------------------------------------------------------
# Fake mdbtools runner
# ---------------------------------------------------------------------------


class FakeMdb:
    """In-memory mdbtools: ``tables`` maps table name → DataFrame."""

    def __init__(self, tables: dict[str, pd.DataFrame], available: bool = True):
        self.tables = tables
        self.is_available = available
        self.calls: list[list[str]] = []

    def available(self) -> bool:
        return self.is_available

    def run(
        self, args: list[str], *, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[Any]:
        assert timeout > 0
        self.calls.append(args)
        if args[0] == "mdb-tables":
            assert text
            return subprocess.CompletedProcess(
                args, 0, stdout="\n".join(self.tables) + "\n\n", stderr=""
            )
        assert not text
        df = self.tables.get(args[-1])
        payload = b"" if df is None else df.to_csv(index=False).encode()
        return subprocess.CompletedProcess(args, 0, stdout=payload, stderr=b"")


def _solution_tables() -> dict[str, pd.DataFrame]:
    return {
        "t_property": pd.DataFrame(
            {
                "property_id": [5, 6],
                "name": ["Price", "Price"],
                "collection_id": [281, 1],
            }
        ),
        "t_key": pd.DataFrame(
            {
                "key_id": [1, 2, 3, 4],
                "membership_id": [10, 11, 10, 12],
                "property_id": [5, 5, 5, 6],
                "period_type_id": [0, 0, 1, 0],
            }
        ),
        "t_membership": pd.DataFrame(
            {
                "membership_id": [10, 11, 12],
                "parent_object_id": [1, 1, 1],
                "child_object_id": [100, 101, 102],
                "collection_id": [281, 281, 1],
            }
        ),
        "t_object": pd.DataFrame(
            {
                "object_id": [100, 101, 102],
                "name": ["Crucero220", "Polpaico220", "GEN"],
                "class_id": [22, 22, 2],
            }
        ),
        "t_data_0": pd.DataFrame(
            {
                "key_id": [1, 1, 2, 2, 4],
                "period_id": [1, 2, 1, 2, 1],
                "value": [40.0, 60.0, 50.0, 70.0, 999.0],
            }
        ),
        "t_period_0": pd.DataFrame(
            {
                "interval_id": [1, 2],
                "datetime": ["2026-05-06 00:00:00", "2026-05-06 01:00:00"],
            }
        ),
    }


@pytest.fixture(name="mdb")
def _mdb() -> Iterator[FakeMdb]:
    fake = FakeMdb(_solution_tables())
    previous = ps.set_mdb_runner(fake)
    yield fake
    ps.set_mdb_runner(previous)


# ---------------------------------------------------------------------------
# Runner plumbing
# ---------------------------------------------------------------------------


def test_subprocess_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = ps.SubprocessMdbRunner()
    monkeypatch.setattr(ps.shutil, "which", lambda b: f"/usr/bin/{b}")
    assert runner.available()
    monkeypatch.setattr(ps.shutil, "which", lambda b: None)
    assert not runner.available()

    seen: dict[str, Any] = {}

    def _run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[Any]:
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, stdout="ok")

    monkeypatch.setattr(ps.subprocess, "run", _run)
    out = runner.run(["mdb-tables", "-1", "x"], text=True, timeout=5)
    assert out.stdout == "ok"
    assert seen["kw"] == {
        "check": True,
        "capture_output": True,
        "text": True,
        "timeout": 5,
    }


def test_require_mdbtools_missing() -> None:
    previous = ps.set_mdb_runner(FakeMdb({}, available=False))
    try:
        assert not ps._have_mdbtools()
        with pytest.raises(SystemExit, match="mdbtools not found"):
            ps.list_tables(Path("x.accdb"))
    finally:
        ps.set_mdb_runner(previous)


def test_cli_exits_4_without_mdbtools(capsys) -> None:
    """Regression: the documented exit code for missing mdbtools is 4.

    ``SystemExit(<message>)`` made the process exit with status 1.
    """
    previous = ps.set_mdb_runner(FakeMdb({}, available=False))
    try:
        with pytest.raises(SystemExit) as excinfo:
            ps.main(["summary", "--res", "x.zip", "--output", "out"])
    finally:
        ps.set_mdb_runner(previous)
    assert excinfo.value.code == ps.EXIT_NO_MDBTOOLS == 4
    assert "mdbtools not found" in str(excinfo.value)
    assert "mdbtools not found" in capsys.readouterr().err


def test_list_and_export(mdb: FakeMdb) -> None:
    assert ps.list_tables(Path("x.accdb"))[:2] == ["t_property", "t_key"]
    df = ps.export_table(Path("x.accdb"), "t_object")
    assert list(df["name"]) == ["Crucero220", "Polpaico220", "GEN"]
    assert ps.export_table(Path("x.accdb"), "missing").empty
    assert mdb.calls[1][:3] == ["mdb-export", "-D", "%Y-%m-%d %H:%M:%S"]


def test_cached_export_and_clear(mdb: FakeMdb) -> None:
    ps._cached_export_table("y.accdb", "t_key")
    ps._cached_export_table("y.accdb", "t_key")
    assert len(mdb.calls) == 1
    ps.clear_table_cache()
    ps._cached_export_table("y.accdb", "t_key")
    assert len(mdb.calls) == 2


# ---------------------------------------------------------------------------
# extract_property
# ---------------------------------------------------------------------------


def test_extract_property(mdb: FakeMdb) -> None:
    df = ps.extract_property(
        Path("sol.accdb"), property_name="Price", collection_id=281
    )
    assert list(df.columns) == ["object_name", "datetime", "hour", "period_id", "value"]
    by = df.set_index(["object_name", "hour"])["value"].to_dict()
    assert by == {
        ("Crucero220", 0): 40.0,
        ("Crucero220", 1): 60.0,
        ("Polpaico220", 0): 50.0,
        ("Polpaico220", 1): 70.0,
    }
    # Second call on the same accdb reuses the cached schema tables.
    n = len(mdb.calls)
    ps.extract_property(Path("sol.accdb"), property_name="Price", collection_id=281)
    assert len(mdb.calls) == n


def test_extract_property_errors(mdb: FakeMdb) -> None:
    with pytest.raises(ValueError, match="no property name='Load'"):
        ps.extract_property(Path("e.accdb"), property_name="Load", collection_id=281)
    with pytest.raises(ValueError, match="no t_key rows"):
        ps.extract_property(
            Path("e.accdb"), property_name="Price", collection_id=281, period_table=7
        )
    assert mdb.calls


# ---------------------------------------------------------------------------
# resolve_solution
# ---------------------------------------------------------------------------


def _pid_zip_members(stem: str, *, with_accdb: bool = True) -> dict[str, bytes]:
    members = {f"{stem}/Modelos/readme.txt": b"x"}
    if with_accdb:
        members[f"{stem}/{PID_SOL}/sol.accdb"] = b"ACCDB"
    return members


@pytest.fixture(name="remote")
def _remote(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"files": [], "payloads": {}, "downloads": []}

    def _fetch_index(**_k: Any) -> list[PcpFile]:
        return list(state["files"])

    def _download_one(f: PcpFile, *, output_dir: Path, **_k: Any) -> Path:
        state["downloads"].append(f.nombre)
        return write_zip(
            output_dir / f.categoria / f.nombre, state["payloads"][f.nombre]
        )

    monkeypatch.setattr(ps, "fetch_index", _fetch_index)
    monkeypatch.setattr(ps, "download_one", _download_one)
    return state


def _file(name: str, cat: str) -> PcpFile:
    return PcpFile.from_dict({"nombre": name, "categoria": cat})


def _local_pid(root: Path, pp: str, *, with_accdb: bool = True) -> Path:
    stem = f"PID_{YMD}_{pp}"
    sol = root / "_unpacked" / stem / stem / PID_SOL
    sol.mkdir(parents=True)
    if with_accdb:
        (sol / "sol.accdb").write_bytes(b"A")
    return sol


def test_resolve_local_pid(tmp_path: Path) -> None:
    _local_pid(tmp_path, "19")
    _local_pid(tmp_path, "20")
    latest = ps.resolve_solution(DATE, download_root=tmp_path, auto_download=False)
    assert latest.source == "PID" and latest.period == 20
    pinned = ps.resolve_solution(
        DATE, period=19, download_root=tmp_path, auto_download=False
    )
    assert pinned.period == 19
    assert pinned.accdb_path.name == "sol.accdb"


def test_resolve_local_pid_odd_name(tmp_path: Path) -> None:
    _local_pid(tmp_path, "x7")
    sol = ps.resolve_solution(DATE, download_root=tmp_path, auto_download=False)
    assert sol.source == "PID" and sol.period is None


def test_resolve_pid_missing(tmp_path: Path) -> None:
    _local_pid(tmp_path, "20", with_accdb=False)
    with pytest.raises(FileNotFoundError, match="period 3"):
        ps.resolve_solution(
            DATE, period=3, source="pid", download_root=tmp_path, auto_download=False
        )
    with pytest.raises(FileNotFoundError, match=f"no PID .accdb for {DATE}$"):
        ps.resolve_solution(
            DATE, source="pid", download_root=tmp_path, auto_download=False
        )


def test_resolve_remote_pid(tmp_path: Path, remote: dict[str, Any]) -> None:
    names = [f"PID_{YMD}_03.zip", f"PID_{YMD}_04.zip"]
    remote["files"] = [_file(n, "PID") for n in names]
    for n in names:
        remote["payloads"][n] = _pid_zip_members(n.removesuffix(".zip"))

    sol = ps.resolve_solution(DATE, download_root=tmp_path)
    assert sol.period == 4 and remote["downloads"] == [names[1]]

    pinned = ps.resolve_solution(DATE, period=3, download_root=tmp_path / "b")
    assert pinned.period == 3


def test_resolve_remote_pid_odd_name(tmp_path: Path, remote: dict[str, Any]) -> None:
    name = f"PID_{YMD}_x.zip"
    remote["files"] = [_file(name, "PID")]
    remote["payloads"][name] = _pid_zip_members(name.removesuffix(".zip"))
    sol = ps.resolve_solution(DATE, download_root=tmp_path)
    assert sol.source == "PID" and sol.period is None


def test_resolve_remote_pid_cached_zip_no_accdb_falls_to_pcp(
    tmp_path: Path, remote: dict[str, Any]
) -> None:
    """Already-extracted PID without .accdb → PCP fall-back (no re-extract)."""
    name = f"PID_{YMD}_04.zip"
    remote["files"] = [_file(name, "PID"), _file(f"PLEXOS{YMD}.zip", "PCP")]
    write_zip(tmp_path / "PID" / name, _pid_zip_members("PID", with_accdb=False))
    stem = name.removesuffix(".zip")
    (tmp_path / "_unpacked" / stem / stem / "Modelos").mkdir(parents=True)
    remote["payloads"][f"PLEXOS{YMD}.zip"] = {
        f"RES{YMD}.zip": zip_bytes({f"{PCP_SOL}/pcp.accdb": b"A"})
    }
    sol = ps.resolve_solution(DATE, download_root=tmp_path)
    assert sol.source == "PCP" and sol.period is None
    assert sol.accdb_path.name == "pcp.accdb"
    assert remote["downloads"] == [f"PLEXOS{YMD}.zip"]

    # Second call: the RES extraction is reused.
    again = ps.resolve_solution(DATE, source="pcp", download_root=tmp_path)
    assert again.accdb_path == sol.accdb_path
    assert remote["downloads"] == [f"PLEXOS{YMD}.zip"]


def test_resolve_pcp_errors(tmp_path: Path, remote: dict[str, Any]) -> None:
    remote["files"] = []
    with pytest.raises(FileNotFoundError, match=f"no PLEXOS{YMD}.zip"):
        ps.resolve_solution(DATE, source="pcp", download_root=tmp_path)

    write_zip(tmp_path / "PCP" / f"PLEXOS{YMD}.zip", {"DATOS.zip": b"x"})
    with pytest.raises(FileNotFoundError, match="no RES inside"):
        ps.resolve_solution(DATE, source="pcp", download_root=tmp_path)

    (tmp_path / "_unpacked" / f"PCP_RES_{YMD}" / PCP_SOL).mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="no .accdb in"):
        ps.resolve_solution(DATE, source="pcp", download_root=tmp_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture(name="solved")
def _solved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mdb: FakeMdb
) -> ps.SolutionPaths:
    accdb = tmp_path / "sol.accdb"
    accdb.write_bytes(b"x" * 2048)
    sol = ps.SolutionPaths(accdb_path=accdb, source="PID", period=7)
    monkeypatch.setattr(ps, "resolve_solution", lambda *_a, **_k: sol)
    assert mdb.tables
    return sol


def test_cli_tables(
    solved: ps.SolutionPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ps.main(["-q", "tables", "--date", DATE]) == ps.EXIT_OK
    out = capsys.readouterr().out
    assert f"=== PID period=07 solution → {solved.accdb_path} ===" in out
    assert "6 tables:" in out and "    t_period_0" in out


def test_cli_dump(
    solved: ps.SolutionPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert solved.period == 7
    assert ps.main(["-v", "dump", "--date", DATE, "--table", "t_object"]) == 0
    out = capsys.readouterr().out
    assert "table 't_object': 3 rows × 3 cols" in out
    assert "Polpaico220" in out

    csv = tmp_path / "o.csv"
    assert (
        ps.main(["dump", "--date", DATE, "--table", "t_key", "--output", str(csv)]) == 0
    )
    assert len(pd.read_csv(csv)) == 4

    assert ps.main(["dump", "--date", DATE, "--table", "nope"]) == ps.EXIT_NO_DATA
    assert "empty or absent" in capsys.readouterr().out


def test_cli_lmp(
    solved: ps.SolutionPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_pq = tmp_path / "lmp.parquet"
    assert ps.main(["lmp", "--date", DATE, "--output", str(out_pq)]) == 0
    out = capsys.readouterr().out
    assert "=== PID period=07 → sol.accdb ===" in out
    assert "rows: 4; buses: 2; hours: 2" in out
    assert "top 8 buses" in out and "bottom 5 buses" in out
    lmp = pd.read_parquet(out_pq)
    assert {"bus_name", "lmp_usd_mwh"} <= set(lmp.columns)
    assert solved.period == 7


def test_cli_lmp_pcp_no_output(
    monkeypatch: pytest.MonkeyPatch,
    mdb: FakeMdb,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sol = ps.SolutionPaths(accdb_path=tmp_path / "p.accdb", source="PCP", period=None)
    monkeypatch.setattr(ps, "resolve_solution", lambda *_a, **_k: sol)
    assert ps.main(["lmp", "--date", DATE]) == 0
    out = capsys.readouterr().out
    assert "=== PCP → p.accdb ===" in out and "wrote" not in out

    empty = pd.DataFrame(
        columns=["object_name", "datetime", "hour", "period_id", "value"]
    )
    monkeypatch.setattr(ps, "extract_property", lambda *_a, **_k: empty)
    assert ps.main(["lmp", "--date", DATE]) == ps.EXIT_NO_DATA
    assert "no Price data extracted" in capsys.readouterr().out
    assert mdb.calls


def test_cli_summary(
    monkeypatch: pytest.MonkeyPatch,
    mdb: FakeMdb,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, Any] = {}

    def _extract(res: Path, workdir: Path) -> Path:
        seen["res"] = res
        return workdir / "x.accdb"

    def _summarize(reader: Any, ref_bus: str) -> tuple[dict[str, Any], pd.DataFrame]:
        seen["t_object"] = reader("t_object")
        seen["ref_bus"] = ref_bus
        return {"costs": {"total": 1.0}, "cmg_day1": {"B": 2.0}}, pd.DataFrame()

    def _write(summary: dict[str, Any], cmg: Any, out: Path, res: Any) -> None:
        seen["written"] = (summary, out, res)
        assert isinstance(cmg, pd.DataFrame)

    monkeypatch.setattr(_res_summary, "extract_accdb", _extract)
    monkeypatch.setattr(_res_summary, "summarize", _summarize)
    monkeypatch.setattr(_res_summary, "write_summary", _write)

    res = tmp_path / "RES.zip.xz"
    rc = ps.main(
        [
            "summary",
            "--res",
            str(res),
            "--output",
            str(tmp_path / "o"),
            "--tmpdir",
            str(tmp_path),
            "--ref-bus",
            "Crucero220",
        ]
    )
    assert rc == ps.EXIT_OK
    assert seen["res"] == res and seen["ref_bus"] == "Crucero220"
    assert len(seen["t_object"]) == 3
    assert '"cmg_day1": {"B": 2.0}' in capsys.readouterr().out

    sol = ps.SolutionPaths(accdb_path=tmp_path / "d.accdb", source="PCP", period=None)
    monkeypatch.setattr(ps, "resolve_solution", lambda *_a, **_k: sol)
    assert ps.main(["summary", "--date", DATE, "--output", str(tmp_path / "o")]) == 0
    assert seen["written"][2] is None

    assert ps.main(["summary", "--output", str(tmp_path / "o")]) == ps.EXIT_USAGE
    assert "give --res or --date" in capsys.readouterr().err


def test_export_bytes_roundtrip(mdb: FakeMdb) -> None:
    """mdb-export output is parsed from bytes (encoding left to pandas)."""
    mdb.tables["accents"] = pd.DataFrame({"name": ["Charrúa"]})
    df = ps.export_table(Path("x.accdb"), "accents")
    assert df["name"].iloc[0] == "Charrúa"
