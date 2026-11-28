# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-(service, direction) keys of the reserve wear factors.

The six SSCC products are CPF, CSF and CTF, each up and down, and their wear
may differ.  ``eoh_per_reserve_mwh``, ``alpha_reserve`` and
``service_weights`` accept, side by side::

    {"cpf": {"up": 0.3, "dn": 0.1},   # nested per direction
     "csf_up": 0.2,                   # flat per direction
     "ctf": 0.05,                     # per service (both directions)
     "*": 0.01}                       # any service / direction

They are stored flat (``"cpf_up"``, ``"cpf_dn"``, ``"ctf"``, ``"*"``) and
resolved ``(service, direction)`` → ``service`` → ``"*_<direction>"`` →
``"*"``.  The flat per-service form of earlier versions is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Reserve directions as written by :func:`gtopt_usage.reserve_table`.
DIRECTIONS = ("up", "dn")


def parse_factors(data: Any, name: str) -> dict[str, float]:
    """Flatten a factor object; values must be numbers >= 0.

    ``{"cpf": {"up": a, "dn": b}}`` becomes ``{"cpf_up": a, "cpf_dn": b}``;
    flat keys are kept.  Raises ``ValueError`` on a bad shape, an unknown
    direction or a negative value.
    """
    if not isinstance(data, Mapping):
        raise ValueError(f"{name} must be an object {{service: value}}")
    out: dict[str, float] = {}
    for key, val in data.items():
        if isinstance(val, Mapping):
            bad = sorted(set(val) - set(DIRECTIONS))
            if bad:
                raise ValueError(
                    f"{name}.{key}: directions are up / dn (got {', '.join(bad)})"
                )
            for d, v in val.items():
                out[f"{key}_{d}"] = float(v)
        else:
            out[str(key)] = float(val)
    if any(v < 0 for v in out.values()):
        raise ValueError(f"{name} factors must be >= 0")
    return out


def resolve(
    factors: Mapping[str, float],
    service: str,
    direction: str | None = None,
    default: float = 0.0,
) -> float:
    """Factor of ``(service, direction)``; ``direction=None`` skips it."""
    keys = [service, "*"]
    if direction is not None:
        keys = [f"{service}_{direction}", service, f"*_{direction}", "*"]
    for k in keys:
        if k in factors:
            return float(factors[k])
    return default


__all__ = ["DIRECTIONS", "parse_factors", "resolve"]
