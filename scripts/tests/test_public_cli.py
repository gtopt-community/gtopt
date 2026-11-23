# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Anti-rot smoke suite for every PUBLIC console script.

The list of public tools is read from ``[project.scripts]`` in
``scripts/pyproject.toml`` -- the single source of truth for what ``pip
install`` puts on a user's PATH.  For every entry this suite checks:

1. ``--help`` renders and exits 0 (catches import errors, renamed entry
   points and bare ``%`` in argparse help strings);
2. at least one end-to-end case is registered (``_cli_cases_*.py``), so a
   new public tool without an e2e test fails CI;
3. each registered e2e case runs the tool on a tiny canonical fixture and
   validates its output.  Cases that solve with gtopt are marked
   ``integration`` and skip when no gtopt binary is available.

Developer / repo-maintenance tools must NOT be console scripts: keep them
under ``tools/`` or reachable as ``python -m <pkg>`` only.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent


def _load(name: str):
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(name, _TESTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_common = _load("_cli_e2e")
for _path in sorted(_TESTS_DIR.glob("_cli_cases_*.py")):
    _load(_path.stem)

PUBLIC = _common.public_scripts()
REGISTRY = list(_common.REGISTRY)
CliRun = _common.CliRun


# ---------------------------------------------------------------------------
# 1. --help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", sorted(PUBLIC))
def test_help_exits_zero(script: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``<script> --help`` imports, renders a usage line and exits 0."""
    module_path, func_name = PUBLIC[script].split(":")
    mod = importlib.import_module(module_path)
    main_fn = getattr(mod, func_name, None)
    assert callable(main_fn), f"{script}: {PUBLIC[script]} is not callable"

    monkeypatch.setattr("sys.argv", [script, "--help"])
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            with pytest.raises(SystemExit) as exc_info:
                main_fn()
    except ValueError as exc:  # argparse bare '%' in help text
        pytest.fail(f"{script} --help raised ValueError: {exc}")
    assert exc_info.value.code in (0, None), (
        f"{script} --help exited {exc_info.value.code}\n{err.getvalue()}"
    )
    assert "usage:" in out.getvalue().lower(), out.getvalue()[:500]


# ---------------------------------------------------------------------------
# 2. registry completeness
# ---------------------------------------------------------------------------


def test_every_public_script_has_an_e2e_case() -> None:
    """A new ``[project.scripts]`` entry must come with an e2e case."""
    covered = {case.script for case in REGISTRY}
    missing = sorted(set(PUBLIC) - covered)
    assert not missing, (
        "public console scripts without an e2e smoke case in "
        f"scripts/tests/_cli_cases_*.py: {missing}"
    )


def test_registry_names_only_public_scripts() -> None:
    """Cases must not refer to removed / renamed console scripts."""
    stale = sorted({case.script for case in REGISTRY} - set(PUBLIC))
    assert not stale, f"e2e cases for non-public scripts: {stale}"


def test_case_ids_are_unique() -> None:
    """Two cases with the same id would shadow each other in reports."""
    ids = [case.id for case in REGISTRY]
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


# ---------------------------------------------------------------------------
# 3. end-to-end runs
# ---------------------------------------------------------------------------

_OFFLINE = [c for c in REGISTRY if not c.needs_gtopt]
_WITH_GTOPT = [c for c in REGISTRY if c.needs_gtopt]


@pytest.mark.parametrize("case", _OFFLINE, ids=[c.id for c in _OFFLINE])
def test_e2e(case, tmp_path: Path) -> None:
    """Run the public tool on a tiny fixture and validate its output."""
    case.func(CliRun(script=case.script, tmp_path=tmp_path))


@pytest.fixture(scope="module")
def gtopt_bin() -> str:
    """gtopt binary path; skip the case when none is available."""
    binary = _common.find_gtopt_binary()
    if binary is None:
        pytest.skip(
            "gtopt binary not found - set GTOPT_BIN, put gtopt on PATH or "
            "build the project (cmake -S all -B build)"
        )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("case", _WITH_GTOPT, ids=[c.id for c in _WITH_GTOPT])
def test_e2e_with_gtopt(case, tmp_path: Path, gtopt_bin: str) -> None:
    """Round-trip: run the tool and solve its result with gtopt."""
    case.func(CliRun(script=case.script, tmp_path=tmp_path, gtopt_bin=gtopt_bin))
