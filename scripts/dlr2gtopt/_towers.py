# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tower coordinates from CEN Infotecnica, OpenStreetMap or a manual file.

Sources, in order of preference:

``cen``
    The Coordinador Electrico Nacional (Chile) publishes, per line section,
    an annex "Listado de distribucion de estructuras" with the coordinates
    of every structure (Infotecnica, https://infotecnica.coordinador.cl,
    public REST API at https://api-infotecnica.coordinador.cl/v1, no login).
    The annex is a PDF (Excel export: column blocks printed one after the
    other), an XLSX/XLS, or occasionally an archive.  :class:`Infotecnica`
    lists and downloads the annexes (cached, polite); the parsers below
    read them.
``osm``
    OpenStreetMap ``power=line`` ways through the Overpass API
    (https://overpass-api.de).  Data (c) OpenStreetMap contributors, ODbL
    1.0 -- attribution is required wherever derived spans are published.
``manual``
    A CSV ``line,tower_seq,lat,lon[,elevation]`` or a KML with a LineString
    or ordered Placemark points (e.g. traced by hand on imagery whose terms
    allow it; never scraped).
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from dlr2gtopt._spans import Tower, haversine_m

logger = logging.getLogger("dlr2gtopt")

USER_AGENT = "gtopt-dlr2gtopt/1.0 (+https://github.com/gtopt-community/gtopt)"
INFOTECNICA_API = "https://api-infotecnica.coordinador.cl/v1"
OVERPASS_API = "https://overpass-api.de/api/interpreter"
OSM_ATTRIBUTION = "Data (c) OpenStreetMap contributors, ODbL 1.0"

HttpGet = Callable[[str], bytes]


def urllib_get(url: str, timeout: float = 120.0) -> bytes:
    """Plain HTTP GET with a generic User-Agent."""
    req = urllib.request.Request(  # noqa: S310 - callers pass https URLs
        url, headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return bytes(r.read())


class PoliteCache:
    """File cache in front of an HTTP getter, with a pause between requests."""

    def __init__(
        self,
        cache_dir: Path,
        get: HttpGet | None = None,
        pause: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 0,
        backoff: float = 30.0,
    ) -> None:
        self.cache_dir = cache_dir
        self.get = get or urllib_get
        self.pause = pause
        self.sleep = sleep
        self.retries = retries
        self.backoff = backoff
        self._last = -math.inf
        self.requests = 0

    def fetch(self, key: str, url: str) -> bytes:
        """Return the cached body for ``key`` or GET ``url`` and cache it.

        Transient failures (dropped connections, timeouts, HTTP 429/5xx) are
        retried ``retries`` times, waiting ``backoff * 2**k`` seconds; other
        HTTP errors (4xx) are raised at once."""
        path = self.cache_dir / key
        if path.exists():
            return path.read_bytes()
        for attempt in range(self.retries + 1):
            wait = self._last + self.pause - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            try:
                body = self.get(url)
                break
            except OSError as e:
                self._last = time.monotonic()
                code = getattr(e, "code", None)
                permanent = isinstance(code, int) and code < 500 and code != 429
                if permanent or attempt == self.retries:
                    raise
                logger.warning("retry %d/%d after %s", attempt + 1, self.retries, e)
                self.sleep(self.backoff * 2**attempt)
        self._last = time.monotonic()
        self.requests += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body


# ---------------------------------------------------------------------------
# CEN Infotecnica
# ---------------------------------------------------------------------------

_STRUCT_DOC = re.compile(
    r"(listado|distribuc).*estructura|estructura.*(listado|distribuc)"
)


def is_structure_list(doc_name: str) -> bool:
    """True for an annex named like "Listado de distribucion de estructuras"."""
    return bool(_STRUCT_DOC.search(doc_name.lower()))


class Infotecnica:
    """Read-only client of the CEN Infotecnica API (cached, polite)."""

    def __init__(self, cache: PoliteCache, api: str = INFOTECNICA_API) -> None:
        self.cache = cache
        self.api = api.rstrip("/")

    def _json(self, key: str, path: str) -> Any:
        return json.loads(self.cache.fetch(key, f"{self.api}/{path}"))

    def search_sections(self, text: str) -> list[dict[str, Any]]:
        """Line sections (``secciones-tramos``) whose name matches ``text``."""
        q = urllib.parse.quote(text)
        slug = re.sub(r"[^a-z0-9]+", "_", text.lower())
        data = self._json(
            f"sections_{slug}.json", f"secciones-tramos/extended/?search={q}"
        )
        return list(data.get("results", data) if isinstance(data, dict) else data)

    def documents(self, section_id: int) -> list[dict[str, Any]]:
        """Annex documents of one section."""
        return list(
            self._json(
                f"docs_{section_id}.json", f"secciones-tramos/{section_id}/documentos/"
            )
        )

    def structure_documents(self, section_id: int) -> list[dict[str, Any]]:
        """The "Listado de distribucion de estructuras" annexes of a section."""
        return [d for d in self.documents(section_id) if is_structure_list(d["nombre"])]

    def ficha(self, section_id: int, kind: str = "general") -> dict[str, Any]:
        """A technical sheet (``general``, ``limites-termicos``) of a section."""
        return dict(
            self._json(
                f"ficha_{section_id}_{kind}.json",
                f"secciones-tramos/{section_id}/fichas-tecnicas/{kind}/",
            )
        )

    def download(self, section_id: int, doc: dict[str, Any]) -> Path:
        """Download (or reuse) one annex; returns its cached path."""
        ext = doc.get("extension") or ""
        key = f"doc_{doc['id']}{ext}"
        self.cache.fetch(
            key, f"{self.api}/secciones-tramos/{section_id}/documentos/{doc['id']}/"
        )
        return self.cache.cache_dir / key


_ROW_A = re.compile(
    r"^\s*(?P<sigid>Estructura-\d+)\s+(?P<tramo>.+?)\s+(?P<circ>C\d+)\s+"
    r"(?P<sub>\S+)\s+(?P<x>-?\d+[.,]\d+)\s*$"
)
_ROW_B = re.compile(r"^\s*(?P<y>-?\d+[.,]\d+)\s+(?P<seq>\d+)\b(?P<rest>.*)$")
_ROW_FULL = re.compile(
    r"^\s*(?P<sigid>Estructura-\d+)\s+(?P<tramo>.+?)\s+(?P<circ>C\d+)\s+"
    r"(?P<sub>\S+)\s+(?P<x>-?\d+[.,]\d+)\s+(?P<y>-?\d+[.,]\d+)\s+(?P<seq>\d+)\b"
)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_structure_text(
    text: str, line: str, circuit: str | None = None
) -> list[Tower]:
    """Towers from the ``pdftotext -layout`` text of a CEN structure list.

    Two layouts are understood: one row per structure (SIGID ... COORD_X
    COORD_Y CORRELATIVO ...), and the Excel-export layout where the column
    block ``SIGID, LAT, CIRCUITO, SUBTIPO, COORD_X`` is printed for all
    rows first and ``COORD_Y, CORRELATIVO, ESTRUCTURA, TIPO`` after it (the
    two blocks are zipped in order).  ``circuit`` keeps only that circuit.
    """
    full: list[tuple[str, float, float, int]] = []
    a: list[tuple[str, float]] = []
    b: list[tuple[float, int]] = []
    for ln in text.splitlines():
        if m := _ROW_FULL.match(ln):
            full.append((m["circ"], _num(m["x"]), _num(m["y"]), int(m["seq"])))
        elif m := _ROW_A.match(ln):
            a.append((m["circ"], _num(m["x"])))
        elif m := _ROW_B.match(ln):
            b.append((_num(m["y"]), int(m["seq"])))
    if not full:
        if len(a) != len(b):
            raise ValueError(
                f"structure list: {len(a)} COORD_X rows but {len(b)} COORD_Y rows"
            )
        full = [(c, x, y, s) for (c, x), (y, s) in zip(a, b)]
    towers = [
        Tower(line=line, seq=s, lat=y, lon=x)
        for c, x, y, s in full
        if circuit is None or c == circuit
    ]
    if not towers:
        raise ValueError("structure list: no structure rows found")
    return sorted(towers, key=lambda t: t.seq)


def _token_num(tok: str) -> float | None:
    tok = tok.strip()
    if "," in tok:
        tok = tok.replace(".", "").replace(",", ".")
    try:
        return float(tok)
    except ValueError:
        return None


def _is_lat(v: float) -> bool:
    return -60.0 <= v <= -10.0


def _is_lon(v: float) -> bool:
    return -80.0 <= v <= -60.0


_HUSO = re.compile(r"(?:huso|zona|zone)\s*(\d{2})", re.IGNORECASE)


def parse_coordinate_text(
    text: str, line: str, utm_zone: int | None = None
) -> list[Tower]:
    """Fallback for free-layout structure lists: one tower per text line
    holding a coordinate pair, in line order.

    A pair is two consecutive numbers that look like a southern-hemisphere
    UTM easting (100 000-900 000) and northing (3 000 000-9 000 000), or a
    latitude (-60..-10) next to a longitude (-80..-60) in either order.
    The UTM zone is read from a "Huso NN" note in the text, else
    ``utm_zone``.
    """
    zone_m = _HUSO.search(text)
    zone = int(zone_m.group(1)) if zone_m else utm_zone
    towers: list[Tower] = []
    for ln in text.splitlines():
        nums = [_token_num(t) for t in ln.split()]
        for a, b in zip(nums, nums[1:]):
            if a is None or b is None:
                continue
            if 1e5 <= a <= 9e5 and 3e6 <= b <= 9e6:
                if zone is None:
                    raise ValueError("coordinate list: UTM coordinates need --utm-zone")
                lat, lon = utm_to_latlon(a, b, zone)
            elif _is_lat(a) and _is_lon(b):
                lat, lon = a, b
            elif _is_lon(a) and _is_lat(b):
                lat, lon = b, a
            else:
                continue
            towers.append(Tower(line=line, seq=len(towers), lat=lat, lon=lon))
            break
    if not towers:
        raise ValueError("coordinate list: no coordinate pairs found")
    return towers


def pdf_to_text(path: Path) -> str:
    """``pdftotext -layout`` (poppler-utils) of a PDF."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise RuntimeError("pdftotext (poppler-utils) is required to read PDFs")
    out = subprocess.run(
        [exe, "-layout", str(path), "-"], check=True, capture_output=True
    )
    return out.stdout.decode("utf-8", errors="replace")


def utm_to_latlon(
    easting: float, northing: float, zone: int, south: bool = True
) -> tuple[float, float]:
    """WGS84 UTM -> (lat, lon) in degrees (Karney-free series, < 1 mm)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = easting - 500000.0
    y = northing - (10000000.0 if south else 0.0)
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    n1 = a / math.sqrt(1 - e2 * math.sin(p1) ** 2)
    t1 = math.tan(p1) ** 2
    c1 = ep2 * math.cos(p1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(p1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = p1 - (n1 * math.tan(p1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(p1)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    return math.degrees(lat), math.degrees(lon0 + lon)


# Header words (accent-free, lower case), by priority.  A header matches a
# word when the word is the whole header or one of its words; e.g.
# "Coordenada Este", "Este UTM", "COORD_X" and "Longitud" all name X.
_HEADERS: dict[str, tuple[str, ...]] = {
    "x": ("coord x", "este", "easting", "longitud", "longitude", "lon", "x"),
    "y": ("coord y", "latitud", "latitude", "lat", "norte", "northing", "y", "sur"),
    "seq": ("correlativo", "corr", "secuencia", "seq"),
    "circ": ("circuito", "circuit", "nombre circuito"),
    "zone": ("huso", "zona", "zone"),
    "type": ("tipo estructura", "tipo", "type"),
}


def _norm(cell: object) -> str:
    text = unicodedata.normalize("NFKD", str(cell or "")).encode("ascii", "ignore")
    words = re.sub(r"[^a-z0-9]+", " ", text.decode().lower()).split()
    return " ".join(w for w in words if w not in ("utm", "coordenada", "de", "m"))


def _match(names: list[str], cands: tuple[str, ...]) -> list[int]:
    out: list[int] = []
    for cand in cands:
        for i, n in enumerate(names):
            if i not in out and (n == cand or f" {cand} " in f" {n} "):
                out.append(i)
    return out


def _cell_num(row: Sequence[object], i: int) -> float | None:
    try:
        return _num(str(row[i]))
    except (ValueError, IndexError):
        return None


def _valid_xy(x: float, y: float) -> bool:
    utm = 1e5 <= x <= 9e5 and 1e6 <= y <= 1e7
    return utm or (_is_lon(x) and _is_lat(y))


def _coord_pair(
    row: Sequence[object], xs: list[int], ys: list[int]
) -> tuple[float, float] | None:
    """First (x, y) of the candidate columns that is a plausible southern
    UTM or lat/lon pair (header words like "Longitud" may name a length)."""
    for i in xs:
        x = _cell_num(row, i)
        if x is None:
            continue
        for j in ys:
            y = _cell_num(row, j)
            if y is not None and _valid_xy(x, y):
                return x, y
    return None


def parse_structure_rows(
    rows: Sequence[Sequence[object]],
    line: str,
    circuit: str | None = None,
    utm_zone: int | None = None,
    utm_south: bool = True,
) -> list[Tower]:
    """Towers from a table (rows of cells), e.g. an XLSX structure list.

    The header row is the first one naming an X and a Y coordinate column
    (``COORD_X``/``COORD_Y``, ``Longitud``/``Latitud``, ``Coordenada Este``/
    ``Coordenada Norte``, ...; accents, case and "UTM" are ignored).  When
    several X or Y columns match (some CEN sheets mislabel the northing as
    "Sur"; "Longitud" may be a span length), each row uses the first pair
    that is a plausible southern UTM (E 1e5-9e5, N 1e6-1e7) or lat/lon
    position.  Coordinates with
    magnitude > 180 are UTM: the row's ``Huso`` column gives the zone, else
    ``utm_zone``.  Rows keep the table order unless a ``Correlativo``
    column is present.
    """
    for h, header in enumerate(rows):  # noqa: B007 - h is read after the loop
        names = [_norm(c) for c in header]
        col = {k: _match(names, c) for k, c in _HEADERS.items()}
        if col["x"] and col["y"]:
            break
    else:
        raise ValueError("structure table: no coordinate header row found")
    towers: list[Tower] = []
    for k, row in enumerate(rows[h + 1 :], start=1):
        pair = _coord_pair(row, col["x"], col["y"])
        if pair is None:
            continue
        x, y = pair
        if col["circ"] and circuit is not None:
            if circuit.lower() not in _norm(row[col["circ"][0]]).split():
                continue
        seq = k
        if col["seq"]:
            v = _cell_num(row, col["seq"][0])
            seq = k if v is None else int(v)
        if abs(x) > 180.0 or abs(y) > 90.0:
            zone = _cell_num(row, col["zone"][0]) if col["zone"] else None
            zone = zone if zone is not None else utm_zone
            if zone is None:
                raise ValueError("structure table: UTM coordinates need --utm-zone")
            lat, lon = utm_to_latlon(x, y, int(zone), utm_south)
        else:
            lat, lon = y, x
        stype = None
        if col["type"] and col["type"][0] < len(row):
            stype = str(row[col["type"][0]] or "").strip() or None
        towers.append(Tower(line=line, seq=seq, lat=lat, lon=lon, structure_type=stype))
    if not towers:
        raise ValueError("structure table: no coordinate rows")
    return sorted(towers, key=lambda t: t.seq)


def read_table_sheets(path: Path) -> list[list[list[object]]]:
    """Rows of every sheet of an XLSX/XLS, or of a CSV (one sheet)."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [
            [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets
        ]
    if suffix == ".xls":
        import xlrd

        book = xlrd.open_workbook(str(path))
        return [
            [sh.row_values(i) for i in range(sh.nrows)]
            for sh in (book.sheet_by_index(k) for k in range(book.nsheets))
        ]
    import csv

    with path.open(newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        return [[list(r) for r in csv.reader(f, dialect)]]


def read_structure_file(
    path: Path,
    line: str,
    circuit: str | None = None,
    utm_zone: int | None = None,
) -> list[Tower]:
    """Towers from a downloaded CEN structure list (PDF, XLSX, XLS or CSV).

    For a workbook, the sheet with the most structures is used."""
    if path.suffix.lower() == ".pdf":
        text = pdf_to_text(path)
        try:
            return parse_structure_text(text, line, circuit)
        except ValueError:
            return parse_coordinate_text(text, line, utm_zone)
    best: list[Tower] = []
    errors: list[str] = []
    for rows in read_table_sheets(path):
        try:
            towers = parse_structure_rows(rows, line, circuit, utm_zone)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if len(towers) > len(best):
            best = towers
    if not best:
        raise ValueError(f"{path.name}: {'; '.join(dict.fromkeys(errors))}")
    return best


# ---------------------------------------------------------------------------
# OpenStreetMap
# ---------------------------------------------------------------------------


def overpass_query(
    way_ids: Iterable[int] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    voltage: str | None = None,
    name: str | None = None,
) -> str:
    """Overpass QL for power lines (by way id, or bbox + voltage/name) and
    their tower nodes.  ``bbox`` is (south, west, north, east)."""
    if way_ids:
        sel = f"way(id:{','.join(str(i) for i in way_ids)})"
    elif bbox is not None:
        flt = '["power"="line"]'
        if voltage:
            flt += f'["voltage"~"{voltage}"]'
        if name:
            flt += f'["name"~"{name}",i]'
        s, w, n, e = bbox
        sel = f"way{flt}({s},{w},{n},{e})"
    else:
        raise ValueError("overpass: give way ids or a bbox")
    return f"[out:json][timeout:180];{sel}->.l;(.l;node(w.l););out body geom;"


def fetch_osm(cache: PoliteCache, query: str, api: str = OVERPASS_API) -> Any:
    """Run (or reuse) an Overpass query; the cache key is the query hash."""
    import hashlib

    key = (
        "osm_"
        + hashlib.sha1(query.encode(), usedforsecurity=False).hexdigest()[:16]
        + ".json"
    )
    url = f"{api}?data={urllib.parse.quote(query)}"
    return json.loads(cache.fetch(key, url))


def _stitch(
    chains: list[list[tuple[int, float, float]]],
) -> list[tuple[int, float, float]]:
    """Join way vertex chains that share end nodes into one path."""
    if not chains:
        return []
    path = list(chains[0])
    rest = [list(c) for c in chains[1:]]
    while rest:
        for i, c in enumerate(rest):
            if c[0][0] == path[-1][0]:
                path += c[1:]
            elif c[-1][0] == path[-1][0]:
                path += c[::-1][1:]
            elif c[-1][0] == path[0][0]:
                path = c[:-1] + path
            elif c[0][0] == path[0][0]:
                path = c[::-1][:-1] + path
            else:
                continue
            rest.pop(i)
            break
        else:
            raise ValueError("osm: the ways do not form one connected path")
    return path


def towers_from_osm(
    data: dict[str, Any], line: str, towers_only: bool = False
) -> list[Tower]:
    """Towers along the (stitched) ``power=line`` ways of an Overpass reply.

    Way vertices are towers on a tower-traced line.  ``towers_only`` keeps
    only vertices tagged ``power=tower``/``pole`` (drops bends drawn by
    hand).
    """
    elements = data.get("elements", [])
    tagged = {
        e["id"]
        for e in elements
        if e.get("type") == "node"
        and e.get("tags", {}).get("power") in ("tower", "pole", "portal")
    }
    chains = []
    for e in elements:
        if e.get("type") != "way":
            continue
        geom = e.get("geometry") or []
        nodes = e.get("nodes") or list(range(-len(geom), 0))
        chains.append([(n, g["lat"], g["lon"]) for n, g in zip(nodes, geom)])
    path = _stitch(chains)
    if towers_only:
        path = [p for p in path if p[0] in tagged]
    return [
        Tower(line=line, seq=k, lat=la, lon=lo) for k, (_, la, lo) in enumerate(path)
    ]


def osm_tower_traced(towers: list[Tower], lo: float = 150.0, hi: float = 700.0) -> bool:
    """Heuristic: a way is tower-traced when its median vertex spacing is a
    plausible span length (default 150-700 m) and it has >= 5 vertices."""
    if len(towers) < 5:
        return False
    d = sorted(
        haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(towers, towers[1:])
    )
    return lo <= d[len(d) // 2] <= hi


# ---------------------------------------------------------------------------
# Manual (KML)
# ---------------------------------------------------------------------------


def read_kml(path: Path, line: str) -> list[Tower]:
    """Towers from a KML: the first LineString, else the Placemark points in
    document order."""
    root = ET.parse(path).getroot()  # noqa: S314 -- local, user-supplied file
    ns = {"k": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    pre = "k:" if ns else ""

    def coords(text: str) -> list[tuple[float, float, float | None]]:
        out = []
        for tok in text.split():
            parts = [float(v) for v in tok.split(",")]
            out.append((parts[1], parts[0], parts[2] if len(parts) > 2 else None))
        return out

    ls = root.find(f".//{pre}LineString/{pre}coordinates", ns)
    if ls is not None and ls.text:
        pts = coords(ls.text)
    else:
        pts = []
        for pt in root.findall(f".//{pre}Point/{pre}coordinates", ns):
            pts += coords(pt.text or "")
    if not pts:
        raise ValueError(f"{path}: no LineString or Point coordinates")
    return [
        Tower(line=line, seq=k, lat=la, lon=lo, elevation=el or None)
        for k, (la, lo, el) in enumerate(pts)
    ]
