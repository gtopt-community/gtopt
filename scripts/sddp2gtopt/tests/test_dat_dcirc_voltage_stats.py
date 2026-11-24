"""Per-voltage-level impedance statistics for the NCP ``.dat`` network path.

Complements ``test_dat_dcirc_percent.py`` (fix/sddp2gtopt-dcirc-percent),
which pins the % -> p.u. conversion circuit by circuit.  These tests pin
the *aggregate* picture that the ohm bug distorted:

* per voltage level, ``x_pu`` min / median / max equal the ``dcirc.dat``
  REACT column divided by 100, for every ``Volt.dat`` variant (correct,
  mistyped suffix copies, absent) -- the impedance never depends on kV;
* lower-voltage lines have a larger per-unit reactance on the 100 MVA
  base than higher-voltage ones, inside the (1e-3, 1) p.u. line range;
* the writer emits exactly those reactances, and only the real couplers
  fall below ``dc_line_reactance_threshold``;
* on the real Guatemala NCP week case (``GTOPT_CASES_DIR``), 52 couplers
  are ties and every voltage level has a physically plausible median.

It also pins the fixed-width RESIS/REACT split: the two 6-character
fields touch when REACT >= 100 % (``    0.100.25`` = R 0 %, X 100.25 %);
the former split one character before the second decimal point read
that as R 0.10 %, X 0.25 % (~400x too stiff).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path

import pytest

from gtopt_shared.compressed_open import find_compressed_path
from gtopt_shared.testing.external_cases import (
    external_case_path,
    missing_case_reason,
)
from sddp2gtopt import dat_loader
from sddp2gtopt.dat_loader import load_dat_case
from sddp2gtopt.dat_parsers import CircuitParser
from sddp2gtopt.gtopt_writer import build_options, build_planning
from sddp2gtopt.tests.test_dat_network import _write_mini_multibus_case

_TIE_THRESHOLD = 1.0e-3
_GUA_WEEK = "guatemala/daily_scheduling/ncp_week_2026-06-21/source"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="latin-1")


def _dcirc_line(fb: int, tb: int, r_pct: float, x_pct: float, name: str) -> str:
    """One ``dcirc.dat`` row in the PSR fixed-width layout (F6.2 R, F6.2 X)."""
    region = f"{r_pct:6.2f}{x_pct:6.2f}"
    return f"{fb:>5}{'':3}{tb:>5}{'':4}{region} {name:<28} 300.0    1 0 0\n"


# (from, to, R %, X %, name, voltage-level key)
_CIRCUITS: list[tuple[int, int, float, float, str, str]] = [
    (1, 2, 0.30, 1.50, "L12A_230", "line230"),
    (1, 2, 0.40, 2.10, "L12B_230", "line230"),
    (3, 6, 0.90, 4.00, "L36_138", "line138"),
    (4, 5, 2.00, 12.00, "L45A_69", "line69"),
    (4, 5, 3.10, 20.00, "L45B_69", "line69"),
    (4, 5, 0.00, 0.05, "CPL45_69", "line69"),
    (2, 3, 0.10, 6.00, "T23_230_138", "xfmr"),
    (6, 4, 0.20, 25.00, "T64_138_69", "xfmr"),
    (5, 7, 0.00, 95.00, "T57_69_13", "xfmr"),
]

_DBUS = (
    " NUM. Tp NombreB Id  # tg Plnt Nombre\n"
    " 1 0 GEN-230 ts  1 0 10 GASPLANT\n"
    " 1 0 GEN-230 ts  2 0 201 HYDRO1\n"
    " 2 0 LOAD-230 ts  0\n"
    " 3 0 SUB-138 ts  0\n"
    " 4 0 DIS-69 ts  0\n"
    " 5 0 DIS-692 ts  0\n"
    " 6 0 FEED-138 ts  0\n"
    " 7 0 MV-13 ts  0\n"
)

_VOLT_OK = (
    "(Bus) (...Nome...) O (.kV)\n 1 GEN-230 230.\n 2 LOAD-230 230.\n"
    " 3 SUB-138 138.\n 4 DIS-69 69.\n 5 DIS-692 69.\n 6 FEED-138 138.\n"
    " 7 MV-13 13.8\n"
)
_VOLT_TYPO = _VOLT_OK.replace(" 5 DIS-692 69.", " 5 DIS-692 692.")


def _multilevel_case(root: Path, volt: str | None) -> Path:
    """7-bus, 4-level (230/138/69/13.8 kV) NCP case; ``volt=None`` drops Volt.dat."""
    _write_mini_multibus_case(root)
    _write(root / "dbus.dat", _DBUS)
    _write(
        root / "dcirc.dat",
        "#BOR.   #BDE.    .RESIS.REACT Nome (MW)   #\n"
        + "".join(_dcirc_line(f, t, r, x, n) for f, t, r, x, n, _ in _CIRCUITS),
    )
    if volt is None:
        (root / "Volt.dat").unlink()
    else:
        _write(root / "Volt.dat", volt)
    return root


def _stats(values: list[float]) -> tuple[float, float, float]:
    return min(values), statistics.median(values), max(values)


def _x_by_level(case: dat_loader.DatCase) -> dict[str, list[float]]:
    level = {n: lvl for _, _, _, _, n, lvl in _CIRCUITS}
    out: dict[str, list[float]] = defaultdict(list)
    for c in case.circuits:
        out[level[c.name]].append(c.reactance_pu)
    return dict(out)


def _expected_by_level() -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for _, _, _, x_pct, _, lvl in _CIRCUITS:
        out[lvl].append(max(x_pct / 100.0, dat_loader._MIN_PU))
    return dict(out)


@pytest.mark.parametrize(
    "volt", [_VOLT_OK, _VOLT_TYPO, None], ids=["ok", "typo", "none"]
)
def test_per_level_stats_are_x_pct_over_100(tmp_path: Path, volt: str | None) -> None:
    """min/median/max x_pu per level == REACT/100, for every Volt.dat variant."""
    case = load_dat_case(_multilevel_case(tmp_path / "c", volt))
    got = _x_by_level(case)
    want = _expected_by_level()
    assert set(got) == set(want)
    for lvl, values in want.items():
        assert _stats(got[lvl]) == pytest.approx(_stats(values)), lvl
    # Resistance follows the same % -> p.u. rule (no floor on R).
    r_by_name = {c.name: c.resistance for c in case.circuits}
    for _, _, r_pct, _, name, _ in _CIRCUITS:
        assert r_by_name[name] == pytest.approx(r_pct / 100.0), name


def test_lower_voltage_lines_have_larger_pu_reactance(tmp_path: Path) -> None:
    """Median 69 kV line X > 138 kV > 230 kV, all inside (1e-3, 1) p.u."""
    got = _x_by_level(load_dat_case(_multilevel_case(tmp_path / "c", _VOLT_OK)))
    real_69 = [x for x in got["line69"] if x >= _TIE_THRESHOLD]
    med = {
        "69": statistics.median(real_69),
        "138": statistics.median(got["line138"]),
        "230": statistics.median(got["line230"]),
    }
    assert med["69"] > med["138"] > med["230"]
    # Plausible transmission range on 100 MVA: 1e-3 < x_pu < 1 for lines.
    for x in real_69 + got["line138"] + got["line230"]:
        assert _TIE_THRESHOLD < x < 1.0


def test_writer_reactance_matches_loader_and_ties(tmp_path: Path) -> None:
    """Line.reactance == loader x_pu; only the coupler is under the threshold."""
    case = load_dat_case(_multilevel_case(tmp_path / "c", _VOLT_TYPO))
    planning = build_planning(
        study=case.study,
        systems=case.systems,
        thermals=case.thermals,
        hydros=case.hydros,
        demands=case.demands,
        name="levels",
        buses=case.buses,
        circuits=case.circuits,
    )
    lines = planning["system"]["line_array"]
    assert len(lines) == len(_CIRCUITS)
    for line, circ in zip(lines, case.circuits, strict=True):
        assert line["reactance"] == pytest.approx(circ.reactance_pu)
    opts = build_options(case.study, use_single_bus=False, use_kirchhoff=True)
    thr = opts["model_options"]["dc_line_reactance_threshold"]
    ties = [ln["name"] for ln in lines if abs(ln["reactance"]) < thr]
    assert [t.rsplit("_", 1)[0] for t in ties] == ["CPL45_69"]
    # Bus voltage metadata is the sanitised class (692 -> 69), not the typo.
    volt = {b["uid"]: b.get("voltage") for b in planning["system"]["bus_array"]}
    assert volt[5] == pytest.approx(69.0)


# ── dcirc.dat fixed-width RESIS/REACT parsing (bug on both branches) ──────

_MERGED_ROWS = [
    # (region as in the file, R %, X %) -- real rows: GUA NCP 69/6 kV
    # 8 MVA transformer, GUA SDDP 2024 plant step-up, Peru COES MRFO.
    ("    0.100.25", 0.0, 100.25),
    ("    0.118.23", 0.0, 118.23),
    (" 71.72235.15", 71.72, 235.15),
    ("1.246410.905", 1.2464, 10.905),
]


@pytest.mark.parametrize(("region", "r_pct", "x_pct"), _MERGED_ROWS)
def test_merged_resis_react_is_fixed_width(
    tmp_path: Path, region: str, r_pct: float, x_pct: float
) -> None:
    assert len(region) == 12
    row = f"{1:>5}{'':3}{2:>5}{'':4}{region} T12{'':25} 8.    14\n"
    path = tmp_path / "dcirc.dat"
    _write(path, "#BOR.   #BDE.    .RESIS.REACT Nome (MW)   #\n" + row)
    circuits = CircuitParser(path).parse()
    assert len(circuits) == 1
    circ = circuits[0]
    assert (circ.resistance, circ.reactance_pu) == pytest.approx((r_pct, x_pct))


# ── real Guatemala NCP week case (GTOPT_CASES_DIR) ───────────────────────


def _gua_case() -> dat_loader.DatCase:
    src = external_case_path(_GUA_WEEK)
    if src is None:
        pytest.skip(missing_case_reason(_GUA_WEEK))
    return load_dat_case(src)


def _gua_lines_by_kv(case: dat_loader.DatCase) -> dict[float, list[float]]:
    kv = {b.number: b.base_kv for b in case.buses}
    out: dict[float, list[float]] = defaultdict(list)
    for c in case.circuits:
        a, b = kv.get(c.from_bus, 0.0), kv.get(c.to_bus, 0.0)
        if a == b and a > 0.0:
            out[a].append(abs(c.reactance_pu))
    return dict(out)


def test_gua_week_ties_and_per_level_medians() -> None:
    """52 real couplers are ties; per-level line medians are plausible."""
    case = _gua_case()
    ties = sum(1 for c in case.circuits if abs(c.reactance_pu) < _TIE_THRESHOLD)
    assert ties == 52
    lines = _gua_lines_by_kv(case)
    # Median x_pu of real lines (couplers excluded) per transmission level:
    # 69 kV ~ 4e-2, 138 kV ~ 4e-2, 230 kV ~ 1.4e-2 (the ohm bug gave 69 kV
    # medians below the 1e-3 tie threshold for the mistyped 691/692 buses).
    for level, (lo, hi) in {69.0: (1e-2, 0.2), 138.0: (1e-2, 0.2)}.items():
        real = [x for x in lines[level] if x >= _TIE_THRESHOLD]
        assert lo < statistics.median(real) < hi, level
    real_230 = [x for x in lines[230.0] if x >= _TIE_THRESHOLD]
    assert 3e-3 < statistics.median(real_230) < 5e-2
    # Every circuit stays within the documented floor/cap.
    assert all(
        dat_loader._MIN_PU <= abs(c.reactance_pu) <= dat_loader._MAX_PU
        for c in case.circuits
    )


def test_gua_week_merged_rows_parse_fixed_width() -> None:
    """Every dcirc row's (R, X) equals the fixed-width [17:23]/[23:29] split."""
    src = external_case_path(_GUA_WEEK)
    if src is None:
        pytest.skip(missing_case_reason(_GUA_WEEK))
    path = find_compressed_path(src / "dcirc.dat")
    assert path is not None
    circuits = CircuitParser(path).parse()
    mnl = [
        c for c in circuits if c.from_bus == 1857 and c.to_bus in {16009, 16010, 16011}
    ]
    assert mnl
    assert all(c.reactance_pu >= 50.0 for c in mnl)
