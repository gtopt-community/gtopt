"""``--import-limit``: fixed-schedule imports keep their schedule; only the
flexible imports are scaled into the remaining room."""

from __future__ import annotations

import logging

import pytest

from sddp2gtopt.dat_loader import _apply_import_cap
from sddp2gtopt.entities import ThermalSpec


def _imp(name: str, pmax: float, tipo: str = "", prof=None) -> ThermalSpec:
    return ThermalSpec(
        code=1,
        name=name,
        reference_id=1,
        pmax=pmax,
        is_import=True,
        amm_tipo=tipo,
        amm_profile=list(prof or []),
    )


def test_fixed_import_kept_flexible_scaled() -> None:
    """Guatemala ncp_pdd: EDC-I fixed at 120 MW for a day (RESTMEX '='), the
    other ties flexible.  EDC-I keeps 120 MW; the flexible ones share 80."""
    edc = _imp("EDC-I", 120.0, "=", [None] * 24 + [120.0] * 24 + [None] * 120)
    mex = _imp("MEX-I", 300.0)
    esa = _imp("ESA-I", 100.0)
    other = ThermalSpec(code=9, name="TERMO", reference_id=9, pmax=500.0)
    _apply_import_cap([edc, mex, esa, other], 200.0)
    assert edc.pmax == pytest.approx(120.0)  # never below its fixed schedule
    assert mex.pmax + esa.pmax == pytest.approx(80.0)
    assert mex.pmax / esa.pmax == pytest.approx(3.0)  # proportional
    assert other.pmax == pytest.approx(500.0)  # not an import


def test_no_scaling_under_the_limit() -> None:
    a, b = _imp("MEX-I", 50.0), _imp("HON-I", 60.0)
    _apply_import_cap([a, b], 200.0)
    assert (a.pmax, b.pmax) == (pytest.approx(50.0), pytest.approx(60.0))


def test_fixed_above_limit_warns_and_closes_flexible(caplog) -> None:
    edc = _imp("EDC-I", 250.0, ">", [250.0] * 4)
    mex = _imp("MEX-I", 300.0)
    with caplog.at_level(logging.WARNING):
        _apply_import_cap([edc, mex], 200.0)
    assert edc.pmax == pytest.approx(250.0)
    assert mex.pmax == pytest.approx(0.0)
    assert "fixed-schedule imports" in caplog.text


def test_pmax_cap_constraint_is_flexible() -> None:
    """A '<' (pmax) RESTMEX constraint does not make an import fixed."""
    a = _imp("MEX-I", 300.0, "<", [100.0] * 4)
    b = _imp("ESA-I", 100.0)
    _apply_import_cap([a, b], 200.0)
    assert a.pmax + b.pmax == pytest.approx(200.0)
