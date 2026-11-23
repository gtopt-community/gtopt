#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Unit tests for GeneratorProfileWriter class."""

import typing
from typing import Any, Dict, List
import pytest
from ..generator_profile_writer import GeneratorProfileWriter


# ---------------------------------------------------------------------------
# Mock parsers
# ---------------------------------------------------------------------------


class MockCentralParser:
    """Minimal CentralParser stub."""

    def __init__(self, centrals: List[Dict[str, Any]]):
        self._data = centrals
        self._by_name = {c["name"]: c for c in centrals}
        self.centrals_of_type: Dict[str, List[Dict[str, Any]]] = {}
        for c in centrals:
            ctype = c.get("type", "unknown")
            self.centrals_of_type.setdefault(ctype, []).append(c)

    @property
    def items(self):
        return self._data

    @property
    def num_centrals(self):
        return len(self._data)

    def get_all(self):
        return self._data

    def get_item_by_name(self, name: str):
        return self._by_name.get(name)


class MockBusParser:
    """Minimal BusParser stub."""

    def __init__(self, buses: List[Dict[str, Any]]):
        self._buses = {b["number"]: b for b in buses}

    def get_bus_by_number(self, number: int):
        return self._buses.get(number)


class MockAflceParser:
    """Minimal AflceParser stub."""

    def __init__(self, flows: List[Dict[str, Any]]):
        self._flows = {f["name"]: f for f in flows}

    def get_item_by_name(self, name: str):
        return self._flows.get(name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_writer():
    """GeneratorProfileWriter with no parsers returns empty array."""
    writer = GeneratorProfileWriter()
    assert not writer.to_json_array()


def test_pasada_with_aflce():
    """Pasada pura central with aflce data produces Flow@discharge profile."""
    centrals = [
        {
            "number": 1,
            "name": "HydroGen",
            "type": "pasada",
            "bus": 1,
            "pmax": 200.0,
            "afluent": 50.0,
        },
    ]
    buses = [{"number": 1, "name": "Bus1"}]
    flows = [{"name": "HydroGen", "flow": [[50.0], [80.0]]}]

    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
        bus_parser=typing.cast(typing.Any, MockBusParser(buses)),
        aflce_parser=typing.cast(typing.Any, MockAflceParser(flows)),
    )
    result = writer.to_json_array()

    assert len(result) == 1
    assert result[0]["uid"] == 1
    assert result[0]["name"] == "HydroGen"
    assert result[0]["owner_kind"] == "generator"
    assert result[0]["owner"] == "HydroGen"
    assert result[0]["profile"] == "Flow@discharge"


def test_pasada_without_aflce_uses_afluent():
    """Pasada without aflce parser uses inline afluent (MW) as per-unit profile."""
    centrals = [
        {
            "number": 1,
            "name": "HydroGen",
            "type": "pasada",
            "bus": 1,
            "pmax": 200.0,
            "afluent": 50.0,
        },
    ]
    buses = [{"number": 1, "name": "Bus1"}]

    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
        bus_parser=typing.cast(typing.Any, MockBusParser(buses)),
    )
    result = writer.to_json_array()

    assert len(result) == 1
    # 50 MW afluent / 200 MW PotMax = 0.25 per unit.
    assert result[0]["profile"] == pytest.approx(0.25)


def test_pasada_zero_afluent_skipped():
    """Pasada with zero afluent and no aflce data is skipped."""
    centrals = [
        {
            "number": 1,
            "name": "NoFlow",
            "type": "pasada",
            "bus": 1,
            "pmax": 100.0,
            "afluent": 0.0,
        },
    ]
    buses = [{"number": 1, "name": "Bus1"}]

    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
        bus_parser=typing.cast(typing.Any, MockBusParser(buses)),
    )
    assert not writer.to_json_array()


def test_invalid_bus_skipped():
    """Central with bus not in bus_parser is skipped."""
    centrals = [
        {
            "number": 1,
            "name": "HydroGen",
            "type": "pasada",
            "bus": 99,
            "pmax": 200.0,
            "afluent": 50.0,
        },
    ]
    buses = [{"number": 1, "name": "Bus1"}]

    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
        bus_parser=typing.cast(typing.Any, MockBusParser(buses)),
    )
    assert not writer.to_json_array()


def test_profile_parquet_filename(tmp_path):
    """Profile mode writes profile.parquet (not discharge.parquet)."""
    import numpy as np

    centrals = [
        {
            "number": 1,
            "name": "Solar1",
            "type": "pasada",
            "bus": 1,
            "pmax": 100.0,
            "afluent": 0.5,
        },
    ]

    # Create a minimal aflce parser with flow data
    class FullAflceParser:
        def __init__(self):
            self._flows = [
                {
                    "name": "Solar1",
                    "block": np.array([1, 2], dtype=np.int32),
                    "flow": np.array([[0.5], [0.8]], dtype=np.float64),
                    "num_hydrologies": 1,
                }
            ]

        @property
        def flows(self):
            return self._flows

        def get_all(self):
            return self._flows

        def get_item_by_name(self, name):
            for f in self._flows:
                if f["name"] == name:
                    return f
            return None

    scenarios = [{"uid": 1, "hydrology": 0}]
    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
        bus_parser=typing.cast(
            typing.Any, MockBusParser([{"number": 1, "name": "B1"}])
        ),
        aflce_parser=typing.cast(typing.Any, FullAflceParser()),
        block_parser=None,
        scenarios=scenarios,
        options={
            "output_dir": tmp_path,
            "compression": "gzip",
            "_pasada_profile_names": {"Solar1"},
        },
    )
    writer.to_json_array()

    profile_dir = tmp_path / "GeneratorProfile"
    assert (profile_dir / "profile.parquet").exists(), (
        "Should write profile.parquet, not discharge.parquet"
    )
    assert not (profile_dir / "discharge.parquet").exists(), (
        "Should NOT write discharge.parquet in GeneratorProfile/"
    )


def test_zero_bus_skipped():
    """Central with bus 0 is skipped."""
    centrals = [
        {
            "number": 1,
            "name": "HydroGen",
            "type": "pasada",
            "bus": 0,
            "pmax": 200.0,
            "afluent": 50.0,
        },
    ]

    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
    )
    assert not writer.to_json_array()


class _FullAflceParser:
    """Aflce stub carrying 2-D flow arrays (blocks x hydrologies)."""

    def __init__(self, flows: List[Dict[str, Any]]):
        self._flows = flows

    @property
    def flows(self):
        return self._flows

    def get_all(self):
        return self._flows

    def get_item_by_name(self, name):
        return next((f for f in self._flows if f["name"] == name), None)


def _write_profile(tmp_path, centrals, flows):
    import pandas as pd  # pylint: disable=import-outside-toplevel

    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
        bus_parser=typing.cast(
            typing.Any, MockBusParser([{"number": 1, "name": "B1"}])
        ),
        aflce_parser=typing.cast(typing.Any, _FullAflceParser(flows)),
        block_parser=None,
        scenarios=[{"uid": 1, "hydrology": 0}],
        options={
            "output_dir": tmp_path,
            "compression": "gzip",
            "_pasada_profile_names": {c["name"] for c in centrals},
        },
    )
    result = writer.to_json_array()
    df = pd.read_parquet(tmp_path / "GeneratorProfile" / "profile.parquet")
    return result, df


def test_profile_parquet_is_per_unit_of_capacity(tmp_path, caplog):
    """Solar aflce in MW becomes aflce * Rendi / PotMax, clipped to [0, 1]."""
    import numpy as np  # pylint: disable=import-outside-toplevel

    centrals = [
        {
            "number": 7,
            "name": "FINIS_FV",
            "type": "pasada",
            "bus": 1,
            "pmax": 281.8,
            "efficiency": 1.0,
            "afluent": 0.0,
        },
    ]
    flows = [
        {
            "name": "FINIS_FV",
            "block": np.array([1, 2, 3, 4], dtype=np.int32),
            # MW-equivalent PLP values; 300 > PotMax is clipped to 1.
            "flow": np.array([[0.0], [63.6], [281.72], [300.0]]),
            "num_hydrologies": 1,
        },
    ]
    with caplog.at_level("WARNING"):
        result, df = _write_profile(tmp_path, centrals, flows)
    assert result[0]["profile"] == "profile"
    vals = df[df["uid"] == 7]["value"].to_numpy()
    assert vals.max() <= 1.0
    assert vals.min() >= 0.0
    assert vals[1] == pytest.approx(63.6 / 281.8)
    assert vals[2] == pytest.approx(281.72 / 281.8)
    assert vals[3] == pytest.approx(1.0)
    assert "clipped 1 per-unit profile value" in caplog.text


def test_profile_parquet_uses_rendimiento_and_zero_capacity(tmp_path, caplog):
    """Rendi scales aflce; PotMax <= 0 yields a zero profile with a warning."""
    import numpy as np  # pylint: disable=import-outside-toplevel

    centrals = [
        {
            "number": 1,
            "name": "ROR",
            "type": "pasada",
            "bus": 1,
            "pmax": 10.0,
            "efficiency": 2.0,
            "afluent": 0.0,
        },
        {
            "number": 2,
            "name": "NOCAP",
            "type": "pasada",
            "bus": 1,
            "pmax": 0.0,
            "afluent": 0.0,
        },
    ]
    flows = [
        {
            "name": n,
            "block": np.array([1, 2], dtype=np.int32),
            "flow": np.array([[1.0], [2.5]]),
            "num_hydrologies": 1,
        }
        for n in ("ROR", "NOCAP")
    ]
    with caplog.at_level("WARNING"):
        _, df = _write_profile(tmp_path, centrals, flows)
    assert df[df["uid"] == 1]["value"].tolist() == pytest.approx([0.2, 0.5])
    assert df[df["uid"] == 2]["value"].tolist() == pytest.approx([0.0, 0.0])
    assert "PotMax <= 0" in caplog.text


def test_profile_factor_helpers():
    """Scalar helpers handle bad / missing capacity robustly."""
    # pylint: disable=import-outside-toplevel
    from ..generator_profile_writer import profile_factor, to_per_unit

    assert profile_factor({"pmax": 50.0}) == pytest.approx(0.02)
    assert profile_factor({"pmax": "bad"}) == 0.0
    assert profile_factor({"pmax": float("nan")}) == 0.0
    assert to_per_unit(80.0, {"pmax": 50.0}) == 1.0
    assert to_per_unit(-5.0, {"pmax": 50.0}) == 0.0


def test_normalize_profile_columns_only_touches_profile_columns():
    """Only listed profile centrals with a column are scaled to per-unit.

    A pasada central outside the profile set (a hydro run-of-river) keeps
    its raw column, and a profile central without a column in the frame
    is skipped without inventing one.
    """
    import pandas as pd  # pylint: disable=import-outside-toplevel

    centrals = [
        {"number": 1, "name": "SOLAR", "type": "pasada", "pmax": 50.0},
        {"number": 2, "name": "HYDRO_ROR", "type": "pasada", "pmax": 10.0},
        {"number": 3, "name": "NO_COLUMN", "type": "pasada", "pmax": 20.0},
    ]
    writer = GeneratorProfileWriter(
        central_parser=typing.cast(typing.Any, MockCentralParser(centrals)),
    )
    df = pd.DataFrame(
        {
            "block": [1, 2],
            "uid:1": [25.0, 60.0],
            "uid:2": [7.0, 30.0],
        }
    )
    # pylint: disable-next=protected-access
    writer._normalize_profile_columns(df, {"SOLAR", "NO_COLUMN"})
    assert df["uid:1"].tolist() == pytest.approx([0.5, 1.0])
    assert df["uid:2"].tolist() == pytest.approx([7.0, 30.0])
    assert "uid:3" not in df.columns
