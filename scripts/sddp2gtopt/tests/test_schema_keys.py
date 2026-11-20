# SPDX-License-Identifier: BSD-3-Clause
"""sddp2gtopt emits only keys gtopt's strict JSON parser accepts.

Converts the bundled fixtures (the multi-file PSR SDDP block case and the
PSRClasses / legacy ``.dat`` cases) with both planning methods and walks
the written planning JSON against the C++ ``json_data_contract`` snapshot
(:mod:`gtopt_shared.json_contracts`): no unknown key, no deprecated
boundary-cut key, no naming-dialect alias in place of a canonical key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtopt_shared.json_contracts import find_schema_problems
from sddp2gtopt.sddp2gtopt import convert_sddp_case

DATA = Path(__file__).parent / "data"
CASES = ("sddp_block_case", "case0", "case_two_systems", "dat_case", "psri_case1")


@pytest.mark.parametrize("method", ["sddp", "monolithic"])
@pytest.mark.parametrize("case", CASES)
def test_converted_case_keys_match_contract(
    tmp_path: Path, case: str, method: str
) -> None:
    out = tmp_path / case
    rc = convert_sddp_case(
        {"input_dir": DATA / case, "output_dir": out, "name": case, "method": method}
    )
    assert rc == 0
    planning = json.loads((out / f"{case}.json").read_text(encoding="utf-8"))
    issues = find_schema_problems(planning)
    assert not issues, [i.message for i in issues]
    model = planning["options"].get("model_options", {})
    assert model, "model options must be nested under options.model_options"
