# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Operator profiles for the generic converters (``--operator``).

Converters (``plexos2gtopt``, ``plp2gtopt``, ``sddp2gtopt``,
``psse2gtopt``) are format-generic; the conventions of one system
operator (bundle layout, naming rules, audited lists, extra files) live
in an operator profile under :mod:`gtopt_operators`.  This module holds
the interface those profiles implement and the ``--operator`` selection:

* ``auto`` (default) -- ask every registered profile's :meth:`detect`;
  the first that recognises the input wins (current behaviour is kept);
* ``none`` -- purely generic conversion;
* ``<id>`` -- force one operator (``cen``, ``amm``, ...).

Only the selection and its log line exist so far: operator rules still
live in the converters and move into the profiles step by step.  The selection is logged, never
written into the case JSON.
"""

from __future__ import annotations

import argparse
import importlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

#: Input formats handled by the converters.
CONVERTER_FORMATS = ("plexos", "plp", "sddp", "psse")

#: Special ``--operator`` values.
AUTO, NONE = "auto", "none"

_LOG = logging.getLogger("gtopt_shared.operator")


@runtime_checkable
class OperatorProfile(Protocol):
    """Hooks an operator package exposes as ``PROFILE``."""

    operator_id: str

    def detect(
        self,
        fmt: str,
        input_path: Path | None,
        args: argparse.Namespace | None,
    ) -> str | None:
        """Return why the input belongs to this operator, or ``None``."""

    def flag_defaults(self, fmt: str) -> Mapping[str, Any]:
        """CLI defaults of this operator for converter ``fmt``."""


class BaseOperatorProfile:
    """No-op profile: never detected, no defaults."""

    def __init__(self, operator_id: str) -> None:
        self.operator_id = operator_id

    def detect(  # pylint: disable=useless-return
        self,
        fmt: str,
        input_path: Path | None,
        args: argparse.Namespace | None,
    ) -> str | None:
        """Never claims an input (subclasses override)."""
        del fmt, input_path, args
        return None

    def flag_defaults(self, fmt: str) -> Mapping[str, Any]:
        """No defaults."""
        del fmt
        return {}


@dataclass(frozen=True, slots=True)
class OperatorSelection:
    """Outcome of :func:`select_operator`."""

    operator: str | None
    mode: str  # "auto" | "explicit" | "none"
    reason: str

    def describe(self) -> str:
        """One log line: which operator won and why."""
        who = self.operator or "none (generic)"
        return f"operator: {who} [{self.mode}] {self.reason}".rstrip()


def operator_choices() -> list[str]:
    """Values accepted by ``--operator``."""
    from gtopt_operators import (  # pylint: disable=import-outside-toplevel
        available_operators,
    )

    return [AUTO, NONE, *available_operators()]


def get_profile(operator_id: str) -> OperatorProfile:
    """Profile of ``operator_id`` (a no-op one when the package has none)."""
    from gtopt_operators.registry import (  # pylint: disable=import-outside-toplevel
        operator_packages,
    )

    package = operator_packages().get(operator_id)
    if package is None:
        raise ValueError(f"unknown operator {operator_id!r}")
    profile = getattr(importlib.import_module(package), "PROFILE", None)
    if isinstance(profile, OperatorProfile):
        return profile
    return BaseOperatorProfile(operator_id)


def select_operator(
    choice: str | None,
    fmt: str,
    input_path: Path | str | None = None,
    args: argparse.Namespace | None = None,
    *,
    log: bool = True,
) -> OperatorSelection:
    """Resolve ``--operator`` for converter ``fmt`` and log the result."""
    if fmt not in CONVERTER_FORMATS:
        raise ValueError(f"unknown converter format {fmt!r}")
    choice = (choice or AUTO).lower()
    path = Path(input_path) if input_path is not None else None
    if choice == NONE:
        sel = OperatorSelection(None, "none", "(--operator none)")
    elif choice != AUTO:
        get_profile(choice)  # validates the id
        sel = OperatorSelection(choice, "explicit", "(--operator)")
    else:
        sel = OperatorSelection(None, "auto", "(no operator recognised the input)")
        for op in operator_choices()[2:]:
            reason = get_profile(op).detect(fmt, path, args)
            if reason:
                sel = OperatorSelection(op, "auto", f"({reason})")
                break
    if log:
        _LOG.info("%s", sel.describe())
    return sel


def add_operator_argument(parser: argparse.ArgumentParser) -> None:
    """Install ``--operator {auto,none,<id>...}`` on a converter parser."""
    parser.add_argument(
        "--operator",
        choices=operator_choices(),
        default=AUTO,
        help=(
            "system operator whose conventions apply (gtopt_operators): "
            "auto = detect from the input (default), none = generic, or an "
            "operator id.  The choice is logged; operator rules are being "
            "moved out of the converters, so today it does not change the "
            "output."
        ),
    )
