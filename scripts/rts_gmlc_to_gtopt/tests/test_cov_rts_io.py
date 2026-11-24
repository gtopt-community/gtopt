"""CSV parsing, conversion and CLI tests for rts_gmlc_to_gtopt.

All inputs are tiny synthetic CSVs shaped like RTS-GMLC ``SourceData``;
the network download is mocked.
"""

from __future__ import annotations

import csv
import io
import json
import runpy
import sys
from pathlib import Path

import pytest

import rts_gmlc_to_gtopt.__main__ as cli
from rts_gmlc_to_gtopt._converter import (
    LBS_PER_MMBTU_TO_TCO2_PER_GJ,
    Conversion,
    RtsGen,
    _collapse_heat_rate,
    _count_csv_rows,
    _float_or,
    _parse_gen_row,
    convert,
    parse_buses,
    parse_generators,
    to_gtopt_json,
    write_json,
)

_GEN_HEADER = [
    "GEN UID",
    "Bus ID",
    "Unit Type",
    "Fuel",
    "PMax MW",
    "PMin MW",
    "VOM",
    "Fuel Price $/MMBTU",
    "HR_avg_0",
    "HR_incr_1",
    "HR_incr_2",
    "HR_incr_3",
    "HR_incr_4",
    "Output_pct_0",
    "Output_pct_1",
    "Output_pct_2",
    "Output_pct_3",
    "Output_pct_4",
    "Emissions CO2 Lbs/MMBTU",
    "Emissions SO2 Lbs/MMBTU",
    "Emissions NOX Lbs/MMBTU",
    "Emissions CH4 Lbs/MMBTU",
    "Emissions N2O Lbs/MMBTU",
    "Emissions CO Lbs/MMBTU",
    "Emissions VOCs Lbs/MMBTU",
]

_GEN_ROWS = [
    # coal unit: flat heat rate 10 000 BTU/kWh, CO2 210 lbs/MMBTU
    ["101_STEAM_1", "101", "STEAM", "Coal", "100", "30", "2.5", "2.0",
     "10000", "0", "0", "0", "0", "1.0", "0", "0", "0", "0",
     "210", "0.5", "", "0", "0", "0", "0"],
    # PV: renewable, heat-rate fields ignored
    ["102_PV_1", "102", "PV", "Solar", "50", "0", "0", "0",
     "", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", ""],
    # nuclear: thermal path (not in renewable set) but zero CO2
    ["103_NUCLEAR_1", "103", "NUCLEAR", "Nuclear", "400", "396", "1", "0.5",
     "10500", "0", "0", "0", "0", "1.0", "0", "0", "0", "0",
     "0", "0", "0", "0", "0", "0", "0"],
    # zero pmax -> dropped
    ["104_CT_1", "101", "CT", "Oil", "0", "0", "0", "0",
     "", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", ""],
    # bad pmax text -> 0 -> dropped
    ["105_CT_1", "101", "CT", "Oil", "n/a", "0", "0", "0",
     "", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", ""],
    # blank GEN UID -> skipped before parsing
    ["", "101", "CT", "Oil", "10", "0", "0", "0",
     "", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", ""],
    [],
]  # fmt: skip


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(header)
        writer.writerows(rows)


@pytest.fixture(name="cache_dir")
def fixture_cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "rts"
    d.mkdir()
    _write_csv(d / "gen.csv", _GEN_HEADER, _GEN_ROWS)
    _write_csv(
        d / "bus.csv",
        ["Bus ID", "Bus Name"],
        [["101", "Abel"], ["102", "Adams"], ["103.0", "Adler"], ["", "x"]],
    )
    _write_csv(
        d / "branch.csv",
        ["UID", "From Bus", "To Bus"],
        [["A1", "101", "102"], ["A2", "102", "103"], [" ", "", ""]],
    )
    return d


# ── small helpers ───────────────────────────────────────────────────────


def test_float_or() -> None:
    assert _float_or("1.5") == 1.5
    assert _float_or("", 7.0) == 7.0
    assert _float_or("abc", -1.0) == -1.0
    assert _float_or(None, 3.0) == 3.0  # type: ignore[arg-type]


def test_collapse_heat_rate_multi_band() -> None:
    # 50 % at 9 000 BTU/kWh, next 50 % at 11 000 incremental -> 10 000 avg
    hr = _collapse_heat_rate(9000.0, [11000.0, 0.0], [0.5, 1.0], 100.0)
    assert hr == pytest.approx(10000.0 * 1.055e-3)


def test_collapse_heat_rate_skips_non_increasing_and_extra_bands() -> None:
    # band 1 has delta 0 (skipped); hr_incr longer than output_pct (break)
    hr = _collapse_heat_rate(10000.0, [5000.0, 8000.0, 1.0], [0.5, 0.5, 1.0], 10.0)
    expected = (0.5 * 10000.0 + 0.5 * 8000.0) * 1.055e-3
    assert hr == pytest.approx(expected)


def test_collapse_heat_rate_empty_output_pct() -> None:
    assert _collapse_heat_rate(10000.0, [], [], 100.0) == 0.0


# ── CSV parsers ─────────────────────────────────────────────────────────


def test_parse_generators(cache_dir: Path) -> None:
    gens = parse_generators(cache_dir / "gen.csv")
    assert [g.name for g in gens] == ["101_STEAM_1", "102_PV_1", "103_NUCLEAR_1"]
    coal, pv, nuc = gens[0], gens[1], gens[2]
    assert coal.bus_id == 101
    assert coal.pmin == 30.0
    assert coal.heat_rate_gj_per_mwh == pytest.approx(10.55)
    assert coal.combustion_tco2_per_gj == pytest.approx(
        210 * LBS_PER_MMBTU_TO_TCO2_PER_GJ
    )
    assert coal.pollutant_combustion["so2"] == pytest.approx(
        0.5 * LBS_PER_MMBTU_TO_TCO2_PER_GJ
    )
    assert coal.pollutant_combustion["nox"] == 0.0  # blank cell
    assert not coal.is_renewable
    assert pv.is_renewable
    assert pv.heat_rate_gj_per_mwh == 0.0
    assert pv.pollutant_combustion == {}
    assert nuc.is_renewable  # grouped with renewables (no combustion CO2)
    assert nuc.heat_rate_gj_per_mwh > 0.0
    assert nuc.combustion_tco2_per_gj == 0.0


def test_parse_gen_row_blank_uid_not_first_column(tmp_path: Path) -> None:
    # GEN UID in a later column: the row-level first-cell guard does not
    # catch the blank name, the row parser must.
    header = ["Bus ID", *[c for c in _GEN_HEADER if c != "Bus ID"]]
    index = {col: i for i, col in enumerate(header)}
    row = ["101", "  ", "CT", "Oil", "10"] + ["0"] * (len(header) - 5)
    assert _parse_gen_row(index, row) is None
    p = tmp_path / "gen.csv"
    _write_csv(p, header, [row])
    assert not parse_generators(p)


def test_parse_buses_with_and_without_bus_id_header(tmp_path: Path) -> None:
    p = tmp_path / "bus.csv"
    _write_csv(p, ["Bus ID", "Name"], [["1", "a"], ["2", "b"]])
    assert parse_buses(p) == [1, 2]
    _write_csv(p, ["Number", "Name"], [["7", "a"], ["", "b"], ["8", "c"]])
    assert parse_buses(p) == [7, 8]


def test_count_csv_rows(tmp_path: Path) -> None:
    assert _count_csv_rows(tmp_path / "missing.csv") == 0
    p = tmp_path / "x.csv"
    _write_csv(p, ["a"], [["1"], [""], ["2"]])
    assert _count_csv_rows(p) == 2


def test_convert(cache_dir: Path) -> None:
    conv = convert(cache_dir, day=3)
    assert conv.day == 3
    assert conv.bus_count == 3
    assert conv.gen_count == 3
    assert conv.line_count == 2
    assert conv.n_hours == 24
    peak = 550.0 * 0.65 * (0.9 + 0.1 * 2 / 9.0)
    assert max(conv.total_load_mw) == pytest.approx(peak)


def test_convert_caps_peak_load(tmp_path: Path) -> None:
    d = tmp_path / "big"
    d.mkdir()
    row = ["G", "1", "WIND", "Wind", "20000", "0", "0", "0"] + [""] * 17
    _write_csv(d / "gen.csv", _GEN_HEADER, [row])
    _write_csv(d / "bus.csv", ["Bus ID"], [["1"]])
    conv = convert(d, day=1)
    assert max(conv.total_load_mw) == pytest.approx(8000.0 * 0.9)
    assert conv.line_count == 0  # no branch.csv


# ── JSON emission edge cases ────────────────────────────────────────────


def _conv(load: list[float], gens: list[RtsGen]) -> Conversion:
    return Conversion(
        day=1,
        n_hours=len(load),
        bus_count=1,
        gen_count=len(gens),
        line_count=0,
        total_load_mw=load,
        generators=gens,
    )


def _gen(**kw: object) -> RtsGen:
    base: dict[str, object] = {
        "name": "g",
        "bus_id": 1,
        "unit_type": "CT",
        "fuel_kind": "Gas",
        "pmax": 10.0,
        "pmin": 0.0,
        "vom": 1.0,
        "fuel_price_per_mmbtu": 1.055,
        "heat_rate_gj_per_mwh": 2.0,
        "combustion_tco2_per_gj": 0.05,
        "pollutant_combustion": {"co2": 0.05, "so2": 0.0},
    }
    base.update(kw)
    return RtsGen(**base)  # type: ignore[arg-type]


def test_to_gtopt_json_without_load_has_no_profile() -> None:
    out = to_gtopt_json(_conv([], []))
    system = out["system"]
    assert system["demand_array"][0]["capacity"] == 1.0
    assert "demand_profile_array" not in system
    assert out["simulation"]["block_array"] == []


def test_to_gtopt_json_zero_peak_has_no_profile() -> None:
    out = to_gtopt_json(_conv([0.0, 0.0], []))
    assert "demand_profile_array" not in out["system"]


def test_to_gtopt_json_skips_zero_factor_pollutants() -> None:
    out = to_gtopt_json(_conv([1.0], [_gen()]), include_pollutants=("co2", "so2"))
    fuel = out["system"]["fuel_array"][0]
    assert fuel["emission_factors"] == [{"emission": 1, "combustion": 0.05}]
    gen = out["system"]["generator_array"][0]
    assert gen["gcost"] == pytest.approx(1.0 + 2.0 * 1.055 / 1.055)
    assert gen["fuel"] == 1
    assert gen["heat_rate"] == 2.0


def test_to_gtopt_json_no_fuel_when_untracked_pollutant_only() -> None:
    gen = _gen(pollutant_combustion={"so2": 0.2})
    out = to_gtopt_json(_conv([1.0], [gen]), include_pollutants=("co2",))
    assert not out["system"]["fuel_array"]
    assert "fuel" not in out["system"]["generator_array"][0]


def test_write_json_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "out.json"
    write_json(target, {"x": 1})
    assert json.loads(target.read_text()) == {"x": 1}


# ── CLI ─────────────────────────────────────────────────────────────────


def test_main_no_download(cache_dir: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "out" / "rts.json"
    rc = cli.main(
        [
            "--no-download",
            "--cache-dir",
            str(cache_dir),
            "-o",
            str(out),
            "--day",
            "2",
            "--pollutants",
            "co2, so2,,",
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["system"]["name"] == "RTSGMLCDay2"
    assert [e["name"] for e in payload["system"]["emission_array"]] == ["co2", "so2"]
    assert "3 gens / 3 buses / 2 lines / 24 h" in capsys.readouterr().out


class _FakeResp(io.BytesIO):
    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_populate_cache_downloads_missing_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "gen.csv").write_text("already here\n")
    (cache / "bus.csv").write_text("")  # empty -> re-downloaded
    urls: list[str] = []

    def fake_urlopen(url: str) -> _FakeResp:
        urls.append(url)
        return _FakeResp(f"payload {url.rsplit('/', 1)[-1]}".encode())

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    cli.populate_cache(cache)
    assert [u.rsplit("/", 1)[-1] for u in urls] == [
        "bus.csv",
        "branch.csv",
        "reserves.csv",
    ]
    assert all(u.startswith(cli.GITHUB_RAW_BASE) for u in urls)
    assert (cache / "gen.csv").read_text() == "already here\n"
    assert (cache / "bus.csv").read_text() == "payload bus.csv"


def test_main_downloads_into_cache(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        cli,
        "populate_cache",
        lambda d, timeseries=False, version=None: calls.append((d, timeseries)),
    )
    out = tmp_path / "rts.json"
    assert cli.main(["--cache-dir", str(cache_dir), "-o", str(out)]) == 0
    assert calls == [(cache_dir, False)]
    assert out.exists()


def test_module_entry_point(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "rts.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["rts_gmlc_to_gtopt", "--no-download", "--cache-dir", str(cache_dir)]
        + ["-o", str(out)],
    )
    monkeypatch.delitem(sys.modules, "rts_gmlc_to_gtopt.__main__", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("rts_gmlc_to_gtopt", run_name="__main__")
    assert exc.value.code == 0
    assert out.exists()
