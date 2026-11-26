# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--legacy-profile``: the shared flag, its deprecated per-tool alias, the
explicit-flag precedence and the recorded toggle list."""

from __future__ import annotations

import argparse
import logging
import re

import pytest

from gtopt_shared.cli_flags import (
    LEGACY_PROFILE_CHOICES,
    LegacyToggle,
    add_legacy_profile_argument,
    apply_legacy_profile,
)

_PROFILES = {
    "cen": (
        LegacyToggle("mode", "replica", "the replica mode", ("--mode",)),
        LegacyToggle("strict", False, "relaxed bounds", ("--strict", "--no-strict")),
        LegacyToggle("quirk", True, "an internal quirk"),
    ),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--mode", default="clean")
    parser.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True)
    add_legacy_profile_argument(
        parser, profiles=_PROFILES, alias="--tool-legacy", alias_dest="tool_legacy"
    )
    return parser


def _apply(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    args = _parser().parse_args(argv)
    applied = apply_legacy_profile(
        args,
        _PROFILES,
        alias_dest="tool_legacy",
        alias_flag="--tool-legacy",
        argv=argv,
    )
    return args, applied


def test_choices() -> None:
    assert LEGACY_PROFILE_CHOICES == ("cen",)


def test_default_is_clean() -> None:
    args, applied = _apply([])
    assert args.legacy_profile is None
    assert args.tool_legacy is False
    assert not applied
    assert args.legacy_toggles == []
    assert (args.mode, args.strict) == ("clean", True)
    assert not hasattr(args, "quirk")


def test_cen_applies_every_toggle(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        args, applied = _apply(["--legacy-profile", "cen"])
    assert args.legacy_profile == "cen"
    assert args.tool_legacy is True
    assert (args.mode, args.strict, args.quirk) == ("replica", False, True)
    assert applied == ["mode=replica", "strict=False", "quirk=True"]
    assert args.legacy_toggles == applied
    assert "--legacy-profile cen: applying mode=replica" in caplog.text


@pytest.mark.parametrize(
    "extra",
    [["--mode", "clean"], ["--mode=clean"], ["--strict"], ["--no-strict"]],
)
def test_explicit_flags_override_the_profile(
    extra: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        args, applied = _apply(["--legacy-profile", "cen", *extra])
    dest = "mode" if extra[0].startswith("--mode") else "strict"
    assert not any(a.startswith(f"{dest}=") for a in applied)
    assert "quirk=True" in applied  # internal toggles always apply
    if dest == "mode":
        assert args.mode == "clean"
    else:
        assert args.strict is (extra[0] == "--strict")
    assert f"explicit flags keep {dest}" in caplog.text


def test_deprecated_alias_maps_to_cen(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        args, applied = _apply(["--tool-legacy"])
    assert args.legacy_profile == "cen"
    assert args.tool_legacy is True
    assert "mode=replica" in applied
    assert "--tool-legacy is deprecated: use --legacy-profile cen" in caplog.text


def test_idempotent(caplog: pytest.LogCaptureFixture) -> None:
    args, applied = _apply(["--tool-legacy"])
    args.mode = "changed later"
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        again = apply_legacy_profile(
            args, _PROFILES, alias_dest="tool_legacy", alias_flag="--tool-legacy"
        )
    assert again == applied
    assert args.mode == "changed later"
    assert "deprecated" not in caplog.text


def test_unknown_profile_on_a_bare_namespace() -> None:
    args = argparse.Namespace(legacy_profile="aemo", tool_legacy=False)
    with pytest.raises(ValueError, match="aemo"):
        apply_legacy_profile(
            args, _PROFILES, alias_dest="tool_legacy", alias_flag="--tool-legacy"
        )


def test_argv_defaults_to_sys_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["tool", "--legacy-profile", "cen", "--mode", "x"])
    args = _parser().parse_args(["--legacy-profile", "cen", "--mode", "x"])
    applied = apply_legacy_profile(
        args, _PROFILES, alias_dest="tool_legacy", alias_flag="--tool-legacy"
    )
    assert args.mode == "x"
    assert "mode=replica" not in applied


def test_help_lists_the_toggles_per_profile() -> None:
    flat = re.sub(r"\s+", " ", _parser().format_help())
    assert "--legacy-profile {cen}" in flat
    assert "Default: no profile, i.e. the clean gtopt formulation" in flat
    assert "Profile 'cen' applies: --mode replica: the replica mode" in flat
    assert "strict=false: relaxed bounds" in flat
    assert "quirk (internal): an internal quirk" in flat
    assert "--tool-legacy deprecated alias of --legacy-profile cen" in flat


def test_toggle_spelling() -> None:
    assert LegacyToggle("x", True, "n", ("--x",)).spelling() == "--x"
    assert LegacyToggle("x", False, "n", ("--x",)).spelling() == "x=false"
    assert LegacyToggle("x", "v", "n", ("--x",)).spelling() == "--x v"
    assert LegacyToggle("x", 1, "n").spelling() == "x (internal)"
