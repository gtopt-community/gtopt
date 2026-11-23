# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI tests for gtopt_diagram.cli.main (viewers mocked, no network)."""

import importlib
import json
import logging
import shutil
import subprocess
import sys
import types

import pytest

from gtopt_diagram._svg_constants import _PALETTE

cli = importlib.import_module("gtopt_diagram.cli")

_CASE = {
    "system": {
        "name": "clicase",
        "bus_array": [
            {"uid": 1, "name": "b1", "voltage": 220},
            {"uid": 2, "name": "b2", "voltage": 66},
            {"uid": 3, "name": "b3", "voltage": 220},
            {"uid": 5, "kv": 500},
        ],
        "generator_array": [
            {"uid": 1, "name": "g1", "bus": 1, "pmax": 100, "gcost": 10},
            {"uid": 2, "name": "g2", "bus": "b3", "pmax": 50, "gcost": 20},
            {"uid": 3, "name": "g_nobus", "pmax": 5},
            {"uid": 4, "bus": "ghost", "pmax": 5},
            {"name": "nameless", "bus": 1},
        ],
        "demand_array": [{"uid": 1, "name": "d1", "bus": 2, "lmax": 80}],
        "line_array": [
            {"uid": 1, "name": "l12", "bus_a": 1, "bus_b": 2, "tmax_ab": 100},
            {"uid": 2, "name": "l23", "bus_a": 2, "bus_b": 3, "tmax_ab": 100},
        ],
    }
}


@pytest.fixture(name="viewer")
def _viewer(monkeypatch):
    """Record display_diagram / _show_mermaid calls instead of opening viewers."""
    calls: list[tuple] = []
    monkeypatch.setattr(cli, "display_diagram", lambda p, f: calls.append(("d", f)))
    monkeypatch.setattr(
        cli, "_show_mermaid", lambda text, title="": calls.append(("m", title))
    )
    return calls


@pytest.fixture(name="case_file")
def _case_file(tmp_path):
    path = tmp_path / "case.json"
    path.write_text(json.dumps(_CASE))
    return path


def _has_dot_engine() -> bool:
    exe = shutil.which("dot")
    if exe is None:
        return False
    proc = subprocess.run(
        [exe, "-Kdot", "-Tsvg"], input=b"graph{a}", capture_output=True, check=False
    )
    return proc.returncode == 0


# ── planning diagrams ────────────────────────────────────────────────────


def test_planning_mermaid_show_and_output(tmp_path, viewer, case_file):
    out = tmp_path / "p.md"
    assert (
        cli.main([str(case_file), "-t", "planning", "-f", "mermaid", "-o", str(out)])
        == 0
    )
    assert out.read_text().startswith("```mermaid")
    assert not viewer
    assert cli.main([str(case_file), "-t", "planning", "-f", "mermaid", "--show"]) == 0
    assert viewer == [("m", "clicase — Planning Structure")]


def test_planning_html_titles(tmp_path, viewer, case_file, monkeypatch):
    out = tmp_path / "p.html"
    assert (
        cli.main([str(case_file), "-t", "planning", "-f", "html", "-o", str(out)]) == 0
    )
    assert "<title>clicase — Planning Structure</title>" in out.read_text()
    assert not viewer
    monkeypatch.chdir(tmp_path)
    # no JSON file → default case name and auto-show
    assert cli.main(["-t", "planning", "-f", "html"]) == 0
    html = (tmp_path / "gtopt_planning.html").read_text()
    assert "<title>gtopt Planning Structure</title>" in html
    assert viewer == [("d", "html")]


@pytest.mark.parametrize("fmt", ["svg", "dot"])
def test_planning_svg_like(tmp_path, viewer, fmt):
    out = tmp_path / f"p.{fmt}"
    assert cli.main(["-t", "planning", "-f", fmt, "-o", str(out), "--show"]) == 0
    assert out.read_text().startswith("<svg")
    assert viewer == [("d", fmt)]


@pytest.mark.parametrize("fmt", ["png", "pdf"])
def test_planning_cairosvg_formats(tmp_path, viewer, monkeypatch, fmt):
    calls = []
    fake = types.SimpleNamespace(
        svg2png=lambda **kw: calls.append(("png", kw["write_to"])),
        svg2pdf=lambda **kw: calls.append(("pdf", kw["write_to"])),
    )
    monkeypatch.setitem(sys.modules, "cairosvg", fake)
    out = tmp_path / f"p.{fmt}"
    assert cli.main(["-t", "planning", "-f", fmt, "-o", str(out)]) == 0
    assert calls == [(fmt, str(out))]
    assert not viewer


def test_planning_without_cairosvg_writes_svg(tmp_path, viewer, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "cairosvg", None)
    out = tmp_path / "p.png"
    with caplog.at_level(logging.WARNING):
        assert cli.main(["-t", "planning", "-f", "png", "-o", str(out)]) == 0
    assert "cairosvg not found" in caplog.text
    assert (tmp_path / "p.svg").read_text().startswith("<svg")
    assert not out.exists()
    assert not viewer


# ── topology diagrams ────────────────────────────────────────────────────


def test_topology_mermaid_output_and_show(tmp_path, viewer, case_file, capsys):
    out = tmp_path / "t.md"
    assert cli.main([str(case_file), "-f", "mermaid", "-o", str(out), "--show"]) == 0
    assert "flowchart" in out.read_text() or "graph" in out.read_text()
    assert viewer == [("m", "clicase")]
    assert cli.main([str(case_file), "-f", "mermaid"]) == 0
    assert "b1" in capsys.readouterr().out


def test_topology_html(tmp_path, viewer, case_file, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main([str(case_file), "-f", "html"]) == 0  # auto-show
    assert (tmp_path / "clicase_topology.html").exists()
    assert viewer == [("d", "html")]
    assert cli.main([str(case_file), "-f", "html", "-o", str(tmp_path / "o.html")]) == 0
    assert viewer == [("d", "html")]  # explicit output: no auto-show


def test_topology_dot_file_and_layout(tmp_path, viewer, case_file):
    pytest.importorskip("graphviz")
    out = tmp_path / "t.dot"
    rc = cli.main(
        [str(case_file), "-f", "dot", "-o", str(out), "--layout", "fdp", "--clusters"]
    )
    assert rc == 0
    src = out.read_text()
    assert "layout=fdp" in src or "graph" in src
    assert not viewer


def test_topology_rendered_svg_with_show(tmp_path, viewer, case_file):
    pytest.importorskip("graphviz")
    if not _has_dot_engine():
        pytest.skip("graphviz 'dot' layout engine not available")
    out = tmp_path / "t.svg"
    assert cli.main([str(case_file), "-o", str(out), "--layout", "dot", "--show"]) == 0
    assert out.read_text().lstrip().startswith(("<?xml", "<svg", "<!DOCTYPE"))
    assert viewer == [("d", "svg")]


def test_topology_flags_and_focus(tmp_path, viewer, case_file, caplog):
    with caplog.at_level(logging.INFO, logger="gtopt_diagram.cli"):
        rc = cli.main(
            [
                str(case_file),
                "-f",
                "mermaid",
                "-o",
                str(tmp_path / "x.md"),
                "--focus-generator",
                "g1",
                "2",
                "g_nobus",
                "missing",
                "4",
                "nameless",
                "--focus-area",
                "200",
                "--voltage-threshold",
                "100",
                "--aggregate",
                "bus",
                "--no-generators",
                "--collapse",
                "all",
            ]
        )
    assert rc == 0
    assert "generator 'missing' not found" in caplog.text
    assert "aggregate=bus" in caplog.text
    assert "voltage≥100 kV" in caplog.text
    assert "no-generators" in caplog.text


def test_topology_auto_info_flag_logged(tmp_path, viewer, case_file, caplog):
    with caplog.at_level(logging.INFO, logger="gtopt_diagram.cli"):
        rc = cli.main(
            [
                str(case_file),
                "-f",
                "mermaid",
                "-o",
                str(tmp_path / "x.md"),
                "--collapse",
                "plant, fuel",
            ]
        )
    assert rc == 0
    assert "auto(" in caplog.text


def test_topology_empty_model_warns(tmp_path, viewer, caplog):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"system": {"name": "e"}}))
    with caplog.at_level(logging.WARNING):
        assert cli.main([str(path), "-f", "mermaid", "-o", str(tmp_path / "e.md")]) == 0
    assert "No elements found" in caplog.text


def test_topology_colorblind_palette(tmp_path, viewer, case_file):
    saved = dict(_PALETTE)
    try:
        rc = cli.main(
            [
                str(case_file),
                "-f",
                "mermaid",
                "-o",
                str(tmp_path / "x.md"),
                "--palette",
                "colorblind",
            ]
        )
        assert rc == 0
        assert _PALETTE != saved
    finally:
        _PALETTE.clear()
        _PALETTE.update(saved)


def test_topology_without_check_json(tmp_path, viewer, case_file, monkeypatch):
    monkeypatch.setitem(sys.modules, "gtopt_check_json._info", None)
    assert (
        cli.main([str(case_file), "-f", "mermaid", "-o", str(tmp_path / "x.md")]) == 0
    )


def test_topology_without_json_file(tmp_path, viewer, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert cli.main(["-f", "mermaid", "-o", "x.md"]) == 0
    assert "No elements found" in caplog.text


def test_file_referenced_fields_are_resolved(tmp_path, viewer):
    """Regression: --scenario/--stage/--block and file-referenced fields
    were ignored because the resolver was keyed on a non-existent
    ``args.input``; labels showed the '—' placeholder."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    case = json.loads(json.dumps(_CASE))
    case["options"] = {"input_directory": "inp"}
    case["system"]["generator_array"][0]["pmax"] = "pmax"
    gen_dir = tmp_path / "inp" / "Generator"
    gen_dir.mkdir(parents=True)
    pd.DataFrame({"block": [1, 2], "uid": [1, 1], "value": [123.0, 456.0]}).to_parquet(
        gen_dir / "pmax.parquet"
    )
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case))

    out = tmp_path / "o.md"
    args = [str(path), "-f", "mermaid", "-o", str(out), "--aggregate", "none"]
    assert cli.main(args) == 0
    assert "123.0 MW" in out.read_text()
    assert cli.main([*args, "--block", "2"]) == 0
    assert "456.0 MW" in out.read_text()


def test_topology_dot_to_stdout_and_rendered_without_show(
    tmp_path, viewer, case_file, capsys, monkeypatch
):
    pytest.importorskip("graphviz")
    assert cli.main([str(case_file), "-f", "dot"]) == 0
    assert "graph" in capsys.readouterr().out
    monkeypatch.setattr(cli, "render_graphviz", lambda *a, **k: str(tmp_path / "x.png"))
    assert cli.main([str(case_file), "-f", "png", "-o", str(tmp_path / "x.png")]) == 0
    assert not viewer
