"""plpmaness.dat (ESS maintenance) → the battery's own schedules.

``IBind Emin Emax DCMin DCMax [DCMod]`` rows used to go through a legacy
path that wrote ``Generator/pmax`` and ``Demand/lmax`` under the battery
uid — overwriting the central (plpmance.dat) and demand maintenance
files — and built separate ``<bat>_disch`` / ``<bat>_chrg`` elements.
They now set ``Battery/emin`` / ``emax`` / ``pmax_discharge`` /
``pmax_charge`` of the unified battery, and a DCMod=2 regulation tank's
``Reservoir/ess_emin`` / ``ess_emax``.
"""

import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from ..battery_writer import BatteryWriter
from ..block_parser import BlockParser
from ..central_parser import CentralParser
from ..ess_parser import EssParser
from ..mance_parser import ManceParser
from ..maness_parser import ManessParser
from ..plp2gtopt import convert_plp_case

_CASE = Path(__file__).parent.parent.parent / "cases" / "plp_min_ess_maness"


def _parse(cls, path: Path):
    p = cls(path)
    p.parse()
    return p


def _case_writer(**kw) -> BatteryWriter:
    return BatteryWriter(
        ess_parser=_parse(EssParser, _CASE / "plpess.dat"),
        maness_parser=_parse(ManessParser, _CASE / "plpmaness.dat"),
        central_parser=_parse(CentralParser, _CASE / "plpcnfce.dat"),
        block_parser=_parse(BlockParser, _CASE / "plpblo.dat"),
        **kw,
    )


def _sched(path: Path) -> dict[int, float]:
    df = pd.read_parquet(path)
    return {int(b): float(v) for b, v in zip(df["block"], df["value"], strict=True)}


def test_maness_sets_battery_schedules(tmp_path):
    writer = _case_writer()
    res = writer.process([], [], tmp_path)
    (bat,) = res["battery_array"]
    assert bat["bus"] == 1
    assert bat["emin"] == "emin" and bat["emax"] == "emax"
    assert bat["pmax_discharge"] == "pmax_discharge"
    assert bat["pmax_charge"] == "pmax_charge"
    # no legacy elements, nothing written for generators / demands
    assert res["generator_array"] == [] and res["demand_array"] == []
    assert "converter_array" not in res
    assert not (tmp_path / "Generator").exists()
    assert not (tmp_path / "Demand").exists()
    # every block; unlisted blocks keep the nominal values
    bdir = tmp_path / "Battery"
    assert _sched(bdir / "pmax_discharge.parquet") == {
        1: 0.0,
        2: 0.0,
        3: 50.0,
        4: 50.0,
        5: 50.0,
        6: 50.0,
    }
    assert _sched(bdir / "pmax_charge.parquet") == _sched(
        bdir / "pmax_discharge.parquet"
    )
    emax = _sched(bdir / "emax.parquet")
    assert emax == {1: 200.0, 2: 200.0, 3: 200.0, 4: 200.0, 5: 100.0, 6: 200.0}
    assert set(_sched(bdir / "emin.parquet").values()) == {0.0}
    df = pd.read_parquet(bdir / "emax.parquet")
    assert list(df["stage"]) == [1, 1, 2, 2, 3, 3]


def test_maness_wins_over_mance_on_shared_blocks(tmp_path):
    """plpmance.dat rows of the battery central fill blocks maness omits."""
    mance_file = tmp_path / "plpmance.dat"
    mance_file.write_text(
        " 1\n'BESS1'\n   2  01\n"
        "     01  001  1  0.00  30.00\n"
        "     02  004  1  0.00  20.00\n"
    )
    writer = _case_writer(mance_parser=_parse(ManceParser, mance_file))
    writer.process([], [], tmp_path / "o")
    dis = _sched(tmp_path / "o" / "Battery" / "pmax_discharge.parquet")
    # block 1: maness DCMax 0 wins over mance 30; block 4: mance 20
    assert dis == {1: 0.0, 2: 0.0, 3: 50.0, 4: 20.0, 5: 50.0, 6: 50.0}


def test_dcmin_is_warned(tmp_path, caplog):
    ess = tmp_path / "plpess.dat"
    ess.write_text(" 1\n  B  1.0  1.0  0.0  100.0  40.0  0\n")
    man = tmp_path / "plpmaness.dat"
    man.write_text("1\n'B'\n1\n1  0.0  100.0  5.0  40.0  0\n")
    writer = BatteryWriter(
        ess_parser=_parse(EssParser, ess), maness_parser=_parse(ManessParser, man)
    )
    writer.process([], [], tmp_path / "o")
    assert "DCMin > 0 ignored" in caplog.text
    # no block parser: the listed blocks only
    assert _sched(tmp_path / "o" / "Battery" / "pmax_charge.parquet") == {1: 40.0}


def test_regulation_tank_maness_goes_to_its_reservoir(tmp_path, caplog):
    ess = tmp_path / "plpess.dat"
    ess.write_text(" 1\n  TANK_ERD  1.0  1.0  0.0  800.0  300.0  2  HYD\n")
    man = tmp_path / "plpmaness.dat"
    man.write_text("1\n'TANK_ERD'\n1\n2  10.0  400.0  0.0  300.0  2\n")
    cp = type(
        "Cp",
        (),
        {
            "centrals": [
                {"name": "HYD", "number": 7, "type": "serie", "pmax": 300.0},
                {"name": "TANK_ERD", "number": 9, "type": "bateria", "pmax": 300.0},
            ]
        },
    )()
    writer = BatteryWriter(
        ess_parser=_parse(EssParser, ess),
        maness_parser=_parse(ManessParser, man),
        central_parser=cp,
    )
    res = writer.process([], [], tmp_path / "o")
    assert not res["battery_array"]
    (rsv,) = res["regulation_reservoirs"]
    assert rsv["emin"] == "ess_emin" and rsv["emax"] == "ess_emax"
    rdir = tmp_path / "o" / "Reservoir"
    assert _sched(rdir / "ess_emax.parquet") == {2: 400.0}
    assert _sched(rdir / "ess_emin.parquet") == {2: 10.0}
    # Reservoir/emin|emax stay the plpmanem.dat writer's files
    assert not (rdir / "emax.parquet").exists()
    assert "DCMin / DCMax not mapped" in caplog.text


def test_regulation_tank_without_maness_rows(tmp_path):
    ess = tmp_path / "plpess.dat"
    ess.write_text(" 1\n  TANK_ERD  1.0  1.0  0.0  800.0  300.0  2  HYD\n")
    man = tmp_path / "plpmaness.dat"
    man.write_text("1\n'OTHER'\n1\n2  10.0  400.0  0.0  300.0  0\n")
    cp = type(
        "Cp",
        (),
        {"centrals": [{"name": "HYD", "number": 7, "type": "serie"}]},
    )()
    writer = BatteryWriter(
        ess_parser=_parse(EssParser, ess),
        maness_parser=_parse(ManessParser, man),
        central_parser=cp,
    )
    res = writer.process([], [], tmp_path / "o")
    (rsv,) = res["regulation_reservoirs"]
    assert rsv["emax"] == pytest.approx(800.0)
    assert not (tmp_path / "o" / "Reservoir").exists()


def test_case_keeps_central_maintenance(tmp_path):
    """The converted case keeps Thermal2's plpmance.dat row in Generator/pmax."""
    out = tmp_path / "out"
    convert_plp_case(
        {
            "input_dir": _CASE,
            "output_dir": out,
            "output_file": out / "case.json",
            "hydrologies": "1",
        }
    )
    gens = {
        g["name"]: g
        for g in json.loads((out / "case.json").read_text())["system"][
            "generator_array"
        ]
    }
    assert "BESS1_disch" not in gens
    assert gens["Thermal2"]["pmax"] == "pmax"
    df = pd.read_parquet(out / "Generator" / "pmax.parquet")
    assert set(df["uid"]) == {2}
    assert float(df[df["block"] == 3]["value"].iloc[0]) == pytest.approx(80.0)


@pytest.mark.integration
def test_e2e_battery_out_in_stage_1(tmp_path, gtopt_bin):
    """Solved: no battery flow in stage 1; the derated emax holds in block 5."""
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
    rdir = out / "results" / "Battery"

    def by_block(stem: str) -> dict[int, float]:
        df = pd.concat([pd.read_csv(f) for f in sorted(rdir.glob(f"{stem}*.csv"))])
        return {int(b): float(v) for b, v in zip(df["block"], df["value"], strict=True)}

    fout, finp, energy = (
        by_block("fout_sol"),
        by_block("finp_sol"),
        by_block("energy_sol"),
    )
    assert fout.get(1, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert fout.get(2, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert finp.get(1, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert finp.get(2, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert energy.get(5, 0.0) <= 100.0 + 1e-6
    # the battery works from stage 2 on (peak shaving)
    assert max(fout.get(b, 0.0) for b in (4, 6)) > 1.0
