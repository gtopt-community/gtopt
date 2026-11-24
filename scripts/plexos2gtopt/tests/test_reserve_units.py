# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""PLEXOS ``Reserve Units Coefficient`` → synthesised Reserve Units variable.

``Reserve Units`` counts the units PROVIDING reserve (<= units generating;
0 while on but not providing).  Aliasing it to the commitment status
turned ``NUEVA_RENCA_CSFLW_RegRange_e2`` (``gen - 287.115·RU >= 0``) into
``gen >= 287 MW whenever committed`` and made 2024-12-15 infeasible.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt.entities import (
    BatterySpec,
    DecisionVariableSpec,
    GeneratorSpec,
    ReserveProvisionSpec,
    UserConstraintSpec,
)
from plexos2gtopt.tests.cov_parsers_kit import bundle
from plexos2gtopt.tests.test_cov_parsers_uc import UcKit


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("NUEVA_RENCA_CSFLW_RegRange_e2", "dn"),
        ("NUEVA_RENCA_CSFRS_RegRange_e2", "up"),
        ("SSCC_NVentanas_Down", "dn"),
        ("SSCC_NVentanas_Up", "up"),
        ("CSF_LW_MIN_BAT_X", "dn"),
        ("CSF_MinUnits", None),
        ("Kelar_GNL_SSCC", None),
        ("X_LW_RS", None),
    ],
)
def test_name_direction(name: str, expected: str | None) -> None:
    """Constraint-name direction rule."""
    assert P._reserve_units_name_direction(name) == expected


def _emitted(**extra: frozenset[str]) -> dict[str, frozenset[str]]:
    base = {
        "Generator": frozenset({"G", "H", "NOPROV", "OFF"}),
        "ReserveProvision": frozenset({"provision_G", "provision_H", "provision_OFF"}),
        "Commitment": frozenset({"uc_G"}),
        "Battery": frozenset({"B"}),
    }
    base.update(extra)
    return base


def test_generator_reserve_units_terms(tmp_path: Path) -> None:
    """Row provision dir > name dir > any; dropped when no provision / offline."""
    kit = UcKit()
    g = kit.obj("Generator", "G")
    h = kit.obj("Generator", "H")
    noprov = kit.obj("Generator", "NOPROV")
    off = kit.obj("Generator", "OFF")
    # gen - 287·RU >= 0 with a Regulation Lower provision term in the row.
    c1 = kit.con("PLANT_RegRange_e1", sense=1.0, rhs=0.0)
    kit.term("Generator", g, c1, "Generation Coefficient", 1.0)
    kit.term("Generator", g, c1, "Regulation Lower Reserve Provision Coefficient", 1.0)
    kit.term("Generator", g, c1, "Reserve Units Coefficient", -287.115)
    # Name carries the direction.
    c2 = kit.con("PLANT_CSFRS_RegRange_e2", sense=1.0, rhs=0.0)
    kit.term("Generator", g, c2, "Generation Coefficient", 1.0)
    kit.term("Generator", g, c2, "Reserve Units Coefficient", -287.115)
    # Neither: units providing any reserve; H has no commitment.
    c3 = kit.con("CSF_MinUnits", sense=1.0, rhs=1.0)
    for oid in (g, h, noprov, off):
        kit.term("Generator", oid, c3, "Reserve Units Coefficient", 1.0)
    out: dict[str, P.ReserveUnitsVar] = {}
    ucs = {
        u.name: u
        for u in P.extract_user_constraints(
            kit.db,
            bundle(tmp_path),
            emitted_names=_emitted(),
            offline_commit_gens=frozenset({"OFF"}),
            reserve_units_out=out,
        )
    }
    assert (
        'decision_variable("reserve_units_G_dn").value'
        in ucs["PLANT_RegRange_e1"].expression
    )
    assert "commitment(" not in ucs["PLANT_RegRange_e1"].expression
    assert (
        'decision_variable("reserve_units_G_up").value'
        in ucs["PLANT_CSFRS_RegRange_e2"].expression
    )
    expr = ucs["CSF_MinUnits"].expression
    assert 'decision_variable("reserve_units_G").value' in expr
    assert 'decision_variable("reserve_units_H").value' in expr
    assert "NOPROV" not in expr and "OFF" not in expr
    assert out["reserve_units_G_dn"] == P.ReserveUnitsVar(
        "reserve_units_G_dn", "G", "uc_G", ("dn",)
    )
    assert out["reserve_units_G"].dirs == ("dn", "up")
    assert out["reserve_units_H"].status is None


def test_battery_reserve_units_forward(tmp_path: Path) -> None:
    """Battery Reserve Units -> variable owned by ``<bat>_gen``."""
    kit = UcKit()
    b = kit.obj("Battery", "B")
    c = kit.con("CSF_LW_MIN_B", sense=1.0, rhs=0.0)
    kit.term("Battery", b, c, "Reserve Units Coefficient", -5.0)
    out: dict[str, P.ReserveUnitsVar] = {}
    ucs = P.extract_user_constraints(
        kit.db, bundle(tmp_path), emitted_names=_emitted(), reserve_units_out=out
    )
    assert 'decision_variable("reserve_units_B_gen_dn").value' in ucs[0].expression
    assert out["reserve_units_B_gen_dn"].status == "uc_B_gen"


def test_build_reserve_units_model() -> None:
    """Bounds, status row, per-provision gates with cap fallbacks."""
    case = P.PlexosCase(
        bundle=None,  # type: ignore[arg-type]
        generators=(
            GeneratorSpec(object_id=1, name="G", bus_name="n", pmax=300.0),
            GeneratorSpec(object_id=2, name="H", bus_name="n", pmax=0.0),
        ),
        batteries=(
            BatterySpec(
                object_id=3,
                name="B",
                bus_name="n",
                pmax_charge=20.0,
                pmax_discharge=25.0,
            ),
        ),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="G",
                reserve_zones=("CSF_LW", "CSF_RS"),
                drmax_profile=(10.0, 40.0),
            ),
            ReserveProvisionSpec(generator_name="H", reserve_zones=("CSF_RS",)),
            ReserveProvisionSpec(generator_name="Z", reserve_zones=()),
            ReserveProvisionSpec(
                generator_name="B_gen",
                reserve_zones=("CSF_LW",),
                name="provision_B_gen__CSF_LW",
            ),
        ),
    )
    variables = {
        "reserve_units_G": P.ReserveUnitsVar(
            "reserve_units_G", "G", "uc_G", ("dn", "up")
        ),
        "reserve_units_H": P.ReserveUnitsVar("reserve_units_H", "H", None, ("up",)),
        "reserve_units_Z": P.ReserveUnitsVar("reserve_units_Z", "Z", None, ("up",)),
        "reserve_units_B_gen_dn": P.ReserveUnitsVar(
            "reserve_units_B_gen_dn", "B_gen", "uc_B_gen", ("dn",)
        ),
    }
    dvs, rows = P.build_reserve_units_model(variables, case)
    bounds = {d.name: (d.lower_bound, d.upper_bound) for d in dvs}
    assert bounds["reserve_units_G"] == (0.0, 1.0)
    assert bounds["reserve_units_Z"] == (0.0, 0.0)  # no zone provision
    # PLEXOS Reserve Units are integer: binary when the owner can provide,
    # continuous (fixed at 0) when it cannot.
    integer = {d.name: d.integer for d in dvs}
    assert integer["reserve_units_G"] is True
    assert integer["reserve_units_B_gen_dn"] is True
    assert integer["reserve_units_Z"] is False
    by_name = {r.name: r.expression for r in rows}
    assert by_name["reserve_units_G_le_status"] == (
        '1 * decision_variable("reserve_units_G").value '
        '- 1 * commitment("uc_G").status <= 0'
    )
    assert by_name["reserve_units_G_gate_provision_G_dn"] == (
        '1 * reserve_provision("provision_G").dn '
        '- 40 * decision_variable("reserve_units_G").value <= 0'
    )
    # No urmax -> owner pmax fallback.
    assert "- 300 *" in by_name["reserve_units_G_gate_provision_G_up"]
    # H: no status row, zero cap (pmax 0) -> no gate row.
    assert not any(n.startswith("reserve_units_H") for n in by_name)
    gate = by_name["reserve_units_B_gen_dn_gate_provision_B_gen__CSF_LW_dn"]
    assert "- 25 *" in gate


def _ru_case() -> P.PlexosCase:
    """G (status) and H (always on) can provide; Z has no provision."""
    return P.PlexosCase(
        bundle=None,  # type: ignore[arg-type]
        generators=(
            GeneratorSpec(object_id=1, name="G", bus_name="n", pmax=300.0),
            GeneratorSpec(object_id=2, name="H", bus_name="n", pmax=50.0),
        ),
        reserve_provisions=(
            ReserveProvisionSpec(generator_name="G", reserve_zones=("CSF_RS",)),
            ReserveProvisionSpec(generator_name="H", reserve_zones=("CSF_RS",)),
        ),
    )


def _ref(name: str) -> str:
    return f'decision_variable("{name}").value'


def test_prune_unreferenced_reserve_units() -> None:
    """Only owners used by an ACTIVE non-gate row keep their DV + gates."""
    case = _ru_case()
    variables = {
        n: P.ReserveUnitsVar(n, owner, status, ("up",))
        for n, owner, status in (
            ("reserve_units_G", "G", "uc_G"),
            ("reserve_units_H", "H", None),
            ("reserve_units_Z", "Z", None),
        )
    }
    dvs, gates = P.build_reserve_units_model(variables, case)
    other_dv = DecisionVariableSpec(name="plexos_dv", upper_bound=5.0)
    rows = (
        # Active PLEXOS row: keeps reserve_units_G.
        UserConstraintSpec(
            name="CSF_MinUnits",
            expression=f"1 * {_ref('reserve_units_G')} >= 1",
        ),
        # Replaced by a native zone (inactive provenance): does not count.
        UserConstraintSpec(
            name="CSF_RS_MinProvision",
            expression=f"1 * {_ref('reserve_units_H')} >= 1",
            active=False,
        ),
        UserConstraintSpec(name="plain", expression=f"1 * {_ref('plexos_dv')} <= 3"),
    )
    case = dataclasses.replace(
        case,
        decision_variables=(other_dv, *dvs),
        user_constraints=rows + gates,
    )
    gate_names = {g.name for g in gates}
    assert {"reserve_units_G_le_status", "reserve_units_H_gate_provision_H_up"} <= (
        gate_names
    )

    out = P.prune_unreferenced_reserve_units(case)

    assert [d.name for d in out.decision_variables] == [
        "plexos_dv",
        "reserve_units_G",
    ]
    kept = {u.name: u for u in out.user_constraints}
    # Referenced owner: DV and every gate row unchanged.
    g_gates = [g for g in gates if g.name.startswith("reserve_units_G_")]
    assert g_gates
    assert all(kept[g.name] == g for g in g_gates)
    # Unreferenced owners: no gate rows left.
    assert not any(n.startswith(("reserve_units_H", "reserve_units_Z")) for n in kept)
    # Non-gate rows stay, including the inactive one.
    assert {"CSF_MinUnits", "CSF_RS_MinProvision", "plain"} <= set(kept)


def test_prune_reserve_units_noop() -> None:
    """No Reserve Units DVs, or all referenced: the case is returned as is."""
    case = _ru_case()
    assert P.prune_unreferenced_reserve_units(case) is case
    variables = {
        "reserve_units_G": P.ReserveUnitsVar("reserve_units_G", "G", "uc_G", ("up",))
    }
    dvs, gates = P.build_reserve_units_model(variables, case)
    used = dataclasses.replace(
        case,
        decision_variables=dvs,
        user_constraints=(
            UserConstraintSpec(
                name="CSF_MinUnits", expression=f"1 * {_ref('reserve_units_G')} >= 1"
            ),
            *gates,
        ),
    )
    assert P.prune_unreferenced_reserve_units(used) is used
