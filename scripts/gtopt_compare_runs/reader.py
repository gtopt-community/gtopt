# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read one gtopt output directory for a run-vs-run comparison.

A :class:`Run` is an output directory plus the planning that names its
elements and defines its time grid: ``--planning`` when given, else the
``planning.json`` gtopt (and the results importers) write into the output
directory, else the case JSON one level up.  Without a planning the
elements are named ``uid:<N>`` and the grid comes from the data.

:meth:`Run.cells` returns one stream as a long frame keyed by element name
and by scenario / stage / block **position** (not uid), with the weight of
each cell (block hours x scenario probability), so two runs of the same
system converted by different tools line up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gtopt_shared.case_reader import TimeGrid
from gtopt_shared.results_reader import GtoptResults
from gtopt_shared.results_writer import find_planning_json

logger = logging.getLogger(__name__)

#: Per-block state streams: compared by their time-weighted mean, not summed.
STATE_FIELDS = frozenset(
    {
        "energy_sol",
        "efin_sol",
        "eini_sol",
        "vfin_sol",
        "vini_sol",
        "volume_sol",
        "status_sol",
        "theta_sol",
    }
)

_SHARD = re.compile(r"^(?P<field>.+?)(?:_s\d+_p\d+)?\.(?:parquet|csv(?:\.zst|\.gz)?)$")


def field_kind(fld: str) -> str:
    """``"mean"`` for prices, duals and states; ``"sum"`` for flows of energy."""
    if fld.endswith("_dual") or fld in STATE_FIELDS:
        return "mean"
    return "sum"


def array_of(cls: str) -> str:
    """``system`` array of a class directory (``GeneratorProfile`` ->
    ``generator_profile_array``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls).lower() + "_array"


def _load_planning(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("%s: unreadable planning (%s)", path, exc)
        return None
    return data if isinstance(data, dict) else None


def resolve_planning(out_dir: Path, planning: Path | None) -> dict[str, Any] | None:
    """The planning of an output directory (see module doc)."""
    if planning is not None:
        return _load_planning(Path(planning))
    own = out_dir / "planning.json"
    if own.is_file():
        return _load_planning(own)
    try:
        return _load_planning(find_planning_json(out_dir.parent))
    except FileNotFoundError:
        return None


def simulation_from_data(out_dir: Path) -> dict[str, Any]:
    """A ``simulation`` block rebuilt from the uids of the streams.

    One-hour blocks, equally likely scenarios and the stages the rows
    name -- enough to align two runs that carry no planning.
    """
    probe = GtoptResults(out_dir, TimeGrid.from_planning({}))
    rows: list[pd.DataFrame] = []
    for cdir in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        for p in cdir.iterdir():
            m = _SHARD.match(p.name)
            if m:
                df = probe.table(cdir.name, m.group("field"))
                if df is not None and not df.empty:
                    rows.append(df)
    if not rows:
        return {}
    df = pd.concat(rows, ignore_index=True)
    scen = sorted(int(x) for x in df.get("scenario", pd.Series([1])).unique())
    blk = df[df.get("block", 0) != 0] if "block" in df.columns else df.iloc[0:0]
    pairs = (
        blk[["stage", "block"]].drop_duplicates().sort_values(["stage", "block"])
        if "stage" in blk.columns
        else pd.DataFrame(columns=["stage", "block"])
    )
    blocks = [int(b) for b in pairs["block"]]
    stages = []
    first = 0
    for st, sub in pairs.groupby("stage", sort=True):
        stages.append({"uid": int(st), "first_block": first, "count_block": len(sub)})
        first += len(sub)
    return {
        "block_array": [{"uid": b, "duration": 1.0} for b in blocks],
        "stage_array": stages,
        "scenario_array": [
            {"uid": u, "probability_factor": 1.0 / len(scen)} for u in scen
        ],
    }


@dataclass
class Run:
    """One gtopt output directory and its planning."""

    out_dir: Path
    planning: dict[str, Any] = field(default_factory=dict)
    label: str = ""

    @classmethod
    def load(
        cls, out_dir: str | Path, planning: str | Path | None = None, label: str = ""
    ) -> Run:
        """Open ``out_dir`` (see module doc for the planning lookup)."""
        out = Path(out_dir)
        if not out.is_dir():
            raise FileNotFoundError(f"{out}: not a directory")
        plan = resolve_planning(out, Path(planning) if planning else None)
        if plan is None:
            logger.warning("%s: no planning found; elements are named by uid", out)
            plan = {}
        if not plan.get("simulation"):
            plan = {**plan, "simulation": simulation_from_data(out)}
        return cls(out, plan, label or out.name)

    def __post_init__(self) -> None:
        """Bind the grid and the stream reader."""
        self.grid = TimeGrid.from_planning(self.planning)
        self.reader = GtoptResults(self.out_dir, self.grid)
        g = self.grid
        probs = np.asarray(g.probabilities, dtype=float)
        total = probs.sum()
        self._prob = (
            probs / total if total > 0 else np.full(g.n_scenarios, 1 / g.n_scenarios)
        )
        self._scen_pos = {u: k for k, u in enumerate(g.scenario_uids)}
        self._block_pos = {u: k for k, u in enumerate(g.block_uids)}
        self._stage_pos = {st.uid: k for k, st in enumerate(g.stages)}
        dur = np.asarray(g.durations, dtype=float)
        self._dur = dur
        self._stage_hours = np.array([dur[list(st.blocks)].sum() for st in g.stages])
        self._block_stage = {b: int(k) for b, k in enumerate(g.stage_of_block())}

    @property
    def weights(self) -> np.ndarray:
        """Normalised scenario probabilities, by scenario position."""
        return self._prob

    # ── discovery ──────────────────────────────────────────────────────
    def streams(self) -> list[tuple[str, str]]:
        """Every ``(Class, field)`` stream in the directory."""
        out: set[tuple[str, str]] = set()
        for cdir in sorted(p for p in self.out_dir.iterdir() if p.is_dir()):
            if cdir.name in ("logs", "solvers") or cdir.name[:1].islower():
                continue
            for p in cdir.iterdir():
                m = _SHARD.match(p.name)
                if m:
                    out.add((cdir.name, m.group("field")))
        return sorted(out)

    def elements(self, cls: str) -> list[dict[str, Any]]:
        """Elements of the class in the planning."""
        system = self.planning.get("system", {}) or {}
        return list(system.get(array_of(cls), []) or [])

    def names(self, cls: str) -> dict[int, str]:
        """``uid -> name`` of a class (``uid:<N>`` when unnamed)."""
        out: dict[int, str] = {}
        for el in self.elements(cls):
            uid = el.get("uid")
            if isinstance(uid, int):
                out[uid] = str(el.get("name", f"uid:{uid}"))
        return out

    def types(self) -> dict[str, str]:
        """Generator name -> technology (``type``, else ``other``)."""
        return {
            str(g.get("name")): str(g.get("type") or "other")
            for g in self.elements("Generator")
        }

    # ── data ───────────────────────────────────────────────────────────
    def cells(self, cls: str, fld: str) -> pd.DataFrame:
        """Long frame ``name, s, k, b, value, weight`` of one stream.

        ``s`` / ``k`` / ``b`` are 0-based scenario / stage / block
        positions (``b = -1`` for per-stage streams); ``weight`` is the
        cell's hours x probability.  Cells of uids, scenarios or blocks
        outside the planning are dropped (with a debug log).
        """
        cols = ["name", "s", "k", "b", "value", "weight"]
        df = self.reader.table(cls, fld)
        if df is None or df.empty or not {"scenario", "stage", "block"} <= set(df):
            return pd.DataFrame(columns=cols)
        names = self.names(cls)
        df = df.copy()
        s = df["scenario"].map(self._scen_pos)
        b = df["block"].map(self._block_pos)
        per_stage = bool(b.isna().all())
        if per_stage:
            k = df["stage"].map(self._stage_pos)
            b = pd.Series(-1, index=df.index)
        else:
            k = b.map(self._block_stage)
        ok = s.notna() & k.notna() & b.notna()
        if not ok.all():
            logger.debug(
                "%s %s/%s: %d cells off the grid", self.label, cls, fld, (~ok).sum()
            )
        out = pd.DataFrame(
            {
                "name": [names.get(int(u), f"uid:{int(u)}") for u in df.loc[ok, "uid"]],
                "s": s[ok].astype(int).to_numpy(),
                "k": k[ok].astype(int).to_numpy(),
                "b": b[ok].astype(int).to_numpy(),
                "value": df.loc[ok, "value"].astype(float).to_numpy(),
            }
        )
        hours = (
            self._stage_hours[out["k"].to_numpy()]
            if per_stage
            else self._dur[out["b"].to_numpy()]
        )
        out["weight"] = hours * self._prob[out["s"].to_numpy()]
        return out[cols]

    def total_weight(self, per_stage: bool) -> float:
        """Σ hours x probability over the grid (the mean's denominator)."""
        hours = self._stage_hours.sum() if per_stage else self._dur.sum()
        return float(hours)

    def objective(self) -> float | None:
        """Expected total cost: Σ ``obj_value`` of the first-phase cells.

        ``solution.csv`` holds one row per (scene, phase); a phase's
        objective includes the cost-to-go of the later phases, so the rows
        of the first phase add up to the expected total.
        """
        path = self.out_dir / "solution.csv"
        if not path.is_file():
            return None
        try:
            df = pd.read_csv(path)
        except (OSError, ValueError):
            return None
        if "obj_value" not in df.columns or df.empty:
            return None
        if "phase" in df.columns:
            df = df[df["phase"] == df["phase"].min()]
        return float(pd.to_numeric(df["obj_value"], errors="coerce").fillna(0).sum())
