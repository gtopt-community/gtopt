"""``Hydro_AntucoBounds.csv`` is the hourly RHS Data File of the XML rows.

PLEXOS tags the static ``RHS`` of ``ANTUCOmin`` / ``ANTUCOmax`` /
``ELTOROmax`` with the ``Hydro_AntucoBounds`` Data File, so the CSV value
in force at each hour replaces the XML scalar (137 / 142).  The LHS stays
the XML Generation Coefficients — MW, not m³/s.  The RHS is the CSV value
stepped by date and ``PERIOD`` at all 24 hours.

The CSV rows are aligned by DATE to the horizon start — CEN ships the day
BEFORE the horizon as the first row — and intra-day ``PERIOD`` rows step
within their day (2021-10-24: ANTUCOmin 126.51 from PERIOD 1, 145 from
PERIOD 11, 126.51 from PERIOD 15).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from plexos2gtopt.entities import GeneratorSpec, UserConstraintSpec
from plexos2gtopt.parsers import (
    _auto_promote_hydro_min_max_to_commitments,
    _parse_hydro_maxrampday_csv,
    extract_user_constraints,
)
from plexos2gtopt.plexos_csv import read_long_stepped
from plexos2gtopt.plexos_loader import PlexosBundle
from plexos2gtopt.plexos_xml import NS, load_xml

_SERIAL_2021_10_24 = (date(2021, 10, 24) - date(1899, 12, 30)).days


def _member(mid: int, coll: int, parent: int, child: int) -> str:
    return (
        f"  <t_membership><membership_id>{mid}</membership_id>"
        f"<collection_id>{coll}</collection_id>"
        f"<parent_object_id>{parent}</parent_object_id>"
        f"<child_object_id>{child}</child_object_id></t_membership>\n"
    )


def _data(did: int, mid: int, pid: int, value: float) -> str:
    return (
        f"  <t_data><data_id>{did}</data_id><membership_id>{mid}</membership_id>"
        f"<property_id>{pid}</property_id><value>{value}</value></t_data>\n"
    )


def _xml() -> str:
    objs = [
        (1, 1, "SEN"),
        (5, 80, "H1"),
        (10, 2, "ANTUCO_U1"),
        (11, 2, "ANTUCO_U2"),
        (20, 2, "EL_TORO_U1"),
        (21, 2, "EL_TORO_U2"),
        (30, 70, "ANTUCOmin"),
        (31, 70, "ELTOROmax"),
    ]
    body = "".join(
        f"  <t_object><object_id>{o}</object_id><class_id>{c}</class_id>"
        f"<name>{n}</name></t_object>\n"
        for o, c, n in objs
    )
    # System->Constraint: Sense, RHS (the static XML value the Data File
    # overrides).  Generator->Constraint: Generation Coefficient = 1 (MW).
    body += _member(700030, 700, 1, 30) + _member(700031, 700, 1, 31)
    body += _data(1, 700030, 4369, 1) + _data(2, 700030, 4384, 137)
    body += _data(3, 700031, 4369, -1) + _data(4, 700031, 4384, 142)
    did = 10
    for mid, gen, con in (
        (3201, 10, 30),
        (3202, 11, 30),
        (3203, 20, 31),
        (3204, 21, 31),
    ):
        body += _member(mid, 32, gen, con) + _data(did, mid, 393, 1)
        did += 1
    return f"""<?xml version="1.0" standalone="yes"?>
<MasterDataSet xmlns="{NS[1:-1]}">
  <t_class><class_id>1</class_id><name>System</name></t_class>
  <t_class><class_id>2</class_id><name>Generator</name></t_class>
  <t_class><class_id>70</class_id><name>Constraint</name></t_class>
  <t_class><class_id>80</class_id><name>Horizon</name></t_class>
  <t_collection><collection_id>700</collection_id><parent_class_id>1</parent_class_id>
    <child_class_id>70</child_class_id><name>Constraints</name></t_collection>
  <t_collection><collection_id>32</collection_id><parent_class_id>2</parent_class_id>
    <child_class_id>70</child_class_id><name>Constraints</name></t_collection>
  <t_property><property_id>4369</property_id><collection_id>700</collection_id>
    <name>Sense</name></t_property>
  <t_property><property_id>4384</property_id><collection_id>700</collection_id>
    <name>RHS</name></t_property>
  <t_property><property_id>393</property_id><collection_id>32</collection_id>
    <name>Generation Coefficient</name></t_property>
  <t_attribute><attribute_id>9</attribute_id><class_id>80</class_id>
    <name>Date From</name></t_attribute>
  <t_attribute_data><object_id>5</object_id><attribute_id>9</attribute_id>
    <value>{_SERIAL_2021_10_24}</value></t_attribute_data>
{body}</MasterDataSet>
"""


# CEN 2021-10-24 layout: the day before first, intra-day PERIOD rows.
_BOUNDS_CSV = (
    "NAME,YEAR,MONTH,DAY,PERIOD,Value\n"
    "ANTUCOmin,2021,10,23,1,125.7031\n"
    "ELTOROmax,2021,10,23,1,0.0463\n"
    "ANTUCOmin,2021,10,24,1,126.5091\n"
    "ANTUCOmin,2021,10,24,11,145\n"
    "ANTUCOmin,2021,10,24,15,126.5091\n"
    "ELTOROmax,2021,10,24,1,60\n"
    "ANTUCOmin,2021,10,25,1,120\n"
    "ELTOROmax,2021,10,25,1,0.0463\n"
)


def _ucs(tmp_path: Path, *, with_csv: bool = True) -> dict[str, UserConstraintSpec]:
    xml = tmp_path / "DBSEN_PRGDIARIO.xml"
    xml.write_text(_xml())
    if with_csv:
        (tmp_path / "Hydro_AntucoBounds.csv").write_text(_BOUNDS_CSV)
    bundle = PlexosBundle(root=tmp_path, source=tmp_path, n_days=2)
    return {uc.name: uc for uc in extract_user_constraints(load_xml(xml), bundle)}


def test_one_mw_row_per_csv_name(tmp_path: Path) -> None:
    """The XML rows carry the bound in MW; no m³/s ``discharge_*`` copy."""
    ucs = _ucs(tmp_path)
    assert not [n for n in ucs if n.startswith("discharge_")]
    expr = ucs["ANTUCOmin"].expression
    assert 'generator("ANTUCO_U1").generation' in expr
    assert 'generator("ANTUCO_U2").generation' in expr
    assert expr.startswith('1 * generator("ANTUCO_U1").generation + 1 * ')  # MW
    assert "<=" in ucs["ELTOROmax"].expression


def test_rhs_steps_by_date_and_period(tmp_path: Path) -> None:
    """Hour h takes the row in force at (start + h//24, h%24 + 1)."""
    ucs = _ucs(tmp_path)
    prof = ucs["ANTUCOmin"].rhs_profile
    assert len(prof) == 48
    # Day 1 = 2021-10-24 rows; the 10-23 row (125.70) is never used.
    assert prof[:10] == (126.5091,) * 10
    assert prof[10:14] == (145.0,) * 4  # PERIOD 11..14
    assert prof[14:24] == (126.5091,) * 10  # from PERIOD 15
    assert prof[24:] == (120.0,) * 24  # 2021-10-25
    assert ucs["ANTUCOmin"].expression.endswith(">= 126.5091")
    el = ucs["ELTOROmax"].rhs_profile
    assert el[:24] == (60.0,) * 24
    assert el[24:] == (0.0463,) * 24


def test_rows_are_hard_without_penalty_price(tmp_path: Path) -> None:
    """No PLEXOS Penalty Price ⇒ hard (PLEXOS semantics)."""
    ucs = _ucs(tmp_path)
    assert not ucs["ANTUCOmin"].penalty
    assert not ucs["ELTOROmax"].penalty


def test_without_csv_the_static_xml_rhs_stays(tmp_path: Path) -> None:
    ucs = _ucs(tmp_path, with_csv=False)
    assert ucs["ANTUCOmin"].expression.endswith(">= 137")
    assert not ucs["ANTUCOmin"].rhs_profile


def test_read_long_stepped_edges(tmp_path: Path) -> None:
    """Hours before the first row take the first row; bad rows skipped;
    ``start=None`` starts at the first date in the file."""
    path = tmp_path / "x.csv"
    path.write_text(
        "NAME;YEAR;MONTH;DAY;PERIOD;VALUE\n"
        "A;2026;1;2;5;7\n"
        "A;2026;1;3;1;9\n"
        "A;2026;1;x;1;1\n"
        ";2026;1;2;1;3\n"
    )
    out = read_long_stepped(path, start=date(2026, 1, 1), n_hours=72)
    assert out["A"][:28] == [7.0] * 28
    assert out["A"][48:] == [9.0] * 24
    out = read_long_stepped(path, start=None, n_hours=30)
    assert out["A"][:4] == [7.0] * 4 and out["A"][24:] == [9.0] * 6
    empty = tmp_path / "e.csv"
    empty.write_text("NAME,YEAR,MONTH,DAY,PERIOD,VALUE\n")
    assert not read_long_stepped(empty, start=None, n_hours=24)


def test_maxrampday_aligned_to_horizon_start(tmp_path: Path) -> None:
    """RES20250427: day 1 uses the 04-27 row (4.3098), not the 04-26 one."""
    path = tmp_path / "Hydro_MaxRampDay.csv"
    path.write_text(
        "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\n"
        "R,2025,4,26,1,4.4642\n"
        "R,2025,4,27,1,4.3098\n"
        "R,2025,4,28,1,4.2424\n"
        "R,2025,4,x,1,1\n"
    )
    start = date(2025, 4, 27)
    assert _parse_hydro_maxrampday_csv(path, start=start) == {"R": [4.3098, 4.2424]}
    assert _parse_hydro_maxrampday_csv(path, start=start, n_days=3) == {
        "R": [4.3098, 4.2424, 4.2424]
    }
    # Legacy reading (no horizon start): first row = day 0.
    assert _parse_hydro_maxrampday_csv(path)["R"][0] == 4.4642


def test_inactive_bound_is_not_promoted() -> None:
    """A row PLEXOS excludes from the ST schedule must not become a
    Commitment floor."""
    uc = UserConstraintSpec(
        name="PLANTmin",
        expression='generator("P_U1").generation >= 10',
        active=False,
    )
    gen = GeneratorSpec(object_id=0, name="P_U1", bus_name="b", pmax=50.0)
    assert _auto_promote_hydro_min_max_to_commitments((uc,), (), (gen,), ()) == (
        (),
        frozenset(),
    )
