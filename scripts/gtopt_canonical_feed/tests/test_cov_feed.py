# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for gtopt_canonical_feed (cells, topology, I/O errors)."""

from __future__ import annotations

import pandas as pd
import pytest

from gtopt_canonical_feed import (
    SCHEMA_VERSION,
    Cells,
    FeedSchemaError,
    Manifest,
    read_feed,
    write_feed,
)
from gtopt_canonical_feed.cells import CELL_KEY_COLS
from gtopt_canonical_feed.topology import _opt_float, _opt_str


def test_cells_predicates_and_empty_keys():
    cells = Cells()
    assert not cells.has_flow_dual()
    assert not cells.has_line_restriction()
    assert list(cells.cell_keys().columns) == CELL_KEY_COLS
    cells.flow_dual = pd.DataFrame({"x": [1]})
    cells.line_restriction = pd.DataFrame({"x": [1]})
    assert cells.has_flow_dual()
    assert cells.has_line_restriction()


def test_topology_lookups(gold_topology):
    assert gold_topology.bus_by_uid(3).name == "b3"
    assert gold_topology.gen_by_uid(20).name == "midcc_gas"
    with pytest.raises(KeyError, match="bus uid 99"):
        gold_topology.bus_by_uid(99)
    with pytest.raises(KeyError, match="generator uid 99"):
        gold_topology.gen_by_uid(99)


def test_opt_helpers():
    assert _opt_str(float("nan")) is None
    assert _opt_str(3) == "3"
    assert _opt_float("abc") is None
    assert _opt_float(None) is None
    assert _opt_float("2.5") == 2.5


def test_write_feed_requires_dispatch(tmp_path, gold_topology):
    manifest = Manifest.make(
        producer="t", producer_version="0", schema_version=SCHEMA_VERSION
    )
    with pytest.raises(FeedSchemaError, match="required cells frame is empty"):
        write_feed(tmp_path / "feed", gold_topology, Cells(), manifest)


def test_read_feed_missing_required_parquet(gold_feed_dir):
    (gold_feed_dir / "topology" / "bus.parquet").unlink()
    with pytest.raises(FeedSchemaError, match="required parquet missing"):
        read_feed(gold_feed_dir, verify_hashes=False)


def test_cell_keys_and_has_flow(gold_cells):
    keys = gold_cells.cell_keys()
    assert len(keys) == len(keys.drop_duplicates())
    assert not Cells().has_flow()


def test_read_feed_optional_frame_absent(gold_feed_dir):
    from gtopt_canonical_feed.feed_io import _read_parquet_optional

    assert _read_parquet_optional(gold_feed_dir / "cells" / "nope.parquet") is None
