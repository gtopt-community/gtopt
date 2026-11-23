"""Edge branches of :mod:`plp2gtopt._plexos_overlay` not hit elsewhere."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import plp2gtopt._plexos_overlay as overlay_mod
from plp2gtopt._plexos_overlay import _resolve_overlay_path, apply_plexos_overlay


def _write(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _planning() -> dict[str, Any]:
    return {
        "system": {
            "generator_array": [
                {"uid": 1, "name": "G1", "gcost": 5.0, "fuel": {"name": "coal"}},
                {"uid": 2, "name": "G2", "gcost": "gcost", "fuel": "gas"},
                {"uid": 3, "name": "G3"},
                {"uid": 4, "name": "G4", "fuel": "diesel"},
            ],
            "fuel_array": [],
            "emission_array": [{"uid": 3, "name": "CO2"}],
            "emission_zone_array": [{"uid": 2, "name": "Z1"}],
        }
    }


def _source() -> dict[str, Any]:
    return {
        "system": {
            "generator_array": [
                {"name": "G1", "lossfactor": [0.1], "gcost": "x", "cogen_mode": ""},
                {"name": "G2", "gcost": 7.0, "lossfactor": 0.02},
                {"name": "G3", "cogen_mode": "must_run"},
            ],
            "fuel_array": [],
            "emission_array": ["bad", {"name": ""}, {"name": "co2"}, {"name": "NOx"}],
            "emission_zone_array": [
                "bad",
                {"name": " "},
                {"name": "z1"},
                {"name": "Z2", "price": 3.0},
            ],
        }
    }


def test_overlay_skips_and_fallback(tmp_path: Path) -> None:
    src = _write(tmp_path / "src.json", _source())
    emis = _write(
        tmp_path / "emis.json",
        {
            "fuels": [],
            "generator_overrides": {
                "_comment": "x",
                "G1": {"type": "thermal:cogen", "expected_fuel_match": "other"},
                "G2": {"type": "renewable:geothermal"},
                "G4": {"type": ""},
            },
        },
    )
    planning = _planning()
    report = apply_plexos_overlay(
        planning,
        src,
        report_path=tmp_path / "rep" / "r.json",
        emissions_fallback_path=emis,
    )
    gens = {g["name"]: g for g in planning["system"]["generator_array"]}
    assert gens["G1"]["fuel"] == {"name": "coal"}  # fuel mismatch -> untouched
    assert "fuel" not in gens["G2"] and gens["G2"]["type"] == "renewable:geothermal"
    assert "fuel" not in gens["G4"] and "type" not in gens["G4"]
    assert gens["G3"]["cogen_mode"] == "must_run"
    assert gens["G2"]["gcost"] == "gcost"
    assert gens["G2"]["lossfactor"] == pytest.approx(0.02)
    skipped = report.skipped_fields
    assert "lossfactor (non-scalar)" in skipped["G1"]
    assert "gcost (non-scalar)" in skipped["G1"]
    assert "gcost (plp profile preserved)" in skipped["G2"]
    names = [e["name"] for e in planning["system"]["emission_array"]]
    assert names == ["CO2", "NOx"]
    zones = [z["name"] for z in planning["system"]["emission_zone_array"]]
    assert zones == ["Z1", "Z2"]
    assert (tmp_path / "rep" / "r.json").exists()


def test_fallback_without_overrides(tmp_path: Path) -> None:
    src = _write(tmp_path / "src.json", {"system": {"generator_array": []}})
    emis = _write(tmp_path / "emis.json", {"fuels": []})
    planning: dict[str, Any] = {"system": {"generator_array": []}}
    apply_plexos_overlay(planning, src, emissions_fallback_path=emis)
    assert not planning["system"]["generator_array"]


def test_resolve_latest_blank_and_bad_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _write(tmp_path / "run.json", {})
    reg = tmp_path / "runs.jsonl"
    reg.write_text(
        json.dumps({"output_file": str(target)})
        + "\n\n"
        + json.dumps({"output_file": 3})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(overlay_mod, "_PLEXOS_RUN_REGISTRY", reg)
    assert _resolve_overlay_path(Path("latest")) == target
