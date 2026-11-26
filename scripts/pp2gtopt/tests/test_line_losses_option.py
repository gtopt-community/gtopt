# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""pp2gtopt line-loss options: physical branch units and model options."""

import json
import math

import pandapower.networks as pn
import pytest

from pp2gtopt.convert import (
    LineLossOptions,
    _build_lines,
    _impedance_fields,
    _line_tmax,
    _loading_percent,
    convert,
)
from pp2gtopt.main import main


def test_impedance_fields_per_unit_without_losses():
    assert _impedance_fields(0.1, 0.02, 345.0, 100.0, False) == {"reactance": 0.1}


def test_impedance_fields_physical_with_losses():
    out = _impedance_fields(0.1, 0.02, 100.0, 100.0, True)
    # z_base = 100 Ω
    assert out == {"voltage": 100.0, "reactance": 10.0, "resistance": 2.0}


def test_line_tmax_scales_with_loading():
    base = _line_tmax(0.25, 345.0)
    assert _line_tmax(0.25, 345.0, 80.0) == pytest.approx(round(base * 0.8, 1), 0.01)
    assert _line_tmax(math.inf, 345.0, 80.0) == 9999


@pytest.mark.parametrize(
    ("value", "expected"),
    [(80.0, 80.0), (None, 100.0), (float("nan"), 100.0), (0.0, 100.0)],
)
def test_loading_percent(value, expected):
    assert _loading_percent({"max_loading_percent": value}) == expected


def test_build_lines_losses_keep_flow_scaling():
    """Physical units keep V²/X equal to base_mva / x_pu on every branch."""
    net = pn.case14()  # lines and transformers
    pu = {ln["name"]: ln for ln in _build_lines(net, 100.0)}
    phys = {ln["name"]: ln for ln in _build_lines(net, 100.0, losses=True)}
    assert pu.keys() == phys.keys()
    for name, ln in phys.items():
        assert ln["voltage"] > 0
        assert "resistance" in ln
        assert ln["voltage"] ** 2 / ln["reactance"] == pytest.approx(
            100.0 / pu[name]["reactance"], rel=1e-4
        )
    assert any(ln["resistance"] > 0 for ln in phys.values())
    assert any(ln.get("type") == "transformer" for ln in phys.values())


def test_convert_emits_loss_options(tmp_path):
    net = pn.case9()
    out = tmp_path / "c9.json"
    data = convert(
        out,
        net=net,
        name="c9",
        solver_type="monolithic",
        losses=LineLossOptions(
            line_losses_mode="tangent_signed_flow",
            loss_segments=6,
            line_thermal_limit_mode="sending",
        ),
    )
    mo = data["options"]["model_options"]
    assert mo["line_losses_mode"] == "tangent_signed_flow"
    assert mo["loss_segments"] == 6
    assert mo["line_thermal_limit_mode"] == "sending"
    lines = data["system"]["line_array"]
    assert all(ln["voltage"] == 345.0 for ln in lines)
    assert json.loads(out.read_text())["options"]["model_options"] == mo


def test_convert_none_mode_stays_per_unit(tmp_path):
    data = convert(
        tmp_path / "c9.json",
        net=pn.case9(),
        name="c9",
        solver_type="monolithic",
        losses=LineLossOptions(line_losses_mode="none"),
    )
    assert data["options"]["model_options"]["line_losses_mode"] == "none"
    assert all("voltage" not in ln for ln in data["system"]["line_array"])


def test_convert_default_has_no_loss_options(tmp_path):
    data = convert(tmp_path / "c9.json", net=pn.case9(), name="c9")
    mo = data["options"]["model_options"]
    for key in ("line_losses_mode", "loss_segments", "line_thermal_limit_mode"):
        assert key not in mo


def test_cli_loss_flags(tmp_path):
    out = tmp_path / "c9.json"
    rc = main(
        [
            "-n",
            "case9",
            "-o",
            str(out),
            "--no-check",
            "--line-losses-mode",
            "bidirectional",
            "--loss-segments",
            "4",
            "--line-thermal-limit-mode",
            "full",
        ]
    )
    assert rc == 0
    mo = json.loads(out.read_text())["options"]["model_options"]
    assert mo["line_losses_mode"] == "bidirectional"
    assert mo["loss_segments"] == 4
    assert mo["line_thermal_limit_mode"] == "full"


def test_line_loss_options_defaults():
    opts = LineLossOptions()
    assert not opts.physical_units
    assert not opts.model_options()
    assert LineLossOptions(line_losses_mode="piecewise").physical_units
