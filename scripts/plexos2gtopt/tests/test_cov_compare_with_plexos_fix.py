# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Regressions in compare_with_plexos (Step-2b bundle pick, Step-3 totals)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from plexos2gtopt import compare_with_plexos as cwp
from plexos2gtopt.compare_with_plexos import _render_uc_block_drilldown


def test_block_drilldown_skips_provenance_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # plexos2gtopt writes ``<case>.provenance.json`` (no ``system`` key)
    # next to the bundle; directory order may list it first.
    prov = tmp_path / "case.provenance.json"
    prov.write_text("{}")
    real_glob = Path.glob

    def _sidecar_first(self: Path, pattern: str) -> list[Path]:
        return sorted(real_glob(self, pattern), key=lambda p: p != prov)

    monkeypatch.setattr(Path, "glob", _sidecar_first)
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "system": {
                    "user_constraint_array": [
                        {"name": "UC1", "expression": "x <= 1"},
                    ]
                }
            }
        )
    )
    buf = io.StringIO()
    rows: list[dict[str, float | str]] = [
        {
            "block": 1,
            "hours": 2.0,
            "rhs": 1.0,
            "op": "<=",
            "plexos_lhs": 0.5,
            "gtopt_lhs": 3.0,
            "slack": 2.0,
        }
    ]
    _render_uc_block_drilldown({"UC1": rows}, tmp_path, Console(file=buf, width=200))
    assert "x <= 1" in buf.getvalue()


def test_plexos_all_keeps_totals_empty_without_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No t_phase_3 layout -> compute_plexos_energy_totals returns {}.
    # The SRMC cost must not be folded into it, otherwise main() sees a
    # truthy dict without ``block_count`` and crashes in Step 3.
    monkeypatch.setattr(cwp, "compute_plexos_energy_totals", lambda _p: {})
    monkeypatch.setattr(
        cwp, "compute_plexos_per_unit_srmc", lambda _p: {"G1": (10.0, 5.0)}
    )
    for name in (
        "compute_plexos_per_bus_lmp",
        "compute_plexos_per_line",
        "compute_plexos_generation_by_technology",
        "compute_plexos_reservoir_volumes",
        "compute_plexos_reservoir_water_value",
        "compute_plexos_battery_operation",
        "compute_plexos_commitment",
    ):
        monkeypatch.setattr(cwp, name, lambda _p: {})
    monkeypatch.setattr(cwp, "_plexos_name_to_category", lambda _p: {})
    data = cwp._compute_plexos_all(tmp_path / "x.accdb")
    assert not data["totals"]
