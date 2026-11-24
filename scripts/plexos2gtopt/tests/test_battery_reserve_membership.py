# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Battery reserve-provision terms vs. Reserve→Battery memberships (Fix 7).

Recent CEN PCP bundles (2026-07 onward) attach reserve-provision
coefficients to batteries that are NOT members of the reserve the term
targets — ``BAT_KALLPA_EO`` / ``BAT_PAMPAS`` / ``BAT_ARENALES`` /
``BAT_SOL_DE_LOS_ANDES_FV`` belong to no reserve at all, yet carry
``Regulation Raise`` / ``Raise Reserve Provision Coefficient`` rows on
``Gx_*`` injection caps; ``BAT_ANDES_4_FV`` (CPF-only) carries the
generic kind on ``CSF_*_MIN_BAT_ANDES_4_FV``.  PLEXOS creates a battery
provision variable only per (Reserve, Battery) membership, so those
terms are identically zero (confirmed on the PLEXOS RES solutions).
The converter must drop them as provably-zero contributions instead of
failing on an unresolved ``provision_<bat>`` reference — while still
failing hard when the battery IS a member and its provision simply was
not emitted.

The synthetic XML below is a minimal PLEXOS ``MasterDataSet``: one
battery, up to two reserves (with a ``Type`` on the System→Reserve
membership), one constraint, and the requested coefficient rows.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from plexos2gtopt.parsers import (
    UnresolvedConstraintReferenceError,
    _battery_reserve_memberships,
    _bess_candidate_zones,
    extract_user_constraints,
    unhandled_constraint_coefficient_kinds,
)
from plexos2gtopt.plexos_loader import PlexosBundle
from plexos2gtopt.plexos_xml import NS, load_xml

# Fixed ids of the synthetic data model.
_SYS_CID, _BAT_CID, _RSV_CID, _CON_CID = 1, 7, 9, 70
_SYS_CON_COLL, _BAT_CON_COLL, _SYS_RSV_COLL, _RSV_BAT_COLL = 700, 90, 710, 720
_PROP_SENSE, _PROP_RHS, _PROP_TYPE = 4369, 4384, 5001
_CONSTR_OID, _BAT_OID = 100, 40

# Coefficient kind → property id on the Battery→Constraint collection.
_KIND_PROP_ID = {
    "Regulation Raise Reserve Provision Coefficient": 983,
    "Regulation Lower Reserve Provision Coefficient": 985,
    "Raise Reserve Provision Coefficient": 986,
    "Lower Reserve Provision Coefficient": 987,
    "Reserve Provision Coefficient": 981,
    "Reserve Units Coefficient": 988,
    "Generation Coefficient": 989,
}


def _xml(
    constraint: str,
    coeffs: dict[str, float],
    reserves: dict[str, int] | None = None,
    *,
    sense: float = -1.0,
    rhs: float = 100.0,
) -> str:
    """Build the synthetic bundle XML.

    ``coeffs``: ``{coefficient kind -> value}`` on BAT_X → ``constraint``.
    ``reserves``: ``{reserve name -> PLEXOS Type}``; BAT_X is a member of
    every listed reserve (``Type`` 0 = left at the PLEXOS default, i.e.
    not stored).
    """
    reserves = reserves or {}
    parts = [
        f'<?xml version="1.0" standalone="yes"?>\n<MasterDataSet xmlns="{NS[1:-1]}">',
        f"<t_class><class_id>{_SYS_CID}</class_id><name>System</name></t_class>",
        f"<t_class><class_id>{_BAT_CID}</class_id><name>Battery</name></t_class>",
        f"<t_class><class_id>{_RSV_CID}</class_id><name>Reserve</name></t_class>",
        f"<t_class><class_id>{_CON_CID}</class_id><name>Constraint</name></t_class>",
        f"<t_object><object_id>1</object_id><class_id>{_SYS_CID}</class_id>"
        "<name>SEN</name></t_object>",
        f"<t_object><object_id>{_BAT_OID}</object_id><class_id>{_BAT_CID}"
        "</class_id><name>BAT_X</name></t_object>",
        f"<t_object><object_id>{_CONSTR_OID}</object_id><class_id>{_CON_CID}"
        f"</class_id><name>{constraint}</name></t_object>",
    ]
    for coll_id, parent, child, name in (
        (_SYS_CON_COLL, _SYS_CID, _CON_CID, "Constraints"),
        (_BAT_CON_COLL, _BAT_CID, _CON_CID, "Constraints"),
        (_SYS_RSV_COLL, _SYS_CID, _RSV_CID, "Reserves"),
        (_RSV_BAT_COLL, _RSV_CID, _BAT_CID, "Batteries"),
    ):
        parts.append(
            f"<t_collection><collection_id>{coll_id}</collection_id>"
            f"<parent_class_id>{parent}</parent_class_id>"
            f"<child_class_id>{child}</child_class_id><name>{name}</name>"
            "</t_collection>"
        )
    props = [
        (_PROP_SENSE, _SYS_CON_COLL, "Sense"),
        (_PROP_RHS, _SYS_CON_COLL, "RHS"),
        (_PROP_TYPE, _SYS_RSV_COLL, "Type"),
    ] + [(pid, _BAT_CON_COLL, kind) for kind, pid in _KIND_PROP_ID.items()]
    for pid, coll_id, name in props:
        parts.append(
            f"<t_property><property_id>{pid}</property_id>"
            f"<collection_id>{coll_id}</collection_id><name>{name}</name>"
            "</t_property>"
        )
    data_id = 0

    def membership(mid: int, coll_id: int, parent: int, child: int) -> None:
        parts.append(
            f"<t_membership><membership_id>{mid}</membership_id>"
            f"<collection_id>{coll_id}</collection_id>"
            f"<parent_object_id>{parent}</parent_object_id>"
            f"<child_object_id>{child}</child_object_id></t_membership>"
        )

    def datum(mid: int, pid: int, value: float) -> None:
        nonlocal data_id
        data_id += 1
        parts.append(
            f"<t_data><data_id>{data_id}</data_id>"
            f"<membership_id>{mid}</membership_id>"
            f"<property_id>{pid}</property_id><value>{value}</value></t_data>"
        )

    membership(700001, _SYS_CON_COLL, 1, _CONSTR_OID)
    datum(700001, _PROP_SENSE, sense)
    datum(700001, _PROP_RHS, rhs)
    membership(90001, _BAT_CON_COLL, _BAT_OID, _CONSTR_OID)
    for kind, value in coeffs.items():
        datum(90001, _KIND_PROP_ID[kind], value)
    for idx, (rsv_name, rsv_type) in enumerate(sorted(reserves.items())):
        oid = 200 + idx
        parts.append(
            f"<t_object><object_id>{oid}</object_id><class_id>{_RSV_CID}"
            f"</class_id><name>{rsv_name}</name></t_object>"
        )
        membership(710001 + idx, _SYS_RSV_COLL, 1, oid)
        if rsv_type:
            datum(710001 + idx, _PROP_TYPE, rsv_type)
        membership(720001 + idx, _RSV_BAT_COLL, oid, _BAT_OID)
    parts.append("</MasterDataSet>\n")
    return "\n".join(parts)


def _extract(tmp_path: Path, xml: str, provisions: frozenset[str] = frozenset()):
    xml_path = tmp_path / "DBSEN_PRGDIARIO.xml"
    xml_path.write_text(xml)
    bundle = PlexosBundle(root=tmp_path, source=tmp_path)
    db = load_xml(xml_path)
    stats: dict[str, int] = {}
    allow = {
        "Battery": frozenset({"BAT_X"}),
        "ReserveProvision": provisions,
        "Commitment": frozenset({"uc_BAT_X_gen"}),
    }
    out = extract_user_constraints(db, bundle, emitted_names=allow, stats_out=stats)
    return {c.name: c for c in out}, stats, db


_GX = "Gx_SSCC_Test"
_REG_RAISE = "Regulation Raise Reserve Provision Coefficient"
_RAISE = "Raise Reserve Provision Coefficient"


def test_memberships_index_reads_reserve_type(tmp_path: Path) -> None:
    """``_battery_reserve_memberships`` maps battery → {reserve: Type}."""
    xml = _xml(_GX, {}, {"CPF_RS_BESS": 3, "CTF_RS_BESS": 0, "CTF_LW_BESS": 2})
    xml_path = tmp_path / "DBSEN_PRGDIARIO.xml"
    xml_path.write_text(xml)
    got = _battery_reserve_memberships(load_xml(xml_path))
    assert got == {"BAT_X": {"CPF_RS_BESS": 3, "CTF_RS_BESS": 0, "CTF_LW_BESS": 2}}


def test_candidate_zones_by_kind() -> None:
    """Regulation kinds → CPF/CSF ``*_BESS`` zones; Raise/Lower → the
    non-regulation member zones of the matching direction."""
    members = {"CPF_RS_BESS": 3, "CSF_LW_BESS": 4, "CTF_RS_BESS": 0, "CTF_LW_BESS": 2}
    assert _bess_candidate_zones("BAT_X", "up", _GX, _REG_RAISE, members) == [
        "CPF_RS_BESS"
    ]
    assert _bess_candidate_zones("BAT_X", "up", _GX, _RAISE, members) == ["CTF_RS_BESS"]
    assert _bess_candidate_zones(
        "BAT_X", "dn", _GX, "Lower Reserve Provision Coefficient", members
    ) == ["CTF_LW_BESS"]
    # No membership at all → empty (term identically zero).
    assert not _bess_candidate_zones("BAT_X", "up", _GX, _REG_RAISE, {})
    assert not _bess_candidate_zones("BAT_X", "up", _GX, _RAISE, {})


def test_non_member_battery_terms_dropped_as_zero(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """2026-11-09 shape: ``BAT_X`` belongs to NO reserve but carries
    Generation + Raise + Regulation Raise coefficients on a ``Gx_*`` cap.

    Both provision terms are dropped (PLEXOS provision ≡ 0) with ONE
    summary warning; the generation term survives; no unresolved-ref
    error is raised.
    """
    xml = _xml(_GX, {"Generation Coefficient": 1.0, _REG_RAISE: 1.0, _RAISE: 1.0})
    with caplog.at_level(logging.WARNING, logger="plexos2gtopt.parsers"):
        by_name, stats, _db = _extract(tmp_path, xml)
    expr = by_name[_GX].expression
    assert 'battery("BAT_X").discharge' in expr
    assert "reserve_provision(" not in expr
    assert stats["battery_zero_provision_terms"] == 2
    assert stats["unresolved_refs"] == 0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "2 Battery reserve term(s) dropped as identically zero" in w
        and f"{_GX} / BAT_X / {_REG_RAISE}" in w
        for w in warnings
    )


def test_raise_kind_does_not_double_count_regulation_zone(tmp_path: Path) -> None:
    """``BAT_X`` is a member of ``CPF_RS_BESS`` (Type 3 = Regulation Raise)
    only.  PLEXOS RES 2026-11-09 (``Gx_SSCC_Jadresic`` / ``BAT_ARENA``)
    counts that provision ONCE: via the Regulation Raise kind.  The Raise
    kind targets non-regulation reserves → no member zone → zero."""
    xml = _xml(_GX, {_REG_RAISE: 1.0, _RAISE: 1.0}, {"CPF_RS_BESS": 3})
    by_name, stats, _db = _extract(
        tmp_path, xml, frozenset({"provision_BAT_X_gen__CPF_RS_BESS"})
    )
    expr = by_name[_GX].expression
    assert expr.count('reserve_provision("provision_BAT_X_gen__CPF_RS_BESS").up') == 1
    assert stats["battery_zero_provision_terms"] == 1


def test_raise_kind_routes_to_non_regulation_zone(tmp_path: Path) -> None:
    """A battery IN a non-regulation raise reserve (``CTF_RS_BESS``, Type
    left at default) gets its Raise term routed to that zone."""
    xml = _xml(_GX, {_RAISE: 1.0}, {"CTF_RS_BESS": 0})
    by_name, stats, _db = _extract(
        tmp_path, xml, frozenset({"provision_BAT_X_gen__CTF_RS_BESS"})
    )
    assert (
        'reserve_provision("provision_BAT_X_gen__CTF_RS_BESS").up'
        in by_name[_GX].expression
    )
    assert stats["battery_zero_provision_terms"] == 0


def test_member_but_unemitted_provision_still_fails_hard(tmp_path: Path) -> None:
    """The battery IS a member of the targeted reserve but its provision
    was not emitted → genuine gap → hard fail (no silent drop)."""
    xml = _xml(_GX, {_RAISE: 1.0}, {"CTF_RS_BESS": 0})
    with pytest.raises(UnresolvedConstraintReferenceError) as excinfo:
        _extract(tmp_path, xml)
    assert _GX in str(excinfo.value)


def test_csf_min_row_of_cpf_only_battery_becomes_inactive_stub(
    tmp_path: Path,
) -> None:
    """2026-08-16 shape: ``CSF_LW_MIN_BAT_X`` (generic provision − 5 ×
    Reserve Units ≥ 0) on a CPF-only battery.  PLEXOS reports Activity 0
    while the battery runs, so BOTH terms are zero; forwarding Reserve
    Units to the battery commitment would force the battery OFF.  The
    constraint collapses to an inactive ``0 >= 0`` stub."""
    name = "CSF_LW_MIN_BAT_X"
    xml = _xml(
        name,
        {"Reserve Provision Coefficient": 1.0, "Reserve Units Coefficient": -5.0},
        {"CPF_LW_BESS": 4, "CPF_RS_BESS": 3},
        sense=1.0,
        rhs=0.0,
    )
    by_name, stats, _db = _extract(
        tmp_path,
        xml,
        frozenset(
            {"provision_BAT_X_gen__CPF_LW_BESS", "provision_BAT_X_gen__CPF_RS_BESS"}
        ),
    )
    uc = by_name[name]
    assert uc.expression == "0 >= 0"
    assert uc.active is False
    assert "commitment(" not in uc.expression
    assert "BAT_X" in uc.description
    assert stats["battery_zero_provision_terms"] == 2


def test_reserve_units_kept_when_battery_is_member(tmp_path: Path) -> None:
    """Same CSF MIN row on a CSF member: both terms stay (legacy path)."""
    name = "CSF_LW_MIN_BAT_X"
    xml = _xml(
        name,
        {"Reserve Provision Coefficient": 1.0, "Reserve Units Coefficient": -5.0},
        {"CSF_LW_BESS": 4},
        sense=1.0,
        rhs=0.0,
    )
    by_name, stats, _db = _extract(
        tmp_path, xml, frozenset({"provision_BAT_X_gen__CSF_LW_BESS"})
    )
    expr = by_name[name].expression
    assert 'reserve_provision("provision_BAT_X_gen__CSF_LW_BESS").dn' in expr
    assert 'decision_variable("reserve_units_BAT_X_gen_dn").value' in expr
    assert stats["battery_zero_provision_terms"] == 0


def test_direction_less_generic_term_still_fails_hard(tmp_path: Path) -> None:
    """Generic kind on a constraint whose name carries no direction: the
    zero rule cannot decide which reserve is meant → hard fail."""
    xml = _xml("MISC_MIN_BAT_X", {"Reserve Provision Coefficient": 1.0})
    with pytest.raises(UnresolvedConstraintReferenceError):
        _extract(tmp_path, xml)


def test_unhandled_coefficient_kind_is_reported(tmp_path: Path) -> None:
    """A Battery→Constraint coefficient kind with no gtopt mapping is
    surfaced (never silently ignored); mapped kinds are not reported."""
    xml = _xml(_GX, {"Generation Coefficient": 1.0}).replace(
        "<name>Reserve Units Coefficient</name>",
        "<name>Hypothetical New Coefficient</name>",
    )
    xml = xml.replace(
        "</MasterDataSet>",
        "<t_data><data_id>999</data_id><membership_id>90001</membership_id>"
        f"<property_id>{_KIND_PROP_ID['Reserve Units Coefficient']}</property_id>"
        "<value>1</value></t_data>\n</MasterDataSet>",
    )
    xml_path = tmp_path / "DBSEN_PRGDIARIO.xml"
    xml_path.write_text(xml)
    got = unhandled_constraint_coefficient_kinds(load_xml(xml_path))
    assert got == {("Battery", "Hypothetical New Coefficient"): 1}
