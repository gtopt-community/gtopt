# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Validation / edge-case tests for pp2gtopt.profiles."""

from __future__ import annotations

import json
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn
import pytest

from pp2gtopt.profiles import (
    ColumnTarget,
    ProfileError,
    ProfileSpec,
    ResolvedProfiles,
    _as_timestamps,
    _bus_lookup,
    _resolve_target,
    _target_for_column,
    element_base_mw,
    load_spec,
    profiles_from_net_controllers,
    read_profile_table,
    read_profile_table_with_times,
    resolve_profiles,
)


def _net() -> pp.pandapowerNet:
    net = pn.case9()
    pp.create_sgen(net, 4, p_mw=0.0, max_p_mw=100.0, name="wind1")
    pp.create_sgen(net, 6, p_mw=7.0, max_p_mw=np.nan, name="fixed")
    pp.create_sgen(net, 6, p_mw=0.0, max_p_mw=0.0, name="dead")
    return net


# ── ColumnTarget / load_spec validation ─────────────────────────────────


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        ({"bogus": 1}, "unknown keys"),
        ({"element": "line"}, "element must be one of"),
        ({"by": "color"}, "by must be one of"),
        ({"units": "kw"}, "units must be one of"),
    ],
)
def test_column_target_validation(data: dict, msg: str) -> None:
    with pytest.raises(ProfileError, match=msg):
        ColumnTarget.from_dict(data, "here")


def test_units_for_prefers_column_units() -> None:
    spec = ProfileSpec(units={"load": "pu"})
    assert spec.units_for(ColumnTarget(element="load", units="mw")) == "mw"
    assert spec.units_for(ColumnTarget(element="load")) == "pu"
    assert spec.units_for(ColumnTarget(element="gen")) == "auto"


def _spec_file(tmp_path: Path, data: object, name: str = "s.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        ([1, 2], "spec must be a mapping"),
        ({"nope": 1}, "unknown spec keys"),
        ({"layout": "diagonal"}, "layout must be auto, wide or long"),
        ({"units": {"load": "kw"}}, "bad units entry"),
    ],
)
def test_load_spec_validation(tmp_path: Path, data: object, msg: str) -> None:
    with pytest.raises(ProfileError, match=msg):
        load_spec(_spec_file(tmp_path, data))


def test_load_spec_yaml(tmp_path: Path) -> None:
    # PyYAML is optional (the `matpower` extra), not a dev dependency.
    pytest.importorskip("yaml")
    path = tmp_path / "s.yaml"
    path.write_text("layout: wide\ndefault:\n  element: sgen\n  units: pu\n")
    spec = load_spec(path)
    assert spec.layout == "wide"
    assert spec.default is not None and spec.default.element == "sgen"


# ── reading tables ──────────────────────────────────────────────────────


def test_read_unsupported_format(tmp_path: Path) -> None:
    path = tmp_path / "p.xlsx"
    path.write_text("x")
    with pytest.raises(ProfileError, match="unsupported profile format"):
        read_profile_table(path, ProfileSpec())


def test_read_parquet_with_explicit_missing_time_column(tmp_path: Path) -> None:
    path = tmp_path / "p.parquet"
    pd.DataFrame({"hour": [0, 1], "load:0": [1.0, 2.0]}).to_parquet(path)
    with pytest.raises(ProfileError, match="time_column 'when' not in table"):
        read_profile_table(path, ProfileSpec(time_column="when"))
    wide = read_profile_table(path, ProfileSpec(time_column="hour", layout="wide"))
    assert list(wide.columns) == ["load:0"]


def test_read_forced_long_layout_errors(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    pd.DataFrame({"a": [1], "value": [2.0]}).to_csv(path, index=False)
    with pytest.raises(ProfileError, match="need a time column"):
        read_profile_table(path, ProfileSpec(layout="long"))
    pd.DataFrame({"hour": [1], "value": [2.0]}).to_csv(path, index=False)
    with pytest.raises(ProfileError, match="need id and value columns"):
        read_profile_table(path, ProfileSpec(layout="long"))
    pd.DataFrame({"hour": [1, 1], "id": ["a", "a"], "value": [1.0, 2.0]}).to_csv(
        path, index=False
    )
    with pytest.raises(ProfileError, match="duplicate"):
        read_profile_table(path, ProfileSpec())


def test_read_long_with_explicit_id_column(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    pd.DataFrame(
        {"time": [0, 0, 1, 1], "who": ["a", "b", "a", "b"], "value": [1, 2, 3, 4]}
    ).to_csv(path, index=False)
    wide = read_profile_table(path, ProfileSpec(id_column="who"))
    assert wide.to_dict("list") == {"a": [1.0, 3.0], "b": [2.0, 4.0]}


def test_read_wide_drops_ignored_and_text_columns(tmp_path: Path, caplog) -> None:
    path = tmp_path / "p.csv"
    pd.DataFrame(
        {"hour": [1, 0], "a": [1.0, 2.0], "note": ["x", "y"], "skip": [0, 0]}
    ).to_csv(path, index=False)
    wide = read_profile_table(path, ProfileSpec(ignore=["skip"]))
    assert list(wide.columns) == ["a"]
    assert wide["a"].tolist() == [2.0, 1.0]  # sorted by hour
    assert "non-numeric column" in caplog.text


def test_read_wide_without_time_column(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    pd.DataFrame({"a": [1.0, 2.0]}).to_csv(path, index=False)
    wide, times = read_profile_table_with_times(path, ProfileSpec())
    assert wide["a"].tolist() == [1.0, 2.0]
    assert times is None


def test_read_missing_values_rejected(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_text("hour,a\n0,1\n1,\n")
    with pytest.raises(ProfileError, match="missing values"):
        read_profile_table(path, ProfileSpec())


def test_as_timestamps_unparseable(caplog) -> None:
    assert _as_timestamps(pd.Series(["2023-01-01", "garbage"])) is None
    assert "not parseable as dates" in caplog.text
    assert _as_timestamps(None) is None


# ── ConstControl extraction ─────────────────────────────────────────────


def _ctrl(**kw: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(**kw)


def test_profiles_from_net_controllers_edge_cases(caplog) -> None:
    net = types.SimpleNamespace(controller=None)
    assert profiles_from_net_controllers(net).empty
    src = types.SimpleNamespace(df=pd.DataFrame({"p": [1.0, 2.0], "q": [1.0] * 2}))
    short = types.SimpleNamespace(df=pd.DataFrame({"s": [1.0]}))
    objs = [
        _ctrl(element="line", variable="p_mw", data_source=src),  # bad element
        _ctrl(element="load", variable="p_mw", data_source=None),  # no source
        _ctrl(
            element="load",
            variable="p_mw",
            data_source=src,
            element_index=[0, 1],
            profile_name=["p", "missing"],
            scale_factor=2.0,
        ),
    ]
    net = types.SimpleNamespace(controller=pd.DataFrame({"object": objs}))
    out = profiles_from_net_controllers(net)
    assert out.to_dict("list") == {"load:0": [2.0, 4.0]}
    assert "not in data source" in caplog.text
    objs.append(
        _ctrl(
            element="sgen",
            variable="max_p_mw",
            data_source=short,
            element_index=[0],
            profile_name=["s"],
            scale_factor=None,
        )
    )
    net = types.SimpleNamespace(controller=pd.DataFrame({"object": objs}))
    with pytest.raises(ProfileError, match="different lengths"):
        profiles_from_net_controllers(net)
    only_bad = types.SimpleNamespace(controller=pd.DataFrame({"object": objs[:2]}))
    assert profiles_from_net_controllers(only_bad).empty


# ── element resolution ──────────────────────────────────────────────────


def test_element_base_mw_falls_back_to_p_mw() -> None:
    net = _net()
    fixed = int(net.sgen.index[net.sgen["name"] == "fixed"][0])
    assert element_base_mw(net, "sgen", fixed) == 7.0


def test_bus_lookup_without_names() -> None:
    net = pn.case9()
    net.bus["name"] = None
    lookup, named = _bus_lookup(net)
    assert not named
    assert lookup["0"] == 0


def test_resolve_target_selectors() -> None:
    net = _net()
    assert not _resolve_target(net, ColumnTarget(element="load", by="name"))
    assert _resolve_target(net, ColumnTarget(element="load", by="index", ref="1")) == [
        1
    ]
    assert not _resolve_target(net, ColumnTarget(element="load", by="index", ref="x"))
    assert not _resolve_target(net, ColumnTarget(element="load", by="bus", ref="999"))
    hits = _resolve_target(net, ColumnTarget(element="sgen", by="bus_index", ref="4"))
    assert hits == [0]
    assert not _resolve_target(
        net, ColumnTarget(element="sgen", by="bus_index", ref="99")
    )
    net.bus["zone"] = [1.0] * 4 + [2.0] * 5
    assert _resolve_target(net, ColumnTarget(element="sgen", by="zone", ref="2"))
    net.bus = net.bus.drop(columns=["zone"])
    assert not _resolve_target(net, ColumnTarget(element="sgen", by="zone", ref="2"))


def test_target_for_column_default_and_unnamed_tables() -> None:
    net = _net()
    spec = ProfileSpec(default=ColumnTarget(element="sgen", by="name", units="pu"))
    tgt = _target_for_column(net, "wind1", spec)
    assert tgt is not None and tgt.element == "sgen" and tgt.units == "pu"
    net.gen = net.gen.drop(columns=["name"])
    assert _target_for_column(net, "nobody", ProfileSpec()) is None


def test_generator_factors_skip_unrated_and_clip(caplog) -> None:
    net = _net()
    dead = int(net.sgen.index[net.sgen["name"] == "dead"][0])
    prof = ResolvedProfiles(
        n_hours=2,
        gen_mw={("sgen", 0): np.array([50.0, 150.0]), ("sgen", dead): np.ones(2)},
    )
    factors = prof.generator_factors(net)
    assert list(factors) == [("sgen", 0)]
    assert factors[("sgen", 0)].tolist() == [0.5, 1.0]
    assert "no rated MW" in caplog.text
    assert "clipped to 1" in caplog.text


def test_resolve_profiles_errors_and_zero_base_share() -> None:
    net = _net()
    with pytest.raises(ProfileError, match="negative values"):
        resolve_profiles(net, pd.DataFrame({"load:0": [-1.0]}), ProfileSpec())
    with pytest.raises(ProfileError, match="no column matched"):
        resolve_profiles(net, pd.DataFrame({"zzz": [1.0]}), ProfileSpec())
    dead = int(net.sgen.index[net.sgen["name"] == "dead"][0])
    out = resolve_profiles(
        net, pd.DataFrame({f"sgen:{dead}": [5.0, 6.0]}), ProfileSpec()
    )
    assert out.gen_mw[("sgen", dead)].tolist() == [5.0, 6.0]  # even split of 1


def test_resolve_profiles_accumulates_repeated_targets() -> None:
    net = _net()
    wide = pd.DataFrame({"load:0": [10.0], "load@all": [3.0], "sgen:0": [0.5]})
    spec = ProfileSpec(columns={"sgen:0": ColumnTarget(element="sgen", ref="0")})
    out = resolve_profiles(net, wide, spec)
    assert out.load_mw[0][0] > 10.0
    many = pd.DataFrame({f"x{i}": [1.0] for i in range(12)} | {"load:0": [1.0]})
    out = resolve_profiles(net, many, ProfileSpec())
    assert len(out.unmatched_columns) == 12
