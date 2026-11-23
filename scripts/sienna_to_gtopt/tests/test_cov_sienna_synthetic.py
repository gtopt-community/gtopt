"""Synthetic-case tests for sienna_to_gtopt (no upstream data needed).

A tiny Sienna-format case directory (bus / branch / gen / hydro CSVs) is
written under ``tmp_path``; variant builders are also driven with
hand-built ``SiennaCase`` objects to reach their validation errors.
"""

from __future__ import annotations

import importlib.util
import io
import json
import runpy
import sys
import tarfile
from pathlib import Path

import pytest

import sienna_to_gtopt.__main__ as cli
from sienna_to_gtopt import _bundle
from sienna_to_gtopt._cascading_hydro import _sorted_hydro_chain, build_cascading_hydro
from sienna_to_gtopt._fuel_cost_ts import build_fuel_cost_ts
from sienna_to_gtopt._hvdc import build_hvdc
from sienna_to_gtopt._interruptible_load import build_interruptible_load
from sienna_to_gtopt._monitored_line import _line_dict, build_monitored_line
from sienna_to_gtopt._pumped_storage import build_pumped_storage
from sienna_to_gtopt._reader import (
    SiennaBranch,
    SiennaBus,
    SiennaCase,
    SiennaGen,
    _int_or,
    load_case,
    parse_branches,
    parse_buses,
    parse_generators,
    parse_hydro_upstream,
    parse_user_descriptors,
    to_any_dict,
)
from sienna_to_gtopt._wecc_240 import build_wecc_240


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


@pytest.fixture(name="case_dir")
def fixture_case_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sienna_tiny"
    d.mkdir()
    _write(
        d / "bus.csv",
        "Bus ID,Bus Name,BaseKV,Bus Type,MW Load,Area\n"
        "1,nodeA,230,REF,0,1\n"
        "2,nodeB,230,PV,100,1\n"
        ",,,,,\n",
    )
    _write(
        d / "branch.csv",
        "UID,From Bus,To Bus,R,X,B,Cont Rating,Tr Ratio\n"
        "br1,1,2,0.01,0.1,0,200,0\n"
        ",1,2,0,0,0,0,0\n",
    )
    _write(
        d / "gen.csv",
        "GEN UID,Bus ID,Unit Type,Fuel,Category,PMax MW,PMin MW,"
        "var_cost_0,var_cost_1,VOM\n"
        "CT1,1,CT,NATURAL_GAS,gas_ct,150,0,0,30,1\n"
        "ST1,1,ST,COAL,coal,100,0,20,,1\n"
        "CC1,2,CC,NATURAL_GAS,gas_cc,80,0,,,7\n"
        "HydroDispatch1,2,HY,HYDRO,hydro_reservoir,50,0,,,0\n"
        "HydroDispatch2,2,HY,HYDRO,hydro_reservoir,40,0,,,0\n"
        ",1,CT,X,x,1,0,,,0\n",
    )
    _write(
        d / "Hydro_Upstream_Input.csv",
        "hydrounit,upstream\nHydroUnit2,(HydroUnit1)\nHydroUnit1,\n,\n",
    )
    return d


# ── reader ──────────────────────────────────────────────────────────────


def test_reader_parses_and_skips_blank_rows(case_dir: Path) -> None:
    case = load_case(case_dir)
    assert [b.bus_id for b in case.buses] == [1, 2]
    assert [b.uid for b in case.branches] == ["br1"]
    costs = {g.name: g.var_cost for g in case.generators}
    assert costs == {
        "CT1": 30.0,
        "ST1": 20.0,
        "CC1": 7.0,
        "HydroDispatch1": 0.0,
        "HydroDispatch2": 0.0,
    }
    assert case.hydro_upstream == {"HydroUnit2": ["HydroUnit1"], "HydroUnit1": []}
    assert parse_buses(case_dir / "bus.csv")[1].mw_load == 100.0
    assert parse_branches(case_dir / "branch.csv")[0].x == 0.1
    assert len(parse_generators(case_dir / "gen.csv")) == 5
    assert "HydroUnit1" in parse_hydro_upstream(case_dir / "Hydro_Upstream_Input.csv")


def test_parse_hydro_upstream_short_row(tmp_path: Path) -> None:
    """Regression: a row without the ``upstream`` cell crashed on None."""
    path = _write(tmp_path / "h.csv", "hydrounit,upstream\nHydroUnit1\n")
    assert parse_hydro_upstream(path) == {"HydroUnit1": []}


def test_int_or_default() -> None:
    assert _int_or("x", 7) == 7
    assert _int_or(" '3.0' ") == 3


def test_parse_user_descriptors(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ud.yaml",
        "# comment\n"
        "  - {custom_name: orphan, name: dropped}\n"
        "bus:\n"
        "\n"
        "  - {custom_name: Bus ID, name: number}\n"
        "  - {custom_name: Bus Name}\n"
        "  - {bad entry, custom_name: Area, name: area}\n"
        "  not a bullet\n"
        "gen:\n"
        "  - {custom_name: GEN UID, name: name}\n",
    )
    assert parse_user_descriptors(path) == {
        "bus": {"Bus ID": "number", "Area": "area"},
        "gen": {"GEN UID": "name"},
    }


def test_to_any_dict() -> None:
    bus = SiennaBus(1, "a", 230.0, "PV", 0.0, 1)
    out = to_any_dict([bus, {"k": 1}])
    assert out[0]["bus_id"] == 1
    assert out[1] == {"k": 1}


# ── variant builders: validation errors / branches ──────────────────────


def _bus(i: int, load: float = 0.0) -> SiennaBus:
    return SiennaBus(i, f"n{i}", 230.0, "PV", load, 1)


def _branch(uid: str, x: float = 0.1) -> SiennaBranch:
    return SiennaBranch(uid, 1, 2, 0.0, x, 0.0, 100.0, 0.0)


def _gen(name: str, fuel: str = "NATURAL_GAS") -> SiennaGen:
    return SiennaGen(name, 1, "CT", fuel, "c", 100.0, 0.0, 10.0, 1.0)


def test_cascading_hydro_requires_hydro() -> None:
    with pytest.raises(ValueError, match="requires HYDRO generators"):
        build_cascading_hydro(SiennaCase(buses=[_bus(1)], generators=[_gen("g")]))


def test_sorted_hydro_chain_handles_out_of_order_rows() -> None:
    gens = [_gen("H1", "HYDRO"), _gen("H2", "HYDRO")]
    ordered = _sorted_hydro_chain(
        gens, {"HydroUnit2": ["HydroUnit1"], "HydroUnit1": []}
    )
    assert [g.name for g in ordered] == ["H1", "H2"]  # headwater first


def test_fuel_cost_ts_validation() -> None:
    with pytest.raises(ValueError, match="non-empty case"):
        build_fuel_cost_ts(SiennaCase())
    with pytest.raises(ValueError, match="must be non-empty"):
        build_fuel_cost_ts(SiennaCase(buses=[_bus(1)]), fuel_price_per_block=())


def test_hvdc_validation() -> None:
    with pytest.raises(ValueError, match="at least one branch"):
        build_hvdc(SiennaCase())
    with pytest.raises(ValueError, match="non-zero reactance"):
        build_hvdc(SiennaCase(buses=[_bus(1)], branches=[_branch("a", x=0.0)]))
    with pytest.raises(ValueError, match="not found in case"):
        build_hvdc(
            SiennaCase(buses=[_bus(1)], branches=[_branch("a")]), dc_branch_uid="zz"
        )


def test_interruptible_load_validation_and_fallback_bus() -> None:
    with pytest.raises(ValueError, match="non-empty case"):
        build_interruptible_load(SiennaCase())
    out = build_interruptible_load(
        SiennaCase(buses=[_bus(7), _bus(8)], generators=[_gen("g")])
    )
    il = [d for d in out["system"]["demand_array"] if d["bus"] == 7]
    assert il  # no bus carries load -> the first bus hosts the IL


def test_monitored_line_validation_and_reactance() -> None:
    with pytest.raises(ValueError, match="at least one branch"):
        build_monitored_line(SiennaCase())
    with pytest.raises(ValueError, match="not found in case"):
        build_monitored_line(
            SiennaCase(buses=[_bus(1)], branches=[_branch("a")]),
            monitored_branch_uid="zz",
        )
    line = _line_dict(_branch("a", x=0.25), 1, True, include_reactance=True)
    assert line["reactance"] == 0.25


def test_pumped_storage_requires_generators() -> None:
    with pytest.raises(ValueError, match="requires generators"):
        build_pumped_storage(SiennaCase(buses=[_bus(1)]))


def test_wecc_240_validation_and_tiny_ring() -> None:
    with pytest.raises(ValueError, match="must all be >= 1"):
        build_wecc_240(SiennaCase(), n_bus=0)
    out = build_wecc_240(SiennaCase(), n_bus=2, n_gen=1, n_demand=1, n_line=4)
    for line in out["system"]["line_array"]:
        assert line["bus_a"] != line["bus_b"]


# ── bundle helpers ──────────────────────────────────────────────────────


class _Resp(io.BytesIO):
    """Minimal urlopen() response stand-in."""

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_download_reads_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, float]] = []

    def fake_urlopen(url: str, timeout: float) -> _Resp:
        seen.append((url, timeout))
        return _Resp(b"data")

    monkeypatch.setattr(_bundle.urllib.request, "urlopen", fake_urlopen)
    assert _bundle._download("http://example.invalid/x") == b"data"
    assert seen[0][0] == "http://example.invalid/x"


def test_bundle_import_without_zstandard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare install without zstandard still imports; bundling then fails."""
    monkeypatch.setitem(sys.modules, "zstandard", None)  # blocks the import
    spec = importlib.util.spec_from_file_location(
        "_bundle_no_zstd", Path(_bundle.__file__)
    )
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh.zstandard is None
    with pytest.raises(RuntimeError, match="zstandard is required"):
        fresh._require_zstandard()


def test_require_zstandard_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_bundle, "zstandard", None)
    with pytest.raises(RuntimeError, match="zstandard is required"):
        _bundle._require_zstandard()


def test_tempdir_base_falls_back_to_home_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setattr(_bundle.Path, "home", staticmethod(lambda: tmp_path))
    assert _bundle._tempdir_base() == tmp_path / "tmp"
    assert (tmp_path / "tmp").is_dir()


def test_extract_bundle_without_filter_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pythons without ``extractall(filter=...)`` use the plain call."""
    monkeypatch.setenv("GTOPT_SIENNA_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    archive = _bundle.bundle_path("14bus")
    archive.parent.mkdir(parents=True)
    archive.write_bytes(_bundle._pack("14bus", [("a.txt", b"hello")]))

    orig = tarfile.TarFile.extractall

    def no_filter(self: tarfile.TarFile, *args: object, **kwargs: object) -> None:
        if "filter" in kwargs:
            raise TypeError("unexpected keyword argument 'filter'")
        orig(self, *args, filter="data", **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tarfile.TarFile, "extractall", no_filter)
    _bundle.extract_bundle.cache_clear()
    try:
        out = _bundle.extract_bundle("14bus")
        assert (out / "a.txt").read_bytes() == b"hello"
    finally:
        _bundle.extract_bundle.cache_clear()


# ── CLI ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("variant", "extra", "default_name"),
    [
        ("hvdc", [], "hvdc.json"),
        ("battery_degeneracy", ["--subcase", "c"], "battery_degeneracy_c.json"),
    ],
)
def test_cli_writes_default_output(
    case_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    variant: str,
    extra: list[str],
    default_name: str,
) -> None:
    monkeypatch.setattr(cli, "extract_bundle", lambda _name: case_dir)
    monkeypatch.chdir(tmp_path)
    assert cli.main([variant, *extra]) == 0
    payload = json.loads((tmp_path / default_name).read_text())
    assert "system" in payload
    assert f"Sienna {variant}:" in capsys.readouterr().out


def test_cli_explicit_output_and_module_entry(
    case_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_bundle, "extract_bundle", lambda _name: case_dir)
    out = tmp_path / "sub" / "ml.json"
    monkeypatch.setattr(
        sys, "argv", ["sienna_to_gtopt", "monitored_line", "-o", str(out)]
    )
    monkeypatch.delitem(sys.modules, "sienna_to_gtopt.__main__", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("sienna_to_gtopt", run_name="__main__")
    assert exc.value.code == 0
    assert out.is_file()
