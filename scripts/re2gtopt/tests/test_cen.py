# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CEN parsers on synthetic replies shaped like the public files (no CEN
data in the repository, no network)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from dlr2gtopt._towers import PoliteCache
from re2gtopt import _cen as cen
from re2gtopt import _plants as pl
from re2gtopt.__main__ import main


def _ficha(**rows: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "row_header_11": {"nombre_concepto": None, "valor_texto": ""},
    }
    for k, (name, val) in enumerate(rows.items()):
        txt, state = val if isinstance(val, tuple) else (val, "Validado")
        out[str(k)] = {
            "nombre_concepto": name.replace("_", " "),
            "valor_texto": txt if isinstance(txt, str) else None,
            "valor_numerico": None if isinstance(txt, str) else txt,
            "estado_certificacion_nombre": state,
        }
    return out


API: dict[str, Any] = {
    "/centrales/extended/": [
        {
            "id": 1,
            "nombre": "PE Uno II",
            "tipo_central_nombre": "Eólicas",
            "latitud": -31.3,
            "longitud": -71.6,
            "potencia_maxima": 60,
            "fecha_inicio_operacion": "15-12-2009",
            "region_nombre": "Coquimbo",
            "comuna_nombre": "Canela",
        },
        {
            "id": 2,
            "nombre": "PFV Dos",
            "tipo_central_nombre": "Solares",
            "latitud": -29.1,
            "longitud": -70.9,
            "potencia_maxima": 100,
            "fecha_inicio_operacion": "01-09-2018",
            "region_nombre": "Atacama",
        },
        {
            "id": 3,
            "nombre": "PMGD Tres",
            "tipo_central_nombre": "Solares",
            "latitud": -33.0,
            "longitud": -70.0,
            "potencia_maxima": 3,
        },
        {
            "id": 4,
            "nombre": "TER X",
            "tipo_central_nombre": "Termoeléctricas",
            "latitud": -27.0,
            "longitud": -70.0,
            "potencia_maxima": 90,
        },
        {
            "id": 5,
            "nombre": "PE Sin Coord",
            "tipo_central_nombre": "Eólicas",
            "latitud": None,
            "longitud": None,
            "potencia_maxima": 10,
        },
        {
            "id": 6,
            "nombre": "PE Seis",
            "tipo_central_nombre": "Eólicas",
            "latitud": -30.0,
            "longitud": -71.0,
            "potencia_maxima": 9,
        },
    ],
    "/centrales/1/fichas-tecnicas/general/": _ficha(
        **{
            "11.1.4 Potencia máxima bruta": "59,4",
            "11.1.6 Capacidad máxima, potencia neta efectiva": 58.0,
            "11.1.2 Puntos de conexión al SI": "S/E Uno 220 kV",
            "Rechazada": ("x", "Rechazado"),
        }
    ),
    "/unidades-generadoras/?id_central=1": [{"id": 11}, {"id": 12}],
    "/unidades-generadoras/11/fichas-tecnicas/turbinas-eolicas/": _ficha(
        **{
            "Modelo Turbina": "AW-1500/82",
            "11.1.36 Marca de la turbina": "ACCIONA",
            "11.1.36 Potencia nominal [MW]": 1500.0,
            "12.4.1 Número de turbinas": 20.0,
            "13.3.4 Altura de la torre de cada aerogenerador [m]": "80",
        }
    ),
    "/unidades-generadoras/12/fichas-tecnicas/turbinas-eolicas/": _ficha(
        **{"12.4.1 Número de turbinas": 20.0}
    ),
    "/centrales/2/fichas-tecnicas/general/": {"a": "not a row"},
    "/unidades-generadoras/?id_central=2": {"results": [{"id": 21}]},
    "/unidades-generadoras/21/fichas-tecnicas/generacion-solar/": _ficha(
        **{
            "12.5.2 Número de paneles o módulos solares": "254880",
            "12.5.7 Potencia nominal del panel [W]": 435.0,
            "12.5.3 Número de Inversores": 40.0,
            "12.5.4 Fabricante de la celda FV": "SunPower",
            "12.5.5 Modelo": "SPR-435",
            "12.5.14 Sistema de Seguimiento": "Seguidor 1 eje",
            "13.4.3 Inclinación": "10",
        }
    ),
    "/centrales/6/fichas-tecnicas/general/": [],
    "/unidades-generadoras/?id_central=6": [],
}


def fake_api(url: str) -> bytes:
    path = url.split("/v1", 1)[1]
    if path not in API:
        raise AssertionError(path)
    return json.dumps(API[path]).encode()


def test_helpers() -> None:
    assert cen.plant_key("PE Canela II") == cen.plant_key("PE-CANELA-2") == "canela2"
    assert cen.plant_key("Parque Eólico Totoral") == "totoral"
    assert cen.parse_number("1.234,5") == 1234.5
    assert cen.parse_number("59,4 MW") == 59.4
    assert cen.parse_number(7) == 7.0
    assert cen.parse_number("") is None and cen.parse_number(None) is None
    assert cen.parse_number("n/a") is None
    assert cen.rotor_from_model("Vestas V90-2.0") == 90
    assert cen.rotor_from_model("AW3000/116") == 116
    assert cen.rotor_from_model("SWT-2.3-101") == 101
    assert cen.rotor_from_model("SG 5.0-145") == 145
    assert cen.rotor_from_model("Unknown") is None
    assert cen.rotor_from_model("V20") is None  # out of range
    assert cen.tracking_from_text("Fijo") == "fixed"
    assert cen.tracking_from_text("Tracker 1 eje") == "single-axis"
    assert cen.tracking_from_text("") is None
    assert cen.tracking_from_text("algo") is None
    v = cen.ficha_values(_ficha(**{"A": "1", "B": ("2", "Rechazado")}))
    assert v == {"A": "1"}
    assert "B" in cen.ficha_values(_ficha(**{"B": ("2", "Rechazado")}), False)
    assert cen.find(v, "z", "^a") == "1" and cen.find(v, "z") is None


def test_local_hours_dst() -> None:
    # Chile: daylight saving ended at midnight 2019-04-06/07 (a 25-hour Saturday)
    d = date(2019, 4, 6)
    t1 = cen.local_hour_to_utc(d, 1)
    t25 = cen.local_hour_to_utc(d, 25)
    assert (t25 - t1).total_seconds() == 24 * 3600
    assert cen.local_hour_to_utc(date(2019, 4, 7), 1) == cen.local_hour_to_utc(d, 26)
    assert t1 == datetime(2019, 4, 6, 3)  # UTC-3 in summer
    assert cen.local_hour_to_utc(date(2018, 7, 1), 1) == datetime(2018, 7, 1, 4)


def test_build_inventory_and_coverage(tmp_path: Path) -> None:
    cache = PoliteCache(tmp_path, get=fake_api, pause=0.0)
    client = cen.Infotecnica(cache)
    rows = cen.build_inventory(client, cen.InventoryOptions(min_mw=5.0))
    by = {r["name"]: r for r in rows}
    assert set(by) == {"PE Uno II", "PFV Dos", "PE Seis"}
    w = by["PE Uno II"]
    assert w["pmax_mw"] == 59.4 and w["pmax_net_mw"] == 58.0
    assert w["turbine"] == "ACCIONA AW-1500/82" and w["turbine_mw"] == 1.5
    assert w["n_turbines"] == 40 and w["hub_height"] == 80 and w["rotor_diameter"] == 82
    assert w["commissioning"] == 2009 and w["source"] == "cen"
    assert w["substation"].startswith("S/E")
    s = by["PFV Dos"]
    assert s["n_modules"] == 254880 and s["module_wp"] == 435
    assert s["tracking"] == "single-axis" and "tilt" not in s
    assert s["module"] == "SunPower SPR-435" and s["n_inverters"] == 40
    assert by["PE Seis"]["source"] == "generic"
    cov = cen.coverage(rows)
    assert cov["wind"]["plants"] == 2
    assert cov["wind"]["share_mw_with_model"] == pytest.approx(59.4 / 68.4, abs=1e-3)
    assert cov["pv"]["fields"]["module"] == 1
    # the inventory feeds the sizing: 40 x 1.5 MW against 59.4 MW
    p = pl.plant_from_row(w, 1)
    sz = pl.size_wind(p, str(p.get("turbine")), 0.02)
    assert sz.source == "cen" and sz.installed_ratio == pytest.approx(60 / 59.4)
    sel = cen.build_inventory(
        client,
        cen.InventoryOptions(types=("pv",), equipment=False, bbox=(-30, -28, -72, -70)),
    )
    assert [r["name"] for r in sel] == ["PFV Dos"]
    assert (
        cen.build_inventory(
            client, cen.InventoryOptions(regions=("coquimbo",), equipment=False)
        )[0]["name"]
        == "PE Uno II"
    )
    assert (
        cen.build_inventory(
            client, cen.InventoryOptions(names=("Seis",), equipment=False)
        )[0]["name"]
        == "PE Seis"
    )


def _gen_rows() -> list[list[Any]]:
    head = (
        ["NOMBRE CENTRAL", "LLAVE NOMBRE", "TIPO", "FECHA"]
        + [f"HORA {h}" for h in range(1, 26)]
        + ["TOTAL"]
    )
    day1 = ["PE UNO II", "PE Uno II", "Eólica", 43561] + [1.0] * 25 + [25]
    day2 = ["PE UNO II", "x", "Eólica", "2019-04-07"] + [2.0] * 24 + [None, 48]
    other = ["TER X", "TER X", "Térmica", 43561] + [9.0] * 24 + [None, 0]
    hydro = ["HP UNO II", "UNO II", "Hidráulica", 43561] + [7.0] * 24 + [None, 0]
    bad = ["PFV DOS", "", "Solar", "??"] + [1.0] * 24 + [None, 0]
    return [["Notas"], head, day1, day2, other, hydro, bad]


def test_parse_generation() -> None:
    wanted = {cen.plant_key("PE Uno II"): "W", cen.plant_key("PFV Dos"): "S"}
    rows = cen.parse_generation_rows(
        _gen_rows(), wanted, types={"W": "wind", "S": "pv"}
    )
    w = [r for r in rows if r[1] == "W"]
    # without the technology check the hydro plant of the same name leaks in
    loose = cen.parse_generation_rows(_gen_rows(), wanted)
    assert len([r for r in loose if r[1] == "W"]) == 49 + 24
    # 2019-04-06 is the 25-hour day: 25 values, then 24 on the next day
    assert len(w) == 49
    times = [r[0] for r in w]
    assert times == sorted(times) and len(set(times)) == 49
    assert w[0][0] == datetime(2019, 4, 6, 3) and w[-1][2] == 2.0
    spanish = [
        ["central_name", "llave_nombre", "_ops_por_hora_new.fecha"]
        + [str(h) for h in range(1, 26)],
        ["PE UNO II", "", "05-ene"] + [3.0] * 24,
    ]
    r2 = cen.parse_generation_rows(spanish, wanted, year=2024)
    assert len(r2) == 24 and r2[0][0] == datetime(2024, 1, 5, 3)
    assert not cen.parse_generation_rows(spanish, wanted)  # no year


def _curt_rows() -> list[list[Any]]:
    return [
        [None, "CURTAILMENTS SOLAR"],
        [None, datetime(2025, 12, 1)],
        [None, "Central/Hora", None, None] + list(range(1, 25)) + ["Total"],
        [None, "PFV-DOS", None, None] + [0] * 12 + [5.5] * 4 + [0] * 8 + [22],
        [None, "PFV-OTRA", None, None] + [1] * 24 + [24],
        [None, "Total", None, None] + [1] * 24 + [24],
    ]


def test_parse_curtailment_and_long() -> None:
    wanted = {cen.plant_key("PFV Dos"): "S"}
    rows = cen.parse_curtailment_rows([[None]] + _curt_rows(), wanted)
    assert len(rows) == 24 and sum(r[2] for r in rows) == 22
    assert rows[12][0] == datetime(2025, 12, 1, 15)  # hour 13 local, UTC-3
    long = [
        ["Fecha", "Nombre", "Valor"],
        ["2023-08-01 00:00:00", "PFV DOS", "4,5"],
        [datetime(2023, 8, 1, 1), "PFV DOS", 5.0],
        ["2023-08-01 02:00:00", "PE OTRO", 1.0],
        [None, "PFV DOS", 2.0],
    ]
    out = cen.parse_long_rows([["junk"]] + long, wanted)
    assert [r[2] for r in out] == [4.5, 5.0]
    assert out[0][0] == datetime(2023, 8, 1, 4)
    real = [
        ["Valor", "Nombre", "Fecha", "Hora"],
        [43.3, "PFV DOS", "2023-08-01 00:00:00", 2],
    ]
    assert cen.parse_long_rows(real, wanted)[0][0] == datetime(2023, 8, 1, 5)
    html = (
        '<a href="https://www.coordinador.cl/wp-content/uploads/2026/01/R.xlsx">'
        '<a href="https://www.coordinador.cl/wp-content/uploads/2026/01/R.xlsx">'
        '<a href="https://www.coordinador.cl/wp-content/uploads/x.pdf">'
    )
    assert cen.scrape_links(html) == [
        "https://www.coordinador.cl/wp-content/uploads/2026/01/R.xlsx"
    ]


def test_cli_inventory_fetch(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    cache = tmp_path / "cache"
    PoliteCache(cache, get=fake_api, pause=0.0)
    # pre-seed the cache: the CLI never reaches the network
    client = cen.Infotecnica(PoliteCache(cache, get=fake_api, pause=0.0))
    cen.build_inventory(client, cen.InventoryOptions(min_mw=5.0))
    inv = tmp_path / "inv.csv"
    assert (
        main(["inventory", "--cache-dir", str(cache), "--min-mw", "5", "-o", str(inv)])
        == 0
    )
    assert (
        json.loads(Path(str(inv) + ".coverage.json").read_text(encoding="utf-8"))[
            "wind"
        ]["plants"]
        == 2
    )
    assert (
        main(
            [
                "inventory",
                "--cache-dir",
                str(cache),
                "--types",
                "pv",
                "--equipment-source",
                "generic",
                "--bbox=-28,-30,-70,-72",
                "-o",
                str(tmp_path / "inv.json"),
            ]
        )
        == 0
    )
    plants = pl.read_inventory(inv)
    assert {p.name for p in plants} == {"PE Uno II", "PFV Dos", "PE Seis"}
    gen = tmp_path / "gen.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2016 al 2019"
    for r in _gen_rows():
        ws.append(r)
    wb.save(gen)
    cur = tmp_path / "cur.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Resumen-Mensual"
    ws = wb.create_sheet("Resumen-DiarioHorario-Solar")
    for r in _curt_rows():
        ws.append(r)
    wb.save(cur)
    fc = tmp_path / "fc.csv"
    fc.write_text("Fecha,Nombre,Valor\n2023-08-01 00:00:00,PFV DOS,4.5\n")
    nm = tmp_path / "map.csv"
    nm.write_text("plant,cen_name\nPE Seis,PE SEIS NUEVO\n")
    out = tmp_path / "obs.parquet"
    assert (
        main(
            [
                "fetch-cen",
                "--plants",
                str(inv),
                "--cache-dir",
                str(cache),
                "--generation",
                str(gen),
                "--curtailment",
                str(cur),
                "--forecast",
                str(fc),
                "--name-map",
                str(nm),
                "-o",
                str(out),
            ]
        )
        == 0
    )
    df = pd.read_parquet(out)
    assert {"time", "plant", "mwh", "curtailed_mwh", "forecast_mwh"} <= set(df.columns)
    w = df[df["plant"] == "PE Uno II"]
    assert w["mwh"].sum() == pytest.approx(25 + 48)
    assert w["time"].min() == pd.Timestamp("2019-04-06 03:00")  # UTC
    assert df["curtailed_mwh"].sum() == pytest.approx(22)
    # URLs and index keys go through the cache
    seeded = PoliteCache(cache, get=lambda u: gen.read_bytes(), pause=0.0)
    seeded.fetch("cen/" + cen.CEN_GEN_FILES["2024"].rsplit("/", 1)[-1], "x")
    assert (
        main(
            [
                "fetch-cen",
                "--plants",
                str(inv),
                "--cache-dir",
                str(cache),
                "--generation",
                "2024",
                "-o",
                str(tmp_path / "o2.csv"),
            ]
        )
        == 0
    )
