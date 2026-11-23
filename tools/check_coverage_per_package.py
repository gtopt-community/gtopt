#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-package Python coverage gate.

Reads one or more ``coverage.json`` reports (``pytest --cov-report=json``,
branch coverage on) and fails when

* any top-level package is below ``package_min`` percent, or
* the combined total is below ``total_min`` percent.

Percentages follow coverage.py's own branch-aware formula::

    (covered_lines + covered_branches) / (num_statements + num_branches)

The package of a measured file is the first component of its path as
recorded in the report (``plp2gtopt/parsers.py`` -> ``plp2gtopt``), with
an optional ``--prefix`` stripped first.  Thresholds default to the
``[tool.gtopt.coverage]`` table of the given ``--config`` pyproject::

    [tool.gtopt.coverage]
    package_min = 90.0
    total_min = 95.0
    # temporary per-package floors (ratchet); remove as packages reach
    # package_min.  A floor can only lower the bar for that package.
    [tool.gtopt.coverage.package_floor]
    plexos2gtopt = 85.0

Usage::

    python tools/check_coverage_per_package.py scripts/coverage.json \\
        --config scripts/pyproject.toml
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Tally:
    """Statement/branch counters for one package (or the total)."""

    statements: int = 0
    covered_lines: int = 0
    branches: int = 0
    covered_branches: int = 0

    def add(self, summary: dict[str, Any]) -> None:
        """Accumulate one file's ``summary`` block."""
        self.statements += int(summary.get("num_statements", 0))
        self.covered_lines += int(summary.get("covered_lines", 0))
        self.branches += int(summary.get("num_branches", 0))
        self.covered_branches += int(summary.get("covered_branches", 0))

    @property
    def percent(self) -> float:
        """Branch-aware coverage percentage (100 when nothing to cover)."""
        denom = self.statements + self.branches
        if denom == 0:
            return 100.0
        return 100.0 * (self.covered_lines + self.covered_branches) / denom

    @property
    def line_percent(self) -> float:
        """Statement-only coverage percentage."""
        if self.statements == 0:
            return 100.0
        return 100.0 * self.covered_lines / self.statements

    @property
    def branch_percent(self) -> float:
        """Branch-only coverage percentage."""
        if self.branches == 0:
            return 100.0
        return 100.0 * self.covered_branches / self.branches


def package_of(path: str, prefix: str = "") -> str:
    """Return the top-level package of a measured file path."""
    norm = path.replace("\\", "/")
    if prefix and norm.startswith(prefix):
        norm = norm[len(prefix) :]
    norm = norm.lstrip("./")
    head, sep, _ = norm.partition("/")
    return head if sep else "<top-level>"


def tally_reports(
    reports: list[dict[str, Any]], prefix: str = ""
) -> tuple[dict[str, Tally], Tally]:
    """Aggregate per-package and total tallies from coverage JSON reports."""
    packages: dict[str, Tally] = {}
    total = Tally()
    for report in reports:
        for path, data in report.get("files", {}).items():
            summary = data.get("summary", {})
            packages.setdefault(package_of(path, prefix), Tally()).add(summary)
            total.add(summary)
    return packages, total


def load_thresholds(config: Path | None) -> tuple[float, float, dict[str, float]]:
    """Read ``package_min``, ``total_min`` and floors from a pyproject."""
    package_min, total_min = 90.0, 95.0
    floors: dict[str, float] = {}
    if config is None:
        return package_min, total_min, floors
    with config.open("rb") as fh:
        table = tomllib.load(fh).get("tool", {}).get("gtopt", {}).get("coverage", {})
    package_min = float(table.get("package_min", package_min))
    total_min = float(table.get("total_min", total_min))
    floors = {k: float(v) for k, v in table.get("package_floor", {}).items()}
    return package_min, total_min, floors


def evaluate(
    packages: dict[str, Tally],
    total: Tally,
    package_min: float,
    total_min: float,
    floors: dict[str, float],
) -> list[str]:
    """Return the list of gate violations (empty when the gate passes)."""
    failures: list[str] = []
    for name, tally in sorted(packages.items()):
        need = min(package_min, floors.get(name, package_min))
        if tally.percent + 1e-9 < need:
            failures.append(f"{name}: {tally.percent:.1f}% < {need:.1f}%")
    if total.percent + 1e-9 < total_min:
        failures.append(f"TOTAL: {total.percent:.1f}% < {total_min:.1f}%")
    return failures


def format_table(packages: dict[str, Tally], total: Tally) -> str:
    """Render the per-package report, worst package first."""
    header = (
        f"{'package':<26} {'stmts':>7} {'miss':>6} {'branch':>7} "
        f"{'bmiss':>6} {'line%':>6} {'br%':>6} {'cover%':>7}"
    )
    rows = [header]

    def row(name: str, t: Tally) -> str:
        return (
            f"{name:<26} {t.statements:>7} {t.statements - t.covered_lines:>6} "
            f"{t.branches:>7} {t.branches - t.covered_branches:>6} "
            f"{t.line_percent:>6.1f} {t.branch_percent:>6.1f} {t.percent:>7.1f}"
        )

    for name, tally in sorted(packages.items(), key=lambda kv: kv[1].percent):
        rows.append(row(name, tally))
    rows.append(row("TOTAL", total))
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports", nargs="+", type=Path, help="coverage.json files")
    parser.add_argument("--config", type=Path, help="pyproject.toml with thresholds")
    parser.add_argument("--prefix", default="", help="path prefix to strip")
    parser.add_argument("--package-min", type=float, help="override package_min")
    parser.add_argument("--total-min", type=float, help="override total_min")
    args = parser.parse_args(argv)

    package_min, total_min, floors = load_thresholds(args.config)
    if args.package_min is not None:
        package_min = args.package_min
    if args.total_min is not None:
        total_min = args.total_min

    reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.reports]
    packages, total = tally_reports(reports, args.prefix)
    print(format_table(packages, total))
    failures = evaluate(packages, total, package_min, total_min, floors)
    if failures:
        print(
            f"\nCoverage gate FAILED (package >= {package_min:.1f}%, "
            f"total >= {total_min:.1f}%):",
            file=sys.stderr,
        )
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"\nCoverage gate OK (package >= {package_min:.1f}%, total >= {total_min:.1f}%)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
