DOXYFILE = "Doxyfile"

THEME_COLOR = "#22272e"
SHOW_UNDOCUMENTED = True

LINKS_NAVBAR1 = [
    (None, "pages", [(None, "about")]),
    (None, "namespaces", []),
    (None, "annotated", []),
]

LINKS_NAVBAR2 = [
    (None, "files", []),
    ('<a href="https://github.com/gtopt-community/gtopt">GitHub</a>', []),
]

# ---- Math: MathJax 3 in the browser instead of m.css's LaTeX -> SVG ----
#
# tools/doxygen_md_math.py (FILTER_PATTERNS in the Doxyfile) turns the
# GitHub-style $...$ / $$...$$ / ```math of the Markdown pages into
# Doxygen \f$ / \f[ formulas.  m.css would render those with LaTeX +
# dvisvgm and the newtx fonts (a full TeX Live in CI); instead its two
# latex2svgextra hooks are replaced so each formula is emitted as
# MathJax-delimited TeX, typeset in the browser by MathJax 3 -- the
# engine GitHub uses, so both renderings agree.  conf.py runs inside
# m.css's doxygen.py process, so patching the module here is enough.
import html as _html

import latex2svgextra as _l2s  # m.css plugins/ is on sys.path


def _mathjax_fetch(formula):
    return 0.0, formula


def _mathjax_patch(formula, _svg, depth, attribs):
    tex = formula.strip()
    if depth is None:  # block: Doxygen hands "\[...\]" or "\begin{env}..."
        return _html.escape(tex)
    if tex.startswith("$") and tex.endswith("$"):
        tex = tex[1:-1]
    return f"<span{attribs}>\\({_html.escape(tex)}\\)</span>"


_l2s.fetch_cached_or_render = _mathjax_fetch
_l2s.patch = _mathjax_patch

# Only the m-math elements are typeset (the rest of the page, code
# listings included, is ignored).  Pinned MathJax 3.2.2 + SRI.
HTML_HEADER = """
<script>
window.MathJax = {
  tex: {inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']]},
  options: {ignoreHtmlClass: 'm-container', processHtmlClass: 'm-math'}
};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js"
  integrity="sha384-AHAnt9ZhGeHIrydA1Kp1L7FN+2UosbF7RQg6C+9Is/a7kDpQ1684C2iH2VWil6r4"
  crossorigin="anonymous"></script>
"""
