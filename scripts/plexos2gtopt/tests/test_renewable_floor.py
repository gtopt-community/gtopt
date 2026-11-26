# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--renewable-floors``: clean frees the Min Stable Level / forced-ON
floors of zero-cost solar, wind and run-of-river commitments; ``plexos``
(``--legacy-profile cen``) keeps them."""

from __future__ import annotations

import logging

import pytest

from plexos2gtopt._renewable_floor import (
    RENEWABLE_FLOOR_MODES,
    curtailable_kind,
    free_renewable_floors,
    storage_hydro_generators,
)
from plexos2gtopt.entities import (
    BundleSpec,
    CommitmentSpec,
    GeneratorSpec,
    PlexosCase,
    ReservoirSpec,
    TurbineSpec,
)
from plexos2gtopt.gtopt_writer import build_commitment_array
from plexos2gtopt.main import make_parser


def _gen(name: str, tag: str, **kw: object) -> GeneratorSpec:
    return GeneratorSpec(
        object_id=1,
        name=name,
        bus_name="n1",
        type_tag=tag,
        **kw,  # type: ignore[arg-type]
    )


def _cmt(name: str, **kw: object) -> CommitmentSpec:
    return CommitmentSpec(generator_name=name, **kw)  # type: ignore[arg-type]


def _case() -> PlexosCase:
    gens = (
        # TAMARICO_FV-like: PLEXOS MSL 5.17 clamped to 0.43, Commit -1.
        _gen("PV", "renewable:solar", vom_charge=0.1),  # small VO&M: in scope
        _gen("WIND", "wind"),  # parser spelling (bare family)
        _gen("ROR", "renewable:hydro"),
        _gen("DAM", "renewable:hydro"),
        _gen("COAL", "thermal:carbon", heat_rate=10.0, fuel_price_override=3.0),
        # Wind with a start-up cost: not cost-free, never touched.
        _gen("WIND_SU", "renewable:wind"),
    )
    cmts = (
        _cmt("PV", pmin=0.43, commit_status_profile=(0, -1, -1, 0)),
        _cmt("WIND", pmin=2.0, pmin_profile=(2.0, 3.0), commit_status_profile=(1, 1)),
        _cmt("ROR", commit_status_profile=(1, 1, 0)),
        _cmt("DAM", pmin=40.0),
        _cmt("COAL", pmin=100.0, commit_status_profile=(1, 1)),
        _cmt("WIND_SU", pmin=1.0, startup_cost=50.0),
        # Hydro <plant>min constraint commitment: origin != generator.
        _cmt("ROR", pmin=5.0, origin="hydro_min"),
    )
    return PlexosCase(
        bundle=BundleSpec(),
        generators=gens,
        commitments=cmts,
        reservoirs=(
            ReservoirSpec(object_id=1, name="LAKE", emax=100.0),
            ReservoirSpec(object_id=2, name="POND", emax=0.0),
        ),
        turbines=(
            TurbineSpec(generator_name="DAM", reservoir_name="LAKE"),
            TurbineSpec(generator_name="ROR", reservoir_name="POND"),
        ),
    )


def _by_gen(case: PlexosCase) -> dict[tuple[str, str], CommitmentSpec]:
    return {(c.generator_name, c.origin): c for c in case.commitments}


def test_classification() -> None:
    case = _case()
    storage = storage_hydro_generators(case)
    assert storage == frozenset({"DAM"})
    kinds = {g.name: curtailable_kind(g, storage) for g in case.generators}
    assert kinds == {
        "PV": "solar",
        "WIND": "wind",
        "ROR": "ror",
        "DAM": None,
        "COAL": None,
        "WIND_SU": "wind",
    }
    csp = _gen("CERRO_DOMINADOR_CS", "renewable:solar")
    assert curtailable_kind(csp, storage) is None


def test_clean_frees_floors(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        out, report = free_renewable_floors(_case(), "free")
    c = _by_gen(out)
    pv = c[("PV", "generator")]
    assert pv.pmin == 0.0
    assert pv.commit_status_profile == (0, -1, -1, 0)  # forced-OFF kept
    wind = c[("WIND", "generator")]
    assert (wind.pmin, wind.pmin_profile, wind.commit_status_profile) == (0.0, (), ())
    ror = c[("ROR", "generator")]
    assert ror.commit_status_profile == (-1, -1, 0)
    # Untouched: storage hydro, thermal, costly wind, constraint commitment.
    assert c[("DAM", "generator")].pmin == 40.0
    assert c[("COAL", "generator")].commit_status_profile == (1, 1)
    assert c[("WIND_SU", "generator")].pmin == 1.0
    assert c[("ROR", "hydro_min")].pmin == 5.0
    assert report.counts() == {
        "pmin": {"solar": 1, "wind": 1},
        "forced_on": {"ror": 1, "wind": 1},
    }
    assert "renewable floors freed" in caplog.text


def test_clean_writer_output_has_no_floor() -> None:
    out, _ = free_renewable_floors(_case(), "free")
    rows = {r["generator"]: r for r in build_commitment_array(out.commitments[:-1])}
    assert "pmin" not in rows["PV"]
    assert "pmin" not in rows["WIND"]
    assert "must_run" not in rows["WIND"]
    assert rows["DAM"]["pmin"] == 40.0
    assert rows["COAL"]["must_run"] is True


def test_plexos_mode_keeps_everything() -> None:
    case = _case()
    out, report = free_renewable_floors(case, "plexos")
    assert out is case
    assert report.counts() == {"pmin": {}, "forced_on": {}}


def test_no_change_returns_same_case() -> None:
    case = PlexosCase(
        bundle=BundleSpec(),
        generators=(_gen("PV", "renewable:solar"),),
        commitments=(_cmt("PV", commit_status_profile=(0, -1)),),
    )
    out, _ = free_renewable_floors(case)
    assert out is case


def test_unknown_mode() -> None:
    with pytest.raises(ValueError, match="renewable-floors"):
        free_renewable_floors(_case(), "bogus")


def test_cli_default_and_choices() -> None:
    args = make_parser().parse_args(["bundle"])
    assert args.renewable_floors == "free"
    assert RENEWABLE_FLOOR_MODES == ("free", "plexos")
    args = make_parser().parse_args(["bundle", "--renewable-floors", "plexos"])
    assert args.renewable_floors == "plexos"
