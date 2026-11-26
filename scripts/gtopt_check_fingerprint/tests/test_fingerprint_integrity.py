# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""The fingerprint check must be real: no empty / trusted hashes, gtopt
JSON goldens comparable with LP files, columns found outside ``Bounds``."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gtopt_check_fingerprint._cli import main
from gtopt_check_fingerprint._fingerprint import (
    FingerprintEntry,
    FingerprintError,
    LpFingerprint,
    _parse_lp_text,
    _rehash,
    load_fingerprint_json,
    lp_comparable,
    write_fingerprint_json,
)
from gtopt_shared.gtopt_binary import find_gtopt_binary

#: A gtopt-style fingerprint (PascalCase classes, C++ context names) of
#: the LP below.
_GOLDEN = LpFingerprint(
    col_template=sorted(
        [
            FingerprintEntry("Generator", "generation", "BlockContext"),
            FingerprintEntry("Demand", "load", "BlockContext"),
            FingerprintEntry("Reservoir", "efin", "StageContext"),
        ]
    ),
    row_template=sorted(
        [
            FingerprintEntry("Bus", "balance", "BlockContext"),
            FingerprintEntry("Sddp", "alpha", "ScenePhaseContext"),
        ]
    ),
)

#: Default-bounded columns appear only in the objective / constraints.
_LP = """\\ written by a solver
min
 obj: +0.02 generator_generation_1_1_1_1 + 1e+05 demand_load_1_1_1_1
st
 bus_balance_1_1_1_1: +1 generator_generation_1_1_1_1 -1 demand_load_1_1_1_1
   + reservoir_efin_7_1_1 = +0
 sddp_alpha_1_1_1: +1 reservoir_efin_7_1_1 >= -1e+30
bounds
 generator_generation_1_1_1_1 <= 300
end
"""


def _golden(tmp_path: Path) -> Path:
    path = tmp_path / "golden.json"
    write_fingerprint_json(_rehash(_GOLDEN), path)
    return path


def _lp(tmp_path: Path, text: str = _LP) -> Path:
    path = tmp_path / "model.lp"
    path.write_text(text, encoding="utf-8")
    return path


def _verify(tmp_path: Path, lp: Path, golden: Path) -> int:
    return main(["verify", "--lp-file", str(lp), "--golden", str(golden)])


def test_verify_gtopt_golden_against_lp(tmp_path: Path, capsys) -> None:
    """Class case and context names of a gtopt golden match LP labels."""
    assert _verify(tmp_path, _lp(tmp_path), _golden(tmp_path)) == 0
    assert "PASS" in capsys.readouterr().out


def test_verify_detects_injected_row_and_column(tmp_path: Path, capsys) -> None:
    injected = _LP.replace(
        "bounds\n",
        " bus_injected_1_1_1_1: +1 battery_charge_3_1_1_1 <= 5\nbounds\n",
    )
    assert _verify(tmp_path, _lp(tmp_path, injected), _golden(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "+ bus.injected" in out
    assert "+ battery.charge" in out


def test_verify_detects_column_missing_from_bounds(tmp_path: Path, capsys) -> None:
    """A removed default-bounded column (never in Bounds) is detected."""
    removed = _LP.replace(" + 1e+05 demand_load_1_1_1_1", "").replace(
        " -1 demand_load_1_1_1_1", ""
    )
    assert _verify(tmp_path, _lp(tmp_path, removed), _golden(tmp_path)) == 1
    assert "- demand.load" in capsys.readouterr().out


def test_verify_rejects_unlabelled_lp(tmp_path: Path, capsys) -> None:
    lp = _lp(tmp_path, "min\n obj: x1\nst\n R1: x1 >= 1\nend\n")
    assert _verify(tmp_path, lp, _golden(tmp_path)) == 2
    assert "no gtopt-labelled" in capsys.readouterr().err


def _mutate(path: Path, fn) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_rejects_empty_hash(tmp_path: Path) -> None:
    """Regression: gtopt wrote ``"hash": ""`` and compare said PASS."""
    path = _golden(tmp_path)
    _mutate(path, lambda d: d["structural"].update(hash=""))
    with pytest.raises(FingerprintError, match="empty fingerprint hash"):
        load_fingerprint_json(path)


def test_load_rejects_empty_template(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    write_fingerprint_json(_rehash(LpFingerprint()), path)
    with pytest.raises(FingerprintError, match="empty fingerprint template"):
        load_fingerprint_json(path)


def test_load_recomputes_hash_from_template(tmp_path: Path) -> None:
    """An edited template with the old hash is caught, not trusted."""
    path = _golden(tmp_path)

    def drop_row(d: dict) -> None:
        d["structural"]["rows"]["template"].pop()

    _mutate(path, drop_row)
    with pytest.raises(FingerprintError, match="do not match"):
        load_fingerprint_json(path)


def test_compare_empty_hashes_is_an_error(tmp_path: Path, capsys) -> None:
    a = _golden(tmp_path)
    _mutate(a, lambda d: d["structural"].update(hash=""))
    assert main(["compare", "--actual", str(a), "--expected", str(a)]) == 2
    assert "empty fingerprint hash" in capsys.readouterr().err


_REPO = Path(__file__).resolve().parents[3]
_CASE = _REPO / "cases" / "ieee_4b_ori" / "ieee_4b_ori.json"


@pytest.mark.skipif(
    find_gtopt_binary() is None or not _CASE.is_file(),
    reason="gtopt binary or the ieee_4b_ori case not available",
)
def test_gtopt_run_writes_real_fingerprint(tmp_path: Path) -> None:
    """End to end: ``--set lp_fingerprint=true`` writes a non-empty hash
    that verifies against the run's own LP file, and an edited LP fails."""
    binary = find_gtopt_binary()
    assert binary is not None
    out = tmp_path / "out"
    env = dict(os.environ)
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'ulimit -c unlimited; exec "$@"',
            "gtopt",
            binary,
            str(_CASE),
            "--set",
            f"output_directory={out}",
            "--set",
            "lp_fingerprint=true",
            "--lp-file",
            str(tmp_path / "model"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    fp_files = sorted(out.glob("lp_fingerprint_*.json"))
    assert fp_files, sorted(p.name for p in out.iterdir())
    data = json.loads(fp_files[0].read_text(encoding="utf-8"))
    assert len(data["structural"]["hash"]) == 64
    load_fingerprint_json(fp_files[0])  # consistent

    lp = tmp_path / "model.lp"
    if not lp.is_file():
        pytest.skip("this gtopt build wrote no plain model.lp")
    assert _verify(tmp_path, lp, fp_files[0]) == 0

    edited = tmp_path / "edited.lp"
    shutil.copy(lp, edited)
    text = edited.read_text(encoding="utf-8")
    marker = "\nbounds" if "\nbounds" in text else "\nBounds"
    head, sep, tail = text.partition(marker)
    edited.write_text(
        head + "\n injected_row_1_1_1_1: +1 bus_theta_9_1_1_1 >= 0" + sep + tail,
        encoding="utf-8",
    )
    assert _verify(tmp_path, edited, fp_files[0]) == 1


def test_lp_comparable_projection() -> None:
    """Case-folded classes, context arities; arity-0 entries dropped."""
    fp = LpFingerprint(
        col_template=[
            FingerprintEntry("Generator", "generation", "BlockContext"),
            FingerprintEntry("generator", "generation", "PhaseContext"),
            FingerprintEntry("x", "y", "Context1"),
            FingerprintEntry("z", "w", "monostate"),
        ]
    )
    proj = lp_comparable(fp)
    assert proj.col_template == [
        FingerprintEntry("generator", "generation", "arity:3"),
        FingerprintEntry("x", "y", "arity:1"),
    ]
    assert len(proj.structural_hash) == 64


def test_parse_ignores_text_before_first_section() -> None:
    cols, rows = _parse_lp_text("stray_text_1_1 here\nmin\n obj: a_b_1_1\nend\n")
    assert cols == ["a_b_1_1"]
    assert not rows
