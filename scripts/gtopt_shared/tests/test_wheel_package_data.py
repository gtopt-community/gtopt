# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""End-to-end packaging check: build the wheel, install it (non-editable)
into a throwaway venv and verify every runtime data file is readable.

Regression test for the 2026-11 packaging bug where a ``pip install
./scripts`` (non-editable) lacked ``plexos2gtopt/data/cen_pcp_hard_ucs.txt``
(→ ~100 CEN user constraints silently demoted from HARD to SOFT), the
solver ``.prm`` files and the repo-root ``share/gtopt`` emissions / cogen
tables.

Marked ``wheel`` (a few seconds; needs ``pip`` + ``setuptools`` +
``wheel`` in the running interpreter, no network).  Run with::

    pytest -m wheel -p no:xdist gtopt_shared/tests/test_wheel_package_data.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import sysconfig
import venv
import zipfile
from pathlib import Path

import pytest

from gtopt_shared.resources import REQUIRED_DATA_FILES

pytestmark = pytest.mark.wheel

SCRIPTS_DIR = Path(__file__).resolve().parents[2]

_IGNORE = shutil.ignore_patterns(
    "tests",
    "__pycache__",
    "*.pyc",
    "build",
    "dist",
    "*.egg-info",
    "cases",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

# Executed inside the venv with ``python -I`` (no cwd / PYTHONPATH / user
# site), so only the wheel-installed copy is importable.
_CHECK_SCRIPT = r"""
import json, os, sys
os.environ["GTOPT_HYDRO_MIN_MODE"] = "soft"
import gtopt_shared, plexos2gtopt
from gtopt_shared.resources import missing_required_data_files
from gtopt_shared.cen_cogen import load_cen_cogen_reference
from plexos2gtopt import parsers
from plexos2gtopt._defaults import resolve_default_emissions_file
from plp2gtopt._parsers import (
    DEFAULT_PMIN_FLOWRIGHT_FILE, DEFAULT_ROR_RESERVOIRS_FILE,
)
names, prefixes = load_cen_cogen_reference()
print(json.dumps({
    "gtopt_shared": gtopt_shared.__file__,
    "plexos2gtopt": plexos2gtopt.__file__,
    "missing": missing_required_data_files(),
    "n_hard": len(parsers._load_plexos_hard_uc_list()),
    "emissions": str(resolve_default_emissions_file()),
    "n_cogen": len(names) + len(prefixes),
    "ror": DEFAULT_ROR_RESERVOIRS_FILE.is_file(),
    "pmin": DEFAULT_PMIN_FLOWRIGHT_FILE.is_file(),
}))
"""


def _require_build_tools() -> None:
    for mod in ("pip", "setuptools", "wheel"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"{mod} not available in the test interpreter")


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    _require_build_tools()
    root = tmp_path_factory.mktemp("wheel")
    # Build from a pristine copy so setuptools' build/ and *.egg-info
    # never land in (or race inside) the source tree.
    src = root / "src"
    shutil.copytree(SCRIPTS_DIR, src, ignore=_IGNORE)
    out = root / "dist"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "-q",
            "-w",
            str(out),
            str(src),
        ],
        check=True,
        cwd=root,
    )
    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_wheel_contains_required_data_files(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as zf:
        members = set(zf.namelist())
    missing = [
        f"{pkg}/{rel}"
        for pkg, rel in REQUIRED_DATA_FILES
        if f"{pkg}/{rel}" not in members
    ]
    assert not missing, f"wheel {built_wheel.name} lacks: {missing}"


def test_installed_wheel_reads_data_files(built_wheel: Path, tmp_path: Path) -> None:
    """Non-editable install into a fresh venv; run the real loaders there.

    The venv sees the test interpreter's site-packages (for pandas & co.)
    after its own, so the wheel copy is what gets imported — asserted via
    ``__file__``.
    """
    env_dir = tmp_path / "venv"
    venv.create(env_dir, with_pip=False)
    py = env_dir / "bin" / "python"
    # Expose the test interpreter's third-party deps (pandas, pyarrow, …)
    # *after* the venv's own site-packages.  A plain path entry in a .pth
    # does not execute the host's .pth files, so a host editable install
    # of gtopt_scripts stays invisible.
    venv_site = Path(
        subprocess.run(
            [str(py), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    # Include the user site (``pip install --user``) and any other
    # site-packages on the host path, where pandas & co. may live.
    host_paths = {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}
    host_paths.update(
        p for p in sys.path if p.endswith(("site-packages", "dist-packages"))
    )
    (venv_site / "_gtopt_host_deps.pth").write_text(
        "\n".join(sorted(host_paths)) + "\n", encoding="utf-8"
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--python",
            str(py),
            "install",
            "--no-deps",
            "--no-index",
            "-q",
            str(built_wheel),
        ],
        check=True,
        cwd=tmp_path,
    )
    proc = subprocess.run(
        [str(py), "-I", "-c", _CHECK_SCRIPT],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    for key in ("gtopt_shared", "plexos2gtopt"):
        assert Path(res[key]).resolve().is_relative_to(env_dir.resolve()), (
            f"{key} imported from {res[key]}, not from the installed wheel"
        )
    assert res["missing"] == []
    # 204 audited names minus 12 hydro-gated under --hydro-min-mode soft.
    assert res["n_hard"] == 192
    assert Path(res["emissions"]).name == "cen_chile.json"
    assert res["n_cogen"] > 0
    assert res["ror"] and res["pmin"]
