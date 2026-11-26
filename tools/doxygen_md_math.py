#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Doxygen input filter: GitHub-style Markdown math -> Doxygen formulas.

Doxygen does not know ``$...$`` math, so the Markdown pages it
publishes showed formulas as raw text or code.  This filter (wired in
``documentation/Doxyfile.in`` as ``FILTER_PATTERNS = *.md=...``)
rewrites every math span found by the same parser as
``tools/check_latex_math.py`` into Doxygen's formula commands:

- ``$x$`` / ``$`x`$``            -> ``\\f$x\\f$``
- one-line ``$$x$$`` in text     -> ``\\f$\\displaystyle x\\f$``
- ``$$`` blocks, ```` ```math ```` fences -> ``\\f[ ... \\f]``

The TeX is the one GitHub typesets: Markdown backslash escapes are
applied to ``$``/``$$`` math (``\\_`` -> ``_``), ``$`...`$`` and
```` ```math ```` stay verbatim.  Inside table rows a ``|`` of the
formula becomes ``\\vert`` (Doxygen would split the cell there).
Code spans, fenced code and everything else pass through unchanged,
and the line count is preserved, so Doxygen's warnings keep pointing
at the right source line.

If markdown-it-py / mdit-py-plugins are missing, the file is passed
through unchanged with a warning on stderr; the docs build never
breaks because of this filter.

Usage (what Doxygen runs)::

    python3 tools/doxygen_md_math.py FILE.md > filtered.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_latex_math as clm  # pylint: disable=wrong-import-position

if TYPE_CHECKING:
    from markdown_it import MarkdownIt
    from markdown_it.token import Token

_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_FENCE = re.compile(r"^(\s*(?:>\s*)*)(`{3,}|~{3,})\s*math\s*$")


def _formula(tex: str, *, in_table: bool) -> str:
    """Return *tex* made safe inside a Doxygen formula (one line)."""
    tex = " ".join(tex.split())
    if in_table:
        tex = re.sub(r"(?<!\\)\|", r"\\vert ", tex)
    return tex


_PROTECTED = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)|\\f\$.*?\\f\$")


def _quote_entities(line: str) -> str:
    """Write ``"`` as ``&quot;`` outside code spans and formulas."""
    out, last = [], 0
    for m in _PROTECTED.finditer(line):
        out += [line[last : m.start()].replace('"', "&quot;"), m.group()]
        last = m.end()
    out.append(line[last:].replace('"', "&quot;"))
    return "".join(out)


def _replace_inline(line: str, needle: str, new: str, start: int) -> tuple[str, int]:
    """Replace the first *needle* at or after *start* outside code spans."""
    code = [m.span() for m in _CODE_SPAN.finditer(line)]
    pos = line.find(needle, start)
    while pos >= 0 and any(a <= pos < b for a, b in code):
        pos = line.find(needle, pos + 1)
    if pos < 0:
        return line, start
    return line[:pos] + new + line[pos + len(needle) :], pos + len(new)


def _inline_spans(tok: Token, lines: list[str], line0: int, *, in_table: bool) -> None:
    raw = tok.content
    cursor = 0
    starts: dict[int, int] = {}
    for child in tok.children or []:
        if child.type not in ("math_inline", "math_inline_double"):
            continue
        content = child.content
        needle = f"{child.markup}{content}{child.markup}"
        pos = raw.find(needle, cursor)
        if pos < 0 or "\n" in content:
            continue  # wrapped: GitHub shows it as text, so do we
        cursor = pos + len(needle)
        verbatim = len(content) >= 2 and content[0] == "`" and content[-1] == "`"
        tex = content[1:-1] if verbatim else clm.github_view(content)
        tex = _formula(tex, in_table=in_table)
        if child.type == "math_inline_double":
            tex = f"\\displaystyle {tex}"
        ln = line0 + raw.count("\n", 0, pos)
        if in_table and needle not in lines[ln]:
            needle = needle.replace("|", "\\|")  # the table parser ate \|
        lines[ln], starts[ln] = _replace_inline(
            lines[ln], needle, f"\\f${tex}\\f$", starts.get(ln, 0)
        )
    if starts:
        # Doxygen does not expand commands between double quotes: a
        # formula inside "..." came out as a raw "\_form#N".
        for ln in range(line0, line0 + raw.count("\n") + 1):
            lines[ln] = _quote_entities(lines[ln])


def _display_block(tok: Token, lines: list[str]) -> None:
    """Rewrite a ``$$`` block in place: ``\\f[`` ... ``\\f]``."""
    assert tok.map is not None
    first, last = tok.map[0], tok.map[1] - 1
    head = lines[first]
    i = head.index("$$")
    if first == last:
        j = head.rindex("$$")
        body = clm.github_view(head[i + 2 : j])
        lines[first] = f"{head[:i]}\\f[{body}\\f]{head[j + 2 :]}"
        return
    lines[first] = f"{head[:i]}\\f[{clm.github_view(head[i + 2 :])}"
    for k in range(first + 1, last):
        lines[k] = clm.github_view(lines[k])
    tail = lines[last]
    j = tail.rindex("$$")
    lines[last] = f"{clm.github_view(tail[:j])}\\f]{tail[j + 2 :]}"


def _math_fence(tok: Token, lines: list[str]) -> None:
    """Rewrite a ```` ```math ```` fence in place (content verbatim)."""
    assert tok.map is not None
    first, last = tok.map[0], tok.map[1] - 1
    m = _FENCE.match(lines[first])
    if m is None:
        return
    lines[first] = f"{m.group(1)}\\f["
    if last > first and lines[last].strip().lstrip(">").strip().startswith(
        m.group(2)[0] * 3
    ):
        prefix = lines[last][: len(lines[last]) - len(lines[last].lstrip(" >"))]
        lines[last] = f"{prefix}\\f]"


def convert(text: str, parsers: tuple[MarkdownIt, MarkdownIt] | None = None) -> str:
    """Return *text* with its math rewritten as Doxygen formulas."""
    math_md, _ = parsers or clm._make_parsers()  # pylint: disable=protected-access
    lines = text.split("\n")
    line0 = 0
    in_table = False
    for tok in math_md.parse(text):
        if tok.map is not None:
            line0 = tok.map[0]
        if tok.type == "table_open":
            in_table = True
        elif tok.type == "table_close":
            in_table = False
        elif tok.type in ("math_block", "math_block_label"):
            _display_block(tok, lines)
        elif tok.type == "fence" and tok.info.strip() == "math":
            _math_fence(tok, lines)
        elif tok.type == "inline":
            _inline_spans(tok, lines, line0, in_table=in_table)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Filter the file named in *argv* to stdout (Doxygen INPUT_FILTER)."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: doxygen_md_math.py FILE.md", file=sys.stderr)
        return 2
    text = Path(args[0]).read_text(encoding="utf-8")
    try:
        parsers = clm._make_parsers()  # pylint: disable=protected-access
    except ImportError as err:
        print(
            f"warning: {args[0]}: math left as is ({err}); "
            "pip install markdown-it-py mdit-py-plugins",
            file=sys.stderr,
        )
        sys.stdout.write(text)
        return 0
    sys.stdout.write(convert(text, parsers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
