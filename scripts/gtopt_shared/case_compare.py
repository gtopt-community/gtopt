# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Compare two gtopt plannings by what they model, not how they spell it.

Exporters round-trip a case through another dialect and read it back
(``gtopt -> gtopt2plp -> plp2gtopt``); the result renames and renumbers
elements (``d_<bus>``, ``j_<plant>``, central numbers, ...).  This module
reduces a planning to name-independent quantities, each an array over
``(scenario, block)``:

* ``demand@<bus>``: total ``lmax`` on each bus;
* ``gen.<field>@<generator>`` for ``pmax`` (effective: ``min(pmax,
  profile x capacity)`` when a profile applies), ``pmin`` and ``gcost``;
* ``line.<field>@<bus_a>-<bus_b>`` for ``tmax_ab``, ``tmax_ba`` and the
  susceptance ``V^2 / X``;
* ``inflow@<generator or junction>``: total positive inflow into a hydro
  plant's junction;
* ``reservoir.<field>@<plant>`` for ``emin``, ``emax``, ``eini``;
* ``battery.<field>@<bus>`` for ``emax``, ``pmax_charge``,
  ``pmax_discharge``;

and :func:`compare_cases` reports the keys whose arrays differ.  Buses
are keyed by uid (both dialects keep bus uids).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from gtopt_shared.case_reader import GtoptCase


def _bus(case: GtoptCase, ref: Any) -> int | None:
    bus = case.ref_index(case.elements("bus_array")).get(ref)
    return None if bus is None else int(bus["uid"])


def _plant_names(case: GtoptCase) -> dict[int, str]:
    """Junction id -> a stable plant name (its turbine's generator name)."""
    jref = case.ref_index(case.elements("junction_array"))
    out: dict[int, str] = {}
    for t in case.elements("turbine_array"):
        j = jref.get(t.get("junction_a"))
        if j is not None and t.get("generator") is not None:
            gens = case.ref_index(case.elements("generator_array"))
            gen = gens.get(t["generator"])
            out[id(j)] = str((gen or {}).get("name", t["generator"]))
    for j in case.elements("junction_array"):
        out.setdefault(id(j), str(j.get("name")).removeprefix("j_"))
    return out


def summarize(case: GtoptCase) -> dict[str, np.ndarray]:
    """Name-independent quantities of a planning (see module docstring)."""
    out: dict[str, np.ndarray] = {}
    g = case.grid

    def add(key: str, arr: np.ndarray) -> None:
        out[key] = out.get(key, 0.0) + arr if key in out else arr

    for d in case.elements("demand_array"):
        bus = _bus(case, d.get("bus"))
        add(
            f"demand@{bus}", np.nan_to_num(case.field("Demand", d, "lmax", default=0.0))
        )

    profiles: dict[Any, dict[str, Any]] = {}
    for p in case.elements("generator_profile_array"):
        profiles[p.get("generator")] = p
    for p in case.elements("capacity_profile_array"):
        if p.get("owner_kind", "generator") == "generator":
            profiles[p.get("owner")] = p
    for gen in case.elements("generator_array"):
        name = gen.get("name")
        cap = gen.get("capacity")
        pmax = case.field("Generator", gen, "pmax", default=np.nan)
        if isinstance(cap, (int, float)):
            pmax = np.where(np.isnan(pmax), float(cap), pmax)
        prof = profiles.get(name, profiles.get(gen.get("uid")))
        if prof is not None:
            pr = np.nan_to_num(
                case.field("GeneratorProfile", prof, "profile", default=1.0), nan=1.0
            )
            capv = (
                float(cap) if isinstance(cap, (int, float)) else float(np.nanmax(pmax))
            )
            pmax = np.minimum(pmax, pr * capv)
        out[f"gen.pmax@{name}"] = pmax
        out[f"gen.pmin@{name}"] = np.nan_to_num(
            case.field("Generator", gen, "pmin", default=0.0)
        )
        out[f"gen.gcost@{name}"] = np.nan_to_num(
            case.field("Generator", gen, "gcost", default=0.0)
        )

    for ln in case.elements("line_array"):
        a, b = _bus(case, ln.get("bus_a")), _bus(case, ln.get("bus_b"))
        key = f"{min(a or 0, b or 0)}-{max(a or 0, b or 0)}"
        fwd = (a or 0) <= (b or 0)
        tab = case.field("Line", ln, "tmax_ab", default=np.inf)
        tba = case.field("Line", ln, "tmax_ba", default=np.inf)
        tab = np.where(tab >= 9000, np.inf, tab)
        tba = np.where(tba >= 9000, np.inf, tba)
        add(f"line.tmax_ab@{key}", tab if fwd else tba)
        add(f"line.tmax_ba@{key}", tba if fwd else tab)
        x = case.field("Line", ln, "reactance", default=np.nan)
        v = case.field("Line", ln, "voltage", default=1.0)
        add(f"line.susceptance@{key}", v**2 / x)

    names = _plant_names(case)
    jref = case.ref_index(case.elements("junction_array"))
    for f in case.elements("flow_array"):
        if int(f.get("direction", 1) or 1) < 0:
            continue
        j = jref.get(f.get("junction"))
        if j is not None:
            key = names[id(j)]
        else:
            turb = [
                t
                for t in case.elements("turbine_array")
                if t.get("flow") in (f.get("name"), f.get("uid"))
            ]
            key = str(turb[0].get("generator") if turb else f.get("name"))
        add(
            f"inflow@{key}",
            np.nan_to_num(case.field("Flow", f, "discharge", default=0.0)),
        )
    for r in case.elements("reservoir_array"):
        j = jref.get(r.get("junction"))
        key = str(names.get(id(j), r.get("name")) if j is not None else r.get("name"))
        cap = r.get("capacity")
        emax = case.field("Reservoir", r, "emax", default=np.nan)
        if isinstance(cap, (int, float)):
            emax = np.where(np.isnan(emax), float(cap), emax)
        out[f"reservoir.emin@{key}"] = np.nan_to_num(
            case.field("Reservoir", r, "emin", default=0.0)
        )
        out[f"reservoir.emax@{key}"] = emax
        eini = r.get("eini")
        out[f"reservoir.eini@{key}"] = np.full(
            (g.n_scenarios, g.n_blocks), np.nan if eini is None else float(eini)
        )
    for bat in case.elements("battery_array"):
        bus = _bus(case, bat.get("bus"))
        for fld in ("emax", "pmax_charge", "pmax_discharge"):
            add(
                f"battery.{fld}@{bus}",
                np.nan_to_num(case.field("Battery", bat, fld, default=0.0)),
            )
    return out


def compare_cases(
    a: GtoptCase, b: GtoptCase, *, rtol: float = 1e-6, atol: float = 1e-6
) -> list[str]:
    """Differences between the summaries of two plannings (empty = same)."""
    sa, sb = summarize(a), summarize(b)
    diffs: list[str] = []
    for key in sorted(set(sa) | set(sb)):
        if key not in sa or key not in sb:
            diffs.append(f"{key}: only in {'first' if key in sa else 'second'}")
            continue
        x, y = np.broadcast_arrays(sa[key], sb[key])
        if x.shape != y.shape or not np.allclose(
            x, y, rtol=rtol, atol=atol, equal_nan=True
        ):
            bad = ~np.isclose(x, y, rtol=rtol, atol=atol, equal_nan=True)
            idx = tuple(int(i) for i in np.argwhere(bad)[0])
            diffs.append(f"{key}: {x[idx]!r} != {y[idx]!r} at (scenario, block) {idx}")
    return diffs
