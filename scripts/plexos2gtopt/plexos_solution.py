"""Read hourly results out of a PLEXOS solution ``.accdb`` (the RES bundle).

The converter is input-driven by design, but two features need the
solution PLEXOS produced for the same bundle:

* ``--horizon-days N`` (a CEN PCP 7-day case cut to N days) pins each
  storage's end-of-horizon target to the PLEXOS state at the end of
  day N (:mod:`plexos2gtopt._horizon`);
* ``--reserve-load-factor auto`` estimates the exogenous reserve load
  factor from PLEXOS's ``Generation_SEN`` (:mod:`plexos2gtopt._reserve_load`).

Only the few tables these need are read (``mdb-export``, one table at a
time; ``t_data_0`` is streamed and filtered by key, never held whole).

Solution layout (PLEXOS 9 output schema)::

    t_phase_3   interval_id -> period_id     (ST phase: hour -> block)
    t_key       key_id -> (membership, property, phase, band, sample)
    t_data_0    (key_id, period_id, value)   period = ST block

``value`` at ``period p`` is the block's average (Generation, Load) or its
end state (End Volume, Energy).  Hour ``h`` (1-based interval) maps to
block ``t_phase_3[h]``; the state at the end of day ``d`` is the value of
the block that holds interval ``24 * d`` (PLEXOS blocks never cross a
day boundary).
"""

from __future__ import annotations

import csv
import io
import logging
import shutil
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Reads one table: ``table name -> rows`` (header-keyed string dicts).
TableSource = Callable[[str], Iterable[Mapping[str, str]]]

#: PLEXOS ``System`` class id (parent of every top-level collection).
_SYSTEM_CLASS = "System"
#: Short-term (hourly) phase: ``t_phase_3`` / ``t_key.phase_id == 3``.
ST_PHASE = "3"


def mdb_table_source(accdb: Path) -> TableSource:
    """``TableSource`` that runs ``mdb-export`` on ``accdb`` per table."""

    def _read(table: str) -> Iterator[dict[str, str]]:
        with subprocess.Popen(
            ["mdb-export", str(accdb), table],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as proc:
            assert proc.stdout is not None
            yield from csv.DictReader(proc.stdout)
            rc = proc.wait()
        if rc != 0:
            raise OSError(f"mdb-export {accdb.name} {table} failed (rc={rc})")

    return _read


def csv_table_source(tables: Mapping[str, str]) -> TableSource:
    """``TableSource`` over in-memory CSV text, one entry per table.

    Used by the tests (and handy for a hand-made fixture): ``{"t_key":
    "key_id,membership_id,...\\n1,2,..."}``.  A missing table reads empty.
    """

    def _read(table: str) -> Iterator[dict[str, str]]:
        yield from csv.DictReader(io.StringIO(tables.get(table, "")))

    return _read


@dataclass(frozen=True)
class SeriesKey:
    """One solution series: a PLEXOS object's property."""

    class_name: str
    object_name: str
    property_name: str


@dataclass
class PlexosSolution:
    """Hourly results of one PLEXOS solution, read on demand."""

    source: TableSource
    phase_id: str = ST_PHASE
    _cache: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    _interval_period: dict[int, int] | None = None

    @classmethod
    def from_accdb(cls, accdb: Path) -> PlexosSolution | None:
        """Solution over ``accdb``; ``None`` when ``mdb-export`` is missing."""
        if shutil.which("mdb-export") is None:
            logger.warning(
                "mdb-export (mdbtools) not found: cannot read the PLEXOS solution %s",
                accdb,
            )
            return None
        return cls(mdb_table_source(accdb))

    def _table(self, name: str) -> list[dict[str, str]]:
        if name not in self._cache:
            self._cache[name] = [dict(r) for r in self.source(name)]
        return self._cache[name]

    # ── time axis ────────────────────────────────────────────────────
    @property
    def interval_period(self) -> dict[int, int]:
        """1-based hour interval -> ST block (period) id."""
        if self._interval_period is None:
            self._interval_period = {
                int(r["interval_id"]): int(r["period_id"])
                for r in self._table(f"t_phase_{self.phase_id}")
            }
        return self._interval_period

    @property
    def n_intervals(self) -> int:
        """Number of hourly intervals the solution covers."""
        return max(self.interval_period, default=0)

    # ── series ───────────────────────────────────────────────────────
    def fetch(
        self, wanted: Iterable[tuple[str, str]]
    ) -> dict[SeriesKey, dict[int, float]]:
        """Read every object's ``(class, property)`` series in one pass.

        ``wanted`` lists ``(class_name, property_name)`` pairs of the
        System collections (``("Storage", "End Volume")``, ``("Battery",
        "Energy")``, ``("Decision Variable", "Value")`` …).  Returns
        ``{SeriesKey: {period: value}}`` for the band-1, sample-0 keys of
        the phase.  Pairs absent from the solution are simply missing.
        """
        wanted = tuple(wanted)
        classes = {r["name"]: r["class_id"] for r in self._table("t_class")}
        system_id = classes.get(_SYSTEM_CLASS)
        cls_names = {cid: name for name, cid in classes.items()}
        # System→<class> collections of the wanted classes.
        coll_class: dict[str, str] = {}
        for r in self._table("t_collection"):
            if r["parent_class_id"] != system_id:
                continue
            cname = cls_names.get(r["child_class_id"])
            if cname is not None and any(cname == c for c, _p in wanted):
                coll_class[r["collection_id"]] = cname
        # Property ids of the wanted (class, property) pairs.
        prop_of: dict[str, tuple[str, str]] = {}
        for r in self._table("t_property"):
            cname = coll_class.get(r["collection_id"])
            if cname is not None and (cname, r["name"]) in wanted:
                prop_of[r["property_id"]] = (cname, r["name"])
        if not prop_of:
            return {}
        objects = {r["object_id"]: r["name"] for r in self._table("t_object")}
        member_obj: dict[str, str] = {
            r["membership_id"]: objects.get(r["child_object_id"], "")
            for r in self._table("t_membership")
            if r["collection_id"] in coll_class
        }
        key_series: dict[str, SeriesKey] = {}
        for r in self._table("t_key"):
            prop = prop_of.get(r["property_id"])
            obj = member_obj.get(r["membership_id"])
            if (
                prop is None
                or not obj
                or r.get("phase_id", self.phase_id) != self.phase_id
                or r.get("sample_id", "0") != "0"
                or r.get("band_id", "1") not in ("1", "0")
            ):
                continue
            key_series[r["key_id"]] = SeriesKey(prop[0], obj, prop[1])
        out: dict[SeriesKey, dict[int, float]] = {}
        # Stream t_data_0 (millions of rows): never cached.
        for row in self.source("t_data_0"):
            sk = key_series.get(row["key_id"])
            if sk is None:
                continue
            try:
                out.setdefault(sk, {})[int(row["period_id"])] = float(row["value"])
            except (KeyError, ValueError):
                continue
        return out

    def value_at_hour(self, series: Mapping[int, float], hour: int) -> float | None:
        """Value of the block holding 1-based ``hour`` (``None`` if absent)."""
        period = self.interval_period.get(hour)
        return None if period is None else series.get(period)

    def end_of_day(self, series: Mapping[int, float], day: int) -> float | None:
        """State at the end of (1-based) ``day``: the block of hour ``24·day``."""
        return self.value_at_hour(series, 24 * day)

    def hourly_sum(self, series: Mapping[int, float], n_hours: int) -> float:
        """Σ over hours ``1..n_hours`` of the block value (MW → MWh)."""
        total = 0.0
        for hour in range(1, n_hours + 1):
            v = self.value_at_hour(series, hour)
            if v is not None:
                total += v
        return total


def open_solution(
    *,
    accdb: Path | None = None,
    input_bundle: Path | None = None,
) -> PlexosSolution | None:
    """Open the PLEXOS solution of a run, or ``None`` when there is none.

    ``accdb`` (``--plexos-solution-accdb``) wins; otherwise the CEN
    ``RES<date>.zip[.xz]`` next to ``input_bundle`` is auto-discovered
    and its nested ``.accdb`` extracted.
    """
    # pylint: disable=import-outside-toplevel
    from ._plexos_versions import is_solution_db
    from .plexos_block_layout import auto_discover_res_zip, extract_accdb_from_res_zip

    path: Path | None = None
    if accdb is not None and is_solution_db(accdb):
        path = Path(accdb)
    elif input_bundle is not None:
        res_zip = auto_discover_res_zip(Path(input_bundle))
        if res_zip is not None:
            logger.info("PLEXOS solution bundle found: %s", res_zip.name)
            path = extract_accdb_from_res_zip(res_zip)
    if path is None or not path.is_file():
        return None
    return PlexosSolution.from_accdb(path)


__all__ = [
    "PlexosSolution",
    "SeriesKey",
    "TableSource",
    "csv_table_source",
    "mdb_table_source",
    "open_solution",
]
