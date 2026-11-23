# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of :func:`plexos2gtopt.parsers.extract_case` assembly.

The hydro / generator extractors are replaced by stubs returning
synthetic specs so the case-level glue (endpoint classification,
inactive-turbine drop, pondage demotion, ``GTOPT_RESERVOIR_SPILL=strict``,
Region VoLL, the Emission warning and the hydro min/max auto-promotion)
can be exercised on a tiny in-memory database.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt.entities import (
    GeneratorSpec,
    ReservoirSpec,
    TurbineSpec,
    UserConstraintSpec,
    WaterwaySpec,
)
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    kit: DbKit,
    *,
    reservoirs: tuple[ReservoirSpec, ...],
    waterways: tuple[WaterwaySpec, ...],
    generators: tuple[GeneratorSpec, ...],
    turbines: tuple[TurbineSpec, ...],
    drains: dict[str, dict[str, float | None]] | None = None,
) -> None:
    def _waterways(*_a: Any, **kw: Any) -> tuple[WaterwaySpec, ...]:
        out = kw.get("junction_drain_configs_out")
        if out is not None and drains:
            out.update(drains)
        return waterways

    monkeypatch.setattr(P, "load_xml", lambda _p: kit.db)
    monkeypatch.setattr(P, "extract_reservoirs", lambda *_a, **_k: reservoirs)
    monkeypatch.setattr(P, "extract_waterways", _waterways)
    monkeypatch.setattr(P, "extract_generators", lambda *_a, **_k: generators)
    monkeypatch.setattr(P, "extract_turbines", lambda *_a, **_k: turbines)


def _gen(oid: int, name: str, pmax: float) -> GeneratorSpec:
    return GeneratorSpec(object_id=oid, name=name, bus_name="n1", pmax=pmax)


def _hydro_case(monkeypatch: pytest.MonkeyPatch, kit: DbKit) -> None:
    reservoirs = (
        ReservoirSpec(object_id=1, name="REAL", emax=100.0, eini=50.0),
        ReservoirSpec(object_id=2, name="POND"),
    )
    waterways = (
        WaterwaySpec(
            object_id=10, name="W_sink", storage_from="REAL", storage_to="X_sink"
        ),
        WaterwaySpec(object_id=11, name="W_odd", storage_from="REAL", storage_to="ODD"),
        WaterwaySpec(
            object_id=12, name="W_odd2", storage_from="POND", storage_to="ODD"
        ),
        WaterwaySpec(object_id=13, name="Vert_REAL", storage_from="REAL"),
        WaterwaySpec(
            object_id=14, name="Spill_REAL", storage_from="REAL", fmax=0.0, fcost=5.0
        ),
        WaterwaySpec(object_id=15, name="Keep", storage_from="POND", fmax=10.0),
    )
    generators = (_gen(21, "H_ON", 50.0), _gen(22, "H_OFF", 0.0))
    turbines = (
        TurbineSpec(
            generator_name="H_ON", reservoir_name="REAL", production_factor=1.0
        ),
        TurbineSpec(
            generator_name="H_OFF", reservoir_name="REAL", production_factor=1.0
        ),
    )
    _patch(
        monkeypatch,
        kit,
        reservoirs=reservoirs,
        waterways=waterways,
        generators=generators,
        turbines=turbines,
        drains={"REAL": {"cost": 1.0}, "X_sink": {"cost": None}},
    )


@pytest.mark.parametrize("spill_mode", ["", "strict"])
def test_extract_case_hydro_glue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    spill_mode: str,
) -> None:
    """Endpoint classification, turbine drop, pondage demotion, strict spill."""
    monkeypatch.setenv("GTOPT_RESERVOIR_SPILL", spill_mode)
    kit = DbKit()
    _hydro_case(monkeypatch, kit)
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        case = P.extract_case(bundle(tmp_path))
    assert [r.name for r in case.reservoirs] == ["REAL"]
    assert {j.name for j in case.junctions} >= {"POND", "X_sink"}
    assert [t.generator_name for t in case.turbines] == ["H_ON"]
    assert "not recognised as sink/ocean names" in caplog.text
    assert "ODD" in caplog.text
    assert "dropped 1 turbine(s)" in caplog.text
    assert "demoted 1 pondage" in caplog.text
    ww = {w.name for w in case.waterways}
    if spill_mode == "strict":
        assert "removed Junction.drain on 1 real reservoirs" in caplog.text
        assert "Vert_REAL" not in ww
        assert "Spill_REAL" not in ww
        assert "Keep" in ww
    else:
        assert {"Vert_REAL", "Spill_REAL"} <= ww


def test_extract_case_strict_without_spill_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Strict mode with nothing to remove / drop logs nothing extra."""
    monkeypatch.setenv("GTOPT_RESERVOIR_SPILL", "strict")
    kit = DbKit()
    _patch(
        monkeypatch,
        kit,
        reservoirs=(ReservoirSpec(object_id=2, name="POND"),),
        waterways=(
            WaterwaySpec(object_id=10, name="W", storage_from="POND", fmax=5.0),
        ),
        generators=(),
        turbines=(),
    )
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        case = P.extract_case(bundle(tmp_path))
    assert not case.reservoirs
    assert [w.name for w in case.waterways] == ["W"]
    assert "GTOPT_RESERVOIR_SPILL=strict" not in caplog.text


def test_extract_case_voll_and_emission_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Distinct Region VoLLs pick the min; Emission w/o CO2 rates warns."""
    monkeypatch.delenv("GTOPT_RESERVOIR_SPILL", raising=False)
    kit = DbKit()
    for i, voll in enumerate((5000.0, 3000.0, 0.0)):
        rid = kit.obj("Region", f"R{i}")
        if voll:
            kit.sysprop("Region", rid, "VoLL", voll)
    kit.obj("Emission", "CO2")
    _patch(monkeypatch, kit, reservoirs=(), waterways=(), generators=(), turbines=())
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        case = P.extract_case(bundle(tmp_path))
    assert case.bundle.demand_fail_cost == pytest.approx(3000.0)
    assert "2 distinct VoLL values" in caplog.text
    assert "Emission object(s) present but no fuel" in caplog.text


def test_extract_case_auto_promotes_hydro_min(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hydro ``<plant>min`` UC becomes a commitment and is dropped."""
    monkeypatch.delenv("GTOPT_RESERVOIR_SPILL", raising=False)
    kit = DbKit()
    _patch(
        monkeypatch,
        kit,
        reservoirs=(),
        waterways=(),
        generators=(_gen(21, "H1", 50.0),),
        turbines=(),
    )
    uc = UserConstraintSpec(
        name="PLANTmin", expression='generator("H1").generation >= 5'
    )
    monkeypatch.setattr(P, "extract_user_constraints", lambda *_a, **_k: (uc,))
    case = P.extract_case(bundle(tmp_path))
    assert [c.generator_name for c in case.commitments] == ["H1"]
    assert all(u.name != "PLANTmin" for u in case.user_constraints)
