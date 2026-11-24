# -*- coding: utf-8 -*-

"""Writer for converting battery data (plpcenbat.dat) to GTOPT JSON format.

Data sources and their roles
-----------------------------
* ``plpcenbat.dat`` (via ``battery_parser``) – **primary** source for battery
  configuration: name, bus, injection centrals (FPC), FPD, emin, emax.
* ``plpcnfce.dat`` (BAT section via ``central_parser``) – source for battery
  UID (central number) and pmax_discharge (central pmax); also used to look up
  injection central pmax for pmax_charge.
* ``plpmanbat.dat`` (via ``manbat_parser``) – per-stage pmax_charge / pmax_discharge
  overrides (maintenance schedules).
* ``plpess.dat`` (via ``ess_parser``) – ESS model; capacity = pmax × hrs_reg.
* ``plpmaness.dat`` (via ``maness_parser``) – per-stage pmax_charge / pmax_discharge
  overrides for ESS (maintenance schedules).

Matching rule: plpcenbat.dat battery name == BAT central name in plpcnfce.dat.

When *no* plpcenbat.dat file is present but BAT centrals exist in plpcnfce.dat,
default battery parameters are applied (FPC=FPD=0.95, emax derived from pmax,
emin=0.0).

UID allocation
--------------
  Battery   uid = bat_central_number    (BAT central number in plpcnfce.dat)
  Generator uid = bat_central_number    (discharge path)
  Demand    uid = bat_central_number    (charge path)
  Converter uid = bat_central_number

Maintenance schedules
---------------------
When ``plpmanbat.dat`` or ``plpmaness.dat`` provides per-block overrides:

**plpmanbat.dat** (battery model, Fortran ``LeeManBat``):
  Each data line contains ``IBind EMin EMax`` (3 fields).
  These modify battery energy bounds per block.  In gtopt, they map to
  Battery ``emin``/``emax`` schedule files (``Battery/emin.parquet`` and
  ``Battery/emax.parquet``).

**plpmaness.dat** (ESS model, Fortran ``LeeManEss``):
  Each data line contains ``IBind Emin Emax DCMin DCMax [DCMod]`` (5-6 fields).
  Energy bounds map to Battery ``emin``/``emax`` schedules (same as above).
  DC power bounds map to Generator ``pmax`` (``Generator/pmax.parquet``)
  and Demand ``lmax`` (``Demand/lmax.parquet``) schedule files.
"""

import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict


from .base_writer import BaseWriter
from .battery_parser import BatteryParser
from .central_parser import CentralParser
from .block_parser import BlockParser
from .bus_parser import BusParser
from .stage_parser import StageParser
from .manbat_parser import ManbatParser
from .ess_parser import EssParser
from .maness_parser import ManessParser

_logger = logging.getLogger(__name__)

# Default battery parameters used when only plpcnfce.dat BAT centrals are present
_DEFAULT_FPC = 0.95
_DEFAULT_FPD = 0.95


def mloss_to_annual_standing_loss(mloss_pct: float) -> Optional[float]:
    """Convert PLP ``plpess.dat`` ``mloss`` [%/month] to gtopt ``annual_standing_loss``.

    PLP (``genpdess.f``) applies a per-block SoC drain of
    ``-bd/730 · ln(1 − mloss/100)`` (``bd`` = block hours).  gtopt's
    linear convention drains ``annual_standing_loss/8760 · bd`` per block, so the
    exact equivalent is ``annual_standing_loss = −12 · ln(1 − mloss/100)``
    (≈ ``0.12 · mloss`` for small ``mloss``).

    Returns ``None`` when ``mloss <= 0``: PLP writes ``0`` both for "no
    loss" and "not specified", so the field is left ABSENT and gtopt's
    planning-wide battery default (``model_options.battery_annual_standing_loss``,
    0.48 p.u./year = 4 %/month) applies.  ``mloss >= 100`` is clamped to
    a full drain within the month (annual_standing_loss = 12 · ln(100) cap).
    """
    if not mloss_pct > 0.0:
        return None
    frac = min(mloss_pct / 100.0, 0.99)
    return -12.0 * math.log1p(-frac)


class BatteryEntry(TypedDict, total=False):
    """Represents a battery in the GTOPT system."""

    uid: int
    name: str
    active: List[int]
    input_efficiency: float
    output_efficiency: float
    emin: float
    emax: float
    eini: float
    capacity: float


class ConverterEntry(TypedDict):
    """Represents a converter linking battery, generator and demand."""

    uid: int
    name: str
    battery: int
    generator: int
    demand: int
    capacity: float


class BatteryWriter(BaseWriter):
    """Converts battery/ESS data to GTOPT JSON arrays.

    Combines storage file data with ``plpcnfce.dat`` BAT centrals
    (for UID and pmax_discharge).  Source priority:

    1. ``plpess.dat`` (via ``ess_parser``) – ESS model; capacity = pmax × hrs_reg.
    2. ``plpcenbat.dat`` (via ``battery_parser``) – battery model; capacity = emax.
    3. BAT centrals only → default parameters applied.

    ``plpess.dat`` and ``plpcenbat.dat`` are mutually exclusive; the caller
    (PLPParser) ensures only one is populated.
    """

    def __init__(
        self,
        battery_parser: Optional[BatteryParser] = None,
        ess_parser: Optional[EssParser] = None,
        central_parser: Optional[CentralParser] = None,
        bus_parser: Optional[BusParser] = None,
        stage_parser: Optional[StageParser] = None,
        manbat_parser: Optional[ManbatParser] = None,
        maness_parser: Optional[ManessParser] = None,
        options: Optional[Dict[str, Any]] = None,
        block_parser: Optional[BlockParser] = None,
    ) -> None:
        """Initialize the BatteryWriter with optional parsers and configuration options."""
        super().__init__(None, options)
        self.battery_parser = battery_parser
        self.ess_parser = ess_parser
        self.central_parser = central_parser
        self.bus_parser = bus_parser
        self.stage_parser = stage_parser
        self.block_parser = block_parser
        # Unified maintenance accessor: manbat for battery, maness for ESS
        self._man_parser = manbat_parser or maness_parser
        self._clamped_warnings: list[str] = []

    def _bat_centrals(self) -> Dict[str, Dict[str, Any]]:
        """Return BAT-type centrals from plpcnfce.dat keyed by name."""
        if not self.central_parser:
            return {}
        return {
            str(c["name"]): c
            for c in self.central_parser.centrals
            if c.get("type") == "bateria"
        }

    def _all_centrals_by_name(self) -> Dict[str, Dict[str, Any]]:
        """Return ALL centrals from plpcnfce.dat keyed by name (for injection lookup)."""
        if not self.central_parser:
            return {}
        return {str(c["name"]): c for c in self.central_parser.centrals}

    def _charge_source(
        self, name: str, cenpc: str, batteries: Dict[str, Dict[str, Any]]
    ) -> Optional[str]:
        """Return the co-located plant that bounds the charge, or None.

        ``cenpc`` is the ``plpess.dat`` CenCarga or the first
        ``plpcenbat.dat`` injection central.  PLP (``genpdess.f`` LeeEss)
        resolves CenCarga by name and silently treats the ESS as
        ``DCMod = 0`` when it is not a known central.  Self-references,
        other batteries and ``<battery>_LOAD`` grid-charge phantoms are not
        a plant either.  Whether the coupling is active in a block is
        decided separately (``_ess_charge_blocks``).
        """
        if not cenpc or cenpc == name:
            return None
        if cenpc.endswith("_LOAD") and cenpc[: -len("_LOAD")] in batteries:
            return None
        if cenpc in batteries:
            return None
        if self.central_parser and cenpc not in self._all_centrals_by_name():
            _logger.warning(
                "Battery '%s': charge plant '%s' is not a central; uncoupled",
                name,
                cenpc,
            )
            return None
        return cenpc

    def _ess_charge_blocks(
        self,
        dcmod: int,
        charge_source: Optional[str],
        man: Optional[Dict[str, Any]],
    ) -> Optional[List[int]]:
        """Blocks where an ESS charge is bounded by its ``CenCarga`` plant.

        Returns ``None`` for "every block", a (possibly empty) list of PLP
        block numbers otherwise.  ``plpess.dat`` sets the DCMod of every
        block; ``plpmaness.dat`` overrides it for the listed blocks (a row
        without the DCMod field means DCMod = 1, ``genpdess.f`` LeeManEss).
        """
        if charge_source is None:
            return []
        man_dcmod: Dict[int, int] = {}
        if man is not None and "dcmod" in man:
            man_dcmod = {
                int(b): int(m)
                for b, m in zip(man["block_index"], man["dcmod"], strict=True)
            }
        if not man_dcmod:
            return None if dcmod >= 1 else []
        if dcmod < 1:
            return sorted(b for b, m in man_dcmod.items() if m >= 1)
        off = {b for b, m in man_dcmod.items() if m < 1}
        if not off:
            return None
        if self.block_parser is None or not self.block_parser.items:
            _logger.warning(
                "ESS coupled to '%s': plpmaness.dat switches DCMod off in"
                " %d block(s) but the block list is unknown; coupling"
                " applied to every block",
                charge_source,
                len(off),
            )
            return None
        return sorted(
            int(it["number"])
            for it in self.block_parser.items
            if int(it["number"]) not in off
        )

    def charge_coupling_constraints(
        self,
        entries: List[Dict[str, Any]],
        generator_names: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Co-located charge limit as user constraints (no uid yet).

        PLP ESS (``genpdess.f`` GenPDEssBloA) adds, per DCMod=1 block, the
        row ``ren_cp · P_cp − ren_ess · dc ≥ 0`` (RHS 0, ``plp-pdmatld.f``):
        the ESS charge ``dc`` — a load at the ESS bus — may not exceed the
        output of its CenCarga plant, which itself injects all of its
        output at its own bus.  In MW both sides are physical power, so the
        gtopt equivalent is ``charge_demand.load <= plant.generation``.
        The same row expresses the ``plpcenbat.dat`` co-located topology
        (plant splits its output between grid and battery, never forced
        through it), with gtopt's own battery semantics otherwise.

        Args:
            entries: Battery entries from ``_all_entries``.
            generator_names: Names present in ``generator_array``.  A plant
                missing there (filtered by the central writer) cannot be
                referenced; the coupling is dropped with a warning.
        """
        ucs: List[Dict[str, Any]] = []
        for entry in entries:
            src = entry.get("charge_source")
            blocks = entry.get("charge_blocks")
            if not src or blocks == []:
                continue
            name = entry["name"]
            if generator_names is not None and src not in generator_names:
                _logger.warning(
                    "Battery '%s': charge plant '%s' has no generator; charge"
                    " coupling dropped",
                    name,
                    src,
                )
                continue
            man = entry.get("man_data")
            has_dc_man = man is not None and "dcmax" in man
            dem = f"{name}_chrg" if has_dc_man else f"{name}_dem"
            expr = f"demand('{dem}').load - generator('{src}').generation <= 0"
            if blocks is not None:
                expr += f", for(block in {{{', '.join(map(str, blocks))}}})"
            ucs.append(
                {
                    "name": f"{name}_charge_source",
                    "expression": expr,
                    "description": (
                        f"co-located storage: charge of {name} <= output of {src}"
                    ),
                }
            )
        return ucs

    def _all_entries(self) -> List[Dict[str, Any]]:
        """Build the unified list of battery entries.

        Each entry contains all fields needed by ``to_battery_array()``,
        ``to_generator_array()``, ``to_demand_array()`` and
        ``to_converter_array()``:

          number, name, bus, busc, nc, nd, emin, emax,
          pmax_charge, pmax_discharge, annual_standing_loss, has_maintenance,
          man_stages, man_pmax_charge, man_pmax_discharge,
          charge_source, charge_blocks.

        Priority:
        1. plpess.dat entries matched to BAT centrals (ESS model)
        2. plpcenbat.dat entries matched to BAT centrals (battery model)
        3. BAT centrals with no matching file → defaults applied
        """
        batteries = self._bat_centrals()
        entries: List[Dict[str, Any]] = []

        # Determine maintenance data accessor
        man_parser = self._man_parser

        if self.ess_parser and self.ess_parser.esses:
            # ESS path: emax from plpess.dat directly (MWh capacity)
            for item in self.ess_parser.esses:
                name = item["name"]
                central = batteries.get(name, {})
                uid = central.get("number", 0)
                bus = central.get("bus", 1)
                nc = item["nc"]
                nd = item["nd"]
                emax = item["emax"]
                dcmax = item["dcmax"]
                mloss = item["mloss"]
                # pmax_discharge: dcmax from ESS, or central pmax.
                # Expansion batteries may have pmax=0 — fall back to emax.
                pmax_discharge = dcmax if dcmax > 0 else central.get("pmax", 0.0)
                if pmax_discharge == 0.0 and emax > 0.0:
                    pmax_discharge = emax
                pmax_charge = pmax_discharge

                man = man_parser.get_item_by_name(name) if man_parser else None

                # DCMod handling (PLP ``genpdess.f``):
                #   dcmod=0: standalone battery, free grid charging
                #   dcmod=1: charge limited by the CenCarga plant output
                #            (``ren_cp·P_cp − ren_ess·dc ≥ 0``); the charge
                #            is a load at the ESS bus and the plant injects
                #            ALL its output at its own bus.  Reproduced as a
                #            standalone battery + a ``charge <= generation``
                #            user constraint (see charge_coupling_constraints)
                #            — NOT via ``source_generator``.
                #   dcmod=2: regulation tank → mapped to hydro Reservoir, not Battery
                dcmod = item.get("dcmod", 0)
                cenpc = item.get("cenpc", "")

                if dcmod == 2 and cenpc:
                    # Skip: regulation tanks are handled by
                    # get_regulation_reservoirs() → hydro elements
                    continue

                charge_source = self._charge_source(name, cenpc, batteries)

                entries.append(
                    {
                        "number": uid,
                        "name": name,
                        "bus": bus,
                        "busc": bus,
                        "nc": nc,
                        "nd": nd,
                        "emin": 0.0,
                        "emax": emax,
                        "pmax_charge": pmax_charge,
                        "pmax_discharge": pmax_discharge,
                        "annual_standing_loss": mloss_to_annual_standing_loss(mloss),
                        "has_maintenance": man is not None,
                        "man_data": man,
                        "charge_source": charge_source,
                        "charge_blocks": self._ess_charge_blocks(
                            dcmod, charge_source, man
                        ),
                    }
                )
        elif self.battery_parser and self.battery_parser.batteries:
            for item in self.battery_parser.batteries:
                name = item["name"]
                central = batteries.get(name, {})
                # UID and pmax_discharge come from BAT central (authoritative)
                uid = central.get("number", item["number"])
                bus = item.get("bus", central.get("bus", 1))
                # Energy capacity directly from plpcenbat.dat
                emax = item.get("emax", 0.0)
                emin = item.get("emin", 0.0)
                # Efficiencies from plpcenbat.dat
                injections = item.get("injections", [])
                nc = injections[0]["fpc"] if injections else _DEFAULT_FPC
                nd = item.get("fpd", _DEFAULT_FPD)
                pmax_discharge = central.get("pmax", 0.0)
                # Expansion batteries have pmax=0 in plpcnfce.dat — the
                # actual power limit comes from plpmance on the source
                # generator.  Use emax as the structural upper bound so
                # that gtopt's LP-reduction does not elide the battery
                # (the per-block profile constrains actual dispatch).
                if pmax_discharge == 0.0 and emax > 0.0:
                    pmax_discharge = emax
                pmax_charge = pmax_discharge

                man = man_parser.get_item_by_name(name) if man_parser else None

                # Co-located plant (NIny > 0, first injection central).
                # PLP ``genpdbaterias.f`` lets the plant split its output
                # between its own grid bus and the battery (``g + EC <=
                # PMax``), it never forces the output through the battery.
                # gtopt: standalone battery on its bus + a ``charge <=
                # plant generation`` user constraint (same physics as the
                # plpess.dat DCMod=1 coupling, charge_coupling_constraints)
                # — NOT ``source_generator``, whose internal bus would make
                # the battery the plant's only outlet and curtail it at
                # ``pmax_charge``.  Self-references (``BESS1 → BESS1``) and
                # ``<battery>_LOAD`` grid-charge phantoms are not a plant.
                cenpc = injections[0]["name"] if injections else ""
                charge_source = self._charge_source(name, cenpc, batteries)

                entries.append(
                    {
                        "number": uid,
                        "name": name,
                        "bus": bus,
                        "busc": bus,
                        "nc": nc,
                        "nd": nd,
                        "emin": emin,
                        "emax": emax,
                        "pmax_charge": pmax_charge,
                        "pmax_discharge": pmax_discharge,
                        # plpcenbat.dat has no self-discharge field →
                        # leave absent so gtopt's battery default applies.
                        "annual_standing_loss": None,
                        "has_maintenance": man is not None,
                        "man_data": man,
                        "charge_source": charge_source,
                        "charge_blocks": None if charge_source else [],
                    }
                )

        return entries

    def to_battery_array(
        self, entries: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Build battery_array JSON list.

        When an entry has no DC-maintenance schedule, the unified battery
        fields (``bus``, ``pmax_charge``, ``pmax_discharge``, ``gcost``)
        are included so that ``System::expand_batteries()`` auto-generates
        the discharge Generator, charge Demand, and linking Converter.

        Co-located plants are NOT wired through ``source_generator``: the
        plant keeps its own grid bus and the coupling is emitted as a user
        constraint (``charge_coupling_constraints``).

        Entries with DC-maintenance (``man_data`` containing ``dcmax``)
        omit the ``bus`` field; the caller must create separate Generator,
        Demand, and Converter elements for those batteries.
        """
        if entries is None:
            entries = self._all_entries()

        batteries = []
        for entry in entries:
            emax = entry["emax"]
            emin = entry["emin"]
            has_man = entry.get("has_maintenance")
            man = entry.get("man_data")
            has_dc_man = man is not None and "dcmax" in man
            nc = entry["nc"]
            nd = entry["nd"]
            bat_name = entry["name"]
            bat_uid = entry["number"]
            clamp = self.options.get("clamp_battery_efficiency", True)
            if nc > 1.0:
                _logger.debug(
                    "(battery_efficiency) Battery '%s' (uid=%d):"
                    " input_efficiency value(s) > 1.0: [%s]",
                    bat_name,
                    bat_uid,
                    nc,
                )
                self._clamped_warnings.append(
                    f"Battery '{bat_name}' (uid={bat_uid}):"
                    f" input_efficiency clamped from {nc:.4g} to 1.0"
                )
                if clamp:
                    nc = 1.0
            if nd > 1.0:
                _logger.debug(
                    "(battery_efficiency) Battery '%s' (uid=%d):"
                    " output_efficiency value(s) > 1.0: [%s]",
                    bat_name,
                    bat_uid,
                    nd,
                )
                self._clamped_warnings.append(
                    f"Battery '{bat_name}' (uid={bat_uid}):"
                    f" output_efficiency clamped from {nd:.4g} to 1.0"
                )
                if clamp:
                    nd = 1.0
            bat: Dict[str, Any] = {
                "uid": bat_uid,
                "name": bat_name,
                "input_efficiency": nc,
                "output_efficiency": nd,
                "emin": "emin" if has_man else emin,
                "emax": "emax" if has_man else emax,
                "capacity": emax,
            }
            # Self-discharge: emit only a nonzero PLP ``mloss`` (already
            # converted to p.u./year).  Absent → gtopt applies its
            # lithium default (4 %/month, ``model_options.
            # battery_annual_standing_loss``).
            annual_standing_loss = entry.get("annual_standing_loss")
            if annual_standing_loss is not None and annual_standing_loss > 0.0:
                bat["annual_standing_loss"] = annual_standing_loss
            # Unified fields: only when no DC maintenance
            if not has_dc_man:
                bat["bus"] = entry["bus"]
                bat["pmax_charge"] = entry["pmax_charge"]
                bat["pmax_discharge"] = entry["pmax_discharge"]
                # `discharge_cost` (was `gcost` before the
                # `e54b795a4` unified-battery rename) — per-MWh cost
                # paid when discharging.  Forwarded to the synthetic
                # discharge `Generator.gcost` by
                # `System::expand_batteries()`.
                bat["discharge_cost"] = 0.0

            batteries.append(bat)
        return batteries

    def to_generator_array(
        self, entries: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Build discharge-path generator entries."""
        if entries is None:
            entries = self._all_entries()
        gens = []
        for entry in entries:
            pmax_d = entry["pmax_discharge"]
            man = entry.get("man_data")
            has_dc_man = man is not None and "dcmax" in man
            pmax_val: Any = "pmax" if has_dc_man else pmax_d
            gens.append(
                {
                    "uid": entry["number"],
                    "name": f"{entry['name']}_disch",
                    "bus": entry["bus"],
                    "pmin": 0.0,
                    "pmax": pmax_val,
                    "gcost": 0.0,
                    "capacity": pmax_d,
                }
            )
        return gens

    def to_demand_array(
        self, entries: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Build charge-path demand entries."""
        if entries is None:
            entries = self._all_entries()
        dems = []
        for entry in entries:
            man = entry.get("man_data")
            has_dc_man = man is not None and "dcmax" in man
            lmax_val: Any = "lmax" if has_dc_man else entry["pmax_charge"]
            dems.append(
                {
                    "uid": entry["number"],
                    "name": f"{entry['name']}_chrg",
                    "bus": entry["busc"],
                    "lmax": lmax_val,
                    # Charging is dispatchable, not curtailable load:
                    # pin fcost=0 so a positive global
                    # ``demand_fail_cost`` (gtopt default 1000 $/MWh)
                    # does not force the battery to charge at lmax.
                    "fcost": 0.0,
                }
            )
        return dems

    def to_converter_array(
        self, entries: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Build converter_array JSON list."""
        if entries is None:
            entries = self._all_entries()
        convs = []
        for entry in entries:
            num = entry["number"]
            name = entry["name"]
            convs.append(
                {
                    "uid": num,
                    "name": name,
                    "battery": name,
                    "generator": name,
                    "demand": name,
                    "capacity": entry.get("capacity", entry["pmax_discharge"]),
                }
            )
        return convs

    # Central types that can host a regulation reservoir (hydro units
    # with a junction and turbine in the gtopt topology).
    _HYDRO_CENTRAL_TYPES = frozenset({"serie", "pasada", "embalse"})

    def get_regulation_reservoirs(self) -> List[Dict[str, Any]]:
        """Build reservoir dicts for DCMod=2 ESS entries (regulation tanks).

        DCMod=2 ESS elements model small hydro regulation tanks (ERD —
        Embalse de Regulación Diaria) that attach to the paired generator's
        existing junction.  They have nc=nd≈1.0 (water, no electrical losses)
        and their emax is in MWh.

        The paired generator (cenpc) must be a hydro unit (serie, pasada, or
        embalse) that already has a junction in the gtopt hydro topology.
        The reservoir connects to that junction, so the turbine's capacity
        naturally enforces the joint capacity constraint
        (DCMod=2: P_gen + P_ess ≤ Pmax).

        Raises:
            ValueError: If the paired generator is not a hydro central type.

        Returns:
            List of reservoir dicts ready for ``reservoir_array``.
        """
        if not self.ess_parser or not self.ess_parser.esses:
            return []

        all_centrals = self._all_centrals_by_name()
        reservoirs: List[Dict[str, Any]] = []

        for item in self.ess_parser.esses:
            dcmod = item.get("dcmod", 0)
            cenpc = item.get("cenpc", "")
            if dcmod != 2 or not cenpc:
                continue

            name = item["name"]
            emax = item["emax"]
            mloss = item["mloss"]

            # Look up the paired generator's central
            cenpc_central = all_centrals.get(cenpc, {})
            junction_uid = cenpc_central.get("number")
            if junction_uid is None:
                raise ValueError(
                    f"DCMod=2 ESS '{name}': paired generator '{cenpc}' "
                    f"not found in central_parser"
                )

            # Validate the paired generator is a hydro unit
            cenpc_type = cenpc_central.get("type", "")
            if cenpc_type not in self._HYDRO_CENTRAL_TYPES:
                raise ValueError(
                    f"DCMod=2 ESS '{name}': paired generator '{cenpc}' "
                    f"has type '{cenpc_type}', expected one of "
                    f"{sorted(self._HYDRO_CENTRAL_TYPES)}. "
                    f"Regulation tanks require a hydro central with a "
                    f"junction and turbine."
                )

            # The BAT central for this ESS provides the UID
            batteries = self._bat_centrals()
            bat_central = batteries.get(name, {})
            uid = bat_central.get("number", 0)

            reservoirs.append(
                {
                    "uid": uid,
                    "name": name,
                    "junction": junction_uid,
                    "emin": 0.0,
                    "emax": emax,
                    "capacity": emax,
                    # Regulation tank (hydro): never the battery
                    # default — emit the PLP loss explicitly, 0 if none.
                    "annual_standing_loss": mloss_to_annual_standing_loss(mloss) or 0.0,
                    "daily_cycle": True,
                }
            )

        return reservoirs

    def _write_lmax_parquet(
        self, entries: List[Dict[str, Any]], output_dir: Path
    ) -> None:
        """Write charge demand lmax values to Demand/lmax.parquet.

        When maintenance schedules are present, the per-stage pmax_charge
        values are written as block-level columns.
        """
        # Placeholder: maintenance parquet writing is handled in process()

    def _write_maintenance_parquet(
        self, entries: List[Dict[str, Any]], output_dir: Path
    ) -> None:
        """Write maintenance-schedule parquet files for battery/ESS.

        For entries with ``has_maintenance == True``, writes:

        **plpmanbat.dat** (battery model, ``man_data`` has ``emin``/``emax``):
        - ``Battery/emin.parquet`` with per-block emin values
        - ``Battery/emax.parquet`` with per-block emax values

        **plpmaness.dat** (ESS model, ``man_data`` has ``emin``/``emax``
        + ``dcmin``/``dcmax``):
        - ``Battery/emin.parquet`` + ``Battery/emax.parquet`` (energy bounds)
        - ``Generator/pmax.parquet`` (dcmax → discharge gen pmax)
        - ``Demand/lmax.parquet`` (dcmax → charge demand lmax)
        """
        import pandas as pd  # pylint: disable=import-outside-toplevel

        man_entries = [e for e in entries if e.get("has_maintenance")]
        if not man_entries:
            return

        # --- Battery energy bounds → Battery/emin, Battery/emax ---
        bat_dir = output_dir / "Battery"
        bat_dir.mkdir(parents=True, exist_ok=True)
        emin_data: Dict[str, Any] = {"block": []}
        emax_data: Dict[str, Any] = {"block": []}
        for entry in man_entries:
            man = entry["man_data"]
            col = f"uid:{entry['number']}"
            block_idx = man["block_index"]
            capacity = entry["emax"]
            if len(block_idx) > 0 and capacity > 0:
                emin_data["block"] = list(block_idx)
                emax_data["block"] = list(block_idx)
                emin_data[col] = list(man["emin"])
                emax_data[col] = list(man["emax"])
        if emin_data["block"]:
            self.write_dataframe(pd.DataFrame(emin_data), bat_dir, "emin")
            self.write_dataframe(pd.DataFrame(emax_data), bat_dir, "emax")

        # --- DC power bounds (ESS only) → Generator/pmax, Demand/lmax ---
        has_dc = any("dcmax" in e.get("man_data", {}) for e in man_entries)
        if not has_dc:
            return

        gen_dir = output_dir / "Generator"
        gen_dir.mkdir(parents=True, exist_ok=True)
        pmax_data: Dict[str, Any] = {"block": []}
        for entry in man_entries:
            man = entry["man_data"]
            if "dcmax" not in man:
                continue
            col = f"uid:{entry['number']}"
            block_idx = man["block_index"]
            if len(block_idx) > 0:
                pmax_data["block"] = list(block_idx)
                pmax_data[col] = list(man["dcmax"])
        if pmax_data["block"]:
            self.write_dataframe(pd.DataFrame(pmax_data), gen_dir, "pmax")

        dem_dir = output_dir / "Demand"
        dem_dir.mkdir(parents=True, exist_ok=True)
        lmax_data: Dict[str, Any] = {"block": []}
        for entry in man_entries:
            man = entry["man_data"]
            if "dcmax" not in man:
                continue
            col = f"uid:{entry['number']}"
            block_idx = man["block_index"]
            if len(block_idx) > 0:
                lmax_data["block"] = list(block_idx)
                lmax_data[col] = list(man["dcmax"])
        if lmax_data["block"]:
            self.write_dataframe(pd.DataFrame(lmax_data), dem_dir, "lmax")

    def process(
        self,
        existing_gen: List[Dict[str, Any]],
        existing_dem: List[Dict[str, Any]],
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Produce all battery output arrays.

        For entries **without** DC-maintenance schedules the unified battery
        definition is used (``bus``, ``pmax_charge``, ``pmax_discharge``,
        ``gcost`` set on the battery itself).
        ``System::expand_batteries()`` will auto-generate the discharge
        Generator, charge Demand, and linking Converter at LP-construction
        time.

        Entries **with** DC-maintenance (``plpmaness.dat`` providing per-block
        ``DCMax`` overrides) fall back to the legacy multi-element approach
        because the Generator/pmax and Demand/lmax parquet files must
        reference the generator/demand UIDs that match the parquet columns.

        Args:
            existing_gen: Generator array from CentralWriter to append to.
            existing_dem: Demand array from DemandWriter to append to.
            output_dir: Base output directory (Demand/ sub-dir for parquet).

        Returns:
            Dict with battery_array, updated generator_array and
            demand_array, and optionally converter_array.
        """
        entries = self._all_entries()
        reg_reservoirs = self.get_regulation_reservoirs()
        if not entries and not reg_reservoirs:
            return {
                "battery_array": [],
                "generator_array": existing_gen,
                "demand_array": existing_dem,
            }

        # Write maintenance schedule parquet files if needed
        self._write_maintenance_parquet(entries, output_dir)

        # Split entries: DC-maintenance needs legacy multi-element approach
        dc_entries = [
            e
            for e in entries
            if e.get("man_data") is not None and "dcmax" in e.get("man_data", {})
        ]

        result: Dict[str, Any] = {
            "battery_array": self.to_battery_array(entries),
            "generator_array": existing_gen,
            "demand_array": existing_dem,
        }

        # Legacy fallback for DC-maintenance entries
        if dc_entries:
            result["converter_array"] = self.to_converter_array(dc_entries)
            result["generator_array"] = existing_gen + self.to_generator_array(
                dc_entries
            )
            result["demand_array"] = existing_dem + self.to_demand_array(dc_entries)

        # DCMod=1 ESS: charge bounded by the CenCarga plant output
        couplings = self.charge_coupling_constraints(
            entries, {str(g.get("name")) for g in existing_gen}
        )
        if couplings:
            result["charge_coupling_constraints"] = couplings

        # DCMod=2 regulation reservoirs (hydro elements, not batteries)
        if reg_reservoirs:
            result["regulation_reservoirs"] = reg_reservoirs

        return result
