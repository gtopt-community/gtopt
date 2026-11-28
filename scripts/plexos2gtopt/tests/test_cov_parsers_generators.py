# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of :func:`plexos2gtopt.parsers.extract_generators`.

Covers the opt-in aux-use CSV, the env-gated PLEXOS-solution overrides
(``GTOPT_USE_PLEXOS_COMMIT`` / ``GTOPT_USE_PLEXOS_GEN_CAP`` — the accdb
cache readers are mocked), the degenerate-pmax floor, the materialised
heat-rate profile and the per-generator fuel-transport fallback.
"""

# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt import plexos_block_layout as layout
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle

_HDR = "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE\n"


def _rating(rows: dict[str, list[float]]) -> str:
    body = _HDR
    for name, vals in rows.items():
        for i, v in enumerate(vals, start=1):
            body += f"{name},2026,1,1,{i},1,{v}\n"
    return body


def _two_gen_db() -> tuple[DbKit, dict[str, int]]:
    kit = DbKit()
    n1 = kit.obj("Node", "n1")
    ids = {}
    for name in ("HYD", "THERM", "ORPHAN"):
        ids[name] = kit.obj("Generator", name)
    kit.mem("Generator", "Node", ids["HYD"], n1, coll_name="Nodes")
    kit.mem("Generator", "Node", ids["THERM"], n1, coll_name="Nodes")
    return kit, ids


def test_aux_use_csv_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Bad / empty / zero / over-cap rows are skipped; sample capped at 5."""
    monkeypatch.setenv("GTOPT_APPLY_GENERATION_AUX_USE", "1")
    kit, _ = _two_gen_db()
    aux = "Name,Value\nTHERM,abc\n,5\nHYD,0\nTHERM,4\n" + "".join(
        f"X{i},80\n" for i in range(6)
    )
    b = bundle(
        tmp_path,
        {"Gen_AuxUse.csv": aux, "Gen_Rating.csv": _rating({"THERM": [10.0]})},
    )
    with caplog.at_level(logging.WARNING, logger=P.logger.name):
        gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert "6 rows had Value" in caplog.text
    assert ", ..." in caplog.text
    assert "ORPHAN" not in gens  # no node attachment -> dropped
    assert gens["THERM"].pmax == pytest.approx(10.0)


def test_nameplate_skips_nonpositive(tmp_path: Path) -> None:
    """``nameplates_out`` only records positive finite Max Capacity."""
    kit, ids = _two_gen_db()
    kit.sysprop("Generator", ids["HYD"], "Max Capacity", 50.0)
    out: dict[str, float] = {}
    P.extract_generators(kit.db, bundle(tmp_path), nameplates_out=out)
    assert out == {"HYD": 50.0}


def test_degenerate_pmax_zeroes_profile(tmp_path: Path) -> None:
    """A sub-kW peak rating is floored to an offline unit."""
    kit, _ = _two_gen_db()
    b = bundle(tmp_path, {"Gen_Rating.csv": _rating({"THERM": [1e-4, 5e-4]})})
    gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["THERM"].pmax == 0.0
    assert all(v == 0.0 for v in gens["THERM"].pmax_profile)


def test_materialised_heat_rate_profile_and_transport_by_gen(tmp_path: Path) -> None:
    """Materialised models keep the full heat-rate series; transport by gen."""
    kit, ids = _two_gen_db()
    fuel = kit.obj("Fuel", "Gas_A")
    kit.mem("Generator", "Fuel", ids["THERM"], fuel, coll_name="Fuels")
    b = bundle(
        tmp_path,
        {
            "Gen_Rating.csv": _rating({"THERM": [10.0, 10.0]}),
            "Gen_HeatRate.csv": _rating({"THERM": [7.0, 8.0]}),
            "Gen_FuelTransportCharge.csv": _rating({"THERM": [3.0]}),
        },
        materialised_model="M",
    )
    gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["THERM"].heat_rate_profile[:2] == (7.0, 8.0)
    assert gens["THERM"].fuel_transport == pytest.approx(3.0)


def test_plexos_commit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Solved Units Generating rescale turbine profiles only."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_COMMIT", "1")
    cache = tmp_path / "cache"
    cache.mkdir()
    kit, ids = _two_gen_db()
    sto = kit.obj("Storage", "S")
    kit.mem("Generator", "Storage", ids["HYD"], sto, coll_name="Head Storage")
    monkeypatch.setattr(
        layout,
        "extract_generator_commit_per_period",
        lambda _d: {"HYD": {1: 2.0, 2: 1.0}, "THERM": {1: 1.0}},
    )
    b = bundle(
        tmp_path,
        {"Gen_Rating.csv": _rating({"HYD": [10.0, 10.0], "THERM": [5.0]})},
        accdb_cache_dir=cache,
    )
    gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["HYD"].pmax_profile[:2] == (10.0, 5.0)
    assert gens["THERM"].pmax == pytest.approx(5.0)


def test_plexos_commit_no_head_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without Head Storage no generator qualifies as a turbine."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_COMMIT", "1")
    cache = tmp_path / "cache"
    cache.mkdir()
    kit, _ = _two_gen_db()
    monkeypatch.setattr(
        layout, "extract_generator_commit_per_period", lambda _d: {"HYD": {1: 0.0}}
    )
    b = bundle(
        tmp_path, {"Gen_Rating.csv": _rating({"HYD": [10.0]})}, accdb_cache_dir=cache
    )
    gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["HYD"].pmax == pytest.approx(10.0)


def test_plexos_commit_zero_units_keeps_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turbine never committed keeps its rating profile."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_COMMIT", "1")
    cache = tmp_path / "cache"
    cache.mkdir()
    kit, ids = _two_gen_db()
    sto = kit.obj("Storage", "S")
    kit.mem("Generator", "Storage", ids["HYD"], sto, coll_name="Head Storage")
    monkeypatch.setattr(
        layout, "extract_generator_commit_per_period", lambda _d: {"HYD": {}}
    )
    b = bundle(
        tmp_path, {"Gen_Rating.csv": _rating({"HYD": [10.0]})}, accdb_cache_dir=cache
    )
    gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["HYD"].pmax == pytest.approx(10.0)


def _cached_tables(
    gen_cls: str | None = "2",
) -> dict[str, list[dict[str, str]] | None]:
    cls_rows = [{"class_id": "9", "name": "Node"}]
    if gen_cls is not None:
        cls_rows.append({"class_id": gen_cls, "name": "Generator"})
    return {
        "t_category": [
            {"category_id": "1", "name": "Hydro Gen Group A"},
            {"category_id": "2", "name": "Thermal"},
        ],
        "t_object": [
            {"class_id": "2", "category_id": "1", "name": "HYD"},
            {"class_id": "2", "category_id": "2", "name": "THERM"},
            {"class_id": "9", "category_id": "1", "name": "n1"},
        ],
        "t_class": cls_rows,
    }


def test_plexos_gen_cap_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Solved Generation caps hydro profiles (bootstrapping missing ones)."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_GEN_CAP", "1")
    cache = tmp_path / "cache"
    cache.mkdir()
    kit, _ = _two_gen_db()
    tables = _cached_tables()
    monkeypatch.setattr(
        layout,
        "extract_generator_generation_per_period",
        lambda _d: {"HYD": {1: 4.0, 3: 6.0}, "THERM": {1: 1.0}},
    )
    monkeypatch.setattr(layout, "_read_cached_csv", lambda _d, t: tables[t])
    b = bundle(
        tmp_path,
        {"Gen_Rating.csv": _rating({"HYD": [10.0, 10.0, 10.0]})},
        accdb_cache_dir=cache,
    )
    gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["HYD"].pmax_profile[:3] == (4.0, 10.0, 6.0)


def test_plexos_gen_cap_without_profile_or_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rating CSV seeds from the cap; no Generator class -> no hydro."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_GEN_CAP", "1")
    cache = tmp_path / "cache"
    cache.mkdir()
    kit, _ = _two_gen_db()
    tables = _cached_tables()
    monkeypatch.setattr(
        layout,
        "extract_generator_generation_per_period",
        lambda _d: {"HYD": {1: 4.0, 2: 6.0}},
    )
    monkeypatch.setattr(layout, "_read_cached_csv", lambda _d, t: tables[t])
    gens = {
        g.name: g
        for g in P.extract_generators(kit.db, bundle(tmp_path, accdb_cache_dir=cache))
    }
    assert gens["HYD"].pmax_profile[:2] == (4.0, 6.0)

    tables2 = _cached_tables(gen_cls=None)
    monkeypatch.setattr(layout, "_read_cached_csv", lambda _d, t: tables2[t])
    gens = {
        g.name: g
        for g in P.extract_generators(kit.db, bundle(tmp_path, accdb_cache_dir=cache))
    }
    assert gens["HYD"].pmax == 0.0

    tables3 = {"t_category": None, "t_object": None, "t_class": None}
    monkeypatch.setattr(layout, "_read_cached_csv", lambda _d, t: tables3[t])
    P.extract_generators(kit.db, bundle(tmp_path, accdb_cache_dir=cache))


def test_plexos_gen_cap_empty_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hydro generator with an empty cap dict is left untouched."""
    monkeypatch.setenv("GTOPT_USE_PLEXOS_GEN_CAP", "1")
    cache = tmp_path / "cache"
    cache.mkdir()
    kit, _ = _two_gen_db()
    tables = _cached_tables()
    monkeypatch.setattr(
        layout, "extract_generator_generation_per_period", lambda _d: {"HYD": {}}
    )
    monkeypatch.setattr(layout, "_read_cached_csv", lambda _d, t: tables[t])
    b = bundle(
        tmp_path, {"Gen_Rating.csv": _rating({"HYD": [10.0]})}, accdb_cache_dir=cache
    )
    gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["HYD"].pmax == pytest.approx(10.0)


def test_heat_rate_base_is_not_a_per_mwh_heat_rate(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``Heat Rate Base`` (no-load burn per hour) never becomes the
    per-MWh ``heat_rate``; ``Heat Rate Incr`` does when no scalar."""
    kit, ids = _two_gen_db()
    kit.sysprop("Generator", ids["THERM"], "Heat Rate Base", 40.0)
    kit.sysprop("Generator", ids["HYD"], "Heat Rate Base", 5.0)
    kit.sysprop("Generator", ids["HYD"], "Heat Rate Incr", 9.0)
    b = bundle(tmp_path, {"Gen_Rating.csv": _rating({"THERM": [10.0], "HYD": [5.0]})})
    with caplog.at_level(logging.WARNING, logger=P.logger.name):
        gens = {g.name: g for g in P.extract_generators(kit.db, b)}
    assert gens["THERM"].heat_rate == pytest.approx(0.0)
    assert "only 'Heat Rate Base'" in caplog.text
    assert gens["HYD"].heat_rate == pytest.approx(9.0)
