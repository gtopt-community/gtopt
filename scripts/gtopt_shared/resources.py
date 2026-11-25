# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Locate non-Python data files bundled inside the gtopt Python packages.

Every runtime data file read by the converters (hard-UC lists, emission
factor tables, cogen reference lists, solver parameter files, expansion
templates) lives *inside* a Python package directory and is declared in
``[tool.setuptools.package-data]`` of ``scripts/pyproject.toml``.  That
gives a single source of truth that works identically for:

* a source checkout (``PYTHONPATH=scripts`` / pytest),
* an editable install (``pip install -e ./scripts``), and
* a regular wheel install (``pip install ./scripts``).

Use :func:`package_data_file` / :func:`package_data_dir` instead of
``Path(__file__).parent / ...`` so that a missing file fails loudly with
:class:`MissingDataFileError` (a :class:`FileNotFoundError`) and an
actionable message, rather than letting the caller silently fall back to
a default that changes model semantics.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REINSTALL_HINT = (
    "The gtopt Python tools were probably installed from a wheel built "
    "without this file (a packaging bug fixed in 2026-11).  Reinstall "
    "from an up-to-date checkout with `pip install --force-reinstall "
    "./scripts` (or `pip install -e ./scripts` for development)."
)


class MissingDataFileError(FileNotFoundError):
    """A data file that must ship with a gtopt Python package is missing."""


def _package_dir(package: str) -> Path:
    """Directory of ``package`` without importing it (avoids import cycles).

    ``importlib.util.find_spec`` only imports the *parents* of a dotted
    name, so module-level constants in one converter can point at another
    converter's data without triggering its ``__init__``.
    """
    try:
        spec = importlib.util.find_spec(package)
    except (ModuleNotFoundError, ValueError) as exc:
        raise MissingDataFileError(
            f"cannot locate package '{package}': {exc}.  {REINSTALL_HINT}"
        ) from exc
    if spec is None or not spec.submodule_search_locations:
        raise MissingDataFileError(
            f"cannot locate package '{package}'.  {REINSTALL_HINT}"
        )
    # All gtopt packages are installed as plain directories (never zipped).
    return Path(next(iter(spec.submodule_search_locations)))


def _resolve(package: str, relparts: tuple[str, ...]) -> Path:
    if not relparts:
        raise ValueError("package data lookup needs at least one path component")
    return _package_dir(package).joinpath(*relparts)


def package_data_file(package: str, *relparts: str, purpose: str = "") -> Path:
    """Return the path of a required data file bundled in ``package``.

    Args:
        package: Dotted name of the owning package (e.g. ``"plexos2gtopt"``).
        *relparts: Path components relative to the package directory
            (e.g. ``"data", "cen_pcp_hard_ucs.txt"``).
        purpose: Optional short description used in the error message.

    Raises:
        MissingDataFileError: when the file does not exist.
    """
    path = _resolve(package, relparts)
    if not path.is_file():
        what = f" ({purpose})" if purpose else ""
        raise MissingDataFileError(
            f"required data file '{package}/{'/'.join(relparts)}'{what} "
            f"not found at {path}.  {REINSTALL_HINT}"
        )
    return path


def package_data_dir(package: str, *relparts: str, purpose: str = "") -> Path:
    """Directory counterpart of :func:`package_data_file`."""
    path = _resolve(package, relparts)
    if not path.is_dir():
        what = f" ({purpose})" if purpose else ""
        raise MissingDataFileError(
            f"required data directory '{package}/{'/'.join(relparts)}'{what} "
            f"not found at {path}.  {REINSTALL_HINT}"
        )
    return path


def package_data_path(package: str, *relparts: str) -> Path:
    """Return the (possibly non-existent) path of a bundled data file.

    For module-level constants such as CLI ``default=`` values, where the
    existence check is deferred to the reader.
    """
    return _resolve(package, relparts)


_SOLVER_PARAM_FILES: tuple[str, ...] = (
    "cplex.prm",
    "cplex_warmstart.prm",
    "gurobi_warmstart.prm",
    "highs_warmstart.opts",
    "mindopt_warmstart.prm",
)

#: Every non-Python file read at runtime, as ``(package, relative/path)``.
#: Checked against a freshly built wheel by
#: ``gtopt_shared/tests/test_wheel_package_data.py`` (``pytest -m wheel``).
#: Keep in sync with ``[tool.setuptools.package-data]``.
REQUIRED_DATA_FILES: tuple[tuple[str, str], ...] = (
    ("gtopt_shared", "data/ipcc_emission_factors.json"),
    ("gtopt_shared", "data/json_contracts.json"),
    ("gtopt_shared", "data/emissions/cen_chile.json"),
    ("gtopt_shared", "data/cogen/cen_chile_cogen.csv"),
    *(
        ("gtopt_shared", f"data/countries/{cc}.toml")
        for cc in ("au", "cl", "co", "gt", "pe")
    ),
    ("plexos2gtopt", "data/cen_pcp_hard_ucs.txt"),
    *(
        ("gtopt_operators", f"{op}/sources.toml")
        for op in ("cen", "amm", "eor", "coes", "xm", "aemo")
    ),
    *(("plexos2gtopt", f"solvers/{n}") for n in _SOLVER_PARAM_FILES),
    *(("plp2gtopt", f"solvers/{n}") for n in _SOLVER_PARAM_FILES),
    ("gtopt_expand", "templates/laja.tampl"),
    ("gtopt_expand", "templates/laja.tson"),
    ("gtopt_expand", "templates/maule.tampl"),
    ("gtopt_expand", "templates/maule.tson"),
    ("gtopt_expand", "templates/maule_machicura.tampl"),
    ("gtopt_expand", "templates/maule_machicura.tson"),
    ("gtopt_expand", "templates/ror_equivalence.csv"),
    ("gtopt_expand", "templates/pmin_as_flowright.csv"),
    ("dlr2gtopt", "data/line_defaults.json"),
)


def missing_required_data_files() -> list[str]:
    """Return ``package/relpath`` of every :data:`REQUIRED_DATA_FILES`
    entry that is absent from the current installation (empty = OK)."""
    missing: list[str] = []
    for package, rel in REQUIRED_DATA_FILES:
        try:
            package_data_file(package, *rel.split("/"))
        except MissingDataFileError:
            missing.append(f"{package}/{rel}")
    return missing
