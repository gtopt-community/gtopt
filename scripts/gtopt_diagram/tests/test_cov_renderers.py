# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Renderer tests on hand-built GraphModels (no case files needed)."""

import importlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from gtopt_diagram._graph_model import Edge, GraphModel, Node
from gtopt_diagram._renderers import (
    _auto_layout,
    _file_to_data_uri,
    _gv_node_attrs,
    _legend_html,
    _mermaid_to_html,
    _reactflow_layout,
    _show_mermaid,
    display_diagram,
    model_to_reactflow,
    model_to_visjs,
    render_graphviz,
    render_html,
    render_mermaid,
)
from gtopt_diagram._svg_constants import (
    _LAYOUT_DOT_THRESHOLD,
    _LAYOUT_FDP_THRESHOLD,
    _LAYOUT_NEATO_THRESHOLD,
)

rnd = importlib.import_module("gtopt_diagram._renderers")


def _dot_engine_ok() -> bool:
    exe = shutil.which("dot")
    if exe is None:
        return False
    proc = subprocess.run(
        [exe, "-Kdot", "-Tsvg"], input=b"graph{a}", capture_output=True, check=False
    )
    return proc.returncode == 0


_needs_dot = pytest.mark.skipif(
    not _dot_engine_ok(), reason="graphviz 'dot' layout engine not available"
)


def _rich_model() -> GraphModel:
    """Model exercising clusters, subclusters, critical items and edge styles."""
    m = GraphModel(title="rich")
    m.plant_meta = {"plant:P1": "Plant P1 (2 units)"}
    nodes = [
        Node("b1", "B1", "bus", cluster="electrical", is_critical=True, size=40),
        Node(
            "b2",
            "B2",
            "bus",
            cluster="electrical",
            super_cluster="community:1",
            subcluster="substation:S",
        ),
        Node(
            "b3",
            "B3",
            "bus",
            cluster="electrical",
            super_cluster="community:1",
            subcluster="substation:S",
        ),
        Node("g1", "G1 & <x>", "gen", cluster="electrical", subcluster="plant:P1"),
        Node("g2", "G2", "gen_solar", cluster="electrical", subcluster="plant:P1"),
        Node("g3", "G3", "gen", cluster="electrical", subcluster="fuel:gas"),
        Node("g4", "G4", "gen", cluster="electrical", subcluster="reserve_zone:R"),
        Node("d1", "D1", "demand", cluster="electrical", super_cluster="misc:x"),
        Node("j1", "J1", "junction", cluster="hydro", super_cluster="basin:A"),
        Node("j2", "J2", "junction", cluster="hydro", super_cluster="basin:A"),
        Node(
            "r1",
            "R1",
            "reservoir_lg",
            cluster="hydro",
            super_cluster="basin:A",
            subcluster="plant:P1",
        ),
        Node("u1", "U1", "unknown_kind", cluster="hydro"),
    ]
    for n in nodes:
        m.add_node(n)
    edges = [
        Edge(
            "b1", "b2", label="l12\n100", directed=False, weight=200, is_critical=True
        ),
        Edge("b2", "b3", directed=False, weight=50, color="#123456"),
        Edge("g1", "g2", style="dashed"),
        Edge("g3", "b1", style="dotted"),
        Edge("g4", "b1", style="animated"),
        Edge("j1", "j2", label="w"),
        Edge("j2", "r1"),
        Edge("d1", "b3", directed=False),
        Edge("b1", "b3", directed=False),
    ]
    for e in edges:
        m.add_edge(e)
    m.edge_betweenness = {("b2", "b3"): 0.9, ("b1", "b3"): 0.6}  # type: ignore[attr-defined]
    m.basin_topo_order = {"basin:A": ["j1", "missing", "r1"]}  # type: ignore[attr-defined]
    m.collapse_groups = ["plant", "basin"]  # type: ignore[attr-defined]
    return m


# ── small helpers ────────────────────────────────────────────────────────


def test_file_to_data_uri(tmp_path):
    f = tmp_path / "x.unknownext"
    f.write_bytes(b"abc")
    assert _file_to_data_uri(str(f)).startswith("data:application/octet-stream;base64,")
    assert _file_to_data_uri(str(tmp_path / "missing.png")) is None


def test_embed_svg_images_keeps_missing_local_file():
    svg = '<svg><image href="/no/such/icon.png"/></svg>'
    assert rnd.embed_svg_images(svg) == svg


def test_gv_node_attrs_icon_and_sized_shapes():
    node = Node("g", "A & <B>\nC", "gen")
    attrs = _gv_node_attrs(node, "/tmp/icon.png")
    assert attrs["shape"] == "none"
    assert "A &amp; &lt;B&gt;<BR/>C" in attrs["label"]
    sized = _gv_node_attrs(Node("b", "B", "bus", size=60), None)
    assert sized["width"] == sized["height"] == "2.0"
    plain = _gv_node_attrs(Node("b", "B", "nokind"), None)
    assert plain["shape"] == "ellipse"
    assert "width" not in plain


def test_auto_layout_thresholds():
    def model(n):
        return GraphModel(nodes=[Node(f"n{i}", "x", "bus") for i in range(n)])

    assert _auto_layout(model(500), "hydro") == "dot"
    assert _auto_layout(model(_LAYOUT_DOT_THRESHOLD), "full") == "dot"
    assert _auto_layout(model(_LAYOUT_NEATO_THRESHOLD), "full") == "neato"
    assert _auto_layout(model(_LAYOUT_FDP_THRESHOLD), "full") == "fdp"
    assert _auto_layout(model(_LAYOUT_FDP_THRESHOLD + 1), "full") == "sfdp"


def test_mermaid_to_html_without_fences():
    html = _mermaid_to_html("flowchart LR\n  a --> b", title="T")
    assert "<title>T</title>" in html
    assert "a --> b" in html


def test_show_mermaid_writes_temp_html(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(rnd.webbrowser, "open", opened.append)
    monkeypatch.setattr(rnd.tempfile, "tempdir", str(tmp_path))
    _show_mermaid("```mermaid\nflowchart LR\n```", title="X")
    assert len(opened) == 1
    path = Path(opened[0].removeprefix("file://"))
    assert "<title>X</title>" in path.read_text(encoding="utf-8")


def test_display_diagram_png_paths(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(rnd.webbrowser, "open", opened.append)
    shown = []
    fake_image = types.SimpleNamespace(
        open=lambda p: types.SimpleNamespace(show=lambda: shown.append(p))
    )
    monkeypatch.setitem(sys.modules, "PIL", types.SimpleNamespace(Image=fake_image))
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image)
    png = tmp_path / "x.png"
    display_diagram(str(png), "png")
    assert shown == [str(png.resolve())]
    assert not opened

    monkeypatch.setitem(sys.modules, "PIL", None)
    display_diagram(str(png), "png")
    assert opened == [png.resolve().as_uri()]


# ── mermaid / visjs / reactflow ─────────────────────────────────────────


def test_render_mermaid_edge_styles():
    text = render_mermaid(_rich_model(), direction="TB")
    assert "flowchart TB" in text
    assert "b1 ---|l12 100| b2" in text
    assert "g1 -.-> g2" in text
    assert "g3 ...> b1" in text
    assert "j1 -->|w| j2" in text
    assert "classDef cls_unknown_kind fill:#FFF,stroke:#333" in text


def test_model_to_visjs_icon_and_plain_nodes():
    data = model_to_visjs(_rich_model())
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["b1"]["shape"] == "image"
    assert by_id["u1"]["shape"] == "dot"
    assert by_id["u1"]["group"] == "hydro"
    assert data["edges"][0]["arrows"] == ""
    assert data["edges"][2]["dashes"] is True


def test_reactflow_with_and_without_networkx(monkeypatch):
    model = _rich_model()
    rf = model_to_reactflow(model)
    assert len(rf["nodes"]) == len(model.nodes)
    by_id = {n["id"]: n for n in rf["nodes"]}
    assert by_id["b1"]["type"] == "bus"
    assert by_id["u1"]["type"] == "default"
    assert by_id["b1"]["data"]["size"] == 40
    edges = rf["edges"]
    assert edges[2]["style"]["strokeDasharray"] == "6 4"
    assert edges[4]["animated"] is True
    assert edges[0]["markerEnd"] is None
    assert edges[2]["markerEnd"] == {"type": "arrowclosed"}

    monkeypatch.setitem(sys.modules, "networkx", None)
    pos = _reactflow_layout(model)
    assert len(pos) == len(model.nodes)
    assert pos["b1"] == pytest.approx((120.0, 0.0))
    assert not _reactflow_layout(GraphModel())


def test_legend_html_icons_and_shapes(monkeypatch):
    model = GraphModel(nodes=[Node("r", "r", "reservoir_sm"), Node("d", "d", "demand")])
    html = _legend_html(model)
    assert "Reservoir / dam" in html
    assert "Demand / load" in html
    monkeypatch.setattr(rnd, "_icon_b64_uri", lambda kind: "")
    html = _legend_html(model)
    assert "<svg" in html


# ── HTML (pyvis) ─────────────────────────────────────────────────────────


def test_render_html_clusters_and_collapse(tmp_path, monkeypatch):
    pytest.importorskip("pyvis")
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "x.html"
    render_html(_rich_model(), str(out))
    html = out.read_text()
    assert "clusterLabels" in html
    labels = json.loads(html.split("var clusterLabels = ", 1)[1].split(";", 1)[0])
    assert labels == {
        "plant:P1": "Plant P1 (2 units)",
        "basin:A": "basin:A",
    }
    assert "#D32F2F" in html  # critical node / edge colour
    assert "rich" in html


def test_render_html_no_collapse_and_plain_shapes(tmp_path, monkeypatch):
    pytest.importorskip("pyvis")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rnd, "_icon_b64_uri", lambda kind: "")
    model = _rich_model()
    model.collapse_groups = ["nothing_matches"]  # type: ignore[attr-defined]
    out = tmp_path / "y.html"
    render_html(model, str(out))
    assert "clusterLabels" not in out.read_text()


def test_render_html_without_pyvis(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyvis.network", None)
    with pytest.raises(SystemExit, match="pip install pyvis"):
        render_html(GraphModel(), str(tmp_path / "z.html"))


# ── Graphviz ─────────────────────────────────────────────────────────────


def test_render_graphviz_without_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "graphviz", None)
    with pytest.raises(SystemExit, match="pip install graphviz"):
        render_graphviz(GraphModel())


def test_render_graphviz_dot_source_clusters():
    pytest.importorskip("graphviz")
    src = render_graphviz(_rich_model(), fmt="dot", use_clusters=True)
    assert "cluster_elec" in src and "cluster_hydro" in src
    assert "cluster_basin_A" in src
    assert "cluster_community_1" in src
    assert "cluster_misc_x" in src
    assert "cluster_plant_P1" in src
    assert "cluster_fuel_gas" in src
    assert "cluster_reserve_zone_R" in src
    assert "cluster_substation_S" in src
    assert "rank=source" in src and "rank=sink" in src
    assert "cluster_legend" in src
    assert "IMG SRC" not in src


def test_render_graphviz_single_kind_flat():
    pytest.importorskip("graphviz")
    model = GraphModel(
        title="flat", nodes=[Node("a", "A", "bus"), Node("b", "B", "bus")]
    )
    model.add_edge(Edge("a", "b", directed=False))
    src = render_graphviz(model, fmt="dot", use_clusters=True)
    assert src.startswith("graph")
    assert "cluster_legend" not in src


@_needs_dot
def test_render_graphviz_svg_file_and_pipe(tmp_path):
    pytest.importorskip("graphviz")
    model = _rich_model()
    model.basin_topo_order = {"basin:A": ["j1", "j2"]}  # type: ignore[attr-defined]
    out = render_graphviz(
        model, fmt="svg", output_path=str(tmp_path / "r.svg"), layout="dot"
    )
    svg = Path(out).read_text(encoding="utf-8")
    assert "<svg" in svg
    assert str(tmp_path) not in svg
    piped = render_graphviz(model, fmt="svg", layout="dot")
    assert "<svg" in piped


def test_render_graphviz_missing_executables(tmp_path, monkeypatch):
    gv = pytest.importorskip("graphviz")

    def boom(*_a, **_k):
        raise FileNotFoundError("dot")

    monkeypatch.setattr(gv.Graph, "render", boom)
    monkeypatch.setattr(gv.Graph, "pipe", boom)
    model = GraphModel(nodes=[Node("a", "A", "bus")])
    with pytest.raises(SystemExit, match="Graphviz executables not found"):
        render_graphviz(model, fmt="svg", output_path=str(tmp_path / "a.svg"))
    with pytest.raises(SystemExit, match="Graphviz executables not found"):
        render_graphviz(model, fmt="svg")


def test_render_graphviz_png_pipe_not_embedded(monkeypatch):
    gv = pytest.importorskip("graphviz")
    monkeypatch.setattr(gv.Graph, "pipe", lambda self, format=None: b"PNGDATA")
    model = GraphModel(nodes=[Node("a", "A", "bus")])
    assert render_graphviz(model, fmt="png") == "PNGDATA"
