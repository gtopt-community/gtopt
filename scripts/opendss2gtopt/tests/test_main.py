# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI tests for opendss2gtopt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendss2gtopt.main import main

DATA = Path(__file__).parent / "data"
TINY = DATA / "tiny_feeder.dss"


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return int(exc.value.code or 0)


def test_info(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["--info", str(TINY)]) == 0
    out = capsys.readouterr().out
    assert "regulators   : 1" in out and "kV bases 4.16, 12.47" in out


def test_convert_dc_and_ldf(tmp_path: Path) -> None:
    assert _run(["-i", str(TINY), "-o", str(tmp_path / "a")]) == 0
    p = json.loads((tmp_path / "a" / "tiny.json").read_text())
    assert p["system"]["name"] == "tiny"
    phases = json.loads((tmp_path / "a" / "tiny_phases.json").read_text())
    assert phases["branches"][0]["members"]
    assert (
        _run(
            [
                str(TINY),
                "-o",
                str(tmp_path / "b"),
                "--name",
                "t2",
                "--network-model",
                "lindistflow",
                "--line-rating",
                "none",
                "--vmin",
                "0.9",
                "--fixed-taps",
                "--no-line-charging",
                "--no-source-impedance",
                "--time-mode",
                "daily",
            ]
        )
        == 0
    )
    p2 = json.loads((tmp_path / "b" / "t2.json").read_text())
    assert all(
        "tmax_ab" not in ln
        for ln in p2["system"]["line_array"]
        if ln.get("type") != "transformer"
    )
    assert _run([str(TINY), "-o", str(tmp_path / "c"), "--line-rating", "emerg"]) == 0


def test_convert_default_outdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _run([str(TINY)]) == 0
    assert (tmp_path / "tiny" / "tiny.json").is_file()


def test_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run([]) == 2
    assert _run([str(tmp_path / "missing.dss")]) == 1
    bad = tmp_path / "bad.dss"
    bad.write_text("New Load.x bus1=a\n")
    assert _run([str(bad)]) == 1
    assert "error" in capsys.readouterr().err


def test_dunder_main() -> None:
    import runpy  # noqa: PLC0415

    with pytest.raises(SystemExit):
        runpy.run_module("opendss2gtopt", run_name="__main__", alter_sys=True)
