# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Storages in the private evaluation (``storage_valuation``).

**Default (``cash``, every market):** the evaluation is cash.  A unit
receives the marginal price for its energy (CMg × energy) and pays its
variable cost; the storage shadow values are part of gtopt's dispatch,
not a cost the evaluator charges.

* thermal units pay their declared variable cost;
* hydro below a reservoir pays only its VOM (``gcost``: water has no
  cash cost);
* a battery pays its charging energy at the CMg (its own project rows);
* LNG-linked units pay their VOM (``gcost``) plus heat rate × the
  terminal's purchase price (``delivery_cost``, else ``spot_cost``):
  evaluated alone, a gas unit is charged for the gas it burns at the
  declared fuel price.  The terminal / owner account (refill, vent)
  lists the cargo purchases paid in cash.

**``shadow`` (opt-in diagnostic, per storage type).**  Every gtopt
storage publishes the dual of its balance row, the shadow value ϑ of
one more stored unit [$/stored unit, discounting and
``scale_objective`` undone, "valuable" = positive]:

========================  ==============================  ============
storage                   output stream                   stored unit
========================  ==============================  ============
``Reservoir``             ``Reservoir/water_value_dual``  hm³
``Battery``               ``Battery/energy_dual``         MWh
``LngTerminal``           ``LngTerminal/energy_dual``     m³ LNG
========================  ==============================  ============

With ``shadow`` a unit pays ϑ × what it draws (heat rate for LNG,
fcr × turbined flow net of the downstream reservoir for hydro,
1 / output efficiency for a battery) instead of a cash fuel cost, which
splits the profit between a storage and the units drawing from it and
values the end-of-horizon inventory.  For one owner of the storage and
its units (representative-year money)::

    unit margins (shadow) + storage margin = energy revenue − VOM
                                             − refill cost − vent cost
    storage margin = Σ ϑ · draw − refill cost − vent cost

and, by the storage balance of every block,
``Σ ϑ · draw = Σ ϑ · (inflow − vent) − Σ ϑ · ΔE − Σ ϑ · loss``;
``closing_value − opening_value`` (ϑ · E at the end and the start of
the horizon) turns the cash profit into the economic profit.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gtopt_pricing_uplift._data import Period

    from .case_data import SystemData, _Reader

#: valuation modes
VALUATIONS = ("cash", "shadow")
#: storage types (``storage_valuation`` mapping keys)
STORAGE_TYPES = ("lng_terminal", "reservoir", "battery")
#: output stream of each storage type's balance dual
DUAL_STREAM = {
    "lng_terminal": "LngTerminal/energy_dual",
    "reservoir": "Reservoir/water_value_dual",
    "battery": "Battery/energy_dual",
}
_CLASS = {"lng_terminal": "LngTerminal", "reservoir": "Reservoir", "battery": "Battery"}

#: draw per hour of a unit in one period, in stored units [unit/h], with
#: the storage it is valued at: [(storage type, storage uid, quantity)]
DrawFn = Callable[["Period"], list[tuple[str, int, float]]]


def valuation_modes(cfg: Any, market: str) -> dict[str, str]:
    """Mode of every storage type from ``storage_valuation`` + market default.

    ``cfg``: ``None``, a mode string for every type, or a mapping
    ``{type: mode}``.  Unknown types / modes raise ``ValueError``.
    """
    del market  # same default in every market
    modes = dict.fromkeys(STORAGE_TYPES, "cash")
    if cfg is None:
        return modes
    items = dict.fromkeys(STORAGE_TYPES, cfg) if isinstance(cfg, str) else dict(cfg)
    for kind, mode in items.items():
        if kind not in STORAGE_TYPES:
            raise ValueError(f"storage_valuation: unknown storage type {kind!r}")
        if mode not in VALUATIONS:
            raise ValueError(
                f"storage_valuation.{kind} = {mode!r}: expected one of {VALUATIONS}"
            )
        modes[kind] = str(mode)
    return modes


@dataclass
class StorageAccount:
    """Yearly accounts of one storage and the units drawing from it."""

    kind: str  #: storage type (``lng_terminal`` / ``reservoir`` / ``battery``)
    name: str
    uid: int
    units: list[str] = field(default_factory=list)
    energy_revenue: float = 0.0  #: Σ CMg · P · h of the units
    vom: float = 0.0  #: Σ (unit cost) · P · h
    shadow_fuel: float = 0.0  #: Σ ϑ · draw (what the units pay at shadow)
    inflow: float = 0.0  #: stored units entering (bought / charged / natural)
    refill_cost: float = 0.0  #: purchase price / grid charge / 0 (hydro)
    vent: float = 0.0  #: stored units vented / spilled by the storage
    vent_cost: float = 0.0
    inflow_value: float = 0.0  #: Σ ϑ · (inflow − vent)
    inventory_change_value: float = 0.0  #: Σ ϑ_b · (E_b − E_{b−1})
    loss_value: float = 0.0  #: Σ ϑ_b · standing loss_b
    opening_value: float = 0.0  #: ϑ · E at the start (first block)
    closing_value: float = 0.0  #: ϑ · E at the end (last block)
    details: dict[str, float] = field(default_factory=dict)

    @property
    def unit_margin(self) -> float:
        """Σ (CMg − ϑ · draw/MWh − VOM) · P · h."""
        return self.energy_revenue - self.vom - self.shadow_fuel

    @property
    def storage_margin(self) -> float:
        """What the units pay at shadow value − refill − vent cost."""
        return self.shadow_fuel - self.refill_cost - self.vent_cost

    @property
    def owner_profit(self) -> float:
        """Cash view of one owner: energy − VOM − refill − vent cost."""
        return self.energy_revenue - self.vom - self.refill_cost - self.vent_cost

    @property
    def economic_profit(self) -> float:
        """Cash profit + inventory change at shadow value."""
        return self.owner_profit + self.closing_value - self.opening_value

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.update(
            unit_margin=self.unit_margin,
            storage_margin=self.storage_margin,
            owner_profit=self.owner_profit,
            economic_profit=self.economic_profit,
        )
        return out


# ── topology: which unit draws what from which storage ───────────────


def _uid_of(ref: Any, by_name: Mapping[str, int]) -> int | None:
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        return by_name.get(ref)
    return None


def _names(items: list[dict[str, Any]]) -> dict[str, int]:
    return {str(it.get("name")): int(it["uid"]) for it in items if "uid" in it}


def _num(v: Any, default: float) -> float:
    return (
        float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    )


class StorageModel:
    """Storages of one solved case and the units that draw from them."""

    def __init__(
        self,
        sysd: SystemData,
        reader: _Reader,
        system: dict[str, Any],
        gens: list[dict[str, Any]],
    ) -> None:
        self.sysd = sysd
        self.reader = reader
        self.system = system
        self.gens = gens
        self.gen_by_name = _names(gens)
        #: generator uid -> (storage type, draw function)
        self.draw: dict[int, tuple[str, DrawFn]] = {}
        self._lng()
        self._reservoirs()
        self._batteries()

    # ── helpers ─────────────────────────────────────────────────────
    def theta(self, kind: str, p: Period, uid: int) -> float:
        dual = self.reader.series(DUAL_STREAM[kind]) or {}
        return dual.get((p.scenario, p.stage, p.block, uid), 0.0)

    def _sol(self, stem: str, p: Period, uid: int) -> float:
        s = self.reader.series(stem) or {}
        return s.get((p.scenario, p.stage, p.block, uid), 0.0)

    def _gen_q(self, p: Period, guid: int) -> float:
        return self._sol("Generator/generation_sol", p, guid)

    # ── LNG: linked generators draw heat_rate × P ───────────────────
    def _lng(self) -> None:
        links: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for term in self.system.get("lng_terminal_array", []) or []:
            for link in term.get("generators", []) or []:
                guid = _uid_of(link.get("generator"), self.gen_by_name)
                if guid is not None:
                    links[guid].append(
                        (int(term["uid"]), _num(link.get("heat_rate"), 1.0))
                    )
        for guid, ls in links.items():

            def fn(p: Period, guid: int = guid, ls: list = ls) -> list:
                q = self._gen_q(p, guid)
                return [("lng_terminal", t, hr * q) for t, hr in ls]

            self.draw[guid] = ("lng_terminal", fn)

    # ── hydro: turbine water from its reservoir, minus downstream ───
    def _reservoirs(self) -> None:
        rsv_at: dict[int, tuple[int, float]] = {}
        junctions = _names(self.system.get("junction_array", []) or [])
        for r in self.system.get("reservoir_array", []) or []:
            j = _uid_of(r.get("junction"), junctions)
            if j is not None:
                fcr = _num(r.get("flow_conversion_rate"), 0.0036)
                rsv_at[j] = (int(r["uid"]), fcr)
        if not rsv_at:
            return
        wws = self.system.get("waterway_array", []) or []
        ww_by_name = _names(wws)
        ww_ends = {
            int(w["uid"]): (
                _uid_of(w.get("junction_a"), junctions),
                _uid_of(w.get("junction_b"), junctions),
            )
            for w in wws
        }
        for t in self.system.get("turbine_array", []) or []:
            ww = _uid_of(t.get("waterway"), ww_by_name)
            guid = _uid_of(t.get("generator"), self.gen_by_name)
            if ww is None or guid is None or ww not in ww_ends:
                continue
            ja, jb = ww_ends[ww]
            up = rsv_at.get(ja) if ja is not None else None
            down = rsv_at.get(jb) if jb is not None else None
            if up is None:
                continue  # run of river: no reservoir upstream

            def fn(p: Period, ww: int = ww, up=up, down=down) -> list:
                flow = self._sol("Waterway/flow_sol", p, ww)
                out = [("reservoir", up[0], up[1] * flow)]
                if down is not None:
                    out.append(("reservoir", down[0], -down[1] * flow))
                return out

            self.draw[guid] = ("reservoir", fn)

    # ── battery: discharge generator draws discharge / η_out ────────
    def _batteries(self) -> None:
        for b in self.system.get("battery_array", []) or []:
            guid = self.gen_by_name.get(f"{b.get('name')}_gen")
            if guid is None:
                continue
            uid = int(b["uid"])

            def fn(p: Period, uid: int = uid, b: dict = b) -> list:
                eta = (
                    self.reader.sched(b.get("output_efficiency"), "Battery", p, uid)
                    or 1.0
                )
                return [("battery", uid, self._sol("Battery/fout_sol", p, uid) / eta)]

            self.draw[guid] = ("battery", fn)

    # ── per unit ────────────────────────────────────────────────────
    def kind_of(self, guid: int) -> str | None:
        hit = self.draw.get(guid)
        return hit[0] if hit else None

    def fuel_cash(self, guid: int, p: Period) -> float:
        """Cash fuel cost of unit ``guid`` per MWh [$/MWh] (LNG only).

        Heat rate × the terminal's purchase price of the stage:
        ``delivery_cost`` (contract), else ``spot_cost``, else 0 — the
        declared price of the gas a stand-alone gas unit burns.  Water and
        battery energy have no cash fuel cost (the battery buys its charge
        at the CMg, in its own project rows).
        """
        total = 0.0
        for term in self.system.get("lng_terminal_array", []) or []:
            for link in term.get("generators", []) or []:
                if _uid_of(link.get("generator"), self.gen_by_name) != guid:
                    continue
                uid = int(term["uid"])
                price = self.reader.sched(
                    term.get("delivery_cost"), "LngTerminal", p, uid
                )
                if price is None:
                    price = self.reader.sched(
                        term.get("spot_cost"), "LngTerminal", p, uid
                    )
                total += _num(link.get("heat_rate"), 1.0) * (price or 0.0)
        return total

    def fuel_value(self, guid: int, p: Period, q: float) -> float:
        """Shadow value of what unit ``guid`` draws per MWh [$/MWh]."""
        hit = self.draw.get(guid)
        if hit is None or abs(q) < 1e-12:
            return 0.0
        cost = sum(self.theta(k, p, s) * d for k, s, d in hit[1](p))
        return cost / q

    # ── accounts ────────────────────────────────────────────────────
    def accounts(self, kinds: set[str]) -> list[StorageAccount]:
        out: list[StorageAccount] = []
        arrays = {
            "lng_terminal": "lng_terminal_array",
            "reservoir": "reservoir_array",
            "battery": "battery_array",
        }
        for kind in STORAGE_TYPES:
            if kind not in kinds:
                continue
            for elem in self.system.get(arrays[kind], []) or []:
                acc = self._account(kind, elem)
                if acc is not None:
                    out.append(acc)
        return out

    def _account(self, kind: str, elem: dict[str, Any]) -> StorageAccount | None:
        sysd, reader = self.sysd, self.reader
        uid = int(elem["uid"])
        units = {
            g: fn
            for g, (k, fn) in self.draw.items()
            if k == kind and any(s == uid for _k, s, _d in fn(_probe(sysd)))
        }
        if not units and kind != "battery":
            return None
        acc = StorageAccount(kind=kind, name=str(elem.get("name")), uid=uid)
        gname = {int(g["uid"]): g for g in self.gens}
        acc.units = sorted(str(gname[g].get("name")) for g in units)
        cls = _CLASS[kind]
        eprev: dict[int, float] = {}
        first: dict[int, bool] = {}
        seen_stage: set[tuple[int, int]] = set()
        last: dict[int, tuple[float, float, float]] = {}
        loss_rate = self._loss_rate(kind, elem)
        fcr = (
            _num(elem.get("flow_conversion_rate"), 0.0036)
            if kind == "reservoir"
            else _num(elem.get("flow_conversion_rate"), 1.0)
        )
        for p, prob in sysd.periods:
            key3 = (p.scenario, p.stage, p.block)
            w = p.duration * prob * sysd.scale
            wst = prob * sysd.scale
            v = self.theta(kind, p, uid)
            for g, fn in units.items():
                q = self._gen_q(p, g)
                if q:
                    gen = gname[g]
                    acc.energy_revenue += (
                        sysd.bus_price(key3, _bus_uid(sysd.case, gen)) * q * w
                    )
                    acc.vom += reader.gcost(gen, p) * q * w
                for _k, s, d in fn(p):
                    if s == uid:
                        acc.shadow_fuel += v * d * p.duration * wst
            inflow, cost, det = self._inflow(
                kind, elem, p, (p.scenario, p.stage) not in seen_stage
            )
            seen_stage.add((p.scenario, p.stage))
            acc.refill_cost += cost * wst
            for k, val in det.items():
                acc.details[k] = acc.details.get(k, 0.0) + val * wst
            vent = self._sol(f"{cls}/drain_sol", p, uid) * fcr * p.duration
            if kind == "reservoir":
                vent += self._spill(elem, p)
            acc.vent += vent * wst
            acc.vent_cost += _num(elem.get("spillway_cost"), 0.0) * vent * wst
            acc.inflow += inflow * wst
            acc.inflow_value += v * (inflow - vent) * wst
            e = self._sol(f"{cls}/energy_sol", p, uid)
            e0 = eprev.get(p.scenario, _num(elem.get("eini"), 0.0))
            acc.inventory_change_value += v * (e - e0) * wst
            acc.loss_value += v * e0 * loss_rate / 8760.0 * p.duration * wst
            if p.scenario not in first:
                first[p.scenario] = True
                acc.opening_value += v * e0 * wst
            last[p.scenario] = (v, e, wst)
            eprev[p.scenario] = e
        acc.closing_value = sum(v * e * wst for v, e, wst in last.values())
        return acc

    def _loss_rate(self, kind: str, elem: dict[str, Any]) -> float:
        val = elem.get("annual_standing_loss")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        if kind == "battery":
            opts = (self.sysd.case.planning.get("options", {}) or {}).get(
                "model_options", {}
            ) or {}
            return _num(opts.get("battery_annual_standing_loss"), 0.48)
        return 0.0

    def _inflow(
        self, kind: str, elem: dict[str, Any], p: Period, first_block: bool
    ) -> tuple[float, float, dict[str, float]]:
        """Stored units entering in period ``p``, their refill cost, details.

        A reservoir's natural inflows (``Flow`` elements at its junction)
        are free: refill cost 0.
        """
        uid = int(elem["uid"])
        reader, sysd = self.reader, self.sysd
        if kind == "reservoir":
            return self._natural_inflow(elem, p), 0.0, {}
        if kind == "battery":
            charge = self._sol("Battery/finp_sol", p, uid) * p.duration
            eta = reader.sched(elem.get("input_efficiency"), "Battery", p, uid) or 1.0
            price = sysd.bus_price(
                (p.scenario, p.stage, p.block), self._charge_bus(elem)
            )
            return eta * charge, price * charge, {"grid_charge": charge}
        # lng_terminal: contracted cargoes + spot cargoes (first block)
        det: dict[str, float] = {}
        contract = self._sol("LngTerminal/delivery_sol", p, uid) * p.duration
        cost = 0.0
        if contract:
            price = (
                reader.sched(elem.get("delivery_cost"), "LngTerminal", p, uid) or 0.0
            )
            cost += price * contract
            det.update(contract_volume=contract, contract_cost=price * contract)
        inflow = contract
        if first_block:
            n = self._spot(p, uid)
            if n:
                vol = _num(elem.get("spot_cargo_volume"), 0.0)
                price = (
                    reader.sched(elem.get("spot_cost"), "LngTerminal", p, uid) or 0.0
                )
                cost += price * n * vol
                det.update(
                    spot_cargoes=n, spot_volume=n * vol, spot_cost=price * n * vol
                )
                inflow += n * vol
        return inflow, cost, det

    def _junction(self, elem: dict[str, Any]) -> int | None:
        return _uid_of(
            elem.get("junction"), _names(self.system.get("junction_array", []) or [])
        )

    def _natural_inflow(self, elem: dict[str, Any], p: Period) -> float:
        """Σ direction · discharge of the ``Flow`` elements at the junction [hm³]."""
        j = self._junction(elem)
        fcr = _num(elem.get("flow_conversion_rate"), 0.0036)
        total = 0.0
        for f in self.system.get("flow_array", []) or []:
            if j is None or self._junction(f) != j:
                continue
            q = self._stb(f.get("discharge"), "Flow", p, int(f["uid"]))
            total += _num(f.get("direction"), 1.0) * q
        return fcr * total * p.duration

    def _stb(self, value: Any, cls: str, p: Period, uid: int) -> float:
        """A ``[scenario][stage][block]`` schedule (``Flow.discharge``)."""
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], list)
            and value[0]
            and isinstance(value[0][0], list)
        ):
            scens = (self.sysd.case.planning.get("simulation", {}) or {}).get(
                "scenario_array", []
            ) or []
            uids = [int(sc.get("uid", i + 1)) for i, sc in enumerate(scens)]
            pos = uids.index(p.scenario) if p.scenario in uids else 0
            value = value[min(pos, len(value) - 1)]
        return self.reader.sched(value, cls, p, uid) or 0.0

    def _spill(self, elem: dict[str, Any], p: Period) -> float:
        """Water leaving the reservoir junction by non-turbine waterways [hm³]."""
        j = self._junction(elem)
        jn = _names(self.system.get("junction_array", []) or [])
        wn = _names(self.system.get("waterway_array", []) or [])
        turb = {
            _uid_of(t.get("waterway"), wn)
            for t in self.system.get("turbine_array", []) or []
        }
        fcr = _num(elem.get("flow_conversion_rate"), 0.0036)
        out = 0.0
        for w in self.system.get("waterway_array", []) or []:
            uid = int(w["uid"])
            if (
                uid not in turb
                and j is not None
                and _uid_of(w.get("junction_a"), jn) == j
            ):
                out += self._sol("Waterway/flow_sol", p, uid)
        return fcr * out * p.duration

    def _spot(self, p: Period, uid: int) -> float:
        s = self.reader.series("LngTerminal/spot_sol") or {}
        return sum(
            n
            for (sc, st, _b, u), n in s.items()
            if sc == p.scenario and st == p.stage and u == uid
        )

    def _charge_bus(self, elem: dict[str, Any]) -> int:
        dems = self.system.get("demand_array", []) or []
        dem = next(
            (d for d in dems if d.get("name") == f"{elem.get('name')}_dem"), None
        )
        ref = dem.get("bus") if dem else elem.get("bus")
        from .case_data import _bus  # pylint: disable=import-outside-toplevel

        return _bus(self.sysd.case, ref)


def _probe(sysd: SystemData) -> Period:
    return sysd.periods[0][0]


def _bus_uid(case: Any, gen: dict[str, Any]) -> int:
    from .case_data import _bus  # pylint: disable=import-outside-toplevel

    return _bus(case, gen.get("bus"))


def notes(kinds: set[str], accounts: list[StorageAccount]) -> list[str]:
    """Report lines for the storage accounts (``kinds``: shadow-valued)."""
    lines = []
    if kinds:
        lines.append(
            "storage_valuation = shadow (diagnostic) for "
            + ", ".join(sorted(kinds))
            + ": units drawing from these storages pay the storage shadow"
            " value (balance dual) instead of their cash fuel cost; refills"
            " at purchase price (LNG), 0 (natural inflow) or CMg (battery)."
        )
    for a in accounts:
        lines.append(
            f"{a.kind} {a.name}: units {', '.join(a.units) or '-'};"
            f" unit margin {a.unit_margin:,.0f} + storage margin"
            f" {a.storage_margin:,.0f} = owner profit {a.owner_profit:,.0f} $/y;"
            f" refill {a.refill_cost:,.0f}, vent {a.vent_cost:,.0f},"
            f" inventory {a.opening_value:,.0f} -> {a.closing_value:,.0f}"
        )
    return lines
