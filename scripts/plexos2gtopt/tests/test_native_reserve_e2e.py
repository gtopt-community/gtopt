# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""End-to-end ``--reserve-model native`` on a mini CEN-PCP-shaped bundle.

The fixture ``fixtures/native_reserve/DBSEN_PRGDIARIO.xml`` (regenerate
with ``make_fixture.py`` there) has three units, a BESS with its SSCC
rows, the CPF / CSF / CTF reserve constraints (Sense 0 / unset), an
overlapping dated coefficient set, a ``CTFOFF`` row, the ``CSF_RS_Def``
definition, the northern inertia minima with a Guacolda-style credit and
an ST-excluded ``SpecialCPFNorth`` row.  The test
converts it through the real CLI pipeline and checks the gtopt JSON field
by field; when a ``gtopt`` binary is available it also solves the
exogenous case and checks that no LMP is negative.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt.plexos2gtopt import convert_plexos_bundle

_BUNDLE = Path(__file__).resolve().parent / "fixtures" / "native_reserve"


def _convert(tmp_path: Path, **opts: Any) -> tuple[dict[str, Any], Path]:
    out_dir = tmp_path / "out"
    out_file = out_dir / "native_reserve.json"
    options: dict[str, Any] = {
        "input_bundle": _BUNDLE,
        "output_dir": out_dir,
        "output_file": out_file,
        "horizon_mode": "hourly",
        "horizon_days": 1,
        "lp_relax": True,
        "run_check": False,
    }
    options.update(opts)
    convert_plexos_bundle(options)
    return json.loads(out_file.read_text()), out_dir


#: The fixture's ``CSF_RS_Def`` / ``MAXCSF_COAL`` / ``CTFOFF_GAS`` rows ship
#: no Penalty Price and are jointly infeasible as HARD rows (the default
#: since PLEXOS treats unpenalised constraints as hard); the solve tests
#: opt back into softening them, as the converter did before.
_SOLVE_OPTS: dict[str, Any] = {"soft_unpenalised_constraints": 1000.0}


def _zones(planning: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {z["name"]: z for z in planning["system"]["reserve_zone_array"]}


def _pampl(out_dir: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(out_dir.glob("uc_*.pampl")))


def test_exogenous_conversion(tmp_path: Path) -> None:
    planning, out_dir = _convert(tmp_path)
    zones = _zones(planning)
    assert {"CPF10s", "CPF5mUp", "CPF5mDown", "CSF_UpMinProvision"} <= set(zones)
    assert "CTF_UpMinProvision" in zones
    assert not any(n.startswith("Special") for n in zones)

    # CPF10s: dated set 2026-11-07 (36.6) wins over the open-ended
    # 2026-01-01 set (30) and the undated base (20).
    up10 = zones["CPF10s"]
    p1, p1_floor, p2 = up10["urreq_planes"]
    assert p1["demand_coef"] == pytest.approx(-0.0366)
    assert p1["inertia_coef"] == pytest.approx(-0.0123)
    assert p1["contingency_coef"] == pytest.approx(1.25)
    # 50 − 12.3·(100/1000): the always-on inertia constant folds into alpha.
    assert p1["alpha"] == pytest.approx(50.0 - 1.23)
    assert p1_floor["alpha"] == pytest.approx(50.0 - 1.23 + 1.25 * 120.0)
    assert "contingency_coef" not in p1_floor
    assert p2 == {"alpha": 30.0, "demand_coef": pytest.approx(-0.01)}
    assert up10["load_term"] == "exogenous"
    assert up10["demand_load_factor"] == pytest.approx(1.05)
    # Default inertia term: commitment, I from the Inertia_SEN zone.
    assert up10["inertia_term"] == "commitment"
    assert up10["inertia_zone"] == "Inertia_SEN"
    assert "system_inertia" not in up10
    assert up10["urcost"] == pytest.approx(10000.0)
    assert "urreq" not in up10
    # PLEXOS Sense 0: equality balance; the CPF10s_Slack surplus cost is
    # not emitted (the plane requirement R absorbs any excess).
    assert up10["requirement_sense"] == "eq"
    assert "ursurplus_cost" not in up10
    assert "contingency_includes_reserve" not in up10

    cands = {c["name"]: c for c in up10["urcontingencies"]}
    coal = cands["COAL_Comparison"]
    assert coal["generators"] == ["COAL", "PV1"]
    assert coal["demands"] == ["BAT1_dem"]
    assert coal["demand_coefs"] == [-1.0]
    assert coal["offset"] == pytest.approx(8.4)
    assert "offset" not in cands["GAS_Comparison"]

    up5 = zones["CPF5mUp"]
    q1, q1_floor = up5["urreq_planes"]
    assert q1 == {
        "alpha": pytest.approx(4.125),
        "demand_coef": pytest.approx(-0.01075),
        "contingency_coef": pytest.approx(1.0912),
    }
    assert q1_floor["alpha"] == pytest.approx(4.125 + 1.0912 * 120.0)
    assert up5["requirement_sense"] == "eq"
    assert "ursurplus_cost" not in up5

    down = zones["CPF5mDown"]
    d1, d1_floor = down["drreq_planes"]
    assert d1["alpha"] == pytest.approx(-8.0)
    assert d1["demand_coef"] == pytest.approx(-0.004)
    assert d1["contingency_coef"] == pytest.approx(0.96)
    assert d1_floor["alpha"] == pytest.approx(-8.0 + 0.96 * 20.0)
    assert down["drcontingencies"] == [
        {"name": "BAT_BAT1_Comparison", "demands": ["BAT1_dem"]}
    ]
    assert down["drcost"] == pytest.approx(10000.0)

    csf = zones["CSF_UpMinProvision"]
    assert csf["urreq"] == [[20.0] * 12 + [25.0] * 12]  # Res_Requirement.csv
    assert csf["requirement_sense"] == "eq"  # unset Sense
    assert "urmin" not in csf  # CSF_RS Min Provision 18 is not a PLEXOS floor
    # One zone per requirement: the extractor's Reserve zones whose
    # provision the row sums (CSF_RS, CSF_RS_BESS) are superseded.
    assert "CSF_RS" not in zones
    assert "CSF_RS_BESS" not in zones
    ctf = zones["CTF_UpMinProvision"]
    assert ctf["urreq"] == [[15.0] * 24]
    assert ctf["urcost"] > 0.0
    assert "requirement_sense" not in ctf  # Sense 1 (>=)

    system = planning["system"]
    izones = {z["name"]: z for z in system["inertia_zone_array"]}
    assert izones["Inertia_SEN"] == {"uid": 1, "name": "Inertia_SEN"}
    # InertiaNRMin_D: timesliced RHS (November: H9-19), continuous credit.
    day = izones["InertiaNRMin_D"]
    assert day["requirement"] == [[0.0] * 8 + [700.0] * 11 + [0.0] * 5]
    assert day["requirement_credits"] == [
        {
            "name": "InertiaGuacolda2",
            "generators": ["COAL", "GAS"],
            "units": 2.0,
            "credit": 200.0,
        }
    ]
    assert izones["InertiaNRMin_N"]["requirement"] == 300.0
    iprov = {p["generator"]: p for p in system["inertia_provision_array"]}
    # One provision per unit, shared by the three zones (same H·S);
    # P_ref = the commitment's Min Stable Level (0.4 × capacity).
    assert iprov["COAL"]["inertia_zones"] == [
        "Inertia_SEN",
        "InertiaNRMin_D",
        "InertiaNRMin_N",
    ]
    assert iprov["COAL"]["provision_max"] == pytest.approx(100.0)
    assert iprov["COAL"]["provision_factor"] * iprov["COAL"][
        "provision_max"
    ] == pytest.approx(500.0)
    assert iprov["GAS"]["provision_factor"] * iprov["GAS"][
        "provision_max"
    ] == pytest.approx(300.0)

    # BESS: native SoC rows, per-side response caps, energy usage, dated
    # Capacity; the AUX battery is not emitted.
    (bat,) = system["battery_array"]
    assert bat["name"] == "BAT1"
    assert bat["emax"] == pytest.approx(150.0)
    assert bat["reserve_sustain_hours"] == pytest.approx(1.0)
    # Deployment energy is per provision (CF_*_COMP: 0.3 CSF, 0 CPF).
    assert "reserve_energy_usage_up" not in bat
    assert "reserve_energy_usage_down" not in bat
    bprov = {p["name"]: p for p in system["reserve_provision_array"]}
    cpf_b = bprov["provision_BAT1_gen__CPF_RS_BESS"]
    assert cpf_b["urmax_response"] == pytest.approx(20.0)
    assert "urmax_charge_response" not in cpf_b
    assert cpf_b["urmax"] == pytest.approx(100.0)  # Pd + Pc
    assert "ur_energy_usage" not in cpf_b
    csf_b = bprov["provision_BAT1_gen__CSF_RS_BESS"]
    profile = [30.0] * 12 + [40.0] * 12
    assert csf_b["urmax_response"] == [profile]
    assert csf_b["urmax_charge_response"] == [profile]
    assert csf_b["ur_energy_usage"] == pytest.approx(0.3)
    # The BESS CSF provision counts in the native zone (Reserve-level
    # Provision Coefficient of CSF_RS_BESS on CSF_UpMinProvision).
    assert csf_b["reserve_zones"] == ["CSF_UpMinProvision"]
    csf_lw = bprov["provision_BAT1_gen__CSF_LW_BESS"]
    assert "urmax_response" not in csf_lw
    assert csf_lw["dr_energy_usage"] == pytest.approx(0.3)
    assert "ur_energy_usage" not in csf_lw

    provs = {p["name"]: p for p in system["reserve_provision_array"]}
    coal_p = provs["provision_COAL"]
    factors = dict(
        zip(
            coal_p["reserve_zones"],
            zip(coal_p["ur_zone_factors"], coal_p["dr_zone_factors"], strict=True),
            strict=True,
        )
    )
    assert factors["CPF10s"] == (pytest.approx(0.9), 0.0)
    assert factors["CPF5mUp"] == (pytest.approx(0.7), 0.0)
    assert factors["CPF5mDown"] == (0.0, pytest.approx(0.5))
    assert factors["CSF_UpMinProvision"] == (1.0, 0.0)
    assert "CSF_RS" not in factors

    text = _pampl(out_dir)
    # Replaced rows are inactive stubs; CTFOFF and the ST-excluded row
    # are untouched (the latter stays inactive, never converted).
    assert "inactive constraint CPF_Up10Calculation_I" in text
    assert "inactive constraint CPF_Up10MinProvision" in text
    assert "inactive constraint Generation_Calculation" in text
    assert "constraint CTFOFF_GAS" in text
    assert "inactive constraint CTFOFF_GAS" not in text
    assert "inactive constraint SpecialCPFNorth:" in text
    assert "replaced by ReserveZone 'SpecialCPFNorth'" not in text
    assert 'reserve_zone("CPF10s").up = 0' in text
    assert 'reserve_zone("CPF5mDown").dn = 0' in text
    # CSF_RS_Def stays a user constraint, as the PLEXOS definition.
    assert 'reserve_provision("provision_BAT1_gen__CSF_RS_BESS").up = 0;' in text
    assert 'decision_variable("CSF_RS_Requirement").value' in text
    assert "inactive constraint CSF_RS_Def" not in text
    for name in ("InertiaNRMin_D", "InertiaNRMin_N", "InertiaCommitGuacolda2"):
        assert f"inactive constraint {name}" in text
    for name in ("UPStorageBound_BAT1", "DOWNStorageBound_BAT1"):
        assert f"inactive constraint {name}" in text


def test_north_special_opt_in(tmp_path: Path) -> None:
    planning, _ = _convert(tmp_path, reserve_north_special=True)
    north = _zones(planning)["SpecialCPFNorth"]
    # Scheduled requirement: the dated RHS of SpecialCPFNorth_Definition
    # (7 MW on 2026-11-07); no corridor-flow term.
    assert north["urreq_planes"] == [{"alpha": pytest.approx(7.0)}]
    assert north["requirement_sense"] == "eq"
    assert "flow_lines" not in north
    provs = {p["name"]: p for p in planning["system"]["reserve_provision_array"]}
    coal = provs["provision_COAL"]
    assert "SpecialCPFNorth" in coal["reserve_zones"]


def test_endogenous_and_uc_modes(tmp_path: Path) -> None:
    planning, _ = _convert(tmp_path / "endo", reserve_load_term="endogenous")
    up10 = _zones(planning)["CPF10s"]
    assert up10["load_term"] == "endogenous_generation"
    assert up10["demand_load_factor"] == 1.0

    planning, out_dir = _convert(tmp_path / "uc", reserve_model="uc")
    assert "CPF10s" not in _zones(planning)
    assert "inertia_zone_array" not in planning["system"]
    assert "constraint CPF_Up10Calculation_I:" in _pampl(out_dir)


def _gtopt_binary() -> Path | None:
    env = os.environ.get("GTOPT_BIN")
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return Path(env)
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "build" / "standalone" / "gtopt"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    on_path = shutil.which("gtopt")
    return Path(on_path) if on_path else None


@pytest.mark.skipif(
    _gtopt_binary() is None,
    reason="gtopt binary not available (set GTOPT_BIN or build standalone)",
)
def test_exogenous_solve_has_no_negative_lmp(tmp_path: Path) -> None:
    pd = pytest.importorskip("pandas")
    binary = _gtopt_binary()
    assert binary is not None
    planning, out_dir = _convert(tmp_path, **_SOLVE_OPTS)
    json_file = out_dir / "native_reserve.json"
    proc = subprocess.run(
        [str(binary), json_file.name, "-d", "output"],
        cwd=out_dir,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    log = proc.stdout + proc.stderr
    if proc.returncode != 0 and "Could not find member" in log:
        pytest.skip(f"{binary} predates the extended reserve schema")
    assert proc.returncode == 0, log[-4000:]
    out = out_dir / "output"
    dual = next(iter((out / "Bus").glob("balance_dual*")))
    frame = pd.read_parquet(dual) if dual.suffix == ".parquet" else pd.read_csv(dual)
    values = frame.select_dtypes("number").drop(
        columns=[c for c in ("stage", "block", "scenario", "uid") if c in frame],
        errors="ignore",
    )
    assert (values.to_numpy() >= -1e-6).all()
    req = next(iter((out / "ReserveZone").glob("urequirement_sol*")))
    assert req.stat().st_size > 0
    assert planning["system"]["reserve_zone_array"]
    # Capped BESS provisions are split per provision (discharge / charge).
    split = list((out / "ReserveProvision").glob("reserve_up_discharge_sol*"))
    assert split, sorted(p.name for p in (out / "ReserveProvision").iterdir())
    # The down-side ``*_RequirementCost`` link resolves ``reserve_zone(..).dn``:
    # the DV equals the CPF5mDown requirement every block.
    system = planning["system"]
    dv_uid = next(
        d["uid"]
        for d in system["decision_variable_array"]
        if d["name"] == "CPF5mDown_Requirement"
    )
    zone_uid = _zones(planning)["CPF5mDown"]["uid"]
    dv = pd.read_parquet(next(iter((out / "DecisionVariable").glob("value_sol*"))))
    dn = pd.read_parquet(next(iter((out / "ReserveZone").glob("drequirement_sol*"))))
    dv_vals = dv[dv["uid"] == dv_uid].sort_values("block")["value"].to_numpy()
    dn_vals = dn[dn["uid"] == zone_uid].sort_values("block")["value"].to_numpy()
    assert len(dn_vals) == 24
    assert dv_vals == pytest.approx(dn_vals)


def _solve(binary: Path, out_dir: Path, json_name: str, dest: str) -> Path:
    proc = subprocess.run(
        [str(binary), json_name, "-d", dest],
        cwd=out_dir,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    log = proc.stdout + proc.stderr
    if proc.returncode != 0 and "Could not find member" in log:
        pytest.skip(f"{binary} predates the extended reserve schema")
    assert proc.returncode == 0, log[-4000:]
    return out_dir / dest


def _zone_duals(pd: Any, out: Path, uid: int) -> list[float]:
    """``urequirement_dual`` of zone ``uid`` per block (absent rows = 0)."""
    path = next(iter((out / "ReserveZone").glob("urequirement_dual*")))
    frame = pd.read_parquet(path)
    by_block = [float(v) for v in frame[frame["uid"] == uid]["value"]]
    return by_block or [0.0] * 24


@pytest.mark.skipif(
    _gtopt_binary() is None,
    reason="gtopt binary not available (set GTOPT_BIN or build standalone)",
)
def test_single_zone_dual_is_net_of_duplicates(tmp_path: Path) -> None:
    """The native CSF zone's dual equals the net dual of the old layout.

    Re-adding the superseded extractor zone ``CSF_RS`` (same requirement,
    priced at the 794 $/MW shortage cost) only splits the dual between
    the two zones; their sum is the single native zone's dual.
    """
    pd = pytest.importorskip("pandas")
    binary = _gtopt_binary()
    assert binary is not None
    planning, out_dir = _convert(tmp_path, **_SOLVE_OPTS)
    csf = _zones(planning)["CSF_UpMinProvision"]
    first = _solve(binary, out_dir, "native_reserve.json", "o1")
    single = _zone_duals(pd, first, csf["uid"])

    system = planning["system"]
    system["reserve_zone_array"].append(
        {"uid": 99, "name": "CSF_RS", "urreq": csf["urreq"], "urcost": 794.11}
    )
    for prov in system["reserve_provision_array"]:
        if "CSF_UpMinProvision" in prov["reserve_zones"]:
            prov["reserve_zones"].append("CSF_RS")
            if "ur_zone_factors" in prov:
                prov["ur_zone_factors"].append(1.0)
                prov["dr_zone_factors"].append(0.0)
    (out_dir / "dup.json").write_text(json.dumps(planning))
    dup = _solve(binary, out_dir, "dup.json", "o2")
    net = [
        a + b
        for a, b in zip(
            _zone_duals(pd, dup, csf["uid"]), _zone_duals(pd, dup, 99), strict=True
        )
    ]
    assert single == pytest.approx(net, rel=1e-6, abs=1e-3)
