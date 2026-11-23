# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Machine-readable snapshot of gtopt's C++ JSON input contract.

The C++ source of truth for every JSON key gtopt accepts is the set of
``daw::json::json_data_contract<T>`` member lists in
``include/gtopt/json/json_*.hpp``; the parser is strict
(``UseExactMappingsByDefault::yes``), so any key not declared there — and
not rewritten first by the naming-dialect canonicaliser
(``share/gtopt/naming_dialects.json``) — makes gtopt reject the input.

This module turns those headers into a small JSON snapshot
(``gtopt_shared/data/json_contracts.json``, shipped in the wheel) and
offers :func:`find_schema_problems`, a key walker that reports every
unknown or deprecated key of a planning dict *before* gtopt is run.

The snapshot is regenerated from the headers with::

    python -m gtopt_shared.json_contracts --update

and ``gtopt_shared/tests/test_json_contracts_sync.py`` fails when it is
stale, or when one of the Python mirrors of the contract (igtopt field
metadata / sheets, options partitions, naming dialects, ...) drifts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Bundled snapshot (relative to this package).
SNAPSHOT_RELPATH = ("data", "json_contracts.json")

#: Root contract of a planning file.
ROOT_TYPE = "Planning"

#: Keys the C++ contract still parses ONLY to translate them onto the
#: FutureCost element (``resolve_future_cost_aliases`` in
#: ``source/future_cost_aliases.cpp``).  Tools must not emit them.
#: ``{contract type: {legacy key: replacement}}``.
DEPRECATED_KEYS: dict[str, dict[str, str]] = {
    "SddpOptions": {
        "boundary_cuts_file": "future_cost_array[].cuts_file",
        "boundary_cuts_mode": "future_cost_array[].mode",
        "boundary_cut_sharing_mode": "future_cost_array[].sharing",
        "boundary_cuts_mean_shift": "future_cost_array[].mean_shift",
        "boundary_max_iterations": "future_cost_array[].iteration_limit",
        "missing_cut_var_mode": "future_cost_array[].missing_cut_var_mode",
    },
    "MonolithicOptions": {
        "boundary_cuts_file": "future_cost_array[].cuts_file",
        "boundary_cuts_mode": "future_cost_array[].mode",
        "boundary_cut_sharing_mode": "future_cost_array[].sharing",
        "boundary_max_iterations": "future_cost_array[].iteration_limit",
    },
    "Simulation": {
        "boundary_cuts_file": "future_cost_array[].cuts_file",
        "boundary_cuts_valuation": "future_cost_array[].valuation",
    },
}

# ── header parsing ─────────────────────────────────────────────────────────

_CONTRACT_RE = re.compile(r"struct\s+json_data_contract\s*<\s*([A-Za-z_][\w:]*)\s*>")
_MEMBER_LIST_RE = re.compile(r"json_member_list\s*<")
_MEMBER_RE = re.compile(r'^(json_\w+?)\s*<\s*"([^"]+)"\s*(?:,(.*))?>$', re.DOTALL)
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Remove C/C++ comments (string literals in contracts never hold them)."""
    return _COMMENT_RE.sub("", text)


def _matching_angle(text: str, start: int) -> int:
    """Index of the ``>`` closing the ``<`` at ``text[start]``."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced '<' in json_member_list")


def _split_top_level(args: str) -> list[str]:
    """Split a template argument list on commas at nesting depth 0."""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in args:
        if ch in "<([{":
            depth += 1
        elif ch in ">)]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def _norm_type(name: str) -> str:
    return re.sub(r"\s+", "", name).removeprefix("gtopt::")


def _unwrap_candidates(arg: str) -> list[str]:
    """Candidate contract type names for a member's C++ type argument."""
    t = _norm_type(arg)
    cands = [t]
    for wrapper in ("std::optional<", "Array<", "std::vector<"):
        if t.startswith(wrapper) and t.endswith(">"):
            cands.append(_norm_type(t[len(wrapper) : -1]))
    cands += [c[3:] for c in list(cands) if c.startswith("Opt") and len(c) > 3]
    return cands


def parse_contract_headers(json_dir: Path) -> dict[str, dict[str, Any]]:
    """Parse every ``json_*.hpp`` under ``json_dir``.

    Returns ``{type: {"header": str, "fields": [key, ...],
    "kinds": {key: kind}, "nested": {key: type}}}`` where ``kind`` is the
    daw member kind without the ``json_`` / ``_null`` / ``_raw`` decoration
    (``number``, ``string``, ``bool``, ``variant``, ``array``, ``class``,
    ``custom``), ``nested`` maps a ``json_class`` / ``json_array`` member to
    the contract type of its value (or of its elements) when that type has
    a contract of its own.
    """
    raw: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(json_dir).glob("json_*.hpp")):
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for cm in _CONTRACT_RE.finditer(text):
            ml = _MEMBER_LIST_RE.search(text, cm.end())
            if ml is None:
                continue
            # Only accept the member list that belongs to this contract
            # (before the next contract declaration).
            nxt = _CONTRACT_RE.search(text, cm.end())
            if nxt is not None and ml.start() > nxt.start():
                continue
            open_idx = ml.end() - 1
            close_idx = _matching_angle(text, open_idx)
            members = _split_top_level(text[open_idx + 1 : close_idx])
            fields: list[str] = []
            type_args: dict[str, tuple[str, list[str]]] = {}
            for member in members:
                mm = _MEMBER_RE.match(member.strip())
                if mm is None:
                    continue
                kind, key = mm.group(1), mm.group(2)
                args = _split_top_level(mm.group(3) or "")
                if key not in fields:
                    fields.append(key)
                type_args[key] = (kind, args)
            type_name = _norm_type(cm.group(1))
            if type_name in raw:
                raise ValueError(
                    f"duplicate json_data_contract<{type_name}> in "
                    f"{raw[type_name]['header']} and {path.name}"
                )
            raw[type_name] = {
                "header": path.name,
                "fields": fields,
                "kinds": {
                    k: re.sub(r"^json_|_null$|_raw$", "", kind).removesuffix("_raw")
                    for k, (kind, _args) in type_args.items()
                },
                "_type_args": type_args,
            }

    known = set(raw)
    for info in raw.values():
        nested: dict[str, str] = {}
        for key, (kind, args) in info.pop("_type_args").items():
            if not args or not kind.startswith(("json_class", "json_array")):
                continue
            ordered = [args[-1], args[0]] if kind.startswith("json_array") else args
            for arg in ordered:
                hit = next((c for c in _unwrap_candidates(arg) if c in known), None)
                if hit is not None:
                    nested[key] = hit
                    break
        info["nested"] = nested
    return raw


def parse_dialect_aliases(dialects_file: Path) -> list[str]:
    """Alternative keys the JSON-input canonicaliser rewrites.

    Only the global ``aliases[]`` section applies to JSON input;
    ``class_aliases[]`` is consulted by the AMPL resolver alone, so a
    class-scoped alias in a planning file is still rejected.
    """
    raw = json.loads(Path(dialects_file).read_text(encoding="utf-8"))
    return sorted({str(row["alt"]) for row in raw.get("aliases", []) if row.get("alt")})


def build_snapshot(repo_root: Path) -> dict[str, Any]:
    """Build the snapshot dict from a source checkout."""
    json_dir = repo_root / "include" / "gtopt" / "json"
    dialects = repo_root / "share" / "gtopt" / "naming_dialects.json"
    return {
        "_doc": (
            "GENERATED by `python -m gtopt_shared.json_contracts --update` "
            "from include/gtopt/json/json_*.hpp and "
            "share/gtopt/naming_dialects.json.  Do not edit by hand."
        ),
        "root": ROOT_TYPE,
        "types": parse_contract_headers(json_dir),
        "dialect_aliases": parse_dialect_aliases(dialects),
    }


def find_repo_root(start: Path | None = None) -> Path | None:
    """Closest ancestor holding ``include/gtopt/json`` (None outside a checkout)."""
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / "include" / "gtopt" / "json").is_dir():
            return parent
    return None


def snapshot_path() -> Path:
    return Path(__file__).resolve().parent.joinpath(*SNAPSHOT_RELPATH)


@lru_cache(maxsize=1)
def load_snapshot() -> dict[str, Any]:
    """The bundled snapshot (see :data:`SNAPSHOT_RELPATH`)."""
    # pylint: disable-next=import-outside-toplevel
    from gtopt_shared.resources import package_data_file

    path = package_data_file(
        "gtopt_shared", *SNAPSHOT_RELPATH, purpose="gtopt JSON contract snapshot"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def contract_fields(type_name: str) -> list[str]:
    """Canonical JSON keys of contract ``type_name`` (empty when unknown)."""
    info = load_snapshot()["types"].get(type_name)
    return list(info["fields"]) if info else []


def array_types(section: str = "System") -> dict[str, str]:
    """``{array key: element contract type}`` of ``System`` / ``Simulation``."""
    info = load_snapshot()["types"][section]
    return {k: t for k, t in info["nested"].items() if k.endswith("_array")}


# ── planning key walker ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SchemaIssue:
    """One key problem found by :func:`find_schema_problems`."""

    kind: str  # "unknown" | "deprecated" | "alias"
    path: str  # JSON path, e.g. "options.use_single_bus"
    key: str
    contract: str  # C++ contract type that owns the parent object
    message: str


def _hint(contract: str, key: str, types: dict[str, Any]) -> str:
    owners = sorted(t for t, info in types.items() if key in info["fields"])
    if contract == "PlanningOptions" and "ModelOptions" in owners:
        return " (model options are nested under options.model_options)"
    if owners:
        return f" (declared on {', '.join(owners[:4])})"
    return ""


def find_schema_problems(
    planning: Any,
    *,
    snapshot: dict[str, Any] | None = None,
    root_type: str | None = None,
) -> list[SchemaIssue]:
    """Walk ``planning`` against the C++ contract.

    Reports keys gtopt would reject (``unknown``), keys that are only
    parsed as deprecated aliases (``deprecated``), and naming-dialect
    aliases (``alias``: accepted, but not the canonical spelling).
    Values of scalar / schedule members are not inspected.
    """
    snap = snapshot if snapshot is not None else load_snapshot()
    types: dict[str, Any] = snap["types"]
    aliases = set(snap.get("dialect_aliases", ()))
    issues: list[SchemaIssue] = []

    def walk(obj: Any, contract: str, path: str) -> None:
        if isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, contract, f"{path}[{i}]")
            return
        if not isinstance(obj, dict):
            return
        info = types.get(contract)
        if info is None:
            return
        fields = set(info["fields"])
        deprecated = DEPRECATED_KEYS.get(contract, {})
        for key, value in obj.items():
            kpath = f"{path}.{key}" if path else key
            if key in deprecated:
                issues.append(
                    SchemaIssue(
                        "deprecated",
                        kpath,
                        key,
                        contract,
                        f"deprecated key '{kpath}' → use {deprecated[key]}",
                    )
                )
            elif key not in fields:
                if key in aliases:
                    issues.append(
                        SchemaIssue(
                            "alias",
                            kpath,
                            key,
                            contract,
                            f"'{kpath}' is a naming-dialect alias, not the "
                            "canonical key",
                        )
                    )
                else:
                    issues.append(
                        SchemaIssue(
                            "unknown",
                            kpath,
                            key,
                            contract,
                            f"unknown key '{kpath}' — not a field of "
                            f"{contract}{_hint(contract, key, types)}",
                        )
                    )
                continue
            sub = info["nested"].get(key)
            if sub is not None:
                walk(value, sub, kpath)

    walk(planning, root_type or snap.get("root", ROOT_TYPE), "")
    return issues


# ── CLI ────────────────────────────────────────────────────────────────────


def _dump(snap: dict[str, Any]) -> str:
    return json.dumps(snap, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Regenerate (``--update``) or verify (``--check``) the snapshot."""
    parser = argparse.ArgumentParser(
        prog="python -m gtopt_shared.json_contracts",
        description=__doc__.split("\n\n", 1)[0] if __doc__ else None,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--update", action="store_true", help="rewrite the bundled snapshot"
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when the bundled snapshot is stale (default)",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=None, help="gtopt source checkout"
    )
    args = parser.parse_args(argv)

    root = args.repo_root or find_repo_root()
    if root is None:
        print("error: gtopt source checkout not found", file=sys.stderr)
        return 2
    fresh = _dump(build_snapshot(root))
    target = snapshot_path()
    if args.update:
        target.write_text(fresh, encoding="utf-8")
        print(f"wrote {target}")
        return 0
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    if current != fresh:
        print(
            f"{target} is stale — run `python -m gtopt_shared.json_contracts --update`",
            file=sys.stderr,
        )
        return 1
    print(f"{target} is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
