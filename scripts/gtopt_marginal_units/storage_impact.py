# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``gtopt-marginal-units storage-impact`` — emission impact of storage.

Values the grid-side dispatch of every battery at the marginal emission
rate (MER) of its bus, following the marginal-emissions approach of the
Verra draft methodology CN0157 (*Methodology for Grid-Connected Energy
Storage Systems*, 2025):

    PE = - sum_i A_i * E_i          (project emissions impact, tCO2e)
    BE = - sum_i A^base_i * E_i     (baseline: revenue-maximising dispatch)
    ER = BE - PE                    (reduction vs the baseline)

``A_i`` is the net grid-side energy of the storage in interval ``i``
(MWh, positive when discharging, negative when charging) and ``E_i`` the
MER at its bus (tCO2e/MWh).  A negative PE means the storage reduces
system emissions.  CN0157's forecast-error discount factor is not
applied.

Inputs are a solved gtopt output directory (the "project" run, e.g. with
a carbon price), an optional baseline output directory (e.g. the same
case without a carbon price) and a ``gtopt-marginal-units`` dataset that
provides the MER per bus and block.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from gtopt_marginal_units._lp_duals import _read_long

_LOG = logging.getLogger("gtopt_marginal_units.storage_impact")

KEY = ["scenario", "stage", "block"]


@dataclass(frozen=True, slots=True)
class StorageUnit:
    """Grid connection of one storage unit."""

    uid: int
    name: str
    bus_uid: int
    gen_uid: Optional[int]  # converter generator (discharge), if any
    dem_uid: Optional[int]  # converter demand (charge), if any


def _ref_to_uid(ref: Any, by_name: dict[str, int]) -> Optional[int]:
    if ref is None:
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        if ref in by_name:
            return by_name[ref]
        if ref.startswith("uid:"):
            return int(ref.split(":", 1)[1])
        if ref.isdigit():
            return int(ref)
    return None


def storage_units(planning: dict[str, Any]) -> list[StorageUnit]:
    """Batteries and the bus / converter elements they reach the grid by."""
    system = planning.get("system", {})
    bus_by_name = {b["name"]: int(b["uid"]) for b in system.get("bus_array", [])}
    gens = {int(g["uid"]): g for g in system.get("generator_array", [])}
    dems = {int(d["uid"]): d for d in system.get("demand_array", [])}
    gen_by_name = {g["name"]: u for u, g in gens.items()}
    dem_by_name = {d["name"]: u for u, d in dems.items()}
    bat_by_name = {b["name"]: int(b["uid"]) for b in system.get("battery_array", [])}

    conv_of: dict[int, dict[str, Any]] = {}
    for conv in system.get("converter_array", []):
        b_uid = _ref_to_uid(conv.get("battery"), bat_by_name)
        if b_uid is not None:
            conv_of[b_uid] = conv

    units: list[StorageUnit] = []
    for bat in system.get("battery_array", []):
        uid = int(bat["uid"])
        conv = conv_of.get(uid)
        gen_uid = dem_uid = None
        bus_ref = bat.get("bus")
        if conv is not None:
            gen_uid = _ref_to_uid(conv.get("generator"), gen_by_name)
            dem_uid = _ref_to_uid(conv.get("demand"), dem_by_name)
            if bus_ref is None and gen_uid in gens:
                bus_ref = gens[gen_uid].get("bus")
            if bus_ref is None and dem_uid in dems:
                bus_ref = dems[dem_uid].get("bus")
        bus_uid = _ref_to_uid(bus_ref, bus_by_name)
        if bus_uid is None:
            _LOG.warning("battery %s has no bus; skipped", bat.get("name", uid))
            continue
        units.append(
            StorageUnit(uid, str(bat.get("name", uid)), bus_uid, gen_uid, dem_uid)
        )
    return units


def block_durations(planning: dict[str, Any]) -> dict[int, float]:
    """Block uid -> duration in hours."""
    blocks = planning.get("simulation", {}).get("block_array", [])
    return {int(b["uid"]): float(b.get("duration", 1.0)) for b in blocks}


def scenario_weights(planning: dict[str, Any]) -> dict[int, float]:
    """Scenario uid -> normalised probability."""
    scen = planning.get("simulation", {}).get("scenario_array", [])
    raw = {int(s["uid"]): float(s.get("probability_factor", 1.0)) for s in scen}
    total = sum(raw.values())
    return {u: (p / total if total > 0 else 0.0) for u, p in raw.items()}


def _series(output_dir: Path, stem: str, uid: Optional[int]) -> pd.DataFrame:
    """Per-(scenario, stage, block) values of one element; empty if absent."""
    if uid is None:
        return pd.DataFrame(columns=[*KEY, "mw"])
    df = _read_long(output_dir, stem, "uid", "mw")
    if df is None:
        return pd.DataFrame(columns=[*KEY, "mw"])
    df = df[df["uid"] == uid]
    return df[KEY + ["mw"]].groupby(KEY, as_index=False)["mw"].sum()


def storage_dispatch(
    output_dir: Path, planning: dict[str, Any], units: list[StorageUnit]
) -> pd.DataFrame:
    """Grid-side charge/discharge energy per storage unit and block.

    Columns: storage_uid, scenario, stage, block, discharge_mwh,
    charge_mwh, net_mwh (discharge - charge).  Blocks without dispatch
    (absent from gtopt's sparse outputs) are zero.
    """
    dur = block_durations(planning)
    frames = []
    for unit in units:
        if unit.gen_uid is not None or unit.dem_uid is not None:
            dis = _series(output_dir, "Generator/generation_sol", unit.gen_uid)
            chg = _series(output_dir, "Demand/load_sol", unit.dem_uid)
        else:
            dis = _series(output_dir, "Battery/fout_sol", unit.uid)
            chg = _series(output_dir, "Battery/finp_sol", unit.uid)
        df = pd.merge(
            dis.rename(columns={"mw": "discharge_mw"}),
            chg.rename(columns={"mw": "charge_mw"}),
            on=KEY,
            how="outer",
        ).fillna(0.0)
        if df.empty:
            continue
        hours = df["block"].astype(int).map(dur).fillna(1.0)
        df["discharge_mwh"] = df["discharge_mw"] * hours
        df["charge_mwh"] = df["charge_mw"] * hours
        df["net_mwh"] = df["discharge_mwh"] - df["charge_mwh"]
        df["storage_uid"] = unit.uid
        frames.append(
            df[["storage_uid", *KEY, "discharge_mwh", "charge_mwh", "net_mwh"]]
        )
    if not frames:
        return pd.DataFrame(
            columns=["storage_uid", *KEY, "discharge_mwh", "charge_mwh", "net_mwh"]
        )
    out = pd.concat(frames, ignore_index=True)
    for col in KEY:
        out[col] = out[col].astype(int)
    return out


def read_mer(mer_dir: Path) -> pd.DataFrame:
    """MER per (scenario, stage, block, bus_uid) in tCO2e/MWh.

    ``mer_dir`` is a ``gtopt-marginal-units`` output directory.
    """
    # pylint: disable=import-outside-toplevel
    from gtopt_marginal_units.consumer import MarginalUnitDataset

    em = MarginalUnitDataset(mer_dir).bus_emission_intensity()
    out = em[[*KEY, "bus_uid", "emission_intensity_t_per_mwh"]].copy()
    out["mer_t_per_mwh"] = out["emission_intensity_t_per_mwh"].fillna(0.0)
    for col in [*KEY, "bus_uid"]:
        out[col] = out[col].astype(int)
    return out[[*KEY, "bus_uid", "mer_t_per_mwh"]]


def emission_impact(
    dispatch: pd.DataFrame, mer: pd.DataFrame, units: list[StorageUnit]
) -> pd.DataFrame:
    """Per-interval impact ``-net_mwh * MER`` (tCO2e) for each storage unit."""
    bus_of = {u.uid: u.bus_uid for u in units}
    df = dispatch.copy()
    df["bus_uid"] = df["storage_uid"].map(bus_of).astype(int)
    df = df.merge(mer, on=[*KEY, "bus_uid"], how="left")
    missing = int(df["mer_t_per_mwh"].isna().sum())
    if missing:
        _LOG.warning("%d dispatch intervals have no MER; counted as 0", missing)
    df["mer_t_per_mwh"] = df["mer_t_per_mwh"].fillna(0.0)
    df["impact_t"] = -df["net_mwh"] * df["mer_t_per_mwh"]
    return df


def summarise(
    project: pd.DataFrame,
    baseline: Optional[pd.DataFrame],
    units: list[StorageUnit],
    weights: dict[int, float],
) -> pd.DataFrame:
    """Probability-weighted totals per storage unit (CN0157 PE, BE, ER)."""

    def _agg(df: pd.DataFrame) -> pd.DataFrame:
        w = df["scenario"].map(weights).fillna(0.0)
        g = (
            df.assign(
                d=df["discharge_mwh"] * w,
                c=df["charge_mwh"] * w,
                i=df["impact_t"] * w,
                dm=df["discharge_mwh"] * df["mer_t_per_mwh"] * w,
                cm=df["charge_mwh"] * df["mer_t_per_mwh"] * w,
            )
            .groupby("storage_uid")[["d", "c", "i", "dm", "cm"]]
            .sum()
        )
        return g

    rows = []
    proj = _agg(project)
    base = _agg(baseline) if baseline is not None else None
    for unit in units:
        tot = {k: 0.0 for k in ("d", "c", "i", "dm", "cm")}
        if unit.uid in proj.index:
            tot = {k: float(proj.loc[unit.uid, k]) for k in tot}
        dis, chg, pe = tot["d"], tot["c"], tot["i"]
        row = {
            "storage": unit.name,
            "bus_uid": unit.bus_uid,
            "discharged_mwh": dis,
            "charged_mwh": chg,
            "mer_discharge_t_per_mwh": tot["dm"] / dis if dis > 0 else 0.0,
            "mer_charge_t_per_mwh": tot["cm"] / chg if chg > 0 else 0.0,
            "PE_t": pe,
        }
        if base is not None:
            be = float(base.loc[unit.uid, "i"]) if unit.uid in base.index else 0.0
            row["BE_t"] = be
            row["ER_t"] = be - pe
        rows.append(row)
    return pd.DataFrame(rows)


def _load_planning(output_dir: Path, planning: Optional[Path]) -> dict[str, Any]:
    path = planning if planning is not None else output_dir / "planning.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; pass --planning (the case JSON) explicitly"
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of the storage-impact subcommand."""
    p = argparse.ArgumentParser(
        prog="gtopt-marginal-units storage-impact",
        description=(
            "Emission impact of storage dispatch valued at the marginal "
            "emission rate of its bus (Verra CN0157 marginal-emissions "
            "approach): PE = -sum(A*E), BE (baseline), ER = BE - PE."
        ),
    )
    p.add_argument(
        "--project-output",
        type=Path,
        required=True,
        help="gtopt output directory of the project run (e.g. with a carbon price).",
    )
    p.add_argument(
        "--baseline-output",
        type=Path,
        help="gtopt output directory of the baseline run (e.g. carbon price 0).",
    )
    p.add_argument(
        "--mer",
        type=Path,
        required=True,
        help="gtopt-marginal-units output directory with the MER per bus/block.",
    )
    p.add_argument(
        "--planning",
        type=Path,
        help="Planning JSON (default: <project-output>/planning.json).",
    )
    p.add_argument("--csv", type=Path, help="Write the per-interval table here.")
    p.add_argument("--json", type=Path, help="Write the summary as JSON here.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point of ``gtopt-marginal-units storage-impact``."""
    args = build_parser().parse_args(argv)
    planning = _load_planning(args.project_output, args.planning)
    units = storage_units(planning)
    if not units:
        _LOG.error("no batteries found in the planning")
        return 1
    mer = read_mer(args.mer)

    project = emission_impact(
        storage_dispatch(args.project_output, planning, units), mer, units
    )
    baseline = None
    if args.baseline_output is not None:
        base_plan = _load_planning(args.baseline_output, args.planning)
        baseline = emission_impact(
            storage_dispatch(args.baseline_output, base_plan, units), mer, units
        )

    summary = summarise(project, baseline, units, scenario_weights(planning))

    if args.csv is not None:
        parts = [project.assign(run="project")]
        if baseline is not None and not baseline.empty:
            parts.append(baseline.assign(run="baseline"))
        table = pd.concat([t for t in parts if not t.empty], ignore_index=True)
        table.to_csv(args.csv, index=False)
    if args.json is not None:
        args.json.write_text(
            json.dumps(summary.to_dict(orient="records"), indent=2) + "\n",
            encoding="utf-8",
        )
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(summary.round(3).to_string(index=False))
    print(
        "\nPE < 0: the storage reduces system emissions; ER > 0: reduction "
        "versus the baseline dispatch (tCO2e, probability-weighted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
