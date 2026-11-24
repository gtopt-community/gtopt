"""Network augmentations built in an AEMO ISP development path (ODP).

The 2026 ISP PLEXOS model sizes REZ and flow-path augmentations in its
long-term plan: an augmentation is a ``Generator`` in a ``Network
Augmentation …`` category with ``Units`` 0, ``Max Capacity`` 1 MW and a
dated ``Max Units Built``, and a REZ export / import (or group) limit
carries it as ``−1 × Installed Capacity``.  A fixed-fleet dispatch week
folds the XML ``Units`` (0), so the limit stays at its pre-augmentation
RHS — e.g. ``ExportLimit_N7_Tumut`` is 0 MW until something is built.

With a published ISP outlook workbook (``2026 ISP - <scenario> -
Core.xlsx``, user-supplied, read only) the augmentation MW built in a
candidate development path (default CDP4, the ODP) for the week's
financial year is added to the folded capacity:

1. **Timings** — an augmentation whose option name (object name without
   the ``<REZ>_`` prefix and the `` Augmentation`` suffix) is a ``Line``
   of the CDP in the *Timings* sheet is fully built (``Max Units Built ×
   Max Capacity``) from 1 July of the financial year before ``StartFYE``.
2. **REZ capacity** — any other augmentation that is buildable on the
   start day (``Max Units Built`` > 0 live) and appears in a REZ export
   limit is sized to the smallest build letting the REZ's installed
   generation of that CDP and financial year (*REZ Generation
   Capacity*, electrolysers excluded) export at full output:
   ``need = max(0, G_REZ − min_t (RHS(t) − folded capacity))``, filled
   in name order and capped at each object's ``Max Units Built``.  The
   workbook does not publish per-object augmentation MW, so this is the
   minimal build consistent with the ODP fleet, not a copy of the
   PLEXOS build.

Everything else (DNSP augmentations, group-REZ objects, augmentations
used only in import / group limits, objects not buildable yet) keeps the
XML ``Units`` and is listed as unmatched in the stats.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .aemo_isp_constraints import CaseView, ConstraintIndex, ConstraintOptions

logger = logging.getLogger(__name__)

#: PLEXOS category prefix of the augmentation generators.
AUGMENTATION_CATEGORY = "Network Augmentation"
DEFAULT_CDP = "CDP4"


@dataclass
class OdpOutlook:
    """The parts of an ISP outlook workbook used for one CDP."""

    cdp: str
    #: option (Timings ``Line``) → ``StartFYE`` (financial year end)
    timings: dict[str, int] = field(default_factory=dict)
    #: Timings ``Line`` → project name (``LineName``)
    projects: dict[str, str] = field(default_factory=dict)
    #: (REZ id, FY label ``2027-28``) → installed generation MW
    rez_capacity: dict[tuple[str, str], float] = field(default_factory=dict)
    #: project → (forward / export MW, reverse / import MW) as published
    details: dict[str, tuple[Any, Any]] = field(default_factory=dict)


def fy_label(day: date) -> str:
    """Australian financial year of ``day`` as ``YYYY-YY``."""
    y = day.year if day.month >= 7 else day.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def cdp_matches(label: Any, cdp: str) -> bool:
    """``CDP4`` matches ``CDP4`` and ``CDP4 (ODP)`` but not ``CDP40``."""
    if not isinstance(label, str):
        return False
    return re.fullmatch(re.escape(cdp) + r"(\s.*)?", label.strip()) is not None


def _rows(ws: Any, first: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Header (the row whose first cell is ``first``) and the rows below."""
    header: list[str] | None = None
    body: list[tuple[Any, ...]] = []
    for row in ws.iter_rows(values_only=True):
        if header is None:
            if row and row[0] == first:
                header = [str(c).strip() if c is not None else "" for c in row]
            continue
        body.append(row)
    if header is None:
        raise ValueError(f"sheet {ws.title!r}: no header row starting {first!r}")
    return header, body


def load_outlook(path: Path, cdp: str = DEFAULT_CDP) -> OdpOutlook:
    """Read the Timings, REZ Generation Capacity and Augmentation details."""
    import openpyxl  # pylint: disable=import-outside-toplevel

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        out = OdpOutlook(cdp=cdp)
        header, body = _rows(wb["Timings"], "Scenario")
        ic, il, iy, ip = (
            header.index(k) for k in ("CDP", "Line", "StartFYE", "LineName")
        )
        for r in body:
            if cdp_matches(r[ic], cdp) and r[il] and r[iy] is not None:
                out.timings[str(r[il]).strip()] = int(r[iy])
                out.projects[str(r[il]).strip()] = str(r[ip] or "").strip()
        header, body = _rows(wb["REZ Generation Capacity"], "CDP")
        ir, it = header.index("REZ"), header.index("Technology")
        fys = [(j, h) for j, h in enumerate(header) if re.fullmatch(r"\d{4}-\d{2}", h)]
        cap: dict[tuple[str, str], float] = defaultdict(float)
        for r in body:
            if not cdp_matches(r[0], cdp) or not r[ir]:
                continue
            if "electrolyser" in str(r[it]).lower():
                continue
            for j, fy in fys:
                v = r[j]
                if isinstance(v, (int, float)):
                    cap[(str(r[ir]).strip(), fy)] += float(v)
        out.rez_capacity = dict(cap)
        if "Augmentation details" in wb.sheetnames:
            _h, body = _rows(wb["Augmentation details"], "Project")
            for r in body:
                if r and isinstance(r[0], str) and r[0] != "Project":
                    out.details[r[0].strip()] = (r[3], r[4])
    finally:
        wb.close()
    if not out.timings and not out.rez_capacity:
        raise ValueError(f"{path}: no Timings / REZ capacity rows for {cdp!r}")
    return out


def option_name(obj_name: str) -> str:
    """``N5_WNV-SNSW Option 1 - VNI West Augmentation`` → the option."""
    _, _, rest = obj_name.partition("_")
    rest = rest or obj_name
    return rest.removesuffix(" Augmentation").strip()


def rez_id(constraint_name: str) -> str:
    """``ExportLimit_N7_Tumut`` → ``N7``."""
    for pre in ("ExportLimit_", "ImportLimit_"):
        if constraint_name.startswith(pre):
            constraint_name = constraint_name[len(pre) :]
    return constraint_name.split("_", 1)[0]


def resolve_builds(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    view: CaseView,
    index: ConstraintIndex,
    opts: ConstraintOptions,
    stats: dict[str, Any],
) -> dict[int, float]:
    """Augmentation object id → MW built in the CDP by the start day.

    Fills ``view.aug_built`` and reports ``odp_augmentations`` (every
    object given a build, with the rule) and ``odp_unmatched`` in
    ``stats``.
    """
    # pylint: disable=import-outside-toplevel
    from .aemo_isp import _fill_nan_from_day_neighbours
    from .aemo_isp_constraints import REZ_CATEGORIES, classify, evaluate

    odp = opts.odp
    assert odp is not None
    res, day0 = view.res, view.day0
    fy = fy_label(day0)
    aug_cid = {
        cid for cid, name in res.cat.items() if name.startswith(AUGMENTATION_CATEGORY)
    }
    # augmentation objects per live constraint
    uses: dict[int, list[tuple[int, str]]] = defaultdict(list)  # aug → (con, side)
    for o in sorted(res.objects("Constraint"), key=lambda o: o.name):
        group = classify(res, index, o.object_id)
        if group in ("capacity_build", "no_coefficients", "company_vret", "emissions"):
            continue
        if res.scalar(o.object_id, "Include in LT Plan", day0, -1.0) == 0:
            continue
        side = REZ_CATEGORIES.get(res.category(o), "group")
        for parent, prop, _rows in index.coefs[o.object_id]:
            po = res.obj[parent]
            if prop == "Installed Capacity Coefficient" and po.category_id in aug_cid:
                uses[parent].append((o.object_id, side))

    def max_add(oid: int) -> float:
        mub = res.scalar(oid, "Max Units Built", day0, default=0.0) or 0.0
        cap = res.scalar(oid, "Max Capacity", day0, default=1.0) or 0.0
        return max(0.0, mub * cap)

    built: dict[int, float] = {}
    report: list[dict[str, Any]] = []
    unmatched: dict[str, str] = {}
    # 1. discrete options with a CDP timing
    for oid in sorted(uses, key=lambda i: res.obj[i].name):
        name = res.obj[oid].name
        opt = option_name(name)
        if opt not in odp.timings:
            continue
        fye = odp.timings[opt]
        in_service = date(fye - 1, 7, 1)
        mw = max_add(oid) if day0 >= in_service else 0.0
        if day0 >= in_service and mw <= 0.0:
            unmatched[name] = f"timing {fye} but Max Units Built not live"
        built[oid] = mw
        report.append(
            {
                "object": name,
                "rule": "timings",
                "option": opt,
                "project": odp.projects.get(opt, ""),
                "start_fye": fye,
                "fy": fy,
                "mw": mw,
            }
        )
    view.aug_built = dict(built)
    # 2. remaining buildable objects sized from the REZ capacity
    exports = sorted(
        {c for uses_ in uses.values() for c, side in uses_ if side == "export"},
        key=lambda c: res.obj[c].name,
    )
    for con in exports:
        cname = res.obj[con].name
        todo = [
            (p, prop_rows)
            for p, prop, prop_rows in index.coefs[con]
            if p in uses
            and p not in built
            and prop == "Installed Capacity Coefficient"
            and max_add(p) > 0.0
        ]
        if not todo:
            continue
        rez = rez_id(cname)
        g = odp.rez_capacity.get((rez, fy))
        if g is None:
            for p, _ in todo:
                unmatched.setdefault(
                    res.obj[p].name, f"REZ {rez} / FY {fy} not in the workbook"
                )
            continue
        ev = evaluate(view, index, opts, con, classify(res, index, con))
        rhs = res.hourly(con, "RHS", day0, view.days)
        if ev is None or ev.const is None or rhs is None:
            for p, _ in todo:
                unmatched.setdefault(res.obj[p].name, f"{cname}: no per-block RHS")
            continue
        limit = float(np.min(_fill_nan_from_day_neighbours(rhs) - ev.const))
        need = max(0.0, g - limit)
        for p, rows in sorted(todo, key=lambda t: res.obj[t[0]].name):
            coef = res.select_row(rows, day0)
            c = coef.value if coef is not None else 0.0
            if c >= 0.0:
                unmatched.setdefault(res.obj[p].name, f"{cname}: coefficient ≥ 0")
                continue
            mw = min(max_add(p), need / -c)
            need -= -c * mw
            built[p] = mw
            view.aug_built[p] = mw
            report.append(
                {
                    "object": res.obj[p].name,
                    "rule": "rez_capacity",
                    "constraint": cname,
                    "rez": rez,
                    "rez_capacity_mw": round(g, 3),
                    "limit_before_mw": round(limit, 3),
                    "fy": fy,
                    "mw": round(mw, 3),
                }
            )
        if need > 1e-6:
            logger.info("%s: ODP REZ capacity exceeds the buildable limit", cname)
    for oid, uses_ in uses.items():
        name = res.obj[oid].name
        if oid in built or name in unmatched:
            continue
        if max_add(oid) <= 0.0:
            unmatched[name] = f"not buildable on {day0} (Max Units Built)"
        else:
            sides = sorted({s for _, s in uses_})
            unmatched[name] = "no workbook timing; used in " + "/".join(sides)
    stats["odp_cdp"] = odp.cdp
    stats["odp_augmentations"] = sorted(report, key=lambda r: r["object"])
    stats["odp_unmatched"] = dict(sorted(unmatched.items()))
    stats["odp_added_mw"] = round(sum(built.values()), 3)
    return built
