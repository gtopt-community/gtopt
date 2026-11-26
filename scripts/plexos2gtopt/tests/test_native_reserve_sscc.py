# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--reserve-model native`` against the gtopt plane-support rules.

The inertia term (``--reserve-inertia-term``: ``commitment`` by default,
exogenous ``system_inertia`` or the PLEXOS-compatible endogenous provision),
the strict-mode support checks gtopt applies to requirement planes, the
non-negative contingency coefficient and the writer fields.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from plexos2gtopt._native_reserve import (
    NativeReserveOptions,
    apply_native_reserve,
    plane_support_issues,
)
from plexos2gtopt.entities import (
    ReserveContingencySpec,
    ReservePlaneSpec,
    ReserveSpec,
)
from plexos2gtopt.gtopt_writer import build_reserve_zone_array
from plexos2gtopt.main import make_parser
from plexos2gtopt.tests.test_native_reserve import _base_ucs, _case, _zone
from plexos2gtopt.tests.test_native_reserve_extras import _db

_EXO = NativeReserveOptions()


def _spec(**kw: object) -> ReserveSpec:
    return replace(ReserveSpec(object_id=0, name="Z"), **kw)


# ── options / CLI ──────────────────────────────────────────────────────────
def test_inertia_term_option() -> None:
    assert NativeReserveOptions.from_options({}).inertia_term == "commitment"
    for term in ("exogenous", "endogenous"):
        opts = NativeReserveOptions.from_options({"reserve_inertia_term": term})
        assert opts.inertia_term == term
    with pytest.raises(ValueError, match="unknown reserve_inertia_term"):
        NativeReserveOptions.from_options({"reserve_inertia_term": "dispatch"})


def test_inertia_term_cli() -> None:
    parser = make_parser()
    assert parser.parse_args(["-i", "x"]).reserve_inertia_term == "commitment"
    args = parser.parse_args(["-i", "x", "--reserve-inertia-term", "endogenous"])
    assert args.reserve_inertia_term == "endogenous"
    with pytest.raises(SystemExit):
        parser.parse_args(["-i", "x", "--reserve-inertia-term", "dispatch"])


# ── plane support ──────────────────────────────────────────────────────────
def test_plane_support_ok() -> None:
    plane = ReservePlaneSpec(alpha=1.0, inertia_coef=-0.01, contingency_coef=1.0)
    exo = _spec(
        urreq_planes=(plane,),
        inertia_term="exogenous",
        system_inertia=(5.0,),
        urcontingencies=(ReserveContingencySpec(generators=("G",)),),
    )
    assert not plane_support_issues(exo)
    endo = replace(
        exo, inertia_term="endogenous", system_inertia=(), inertia_zone="I_SEN"
    )
    assert not plane_support_issues(endo)
    commit = replace(endo, inertia_term="commitment")
    assert not plane_support_issues(commit)
    # commitment without its zone, or exogenous without data: unsupported.
    assert plane_support_issues(replace(commit, inertia_zone=""))
    assert plane_support_issues(replace(exo, system_inertia=()))
    # Zero / absent coefficients need no support.
    assert not plane_support_issues(_spec(urreq_planes=(ReservePlaneSpec(),)))
    zeros = ReservePlaneSpec(inertia_coef=(0.0, 0.0), flow_coef=0.0)
    assert not plane_support_issues(_spec(drreq_planes=(zeros,)))


def test_plane_support_issues() -> None:
    up = ReservePlaneSpec(
        alpha=1.0,
        inertia_coef=-0.01,
        contingency_coef=(1.0, -0.5),
        flow_coef=0.2,
        source="UP",
    )
    dn = ReservePlaneSpec(contingency_coef=0.9, source="DN")
    # Endogenous with system_inertia but no inertia_zone: still unsupported.
    zone = _spec(
        urreq_planes=(up,),
        drreq_planes=(dn,),
        inertia_term="endogenous",
        system_inertia=(5.0,),
    )
    assert plane_support_issues(zone) == [
        "Z: inertia_coef without system_inertia/inertia_zone",
        "Z: up contingency_coef without candidates",
        "Z: down contingency_coef without candidates",
        "Z: negative contingency_coef (UP)",
        "Z: flow_coef without flow_lines",
    ]
    singles = replace(
        zone,
        inertia_zone="I_SEN",
        contingency_generators=("G",),
        contingency_demands=("D",),
        flow_lines=("L",),
    )
    assert plane_support_issues(singles) == ["Z: negative contingency_coef (UP)"]


def test_apply_rejects_unsupported_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    import plexos2gtopt._native_reserve as nr  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(nr, "plane_support_issues", lambda z: [f"{z.name}: bad"])
    with pytest.raises(ValueError, match="inconsistent plane support: CPF10s: bad"):
        apply_native_reserve(_case(), _EXO)


def test_negative_contingency_coefficient_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A CPF row whose contingency DV enters with the wrong sign: the term is
    dropped (gtopt rejects contingency_coef < 0) and the plane is kept."""
    ucs = [
        replace(u, expression=u.expression.replace("- 1.25 *", "+ 1.25 *"))
        if u.name == "CPF_Up10Calculation_I"
        else u
        for u in _base_ucs()
    ]
    assert ucs != _base_ucs()
    out = apply_native_reserve(_case(ucs), _EXO)
    planes = _zone(out, "CPF10s").urreq_planes
    assert all(p.contingency_coef is None for p in planes)
    assert "negative contingency coefficient (-1.25); contingency term dropped" in (
        caplog.text
    )


# ── writer ─────────────────────────────────────────────────────────────────
def test_writer_inertia_fields() -> None:
    zone = _spec(
        urreq_planes=(ReservePlaneSpec(alpha=1.0, inertia_coef=-0.01),),
        inertia_term="exogenous",
        system_inertia=(100.0, 200.0),
        omit_zero_requirement=True,
    )
    entry = build_reserve_zone_array((zone,), block_count=2)[0]
    assert entry["inertia_term"] == "exogenous"
    assert entry["system_inertia"] == [[100.0, 200.0]]
    assert "inertia_zone" not in entry
    plain = build_reserve_zone_array((_spec(),), block_count=2)[0]
    assert "inertia_term" not in plain
    assert "system_inertia" not in plain


def test_fixed_equality_zone_keeps_surplus_cost() -> None:
    """Under ``--legacy-profile cen`` a fixed-requirement zone (no planes)
    keeps ``"eq"`` + surplus cost: there the surplus column is built.  The
    clean default writes ``>=``."""
    ucs = [
        replace(u, penalty=7.0) if u.name == "CSF_UpMinProvision" else u
        for u in _base_ucs()
    ]
    db = _db({"CSF_UpMinProvision": {"Sense": 0.0}})
    replica = NativeReserveOptions(surplus_equality=True)
    out = apply_native_reserve(_case(ucs), replica, db=db)
    csf = _zone(out, "CSF_UpMinProvision")
    assert (csf.requirement_sense, csf.ursurplus_cost) == ("eq", 7.0)
    entry = build_reserve_zone_array((csf,), block_count=24)[0]
    assert entry["ursurplus_cost"] == 7.0
    # Clean default: '>=' with a free excess, no surplus column.
    clean = _zone(apply_native_reserve(_case(ucs), _EXO, db=db), "CSF_UpMinProvision")
    assert (clean.requirement_sense, clean.ursurplus_cost) == ("", None)
