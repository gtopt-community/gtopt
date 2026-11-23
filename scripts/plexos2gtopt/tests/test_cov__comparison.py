# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for :mod:`plexos2gtopt._comparison`."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gtopt_check_json._info import SystemCounts
from plexos2gtopt import _comparison as cmp
from plexos2gtopt.entities import (
    BoundaryCutSpec,
    BundleSpec,
    FlowSpec,
    FuelSpec,
    GeneratorSpec,
    LineSpec,
    PlexosCase,
    ReserveProvisionSpec,
    UserConstraintSpec,
    UserConstraintStats,
)
from plexos2gtopt.tests.test_comparison import _make_case


def _rich_case() -> PlexosCase:
    base = _make_case()
    gens = base.generators + (
        # Renewable with a profile -> energy integrates the profile.
        GeneratorSpec(
            object_id=4, name="WIND1", bus_name="b1", pmax_profile=(10.0, 20.0, 30.0)
        ),
        # Zero pmax, zero profile -> skipped renewable.
        GeneratorSpec(object_id=5, name="IDLE", bus_name="b1"),
        # Fuel-cost unit with fixed load and a pmin floor.
        GeneratorSpec(
            object_id=6,
            name="GAS_FL",
            bus_name="b1",
            pmax=50.0,
            pmin=5.0,
            heat_rate=8.0,
            fuel_names=("gas",),
            fixed_load_profile=(0.0, 20.0, 25.0),
        ),
        # Always-on floor only.
        GeneratorSpec(object_id=7, name="BASE", bus_name="b1", pmax=9.0, pmin=3.0),
        # Zero-cost fixed load (curtailable): pmin stays 0 for positive blocks.
        GeneratorSpec(
            object_id=8,
            name="ROR_FL",
            bus_name="b1",
            pmax=12.0,
            fixed_load_profile=(4.0, 4.0, 4.0),
        ),
    )
    return dataclasses.replace(
        base,
        generators=gens,
        lines=base.lines
        + (LineSpec(object_id=10, name="L0", bus_from="b1", bus_to="b2"),),
        flows=base.flows + (FlowSpec(name="f_empty", junction_name="j1"),),
        fuels=(
            FuelSpec(object_id=1, name="gas", co2_rate=0.2),
            FuelSpec(object_id=2, name="Carbon_X", subtype="bituminous"),
            FuelSpec(object_id=3, name="zzz_unknown"),
        ),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="COAL1",
                reserve_zones=("z",),
                urmax=10.0,
                urmax_profile=(5.0, 8.0, 7.0),
                drmax=4.0,
                name="p1",
            ),
        ),
        raw_class_counts={"Line": 1, "Battery": 2, "Storage": 2, "Generator": 3},
    )


def test_profile_peak_paths() -> None:
    assert cmp._profile_peak(3.0, None) == 3.0
    assert cmp._profile_peak(0.0, (1.0, 5.0, 2.0)) == 5.0


def test_effective_pmax_uses_profile_peak() -> None:
    gen = SimpleNamespace(pmax=0.0, pmax_profile=(1.0, 7.0))
    assert cmp._effective_pmax(gen) == 7.0
    assert cmp._effective_pmax(SimpleNamespace(pmax=None)) == 0.0


def test_effective_gen_pmin() -> None:
    gen = GeneratorSpec(
        object_id=1,
        name="g",
        bus_name="b",
        pmin=2.0,
        heat_rate=9.0,
        fuel_names=("gas",),
        fixed_load_profile=(1.0, 6.0),
    )
    assert cmp._plexos_effective_gen_pmin(gen) == 6.0
    assert cmp._plexos_effective_gen_pmin(dataclasses.replace(gen, pmin=0.0)) == 6.0
    assert (
        cmp._plexos_effective_gen_pmin(dataclasses.replace(gen, fixed_load_profile=()))
        == 2.0
    )


def test_durations_for() -> None:
    case = _make_case()  # layout (2, 2, 4) intervals
    assert cmp._durations_for(case, 3) == [2.0, 2.0, 4.0]
    assert cmp._durations_for(case, 8) == [1.0] * 8
    assert cmp._durations_for(case, 4) == [2.0] * 4
    assert cmp._durations_for(case, 5) == [1.0] * 5
    flat = dataclasses.replace(case, bundle=BundleSpec())
    assert cmp._durations_for(flat, 2) == [1.0, 1.0]


def test_plexos_indicators_rich_case() -> None:
    ind = cmp._plexos_indicators(_rich_case())
    assert ind["num_fuel_emission_factors"] >= 1
    assert ind["fixed_load_energy_gwh"] > 0.0


def test_plexos_indicators_emission_defaults_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gtopt_shared.emissions as em  # pylint: disable=import-outside-toplevel

    def _boom() -> Any:
        raise FileNotFoundError("no defaults")

    monkeypatch.setattr(em, "load_emission_defaults", _boom)
    ind = cmp._plexos_indicators(_rich_case())
    # Only the explicit co2_rate fuel is counted.
    assert ind["num_fuel_emission_factors"] == 1


def _planning() -> dict[str, Any]:
    return {
        "system": {
            "name": "PCP",
            "version": "1.0",
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [{"uid": 1, "name": "COAL1", "bus": 1, "capacity": 300}],
            "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "fcost": 500}],
            "user_constraint_array": [
                {"uid": 1, "name": "CC1_Uniq", "expression": "x <= 1"},
                {"uid": 2, "name": "Extra_new", "expression": "y <= 1"},
                {"uid": 3, "name": "Gas_MaxOp_B", "expression": "y <= 1"},
            ],
            "user_constraint_files": ["a.pampl"],
        },
        "simulation": {
            "block_array": [
                {"uid": 1, "duration": 2},
                {"uid": 2, "duration": 2},
                {"uid": 3, "duration": 0},
            ],
            "stage_array": [{"uid": 1}],
            "scenario_array": [{"uid": 1}],
        },
        "options": {},
    }


def _fake_checks(monkeypatch: pytest.MonkeyPatch, findings: list[Any]) -> None:
    import gtopt_check_json._checks as chk  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(chk, "run_all_checks", lambda *_a, **_k: findings)


@pytest.mark.parametrize("terminal", [False, True])
def test_run_post_check_with_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, terminal: bool
) -> None:
    from gtopt_check_json import _terminal  # pylint: disable=import-outside-toplevel
    from gtopt_check_json._checks import (  # pylint: disable=import-outside-toplevel
        Severity,
    )

    real_console = _terminal.console

    def _console() -> Any:
        con = real_console()
        monkeypatch.setattr(con, "_force_terminal", terminal, raising=False)
        return con

    monkeypatch.setattr(_terminal, "console", _console)
    findings = [
        SimpleNamespace(severity=sev, message=f"m{sev.name}", action="a")
        for sev in (Severity.CRITICAL, Severity.WARNING, Severity.NOTE)
    ]
    _fake_checks(monkeypatch, findings)
    case = _rich_case()
    stats = UserConstraintStats(
        raw_plexos_constraints=12,
        empty_lhs_dropped=1,
        base_emitted=4,
        hydro_synthesized=1,
        plant_cap_synthesized=1,
    )
    case = dataclasses.replace(
        case,
        boundary_cut=BoundaryCutSpec(fcf=1.0, slopes={"R1": 1.0, "GHOST": 2.0}),
        uc_stats=stats,
        user_constraints=case.user_constraints
        + (UserConstraintSpec(name="Weird_family_x", expression="q <= 1"),),
    )
    assert cmp.run_post_check(_planning(), case, output_dir=tmp_path) == 1


def test_run_post_check_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_checks(monkeypatch, [])
    case = dataclasses.replace(
        _make_case(),
        boundary_cut=None,
        raw_class_counts={"Line": 1, "Generator": 9},
    )
    assert cmp.run_post_check(_planning(), case) == 0


def test_run_post_check_without_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "gtopt_check_json._checks", None)
    assert cmp.run_post_check(_planning(), _make_case()) == 0


def test_drop_funnel_skips_without_rows() -> None:
    counts = SystemCounts()
    case = dataclasses.replace(_make_case(), raw_class_counts={"Other": 3})
    cmp._log_drop_funnel(case, counts)
    cmp._log_drop_funnel(_make_case(), counts)


def test_drop_funnel_equal_and_gained() -> None:
    case = dataclasses.replace(
        _make_case(), raw_class_counts={"Line": 2, "Generator": 1}
    )
    cmp._log_drop_funnel(case, SystemCounts(lines=2, generators=4))


def test_log_comparison_variants() -> None:
    case = _make_case()
    case = dataclasses.replace(
        case,
        demands=case.demands
        + (dataclasses.replace(case.demands[0], name="d2", lmax_profile=(1.0,)),),
    )
    pc = cmp._plexos_element_counts(case)
    pc["boundary_state_variables_discarded"] = ["GHOST"]
    uc = cmp._plexos_uc_classification(case)
    # No "other dropped", emitted > detected, unknown gtopt family.
    uc.update(raw_plexos_constraints=3, empty_lhs_dropped=1, base_emitted=2)
    g = SystemCounts(
        user_constraints_total=50,
        uc_by_family={"zz_unknown_family": 2, "operational": 48},
    )
    plexos_ind = cmp._plexos_indicators(case)
    gtopt_ind = dict.fromkeys(plexos_ind, 0.0)
    plexos_zero = dict.fromkeys(plexos_ind, 0.0)
    gtopt_ind["total_gen_capacity_mw"] = 1.0
    cmp._log_comparison(pc, g, uc, plexos_zero, gtopt_ind)
    cmp._log_comparison(pc, g, uc, None, None)
    # emitted == detected.
    g_eq = SystemCounts(
        user_constraints_total=uc["total"], uc_by_family=dict(uc["by_family"])
    )
    cmp._log_comparison(pc, g_eq, uc, plexos_ind, plexos_ind)


def test_log_conversion_stats_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    from gtopt_check_json import _info  # pylint: disable=import-outside-toplevel

    cmp.log_conversion_stats({"system": {}, "simulation": {}})
    monkeypatch.setattr(_info, "_count_boundary_cuts", lambda *_a, **_k: (1, 2))
    cmp.log_conversion_stats(
        {
            "system": {
                "user_constraint_array": [],
                "boundary_cuts_array": [{"uid": 1}],
                "demand_array": [{"fcost": 100.0}, {"fcost": 200.0}],
            },
            "simulation": {"block_array": [{"uid": 1}]},
        }
    )


def test_compare_plexos_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from plexos2gtopt import parsers, plexos_loader  # pylint: disable=import-outside-toplevel

    class _Ctx:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_a: object) -> None:
            return None

    seen: list[PlexosCase] = []
    monkeypatch.setattr(plexos_loader, "locate_bundle", lambda _p: _Ctx())
    monkeypatch.setattr(parsers, "extract_case", lambda _b: _make_case())

    def _fake_post(_pl: Any, case: PlexosCase, output_dir: Path) -> int:
        assert output_dir == tmp_path
        seen.append(case)
        return 0

    monkeypatch.setattr(cmp, "run_post_check", _fake_post)
    js = tmp_path / "case.json"
    js.write_text(json.dumps(_planning()))
    assert cmp.compare_plexos_bundle({"input_bundle": "x", "compare_json": js}) == 0
    assert seen[0].bundle.block_layout == ((0, 1), (0, 1), (0,))
    # No blocks -> layout untouched.
    js.write_text(json.dumps({"simulation": {}}))
    cmp.compare_plexos_bundle({"input_bundle": "x", "compare_json": str(js)})
    assert seen[1].bundle.block_layout == _make_case().bundle.block_layout


def test_compare_plexos_bundle_requires_inputs() -> None:
    with pytest.raises(ValueError, match="requires"):
        cmp.compare_plexos_bundle({"input_bundle": "x"})
