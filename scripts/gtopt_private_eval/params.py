# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Parameters: built-in defaults (with sources) + user config overrides.

A configuration is a nested mapping (JSON, or YAML when PyYAML is
installed) deep-merged over :data:`DEFAULTS`:

``finance``      pre-tax WACC, fixed O&M % of capex, default life
``co2``          carbon price in the cash flow and the sensitivity list
``technologies`` per technology: emission rate, life, extra VOM, capex
                 range for the sensitivity (low / high $/kW)
``projects``     per project name: technology, overnight cost, life,
                 private WACC, fixed O&M %, extra VOM, emission rate, ...
``markets``      one block per market model (see ``markets/``)
``sources``      ``{key: {"url": ..., "date": ..., "note": ...}}``

Every regulator / operator value in the defaults is a single cited
reference parameter (link, not embed).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .defaults import DEFAULTS

#: substring → technology (first match wins) when a project has no mapping
_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("bess", "bess"),
    ("_fv", "solar_pv"),
    ("_eo", "wind"),
    ("batt", "bess"),
    ("storage", "bess"),
    ("solar", "solar_pv"),
    ("pv", "solar_pv"),
    ("wind", "wind"),
    ("eol", "wind"),
    ("nuclear", "nuclear"),
    ("coal", "coal"),
    ("carbon", "coal"),
    ("ccgt", "ccgt"),
    ("combined", "ccgt"),
    ("ocgt", "ocgt"),
    ("peak", "ocgt"),
    ("diesel", "ocgt"),
    ("gas", "ocgt"),
    ("hydro", "hydro"),
)


def deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive merge: mappings merge, everything else is replaced."""
    out = copy.deepcopy(dict(base))
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None) -> dict[str, Any]:
    """Defaults merged with the JSON / YAML file at ``path`` (if any)."""
    if path is None:
        return copy.deepcopy(DEFAULTS)
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml  # noqa: PLC0415  (optional dependency)

        user = yaml.safe_load(text) or {}
    else:
        user = json.loads(text)
    if not isinstance(user, dict):
        raise ValueError(f"{p}: the configuration must be a mapping")
    return deep_merge(DEFAULTS, user)


def set_path(cfg: dict[str, Any], dotted: str, value: Any) -> None:
    """``--set a.b.c=value`` (value parsed as JSON when possible)."""
    keys = dotted.split(".")
    node = cfg
    for k in keys[:-1]:
        node = node.setdefault(k, {})
        if not isinstance(node, dict):
            raise ValueError(f"--set {dotted}: {k} is not a mapping")
    try:
        node[keys[-1]] = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        node[keys[-1]] = value


class Params:
    """Typed accessors over a merged configuration."""

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        self.cfg = cfg

    @property
    def finance(self) -> Mapping[str, Any]:
        return self.cfg.get("finance", {})

    @property
    def co2(self) -> Mapping[str, Any]:
        return self.cfg.get("co2", {})

    def market(self, name: str) -> Mapping[str, Any]:
        return self.cfg.get("markets", {}).get(name, {})

    def project(self, name: str) -> Mapping[str, Any]:
        return self.cfg.get("projects", {}).get(name, {}) or {}

    def technology(self, name: str, kind: str, elem_type: str | None = None) -> str:
        """Project mapping → element ``type`` (``technology_types``) → name
        hints → ``thermal``.  A generic type (``thermal``) yields to a more
        specific name hint (``..._ccgt``)."""
        tech = self.project(name).get("technology")
        if tech:
            return str(tech)
        if kind == "Battery":
            return "bess"
        if kind == "Line":
            return "line"
        types = self.cfg.get("technology_types", {}) or {}
        by_type = types.get(elem_type.lower()) if elem_type else None
        if by_type and by_type != "thermal":
            return str(by_type)
        low = name.lower()
        for hint, t in _NAME_HINTS:
            if hint in low:
                return t
        return str(by_type) if by_type else "thermal"

    def tech(self, tech: str) -> Mapping[str, Any]:
        return self.cfg.get("technologies", {}).get(tech, {}) or {}

    def value(self, name: str, tech: str, key: str, default: Any = None) -> Any:
        """Project override → technology value → finance default."""
        for src in (self.project(name), self.tech(tech)):
            if src.get(key) is not None:
                return src[key]
        v = self.finance.get(key)
        return default if v is None else v

    def sources(self) -> dict[str, Any]:
        return dict(self.cfg.get("sources", {}))
