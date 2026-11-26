#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Lint LaTeX math in Markdown files the way GitHub renders it.

Math is extracted with a real CommonMark parser (markdown-it-py + the
``dollarmath`` plugin of mdit-py-plugins), so code spans / fences,
escaped ``\\$``, currency such as ``$20`` or ``$/MWh`` and ``[$]`` table
headers are never mistaken for math.  Recognised forms: ``$...$``,
``$$...$$`` (block or inline), ``$`...`$`` and fenced ```` ```math ````
blocks.

Every span is then typeset with MathJax 3 (the engine GitHub uses), in
one batched Node process (``tools/latex_lint/``, ``npm ci`` there), and
TeX errors -- undefined control sequences, unbalanced braces,
``\\left``/``\\right`` mismatches, missing arguments -- are reported.

GitHub hands MathJax the math *after* Markdown processing, which this
lint reproduces.  Each rule below was verified against GitHub's own
renderer (``gh api markdown -f mode=gfm``):

``tex-error``
    MathJax cannot typeset the span as GitHub delivers it.  GitHub
    strips the backslash of Markdown escapes inside ``$``/``$$`` math
    (``\\{`` -> ``{``, ``\\%`` -> ``%``, mid-line ``\\\\`` -> ``\\``), so
    ``\\left\\{`` fails there although it is valid TeX.
``display-line-start``
    A line inside a ``$$`` block starts with a Markdown block marker
    (``+``, ``-``, ``*``, ``>``, ``#``, ``1.``, ``=``): GitHub turns it
    into a list / quote / heading and the formula falls apart.
    ``+c`` / ``-d`` without a following space are safe.
``display-in-list``
    A multi-line ``$$`` block inside a list item: GitHub shows it as
    plain text; a one-line ``$$...$$`` renders (a ```` ```math ````
    fence there is not always typeset server-side).
``text-escape``
    ``\\_`` ``\\%`` ``\\&`` ``\\#`` inside ``\\text{}`` of verbatim math:
    MathJax prints the backslash (GitHub loads no ``textmacros``).
``wrapped-inline-math``
    Inline ``$...$`` broken over two source lines: GitHub does not
    render it as math.
``emphasis-in-math``
    ``*`` / ``_`` inside inline math pair up as Markdown emphasis on
    GitHub (e.g. ``$x^*$ and $y^*$``).
``currency-pair``
    Two unescaped ``$`` on one text line that GitHub pairs as inline
    math (e.g. ``$20 and $/MWh``).  Escape currency as ``\\$``.
``github-escape``
    ``\\,`` ``\\;`` ``\\:`` ``\\!`` ``\\>`` ``\\{`` ``\\}`` ``\\|`` or a
    mid-line ``\\\\`` inside ``$``/``$$`` math: GitHub drops the
    backslash, so it renders a literal ``,``/``;``/brace-less group
    instead of the spacing/delimiter (no TeX error, still wrong).  Fix:
    ``$`...`$`` inline / a ```` ```math ```` fence for display math
    (GitHub keeps both verbatim, and they stay plain TeX), or
    ``\\lbrace`` ``\\rbrace`` ``\\Vert`` when only delimiters are
    involved.  ``--github-escapes=warn`` reduces it to one count per
    file, ``off`` disables it.

Exit code 0 when no issues are found, 1 otherwise (2 with
``--strict-deps`` when a dependency is missing).  Without Node or the
npm packages the structural checks still run (one warning); without
markdown-it-py / mdit-py-plugins the lint is skipped with a warning.

Usage::

    python tools/check_latex_math.py [FILE_OR_DIR ...]
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from markdown_it import MarkdownIt
    from markdown_it.token import Token

_TOOL_DIR = Path(__file__).resolve().parent / "latex_lint"
_NODE_SCRIPT = _TOOL_DIR / "mathjax_check.mjs"
_NODE_MODULE = _TOOL_DIR / "node_modules" / "mathjax-full"

_SKIP_DIRS = {"node_modules", "build", ".git", "__pycache__", "cpm_modules"}

# CommonMark: any ASCII punctuation character can be backslash-escaped.
_ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
# Escapes whose loss changes the rendering without a TeX error.
_SILENT_ESCAPES = frozenset(",;:!>{}|\\")

# A Markdown block marker at the start of a line inside a $$ block.
_BLOCK_MARKER = re.compile(r"^(?:[-+*](?:\s|$)|>|#{1,6}(?:\s|$)|1[.)](?:\s|$)|=+\s*$)")
# \text{a\_b} renders a literal backslash in MathJax without textmacros.
_TEXT_ESCAPE = re.compile(
    r"\\(?:text|textrm|textsf|texttt|textit|textbf|mbox)\s*\{[^{}]*\\[_%&#$]"
)


@dataclass
class MathSpan:
    """One math expression of a Markdown file."""

    line: int  # 1-based source line of the span start
    tex: str  # TeX as written in the source
    display: bool
    verbatim: bool  # $`...`$ or ```math: GitHub keeps the TeX unchanged

    @property
    def rendered(self) -> str:
        """TeX as GitHub hands it to MathJax."""
        return self.tex if self.verbatim else github_view(self.tex)


@dataclass
class FileReport:
    """Spans and structural findings of one file."""

    path: Path
    spans: list[MathSpan] = field(default_factory=list)
    findings: list[tuple[int, str, str]] = field(default_factory=list)
    silent: list[tuple[int, list[str]]] = field(default_factory=list)

    def add(self, line: int, check: str, message: str) -> None:
        """Record a finding at a 1-based *line*."""
        self.findings.append((line, check, message))


# ---------------------------------------------------------------------------
# GitHub's view of the math
# ---------------------------------------------------------------------------


def _escapes(tex: str) -> Iterator[tuple[int, str]]:
    """Yield (pos, char) for every Markdown escape GitHub consumes."""
    i = 0
    while i < len(tex) - 1:
        if tex[i] != "\\":
            i += 1
            continue
        nxt = tex[i + 1]
        # "\\" right before a newline survives (verified on GitHub).
        if nxt == "\\" and (i + 2 == len(tex) or tex[i + 2] == "\n"):
            i += 2
            continue
        if nxt in _ASCII_PUNCT:
            yield i, nxt
        i += 2


def github_view(tex: str) -> str:
    """Return *tex* after GitHub's Markdown backslash-escape processing."""
    out: list[str] = []
    last = 0
    for pos, _ in _escapes(tex):
        out.append(tex[last:pos])
        last = pos + 1
    out.append(tex[last:])
    return "".join(out)


def silent_escapes(tex: str) -> list[str]:
    """Return the ``\\x`` escapes of *tex* GitHub drops without an error."""
    return [f"\\{c}" for _, c in _escapes(tex) if c in _SILENT_ESCAPES]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _make_parsers() -> tuple[MarkdownIt, MarkdownIt]:
    """Return (math-aware parser, plain GFM-like parser)."""
    # pylint: disable=import-outside-toplevel
    from markdown_it import MarkdownIt
    from mdit_py_plugins.dollarmath import dollarmath_plugin

    math_md = (
        MarkdownIt("commonmark")
        .enable(["table", "strikethrough"])
        .use(
            dollarmath_plugin,
            allow_space=False,
            allow_digits=False,
            double_inline=True,
            allow_blank_lines=True,
        )
    )
    plain_md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    return math_md, plain_md


_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_ESCAPED_DOLLAR = re.compile(r"(?<!\\)(?:\\\\)*\\\$")


def _blank_str(s: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in s)


def _blank(match: re.Match[str]) -> str:
    return _blank_str(match.group())


def _snippet(s: str, width: int = 70) -> str:
    s = " ".join(s.split())
    return s if len(s) <= width else s[: width - 3] + "..."


def _is_space(s: str, i: int) -> bool:
    return i < 0 or i >= len(s) or s[i].isspace()


def _currency_pairs(text: str) -> list[int]:
    """Return 0-based line offsets where GitHub pairs stray ``$`` as math.

    *text* holds only the ``$`` that are not math, code or escaped.
    GitHub's rule, as observed on its renderer: an opener ``$`` is not
    preceded by a letter/digit nor followed by whitespace; its closer is
    the next ``$`` on the line, which must not be followed by a
    letter/digit nor have whitespace on both sides.  So ``$20 and
    $/MWh`` and ``($/m3 or $/MWh)`` become math, ``$5 and $10``,
    ``6701 $MM ... 5058 $MM`` and ``$ x $`` do not.
    """
    hits: list[int] = []
    for off, line in enumerate(text.split("\n")):
        pos = [i for i, c in enumerate(line) if c == "$"]
        for o, c in itertools.pairwise(pos):
            opener = not _is_space(line, o + 1) and not (
                o > 0 and line[o - 1].isalnum()
            )
            closer = not line[c + 1 : c + 2].isalnum() and not (
                _is_space(line, c - 1) and _is_space(line, c + 1)
            )
            if opener and closer:
                hits.append(off)
                break
    return hits


def _count_emphasis(tokens: Sequence[Token]) -> int:
    return sum(1 for t in tokens if t.type in ("em_open", "strong_open"))


def _check_inline(
    report: FileReport, tok: Token, line0: int, plain_md: MarkdownIt
) -> None:
    """Collect the math of one inline token and run the inline checks."""
    raw = tok.content
    children = tok.children or []
    masked = raw
    cursor = 0
    has_emph_char = False
    for child in children:
        if child.type not in ("math_inline", "math_inline_double"):
            continue
        needle = f"{child.markup}{child.content}{child.markup}"
        pos = masked.find(needle, cursor)
        line = line0
        if pos >= 0:
            line += raw.count("\n", 0, pos)
            masked = masked[:pos] + _blank_str(needle) + masked[pos + len(needle) :]
            cursor = pos + len(needle)
        content = child.content
        verbatim = len(content) >= 2 and content[0] == "`" and content[-1] == "`"
        if "\n" in content:
            # GitHub never pairs '$' across lines: plain text there, so
            # it is not typeset either.
            report.add(
                line,
                "wrapped-inline-math",
                "'$...$' spans two source lines; GitHub shows it as plain "
                "text -- keep math on one line (escape currency as \\$)  "
                f"[{_snippet(content)}]",
            )
            continue
        report.spans.append(
            MathSpan(
                line=line,
                tex=content[1:-1] if verbatim else content,
                display=child.type == "math_inline_double",
                verbatim=verbatim,
            )
        )
        if not verbatim and re.search(r"(?<!\\)[*_]", content):
            has_emph_char = True

    if has_emph_char:
        plain = plain_md.parseInline(raw)
        if plain and _count_emphasis(plain[0].children or []) > _count_emphasis(
            children
        ):
            report.add(
                line0,
                "emphasis-in-math",
                "'*'/'_' inside inline math pair up as Markdown emphasis on "
                f"GitHub; write \\ast, \\* or $`...`$  [{_snippet(raw)}]",
            )

    masked = _CODE_SPAN.sub(_blank, masked)
    masked = _ESCAPED_DOLLAR.sub(_blank, masked)
    raw_lines = raw.split("\n")
    for off in _currency_pairs(masked):
        report.add(
            line0 + off,
            "currency-pair",
            "two unescaped '$' on one line: GitHub typesets the text between "
            f"them as math; escape currency as \\$  [{_snippet(raw_lines[off])}]",
        )


def _check_display(report: FileReport, tok: Token, line0: int) -> None:
    report.spans.append(MathSpan(line0, tok.content, display=True, verbatim=False))
    for off, ln in enumerate(tok.content.split("\n")):
        if off and _BLOCK_MARKER.match(ln.strip()):  # off 0: on the $$ line
            report.add(
                line0 + off,
                "display-line-start",
                "line in $$ block starts with a Markdown block marker; GitHub "
                "breaks the formula there -- move the operator to the "
                f"previous line or prefix {{}}  [{_snippet(ln)}]",
            )


def extract(
    text: str, path: Path, parsers: tuple[MarkdownIt, MarkdownIt]
) -> FileReport:
    """Parse *text* and return its math spans plus structural findings."""
    math_md, plain_md = parsers
    report = FileReport(path)
    line0 = 1
    list_depth = 0
    for tok in math_md.parse(text):
        if tok.map is not None:
            line0 = tok.map[0] + 1
        if tok.type in ("bullet_list_open", "ordered_list_open"):
            list_depth += 1
        elif tok.type in ("bullet_list_close", "ordered_list_close"):
            list_depth -= 1
        elif tok.type in ("math_block", "math_block_label"):
            _check_display(report, tok, line0)
            if list_depth and tok.map and tok.map[1] - tok.map[0] > 1:
                report.add(
                    line0,
                    "display-in-list",
                    "multi-line $$ block inside a list item: GitHub shows it "
                    "as plain text -- put it on one line ($$...$$)",
                )
        elif tok.type == "fence" and tok.info.strip() == "math":
            report.spans.append(MathSpan(line0 + 1, tok.content, True, True))
        elif tok.type == "inline":
            _check_inline(report, tok, line0, plain_md)
    for span in report.spans:
        if not span.verbatim and (esc := silent_escapes(span.tex)):
            report.silent.append((span.line, esc))
        if m := _TEXT_ESCAPE.search(span.rendered):
            report.add(
                span.line,
                "text-escape",
                "MathJax prints \\_ \\% \\& \\# literally inside \\text{} (no "
                f"textmacros on GitHub): write the bare character  [{m.group()}]",
            )
    return report


# ---------------------------------------------------------------------------
# MathJax
# ---------------------------------------------------------------------------


def mathjax_available() -> str | None:
    """Return the reason MathJax validation is unavailable, or None."""
    if shutil.which("node") is None:
        return "node not found on PATH"
    if not _NODE_MODULE.is_dir():
        return f"npm packages missing (run: npm ci --prefix {_TOOL_DIR})"
    return None


def run_mathjax(
    reports: Sequence[FileReport],
) -> dict[tuple[int, int], str] | str:
    """Typeset every span; return {(report, span): error} or a failure reason."""
    items: list[dict[str, Any]] = []
    index: list[tuple[int, int]] = []
    for r_i, rep in enumerate(reports):
        for s_i, span in enumerate(rep.spans):
            items.append(
                {
                    "id": len(index),
                    "doc": str(rep.path),
                    "tex": span.rendered,
                    "display": span.display,
                }
            )
            index.append((r_i, s_i))
    if not items:
        return {}
    proc = subprocess.run(
        [shutil.which("node") or "node", str(_NODE_SCRIPT)],
        input=json.dumps(items),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return f"node exited {proc.returncode}: {proc.stderr.strip()[:300]}"
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as err:
        return f"bad output from node: {err}"
    return {index[int(e["id"])]: str(e["error"]) for e in result}


def _tex_error_message(span: MathSpan, error: str) -> str:
    msg = f"TeX error: {error}"
    if not span.verbatim and span.rendered != span.tex:
        msg += (
            " (as GitHub renders it, after stripping Markdown backslash "
            "escapes: use \\lbrace/\\rbrace, \\Vert, $`...`$ or ```math)"
        )
    return f"{msg}  [{_snippet(span.tex)}]"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _collect_md_files(paths: list[str]) -> list[Path]:
    """Expand arguments into a sorted list of Markdown files."""
    result: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_file() and pp.suffix == ".md":
            result.append(pp)
        elif pp.is_dir():
            for md in sorted(pp.rglob("*.md")):
                if any(part in _SKIP_DIRS or part.startswith(".") for part in md.parts):
                    continue
                result.append(md)
    return sorted(set(result))


def check_files(
    files: Sequence[Path],
    *,
    use_mathjax: bool = True,
    github_escapes: str = "error",
) -> tuple[list[str], list[str]]:
    """Return (error diagnostics, warnings) for *files*."""
    parsers = _make_parsers()
    reports = [
        extract(f.read_text(encoding="utf-8"), f, parsers) for f in sorted(files)
    ]
    warnings: list[str] = []
    if use_mathjax:
        reason = mathjax_available()
        outcome = run_mathjax(reports) if reason is None else reason
        if isinstance(outcome, str):
            warnings.append(
                f"warning: MathJax validation skipped ({outcome}); "
                "only structural checks ran"
            )
        else:
            for (r_i, s_i), err in outcome.items():
                span = reports[r_i].spans[s_i]
                reports[r_i].add(span.line, "tex-error", _tex_error_message(span, err))

    diags: list[str] = []
    for rep in reports:
        if github_escapes == "error":
            for line, esc in rep.silent:
                rep.add(
                    line,
                    "github-escape",
                    f"GitHub drops the backslash of {' '.join(sorted(set(esc)))}"
                    " in $-math; use $`...`$ / a ```math fence, or "
                    "\\lbrace \\rbrace \\Vert",
                )
        elif github_escapes == "warn" and rep.silent:
            warnings.append(
                f"warning: {rep.path}: {len(rep.silent)} math span(s) use "
                "\\, \\; \\{ ... that GitHub renders without the backslash "
                "(--github-escapes=error lists them)"
            )
        diags.extend(
            f"{rep.path}:{line}: [{check}] {msg}"
            for line, check, msg in sorted(rep.findings)
        )
    return diags, warnings


def check_file(filepath: Path, *, use_mathjax: bool = True) -> list[str]:
    """Return the error diagnostics of one file (github-escape off)."""
    return check_files([filepath], use_mathjax=use_mathjax, github_escapes="off")[0]


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Lint LaTeX math in Markdown files as GitHub renders it."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Files or directories to check (default: current directory)",
    )
    parser.add_argument(
        "--no-mathjax",
        action="store_true",
        help="structural checks only (skip the MathJax typesetting)",
    )
    parser.add_argument(
        "--github-escapes",
        choices=("error", "warn", "off"),
        default="error",
        help="report of \\, \\; \\{ ... that GitHub drops (default: error; "
        "warn = one count per file)",
    )
    parser.add_argument(
        "--strict-deps",
        action="store_true",
        help="exit 2 instead of degrading when markdown-it-py, "
        "mdit-py-plugins, node or the npm packages are missing (CI)",
    )
    args = parser.parse_args(argv)

    try:
        _make_parsers()
    except ImportError as err:
        print(
            f"warning: LaTeX math lint skipped ({err}); "
            "pip install markdown-it-py mdit-py-plugins",
            file=sys.stderr,
        )
        return 2 if args.strict_deps else 0
    if args.strict_deps and not args.no_mathjax and (why := mathjax_available()):
        print(f"error: MathJax unavailable: {why}", file=sys.stderr)
        return 2

    files = _collect_md_files(args.paths)
    if not files:
        print("No Markdown files found.", file=sys.stderr)
        return 0

    diags, warnings = check_files(
        files,
        use_mathjax=not args.no_mathjax,
        github_escapes=args.github_escapes,
    )
    for w in warnings:
        print(w, file=sys.stderr)
    if args.strict_deps and any("MathJax validation skipped" in w for w in warnings):
        return 2
    for d in diags:
        print(d)
    if diags:
        print(f"\n{len(diags)} issue(s) found.", file=sys.stderr)
        return 1
    print(f"Checked {len(files)} file(s) -- no issues found.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
