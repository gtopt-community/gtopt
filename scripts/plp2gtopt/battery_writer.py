# -*- coding: utf-8 -*-

"""Writer for converting battery data (plpcenbat.dat) to GTOPT JSON format.

Data sources and their roles
-----------------------------
* ``plpcenbat.dat`` (via ``battery_parser``) – **primary** source for battery
  configuration: name, bus, injection centrals (FPC), FPD, emin, emax.
* ``plpcnfce.dat`` (BAT section via ``central_parser``) – source for battery
  UID (central number) and pmax_discharge (central pmax); also used to look up
  injection central pmax for pmax_charge.
* ``plpmanbat.dat`` (via ``manbat_parser``) – per-block emin / emax
  overrides (maintenance schedules).
* ``plpess.dat`` (via ``ess_parser``) – ESS model; capacity = pmax × hrs_reg.
* ``plpmaness.dat`` (via ``maness_parser``) – per-block emin / emax and
  DC power (pmax_charge / pmax_discharge) overrides for ESS.

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
  ``DCMax`` is the block's discharge and charge limit: Battery
  ``pmax_discharge`` / ``pmax_charge`` schedules (``Battery/pmax_*``,
  shared with the plpmance.dat rows below; plpmaness.dat wins on a block
  listed in both).  ``DCMin`` > 0 has no gtopt counterpart and is warned.
  ``DCMod`` selects the charge coupling (see ``charge_coupling_constraints``).
  A DCMod=2 regulation tank is a hydro Reservoir: its Emin / Emax become
  ``Reservoir/ess_emin`` / ``ess_emax`` (``Reservoir/emin`` / ``emax``
  belong to plpmanem.dat).  Nothing is written to ``Generator/pmax`` or
  ``Demand/lmax``, which the central / demand maintenance writers own.

**plpmance.dat** (central maintenance) rows of the battery central
(``<battery>``) and of its charge phantom (``<battery>_LOAD``):
  PLP bases set battery entry dates there (PotMax = 0 / PotMin = 0 until
  the battery enters).  They map to the battery's own per-block
  ``pmax_discharge`` (PotMax of ``<battery>``) and ``pmax_charge``
  (−PotMin of ``<battery>_LOAD``) schedules — ``Battery/pmax_discharge``
  and ``Battery/pmax_charge`` — which ``System::expand_batteries()``
  forwards to the synthetic discharge generator / charge demand.  Blocks
  not listed keep the central's own plpcnfce.dat value, as in PLP.
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
from .mance_parser import ManceParser
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
        mance_parser: Optional[ManceParser] = None,
    ) -> None:
        """Initialize the BatteryWriter with optional parsers and configuration options."""
        super().__init__(None, options)
        self.battery_parser = battery_parser
        self.ess_parser = ess_parser
        self.central_parser = central_parser
        self.bus_parser = bus_parser
        self.stage_parser = stage_parser
        self.block_parser = block_parser
        self.mance_parser = mance_parser
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
            dem = f"{name}_dem"
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

        Each entry contains all fields needed by ``to_battery_array()``:

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

        Every battery is unified; maintenance schedules are file
        references (``emin`` / ``emax`` / ``pmax_*``) of the battery itself.
        """
        if entries is None:
            entries = self._all_entries()

        batteries = []
        for entry in entries:
            emax = entry["emax"]
            emin = entry["emin"]
            has_man = entry.get("man_energy", False)
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
            # Unified battery: System::expand_batteries() builds the
            # discharge generator / charge demand / converter.  Maintenance
            # (plpmance.dat, plpmaness.dat DCMax) → file-backed per-block
            # schedules of the battery itself.
            bat["bus"] = entry["bus"]
            bat["pmax_charge"] = (
                "pmax_charge" if entry.get("mance_charge") else entry["pmax_charge"]
            )
            bat["pmax_discharge"] = (
                "pmax_discharge"
                if entry.get("mance_discharge")
                else entry["pmax_discharge"]
            )
            # `discharge_cost` (was `gcost` before the `e54b795a4`
            # unified-battery rename) — per-MWh cost paid when
            # discharging.  Forwarded to the synthetic discharge
            # `Generator.gcost` by `System::expand_batteries()`.
            bat["discharge_cost"] = 0.0

            batteries.append(bat)
        return batteries

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

    def _mance_rows(self, central: str) -> Optional[Dict[int, tuple[float, float]]]:
        """``{block: (PotMin, PotMax)}`` of ``central`` in plpmance.dat."""
        if self.mance_parser is None:
            return None
        item = self.mance_parser.get_item_by_name(central)
        if not item or len(item.get("block", [])) == 0:
            return None
        return {
            int(b): (float(lo), float(hi))
            for b, lo, hi in zip(item["block"], item["pmin"], item["pmax"], strict=True)
        }

    def _blocks(self, extra: Optional[set[int]] = None) -> List[int]:
        """All PLP block numbers (plpblo.dat), else the listed ones."""
        if self.block_parser is not None and self.block_parser.items:
            return [int(it["number"]) for it in self.block_parser.items]
        return sorted(extra or set())

    def _write_block_schedules(
        self,
        out_dir: Path,
        blocks: List[int],
        series: Dict[str, Dict[str, List[float]]],
    ) -> None:
        """Write ``{stem: {uid:N column: per-block values}}`` under ``out_dir``."""
        import pandas as pd  # pylint: disable=import-outside-toplevel

        for stem, cols in series.items():
            if not cols or not blocks:
                continue
            data: Dict[str, Any] = {"block": blocks}
            if self.block_parser is not None and self.block_parser.items:
                data["stage"] = self.block_parser.get_stage_numbers(blocks)
            data.update(cols)
            out_dir.mkdir(parents=True, exist_ok=True)
            self.write_dataframe(pd.DataFrame(data), out_dir, stem)

    @staticmethod
    def _man_rows(man: Dict[str, Any], field: str) -> Dict[int, float]:
        """``{block: value}`` of one plpmanbat / plpmaness field."""
        return {
            int(b): float(v)
            for b, v in zip(man["block_index"], man[field], strict=True)
        }

    def _write_power_schedules(
        self, entries: List[Dict[str, Any]], output_dir: Path
    ) -> None:
        """Battery power schedules from plpmance.dat and plpmaness.dat.

        PLP keeps a battery as two centrals: ``<battery>`` (discharge,
        PotMax) and ``<battery>_LOAD`` (charge, a negative PotMin).  Their
        plpmance.dat rows (battery entry dates, derates) become per-block
        ``Battery/pmax_discharge`` (PotMax of ``<battery>``) and
        ``Battery/pmax_charge`` (−PotMin of ``<battery>_LOAD``) schedules.
        A plpmaness.dat row (ESS maintenance) sets ``DCMax`` as both the
        discharge and the charge limit of its block and wins over a
        plpmance.dat row of the same block.  Unlisted blocks keep the
        nominal power (PLP: a maintenance file overrides only the listed
        blocks); without a ``<battery>_LOAD`` central the nominal
        ``pmax_charge`` fills the charge schedule.

        Nothing goes to ``Generator/pmax`` or ``Demand/lmax``: those
        files belong to the central / demand maintenance writers.

        Sets ``entry["mance_discharge"]`` / ``entry["mance_charge"]`` so
        ``to_battery_array`` references the files.
        """
        centrals = self._all_centrals_by_name()
        series: Dict[str, Dict[str, List[float]]] = {
            "pmax_discharge": {},
            "pmax_charge": {},
        }
        pending = []
        listed: set[int] = set()
        for entry in entries:
            name = entry["name"]
            load_name = f"{name}_LOAD"
            dis = self._mance_rows(name)
            chg = self._mance_rows(load_name)
            man = entry.get("man_data")
            dc = self._man_rows(man, "dcmax") if man and "dcmax" in man else None
            if man and "dcmin" in man and any(float(v) > 0 for v in man["dcmin"]):
                _logger.warning(
                    "Battery '%s': plpmaness.dat DCMin > 0 ignored (gtopt"
                    " batteries have no minimum charge / discharge)",
                    name,
                )
            if dis is None and chg is None and dc is None:
                continue
            listed |= set(dis or {}) | set(chg or {}) | set(dc or {})
            pending.append((entry, dis, chg, dc))
        blocks = self._blocks(listed)
        for entry, dis, chg, dc in pending:
            name = entry["name"]
            col = self.pcol_name(name, entry["number"])
            dc = dc or {}
            fill_d = float(centrals.get(name, {}).get("pmax", entry["pmax_discharge"]))
            if not fill_d:
                fill_d = float(entry["pmax_discharge"])
            load = centrals.get(f"{name}_LOAD")
            fill_c = (
                max(0.0, -float(load.get("pmin", 0.0)))
                if load is not None
                else float(entry["pmax_charge"])
            )
            if dis is not None or dc:
                rows_d = {b: max(0.0, v[1]) for b, v in (dis or {}).items()}
                rows_d.update({b: max(0.0, v) for b, v in dc.items()})
                series["pmax_discharge"][col] = [rows_d.get(b, fill_d) for b in blocks]
                entry["mance_discharge"] = True
            if chg is not None or dc:
                rows_c = {b: max(0.0, -v[0]) for b, v in (chg or {}).items()}
                rows_c.update({b: max(0.0, v) for b, v in dc.items()})
                series["pmax_charge"][col] = [rows_c.get(b, fill_c) for b in blocks]
                entry["mance_charge"] = True
        self._write_block_schedules(output_dir / "Battery", blocks, series)

    def _write_maintenance_parquet(
        self, entries: List[Dict[str, Any]], output_dir: Path
    ) -> None:
        """Battery energy bounds from plpmanbat.dat / plpmaness.dat.

        ``Emin`` / ``Emax`` of the listed blocks → ``Battery/emin`` and
        ``Battery/emax`` (one column per battery, every block; unlisted
        blocks keep the battery's own ``emin`` / ``emax``).  Sets
        ``entry["man_energy"]`` so ``to_battery_array`` references them.
        The DC power bounds of plpmaness.dat are written by
        :meth:`_write_power_schedules`.
        """
        man_entries = [
            e
            for e in entries
            if e.get("has_maintenance")
            and e.get("man_data") is not None
            and len(e["man_data"]["block_index"]) > 0
            and e["emax"] > 0
        ]
        if not man_entries:
            return
        listed = {int(b) for e in man_entries for b in e["man_data"]["block_index"]}
        blocks = self._blocks(listed)
        series: Dict[str, Dict[str, List[float]]] = {"emin": {}, "emax": {}}
        for entry in man_entries:
            man = entry["man_data"]
            col = self.pcol_name(entry["name"], entry["number"])
            for field in ("emin", "emax"):
                rows = self._man_rows(man, field)
                fill = float(entry[field])
                series[field][col] = [rows.get(b, fill) for b in blocks]
            entry["man_energy"] = True
        self._write_block_schedules(output_dir / "Battery", blocks, series)

    def _write_regulation_maintenance(
        self, reservoirs: List[Dict[str, Any]], output_dir: Path
    ) -> None:
        """plpmaness.dat rows of a DCMod=2 regulation tank → its Reservoir.

        ``Emin`` / ``Emax`` become ``Reservoir/ess_emin`` / ``ess_emax``
        (own stems: ``Reservoir/emin`` / ``emax`` belong to the
        plpmanem.dat reservoir maintenance).  The tank's power is the
        paired turbine's, so ``DCMin`` / ``DCMax`` are not mapped (warned
        when they differ from the plpess.dat values).
        """
        man_parser = self._man_parser
        if man_parser is None or not reservoirs:
            return
        hits = []
        listed: set[int] = set()
        for rsv in reservoirs:
            man = man_parser.get_item_by_name(rsv["name"])
            if man is None or len(man.get("block_index", [])) == 0:
                continue
            if "dcmax" in man:
                _logger.warning(
                    "Regulation tank '%s' (DCMod=2): plpmaness.dat DCMin /"
                    " DCMax not mapped (the paired turbine limits its power)",
                    rsv["name"],
                )
            listed |= {int(b) for b in man["block_index"]}
            hits.append((rsv, man))
        if not hits:
            return
        blocks = self._blocks(listed)
        series: Dict[str, Dict[str, List[float]]] = {"ess_emin": {}, "ess_emax": {}}
        for rsv, man in hits:
            col = self.pcol_name(rsv["name"], rsv["uid"])
            for field in ("emin", "emax"):
                rows = self._man_rows(man, field)
                fill = float(rsv[field])
                series[f"ess_{field}"][col] = [rows.get(b, fill) for b in blocks]
            rsv["emin"] = "ess_emin"
            rsv["emax"] = "ess_emax"
        self._write_block_schedules(output_dir / "Reservoir", blocks, series)

    def process(
        self,
        existing_gen: List[Dict[str, Any]],
        existing_dem: List[Dict[str, Any]],
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Produce all battery output arrays.

        Every battery uses the unified definition (``bus``,
        ``pmax_charge``, ``pmax_discharge``, ``discharge_cost`` set on the
        battery itself).
        ``System::expand_batteries()`` will auto-generate the discharge
        Generator, charge Demand, and linking Converter at LP-construction
        time.

        Maintenance files (plpmanbat / plpmaness / plpmance rows of the
        battery centrals) become the battery's own per-block schedules.

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

        # Maintenance → the battery's own schedules (Battery/emin, emax,
        # pmax_discharge, pmax_charge) and the regulation tank's reservoir
        self._write_maintenance_parquet(entries, output_dir)
        self._write_power_schedules(entries, output_dir)
        self._write_regulation_maintenance(reg_reservoirs, output_dir)

        result: Dict[str, Any] = {
            "battery_array": self.to_battery_array(entries),
            "generator_array": existing_gen,
            "demand_array": existing_dem,
        }

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
