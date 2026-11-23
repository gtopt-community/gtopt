"""Branch coverage for the sddp2gtopt CLI, ``--info`` and convert front-ends."""

from __future__ import annotations

import json
import runpy
import shutil
import sys
from pathlib import Path

import pytest

from sddp2gtopt import main as main_mod
from sddp2gtopt.info_display import display_sddp_info
from sddp2gtopt.sddp2gtopt import convert_sddp_case, validate_sddp_case
from sddp2gtopt.tests.test_cov_dat_loader import _add_optional_inputs, _base_case
from sddp2gtopt.tests.test_dat_parsers import _fw_chidro_line

BLOCK_CASE = Path(__file__).parent / "data" / "sddp_block_case"


def _storage_case(root: Path) -> Path:
    header = (
        " NUM ...Nombre... .PV. .VAA .TAA #Uni Tipo ....Pot .FPMed. "
        ".QMin.. .QMax.. .VMin.. .VMax.. .VInic.\n"
    )
    rows = header + _fw_chidro_line(
        201, "HYDRO1", 202, 60.0, 1.5, 15.0, 90.0, 340.0, 200.0
    )
    case = _base_case(root, hydro_rows=rows)
    (case / "watervcp.csv").write_text(
        "Unit,k$/hm3\nStag,Seq.,Blck,HYDRO1\n   1,   1,   1,  50.0\n",
        encoding="latin-1",
    )
    return case


def test_info_dat_case_multibus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _base_case(tmp_path / "case")
    _add_optional_inputs(root)
    display_sddp_info({"input_dir": root})
    out = capsys.readouterr().out
    assert "multi-bus" in out
    assert "demand nodes" in out


def test_info_dat_case_single_bus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "case"
    root.mkdir()
    (root / "sddp.dat").write_text("x\nETAPA 1\n", encoding="latin-1")
    display_sddp_info({"input_dir": root})
    out = capsys.readouterr().out
    assert "single-bus" in out
    assert "demand blocks      : 0" in out


def test_info_psrclasses_sparse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "psrclasses.json").write_text(
        json.dumps(
            {
                "PSRSystem": [{"code": 1, "name": "S", "UnM": None}],
                "PSRThermalPlant": [
                    {
                        "code": 7,
                        "name": "T",
                        "GerMin": [],
                        "GerMax": [1.5, 2.0],
                        "CTransp": [3],
                    }
                ],
                "PSRGaugingStation": [{"code": 1, "name": "G", "Vazao": [1, 2]}],
            }
        ),
        encoding="utf-8",
    )
    display_sddp_info({"input_dir": tmp_path})
    out = capsys.readouterr().out
    assert "Study: (no PSRStudy entity)" in out
    assert "[2 items]" in out
    assert "(none)" in out  # no hydro plants
    assert "Gauging stations:" in out

    data = json.loads((tmp_path / "psrclasses.json").read_text(encoding="utf-8"))
    data["PSRStudy"] = [{"Ano_inicial": [2030], "DeficitCost": []}]
    (tmp_path / "psrclasses.json").write_text(json.dumps(data), encoding="utf-8")
    display_sddp_info({"input_dir": tmp_path})
    out = capsys.readouterr().out
    assert "2030" in out


def test_validate_block_case(tmp_path: Path) -> None:
    assert validate_sddp_case({"input_dir": BLOCK_CASE})
    bad = tmp_path / "bad"
    shutil.copytree(BLOCK_CASE, bad)
    (bad / "deme02tt.dat").write_text("\n", encoding="latin-1")
    assert not validate_sddp_case({"input_dir": bad})


def test_validate_and_convert_dat_with_boundary_cut(tmp_path: Path) -> None:
    root = _storage_case(tmp_path / "case")
    assert validate_sddp_case({"input_dir": root})
    out = tmp_path / "out"
    assert convert_sddp_case({"input_dir": root, "output_dir": out}) == 0
    data = json.loads((out / "out.json").read_text(encoding="utf-8"))
    cuts = Path(data["system"]["future_cost_array"][0]["cuts_file"])
    assert cuts.is_absolute() and cuts.is_file()


def test_convert_psrclasses_without_systems(tmp_path: Path) -> None:
    (tmp_path / "psrclasses.json").write_text(
        json.dumps({"PSRStudy": [{"Ano_inicial": 2030}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no PSRSystem"):
        convert_sddp_case({"input_dir": tmp_path, "output_dir": tmp_path / "o"})


def test_main_positional_convert(tmp_path: Path) -> None:
    root = _base_case(tmp_path / "case")
    main_mod.main([str(root), "-o", str(tmp_path / "out")])
    assert (tmp_path / "out" / "out.json").is_file()


def test_main_info_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--info", "-i", str(tmp_path / "missing")])
    assert exc.value.code == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("effect", "code", "needle"),
    [
        (NotImplementedError("later"), 2, "sddp2gtopt: later"),
        (FileNotFoundError("gone"), 1, "error: gone"),
        (3, 1, "3 CRITICAL"),
    ],
)
def test_main_convert_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    effect: object,
    code: int,
    needle: str,
) -> None:
    def fake_convert(_options: dict[str, object]) -> object:
        if isinstance(effect, Exception):
            raise effect
        return effect

    monkeypatch.setattr(main_mod, "convert_sddp_case", fake_convert)
    with pytest.raises(SystemExit) as exc:
        main_mod.main(["-i", str(tmp_path)])
    assert exc.value.code == code
    assert needle in capsys.readouterr().err


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_module_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["sddp2gtopt", "--version"])
    for mod in ("sddp2gtopt", "sddp2gtopt.main"):
        sys.modules.pop("sddp2gtopt.__main__", None)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module(mod, run_name="__main__", alter_sys=False)
        assert exc.value.code == 0
