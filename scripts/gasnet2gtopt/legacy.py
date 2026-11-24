# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Legacy emitter: the gas network as generic gtopt elements.

The 2026-11 prototype lowered the gas network onto ``DecisionVariable`` columns and
``UserConstraint`` rows.  It is kept behind ``--legacy`` so the native
elements can be compared against it on the same cases: both outputs are
the same LP (same columns, rows and objective), only the carrier differs.
"""

from __future__ import annotations

from typing import Any

from gasnet2gtopt.common import (
    BuildOptions,
    check_radial,
    gas_properties,
    operating_point,
    pipe_physics,
    planning_skeleton,
    plant_pressure_settings,
    power_system,
    resolve_profile,
    source_rate,
    station_model,
    system_name,
)
from gasnet2gtopt.physics import (
    electric_kappa,
    linepack_coeff,
    plant_capability_rows,
    saldarriaga_coeffs,
    tangent_points,
)


def fmt(x: float) -> str:
    """Compact decimal for UC expressions."""
    s = f"{x:.10f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def dv(name: str) -> str:
    """PAMPL reference to a decision variable."""
    return f"decision_variable('{name}').value"


class Expr:
    """Tiny linear-expression builder emitting gtopt UC syntax."""

    def __init__(self) -> None:
        self.terms: list[tuple[float, str]] = []

    def add(self, coef: float, ref: str) -> Expr:
        """Appends ``coef * ref`` (zero coefficients are dropped)."""
        if coef != 0.0:
            self.terms.append((coef, ref))
        return self

    def __str__(self) -> str:
        out = []
        for i, (c, ref) in enumerate(self.terms):
            sign = "-" if c < 0 else "+"
            mag = abs(c)
            body = ref if mag == 1.0 else f"{fmt(mag)} * {ref}"
            if i == 0:
                out.append(("-" if c < 0 else "") + body)
            else:
                out.append(f" {sign} {body}")
        return "".join(out) if out else "0"


class _Builder:
    """Accumulates DVs, UCs and node balances for the legacy output."""

    def __init__(self, nodes: dict[str, dict[str, Any]], nblocks: int) -> None:
        self.dvs: list[dict[str, Any]] = []
        self.ucs: list[dict[str, Any]] = []
        self.bal = {n: Expr() for n in nodes}
        self.rhs = {n: [0.0] * nblocks for n in nodes}
        self.extra_demands: list[dict[str, Any]] = []

    def add_dv(
        self, name: str, lb: float, ub: float, cost: float | None = None, **kw: Any
    ) -> None:
        """Adds one decision variable (``cost_type: power``)."""
        d: dict[str, Any] = {
            "uid": len(self.dvs) + 1,
            "name": name,
            "lower_bound": lb,
            "upper_bound": ub,
        }
        if cost is not None:
            d["cost"] = cost
            d["cost_type"] = "power"
        d.update(kw)
        self.dvs.append(d)

    def add_uc(self, name: str, expr: str, rhs: list[float] | None = None) -> None:
        """Adds one user constraint (``constraint_type: power``)."""
        u: dict[str, Any] = {
            "uid": len(self.ucs) + 1,
            "name": name,
            "expression": expr,
            "constraint_type": "power",
        }
        if rhs is not None:
            u["rhs"] = [rhs]
        self.ucs.append(u)


def _pipes(
    b: _Builder,
    gas: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    opts: BuildOptions,
    meta: dict[str, Any],
) -> None:
    for p in gas["gas_pipeline_array"]:
        name, na_, nb_ = p["name"], p["node_a"], p["node_b"]
        kw, exp_s = pipe_physics(p, gas, nodes)
        nseg = opts.segments or p.get("segments", 8)
        fmax = p["fmax"]
        has_lp = opts.linepack and p.get("linepack", False)
        pm: dict[str, Any] = {
            "K": kw,
            "exp_s": exp_s,
            "segments": nseg,
            "fmax": fmax,
            "node_a": na_,
            "node_b": nb_,
            "linepack": has_lp,
        }
        if has_lp:
            fin, fout = f"fin_{name}", f"fout_{name}"
            b.add_dv(fin, 0.0, fmax)
            b.add_dv(fout, 0.0, fmax)
            b.bal[na_].add(-1.0, dv(fin))
            b.bal[nb_].add(1.0, dv(fout))
            flow_terms = [(0.5, dv(fin)), (0.5, dv(fout))]
            c = p.get("linepack_coeff") or linepack_coeff(
                p["length"],
                p["diameter"],
                p.get("z", gas_properties(gas)[2]),
                p.get("temperature", gas_properties(gas)[1]),
            )
            na, nb = nodes[na_], nodes[nb_]
            pr = p.get("linepack_p_ref") or 0.25 * (
                na["pmin"] + na["pmax"] + nb["pmin"] + nb["pmax"]
            )
            alpha = c / (4.0 * pr)
            beta = c * pr / 2.0
            lp0 = alpha * (na["p_init"] ** 2 + nb["p_init"] ** 2) + beta
            lpn = f"lp_{name}"
            b.add_dv(
                lpn,
                0.0,
                1e6,
                scope="block",
                link=True,
                block_state=True,
                initial_value=lp0,
            )
            e = Expr().add(1.0, dv(lpn)).add(-alpha, dv(f"pi_{na_}"))
            e.add(-alpha, dv(f"pi_{nb_}"))
            b.add_uc(f"linepack_def_{name}", f"{e} = {fmt(beta)}")
            b.add_uc(
                f"linepack_bal_{name}",
                f"{dv(fin)} * block.duration - {dv(fout)} * block.duration"
                f" - {dv(lpn)} + prev({dv(lpn)}) = 0",
            )
            if gas.get("linepack_cyclic", True):
                b.add_uc(
                    f"linepack_end_{name}",
                    f"{dv(lpn)} >= {fmt(lp0)}, for(block = {opts.nblocks})",
                )
            pm.update({"c": c, "p_ref": pr, "lp0": lp0, "alpha": alpha, "beta": beta})
        else:
            f = f"f_{name}"
            b.add_dv(f, 0.0, fmax)
            b.bal[na_].add(-1.0, dv(f))
            b.bal[nb_].add(1.0, dv(f))
            flow_terms = [(1.0, dv(f))]
        layout = p.get("tangent_layout", "uniform")
        tpts = tangent_points(kw, fmax, nseg, layout, nodes[na_]["pmax"])
        pm["tangent_layout"] = layout
        pm["tangent_points"] = tpts
        for k, t_k in enumerate(tpts, start=1):
            e = Expr().add(1.0, dv(f"pi_{na_}")).add(-exp_s, dv(f"pi_{nb_}"))
            for coef, ref in flow_terms:
                e.add(-2.0 * kw * t_k * coef, ref)
            b.add_uc(f"weymouth_{name}_{k}", f"{e} >= {fmt(-kw * t_k * t_k)}")
        meta["pipes"][name] = pm


def _compressors(
    b: _Builder,
    gas: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    opts: BuildOptions,
    meta: dict[str, Any],
) -> None:
    for cs in gas.get("compressor_station_array", []):
        name, ni, no = cs["name"], cs["node_in"], cs["node_out"]
        fmax, cr = cs["fmax"], cs["max_ratio"]
        sm = station_model(cs, opts)
        pi_in, pi_out = dv(f"pi_{ni}"), dv(f"pi_{no}")
        fc = f"fc_{name}"
        cm: dict[str, Any] = {
            "mode": sm.mode,
            "drive": sm.drive,
            "max_ratio": cr,
            "fuel_fraction": sm.gamma_ff,
            "fuel_model": sm.model,
            "gamma": sm.sgamma,
            "alpha": sm.alpha,
        }
        b.add_dv(fc, 0.0, fmax)
        b.bal[no].add(1.0, dv(fc))
        if sm.mode == "off":
            # bypassed station: plain short pipe, no boost, no fuel
            b.bal[ni].add(-1.0, dv(fc))
            b.add_uc(f"cs_ratio_{name}", f"{pi_out} - {pi_in} <= 0")
            cm["fuel_fraction"] = 0.0
            cm["fuel_model"] = "none"
        elif sm.model == "fixed_fraction":
            if sm.drive != "gas":
                raise ValueError(f"{name}: fixed_fraction needs drive='gas'")
            b.bal[ni].add(-(1.0 + sm.gamma_ff), dv(fc))  # fuel drawn at suction
            b.add_uc(f"cs_ratio_{name}", f"{pi_out} - {fmt(cr * cr)} * {pi_in} <= 0")
        elif sm.model == "saldarriaga":
            psi0, betas, p_in0 = operating_point(cs, nodes[ni])
            planes = [
                saldarriaga_coeffs(sm.sgamma, sm.alpha, psi0, bb, p_in0) for bb in betas
            ]
            lc = f"lc_{name}"
            b.add_dv(lc, 0.0, fmax)
            b.bal[ni].add(-1.0, dv(fc))
            if sm.drive == "gas":
                b.bal[ni].add(-1.0, dv(lc))
            elif sm.drive == "electric":
                kappa = electric_kappa(
                    cs.get("driver_efficiency", 0.33), cs.get("motor_efficiency", 0.95)
                )
                dname = f"{name}_EL"
                b.extra_demands.append(
                    {
                        "name": dname,
                        "bus": cs["bus"],
                        "lmax": cs.get("rating_mw", 5.0),
                        "fcost": 0.0,
                    }
                )
                b.add_uc(
                    f"cs_motor_{name}",
                    f"demand('{dname}').load - {fmt(kappa)} * {dv(lc)} >= 0",
                )
                cm.update({"kappa": kappa, "bus": cs["bus"], "demand": dname})
            else:
                raise ValueError(f"{name}: unknown drive {sm.drive!r}")
            b.add_uc(f"cs_ratio_{name}", f"{pi_out} - {fmt(cr * cr)} * {pi_in} <= 0")
            if not cs.get("allow_bypass", False):
                b.add_uc(f"cs_noexpand_{name}", f"{pi_out} - {pi_in} >= 0")
            for k, (cps, cpi, rr) in enumerate(planes):
                e = Expr().add(1.0, dv(lc)).add(-cps, dv(fc))
                e.add(-cpi, pi_out).add(cpi * rr, pi_in)
                b.add_uc(f"cs_fuel_{name}" + (f"_{k + 1}" if k else ""), f"{e} >= 0")
            cm.update(
                {
                    "op_flow": psi0,
                    "op_ratio": betas if len(betas) > 1 else betas[0],
                    "op_suction_pressure": p_in0,
                    "c_psi": planes[0][0],
                    "c_pi": planes[0][1],
                    "r0": planes[0][2],
                    "planes": planes,
                }
            )
        else:
            raise ValueError(f"{name}: unknown fuel_model {sm.model!r}")
        meta["compressors"][name] = cm


def build_legacy(
    desc: dict[str, Any], opts: BuildOptions
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lowers a gasnet description to DecisionVariable / UserConstraint."""
    gas = desc["gas_network"]
    check_radial(gas)
    prof = desc.get("profiles", {})
    nodes = {n["name"]: n for n in gas["gas_node_array"]}
    meta: dict[str, Any] = {
        "season": opts.season,
        "compressor": opts.compressor,
        "linepack": opts.linepack,
        "pipes": {},
        "compressors": {},
        "nodes": {},
        "booster": opts.booster,
        "derate": opts.derate,
        "plants": {},
    }
    b = _Builder(nodes, opts.nblocks)

    # GasNode: squared pressure pi = p^2 with the pressure tie-break.
    tie = gas.get("pressure_tiebreak", 1e-4)
    for n in nodes.values():
        b.add_dv(
            f"pi_{n['name']}", n["pmin"] ** 2, n["pmax"] ** 2, -tie if tie else None
        )
        meta["nodes"][n["name"]] = {"pmin": n["pmin"], "pmax": n["pmax"]}

    for s in gas.get("gas_source_array", []):
        rate, cap = source_rate(s, prof, opts)
        sref = dv(f"src_{s['name']}")
        b.add_dv(f"src_{s['name']}", 0.0, rate, s["cost"])
        b.bal[s["node"]].add(1.0, sref)
        if cap is not None:
            b.add_uc(f"src_cap_{s['name']}", f"{sref} <= 0", rhs=cap)
        if s.get("volume") is not None:
            acc = f"vol_{s['name']}"
            b.add_dv(
                acc,
                0.0,
                float(s["volume"]),
                scope="block",
                link=True,
                block_state=True,
                initial_value=0.0,
            )
            b.add_uc(
                f"vol_bal_{s['name']}",
                f"{dv(acc)} - prev({dv(acc)}) - {sref} * block.duration = 0",
            )

    for t in gas.get("gas_transfer_array", []):
        tn = f"tr_{t['name']}"
        b.add_dv(tn, 0.0, t["fmax"], t.get("cost"))
        b.bal[t["from_node"]].add(-1.0, dv(tn))
        b.bal[t["to_node"]].add(1.0, dv(tn))

    for d in gas.get("gas_demand_array", []):
        load = resolve_profile(d["load"], prof, opts.nblocks, opts.season)
        b.add_dv(f"ens_{d['name']}", 0.0, max(load), d["fail_cost"])
        b.bal[d["node"]].add(1.0, dv(f"ens_{d['name']}"))
        b.rhs[d["node"]] = [x + y for x, y in zip(b.rhs[d["node"]], load)]
        meta.setdefault("demands", {})[d["name"]] = load

    gen_pmax = {  # a profile-valued pmax uses the unit's capacity
        g["name"]: g["pmax"] if isinstance(g["pmax"], (int, float)) else g["capacity"]
        for g in desc["power_system"]["generator_array"]
    }
    for g in gas.get("generator_gas_link_array", []):
        gname = g["generator"]
        gref = f"generator('{gname}').generation"
        b.bal[g["gas_node"]].add(-g["heat_rate"], gref)
        settings = plant_pressure_settings(g, opts)
        if settings is None:
            continue
        p_min, band, p_nom = settings
        rows = plant_capability_rows(
            gen_pmax[gname], p_min, band, p_nom, g.get("derate_min_output")
        )
        pi_ref = dv(f"pi_{g['gas_node']}")
        for i, (slope, icpt) in enumerate(rows):
            tag = "pmin" if i == 0 else "derate"
            b.add_uc(
                f"plant_{tag}_{gname}",
                f"{gref} - {fmt(slope)} * {pi_ref} <= {fmt(icpt)}",
            )
        meta["plants"][gname] = {
            "gas_node": g["gas_node"],
            "min_pressure": p_min,
            "band": band,
            "p_nom": p_nom,
            "rows": rows,
        }

    _pipes(b, gas, nodes, opts, meta)
    _compressors(b, gas, nodes, opts, meta)

    for u in desc.get("user_constraints", []):
        b.add_uc(u["name"], u["expression"])

    for n in nodes:
        b.add_uc(f"gas_balance_{n}", f"{b.bal[n]} = 0", rhs=b.rhs[n])

    planning = planning_skeleton(opts.nblocks)
    planning["system"] = {
        "name": system_name(desc, opts),
        **power_system(desc, opts, b.extra_demands),
        "decision_variable_array": b.dvs,
        "user_constraint_array": b.ucs,
    }
    return planning, meta
