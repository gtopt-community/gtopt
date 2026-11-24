# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/lrc_scuc_benchmark.py (tiny synthetic .dat, no solver)."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lrc_scuc_benchmark as lb  # pylint: disable=wrong-import-position

_DAT = """\
# comment line
param: BUS: bus_num bus_Pd bus_Solar bus_Wind:=
1 1 100 1 0
2 2 0 0 2;
param: GEND: genD_bus genD_minUP genD_minDN genD_status genD_Pmax genD_Pmin \
genC_Startup genC_Cost genC_NLoad SPRamp NSRamp HRamp StartRamp gen_Style \
inertia_constant resC:=
1 1 1 1 1 50 10 75 20 1000 20 20 20 20 0 4 10
2 2 8 4 1 400 50 2400 5 215 200 0 400 400 0 3.25 6;
param: BRANCH: branch_fbus branch_tbus branch_b branch_rateA branch_rateC \
branch_radial:=
1 1 2 -10 150 180 0;
param: PERIOD: load_pcnt Solar_pcnt Wind_pcnt:=
1 80 0 50   # trailing comment
2 120 10 25;
"""


def _tables() -> dict:
    return lb.parse_dat(_DAT.replace("\\\n", ""))


def test_parse_dat() -> None:
    t = _tables()
    assert sorted(t) == ["BRANCH", "BUS", "GEND", "PERIOD"]
    assert t["GEND"][2]["genD_Pmax"] == 400.0
    assert t["GEND"][1]["inertia_constant"] == 4.0
    assert t["PERIOD"][1] == {"load_pcnt": 80.0, "Solar_pcnt": 0.0, "Wind_pcnt": 50.0}


def test_parse_dat_errors() -> None:
    with pytest.raises(ValueError, match="width"):
        lb.parse_dat("param: BUS: a b := 1 2 3 4;")
    with pytest.raises(ValueError, match="missing table GEND"):
        lb.parse_dat("param: BUS: a := 1 2;")


def test_erc_expression() -> None:
    gens = _tables()["GEND"]
    assert lb.erc_expression(gens, 1) == (
        '1300 * commitment("uc2").status - 60 * generator("g1").generation >= 0'
    )


def test_to_gtopt_t() -> None:
    case = lb.to_gtopt(_tables(), "T")
    system = case["system"]
    assert "user_constraint_array" not in system
    assert system["demand_array"] == [
        {"uid": 1, "name": "d1", "bus": 1, "lmax": [[80.0, 120.0]]}
    ]
    renew = {g["name"]: g for g in system["generator_array"] if g["uid"] > 1000}
    assert renew["solar1"]["pmax"] == [[0.0, 40.0]]  # 4 x 1 x 10 % x 100
    assert renew["wind2"]["pmin"] == [[96.0, 48.0]]  # 0.96 x 2 x 50 % x 100
    uc = system["commitment_array"][1]
    assert (uc["pmin"], uc["startup_cost"], uc["ramp_up"]) == (50.0, 2400.0, 400.0)
    # the committed unit's floor is on the Commitment, not the Generator
    gen = {g["name"]: g for g in system["generator_array"]}[uc["generator"]]
    assert gen["pmin"] == 0
    assert uc["initial_status"] == 0
    assert system["line_array"][0]["reactance"] == pytest.approx(0.1)
    zone = system["reserve_zone_array"][0]
    assert zone["contingency_includes_reserve"] is True
    assert zone["contingency_generators"] == ["g1", "g2"]
    assert system["reserve_provision_array"][0]["urcost"] == 10.0
    assert case["simulation"]["stage_array"][0]["chronological"] is True


def test_to_gtopt_erc_and_bad_variant() -> None:
    rows = lb.to_gtopt(_tables(), "ERC")["system"]["user_constraint_array"]
    assert [r["name"] for r in rows] == ["erc_rocof_g1", "erc_rocof_g2"]
    with pytest.raises(ValueError, match="unknown variant"):
        lb.to_gtopt(_tables(), "LRC")


def test_fetch_downloads_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake_urlopen(url: str) -> contextlib.nullcontext[io.BytesIO]:
        urls.append(url)
        return contextlib.nullcontext(io.BytesIO(b"data"))

    monkeypatch.setattr(lb.urllib.request, "urlopen", fake_urlopen)
    first = lb.fetch(tmp_path / "c")
    second = lb.fetch(tmp_path / "c")
    assert first == second
    assert first.read_bytes() == b"data"
    assert urls == [lb.DATA_URL]
    assert lb.PINNED_SHA in lb.DATA_URL


def _write_dat(tmp_path: Path) -> Path:
    path = tmp_path / "d.dat"
    path.write_text(_DAT.replace("\\\n", ""), encoding="utf-8")
    return path


def test_main_convert_only(tmp_path: Path) -> None:
    out = tmp_path / "case.json"
    assert lb.main(["--variant", "T", "--data", str(_write_dat(tmp_path)),
                    "--out", str(out)]) == 0  # fmt: skip
    assert json.loads(out.read_text(encoding="utf-8"))["system"]["name"] == (
        "lrc_scuc_t"
    )


def _fake_gtopt(status: str, obj: float):
    def fake_run(cmd: list[str], check: bool, cwd: Path) -> None:
        del check, cwd
        out = Path(cmd[cmd.index("-d") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "solution.csv").write_text(
            f"scene,status_name,obj_value\n0,{status},{obj}\n", encoding="utf-8"
        )

    return fake_run


@pytest.mark.parametrize(
    ("status", "obj", "rc"),
    [
        ("optimal", lb.CODE_TOTAL["ERC"], 0),
        ("optimal", 1.01 * lb.CODE_TOTAL["ERC"], 1),
        ("infeasible", 0.0, 1),
    ],
)
def test_main_solve_and_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, obj: float, rc: int
) -> None:
    monkeypatch.setattr(lb.subprocess, "run", _fake_gtopt(status, obj))
    args = ["--data", str(_write_dat(tmp_path)), "--gtopt", "gt"]
    assert lb.main([*args, "--work-dir", str(tmp_path / "w"),
                    "--solver", "cplex"]) == rc  # fmt: skip
    assert (tmp_path / "w" / "lrc_scuc_erc.json").exists()


def test_main_run_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(cmd: list[str], check: bool, cwd: Path) -> None:
        del check, cwd
        raise subprocess.CalledProcessError(3, cmd)

    monkeypatch.setattr(lb.subprocess, "run", failing)
    args = ["--data", str(_write_dat(tmp_path)), "--gtopt", "gt"]
    assert lb.main([*args, "--work-dir", str(tmp_path)]) == 2
    assert lb.main(["--data", str(tmp_path / "missing.dat")]) == 2


def test_main_fetches_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lb, "fetch", lambda _d: _write_dat(tmp_path))
    assert lb.main(["--cache-dir", str(tmp_path / "c")]) == 0
