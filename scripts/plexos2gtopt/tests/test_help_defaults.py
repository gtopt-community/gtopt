"""``plexos2gtopt --help`` defaults must match the argparse defaults."""

from __future__ import annotations

import argparse
import importlib
import re

plexos_main = importlib.import_module("plexos2gtopt.main")

_DEFAULT_RE = re.compile(r"\(default:\s*([^)]*)\)")


def _action(parser: argparse.ArgumentParser, flag: str) -> argparse.Action:
    for action in parser._actions:  # pylint: disable=protected-access
        if flag in action.option_strings:
            return action
    raise AssertionError(f"{flag} not registered")


def _rendered_help(action: argparse.Action) -> str:
    text = action.help or ""
    if "%(" in text:
        text = text % {**vars(action), "prog": "plexos2gtopt"}
    return " ".join(text.split())


def test_line_losses_mode_help_default() -> None:
    """The help shows the converter's default, not gtopt's 'adaptive'."""
    parser = plexos_main.make_parser()
    action = _action(parser, "--line-losses-mode")
    assert action.default == plexos_main.DEFAULT_LINE_LOSSES_MODE
    text = _rendered_help(action)
    match = _DEFAULT_RE.search(text)
    assert match is not None
    assert match.group(1).strip() == plexos_main.DEFAULT_LINE_LOSSES_MODE
    assert "gtopt picks 'adaptive'" not in text


def test_literal_help_defaults_match_argparse() -> None:
    """Every '(default: X)' of a string flag equals the argparse default."""
    parser = plexos_main.make_parser()
    mismatches = []
    for action in parser._actions:  # pylint: disable=protected-access
        if not isinstance(action.default, str) or not action.help:
            continue
        for match in _DEFAULT_RE.finditer(_rendered_help(action)):
            shown = match.group(1).strip()
            if " " in shown:  # descriptive text, not a literal value
                continue
            if shown != action.default:
                mismatches.append((action.option_strings, action.default, shown))
    assert not mismatches
