"""Optional post-steps of :meth:`GTOptWriter.to_json` / ``write``.

PLEXOS overlay with default report path, emissions defaults (including
the ``--only-emissions`` synthetic boundary-cut ray), disabling the
reservoir flow estimate and the cascade-reduced L1/L2 toggles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from plp2gtopt.plp2gtopt import convert_plp_case

_CASES = Path(__file__).resolve().parents[2] / "cases"


def _convert(tmp_path: Path, case: str = "plp_hydro_4b", **extra: Any) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    opts: dict[str, Any] = {
        "input_dir": _CASES / case,
        "output_dir": out,
        "output_file": out / "case.json",
        "hydrologies": "1",
        "last_stage": -1,
        "last_time": -1,
        "compression": "snappy",
        "probability_factors": None,
        "discount_rate": 0.0,
        "management_factor": 0.0,
    }
    opts.update(extra)
    convert_plp_case(opts)
    return out


def test_overlay_and_only_emissions(tmp_path: Path) -> None:
    src = tmp_path / "plexos.json"
    src.write_text(
        json.dumps(
            {
                "system": {
                    "generator_array": [
                        {"name": "Thermal1", "heat_rate": 9.0, "fuel": "gas"}
                    ],
                    "fuel_array": [{"uid": 1, "name": "gas", "price": 4.0}],
                }
            }
        ),
        encoding="utf-8",
    )
    out = _convert(
        tmp_path,
        plexos_overlay=src,
        emissions=True,
        only_emissions=True,
        emissions_discount_rate=0.05,
    )
    assert (out / "plexos_overlay_report.json").exists()
    assert (out / "plexos_emissions_report.json").exists()
    data = json.loads((out / "case.json").read_text(encoding="utf-8"))
    gen = next(g for g in data["system"]["generator_array"] if g["name"] == "Thermal1")
    assert gen["heat_rate"] == pytest.approx(9.0)


def test_emissions_without_output_dir_reports(tmp_path: Path) -> None:
    out = _convert(
        tmp_path,
        emissions=True,
        emissions_report=tmp_path / "er.json",
        reservoir_flow_estimate=False,
    )
    assert (tmp_path / "er.json").exists()
    assert (out / "case.json").exists()


@pytest.mark.parametrize(
    ("opts", "levels"),
    [
        ({"disable_l1": True, "disable_l2": True}, set()),
        ({"disable_l1": True}, {"L2"}),
        ({"disable_l2": True}, {"L1"}),
    ],
)
def test_cascade_reduced_toggles(
    tmp_path: Path, opts: dict[str, Any], levels: set[str]
) -> None:
    out = _convert(
        tmp_path,
        "plp_min_2bus",
        method="cascade-reduced",
        cascade_reduced_opts=opts,
    )
    written = {tag for tag in ("L1", "L2") if any(out.glob(f"*.{tag}.json"))}
    assert written == levels
