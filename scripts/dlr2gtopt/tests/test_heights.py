# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-span effective conductor height (spans --heights derive)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import pytest

from dlr2gtopt import __main__ as cli
from dlr2gtopt import _heights as hg
from dlr2gtopt._spans import (
    Tower,
    build_spans,
    read_spans,
    read_towers,
    route_towers,
    write_towers,
)
from dlr2gtopt.tests._synthetic import make_towers

GEO = {
    "crossarm": {"-3": [16.63, 21.63, 26.63], "0": [19.58, 24.58, 29.58]},
    "reference_family": "AD",
    "families": {"AD": "suspension", "BD": "suspension", "CD": "strain"},
    "string_m": 2.2,
    "weighted": [18.5, 23.5, 28.5],
    "ruling_sag_m": 9.0,
    "source": "test memo",
}


def run(*argv: Any) -> int:
    return cli.main([str(a) for a in argv])


def test_parse_structure_type() -> None:
    assert hg.parse_structure_type("22AD+3") == ("AD", 3.0)
    assert hg.parse_structure_type("22CD -3") == ("CD", -3.0)
    assert hg.parse_structure_type("BD") == ("BD", 0.0)
    assert hg.parse_structure_type("AD±0") == ("AD", 0.0)
    assert hg.parse_structure_type("ad+1,5") == ("AD", 1.5)
    assert hg.parse_structure_type("22BD.2-3") == ("BD", -3.0)
    assert hg.parse_structure_type("22FD B-3 P+0+0") == ("FD", -3.0)
    assert hg.parse_structure_type("50AD70") == ("AD", 0.0)
    assert hg.parse_structure_type(None) == (None, None)
    assert hg.parse_structure_type("--") == (None, None)


def test_tower_attachment_ladder() -> None:
    geo = hg.TowerGeometry(
        crossarm={-3.0: (16.63, 21.63, 26.63), 0.0: (19.58, 24.58, 29.58)},
        reference_family="AD",
        families={"AD": "suspension", "BD": "suspension", "CD": "strain"},
        string_m=2.2,
        weighted=(18.5, 23.5, 28.5),
    )

    def t(stype: str | None = None, att: float | None = None) -> Tower:
        return Tower("L", 1, 0.0, 0.0, structure_type=stype, attachment=att)

    at = hg.tower_attachment
    assert at(t(att=30.0), geo, "mid", 24.0) == (30.0, "list")
    assert at(t("22AD+0"), geo, "mid", 24.0) == (pytest.approx(22.38), "memo")
    assert at(t("22AD-3"), geo, "low", 24.0) == (pytest.approx(14.43), "memo")
    # +3 is not tabulated: shifted from the nearest (0) by +3 m
    assert at(t("22BD+3"), geo, "mid", 24.0) == (pytest.approx(25.38), "memo_family")
    assert at(t("22CD+0"), geo, "high", 24.0) == (pytest.approx(29.58), "memo_strain")
    assert at(t("PORTICO"), geo, "mid", 24.0) == (pytest.approx(21.3), "memo_weighted")
    assert at(t("22AD"), None, "mid", 24.0) == (24.0, "class")
    no_w = hg.TowerGeometry(crossarm=geo.crossarm, families=geo.families)
    assert at(t("XX+0"), no_w, "mid", 24.0) == (24.0, "class")


def _line(n: int, length: float = 400.0) -> tuple[list[Tower], list[Any]]:
    # towers along a meridian, `length` m apart
    dlat = length / 111_195.0
    towers = [Tower("L", k, -33.0 - k * dlat, -70.0) for k in range(n)]
    return towers, build_spans(towers)


def test_line_heights_flat_and_class_consistency() -> None:
    towers, spans = _line(5)
    hs = hg.line_heights(towers, spans, class_attach=24.0, class_height=16.0)
    # class only, equal spans: the class mean height exactly
    assert [h.height for h in hs] == pytest.approx([16.0] * 4, abs=1e-6)
    assert {h.src for h in hs} == {"class"} and hs[0].sag_src == "class"
    assert hs[0].h_terrain is None and hs[0].clearance is None
    with pytest.raises(ValueError, match="phase"):
        hg.line_heights(towers, spans, phase="top")
    with pytest.raises(ValueError, match="one more tower"):
        hg.line_heights(towers[:-1], spans)


def test_line_heights_ruling_span_sag_and_catenary() -> None:
    dl = 1 / 111_195.0
    lat = [-33.0, -33.0 - 300 * dl, -33.0 - 900 * dl]  # spans of 300 and 600 m
    towers = [
        Tower("L", k, la, -70.0, structure_type="22AD+0") for k, la in enumerate(lat)
    ]
    spans = build_spans(towers)
    geo = hg.TowerGeometry.from_json(_write(GEO))
    hs = hg.line_heights(towers, spans, geometry=geo)
    ls = [x.length_m for x in spans]
    rs = math.sqrt(sum(x**3 for x in ls) / sum(ls))
    assert hs[1].sag_m == pytest.approx(9.0 * (spans[1].length_m / rs) ** 2, rel=1e-6)
    assert hs[0].sag_m < 9.0 < hs[1].sag_m
    assert hs[0].src == "memo" and hs[0].sag_src == "memo_ruling"
    assert hs[0].height == pytest.approx(24.58 - 2.2 - 2 / 3 * hs[0].sag_m)
    cat = hg.TowerGeometry(catenary_m=1000.0)
    hc = hg.line_heights(towers, spans, geometry=cat)
    assert hc[1].sag_src == "catenary"
    assert hc[1].sag_m == pytest.approx(spans[1].length_m ** 2 / 8000.0, rel=1e-6)
    # a long span with low towers never drops below 5 m
    low = [Tower("L", k, la, -70.0, attachment=6.0) for k, la in enumerate(lat)]
    assert min(h.height for h in hg.line_heights(low, spans, geometry=cat)) == 5.0


def test_line_heights_terrain() -> None:
    towers, spans = _line(3)
    towers = [Tower(t.line, t.seq, t.lat, t.lon, elevation=100.0) for t in towers]
    flat = hg.line_heights(towers, spans)[0].h_flat
    valley = hg.line_heights(towers, spans, midspan_ground={1: 90.0, 2: 100.0})
    assert valley[0].h_terrain == pytest.approx(flat + 10.0)
    assert valley[0].height == pytest.approx(flat + 10.0)
    assert valley[1].height == pytest.approx(flat)
    assert valley[0].clearance == pytest.approx(24.0 - valley[0].sag_m + 10.0)
    gorge = hg.line_heights(towers, spans, midspan_ground={1: 0.0})
    assert gorge[0].height == pytest.approx(flat + 25.0)  # bounded
    ridge = hg.line_heights(towers, spans, midspan_ground={1: 130.0})
    assert ridge[0].height == pytest.approx(max(flat - 10.0, 5.0))
    # no ground at a span -> flat
    assert ridge[1].h_terrain is None


def _write(d: dict[str, Any], path: Path | None = None) -> Path:
    import tempfile

    p = path or Path(tempfile.mkstemp(suffix=".json")[1])
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_towers_csv_type_and_attachment(tmp_path: Path) -> None:
    towers = [
        Tower("L", 1, -33.0, -70.0, 10.0, "22AD+3", 27.5),
        Tower("L", 2, -33.01, -70.0),
    ]
    write_towers(tmp_path / "t.csv", towers)
    back = read_towers(tmp_path / "t.csv")
    assert back[0].structure_type == "22AD+3" and back[0].attachment == 27.5
    assert back[1].structure_type is None and back[1].attachment is None
    assert len(route_towers(back)["L"]) == 2


def test_cli_spans_heights(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    towers = [
        Tower(
            t.line,
            t.seq,
            t.lat,
            t.lon,
            t.elevation,
            "22AD+0" if t.seq % 2 else "22CD-3",
        )
        for t in make_towers(6)
    ]
    write_towers(tmp_path / "t.csv", towers)
    geo = _write(GEO, tmp_path / "geo.json")
    with (tmp_path / "ground.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line", "span", "elevation"])
        w.writerow(["L1", 1, 95.0])
        w.writerow(["L1", 2, ""])
    out, det = tmp_path / "s.csv", tmp_path / "d.csv"
    with caplog.at_level("INFO", logger="dlr2gtopt"):
        assert (
            run(
                "spans",
                tmp_path / "t.csv",
                "--heights",
                "derive",
                "--geometry",
                geo,
                "--kv",
                220,
                "--midspan-ground",
                tmp_path / "ground.csv",
                "--heights-detail",
                det,
                "-o",
                out,
            )
            == 0
        )
    assert "heights by source" in caplog.text
    spans = read_spans(out)
    assert all(s.height is not None and s.height >= 5.0 for s in spans)
    rows = list(csv.DictReader(det.open(encoding="utf-8")))
    assert (
        set(rows[0]["src"].split("|")) == {"memo", "memo_strain"}
        and rows[0]["h_terrain"] != ""
    )
    assert rows[1]["h_terrain"] == ""
    # default: no heights; derive without a voltage fails
    assert run("spans", tmp_path / "t.csv", "-o", tmp_path / "n.csv") == 0
    assert read_spans(tmp_path / "n.csv")[0].height is None
    with pytest.raises(SystemExit, match="--kv"):
        run("spans", tmp_path / "t.csv", "--heights", "derive", "-o", out)
    # class fallback through --line-params: class mean height on equal spans
    lp = tmp_path / "lp.json"
    assert run("line-params", "--kv", 500, "-o", lp) == 0
    assert (
        run(
            "spans",
            tmp_path / "t.csv",
            "--heights",
            "derive",
            "--line-params",
            lp,
            "-o",
            out,
        )
        == 0
    )
    hs = [s.height for s in read_spans(out)]
    assert min(hs) < 24.0 + 1.0 and max(hs) > 24.0 - 3.0
