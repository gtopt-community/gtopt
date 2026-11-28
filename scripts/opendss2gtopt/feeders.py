# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""IEEE distribution test feeders in OpenDSS form, fetched on demand.

The scripts are not committed to gtopt (their redistribution terms are
not stated); they are read from ``$GTOPT_OPENDSS_FEEDERS`` (a directory
laid out like the OpenDSS ``Distrib/IEEETestCases`` tree) or downloaded
once into ``~/.cache/gtopt/opendss-feeders`` from the DSS-Extensions
mirror of the OpenDSS test cases.  ``GTOPT_OFFLINE=1`` forbids the
download.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

BASE_URL = (
    "https://raw.githubusercontent.com/dss-extensions/electricdss-tst/master/"
    "Version8/Distrib/IEEETestCases"
)

# name -> (sub-directory, master script, every file the master reads)
FEEDERS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "ieee4": ("4Bus-YY-Bal", "4Bus-YY-Bal.DSS", ("4Bus-YY-Bal.DSS",)),
    "ieee13": (
        "13Bus",
        "IEEE13Nodeckt.dss",
        (
            "IEEE13Nodeckt.dss",
            "IEEELineCodes.DSS",
            "IEEE13Node_BusXY.csv",
        ),
    ),
    "ieee34": ("34Bus", "ieee34Mod1.dss", ("ieee34Mod1.dss", "IEEELineCodes.DSS")),
    "ieee123": (
        "123Bus",
        "IEEE123Master.dss",
        (
            "IEEE123Master.dss",
            "IEEELineCodes.DSS",
            "IEEE123Loads.DSS",
            "IEEE123Regulators.DSS",
        ),
    ),
}
# Shared file one level up that the per-feeder IEEELineCodes.DSS redirects to.
_SHARED = ("IEEELineCodes.DSS",)


def cache_dir() -> Path:
    """Directory holding (or receiving) the feeder scripts."""
    env = os.environ.get("GTOPT_OPENDSS_FEEDERS")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "gtopt" / "opendss-feeders"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
        dest.write_bytes(resp.read())


def feeder_path(name: str, *, download: bool = True) -> Path | None:
    """Master script of feeder *name*, or None when unavailable."""
    if name not in FEEDERS:
        raise KeyError(f"unknown feeder '{name}' (known: {', '.join(FEEDERS)})")
    sub, master, files = FEEDERS[name]
    root = cache_dir()
    wanted = [root / sub / f for f in files] + [root / f for f in _SHARED]
    missing = [p for p in wanted if not p.is_file()]
    if missing:
        if not download or os.environ.get("GTOPT_OFFLINE") == "1":
            return None
        try:
            for p in missing:
                rel = p.relative_to(root).as_posix()
                _download(f"{BASE_URL}/{rel}", p)
        except OSError:
            return None
    return root / sub / master


def synthetic_feeder_dss(n_buses: int, *, seed: int = 1, kv: float = 12.47) -> str:
    """A random radial 3-phase feeder with *n_buses* buses (scaling tests).

    Every new bus hangs off one of the last few buses (long, deep
    laterals like real rural feeders); 70 % of the buses carry a
    0.9-pf load, the total peak is about 6 MW at 12.47 kV.
    """
    import random  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    rng = random.Random(seed)  # noqa: S311 (not cryptographic)
    lines = [
        f"Clear\nNew Circuit.synth{n_buses} basekv={kv} pu=1.0 MVAsc3=200 MVAsc1=180",
        "New LineCode.acsr336 nphases=3 r1=0.306 x1=0.627 r0=0.775 x0=1.98 "
        "c1=0 c0=0 units=mi normamps=530",
        "New LineCode.acsr1_0 nphases=3 r1=0.592 x1=0.656 r0=1.06 x0=2.0 "
        "c1=0 c0=0 units=mi normamps=310",
    ]
    names = ["sourcebus"]
    loads = []
    for i in range(1, n_buses):
        parent = names[max(0, len(names) - 1 - int(rng.expovariate(0.3)))]
        bus = f"n{i}"
        code = "acsr336" if i < n_buses // 10 else "acsr1_0"
        length = round(rng.uniform(0.02, 0.12), 4)
        lines.append(
            f"New Line.l{i} bus1={parent} bus2={bus} linecode={code} "
            f"length={length} units=mi"
        )
        names.append(bus)
        if rng.random() < 0.7:
            loads.append(bus)
    kw = 6000.0 / max(len(loads), 1)
    for j, bus in enumerate(loads):
        lines.append(
            f"New Load.ld{j} bus1={bus} phases=3 kV={kv} kW={kw:.4f} pf=0.9 model=1"
        )
    lines += [f"Set VoltageBases=[{kv}]", "CalcVoltageBases", "Solve"]
    return "\n".join(lines) + "\n"
