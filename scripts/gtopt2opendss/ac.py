# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""AC power-flow reference through the DSS C-API (``opendssdirect.py``).

Optional: ``pip install opendssdirect.py`` (the ``opendss`` extra of
gtopt-scripts).  Everything here takes the engine module as a
parameter (default: import ``opendssdirect``) so the logic is testable
without the binary engine.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AcSolution:
    """AC power-flow results of one snapshot."""

    converged: bool
    vpu: dict[str, float] = field(default_factory=dict)  # mean over phases
    vpu_min: dict[str, float] = field(default_factory=dict)
    losses_kw: float = 0.0
    losses_kvar: float = 0.0
    source_kw: float = 0.0
    source_kvar: float = 0.0


def load_engine() -> Any:
    """Import ``opendssdirect`` (raises ImportError when missing)."""
    import opendssdirect  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return opendssdirect


def solve_file(path: str | Path, engine: Any = None) -> AcSolution:
    """Compile and solve a .dss master file; return the AC solution."""
    dss = engine if engine is not None else load_engine()
    cwd = os.getcwd()  # OpenDSS "Compile" changes the working directory
    try:
        dss.Text.Command("Clear")
        dss.Text.Command(f'Compile "{Path(path).resolve()}"')
        dss.Solution.Solve()
        return _collect(dss)
    finally:
        os.chdir(cwd)


def solve_text(script: str, engine: Any = None) -> AcSolution:
    """Solve a .dss script given as text (written to a temp file)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "master.dss"
        p.write_text(script, encoding="utf-8")
        return solve_file(p, engine)


def _collect(dss: Any) -> AcSolution:
    sol = AcSolution(converged=bool(dss.Solution.Converged()))
    names = dss.Circuit.AllNodeNames()
    mags = dss.Circuit.AllBusMagPu()
    per_bus: dict[str, list[float]] = {}
    for node, v in zip(names, mags):
        bus = str(node).split(".", maxsplit=1)[0].lower()
        per_bus.setdefault(bus, []).append(float(v))
    for bus, vs in per_bus.items():
        sol.vpu[bus] = sum(vs) / len(vs)
        sol.vpu_min[bus] = min(vs)
    losses = dss.Circuit.Losses()  # W, var
    sol.losses_kw = float(losses[0]) / 1000.0
    sol.losses_kvar = float(losses[1]) / 1000.0
    total = dss.Circuit.TotalPower()  # kW, kvar (negative = supplied)
    sol.source_kw = -float(total[0])
    sol.source_kvar = -float(total[1])
    return sol


@dataclass
class Comparison:
    """Error statistics of a voltage profile against an AC reference."""

    max_abs_dv: float
    mean_abs_dv: float
    worst_bus: str
    n_buses: int
    loss_kw_ref: float
    loss_kw_model: float | None

    @property
    def loss_rel_err(self) -> float | None:
        """Relative loss error (None when the model has no loss figure)."""
        if self.loss_kw_model is None or self.loss_kw_ref == 0:
            return None
        return (self.loss_kw_model - self.loss_kw_ref) / self.loss_kw_ref


def compare_voltages(
    model_vpu: dict[str, float],
    ref: AcSolution,
    *,
    model_losses_kw: float | None = None,
    skip: set[str] | None = None,
) -> Comparison:
    """Compare bus voltage magnitudes (pu) against the AC reference."""
    skip = skip or set()
    diffs = {
        b: abs(v - ref.vpu[b.lower()])
        for b, v in model_vpu.items()
        if b.lower() in ref.vpu and b not in skip
    }
    if not diffs:
        raise ValueError("no common buses between the model and the reference")
    worst = max(diffs, key=lambda k: diffs[k])
    return Comparison(
        max_abs_dv=diffs[worst],
        mean_abs_dv=sum(diffs.values()) / len(diffs),
        worst_bus=worst,
        n_buses=len(diffs),
        loss_kw_ref=ref.losses_kw,
        loss_kw_model=model_losses_kw,
    )


def vpu_from_vsq(vsq: dict[str, float]) -> dict[str, float]:
    """Voltage magnitude from gtopt's squared-voltage output ``w = |V|^2``."""
    return {b: math.sqrt(max(w, 0.0)) for b, w in vsq.items()}
