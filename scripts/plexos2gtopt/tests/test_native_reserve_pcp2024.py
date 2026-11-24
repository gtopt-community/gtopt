# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--reserve-model native`` on a CEN PCP 2024-shaped case (regression).

The PCP 2024-12-15 bundle crashed the native transform with ``zip()
argument 2 is shorter than argument 1``: its PLEXOS solution layout has
144 hourly blocks over 6 days, while the ``Res_Requirement`` / CPF RHS
profiles carry the 168-hour week of the 7-day input horizon.  It also
uses the 2024 coefficient sets (CPF rows without BESS terms) and models
BESS as PLEXOS Storage objects: a discharge generator ``BAT_X`` plus a
charge generator ``BAT_X_LOAD``, no Battery object.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from plexos2gtopt._native_reserve import NativeReserveOptions, apply_native_reserve
from plexos2gtopt.entities import (
    BundleSpec,
    CommitmentSpec,
    DecisionVariableSpec,
    GeneratorSpec,
    PlexosCase,
    ReserveProvisionSpec,
    ReserveSpec,
    UserConstraintSpec,
)

_DV = 'decision_variable("{}").value'
_N_HOURS = 144  # PLEXOS solved 6 days
_WEEK = tuple(float(100 + h) for h in range(168))  # 7-day input profile


def _dv(name: str) -> str:
    return _DV.format(name)


def _uc(name: str, expr: str, **kw: object) -> UserConstraintSpec:
    return replace(UserConstraintSpec(name=name, expression=expr), **kw)


def _case(rhs: tuple[float, ...] = _WEEK) -> PlexosCase:
    ucs = [
        _uc(
            "Generation_Calculation",
            '1 * generator("G1").generation + 1 * generator("BAT_A").generation'
            ' - 1 * generator("BAT_A_LOAD").generation'
            f" - 1000 * {_dv('Generation_SEN')} >= 0",
        ),
        _uc(
            "Inertia_Calculation_e1",
            f'5 * commitment("uc_G1").status - 1000 * {_dv("Inertia_SEN")} >= -800',
        ),
        _uc(
            "CPF_Up10Calculation_I",
            f"1 * {_dv('CPF10s_Requirement')} + 29.8 * {_dv('Generation_SEN')}"
            f" + 7.075 * {_dv('Inertia_SEN')} - 0.747 * {_dv('MaxInjection_SEN')}"
            " >= 600",
            rhs_profile=rhs,
        ),
        _uc(
            "CPF_Up10MinProvision",
            '1 * reserve_provision("provision_G1").up'
            ' + 1 * reserve_provision("provision_BAT_A").up'
            f" + 1 * {_dv('CPF10s_VoRS')} - 1 * {_dv('CPF10s_Requirement')} >= 0",
        ),
        _uc(
            "G1_Comparison",
            f'1 * {_dv("MaxInjection_SEN")} - 1 * generator("G1").generation >= 0',
        ),
        _uc(
            "CSF_UpMinProvision",
            '1 * reserve_provision("provision_G1").up'
            ' + 1 * reserve_provision("provision_BAT_A").up >= 100',
            rhs_profile=rhs,
        ),
    ]
    return PlexosCase(
        bundle=BundleSpec(
            bundle_date="2024-12-15",
            n_days=6,
            block_layout=tuple((h,) for h in range(_N_HOURS)),
        ),
        generators=(
            GeneratorSpec(object_id=1, name="G1", bus_name="b", pmin=50.0),
            GeneratorSpec(object_id=2, name="BAT_A", bus_name="b"),
            GeneratorSpec(object_id=3, name="BAT_A_LOAD", bus_name="b"),
        ),
        commitments=(
            CommitmentSpec(generator_name="G1", commit_status_profile=(1,) * 168),
        ),
        reserves=(ReserveSpec(object_id=1, name="CPF_RS"),),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="G1", reserve_zones=("CPF_RS",), name="provision_G1"
            ),
            ReserveProvisionSpec(
                generator_name="BAT_A",
                reserve_zones=("CPF_RS",),
                name="provision_BAT_A",
            ),
        ),
        decision_variables=(
            DecisionVariableSpec(name="Generation_SEN"),
            DecisionVariableSpec(name="Inertia_SEN"),
            DecisionVariableSpec(name="CPF10s_Requirement"),
            DecisionVariableSpec(name="CPF10s_VoRS", cost=10000.0),
            DecisionVariableSpec(name="MaxInjection_SEN", lower_bound=200.0),
        ),
        user_constraints=tuple(ucs),
    )


def _zone(case: PlexosCase, name: str) -> ReserveSpec:
    return next(z for z in case.reserves if z.name == name)


def test_week_profile_on_six_day_layout() -> None:
    out = apply_native_reserve(_case(), NativeReserveOptions(inertia_term="exogenous"))
    cpf = _zone(out, "CPF10s")
    plane, floor = cpf.urreq_planes
    # The 168-value week is cut to the 144 solved hours, from the start.
    assert isinstance(plane.alpha, tuple)
    assert len(plane.alpha) == _N_HOURS
    shift = 7.075 * 0.8  # always-on inertia constant folded into alpha
    assert plane.alpha[0] == pytest.approx(100.0 - shift)
    assert plane.alpha[-1] == pytest.approx(243.0 - shift)
    assert floor.contingency_coef is None
    # G1 is forced on every hour: system_inertia = its H·S.
    assert cpf.inertia_term == "exogenous"
    assert cpf.system_inertia == (5.0,) * _N_HOURS
    csf = _zone(out, "CSF_UpMinProvision")
    assert csf.ur_requirement == _WEEK[:_N_HOURS]
    # The Storage-modelled BESS provides through its discharge generator.
    provs = {p.name: p for p in out.reserve_provisions}
    bat = provs["provision_BAT_A"]
    assert bat.reserve_zones == ("CPF_RS", "CPF10s", "CSF_UpMinProvision")
    assert bat.ur_zone_factors == (1.0, 1.0, 1.0)
    # No Battery object: no battery terms, no Inertia_SEN provisions.
    assert out.batteries == ()
    assert out.inertia_provisions == ()


def test_unalignable_profile_names_the_row() -> None:
    with pytest.raises(ValueError, match=r"CPF_Up10Calculation_I: RHS profile has 150"):
        apply_native_reserve(_case(_WEEK[:150]), NativeReserveOptions())
