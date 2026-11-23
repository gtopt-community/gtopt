# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for :mod:`gtopt_shared.state_snapshot`."""

from __future__ import annotations

import argparse
import enum
import json
import logging
from pathlib import Path

import pytest

from gtopt_shared import state_snapshot as ss


class _Color(enum.Enum):
    RED = 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tool")
    p.add_argument("--input", type=Path)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--count", type=int)
    p.add_argument("--files", type=Path, nargs="*")
    p.add_argument("--name", default="x")
    p.add_argument("--from-state", type=Path)
    return p


def test_write_and_load_roundtrip(tmp_path):
    ns = argparse.Namespace(
        input=Path("a/b.json"),
        values=(1, 2),
        nested={1: [Path("c")]},
        color=_Color.RED,
        flag=True,
        nothing=None,
    )
    path = ss.write_state_snapshot(
        output_dir=tmp_path / "out",
        tool_name="tool",
        args=ns,
        tool_version="1.2",
        extra={"opt": frozenset({"a"})},
    )
    assert path.name == "tool_state.json"
    assert not path.with_suffix(".json.tmp").exists()
    payload = ss.load_state_snapshot(path)
    assert payload["_meta"]["tool"] == "tool"
    assert payload["_meta"]["version"] == "1.2"
    assert payload["args"] == {
        "input": "a/b.json",
        "values": [1, 2],
        "nested": {"1": ["c"]},
        "color": "_Color.RED",
        "flag": True,
        "nothing": None,
    }
    assert payload["extra"] == {"opt": "frozenset({'a'})"}

    # Default extra → empty dict.
    path2 = ss.write_state_snapshot(
        output_dir=tmp_path / "o2", tool_name="t2", args=argparse.Namespace()
    )
    assert json.loads(path2.read_text())["extra"] == {}


def test_load_state_snapshot_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        ss.load_state_snapshot(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"_meta": {}}))
    with pytest.raises(ValueError, match="missing 'args'"):
        ss.load_state_snapshot(bad)


def test_apply_state_to_args_merges_and_coerces(caplog):
    state = {
        "input": "data/case.json",
        "scale": "2.5",
        "count": "not-an-int",
        "files": ["a.csv", Path("b.csv")],
        "name": "from-state",
        "from_state": None,
    }
    argv = [
        "--from-state",
        "s.json",
        "--from-state=t.json",
        "--name",
        "cli",
    ]
    parser = _parser()
    with caplog.at_level(logging.WARNING, logger=ss.__name__):
        merged = ss.apply_state_to_args(parser, state, argv)
    assert merged.input == Path("data/case.json")
    assert merged.scale == pytest.approx(2.5)
    assert merged.count == "not-an-int"  # coercion failure is logged
    assert "could not coerce count" in caplog.text
    assert merged.files == [Path("a.csv"), Path("b.csv")]
    assert merged.name == "cli"
    assert merged.from_state is None


def test_apply_state_skips_missing_dests_and_non_str_values():
    parser = _parser()
    merged = ss.apply_state_to_args(parser, {"scale": 3.0, "count": 4}, [])
    assert merged.scale == 3.0
    assert merged.count == 4
    assert not hasattr(merged, "input")


@pytest.mark.parametrize(
    ("writer", "tool"),
    [
        (ss.write_plexos2gtopt_readme, "plexos2gtopt"),
        (ss.write_plp2gtopt_readme, "plp2gtopt"),
        (ss.write_pp2gtopt_readme, "pp2gtopt"),
        (ss.write_nrel118_to_gtopt_readme, "nrel118_to_gtopt"),
        (ss.write_gtopt_readme, "gtopt"),
    ],
)
def test_readme_writers(tmp_path, writer, tool):
    out = writer(tmp_path / "o")
    text = out.read_text()
    assert out.name == "README.md"
    assert text.startswith(f"# {tool} output directory")
    assert f"{tool}_state.json" in text
    assert "## Reproducing this run" in text
