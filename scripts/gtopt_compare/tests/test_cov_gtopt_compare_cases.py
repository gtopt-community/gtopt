# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for gtopt_compare: readers, per-case comparisons and CLI.

The static cases run the real (fast) pandapower DC OPF on the built-in
networks and compare against synthetic gtopt CSV output derived from the
pandapower result itself (matching → PASS, perturbed → FAIL).
"""

import runpy
import sys
from pathlib import Path

import pandapower as pp
import pytest

from gtopt_compare.main import (
    _CASES,
    _NET_BUILDERS,
    _compare_bat_4b_24,
    _compare_plp,
    _find_gtopt_csv,
    _read_gtopt_per_block_uid_values,
    _read_solution_csv,
    _report_cost,
    _report_diff,
    build_net_bat_4b_24,
    build_net_ieee_57b,
    build_net_s1b,
    load_pandapower_net,
    main,
    read_gtopt_battery_dispatch,
    read_gtopt_generation,
    read_gtopt_lmps,
    save_pandapower_net,
)


def _wide(path: Path, rows: list[list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(rows[0]) if rows else 0
    header = "scenario,stage,block," + ",".join(f"uid:{i + 1}" for i in range(n))
    body = "".join(
        f"1,1,{b + 1}," + ",".join(repr(v) for v in row) + "\n"
        for b, row in enumerate(rows)
    )
    path.write_text(header + "\n" + body)


def _long(path: Path, cells: list[tuple]) -> None:
    """cells: (block, uid, value) triples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"1,1,{b},{u},{v}\n" for b, u, v in cells)
    path.write_text("scenario,stage,block,uid,value\n" + body)


# ── CSV readers ──────────────────────────────────────────────────────────


def test_find_gtopt_csv_prefers_shards(tmp_path):
    (tmp_path / "generation_sol.csv").write_text("x\n")
    shard = tmp_path / "generation_sol_s1_p1.csv"
    shard.write_text("x\n")
    assert _find_gtopt_csv(tmp_path, "generation_sol") == shard


def test_long_form_reader_orders_and_pads(tmp_path):
    path = tmp_path / "g.csv"
    _long(path, [(2, 3, 30.0), (2, 1, 10.0), (4, 2, 7.0)])
    with path.open("a") as fh:
        fh.write("\n1,1,5,bad,1.0\n1,1,6,1\n")  # blank, unparsable, short rows
    assert _read_gtopt_per_block_uid_values(path) == [
        [10.0, 0.0, 30.0],
        [0.0, 7.0, 0.0],
    ]
    assert _read_gtopt_per_block_uid_values(path, n_blocks=4, n_uids=2) == [
        [0.0, 0.0],
        [10.0, 0.0],
        [0.0, 0.0],
        [0.0, 7.0],
    ]


def test_long_form_reader_without_block_column(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("scenario,uid,value\n1,1,5.0\n2,1,6.0\n")
    # n_blocks is ignored when there is no block column
    assert _read_gtopt_per_block_uid_values(path, n_blocks=3) == [[5.0], [6.0]]


def test_long_form_reader_skips_non_integer_block_keys(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("block,uid,value\nx,1,5.0\n2,1,6.0\n")
    assert _read_gtopt_per_block_uid_values(path, n_blocks=2) == [[0.0], [6.0]]


def test_wide_reader_without_uid_columns(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("scenario,stage,block\n1,1,1\n")
    assert not _read_gtopt_per_block_uid_values(path)


def test_generation_and_lmps_empty_files(tmp_path):
    _wide(tmp_path / "Generator" / "generation_sol.csv", [])
    _wide(tmp_path / "Bus" / "balance_dual.csv", [])
    assert not read_gtopt_generation(tmp_path)
    assert not read_gtopt_lmps(tmp_path)


def test_read_solution_csv_formats(tmp_path):
    sol = tmp_path / "solution.csv"
    sol.write_text("\n\n")
    assert not _read_solution_csv(sol)

    sol.write_text("scene,phase,status,obj_value,kappa,extra\n1,1,optimal,12.5\n")
    assert _read_solution_csv(sol) == {
        "scene": 1,
        "phase": 1,
        "status": "optimal",
        "obj_value": 12.5,
    }

    sol.write_text("scene,phase,status,obj_value\n")  # header only
    assert not _read_solution_csv(sol)

    sol.write_text("obj_value,3.5\nstatus,ok\niters,7\n")
    assert _read_solution_csv(sol) == {"obj_value": 3.5, "status": "ok", "iters": 7}


def test_battery_dispatch_long_form_padding(tmp_path):
    _long(tmp_path / "Battery" / "fout_sol.csv", [(2, 1, 5.0)])
    _long(tmp_path / "Battery" / "finp_sol.csv", [(1, 1, 3.0)])
    fout, finp = read_gtopt_battery_dispatch(tmp_path, n_blocks=3)
    assert fout == [0.0, 5.0, 0.0]
    assert finp == [3.0, 0.0, 0.0]


def test_battery_dispatch_empty_rows_default_to_zero(tmp_path):
    _wide(tmp_path / "Battery" / "fout_sol.csv", [[1.0]])
    (tmp_path / "Battery" / "finp_sol.csv").write_text("scenario,stage,block,uid:1\n")
    fout, finp = read_gtopt_battery_dispatch(tmp_path)
    assert fout == [1.0]
    assert not finp


# ── pandapower file I/O and builders ─────────────────────────────────────


def test_save_and_load_pandapower_net(tmp_path):
    path = tmp_path / "sub" / "net.json"
    save_pandapower_net(build_net_s1b(), path)
    net = load_pandapower_net(path)
    assert len(net.gen) == 2


def test_build_net_ieee_57b_linear_costs():
    net = build_net_ieee_57b()
    assert len(net.bus) == 57
    assert (net.poly_cost["cp2_eur_per_mw2"] == 0.0).all()


@pytest.mark.parametrize(
    ("fout", "finp", "n_gen", "n_load"),
    [(0.0, 0.0, 3, 2), (10.0, 0.0, 4, 2), (0.0, 10.0, 3, 3)],
)
def test_build_net_bat_4b_24_battery_injection(fout, finp, n_gen, n_load):
    net = build_net_bat_4b_24(12, fout, finp)
    assert len(net.gen) == n_gen
    assert len(net.load) == n_load
    assert len(net.line) == 5


# ── diff helpers ─────────────────────────────────────────────────────────


def test_report_diff_and_cost(capsys):
    assert _report_diff("  g1", "ref", 1.0, 1.5, 1.0)
    assert not _report_diff("  g2", "ref", 1.0, 3.0, 1.0, prec=2)
    assert _report_cost(1000.0, 1000.5)
    assert not _report_cost(1000.0, 1010.0)
    out = capsys.readouterr().out
    assert "  g1: ref=1.0000  gtopt=1.5000  diff=0.5000  [PASS]" in out
    assert "  g2: ref=1.00  gtopt=3.00  diff=2.00  [FAIL]" in out
    assert "Cost: pandapower=1000.00  gtopt=1010.00  diff=10.0000  [FAIL]" in out


def test_report_diff_nan_fails(capsys):
    """Regression: a NaN value printed [FAIL] but was counted as a pass."""
    nan = float("nan")
    assert not _report_diff("  g1", "ref", 1.0, nan, 1.0)
    assert not _report_cost(1000.0, nan)
    out = capsys.readouterr().out
    assert "[PASS]" not in out
    assert out.count("[FAIL]") == 2


def test_static_case_nan_output_fails(tmp_path):
    """Regression: NaN gtopt dispatch made the s1b comparison return True."""
    _static_output(tmp_path, build_net_s1b(), total=False, delta=float("nan"))
    assert _CASES["s1b"](tmp_path, tol_mw=1.0, tol_lmp=0.1) is False


# ── static DC-OPF cases ──────────────────────────────────────────────────


def _static_output(out_dir: Path, net, *, total: bool, delta: float) -> None:
    """Write gtopt output that mirrors pandapower's solution of *net*."""
    pp.rundcopp(net, verbose=False)
    gen = [float(v) for v in net.res_gen["p_mw"].values]
    if total:
        gen = [float(net.res_ext_grid["p_mw"].sum())] + gen
    _wide(out_dir / "Generator" / "generation_sol.csv", [[g + delta for g in gen]])
    lmps = [float(v) + delta for v in net.res_bus["lam_p"].values]
    _wide(out_dir / "Bus" / "balance_dual.csv", [lmps])
    (out_dir / "solution.csv").write_text(
        f"obj_value,{float(net.res_cost) + 100.0 * delta}\n"
    )


_STATIC = [
    ("s1b", False),
    ("ieee_4b_ori", False),
    ("ieee30b", True),
    ("ieee_57b", True),
]


@pytest.mark.parametrize(("case", "total"), _STATIC)
def test_static_case_pass_and_fail(tmp_path, capsys, case, total):
    ok_dir, bad_dir = tmp_path / "ok", tmp_path / "bad"
    _static_output(ok_dir, _NET_BUILDERS[case](), total=total, delta=0.0)
    _static_output(bad_dir, _NET_BUILDERS[case](), total=total, delta=5.0)

    assert _CASES[case](ok_dir, tol_mw=1.0, tol_lmp=0.1) is True
    assert _CASES[case](bad_dir, tol_mw=1.0, tol_lmp=0.1) is False
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert ("Total generation:" in out) == total
    assert ("Bus LMP comparison" in out) == (case != "s1b")


def test_static_case_from_pandapower_file(tmp_path):
    net_file = tmp_path / "net.json"
    save_pandapower_net(build_net_s1b(), net_file)
    _static_output(tmp_path, build_net_s1b(), total=False, delta=0.0)
    assert _CASES["s1b"](tmp_path, 1.0, 0.1, pandapower_file=net_file) is True


# ── bat_4b_24 ────────────────────────────────────────────────────────────


def _bat_output(out_dir: Path, delta: float, n_blocks: int = 24) -> None:
    fout = [0.0] * n_blocks
    finp = [0.0] * n_blocks
    fout[18] = 20.0
    finp[3] = 10.0
    _wide(out_dir / "Battery" / "fout_sol.csv", [[v] for v in fout])
    _wide(out_dir / "Battery" / "finp_sol.csv", [[v] for v in finp])
    rows = []
    for b in range(min(n_blocks, 24)):
        net = build_net_bat_4b_24(b, fout[b], finp[b])
        pp.rundcopp(net, verbose=False)
        rows.append([float(v) + delta for v in net.res_gen["p_mw"].values[:3]])
    _wide(out_dir / "Generator" / "generation_sol.csv", rows)


def test_compare_bat_4b_24_pass_and_fail(tmp_path, capsys):
    ok_dir, bad_dir = tmp_path / "ok", tmp_path / "bad"
    _bat_output(ok_dir, 0.0)
    _bat_output(bad_dir, 5.0)
    assert _compare_bat_4b_24(ok_dir, 1.0, 0.1, pandapower_file=Path("x.json"))
    assert not _compare_bat_4b_24(bad_dir, 1.0, 0.1)
    out = capsys.readouterr().out
    assert "--pandapower-file is ignored for bat_4b_24" in out
    assert "block 24 g_solar" in out


def test_compare_bat_4b_24_wrong_block_count(tmp_path):
    _wide(tmp_path / "Battery" / "fout_sol.csv", [[0.0]] * 3)
    _wide(tmp_path / "Battery" / "finp_sol.csv", [[0.0]] * 3)
    with pytest.raises(ValueError, match="Expected 24 blocks"):
        _compare_bat_4b_24(tmp_path, 1.0, 0.1)


# ── generic PLP comparison ───────────────────────────────────────────────


def _plp_ref(plp_dir: Path, *, ess_rows: list[str] | None) -> None:
    """Two blocks: centrals g1 (Ter), BESS1 (BAT), fail (FAL); buses b1, b2."""
    plp_dir.mkdir(parents=True, exist_ok=True)
    cen = ["Hidro,Bloque,CenNum,CenNom,CenTip,CenPgen"]
    for b in (1, 2):
        cen += [
            f"Sim  1,{b},1,g1,Ter,{10.0 * b}",
            f"Sim  1,{b},2,BESS1,BAT,{1.0 * b}",
            f"Sim  1,{b},3,fail,FAL,0.0",
        ]
    cen.append("MEDIA,1,1,g1,Ter,999.0")
    (plp_dir / "plpcen.csv").write_text("\n".join(cen) + "\n")
    bus_rows = ["Hidro,Bloque,BarNum,BarNom,CMgBar"]
    for b in (1, 2):
        bus_rows += [f"Sim  1,{b},1,b1,20.0", f"Sim  1,{b},2,b2,{20.0 + b}"]
    (plp_dir / "plpbar.csv").write_text("\n".join(bus_rows) + "\n")
    if ess_rows is not None:
        header = "Hidro,Bloque,EssNom,DCar,GDes"
        (plp_dir / "plpess.csv").write_text("\n".join([header, *ess_rows]) + "\n")


def _plp_gtopt(out_dir: Path, *, gen_delta=0.0, lmp_delta=0.0, battery=True):
    _wide(out_dir / "Generator" / "generation_sol.csv", [[10.0 + gen_delta], [20.0]])
    _wide(
        out_dir / "Bus" / "balance_dual.csv", [[20.0, 21.0 + lmp_delta], [20.0, 22.0]]
    )
    if battery:
        _wide(out_dir / "Battery" / "fout_sol.csv", [[1.0], [2.0]])
        _wide(out_dir / "Battery" / "finp_sol.csv", [[0.0], [0.0]])


_ESS_ONE = ["Sim  1,1,BESS1,0.0,1.0", "Sim  1,2,BESS1,0.0,2.0"]


def test_compare_plp_all_match(tmp_path, capsys):
    plp = tmp_path / "plp"
    _plp_ref(plp, ess_rows=_ESS_ONE)
    _plp_gtopt(tmp_path / "out")
    assert _compare_plp(tmp_path / "out", 1.0, 0.1, plp_output=plp)
    out = capsys.readouterr().out
    assert "1 conventional central(s): g1" in out
    assert "battery ESS dispatch" in out
    assert "BAT central dispatch (MW) (BESS1)" in out
    assert "[FAIL]" not in out


def test_compare_plp_mismatches_fail(tmp_path, capsys):
    plp = tmp_path / "plp"
    _plp_ref(plp, ess_rows=_ESS_ONE)
    out_dir = tmp_path / "out"
    _plp_gtopt(out_dir, gen_delta=5.0, lmp_delta=1.0)
    _wide(out_dir / "Battery" / "fout_sol.csv", [[9.0], [2.0]])
    assert not _compare_plp(out_dir, 1.0, 0.1, plp_output=plp)
    fails = [ln for ln in capsys.readouterr().out.splitlines() if "[FAIL]" in ln]
    assert any(" g1:" in ln for ln in fails)
    assert any("BESS1 discharge" in ln for ln in fails)
    assert any("BAT discharge" in ln for ln in fails)
    assert any(" b2:" in ln for ln in fails)


def test_compare_plp_multiple_ess_units(tmp_path, capsys):
    plp = tmp_path / "plp"
    rows = _ESS_ONE + ["Sim  1,1,BESS2,0.0,0.0", "Sim  1,2,BESS2,0.0,0.0"]
    _plp_ref(plp, ess_rows=rows)
    _plp_gtopt(tmp_path / "out")
    _compare_plp(tmp_path / "out", 1.0, 0.1, plp_output=plp)
    out = capsys.readouterr().out
    assert "2 ESS unit(s): BESS1, BESS2" in out
    assert "BESS2 charge" in out


def test_compare_plp_without_battery_output(tmp_path, capsys):
    """PLP has ESS data but gtopt wrote no Battery dir → treated as zero."""
    plp = tmp_path / "plp"
    _plp_ref(plp, ess_rows=_ESS_ONE)
    _plp_gtopt(tmp_path / "out", battery=False)
    assert not _compare_plp(tmp_path / "out", 1.0, 0.1, plp_output=plp)
    out = capsys.readouterr().out
    assert "BAT central dispatch" not in out  # no gtopt fout to compare


def test_compare_plp_battery_dir_without_files(tmp_path):
    plp = tmp_path / "plp"
    _plp_ref(plp, ess_rows=_ESS_ONE)
    _plp_gtopt(tmp_path / "out", battery=False)
    (tmp_path / "out" / "Battery").mkdir()
    assert not _compare_plp(tmp_path / "out", 1.0, 0.1, plp_output=plp)


def test_compare_plp_empty_ess_file_and_no_ess_file(tmp_path, capsys):
    plp = tmp_path / "plp"
    _plp_ref(plp, ess_rows=[])
    _plp_gtopt(tmp_path / "out")
    assert _compare_plp(tmp_path / "out", 1.0, 0.1, plp_output=plp)
    assert "battery ESS dispatch" not in capsys.readouterr().out

    plp2 = tmp_path / "plp2"
    _plp_ref(plp2, ess_rows=None)
    assert _compare_plp(tmp_path / "out", 1.0, 0.1, plp_output=plp2)
    assert "battery ESS dispatch" not in capsys.readouterr().out


def test_compare_plp_short_gtopt_rows_default_to_zero(tmp_path):
    plp = tmp_path / "plp"
    _plp_ref(plp, ess_rows=None)
    out_dir = tmp_path / "out"
    _plp_gtopt(out_dir)
    # long-form LMP file with only bus 1 in block 1: missing cells → 0.0
    _long(out_dir / "Bus" / "balance_dual.csv", [(1, 1, 20.0)])
    assert not _compare_plp(out_dir, 1.0, 0.1, plp_output=plp)


# ── CLI ──────────────────────────────────────────────────────────────────


def test_main_save_pandapower_file_only(tmp_path, capsys):
    out = tmp_path / "net.json"
    with pytest.raises(SystemExit) as exc:
        main(["--case", "s1b", "--save-pandapower-file", str(out)])
    assert exc.value.code == 0
    assert out.exists()
    assert "Saved pandapower network to" in capsys.readouterr().out


def test_main_save_then_compare_pass(tmp_path, capsys):
    _static_output(tmp_path, build_net_s1b(), total=False, delta=0.0)
    out = tmp_path / "net.json"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--case",
                "s1b",
                "--save-pandapower-file",
                str(out),
                "--gtopt-output",
                str(tmp_path),
            ]
        )
    assert exc.value.code == 0
    assert "RESULT: PASS" in capsys.readouterr().out


def test_main_compare_fail_exit_1(tmp_path, capsys):
    _static_output(tmp_path, build_net_s1b(), total=False, delta=5.0)
    with pytest.raises(SystemExit) as exc:
        main(["--case", "s1b", "--gtopt-output", str(tmp_path)])
    assert exc.value.code == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_main_save_error_exit_2(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("builder exploded")

    monkeypatch.setitem(_NET_BUILDERS, "s1b", boom)
    with pytest.raises(SystemExit) as exc:
        main(["--case", "s1b", "--save-pandapower-file", str(tmp_path / "n.json")])
    assert exc.value.code == 2


def test_main_value_error_exit_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--case", "plp", "--gtopt-output", str(tmp_path)])
    assert exc.value.code == 2


def test_module_entry_point(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["gtopt_compare", "--case", "plp", "--gtopt-output", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt_compare", run_name="__main__")
    assert exc.value.code == 2


def test_compare_plp_multiple_ess_indexes_per_block_and_unit(tmp_path, capsys):
    """Regression: the 2nd ESS must read its own uid column at every block.

    Previously ``ei > 0`` indexed the per-block list by the ESS index
    (``gt_finp[ei]``), i.e. it compared BESS2 at every block against
    BESS1's value at block ``ei``.
    """
    plp = tmp_path / "plp"
    rows = _ESS_ONE + ["Sim  1,1,BESS2,3.0,5.0", "Sim  1,2,BESS2,4.0,6.0"]
    _plp_ref(plp, ess_rows=rows)
    out_dir = tmp_path / "out"
    _plp_gtopt(out_dir)
    _wide(out_dir / "Battery" / "fout_sol.csv", [[1.0, 5.0], [2.0, 6.0]])
    _wide(out_dir / "Battery" / "finp_sol.csv", [[0.0, 3.0], [0.0, 4.0]])
    ok = _compare_plp(out_dir, 1.0, 0.1, plp_output=plp)
    out = capsys.readouterr().out
    assert "BESS2 charge" in out
    assert not [ln for ln in out.splitlines() if "[FAIL]" in ln and "BESS" in ln]
    assert ok
