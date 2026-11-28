# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""E2E smoke cases for the public *converter* console scripts."""

from __future__ import annotations

import csv
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from _cli_e2e import (
    CASES_DIR,
    SCRIPT_CASES_DIR,
    SCRIPTS_DIR,
    CliRun,
    assert_planning_json,
    assert_solved,
    copy_case,
    e2e,
    load_json,
)


def _plp2gtopt_min_1bus(ctx: CliRun) -> Path:
    out_dir = ctx.tmp_path / "plp_min_1bus"
    case_json = ctx.tmp_path / "plp_min_1bus.json"
    ctx.run(
        [
            "-i",
            SCRIPT_CASES_DIR / "plp_min_1bus",
            "-o",
            out_dir,
            "-f",
            case_json,
        ]
    )
    data = assert_planning_json(case_json)
    assert data["system"]["generator_array"][0]["name"] == "Thermal1"
    return case_json


@e2e("plp2gtopt")
def case_convert_min_1bus(ctx: CliRun) -> None:
    """PLP .dat directory -> gtopt JSON."""
    _plp2gtopt_min_1bus(ctx)


@e2e("plp2gtopt", needs_gtopt=True)
def case_roundtrip_min_2bus(ctx: CliRun) -> None:
    """PLP -> gtopt JSON -> optimal gtopt solve."""
    case_json = ctx.tmp_path / "plp_min_2bus.json"
    ctx.run(
        [
            "-i",
            SCRIPT_CASES_DIR / "plp_min_2bus",
            "-o",
            ctx.tmp_path / "plp_min_2bus",
            "-f",
            case_json,
        ]
    )
    assert_planning_json(case_json)
    assert_solved(ctx.run_gtopt(case_json))


@e2e("plp2gtopt", needs_gtopt=True)
def case_zip_roundtrip_min_2bus(ctx: CliRun) -> None:
    """PLP -> gtopt ZIP (-z) -> unzip -> optimal gtopt solve from the root."""
    out_dir = ctx.tmp_path / "gtopt_min2"
    ctx.run(["-i", SCRIPT_CASES_DIR / "plp_min_2bus", "-o", out_dir, "-z"])
    unzipped = ctx.tmp_path / "unzipped"
    with zipfile.ZipFile(ctx.tmp_path / "gtopt_min2.zip") as zf:
        zf.extractall(unzipped)
    case_json = unzipped / "gtopt_min2.json"
    data = assert_planning_json(case_json)
    assert data["options"]["input_directory"] == "gtopt_min2"
    assert_solved(ctx.run_gtopt(case_json))


# ---------------------------------------------------------------------------
# pp2gtopt
# ---------------------------------------------------------------------------


def _pp2gtopt_case9(ctx: CliRun) -> Path:
    case_json = ctx.tmp_path / "case9" / "case9.json"
    ctx.run(["-n", "case9", "-o", case_json])
    data = assert_planning_json(case_json)
    assert len(data["system"]["bus_array"]) == 9
    return case_json


@e2e("pp2gtopt")
def case_convert_case9(ctx: CliRun) -> None:
    """Built-in pandapower case9 -> gtopt JSON."""
    _pp2gtopt_case9(ctx)


@e2e("pp2gtopt", needs_gtopt=True)
def case_roundtrip_case9(ctx: CliRun) -> None:
    """pandapower case9 -> gtopt JSON -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_pp2gtopt_case9(ctx)))


# ---------------------------------------------------------------------------
# igtopt
# ---------------------------------------------------------------------------


def _igtopt_c0(ctx: CliRun) -> Path:
    case_json = ctx.tmp_path / "c0" / "system_c0.json"
    ctx.run(
        [
            SCRIPT_CASES_DIR / "igtopt_c0" / "system_c0.xlsx",
            "-j",
            case_json,
            "-d",
            case_json.parent / "system_c0",
        ]
    )
    assert_planning_json(case_json)
    assert (case_json.parent / "system_c0" / "Demand" / "lmax.parquet").is_file()
    return case_json


@e2e("igtopt")
def case_convert_c0_workbook(ctx: CliRun) -> None:
    """Excel workbook -> gtopt JSON + Parquet schedules."""
    _igtopt_c0(ctx)


@e2e("igtopt", needs_gtopt=True)
def case_roundtrip_c0_workbook(ctx: CliRun) -> None:
    """Excel workbook -> gtopt JSON -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_igtopt_c0(ctx)))


# ---------------------------------------------------------------------------
# nrel118_to_gtopt -- offline on a synthetic 3-bus dataset in NREL format
# ---------------------------------------------------------------------------

_NREL_HOURS = 24
_NREL_GEN_HEADER = (
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
_NREL_GEN_ROWS = (
    "CC NG 01,bus001,200,0,DA UC,RT,300,6500,,,,,200,,,,,CC NG 01,"
    "2,2,6,8,40,CC NG 01,3000,1.5,200,CC NG 1,DA",
    "CT Oil 01,bus003,100,0,DA UC,RT,200,10000,,,,,100,,,,,CT Oil 01,"
    "5,5,1,1,10,CT Oil 01,500,4,100,CT Oil 1,DA",
    "Wind 01,bus002,40,,,,,,,,,,,,,,,,,,,,0,,,0,Wind 01,,DA",
)


def _nrel_hourly(path: Path, values: list[float]) -> None:
    rows = ['"DATETIME","value"'] + [f"h{k},{v:.3f}" for k, v in enumerate(values)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_nrel_mini(root: Path) -> Path:
    """3-bus / 2-region dataset in the upstream NREL-118 CSV formats."""
    root.mkdir(parents=True)
    (root / "Buses.csv").write_text(
        "Number,Name,BusType,Angle,Magnitude ,Voltage-Max (pu),"
        "Voltage-Min (pu),Base Voltage kV,Area,LoadZone\n"
        "1,,AC,,1,1.06,0.94,138,R1,\n"
        "2,,AC,,1,1.06,0.94,138,R1,\n"
        "3,,AC,,1,1.06,0.94,138,R2,\n",
        encoding="utf-8",
    )
    (root / "partfact.csv").write_text(
        "Bus Name,Region,Load Participation Factor\n"
        "bus001,R1,0.25\nbus002,R1,0.75\nbus003,R2,1\n",
        encoding="utf-8",
    )
    (root / "Lines.csv").write_text(
        "Line Name,Bus from ,Bus to,Max Flow (MW),Min Flow (MW),"
        "Reactance (p.u.),Resistance (p.u.)\n"
        "line001,bus001,bus002,600,-600,0.1,0.03\n"
        "line002,bus002,bus003,300,-300,0.05,0.01\n"
        "line003,bus001,bus003,600,-600,0.2,0.05\n",
        encoding="utf-8",
    )
    (root / "Generators.csv").write_text(
        "\n".join([_NREL_GEN_HEADER, *_NREL_GEN_ROWS]) + "\n", encoding="utf-8"
    )
    hours = range(_NREL_HOURS)
    _nrel_hourly(root / "LoadR1DA.csv", [100.0 + 2.0 * (h % 12) for h in hours])
    _nrel_hourly(root / "LoadR2DA.csv", [40.0 + 1.0 * (h % 12) for h in hours])
    _nrel_hourly(root / "Wind1DA.csv", [10.0 + (h % 5) for h in hours])
    (root / "118-hydro.csv").write_text("", encoding="utf-8")
    (root / "Hydro_nondispatchable.csv").write_text(
        "Generator,Property,Value,Units,Band,Date From,Date To,"
        "Timeslice,Escalator,Data File,Scenario,Memo,Category\n",
        encoding="utf-8",
    )
    return root


def _nrel118_mini(ctx: CliRun) -> Path:
    cache = _write_nrel_mini(ctx.tmp_path / "nrel_cache")
    case_json = ctx.tmp_path / "run" / "nrel.json"
    ctx.run(
        [
            "--cache-dir",
            cache,
            "--no-download",
            "--hours",
            f"0+{_NREL_HOURS}",
            "--stage-hours",
            "12",
            "-o",
            case_json,
        ]
    )
    data = assert_planning_json(case_json)
    assert len(data["system"]["bus_array"]) == 3
    assert len(data["simulation"]["block_array"]) == _NREL_HOURS
    return case_json


@e2e("nrel118_to_gtopt")
def case_convert_offline_mini(ctx: CliRun) -> None:
    """Synthetic NREL-format cache (--no-download) -> gtopt JSON."""
    _nrel118_mini(ctx)


@e2e("nrel118_to_gtopt")
def case_missing_cache_offline(ctx: CliRun) -> None:
    """Empty cache + --no-download: clean exit 2, no network access."""
    res = ctx.run(["--cache-dir", ctx.tmp_path / "empty", "--no-download"], expect_rc=2)
    assert "Traceback" not in res.output


@e2e("nrel118_to_gtopt", needs_gtopt=True)
def case_roundtrip_offline_mini(ctx: CliRun) -> None:
    """Synthetic NREL-format cache -> gtopt JSON -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_nrel118_mini(ctx)))


# ---------------------------------------------------------------------------
# plexos2gtopt -- vendored plexos-coad IEEE 118-bus XML (t_data fallback)
# ---------------------------------------------------------------------------

_PLEXOS_118 = SCRIPTS_DIR / "plexos2gtopt" / "tests" / "data" / "plexos_coad"


def _plexos2gtopt_118(ctx: CliRun) -> Path:
    out_dir = ctx.tmp_path / "p118"
    ctx.run(
        [
            _PLEXOS_118 / "118-Bus.xml.xz",
            "-o",
            out_dir,
            "--horizon-mode",
            "plexos",
            "--block-layout",
            "1",
            "--line-losses-mode",
            "none",
        ]
    )
    case_json = out_dir / "118-Bus.json"
    data = assert_planning_json(case_json)
    assert len(data["system"]["bus_array"]) == 118
    return case_json


@e2e("plexos2gtopt")
def case_convert_coad_118bus(ctx: CliRun) -> None:
    """PLEXOS XML (IEEE 118-bus, plexos-coad) -> gtopt JSON."""
    _plexos2gtopt_118(ctx)


@e2e("plexos2gtopt")
def case_info_coad_118bus(ctx: CliRun) -> None:
    """``--info`` inspects a bundle without converting."""
    res = ctx.run(["--info", _PLEXOS_118 / "118-Bus.xml.xz"])
    assert "Generator" in res.output


@e2e("plexos2gtopt", needs_gtopt=True)
def case_roundtrip_coad_118bus(ctx: CliRun) -> None:
    """PLEXOS XML -> gtopt JSON -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_plexos2gtopt_118(ctx)))


# ---------------------------------------------------------------------------
# sddp2gtopt / psse2gtopt
# ---------------------------------------------------------------------------


def _sddp2gtopt_case_min(ctx: CliRun) -> Path:
    out_dir = ctx.tmp_path / "sddp"
    ctx.run(
        [
            "-i",
            SCRIPTS_DIR / "sddp2gtopt" / "tests" / "data" / "case_min",
            "-o",
            out_dir,
        ]
    )
    return out_dir / "sddp.json"


@e2e("sddp2gtopt")
def case_convert_case_min(ctx: CliRun) -> None:
    """PSR SDDP psrclasses.json (1 thermal, 1 demand) -> gtopt JSON."""
    assert_planning_json(_sddp2gtopt_case_min(ctx))


@e2e("sddp2gtopt", needs_gtopt=True)
def case_roundtrip_case_min(ctx: CliRun) -> None:
    """SDDP case -> gtopt JSON -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_sddp2gtopt_case_min(ctx)))


def _psse2gtopt_ieee14(ctx: CliRun) -> Path:
    out_dir = ctx.tmp_path / "psse"
    ctx.run(
        [
            "-i",
            SCRIPTS_DIR / "psse2gtopt" / "tests" / "data" / "ieee14.raw",
            "-o",
            out_dir,
        ]
    )
    data = assert_planning_json(out_dir / "psse.json")
    assert len(data["system"]["bus_array"]) == 14
    return out_dir / "psse.json"


@e2e("psse2gtopt")
def case_convert_ieee14(ctx: CliRun) -> None:
    """PSS/E RAW (IEEE 14-bus) -> gtopt JSON."""
    _psse2gtopt_ieee14(ctx)


@e2e("psse2gtopt", needs_gtopt=True)
def case_roundtrip_ieee14(ctx: CliRun) -> None:
    """PSS/E RAW -> gtopt JSON -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_psse2gtopt_ieee14(ctx)))


# ---------------------------------------------------------------------------
# opendss2gtopt / gtopt2opendss (hand-made feeder, no OpenDSS engine)
# ---------------------------------------------------------------------------

_TINY_DSS = SCRIPTS_DIR / "opendss2gtopt" / "tests" / "data" / "tiny_feeder.dss"


def _opendss2gtopt_tiny(ctx: CliRun, *extra: str) -> Path:
    out_dir = ctx.tmp_path / "dss"
    ctx.run(["-i", _TINY_DSS, "-o", out_dir, *extra])
    data = assert_planning_json(out_dir / "tiny.json")
    assert len(data["system"]["bus_array"]) == 10
    return out_dir / "tiny.json"


@e2e("opendss2gtopt")
def case_convert_tiny_feeder(ctx: CliRun) -> None:
    """OpenDSS script (12.47/4.16 kV feeder with regulators, DER) -> gtopt."""
    _opendss2gtopt_tiny(ctx)


@e2e("opendss2gtopt", needs_gtopt=True)
def case_roundtrip_tiny_feeder(ctx: CliRun) -> None:
    """OpenDSS script -> gtopt DC case -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_opendss2gtopt_tiny(ctx, "--line-rating", "none")))


@e2e("gtopt2opendss")
def case_export_ieee_4b(ctx: CliRun) -> None:
    """gtopt case (ieee_4b_ori) -> balanced 3-phase OpenDSS script."""
    out = ctx.tmp_path / "ieee4b.dss"
    ctx.run([CASES_DIR / "ieee_4b_ori" / "ieee_4b_ori.json", "-o", out])
    text = out.read_text(encoding="utf-8")
    assert "New Circuit." in text and text.count("New Line.") == 5


# ---------------------------------------------------------------------------
# sienna2gtopt -- the Sienna data is not redistributable; it is only
# used when already cached.  GTOPT_OFFLINE=1 forbids downloads here.
# ---------------------------------------------------------------------------


def _sienna_cached() -> bool:
    # pylint: disable-next=import-outside-toplevel
    from sienna2gtopt._bundle import bundle_path

    return bundle_path("5bus").is_file()


@e2e("sienna2gtopt")
def case_convert_or_clean_offline_error(ctx: CliRun) -> None:
    """Convert ``hvdc`` when cached, else a clean offline error (exit 2)."""
    ctx.env["GTOPT_OFFLINE"] = "1"
    out = ctx.tmp_path / "hvdc.json"
    if _sienna_cached():
        ctx.run(["hvdc", "-o", out])
        assert_planning_json(out)
    else:
        res = ctx.run(["hvdc", "-o", out], expect_rc=2)
        assert "Traceback" not in res.output
        assert "not cached" in res.output


@e2e("sienna2gtopt", needs_gtopt=True)
def case_roundtrip_cascading_hydro(ctx: CliRun) -> None:
    """Sienna 5-bus cascading hydro -> gtopt JSON -> optimal solve."""
    if not _sienna_cached():
        pytest.skip("Sienna 5-bus bundle not cached (not redistributable)")
    ctx.env["GTOPT_OFFLINE"] = "1"
    case_json = ctx.tmp_path / "cascading.json"
    ctx.run(["cascading_hydro", "-o", case_json])
    assert_planning_json(case_json)
    assert_solved(ctx.run_gtopt(case_json))


@e2e("sienna_to_gtopt")
def case_legacy_alias_offline(ctx: CliRun) -> None:
    """Deprecated alias: same converter, plus a deprecation note."""
    ctx.env["GTOPT_OFFLINE"] = "1"
    out = ctx.tmp_path / "hvdc.json"
    if _sienna_cached():
        res = ctx.run(["hvdc", "-o", out])
        assert_planning_json(out)
    else:
        res = ctx.run(["hvdc", "-o", out], expect_rc=2)
        assert "Traceback" not in res.output
        assert "not cached" in res.output
    assert "sienna_to_gtopt is deprecated" in res.output


# ---------------------------------------------------------------------------
# gtopt2plp / gtopt2sddp / gtopt2plexos (gtopt -> other tools' inputs)
# ---------------------------------------------------------------------------


@e2e("gtopt2plp")
def case_export_plp_ieee_4b(ctx: CliRun) -> None:
    """gtopt JSON (ieee_4b_ori) -> PLP .dat files."""
    out = ctx.tmp_path / "plp"
    ctx.run([CASES_DIR / "ieee_4b_ori" / "ieee_4b_ori.json", "-o", out])
    for name in ("plpbar.dat", "plpblo.dat", "plpcnfce.dat", "plpdem.dat"):
        assert (out / name).stat().st_size > 0, name
    assert "warnings" in json.loads((out / "gtopt2plp.json").read_text())


@e2e("gtopt2sddp")
def case_export_sddp_ieee_4b(ctx: CliRun) -> None:
    """gtopt JSON (ieee_4b_ori) -> PSR SDDP block study .dat files."""
    out = ctx.tmp_path / "sddp"
    ctx.run([CASES_DIR / "ieee_4b_ori" / "ieee_4b_ori.json", "-o", out])
    for name in ("sddp.dat", "dbus.dat", "dcirc.dat", "ctermigt.dat"):
        assert (out / name).stat().st_size > 0, name
    assert "warnings" in json.loads((out / "gtopt2sddp.json").read_text())


@e2e("gtopt2plexos")
def case_export_plexos_ieee_4b(ctx: CliRun) -> None:
    """gtopt JSON (ieee_4b_ori) -> PLEXOS XML dataset + data files."""
    out = ctx.tmp_path / "plexos"
    ctx.run([CASES_DIR / "ieee_4b_ori" / "ieee_4b_ori.json", "-o", out])
    assert list(out.glob("*.xml"))
    assert (out / "block_layout.csv").stat().st_size > 0
    assert "warnings" in json.loads((out / "gtopt2plexos.json").read_text())


# ---------------------------------------------------------------------------
# gtopt2pp
# ---------------------------------------------------------------------------


@e2e("gtopt2pp")
def case_export_ieee_4b(ctx: CliRun) -> None:
    """gtopt JSON (ieee_4b_ori) -> pandapower JSON network."""
    out = ctx.tmp_path / "ieee_4b_pp.json"
    ctx.run([CASES_DIR / "ieee_4b_ori" / "ieee_4b_ori.json", "-o", out])
    # pylint: disable-next=import-outside-toplevel
    import pandapower as pp

    net = pp.from_json(str(out))
    assert len(net.bus) == 4
    assert len(net.gen) + len(net.sgen) + len(net.ext_grid) > 0


# ---------------------------------------------------------------------------
# ts2gtopt / cvs2parquet
# ---------------------------------------------------------------------------


@e2e("ts2gtopt")
def case_project_hourly_year(ctx: CliRun) -> None:
    """Synthetic hourly year -> 4-stage x 24-block Parquet schedule."""
    ts = ctx.tmp_path / "in" / "lmax.csv"
    ts.parent.mkdir()
    lines = ["datetime,uid:1"]
    start = datetime(2023, 1, 1)
    for h in range(8760):
        lines.append(f"{start + timedelta(hours=h):%Y-%m-%d %H:%M:%S},{h % 24}")
    ts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = ctx.tmp_path / "out"
    horizon = ctx.tmp_path / "horizon.json"
    ctx.run([ts, "-y", "2023", "--stages", "4", "-o", out, "--output-horizon", horizon])
    # pylint: disable-next=import-outside-toplevel
    import pandas as pd

    df = pd.read_parquet(out / "lmax.parquet")
    assert len(df) == 4 * 24
    assert load_json(horizon)


@e2e("cvs2parquet")
def case_csv_to_parquet(ctx: CliRun) -> None:
    """CSV schedule -> Parquet with gtopt index dtypes."""
    csv = ctx.tmp_path / "lmax.csv"
    csv.write_text(
        "scenario,stage,block,uid:1,uid:2\n1,1,1,10.5,20\n1,1,2,11,21\n",
        encoding="utf-8",
    )
    out = ctx.tmp_path / "lmax.parquet"
    ctx.run([csv, "-o", out])
    # pylint: disable-next=import-outside-toplevel
    import pandas as pd

    df = pd.read_parquet(out)
    assert list(df.columns) == ["scenario", "stage", "block", "uid:1", "uid:2"]
    assert str(df["stage"].dtype) == "int32"
    assert df["uid:1"].tolist() == [10.5, 11.0]


# ---------------------------------------------------------------------------
# gtopt_expand
# ---------------------------------------------------------------------------


@e2e("gtopt_expand")
def case_lng_terminal(ctx: CliRun) -> None:
    """Canonical lng.json -> LngTerminal entities."""
    lng = ctx.tmp_path / "lng.json"
    lng.write_text(
        json.dumps(
            {
                "terminals": [
                    {
                        "number": 1,
                        "name": "GNL_A",
                        "vmax": 150000.0,
                        "vini": 80000.0,
                        "cgnl": 5.0,
                        "cver": 100.0,
                        "creg": 2.5,
                        "calm": 0.01,
                        "gnlren": 0.9,
                        "generators": [{"name": "CC_1", "efficiency": 0.4}],
                        "deliveries": [{"stage": 1, "volume": 50000}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out = ctx.tmp_path / "out"
    ctx.run(["lng", "--input", lng, "--output", out, "--num-stages", "2"])
    entities = load_json(out / "lng_entities.json")
    assert [t["name"] for t in entities["lng_terminal_array"]] == ["GNL_A"]


# ---------------------------------------------------------------------------
# plp_compress_case / plexos_compress_case
# ---------------------------------------------------------------------------


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@e2e("plp_compress_case")
def case_compress_convert_decompress(ctx: CliRun) -> None:
    """Compress a PLP case, convert it compressed, restore it bit-exact."""
    src = SCRIPT_CASES_DIR / "plp_min_1bus"
    work = copy_case(src, ctx.tmp_path / "plp")
    cfg: list[str | Path] = ["--config", ctx.tmp_path / "none.conf", "--codec", "xz"]
    ctx.run([*cfg, work])
    assert not list(work.glob("*.dat")), "plain .dat left after compress"
    assert list(work.glob("*.dat.xz"))
    # plp2gtopt reads the compressed files transparently.
    case_json = ctx.tmp_path / "from_xz.json"
    ctx.run(
        ["-i", work, "-o", ctx.tmp_path / "from_xz", "-f", case_json],
        script="plp2gtopt",
    )
    assert_planning_json(case_json)
    ctx.run([*cfg, "--decompress", work])
    assert _tree_bytes(work) == _tree_bytes(src)


@e2e("plexos_compress_case")
def case_zip_roundtrip(ctx: CliRun) -> None:
    """Compress a PLEXOS ``*.zip`` bundle to ``.zip.xz`` and back."""
    work = ctx.tmp_path / "bundle"
    work.mkdir()
    payload = work / "DATOS20260101.zip"
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("Nod_Load.csv", "YEAR,MONTH,DAY,PERIOD,b1\n2026,1,1,1,10\n")
    original = payload.read_bytes()
    cfg: list[str | Path] = ["--config", ctx.tmp_path / "none.conf", "--codec", "xz"]
    ctx.run([*cfg, work])
    assert not payload.exists()
    assert (work / "DATOS20260101.zip.xz").is_file()
    ctx.run([*cfg, "--decompress", work])
    assert payload.read_bytes() == original


# ---------------------------------------------------------------------------
# gasnet2gtopt
# ---------------------------------------------------------------------------


def _gasnet2gtopt_tiny_plant(ctx: CliRun) -> Path:
    out = ctx.tmp_path / "gas" / "tiny_plant.json"
    ctx.run(
        [
            SCRIPTS_DIR / "gasnet2gtopt" / "cases" / "tiny_plant.gasnet.json",
            "-o",
            out,
            "--nblocks",
            "2",
            "--booster",
            "on",
        ]
    )
    return out


@e2e("gasnet2gtopt")
def case_convert_tiny_plant(ctx: CliRun) -> None:
    """Gas network + plant pressure gate -> native gas arrays."""
    data = assert_planning_json(_gasnet2gtopt_tiny_plant(ctx))
    assert data["system"]["gas_turbine_array"]


@e2e("gasnet2gtopt", needs_gtopt=True)
def case_roundtrip_tiny_plant(ctx: CliRun) -> None:
    """Gas network -> native gtopt JSON -> optimal gtopt solve."""
    assert_solved(ctx.run_gtopt(_gasnet2gtopt_tiny_plant(ctx)))


# ---------------------------------------------------------------------------
# cucdata2gtopt -- the CUC-data workbooks have no licence: the e2e cases use
# the synthetic workbook of the package tests (same layout, made-up data).
# ---------------------------------------------------------------------------


def _cucdata2gtopt_synthetic(ctx: CliRun) -> Path:
    # pylint: disable-next=import-outside-toplevel,import-error
    from cucdata2gtopt.tests._synthetic import make_workbook

    workbook = make_workbook(ctx.tmp_path / "CUC_synthetic.xlsx")
    out_dir = ctx.tmp_path / "cuc"
    ctx.run(["--input", workbook, "--reserve", "10", "-o", out_dir])
    return out_dir / "cuc39_r10_s2.json"


@e2e("cucdata2gtopt")
def case_convert_synthetic(ctx: CliRun) -> None:
    """Synthetic CUC workbook -> IUC planning JSON with GAMS user rows."""
    data = assert_planning_json(_cucdata2gtopt_synthetic(ctx))
    assert len(data["system"]["commitment_array"]) == 4
    assert data["system"]["user_constraint_array"]


@e2e("cucdata2gtopt", needs_gtopt=True)
def case_roundtrip_synthetic(ctx: CliRun) -> None:
    """Synthetic CUC workbook -> gtopt JSON -> optimal MIP solve."""
    assert_solved(ctx.run_gtopt(_cucdata2gtopt_synthetic(ctx)))


# ---------------------------------------------------------------------------
# dlr2gtopt -- synthetic towers and weather (the package test helpers); no
# network.  A gtopt solve round trip needs Line.dlr support in the binary.
# ---------------------------------------------------------------------------


@e2e("dlr2gtopt")
def case_dlr_pipeline_synthetic(ctx: CliRun) -> None:
    """Towers -> spans -> weather -> ampacity -> critical spans -> Line.dlr."""
    # pylint: disable-next=import-outside-toplevel,import-error
    from dlr2gtopt._spans import read_spans, write_towers

    # pylint: disable-next=import-outside-toplevel,import-error
    from dlr2gtopt.tests._synthetic import make_towers, make_weather

    tmp = ctx.tmp_path
    write_towers(tmp / "towers.csv", make_towers(11))
    ctx.run(["spans", tmp / "towers.csv", "-o", tmp / "spans.csv"])
    make_weather(read_spans(tmp / "spans.csv"), hours=48).write(tmp / "w.csv")
    ctx.run(
        [
            "ampacity",
            "--spans",
            tmp / "spans.csv",
            "--weather",
            tmp / "w.csv",
            "--conductor",
            "flint",
            "--tmax",
            "55",
            "-o",
            tmp / "amp.csv",
        ]
    )
    ctx.run(
        ["critical-spans", tmp / "amp.csv", "--max-spans", "3", "-o", tmp / "crit.json"]
    )
    crit = json.loads((tmp / "crit.json").read_text(encoding="utf-8"))
    assert 1 <= len(crit["critical_spans"]) <= 3
    assert crit["trace"]
    ctx.run(
        [
            "emit",
            "--spans",
            tmp / "spans.csv",
            "--weather",
            tmp / "w.csv",
            "--critical",
            tmp / "crit.json",
            "--conductor",
            "flint",
            "--tmax",
            "55",
            "--block-hours",
            "6",
            "--format",
            "csv",
            "-o",
            tmp / "case",
        ]
    )
    dlr = json.loads((tmp / "case" / "dlr_line1.json").read_text(encoding="utf-8"))
    assert len(dlr["dlr"]["critical_spans"]) == len(crit["critical_spans"])
    rows = (tmp / "case" / "Line" / "dlr_wind_speed.csv").read_text(encoding="utf-8")
    assert rows.startswith("scenario,stage,block,uid,value")


# ---------------------------------------------------------------------------
# tcheby2gtopt
# ---------------------------------------------------------------------------


@e2e("tcheby2gtopt")
def case_tcheby_convert_reszone(ctx: CliRun) -> None:
    """tcheby SQLite system DB -> block + Chebyshev point-stage JSON."""
    # pylint: disable-next=import-outside-toplevel,import-error
    from tcheby2gtopt.tests.conftest import RESZONE_SYSTEM, make_db

    db = make_db(ctx.tmp_path / "system1_reszone.db", RESZONE_SYSTEM)
    out = ctx.tmp_path / "out"
    ctx.run(["convert", db, "--name", "rz", "--out-dir", out, "--hours", "12"])
    blk = assert_planning_json(out / "rz_block.json")
    assert len(blk["simulation"]["block_array"]) == 12
    assert_planning_json(out / "rz_point.json")


# ---------------------------------------------------------------------------
# rts_gmlc_to_gtopt
# ---------------------------------------------------------------------------

_RTS_GEN_HEADER = [
    "GEN UID", "Bus ID", "Unit Type", "Fuel", "PMax MW", "PMin MW", "VOM",
    "Fuel Price $/MMBTU", "HR_avg_0", "HR_incr_1", "HR_incr_2", "HR_incr_3",
    "HR_incr_4", "Output_pct_0", "Output_pct_1", "Output_pct_2",
    "Output_pct_3", "Output_pct_4", "Emissions CO2 Lbs/MMBTU",
    "Emissions SO2 Lbs/MMBTU", "Emissions NOX Lbs/MMBTU",
    "Emissions CH4 Lbs/MMBTU", "Emissions N2O Lbs/MMBTU",
    "Emissions CO Lbs/MMBTU", "Emissions VOCs Lbs/MMBTU",
]  # fmt: skip
_RTS_GEN_ROWS = [
    ["101_STEAM_1", "101", "STEAM", "Coal", "100", "30", "2.5", "2.0",
     "10000", "0", "0", "0", "0", "1.0", "0", "0", "0", "0",
     "210", "0.5", "", "0", "0", "0", "0"],
    ["102_PV_1", "102", "PV", "Solar", "50", "0", "0", "0",
     "", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", ""],
]  # fmt: skip


@e2e("rts_gmlc_to_gtopt")
def case_rts_gmlc_offline_cache(ctx: CliRun) -> None:
    """Primed RTS-GMLC CSV cache (no download) -> gtopt JSON."""
    cache = ctx.tmp_path / "rts"
    cache.mkdir()
    tables = {
        "gen.csv": [_RTS_GEN_HEADER, *_RTS_GEN_ROWS],
        "bus.csv": [["Bus ID", "Bus Name"], ["101", "Abel"], ["102", "Adams"]],
        "branch.csv": [["UID", "From Bus", "To Bus"], ["A1", "101", "102"]],
    }
    for name, rows in tables.items():
        with (cache / name).open("w", newline="", encoding="utf-8") as fp:
            csv.writer(fp).writerows(rows)
    out = ctx.tmp_path / "rts.json"
    res = ctx.run(
        ["--no-download", "--cache-dir", cache, "-o", out, "--pollutants", "co2"]
    )
    data = assert_planning_json(out)
    assert len(data["system"]["generator_array"]) == 2
    assert "Written:" in res.stdout


# ---------------------------------------------------------------------------
# re2gtopt -- one wind plant on synthetic hub-height weather (the dlr2gtopt
# synthetic history's hours); no network, no pvlib.
# ---------------------------------------------------------------------------


@e2e("re2gtopt")
def case_re_wind_model_synthetic(ctx: CliRun) -> None:
    """Plant list + plant weather -> hourly wind capacity factors."""
    # pylint: disable-next=import-outside-toplevel
    import numpy as np

    # pylint: disable-next=import-outside-toplevel,import-error
    from dlr2gtopt.tests._typical_synth import history

    # pylint: disable-next=import-outside-toplevel,import-error
    from re2gtopt._sources import PlantWeather

    tmp = ctx.tmp_path
    (tmp / "plants.csv").write_text(
        "name,type,lat,lon,pmax_mw,hub_height,turbine\n"
        "W1,wind,-31.505,-71.505,90,100,iec2\n",
        encoding="utf-8",
    )
    w = history(years=(2001, 2002))
    n = len(w.times)
    PlantWeather(
        list(w.times),
        ["W1"],
        {
            "wind_speed_hub": w.data["wind_speed"][:, :1] * 2.5,
            "temperature": np.full((n, 1), 15.0),
            "pressure": np.full((n, 1), np.nan),
            "ghi": np.zeros((n, 1)),
            "wind_speed_10m": np.full((n, 1), 3.0),
        },
        {"W1": 100.0},
    ).write(tmp / "weather.parquet")
    ctx.run(
        [
            "model",
            "--plants",
            tmp / "plants.csv",
            "--weather",
            tmp / "weather.parquet",
            "-o",
            tmp / "run",
        ]
    )
    assert (tmp / "run" / "cf_model.parquet").stat().st_size > 0
