"""Skip / error branches of the waterway-fmin → FlowRight transform."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from plp2gtopt.pmin_flowright_writer import PminFlowRightWriter, _parse_csv
from plp2gtopt.tests.test_pmin_flowright_writer import (
    FakeBlockParser,
    FakeCentralParser,
    FakeManceParser,
    FakeStageParser,
)

_BLOCKS = [
    {"number": 1, "stage": 1, "duration": 1.0},
    {"number": 2, "stage": 1, "duration": 1.0},
]


def _writer(options: dict[str, Any], blocks: Any = None) -> PminFlowRightWriter:
    return PminFlowRightWriter(
        whitelist=[],
        central_parser=FakeCentralParser([]),
        mance_parser=FakeManceParser([]),
        block_parser=FakeBlockParser(_BLOCKS) if blocks is None else blocks,
        stage_parser=FakeStageParser(),
        options=options,
    )


def _ww(uid: Any, name: Any, jb: Any, fmin: Any) -> dict[str, Any]:
    return {"uid": uid, "name": name, "junction_b": jb, "fmin": fmin}


def test_parse_csv_errors_and_blank(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("name\nA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required"):
        _parse_csv(bad)
    good = tmp_path / "good.csv"
    good.write_text("name,enabled\n,true\nA,yes\nB,no\n", encoding="utf-8")
    assert _parse_csv(good) == ["A"]


def test_no_blocks_or_no_waterways(tmp_path: Path) -> None:
    w = _writer({"output_dir": tmp_path}, blocks=FakeBlockParser([]))
    system: dict[str, Any] = {"waterway_array": [_ww(1, "W", "J", 3.0)]}
    assert not w._process_waterway_fmin(system, fail_cost=1.0)  # pylint: disable=protected-access
    assert not _writer({})._process_waterway_fmin({}, fail_cost=1.0)  # pylint: disable=protected-access


def test_unreadable_parquet(tmp_path: Path) -> None:
    (tmp_path / "Waterway").mkdir()
    (tmp_path / "Waterway" / "fmin.parquet").write_text("junk", encoding="utf-8")
    system = {"waterway_array": [_ww(1, "W", "J", "fmin")]}
    assert not _writer({"output_dir": tmp_path})._process_waterway_fmin(  # pylint: disable=protected-access
        system, fail_cost=1.0
    )


def test_skips_and_partial_drop(tmp_path: Path) -> None:
    ww_dir = tmp_path / "Waterway"
    ww_dir.mkdir()
    pd.DataFrame(
        {
            "block": np.array([1, 2], dtype=np.int32),
            "stage": np.array([1, 1], dtype=np.int32),
            "uid:1": [2.0, 2.0],
            "uid:9": [5.0, 5.0],
        }
    ).to_parquet(ww_dir / "fmin.parquet", index=False)
    system: dict[str, Any] = {
        "waterway_array": [
            "not-a-dict",
            _ww(None, "A", "J", 1.0),
            _ww(5, 7, "J", 1.0),
            _ww(6, "NoJ", "", 1.0),
            _ww(7, "NoCol", "J", "fmin"),
            _ww(8, "Neg", "J", -1.0),
            _ww(1, "W1", "J", "fmin"),
            _ww(3, "W3", "J", 4.0),
        ]
    }
    w = _writer({"output_dir": tmp_path, "output_format": "parquet"})
    frs = w._process_waterway_fmin(system, fail_cost=2.0)  # pylint: disable=protected-access
    assert sorted(fr["name"].split("_")[0] for fr in frs) == ["W1", "W3"]
    # uid:9 survives the column drop, so the table is rewritten (not removed).
    assert (ww_dir / "fmin.parquet").exists()


def test_size_mismatch_and_rewrite_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ww_dir = tmp_path / "Waterway"
    ww_dir.mkdir()
    pd.DataFrame(
        {
            "block": np.array([1, 2, 3], dtype=np.int32),
            "stage": np.array([1, 1, 1], dtype=np.int32),
            "uid:1": [2.0, 2.0, 2.0],
        }
    ).to_parquet(ww_dir / "fmin.parquet", index=False)
    system: dict[str, Any] = {"waterway_array": [_ww(1, "W1", "J", "fmin")]}
    w = _writer({"output_dir": tmp_path})
    assert not w._process_waterway_fmin(system, fail_cost=1.0)  # pylint: disable=protected-access

    pd.DataFrame(
        {
            "block": np.array([1, 2], dtype=np.int32),
            "stage": np.array([1, 1], dtype=np.int32),
            "uid:1": [2.0, 2.0],
            "uid:4": [3.0, 3.0],
        }
    ).to_parquet(ww_dir / "fmin.parquet", index=False)

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("ro")

    monkeypatch.setattr(w, "write_dataframe", boom)
    system = {"waterway_array": [_ww(1, "W1", "J", "fmin")], "flow_right_array": []}
    monkeypatch.setattr(w, "_write_parquets", lambda *_a: None)
    assert len(w._process_waterway_fmin(system, fail_cost=1.0)) == 1  # pylint: disable=protected-access


def test_flow_right_array_must_be_list(tmp_path: Path) -> None:
    system: dict[str, Any] = {
        "waterway_array": [_ww(1, "W1", "J", 3.0)],
        "flow_right_array": {},
    }
    with pytest.raises(ValueError, match="must be a list"):
        _writer({"output_dir": tmp_path})._process_waterway_fmin(  # pylint: disable=protected-access
            system, fail_cost=1.0
        )


def test_write_parquets_default_dir_and_missing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    w = _writer({})
    blocks = np.array([1, 2], dtype=np.int32)
    w._write_parquets(  # pylint: disable=protected-access
        [{"uid": 1, "target": "x"}, {"uid": 2, "target": "y"}],
        {2: np.array([1.0, 1.0])},
        blocks,
        blocks,
    )
    assert (Path(os.getcwd()) / "FlowRight").is_dir()
