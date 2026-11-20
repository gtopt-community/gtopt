"""Generic parsers for classic PSR **SDDP** ``.dat`` files (monthly/weekly).

The original ``.dat`` front-end (:mod:`sddp2gtopt.dat_parsers`) targets the
PSR **NCP** short-term dispatch dialect (hourly ``cpde`` demand, Guatemala
``gu`` suffix).  A medium-term PSR SDDP study — e.g. the Peruvian COES
``pe`` cases — ships a different, richer file set:

| file pattern            | holds                                         |
|-------------------------|-----------------------------------------------|
| ``deme<NB><sfx>.dat``   | per-block demand [GWh] per load class          |
| ``dbf<NB><sfx>.dat``    | bus participation factors of each load class  |
| ``duvame<NB>.dat``      | variable block durations [h] per month         |
| ``hinflw.dat``          | historical monthly inflows per gauging station |
| ``cgnd<sfx>.dat``       | renewable plants (+ ``mgnd`` modifications)    |
| ``blrenw<NB><sfx>.dat`` | per-scenario/block renewable p.u. profiles     |
| ``cbattery<sfx>.dat``   | batteries (+ ``mbattery`` modifications)       |
| ``m*<sfx>.dat`` tables  | monthly schedules (volumes, outflows, …)       |
| ``pm*<sfx>.dat``        | maintenance (available % per plant)           |
| ``rgme<NB><sfx>.dat``   | generation constraints (``>`` / ``<``)         |
| ``dcirc``/``mcirc``     | network circuits (R/X in % on 100 MVA)         |

Everything here is **format** knowledge only (fixed-width records, the
``**** <code> …`` + ``A~NO`` monthly-table layout, ``MOD: dd/mm/yyyy``
modification blocks); the model semantics live in
:mod:`sddp2gtopt.sddp_case`.

Fixed-width convention: PSR right-aligns numeric fields under a dotted
header label (``.VMax..``), and values may overflow into the separating
blank (``1.246410.905`` = R ``1.2464`` + X ``10.905``).  So a numeric
field spans from the *end of the previous header token* to the end of
its own token; text fields (12-char PSR names) are left-aligned at their
label's start.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from gtopt_shared.compressed_open import open_binary, read_text

logger = logging.getLogger(__name__)

# PSR names are at most 12 characters.
NAME_WIDTH = 12

Date = tuple[int, int, int]  # (year, month, day)


# ─────────────────────────────── basic helpers ──────────────────────────────


def read_lines(path: str | Path) -> list[str]:
    """Read a (possibly compressed) Latin-1 ``.dat`` as rstripped lines."""
    text = read_text(Path(path), encoding="latin-1", errors="replace")
    return [ln.rstrip("\r\n").rstrip() for ln in text.splitlines()]


def to_float(token: str | None) -> float | None:
    """Parse a PSR numeric token (``50.``, ``.237E-4``); blank → ``None``."""
    if token is None:
        return None
    tok = token.strip()
    if not tok:
        return None
    try:
        return float(tok)
    except ValueError:
        return None


def to_int(token: str | None) -> int | None:
    """Parse an integer token (``"  12"``, ``"12."``); blank/bad → ``None``."""
    val = to_float(token)
    return int(val) if val is not None else None


def parse_date(token: str) -> Date | None:
    """Parse ``dd/mm/yyyy`` → ``(year, month, day)``."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", token)
    if not m:
        return None
    return int(m.group(3)), int(m.group(2)), int(m.group(1))


@dataclass(frozen=True)
class FieldSpan:
    """One fixed-width column derived from a header row."""

    label: str
    start: int  # label start (text fields)
    end: int  # label end (exclusive)
    num_start: int  # numeric field start (= previous label end)


def clean_label(label: str) -> str:
    """``'.VMax..'`` → ``'VMax'``; ``'!Num'`` → ``'Num'``; ``'(  MW)'`` → ``'MW'``."""
    return re.sub(r"[.!()\s]", "", label)


def header_spans(header: str) -> list[FieldSpan]:
    """Column spans from a PSR fixed-width header row."""
    spans: list[FieldSpan] = []
    prev_end = 0
    for m in re.finditer(r"\S+", header):
        spans.append(
            FieldSpan(
                label=clean_label(m.group()),
                start=m.start(),
                end=m.end(),
                num_start=prev_end,
            )
        )
        prev_end = m.end()
    return spans


def fw_record(
    line: str,
    spans: list[FieldSpan],
    text_labels: tuple[str, ...] = ("Nombre", "Name", "Nome", "NombreB"),
) -> dict[str, str]:
    """Slice a fixed-width record into ``{label: raw text}``.

    Repeated labels keep the first occurrence under the bare label and
    later ones as ``label@2``, ``label@3`` … (see :func:`span_keys`).

    Numeric fields take ``line[num_start:end]``; text labels (names) take
    the left-aligned ``NAME_WIDTH`` window starting at the label.
    """
    out: dict[str, str] = {}
    for key, sp in zip(span_keys(spans), spans):
        if sp.label in text_labels:
            out[key] = line[sp.start : sp.start + NAME_WIDTH].strip()
        else:
            out[key] = line[sp.num_start : sp.end].strip()
    return out


def span_keys(spans: list[FieldSpan]) -> list[str]:
    """Record keys per span: the label, ``label@2``/``@3`` … for repeats."""
    seen: dict[str, int] = {}
    keys: list[str] = []
    for sp in spans:
        n = seen.get(sp.label, 0) + 1
        seen[sp.label] = n
        keys.append(sp.label if n == 1 else f"{sp.label}@{n}")
    return keys


# ───────────────────────────── control / system ─────────────────────────────


@dataclass
class SddpControl:
    """Study-control fields of a PSR SDDP ``sddp.dat`` beyond the NCP subset."""

    stage_type: int = 2
    initial_stage: int = 1
    initial_year: int = 2000
    num_stages: int = 1
    num_blocks: int = 1
    num_series: int = 1
    inflow_type: int = 2  # 0=read, 1=ARP, 2=historical, 3=forw&back
    hydro_initial_month: int = 1
    hydro_initial_year: int = 0
    use_network: bool = False
    use_losses: bool = False
    discount_rate: float = 0.0
    deficit_cost: float = 1000.0
    deficit_segments: list[tuple[float, float]] = field(default_factory=list)
    spill_penalty: float = 0.0  # k$/hm³ (``vert``)
    selected_series: list[int] = field(default_factory=list)


def parse_sddp_control(path: str | Path) -> SddpControl:
    """Parse the keyword-per-line ``sddp.dat`` (ES/PT/EN labels)."""
    lines = read_lines(path)
    ctl = SddpControl()

    def kv(*keys: str) -> float | None:
        for ln in lines:
            up = ln.upper()
            for key in keys:
                if up.lstrip().startswith(key):
                    toks = ln[len(ln) - len(ln.lstrip()) + len(key) :].split()
                    for t in toks:
                        v = to_float(t)
                        if v is not None:
                            return v
        return None

    def set_int(attr: str, *keys: str) -> None:
        v = kv(*keys)
        if v is not None:
            setattr(ctl, attr, int(v))

    set_int("stage_type", "ETAPA ")
    set_int("initial_stage", "MES/SEMANA INICIAL")
    set_int("initial_year", "ANO INICIAL", "A~NO INICIAL")
    set_int("num_stages", "NUMERO DE ETAPAS")
    set_int("num_blocks", "NUMERO DE BLOQUES DEMANDA")
    set_int("num_series", "NUMERO DE SERIES")
    set_int("inflow_type", "CAUDALES")
    set_int("hydro_initial_month", "MES  COND. INIC. HIDRO", "MES COND. INIC. HIDRO")
    set_int("hydro_initial_year", "ANO COND. INIC. HIDRO", "A~NO COND. INIC. HIDRO")
    net = kv("REPRESENTACION DE LA RED")
    ctl.use_network = bool(net and net > 0)
    loss = kv("PERDAS TRANSMISION", "PERDIDAS TRANSMISION")
    ctl.use_losses = bool(loss and loss > 0)
    rate = kv("TASA DE DESCUENTO")
    ctl.discount_rate = rate if rate is not None else 0.0

    for i, ln in enumerate(lines):
        up = ln.upper()
        if "PEN. DEFL" in up and "VERT" in up:
            tail = up.split("VERT", 1)[1].split()
            vals = [to_float(t) for t in tail]
            nums = [v for v in vals if v is not None]
            if nums:
                ctl.spill_penalty = nums[0]
        if "COSTO DEFICIT" in up or "CUSTO DEFICIT" in up:
            for nxt in lines[i + 1 :]:
                toks = nxt.split()
                if len(toks) >= 2 and to_float(toks[0]) is not None:
                    seg_vals = [to_float(t) for t in toks]
                    segs = [
                        (pct, cost)
                        for pct, cost in zip(seg_vals[0::2], seg_vals[1::2])
                        if pct is not None and cost is not None and pct > 0
                    ]
                    if segs:
                        ctl.deficit_cost = segs[0][1]
                        ctl.deficit_segments = segs
                    break
        if "SERIES A SIMULAR" in up and "---" in ln:
            for nxt in lines[i + 1 : i + 4]:
                toks = nxt.split()
                ints = [to_int(t) for t in toks]
                if toks and all(v is not None for v in ints):
                    vals_i = [v for v in ints if v is not None]
                    ctl.selected_series = vals_i[1 : 1 + vals_i[0]]
                    break
    return ctl


def parse_system_suffix(path: str | Path) -> str:
    """Return the system file suffix (``ID`` column) of ``sistem.dat``."""
    lines = read_lines(path)
    if not lines:
        return ""
    spans = header_spans(lines[0])
    labels = [s.label for s in spans]
    for ln in lines[1:]:
        if not ln.strip() or to_int(ln[:5]) is None:
            continue
        if "ID" in labels:
            idx = labels.index("ID")
            sp = spans[idx]
            # ID is left-aligned under its label (2-char system code).
            return ln[sp.start : sp.start + 2].strip().lower()
        toks = ln.split()
        return toks[-1].lower() if len(toks) >= 3 else ""
    return ""


# ──────────────────────────── fixed-width tables ────────────────────────────


def parse_fw_file(
    path: str | Path,
    *,
    key_label: str | None = None,
) -> tuple[list[FieldSpan], list[dict[str, str]]]:
    """Parse a single-header fixed-width file (``chidro``/``ctermi``/``cgnd``).

    The header is the first line containing ``key_label`` (or the first
    ``!``/blank-led line); every following line with a leading integer is a
    record.
    """
    lines = read_lines(path)
    hdr_idx = 0
    for i, ln in enumerate(lines):
        if ln.startswith("$"):
            continue
        if key_label is None or key_label in ln:
            hdr_idx = i
            break
    spans = header_spans(lines[hdr_idx].replace("!", " "))
    recs: list[dict[str, str]] = []
    for ln in lines[hdr_idx + 1 :]:
        if not ln.strip() or ln.lstrip().startswith(("!", "$")):
            continue
        if to_int(ln[: spans[0].end]) is None:
            continue
        recs.append(fw_record(ln, spans))
    return spans, recs


def parse_mod_file(path: str | Path) -> list[tuple[Date, dict[str, str]]]:
    """Parse a ``MOD: dd/mm/yyyy`` modification file (``mtermi``/``mhidro``/``mcirc``).

    Each block is ``MOD: <date>``, a header row, then fixed-width records
    laid out like the base file.  Blank fields mean *unchanged*; the raw
    strings are returned so the caller can tell blank from zero.
    """
    out: list[tuple[Date, dict[str, str]]] = []
    cur: Date | None = None
    spans: list[FieldSpan] | None = None
    for ln in read_lines(path):
        if not ln.strip() or ln.startswith("$"):
            continue
        if ln.startswith("MOD:"):
            cur = parse_date(ln[4:])
            spans = None
            continue
        if spans is None:
            spans = header_spans(ln.replace("!", " "))
            continue
        if cur is None or to_int(ln[: spans[0].end]) is None:
            continue
        out.append((cur, fw_record(ln, spans)))
    return out


def parse_dated_fw_file(path: str | Path) -> list[tuple[Date, dict[str, str]]]:
    """Parse a dated fixed-width file (``mgnd``: ``d/mm/yyyy .Num Name …``)."""
    lines = [ln for ln in read_lines(path) if ln.strip() and not ln.startswith("$")]
    hdr = next((ln for ln in lines if ln.startswith("!")), None)
    if hdr is None:
        return []
    spans = header_spans(hdr.replace("!", " "))
    out: list[tuple[Date, dict[str, str]]] = []
    for ln in lines:
        if ln.startswith("!"):
            continue
        date = parse_date(ln)
        if date is None:
            continue
        out.append((date, fw_record(ln, spans)))
    return out


def parse_csv_dat(path: str | Path) -> list[dict[str, str]]:
    """Parse a comma-separated PSR ``.dat`` whose header is a ``!``-led row."""
    lines = [ln for ln in read_lines(path) if ln.strip() and not ln.startswith("$")]
    hdr_i = next((i for i, ln in enumerate(lines) if ln.startswith("!")), None)
    if hdr_i is None:
        return []
    names = [h.strip() for h in lines[hdr_i].lstrip("!").split(",")]
    out: list[dict[str, str]] = []
    for row in csv.reader(io.StringIO("\n".join(lines[hdr_i + 1 :]))):
        if not row or not "".join(row).strip():
            continue
        out.append({names[i]: row[i].strip() for i in range(min(len(names), len(row)))})
    return out


# ───────────────────────────── monthly tables ───────────────────────────────


@dataclass
class MonthlyTable:
    """One ``****`` block of a PSR monthly schedule file.

    ``values[(year, block)]`` holds 12 monthly values (``None`` = blank);
    files without a ``Blk`` column use ``block = 0``.
    """

    code: int
    name: str = ""
    penalty: float | None = None
    penalty_values: list[float] = field(default_factory=list)
    sense: str = ""  # rgme: ``>`` / ``<`` / ``=``
    target: tuple[str, int] | None = None  # rgme: (``H``/``T``/``R``, plant code)
    tag: str = ""  # trailing class tag (rrme: ``h``/``t``)
    # Per-table ``<units> <info>`` override written after the name by
    # SDDP ≥ 17 (``**** Planta: 601 AGU-H   2  1``); ``None`` = file level.
    units: int | None = None
    info_type: int | None = None
    values: dict[tuple[int, int], list[float | None]] = field(default_factory=dict)

    def get(self, year: int, month: int, block: int = 0) -> float | None:
        """Value for (year, month[1-12], block); ``None`` when absent/blank."""
        row = self.values.get((year, block))
        if row is None:
            return None
        return row[month - 1]


@dataclass
class MonthlyFile:
    """A parsed monthly-table file plus its ``Tipo Info``/``Unidades`` header."""

    tables: list[MonthlyTable] = field(default_factory=list)
    info_type: int | None = None  # 1 = reduction, 2 = absolute
    units: int | None = None  # 1=#units, 2=%, 3=MW, 4=m³/s, 12=p.u.

    def by_code(self) -> dict[int, MonthlyTable]:
        """Index the tables by their record code (last one wins)."""
        return {t.code: t for t in self.tables}


_HEAD_LABELS = re.compile(r"^(\*{4}|NUM:)\s*")


def _parse_table_header(line: str) -> MonthlyTable:
    body = _HEAD_LABELS.sub("", line)
    sense = ""
    target: tuple[str, int] | None = None
    m_sense = re.search(r":([<>=]):", body)
    if m_sense:
        sense = m_sense.group(1)
    m_tgt = re.search(r"\b([HTRB]):\s*(\d+)", body)
    if m_tgt:
        target = (m_tgt.group(1), int(m_tgt.group(2)))
    penalty: float | None = None
    pen_vals: list[float] = []
    m_pen = re.search(r"Penal:\s*(.*)$", body)
    if m_pen:
        nums = [to_float(t) for t in m_pen.group(1).split()]
        pen_vals = [v for v in nums if v is not None]
        # First number is the penalty; ``riegmepe`` prefixes an integer flag
        # (``Penal: 0  10.``) — prefer the first value written with a dot.
        dotted = [
            to_float(t)
            for t in m_pen.group(1).split()
            if "." in t and to_float(t) is not None
        ]
        penalty = dotted[0] if dotted else (pen_vals[0] if pen_vals else None)
        body = body[: m_pen.start()]
    # Drop the leading label ("Planta:", "Load  :", "Res ger").
    body = re.sub(r"^(Planta|Load|Fuel|Res\s+ger)\s*:?\s*", "", body.strip())
    code = 0
    name = ""
    toks = body.split()
    rest = body
    for tok in toks:
        v = to_int(tok)
        if v is not None and v >= 0 and re.fullmatch(r"-?\d+\.?", tok):
            code = v
            rest = body[body.find(tok) + len(tok) :]
            break
        if v is None:
            break
    rest = re.split(r":[<>=]:|Sgmnt:|\s{3,}", rest.lstrip(), maxsplit=1)[0]
    name = rest[:NAME_WIDTH].strip()
    tag = ""
    tail = line.rstrip().split()
    if tail and len(tail[-1]) == 1 and tail[-1].isalpha():
        tag = tail[-1].lower()
    units: int | None = None
    info: int | None = None
    m_flags = re.search(r"\S\s{3,}(\d)\s+(\d)\s*$", line.rstrip())
    if m_flags and m_pen is None and not sense:
        units, info = int(m_flags.group(1)), int(m_flags.group(2))
    return MonthlyTable(
        code=code,
        name=name,
        penalty=penalty,
        penalty_values=pen_vals,
        sense=sense,
        target=target,
        tag=tag,
        units=units,
        info_type=info,
    )


def parse_monthly_file(path: str | Path) -> MonthlyFile:
    """Parse a PSR monthly schedule file (``vmin``/``mxto``/``pmtr``/``deme`` …).

    Layout::

        Tipo Info. :    2  (1=Reduc;2=Absoluto)      # optional
        Unidades   :    2  (1=No.Unid;2=%;3=MW)      # optional
        **** Planta: 9169 Junin Lago   Penal:   50.
        A~NO ......1 ......2 … .....12               # or "A~no Blk ....1 …"
        2026   144.3   144.3 …

    Column widths come from the ``A~NO`` header (see :func:`header_spans`).
    """
    mf = MonthlyFile()
    cur: MonthlyTable | None = None
    spans: list[FieldSpan] = []
    has_blk = False
    for ln in read_lines(path):
        if not ln.strip() or ln.startswith("$"):
            continue
        low = ln.lower()
        if low.startswith("tipo info"):
            mf.info_type = to_int(ln.split(":", 1)[1].split()[0])
            continue
        if low.startswith("unidades"):
            mf.units = to_int(ln.split(":", 1)[1].split()[0])
            continue
        if _HEAD_LABELS.match(ln):
            cur = _parse_table_header(ln)
            mf.tables.append(cur)
            continue
        if low.startswith(("a~no", "ano ", "year")):
            spans = header_spans(ln)
            has_blk = len(spans) > 1 and spans[1].label.lower() in ("blk", "bloq")
            if cur is None:  # headless single table (``duvame``)
                cur = MonthlyTable(code=0)
                mf.tables.append(cur)
            continue
        if cur is None or not spans:
            continue
        year = to_int(ln[: spans[0].end])
        if year is None:
            continue
        first = 2 if has_blk else 1
        blk = to_int(ln[spans[0].end : spans[1].end]) if has_blk else 0
        vals: list[float | None] = []
        for sp in spans[first : first + 12]:
            vals.append(to_float(ln[sp.num_start : sp.end]))
        while len(vals) < 12:
            vals.append(None)
        cur.values[(year, blk or 0)] = vals
    return mf


# ─────────────────────────── demand / durations ─────────────────────────────


def parse_bus_load_factors(
    path: str | Path,
) -> dict[tuple[int, Date], dict[int, dict[int, float]]]:
    """Parse ``dbf*.dat`` → ``{(load_code, date): {block: {bus: weight}}}``.

    Records ``BCod Bus.Name LCod dd/mm/yyyy Llev Load Icca``; ``Load`` is a
    per-bus weight of the load class at that block (normalised by the
    caller).  Names may contain blanks, so fields are taken from the right.
    """
    out: dict[tuple[int, Date], dict[int, dict[int, float]]] = {}
    for ln in read_lines(path):
        if not ln.strip() or ln.lstrip().startswith(("!", "$")):
            continue
        toks = ln.split()
        if len(toks) < 6:
            continue
        bus = to_int(toks[0])
        lcod = to_int(toks[-5])
        date = parse_date(toks[-4])
        blk = to_int(toks[-3])
        val = to_float(toks[-2])
        if None in (bus, lcod, blk, val) or date is None:
            continue
        assert bus is not None and lcod is not None and blk is not None
        out.setdefault((lcod, date), {}).setdefault(blk, {})[bus] = val or 0.0
    return out


def parse_load_classes(path: str | Path) -> dict[int, str]:
    """Parse ``cloads*.dat`` → ``{load_code: name}``."""
    out: dict[int, str] = {}
    for ln in read_lines(path):
        toks = ln.split()
        if len(toks) >= 2 and to_int(toks[0]) is not None:
            code = to_int(toks[0])
            assert code is not None
            out[code] = toks[1]
    return out


def parse_fixed_durations(path: str | Path) -> list[float]:
    """Parse ``durfix*.dat`` → per-block duration in % of the stage."""
    out: list[float] = []
    for ln in read_lines(path):
        v = to_float(ln.split()[0]) if ln.split() else None
        if v is not None:
            out.append(v)
    return out


# ──────────────────────────────── hydrology ─────────────────────────────────


def parse_hist_inflows(path: str | Path) -> dict[int, dict[tuple[int, int], float]]:
    """Parse ``hinflw.dat`` → ``{station: {(year, month): m³/s}}``.

    Header ``mm/yyyy`` + station codes, then one row per month; every
    field is 7 characters wide (values often touch: ``1.396379.80792``).
    """
    lines = [ln for ln in read_lines(path) if ln.strip()]
    if not lines:
        return {}
    width = 7
    hdr = lines[0]
    n = (len(hdr) - width + width - 1) // width
    codes = [to_int(hdr[width + width * i : 2 * width + width * i]) for i in range(n)]
    out: dict[int, dict[tuple[int, int], float]] = {c: {} for c in codes if c}
    for ln in lines[1:]:
        m = re.match(r"\s*(\d{1,2})/(\d{4})", ln[:width])
        if not m:
            continue
        key = (int(m.group(2)), int(m.group(1)))
        for i, code in enumerate(codes):
            if not code:
                continue
            v = to_float(ln[width + width * i : 2 * width + width * i])
            out[code][key] = v if v is not None else 0.0
    return out


# ───────────────────────────── renewables etc. ──────────────────────────────


def parse_block_profiles(
    path: str | Path,
    *,
    years: set[int] | None = None,
) -> tuple[list[str], dict[tuple[int, int, int, int], list[float]]]:
    """Parse ``blrenw*.dat`` (``Scn,Year,Month,Block,<station…>``, p.u.).

    Returns ``(station_names, {(scn, year, month, block): [values…]})``.
    ``years`` filters rows early (the per-scenario file is ~100 MB).
    """
    with open_binary(Path(path)) as fh:
        raw = fh.read(4)
    if raw[:1] not in (b"$", b"!", b" ") and raw[1:4] == b"\x00\x00\x00":
        return parse_binary_block_profiles(path)
    lines = read_lines(path)
    hdr_i = next((i for i, ln in enumerate(lines) if ln.startswith("!")), None)
    if hdr_i is None:
        return [], {}
    names = [h.strip() for h in lines[hdr_i].lstrip("!").split(",")][4:]
    out: dict[tuple[int, int, int, int], list[float]] = {}
    for ln in lines[hdr_i + 1 :]:
        if not ln or ln.startswith(("!", "$")):
            continue
        head = ln.split(",", 4)
        if len(head) < 5:
            continue
        try:
            scn, yr, mo, blk = (int(head[0]), int(head[1]), int(head[2]), int(head[3]))
        except ValueError:
            continue
        if years is not None and yr not in years:
            continue
        vals = [to_float(x) or 0.0 for x in head[4].split(",")]
        out[(scn, yr, mo, blk)] = vals
    return names, out


def parse_stage_profiles(
    path: str | Path,
) -> tuple[list[str], dict[tuple[int, int, int], list[float]]]:
    """Parse ``gndme*.dat`` (``Stg NScn LBlk <station…>``, p.u.).

    Older SDDP renewable scenarios indexed by study stage:
    ``{(scenario, stage, block): [values…]}``.
    """
    lines = read_lines(path)
    hdr = next((ln for ln in lines if ln.startswith("!Stg")), None)
    if hdr is None:
        return [], {}
    names = hdr.lstrip("!").split()[3:]
    out: dict[tuple[int, int, int], list[float]] = {}
    for ln in lines:
        toks = ln.split()
        if ln.startswith("!") or len(toks) < 4:
            continue
        stg, scn, blk = to_int(toks[0]), to_int(toks[1]), to_int(toks[2])
        if stg is None or scn is None or blk is None:
            continue
        out[(scn, stg, blk)] = [to_float(t) or 0.0 for t in toks[3:]]
    return names, out


def _fortran_records(data: bytes, count: int, pos: int = 0) -> tuple[list[bytes], int]:
    """Read ``count`` Fortran sequential records (4-byte length markers)."""
    out: list[bytes] = []
    for _ in range(count):
        (n,) = struct.unpack_from("<i", data, pos)
        out.append(data[pos + 4 : pos + 4 + n])
        (tail,) = struct.unpack_from("<i", data, pos + 4 + n)
        if tail != n:
            raise ValueError(f"bad Fortran record marker at byte {pos}")
        pos += n + 8
    return out, pos


def parse_binary_block_profiles(
    path: str | Path,
) -> tuple[list[str], dict[tuple[int, int, int, int], list[float]]]:
    """Parse a binary typical-year profile (``blrenw<nb>0.dat``, SDDP ≥ 17).

    Layout (little-endian Fortran sequential header + raw ``float32``
    body): a version record; a 10-int + 7-char unit + int record whose
    ints[1] is the number of stages, ints[3] the number of agents and
    ints[9] the initial year; a record of ``stages + 1`` cumulative block
    offsets; one 12-char name record per agent; then one row of
    ``agents`` floats per (stage, block), stage-major.  Stages are
    calendar months of a typical year, so keys use ``year = 0`` like the
    CSV ``blrenw…0.dat`` files: ``{(1, 0, month, block): [values…]}``.
    """
    with open_binary(Path(path)) as fh:
        data = fh.read()
    recs, pos = _fortran_records(data, 2)
    ints = struct.unpack_from("<10i", recs[1], 0)
    n_stages, n_agents = ints[1], ints[3]
    offs_rec, pos = _fortran_records(data, 1, pos)
    offsets = list(struct.unpack_from(f"<{n_stages + 1}i", offs_rec[0], 0))
    name_recs, pos = _fortran_records(data, n_agents, pos)
    names = [r.decode("latin-1").strip() for r in name_recs]
    width = 4 * n_agents
    out: dict[tuple[int, int, int, int], list[float]] = {}
    for st in range(n_stages):
        for blk in range(offsets[st + 1] - offsets[st]):
            row_at = pos + (offsets[st] + blk) * width
            if row_at + width > len(data):
                break
            vals = struct.unpack_from(f"<{n_agents}f", data, row_at)
            out[(1, 0, st % 12 + 1, blk + 1)] = [float(v) for v in vals]
    return names, out


def parse_dbus(path: str | Path) -> tuple[dict[int, str], dict[tuple[int, int], int]]:
    """Parse ``dbus.dat`` → ``({bus: name}, {(gen_type, plant_code): bus})``.

    ``gen_type`` is PSR's ``tg`` (1 = hydro, 2 = thermal).  A bus row may be
    repeated once per connected generator.
    """
    lines = read_lines(path)
    buses: dict[int, str] = {}
    gens: dict[tuple[int, int], int] = {}
    if any(ln.startswith("!Code,") for ln in lines[:3]):
        # SDDP ≥ 17 CSV layout; generator connections live in
        # ``busconnection.dat`` (see :func:`parse_bus_connections`).
        for r in parse_csv_dat(path):
            num = to_int(r.get("Code"))
            if num is not None:
                buses.setdefault(num, r.get("Name", "") or f"b{num}")
        return buses, gens
    spans = header_spans(lines[0])
    for ln in lines[1:]:
        rec = fw_record(ln, spans)
        num = to_int(rec.get("NUM"))
        if num is None:
            continue
        buses.setdefault(num, rec.get("NombreB", "") or f"b{num}")
        tg = to_int(rec.get("tg"))
        plnt = to_int(rec.get("Plnt"))
        if tg is not None and plnt is not None:
            gens[(tg, plnt)] = num
    return buses, gens


PLANT_TYPE_CODE = {"H": 1, "T": 2, "R": 3, "B": 4}


def parse_bus_connections(path: str | Path) -> dict[tuple[int, int], int]:
    """Parse ``busconnection.dat`` → ``{(gen_type, plant_code): bus}``.

    ``SystemId,BusCode,PlantType,PlantCode`` with ``PlantType`` in
    ``H``/``T``/``R``/``B`` (mapped to 1/2/3/4, ``dbus`` ``tg`` codes).
    """
    out: dict[tuple[int, int], int] = {}
    for r in parse_csv_dat(path):
        bus = to_int(r.get("BusCode"))
        code = to_int(r.get("PlantCode"))
        kind = PLANT_TYPE_CODE.get(r.get("PlantType", "").strip().upper())
        if bus is not None and code is not None and kind is not None:
            out[(kind, code)] = bus
    return out


def parse_volt(path: str | Path) -> dict[int, float]:
    """Parse ``volt.dat`` (``Bus Nome O kV``) → ``{bus: kV}``."""
    out: dict[int, float] = {}
    for ln in read_lines(path):
        toks = ln.split()
        if len(toks) >= 2 and to_int(toks[0]) is not None and "(" not in ln:
            kv = to_float(toks[-1])
            if kv and kv > 0:
                out[int(toks[0])] = kv
    return out


@dataclass
class SddpCircuit:
    """A PSR SDDP circuit (``dcirc`` v2, R/X in % on the 100 MVA base)."""

    code: int
    from_bus: int
    to_bus: int
    name: str
    r_pct: float
    x_pct: float
    rating: float  # normal limit [MW]; 0 = unlimited
    future: bool = False


def _circuit_from_line(ln: str) -> SddpCircuit | None:
    """Decode one ``dcirc`` v2 / ``mcirc`` record (fixed columns)."""
    fb = to_int(ln[0:8])
    tb = to_int(ln[8:17])
    code = to_int(ln[68:74])
    if fb is None or tb is None or code is None:
        return None
    return SddpCircuit(
        code=code,
        from_bus=fb,
        to_bus=tb,
        name=ln[30:42].strip(),
        r_pct=to_float(ln[17:23]) or 0.0,
        x_pct=to_float(ln[23:29]) or 0.0,
        rating=to_float(ln[58:64]) or 0.0,
        future=ln[75:76] == "1",
    )


def parse_circuits(path: str | Path) -> list[SddpCircuit]:
    """Parse ``dcirc.dat`` (``$version=2`` layout).

    Columns: ``#BOR`` [0:8], ``#BDE`` [8:17], R% [17:23], X% [23:29],
    name [30:42], normal rating ``(  MW)`` [58:64], code ``(Num)``
    [68:74], existing flag ``I`` [75] (``1`` = future).
    """
    lines = read_lines(path)
    if any(ln.startswith("!Code,") for ln in lines[:3]):
        return [c for _, c in _circuits_from_csv(path)]
    if not any(ln.startswith("$version=") for ln in lines[:3]):
        hdr = next((ln for ln in lines if ln.startswith("#BOR")), None)
        if hdr is not None:
            return _circuits_from_header(hdr, lines)
    out: list[SddpCircuit] = []
    for ln in lines:
        if ln.startswith(("$", "#", "!")) or not ln.strip():
            continue
        c = _circuit_from_line(ln)
        if c is not None:
            out.append(c)
    return out


def _circuits_from_header(header: str, lines: list[str]) -> list[SddpCircuit]:
    """Pre-``$version`` ``dcirc`` layout, columns located from the header.

    ``#BOR. #BDE. .RESIS.REACT Nome… (  MW)(MW) # I C`` — R and X share
    the ``.RESIS.REACT`` header token (blank-separated in this layout).
    """
    spans = header_spans(header.replace("#B", " B"))
    by = {sp.label: sp for sp in spans}
    rx = next((sp for sp in spans if sp.label.startswith("RESIS")), None)
    # ``(  MW)`` may be glued to the neighbouring labels in one token.
    mw_end = header.find("MW)") + 3 if "MW)" in header else -1
    code_sp = spans[spans.index(by["I"]) - 1] if "I" in by else None
    out: list[SddpCircuit] = []
    for ln in lines:
        if ln.startswith(("$", "#", "!")) or not ln.strip():
            continue
        rec = fw_record(ln, spans)
        fb, tb = to_int(rec.get("BOR")), to_int(rec.get("BDE"))
        code = to_int(ln[code_sp.num_start : code_sp.end]) if code_sp else None
        if fb is None or tb is None or code is None:
            continue
        r_pct = x_pct = 0.0
        if rx is not None:
            field_txt = ln[rx.num_start : rx.end]
            toks = field_txt.split()
            if len(toks) >= 2:
                r_pct, x_pct = to_float(toks[0]) or 0.0, to_float(toks[1]) or 0.0
            elif toks:
                x_pct = to_float(toks[0]) or 0.0
        nm = next((sp for sp in spans if sp.label.startswith("Nome")), None)
        out.append(
            SddpCircuit(
                code=code,
                from_bus=fb,
                to_bus=tb,
                name=ln[nm.start : nm.start + NAME_WIDTH].strip() if nm else "",
                r_pct=r_pct,
                x_pct=x_pct,
                rating=(
                    (to_float(ln[mw_end - 6 : mw_end]) or 0.0) if mw_end > 0 else 0.0
                ),
                future=rec.get("I", "0") == "1",
            )
        )
    return out


def _circuits_from_csv(path: str | Path) -> list[tuple[Date, SddpCircuit]]:
    """``dcirc``/``mcirc`` CSV layout (SDDP ≥ 17).

    ``Existing`` 1 = future, ``Decommissioned`` 1 = out of service (both
    reported as ``future``); R/X in % on the 100 MVA base.  ``mcirc`` rows
    carry ``Day,Month,Year``; ``dcirc`` rows get date ``(0, 0, 0)``.
    """
    out: list[tuple[Date, SddpCircuit]] = []
    for r in parse_csv_dat(path):
        code = to_int(r.get("Code"))
        fb = to_int(r.get("BusFromCode", r.get("BusFrom")))
        tb = to_int(r.get("BusToCode", r.get("BusTo")))
        if code is None or fb is None or tb is None:
            continue
        date = (
            to_int(r.get("Year")) or 0,
            to_int(r.get("Month")) or 0,
            to_int(r.get("Day")) or 0,
        )
        out.append(
            (
                date,
                SddpCircuit(
                    code=code,
                    from_bus=fb,
                    to_bus=tb,
                    name=r.get("Name", "").strip(),
                    r_pct=to_float(r.get("Resistance")) or 0.0,
                    x_pct=to_float(r.get("Reactance")) or 0.0,
                    rating=to_float(r.get("Rating")) or 0.0,
                    future=(to_int(r.get("Existing")) or 0) == 1
                    or (to_int(r.get("Decommissioned")) or 0) == 1,
                ),
            )
        )
    return out


def parse_circuit_mods(path: str | Path) -> list[tuple[Date, SddpCircuit]]:
    """Parse ``mcirc.dat`` (``MOD: date`` blocks of ``dcirc`` records, or CSV)."""
    lines = read_lines(path)
    if any(ln.startswith("!Day,") for ln in lines[:3]):
        return _circuits_from_csv(path)
    out: list[tuple[Date, SddpCircuit]] = []
    cur: Date | None = None
    for ln in lines:
        if ln.startswith("MOD:"):
            cur = parse_date(ln[4:])
            continue
        if cur is None or ln.startswith(("$", "#", "!")) or not ln.strip():
            continue
        c = _circuit_from_line(ln)
        if c is not None:
            out.append((cur, c))
    return out


def parse_fuels(path: str | Path) -> dict[int, float]:
    """Parse ``ccombu*.dat`` → ``{fuel_code: cost [$ / fuel unit]}``.

    Accepts the fixed-width layout and the SDDP ≥ 17 CSV one
    (``!Code,Name,FuelUnit,Cost,…``).
    """
    if any(ln.startswith("!Code,") for ln in read_lines(path)[:3]):
        return {
            code: to_float(r.get("Cost")) or 0.0
            for r in parse_csv_dat(path)
            if (code := to_int(r.get("Code"))) is not None
        }
    _, recs = parse_fw_file(path, key_label="Custo")
    out: dict[int, float] = {}
    for r in recs:
        code = to_int(r.get("Num"))
        if code is not None:
            out[code] = to_float(r.get("Custo")) or 0.0
    return out


def find_case_file(case_dir: Path, *names: str) -> Path | None:
    """Return the first existing file among ``names`` (case-insensitive, compressed ok)."""
    from .dat_parsers import find_dat  # pylint: disable=import-outside-toplevel

    lower = {p.name.lower(): p for p in case_dir.iterdir() if p.is_file()}
    for name in names:
        hit = find_dat(case_dir, name)
        if hit is not None:
            return hit
        for ext in ("", ".xz", ".gz", ".zst", ".bz2", ".lz4"):
            p = lower.get((name + ext).lower())
            if p is not None:
                return p
    return None
