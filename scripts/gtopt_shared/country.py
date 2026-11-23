# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-country regulatory defaults (``gtopt_shared/data/countries/<cc>.toml``).

One TOML file per country (ISO 3166 alpha-2, lower case) holds the
regulatory *scalars* a converter may need when the source model does not
carry them: unserved-energy cost (single value or tiers by rationing
depth), social carbon price, currency, time zone, start month of the
hydrological year and the default emissions overlay.  Every value cites
its source.

Precedence in the converters (highest first): explicit CLI flag > value
read from the source model (PLP FALLA, PLEXOS ``Region.VoLL``, SDDP
deficit curve) > operator defaults > this file > ``COST_DEFAULTS`` >
gtopt's compiled default.  Operator profiles
(:mod:`gtopt_operators`) declare their ``country``.

Schema (``schema_version = 1``)::

    country = "cl"                 # must match the file name
    name = "Chile"
    currency = "USD"
    timezone = "America/Santiago"
    hydro_year_start_month = 4     # optional

    [unserved_energy]              # optional
    kind = "tiers"                 # "tiers" | "single"
    unit = "USD/MWh"
    cost = 746.0                   # kind = "single"
    tiers = [{ depth = 0.05, cost = 795.11 }, ...]   # kind = "tiers"
    short_duration_cost = 13730.0  # optional
    source = "..."; url = "https://..."; valid_from = "YYYY-MM-DD"

    [carbon]                       # optional
    social_price = 35.0; unit = "USD/tCO2e"; source = "..."

    [emissions]                    # optional
    overlay = "cen_chile.json"     # file under gtopt_shared/data/emissions/
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from gtopt_shared.resources import package_data_dir, package_data_file

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

SCHEMA_VERSION = 1

_TOP_KEYS = frozenset(
    {
        "schema_version",
        "country",
        "name",
        "currency",
        "timezone",
        "hydro_year_start_month",
        "unserved_energy",
        "carbon",
        "emissions",
    }
)


class CountryError(ValueError):
    """Unknown country or malformed country file."""


@dataclass(frozen=True, slots=True)
class UnservedEnergyTier:
    """One tier of a depth-dependent unserved-energy cost."""

    depth: float  # upper fraction of the demand covered by the tier
    cost: float


@dataclass(frozen=True, slots=True)
class UnservedEnergy:  # pylint: disable=too-many-instance-attributes
    """``[unserved_energy]`` table."""

    kind: str
    unit: str
    source: str
    url: str = ""
    valid_from: str = ""
    cost: float | None = None
    tiers: tuple[UnservedEnergyTier, ...] = ()
    short_duration_cost: float | None = None

    @property
    def single_value(self) -> float:
        """One value for tools that admit a single cost: the first tier."""
        if self.kind == "single":
            assert self.cost is not None
            return self.cost
        return self.tiers[0].cost


@dataclass(frozen=True, slots=True)
class CountryDefaults:  # pylint: disable=too-many-instance-attributes
    """Parsed ``<cc>.toml``."""

    code: str
    name: str
    currency: str
    timezone: str
    hydro_year_start_month: int | None = None
    unserved_energy: UnservedEnergy | None = None
    social_carbon_price: float | None = None
    carbon_unit: str = ""
    carbon_source: str = ""
    emissions_overlay: str = ""


def _num(value: Any, where: str, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where} must be a number")
        return None
    return float(value)


def _parse_unserved(tbl: Any, errors: list[str]) -> UnservedEnergy | None:
    if tbl is None:
        return None
    where = "[unserved_energy]"
    if not isinstance(tbl, dict):
        errors.append(f"{where} must be a table")
        return None
    kind = tbl.get("kind")
    for key in ("unit", "source"):
        if not isinstance(tbl.get(key), str) or not tbl.get(key):
            errors.append(f"{where}.{key} must be a non-empty string")
    cost: float | None = None
    tiers: list[UnservedEnergyTier] = []
    if kind == "single":
        cost = _num(tbl.get("cost"), f"{where}.cost", errors)
    elif kind == "tiers":
        raw = tbl.get("tiers")
        if not isinstance(raw, list) or not raw:
            errors.append(f"{where}.tiers must be a non-empty list")
            raw = []
        for i, row in enumerate(raw):
            if not isinstance(row, dict):
                errors.append(f"{where}.tiers[{i}] must be a table")
                continue
            d = _num(row.get("depth"), f"{where}.tiers[{i}].depth", errors)
            c = _num(row.get("cost"), f"{where}.tiers[{i}].cost", errors)
            if d is not None and c is not None:
                tiers.append(UnservedEnergyTier(depth=d, cost=c))
        depths = [t.depth for t in tiers]
        costs = [t.cost for t in tiers]
        if depths and (
            depths != sorted(depths) or depths[-1] != 1.0 or depths[0] <= 0.0
        ):
            errors.append(f"{where}.tiers depths must increase in (0, 1] up to 1.0")
        if costs != sorted(costs):
            errors.append(f"{where}.tiers costs must be non-decreasing (convex)")
    else:
        errors.append(f"{where}.kind must be 'single' or 'tiers'")
    short = tbl.get("short_duration_cost")
    return UnservedEnergy(
        kind=str(kind),
        unit=str(tbl.get("unit", "")),
        source=str(tbl.get("source", "")),
        url=str(tbl.get("url", "")),
        valid_from=str(tbl.get("valid_from", "")),
        cost=cost,
        tiers=tuple(tiers),
        short_duration_cost=(
            None
            if short is None
            else _num(short, f"{where}.short_duration_cost", errors)
        ),
    )


def parse_country(data: dict[str, Any], code: str) -> CountryDefaults:
    """Validate parsed TOML of country ``code``; raise :class:`CountryError`."""
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        errors.append(f"unknown key(s) {sorted(unknown)}")
    if data.get("country") != code:
        errors.append(f"country must be {code!r} (the file name)")
    for key in ("name", "currency", "timezone"):
        if not isinstance(data.get(key), str) or not data.get(key):
            errors.append(f"{key} must be a non-empty string")
    month = data.get("hydro_year_start_month")
    if month is not None and (not isinstance(month, int) or not 1 <= month <= 12):
        errors.append("hydro_year_start_month must be an integer in 1..12")
    unserved = _parse_unserved(data.get("unserved_energy"), errors)
    carbon = data.get("carbon") or {}
    scc = (
        _num(carbon["social_price"], "[carbon].social_price", errors)
        if "social_price" in carbon
        else None
    )
    overlay = str((data.get("emissions") or {}).get("overlay", ""))
    if errors:
        raise CountryError(
            f"invalid country file {code}.toml:\n  " + "\n  ".join(errors)
        )
    return CountryDefaults(
        code=code,
        name=str(data["name"]),
        currency=str(data["currency"]),
        timezone=str(data["timezone"]),
        hydro_year_start_month=month,
        unserved_energy=unserved,
        social_carbon_price=scc,
        carbon_unit=str(carbon.get("unit", "")),
        carbon_source=str(carbon.get("source", "")),
        emissions_overlay=overlay,
    )


def available_countries() -> list[str]:
    """Codes of every bundled country file."""
    root = package_data_dir("gtopt_shared", "data", "countries")
    return sorted(p.stem for p in root.glob("*.toml"))


@cache
def load_country(code: str) -> CountryDefaults:
    """Return the defaults of country ``code`` (ISO alpha-2, any case)."""
    cc = code.strip().lower()
    if cc not in available_countries():
        raise CountryError(
            f"no country file for {code!r}; known: {', '.join(available_countries())}"
        )
    path = package_data_file(
        "gtopt_shared", "data", "countries", f"{cc}.toml", purpose="country defaults"
    )
    with path.open("rb") as fh:
        return parse_country(tomllib.load(fh), cc)


def emissions_overlay_path(code: str) -> Path | None:
    """Bundled emissions overlay of country ``code`` (``None`` if none)."""
    overlay = load_country(code).emissions_overlay
    if not overlay:
        return None
    return package_data_file(
        "gtopt_shared",
        "data",
        "emissions",
        overlay,
        purpose=f"{code} emissions overlay",
    )
