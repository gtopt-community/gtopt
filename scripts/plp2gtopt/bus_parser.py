# -*- coding: utf-8 -*-

"""Parser for plpbar.dat format files containing bus data.

Handles:
- File parsing and validation
- Bus data structure creation
- Bus lookup by name
"""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

from gtopt_shared.bus_kv import SEN_KV_LEVELS

from .base_parser import BaseParser

_DIGITS = re.compile(r"\d+")


def _sen_suffix_kv(group: str) -> Optional[float]:
    """Return the SEN level a digit ``group`` ends with, else ``None``.

    ``"1220"`` (bus ``OGP1220``: unit 1 at 220 kV) → 220.  The longest
    matching level wins (``"1500"`` → 500, not 0); the group must be
    strictly longer than the level (an exact match is handled upstream).
    """
    for kv in sorted(SEN_KV_LEVELS, key=lambda k: -len(str(k))):
        tag = str(kv)
        if len(group) > len(tag) and group.endswith(tag):
            return float(kv)
    return None


def resolve_bus_voltages(
    buses: Iterable[MutableMapping[str, Any]],
    lines: Iterable[Mapping[str, Any]],
) -> int:
    """Fix bus voltages the name did not give as a SEN level, in place.

    ``plpbar.dat`` carries no voltage, so :class:`BusParser` reads it
    from the bus name.  When that is not a SEN level (``OGP1220`` →
    1220, ``Ancoa500auxS`` → none) the ``Tension`` of the incident
    ``plpcnfli.dat`` lines is authoritative: a single incident level
    wins; with several (a transformer bus), the one the name's digits
    end with.  Buses whose name already gives a SEN level are left
    untouched (transformer lines carry the HV-side ``Tension``, so the
    name is the better source there).  Returns the number of buses
    changed.
    """
    incident: dict[int, set[float]] = defaultdict(set)
    for ln in lines:
        kv = float(ln.get("voltage") or 0.0)
        if kv > 0.0:
            incident[int(ln["bus_a"])].add(kv)
            incident[int(ln["bus_b"])].add(kv)
    changed = 0
    for bus in buses:
        if bus.get("voltage") in SEN_KV_LEVELS:
            continue
        cands = incident.get(int(bus["number"]), set())
        groups = _DIGITS.findall(str(bus["name"]))
        if len(cands) > 1:
            cands = {
                kv for kv in cands if any(g.endswith(str(int(kv))) for g in groups)
            }
        if len(cands) == 1:
            kv = next(iter(cands))
        else:
            continue
        if kv != bus.get("voltage"):
            bus["voltage"] = kv
            changed += 1
    return changed


class BusParser(BaseParser):
    """Parser for plpbar.dat format files containing bus data."""

    def __init__(self, file_path: str | Path) -> None:
        """Initialize the bus parser.

        Args:
            file_path: Path to the bus data file (plpbar.dat)

        Raises:
            TypeError: If file_path is not str or Path
            ValueError: If file_path is empty
        """
        super().__init__(file_path)
        self.bus_num_map: Dict[int, int] = {}

    @property
    def buses(self) -> List[Dict[str, str | float | bool]]:
        """Return the parsed buses structure."""
        return self.get_all()

    @property
    def num_buses(self) -> int:
        """Return the number of buses in the file."""
        return len(self.buses)

    def parse(self, parsers: Optional[dict[str, Any]] = None) -> None:
        """Parse the bus file and populate the buses structure."""
        self.validate_file()
        lines = self._read_non_empty_lines()
        if not lines:
            raise ValueError("File is empty")

        idx = 0
        num_buses = self._parse_int(lines[idx])
        idx += 1

        for _ in range(num_buses):
            # Parse bus line with format: "number 'name' [voltage]"
            line = lines[idx].strip()
            idx += 1

            # Split into number and quoted name (voltage is optional)
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                raise ValueError(f"Invalid bus entry at line {idx}")

            bus_num = self._parse_int(parts[0])
            name = parts[1].strip("'")

            # Try to extract voltage from name (handles various patterns)
            voltage_match = re.search(
                r"(\d+)(?:kV|KV)?(?:[-_].*)?$", name, re.IGNORECASE
            )
            if voltage_match:
                voltage = float(voltage_match.group(1))
                if int(voltage) not in SEN_KV_LEVELS:
                    # ``OGP1220`` (unit 1 at 220 kV): the greedy group
                    # glues a unit index to the level.  Fall back to the
                    # SEN level the group ends with; the incident lines
                    # refine it later (:func:`resolve_bus_voltages`).
                    voltage = _sen_suffix_kv(voltage_match.group(1)) or voltage
            else:
                # Try alternative patterns like 'KV220' format
                voltage_match = re.search(r"(?:kV|KV)(\d+)", name, re.IGNORECASE)
                if voltage_match:
                    voltage = float(voltage_match.group(1))
                else:
                    # Default to 1 if no voltage found in name
                    voltage = 1.0

            if bus_num > 0:
                self._append({"number": bus_num, "name": name, "voltage": voltage})
                self.bus_num_map[bus_num] = self.num_buses - 1

    def get_bus_by_number(self, number: int) -> Optional[Dict[str, Any]]:
        """Get bus by bus number."""
        return self.get_item_by_number(number)

    def get_bus_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get bus by bus name."""
        if not isinstance(name, str):
            raise TypeError("name must be str")
        return self.get_item_by_name(name)
