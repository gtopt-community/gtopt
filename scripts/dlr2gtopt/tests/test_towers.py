# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tower sources: CEN structure lists, Infotecnica client, OSM, KML."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dlr2gtopt import _towers as tw

# Excel-export layout of a CEN structure list (made-up coordinates): the
# COORD_X block is printed for all rows, then the COORD_Y block.
CEN_TEXT_BLOCKS = """\
SIGID              LAT                      CIRCUITO   SUBTIPO_DESC   COORD_X
Estructura-1       Alfa - Beta              C1         ML             -71,40000000
Estructura-2       Alfa - Beta              C1         Suspension     -71,40100000
Estructura-4       Alfa - Beta              C1         Anclaje        -71,40300000
Estructura-3       Alfa - Beta              C1         Suspension     -71,40200000
           Page 1
COORD_Y         CORRELATIVO   ESTRUCTURA   TIPO
-31,90000000   0             ML
-31,89700000   1             E-1          DD-3
-31,89100000   3             E-3          AD+3
-31,89400000   2             E-2          AS
"""

CEN_TEXT_ROWS = """\
Estructura-1   Alfa - Beta   C1   ML          -71,4 -31,9 0 E-0
Estructura-2   Alfa - Beta   C2   Suspension  -71,401 -31,897 1 E-1
Estructura-3   Alfa - Beta   C1   Suspension  -71,402 -31,894 2 E-2
"""


def test_parse_structure_text_blocks_sorted_by_correlative() -> None:
    towers = tw.parse_structure_text(CEN_TEXT_BLOCKS, "AB")
    assert [t.seq for t in towers] == [0, 1, 2, 3]
    assert towers[2].lat == pytest.approx(-31.894)
    assert towers[2].lon == pytest.approx(-71.402)


def test_parse_structure_text_rows_and_circuit() -> None:
    assert len(tw.parse_structure_text(CEN_TEXT_ROWS, "AB")) == 3
    c1 = tw.parse_structure_text(CEN_TEXT_ROWS, "AB", circuit="C1")
    assert [t.seq for t in c1] == [0, 2]


def test_parse_structure_text_errors() -> None:
    bad = CEN_TEXT_BLOCKS.replace("-31,89400000   2             E-2          AS\n", "")
    with pytest.raises(ValueError, match="COORD_Y rows"):
        tw.parse_structure_text(bad, "AB")
    with pytest.raises(ValueError, match="no structure rows"):
        tw.parse_structure_text("nothing here", "AB")


def test_is_structure_list() -> None:
    assert tw.is_structure_list("ID 1972 Listado de distribución de estructuras")
    assert not tw.is_structure_list("Tipo de torre mas representativa")


def test_utm_to_latlon_round_numbers() -> None:
    # central meridian of zone 19 (-69 deg) on the equator (south hemisphere)
    lat, lon = tw.utm_to_latlon(500000.0, 10000000.0, 19, south=True)
    assert lat == pytest.approx(0.0, abs=1e-6)
    assert lon == pytest.approx(-69.0, abs=1e-6)
    # symmetric about the central meridian, ~1 km per 1000 m of easting
    la1, lo1 = tw.utm_to_latlon(480000.0, 6470000.0, 19)
    la2, lo2 = tw.utm_to_latlon(520000.0, 6470000.0, 19)
    assert la1 == pytest.approx(la2, abs=1e-9)
    assert lo1 + lo2 == pytest.approx(-138.0, abs=1e-9)
    la3, lo3 = tw.utm_to_latlon(481000.0, 6470000.0, 19)
    assert tw.haversine_m(la1, lo1, la3, lo3) == pytest.approx(1000.0, rel=2e-3)
    assert -32.0 < la1 < -31.8


def test_parse_structure_rows_latlon_and_utm() -> None:
    rows = [
        ["Listado"],
        ["SIGID", "Circuito", "COORD_X", "COORD_Y", "Correlativo"],
        ["E1", "C1", "-71,40", "-31,90", 1],
        ["E2", "C2", "-71,41", "-31,91", 2],
        ["E3", "C1", -71.42, -31.92, "x"],
        ["note", "", "", "", ""],
    ]
    t = tw.parse_structure_rows(rows, "L", circuit="C1")
    assert [x.seq for x in t] == [1, 3]
    utm = [["Este", "Norte"], [480000.0, 6470000.0], [480100.0, 6470300.0]]
    with pytest.raises(ValueError, match="utm-zone"):
        tw.parse_structure_rows(utm, "L")
    t2 = tw.parse_structure_rows(utm, "L", utm_zone=19)
    assert -32.0 < t2[0].lat < -31.8
    with pytest.raises(ValueError, match="header"):
        tw.parse_structure_rows([["a", "b"]], "L")
    with pytest.raises(ValueError, match="no coordinate rows"):
        tw.parse_structure_rows([["lat", "lon"], ["x", "y"]], "L")


def test_parse_cen_template_rows() -> None:
    """CEN template: grouped header, "Coordenada Este/Norte", Huso column,
    northing mislabelled "Sur" in some rows, descending tower numbers."""
    rows = [
        ["Datos:"],
        [None, "Ítem", "Sección Tramo Asociada", None, "Coordenadas UTM"],
        [
            None,
            "Nº",
            "Nombre Circuito",
            "N° de Estructura",
            "Coordenada Este",
            "Coordenada Norte",
            "Coordenada Sur",
            "Huso",
        ],
        [None, 1, "Circuito C1", 777, 480000.0, 6470000.0, None, 19],
        [None, 2, "Circuito C1", 776, 480100.0, None, 6470300.0, 19],
        [None, 3, "Circuito C2", 775, 480200.0, 6470600.0, None, 19],
        [None, None, None, None, None, None, None, None],
    ]
    t = tw.parse_structure_rows(rows, "L")
    assert len(t) == 3 and [x.seq for x in t] == [1, 2, 3]
    assert t[0].lat < t[1].lat < t[2].lat
    assert len(tw.parse_structure_rows(rows, "L", circuit="C1")) == 2


def test_read_structure_file_picks_largest_sheet(tmp_path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Metodologia"])
    big = wb.create_sheet("tramo")
    big.append(["Este UTM", "Norte UTM", "Huso"])
    for k in range(4):
        big.append([480000.0 + 100 * k, 6470000.0 + 300 * k, 19])
    small = wb.create_sheet("otro")
    small.append(["lat", "lon"])
    small.append([-31.9, -71.5])
    wb.save(tmp_path / "m.xlsx")
    assert len(tw.read_structure_file(tmp_path / "m.xlsx", "L")) == 4
    wb2 = openpyxl.Workbook()
    wb2.save(tmp_path / "e.xlsx")
    with pytest.raises(ValueError, match="no coordinate header"):
        tw.read_structure_file(tmp_path / "e.xlsx", "L")


def test_read_structure_file_xlsx_csv(tmp_path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["LATITUD", "LONGITUD"])
    ws.append([-31.9, -71.5])
    ws.append([-31.897, -71.5])
    wb.save(tmp_path / "s.xlsx")
    assert len(tw.read_structure_file(tmp_path / "s.xlsx", "L")) == 2
    (tmp_path / "s.csv").write_text(
        "lat;lon\n-31.9;-71.5\n-31.89;-71.5\n", encoding="utf-8"
    )
    assert len(tw.read_structure_file(tmp_path / "s.csv", "L")) == 2


def test_read_structure_file_xls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Sheet:
        nrows = 2

        def row_values(self, i: int) -> list[object]:
            return [["lat", "lon"], [-31.9, -71.5]][i]

    class Book:
        nsheets = 1

        def sheet_by_index(self, _i: int) -> Sheet:
            return Sheet()

    import sys
    import types

    fake = types.ModuleType("xlrd")
    fake.open_workbook = lambda _p: Book()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xlrd", fake)
    assert len(tw.read_structure_file(tmp_path / "s.xls", "L")) == 1


def test_pdf_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tw.shutil, "which", lambda _n: None)
    with pytest.raises(RuntimeError, match="pdftotext"):
        tw.read_structure_file(tmp_path / "x.pdf", "L")

    class Done:
        stdout = CEN_TEXT_BLOCKS.encode()

    monkeypatch.setattr(tw.shutil, "which", lambda _n: "/usr/bin/pdftotext")
    monkeypatch.setattr(tw.subprocess, "run", lambda *a, **k: Done())
    assert len(tw.read_structure_file(tmp_path / "x.pdf", "L")) == 4


class FakeGet:
    """Recorded-response HTTP getter: URL substring -> body."""

    def __init__(self, replies: dict[str, bytes]) -> None:
        self.replies = replies
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        for k, v in self.replies.items():
            if k in url:
                return v
        raise AssertionError(f"unexpected URL {url}")


def test_polite_cache_pauses_and_caches(tmp_path: Path) -> None:
    get = FakeGet({"a": b"A", "b": b"B"})
    slept: list[float] = []
    cache = tw.PoliteCache(tmp_path, get, pause=5.0, sleep=slept.append)
    assert cache.fetch("k1", "http://x/a") == b"A"
    assert cache.fetch("k2", "http://x/b") == b"B"
    assert cache.fetch("k1", "http://x/a") == b"A"
    assert len(get.urls) == 2
    assert cache.requests == 2
    assert len(slept) == 1 and slept[0] > 4.0


def test_infotecnica_client(tmp_path: Path) -> None:
    docs = [
        {"id": 7, "nombre": "Minuta", "extension": ".pdf"},
        {
            "id": 9,
            "nombre": "Listado de distribución de estructuras",
            "extension": ".pdf",
        },
    ]
    get = FakeGet(
        {
            "extended/?search=": json.dumps(
                {"results": [{"id": 5, "nombre": "A - B 220KV C1"}]}
            ).encode(),
            "documentos/9/": b"%PDF-fake",
            "/documentos/": json.dumps(docs).encode(),
        }
    )
    client = tw.Infotecnica(tw.PoliteCache(tmp_path, get, pause=0.0))
    assert client.search_sections("A - B")[0]["id"] == 5
    st = client.structure_documents(5)
    assert [d["id"] for d in st] == [9]
    path = client.download(5, st[0])
    assert path.read_bytes() == b"%PDF-fake"
    assert path.name == "doc_9.pdf"


def test_overpass_query() -> None:
    q = tw.overpass_query([1, 2])
    assert "way(id:1,2)" in q and "node(w.l)" in q
    q2 = tw.overpass_query(bbox=(-32, -72, -31, -71), voltage="220000", name="Vilos")
    assert '["voltage"~"220000"]' in q2 and '["name"~"Vilos",i]' in q2
    with pytest.raises(ValueError):
        tw.overpass_query()


def _way(
    wid: int, nodes: list[int], coords: list[tuple[float, float]]
) -> dict[str, object]:
    return {
        "type": "way",
        "id": wid,
        "nodes": nodes,
        "geometry": [{"lat": a, "lon": b} for a, b in coords],
    }


def osm_reply() -> dict[str, object]:
    """Overpass reply: two ways, the second drawn backwards."""
    p = [(-31.9 + 0.0036 * k, -71.5) for k in range(7)]
    return {
        "elements": [
            _way(1, [10, 11, 12, 13], p[:4]),
            _way(2, [16, 15, 14, 13], p[3:][::-1]),
            *[
                {"type": "node", "id": n, "tags": {"power": "tower"}}
                for n in (10, 11, 13, 14, 15, 16)
            ],
        ]
    }


def test_towers_from_osm_stitches(tmp_path: Path) -> None:
    towers = tw.towers_from_osm(osm_reply(), "L")
    assert len(towers) == 7
    assert towers[-1].lat > towers[0].lat
    assert tw.osm_tower_traced(towers)
    only = tw.towers_from_osm(osm_reply(), "L", towers_only=True)
    assert len(only) == 6
    assert not tw.osm_tower_traced(only[:3])
    get = FakeGet({"interpreter": json.dumps(osm_reply()).encode()})
    data = tw.fetch_osm(
        tw.PoliteCache(tmp_path, get, pause=0.0), tw.overpass_query([1, 2])
    )
    assert len(data["elements"]) == 8


def test_stitch_variants() -> None:
    a = [(1, 0.0, 0.0), (2, 0.0, 1.0)]
    assert [n for n, *_ in tw._stitch([a, [(0, 0.0, -1.0), (1, 0.0, 0.0)]])] == [
        0,
        1,
        2,
    ]
    assert [n for n, *_ in tw._stitch([a, [(1, 0.0, 0.0), (0, 0.0, -1.0)]])] == [
        0,
        1,
        2,
    ]
    assert tw._stitch([]) == []
    with pytest.raises(ValueError, match="connected"):
        tw._stitch([a, [(8, 1.0, 1.0), (9, 2.0, 2.0)]])


def test_read_kml(tmp_path: Path) -> None:
    ns = 'xmlns="http://www.opengis.net/kml/2.2"'
    (tmp_path / "l.kml").write_text(
        f"<kml {ns}><Placemark><LineString><coordinates>"
        "-71.5,-31.9,100 -71.5,-31.896,110</coordinates></LineString></Placemark></kml>",
        encoding="utf-8",
    )
    t = tw.read_kml(tmp_path / "l.kml", "L")
    assert len(t) == 2 and t[1].elevation == pytest.approx(110.0)
    (tmp_path / "p.kml").write_text(
        "<kml><Placemark><Point><coordinates>-71.5,-31.9</coordinates></Point></Placemark>"
        "<Placemark><Point><coordinates>-71.5,-31.89</coordinates></Point></Placemark></kml>",
        encoding="utf-8",
    )
    p = tw.read_kml(tmp_path / "p.kml", "L")
    assert [x.seq for x in p] == [0, 1] and p[0].elevation is None
    (tmp_path / "e.kml").write_text("<kml></kml>", encoding="utf-8")
    with pytest.raises(ValueError, match="no LineString"):
        tw.read_kml(tmp_path / "e.kml", "L")


FREE_PDF = """\
      N°     VANO (m)   COORDENADAS UTM, WGS84, Huso 19     COTA (m)
 ESTRUCTURA             ESTE (m)       NORTE (m)
ML S/E ALFA    49,07    480000,00      6470000,00     149,62   ML
       1      101,51    480100,50      6470300,25     148,84   22A60
  pagina 2
       2      248,20    480200,00      6470600,00     147,30   22S5
"""


def test_parse_coordinate_text() -> None:
    t = tw.parse_coordinate_text(FREE_PDF, "L")
    assert [x.seq for x in t] == [0, 1, 2]
    assert t[0].lat < t[1].lat < t[2].lat
    with pytest.raises(ValueError, match="utm-zone"):
        tw.parse_coordinate_text(FREE_PDF.replace("Huso 19", ""), "L")
    ll = tw.parse_coordinate_text("1 -31,90 -71,50 x\n2 -71.49 -31.89\nfoo 3 4\n", "L")
    assert [round(x.lat, 2) for x in ll] == [-31.9, -31.89]
    with pytest.raises(ValueError, match="no coordinate pairs"):
        tw.parse_coordinate_text("nothing 1 2", "L")


def test_pdf_free_layout_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tw, "pdf_to_text", lambda _p: FREE_PDF)
    assert len(tw.read_structure_file(tmp_path / "x.pdf", "L")) == 3
