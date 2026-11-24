# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read a CUC-data workbook (``CUC_IEEE<N>-busSystem.xlsx``).

The workbook is the xls2gms input of ``StarGenLite_Clustered-UC.gms``: its
sheets hold GAMS ``$include`` fragments, so the reader locates every table
by its header labels instead of fixed cell addresses.  Values are returned
raw (MW, EUR, hours, tonnes) -- the GAMS unit scaling and derived
parameters live in :mod:`cucdata2gtopt._model`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Thermal-unit columns of the ``Generation`` / ``ClusteredGen`` sheets.
UNIT_FIELDS: tuple[str, ...] = (
    "Bus",
    "MaxProd",
    "MinProd",
    "IniProd",
    "IniState",
    "SUcap",
    "SDcap",
    "RampUp",
    "RampDw",
    "FuelCost",
    "SlopeVarCost",
    "InterVarCost",
    "OMVarCost",
    "MinTU",
    "MinTD",
    "SDduration",
    "ShutdownCost",
    "SUduration1",
    "DownTtimeforSU1",
    "SUcost1",
)

Row = list[Any]


@dataclass(frozen=True)
class RawUnit:
    """One row of the ``Generation`` or ``ClusteredGen`` sheet."""

    name: str
    values: dict[str, float]

    def __getitem__(self, key: str) -> float:
        return self.values.get(key, 0.0)


@dataclass(frozen=True)
class RawLine:
    """One circuit of the ``Network`` sheet."""

    bus_from: int
    bus_to: int
    circuit: int
    in_service: bool
    r: float
    x: float
    pmax: float


@dataclass
class CucData:
    """Everything the GAMS model reads from one workbook."""

    periods: list[str]
    demand: list[float]
    reserve_up: list[float]
    reserve_dn: list[float]
    buses: list[int]
    bus_factor: dict[int, float]
    #: scenario -> bus -> per-period intermittent generation [MW]
    intermittent: dict[str, dict[int, list[float]]]
    units: list[RawUnit]
    clusters: list[RawUnit]
    unit_cluster: dict[str, str]
    lines: list[RawLine]
    params: dict[str, float] = field(default_factory=dict)
    scenario_prob: dict[str, float] = field(default_factory=dict)

    def active_scenarios(self) -> list[str]:
        """Scenarios with a nonzero ``pScenProb`` (the GAMS ``$`` filter)."""
        return [sc for sc, p in self.scenario_prob.items() if p]

    def units_per_cluster(self) -> dict[str, int]:
        """Number of individual units mapped to each cluster."""
        counts: dict[str, int] = {c.name: 0 for c in self.clusters}
        for cluster in self.unit_cluster.values():
            counts[cluster] = counts.get(cluster, 0) + 1
        return counts


def _sheet_rows(wb: Any, name: str) -> list[Row]:
    if name not in wb.sheetnames:
        raise ValueError(f"sheet {name!r} missing; found {wb.sheetnames}")
    return [list(r) for r in wb[name].iter_rows(values_only=True)]


def _label(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _find_cell(rows: list[Row], label: str) -> tuple[int, int]:
    """Return (row, col) of the first cell whose stripped text is *label*."""
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            if _label(value) == label:
                return i, j
    raise ValueError(f"header {label!r} not found")


def _num(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _cell(row: Row, col: int) -> Any:
    return row[col] if col < len(row) else None


def _parse_params(rows: list[Row]) -> tuple[dict[str, float], dict[str, float]]:
    """Scalar ``pXxx = value ;`` assignments and ``pScenProb('scNN')``."""
    params: dict[str, float] = {}
    probs: dict[str, float] = {}
    for row in rows:
        cells = [c for c in row if c is not None]
        for k, value in enumerate(cells):
            name = _label(value)
            if not name.startswith("p") or "=" not in [_label(c) for c in cells]:
                continue
            eq = [_label(c) for c in cells].index("=")
            if k >= eq or eq + 1 >= len(cells):
                continue
            rhs = _num(cells[eq + 1])
            if name == "pScenProb" and k + 1 < eq:
                probs[_label(cells[k + 1]).strip("()'\"")] = rhs
            else:
                params[name] = rhs
            break
    return params, probs


def _parse_series(rows: list[Row], header: str) -> tuple[list[str], list[float]]:
    """Period-labelled column under *header* (period label one column left
    of the first value column of the same table)."""
    hr, hc = _find_cell(rows, header)
    periods: list[str] = []
    values: list[float] = []
    for row in rows[hr + 1 :]:
        key = _label(_cell(row, _key_col(rows, hr, hc)))
        if not key:
            if periods:
                break
            continue
        periods.append(key)
        values.append(_num(_cell(row, hc)))
    return periods, values


def _key_col(rows: list[Row], hr: int, hc: int) -> int:
    """Column holding the period labels of the table whose header is at
    (hr, hc): the nearest non-empty column left of *hc* in the first data
    row."""
    first = rows[hr + 1] if hr + 1 < len(rows) else []
    for col in range(hc - 1, -1, -1):
        if _label(_cell(first, col)):
            return col
    raise ValueError("period label column not found")


def _parse_bus_factors(rows: list[Row]) -> dict[int, float]:
    hr, bc = _find_cell(rows, "Bus")
    _, fc = _find_cell(rows, "Load Factor")
    out: dict[int, float] = {}
    for row in rows[hr + 1 :]:
        bus = _cell(row, bc)
        if bus is None or _label(bus) == "":
            continue
        out[int(bus)] = _num(_cell(row, fc))
    return out


def _parse_intermittent(
    rows: list[Row], periods: list[str]
) -> dict[str, dict[int, list[float]]]:
    """``tIntermGen(p, i, 'En', sc)`` -> {sc: {bus: [MW per period]}}."""
    header_cells: list[tuple[int, int, str]] = []
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            text = _label(value)
            if text.startswith("En."):
                header_cells.append((i, j, text[3:]))
    if not header_cells:
        return {}
    hr = header_cells[0][0]
    first_col = min(j for _, j, _ in header_cells)
    # row layout: <period> '.' <bus> <sc01> <sc02> ...
    period_col, bus_col = first_col - 3, first_col - 1
    index = {p: k for k, p in enumerate(periods)}
    out: dict[str, dict[int, list[float]]] = {}
    for row in rows[hr + 1 :]:
        period = _label(_cell(row, period_col))
        if period not in index:
            continue
        bus = int(_num(_cell(row, bus_col)))
        for _, col, scen in header_cells:
            series = out.setdefault(scen, {}).setdefault(bus, [0.0] * len(periods))
            series[index[period]] = _num(_cell(row, col))
    return out


def _parse_units(rows: list[Row]) -> list[RawUnit]:
    hr, _ = _find_cell(rows, "MaxProd")
    cols = {_label(v): j for j, v in enumerate(rows[hr]) if _label(v)}
    missing = [f for f in UNIT_FIELDS if f not in cols]
    if missing:
        raise ValueError(f"unit table lacks columns {missing}")
    name_col = cols["Bus"] - 1
    units: list[RawUnit] = []
    for row in rows[hr + 1 :]:
        name = _label(_cell(row, name_col))
        if not name or name.startswith("*") or _cell(row, cols["MaxProd"]) is None:
            continue
        units.append(RawUnit(name, {f: _num(_cell(row, cols[f])) for f in UNIT_FIELDS}))
    return units


def _parse_unit_cluster(rows: list[Row], cluster_names: set[str]) -> dict[str, str]:
    """``tct(g, g)`` pairs ``unit . cluster``."""
    out: dict[str, str] = {}
    for row in rows:
        texts = [_label(c) for c in row if _label(c) not in ("", ".", "/", ",")]
        for k in range(len(texts) - 1):
            if texts[k + 1] in cluster_names and texts[k] not in cluster_names:
                out[texts[k]] = texts[k + 1]
    return out


def _parse_network(rows: list[Row]) -> list[RawLine]:
    hr, _ = _find_cell(rows, "InService")
    cols = {_label(v): j for j, v in enumerate(rows[hr]) if _label(v)}
    lines: list[RawLine] = []
    for row in rows[hr + 1 :]:
        # <from> '.' <to> '.' <circuit> <InService> <R> <X> <Pmax>
        numeric = [(j, v) for j, v in enumerate(row) if isinstance(v, (int, float))]
        dots = [j for j, v in enumerate(row) if _label(v) == "."]
        if len(dots) < 2 or not numeric:
            continue
        bus_from = row[dots[0] - 1]
        bus_to = row[dots[0] + 1]
        circuit = row[dots[1] + 1]
        lines.append(
            RawLine(
                bus_from=int(bus_from),
                bus_to=int(bus_to),
                circuit=int(circuit),
                in_service=bool(_num(_cell(row, cols["InService"]))),
                r=_num(_cell(row, cols["R"])),
                x=_num(_cell(row, cols["X"])),
                pmax=_num(_cell(row, cols["Pmax"])),
            )
        )
    return lines


def read_workbook(path: Path) -> CucData:
    """Parse a CUC-data workbook into :class:`CucData`."""
    import openpyxl  # noqa: PLC0415  (heavy import, only when reading)

    wb = openpyxl.load_workbook(path, data_only=True)
    param_rows = _sheet_rows(wb, "Indices&Param")
    params, probs = _parse_params(param_rows)
    dri = _sheet_rows(wb, "DemandReserveIG")
    periods, demand = _parse_series(dri, "Energy")
    res_periods, reserve_up = _parse_series(dri, "Energy_up")
    _, reserve_dn = _parse_series(dri, "Energy_dw")
    if res_periods != periods:
        raise ValueError("reserve and demand tables have different periods")
    bus_factor = _parse_bus_factors(dri)
    units = _parse_units(_sheet_rows(wb, "Generation"))
    clusters = _parse_units(_sheet_rows(wb, "ClusteredGen"))
    unit_cluster = _parse_unit_cluster(
        _sheet_rows(wb, "GenToCluster"), {c.name for c in clusters}
    )
    lines = _parse_network(_sheet_rows(wb, "Network"))
    buses = sorted(
        set(bus_factor)
        | {ln.bus_from for ln in lines}
        | {ln.bus_to for ln in lines}
        | {int(u["Bus"]) for u in units}
    )
    return CucData(
        periods=periods,
        demand=demand,
        reserve_up=reserve_up,
        reserve_dn=reserve_dn,
        buses=buses,
        bus_factor=bus_factor,
        intermittent=_parse_intermittent(dri, periods),
        units=units,
        clusters=clusters,
        unit_cluster=unit_cluster,
        lines=lines,
        params=params,
        scenario_prob=probs,
    )
