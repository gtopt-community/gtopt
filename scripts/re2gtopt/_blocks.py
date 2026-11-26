# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Typical-year RE profiles -> the case's blocks -> ``GeneratorProfile``.

The hour -> block membership is built by ``dlr2gtopt``'s own functions
(``blocks_from_map`` / ``blocks_from_bands`` / ``blocks_chronological``
and ``repeat``), called with the same options, so the line ratings and the
RE profiles of a case go through the **same** hour -> block assignment.

Block value (``--block-stat``):

``mean`` (default)  mean CF over the block's hours: preserves the energy
                    of the typical year in every block;
``dlr-hour``        the CF of the very hour ``dlr2gtopt to-blocks`` chose
                    as the block's representative (its ``source_hour``):
                    jointly consistent with the block's DLR weather, but
                    one hour per block (energy not preserved; a knn-mean
                    block, ``source_hour`` 0, falls back to ``mean``).
                    With ``--cf-history`` and a DLR table that has
                    ``source_time`` (``dlr2gtopt`` writes it), the CF of
                    that exact historical UTC hour: required for DLR
                    blocks built ``--history``, whose ``source_hour`` is
                    an hour of some other year;
``pNN`` / ``quantile:<q>`` / ``min``
                    the quantile of the block's hourly CF (``p90`` = CF
                    exceeded in 90 % of the block's hours).

Output (gtopt long layout, the only one gtopt reads)::

    <out>/GeneratorProfile/<prefix>.parquet   scenario,stage,block,uid,value
    <out>/<prefix>_generator_profiles.json    {"generator_profile_array": [...]}
    <out>/<prefix>_blocks.<fmt>               the per-block table (repeatable)
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt import _typical as dty

FloatArray = npt.NDArray[np.float64]
BLOCK_STATS_HELP = "mean (default) | dlr-hour | pNN | quantile:<q> | min"


def block_values(
    cf: FloatArray,
    specs: Sequence[dty.BlockSpec],
    stat: str = "mean",
    dlr_source_hour: Sequence[int] | None = None,
    *,
    dlr_source_time: Sequence[datetime | None] | None = None,
    cf_history: Any = None,
) -> tuple[FloatArray, dict[str, int]]:
    """(blocks, plants) block values and counters.

    ``dlr-hour``: the output of the DLR block's own hour.  With the DLR
    table's ``source_time`` and a ``cf_history`` (:class:`CFHistory`),
    the CF of that exact historical UTC hour (required for DLR blocks
    built ``--history``, whose ``source_hour`` is an hour of another
    year); else the typical year's ``source_hour``."""
    n_b, n_p = len(specs), cf.shape[1]
    out = np.zeros((n_b, n_p))
    fallbacks = 0
    s = stat.strip().lower()
    case = None if s in ("mean", "dlr-hour") else dty.parse_case(s)
    if s == "dlr-hour" and (dlr_source_hour is None or len(dlr_source_hour) != n_b):
        raise ValueError("block-stat dlr-hour needs the DLR blocks table (same blocks)")
    src_h = (
        np.zeros(n_b, dtype=np.int64)
        if dlr_source_hour is None
        else np.asarray(dlr_source_hour, dtype=np.int64)
    )
    exact = 0
    idx = cf_history.index() if cf_history is not None else {}
    if dlr_source_time is not None and len(dlr_source_time) != n_b:
        raise ValueError("DLR source_time: not the same blocks")
    for i, sp in enumerate(specs):
        if sp.hours.size == 0:
            raise ValueError(f"stage {sp.stage} block {sp.block} has no hours")
        vals = cf[sp.hours]
        if s == "dlr-hour":
            t = dlr_source_time[i] if dlr_source_time is not None else None
            if t is not None and t in idx:
                row = cf_history.cf[idx[t]]
                if np.isfinite(row).all():
                    out[i] = row
                    exact += 1
                    continue
            hsrc = int(src_h[i])
            if hsrc > 0:
                out[i] = cf[hsrc - 1]
                continue
            fallbacks += 1
            out[i] = vals.mean(axis=0)
        elif case is None or case.is_mean:
            out[i] = vals.mean(axis=0)
        else:
            out[i] = np.quantile(vals, float(0.5 if case.q is None else case.q), axis=0)
    return out, {"blocks": n_b, "dlr_hour_fallbacks": fallbacks, "dlr_exact": exact}


def wrap(specs: Sequence[dty.BlockSpec], values: FloatArray) -> dty.BlockWeather:
    """A ``BlockWeather`` carrying the CF, so ``dlr2gtopt``'s ``repeat``
    (block renumbering over the horizon) is reused verbatim."""
    n_b = len(specs)
    return dty.BlockWeather(
        list(specs),
        {"cf": values},
        values,
        np.zeros(n_b),
        np.zeros(n_b, dtype=np.int64),
        np.zeros(n_b),
    )


def block_frame(
    specs: Sequence[dty.BlockSpec],
    values: FloatArray,
    plants: Sequence[str],
    uids: Sequence[int],
) -> Any:
    """Long per-block table ``stage, block, month, hours, plant, uid, cf``."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    n_p = len(plants)

    def rep(a: Any) -> Any:
        return np.repeat(np.asarray(a), n_p)

    return pd.DataFrame(
        {
            "stage": rep([s.stage for s in specs]),
            "block": rep([s.block for s in specs]),
            "month": rep([s.month for s in specs]),
            "hours": rep([s.hours.size for s in specs]),
            "plant": np.tile(list(plants), len(specs)),
            "uid": np.tile(list(uids), len(specs)),
            "cf": values.reshape(-1),
        }
    )


def read_block_frame(
    path: Path,
) -> tuple[list[dty.BlockSpec], FloatArray, list[str], list[int]]:
    """Read a table written by :func:`block_frame` (hours as counts only)."""
    df = dty.read_frame(path)
    plants = list(dict.fromkeys(df["plant"].astype(str)))
    n_p = len(plants)
    uids = [int(u) for u in df["uid"].iloc[:n_p]]
    first = df.iloc[::n_p]
    specs = [
        dty.BlockSpec(int(s), int(b), np.zeros(int(n), dtype=np.int64), int(m))
        for s, b, m, n in zip(
            first["stage"], first["block"], first["month"], first["hours"]
        )
    ]
    return specs, df["cf"].to_numpy(dtype=float).reshape(len(specs), n_p), plants, uids


def repeat(
    specs: Sequence[dty.BlockSpec],
    values: FloatArray,
    stages: Sequence[tuple[int, int]],
    first_block: int = 1,
) -> tuple[list[dty.BlockSpec], FloatArray]:
    """Tile a month template over a horizon with ``dlr2gtopt``'s ``repeat``."""
    bw = dty.repeat(wrap(specs, values), stages, first_block)
    return bw.specs, bw.data["cf"]


def read_dlr_source_times(path: Path) -> list[datetime | None] | None:
    """Per-block ``source_time`` (UTC) of a ``dlr2gtopt to-blocks`` table,
    None when the table has none (older ``dlr2gtopt``)."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    df = dty.read_frame(path)
    if "source_time" not in df.columns:
        return None
    first = df.drop_duplicates(["stage", "block"], keep="first")
    return [
        None if pd.isna(t) else pd.Timestamp(t).to_pydatetime().replace(tzinfo=None)
        for t in first["source_time"]
    ]


def read_dlr_source_hours(path: Path) -> list[int]:
    """Per-block ``source_hour`` of a ``dlr2gtopt to-blocks`` table."""
    df = dty.read_frame(path)
    if "source_hour" not in df.columns:
        raise ValueError(f"{path}: not a dlr2gtopt blocks table (no source_hour)")
    first = df.drop_duplicates(["stage", "block"], keep="first")
    return [int(x) for x in first["source_hour"]]


def write_generator_profiles(
    out_dir: Path,
    specs: Sequence[dty.BlockSpec],
    values: FloatArray,
    plants: Sequence[str],
    uids: Sequence[int],
    generators: Sequence[str] | None = None,
    *,
    prefix: str = "re_profile",
    fmt: str = "parquet",
    scenarios: Sequence[int] = (1,),
) -> dict[str, Any]:
    """Write the ``GeneratorProfile`` schedule and its JSON snippet."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    if np.any(values < -1e-12) or np.any(values > 1 + 1e-12):
        raise ValueError("GeneratorProfile values must be per-unit in [0, 1]")
    n_b, n_p = values.shape
    st = np.array([s.stage for s in specs], dtype=np.int32)
    bl = np.array([s.block for s in specs], dtype=np.int32)
    frames = [
        pd.DataFrame(
            {
                "scenario": np.full(n_b * n_p, sc, dtype=np.int32),
                "stage": np.repeat(st, n_p),
                "block": np.repeat(bl, n_p),
                "uid": np.tile(np.asarray(uids, dtype=np.int32), n_b),
                "value": np.clip(values, 0.0, 1.0).reshape(-1),
            }
        )
        for sc in scenarios
    ]
    dty.write_frame(
        pd.concat(frames, ignore_index=True),
        out_dir / "GeneratorProfile" / f"{prefix}.{fmt}",
    )
    gens = list(generators) if generators is not None else list(plants)
    arr = [
        {
            "uid": int(u),
            "name": f"{p}_profile",
            "generator": int(g) if str(g).isdigit() else g,
            "profile": prefix,
        }
        for p, u, g in zip(plants, uids, gens)
    ]
    snippet = {"generator_profile_array": arr}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{prefix}_generator_profiles.json").write_text(
        json.dumps(snippet, indent=2) + "\n", encoding="utf-8"
    )
    return snippet
