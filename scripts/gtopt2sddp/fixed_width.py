# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fixed-width writing helpers for the PSR SDDP ``.dat`` dialect.

The reader (:mod:`sddp2gtopt.sddp_dat_parsers`) turns every header token
into a column: a numeric value is the slice from the end of the previous
token to the end of its own token (so values are right-aligned to their
token), and a name is the 12 characters starting at its label.  The
helpers here build such a header from ``(label, width)`` pairs and format
records to match.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

NAME_WIDTH = 12


@dataclass(frozen=True, slots=True)
class Column:
    """One fixed-width column: ``label`` as the reader sees it, ``width``."""

    label: str
    width: int
    text: bool = False

    @property
    def token(self) -> str:
        """Header token: the label padded on the left with dots."""
        if self.text:
            return self.label.center(self.width, ".")
        return self.label.rjust(self.width, ".")


def fmt_num(value: float | int | None, width: int) -> str:
    """Format ``value`` in at most ``width`` characters, keeping precision.

    Integers are written as integers; floats use the shortest ``g``
    representation that fits (``None`` / NaN give a blank cell).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        text = str(value)
        if len(text) > width:
            raise ValueError(f"integer {value} does not fit in {width} columns")
        return text
    if math.isnan(value):
        return ""
    if float(value).is_integer() and abs(value) < 10 ** (width - 2):
        return f"{value:.1f}" if len(f"{value:.1f}") <= width else str(int(value))
    for prec in range(15, 0, -1):
        text = f"{value:.{prec}g}"
        if len(text) <= width:
            return text
    raise ValueError(f"value {value} does not fit in {width} columns")


def header(columns: list[Column], *, lead: str = "") -> str:
    """Header line: ``lead`` + the column tokens joined by one space."""
    return lead + " ".join(c.token for c in columns)


def record(columns: list[Column], values: Sequence[object], *, lead: str = "") -> str:
    """One record aligned under :func:`header` (same ``lead``)."""
    parts: list[str] = []
    for col, val in zip(columns, values, strict=True):
        if col.text:
            name = "" if val is None else str(val)
            parts.append(name[:NAME_WIDTH].ljust(col.width))
        else:
            cell = fmt_num(val, col.width)  # type: ignore[arg-type]
            parts.append(cell.rjust(col.width))
    return (lead + " ".join(parts)).rstrip()


def fmt_fixed7(value: float) -> str:
    """Format an inflow in exactly 7 characters (``hinflw.dat``)."""
    v = max(0.0, float(value))
    int_digits = len(str(int(v)))
    decimals = 6 - int_digits
    if decimals >= 1:
        text = f"{v:7.{decimals}f}"
    else:
        text = f"{round(v):7d}"
    if len(text) != 7:
        raise ValueError(f"inflow {value} does not fit in 7 columns")
    return text
