"""Unit tests for the branches of :mod:`plexos2gtopt.aemo_isp_constraints`.

The XML-driven tests in ``test_aemo_isp_constraints.py`` exercise the
happy path end to end.  Here a minimal in-memory :class:`IspResolver`
(built without an XML file) feeds the engine directly, so every skip
reason, term class and REZ rejection rule is checked in isolation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from plexos2gtopt import aemo_isp_constraints as isp_c
from plexos2gtopt.aemo_isp import IspResolver, _Row
from plexos2gtopt.isp_traces import TimesliceCalendar

D0 = date(2027, 1, 18)


class _Db:
    """The slice of :class:`PlexosDb` the resolver reads."""

    def __init__(self) -> None:
        self.class_ids: dict[str, int] = {}
        self.objs: dict[str, list[Any]] = defaultdict(list)
        self.collections: list[Any] = []
        self.links: dict[int, dict[int, list[int]]] = {}

    def class_id(self, name: str) -> int:
        return self.class_ids.setdefault(name, len(self.class_ids) + 1)

    def objects_of_class(self, name: str) -> list[Any]:
        return list(self.objs.get(name, []))

    def parent_to_children(self, cid: int) -> dict[int, list[int]]:
        return self.links[cid]


def _row(
    value: float,
    *,
    frm: str | None = None,
    to: str | None = None,
    df: str | None = None,
    others: tuple[int, ...] = (),
) -> _Row:
    return _Row(
        value=value,
        date_from=datetime.fromisoformat(frm) if frm else None,
        date_to=datetime.fromisoformat(to) if to else None,
        timeslices=(),
        data_file=df,
        other_tag=bool(others),
        others=others,
    )


class Fake:
    """Build an :class:`IspResolver` object by object."""

    def __init__(self) -> None:
        self.db = _Db()
        res = IspResolver.__new__(IspResolver)
        res.db = self.db  # type: ignore[assignment]
        res.obj = {}
        res.cls = {}
        res.cat = {}
        res.rows = defaultdict(list)
        res.coll_rows = defaultdict(list)
        res.filename = {}
        res.locator = SimpleNamespace(resolve=lambda _f: None)  # type: ignore[assignment]
        res.missing_traces = set()
        res.missing_hits = 0
        res.used_files = set()
        res.calendar = TimesliceCalendar()
        self.res = res

    def obj(self, cls: str, name: str, category: str = "") -> int:
        cid = self.db.class_id(cls)
        self.res.cls[cid] = cls
        cat_id = None
        if category:
            cat_id = len(self.res.cat) + 1
            self.res.cat[cat_id] = category
        oid = len(self.res.obj) + 1
        o = SimpleNamespace(object_id=oid, class_id=cid, name=name, category_id=cat_id)
        self.res.obj[oid] = o
        self.db.objs[cls].append(o)
        return oid

    def prop(self, oid: int, prop: str, value: float, **kw: Any) -> None:
        self.res.rows[(oid, prop)].append(_row(value, **kw))

    def coef(self, parent: int, con: int, prop: str, value: float, **kw: Any) -> None:
        self.res.coll_rows[(parent, con, "Constraints::" + prop)].append(
            _row(value, **kw)
        )

    def member(self, pcls: str, coll: str, parent: int, child: int) -> None:
        cid = len(self.db.collections) + 1
        for c in self.db.collections:
            if c.parent_class_id == self.db.class_id(pcls) and c.name == coll:
                cid = c.collection_id
                break
        else:
            self.db.collections.append(
                SimpleNamespace(
                    collection_id=cid,
                    parent_class_id=self.db.class_id(pcls),
                    name=coll,
                )
            )
            self.db.links[cid] = defaultdict(list)
        self.db.links[cid][parent].append(child)

    def con(
        self,
        name: str,
        category: str = "Group Constraints",
        *,
        sense: int | None = None,
        penalty: float | None = None,
        **rhs: float,
    ) -> int:
        oid = self.obj("Constraint", name, category)
        if sense is not None:
            self.prop(oid, "Sense", sense)
        if penalty is not None:
            self.prop(oid, "Penalty Price", penalty)
        for prop, value in rhs.items():
            self.prop(oid, prop.replace("_", " "), value)
        return oid


def _view(f: Fake, days: int = 1, **kw: Any) -> isp_c.CaseView:
    system = kw.pop(
        "system", {"bus_array": [], "line_array": [], "generator_array": []}
    )
    return isp_c.CaseView(
        res=f.res,
        day0=D0,
        days=days,
        gens=kw.pop("gens", {}),
        batteries=kw.pop("batteries", {}),
        lines=kw.pop("lines", set()),
        node_load=kw.pop("node_load", {}),
        system=system,
        sched=defaultdict(dict),
        **kw,
    )


def _groups() -> dict[str, Counter[str]]:
    return defaultdict(Counter)


def _ev(**kw: Any) -> isp_c.EvalConstraint:
    base: dict[str, Any] = {
        "name": "C",
        "category": "Cat",
        "group": "operational",
        "sense": -1,
        "penalty": None,
        "rhs_kind": "RHS",
        "const": np.zeros(24),
    }
    base.update(kw)
    return isp_c.EvalConstraint(**base)


# ---------------------------------------------------------------------------
# periods and RHS kinds
# ---------------------------------------------------------------------------


def test_period_of_custom_and_dated_rows() -> None:
    d = date(2027, 3, 4)
    plain = _row(1.0)
    assert isp_c.period_of("RHS Custom", plain, d) == (
        date(2026, 7, 1),
        date(2027, 6, 30),
    )
    dated = _row(1.0, frm="2027-01-01", to="2027-12-31")
    assert isp_c.period_of("RHS Year", dated, d) == (
        date(2027, 1, 1),
        date(2027, 12, 31),
    )
    assert isp_c.period_of("RHS Month", dated, d) == (
        date(2027, 3, 1),
        date(2027, 3, 31),
    )


def test_prorated_rhs_skips_traces_and_missing_rows() -> None:
    f = Fake()
    c = f.con("C")
    assert isp_c.prorated_rhs(f.res, c, "RHS Month", D0, 2) == (None, [])
    f.prop(c, "RHS Month", 5.0, df="trace")
    assert isp_c.prorated_rhs(f.res, c, "RHS Month", D0, 2) == (None, [])
    f.res.rows[(c, "RHS Month")] = [_row(31.0, frm="2027-01-19")]
    total, periods = isp_c.prorated_rhs(f.res, c, "RHS Month", D0, 2)
    assert total == pytest.approx(1.0)
    assert periods[0]["window_days"] == 1


def test_classify_groups() -> None:
    f = Fake()
    gen = f.obj("Generator", "G")
    comp = f.obj("Company", "Co")
    pur = f.obj("Purchaser", "P")
    other = f.obj("Node", "N")
    empty = f.con("Empty")
    vret = f.con("Vret")
    net = f.con("Net", "Network Purchasers")
    f.coef(comp, vret, "Generation Coefficient", 1.0)
    f.coef(pur, net, "Load Coefficient", 1.0)
    # a "Constraints" collection whose child is not a Constraint
    f.res.coll_rows[(gen, other, "Constraints::Generation Coefficient")].append(
        _row(1.0)
    )
    f.res.coll_rows[(gen, vret, "Generators::Other")].append(_row(1.0))
    index = isp_c.ConstraintIndex.build(f.res)
    assert other not in index.coefs
    assert isp_c.classify(f.res, index, empty) == "no_coefficients"
    assert isp_c.classify(f.res, index, vret) == "company_vret"
    assert isp_c.classify(f.res, index, net) == "purchaser_network"


def test_rhs_kind_none_and_not_live() -> None:
    f = Fake()
    c = f.con("C")
    assert isp_c.rhs_kind(f.res, c, D0, 1) is None
    f.prop(c, "RHS", 5.0, frm="2030-01-01")
    f.prop(c, "RHS Day", 1.0)
    assert isp_c.rhs_kind(f.res, c, D0, 1) == "RHS Day"
    view = _view(f)
    index = isp_c.ConstraintIndex.build(f.res)
    none = f.con("None")
    assert isp_c.evaluate(view, index, isp_c.ConstraintOptions(), none, "g") is None


# ---------------------------------------------------------------------------
# purchasers
# ---------------------------------------------------------------------------


def _purchaser_case() -> tuple[Fake, isp_c.CaseView]:
    f = Fake()
    node = f.obj("Node", "STH")
    h2 = f.con("H2", "H2 Subregion Domestic Consumption", RHS_Month=1.0)
    for name, units, maxl in (
        ("ELY", 1.0, 20.0),
        ("NOUNITS", 0.0, 20.0),
        ("ZERO", 1.0, 0.0),
        ("NOMAX", 1.0, None),
    ):
        p = f.obj("Purchaser", name)
        f.member("Purchaser", "Nodes", p, node)
        f.prop(p, "Units", units)
        if maxl is not None:
            f.prop(p, "Max Load", maxl)
        if name == "ELY":
            f.coef(p, h2, "Load Coefficient", 1.0)
            f.coef(p, h2, "Bid Price Coefficient", 1.0)
    return f, _view(f)


def test_build_purchasers_skips_and_unlinked_converter() -> None:
    f, view = _purchaser_case()
    stats: dict[str, Any] = {"purchaser_fixed_mw": 0.0, "electrolyser_mw": 0.0}
    index = isp_c.ConstraintIndex.build(f.res)
    isp_c.build_purchasers(
        view, index, isp_c.ConstraintOptions(electrolyser_kwh_per_kg=50.0), stats
    )
    assert stats["purchasers"] == {
        "flexible": 1,
        "skipped_no_units": 1,
        "skipped_zero_load": 2,
    }
    assert view.purchasers["ELY"].h2_constraint == "H2"
    # a flexible purchaser outside any H2 constraint keeps no to_node
    view.purchasers["ELY"].h2_constraint = None
    view.system = {}
    isp_c._emit_purchasers(view, isp_c.ConstraintOptions(electrolyser_kwh_per_kg=50.0))
    (cc,) = view.system["carrier_converter_array"]
    assert "to_node" not in cc


def test_purchaser_ref_demand_model() -> None:
    f = Fake()
    opts = isp_c.ConstraintOptions(h2_model="demand")
    assert isp_c.purchaser_ref(_view(f), opts, "E") == 'demand("E").load'


# ---------------------------------------------------------------------------
# directional flows
# ---------------------------------------------------------------------------


def test_split_flow_convexity() -> None:
    t_pos = isp_c.Term('decision_variable("x").value', 1.0, "Line", "L")
    t_neg = isp_c.Term('decision_variable("x").value', -1.0, "Line", "L")
    assert not isp_c._split_flow_convex(_ev(sense=-1, terms=[t_neg]))
    assert not isp_c._split_flow_convex(_ev(sense=1, terms=[t_pos]))
    assert isp_c._split_flow_convex(_ev(sense=1, terms=[t_neg]))
    assert not isp_c._split_flow_convex(_ev(sense=0, terms=[t_pos]))


def test_emit_split_flows_ignores_unknown_names() -> None:
    f = Fake()
    view = _view(f, lines={"L1"})
    ucs = [
        {"expression": 'decision_variable("flow_fwd_OTHER").value <= 1'},
        {"name": "x"},
    ]
    assert isp_c._emit_split_flows(view, ucs) == 0
    assert view.system["decision_variable_array"] == []


# ---------------------------------------------------------------------------
# coefficient rows and term resolution
# ---------------------------------------------------------------------------


def test_row_coef_variable_profile() -> None:
    f = Fake()
    var = f.obj("Variable", "V")
    nod = f.obj("Node", "N")
    novar = f.obj("Variable", "NoProfile")
    f.prop(var, "Profile", 2.0)
    view = _view(f)
    got = isp_c._row_coef(view, [_row(1.5, others=(var, nod, novar))])
    assert got is not None
    assert np.allclose(got[0], 3.0)
    # the latest live tagged row wins; expired tagged rows fall back to plain
    got = isp_c._row_coef(
        view,
        [
            _row(1.0, others=(var,), frm="2026-01-01"),
            _row(4.0, others=(var,), frm="2027-01-01"),
        ],
    )
    assert got is not None and np.allclose(got[0], 8.0)
    stale = [_row(1.0, others=(var,), to="2026-01-01"), _row(7.0)]
    assert isp_c._row_coef(view, stale) == (7.0,)
    assert isp_c._row_coef(view, [_row(7.0, df="trace")]) is None


def _term_case() -> tuple[Fake, isp_c.CaseView, dict[str, int]]:
    f = Fake()
    ids = {
        "G": f.obj("Generator", "G"),
        "GX": f.obj("Generator", "GX"),
        "CAND": f.obj("Generator", "CAND"),
        "B": f.obj("Battery", "B"),
        "BX": f.obj("Battery", "BX"),
        "BC": f.obj("Battery", "BC"),
        "L": f.obj("Line", "L"),
        "LX": f.obj("Line", "LX"),
        "N": f.obj("Node", "N"),
        "NX": f.obj("Node", "NX"),
        "PF": f.obj("Purchaser", "PF"),
        "PX": f.obj("Purchaser", "PX"),
        "FX": f.obj("Fuel", "FX"),
        "R": f.obj("Region", "R"),
    }
    f.prop(ids["B"], "Units", 2.0)
    f.prop(ids["B"], "Max Power", 10.0)
    f.prop(ids["CAND"], "Aux Incr", 10.0)
    view = _view(
        f,
        gens={"G": isp_c.GenInfo("G", "b1", 0.0)},
        batteries={"B": "b1"},
        lines={"L"},
        node_load={"N": np.full(24, 5.0)},
        expansion=frozenset({"CAND", "BC"}),
    )
    view.purchasers["PF"] = isp_c.PurchaserInfo(
        "PF", "b1", "fixed", 3.0, 0.0, fixed=np.full(24, 3.0)
    )
    return f, view, ids


@pytest.mark.parametrize(
    ("cls", "prop", "expansion", "expect"),
    [
        ("G", "Units Built in Year Coefficient", False, ("dropped", "build_term_zero")),
        ("G", "Pump Load Coefficient", False, ("dropped", "pump_load_not_modelled")),
        ("G", "Load Coefficient", False, ("unsupported", "Generator.Load Coefficient")),
        ("GX", "Generation Coefficient", False, ("dropped", "object_not_in_case")),
        ("CAND", "Generation Coefficient", False, ("ref", 'generator("CAND")')),
        ("B", "Generation Capacity Coefficient", False, ("const", 20.0)),
        ("BC", "Generation Capacity Coefficient", False, ("dropped", "capacity_zero")),
        ("BC", "Generation Capacity Coefficient", True, ("ref", "converter(")),
        ("BX", "Generation Coefficient", False, ("dropped", "object_not_in_case")),
        ("B", "Generation Coefficient", False, ("ref", ".discharge")),
        ("B", "Load Coefficient", False, ("ref", ".charge")),
        ("B", "Flow Coefficient", False, ("unsupported", "Battery.Flow Coefficient")),
        ("LX", "Flow Coefficient", False, ("dropped", "object_not_in_case")),
        ("L", "Flow Coefficient", False, ("ref", 'line("L").flow')),
        ("L", "Load Coefficient", False, ("unsupported", "Line.Load Coefficient")),
        ("N", "Load Coefficient", False, ("const", 5.0)),
        ("NX", "Load Coefficient", False, ("dropped", "object_not_in_case")),
        ("PF", "Load Coefficient", False, ("const", 3.0)),
        ("PX", "Load Coefficient", False, ("dropped", "object_not_in_case")),
        ("FX", "Offtake Coefficient", False, ("dropped", "object_not_in_case")),
        ("R", "Load Coefficient", False, ("unsupported", "Region.Load Coefficient")),
    ],
)
def test_add_term_by_class(
    cls: str, prop: str, expansion: bool, expect: tuple[str, object]
) -> None:
    _f, view, ids = _term_case()
    ev = _ev()
    opts = isp_c.ConstraintOptions(expansion=expansion)
    isp_c._add_term(view, opts, ev, ids[cls], prop, [_row(1.0)])
    kind, value = expect
    if kind == "dropped":
        assert ev.dropped[str(value)] == 1
    elif kind == "unsupported":
        assert ev.unsupported == {value}
    elif kind == "const":
        assert ev.const is not None and ev.const[0] == pytest.approx(value)
    else:
        assert len(ev.terms) == 1 and str(value) in ev.terms[0].ref


def test_add_term_candidate_aux_and_missing_rows() -> None:
    _f, view, ids = _term_case()
    ev = _ev()
    opts = isp_c.ConstraintOptions()
    isp_c._add_term(view, opts, ev, ids["CAND"], "Generation Coefficient", [_row(0.9)])
    assert ev.terms[0].coef == pytest.approx(1.0)
    isp_c._add_term(view, opts, ev, ids["G"], "Generation Coefficient", [])
    isp_c._add_term(view, opts, ev, ids["G"], "Generation Coefficient", [_row(0.0)])
    assert ev.dropped["no_live_coefficient"] == 1
    assert ev.dropped["zero_coefficient"] == 1
    assert not ev.fixed_loads


def test_simplify_and_lhs_text_profiles() -> None:
    prof = np.array([1.0, 2.0])
    assert isinstance(isp_c._simplify(prof), np.ndarray)
    assert isp_c._simplify(np.array([3.0, 3.0])) == 3.0
    terms = [
        isp_c.Term("x", prof, "G", "x"),
        isp_c.Term("y", prof, "G", "y"),
        isp_c.Term("z", -2.0, "G", "z"),
    ]
    assert isp_c.lhs_text(terms) == "[1, 2] * x + [1, 2] * y - 2 * z"
    assert isp_c.lhs_text([isp_c.Term("z", -1.0, "G", "z")]) == "-z"


# ---------------------------------------------------------------------------
# user-constraint translation
# ---------------------------------------------------------------------------


def _uc_case(**rhs: float) -> tuple[Fake, isp_c.CaseView]:
    f = Fake()
    f.con("C", **rhs)
    return f, _view(f, days=2)


GTERM = isp_c.Term('generator("G").generation', 1.0, "Generator", "G", 1)


def _ev2(**kw: Any) -> isp_c.EvalConstraint:
    kw.setdefault("const", np.zeros(48))
    return _ev(**kw)


def test_to_user_constraint_period_branches() -> None:
    opts = isp_c.ConstraintOptions()
    f, view = _uc_case(RHS_Month=31.0)
    groups = _groups()
    notes: list[str] = []
    ev = _ev2(rhs_kind="RHS Month", const=np.ones(48))
    assert isp_c._to_user_constraint(view, opts, ev, groups, notes) is None
    ev = _ev2(rhs_kind="RHS Month")
    assert isp_c._to_user_constraint(view, opts, ev, groups, notes) is None
    ev = _ev2(rhs_kind="RHS Month", terms=[GTERM], penalty=2000.0)
    uc = isp_c._to_user_constraint(view, opts, ev, groups, notes)
    assert uc is not None and uc["penalty"] == pytest.approx(2.0)
    # no live period row
    f.res.rows[(1, "RHS Month")] = [_row(31.0, frm="2030-01-01")]
    ev = _ev2(rhs_kind="RHS Month", terms=[GTERM])
    assert isp_c._to_user_constraint(view, opts, ev, groups, notes) is None
    g = groups["operational"]
    assert g["skipped: capacity term in a period constraint"] == 1
    assert g["skipped: no term in the case (trivially satisfied)"] == 1
    assert g["skipped: RHS period does not overlap the window"] == 1


def test_to_user_constraint_per_block_branches() -> None:
    opts = isp_c.ConstraintOptions()
    groups = _groups()
    notes: list[str] = []
    f, view = _uc_case()
    assert isp_c._to_user_constraint(view, opts, _ev2(), groups, notes) is None
    f.prop(1, "RHS", 5.0)
    f.prop(1, "RHS", 9.0, frm="2027-01-19")
    # constant-only: ≤ uses the minimum RHS, ≥ the maximum
    assert isp_c._to_user_constraint(view, opts, _ev2(), groups, notes) is None
    ev = _ev2(sense=1)
    assert isp_c._to_user_constraint(view, opts, ev, groups, notes) is None
    ev = _ev2(terms=[GTERM], penalty=10.0)
    uc = isp_c._to_user_constraint(view, opts, ev, groups, notes)
    assert uc is not None
    assert uc["penalty"] == 10.0 and uc["rhs"] == "rhs"
    assert uc["_rhs"][-1] == pytest.approx(9.0)
    g = groups["operational"]
    assert g["skipped: no RHS live in the window"] == 1
    assert g["skipped: no term in the case (VIOLATED by constants)"] == 1
    assert g["converted: per block"] == 1


def test_to_user_constraint_rhs_day_branches() -> None:
    opts = isp_c.ConstraintOptions()
    groups = _groups()
    f, view = _uc_case()
    f.prop(1, "RHS Day", 1.0)
    f.prop(1, "RHS Day", 2.0, frm="2027-01-19")
    ev = _ev2(rhs_kind="RHS Day")
    assert isp_c._to_user_constraint(view, opts, ev, groups, []) is None
    ev = _ev2(rhs_kind="RHS Day", terms=[GTERM], penalty=3000.0)
    uc = isp_c._to_user_constraint(view, opts, ev, groups, [])
    assert uc is not None
    assert uc["daily_sum"] and uc["rhs"] == "rhs" and uc["penalty"] == 3.0
    assert groups["operational"]["converted: daily_sum"] == 1


def test_constant_only_equality_and_unknown_sense() -> None:
    groups = _groups()
    isp_c._constant_only(_ev(sense=0), 1.0, groups)
    isp_c._constant_only(_ev(sense=7), -5.0, groups)
    g = groups["operational"]
    assert g["skipped: no term in the case (VIOLATED by constants)"] == 1
    assert g["skipped: no term in the case (trivially satisfied)"] == 1


def test_constraint_oid_unknown() -> None:
    with pytest.raises(KeyError):
        isp_c._constraint_oid(_view(Fake()), "missing")


def test_capacity_constraint_branches() -> None:
    f, view = _uc_case(RHS_Year=100.0)
    groups = _groups()
    cap = isp_c.Term('generator("G").capainst', 1.0, "Generator", "G")
    ev = _ev2(rhs_kind="RHS Year", group="capacity_build", unsupported={"X.Y"})
    assert isp_c._capacity_constraint(view, ev, groups) is None
    ev = _ev2(rhs_kind="RHS Year", group="capacity_build", terms=[cap], penalty=5.0)
    uc = isp_c._capacity_constraint(view, ev, groups)
    assert uc is not None and uc["penalty"] == 5.0 and uc["scope"] == "stage"
    prof = isp_c.Term("x", np.array([1.0, 2.0]), "Generator", "G")
    ev = _ev2(rhs_kind="RHS Year", group="capacity_build", terms=[prof])
    assert isp_c._capacity_constraint(view, ev, groups) is None
    ev = _ev2(rhs_kind="RHS Year", group="capacity_build")
    assert isp_c._capacity_constraint(view, ev, groups) is None
    f.res.rows[(1, "RHS Year")] = [_row(1.0, df="trace")]
    ev = _ev2(rhs_kind="RHS Year", group="capacity_build", terms=[cap])
    assert isp_c._capacity_constraint(view, ev, groups) is None
    g = groups["capacity_build"]
    assert g["skipped: unsupported term X.Y"] == 1
    assert g["skipped: time-varying capacity coefficient"] == 1
    assert g["skipped: no RHS live in the window"] == 1
    assert g["skipped: no term in the case (trivially satisfied)"] == 1


# ---------------------------------------------------------------------------
# hydrogen demand
# ---------------------------------------------------------------------------


def test_h2_demand_branches() -> None:
    opts = isp_c.ConstraintOptions(electrolyser_kwh_per_kg=50.0)
    f, view = _uc_case(RHS_Month=31.0)
    view.purchasers["E"] = isp_c.PurchaserInfo("E", "b", "flexible", 1.0, 0.0)
    groups = _groups()
    notes: list[str] = []
    pterm = isp_c.Term("p", 1.0, "Purchaser", "E", -1)

    def h2(**kw: Any) -> dict[str, Any] | None:
        kw.setdefault("rhs_kind", "RHS Month")
        kw.setdefault("group", "h2_consumption")
        return isp_c._h2_demand(view, opts, _ev2(**kw), groups, notes)

    assert h2(rhs_kind="RHS") is None
    assert h2() is None  # no feeder, NONZERO demand dropped
    assert h2(terms=[isp_c.Term("p", 2.0, "Purchaser", "E", -1)]) is None
    item = h2(terms=[pterm], sense=1, penalty=1000.0)
    assert item is not None
    assert "max_energy" not in item and item["min_energy"] > 0
    assert item["min_energy_cost"] == pytest.approx(1.0 / opts.h2_efficiency)
    assert notes[0].startswith("WARNING C")
    f.res.rows[(1, "RHS Month")] = [_row(0.0)]
    assert h2() is None  # zero demand, nothing lost
    f.res.rows[(1, "RHS Month")] = [_row(1.0, frm="2030-01-01")]
    assert h2(terms=[pterm]) is None
    g = groups["h2_consumption"]
    assert g["skipped: H2 constraint without a period RHS"] == 1
    assert g["skipped: no electrolyser in the case (NONZERO H2 demand dropped)"] == 1
    assert g["skipped: no electrolyser in the case"] == 1
    assert g["skipped: non-unit purchaser coefficient"] == 1
    assert g["skipped: RHS period does not overlap the window"] == 1


# ---------------------------------------------------------------------------
# emissions
# ---------------------------------------------------------------------------


def _emission_case(*, rate: bool = True) -> tuple[Fake, isp_c.CaseView]:
    f = Fake()
    co2 = f.obj("Emission", "CO2")
    f.prop(co2, "Price", 25.0)
    g = f.obj("Generator", "G")
    fuel = f.obj("Fuel", "Gas")
    if rate:
        f.res.coll_rows[(co2, fuel, "Fuels::Production Rate")].append(_row(50.0))
        f.res.coll_rows[(co2, g, "Generators::Production Rate")].append(
            _row(1.0, df="trace")
        )
    budgets = (
        ("Off", {"Include_in_LT_Plan": 0.0, "RHS_Custom": 1.0}),
        ("Late", {"RHS_Custom": 1.0}),
        ("NoCoef", {"RHS_Month": 31.0}),
        ("Ge", {"RHS_Month": 31.0, "Sense": 1.0}),
        ("Pen", {"RHS_Month": 31.0, "Penalty_Price": 100.0}),
        ("NoPen", {"RHS_Month": 31.0}),
    )
    for name, props in budgets:
        b = f.con(name, "Emissions Budget")
        for prop, value in props.items():
            kw = {"frm": "2030-01-01"} if name == "Late" else {}
            f.prop(b, prop.replace("_", " "), value, **kw)
        if name == "NoCoef":
            f.coef(co2, b, "Other Coefficient", 1.0)
            f.coef(co2, b, "Production Coefficient", 1.0, frm="2030-01-01")
        else:
            f.coef(co2, b, "Production Coefficient", 0.001)
    gens = {
        "G": isp_c.GenInfo("G", "b", 0.0, heat_rate=10.0, fuel="Gas"),
        "NOTINXML": isp_c.GenInfo("NOTINXML", "b", 0.0),
    }
    return f, _view(f, gens=gens)


def test_build_emissions_budget_branches() -> None:
    f, view = _emission_case()
    stats: dict[str, Any] = {}
    groups = _groups()
    notes: list[str] = []
    index = isp_c.ConstraintIndex.build(f.res)
    isp_c.build_emissions(view, index, isp_c.ConstraintOptions(), stats, groups, notes)
    zones = {z["name"]: z for z in view.system["emission_zone_array"]}
    assert "price_by_emission" not in zones["isp_co2_total"]
    assert zones["isp_Pen"]["cap_cost"] == pytest.approx(0.1)
    assert "cap_cost" not in zones["isp_NoPen"]
    (src,) = [s for s in view.system["emission_source_array"] if s["zone"] == "isp_Pen"]
    assert src["rate"] == pytest.approx(0.5)
    g = groups["emissions"]
    assert g["skipped: disabled (Include in LT Plan = 0)"] == 1
    assert g["skipped: budget period does not overlap the window"] == 1
    assert g["skipped: no live coefficient"] == 1
    assert g["skipped: non ≤ emission constraint"] == 1
    assert stats["emitting_generators"] == 1


def test_build_emissions_without_rates_or_objects() -> None:
    f, view = _emission_case(rate=False)
    index = isp_c.ConstraintIndex.build(f.res)
    opts = isp_c.ConstraintOptions()
    isp_c.build_emissions(view, index, opts, {}, _groups(), [])
    assert "emission_array" not in view.system
    f = Fake()
    view = _view(f)
    isp_c.build_emissions(view, index, opts, {}, _groups(), [])
    assert "emission_array" not in view.system


# ---------------------------------------------------------------------------
# REZ buses
# ---------------------------------------------------------------------------


def _inj(cls: str, obj: str, coef: float | np.ndarray = 1.0, inj: int = 1) -> Any:
    return isp_c.Term(f"{cls}.{obj}", coef, cls, obj, inj)


@pytest.mark.parametrize(
    ("sides", "why"),
    [
        ({"import": {}}, "import limit without an export limit"),
        ({"export": {"sense": 1}}, "not a hard per-block <= limit"),
        ({"export": {"terms": []}}, "no element in the case"),
        (
            {"export": {"terms": [_inj("Generator", "G", inj=0)]}},
            "term is not a REZ injection (flow / fuel / other)",
        ),
        (
            {"export": {"terms": [_inj("Generator", "OTHER")]}},
            "term is not a REZ injection (flow / fuel / other)",
        ),
        (
            {"export": {"terms": [_inj("Generator", "G", np.ones(2))]}},
            "non-unit coefficient",
        ),
        ({"export": {"terms": [_inj("Generator", "G", 2.0)]}}, "non-unit coefficient"),
        (
            {"export": {"fixed_loads": [("P", 1.0)]}},
            "non-unit coefficient",
        ),
        (
            {
                "export": {},
                "import": {"terms": [_inj("Battery", "B", 1.0, -1)]},
            },
            "export and import limits cover different elements",
        ),
        ({"export": {}, "import": {"terms": [_inj("Generator", "G", -1.0)]}}, None),
    ],
)
def test_rez_reject_reason(sides: dict[str, dict[str, Any]], why: str | None) -> None:
    elem_bus = {("Generator", "G"): "b", ("Battery", "B"): "b"}
    evs = {}
    for side, kw in sides.items():
        kw = dict(kw)
        kw.setdefault("terms", [_inj("Generator", "G")])
        evs[side] = _ev(**kw)
    assert isp_c._rez_reject_reason(evs, elem_bus) == why


def test_rez_key_without_prefix() -> None:
    assert isp_c._rez_key("Q1") == "Q1"


def _rez_case() -> tuple[Fake, isp_c.CaseView]:
    f = Fake()
    system: dict[str, Any] = {
        "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
        "line_array": [],
        "generator_array": [
            {"name": "G1", "bus": "b1", "capacity": 50.0},
            {"name": "G2", "bus": "b2"},
            {"name": "G3", "bus": "b1"},
            {"name": "G4", "bus": "b1", "capacity": 10.0},
        ],
        "battery_array": [
            {"name": "B1", "bus": "b1", "pmax_charge": 5.0, "pmax_discharge": 6.0},
            {"name": "B2", "bus": "b1", "pmax_charge": 1.0, "pmax_discharge": 1.0},
        ],
        "demand_array": [{"name": "PF", "bus": "b1"}, {"name": "D", "bus": "b1"}],
        "carrier_converter_array": [
            {"name": "PE", "from_node": "b1"},
            {"name": "X", "from_node": "b1"},
        ],
    }
    gens = {
        n: isp_c.GenInfo(n, "b2" if n == "G2" else "b1", 0.0)
        for n in ("G1", "G2", "G3", "G4")
    }
    view = _view(f, gens=gens, batteries={"B1": "b1", "B2": "b1"}, system=system)
    view.purchasers["PF"] = isp_c.PurchaserInfo(
        "PF", "b1", "fixed", 2.0, 0.0, fixed=np.full(24, 2.0)
    )
    view.purchasers["PE"] = isp_c.PurchaserInfo("PE", "b1", "flexible", 3.0, 0.0)
    return f, view


def _rez_ev(f: Fake, name: str, cat: str, rhs: float, **kw: Any) -> Any:
    f.con(name, cat, RHS=rhs)
    return _ev(name=name, category=cat, **kw)


def test_build_rez_buses_paths() -> None:
    f, view = _rez_case()
    exp_cat = "REZ Network Export Constraints"
    imp_cat = "REZ Network Import Constraints"
    evaluated = [
        # Q1: export + import over G1, B1 (both directions), PE and fixed PF
        _rez_ev(
            f,
            "ExportLimit_Q1",
            exp_cat,
            100.0,
            terms=[
                _inj("Generator", "G1"),
                _inj("Battery", "B1"),
                _inj("Battery", "B1", -1.0, -1),
                _inj("Purchaser", "PE", -1.0, -1),
            ],
            fixed_loads=[("PF", -1.0)],
            const=np.full(24, -2.0),
        ),
        _rez_ev(
            f,
            "ImportLimit_Q1",
            imp_cat,
            40.0,
            terms=[
                _inj("Generator", "G1", -1.0),
                _inj("Battery", "B1", -1.0),
                _inj("Battery", "B1", 1.0, -1),
                _inj("Purchaser", "PE", 1.0, -1),
            ],
            fixed_loads=[("PF", 1.0)],
            const=np.full(24, 2.0),
        ),
        # Q2: elements on two buses
        _rez_ev(
            f,
            "ExportLimit_Q2",
            exp_cat,
            10.0,
            terms=[_inj("Generator", "G2"), _inj("Generator", "G3")],
        ),
        # Q3 / Q4 share G4 → both dropped
        _rez_ev(f, "ExportLimit_Q3", exp_cat, 10.0, terms=[_inj("Generator", "G4")]),
        _rez_ev(f, "ExportLimit_Q4", exp_cat, 10.0, terms=[_inj("Generator", "G4")]),
        # Q5: negative limit after folding constants
        _rez_ev(
            f,
            "ExportLimit_Q5",
            exp_cat,
            1.0,
            terms=[_inj("Battery", "B2")],
            const=np.full(24, 5.0),
        ),
        # Q6: rejected (penalised)
        _rez_ev(f, "ExportLimit_Q6", exp_cat, 1.0, penalty=5.0),
        # not a REZ category
        _ev(name="Other"),
    ]
    stats: dict[str, Any] = {}
    groups = _groups()
    done = isp_c.build_rez_buses(view, evaluated, stats, groups)
    assert done == {"ExportLimit_Q1", "ImportLimit_Q1"}
    assert stats["rez_not_line_equivalent"] == {
        "elements on several sub-region buses": 1,
        "element shared with another REZ": 1,
        "negative limit after folding constants": 1,
        "not a hard per-block <= limit": 1,
    }
    q1 = stats["rez_buses"]["Q1"]
    assert q1["import_limit"]
    # the folded fixed load moves to the REZ bus: limits are the raw RHS
    assert q1["export_mw"] == [100.0, 100.0]
    assert q1["import_mw"] == [40.0, 40.0]
    sysm = view.system
    assert sysm["battery_array"][0]["bus"] == "REZ_Q1"
    assert sysm["battery_array"][1]["bus"] == "b1"
    assert sysm["demand_array"][0]["bus"] == "REZ_Q1"
    assert sysm["demand_array"][1]["bus"] == "b1"
    assert sysm["carrier_converter_array"][0]["from_node"] == "REZ_Q1"
    assert sysm["carrier_converter_array"][1]["from_node"] == "b1"
    assert view.purchasers["PF"].bus == "REZ_Q1"
    assert view.batteries["B1"] == "REZ_Q1"


def test_rez_capacities_default_limits() -> None:
    _f, view = _rez_case()
    elems = {("Generator", "G1"), ("Generator", "G2"), ("Battery", "B1")}
    elems |= {("Purchaser", "PE"), ("Node", "N")}
    assert isp_c._rez_gen_capacity(view, elems) == pytest.approx(56.0)
    assert isp_c._rez_load_capacity(view, elems) == pytest.approx(8.0)
    view.system.pop("battery_array")
    assert isp_c._rez_load_capacity(view, {("Battery", "B1")}) == 0.0
    assert isp_c._rez_gen_capacity(view, {("Battery", "B1")}) == 0.0


def test_build_rez_export_only_uses_load_capacity() -> None:
    f, view = _rez_case()
    ev = _rez_ev(
        f,
        "ExportLimit_Q",
        "DREZ Export Constraints",
        30.0,
        terms=[_inj("Purchaser", "PE", -1.0, -1)],
    )
    stats: dict[str, Any] = {}
    isp_c.build_rez_buses(view, [ev], stats, _groups())
    assert stats["rez_buses"]["Q"]["import_mw"] == [4.0, 4.0]


# ---------------------------------------------------------------------------
# apply(): option switches and skip reasons
# ---------------------------------------------------------------------------


def _apply_case() -> tuple[Fake, isp_c.CaseView]:
    f = Fake()
    g = f.obj("Generator", "G")
    gas = f.obj("Fuel", "Gas")
    pur = f.obj("Purchaser", "E")
    co = f.obj("Company", "Co")
    trace = f.con("Trace", RHS=0.0)
    f.res.rows[(trace, "RHS")] = [_row(0.0, df="tr")]
    f.coef(g, trace, "Generation Coefficient", 1.0)
    uns = f.con("Uns", RHS=1.0)
    f.coef(g, uns, "Load Coefficient", 1.0)
    late = f.con("Late")
    f.prop(late, "RHS", 1.0, frm="2030-01-01")
    f.coef(g, late, "Generation Coefficient", 1.0)
    fuel = f.con("Fuel", RHS_Day=1.0)
    f.coef(gas, fuel, "Offtake Coefficient", 1.0)
    net = f.con("Net", "Network", RHS=1.0)
    f.coef(pur, net, "Load Coefficient", 1.0)
    vret = f.con("Vret", RHS=1.0)
    f.coef(co, vret, "Generation Coefficient", 1.0)
    h2 = f.con("H2 X", "H2 Subregion Domestic Consumption", RHS=1.0)
    f.coef(pur, h2, "Load Coefficient", 1.0)
    bld = f.con("Build")
    f.coef(g, bld, "Capacity Built Coefficient", 1.0)
    view = _view(
        f,
        gens={"G": isp_c.GenInfo("G", "b", 0.0, heat_rate=10.0, fuel="Gas")},
    )
    view.system["carrier_converter_array"] = [
        {"name": "E", "to_node": "h2_gone", "to_carrier": "hydrogen"},
        {"name": "K"},
    ]
    return f, view


def test_apply_skip_reasons() -> None:
    _f, view = _apply_case()
    opts = isp_c.ConstraintOptions(purchasers=False, emissions=False)
    stats: dict[str, Any] = {}
    ucs = isp_c.apply(view, opts, stats)
    assert [uc["name"] for uc in ucs] == ["isp_Fuel"]
    g = stats["constraint_groups"]
    assert g["operational"]["skipped: Data File trace not available"] == 1
    assert g["operational"]["skipped: unsupported term Generator.Load Coefficient"] == 1
    assert g["operational"]["skipped: no RHS live in the window"] == 1
    assert g["purchaser_network"]["skipped: --no-purchasers"] == 1
    assert g["h2_consumption"]["skipped: --no-purchasers"] == 1
    assert g["company_vret"]["skipped: company-level yearly policy (VRET)"] == 1
    assert g["fuel_offtake"]["converted: daily_sum"] == 1
    assert "directional_flow_variables" not in stats


def test_apply_without_constraints_or_fuels() -> None:
    _f, view = _apply_case()
    opts = isp_c.ConstraintOptions(purchasers=False, emissions=False, constraints=False)
    stats: dict[str, Any] = {}
    isp_c.apply(view, opts, stats)
    g = stats["constraint_groups"]
    assert g["operational"]["skipped: --no-isp-constraints"] == 3
    _f, view = _apply_case()
    opts = isp_c.ConstraintOptions(purchasers=False, emissions=False, fuels=False)
    isp_c.apply(view, opts, stats)
    g = stats["constraint_groups"]
    assert g["fuel_offtake"]["skipped: --no-fuel-offtake"] == 1


def test_apply_h2_native_prunes_dangling_converters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    f, view = _apply_case()
    # E2 feeds the H2 Y target; E feeds a node that never gets a demand
    view.system["carrier_converter_array"].append(
        {"name": "E2", "to_node": "h2_H2_Y", "to_carrier": "hydrogen"}
    )
    view.purchasers["E2"] = isp_c.PurchaserInfo("E2", "b", "flexible", 10.0, 0.0)
    e2 = f.obj("Purchaser", "E2")
    h2y = f.con("H2 Y", "H2 Subregion Domestic Consumption", sense=-1)
    f.prop(h2y, "RHS Month", 0.0)
    f.coef(e2, h2y, "Load Coefficient", 1.0)
    # keep the hand-made purchasers (the fake XML has no purchaser data)
    monkeypatch.setattr(isp_c, "build_purchasers", lambda *_a, **_k: None)
    stats: dict[str, Any] = {}
    opts = isp_c.ConstraintOptions(
        emissions=False, expansion=True, electrolyser_kwh_per_kg=50.0
    )
    isp_c.apply(view, opts, stats)
    ccs = {c["name"]: c for c in view.system["carrier_converter_array"]}
    assert "to_node" not in ccs["E"]
    assert ccs["E2"]["to_node"] == "h2_H2_Y"
    assert [n["name"] for n in view.system["hydrogen_node_array"]] == ["h2_H2_Y"]
    g = stats["constraint_groups"]
    assert g["h2_consumption"]["skipped: H2 constraint without a period RHS"] == 1
    assert g["capacity_build"]["skipped: no RHS live in the window"] == 1


def test_add_term_sent_out_generation_not_aux_scaled() -> None:
    _f, view, ids = _term_case()
    view.gens["G"].aux = 0.5
    ev = _ev()
    isp_c._add_term(
        view,
        isp_c.ConstraintOptions(),
        ev,
        ids["G"],
        "Generation Sent Out Coefficient",
        [_row(2.0)],
    )
    assert ev.terms[0].coef == pytest.approx(2.0)


def test_emission_sources_only_in_covering_zones() -> None:
    f, view = _emission_case()
    nox = f.obj("Emission", "NOX")
    gas = f.res.db.objs["Fuel"][0].object_id
    f.res.coll_rows[(nox, gas, "Fuels::Production Rate")].append(_row(1.0))
    index = isp_c.ConstraintIndex.build(f.res)
    isp_c.build_emissions(view, index, isp_c.ConstraintOptions(), {}, _groups(), [])
    by_zone: dict[str, set[str]] = defaultdict(set)
    for s in view.system["emission_source_array"]:
        by_zone[s["zone"]].add(s["emission"])
    assert by_zone["isp_co2_total"] == {"CO2", "NOX"}
    assert by_zone["isp_Pen"] == {"CO2"}
