# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Synthetic CUC-data workbook (same sheet layout, made-up numbers).

The real CUC-data files carry no licence, so the unit tests never use
them: :func:`make_workbook` writes a 3-bus, 2-cluster x 2-unit, 4-hour
workbook laid out exactly like the xls2gms input of the GAMS model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cucdata2gtopt._reader import UNIT_FIELDS

PERIODS = ["h01", "h02", "h03", "h04"]
DEMAND = [100.0, 150.0, 200.0, 120.0]

#: cluster name -> (bus, field values in UNIT_FIELDS order after Bus)
CLUSTERS: dict[str, list[float]] = {
    # Bus Max Min IniP IniS SU SD RU RD Fuel Slope Inter OM TU TD SDd SDc SUd DT SUc
    "genC01": [1, 100, 20, 60, 3, 20, 20, 50, 50, 1, 10, 50, 1, 3, 2, 1, 0, 1, 2, 70],
    "genC02": [3, 80, 10, 0, -1, 10, 10, 30, 30, 2, 15, 20, 0, 1, 0, 0, 5, 1, 1, 30],
}


def _put(ws: Any, row: int, col: int, values: list[Any]) -> None:
    for k, value in enumerate(values):
        if value is not None:
            ws.cell(row=row, column=col + k, value=value)


def _unit_sheet(ws: Any, rows: list[tuple[str, list[float]]]) -> None:
    _put(ws, 3, 2, ["* ", None, None, "DownTtimeforSU1 must be equal to MinTD"])
    _put(ws, 8, 4, list(UNIT_FIELDS))
    _put(ws, 9, 2, ["*", None, "Ord", "[MW]"])
    _put(ws, 10, 2, ["*"])
    for k, (name, values) in enumerate(rows):
        _put(ws, 11 + k, 3, [name, *values])


def make_workbook(path: Path, *, reserve_pct: float = 10.0, ig: bool = True) -> Path:
    """Write the synthetic workbook to *path* and return it."""
    import openpyxl  # noqa: PLC0415

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Indices&Param"
    _put(ws, 3, 2, ["* indices"])
    _put(ws, 5, 3, ["p", "periods", "/", "h01", "*", "h04", "/"])
    _put(ws, 21, 3, ["pENSCost", None, "=", 5000, ";"])
    _put(ws, 23, 3, ["pWindCurtCost", None, "=", 100, ";"])
    _put(ws, 25, 3, ["pNetworkConst", None, "=", 1, ";"])
    _put(ws, 29, 3, ["pScenProb", "('sc01')", "=", 1, ";"])
    _put(ws, 30, 3, ["pScenProb", "('sc02')", "=", 0, ";"])

    ws = wb.create_sheet("DemandReserveIG")
    _put(ws, 3, 2, ["* demand [MW]"])
    _put(ws, 4, 13, ["*", "Bus", "Load Factor"])
    _put(ws, 5, 4, ["Energy", None, None, None, None, "Energy_up", "Energy_dw"])
    _put(ws, 5, 22, ["En.sc01", "En.sc02"])
    for k, (p, d) in enumerate(zip(PERIODS, DEMAND, strict=True)):
        r = d * reserve_pct / 100.0
        _put(ws, 6 + k, 3, [p, d, None, None, None, p, r, r])
        if ig:
            _put(ws, 6 + k, 19, [p, ".", 2, 10.0 * (k + 1), 0])
    for k, (bus, factor) in enumerate([(1, 0.5), (2, 0.0), (3, 0.5)]):
        _put(ws, 6 + k, 14, [bus, factor])

    units = [
        (f"gen{2 * c + k + 1}", values)
        for c, values in enumerate(CLUSTERS.values())
        for k in range(2)
    ]
    _unit_sheet(wb.create_sheet("Generation"), units)
    _unit_sheet(wb.create_sheet("ClusteredGen"), list(CLUSTERS.items()))

    ws = wb.create_sheet("GenToCluster")
    _put(ws, 3, 2, ["* individual generator to clustered generator"])
    for k, (name, _) in enumerate(units):
        cluster = list(CLUSTERS)[k // 2]
        lead = ["tct(g,g)", None, "/"] if k == 0 else [None, None, None]
        _put(ws, 5 + k, 3, [*lead, name, ".", cluster])

    ws = wb.create_sheet("Network")
    _put(ws, 2, 2, ["* Network Information"])
    _put(ws, 4, 8, ["InService", "R ", "X ", "Pmax"])
    _put(ws, 5, 2, ["*", "From bus i", None, "To bus j", None, "Circuit ID"])
    lines = [(1, 2, 1, 1, 0.01, 0.1, 150), (2, 3, 1, 1, 0.01, 0.2, 150)]
    lines += [(2, 3, 2, 1, 0.0, 0.2, 50), (1, 3, 1, 0, 0.0, 0.1, 10)]
    for k, (a, b, c, ins, r, x, pmax) in enumerate(lines):
        _put(ws, 6 + k, 3, [a, ".", b, ".", c, ins, r, x, pmax])
    wb.save(path)
    return path
