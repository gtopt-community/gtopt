# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``gtopt_check_json._info`` (indicators / counts)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gtopt_check_json import _info as inf

# ---------------------------------------------------------------------------
# scalar helpers
# ---------------------------------------------------------------------------


def test_scalar_helpers() -> None:
    assert inf._block_vector({"a": 1}) is None  # noqa: SLF001
    assert inf._first_scalar(5) == 5.0  # noqa: SLF001
    assert inf._first_scalar(["x"]) is None  # noqa: SLF001
    assert inf._scalar_at_block(7, 3) == 7.0  # noqa: SLF001
    assert inf._scalar_at_block([1.0, 2.0], 5) == 2.0  # noqa: SLF001
    assert inf._scalar_at_block([1.0, "x"], 1) is None  # noqa: SLF001
    assert inf._scalar_at_block([], 0) is None  # noqa: SLF001
    assert inf._scalar_at_block(["x"], 4) is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# block totals from files
# ---------------------------------------------------------------------------


def _csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def test_block_totals_file_variants(tmp_path: Path) -> None:
    fn = inf._resolve_block_totals_from_file  # noqa: SLF001
    base = str(tmp_path)
    assert fn(base, ".", "Demand", "none", [1]) is None
    _csv(tmp_path / "Demand" / "nouid.csv", pd.DataFrame({"block": [1], "x": [1]}))
    assert fn(base, ".", "Demand", "nouid", [1]) is None
    # scenario + block -> averaged over scenarios
    _csv(
        tmp_path / "in" / "Demand" / "sc.csv",
        pd.DataFrame({"scenario": [1, 2], "block": [1, 1], "uid:1": [10.0, 30.0]}),
    )
    res = fn(base, "in", "Demand", "sc", [1, 2])
    assert res is not None
    assert res.totals == [20.0, 0.0]
    # no block column -> whole file is block 0
    _csv(tmp_path / "Demand" / "nb.csv", pd.DataFrame({"uid:1": [1.0], "uid:2": [2.0]}))
    res = fn(base, ".", "Demand", "nb", [1, 2])
    assert res is not None
    assert res.totals == [3.0, 0.0]
    assert res.num_uids == 2
    res = fn(base, ".", "Demand", "nb", [])
    assert res is not None
    assert not res.totals
    # no matching block
    _csv(tmp_path / "Demand" / "nm.csv", pd.DataFrame({"block": [9], "uid:1": [1.0]}))
    res = fn(base, ".", "Demand", "nm", [1])
    assert res is not None
    assert res.totals == [0.0]


def test_block_totals_without_file_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "gtopt_check_json._file_reader", None)
    assert (
        inf._resolve_block_totals_from_file(str(tmp_path), ".", "D", "x", [1])  # noqa: SLF001
        is None
    )
    demands = [{"uid": 1, "fcost": "fcost"}]
    assert inf._avg_demand_fcost(demands, str(tmp_path), ".") == 0.0  # noqa: SLF001


def test_avg_demand_fcost_file(tmp_path: Path) -> None:
    _csv(
        tmp_path / "in" / "Demand" / "fcost.csv",
        pd.DataFrame({"uid:1": [500.0]}),
    )
    demands = [
        {"uid": 1, "fcost": "fcost"},
        {"uid": 2, "fcost": "fcost"},  # not in file -> skipped
        {"uid": 3, "fcost": 300},
    ]
    assert inf._avg_demand_fcost(demands, str(tmp_path), "in") == 400.0  # noqa: SLF001


# ---------------------------------------------------------------------------
# compute_indicators
# ---------------------------------------------------------------------------


def _planning() -> dict[str, Any]:
    return {
        "options": {"input_directory": "in", "model_options": {"demand_fail_cost": 99}},
        "simulation": {
            "block_array": [{"uid": 1, "duration": 2.0}, {"uid": 2, "duration": 3.0}],
        },
        "system": {
            "generator_array": [
                {"uid": 1, "name": "hyd", "type": "embalse", "pmax": 100},
                {
                    "uid": 2,
                    "name": "th",
                    "type": "termica",
                    "pmax": 50,
                    "fuel": "gas",
                    "heat_rate": 2.0,
                    "gcost": 1.0,
                },
                {"uid": 3, "name": "pv", "type": "solar", "capacity": [[0.0, 40.0]]},
                {"uid": 4, "name": "odd", "type": "weird", "pmax": 5},
                {"uid": 5, "name": "turb_gen", "type": "renewable", "pmax": 20},
                {"uid": 6, "name": "noc", "type": "termica", "pmax": "pmax"},
                {"uid": 7, "name": "fail", "type": "falla", "pmax": 999},
                {"uid": 8, "name": "pv2", "type": "solar", "pmax": [10.0, "x"]},
            ],
            "turbine_array": [{"generator": "turb_gen"}, {}],
            "fuel_array": [
                {"name": "gas", "price": 3.0, "max_offtake": 10, "min_offtake": 1},
                {"name": "coal"},
            ],
            "battery_array": [
                {
                    "uid": 1,
                    "pmax_discharge": 5,
                    "input_efficiency": 0.9,
                    "output_efficiency": 0.9,
                    "emax": 20,
                },
                {"uid": 2},
            ],
            "line_array": [
                {"uid": 1, "loss_envelope": 30.0, "tmax_ab": 150},
                {"uid": 2, "tmax_ab": 10, "tmax_ba": "tmax"},
            ],
            "reserve_zone_array": [{"urreq": [1.0, 4.0]}, {"drreq": 2.0}],
            "demand_array": [
                {"uid": 1, "lmax": "lmax", "fcost": 1000},
                {"uid": 2, "lmax": "lmax"},  # same file: read once
                {"uid": 3, "lmax": [5.0, "x"]},
                {"uid": 4},
                {"uid": 5, "lmax": "missing"},
            ],
            "flow_array": [
                {"uid": 1, "discharge": "discharge"},
                {"uid": 2, "discharge": "discharge"},
                {"uid": 3, "discharge": 2.0},
                {"uid": 4, "discharge": [[1.0, 3.0]]},
                {"uid": 5, "discharge": ["x"]},
                {"uid": 6},
                {"uid": 7, "discharge": "empty"},
            ],
        },
    }


def test_compute_indicators_rich(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "plp2gtopt.tech_classify", None)
    _csv(
        tmp_path / "in" / "Demand" / "lmax.csv",
        pd.DataFrame({"block": [1, 2], "uid:1": [10.0, 20.0], "uid:2": [1.0, 2.0]}),
    )
    _csv(
        tmp_path / "in" / "Flow" / "discharge.csv",
        pd.DataFrame({"block": [1, 2], "uid:1": [4.0, 6.0]}),
    )
    _csv(tmp_path / "in" / "Flow" / "empty.csv", pd.DataFrame({"block": [1], "x": [1]}))
    ind = inf.compute_indicators(_planning(), base_dir=str(tmp_path))
    assert ind.hydro_capacity_mw == 120.0  # embalse + turbine-driven
    assert ind.thermal_capacity_mw == 50.0
    assert ind.renewable_capacity_mw == 50.0
    assert ind.total_gen_capacity_mw == 100 + 50 + 40 + 5 + 20 + 10 + 5
    assert ind.total_line_capacity_mw == 70.0
    assert ind.up_reserve_req_mw == 4.0
    assert ind.dn_reserve_req_mw == 2.0
    assert ind.total_demand_by_block == [16.0, 22.0]
    assert ind.first_block_affluent_avg == 4.0 + 2.0 + 1.0
    assert ind.last_block_affluent_avg == 6.0 + 2.0 + 3.0
    assert ind.num_flows == 3
    assert ind.max_gcost == 7.0
    assert ind.avg_battery_efficiency == pytest.approx(0.81)
    assert ind.demand_fail_cost == 99.0
    # pv: 0*2 + 40*3 ; pv2: 10*2 (block 2 is not numeric -> last value only
    # applies beyond the vector) -> 120 + 20 = 140 MWh
    assert ind.renewable_energy_gwh == pytest.approx(0.14)


def test_compute_indicators_without_blocks() -> None:
    planning = {
        "system": {
            "demand_array": [{"uid": 1, "lmax": 4.0}, {"uid": 2, "lmax": "f"}],
            "generator_array": [{"uid": 1, "pmax": 8.0, "type": "solar"}],
        }
    }
    ind = inf.compute_indicators(planning)
    assert ind.total_demand_by_block == [4.0]
    assert ind.num_blocks == 1
    assert ind.capacity_adequacy_ratio == 2.0
    empty = inf.compute_indicators({"system": {"demand_array": [{"lmax": 0.0}]}})
    assert not empty.total_demand_by_block
    assert empty.peak_demand_block == -1


# ---------------------------------------------------------------------------
# FCF / cuts / pampl
# ---------------------------------------------------------------------------


def _fcf(cuts: str = "cuts.csv") -> dict[str, Any]:
    return {
        "system": {"future_cost_array": [{"uid": 1, "name": "f", "cuts_file": cuts}]}
    }


def test_read_fcf_intercept(tmp_path: Path) -> None:
    rd = inf._read_fcf_intercept  # noqa: SLF001
    assert rd({}, str(tmp_path)) == (0.0, 0)
    assert rd(_fcf(), None) == (0.0, 0)
    assert rd(_fcf(), str(tmp_path)) == (0.0, 0)
    (tmp_path / "cuts.csv").write_text("scene,rhs,r1,r2\n", encoding="utf-8")
    assert rd(_fcf(), str(tmp_path)) == (0.0, 0)
    (tmp_path / "cuts.csv").write_text("scene,rhs,r1,r2\n1,1500.5,2,3\n", "utf-8")
    assert rd(_fcf(), str(tmp_path)) == (1500.5, 2)
    (tmp_path / "cuts.csv").write_text("scene,rhs,r1\n1,abc,2\n", "utf-8")
    assert rd(_fcf(), str(tmp_path)) == (0.0, 1)
    (tmp_path / "cuts.csv").write_text("scene,rhs\n1\n", "utf-8")
    assert rd(_fcf(), str(tmp_path)) == (0.0, 0)
    (tmp_path / "dir.csv").mkdir()
    assert rd(_fcf("dir.csv"), str(tmp_path)) == (0.0, 0)


def test_read_fcf_intercept_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "cuts.csv").write_text("a\n", "utf-8")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("denied")

    monkeypatch.setattr(Path, "open", _boom)
    assert inf._read_fcf_intercept(_fcf(), str(tmp_path)) == (0.0, 0)  # noqa: SLF001
    assert inf._count_boundary_cuts(_fcf(), str(tmp_path)) == (1, 0)  # noqa: SLF001


def test_count_boundary_cuts(tmp_path: Path) -> None:
    cnt = inf._count_boundary_cuts  # noqa: SLF001
    assert cnt(_fcf(), None) == (1, 0)
    assert cnt(_fcf(), str(tmp_path)) == (1, 0)
    (tmp_path / "cuts.csv").write_text("", "utf-8")
    assert cnt(_fcf(), str(tmp_path)) == (0, 0)
    (tmp_path / "cuts.csv").write_text("scene,rhs,a\n1,2,3\n1,4,5\n", "utf-8")
    assert cnt(_fcf(), str(tmp_path)) == (2, 1)


def test_family_from_pampl_name() -> None:
    fam = inf._family_from_pampl_name  # noqa: SLF001
    assert fam("dir/other.txt") == "operational"
    assert fam("uc_not_a_family.pampl") == "operational"
    assert fam(f"uc_{inf.UC_FAMILY_ORDER[0]}.pampl") == inf.UC_FAMILY_ORDER[0]


def test_count_pampl_uc_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "uc_empty.pampl").write_text("", "utf-8")
    (tmp_path / "unreadable.pampl").mkdir()
    planning = {
        "system": {
            "user_constraint_files": ["uc_empty.pampl", "missing.pampl"],
            "user_constraint_file": "unreadable.pampl",
        }
    }
    counts = inf.compute_counts(planning, base_dir=str(tmp_path))
    assert counts.uc_in_pampl == 0
    assert counts.uc_by_family == {"operational": 0}

    monkeypatch.setitem(sys.modules, "gtopt_check_pampl._checks", None)
    counts = inf.compute_counts(planning, base_dir=str(tmp_path))
    assert counts.uc_in_pampl == 0


def test_count_pampl_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "uc_x.pampl").write_text("x", "utf-8")
    original = Path.read_text

    def _read(self: Path, *a: Any, **k: Any) -> str:
        if self.name == "uc_x.pampl":
            raise OSError("denied")
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _read)
    counts = inf.compute_counts(
        {"system": {"user_constraint_files": ["uc_x.pampl"]}}, base_dir=str(tmp_path)
    )
    assert counts.uc_in_pampl == 0


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def test_format_and_print_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    planning = {
        "system": {
            "generator_array": [{"uid": 1, "name": "tg", "type": ""}],
            "turbine_array": [{"generator": "tg"}],
            "demand_array": [{"uid": 1, "lmax": 5.0, "fcost": 100.0}],
            "user_constraint_array": [{"name": "c1", "expression": "x <= 1"}],
        },
        "_skipped_isolated": ["zeta", "alpha"],
    }
    text = inf.format_info(planning, base_dir=str(tmp_path))
    assert "Skipped Centrals (2)" in text
    assert "Avg failure cost" in text
    inf.print_info(planning, base_dir=str(tmp_path))
    err = capsys.readouterr().err
    assert "Skipped Centrals (2)" in err
    counts = inf.compute_counts(planning)
    assert counts.gen_by_type == {"hydro": 1}


def test_info_sections_without_elements() -> None:
    sections = dict(inf._build_info_sections({}))  # noqa: SLF001
    assert sections["Elements"]
    assert all(v == "0" for _k, v in sections["Elements"])


def test_counts_sections_unknown_family() -> None:
    counts = inf.SystemCounts()
    counts.user_constraints_total = 2
    counts.uc_by_family = {"zz_custom": 2}
    sections = dict(inf._build_counts_sections(counts))  # noqa: SLF001
    assert ("  zz_custom", "2") in sections["User Constraints"]
