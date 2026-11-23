# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of the hydro extractors in :mod:`plexos2gtopt.parsers`.

Reservoirs, waterways (``Vert_*`` routing, forced flows, diversions),
junctions, turbines and head-dependent production factors.
"""

# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt import plexos_block_layout as layout
from plexos2gtopt.entities import ReservoirSpec
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


def _wide(rows: dict[str, list[float]], periods: int = 24) -> str:
    names = list(rows)
    body = "YEAR,MONTH,DAY,PERIOD," + ",".join(names) + "\n"
    for p in range(1, periods + 1):
        vals = [
            str(rows[n][p - 1] if p - 1 < len(rows[n]) else rows[n][-1]) for n in names
        ]
        body += f"2026,1,1,{p}," + ",".join(vals) + "\n"
    return body


# --------------------------------------------------------------------------
# demands / batteries (read-only paths)
# --------------------------------------------------------------------------


def test_bus_to_region_voll_dangling_memberships() -> None:
    """Dangling Node->Region rows and VoLL-less regions are skipped."""
    kit = DbKit()
    n1 = kit.obj("Node", "n1")
    n2 = kit.obj("Node", "n2")
    r1 = kit.obj("Region", "R1")
    r2 = kit.obj("Region", "R2")
    kit.sysprop("Region", r1, "VoLL", 5000.0)
    kit.mem("Node", "Region", n1, r1, coll_name="Region")
    kit.mem("Node", "Region", n2, r2, coll_name="Region")
    kit.mem("Node", "Region", n2, 9999, coll_name="Region")
    kit.mem("Node", "Region", 8888, r1, coll_name="Region")
    assert P._bus_to_region_voll(kit.db) == {"n1": 5000.0}


def test_extract_batteries_drop_env_and_aux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``GTOPT_DROP_BATTERIES`` drops all; ``_AUX`` / unattached are skipped."""
    kit = DbKit()
    n1 = kit.obj("Node", "n1")
    b1 = kit.obj("Battery", "B1_AUX")
    kit.obj("Battery", "B2")  # no node
    kit.mem("Battery", "Node", b1, n1, coll_name="Nodes")
    monkeypatch.setenv("GTOPT_DROP_BATTERIES", "1")
    assert not P.extract_batteries(kit.db, bundle(tmp_path))
    monkeypatch.delenv("GTOPT_DROP_BATTERIES")
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        assert not P.extract_batteries(kit.db, bundle(tmp_path))
    assert "_AUX" in caplog.text


# --------------------------------------------------------------------------
# reservoirs
# --------------------------------------------------------------------------


def test_reservoirs_lng_vert_penalty_and_solution_efin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LNG storages dropped; Vert_* penalties; PLEXOS-solution efin pin."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_EFIN", "1")
    monkeypatch.setattr(
        layout, "extract_storage_solution_efin", lambda _d: {"RES": 42.0}
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    kit = DbKit()
    res = kit.obj("Storage", "RES")
    kit.obj("Storage", "X_GNL_INF")
    kit.sysprop("Storage", res, "Max Volume", 100.0)
    kit.sysprop("Storage", res, "Initial Volume", 50.0)
    w1 = kit.obj("Waterway", "Vert_RES")
    kit.sysprop("Waterway", w1, "Max Flow Penalty", 7.0)
    kit.obj("Waterway", "Vert_X_GNL_INF")
    kit.obj("Waterway", "Gen_RES")
    w4 = kit.obj("Waterway", "Vert_OTHER")
    kit.sysprop("Waterway", w4, "Max Flow Penalty", 0.0)
    out = P.extract_reservoirs(kit.db, bundle(tmp_path, accdb_cache_dir=cache))
    (spec,) = out
    assert spec.name == "RES"
    assert spec.efin == pytest.approx(42.0)
    assert spec.spill_flow_penalty == pytest.approx(7.0)


def test_reservoirs_short_min_volume_csv(tmp_path: Path) -> None:
    """An emin series shorter than the horizon keeps efin = 0."""
    kit = DbKit()
    kit.obj("Storage", "RES")
    b = bundle(
        tmp_path, {"Hydro_MinVolume.csv": _wide({"RES": [5.0]}, periods=24)}, n_days=3
    )
    (spec,) = P.extract_reservoirs(kit.db, b)
    assert spec.efin == 0.0


def test_reservoir_pure_pondage_predicate() -> None:
    """All-zero envelope with no targets is pondage; eini > 0 is not."""
    assert P._reservoir_is_pure_pondage(ReservoirSpec(object_id=1, name="P"))
    assert not P._reservoir_is_pure_pondage(
        ReservoirSpec(object_id=1, name="P", eini=1.0)
    )


# --------------------------------------------------------------------------
# waterways
# --------------------------------------------------------------------------


def _ww_db() -> tuple[DbKit, dict[str, int]]:
    kit = DbKit()
    ids = {n: kit.obj("Storage", n) for n in ("A", "B", "C_GNL_INF")}
    return kit, ids


def _ww(kit: DbKit, name: str, src: int | None, dst: int | None) -> int:
    wid = kit.obj("Waterway", name)
    if src is not None:
        kit.mem("Waterway", "Storage", wid, src, coll_name="Storage From")
    if dst is not None:
        kit.mem("Waterway", "Storage", wid, dst, coll_name="Storage To")
    return wid


def test_waterways_skip_list_and_lng_source(tmp_path: Path) -> None:
    """Vert_ELTORO and LNG-sourced spillways are never emitted."""
    kit, ids = _ww_db()
    _ww(kit, "Vert_ELTORO", ids["A"], ids["B"])
    _ww(kit, "Vert_LNG", ids["C_GNL_INF"], ids["B"])
    _ww(kit, "Dangling", ids["A"], None)
    assert not P.extract_waterways(kit.db, bundle(tmp_path))


def test_waterways_vert_passthrough_and_drain_collapse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass-through Vert_* stays a waterway; real-source Vert_* collapses."""
    monkeypatch.setenv("GTOPT_SPILL_FCOST", "bad")
    monkeypatch.setenv("GTOPT_SPILL_FCOST_SCALE", "bad")
    kit, ids = _ww_db()
    _ww(kit, "Vert_A", ids["A"], ids["B"])
    w2 = _ww(kit, "Vert_B", ids["B"], None)
    kit.sysprop("Waterway", w2, "Max Flow", 30.0)
    kit.sysprop("Waterway", w2, "Max Flow Penalty", 2.0)
    drains: dict[str, dict[str, float | None]] = {}
    out = P.extract_waterways(
        kit.db,
        bundle(tmp_path),
        junction_drain_configs_out=drains,
        real_reservoir_names=frozenset({"X"}),
    )
    assert [w.name for w in out] == ["Vert_A"]
    assert out[0].fcost == 0.0
    assert drains == {"B": {"drain_capacity": 30.0, "drain_cost": 2.0}}


def test_waterways_drain_cost_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GTOPT_SPILL_FCOST`` / ``_SCALE`` override the collapsed drain cost."""
    monkeypatch.setenv("GTOPT_SPILL_FCOST", "4")
    monkeypatch.setenv("GTOPT_SPILL_FCOST_SCALE", "2.5")
    kit, ids = _ww_db()
    _ww(kit, "Vert_B", ids["B"], None)
    drains: dict[str, dict[str, float | None]] = {}
    P.extract_waterways(kit.db, bundle(tmp_path), junction_drain_configs_out=drains)
    assert drains["B"]["drain_cost"] == pytest.approx(10.0)


def test_waterways_spill_out_dropped_when_drain_enabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A real-reservoir Vert_* with drain enabled is dropped."""
    kit, ids = _ww_db()
    _ww(kit, "Vert_A", ids["A"], ids["B"])
    ocean: set[str] = set()
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        out = P.extract_waterways(
            kit.db,
            bundle(tmp_path),
            real_reservoir_names=frozenset({"A"}),
            ocean_sources_out=ocean,
        )
    assert not out
    assert "spill-out" in caplog.text
    assert not ocean


def test_waterways_vert_fcost_env_on_emitted_arc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Emitted Vert_* arcs honour the spill-cost env override + scale."""
    monkeypatch.setenv("GTOPT_SPILL_FCOST", "3")
    monkeypatch.setenv("GTOPT_SPILL_FCOST_SCALE", "2")
    kit, ids = _ww_db()
    _ww(kit, "Vert_A", ids["A"], ids["B"])
    (ww,) = P.extract_waterways(kit.db, bundle(tmp_path))
    assert ww.fcost == pytest.approx(6.0)
    monkeypatch.setenv("GTOPT_SPILL_FCOST", "x")
    monkeypatch.setenv("GTOPT_SPILL_FCOST_SCALE", "y")
    (ww,) = P.extract_waterways(kit.db, bundle(tmp_path))
    assert ww.fcost == 0.0


def test_waterways_forced_flows(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Forced flows pin arcs, synthesize sinks and feed FlowRight targets."""
    kit, ids = _ww_db()
    _ww(kit, "Filt_A", ids["A"], ids["B"])  # varying forced -> profile
    _ww(kit, "Riego_A", ids["A"], None)  # sink + forced target
    _ww(kit, "Caudal_Eco_A", ids["A"], None)  # sink, kept as waterway
    _ww(kit, "B_A", ids["A"], ids["B"])  # bypass
    _ww(kit, "Riego_B", ids["B"], ids["A"])  # inactive diversion
    flows = _wide(
        {
            "Filt_A": [1.0, 2.0],
            "Riego_A": [3.0],
            "Caudal_Eco_A": [4.0],
            "B_A": [5.0],
        }
    )
    targets: list[tuple[str, str, float, tuple[float, ...]]] = []
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        out = {
            w.name: w
            for w in P.extract_waterways(
                kit.db,
                bundle(tmp_path, {"Hydro_WaterFlows.csv": flows}),
                forced_targets_out=targets,
            )
        }
    assert out["Filt_A"].forced_flow_profile[:2] == (1.0, 2.0)
    assert out["Filt_A"].fmin == pytest.approx(2.0)
    assert out["B_A"].fmax == 0.0
    assert out["Riego_B"].inactive
    assert [t[0] for t in targets] == ["Riego_A", "Caudal_Eco_A"]
    assert "converted" in caplog.text


def test_waterways_forced_sink_kept_without_targets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without ``forced_targets_out`` the synthetic sink stays."""
    kit, ids = _ww_db()
    _ww(kit, "Riego_A", ids["A"], None)
    flows = _wide({"Riego_A": [3.0]})
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        (ww,) = P.extract_waterways(
            kit.db, bundle(tmp_path, {"Hydro_WaterFlows.csv": flows})
        )
    assert ww.storage_to == "Riego_A_sink"
    assert "synthesised 1 sink" in caplog.text


# --------------------------------------------------------------------------
# junctions / turbines / production factors
# --------------------------------------------------------------------------


def test_junctions_extra_names_and_drain_configs() -> None:
    """Extra junction names are de-duplicated; drain configs applied."""
    res = (ReservoirSpec(object_id=1, name="R"),)
    out = P.extract_junctions(
        res,
        ("R", "", "X_sink", "Y", "Y"),
        drain_configs={"Y": {"drain_capacity": 5.0, "drain_cost": None}},
    )
    by = {j.name: j for j in out}
    assert list(by) == ["R", "X_sink", "Y"]
    assert by["X_sink"].drain
    assert by["Y"].drain and by["Y"].drain_capacity == 5.0


def test_turbines_tail_storage_and_bad_csv(tmp_path: Path) -> None:
    """Tail Storage links resolve; a malformed efficiency CSV is ignored."""
    kit = DbKit()
    g = kit.obj("Generator", "G")
    head = kit.obj("Storage", "H")
    tail = kit.obj("Storage", "T")
    kit.mem("Generator", "Storage", g, head, coll_name="Head Storage")
    kit.mem("Generator", "Storage", 9999, head, coll_name="Head Storage")
    kit.mem("Generator", "Storage", g, tail, coll_name="Tail Storage")
    kit.mem("Generator", "Storage", g, 9999, coll_name="Tail Storage")
    kit.sysprop("Generator", g, "Efficiency Incr", 1.5)
    bad = "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\nG,2026,1,1,1,abc\n"
    (tur,) = P.extract_turbines(
        kit.db, bundle(tmp_path, {"Hydro_EfficiencyIncr.csv": bad})
    )
    assert tur.tail_reservoir_name == "T"
    assert tur.production_factor == pytest.approx(1.5)


def test_production_factor_curve_edges() -> None:
    """Dangling, system-less, head-effects-off and point-less gens skip."""
    kit = DbKit()
    head = kit.obj("Storage", "H")
    g_ok = kit.obj("Generator", "G_OK")
    g_off = kit.obj("Generator", "G_OFF")
    g_none = kit.obj("Generator", "G_NONE")
    g_nosys = kit.obj("Generator", "G_NOSYS", sys_member=False)
    for g in (g_ok, g_off, g_none, g_nosys, 9999):
        kit.mem("Generator", "Storage", g, head, coll_name="Head Storage")
    for band, (vol, eff) in enumerate(((10.0, 1.0), (20.0, 1.4)), start=1):
        kit.sysprop("Generator", g_ok, "Head Storage Volume Point", vol, band=band)
        kit.sysprop("Generator", g_ok, "Head Storage Efficiency Point", eff, band=band)
    kit.sysprop("Generator", g_ok, "Head Effects Method", 1)
    kit.sysprop("Generator", g_off, "Head Effects Method", 0)
    (spec,) = P.extract_reservoir_production_factors(kit.db, bundle(Path(".")))
    assert spec.turbine_name == "G_OK"
    assert spec.mean_production_factor == pytest.approx(1.2)
    assert spec.segments[0].slope == pytest.approx(0.04)


def test_bus_region_first_child_dangling() -> None:
    """A node whose first Region child is unknown is skipped."""
    kit = DbKit()
    n1 = kit.obj("Node", "n1")
    r1 = kit.obj("Region", "R1")
    kit.sysprop("Region", r1, "VoLL", 100.0)
    kit.mem("Node", "Region", n1, 9999, coll_name="Region")
    assert not P._bus_to_region_voll(kit.db)


def test_reservoirs_empty_solution_efin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty PLEXOS solution leaves efin from the inputs."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_EFIN", "1")
    monkeypatch.setattr(layout, "extract_storage_solution_efin", lambda _d: {})
    cache = tmp_path / "cache"
    cache.mkdir()
    kit = DbKit()
    kit.obj("Storage", "RES")
    (spec,) = P.extract_reservoirs(kit.db, bundle(tmp_path, accdb_cache_dir=cache))
    assert spec.efin == 0.0


def test_waterways_forced_to_real_sink_storage(tmp_path: Path) -> None:
    """A forced arc into a real ``*_sink`` storage feeds the targets list."""
    kit = DbKit()
    a = kit.obj("Storage", "A")
    sink = kit.obj("Storage", "A_sink")
    _ww(kit, "Riego_A", a, sink)
    targets: list[tuple[str, str, float, tuple[float, ...]]] = []
    flows = _wide({"Riego_A": [3.0]})
    out = P.extract_waterways(
        kit.db,
        bundle(tmp_path, {"Hydro_WaterFlows.csv": flows}),
        forced_targets_out=targets,
    )
    assert not out
    assert targets[0][:3] == ("Riego_A", "A", 3.0)


def test_turbines_zero_csv_and_undecodable_csv(tmp_path: Path) -> None:
    """All-zero CSV rows are ignored; undecodable CSVs are tolerated."""
    kit = DbKit()
    g = kit.obj("Generator", "G")
    head = kit.obj("Storage", "H")
    kit.mem("Generator", "Storage", g, head, coll_name="Head Storage")
    zero = "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\nG,2026,1,1,1,0\n"
    (tur,) = P.extract_turbines(
        kit.db, bundle(tmp_path, {"Hydro_EfficiencyIncr.csv": zero})
    )
    assert tur.production_factor == 0.0
    (tmp_path / "Hydro_EfficiencyIncr.csv").write_bytes(b"NAME,VALUE\n\xff\xfe\x00G\n")
    (tur,) = P.extract_turbines(kit.db, bundle(tmp_path))
    assert tur.generator_name == "G"


def test_production_factor_without_method_or_points() -> None:
    """No Head Effects Method property and no matching points -> empty."""
    kit = DbKit()
    head = kit.obj("Storage", "H")
    g = kit.obj("Generator", "G")
    g2 = kit.obj("Generator", "G2")
    kit.mem("Generator", "Storage", g, head, coll_name="Head Storage")
    kit.mem("Generator", "Storage", g2, head, coll_name="Head Storage")
    kit.sysprop("Generator", g2, "Head Storage Volume Point", 1.0)
    kit.sysprop("Generator", g2, "Head Storage Efficiency Point", 2.0)
    out = P.extract_reservoir_production_factors(kit.db, bundle(Path(".")))
    assert [s.turbine_name for s in out] == ["G2"]
    kit2 = DbKit()
    head = kit2.obj("Storage", "H")
    g = kit2.obj("Generator", "G")
    kit2.mem("Generator", "Storage", g, head, coll_name="Head Storage")
    gcoll = kit2.coll("System", "Generator", "Generators")
    kit2.prop(gcoll, "Head Storage Volume Point")
    kit2.prop(gcoll, "Head Storage Efficiency Point")
    assert not P.extract_reservoir_production_factors(kit2.db, bundle(Path(".")))
