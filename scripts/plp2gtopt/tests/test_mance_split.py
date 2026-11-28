"""Tests for ``--mance-split`` (plpmance maintenance → outage_fraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from ..mance_split import (
    MANCE_SPLIT_MODES,
    maintenance_centrals_from_xlsm,
    maintenance_positions,
    split_mances,
)
from ..mance_writer import ManceWriter

# 6 stages × 2 blocks: block b belongs to stage (b + 1) // 2.
NBLOCKS = 12


def _stage_of(block: int) -> int:
    return (block + 1) // 2


def _blocks() -> np.ndarray:
    return np.arange(1, NBLOCKS + 1, dtype=np.int32)


def _item(name: str, pmax: List[float], pmin: float = 0.0) -> Dict[str, Any]:
    """A mance item with one pmax value per STAGE (expanded to blocks)."""
    per_block = np.repeat(np.asarray(pmax, dtype=float), 2)
    return {
        "name": name,
        "block": _blocks(),
        "pmin": np.full(NBLOCKS, pmin, dtype=float),
        "pmax": per_block,
    }


def _central(name: str, ctype: str = "termica", pmax: float = 100.0) -> Dict:
    return {"name": name, "number": 1, "type": ctype, "pmax": pmax}


def _groups() -> Dict[int, List[int]]:
    groups: Dict[int, List[int]] = {}
    for pos, b in enumerate(_blocks().tolist()):
        groups.setdefault(_stage_of(b), []).append(pos)
    return groups


class TestMaintenancePositions:
    """The stage rule."""

    def test_interior_derate_is_maintenance(self):
        pmax = np.repeat([100.0, 75.0, 100.0, 0.0, 100.0, 100.0], 2)
        found = maintenance_positions(pmax, 100.0, _groups())
        assert [(pos, round(f, 6)) for pos, f in found] == [
            ([2, 3], 0.25),
            ([6, 7], 1.0),
        ]

    def test_entry_and_retirement_zero_runs_are_not(self):
        pmax = np.repeat([0.0, 0.0, 50.0, 100.0, 0.0, 0.0], 2)
        found = maintenance_positions(pmax, 100.0, _groups())
        assert found == [([4, 5], 0.5)]

    def test_all_zero_central_is_not(self):
        pmax = np.zeros(NBLOCKS)
        assert not maintenance_positions(pmax, 100.0, _groups())

    def test_non_uniform_stage_is_a_profile(self):
        pmax = np.repeat([100.0] * 6, 2)
        pmax[2], pmax[3] = 40.0, 60.0  # varies inside stage 2
        assert not maintenance_positions(pmax, 100.0, _groups())

    def test_above_nominal_and_zero_pnom(self):
        pmax = np.repeat([120.0] * 6, 2)
        assert not maintenance_positions(pmax, 100.0, _groups())
        assert not maintenance_positions(pmax, 0.0, _groups())
        assert not maintenance_positions(pmax, 100.0, {})


class TestSplitMances:
    """The item-level split."""

    def test_off_is_identity(self):
        items = [_item("G", [100, 50, 100, 100, 100, 100])]
        adj, out = split_mances(items, [_central("G")], _stage_of, "off")
        assert adj == items
        assert not out

    def test_unknown_mode(self):
        with pytest.raises(ValueError, match="unknown mode"):
            split_mances([], [], _stage_of, "bogus")
        assert MANCE_SPLIT_MODES == ("off", "auto", "xlsm")

    def test_xlsm_needs_names(self):
        with pytest.raises(ValueError, match="mance-xlsm"):
            split_mances([], [], _stage_of, "xlsm")

    def test_auto_thermal_split_keeps_effective_bounds(self):
        item = _item("G", [100, 50, 100, 100, 100, 100], pmin=20.0)
        adj, out = split_mances([item], [_central("G")], _stage_of, "auto")
        assert len(out) == 1
        frac = out[0]["outage_fraction"]
        assert frac.tolist() == [0, 0, 0.5, 0.5] + [0] * 8
        new = adj[0]
        assert new["pmax"].tolist() == [100.0] * NBLOCKS
        # Invariant: the effective bounds (1 − f)·[pmin, pmax] are the
        # original plpmance bounds on every block.
        np.testing.assert_allclose((1 - frac) * new["pmax"], item["pmax"])
        np.testing.assert_allclose((1 - frac) * new["pmin"], item["pmin"])
        # The input item is not mutated.
        assert item["pmax"].tolist()[2] == 50.0

    def test_auto_pmin_capped_at_nominal(self):
        item = _item("G", [100, 10, 100, 100, 100, 100], pmin=60.0)
        adj, _ = split_mances([item], [_central("G")], _stage_of, "auto")
        assert adj[0]["pmin"][2] == pytest.approx(100.0)

    def test_auto_skips_non_thermal_and_unknown(self):
        items = [
            _item("H", [100, 50, 100, 100, 100, 100]),
            _item("X", [100, 50, 100, 100, 100, 100]),
            _item("G", [100] * 6),
        ]
        centrals = [_central("H", "embalse"), _central("G")]
        adj, out = split_mances(items, centrals, _stage_of, "auto")
        assert adj == items
        assert not out

    def test_xlsm_uses_the_workbook_set(self):
        items = [
            _item("H", [100, 50, 100, 100, 100, 100]),
            _item("G", [100, 50, 100, 100, 100, 100]),
        ]
        centrals = [_central("H", "embalse"), _central("G")]
        _, out = split_mances(items, centrals, _stage_of, "xlsm", maintained={"H"})
        assert [o["name"] for o in out] == ["H"]


def _workbook(path: Path, sheet: str, header: str, names: List[str]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Path"
    ms = wb.create_sheet(sheet)
    ms.append(["Mantenimiento Mayor (PMM)"])
    ms.append([None, header, "Inicio", "Fin", "Pmin", "Pmax"])
    for name in names:
        ms.append([None, name, "2026-01-01", "2026-01-10", 0, 0])
    ms.append([None, None])
    wb.save(path)
    return path


class TestXlsm:
    """Reading the maintained centrals from the PLP workbook."""

    def test_mant_mayor_sheet(self, tmp_path):
        p = _workbook(tmp_path / "w.xlsx", "Mant.Mayor(4)", "Central", ["A", "B", "A"])
        assert maintenance_centrals_from_xlsm(p) == {"A", "B"}

    def test_mantcen_fallback_accent_header(self, tmp_path):
        p = _workbook(tmp_path / "w.xlsx", "MantCEN", "NOMBRE", ["C"])
        assert maintenance_centrals_from_xlsm(p) == {"C"}

    def test_missing_sheet_or_header(self, tmp_path):
        p = _workbook(tmp_path / "a.xlsx", "Otra", "Central", ["A"])
        with pytest.raises(ValueError, match="no maintenance sheet"):
            maintenance_centrals_from_xlsm(p)
        q = _workbook(tmp_path / "b.xlsx", "MantCEN", "Unidad", ["A"])
        with pytest.raises(ValueError, match="no CENTRAL header"):
            maintenance_centrals_from_xlsm(q)


class _Parser:
    """Minimal parser stub (items + lookups)."""

    def __init__(self, items: List[Dict[str, Any]]):
        self.items = items

    def get_item_by_name(self, name: str):
        return next((i for i in self.items if i["name"] == name), None)

    @staticmethod
    def get_stage_number(block: int) -> int:
        return _stage_of(int(block))


class _Mance(_Parser):
    def get_all(self) -> List[Dict[str, Any]]:
        return self.items


def _writer(options: Dict[str, Any]) -> ManceWriter:
    centrals = _Parser([_central("G"), _central("H", "embalse") | {"number": 2}])
    blocks = _Parser([{"number": int(b)} for b in _blocks()])
    mance = _Mance(
        [
            _item("G", [100, 50, 100, 100, 100, 100], pmin=10.0),
            _item("H", [100, 30, 100, 100, 100, 100]),
        ]
    )
    return ManceWriter(mance, centrals, blocks, options)  # type: ignore[arg-type]


class TestManceWriter:
    """End to end through ManceWriter.to_parquet."""

    def test_default_off_writes_no_outage(self, tmp_path):
        cols = _writer({"output_dir": tmp_path}).to_parquet(tmp_path)
        assert not cols["outage_fraction"]
        assert not (tmp_path / "outage_fraction.parquet").exists()
        assert cols["pmax"]

    def test_auto_writes_outage_and_nominal_pmax(self, tmp_path):
        w = _writer({"output_dir": tmp_path, "mance_split": "auto"})
        cols = w.to_parquet(tmp_path)
        assert cols["outage_fraction"] == ["block", "uid:1", "stage"]
        assert (tmp_path / "outage_fraction.parquet").exists()
        _, outages = w.split_outages()
        df = w.outage_dataframe(outages)
        assert df["uid:1"].tolist() == [0, 0, 0.5, 0.5] + [0] * 8
        assert df["stage"].tolist() == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
        # G's pmax is nominal everywhere → no pmax column for it; the
        # hydro central H is untouched in auto mode.
        assert "uid:1" not in cols["pmax"]
        assert "uid:2" in cols["pmax"]

    def test_xlsm_requires_file(self, tmp_path):
        w = _writer({"mance_split": "xlsm"})
        with pytest.raises(ValueError, match="mance-xlsm"):
            w.to_parquet(tmp_path)

    def test_xlsm_mode(self, tmp_path):
        book = _workbook(tmp_path / "w.xlsx", "Mant.Mayor(4)", "CENTRAL", ["H"])
        w = _writer({"mance_split": "xlsm", "mance_xlsm": book})
        cols = w.to_parquet(tmp_path)
        assert cols["outage_fraction"] == ["block", "uid:2", "stage"]

    def test_needs_parsers(self):
        w = ManceWriter(
            _Mance([_item("G", [100] * 6)]), None, None, {"mance_split": "auto"}
        )  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="central and block"):
            w.split_outages()
        assert w.outage_dataframe([]).empty


class TestCli:
    """``--mance-split`` / ``--mance-xlsm`` plumbing."""

    def test_default_off(self):
        from ..main import build_options, make_parser  # noqa: PLC0415

        opts = build_options(make_parser().parse_args([]))
        assert opts["mance_split"] == "off"
        assert opts["mance_xlsm"] is None

    def test_auto_and_xlsm(self, tmp_path):
        from ..main import build_options, make_parser  # noqa: PLC0415

        opts = build_options(make_parser().parse_args(["--mance-split", "auto"]))
        assert opts["mance_split"] == "auto"
        book = tmp_path / "w.xlsm"
        args = make_parser().parse_args(
            ["--mance-split", "xlsm", "--mance-xlsm", str(book)]
        )
        assert build_options(args)["mance_xlsm"] == book
        with pytest.raises(ValueError, match="mance-xlsm"):
            build_options(make_parser().parse_args(["--mance-split", "xlsm"]))
        with pytest.raises(SystemExit):
            make_parser().parse_args(["--mance-split", "bogus"])
