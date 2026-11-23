"""CLI tests for cvs2parquet.main()."""

import runpy
import sys
from pathlib import Path

import pandas as pd
import pytest

from cvs2parquet.cvs2parquet import main


def _csv(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("stage,block,val\n1,1,1.5\n2,2,2.5\n")
    return path


def test_main_default_output_path(tmp_path):
    """Without -o the output lands next to the input with a .parquet suffix."""
    a = _csv(tmp_path, "a.csv")
    b = _csv(tmp_path, "b.csv")
    assert main([str(a), str(b)]) == 0
    for stem in ("a", "b"):
        df = pd.read_parquet(tmp_path / f"{stem}.parquet")
        assert df["stage"].dtype == "int32"
        assert df["val"].tolist() == [1.5, 2.5]


def test_main_explicit_output_with_schema(tmp_path):
    """-o and --schema write the requested file via the PyArrow schema path."""
    src = _csv(tmp_path, "in.csv")
    out = tmp_path / "custom.parquet"
    assert main([str(src), "-o", str(out), "--schema", "--no-color"]) == 0
    df = pd.read_parquet(out)
    assert df["block"].dtype == "int32"
    assert not (tmp_path / "in.parquet").exists()


def test_main_output_with_multiple_inputs_errors(tmp_path):
    """--output combined with several inputs is a usage error (exit 2)."""
    a = _csv(tmp_path, "a.csv")
    b = _csv(tmp_path, "b.csv")
    with pytest.raises(SystemExit) as exc:
        main([str(a), str(b), "-o", str(tmp_path / "x.parquet")])
    assert exc.value.code == 2


def test_module_entry_point(tmp_path, monkeypatch):
    """Running the module as a script exits with main()'s return code."""
    src = _csv(tmp_path, "m.csv")
    monkeypatch.setattr(sys, "argv", ["cvs2parquet", str(src)])
    sys.modules.pop("cvs2parquet.cvs2parquet", None)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("cvs2parquet.cvs2parquet", run_name="__main__")
    assert exc.value.code == 0
    assert (tmp_path / "m.parquet").exists()
