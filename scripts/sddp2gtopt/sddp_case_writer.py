"""Write a :class:`~sddp2gtopt.sddp_case.SddpBlockCase` as a gtopt planning.

Output layout (``input_format = parquet``, long ``[scenario,] stage,
[block,] uid, value`` tables)::

    <out>/<name>.json
    <out>/Generator/{pmax,pmin,pmin_fcost,gcost}.parquet
    <out>/GeneratorProfile/profile.parquet      # scenario × stage × block
    <out>/Demand/lmax.parquet
    <out>/Flow/discharge.parquet                # scenario × stage
    <out>/Line/{active,tmax_ab,tmax_ba}.parquet
    <out>/Reservoir/{emax,soft_emin}.parquet
    <out>/Waterway/{fmin,fmax}.parquet
    <out>/Turbine/{production_factor,capacity}.parquet

Model mapping (one PSR element → gtopt elements):

* **Thermal** → ``Generator`` (``gcost`` = ``CEsp1 × fuel cost + CVaria``,
  ``pmax`` = ``GerMax`` × maintenance × (1 − ``Teif``)(1 − ``Ih``) × (1 −
  individual reserve), ``pmin`` = ``GerMin`` for must-run units, soft
  floors from ``gmme``/``rgme`` via ``pmin_fcost``).
* **Hydro** → ``Junction`` (+ ``Reservoir`` when ``VMax > VMin``), a
  ``Turbine`` to the ``VAA`` plant (a ``Waterway`` for zero-power intakes),
  a spill ``Waterway`` to the ``TAA`` plant (or a costed junction drain),
  an inflow ``Flow`` from the plant's gauging station, an irrigation
  withdrawal ``Waterway`` and — when a minimum total outflow applies — an
  outlet ``Waterway`` with a soft ``fmin`` feeding an outlet junction.
* **Renewable** → ``Generator`` + ``GeneratorProfile`` (per-scenario
  block profile).
* **Circuit** → ``Line`` (R/X converted from % on 100 MVA to Ω at the
  higher end voltage; PWL losses when the study enables them).
* **Demand** → one ``Demand`` per bus.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gtopt_shared.json_planning import build_json_item, write_planning

from .sddp_case import HM3_PER_M3S_H, Grid, SddpBlockCase

logger = logging.getLogger(__name__)

_FLOW_CONVERSION_RATE = HM3_PER_M3S_H
_UNLIMITED_FLOW = 99999.0


def _ref(name: str) -> str:
    return name.strip().replace(" ", "_")


class _TableSink:
    """Collect long-format rows per ``(class, field)`` and write Parquet."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, list[Any]]] = {}

    def add_tb(self, cls: str, fld: str, uid: int, grid: Grid) -> str:
        tab = self.rows.setdefault(
            (cls, fld), {"stage": [], "block": [], "uid": [], "value": []}
        )
        for s, row in enumerate(grid):
            for b, v in enumerate(row):
                tab["stage"].append(s + 1)
                tab["block"].append(s * len(row) + b + 1)  # global block uid
                tab["uid"].append(uid)
                tab["value"].append(float(v))
        return fld

    def add_t(self, cls: str, fld: str, uid: int, values: list[float]) -> str:
        tab = self.rows.setdefault((cls, fld), {"stage": [], "uid": [], "value": []})
        for s, v in enumerate(values):
            tab["stage"].append(s + 1)
            tab["uid"].append(uid)
            tab["value"].append(float(v))
        return fld

    def add_stb(
        self, cls: str, fld: str, uid: int, cube: list[Grid], scenarios: list[int]
    ) -> str:
        tab = self.rows.setdefault(
            (cls, fld),
            {"scenario": [], "stage": [], "block": [], "uid": [], "value": []},
        )
        for scn, grid in zip(scenarios, cube):
            for s, row in enumerate(grid):
                for b, v in enumerate(row):
                    tab["scenario"].append(scn)
                    tab["stage"].append(s + 1)
                    tab["block"].append(s * len(row) + b + 1)
                    tab["uid"].append(uid)
                    tab["value"].append(float(v))
        return fld

    def write(self, root: Path, compression: str = "zstd") -> list[Path]:
        import pandas as pd  # pylint: disable=import-outside-toplevel

        written: list[Path] = []
        for (cls, fld), cols in sorted(self.rows.items()):
            df = pd.DataFrame(cols)
            for c in df.columns:
                if c != "value":
                    df[c] = df[c].astype("int32")
            # Activation flags (``active``) are int32 IntBool schedules.
            df["value"] = df["value"].astype("int32" if fld == "active" else "float64")
            out = root / cls / f"{fld}.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(out, index=False, compression=compression)
            written.append(out)
        return written


def _const(grid: Grid) -> float | None:
    flat = [v for row in grid for v in row]
    if flat and all(abs(v - flat[0]) < 1e-9 for v in flat):
        return flat[0]
    return None


def _const_list(values: list[float]) -> float | None:
    if values and all(abs(v - values[0]) < 1e-9 for v in values):
        return values[0]
    return None


def _tb_field(
    sink: _TableSink, cls: str, fld: str, uid: int, grid: Grid
) -> float | str:
    c = _const(grid)
    return c if c is not None else sink.add_tb(cls, fld, uid, grid)


def _t_as_tb(values: list[float], nb: int) -> Grid:
    return [[v] * nb for v in values]


def _resolve_downstream(
    code: int | None, bypass: dict[int, int | None], active: set[int]
) -> int | None:
    """Follow a downstream code past plants not modelled (never in service
    in the horizon): their water continues along ``bypass`` (their ``VAA``,
    else ``TAA``) until a modelled plant; ``None`` = leaves the system."""
    seen: set[int] = set()
    while code and code not in active and code not in seen:
        seen.add(code)
        code = bypass.get(code)
    return code if code in active else None


def _bus_names(case: SddpBlockCase) -> dict[int, str]:
    out: dict[int, str] = {}
    used: set[str] = set()
    for num, raw in sorted(case.buses.items()):
        name = _ref(raw) or f"b{num}"
        if name in used:
            name = f"{name}_{num}"
        used.add(name)
        out[num] = name
    return out


def build_sddp_planning(
    case: SddpBlockCase,
    *,
    name: str,
    method: str = "sddp",
) -> tuple[dict[str, Any], _TableSink]:
    """Assemble the gtopt planning dict + the Parquet tables to write."""
    sink = _TableSink()
    ctl = case.control
    nb = case.num_blocks
    ns = len(case.stages)
    bus_name = _bus_names(case)

    # ── simulation ─────────────────────────────────────────────────────────
    block_array: list[dict[str, Any]] = []
    stage_array: list[dict[str, Any]] = []
    for s, st in enumerate(case.stages):
        for b, dur in enumerate(st.durations):
            block_array.append({"uid": s * nb + b + 1, "duration": dur})
        stage_array.append(
            {
                "uid": s + 1,
                "name": f"{st.year}-{st.month:02d}",
                "first_block": s * nb,
                "count_block": nb,
                "active": 1,
            }
        )
    nscn = len(case.scenarios)
    prob = 1.0 / nscn
    scenario_array = [
        {
            "uid": scn,
            "name": f"hydro_{case.hydro_years[i]}" if case.hydro_years else f"s{scn}",
            "probability_factor": prob,
        }
        for i, scn in enumerate(case.scenarios)
    ]
    simulation: dict[str, Any] = {
        "annual_discount_rate": ctl.discount_rate,
        "block_array": block_array,
        "stage_array": stage_array,
        "scenario_array": scenario_array,
    }
    if ns > 1:
        simulation["phase_array"] = [
            {"uid": s + 1, "first_stage": s, "count_stage": 1} for s in range(ns)
        ]
        simulation["scene_array"] = [
            {"uid": i + 1, "first_scenario": i, "count_scenario": 1}
            for i in range(nscn)
        ]
        simulation["aperture_array"] = [
            {"uid": i + 1, "source_scenario": scn, "probability_factor": prob}
            for i, scn in enumerate(case.scenarios)
        ]
        if nscn > 2:
            for k, phase in enumerate(simulation["phase_array"]):
                phase["apertures"] = _stratified_apertures(case, k)

    # ── network ────────────────────────────────────────────────────────────
    bus_array = []
    for num in sorted(case.buses):
        entry: dict[str, Any] = {"uid": num, "name": bus_name[num]}
        kv = case.bus_kv.get(num, 0.0)
        if kv > 0:
            entry["voltage"] = kv
        bus_array.append(entry)

    line_array: list[dict[str, Any]] = []
    for uid, ln in enumerate(case.lines, start=1):
        tmax = [t if t > 0 else _UNLIMITED_FLOW for t in ln.tmax]
        tgrid = _t_as_tb(tmax, nb)
        item: dict[str, Any] = {
            "uid": uid,
            "name": f"{ln.name}_{ln.code}",
            "bus_a": bus_name[ln.from_bus],
            "bus_b": bus_name[ln.to_bus],
            "voltage": ln.voltage,
            "reactance": ln.reactance,
        }
        if ctl.use_losses and ln.resistance > 0:
            item["resistance"] = ln.resistance
        item["tmax_ab"] = _tb_field(sink, "Line", "tmax_ab", uid, tgrid)
        item["tmax_ba"] = _tb_field(sink, "Line", "tmax_ba", uid, tgrid)
        if not all(ln.active):
            item["active"] = sink.add_t(
                "Line", "active", uid, [1.0 if a else 0.0 for a in ln.active]
            )
        line_array.append(item)

    # ── generators ─────────────────────────────────────────────────────────
    gen_array: list[dict[str, Any]] = []
    used_names: set[str] = set()

    def gname(raw: str, code: int, kind: str) -> str:
        nm = _ref(raw) or f"{kind}{code}"
        if nm in used_names:
            nm = f"{nm}_{kind}{code}"
        used_names.add(nm)
        return nm

    guid = 1
    deficit = ctl.deficit_cost
    for t in case.thermals:
        pmin = t.pmin
        if t.pmin_soft is not None:
            # Soft floor: max(must-run pmin, soft floor), penalised at the
            # deficit cost (PSR "automatic" penalty) so a dry series stays
            # feasible.
            pmin = [
                [max(a, b) for a, b in zip(r1, r2)]
                for r1, r2 in zip(t.pmin, t.pmin_soft)
            ]
        gcost_c = _const_list(t.gcost)
        item = {
            "uid": guid,
            "name": gname(t.name, t.code, "t"),
            "type": "thermal",
            "bus": bus_name[t.bus],
            "capacity": t.capacity,
            "pmax": _tb_field(sink, "Generator", "pmax", guid, t.pmax),
            "pmin": _tb_field(sink, "Generator", "pmin", guid, pmin),
            "gcost": (
                gcost_c
                if gcost_c is not None
                else sink.add_tb("Generator", "gcost", guid, _t_as_tb(t.gcost, nb))
            ),
        }
        if t.pmin_soft is not None:
            item["pmin_fcost"] = deficit
        gen_array.append(item)
        guid += 1

    hydro_gen: dict[int, str] = {}
    for h in case.hydros:
        if h.bus is None or h.bus not in bus_name or max(h.pot) <= 0:
            continue
        nm = gname(h.name, h.code, "h")
        item = {
            "uid": guid,
            "name": nm,
            "type": "hydro",
            "bus": bus_name[h.bus],
            "capacity": max(h.pot),
            "pmax": _tb_field(sink, "Generator", "pmax", guid, h.pmax),
            "gcost": 0.0,
        }
        if h.pmin_soft is not None:
            item["pmin"] = _tb_field(sink, "Generator", "pmin", guid, h.pmin_soft)
            item["pmin_fcost"] = deficit
        gen_array.append(item)
        hydro_gen[h.code] = nm
        guid += 1

    profile_array: list[dict[str, Any]] = []
    tech = {
        0: "renewable",
        1: "wind",
        2: "solar",
        3: "biomass",
        4: "small_hydro",
        5: "csp",
    }
    for r in case.renewables:
        if r.bus not in bus_name:
            continue
        nm = gname(r.name, r.code, "r")
        gen_array.append(
            {
                "uid": guid,
                "name": nm,
                "type": tech.get(r.tech, "renewable"),
                "bus": bus_name[r.bus],
                "capacity": max(r.capacity),
                "pmax": _tb_field(
                    sink, "Generator", "pmax", guid, _t_as_tb(r.capacity, nb)
                ),
                "gcost": r.om_cost,
            }
        )
        cube = case.profiles.get(r.station)
        if cube is not None:
            profile_array.append(
                {
                    "uid": len(profile_array) + 1,
                    "name": f"pf_{nm}",
                    "generator": nm,
                    "profile": sink.add_stb(
                        "GeneratorProfile",
                        "profile",
                        len(profile_array) + 1,
                        cube,
                        case.scenarios,
                    ),
                }
            )
        guid += 1

    battery_array = [
        {
            "uid": i + 1,
            "name": _ref(bt.name),
            "bus": bus_name[bt.bus],
            "input_efficiency": bt.eff_charge,
            "output_efficiency": bt.eff_discharge,
            "emin": bt.emin,
            "emax": bt.emax,
            "eini": bt.eini,
            "capacity": bt.emax,
            "pmax_charge": bt.pmax,
            "pmax_discharge": bt.pmax,
        }
        for i, bt in enumerate(case.batteries)
        if bt.bus in bus_name
    ]

    # ── demand ─────────────────────────────────────────────────────────────
    demand_array = []
    for duid, bus in enumerate(sorted(b for b in case.demands if b in bus_name), 1):
        demand_array.append(
            {
                "uid": duid,
                "name": f"d_{bus_name[bus]}",
                "bus": bus_name[bus],
                "lmax": _tb_field(sink, "Demand", "lmax", duid, case.demands[bus]),
            }
        )

    # ── hydro network ──────────────────────────────────────────────────────
    water = _build_water_network(case, sink, hydro_gen)

    system: dict[str, Any] = {
        "name": name,
        "bus_array": bus_array,
        "generator_array": gen_array,
        "demand_array": demand_array,
        "line_array": line_array,
    }
    if profile_array:
        system["generator_profile_array"] = profile_array
    if battery_array:
        system["battery_array"] = battery_array
    system.update({k: v for k, v in water.items() if v})

    model_options: dict[str, Any] = {
        "use_single_bus": not line_array,
        "use_kirchhoff": bool(line_array),
        "demand_fail_cost": deficit,
        "scale_objective": 1000.0,
    }
    if ctl.use_losses and line_array:
        model_options["line_losses_mode"] = "piecewise"
        model_options["loss_segments"] = 3
    options: dict[str, Any] = {
        "method": method if ns > 1 else "monolithic",
        "input_directory": ".",
        "input_format": "parquet",
        "output_directory": "results",
        "output_format": "parquet",
        "model_options": model_options,
    }
    planning = {"options": options, "simulation": simulation, "system": system}
    return planning, sink


def _bit_reversed_order(n: int) -> list[int]:
    """0..n-1 in van der Corput order: every prefix spreads over the range."""
    bits = max(1, (n - 1).bit_length())
    keys = sorted(range(1 << bits), key=lambda i: int(f"{i:0{bits}b}"[::-1], 2))
    return [i for i in keys if i < n]


def _stratified_apertures(case: SddpBlockCase, stage: int) -> list[int]:
    """Aperture uids of one phase ordered so any prefix is stratified.

    Scenarios are ranked by their total natural inflow in ``stage`` and
    visited in bit-reversed rank order (median first, then the quartiles,
    …), so ``sddp_options.num_apertures = N`` keeps N hydrologies spread
    across the dry-to-wet range instead of the first N series.
    """
    n = len(case.scenarios)
    totals = [
        sum(series[i][stage] for series in case.inflows.values()) for i in range(n)
    ]
    ranked = sorted(range(n), key=lambda i: (totals[i], i))
    return [ranked[r] + 1 for r in _bit_reversed_order(n)]


def _build_water_network(
    case: SddpBlockCase, sink: _TableSink, hydro_gen: dict[int, str]
) -> dict[str, list[dict[str, Any]]]:
    nb = case.num_blocks
    by_code = {h.code: h for h in case.hydros}
    active = set(by_code)
    spill_cost = case.control.spill_penalty * 1000.0 * HM3_PER_M3S_H
    jn: dict[int, str] = {h.code: f"j_{_ref(h.name)}" for h in case.hydros}
    junctions: list[dict[str, Any]] = []
    reservoirs: list[dict[str, Any]] = []
    turbines: list[dict[str, Any]] = []
    waterways: list[dict[str, Any]] = []
    flows: list[dict[str, Any]] = []
    station_owner: dict[int, int] = {}
    for h in case.hydros:
        if h.station and h.station in case.inflows:
            station_owner.setdefault(h.station, h.code)

    def new_junction(nm: str, drain: bool) -> None:
        item: dict[str, Any] = {"uid": len(junctions) + 1, "name": nm}
        if drain:
            item["drain"] = True
            if spill_cost > 0:
                item["drain_cost"] = spill_cost
        junctions.append(item)

    def new_waterway(**fields: Any) -> int:
        uid = len(waterways) + 1
        waterways.append(build_json_item(uid=uid, **fields))
        return uid

    for h in case.hydros:
        j = jn[h.code]
        down = _resolve_downstream(h.downstream, case.hydro_bypass, active)
        spill = _resolve_downstream(h.spill_to, case.hydro_bypass, active)
        if spill == h.code:
            spill = None
        if down == h.code:
            down = None
        use_outlet = any(v > 0 for v in h.min_outflow)
        src = f"jo_{_ref(h.name)}" if use_outlet else j
        new_junction(j, drain=(spill is None and not use_outlet))
        if use_outlet:
            new_junction(src, drain=spill is None)
            wuid = len(waterways) + 1
            new_waterway(
                name=f"out_{_ref(h.name)}",
                junction_a=j,
                junction_b=src,
                fmin=sink.add_tb("Waterway", "fmin", wuid, _t_as_tb(h.min_outflow, nb)),
                fmin_fcost=h.min_outflow_cost or None,
            )

        if h.has_storage:
            ruid = len(reservoirs) + 1
            emax_grid = _t_as_tb(h.emax, nb)
            res: dict[str, Any] = {
                "uid": ruid,
                "name": f"r_{_ref(h.name)}",
                "junction": j,
                "capacity": h.vmax,
                "emin": h.vmin,
                "emax": _tb_field(sink, "Reservoir", "emax", ruid, emax_grid),
                "eini": h.eini,
                "flow_conversion_rate": _FLOW_CONVERSION_RATE,
                "use_state_variable": True,
                # Bound the net extraction column (free otherwise).
                "fmin": -_UNLIMITED_FLOW,
                "fmax": _UNLIMITED_FLOW,
            }
            if any(v > h.vmin for v in h.soft_emin) and h.soft_emin_cost > 0:
                res["soft_emin"] = _tb_field(
                    sink,
                    "Reservoir",
                    "soft_emin",
                    ruid,
                    _t_as_tb([min(v, h.vmax) for v in h.soft_emin], nb),
                )
                res["soft_emin_cost"] = h.soft_emin_cost
            reservoirs.append(res)

        gen = hydro_gen.get(h.code)
        fp = h.fp
        qcap = [q if q > 0 else 0.0 for q in h.qmax]
        if gen is not None and max(fp) > 0:
            tuid = len(turbines) + 1
            pf_c = _const_list(fp)
            q_c = _const_list(qcap)
            turbines.append(
                build_json_item(
                    uid=tuid,
                    name=f"tb_{_ref(h.name)}",
                    junction_a=src,
                    junction_b=jn[down] if down else None,
                    generator=gen,
                    production_factor=(
                        pf_c
                        if pf_c is not None
                        else sink.add_t("Turbine", "production_factor", tuid, fp)
                    ),
                    capacity=(
                        (q_c if q_c > 0 else None)
                        if q_c is not None
                        else sink.add_t(
                            "Turbine",
                            "capacity",
                            tuid,
                            [q if q > 0 else _UNLIMITED_FLOW for q in qcap],
                        )
                    ),
                )
            )
        elif down is not None:
            wuid = len(waterways) + 1
            q_c = _const_list(qcap)
            new_waterway(
                name=f"q_{_ref(h.name)}",
                junction_a=src,
                junction_b=jn[down],
                fmax=(
                    (q_c if q_c > 0 else None)
                    if q_c is not None
                    else sink.add_tb(
                        "Waterway",
                        "fmax",
                        wuid,
                        _t_as_tb([q if q > 0 else _UNLIMITED_FLOW for q in qcap], nb),
                    )
                ),
            )

        if spill is not None:
            new_waterway(
                name=f"s_{_ref(h.name)}",
                junction_a=src,
                junction_b=jn[spill],
                fcost=spill_cost or None,
            )

        if any(v > 0 for v in h.irrigation):
            wuid = len(waterways) + 1
            grid = _t_as_tb(h.irrigation, nb)
            new_waterway(
                name=f"irr_{_ref(h.name)}",
                junction_a=j,
                fmin=sink.add_tb("Waterway", "fmin", wuid, grid),
                fmax=sink.add_tb("Waterway", "fmax", wuid, grid),
                fmin_fcost=h.irrigation_cost or case.control.deficit_cost * 0.0036,
            )

        own = (
            [h.station] if h.station and station_owner.get(h.station) == h.code else []
        )
        stations = [st for st in [*own, *h.extra_stations] if st in case.inflows]
        if stations:
            series = [
                [sum(vals) for vals in zip(*rows)]
                for rows in zip(*(case.inflows[st] for st in stations))
            ]  # [scenario][stage]
            cube = [_t_as_tb(row, nb) for row in series]  # [scn][stage][block]
            if any(v > 0 for g in cube for r in g for v in r):
                fuid = len(flows) + 1
                flows.append(
                    {
                        "uid": fuid,
                        "name": f"in_{_ref(h.name)}",
                        "junction": j,
                        "direction": 1,
                        "discharge": sink.add_stb(
                            "Flow", "discharge", fuid, cube, case.scenarios
                        ),
                    }
                )
    logger.info(
        "water network: %d junctions, %d reservoirs, %d turbines, %d waterways, "
        "%d inflows",
        len(junctions),
        len(reservoirs),
        len(turbines),
        len(waterways),
        len(flows),
    )
    return {
        "junction_array": junctions,
        "waterway_array": waterways,
        "reservoir_array": reservoirs,
        "turbine_array": turbines,
        "flow_array": flows,
    }


def write_sddp_case(
    case: SddpBlockCase,
    output_dir: Path,
    output_file: Path,
    *,
    name: str,
    method: str = "sddp",
    compression: str = "zstd",
) -> Path:
    """Write the planning JSON + Parquet tables; returns the JSON path."""
    planning, sink = build_sddp_planning(case, name=name, method=method)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sink.write(output_dir, compression=compression)
    write_planning(planning, output_file)
    logger.info("wrote %d parquet tables under %s", len(files), output_dir)
    return output_file
