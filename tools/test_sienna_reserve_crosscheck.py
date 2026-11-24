# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/sienna_reserve_crosscheck.py (no gtopt, no Julia).

A fabricated gtopt CSV output that reproduces the golden's primal must
pass every tolerance; perturbing it must trip the matching check.  The
real goldens are converted to make sure the metrics cover every slug.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sienna_reserve_crosscheck as src  # pylint: disable=wrong-import-position


def _write_long(path: Path, series: dict[int, list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ['"scenario","stage","block","uid","value"']
    for uid, values in series.items():
        rows += [f"1,1,{b + 1},{uid},{v}" for b, v in enumerate(values)]
    path.write_text("\n".join(rows) + "\n")


def _fake_output(
    run_dir: Path, golden: dict[str, Any], payload: dict[str, Any], obj: float
) -> None:
    """gtopt-shaped CSV output equal to the golden's solution."""
    system = payload["system"]
    hours = int(golden["horizon_hours"])
    out = run_dir / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "solution.csv").write_text(
        f"scene,phase,status,status_name,obj_value\n0,0,0,optimal,{obj}\n"
    )
    gens = {
        g["uid"]: golden["gen_dispatch_mw"][g["name"]]
        for g in system["generator_array"]
    }
    _write_long(out / "Generator" / "generation_sol_s0_p0.csv", gens)
    status = {
        c["uid"]: golden["thermal_on"][c["generator"]]
        for c in system["commitment_array"]
    }
    _write_long(out / "Commitment" / "status_sol_s0_p0.csv", status)
    zones = system.get("reserve_zone_array", [])
    services = golden["system_data"]["services"]
    ureq: dict[int, list[float]] = {}
    dreq: dict[int, list[float]] = {}
    for zone in zones:
        base, _, step = zone["name"].rpartition("_s")
        if base in services and step.isdigit():
            # Fill the ORDC steps in order with the golden's served MW.
            served = golden["ordc_served_mw"][base]
            lo = sum(
                z["urreq"]
                for z in zones
                if z["name"].startswith(base + "_s") and z["uid"] < zone["uid"]
            )
            ureq[zone["uid"]] = [min(zone["urreq"], max(0.0, s - lo)) for s in served]
        elif "drreq" in zone:
            dreq[zone["uid"]] = zone["drreq"][0]
        else:
            ureq[zone["uid"]] = zone["urreq"][0]
    _write_long(out / "ReserveZone" / "urequirement_sol_s0_p0.csv", ureq)
    if dreq:
        _write_long(out / "ReserveZone" / "drequirement_sol_s0_p0.csv", dreq)
    up: dict[int, list[float]] = {}
    dn: dict[int, list[float]] = {}
    for prov in system.get("reserve_provision_array", []):
        name = prov["name"]
        dev, svc = prov["generator"], name[len(prov["generator"]) + 1 :]
        series = golden["reserve_provision_mw"].get(svc, {}).get(dev, [0.0] * hours)
        (dn if "drmax" in prov else up)[prov["uid"]] = series
    _write_long(out / "ReserveProvision" / "uprovision_sol_s0_p0.csv", up)
    if dn:
        _write_long(out / "ReserveProvision" / "dprovision_sol_s0_p0.csv", dn)


@pytest.mark.parametrize("slug", src.RESERVE_SLUGS)
def test_golden_solution_passes(slug: str, tmp_path: Path) -> None:
    golden = src.load_golden(slug)
    payload = src.build_reserves(golden)
    obj = float(golden["objective"]) + src.objective_offset(golden)
    _fake_output(tmp_path, golden, payload, obj)
    res = src.compare(slug, golden, payload, tmp_path)
    assert not src.failures(res)
    assert res.commitment_mismatches == 0
    assert res.max_dispatch_diff == pytest.approx(0.0)
    assert res.min_reserve_margin >= -1e-9


def test_perturbations_fail(tmp_path: Path) -> None:
    golden = src.load_golden("reserves_uc")
    payload = src.build_reserves(golden)
    bad = json.loads(json.dumps(golden))
    bad["objective"] = float(golden["objective"]) + 10.0
    bad["gen_dispatch_mw"]["Alta"][0] += 5.0
    bad["thermal_on"]["Alta"][0] = 1.0 - round(golden["thermal_on"]["Alta"][0])
    bad["reserve_requirement_mw"]["Reserve1"][0] += 1.0
    bad["ordc_served_mw"]["ORDC1"][0] -= 3.0
    obj = float(golden["objective"]) + src.objective_offset(golden)
    _fake_output(tmp_path, golden, payload, obj)
    res = src.compare("reserves_uc", bad, payload, tmp_path)
    text = " | ".join(src.failures(res))
    for word in ("objective", "total generation", "requirement", "ORDC"):
        assert word in text
    assert res.commitment_mismatches == 1
    assert res.max_dispatch_unit == "Alta@h1"


def test_read_field_wide_layout_and_missing(tmp_path: Path) -> None:
    folder = tmp_path / "output" / "X"
    folder.mkdir(parents=True)
    (folder / "f.csv").write_text(
        "scenario,stage,block,uid:1,uid:2\n1,1,2,3,\n1,1,1,1,2\n"
    )
    assert src.read_field(tmp_path, "X", "f") == {
        "uid:1": [1.0, 3.0],
        "uid:2": [2.0, 0.0],
    }
    with pytest.raises(FileNotFoundError):
        src.read_field(tmp_path, "X", "nope")
