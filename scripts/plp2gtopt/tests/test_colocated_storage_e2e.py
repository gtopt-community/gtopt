"""End-to-end: a co-located plant is not curtailed by its battery rating.

PLP co-located storage (``plpcenbat.dat`` injection central, ``plpess.dat``
DCMod=1 CenCarga) lets the plant inject directly to its grid bus AND charge
its battery.  plp2gtopt expresses it as a standalone battery on its bus
plus the user constraint ``charge <= plant generation``.  The former
``source_generator`` wiring put the plant behind the battery (the charge
demand was its only outlet), capping the plant at ``pmax_charge``.

Fixture: 100 MW zero-cost plant, battery rated 10 MW, 50 $/MWh backup,
load 60 MW (block 1) and 120 MW (block 2).
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ..battery_parser import BatteryParser
from ..battery_writer import BatteryWriter

_PLPCENBAT = (
    "# plpcenbat.dat\n"
    "# Numero de baterias, maximo de inyecciones\n"
    " 1     1\n"
    "# Baterias\n"
    " 1     BESS1\n"
    " # Numero de centrales que inyectan\n"
    " 1\n"
    " # Central que inyecta, FPC\n"
    " SOLAR1     0.95\n"
    "# Barra, FPD, EMin, EMax\n"
    " 1     0.95     0.0     40.0\n"
)


class _Centrals:  # pylint: disable=too-few-public-methods
    """plpcnfce.dat stand-in: the BAT central and its co-located plant."""

    centrals = [
        {"name": "BESS1", "number": 1, "type": "bateria", "pmax": 10.0, "bus": 1},
        {"name": "SOLAR1", "number": 2, "type": "termica", "pmax": 100.0, "bus": 1},
    ]


def _writer(tmp_path: Path) -> BatteryWriter:
    f = tmp_path / "plpcenbat.dat"
    f.write_text(_PLPCENBAT)
    bp = BatteryParser(f)
    bp.parse()
    return BatteryWriter(
        battery_parser=bp,
        central_parser=_Centrals(),  # type: ignore[arg-type]
    )


def _case(batteries: list[dict[str, Any]], ucs: list[dict[str, Any]]) -> dict:
    # No self-discharge: keeps the hand-computed MW exact (plpcenbat.dat
    # has no loss field, so the writer leaves gtopt's default in place).
    batteries = [{**b, "annual_standing_loss": 0.0} for b in batteries]
    return {
        "options": {
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {"demand_fail_cost": 1000},
        },
        "simulation": {
            "block_array": [
                {"uid": 1, "duration": 1},
                {"uid": 2, "duration": 1},
            ],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2}],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": {
            "name": "colocated",
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [
                {
                    "uid": 2,
                    "name": "SOLAR1",
                    "bus": 1,
                    "pmax": 100.0,
                    "gcost": 0.0,
                    "capacity": 100.0,
                },
                {
                    "uid": 3,
                    "name": "BACKUP",
                    "bus": 1,
                    "pmax": 500.0,
                    "gcost": 50.0,
                    "capacity": 500.0,
                },
            ],
            "demand_array": [
                {"uid": 1, "name": "d1", "bus": 1, "lmax": [[60.0, 120.0]]}
            ],
            "battery_array": batteries,
            "user_constraint_array": [{"uid": i + 1, **uc} for i, uc in enumerate(ucs)],
        },
    }


def _solve(gtopt_bin: str, out: Path, case: dict) -> pd.DataFrame:
    out.mkdir(parents=True, exist_ok=True)
    (out / "case.json").write_text(json.dumps(case))
    res = subprocess.run(
        ["bash", "-c", f'ulimit -c unlimited; exec "{gtopt_bin}" case.json'],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    gen = pd.read_csv(out / "output" / "Generator" / "generation_sol_s0_p0.csv")
    return gen.pivot_table(index="block", columns="uid", values="value").reindex([1, 2])


def _gen(gen: pd.DataFrame, uid: int) -> list[float]:
    # Zero columns may be omitted by the writer.
    if uid not in gen:
        return [0.0] * len(gen)
    return [float(v) for v in gen[uid].fillna(0.0)]


def test_writer_emits_standalone_battery_and_coupling(tmp_path: Path) -> None:
    """No source_generator; one ``charge <= plant`` user constraint."""
    writer = _writer(tmp_path)
    res = writer.process([{"uid": 2, "name": "SOLAR1", "bus": 1}], [], tmp_path / "out")
    (bat,) = res["battery_array"]
    assert "source_generator" not in bat
    assert bat["bus"] == 1
    assert bat["pmax_charge"] == pytest.approx(10.0)
    (uc,) = res["charge_coupling_constraints"]
    assert uc["expression"] == (
        "demand('BESS1_dem').load - generator('SOLAR1').generation <= 0"
    )


@pytest.mark.integration
def test_e2e_plant_not_capped_by_battery_rating(tmp_path: Path, gtopt_bin: str) -> None:
    """The 100 MW plant serves the load directly; backup only covers 120 MW."""
    writer = _writer(tmp_path)
    res = writer.process(
        [{"uid": 2, "name": "SOLAR1", "bus": 1}], [], tmp_path / "conv"
    )
    gen = _solve(
        gtopt_bin,
        tmp_path / "new",
        _case(res["battery_array"], res["charge_coupling_constraints"]),
    )
    solar = _gen(gen, 2)
    backup = _gen(gen, 3)
    discharge = _gen(gen, 4)  # synthetic BESS1_gen
    # Block 1: the plant serves the 60 MW load directly and charges the
    # battery at its 10 MW rating (70 MW, not capped at 10 MW).
    assert solar[0] == pytest.approx(70.0, abs=1e-6)
    assert backup[0] == pytest.approx(0.0, abs=1e-6)
    # Block 2: plant at its full 100 MW; the battery returns
    # 10 · 0.95 · 0.95 = 9.025 MW; backup covers the rest.
    assert solar[1] == pytest.approx(100.0, abs=1e-6)
    assert discharge[1] == pytest.approx(9.025, abs=1e-6)
    assert backup[1] == pytest.approx(120.0 - 100.0 - 9.025, abs=1e-6)


@pytest.mark.integration
def test_e2e_source_generator_wiring_caps_plant(tmp_path: Path, gtopt_bin: str) -> None:
    """Contrast: the former source_generator wiring curtails the plant."""
    writer = _writer(tmp_path)
    res = writer.process(
        [{"uid": 2, "name": "SOLAR1", "bus": 1}], [], tmp_path / "conv"
    )
    (bat,) = res["battery_array"]
    old = _case([{**bat, "source_generator": "SOLAR1"}], [])
    gen = _solve(gtopt_bin, tmp_path / "old", old)
    solar = _gen(gen, 2)
    backup = _gen(gen, 3)
    # Plant output is bounded by the 10 MW charge rating in every block,
    # so the backup unit carries the load the plant could have served.
    assert max(solar) <= 10.0 + 1e-6
    assert backup[0] == pytest.approx(60.0, abs=1e-6)
    assert backup[1] >= 100.0
