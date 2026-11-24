# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--reserve-load-factor auto`` (:mod:`plexos2gtopt._reserve_load`)."""

from __future__ import annotations

import argparse
import logging
from datetime import date

import pytest

from plexos2gtopt._native_reserve import (
    DEFAULT_RESERVE_LOAD_FACTOR,
    NativeReserveOptions,
)
from plexos2gtopt._reserve_load import (
    MONTHLY_LOAD_FACTOR,
    estimate_load_factor,
    monthly_load_factor,
    parse_case_date,
    reserve_load_factor_arg,
    resolve_reserve_load_factor,
)
from plexos2gtopt.main import make_parser
from plexos2gtopt.plexos_solution import PlexosSolution, csv_table_source
from plexos2gtopt.tests import horizon_fixture as fx

_LOAD = [[100.0] * 168]


def _solution(tables: dict[str, str] | None = None) -> PlexosSolution:
    return PlexosSolution(csv_table_source(tables or fx.solution_tables()))


def test_arg_type() -> None:
    assert reserve_load_factor_arg("AUTO") == "auto"
    assert reserve_load_factor_arg("1.03") == pytest.approx(1.03)
    for bad in ("x", "0", "-1"):
        with pytest.raises(argparse.ArgumentTypeError):
            reserve_load_factor_arg(bad)


def test_cli_default_stays_fixed() -> None:
    # The CLI leaves the factor unset (so an explicit factor can be warned
    # about under --reserve-system-load / the endogenous load term); the
    # native reserve options then fall back to the fixed default.
    raw = make_parser().parse_args(["x"]).reserve_load_factor
    assert raw is None
    opts = NativeReserveOptions.from_options({"reserve_load_factor": raw})
    assert opts.load_factor == pytest.approx(DEFAULT_RESERVE_LOAD_FACTOR)
    args = make_parser().parse_args(["x", "--reserve-load-factor", "auto"])
    assert args.reserve_load_factor == "auto"


def test_monthly_table_lookup() -> None:
    assert monthly_load_factor(date(2026, 11, 7)) == pytest.approx(1.0463)
    # Latest year at or before the case year; earliest when older.
    assert monthly_load_factor(date(2027, 12, 1)) == MONTHLY_LOAD_FACTOR[(2025, 12)]
    assert monthly_load_factor(date(2020, 3, 1)) == MONTHLY_LOAD_FACTOR[(2024, 3)]
    assert all(1.02 < v < 1.05 for v in MONTHLY_LOAD_FACTOR.values())


def test_parse_case_date() -> None:
    assert parse_case_date("2026-11-07") == date(2026, 11, 7)
    assert parse_case_date("20241215") == date(2024, 12, 15)
    assert parse_case_date("") is None
    assert parse_case_date("garbage") is None


def test_estimate_from_generation_sen() -> None:
    est = estimate_load_factor(_LOAD, hours=24, case_date=None, solution=_solution())
    assert est.factor == pytest.approx(1.05)
    assert est.source == "plexos:Generation_SEN"
    assert est.generation_mwh == pytest.approx(2520.0)
    assert est.demand_mwh == pytest.approx(2400.0)


def test_estimate_generation_sen_in_mw() -> None:
    tables = fx.solution_tables(n_days=1)
    tables["t_data_0"] = tables["t_data_0"].replace(",0.105\n", ",2100\n")
    est = estimate_load_factor(
        [[2000.0] * 24], hours=24, case_date=None, solution=_solution(tables)
    )
    assert est.factor == pytest.approx(1.05)


def test_estimate_from_summed_generators() -> None:
    tables = fx.solution_tables(n_days=1)
    tables["t_class"] += "2,Generator\n"
    tables["t_collection"] += "1,1,2,Generators\n"
    tables["t_property"] += "40,1,Generation\n"
    tables["t_object"] = tables["t_object"].replace(
        "3,72,Generation_SEN", "3,72,Other_DV"
    )
    tables["t_object"] += "4,2,G1\n5,2,G2\n"
    tables["t_membership"] += "400,1,0,4\n500,1,0,5\n"
    tables["t_key"] += "40,400,3,40,1,0\n50,500,3,40,1,0\n"
    tables["t_data_0"] += "".join(f"40,{h},60\n50,{h},43\n" for h in range(1, 25))
    est = estimate_load_factor(
        [[100.0] * 24], hours=24, case_date=None, solution=_solution(tables)
    )
    assert est.source == "plexos:generators"
    assert est.factor == pytest.approx(1.03)


def test_estimate_falls_back_to_table(caplog: pytest.LogCaptureFixture) -> None:
    # Implausible ratio (load 10x too small) -> monthly table.
    with caplog.at_level(logging.WARNING):
        est = estimate_load_factor(
            [[10.0] * 24], hours=24, case_date=date(2025, 4, 2), solution=_solution()
        )
    assert est.source == "monthly-table:2025-04"
    assert est.factor == pytest.approx(MONTHLY_LOAD_FACTOR[(2025, 4)])
    assert "out of" in caplog.text
    # No solution, no date: today's month, warned.
    est = estimate_load_factor(_LOAD, hours=24, case_date=None, solution=None)
    assert est.source.startswith("monthly-table:")
    assert est.demand_mwh == pytest.approx(2400.0)


def test_resolve_only_for_auto() -> None:
    assert (
        resolve_reserve_load_factor(
            {"reserve_load_factor": 1.05},
            _LOAD,
            hours=24,
            case_date=None,
            solution=None,
        )
        is None
    )
    est = resolve_reserve_load_factor(
        {"reserve_load_factor": "auto"},
        _LOAD,
        hours=24,
        case_date=date(2026, 10, 1),
        solution=None,
    )
    assert est is not None
    assert est.as_dict()["factor"] == pytest.approx(1.0429)
