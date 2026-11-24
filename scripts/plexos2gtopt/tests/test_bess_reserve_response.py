# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""BESS response caps, energy usage and dated Capacity (``--bess-reserve native``).

Covers the PLEXOS ``Reserve → Battery`` ``Max Response`` / ``Max Charge
Response`` mapping (scalar, ``CFdata`` data file, ``1e30`` sentinel), the
``<bat>_CF_LOAD_COMP`` / ``_CF_GEN_COMP`` energy-usage coefficients and
the dated battery ``Capacity`` → ``emax``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from plexos2gtopt._bess_reserve import (
    _bundle_file,
    _dated_capacity,
    _with_energy_usage,
    _PairIndex,
    _response_series,
    _with_response_caps,
    apply_native_bess_reserve,
    read_value_csv,
)
from plexos2gtopt.entities import (
    BatterySpec,
    BundleSpec,
    PlexosCase,
    ReserveProvisionSpec,
)
from plexos2gtopt.plexos_xml import PlexosDataRow, PlexosMembership
from plexos2gtopt.tests.cov_parsers_kit import DbKit

_DAY1 = datetime(2026, 11, 7)
_DAY2 = datetime(2026, 11, 8)
_MRU = "CFdata\\CSF\\CSF_B_MRU.csv"


def _kit() -> tuple[DbKit, int]:
    k = DbKit()
    k.db.horizon_start = _DAY1
    bat = k.obj("Battery", "B")
    k.sysprop("Battery", bat, "Capacity", 200.0)
    return k, bat


def _response(
    k: DbKit, bat: int, reserve: str, resp: float, chg: float, text: str | None
) -> None:
    rid = k.obj("Reserve", reserve)
    mid = k.rel("Reserve", rid, "Battery", bat, coll_name="Batteries")
    coll = k.coll("Reserve", "Battery", "Batteries")
    k.data(mid, k.prop(coll, "Max Response"), resp, text=text)
    k.data(mid, k.prop(coll, "Max Charge Response"), chg, text=text)


def _battery(**kw: object) -> BatterySpec:
    base = BatterySpec(
        object_id=1,
        name="B",
        bus_name="n1",
        emax=200.0,
        pmax_discharge=50.0,
        pmax_charge=40.0,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def _write_mru(root: Path, values: list[float]) -> None:
    folder = root / "CFdata" / "CSF"
    folder.mkdir(parents=True)
    lines = ["YEAR,MONTH,DAY,PERIOD,BAND,VALUE", ""]
    lines += [f"2026,11,7,{i + 1},1,{v}" for i, v in enumerate(values)]
    (folder / "CSF_B_MRU.csv").write_text("\n".join(lines), encoding="utf-8")


# ── _PairIndex / read_value_csv ────────────────────────────────────────────
def test_pair_index_skips_orphan_rows() -> None:
    k, bat = _kit()
    _response(k, bat, "R", 10.0, 1e30, None)
    k.db.memberships.append(
        PlexosMembership(
            membership_id=500, collection_id=1, parent_object_id=1, child_object_id=999
        )
    )
    for did, mid in ((900, 500), (901, 777)):  # missing child / membership
        k.db.data_rows.append(
            PlexosDataRow(data_id=did, membership_id=mid, property_id=1, value=1.0)
        )
    idx = _PairIndex(k.db)
    assert [r.value for r in idx.get("R", "B", "Max Response")] == [10.0]
    assert idx.get("R", "B", "nope") == []
    assert [p for p, _ in idx.by_child("B", "Capacity")] == ["SYS"]


def test_read_value_csv(tmp_path: Path) -> None:
    assert not read_value_csv(tmp_path / "missing.csv", 24)
    _write_mru(tmp_path, [1.0, 2.0, 3.0])
    path = tmp_path / "CFdata" / "CSF" / "CSF_B_MRU.csv"
    assert read_value_csv(path, 2) == (1.0, 2.0)
    # A file shorter than the horizon is not used (no short series).
    assert not read_value_csv(path, 24)
    assert read_value_csv(path, 3, _DAY1) == (1.0, 2.0, 3.0)
    assert not read_value_csv(path, 4, _DAY1)


def test_read_value_csv_aligns_by_date(tmp_path: Path) -> None:
    """Rows are matched by date / period (band 1), not by file position."""
    path = tmp_path / "v.csv"
    lines = ["\ufeffYEAR,MONTH,DAY,PERIOD,BAND,VALUE"]
    lines += [f"2026,11,6,{h},1,{100 + h}" for h in range(1, 25)]  # day before
    lines += [f"2026,11,7,{h},2,999" for h in range(1, 25)]  # band 2
    lines += [f"2026,11,7,{h},1,{h}" for h in range(1, 25)]
    lines += ["2026,x,7,1,1,5", "2026,11,7,1,1,"]  # malformed rows skipped
    path.write_text("\n".join(lines), encoding="utf-8")
    assert read_value_csv(path, 24, _DAY1) == tuple(float(h) for h in range(1, 25))
    # Header only.
    head = tmp_path / "h.csv"
    head.write_text("YEAR,MONTH,DAY,PERIOD,VALUE\n", encoding="utf-8")
    assert not read_value_csv(head, 1, _DAY1)
    assert not read_value_csv(head, 1)


def test_bundle_file_rejects_paths_outside_the_bundle(tmp_path: Path) -> None:
    assert (
        _bundle_file(tmp_path, "CFdata\\CSF\\x.csv")
        == tmp_path / "CFdata" / "CSF" / "x.csv"
    )
    for bad in ("..\\..\\etc\\x.csv", "C:\\data\\x.csv", "\\abs\\x.csv", "a/../../x"):
        assert _bundle_file(tmp_path, bad) is None, bad


# ── _response_series ───────────────────────────────────────────────────────
def test_response_series_paths(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    k, bat = _kit()
    _response(k, bat, "FILE", 1e30, 1e30, _MRU)
    _response(k, bat, "SCALAR", 20.0, 1e30, None)
    idx = _PairIndex(k.db)
    # No rows: no cap.
    assert not _response_series(idx, tmp_path, ("NONE", "B", "Max Response"), 3)
    # Scalar and the 1e30 "no limit" sentinel.
    assert _response_series(idx, None, ("SCALAR", "B", "Max Response"), 3) == (
        20.0,
        20.0,
        20.0,
    )
    key = ("SCALAR", "B", "Max Charge Response")
    assert not _response_series(idx, None, key, 3)
    # Data file: read relative to the bundle root (Windows separators).
    _write_mru(tmp_path, [5.0, 6.0, 7.0, 8.0])
    file_key = ("FILE", "B", "Max Response")
    assert _response_series(idx, tmp_path, file_key, 3) == (5.0, 6.0, 7.0)
    # Unreadable data file (no root / missing): the scalar (here no limit).
    with caplog.at_level(logging.WARNING):
        assert not _response_series(idx, None, file_key, 3)
        assert not _response_series(idx, tmp_path / "x", file_key, 3)
    assert "not readable" in caplog.text


def test_response_series_dated_rows_and_escape(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    k, bat = _kit()
    rid = k.obj("Reserve", "R")
    mid = k.rel("Reserve", rid, "Battery", bat, coll_name="Batteries")
    pid = k.prop(k.coll("Reserve", "Battery", "Batteries"), "Max Response")
    k.data(mid, pid, 20.0)
    k.data(mid, pid, 30.0, date_from=datetime(2026, 11, 1))  # in force
    k.data(mid, pid, 40.0, date_from=datetime(2026, 12, 1))  # later
    pid_c = k.prop(k.coll("Reserve", "Battery", "Batteries"), "Max Charge Response")
    k.data(mid, pid_c, 15.0)
    k.data(mid, pid_c, 1e30, date_from=datetime(2026, 11, 1))  # dated "no limit"
    idx = _PairIndex(k.db)
    assert _response_series(idx, tmp_path, ("R", "B", "Max Response"), 3) == (30.0,) * 3
    assert not _response_series(idx, tmp_path, ("R", "B", "Max Charge Response"), 3)
    # A data file outside the bundle is never read: the scalar applies.
    k, bat = _kit()  # fresh DB (the row index is cached per DB)
    _response(k, bat, "ESC", 12.0, 1e30, "..\\outside.csv")
    (tmp_path.parent / "outside.csv").write_text("VALUE\n1\n1\n1\n", encoding="utf-8")
    idx = _PairIndex(k.db)
    with caplog.at_level(logging.WARNING):
        out = _response_series(idx, tmp_path, ("ESC", "B", "Max Response"), 3)
    assert out == (12.0,) * 3
    assert "outside the bundle" in caplog.text


def test_response_series_sentinel_hours(tmp_path: Path) -> None:
    """CSV ``1e30`` hours are "no cap": the physical range, or no series."""
    k, bat = _kit()
    _response(k, bat, "FILE", 1e30, 1e30, _MRU)
    _write_mru(tmp_path, [5.0, 1e30, 7.0])
    idx = _PairIndex(k.db)
    key = ("FILE", "B", "Max Response")
    assert _response_series(idx, tmp_path, key, 3, no_cap=90.0) == (5.0, 90.0, 7.0)
    assert not _response_series(idx, tmp_path, key, 3)
    _write_mru_all = tmp_path / "all"
    _write_mru(_write_mru_all, [1e30, 1e30, 1e30])
    assert not _response_series(idx, _write_mru_all, key, 3, no_cap=90.0)


# ── _with_response_caps ────────────────────────────────────────────────────
def test_with_response_caps_directions() -> None:
    k, bat = _kit()
    _response(k, bat, "UPR", 20.0, 15.0, None)
    _response(k, bat, "DNR", 1e30, 12.0, None)
    _response(k, bat, "FREE", 1e30, 1e30, None)
    idx = _PairIndex(k.db)
    b = _battery()
    up = ReserveProvisionSpec(
        generator_name="B_gen", name="provision_B_gen__UPR", urmax=50.0
    )
    out = _with_response_caps(up, b, idx, None, 2)
    assert out.urmax == pytest.approx(90.0)
    assert out.urmax_response == (20.0, 20.0)
    assert out.urmax_charge_response == (15.0, 15.0)
    assert not out.drmax_response

    dn = ReserveProvisionSpec(
        generator_name="B_gen",
        name="provision_B_gen__DNR",
        drmax_profile=(40.0, 40.0),
    )
    out = _with_response_caps(dn, b, idx, None, 2)
    assert out.drmax == pytest.approx(90.0)
    assert out.drmax_profile == ()
    assert out.drmax_response == ()
    assert out.drmax_charge_response == (12.0, 12.0)

    free = replace(up, name="provision_B_gen__FREE")
    assert _with_response_caps(free, b, idx, None, 2) is free
    plain = replace(up, name="provision_B_gen")
    assert _with_response_caps(plain, b, idx, None, 2) is plain


# ── _with_energy_usage ─────────────────────────────────────────────────────
def test_energy_usage_per_provision() -> None:
    k, _bat = _kit()
    for suffix, coefs in (
        ("_CF_LOAD_COMP", {"CSF": 0.3, "CPF": 0.0}),
        ("_CF_GEN_COMP", {"CTF": 0.0}),
    ):
        cid = k.obj("Constraint", "B" + suffix)
        for reserve, coef in coefs.items():
            rid = k.obj("Reserve", reserve + suffix)
            k.rel(
                "Reserve", rid, "Constraint", cid, props={"Provision Coefficient": coef}
            )
    idx = _PairIndex(k.db)

    def prov(reserve: str, **kw: object) -> ReserveProvisionSpec:
        spec = ReserveProvisionSpec(
            generator_name="B_gen", name=f"provision_B_gen__{reserve}"
        )
        return replace(spec, **kw)  # type: ignore[arg-type]

    # Raise reserve on CF_LOAD_COMP: its own coefficient.
    csf = _with_energy_usage(prov("CSF_CF_LOAD_COMP", urmax=10.0), "B", idx)
    assert csf.ur_energy_usage == 0.3
    assert csf.dr_energy_usage is None
    # Zero coefficient / no row / no reserve suffix: unchanged.
    cpf = prov("CPF_CF_LOAD_COMP", urmax_profile=(5.0,))
    assert _with_energy_usage(cpf, "B", idx) is cpf
    ctf = prov("CTF_CF_GEN_COMP", drmax=10.0)
    assert _with_energy_usage(ctf, "B", idx) is ctf
    other = prov("NOPE", drmax=10.0)
    assert _with_energy_usage(other, "B", idx) is other
    plain = replace(other, name="provision_B_gen")
    assert _with_energy_usage(plain, "B", idx) is plain


def test_energy_usage_dated_coefficient() -> None:
    """A dated ``Provision Coefficient`` in force at the horizon start wins."""
    k, _bat = _kit()
    cid = k.obj("Constraint", "B_CF_LOAD_COMP")
    rid = k.obj("Reserve", "CSF_RS")
    mid = k.rel("Reserve", rid, "Constraint", cid, props={"Provision Coefficient": 0.3})
    pid = k.prop(k.coll("Reserve", "Constraint"), "Provision Coefficient")
    k.data(mid, pid, 0.25, date_from=datetime(2026, 10, 1))
    rp = ReserveProvisionSpec(
        generator_name="B_gen", name="provision_B_gen__CSF_RS", urmax=10.0
    )
    assert _with_energy_usage(rp, "B", _PairIndex(k.db)).ur_energy_usage == 0.25
    # Without a horizon date: the undated row.
    k.db.horizon_start = None
    assert _with_energy_usage(rp, "B", _PairIndex(k.db)).ur_energy_usage == 0.3


def test_energy_usage_lower_reserve() -> None:
    k, _bat = _kit()
    cid = k.obj("Constraint", "B_CF_GEN_COMP")
    rid = k.obj("Reserve", "CSF_LW")
    k.rel("Reserve", rid, "Constraint", cid, props={"Provision Coefficient": 0.3})
    rp = ReserveProvisionSpec(
        generator_name="B_gen", name="provision_B_gen__CSF_LW", drmax=10.0
    )
    out = _with_energy_usage(rp, "B", _PairIndex(k.db))
    assert out.dr_energy_usage == 0.3
    assert out.ur_energy_usage is None


# ── _dated_capacity ────────────────────────────────────────────────────────
def test_dated_capacity_variants() -> None:
    b = _battery()
    k, bat = _kit()
    assert _dated_capacity(_PairIndex(k.db), b, 24) is b  # undated only
    k, bat = _kit()  # fresh DB: the membership-row index is cached
    k.sysprop("Battery", bat, "Capacity", 200.0, date_from=_DAY1, date_to=_DAY1)
    assert _dated_capacity(_PairIndex(k.db), b, 24) is b  # same value
    k, bat = _kit()
    k.sysprop("Battery", bat, "Capacity", 150.0, date_from=_DAY1, date_to=_DAY1)
    idx = _PairIndex(k.db)
    out = _dated_capacity(idx, b, 24)
    assert out.emax == pytest.approx(150.0)
    assert out.emax_profile == ()
    # Two days, de-rated on the first only: an hourly emax schedule.
    out = _dated_capacity(idx, b, 48)
    assert out.emax == pytest.approx(200.0)
    assert out.emax_profile == (150.0,) * 24 + (200.0,) * 24


def test_apply_dated_capacity_without_native_rows() -> None:
    k, bat = _kit()
    k.sysprop("Battery", bat, "Capacity", 120.0, date_from=_DAY2)
    k.db.horizon_start = _DAY2
    case = PlexosCase(bundle=BundleSpec(), batteries=(_battery(),))
    out = apply_native_bess_reserve(case, None, k.db)  # type: ignore[arg-type]
    assert out is not case
    assert out.batteries[0].emax == pytest.approx(120.0)
    assert out.batteries[0].reserve_sustain_hours is None
