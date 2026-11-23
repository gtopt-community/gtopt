"""Regression: ``--pasada-mode hydro`` must not duplicate Flow/Turbine uids.

In ``hydro`` mode the JunctionWriter already emits the junction / waterway /
flow / turbine topology for the pasada centrals; ``process_flow_turbines``
used to add a second flow+turbine pair with the same uids.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from plp2gtopt.plp2gtopt import convert_plp_case

_CASES = Path(__file__).resolve().parents[2] / "cases"


def _convert(tmp_path: Path, mode: str) -> dict[str, Any]:
    out = tmp_path / mode
    out.mkdir()
    opts: dict[str, Any] = {
        "input_dir": _CASES / "plp_hydro_4b",
        "output_dir": out,
        "output_file": out / "case.json",
        "hydrologies": "1",
        "last_stage": -1,
        "last_time": -1,
        "compression": "snappy",
        "probability_factors": None,
        "discount_rate": 0.0,
        "management_factor": 0.0,
        "pasada_mode": mode,
    }
    convert_plp_case(opts)
    system: dict[str, Any] = json.loads(
        (out / "case.json").read_text(encoding="utf-8")
    )["system"]
    return system


@pytest.mark.parametrize("mode", ["hydro", "flow-turbine"])
def test_pasada_flow_turbine_uids_unique(tmp_path: Path, mode: str) -> None:
    system = _convert(tmp_path, mode)
    for key in ("flow_array", "turbine_array"):
        uids = Counter(e["uid"] for e in system.get(key, []))
        assert not [u for u, n in uids.items() if n > 1], key
    assert system["turbine_array"]
