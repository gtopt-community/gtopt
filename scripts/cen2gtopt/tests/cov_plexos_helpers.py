# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Synthetic PLEXOS XML / PCP bundle builders shared by the cov tests."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from pathlib import Path

NS_URI = "http://tempuri.org/MasterDataSet.xsd"


def _row(tag: str, **fields: object) -> str:
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return f"<{tag}>{inner}</{tag}>"


def plexos_xml(
    *,
    objects: list[tuple[object, object, object]],
    memberships: Sequence[dict[str, object]] = (),
    collections: Sequence[dict[str, object]] = (),
    properties: Sequence[dict[str, object]] = (),
    data: Sequence[dict[str, object]] = (),
    raw_rows: tuple[str, ...] = (),
) -> str:
    """Return a MasterDataSet XML string.

    ``objects`` are ``(object_id, name, class_id)`` triples; use ``None``
    to omit a field.  The other lists are dicts of child tags.
    """
    parts: list[str] = []
    for oid, name, cid in objects:
        fields = {
            k: v
            for k, v in (("object_id", oid), ("name", name), ("class_id", cid))
            if v is not None
        }
        parts.append(_row("t_object", **fields))
    parts += [_row("t_membership", **m) for m in memberships]
    parts += [_row("t_collection", **c) for c in collections]
    parts += [_row("t_property", **p) for p in properties]
    parts += [_row("t_data", **d) for d in data]
    parts += list(raw_rows)
    return f'<MasterDataSet xmlns="{NS_URI}">{"".join(parts)}</MasterDataSet>'


#: Generator → Fuel / Node fixture: 3 generators, 2 fuels, 1 node.
DEFAULT_XML = plexos_xml(
    objects=[
        (1, "ANGAMOS_1", 2),
        (2, "TG_Diesel_X", 2),
        (3, "HYDRO_RIO", 2),
        (10, "Carbon_Ang", 4),
        (11, "GasExtra", 4),
        (20, "Crucero220", 22),
    ],
    memberships=[
        {
            "membership_id": 100,
            "collection_id": 7,
            "parent_object_id": 1,
            "child_object_id": 10,
        },
        {
            "membership_id": 101,
            "collection_id": 7,
            "parent_object_id": 1,
            "child_object_id": 11,
        },
        {
            "membership_id": 102,
            "collection_id": 12,
            "parent_object_id": 1,
            "child_object_id": 20,
        },
    ],
    collections=[{"collection_id": 12, "parent_class_id": 2, "child_class_id": 22}],
)


def zip_bytes(members: dict[str, bytes]) -> bytes:
    """In-memory zip with ``{arcname: payload}``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def write_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zip_bytes(members))
    return path
