# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""E2E smoke cases for the CEN / marginal-emission public console scripts.

None of these cases touch the network: every tool that can reach the CEN
APIs runs with a dead HTTP(S) proxy and an isolated ``HOME`` (the CEN
caches live under ``~/.cache/gtopt/cen2gtopt``), and is fed a local
fixture -- a seeded index / cache / bundle, a loopback HTTP server, or
stub ``mdb-tables`` / ``mdb-export`` executables.
"""

from __future__ import annotations

import json
import os
import shutil
import site
import stat
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from _cli_e2e import CASES_DIR, CliRun, assert_solved, e2e

#: Unroutable proxy: an accidental fetch fails fast instead of hitting CEN.
_DEAD_PROXY = "http://127.0.0.1:9"


def _offline(ctx: CliRun, home: Path | None = None) -> Path:
    """Isolate HOME and kill outbound HTTP for the tool under test."""
    home = home or ctx.tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    ctx.env.update(
        {
            "HOME": str(home),
            "HTTP_PROXY": _DEAD_PROXY,
            "HTTPS_PROXY": _DEAD_PROXY,
            "http_proxy": _DEAD_PROXY,
            "https_proxy": _DEAD_PROXY,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "CEN_USER_KEY": "offline-test-key",
            # A new HOME would hide the user site-packages (pip --user).
            "PYTHONUSERBASE": os.environ.get("PYTHONUSERBASE", site.getuserbase()),
        }
    )
    return home


def _cen_cache(home: Path) -> Path:
    return home / ".cache" / "gtopt" / "cen2gtopt"


def _write_catalogue(path: Path) -> Path:
    """Tiny two-bus catalogue in the ``known_buses.parquet`` schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "bus_id": [2061, 3105],
            "bar_transf": ["CHARRUA________220", "CRUCERO________220"],
            "barra_info": ["BA S/E CHARRUA 220KV", "BA S/E CRUCERO 220KV"],
            "voltage_kv": [220, 220],
            "region": ["south-centre", "north"],
            "last_seen": ["2026-04-22", "2026-04-22"],
        }
    ).to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------
# gtopt-marginal-units
# ---------------------------------------------------------------------------


def _marginal_units(ctx: CliRun, planning: Path, output: Path) -> None:
    out = ctx.tmp_path / "marginals.parquet"
    # 0 = all cells attributed, 2 = some cell left __unattributed__.
    ctx.run(
        [
            "--input-kind",
            "gtopt-dir",
            "--mode",
            "simulated",
            "--planning",
            planning,
            "--output",
            output,
            "--out",
            out,
        ],
        expect_rc=(0, 2),
    )
    # pylint: disable-next=import-outside-toplevel
    from gtopt_marginal_units.consumer import MarginalUnitDataset

    per_bus = MarginalUnitDataset.open(out).per_bus()
    assert per_bus["bus_uid"].nunique() == 9, per_bus.head()


@e2e("gtopt-marginal-units")
def case_ieee_9b_ori_golden_output(ctx: CliRun) -> None:
    """Marginal units of the committed ieee_9b_ori golden solution."""
    case = CASES_DIR / "ieee_9b_ori"
    _marginal_units(ctx, case / "ieee_9b_ori.json", case / "output")


@e2e("gtopt-marginal-units", needs_gtopt=True)
def case_ieee_9b_ori_fresh_solve(ctx: CliRun) -> None:
    """Solve ieee_9b_ori with gtopt, then attribute its marginal units."""
    case_dir = ctx.tmp_path / "ieee_9b_ori"
    shutil.copytree(
        CASES_DIR / "ieee_9b_ori", case_dir, ignore=shutil.ignore_patterns("output")
    )
    case_json = case_dir / "ieee_9b_ori.json"
    out = ctx.run_gtopt(case_json)
    assert_solved(out)
    _marginal_units(ctx, case_json, out)


# ---------------------------------------------------------------------------
# cen2gtopt
# ---------------------------------------------------------------------------


@e2e("cen2gtopt")
def case_manifest_only(ctx: CliRun) -> None:
    """``--manifest-only`` writes the feed manifest without any fetch."""
    _offline(ctx)
    out = ctx.tmp_path / "feed.parquet"
    ctx.run(
        [
            "--start",
            "2026-04-01",
            "--end",
            "2026-04-07",
            "--out",
            out,
            "--source",
            "csv",
            "--include",
            "lmp",
            "--manifest-only",
        ]
    )
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["producer"] == "cen2gtopt"
    assert manifest["manifest_only"] is True
    assert manifest["args"] == {
        "start": "2026-04-01",
        "end": "2026-04-07",
        "source": "csv",
        "include": ["lmp"],
    }


@e2e("cen2gtopt")
def case_bad_input_is_clean_error(ctx: CliRun) -> None:
    """Reversed dates exit with EXIT_INPUT_ERROR (3), not a traceback."""
    _offline(ctx)
    res = ctx.run(
        ["--start", "2026-04-10", "--end", "2026-04-01", "--out", ctx.tmp_path / "f"],
        expect_rc=3,
    )
    assert "Traceback" not in res.output
    assert "--start > --end" in res.output


# ---------------------------------------------------------------------------
# cen_demanda -- served by a loopback SIPUB stand-in
# ---------------------------------------------------------------------------


class _SipubHandler(BaseHTTPRequestHandler):
    """Answers ``/<endpoint>/?user_key=..&fecha=..`` with a JSON day."""

    def do_GET(self) -> None:  # noqa: N802  (http.server API)
        """Serve one synthetic day of hourly demand."""
        url = urlparse(self.path)
        fecha = parse_qs(url.query).get("fecha", ["?"])[0]
        body = json.dumps(
            {
                "endpoint": url.path.strip("/"),
                "fecha": fecha,
                "data": [{"hora": h, "demanda_mw": 9000.0 + h} for h in range(24)],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the test output quiet."""
        del format, args


@contextmanager
def _sipub_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SipubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/sipub/api/v2"
    finally:
        server.shutdown()
        server.server_close()


@e2e("cen_demanda")
def case_download_from_local_server(ctx: CliRun) -> None:
    """Download 3 days, then resume (existing days are not re-fetched)."""
    _offline(ctx)
    out_dir = ctx.tmp_path / "demanda"
    args: list[str | Path] = [
        "--start",
        "2024-01-01",
        "--end",
        "2024-01-03",
        "--output-dir",
        out_dir,
        "--rate-per-hour",
        "3600000",
    ]
    with _sipub_server() as base_url:
        ctx.run([*args, "--base-url", base_url])
        files = sorted((out_dir / "demanda_sistema_real").glob("*.json"))
        assert [f.stem for f in files] == ["2024-01-01", "2024-01-02", "2024-01-03"]
        day = json.loads(files[1].read_text(encoding="utf-8"))
        assert day["fecha"] == "2024-01-02"
        assert len(day["data"]) == 24
        # Resume against a dead URL: nothing left to fetch -> still exit 0.
        res = ctx.run([*args, "--base-url", "http://127.0.0.1:9/none"])
        assert "0 new files" in res.output


@e2e("cen_demanda")
def case_unreachable_api_is_clean_error(ctx: CliRun) -> None:
    """A dead endpoint aborts with exit 2 and a log line, no traceback."""
    _offline(ctx)
    res = ctx.run(
        [
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-01",
            "--output-dir",
            ctx.tmp_path / "demanda",
            "--base-url",
            "http://127.0.0.1:9/none",
        ],
        expect_rc=2,
    )
    assert "download aborted" in res.output
    assert "Traceback" not in res.output


# ---------------------------------------------------------------------------
# cen2gtopt-buses / cen2gtopt-cache
# ---------------------------------------------------------------------------


@e2e("cen2gtopt-buses")
def case_list_show_categorize(ctx: CliRun) -> None:
    """Catalogue viewer subcommands on a two-bus catalogue."""
    _offline(ctx)
    cat = _write_catalogue(ctx.tmp_path / "known_buses.parquet")
    res = ctx.run(["--catalogue", cat, "list", "--min-voltage", "200"])
    assert "2 buses" in res.stdout and "CHARRUA" in res.stdout
    res = ctx.run(["--catalogue", cat, "show", "2061"])
    assert "CHARRUA" in res.stdout and "CRUCERO" not in res.stdout
    res = ctx.run(["--catalogue", cat, "categorize"])
    assert "2 buses total" in res.stdout


@e2e("cen2gtopt-cache")
def case_list_and_clear(ctx: CliRun) -> None:
    """List and clear an (isolated) on-disk CEN cache."""
    home = _offline(ctx)
    frame = _cen_cache(home) / "sip" / "unidades_generadoras" / "abc.parquet"
    frame.parent.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2]}).to_parquet(frame)
    res = ctx.run(["list"])
    assert "1 cached frame(s)" in res.stdout
    assert "unidades_generadoras" in res.stdout
    res = ctx.run(["clear", "--service", "sip"])
    assert "removed 1 cached file(s)" in res.stdout
    assert not frame.exists()


# ---------------------------------------------------------------------------
# cen2gtopt-pcp-archive / cen2gtopt-pcp-inputs / cen2gtopt-pcp-solution
# ---------------------------------------------------------------------------


def _index_row(nombre: str, categoria: str) -> dict[str, object]:
    return {
        "id": abs(hash(nombre)) % 10_000,
        "nombre": nombre,
        "descripcion": f"{categoria} bundle {nombre}",
        "categoria": categoria,
        "path": f"{categoria}/{nombre}",
        "fechaPublicacion": "2026-04-06T12:00:00",
        "fechaVigencia": "2026-04-07",
        "enlaceDescarga": "http://127.0.0.1:9/never",
        "vencimiento": "2099-01-01",
        "estado": "PUBLICADO",
    }


@e2e("cen2gtopt-pcp-archive")
def case_list_and_plp_in_force(ctx: CliRun) -> None:
    """Browse a seeded index; extract the PLP bundle in force offline."""
    home = _offline(ctx)
    index = _cen_cache(home) / "pcp_index.json"
    index.parent.mkdir(parents=True)
    index.write_text(
        json.dumps(
            [
                _index_row("PLEXOS20260407.zip", "PCP"),
                _index_row("IPLP_20260301.zip", "PLP"),
                _index_row("IPLP_20260401.zip", "PLP"),
                _index_row("OPLP_20260401.zip", "PLP"),
            ]
        ),
        encoding="utf-8",
    )
    res = ctx.run(["list", "--pattern", "PLP", "--since", "2026-04-01"])
    assert "IPLP_20260401.zip" in res.stdout
    assert "IPLP_20260301.zip" not in res.stdout
    assert "2 files matched" in res.stdout

    # Pre-"downloaded" bundle: download_one skips existing files.
    dl_root = ctx.tmp_path / "dl"
    bundle = dl_root / "PLP" / "IPLP_20260401.zip"
    bundle.parent.mkdir(parents=True)
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("IPLP/Datos/plpblo.dat", "# blocks\n")
        zf.writestr("IPLP/Datos/plpbar.dat", "# buses\n")
        zf.writestr("IPLP/Datos/README.txt", "not a PLP input\n")
    extract = ctx.tmp_path / "plp_inputs"
    res = ctx.run(
        ["plp-in-force", "2026-04-20", "--output", dl_root, "--extract", extract]
    )
    assert "IPLP_20260401.zip" in res.stderr
    assert sorted(p.name for p in extract.iterdir()) == ["plpbar.dat", "plpblo.dat"]


@e2e("gtopt-fetch")
def case_fetch_pcp_from_legacy_cache_and_verify(ctx: CliRun) -> None:
    """Registry listing + one PCP day served from the legacy cache, offline."""
    home = _offline(ctx)
    ctx.env["GTOPT_FETCH_CACHE"] = str(ctx.tmp_path / "fetch")
    index = _cen_cache(home) / "pcp_index.json"
    index.parent.mkdir(parents=True)
    index.write_text(
        json.dumps([_index_row("PLEXOS20260407.zip", "PCP")]), encoding="utf-8"
    )
    wrapper = _cen_cache(home) / "pcp_archive" / "PCP" / "PLEXOS20260407.zip"
    wrapper.parent.mkdir(parents=True)
    with zipfile.ZipFile(wrapper, "w") as zf:
        zf.writestr("PLEXOS20260407/DATOS20260407.zip", b"datos")
        zf.writestr("PLEXOS20260407/RES20260407.zip", b"res")

    res = ctx.run(["operators"])
    assert "cen" in res.stdout and "aemo" in res.stdout
    res = ctx.run(["cen", "list"])
    assert "plp_inputs" in res.stdout

    out = ctx.tmp_path / "case" / "source"
    res = ctx.run(["cen", "get", "pcp", "--date", "2026-04-07", "--out", out])
    assert sorted(p.name for p in out.iterdir()) == [
        "DATOS20260407.zip",
        "MANIFEST.jsonl",
        "RES20260407.zip",
    ]
    assert (out / "DATOS20260407.zip").read_bytes() == b"datos"
    res = ctx.run(["verify", out])
    assert "2/2 files ok" in res.stdout

    # Manual, non-redistributable dataset: instructions + exit 3.
    res = ctx.run(["aemo", "get", "iasr_workbook", "--version", "2026"], expect_rc=3)
    assert "NOT redistributable" in res.stderr


@e2e("cen2gtopt-pcp-inputs")
def case_summary_and_merit(ctx: CliRun) -> None:
    """Summarise a synthetic cached ``DATOS<date>.zip`` bundle."""
    home = _offline(ctx)
    datos = _cen_cache(home) / "pcp_archive" / "PCP" / "DATOS20260407.zip"
    datos.parent.mkdir(parents=True)
    periods = range(1, 25)

    def _csv(names: list[str], values: list[float]) -> str:
        rows = ["NAME,YEAR,MONTH,DAY,PERIOD,VALUE"]
        for name, value in zip(names, values, strict=True):
            rows += [f"{name},2026,4,7,{p},{value}" for p in periods]
        return "\n".join(rows) + "\n"

    units = ["ANGAMOS_1", "NEHUENCO_GN"]
    with zipfile.ZipFile(datos, "w") as zf:
        zf.writestr("Gen_HeatRate.csv", _csv(units, [9.5, 7.0]))
        zf.writestr("Gen_Rating.csv", _csv(units, [270.0, 380.0]))
        zf.writestr("Gen_MinStableLevel.csv", _csv(units, [120.0, 150.0]))
        zf.writestr("Gen_VOMCharge.csv", _csv(units, [3.0, 2.5]))
        # No PLEXOS XML: units resolve to fuels by name substring.
        zf.writestr("Fuel_Price.csv", _csv(["ANGAMOS", "NEHUENCO"], [3.1, 8.4]))
    res = ctx.run(["summary", "--date", "2026-04-07", "--source", "pcp"])
    assert "PCP inputs summary" in res.stdout
    assert "n_units         = 2" in res.stdout
    assert "n_hours         = 24" in res.stdout
    assert "NEHUENCO_GN" in res.stdout
    res = ctx.run(["merit", "--date", "2026-04-07", "--source", "pcp"])
    assert "total units with derived MC: 2" in res.stdout, res.output
    # merit order: ANGAMOS (9.5 x 3.1 + 3.0) before NEHUENCO (7.0 x 8.4 + 2.5)
    assert res.stdout.index("ANGAMOS_1") < res.stdout.index("NEHUENCO_GN")


def _stub_mdbtools(bin_dir: Path, tables: dict[str, str]) -> None:
    """Write fake ``mdb-tables`` / ``mdb-export`` serving *tables* (CSV)."""
    bin_dir.mkdir(parents=True)
    data_dir = bin_dir / "tables"
    data_dir.mkdir()
    for name, csv_text in tables.items():
        (data_dir / f"{name}.csv").write_text(csv_text, encoding="utf-8")
    scripts = {
        "mdb-tables": f'#!/bin/sh\nls "{data_dir}" | sed "s/\\.csv$//"\n',
        # mdb-export -D <fmt> <accdb> <table>
        "mdb-export": f'#!/bin/sh\nf="{data_dir}/$4.csv"\n[ -f "$f" ] && cat "$f"\n',
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


@e2e("cen2gtopt-pcp-solution")
def case_tables_and_dump(ctx: CliRun) -> None:
    """List / dump a cached PID solution DB through stub mdbtools."""
    home = _offline(ctx)
    pid = "PID_20260407_05"
    sol_dir = (
        _cen_cache(home)
        / "pcp_archive"
        / "_unpacked"
        / pid
        / pid
        / "Modelos"
        / "Model PRGdia_Full_Definitivo_PID Solution"
    )
    sol_dir.mkdir(parents=True)
    (sol_dir / "Model PRGdia_Full_Definitivo_PID Solution.accdb").write_bytes(b"\0")
    bin_dir = ctx.tmp_path / "bin"
    _stub_mdbtools(
        bin_dir,
        {
            "t_object": "object_id,name\n1,Polpaico220\n2,Charrua220\n",
            "t_property": "property_id,name\n7,Price\n",
        },
    )
    ctx.env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    res = ctx.run(["tables", "--date", "2026-04-07"])
    assert "PID period=05" in res.stdout
    assert "2 tables:" in res.stdout and "t_object" in res.stdout
    out_csv = ctx.tmp_path / "t_object.csv"
    res = ctx.run(
        ["dump", "--date", "2026-04-07", "--table", "t_object", "--output", out_csv]
    )
    assert "2 rows" in res.stdout
    assert pd.read_csv(out_csv)["name"].tolist() == ["Polpaico220", "Charrua220"]


# ---------------------------------------------------------------------------
# cen2gtopt-marginal-period
# ---------------------------------------------------------------------------


@e2e("cen2gtopt-marginal-period")
def case_select_plan_and_resume(ctx: CliRun) -> None:
    """Bus selection, dry-run plan and a fully-resumed (no-fetch) run."""
    _offline(ctx)
    cat = _write_catalogue(ctx.tmp_path / "known_buses.parquet")
    out_root = ctx.tmp_path / "marginal"
    common: list[str | Path] = [
        "--start-date",
        "2026-04-01",
        "--end-date",
        "2026-04-02",
        "--catalogue",
        cat,
        "--output",
        out_root,
        "--workers",
        "1",
    ]
    res = ctx.run([*common, "--buses-region", "north", "--list-buses"])
    assert "1 buses" in res.stdout and "CRUCERO" in res.stdout

    res = ctx.run([*common, "--dry-run"])
    assert "to process:                2" in res.stdout
    assert "DRY RUN" in res.stdout
    assert not out_root.exists()

    # Both days already computed -> nothing to fetch; manifest still written.
    # pylint: disable-next=import-outside-toplevel
    from cen2gtopt.marginal_period import day_parquet_path

    for day in (date(2026, 4, 1), date(2026, 4, 2)):
        path = day_parquet_path(out_root, day)
        path.parent.mkdir(parents=True)
        pd.DataFrame({"bus_uid": [2061], "quarter": [0]}).to_parquet(path)
    res = ctx.run(common)
    assert "skipped:       2" in res.stdout
    manifests = list(out_root.rglob("*manifest*.json"))
    assert manifests, sorted(p.name for p in out_root.rglob("*"))
