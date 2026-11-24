"""2024-format CEN BESS: batteries emulated with PLEXOS hydro objects.

Before PLEXOS ``Battery`` objects (2025+ CEN PCP bundles), CEN modelled
each BESS as a small hydro system:

* a ``Storage`` ``S`` (``BAT_<plant>``, volume = stored MWh),
* a discharge ``Generator`` ``G`` with ``Head Storage S`` (``Efficiency
  Incr`` 1 ⇒ 1 MWh of volume per MWh),
* a charge ``Generator`` ``L`` (``BAT_<plant>_LOAD``) with ``Head Storage
  P`` — the co-located plant's zero-volume pondage whose ``Natural
  Inflow`` is that plant's ``Gen_Rating`` — and ``Tail Storage S``
  (``Efficiency Incr`` 0.001 ⇒ ``gen_L = 0.001 · stored``),
* two 1-ended ``Loss_*`` waterways and their loss rows
  ``a · Generation(G) − Flow(Loss_S_GEN) = 0`` (drawn from ``S``) and
  ``b · Generation(L) − Flow(Loss_S_LOAD) = 0`` (drawn from ``P``).

Verified against the PLEXOS solution of 2024-12-15 (BAT_COYA_FV):
discharging 137.38 MW for one hour lowers ``S`` by 137.38·(1 + 0.0306) =
141.58, charging 137.38 MWh draws 137.38·(1 + 20·0.001) from the solar
plant, and ``solar generation + 1.02 · charge = solar availability`` holds
in every period.  That is exactly a battery with
``output_efficiency = 1 / (1/eff_G + a) = 0.970`` and
``input_efficiency = 1 / (1 + b·eff_L) = 0.980`` charged only from the
co-located plant — the same BESS PLEXOS models from 2025 on as a
``Battery`` (Charge/Discharge Efficiency 98 / 97 %) with a
``CRCA_<bat>: Load(bat) − Generation(plant) ≤ 0`` row.

The converter therefore translates every *standard* online group into a
gtopt ``Battery`` (bus = node of ``G``) plus the charge-source row
``battery(S).charge − generator(P).generation ≤ 0``, drops the hydro
objects, absorbs the two loss rows into the efficiencies, and rewrites
other constraint references (``Generation(G)`` → ``battery(S).discharge``,
``Generation(L)`` → ``eff_L · input_efficiency · battery(S).charge``).
Constraints that use a commitment / reserve term of ``G`` or ``L`` have no
gtopt Battery equivalent and are dropped with a warning (the ``*_Uniq``
no-simultaneous-charge rows: never optimal with η < 1, PLEXOS 2025+
``Simultaneous Charge and Discharge = 0``).

Groups whose generators are offline for the whole horizon (pmax = 0) keep
the legacy hydro emulation (nothing dispatches); only their loss rows —
``0 − flow = 0`` with a waterway the converter never emits — are skipped.
Non-standard online groups are left untouched so the strict
unresolved-reference check still reports them.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .entities import BatterySpec, GeneratorSpec, UserConstraintSpec
from .plexos_csv import read_long
from .plexos_loader import PlexosBundle
from .plexos_xml import PlexosDataRow, PlexosDb

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LossRow:
    """A PLEXOS ``<gen> Generation · a − Flow(loss waterway) = 0`` row."""

    constraint: str
    #: ``{generator: Generation Coefficient}``.
    gen_coeffs: tuple[tuple[str, float], ...]
    #: The 1-ended loss waterway (``Flow Coefficient`` −1).
    loss_waterway: str
    #: Its ``Storage From``.
    loss_source: str
    #: Any further waterway terms (``Act_*`` reserve-emulation arcs).
    other_waterways: tuple[str, ...] = ()

    @property
    def is_standard(self) -> bool:
        """One generator term and the loss waterway only."""
        return len(self.gen_coeffs) == 1 and not self.other_waterways


@dataclass(frozen=True)
class HydroBessGroup:
    """One hydro-emulated BESS (storage + discharge/charge generators)."""

    storage: str
    storage_object_id: int
    discharge_gen: str
    charge_gen: str | None
    #: Head Storage of the charge generator (co-located plant pondage).
    source_storage: str | None
    #: ``a`` (discharge loss per MWh) and ``b`` (charge-loss per charge-gen MW).
    gen_loss_coeff: float
    load_loss_coeff: float
    #: ``Efficiency Incr`` of the discharge / charge generators.
    eff_gen: float
    eff_load: float
    loss_rows: tuple[str, ...]
    #: The 1-ended loss waterways of those rows, plus any further waterway
    #: they reference (``Act_*`` reserve-emulation arcs).
    loss_waterways: tuple[str, ...]
    #: True when every row/membership matches the standard pattern.
    standard: bool
    #: Every generator of the group (``G``, ``L``, reserve emulators …).
    members: tuple[str, ...]
    #: Pumped form (2025 CEN bundles, e.g. BAT_DEL_DESIERTO): ``G`` itself
    #: charges ``S`` by pumping from its ``Tail Storage`` (the mirror
    #: tank, ``source_storage``).  ``Pump Load`` [MW] and ``Pump
    #: Efficiency`` (fraction) of ``G``; 0 for the 2024 two-generator form.
    pump_load: float = 0.0
    pump_efficiency: float = 0.0

    @property
    def output_efficiency(self) -> float:
        """Discharge efficiency ``1 / (1/eff_G + a)``."""
        return 1.0 / (1.0 / self.eff_gen + self.gen_loss_coeff)

    @property
    def input_efficiency(self) -> float:
        """Charge efficiency ``1 / (1 + b · eff_L)`` (pumped: Pump Efficiency)."""
        if self.pump_efficiency > 0.0:
            return self.pump_efficiency
        return 1.0 / (1.0 + self.load_loss_coeff * self.eff_load)

    @property
    def charge_gen_factor(self) -> float:
        """``Generation(L)`` per MW of gtopt battery charge."""
        return self.eff_load * self.input_efficiency


@dataclass(frozen=True)
class GeneratorAlias:
    """How a PLEXOS generator's ``Generation`` maps onto a gtopt Battery."""

    battery: str
    accessor: str  # "discharge" | "charge"
    factor: float


def _rows_by_membership(db: PlexosDb) -> dict[int, list[PlexosDataRow]]:
    out: dict[int, list[PlexosDataRow]] = {}
    for row in db.data_rows:
        out.setdefault(row.membership_id, []).append(row)
    return out


def _constraint_terms(
    db: PlexosDb,
) -> dict[str, list[tuple[str, str, str, float]]]:
    """``{constraint: [(parent_class, parent_name, property, value), …]}``."""
    cid = db.class_id("Constraint")
    if cid is None:
        return {}
    objs = db.object_by_id()
    props = {p.property_id: p.name for p in db.properties}
    rows = _rows_by_membership(db)
    out: dict[str, list[tuple[str, str, str, float]]] = {}
    for coll in db.collections:
        if coll.child_class_id != cid or coll.name != "Constraints":
            continue
        parent_class = db.classes_by_id.get(coll.parent_class_id, "")
        if parent_class == "System":
            continue
        for m in db.memberships_of(coll.collection_id):
            parent = objs.get(m.parent_object_id)
            child = objs.get(m.child_object_id)
            if parent is None or child is None:
                continue
            for r in rows.get(m.membership_id, ()):
                out.setdefault(child.name, []).append(
                    (parent_class, parent.name, props.get(r.property_id, ""), r.value)
                )
    return out


def _named_links(db: PlexosDb, parent: str, child: str, name: str) -> dict[str, str]:
    coll = db.collection_for_named(parent, child, name)
    if coll is None:
        return {}
    objs = db.object_by_id()
    out: dict[str, str] = {}
    for m in db.memberships_of(coll.collection_id):
        p = objs.get(m.parent_object_id)
        c = objs.get(m.child_object_id)
        if p is not None and c is not None:
            out.setdefault(p.name, c.name)
    return out


def _find_loss_rows(
    db: PlexosDb,
    terms_by_constraint: Mapping[str, list[tuple[str, str, str, float]]],
    ww_from: Mapping[str, str],
    ww_to: Mapping[str, str],
) -> list[LossRow]:
    constraint_ids = {o.name: o.object_id for o in db.objects_of_class("Constraint")}
    out: list[LossRow] = []
    for cname, terms in terms_by_constraint.items():
        gens: list[tuple[str, float]] = []
        loss: list[str] = []
        others: list[str] = []
        ok = True
        for pclass, pname, prop, val in terms:
            if pclass == "Generator" and prop == "Generation Coefficient":
                gens.append((pname, val))
            elif pclass == "Waterway" and prop == "Flow Coefficient":
                if val == -1.0 and pname in ww_from and pname not in ww_to:
                    loss.append(pname)
                else:
                    others.append(pname)
            else:
                ok = False
        if not ok or len(loss) != 1 or not gens or any(v <= 0.0 for _g, v in gens):
            continue
        oid = constraint_ids.get(cname)
        if oid is None:
            continue
        if db.static_property("Constraint", oid, "RHS") != 0.0:
            continue
        if db.static_property("Constraint", oid, "Sense") != 0.0:
            continue
        out.append(
            LossRow(
                constraint=cname,
                gen_coeffs=tuple(gens),
                loss_waterway=loss[0],
                loss_source=ww_from[loss[0]],
                other_waterways=tuple(others),
            )
        )
    return out


def detect_hydro_bess(db: PlexosDb) -> tuple[HydroBessGroup, ...]:
    """Detect every hydro-emulated BESS from its loss-row signature.

    A storage ``S`` is a BESS when a loss row's waterway drains ``S`` and
    every generator of that row has ``Head Storage S``.  The group is
    *standard* when ``S`` has exactly that one discharge generator (with no
    ``Tail Storage`` and no ``Pump Load``), at most one charge generator
    with ``Tail Storage S`` whose own loss row drains its ``Head Storage``,
    and both rows are a single generator term plus the loss waterway.
    """
    head = _named_links(db, "Generator", "Storage", "Head Storage")
    tail = _named_links(db, "Generator", "Storage", "Tail Storage")
    ww_from = _named_links(db, "Waterway", "Storage", "Storage From")
    ww_to = _named_links(db, "Waterway", "Storage", "Storage To")
    rows = _find_loss_rows(db, _constraint_terms(db), ww_from, ww_to)
    if not rows:
        return ()
    gen_ids = {o.name: o.object_id for o in db.objects_of_class("Generator")}
    sto_ids = {o.name: o.object_id for o in db.objects_of_class("Storage")}
    heads_of: dict[str, list[str]] = {}
    for g, s in head.items():
        heads_of.setdefault(s, []).append(g)
    tails_of: dict[str, list[str]] = {}
    for g, s in tail.items():
        tails_of.setdefault(s, []).append(g)

    def _eff(gen: str) -> float:
        oid = gen_ids.get(gen)
        v = db.static_property("Generator", oid, "Efficiency Incr") if oid else 0.0
        return v if v > 0.0 else 1.0

    candidates = [
        r
        for r in rows
        if all(head.get(g) == r.loss_source for g, _v in r.gen_coeffs)
        and r.loss_source in sto_ids
    ]
    # A charge row (``L`` drains its Head Storage ``P``) has the same shape;
    # it is recognised by ``L``'s Tail Storage being another BESS storage.
    sources = {r.loss_source for r in candidates}
    discharge_rows = [
        r
        for r in candidates
        if not any(tail.get(g) in sources for g, _v in r.gen_coeffs)
    ]
    out: list[HydroBessGroup] = []
    for drow in sorted(discharge_rows, key=lambda r: r.loss_source):
        s = drow.loss_source
        g, a = drow.gen_coeffs[0]
        charge_gens = tails_of.get(s, [])
        lrow: LossRow | None = None
        source: str | None = None
        charge: str | None = None
        if len(charge_gens) == 1:
            charge = charge_gens[0]
            source = head.get(charge)
            lrow = next(
                (
                    r
                    for r in rows
                    if any(gn == charge for gn, _v in r.gen_coeffs)
                    and r.loss_source == source
                ),
                None,
            )
        g_oid = gen_ids.get(g)
        pump_load = (
            db.static_property("Generator", g_oid, "Pump Load") if g_oid else 0.0
        )
        pump = pump_load > 0.0
        standard = (
            drow.is_standard
            and sorted(heads_of.get(s, [])) == [g]
            and g not in tail
            and not pump
            and len(charge_gens) <= 1
            and (
                charge is None
                or (
                    lrow is not None
                    and lrow.is_standard
                    and source is not None
                    and source != s
                )
            )
        )
        # Pumped form: ``G`` (Head S, Tail T) generates S → T and pumps
        # T → S; T is used by ``G`` alone, so it only mirrors S and the
        # group is a plain battery charged at ``Pump Load`` with ``Pump
        # Efficiency``.  Without this the charge path was lost (only the
        # discharge turbine was emitted) and an end target above eini
        # made the LP infeasible (PCP 2025-04-27).
        pump_eff = 0.0
        t = tail.get(g)
        mirror = (
            t is not None
            and t != s
            and t in sto_ids
            and not heads_of.get(t)
            and tails_of.get(t, []) == [g]
        )
        single = drow.is_standard and sorted(heads_of.get(s, [])) == [g]
        if not standard and pump and single and not charge_gens and mirror:
            pe = (
                db.static_property("Generator", g_oid, "Pump Efficiency")
                if g_oid is not None
                else 0.0
            )
            pump_eff = pe / 100.0 if pe > 1.0 else (pe if pe > 0.0 else 1.0)
            standard = True
            source = t
        else:
            pump_load = 0.0
        members = sorted(
            {gn for gn, _v in drow.gen_coeffs}
            | set(heads_of.get(s, []))
            | set(charge_gens)
            | ({gn for gn, _v in lrow.gen_coeffs} if lrow else set())
        )
        out.append(
            HydroBessGroup(
                storage=s,
                storage_object_id=sto_ids[s],
                discharge_gen=g,
                charge_gen=charge,
                source_storage=source,
                gen_loss_coeff=a,
                load_loss_coeff=(lrow.gen_coeffs[0][1] if lrow else 0.0),
                eff_gen=_eff(g),
                eff_load=_eff(charge) if charge else 1.0,
                loss_rows=tuple(
                    sorted({drow.constraint} | ({lrow.constraint} if lrow else set()))
                ),
                loss_waterways=tuple(
                    sorted(
                        {drow.loss_waterway, *drow.other_waterways}
                        | (
                            {lrow.loss_waterway, *lrow.other_waterways}
                            if lrow
                            else set()
                        )
                    )
                ),
                standard=standard,
                members=tuple(members),
                pump_load=pump_load,
                pump_efficiency=pump_eff,
            )
        )
    return tuple(out)


def _peak(gen: GeneratorSpec | None) -> float:
    if gen is None:
        return 0.0
    return max(gen.pmax_profile) if gen.pmax_profile else gen.pmax


def group_is_online(group: HydroBessGroup, gens: Mapping[str, GeneratorSpec]) -> bool:
    """True when any generator of the group can dispatch in the horizon."""
    return any(_peak(gens.get(n)) > 0.0 for n in group.members)


def build_battery(
    db: PlexosDb,
    bundle: PlexosBundle | None,
    group: HydroBessGroup,
    gens: Mapping[str, GeneratorSpec],
) -> BatterySpec:
    """Translate one standard online group into a gtopt :class:`BatterySpec`."""
    sid = group.storage_object_id
    emax = db.static_property("Storage", sid, "Max Volume")
    emin = db.static_property("Storage", sid, "Min Volume")
    eini: float | None = None
    if bundle is not None:
        for fname in ("BESS_IniValue.csv", "Hydro_InitialVolume.csv"):
            if eini is None and bundle.has(fname):
                series = read_long(bundle.csv(fname)).get(group.storage)
                if series:
                    eini = series[0]
    if eini is None:
        eini = db.static_property("Storage", sid, "Initial Volume")
    eini = min(max(eini, emin), emax) if emax > 0.0 else max(eini, emin)
    g = gens[group.discharge_gen]
    pmax_dis = _peak(g)
    pmax_ch = group.pump_load
    if group.charge_gen is not None:
        pmax_ch = _peak(gens.get(group.charge_gen)) / group.input_efficiency
    lg = gens.get(group.charge_gen) if group.charge_gen else None
    profile: tuple[float, ...] = ()
    if g.pmax_profile and len(set(g.pmax_profile)) > 1:
        if lg is None or tuple(lg.pmax_profile) == tuple(g.pmax_profile):
            profile = tuple(g.pmax_profile)
    max_starts = 0.0
    for name in (group.charge_gen, group.discharge_gen):
        oid = next(
            (o.object_id for o in db.objects_of_class("Generator") if o.name == name),
            None,
        )
        if oid is not None and max_starts <= 0.0:
            max_starts = db.static_property("Generator", oid, "Max Starts Day")
    pin = os.environ.get("GTOPT_BATTERY_PIN_EFIN", "1").strip()
    pin_efin = pin not in ("0", "false", "False", "no", "NO", "off")
    return BatterySpec(
        object_id=sid,
        name=group.storage,
        bus_name=g.bus_name,
        emin=emin,
        emax=emax,
        eini=eini,
        efin=eini if pin_efin else 0.0,
        pmax_charge=pmax_ch,
        pmax_discharge=pmax_dis,
        power_profile=profile,
        input_efficiency=group.input_efficiency,
        output_efficiency=group.output_efficiency,
        max_cycles_day=max_starts if max_starts > 0.0 else 0.0,
    )


def charge_source_constraint(
    group: HydroBessGroup, emitted_generators: frozenset[str]
) -> UserConstraintSpec | None:
    """``battery(S).charge − generator(P).generation ≤ 0`` (PLEXOS 2025+ CRCA)."""
    src = group.source_storage
    if src is None or src not in emitted_generators:
        if group.charge_gen is not None:
            logger.warning(
                "hydro-BESS %s: no generator named after its charge source "
                "storage %r — the battery may charge from the grid freely",
                group.storage,
                src,
            )
        return None
    return UserConstraintSpec(
        name=f"bess_charge_source_{group.storage}",
        expression=(
            f'1 * battery("{group.storage}").charge '
            f'- 1 * generator("{src}").generation <= 0'
        ),
        description=(
            f"2024 hydro-emulated BESS '{group.storage}': charged only from "
            f"co-located plant '{src}' (PLEXOS charge generator "
            f"'{group.charge_gen}' draws from storage '{src}' whose inflow is "
            f"the plant's Gen_Rating) — same row as the PLEXOS 2025+ "
            f"CRCA_{group.storage} (File: DBSEN_PRGDIARIO.xml)"
        ),
    )


def generator_aliases(
    groups: Sequence[HydroBessGroup],
) -> dict[str, GeneratorAlias]:
    """``{generator: alias}`` for the translated groups."""
    out: dict[str, GeneratorAlias] = {}
    for grp in groups:
        out[grp.discharge_gen] = GeneratorAlias(grp.storage, "discharge", 1.0)
        if grp.charge_gen is not None:
            out[grp.charge_gen] = GeneratorAlias(
                grp.storage, "charge", grp.charge_gen_factor
            )
    return out
