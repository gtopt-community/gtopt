"""Tests for the ``--gen-costs`` / ``--line-limits`` CSV overlays."""

from __future__ import annotations

from pathlib import Path

import pytest

from psse2gtopt.gtopt_writer import build_planning
from psse2gtopt.main import main
from psse2gtopt.overlay import parse_gen_costs, parse_line_limits, split_segments
from psse2gtopt.raw_parser import parse_raw


_RAW = """\
0,   100.00, 33, 0, 1, 60.00     / overlay fixture
OVERLAY FIXTURE
SECOND TITLE
     1,'A           ', 230.0000,3,   1,   1,   1,1.00000,   0.0000
     2,'B           ', 230.0000,1,   1,   1,   1,1.00000,   0.0000
 0 /End of Bus data, Begin Load data
     2,'1 ',1,   1,   1,   150.000,     0.000,     0.000,     0.000,     0.000,     0.000,   1,1
 0 /End of Load data, Begin Fixed shunt data
 0 /End of Fixed shunt data, Begin Generator data
     1,'G ',   100.000,     0.000,   100.000,  -100.000,1.00000,     0,   300.000, 0.00000E+0, 2.00000E-1, 0.00000E+0, 0.00000E+0,1.00000,1,  100.0,   300.000,     0.000,   1,1.0000
     2,'W ',    50.000,     0.000,    50.000,   -50.000,1.00000,     0,   200.000, 0.00000E+0, 2.00000E-1, 0.00000E+0, 0.00000E+0,1.00000,1,  100.0,   200.000,     0.000,   1,1.0000
     2,'H ',     0.000,     0.000,    50.000,   -50.000,1.00000,     0,   100.000, 0.00000E+0, 2.00000E-1, 0.00000E+0, 0.00000E+0,1.00000,1,  100.0,   100.000,     0.000,   1,1.0000
 0 /End of Generator data, Begin Branch data
     1,     2,'1 ', 1.00000E-3, 1.00000E-2, 0.00000E+0,    0.00,    0.00,    0.00,  0.00000,  0.00000,  0.00000,  0.00000,1,1,   0.00,   1,1.0000
     1,     2,'2 ', 1.00000E-3, 1.00000E-2, 0.00000E+0,  500.00,    0.00,    0.00,  0.00000,  0.00000,  0.00000,  0.00000,1,1,   0.00,   1,1.0000
 0 /End of Branch data, Begin Transformer data
 0 /End of Transformer data
"""

_COSTS = """\
bus,id,pmax,mw1,cost1,mw2,cost2,mw3,cost3
1,G,,100,20,250,30,280,45
2,W,40,200,0,,,,
2,H,0,,,,,,
"""

_LIMITS = """\
from,to,ckt,tmax
2,1,1,80
1,2,2,
"""


@pytest.fixture(name="files")
def _files(tmp_path: Path) -> dict[str, Path]:
    raw = tmp_path / "case.raw"
    raw.write_text(_RAW, encoding="latin-1")
    costs = tmp_path / "costs.csv"
    costs.write_text(_COSTS, encoding="utf-8")
    limits = tmp_path / "limits.csv"
    limits.write_text(_LIMITS, encoding="utf-8")
    return {"raw": raw, "costs": costs, "limits": limits}


def test_split_segments() -> None:
    segs = [(100.0, 20.0), (250.0, 30.0), (280.0, 45.0)]
    assert split_segments(segs, 300.0) == [(100.0, 20.0), (150.0, 30.0), (50.0, 45.0)]
    assert split_segments(segs, 200.0) == [(100.0, 20.0), (100.0, 30.0)]
    assert split_segments([(0.0, 5.0)], 60.0) == [(60.0, 5.0)]


def test_parse_overlays(files: dict[str, Path]) -> None:
    costs = parse_gen_costs(files["costs"])
    assert costs[(1, "G")].pmax is None
    assert costs[(1, "G")].segments == [(100.0, 20.0), (250.0, 30.0), (280.0, 45.0)]
    assert costs[(2, "W")].pmax == pytest.approx(40.0)
    limits = parse_line_limits(files["limits"])
    assert limits[(1, 2, "1")] == pytest.approx(80.0)
    assert limits[(2, 1, "1")] == pytest.approx(80.0)
    assert limits[(1, 2, "2")] == pytest.approx(0.0)


def test_overlay_applied(files: dict[str, Path]) -> None:
    planning = build_planning(
        parse_raw(files["raw"]),
        name="ov",
        gen_costs=parse_gen_costs(files["costs"]),
        line_limits=parse_line_limits(files["limits"]),
    )
    gens = {g["name"]: g for g in planning["system"]["generator_array"]}
    # Thermal unit split into 3 convex segments; last stretched to PT=300.
    assert [gens[n]["pmax"] for n in ("g1_G", "g1_G_s2", "g1_G_s3")] == [
        pytest.approx(100.0),
        pytest.approx(150.0),
        pytest.approx(50.0),
    ]
    assert [gens[n]["gcost"] for n in ("g1_G", "g1_G_s2", "g1_G_s3")] == [
        pytest.approx(20.0),
        pytest.approx(30.0),
        pytest.approx(45.0),
    ]
    # pmax override, single zero-cost segment; pmax 0 drops the unit.
    assert gens["g2_W"]["pmax"] == pytest.approx(40.0)
    assert gens["g2_W"]["gcost"] == pytest.approx(0.0)
    assert "g2_H" not in gens
    uids = [g["uid"] for g in planning["system"]["generator_array"]]
    assert len(set(uids)) == len(uids)
    lines = {ln["name"]: ln for ln in planning["system"]["line_array"]}
    assert lines["l1_2_1"]["tmax_ab"] == pytest.approx(80.0)
    # Blank overlay value -> unlimited, overriding the RAW rating of 500.
    assert lines["l1_2_2"]["tmax_ab"] == pytest.approx(99999.0)


def test_cli_overlay_flags(files: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "out"
    main(
        [
            str(files["raw"]),
            "-o",
            str(out),
            "--gen-costs",
            str(files["costs"]),
            "--line-limits",
            str(files["limits"]),
        ]
    )
    text = (out / "out.json").read_text(encoding="utf-8")
    assert "g1_G_s3" in text
