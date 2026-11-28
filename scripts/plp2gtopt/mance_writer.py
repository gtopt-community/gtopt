# -*- coding: utf-8 -*-

"""Writer for converting maintenance data to JSON format."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict, cast

import pandas as pd
from .base_writer import BaseWriter
from .mance_parser import ManceParser
from .central_parser import CentralParser
from .block_parser import BlockParser
from .mance_split import maintenance_centrals_from_xlsm, split_mances


class GeneratorMaintenance(TypedDict):
    """Represents generator maintenance data."""

    name: str
    block: List[int]
    pmin: List[float]
    pmax: List[float]


class ManceWriter(BaseWriter):
    """Converts maintenance parser data to JSON format used by GTOPT."""

    def __init__(
        self,
        mance_parser: Optional[ManceParser] = None,
        central_parser: Optional[CentralParser] = None,
        block_parser: Optional[BlockParser] = None,
        options: Optional[Dict[str, Any]] = None,
    ):
        """Initialize with a ManceParser instance.

        Args:
            mance_parser: Parser for maintenance data
            central_parser: Parser for central data
            block_parser: Parser for block data
            options: Dictionary of writer options
        """
        super().__init__(mance_parser, options)
        self.central_parser = central_parser
        self.block_parser = block_parser

    def to_json_array(
        self, items: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Convert maintenance data to JSON array format."""
        if items is None:
            items = self.items or []
        json_mances: List[GeneratorMaintenance] = [
            {
                "name": mance["name"],
                "block": mance["block"].tolist(),
                "pmin": mance["pmin"].tolist(),
                "pmax": mance["pmax"].tolist(),
            }
            for mance in items
        ]
        return cast(List[Dict[str, Any]], json_mances)

    def _create_dataframe_for_field(
        self, field: str, items: list[dict[str, Any]]
    ) -> pd.DataFrame:
        """Create a DataFrame for a specific maintenance field (pmin/pmax)."""
        df = self._create_dataframe(
            items=items,
            unit_parser=self.central_parser,
            index_parser=self.block_parser,
            value_field=field,
            index_field="block",
            fill_field=field,
            skip_types=("falla",),
        )

        return df

    def to_dataframe(
        self, items: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Convert maintenance data to pandas DataFrames for pmin and pmax."""
        if items is None:
            items = self.items or []

        if not items:
            return pd.DataFrame(), pd.DataFrame()

        try:
            df_pmin = self._create_dataframe_for_field("pmin", items).copy()
            df_pmax = self._create_dataframe_for_field("pmax", items).copy()
            if self.block_parser:
                df_pmin["stage"] = df_pmin.index.map(
                    self.block_parser.get_stage_number
                ).astype("int32")
                df_pmax["stage"] = df_pmax.index.map(
                    self.block_parser.get_stage_number
                ).astype("int32")

            return df_pmin, df_pmax
        except Exception as e:
            raise ValueError(f"Failed to create DataFrames: {str(e)}") from e

    def split_outages(
        self, items: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Apply ``--mance-split`` (see :mod:`plp2gtopt.mance_split`).

        Returns ``(pmin/pmax items, outage_fraction items)``; mode ``off``
        (default) returns the items unchanged and no outages.
        """
        if items is None:
            items = self.items or []
        mode = str(self.options.get("mance_split", "off") or "off")
        if mode == "off" or not items:
            return list(items), []
        if self.central_parser is None or self.block_parser is None:
            raise ValueError("mance-split needs the central and block parsers")
        maintained = None
        if mode == "xlsm":
            xlsm = self.options.get("mance_xlsm")
            if not xlsm:
                raise ValueError("--mance-split xlsm needs --mance-xlsm FILE")
            maintained = maintenance_centrals_from_xlsm(Path(xlsm))
        return split_mances(
            items,
            self.central_parser.items or [],
            self.block_parser.get_stage_number,
            mode,
            maintained,
        )

    def outage_dataframe(self, outages: List[Dict[str, Any]]) -> pd.DataFrame:
        """Per-block ``outage_fraction`` DataFrame (0 where not in outage)."""
        if not outages:
            return pd.DataFrame()
        df = self._create_dataframe(
            items=outages,
            unit_parser=self.central_parser,
            index_parser=self.block_parser,
            value_field="outage_fraction",
            index_field="block",
            skip_types=("falla",),
        )
        if df.empty:
            return df
        df = df.fillna(0.0).copy()
        if self.block_parser:
            df["stage"] = df.index.map(self.block_parser.get_stage_number).astype(
                "int32"
            )
        return df

    def _without_battery_centrals(
        self, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Drop the rows of battery centrals and their ``_LOAD`` phantoms.

        No generator carries their uid: ``BatteryWriter`` maps those rows
        to the battery's own ``pmax_discharge`` / ``pmax_charge``.
        """
        if self.central_parser is None:
            return items
        centrals = (
            getattr(self.central_parser, "items", None)
            or getattr(self.central_parser, "centrals", None)
            or []
        )
        bats = {
            str(c["name"])
            for c in centrals
            if str(c.get("type", "")).lower() == "bateria"
        }
        if not bats:
            return items

        def is_battery(name: str) -> bool:
            return name in bats or (
                name.endswith("_LOAD") and name[: -len("_LOAD")] in bats
            )

        return [it for it in items if not is_battery(str(it.get("name", "")))]

    def to_parquet(
        self, output_dir: Path, items: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, List[str]]:
        """Write maintenance data to Parquet/CSV files for pmin and pmax.

        With ``--mance-split auto|xlsm`` also writes ``outage_fraction``.
        Battery centrals are left out (see ``_without_battery_centrals``).
        """
        cols: Dict[str, List[str]] = {"pmin": [], "pmax": [], "outage_fraction": []}
        if items is None:
            items = self.items or []
        items, outages = self.split_outages(self._without_battery_centrals(items))
        df_out = self.outage_dataframe(outages)
        if not df_out.empty:
            output_dir.mkdir(parents=True, exist_ok=True)
            self.write_dataframe(df_out, output_dir, "outage_fraction")
            cols["outage_fraction"] = df_out.columns.tolist()
        df_pmin, df_pmax = self.to_dataframe(items)

        cols["pmin"] = df_pmin.columns.tolist() if not df_pmin.empty else []
        cols["pmax"] = df_pmax.columns.tolist() if not df_pmax.empty else []

        try:
            # Ensure output directory exists
            output_dir.mkdir(parents=True, exist_ok=True)
            if not df_pmin.empty:
                self.write_dataframe(df_pmin, output_dir, "pmin")
            if not df_pmax.empty:
                self.write_dataframe(df_pmax, output_dir, "pmax")
        except Exception as e:
            raise IOError(f"Failed to write Parquet files: {str(e)}") from e
        finally:
            # Explicitly clear and delete DataFrames
            if "df_pmin" in locals():
                del df_pmin
            if "df_pmax" in locals():
                del df_pmax

        return cols
