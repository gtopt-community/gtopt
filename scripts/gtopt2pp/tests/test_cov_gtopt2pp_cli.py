"""Coverage tests for gtopt2pp: CLI driver, FieldSched file reads, edge cases."""

import copy
import importlib
import json
import logging
import runpy
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from gtopt2pp.convert import (
    _build_bus_ref_map,
    _build_demand_profile_map,
    _build_gen_profile_map,
    _first_uid,
    _read_file_sched_value,
    _resolve_bus_ref,
    _resolve_field_sched,
    _resolve_field_sched_with_file,
    _uid_to_index,
    convert,
    run_dcopp,
)
from gtopt2pp.main import (
    _log_source_summary,
    _parse_block_spec,
    main,
    run_source_check,
)

pp_main = importlib.import_module("gtopt2pp.main")
pp_convert = importlib.import_module("gtopt2pp.convert")


def _case() -> dict:
    return {
        "options": {},
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
            "scenario_array": [{"uid": 1}],
        },
        "system": {
            "name": "cov",
            "bus_array": [
                {"uid": 1, "name": "b1", "reference_theta": 0},
                {"uid": 2, "name": "b2"},
            ],
            "generator_array": [
                {"uid": 1, "name": "g1", "bus": 1, "pmax": 100, "gcost": 10},
            ],
            "demand_array": [{"uid": 1, "name": "d1", "bus": 2, "lmax": [40, 60]}],
            "line_array": [
                {
                    "uid": 1,
                    "name": "l12",
                    "bus_a": 1,
                    "bus_b": 2,
                    "reactance": 0.1,
                    "tmax_ab": 100,
                    "tmax_ba": 100,
                },
            ],
        },
    }


def _write_case(tmp_path: Path, case: dict | None = None) -> Path:
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case if case is not None else _case()))
    return path


# ── _parse_block_spec / summaries ────────────────────────────────────────


def test_parse_block_spec_mixed():
    assert _parse_block_spec("8, 1,3-5,,3") == [1, 3, 4, 5, 8]
    assert _parse_block_spec("2") == [2]


def test_log_source_summary_reports_skipped(caplog):
    case = _case()
    case["system"]["battery_array"] = [{"uid": 1}]
    case["system"]["junction_array"] = [{"uid": 1}, {"uid": 2}]
    with caplog.at_level(logging.INFO, logger="gtopt2pp.main"):
        _log_source_summary(case)
    assert "1 batteries, 2 junctions" in caplog.text
    assert "System name     : cov" in caplog.text


def test_log_source_summary_empty_case(caplog):
    with caplog.at_level(logging.INFO, logger="gtopt2pp.main"):
        _log_source_summary({})
    assert "(unnamed)" in caplog.text
    assert "Skipped" not in caplog.text


# ── run_source_check ─────────────────────────────────────────────────────


def test_run_source_check_findings_by_severity(monkeypatch, caplog):
    checks = importlib.import_module("gtopt_check_json._checks")
    info = importlib.import_module("gtopt_check_json._info")
    sev = checks.Severity
    findings = [
        types.SimpleNamespace(severity=sev.CRITICAL, check_id="c1", message="bad"),
        types.SimpleNamespace(severity=sev.WARNING, check_id="w1", message="meh"),
        types.SimpleNamespace(severity=sev.NOTE, check_id="n1", message="fyi"),
    ]
    monkeypatch.setattr(checks, "run_all_checks", lambda *a, **k: findings)
    monkeypatch.setattr(info, "format_info", lambda case: "line one\nline two")
    with caplog.at_level(logging.INFO, logger="gtopt2pp.main"):
        run_source_check(_case())
    assert "[CRITICAL] (c1) bad" in caplog.text
    assert "[WARNING] (w1) meh" in caplog.text
    assert "[NOTE] (n1) fyi" in caplog.text
    assert "1 critical, 1 warnings, 1 notes" in caplog.text
    assert "line two" in caplog.text


def test_run_source_check_no_findings(monkeypatch, caplog):
    checks = importlib.import_module("gtopt_check_json._checks")
    monkeypatch.setattr(checks, "run_all_checks", lambda *a, **k: [])
    with caplog.at_level(logging.INFO, logger="gtopt2pp.main"):
        run_source_check(_case())
    assert "all checks passed" in caplog.text


def test_run_source_check_without_check_json(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "gtopt_check_json._info", None)
    with caplog.at_level(logging.DEBUG, logger="gtopt2pp.main"):
        run_source_check(_case())
    assert "gtopt_check_json not available" in caplog.text


# ── main() ───────────────────────────────────────────────────────────────


def test_main_default_single_block_output(tmp_path, capsys):
    path = _write_case(tmp_path)
    assert main([str(path)]) == 0
    out = tmp_path / "case_pp.json"
    assert out.exists()
    assert f"Written: {out}" in capsys.readouterr().out


def test_main_empty_block_array_defaults_to_uid_1(tmp_path):
    case = _case()
    case["simulation"]["block_array"] = [{"uid": 1, "duration": 1}]
    path = _write_case(tmp_path, case)
    out = tmp_path / "x.json"
    assert main(["--no-check", "-o", str(out), str(path)]) == 0
    assert out.exists()


def test_main_no_blocks_falls_back_to_block_1(tmp_path, monkeypatch):
    """An empty block_array makes main() ask convert() for block UID 1."""
    case = _case()
    case["simulation"]["block_array"] = []
    path = _write_case(tmp_path, case)
    seen = []

    def fake_convert(case, scenario=None, block=None):
        seen.append(block)
        return pp_convert.convert(_case(), scenario=scenario, block=block)

    monkeypatch.setattr(pp_main, "convert", fake_convert)
    assert main(["--no-check", str(path)]) == 0
    assert seen == [1]


def test_main_all_blocks_default_names(tmp_path, capsys):
    path = _write_case(tmp_path)
    assert main(["--no-check", "--all-blocks", "--diagnostic", str(path)]) == 0
    out = capsys.readouterr().out
    for uid in (1, 2):
        assert (tmp_path / f"case_pp_b{uid}.json").exists()
        assert f"Block {uid}: written" in out
    assert "pandapower diagnostic (block 2)" in out


def test_main_block_range_with_output(tmp_path):
    path = _write_case(tmp_path)
    out = tmp_path / "net.json"
    assert main(["--no-check", "-b", "1-2", "-o", str(out), str(path)]) == 0
    assert (tmp_path / "net_b1.json").exists()
    assert (tmp_path / "net_b2.json").exists()
    assert not out.exists()


def test_main_solve_warns_and_writes(tmp_path, monkeypatch, capsys):
    """--solve runs only the first block (DC OPF mocked)."""
    path = _write_case(tmp_path)
    calls = []

    def fake_run_dcopp(case, scenario=None, block=None):
        calls.append(block)
        net = pp_convert.convert(case, scenario=scenario, block=block)
        net.OPF_converged = True
        net.res_cost = 12.5
        return net

    monkeypatch.setattr(pp_main, "run_dcopp", fake_run_dcopp)
    assert main(["--no-check", "--solve", "-b", "2,1", str(path)]) == 0
    captured = capsys.readouterr()
    assert calls == [1]
    assert "ignoring blocks [2]" in captured.err
    assert "Objective: 12.5000" in captured.out
    assert (tmp_path / "case_pp_solved.json").exists()


def test_main_solve_single_block_explicit_output(tmp_path, monkeypatch, capsys):
    path = _write_case(tmp_path)

    def fake_run_dcopp(case, scenario=None, block=None):
        net = pp_convert.convert(case, scenario=scenario, block=block)
        net.OPF_converged = True
        net.res_cost = 1.0
        return net

    monkeypatch.setattr(pp_main, "run_dcopp", fake_run_dcopp)
    out = tmp_path / "solved.json"
    assert main(["--no-check", "--solve", "-o", str(out), str(path)]) == 0
    assert out.exists()
    assert "ignoring" not in capsys.readouterr().err


def test_module_entry_point(tmp_path, monkeypatch):
    path = _write_case(tmp_path)
    monkeypatch.setattr(sys, "argv", ["gtopt2pp", "--no-check", str(path)])
    sys.modules.pop("gtopt2pp.main", None)
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("gtopt2pp.main", run_name="__main__")
    finally:
        sys.modules["gtopt2pp.main"] = pp_main
    assert exc.value.code == 0


# ── run_dcopp ────────────────────────────────────────────────────────────


def test_run_dcopp_not_converged_raises(monkeypatch):
    def fake_rundcopp(net):
        net.OPF_converged = False

    monkeypatch.setattr(pp_convert.pp, "rundcopp", fake_rundcopp)
    with pytest.raises(RuntimeError, match="failed to converge"):
        run_dcopp(_case(), scenario=1, block=1)


def test_run_dcopp_converged(monkeypatch):
    def fake_rundcopp(net):
        net.OPF_converged = True

    monkeypatch.setattr(pp_convert.pp, "rundcopp", fake_rundcopp)
    assert run_dcopp(_case()).OPF_converged


# ── helpers ──────────────────────────────────────────────────────────────


def test_bus_ref_map_skips_missing_uid_and_name():
    ref = _build_bus_ref_map({"bus_array": [{"name": "only_name"}, {"uid": 7}]})
    assert ref == {"only_name": 0, 7: 1}


def test_first_uid_errors():
    with pytest.raises(ValueError, match="No block defined"):
        _first_uid([], "block")
    with pytest.raises(ValueError, match="First block has no uid"):
        _first_uid([{"duration": 1}], "block")


def test_uid_to_index_missing():
    with pytest.raises(ValueError, match="scenario UID 9 not found"):
        _uid_to_index(9, [{"uid": 1}], "scenario")


def test_resolve_field_sched_edge_cases():
    assert _resolve_field_sched([], 0, 0) is None
    assert _resolve_field_sched([[1.0]], 3, 0) is None  # scenario out of range
    assert _resolve_field_sched([[1.0]], 0, 5) is None  # block out of range
    assert _resolve_field_sched({"x": 1}, 0, 0) is None  # unsupported type


def test_resolve_field_sched_with_file_paths(tmp_path):
    args = (0, 0, None, "input", "Generator", 1, "g1", 1, 1)
    assert _resolve_field_sched_with_file(None, *args) is None
    assert _resolve_field_sched_with_file(3, *args) == 3.0
    assert _resolve_field_sched_with_file([4.0], *args) == 4.0
    assert _resolve_field_sched_with_file("pmax", *args) is None  # no case_dir
    assert _resolve_field_sched_with_file({"a": 1}, *args) is None


def test_resolve_bus_ref_variants():
    ref = {1: 0, "b2": 1}
    assert _resolve_bus_ref(None, ref) is None
    assert _resolve_bus_ref("1", ref) == 0
    assert _resolve_bus_ref("7", ref) is None
    assert _resolve_bus_ref("nope", ref) is None
    assert _resolve_bus_ref(3.5, ref) is None


def test_profile_maps_name_refs_and_missing_profiles():
    system = {
        "generator_array": [{"uid": 5, "name": "g5"}],
        "generator_profile_array": [
            {"generator": "g5", "profile": [0.5, 0.7]},
            {"generator": "unknown", "profile": 0.9},
            {"generator": 6},  # no profile -> skipped
            {"generator": 7, "profile": "file_ref"},  # unresolved -> skipped
        ],
        "demand_profile_array": [
            {"demand": 1},
            {"demand": 2, "profile": "file_ref"},
            {"demand": 3, "profile": [0.25]},
        ],
    }
    assert _build_gen_profile_map(system, 0, 1) == {5: 0.7, 0: 0.9}
    assert _build_demand_profile_map(system, 0, 0) == {3: 0.25}


# ── _read_file_sched_value ───────────────────────────────────────────────


def _read(tmp_path, field, **kw):
    params = {
        "case_dir": str(tmp_path),
        "input_dir": "input",
        "class_name": "Generator",
        "field_name": field,
        "element_uid": 1,
        "element_name": "g1",
        "scenario_uid": 1,
        "block_uid": 2,
        "preferred_format": "parquet",
    }
    params.update(kw)
    return _read_file_sched_value(**params)


def _gen_dir(tmp_path: Path, cls: str = "Generator") -> Path:
    d = tmp_path / "input" / cls
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_read_file_missing_returns_none(tmp_path):
    assert _read(tmp_path, "pmax") is None


def test_read_file_long_layout(tmp_path):
    pd.DataFrame(
        {
            "scenario": [1, 1, 1],
            "block": [1, 2, 2],
            "uid": [1, 1, 2],
            "value": [10.0, 20.0, 30.0],
        }
    ).to_parquet(_gen_dir(tmp_path) / "pmax.parquet")
    assert _read(tmp_path, "pmax") == 20.0
    # block filter eliminates everything -> fall back to the first uid row
    assert _read(tmp_path, "pmax", block_uid=9) == 10.0
    # unknown uid -> None
    assert _read(tmp_path, "pmax", element_uid=99) is None
    # no scenario/block filter
    assert _read(tmp_path, "pmax", scenario_uid=None, block_uid=None) == 10.0


def test_read_file_wide_csv_with_at_reference(tmp_path):
    """`Class@field` points at another class dir; csv preferred format."""
    pd.DataFrame({"scenario": [1, 1], "block": [1, 2], "uid:1": [5.0, 6.0]}).to_csv(
        _gen_dir(tmp_path, "Other") / "prof.csv", index=False
    )
    assert _read(tmp_path, "Other@prof", preferred_format="csv") == 6.0


def test_read_file_wide_name_and_bare_uid_columns(tmp_path):
    d = _gen_dir(tmp_path)
    pd.DataFrame({"block": [1, 2], "g1": [7.0, 8.0]}).to_parquet(d / "a.parquet")
    assert _read(tmp_path, "a") == 8.0
    pd.DataFrame({"block": [1, 2], "1": [3.0, 4.0]}).to_parquet(d / "b.parquet")
    assert _read(tmp_path, "b", element_name=None) == 4.0
    pd.DataFrame({"block": [1], "other": [1.0]}).to_parquet(d / "c.parquet")
    assert _read(tmp_path, "c") is None
    assert _read(tmp_path, "c", element_uid=None, element_name=None) is None


def test_read_file_wide_filter_empty_fallbacks(tmp_path):
    d = _gen_dir(tmp_path)
    pd.DataFrame({"block": [1], "uid:1": [9.0]}).to_parquet(d / "f.parquet")
    assert _read(tmp_path, "f", block_uid=5) == 9.0  # falls back to first row
    pd.DataFrame({"block": pd.Series([], dtype="int64"), "uid:1": []}).to_parquet(
        d / "e.parquet"
    )
    assert _read(tmp_path, "e") is None


def test_read_file_unreadable_falls_through_to_next_format(tmp_path):
    d = _gen_dir(tmp_path)
    (d / "pmax.parquet").write_bytes(b"garbage")
    pd.DataFrame({"block": [2], "uid:1": [42.0]}).to_csv(d / "pmax.csv", index=False)
    assert _read(tmp_path, "pmax") == 42.0


# ── convert() edge cases ─────────────────────────────────────────────────


def test_convert_reads_file_sched_from_case_dir(tmp_path):
    d = _gen_dir(tmp_path)
    pd.DataFrame({"block": [1, 2], "uid": [1, 1], "value": [70.0, 80.0]}).to_parquet(
        d / "pmax.parquet"
    )
    case = _case()
    case["_case_dir"] = str(tmp_path)
    case["system"]["generator_array"][0]["pmax"] = "pmax"
    net = convert(case, block=2)
    assert float(net.ext_grid["max_p_mw"].iloc[0]) == pytest.approx(80.0)


def test_convert_skips_unresolvable_elements_and_uses_capacity():
    case = _case()
    system = case["system"]
    system["generator_array"].append({"uid": 2, "bus": 99, "pmax": 5})
    system["generator_array"].append({"uid": 3, "bus": 2, "capacity": 30})
    system["demand_array"].append({"uid": 2, "bus": "nowhere", "lmax": 5})
    system["line_array"].append({"uid": 2, "bus_a": 1, "bus_b": 42})
    net = convert(case)
    assert len(net.gen) == 1
    assert float(net.gen["max_p_mw"].iloc[0]) == pytest.approx(30.0)
    assert float(net.gen["min_p_mw"].iloc[0]) == pytest.approx(0.0)
    assert len(net.load) == 1
    assert len(net.line) == 1


def test_convert_generator_without_pmax_or_capacity():
    case = _case()
    del case["system"]["generator_array"][0]["pmax"]
    net = convert(case)
    assert float(net.ext_grid["max_p_mw"].iloc[0]) == pytest.approx(0.0)


def test_convert_no_buses_and_no_generators():
    case = _case()
    case["system"] = {"name": "empty"}
    net = convert(case)
    assert len(net.bus) == 0
    assert len(net.ext_grid) == 0


def test_convert_no_reference_no_generators_first_bus_slack():
    case = _case()
    del case["system"]["bus_array"][0]["reference_theta"]
    case["system"]["generator_array"] = []
    net = convert(case)
    assert len(net.ext_grid) == 1
    assert net.ext_grid["name"].iloc[0] == "slack"


def test_convert_phase_shift_and_tap_make_transformer():
    case = _case()
    line = case["system"]["line_array"][0]
    line["phase_shift_deg"] = 10.0
    line["tap_ratio"] = 2.0
    case["system"]["bus_array"][0]["voltage"] = 110.0
    case["system"]["bus_array"][1]["voltage"] = 220.0  # bus_b is HV
    net = convert(case)
    assert len(net.line) == 0
    assert len(net.trafo) == 1
    trafo = net.trafo.iloc[0]
    assert float(trafo["shift_degree"]) == pytest.approx(10.0)
    assert int(trafo["hv_bus"]) == 1
    assert float(trafo["vk_percent"]) == pytest.approx(20.0)


def test_convert_unlimited_line_without_thermal_limits():
    case = _case()
    line = case["system"]["line_array"][0]
    for key in ("tmax_ab", "tmax_ba", "reactance", "name"):
        del line[key]
    net = convert(case)
    assert float(net.line["max_i_ka"].iloc[0]) == pytest.approx(9999.0)
    assert net.line["name"].iloc[0] == "l1_2"


def test_convert_multiple_scenarios_by_uid():
    case = copy.deepcopy(_case())
    case["simulation"]["scenario_array"] = [{"uid": 3}, {"uid": 4}]
    case["system"]["demand_array"][0]["lmax"] = [[10, 11], [20, 21]]
    net = convert(case, scenario=4, block=2)
    assert float(net.load["p_mw"].iloc[0]) == pytest.approx(21.0)


def test_read_file_wide_without_block_filter(tmp_path):
    d = _gen_dir(tmp_path)
    pd.DataFrame({"uid:1": [11.0, 12.0]}).to_parquet(d / "g.parquet")
    assert _read(tmp_path, "g", block_uid=None) == 11.0


def test_main_empty_block_spec_writes_nothing(tmp_path, capsys):
    """`-b ,` selects no block: nothing is converted or written."""
    path = _write_case(tmp_path)
    assert main(["--no-check", "-b", ",", str(path)]) == 0
    assert "written" not in capsys.readouterr().out
    assert not list(tmp_path.glob("case_pp*.json"))
