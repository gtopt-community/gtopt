# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for gtopt_expand.cli helpers and error paths."""

from __future__ import annotations

import argparse
import json
import runpy
import sys

import pytest

from gtopt_expand import cli
from gtopt_expand.pumped_storage_expand import default_config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, 3),
        (14, 2),
        (0, 12),
        ("7", 7),
        ("13", 1),
        (" Marzo ", 3),
        ("setiembre", 9),
        ("bogus", 1),
        (None, 1),
    ],
)
def test_normalize_month(raw, expected):
    assert cli._normalize_month(raw) == expected


def test_extract_stage_list_shapes():
    stages = [{"number": 1}]
    assert cli._extract_stage_list(stages) is stages
    assert cli._extract_stage_list({"stages": stages}) is stages
    assert cli._extract_stage_list({"simulation": {"stage_array": stages}}) is stages
    assert cli._extract_stage_list({"simulation": {}}) is None
    assert cli._extract_stage_list(5) is None


def test_extract_reservoir_names_shapes():
    assert cli._extract_reservoir_names([]) == set()
    assert cli._extract_reservoir_names({"system": 3}) == set()
    assert cli._extract_reservoir_names({"reservoir_array": "x"}) == set()
    names = cli._extract_reservoir_names(
        {"reservoir_array": ["junk", {"name": ""}, {"name": "A"}, {"name": 4}]}
    )
    assert names == {"A"}


def test_load_reservoir_names(tmp_path):
    inp = tmp_path / "maule.json"
    assert cli._load_reservoir_names(None, inp) == set()
    (tmp_path / "planning.json").write_text(
        json.dumps({"system": {"reservoir_array": [{"name": "R"}]}}),
        encoding="utf-8",
    )
    assert cli._load_reservoir_names(None, inp) == {"R"}
    with pytest.raises(FileNotFoundError, match="--planning"):
        cli._load_reservoir_names(str(tmp_path / "nope.json"), inp)


def test_load_stages(tmp_path):
    assert cli._load_stages(None) is None
    path = tmp_path / "st.json"
    path.write_text(
        json.dumps({"stages": [{"uid": 5, "month": "april"}, {"month": 2}]}),
        encoding="utf-8",
    )
    stages = cli._load_stages(str(path)).get_all()
    assert stages == [{"uid": 5, "number": 5, "month": 4}, {"month": 2, "number": 2}]
    path.write_text(json.dumps({"x": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a stage list"):
        cli._load_stages(str(path))


def _wv_args(**kw) -> argparse.Namespace:
    base = {
        "auto_water_fail_cost": False,
        "water_fail_cost": None,
        "plp_case_dir": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_water_value_resolver_guards(tmp_path, capsys):
    assert cli._maybe_build_water_value_resolver(_wv_args()) is None
    assert (
        cli._maybe_build_water_value_resolver(_wv_args(auto_water_fail_cost=True))
        is None
    )
    assert "--plp-case-dir not provided" in capsys.readouterr().err
    with pytest.raises(FileNotFoundError, match="not a directory"):
        cli._maybe_build_water_value_resolver(
            _wv_args(water_fail_cost=1.0, plp_case_dir=str(tmp_path / "missing"))
        )
    with pytest.raises(FileNotFoundError, match="plpcnfce.dat"):
        cli._maybe_build_water_value_resolver(
            _wv_args(water_fail_cost=1.0, plp_case_dir=str(tmp_path))
        )


class _FakeParser:
    def __init__(self, path):
        self.path = path
        self.centrals = [{"name": "X", "type": "termica", "gcost": 10.0}]
        self.efficiencies: list = []

    def parse(self):
        return None


def test_water_value_resolver_built(tmp_path, monkeypatch):
    (tmp_path / "plpcnfce.dat").write_text("x", encoding="utf-8")
    (tmp_path / "plpcenre.dat").write_text("x", encoding="utf-8")
    monkeypatch.setattr("plp2gtopt.central_parser.CentralParser", _FakeParser)
    monkeypatch.setattr("plp2gtopt.cenre_parser.CenreParser", _FakeParser)
    res = cli._maybe_build_water_value_resolver(
        _wv_args(water_fail_cost="42", plp_case_dir=str(tmp_path))
    )
    assert res.anchor == 42.0
    assert res.cenre_parser is not None
    (tmp_path / "plpcenre.dat").unlink()
    res2 = cli._maybe_build_water_value_resolver(
        _wv_args(auto_water_fail_cost=True, plp_case_dir=str(tmp_path))
    )
    assert res2.cenre_parser is None
    assert res2.is_active


def test_cli_pumped_storage(tmp_path, capsys):
    planning = {
        "system": {
            "reservoir_array": [
                {
                    "uid": 1,
                    "name": "COLBUN",
                    "junction": "COLBUN",
                    "emin": 1.0,
                    "emax": 9.0,
                },
                {"uid": 2, "name": "MACHICURA", "junction": "MACHICURA"},
            ]
        }
    }
    ppath = tmp_path / "planning.json"
    ppath.write_text(json.dumps(planning), encoding="utf-8")
    cfgs = []
    for name in ("hb_a", "hb_b"):
        p = tmp_path / f"{name}.json"
        p.write_text(
            json.dumps(default_config(name=name, vmin=1000.0, vmax=9000.0)),
            encoding="utf-8",
        )
        cfgs += ["--input", str(p)]
    out = tmp_path / "out"
    rc = cli.main(
        ["pumped_storage", *cfgs, "--planning", str(ppath), "--out", str(out)]
    )
    assert rc == 0
    assert (out / "hb_a.json").is_file() and (out / "hb_b.json").is_file()
    assert "wrote" in capsys.readouterr().err


def test_cli_error_paths(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli.main(["laja", "--in", str(bad), "--out", str(tmp_path / "o")]) == 2
    assert "invalid JSON" in capsys.readouterr().err

    def _boom(_args):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "_run", _boom)
    assert cli.main(["laja", "--in", str(bad), "--out", str(tmp_path / "o")]) == 2
    assert "I/O failure" in capsys.readouterr().err


def test_module_entry_point(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gtopt_expand", "--help"])
    monkeypatch.delitem(sys.modules, "gtopt_expand.cli", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt_expand.cli", run_name="__main__")
    assert exc.value.code == 0


def test_stage_list_shim():
    shim = cli._StageList([{"number": 1}])
    assert shim.get_all() == [{"number": 1}]
