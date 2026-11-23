# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of the case-assembly helpers in :mod:`plexos2gtopt.parsers`.

Plant families / caps, the native fuel-offtake caps, the boundary cut, and
the hydro min/max auto-promotion to commitments.
"""

# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt import plexos_block_layout as layout
from plexos2gtopt.entities import (
    CommitmentSpec,
    FuelSpec,
    GeneratorSpec,
    PlexosCase,
    UserConstraintSpec,
)
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


def _g(
    name: str, pmax: float = 0.0, profile: tuple[float, ...] = (), **kw: object
) -> GeneratorSpec:
    return GeneratorSpec(
        object_id=hash(name) & 0xFFFF,
        name=name,
        bus_name="b",
        pmax=pmax,
        pmax_profile=profile,
        **kw,  # type: ignore[arg-type]
    )


def test_detect_plant_families_profile_activity() -> None:
    """A profile with a positive entry marks a variant active."""
    gens = (_g("P_GN_A", 0.0, (0.0, 5.0)), _g("P_GN_B", 10.0), _g("SOLO"))
    assert set(P._detect_plant_families(gens)) == {"P"}


def test_extract_plants_uses_profile_peak() -> None:
    """Plant pmax is the peak of the variants' profiles."""
    case = PlexosCase(
        bundle=None,  # type: ignore[arg-type]
        generators=(_g("P_GN_A", 0.0, (3.0, 7.0)), _g("P_GN_B", 5.0)),
    )
    (plant,) = P.extract_plants(case)
    assert plant.pmax == pytest.approx(7.0)


def test_config_mutex_groups_edges() -> None:
    """No Gen->Constraint collection; dangling gens; single-member groups."""
    kit = DbKit()
    kit.obj("Constraint", "A_Uniq")
    assert not P._extract_config_mutex_groups(kit.db)
    kit = DbKit()
    a = kit.obj("Constraint", "A_Uniq")
    b = kit.obj("Constraint", "B_Uniq")
    g1 = kit.obj("Generator", "G1")
    g2 = kit.obj("Generator", "G2")
    for g in (g1, g2):
        kit.mem("Generator", "Constraint", g, a, coll_name="Constraints")
    kit.mem("Generator", "Constraint", g1, b, coll_name="Constraints")
    kit.mem("Generator", "Constraint", 999, b, coll_name="Constraints")
    assert P._extract_config_mutex_groups(kit.db) == [
        ("A_Uniq", frozenset({"G1", "G2"}))
    ]


def test_plant_cap_ucs_profiles_commit_mutex_and_single_active() -> None:
    """Profile peaks, the commitment mutex and <2-active groups."""
    gens = (
        _g("P-TG_GN_A", 0.0, (0.0, 120.0)),
        _g("P-TG_GN_B", 100.0),
        _g("Q-TG_GN_A", 50.0),
        _g("Q-TG_GN_B", 0.0),
    )
    case = PlexosCase(
        bundle=None,  # type: ignore[arg-type]
        generators=gens,
        commitments=(
            CommitmentSpec(generator_name="P-TG_GN_A"),
            CommitmentSpec(generator_name="P-TG_GN_B"),
        ),
    )
    mutex = (
        ("P_Uniq", frozenset({"P-TG_GN_A", "P-TG_GN_B"})),
        ("Q_Uniq", frozenset({"Q-TG_GN_A", "Q-TG_GN_B"})),
    )
    by = {u.name: u for u in P._build_plant_cap_ucs(case, mutex)}
    assert by["PlantCap_P"].expression.endswith("<= 120.000000")
    assert by["PlantCommit_P"].expression.endswith("<= 1")
    assert "PlantCap_Q" not in by
    assert not P._build_plant_cap_ucs(
        PlexosCase(bundle=None, generators=(_g("X", 1.0),))  # type: ignore[arg-type]
    )


def test_native_fuel_offtake_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binding caps set Fuel.max_offtake; sentinels / no caps pass through."""
    cache = tmp_path / "cache"
    cache.mkdir()
    b = bundle(tmp_path, accdb_cache_dir=cache)
    fuels = (FuelSpec(object_id=1, name="Gas_A"), FuelSpec(object_id=2, name="Gas_B"))
    monkeypatch.setattr(layout, "extract_fuel_offtake_caps", lambda _d: None)
    assert P._apply_native_fuel_offtake_caps(b, fuels) is fuels
    monkeypatch.setattr(
        layout, "extract_fuel_offtake_caps", lambda _d: {"Gas_A": (1e16, 168.0)}
    )
    assert P._apply_native_fuel_offtake_caps(b, fuels) is fuels
    monkeypatch.setattr(
        layout, "extract_fuel_offtake_caps", lambda _d: {"Gas_A": (500.0, 168.0)}
    )
    out = P._apply_native_fuel_offtake_caps(b, fuels)
    assert out[0].max_offtake == pytest.approx(500.0)
    assert out[0].max_offtake_cost == pytest.approx(P._FUEL_OFFTAKE_SOFT_PENALTY)
    assert out[1] is fuels[1]


def test_boundary_cut(tmp_path: Path) -> None:
    """FCF intercept + slopes; all-zero slopes -> None."""
    hdr = "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\n"
    b = bundle(
        tmp_path,
        {"Hydro_StoWaterValues.csv": hdr + "FCF,2026,1,1,1,9\nR1,2026,1,1,1,3\n"},
    )
    cut = P._extract_boundary_cut(b)
    assert cut is not None
    assert cut.fcf == pytest.approx(9.0)
    assert cut.slopes == {"R1": 3.0}
    (tmp_path / "Hydro_StoWaterValues.csv").write_text(
        hdr + "FCF,2026,1,1,1,9\nR1,2026,1,1,1,0\n"
    )
    assert P._extract_boundary_cut(bundle(tmp_path)) is None


def test_combine_bound_profiles_branches() -> None:
    """Empty / broadcast / mismatched / element-wise combinations."""
    assert P._combine_bound_profiles((), (), 3.0, reducer=max) == ()
    assert P._combine_bound_profiles((1.0, 5.0), (), 3.0, reducer=max) == (3.0, 5.0)
    assert P._combine_bound_profiles((), (1.0, 5.0), 3.0, reducer=min) == (1.0, 3.0)
    assert P._combine_bound_profiles((1.0,), (2.0, 4.0), 0.0, reducer=max) == (2.0, 4.0)
    assert P._combine_bound_profiles((1.0, 2.0, 3.0), (2.0,), 0.0, reducer=max) == (
        1.0,
        2.0,
        3.0,
    )


def _uc(
    name: str, expression: str, rhs_profile: tuple[float, ...] = ()
) -> UserConstraintSpec:
    return UserConstraintSpec(name=name, expression=expression, rhs_profile=rhs_profile)


def test_auto_promote_hydro_min_max() -> None:
    """Hydro min/max single-gen UCs become commitments; filters apply."""
    gens = (_g("H1", 50.0), _g("H2", 50.0), _g("T1", 50.0, fuel_names=("Gas",)))
    base = (
        _uc("PLANTmin", 'generator("H1").generation >= 5', (5.0, 6.0)),
        _uc("PLANTmin", 'generator("H1").generation >= 4', (7.0, 1.0)),
        _uc("PLANTmax", 'generator("H1").generation <= 40'),
        _uc("PLANTmax", 'generator("H1").generation <= 30'),
        _uc("TWOmin", '2 * generator("H2").generation >= 5'),
        _uc("THERMmin", 'generator("T1").generation >= 5'),
        _uc("NOTMATCH", 'generator("H1").generation >= 5'),
        _uc("MULTImin", 'generator("H1").generation + generator("H2").generation >= 5'),
        _uc("PLANTmaxramp", 'generator("H1").generation >= 5'),
    )
    hydro = (
        _uc("discharge_PLANTmax", "x <= 1"),
        _uc("discharge_ODDNAME", "x <= 1"),
        _uc("other", "x <= 1"),
        _uc("discharge_ELSEmin", "x <= 1"),
    )
    cmts, drops = P._auto_promote_hydro_min_max_to_commitments(base, hydro, gens, ())
    (cmt,) = cmts
    assert cmt.generator_name == "H1"
    assert cmt.pmin == pytest.approx(5.0)
    assert cmt.pmin_profile == (7.0, 6.0)
    assert "discharge_PLANTmax" in drops and "PLANTmax" in drops
    assert "discharge_ELSEmin" not in drops


def test_auto_promote_no_matches() -> None:
    """No hydro gens / no matches / unsupported op -> nothing promoted."""
    assert P._auto_promote_hydro_min_max_to_commitments((), (), (), ()) == (
        (),
        frozenset(),
    )
    gens = (_g("H1", 50.0),)
    assert P._auto_promote_hydro_min_max_to_commitments(
        (_uc("Amin", 'generator("H1").generation >= 1'),),
        (),
        gens,
        (CommitmentSpec(generator_name="H1"),),
    ) == ((), frozenset())
    # A ``min`` UC with a ``<=`` op matches the stem but promotes nothing.
    out = P._auto_promote_hydro_min_max_to_commitments(
        (_uc("Amin", 'generator("H1").generation <= 1'),), (), gens, ()
    )
    assert out[1] == frozenset()
