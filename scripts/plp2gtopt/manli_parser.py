# -*- coding: utf-8 -*-

"""Parser for plpmanli.dat format files containing line maintenance data.

Handles:
- File parsing and validation
- Line maintenance data structure creation
- Maintenance lookup by line name
"""

from pathlib import Path
from typing import Dict, List, Any, Optional
import numpy as np

from .base_parser import BaseParser
from .compressed_open import compressed_open, find_compressed_path


class ManliParser(BaseParser):
    """Parser for plpmanli.dat format files containing line maintenance data."""

    @property
    def manlis(self) -> List[Dict]:
        """Return the maintenance entries."""
        return self.get_all()

    @property
    def num_manlis(self) -> int:
        """Return the number of maintenance entries in the file."""
        return len(self.manlis)

    def parse(self, parsers: Optional[dict[str, Any]] = None) -> None:
        """Parse the maintenance file and populate the data structure."""
        self.validate_file()

        try:
            lines = self._read_non_empty_lines()
            if not lines:
                raise ValueError("The line maintenance file is empty or malformed.")

            idx = self._next_idx(-1, lines)
            num_lines = self._parse_int(lines[idx])

            for _ in range(num_lines):
                # Get line name
                idx = self._next_idx(idx, lines)
                name = lines[idx].strip("'")

                # Get number of blocks
                idx = self._next_idx(idx, lines)
                parts = lines[idx].split()
                num_blocks = self._parse_int(parts[0])
                if num_blocks <= 0:
                    continue

                # Initialize numpy arrays
                blocks = np.empty(num_blocks, dtype=np.int32)
                p_max_ab = np.empty(num_blocks, dtype=np.float64)
                p_max_ba = np.empty(num_blocks, dtype=np.float64)
                operational = np.empty(num_blocks, dtype=np.int8)

                # Parse maintenance entries
                for i in range(num_blocks):
                    idx = self._next_idx(idx, lines)
                    parts = lines[idx].split()
                    if len(parts) < 4:
                        raise ValueError(f"Invalid maintenance entry at line {idx + 1}")

                    blocks[i] = self._parse_int(parts[0])  # Block number
                    p_max_ab[i] = self._parse_float(parts[1])  # Max flow AB
                    p_max_ba[i] = self._parse_float(parts[2])  # Max flow BA
                    operational[i] = np.int8(parts[3] == "T")  # Operational status

                # Store complete data
                manli = {
                    "name": name,
                    "block": blocks,
                    "tmax_ab": p_max_ab,
                    "tmax_ba": p_max_ba,
                    "operational": operational,
                }

                self._append(manli)

        finally:
            lines.clear()

        self._merge_mantlin_csv()

    # ------------------------------------------------------------------
    # mantlin.csv (CEN PLP extension, leemanli.f ``NArcManLin2``)
    # ------------------------------------------------------------------
    MANTLIN_FILE = "mantlin.csv"

    def _merge_mantlin_csv(self) -> None:
        """Merge the optional ``mantlin.csv`` sibling into the entries.

        Rows are ``NomLin,EtaIni,EtaFin,ManALin,ManBLin,VNomLin,ResLin,
        XImpLin,FOpeLin`` where ``EtaIni``/``EtaFin`` are an inclusive
        **block** range (the header says "Eta" but ``leemanli.f`` loops
        ``NumBlo = BloIni, BloFin``).  PLP reads it after ``plpmanli.dat``
        and each row overwrites capacity, operative flag, nominal voltage,
        resistance and reactance on its blocks, so later rows (and this
        file over ``plpmanli.dat``) win.  The resulting per-block arrays
        replace the line's ``plpmanli.dat`` entry; ``r``/``x``/``voltage``
        arrays are added (base ``plpcnfli.dat`` values are filled in by
        the writer for blocks no row covers).
        """
        path = find_compressed_path(Path(self.file_path).parent / self.MANTLIN_FILE)
        if path is None:
            return

        per_line: Dict[str, Dict[int, tuple]] = {}
        order: List[str] = []
        with compressed_open(path, encoding="latin-1") as fh:
            for raw in fh:
                text = raw.strip()
                if not text or text.startswith("#"):
                    continue
                parts = [p.strip() for p in text.split(",")]
                if len(parts) < 9:
                    raise ValueError(f"{path}: malformed mantlin row: {text!r}")
                name = parts[0].strip("'\"")
                b_ini = max(int(parts[1]), 1)
                b_fin = int(parts[2])
                rec = (
                    float(parts[3]),
                    float(parts[4]),
                    float(parts[5]),
                    float(parts[6]),
                    float(parts[7]),
                    parts[8].upper().startswith("T"),
                )
                if name not in per_line:
                    per_line[name] = {}
                    order.append(name)
                blocks = per_line[name]
                for blk in range(b_ini, b_fin + 1):
                    blocks[blk] = rec

        for name in order:
            blocks = per_line[name]
            # Seed with the plpmanli.dat entry so mantlin rows override it
            # block by block without dropping plpmanli-only blocks.
            prev = self.get_item_by_name(name)
            merged: Dict[int, tuple] = {}
            if prev is not None:
                for i, blk in enumerate(prev["block"]):
                    merged[int(blk)] = (
                        float(prev["tmax_ab"][i]),
                        float(prev["tmax_ba"][i]),
                        np.nan,
                        np.nan,
                        np.nan,
                        bool(prev["operational"][i]),
                    )
            merged.update(blocks)
            keys = np.array(sorted(merged), dtype=np.int32)
            vals = [merged[int(k)] for k in keys]
            entry = {
                "name": name,
                "block": keys,
                "tmax_ab": np.array([v[0] for v in vals], dtype=np.float64),
                "tmax_ba": np.array([v[1] for v in vals], dtype=np.float64),
                "voltage": np.array([v[2] for v in vals], dtype=np.float64),
                "r": np.array([v[3] for v in vals], dtype=np.float64),
                "x": np.array([v[4] for v in vals], dtype=np.float64),
                "operational": np.array([v[5] for v in vals], dtype=np.int8),
            }
            if prev is not None:
                prev.clear()
                prev.update(entry)
            else:
                self._append(entry)

    def get_manli_by_name(self, name: str) -> Optional[Dict]:
        """Get maintenance data for a specific line name."""
        return self.get_item_by_name(name)
