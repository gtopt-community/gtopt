# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Render and write a :class:`~gtopt_compare_runs.engine.Report`.

* :func:`render` -- Rich tables: objective, per-stream summary, the top-N
  differing elements of every failing (or, with ``all_streams``, every)
  stream, elements found in one run only, generation by technology and
  reservoir end-of-stage volumes;
* :func:`write_csv` -- ``streams.csv``, ``elements.csv``,
  ``technology.csv`` and ``reservoirs.csv`` in a directory;
* :func:`write_json` -- the whole summary as JSON;
* :func:`write_plots` -- optional PNG bar charts (matplotlib) of the
  per-stream totals and the generation by technology.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from gtopt_compare_runs.engine import Report

logger = logging.getLogger(__name__)


def _fmt(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    av = abs(v)
    if av != 0 and (av >= 1e7 or av < 1e-3):
        return f"{v:.4g}"
    return f"{v:,.3f}"


def _objective_table(rep: Report) -> Table:
    t = Table(title="Objective (expected total cost, solution.csv)")
    for col in ("", rep.label_a, rep.label_b, "B - A", "rel"):
        t.add_column(col, justify="right" if col else "left")
    a, b = rep.objective_a, rep.objective_b
    diff = None if a is None or b is None else b - a
    rel = None if diff is None or not a else diff / abs(a)
    t.add_row(
        "obj_value", _fmt(a), _fmt(b), _fmt(diff), "-" if rel is None else f"{rel:+.2%}"
    )
    return t


def _streams_table(rep: Report) -> Table:
    t = Table(title=f"Streams ({rep.label_a} = A, {rep.label_b} = B)")
    for col in (
        "stream",
        "kind",
        "n A/B",
        "only A/B",
        "total A",
        "total B",
        "B - A",
        "max |Δ|",
        "max rel",
        "fail",
    ):
        t.add_column(col, justify="left" if col in ("stream", "kind") else "right")
    for s in rep.streams:
        t.add_row(
            s.key,
            s.kind,
            f"{s.n_a}/{s.n_b}",
            f"{len(s.only_a)}/{len(s.only_b)}",
            _fmt(s.total_a),
            _fmt(s.total_b),
            _fmt(s.total_b - s.total_a),
            _fmt(s.max_abs),
            f"{s.max_rel:.2%}",
            "[green]ok[/green]" if s.passed else f"[red]{s.n_fail}[/red]",
        )
    return t


def render(
    rep: Report, console: Console | None = None, *, all_streams: bool = False
) -> None:
    """Print the report (see module doc)."""
    console = console or Console()
    console.print(_objective_table(rep))
    console.print(_streams_table(rep))
    top = rep.options.top
    for s in rep.streams:
        if (s.passed and not all_streams) or s.elements.empty:
            continue
        t = Table(title=f"{s.key}: top {top} elements by |B - A| ({s.kind})")
        for col in ("A", "B", "agg A", "agg B", "B - A", "rel", "max |Δ| cell"):
            t.add_column(
                col,
                justify="right"
                if col.startswith(("agg", "B -", "rel", "max"))
                else "left",
            )
        for r in s.elements.head(top).itertuples():
            t.add_row(
                str(r.name_a),
                str(r.name_b),
                _fmt(r.agg_a),
                _fmt(r.agg_b),
                _fmt(r.diff),
                f"{r.rel_diff:.2%}",
                _fmt(r.max_abs_cell),
            )
        console.print(t)
    for s in rep.streams:
        if s.only_a or s.only_b:
            console.print(
                f"[yellow]{s.key}[/yellow]: only in A: {', '.join(s.only_a[:top]) or '-'}"
                f"{' ...' if len(s.only_a) > top else ''}; only in B: "
                f"{', '.join(s.only_b[:top]) or '-'}"
                f"{' ...' if len(s.only_b) > top else ''}"
            )
    if not rep.technology.empty:
        t = Table(title="Generation by technology [MWh, expected]")
        for col in ("technology", "A", "B", "B - A"):
            t.add_column(col, justify="left" if col == "technology" else "right")
        for r in rep.technology.itertuples():
            t.add_row(str(r.technology), _fmt(r.mwh_a), _fmt(r.mwh_b), _fmt(r.diff))
        console.print(t)
    if not rep.reservoirs.empty:
        t = Table(title="Reservoir end-of-stage volume (expected)")
        for col in ("reservoir", "stage", "A", "B", "B - A"):
            t.add_column(col, justify="left" if col == "reservoir" else "right")
        for r in rep.reservoirs.head(top * 5).itertuples():
            t.add_row(
                str(r.reservoir),
                str(r.stage),
                _fmt(r.efin_a),
                _fmt(r.efin_b),
                _fmt(r.diff),
            )
        console.print(t)
    verdict = "[green]PASS[/green]" if rep.passed else "[red]FAIL[/red]"
    console.print(
        f"{verdict}: {sum(not s.passed for s in rep.streams)} of "
        f"{len(rep.streams)} stream(s) beyond abs_tol={rep.options.abs_tol:g}, "
        f"rel_tol={rep.options.rel_tol:g}"
    )


def write_csv(rep: Report, directory: Path) -> list[Path]:
    """Write the CSV tables; returns their paths."""
    directory.mkdir(parents=True, exist_ok=True)
    streams = pd.DataFrame(
        [
            {**s.summary(), "only_a": len(s.only_a), "only_b": len(s.only_b)}
            for s in rep.streams
        ]
    )
    frames = [s.elements for s in rep.streams if not s.elements.empty]
    elements = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out = []
    for name, df in (
        ("streams.csv", streams),
        ("elements.csv", elements),
        ("technology.csv", rep.technology),
        ("reservoirs.csv", rep.reservoirs),
    ):
        path = directory / name
        df.to_csv(path, index=False)
        out.append(path)
    return out


def write_json(rep: Report, path: Path) -> Path:
    """Write the JSON summary; returns ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rep.summary(), indent=2, default=float) + "\n", encoding="utf-8"
    )
    return path


def write_plots(rep: Report, directory: Path) -> list[Path]:
    """Bar charts of the stream totals and the technology mix (PNG)."""
    try:
        # pylint: disable=import-outside-toplevel
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed: no plots")
        return []
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    data = [(s.key, s.total_a, s.total_b) for s in rep.streams]
    if data:
        fig, ax = plt.subplots(figsize=(8, 0.35 * len(data) + 1.5))
        ys = range(len(data))
        ax.barh([y - 0.2 for y in ys], [d[1] for d in data], 0.4, label=rep.label_a)
        ax.barh([y + 0.2 for y in ys], [d[2] for d in data], 0.4, label=rep.label_b)
        ax.set_yticks(list(ys), [d[0] for d in data])
        ax.set_xscale("symlog")
        ax.legend()
        ax.set_title("Stream totals")
        fig.tight_layout()
        path = directory / "streams.png"
        fig.savefig(path)
        plt.close(fig)
        out.append(path)
    if not rep.technology.empty:
        tech = rep.technology
        fig, ax = plt.subplots(figsize=(8, 0.35 * len(tech) + 1.5))
        ys = range(len(tech))
        ax.barh([y - 0.2 for y in ys], tech["mwh_a"], 0.4, label=rep.label_a)
        ax.barh([y + 0.2 for y in ys], tech["mwh_b"], 0.4, label=rep.label_b)
        ax.set_yticks(list(ys), list(tech["technology"]))
        ax.legend()
        ax.set_title("Generation by technology [MWh]")
        fig.tight_layout()
        path = directory / "technology.png"
        fig.savefig(path)
        plt.close(fig)
        out.append(path)
    return out
