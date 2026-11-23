"""Branch coverage for the :mod:`plexos2gtopt.plexos2gtopt` façade.

Drives :func:`convert_plexos_bundle` on a tiny synthetic XML bundle and
replaces the heavy collaborators (accdb readers, PLEXOS model
materialisation, auto-lift OPF, emissions) with light fakes so each
orchestration branch is exercised in isolation.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from plexos2gtopt import plexos2gtopt as p2g
from plexos2gtopt import plexos_block_layout as pbl
from plexos2gtopt.entities import BoundaryCutSpec
from plexos2gtopt.gtopt_writer import build_planning as _real_build_planning
from plexos2gtopt.main import make_parser
from plexos2gtopt.parsers import extract_case as _real_extract_case
from plexos2gtopt.plexos_loader import DBSEN_FILENAME
from plexos2gtopt.plexos_xml import NS


_TINY_XML = f"""<?xml version="1.0" standalone="yes"?>
<MasterDataSet xmlns="{NS[1:-1]}">
  <t_class><class_id>1</class_id><name>System</name></t_class>
  <t_class><class_id>22</class_id><name>Node</name></t_class>
  <t_class><class_id>2</class_id><name>Generator</name></t_class>
  <t_object>
    <object_id>1</object_id><class_id>22</class_id><name>bus_a</name>
  </t_object>
  <t_object>
    <object_id>2</object_id><class_id>22</class_id><name>bus_b</name>
  </t_object>
</MasterDataSet>
"""


@pytest.fixture(autouse=True)
def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the run registry out of the real ``~/.cache``."""
    reg = tmp_path / "cache" / "runs.jsonl"
    monkeypatch.setattr(p2g, "PLEXOS_RUN_REGISTRY", reg)
    return reg


def _bundle(tmp_path: Path, *, csv: bool = False) -> Path:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / DBSEN_FILENAME).write_text(_TINY_XML)
    if csv:
        (bundle_dir / "Gen_Rating.csv").write_text("NAME,1\n")
    return bundle_dir


def _convert(tmp_path: Path, **extra: Any) -> tuple[int, dict[str, Any], Path]:
    bundle_dir = _bundle(tmp_path, csv=bool(extra.pop("_csv", False)))
    out_dir = tmp_path / "out"
    opts: dict[str, Any] = {
        "input_bundle": bundle_dir,
        "horizon_mode": "hourly",
        "horizon_days": 1,
        "output_dir": out_dir,
        "run_check": False,
    }
    opts.update(extra)
    rc = p2g.convert_plexos_bundle(opts)
    planning = json.loads((out_dir / "bundle.json").read_text())
    return rc, planning, out_dir


# ---------------------------------------------------------------------------
# validate_plexos_bundle
# ---------------------------------------------------------------------------


def test_validate_ok_and_missing_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = _bundle(tmp_path)
    assert p2g.validate_plexos_bundle({"input_bundle": bundle_dir}) is True

    @contextlib.contextmanager
    def _fake(_p: Path) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(xml_path=tmp_path / "nope.xml", source=_p)

    monkeypatch.setattr(p2g, "locate_bundle", _fake)
    assert p2g.validate_plexos_bundle({"input_bundle": bundle_dir}) is False


# ---------------------------------------------------------------------------
# _maybe_patch_with_plp_embalses
# ---------------------------------------------------------------------------


def test_plp_patch_disabled_flags(tmp_path: Path) -> None:
    case = object()
    assert (
        p2g._maybe_patch_with_plp_embalses(case, {"plexos_legacy": True}, tmp_path)
        is case
    )
    assert (
        p2g._maybe_patch_with_plp_embalses(case, {"no_plp_embalses": True}, tmp_path)
        is case
    )


def test_plp_patch_explicit_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plexos2gtopt import _plp_patch

    calls: list[Path] = []

    def _patch(case: Any, path: Path) -> Any:
        calls.append(path)
        return ("patched", case)

    monkeypatch.setattr(_plp_patch, "patch_case_with_plp_embalses", _patch)
    existing = tmp_path / "embalses.csv"
    existing.write_text("x\n")
    out = p2g._maybe_patch_with_plp_embalses("c", {"plp_embalses": existing}, tmp_path)
    assert out == ("patched", "c")
    assert calls == [existing]

    # Missing path resolved through find_compressed_path.
    resolved = tmp_path / "embalses.csv.xz"
    monkeypatch.setattr(_plp_patch, "find_compressed_path", lambda _p: resolved)
    p2g._maybe_patch_with_plp_embalses(
        "c", {"plp_embalses": tmp_path / "missing.csv"}, tmp_path
    )
    assert calls[-1] == resolved

    # Missing path that cannot be resolved -> FileNotFoundError.
    monkeypatch.setattr(_plp_patch, "find_compressed_path", lambda _p: None)
    with pytest.raises(FileNotFoundError, match="--plp-embalses"):
        p2g._maybe_patch_with_plp_embalses(
            "c", {"plp_embalses": tmp_path / "missing.csv"}, tmp_path
        )


def test_plp_patch_auto_detect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from plexos2gtopt import _plp_patch

    monkeypatch.setattr(_plp_patch, "auto_detect_embalses", lambda _p: None)
    assert p2g._maybe_patch_with_plp_embalses("c", {}, tmp_path) == "c"
    found = tmp_path / "e.csv"
    monkeypatch.setattr(_plp_patch, "auto_detect_embalses", lambda _p: found)
    monkeypatch.setattr(_plp_patch, "patch_case_with_plp_embalses", lambda c, p: (c, p))
    assert p2g._maybe_patch_with_plp_embalses("c", {}, tmp_path) == ("c", found)


# ---------------------------------------------------------------------------
# _materialise_plexos_model
# ---------------------------------------------------------------------------


def _patch_materialise(monkeypatch: pytest.MonkeyPatch, horizon: Any) -> list[Any]:
    from plexos2gtopt import plexos_model, plexos_xml

    seen: list[Any] = []

    class _Report:
        def as_dict(self) -> dict[str, Any]:
            return {"model": "M"}

    def _mat(xml_path: Path, mopts: Any) -> tuple[Any, Any]:
        seen.append(mopts)
        return SimpleNamespace(name=str(xml_path.parent)), _Report()

    monkeypatch.setattr(plexos_model, "materialise_bundle_dir", _mat)
    monkeypatch.setattr(
        plexos_xml, "load_xml", lambda _p: SimpleNamespace(horizon_start=horizon)
    )
    return seen


def test_materialise_plexos_model_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as dt

    start = dt.datetime(2025, 1, 2)
    seen = _patch_materialise(monkeypatch, start)
    bundle = SimpleNamespace(
        xml_path=tmp_path / "x.xml",
        n_days=None,
        source=str(tmp_path / "b.zip"),
        _extra_cleanup=[],
        root=None,
        materialised_model=None,
    )
    opts: dict[str, Any] = {"plexos_model": "M", "plexos_data_dirs": [tmp_path]}
    p2g._materialise_plexos_model(bundle, opts)
    assert seen[-1].start == start
    assert seen[-1].n_days == 1
    assert seen[-1].sample == 1
    assert seen[-1].data_dirs == (tmp_path, tmp_path)
    assert bundle.materialised_model == "M"
    assert opts["_plexos_model_report"] == {"model": "M"}

    # Explicit start date wins over the Horizon.
    opts2: dict[str, Any] = {
        "plexos_model": "M",
        "plexos_start_date": "2024-05-06",
        "plexos_sample": 3,
    }
    p2g._materialise_plexos_model(bundle, opts2)
    assert seen[-1].start.year == 2024
    assert seen[-1].sample == 3

    # No explicit date and no Horizon 'Date From' -> error.
    _patch_materialise(monkeypatch, None)
    with pytest.raises(ValueError, match="plexos-start-date"):
        p2g._materialise_plexos_model(bundle, {"plexos_model": "M"})


def test_convert_with_plexos_model_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake(bundle: Any, options: dict[str, Any]) -> None:
        options["_plexos_model_report"] = {"model": "Base", "n": 1}

    monkeypatch.setattr(p2g, "_materialise_plexos_model", _fake)
    rc, _planning, out_dir = _convert(tmp_path, plexos_model="Base")
    assert rc == 0
    rep = json.loads((out_dir / "bundle.plexos_model.json").read_text())
    assert rep == {"model": "Base", "n": 1}


# ---------------------------------------------------------------------------
# convert_plexos_bundle: horizon / block layout branches
# ---------------------------------------------------------------------------


def test_convert_with_csv_and_explicit_accdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accdb = tmp_path / "sol.accdb"
    cached: list[Path] = []
    monkeypatch.setattr(pbl, "load_block_layout_from_accdb", lambda _p: ((1, 2), (3,)))

    def _cache(acc: Path, out: Path) -> Path:
        cached.append(acc)
        return out / "cache"

    monkeypatch.setattr(pbl, "cache_plexos_tables", _cache)
    rc, planning, _ = _convert(
        tmp_path,
        _csv=True,
        horizon_mode="plexos",
        plexos_solution_accdb=str(accdb),
        use_plexos_commit=True,
    )
    assert rc == 0
    assert cached == [accdb]
    assert len(planning["simulation"]["block_array"]) == 2


def test_convert_explicit_non_accdb_path_falls_back(tmp_path: Path) -> None:
    rc, planning, _ = _convert(
        tmp_path,
        horizon_mode="plexos",
        plexos_solution_accdb=str(tmp_path / "sol.txt"),
    )
    assert rc == 0
    assert len(planning["simulation"]["block_array"]) == 24


def test_convert_auto_discovered_res_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = tmp_path / "RES.zip"
    monkeypatch.setattr(pbl, "auto_discover_res_zip", lambda _p: res)
    monkeypatch.setattr(
        pbl, "extract_accdb_from_res_zip", lambda _p: tmp_path / "a.accdb"
    )
    monkeypatch.setattr(pbl, "load_block_layout_from_accdb", lambda _p: ((1,), (2,)))
    rc, planning, _ = _convert(tmp_path, horizon_mode="plexos")
    assert rc == 0
    # No solution consumer requested -> no cache dump, layout still used.
    assert len(planning["simulation"]["block_array"]) == 2


def test_convert_res_zip_without_accdb_infers_days(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(pbl, "auto_discover_res_zip", lambda _p: tmp_path / "R.zip")
    monkeypatch.setattr(pbl, "extract_accdb_from_res_zip", lambda _p: None)
    monkeypatch.setattr(pbl, "infer_horizon_days_from_input", lambda _p: 2)
    with caplog.at_level(logging.WARNING):
        rc, planning, _ = _convert(tmp_path, horizon_mode="plexos", horizon_days=None)
    assert rc == 0
    assert len(planning["simulation"]["block_array"]) == 48
    assert any("inferred from Horizon name" in r.message for r in caplog.records)


def test_convert_plexos_mode_default_one_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(pbl, "auto_discover_res_zip", lambda _p: None)
    with caplog.at_level(logging.WARNING):
        rc, planning, _ = _convert(tmp_path, horizon_mode="plexos", horizon_days=None)
    assert rc == 0
    assert len(planning["simulation"]["block_array"]) == 24
    assert any("default (set --horizon-days N)" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# convert_plexos_bundle: option branches after extraction
# ---------------------------------------------------------------------------


def test_convert_misc_option_branches(tmp_path: Path) -> None:
    rc, planning, _ = _convert(
        tmp_path,
        bess_reserve="legacy",
        cogen_must_run="not-a-tuple",
        use_single_bus=True,
        reservoir_flow_estimate=False,
        emissions_report=str(tmp_path / "em.json"),
    )
    assert rc == 0
    mo = planning["options"]["model_options"]
    assert mo["use_single_bus"] is True
    assert mo["use_kirchhoff"] is False
    assert (tmp_path / "em.json").is_file()


def test_convert_scale_theta_not_emitted_by_default(tmp_path: Path) -> None:
    """Unset ``--scale-theta`` leaves the θ scale to gtopt's C++ auto mode."""
    rc, planning, _ = _convert(tmp_path)
    assert rc == 0
    mo = planning["options"].get("model_options", {})
    assert "scale_theta" not in mo


def test_convert_scale_theta_explicit_emitted(tmp_path: Path) -> None:
    """An explicit ``--scale-theta 1000`` reaches ``model_options``."""
    rc, planning, _ = _convert(tmp_path, scale_theta=1000.0)
    assert rc == 0
    assert planning["options"]["model_options"]["scale_theta"] == 1000.0


def test_cli_scale_theta_flag() -> None:
    """``--scale-theta`` is opt-in (default None) and parsed as float."""
    parser = make_parser()
    assert parser.parse_args(["bundle.zip"]).scale_theta is None
    args = parser.parse_args(["bundle.zip", "--scale-theta", "1000"])
    assert args.scale_theta == 1000.0


def test_convert_boundary_cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_extract = _real_extract_case

    def _extract(bundle: Any, **kw: Any) -> Any:
        case = real_extract(bundle, **kw)
        return replace(case, boundary_cut=BoundaryCutSpec(fcf=10.0, slopes={}))

    monkeypatch.setattr(p2g, "extract_case", _extract)
    written: list[str | None] = ["boundary_cuts.csv"]
    monkeypatch.setattr(p2g, "write_boundary_cut_csv", lambda *_a, **_k: written[0])
    _rc, planning, _ = _convert(tmp_path)
    fc = planning["system"].get("future_cost_array")
    assert fc and fc[0]["cuts_file"] == "boundary_cuts.csv"

    # Writer declining to emit a cut -> no FutureCost element.
    written[0] = None
    for sub in ("bundle", "out"):
        import shutil

        shutil.rmtree(tmp_path / sub)
    _rc, planning, _ = _convert(tmp_path)
    assert not planning["system"].get("future_cost_array")


def _inject_ucs(monkeypatch: pytest.MonkeyPatch) -> None:
    real_build = _real_build_planning

    def _build(case: Any, **kw: Any) -> dict[str, Any]:
        planning = real_build(case, **kw)
        planning["system"]["user_constraint_array"] = [{"name": "uc1"}]
        return planning

    monkeypatch.setattr(p2g, "build_planning", _build)


@pytest.mark.parametrize("kept", [[{"name": "uc1"}], []])
def test_convert_uc_emit_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept: list[dict[str, str]]
) -> None:
    _inject_ucs(monkeypatch)
    seen: dict[str, Any] = {}

    def _filter(arr: Any, **kw: Any) -> list[dict[str, str]]:
        seen.update(kw)
        return kept

    monkeypatch.setattr(p2g, "filter_user_constraints", _filter)
    _rc, planning, _ = _convert(
        tmp_path,
        uc_emit="inline",
        pampl_uc_only="a, b,",
        pampl_uc_off="c",
        default_uc_penalty=5.0,
    )
    assert seen["only"] == frozenset({"a", "b"})
    assert seen["off"] == frozenset({"c"})
    assert seen["force_penalty"] == 5.0
    assert planning["system"].get("user_constraint_array", []) == kept


@pytest.mark.parametrize(
    ("files", "rem"),
    [(["uc.pampl"], [{"name": "uc1"}]), ([], [])],
)
def test_convert_uc_emit_pampl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: list[str],
    rem: list[dict[str, str]],
) -> None:
    _inject_ucs(monkeypatch)
    monkeypatch.setattr(
        p2g, "write_user_constraint_pampl", lambda *_a, **_k: (files, rem)
    )
    _rc, planning, _ = _convert(tmp_path)
    sys_d = planning["system"]
    assert sys_d.get("user_constraint_files", []) == files
    assert sys_d.get("user_constraint_array", []) == rem


def test_convert_emission_flag_conflict(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mutually"):
        _convert(tmp_path, only_emissions=True, no_emissions=True)


def test_convert_no_emissions_strips_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_build = _real_build_planning

    def _build(case: Any, **kw: Any) -> dict[str, Any]:
        planning = real_build(case, **kw)
        sys_ = planning["system"]
        sys_["emission_zone_array"] = [{"uid": 1}]
        sys_["fuel_array"] = [{"name": "f", "emission_factors": [1], "heat_content": 2}]
        sys_["generator_array"] = [
            {"name": "g", "emission_rate": 1, "emission_captures": [], "emissions": 1}
        ]
        return planning

    monkeypatch.setattr(p2g, "build_planning", _build)
    _rc, planning, _ = _convert(tmp_path, no_emissions=True)
    sys_ = planning["system"]
    assert "emission_zone_array" not in sys_
    assert sys_["fuel_array"] == [{"name": "f", "heat_content": 2}]
    assert sys_["generator_array"][0] == {"name": "g"}


def test_convert_explicit_emissions_file_and_import_buses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gtopt_shared.emissions as em

    seen: dict[str, Any] = {}

    def _apply(planning: dict[str, Any], src: Any, **kw: Any) -> None:
        seen["src"] = src
        planning["system"]["emission_zone_array"] = [
            {"uid": 1, "name": "z", "emissions": [{"pollutant": "co2"}]}
        ]

    monkeypatch.setattr(em, "apply_emission_defaults_from_file", _apply)
    src = tmp_path / "em.json"
    _rc, planning, _ = _convert(
        tmp_path, emissions_file=str(src), emission_import_buses=["bus_b"]
    )
    assert seen["src"] == src
    zones = planning["system"]["emission_zone_array"]
    assert [z["name"] for z in zones] == ["z", "z_imports"]
    assert zones[1]["buses"] == ["bus_b"]


def test_convert_registry_oserror_is_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _boom(**_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(p2g, "_record_plexos_run", _boom)
    with caplog.at_level(logging.WARNING):
        rc, _planning, _ = _convert(tmp_path)
    assert rc == 0
    assert any("run registry" in r.message for r in caplog.records)


def test_convert_records_run(tmp_path: Path, _registry: Path) -> None:
    _convert(tmp_path)
    row = json.loads(_registry.read_text().splitlines()[-1])
    assert row["bundle_stem"] == "bundle"


@pytest.mark.parametrize(
    ("engine", "overloaded", "msg"),
    [
        ("gtopt", ["L1"], "patched 1"),
        ("pandapower", [], "nothing patched"),
    ],
)
def test_convert_auto_lift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    engine: str,
    overloaded: list[str],
    msg: str,
) -> None:
    from plexos2gtopt import auto_lift_lines as al

    monkeypatch.setattr(
        al, "detect_overloaded_lines_via_gtopt", lambda *_a, **_k: overloaded
    )
    monkeypatch.setattr(al, "detect_overloaded_lines", lambda *_a, **_k: overloaded)
    monkeypatch.setattr(al, "patch_bundle_with_lifts", lambda _f, o: len(o))
    with caplog.at_level(logging.INFO):
        rc, _planning, _ = _convert(
            tmp_path, auto_lift_lines=1.5, auto_lift_engine=engine
        )
    assert rc == 0
    assert any(msg in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# split_import_emission_zones
# ---------------------------------------------------------------------------


def test_split_import_emission_zones() -> None:
    planning: dict[str, Any] = {
        "system": {
            "bus_array": [{"name": "a"}, {"name": "b"}],
            "emission_zone_array": [
                {"uid": 3, "name": "z1", "emissions": [{"p": 1}]},
                {"uid": 4, "name": "z2", "buses": ["a"]},
            ],
        }
    }
    assert p2g.split_import_emission_zones(planning, ["b"]) == 1
    zones = planning["system"]["emission_zone_array"]
    assert zones[0]["buses"] == ["a"]
    assert zones[2] == {
        "uid": 5,
        "name": "z1_imports",
        "emissions": [{"p": 1}],
        "buses": ["b"],
    }
    with pytest.raises(ValueError, match="unknown bus"):
        p2g.split_import_emission_zones(planning, ["zz"])


def test_python_dash_m_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """``python -m plexos2gtopt --version`` runs ``main.main``."""
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["plexos2gtopt", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("plexos2gtopt", run_name="__main__")
    assert exc.value.code in (0, None)
