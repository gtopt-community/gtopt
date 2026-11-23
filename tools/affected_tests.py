#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Select the tests (and lint targets) affected by a git change.

Reads ``git diff --name-only <base>...<head>`` (optionally plus the
uncommitted worktree) and maps every changed path to the smallest test
selection that still covers it:

* **C++ unit tests** (``gtoptTests``): changed ``test/source/**/test_*.cpp``
  files directly; changed headers via a lightweight ``#include`` graph of
  ``include/`` and ``test/source/`` (no compiler needed); changed
  ``source/**.cpp`` via their same-stem header plus a ``test_<stem>*``
  name heuristic.  Files that a large share of the test suite depends on
  ("core" files, build system, test main, solver plugins) escalate to the
  full suite and the reason is always printed.
* **C++ integration tests** (CTest ``e2e_*`` / ``cli_*`` / ...): a library
  change selects all of them; a ``cases/<dir>`` change selects only the
  entries registered for that case directory.
* **Python** (pytest): ``scripts/<pkg>`` -> ``scripts/<pkg>/tests`` plus
  every package that imports ``<pkg>`` (static, transitive import scan);
  ``guiservice/`` -> ``guiservice/tests``; ``tools/<x>.py`` ->
  ``tools/test_<x>.py``; packaging/config files escalate to the full
  Python suite.
* **Lints**: changed C++ files for clang-format / clang-tidy and the
  Python packages for ruff / pylint / mypy.

Usage::

    python tools/affected_tests.py                     # vs origin/master
    python tools/affected_tests.py --worktree -v       # + uncommitted, why
    python tools/affected_tests.py --format json
    python tools/affected_tests.py --run -B build      # build + run subset
    python tools/affected_tests.py --run --lint -B build

Exit status: 0 on success (or when ``--run`` passed), 1 when a selected
test or lint failed, 2 on usage / git errors.
"""

# pylint: disable=too-many-lines
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import resource
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Headers (by stem) that are always treated as "core": any change to the
#: header or to a ``source/`` file implementing it escalates the C++ unit
#: selection to the full suite, independently of the include fan-out.
CORE_STEMS: frozenset[str] = frozenset(
    {
        "linear_interface",
        "linear_problem",
        "planning_lp",
        "system_lp",
        "simulation_lp",
        "system_context",
        "basic_types",
        "json_planning",
        "json_system",
        "json_options",
        "gtopt_json_io",
        "solver_backend",
        "solver_registry",
        "lp_matrix",
        "flat_linear_problem",
    }
)

#: Default include fan-out above which a header counts as core.
DEFAULT_CORE_THRESHOLD = 0.5

#: Python selection covering at least this share of the packages is
#: reported (and run) as the full Python suite.
DEFAULT_PY_FULL_THRESHOLD = 0.75

#: Path globs that escalate the C++ unit suite AND the integration tests.
CPP_FULL_GLOBS: tuple[tuple[str, str], ...] = (
    ("CMakeLists.txt", "top-level build script"),
    ("all/*", "super-project build script"),
    ("cmake/*", "shared CMake module"),
    ("cmake_local/*", "shared CMake module"),
    ("test/CMakeLists.txt", "test build script"),
    ("test/source/main.cpp", "doctest main"),
    ("source/CMakeLists.txt", "library build script"),
    ("include/CMakeLists.txt", "library build script"),
    ("plugins/*", "LP solver plugin (every solve depends on it)"),
    ("vcpkg.json", "dependency manifest"),
)

#: Path globs that select every integration (non-doctest) CTest entry.
INTEGRATION_FULL_GLOBS: tuple[tuple[str, str], ...] = (
    ("integration_test/*", "integration-test harness"),
    ("standalone/*", "gtopt standalone binary"),
    ("tools/validate_solution.py", "e2e solution validator"),
    ("tools/validate_sddp_output.py", "SDDP e2e validator"),
    ("tools/gtopt_compare_csv.py", "e2e CSV comparator"),
    ("tools/check_case_conditioning.py", "numerics test driver"),
)

#: Path globs that escalate the Python suite to full.
PY_FULL_GLOBS: tuple[tuple[str, str], ...] = (
    ("scripts/pyproject.toml", "scripts packaging / pytest config"),
    ("scripts/conftest.py", "top-level pytest conftest"),
    ("scripts/requirements*.txt", "scripts dependencies"),
    ("scripts/CMakeLists.txt", "pytest CTest registration"),
    ("scripts/cmake/*", "pytest CTest registration"),
    ("pyproject.toml", "repository-wide Python tool config"),
    (".pylintrc", "pylint configuration"),
)

#: Paths that select everything (C++, integration, Python).
ALL_FULL_GLOBS: tuple[tuple[str, str], ...] = (
    (".github/workflows/ubuntu.yml", "CI workflow changed"),
    (".github/workflows/scripts.yml", "CI workflow changed"),
    (".github/workflows/guiservice.yml", "CI workflow changed"),
    (".github/workflows/tsan.yml", "CI workflow changed"),
    (".github/actions/*", "CI action changed"),
)

#: Paths with no tests of their own (documentation, other CI systems ...).
IGNORED_GLOBS: tuple[tuple[str, str], ...] = (
    ("*.md", "documentation"),
    ("*.rst", "documentation"),
    ("*.txt.license", "license metadata"),
    ("docs/*", "documentation"),
    ("documentation/*", "documentation"),
    ("LICENSE*", "license"),
    ("reports/*", "reports"),
    ("support/*", "support material"),
    ("emacs/*", "editor config"),
    ("profiling/*", "profiling material"),
    ("webservice/*", "covered by webservice.yml (npm)"),
    ("guiservice-plus/*", "covered by its own npm tests"),
    ("duckdb/*", "DuckDB helpers"),
    ("share/*", "installed data"),
    (".github/*", "other CI configuration"),
    (".clang-format", "format config (lint only)"),
    (".clang-tidy", "clang-tidy config (lint only)"),
    ("test/.clang-tidy", "clang-tidy config (lint only)"),
    (".gitignore", "git metadata"),
    (".gitattributes", "git metadata"),
    ("CHANGELOG*", "changelog"),
    ("TODO*", "notes"),
)

#: Test-data roots: a changed file under ``<root>/<name>/`` selects the
#: tests that mention ``<name>``.
DATA_ROOTS: tuple[str, ...] = ("cases", "scripts/cases", "tools/test_data")

#: CTest names that are Python unit runners (not integration tests).
PY_UNIT_CTEST_RE = re.compile(r"^scripts-(.*-unit|all-unit|install-deps)$")

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_]\w*)[\w.]*\s+import\b|import\s+([^#\n]+))",
    re.MULTILINE,
)
_CMAKE_CASE_RE = re.compile(
    r"^\s*(add_e2e_case|add_sddp_case|add_pandapower_comparison)\s*\(\s*"
    r"([A-Za-z0-9_]+)([^)]*)\)",
    re.MULTILINE,
)
_CASE_DIR_RE = re.compile(r"CASE_DIR\s+([A-Za-z0-9_]+)")
_CPP_SUFFIXES = (".cpp", ".hpp", ".h", ".cc", ".cxx", ".hxx")


def _match(path: str, globs: Iterable[tuple[str, str]]) -> str | None:
    """Return the reason of the first glob ``path`` matches, else ``None``.

    A glob ending in ``/*`` matches the whole subtree; a glob starting
    with ``*`` (``*.md``) matches the basename anywhere in the tree; any
    other glob matches the full repo-relative path.
    """
    for pattern, reason in globs:
        if pattern.endswith("/*"):
            if path.startswith(pattern[:-1]):
                return reason
        elif pattern.startswith("*"):
            if fnmatch.fnmatch(PurePosixPath(path).name, pattern):
                return reason
        elif fnmatch.fnmatch(path, pattern):
            return reason
    return None


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class Bucket:
    """A set of selected targets, each with the reasons it was selected."""

    full: bool = False
    full_reasons: list[str] = field(default_factory=list)
    targets: dict[str, list[str]] = field(default_factory=dict)

    def add(self, target: str, reason: str) -> None:
        """Select ``target`` because of ``reason``."""
        reasons = self.targets.setdefault(target, [])
        if reason not in reasons:
            reasons.append(reason)

    def escalate(self, reason: str) -> None:
        """Select the full suite because of ``reason``."""
        self.full = True
        if reason not in self.full_reasons:
            self.full_reasons.append(reason)

    @property
    def empty(self) -> bool:
        """True when nothing at all is selected."""
        return not self.full and not self.targets

    def to_json(self) -> dict[str, Any]:
        """JSON-serialisable view."""
        return {
            "full": self.full,
            "full_reasons": self.full_reasons,
            "targets": {k: self.targets[k] for k in sorted(self.targets)},
        }


@dataclass
class Selection:  # pylint: disable=too-many-instance-attributes
    """Everything selected for one change set."""

    changed: list[str] = field(default_factory=list)
    cpp_unit: Bucket = field(default_factory=Bucket)
    integration: Bucket = field(default_factory=Bucket)
    python: Bucket = field(default_factory=Bucket)
    python_deps: Bucket = field(default_factory=Bucket)
    guiservice: Bucket = field(default_factory=Bucket)
    tools: Bucket = field(default_factory=Bucket)
    ignored: dict[str, str] = field(default_factory=dict)
    lint_cpp: list[str] = field(default_factory=list)
    lint_py_files: list[str] = field(default_factory=list)
    lint_py_packages: dict[str, list[str]] = field(default_factory=dict)
    type_py_packages: dict[str, list[str]] = field(default_factory=dict)
    coverage_packages: list[str] = field(default_factory=list)
    tools_all: list[str] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """JSON-serialisable view."""
        return {
            "changed": self.changed,
            "cpp_unit": self.cpp_unit.to_json(),
            "integration": self.integration.to_json(),
            "python": self.python.to_json(),
            "python_deps": self.python_deps.to_json(),
            "guiservice": self.guiservice.to_json(),
            "tools": self.tools.to_json(),
            "ignored": self.ignored,
            "lint": {
                "cpp": self.lint_cpp,
                "cpp_tidy": tidy_files(self.lint_cpp),
                "py_files": self.lint_py_files,
                "py_packages": self.lint_py_packages,
                "py_type_packages": self.type_py_packages,
            },
            "coverage_packages": self.coverage_packages,
            "totals": self.totals,
        }


def tidy_files(files: Iterable[str]) -> list[str]:
    """The ``.cpp`` subset of ``files`` (clang-tidy never runs on headers)."""
    return [f for f in files if f.endswith(".cpp")]


# ---------------------------------------------------------------------------
# Repository model (include graph, Python import graph, case registry)
# ---------------------------------------------------------------------------


class Repo:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Lazily scanned static model of the repository."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._text: dict[str, str] = {}
        self._includes: dict[str, list[str]] | None = None
        self._closure: dict[str, frozenset[str]] = {}
        self._dependents: dict[str, set[str]] | None = None
        self._py_imports: dict[str, set[str]] | None = None
        self._py_test_imports: dict[str, set[str]] | None = None
        self._case_tests: dict[str, list[str]] | None = None

    # -- file helpers -------------------------------------------------------

    def exists(self, rel: str) -> bool:
        """True when ``rel`` exists in the working tree."""
        return (self.root / rel).is_file()

    def text(self, rel: str) -> str:
        """Cached file contents ('' when unreadable)."""
        if rel not in self._text:
            try:
                self._text[rel] = (self.root / rel).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                self._text[rel] = ""
        return self._text[rel]

    def glob(self, base: str, pattern: str) -> list[str]:
        """Sorted repo-relative paths under ``base`` matching ``pattern``."""
        top = self.root / base
        if not top.is_dir():
            return []
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in top.rglob(pattern)
            if p.is_file()
        )

    # -- C++ ------------------------------------------------------------------

    @property
    def cpp_tests(self) -> list[str]:
        """Every doctest translation unit (``test/source/**/test_*.cpp``)."""
        return [
            p
            for p in self.glob("test/source", "test_*.cpp")
            if "test_benchmark_" not in p
        ]

    @property
    def headers(self) -> list[str]:
        """Every project header (library + test helpers + private)."""
        out: list[str] = []
        for base in ("include", "test/source", "source"):
            for pat in ("*.hpp", "*.h"):
                out.extend(self.glob(base, pat))
        return sorted(set(out))

    @property
    def library_sources(self) -> list[str]:
        """Every library translation unit under ``source/``."""
        return self.glob("source", "*.cpp")

    def _resolve(self, includer: str, inc: str) -> str | None:
        """Resolve an ``#include`` spelling to a repo file, if it is ours."""
        candidates = []
        if inc.startswith("gtopt/"):
            candidates.append(f"include/{inc}")
        parent = PurePosixPath(includer).parent
        candidates.append(os.path.normpath((parent / inc).as_posix()))
        for cand in candidates:
            if self.exists(cand):
                return cand
        return None

    def includes(self) -> dict[str, list[str]]:
        """Direct project includes of every header / test / source file."""
        if self._includes is None:
            files = self.headers + self.cpp_tests + self.library_sources
            files.append("test/source/main.cpp")
            graph: dict[str, list[str]] = {}
            for rel in files:
                found = []
                for inc in _INCLUDE_RE.findall(self.text(rel)):
                    res = self._resolve(rel, inc)
                    if res is not None and res != rel:
                        found.append(res)
                graph[rel] = found
            self._includes = graph
        return self._includes

    def closure(self, rel: str) -> frozenset[str]:
        """Transitive project includes of ``rel`` (excluding itself).

        The walk does not descend into hub headers (:data:`CORE_STEMS`,
        e.g. ``system_lp.hpp`` which pulls in every element): a test that
        only reaches ``battery.hpp`` through ``system_lp.hpp`` is not
        selected for a ``battery.hpp`` change.  The hubs themselves are
        core, so changing one escalates to the full suite anyway.
        """
        if rel in self._closure:
            return self._closure[rel]
        graph = self.includes()
        seen: set[str] = set()
        stack = list(graph.get(rel, []))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if PurePosixPath(cur).stem in CORE_STEMS:
                continue  # hub: its own includes are not followed
            if cur in self._closure:
                seen.update(self._closure[cur])
                continue
            stack.extend(graph.get(cur, []))
        result = frozenset(seen)
        self._closure[rel] = result
        return result

    def dependents(self) -> dict[str, set[str]]:
        """header -> test files that include it (transitively)."""
        if self._dependents is None:
            dep: dict[str, set[str]] = {}
            for test in self.cpp_tests:
                for hdr in self.closure(test):
                    dep.setdefault(hdr, set()).add(test)
            self._dependents = dep
        return self._dependents

    def fanout(self, header: str) -> float:
        """Share of doctest files that (transitively) include ``header``."""
        total = len(self.cpp_tests) or 1
        return len(self.dependents().get(header, ())) / total

    def headers_by_stem(self, stem: str) -> list[str]:
        """Project headers named ``<stem>.hpp`` / ``<stem>.h``."""
        return [h for h in self.headers if PurePosixPath(h).stem == stem]

    def tests_by_name(self, stem: str) -> list[str]:
        """Doctest files named after ``stem`` (``test_<stem>*``)."""
        names = {stem}
        if stem.endswith("_lp") and len(stem) > 3:
            names.add(stem[:-3])
        out = []
        for test in self.cpp_tests:
            tstem = PurePosixPath(test).stem
            if any(tstem.startswith(f"test_{n}") for n in names):
                out.append(test)
        return out

    def header_for_source(self, src: str) -> list[str]:
        """Headers a ``source/**.cpp`` file implements (same / prefix stem)."""
        stem = PurePosixPath(src).stem
        same = self.headers_by_stem(stem)
        if same:
            return same
        # e.g. sddp_method_iteration.cpp -> sddp_method.hpp
        direct = self.includes().get(src, [])
        best: list[str] = []
        best_len = 0
        for hdr in direct:
            hstem = PurePosixPath(hdr).stem
            if stem.startswith(hstem + "_") and len(hstem) > best_len:
                best, best_len = [hdr], len(hstem)
        return best

    def sources_including(self, header: str) -> list[str]:
        """Library sources whose include closure contains ``header``."""
        return [s for s in self.library_sources if header in self.closure(s)]

    # -- integration cases ----------------------------------------------------

    def case_tests(self) -> dict[str, list[str]]:
        """cases/<dir> -> registered CTest name prefixes."""
        if self._case_tests is None:
            reg: dict[str, list[str]] = {}
            text = self.text("integration_test/CMakeLists.txt")
            text = re.sub(r"#[^\n]*", "", text)
            for func, name, rest in _CMAKE_CASE_RE.findall(text):
                m = _CASE_DIR_RE.search(rest)
                case_dir = m.group(1) if m else name
                suffix = "_sddp_" if func == "add_sddp_case" else "_"
                if func == "add_pandapower_comparison":
                    suffix = "_gtopt_compare"
                prefix = f"e2e_{name}{suffix}"
                if prefix not in reg.setdefault(case_dir, []):
                    reg[case_dir].append(prefix)
            self._case_tests = reg
        return self._case_tests

    # -- Python ---------------------------------------------------------------

    @property
    def py_packages(self) -> list[str]:
        """Top-level packages under ``scripts/`` (dirs with ``.py`` files)."""
        top = self.root / "scripts"
        if not top.is_dir():
            return []
        return sorted(
            d.name
            for d in top.iterdir()
            if d.is_dir()
            and d.name not in ("tests", "cases", "cmake")
            and not d.name.startswith((".", "_"))
            and any(d.glob("*.py"))
        )

    def py_test_dir(self, pkg: str) -> str | None:
        """``scripts/<pkg>/tests`` when it exists."""
        rel = f"scripts/{pkg}/tests"
        return rel if (self.root / rel).is_dir() else None

    def _imports_of(self, rel: str) -> set[str]:
        names: set[str] = set()
        for frm, imp in _IMPORT_RE.findall(self.text(rel)):
            if frm:
                names.add(frm)
            if imp:
                for part in imp.split(","):
                    tok = part.strip().split(" ")[0].split(".")[0]
                    if tok.isidentifier():
                        names.add(tok)
        return names

    def py_imports(self) -> dict[str, set[str]]:
        """unit -> scripts packages its *library* code imports.

        Units are ``scripts/<pkg>`` packages, ``guiservice`` and each
        ``tools/*.py`` file.  Imports made only from ``tests/`` folders
        live in :meth:`py_test_imports` -- they make the importing test
        dir a leaf consumer but do not propagate further.
        """
        if self._py_imports is None:
            self._scan_py()
        assert self._py_imports is not None
        return self._py_imports

    def py_test_imports(self) -> dict[str, set[str]]:
        """test unit -> scripts packages imported by its test files.

        Test units: ``<pkg>`` (its ``tests/``), ``scripts/tests``,
        ``guiservice`` (its tests) and ``tools:test_*`` files.
        """
        if self._py_test_imports is None:
            self._scan_py()
        assert self._py_test_imports is not None
        return self._py_test_imports

    def _scan_py(self) -> None:
        pkgs = set(self.py_packages)
        tools_mods = {PurePosixPath(p).stem for p in self.glob("tools", "*.py")}
        lib: dict[str, set[str]] = {}
        tst: dict[str, set[str]] = {}

        def scan(
            store: dict[str, set[str]], unit: str, files: list[str], known: set[str]
        ) -> None:
            acc = store.setdefault(unit, set())
            for rel in files:
                acc.update(self._imports_of(rel) & known)
            acc.discard(unit)

        for pkg in pkgs:
            files = self.glob(f"scripts/{pkg}", "*.py")
            scan(lib, pkg, [f for f in files if "/tests/" not in f], pkgs)
            scan(tst, pkg, [f for f in files if "/tests/" in f], pkgs)
        scan(tst, "scripts/tests", self.glob("scripts/tests", "*.py"), pkgs)
        gui = self.glob("guiservice", "*.py")
        scan(lib, "guiservice", [f for f in gui if "/tests/" not in f], pkgs)
        scan(tst, "guiservice", [f for f in gui if "/tests/" in f], pkgs)
        for rel in self.glob("tools", "*.py"):
            name = rel[len("tools/") :]
            if "/" in name:
                continue
            stem = PurePosixPath(name).stem
            store = tst if stem.startswith("test_") else lib
            scan(store, f"tools:{stem}", [rel], pkgs | tools_mods)
        self._py_imports = lib
        self._py_test_imports = tst

    def py_reverse_deps(self, pkg: str) -> dict[str, list[str]]:
        """Test units affected by a change in ``pkg`` -> import chain.

        Walks the library import graph backwards from ``pkg`` (transitive),
        then adds every test unit importing any reached package.  The
        package's own tests are not included (the caller adds them).
        """
        lib = self.py_imports()
        reached: dict[str, list[str]] = {pkg: [pkg]}
        frontier = [pkg]
        while frontier:
            cur = frontier.pop(0)
            for unit, deps in sorted(lib.items()):
                if unit not in reached and cur in deps:
                    reached[unit] = reached[cur] + [unit]
                    frontier.append(unit)
        out: dict[str, list[str]] = {}
        for unit, chain in reached.items():
            if unit != pkg:
                out[unit] = chain
        for unit, deps in sorted(self.py_test_imports().items()):
            if unit in out or unit == pkg:
                continue
            for dep in sorted(deps):
                if dep in reached:
                    out[unit] = reached[dep] + [f"{unit} (tests)"]
                    break
        return out

    def entry_point_modules(self) -> dict[str, list[str]]:
        """Repo path of each ``[project.scripts]`` module -> script names."""
        text = self.text("scripts/pyproject.toml")
        m = re.search(
            r"^\[project\.scripts\]\s*$(.*?)^\[", text, re.MULTILINE | re.DOTALL
        )
        out: dict[str, list[str]] = {}
        if not m:
            return out
        for line in m.group(1).splitlines():
            em = re.match(r'\s*([\w.-]+)\s*=\s*"([\w.]+):\w+"', line)
            if not em:
                continue
            mod = em.group(2).replace(".", "/")
            for cand in (f"scripts/{mod}.py", f"scripts/{mod}/__init__.py"):
                if self.exists(cand):
                    out.setdefault(cand, []).append(em.group(1))
        return out

    def ci_python_lists(self) -> tuple[set[str] | None, set[str] | None]:
        """(pylint, mypy) package lists of ``.github/workflows/scripts.yml``."""
        text = self.text(".github/workflows/scripts.yml")

        def grab(tool: str) -> set[str] | None:
            m = re.search(rf"run:\s*{tool}\s+([^\n]*)", text)
            if not m:
                return None
            return {
                t for t in m.group(1).split() if not t.startswith("-") and "=" not in t
            }

        return grab("pylint"), grab("mypy")

    def tests_mentioning(
        self, token: str, roots: Iterable[str], context: str
    ) -> list[str]:
        """Test files under ``roots`` that reference data dir ``token``.

        A reference is ``token`` spelled as a quoted string or a path
        segment (``"c0"``, ``'c0'``, ``/c0/``) in a file that also
        mentions ``context`` (the data root, e.g. ``cases``) -- a bare
        ``c0`` column name does not count.
        """
        pat = re.compile(rf"""(?:["'/]){re.escape(token)}(?:["'/.])""")
        out = []
        for root in roots:
            for rel in self.glob(root, "test_*"):
                if not rel.endswith((".py", ".cpp")):
                    continue
                text = self.text(rel)
                if context in text and pat.search(text):
                    out.append(rel)
        return out


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class Selector:
    """Map changed paths to a :class:`Selection`."""

    def __init__(
        self,
        repo: Repo,
        core_threshold: float = DEFAULT_CORE_THRESHOLD,
        py_full_threshold: float = DEFAULT_PY_FULL_THRESHOLD,
    ) -> None:
        self.repo = repo
        self.core_threshold = core_threshold
        self.py_full_threshold = py_full_threshold
        self.sel = Selection()

    # -- C++ helpers ----------------------------------------------------------

    def _core_reason(self, header: str) -> str | None:
        stem = PurePosixPath(header).stem
        if stem in CORE_STEMS:
            return f"{header} is a core header"
        share = self.repo.fanout(header)
        if share > self.core_threshold:
            return (
                f"{header} is included by {share:.0%} of the test files "
                f"(> {self.core_threshold:.0%})"
            )
        return None

    def _select_header(self, path: str, why: str) -> None:
        core = self._core_reason(path)
        if core:
            self.sel.cpp_unit.escalate(f"{why}: {core}")
            return
        tests = self.repo.dependents().get(path, set())
        for test in sorted(tests):
            self.sel.cpp_unit.add(test, f"{why} (includes {path})")
        for test in self.repo.tests_by_name(PurePosixPath(path).stem):
            self.sel.cpp_unit.add(test, f"{why} (name match)")
        if tests:
            return
        # Header not included by any test: follow the sources using it.
        for src in self.repo.sources_including(path):
            self._select_source(src, f"{why} via {src}", depth=1)

    def _select_source(self, path: str, why: str, depth: int = 0) -> None:
        headers = self.repo.header_for_source(path)
        for hdr in headers:
            core = self._core_reason(hdr)
            if core:
                self.sel.cpp_unit.escalate(f"{why}: implements {core}")
                return
        mapped = False
        for hdr in headers:
            for test in sorted(self.repo.dependents().get(hdr, set())):
                self.sel.cpp_unit.add(test, f"{why} (implements {hdr})")
                mapped = True
        for test in self.repo.tests_by_name(PurePosixPath(path).stem):
            self.sel.cpp_unit.add(test, f"{why} (name match)")
            mapped = True
        if not mapped and depth == 0:
            self.sel.cpp_unit.escalate(f"{why}: no test maps to this source")

    # -- Python helpers -------------------------------------------------------

    def _select_py_package(self, pkg: str, path: str) -> None:
        why = f"{path} changed"
        self.sel.lint_py_packages.setdefault(pkg, []).append(path)
        self.sel.type_py_packages.setdefault(pkg, []).append(path)
        tdir = self.repo.py_test_dir(pkg)
        if tdir:
            self.sel.python.add(tdir, why)
            self.sel.coverage_packages.append(pkg)
        for unit, chain in self.repo.py_reverse_deps(pkg).items():
            via = " -> ".join(reversed(chain))
            reason = f"{why} (imports: {via})"
            if unit == "guiservice":
                self.sel.guiservice.add("guiservice/tests", reason)
            elif unit == "scripts/tests":
                self.sel.python_deps.add("scripts/tests", reason)
            elif unit.startswith("tools:"):
                test = f"tools/test_{unit[6:]}.py"
                if unit[6:].startswith("test_"):
                    test = f"tools/{unit[6:]}.py"
                if self.repo.exists(test):
                    self.sel.tools.add(test, reason)
            else:
                if not chain[-1].endswith("(tests)"):
                    self.sel.type_py_packages.setdefault(unit, []).append(
                        f"imports {pkg}"
                    )
                rdir = self.repo.py_test_dir(unit)
                if rdir:
                    self.sel.python_deps.add(rdir, reason)
        eps = self.repo.entry_point_modules()
        if path in eps:
            self.sel.python.add(
                "scripts/tests/test_cli_help.py",
                f"{path} is a [project.scripts] entry point ({', '.join(eps[path])})",
            )

    def _select_data(self, path: str) -> bool:
        for root in DATA_ROOTS:
            if not path.startswith(root + "/"):
                continue
            rest = path[len(root) + 1 :]
            name = rest.split("/")[0]
            if "/" not in rest:
                name = PurePosixPath(rest).name
            ctx = PurePosixPath(root).name
            why = f"{path} changed ({root}/{name} referenced)"
            repo = self.repo
            for test in repo.tests_mentioning(name, ["test/source"], ctx):
                self.sel.cpp_unit.add(test, why)
            pydirs = sorted(
                {
                    str(PurePosixPath(p).parent)
                    for p in repo.glob("scripts", "test_*.py")
                    if "/tests/" in p or p.startswith("scripts/tests/")
                }
            )
            for test in repo.tests_mentioning(name, pydirs, ctx):
                self.sel.python.add(test, why)
            for test in repo.tests_mentioning(name, ["tools"], ctx):
                if "/" not in test[len("tools/") :]:
                    self.sel.tools.add(test, why)
            for test in repo.tests_mentioning(name, ["guiservice/tests"], ctx):
                self.sel.guiservice.add(test, why)
            if root == "cases":
                for prefix in repo.case_tests().get(name, []):
                    self.sel.integration.add(f"^{prefix}", f"{path} changed")
            return True
        return False

    # -- main dispatch --------------------------------------------------------

    def classify(self, path: str) -> None:
        # pylint: disable=too-many-branches,too-many-statements,too-many-return-statements
        """Route one changed path to the right buckets."""
        sel = self.sel
        why = f"{path} changed"
        is_cpp = path.endswith(_CPP_SUFFIXES)
        if is_cpp and self.repo.exists(path):
            sel.lint_cpp.append(path)
        if path.endswith(".py") and self.repo.exists(path):
            sel.lint_py_files.append(path)

        reason = _match(path, ALL_FULL_GLOBS)
        if reason:
            full_selection(sel, f"{path}: {reason}")
            return
        reason = _match(path, CPP_FULL_GLOBS)
        if reason:
            sel.cpp_unit.escalate(f"{path}: {reason}")
            sel.integration.escalate(f"{path}: {reason}")
            return
        reason = _match(path, INTEGRATION_FULL_GLOBS)
        if reason:
            sel.integration.escalate(f"{path}: {reason}")
            if path.startswith("tools/"):
                self._select_tools(path)
            return
        reason = _match(path, PY_FULL_GLOBS)
        if reason:
            sel.python.escalate(f"{path}: {reason}")
            if path in ("pyproject.toml", ".pylintrc"):
                sel.guiservice.escalate(f"{path}: {reason}")
            return
        if self._select_data(path):
            return
        reason = _match(path, IGNORED_GLOBS)
        if reason:
            sel.ignored[path] = reason
            return

        if path.startswith("test/"):
            if path.startswith("test/source/") and is_cpp:
                name = PurePosixPath(path).name
                if name.startswith("test_") and path.endswith(".cpp"):
                    if self.repo.exists(path):
                        sel.cpp_unit.add(path, "test file changed")
                    else:
                        sel.ignored[path] = "deleted test file"
                    return
                if path.endswith((".hpp", ".h")):
                    self._select_header(path, why)
                    return
            sel.cpp_unit.escalate(f"{path}: unrecognised test-tree file")
            return
        if path.startswith(("include/", "source/")):
            sel.integration.escalate(f"{path}: library changed (e2e smoke)")
            if path.endswith((".hpp", ".h")):
                if self.repo.exists(path):
                    self._select_header(path, why)
                else:
                    for test in self.repo.tests_by_name(PurePosixPath(path).stem):
                        sel.cpp_unit.add(test, f"{why} (deleted header, name match)")
                return
            if path.endswith(".cpp"):
                self._select_source(path, why)
                return
            sel.cpp_unit.escalate(f"{path}: unrecognised library file")
            return

        if path.startswith("scripts/"):
            parts = path.split("/")
            if parts[1] == "tests":
                if parts[-1].startswith("test_") and path.endswith(".py"):
                    sel.python.add(path, "test file changed")
                else:
                    sel.python.add("scripts/tests", why)
                return
            pkg = parts[1]
            if len(parts) > 2 and pkg in self.repo.py_packages:
                if parts[2] == "tests":
                    sel.lint_py_packages.setdefault(pkg, []).append(path)
                    if parts[-1].startswith("test_") and path.endswith(".py"):
                        if self.repo.exists(path):
                            sel.python.add(path, "test file changed")
                    else:
                        tdir = self.repo.py_test_dir(pkg)
                        if tdir:
                            sel.python.add(tdir, f"{why} (test support file)")
                    return
                self._select_py_package(pkg, path)
                return
            sel.ignored[path] = "scripts/ file outside any package"
            return

        if path.startswith("guiservice/"):
            name = PurePosixPath(path).name
            if (
                path.startswith("guiservice/tests/")
                and name.startswith("test_")
                and path.endswith(".py")
                and self.repo.exists(path)
            ):
                sel.guiservice.add(path, "test file changed")
                return
            sel.guiservice.add("guiservice/tests", why)
            return

        if path.startswith("tools/"):
            self._select_tools(path)
            return

        sel.ignored[path] = "no test mapping"

    def _select_tools(self, path: str) -> None:
        sel = self.sel
        rest = path[len("tools/") :]
        if "/" in rest or not rest.endswith(".py"):
            sel.ignored[path] = "tools/ helper (no unit tests)"
            return
        stem = PurePosixPath(rest).stem
        if stem == "conftest":
            for test in self.repo.glob("tools", "test_*.py"):
                if "/" not in test[len("tools/") :]:
                    sel.tools.add(test, f"{path} changed")
            return
        if stem.startswith("test_"):
            if self.repo.exists(path):
                sel.tools.add(path, "test file changed")
            return
        found = False
        test = f"tools/test_{stem}.py"
        if self.repo.exists(test):
            sel.tools.add(test, f"{path} changed (name match)")
            found = True
        for unit, deps in self.repo.py_test_imports().items():
            if unit.startswith("tools:test_") and stem in deps:
                sel.tools.add(f"tools/{unit[6:]}.py", f"{path} changed (imported)")
                found = True
        if not found:
            sel.ignored[path] = "tools/ script without tests"

    def finish(self) -> Selection:  # pylint: disable=too-many-branches
        """Post-process: thresholds, totals, coverage packages."""
        sel = self.sel
        repo = self.repo
        pkgs = repo.py_packages
        test_dirs = [d for d in map(repo.py_test_dir, pkgs) if d]
        if (repo.root / "scripts/tests").is_dir():
            test_dirs.append("scripts/tests")
        # A whole test dir subsumes individual files inside it.
        for bucket in (sel.python, sel.guiservice):
            dirs = [t for t in bucket.targets if not t.endswith(".py")]
            for tgt in list(bucket.targets):
                parent = next((d for d in dirs if tgt.startswith(d + "/")), None)
                if parent is not None:
                    for reason in bucket.targets.pop(tgt):
                        bucket.add(parent, reason)
        if sel.python.full:
            for reason in sel.python.full_reasons:
                sel.python_deps.escalate(reason)
        for tgt in list(sel.python_deps.targets):
            files = [f for f in sel.python.targets if f.startswith(tgt + "/")]
            if files:
                # A changed test file inside a dependent dir: run the whole
                # dir as a direct target instead of twice.
                for reason in sel.python_deps.targets[tgt]:
                    sel.python.add(tgt, reason)
                for f in files:
                    for reason in sel.python.targets.pop(f):
                        sel.python.add(tgt, reason)
            if tgt in sel.python.targets:
                del sel.python_deps.targets[tgt]
        touched = {
            t for t in [*sel.python.targets, *sel.python_deps.targets] if t in test_dirs
        }
        if (
            not sel.python_deps.full
            and test_dirs
            and len(touched) >= self.py_full_threshold * len(test_dirs)
        ):
            sel.python_deps.escalate(
                f"selection covers {len(touched)} of {len(test_dirs)} test dirs "
                f"(>= {self.py_full_threshold:.0%})"
            )
        if sel.python.full:
            sel.coverage_packages = [p for p in pkgs if repo.py_test_dir(p)]
        else:
            sel.coverage_packages = sorted(set(sel.coverage_packages))
        pylint_ci, mypy_ci = repo.ci_python_lists()
        if pylint_ci is not None:
            sel.lint_py_packages = {
                k: v for k, v in sel.lint_py_packages.items() if k in pylint_ci
            }
        if mypy_ci is not None:
            sel.type_py_packages = {
                k: v for k, v in sel.type_py_packages.items() if k in mypy_ci
            }
        sel.tools_all = [
            t for t in repo.glob("tools", "test_*.py") if "/" not in t[len("tools/") :]
        ]
        sel.totals = {
            "cpp_test_files": len(repo.cpp_tests),
            "py_test_dirs": len(test_dirs),
            "guiservice_test_files": len(repo.glob("guiservice/tests", "test_*.py")),
            "tools_test_files": len(sel.tools_all),
        }
        return sel

    def select(self, changed: Iterable[str]) -> Selection:
        """Classify every changed path and return the selection."""
        self.sel.changed = sorted(set(changed))
        for path in self.sel.changed:
            self.classify(path)
        return self.finish()


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    """Run git in ``root``; raise ``RuntimeError`` on failure."""
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def changed_files(
    root: Path, base: str, head: str, two_dot: bool, worktree: bool
) -> list[str]:
    """Paths changed between ``base`` and ``head`` (+ worktree if asked)."""
    rng = f"{base}..{head}" if two_dot else f"{base}...{head}"
    out = set(git(root, "diff", "--name-only", "--no-renames", rng).split())
    if worktree:
        out.update(git(root, "diff", "--name-only", "--no-renames", "HEAD").split())
        out.update(git(root, "ls-files", "--others", "--exclude-standard").split())
    return sorted(out)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def sf_filter(tests: Iterable[str]) -> str:
    """doctest ``--source-file`` filter for the given test files."""
    return ",".join(f"*/{t.removeprefix('test/')}" for t in sorted(tests))


def ctest_regex(bucket: Bucket) -> str:
    """CTest ``-R`` regex for the integration bucket ('' when full)."""
    if bucket.full:
        return ""
    return "|".join(sorted(bucket.targets))


def pytest_args(bucket: Bucket) -> list[str]:
    """pytest targets relative to ``scripts/`` (empty for the full suite)."""
    return [t.removeprefix("scripts/") for t in sorted(bucket.targets)]


def _state(bucket: Bucket) -> str:
    if bucket.full:
        return "full"
    return "subset" if bucket.targets else "none"


PY_MARKER = ("-m", "not integration and not wheel")
PY_IGNORE = ("--ignore=plp2gtopt/tests/test_integration.py",)


def resolve_engine(requested: str, root: Path) -> str:
    """Python selection engine: ``testmon`` or ``static``.

    ``auto`` picks testmon when pytest-testmon is importable and a
    ``scripts/.testmondata`` database exists (without one testmon would
    run -- and record -- everything).
    """
    if requested != "auto":
        return requested
    has_plugin = importlib.util.find_spec("testmon") is not None
    has_db = (root / "scripts" / ".testmondata").is_file()
    return "testmon" if has_plugin and has_db else "static"


@dataclass
class Command:
    """One test command: working dir (repo-relative) and argv."""

    label: str
    cwd: str
    argv: list[str]

    def shell(self) -> str:
        """Copy-pasteable form."""
        argv = ["python" if a == sys.executable else a for a in self.argv]
        if self.cwd in ("", "."):
            return shlex.join(argv)
        return f"(cd {self.cwd} && {shlex.join(argv)})"


def python_commands(sel: Selection, engine: str, workers: str = "") -> list[Command]:
    """pytest invocations for the Python buckets.

    * ``python`` (direct): own test dirs of the changed packages, changed
      test files, data-referencing tests -- always run in full (CI gates
      their coverage).
    * ``python_deps``: test dirs of reverse dependencies.  With the
      testmon engine they run through ``--testmon --testmon-forceselect``
      (only tests whose recorded coverage touches a changed block);
      otherwise in full.
    """
    py = sys.executable
    xdist = ["-n", workers] if workers else []
    base = [py, "-m", "pytest", "-q", *xdist, *PY_MARKER, *PY_IGNORE]
    tm_collect = ["--testmon"] if engine == "testmon" else []
    tm_select = ["--testmon", "--testmon-forceselect"] if engine == "testmon" else []
    cmds: list[Command] = []
    if sel.python.full:
        cmds.append(Command("pytest scripts (full)", "scripts", base + tm_collect))
    else:
        if sel.python.targets:
            cmds.append(
                Command(
                    "pytest scripts (direct)",
                    "scripts",
                    base + tm_collect + pytest_args(sel.python),
                )
            )
        if not sel.python_deps.empty:
            targets = [] if sel.python_deps.full else pytest_args(sel.python_deps)
            label = f"pytest scripts (dependents, {engine})"
            cmds.append(Command(label, "scripts", base + tm_select + targets))
    if not sel.guiservice.empty:
        targets = (
            ["guiservice/tests"]
            if sel.guiservice.full
            else sorted(sel.guiservice.targets)
        )
        cmds.append(
            Command(
                "pytest guiservice", ".", [py, "-m", "pytest", "-q", *xdist, *targets]
            )
        )
    tools = sorted(sel.tools.targets)
    if sel.tools.full:
        tools = sel.tools_all
    if tools:
        names = [t.removeprefix("tools/") for t in tools]
        cmds.append(
            Command("pytest tools", "tools", [py, "-m", "pytest", "-q", *names])
        )
    return cmds


def render_text(sel: Selection, verbose: bool, engine: str = "static") -> str:
    """Human-readable report."""
    lines = [f"changed files: {len(sel.changed)}"]
    if verbose:
        lines.extend(f"  {p}" for p in sel.changed)
    tot = sel.totals

    def section(title: str, bucket: Bucket, total: int | None) -> None:
        state = _state(bucket)
        size = (
            "ALL"
            if bucket.full
            else f"{len(bucket.targets)}" + (f"/{total}" if total else "")
        )
        lines.append(f"\n{title}: {state} ({size})")
        for reason in bucket.full_reasons:
            lines.append(f"  ESCALATED TO FULL: {reason}")
        if bucket.full and not verbose:
            return
        for tgt in sorted(bucket.targets):
            lines.append(f"  {tgt}")
            if verbose:
                lines.extend(f"      <- {r}" for r in bucket.targets[tgt])

    section("C++ unit (gtoptTests)", sel.cpp_unit, tot.get("cpp_test_files"))
    section("C++ integration (ctest)", sel.integration, None)
    section("Python direct (scripts pytest)", sel.python, tot.get("py_test_dirs"))
    section(
        f"Python dependents (scripts pytest, engine={engine})",
        sel.python_deps,
        tot.get("py_test_dirs"),
    )
    section("guiservice (pytest)", sel.guiservice, tot.get("guiservice_test_files"))
    section("tools (pytest)", sel.tools, tot.get("tools_test_files"))

    lines.append("\nlint:")
    lines.append(f"  clang-format: {' '.join(sel.lint_cpp) or '-'}")
    lines.append(f"  clang-tidy:   {' '.join(tidy_files(sel.lint_cpp)) or '-'}")
    lines.append(f"  ruff:         {' '.join(sel.lint_py_files) or '-'}")
    lines.append(f"  pylint:       {' '.join(sorted(sel.lint_py_packages)) or '-'}")
    lines.append(f"  mypy:         {' '.join(sorted(sel.type_py_packages)) or '-'}")
    if sel.coverage_packages:
        lines.append(f"  coverage gate: {' '.join(sel.coverage_packages)}")
    if sel.ignored:
        lines.append(f"\nno tests needed: {len(sel.ignored)} file(s)")
        if verbose:
            lines.extend(f"  {p}: {r}" for p, r in sorted(sel.ignored.items()))

    lines.append("\ncommands:")
    lines.extend(f"  {c}" for c in suggested_commands(sel, engine=engine))
    return "\n".join(lines)


def suggested_commands(
    sel: Selection, build_dir: str = "build", engine: str = "static"
) -> list[str]:
    """Commands that run the selection by hand."""
    cmds: list[str] = []
    if sel.cpp_unit.full:
        cmds.append(f"ulimit -c unlimited; {build_dir}/test/gtoptTests")
    elif sel.cpp_unit.targets:
        cmds.append(
            f"ulimit -c unlimited; {build_dir}/test/gtoptTests "
            f"-sf={shlex.quote(sf_filter(sel.cpp_unit.targets))}"
        )
    if sel.integration.full:
        cmds.append(
            f"ulimit -c unlimited; ctest --test-dir {build_dir} -j8 "
            "-E '^scripts-' (integration entries; --run selects them exactly)"
        )
    elif sel.integration.targets:
        cmds.append(
            f"ulimit -c unlimited; ctest --test-dir {build_dir} -j8 "
            f"-R {shlex.quote(ctest_regex(sel.integration))}"
        )
    cmds.extend(c.shell() for c in python_commands(sel, engine, "auto"))
    return cmds


def render_shell(sel: Selection) -> str:
    """``eval``-able shell assignments."""
    pairs = {
        "AFFECTED_CPP": _state(sel.cpp_unit),
        "AFFECTED_CPP_SF": sf_filter(sel.cpp_unit.targets),
        "AFFECTED_CPP_FILES": " ".join(sorted(sel.cpp_unit.targets)),
        "AFFECTED_INTEGRATION": _state(sel.integration),
        "AFFECTED_CTEST_REGEX": ctest_regex(sel.integration),
        "AFFECTED_PY": _state(sel.python),
        "AFFECTED_PYTEST": " ".join(pytest_args(sel.python)),
        "AFFECTED_PY_DEPS": _state(sel.python_deps),
        "AFFECTED_PYTEST_DEPS": " ".join(pytest_args(sel.python_deps)),
        "AFFECTED_GUI": _state(sel.guiservice),
        "AFFECTED_GUI_TESTS": " ".join(sorted(sel.guiservice.targets)),
        "AFFECTED_TOOLS": _state(sel.tools),
        "AFFECTED_TOOLS_TESTS": " ".join(sorted(sel.tools.targets)),
        "AFFECTED_LINT_CPP": " ".join(sel.lint_cpp),
        "AFFECTED_LINT_TIDY": " ".join(tidy_files(sel.lint_cpp)),
        "AFFECTED_LINT_PY_FILES": " ".join(sel.lint_py_files),
        "AFFECTED_PYLINT": " ".join(sorted(sel.lint_py_packages)),
        "AFFECTED_MYPY": " ".join(sorted(sel.type_py_packages)),
        "AFFECTED_COVERAGE_PACKAGES": " ".join(sel.coverage_packages),
    }
    return "\n".join(f"{k}={shlex.quote(v)}" for k, v in pairs.items())


def render_github(sel: Selection) -> str:
    """``key=value`` lines for ``$GITHUB_OUTPUT`` (lower-case keys)."""
    out = []
    for line in render_shell(sel).splitlines():
        key, _, val = line.partition("=")
        name = key.removeprefix("AFFECTED_").lower()
        out.append(f"{name}={shlex.split(val)[0] if val else ''}")
    return "\n".join(out)


def render_markdown(sel: Selection, title: str = "Affected tests") -> str:
    """Markdown summary (for ``$GITHUB_STEP_SUMMARY``)."""
    tot = sel.totals
    rows = [
        ("C++ unit", sel.cpp_unit, tot.get("cpp_test_files")),
        ("C++ integration", sel.integration, None),
        ("Python (direct)", sel.python, tot.get("py_test_dirs")),
        ("Python (dependents)", sel.python_deps, tot.get("py_test_dirs")),
        ("guiservice", sel.guiservice, tot.get("guiservice_test_files")),
        ("tools", sel.tools, tot.get("tools_test_files")),
    ]
    md = [f"## {title}", "", f"{len(sel.changed)} changed file(s).", ""]
    md += ["| suite | mode | selected |", "|---|---|---|"]
    for name, bucket, total in rows:
        size = "all" if bucket.full else str(len(bucket.targets))
        if total and not bucket.full:
            size += f" / {total}"
        md.append(f"| {name} | {_state(bucket)} | {size} |")
    md.append("")
    for name, bucket, _ in rows:
        if bucket.empty:
            continue
        md.append(f"<details><summary>{name}: {_state(bucket)}</summary>\n")
        for reason in bucket.full_reasons:
            md.append(f"- **full**: {reason}")
        for tgt in sorted(bucket.targets):
            md.append(f"- `{tgt}` &larr; {'; '.join(bucket.targets[tgt])}")
        md.append("\n</details>\n")
    if sel.ignored:
        md.append(f"{len(sel.ignored)} changed file(s) need no tests.\n")
    return "\n".join(md)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def enable_core_dumps() -> None:
    """``ulimit -c unlimited`` for this process and its children."""
    try:
        _, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (hard, hard))
    except (ValueError, OSError) as exc:  # pragma: no cover - platform
        print(f"warning: cannot raise RLIMIT_CORE: {exc}", file=sys.stderr)


def _run(cmd: list[str], cwd: Path, dry: bool) -> int:
    print(f"+ (cd {cwd} && {shlex.join(cmd)})", flush=True)
    if dry:
        return 0
    return subprocess.run(cmd, cwd=cwd, check=False).returncode


def parse_doctest_list(output: str) -> list[str]:
    """Test-case names from ``gtoptTests --list-test-cases`` output."""
    return [
        line
        for line in output.splitlines()
        if line and not line.startswith(("[doctest]", "===="))
    ]


def doctest_names(binary: Path, sf: str | None) -> list[str]:
    """Test-case names ``gtoptTests --list-test-cases`` reports."""
    cmd = [str(binary), "--list-test-cases"]
    if sf:
        cmd.append(f"-sf={sf}")
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return parse_doctest_list(out)


def ctest_names(build: Path) -> list[str]:
    """Every test name registered in ``build``."""
    out = subprocess.run(
        ["ctest", "--test-dir", str(build), "--show-only=json-v1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [t["name"] for t in json.loads(out).get("tests", [])]


def select_integration(
    sel: Selection, all_names: Iterable[str], unit_names: set[str]
) -> list[str]:
    """Registered non-doctest, non-Python-unit CTest names selected."""
    if sel.integration.empty:
        return []
    names = [
        n for n in all_names if n not in unit_names and not PY_UNIT_CTEST_RE.match(n)
    ]
    if sel.integration.full:
        return names
    pat = re.compile(ctest_regex(sel.integration))
    return [n for n in names if pat.search(n)]


def run_cpp(  # pylint: disable=too-many-locals
    sel: Selection, args: argparse.Namespace, root: Path
) -> list[str]:
    """Build and run the C++ selection through ``ctest --tests-from-file``."""
    dry = args.dry_run
    build = Path(args.build_dir).resolve()
    jobs = str(args.jobs)
    if not args.no_build:
        target = [] if not sel.integration.empty else ["--target", "gtoptTests"]
        if _run(["cmake", "--build", str(build), "-j", jobs, *target], root, dry):
            return ["build"]
    if dry:
        print("+ (dry-run) list doctest + integration names, ctest --tests-from-file")
        return []
    binary = build / "test" / "gtoptTests"
    unit: list[str] = []
    if sel.cpp_unit.full:
        unit = doctest_names(binary, None)
    elif sel.cpp_unit.targets:
        unit = doctest_names(binary, sf_filter(sel.cpp_unit.targets))
    registered = ctest_names(build)
    names, direct = split_registered(unit, registered)
    if not sel.integration.empty:
        all_unit = set(doctest_names(binary, None)) if binary.exists() else set()
        names += select_integration(sel, registered, all_unit)
    failures: list[str] = []
    if names:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", prefix="affected-", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("\n".join(names) + "\n")
            listfile = fh.name
        print(f"running {len(names)} ctest entries (list: {listfile})")
        cmd = [
            "ctest",
            "--test-dir",
            str(build),
            "--tests-from-file",
            listfile,
            "-j",
            jobs,
            "--output-on-failure",
            *args.ctest_arg,
        ]
        if _run(cmd, root, dry):
            failures.append("ctest")
    if direct:
        # Test cases CTest cannot address by name (doctest_discover_tests
        # splits names containing ';'): run them through gtoptTests.
        print(f"running {len(direct)} test case(s) directly (not addressable in ctest)")
        cmd = [str(binary), f"--test-case={doctest_case_filter(direct)}"]
        if _run(cmd, build / "test", dry):
            failures.append("gtoptTests")
    if not names and not direct:
        print("no registered ctest entries selected")
    return failures


def split_registered(
    unit: Iterable[str], registered: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Dedupe doctest names; split into CTest-registered and the rest."""
    known = set(registered)
    seen: set[str] = set()
    in_ctest: list[str] = []
    direct: list[str] = []
    for name in unit:
        if name in seen:
            continue
        seen.add(name)
        (in_ctest if name in known else direct).append(name)
    return in_ctest, direct


def doctest_case_filter(names: Iterable[str]) -> str:
    """``--test-case`` value matching exactly ``names`` (',' escaped)."""
    return ",".join(n.replace(",", "\\,") for n in names)


def run_selection(sel: Selection, args: argparse.Namespace, root: Path) -> int:
    """Build what is needed and run the selection; return an exit code."""
    enable_core_dumps()
    suites = {x.strip() for x in args.suites.split(",") if x.strip()}
    failures: list[str] = []
    if "cpp" in suites and (not sel.cpp_unit.empty or not sel.integration.empty):
        failures += run_cpp(sel, args, root)
    engine = resolve_engine(args.py_engine, root)
    for cmd in python_commands(sel, engine, args.pytest_workers):
        suite = cmd.label.split()[1]  # "pytest <suite> ..."
        suite = "python" if suite == "scripts" else suite
        if suite not in suites:
            continue
        if _run(cmd.argv, root / cmd.cwd, args.dry_run):
            failures.append(cmd.label)
    if args.lint:
        failures += run_lints(sel, args, root)
    if failures:
        print(f"\nFAILED: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("\nall selected tests passed")
    return 0


def run_lints(sel: Selection, args: argparse.Namespace, root: Path) -> list[str]:
    """Run the scoped lints; return the names of the failing ones."""
    dry = args.dry_run
    failed: list[str] = []
    fmt = ["clang-format", "--dry-run", "-Werror", *sel.lint_cpp]
    if sel.lint_cpp and _run(fmt, root, dry):
        failed.append("clang-format")
    tidy = tidy_files(sel.lint_cpp)
    if tidy:
        cmd = ["bash", "tools/run_tidy.sh", "-p", args.build_dir, *tidy]
        if _run(cmd, root, dry):
            failed.append("clang-tidy")
    if sel.lint_py_files:
        if _run(["ruff", "format", "--check", *sel.lint_py_files], root, dry):
            failed.append("ruff format")
        if _run(["ruff", "check", *sel.lint_py_files], root, dry):
            failed.append("ruff check")
    if sel.lint_py_packages:
        pkgs = sorted(sel.lint_py_packages)
        cmd = [sys.executable, "-m", "pylint", "--jobs=0", *pkgs]
        if _run(cmd, root / "scripts", dry):
            failed.append("pylint")
    if sel.type_py_packages:
        pkgs = sorted(sel.type_py_packages)
        cmd = [sys.executable, "-m", "mypy", *pkgs, "--ignore-missing-imports"]
        if _run(cmd, root / "scripts", dry):
            failed.append("mypy")
    return failed


# ---------------------------------------------------------------------------
# Coverage filter (affected-mode coverage gate)
# ---------------------------------------------------------------------------


def filter_coverage(src: Path, dst: Path, packages: Iterable[str]) -> int:
    """Keep only files of ``packages`` in a coverage.py JSON report.

    Used in affected CI runs: per-package thresholds are enforced only for
    the packages whose own test directory ran (the total is meaningless
    for a partial run).  Returns the number of files kept.
    """
    keep = set(packages)
    report = json.loads(src.read_text(encoding="utf-8"))
    files = report.get("files", {})
    kept = {
        path: data
        for path, data in files.items()
        if path.replace("\\", "/").lstrip("./").split("/")[0] in keep
    }
    report["files"] = kept
    dst.write_text(json.dumps(report), encoding="utf-8")
    return len(kept)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    p = argparse.ArgumentParser(
        prog="affected_tests.py",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See tools/README.md (affected_tests.py) for the mapping rules.",
    )
    p.add_argument("--base", default="origin/master", help="base ref [origin/master]")
    p.add_argument("--head", default="HEAD", help="head ref [HEAD]")
    p.add_argument(
        "--two-dot",
        action="store_true",
        help="diff base..head instead of base...head (shallow CI clones)",
    )
    p.add_argument(
        "--worktree",
        action="store_true",
        help="also include uncommitted and untracked files",
    )
    p.add_argument(
        "--files",
        nargs="*",
        metavar="PATH",
        help="use these repo-relative paths instead of git diff",
    )
    p.add_argument("--full", action="store_true", help="force the full selection")
    p.add_argument("--repo", type=Path, help="repository root [git toplevel]")
    p.add_argument(
        "--format",
        choices=("text", "shell", "json", "github", "markdown"),
        default="text",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="explain each pick")
    p.add_argument(
        "--summary", type=Path, help="append a markdown summary to this file"
    )
    p.add_argument(
        "--core-threshold",
        type=float,
        default=DEFAULT_CORE_THRESHOLD,
        help="include fan-out above which a header escalates to full [0.5]",
    )
    p.add_argument(
        "--py-full-threshold",
        type=float,
        default=DEFAULT_PY_FULL_THRESHOLD,
        help="share of Python test dirs that counts as full [0.75]",
    )
    run = p.add_argument_group("running")
    run.add_argument("--run", action="store_true", help="build + run the selection")
    run.add_argument("--lint", action="store_true", help="with --run: scoped lints")
    run.add_argument("-B", "--build-dir", default="build", help="CMake build dir")
    run.add_argument("--no-build", action="store_true", help="skip cmake --build")
    run.add_argument("-j", "--jobs", type=int, default=8, help="parallelism [8]")
    run.add_argument(
        "-n",
        "--pytest-workers",
        default="",
        help="pytest-xdist workers (e.g. auto); default: no xdist",
    )
    run.add_argument("--dry-run", action="store_true", help="print, do not run")
    run.add_argument(
        "--suites",
        default="cpp,python,guiservice,tools",
        help="comma list of suites --run executes [cpp,python,guiservice,tools]",
    )
    run.add_argument(
        "--ctest-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument for ctest (repeatable, e.g. --ctest-arg=-E ...)",
    )
    run.add_argument(
        "--py-engine",
        choices=("auto", "testmon", "static"),
        default="auto",
        help="Python dependents: testmon-narrowed or static [auto: testmon "
        "when pytest-testmon and scripts/.testmondata exist]",
    )
    cov = p.add_argument_group("coverage filter (CI)")
    cov.add_argument(
        "--filter-coverage",
        nargs=2,
        type=Path,
        metavar=("IN", "OUT"),
        help="write a coverage.json restricted to --coverage-packages, then exit",
    )
    cov.add_argument(
        "--coverage-packages",
        default="",
        help="space/comma separated package list for --filter-coverage",
    )
    return p


def full_selection(sel: Selection, reason: str) -> None:
    """Escalate every bucket."""
    for bucket in (
        sel.cpp_unit,
        sel.integration,
        sel.python,
        sel.python_deps,
        sel.guiservice,
        sel.tools,
    ):
        bucket.escalate(reason)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    if args.filter_coverage:
        pkgs = [p for p in re.split(r"[,\s]+", args.coverage_packages) if p]
        src, dst = args.filter_coverage
        kept = filter_coverage(src, dst, pkgs)
        print(f"kept {kept} file(s) of {len(pkgs)} package(s)")
        return 0
    try:
        root = args.repo or Path(
            git(Path.cwd(), "rev-parse", "--show-toplevel").strip()
        )
        root = root.resolve()
        if args.files is not None:
            changed = [f.removeprefix("./") for f in args.files]
        else:
            changed = changed_files(
                root, args.base, args.head, args.two_dot, args.worktree
            )
    except RuntimeError as exc:
        print(f"affected_tests: {exc}", file=sys.stderr)
        return 2
    selector = Selector(Repo(root), args.core_threshold, args.py_full_threshold)
    sel = selector.select(changed)
    if args.full:
        full_selection(sel, "--full requested")
        sel = selector.finish()

    renderers = {
        "text": lambda: render_text(
            sel, args.verbose, resolve_engine(args.py_engine, root)
        ),
        "shell": lambda: render_shell(sel),
        "json": lambda: json.dumps(sel.to_json(), indent=2),
        "github": lambda: render_github(sel),
        "markdown": lambda: render_markdown(sel),
    }
    print(renderers[args.format]())
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(render_markdown(sel) + "\n")
    if args.run:
        return run_selection(sel, args, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
