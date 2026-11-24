# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Line impedances in mixed source conventions reduce to the right X/V².

gtopt's KVL uses ``x = X / V²`` (θ_a − θ_b = x · f, f in MW; ``V``
defaults to 1).  Converters write that in different conventions:

* plexos2gtopt: X in p.u. on 100 MVA with ``voltage = 10`` (√S_base);
* plp2gtopt: X in Ω with ``voltage`` in kV;
* sddp2gtopt: X % on 100 MVA turned into Ω with ``voltage`` in kV;
* bare cases: no ``voltage`` (V = 1).

The reducer must combine the lines on ``X/V²`` (not on the raw ``X``)
and emit equivalents whose ``X/V²`` is the series/parallel combination.
Every line below has ``x = X/V²`` = 1e-3 or 2e-3 [rad/MW].
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from gtopt_reduce_network._io import Case, _index_buses
from gtopt_reduce_network._reduce import ReduceConfig, reduce_case
from gtopt_reduce_network._topology import build_line_graph

#: plexos: 0.1 p.u. @ V=10; plp: 48.4 Ω @ 220 kV; sddp: 10 % @ 230 kV
#: (= 0.1 · 230² / 100 Ω); bare: 1e-3 @ V=1.  All x = 1e-3.
_LINES: list[dict[str, Any]] = [
    {"uid": 1, "name": "plexos_b1_b4", "bus_a": "b1", "bus_b": "b4",
     "reactance": 0.1, "voltage": 10.0},
    {"uid": 2, "name": "plp_b1_b4", "bus_a": "b1", "bus_b": "b4",
     "reactance": 48.4, "voltage": 220.0},
    {"uid": 3, "name": "sddp_b1_b3", "bus_a": "b1", "bus_b": "b3",
     "reactance": 0.1 * 230.0**2 / 100.0, "voltage": 230.0},
    {"uid": 4, "name": "bare_b3_b4", "bus_a": "b3", "bus_b": "b4",
     "reactance": 1e-3},
    {"uid": 5, "name": "plexos_b2_b4", "bus_a": "b2", "bus_b": "b4",
     "reactance": 0.2, "voltage": 10.0},
    {"uid": 6, "name": "plp_b1_b2", "bus_a": "b1", "bus_b": "b2",
     "reactance": 96.8, "voltage": 220.0, "tmax": 15.0},
]  # fmt: skip


def _raw_case() -> dict[str, Any]:
    lines = []
    for spec in _LINES:
        ln = {k: v for k, v in spec.items() if k != "tmax"}
        cap = spec.get("tmax", 1000.0)
        ln["tmax_ab"] = cap
        ln["tmax_ba"] = cap
        lines.append(ln)
    return {
        "options": {
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": True,
                "demand_fail_cost": 1000,
                "scale_objective": 1,
            },
        },
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1}],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": 1, "active": 1}
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": {
            "name": "mixed_units",
            "bus_array": [
                {"uid": 1, "name": "b1"},
                {"uid": 2, "name": "b2"},
                {"uid": 3, "name": "b3"},
                {"uid": 4, "name": "b4"},
            ],
            "generator_array": [
                {
                    "uid": 1,
                    "name": "g1",
                    "bus": "b1",
                    "pmin": 0,
                    "pmax": 300,
                    "gcost": 20,
                    "capacity": 300,
                },
                {
                    "uid": 2,
                    "name": "g2",
                    "bus": "b2",
                    "pmin": 0,
                    "pmax": 300,
                    "gcost": 35,
                    "capacity": 300,
                },
            ],  # fmt: skip
            "demand_array": [
                {"uid": 1, "name": "d4", "bus": "b4", "lmax": [[250.0]]},
            ],
            "line_array": lines,
        },
    }


def _case(raw: dict[str, Any]) -> Case:
    case = Case(raw=copy.deepcopy(raw))
    _index_buses(case)
    return case


def _reduce(raw: dict[str, Any]) -> dict[str, Any]:
    """Reduce to the 3 injection buses: b3 (no injection) is eliminated,
    the b1–b4 corridor (2 parallels + the b1–b3–b4 series path) merges."""
    result = reduce_case(_case(raw), ReduceConfig(target_buses=3))
    return result.case.raw


def _x(line: dict[str, Any]) -> float:
    return float(line["reactance"]) / float(line.get("voltage", 1.0)) ** 2


def _bus_uid(case: dict[str, Any], ref: Any) -> int:
    for bus in case["system"]["bus_array"]:
        if ref in (bus["uid"], bus["name"]):
            return int(bus["uid"])
    raise KeyError(ref)


def test_line_graph_is_per_unit_on_100_mva() -> None:
    """The graph holds X·100/V² for every convention (all 0.1 / 0.2 p.u.)."""
    graph = build_line_graph(_case(_raw_case()))
    x_pu = dict(zip(graph.line_uids, graph.line_x))
    assert x_pu == pytest.approx({1: 0.1, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.2, 6: 0.2})


def test_unresolvable_voltage_reads_v_equal_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A schedule-valued voltage (parquet stem) or V <= 0 falls back to
    gtopt's default V = 1; the stem case is warned about."""
    raw = _raw_case()
    raw["system"]["line_array"][0]["voltage"] = "voltage"  # parquet stem
    raw["system"]["line_array"][4]["voltage"] = 0.0
    with caplog.at_level("WARNING"):
        graph = build_line_graph(_case(raw))
    x_pu = dict(zip(graph.line_uids, graph.line_x))
    assert x_pu[1] == pytest.approx(0.1 * 100.0)
    assert x_pu[5] == pytest.approx(0.2 * 100.0)
    assert "non-scalar voltage" in caplog.text


def test_series_parallel_equivalent_x_over_v2() -> None:
    reduced = _reduce(_raw_case())
    lines = reduced["system"]["line_array"]
    assert len(reduced["system"]["bus_array"]) == 3
    assert len(lines) == 3
    by_ends: dict[frozenset[int], dict[str, Any]] = {
        frozenset((_bus_uid(reduced, ln["bus_a"]), _bus_uid(reduced, ln["bus_b"]))): ln
        for ln in lines
    }
    assert all(ln["voltage"] == 10.0 for ln in lines)
    # b1–b4: 1e-3 ∥ 1e-3 ∥ (1e-3 + 1e-3) = 4e-4.
    corridor = [ln for ends, ln in by_ends.items() if len(ends) == 2]
    xs = sorted(_x(ln) for ln in corridor)
    assert xs == pytest.approx([4e-4, 2e-3, 2e-3])


def _dc_flows(case: dict[str, Any], inj: dict[str, float]) -> dict[int, float]:
    """DC power flow f = (θ_a − θ_b) / (X/V²) with bus b1 as reference."""
    buses = [int(b["uid"]) for b in case["system"]["bus_array"]]
    names = {b["name"]: int(b["uid"]) for b in case["system"]["bus_array"]}
    idx = {u: i for i, u in enumerate(buses)}
    n = len(buses)
    bmat = np.zeros((n, n))
    ends = {}
    for ln in case["system"]["line_array"]:
        a = idx[_bus_uid(case, ln["bus_a"])]
        b = idx[_bus_uid(case, ln["bus_b"])]
        y = 1.0 / _x(ln)
        bmat[a, a] += y
        bmat[b, b] += y
        bmat[a, b] -= y
        bmat[b, a] -= y
        ends[int(ln["uid"])] = (a, b, y)
    p = np.zeros(n)
    for name, mw in inj.items():
        p[idx[names[name]]] = mw
    theta = np.zeros(n)
    ref = idx[names["b1"]]
    keep = [i for i in range(n) if i != ref]
    theta[keep] = np.linalg.solve(bmat[np.ix_(keep, keep)], p[keep])
    return {u: (theta[a] - theta[b]) * y for u, (a, b, y) in ends.items()}


def _corridor_flows(case: dict[str, Any], flows: dict[int, float]) -> dict:
    """Flow per (sorted bus-name pair) leaving the lower name, b3 folded."""
    out: dict[tuple[str, str], float] = {}
    names = {int(b["uid"]): b["name"] for b in case["system"]["bus_array"]}
    for ln in case["system"]["line_array"]:
        a = names[_bus_uid(case, ln["bus_a"])]
        b = names[_bus_uid(case, ln["bus_b"])]
        if "b3" in (a, b):
            if a != "b1":  # count the series path once, on its b1 leg
                continue
            b = "b4"
        key = tuple(sorted((a, b)))
        sign = 1.0 if (a, b) == key else -1.0
        out[key] = out.get(key, 0.0) + sign * flows[int(ln["uid"])]
    return out


def test_reduced_dc_flows_match_full() -> None:
    """The reduction is exact: corridor flows of the DC power flow match."""
    raw = _raw_case()
    reduced = _reduce(raw)
    inj = {"b1": 200.0, "b2": 50.0, "b4": -250.0}
    full = _corridor_flows(raw, _dc_flows(raw, inj))
    red = _corridor_flows(reduced, _dc_flows(reduced, inj))
    assert red == pytest.approx(full, rel=1e-9)
    # Sanity: the flows really depend on X (b1–b2 carries flow).
    assert abs(full[("b1", "b2")]) > 1.0


# ─────────────────────────── gtopt LP equivalence ───────────────────────


def _gtopt_bin() -> str | None:
    explicit = os.environ.get("GTOPT_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("gtopt")


def _solve(gtopt: str, case: dict[str, Any], work: Path) -> tuple[float, dict]:
    work.mkdir()
    path = work / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    subprocess.run(
        [gtopt, str(path), "-d", str(work / "out")],
        check=True,
        cwd=work,
        capture_output=True,
        timeout=120,
    )
    sol = pd.read_csv(work / "out" / "solution.csv")
    (flow_file,) = sorted((work / "out" / "Line").glob("flow_sol*.csv"))
    flows_df = pd.read_csv(flow_file)
    flows = {int(u): float(v) for u, v in zip(flows_df["uid"], flows_df["value"])}
    return float(sol["obj_value"].iloc[0]), flows


@pytest.mark.integration
def test_gtopt_lp_reduced_equals_full(tmp_path: Path) -> None:
    """gtopt solves full and reduced cases to the same objective and
    corridor flows; the b1–b2 limit (15 MW) binds so X/V² matters."""
    gtopt = _gtopt_bin()
    if gtopt is None:
        pytest.skip("GTOPT_BIN not set and `gtopt` not on PATH")
    raw = _raw_case()
    reduced = _reduce(raw)
    obj_full, f_full = _solve(gtopt, raw, tmp_path / "full")
    obj_red, f_red = _solve(gtopt, reduced, tmp_path / "red")
    assert obj_red == pytest.approx(obj_full, rel=1e-7)
    full = _corridor_flows(raw, f_full)
    red = _corridor_flows(reduced, f_red)
    assert red == pytest.approx(full, rel=1e-6, abs=1e-6)
    assert abs(full[("b1", "b2")]) == pytest.approx(15.0)
