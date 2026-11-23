# -*- coding: utf-8 -*-

"""Writer for converting central data to JSON format."""

from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, cast
import logging
import math
import typing

import pandas as pd

from .base_writer import BaseWriter
from .central_parser import CentralParser
from .bus_parser import BusParser
from .aflce_parser import AflceParser
from .aflce_writer import AflceWriter
from .block_parser import BlockParser

_logger = logging.getLogger(__name__)


def profile_factor(central: Dict[str, Any]) -> float:
    """Return the factor mapping a PLP pasada ``aflce`` value to per-unit.

    PLP models a pasada central as a flow column ``q`` bounded by
    ``q <= min(aflce, PMax / Rendi)`` and produces ``Rendi * q`` MW
    (``plp-fijamues.f``).  Its available generation is therefore
    ``Rendi * aflce`` MW, while a gtopt ``GeneratorProfile`` is a
    per-unit factor of the generator ``capacity`` (= PLP ``PotMax``):
    ``profile = Rendi * aflce / PotMax``.  A non-positive ``PotMax``
    returns 0 (the gtopt generator has no capacity to scale anyway).
    """
    try:
        capacity = float(central.get("pmax", 0.0) or 0.0)
        efficiency = float(central.get("efficiency", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(capacity) or capacity <= 0.0:
        return 0.0
    return efficiency / capacity


def to_per_unit(value: float, central: Dict[str, Any]) -> float:
    """Scalar ``aflce`` (MW-equivalent) → per-unit profile clipped to [0, 1]."""
    return min(max(float(value) * profile_factor(central), 0.0), 1.0)


class CapacityProfile(TypedDict):
    """Represents a kind-tagged capacity-factor profile.

    Wire format matches the C++ `CapacityProfile` struct introduced by
    the legacy →  unified migration:
        {"uid": ..., "name": ..., "owner_kind": "generator"|"demand",
         "owner": <uid-or-name>, "profile": ...}
    """

    uid: int
    name: str
    owner_kind: str
    owner: int | str
    profile: str | float


class GeneratorProfileWriter(BaseWriter):
    """Converts central parser data to JSON format used by GTOPT."""

    def __init__(
        self,
        central_parser: Optional[CentralParser] = None,
        block_parser: Optional[BlockParser] = None,
        bus_parser: Optional[BusParser] = None,
        aflce_parser: Optional[AflceParser] = None,
        scenarios: Optional[List[Dict[str, Any]]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize with a CentralParser instance."""
        super().__init__(central_parser, options)
        self.block_parser = block_parser
        self.bus_parser = bus_parser
        self.aflce_parser = aflce_parser
        self.scenarios = scenarios

    @property
    def central_parser(self) -> CentralParser:
        """Get the central parser instance."""
        return typing.cast(CentralParser, self.parser)

    def to_json_array(
        self, items: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Convert pasada centrals classified for profile mode to JSON.

        Only pasada centrals whose names appear in
        ``options["_pasada_profile_names"]`` are included.  When the set
        is empty (e.g. all pasada are hydro), returns ``[]``.

        For legacy ``--pasada-mode profile`` (all pasada), the set is
        populated by ``classify_pasada_centrals`` to include all.
        """
        profile_names: set[str] | None = None
        if self.options:
            profile_names = self.options.get("_pasada_profile_names")

        # When _pasada_profile_names is explicitly empty (set()), no profiles.
        # When absent (None), include all pasada (backward compat / unit tests).
        if profile_names is not None and not profile_names:
            return []

        if items is None:
            items = (
                self.central_parser.centrals_of_type.get("pasada", [])
                if self.central_parser
                else []
            )
        if not items:
            return []

        # Write own parquet files for profile centrals (not using Flow@discharge)
        if profile_names is not None:
            self._write_profile_parquet(profile_names)

        json_profiles: List[CapacityProfile] = []
        for central in items:
            central_name = central["name"]
            # When profile_names is set, only include listed centrals
            if profile_names is not None and central_name not in profile_names:
                continue

            # Skip centrals without a bus or with bus 0
            bus_number = central.get("bus", -1)
            if bus_number <= 0:
                continue

            if self.bus_parser:
                bus = self.bus_parser.get_bus_by_number(bus_number)
                if bus is None or bus.get("number", -1) <= 0:
                    continue

            central_number = central["number"]

            aflce = (
                self.aflce_parser.get_item_by_name(central_name)
                if self.aflce_parser
                else None
            )
            afluent: str | float
            if aflce is None:
                # Scalar PLP ``afluent`` is MW-equivalent: normalise it to a
                # per-unit factor of the generator capacity.
                afluent = to_per_unit(central.get("afluent", 0.0), central)
            elif profile_names is not None:
                # Auto/explicit profile mode: own GeneratorProfile parquet
                afluent = "profile"
            else:
                # Legacy/backward compat: reference Flow@discharge
                afluent = "Flow@discharge"

            if isinstance(afluent, float) and afluent <= 0.0:
                continue

            profile: CapacityProfile = {
                "uid": central_number,
                "name": central_name,
                "owner_kind": "generator",
                "owner": central_name,
                "profile": afluent,
            }
            json_profiles.append(profile)

        return cast(List[Dict[str, Any]], json_profiles)

    def _write_profile_parquet(self, profile_names: set[str]) -> None:
        """Write profile Parquet files for solar/wind pasada centrals.

        Writes to ``GeneratorProfile/profile.parquet`` the per-unit
        capacity factor of each profile central: the PLP ``aflce`` value
        (MW-equivalent) times ``Rendi`` divided by ``PotMax`` (the gtopt
        ``Generator.capacity``), clipped to [0, 1] — see
        :func:`profile_factor`.  Values above ``PotMax`` are clipped (PLP
        caps them at ``PMax`` too); every clip is counted in one warning.
        """
        if not self.aflce_parser or not profile_names:
            return

        output_dir = (
            self.options["output_dir"] / "GeneratorProfile"
            if self.options and "output_dir" in self.options
            else Path("GeneratorProfile")
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        # Filter aflce items to only include profile centrals
        profile_items = [
            f for f in self.aflce_parser.flows if f.get("name") in profile_names
        ]

        if not profile_items:
            return

        aflce_writer = AflceWriter(
            self.aflce_parser,
            self.central_parser,
            self.block_parser,
            self.scenarios,
            self.options,
        )
        # Write as profile.parquet (not discharge.parquet) to match the
        # C++ GeneratorProfile.profile field name.
        df = aflce_writer.to_dataframe(items=profile_items)
        if isinstance(df, pd.DataFrame) and not df.empty:
            self._normalize_profile_columns(df, profile_names)
            aflce_writer.write_dataframe(df, output_dir, "profile")

    def _normalize_profile_columns(
        self, df: pd.DataFrame, profile_names: set[str]
    ) -> None:
        """Convert the MW-equivalent aflce columns of *df* to per-unit."""
        centrals = (
            self.central_parser.centrals_of_type.get("pasada", [])
            if self.central_parser
            else []
        )
        n_clipped = 0
        n_zero_cap: list[str] = []
        max_raw = 0.0
        for central in centrals:
            name = central["name"]
            if name not in profile_names:
                continue
            col = self.pcol_name(name, int(central["number"]))
            if col not in df.columns:
                continue
            factor = profile_factor(central)
            if factor <= 0.0:
                n_zero_cap.append(name)
            scaled = df[col].astype("float64") * factor
            over = scaled > 1.0 + 1e-9
            if over.any():
                n_clipped += int(over.sum())
                max_raw = max(max_raw, float(scaled.max()))
            df[col] = scaled.clip(lower=0.0, upper=1.0)
        if n_clipped:
            _logger.warning(
                "GeneratorProfile: clipped %d per-unit profile value(s) > 1 "
                "(max %.3f): PLP aflce * Rendi exceeds PotMax",
                n_clipped,
                max_raw,
            )
        if n_zero_cap:
            _logger.warning(
                "GeneratorProfile: %d profile central(s) with PotMax <= 0 "
                "get a zero profile: %s",
                len(n_zero_cap),
                ", ".join(sorted(n_zero_cap)[:10]),
            )
