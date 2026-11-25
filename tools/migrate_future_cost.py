#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Migrate deprecated boundary-cut keys of gtopt JSON files to FutureCost.

The FutureCost element (``system.future_cost_array``) is the single,
method-neutral source of the FCF boundary cuts.  This rewrites every
``sddp_options.boundary_*`` / ``missing_cut_var_mode``,
``monolithic_options.boundary_*`` and ``simulation.boundary_cuts_*`` key of
the given files onto the first active FutureCost element (created as
``{uid: 1, name: "fcf"}`` when absent), leaving every other key untouched.

By default EVERY legacy key is moved, whatever ``options.method`` says (a
checked-in case carrying a cut file is meant to load it); this can change a
case's behaviour under a method that ignored the alias before — such files
are listed.  ``--method-rules`` applies gtopt's own alias rules instead
(keys of the other method are dropped).

Formatting is preserved: the file is re-serialised with its detected indent
and escaping only after checking that an unmodified round trip reproduces it
byte for byte; otherwise the file is skipped and reported.

Usage::

    python tools/migrate_future_cost.py [--method-rules] [--dry-run] FILE...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# pylint: disable=wrong-import-position
from gtopt_shared.future_cost import (
    LEGACY_ALIASES,
    ensure_future_cost,
    legacy_keys_present,
    planning_method,
    resolve_legacy_future_cost_keys,
)


def _dump(data: Any, indent: int | None, ensure_ascii: bool) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)


def _detect_format(text: str) -> tuple[int | None, bool] | None:
    """(indent, ensure_ascii) that reproduce *text* exactly, or None."""
    data = json.loads(text)
    for indent in (2, 4, None):
        for ensure_ascii in (False, True):
            if _dump(data, indent, ensure_ascii).strip() == text.strip():
                return indent, ensure_ascii
    return None


def _fold_all(planning: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Move EVERY legacy key onto FutureCost, ignoring the method rule.

    Returns ``(foreign, errors)``: the keys the declared method would have
    ignored, and any conflicting-value messages.
    """
    method = planning_method(planning)
    foreign: list[str] = []
    errors: list[str] = []
    for alias in LEGACY_ALIASES:
        if alias.section == "simulation":
            section = planning.get("simulation") or {}
        else:
            section = (planning.get("options") or {}).get(alias.section) or {}
        if section.get(alias.key) is None:
            continue
        value = section[alias.key]
        if alias.methods is not None and method not in alias.methods:
            foreign.append(alias.dotted)
        element = ensure_future_cost(planning)
        current = element.get(alias.target)
        if current is not None and current != value:
            errors.append(
                f"conflicting '{alias.target}': {current!r} vs {value!r} "
                f"(from {alias.dotted})"
            )
            continue
        element[alias.target] = value
        del section[alias.key]
    return foreign, errors


def migrate(path: Path, *, method_rules: bool, dry_run: bool) -> str:
    """Migrate one file; return a one-line status."""
    text = path.read_text(encoding="utf-8")
    fmt = _detect_format(text)
    if fmt is None:
        return f"SKIP {path}: formatting does not round-trip"
    planning = json.loads(text)
    present = legacy_keys_present(planning)
    if not present:
        return f"OK   {path}: nothing to migrate"

    foreign: list[str] = []
    if method_rules:
        errors = resolve_legacy_future_cost_keys(planning).errors
    else:
        foreign, errors = _fold_all(planning)
    if errors:
        return f"FAIL {path}: " + "; ".join(errors)
    opts = planning.get("options", {})
    for section in ("sddp_options", "monolithic_options"):
        if section in opts and not opts[section]:
            del opts[section]

    new_text = _dump(planning, *fmt) + ("\n" if text.endswith("\n") else "")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    note = (
        f" (NOTE: {', '.join(foreign)} was ignored under method="
        f"{planning_method(planning)}; the cuts now load)"
        if foreign
        else ""
    )
    return f"DONE {path}: moved {', '.join(present)}{note}"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument(
        "--method-rules",
        action="store_true",
        help="apply gtopt's per-method alias rules (drop other-method keys)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rc = 0
    for path in args.files:
        status = migrate(path, method_rules=args.method_rules, dry_run=args.dry_run)
        print(status)
        if status.startswith(("FAIL", "SKIP")):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
