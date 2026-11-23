# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Shared helpers for the public-CLI smoke suite (``test_public_cli.py``).

Every console script in ``[project.scripts]`` of ``scripts/pyproject.toml``
is a *public* tool and must have at least one end-to-end case registered
here (see the ``_cli_cases_*.py`` modules).  A case is a plain function
decorated with :func:`e2e`; it receives a :class:`CliRun` context, runs the
tool through :meth:`CliRun.run` on a tiny fixture and asserts that the
output exists and is valid.

Cases that need the gtopt solver binary pass ``needs_gtopt=True``: they
are marked ``integration`` and skip cleanly when no binary is found
(``GTOPT_BIN``, the standard build directories, or ``gtopt`` on PATH).
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_DIR.parent
CASES_DIR = REPO_ROOT / "cases"
SCRIPT_CASES_DIR = SCRIPTS_DIR / "cases"

#: Default per-invocation timeout (seconds).
DEFAULT_TIMEOUT = 300.0


def public_scripts() -> dict[str, str]:
    """Return ``{script_name: "module:function"}`` from pyproject."""
    with (SCRIPTS_DIR / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return dict(data["project"]["scripts"])


def find_gtopt_binary() -> str | None:
    """Locate the gtopt binary (no download, no build)."""
    env_bin = os.environ.get("GTOPT_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin
    for rel in (
        "build/standalone/gtopt",
        "build/gtopt",
        "build-standalone/gtopt",
        "all/build/gtopt",
    ):
        candidate = REPO_ROOT / rel
        if candidate.exists():
            return str(candidate)
    return shutil.which("gtopt")


@dataclass
class CliResult:
    """Outcome of one CLI invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """Combined stdout + stderr."""
        return self.stdout + self.stderr


@dataclass
class CliRun:
    """Context handed to every e2e case."""

    script: str
    tmp_path: Path
    gtopt_bin: str | None = None
    env: dict[str, str] = field(default_factory=dict)

    def run(
        self,
        args: Sequence[str | Path],
        *,
        script: str | None = None,
        expect_rc: int | Sequence[int] = 0,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: Path | None = None,
        stdin: str | None = None,
    ) -> CliResult:
        """Run a console script exactly as the installed wrapper would.

        The entry point is resolved from ``pyproject.toml`` and executed in
        a fresh interpreter as ``sys.exit(<func>())`` with ``sys.argv[0]``
        set to the script name -- the same thing the setuptools console
        wrapper does -- so import-time errors, argv parsing and exit codes
        are all exercised.
        """
        name = script or self.script
        target = public_scripts()[name]
        module, func = target.split(":")
        code = (
            "import importlib, sys\n"
            f"sys.argv[0] = {name!r}\n"
            f"sys.exit(getattr(importlib.import_module({module!r}), {func!r})())\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SCRIPTS_DIR), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        env.setdefault("MPLBACKEND", "Agg")
        env["NO_COLOR"] = "1"
        if self.gtopt_bin:
            env["GTOPT_BIN"] = self.gtopt_bin
        env.update(self.env)
        proc = subprocess.run(
            [sys.executable, "-c", code, *[str(a) for a in args]],
            cwd=cwd or self.tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
            check=False,
        )
        result = CliResult(proc.returncode, proc.stdout, proc.stderr)
        allowed = (expect_rc,) if isinstance(expect_rc, int) else tuple(expect_rc)
        assert result.returncode in allowed, (
            f"{name} {' '.join(map(str, args))} exited {result.returncode} "
            f"(expected {allowed})\n--- stdout ---\n{result.stdout[-4000:]}"
            f"\n--- stderr ---\n{result.stderr[-4000:]}"
        )
        return result

    def run_gtopt(
        self, case_json: Path, *extra: str, output_dir: Path | None = None
    ) -> Path:
        """Solve *case_json* with gtopt (cwd = its directory).

        Returns the output directory (``-d``), ``<tmp>/gtopt_output`` unless
        *output_dir* is given.  Core dumps are enabled for the child so a
        crash leaves something to autopsy.
        """
        assert self.gtopt_bin, "case must be registered with needs_gtopt=True"
        out = output_dir or self.tmp_path / "gtopt_output"
        proc = subprocess.run(
            [self.gtopt_bin, case_json.name, "-d", str(out), *extra],
            cwd=case_json.parent,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
            check=False,
            preexec_fn=_enable_core_dumps,
        )
        assert proc.returncode == 0, (
            f"gtopt {case_json} exited {proc.returncode}\n"
            f"{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
        )
        return out


def _enable_core_dumps() -> None:
    """``ulimit -c unlimited`` for the gtopt child (best effort)."""
    try:
        resource.setrlimit(
            resource.RLIMIT_CORE, (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
        )
    except (ValueError, OSError):
        pass


CaseFunc = Callable[[CliRun], None]


@dataclass(frozen=True)
class E2ECase:
    """One registered end-to-end invocation of a public script."""

    script: str
    name: str
    func: CaseFunc
    needs_gtopt: bool = False

    @property
    def id(self) -> str:
        """Pytest id: ``<script>::<case>``."""
        return f"{self.script}::{self.name}"


REGISTRY: list[E2ECase] = []


def e2e(script: str, *, needs_gtopt: bool = False) -> Callable[[CaseFunc], CaseFunc]:
    """Register *func* as an e2e case for console script *script*."""

    def deco(func: CaseFunc) -> CaseFunc:
        REGISTRY.append(
            E2ECase(
                script=script,
                name=func.__name__.removeprefix("case_"),
                func=func,
                needs_gtopt=needs_gtopt,
            )
        )
        return func

    return deco


# ---------------------------------------------------------------------------
# Small fixture helpers shared by the case modules.
# ---------------------------------------------------------------------------


def copy_case(src: Path, dst: Path) -> Path:
    """Copy a case directory tree into *dst* and return *dst*."""
    shutil.copytree(src, dst)
    return dst


def load_json(path: Path) -> dict:
    """Load a JSON file, asserting it exists."""
    assert path.is_file(), f"expected output file {path} is missing"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def assert_planning_json(path: Path) -> dict:
    """Assert *path* is a gtopt planning JSON with a non-empty system."""
    data = load_json(path)
    assert "system" in data, f"{path}: no 'system' key"
    assert data["system"].get("bus_array") or data["system"].get("generator_array"), (
        f"{path}: empty system"
    )
    return data


def assert_solved(output_dir: Path) -> None:
    """Assert a gtopt output directory reports an optimal solve."""
    # pylint: disable-next=import-outside-toplevel
    from gtopt_shared.solution_csv import parse_solution_csv

    solution = output_dir / "solution.csv"
    assert solution.is_file(), f"{solution} missing"
    row = parse_solution_csv(solution.read_text(encoding="utf-8"))
    assert row.get("status") == "0", f"non-optimal solve: {row}"
