"""Diagram outputs must be self-contained (no absolute local file paths).

Graphviz copies ``<IMG SRC>`` icon paths verbatim into SVG output; those
pointed at the local icon cache under the temp dir, so the SVG broke on any
other machine and leaked a local path.  Icons are now inlined as ``data:``
URIs and DOT source no longer references the cache.
"""

import re
import shutil
import tempfile
from pathlib import Path

import pytest

from gtopt_diagram import gtopt_diagram as gd
from gtopt_diagram._renderers import embed_svg_images

_PLANNING = {
    "system": {
        "name": "portable",
        "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
        "generator_array": [
            {"uid": 1, "name": "g1", "bus": 1, "pmax": 100, "gcost": 10},
        ],
        "demand_array": [{"uid": 1, "name": "d1", "bus": 2, "lmax": 50}],
        "line_array": [
            {"uid": 1, "name": "l1", "bus_a": 1, "bus_b": 2, "tmax_ab": 100},
        ],
    }
}

# Any href / src attribute that is an absolute POSIX or Windows path, or a
# file: URI.
_ABS_PATH_RE = re.compile(r"""(?:href|src)=["'](?:/|[A-Za-z]:[\\/]|file:)""")

try:
    import graphviz as _graphviz  # noqa: F401

    _HAS_GRAPHVIZ_PY = True
except ImportError:
    _HAS_GRAPHVIZ_PY = False

_skip_no_dot = pytest.mark.skipif(
    not (_HAS_GRAPHVIZ_PY and shutil.which("dot")),
    reason="graphviz Python package or executables not installed",
)


def _model():
    builder = gd.TopologyBuilder(_PLANNING, opts=gd.FilterOptions())
    return builder.build()


def _assert_portable(text: str) -> None:
    assert not _ABS_PATH_RE.search(text), _ABS_PATH_RE.search(text)
    assert tempfile.gettempdir() not in text
    assert str(Path.home()) not in text


class TestEmbedSvgImages:
    def test_local_image_becomes_data_uri(self, tmp_path):
        icon = tmp_path / "icon_bus.png"
        icon.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        svg = (
            '<svg xmlns:xlink="http://www.w3.org/1999/xlink">'
            f'<image xlink:href="{icon}" width="48" height="48"/>'
            f"<image href='file://{icon}'/></svg>"
        )
        out = embed_svg_images(svg)
        _assert_portable(out)
        assert str(tmp_path) not in out
        assert out.count("data:image/png;base64,") == 2

    def test_remote_and_data_hrefs_untouched(self):
        svg = (
            '<svg><image xlink:href="data:image/png;base64,AAAA"/>'
            '<image href="https://example.org/x.png"/></svg>'
        )
        assert embed_svg_images(svg) == svg


class TestPortableFormats:
    @pytest.mark.skipif(not _HAS_GRAPHVIZ_PY, reason="graphviz not installed")
    def test_dot_source_has_no_local_icon_path(self):
        src = gd.render_graphviz(_model(), fmt="dot")
        assert "IMG SRC" not in src
        _assert_portable(src)

    @_skip_no_dot
    def test_svg_file_is_self_contained(self, tmp_path):
        out = gd.render_graphviz(
            _model(), fmt="svg", output_path=str(tmp_path / "diagram.svg")
        )
        svg = Path(out).read_text(encoding="utf-8")
        assert "<svg" in svg
        _assert_portable(svg)
        assert str(tmp_path) not in svg
        if "<image" in svg:
            assert "data:image/png;base64," in svg

    @_skip_no_dot
    def test_svg_pipe_is_self_contained(self):
        svg = gd.render_graphviz(_model(), fmt="svg")
        assert "<svg" in svg
        _assert_portable(svg)

    def test_html_is_self_contained(self, tmp_path, monkeypatch):
        # pyvis drops its ``lib/`` assets into the cwd.
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "diagram.html"
        gd.render_html(_model(), output_path=str(out))
        html = out.read_text(encoding="utf-8")
        assert tempfile.gettempdir() not in html
        assert str(tmp_path) not in html
        assert "gtopt_icons" not in html

    def test_mermaid_is_self_contained(self):
        text = gd.render_mermaid(_model())
        _assert_portable(text)
        assert "gtopt_icons" not in text
