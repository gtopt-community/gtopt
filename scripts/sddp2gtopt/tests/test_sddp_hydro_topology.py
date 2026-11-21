"""PSR ``chidro`` cascade topology: TAA (turbined water) vs VAA (spilled).

PSR SDDP gives every hydro plant two downstream codes: ``TAA``
(*turbinamiento aguas abajo*) receives the water released through the
turbines, ``VAA`` (*vertimiento aguas abajo*) the spilled water; a blank
code sends that water out of the system.  Plants never in service in the
horizon are not modelled, but water reaching them must continue down the
river (their ``VAA``, else ``TAA``).

The synthetic cascade (fixture ``sddp_block_case`` with a replaced
``chidrott.dat``) mirrors the Peruvian cases that motivated the fix:

* ``HeadRes`` (turbine-less reservoir, like Sibinacocha) → ``Mid``;
* ``Mid`` turbines into ``FutA`` (never built) and spills into ``FutB``
  (never built); both continue to ``Machu``;
* ``Machu`` turbines into ``StaTer`` (TAA) and has no spill destination
  (VAA blank → its spill leaves the system), like Machupicchu;
* ``Yurac`` (turbine-less reservoir) releases into ``Huanch`` (TAA) and
  spills into ``StaTer`` (VAA), like Yuracmayo → Huanchor;
* ``StaTer`` is terminal (both blank).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sddp2gtopt.sddp_case import load_sddp_block_case
from sddp2gtopt.sddp_case_writer import build_sddp_planning

CASE = Path(__file__).parent / "data" / "sddp_block_case"

_HEADER = (
    "!NUM ...Nombre... .PV. .VAA .TAA #Uni Tipo ....Pot .FPMed. .QMin.. "
    ".QMax.. .VMin.. .VMax.. .VInic. Min.V+T ..VNC.. ..ICP.. ...IH.."
)

# code, name, station, VAA, TAA, tipo, pot, fp, qmax, vmin, vmax
_PLANTS = [
    (9201, "HeadRes", 701, 9202, 9202, 0, "0.", "0.", "10.", "0.", "100."),
    (9202, "Mid", 702, 9204, 9203, 0, "10.", "1.0", "20.", "0.", "0."),
    (9203, "FutA", 703, 9205, 9205, 1, "20.", "2.0", "20.", "0.", "0."),
    (9204, "FutB", 0, 9205, 9205, 1, "20.", "2.0", "20.", "0.", "0."),
    (9205, "Machu", 0, "", 9206, 0, "100.", "3.0", "40.", "0.", "0."),
    (9206, "StaTer", 0, "", "", 0, "50.", "1.7", "40.", "0.", "0."),
    (9207, "Yurac", 0, 9206, 9208, 0, "0.", "0.", "10.", "5.", "50."),
    (9208, "Huanch", 0, 9206, 9206, 0, "20.", "1.8", "11.", "0.", "0."),
]
_GEN = ("Mid", "FutA", "FutB", "Machu", "StaTer", "Huanch")


def _row(values: list[object]) -> str:
    """Right-align each value at the end column of its header token."""
    import re

    ends = [m.end() for m in re.finditer(r"\S+", _HEADER)]
    buf = [" "] * (ends[-1] + 1)
    for i, v in enumerate(values):
        s = str(v)
        if i == 1:  # name: left-justified after NUM
            buf[5 : 5 + len(s)] = list(s)
            continue
        buf[ends[i] - len(s) : ends[i]] = list(s)
    return "".join(buf).rstrip()


@pytest.fixture(name="planning")
def fixture_planning(tmp_path: Path) -> dict:
    d = tmp_path / "case"
    shutil.copytree(CASE, d)
    rows = [_HEADER]
    for code, name, pv, vaa, taa, tipo, pot, fp, qmax, vmin, vmax in _PLANTS:
        rows.append(
            _row(
                [
                    code,
                    name,
                    pv or "",
                    vaa,
                    taa,
                    1,
                    tipo,
                    pot,
                    fp,
                    "0.",
                    qmax,
                    vmin,
                    vmax,
                    "0.5",
                    "0.",
                    0,
                    "0.",
                    "0.",
                ]
            )
        )
    (d / "chidrott.dat").write_text("\n".join(rows) + "\n", encoding="latin-1")
    # generators of the new plants on bus 2 (dbus tg = 1 → hydro)
    extra = "".join(
        f"    2  0 CENTRO220       {i + 3:2d}  1 {code} {name:<15s} 1\n"
        for i, (code, name, *_r) in enumerate(_PLANTS)
        if name in _GEN
    )
    dbus = (d / "dbus.dat").read_text(encoding="latin-1")
    dbus = "\n".join(
        ln for ln in dbus.splitlines() if " 9101 " not in ln and " 9102 " not in ln
    )
    (d / "dbus.dat").write_text(dbus + "\n" + extra, encoding="latin-1")
    for f in d.glob("rgme*"):
        f.unlink()
    for f in d.glob("vminme*"):
        f.unlink()
    case = load_sddp_block_case(d)
    planning, _sink = build_sddp_planning(case, name="topo")
    return planning


def _by_name(planning: dict, arr: str) -> dict[str, dict]:
    return {e["name"]: e for e in planning["system"].get(arr, [])}


def test_future_plants_not_modelled(planning) -> None:
    j = _by_name(planning, "junction_array")
    assert "j_FutA" not in j and "j_FutB" not in j
    assert {"j_HeadRes", "j_Mid", "j_Machu", "j_StaTer", "j_Yurac", "j_Huanch"} <= set(
        j
    )


def test_turbine_follows_taa(planning) -> None:
    """Machupicchu-like: TAA = next plant, VAA blank."""
    tb = _by_name(planning, "turbine_array")
    assert tb["tb_Machu"]["junction_b"] == "j_StaTer"
    assert tb["tb_Huanch"]["junction_b"] == "j_StaTer"


def test_terminal_plant_turbines_and_spills_to_sea(planning) -> None:
    tb = _by_name(planning, "turbine_array")
    j = _by_name(planning, "junction_array")
    ww = _by_name(planning, "waterway_array")
    assert "junction_b" not in tb["tb_StaTer"]
    assert j["j_StaTer"].get("drain") is True
    assert "s_StaTer" not in ww
    # Machu: no spill destination → its spill leaves the system (drain), and
    # there is exactly one path to sea (no spill waterway as well).
    assert j["j_Machu"].get("drain") is True
    assert "s_Machu" not in ww


def test_split_taa_vaa(planning) -> None:
    """Yuracmayo-like: releases to Huanchor (TAA), spills past it (VAA)."""
    ww = _by_name(planning, "waterway_array")
    j = _by_name(planning, "junction_array")
    assert ww["q_Yurac"]["junction_b"] == "j_Huanch"
    assert ww["s_Yurac"]["junction_b"] == "j_StaTer"
    assert not j["j_Yurac"].get("drain")  # spill stays in the cascade


def test_chain_through_inactive_plants(planning) -> None:
    """Sibinacocha-like: head reservoir → Mid → (never-built FutA/FutB) →
    Machu → StaTer: the cascade is not cut by plants absent from the horizon."""
    ww = _by_name(planning, "waterway_array")
    tb = _by_name(planning, "turbine_array")
    assert ww["q_HeadRes"]["junction_b"] == "j_Mid"
    assert ww["s_HeadRes"]["junction_b"] == "j_Mid"
    assert tb["tb_Mid"]["junction_b"] == "j_Machu"  # TAA FutA → its VAA → Machu
    assert ww["s_Mid"]["junction_b"] == "j_Machu"  # VAA FutB → Machu
    # follow turbines/release arcs from the head reservoir down to the sea
    nxt: dict[str, str | None] = {}
    for t in tb.values():
        nxt[t["junction_a"]] = t.get("junction_b")
    for w in ww.values():
        if w["name"].startswith("q_"):
            nxt.setdefault(w["junction_a"], w.get("junction_b"))
    path, node = [], "j_HeadRes"
    while node is not None and node not in path:
        path.append(node)
        node = nxt.get(node)
    assert path == ["j_HeadRes", "j_Mid", "j_Machu", "j_StaTer"]
