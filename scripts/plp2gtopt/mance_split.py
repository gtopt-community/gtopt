# -*- coding: utf-8 -*-

"""Split ``plpmance.dat`` maintenance derates into ``outage_fraction``.

PLP writes a maintenance of *d* days in a month of *D* days as a derate
``Pmax = Pnom·(1 − d/D)`` on every block of that stage, mixed in the same
field with unit entries / retirements, fuel limits and resource profiles.
``--mance-split`` separates the maintenance part so gtopt sees it as a
``Generator.outage_fraction`` schedule (``f = 1 − Pmax/Pnom``) while
``pmax`` / ``pmin`` return to the nominal unit; the reserve treatment then
follows ``model_options.outage_reserve_mode``.

Modes:

* ``off`` (default): no split, ``plpmance`` stays in ``pmax`` / ``pmin``.
* ``auto``: heuristic on ``plpmance`` + ``plpcnfce`` — a stage is a
  maintenance stage of a *thermal* central when its ``Pmax`` is the same
  on all of the stage's blocks and below the nominal ``Pmax``, except the
  leading zero run (entry), the trailing zero run (retirement) and a
  central that is zero throughout (blocked fuel alternative, candidate).
  Hydro, battery and failure centrals are never split (their ``plpmance``
  carries flow envelopes / profiles).
* ``xlsm``: the same stage rule, but the set of maintained centrals is
  read from the PLP input workbook (the maintenance-events sheet,
  ``Mant.Mayor…``, or the ``MantCEN`` assembler sheet), so a central that
  is only fuel-limited or profiled (``Disp.Combust.``, ``ERNC`` sheets) is
  never split, whatever its type.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

MANCE_SPLIT_MODES = ("off", "auto", "xlsm")

#: PLP central types whose ``plpmance`` derates may be maintenance in
#: ``auto`` mode.
_AUTO_TYPES = frozenset({"termica"})

#: Relative tolerance for "uniform over the stage" / "below nominal".
_REL_TOL = 1e-6


def _norm(text: Any) -> str:
    """Upper-case, accent-free, stripped text (for header matching)."""
    raw = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in raw if not unicodedata.combining(c)).strip().upper()


def maintenance_centrals_from_xlsm(path: Path) -> set[str]:
    """Return the central names listed in the workbook's maintenance sheet.

    Looks for a sheet named ``Mant.Mayor…`` (maintenance events), else
    ``MantCEN``; the header row is the first row holding a ``CENTRAL`` (or
    ``NOMBRE``) cell, and every non-empty cell below it in that column is
    a central name.

    Raises:
        ValueError: no maintenance sheet / header was found.
    """
    import openpyxl  # noqa: PLC0415  (optional heavy dependency)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        by_norm = {_norm(name): name for name in wb.sheetnames}
        sheet = next(
            (by_norm[k] for k in by_norm if k.startswith("MANT.MAYOR")), None
        ) or next((by_norm[k] for k in by_norm if k == "MANTCEN"), None)
        if sheet is None:
            raise ValueError(
                f"{path}: no maintenance sheet (Mant.Mayor… or MantCEN); "
                f"sheets: {wb.sheetnames}"
            )
        names: set[str] = set()
        col: Optional[int] = None
        for row in wb[sheet].iter_rows(values_only=True):
            if col is None:
                for j, cell in enumerate(row):
                    if cell is not None and _norm(cell) in ("CENTRAL", "NOMBRE"):
                        col = j
                        break
                continue
            if col < len(row) and row[col] is not None:
                name = str(row[col]).strip()
                if name:
                    names.add(name)
        if col is None:
            raise ValueError(f"{path}: sheet {sheet!r} has no CENTRAL header")
        logger.info(
            "mance-split xlsm: %d maintained centrals in sheet %r", len(names), sheet
        )
        return names
    finally:
        wb.close()


def _stage_groups(
    blocks: np.ndarray, stage_of: Callable[[int], int]
) -> Dict[int, List[int]]:
    """Group the positions of ``blocks`` by stage number (insertion order)."""
    groups: Dict[int, List[int]] = defaultdict(list)
    for pos, block in enumerate(blocks.tolist()):
        groups[stage_of(int(block))].append(pos)
    return groups


def maintenance_positions(
    pmax: np.ndarray,
    pnom: float,
    stage_groups: Dict[int, List[int]],
) -> List[Tuple[List[int], float]]:
    """Return ``[(block positions, outage fraction)]`` per maintenance stage.

    A stage is a maintenance stage when its ``pmax`` is uniform over its
    blocks and strictly below ``pnom``, excluding the leading / trailing
    zero runs (entry / retirement) and an all-zero central.
    """
    if pnom <= 0.0 or not stage_groups:
        return []
    tol = _REL_TOL * pnom
    stages = list(stage_groups.items())
    level: List[Optional[float]] = []
    for _, pos in stages:
        vals = pmax[pos]
        uniform = float(vals.max() - vals.min()) <= tol
        level.append(float(vals[0]) if uniform else None)

    def is_zero(v: Optional[float]) -> bool:
        return v is not None and v <= tol

    if all(is_zero(v) for v in level):
        return []
    first = 0
    while first < len(level) and is_zero(level[first]):
        first += 1
    last = len(level) - 1
    while last >= 0 and is_zero(level[last]):
        last -= 1

    out: List[Tuple[List[int], float]] = []
    for k in range(first, last + 1):
        v = level[k]
        if v is None or v >= pnom - tol or v < 0.0:
            continue
        out.append((stages[k][1], 1.0 - v / pnom))
    return out


def split_mances(
    items: Sequence[Dict[str, Any]],
    centrals: Iterable[Dict[str, Any]],
    stage_of: Callable[[int], int],
    mode: str,
    maintained: Optional[set[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split maintenance derates out of ``plpmance`` items.

    Args:
        items: ``ManceParser`` items (``name``, ``block``, ``pmin``, ``pmax``).
        centrals: ``CentralParser`` items (``name``, ``type``, ``pmax``).
        stage_of: block number → stage number.
        mode: one of :data:`MANCE_SPLIT_MODES`.
        maintained: central names from the workbook (``xlsm`` mode).

    Returns:
        ``(adjusted items, outage items)``: the adjusted items carry the
        nominal ``pmax`` (and ``pmin / (1 − f)``) on maintenance stages;
        each outage item has ``name``, ``block`` and ``outage_fraction``
        (one value per block of the mance item, 0 outside maintenance).
    """
    if mode not in MANCE_SPLIT_MODES:
        raise ValueError(
            f"mance-split: unknown mode {mode!r} (expected one of "
            f"{', '.join(MANCE_SPLIT_MODES)})"
        )
    if mode == "off":
        return list(items), []
    if mode == "xlsm" and maintained is None:
        raise ValueError("mance-split xlsm: no workbook (use --mance-xlsm FILE)")

    info = {str(c["name"]): c for c in centrals}
    adjusted: List[Dict[str, Any]] = []
    outages: List[Dict[str, Any]] = []
    for item in items:
        name = str(item["name"])
        central = info.get(name)
        eligible = central is not None and (
            name in maintained
            if mode == "xlsm" and maintained is not None
            else str(central.get("type", "")).lower() in _AUTO_TYPES
        )
        if not eligible or central is None:
            adjusted.append(item)
            continue
        pnom = float(central.get("pmax", 0.0) or 0.0)
        blocks = np.asarray(item["block"])
        pmax = np.asarray(item["pmax"], dtype=float)
        pmin = np.asarray(item["pmin"], dtype=float)
        found = maintenance_positions(pmax, pnom, _stage_groups(blocks, stage_of))
        if not found:
            adjusted.append(item)
            continue
        new_pmax = pmax.copy()
        new_pmin = pmin.copy()
        frac = np.zeros(len(blocks), dtype=float)
        for pos, f in found:
            frac[pos] = f
            new_pmax[pos] = pnom
            if f < 1.0:
                new_pmin[pos] = np.minimum(pnom, pmin[pos] / (1.0 - f))
        adjusted.append({**item, "pmax": new_pmax, "pmin": new_pmin})
        outages.append({"name": name, "block": blocks, "outage_fraction": frac})
    logger.info(
        "mance-split %s: %d of %d centrals carry a maintenance outage schedule",
        mode,
        len(outages),
        len(items),
    )
    return adjusted, outages
