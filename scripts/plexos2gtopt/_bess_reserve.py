"""Native mapping of CEN's PLEXOS "BESS SSCC Constraints" (``--bess-reserve``).

CEN's PCP model ships, per BESS that offers frequency-control reserve
(category ``BESS SSCC Constraints``):

``UPStorageBound_<bat>``
    ``r_up - eta_d * E <= 0`` (energy to sustain the up reserve 1 h).
``DOWNStorageBound_<bat>``
    ``E + eta_c * r_dn <= Capacity`` (room to absorb the down reserve 1 h).
``<bat>_CF_GEN_COMP`` / ``<bat>_CF_LOAD_COMP``
    ``sum_z a_z(t) r_dn,z - Gen(<bat>_AUX) = 0`` and
    ``sum_z a_z(t) r_up,z - Load(<bat>_AUX) = 0`` (Sense unset => ``=``),
    with ``<bat>_AUX`` a 99 999 MWh, +-1000 MW, eta = 1, 50 %-initial,
    free-end auxiliary PLEXOS Battery at the same node and ``a_z(t)`` from
    ``ReserveUsageTxCompensation.csv``.
``CSF_{RS,LW}_MIN_BAT_<bat>``
    minimum 5 MW provision when the unit offers reserve.

The CF_*_COMP equalities inject ``a*r_dn`` and withdraw ``a*r_up`` at the
BESS bus from a fictitious tank: energy is created/destroyed and, where the
injection cannot be disposed of, the bus price is pinned at
``(c_dn - pi_dn)/a < 0``.  With ``--bess-reserve native`` the converter
instead sets ``Battery.reserve_sustain_hours`` (gtopt's native power
headroom + state-of-charge sufficiency rows, see
``docs/formulation/mathematical-formulation.md``), turns the four
families above (except the CSF minimum-provision rows) into inactive
provenance stubs, and makes the BESS SSCC provisions count at their gross
value (PLEXOS ``Energy Usage`` from ``SSCC_Activation_BESS.csv`` is an
expected-deployment fraction, not a requirement-contribution factor).

It also maps, for every battery with the native rows:

* ``Max Response`` / ``Max Charge Response`` of each ``C*_BESS`` reserve
  (scalar, or the ``CFdata/*_MRU|MRD.csv`` data file the row names) →
  ``ReserveProvision.{ur,dr}max_response`` / ``…_charge_response`` (the
  provision is split into a discharge and a charge part, each capped);
  the total cap becomes the physical range ``Pd + Pc``;
* the ``<bat>_CF_LOAD_COMP`` / ``_CF_GEN_COMP`` reserve ``Provision
  Coefficient`` (0.3 for CSF, 0 for CPF/CTF in PCP 2026-11-07) →
  the provision's ``ur_energy_usage`` (``_CF_LOAD_COMP``, raise) /
  ``dr_energy_usage`` (``_CF_GEN_COMP``, lower), per reserve; the
  ``<bat>_AUX`` battery is never emitted (``extract_batteries``).

and, for every battery, a dated ``Capacity`` in force inside the horizon
→ the ``Battery.emax`` schedule.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath

from ._native_reserve import dated_row, hourly_values
from .entities import BatterySpec, PlexosCase, ReserveProvisionSpec
from .plexos_csv import sniff_delimiter
from .plexos_loader import PlexosBundle
from .plexos_xml import PlexosDataRow, PlexosDb, load_xml

logger = logging.getLogger(__name__)

#: Accepted values of ``--bess-reserve``.
BESS_RESERVE_MODES = ("uc", "native")

_UP_PREFIX = "UPStorageBound_"
_DN_PREFIX = "DOWNStorageBound_"
_COMP_SUFFIXES = ("_CF_GEN_COMP", "_CF_LOAD_COMP")
#: PLEXOS "no limit" sentinel of ``Max Response``.
_NO_LIMIT = 1e29
_RESPONSE_PROPS = {
    "Max Response": "max_response",
    "Max Charge Response": "max_charge_response",
}


def _rows_by_membership(db: PlexosDb) -> dict[int, list[PlexosDataRow]]:
    cached: dict[int, list[PlexosDataRow]] | None = getattr(
        db, "_bess_rows_by_mem", None
    )
    if cached is not None:
        return cached
    out: dict[int, list[PlexosDataRow]] = {}
    for row in db.data_rows:
        out.setdefault(row.membership_id, []).append(row)
    object.__setattr__(db, "_bess_rows_by_mem", out)
    return out


def _battery_coefficients(db: PlexosDb, constraint: str) -> dict[str, float]:
    """``{property name: value}`` of the Battery -> ``constraint`` membership."""
    objs = db.object_by_id()
    cons = next(
        (o for o in db.objects_of_class("Constraint") if o.name == constraint),
        None,
    )
    if cons is None:
        return {}
    coll = db.collection_for_named("Battery", "Constraint", "Constraints")
    if coll is None:
        return {}
    props = {p.property_id: p.name for p in db.properties}
    out: dict[str, float] = {}
    for m in db.memberships_of(coll.collection_id):
        if m.child_object_id != cons.object_id or objs.get(m.parent_object_id) is None:
            continue
        for row in _rows_by_membership(db).get(m.membership_id, ()):
            out[props.get(row.property_id, "")] = row.value
    return out


def sustain_hours_from_bounds(
    db: PlexosDb, battery: str, eta_c: float, eta_d: float
) -> float | None:
    """Recover the sustain duration ``T`` [h] from CEN's storage bounds.

    ``UPStorageBound``: ``k_r r_up - k_e E <= 0``  =>  ``T = eta_d k_r / k_e``.
    ``DOWNStorageBound``: ``E + k_d r_dn <= Cap``  =>  ``T = k_d / eta_c``.
    Returns the up value when present (else the down one); ``None`` when
    the battery has neither row.  Mismatches are logged.
    """
    up = _battery_coefficients(db, _UP_PREFIX + battery)
    dn = _battery_coefficients(db, _DN_PREFIX + battery)
    t_up = None
    k_e = -up.get("Energy Coefficient", 0.0)
    k_r = up.get("Regulation Raise Reserve Provision Coefficient", 0.0)
    if k_e > 0.0 and k_r > 0.0:
        t_up = (eta_d if eta_d > 0.0 else 1.0) * k_r / k_e
    t_dn = None
    k_d = dn.get("Regulation Lower Reserve Provision Coefficient", 0.0)
    if k_d > 0.0 and dn.get("Energy Coefficient", 0.0) > 0.0:
        t_dn = k_d / (eta_c if eta_c > 0.0 else 1.0)
    if t_up is not None and t_dn is not None and abs(t_up - t_dn) > 1e-6:
        logger.warning(
            "bess-reserve native: %s up/down sustain differ (%.4f h vs %.4f h);"
            " using the up value",
            battery,
            t_up,
            t_dn,
        )
    return t_up if t_up is not None else t_dn


def _is_replaced_constraint(name: str, natives: set[str]) -> bool:
    for bat in natives:
        if name in (_UP_PREFIX + bat, _DN_PREFIX + bat):
            return True
        if any(name == bat + suffix for suffix in _COMP_SUFFIXES):
            return True
    return False


class _PairIndex:
    """``(parent name, child name, property) -> data rows`` of a PLEXOS DB."""

    def __init__(self, db: PlexosDb) -> None:
        objs = db.object_by_id()
        props = {p.property_id: p.name for p in db.properties}
        pairs = {
            m.membership_id: (m.parent_object_id, m.child_object_id)
            for m in db.memberships
        }
        self.rows: dict[tuple[str, str, str], list[PlexosDataRow]] = {}
        self.text = db.text_for_data
        self.start: datetime | None = db.horizon_start
        for mid, rows in _rows_by_membership(db).items():
            pair = pairs.get(mid)
            if pair is None:
                continue
            parent, child = objs.get(pair[0]), objs.get(pair[1])
            if parent is None or child is None:
                continue
            for row in rows:
                key = (parent.name, child.name, props.get(row.property_id, ""))
                self.rows.setdefault(key, []).append(row)

    def get(self, parent: str, child: str, prop: str) -> list[PlexosDataRow]:
        """Rows of ``prop`` on the ``parent -> child`` membership."""
        return self.rows.get((parent, child, prop), [])

    def by_child(self, child: str, prop: str) -> list[tuple[str, PlexosDataRow]]:
        """``(parent, row)`` of every ``prop`` row whose child is ``child``."""
        return [
            (par, row)
            for (par, ch, pr), rows in self.rows.items()
            if ch == child and pr == prop
            for row in rows
        ]


def _last_float(row: list[str]) -> float | None:
    """The last cell as a number (``None`` for a header / blank cell)."""
    try:
        return float(row[-1])
    except (ValueError, IndexError):
        return None


def read_value_csv(
    path: Path, n_hours: int, start: datetime | None = None
) -> tuple[float, ...]:
    """Hourly ``VALUE`` (last) column of a CEN ``YEAR,MONTH,DAY,PERIOD`` file.

    With ``start`` and a ``YEAR,MONTH,DAY,PERIOD[,BAND]`` header the rows
    are aligned by date and period (band 1), so a file that starts before
    the horizon is not shifted; otherwise they are taken in file order.
    Empty (the caller falls back to the scalar) when the file is missing
    or does not cover every one of the ``n_hours`` horizon hours.
    """
    if not path.is_file():
        return ()
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh, delimiter=sniff_delimiter(fh)) if r]
    head = [c.strip().upper() for c in rows[0]] if rows else []
    if start is None or not {"YEAR", "MONTH", "DAY", "PERIOD"} <= set(head):
        vals = [v for row in rows if (v := _last_float(row)) is not None]
        return tuple(vals[:n_hours]) if len(vals) >= n_hours else ()
    by_key: dict[tuple[int, int, int, int], float] = {}
    for row in rows:
        value = _last_float(row)
        cell = dict(zip(head, row, strict=False))
        try:
            year, month, day, period = (
                int(float(cell[c])) for c in ("YEAR", "MONTH", "DAY", "PERIOD")
            )
            band = int(float(cell.get("BAND") or "1"))
        except ValueError:
            continue
        if value is not None and band == 1:
            by_key.setdefault((year, month, day, period), value)
    out: list[float] = []
    for h in range(n_hours):
        when = start + timedelta(hours=h)
        value = by_key.get((when.year, when.month, when.day, when.hour + 1))
        if value is None:
            return ()
        out.append(value)
    return tuple(out)


def _bundle_file(root: Path, text: str) -> Path | None:
    """``root``-relative data file named by a PLEXOS (Windows) path.

    ``None`` for an absolute path, a drive or a ``..`` part: a data file
    reference must stay inside the bundle.
    """
    win = PureWindowsPath(text)
    if win.drive or win.root or any(p == ".." for p in win.parts):
        return None
    return root.joinpath(*win.parts)


def _in_force(
    rows: list[PlexosDataRow], start: datetime | None
) -> PlexosDataRow | None:
    """The row in force at the horizon start (lowest ``data_id`` without one)."""
    if start is not None:
        return dated_row(rows, start, skip_sentinel=False)
    undated = [r for r in rows if r.date_from is None and r.date_to is None]
    return min(undated or rows, key=lambda r: r.data_id) if rows else None


def _response_series(
    idx: _PairIndex,
    root: Path | None,
    key: tuple[str, str, str],
    n_hours: int,
    no_cap: float | None = None,
) -> tuple[float, ...]:
    """Hourly cap [MW] of one ``Max (Charge) Response``; ``()`` = no cap.

    ``key`` = ``(reserve, battery, property)``.  The row in force at the
    horizon start decides (dated sets resolved); a data-file row is read
    aligned by date, a scalar row resolves per hour (dated / timesliced
    rows).  The PLEXOS "no limit" sentinel (``>= 1e29``) is no cap: the
    whole series when the scalar is a sentinel, else the hours carrying it
    get ``no_cap`` (the physical range), or the series is dropped when
    ``no_cap`` is ``None``.
    """
    rows = idx.get(*key)
    row = _in_force(rows, idx.start)
    if row is None:
        return ()
    text = idx.text.get(row.data_id)
    series: tuple[float, ...] = ()
    if text:
        path = _bundle_file(root, text) if root is not None else None
        series = read_value_csv(path, n_hours, idx.start) if path is not None else ()
        if not series:
            logger.warning(
                "bess-reserve native: %s %s of %s: data file %s not readable, "
                "outside the bundle or not covering the horizon; using the "
                "scalar value",
                key[0],
                key[2],
                key[1],
                text,
            )
    if not series:
        if row.value >= _NO_LIMIT:
            return ()
        plain = [r for r in rows if not idx.text.get(r.data_id)]
        series = hourly_values(plain, idx.start, n_hours, row.value)
    if all(v >= _NO_LIMIT for v in series):
        return ()
    if any(v >= _NO_LIMIT for v in series):
        if no_cap is None:
            return ()
        series = tuple(no_cap if v >= _NO_LIMIT else v for v in series)
    return series


def _with_response_caps(
    rp: ReserveProvisionSpec,
    bat: BatterySpec,
    idx: _PairIndex,
    root: Path | None,
    n_hours: int,
) -> ReserveProvisionSpec:
    """Move a per-(battery, reserve) provision's caps to the per-side fields."""
    _, sep, reserve = rp.name.partition("__")
    if not sep:
        return rp
    total = bat.pmax_discharge + bat.pmax_charge
    caps = {
        field: _response_series(
            idx, root, (reserve, bat.name, prop), n_hours, no_cap=total
        )
        for prop, field in _RESPONSE_PROPS.items()
    }
    if not any(caps.values()):
        return rp
    if rp.urmax > 0.0 or rp.urmax_profile:
        return replace(
            rp,
            urmax=total,
            urmax_profile=(),
            urmax_response=caps["max_response"],
            urmax_charge_response=caps["max_charge_response"],
        )
    return replace(
        rp,
        drmax=total,
        drmax_profile=(),
        drmax_response=caps["max_response"],
        drmax_charge_response=caps["max_charge_response"],
    )


def _with_energy_usage(
    rp: ReserveProvisionSpec, battery: str, idx: _PairIndex
) -> ReserveProvisionSpec:
    """Per-provision deployment energy from the reserve's ``CF_*_COMP`` row.

    Raise reserves read ``<bat>_CF_LOAD_COMP``, lower ones
    ``<bat>_CF_GEN_COMP`` (``Provision Coefficient``, 0.3 CSF / 0 CPF/CTF
    in CEN PCP); only a positive coefficient is emitted.
    """
    _, sep, reserve = rp.name.partition("__")
    if not sep:
        return rp
    up = rp.urmax > 0.0 or bool(rp.urmax_profile)
    suffix = "_CF_LOAD_COMP" if up else "_CF_GEN_COMP"
    row = _in_force(
        idx.get(reserve, battery + suffix, "Provision Coefficient"), idx.start
    )
    coef = row.value if row is not None else 0.0
    if coef <= 0.0 or coef >= _NO_LIMIT:
        return rp
    if up:
        return replace(rp, ur_energy_usage=coef)
    return replace(rp, dr_energy_usage=coef)


def _dated_capacity(idx: _PairIndex, bat: BatterySpec, n_hours: int) -> BatterySpec:
    """``emax`` (scalar or hourly schedule) from the dated ``Capacity`` rows."""
    rows = [row for _par, row in idx.by_child(bat.name, "Capacity")]
    if not any(r.date_from is not None or r.date_to is not None for r in rows):
        return bat
    values = hourly_values(rows, idx.start, n_hours, bat.emax)
    if all(v == values[0] for v in values):
        if values[0] == bat.emax:
            return bat
        logger.info(
            "bess-reserve native: %s Capacity %g -> %g (dated)",
            bat.name,
            bat.emax,
            values[0],
        )
        return replace(bat, emax=values[0])
    logger.info(
        "bess-reserve native: %s dated Capacity %g..%g -> emax schedule",
        bat.name,
        min(values),
        max(values),
    )
    return replace(bat, emax=max(values), emax_profile=values)


def apply_native_bess_reserve(
    case: PlexosCase, bundle: PlexosBundle, db: PlexosDb | None = None
) -> PlexosCase:
    """Rewrite ``case`` so CEN's BESS SSCC rows use gtopt's native primitive."""
    if not case.batteries:
        return case
    if db is None:
        db = load_xml(bundle.xml_path)
    idx = _PairIndex(db)
    n_hours = max(1, case.bundle.n_days) * 24
    root = getattr(bundle, "root", None)
    natives: set[str] = set()
    batteries = []
    for bat in case.batteries:
        bat = _dated_capacity(idx, bat, n_hours)
        t_h = sustain_hours_from_bounds(
            db, bat.name, bat.input_efficiency, bat.output_efficiency
        )
        if t_h is None:
            batteries.append(bat)
            continue
        natives.add(bat.name)
        batteries.append(replace(bat, reserve_sustain_hours=t_h))
    if not natives:
        if all(a is b for a, b in zip(batteries, case.batteries, strict=True)):
            return case
        return replace(case, batteries=tuple(batteries))

    ucs = []
    n_dropped = 0
    for uc in case.user_constraints:
        if _is_replaced_constraint(uc.name, natives) and uc.active is not False:
            n_dropped += 1
            ucs.append(
                replace(
                    uc,
                    active=False,
                    description=(
                        (uc.description + " ").lstrip()
                        + "[--bess-reserve native: replaced by "
                        "Battery.reserve_sustain_hours]"
                    ),
                )
            )
        else:
            ucs.append(uc)

    bat_by_name = {b.name: b for b in batteries}
    provisions = []
    for rp in case.reserve_provisions:
        bat_name = rp.generator_name.removesuffix("_gen")
        if bat_name in natives and rp.name.startswith("provision_"):
            rp = replace(
                rp,
                ur_provision_factor_profile=tuple(
                    1.0 for _ in rp.ur_provision_factor_profile
                ),
                dr_provision_factor_profile=tuple(
                    1.0 for _ in rp.dr_provision_factor_profile
                ),
            )
            rp = _with_response_caps(rp, bat_by_name[bat_name], idx, root, n_hours)
            rp = _with_energy_usage(rp, bat_name, idx)
        provisions.append(rp)

    logger.info(
        "bess-reserve native: %d batteries (%s) -> reserve_sustain_hours; "
        "%d CEN SSCC rows made inactive",
        len(natives),
        ", ".join(sorted(natives)),
        n_dropped,
    )
    return replace(
        case,
        batteries=tuple(batteries),
        user_constraints=tuple(ucs),
        reserve_provisions=tuple(provisions),
    )
