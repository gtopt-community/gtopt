# -*- coding: utf-8 -*-

"""Writer for converting demand data to JSON format."""

import logging
from typing import Any, Dict, List, Optional, TypedDict, cast
from pathlib import Path
import pandas as pd

from gtopt_shared.demand_tiers import fail_segments, tier_name, tier_uid

from .base_writer import BaseWriter
from .demand_parser import DemandParser
from .block_parser import BlockParser
from .falla_segments import FallaTier

_logger = logging.getLogger(__name__)


class Demand(TypedDict, total=False):
    """Represents a demand in the system."""

    uid: int | str
    name: str
    bus: int | str
    lmax: str
    emin: float


class DemandWriter(BaseWriter):
    """Converts demand parser data to JSON format used by GTOPT."""

    def __init__(
        self,
        demand_parser: Optional[DemandParser] = None,
        block_parser: Optional[BlockParser] = None,
        options: Optional[Dict[str, Any]] = None,
        falla_tiers: Optional[Dict[int, List[FallaTier]]] = None,
        falla_uid_offset: int = 0,
    ):
        """Initialize with a DemandParser instance.

        *falla_tiers* (bus -> tiers, see
        :func:`plp2gtopt.falla_segments.build_falla_tiers`) enables the
        ``--falla-segments all`` split: every demand on such a bus is
        emitted as one ``<name>_falla<k>`` sibling per tier, with uid
        ``uid + (k - 1) * falla_uid_offset``.
        """
        super().__init__(demand_parser, options)
        self.block_parser = block_parser
        self.falla_tiers: Dict[int, List[FallaTier]] = falla_tiers or {}
        self.falla_uid_offset = falla_uid_offset
        #: uid -> falla central of every emitted tier demand.
        self.tier_centrals: Dict[Any, Dict[str, Any]] = {}

    def _tiers_of(self, demand: Dict[str, Any]) -> List[FallaTier]:
        """Return the falla tiers that split *demand* (empty = no split)."""
        bus = demand.get("bus")
        if not self.falla_tiers or not isinstance(bus, int):
            return []
        return self.falla_tiers.get(bus, [])

    def to_json_array(
        self, items: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Convert demand data to JSON array format."""
        if items is None:
            # ``self.items`` is None when the writer has no parser; match
            # the other writers and treat that as "no demands".
            items = self.items or []

        self.to_parquet(items=items)

        total_energy = 0.0
        json_demands: List[Demand] = []
        for demand in items:
            if demand.get("bus", -1) == 0:
                continue

            if len(demand["blocks"]) == 0 or len(demand["values"]) == 0:
                continue

            dem: Demand = {
                "uid": demand.get("bus", demand["number"]),
                "name": demand["name"],
                "bus": demand.get("bus", demand["name"]),
                "lmax": "lmax",
                **({"emin": demand["emin"]} if "emin" in demand else {}),
            }
            total_energy += demand["energy"]

            tiers = self._tiers_of(demand)
            if not tiers:
                json_demands.append(dem)
                continue
            for tier in tiers:
                uid = tier_uid(int(dem["uid"]), tier.k, self.falla_uid_offset)
                tdem = cast(Demand, dict(dem))
                tdem["uid"] = uid
                tdem["name"] = tier_name(demand["name"], tier.k)
                json_demands.append(tdem)
                self.tier_centrals[uid] = tier.central

        _logger.debug("Total Energy Demand [GWh]: %.2f", total_energy / 1000.0)
        return cast(List[Dict[str, Any]], json_demands)

    def to_dataframe(
        self, items: Optional[List[Dict[str, Any]]] = None
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Convert demand data to pandas DataFrame format.

        Returns:
            DataFrame with:
            - Index: Block numbers (merged from all demands)
            - Columns: Demand values for each bus (column name = bus name)
        """
        df, de = [pd.DataFrame(), pd.DataFrame()]

        if items is None:
            items = self.items
        if not items:
            return df, de

        management_factor = self.options.get("management_factor", 0.0)

        blocks = self.block_parser.items if self.block_parser else []
        last_stage = self._get_last_stage(blocks)

        series = []
        energies = []
        for demand in items:
            if demand.get("bus", -1) == 0:
                continue

            if len(demand["blocks"]) == 0 or len(demand["values"]) == 0:
                continue

            # Create Series for this demand
            uid = demand.get("bus", demand["name"])
            name = f"uid:{uid}" if not isinstance(uid, str) else uid

            s = pd.Series(data=demand["values"], index=demand["blocks"], name=name)
            s = s.iloc[~s.index.duplicated(keep="last")]
            #
            es = pd.Series(
                data=demand["energies"],
                index=demand["stages"],
                name=name,
            )
            te = es[:last_stage].sum()
            demand["energy"] = te
            if management_factor > 0.0:
                s = (1.0 - management_factor) * s
                e = management_factor * es
                demand["emin"] = "emin"
                if not self._tiers_of(demand):
                    energies.append(e)

            tiers = self._tiers_of(demand)
            if tiers:
                # --falla-segments all: one scaled column per tier.
                for tier in tiers:
                    tname = f"uid:{tier_uid(uid, tier.k, self.falla_uid_offset)}"
                    series.append((tier.fraction * s).rename(tname))
                    if management_factor > 0.0:
                        energies.append((tier.fraction * e).rename(tname))
                continue

            # Add to DataFrame
            series.append(s)

        if not series:
            # Every demand was skipped (bus 0 / no block data): nothing to
            # write — ``pd.concat([])`` would raise "No objects to concatenate".
            return df, de

        df = pd.concat(series, axis=1)
        if self.block_parser:
            stage_series = pd.Series(
                df.index.map(self.block_parser.get_stage_number).astype("int32"),
                index=df.index,
                name="stage",
            )
            df = pd.concat([df, stage_series], axis=1)
        # Convert index to block column
        df.index = df.index.astype("int32")
        df = df.copy()  # Defragment to avoid PerformanceWarning on reset_index
        df = df.reset_index().rename(columns={"index": "block"})

        # Fill missing values with column-specific defaults
        fill_values = {
            "stage": -1,  # Special value for missing stages
            **{
                col: 0.0 for col in df.columns if col not in ("stage", "block")
            },  # 0.0 for demands
        }
        df = df.fillna(fill_values)

        # prepare the energies dataframe
        if energies:
            de = pd.concat(energies, axis=1)
            de.index = de.index.astype("int32")
            de = de.reset_index().rename(columns={"index": "stage"})

        return df, de

    def to_parquet(self, items: Optional[List[Dict[str, Any]]] = None) -> None:
        """Write demand data to Parquet file format."""
        output_dir = (
            self.options["output_dir"] / "Demand"
            if "output_dir" in self.options
            else Path("Demand")
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        df, de = self.to_dataframe(items)

        if not df.empty:
            self.write_dataframe(df, output_dir, "lmax")

        if not de.empty:
            self.write_dataframe(de, output_dir, "emin")

    def to_fcost_dataframe(
        self,
        demand_array: List[Dict[str, Any]],
        falla_by_bus: Dict[int, Dict[str, Any]],
        cost_parser: Any,
        stage_parser: Any,
        central_parser: Any,
    ) -> tuple[pd.DataFrame, set[Any]]:
        """Build the fcost DataFrame from falla cost schedules.

        When multiple falla centrals sit on the same bus and have cost
        schedules, the **minimum** cost per stage across all of them is
        used.  The base gcost from plpcnfce.dat fills stages not covered
        by the schedule.  Tier demands emitted by ``--falla-segments all``
        (see :attr:`tier_centrals`) use the schedule of their own tier.

        Returns ``(df, filed_uids)`` where *df* has columns
        ``stage, uid:<N>, …`` and *filed_uids* is the set of demand uids
        that got a time-varying fcost (the bus number for untiered
        demands).
        """
        empty: tuple[pd.DataFrame, set[Any]] = (pd.DataFrame(), set())

        if not cost_parser or not stage_parser or not central_parser:
            return empty

        # Group all falla centrals by bus
        fallas_per_bus: Dict[int, List[Dict[str, Any]]] = {}
        for central in central_parser.centrals:
            if central.get("type") != "falla":
                continue
            bus = central.get("bus", 0)
            if bus <= 0 or bus not in falla_by_bus:
                continue
            fallas_per_bus.setdefault(bus, []).append(central)

        # Demand uid → falla centrals whose cost schedule it follows.
        # Untiered demands follow every falla of their bus (bus order of
        # the central list); tier demands follow their own tier only.
        bus_to_uid: Dict[int, Any] = {}
        tier_groups: Dict[Any, List[Dict[str, Any]]] = {}
        for dem in demand_array:
            uid = dem.get("uid", dem.get("bus"))
            if uid in self.tier_centrals:
                tier_groups[uid] = [self.tier_centrals[uid]]
            elif dem.get("bus") in fallas_per_bus:
                bus_to_uid[dem["bus"]] = uid
        groups: Dict[Any, List[Dict[str, Any]]] = {
            bus_to_uid[bus]: fallas
            for bus, fallas in fallas_per_bus.items()
            if bus in bus_to_uid
        }
        groups.update(tier_groups)

        if not groups:
            return empty

        stages = [s["number"] for s in stage_parser.items]

        series_list: List[pd.Series] = []
        filed_uids: set[Any] = set()

        for dem_uid, fallas in groups.items():
            # Collect cost schedules from all fallas of this demand
            bus_schedules: List[pd.Series] = []
            min_base_gcost = min(f.get("gcost", 0.0) for f in fallas)
            for falla in fallas:
                cost_entry = cost_parser.get_cost_by_name(falla.get("name", ""))
                if cost_entry is None:
                    # No schedule — use constant base gcost
                    bus_schedules.append(
                        pd.Series(
                            falla.get("gcost", 0.0),
                            index=stages,
                            dtype="float64",
                        )
                    )
                else:
                    s = pd.Series(
                        data=cost_entry["cost"],
                        index=cost_entry["stage"].tolist(),
                        dtype="float64",
                    )
                    s = s[~s.index.duplicated(keep="last")]
                    # Reindex to all stages, fill with base gcost
                    s = s.reindex(stages).fillna(falla.get("gcost", 0.0))
                    bus_schedules.append(s)

            # Element-wise minimum across all fallas of this demand
            min_series = bus_schedules[0]
            for s in bus_schedules[1:]:
                min_series = min_series.combine(s, min)

            # Skip writing if the schedule is constant and equals min base gcost
            if (min_series == min_base_gcost).all():
                continue

            min_series.name = f"uid:{dem_uid}"
            series_list.append(min_series)
            filed_uids.add(dem_uid)

        if not series_list:
            return empty

        df = pd.concat(series_list, axis=1)
        df = df.reset_index().rename(columns={"index": "stage"})
        df["stage"] = df["stage"].astype("int32")

        return df, filed_uids

    @staticmethod
    def _falla_cost_series(
        falla: Dict[str, Any], cost_parser: Any, stages: List[int]
    ) -> pd.Series:
        """Per-stage cost of one falla central (base gcost where no schedule)."""
        base = float(falla.get("gcost", 0.0))
        entry = (
            cost_parser.get_cost_by_name(falla.get("name", "")) if cost_parser else None
        )
        if entry is None:
            return pd.Series(base, index=stages, dtype="float64")
        s = pd.Series(
            data=entry["cost"], index=entry["stage"].tolist(), dtype="float64"
        )
        s = s[~s.index.duplicated(keep="last")]
        return s.reindex(stages).fillna(base)

    def apply_fail_segments(
        self,
        demand_array: List[Dict[str, Any]],
        tiers_by_bus: Dict[int, List[FallaTier]],
        cost_parser: Any,
        stage_parser: Any,
    ) -> None:
        """``--falla-segments all``: set the native ``fail_segments`` curve.

        Every demand on a multi-tier bus gets ``fail_segments`` =
        ``[{fraction_k, cost_k}, …, {cost_K}]`` (last tier open) and loses
        its single ``fcost``.  A tier cost is the falla's scalar ``gcost``
        or, when its ``plpcosce`` schedule varies, the per-stage file
        ``Demand/fail_cost_seg<k>`` (column ``uid:<demand uid>``).
        """
        stages = [s["number"] for s in stage_parser.items] if stage_parser else []
        per_k: Dict[int, List[pd.Series]] = {}
        for dem in demand_array:
            tiers = tiers_by_bus.get(dem.get("bus"))  # type: ignore[arg-type]
            if not tiers:
                continue
            costs: List[Any] = []
            for tier in tiers:
                base = float(tier.central.get("gcost", 0.0))
                cost: Any = base
                if stages:
                    series = self._falla_cost_series(tier.central, cost_parser, stages)
                    if not (series == base).all():
                        per_k.setdefault(tier.k, []).append(
                            series.rename(f"uid:{dem['uid']}")
                        )
                        cost = f"fail_cost_seg{tier.k}"
                costs.append(cost)
            dem.pop("fcost", None)
            dem["fail_segments"] = fail_segments([t.fraction for t in tiers], costs)
        if not per_k:
            return
        output_dir = self.options.get("output_dir", Path("results")) / "Demand"
        output_dir.mkdir(parents=True, exist_ok=True)
        for k, series_list in sorted(per_k.items()):
            df = pd.concat(series_list, axis=1)
            df = df.reset_index().rename(columns={"index": "stage"})
            df["stage"] = df["stage"].astype("int32")
            self.write_dataframe(df, output_dir, f"fail_cost_seg{k}")

    def write_fcost(
        self,
        demand_array: List[Dict[str, Any]],
        falla_by_bus: Dict[int, Dict[str, Any]],
        cost_parser: Any,
        stage_parser: Any,
        central_parser: Any,
    ) -> set[Any]:
        """Write Demand/fcost from falla cost schedules.

        Returns the set of demand uids that got a file-based fcost.
        """
        df, filed_uids = self.to_fcost_dataframe(
            demand_array, falla_by_bus, cost_parser, stage_parser, central_parser
        )

        if not df.empty:
            output_dir = self.options.get("output_dir", Path("results")) / "Demand"
            output_dir.mkdir(parents=True, exist_ok=True)
            self.write_dataframe(df, output_dir, "fcost")

        return filed_uids
