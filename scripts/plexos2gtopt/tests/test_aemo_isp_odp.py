"""Tests for :mod:`plexos2gtopt.aemo_isp_odp` (ODP augmentation builds)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from plexos2gtopt import aemo_isp_constraints as isp_c
from plexos2gtopt import aemo_isp_odp as odp
from plexos2gtopt.aemo_isp import IspCaseOptions, build_dispatch_case
from plexos2gtopt.tests.test_aemo_isp_constraints import KWH_PER_KG, _make_ccase

openpyxl = pytest.importorskip("openpyxl")


def _workbook(path: Path, s1_mw: float = 100.0) -> Path:
    """A tiny outlook workbook in the published 2026 ISP layout."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Timings"
    ws.append(["Scenario", "CDP", "SensitivityName", "Line", "StartFYE", "LineName"])
    ws.append(["Step Change - Core", "CDP4 (ODP)", "x", "S1 Option 1", 2027, "S1 Exp"])
    ws.append(["Step Change - Core", "CDP40", "x", "S1 Option 9", 2027, "Other"])
    rez = wb.create_sheet("REZ Generation Capacity")
    rez.append(["Installed Generator Capacity by REZ (MW)"])
    rez.append(["CDP", "Region", "REZ", "REZ Name", "Technology", "2026-27", "2027-28"])
    rez.append(["CDP4 (ODP)", "SA", "S1", "Test", "Wind", s1_mw - 20.0, 500.0])
    rez.append(["CDP4 (ODP)", "SA", "S1", "Test", "Solar", 20.0, 0.0])
    rez.append(["CDP4 (ODP)", "SA", "S1", "Test", "Alkaline Electrolyser", 99.0, 0])
    rez.append(["CDP1", "SA", "S1", "Test", "Wind", 999.0, 999.0])
    det = wb.create_sheet("Augmentation details")
    det.append(["Project", "EISD", "End", "Forward increase (MW)", "Reverse (MW)"])
    det.append(["S1 Exp", "2026-27", "2028-29", 10, 0])
    out = path / "outlook.xlsx"
    wb.save(out)
    return out


def test_fy_and_cdp_helpers() -> None:
    assert odp.fy_label(date(2027, 1, 18)) == "2026-27"
    assert odp.fy_label(date(2027, 7, 1)) == "2027-28"
    assert odp.cdp_matches("CDP4 (ODP)", "CDP4")
    assert odp.cdp_matches("CDP4", "CDP4")
    assert not odp.cdp_matches("CDP40", "CDP4")
    assert odp.option_name("N5_WNV-SNSW Option 1 - VNI West Augmentation") == (
        "WNV-SNSW Option 1 - VNI West"
    )
    assert odp.option_name("N3_N3 Option 1") == "N3 Option 1"
    assert odp.rez_id("ExportLimit_N7_Tumut") == "N7"


def test_load_outlook(tmp_path: Path) -> None:
    out = odp.load_outlook(_workbook(tmp_path), "CDP4")
    assert out.timings == {"S1 Option 1": 2027}
    assert out.projects["S1 Option 1"] == "S1 Exp"
    # electrolysers are loads, not exportable generation; other CDPs ignored
    assert out.rez_capacity[("S1", "2026-27")] == pytest.approx(100.0)
    assert out.rez_capacity[("S1", "2027-28")] == pytest.approx(500.0)
    assert out.details["S1 Exp"] == (10, 0)


def _export_limit(tmp_path: Path, outlook: odp.OdpOutlook | None):
    xml, traces = _make_ccase(tmp_path, odp_aug=True)
    opts = IspCaseOptions(
        xml=xml,
        trace_roots=[xml.parent, traces],
        start=date(2027, 1, 18),
        days=3,
        name="t",
        isp=isp_c.ConstraintOptions(odp=outlook, electrolyser_kwh_per_kg=KWH_PER_KG),
    )
    case = build_dispatch_case(opts)
    line = next(
        ln for ln in case.planning["system"]["line_array"] if ln.get("type") == "rez"
    )
    tab = case.schedules[("Line", "tmax_ab")][line["uid"]]
    return case, np.asarray(tab)


def test_without_outlook_augmentations_stay_unbuilt(tmp_path: Path) -> None:
    case, limit = _export_limit(tmp_path, None)
    assert np.allclose(limit, 60.0)  # summer RHS, Units 0 folded
    assert "odp_augmentations" not in case.stats


def test_timing_and_rez_capacity_builds(tmp_path: Path) -> None:
    outlook = odp.load_outlook(_workbook(tmp_path), "CDP4")
    case, limit = _export_limit(tmp_path, outlook)
    built = {r["object"]: r for r in case.stats["odp_augmentations"]}
    # S1 Option 1 in service from FY 2026-27: its full Max Units Built
    assert built["S1_S1 Option 1"]["rule"] == "timings"
    assert built["S1_S1 Option 1"]["mw"] == pytest.approx(10.0)
    # the linear one covers the rest of the 100 MW REZ fleet: 100 − (60 + 10)
    assert built["S1_Linear Augmentation 1"]["rule"] == "rez_capacity"
    assert built["S1_Linear Augmentation 1"]["mw"] == pytest.approx(30.0)
    assert np.allclose(limit, 100.0)
    assert case.stats["odp_added_mw"] == pytest.approx(40.0)
    assert case.stats["odp_unmatched"] == {}


def test_rez_capacity_capped_at_max_units_built(tmp_path: Path) -> None:
    outlook = odp.load_outlook(_workbook(tmp_path, s1_mw=1000.0), "CDP4")
    case, limit = _export_limit(tmp_path, outlook)
    built = {r["object"]: r["mw"] for r in case.stats["odp_augmentations"]}
    assert built["S1_Linear Augmentation 1"] == pytest.approx(200.0)
    assert np.allclose(limit, 270.0)
