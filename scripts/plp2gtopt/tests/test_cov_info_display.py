"""Tests for :mod:`plp2gtopt.info_display` (``plp2gtopt --info``)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plp2gtopt.info_display import (
    _aperture_stage_groups,
    _compact_range,
    display_plp_info,
)

_CASES = Path(__file__).resolve().parents[2] / "cases"


def test_compact_range() -> None:
    assert _compact_range([]) == ""
    assert _compact_range([55, 51, 52, 53, 54]) == "51-55"
    assert _compact_range([1, 2, 51, 52]) == "1-2,51-52"
    assert _compact_range([1, 3, 5]) == "1,3,5"


def test_aperture_stage_groups() -> None:
    assert _aperture_stage_groups(None, 3) == "  (no plpidap2.dat)"
    table = {1: [1, 2], 2: [1, 2], 3: [5]}
    parser = SimpleNamespace(num_stages=3, get_apertures=lambda s: table[s])
    text = _aperture_stage_groups(parser, 5)
    assert text.splitlines() == [
        "  Stages 1-2: 2 apertures  [1-2]",
        "  Stage 3: 1 apertures  [5]",
    ]


def test_display_case_with_apertures(capsys: pytest.CaptureFixture[str]) -> None:
    display_plp_info({"input_dir": _CASES / "plp_hydro_4b", "last_stage": 2})
    out = capsys.readouterr().out
    assert "PLP Case Summary" in out
    assert "[plpidsim.dat]" in out
    assert "[plpidap2.dat]" in out
    assert "[plpidape.dat]" in out
    assert "-a all" in out and "-s 2" in out
    assert "gtopt_hydro_4b" in out


def test_display_minimal_case(capsys: pytest.CaptureFixture[str]) -> None:
    display_plp_info({"input_dir": _CASES / "plp_min_1bus"})
    out = capsys.readouterr().out
    assert "apertures disabled" in out
    assert "no plpidsim.dat" in out
    assert "-a all" not in out


def test_display_extra_aperture_hydros(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Aperture classes outside the forward set are reported as extra."""
    case = tmp_path / "case"
    case.mkdir()
    src = _CASES / "plp_hydro_4b"
    for f in src.iterdir():
        if f.is_file():
            (case / f.name).write_bytes(f.read_bytes())
    (case / "plpidsim.dat").unlink()
    display_plp_info({"input_dir": case})
    out = capsys.readouterr().out
    assert "gtopt_case" in out
    assert "aperture" in out.lower()
