"""Builder: PowerSimulations reserve benchmarks (``c_sys5_uc[_re]`` + reserves).

Unlike the other variants this one does not read the 5-bus CSV bundle:
the reserve systems are assembled in PowerSystemCaseBuilder's Julia code
(``build_c_sys5_uc`` / ``build_c_sys5_uc_re`` with ``add_reserves``), and
the reserve requirement series are ``rand(24)`` draws frozen in PSCB's
serialized system.  ``tools/sienna_solve_golden.jl`` therefore dumps the
data the model actually used into the golden JSON (``system_data``, all in
natural units) and this builder converts that block, so the Python side
needs no Julia.

Mapping (PowerSimulations 0.38 formulations used by the driver):

* ``ThermalStandard`` + ``ThermalBasicUnitCommitment`` → Generator
  (``pmin`` / ``pmax`` / ``gcost``) + Commitment (``startup_cost``,
  ``shutdown_cost``, ``noload_cost``, ``initial_status``).  The basic UC
  formulation has no ramp or minimum up/down rows, so none are emitted
  (the PSY ``time_limits`` / ``ramp_limits`` are ignored on both sides).
* ``PowerLoad`` → Demand with the per-hour ``lmax`` series.
* ``RenewableDispatch`` (``RenewableFullDispatch``) → Generator with the
  per-hour ``pmax`` series and its linear cost.
* ``InterruptiblePowerLoad`` (``PowerLoadInterruption``) → Demand whose
  ``fcost`` is the load's value ($/MWh): PSI credits ``-c·p`` for the
  served MW, gtopt charges ``c·(lmax − p)``, the same LP up to the
  constant ``c·Σ lmax`` (:func:`objective_offset`).  PSI's fixed
  ``-fixed·u`` term on the load's on/off binary has no gtopt counterpart;
  it is a constant whenever ``u`` is free (see :func:`objective_offset`).
* ``VariableReserve{ReserveUp|ReserveDown}`` (``RangeReserve``) → one
  ReserveZone with the hard per-hour requirement (``urreq`` / ``drreq``,
  no shortage cost: the driver uses no reserve slack) and one
  ReserveProvision per contributing device, capped at
  ``max_output_fraction · pmax`` (and at ``max_participation_factor ·
  requirement`` when that factor is below 1), with PSI's proportional
  reserve cost ``DEFAULT_RESERVE_COST / base_power`` per p.u. (1e-4 $/MW at
  a 100 MVA base) as ``urcost`` / ``drcost``.
* ``ReserveDemandCurve{ReserveUp}`` (``StepwiseCostReserve``, ORDC) →
  one ReserveZone per curve step (``ordc="stacked"``): requirement = the
  step width, shortage cost = the step price, one provision per
  contributing device and step.  gtopt's priced requirement column
  (``0 ≤ rcol ≤ req``, cost ``-c``) is PSI's step variable ``δ_k`` with
  benefit ``-slope_k``; gtopt adds ``+c·req`` as an objective constant,
  so the two objectives differ by ``Σ_t Σ_k slope_k · width_k · Δt``
  (:func:`objective_offset`).  ``ordc="omit"`` drops the ORDC (to compare
  against a golden whose template left it out).

All provisions of a generator are ``shared_headroom``: PSI stacks every
service of a device in one ``p + Σ r_up ≤ pmax·u`` /
``p − Σ r_dn ≥ pmin·u`` pair, as gtopt does with a shared headroom row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sienna2gtopt._common import make_simulation

ORDC_MODES = ("auto", "stacked", "omit")

# gtopt has no reserve slack here: PSI's RangeReserve requirement is hard
# (the driver builds the ServiceModels without slacks).  The main loads
# are firm in PSI's CopperPlate balance too; a large curtailment cost
# keeps them so in gtopt.
DEFAULT_DEMAND_FAIL_COST = 1.0e5


def _linear_cost(cost: Mapping[str, Any], base_power: float, what: str) -> float:
    """$/MWh slope of a dumped PSY ``CostCurve`` (linear + VOM)."""
    if abs(float(cost.get("quadratic", 0.0))) > 0.0:
        raise ValueError(f"{what}: quadratic cost curves are not representable")
    units = str(cost.get("power_units", "NATURAL_UNITS"))
    slope = float(cost.get("linear", 0.0)) + float(cost.get("vom", 0.0))
    if "NATURAL_UNITS" in units:
        return slope
    if "SYSTEM_BASE" in units:
        return slope / base_power
    raise ValueError(f"{what}: unsupported cost power_units {units!r}")


def _series(
    timeseries: Mapping[str, Mapping[str, Sequence[float]]],
    device_type: str,
    name: str,
    hours: int,
) -> list[float]:
    """Absolute per-hour MW series of one device (loads come out negative)."""
    try:
        values = timeseries[device_type][name]
    except KeyError as exc:
        raise ValueError(f"no {device_type} time series for {name!r}") from exc
    if len(values) < hours:
        raise ValueError(f"{device_type} {name!r}: {len(values)} steps < {hours}")
    return [abs(float(v)) for v in values[:hours]]


def _resolve_ordc_mode(mode: str, service: Mapping[str, Any]) -> str:
    if mode not in ORDC_MODES:
        raise ValueError(f"ordc mode must be one of {ORDC_MODES}, got {mode!r}")
    if mode == "auto":
        return "stacked" if service.get("modelled", True) else "omit"
    return mode


def ordc_steps(service: Mapping[str, Any]) -> list[tuple[float, float]]:
    """``(width_mw, price_per_mwh)`` of every priced ORDC step."""
    curve = service.get("ordc")
    if not curve:
        raise ValueError("ReserveDemandCurve without a dumped 'ordc' curve")
    if "NATURAL_UNITS" not in str(curve.get("power_units", "NATURAL_UNITS")):
        raise ValueError("ORDC curve must be in natural units")
    points = [float(x) for x in curve["breakpoints_mw"]]
    slopes = [float(y) for y in curve["slopes_per_mwh"]]
    if len(points) != len(slopes) + 1:
        raise ValueError("ORDC curve needs one more breakpoint than slopes")
    steps: list[tuple[float, float]] = []
    for k, price in enumerate(slopes):
        width = points[k + 1] - points[k]
        # A non-positive step adds no benefit: PSI never serves it.
        if width > 0.0 and price > 0.0:
            steps.append((width, price))
    return steps


def _is_ordc(service: Mapping[str, Any]) -> bool:
    return "ReserveDemandCurve" in str(service.get("type", ""))


def build_reserves(
    golden: Mapping[str, Any],
    *,
    ordc: str = "auto",
    demand_fail_cost: float = DEFAULT_DEMAND_FAIL_COST,
) -> dict[str, Any]:
    """gtopt planning JSON for a ``sienna_solve_golden.jl`` reserve golden.

    Parameters
    ----------
    golden
        The golden payload (needs ``system_data``, ``horizon_hours`` and
        ``reserve_requirement_mw``).
    ordc
        ``"auto"`` (stack the ORDC when the golden modelled it, else omit),
        ``"stacked"`` or ``"omit"``.
    demand_fail_cost
        Curtailment cost of the firm loads [$/MWh].
    """
    data = golden.get("system_data")
    if not data:
        raise ValueError("golden has no 'system_data' (regenerate it)")
    hours = int(golden.get("horizon_hours") or 0)
    if hours <= 0:
        raise ValueError("golden has no positive 'horizon_hours'")
    base = float(data.get("base_power", 100.0))
    timeseries = data.get("timeseries_mw", {})
    requirements = golden.get("reserve_requirement_mw", {})

    buses = sorted(
        {
            int(d["bus"])
            for group in ("thermal", "renewable", "load")
            for d in data.get(group, {}).values()
        }
        | {int(d["bus"]) for d in data.get("interruptible_load", {}).values()}
    )
    bus_array = [{"uid": b, "name": f"bus{b}"} for b in buses]

    generator_array: list[dict[str, Any]] = []
    commitment_array: list[dict[str, Any]] = []
    gen_pmax: dict[str, float] = {}
    for name in sorted(data.get("thermal", {})):
        unit = data["thermal"][name]
        if not unit.get("available", True):
            continue
        pmax = float(unit["pmax_mw"])
        gen_pmax[name] = pmax
        generator_array.append(
            {
                "uid": len(generator_array) + 1,
                "name": name,
                "bus": int(unit["bus"]),
                # Generator.pmin is gtopt's ALWAYS-on floor; PSI's pmin only
                # binds while committed, so it goes on Commitment.pmin.
                "pmin": 0.0,
                "pmax": pmax,
                "capacity": pmax,
                "gcost": _linear_cost(unit["variable_cost"], base, name),
            }
        )
        commitment_array.append(
            {
                "uid": len(commitment_array) + 1,
                "name": f"{name}_uc",
                "generator": name,
                "pmin": float(unit["pmin_mw"]),
                "startup_cost": float(unit.get("start_up_cost") or 0.0),
                "shutdown_cost": float(unit.get("shut_down_cost") or 0.0),
                "noload_cost": float(unit.get("fixed_cost") or 0.0)
                + float(unit["variable_cost"].get("constant", 0.0)),
                "initial_status": 1.0 if unit.get("status") else 0.0,
            }
        )
    for name in sorted(data.get("renewable", {})):
        unit = data["renewable"][name]
        pmax_series = _series(timeseries, "RenewableDispatch", name, hours)
        gen_pmax[name] = max(pmax_series, default=0.0)
        generator_array.append(
            {
                "uid": len(generator_array) + 1,
                "name": name,
                "bus": int(unit["bus"]),
                "pmin": 0.0,
                "pmax": [pmax_series],
                "capacity": float(unit.get("rating_mw") or gen_pmax[name]),
                "gcost": _linear_cost(unit["variable_cost"], base, name),
            }
        )

    demand_array: list[dict[str, Any]] = []
    for name in sorted(data.get("load", {})):
        demand_array.append(
            {
                "uid": len(demand_array) + 1,
                "name": name,
                "bus": int(data["load"][name]["bus"]),
                "lmax": [_series(timeseries, "PowerLoad", name, hours)],
            }
        )
    for name in sorted(data.get("interruptible_load", {})):
        load = data["interruptible_load"][name]
        demand_array.append(
            {
                "uid": len(demand_array) + 1,
                "name": name,
                "bus": int(load["bus"]),
                "lmax": [_series(timeseries, "InterruptiblePowerLoad", name, hours)],
                "fcost": _linear_cost(load["variable_cost"], base, name),
            }
        )

    zones, provisions = reserve_elements(
        data.get("services", {}), requirements, gen_pmax, hours, ordc=ordc
    )

    system: dict[str, Any] = {
        "name": f"Sienna_{golden.get('slug', 'reserves')}",
        "bus_array": bus_array,
        "generator_array": generator_array,
        "demand_array": demand_array,
        "commitment_array": commitment_array,
    }
    if zones:
        system["reserve_zone_array"] = zones
        system["reserve_provision_array"] = provisions

    simulation = make_simulation(hours)
    simulation["stage_array"][0]["chronological"] = True
    return {
        "options": {
            "model_options": {
                "use_single_bus": True,
                "use_kirchhoff": False,
                "demand_fail_cost": float(demand_fail_cost),
                "scale_objective": 1.0,
            }
        },
        "simulation": simulation,
        "system": system,
    }


def reserve_elements(
    services: Mapping[str, Mapping[str, Any]],
    requirements: Mapping[str, Sequence[float]],
    gen_pmax: Mapping[str, float],
    hours: int,
    *,
    ordc: str = "auto",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """ReserveZone / ReserveProvision arrays of the dumped PSY services."""
    zones: list[dict[str, Any]] = []
    provisions: list[dict[str, Any]] = []

    def add_provisions(
        zone_uid: int,
        label: str,
        devices: Sequence[str],
        side: str,
        cap: Any,
        cost: float,
    ) -> None:
        for dev in devices:
            if dev not in gen_pmax:
                continue
            entry: dict[str, Any] = {
                "uid": len(provisions) + 1,
                "name": f"{dev}_{label}",
                "generator": dev,
                "reserve_zones": [zone_uid],
                "shared_headroom": True,
                f"{side}max": cap(dev),
                f"{side}_provision_factor": 1.0,
            }
            if cost > 0.0:
                entry[f"{side}cost"] = cost
            provisions.append(entry)

    for name in sorted(services):
        svc = services[name]
        if not svc.get("available", True):
            continue
        devices = sorted(svc.get("contributing_devices", []))
        side = "dr" if svc.get("direction") == "down" else "ur"
        frac = float(svc.get("max_output_fraction") or 1.0)
        if _is_ordc(svc):
            if _resolve_ordc_mode(ordc, svc) == "omit":
                continue
            for k, (width, price) in enumerate(ordc_steps(svc), start=1):
                zone_uid = len(zones) + 1
                label = f"{name}_s{k}"
                zones.append(
                    {
                        "uid": zone_uid,
                        "name": label,
                        f"{side}req": width,
                        f"{side}cost": price,
                    }
                )
                add_provisions(
                    zone_uid,
                    label,
                    devices,
                    side,
                    lambda d, frac=frac: frac * gen_pmax[d],
                    0.0,
                )
            continue
        if name not in requirements:
            raise ValueError(f"no requirement series for reserve {name!r}")
        req = [float(v) for v in requirements[name][:hours]]
        if len(req) < hours:
            raise ValueError(f"reserve {name!r}: {len(req)} steps < {hours}")
        zone_uid = len(zones) + 1
        zones.append({"uid": zone_uid, "name": name, f"{side}req": [req]})
        mpf = float(svc.get("max_participation_factor") or 1.0)

        def cap(
            dev: str, req: list[float] = req, mpf: float = mpf, frac: float = frac
        ) -> Any:
            top = frac * gen_pmax[dev]
            if mpf >= 1.0:
                return top
            return [[min(top, mpf * r) for r in req]]

        add_provisions(
            zone_uid,
            name,
            devices,
            side,
            cap,
            float(svc.get("reserve_cost_per_mw") or 0.0),
        )
    return zones, provisions


def objective_offset(golden: Mapping[str, Any], *, ordc: str = "auto") -> float:
    """gtopt objective minus PowerSimulations' objective, a data constant.

    ``Σ_t Σ_k slope_k · width_k`` of every stacked ORDC step (gtopt adds
    the met-requirement valuation ``+c·req``; PSI subtracts the served
    benefit), plus ``c · Σ lmax`` of every interruptible load (gtopt
    charges unserved MWh, PSI credits served MWh), plus PSI's
    ``fixed · Σ_t u_t`` on-variable credit of those loads, read from the
    golden's ``load_on`` (``u`` carries no other cost, so PSI keeps it at
    1 and the credit is a constant of the solution).  One-hour steps.
    """
    data = golden["system_data"]
    hours = int(golden["horizon_hours"])
    base = float(data.get("base_power", 100.0))
    offset = 0.0
    for svc in data.get("services", {}).values():
        if not svc.get("available", True) or not _is_ordc(svc):
            continue
        if _resolve_ordc_mode(ordc, svc) == "omit":
            continue
        offset += hours * sum(w * p for w, p in ordc_steps(svc))
    timeseries = data.get("timeseries_mw", {})
    load_on = golden.get("load_on", {})
    for name, load in data.get("interruptible_load", {}).items():
        price = _linear_cost(load["variable_cost"], base, name)
        offset += price * sum(
            _series(timeseries, "InterruptiblePowerLoad", name, hours)
        )
        on_hours = sum(round(float(u)) for u in load_on.get(name, [])[:hours])
        offset += float(load.get("fixed_cost") or 0.0) * on_hours
    return offset


__all__ = [
    "DEFAULT_DEMAND_FAIL_COST",
    "ORDC_MODES",
    "build_reserves",
    "objective_offset",
    "ordc_steps",
    "reserve_elements",
]
