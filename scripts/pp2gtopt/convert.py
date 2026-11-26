#!/usr/bin/env python3
"""Convert pandapower networks to gtopt JSON format.

The default network is the IEEE 30-bus Washington test system from MATPOWER,
available as pandapower.networks.case_ieee30(). Arbitrary pandapower networks
can be loaded from files via ``load_network(path)`` (JSON, Excel, MATPOWER).
The conversion:
  - Converts line/trafo reactances from physical (Ohm) to per-unit (p.u.)
  - Linearises quadratic generator costs to their cp1 coefficient ($/MWh)
  - Models lossless transformers as lines with x = vk_percent / 100 p.u.
  - Disables network thermal limits (max_i_ka = inf in the pandapower case)

For CLI usage run ``pp2gtopt --help``.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandapower as pp
import pandapower.networks as pn

import pandas as pd

from gtopt_shared.output_format import write_dataframe
from gtopt_shared.time_grid import (
    DEFAULT_START,
    BlockGrid,
    TimeGrid,
    hour_timestamps,
    write_block_schedule,
)

logger = logging.getLogger(__name__)

_BASE_MVA = 100.0  # system base (MVA) used for per-unit conversions
_TMAX_UNLIMITED: float = 9999  # sentinel for unconstrained thermal limit

# File extensions recognised by load_network()
_FORMAT_JSON = ".json"
_FORMAT_EXCEL = {".xlsx", ".xls"}
_FORMAT_MATPOWER = {".m", ".mat"}
_SUPPORTED_FORMATS = f"{_FORMAT_JSON}, .xlsx/.xls, .m/.mat (MATPOWER)"


def load_network(path: Path, mpc_var: str = "mpc") -> pp.pandapowerNet:
    """Load a pandapower network from a file.

    Supported formats
    -----------------
    ``.json``
        pandapower JSON (written by ``pandapower.to_json()``).
    ``.xlsx`` / ``.xls``
        pandapower Excel workbook (written by ``pandapower.to_excel()``).
    ``.m`` / ``.mat``
        MATPOWER case (``.m`` text or ``.mat`` holding an ``mpc`` struct),
        converted via ``pandapower.converter.from_mpc()``.  *mpc_var* names
        the struct variable inside a ``.mat`` file (default ``mpc``).

    Parameters
    ----------
    path:
        Path to the network file.

    Returns
    -------
    pp.pandapowerNet
        The loaded network.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValueError
        If the file extension is not recognised.
    """
    if not path.exists():
        raise FileNotFoundError(f"Network file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == _FORMAT_JSON:
        return pp.from_json(str(path))
    if suffix in _FORMAT_EXCEL:
        return pp.from_excel(str(path))
    if suffix in _FORMAT_MATPOWER:
        return _load_matpower(path, mpc_var)
    raise ValueError(
        f"Unsupported file format {suffix!r} for {path.name}. "
        f"Supported extensions: {_SUPPORTED_FORMATS}"
    )


def _load_matpower(path: Path, mpc_var: str = "mpc") -> pp.pandapowerNet:
    """Load a MATPOWER ``.m`` / ``.mat`` case through pandapower, fixing PST signs.

    *mpc_var* names the MATPOWER struct inside a ``.mat`` file.

    ``pandapower.converter.from_ppc`` orients a transformer from its
    high- to its low-voltage bus; when the MATPOWER branch runs from the
    low- to the high-voltage side it swaps the buses but keeps the
    ``SHIFT`` angle, which reverses the phase shift (MATPOWER:
    ``P_ft = b (theta_f - theta_t - shift)``).  Undo that here.
    """
    # pylint: disable=import-outside-toplevel
    import copy

    from pandapower.converter.matpower.from_mpc import _m2ppc, _mat2ppc
    from pandapower.converter.pypower.from_ppc import from_ppc

    if path.suffix.lower() == ".mat":
        ppc = _mat2ppc(str(path), mpc_var)
    else:
        ppc = _m2ppc(str(path))
    net = from_ppc(copy.deepcopy(ppc), f_hz=50)
    _fix_swapped_phase_shift(net, ppc)
    return net


def _fix_swapped_phase_shift(net: pp.pandapowerNet, ppc: dict[str, Any]) -> int:
    """Negate ``shift_degree`` of trafos whose buses ``from_ppc`` swapped.

    Returns the number of transformers corrected.
    """
    branch = ppc.get("branch")
    bus = ppc.get("bus")
    if branch is None or bus is None or net.trafo.empty:
        return 0
    base_kv = {int(row[0]): float(row[9]) for row in bus}
    fixed = 0
    used: set[int] = set()
    for row in branch:
        shift = float(row[9])
        if shift == 0.0 or float(row[10]) <= 0.0:
            continue
        f_bus, t_bus = int(row[0]), int(row[1])
        if base_kv.get(t_bus, 0.0) <= base_kv.get(f_bus, 0.0):
            continue  # not swapped by from_ppc
        mask = (
            (net.trafo["hv_bus"] == t_bus)
            & (net.trafo["lv_bus"] == f_bus)
            & (net.trafo["shift_degree"] == shift)
        )
        for idx in net.trafo.index[mask]:
            if int(idx) in used:
                continue
            net.trafo.at[idx, "shift_degree"] = -shift
            used.add(int(idx))
            fixed += 1
            break
    return fixed


def get_bus_base_kv(net: pp.pandapowerNet, bus_idx: int) -> float:
    """Return nominal voltage (kV) of a bus by its integer index."""
    return float(net.bus.loc[bus_idx, "vn_kv"])


def ohm_to_pu(ohm: float, base_kv: float, base_mva: float = 100.0) -> float:
    """Convert impedance in Ohm to per-unit on (base_kv, base_mva) base."""
    z_base = base_kv**2 / base_mva
    return ohm / z_base


def _in_service(row: Any) -> bool:
    """Return the pandapower ``in_service`` flag of *row* (default True)."""
    flag = row.get("in_service", True)
    return True if flag is None else bool(flag)


def _get_poly_cost(
    net: pp.pandapowerNet, et: str, element: int, default: float
) -> float:
    """Return cp1 linear cost coefficient for an element, or *default*."""
    mask = (net.poly_cost["et"] == et) & (net.poly_cost["element"] == element)
    row = net.poly_cost[mask]
    return float(row["cp1_eur_per_mw"].iloc[0]) if not row.empty else default


def _build_buses(net: pp.pandapowerNet) -> list[dict[str, Any]]:
    """Build bus array; fix the ext_grid bus as the DC OPF reference (theta=0)."""
    slack_bus = int(net.ext_grid.iloc[0]["bus"]) + 1  # 1-indexed
    bus_array: list[dict[str, Any]] = []
    for idx, _row in net.bus.iterrows():
        bus_uid = int(idx) + 1
        entry: dict[str, Any] = {"uid": bus_uid, "name": f"b{bus_uid}"}
        if bus_uid == slack_bus:
            entry["reference_theta"] = 0  # fix slack angle to 0 rad
        bus_array.append(entry)
    return bus_array


def _build_ext_grid_gen(net: pp.pandapowerNet) -> dict[str, Any]:
    """Return the gtopt generator entry for the ext_grid (slack generator)."""
    eg = net.ext_grid.iloc[0]
    eg_bus = int(eg["bus"])
    gcost = _get_poly_cost(net, "ext_grid", 0, default=20.0)
    pmax = float(eg.get("max_p_mw", 360.2))
    pmin = float(eg.get("min_p_mw", 0.0) or 0.0)
    if not math.isfinite(pmin):
        pmin = 0.0
    return {
        "uid": 1,
        "name": "g1",
        "bus": eg_bus + 1,
        "pmin": pmin,
        "pmax": pmax,
        "gcost": gcost,
        "capacity": pmax,
    }


GenKey = tuple[str, int]  # (pandapower table, index), e.g. ("sgen", 3)


def _build_generators(net: pp.pandapowerNet) -> list[dict[str, Any]]:
    """Build generator array: ext_grid first, then PV gens, then sgens."""
    return [entry for _key, entry in _build_generators_keyed(net)]


def _build_generators_keyed(
    net: pp.pandapowerNet,
) -> list[tuple[GenKey, dict[str, Any]]]:
    """Generator entries paired with their pandapower ``(table, index)``.

    Order and uids: ext_grid (uid 1), in-service ``net.gen`` rows (uid
    2…), then in-service ``net.sgen`` rows.  Out-of-service rows are
    skipped.  Static generators (``net.sgen``: wind / PV, and every
    MATPOWER generator after the first one on a bus, which
    ``from_ppc`` puts there) keep their ``min_p_mw`` / ``max_p_mw``
    limits and ``poly_cost`` (default 0); an sgen without limits (e.g. a
    MATPOWER negative load) is a fixed injection ``pmin = pmax = p_mw``.
    """
    keyed: list[tuple[GenKey, dict[str, Any]]] = [
        (("ext_grid", int(net.ext_grid.index[0])), _build_ext_grid_gen(net))
    ]
    gen_uid = 1
    for idx, row in net.gen.iterrows():
        if not _in_service(row):
            continue
        gen_uid += 1
        pmax = float(row["max_p_mw"])
        pmin = float(row.get("min_p_mw", 0.0))
        gcost = _get_poly_cost(net, "gen", int(idx), default=40.0)
        keyed.append(
            (
                ("gen", int(idx)),
                {
                    "uid": gen_uid,
                    "name": f"g{gen_uid}",
                    "bus": int(row["bus"]) + 1,
                    "pmin": pmin,
                    "pmax": pmax,
                    "gcost": gcost,
                    "capacity": pmax,
                },
            )
        )
    sgen = getattr(net, "sgen", None)
    if sgen is not None:
        for idx, row in sgen.iterrows():
            if not _in_service(row):
                continue
            pmax = float(row.get("max_p_mw", math.nan))
            if math.isfinite(pmax):
                pmin = float(row.get("min_p_mw", 0.0))
                if not math.isfinite(pmin):
                    pmin = 0.0
            else:
                # No limits: a fixed injection (e.g. a MATPOWER negative
                # load, Pd < 0, which from_ppc turns into an sgen).
                pmax = float(row.get("p_mw", 0.0) or 0.0)
                pmin = pmax
            if pmax <= 0.0:
                continue
            gen_uid += 1
            keyed.append(
                (
                    ("sgen", int(idx)),
                    {
                        "uid": gen_uid,
                        "name": f"g{gen_uid}",
                        "bus": int(row["bus"]) + 1,
                        "pmin": pmin,
                        "pmax": pmax,
                        "gcost": _get_poly_cost(net, "sgen", int(idx), default=0.0),
                        "capacity": pmax,
                    },
                )
            )
    return keyed


def _build_demands(net: pp.pandapowerNet) -> list[dict[str, Any]]:
    """Build demand array from net.load, skipping zero-load entries."""
    return [entry for _idx, entry in _build_demands_keyed(net)]


def _build_demands_keyed(
    net: pp.pandapowerNet,
    profiled: frozenset[int] = frozenset(),
) -> list[tuple[int, dict[str, Any]]]:
    """Demand entries paired with their ``net.load`` index.

    Snapshot mode (``profiled`` empty) writes ``lmax = [[p_mw]]`` and skips
    loads with ``p_mw <= 0``.  In time-series mode a load in ``profiled``
    is always kept (``lmax`` is then rewritten to the ``"lmax"`` schedule
    reference by the caller) and unprofiled loads get a constant scalar
    ``lmax``.
    """
    keyed: list[tuple[int, dict[str, Any]]] = []
    uid = 1
    ts_mode = bool(profiled)
    for idx, row in net.load.iterrows():
        if not _in_service(row):
            continue
        p_mw = float(row["p_mw"]) * float(row.get("scaling", 1.0) or 1.0)
        if p_mw <= 0.0 and int(idx) not in profiled:
            continue
        keyed.append(
            (
                int(idx),
                {
                    "uid": uid,
                    "name": f"d{uid}",
                    "bus": int(row["bus"]) + 1,
                    "lmax": p_mw if ts_mode else [[p_mw]],
                },
            )
        )
        uid += 1
    return keyed


def _line_tmax(max_i_ka: float, base_kv: float, max_loading: float = 100.0) -> float:
    """Return thermal limit in MW; 9999 if the pandapower value is unconstrained.

    The rating is the current limit at nominal voltage,
    ``sqrt(3) * V * max_i_ka``, scaled by ``max_loading_percent`` (the
    OPF limit pandapower applies to the larger end current).
    """
    if math.isinf(max_i_ka) or max_i_ka >= _TMAX_UNLIMITED:
        return _TMAX_UNLIMITED
    return round(max_i_ka * base_kv * math.sqrt(3) * max_loading / 100.0, 1)


def _loading_percent(row: Any) -> float:
    """``max_loading_percent`` of a branch row (100 when missing / NaN)."""
    value = _finite_pos(row.get("max_loading_percent", 100.0))
    return 100.0 if value is None else value


def _impedance_fields(
    x_pu: float, r_pu: float, base_kv: float, base_mva: float, losses: bool
) -> dict[str, float]:
    """Series impedance fields of a branch entry.

    Without losses: ``reactance`` in p.u. (no ``voltage``, gtopt's
    per-unit mode).  With losses every branch is written in physical
    units, ``voltage`` [kV] plus ``reactance`` / ``resistance`` [Ω] on
    that voltage, so that gtopt's ``R·f²/V²`` loss is in MW.  Mixing the
    two is not allowed: ``f = V²/X·Δθ`` would scale the per-unit
    branches by ``1/base_mva`` against the physical ones.
    """
    if not losses:
        return {"reactance": round(x_pu, 6)}
    z_base = base_kv**2 / base_mva
    # Significant digits, not decimals: a 0.208 kV bus has z_base ≈ 4e-4 Ω.
    return {
        "voltage": round(base_kv, 6),
        "reactance": float(f"{x_pu * z_base:.9g}"),
        "resistance": float(f"{r_pu * z_base:.9g}"),
    }


def _build_physical_lines(
    net: pp.pandapowerNet, base_mva: float, losses: bool = False
) -> list[dict[str, Any]]:
    """Build line entries for physical lines (not transformers)."""
    lines: list[dict[str, Any]] = []
    for _idx, row in net.line.iterrows():
        if not _in_service(row):
            continue
        fb = int(row["from_bus"])
        tb = int(row["to_bus"])
        base_kv = get_bus_base_kv(net, fb)
        length = float(row["length_km"])
        x_pu = ohm_to_pu(float(row["x_ohm_per_km"]) * length, base_kv, base_mva)
        if abs(x_pu) < 1e-6:  # keep series-compensated (x < 0) branches
            continue
        r_pu = ohm_to_pu(
            float(row.get("r_ohm_per_km", 0.0) or 0.0) * length, base_kv, base_mva
        )
        tmax = _line_tmax(
            float(row.get("max_i_ka", float("inf"))), base_kv, _loading_percent(row)
        )
        lines.append(
            {
                "name": f"l{fb + 1}_{tb + 1}",
                "bus_a": fb + 1,
                "bus_b": tb + 1,
                **_impedance_fields(x_pu, r_pu, base_kv, base_mva, losses),
                "tmax_ab": tmax,
                "tmax_ba": tmax,
            }
        )
    return lines


def _get_tap_ratio(row: Any) -> float:
    """Return the off-nominal tap ratio for a pandapower transformer row.

    The off-nominal tap ratio is computed as::

        tap = 1 + (tap_pos - tap_neutral) * tap_step_percent / 100

    When ``tap_pos`` equals ``tap_neutral`` (or tap data is missing / NaN)
    the function returns 1.0 (nominal, no correction).
    """
    try:
        neutral = row.get("tap_neutral")
        tap_neutral = 0.0 if neutral is None else float(neutral)

        pos = row.get("tap_pos")
        tap_pos = tap_neutral if pos is None else float(pos)

        step = row.get("tap_step_percent")
        tap_step_percent = 0.0 if step is None else float(step)
    except (TypeError, ValueError):
        return 1.0
    deviation = (tap_pos - tap_neutral) * tap_step_percent / 100.0
    return round(1.0 + deviation, 6)


def _build_transformers(
    net: pp.pandapowerNet, base_mva: float, losses: bool = False
) -> list[dict[str, Any]]:
    """Build line entries for transformers (modelled as branches with tap support).

    Each pandapower ``trafo`` row is converted to a gtopt line entry with:

    * ``type = "transformer"`` for identification.
    * Reactance computed from the short-circuit voltage:
      ``x_pu = (vk_percent / 100) * (base_mva / sn_mva)``.
    * ``tap_ratio`` set to the off-nominal tap ratio (1.0 when at nominal).
    * ``phase_shift_deg`` set for phase-shifting transformers
      (from ``shift_degree``).
    * Thermal limit in MW derived from ``sn_mva`` when ``max_loading_percent``
      is available, otherwise unconstrained (9999 MW).
    """
    trafos: list[dict[str, Any]] = []
    for _idx, row in net.trafo.iterrows():
        if not _in_service(row):
            continue
        hv = int(row["hv_bus"])
        lv = int(row["lv_bus"])
        sn_mva = float(row["sn_mva"])
        # Series reactance from the short-circuit voltages: |z| = vk and
        # r = vkr, so x = sign(vk) * sqrt(vk^2 - vkr^2) (vk < 0 encodes a
        # negative-reactance branch, e.g. a 3-winding star-point leg).
        vk = float(row["vk_percent"])
        vkr = float(row.get("vkr_percent", 0.0) or 0.0)
        xk = math.copysign(math.sqrt(max(vk * vk - vkr * vkr, 0.0)), vk)
        x_pu = (xk / 100.0) * (base_mva / sn_mva)
        if abs(x_pu) < 1e-6:  # keep series-compensated (x < 0) branches
            continue

        r_pu = (vkr / 100.0) * (base_mva / sn_mva)
        entry: dict[str, Any] = {
            "name": f"t{hv + 1}_{lv + 1}",
            "bus_a": hv + 1,
            "bus_b": lv + 1,
            **_impedance_fields(
                x_pu,
                r_pu,
                get_bus_base_kv(net, hv) if losses else 0.0,
                base_mva,
                losses,
            ),
            "type": "transformer",
        }

        # Thermal limit: derive from sn_mva when loading percentage is given,
        # otherwise leave unconstrained.
        max_loading = float(row.get("max_loading_percent", 100) or 100)
        tmax_mw = round(sn_mva * max_loading / 100.0, 1)
        if tmax_mw < _TMAX_UNLIMITED:
            entry["tmax_ab"] = tmax_mw
            entry["tmax_ba"] = tmax_mw
        else:
            entry["tmax_ab"] = _TMAX_UNLIMITED
            entry["tmax_ba"] = _TMAX_UNLIMITED

        # Off-nominal tap ratio
        tau = _get_tap_ratio(row)
        if abs(tau - 1.0) > 1e-6:
            entry["tap_ratio"] = tau

        # Phase-shift angle for PSTs
        shift_deg = float(row.get("shift_degree", 0) or 0)
        if abs(shift_deg) > 1e-6:
            entry["phase_shift_deg"] = round(shift_deg, 6)

        trafos.append(entry)
    return trafos


def _build_impedances(
    net: pp.pandapowerNet, base_mva: float, losses: bool = False
) -> list[dict[str, Any]]:
    """Build line entries for pandapower ``impedance`` branches.

    ``from_ppc`` turns MATPOWER branches between buses of different
    nominal voltage with no tap ratio into ``impedance`` elements, whose
    ``xft_pu`` is on the element base ``sn_mva``.
    """
    table = getattr(net, "impedance", None)
    entries: list[dict[str, Any]] = []
    if table is None:
        return entries
    for _idx, row in table.iterrows():
        if not _in_service(row):
            continue
        fb = int(row["from_bus"])
        tb = int(row["to_bus"])
        x_pu = float(row["xft_pu"]) * base_mva / float(row["sn_mva"])
        if abs(x_pu) < 1e-6:
            continue
        r_pu = float(row.get("rft_pu", 0.0) or 0.0) * base_mva / float(row["sn_mva"])
        entries.append(
            {
                "name": f"z{fb + 1}_{tb + 1}",
                "bus_a": fb + 1,
                "bus_b": tb + 1,
                **_impedance_fields(
                    x_pu,
                    r_pu,
                    get_bus_base_kv(net, fb) if losses else 0.0,
                    base_mva,
                    losses,
                ),
                "tmax_ab": _TMAX_UNLIMITED,
                "tmax_ba": _TMAX_UNLIMITED,
            }
        )
    return entries


def _build_lines(
    net: pp.pandapowerNet, base_mva: float, losses: bool = False
) -> list[dict[str, Any]]:
    """Build line array: physical lines followed by transformers, with sequential UIDs.

    Duplicate names (e.g. parallel transformers between the same buses) are
    made unique by appending ``_2``, ``_3``, … to later occurrences.
    With *losses* every branch carries ``voltage`` / ``resistance`` in
    physical units (see :func:`_impedance_fields`).
    """
    entries = (
        _build_physical_lines(net, base_mva, losses)
        + _build_transformers(net, base_mva, losses)
        + _build_impedances(net, base_mva, losses)
    )
    seen: dict[str, int] = {}
    for uid, entry in enumerate(entries, start=1):
        entry["uid"] = uid
        name = entry.get("name", "")
        if name in seen:
            seen[name] += 1
            entry["name"] = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
    return entries


@dataclass(frozen=True)
class LineLossOptions:
    """Line-loss model options emitted into ``model_options``.

    ``line_losses_mode`` (``None`` = not emitted): any mode other than
    ``"none"`` also writes every branch in physical units (``voltage``
    [kV], ``reactance`` / ``resistance`` [Ω]) so the pandapower
    resistances become gtopt losses ``R·f²/V²``.  ``loss_segments``
    (PWL segments) and ``line_thermal_limit_mode`` (``flow`` /
    ``sending`` / ``full``) are emitted when set.
    """

    line_losses_mode: str | None = None
    loss_segments: int | None = None
    line_thermal_limit_mode: str | None = None

    @property
    def physical_units(self) -> bool:
        """Whether branches carry resistance (a loss mode is active)."""
        return self.line_losses_mode not in (None, "none")

    def model_options(self) -> dict[str, Any]:
        """The ``model_options`` entries of the options that are set."""
        fields = {
            "line_losses_mode": self.line_losses_mode,
            "loss_segments": self.loss_segments,
            "line_thermal_limit_mode": self.line_thermal_limit_mode,
        }
        return {key: val for key, val in fields.items() if val is not None}


SUPPORTED_SOLVERS: tuple[str, ...] = ("monolithic", "sddp", "cascade")
DEFAULT_SOLVER: str = "cascade"


def _default_cascade_options() -> dict[str, Any]:
    """Build a 3-level default cascade configuration for OPF cases.

    Levels mirror the plp2gtopt defaults so behaviour is consistent:
    uninodal (single-bus) → transport (lines, no Kirchhoff/losses) →
    full network (Kirchhoff enabled).  Each transition inherits the
    Benders optimality cuts from the previous level.
    """
    transition: dict[str, Any] = {
        "inherit_optimality_cuts": -1,
    }
    return {
        "level_array": [
            {
                "uid": 1,
                "name": "uninodal",
                "model_options": {"use_single_bus": True},
            },
            {
                "uid": 2,
                "name": "transport",
                "model_options": {
                    "use_single_bus": False,
                    "use_kirchhoff": False,
                    "use_line_losses": False,
                },
                "transition": transition,
            },
            {
                "uid": 3,
                "name": "full_network",
                "model_options": {
                    "use_single_bus": False,
                    "use_kirchhoff": True,
                },
                "transition": transition,
            },
        ],
    }


@dataclass
class TimeSeriesData:
    """Hourly profiles + time grid for a multi-block conversion.

    ``load_mw`` maps ``net.load`` index → hourly MW; ``gen_factor`` maps
    ``(table, index)`` (``"gen"`` / ``"sgen"``) → hourly availability
    factor in [0, 1].  Every array has ``grid.n_hours`` entries; they are
    averaged into blocks by ``grid.aggregate`` (chronological
    :class:`TimeGrid` or general :class:`BlockGrid`, e.g. monthly
    load-duration-curve blocks).

    ``hour_times`` (calendar time of every hour; default: hourly from
    ``2023-01-01``) and ``hour_offset`` (position of the first hour in the
    source series) only feed the ``BlockHourMap`` traceability table.
    ``storage`` converts ``net.storage`` rows into gtopt batteries with
    one-way efficiency ``storage_efficiency``.
    """

    grid: TimeGrid | BlockGrid
    load_mw: dict[int, np.ndarray] = field(default_factory=dict)
    gen_factor: dict[GenKey, np.ndarray] = field(default_factory=dict)
    one_phase_per_stage: bool = True
    summary: dict[str, Any] = field(default_factory=dict)
    hour_times: pd.DatetimeIndex | None = None
    hour_offset: int = 0
    storage: bool = True
    storage_efficiency: float = 0.95


def _finite_pos(value: Any) -> float | None:
    """``float(value)`` when finite and > 0, else ``None``."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) and val > 0 else None


def _build_batteries(
    net: pp.pandapowerNet, efficiency: float = 0.95
) -> list[dict[str, Any]]:
    """Convert in-service ``net.storage`` rows into unified gtopt batteries.

    pandapower storage uses the load sign convention (``p_mw > 0`` =
    charging).  Mapping:

    * ``emax = capacity = max_e_mwh``; ``emin = min_e_mwh`` (default 0).
    * ``pmax_charge = max_p_mw`` (else ``sn_mva``, else ``emax / 4``);
      ``pmax_discharge = -min_p_mw`` when negative, else ``pmax_charge``.
    * ``input_efficiency = output_efficiency``: ``sqrt(efficiency)`` when a
      round-trip ``efficiency`` column (p.u.) or ``efficiency_percent`` is
      present, else *efficiency*.
    * ``eini = soc_percent / 100 × emax`` (default 50 %).

    Rows without a positive ``max_e_mwh`` are skipped with a warning.
    """
    table = getattr(net, "storage", None)
    if table is None or table.empty:
        return []
    batteries: list[dict[str, Any]] = []
    for idx, row in table.iterrows():
        if not bool(row.get("in_service", True)):
            continue
        emax = _finite_pos(row.get("max_e_mwh"))
        if emax is None:
            logger.warning("storage %s: no max_e_mwh; skipped", idx)
            continue
        emin = float(row.get("min_e_mwh", 0.0) or 0.0)
        emin = emin if math.isfinite(emin) else 0.0
        pch = (
            _finite_pos(row.get("max_p_mw"))
            or _finite_pos(row.get("sn_mva"))
            or emax / 4.0
        )
        min_p = row.get("min_p_mw", math.nan)
        try:
            pdis = -float(min_p) if float(min_p) < 0 else pch
        except (TypeError, ValueError):
            pdis = pch
        eff = efficiency
        rt = _finite_pos(row.get("efficiency"))
        if rt is None and _finite_pos(row.get("efficiency_percent")) is not None:
            rt = float(row["efficiency_percent"]) / 100.0
        if rt is not None:
            eff = math.sqrt(min(rt, 1.0))
        soc = row.get("soc_percent", math.nan)
        try:
            soc_frac = float(soc) / 100.0 if math.isfinite(float(soc)) else 0.5
        except (TypeError, ValueError):
            soc_frac = 0.5
        uid = len(batteries) + 1
        name = row.get("name")
        batteries.append(
            {
                "uid": uid,
                "name": str(name) if isinstance(name, str) and name else f"bess{uid}",
                "bus": int(row["bus"]) + 1,
                "input_efficiency": round(eff, 6),
                "output_efficiency": round(eff, 6),
                "emin": emin,
                "emax": emax,
                "capacity": emax,
                "eini": round(min(max(soc_frac, 0.0), 1.0) * emax, 6),
                "pmax_charge": pch,
                "pmax_discharge": pdis,
            }
        )
    return batteries


def _write_block_hour_map(ts: TimeSeriesData, input_dir: Path) -> str:
    """Write ``BlockHourMap/block_hour_map.parquet``; return its JSON key."""
    grid = ts.grid
    block_grid = grid.to_block_grid() if isinstance(grid, TimeGrid) else grid
    times = ts.hour_times
    if times is None:
        times = hour_timestamps(grid.n_hours, DEFAULT_START) + pd.Timedelta(
            hours=ts.hour_offset
        )
    out_dir = input_dir / "BlockHourMap"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_dataframe(
        block_grid.hour_block_map(times, ts.hour_offset), out_dir, "block_hour_map"
    )
    return "BlockHourMap/block_hour_map"


def _apply_timeseries(
    data: dict[str, Any],
    net: pp.pandapowerNet,
    ts: TimeSeriesData,
    input_dir: Path,
) -> dict[str, Any]:
    """Rewrite *data* for a multi-block horizon and write the schedules.

    Emits ``<input_dir>/Demand/lmax.parquet`` (profiled loads) and
    ``<input_dir>/GeneratorProfile/profile.parquet`` (+ the matching
    ``capacity_profile_array``).  Returns an energy summary.
    """
    grid = ts.grid
    system = data["system"]
    data["simulation"] = grid.simulation(one_phase_per_stage=ts.one_phase_per_stage)
    durations = grid.block_durations()
    data["simulation"]["block_hour_map"] = _write_block_hour_map(ts, input_dir)

    # -- demands ---------------------------------------------------------
    demands_keyed = _build_demands_keyed(net, frozenset(ts.load_mw))
    system["demand_array"] = [entry for _idx, entry in demands_keyed]
    lmax_by_uid: dict[int, np.ndarray] = {}
    for idx, entry in demands_keyed:
        hourly = ts.load_mw.get(idx)
        if hourly is not None:
            lmax_by_uid[entry["uid"]] = grid.aggregate(hourly)
            entry["lmax"] = "lmax"
    if lmax_by_uid:
        write_block_schedule(input_dir / "Demand", "lmax", lmax_by_uid, grid)
    profiled_mw = (
        np.sum(list(lmax_by_uid.values()), axis=0)
        if lmax_by_uid
        else np.zeros(grid.n_blocks)
    )
    constant_mw = sum(
        float(e["lmax"]) for e in system["demand_array"] if e["lmax"] != "lmax"
    )
    total_load = profiled_mw + constant_mw

    # -- generator availability profiles --------------------------------
    gens_keyed = _build_generators_keyed(net)
    system["generator_array"] = [entry for _key, entry in gens_keyed]
    profiles: list[dict[str, Any]] = []
    factor_by_uid: dict[int, np.ndarray] = {}
    avail_mwh = 0.0
    for key, entry in gens_keyed:
        hourly = ts.gen_factor.get(key)
        if hourly is None:
            continue
        if float(entry.get("pmin", 0.0)) > 0.0:
            logger.info(
                "profiles: %s (%s %d) has pmin=%.1f MW; set to 0 so the "
                "availability profile can curtail it",
                entry["name"],
                key[0],
                key[1],
                entry["pmin"],
            )
            entry["pmin"] = 0.0
        puid = len(profiles) + 1
        factor = grid.aggregate(hourly)
        factor_by_uid[puid] = factor
        avail_mwh += float(np.sum(factor * durations)) * float(entry["capacity"])
        profiles.append(
            {
                "uid": puid,
                "name": f"{entry['name']}_profile",
                "owner_kind": "generator",
                "owner": entry["uid"],
                "profile": "profile",
            }
        )
    if profiles:
        system["capacity_profile_array"] = profiles
        write_block_schedule(
            input_dir / "GeneratorProfile",
            "profile",
            factor_by_uid,
            grid,
            with_scenario=True,
        )

    batteries = _build_batteries(net, ts.storage_efficiency) if ts.storage else []
    if batteries:
        system["battery_array"] = batteries

    return {
        "batteries": len(batteries),
        "hours": grid.n_hours,
        "blocks": grid.n_blocks,
        "stages": grid.n_stages,
        "block_hours": grid.block_hours if isinstance(grid, TimeGrid) else None,
        "blocks_per_stage_max": max(c for _f, c in grid.stage_ranges()),
        "profiled_loads": len(lmax_by_uid),
        "constant_loads": len(system["demand_array"]) - len(lmax_by_uid),
        "load_energy_mwh": float(np.sum(total_load * durations)),
        "profiled_load_energy_mwh": float(np.sum(profiled_mw * durations)),
        "peak_load_mw": float(total_load.max(initial=0.0)),
        "min_load_mw": float(total_load.min()) if total_load.size else 0.0,
        "profiled_generators": len(profiles),
        "available_profiled_energy_mwh": avail_mwh,
        "thermal_capacity_mw": float(
            sum(e["capacity"] for k, e in gens_keyed if k not in ts.gen_factor)
        ),
    }


def _sddp_options(
    has_state: bool, max_iterations: int | None, convergence_tol: float
) -> dict[str, Any]:
    """``sddp_options`` for a time-series case; logs why the iteration count."""
    if max_iterations is None:
        max_iterations = 100 if has_state else 0
        if has_state:
            logger.info(
                "sddp: storage present (state carried between monthly / "
                "staged phases) -> normal SDDP, max_iterations=%d, "
                "convergence_tol=%g",
                max_iterations,
                convergence_tol,
            )
        else:
            logger.info(
                "sddp: no storage / reservoirs -> phases are independent; "
                "max_iterations=0 (forward pass only, same result as "
                "--solver monolithic, SDDP output layout)"
            )
    elif not has_state and max_iterations > 0:
        logger.warning(
            "sddp: no storage in the case; %d iterations will not improve "
            "on the forward pass (phases are independent)",
            max_iterations,
        )
    return {"max_iterations": max_iterations, "convergence_tol": convergence_tol}


def convert(  # pylint: disable=too-many-arguments
    output_path: Path | None = None,
    net: pp.pandapowerNet | None = None,
    name: str | None = None,
    solver_type: str = DEFAULT_SOLVER,
    *,
    scale_objective: float = 1000.0,
    demand_fail_cost: float = 1000.0,
    use_kirchhoff: bool = True,
    use_single_bus: bool = False,
    timeseries: TimeSeriesData | None = None,
    output_format: str = "csv",
    sddp_max_iterations: int | None = None,
    sddp_convergence_tol: float = 0.01,
    losses: "LineLossOptions | None" = None,
) -> dict[str, Any]:
    """Load a pandapower network and write the gtopt JSON file.

    Parameters
    ----------
    output_path:
        Destination for the JSON file.  When *None* the file is written as
        ``<name>.json`` in the same directory as this module.
    net:
        A pre-loaded ``pandapowerNet`` object.  When *None* ``case_ieee30()``
        is loaded automatically.
    name:
        System name to embed in the JSON.  Defaults to ``"ieee30b"``.
    solver_type:
        Planning method to embed in ``options.method``.  One of
        ``SUPPORTED_SOLVERS`` (``"monolithic"``, ``"sddp"`` or ``"cascade"``).  When
        ``"cascade"``, a 3-level default ``cascade_options`` block is
        also emitted.  Defaults to ``"cascade"``.
    timeseries:
        Optional hourly profiles + time grid.  When given, the case gets
        one block per ``grid.block_hours`` hours, stages of
        ``grid.stage_hours`` (one phase per stage), and per-block
        ``Demand/lmax`` / ``GeneratorProfile/profile`` Parquet schedules
        written to ``<output dir>/<output stem>/`` (referenced through
        ``options.input_directory``).  The energy summary is stored in
        ``timeseries.summary``.
    output_format:
        gtopt solution output format (``options.output_format``).
    sddp_max_iterations / sddp_convergence_tol:
        ``options.sddp_options`` emitted for ``solver_type="sddp"`` in
        time-series mode.  ``sddp_max_iterations=None`` picks 100 (as
        plp2gtopt) when the case has state (batteries from
        ``net.storage``) and 0 — forward pass only, the same result as
        ``monolithic`` with one phase per stage — when it has none.
    losses:
        Line-loss model options (:class:`LineLossOptions`; ``None`` =
        the historical lossless per-unit output).

    Returns
    -------
    dict[str, Any]
        The generated gtopt planning dictionary.
    """
    if solver_type not in SUPPORTED_SOLVERS:
        raise ValueError(
            f"unsupported solver_type {solver_type!r}; "
            f"expected one of {SUPPORTED_SOLVERS}"
        )
    if net is None:
        net = pn.case_ieee30()
    if name is None:
        name = "ieee30b"
    options: dict[str, Any] = {
        "method": solver_type,
        "annual_discount_rate": 0.0,
        "output_format": output_format,
        "output_compression": "uncompressed",
        "model_options": {
            "use_single_bus": use_single_bus,
            "demand_fail_cost": demand_fail_cost,
            "scale_objective": scale_objective,
            "use_kirchhoff": use_kirchhoff,
        },
    }
    if losses is None:
        losses = LineLossOptions()
    options["model_options"].update(losses.model_options())
    if solver_type == "cascade":
        options["cascade_options"] = _default_cascade_options()

    data: dict[str, Any] = {
        "options": options,
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1}],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": 1, "active": 1}
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": {
            "name": name,
            "bus_array": _build_buses(net),
            "generator_array": _build_generators(net),
            "demand_array": _build_demands(net),
            "line_array": _build_lines(net, _BASE_MVA, losses.physical_units),
        },
    }

    if output_path is None:
        output_path = Path(__file__).parent / f"{name}.json"

    if timeseries is not None:
        input_dir = output_path.parent / output_path.stem
        options["input_directory"] = output_path.stem
        options["input_format"] = "parquet"
        timeseries.summary.update(_apply_timeseries(data, net, timeseries, input_dir))
        if solver_type == "sddp":
            options["sddp_options"] = _sddp_options(
                timeseries.summary["batteries"] > 0,
                sddp_max_iterations,
                sddp_convergence_tol,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"Written: {output_path}")
    return data
