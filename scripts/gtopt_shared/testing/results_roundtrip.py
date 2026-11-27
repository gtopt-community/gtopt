# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Helpers for results round-trip tests (``gtopt -> gtopt2X -> X2gtopt``).

* :func:`check_layout` asserts an output directory has the exact layout
  a gtopt run writes (hive partitions, schema, codec, run files),
* :func:`stream_diffs` compares two output directories stream by stream,
  matching elements by name (the converted case renumbers / renames them).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.results_reader import GtoptResults
from gtopt_shared.results_writer import SOLUTION_HEADER, ElementIndex

#: ``(class, field, system array)`` compared by :func:`stream_diffs`.
STREAMS: tuple[tuple[str, str, str], ...] = (
    ("Generator", "generation_sol", "generator_array"),
    ("Bus", "balance_dual", "bus_array"),
    ("Line", "flow_sol", "line_array"),
    ("Demand", "load_sol", "demand_array"),
    ("Demand", "fail_sol", "demand_array"),
    ("Reservoir", "water_value_dual", "reservoir_array"),
    ("Battery", "energy_sol", "battery_array"),
    ("Battery", "fout_sol", "battery_array"),
    ("Battery", "finp_sol", "battery_array"),
    ("Battery", "energy_dual", "battery_array"),
    ("Turbine", "flow_sol", "turbine_array"),
)

#: Name prefixes the converters give derived elements.
PREFIXES = ("d_", "load_", "r_", "rs_", "tb_", "turbine_", "j_")

GTOPT_SCHEMA = pa.schema(
    [
        ("scenario", pa.int32()),
        ("stage", pa.int32()),
        ("block", pa.int32()),
        ("uid", pa.int32()),
        ("value", pa.float32()),
    ]
)


def check_layout(out_dir: Path) -> list[Path]:
    """Assert ``out_dir`` is laid out as a gtopt run; returns the parts.

    The value codec is the one ``planning.json`` asks for (gtopt's
    ``output_compression``, default ``zstd``).
    """
    parts = sorted(out_dir.glob("*/*.parquet/scene=*/phase=*/part.parquet"))
    assert parts, f"{out_dir}: no hive-partitioned streams"
    plan = json.loads((out_dir / "planning.json").read_text(encoding="utf-8"))
    codec = str((plan.get("options") or {}).get("output_compression") or "zstd")
    for part in parts:
        meta = pq.ParquetFile(part).metadata
        schema = pq.read_schema(part)
        assert schema.remove_metadata().equals(GTOPT_SCHEMA), (part, schema)
        if meta.num_row_groups:
            col = meta.row_group(0).column(4)
            assert col.compression == codec.upper(), (part, col.compression)
    with (out_dir / "solution.csv").open(encoding="utf-8") as fh:
        head = next(csv.reader(fh))
    assert tuple(head) == SOLUTION_HEADER
    for name in ("solver_status.json", "planning.json", "README.md"):
        assert (out_dir / name).is_file(), name
    return parts


def stream_diffs(
    orig_case: GtoptCase,
    orig_out: Path,
    back_case: GtoptCase,
    back_out: Path,
    streams: Iterable[tuple[str, str, str]] = STREAMS,
    scale: dict[str, float] | None = None,
) -> dict[tuple[str, str, str], float]:
    """Max abs difference per ``(class, field, name)`` of two output dirs.

    Elements of ``orig_case`` are found in ``back_case`` by name
    (:class:`ElementIndex` with :data:`PREFIXES`); missing ones map to
    ``nan``.  ``scale`` multiplies the original values of a class (unit
    changes, e.g. reservoir volumes in another unit).
    """
    a = GtoptResults(orig_out, orig_case.grid)
    b = GtoptResults(back_out, back_case.grid)
    idx = ElementIndex(back_case.planning)
    out: dict[tuple[str, str, str], float] = {}
    for cls, fld, arr in streams:
        for el in orig_case.elements(arr):
            name = str(el.get("name"))
            match = idx.find(arr, name, prefixes=PREFIXES)
            if match is None:
                out[(cls, fld, name)] = float("nan")
                continue
            va = a.block_of(cls, fld, el.get("uid")) * (scale or {}).get(cls, 1.0)
            vb = b.block_of(cls, fld, match.get("uid"))
            if va.shape != vb.shape:
                out[(cls, fld, name)] = float("nan")
                continue
            out[(cls, fld, name)] = float(np.abs(va - vb).max(initial=0.0))
    return out
