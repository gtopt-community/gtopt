# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Markdown digest writer (``_report.write_report``)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from gtopt_marginal_units._report import write_report


def test_report_full_sections_with_truncation(tmp_path: Path):
    per_bus = pd.DataFrame(
        {
            "gen_uid": [1, 1, 2, 3, None],
            "gen_name": ["a", "a", "b", "c", None],
            "is_marginal": [True, True, True, True, None],
        }
    )
    per_zone = pd.DataFrame(
        {"scenario": [1, 1], "stage": [1, 1], "block": [1, 2], "zone_id": [0, 1]}
    )
    sat = pd.DataFrame({"line_uid": [10, 10, 11, 12]})
    unattr = pd.DataFrame({"reason": ["x", "x", "y", "z"]})
    out = write_report(
        tmp_path / "sub" / "r.md",
        per_bus=per_bus,
        per_zone=per_zone,
        saturated_lines=sat,
        unattributed=unattr,
        extras={"mode": "simulated"},
        top_n_marginal=2,
        top_n_lines=2,
        top_n_reasons=2,
    )
    text = out.read_text(encoding="utf-8")
    assert "- Cells:     2" in text
    assert "- Zones:     2" in text
    assert "- Unattributed cells: 4" in text
    assert "- mode: simulated" in text
    assert "_Showing 2 of 3 distinct marginal units" in text
    assert "| 1 | a | 2 |" in text
    assert "_Showing 2 of 3 saturated lines" in text
    assert "| 10 | 2 |" in text
    assert "_Showing 2 of 3 distinct reasons" in text
    assert "| x | 2 |" in text


def test_report_minimal_inputs(tmp_path: Path):
    per_bus = pd.DataFrame({"gen_uid": [1], "is_marginal": [False]})
    out = write_report(
        tmp_path / "r.md",
        per_bus=per_bus,
        per_zone=pd.DataFrame({"x": [1, 2, 3]}),
        saturated_lines=pd.DataFrame(),
        unattributed=pd.DataFrame({"cell": [1]}),
    )
    text = out.read_text(encoding="utf-8")
    assert "- Cells:     3" in text
    assert "- Zones:     0" in text
    assert "marginal units" not in text
    assert "Saturated lines" not in text
    assert "## Unattributed cells" in text
    assert "| reason |" not in text


def test_report_marginal_without_name_and_no_truncation(tmp_path: Path):
    per_bus = pd.DataFrame({"gen_uid": [5], "is_marginal": [True]})
    out = write_report(
        tmp_path / "r.md",
        per_bus=per_bus,
        per_zone=pd.DataFrame(),
        saturated_lines=pd.DataFrame({"line_uid": [1]}),
        unattributed=pd.DataFrame({"reason": ["only"]}),
    )
    text = out.read_text(encoding="utf-8")
    assert "| 5 |  | 1 |" in text
    assert "_Showing" not in text
