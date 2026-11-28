# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI: ``gtopt_private_eval RESULTS_DIR [...] --market cl``.

Private-investor (pre-tax, EBIT) evaluation of the projects a gtopt
expansion plan selected, under a pluggable market model.  Sub-command
``gtopt_private_eval capex-trajectory`` prints / applies capex
trajectories (local base × NREL ATB index); ``gtopt_private_eval
fuel-trajectory`` does the same for fuel costs (declared base × IEA WEO
index).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import capex_trajectory, fuel_trajectory
from .case_data import load_system
from .evaluate import EvalReport, ProjectEvaluation, evaluate
from .markets import MARKETS
from .params import Params, load_config, set_path
from .storage_value import STORAGE_TYPES


def _m(v: float | None) -> str:
    """Money in M$ with one decimal."""
    return "-" if v is None else f"{v / 1e6:,.2f}"


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{100.0 * v:.1f}%"


def _yrs(v: float | None) -> str:
    return "never" if v is None else f"{v:.1f}"


def _print_project(p: ProjectEvaluation) -> None:
    y1 = p.year1
    size = f"{p.new_mw:,.1f} MW"
    if p.new_mwh:
        size += f" / {p.new_mwh:,.1f} MWh"
    tag = " (existing)" if p.existing else ""
    print(
        f"\n  {p.kind}:{p.name}{tag} [{p.technology}] {size}  capex {_m(p.capex)} M$  "
        f"life {p.life} y  WACC {_pct(p.wacc)} (planning {_pct(p.planning_rate)})"
    )
    print(
        "    year-1 revenues (M$/yr): "
        + ", ".join(f"{k} {_m(v)}" for k, v in y1.revenues.items())
    )
    print(
        "    year-1 costs    (M$/yr): "
        + ", ".join(f"{k} {_m(v)}" for k, v in y1.costs.items())
    )
    print(
        f"    EBITDA {_m(y1.ebitda)}  depreciation {_m(y1.depreciation)}  "
        f"EBIT {_m(y1.ebit)}  EBIT margin {_pct(y1.ebit_margin)}"
    )
    cash = p.cash
    lcoe = cash.lcoe()
    print(
        f"    NPV {_m(cash.npv)} M$  IRR {_pct(cash.irr)}  payback "
        f"{_yrs(cash.payback)} y (discounted {_yrs(cash.discounted_payback)})  "
        f"LCOE {'-' if lcoe is None else f'{lcoe:,.1f}'} $/MWh  captured "
        f"{'-' if p.captured_price is None else f'{p.captured_price:,.1f}'} $/MWh"
    )
    print(
        f"    planning view: margin {_m(p.planning_margin)} - annuity "
        f"{_m(p.planning_annuity)} = profit {_m(p.planning_profit)} M$/yr;  "
        f"private: EBITDA - capex*CRF = {_m(cash.equivalent_annual)} M$/yr"
    )
    print("    bridge: " + ", ".join(f"{k} {_m(v)}" for k, v in p.bridge().items()))
    if p.risk is not None:
        rk = p.risk
        print(
            f"    risk ({rk.mode}, tail {rk.tail}): E[NPV] {_m(rk.expectation)} M$"
            f"  IRR(E) {_pct(rk.irr)}  CVaR{100 * rk.alpha:.0f}% {_m(rk.cvar)}"
            f"  trimmed(-{len(rk.trimmed_scenarios)}) {_m(rk.trimmed_mean)} M$"
        )
        print(
            "      verification: expectation "
            + ("pass" if rk.passes_expectation else "FAIL")
            + ", private CVaR "
            + ("pass" if rk.passes_cvar else "FAIL (unpaid private risk)")
        )
        print(
            "      NPV by scenario (worst first): "
            + ", ".join(f"{sc}: {_m(rk.scenarios[sc]['npv'])}" for sc in rk.order)
        )
    if p.streams is not None:
        st = p.streams
        print(
            f"    stream discounting ({st.method}): NPV {_m(st.npv)} M$  "
            f"equivalent rate {_pct(st.equivalent_rate)} "
            "(replaces the IRR: several rates)"
        )
        print(
            "      "
            + ", ".join(
                f"{k} @{_pct(e['rate'])}"
                + (f" -{100 * e['haircut']:.0f}%" if e["haircut"] else "")
                + f" {_m(e['pv'])}"
                for k, e in st.streams.items()
            )
        )
    if p.details:
        print(
            "    details: " + ", ".join(f"{k} {v:,.4g}" for k, v in p.details.items())
        )
    for label, sens in p.sensitivity.items():
        print(f"    {label}: " + ", ".join(f"{k}: {_m(v)}" for k, v in sens.items()))


def print_report(rep: EvalReport) -> None:
    """Console report of one case under one market."""
    print(
        f"\n== {rep.results_dir}  market={rep.market} ({rep.market_title})"
        f"\n   pre-tax EBIT analysis; money scaled to one year x{rep.scale:g}; "
        f"peak demand {rep.peak_demand_mw:,.1f} MW"
    )
    for note in rep.notes:
        print(f"   - {note}")
    for acc in rep.storages:
        print(
            f"   storage {acc['kind']} {acc['name']}: units"
            f" {', '.join(acc['units']) or '-'}; unit margin"
            f" {_m(acc['unit_margin'])} + storage margin"
            f" {_m(acc['storage_margin'])} = owner profit"
            f" {_m(acc['owner_profit'])} M$/y"
        )
    for p in rep.projects:
        _print_project(p)
    streams = any(p.streams is not None for p in rep.projects)
    risky = any(p.risk is not None for p in rep.projects)
    print(
        f"\n  {'project':<22}{'NPV M$':>11}{'IRR':>8}{'payback':>9}"
        f"{'plan profit':>13}{'viable':>8}"
        + (f"{'stream NPV':>12}{'eq. rate':>10}" if streams else "")
        + (f"{'risk NPV':>10}{'CVaR ok':>9}" if risky else "")
    )
    for p in rep.projects:
        line = (
            f"  {p.name:<22}{_m(p.cash.npv):>11}{_pct(p.cash.irr):>8}"
            f"{_yrs(p.cash.payback):>9}{_m(p.planning_profit):>13}"
            f"{'yes' if p.cash.npv >= 0 else 'no':>8}"
        )
        if streams:
            st = p.streams
            line += (
                f"{_m(st.npv):>12}{_pct(st.equivalent_rate):>10}"
                if st is not None
                else f"{'-':>12}{'-':>10}"
            )
        if risky:
            rk = p.risk
            line += (
                f"{_m(rk.headline):>10}{'yes' if rk.passes_cvar else 'no':>9}"
                if rk is not None
                else f"{'-':>10}{'-':>9}"
            )
        print(line)


def _write_csv(path: Path, reports: list[EvalReport]) -> None:
    rows: list[dict[str, Any]] = []
    keys: list[str] = []
    for rep in reports:
        for p in rep.projects:
            for y in p.cash.years:
                row: dict[str, Any] = {
                    "results_dir": rep.results_dir,
                    "market": rep.market,
                    "project": p.name,
                    "year": y.year,
                }
                row.update({f"rev_{k}": v for k, v in y.revenues.items()})
                row.update({f"cost_{k}": v for k, v in y.costs.items()})
                row.update(
                    revenue=y.revenue,
                    opex=y.opex,
                    ebitda=y.ebitda,
                    depreciation=y.depreciation,
                    ebit=y.ebit,
                    ebit_margin=y.ebit_margin,
                )
                for k in row:
                    if k not in keys:
                        keys.append(k)
                rows.append(row)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, restval=0.0)
        w.writeheader()
        w.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gtopt_private_eval",
        description=(
            "Private-investor (pre-tax EBIT) evaluation of the projects of a "
            "solved gtopt expansion plan under a market model. "
            "Sub-commands: 'capex-trajectory', 'fuel-trajectory' (see "
            "'<sub-command> --help')."
        ),
    )
    p.add_argument("results_dirs", nargs="+", type=Path, help="gtopt output dirs")
    p.add_argument(
        "--market",
        action="append",
        choices=sorted(MARKETS),
        help="market model (repeatable; default energy_only)",
    )
    p.add_argument("--config", type=Path, help="JSON / YAML parameter file")
    p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a parameter, e.g. finance.wacc=0.09",
    )
    p.add_argument("--planning", type=Path, help="planning JSON override")
    p.add_argument("--case-dir", type=Path, help="input directory base")
    p.add_argument("--mef-file", type=Path, help="MEF CSV scenario,stage,block,bus,mef")
    p.add_argument(
        "--include-unbuilt", action="store_true", help="also evaluate unbuilt"
    )
    p.add_argument(
        "--existing",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "also evaluate existing (non-candidate) generators / batteries "
            "whose name matches PATTERN (fnmatch, repeatable) as whole "
            "projects: all their capacity, replacement capex of their "
            "technology unless the config gives one"
        ),
    )
    p.add_argument(
        "--storage-valuation",
        action="append",
        default=[],
        metavar="[TYPE=]MODE",
        help=(
            "MODE 'cash' (default: CMg revenue minus the cash variable cost;"
            " LNG fuel at the purchase price, water free, battery charge at"
            " CMg) or 'shadow' (diagnostic: what a unit draws from the"
            " storage at the storage balance dual, to split profit between"
            " storage and units / value the inventory); TYPE lng_terminal /"
            " reservoir / battery (none = all types; repeatable; config key"
            " storage_valuation)"
        ),
    )
    p.add_argument("--json-out", type=Path, help="write the full report(s) as JSON")
    p.add_argument("--csv-out", type=Path, help="write per project-year rows as CSV")
    p.add_argument("-q", "--quiet", action="store_true", help="no console report")
    return p


def _storage_valuation(cfg: dict[str, Any], items: list[str]) -> None:
    """Merge ``--storage-valuation [TYPE=]MODE`` into ``cfg``."""
    if not items:
        return
    cur = cfg.get("storage_valuation")
    modes: dict[str, str] = (
        dict.fromkeys(STORAGE_TYPES, cur) if isinstance(cur, str) else dict(cur or {})
    )
    for item in items:
        kind, sep, mode = item.partition("=")
        if sep:
            modes[kind] = mode
        else:
            modes.update(dict.fromkeys(STORAGE_TYPES, kind))
    cfg["storage_valuation"] = modes


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "capex-trajectory":
        return capex_trajectory.main(argv[1:])
    if argv and argv[0] == "fuel-trajectory":
        return fuel_trajectory.main(argv[1:])
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        cfg = load_config(args.config)
        for item in args.set:
            key, sep, value = item.partition("=")
            if not sep:
                raise ValueError(f"--set {item}: expected KEY=VALUE")
            set_path(cfg, key, value)
        _storage_valuation(cfg, args.storage_valuation)
        params = Params(cfg)
        reports = []
        for d in args.results_dirs:
            sysd = load_system(
                d,
                planning=args.planning,
                case_dir=args.case_dir,
                existing=args.existing,
            )
            for market in args.market or ["energy_only"]:
                reports.append(
                    evaluate(
                        sysd,
                        params,
                        market,
                        mef_file=args.mef_file,
                        include_unbuilt=args.include_unbuilt,
                    )
                )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"gtopt_private_eval: error: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        for rep in reports:
            print_report(rep)
    if args.json_out is not None:
        payload = [r.to_dict() for r in reports]
        args.json_out.write_text(
            json.dumps(payload[0] if len(payload) == 1 else payload, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.csv_out is not None:
        _write_csv(args.csv_out, reports)
    return 0
