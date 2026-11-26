"""``plp2gtopt --help`` defaults must match the values actually used.

Guards against help strings that hard-code a default which later drifts
from the real argparse default or from what the writer emits.
"""

from __future__ import annotations

import argparse
import importlib
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from plp2gtopt._parsers import DEFAULT_LINE_LOSSES_MODE, DEFAULT_METHOD
from plp2gtopt.falla_segments import DEFAULT_GLOBAL_DEMAND_FAIL_COST
from plp2gtopt.gtopt_writer import GTOptWriter

# ``plp2gtopt.main`` the attribute is the entry function; get the module.
plp_main = importlib.import_module("plp2gtopt.main")

#: Descriptive help defaults that stand for a literal argparse default.
_SYNONYMS: dict[str, object] = {
    "enabled": True,
    "disabled": False,
    "empty": "",
    "all stages": -1,
}

_DEFAULT_RE = re.compile(r"\(default:\s*([^)]*)\)")


@pytest.fixture(name="parser")
def _parser(monkeypatch: pytest.MonkeyPatch) -> argparse.ArgumentParser:
    """Parser built without the user's ``~/.gtopt.conf`` overrides."""
    monkeypatch.setattr(plp_main, "_conf_defaults", dict)
    return plp_main.make_parser()


def _rendered_help(action: argparse.Action) -> str:
    text = action.help or ""
    if "%(" in text:
        text = text % {**vars(action), "prog": "plp2gtopt"}
    return " ".join(text.split())


def _action(parser: argparse.ArgumentParser, flag: str) -> argparse.Action:
    for action in parser._actions:  # pylint: disable=protected-access
        if flag in action.option_strings:
            return action
    raise AssertionError(f"{flag} not registered")


def _help_default(parser: argparse.ArgumentParser, flag: str) -> str:
    match = _DEFAULT_RE.search(_rendered_help(_action(parser, flag)))
    assert match is not None, f"{flag} help has no '(default: ...)'"
    return match.group(1).strip()


def _matches(default: object, shown: str) -> bool:
    head = re.split(r"\s+[—;-]\s+|;\s*", shown, maxsplit=1)[0].strip()
    if head in _SYNONYMS:
        return _SYNONYMS[head] == default
    if isinstance(default, bool):
        return head == str(default)
    if isinstance(default, (int, float)):
        try:
            return float(head) == float(default)
        except ValueError:
            return False
    return head == str(default)


def test_literal_help_defaults_match_argparse(
    parser: argparse.ArgumentParser,
) -> None:
    """Every '(default: X)' of a flag with a concrete default equals it."""
    mismatches = []
    for action in parser._actions:  # pylint: disable=protected-access
        if action.default is None or isinstance(action.default, Path):
            continue
        if action.help in (None, argparse.SUPPRESS):
            continue
        for match in _DEFAULT_RE.finditer(_rendered_help(action)):
            if not _matches(action.default, match.group(1).strip()):
                mismatches.append(
                    (action.option_strings, action.default, match.group(1))
                )
    assert not mismatches


def test_method_help_default(parser: argparse.ArgumentParser) -> None:
    """``-M`` help names cascade as the default, never sddp."""
    assert DEFAULT_METHOD == "cascade"
    assert _help_default(parser, "--method") == DEFAULT_METHOD
    assert parser.parse_args(["case"]).method == DEFAULT_METHOD
    assert "'sddp' (default)" not in _rendered_help(_action(parser, "--method"))
    # pylint: disable-next=protected-access
    assert plp_main._SECTION_DEFAULTS["method"] == DEFAULT_METHOD


def test_line_losses_mode_help_default_is_emitted(
    parser: argparse.ArgumentParser,
) -> None:
    """The help default is the mode written to model_options."""
    assert _help_default(parser, "--line-losses-mode") == DEFAULT_LINE_LOSSES_MODE
    args = parser.parse_args(["case"])
    assert args.line_losses_mode == DEFAULT_LINE_LOSSES_MODE
    opts = plp_main.build_options(args)
    assert opts["model_options"]["line_losses_mode"] == DEFAULT_LINE_LOSSES_MODE


def test_demand_fail_cost_help_default_is_written(
    parser: argparse.ArgumentParser,
) -> None:
    """The global demand_fail_cost in the help is the one the writer emits."""
    shown = _help_default(parser, "--demand-fail-cost")
    assert float(shown) == pytest.approx(DEFAULT_GLOBAL_DEMAND_FAIL_COST)
    args = parser.parse_args(["case"])
    assert args.demand_fail_cost is None
    opts = plp_main.build_options(args)
    writer = GTOptWriter(MagicMock())
    writer.process_options({**opts, "output_dir": "out"})
    written = writer.planning["options"]["model_options"]["demand_fail_cost"]
    assert written == pytest.approx(DEFAULT_GLOBAL_DEMAND_FAIL_COST)


def test_epilog_uses_only_registered_flags(parser: argparse.ArgumentParser) -> None:
    """Every short flag used in the epilog examples exists."""
    known = {opt for a in parser._actions for opt in a.option_strings}  # pylint: disable=protected-access
    used = set(re.findall(r"(?<![\w-])(-[A-Za-z])\b", parser.epilog or ""))
    assert used <= known, used - known
