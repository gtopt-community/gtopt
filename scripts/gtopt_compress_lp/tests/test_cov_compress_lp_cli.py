# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for gtopt_compress_lp: config, wizard, runner and CLI.

All external compressors are mocked (``subprocess.run`` / ``shutil.which``),
so these tests do not depend on which tools are installed on the host.
"""

from __future__ import annotations

import configparser
import io
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gtopt_compress_lp import _colors
from gtopt_compress_lp import _config as cfgmod
from gtopt_compress_lp import gtopt_compress_lp as cli

_MOD = "gtopt_compress_lp.gtopt_compress_lp"


def _lp(tmp_path: Path, name: str = "m.lp") -> Path:
    p = tmp_path / name
    p.write_text("Minimize\n obj: x\nEnd\n", encoding="utf-8")
    return p


class _Tty(io.StringIO):
    """A stdin stand-in that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(name="isolated_home")
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the legacy and unified config paths into *tmp_path*."""
    old = tmp_path / "old.conf"
    new = tmp_path / "gtopt.conf"
    monkeypatch.setattr(cfgmod, "_OLD_CONFIG_PATH", old)
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", new)
    return tmp_path


def _which_only(*names: str) -> Any:
    def _which(binary: str) -> str | None:
        return f"/usr/bin/{binary}" if binary in names else None

    return _which


# ---------------------------------------------------------------------------
# _colors
# ---------------------------------------------------------------------------


def test_colors_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_colors, "USE_COLOR", True)
    assert _colors.c(_colors.RED, "x") == f"{_colors.RED}x{_colors.RESET}"
    monkeypatch.setattr(_colors, "USE_COLOR", False)
    assert _colors.c(_colors.RED, "x") == "x"
    hdr = _colors.header("Title")
    assert "│  Title  │" in hdr
    assert hdr.count("\n") == 3


# ---------------------------------------------------------------------------
# Legacy-config migration
# ---------------------------------------------------------------------------


class TestMigration:
    """``_migrate_old_config`` and the migration hook in ``load_config``."""

    def test_no_old_file(self, isolated_home: Path) -> None:
        cfgmod._migrate_old_config()  # noqa: SLF001
        assert not (isolated_home / "gtopt.conf").exists()

    def test_unparseable_old_file(self, isolated_home: Path) -> None:
        (isolated_home / "old.conf").write_text("no section header\n", "utf-8")
        cfgmod._migrate_old_config()  # noqa: SLF001
        assert not (isolated_home / "gtopt.conf").exists()

    def test_old_file_without_section(self, isolated_home: Path) -> None:
        (isolated_home / "old.conf").write_text("[other]\na = 1\n", "utf-8")
        cfgmod._migrate_old_config()  # noqa: SLF001
        assert not (isolated_home / "gtopt.conf").exists()

    def test_empty_section(self, isolated_home: Path) -> None:
        (isolated_home / "old.conf").write_text("[gtopt_compress_lp]\n", "utf-8")
        cfgmod._migrate_old_config()  # noqa: SLF001
        assert not (isolated_home / "gtopt.conf").exists()

    def test_migrates_tool_and_global_keys(self, isolated_home: Path) -> None:
        (isolated_home / "old.conf").write_text(
            "[gtopt_compress_lp]\ncompressor = xz\ncolor = never\n", "utf-8"
        )
        new = isolated_home / "gtopt.conf"
        cfg = cfgmod.load_config(new)
        assert cfg["compressor"] == "xz"
        assert cfg["color"] == "never"
        parser = configparser.ConfigParser()
        parser.read(new, encoding="utf-8")
        assert parser.get("global", "color") == "never"

    def test_only_global_key(self, isolated_home: Path) -> None:
        (isolated_home / "old.conf").write_text(
            "[gtopt_compress_lp]\ncolor = always\n", "utf-8"
        )
        cfgmod._migrate_old_config()  # noqa: SLF001
        parser = configparser.ConfigParser()
        parser.read(isolated_home / "gtopt.conf", encoding="utf-8")
        assert not parser.has_section("gtopt_compress_lp")
        assert parser.get("global", "color") == "always"

    def test_no_migration_when_new_exists(self, isolated_home: Path) -> None:
        (isolated_home / "old.conf").write_text(
            "[gtopt_compress_lp]\ncompressor = xz\n", "utf-8"
        )
        new = isolated_home / "gtopt.conf"
        new.write_text("[gtopt_compress_lp]\ncompressor = lz4\n", "utf-8")
        assert cfgmod.load_config(new)["compressor"] == "lz4"


def test_save_config_only_tool_keys(tmp_path: Path) -> None:
    path = tmp_path / "c.conf"
    cfgmod.save_config(path, {"compressor": "gzip"})
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    assert parser.get("gtopt_compress_lp", "compressor") == "gzip"
    assert not parser.has_option("global", "compressor")


def test_save_config_only_global_keys(tmp_path: Path) -> None:
    path = tmp_path / "c.conf"
    cfgmod.save_config(path, {"color": "never"})
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    assert parser.get("global", "color") == "never"
    assert not parser.has_section("gtopt_compress_lp")


# ---------------------------------------------------------------------------
# Compressor availability
# ---------------------------------------------------------------------------


def test_pick_auto_compressor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfgmod.shutil, "which", _which_only("gzip", "xz"))
    assert cfgmod.pick_auto_compressor() == "gzip"
    monkeypatch.setattr(cfgmod.shutil, "which", _which_only())
    assert cfgmod.pick_auto_compressor() is None


@pytest.mark.parametrize("use_color", [True, False])
def test_print_compressor_status_mixed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    use_color: bool,
) -> None:
    monkeypatch.setattr(cfgmod.shutil, "which", _which_only("zstd"))
    monkeypatch.setattr(
        cfgmod,
        "_COMPRESSOR_CATALOGUE",
        [*cfgmod._COMPRESSOR_CATALOGUE, ("nohint", "No hint", "slow", None, "n")],  # noqa: SLF001
    )
    cfgmod.print_compressor_status(use_color=use_color)
    out = capsys.readouterr().out
    assert "(not found on PATH)" in out
    assert "To install missing tools:" in out
    assert "sudo apt install lz4" in out
    assert "# No hint" not in out
    assert ("\033[" in out) is use_color


def test_print_compressor_status_all_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cfgmod.shutil, "which", lambda b: f"/bin/{b}")
    cfgmod.print_compressor_status(use_color=False)
    out = capsys.readouterr().out
    assert "not found" not in out
    assert "To install" not in out
    assert "1st choice: zstd" in out


# ---------------------------------------------------------------------------
# _prompt and the interactive wizard
# ---------------------------------------------------------------------------


class TestPrompt:
    """``_prompt`` on tty / non-tty stdin."""

    def test_not_a_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert cfgmod._prompt("Q", "d") == "d"  # noqa: SLF001

    def test_answer_and_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", _Tty())
        prompts: list[str] = []
        answers = iter([" val ", ""])

        def _input(p: str) -> str:
            prompts.append(p)
            return next(answers)

        monkeypatch.setattr("builtins.input", _input)
        assert cfgmod._prompt("Q", "d") == "val"  # noqa: SLF001
        assert cfgmod._prompt("Q") == ""  # noqa: SLF001
        assert prompts == ["Q [d]: ", "Q: "]

    @pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
    def test_interrupt(
        self, monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
    ) -> None:
        monkeypatch.setattr(sys, "stdin", _Tty())

        def _raise(_p: str) -> str:
            raise exc

        monkeypatch.setattr("builtins.input", _raise)
        assert cfgmod._prompt("Q", "d") == "d"  # noqa: SLF001


class TestWizard:
    """``run_interactive_setup`` with scripted answers."""

    def _run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        path: Path,
        answers: list[str],
        installed: tuple[str, ...],
        use_color: bool = False,
    ) -> dict[str, str]:
        it = iter(answers)
        monkeypatch.setattr(cfgmod, "_prompt", lambda _t, d="": next(it) or d)
        monkeypatch.setattr(cfgmod.shutil, "which", _which_only(*installed))
        return cfgmod.run_interactive_setup(path, use_color=use_color)

    def test_defaults_with_auto_tool(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = isolated_home / "w.conf"
        cfg = self._run(monkeypatch, path, ["", "", ""], ("lz4",), use_color=True)
        assert cfg == {"color": "auto", "compressor": "lz4", "extra_args": ""}
        out = capsys.readouterr().out
        assert "Best available tool" in out
        assert "Configuration saved to" in out
        assert cfgmod.load_config(path)["compressor"] == "lz4"

    def test_nothing_installed_and_bad_answers(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = isolated_home / "w.conf"
        cfg = self._run(monkeypatch, path, ["__nope__", "-9", "rainbow"], ())
        assert cfg["compressor"] == "auto"
        assert cfg["extra_args"] == "-9"
        assert cfg["color"] == "auto"
        captured = capsys.readouterr()
        assert "Best available tool" not in captured.out
        assert "falling back to 'auto'" in captured.err

    def test_keeps_configured_compressor(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = isolated_home / "w.conf"
        cfgmod.save_config(path, {"compressor": "/opt/mygz", "color": "never"})
        seen: list[str] = []

        def _prompt(_t: str, d: str = "") -> str:
            seen.append(d)
            return d

        monkeypatch.setattr(cfgmod, "_prompt", _prompt)
        monkeypatch.setattr(cfgmod.shutil, "which", _which_only("/opt/mygz"))
        cfg = cfgmod.run_interactive_setup(path, use_color=False)
        assert seen[0] == "/opt/mygz"
        assert cfg["compressor"] == "/opt/mygz"
        assert cfg["color"] == "never"


# ---------------------------------------------------------------------------
# _run_compressor with a mocked subprocess
# ---------------------------------------------------------------------------


class TestRunCompressor:
    """Every outcome of a single compressor invocation."""

    def _patch_run(
        self, monkeypatch: pytest.MonkeyPatch, effect: Any
    ) -> list[list[str]]:
        calls: list[list[str]] = []

        def _run(cmd: list[str], **_kw: Any) -> Any:
            calls.append(cmd)
            if isinstance(effect, BaseException):
                raise effect
            return effect(cmd)

        monkeypatch.setattr(cli.subprocess, "run", _run)
        return calls

    def test_success_output_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lp = _lp(tmp_path)

        def _ok(cmd: list[str]) -> Any:
            Path(cmd[-1]).write_bytes(b"z")  # lz4 explicit output path
            return SimpleNamespace(returncode=0, stderr=b"")

        calls = self._patch_run(monkeypatch, _ok)
        out = cli._run_compressor("lz4", ["-9"], lp, quiet=True)  # noqa: SLF001
        assert out == Path(str(lp) + ".lz4")
        assert calls[0] == ["lz4", "-f", "--rm", "-9", str(lp), str(out)]

    def test_success_original_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lp = _lp(tmp_path)

        def _ok(_cmd: list[str]) -> Any:
            lp.unlink()
            return SimpleNamespace(returncode=0, stderr=b"")

        calls = self._patch_run(monkeypatch, _ok)
        out = cli._run_compressor("mytool", [], lp, quiet=True)  # noqa: SLF001
        assert out == Path(str(lp) + ".gz")
        assert calls[0] == ["mytool", "-f", str(lp)]

    def test_success_but_nothing_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lp = _lp(tmp_path)
        self._patch_run(
            monkeypatch, lambda _c: SimpleNamespace(returncode=0, stderr=b"")
        )
        assert cli._run_compressor("gzip", [], lp, quiet=True) is None  # noqa: SLF001

    @pytest.mark.parametrize("stderr", [b"bad \xff input", b""])
    def test_nonzero_exit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        stderr: bytes,
    ) -> None:
        lp = _lp(tmp_path)
        self._patch_run(
            monkeypatch, lambda _c: SimpleNamespace(returncode=2, stderr=stderr)
        )
        assert cli._run_compressor("zstd", [], lp, quiet=False) is None  # noqa: SLF001
        err = capsys.readouterr().err
        assert "zstd: returned exit code 2" in err
        assert ("bad" in err) is bool(stderr)
        # quiet suppresses the diagnostics
        assert cli._run_compressor("zstd", [], lp, quiet=True) is None  # noqa: SLF001
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize(
        ("exc", "msg"),
        [
            (FileNotFoundError(), "not found on PATH"),
            (subprocess.TimeoutExpired("x", 1), "timed out after"),
            (PermissionError("denied"), "error: denied"),
        ],
    )
    def test_exceptions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        exc: BaseException,
        msg: str,
    ) -> None:
        lp = _lp(tmp_path)
        self._patch_run(monkeypatch, exc)
        assert cli._run_compressor("xz", [], lp, quiet=False) is None  # noqa: SLF001
        assert msg in capsys.readouterr().err
        assert cli._run_compressor("xz", [], lp, quiet=True) is None  # noqa: SLF001
        assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# compress_lp cascade
# ---------------------------------------------------------------------------


class TestCompressLp:
    """Candidate ordering and messaging of ``compress_lp``."""

    def _record(
        self, monkeypatch: pytest.MonkeyPatch, succeed: set[str], auto: str | None
    ) -> list[str]:
        tried: list[str] = []

        def _fake(binary: str, _x: list[str], lp: Path, _q: bool) -> Path | None:
            tried.append(binary)
            return Path(str(lp) + ".out") if binary in succeed else None

        monkeypatch.setattr(cli, "_run_compressor", _fake)
        monkeypatch.setattr(cli, "pick_auto_compressor", lambda: auto)
        return tried

    def test_missing_file_verbose(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.compress_lp(tmp_path / "nope.lp", {}) is None
        assert "file not found" in capsys.readouterr().err

    def test_codec_hint_first(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        lp = _lp(tmp_path)
        tried = self._record(monkeypatch, {"zstd"}, "zstd")
        res = cli.compress_lp(lp, {"compressor": "xz"}, codec_hint="lz4")
        assert res == Path(str(lp) + ".out")
        assert tried == ["lz4", "xz", "zstd"]
        assert "compressed m.lp" in capsys.readouterr().out

    def test_unknown_codec_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        lp = _lp(tmp_path)
        tried = self._record(monkeypatch, set(), "gzip")
        assert cli.compress_lp(lp, {"compressor": "gzip"}, codec_hint="brotli") is None
        assert tried == ["gzip"]  # configured == auto: tried once
        err = capsys.readouterr().err
        assert "unknown codec 'brotli'" in err
        assert "no compressor available" in err

    def test_unknown_codec_quiet_and_dedup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        lp = _lp(tmp_path)
        tried = self._record(monkeypatch, {"zstd"}, None)
        res = cli.compress_lp(lp, {"compressor": "zstd"}, quiet=True, codec_hint="zstd")
        assert res is not None
        assert tried == ["zstd"]
        tried.clear()
        cli.compress_lp(lp, {"compressor": "auto"}, quiet=True, codec_hint="brotli")
        assert not tried
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("hint", ["none", "auto", ""])
    def test_ignored_hints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hint: str
    ) -> None:
        lp = _lp(tmp_path)
        tried = self._record(monkeypatch, set(), "gzip")
        cli.compress_lp(lp, {"compressor": ""}, quiet=True, codec_hint=hint)
        assert tried == ["gzip"]


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """CLI dispatch in ``main``."""

    def test_show_config(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conf = tmp_path / "c.conf"
        cfgmod.save_config(conf, {"compressor": "xz", "color": "never"})
        assert cli.main(["--show-config", "--config", str(conf)]) == 0
        assert "compressor = xz" in capsys.readouterr().out
        args = ["--show-config", "--config", str(conf), "--compressor", "lz4"]
        assert cli.main(args) == 0
        assert "compressor = lz4" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [(["--color", "always"], True), (["--color", "never"], False), ([], False)],
    )
    def test_color_modes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        flags: list[str],
        expected: bool,
    ) -> None:
        seen: list[bool] = []

        def _status(use_color: bool) -> None:
            seen.append(use_color)

        monkeypatch.setattr(cli, "print_compressor_status", _status)
        monkeypatch.setattr(sys, "stdout", io.StringIO())  # not a tty
        assert cli.main(["--list-tools", *flags]) == 0
        assert seen == [expected]
        assert _colors.USE_COLOR is expected
        capsys.readouterr()

    def test_first_run_no_tty_prints_hint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        conf = tmp_path / "missing.conf"
        assert cli.main(["--no-color", "--config", str(conf)]) == 0
        captured = capsys.readouterr()
        assert "No configuration file found." in captured.out
        assert "--init-config" in captured.err
        assert "usage:" in captured.out  # help shown when no files

    @pytest.mark.parametrize("accept", [True, False])
    def test_first_run_tty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        accept: bool,
    ) -> None:
        monkeypatch.setattr(sys, "stdin", _Tty())
        monkeypatch.setattr(cli, "_prompt_first_run", lambda _c: accept)
        wizard: list[Path] = []

        def _setup(path: Path, use_color: bool) -> dict[str, str]:
            assert use_color is False
            wizard.append(path)
            return {"compressor": "gzip", "extra_args": "", "color": "never"}

        monkeypatch.setattr(cli, "run_interactive_setup", _setup)
        lp = _lp(tmp_path)
        used: list[str] = []

        def _compress(
            p: Path, cfg: dict[str, str], quiet: bool, codec_hint: str | None
        ) -> Path:
            assert not quiet
            assert codec_hint is None
            used.append(cfg["compressor"])
            return p

        monkeypatch.setattr(cli, "compress_lp", _compress)
        conf = tmp_path / "missing.conf"
        args = ["--no-color", "--compressor", "xz", "--config", str(conf), str(lp)]
        assert cli.main(args) == 0
        assert len(wizard) == int(accept)
        assert used == ["gzip" if accept else "xz"]
        capsys.readouterr()

    def test_failure_exit_codes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        conf = tmp_path / "c.conf"
        cfgmod.save_config(conf, {"compressor": "gzip"})
        monkeypatch.setattr(cli, "compress_lp", lambda *_a, **_k: None)
        lp = _lp(tmp_path)
        already = tmp_path / "x.lp.gz"
        already.write_bytes(b"")
        ghost = tmp_path / "ghost.lp"
        base = ["--no-color", "--config", str(conf)]
        # Already-compressed and missing files are not failures.
        assert cli.main([*base, str(already), str(ghost)]) == 0
        # A present, uncompressed file that could not be compressed fails ...
        assert cli.main([*base, str(lp)]) == 1
        # ... except in quiet mode.
        assert cli.main([*base, "--quiet", str(lp)]) == 0
        # quiet + no files: no help printed.
        assert cli.main([*base, "--quiet"]) == 0
        assert "usage:" not in capsys.readouterr().out

    def test_dunder_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        conf = tmp_path / "c.conf"
        monkeypatch.setattr(
            sys, "argv", ["gtopt_compress_lp", "--show-config", "--config", str(conf)]
        )
        monkeypatch.delitem(sys.modules, _MOD, raising=False)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module(_MOD, run_name="__main__")
        assert exc.value.code == 0


class TestPromptFirstRun:
    """``_prompt_first_run`` answers."""

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [("", True), ("Y", True), ("yes", True), ("n", False)],
    )
    def test_answers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        answer: str,
        expected: bool,
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _p: answer)
        assert cli._prompt_first_run(use_color=True) is expected  # noqa: SLF001
        assert "needs to be configured" in capsys.readouterr().out

    @pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
    def test_interrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        exc: type[BaseException],
    ) -> None:
        def _raise(_p: str) -> str:
            raise exc

        monkeypatch.setattr("builtins.input", _raise)
        assert cli._prompt_first_run(use_color=False) is False  # noqa: SLF001
        capsys.readouterr()
