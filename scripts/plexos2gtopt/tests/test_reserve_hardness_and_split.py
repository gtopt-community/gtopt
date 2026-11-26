# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hard CEN reserves, scenario-gated slack DVs and the per-family split.

* ``Include in ST Schedule`` of a Decision Variable is resolved like a
  Constraint's (scenario tiers + ``Date From`` / ``Date To`` window); an
  excluded DV does not exist in the PLEXOS ST run, so the writer pins it
  at 0.  CEN's ``*_VoRS`` DVs are tagged only to ``CPF_VoRS_ON``, which
  the solved model does not include: the CPF zones are HARD.
* Slack DVs are formulation devices: ``+ VoRS`` is a feasibility switch
  (soft penalty only while active), ``− Slack`` the surplus of an
  equality.  With hard native zones the writer emits no global
  ``reserve_shortage_cost`` (gtopt would otherwise price every zone);
  ``--soft-unpenalised-constraints P`` softens them at ``P``.
* A unit in several reserve families gets one provision per family with
  ``shared_headroom``; the CTF equality no longer caps its CPF offer.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt._native_reserve import NativeReserveOptions, apply_native_reserve
from plexos2gtopt._provision_split import reserve_family, split_provisions_by_family
from plexos2gtopt.entities import (
    BundleSpec,
    DecisionVariableSpec,
    PlexosCase,
    ReserveProvisionSpec,
    ReserveSpec,
    UserConstraintSpec,
)
from plexos2gtopt.gtopt_writer import (
    build_decision_variable_array,
    build_options,
    build_reserve_provision_array,
)
from plexos2gtopt.parsers import resolve_include_in_st
from plexos2gtopt.plexos_xml import PlexosDataRow, PlexosDb

_DV = 'decision_variable("{}").value'


def _dv(name: str) -> str:
    return _DV.format(name)


# ── Include in ST Schedule resolution ─────────────────────────────────────
def _row(data_id: int, value: float, **kw: Any) -> PlexosDataRow:
    return PlexosDataRow(
        data_id=data_id, membership_id=1, property_id=1, value=value, **kw
    )


def test_include_in_st_tiers_and_date_window() -> None:
    db = PlexosDb(path=Path("synthetic.xml"), tag_for_data={1: [900], 3: [901]})
    active = {901}
    # VoRS shape: -1 tagged to an INACTIVE scenario, untagged 0 -> excluded.
    assert not resolve_include_in_st(db, [_row(1, -1.0), _row(2, 0.0)], active)
    # The same -1 tagged to an ACTIVE scenario wins over the default.
    assert resolve_include_in_st(db, [_row(3, -1.0), _row(2, 0.0)], active)
    # Only inactive-tagged rows / no rows: project default (include).
    assert resolve_include_in_st(db, [_row(1, 0.0)], active)
    assert resolve_include_in_st(db, [], active)
    # GEN_T4_MACHICURA: the untagged 0 is dated 2022-04-09 only, so on a
    # 2025 horizon nothing applies and the DV exists.
    dated = _row(
        2,
        0.0,
        date_from=datetime(2022, 4, 9),
        date_to=datetime(2022, 4, 9),
    )
    assert not resolve_include_in_st(db, [_row(1, 0.0), dated], active)
    assert resolve_include_in_st(
        db, [_row(1, 0.0), dated], active, datetime(2025, 9, 16)
    )
    assert not resolve_include_in_st(db, [dated], active, datetime(2022, 4, 9))


def test_excluded_dv_is_pinned_at_zero() -> None:
    out = build_decision_variable_array(
        (
            DecisionVariableSpec(name="CPF_RS5m_VoRS", cost=10000.0, active=False),
            DecisionVariableSpec(name="CPF5mUp_Slack", cost=1.0),
        )
    )
    assert len(out) == 2
    vors, slack = out[0], out[1]
    assert vors["lower_bound"] == 0.0
    assert vors["upper_bound"] == 0.0
    assert "cost" not in vors
    assert "Include in ST Schedule" in vors["description"]
    assert slack["cost"] == 1.0
    assert "upper_bound" not in slack


# ── slack classification / hardness ───────────────────────────────────────
def _ucs() -> list[UserConstraintSpec]:
    def uc(name: str, expr: str) -> UserConstraintSpec:
        return UserConstraintSpec(name=name, expression=expr)

    return [
        uc(
            "Generation_Calculation",
            '1 * generator("G1").generation - 1000 * '
            + _dv("Generation_SEN")
            + " >= 0",
        ),
        uc(
            "CPF_UpCalculation",
            f"1 * {_dv('CPF5mUp_Requirement')} + 13.37 * {_dv('Generation_SEN')}"
            " >= 300",
        ),
        uc(
            "CPF_UpMinProvision",
            '1 * reserve_provision("provision_G1").up'
            f" + 1 * {_dv('CPF_RS5m_VoRS')} - 1 * {_dv('CPF5mUp_Requirement')}"
            f" - 1 * {_dv('CPF5mUp_Slack')} >= 0",
        ),
        uc(
            "CTF_UpMinProvision",
            '1 * reserve_provision("provision_G1").up'
            ' + 1 * reserve_provision("provision_G2").up'
            f" + 1 * {_dv('CTF_VoRS')} >= 40",
        ),
    ]


def _case(vors_active: bool = False, ctf_vors: bool = False) -> PlexosCase:
    dvs = [
        DecisionVariableSpec(name="Generation_SEN"),
        DecisionVariableSpec(name="CPF5mUp_Requirement", cost=10.0),
        DecisionVariableSpec(name="CPF_RS5m_VoRS", cost=10000.0, active=vors_active),
        DecisionVariableSpec(name="CPF5mUp_Slack", cost=1.0),
    ]
    if ctf_vors:
        dvs.append(DecisionVariableSpec(name="CTF_VoRS", cost=1000.0, active=False))
    ucs = _ucs()
    if not ctf_vors:
        ucs[-1] = replace(
            ucs[-1],
            expression='1 * reserve_provision("provision_G1").up'
            ' + 1 * reserve_provision("provision_G2").up >= 40',
        )
    return PlexosCase(
        bundle=BundleSpec(bundle_date="2025-09-16"),
        reserves=(
            ReserveSpec(object_id=1, name="CPF_RS"),
            ReserveSpec(object_id=2, name="CTF_RS"),
        ),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="G1",
                reserve_zones=("CPF_RS", "CTF_RS"),
                name="provision_G1",
                urmax=100.0,
            ),
            ReserveProvisionSpec(
                generator_name="G2", reserve_zones=("CTF_RS",), name="provision_G2"
            ),
        ),
        decision_variables=tuple(dvs),
        user_constraints=tuple(ucs),
    )


def _zone(case: PlexosCase, name: str) -> ReserveSpec:
    return next(z for z in case.reserves if z.name == name)


def _model_options(case: PlexosCase) -> dict[str, Any]:
    opts = build_options(
        case.bundle,
        demand_fail_cost=467.19,
        reserve_shortage_cost=(
            case.reserve_soft_penalty
            if case.reserve_soft_penalty is not None
            else None
            if case.hard_reserves
            else "default"
        ),
    )
    return opts["model_options"]


def test_inactive_vors_makes_the_zone_hard() -> None:
    out = apply_native_reserve(_case(), NativeReserveOptions())
    up = _zone(out, "CPF5mUp")
    assert up.urcost == 0.0
    assert "HARD" in up.description
    assert out.hard_reserves is True
    assert out.reserve_soft_penalty is None
    assert "reserve_shortage_cost" not in _model_options(out)
    kinds = {(r[1], r[2], r[4]) for r in out.reserve_slacks}
    assert ("CPF_RS5m_VoRS", "switch", False) in kinds
    assert ("CPF5mUp_Slack", "surplus", True) in kinds


def test_active_vors_is_a_labelled_soft_penalty() -> None:
    out = apply_native_reserve(_case(vors_active=True), NativeReserveOptions())
    up = _zone(out, "CPF5mUp")
    assert up.urcost == pytest.approx(10000.0)
    assert "feasibility switch" in up.description
    assert "not a reserve-shortage cost" in up.description


def test_soft_unpenalised_softens_hard_zones() -> None:
    opts = NativeReserveOptions.from_options({"soft_unpenalised_constraints": 750.0})
    out = apply_native_reserve(_case(ctf_vors=True), opts)
    assert _zone(out, "CPF5mUp").urcost == pytest.approx(750.0)
    ctf = _zone(out, "CTF_UpMinProvision")
    assert ctf.urcost == pytest.approx(750.0)
    assert "--soft-unpenalised-constraints" in ctf.description
    assert out.hard_reserves is False
    assert _model_options(out)["reserve_shortage_cost"] == pytest.approx(750.0)


def test_fixed_zone_with_excluded_switch_is_hard() -> None:
    out = apply_native_reserve(_case(ctf_vors=True), NativeReserveOptions())
    assert _zone(out, "CTF_UpMinProvision").urcost == 0.0
    assert ("CTF_VoRS", "switch", False) in {
        (r[1], r[2], r[4]) for r in out.reserve_slacks
    }


def test_non_native_case_keeps_the_global_default() -> None:
    case = PlexosCase(bundle=BundleSpec(bundle_date="2025-09-16"))
    assert _model_options(case)["reserve_shortage_cost"] == pytest.approx(466.19)


# ── per-family split ──────────────────────────────────────────────────────
def test_reserve_family() -> None:
    assert reserve_family("CPF5mUp") == "CPF"
    assert reserve_family("CTF_UpMinProvision") == "CTF"
    assert reserve_family("SpecialCPFNorth") == "CPF"
    assert reserve_family("Other") == "Other"


def test_cpf_and_ctf_unit_gets_two_provisions_with_shared_headroom() -> None:
    case = apply_native_reserve(_case(), NativeReserveOptions())
    caps = {("G1", "CPF", "MRU"): (30.0,) * 24, ("G1", "CTF", "MRU"): (60.0,) * 24}
    out = split_provisions_by_family(case, caps=lambda g, f, d: caps.get((g, f, d), ()))
    provs = {p.name: p for p in out.reserve_provisions}
    assert "provision_G1" not in provs
    cpf, ctf = provs["provision_G1__CPF"], provs["provision_G1__CTF"]
    assert cpf.shared_headroom is True and ctf.shared_headroom is True
    assert all(reserve_family(z) == "CPF" for z in cpf.reserve_zones)
    assert "CPF5mUp" in cpf.reserve_zones
    assert "CTF_UpMinProvision" in ctf.reserve_zones
    assert all(reserve_family(z) == "CTF" for z in ctf.reserve_zones)
    # Per-family CFdata caps replace the aggregated one.
    assert cpf.urmax_profile == (30.0,) * 24
    assert ctf.urmax_profile == (60.0,) * 24
    # Single-family G2 is untouched.
    assert provs["provision_G2"].shared_headroom is None
    # Written: two entries for G1, both flagged.
    written = build_reserve_provision_array(out.reserve_provisions)
    g1 = [w for w in written if w["generator"] == "G1"]
    assert len(g1) == 2
    assert all(w["shared_headroom"] is True for w in g1)


def test_ctf_equality_no_longer_caps_the_cpf_offer() -> None:
    """The CTF zone and the CPF zone price different columns: the CTF
    requirement (an equality) binds ``provision_G1__CTF`` only, so the
    CPF column is free up to the shared headroom."""
    out = split_provisions_by_family(
        apply_native_reserve(_case(), NativeReserveOptions())
    )
    by_zone: dict[str, set[str]] = {}
    for p in out.reserve_provisions:
        for z in p.reserve_zones:
            by_zone.setdefault(z, set()).add(p.name or "")
    assert "provision_G1__CPF" in by_zone["CPF5mUp"]
    assert "provision_G1__CPF" not in by_zone["CTF_UpMinProvision"]
    assert "provision_G1__CTF" in by_zone["CTF_UpMinProvision"]
    assert "provision_G1__CTF" not in by_zone["CPF5mUp"]


def test_user_constraint_references_are_redirected() -> None:
    def uc(name: str, expr: str) -> UserConstraintSpec:
        return UserConstraintSpec(name=name, expression=expr)

    case = PlexosCase(
        bundle=BundleSpec(bundle_date="2025-09-16"),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="G1",
                reserve_zones=("CPF_RS", "CSF_RS", "CTF_RS"),
                name="provision_G1",
            ),
        ),
        user_constraints=(
            uc("G1_CPF_Simmetry", '1 * reserve_provision("provision_G1").up = 0'),
            uc(
                "Max_Tx",
                '1 * line("L").flow - 2 * reserve_provision("provision_G1").up <= 9',
            ),
            uc(
                "units_gate",
                'reserve_provision("provision_G1").dn - 5 * ' + _dv("RU") + " <= 0",
            ),
            uc("X_ActCSFLW", '1 * reserve_provision("provision_G1").dn = 0'),
            uc("Other", '1 * generator("G1").generation <= 5'),
        ),
    )
    out = {
        u.name: u.expression for u in split_provisions_by_family(case).user_constraints
    }
    assert out["G1_CPF_Simmetry"] == '1 * reserve_provision("provision_G1__CPF").up = 0'
    assert out["X_ActCSFLW"] == '1 * reserve_provision("provision_G1__CSF").dn = 0'
    # Generator-level ("any reserve") rows keep the old column: the sum.
    assert out["Max_Tx"] == (
        '1 * line("L").flow - 2 * reserve_provision("provision_G1__CPF").up'
        ' - 2 * reserve_provision("provision_G1__CSF").up'
        ' - 2 * reserve_provision("provision_G1__CTF").up <= 9'
    )
    assert out["units_gate"].startswith(
        '1 * reserve_provision("provision_G1__CPF").dn'
        ' + 1 * reserve_provision("provision_G1__CSF").dn'
    )
    assert out["Other"] == '1 * generator("G1").generation <= 5'


def test_family_absent_from_unit_zeroes_the_term() -> None:
    case = PlexosCase(
        bundle=BundleSpec(bundle_date="2025-09-16"),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="G1",
                reserve_zones=("CPF_RS", "CSF_RS"),
                name="provision_G1",
            ),
        ),
        user_constraints=(
            UserConstraintSpec(
                name="CTF_Row",
                expression='1 * reserve_provision("provision_G1").up <= 3',
            ),
        ),
    )
    (uc,) = split_provisions_by_family(case).user_constraints
    assert uc.expression == '0 * reserve_provision("provision_G1__CPF").up <= 3'


def test_no_multi_family_provision_is_a_no_op() -> None:
    case = PlexosCase(
        bundle=BundleSpec(bundle_date="2025-09-16"),
        reserve_provisions=(
            ReserveProvisionSpec(generator_name="G1", reserve_zones=("CPF_RS",)),
        ),
    )
    assert split_provisions_by_family(case) is case
