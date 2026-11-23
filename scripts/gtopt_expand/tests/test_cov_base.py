# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for gtopt_expand._base and _template_engine."""

from __future__ import annotations

import jinja2
import pytest

from gtopt_expand._base import _RightsAgreementBase
from gtopt_expand._template_engine import _json_finalize


class _Agreement(_RightsAgreementBase):
    """Bare subclass: skips template rendering so helpers run in isolation."""

    _ARTIFACT = "laja"

    def _build(self) -> None:
        return None

    def _prepare_context(self) -> dict:
        return {}


class _Stages:
    def __init__(self, stages):
        self._stages = stages

    def get_all(self):
        return self._stages


def test_missing_artifact_raises():
    with pytest.raises(TypeError, match="must set _ARTIFACT"):
        _RightsAgreementBase({})


def test_prepare_context_is_abstract():
    with pytest.raises(NotImplementedError):
        _RightsAgreementBase._prepare_context(_Agreement({}))


def test_get_stages_and_block_counts():
    assert _Agreement({})._get_stages() == []
    stages = _Stages(
        [
            {"number": 1, "count_block": "bad"},
            {"number": 2, "count_block": 0},
            {"number": 3},
        ]
    )
    ag = _Agreement({}, stage_parser=stages, options={"last_stage": "x"})
    assert len(ag._get_stages()) == 3
    ag2 = _Agreement(
        {}, stage_parser=stages, options={"last_stage": 2, "blocks_per_stage": 4}
    )
    assert [s["number"] for s in ag2._get_stages()] == [1, 2]
    # bad → uniform 4, 0 → clamped to 1, beyond the parser → uniform.
    assert ag2._stage_block_counts(3) == [4, 1, 4]


def test_schedule_helpers():
    ag = _Agreement({}, options={"blocks_per_stage": 2})
    assert ag._to_t_sched([]) == 0.0
    assert ag._to_t_sched([3.0, 3.0]) == 3.0
    assert ag._to_t_sched([1.0, 2.0]) == [1.0, 2.0]
    assert ag._to_tb_sched([]) == 0.0
    assert ag._to_stb_sched([]) == 0.0
    assert ag._to_stb_sched([5.0]) == 5.0
    assert ag._to_stb_sched([1.0, 2.0]) == [[[1.0, 1.0], [2.0, 2.0]]]


def test_to_json_dict_inline_constraints():
    ag = _Agreement({})
    ag.user_constraints = [{"name": "uc"}]
    assert ag.to_json_dict() == {"user_constraint_array": [{"name": "uc"}]}


def test_json_finalize_undefined():
    assert _json_finalize(jinja2.Undefined()) == ""
    assert _json_finalize([1, "a"]) == '[1, "a"]'
