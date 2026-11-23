# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt.marginal_units` (no network).

A small synthetic "CEN world" (five plants, a handful of buses, one
day of 15-min CMG) is served by a fake ``fetch_by_name`` and a fake
SCVIC ``cv_rpt_for_date``; PID / PLEXOS / accdb readers are injected
through the modules they are imported from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import pcp_inputs, pcp_solution
from cen2gtopt import marginal_units as mu
from cen2gtopt._emission_factors import UpstreamBasis

#: The fetch layer is faked, so no real client is ever needed.
NO_CLIENT: Any = None

# ---------------------------------------------------------------------------
# Synthetic CEN world
# ---------------------------------------------------------------------------

#: id_central, name, tipo, pmin, pmax, fuel, SCVIC CV options
PLANTS: list[tuple[int, str, str, str, str, str, list[float]]] = [
    (101, "TER GASCITY", "Termoeléctrica", "50", "300", "Gas Natural", [60, 75, 10]),
    (102, "TER CARBONIA", "Termoeléctrica", "100", "200", "Carbón", [45]),
    (103, "HE LAGUNA", "Hidroeléctrica de embalse", "0", "500", "", []),
    (104, "PFV SOLARIS", "Solar", "0", "100", "Solar", [16]),
    (105, "TER DIESELO", "Termoeléctrica", "20", "100", "Diésel", [150]),
]

#: bar_transf → (id_info, label, multiplicative factor, additive offset,
#: last published quarter)
BUSES: dict[str, tuple[int, str, float, float, int]] = {
    "CHARRUA_______220": (2, "CHARRÚA 220KV (south-centre)", 1.0, 0.0, 95),
    "CRUCERO_______220": (10, "CRUCERO 220KV (north)", 1.02, 0.0, 95),
    "A.MELIP_______220": (1, "ALTO MELIPILLA 220KV (Santiago)", 1.0, 140.0, 20),
}


def _base_lmp(hour: int) -> float:
    if hour < 6:
        return 0.3  # renewable curtailment
    if hour < 12:
        return 60.0  # gas tier
    if hour < 18:
        return 45.0  # coal tier
    if hour < 22:
        return 900.0  # demand fail
    return 54.0  # closest CV above λ (gas @ 60)


@dataclass
class World:
    """Knobs for the fake CEN endpoints."""

    flat_lmp: float | None = None
    empty: set[str] = field(default_factory=set)
    raising: set[str] = field(default_factory=set)
    scvic_empty: bool = False
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def lmp(self, hour: int, factor: float, offset: float) -> float:
        if self.flat_lmp is not None:
            return self.flat_lmp * factor
        return _base_lmp(hour) * factor + offset

    def cmg(self, kind: str, bar_transf: str | None) -> pd.DataFrame:
        targets = [bar_transf] if bar_transf else list(BUSES)
        rows = []
        for bt in targets:
            if bt not in BUSES:
                continue
            iid, label, fac, off, last_q = BUSES[bt]
            first_q = 10 if kind == "cmg_real" else 0
            for q in range(first_q, last_q + 1):
                h, m = divmod(q, 4)
                row = {
                    "fecha": "2026-04-22 00:00:00",
                    "hra": h,
                    "min": m * 15,
                    "barra_info": label,
                    "barra_transf": bt,
                    "id_info": iid,
                    "cmg_usd_mwh_": self.lmp(h, fac, off),
                }
                if kind == "cmg_online":
                    row["version"] = "REAL-PRE"
                rows.append(row)
        return pd.DataFrame(rows)

    def fetch(self, _client: Any, name: str, **kw: Any) -> pd.DataFrame:
        self.calls.append((name, kw))
        if name in self.raising:
            raise RuntimeError(f"{name} upstream error")
        if name in self.empty:
            return pd.DataFrame()
        bt = (kw.get("extra_params") or {}).get("bar_transf")
        if name in ("cmg_real", "cmg_online"):
            return self.cmg(name, bt)
        builder = getattr(self, f"_ep_{name}")
        return builder()

    def _ep_generacion_real(self) -> pd.DataFrame:
        rows = []
        for h in range(24):
            mw = {101: 150.0, 102: 200.0, 103: 100.0, 105: 20.0}
            mw[104] = 50.0 if 8 <= h <= 17 else 0.0
            for ic, name, *_rest in PLANTS:
                rows.append(
                    {
                        "hora": h + 1,
                        "central": name,
                        "gen_real_mw": mw[ic],
                        "potencia_maxima": float(dict(_pmax())[ic]),
                    }
                )
            rows.append(
                {
                    "hora": h + 1,
                    "central": "UNKNOWN PLANT",
                    "gen_real_mw": 10.0,
                    "potencia_maxima": 10.0,
                }
            )
        return pd.DataFrame(rows)

    def _ep_instrucciones_cmg(self) -> pd.DataFrame:
        rows = []
        for h in range(24):
            rows.append(
                {
                    "hora": f"{h:02d}:00",
                    "central": "TER DIESELO",
                    "id_central": 105,
                    "estado": "RO",
                    "consigna": "N",
                    "zona_desaclope": "1",
                }
            )
            rows.append(
                {
                    "hora": f"{h:02d}:00",
                    "central": "MYSTERY UNIT",
                    "id_central": 999,
                    "estado": "N",
                    "consigna": "CI",
                    "zona_desaclope": "2" if h == 3 else "1",
                }
            )
        return pd.DataFrame(rows)

    def _ep_unidades_generadoras(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "id_central": ic,
                    "central": name,
                    "unidad_nombre": name,
                    "unidad_nemotecnico": f"U{ic}",
                    "pot_min_tecnica": pmin,
                    "pot_neta_efectiva": pmax,
                    "pot_max_bruta": pmax,
                    "tipo_tecnologia_unidad": tipo,
                }
                for ic, name, tipo, pmin, pmax, _f, _cv in PLANTS
            ]
        )

    def _ep_limitaciones(self) -> pd.DataFrame:
        return pd.DataFrame({"instalacion_nombre": ["LINEA A", "LINEA A", "LINEA B"]})

    def _ep_generacion_programada_pcp(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "fecha_hora": ["2026-04-22 01:00:00", "bad"],
                "gen_programada_mw": ["10", "5"],
                "id_central": ["101", "102"],
            }
        )

    def _ep_cmg_programado_pcp(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"fecha_hora": ["2026-04-22 02:00:00"], "cmg_usd_mwh": ["55.5"]}
        )

    def _ep_flujo_programado_pcp(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"fecha_hora": ["2026-04-22 03:00:00", None], "flujo_mw": [12.0, 3.0]}
        )

    def _ep_embalse_real(self) -> pd.DataFrame:
        return pd.DataFrame({"nombre_embalse": ["LAGUNA", "LAGUNA"]})

    def scvic(self, _date: str) -> pd.DataFrame:
        if self.scvic_empty:
            return pd.DataFrame()
        rows = []
        for _ic, name, _t, _pmin, _pmax, fuel, cvs in PLANTS:
            for cv in cvs:
                rows.append(
                    {
                        "central_name": name,
                        "unidad_name": None,
                        "configuracion": "",
                        "tipo_combustible": fuel,
                        "costo_variable_total": float(cv),
                    }
                )
        rows.append(
            {
                "central_name": "GHOST",
                "unidad_name": "",
                "configuracion": "",
                "tipo_combustible": "Carbón",
                "costo_variable_total": 50.0,
            }
        )
        rows.append(
            {
                "central_name": "TER GASCITY",
                "unidad_name": "",
                "configuracion": "",
                "tipo_combustible": "Gas Natural",
                "costo_variable_total": 5000.0,
            }
        )
        return pd.DataFrame(rows)


def _pmax() -> list[tuple[int, str]]:
    return [(ic, pmax) for ic, _n, _t, _pmin, pmax, _f, _cv in PLANTS]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    monkeypatch.setattr(mu, "fetch_by_name", w.fetch)
    monkeypatch.setattr(mu, "cv_rpt_for_date", w.scvic)
    return w


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_resolve_epsilon_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    base = mu.FUEL_EF["Carbón"]
    assert mu._resolve_epsilon("Solar", UpstreamBasis.MIXED) == 0.0
    assert mu._resolve_epsilon("Carbón", UpstreamBasis.NONE) == base
    assert mu._resolve_epsilon("Carbón", UpstreamBasis.MIXED) > base
    monkeypatch.setattr(mu, "lookup_scvic", lambda _f, _b: None)
    assert mu._resolve_epsilon("Carbón", UpstreamBasis.MIXED) == base
    monkeypatch.setattr(
        mu,
        "lookup_scvic",
        lambda _f, _b: SimpleNamespace(ttw_kg_per_gj=0.0, wtt_kg_per_gj=1.0),
    )
    assert mu._resolve_epsilon("Carbón", UpstreamBasis.MIXED) == base


def test_to_float_es_branches() -> None:
    assert mu._to_float_es(3) == 3.0
    assert mu._to_float_es("1.234,5") == 1234.5
    assert mu._to_float_es("12,5") == 12.5
    assert pd.isna(mu._to_float_es("   "))


def test_build_unit_metadata_modes() -> None:
    units = pd.DataFrame(
        {
            "id_central": [1, 1, "x"],
            "pot_min_tecnica": ["10", "5", "1"],
            "min_tecnico_normativa_ambiental": ["30", "No Aplica", "1"],
            "pot_neta_efectiva": [None, "100", "1"],
            "pot_max_bruta": ["50", "120", "1"],
        }
    )
    pmin, pmax, tipo = mu.build_unit_metadata(units, pmin_mode="binding")
    assert pmin == {1: 35.0}
    assert pmax == {1: 150.0}
    assert tipo == {1: ""}
    with pytest.raises(ValueError, match="pmin_mode"):
        mu.build_unit_metadata(units, pmin_mode="bogus")
    bare = pd.DataFrame({"id_central": [2], "pot_max_bruta": ["7"]})
    assert mu.build_unit_metadata(bare, pmin_mode="binding")[0] == {2: 0.0}


# ---------------------------------------------------------------------------
# Fetch wrappers
# ---------------------------------------------------------------------------


def test_simple_fetch_wrappers(world: World) -> None:
    assert len(mu.fetch_limitaciones(NO_CLIENT)) == 3
    assert len(mu.fetch_embalse_real(NO_CLIENT)) == 2
    assert len(mu.fetch_unidades_generadoras(NO_CLIENT)) == len(PLANTS)
    gen = mu.fetch_generacion_real(NO_CLIENT, date="2026-04-22")
    assert gen["hour"].min() == 0 and gen["hour"].max() == 23
    instr = mu.fetch_instrucciones(NO_CLIENT)
    assert set(instr["hour"]) == set(range(24))
    pcp = mu.fetch_gen_programada_pcp(NO_CLIENT)
    assert list(pcp["hour"]) == [1]
    assert world.calls[0] == ("limitaciones", {"start": mu.DATE})


@pytest.mark.parametrize(
    "name",
    [
        "generacion_real",
        "instrucciones_cmg",
        "generacion_programada_pcp",
    ],
)
def test_fetch_wrappers_empty(world: World, name: str) -> None:
    world.empty.add(name)
    fn = {
        "generacion_real": mu.fetch_generacion_real,
        "instrucciones_cmg": mu.fetch_instrucciones,
        "generacion_programada_pcp": mu.fetch_gen_programada_pcp,
    }[name]
    assert fn(NO_CLIENT).empty


def test_instrucciones_helpers_edge_cases() -> None:
    bad = pd.DataFrame(
        {"hour": [1, "x"], "id_central": [5, 6], "consigna": ["CI", "MT"]}
    )
    lookup = mu.build_instrucciones_lookup(bad)
    assert lookup == {(1, 5): {"consigna": "CI"}}
    blank = pd.DataFrame({"hour": [1], "zona_desaclope": [" "]})
    assert mu.system_zonas_desaclope_per_hour(blank) == {}


def test_try_fetch_cmg(world: World) -> None:
    df = mu._try_fetch_cmg(NO_CLIENT, "cmg_real", "CHARRUA_______220")
    assert set(df["source"]) == {"cmg_real"}
    assert df["quarter"].min() == 10
    assert mu._try_fetch_cmg(NO_CLIENT, "cmg_real", "NOBUS").empty
    world.raising.add("cmg_online")
    assert mu._try_fetch_cmg(NO_CLIENT, "cmg_online", "CHARRUA_______220").empty


@pytest.mark.parametrize("source", ["real", "online", "best_real"])
def test_fetch_cmg_real_policies(
    world: World, source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    buses = [
        ("CHARRUA_______220", 2, "Charrua"),
        ("NOBUS_________220", 99, "Nowhere"),
    ]
    df = mu.fetch_cmg_real(NO_CLIENT, ref_buses=buses, cmg_source=source)
    assert sorted(df["quarter"]) == list(range(96))
    assert set(df["bus_uid"]) == {2}
    src = df.set_index("quarter")["source"]
    if source == "real":
        assert src[0] == "extrapolated" and src[50] == "cmg_real"
    elif source == "online":
        assert set(src) == {"cmg_online"}
    else:
        assert src[0] == "cmg_online" and src[50] == "cmg_real"
    out = capsys.readouterr().out
    assert "skip Nowhere" in out


def test_fetch_cmg_real_best_real_fallbacks(world: World) -> None:
    buses = [("CHARRUA_______220", 2, "Charrua")]
    world.empty.add("cmg_real")
    df = mu.fetch_cmg_real(NO_CLIENT, ref_buses=buses, verbose=False)
    assert set(df["source"]) == {"cmg_online"}
    world.empty = {"cmg_online"}
    df = mu.fetch_cmg_real(NO_CLIENT, ref_buses=buses, verbose=False)
    assert set(df["source"]) == {"cmg_real", "extrapolated"}
    world.empty = {"cmg_online", "cmg_real"}
    assert mu.fetch_cmg_real(NO_CLIENT, ref_buses=buses, verbose=False).empty


# ---------------------------------------------------------------------------
# PID / PLEXOS loss-factor readers
# ---------------------------------------------------------------------------


@dataclass
class _Inputs:
    is_pid: bool = True
    nod_loss: pd.DataFrame = field(default_factory=pd.DataFrame)
    nod_load_long: pd.DataFrame = field(default_factory=pd.DataFrame)
    marginal_loss_factor: pd.DataFrame = field(default_factory=pd.DataFrame)


def _patch_inputs(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    def _load(_date: str, **_kw: Any) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(pcp_inputs, "load_pcp_inputs", _load)


def test_pid_per_bus_mlf(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_inputs(monkeypatch, FileNotFoundError("none"))
    assert not mu._pid_per_bus_mlf("d")
    _patch_inputs(monkeypatch, _Inputs(is_pid=False))
    assert not mu._pid_per_bus_mlf("d")
    _patch_inputs(monkeypatch, _Inputs())
    assert not mu._pid_per_bus_mlf("d")

    losses = pd.DataFrame(
        {
            "NAME": ["A", "A", "B", "B", "C"],
            "PERIOD": [1, 2, 1, 2, 3],
            "VALUE": [1.0, 1.0, 5.0, 2.0, 1.0],
        }
    )
    load = pd.DataFrame(
        {
            "bus_name": ["A", "A", "B", "B", "C"],
            "PERIOD": [1, 2, 1, 2, 3],
            "load_mw": [100.0, 100.0, 50.0, 0.0, 10.0],
        }
    )
    _patch_inputs(monkeypatch, _Inputs(nod_loss=losses, nod_load_long=load))
    mlf = mu._pid_per_bus_mlf("d")
    assert mlf[("A", 1)] == pytest.approx(1.0)
    assert mlf[("B", 1)] == pytest.approx(0.91)
    # B has zero load in period 2 → falls back to the centroid loss.
    assert mlf[("B", 2)] == pytest.approx(1.0)
    # Period 3 has no centroid sample → centroid loss 0.
    assert mlf[("C", 3)] == pytest.approx(0.9)


def test_pid_per_bus_mlf_from_accdb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pcp_solution, "_have_mdbtools", lambda: False)
    assert not mu._pid_per_bus_mlf_from_accdb("d")
    monkeypatch.setattr(pcp_solution, "_have_mdbtools", lambda: True)

    def _missing(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("no bundle")

    monkeypatch.setattr(pcp_solution, "resolve_solution", _missing)
    assert not mu._pid_per_bus_mlf_from_accdb("d")
    monkeypatch.setattr(
        pcp_solution,
        "resolve_solution",
        lambda *_a, **_k: SimpleNamespace(accdb_path=Path("x.accdb")),
    )

    def _bad(*_a: Any, **_k: Any) -> pd.DataFrame:
        raise RuntimeError("mdb-export failed")

    monkeypatch.setattr(pcp_solution, "extract_property", _bad)
    assert not mu._pid_per_bus_mlf_from_accdb("d")
    monkeypatch.setattr(
        pcp_solution, "extract_property", lambda *_a, **_k: pd.DataFrame()
    )
    assert not mu._pid_per_bus_mlf_from_accdb("d")
    frame = pd.DataFrame(
        {"object_name": ["Crucero220", "X"], "hour": [0, 1], "value": [0.98, "bad"]}
    )
    monkeypatch.setattr(pcp_solution, "extract_property", lambda *_a, **_k: frame)
    assert mu._pid_per_bus_mlf_from_accdb("d") == {("Crucero220", 1): 0.98}


def test_load_pid_mlf_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_inputs(monkeypatch, FileNotFoundError("x"))
    assert not mu._load_pid_mlf_by_id("d", {})
    _patch_inputs(monkeypatch, _Inputs(is_pid=False))
    assert not mu._load_pid_mlf_by_id("d", {})
    _patch_inputs(monkeypatch, _Inputs())
    assert not mu._load_pid_mlf_by_id("d", {})
    frame = pd.DataFrame({"NAME": ["U1", "U1", "U2"], "VALUE": [0.9, 1.1, 0.5]})
    _patch_inputs(monkeypatch, _Inputs(marginal_loss_factor=frame))
    monkeypatch.setattr(
        pcp_inputs, "resolve_pcp_unit", lambda n, _i: 7 if n == "U1" else None
    )
    assert mu._load_pid_mlf_by_id("d", {}) == {7: pytest.approx(1.0)}


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def _no_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mu, "_load_pid_mlf_by_id", lambda _d, _a: {})
    monkeypatch.setattr(mu, "_pid_per_bus_mlf_from_accdb", lambda _d: {})
    monkeypatch.setattr(mu, "_pid_per_bus_mlf", lambda _d: {})


def _with_pid(monkeypatch: pytest.MonkeyPatch, accdb: bool) -> None:
    monkeypatch.setattr(mu, "_load_pid_mlf_by_id", lambda _d, _a: {101: 0.98})
    per_bus = {("Charrua220", h + 1): 1.01 for h in range(24)}
    per_bus[("Crucero220", 1)] = 0.97
    monkeypatch.setattr(
        mu, "_pid_per_bus_mlf_from_accdb", lambda _d: per_bus if accdb else {}
    )
    monkeypatch.setattr(mu, "_pid_per_bus_mlf", lambda _d: per_bus)


def _assert_regimes(out: pd.DataFrame) -> None:
    by_q = out[out["bus_uid"] == 2].set_index("quarter")
    assert by_q.loc[0, "regime"] == "renewable_curtailment"
    assert by_q.loc[32, "marginal_id_central"] == 101  # 08:00, λ=60 → gas
    assert by_q.loc[32, "regime"] == "matched_interior"
    assert by_q.loc[32, "tier_wedge_usd"] == pytest.approx(15.0)
    assert by_q.loc[56, "marginal_id_central"] == 102  # 14:00, λ=45 → coal
    assert by_q.loc[56, "regime"] == "matched_capped_pmax"
    assert by_q.loc[76, "regime"] == "demand_fail"
    assert by_q.loc[90, "regime"] == "matched_above"
    assert bool(by_q.loc[90, "discrepancy_flag"])


def test_compute_marginal_units_for_day(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _with_pid(monkeypatch, accdb=False)
    buses = [(bt, iid, lbl) for bt, (iid, lbl, *_r) in BUSES.items()]
    out = mu.compute_marginal_units_for_day(
        NO_CLIENT, date_iso="2026-04-22", ref_buses=buses, verbose=True
    )
    _assert_regimes(out)
    assert (out["system_total_demand_mw"] > 0).all()
    noon = out[(out["bus_uid"] == 2) & (out["hour"] == 12)].iloc[0]
    assert noon["system_renewable_fraction"] == pytest.approx(150.0 / 520.0)
    assert out["loss_factor_pid"].notna().all()
    assert out.loc[out["bus_uid"] == 2, "bus_mlf"].notna().all()
    text = capsys.readouterr().out
    assert "PID marginal-loss factor loaded for 1 units" in text
    assert "PID per-bus MLF loaded" in text


def test_compute_marginal_units_for_day_variants(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_pid(monkeypatch, accdb=True)
    out = mu.compute_marginal_units_for_day(
        NO_CLIENT, date_iso="2026-04-22", use_pid_mlf=False
    )
    # Default REF_BUSES: only the two buses the fake publishes for.
    assert set(out["bus_uid"]) == {1, 2, 10}
    assert out["loss_factor_pid"].isna().all()

    out2 = mu.compute_marginal_units_for_day(NO_CLIENT, date_iso="2026-04-22")
    assert out2["loss_factor_pid"].notna().all()

    world.empty = {"cmg_real", "cmg_online"}
    assert mu.compute_marginal_units_for_day(NO_CLIENT, date_iso="2026-04-22").empty


def test_compute_marginal_units_for_day_degenerate(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_pid(monkeypatch)
    world.scvic_empty = True
    world.empty = {"instrucciones_cmg"}
    out = mu.compute_marginal_units_for_day(NO_CLIENT, date_iso="2026-04-22")
    assert set(out["regime"]) <= {
        "renewable_curtailment",
        "demand_fail",
        "no_cv_match",
    }
    assert (out["n_forced_in_pool"] == 0).all()


def test_compute_marginal_units_no_dispatch(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_pid(monkeypatch)
    world.empty = {"generacion_real"}
    monkeypatch.setattr(
        mu,
        "fetch_generacion_real",
        lambda _c, date=None: pd.DataFrame(columns=["central", "hour", "mw", "pmax"]),
    )
    out = mu.compute_marginal_units_for_day(NO_CLIENT, date_iso="2026-04-22")
    assert "no_candidates" in set(out["regime"])
    assert (out["system_total_demand_mw"] == 0).all()


def test_reconstruct_output_empty() -> None:
    assert mu._reconstruct_output(
        cmg_islands=pd.DataFrame(),
        island_marginals={},
        island_size=pd.Series(dtype=int),
        mlf_by_id={},
    ).empty


def test_reconstruct_without_source_column() -> None:
    cmg = pd.DataFrame(
        {
            "date_utc": ["d"],
            "hour": [0],
            "minute": [0],
            "quarter": [0],
            "bus_uid": [1],
            "bus_label": ["b"],
            "lmp": [10.0],
            "island_id": ["0@d_q0"],
        }
    )
    out = mu._reconstruct_output(
        cmg_islands=cmg,
        island_marginals={(0, "0@d_q0"): {"marginal_id": None, "regime": "x"}},
        island_size=pd.Series({(0, "0@d_q0"): 1}),
        mlf_by_id={},
    )
    assert out["lambda_provenance"].iloc[0] == ""
    assert out["pmin_pmax_source"].iloc[0] == ""


# ---------------------------------------------------------------------------
# Legacy single-day CLI
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def legacy_env(world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> World:
    monkeypatch.setattr(mu, "CenApiClient", _Client)
    monkeypatch.setattr(mu, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(mu.warnings, "filterwarnings", lambda *_a, **_k: None)
    return world


def test_main_full_report(
    legacy_env: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert mu.main() == 0
    text = capsys.readouterr().out
    assert "most-limited installations" in text
    assert "LINEA A: 2" in text
    assert "reservoir-hydro id_central set: 1 centrales" in text
    assert "Pattern A" in text and "Pattern B" in text
    assert "next_above_λ" in text
    assert re.search(r"Δ\s+picked", text)
    # Regression: the accuracy section filtered on regime names that
    # identify_marginal never emits, so it was always silently empty.
    assert "attributed cells:" in text
    assert "OK_in_pool" in text and "FORCED_RO" in text and "NO_SCVIC_CV" in text
    assert "…" in text  # long bus display is truncated
    out = pd.read_parquet(tmp_path / "work" / "marginal_units_v3.parquet")
    assert {"loss_factor", "epsilon_at_bus", "discrepancy_flag"} <= set(out.columns)
    assert (tmp_path / "work" / "discrepancies_v3.parquet").exists()


def test_main_quiet_day_and_fetch_warnings(
    legacy_env: World, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy_env.flat_lmp = 60.0
    legacy_env.raising = {"limitaciones", "generacion_programada_pcp", "embalse_real"}
    assert mu.main() == 0
    text = capsys.readouterr().out
    assert text.count("WARN: RuntimeError") == 3
    assert "(none)" in text


def test_main_empty_side_feeds(
    legacy_env: World, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy_env.empty = {
        "limitaciones",
        "generacion_programada_pcp",
        "embalse_real",
        "instrucciones_cmg",
    }
    assert mu.main() == 0
    text = capsys.readouterr().out
    assert "Forced id set: 0 centrales" in text
    assert "0 rows / 0 centrales" in text
    assert "0 reservoir rows / 0 unique reservoirs" in text


def test_main_aborts_without_cmg(
    legacy_env: World, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy_env.empty = {"cmg_real", "cmg_online"}
    assert mu.main() == 1
    assert "no CMG — aborting" in capsys.readouterr().out


def test_build_instrucciones_lookup_missing_columns() -> None:
    assert not mu.build_instrucciones_lookup(pd.DataFrame())
    assert not mu.build_instrucciones_lookup(pd.DataFrame({"hour": [1]}))


def test_reservoir_hydro_ids() -> None:
    assert mu._reservoir_hydro_ids(pd.DataFrame()) == (set(), [])
    units = pd.DataFrame(
        {
            "id_central": [7, "x", None, 8],
            "central": ["HE A", "HE B", "HE C", "PFV D"],
            "tipo_tecnologia_unidad": ["Hidroeléctrica de embalse"] * 3 + ["Solar"],
        }
    )
    assert mu._reservoir_hydro_ids(units) == ({7}, ["HE A", "HE B", "HE C"])


def test_rejection_reason_all_paths() -> None:
    def r(bridged: bool, has: bool, forced: bool) -> str:
        return mu._rejection_reason(
            pd.Series({"bridged": bridged, "has_scvic": has, "forced": forced})
        )

    assert r(False, False, False) == "ID_BRIDGE_MISS"
    assert r(True, True, True) == "FORCED_RO"
    assert r(True, False, False) == "NO_SCVIC_CV"
    assert r(True, True, False) == "OK_in_pool"


def _report_frame(delta: float, regime: str, bus_uid: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bus_uid": [bus_uid],
            "bus_label": ["b"],
            "quarter": [0],
            "hour": [0],
            "minute": [0],
            "lambda_cen": [50.0],
            "lambda_pipe": [50.0 + delta],
            "delta_lmp": [delta],
            "epsilon": [0.0],
            "marginal_central": ["X"],
            "marginal_fuel": [""],
            "regime": [regime],
            "top_above_lambda": [None],
            "discrepancy_flag": [abs(delta) > 5],
        }
    )


def test_report_sections_sparse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Displayed bus 1 has no rows; bus 2 has a single (short) series.
    shown = mu._print_bus_views(_report_frame(-9.0, "no_candidates"), "d")
    assert shown == [1, 2]
    mu._print_daily_and_accuracy(_report_frame(-9.0, "no_candidates"), "d")
    text = capsys.readouterr().out
    assert "attributed cells" not in text
    assert "Bus 1:" not in text and "Bus 2:" in text
    # Only Pattern A rows → the Pattern B table is skipped.
    mu._print_discrepancies(_report_frame(-9.0, "no_candidates"), tmp_path)
    text = capsys.readouterr().out
    assert "next_above_λ" in text
    assert not re.search(r"Δ\s+picked", text)
    # Only Pattern B rows → the Pattern A table is skipped.
    mu._print_discrepancies(_report_frame(9.0, "matched_above"), tmp_path)
    text = capsys.readouterr().out
    assert re.search(r"Δ\s+picked", text)
    assert "next_above_λ" not in text
