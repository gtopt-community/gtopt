"""plpmance.dat rows of battery centrals → battery pmax schedules.

PLP bases set battery entry dates (and derates) as plpmance.dat rows of
the battery central ``<battery>`` (discharge, PotMax) and of its charge
phantom ``<battery>_LOAD`` (charge, −PotMin).  They must reach the
battery's own ``pmax_discharge`` / ``pmax_charge`` schedules; before the
fix they were written to ``Generator/pmax`` under the battery uid, where
no generator reads them, and every battery was in service from day one.
"""

import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from ..battery_parser import BatteryParser
from ..block_parser import BlockParser
from ..central_parser import CentralParser
from ..mance_parser import ManceParser
from ..mance_writer import ManceWriter
from ..battery_writer import BatteryWriter
from ..plp2gtopt import convert_plp_case

_CASE = Path(__file__).parent.parent.parent / "cases" / "plp_min_battery_entry"


def _parsers() -> dict:
    out = {}
    for key, cls, fname in (
        ("battery", BatteryParser, "plpcenbat.dat"),
        ("central", CentralParser, "plpcnfce.dat"),
        ("mance", ManceParser, "plpmance.dat"),
        ("block", BlockParser, "plpblo.dat"),
    ):
        p = cls(_CASE / fname)
        p.parse()
        out[key] = p
    return out


def _sched(path: Path) -> dict[int, float]:
    df = pd.read_parquet(path)
    return {int(b): float(v) for b, v in zip(df["block"], df["value"], strict=True)}


def test_mance_rows_map_to_battery_pmax_schedules(tmp_path):
    """Zero PotMax / PotMin rows in stage 1 → 0 MW there, nominal after."""
    p = _parsers()
    writer = BatteryWriter(
        battery_parser=p["battery"],
        central_parser=p["central"],
        block_parser=p["block"],
        mance_parser=p["mance"],
    )
    res = writer.process([], [], tmp_path)
    (bat,) = res["battery_array"]
    assert bat["pmax_discharge"] == "pmax_discharge"
    assert bat["pmax_charge"] == "pmax_charge"
    dis = _sched(tmp_path / "Battery" / "pmax_discharge.parquet")
    chg = _sched(tmp_path / "Battery" / "pmax_charge.parquet")
    # plpcnfce.dat: BESS1 PotMax 50, BESS1_LOAD PotMin −50.
    assert dis == {1: 0.0, 2: 0.0, 3: 50.0, 4: 50.0, 5: 50.0, 6: 50.0}
    assert chg == {1: 0.0, 2: 0.0, 3: 50.0, 4: 50.0, 5: 50.0, 6: 50.0}
    df = pd.read_parquet(tmp_path / "Battery" / "pmax_discharge.parquet")
    assert set(df["uid"]) == {4}
    assert list(df["stage"]) == [1, 1, 2, 2, 3, 3]


def test_no_mance_parser_keeps_scalars(tmp_path):
    """Without plpmance.dat the battery keeps its scalar ratings."""
    p = _parsers()
    writer = BatteryWriter(
        battery_parser=p["battery"],
        central_parser=p["central"],
        block_parser=p["block"],
    )
    (bat,) = writer.process([], [], tmp_path)["battery_array"]
    assert bat["pmax_discharge"] == pytest.approx(50.0)
    assert bat["pmax_charge"] == pytest.approx(50.0)
    assert not (tmp_path / "Battery").exists()


def _mance(tmp_path: Path, rows: dict[str, list[tuple[int, float, float]]]):
    lines = [f" {len(rows)}"]
    for name, blocks in rows.items():
        lines += [f"'{name}'", f"   {len(blocks)}   01"]
        lines += [f"  01  {b:03d}  1  {lo:.2f}  {hi:.2f}" for b, lo, hi in blocks]
    f = tmp_path / "plpmance.dat"
    f.write_text("\n".join(lines) + "\n")
    m = ManceParser(f)
    m.parse()
    return m


def test_derate_and_missing_load_central(tmp_path):
    """A derate keeps its value; without ``_LOAD`` the charge is untouched."""
    p = _parsers()
    mance = _mance(tmp_path, {"BESS1": [(3, 0.0, 20.0)]})
    writer = BatteryWriter(
        battery_parser=p["battery"],
        central_parser=p["central"],
        block_parser=p["block"],
        mance_parser=mance,
    )
    (bat,) = writer.process([], [], tmp_path / "o")["battery_array"]
    assert bat["pmax_discharge"] == "pmax_discharge"
    assert bat["pmax_charge"] == pytest.approx(50.0)
    dis = _sched(tmp_path / "o" / "Battery" / "pmax_discharge.parquet")
    assert dis == {1: 50.0, 2: 50.0, 3: 20.0, 4: 50.0, 5: 50.0, 6: 50.0}
    assert not (tmp_path / "o" / "Battery" / "pmax_charge.parquet").exists()


def test_charge_only_rows_without_block_parser(tmp_path):
    """Only ``_LOAD`` rows, no block list: the listed blocks are the index."""
    p = _parsers()
    mance = _mance(tmp_path, {"BESS1_LOAD": [(2, -30.0, 0.0), (5, 0.0, 0.0)]})
    writer = BatteryWriter(
        battery_parser=p["battery"],
        central_parser=p["central"],
        mance_parser=mance,
    )
    (bat,) = writer.process([], [], tmp_path / "o")["battery_array"]
    assert bat["pmax_charge"] == "pmax_charge"
    assert bat["pmax_discharge"] == pytest.approx(50.0)
    df = pd.read_parquet(tmp_path / "o" / "Battery" / "pmax_charge.parquet")
    assert "stage" not in df.columns
    assert _sched(tmp_path / "o" / "Battery" / "pmax_charge.parquet") == {
        2: 30.0,
        5: 0.0,
    }


def test_load_central_absent_fills_nominal(tmp_path):
    """No ``<battery>_LOAD`` central: unlisted blocks keep ``pmax_charge``."""
    p = _parsers()
    p["central"].centrals[:] = [
        c for c in p["central"].centrals if c["name"] != "BESS1_LOAD"
    ]
    mance = _mance(tmp_path, {"BESS1_LOAD": [(1, 0.0, 0.0)]})
    writer = BatteryWriter(
        battery_parser=p["battery"],
        central_parser=p["central"],
        block_parser=p["block"],
        mance_parser=mance,
    )
    writer.process([], [], tmp_path / "o")
    chg = _sched(tmp_path / "o" / "Battery" / "pmax_charge.parquet")
    assert chg == {1: 0.0, 2: 50.0, 3: 50.0, 4: 50.0, 5: 50.0, 6: 50.0}


def test_dc_maintenance_rows_override_mance(tmp_path):
    """A plpmaness.dat DCMax row wins on its block; mance fills the rest."""
    p = _parsers()
    writer = BatteryWriter(
        battery_parser=p["battery"],
        central_parser=p["central"],
        block_parser=p["block"],
        mance_parser=p["mance"],
    )
    entries = writer._all_entries()  # pylint: disable=protected-access
    entries[0]["man_data"] = {"dcmax": [10.0], "block_index": [1]}
    writer._write_power_schedules(entries, tmp_path)  # pylint: disable=protected-access
    dis = _sched(tmp_path / "Battery" / "pmax_discharge.parquet")
    assert dis == {1: 10.0, 2: 0.0, 3: 50.0, 4: 50.0, 5: 50.0, 6: 50.0}
    assert entries[0]["mance_discharge"] and entries[0]["mance_charge"]


def test_mance_writer_drops_battery_centrals(tmp_path):
    """Generator/pmax no longer carries the battery central's rows."""
    p = _parsers()
    writer = ManceWriter(p["mance"], p["central"], p["block"], {})
    cols = writer.to_parquet(tmp_path)
    assert not cols["pmax"]
    assert not (tmp_path / "pmax.parquet").exists()
    # Without a central parser nothing is filtered (no type information).
    items = p["mance"].mances
    assert ManceWriter(p["mance"])._without_battery_centrals(items) == items  # pylint: disable=protected-access


def test_conversion_writes_battery_schedules(tmp_path):
    """Full conversion: the battery references its own schedule files."""
    out = tmp_path / "case"
    convert_plp_case(
        {
            "input_dir": _CASE,
            "output_dir": out,
            "output_file": out / "case.json",
            "hydrologies": "1",
        }
    )
    data = json.loads((out / "case.json").read_text(encoding="utf-8"))
    (bat,) = data["system"]["battery_array"]
    assert bat["pmax_discharge"] == "pmax_discharge"
    assert bat["pmax_charge"] == "pmax_charge"
    assert (out / "Battery" / "pmax_discharge.parquet").exists()
    assert not (out / "Generator" / "pmax.parquet").exists()


@pytest.mark.integration
def test_e2e_battery_enters_in_stage_2(tmp_path, gtopt_bin):
    """Solved: no battery in stage 1, peak shaving from stage 2 on.

    Blocks: 50 MW (off-peak) / 150 MW (peak) per stage; Thermal1 100 MW
    at 10 $/MWh, Thermal2 100 MW at 100 $/MWh, BESS1 50 MW / 200 MWh at
    efficiency 1.  Stage 1: Thermal2 covers the 50 MW above Thermal1 at
    peak.  Stages 2-3: the battery charges 50 MW off-peak from Thermal1
    and discharges 50 MW at peak, so Thermal2 is ~0 (the default
    self-discharge loses a few kWh).
    """
    out = tmp_path / "case"
    convert_plp_case(
        {
            "input_dir": _CASE,
            "output_dir": out,
            "output_file": out / "case.json",
            "hydrologies": "1",
            "method": "monolithic",
        }
    )
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'ulimit -c unlimited; exec "{gtopt_bin}" case.json'
            " --set output_format=csv --set output_compression=uncompressed",
        ],
        cwd=out,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    files = sorted((out / "results" / "Generator").glob("generation_sol*.csv"))
    gen = pd.concat([pd.read_csv(f) for f in files])
    by = gen.pivot_table(index="block", columns="uid", values="value").fillna(0.0)
    by = by.reindex(range(1, 7), fill_value=0.0)
    thermal2 = [float(v) for v in by.get(2, pd.Series(0.0, index=by.index))]
    assert thermal2[1] == pytest.approx(50.0, abs=1e-6)  # stage 1 peak
    assert thermal2[3] == pytest.approx(0.0, abs=0.05)  # stage 2 peak
    assert thermal2[5] == pytest.approx(0.0, abs=0.05)  # stage 3 peak
    # The synthetic discharge generator (uid not a PLP central) supplies
    # the rest of the stage 2-3 peaks and nothing in stage 1.
    others = by.drop(columns=[c for c in (1, 2) if c in by.columns])
    battery = others.sum(axis=1)
    assert float(battery.iloc[1]) == pytest.approx(0.0, abs=1e-6)
    assert float(battery.iloc[3]) == pytest.approx(50.0, abs=0.05)
