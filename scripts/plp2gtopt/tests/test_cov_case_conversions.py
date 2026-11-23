"""Conversion-only runs of the small PLP cases under ``scripts/cases``.

The solver-backed integration tests (``-m integration``) also convert
these cases, but they are skipped without a ``gtopt`` binary.  These
tests run the converter alone (no solver) so the hydro / network /
irrigation writer paths are exercised in every test run, and check
structural invariants of the produced planning.
"""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from plp2gtopt.plp2gtopt import convert_plp_case

_main_mod = importlib.import_module("plp2gtopt.main")
_CASES = Path(__file__).resolve().parents[2] / "cases"


def _opts(case: str, tmp_path: Path, **extra: Any) -> dict[str, Any]:
    out_dir = tmp_path / case
    out_dir.mkdir(parents=True, exist_ok=True)
    opts: dict[str, Any] = {
        "input_dir": _CASES / case,
        "output_dir": out_dir,
        "output_file": out_dir / f"{case}.json",
        "hydrologies": "1",
        "last_stage": -1,
        "last_time": -1,
        "compression": "snappy",
        "probability_factors": None,
        "discount_rate": 0.0,
        "management_factor": 0.0,
    }
    opts.update(extra)
    return opts


def _convert(case: str, tmp_path: Path, **extra: Any) -> dict[str, Any]:
    opts = _opts(case, tmp_path, **extra)
    convert_plp_case(opts)
    data: dict[str, Any] = json.loads(opts["output_file"].read_text(encoding="utf-8"))
    return data


def _uids_unique(system: dict[str, Any]) -> None:
    for key, arr in system.items():
        if key.endswith("_array") and isinstance(arr, list):
            uids = [e["uid"] for e in arr if isinstance(e, dict) and "uid" in e]
            assert len(uids) == len(set(uids)), key


@pytest.mark.parametrize(
    "case",
    [
        "plp_min_hydro",
        "plp_min_hydro_ms",
        "plp_min_reservoir",
        "plp_min_mance",
        "plp_min_manli",
        "plp_min_irrigation",
        "plp_hydro_4b",
        "plp_bat_4b_24",
    ],
)
def test_convert_small_case(case: str, tmp_path: Path) -> None:
    data = _convert(case, tmp_path)
    system = data["system"]
    assert system["bus_array"]
    _uids_unique(system)


def _copy_case(case: str, dst: Path, drop: tuple[str, ...] = ()) -> Path:
    dst.mkdir(parents=True)
    for dat in (_CASES / case).glob("*.dat"):
        if dat.name not in drop:
            shutil.copy(dat, dst / dat.name)
    return dst


@pytest.mark.parametrize(
    ("drop", "extra", "expected"),
    [
        ((), [], {"laja", "maule"}),
        (("plpmaulen.dat",), ["-M", "sddp"], {"laja"}),
        (("plplajam.dat",), ["--no-irrigation-couplings"], {"maule"}),
    ],
)
def test_convert_irrigation_agreements(
    tmp_path: Path, drop: tuple[str, ...], extra: list[str], expected: set[str]
) -> None:
    """``--expand-water-rights`` in-process: Laja / Maule fragments emitted."""
    src = _copy_case("plp_min_irrigation", tmp_path / "plp", drop)
    out = tmp_path / "case"
    _main_mod.main([str(src), "-o", str(out), "--expand-water-rights", *extra])
    fragments = {p.name.split("_")[0] for p in out.glob("*_water_rights.json")}
    assert fragments == expected
    data = json.loads((out / "case.json").read_text(encoding="utf-8"))
    assert data["system"]["flow_right_array"]


_RICH_HYDRO_FILES = {
    "plpfilemb.dat": (
        "# Numero Embalses con filtraciones\n1\n# Nombre de embalse\n'LakeA'\n"
        "# Filtraciones medias [m3/s]\n5.0\n# Numero de Tramos\n2\n"
        "#Tramo Vol Pendiente Constante\n"
        "     1     0.0     0.003     1.8\n     2   500.0     0.001     4.8\n"
        "# Nombre de la Central aguas abajo\n'TurbineA'\n"
    ),
    "plpralco.dat": (
        "# Nombre Lago\n'LakeA'\n# Numero de Segmentos\n2\n"
        "0.0  80.0  1.0d-5\n600000.0  60.0  2.0d-5\n"
    ),
    "plpvrebemb.dat": "# n\n1\n'LakeA'\n1100000\n7.5\n",
    "plpminembh.dat": (
        "# Numero de embalses\n1\n# Nombre\n'LakeA'\n# Etapas\n2\n"
        "# Etapa VolMin Costo\n     1   150.0   0.0\n     2   160.0   3.0\n"
    ),
    "plpcenpmax.dat": (
        "# Numero de embalses\n1\n'LakeA'\n'LakeA'\n2\n"
        "   0.0   0.05   60.0\n 600.0   0.02   90.0\n"
    ),
}


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"soft_storage_bounds": True, "soft_emin_cost": 2.0, "vert_cost_cap": 5.0},
        {"plp_spill_costs": True, "vrebemb_as_sink": True},
        {"drop_spillway_waterway": True, "embed_reservoir_constraints": True},
        {"disable_discharge_limit_for": "LakeA", "auto_water_fail_cost": True},
        {"ror_as_reservoirs": "all", "pasada_mode": "hydro"},
        {"pasada_mode": "profile", "plp_legacy": True},
    ],
)
def test_convert_rich_hydro_variants(tmp_path: Path, extra: dict[str, Any]) -> None:
    """plp_hydro_4b + seepage / Ralco / spill-cost / soft-min / Pmax-curve files."""
    src = _copy_case("plp_hydro_4b", tmp_path / "plp")
    for name, text in _RICH_HYDRO_FILES.items():
        (src / name).write_text(text, encoding="latin-1")
    opts = _opts("plp_hydro_4b", tmp_path, **extra)
    opts["input_dir"] = src
    if "ror_as_reservoirs" in extra:
        csv = tmp_path / "ror.csv"
        csv.write_text(
            "name,vmax_hm3,production_factor\nTurbineA,2.0,1.0\nHydroRoR,1.0,1.0\n",
            encoding="utf-8",
        )
        opts["ror_as_reservoirs_file"] = csv
    convert_plp_case(opts)
    data = json.loads(opts["output_file"].read_text(encoding="utf-8"))
    system = data["system"]
    _uids_unique(system)
    assert any(r["name"] == "LakeA" for r in system["reservoir_array"])
