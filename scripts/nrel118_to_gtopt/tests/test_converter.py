"""Unit tests for the NREL-118 → gtopt converter.

Everything except the ``integration``-marked tests runs on a tiny
synthetic dataset written by :func:`write_mini_dataset` in the upstream
CSV formats (3 buses, 2 regions, 48 hours) — no network, no vendored
NREL data (the upstream repository has no licence file).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nrel118_to_gtopt import _data
from nrel118_to_gtopt._converter import (
    BTU_PER_KWH_TO_GJ_PER_MWH,
    IPCC_AR6_TCO2_PER_GJ,
    SIENNA_FUEL_PRICE_USD_PER_MMBTU,
    CaseOptions,
    HydroBudget,
    _shape_energy,
    build_case,
    collapse_piecewise_heat_rate,
    convert,
    hydro_budget_availability,
    parse_buses,
    unit_type_to_fuel,
)
from nrel118_to_gtopt._data import populate_cache, read_hourly, read_rows
from nrel118_to_gtopt.__main__ import main

N_HOURS = 48

# ---------------------------------------------------------------------------
# Synthetic mini dataset (same CSV formats as NREL-Sienna 118-Bus/)
# ---------------------------------------------------------------------------

_GEN_HEADER = (
    "Generator Name,bus of connection,Max Capacity (MW),Commit,"
    "Data File of Commit,Scenario of Commit,Heat Rate Base (MMBTU/hr),"
    "Heat Rate Inc Band 1 (BTU/kWh),Heat Rate Inc Band 2 (BTU/kWh),"
    "Heat Rate Inc Band 3 (BTU/kWh),Heat Rate Inc Band 4 (BTU/kWh),"
    "Heat Rate Inc Band 5 (BTU/kWh),Load Point Band 1 (MW),"
    "Load Point Band 2 (MW),Load Point Band 3 (MW),Load Point Band 4 (MW),"
    "Load Point Band 5 (MW),Escalator of Load Point (all bands),"
    "Max Ramp Down (MW/min),Max Ramp Up (MW/min),Min Down Time (h),"
    "Min Up Time (h),Min Stable Level (MW),Escalator of Min Stable Level,"
    "Start Cost ($),VO&M Charge ($/MWh),Rating,Escalator of rating,"
    "Committed in following market in PLEXOS"
)
_GEN_ROWS = (
    # thermal: 100 MW CC at bus 1, 50 MW CT Oil at bus 3
    "CC NG 01,bus001,100,0,DA UC,RT,300,6500,,,,,100,,,,,CC NG 01,"
    "2,2,6,8,40,CC NG 01,3000,1.5,100,CC NG 1,DA",
    "CT Oil 01,bus003,50,0,DA UC,RT,200,10000,,,,,50,,,,,CT Oil 01,"
    "5,5,1,1,10,CT Oil 01,500,4,50,CT Oil 1,DA",
    # wind + solar with DA profile files (Rating = own name)
    "Wind 01,bus002,40,,,,,,,,,,,,,,,,,,,,0,,,0,Wind 01,,DA",
    "Solar 01,bus003,30,,,,,,,,,,,,,,,,,,,,0,,,0,Solar 01,,DA",
    # energy-limited hydro (118-hydro.csv) and run-of-river hydro file
    "Hydro 01,bus001,500,1,,,,,,,,,,,,,,,1,1,1,1,0,,,0,,,DA",
    "Hydro 16,bus002,20,1,,,,,,,,,,,,,,,0.1,0.1,1,1,0,,,0,Hydro 16,,DA",
    # monthly fixed-MW hydro (Hydro_nondispatchable.csv)
    "Hydro 36,bus003,10,1,,,,,,,,,,,,,,,0.1,0.1,1,1,0,,,0,,,DA",
)


def _hourly_csv(path: Path, values: np.ndarray, *, quoted: bool = True) -> None:
    lines = ['"DATETIME","value"' if quoted else "DATETIME,Value"]
    lines += [f"h{k},{v:.6f}" for k, v in enumerate(values)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def region_load(region: str) -> np.ndarray:
    """Synthetic regional load [MW] of the mini dataset."""
    hours = np.arange(N_HOURS)
    base = 120.0 if region == "R1" else 40.0
    return base + 20.0 * np.sin(2 * np.pi * hours / 24.0)


def write_mini_dataset(root: Path) -> Path:
    """Write a 3-bus / 2-region NREL-118-format dataset under *root*."""
    root.mkdir(parents=True, exist_ok=True)
    # Buses.csv: BOM + trailing-space header like upstream.
    (root / "Buses.csv").write_text(
        "﻿Number,Name,BusType,Angle,Magnitude ,Voltage-Max (pu),"
        "Voltage-Min (pu),Base Voltage kV,Area,LoadZone\n"
        "1,,AC,,1,1.06,0.94,138,R1,\n"
        "2,,AC,,1,1.06,0.94,138,R1,\n"
        "3,,AC,,1,1.06,0.94,138,R2,\n",
        encoding="utf-8",
    )
    # partfact.csv / Lines.csv / Generators.csv: classic-Mac CR endings.
    (root / "partfact.csv").write_text(
        "\r".join(
            [
                "Bus Name,Region,Load Participation Factor",
                "bus001,R1,0.25",
                "bus002,R1,0.75",
                "bus003,R2,1",
            ]
        ),
        encoding="utf-8",
    )
    (root / "Lines.csv").write_text(
        "\r".join(
            [
                "Line Name,Bus from ,Bus to,Max Flow (MW),Min Flow (MW),"
                "Reactance (p.u.),Resistance (p.u.)",
                "line001,bus001,bus002,600,-600,0.1,0.03",
                "line002,bus002,bus003,80,-60,0.05,0.01",
                "line003,bus001,bus003,600,-600,0.2,0.05",
            ]
        ),
        encoding="utf-8",
    )
    (root / "Generators.csv").write_text(
        "\r".join([_GEN_HEADER, *_GEN_ROWS]), encoding="utf-8"
    )
    for region in ("R1", "R2"):
        _hourly_csv(root / f"Load{region}DA.csv", region_load(region))
    hours = np.arange(N_HOURS)
    # Wind peaks at 44 MW (> 40 MW Max Capacity, like upstream Wind 17).
    _hourly_csv(root / "Wind1DA.csv", 22.0 + 22.0 * np.cos(hours / 5.0))
    _hourly_csv(
        root / "Solar1DA.csv",
        np.clip(30.0 * np.sin(np.pi * ((hours % 24) - 6) / 12.0), 0.0, None),
    )
    _hourly_csv(root / "Hydro 16.csv", np.full(N_HOURS, 8.0), quoted=False)
    hydro_rows = [
        "﻿Generators,System,Hydro 01,Max Capacity,25,,MW,1,,,,=,,,,Hydro",
        "Generators,System,Hydro 01,Max Energy Month,0.6,,GWh,1,,,M1,=,,,,Hydro",
        "Generators,System,Hydro 01,Max Energy Month,0.3,,GWh,1,,,M2,=,,,,Hydro",
        "Generators,System,Hydro 01,Units,1,,-,1,,,,=,,,,Hydro",
    ]
    (root / "118-hydro.csv").write_text("\n".join(hydro_rows) + "\n", "utf-8")
    (root / "Hydro_nondispatchable.csv").write_text(
        "\r".join(
            [
                "Generator,Property,Value,Units,Band,Date From,Date To,"
                "Timeslice,Escalator,Data File,Scenario,Memo,Category",
                "Hydro 16,Fixed Load,0,MW,1,,,,,Hydro 16,,,Hydro",
                "Hydro 36,Fixed Load,4.5,MW,1,,,M1,,,,,Hydro",
                "Hydro 36,Fixed Load,6,MW,1,,,M2,,,,,Hydro",
            ]
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture(name="mini")
def fixture_mini(tmp_path: Path) -> Path:
    """Synthetic NREL-118-format dataset directory."""
    return write_mini_dataset(tmp_path / "cache")


def _read_long(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# Heat rate / fuel mapping (unchanged emission framework)
# ---------------------------------------------------------------------------


def test_btu_per_kwh_conversion_constant():
    """Sanity-check the BTU/kWh → GJ/MWh constant: 0.001055."""

    assert math.isclose(BTU_PER_KWH_TO_GJ_PER_MWH, 1.055e-3, rel_tol=1e-6)


def test_ipcc_factors_coal_natural_gas_diesel():
    """Cross-check against IPCC AR6 Table A.III.2 (rounded to 4 dp)."""

    assert IPCC_AR6_TCO2_PER_GJ["coal"] == pytest.approx(0.0946, abs=1e-6)
    assert IPCC_AR6_TCO2_PER_GJ["natural_gas"] == pytest.approx(0.0561, abs=1e-6)
    assert IPCC_AR6_TCO2_PER_GJ["diesel"] == pytest.approx(0.0741, abs=1e-6)
    for renewable in ("wind", "solar", "hydro", "nuclear", "biomass"):
        assert IPCC_AR6_TCO2_PER_GJ[renewable] == 0.0


def test_unit_type_to_fuel_mapping_covers_nrel118_categories():
    """Every category that appears in NREL-118 must map to a known fuel."""

    cases = {
        "Biomass 01": "biomass",
        "CC NG 02": "natural_gas",
        "CT NG 03": "natural_gas",
        "CT Oil 04": "diesel",
        "Geo 01": "geothermal",
        "Hydro 12": "hydro",
        "ICE NG 05": "natural_gas",
        "Solar 18": "solar",
        "ST Coal 06": "coal",
        "ST NG 07": "natural_gas",
        "ST Other 08": "other",
        "Wind 09": "wind",
    }
    for name, expected_kind in cases.items():
        assert unit_type_to_fuel(name) == expected_kind, name


def test_collapse_piecewise_heat_rate_single_band():
    """ST_Coal_01: no incremental band fires (LP1 == pmin)."""

    hr_gj_per_mwh = collapse_piecewise_heat_rate(
        hr_base_mmbtu_per_hr=108.8,
        hr_inc_bands_btu_per_kwh=[10171.43, 0, 0, 0, 0],
        load_points_mw=[19.84, 0, 0, 0, 0],
        pmin=19.84,
        pmax=20.0,
    )
    assert hr_gj_per_mwh == pytest.approx(108.8 / 20.0 * 1.055, rel=1e-5)


def test_collapse_piecewise_heat_rate_with_band_extension():
    """Real CC_NG_01: HR_Base dominates, small Inc1 contribution."""

    hr = collapse_piecewise_heat_rate(
        hr_base_mmbtu_per_hr=316.78,
        hr_inc_bands_btu_per_kwh=[6489.0, 0, 0, 0, 0],
        load_points_mw=[41.5, 0, 0, 0, 0],
        pmin=41.08,
        pmax=41.5,
    )
    assert hr == pytest.approx(316.78 / 41.5 * 1.055, rel=0.01)


def test_collapse_piecewise_heat_rate_zero_pmax_returns_zero():
    """Defensive guard: pmax <= 0 → 0 (no division-by-zero)."""

    assert (
        collapse_piecewise_heat_rate(0, [0, 0, 0, 0, 0], [0, 0, 0, 0, 0], 0, 0) == 0.0
    )


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def test_read_rows_handles_cr_and_bom(tmp_path: Path):
    """Classic-Mac CR terminators and a UTF-8 BOM are normalised."""
    path = tmp_path / "x.csv"
    path.write_bytes("﻿a,b\r1,2\r\r3,4".encode())
    assert read_rows(path) == [["a", "b"], ["1", "2"], ["3", "4"]]


def test_read_hourly_drops_duplicated_hydro_rows(tmp_path: Path):
    """An 8808-row hydro file loses rows 1416..1439 (Sienna loader rule)."""
    values = np.arange(_data.HYDRO_FILE_ROWS, dtype=float)
    path = tmp_path / "Hydro 99.csv"
    _hourly_csv(path, values, quoted=False)
    out = read_hourly(path)
    assert out.size == 8784
    assert out[1415] == 1415.0
    assert out[1416] == 1440.0


def test_profile_remote_path():
    """Upstream paths of the per-unit profile files."""
    assert _data.profile_remote_path("Wind 07") == "Wind/DA/Wind7DA.csv"
    assert _data.profile_remote_path("Solar 12") == "Solar/DA/Solar12DA.csv"
    assert _data.profile_remote_path("Hydro 16") == "Hydro/Hydro 16.csv"
    assert _data.profile_remote_path("CC NG 01") is None


def test_resolve_accepts_upstream_tree_layout(tmp_path: Path):
    """A checkout layout (``118-Bus/Load/DA/...``) resolves like the cache."""
    nested = tmp_path / "118-Bus" / "Load" / "DA"
    nested.mkdir(parents=True)
    (nested / "LoadR1DA.csv").write_text("x")
    got = _data.resolve(tmp_path, "Load/DA/LoadR1DA.csv")
    assert got == nested / "LoadR1DA.csv"


def test_populate_cache_fetches_core_and_profiles(tmp_path: Path, monkeypatch):
    """Profile files are derived from Generators.csv ``Rating``; no network."""
    src = write_mini_dataset(tmp_path / "src")
    fetched: list[str] = []

    def fake_download(remote: str, local: Path, ref: str = _data.DEFAULT_REF):
        fetched.append(remote)
        assert ref == "abc123"
        local.write_bytes((src / _data.local_name(remote)).read_bytes())

    monkeypatch.setattr(_data, "_download", fake_download)
    cache = tmp_path / "cache"
    for name in ("LoadR3DA.csv",):
        (src / name).write_text("DATETIME,value\n")
    n = populate_cache(cache, ref="abc123")
    assert n == len(fetched)
    assert "Wind/DA/Wind1DA.csv" in fetched
    assert "Hydro/Hydro 16.csv" in fetched
    assert "Load/partfact.csv" in fetched
    # Idempotent: nothing left to fetch.
    assert populate_cache(cache, ref="abc123") == 0


# ---------------------------------------------------------------------------
# Conversion on the mini dataset
# ---------------------------------------------------------------------------


def test_parse_buses_reads_participation_factors(mini: Path):
    """partfact.csv attaches region + factor to each Buses.csv bus."""
    buses = parse_buses(mini / "Buses.csv", mini / "partfact.csv")
    assert [(b.number, b.region, b.load_factor) for b in buses] == [
        (1, "R1", 0.25),
        (2, "R1", 0.75),
        (3, "R2", 1.0),
    ]


def test_load_distributed_by_participation_factor(mini: Path):
    """Bus load = factor × regional load; per-region totals preserved."""
    conv = convert(mini)
    assert conv.n_hours == N_HOURS
    r1, r2 = region_load("R1"), region_load("R2")
    np.testing.assert_allclose(conv.bus_load_mw[1], 0.25 * r1)
    np.testing.assert_allclose(conv.bus_load_mw[2], 0.75 * r1)
    np.testing.assert_allclose(conv.bus_load_mw[3], r2)
    np.testing.assert_allclose(conv.total_load_mw, r1 + r2)


def test_profile_capacity_raised_to_series_peak(mini: Path):
    """A DA series above Max Capacity keeps its MW exactly (cap = peak)."""
    conv = convert(mini)
    assert conv.gen_capacity["Wind 01"] == pytest.approx(44.0)
    assert conv.gen_capacity["Solar 01"] == pytest.approx(30.0)
    np.testing.assert_allclose(conv.gen_avail_mw["Hydro 16"], 8.0)


def test_hydro_budget_monthly_energy_and_cap(mini: Path):
    """Energy-limited hydro: capacity from 118-hydro.csv, January budget kept."""
    conv = convert(mini)
    assert conv.gen_capacity["Hydro 01"] == pytest.approx(25.0)
    avail = conv.gen_avail_mw["Hydro 01"]
    assert avail.max() <= 25.0 + 1e-9
    # 48 h are all in January; full-year budget shaped over 744 h, so the
    # slice is load-proportional but never above the cap.
    budget = HydroBudget(capacity=25.0, month_energy_gwh={1: 0.6})
    year = hydro_budget_availability(budget, 25.0, np.ones(8784))
    assert year[:744].sum() == pytest.approx(600.0)
    assert year[744:].sum() == pytest.approx(0.0)


def test_shape_energy_water_filling():
    """Water-filling keeps the energy and respects the cap."""
    weight = np.array([1.0, 2.0, 10.0, 1.0])
    out = _shape_energy(20.0, 8.0, weight)
    assert out.sum() == pytest.approx(20.0)
    assert out.max() == pytest.approx(8.0)
    assert out[1] == pytest.approx(2 * out[0])
    np.testing.assert_allclose(_shape_energy(100.0, 8.0, weight), 8.0)


def test_monthly_fixed_mw_hydro(mini: Path):
    """Hydro 36 gets its January MW from Hydro_nondispatchable.csv."""
    conv = convert(mini)
    np.testing.assert_allclose(conv.gen_avail_mw["Hydro 36"], 4.5)


def test_build_case_network_layout(mini: Path, tmp_path: Path):
    """Full network JSON: buses, lines, per-bus demands, profiles, fuels."""
    conv = convert(mini)
    out = tmp_path / "case" / "mini.json"
    planning, summary = build_case(conv, out)
    system = planning["system"]
    assert [b["uid"] for b in system["bus_array"]] == [1, 2, 3]
    # Bus 69 (Sienna reference) absent → lowest bus is the reference.
    assert system["bus_array"][0]["reference_theta"] == 0
    line2 = system["line_array"][1]
    assert (line2["bus_a"], line2["bus_b"]) == (2, 3)
    assert (line2["tmax_ab"], line2["tmax_ba"]) == (80.0, 60.0)
    assert line2["reactance"] == pytest.approx(0.05)
    assert line2["voltage"] == pytest.approx(10.0)
    assert {d["bus"] for d in system["demand_array"]} == {1, 2, 3}
    assert all(d["lmax"] == "lmax" for d in system["demand_array"])
    gens = {g["name"]: g for g in system["generator_array"]}
    assert gens["CC NG 01"]["bus"] == 1 and gens["Wind 01"]["bus"] == 2
    assert gens["CC NG 01"]["type"] == "thermal"
    # SRMC = VO&M + HR × Sienna NG price.
    hr = gens["CC NG 01"]["heat_rate"]
    assert gens["CC NG 01"]["gcost"] == pytest.approx(
        1.5 + hr * SIENNA_FUEL_PRICE_USD_PER_MMBTU["natural_gas"] / 1.055, rel=1e-5
    )
    assert gens["CC NG 01"]["fuel"] in {f["uid"] for f in system["fuel_array"]}
    assert "commitment_array" not in system
    # Profiled: wind, solar, Hydro 01, Hydro 16, Hydro 36.
    owners = {p["owner"] for p in system["capacity_profile_array"]}
    assert owners == {
        gens[n]["uid"]
        for n in ("Wind 01", "Solar 01", "Hydro 01", "Hydro 16", "Hydro 36")
    }
    opts = planning["options"]
    assert opts["input_directory"] == "mini"
    assert opts["model_options"]["use_single_bus"] is False
    assert opts["model_options"]["use_kirchhoff"] is True
    assert opts["model_options"]["demand_fail_cost"] >= 500.0
    # Default time grid: 1-h blocks, daily stages, one phase per stage.
    sim = planning["simulation"]
    assert len(sim["block_array"]) == N_HOURS
    assert len(sim["stage_array"]) == 2
    assert len(sim["phase_array"]) == 2
    assert summary["stages"] == 2
    # Emission infrastructure kept.
    assert system["emission_array"][0]["name"] == "co2"
    assert system["emission_zone_array"][0]["emissions"][0]["emission"] == 1


def test_build_case_parquet_schedules(mini: Path, tmp_path: Path):
    """lmax.parquet reproduces bus loads; profile factors are in [0, 1]."""
    conv = convert(mini)
    out = tmp_path / "mini.json"
    build_case(conv, out, CaseOptions(block_hours=2, stage_hours=24))
    lmax = _read_long(tmp_path / "mini" / "Demand" / "lmax.parquet")
    assert set(lmax.columns) >= {"stage", "block", "uid", "value"}
    assert lmax["block"].max() == N_HOURS // 2
    assert lmax["stage"].max() == 2
    # Energy preserved by the 2-h block averaging.
    bus1 = lmax[lmax["uid"] == 1]["value"].to_numpy()
    assert bus1.sum() * 2.0 == pytest.approx(conv.bus_load_mw[1].sum())
    prof = _read_long(tmp_path / "mini" / "GeneratorProfile" / "profile.parquet")
    assert "scenario" in prof.columns
    assert prof["value"].between(0.0, 1.0).all()


def test_build_case_commitment_and_single_bus(mini: Path, tmp_path: Path):
    """--commitment relaxed emits ramps/min-up/start cost; chronological stages."""
    conv = convert(mini)
    planning, _ = build_case(
        conv,
        tmp_path / "uc.json",
        CaseOptions(commitment="relaxed", single_bus=True, stage_hours=None),
    )
    ucs = {u["name"]: u for u in planning["system"]["commitment_array"]}
    cc = ucs["CC NG 01_uc"]
    assert cc["pmin"] == pytest.approx(40.0)
    assert cc["ramp_up"] == pytest.approx(120.0)  # 2 MW/min × 60
    assert cc["min_up_time"] == pytest.approx(8.0)
    assert cc["startup_cost"] == pytest.approx(3000.0)
    assert cc["relax"] is True
    stages = planning["simulation"]["stage_array"]
    assert len(stages) == 1 and stages[0]["chronological"] is True
    assert "phase_array" not in planning["simulation"]
    mo = planning["options"]["model_options"]
    assert mo["use_single_bus"] is True and mo["use_kirchhoff"] is False


def test_renewables_share_rescales_variable_generation(mini: Path, tmp_path: Path):
    """share=S sizes wind+solar to S × load energy; share=0 drops them."""
    conv = convert(mini, renewables_share=0.2)
    vg = conv.gen_avail_mw["Wind 01"].sum() + conv.gen_avail_mw["Solar 01"].sum()
    assert vg == pytest.approx(0.2 * conv.total_load_mw.sum())
    none = convert(mini, renewables_share=0.0)
    planning, _ = build_case(none, tmp_path / "none.json")
    names = {g["name"] for g in planning["system"]["generator_array"]}
    assert "Wind 01" not in names and "Solar 01" not in names
    assert "CC NG 01" in names


def test_hour_window_and_week(mini: Path):
    """``hours`` slices the series; out-of-range windows are rejected."""
    conv = convert(mini, (24, 48))
    np.testing.assert_allclose(
        conv.total_load_mw, (region_load("R1") + region_load("R2"))[24:]
    )
    with pytest.raises(ValueError):
        convert(mini, (0, N_HOURS + 1))
    with pytest.raises(ValueError):
        convert(mini, week=2)  # 168..336 beyond the 48-h fixture


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_case_state_and_replays(mini: Path, tmp_path: Path):
    """CLI end-to-end on the fixture + ``--from-state`` replay."""
    out = tmp_path / "run" / "nrel.json"
    rc = main(
        [
            "--cache-dir",
            str(mini),
            "--no-download",
            "--hours",
            "0+24",
            "--block-hours",
            "2",
            "--stage-hours",
            "12",
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    planning = json.loads(out.read_text())
    assert len(planning["simulation"]["block_array"]) == 12
    assert len(planning["simulation"]["stage_array"]) == 2
    assert planning["options"]["input_directory"] == "nrel"
    state = out.parent / "nrel" / "nrel118_to_gtopt_state.json"
    assert state.is_file()
    assert (out.parent / "nrel" / "README.md").is_file()
    payload = json.loads(state.read_text())
    # Paths are stored relative to the state directory.
    assert not os.path.isabs(payload["args"]["cache_dir"])
    assert payload["extra"]["summary"]["buses"] == 3

    out.unlink()
    assert main(["--from-state", str(state), "--block-hours", "1"]) == 0
    replay = json.loads(out.read_text())
    assert len(replay["simulation"]["block_array"]) == 24


def test_cli_missing_cache_without_download(tmp_path: Path):
    """An empty cache with ``--no-download`` exits with code 2."""
    rc = main(["--cache-dir", str(tmp_path / "empty"), "--no-download"])
    assert rc == 2


def test_cli_rejects_bad_hour_range(mini: Path, tmp_path: Path):
    """A malformed --hours is a conversion error (exit 1)."""
    rc = main(
        [
            "--cache-dir",
            str(mini),
            "--no-download",
            "--hours",
            "40:10",
            "-o",
            str(tmp_path / "x.json"),
        ]
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# Integration: real NREL-118 data (cache / network) + gtopt binary
# ---------------------------------------------------------------------------

REAL_CACHE = _data.DEFAULT_CACHE_DIR


def _gtopt_binary() -> str | None:
    """Locate the gtopt executable for end-to-end smoke tests."""

    env = os.environ.get("GTOPT_BIN")
    if env and Path(env).is_file():
        return env
    for candidate in (
        Path.cwd() / "build" / "standalone" / "gtopt",
        Path(__file__).resolve().parents[3] / "build" / "standalone" / "gtopt",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("gtopt")


def _real_cache_or_skip() -> Path:
    try:
        populate_cache(REAL_CACHE)
    except OSError as exc:  # pragma: no cover - network failure path
        pytest.skip(f"NREL-118 cache not available: {exc}")
    if _data.missing_files(REAL_CACHE):
        pytest.skip("NREL-118 cache incomplete")
    return REAL_CACHE


def _sum_values(out_dir: Path, cls: str, field: str) -> float:
    """Sum a gtopt output field (Parquet file/dir or per-cell CSV)."""
    total = 0.0
    for path in (out_dir / cls).glob(f"{field}*"):
        is_parquet = path.is_dir() or path.suffix == ".parquet"
        frame = pd.read_parquet(path) if is_parquet else pd.read_csv(path)
        cols = (
            ["value"]
            if "value" in frame.columns
            else [c for c in frame.columns if str(c).startswith("uid:")]
        )
        total += float(frame[cols].to_numpy().sum())
    return total


@pytest.mark.integration
def test_nrel118_real_week_converts_full_network(tmp_path: Path):
    """Real data: one week → 118 buses / 186 lines / 91 demands."""
    cache = _real_cache_or_skip()
    conv = convert(cache, (168, 336))
    planning, summary = build_case(conv, tmp_path / "week.json")
    assert summary["buses"] == 118
    assert summary["lines"] == 186
    assert summary["demands"] == 91
    assert summary["generators"] == 327
    assert summary["stages"] == 7
    assert planning["system"]["bus_array"][68]["reference_theta"] == 0


@pytest.mark.integration
def test_nrel118_week_co2_reduction_in_expected_window(tmp_path: Path):
    """Solve the week at 0 % and 33 % wind+solar; the CO2 reduction must
    land in the [15 %, 50 %] window around Peña et al. 2018's annual
    29-34 % reference (wider for a 1-week slice)."""

    binary = _gtopt_binary()
    if binary is None:
        pytest.skip("gtopt binary not found — set GTOPT_BIN or build first")
    cache = _real_cache_or_skip()

    totals: dict[str, float] = {}
    for name, share in (("baseline", 0.0), ("ren33", 0.33)):
        conv = convert(cache, (168, 336), renewables_share=share)
        jpath = tmp_path / f"{name}.json"
        planning, _ = build_case(conv, jpath)
        jpath.write_text(json.dumps(planning), encoding="utf-8")
        sol_dir = tmp_path / f"sol_{name}"
        result = subprocess.run(
            [binary, "-s", str(jpath), "-d", str(sol_dir)],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert _sum_values(sol_dir, "Demand", "fail_sol") < 1e-3
        totals[name] = _sum_values(sol_dir, "EmissionSource", "emissions_sol")

    assert totals["baseline"] > 0.0
    pct = 100.0 * (totals["baseline"] - totals["ren33"]) / totals["baseline"]
    assert 15.0 <= pct <= 50.0, f"CO2 reduction {pct:.1f}% outside [15, 50]"
