# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of commitment, discharge-UC and pinned FlowRight builders."""

# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt.entities import GeneratorSpec
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle

_HDR = "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE\n"


def _long(rows: dict[str, list[float]]) -> str:
    body = _HDR
    for name, vals in rows.items():
        for i, v in enumerate(vals, start=1):
            body += f"{name},2026,1,1,{i},1,{v}\n"
    return body


def _setup(
    names: tuple[str, ...],
) -> tuple[DbKit, dict[str, int]]:
    kit = DbKit()
    ids = {n: kit.obj("Generator", n) for n in names}
    return kit, ids


def _spec(oid: int, name: str, **kw: object) -> GeneratorSpec:
    return GeneratorSpec(object_id=oid, name=name, bus_name="n1", **kw)  # type: ignore[arg-type]


def test_max_starts_and_fixed_load(tmp_path: Path) -> None:
    """Max Starts scope + Fixed Load zeroes the MSL and seeds initial power."""
    kit, ids = _setup(("G",))
    kit.sysprop("Generator", ids["G"], "Max Starts Week", 3.0)
    kit.sysprop("Generator", ids["G"], "Min Up Time", 2.0)
    kit.sysprop("Generator", ids["G"], "Min Stable Level", 20.0)
    gen = _spec(ids["G"], "G", pmax=100.0, fixed_load_profile=(30.0, 0.0))
    b = bundle(tmp_path, {"Gen_MinStableLevel.csv": _long({"G": [10.0, 20.0]})})
    (cmt,) = P.extract_commitments(kit.db, b, (gen,))
    assert cmt.max_starts == 3
    assert cmt.starts_scope == "week"
    assert cmt.pmin == 0.0
    assert cmt.initial_power == pytest.approx(30.0)
    assert not cmt.pmin_profile


def test_msl_capped_by_min_positive_pmax(tmp_path: Path) -> None:
    """MSL above the smallest positive pmax is capped; profile kept."""
    kit, ids = _setup(("G",))
    gen = _spec(ids["G"], "G", pmax=50.0, pmax_profile=(50.0, 30.0, 0.0))
    b = bundle(tmp_path, {"Gen_MinStableLevel.csv": _long({"G": [40.0, 20.0]})})
    (cmt,) = P.extract_commitments(kit.db, b, (gen,))
    assert cmt.pmin == pytest.approx(30.0)
    assert cmt.pmin_profile[:2] == (30.0, 20.0)


def test_msl_profile_collapses_when_capped_flat(tmp_path: Path) -> None:
    """A varying MSL that caps to a flat series leaves no profile."""
    kit, ids = _setup(("G",))
    gen = _spec(ids["G"], "G", pmax=10.0)
    b = bundle(
        tmp_path, {"Gen_MinStableLevel.csv": _long({"G": [40.0, 20.0] + [30.0] * 22})}
    )
    (cmt,) = P.extract_commitments(kit.db, b, (gen,))
    assert not cmt.pmin_profile


def test_initial_power_capped_by_first_block_pmax(tmp_path: Path) -> None:
    """Initial generation above pmax[0] + ramp is capped; tiny values zeroed."""
    kit, ids = _setup(("G", "T"))
    for gid in ids.values():
        kit.sysprop("Generator", gid, "Max Ramp Down", 0.5)
    gens = (
        _spec(
            ids["G"],
            "G",
            pmax=100.0,
            pmax_profile=(40.0, 100.0),
            initial_generation=90.0,
        ),
        _spec(ids["T"], "T", pmax=100.0, initial_generation=5e-4),
    )
    b = bundle(tmp_path, {"Gen_IniUnits.csv": _long({"G": [1.0], "T": [1.0]})})
    out = {c.generator_name: c for c in P.extract_commitments(kit.db, b, gens)}
    assert out["G"].initial_power == pytest.approx(40.0)
    assert out["T"].initial_power == 0.0


def test_noload_only_generator_has_no_commitment(tmp_path: Path) -> None:
    """Only no-load cost / initial hours do not justify a commitment row."""
    kit, ids = _setup(("G",))
    kit.sysprop("Generator", ids["G"], "Heat Rate Base", 2.0)
    gen = _spec(ids["G"], "G", pmax=10.0, fuel_price_override=3.0)
    assert not P.extract_commitments(kit.db, bundle(tmp_path), (gen,))


# --------------------------------------------------------------------------
# Pinned FlowRights
# --------------------------------------------------------------------------


def test_synthesise_pinned_flow_rights(caplog: pytest.LogCaptureFixture) -> None:
    """Known sources become FlowRights; unknown ones are dropped loudly."""
    targets = [
        ("Riego_A", "A", 3.0, (3.0, 0.0)),
        ("Filt_B", "B", 2.0, (2.0, 2.0)),
        ("Riego_X", "X", 1.0, ()),
    ]
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        out = P._synthesise_pinned_flow_rights(targets, frozenset({"A", "B"}))
    by = {f.name: f for f in out}
    assert set(by) == {"soft_Riego_A", "soft_Filt_B"}
    assert by["soft_Riego_A"].target_profile == (3.0, 0.0)
    assert by["soft_Filt_B"].target_profile == ()
    assert by["soft_Filt_B"].fmax == pytest.approx(20.0)
    assert "DROPPED 1" in caplog.text
    assert not P._synthesise_pinned_flow_rights([], frozenset())
