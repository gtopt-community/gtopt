"""HTML-escaping contract for ``guiservice/static/js/app.js``.

Case files are user-supplied (uploaded ZIPs, shared cases), so every
case name/value interpolated into an ``innerHTML`` template must be
escaped.  Two layers:

* a static scan: every ``${...}`` inside an HTML template in app.js is
  either wrapped in ``escapeHtml``/``jsAttrString``, a loop index, or a
  ternary between string literals;
* a behavioural check run under Node (skipped when ``node`` is absent):
  the real render functions are extracted from app.js, fed hostile case
  data through a minimal DOM stub, and the produced markup must contain
  no live tag or attribute break-out.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"

# A value that breaks out of a quoted attribute and opens a tag if it is
# interpolated raw.
HOSTILE = "x\"'><i id=injected>y</i>"

_SAFE_INTERPOLATION = re.compile(
    r"""\$\{\s*(?:
        escapeHtml\(.*\)              # escaped value
      | jsAttrString\(.*\)            # JS string literal in an attribute
      | i                             # loop index
      | [^{}?]+\?\s*"[^"]*"\s*:\s*"[^"]*"  # ternary between literals
    )\s*\}$""",
    re.VERBOSE,
)


def _read_app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _interpolations(line: str) -> list[str]:
    """Return the ``${...}`` expressions of *line* (balanced braces)."""
    out = []
    pos = 0
    while (start := line.find("${", pos)) != -1:
        depth = 0
        for j in range(start + 1, len(line)):
            if line[j] == "{":
                depth += 1
            elif line[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(line[start : j + 1])
                    pos = j + 1
                    break
        else:
            break
    return out


def test_every_html_interpolation_is_escaped():
    """No raw ``${...}`` inside markup-producing template literals."""
    offenders = []
    for lineno, line in enumerate(_read_app_js().splitlines(), start=1):
        if "${" not in line or not re.search(r"<\w|=\"", line):
            continue
        if "querySelector" in line or line.lstrip().startswith(("*", "//")):
            continue
        for expr in _interpolations(line):
            if not _SAFE_INTERPOLATION.match(expr):
                offenders.append(f"{lineno}: {expr}")
    assert not offenders, "unescaped HTML interpolations:\n" + "\n".join(offenders)


def _extract(source: str, header: str) -> str:
    """Return the top-level declaration starting with *header*."""
    start = source.index(header)
    depth = 0
    for j in range(source.index("{", start), len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start : j + 1] + ";\n"
    raise AssertionError(f"unbalanced declaration: {header}")


_NODE_HARNESS = r"""
const created = [];
function makeEl() {
  const el = { innerHTML: "", className: "", children: [],
               appendChild(c) { this.children.push(c); } };
  created.push(el);
  return el;
}
const nodes = {};
const document = {
  createElement: () => makeEl(),
  querySelector: (sel) => (nodes[sel] ??= makeEl()),
  getElementById: (id) => (nodes["#" + id] ??= makeEl()),
};
const HOSTILE = process.argv[2];
let currentElementType = "bus_array";
const schemas = { bus_array: { label: HOSTILE, fields: [
  { name: "uid", type: "integer" },
  { name: "name", type: "string" },
  { name: "voltage", type: "number" },
  { name: "lmax", type: "number_or_file" },
  { name: "flag", type: "boolean" },
] } };
const row = { uid: HOSTILE, name: HOSTILE, duration: HOSTILE,
  first_block: HOSTILE, count_block: HOSTILE, discount_factor: HOSTILE,
  probability_factor: HOSTILE };
const caseData = {
  simulation: { block_array: [row], stage_array: [row], scenario_array: [row] },
  system: { bus_array: [{ uid: HOSTILE, name: HOSTILE, voltage: HOSTILE,
                          lmax: HOSTILE, flag: true }] },
};
__FUNCTIONS__
renderBlocksTable();
renderStagesTable();
renderScenariosTable();
renderElements();
renderMonitorStats({ _solver_type: "sddp", status: HOSTILE,
  iteration: HOSTILE, max_iterations: HOSTILE, gap: 0.1, history: [] });
renderMonitorStats({ status: HOSTILE, scenes_done: HOSTILE,
  total_scenes: HOSTILE });
process.stdout.write(JSON.stringify({
  html: created.map((e) => e.innerHTML).filter((h) => h),
  escaped: escapeHtml(HOSTILE),
  js: jsAttrString(HOSTILE),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_render_functions_escape_hostile_case_data(tmp_path):
    """Hostile names/values from a case never become live markup."""
    source = _read_app_js()
    functions = "".join(
        _extract(source, header)
        for header in (
            "const _HTML_ESCAPES",
            "function escapeHtml(",
            "function jsAttrString(",
            "function getElements(",
            "function renderBlocksTable(",
            "function renderStagesTable(",
            "function renderScenariosTable(",
            "function renderElements(",
            "function renderMonitorStats(",
        )
    )
    script = tmp_path / "harness.js"
    script.write_text(_NODE_HARNESS.replace("__FUNCTIONS__", functions))
    proc = subprocess.run(
        ["node", str(script), HOSTILE],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout)

    assert result["escaped"] == "x&quot;&#39;&gt;&lt;i id=injected&gt;y&lt;/i&gt;"
    assert result["js"] == "&quot;x\\&quot;&#39;&gt;&lt;i id=injected&gt;y&lt;/i&gt;&quot;"

    html = "\n".join(result["html"])
    # Every render path above ran and produced markup.
    assert html.count("x&quot;&#39;&gt;&lt;i id=injected") >= 10
    # ... but the hostile value never opens a tag or closes an attribute.
    assert "<i id=injected" not in html
    assert "\"'>" not in html
