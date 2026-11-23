"""Tests for the pasada routing in :mod:`plp2gtopt._writer_generation`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plp2gtopt._writer_generation import GenerationMixin

_PASADA = [
    {"name": "RIO_A", "bus": 1},
    {"name": "PV_SUN", "bus": 2},
    {"name": "EOLICO_W", "bus": 3},
    {"name": "OFFLINE", "bus": 0},
]


def _mixin(parsed: dict[str, Any]) -> GenerationMixin:
    gm = GenerationMixin()
    gm.parser = SimpleNamespace(parsed_data=parsed)
    gm.planning = {}
    return gm


def _with_pasada(centrals: list[dict[str, Any]] | None) -> GenerationMixin:
    cp = SimpleNamespace(centrals_of_type={"pasada": centrals} if centrals else {})
    return _mixin({"central_parser": cp})


@pytest.mark.parametrize(
    "parsed",
    [
        {},
        {"central_parser": SimpleNamespace(centrals_of_type=None)},
        {"central_parser": SimpleNamespace(centrals_of_type={"pasada": []})},
    ],
)
def test_no_pasada_leaves_options_untouched(parsed: dict[str, Any]) -> None:
    opts: dict[str, Any] = {}
    _mixin(parsed).classify_pasada_centrals(opts)
    assert not opts


@pytest.mark.parametrize(
    ("mode", "hydro", "profile"),
    [
        ("profile", set(), {"RIO_A", "PV_SUN", "EOLICO_W"}),
        ("hydro", {"RIO_A", "PV_SUN", "EOLICO_W"}, set()),
        ("flow-turbine", {"RIO_A", "PV_SUN", "EOLICO_W"}, set()),
    ],
)
def test_global_modes(mode: str, hydro: set[str], profile: set[str]) -> None:
    opts: dict[str, Any] = {"pasada_mode": mode}
    _with_pasada(_PASADA).classify_pasada_centrals(opts)
    assert opts["_pasada_hydro_names"] == hydro
    assert opts["_pasada_profile_names"] == profile


def test_auto_mode_with_centipo_and_user_overrides(tmp_path: Path) -> None:
    (tmp_path / "centipo.csv").write_text(
        "Central,Tipo_T\nPV_SUN,PFV\n", encoding="latin-1"
    )
    opts: dict[str, Any] = {
        "input_dir": str(tmp_path),
        "tech_overrides": {"EOLICO_W": "wind"},
    }
    _with_pasada(_PASADA).classify_pasada_centrals(opts)
    assert opts["_pasada_profile_names"] == {"PV_SUN", "EOLICO_W"}
    assert "RIO_A" in opts["_pasada_hydro_names"]
    assert "OFFLINE" not in (
        opts["_pasada_hydro_names"] | opts["_pasada_profile_names"]
    )


def test_auto_mode_without_input_dir() -> None:
    opts: dict[str, Any] = {}
    _with_pasada(_PASADA).classify_pasada_centrals(opts)
    assert opts["_pasada_profile_names"] == set()
    assert opts["_pasada_hydro_names"] == {"RIO_A", "PV_SUN", "EOLICO_W"}
