# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt._bus_catalogue` (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import _bus_catalogue as bc


def _cmg_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id_info": [1, 1, 2, "x", 3],
            "barra_info": ["CRUCERO 220", "CRUCERO 220", "CHARRUA 500", "Z", "FOO"],
            "barra_transf": [
                "CRUCERO_______220",
                "CRUCERO_______220",
                "CHARRUA_______500",
                "Z_____110",
                None,
            ],
            "fecha": [
                "2025-01-01 00:00",
                "2025-01-02 00:00",
                "2025-01-01 00:00",
                "2025-01-01 00:00",
                "2025-01-01 00:00",
            ],
        }
    )


def test_parse_voltage_and_infer_region() -> None:
    assert bc.parse_voltage(None) == 0
    assert bc.parse_voltage("NOVOLT") == 0
    assert bc.parse_voltage("ELMAITEN______066") == 66
    assert bc.infer_region(None, None) == "unknown"
    assert bc.infer_region("Charrúa 220") == "south-centre"
    assert bc.infer_region(None, "PUERTO_MONTT__220") == "south"
    assert bc.infer_region("Nowhere") == "unknown"


def test_discover_buses_from_cache(tmp_path: Path) -> None:
    assert bc.discover_buses_from_cache(tmp_path).empty
    sip = tmp_path / "sip" / "costo_marginal_real__v4__findByDate"
    sip.mkdir(parents=True)
    assert bc.discover_buses_from_cache(tmp_path).empty  # dir with no files
    _cmg_rows().astype({"id_info": str}).to_parquet(sip / "a.parquet")
    (sip / "b.parquet").write_bytes(b"not parquet")
    cat = bc.discover_buses_from_cache(tmp_path)
    assert list(cat["bus_id"]) == [1, 2]
    row = cat.set_index("bus_id").loc[1]
    assert row["last_seen"] == "2025-01-02"
    assert row["voltage_kv"] == 220
    assert row["region"] == "north"


def test_discover_buses_from_cache_all_corrupt(tmp_path: Path) -> None:
    sip = tmp_path / "sip" / "costo_marginal_real__v4__findByDate"
    sip.mkdir(parents=True)
    (sip / "b.parquet").write_bytes(b"junk")
    assert bc.discover_buses_from_cache(tmp_path).empty


@pytest.mark.parametrize("use_online", [True, False])
def test_discover_buses_from_api(
    monkeypatch: pytest.MonkeyPatch, use_online: bool
) -> None:
    seen: list[str] = []

    def _fake(_client: Any, name: str, *, start: str) -> pd.DataFrame:
        seen.append(name)
        return _cmg_rows()

    monkeypatch.setattr(bc, "fetch_by_name", _fake)
    cat = bc.discover_buses_from_api(object(), date="2025-03-01", use_online=use_online)
    assert seen == ["cmg_online" if use_online else "cmg_real"]
    assert set(cat["last_seen"]) == {"2025-03-01"}
    assert sorted(cat["bus_id"]) == [1, 2]


def test_discover_buses_from_api_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bc, "fetch_by_name", lambda *_a, **_k: pd.DataFrame())
    assert bc.discover_buses_from_api(object(), date="2025-03-01").empty


def test_persistence_merge_filter_and_ref_buses(tmp_path: Path) -> None:
    path = tmp_path / "cat" / "known.parquet"
    assert bc.load_catalogue(path).empty
    a = pd.DataFrame(
        {
            "bus_id": [1, 2],
            "bar_transf": ["A_220", "B_110"],
            "barra_info": ["Alpha", "Beta"],
            "voltage_kv": [220, 110],
            "region": ["north", "south"],
            "last_seen": ["2025-01-01", "2025-01-01"],
        }
    )
    b = a.iloc[[0]].assign(last_seen="2025-02-01", barra_info="Alpha2")
    assert bc.save_catalogue(a, path) == path
    assert len(bc.load_catalogue(path)) == 2

    empty = pd.DataFrame()
    assert bc.merge_catalogues(empty, empty).empty
    assert len(bc.merge_catalogues(empty, a)) == 2
    assert len(bc.merge_catalogues(a, empty)) == 2
    merged = bc.merge_catalogues(a, b)
    assert merged.set_index("bus_id").loc[1, "barra_info"] == "Alpha2"

    assert bc.filter_catalogue(empty, region="north").empty
    assert list(bc.filter_catalogue(a, bus_ids={2})["bus_id"]) == [2]
    assert list(bc.filter_catalogue(a, name_regex="alp")["bus_id"]) == [1]
    assert list(bc.filter_catalogue(a, region="south")["bus_id"]) == [2]
    assert list(bc.filter_catalogue(a, min_voltage=200)["bus_id"]) == [1]
    assert len(bc.filter_catalogue(a)) == 2

    assert bc.to_ref_buses(empty) == []
    assert bc.to_ref_buses(a) == [("A_220", 1, "Alpha"), ("B_110", 2, "Beta")]
