"""Branch-coverage tests for :mod:`plexos2gtopt.uc_audit`.

Small synthetic PLEXOS-cache / planning-JSON fixtures exercising the
defensive skips, the CLI and the less common audit buckets.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plexos2gtopt import uc_audit as ua

# ---------------------------------------------------------------------------
# Fixture writers
# ---------------------------------------------------------------------------


def _csv(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n")


def _constraint_cache(
    cache: Path,
    constraints: dict[str, dict[int, list[float]]],
    *,
    junk: bool = False,
) -> None:
    """PLEXOS sol cache with one Constraint object per name.

    ``junk`` appends one malformed row to every table (skipped by the reader).
    """
    obj = []
    mem = []
    key = []
    data = []
    kid = 5000
    for i, (name, props) in enumerate(constraints.items()):
        oid = 100 + i
        mid = 1000 + i
        obj.append(f"{oid},{name},{ua.CONSTRAINT_CLASS_ID}")
        mem.append(f"{mid},{ua.SYS_CONSTRAINT_COLLECTION_ID},70,{oid}")
        for pid, series in props.items():
            kid += 1
            key.append(f"{kid},{mid},{pid}")
            data.extend(f"{kid},{v}" for v in series)
    if junk:
        obj.append("x,bad,y")
        mem.append("x,y,z,w")
        key.append("x,y,z")
        data.append("x,y")
    _csv(cache / "t_object.csv", "object_id,name,class_id", obj)
    _csv(
        cache / "t_membership.csv",
        "membership_id,collection_id,child_class_id,child_object_id",
        mem,
    )
    _csv(cache / "t_key.csv", "key_id,membership_id,property_id", key)
    _csv(cache / "t_data_0.csv", "key_id,value", data)


def _plan(
    path: Path, system: dict[str, Any], options: dict[str, Any] | None = None
) -> Path:
    payload: dict[str, Any] = {"system": system}
    if options is not None:
        payload["options"] = options
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _long_csv(path: Path, rows: dict[str, list[float]], day: int = 15) -> None:
    lines = []
    for name, series in rows.items():
        for h, v in enumerate(series, start=1):
            lines.append(f"{name},2026,2,{day},{h},1,{v}")
    _csv(path, "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE", lines)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_parse_pampl_and_json_without_operator(tmp_path: Path) -> None:
    p = tmp_path / "uc_a.pampl"
    p.write_text('constraint NoOp: 2 * x("A") - - 3 * y("B");\n')
    rows = ua.parse_pampl_file(p)
    assert rows[0]["op"] == "?"
    assert rows[0]["rhs_scalar"] is None
    assert [t["coeff"] for t in rows[0]["terms"]] == [2.0, 3.0]

    plan = _plan(
        tmp_path / "p.json",
        {"user_constraint_array": [{"name": "J", "expression": 'x("A")'}]},
    )
    assert ua.parse_json_ucs(plan)[0]["op"] == "?"


def test_missing_planning_json_degrades(tmp_path: Path) -> None:
    nope = tmp_path / "nope.json"
    assert not ua._reserve_zone_uid_to_name(nope)
    assert not ua.reserve_zone_requirement_series(nope)
    assert ua._parse_gtopt_bounds(nope) == ([], [])
    assert not ua._parse_gtopt_provisions(nope)
    assert not ua.build_b13_profile_collapse(nope, tmp_path)
    assert not ua._parse_gtopt_reservoir_bounds(nope)
    assert not ua.build_b14_reservoir_bounds(nope, tmp_path)
    assert not ua.build_b15_fuel_price(nope, tmp_path)
    assert not ua._model_options(nope)
    assert not ua._commitment_pmin_generators(nope)
    assert not ua.build_b17_soft_slack(nope, None)
    with pytest.raises(FileNotFoundError):
        ua.load_hard_list(nope)


def test_nameless_entries_are_skipped(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path / "p.json",
        {
            "reserve_zone_array": [
                {"uid": "x", "name": "bad"},
                {"uid": 2},
                {"urreq": [1.0]},
                {"uid": 3, "name": "Z", "urreq": [[1.0, "x", None]], "drreq": []},
            ],
            "generator_array": [{"pmax": 1.0}],
            "line_array": [{"tmax_ab": 1.0}],
            "reserve_provision_array": [{"urmax": 1.0}],
            "reservoir_array": [{"emax": 1.0}],
            "commitment_array": [{"pmin": 1.0}, {"generator": "G", "pmin": 5.0}],
        },
    )
    assert ua._reserve_zone_uid_to_name(plan) == {3: "Z"}
    assert ua.reserve_zone_requirement_series(plan) == {("Z", "up"): [1.0]}
    gens, lines = ua._parse_gtopt_bounds(plan)
    assert not gens and not lines
    assert not ua._parse_gtopt_provisions(plan)
    assert not ua._parse_gtopt_reservoir_bounds(plan)
    assert ua._commitment_pmin_generators(plan) == {"G"}


def test_field_peak_skips_non_numeric() -> None:
    assert ua._field_peak([[1.0, "x"], [3.0]]) == 3.0


def test_bounds_item_non_numeric() -> None:
    assert ua._bounds_item("g", "pmax", "abc", 1.0) is None
    assert ua._bounds_item("g", "pmax", None, 1.0) is None


# ---------------------------------------------------------------------------
# B12 / B13 / B14 (input-based)
# ---------------------------------------------------------------------------


def test_b12_pmin_mismatch_and_non_numeric_pmax(tmp_path: Path) -> None:
    inp = tmp_path / "in"
    _long_csv(inp / "Gen_Rating.csv", {"G1": [100.0], "G2": [100.0]})
    _long_csv(inp / "Gen_MinStableLevel.csv", {"G1": [40.0]})
    _long_csv(inp / "Gen_UnitsOut.csv", {"G2": [1.0]})
    _long_csv(inp / "Lin_MaxRating.csv", {"L1": [50.0]})
    plan = _plan(
        tmp_path / "p.json",
        {
            "generator_array": [
                {"name": "G1", "pmin": 10.0, "pmax": 100.0},
                {"name": "G2", "pmin": 0.0, "pmax": "n/a"},
            ],
            "line_array": [{"name": "L1", "tmax_ab": 50.0, "tmax_ba": 50.0}],
        },
    )
    items = ua.build_b12_bounds(plan, inp)
    assert {(i["name"], i["field"]) for i in items} == {("G1", "pmin")}


def test_b13_skips_nameless_and_zero_capacity(tmp_path: Path) -> None:
    inp = tmp_path / "in"
    _long_csv(inp / "Gen_Rating.csv", {"B1": [10.0, 20.0], "B2": [10.0, 20.0]})
    plan = _plan(
        tmp_path / "p.json",
        {
            "battery_array": [
                {"pmax_discharge": 20.0},
                {"name": "B1", "pmax_discharge": 0.0},
                {"name": "B2", "pmax_discharge": "x"},
            ],
            "generator_array": [{"name": "B1", "pmax": 0.0}],
        },
    )
    assert not ua.build_b13_profile_collapse(plan, inp)


def _wide(path: Path, col: str, per_hour: list[float]) -> None:
    rows = [f"2025,10,19,{h},{v}" for h, v in enumerate(per_hour, start=1)]
    _csv(path, "YEAR,MONTH,DAY,PERIOD," + col, rows)


def test_b14_emin_collapse_and_emax_above_input(tmp_path: Path) -> None:
    inp = tmp_path / "in"
    _wide(inp / "Hydro_MaxVolume.csv", "R1", [100.0, 100.0])
    _wide(inp / "Hydro_MinVolume.csv", "R1", [1.0, 5.0])
    plan = _plan(
        tmp_path / "p.json",
        {"reservoir_array": [{"name": "R1", "emin": 1.0, "emax": 200.0}]},
    )
    items = ua.build_b14_reservoir_bounds(plan, inp)
    assert [i["field"] for i in items] == ["reservoir.emin"]


def test_b14_without_emax_input(tmp_path: Path) -> None:
    inp = tmp_path / "in"
    _wide(inp / "Hydro_MinVolume.csv", "R1", [1.0, 1.0])
    plan = _plan(tmp_path / "p.json", {"reservoir_array": [{"name": "R1"}]})
    assert not ua.build_b14_reservoir_bounds(plan, inp)


# ---------------------------------------------------------------------------
# Solution-cache loaders with malformed rows
# ---------------------------------------------------------------------------


def test_build_plexos_solution_skips_malformed_rows(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _constraint_cache(cache, {"C1": {ua.PROP_RHS: [5.0]}}, junk=True)
    out = ua.build_plexos_solution(cache)
    assert out["C1"]["rhs_max"] == 5.0


def test_load_plexos_reserve_softness_malformed(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    obj = [f"1,R1,{ua.RESERVE_CLASS_ID}", "x,bad,y"]
    mem = [f"10,{ua.SYS_RESERVE_COLLECTION_ID},{ua.RESERVE_CLASS_ID},1", "x,y,z,w"]
    key = [
        f"100,10,{ua.RESERVE_PROP_SHORTAGE}",
        f"101,10,{ua.RESERVE_PROP_PRICE}",
        "102,10,9999",
        "103,99,9999",
        "x,y,z",
    ]
    data = ["100,2.0", "101,50.0", "102,7.0", "x,y"]
    _csv(cache / "t_object.csv", "object_id,name,class_id", obj)
    _csv(
        cache / "t_membership.csv",
        "membership_id,collection_id,child_class_id,child_object_id",
        mem,
    )
    _csv(cache / "t_key.csv", "key_id,membership_id,property_id", key)
    _csv(cache / "t_data_0.csv", "key_id,value", data)
    out = ua.load_plexos_reserve_softness(cache)
    assert out["shortage_present"] is True
    assert out["price_max"] == 50.0
    assert out["shortage_max"] == 2.0


def test_storage_series_from_cache_malformed(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    pid = ua.STORAGE_PROP_END_VOLUME
    sc = ua.STORAGE_COLLECTION_ID
    _csv(cache / "t_object.csv", "object_id,name,class_id", ["1,R1,8", "x,bad,8"])
    _csv(
        cache / "t_membership.csv",
        "membership_id,collection_id,child_object_id",
        [f"10,{sc},1", f"11,{sc},2", "x,y,z"],
    )
    _csv(
        cache / "t_key.csv",
        "key_id,membership_id,property_id",
        [f"100,10,{pid}", f"101,11,{pid}", "x,y,z"],
    )
    _csv(
        cache / "t_data_0.csv",
        "key_id,period_id,value",
        ["100,1,5.0", "100,x,6.0", "100,3,bad", "x,1,1", "101,1,1.0", "999,1,1"],
    )
    out = ua._storage_series_from_cache(cache, (pid,))
    assert out["R1"][pid] == [5.0, 6.0]
    assert not ua._storage_series_from_cache(tmp_path / "missing", (pid,))


def test_load_plexos_reserve_vors_from_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plexos2gtopt.parsers as parsers_mod  # noqa: PLC0415
    import plexos2gtopt.plexos_xml as xml_mod  # noqa: PLC0415

    xml = tmp_path / "m.xml"
    xml.write_text("<x/>")
    reserves = [SimpleNamespace(object_id=i, name=f"R{i}") for i in range(3)]
    db = SimpleNamespace(objects_of_class=lambda _cls: reserves)
    monkeypatch.setattr(xml_mod, "load_xml", lambda _p: db)
    vals = {0: (-1.0, "VoRS"), 1: (250.0, "Violation Cost"), 2: (0.0, None)}
    monkeypatch.setattr(
        parsers_mod, "probe_reserve_violation_cost", lambda _db, oid: vals[oid]
    )
    out = ua.load_plexos_reserve_vors(xml)
    assert [out[f"R{i}"]["interpreted"] for i in range(3)] == [
        "default_sentinel",
        "explicit_cost",
        "no_penalty",
    ]

    def _boom(_p: Path) -> None:
        raise OSError("unreadable")

    monkeypatch.setattr(xml_mod, "load_xml", _boom)
    assert not ua.load_plexos_reserve_vors(xml)


# ---------------------------------------------------------------------------
# Series helpers
# ---------------------------------------------------------------------------


def test_series_helpers() -> None:
    assert ua._emitted_block_series(None, 3) is None
    assert ua._emitted_block_series("x", 3) is None
    assert ua._emitted_block_series([["a", 2.0]], 3) == [2.0]
    assert ua._trim_trailing_zeros([1.0, 0.0, 0.0]) == [1.0]
    assert not ua._resample_to([], 3)
    assert ua._resample_to([1.0, 2.0], 4) == [1.0, 1.0, 2.0, 2.0]
    assert ua._distinct_set_mismatch([], [1.0]) is None
    assert ua._profile_block_diff([], [1.0]) is None
    # worst block keeps the FIRST largest difference
    diff = ua._profile_block_diff([1.0, 1.0, 5.0], [9.0, 2.0, 5.0])
    assert diff is not None and diff[2] == 0


# ---------------------------------------------------------------------------
# B14 sol / B15 / B16
# ---------------------------------------------------------------------------


def test_b14_sol_skips_and_emax_violation(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path / "p.json",
        {
            "reservoir_array": [
                {"name": "NOSOL", "emax": 10.0},
                {"name": "EMPTY", "emax": 10.0},
                {"name": "R1", "emax": 10.0},
                {"name": "R2", "emin": 1.0, "emax": 10.0},
            ]
        },
    )
    storages = {
        "EMPTY": {ua.STORAGE_PROP_SHADOW_PRICE: [1.0]},
        "R1": {ua.STORAGE_PROP_END_VOLUME: [5.0, 20.0, 30.0]},
        "R2": {ua.STORAGE_PROP_END_VOLUME: [5.0, 6.0]},
    }
    items = ua.build_b14_reservoir_bounds_sol(plan, storages)
    viol = [i for i in items if i["name"] == "R1"]
    assert viol and viol[0]["kind"] == "bound_violated_in_sol"
    assert not [i for i in items if i["name"] == "R2"]


def test_b15_skips_and_empty_input(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path / "p.json",
        {"fuel_array": [{"price": 1.0}, {"name": "NOPE", "price": 1.0}]},
    )
    assert not ua.build_b15_fuel_price(plan, None, {"GAS": [1.0, 2.0]})
    inp = tmp_path / "in"
    _csv(inp / "Fuel_Price.csv", "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE", [])
    assert not ua.build_b15_fuel_price(plan, inp)


def test_b15_match_scalar_series(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "p.json", {"fuel_array": [{"name": "GAS", "price": 3.0}]})
    assert not ua.build_b15_fuel_price(plan, None, {"GAS": [3.0, 3.0]})


def test_b16_skips(tmp_path: Path) -> None:
    inp = tmp_path / "in"
    _csv(inp / "Hydro_EfficiencyIncr.csv", "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE", [])
    plan = _plan(tmp_path / "p.json", {"turbine_array": []})
    assert not ua.build_b16_turbine_pf(plan, inp)

    _long_csv(inp / "Hydro_EfficiencyIncr.csv", {"G1": [2.0, 2.0]})
    plan = _plan(
        tmp_path / "p.json",
        {
            "turbine_array": [
                {"generator": "G1", "production_factor": 2.0},
                {"name": "T0", "production_factor": 2.0},
                {"name": "T2", "generator": "G2", "production_factor": 2.0},
                {"name": "T1", "generator": "G1", "production_factor": 2.0},
            ]
        },
    )
    assert not ua.build_b16_turbine_pf(plan, inp)


# ---------------------------------------------------------------------------
# B17
# ---------------------------------------------------------------------------


def test_b17_bad_reserve_cost_and_missing_gens(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path / "p.json",
        {
            "commitment_array": [{"generator": "GHOST", "pmin": 5.0}],
            "generator_array": [],
            "waterway_array": [{"fmin": 1.0}, {"name": "W", "fmin": 0.0}],
        },
        options={"model_options": {"reserve_shortage_cost": "abc"}},
    )
    items = ua.build_b17_soft_slack(plan, None)
    assert isinstance(items, list)


# ---------------------------------------------------------------------------
# B11 native comparison
# ---------------------------------------------------------------------------


def _p(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rhs_n": 2,
        "price_sum_abs": 1.0,
        "hours_binding_sum": 1.0,
        "rhs_values": [1.0, 2.0],
    }
    base.update(kw)
    return base


def test_native_sources() -> None:
    assert ua._commitment_plexos_source("abc", {"OTHER": {}}) is None
    assert ua._reserve_zone_plexos_source("ZONE", "up", {}) is None
    assert ua._reserve_zone_plexos_source("CPF_RS", "down", {}) is None


def test_compare_native_element_paths() -> None:
    bess = ua._compare_native_element("reserve_zone", "CPF_RS_BESS", "up", [1.0], {})
    assert bess is not None and "provision-only" in bess["deferred"]
    plexos = {"gmax": _p(rhs_n=0)}
    nrhs = ua._compare_native_element("commitment", "g", "up", [1.0], plexos)
    assert nrhs is not None and "no RHS" in nrhs["deferred"]
    plexos = {"gmax": _p(price_sum_abs=0.0)}
    assert ua._compare_native_element("commitment", "g", "up", [1.0], plexos) is None
    plexos = {"gmax": _p(rhs_values=[10000.0])}
    assert ua._compare_native_element("commitment", "g", "up", [1.0], plexos) is None


def test_build_b11_skips_reserve_without_requirement() -> None:
    native = {("reserve_zone", "CPF_RS", "up"): [1.0]}
    b11, deferred = ua.build_b11_native_rhs(native, {}, {})
    assert not b11 and not deferred


# ---------------------------------------------------------------------------
# run_audit buckets + CLI
# ---------------------------------------------------------------------------


def _audit_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    act = {ua.PROP_ACTIVITY: [3.0], ua.PROP_PRICE: [2.0], ua.PROP_RHS: [5.0]}
    constraints: dict[str, dict[int, list[float]]] = {
        "SoftResv": dict(act),
        "SoftOp": dict(act),
        "NegPen": dict(act),
        "Inact": dict(act),
    }
    for i in range(55):
        constraints[f"Zzz{i:02d}"] = {ua.PROP_RHS: [1.0]}
    cache = tmp_path / "cache"
    _constraint_cache(cache, constraints)
    gdir = tmp_path / "gtopt"
    _plan(
        gdir / "case.json",
        {
            "user_constraint_array": [
                {"name": "SoftResv", "expression": "x <= 5", "penalty": 5000.0},
                {"name": "SoftOp", "expression": "x <= 5", "penalty": 10.0},
                {"name": "NegPen", "expression": "x <= 5", "penalty": -1.0},
                {"name": "Inact", "expression": "x <= 5", "active": False},
                {"name": "Synth", "expression": "x <= 5"},
            ]
        },
    )
    (gdir / "case.provenance.json").write_text("{}")
    hard = tmp_path / "hard.txt"
    hard.write_text("SoftResv\n")
    return cache, gdir, hard


def test_run_audit_buckets(tmp_path: Path) -> None:
    cache, gdir, hard = _audit_fixture(tmp_path)
    res = ua.run_audit(
        ua.AuditInputs(
            plexos_cache_dir=cache,
            gtopt_pampl_dir=gdir,
            gtopt_json=gdir / "case.json",
            hard_list=hard,
        )
    )
    b = res.buckets
    assert [i["name"] for i in b["B5_hard_in_plexos_soft_in_gtopt"]] == ["SoftResv"]
    assert [i["name"] for i in b["B9_inactive_gtopt_active_plexos"]] == ["Inact"]
    assert b["B7_missing_uc_family"][0]["count"] == 55
    assert [i["name"] for i in b["B8_synthetic_in_gtopt"]] == ["Synth"]
    classes = {r["name"]: r["gtopt_penalty_class"] for r in res.per_row_diff}
    assert classes["SoftResv"] == "soft_resv"
    assert classes["SoftOp"] == "soft_op"
    assert classes["NegPen"] == "unknown"


def test_main_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cache, gdir, hard = _audit_fixture(tmp_path)
    out = tmp_path / "out" / "audit.json"
    rc = ua.main(
        [
            "--plexos-cache",
            str(cache),
            "--gtopt-dir",
            str(gdir),
            "--hard-list",
            str(hard),
            "--gtopt-lp",
            str(tmp_path / "missing.lp"),
            "--plexos-input-dir",
            str(tmp_path / "no_dir"),
            "--plexos-xml",
            str(tmp_path / "no.xml"),
            "--output",
            str(out),
            "--strict",
        ]
    )
    assert rc == 1
    assert json.loads(out.read_text())["summary"]["n_plexos"] == 59
    captured = capsys.readouterr()
    assert "buckets:" in captured.out
    assert "[strict]" in captured.err

    rc = ua.main(
        [
            "--plexos-cache",
            str(cache),
            "--gtopt-dir",
            str(gdir),
            "--gtopt-json",
            str(gdir / "case.json"),
            "--hard-list",
            str(hard),
            "--strict",
        ]
    )
    assert rc == 1


def test_main_clean_strict_returns_zero(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _constraint_cache(cache, {"C": {ua.PROP_RHS: [5.0]}})
    gdir = tmp_path / "g"
    _plan(gdir / "p.json", {"user_constraint_array": []})
    (gdir / "uc_x.pampl").write_text('constraint C: 1 * x("a") <= 5;\n')
    hard = tmp_path / "hard.txt"
    hard.write_text("OTHER\n")
    argv = ["--plexos-cache", str(cache), "--gtopt-dir", str(gdir)]
    assert ua.main([*argv, "--hard-list", str(hard), "--strict"]) == 0


def test_main_argument_errors(tmp_path: Path) -> None:
    gdir = tmp_path / "g"
    gdir.mkdir()
    with pytest.raises(SystemExit, match="plexos-cache"):
        ua.main(["--plexos-cache", str(tmp_path / "x"), "--gtopt-dir", str(gdir)])
    with pytest.raises(SystemExit, match="gtopt-dir"):
        ua.main(["--plexos-cache", str(gdir), "--gtopt-dir", str(tmp_path / "x")])
    (gdir / "a.provenance.json").write_text("{}")
    with pytest.raises(SystemExit, match="no planning JSON"):
        ua.main(["--plexos-cache", str(gdir), "--gtopt-dir", str(gdir)])


def test_print_summary_optional_lines(capsys: pytest.CaptureFixture[str]) -> None:
    summary = {
        "n_plexos": 1,
        "n_gtopt_total": 1,
        "n_gtopt_pampl": 1,
        "n_gtopt_json": 0,
        "n_intersection": 1,
        "n_missing_native_primitive": 2,
        "n_missing_real": 1,
        "n_missing_from_gtopt": 3,
        "n_synthetic_in_gtopt": 0,
        "n_duplicates_in_gtopt": 0,
        "n_native_deferred": 4,
        "hard_list_total": 7,
        "bucket_counts": {"B3": 1},
    }
    result = ua.AuditResult(
        plexos_solution={},
        gtopt_ucs=[],
        duplicates={},
        intersection=[],
        missing_from_gtopt=[],
        synthetic_in_gtopt=[],
        buckets={
            "B17_soft_slack_coverage": [
                {"priority": "HIGH", "kind": "k", "family": "f", "field": "x"},
                {"kind": "info"},
            ]
        },
        per_row_diff=[],
        summary=summary,
    )
    ua._print_summary(result)
    text = capsys.readouterr().out
    assert "encoded as native primitives" in text
    assert "native RHS source deferred:4" in text
    assert "hard-list size:            7" in text
    assert "[HIGH] k (f)" in text


def test_main_input_dir_buckets_not_strict(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _constraint_cache(cache, {"C": {ua.PROP_RHS: [5.0]}})
    inp = tmp_path / "in"
    _long_csv(inp / "Gen_Rating.csv", {"B1": [10.0, 20.0], "G1": [5.0, 5.0]})
    _long_csv(inp / "Hydro_EfficiencyIncr.csv", {"G1": [1.0, 3.0]})
    _wide(inp / "Hydro_MaxVolume.csv", "R1", [100.0, 100.0])
    gdir = tmp_path / "g"
    _plan(
        gdir / "p.json",
        {
            "battery_array": [{"name": "B1", "pmax_discharge": 20.0}],
            "reservoir_array": [{"name": "R1", "emax": 50.0}],
            "turbine_array": [
                {"name": "T1", "generator": "G1", "production_factor": 2.0}
            ],
        },
    )
    out = tmp_path / "a.json"
    rc = ua.main(
        [
            "--plexos-cache",
            str(cache),
            "--gtopt-dir",
            str(gdir),
            "--plexos-input-dir",
            str(inp),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    buckets = json.loads(out.read_text())["buckets"]
    for key in (
        "B13_profile_collapse",
        "B14_reservoir_bounds_mismatch",
        "B16_turbine_pf_mismatch",
    ):
        assert buckets[key]["count"] >= 1
