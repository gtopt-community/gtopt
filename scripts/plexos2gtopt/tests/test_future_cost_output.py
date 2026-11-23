# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""plexos2gtopt wires the FCF boundary cut through the FutureCost element.

The FutureCost element (``system.future_cost_array``) is the single,
method-neutral source of the boundary cuts in gtopt; the converter must
never write the deprecated ``monolithic_options.boundary_*`` /
``sddp_options.boundary_*`` / ``simulation.boundary_cuts_file`` keys.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from plexos2gtopt import plexos2gtopt as p2g
from plexos2gtopt.entities import BoundaryCutSpec
from plexos2gtopt.tests.test_cli_and_loader import _make_dir_bundle


def _legacy_keys(planning: dict) -> list[str]:
    found = []
    opts = planning.get("options", {})
    for section in ("sddp_options", "monolithic_options"):
        for key in opts.get(section, {}) or {}:
            if key.startswith("boundary_"):
                found.append(f"{section}.{key}")
    for key in planning.get("simulation", {}):
        if key.startswith("boundary_"):
            found.append(f"simulation.{key}")
    return found


def test_boundary_cut_written_to_future_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_extract = p2g.extract_case

    def extract_with_cut(*args, **kwargs):
        case = real_extract(*args, **kwargs)
        return dataclasses.replace(
            case, boundary_cut=BoundaryCutSpec(fcf=1.0, slopes={"R1": 2.0})
        )

    monkeypatch.setattr(p2g, "extract_case", extract_with_cut)
    monkeypatch.setattr(
        p2g, "write_boundary_cut_csv", lambda *_a, **_k: "boundary_cuts.csv"
    )

    bundle_dir = _make_dir_bundle(tmp_path)
    out_dir = tmp_path / "out"
    rc = p2g.convert_plexos_bundle(
        {
            "input_bundle": bundle_dir,
            "horizon_mode": "hourly",
            "horizon_days": 1,
            "output_dir": out_dir,
        }
    )
    assert rc == 0
    planning = json.loads((out_dir / f"{bundle_dir.name}.json").read_text())
    fcf = planning["system"]["future_cost_array"]
    assert fcf == [{"uid": 1, "name": "fcf", "cuts_file": "boundary_cuts.csv"}]
    assert not _legacy_keys(planning)


def test_no_boundary_cut_no_future_cost(tmp_path: Path) -> None:
    bundle_dir = _make_dir_bundle(tmp_path)
    out_dir = tmp_path / "out"
    rc = p2g.convert_plexos_bundle(
        {
            "input_bundle": bundle_dir,
            "horizon_mode": "hourly",
            "horizon_days": 1,
            "output_dir": out_dir,
        }
    )
    assert rc == 0
    planning = json.loads((out_dir / f"{bundle_dir.name}.json").read_text())
    assert "future_cost_array" not in planning["system"]
    assert not _legacy_keys(planning)
