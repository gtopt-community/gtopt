# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Build a PLEXOS ``MasterDataSet`` XML (the input object database).

Only the tables :mod:`plexos2gtopt.plexos_xml` reads are written:
``t_class``, ``t_object``, ``t_collection``, ``t_membership``,
``t_property`` and ``t_data``.  Every class gets its ``System -> <Class>``
collection, which is where PLEXOS keeps an object's static properties
(``Max Capacity``, ``Reactance``, ...).  Ids are assigned in insertion
order, so the same planning always gives the same file.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

NS_URI = "http://tempuri.org/MasterDataSet.xsd"

#: ``System -> <Class>`` collection names (PLEXOS plural spelling).
SYSTEM_COLLECTION = {
    "Battery": "Batteries",
    "Storage": "Storages",
    "Generator": "Generators",
    "Node": "Nodes",
    "Line": "Lines",
    "Region": "Regions",
    "Waterway": "Waterways",
}


def _plural(cls: str) -> str:
    return SYSTEM_COLLECTION.get(cls, cls + "s")


def num_text(value: float) -> str:
    """Shortest text that round-trips ``float(value)``."""
    v = float(value)
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


@dataclass
class MasterDataSet:
    """In-memory PLEXOS object database, written by :meth:`write`."""

    classes: dict[str, int] = field(default_factory=dict)
    objects: list[tuple[int, int, str]] = field(default_factory=list)
    collections: dict[tuple[str, str, str], int] = field(default_factory=dict)
    memberships: list[tuple[int, int, int, int]] = field(default_factory=list)
    properties: dict[tuple[int, str], int] = field(default_factory=dict)
    data: list[tuple[int, int, int, float]] = field(default_factory=list)
    _object_ids: dict[tuple[str, str], int] = field(default_factory=dict)
    _sys_members: dict[tuple[str, str], int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Create the ``System`` object every collection hangs from."""
        self.add_object("System", "System")

    def class_id(self, name: str) -> int:
        """Id of class ``name`` (registered on first use)."""
        if name not in self.classes:
            self.classes[name] = len(self.classes) + 1
        return self.classes[name]

    def collection_id(self, parent: str, child: str, name: str) -> int:
        """Id of collection ``parent -> child`` called ``name``."""
        key = (parent, child, name)
        if key not in self.collections:
            self.class_id(parent)
            self.class_id(child)
            self.collections[key] = len(self.collections) + 1
        return self.collections[key]

    def objects_of(self, cls: str) -> list[str]:
        """Names of the objects of class ``cls``, in insertion order."""
        return [n for (c, n) in self._object_ids if c == cls]

    def object_id(self, cls: str, name: str) -> int:
        """Id of an existing object."""
        return self._object_ids[(cls, name)]

    def add_object(self, cls: str, name: str) -> int:
        """Add an object (and its System membership); returns its id."""
        if (cls, name) in self._object_ids:
            raise ValueError(f"duplicate PLEXOS {cls} object {name!r}")
        oid = len(self.objects) + 1
        self.objects.append((oid, self.class_id(cls), name))
        self._object_ids[(cls, name)] = oid
        if cls != "System":
            self._sys_members[(cls, name)] = self.add_membership(
                "System", "System", cls, name, _plural(cls)
            )
        return oid

    def add_membership(
        self, parent_cls: str, parent: str, child_cls: str, child: str, coll: str
    ) -> int:
        """Add the edge ``parent -> child`` in collection ``coll``."""
        cid = self.collection_id(parent_cls, child_cls, coll)
        mid = len(self.memberships) + 1
        self.memberships.append(
            (
                mid,
                cid,
                self.object_id(parent_cls, parent),
                self.object_id(child_cls, child),
            )
        )
        return mid

    def set_property(self, cls: str, name: str, prop: str, value: float) -> None:
        """Set a static (System collection) property of an object."""
        cid = self.collection_id("System", cls, _plural(cls))
        pid = self.properties.setdefault((cid, prop), len(self.properties) + 1)
        mid = self._sys_members[(cls, name)]
        self.data.append((len(self.data) + 1, mid, pid, float(value)))

    def write(self, path: Path) -> Path:
        """Write the XML file."""
        ET.register_namespace("", NS_URI)
        root = ET.Element(f"{{{NS_URI}}}MasterDataSet")

        def row(table: str, **cols: object) -> None:
            elem = ET.SubElement(root, f"{{{NS_URI}}}{table}")
            for key, val in cols.items():
                sub = ET.SubElement(elem, f"{{{NS_URI}}}{key}")
                sub.text = num_text(val) if isinstance(val, float) else str(val)

        for name, cid in self.classes.items():
            row("t_class", class_id=cid, name=name)
        for oid, cid, name in self.objects:
            row("t_object", object_id=oid, class_id=cid, name=name)
        for (parent, child, name), cid in self.collections.items():
            row(
                "t_collection",
                collection_id=cid,
                parent_class_id=self.classes[parent],
                child_class_id=self.classes[child],
                name=name,
            )
        for mid, cid, po, co in self.memberships:
            row(
                "t_membership",
                membership_id=mid,
                collection_id=cid,
                parent_object_id=po,
                child_object_id=co,
            )
        for (cid, name), pid in self.properties.items():
            row("t_property", property_id=pid, collection_id=cid, name=name)
        for did, mid, pid, value in self.data:
            row("t_data", data_id=did, membership_id=mid, property_id=pid, value=value)
        ET.indent(root, space="  ")
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        return path
