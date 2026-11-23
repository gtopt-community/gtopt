"""``dcirc.dat`` R/X are % on 100 MVA; ``Volt.dat`` kV only feeds metadata.

Regression for the NCP ``.dat`` front-end reading ``dcirc.dat`` impedances
as ohms (``X·100/kV²``): on the Guatemala case that shrank 69 kV lines
below ``dc_line_reactance_threshold`` (and ~5000x on buses whose
``Volt.dat`` kV was mistyped as the name suffix, e.g. 692).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sddp2gtopt.dat_loader import (
    _MAX_PU,
    _MIN_PU,
    _pct_to_pu,
    _sanitize_kv,
    _sanitize_volt,
    load_dat_case,
)
from sddp2gtopt.gtopt_writer import build_options
from sddp2gtopt.tests.test_dat_network import _write_mini_multibus_case


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _case(root: Path, volt: str) -> Path:
    """Tiny 4-bus NCP case: a 69 kV line, a coupler, a zero-X tie, a xfmr."""
    _write_mini_multibus_case(root)
    _write(
        root / "dbus.dat",
        " NUM. Tp NombreB Id  # tg Plnt Nombre\n"
        " 1 0 ESC-692 ts  1 0 10 GASPLANT\n"
        " 1 0 ESC-692 ts  2 0 201 HYDRO1\n"
        " 2 0 ESC-69 ts  0\n"
        " 3 0 TAP-691 ts  0\n"
        " 4 0 GSU-231 ts  0\n",
    )
    _write(
        root / "dcirc.dat",
        "#BOR.   #BDE.    .RESIS.REACT Nome (MW)   #\n"
        " 1       2         0.95  4.50 L12_69KV                     80.0     1 0 0\n"
        " 2       3         0.00  0.05 CPL23                        80.0     2 0 0\n"
        " 3       1         0.00  0.00 TIE31                        80.0     3 0 0\n"
        " 4       2         0.10  8.40 T42                          100.0    4 0 0\n",
    )
    _write(root / "Volt.dat", volt)
    _write(
        root / "cpdexbus.dat",
        "Infxbloque : 1\ndd/mm/aaaa:hh   2\n01/01/2026:00   50.0\n",
    )
    return root


_VOLT_TYPO = (
    "(Bus) (...Nome...) O (.kV)\n"
    " 1 ESC-692 692.\n 2 ESC-69 69.\n 3 TAP-691 691.\n 4 GSU-231 230.\n"
)
_VOLT_OK = (
    "(Bus) (...Nome...) O (.kV)\n 1 ESC-692 69.\n 2 ESC-69 69.\n"
    " 3 TAP-691 69.\n 4 GSU-231 230.\n"
)


def test_pct_to_pu() -> None:
    assert _pct_to_pu(4.5) == pytest.approx(0.045)
    assert _pct_to_pu(0.0) == 0.0
    assert _pct_to_pu(-2.0) == pytest.approx(-0.02)


def test_dcirc_percent_independent_of_volt(tmp_path: Path) -> None:
    """Same dcirc → same p.u. impedances whatever Volt.dat says."""
    typo = load_dat_case(_case(tmp_path / "typo", _VOLT_TYPO))
    good = load_dat_case(_case(tmp_path / "good", _VOLT_OK))
    for case in (typo, good):
        by_name = {c.name: c for c in case.circuits}
        assert by_name["L12_69KV"].reactance_pu == pytest.approx(0.045)
        assert by_name["L12_69KV"].resistance == pytest.approx(0.0095)
        assert by_name["CPL23"].reactance_pu == pytest.approx(5.0e-4)
        assert by_name["TIE31"].reactance_pu == pytest.approx(_MIN_PU)
        assert by_name["T42"].reactance_pu == pytest.approx(0.084)
        assert by_name["T42"].resistance == pytest.approx(0.001)
    # Mistyped kV sanitised to 69 → identical bus voltages too.
    assert {b.number: b.base_kv for b in typo.buses} == {
        b.number: b.base_kv for b in good.buses
    }


def test_only_real_couplers_below_tie_threshold(tmp_path: Path) -> None:
    """A 4.5 % 69 kV line is a real line, not a tiny-reactance tie."""
    case = load_dat_case(_case(tmp_path / "c", _VOLT_TYPO))
    opts = build_options(case.study, use_single_bus=False, use_kirchhoff=True)
    thr = opts["model_options"]["dc_line_reactance_threshold"]
    ties = sorted(c.name for c in case.circuits if abs(c.reactance_pu) < thr)
    assert ties == ["CPL23", "TIE31"]
    # Floor stays below the tie threshold so it never reclassifies a line.
    assert _MIN_PU < thr < 0.01 < _MAX_PU


@pytest.mark.parametrize(
    ("kv", "name", "expected"),
    [
        (692.0, "ESC-692", 69.0),
        (691.0, "TAP-691", 69.0),
        (342.0, "XYZ-342", 34.5),
        (232.0, "GSU-232", 230.0),
        (234.0, "GSU-234", 230.0),
        (132.0, "ABC-132", 13.8),
        (69.0, "ESC-692", None),  # already standard
        (230.0, "GSU-231", None),
        (64.5, "PET-34", None),  # odd but not a suffix copy
        (13.0, "AGU-13", None),  # 2-digit suffix: not an index pattern
        (692.0, "ESC692", None),  # no "-" suffix
        (692.0, "ESC-693", None),  # suffix differs from the value
        (991.0, "ESC-991", None),  # no standard class "99"
        (0.0, "ESC-692", None),
    ],
)
def test_sanitize_kv(kv: float, name: str, expected: float | None) -> None:
    assert _sanitize_kv(kv, name) == expected


def test_sanitize_volt_warns_once_per_fix(caplog: pytest.LogCaptureFixture) -> None:
    volt = {i: 692.0 for i in range(1, 8)} | {8: 342.0, 9: 230.0, 10: 13.8}
    names = {i: f"B{i}-692" for i in range(1, 8)} | {
        8: "C-342",
        9: "D-230",
        10: "E-13",
    }
    with caplog.at_level(logging.WARNING, logger="sddp2gtopt.dat_loader"):
        out = _sanitize_volt(volt, names)
    assert all(out[i] == 69.0 for i in range(1, 8))
    assert out[8] == 34.5 and out[9] == 230.0 and out[10] == 13.8
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2
    assert any("692" in m and "-> 69 kV" in m and "…" in m for m in msgs)
    assert any("342" in m and "-> 34.5 kV" in m for m in msgs)
    # unknown bus name → no fix, no crash
    assert _sanitize_volt({99: 692.0}, {}) == {99: 692.0}
