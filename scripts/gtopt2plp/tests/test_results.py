# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of the PLP results export (:mod:`gtopt2plp.results`)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from gtopt_compare.main import (
    _compare_plp,
    read_plp_bus_names,
    read_plp_central_names,
    read_plp_cmg,
    read_plp_ess,
    read_plp_generation,
)
from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.testing.roundtrip import gtopt_binary
from gtopt_shared.testing.tiny_cases import write_tiny_case, write_tiny_results
from gtopt2plp.main import main
from gtopt2plp.results import PlpResultsWriter, fortran_e, write_results
from gtopt2plp.writer import PlpWriter, line_names

REPO = Path(__file__).resolve().parents[3]


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="latin-1", newline="") as fh:
        rd = csv.reader(fh)
        head = [h.strip() for h in next(rd)]
        return [dict(zip(head, (c.strip() for c in r), strict=True)) for r in rd]


def _pick(rows: list[dict[str, str]], **kw: str) -> dict[str, str]:
    got = [r for r in rows if all(r[k] == v for k, v in kw.items())]
    assert len(got) == 1, (kw, got)
    return got[0]


@pytest.fixture(name="exported")
def _exported(tmp_path: Path) -> Path:
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    res = write_tiny_results(tmp_path / "res", "hive")
    out = tmp_path / "plp"
    files = write_results(case, res, out)
    assert {f.name for f in files} >= {
        "plpcen.csv",
        "plpbar.csv",
        "plplin.csv",
        "plpemb.csv",
        "plpess.csv",
        "plpfal.csv",
        "plpcostop.csv",
        "centrales.csv",
        "barras.csv",
        "lineas.csv",
    }
    return out


def test_fortran_e() -> None:
    """``E9.2`` text as the Fortran writer prints it."""
    assert fortran_e(1.0e6) == " 0.10E+07"
    assert fortran_e(1.0e9) == " 0.10E+10"
    assert fortran_e(0.0) == " 0.00E+00"
    assert fortran_e(0.0999).strip() == "0.10E+00"
    assert fortran_e(-250.0).strip() == "-0.25E+03"


def test_plpcen(exported: Path) -> None:
    """Generation, energy (GWh), injection value and costs per central."""
    rows = _rows(exported / "plpcen.csv")
    r = _pick(rows, Hidro="Sim  1", Bloque="2", CenNom="TERMO")
    assert r["CenNum"] == "4" and r["CenTip"] == "Ter" and r["CenBar"] == "2"
    assert r["BarNom"] == "Sur220" and r["TipoEtapa"] == "02 Bloques"
    assert float(r["CenPgen"]) == pytest.approx(12.0)
    assert float(r["CenQgen"]) == pytest.approx(12.0)
    assert float(r["CenEgen"]) == pytest.approx(0.17)  # 12 MW x 14 h
    assert float(r["CenInyP"]) == pytest.approx(12 * 62.0)
    assert float(r["CenCVar"]) == pytest.approx(50.0)
    assert float(r["CenCostOp"]) == pytest.approx(8.4)  # k$
    assert float(r["CenPMax"]) == pytest.approx(150.0)
    lago = _pick(rows, Hidro="Sim  2", Bloque="1", CenNom="LAGO")
    assert lago["CenTip"] == "Emb"
    assert float(lago["CenQgen"]) == pytest.approx(25.0)  # turbine m3/s
    assert float(lago["CenRen"]) == pytest.approx(2.0)
    bat = _pick(rows, Hidro="Sim  1", Bloque="2", CenNom="BESS1")
    assert bat["CenTip"] == "BAT" and float(bat["CenPgen"]) == pytest.approx(3.0)
    assert _pick(rows, Hidro="Sim  1", Bloque="1", CenNom="PV1")["CenTip"] == "Pas"
    media = _pick(rows, Hidro="MEDIA", Bloque="1", CenNom="TERMO")
    assert float(media["CenPgen"]) == pytest.approx(16.0)  # (11 + 21) / 2
    assert not [r for r in rows if r["CenTip"] == "Fal"]
    # header exactly as CEN65 plp-gdbdcen.f
    head = (exported / "plpcen.csv").read_text(encoding="latin-1").splitlines()[0]
    assert head == (
        "Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,CenQgen,"
        "CenPgen,CenEgen,CenInyP,CenInyE,CenRen,CenCVar,CenCostOp,CenPMax"
    )


def test_plpbar_plpfal(exported: Path) -> None:
    """CMg is the balance dual; demand = served + unserved; deficit rows."""
    rows = _rows(exported / "plpbar.csv")
    r = _pick(rows, Hidro="Sim  2", Bloque="4", BarNom="Sur220")
    assert r["BarNum"] == "2"
    assert float(r["CMgBar"]) == pytest.approx(64.0)
    dem = 24.0 + 0.0 + 0.0 + 20.0 - 0.0 + 2.0  # TERMO + flow in + fail
    assert float(r["DemBarP"]) == pytest.approx(dem)
    assert float(r["DemBarE"]) == pytest.approx(dem * 12 / 1000, abs=1e-3)
    assert float(r["BarRetP"]) == pytest.approx(dem * 64.0, abs=1e-3)
    assert float(r["PerBarP"]) == 0.0
    fal = _rows(exported / "plpfal.csv")
    assert [(f["Hidro"], f["Bloque"]) for f in fal] == [
        ("Sim  1", "4"),
        ("Sim  2", "4"),
        ("MEDIA", "4"),
    ]
    assert fal[0]["CenNom"] == "FALLA_Sur220" and fal[0]["CenTip"] == "Fal"
    assert float(fal[0]["CenPgen"]) == pytest.approx(2.0)


def test_plplin(exported: Path) -> None:
    """Flow, use (%) and tariff income ($/h) per line."""
    rows = _rows(exported / "plplin.csv")
    r = _pick(rows, Hidro="Sim  1", Bloque="3")
    assert r["LinNom"] == "Norte_Sur" and (r["BarA"], r["BarB"]) == ("1", "2")
    assert float(r["LinFluP"]) == pytest.approx(20.0)
    assert float(r["LinFluMax"]) == pytest.approx(80.0)
    assert float(r["LinUso"]) == pytest.approx(25.0)
    assert float(r["LinITP"]) == pytest.approx(20.0 * 10.0)  # F x (CMg_b - CMg_a)
    assert float(r["LinITE"]) == pytest.approx(2.4)


def test_plpemb(exported: Path) -> None:
    """Stage volumes (hm3, EmbFac 1e6), flows and water values."""
    rows = _rows(exported / "plpemb.csv")
    r = _pick(rows, Hidro="Sim  2", Bloque="3", EmbNom="LAGO")
    assert r["EmbFac"] == "0.10E+07"
    assert float(r["EmbVini"]) == pytest.approx(480.0)
    assert float(r["EmbVfin"]) == pytest.approx(300.0)
    assert float(r["EmbQgen"]) == pytest.approx(25.0)
    assert float(r["EmbQver"]) == pytest.approx(1.0)  # drain
    assert float(r["EmbAflu"]) == pytest.approx(15.0)
    assert float(r["EmbPsom2"]) == pytest.approx(2.0)  # 2000 $/hm3 per dam3
    assert float(r["EmbPsom"]) == pytest.approx(2.0 * 3.6 / 2.0)  # $/MWh


def test_plpess_and_costop(exported: Path) -> None:
    """ESS rows (as gtopt_compare reads them) and the operating cost."""
    rows = _rows(exported / "plpess.csv")
    r1 = _pick(rows, Hidro="Sim  1", Bloque="1")
    assert r1["EssNom"] == "BESS1"
    assert float(r1["Efin"]) == pytest.approx(30.0)
    assert float(r1["DCar"]) == pytest.approx(3.0)
    assert float(r1["EMax"]) == pytest.approx(160.0)
    assert float(r1["PGNet"]) == pytest.approx(-3.0)
    assert float(r1["EfPSom"]) == pytest.approx(36.0)
    assert float(r1["EfPSom2"]) == pytest.approx(10.0)
    r2 = _pick(rows, Hidro="Sim  1", Bloque="2")
    assert float(r2["GDes"]) == pytest.approx(3.0)
    cost = _rows(exported / "plpcostop.csv")
    assert float(_pick(cost, Hidro="Sim  1", IBlo="1")["CostoOperActual"]) == (
        pytest.approx(11 * 50.0 * 10)
    )
    # block 4: TERMO 14 MW x 55 $/MWh + 2 MW deficit x 1000 $/MWh, 12 h
    assert float(_pick(cost, Hidro="Sim  1", IBlo="4")["CostoOperActual"]) == (
        pytest.approx((14 * 55.0 + 2 * 1000.0) * 12)
    )


def test_name_tables(exported: Path) -> None:
    """barras / centrales / lineas carry the input export's numbers."""
    cen = (exported / "centrales.csv").read_text(encoding="latin-1").splitlines()
    assert cen[0] == "#Numero, Nombre, Tipo, Empresa, Barra"
    kinds = {ln.split(",")[1].strip(): ln.split(",")[2] for ln in cen[1:]}
    assert kinds == {
        "LAGO": "E",
        "PV1": "P",
        "TERMO": "T",
        "BESS1": "B",
        "FALLA_Norte220": "F",
        "FALLA_Sur220": "F",
    }
    bars = (exported / "barras.csv").read_text(encoding="latin-1").splitlines()
    assert [b.split(",")[1].strip() for b in bars[1:]] == ["Norte220", "Sur220"]
    lin = (exported / "lineas.csv").read_text(encoding="latin-1").splitlines()
    assert lin[1].split(",")[1].strip() == "Norte_Sur"
    assert lin[1].split(",")[2].strip() == "220"


def test_gtopt_compare_reads_files(exported: Path) -> None:
    """gtopt_compare's PLP readers parse the exported files."""
    names = read_plp_central_names(exported)
    assert ("TERMO", "Ter") in names and ("BESS1", "BAT") in names
    assert [n for n, _ in names] == ["LAGO", "TERMO", "BESS1", "PV1"]  # CenNum order
    assert read_plp_bus_names(exported) == ["Norte220", "Sur220"]
    gen = read_plp_generation(exported)
    assert sorted(gen) == [1, 2, 3, 4]
    assert read_plp_cmg(exported)[2]["Sur220"] == pytest.approx(62.0)
    ess = read_plp_ess(exported)
    assert ess[1]["BESS1"]["charge"] == pytest.approx(3.0)


def test_consistent_with_input_export(tmp_path: Path) -> None:
    """Names / numbers in the result files are those of the .dat files."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    out = tmp_path / "plp"
    w = PlpWriter(case, out)
    w.write()
    write_results(case, write_tiny_results(tmp_path / "res", "csv"), out, w)
    meta = json.loads((out / "gtopt2plp.json").read_text(encoding="utf-8"))
    cen = {r["CenNom"]: int(r["CenNum"]) for r in _rows(out / "plpcen.csv")}
    for name, num in cen.items():
        assert meta["centrals"][name]["num"] == num
    cnfli = (out / "plpcnfli.dat").read_text(encoding="latin-1")
    assert all(f"'{n}'" in cnfli for n in line_names(case.elements("line_array")))


def test_no_results(tmp_path: Path) -> None:
    """An output directory without results is an error."""
    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no gtopt results"):
        PlpResultsWriter(case, tmp_path / "empty", tmp_path / "plp").write()


def test_losses_and_flowp(tmp_path: Path) -> None:
    """Without ``flow_sol`` the flow is flowp - flown; losses go to buses."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    case = GtoptCase.load(write_tiny_case(tmp_path / "case"))
    res = write_tiny_results(tmp_path / "res", "parquet")
    (res / "Line" / "flow_sol.parquet").unlink()
    cells = [(s, (b + 1) // 2, b, 1) for s in (1, 2) for b in (1, 2, 3, 4)]
    for fld, val in (("flowp_sol", 0.0), ("flown_sol", 30.0), ("loss_sol", 2.0)):
        pd.DataFrame(
            [(*c, val) for c in cells],
            columns=["scenario", "stage", "block", "uid", "value"],
        ).to_parquet(res / "Line" / f"{fld}.parquet")
    out = tmp_path / "plp"
    write_results(case, res, out)
    r = _pick(_rows(out / "plplin.csv"), Hidro="Sim  1", Bloque="1")
    assert float(r["LinFluP"]) == pytest.approx(-30.0)
    assert float(r["LinPerP"]) == pytest.approx(2.0)
    # B -> A flow: income = |F| (CMg_a - CMg_b) - loss x CMg_a (receiver a)
    assert float(r["LinITP"]) == pytest.approx(30 * (51.0 - 61.0) - 2.0 * 51.0)
    bars = _rows(out / "plpbar.csv")
    # receiver allocation: the loss sits at the receiving bus (bus 1 here)
    assert float(_pick(bars, Hidro="Sim  1", Bloque="1", BarNum="1")["PerBarP"]) == (
        pytest.approx(2.0)
    )
    assert float(_pick(bars, Hidro="Sim  1", Bloque="1", BarNum="2")["PerBarP"]) == 0.0


def test_turbine_on_waterway(tmp_path: Path) -> None:
    """A turbine on a waterway reads its flow from ``Waterway/flow_sol``;
    a seepage waterway goes to ``EmbQFil``, any other to ``EmbQver``."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    from gtopt_shared.testing.tiny_cases import (  # pylint: disable=import-outside-toplevel
        tiny_planning,
    )

    p = tiny_planning()
    s = p["system"]
    s["junction_array"].append({"uid": 2, "name": "MAR", "drain": True})
    s["waterway_array"] = [
        {"uid": 1, "name": "LAGO_ver_1", "junction_a": "LAGO", "junction_b": "MAR"},
        {"uid": 2, "name": "LAGO_fil", "junction_a": "LAGO", "junction_b": "MAR"},
    ]
    s["reservoir_seepage_array"] = [
        {"uid": 1, "name": "fil", "waterway": "LAGO_fil", "reservoir": "LAGO"}
    ]
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "c.json").write_text(json.dumps(p), encoding="utf-8")
    case = GtoptCase.load(case_dir / "c.json")
    w = PlpWriter(case, tmp_path / "plp")
    w.collect()
    lago = next(ce for ce in w.centrals if ce.name == "LAGO")
    lago.turbine = {**(lago.turbine or {}), "waterway": "LAGO_ver_1"}
    res = write_tiny_results(tmp_path / "res", "parquet")
    (res / "Turbine" / "flow_sol.parquet").unlink()
    (res / "Waterway").mkdir()
    cells = [(sc, (b + 1) // 2, b) for sc in (1, 2) for b in (1, 2, 3, 4)]
    pd.DataFrame(
        [(*c, 1, 7.0) for c in cells] + [(*c, 2, 0.5) for c in cells],
        columns=["scenario", "stage", "block", "uid", "value"],
    ).to_parquet(res / "Waterway" / "flow_sol.parquet")
    write_results(case, res, tmp_path / "plp", w)
    r = _pick(_rows(tmp_path / "plp" / "plpemb.csv"), Hidro="Sim  1", Bloque="1")
    assert float(r["EmbQgen"]) == pytest.approx(7.0)
    assert float(r["EmbQFil"]) == pytest.approx(0.5)
    assert float(r["EmbQver"]) == pytest.approx(0.0)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--results`` adds the result files; ``--results-only`` skips .dat."""
    case = write_tiny_case(tmp_path / "case")
    res = write_tiny_results(tmp_path / "res", "csv.zst")
    assert main([str(case), "-o", str(tmp_path / "a"), "--results", str(res)]) == 0
    assert (tmp_path / "a" / "plpcnfce.dat").exists()
    assert (tmp_path / "a" / "plpcen.csv").exists()
    out = tmp_path / "b"
    assert (
        main([str(case), "-o", str(out), "--results", str(res), "--results-only"]) == 0
    )
    assert (out / "plpcen.csv").exists() and not (out / "plpcnfce.dat").exists()
    assert "PLP result file" in capsys.readouterr().out
    assert main([str(case), "-o", str(out), "--results-only"]) == 1
    (tmp_path / "none").mkdir()
    assert main([str(case), "-o", str(out), "--results", str(tmp_path / "none")]) == 1


# ── integration: solve with gtopt, export, read back ──────────────────


@pytest.mark.integration
def test_solved_tiny_case(tmp_path: Path) -> None:
    """Solve the tiny case, export PLP results and check the balances."""
    import subprocess  # pylint: disable=import-outside-toplevel

    gtopt = gtopt_binary()
    if gtopt is None:
        pytest.skip("gtopt binary not found")
    case_json = write_tiny_case(tmp_path / "case")
    out_dir = tmp_path / "gt"
    proc = subprocess.run(
        [
            gtopt,
            str(case_json),
            "--set",
            f"input_directory={case_json.parent}",
            "-d",
            str(out_dir),
            "--solver",
            "highs",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    case = GtoptCase.load(case_json)
    plp = tmp_path / "plp"
    write_results(case, out_dir, plp)
    cen = [r for r in _rows(plp / "plpcen.csv") if r["Hidro"].startswith("Sim")]
    bars = [r for r in _rows(plp / "plpbar.csv") if r["Hidro"].startswith("Sim")]
    fal = [r for r in _rows(plp / "plpfal.csv") if r["Hidro"].startswith("Sim")]
    ess = [r for r in _rows(plp / "plpess.csv") if r["Hidro"].startswith("Sim")]
    for hidro in ("Sim  1", "Sim  2"):
        for blk in ("1", "2", "3", "4"):
            gen = sum(
                float(r["CenPgen"])
                for r in cen
                if (r["Hidro"], r["Bloque"]) == (hidro, blk)
            )
            charge = sum(
                float(r["DCar"])
                for r in ess
                if (r["Hidro"], r["Bloque"]) == (hidro, blk)
            )
            fail = sum(
                float(r["CenPgen"])
                for r in fal
                if (r["Hidro"], r["Bloque"]) == (hidro, blk)
            )
            dem = sum(
                float(r["DemBarP"])
                for r in bars
                if (r["Hidro"], r["Bloque"]) == (hidro, blk)
            )
            # lossless: generation (incl. battery discharge) + deficit
            # = demand + battery charge
            assert gen + fail == pytest.approx(dem + charge, abs=0.05)
    # CMg = the balance dual as gtopt wrote it ($/MWh: thermal cost, then
    # the fail cost in the deficit block)
    cmg = read_plp_cmg(plp)
    assert cmg[1]["Sur220"] == pytest.approx(50.0, abs=0.01)
    assert cmg[4]["Sur220"] == pytest.approx(1000.0, abs=0.01)
    assert read_plp_bus_names(plp) == ["Norte220", "Sur220"]
    assert ("BESS1", "BAT") in read_plp_central_names(plp)
    assert read_plp_ess(plp)


@pytest.mark.integration
def test_gtopt_compare_ieee_9b(tmp_path: Path) -> None:
    """gtopt_compare --case plp passes on gtopt's own results exported to PLP."""
    import subprocess  # pylint: disable=import-outside-toplevel

    gtopt = gtopt_binary()
    case_json = REPO / "cases" / "ieee_9b" / "ieee_9b.json"
    if gtopt is None or not case_json.exists():
        pytest.skip("gtopt binary or cases/ieee_9b not found")
    out_dir = tmp_path / "gt"
    proc = subprocess.run(
        [
            gtopt,
            str(case_json),
            "--set",
            f"input_directory={case_json.parent}",
            "-d",
            str(out_dir),
            "--solver",
            "highs",
            "--set",
            "output_format=csv",
            "--set",
            "output_compression=uncompressed",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    plp = tmp_path / "plp"
    write_results(GtoptCase.load(case_json), out_dir, plp)
    assert _compare_plp(out_dir, tol_mw=0.01, tol_lmp=0.01, plp_output=plp)
