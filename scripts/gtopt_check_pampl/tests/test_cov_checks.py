# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for the individual PAMPL checks."""

from __future__ import annotations

from gtopt_check_pampl import _checks as ck


def _msgs(findings):
    return [f.message for f in findings]


def test_syntax_unmatched_closers():
    msgs = _msgs(ck.check_syntax("a = b) + c];\n(x;\n[y;\n", "f.pampl"))
    assert any("Unmatched ')'" in m for m in msgs)
    assert any("Unmatched ']'" in m for m in msgs)
    assert any("(" in m and "Unmatched" not in m for m in msgs)


def test_constraint_header_without_colon():
    msgs = _msgs(ck.check_constraint_names('constraint foo "desc"\n  x <= 1;\n'))
    assert msgs


def test_param_declarations():
    msgs = _msgs(ck.check_param_declarations("param p;\nparam q\n  = 3;\n"))
    assert any("missing '='" in m for m in msgs)
    assert len([m for m in msgs if "missing '='" in m]) == 1


def test_operator_usage_runs_on_mixed_statements():
    assert isinstance(ck.check_operator_usage(";;\n x <= 1;\n"), list)


def test_domain_clauses_dimensions():
    msgs = _msgs(ck.check_domain_clauses("x <= 1 for(stage in 1..3, foo in {1});\n"))
    assert any("foo" in m for m in msgs)


def test_template_block_and_if_balance():
    msgs = _msgs(ck.check_template_variables("{% if a %\n{% if b %}\nx;\n"))
    assert any("template block" in m for m in msgs)
    assert any("if" in m.lower() for m in msgs)


def test_stats_counts_bare_and_named():
    stats = ck.compute_stats(
        "param p = 1;\nconstraint c1: x <= 1;\ny >= 0;\ninactive constraint c2: z <= 2;\n"
    )
    assert stats.num_constraints == 3
    assert stats.num_inactive == 1
    assert stats.num_params == 1


def test_split_statements_tail_and_blank_parts():
    stmts = ck._split_statements("a <= 1; ;\n\nb >= 2")
    assert [s.strip() for s, _ in stmts] == ["a <= 1", "b >= 2"]
    assert ck._split_statements("x;\n   \n") == [("x", 1)]
