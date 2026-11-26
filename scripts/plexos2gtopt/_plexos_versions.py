# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""PLEXOS versions plexos2gtopt reads: detection, resolution and differences.

Every version-specific fact lives here, in one table (:data:`PROFILES`):

* **8.100** (CEN PCP 2021): solution DB is an Access 2000 ``.mdb``; BESS
  modelled with the PLEXOS ``Battery`` class; no ``BESS_IniValue.csv``,
  ``Res_Requirement.csv`` or ``Res_Timeslice.csv`` Data Files.
* **9.000** (CEN PCP 2024): ``.accdb`` solution; BESS emulated with hydro
  objects (storage + discharge / charge generators, :mod:`._hydro_bess`).
* **10.000** (CEN PCP 2025-2026): ``.accdb`` solution; ``Battery`` class
  and the 2025 pumped single-generator hydro BESS; reserve Data Files.

Ids are NOT version-stable (Node ``Price`` is property 1285 in a 8.100
solution, 1412 in 10.000; Storage ``End Volume`` 343 vs 646), so every
class / collection / property is resolved **by name** through the
database's own ``t_class`` / ``t_collection`` / ``t_property`` tables
(:func:`resolve_property_ids`).  The XML reader (:mod:`.plexos_xml`) has
always worked by name; the schema of those tables is identical in all
three versions.

Version declaration and resolution use the shared
:mod:`gtopt_shared.source_versions` contract (:data:`SOURCE_FORMAT`,
:func:`detect_version`, ``--source-version``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gtopt_shared.source_versions import SourceFormat, apply_source_version
from gtopt_shared import safe_xml

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


@dataclass(frozen=True, order=True)
class PlexosVersion:
    """A PLEXOS ``major.minor`` version; ``minor`` keeps PLEXOS's 3 digits."""

    major: int
    minor: int

    @classmethod
    def parse(cls, text: str | None) -> PlexosVersion | None:
        """Parse ``"8.100 R01 Gold"``, ``"PLEXOS Desktop 10.000 R08"``, ``"9.0"``.

        The minor part is right-padded to PLEXOS's three digits, so
        ``"8.1"`` and ``"8.100"`` are the same version.
        """
        if not text:
            return None
        m = _VERSION_RE.search(text)
        if m is None:
            return None
        return cls(int(m.group(1)), int(m.group(2).ljust(3, "0")[:3]))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor:03d}"

    @property
    def ordinal(self) -> int:
        """Monotone integer for distance comparisons."""
        return self.major * 1000 + self.minor


@dataclass(frozen=True)
class VersionProfile:
    """What differs in one PLEXOS version, as plexos2gtopt sees it."""

    version: PlexosVersion
    #: Converted and solved end to end on real bundles.
    tested: bool
    #: Solution database file extension inside ``RES*.zip``.
    solution_ext: str
    #: How the CEN bundles of this version model BESS.
    bess_model: str
    #: Data Files that only exist from this version on.
    data_files: tuple[str, ...]
    #: Bundles it was validated on (help / provenance text).
    tested_on: str = ""
    #: Data Files are change-point series starting before the horizon
    #: (densified onto it before extraction, see
    #: :meth:`.plexos_loader.PlexosBundle.normalize_dated_csvs`).
    change_point_csvs: bool = False


PROFILES: tuple[VersionProfile, ...] = (
    VersionProfile(
        version=PlexosVersion(8, 100),
        tested=True,
        solution_ext=".mdb",
        bess_model="Battery class",
        data_files=(),
        tested_on="CEN PCP 2021 (pcp_2021-10-17, pcp_2021-10-24)",
        change_point_csvs=True,
    ),
    VersionProfile(
        version=PlexosVersion(9, 0),
        tested=True,
        solution_ext=".accdb",
        bess_model="hydro-emulated (storage + generators)",
        data_files=(),
        tested_on="CEN PCP 2024 (pcp_2024-09-01, pcp_2024-12-15, ...)",
    ),
    VersionProfile(
        version=PlexosVersion(10, 0),
        tested=True,
        solution_ext=".accdb",
        bess_model="Battery class + 2025 pumped hydro BESS",
        data_files=("BESS_IniValue.csv", "Res_Requirement.csv", "Res_Timeslice.csv"),
        tested_on="CEN PCP 2025-2026 (20 loss-study / worst-day cases)",
    ),
)


#: Version used when a bundle carries no readable version.
DEFAULT_VERSION = PlexosVersion(10, 0)

#: Solution DB extensions in preference order (newest container first).
SOLUTION_EXTS: tuple[str, ...] = (".accdb", ".mdb")

#: The shared declaration (``gtopt_shared.source_versions``), generated
#: from :data:`PROFILES`: oldest first; tested = exercised on real bundles.
SOURCE_FORMAT = SourceFormat(
    name="PLEXOS",
    supported=tuple(str(p.version) for p in PROFILES),
    tested=tuple(str(p.version) for p in PROFILES if p.tested),
    default=str(DEFAULT_VERSION),
)


def profile_for(version: PlexosVersion) -> VersionProfile | None:
    """The exact profile of ``version``, or ``None``."""
    return next((p for p in PROFILES if p.version == version), None)


@dataclass(frozen=True)
class ResolvedVersion:
    """The version a conversion runs with, and where it came from."""

    profile: VersionProfile
    detected: PlexosVersion | None
    requested: PlexosVersion | None
    source: str  # "override" | "detected" | "closest" | "default"

    @property
    def version(self) -> PlexosVersion:
        """The profile's version (what the converter applies)."""
        return self.profile.version

    def as_dict(self) -> dict[str, str | bool | None]:
        """PLEXOS-specific provenance (next to the shared ``source_format``)."""
        return {
            "plexos_version": str(self.version),
            "source": self.source,
            "solution_ext": self.profile.solution_ext,
            "bess_model": self.profile.bess_model,
            "change_point_csvs": self.profile.change_point_csvs,
        }


def _as_version(value: str | PlexosVersion | None) -> PlexosVersion | None:
    if value is None or isinstance(value, PlexosVersion):
        return value
    parsed = PlexosVersion.parse(value)
    if parsed is None:
        raise ValueError(f"unparseable PLEXOS version {value!r} (expected X.Y)")
    return parsed


def resolve_version(
    detected: PlexosVersion | None,
    override: str | PlexosVersion | None = None,
    *,
    provenance: MutableMapping[str, Any] | None = None,
) -> ResolvedVersion:
    """Pick the profile: ``override`` > ``detected`` > :data:`DEFAULT_VERSION`.

    Delegates to :func:`gtopt_shared.source_versions.apply_source_version`
    (logs "PLEXOS source version: X (origin)", warns on unknown / untested
    versions, records ``provenance["source_format"]``).  The override is
    normalised first, so ``8.1`` means ``8.100``.  An unknown version maps
    to the newest declared one not newer than it, else the oldest.
    """
    requested = _as_version(override)
    if requested is None and detected is None:
        logger.warning(
            "PLEXOS version not found in the bundle (XML t_config 'Version'); "
            "assuming %s.  Pass --source-version X.Y to set it.",
            DEFAULT_VERSION,
        )
    want = requested or detected
    version = apply_source_version(
        SOURCE_FORMAT,
        str(detected) if detected else None,
        str(requested) if requested else None,
        provenance=provenance,
        log=logger,
    )
    if want is None:
        source = "default"
    elif SOURCE_FORMAT.match(str(want)) is None:
        source = "closest"
    else:
        source = "override" if requested is not None else "detected"
    resolved = PlexosVersion.parse(version)
    profile = profile_for(resolved) if resolved else None
    if profile is None:  # declaration and PROFILES are built together
        raise LookupError(f"no PLEXOS profile for declared version {version}")
    return ResolvedVersion(profile, detected, requested, source)


def detect_version(xml_path: Path | str) -> PlexosVersion | None:
    """Read the version from the XML ``t_config`` rows.

    ``Version`` (``"8.100"``) wins; ``Saved With`` (``"PLEXOS Desktop
    10.000 R08"``) is the fallback.  Streams the file and stops at the
    first ``t_data``-sized table after ``t_config``, so a 40 MB bundle
    costs milliseconds.
    """
    config = read_xml_config(xml_path)
    return PlexosVersion.parse(config.get("Version")) or PlexosVersion.parse(
        config.get("Saved With")
    )


def read_xml_config(xml_path: Path | str) -> dict[str, str]:
    """``{element: value}`` of the XML ``t_config`` table."""
    out: dict[str, str] = {}
    seen = False
    for _ev, elem in safe_xml.iterparse(xml_path, events=("end",)):
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "t_config":
            seen = True
            vals = {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in elem}
            if "element" in vals:
                out[vals["element"]] = vals.get("value", "")
        elif seen and tag.startswith("t_") and tag != "t_config":
            break
        if tag.startswith("t_"):
            elem.clear()
    return out


def detect_solution_version(
    config_rows: Iterable[Mapping[str, str]],
) -> PlexosVersion | None:
    """Version from a solution DB ``t_config`` (``"8.100 R01 Gold"``)."""
    for row in config_rows:
        if row.get("element") == "Version":
            return PlexosVersion.parse(row.get("value"))
    return None


def versions_help() -> str:
    """One line per known version, for ``--help`` (generated, never typed)."""
    lines = [
        f"  {str(p.version):<7} {'tested' if p.tested else 'untested':<8} "
        f"solution {p.solution_ext:<6} BESS: {p.bess_model}"
        + (f"\n  {'':<7} {'':<8} validated on {p.tested_on}" if p.tested_on else "")
        for p in PROFILES
    ]
    return (
        f"Supported {SOURCE_FORMAT.name} versions (read from the XML "
        f"t_config; default {SOURCE_FORMAT.default};\nan unknown version "
        "uses the closest one, override with --source-version):\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Name-based ids
# ---------------------------------------------------------------------------


def resolve_property_ids(
    classes: Iterable[Mapping[str, str]],
    collections: Iterable[Mapping[str, str]],
    properties: Iterable[Mapping[str, str]],
    child_class: str,
    property_name: str,
    *,
    parent_class: str = "System",
    collection: str | None = None,
) -> set[str]:
    """Ids of ``property_name`` on ``parent_class -> child_class`` collections.

    Rows are the solution / XML tables as string dicts (``mdb-export`` CSV
    or the XML reader).  ``collection`` narrows to one collection name;
    by default every ``parent_class -> child_class`` collection counts.
    """
    cls_id = {r["name"]: r["class_id"] for r in classes}
    pid, cid = cls_id.get(parent_class), cls_id.get(child_class)
    if pid is None or cid is None:
        return set()
    colls = {
        r["collection_id"]
        for r in collections
        if r.get("parent_class_id") == pid
        and r.get("child_class_id") == cid
        and (collection is None or r.get("name") == collection)
    }
    return {
        r["property_id"]
        for r in properties
        if r.get("collection_id") in colls and r.get("name") == property_name
    }


#: Logical solution properties the readers use -> ``(child class,
#: property name, collection, canonical id)``.  The canonical id is the
#: PLEXOS 10.000 id, which the legacy ``PLEXOS_PROP_*`` constants in
#: :mod:`.compare_with_plexos` hard-code; :func:`canonical_property_map`
#: rewrites other versions' ids onto it.
SOLUTION_PROPERTIES: dict[str, tuple[str, str, str, int]] = {
    "GENERATOR_GENERATION": ("Generator", "Generation", "Generators", 2),
    "GENERATOR_UNITS_GENERATING": ("Generator", "Units Generating", "Generators", 7),
    "GENERATOR_GENCOST": ("Generator", "Generation Cost", "Generators", 119),
    "GENERATOR_STARTUP_COST": (
        "Generator",
        "Start & Shutdown Cost",
        "Generators",
        120,
    ),
    "GENERATOR_SRMC": ("Generator", "SRMC", "Generators", 137),
    "BATTERY_GENERATION": ("Battery", "Generation", "Batteries", 520),
    "BATTERY_LOAD": ("Battery", "Load", "Batteries", 521),
    "BATTERY_CHARGING": ("Battery", "Charging", "Batteries", 523),
    "BATTERY_DISCHARGING": ("Battery", "Discharging", "Batteries", 524),
    "STORAGE_SOC": ("Battery", "SoC", "Batteries", 518),
    "STORAGE_INITIAL_VOLUME": ("Storage", "Initial Volume", "Storages", 645),
    "STORAGE_END_VOLUME": ("Storage", "End Volume", "Storages", 646),
    "STORAGE_SHADOW_PRICE": ("Storage", "Shadow Price", "Storages", 680),
    "REGION_LOAD": ("Region", "Load", "Regions", 966),
    "REGION_GENERATION": ("Region", "Generation", "Regions", 970),
    "REGION_LOSSES": ("Region", "Transmission Losses", "Regions", 997),
    "NODE_LOAD": ("Node", "Load", "Nodes", 1373),
    "NODE_GENERATION": ("Node", "Generation", "Nodes", 1377),
    "NODE_PRICE": ("Node", "Price", "Nodes", 1412),
    "NODE_UNSERVED": ("Node", "Unserved Energy", "Nodes", 1408),
    "LINE_FLOW": ("Line", "Flow", "Lines", 1462),
    "LINE_EXPORT_LIMIT": ("Line", "Export Limit", "Lines", 1465),
    "LINE_IMPORT_LIMIT": ("Line", "Import Limit", "Lines", 1466),
}


def resolve_solution_ids(
    classes: Iterable[Mapping[str, str]],
    collections: Iterable[Mapping[str, str]],
    properties: Iterable[Mapping[str, str]],
) -> dict[str, int]:
    """``{logical key: property_id}`` for every :data:`SOLUTION_PROPERTIES`.

    Keys whose property is absent from the database are left out: a
    solution DB only holds the properties its model reported (the 8.100
    CEN runs report no Node ``Unserved Energy`` nor Battery ``SoC``).
    """
    cl, co, pr = list(classes), list(collections), list(properties)
    out: dict[str, int] = {}
    for key, (child, name, coll, _canon) in SOLUTION_PROPERTIES.items():
        ids = resolve_property_ids(cl, co, pr, child, name, collection=coll)
        if len(ids) == 1:
            out[key] = int(next(iter(ids)))
        elif len(ids) > 1:
            logger.warning(
                "solution property %s is ambiguous: ids %s", key, sorted(ids)
            )
    return out


def canonical_property_map(
    classes: Iterable[Mapping[str, str]],
    collections: Iterable[Mapping[str, str]],
    properties: Iterable[Mapping[str, str]],
) -> dict[str, str]:
    """``{db property_id: canonical id}`` (strings, as ``mdb-export`` gives).

    Maps each resolved :data:`SOLUTION_PROPERTIES` id onto its canonical
    10.000 id, and moves any OTHER property that happens to sit on a
    canonical id out of the way (``"-<id>"``), so a 8.100 property 646
    is never read as Storage ``End Volume``.  Ids not involved are left
    out of the map.  Empty when no resolved id differs from its canonical
    one (a 10.000 database, or one whose metadata tables are missing),
    which keeps the legacy id behaviour.
    """
    resolved = resolve_solution_ids(classes, collections, properties)
    if all(db_id == SOLUTION_PROPERTIES[k][3] for k, db_id in resolved.items()):
        return {}
    out: dict[str, str] = {}
    for key, db_id in resolved.items():
        canon = SOLUTION_PROPERTIES[key][3]
        if db_id != canon:
            out[str(db_id)] = str(canon)
    correct = {
        db_id for key, db_id in resolved.items() if db_id == SOLUTION_PROPERTIES[key][3]
    }
    for _child, _name, _coll, canon in SOLUTION_PROPERTIES.values():
        if canon not in correct and str(canon) not in out:
            out[str(canon)] = f"-{canon}"
    return out


def canonicalize_keys(
    keys: Iterable[Mapping[str, str]], id_map: Mapping[str, str]
) -> list[dict[str, str]]:
    """``t_key`` rows with ``property_id`` rewritten through ``id_map``."""
    out = []
    for k in keys:
        row = dict(k)
        pid = row.get("property_id", "")
        row["property_id"] = id_map.get(pid, pid)
        out.append(row)
    return out


def find_solution_db(names: Iterable[str]) -> str | None:
    """The solution database among archive ``names`` (``.accdb``, else ``.mdb``).

    PLEXOS 9+ writes an Access 2007 ``.accdb``; 8.x an Access 2000
    ``.mdb``.  ``mdb-export`` reads both.
    """
    listed = list(names)
    for ext in SOLUTION_EXTS:
        hit = next((n for n in listed if n.lower().endswith(ext)), None)
        if hit is not None:
            return hit
    return None


def is_solution_db(path: Path | str) -> bool:
    """Whether ``path`` has a PLEXOS solution database extension."""
    return Path(path).suffix.lower() in SOLUTION_EXTS


# ---------------------------------------------------------------------------
# XML layout differences
# ---------------------------------------------------------------------------

#: Collection names that changed between versions: the name the converter
#: asks for (10.000) -> older names tried in order when it is absent.
#: 8.100 calls ``Battery -> Node`` "Node" (10.000: "Nodes").
COLLECTION_ALIASES: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("Battery", "Node", "Nodes"): ("Node", "Nodes*"),
}

#: Name of the ``X -> Constraint`` collection that carries constraint
#: terms in PLEXOS >= 9.
CONSTRAINT_TERMS_COLLECTION = "Constraints"


def mirror_constraint_memberships(db: Any) -> int:
    """Re-key 8.x ``Constraint -> X`` constraint terms as ``X -> Constraint``.

    PLEXOS 8.100 stores a constraint's LHS terms on ``Constraint -> X``
    memberships (collections ``Generators``, ``Lines``, ``Decision
    Variables``, ...: the constraint is the PARENT); PLEXOS 9+ stores
    them on ``X -> Constraint`` (``Constraints``: the element is the
    parent), which is what the extractors read.  For every
    ``Constraint -> X`` collection with memberships this adds the
    10.000-shaped collection (reused when it exists), one mirrored
    membership per term, the coefficient properties (same names) under
    it, and moves the term data rows onto them.  ``Constraint ->
    Constraint`` is left alone.  A 9+ database has no such memberships,
    so this is a no-op there.  Returns the number of memberships
    mirrored.
    """
    # pylint: disable=import-outside-toplevel
    from .plexos_xml import PlexosCollection, PlexosMembership, PlexosProperty

    cons = db.classes_by_name.get("Constraint")
    if cons is None:
        return 0
    by_coll: dict[int, list[Any]] = {}
    for m in db.memberships:
        by_coll.setdefault(m.collection_id, []).append(m)
    legacy = [
        c
        for c in db.collections
        if c.parent_class_id == cons
        and c.child_class_id != cons
        and by_coll.get(c.collection_id)
    ]
    if not legacy:
        return 0
    next_coll = max(c.collection_id for c in db.collections) + 1
    next_mid = max(m.membership_id for m in db.memberships) + 1
    next_pid = max((p.property_id for p in db.properties), default=0) + 1
    mid_map: dict[int, int] = {}
    pid_map: dict[int, int] = {}
    for old in legacy:
        target = next(
            (
                c
                for c in db.collections
                if c.parent_class_id == old.child_class_id
                and c.child_class_id == cons
                and c.name == CONSTRAINT_TERMS_COLLECTION
            ),
            None,
        )
        if target is None:
            target = PlexosCollection(
                next_coll, old.child_class_id, cons, CONSTRAINT_TERMS_COLLECTION
            )
            next_coll += 1
            db.collections.append(target)
        have = {
            p.name: p.property_id
            for p in db.properties
            if p.collection_id == target.collection_id
        }
        for p in [p for p in db.properties if p.collection_id == old.collection_id]:
            if p.name not in have:
                db.properties.append(
                    PlexosProperty(next_pid, target.collection_id, p.name)
                )
                have[p.name] = next_pid
                next_pid += 1
            pid_map[p.property_id] = have[p.name]
        for m in by_coll[old.collection_id]:
            db.memberships.append(
                PlexosMembership(
                    next_mid,
                    target.collection_id,
                    m.child_object_id,
                    m.parent_object_id,
                )
            )
            mid_map[m.membership_id] = next_mid
            next_mid += 1
    for d in db.data_rows:
        new_mid = mid_map.get(d.membership_id)
        if new_mid is not None:
            d.membership_id = new_mid
            d.property_id = pid_map.get(d.property_id, d.property_id)
    logger.debug(
        "PLEXOS 8.x constraint terms: %d Constraint->X memberships "
        "re-keyed as X->Constraint (%d collections)",
        len(mid_map),
        len(legacy),
    )
    return len(mid_map)
