# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt ``line_thermal_limit_mode`` against the pandapower AC-OPF.

Case ``cases/ieee9b_line_limit`` (MATPOWER case9 with linear costs and
line 5-6 limited to 80 % loading, see its README).  The AC-OPF limits
the line CURRENT, which binds at the sending end.  gtopt with the
``tangent_signed_flow`` loss model books half of the loss at each end,
so under ``flow`` the sending-end power ``f + ℓ/2`` overshoots the
rating by half the loss, while ``sending`` holds it at the rating.

Needs pandapower (skipped otherwise) and a gtopt binary (``GTOPT_BIN``,
``PATH`` or a standard build dir; skipped otherwise).
"""

from __future__ import annotations

import csv
import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from gtopt_shared.gtopt_binary import find_gtopt_binary

pytest.importorskip("pandapower")

_REPO = Path(__file__).resolve().parents[3]
_CASE = _REPO / "cases" / "ieee9b_line_limit"
#: 1-based buses of the congested line (power flows 6 -> 5).
_SEND, _RECV = 6, 5
_LINE_NAME = "l5_6"


def _load_case_module() -> ModuleType:
    path = _CASE / "make_case.py"
    if not path.exists():
        pytest.skip(f"case script missing: {path}")
    spec = importlib.util.spec_from_file_location("ieee9b_line_limit_case", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def case_mod() -> ModuleType:
    return _load_case_module()


@pytest.fixture(scope="module")
def gtopt_bin() -> str:
    path = find_gtopt_binary()
    if path is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    return path


@pytest.fixture(scope="module")
def acopf(case_mod: ModuleType) -> dict[str, float]:
    return case_mod.run_acopf(case_mod.build_net())


def _csv(out: Path, stem: str) -> Path:
    """``<stem>.csv`` or its single (scene, phase) shard ``<stem>_s0_p0.csv``."""
    flat = out / f"{stem}.csv"
    return flat if flat.exists() else out / f"{stem}_s0_p0.csv"


def _values(path: Path) -> dict[int, float]:
    """``uid -> value`` of a single-block long-form or wide CSV output."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out: dict[int, float] = {}
    for row in rows:
        if "uid" in row and "value" in row:
            out[int(float(row["uid"]))] = float(row["value"])
            continue
        for key, val in row.items():
            if key.startswith("uid:"):
                out[int(key.split(":", 1)[1])] = float(val)
    return out


def _solve(
    case_mod: ModuleType,
    gtopt_bin: str,
    tmp_path: Path,
    mode: str,
    loss_mode: str,
    allocation: str | None = None,
) -> dict[str, float]:
    work = tmp_path / f"{loss_mode}_{allocation}_{mode}"
    work.mkdir()
    planning = case_mod.gtopt_case(
        case_mod.build_net(),
        mode,
        work / "case.json",
        loss_mode=loss_mode,
        allocation=allocation,
    )
    env = dict(os.environ)
    env.setdefault("GTOPT_SOLVER", "highs")
    subprocess.run(
        [gtopt_bin, "case.json", "--output-directory", "out", "--write-out", "all"],
        cwd=work,
        check=True,
        env=env,
        capture_output=True,
    )
    line = next(
        ln for ln in planning["system"]["line_array"] if ln["name"] == _LINE_NAME
    )
    uid = int(line["uid"])
    out = work / "out"
    flow = _values(_csv(out, "Line/flow_sol"))[uid]
    loss = _values(_csv(out, "Line/loss_sol")).get(uid, 0.0)
    duals = _values(_csv(out, "Bus/balance_dual"))
    # line runs bus_a=5 -> bus_b=6; power flows 6 -> 5 (negative f).
    assert int(line["bus_a"]) == _RECV
    a = 0.5 if loss_mode == "tangent_signed_flow" or allocation == "split" else 0.0
    if allocation == "sender":
        a = 1.0
    f = abs(flow)
    with (out / "solution.csv").open(encoding="utf-8") as fh:
        obj = float(next(csv.DictReader(fh))["obj_value"])
    return {
        "p_send": f + a * loss,
        "p_recv": f - (1.0 - a) * loss,
        "loss": loss,
        "lmp_send": duals[_SEND],
        "lmp_recv": duals[_RECV],
        "obj": obj,
    }


@pytest.mark.integration
def test_sending_mode_holds_ac_sending_limit(case_mod, gtopt_bin, acopf, tmp_path):
    rating = case_mod.rating_mw(case_mod.build_net())
    # The AC current limit binds at the sending end.
    assert acopf["s_eq_send"] >= acopf["s_eq_recv"] - 1e-3
    assert acopf["loading"] == pytest.approx(case_mod.MAX_LOADING, abs=0.05)

    res = {
        m: _solve(case_mod, gtopt_bin, tmp_path, m, "tangent_signed_flow")
        for m in ("flow", "sending", "full")
    }
    # `sending`: sending-end power at the rating (the DC image of the AC
    # sending-end current limit).
    assert res["sending"]["p_send"] == pytest.approx(rating, rel=1e-4)
    # `flow` overshoots by ℓ/2; `full` undershoots by ℓ/2.
    assert res["flow"]["p_send"] == pytest.approx(
        rating + 0.5 * res["flow"]["loss"], rel=1e-4
    )
    assert res["full"]["p_send"] < rating - 0.4 * res["full"]["loss"]
    # AC: the current limit binds at the sending end, sqrt(3)·V_nom·I_send
    # = rating — the quantity `sending` holds at the rating in the DC model.
    assert acopf["s_eq_send"] == pytest.approx(rating, rel=1e-3)
    assert res["sending"]["p_send"] == pytest.approx(acopf["s_eq_send"], rel=1e-3)
    # Congestion: the receiving end is dearer than the sending end.
    for r in res.values():
        assert r["lmp_recv"] > r["lmp_send"]
    # Tighter limit ⇒ costlier dispatch.
    assert res["flow"]["obj"] <= res["sending"]["obj"] <= res["full"]["obj"]


@pytest.mark.integration
def test_receiver_allocation_sending_is_flow(case_mod, gtopt_bin, tmp_path):
    """Under receiver allocation the flow already is the sending end."""
    flow = _solve(case_mod, gtopt_bin, tmp_path, "flow", "bidirectional")
    sending = _solve(case_mod, gtopt_bin, tmp_path, "sending", "bidirectional")
    rating = case_mod.rating_mw(case_mod.build_net())
    assert flow["p_send"] == pytest.approx(rating, rel=1e-4)
    assert sending["p_send"] == pytest.approx(flow["p_send"], rel=1e-6)
