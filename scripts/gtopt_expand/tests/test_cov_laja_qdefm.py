# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for the Laja qdefm netting carrier (hoya-intermedia inflows)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gtopt_expand.laja_agreement import LajaAgreement
from plp2gtopt.laja_parser import LajaParser

_PLPLAJAM = (
    Path(__file__).resolve().parents[2]
    / "cases"
    / "plp_min_irrigation"
    / "plplajam.dat"
)
_CAL_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


class _Stages:
    def get_all(self) -> list[dict[str, Any]]:
        return [{"number": i + 1, "month": m} for i, m in enumerate(_CAL_MONTHS)]


@pytest.fixture(name="cfg")
def _cfg() -> dict[str, Any]:
    parser = LajaParser(_PLPLAJAM)
    parser.parse()
    return dict(parser.config)


def _entities(laja: LajaAgreement) -> str:
    return json.dumps(laja.to_json_dict())


def test_qdefm_carrier_single_series(cfg):
    base = _entities(LajaAgreement(dict(cfg), stage_parser=_Stages()))
    cfg["q_hoya_inter"] = [5.0] * 6  # shorter than the stage list → 0 tail
    laja = LajaAgreement(cfg, stage_parser=_Stages())
    assert laja._cfg["use_qdefm_carrier"] is True
    assert _entities(laja) != base


def test_qdefm_carrier_scenario_mean(cfg):
    one = dict(cfg)
    one["q_hoya_inter"] = [6.0] * 12
    mean = dict(cfg)
    mean["q_hoya_inter"] = [6.0] * 12
    mean["q_hoya_inter_by_scenario"] = [[4.0] * 12, [8.0] * 12]
    # The scenario mean (6.0) feeds the qdefm cap; it must reproduce the
    # single-series result.
    assert _entities(LajaAgreement(mean, stage_parser=_Stages())) == _entities(
        LajaAgreement(one, stage_parser=_Stages())
    )


def test_qdefm_disabled_by_flags(cfg):
    cfg["q_hoya_inter"] = [5.0] * 12
    cfg["enable_attribution_cap"] = False
    cfg["enable_netted_targets"] = False
    laja = LajaAgreement(cfg, stage_parser=_Stages())
    assert "use_qdefm_carrier" not in laja._cfg
