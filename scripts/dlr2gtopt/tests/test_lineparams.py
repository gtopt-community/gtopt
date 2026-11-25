# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Line parameters: CEN technical sheet, conductor library, per-kV defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dlr2gtopt import _lineparams as lp
from dlr2gtopt.__main__ import main


def ficha(**vals: Any) -> dict[str, Any]:
    """A synthetic ficha reply in the Infotecnica layout."""
    names = {
        "kv": ("1.1 Tensión nominal", "[kV]"),
        "r20": ("1.3 Resistencia de secuencia positiva a 20°C (50 Hz)", "[Ω/km]"),
        "temp": ("1.9 Temperatura de diseño del conductor en régimen permanente", "°C"),
        "limit": ("1.9 Límite térmico permanente en verano con sol 25°C", "[kA]"),
        "sec": ("1.15 Sección del conductor", "[mm²]"),
        "type": ("1.16 Tipo de conductor (ACSR, ACAR, entre otros)", "-----"),
        "n": ("1.17 Número de conductores por fase", "-----"),
    }
    out: dict[str, Any] = {"row_header_11": {"nombre_concepto": None}}
    for k, (name, unit) in names.items():
        if k not in vals:
            continue
        v = vals[k]
        out[k] = {
            "nombre_concepto": name,
            "valor_texto": v if isinstance(v, str) else "",
            "valor_numerico": None if isinstance(v, str) else v,
            "unidad_nemo": unit,
            "estado_certificacion_nombre": "Certificado",
        }
    return out


def test_parse_ficha_general() -> None:
    f = lp.parse_ficha_general(
        ficha(
            kv="220",
            r20="0,08890052",
            temp="55",
            sec="375,4",
            type="AAAC FLINT -----",
            n="1",
        )
    )
    assert f == {
        "voltage_kv": 220.0,
        "r20_ohm_km": pytest.approx(0.08890052),
        "design_temp": 55.0,
        "section_mm2": pytest.approx(375.4),
        "conductor_type": "AAAC FLINT",
        "subconductors": 1,
    }
    rejected = ficha(kv=220.0, temp="No aplica")
    rejected["kv"]["estado_certificacion_nombre"] = "Rechazado"
    rejected["junk"] = "x"
    assert not lp.parse_ficha_general(rejected)


def test_resolve_from_cen_and_library() -> None:
    d = lp.load_defaults()
    f = lp.parse_ficha_general(
        ficha(kv=220.0, r20=0.0889, temp=55.0, type="AAAC FLINT", n=1)
    )
    p = lp.resolve(f, d)
    assert p.diameter == pytest.approx(0.02517)
    assert p.r20 == pytest.approx(0.0889e-3)
    assert p.alpha_r == pytest.approx(lp.ALPHA_AAAC)
    assert p.tmax_conductor == pytest.approx(55.0)
    assert p.source["diameter"] == "conductor-library"
    assert "conductor_height" in p.uses_assumptions()
    assert "diameter" not in p.uses_assumptions()
    c = p.conductor()
    assert c.subconductors == 1


def test_resolve_estimates_and_defaults() -> None:
    d = lp.load_defaults()
    f = lp.parse_ficha_general(
        ficha(kv=500.0, sec=380.0, type="ACAR 750 MCM", n=4, r20=0.02)
    )
    p = lp.resolve(f, d)
    assert p.source["diameter"] == "estimated"
    assert p.diameter == pytest.approx(
        1.15 * (4 * 380 / 3.141592653589793) ** 0.5 / 1000
    )
    assert p.r20 == pytest.approx(0.02 * 4 / 1000)
    assert p.alpha_r == pytest.approx(lp.ALPHA_AL)
    assert p.conductor_height == pytest.approx(24.0)
    bare = lp.resolve(
        None, d, kv=230.0, overrides={"conductor_height": 18.0, "tmax_conductor": None}
    )
    assert bare.source["voltage_kv"] == "option"
    assert bare.conductor_height == pytest.approx(18.0)
    assert bare.source["conductor_height"] == "option"
    assert bare.source["diameter"] == "default"
    assert set(bare.uses_assumptions()) >= {"diameter", "r20", "tmax_conductor"}
    named = lp.resolve(None, lp.load_defaults(), kv=220.0)
    assert named.source["diameter"] == "default"  # library name in the default type
    with pytest.raises(ValueError, match="voltage"):
        lp.resolve({}, d)


def test_helpers(tmp_path: Path) -> None:
    assert lp.conductor_family("ACSR Drake") == "acsr"
    assert lp.conductor_family("cobre") == ""
    assert lp.library_diameter("2 x acsr rail") == pytest.approx(0.02959)
    assert lp.library_diameter("unknown") is None
    custom = {
        "classes": {
            "110": {**lp.load_defaults()["classes"]["220"], "conductor_height": 12.0}
        }
    }
    (tmp_path / "d.json").write_text(json.dumps(custom), encoding="utf-8")
    d = lp.load_defaults(tmp_path / "d.json")
    assert lp.voltage_class(d, 66.0)["conductor_height"] == pytest.approx(12.0)
    p = lp.resolve(None, d, kv=110.0)
    (tmp_path / "p.json").write_text(json.dumps(p.to_json()), encoding="utf-8")
    assert lp.read_line_params(tmp_path / "p.json").conductor_height == pytest.approx(
        12.0
    )


def test_cli_line_params_and_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dlr2gtopt import _towers as tw
    from dlr2gtopt._spans import read_spans, write_towers
    from dlr2gtopt.tests._synthetic import make_towers, make_weather

    (tmp_path / "f.json").write_text(
        json.dumps(ficha(kv=220.0, r20=0.0889, temp=55.0, type="AAAC FLINT", n=1)),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "line-params",
                "--ficha",
                str(tmp_path / "f.json"),
                "-o",
                str(tmp_path / "lp.json"),
            ]
        )
        == 0
    )
    monkeypatch.setattr(
        tw, "urllib_get", lambda _u: json.dumps(ficha(kv=500.0)).encode()
    )
    assert (
        main(
            [
                "line-params",
                "--section-id",
                "7",
                "--cache-dir",
                str(tmp_path / "c"),
                "--pause",
                "0",
                "--conductor-height",
                "30",
                "-o",
                str(tmp_path / "lp5.json"),
            ]
        )
        == 0
    )
    assert json.loads((tmp_path / "lp5.json").read_text())["conductor_height"] == 30.0
    assert main(["line-params", "--kv", "220", "-o", str(tmp_path / "lpk.json")]) == 0
    write_towers(tmp_path / "t.csv", make_towers(6))
    assert main(["spans", str(tmp_path / "t.csv"), "-o", str(tmp_path / "s.csv")]) == 0
    make_weather(read_spans(tmp_path / "s.csv"), hours=24).write(tmp_path / "w.csv")
    common = ["--spans", str(tmp_path / "s.csv"), "--weather", str(tmp_path / "w.csv")]
    assert (
        main(
            [
                "ampacity",
                *common,
                "--line-params",
                str(tmp_path / "lp.json"),
                "-o",
                str(tmp_path / "a.csv"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "ampacity",
                *common,
                "--line-params",
                str(tmp_path / "lp.json"),
                "--r25",
                "7e-5",
                "--r75",
                "8.5e-5",
                "--emissivity",
                "0.8",
                "-o",
                str(tmp_path / "a2.csv"),
            ]
        )
        == 0
    )
    assert main(["ampacity", *common, "-o", str(tmp_path / "a3.csv")]) == 2
    assert (
        main(
            [
                "emit",
                *common,
                "--line-params",
                str(tmp_path / "lp.json"),
                "--format",
                "csv",
                "-o",
                str(tmp_path / "case"),
            ]
        )
        == 0
    )
    dlr = json.loads((tmp_path / "case" / "dlr_line1.json").read_text())["dlr"]
    assert dlr["tmax_conductor"] == 55.0 and dlr["voltage"] == 220.0
    assert (
        main(
            [
                "weather",
                "--spans",
                str(tmp_path / "s.csv"),
                "--source",
                "csv",
                "--file",
                str(tmp_path / "w.csv"),
                "--line-params",
                str(tmp_path / "lp.json"),
                "-o",
                str(tmp_path / "w2.csv"),
            ]
        )
        == 0
    )
