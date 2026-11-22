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
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from .plexos_xml import load_xml

if TYPE_CHECKING:
    from .entities import PlexosCase
    from .plexos_loader import PlexosBundle
    from .plexos_xml import PlexosDb

logger = logging.getLogger(__name__)

#: Accepted values of ``--bess-reserve``.
BESS_RESERVE_MODES = ("uc", "native")

_UP_PREFIX = "UPStorageBound_"
_DN_PREFIX = "DOWNStorageBound_"
_COMP_SUFFIXES = ("_CF_GEN_COMP", "_CF_LOAD_COMP")


def _rows_by_membership(db: PlexosDb) -> dict[int, list]:
    cached = getattr(db, "_bess_rows_by_mem", None)
    if cached is not None:
        return cached
    out: dict[int, list] = {}
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


def apply_native_bess_reserve(
    case: PlexosCase, bundle: PlexosBundle, db: PlexosDb | None = None
) -> PlexosCase:
    """Rewrite ``case`` so CEN's BESS SSCC rows use gtopt's native primitive."""
    if db is None:
        db = load_xml(bundle.xml_path)
    natives: set[str] = set()
    batteries = []
    for bat in case.batteries:
        t_h = sustain_hours_from_bounds(
            db, bat.name, bat.input_efficiency, bat.output_efficiency
        )
        if t_h is None:
            batteries.append(bat)
            continue
        natives.add(bat.name)
        batteries.append(replace(bat, reserve_sustain_hours=t_h))
    if not natives:
        return case

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
