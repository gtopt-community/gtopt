"""Reserve products, day-ahead series and inertia in rts_gmlc_to_gtopt.

Synthetic CSVs shaped like RTS-GMLC ``SourceData/reserves.csv``,
``gen.csv`` (Category, Ramp Rate, Inertia, Base MVA) and the
``timeseries_data_files`` layouts (long ``Period`` and wide hourly).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

import rts_gmlc_to_gtopt.__main__ as cli
from rts_gmlc_to_gtopt._converter import (
    LOAD_SERIES,
    Conversion,
    RtsGen,
    RtsReserve,
    _paren_list,
    convert,
    day_date,
    inertia_elements,
    parse_generators,
    parse_reserves,
    read_day_series,
    reserve_elements,
    reserve_series_file,
    to_gtopt_json,
)

_GEN_HEADER = [
    "GEN UID",
    "Bus ID",
    "Unit Type",
    "Category",
    "Fuel",
    "PMax MW",
    "PMin MW",
    "VOM",
    "Fuel Price $/MMBTU",
    "Ramp Rate MW/Min",
    "Inertia MJ/MW",
    "Base MVA",
]
# heat-rate / emission columns the thermal path reads (left blank here)
_HR_COLS = [
    "HR_avg_0",
    *[f"HR_incr_{k}" for k in range(1, 5)],
    *[f"Output_pct_{k}" for k in range(5)],
    *[f"Emissions {p} Lbs/MMBTU" for p in ("CO2", "SO2", "NOX", "CH4", "N2O", "CO")],
    "Emissions VOCs Lbs/MMBTU",
]
_GEN_ROWS = [
    # area 1 gas CT: ramp 3 MW/min, H 2.8 s, 24 MVA
    ["101_CT_1", "101", "CT", "Gas CT", "NG", "20", "8", "5", "4", "3", "2.8", "24"],
    # area 2 coal: ramp 2 MW/min, H 4 s, no Base MVA -> pmax
    ["201_STEAM_1", "201", "STEAM", "Coal", "Coal", "150", "60", "2", "2", "2",
     "4", ""],
    # area 3 PV: no ramp (cap = pmax), no inertia
    ["301_PV_1", "301", "PV", "Solar PV", "Solar", "50", "0", "0", "0", "", "", ""],
    # area 1 nuclear: not an eligible category
    ["102_NUCLEAR_1", "102", "NUCLEAR", "Nuclear", "Nuc", "400", "396", "1",
     "0.5", "20", "5", "450"],
]  # fmt: skip

_RESERVES = [
    ["Reserve Product", "Timeframe (sec)", "Requirement (MW)", "Eligible Regions",
     "Eligible Device Categories", "Eligible Device SubCategories", "Direction"],
    ["Spin_Up_R1", "600", "40", "1", "(Generator)", "(Gas CT,Coal)", "Up"],
    ["Reg_Down", "300", "30", "(1,2,3)", "(Generator)",
     "(Gas CT,Coal,Solar PV)", "Down"],
    ["", "", "", "", "", "", ""],
]  # fmt: skip


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="") as fp:
        csv.writer(fp).writerows(rows)


def _long_rows(month: int, day: int, cols: int, base: float) -> list[list[str]]:
    # periods written in reverse order: the reader must sort them
    return [
        ["2020", str(month), str(day), str(h)]
        + [str(base + 10 * c + h) for c in range(cols)]
        for h in range(24, 0, -1)
    ]


@pytest.fixture(name="cache_dir")
def fixture_cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "rts"
    d.mkdir()
    blank = [""] * len(_HR_COLS)
    _write_csv(
        d / "gen.csv",
        [_GEN_HEADER + _HR_COLS, *[row + blank for row in _GEN_ROWS]],
    )
    _write_csv(d / "bus.csv", [["Bus ID"], ["101"], ["201"], ["301"]])
    _write_csv(d / "branch.csv", [["UID"], ["A1"]])
    _write_csv(d / "reserves.csv", _RESERVES)
    # Load (long, 3 areas): Jan 1 and Feb 1 (day 32)
    _write_csv(
        d / LOAD_SERIES,
        [["Year", "Month", "Day", "Period", "1", "2", "3"]]
        + _long_rows(1, 1, 3, 100.0)
        + _long_rows(2, 1, 3, 500.0),
    )
    # Spin_Up_R1 (long, one value column) for day 32 only
    _write_csv(
        d / reserve_series_file("Spin_Up_R1"),
        [["Year", "Month", "Day", "Period", "Spin_Up_R1"]] + _long_rows(2, 1, 1, 40.0),
    )
    # Reg_Down has NO cached series: flat requirement
    return d


def test_paren_list() -> None:
    assert _paren_list("(1,2, 3)") == ["1", "2", "3"]
    assert _paren_list("1") == ["1"]
    assert _paren_list("") == []


def test_parse_reserves(cache_dir: Path) -> None:
    res = parse_reserves(cache_dir / "reserves.csv")
    assert [r.name for r in res] == ["Spin_Up_R1", "Reg_Down"]
    spin, reg = res[0], res[1]
    assert spin.regions == (1,)
    assert spin.categories == ("Gas CT", "Coal")
    assert spin.timeframe_s == 600.0
    assert spin.direction == "Up"
    assert reg.regions == (1, 2, 3)
    assert reg.direction == "Down"


def test_parse_reserves_bad_direction(tmp_path: Path) -> None:
    path = tmp_path / "reserves.csv"
    _write_csv(path, [_RESERVES[0], ["X", "60", "1", "1", "", "(Coal)", "Sideways"]])
    with pytest.raises(ValueError, match="unknown direction"):
        parse_reserves(path)


def test_day_date() -> None:
    assert day_date(1).isoformat() == "2020-01-01"
    assert day_date(60).isoformat() == "2020-02-29"
    assert day_date(366).isoformat() == "2020-12-31"
    for bad in (0, 367):
        with pytest.raises(ValueError, match="outside"):
            day_date(bad)


def test_read_day_series_long_sorted(cache_dir: Path) -> None:
    series = read_day_series(cache_dir / LOAD_SERIES, 32)
    assert list(series) == ["1", "2", "3"]
    assert series["1"][:2] == [501.0, 502.0]
    assert series["3"][-1] == 544.0


def test_read_day_series_wide(tmp_path: Path) -> None:
    path = tmp_path / "wide.csv"
    _write_csv(
        path,
        [["Year", "Month", "Day", *[str(h) for h in range(1, 25)]],
         ["2020", "1", "2", *[str(h) for h in range(1, 25)]]],
    )  # fmt: skip
    assert read_day_series(path, 2) == {"value": [float(h) for h in range(1, 25)]}


def test_read_day_series_errors(tmp_path: Path) -> None:
    path = tmp_path / "short.csv"
    _write_csv(path, [["Year", "Month", "Day", "1", "2"], ["2020", "1", "1", "5", "6"]])
    with pytest.raises(ValueError, match="expected 24"):
        read_day_series(path, 1)
    with pytest.raises(ValueError, match="no rows"):
        read_day_series(path, 3)


def test_parse_generators_new_columns(cache_dir: Path) -> None:
    gens = {g.name: g for g in parse_generators(cache_dir / "gen.csv")}
    ct = gens["101_CT_1"]
    assert (ct.category, ct.area, ct.ramp_mw_per_min) == ("Gas CT", 1, 3.0)
    assert (ct.inertia_h, ct.base_mva) == (2.8, 24.0)
    assert gens["301_PV_1"].ramp_mw_per_min == 0.0


def test_reserve_eligibility_and_cap(cache_dir: Path) -> None:
    gens = {g.name: g for g in parse_generators(cache_dir / "gen.csv")}
    res = parse_reserves(cache_dir / "reserves.csv")
    spin, reg = res[0], res[1]
    assert spin.eligible(gens["101_CT_1"])
    assert not spin.eligible(gens["201_STEAM_1"])  # area 2
    assert not reg.eligible(gens["102_NUCLEAR_1"])  # category
    assert spin.cap_mw(gens["101_CT_1"]) == 20.0  # 3 * 10 min > pmax 20
    assert reg.cap_mw(gens["201_STEAM_1"]) == 10.0  # 2 MW/min * 5 min
    assert reg.cap_mw(gens["301_PV_1"]) == 50.0  # no ramp: pmax


def test_convert_timeseries_and_reserves(cache_dir: Path) -> None:
    conv = convert(cache_dir, day=32, timeseries=True, reserves=True)
    assert conv.load_source == "timeseries"
    assert conv.total_load_mw[0] == 501.0 + 511.0 + 521.0
    assert conv.reserve_series["Spin_Up_R1"][0] == 41.0  # from its series
    assert conv.reserve_series["Reg_Down"] == [30.0] * 24  # flat fallback


def test_convert_reserves_without_timeseries(cache_dir: Path) -> None:
    conv = convert(cache_dir, day=32, reserves=True)
    assert conv.load_source == "synthetic"
    assert conv.reserve_series["Spin_Up_R1"] == [40.0] * 24


def test_to_gtopt_json_reserves_and_inertia(cache_dir: Path) -> None:
    conv = convert(cache_dir, day=32, timeseries=True, reserves=True)
    payload = to_gtopt_json(
        conv, inertia=True, inertia_requirement_mws=900.0, shortfall_cost=500.0
    )
    system = payload["system"]
    zones = {z["name"]: z for z in system["reserve_zone_array"]}
    assert zones["Spin_Up_R1"]["urreq"][0][0] == 41.0
    assert zones["Spin_Up_R1"]["urcost"] == 500.0
    assert zones["Reg_Down"]["drreq"] == [[30.0] * 24]
    assert zones["Reg_Down"]["drcost"] == 500.0
    provs = {p["name"]: p for p in system["reserve_provision_array"]}
    assert sorted(provs) == [
        "101_CT_1_Reg_Down",
        "101_CT_1_Spin_Up_R1",
        "201_STEAM_1_Reg_Down",
        "301_PV_1_Reg_Down",
    ]
    ct_spin = provs["101_CT_1_Spin_Up_R1"]
    assert ct_spin["urmax"] == 20.0
    assert ct_spin["ur_provision_factor"] == 1.0
    assert ct_spin["shared_headroom"] is True
    assert provs["201_STEAM_1_Reg_Down"]["drmax"] == 10.0
    gen_uid = {g["name"]: g["uid"] for g in system["generator_array"]}
    assert ct_spin["generator"] == gen_uid["101_CT_1"]

    assert system["inertia_zone_array"] == [
        {"uid": 1, "name": "system", "requirement": 900.0, "cost": 500.0}
    ]
    iprovs = {p["name"]: p for p in system["inertia_provision_array"]}
    assert sorted(iprovs) == [
        "101_CT_1_inertia",
        "102_NUCLEAR_1_inertia",
        "201_STEAM_1_inertia",
    ]
    assert iprovs["101_CT_1_inertia"]["rated_power"] == 24.0
    assert iprovs["201_STEAM_1_inertia"]["rated_power"] == 150.0  # pmax
    assert iprovs["201_STEAM_1_inertia"]["inertia_constant"] == 4.0


def test_to_gtopt_json_default_has_no_reserve_or_inertia(cache_dir: Path) -> None:
    system = to_gtopt_json(convert(cache_dir))["system"]
    assert "reserve_zone_array" not in system
    assert "inertia_zone_array" not in system


def test_reserve_elements_missing_series_uses_requirement() -> None:
    gen = RtsGen(
        name="g",
        bus_id=101,
        unit_type="CT",
        fuel_kind="NG",
        pmax=10.0,
        pmin=0.0,
        vom=0.0,
        fuel_price_per_mmbtu=0.0,
        heat_rate_gj_per_mwh=0.0,
        combustion_tco2_per_gj=0.0,
        category="Gas CT",
    )
    prod = RtsReserve("R", 60.0, 7.0, (1,), ("Gas CT",), "Up")
    conv = Conversion(1, 2, 1, 1, 0, [1.0, 1.0], [gen], [prod], {})
    zones, provs = reserve_elements(conv, {"g": 1}, 10.0)
    assert zones[0]["urreq"] == [[7.0, 7.0]]
    assert provs[0]["urmax"] == 10.0
    izones, iprovs = inertia_elements(conv, {"g": 1}, 0.0, 10.0)
    assert izones[0]["requirement"] == 0.0
    assert not iprovs


class _FakeResp(io.BytesIO):
    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_timeseries_files(cache_dir: Path) -> None:
    files = cli.timeseries_files(cache_dir)
    assert files[0] == (f"{cli.TIMESERIES_BASE}/Load/{LOAD_SERIES}", LOAD_SERIES)
    assert [name for _, name in files[1:]] == [
        "DAY_AHEAD_regional_Spin_Up_R1.csv",
        "DAY_AHEAD_regional_Reg_Down.csv",
    ]
    assert files[2][0].startswith(f"{cli.TIMESERIES_BASE}/Reserves/")


def test_populate_cache_timeseries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    urls: list[str] = []

    def fake_urlopen(url: str) -> _FakeResp:
        urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name == "reserves.csv":
            buf = io.StringIO()
            csv.writer(buf).writerows(_RESERVES[:2])
            return _FakeResp(buf.getvalue().encode())
        return _FakeResp(b"x")

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    cli.populate_cache(cache, timeseries=True)
    names = [u.rsplit("/", 1)[-1] for u in urls]
    assert names[-2:] == [LOAD_SERIES, "DAY_AHEAD_regional_Spin_Up_R1.csv"]
    assert (cache / LOAD_SERIES).read_text() == "x"


def test_main_reserves_inertia_timeseries(
    cache_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "rts.json"
    rc = cli.main(
        [
            "--no-download",
            "--cache-dir",
            str(cache_dir),
            "-o",
            str(out),
            "--day",
            "32",
            "--timeseries",
            "--reserves",
            "--inertia",
            "--inertia-requirement",
            "1000",
            "--shortfall-cost",
            "250",
        ]
    )
    assert rc == 0
    system = json.loads(out.read_text())["system"]
    assert len(system["reserve_zone_array"]) == 2
    assert system["inertia_zone_array"][0]["requirement"] == 1000.0
    assert system["reserve_zone_array"][0]["urcost"] == 250.0
    assert "load = timeseries, reserves = 2" in capsys.readouterr().out
