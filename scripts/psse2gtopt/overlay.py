"""Readers for CSV overlays that add economic data to a PSS/E case.

A PSS/E ``.raw`` holds no generator costs and often no branch ratings.
When a companion data set provides them (e.g. a production-cost model of
the same network), they can be written to two small CSV files and passed
to ``psse2gtopt``:

* ``--gen-costs`` — one row per generator, keyed by PSS/E bus number and
  machine ID::

      bus,id,pmax,mw1,cost1,mw2,cost2,mw3,cost3,mw4,cost4
      1032,G,,352.94,54.64,705.88,56.81,1050,71.27

  ``pmax`` (optional, blank = keep the RAW ``PT``) overrides the unit's
  maximum output.  ``mwK``/``costK`` describe a piecewise-linear marginal
  cost curve: segment *K* runs from the previous breakpoint (0 for the
  first) to the cumulative breakpoint ``mwK`` [MW] at ``costK`` [$/MWh].
  Any number of ``mwK``/``costK`` pairs may be given; blank pairs are
  ignored.  The last segment is stretched or clipped to the unit's
  ``pmax``.

* ``--line-limits`` — one row per branch or two-winding transformer,
  keyed by the PSS/E ``from``/``to`` bus numbers and circuit ID
  (either orientation)::

      from,to,ckt,tmax
      1001,1201,1,2000

  ``tmax`` [MW] replaces the RAW rating; a blank or non-positive value
  means unlimited.

Both overlays are optional and plain CSV (no spreadsheet dependency).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass
class GenCost:
    """Cost overlay of one generator.

    Attributes:
        pmax: Maximum output override [MW], or ``None`` to keep ``PT``.
        segments: ``(mw_to, cost)`` cumulative breakpoints [MW] and
            marginal costs [$/MWh], in increasing ``mw_to`` order.
    """

    pmax: float | None = None
    segments: list[tuple[float, float]] = field(default_factory=list)


GenKey = tuple[int, str]
LineKey = tuple[int, int, str]


def _num(text: str | None) -> float | None:
    """Parse an optional CSV number (blank → ``None``)."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    return float(text)


def _norm_id(text: str) -> str:
    """Normalise a PSS/E machine / circuit ID (strip quotes and blanks)."""
    return text.strip().strip("'").strip()


def parse_gen_costs(path: str | Path) -> dict[GenKey, GenCost]:
    """Read a ``--gen-costs`` CSV into ``{(bus, id): GenCost}``.

    Raises:
        ValueError: On a missing ``bus``/``id`` column, a duplicate key or
            decreasing breakpoints.
    """
    path = Path(path)
    out: dict[GenKey, GenCost] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        if "bus" not in cols or "id" not in cols:
            raise ValueError(f"{path}: --gen-costs needs 'bus' and 'id' columns")
        ks = sorted(int(m.group(1)) for c in cols if (m := re.fullmatch(r"mw(\d+)", c)))
        for row in reader:
            key = (int(float(row["bus"])), _norm_id(row["id"]))
            if key in out:
                raise ValueError(f"{path}: duplicate generator {key}")
            segs: list[tuple[float, float]] = []
            for k in ks:
                mw = _num(row.get(f"mw{k}"))
                cost = _num(row.get(f"cost{k}"))
                if mw is None or cost is None:
                    continue
                if segs and mw < segs[-1][0]:
                    raise ValueError(
                        f"{path}: generator {key} breakpoints must increase"
                    )
                segs.append((mw, cost))
            out[key] = GenCost(pmax=_num(row.get("pmax")), segments=segs)
    logger.info("gen-costs overlay %s: %d generators", path.name, len(out))
    return out


def parse_line_limits(path: str | Path) -> dict[LineKey, float]:
    """Read a ``--line-limits`` CSV into ``{(from, to, ckt): tmax}``.

    Both orientations are stored so a lookup does not depend on the
    branch direction.  A blank or non-positive ``tmax`` is stored as 0
    (unlimited).
    """
    path = Path(path)
    out: dict[LineKey, float] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        for col in ("from", "to", "ckt", "tmax"):
            if col not in cols:
                raise ValueError(f"{path}: --line-limits needs a '{col}' column")
        n = 0
        for row in reader:
            a, b = int(float(row["from"])), int(float(row["to"]))
            ckt = _norm_id(row["ckt"])
            tmax = _num(row["tmax"]) or 0.0
            out[(a, b, ckt)] = max(tmax, 0.0)
            out[(b, a, ckt)] = max(tmax, 0.0)
            n += 1
    logger.info("line-limits overlay %s: %d branches", path.name, n)
    return out


def split_segments(
    segments: list[tuple[float, float]], pmax: float
) -> list[tuple[float, float]]:
    """Turn cumulative breakpoints into ``(width, cost)`` blocks up to ``pmax``.

    Blocks beyond ``pmax`` are dropped, the last kept block is stretched
    to end at ``pmax``, and zero-width blocks are skipped.
    """
    blocks: list[tuple[float, float]] = []
    lo = 0.0
    for mw_to, cost in segments:
        hi = min(mw_to, pmax)
        if hi > lo + 1e-9:
            blocks.append((hi - lo, cost))
            lo = hi
        if lo >= pmax - 1e-9:
            break
    if not blocks:
        return [(pmax, segments[-1][1])] if segments and pmax > 0 else []
    if lo < pmax - 1e-9:
        width, cost = blocks[-1]
        blocks[-1] = (width + (pmax - lo), cost)
    return blocks
