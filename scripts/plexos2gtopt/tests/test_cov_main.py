# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI dispatch coverage for :mod:`plexos2gtopt.main` / ``__main__``."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt import main as main_module
from plexos2gtopt.main import _parse_cogen_must_run, main
from plexos2gtopt.parsers import UnresolvedConstraintReferenceError
from plexos2gtopt.tests.test_cli_and_loader import _make_dir_bundle


def test_parse_cogen_must_run() -> None:
    assert _parse_cogen_must_run(None) == (frozenset(), False)
    assert _parse_cogen_must_run("A, B,,") == (frozenset({"A", "B"}), False)
    assert _parse_cogen_must_run("all,FOO") == (frozenset(), True)


def _patch_convert(monkeypatch: pytest.MonkeyPatch, fn: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def _wrapped(opts: dict[str, Any]) -> Any:
        seen.append(opts)
        return fn(opts)

    monkeypatch.setattr(main_module, "convert_plexos_bundle", _wrapped)
    return seen


def test_convert_success_writes_snapshot_and_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _make_dir_bundle(tmp_path)
    out = tmp_path / "out"
    seen = _patch_convert(monkeypatch, lambda _o: 0)
    main([str(bundle), "-o", str(out), "--cogen-must-run", "X"])
    state = out / "plexos2gtopt_state.json"
    assert state.is_file()
    assert seen[0]["cogen_must_run"] == (frozenset({"X"}), False)
    # Replay from the snapshot: same options reach the converter.
    main(["--from-state", str(state)])
    assert seen[1]["cogen_must_run"] == (frozenset({"X"}), False)
    assert Path(seen[1]["input_bundle"]) == bundle


def test_from_state_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--from-state", str(tmp_path / "ghost.json")])
    assert exc.value.code == 2


def test_missing_bundle_is_parser_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_snapshot_failure_is_only_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = _make_dir_bundle(tmp_path)

    def _boom(**_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(main_module, "write_state_snapshot", _boom)
    _patch_convert(monkeypatch, lambda _o: None)
    main([str(bundle), "-o", str(tmp_path / "o")])
    assert "failed to write state snapshot" in capsys.readouterr().err


def test_unresolved_reference_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = _make_dir_bundle(tmp_path)

    def _raise(_o: dict[str, Any]) -> int:
        raise UnresolvedConstraintReferenceError("UC1 -> ghost")

    _patch_convert(monkeypatch, _raise)
    with pytest.raises(SystemExit) as exc:
        main([str(bundle), "-o", str(tmp_path / "o")])
    assert exc.value.code == 1
    assert "UC1 -> ghost" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("result", "code"),
    [(0, 0), (None, 0), (2, 1), (FileNotFoundError("gone"), 1)],
)
def test_compare_json_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: Any,
    code: int,
) -> None:
    bundle = _make_dir_bundle(tmp_path)

    def _fake(_o: dict[str, Any]) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(main_module, "compare_plexos_bundle", _fake)
    cmp_json = tmp_path / "case.json"
    cmp_json.write_text("{}")
    with pytest.raises(SystemExit) as exc:
        main([str(bundle), "--compare", str(cmp_json)])
    assert exc.value.code == code


def test_dunder_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(main_module, "main", lambda: called.append(True))
    monkeypatch.setattr(sys, "argv", ["plexos2gtopt"])
    sys.modules.pop("plexos2gtopt.__main__", None)
    runpy.run_module("plexos2gtopt", run_name="__main__")
    assert called == [True]
