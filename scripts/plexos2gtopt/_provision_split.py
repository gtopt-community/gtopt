# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""One ``ReserveProvision`` per PLEXOS reserve family, sharing headroom.

PLEXOS gives every (Reserve, Generator) pair its own provision variable
(``Reserve.Generators`` ``Provision`` in the solution), all bounded by one
shared headroom ``gen + Σ_up r ≤ available capacity``.  CEN units take part
in several families at once (RES 2025-09-16: ANGOSTURA_U1 CSF 45 + CTF 16,
NEHUENCO_1 CSF 37.5 + CTF 31.5, RALCO_U1 CPF 73 + CSF 54; the headroom
holds on every providing unit).

The extractor emits ONE provision per generator covering all its zones, and
gtopt's ``ReserveProvision`` has ONE up column shared by all its zones, so a
zone balance on one family caps the offer to every other family.  On CEN
the ``CTF_UpMinProvision`` equality (PLEXOS Sense 0, activity == RHS, e.g.
76 MW) then pins the shared column of every CTF-eligible unit, which also
starves ``CPF5mUp`` (167 MW shortage on 2025-09-16 h21) and, through the
``Generation_SEN`` term of the CPF regression, prices over-generation at
``shortage × 0.0134``, so the surplus burns in losses at a −1 $/MWh floor.

:func:`split_provisions_by_family` splits every multi-family provision into
one provision per family (``CPF`` / ``CSF`` / ``CTF`` from the zone
name, any other zone its own family), named ``<name>__<FAMILY>``, each
with ``shared_headroom`` (minuta (A.23)–(A.24): one headroom row per
generator), its own zone subset / factors and, when CEN's ``CFdata`` ships
it, its own per-family ``MRU`` / ``MRD`` cap.  User-constraint references to
the old name are rewritten: a row whose name names one family (``*_CPF_*``,
``*ActCSFLW``) takes that family's column; any other row (``Reserve Units``
gates "any reserve", ``CFRS_*``, ``Max_Tx_*``) takes the sum of the
families, i.e. exactly the old single column.  BESS provisions are already
one per zone (``provision_<bat>_gen__<ZONE>``) and stay untouched.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import replace

from .entities import PlexosCase, ReserveProvisionSpec, UserConstraintSpec

logger = logging.getLogger(__name__)

#: CEN reserve families (zone-name token: ``CPF5mUp``, ``CSF_UpMinProvision``,
#: ``SpecialCPFNorth`` — the northern CPF sub-requirement — …).
_FAMILY_RE = re.compile(r"(CPF|CSF|CTF)")
#: A family token in a user-constraint name.
_ROW_FAMILY_RE = re.compile(r"(CPF|CSF|CTF)")
_NUM = r"[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?"
_REF_RE = re.compile(
    rf"(?P<sign>[+-]?)\s*(?:(?P<num>{_NUM})\s*\*\s*)?"
    r'reserve_provision\("(?P<ref>[^"]+)"\)\.(?P<attr>[a-z_]+)'
)

#: ``(generator, family, "MRU" | "MRD") -> per-hour cap`` (empty = none).
CapLookup = Callable[[str, str, str], tuple[float, ...]]


def reserve_family(zone: str) -> str:
    """``CPF`` / ``CSF`` / ``CTF`` for a CEN zone, else the zone itself."""
    m = _FAMILY_RE.search(zone)
    return m.group(1) if m else zone


def _families(zones: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for z in zones:
        f = reserve_family(z)
        if f not in out:
            out.append(f)
    return out


def _subset(values: tuple[float, ...], idx: list[int]) -> tuple[float, ...]:
    return tuple(values[i] for i in idx) if values else ()


def _split_one(
    rp: ReserveProvisionSpec, caps: CapLookup | None
) -> dict[str, ReserveProvisionSpec]:
    """``{family: provision}`` for one multi-family provision."""
    base = rp.name or f"provision_{rp.generator_name}"
    out: dict[str, ReserveProvisionSpec] = {}
    for fam in _families(rp.reserve_zones):
        idx = [i for i, z in enumerate(rp.reserve_zones) if reserve_family(z) == fam]
        ur_cap = caps(rp.generator_name, fam, "MRU") if caps else ()
        dr_cap = caps(rp.generator_name, fam, "MRD") if caps else ()
        out[fam] = replace(
            rp,
            name=f"{base}__{fam}",
            reserve_zones=tuple(rp.reserve_zones[i] for i in idx),
            ur_zone_factors=_subset(rp.ur_zone_factors, idx),
            dr_zone_factors=_subset(rp.dr_zone_factors, idx),
            urmax_profile=ur_cap or rp.urmax_profile,
            drmax_profile=dr_cap or rp.drmax_profile,
            shared_headroom=True,
        )
    return out


def _rewrite(
    uc: UserConstraintSpec, splits: dict[str, dict[str, ReserveProvisionSpec]]
) -> UserConstraintSpec:
    """``uc`` with every reference to a split provision redirected."""
    if "reserve_provision(" not in uc.expression:
        return uc
    tokens = set(_ROW_FAMILY_RE.findall(uc.name))
    row_family = tokens.pop() if len(tokens) == 1 else None

    def _sub(m: re.Match[str]) -> str:
        fams = splits.get(m.group("ref"))
        if fams is None:
            return m.group(0)
        sign = m.group("sign")
        num = m.group("num")
        coef = num if num is not None else "1"
        attr = m.group("attr")
        if row_family is not None:
            target = fams.get(row_family)
            if target is None:  # the unit is not in that family: term is 0
                first = next(iter(fams.values()))
                return f'{sign} 0 * reserve_provision("{first.name}").{attr}'
            return f'{sign} {coef} * reserve_provision("{target.name}").{attr}'
        cont = "-" if sign == "-" else "+"
        terms = [
            f'{coef} * reserve_provision("{p.name}").{attr}' for p in fams.values()
        ]
        return f"{sign} " + f" {cont} ".join(terms)

    expression = _REF_RE.sub(_sub, uc.expression).strip()
    if expression == uc.expression:
        return uc
    return replace(uc, expression=expression)


def split_provisions_by_family(
    case: PlexosCase, caps: CapLookup | None = None
) -> PlexosCase:
    """Split multi-family provisions (see the module docstring)."""
    splits: dict[str, dict[str, ReserveProvisionSpec]] = {}
    out: list[ReserveProvisionSpec] = []
    for rp in case.reserve_provisions:
        if len(_families(rp.reserve_zones)) <= 1:
            out.append(rp)
            continue
        parts = _split_one(rp, caps)
        splits[rp.name or f"provision_{rp.generator_name}"] = parts
        out.extend(parts.values())
    if not splits:
        return case
    ucs = tuple(_rewrite(uc, splits) for uc in case.user_constraints)
    n_rewritten = sum(a is not b for a, b in zip(ucs, case.user_constraints))
    logger.info(
        "reserve provisions: %d multi-family provision(s) split into %d "
        "per-family provisions with shared headroom; %d user constraint(s) "
        "re-referenced",
        len(splits),
        sum(len(p) for p in splits.values()),
        n_rewritten,
    )
    return replace(case, reserve_provisions=tuple(out), user_constraints=ucs)
