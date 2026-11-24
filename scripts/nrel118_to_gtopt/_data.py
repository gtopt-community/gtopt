# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""NREL-118 source data: on-demand download, cache layout and CSV readers.

The NREL-118 CSVs live in the ``118-Bus/`` directory of
`NREL-Sienna/PowerSystemsTestData
<https://github.com/NREL-Sienna/PowerSystemsTestData/tree/master/118-Bus>`_.
That repository ships no licence file, so the data is **never vendored**
in gtopt: it is fetched on demand into a flat cache directory
(``~/.cache/gtopt/nrel118/`` by default) and read from there.

Cache layout (flat, one file per upstream CSV — the upstream sub-directory
is dropped):

* ``Buses.csv`` / ``Lines.csv`` / ``Generators.csv`` — network + fleet,
* ``partfact.csv`` — per-bus regional load participation factors,
* ``LoadR{1,2,3}DA.csv`` — day-ahead regional hourly load [MW],
* ``Wind{N}DA.csv`` / ``Solar{N}DA.csv`` — day-ahead hourly available
  output [MW] of ``Wind NN`` / ``Solar NN``,
* ``Hydro NN.csv`` — hourly output [MW] of the run-of-river ``Hydro NN``
  units (those whose ``Rating`` column names a data file),
* ``Hydro_nondispatchable.csv`` — monthly MW of ``Hydro 36`` .. ``Hydro 39``,
* ``118-hydro.csv`` — PLEXOS property dump with the monthly energy
  budgets (``Max Energy Month`` [GWh]) and monthly capacity overrides of
  the energy-limited ``Hydro 01`` .. ``Hydro 15`` units.

The upstream files mix line terminators (several are classic-Mac ``\\r``
only) and some carry a UTF-8 BOM; :func:`read_rows` normalises both.
"""

from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

#: PowerSystemsTestData commit the converter is validated against.  The
#: download URL is pinned to it so a re-fetch reproduces the same inputs.
DEFAULT_REF = "a2ecae9ff3cfe6d219744f1b10e4edd4eeecabc9"
RAW_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/NREL-Sienna/PowerSystemsTestData/{ref}/118-Bus"
)
GITHUB_RAW_BASE = RAW_URL_TEMPLATE.format(ref=DEFAULT_REF)
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "gtopt" / "nrel118"
#: Cache file naming the git ref a fresh cache was downloaded from (read by
#: :func:`nrel118_to_gtopt.source_version.detect_version`).
SOURCE_MARKER = "SOURCE_REF"

REGIONS: tuple[str, ...] = ("R1", "R2", "R3")
#: Row count of the upstream ``Hydro NN.csv`` files (8784 + 24 duplicates).
HYDRO_FILE_ROWS = 8784 + 24

# Upstream-relative paths of the files every conversion needs.
CORE_FILES: tuple[str, ...] = (
    "Generators.csv",
    "Buses.csv",
    "Lines.csv",
    "Load/partfact.csv",
    *(f"Load/DA/Load{r}DA.csv" for r in REGIONS),
    "Hydro/118-hydro.csv",
    "Hydro/Hydro_nondispatchable.csv",
)

# Kept for backward compatibility with callers that imported the old name.
CACHE_FILES = CORE_FILES


def local_name(remote: str) -> str:
    """Map an upstream-relative path to its flat cache file name."""
    return remote.rsplit("/", maxsplit=1)[-1]


def profile_remote_path(gen_name: str) -> str | None:
    """Upstream path of the hourly profile file of generator *gen_name*.

    ``Wind 07`` → ``Wind/DA/Wind7DA.csv``, ``Solar 12`` →
    ``Solar/DA/Solar12DA.csv``, ``Hydro 16`` → ``Hydro/Hydro 16.csv``.
    Returns ``None`` for any other technology.
    """
    kind, _, num = gen_name.strip().rpartition(" ")
    if not num.isdigit():
        return None
    if kind == "Wind":
        return f"Wind/DA/Wind{int(num)}DA.csv"
    if kind == "Solar":
        return f"Solar/DA/Solar{int(num)}DA.csv"
    if kind == "Hydro":
        return f"Hydro/Hydro {num}.csv"
    return None


def _download(remote: str, local: Path, ref: str = DEFAULT_REF) -> None:
    url = f"{RAW_URL_TEMPLATE.format(ref=ref)}/{urllib.parse.quote(remote)}"
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
        payload = resp.read()
    tmp = local.with_name(local.name + ".part")
    tmp.write_bytes(payload)
    tmp.replace(local)


def resolve(cache_dir: Path, remote: str) -> Path:
    """Local path of upstream file *remote* inside *cache_dir*.

    Accepts both the flat cache layout (``<dir>/LoadR1DA.csv``) and a
    checkout / copy of the upstream tree, given either as the ``118-Bus``
    directory (``<dir>/Load/DA/LoadR1DA.csv``) or as the repository root
    (``<dir>/118-Bus/Load/DA/LoadR1DA.csv``).  Returns the flat path when
    none exists (the download target).
    """
    flat = cache_dir / local_name(remote)
    if flat.is_file():
        return flat
    for candidate in (cache_dir / remote, cache_dir / "118-Bus" / remote):
        if candidate.is_file():
            return candidate
    return flat


def _cached(cache_dir: Path, remote: str) -> bool:
    local = resolve(cache_dir, remote)
    return local.exists() and local.stat().st_size > 0


def populate_cache(
    cache_dir: Path,
    profile_names: list[str] | None = None,
    ref: str = DEFAULT_REF,
) -> int:
    """Download every missing source CSV into *cache_dir* (idempotent).

    Fetches :data:`CORE_FILES` first, then the per-unit profile file of
    every generator whose ``Rating`` column names one (wind, solar,
    run-of-river hydro).  *profile_names* overrides that list (tests).
    A cache that held none of :data:`CORE_FILES` is stamped with *ref*
    (:data:`SOURCE_MARKER`).  Returns the number of files downloaded.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    fresh = not any(_cached(cache_dir, remote) for remote in CORE_FILES)
    fetched = 0
    for remote in CORE_FILES:
        if not _cached(cache_dir, remote):
            _download(remote, cache_dir / local_name(remote), ref)
            fetched += 1
    if fresh:
        (cache_dir / SOURCE_MARKER).write_text(f"{ref}\n", encoding="utf-8")
    if profile_names is None:
        profile_names = profile_generator_names(resolve(cache_dir, "Generators.csv"))
    for name in profile_names:
        profile = profile_remote_path(name)
        if profile is not None and not _cached(cache_dir, profile):
            _download(profile, cache_dir / local_name(profile), ref)
            fetched += 1
    return fetched


def missing_files(cache_dir: Path) -> list[str]:
    """Required inputs absent from *cache_dir* (empty list = cache usable).

    ``Generators.csv``, ``Buses.csv`` and ``Lines.csv`` are required, plus
    at least one regional load file; everything else (participation
    factors, hydro tables, profile files) degrades gracefully with a note.
    """
    missing = [
        name
        for name in ("Generators.csv", "Buses.csv", "Lines.csv")
        if not resolve(cache_dir, name).is_file()
    ]
    if not any(resolve(cache_dir, f"Load/DA/Load{r}DA.csv").is_file() for r in REGIONS):
        missing.append("LoadR*DA.csv")
    return missing


def read_rows(path: Path) -> list[list[str]]:
    """Read a CSV with any line terminator / optional BOM; drop blank rows."""
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]


def read_table(path: Path) -> tuple[dict[str, int], list[list[str]]]:
    """Return ``(header → column index, data rows)`` of a CSV file.

    Header names are stripped (``Buses.csv`` has ``"Magnitude "``,
    ``Lines.csv`` has ``"Bus from "``).
    """
    rows = read_rows(path)
    if not rows:
        return {}, []
    header = {col.strip(): idx for idx, col in enumerate(rows[0]) if col.strip()}
    return header, rows[1:]


def read_hourly(path: Path, n_hours: int | None = None) -> np.ndarray:
    """Read the value column of a ``DATETIME,value`` hourly CSV.

    Values are taken positionally (row k = hour k of the year).  The
    upstream timestamps are not used: the DA load files are hour-ending
    (``1/1/24 1:00`` first) while the wind files start at ``0:00`` and the
    hydro files carry 8808 rows — all describe the same 8784-hour study
    year.  As in the NREL-Sienna loader (``data_118bus.jl``:
    ``deleteat!(hydrodf, 1417:1440)``) the 24 duplicated rows at
    positions 1416..1439 of an 8808-row file are dropped.  *n_hours*
    truncates the series.
    """
    rows = read_rows(path)[1:]
    if len(rows) == HYDRO_FILE_ROWS:
        rows = rows[:1416] + rows[1440:]
    values = np.array(
        [float(r[1]) if len(r) > 1 and r[1].strip() else 0.0 for r in rows],
        dtype=np.float64,
    )
    if n_hours is not None:
        values = values[:n_hours]
    return values


def profile_generator_names(generators_csv: Path) -> list[str]:
    """Names of the generators whose ``Rating`` column names a data file."""
    if not generators_csv.is_file():
        return []
    header, rows = read_table(generators_csv)
    name_col = header.get("Generator Name", 0)
    rating_col = header.get("Rating")
    names: list[str] = []
    for row in rows:
        name = row[name_col].strip() if len(row) > name_col else ""
        if not name or rating_col is None or len(row) <= rating_col:
            continue
        rating = row[rating_col].strip()
        if rating == name and profile_remote_path(name) is not None:
            names.append(name)
    return names
