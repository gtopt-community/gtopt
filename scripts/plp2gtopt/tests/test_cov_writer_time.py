"""Edge branches of :class:`plp2gtopt._writer_time.TimeMixin`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plp2gtopt.gtopt_writer import GTOptWriter
from plp2gtopt.indhor_parser import IndhorParser


class _IdSim:
    """Three simulations; sims 0/1 share stage-1 hydrology 1; no stage rows."""

    num_simulations = 3

    @staticmethod
    def get_index(sim: int, _stage: int) -> int:
        return [1, 1, 2][sim]

    @staticmethod
    def get_stage_map(_sim: int) -> dict[int, int]:
        return {}


def _writer(parsed: dict[str, Any]) -> GTOptWriter:
    return GTOptWriter(SimpleNamespace(parsed_data=parsed))  # type: ignore[arg-type]


def _aflce(nh: int) -> SimpleNamespace:
    return SimpleNamespace(items=[{"num_hydrologies": nh}])


@pytest.mark.parametrize(
    ("spec", "uids"),
    [("first", [1]), ("all", [1, 2]), ("3", [3])],
)
def test_scenarios_with_duplicate_stage1_and_empty_rows(
    spec: str, uids: list[int]
) -> None:
    w = _writer({"idsim_parser": _IdSim(), "aflce_parser": _aflce(3)})
    opts: dict[str, Any] = {"hydrologies": spec, "method": "monolithic"}
    w.process_scenarios(opts)
    scen = w.planning["simulation"]["scenario_array"]
    assert [s["uid"] for s in scen] == uids
    assert opts["_hydrology_stage_maps"] == {}


def test_apertures_early_returns() -> None:
    w = _writer({})
    w.process_apertures({})
    w.process_apertures({"num_apertures": "all"})
    assert "aperture_array" not in w.planning["simulation"]


def test_indhor_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    csv = tmp_path / "indhor.csv"
    csv.write_text(
        "Año,Mes,Dia,Hora,Bloque\n2024,1,1,1,1\n2024,1,1,2,1\n", encoding="utf-8"
    )
    parser = IndhorParser(csv)
    parser.parse()
    w = _writer({"indhor_parser": parser})
    w.process_indhor({"output_dir": tmp_path / "out"})
    assert w.planning["simulation"]["block_hour_map"]

    monkeypatch.chdir(tmp_path)
    w2 = _writer({"indhor_parser": parser})
    monkeypatch.setattr(
        "plp2gtopt._writer_time.IndhorWriter.to_parquet", lambda _s, _d: None
    )
    w2.process_indhor({})
    assert "block_hour_map" not in w2.planning["simulation"]
