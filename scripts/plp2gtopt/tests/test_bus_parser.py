"""Unit tests for bus_parser.py BusParser class."""

from pathlib import Path
import pytest
from ..bus_parser import BusParser, resolve_bus_voltages
from .conftest import get_example_file


@pytest.fixture
def sample_bus_file():
    """Fixture providing path to sample bus file."""
    return get_example_file("plpbar.dat")


def test_bus_parser_initialization():
    """Test BusParser initialization."""
    test_path = "test.dat"
    parser = BusParser(test_path)
    assert parser.file_path == Path(test_path)
    # assert parser.num_buses == 0


def test_parse_sample_file(sample_bus_file):  # pylint: disable=redefined-outer-name
    """Test parsing of the sample bus file."""
    parser = BusParser(str(sample_bus_file))
    parser.parse()

    # Verify basic structure
    num_buses = parser.num_buses
    assert num_buses == 10
    buses = parser.buses
    assert len(buses) == num_buses

    # Verify all buses have required fields
    for bus in buses:
        assert isinstance(bus["number"], int)
        assert isinstance(bus["name"], str)
        assert isinstance(bus["voltage"], float)
        assert bus["number"] > 0
        assert bus["name"] != ""
        assert bus["voltage"] > 0

    # Verify first bus data
    bus1 = buses[0]
    assert bus1["number"] == 1
    assert bus1["name"] == "AltoNorte110"
    assert bus1["voltage"] == 110.0

    # Verify last bus data
    last_bus = buses[-1]
    assert last_bus["number"] == 10
    assert last_bus["name"] == "Capricornio220"
    assert last_bus["voltage"] == 220.0

    # Verify bus numbers are sequential
    for i, bus in enumerate(buses, 1):
        assert bus["number"] == i

    # Verify voltage extraction from names
    test_cases = [
        ("AltoNorte110", 110.0),
        ("Andes220", 220.0),
        ("Andes345", 345.0),
        ("Arica066", 66.0),
        ("Atacama220_BP1", 220.0),
        ("Capricornio110", 110.0),
    ]
    for name, expected_voltage in test_cases:
        assert any(
            bus["name"] == name and bus["voltage"] == expected_voltage for bus in buses
        )


def test_get_bus_by_name(sample_bus_file):  # pylint: disable=redefined-outer-name
    """Test getting bus by name."""
    parser = BusParser(str(sample_bus_file))
    parser.parse()

    # Test existing bus
    buses = parser.buses
    first_bus_name = buses[0]["name"]
    bus = parser.get_bus_by_name(str(first_bus_name))

    assert bus is not None
    assert bus["name"] == first_bus_name

    # Test non-existent bus
    missing = parser.get_bus_by_name("NonExistentBus")
    assert missing is None


def _write_plpbar(tmp_path: Path, names: list[str]) -> Path:
    body = "".join(f"  {i}  '{n}'\n" for i, n in enumerate(names, 1))
    path = tmp_path / "plpbar.dat"
    path.write_text(
        f"# Archivo con definicion de Barras (plpbar.dat)\n# Numero de Barras\n"
        f"  {len(names)}\n# Numero  Nombre\n{body}",
        encoding="utf-8",
    )
    return path


def test_parse_unit_index_glued_to_kv(tmp_path):
    """``OGP1220`` (unit 1 at 220 kV) parses to 220, not 1220."""
    parser = BusParser(_write_plpbar(tmp_path, ["OGP1220", "Tamaya033", "b7"]))
    parser.parse()
    assert [b["voltage"] for b in parser.buses] == [220.0, 33.0, 7.0]


def test_resolve_bus_voltages_from_incident_lines():
    """Incident ``plpcnfli`` Tension fixes names without a SEN level."""
    buses = [
        {"number": 1, "name": "OGP1220", "voltage": 1220.0},
        {"number": 2, "name": "Ancoa500auxS", "voltage": 1.0},
        {"number": 3, "name": "Ancoa500", "voltage": 500.0},
        {"number": 4, "name": "Domeyko220", "voltage": 220.0},
        {"number": 5, "name": "Tap154x", "voltage": 1.0},
        {"number": 6, "name": "Tap66x", "voltage": 1.0},
        {"number": 7, "name": "Lonely9", "voltage": 9.0},
        {"number": 8, "name": "Mix1", "voltage": 1.0},
    ]
    lines = [
        {"bus_a": 1, "bus_b": 4, "voltage": 220.0},
        {"bus_a": 2, "bus_b": 3, "voltage": 500.0},
        # A transformer bus: two levels; the name's digits pick one.
        {"bus_a": 5, "bus_b": 4, "voltage": 220.0},
        {"bus_a": 5, "bus_b": 6, "voltage": 154.0},
        # Bus 6: two levels, none matching the name -> left alone.
        {"bus_a": 6, "bus_b": 4, "voltage": 220.0},
        # Bus 8: two levels, no name match -> left alone.
        {"bus_a": 8, "bus_b": 3, "voltage": 500.0},
        {"bus_a": 8, "bus_b": 4, "voltage": 220.0},
        {"bus_a": 8, "bus_b": 4, "voltage": 0.0},  # no Tension: ignored
    ]
    assert resolve_bus_voltages(buses, lines) == 3
    kv = {b["name"]: b["voltage"] for b in buses}
    assert kv["OGP1220"] == 220.0
    assert kv["Ancoa500auxS"] == 500.0
    assert kv["Tap154x"] == 154.0
    # SEN-level names are never overridden (transformer Tension = HV side).
    assert kv["Ancoa500"] == 500.0 and kv["Domeyko220"] == 220.0
    assert kv["Tap66x"] == 1.0
    assert kv["Lonely9"] == 9.0  # no incident line
    assert kv["Mix1"] == 1.0
