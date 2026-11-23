"""Edge branches of ``simulation_writer`` and ``tech_detect`` loaders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plp2gtopt import tech_detect
from plp2gtopt.indhor_parser import IndhorParser
from plp2gtopt.simulation_writer import SimulationWriter


def test_simulation_cascade_and_indhor(tmp_path: Path) -> None:
    assert SimulationWriter._normalize_method("cascade") == "cascade"  # pylint: disable=protected-access
    csv = tmp_path / "indhor.csv"
    csv.write_text("Año,Mes,Dia,Hora,Bloque\n2024,1,1,1,1\n", encoding="utf-8")
    parser = IndhorParser(csv)
    parser.parse()
    sim = SimulationWriter({"indhor_parser": parser}, {"output_dir": tmp_path / "out"})
    sim._build_indhor()  # pylint: disable=protected-access
    sim2 = SimulationWriter({}, {})
    sim2._build_indhor()  # pylint: disable=protected-access
    assert sim.build() is not None


def test_simulation_indhor_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "indhor.csv"
    csv.write_text("Año,Mes,Dia,Hora,Bloque\n2024,1,1,1,1\n", encoding="utf-8")
    parser = IndhorParser(csv)
    parser.parse()
    monkeypatch.setattr(
        "plp2gtopt.simulation_writer.IndhorWriter.to_parquet", lambda _s, _d: None
    )
    sim = SimulationWriter({"indhor_parser": parser}, {"output_dir": tmp_path})
    sim._build_indhor()  # pylint: disable=protected-access


def test_centipo_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "centipo.csv").write_text(
        "Central Tipo\n\n# c\nSOLAR1  PFV\nONLY\n'W1' 'EOL'\n", encoding="latin-1"
    )
    got = tech_detect.load_centipo_csv(tmp_path)
    assert got["SOLAR1"] == "solar" and "ONLY" not in got
    assert "W1" in got

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr("plp2gtopt.compressed_open.compressed_open", boom)
    assert not tech_detect.load_centipo_csv(tmp_path)


def test_load_overrides_variants(tmp_path: Path) -> None:
    assert tech_detect.load_overrides("A:solar, ,bad,B : wind") == {
        "A": "solar",
        "B": "wind",
    }
    js = tmp_path / "o.json"
    js.write_text(json.dumps([{"name": "A", "type": "solar"}, {"x": 1}]))
    assert tech_detect.load_overrides(str(js)) == {"A": "solar"}
    js.write_text(json.dumps({"B": "wind"}))
    assert tech_detect.load_overrides(str(js)) == {"B": "wind"}
    js.write_text(json.dumps(3))
    assert tech_detect.load_overrides(str(js)) == {}
    csv = tmp_path / "o.csv"
    csv.write_text("# c\n\nA,solar\nlonely\n", encoding="utf-8")
    assert tech_detect.load_overrides(str(csv)) == {"A": "solar"}
