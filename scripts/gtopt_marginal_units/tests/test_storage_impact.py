# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for ``gtopt-marginal-units storage-impact`` (CN0157-style
emission impact of storage) and for the uid-keyed emission zone fix."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from gtopt_marginal_units import storage_impact as si
from gtopt_marginal_units._gtopt_reader import topology_from_planning
from gtopt_marginal_units.main import cli


def _planning(with_converter: bool = True) -> dict:
    """One battery on bus b3, 4 blocks (block 4 lasts 2 h), 2 scenarios."""
    system: dict = {
        "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 3, "name": "b3"}],
        "battery_array": [{"uid": 7, "name": "bat"}],
        "generator_array": [],
        "demand_array": [],
    }
    if with_converter:
        system["generator_array"] = [{"uid": 40, "name": "bat_gen", "bus": "b3"}]
        system["demand_array"] = [{"uid": 30, "name": "bat_dem", "bus": "b3"}]
        system["converter_array"] = [
            {
                "uid": 1,
                "name": "conv",
                "battery": "bat",
                "generator": "bat_gen",
                "demand": "bat_dem",
            }
        ]
    else:
        system["battery_array"][0]["bus"] = "b3"
    return {
        "simulation": {
            "block_array": [
                {"uid": 1, "duration": 1},
                {"uid": 2, "duration": 1},
                {"uid": 3, "duration": 1},
                {"uid": 4, "duration": 2},
            ],
            "scenario_array": [
                {"uid": 1, "probability_factor": 3},
                {"uid": 2, "probability_factor": 1},
            ],
        },
        "system": system,
    }


def _write(path: Path, rows: list[tuple[int, int, int, int, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=["scenario", "stage", "block", "uid", "value"])
    df.to_csv(path, index=False)


def _output(tmp: Path, charge: dict, discharge: dict, converter: bool = True) -> Path:
    """Sparse gtopt-style CSV outputs: {(scenario, block): MW}."""
    gen_stem, dem_stem = (
        ("Generator/generation_sol", "Demand/load_sol")
        if converter
        else ("Battery/fout_sol", "Battery/finp_sol")
    )
    gen_uid, dem_uid = (40, 30) if converter else (7, 7)
    _write(
        tmp / f"{gen_stem}_s0_p0.csv",
        [(s, 1, b, gen_uid, mw) for (s, b), mw in discharge.items()],
    )
    _write(
        tmp / f"{dem_stem}_s0_p0.csv",
        [(s, 1, b, dem_uid, mw) for (s, b), mw in charge.items()],
    )
    return tmp


def _mer(values: dict[int, float]) -> pd.DataFrame:
    """Same MER on bus 3 for both scenarios: {block: t/MWh}."""
    rows = [
        {"scenario": s, "stage": 1, "block": b, "bus_uid": 3, "mer_t_per_mwh": v}
        for s in (1, 2)
        for b, v in values.items()
    ]
    return pd.DataFrame(rows)


MER = {1: 0.0, 2: 0.2, 3: 0.9, 4: 0.6}


@pytest.mark.parametrize("converter", [True, False])
def test_storage_units_bus_resolution(converter):
    units = si.storage_units(_planning(with_converter=converter))
    assert len(units) == 1
    unit = units[0]
    assert (unit.uid, unit.name, unit.bus_uid) == (7, "bat", 3)
    if converter:
        assert (unit.gen_uid, unit.dem_uid) == (40, 30)
    else:
        assert unit.gen_uid is None and unit.dem_uid is None


@pytest.mark.parametrize("converter", [True, False])
def test_dispatch_energy_uses_block_duration(tmp_path, converter):
    planning = _planning(with_converter=converter)
    out = _output(
        tmp_path,
        charge={(1, 1): 10.0, (1, 2): 5.0},
        discharge={(1, 3): 8.0, (1, 4): 2.0},
        converter=converter,
    )
    units = si.storage_units(planning)
    disp = si.storage_dispatch(out, planning, units).set_index(["scenario", "block"])
    assert disp.loc[(1, 1), "net_mwh"] == pytest.approx(-10.0)
    assert disp.loc[(1, 3), "net_mwh"] == pytest.approx(8.0)
    # Block 4 lasts 2 h: 2 MW -> 4 MWh.
    assert disp.loc[(1, 4), "discharge_mwh"] == pytest.approx(4.0)


def test_cn0157_pe_be_er(tmp_path):
    """Hand-computed PE, BE and ER with scenario probabilities 3:1."""
    planning = _planning()
    units = si.storage_units(planning)
    mer = _mer(MER)
    # Project: charges 10 MWh at MER 0 (block 1), discharges 8 MWh at 0.9.
    proj_dir = _output(
        tmp_path / "proj",
        charge={(1, 1): 10.0, (2, 1): 10.0},
        discharge={(1, 3): 8.0, (2, 3): 8.0},
    )
    # Baseline: charges 10 MWh at 0.2 (block 2), discharges 8 MWh at 0.2... ->
    # here at block 4 (2 h x 4 MW = 8 MWh, MER 0.6).
    base_dir = _output(
        tmp_path / "base",
        charge={(1, 2): 10.0, (2, 2): 10.0},
        discharge={(1, 4): 4.0, (2, 4): 4.0},
    )
    proj = si.emission_impact(
        si.storage_dispatch(proj_dir, planning, units), mer, units
    )
    base = si.emission_impact(
        si.storage_dispatch(base_dir, planning, units), mer, units
    )
    summary = si.summarise(proj, base, units, si.scenario_weights(planning)).iloc[0]

    pe = -(8.0 * 0.9 - 10.0 * 0.0)  # -7.2 t: reduction
    be = -(8.0 * 0.6 - 10.0 * 0.2)  # -2.8 t
    assert summary["PE_t"] == pytest.approx(pe)
    assert summary["BE_t"] == pytest.approx(be)
    assert summary["ER_t"] == pytest.approx(be - pe)  # 4.4 t more reduction
    assert summary["discharged_mwh"] == pytest.approx(8.0)
    assert summary["charged_mwh"] == pytest.approx(10.0)
    assert summary["mer_discharge_t_per_mwh"] == pytest.approx(0.9)
    assert summary["mer_charge_t_per_mwh"] == pytest.approx(0.0)


def test_round_trip_losses_increase_emissions_under_flat_mer(tmp_path):
    """With a flat MER, cycling only adds the losses: PE = losses x MER > 0."""
    planning = _planning()
    units = si.storage_units(planning)
    out = _output(tmp_path, charge={(1, 1): 10.0}, discharge={(1, 3): 9.0})
    flat = _mer(dict.fromkeys(MER, 0.5))
    impact = si.emission_impact(si.storage_dispatch(out, planning, units), flat, units)
    summary = si.summarise(impact, None, units, {1: 1.0, 2: 0.0}).iloc[0]
    assert summary["PE_t"] == pytest.approx(0.5)
    assert "ER_t" not in summary.index


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    planning = _planning()
    out = _output(tmp_path / "proj", charge={(1, 1): 10.0}, discharge={(1, 3): 8.0})
    (out / "planning.json").write_text(json.dumps(planning), encoding="utf-8")
    monkeypatch.setattr(si, "read_mer", lambda _path: _mer(MER))
    summary_json = tmp_path / "summary.json"
    table_csv = tmp_path / "table.csv"
    rc = cli(
        [
            "storage-impact",
            "--project-output",
            str(out),
            "--mer",
            str(tmp_path / "unused"),
            "--json",
            str(summary_json),
            "--csv",
            str(table_csv),
        ]
    )
    assert rc == 0
    rows = json.loads(summary_json.read_text(encoding="utf-8"))
    assert rows[0]["storage"] == "bat"
    # Scenario 1 has weight 3/4.
    assert rows[0]["PE_t"] == pytest.approx(0.75 * -(8.0 * 0.9))
    assert len(pd.read_csv(table_csv)) == 2
    assert "PE < 0" in capsys.readouterr().out


def test_emission_zone_referenced_by_uid():
    """``emissions[].emission`` and ``EmissionSource.emission`` given as
    integer uids must still match (was silently weight 0)."""
    planning = {
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [
                {"uid": 1, "name": "g1", "bus": "b1", "pmax": 10, "gcost": 1}
            ],
            "emission_array": [{"uid": 1, "name": "co2"}],
            "emission_zone_array": [
                {"uid": 1, "name": "z", "emissions": [{"emission": 1, "weight": 1.0}]}
            ],
            "emission_source_array": [
                {"uid": 1, "generator": 1, "zone": 1, "emission": 1, "rate": 0.8}
            ],
        }
    }
    topo = topology_from_planning(planning)
    gen = next(g for g in topo.generators if g.uid == 1)
    assert gen.emission_rate == pytest.approx(0.8)
