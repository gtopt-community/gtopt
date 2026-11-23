"""Edge-case / error-path tests for nrel118_to_gtopt (synthetic data only)."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import numpy as np
import pytest

import nrel118_to_gtopt.__main__ as cli
from nrel118_to_gtopt import _converter as conv_mod
from nrel118_to_gtopt import _data
from nrel118_to_gtopt._converter import (
    CaseOptions,
    Conversion,
    HydroBudget,
    NrelBus,
    NrelGen,
    _cell,
    _commitment_entry,
    _distribute_load,
    _parse_generator_row,
    build_case,
    bus_number,
    collapse_piecewise_heat_rate,
    convert,
    parse_buses,
    parse_generators,
    parse_hydro_budgets,
    parse_lines,
    parse_monthly_fixed_mw,
    unit_type_to_fuel,
)
from nrel118_to_gtopt.tests.test_converter import N_HOURS, write_mini_dataset


@pytest.fixture(name="mini")
def fixture_mini(tmp_path: Path) -> Path:
    return write_mini_dataset(tmp_path / "cache")


# ── small helpers ───────────────────────────────────────────────────────


def test_unit_type_to_fuel_nuclear_and_other() -> None:
    assert unit_type_to_fuel("Nuclear 01") == "nuclear"
    assert unit_type_to_fuel("Biomass 02") == "biomass"
    assert unit_type_to_fuel("Geo 01") == "geothermal"
    assert unit_type_to_fuel("Coal ST 01") == "coal"
    assert unit_type_to_fuel("CT NG") == "natural_gas"
    assert unit_type_to_fuel("Mystery 1") == "other"


def test_bus_number_rejects_names_without_digits() -> None:
    assert bus_number("node042") == 42
    with pytest.raises(ValueError, match="cannot parse a bus number"):
        bus_number("busX")


def test_collapse_heat_rate_more_load_points_than_bands() -> None:
    # Second load point has no incremental band -> loop stops.
    hr = collapse_piecewise_heat_rate(100.0, [5000.0], [60.0, 80.0], 40.0, 80.0)
    expected = (100.0 + 5000.0 * 20.0 * 1e-3) / 80.0 * conv_mod.MMBTU_PER_GJ
    assert hr == pytest.approx(expected)


def test_cell_out_of_range_and_unknown_column() -> None:
    header = {"a": 0, "b": 5}
    assert _cell(header, [" x "], "a") == "x"
    assert _cell(header, ["x"], "b") == ""
    assert _cell(header, ["x"], "zzz") == ""


def test_parse_generator_row_skips_blank_and_zero_capacity(tmp_path: Path) -> None:
    header = {"Generator Name": 0, "Max Capacity (MW)": 1, "bus of connection": 2}
    assert _parse_generator_row(header, ["", "10", "bus001"]) is None
    assert _parse_generator_row(header, ["CT NG 01", "0", "bus001"]) is None
    nuc = _parse_generator_row(header, ["Nuclear 01", "100", "bus001"])
    assert nuc is not None
    assert nuc.heat_rate_gj_per_mwh == 0.0
    csv = tmp_path / "Generators.csv"
    csv.write_text("Generator Name,Max Capacity (MW)\nCT NG 01,0\nCT NG 02,5\n")
    assert [g.name for g in parse_generators(csv)] == ["CT NG 02"]


def test_parse_buses_partfact_edge_cases(tmp_path: Path) -> None:
    buses = tmp_path / "Buses.csv"
    buses.write_text("Number,Area\n1,R1\nx,R1\n2,R2\n")
    # without partfact -> no factors
    out = parse_buses(buses, tmp_path / "missing.csv")
    assert [(b.number, b.region, b.load_factor) for b in out] == [
        (1, "R1", 0.0),
        (2, "R2", 0.0),
    ]
    assert parse_buses(buses) == out
    pf = tmp_path / "partfact.csv"
    pf.write_text(
        "Bus Name,Region,Load Participation Factor\n"
        ",R1,0.5\n"  # blank name skipped
        "bus002,,0.4\n"  # blank region keeps the Area
        "bus007,R3,1\n"  # bus only in partfact
    )
    out = parse_buses(buses, pf)
    assert [(b.number, b.region, b.load_factor) for b in out] == [
        (1, "R1", 0.0),
        (2, "R2", 0.4),
        (7, "R3", 1.0),
    ]


def test_parse_lines_missing_file_and_blank_name(tmp_path: Path) -> None:
    assert not parse_lines(tmp_path / "missing.csv")
    p = tmp_path / "Lines.csv"
    p.write_text(
        "Line Name,Bus from ,Bus to,Max Flow (MW),Min Flow (MW)\n"
        ",bus001,bus002,1,-1\n"
        "l1,bus001,bus002,100,\n"
    )
    lines = parse_lines(p)
    assert len(lines) == 1
    assert lines[0].tmax_ab == 100.0
    assert lines[0].tmax_ba == 100.0  # blank Min Flow -> symmetric limit


def test_parse_hydro_budgets_edge_cases(tmp_path: Path) -> None:
    assert not parse_hydro_budgets(tmp_path / "missing.csv")
    p = tmp_path / "118-hydro.csv"
    rows = [
        "short,row",
        "Generators,System,H1,Units,1,,-,1,,,,=",
        "Generators,System,H1,Max Energy Month,abc,,GWh,1,,,M1,=",
        "Generators,System,H1,Max Energy Month,9,,GWh,1,,,,=",  # no month
        "Generators,System,H1,Max Energy Month,0.5,,GWh,1,,,M2,=",
        "Generators,System,H1,Max Capacity,30,,MW,1,,,,=",
        "Generators,System,H1,Max Capacity,20,,MW,1,,,M2,=",
        "Generators,System,H1,Max Capacity,20,,MW,1,,,M13,=",  # bad month
        "Generators,System,H2,Max Capacity,10,,MW,1,,,,=",  # no energy
    ]
    p.write_text("\n".join(rows) + "\n")
    budgets = parse_hydro_budgets(p)
    assert set(budgets) == {"H1"}
    h1 = budgets["H1"]
    assert h1.month_energy_gwh == {2: 0.5}
    assert h1.capacity == 20.0  # M13 is not a month -> base override
    assert h1.month_capacity == {2: 20.0}


def test_parse_monthly_fixed_mw_edge_cases(tmp_path: Path) -> None:
    assert not parse_monthly_fixed_mw(tmp_path / "missing.csv")
    p = tmp_path / "nd.csv"
    p.write_text(
        "Generator,Property,Value,Timeslice\n"
        "H1,Max Capacity,5,M1\n"
        "H1,Fixed Load,3,\n"
        "H1,Fixed Load,4,M3\n"
    )
    assert parse_monthly_fixed_mw(p) == {"H1": {3: 4.0}}


def test_distribute_load_even_split_and_missing_region() -> None:
    series = np.array([30.0, 60.0])
    buses = [NrelBus(1, "R1"), NrelBus(2, "R1"), NrelBus(3, "R2")]
    out = _distribute_load(buses, {"R1": series})
    np.testing.assert_allclose(out[1], series / 2)
    np.testing.assert_allclose(out[2], series / 2)
    assert 3 not in out
    with pytest.raises(ValueError, match="region R9 has no buses"):
        _distribute_load(buses, {"R9": series})


def test_conversion_properties_without_load() -> None:
    conv = Conversion(
        hour_start=0,
        hour_stop=3,
        buses=[NrelBus(1, "R1")],
        lines=[],
        generators=[],
        bus_load_mw={},
        gen_avail_mw={},
        gen_capacity={},
    )
    np.testing.assert_array_equal(conv.total_load_mw, np.zeros(3))
    assert (conv.bus_count, conv.gen_count, conv.line_count) == (1, 0, 0)


def test_commitment_entry_without_ramps_mip() -> None:
    gen = NrelGen(name="CT NG 01", bus="bus001", pmax=50, pmin=60, vom=1, fuel="x")
    uc = _commitment_entry(gen, 4, 50.0, CaseOptions(commitment="mip"), 2)
    assert uc["pmin"] == 50.0  # capped at capacity
    assert "ramp_up" not in uc
    assert "ramp_down" not in uc
    assert "relax" not in uc


# ── convert() data-availability paths ───────────────────────────────────


def test_convert_without_load_files(mini: Path) -> None:
    for region in ("R1", "R2"):
        (mini / f"Load{region}DA.csv").unlink()
    with pytest.raises(FileNotFoundError, match="no LoadR"):
        convert(mini)


def test_convert_rejects_week_zero(mini: Path) -> None:
    with pytest.raises(ValueError, match="week must be >= 1"):
        convert(mini, week=0)


def test_convert_degrades_without_optional_files(mini: Path) -> None:
    (mini / "partfact.csv").unlink()
    (mini / "Wind1DA.csv").unlink()
    (mini / "Lines.csv").unlink()
    conv = convert(mini, renewables_share=0.1)
    assert any("partfact.csv missing" in n for n in conv.notes)
    assert any("Wind 01: profile file missing" in n for n in conv.notes)
    assert "Wind 01" not in conv.gen_avail_mw
    assert conv.line_count == 0
    # evenly split: R1 load over buses 1 and 2
    np.testing.assert_allclose(conv.bus_load_mw[1], conv.bus_load_mw[2])
    assert conv.vg_scale > 0.0


def test_convert_pads_short_profile(mini: Path) -> None:
    (mini / "Solar1DA.csv").write_text('"DATETIME","value"\nh0,5\nh1,7\n')
    conv = convert(mini)
    solar = conv.gen_avail_mw["Solar 01"]
    assert solar.size == N_HOURS
    assert solar[:2].tolist() == [5.0, 7.0]
    assert not solar[2:].any()


def test_convert_hydro_budget_without_base_capacity(mini: Path) -> None:
    hydro = mini / "118-hydro.csv"
    lines = hydro.read_text(encoding="utf-8").splitlines()
    hydro.write_text(
        "\n".join(line for line in lines if "Max Capacity" not in line) + "\n",
        encoding="utf-8",
    )
    conv = convert(mini)
    assert conv.gen_capacity["Hydro 01"] == 500.0  # Generators.csv pmax


def test_convert_zero_vg_energy_scales_to_zero(mini: Path) -> None:
    (mini / "Wind1DA.csv").unlink()
    zero = '"DATETIME","value"\n' + "".join(f"h{k},0\n" for k in range(N_HOURS))
    (mini / "Solar1DA.csv").write_text(zero)
    conv = convert(mini, renewables_share=0.3)
    assert conv.vg_scale == 0.0
    assert conv.gen_capacity["Wind 01"] == 0.0


def test_hydro_budget_month_capacity_caps() -> None:
    budget = HydroBudget(capacity=10.0, month_capacity={1: 2.0})
    budget.month_energy_gwh[1] = 1000.0
    avail = conv_mod.hydro_budget_availability(budget, 10.0, np.ones(24))
    assert avail.max() == pytest.approx(2.0)


# ── build_case() option validation / branches ───────────────────────────


def test_build_case_rejects_bad_options(mini: Path, tmp_path: Path) -> None:
    conv = convert(mini, (0, 24))
    with pytest.raises(ValueError, match="commitment must be one of"):
        build_case(conv, tmp_path / "c.json", CaseOptions(commitment="bogus"))
    with pytest.raises(ValueError, match="fuel_prices must be one of"):
        build_case(conv, tmp_path / "c.json", CaseOptions(fuel_prices="bogus"))


def test_build_case_without_profiles(mini: Path, tmp_path: Path) -> None:
    conv = convert(mini, (0, 24))
    conv.gen_avail_mw.clear()
    planning, summary = build_case(
        conv, tmp_path / "c.json", CaseOptions(output_format="csv")
    )
    assert "capacity_profile_array" not in planning["system"]
    assert summary["profiled_generators"] == 0
    assert planning["options"]["output_format"] == "csv"
    assert not (tmp_path / "c" / "GeneratorProfile").exists()


# ── _data helpers ───────────────────────────────────────────────────────


def test_profile_remote_path_rejects_non_numbered_and_other_kinds() -> None:
    assert _data.profile_remote_path("Wind") is None
    assert _data.profile_remote_path("CT NG 01") is None


class _Resp:
    """Minimal urlopen() response stand-in."""

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b"payload"


def test_download_writes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> _Resp:
        urls.append(url)
        assert timeout == 60
        return _Resp()

    monkeypatch.setattr(_data.urllib.request, "urlopen", fake_urlopen)
    local = tmp_path / "Hydro 16.csv"
    _data._download("Hydro/Hydro 16.csv", local, ref="abc")
    assert local.read_bytes() == b"payload"
    assert not (tmp_path / "Hydro 16.csv.part").exists()
    assert "abc" in urls[0]
    assert urls[0].endswith("Hydro/Hydro%2016.csv")


def test_populate_cache_with_explicit_profile_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched: list[str] = []
    monkeypatch.setattr(
        _data, "_download", lambda remote, local, ref: fetched.append(remote)
    )
    cache = write_mini_dataset(tmp_path / "c")
    n = _data.populate_cache(cache, profile_names=["Wind 02", "CT NG 01"])
    assert fetched[-1] == "Wind/DA/Wind2DA.csv"
    assert n == len(fetched)


def test_read_table_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "e.csv"
    p.write_text("\n\n")
    assert _data.read_table(p) == ({}, [])


def test_profile_generator_names_edge_cases(tmp_path: Path) -> None:
    assert not _data.profile_generator_names(tmp_path / "missing.csv")
    p = tmp_path / "Generators.csv"
    p.write_text("Generator Name,Max\nWind 01,1\n")  # no Rating column
    assert not _data.profile_generator_names(p)
    p.write_text("Generator Name,Rating\n,x\nWind 01\nWind 02,Wind 02\n")
    assert _data.profile_generator_names(p) == ["Wind 02"]


# ── CLI ─────────────────────────────────────────────────────────────────


def _base_args(mini: Path, out: Path) -> list[str]:
    return ["--cache-dir", str(mini), "--no-download", "-o", str(out)]


def test_cli_week_window_and_conflicts(mini: Path, tmp_path: Path) -> None:
    out = tmp_path / "w.json"
    assert cli.main([*_base_args(mini, out), "--week", "1"]) == 1  # 168 h > 48 h
    assert cli.main([*_base_args(mini, out), "--week", "0"]) == 1
    assert cli.main([*_base_args(mini, out), "--week", "1", "--hours", "0+24"]) == 1
    assert not out.exists()


def test_hour_window_week() -> None:
    args = cli.make_parser().parse_args(["--week", "2"])
    assert cli._hour_window(args) == (168, 336)


def test_cli_download_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cache_dir: Path, ref: str) -> int:
        raise OSError("offline")

    monkeypatch.setattr(cli, "populate_cache", boom)
    assert cli.main(["--cache-dir", str(tmp_path / "c")]) == 2


def test_cli_download_then_convert_logs_notes(
    mini: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        cli, "populate_cache", lambda cache_dir, ref: calls.append((cache_dir, ref))
    )
    (mini / "partfact.csv").unlink()
    out = tmp_path / "n.json"
    rc = cli.main(
        ["--cache-dir", str(mini), "-o", str(out), "--hours", "0+24"]
        + ["--stage-hours", "0", "--output-format", "csv"]
    )
    assert rc == 0
    assert calls and calls[0][0] == mini
    assert "partfact.csv missing" in caplog.text
    planning = json.loads(out.read_text())
    assert len(planning["simulation"]["stage_array"]) == 1


def test_cli_from_state_errors(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--from-state", str(tmp_path / "missing.json")])
    assert exc.value.code == 2
    assert "--from-state" in capsys.readouterr().err


def test_cli_from_state_absolute_paths(mini: Path, tmp_path: Path) -> None:
    out = tmp_path / "first" / "abs.json"
    assert cli.main([*_base_args(mini, out), "--hours", "0+24"]) == 0
    state = out.parent / "abs" / "nrel118_to_gtopt_state.json"
    payload = json.loads(state.read_text())
    target = tmp_path / "second" / "abs.json"
    payload["args"]["cache_dir"] = str(mini)  # absolute: kept verbatim
    payload["args"]["output"] = str(target)
    state.write_text(json.dumps(payload))
    assert cli.main(["--from-state", str(state)]) == 0
    assert target.is_file()


def test_write_state_failure_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    import gtopt_shared.state_snapshot as snap_mod  # noqa: PLC0415

    def boom(**_kw: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(snap_mod, "write_state_snapshot", boom)
    args = cli.make_parser().parse_args(["-o", str(tmp_path / "x.json")])
    args.cache_dir = None
    cli._write_state(args, tmp_path / "x", {"buses": 0})
    assert "failed to write state snapshot" in caplog.text


def test_module_entry_point(
    mini: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "m.json"
    monkeypatch.setattr(
        sys, "argv", ["nrel118_to_gtopt", *_base_args(mini, out), "--hours", "0+24"]
    )
    monkeypatch.delitem(sys.modules, "nrel118_to_gtopt.__main__", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("nrel118_to_gtopt", run_name="__main__")
    assert exc.value.code == 0
    assert out.is_file()
