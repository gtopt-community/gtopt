# -*- coding: utf-8 -*-

"""Writer for converting line data to JSON format."""

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, cast
from .base_writer import BaseWriter
from .line_parser import LineParser
from .manli_parser import ManliParser
from .manli_writer import ManliWriter
from .block_parser import BlockParser


#: Default ``--loss-tangents`` policy: one tangent per PLP loss tramo per
#: flow direction (``K = 2 * Num.Tramos``; Num.Tramos counts tramos per
#: direction, ``leecnfli.f``).
DEFAULT_LOSS_TANGENTS = "double"

#: Symbolic ``--loss-tangents`` policies (an integer K >= 2 is also valid).
LOSS_TANGENTS_POLICIES = ("tramos", "double")


def parse_loss_tangents(value: str) -> str | int:
    """Validate a ``--loss-tangents`` value (argparse ``type=`` hook).

    Accepts ``tramos``, ``double`` or an integer K >= 2.
    """
    text = str(value).strip().lower()
    if text in LOSS_TANGENTS_POLICIES:
        return text
    try:
        k = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --loss-tangents {value!r}: expected one of "
            f"{', '.join(LOSS_TANGENTS_POLICIES)} or an integer >= 2"
        ) from exc
    if k < 2:
        raise argparse.ArgumentTypeError(
            f"invalid --loss-tangents {value!r}: integer K must be >= 2"
        )
    return k


def loss_segments_for(
    losses_mode: Optional[str], num_sections: int, loss_tangents: str | int
) -> int:
    """Return the per-line ``loss_segments`` for a PLP line with losses.

    PLP's Num.Tramos (``LinNFlu``, ``leecnfli.f``) is the number of
    equal-width flow tramos PER DIRECTION: ``genpdlin.f`` creates
    ``2 * LinNFlu`` flow columns (``fp_k`` / ``fn_k``) per line-block whose
    bus-balance coefficient carries the secant loss slope
    ``R * (2k - 1) * dF / V^2`` (``dF = tmax / LinNFlu``).

    ``tangent_signed_flow`` places its K tangents over the SIGNED range
    ``[-fmax, +fmax]``.  The tangent count follows ``loss_tangents``:

    * ``"tramos"``: ``K = Num.Tramos`` — the smallest LP (half PLP's
      tramo count over the signed range).  Max loss under-estimate
      ``R * fmax^2 / (V^2 * K^2)``.
    * ``"double"`` (default): ``K = 2 * Num.Tramos`` — Num.Tramos
      tangents per flow direction, one per PLP tramo, the same worst-case error as PLP's secants
      (``R * fmax^2 / (4 * V^2 * Num.Tramos^2)``) at twice the rows.
    * an integer ``K``: that tangent count on every piecewise lossy
      line (Num.Tramos >= 2).

    Every other mode (``piecewise_direct`` PLP-replication runs,
    ``piecewise``, ``bidirectional``, ``adaptive`` ...) keeps PLP's
    per-direction count verbatim.
    """
    if losses_mode != "tangent_signed_flow":
        return num_sections
    if isinstance(loss_tangents, int):
        return loss_tangents
    if loss_tangents == "double":
        return 2 * num_sections
    return num_sections


class Line(TypedDict, total=False):
    """Represents a line in the system."""

    uid: int
    name: str
    active: int | str
    bus_a: int
    bus_b: int
    resistance: float | str
    reactance: float | str
    tmax_ab: float | str
    tmax_ba: float | str
    voltage: float | str
    loss_segments: int
    use_line_losses: bool
    loss_allocation_mode: str
    loss_envelope: float


class LineWriter(BaseWriter):
    """Converts line parser data to JSON format used by GTOPT."""

    def __init__(
        self,
        line_parser: Optional[LineParser] = None,
        block_parser: Optional[BlockParser] = None,
        manli_parser: Optional[ManliParser] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize with a LineParser instance."""
        super().__init__(line_parser, options)
        self.manli_parser = manli_parser
        self.line_parser = line_parser
        self.block_parser = block_parser

    def to_json_array(
        self, items: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Convert line data to JSON array format."""
        if items is None:
            items = self.items

        if not items:
            return []

        pcols = self._write_parquet_files()

        # Effective global loss mode: LineWriter receives the full CLI
        # options dict (``main.build_options`` output, threaded through
        # ``GTOptWriter.process_lines``), so the mode set by
        # ``--line-losses-mode`` lives under ``model_options``.
        losses_mode = (
            (self.options or {}).get("model_options", {}).get("line_losses_mode")
        )

        loss_tangents = (self.options or {}).get("loss_tangents", DEFAULT_LOSS_TANGENTS)
        # PLP global FPerdTram (plpcnfli.dat header); mock parsers in
        # tests may not carry it -> PLP default (losses on).
        fperdtram = bool(getattr(self.line_parser, "loss_modeling_enabled", True))

        json_lines: List[Line] = []
        for line in items:
            line_name = line["name"]
            line_number = line["number"]

            bus_number_a = line.get("bus_a", -1)
            bus_number_b = line.get("bus_b", -1)
            if bus_number_a <= 0 or bus_number_b <= 0 or bus_number_a == bus_number_b:
                continue

            # lookup for cols in parquet files
            pcol_name = self.pcol_name(line_name, line_number)
            tmax_ab = "tmax_ab" if pcol_name in pcols["tmax_ab"] else line["tmax_ab"]
            tmax_ba = "tmax_ba" if pcol_name in pcols["tmax_ba"] else line["tmax_ba"]
            active = "active" if pcol_name in pcols["active"] else line["operational"]

            # Loss modeling: num_sections (Num.Tramos) and mod_perdidas flag
            num_sections = line.get("num_sections", 1)
            mod_perdidas = line.get("mod_perdidas", False)

            json_line: Line = {
                "uid": line_number,
                "name": line_name,
                "active": active,
                "bus_a": bus_number_a,
                "bus_b": bus_number_b,
                "resistance": (
                    "resistance" if pcol_name in pcols["resistance"] else line["r"]
                ),
                "tmax_ab": tmax_ab,
                "tmax_ba": tmax_ba,
                "voltage": (
                    "voltage" if pcol_name in pcols["voltage"] else line["voltage"]
                ),
            }

            # Only include reactance for AC lines.  DC/HVDC lines
            # (PLP X=0 or hvdc=True) omit reactance so gtopt skips
            # the Kirchhoff voltage-law constraint for them.  Never
            # write reactance=0: gtopt reads an explicit 0 as a
            # zero-impedance tie (equal bus angles), not as DC.
            is_hvdc = line.get("hvdc", False)
            reactance = line["x"]
            if pcol_name in pcols["reactance"] and not is_hvdc:
                json_line["reactance"] = "reactance"
            elif reactance != 0.0 and not is_hvdc:
                json_line["reactance"] = reactance

            # PLP losses are ON for a line only when the global FPerdTram
            # flag AND the per-line Mod.Perd. flag are both true
            # (``leecnfli.f:213-215``: otherwise LinNFlu := 1, and
            # ``genpdlin.f:105`` stamps no loss factor).  Mirror that explicitly: a PLP
            # lossless line is emitted with ``use_line_losses = false``
            # (gtopt's global default is losses ON, so leaving the key
            # out would give an R > 0 line losses PLP never models).
            plp_lossy = bool(mod_perdidas) and fperdtram
            if plp_lossy:
                json_line["use_line_losses"] = True
                # Only include loss_segments when > 1 (piecewise-linear
                # model); Num.Tramos = 1 keeps gtopt's linear fallback,
                # which equals PLP's single-tramo secant R·tmax/V²·|f|.
                if num_sections > 1:
                    json_line["loss_segments"] = loss_segments_for(
                        losses_mode, num_sections, loss_tangents
                    )
            else:
                json_line["use_line_losses"] = False

            # Loss allocation mode from PLP global FPerdLin setting.
            # Only emit if different from gtopt default ("receiver").
            if self.line_parser is not None:
                loss_mode = self.line_parser.loss_allocation_mode
                if loss_mode != "receiver":
                    json_line["loss_allocation_mode"] = loss_mode

            # --lift-line-caps: widen loss_envelope on the named lines.
            # Only stamps a numeric envelope when tmax_ab is a numeric
            # literal — when tmax_ab is a parquet column reference the
            # per-block envelope would need parquet rewiring, deferred.
            lift = self.options.get("lift_line_caps") if self.options else None
            if lift and line_name in lift and isinstance(line["tmax_ab"], (int, float)):
                json_line["loss_envelope"] = float(line["tmax_ab"]) * lift[line_name]

            json_lines.append(json_line)

        return cast(List[Dict[str, Any]], json_lines)

    def _write_parquet_files(self) -> Dict[str, List[str]]:
        """Write line data to Parquet file format."""
        cols: Dict[str, List[str]] = {
            "tmax_ab": [],
            "tmax_ba": [],
            "active": [],
            "voltage": [],
            "resistance": [],
            "reactance": [],
        }

        if not self.manli_parser:
            return cols

        output_dir = (
            self.options["output_dir"] / "Line"
            if "output_dir" in self.options
            else Path("Line")
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        manli_writer = ManliWriter(
            self.manli_parser, self.line_parser, self.block_parser, self.options
        )
        cols.update(manli_writer.to_parquet(output_dir))

        return cols
