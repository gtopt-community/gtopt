# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--reserve-model native``: CEN CPF regressions -> gtopt ReserveZone planes.

A synthetic case reproduces the PCP structure (minuta GM-DPRO Nº05-2026):
``Generation_SEN`` / ``Inertia_SEN`` definition rows, two
``CPF_Up10Calculation_*`` planes, ``CPF_DownCalculation``, the
``*MinProvision`` rows (VoRS + slack), the ``MaxInjection_SEN`` /
``MaxLoad_SEN`` comparison rows and a pure provision-sum CSF row.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plexos2gtopt._native_reserve import (
    DatedIndex,
    NativeReserveOptions,
    _blocks_to_hours,
    _inertia_ref_power,
    apply_native_reserve,
    hourly_values,
    parse_expression,
    read_system_load_csv,
    resolve_dated,
)
from plexos2gtopt.entities import (
    BundleSpec,
    CommitmentSpec,
    DecisionVariableSpec,
    GeneratorSpec,
    InertiaProvisionSpec,
    InertiaZoneSpec,
    PlexosCase,
    ReserveContingencySpec,
    ReservePlaneSpec,
    ReserveProvisionSpec,
    ReserveSpec,
    UserConstraintSpec,
)
from plexos2gtopt.gtopt_writer import (
    build_inertia_provision_array,
    build_inertia_zone_array,
    build_reserve_provision_array,
    build_reserve_zone_array,
)
from plexos2gtopt.main import _positive_float, make_parser
from plexos2gtopt.plexos2gtopt import _load_reserve_db
from plexos2gtopt.plexos_xml import (
    PlexosCollection,
    PlexosDataRow,
    PlexosDb,
    PlexosMembership,
    PlexosObject,
    PlexosProperty,
)

_DV = 'decision_variable("{}").value'
_EXO = NativeReserveOptions()
_ENDO = NativeReserveOptions(load_term="endogenous")
_ENDO_I = NativeReserveOptions(inertia_term="endogenous")
_EXO_I = NativeReserveOptions(inertia_term="exogenous")


def _uc(name: str, expr: str, **kw: Any) -> UserConstraintSpec:
    return replace(UserConstraintSpec(name=name, expression=expr), **kw)


def _dv(name: str) -> str:
    return _DV.format(name)


def _base_ucs() -> list[UserConstraintSpec]:
    return [
        _uc(
            "Generation_Calculation",
            '1 * generator("G1").generation + 1 * battery("B1").discharge'
            ' - 1 * battery("B1").charge - 1000 * ' + _dv("Generation_SEN") + " >= 0",
        ),
        _uc(
            "Inertia_Calculation_e1",
            '5 * commitment("uc_G1").status + 3 * commitment("uc_G2").status'
            ' + 2 * commitment("uc_NOPE").status - 1000 * '
            + _dv("Inertia_SEN")
            + " >= -800",
        ),
        _uc("InertiaGlobalMin_New", "1 * " + _dv("Inertia_SEN") + " >= 6"),
        _uc(
            "CPF_Up10Calculation_I",
            f"1 * {_dv('CPF10s_Requirement')} + 36.6 * {_dv('Generation_SEN')}"
            f" + 12.3 * {_dv('Inertia_SEN')} - 1.25 * {_dv('MaxInjection_SEN')}"
            " >= 516",
        ),
        _uc(
            "CPF_Up10Calculation_II",
            f"1 * {_dv('CPF10s_Requirement')} + 23.5 * {_dv('Generation_SEN')} >= 300",
        ),
        _uc(
            "CPF_Up10MinProvision",
            '0.9 * reserve_provision("provision_G1").up'
            ' + 1 * reserve_provision("provision_B1").up'
            f" + 1 * {_dv('CPF10s_VoRS')} - 1 * {_dv('CPF10s_Requirement')}"
            f" - 1 * {_dv('CPF10s_Slack')} >= 0",
        ),
        _uc(
            "CPF_DownCalculation",
            f"1 * {_dv('CPF5mDown_Requirement')} - 0.96 * {_dv('MaxLoad_SEN')}"
            f" + 4 * {_dv('Generation_SEN')} >= -80",
        ),
        _uc(
            "CPF_DownMinProvision",
            '0.5 * reserve_provision("provision_G1").dn'
            f" + 1 * {_dv('CPF5mDown_VoRS')} - 1 * {_dv('CPF5mDown_Requirement')}"
            " >= 0",
        ),
        _uc(
            "G1_Comparison",
            f'1 * {_dv("MaxInjection_SEN")} - 1 * generator("G1").generation >= 0',
        ),
        _uc(
            "B1_Comparison",
            f'1 * {_dv("MaxInjection_SEN")} - 1 * battery("B1").discharge >= 0',
        ),
        _uc(
            "G2_Comparison",
            f'1 * {_dv("MaxInjection_SEN")} - 1 * generator("G2").generation'
            f" - 1 * {_dv('LGG')} - 2 * {_dv('Orphan')}"
            ' + 1 * line("L1").flow - 1 * commitment("uc_G2").status >= 5',
        ),
        _uc(
            "limited_generation_calculation",
            '-1 * generator("PV1").generation + 1 * battery("B2").charge'
            f' - 1 * commitment("uc_G1").status + 1 * {_dv("LGG")} >= 2',
        ),
        _uc(
            "BAT_B1_Comparison",
            f'1 * {_dv("MaxLoad_SEN")} - 1 * battery("B1").charge >= 0',
        ),
        _uc(
            "CSF_UpMinProvision",
            '1 * reserve_provision("provision_G1").up'
            ' + 1 * reserve_provision("provision_G2").up >= 50',
            rhs_profile=(50.0,) * 12 + (70.0,) * 12,
        ),
        _uc("Unrelated", '1 * generator("G2").generation <= 99'),
    ]


def _dvs() -> tuple[DecisionVariableSpec, ...]:
    return (
        DecisionVariableSpec(name="Generation_SEN"),
        DecisionVariableSpec(name="Inertia_SEN"),
        DecisionVariableSpec(name="CPF10s_Requirement", cost=10.0),
        DecisionVariableSpec(name="CPF10s_VoRS", cost=10000.0),
        DecisionVariableSpec(name="CPF10s_Slack", cost=1.0),
        DecisionVariableSpec(name="CPF5mDown_Requirement"),
        DecisionVariableSpec(name="CPF5mDown_VoRS", cost=5000.0),
        DecisionVariableSpec(
            name="MaxInjection_SEN", lower_bound=300.0, upper_bound=400.0
        ),
        DecisionVariableSpec(name="MaxLoad_SEN", lower_bound=200.0),
        DecisionVariableSpec(name="LGG"),
    )


def _case(ucs: list[UserConstraintSpec] | None = None, **kw: Any) -> PlexosCase:
    case = PlexosCase(
        bundle=BundleSpec(bundle_date="2026-11-07"),
        generators=(
            GeneratorSpec(object_id=1, name="G1", bus_name="b", pmin=50.0),
            GeneratorSpec(object_id=2, name="G2", bus_name="b", pmin=0.4),
        ),
        commitments=(
            CommitmentSpec(generator_name="G1"),
            CommitmentSpec(generator_name="G2"),
        ),
        reserves=(ReserveSpec(object_id=1, name="CPF_RS"),),
        reserve_provisions=(
            ReserveProvisionSpec(
                generator_name="G1",
                reserve_zones=("CPF_RS",),
                name="provision_G1",
            ),
            ReserveProvisionSpec(
                generator_name="G2", reserve_zones=("CPF_RS",), name="provision_G2"
            ),
            ReserveProvisionSpec(
                generator_name="B1_gen",
                reserve_zones=("CPF_RS",),
                name="provision_B1",
                ur_provision_factor_profile=(0.5,) * 24,
            ),
            ReserveProvisionSpec(generator_name="G3", reserve_zones=("CPF_RS",)),
        ),
        decision_variables=_dvs(),
        user_constraints=tuple(_base_ucs() if ucs is None else ucs),
    )
    return replace(case, **kw)


def _zone(case: PlexosCase, name: str) -> ReserveSpec:
    return next(z for z in case.reserves if z.name == name)


def _ucmap(case: PlexosCase) -> dict[str, UserConstraintSpec]:
    return {u.name: u for u in case.user_constraints}


# ── options ────────────────────────────────────────────────────────────────
class TestOptions:
    def test_defaults(self) -> None:
        opts = NativeReserveOptions.from_options({})
        assert opts.model == "native"
        assert not opts.endogenous
        assert opts.load_factor == pytest.approx(1.05)
        assert not opts.system_load

    def test_explicit(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "load.csv"
        csv_path.write_text("hour,system_load\n2,9000\n1,8000\n", encoding="utf-8")
        opts = NativeReserveOptions.from_options(
            {
                "reserve_model": "uc",
                "reserve_load_term": "exogenous",
                "reserve_system_load": str(csv_path),
            }
        )
        assert opts.model == "uc"
        assert not opts.endogenous
        assert opts.load_factor == pytest.approx(1.05)
        assert opts.system_load == (8000.0, 9000.0)
        opts = NativeReserveOptions.from_options(
            {"reserve_load_term": "endogenous", "reserve_load_factor": 1.04}
        )
        assert opts.endogenous
        assert opts.load_factor == pytest.approx(1.04)

    @pytest.mark.parametrize(
        "bad",
        [
            {"reserve_model": "x"},
            {"reserve_load_term": "x"},
            {"reserve_load_factor": 0.0},
        ],
    )
    def test_invalid(self, bad: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            NativeReserveOptions.from_options(bad)

    def test_csv_variants(self, tmp_path: Path) -> None:
        gen = tmp_path / "gen.csv"
        gen.write_text("generation_sen\n8.5\n9.0\n", encoding="utf-8")
        assert read_system_load_csv(gen) == (8500.0, 9000.0)
        val = tmp_path / "val.csv"
        val.write_text("Value\n1\n", encoding="utf-8")
        assert read_system_load_csv(val) == (1.0,)
        last = tmp_path / "last.csv"
        last.write_text("a,b\n1,7\n", encoding="utf-8")
        assert read_system_load_csv(last) == (7.0,)
        empty = tmp_path / "empty.csv"
        empty.write_text("a\n", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            read_system_load_csv(empty)

    def test_system_load_csv_rejects_blank_nan_and_non_positive(
        self, tmp_path: Path
    ) -> None:
        for body, bad in (
            ("hour,system_load\n1,8000\n2,\n", "''"),
            ("system_load\n8000\nnan\n", "'nan'"),
            ("system_load\n8000\n0\n", "'0'"),
            ("system_load\n-1\n", "'-1'"),
        ):
            path = tmp_path / "load.csv"
            path.write_text(body, encoding="utf-8")
            with pytest.raises(ValueError, match=f"load.csv: data row .*{bad}"):
                read_system_load_csv(path)

    def test_endogenous_ignores_system_load_and_factor(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "nope.csv"  # never read in endogenous mode
        opts = NativeReserveOptions.from_options(
            {
                "reserve_load_term": "endogenous",
                "reserve_system_load": str(missing),
                "reserve_load_factor": 1.1,
            }
        )
        assert not opts.system_load
        assert "--reserve-system-load ignored" in caplog.text
        assert "--reserve-load-factor ignored (endogenous" in caplog.text

    def test_system_load_length_must_match_horizon(self, tmp_path: Path) -> None:
        opts = NativeReserveOptions(system_load=(9000.0,) * 23)
        with pytest.raises(ValueError, match="23 values, the horizon has 24"):
            apply_native_reserve(_case(), opts)
        # Endogenous runs never use it.
        endo = NativeReserveOptions(load_term="endogenous", system_load=(1.0,))
        assert apply_native_reserve(_case(), endo).reserves


# ── parsing / dated helpers ───────────────────────────────────────────────
class TestParsing:
    def test_parse_expression(self) -> None:
        parsed = parse_expression(
            f'2.5 * generator("G").generation - {_dv("X")} + 1e3 * {_dv("Y")} >= - 4 ;'
        )
        assert parsed is not None
        terms, op, rhs = parsed
        assert [(t.coef, t.kind, t.ref) for t in terms] == [
            (2.5, "generator", "G"),
            (-1.0, "decision_variable", "X"),
            (1000.0, "decision_variable", "Y"),
        ]
        assert (op, rhs) == (">=", -4.0)

    @pytest.mark.parametrize(
        "expr",
        [
            "sum(g in G: generator(g).generation) >= 1",
            '1 * generator("G").generation 2 * generator("H").generation >= 1',
            '1 * generator("G").generation + foo >= 1',
            ">= 3",
            '1 * generator("G").generation',
        ],
    )
    def test_parse_rejects(self, expr: str) -> None:
        assert parse_expression(expr) is None

    def test_resolve_dated(self) -> None:
        when = datetime(2026, 11, 7, 5)
        undated = [
            PlexosDataRow(data_id=2, membership_id=1, property_id=1, value=1.0),
            PlexosDataRow(data_id=1, membership_id=1, property_id=1, value=0.5),
        ]
        assert resolve_dated(undated, when) == 0.5
        old = PlexosDataRow(
            data_id=3,
            membership_id=1,
            property_id=1,
            value=2.0,
            date_from=datetime(2026, 1, 1),
        )
        new = PlexosDataRow(
            data_id=4,
            membership_id=1,
            property_id=1,
            value=3.0,
            date_from=datetime(2026, 11, 7),
            date_to=datetime(2026, 11, 7),
        )
        expired = PlexosDataRow(
            data_id=5,
            membership_id=1,
            property_id=1,
            value=9.0,
            date_to=datetime(2025, 1, 1),
        )
        rows = [*undated, old, new, expired]
        assert resolve_dated(rows, when) == 3.0
        assert resolve_dated(rows, datetime(2026, 11, 8)) == 2.0
        assert resolve_dated(rows, datetime(2025, 6, 1)) == 0.5
        assert resolve_dated([expired], datetime(2026, 1, 1)) is None

    def test_hourly_values(self) -> None:
        row = PlexosDataRow(
            data_id=1,
            membership_id=1,
            property_id=1,
            value=7.0,
            date_from=datetime(2026, 11, 8),
        )
        assert hourly_values([], datetime(2026, 11, 7), 2, 1.0) == (1.0, 1.0)
        assert hourly_values([row], None, 2, 1.0) == (1.0, 1.0)
        vals = hourly_values([row], datetime(2026, 11, 7, 23), 2, 1.0)
        assert vals == (1.0, 7.0)

    def test_blocks_to_hours(self) -> None:
        # A length that is neither hours, blocks nor a day pattern is an error.
        with pytest.raises(
            ValueError, match=r"ROW: RHS profile has 2 values, expected 4"
        ):
            _blocks_to_hours((1.0, 2.0), (), 4, where="ROW")
        with pytest.raises(ValueError, match="or 2 blocks"):
            _blocks_to_hours((1.0,) * 3, ((1, 2), (3, 4)), 4)
        # One CEN day repeats over a two-day horizon.
        day = tuple(float(h) for h in range(24))
        assert _blocks_to_hours(day, (), 48) == day + day
        # A CEN week on a one-day run: the horizon's first day.
        assert _blocks_to_hours(day * 7, (), 24) == day
        # Three days cannot fill four.
        with pytest.raises(ValueError, match="72 values, expected 96"):
            _blocks_to_hours(day * 3, (), 96)
        assert _blocks_to_hours((1.0,) * 4, ((1, 2), (3, 4)), 4) == (1.0,) * 4
        layout = ((1, 2), (3, 9))
        assert _blocks_to_hours((1.0, 2.0), layout, 4) == (1.0, 1.0, 2.0, 0.0)

    def test_inertia_ref_power(self) -> None:
        assert _inertia_ref_power(0.0) == 1.0
        assert _inertia_ref_power(0.4) == pytest.approx(0.4)
        assert _inertia_ref_power(50.0) == 50.0


# ── the transform ──────────────────────────────────────────────────────────
class TestApply:
    def test_uc_model_is_noop(self) -> None:
        case = _case()
        assert apply_native_reserve(case, NativeReserveOptions(model="uc")) is case

    def test_no_reserve_rows_is_noop(self) -> None:
        case = _case([_uc("Unrelated", '1 * generator("G2").generation <= 99')])
        assert apply_native_reserve(case, _EXO) is case

    def test_exogenous_zones(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            out = apply_native_reserve(_case(), _EXO)
        up = _zone(out, "CPF10s")
        assert up.load_term == "exogenous"
        assert up.demand_load_factor == pytest.approx(1.05)
        # Default inertia term: commitment, I from the Inertia_SEN zone.
        assert up.inertia_term == "commitment"
        assert up.inertia_zone == "Inertia_SEN"
        assert up.system_inertia == ()
        assert up.urcost == pytest.approx(10000.0)
        assert up.drreq_planes == ()
        assert up.omit_zero_requirement
        g1c, b1c, g2c = up.urcontingencies
        assert (g1c.name, g1c.generators, g1c.offset) == ("G1_Comparison", ("G1",), 0)
        assert b1c.generators == ("B1_gen",)
        # C ≥ p_G2 + LGG − flow_L1 + 5 with LGG ≥ p_PV1 − charge_B2 + 2.
        assert g2c.generators == ("G2", "PV1")
        assert g2c.generator_coefs == (1.0, 1.0)
        assert g2c.demands == ("B2_dem",)
        assert g2c.demand_coefs == (-1.0,)
        assert g2c.lines == ("L1",)
        assert g2c.line_coefs == (-1.0,)
        assert g2c.offset == pytest.approx(7.0)
        p1, p1_floor, p2 = up.urreq_planes
        # R ≥ 516 − 36.6 G − 12.3 I + 1.25 C with G = D/1000 and
        # I = (ΣHSu + 800)/1000.
        assert p1.alpha == pytest.approx(516.0 - 12.3 * 0.8)
        assert p1.demand_coef == pytest.approx(-0.0366)
        assert p1.inertia_coef == pytest.approx(-0.0123)
        assert p1.contingency_coef == pytest.approx(1.25)
        assert p1_floor.alpha == pytest.approx(516.0 - 12.3 * 0.8 + 1.25 * 300)
        assert p1_floor.contingency_coef is None
        assert p1_floor.source.startswith("CPF_Up10Calculation_I ")
        assert p2.alpha == pytest.approx(300.0)
        assert p2.inertia_coef is None
        assert p2.contingency_coef is None

        dn = _zone(out, "CPF5mDown")
        assert dn.urreq_planes == ()
        assert dn.drcost == pytest.approx(5000.0)
        assert dn.urcost == 0.0
        assert dn.inertia_zone == ""
        (b1d,) = dn.drcontingencies
        assert b1d.demands == ("B1_dem",)
        assert dn.urcontingencies == ()
        d1, d1_floor = dn.drreq_planes
        assert d1.alpha == pytest.approx(-80.0)
        assert d1.demand_coef == pytest.approx(-0.004)
        assert d1.contingency_coef == pytest.approx(0.96)
        assert d1_floor.alpha == pytest.approx(-80.0 + 0.96 * 200)

        csf = _zone(out, "CSF_UpMinProvision")
        assert csf.ur_requirement == (50.0,) * 12 + (70.0,) * 12
        assert csf.urcost == 0.0

        ucs = _ucmap(out)
        for name in (
            "CPF_Up10Calculation_I",
            "CPF_Up10Calculation_II",
            "CPF_Up10MinProvision",
            "CPF_DownCalculation",
            "CPF_DownMinProvision",
            "CSF_UpMinProvision",
            "Generation_Calculation",
        ):
            assert ucs[name].active is False, name
            assert "--reserve-model native" in ucs[name].description
        # Inertia_SEN is still used by InertiaGlobalMin_New.
        assert ucs["Inertia_Calculation_e1"].active is None
        assert ucs["G1_Comparison"].active is None
        link = ucs["CPF10s_RequirementCost"]
        assert 'reserve_zone("CPF10s").up = 0' in link.expression
        assert "CPF5mDown_RequirementCost" not in ucs

        assert out.inertia_zones == (InertiaZoneSpec(name="Inertia_SEN"),)
        g1, g2 = out.inertia_provisions
        assert (g1.generator, g1.provision_max) == ("G1", 50.0)
        assert g1.provision_factor * g1.provision_max == pytest.approx(5.0)
        assert g2.provision_max == pytest.approx(0.4)
        assert g2.provision_factor * g2.provision_max == pytest.approx(3.0)

        provs = {p.name or p.generator_name: p for p in out.reserve_provisions}
        g1p = provs["provision_G1"]
        assert g1p.reserve_zones == (
            "CPF_RS",
            "CPF10s",
            "CPF5mDown",
            "CSF_UpMinProvision",
        )
        assert g1p.ur_zone_factors == (1.0, 0.9, 0.0, 1.0)
        assert g1p.dr_zone_factors == (1.0, 0.0, 0.5, 0.0)
        assert provs["G3"].reserve_zones == ("CPF_RS",)

        text = caplog.text
        assert "factor 1.05 x demand" in text
        assert "CPF_Up10MinProvision: surplus (−Slack) cost dropped (a plane" in text
        assert "provision factor profiles != 1" in text
        assert "Orphan) .value" not in text
        assert "-2·decision_variable(Orphan).value not representable" in text
        assert "commitment(uc_G2).status not representable" in text
        assert "commitment(uc_G1).status of LGG's definition" in text
        assert "uc_NOPE" in text

    def test_endogenous_inertia(self) -> None:
        out = apply_native_reserve(_case(), _ENDO_I)
        up = _zone(out, "CPF10s")
        assert (up.inertia_term, up.inertia_zone) == ("endogenous", "Inertia_SEN")
        assert up.system_inertia == ()
        assert _zone(out, "CPF5mDown").inertia_term == ""
        assert out.inertia_zones == (InertiaZoneSpec(name="Inertia_SEN"),)
        g1, g2 = out.inertia_provisions
        assert (g1.generator, g1.provision_max) == ("G1", 50.0)
        assert g1.provision_factor * g1.provision_max == pytest.approx(5.0)
        assert g2.provision_max == pytest.approx(0.4)
        assert g2.provision_factor * g2.provision_max == pytest.approx(3.0)

    def test_must_run_inertia(self) -> None:
        """Only the hours PLEXOS forces a unit on (Gen_Commit = 1) count."""
        commits = (
            CommitmentSpec(generator_name="G1", commit_status_profile=(1,) * 24),
            CommitmentSpec(
                generator_name="G2", commit_status_profile=(1, 0, -1) + (1,) * 3
            ),
        )
        out = apply_native_reserve(_case(commitments=commits), _EXO_I)
        up = _zone(out, "CPF10s")
        assert (up.inertia_term, up.inertia_zone) == ("exogenous", "")
        assert out.inertia_zones == ()
        assert out.inertia_provisions == ()
        si = up.system_inertia
        assert si[:6] == pytest.approx((8.0, 5.0, 5.0, 8.0, 8.0, 8.0))
        assert si[6:] == pytest.approx((5.0,) * 18)

    def test_must_run_inertia_none_forced(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        out = apply_native_reserve(_case(), _EXO_I)
        assert _zone(out, "CPF10s").system_inertia == (0.0,) * 24
        assert "no unit is forced on; the exogenous system_inertia is 0" in (
            caplog.text
        )

    def test_endogenous(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            out = apply_native_reserve(_case(), _ENDO)
        up = _zone(out, "CPF10s")
        assert up.load_term == "endogenous_generation"
        assert up.demand_load_factor == 1.0
        assert not up.system_load
        assert "load term endogenous_generation" in caplog.text

    def test_system_load(self, caplog: pytest.LogCaptureFixture) -> None:
        opts = NativeReserveOptions(system_load=(9000.0,) * 24)
        with caplog.at_level(logging.INFO):
            out = apply_native_reserve(_case(), opts)
        assert _zone(out, "CPF10s").system_load == (9000.0,) * 24
        assert "given system load" in caplog.text

    def test_without_contingency_units(self, caplog: pytest.LogCaptureFixture) -> None:
        ucs = [
            u
            for u in _base_ucs()
            if not u.name.endswith("_Comparison") and u.name != "CSF_UpMinProvision"
        ]
        dvs = tuple(
            DecisionVariableSpec(name="MaxLoad_SEN") if d.name == "MaxLoad_SEN" else d
            for d in _dvs()
        )
        out = apply_native_reserve(_case(ucs, decision_variables=dvs), _EXO)
        up = _zone(out, "CPF10s")
        assert len(up.urreq_planes) == 2
        assert up.urreq_planes[0].contingency_coef is None
        assert up.urreq_planes[0].alpha == pytest.approx(516 - 12.3 * 0.8 + 375)
        dn = _zone(out, "CPF5mDown")
        # No bound on MaxLoad_SEN: the contingency term is dropped.
        (d1,) = dn.drreq_planes
        assert d1.contingency_coef is None
        assert d1.alpha == pytest.approx(-80.0)
        assert "contingency fixed at its lower bound" in caplog.text
        assert "CPF5mDown: no contingency comparison rows; contingency DV has no " in (
            caplog.text
        )

    def test_name_clash_and_missing_definitions(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ucs = [
            u
            for u in _base_ucs()
            if u.name not in ("Generation_Calculation", "Inertia_Calculation_e1")
        ]
        case = _case(
            ucs,
            reserves=(ReserveSpec(object_id=1, name="CPF10s"),),
            decision_variables=_dvs()[:-1],
        )
        out = apply_native_reserve(case, _EXO)
        up = _zone(out, "CPF10s_native")
        assert up.urreq_planes[0].demand_coef == pytest.approx(-0.0366)
        assert up.urreq_planes[0].inertia_coef is None
        assert up.inertia_zone == ""
        assert out.inertia_provisions == ()
        assert "no defining row found" in caplog.text
        assert "inertia term dropped" in caplog.text
        assert "without a ReserveProvision" not in caplog.text

    def test_bad_provision_row(self, caplog: pytest.LogCaptureFixture) -> None:
        ucs = [
            u
            for u in _base_ucs()
            if u.name not in ("CPF_DownMinProvision", "CSF_UpMinProvision")
        ]
        ucs = [
            _uc(
                u.name,
                u.expression.replace('provision_B1").up', 'provision_B1").dn'),
            )
            if u.name == "CPF_Up10MinProvision"
            else u
            for u in ucs
        ]
        out = apply_native_reserve(_case(ucs), _EXO)
        assert out.reserves == _case().reserves
        assert "provision row not of the form" in caplog.text

    def test_dated_coefficients(self, caplog: pytest.LogCaptureFixture) -> None:
        case = _case(bundle=BundleSpec(bundle_date="2026-11-07", n_days=2))
        out = apply_native_reserve(case, _EXO, db=_dated_db())
        p1 = _zone(out, "CPF10s").urreq_planes[0]
        assert isinstance(p1.demand_coef, tuple)
        assert len(p1.demand_coef) == 48
        assert p1.demand_coef[0] == pytest.approx(-0.040)
        assert p1.demand_coef[30] == pytest.approx(-0.0366)
        assert isinstance(p1.alpha, tuple)
        assert p1.alpha[0] == pytest.approx(600.0 - 12.3 * 0.8)
        assert p1.alpha[30] == pytest.approx(516.0 - 12.3 * 0.8)
        assert "differs from the extracted" in caplog.text
        # The CSF RHS profile spans one day: it repeats over both days.
        req = _zone(out, "CSF_UpMinProvision").ur_requirement
        assert len(req) == 48
        assert req[:24] == req[24:]

    def test_fixed_zone_variants(self) -> None:
        ucs = [
            _uc(
                "CTF_UpMinProvision",
                '1 * reserve_provision("provision_G1").up'
                f" + 1 * {_dv('CTF_VoRS')} >= 40",
            ),
            _uc(
                "CTF_DownMinProvision",
                '2 * reserve_provision("provision_G1").dn >= 30',
                penalty=700.0,
            ),
            _uc(
                "CSF_Def",
                '1 * reserve_provision("provision_G1").up'
                f" - 1 * {_dv('CSF_Requirement')} >= 0",
            ),
            _uc("Neg", '1 * reserve_provision("provision_G1").up >= -5'),
            _uc(
                "Mixed",
                '1 * reserve_provision("provision_G1").up'
                ' + 1 * reserve_provision("provision_G2").dn >= 5',
            ),
            _uc("Leq", '1 * reserve_provision("provision_G1").up <= 5'),
            _uc("CPF_RS", '1 * reserve_provision("provision_G1").up >= 5'),
        ]
        dvs = (
            DecisionVariableSpec(name="CTF_VoRS", cost=1000.0),
            DecisionVariableSpec(name="CSF_Requirement"),
        )
        out = apply_native_reserve(_case(ucs, decision_variables=dvs), _EXO)
        names = [z.name for z in out.reserves]
        assert names == ["CPF_RS", "CTF_UpMinProvision", "CTF_DownMinProvision"]
        ctf_up = _zone(out, "CTF_UpMinProvision")
        assert ctf_up.urcost == pytest.approx(1000.0)
        assert ctf_up.ur_requirement == (40.0,) * 24
        ctf_dn = _zone(out, "CTF_DownMinProvision")
        assert ctf_dn.drcost == pytest.approx(700.0)
        assert ctf_dn.dr_requirement == (30.0,) * 24
        provs = {p.name: p for p in out.reserve_provisions}
        assert provs["provision_G1"].dr_zone_factors == (1.0, 0.0, 2.0)
        ucs_out = _ucmap(out)
        assert ucs_out["CSF_Def"].active is None
        assert ucs_out["CTF_UpMinProvision"].active is False

    def test_fixed_zone_cost_from_two_dvs(self) -> None:
        ucs = [
            _uc(
                "CTF_UpMinProvision",
                '1 * reserve_provision("provision_G1").up'
                f" + 1 * {_dv('A')} + 2 * {_dv('B')} >= 40",
                penalty=900.0,
            ),
        ]
        dvs = (
            DecisionVariableSpec(name="A", cost=1000.0),
            DecisionVariableSpec(name="B", cost=1000.0),
        )
        out = apply_native_reserve(_case(ucs, decision_variables=dvs), _EXO)
        assert _zone(out, "CTF_UpMinProvision").urcost == pytest.approx(500.0)

    def test_missing_provision_reference(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ucs = [
            _uc(
                "CTF_UpMinProvision",
                '1 * reserve_provision("provision_ZZ").up >= 40',
            ),
        ]
        out = apply_native_reserve(_case(ucs), _EXO)
        assert _zone(out, "CTF_UpMinProvision").ur_requirement == (40.0,) * 24
        assert "without a ReserveProvision" in caplog.text

    def test_skips_inactive_and_scoped_rows(self) -> None:
        ucs = [
            _uc("A", '1 * reserve_provision("provision_G1").up >= 4', active=False),
            _uc("B", '1 * reserve_provision("provision_G1").up >= 4', daily_sum=True),
            _uc("C", '1 * reserve_provision("provision_G1").up >= 4', scope="x"),
        ]
        case = _case(ucs)
        assert apply_native_reserve(case, _EXO) is case

    def test_generation_definition_kept_when_used(self) -> None:
        ucs = [
            *_base_ucs(),
            _uc("UsesGen", f"1 * {_dv('Generation_SEN')} >= 1"),
        ]
        out = apply_native_reserve(_case(ucs), _EXO)
        assert _ucmap(out)["Generation_Calculation"].active is None

    def test_positive_definition_coefficient(self) -> None:
        """``1000·I − Σ H·S·u <= …`` style rows are not definitions; a
        positive ``k`` flips the sign of the unit terms."""
        ucs = [u for u in _base_ucs() if u.name != "Inertia_Calculation_e1"] + [
            _uc(
                "Inertia_Calculation_e1",
                f'1000 * {_dv("Inertia_SEN")} - 5 * commitment("uc_G1").status >= 800',
            )
        ]
        out = apply_native_reserve(_case(ucs), _ENDO_I)
        (g1,) = out.inertia_provisions
        assert g1.provision_factor * g1.provision_max == pytest.approx(5.0)
        p1 = _zone(out, "CPF10s").urreq_planes[0]
        assert p1.inertia_coef == pytest.approx(0.0123)


_CON, _DVC, _SYS = 70, 71, 1
_C_DV, _C_SYS = 90, 91
_P_VC, _P_RHS = 1, 2


def _dated_db() -> PlexosDb:
    """``CPF_Up10Calculation_I``: Generation_SEN coefficient 40 on day 1
    (dated), 36.6 undated; RHS 600 dated on day 1, 516 undated."""
    db = PlexosDb(path=Path("synthetic.xml"))
    db.horizon_start = datetime(2026, 11, 7)
    db.classes_by_name = {"Constraint": _CON, "Decision Variable": _DVC, "System": 5}
    db.classes_by_id = {v: k for k, v in db.classes_by_name.items()}
    db.objects = [
        PlexosObject(object_id=1, class_id=_CON, name="CPF_Up10Calculation_I"),
        PlexosObject(object_id=2, class_id=_DVC, name="Generation_SEN"),
        PlexosObject(object_id=3, class_id=5, name="System"),
        PlexosObject(object_id=4, class_id=_DVC, name="Orphan"),
    ]
    db.collections = [
        PlexosCollection(
            collection_id=_C_DV,
            parent_class_id=_DVC,
            child_class_id=_CON,
            name="Constraints",
        ),
        PlexosCollection(
            collection_id=_C_SYS,
            parent_class_id=5,
            child_class_id=_CON,
            name="Constraints",
        ),
    ]
    db.memberships = [
        PlexosMembership(
            membership_id=10, collection_id=_C_DV, parent_object_id=2, child_object_id=1
        ),
        PlexosMembership(
            membership_id=11,
            collection_id=_C_SYS,
            parent_object_id=3,
            child_object_id=1,
        ),
        # Parent / child of the wrong class: ignored.
        PlexosMembership(
            membership_id=12, collection_id=_C_DV, parent_object_id=1, child_object_id=4
        ),
    ]
    db.properties = [
        PlexosProperty(
            property_id=_P_VC, collection_id=_C_DV, name="Value Coefficient"
        ),
        PlexosProperty(property_id=_P_RHS, collection_id=_C_SYS, name="RHS"),
    ]
    day1 = datetime(2026, 11, 7)
    db.data_rows = [
        PlexosDataRow(data_id=1, membership_id=10, property_id=_P_VC, value=36.6),
        PlexosDataRow(
            data_id=2,
            membership_id=10,
            property_id=_P_VC,
            value=40.0,
            date_from=day1,
            date_to=day1,
        ),
        PlexosDataRow(data_id=3, membership_id=11, property_id=_P_RHS, value=516.0),
        PlexosDataRow(
            data_id=4,
            membership_id=11,
            property_id=_P_RHS,
            value=600.0,
            date_from=day1,
            date_to=day1,
        ),
        PlexosDataRow(data_id=5, membership_id=12, property_id=_P_VC, value=1.0),
        PlexosDataRow(data_id=6, membership_id=99, property_id=_P_VC, value=1.0),
    ]
    return db


def test_dated_index_without_db() -> None:
    idx = DatedIndex(None)
    assert idx.start is None
    assert idx.rows("a", "b", "c") == []
    assert idx.rhs_rows("a") == []


# ── writer ─────────────────────────────────────────────────────────────────
class TestWriter:
    def test_dynamic_zone(self) -> None:
        zone = ReserveSpec(
            object_id=0,
            name="CPF10s",
            description="native",
            urreq_planes=(
                ReservePlaneSpec(
                    alpha=(1.0, 2.0),
                    demand_coef=-0.0366,
                    contingency_coef=1.25,
                    flow_coef=None,
                ),
            ),
            drreq_planes=(ReservePlaneSpec(alpha=0.0),),
            load_term="exogenous",
            system_load=(9000.0, 9100.0),
            demand_load_factor=1.025,
            inertia_zone="Inertia_SEN",
            contingency_generators=("G1",),
            contingency_demands=("B1_dem",),
            flow_lines=("L1",),
            urcontingencies=(
                ReserveContingencySpec(
                    name="c1",
                    generators=("G1", "PV"),
                    generator_coefs=(1.0, 0.5),
                    lines=("L1",),
                    line_coefs=(1.0,),
                    offset=8.4,
                ),
                ReserveContingencySpec(demands=("B1_dem",), demand_coefs=(1.0,)),
            ),
            drcontingencies=(
                ReserveContingencySpec(demands=("B1_dem",), demand_coefs=(-1.0,)),
            ),
            urcost=10000.0,
            omit_zero_requirement=True,
        )
        entry = build_reserve_zone_array((zone,), block_count=2)[0]
        assert "urreq" not in entry
        assert "drreq" not in entry
        assert entry["description"] == "native"
        assert entry["urreq_planes"] == [
            {"alpha": [[1.0, 2.0]], "demand_coef": -0.0366, "contingency_coef": 1.25}
        ]
        assert entry["drreq_planes"] == [{"alpha": 0.0}]
        assert entry["system_load"] == [[9000.0, 9100.0]]
        assert entry["demand_load_factor"] == 1.025
        assert entry["inertia_zone"] == "Inertia_SEN"
        assert entry["contingency_generators"] == ["G1"]
        assert entry["contingency_demands"] == ["B1_dem"]
        assert entry["flow_lines"] == ["L1"]
        assert entry["urcontingencies"] == [
            {
                "name": "c1",
                "generators": ["G1", "PV"],
                "generator_coefs": [1.0, 0.5],
                "lines": ["L1"],
                "offset": 8.4,
            },
            {"demands": ["B1_dem"]},
        ]
        assert entry["drcontingencies"] == [
            {"demands": ["B1_dem"], "demand_coefs": [-1.0]}
        ]

    def test_fixed_omit_zero(self) -> None:
        zone = ReserveSpec(
            object_id=0,
            name="CSF",
            ur_requirement=(5.0, 6.0),
            dr_requirement=(1.0, 1.0),
            omit_zero_requirement=True,
        )
        entry = build_reserve_zone_array((zone,), block_count=2)[0]
        assert entry["urreq"] == [[5.0, 6.0]]
        assert entry["drreq"] == [[1.0, 1.0]]
        assert "load_term" not in entry
        assert "description" not in entry

    def test_provision_factors_and_flags(self) -> None:
        rp = ReserveProvisionSpec(
            generator_name="G1",
            reserve_zones=("A", "B"),
            name="provision_G1",
            ur_zone_factors=(1.0, 0.9),
            dr_zone_factors=(1.0, 0.0),
            shared_headroom=True,
            offline=False,
            symmetric=None,
        )
        entry = build_reserve_provision_array((rp,))[0]
        assert entry["ur_zone_factors"] == [1.0, 0.9]
        assert entry["dr_zone_factors"] == [1.0, 0.0]
        assert entry["shared_headroom"] is True
        assert entry["offline"] is False
        assert "symmetric" not in entry

    def test_inertia_arrays(self) -> None:
        zones = build_inertia_zone_array(
            (
                InertiaZoneSpec(name="Inertia_SEN"),
                InertiaZoneSpec(name="NR", requirement=5.0, cost=100.0),
            )
        )
        assert zones == [
            {"uid": 1, "name": "Inertia_SEN"},
            {"uid": 2, "name": "NR", "requirement": 5.0, "cost": 100.0},
        ]
        provs = build_inertia_provision_array(
            (
                InertiaProvisionSpec(
                    name="inertia_G1",
                    generator="G1",
                    inertia_zones=("Inertia_SEN",),
                    provision_factor=5.0,
                    provision_max=1.0,
                ),
            )
        )
        assert provs == [
            {
                "uid": 1,
                "name": "inertia_G1",
                "generator": "G1",
                "inertia_zones": ["Inertia_SEN"],
                "provision_factor": 5.0,
                "provision_max": 1.0,
            }
        ]


def test_cli_flags() -> None:
    parser = make_parser()
    args = parser.parse_args(["-i", "x"])
    assert args.reserve_model == "native"
    assert args.reserve_load_term == "exogenous"
    assert args.reserve_load_factor is None  # default 1.05 applied later
    assert args.reserve_system_load is None
    args = parser.parse_args(
        [
            "-i",
            "x",
            "--reserve-model",
            "uc",
            "--reserve-load-term",
            "endogenous",
            "--reserve-load-factor",
            "1.04",
            "--reserve-system-load",
            "g.csv",
        ]
    )
    assert (args.reserve_model, args.reserve_load_term) == ("uc", "endogenous")
    assert args.reserve_load_factor == pytest.approx(1.04)
    assert args.reserve_system_load == Path("g.csv")
    for bad in ("0", "-1", "abc"):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_float(bad)


def test_include_in_st_zero_rows_are_never_converted() -> None:
    """``SpecialCPFNorth*`` / ``*_Definition`` / ``TxJadresic_Comparison``
    ship ``Include in ST Schedule = 0`` (extracted ``active=False``): their
    requirement is the endogenous corridor flow, so they must stay inactive
    stubs and never become zones, planes or contingency candidates."""
    ucs = [
        *_base_ucs(),
        _uc(
            "SpecialCPFNorth",
            '1 * reserve_provision("provision_G1").up'
            f" - 1 * {_dv('SpecialCPFNorth_Requirement')} >= 0",
            active=False,
        ),
        _uc(
            "SpecialCPFNorth_Definition",
            f'1 * {_dv("SpecialCPFNorth_Requirement")} - 0.1 * line("L1").flow >= 0',
            active=False,
        ),
        _uc(
            "TxJadresic_Comparison",
            f'1 * line("L1").flow + 1 * {_dv("MaxInjection_SEN")} >= 0',
            active=False,
        ),
        _uc(
            "SpecialCSFNorth",
            '1 * reserve_provision("provision_G1").up >= 30',
            active=False,
        ),
    ]
    out = apply_native_reserve(_case(ucs), _EXO)
    names = {z.name for z in out.reserves}
    assert not any(n.startswith("Special") for n in names)
    ucs_out = _ucmap(out)
    for name in ("SpecialCPFNorth", "SpecialCPFNorth_Definition", "SpecialCSFNorth"):
        assert ucs_out[name].active is False
        assert "--reserve-model native" not in ucs_out[name].description
    cands = _zone(out, "CPF10s").urcontingencies
    assert "TxJadresic_Comparison" not in {c.name for c in cands}


def test_zero_alpha_plane_and_non_comparison_rows() -> None:
    """A plane with RHS 0 keeps ``alpha = 0``; rows bounding the contingency
    DV without unit terms and unparseable rows are ignored."""
    ucs = [
        *_base_ucs(),
        _uc(
            "CPF_Up10Calculation_III",
            f"1 * {_dv('CPF10s_Requirement')} + 10 * {_dv('Generation_SEN')} >= 0",
        ),
        _uc("MaxInj_Floor", f"1 * {_dv('MaxInjection_SEN')} >= 300"),
        _uc("Opaque", "sum(g in G: generator(g).generation) >= 1"),
    ]
    out = apply_native_reserve(_case(ucs), _EXO)
    planes = _zone(out, "CPF10s").urreq_planes
    assert planes[-1].alpha == 0.0
    assert planes[-1].demand_coef == pytest.approx(-0.01)
    assert _ucmap(out)["MaxInj_Floor"].active is None


def test_db_loader_is_lazy() -> None:
    calls: list[int] = []

    def loader() -> PlexosDb:
        calls.append(1)
        return _dated_db()

    plain = _case([_uc("Unrelated", '1 * generator("G2").generation <= 99')])
    assert apply_native_reserve(plain, _EXO, db_loader=loader) is plain
    assert not calls
    out = apply_native_reserve(_case(), _EXO, db_loader=loader)
    assert calls == [1]
    alpha = _zone(out, "CPF10s").urreq_planes[0].alpha
    assert alpha == pytest.approx(600.0 - 12.3 * 0.8)


def test_load_reserve_db(tmp_path: Path) -> None:
    assert _load_reserve_db(SimpleNamespace()) is None
    assert _load_reserve_db(SimpleNamespace(xml_path=tmp_path / "no.xml")) is None
    xml = tmp_path / "db.xml"
    xml.write_text(
        '<MasterDataSet xmlns="http://tempuri.org/MasterDataSet.xsd"></MasterDataSet>',
        encoding="utf-8",
    )
    db = _load_reserve_db(SimpleNamespace(xml_path=xml))
    assert isinstance(db, PlexosDb)
