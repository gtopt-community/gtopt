"""Warning / fallback branches of :mod:`plp2gtopt.junction_writer`.

Each case copies ``plp_hydro_4b`` and overrides optional hydro input
files with entries that reference unknown centrals / reservoirs or carry
degenerate segments; the conversion must still succeed and skip them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from plp2gtopt.plp2gtopt import convert_plp_case

_CASES = Path(__file__).resolve().parents[2] / "cases"


def _filemb(entries: list[tuple[str, list[tuple[float, float, float]], str]]) -> str:
    out = f"# n\n{len(entries)}\n"
    for emb, segs, cen in entries:
        out += f"'{emb}'\n5.0\n{len(segs)}\n"
        for i, (vol, slope, const) in enumerate(segs, 1):
            out += f"  {i}  {vol}  {slope}  {const}\n"
        out += f"'{cen}'\n"
    return out


_TWO = [(0.0, 0.003, 1.8), (500.0, 0.001, 4.8)]

_BAD_REFS = {
    "plpfilemb.dat": _filemb(
        [
            ("Ghost", _TWO, "TurbineA"),
            ("LakeA", _TWO, "Ghost"),
            ("LakeA", _TWO, "LakeA"),
            ("LakeA", [(0.0, 0.003, 1.8)], "TurbineA"),
            ("LakeA", [(0.0, 0.003, 1.8), (50.0, 0.001, 4.8)], "TurbineA"),
            ("LakeA", [(0.0, 0.0, 0.0), (500.0, 0.001, 4.8)], "TurbineA"),
        ]
    ),
    "plpcenre.dat": (
        "# n\n3\n'Ghost'\n'LakeA'\n1.0\n1\n 1 0.0 0.001 1.0\n"
        "'LakeA'\n'Ghost'\n1.0\n1\n 1 0.0 0.001 1.0\n"
        "'LakeA'\n'LakeA'\n1.0\n2\n 1 0.0 0.001 1.0\n 2 600.0 0.0005 1.3\n"
    ),
    "plpcenpmax.dat": (
        "# n\n2\n'Ghost'\n'LakeA'\n1\n 0.0 0.05 60.0\n"
        "'LakeA'\n'Ghost'\n1\n 0.0 0.05 60.0\n"
    ),
    "plpralco.dat": "'Ghost'\n1\n0.0  80.0  1.0d-5\n",
    "plpextrac.dat": (
        "# n\n3\n'Ghost'\n10.0\n'TurbineA'\n'LakeA'\n10.0\n'Ghost'\n"
        "'LakeA'\n10.0\n'TurbineA'\n"
    ),
}

_CENFI = {
    "plpcenfi.dat": (
        "# n\n4\n'Ghost'\n'LakeA'\n0.001 1.0\n'TurbineA'\n'Ghost'\n0.001 1.0\n"
        "'TurbineA'\n'LakeA'\n0.001 1.0\n"
        "'LakeA'\n'LakeA'\n2\n 1 0.0 0.001 1.0\n 2 600.0 0.0005 1.3\n"
    ),
}

_MANEM = {
    "plpmanem.dat": (
        "# n\n2\n'LakeA'\n 2\n 01 001 0.0002 0.0011\n 01 002 0.0 0.0011\n'Ghost'\n 0\n"
    ),
}


def _convert(
    tmp_path: Path, files: dict[str, str], drop: tuple[str, ...] = (), **extra: Any
) -> dict[str, Any]:
    src = tmp_path / "plp"
    shutil.copytree(_CASES / "plp_hydro_4b", src)
    for name in drop:
        (src / name).unlink()
    for name, text in files.items():
        (src / name).write_text(text, encoding="latin-1")
    out = tmp_path / "out"
    out.mkdir()
    opts: dict[str, Any] = {
        "input_dir": src,
        "output_dir": out,
        "output_file": out / "case.json",
        "hydrologies": "1",
        "last_stage": -1,
        "last_time": -1,
        "compression": "snappy",
        "probability_factors": None,
        "discount_rate": 0.0,
        "management_factor": 0.0,
    }
    opts.update(extra)
    convert_plp_case(opts)
    system: dict[str, Any] = json.loads(
        (out / "case.json").read_text(encoding="utf-8")
    )["system"]
    return system


@pytest.mark.parametrize("legacy", [False, True])
def test_bad_references_skipped(tmp_path: Path, legacy: bool) -> None:
    system = _convert(tmp_path, _BAD_REFS, plp_legacy=legacy)
    names = {w["name"] for w in system["waterway_array"]}
    assert not any("Ghost" in n for n in names)
    lake = next(r for r in system["reservoir_array"] if r["name"] == "LakeA")
    assert lake["name"] == "LakeA"


def test_cenfi_seepage(tmp_path: Path) -> None:
    system = _convert(tmp_path, _CENFI, drop=("plpfilemb.dat",))
    seeps = system.get("reservoir_seepage_array", [])
    lake = next(r for r in system["reservoir_array"] if r["name"] == "LakeA")
    assert seeps or lake.get("seepage")


@pytest.mark.parametrize(
    "extra",
    [
        {"soft_storage_bounds": True},
        {"soft_storage_bounds": True, "auto_water_fail_cost": True},
    ],
)
def test_manem_rerouted_to_soft_emin(tmp_path: Path, extra: dict[str, Any]) -> None:
    system = _convert(tmp_path, _MANEM, **extra)
    lake = next(r for r in system["reservoir_array"] if r["name"] == "LakeA")
    assert lake.get("soft_emin")
    assert lake["efin_cost"] > 0


def test_minembh_zero_cost_and_ar1(tmp_path: Path) -> None:
    files = {
        "plpminembh.dat": ("# n\n1\n'LakeA'\n1\n     1   150.0   0.0\n"),
    }
    system = _convert(tmp_path, files, soft_emin_cost=0.0, inflow_model="ar1")
    lake = next(r for r in system["reservoir_array"] if r["name"] == "LakeA")
    assert not lake.get("soft_emin")


def test_pf_curve_helpers() -> None:
    # pylint: disable=import-outside-toplevel
    from plp2gtopt.junction_writer import _eval_pf_segments, _merge_pf_curves_min

    segs = [
        {"volume": 0.0, "slope": 0.01, "constant": 1.0},
        {"volume": 100.0, "slope": 0.0, "constant": 3.0},
    ]
    assert _eval_pf_segments([], 5.0) == 0.0
    assert _eval_pf_segments(segs, 50.0) == pytest.approx(1.5)
    assert _eval_pf_segments(segs, 150.0) == pytest.approx(3.0)
    other = [
        {"volume": 0.0, "slope": 0.0, "constant": 2.0},
        {"volume": 500.0, "slope": 0.0, "constant": 0.5},
    ]
    assert _merge_pf_curves_min([], other) == other
    assert _merge_pf_curves_min(segs, []) == segs
    merged = _merge_pf_curves_min(segs, other)
    assert merged[0] == segs[0]
    assert merged[1] == {"volume": 100.0, "slope": 0.0, "constant": 2.0}


def test_json_outside_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("x", encoding="utf-8")
    target = tmp_path / "elsewhere" / "case.json"
    target.parent.mkdir()
    opts: dict[str, Any] = {
        "input_dir": _CASES / "plp_hydro_4b",
        "output_dir": out,
        "output_file": target,
        "hydrologies": "1",
        "last_stage": -1,
        "last_time": -1,
        "compression": "snappy",
        "probability_factors": None,
        "discount_rate": 0.0,
        "management_factor": 0.0,
    }
    convert_plp_case(opts)
    text = target.read_text(encoding="utf-8")
    assert ".out.tmp." not in text and not (out / "stale.txt").exists()
