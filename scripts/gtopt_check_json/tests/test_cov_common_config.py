# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for gtopt_check_json helpers, colours and config."""

from __future__ import annotations

import configparser
import io
import sys
from pathlib import Path

import pytest

from gtopt_check_json import _checks_common as cc
from gtopt_check_json import _colors
from gtopt_check_json import _config as cf


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# _checks_common
# ---------------------------------------------------------------------------


def test_uid_and_name_sets() -> None:
    sys_ = {"x": [{"uid": 1, "name": "a"}, {"name": ""}, {"uid": 1, "name": "a"}]}
    assert dict(cc._get_uid_set(sys_, "x")) == {1: [0, 2]}  # noqa: SLF001
    assert dict(cc._get_name_set(sys_, "x")) == {"a": [0, 2]}  # noqa: SLF001


def test_bus_lookup_and_refs() -> None:
    sys_ = {"bus_array": [{"uid": 1, "name": "b1"}, {"name": "b2"}, {"uid": 3}]}
    uids, names = cc._resolve_bus_lookup(sys_)  # noqa: SLF001
    assert uids == {1, 3}
    assert names == {"b1": 1, "b2": None}
    assert cc._resolve_bus_ref(None, uids, names)  # noqa: SLF001
    assert cc._resolve_bus_ref(3, uids, names)  # noqa: SLF001
    assert cc._resolve_bus_ref("b2", uids, names)  # noqa: SLF001
    assert not cc._resolve_bus_ref("zz", uids, names)  # noqa: SLF001
    assert not cc._resolve_bus_ref(9, uids, names)  # noqa: SLF001


def test_uid_ref_and_maps() -> None:
    uids, names = cc._build_uid_name_maps(  # noqa: SLF001
        {"g": [{"uid": 1, "name": "g1"}, {"name": "g2"}, {"uid": 2}]}, "g"
    )
    assert uids == {1, 2}
    assert names == {"g1": 1, "g2": None}
    assert cc._resolve_uid_ref(None, uids, names)  # noqa: SLF001
    assert cc._resolve_uid_ref(2, uids, names)  # noqa: SLF001
    assert cc._resolve_uid_ref("g2", uids, names)  # noqa: SLF001
    assert not cc._resolve_uid_ref("g3", uids, names)  # noqa: SLF001
    assert not cc._resolve_uid_ref(7, uids, names)  # noqa: SLF001


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        (None, []),
        (3, [3.0]),
        ("f.parquet", []),
        ([1, "x", [2, "y", 3.5]], [1.0, 2.0, 3.5]),
        ({"a": 1}, []),
    ],
)
def test_extract_scalar_values(field: object, expected: list[float]) -> None:
    assert cc._extract_scalar_values(field) == expected  # noqa: SLF001


# ---------------------------------------------------------------------------
# _colors
# ---------------------------------------------------------------------------


def test_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_colors, "USE_COLOR", True)
    assert _colors.c(_colors.BOLD, "t").endswith(_colors.RESET)
    assert "━━━" in _colors.header("Hi")
    monkeypatch.setattr(_colors, "USE_COLOR", False)
    assert _colors.c(_colors.BOLD, "t") == "t"
    assert _colors.header("Hi") == "\n  ======\n   Hi\n  ======"


# ---------------------------------------------------------------------------
# _config
# ---------------------------------------------------------------------------


@pytest.fixture(name="home")
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cf, "_OLD_CONFIG_PATH", tmp_path / "old.conf")
    monkeypatch.setattr(cf, "DEFAULT_CONFIG_PATH", tmp_path / "gtopt.conf")
    return tmp_path


def _read(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return parser


def test_default_config_path(home: Path) -> None:
    assert cf.default_config_path() == home / "gtopt.conf"


class TestMigration:
    """Legacy ``~/.gtopt_check_json.conf`` migration."""

    def test_no_old(self, home: Path) -> None:
        cf._migrate_old_config()  # noqa: SLF001
        assert not (home / "gtopt.conf").exists()

    @pytest.mark.parametrize(
        "content", ["garbage\n", "[other]\na = 1\n", "[gtopt_check_json]\n"]
    )
    def test_nothing_to_migrate(self, home: Path, content: str) -> None:
        (home / "old.conf").write_text(content, encoding="utf-8")
        cf._migrate_old_config()  # noqa: SLF001
        assert not (home / "gtopt.conf").exists()

    def test_via_load_config(self, home: Path) -> None:
        (home / "old.conf").write_text(
            "[gtopt_check_json]\ncheck_uid_uniqueness = false\ncolor = never\n",
            encoding="utf-8",
        )
        cfg = cf.load_config(home / "gtopt.conf")
        assert cfg["check_uid_uniqueness"] == "false"
        assert cfg["color"] == "never"

    def test_only_global(self, home: Path) -> None:
        (home / "old.conf").write_text(
            "[gtopt_check_json]\nai_model = m\n", encoding="utf-8"
        )
        cf._migrate_old_config()  # noqa: SLF001
        parser = _read(home / "gtopt.conf")
        assert parser.get("global", "ai_model") == "m"
        assert not parser.has_section("gtopt_check_json")


def test_save_config_partitions(tmp_path: Path) -> None:
    a = tmp_path / "a.conf"
    cf.save_config(a, {"check_x": "true"})
    assert _read(a).get("gtopt_check_json", "check_x") == "true"
    b = tmp_path / "b.conf"
    cf.save_config(b, {"color": "never"})
    assert not _read(b).has_section("gtopt_check_json")


def test_is_check_enabled() -> None:
    assert cf.is_check_enabled({"check_a": "Yes"}, "a")
    assert not cf.is_check_enabled({"check_a": "0"}, "a")
    assert cf.is_check_enabled({}, "unknown_check")


class TestPrompt:
    """``_prompt`` interaction."""

    def test_not_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO())
        assert cf._prompt("Q", "d") == "d"  # noqa: SLF001

    def test_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", _Tty())
        seen: list[str] = []
        answers = iter([" v ", ""])

        def _input(p: str) -> str:
            seen.append(p)
            return next(answers)

        monkeypatch.setattr("builtins.input", _input)
        assert cf._prompt("Q", "d") == "v"  # noqa: SLF001
        assert cf._prompt("Q") == ""  # noqa: SLF001
        assert seen == ["Q [d]: ", "Q: "]

    @pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
    def test_interrupt(
        self, monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
    ) -> None:
        monkeypatch.setattr(sys, "stdin", _Tty())

        def _raise(_p: str) -> str:
            raise exc

        monkeypatch.setattr("builtins.input", _raise)
        assert cf._prompt("Q", "d") == "d"  # noqa: SLF001


@pytest.mark.parametrize("use_color", [True, False])
def test_wizard_defaults(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    use_color: bool,
) -> None:
    monkeypatch.setattr(cf, "_prompt", lambda _t, d="": d)
    path = home / "w.conf"
    cfg = cf.run_interactive_setup(path, use_color=use_color)
    for check_id, default in cf.CHECK_DEFAULTS.items():
        assert cfg[f"check_{check_id}"] == default
    assert "Configuration saved to" in capsys.readouterr().out
    assert cf.load_config(path)["color"] == cfg["color"]


def test_wizard_invalid_answers(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    n_checks = len(cf.CHECK_DEFAULTS)
    answers = iter(["purple"] + ["maybe"] * n_checks + ["perhaps", "nobody", "mm"])
    monkeypatch.setattr(cf, "_prompt", lambda _t, d="": next(answers))
    cfg = cf.run_interactive_setup(home / "w.conf", use_color=False)
    assert cfg["color"] == "auto"
    for check_id, default in cf.CHECK_DEFAULTS.items():
        assert cfg[f"check_{check_id}"] == default
    assert cfg["ai_enabled"] == "false"
    assert cfg["ai_provider"] == cf._AI_DEFAULT_PROVIDER  # noqa: SLF001
    assert cfg["ai_model"] == "mm"
    assert "Unknown provider 'nobody'" in capsys.readouterr().out
