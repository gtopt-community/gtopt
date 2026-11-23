# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for the individual output checks in ``_checks``."""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gtopt_check_output import _checks as ck
from gtopt_check_output._loss_arbitrage import LineDiagnosis, LossArbitrageReport

_PLANNING: dict = {
    "simulation": {"block_array": [{"uid": 1, "duration": 1.0}]},
    "system": {
        "generator_array": [
            {"uid": 1, "name": "g1", "type": "termica"},
            {"uid": 2, "name": "g2", "type": "hidro"},
        ],
    },
}


def _wide(results: Path, stem: str, data: dict[str, list], phase: int = 0) -> None:
    """Write one hive partition of a wide ``uid:N`` stream."""
    path = results / f"{stem}.parquet" / f"scene=0/phase={phase}/part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [pa.field(k, pa.int32() if k == "block" else pa.float64()) for k in data]
    pq.write_table(pa.table(data, schema=pa.schema(fields)), path)


def _long(results: Path, stem: str, rows: list[tuple], phase: int = 0) -> None:
    """Write one hive partition of a long ``uid``/``value`` stream."""
    path = results / f"{stem}.parquet" / f"scene=0/phase={phase}/part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks, uids, values = (list(c) for c in zip(*rows))
    schema = pa.schema(
        [
            pa.field("scenario", pa.int32()),
            pa.field("stage", pa.int32()),
            pa.field("block", pa.int32()),
            pa.field("uid", pa.int32()),
            pa.field("value", pa.float64()),
        ]
    )
    table = pa.table(
        {
            "scenario": [0] * len(rows),
            "stage": [1] * len(rows),
            "block": blocks,
            "uid": uids,
            "value": values,
        },
        schema=schema,
    )
    pq.write_table(table, path)


def _messages(findings: list[ck.Finding]) -> str:
    return "\n".join(f"{f.severity}:{f.message}" for f in findings)


# ---------------------------------------------------------------------------
# expected files / load shedding / gen vs demand
# ---------------------------------------------------------------------------


def test_expected_files_all_present_with_csv_forms(tmp_path: Path) -> None:
    (tmp_path / "solution.csv").write_text("a\n1\n")
    for stem in (
        "Generator/generation_sol",
        "Bus/balance_dual",
        "Demand/fail_sol",
        "Demand/load_sol",
    ):
        (tmp_path / stem).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"{stem}_s0_p0.csv.gz").write_bytes(b"")
    (tmp_path / "Line").mkdir()
    (tmp_path / "Line" / "flow_sol.csv.zst").write_bytes(b"")
    findings = ck.check_expected_files(tmp_path)
    assert [f.message for f in findings] == ["all expected output files present"]


def test_load_shedding_without_uid_columns(tmp_path: Path) -> None:
    _wide(tmp_path, "Demand/fail_sol", {"block": [1], "other": [1.0]})
    assert not ck.check_load_shedding(tmp_path, _PLANNING)


def test_generation_below_demand(tmp_path: Path) -> None:
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [50.0]})
    _wide(tmp_path, "Demand/load_sol", {"block": [1], "uid:1": [100.0]})
    findings = ck.check_generation_vs_demand(tmp_path, _PLANNING)
    assert findings[0].severity == "WARNING"
    assert "< demand" in findings[0].message


def test_generation_with_zero_demand(tmp_path: Path) -> None:
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [0.0]})
    _wide(tmp_path, "Demand/load_sol", {"block": [1], "uid:1": [0.0]})
    findings = ck.check_generation_vs_demand(tmp_path, _PLANNING)
    assert "ratio: inf" in findings[0].message


# ---------------------------------------------------------------------------
# energy by type
# ---------------------------------------------------------------------------


def test_energy_by_type_empty_and_missing(tmp_path: Path) -> None:
    assert ck.compute_energy_by_type(tmp_path, _PLANNING) == ([], {})
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "other": [1.0]})
    assert ck.compute_energy_by_type(tmp_path, _PLANNING) == ([], {})


def test_energy_by_type_without_tech_classify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wide(
        tmp_path,
        "Generator/generation_sol",
        {"block": [1], "uid:1": [30.0], "uid:2": [10.0], "uid:9": [0.0]},
    )
    monkeypatch.setitem(sys.modules, "plp2gtopt.tech_classify", None)
    findings, by_type = ck.compute_energy_by_type(tmp_path, _PLANNING)
    assert by_type == {"termica": 30.0, "hidro": 10.0, "unknown": 0.0}
    text = _messages(findings)
    assert "--- by category ---" in text
    # Without plp2gtopt the raw type is used as label and category.
    assert "  termica" in text
    assert "(  0.0%)" in text


def test_energy_by_type_zero_total(tmp_path: Path) -> None:
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [0.0]})
    findings, by_type = ck.compute_energy_by_type(tmp_path, _PLANNING)
    assert by_type == {"termica": 0.0}
    assert "(  0.0%)" in _messages(findings)
    assert "by category" not in _messages(findings)


# ---------------------------------------------------------------------------
# congestion
# ---------------------------------------------------------------------------


def test_abs_max_per_col_none() -> None:
    assert not ck._streaming_uid_abs_max_per_col(None)  # noqa: SLF001


def test_abs_max_per_col_long_and_wide(tmp_path: Path) -> None:
    _long(tmp_path, "L/f", [(1, 1, -8.0), (1, None, 3.0)], phase=0)
    _long(tmp_path, "L/f", [(1, 1, 2.0), (1, 2, None)], phase=1)
    out = ck._streaming_uid_abs_max_per_col(ck.open_dataset(tmp_path, "L/f"))  # noqa: SLF001
    assert out == {1: 8.0}
    _wide(tmp_path, "W/f", {"block": [1], "uid:1": [-8.0], "uid:2": [None]}, 0)
    _wide(tmp_path, "W/f", {"block": [1], "uid:1": [2.0], "uid:2": [None]}, 1)
    out = ck._streaming_uid_abs_max_per_col(ck.open_dataset(tmp_path, "W/f"))  # noqa: SLF001
    assert out == {1: 8.0, 2: 0.0}


def test_congestion_legacy_directional_flows(tmp_path: Path) -> None:
    planning = {
        "system": {
            "line_array": [
                {"uid": 1, "name": "L1", "tmax_ab": 100},
                {"uid": 2, "name": "L2", "tmax": 0},
            ]
        }
    }
    _wide(tmp_path, "Line/flowp_sol", {"block": [1], "uid:1": [50.0], "uid:2": [5.0]})
    _wide(tmp_path, "Line/flown_sol", {"block": [1], "uid:1": [95.0]})
    findings = ck.compute_congestion_ranking(tmp_path, planning, top_n=1)
    assert findings[0].severity == "WARNING"
    assert "L1" in findings[1].message
    assert len(findings) == 2


def test_congestion_legacy_only_positive(tmp_path: Path) -> None:
    planning = {"system": {"line_array": [{"uid": 1, "name": "L1", "tmax": 100}]}}
    _wide(tmp_path, "Line/flowp_sol", {"block": [1], "uid:1": [10.0], "uid:3": [1.0]})
    findings = ck.compute_congestion_ranking(tmp_path, planning)
    assert findings[0].severity == "INFO"
    assert "uid:3" in _messages(findings)


def test_congestion_no_flows_or_no_lines(tmp_path: Path) -> None:
    assert not ck.compute_congestion_ranking(tmp_path, {})
    _wide(tmp_path, "Line/flow_sol", {"block": [1], "uid:1": [10.0]})
    assert not ck.compute_congestion_ranking(tmp_path, {})


# ---------------------------------------------------------------------------
# LMP / battery / reservoir
# ---------------------------------------------------------------------------


def test_lmp_missing_and_empty(tmp_path: Path) -> None:
    assert not ck.compute_lmp_statistics(tmp_path, {})
    _wide(tmp_path, "Bus/balance_dual", {"block": [1], "uid:1": [None]})
    assert not ck.compute_lmp_statistics(tmp_path, {})


def test_lmp_negative_prices(tmp_path: Path) -> None:
    _wide(tmp_path, "Bus/balance_dual", {"block": [1, 1], "uid:1": [-5.0, 10.0]})
    text = _messages(ck.compute_lmp_statistics(tmp_path, {}))
    assert "1 negative LMP values" in text
    assert "high LMP spread" not in text


def test_battery_and_reservoir(tmp_path: Path) -> None:
    assert not ck.check_battery_soc(tmp_path, {})
    assert not ck.check_reservoir_levels(tmp_path, {})
    _wide(tmp_path, "Battery/energy_sol", {"block": [1], "uid:1": [5.0]})
    assert "within bounds" in ck.check_battery_soc(tmp_path, {})[0].message
    _wide(tmp_path, "Reservoir/energy_sol", {"block": [1], "uid:1": [-1.0]})
    res = ck.check_reservoir_levels(tmp_path, {})
    assert res[0].severity == "WARNING"
    _wide(tmp_path, "Reservoir/energy_sol", {"block": [1], "uid:1": [1.0]})
    assert "within bounds" in ck.check_reservoir_levels(tmp_path, {})[0].message


def test_battery_and_reservoir_no_uid_columns(tmp_path: Path) -> None:
    _wide(tmp_path, "Battery/energy_sol", {"block": [1], "x": [-5.0]})
    _wide(tmp_path, "Reservoir/energy_sol", {"block": [1], "x": [-5.0]})
    assert ck.check_battery_soc(tmp_path, {})[0].severity == "INFO"
    assert ck.check_reservoir_levels(tmp_path, {})[0].severity == "INFO"


# ---------------------------------------------------------------------------
# cost breakdown
# ---------------------------------------------------------------------------


def test_scalar_from_planning_field() -> None:
    assert ck._scalar_from_planning_field(3) == 3.0  # noqa: SLF001
    assert ck._scalar_from_planning_field("f.parquet", 7.0) == 7.0  # noqa: SLF001


def test_cost_breakdown_components(tmp_path: Path) -> None:
    planning = {
        "simulation": {"block_array": [{"uid": 1, "duration": 2.0}]},
        "options": {"model_options": {"demand_fail_cost": 100.0}},
        "system": {
            "line_array": [
                {"uid": 1, "tcost": 1.0},
                {"uid": 2, "tcost": "tcost.parquet"},
            ]
        },
    }
    _wide(tmp_path, "Generator/srmc_sol", {"block": [1], "uid:1": [10.0]})
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [5.0]})
    _wide(tmp_path, "Demand/fail_sol", {"block": [1], "uid:1": [1.0]})
    _wide(tmp_path, "Line/flowp_sol", {"block": [1], "uid:1": [3.0], "uid:2": [9.0]})
    _wide(tmp_path, "Line/flown_sol", {"block": [1], "uid:1": [0.0]})
    text = _messages(ck.compute_cost_breakdown(tmp_path, planning))
    assert "total system cost: 306.00" in text
    assert "generation" in text
    assert "load shedding penalty" in text
    assert "transmission (A→B)" in text
    assert "transmission (B→A)" not in text


def test_cost_breakdown_zero_contributions(tmp_path: Path) -> None:
    planning = {"options": {"model_options": {"demand_fail_cost": 0.0}}}
    _wide(tmp_path, "Generator/srmc_sol", {"block": [1], "uid:1": [0.0]})
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [5.0]})
    _wide(tmp_path, "Demand/fail_sol", {"block": [1], "uid:1": [1.0]})
    _wide(tmp_path, "Line/flow_sol", {"block": [1], "uid:1": [3.0]})
    assert not ck.compute_cost_breakdown(tmp_path, planning)


def test_cost_breakdown_tiny_fail_and_flown_only(tmp_path: Path) -> None:
    planning = {
        "options": {"model_options": {"demand_fail_cost": 1.0}},
        "system": {"line_array": [{"uid": 1, "tcost": 2.0}]},
    }
    _wide(tmp_path, "Demand/fail_sol", {"block": [1], "uid:1": [0.001]})
    _wide(tmp_path, "Line/flown_sol", {"block": [1], "uid:1": [4.0]})
    text = _messages(ck.compute_cost_breakdown(tmp_path, planning))
    assert "transmission (B→A)" in text
    assert "load shedding" not in text


# ---------------------------------------------------------------------------
# transmission losses
# ---------------------------------------------------------------------------


_LOSS_PLANNING: dict = {
    "simulation": {"block_array": [{"uid": 1, "duration": 2.0}]},
    "system": {
        "line_array": [
            # arbitrage candidate: |flow| = 50 > 3 * tmax
            {"uid": 1, "name": "hot", "tmax": 10, "resistance": 1.0, "voltage": 10.0},
            # throughput < 1 MWh -> skipped
            {"uid": 2, "name": "tiny", "tmax": 10, "resistance": 1.0, "voltage": 10.0},
            # no tmax -> zero baseline -> never a candidate
            {"uid": 3, "name": "nobase", "tmax": 0, "resistance": 1.0, "voltage": 10.0},
            # no flow -> zero throughput
            {"uid": 4, "name": "idle", "tmax": 10, "resistance": 1.0, "voltage": 10.0},
            # no loss model (R = 0)
            {"uid": 5, "name": "lossless", "tmax": 10, "voltage": 10.0},
        ]
    },
}


def test_transmission_losses_missing_inputs(tmp_path: Path) -> None:
    assert ck.check_transmission_losses(tmp_path, {}) == ([], {})


def test_transmission_losses_full(tmp_path: Path) -> None:
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [200.0]})
    _wide(tmp_path, "Demand/load_sol", {"block": [1], "uid:1": [100.0]})
    _wide(tmp_path, "Battery/finp_sol", {"block": [1], "uid:1": [3.0]})
    _wide(tmp_path, "Battery/fout_sol", {"block": [1], "uid:1": [2.0]})
    _wide(
        tmp_path,
        "Line/flow_sol",
        {
            "block": [1],
            "uid:1": [-50.0],
            "uid:2": [0.1],
            "uid:3": [20.0],
            "uid:4": [0.0],
            "uid:5": [7.0],
        },
    )
    findings, ind = ck.check_transmission_losses(tmp_path, _LOSS_PLANNING)
    text = _messages(findings)
    losses = ind["losses"]
    assert losses["balance_method_MWh"] == pytest.approx(200.0)
    assert losses["battery_roundtrip_MWh"] == pytest.approx(2.0)
    # coef = 1/100: 25*2 + 0.0001*2 + 4*2 + 0
    assert losses["analytical_method_MWh"] == pytest.approx(58.0002)
    assert "large residue after battery roundtrip" in text
    assert "potential loss-arbitrage candidates: 1" in text
    assert "hot" in text
    assert "top 10 lines by analytical loss energy" in text


def test_transmission_losses_no_loss_model(tmp_path: Path) -> None:
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [100.0]})
    _wide(tmp_path, "Demand/load_sol", {"block": [1], "uid:1": [100.0]})
    _wide(tmp_path, "Line/flow_sol", {"block": [1], "uid:1": [10.0]})
    planning = {"system": {"line_array": [{"uid": 1, "name": "L", "tmax": 5}]}}
    findings, ind = ck.check_transmission_losses(tmp_path, planning)
    assert ind["losses"]["analytical_method_MWh"] is None
    assert len(findings) == 1


def test_transmission_losses_small_residue_no_candidates(tmp_path: Path) -> None:
    _wide(tmp_path, "Generator/generation_sol", {"block": [1], "uid:1": [100.04]})
    _wide(tmp_path, "Demand/load_sol", {"block": [1], "uid:1": [100.0]})
    _wide(tmp_path, "Line/flow_sol", {"block": [1], "uid:7": [2.0]})
    planning = {
        "system": {
            "line_array": [
                {"uid": 7, "tmax": 10, "resistance": 1.0, "voltage": 10.0},
            ]
        }
    }
    findings, _ = ck.check_transmission_losses(tmp_path, planning)
    text = _messages(findings)
    assert "WARNING" not in text
    assert "uid:7" in text


# ---------------------------------------------------------------------------
# curtailment / loss arbitrage wrapper
# ---------------------------------------------------------------------------


def test_curtailment_no_profiles(tmp_path: Path) -> None:
    _wide(tmp_path, "GeneratorProfile/spillover_sol", {"block": [1], "uid:1": [1.0]})
    assert ck.check_renewable_curtailment(tmp_path, {}) == ([], {})


def test_curtailment_without_generation_stream(tmp_path: Path) -> None:
    _wide(tmp_path, "GeneratorProfile/spillover_sol", {"block": [1], "uid:1": [0.0]})
    planning = {
        "system": {"generator_profile_array": [{"uid": 1, "name": "p", "generator": 5}]}
    }
    findings, data = ck.check_renewable_curtailment(tmp_path, planning)
    assert data == {"p": 0.0}
    assert "no renewable curtailment" in findings[0].message
    assert "uid:5" in findings[1].message


def test_loss_arbitrage_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = LossArbitrageReport(
        diagnoses=[
            LineDiagnosis(
                uid=1,
                name="L1",
                worst_symptom="phantom-circulation",
                arbitrage_mwh=3.0,
                worst_pair_sum=-40.0,
                n_flagged=2,
            )
        ],
        total_fictitious_mwh=3.0,
        notes=["the fictitious-loss sink is active", "plain note"],
    )
    monkeypatch.setattr(ck, "detect_loss_arbitrage", lambda *_a, **_k: report)
    findings, la = ck.check_loss_arbitrage(tmp_path, {}, tol=0.5, circulation_factor=2)
    assert la is report
    assert findings[0].severity == "WARNING"
    assert "tol=0.5, k=2" in findings[0].message
    assert any("L1" in f.message for f in findings)
    assert findings[-2].severity == "WARNING"
    assert findings[-1].severity == "INFO"


def test_cost_breakdown_unified_flow_tcost(tmp_path: Path) -> None:
    planning = {"system": {"line_array": [{"uid": 1, "tcost": 2.0}]}}
    _wide(tmp_path, "Line/flow_sol", {"block": [1], "uid:1": [-4.0]})
    text = _messages(ck.compute_cost_breakdown(tmp_path, planning))
    assert "total system cost: 8.00" in text
    assert "transmission" in text


def test_curtailment_empty_spillover_map(tmp_path: Path) -> None:
    _wide(tmp_path, "GeneratorProfile/spillover_sol", {"block": [1], "x": [1.0]})
    planning = {
        "system": {
            "generator_profile_array": [
                {"uid": 1, "name": "p1", "generator": 5},
                {"uid": 2, "name": "p2", "generator": 6},
            ]
        }
    }
    findings, data = ck.check_renewable_curtailment(tmp_path, planning)
    assert data == {"p1": 0.0, "p2": 0.0}
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# stepwise demand-failure curves (fail_segments)
# ---------------------------------------------------------------------------


def test_cost_breakdown_fail_segments(tmp_path: Path) -> None:
    """Per-segment pricing: own curve, own fcost, global curve, unlisted uid."""
    planning = {
        "simulation": {"block_array": [{"uid": 1, "duration": 1.0}]},
        "options": {
            "model_options": {
                "demand_fail_cost": 100.0,
                "demand_fail_segments": [
                    {"fraction": 0.5, "cost": 10.0},
                    {"cost": 20.0},
                ],
            }
        },
        "system": {
            "demand_array": [
                {
                    "uid": 1,
                    "fail_segments": [
                        {"fraction": 0.1, "cost": 300.0},
                        {"cost": 500.0},
                    ],
                },
                {"uid": 2, "fcost": 1000.0},
                {"uid": 3},
                {"uid": 5, "forced": True},
            ]
        },
    }
    # uid 1: 1 MW in seg1 + 2 MW in seg2; uid 2: 1 MW; uid 3 (global
    # curve): 4 MW in seg1; uid 4 (not listed): 1 MW at the global cost.
    _wide(
        tmp_path,
        "Demand/fail_sol",
        {"block": [1], "uid:1": [3.0], "uid:2": [1.0], "uid:3": [4.0], "uid:4": [1.0]},
    )
    _wide(
        tmp_path, "Demand/fail_seg1_sol", {"block": [1], "uid:1": [1.0], "uid:3": [4.0]}
    )
    _wide(
        tmp_path, "Demand/fail_seg2_sol", {"block": [1], "uid:1": [2.0], "uid:3": [0.0]}
    )
    text = _messages(ck.compute_cost_breakdown(tmp_path, planning))
    expected = 300.0 + 2 * 500.0 + 1000.0 + 4 * 10.0 + 100.0
    assert f"total system cost: {expected:.2f}" in text


def test_demand_fail_cost_coefs_non_scalar_segment() -> None:
    single, segs = ck.demand_fail_cost_coefs(
        {"system": {"demand_array": [{"uid": 1, "fail_segments": [{"cost": "f"}]}]}}
    )
    assert not single
    assert not segs


def test_demand_fail_cost_coefs_segments_not_a_list() -> None:
    """A non-list ``fail_segments`` (e.g. a file reference) is not priced.

    The global curve applies only when the demand has no own curve, so a
    file-referenced own curve contributes nothing instead of silently
    falling back to the global segments.
    """
    single, segs = ck.demand_fail_cost_coefs(
        {
            "options": {
                "model_options": {
                    "demand_fail_segments": [
                        {"fraction": 0.5, "cost": 100.0},
                        {"fraction": 1.0, "cost": 500.0},
                    ]
                }
            },
            "system": {
                "demand_array": [
                    {"uid": 1, "fail_segments": "fail_segments.parquet"},
                    {"uid": 2},
                ]
            },
        }
    )
    assert not single
    assert segs == [{2: 100.0}, {2: 500.0}]
