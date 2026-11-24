"""End-to-end: PLP loss tramos -> tangent_signed_flow, solved with HiGHS.

A lossy variant of ``plp_min_2bus`` (FPerdTram = T, one line with
R = 20 Ohm, V = 220 kV, Num.Tramos = 3) is converted with plp2gtopt and
solved with gtopt + HiGHS.  With ``c = R / V^2``, ``fmax = 150 MW`` and K
tangents spread over the signed range, the LP loss must satisfy

    c*f^2 - c*fmax^2/K^2 - tol  <=  loss  <=  c*f^2 + tol

(tangent lower envelope, never above the physical quadratic = no
fictitious loss).  The same holds when both buses are driven to a
NEGATIVE price (a -0.4 $/MWh surplus injection at each bus) as long as
``loss_cost_eps`` (1 $/MWh here) exceeds half the
bus-price pair-sum.  ``piecewise_direct`` -- the PLP-replication mode --
has no loss rows and burns energy in fictitious losses in that same
negative-price setting, which is why it is NOT the plp2gtopt default.
"""

from __future__ import annotations

import json
import resource
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from plp2gtopt.line_writer import DEFAULT_LOSS_TANGENTS
from plp2gtopt.plp2gtopt import convert_plp_case

_SRC = Path(__file__).parent.parent.parent / "cases" / "plp_min_2bus"

_R_OHM = 20.0
_V_KV = 220.0
_FMAX = 150.0
_TRAMOS = 3
_C = _R_OHM / (_V_KV * _V_KV)
_TOL = 1e-3

_CNFLI = f"""# Archivo de configuracion de lineas (plpcnfli.dat)
# Num.Lineas   Modela Perdidas  Perd.en.ERM   Ang. de Ref.
         1          T             'M'         1000.d0
# Nombre  F.Max.A-B  F.Max.B-A  Barra A  Barra B  Tension  R(Ohm)  X(ohm)  Mod.Perd.  Num.Tramos  Operativa
'Bus1->Bus2'  {_FMAX}  {_FMAX}  1  2  {_V_KV}  {_R_OHM}  10.000  T  {_TRAMOS}  T
"""


def _core_on() -> None:
    resource.setrlimit(
        resource.RLIMIT_CORE, (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
    )


@pytest.fixture(scope="module")
def highs_bin(gtopt_bin: str) -> str:
    """The gtopt binary, skipping when no HiGHS plugin is loadable."""
    out = subprocess.run(
        [gtopt_bin, "--solvers"], capture_output=True, text=True, check=False
    )
    listing = out.stdout.split("Available LP solvers:")[-1]
    if "highs" not in listing:
        pytest.skip("HiGHS solver plugin not available")
    return gtopt_bin


def _solve(
    tmp_path: Path,
    gtopt_bin: str,
    *,
    mode: str,
    loss_tangents: str | int = DEFAULT_LOSS_TANGENTS,
    negative_prices: bool = False,
) -> tuple[dict, pd.DataFrame]:
    """Convert the lossy 2-bus case, optionally inject surplus, solve."""
    plp_dir = tmp_path / "plp"
    shutil.copytree(_SRC, plp_dir)
    (plp_dir / "plpcnfli.dat").write_text(_CNFLI, encoding="utf-8")
    out_dir = tmp_path / "gtopt"
    out_dir.mkdir()
    case_json = out_dir / "case.json"
    convert_plp_case(
        {
            "input_dir": plp_dir,
            "output_dir": out_dir,
            "output_file": case_json,
            "hydrologies": "1",
            "method": "mono",
            "model_options": {"line_losses_mode": mode, "loss_cost_eps": 1.0},
            "loss_tangents": loss_tangents,
        }
    )
    case = json.loads(case_json.read_text(encoding="utf-8"))
    if negative_prices:
        # A curtailable -0.4 $/MWh surplus at BOTH buses makes it the
        # marginal unit everywhere: both bus prices = -0.4, so dumping
        # 1 MWh into a loss column earns 0.4 $ (< loss_cost_eps = 1).
        gens = case["system"]["generator_array"]
        for i, bus in enumerate((1, 2)):
            gens.append(
                {
                    "uid": 900 + i,
                    "name": f"surplus{bus}",
                    "bus": bus,
                    "gcost": -0.4,
                    "pmax": 500.0,
                    "capacity": 500.0,
                }
            )
        case_json.write_text(json.dumps(case), encoding="utf-8")

    run = subprocess.run(
        [gtopt_bin, case_json.name, "--solver", "highs"],
        cwd=out_dir,
        capture_output=True,
        text=True,
        check=False,
        preexec_fn=_core_on,
    )
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]

    line_dir = out_dir / "results" / "Line"

    def _per_block(name: str) -> pd.Series:
        df = pd.read_parquet(line_dir / f"{name}.parquet")
        return df.groupby("block")["value"].sum()

    # Zero-valued cells are not written: reindex on the case's blocks.
    blocks = pd.Index(
        sorted(b["uid"] for b in case["simulation"]["block_array"]), name="block"
    )
    flows = _per_block("flow_sol").reindex(blocks, fill_value=0.0)
    losses = _per_block("loss_sol").reindex(blocks, fill_value=0.0)
    return case, pd.DataFrame({"flow": flows, "loss": losses})


def _physical(flow: pd.Series) -> pd.Series:
    return _C * flow * flow


@pytest.mark.integration
@pytest.mark.parametrize(("spec", "k"), [("tramos", _TRAMOS), ("double", 6)])
def test_tangent_loss_within_envelope(tmp_path, highs_bin, spec, k):
    """Positive prices: loss on the K-tangent envelope, below c*f^2."""
    case, sol = _solve(
        tmp_path, highs_bin, mode="tangent_signed_flow", loss_tangents=spec
    )
    line = case["system"]["line_array"][0]
    assert line["loss_segments"] == k
    assert line["use_line_losses"] is True

    phys = _physical(sol["flow"])
    assert (sol["flow"].abs() > 1.0).all(), "line should carry flow"
    assert (sol["loss"] <= phys + _TOL).all(), sol
    assert (sol["loss"] >= phys - _C * _FMAX**2 / k**2 - _TOL).all(), sol


@pytest.mark.integration
@pytest.mark.parametrize("spec", ["tramos", "double"])
def test_tangent_no_fictitious_loss_negative_prices(tmp_path, highs_bin, spec):
    """Negative bus prices: tangent_signed_flow never exceeds c*f^2."""
    _, sol = _solve(
        tmp_path,
        highs_bin,
        mode="tangent_signed_flow",
        loss_tangents=spec,
        negative_prices=True,
    )
    assert (sol["loss"] <= _physical(sol["flow"]) + _TOL).all(), sol


@pytest.mark.integration
def test_piecewise_direct_burns_fictitious_loss(tmp_path, highs_bin):
    """Negative bus prices: piecewise_direct dumps energy into losses.

    Documents why plp2gtopt does not default to the PLP-replication mode:
    with no loss rows, opposite-direction segment flows on the same line
    dissipate energy the physics cannot explain.
    """
    case, sol = _solve(
        tmp_path, highs_bin, mode="piecewise_direct", negative_prices=True
    )
    assert case["system"]["line_array"][0]["loss_segments"] == _TRAMOS
    fictitious = sol["loss"] - _physical(sol["flow"])
    assert (fictitious > 1.0).all(), sol
