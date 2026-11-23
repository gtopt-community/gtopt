# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""In-memory :class:`PlexosDb` builder shared by the ``test_cov_parsers*`` tests.

Builds the PLEXOS MasterDataSet tables directly as Python objects (no XML
round-trip) so each test can declare only the classes / memberships /
properties the branch under test needs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from plexos2gtopt.plexos_loader import PlexosBundle
from plexos2gtopt.plexos_xml import (
    PlexosCollection,
    PlexosDataRow,
    PlexosDb,
    PlexosMembership,
    PlexosObject,
    PlexosProperty,
)


class DbKit:
    """Fluent builder of a synthetic :class:`PlexosDb`."""

    def __init__(self, *, system: bool = True) -> None:
        self.db = PlexosDb(path=Path("synthetic.xml"))
        self._colls: dict[tuple[str, str, str], int] = {}
        self._props: dict[tuple[int, str], int] = {}
        self._next = {"obj": 1, "coll": 1, "prop": 1, "mem": 1, "data": 1}
        self.system_id: int | None = None
        if system:
            self.system_id = self.obj("System", "SYS")

    # ----- tables ---------------------------------------------------------
    def cls(self, name: str) -> int:
        """Return (registering if new) the class id of ``name``."""
        cid = self.db.classes_by_name.get(name)
        if cid is None:
            cid = len(self.db.classes_by_name) + 1
            self.db.classes_by_name[name] = cid
            self.db.classes_by_id[cid] = name
        return cid

    def _id(self, key: str) -> int:
        val = self._next[key]
        self._next[key] += 1
        return val

    def obj(
        self,
        cls: str,
        name: str,
        *,
        category: str | None = None,
        sys_member: bool = True,
    ) -> int:
        """Add an object; non-System objects join ``System -> <cls>``."""
        oid = self._id("obj")
        cat_id = None
        if category is not None:
            cat_id = len(self.db.categories_by_id) + 1
            self.db.categories_by_id[cat_id] = category
        self.db.objects.append(
            PlexosObject(
                object_id=oid, class_id=self.cls(cls), name=name, category_id=cat_id
            )
        )
        if cls != "System" and sys_member and self.system_id is not None:
            self.mem("System", cls, self.system_id, oid, coll_name=_plural(cls))
        return oid

    def coll(self, parent: str, child: str, name: str | None = None) -> int:
        """Return (registering if new) a collection id."""
        cname = name if name is not None else _plural(child)
        key = (parent, child, cname)
        if key not in self._colls:
            cid = self._id("coll")
            self._colls[key] = cid
            self.db.collections.append(
                PlexosCollection(
                    collection_id=cid,
                    parent_class_id=self.cls(parent),
                    child_class_id=self.cls(child),
                    name=cname,
                )
            )
        return self._colls[key]

    def mem(
        self,
        parent: str,
        child: str,
        parent_oid: int,
        child_oid: int,
        *,
        coll_name: str | None = None,
    ) -> int:
        """Add a membership; return its id."""
        mid = self._id("mem")
        self.db.memberships.append(
            PlexosMembership(
                membership_id=mid,
                collection_id=self.coll(parent, child, coll_name),
                parent_object_id=parent_oid,
                child_object_id=child_oid,
            )
        )
        return mid

    def prop(self, coll_id: int, name: str) -> int:
        """Return (registering if new) a property id on ``coll_id``."""
        key = (coll_id, name)
        if key not in self._props:
            pid = self._id("prop")
            self._props[key] = pid
            self.db.properties.append(
                PlexosProperty(property_id=pid, collection_id=coll_id, name=name)
            )
        return self._props[key]

    def data(  # pylint: disable=too-many-arguments
        self,
        mid: int,
        pid: int,
        value: float,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        timeslice: str | None = None,
        band: int = 1,
        tags: list[int] | None = None,
        text: str | None = None,
    ) -> int:
        """Add a ``t_data`` row; return its id."""
        did = self._id("data")
        self.db.data_rows.append(
            PlexosDataRow(
                data_id=did,
                membership_id=mid,
                property_id=pid,
                value=value,
                date_from=date_from,
                date_to=date_to,
                timeslice=timeslice,
                band=band,
            )
        )
        if tags:
            self.db.tag_for_data[did] = list(tags)
        if band != 1:
            self.db.band_for_data[did] = band
        if text is not None:
            self.db.text_for_data[did] = text
        return did

    # ----- conveniences ----------------------------------------------------
    def sys_mid(self, cls: str, oid: int) -> int:
        """System membership id of ``oid``."""
        cid = self._colls[("System", cls, _plural(cls))]
        for m in self.db.memberships:
            if m.collection_id == cid and m.child_object_id == oid:
                return m.membership_id
        raise KeyError(oid)

    def sysprop(self, cls: str, oid: int, name: str, value: float, **kw: object) -> int:
        """Set a System-collection property value on ``oid``."""
        coll = self._colls[("System", cls, _plural(cls))]
        return self.data(self.sys_mid(cls, oid), self.prop(coll, name), value, **kw)  # type: ignore[arg-type]

    def rel(  # pylint: disable=too-many-arguments
        self,
        parent: str,
        parent_oid: int,
        child: str,
        child_oid: int,
        coll_name: str | None = None,
        props: dict[str, float] | None = None,
    ) -> int:
        """Membership + optional property values on it."""
        mid = self.mem(parent, child, parent_oid, child_oid, coll_name=coll_name)
        cid = self.coll(parent, child, coll_name)
        for pname, val in (props or {}).items():
            self.data(mid, self.prop(cid, pname), val)
        return mid

    def reset_indexes(self) -> PlexosDb:
        """Drop the lazily-built indexes (after late mutations)."""
        self.db._data_index = None  # pylint: disable=protected-access
        self.db._sys_mem_index = None  # pylint: disable=protected-access
        self.db._prop_index = None  # pylint: disable=protected-access
        return self.db


def _plural(cls: str) -> str:
    special = {
        "Battery": "Batteries",
        "Constraint": "Constraints",
        "Decision Variable": "Decision Variables",
        "Reserve": "Reserves",
        "Storage": "Storages",
    }
    return special.get(cls, cls + "s")


def bundle(
    tmp_path: Path, csvs: dict[str, str] | None = None, **kw: object
) -> PlexosBundle:
    """A directory :class:`PlexosBundle` with the given CSV files."""
    for name, body in (csvs or {}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return PlexosBundle(root=tmp_path, source=tmp_path, **kw)  # type: ignore[arg-type]
