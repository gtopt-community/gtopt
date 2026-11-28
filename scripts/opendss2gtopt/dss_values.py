# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Value parsing for OpenDSS property strings.

OpenDSS property values are plain text: numbers, booleans, bus specs,
arrays delimited by ``[]``, ``()``, ``{}``, ``""`` or ``''``, lower-
triangular matrices with ``|`` row separators and, for numeric
properties, in-line reverse-Polish (RPN) expressions such as
``(8 1000 /)``.  These helpers turn the raw strings kept by
:mod:`opendss2gtopt.dss_parser` into Python values.
"""

from __future__ import annotations

import math
import re

_OPEN = "[({\"'"
_CLOSE = {"[": "]", "(": ")", "{": "}", '"': '"', "'": "'"}

# Length units accepted by OpenDSS ``units=`` and their size in metres.
LENGTH_UNITS_M: dict[str, float] = {
    "none": 1.0,
    "mi": 1609.344,
    "kft": 304.8,
    "km": 1000.0,
    "m": 1.0,
    "meter": 1.0,
    "ft": 0.3048,
    "in": 0.0254,
    "cm": 0.01,
    "mm": 0.001,
}

_TRUE = {"y", "yes", "t", "true", "1"}

_RPN_BINARY = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "^": lambda a, b: a**b,
    "atan2": math.atan2,
}
_RPN_UNARY = {
    "sqr": lambda a: a * a,
    "sqrt": math.sqrt,
    "inv": lambda a: 1.0 / a,
    "ln": math.log,
    "exp": math.exp,
    "log10": math.log10,
    "sin": lambda a: math.sin(math.radians(a)),
    "cos": lambda a: math.cos(math.radians(a)),
    "tan": lambda a: math.tan(math.radians(a)),
    "asin": lambda a: math.degrees(math.asin(a)),
    "acos": lambda a: math.degrees(math.acos(a)),
    "atan": lambda a: math.degrees(math.atan(a)),
    "chs": lambda a: -a,
}


class DssValueError(ValueError):
    """A property value that cannot be interpreted."""


def strip_delims(text: str) -> str:
    """Remove one pair of enclosing array/quote delimiters, if present."""
    s = text.strip()
    if len(s) >= 2 and s[0] in _OPEN and s[-1] == _CLOSE[s[0]]:
        return s[1:-1].strip()
    return s


def eval_rpn(expr: str) -> float:
    """Evaluate an OpenDSS reverse-Polish expression like ``8 1000 /``."""
    stack: list[float] = []
    for tok in expr.replace(",", " ").split():
        low = tok.lower()
        if low in _RPN_BINARY:
            if len(stack) < 2:
                raise DssValueError(f"RPN stack underflow in '{expr}'")
            b = stack.pop()
            a = stack.pop()
            stack.append(_RPN_BINARY[low](a, b))
        elif low in _RPN_UNARY:
            if not stack:
                raise DssValueError(f"RPN stack underflow in '{expr}'")
            stack.append(_RPN_UNARY[low](stack.pop()))
        elif low == "pi":
            stack.append(math.pi)
        elif low in ("swap", "xy"):
            if len(stack) < 2:
                raise DssValueError(f"RPN stack underflow in '{expr}'")
            stack[-1], stack[-2] = stack[-2], stack[-1]
        else:
            try:
                stack.append(float(tok))
            except ValueError as exc:
                raise DssValueError(f"bad RPN token '{tok}' in '{expr}'") from exc
    if not stack:
        raise DssValueError(f"empty RPN expression '{expr}'")
    return stack[-1]


def to_float(text: str) -> float:
    """Parse a numeric property (plain number or a delimited RPN string)."""
    s = strip_delims(text)
    try:
        return float(s)
    except ValueError:
        return eval_rpn(s)


def to_int(text: str) -> int:
    """Parse an integer property."""
    return int(round(to_float(text)))


def to_bool(text: str) -> bool:
    """Parse a boolean property (``y``/``yes``/``true``/``t``/``1``)."""
    return strip_delims(text).lower() in _TRUE


def split_items(text: str) -> list[str]:
    """Split an array value into its raw items (``|`` separators dropped)."""
    s = strip_delims(text)
    return [t for t in re.split(r"[\s,|]+", s) if t]


def to_floats(text: str) -> list[float]:
    """Parse a numeric array property."""
    out: list[float] = []
    for item in split_items(text):
        try:
            out.append(float(item))
        except ValueError as exc:
            raise DssValueError(f"non-numeric array item '{item}'") from exc
    return out


def to_matrix(text: str, n: int) -> list[list[float]]:
    """Parse an ``n x n`` symmetric matrix (lower triangle or full).

    Rows may be separated by ``|``; a value list of ``n(n+1)/2`` items
    is read as the lower triangle by rows, ``n*n`` items as the full
    matrix.
    """
    vals = to_floats(text)
    mat = [[0.0] * n for _ in range(n)]
    if len(vals) == n * (n + 1) // 2:
        k = 0
        for i in range(n):
            for j in range(i + 1):
                mat[i][j] = mat[j][i] = vals[k]
                k += 1
        return mat
    if len(vals) == n * n:
        for i in range(n):
            for j in range(n):
                mat[i][j] = vals[i * n + j]
        return mat
    raise DssValueError(f"matrix '{text}' does not fit {n}x{n}")


def parse_bus(spec: str) -> tuple[str, list[int]]:
    """Split ``bus.1.2.3`` into the lower-case bus name and its nodes."""
    s = strip_delims(spec)
    parts = s.split(".")
    name = parts[0].strip().lower()
    nodes: list[int] = []
    for p in parts[1:]:
        p = p.strip()
        if p:
            try:
                nodes.append(int(p))
            except ValueError as exc:
                raise DssValueError(f"bad node '{p}' in bus '{spec}'") from exc
    return name, nodes


def length_factor_m(unit: str) -> float:
    """Metres per length unit (``none`` counts as 1)."""
    key = unit.strip().lower()
    if key not in LENGTH_UNITS_M:
        raise DssValueError(f"unknown length unit '{unit}'")
    return LENGTH_UNITS_M[key]
