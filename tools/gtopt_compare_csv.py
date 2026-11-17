#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Compare two CSV files with floating-point tolerance.

Designed for comparing gtopt solver output across different LP backends
(CLP, HiGHS, CPLEX) where numerical differences are expected.

Handles:
  - Signed zero: ``-0`` and ``0`` are treated as equal.
  - Near-zero values: absolute differences below *tolerance* pass.
  - Run-dependent solver telemetry columns (``kappa``, ``max_kappa``,
    ``solve_ticks``, ``solve_time_s``, ``solve_calls``, ``infeasible_count``
    and the ``scale_objective`` metadata in ``solution.csv``) are never
    compared — see ``SKIP_COLUMNS``.
  - Columns are matched **by name** and only the columns present in the
    *expected* (golden) file are compared.  Extra columns appended to the
    actual output (e.g. new telemetry in ``solution.csv``) are ignored, so
    goldens do not break whenever the writer grows a column.  The one
    exception is a per-element ``uid:N`` column: an extra element in the
    actual output is a structural change and is reported as an error.
    A golden column missing from the actual output is always an error.
  - ``obj_value`` in ``solution.csv`` is compared in physical cost units,
    using the ``scale_objective`` column to reconcile a golden written in
    LP-raw (scaled) units with a physical output — see ``_ObjectiveColumns``.
  - Numeric differences within *tolerance* are accepted silently.
  - Larger numeric differences (degenerate LP solutions) are logged as
    warnings but accepted by default.  Use ``--strict`` to treat them
    as errors.

Exit codes:
  0  Files match (within tolerance, or numeric-only diffs in default mode).
  1  Files differ (structural, header, non-numeric, or strict numeric).
  2  Usage / file-not-found error.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

_NUMERIC_RE = re.compile(r"^-?[0-9]+(\.[0-9]+(e[+-]?[0-9]+)?)?$", re.IGNORECASE)

# Run-dependent solver metadata / effort telemetry that is never compared:
# condition numbers vary across solvers and builds, and the effort counters
# (deterministic ticks, wall-clock seconds, solve-call and infeasible-solve
# counts) depend on timing, warm starts, thread scheduling and the backend.
SKIP_COLUMNS = frozenset(
    {
        "kappa",
        "max_kappa",
        "solve_ticks",
        "solve_time_s",
        "solve_calls",
        "infeasible_count",
        # LP-scaling metadata, not a result: it only reconciles obj_value
        # units (see _ObjectiveColumns) and legitimately differs between
        # methods (SDDP/cascade default 1, monolithic 1000) and options.
        "scale_objective",
    }
)


def _is_numeric(value: str) -> bool:
    return _NUMERIC_RE.match(value) is not None


def _parse_float(value: str) -> float:
    """Parse a numeric string, treating '-0' as 0."""
    v = float(value)
    return 0.0 if v == 0.0 else v


def _parse_header(line: str) -> list[str]:
    """Return unquoted column names from a CSV header line."""
    return [f.strip().strip('"') for f in line.split(",")]


def _is_long_layout(header: list[str]) -> bool:
    """Return True for the long-form per-element schema.

    Long form (default since 2026-05-19 when ``output_layout=long``)
    has the bare columns ``uid`` and ``value`` — never coexists with
    a wide-form ``uid:N`` column.
    """
    return "uid" in header and "value" in header


def _is_wide_layout(header: list[str]) -> bool:
    """Return True when the header has any ``uid:N`` column."""
    return any(h.startswith("uid:") for h in header)


def _explode_wide_to_long(lines: list[str]) -> list[str]:
    """Convert a wide-form gtopt CSV to canonical long form.

    Wide:  ``scenario,stage,block,uid:1,uid:2,…`` with one row per
           ``(scenario, stage, block)`` and one float column per uid.
    Long:  ``scenario,stage,block,uid,value`` with one row per
           ``(scenario, stage, block, uid)``; zero-valued cells are
           dropped to mirror how the C++ writer emits the long form.

    The result is sorted by ``(scenario, stage, block, uid)`` so the
    line-by-line comparison stays deterministic regardless of the
    source layout.  Returns a list of CSV lines (header + data) ready
    for the existing comparator.
    """
    if not lines:
        return []
    header = _parse_header(lines[0])
    uid_cols: list[tuple[int, int]] = []
    for idx, name in enumerate(header):
        if name.startswith("uid:"):
            try:
                uid_cols.append((idx, int(name.split(":", 1)[1])))
            except ValueError:
                continue
    if not uid_cols:
        return lines  # nothing to explode
    key_cols = [k for k in ("scenario", "stage", "block") if k in header]
    if not key_cols:
        return lines
    key_idxs = [header.index(k) for k in key_cols]

    out_rows: list[tuple[tuple[int, ...], str]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = [f.strip() for f in line.split(",")]
        try:
            key_vals = tuple(int(fields[i]) for i in key_idxs)
        except (ValueError, IndexError):
            continue
        for col_idx, uid in uid_cols:
            if col_idx >= len(fields):
                continue
            raw = fields[col_idx]
            if not raw:
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            # Drop zero-valued cells so the row count matches what the
            # long-form writer would have emitted.
            if val == 0.0:
                continue
            sort_key = (*key_vals, uid)
            # Round-trip via repr-style float formatting so that
            # numerical equality is preserved exactly when the source
            # CSV already happened to be long form.  The downstream
            # comparator does its own tolerance check, so the exact
            # text encoding here does not need to match the C++
            # writer byte-for-byte.
            out_rows.append((sort_key, raw))

    out_rows.sort(key=lambda kv: kv[0])
    header_long = [*key_cols, "uid", "value"]
    out_lines = [",".join(header_long)]
    for key, val_str in out_rows:
        out_lines.append(",".join(str(v) for v in key) + f",{val_str}")
    return out_lines


def _sort_long_lines(lines: list[str]) -> list[str]:
    """Sort an already-long CSV's data rows by ``(scenario, stage, block, uid)``.

    The C++ writer is order-stable but the wide→long conversion above
    sorts on this composite key, so we sort the genuine-long-form side
    on the same key to keep line-by-line comparison aligned.  The
    header is rewritten without surrounding quotes so the byte-for-byte
    header comparison against a wide-side that was exploded by
    :func:`_explode_wide_to_long` (which never quotes) matches.
    """
    if len(lines) <= 1:
        return lines
    header = _parse_header(lines[0])
    key_idxs = [
        header.index(k) for k in ("scenario", "stage", "block", "uid") if k in header
    ]
    if not key_idxs:
        return lines

    def _key(line: str) -> tuple[int, ...]:
        fs = [f.strip() for f in line.split(",")]
        out: list[int] = []
        for i in key_idxs:
            if i >= len(fs):
                return tuple(out)
            try:
                out.append(int(fs[i]))
            except ValueError:
                return tuple(out)
        return tuple(out)

    sorted_data = sorted(lines[1:], key=_key)
    canonical_header = ",".join(header)
    return [canonical_header, *sorted_data]


def _parse_long_keyed_values(
    lines: list[str],
) -> tuple[list[str], dict[tuple[int, ...], float]]:
    """Convert long-form CSV lines into a ``{key_tuple: value}`` dict.

    The key is built from whatever subset of ``(scenario, stage, block,
    uid)`` columns is present in the header — gtopt emits per-stage-only
    files (Battery/capacost_sol etc.) with just ``stage, uid, value`` and
    per-(scene, phase) stage files with the full key.  Returns
    ``(key_cols, dict)`` so the comparator can report keys with the same
    columns the file uses.  ``uid`` is always the last key column when
    present.  Skips the header.  Zero values are kept (so that a wide
    side that exploded into long with zero-drop can still be
    reconstructed correctly: any key absent here means "implicit zero").
    """
    if not lines:
        return [], {}
    header = _parse_header(lines[0])
    key_cols = [k for k in ("scenario", "stage", "block", "uid") if k in header]
    try:
        key_idxs = [header.index(k) for k in key_cols]
        i_va = header.index("value")
    except ValueError:
        return [], {}
    out: dict[tuple[int, ...], float] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        fs = [f.strip() for f in line.split(",")]
        try:
            key = tuple(int(fs[i]) for i in key_idxs)
            val = _parse_float(fs[i_va])
        except (ValueError, IndexError):
            continue
        out[key] = val
    return key_cols, out


def _close_enough(a: float, b: float, tolerance: float) -> bool:
    """Tolerance check matching the loop in :func:`compare_csv`."""
    diff = abs(a - b)
    if abs(b) < tolerance and diff < tolerance:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and diff / scale < tolerance


def _compare_long_keyed(
    actual_lines: list[str],
    expected_lines: list[str],
    *,
    tolerance: float,
    strict: bool,
    verbose: bool,
) -> tuple[list[str], list[str]]:
    """Compare two long-form CSVs by ``(scenario,stage,block,uid)`` key.

    Missing keys default to zero — this is the correct semantics for
    the gtopt long-form encoding, which drops cells whose value is
    zero.  A key present on only one side therefore compares its value
    to ``0.0``.  Used when at least one input is in long form so the
    comparator does not require byte-equal line counts.
    """
    a_key_cols, a_map = _parse_long_keyed_values(actual_lines)
    e_key_cols, e_map = _parse_long_keyed_values(expected_lines)
    errors: list[str] = []
    warnings: list[str] = []
    # Project both maps to the common (intersection-ordered) key
    # columns so a per-stage golden (header ``stage, uid, value``) can
    # still be compared against a per-(scene, stage, block) actual
    # (header ``scenario, stage, block, uid, value``).  The intersection
    # is taken in the natural ``scenario, stage, block, uid`` order so
    # the message labels stay readable.
    common_cols = [
        k
        for k in ("scenario", "stage", "block", "uid")
        if k in a_key_cols and k in e_key_cols
    ]
    if not common_cols:
        # Nothing to key on — degenerate case (e.g. files with no
        # ``stage``/``uid`` columns).  Emit a single header-mismatch
        # error and bail.
        if (
            actual_lines
            and expected_lines
            and _parse_header(actual_lines[0]) != _parse_header(expected_lines[0])
        ):
            errors.append(
                f"Header mismatch:\n"
                f"  Actual:   {actual_lines[0]}\n"
                f"  Expected: {expected_lines[0]}"
            )
        return errors, warnings

    def _project(
        key_cols: list[str], full_map: dict[tuple[int, ...], float]
    ) -> dict[tuple[int, ...], float]:
        if key_cols == common_cols:
            return full_map
        keep = [key_cols.index(c) for c in common_cols]
        out: dict[tuple[int, ...], float] = {}
        for k, v in full_map.items():
            sub = tuple(k[i] for i in keep)
            # Multiple full keys can project onto the same common key
            # (e.g. when the wide-side has no ``scenario`` column but
            # the actual has multiple scenarios) — sum is the right
            # aggregation for emit-and-drop-zeros long form because the
            # opposite side carries the same aggregation.
            out[sub] = out.get(sub, 0.0) + v
        return out

    a_projected = _project(a_key_cols, a_map)
    e_projected = _project(e_key_cols, e_map)
    keys = set(a_projected) | set(e_projected)
    key_label = "(" + ",".join(common_cols) + ")"
    for key in sorted(keys):
        a_val = a_projected.get(key, 0.0)
        e_val = e_projected.get(key, 0.0)
        if a_val == e_val:
            continue
        if _close_enough(a_val, e_val, tolerance):
            if verbose:
                print(
                    f"  Numeric diff at key {key}: actual={a_val!r}, expected={e_val!r}"
                )
            continue
        msg = (
            f"Numeric mismatch at key {key_label}={key}: "
            f"actual={a_val!r}, expected={e_val!r} (diff={abs(a_val - e_val):.6e})"
        )
        if strict:
            errors.append(msg)
        else:
            warnings.append(msg)
    return errors, warnings


OBJ_VALUE_COLUMN = "obj_value"
SCALE_OBJECTIVE_COLUMN = "scale_objective"


@dataclass(frozen=True)
class _ObjectiveColumns:
    """Scale-aware comparison of the ``solution.csv`` objective.

    Convention (``include/gtopt/solution_csv.hpp``): ``obj_value`` is the
    cell objective in PHYSICAL cost units (LP-raw objective ×
    ``scale_objective``) for every planning method, and the
    ``scale_objective`` column records the factor.  A file that carries
    the ``scale_objective`` column is therefore unambiguous: its
    ``obj_value`` is compared as-is.

    Legacy goldens written before the column existed may hold either the
    LP-raw objective (the pre-April-2026 convention, ``obj_value`` ÷
    ``scale_objective``) or the physical one.  For such a file the
    candidate physical values are ``{v, v × S}`` where ``S`` is the
    ``scale_objective`` of the other side of the same row; the row
    matches when any candidate pair agrees within tolerance.  This keeps
    the objective under comparison (a real cost change is still caught)
    while tolerating a golden written in the other scale.
    """

    a_obj: int
    e_obj: int
    a_scale: int | None
    e_scale: int | None

    @classmethod
    def from_headers(
        cls, a_header: list[str], e_header: list[str]
    ) -> _ObjectiveColumns | None:
        """Build the column map, or ``None`` when no objective is compared."""
        if OBJ_VALUE_COLUMN not in e_header or OBJ_VALUE_COLUMN not in a_header:
            return None

        def _idx(header: list[str]) -> int | None:
            if SCALE_OBJECTIVE_COLUMN in header:
                return header.index(SCALE_OBJECTIVE_COLUMN)
            return None

        return cls(
            a_obj=a_header.index(OBJ_VALUE_COLUMN),
            e_obj=e_header.index(OBJ_VALUE_COLUMN),
            a_scale=_idx(a_header),
            e_scale=_idx(e_header),
        )

    @staticmethod
    def _float_at(fields: list[str], idx: int | None) -> float | None:
        if idx is None or idx >= len(fields) or not _is_numeric(fields[idx]):
            return None
        return _parse_float(fields[idx])

    def compare(
        self,
        actual_line: str,
        expected_line: str,
        *,
        line_num: int,
        tolerance: float,
        errors: list[str],
    ) -> None:
        """Compare the objective of one row in physical units.

        The optimal objective is unique even when the primal solution is
        degenerate, so — unlike per-element primal values — an objective
        mismatch is always a hard error, also in non-strict mode.
        """
        a_fields = [f.strip() for f in actual_line.split(",")]
        e_fields = [f.strip() for f in expected_line.split(",")]
        a_val = self._float_at(a_fields, self.a_obj)
        e_val = self._float_at(e_fields, self.e_obj)
        if a_val is None or e_val is None:
            errors.append(
                f"Non-numeric or missing {OBJ_VALUE_COLUMN} at line {line_num}:\n"
                f"  Actual:   {actual_line}\n"
                f"  Expected: {expected_line}"
            )
            return
        a_scale = self._float_at(a_fields, self.a_scale)
        e_scale = self._float_at(e_fields, self.e_scale)

        # Physical-value candidates per side (see class docstring).
        a_cands = [a_val]
        if self.a_scale is None and e_scale is not None:
            a_cands.append(a_val * e_scale)
        e_cands = [e_val]
        if self.e_scale is None and a_scale is not None:
            e_cands.append(e_val * a_scale)

        if any(_close_enough(a, e, tolerance) for a in a_cands for e in e_cands):
            return

        diff = min(abs(a - e) for a in a_cands for e in e_cands)
        msg = (
            f"Objective mismatch at line {line_num} (physical units): "
            f"actual {OBJ_VALUE_COLUMN}={a_val!r} "
            f"(scale_objective={a_scale!r}), expected {OBJ_VALUE_COLUMN}="
            f"{e_val!r} (scale_objective={e_scale!r}) (diff={diff:.6e})"
        )
        errors.append(msg)


def _match_columns(
    actual_header: list[str], expected_header: list[str]
) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Pair golden columns with actual columns by name.

    Returns ``(pairs, errors)`` where *pairs* is a list of
    ``(name, actual_idx, expected_idx)`` for every golden column that is
    compared (``SKIP_COLUMNS`` excluded).  Errors are reported for golden
    columns missing from the actual header, for duplicated column names,
    and for extra per-element ``uid:N`` columns in the actual header.
    Other extra actual columns are ignored (forward compatibility).
    """
    errors: list[str] = []
    for label, header in (("actual", actual_header), ("expected", expected_header)):
        dups = sorted({h for h in header if header.count(h) > 1})
        if dups:
            errors.append(f"Duplicate column(s) in {label} header: {dups}")
    if errors:
        return [], errors

    actual_pos = {name: idx for idx, name in enumerate(actual_header)}
    # A never-compared (SKIP_COLUMNS) golden column may disappear freely.
    missing = [
        h for h in expected_header if h not in actual_pos and h not in SKIP_COLUMNS
    ]
    if missing:
        errors.append(
            f"Column(s) {missing} missing from actual output\n"
            f"  Actual:   {','.join(actual_header)}\n"
            f"  Expected: {','.join(expected_header)}"
        )
    expected_set = set(expected_header)
    extra_uids = [
        h for h in actual_header if h.startswith("uid:") and h not in expected_set
    ]
    if extra_uids:
        errors.append(f"Unexpected element column(s) in actual output: {extra_uids}")
    if errors:
        return [], errors

    pairs = [
        (name, actual_pos[name], e_idx)
        for e_idx, name in enumerate(expected_header)
        if name not in SKIP_COLUMNS
    ]
    return pairs, errors


def _compare_row(  # pylint: disable=too-many-arguments
    actual_line: str,
    expected_line: str,
    *,
    line_num: int,
    col_pairs: list[tuple[str, int, int]],
    tolerance: float,
    strict: bool,
    verbose: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Compare one data row on the matched columns, appending diagnostics."""
    actual_fields = [f.strip() for f in actual_line.split(",")]
    expected_fields = [f.strip() for f in expected_line.split(",")]

    for name, a_idx, e_idx in col_pairs:
        if a_idx >= len(actual_fields) or e_idx >= len(expected_fields):
            errors.append(
                f"Field count mismatch at line {line_num} (column '{name}'):\n"
                f"  Actual:   {actual_line}\n"
                f"  Expected: {expected_line}"
            )
            return
        av = actual_fields[a_idx]
        ev = expected_fields[e_idx]
        if av == ev:
            continue

        # Normalize signed zero
        if ("0" if av == "-0" else av) == ("0" if ev == "-0" else ev):
            continue

        if not (_is_numeric(av) and _is_numeric(ev)):
            # Non-numeric: require exact match
            errors.append(
                f"Value mismatch at line {line_num}, column '{name}':\n"
                f"  Actual:   '{av}'\n"
                f"  Expected: '{ev}'"
            )
            continue

        a_val = _parse_float(av)
        e_val = _parse_float(ev)
        if _close_enough(a_val, e_val, tolerance):
            if verbose:
                print(
                    f"  Numeric diff at line {line_num}, column '{name}': "
                    f"actual={av}, expected={ev}"
                )
            continue

        # Beyond tolerance — warning or error depending on mode
        msg = (
            f"Numeric mismatch at line {line_num}, column '{name}': "
            f"actual={av}, expected={ev} (diff={abs(a_val - e_val):.6e})"
        )
        if strict:
            errors.append(msg)
        else:
            warnings.append(msg)


def compare_csv(
    actual_path: str,
    expected_path: str,
    tolerance: float = 1e-6,
    *,
    strict: bool = False,
    verbose: bool = False,
) -> tuple[list[str], list[str]]:
    """Compare two CSV files.

    Returns ``(errors, warnings)`` where *errors* are hard failures
    (structural, header, non-numeric mismatches, or strict-mode numeric)
    and *warnings* are numeric differences beyond tolerance that are
    accepted in non-strict mode.

    Layout-aware: when one input is in the wide gtopt per-element
    layout (``uid:N`` columns) and the other is in the long layout
    (``scenario, stage, block, uid, value``), both are normalised to
    long form before the line-by-line comparison so the comparator
    works regardless of which side flipped its ``output_layout``.
    When both sides are (or normalise to) long form, the comparison
    runs key-by-key on ``(scenario, stage, block, uid)`` and treats
    a missing key as the implicit zero the long encoding drops —
    line-count differences therefore become per-key numeric diffs
    rather than a fatal structural mismatch.
    """
    with open(actual_path, encoding="utf-8") as f:
        actual_lines = f.read().splitlines()
    with open(expected_path, encoding="utf-8") as f:
        expected_lines = f.read().splitlines()

    # Normalise to a common shape when the two sides used different
    # ``output_layout`` settings.  When both are already wide (or both
    # already long, or neither is a per-element file like solution.csv),
    # this is a no-op aside from a stable sort on the long side.
    layout_long = False
    if actual_lines and expected_lines:
        a_hdr = _parse_header(actual_lines[0])
        e_hdr = _parse_header(expected_lines[0])
        a_long = _is_long_layout(a_hdr)
        a_wide = _is_wide_layout(a_hdr)
        e_long = _is_long_layout(e_hdr)
        e_wide = _is_wide_layout(e_hdr)
        if (a_long and e_wide) or (a_wide and e_long):
            if a_wide:
                actual_lines = _explode_wide_to_long(actual_lines)
            else:
                actual_lines = _sort_long_lines(actual_lines)
            if e_wide:
                expected_lines = _explode_wide_to_long(expected_lines)
            else:
                expected_lines = _sort_long_lines(expected_lines)
            layout_long = True
        elif a_long and e_long:
            actual_lines = _sort_long_lines(actual_lines)
            expected_lines = _sort_long_lines(expected_lines)
            layout_long = True

    if layout_long:
        return _compare_long_keyed(
            actual_lines,
            expected_lines,
            tolerance=tolerance,
            strict=strict,
            verbose=verbose,
        )

    errors: list[str] = []
    warnings: list[str] = []

    if len(actual_lines) != len(expected_lines):
        errors.append(
            f"Line count mismatch: actual={len(actual_lines)}, "
            f"expected={len(expected_lines)}"
        )
        return errors, warnings
    if not expected_lines:
        return errors, warnings

    # Map each compared golden column to its position in the actual header.
    a_header = _parse_header(actual_lines[0])
    e_header = _parse_header(expected_lines[0])
    col_pairs, header_errors = _match_columns(a_header, e_header)
    if header_errors:
        errors.extend(header_errors)
        return errors, warnings

    # ``obj_value`` is compared in physical units (see _ObjectiveColumns),
    # not by the generic per-column loop.
    objective = _ObjectiveColumns.from_headers(a_header, e_header)
    if objective is not None:
        col_pairs = [p for p in col_pairs if p[0] != OBJ_VALUE_COLUMN]

    for i, (actual_line, expected_line) in enumerate(
        zip(actual_lines[1:], expected_lines[1:]), start=2
    ):
        if objective is not None:
            objective.compare(
                actual_line,
                expected_line,
                line_num=i,
                tolerance=tolerance,
                errors=errors,
            )
        _compare_row(
            actual_line,
            expected_line,
            line_num=i,
            col_pairs=col_pairs,
            tolerance=tolerance,
            strict=strict,
            verbose=verbose,
            errors=errors,
            warnings=warnings,
        )

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two CSV files with floating-point tolerance."
    )
    parser.add_argument("actual", help="Path to the actual (solver output) CSV file.")
    parser.add_argument("expected", help="Path to the expected (reference) CSV file.")
    parser.add_argument(
        "-t",
        "--tolerance",
        type=float,
        default=1e-6,
        help="Numeric tolerance (default: 1e-6).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat numeric differences beyond tolerance as errors.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show accepted numeric diffs."
    )
    args = parser.parse_args(argv)

    for path, label in [(args.actual, "Actual"), (args.expected, "Expected")]:
        try:
            with open(path, encoding="utf-8"):
                pass
        except FileNotFoundError:
            print(f"{label} file not found: {path}", file=sys.stderr)
            return 2

    errors, warnings = compare_csv(
        args.actual,
        args.expected,
        tolerance=args.tolerance,
        strict=args.strict,
        verbose=args.verbose,
    )

    for warn in warnings:
        print(f"WARN: {warn}", file=sys.stderr)

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    if warnings:
        print(f"OK (with {len(warnings)} numeric warnings): {args.actual}")
    else:
        print(f"OK: {args.actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
