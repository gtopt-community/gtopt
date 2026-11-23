# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Offline tests for :mod:`cen2gtopt.pcp_inputs` on synthetic PCP/PID bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import pcp_inputs as pi
from cen2gtopt.pcp_archive import PcpFile
from cen2gtopt.tests.cov_plexos_helpers import DEFAULT_XML, write_zip

DATE = "2026-05-06"
YMD = "20260506"


# ---------------------------------------------------------------------------
# Synthetic bundle builders
# ---------------------------------------------------------------------------


def _per_period(values: dict[str, dict[int, float]]) -> str:
    lines = ["NAME,YEAR,MONTH,DAY,PERIOD,VALUE"]
    for name, by_p in values.items():
        for period, val in by_p.items():
            lines.append(f"{name},2026,5,6,{period},{val}")
    return "\n".join(lines) + "\n"


def _datos_files(*, with_xml: str | None = "DBSEN_PRGDIARIO.xml") -> dict[str, str]:
    files = {
        "Gen_HeatRate.csv": _per_period(
            {
                "ANGAMOS_1": {18: 10.0, 1: 9.0},
                "TG_Diesel_X": {1: 11.0, 18: 12.0},
                "HYDRO_RIO": {1: 0.0, 18: 0.0},
            }
        ),
        "Gen_Rating.csv": _per_period(
            {"ANGAMOS_1": {1: 250.0}, "TG_Diesel_X": {1: 80.0}, "HYDRO_RIO": {1: 40.0}}
        ),
        "Gen_MinStableLevel.csv": _per_period({"ANGAMOS_1": {1: 100.0}}),
        "Gen_VOMCharge.csv": _per_period({"ANGAMOS_1": {1: 2.0}}),
        "Fuel_Price.csv": _per_period(
            {"Carbon_Ang": {1: 3.0, 18: 3.5}, "Diesel": {1: 20.0}}
        ),
        "Hydro_StoWaterValues.csv": _per_period({"LAGO": {1: 100.0, 2: 101.0}}),
        "Lin_MaxRating.csv": _per_period({"L1": {1: 50.0}, "L2": {1: 60.0}}),
        "Nod_Load.csv": (
            "YEAR,MONTH,DAY,PERIOD,BUS1,BUS2\n2026,5,6,1,10.5,x\n2026,5,6,2,11.0,3.0\n"
        ),
    }
    if with_xml:
        files[with_xml] = DEFAULT_XML
    return files


def _write_dir(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def _pid_files(*, with_mlf: bool = True) -> dict[str, str]:
    files = {
        f"Modelos/{k}": v
        for k, v in _datos_files(with_xml="DBSEN_PRGDIARIO_PID.xml").items()
    }
    files["Modelos/Acople_PCP/Nod_Loss.csv"] = _per_period({"BUS1": {1: 1.0}})
    if with_mlf:
        files["Modelos/Acople_PCP/Gen_MarginalLossFactor.csv"] = _per_period(
            {"ANGAMOS_1": {1: 0.9, 2: 0.95}, "TG_Diesel_X": {1: 1.02}}
        )
    return files


def _zip_members(prefix: str, files: dict[str, str]) -> dict[str, bytes]:
    return {f"{prefix}{k}": v.encode() for k, v in files.items()}


def _pcp(tmp_path: Path, **kw: Any) -> pi.PcpInputs:
    return pi.PcpInputs(
        date_iso=DATE, datos_dir=_write_dir(tmp_path, _datos_files(**kw))
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_co2_factor() -> None:
    assert pi._co2_factor("") == 0.0
    assert pi._co2_factor("Carbon_Ang") == pytest.approx(94.6)
    assert pi._co2_factor("gnl quintero") == 0.0
    assert pi._co2_factor("Gas Natural") == pytest.approx(56.1)


def test_pcp_aliases_and_resolve() -> None:
    aliases = pi._pcp_aliases("ANDES_U1-U2_DIE")
    assert "andesu1u2" in aliases  # suffix stripped
    assert "andes" in aliases  # first word
    assert "terandes" in aliases  # CEN type prefix
    assert all(len(a) >= 2 for a in aliases)
    # Leading separator → empty head (skipped).
    assert "x" not in pi._pcp_aliases("_X")

    assert pi.resolve_pcp_unit("ABANICO", {"abanico": 3}) == 3  # bridge hit
    assert pi.resolve_pcp_unit("ABANICO", {"hpabanico": 5}) == 5  # PCP alias
    assert pi.resolve_pcp_unit("ABANICO", {"other": 1}) is None


# ---------------------------------------------------------------------------
# Layout detection
# ---------------------------------------------------------------------------


def test_model_root_layouts(tmp_path: Path) -> None:
    flat = _write_dir(tmp_path / "flat", {"Gen_HeatRate.csv": "x\n"})
    assert pi.PcpInputs(DATE, flat)._model_root() == flat

    pid = _write_dir(tmp_path / "pid", {"Modelos/Gen_HeatRate.csv": "x\n"})
    assert pi.PcpInputs(DATE, pid)._model_root() == pid / "Modelos"

    nested = _write_dir(
        tmp_path / "nested",
        {"README.txt": "x", "empty/a.txt": "x", "P_01/Modelos/Gen_HeatRate.csv": "x"},
    )
    assert pi.PcpInputs(DATE, nested)._model_root() == nested / "P_01" / "Modelos"

    nested_flat = _write_dir(tmp_path / "nf", {"P_01/Gen_HeatRate.csv": "x"})
    assert pi.PcpInputs(DATE, nested_flat)._model_root() == nested_flat / "P_01"

    none = _write_dir(tmp_path / "none", {"a/b.txt": "x"})
    inputs = pi.PcpInputs(DATE, none)
    assert inputs._model_root() == none
    assert not inputs.is_pid
    assert inputs.heat_rate.empty
    assert inputs.units.empty
    assert inputs.fuels.empty
    assert inputs.nod_load_long.empty
    assert inputs.marginal_cost_per_unit().empty
    assert inputs.epsilon_per_unit().empty
    assert not inputs.unit_fuel_map
    assert not inputs.unit_node_map


# ---------------------------------------------------------------------------
# PCP inputs: raw tables + derived quantities
# ---------------------------------------------------------------------------


def test_raw_tables(tmp_path: Path) -> None:
    inputs = _pcp(tmp_path)
    assert not inputs.is_pid
    assert len(inputs.gen_rating) == 3
    assert len(inputs.gen_min_stable_level) == 1
    assert not inputs.gen_vom_charge.empty
    assert inputs.gen_fuel_transport_charge.empty
    for empty in (
        inputs.gen_start_cost,
        inputs.gen_units_out,
        inputs.fuel_max_offtake_week,
        inputs.hydro_water_flows,
        inputs.hydro_max_volume,
        inputs.hydro_min_volume,
        inputs.hydro_initial_volume,
        inputs.lin_min_rating,
        inputs.lin_units,
        inputs.res_requirement,
        inputs.sscc_activation_bess,
        inputs.bess_ini_value,
        inputs.marginal_loss_factor,
        inputs.nod_loss,
        inputs.hydro_water_values_pcp,
        inputs.fuel_max_offtake_48h,
        inputs.fuel_penalty_price,
        inputs.fuel_penalty_quantity,
    ):
        assert empty.empty
    assert len(inputs.lin_max_rating) == 2
    long = inputs.nod_load_long
    # "x" is coerced to NaN and dropped.
    assert len(long) == 3
    assert set(long["bus_name"]) == {"BUS1", "BUS2"}


def test_units_and_fuels(tmp_path: Path) -> None:
    inputs = _pcp(tmp_path)
    units = inputs.units.set_index("unit_name")
    # First period (1) is canonical.
    assert units.loc["ANGAMOS_1", "heat_rate_gj_per_mwh"] == pytest.approx(9.0)
    assert units.loc["ANGAMOS_1", "pmax"] == pytest.approx(250.0)
    assert pd.isna(units.loc["TG_Diesel_X", "pmin"])
    assert units["fuel_transport_usd_per_mwh"].isna().all()
    # Authoritative XML map: the first listed fuel wins.
    assert units.loc["ANGAMOS_1", "fuel_name"] == "Carbon_Ang"
    assert units.loc["TG_Diesel_X", "fuel_name"] == ""
    assert inputs.unit_node_map == {"ANGAMOS_1": "Crucero220"}

    fuels = inputs.fuels.set_index("fuel_name")
    assert fuels.loc["Carbon_Ang", "price_usd_per_gj"] == pytest.approx(3.0)
    assert fuels.loc["Carbon_Ang", "co2_kg_per_gj"] == pytest.approx(94.6)


def test_units_without_xml(tmp_path: Path) -> None:
    inputs = _pcp(tmp_path, with_xml=None)
    assert not inputs.unit_fuel_map
    assert (inputs.units["fuel_name"] == "").all()


def test_marginal_cost_per_unit(tmp_path: Path) -> None:
    inputs = _pcp(tmp_path)
    mc = inputs.marginal_cost_per_unit(period=18).set_index("unit_name")
    # Period-18 heat-rate override: 10 GJ/MWh × 3 USD/GJ + 2 VOM.
    assert mc.loc["ANGAMOS_1", "mc_usd_mwh"] == pytest.approx(32.0)
    # Not in the XML → substring heuristic picks "Diesel".
    assert mc.loc["TG_Diesel_X", "fuel_name"] == "Diesel"
    assert mc.loc["TG_Diesel_X", "mc_usd_mwh"] == pytest.approx(240.0)
    assert mc.loc["HYDRO_RIO", "fuel_name"] == ""
    assert pd.isna(mc.loc["HYDRO_RIO", "mc_usd_mwh"])

    # Unknown period: keeps the canonical first-period heat rate.
    mc99 = inputs.marginal_cost_per_unit(period=99).set_index("unit_name")
    assert mc99.loc["ANGAMOS_1", "heat_rate_gj_per_mwh"] == pytest.approx(9.0)


def test_marginal_cost_heuristic_only(tmp_path: Path) -> None:
    inputs = _pcp(tmp_path, with_xml=None)
    mc = inputs.marginal_cost_per_unit(period=1).set_index("unit_name")
    assert mc.loc["ANGAMOS_1", "fuel_name"] == ""
    assert mc.loc["TG_Diesel_X", "fuel_name"] == "Diesel"


def test_marginal_cost_without_fuels(tmp_path: Path) -> None:
    files = _datos_files()
    del files["Fuel_Price.csv"]
    inputs = pi.PcpInputs(DATE, _write_dir(tmp_path, files))
    mc = inputs.marginal_cost_per_unit(period=18)
    assert (mc["fuel_name"] == "").all()
    assert mc["mc_usd_mwh"].isna().all()


def test_epsilon_per_unit(tmp_path: Path) -> None:
    eps = _pcp(tmp_path).epsilon_per_unit().set_index("unit_name")
    assert list(eps.columns) == [
        "fuel_name",
        "heat_rate_gj_per_mwh",
        "co2_kg_per_gj",
        "epsilon_kg_per_mwh",
    ]
    assert eps.loc["TG_Diesel_X", "epsilon_kg_per_mwh"] == pytest.approx(12 * 74.1)


def test_broken_xml_maps_are_empty(tmp_path: Path) -> None:
    files = _datos_files(with_xml=None)
    files["DBSEN_PRGDIARIO.xml"] = "<not-xml"
    inputs = pi.PcpInputs(DATE, _write_dir(tmp_path, files))
    assert not inputs.unit_fuel_map
    assert not inputs.unit_node_map


def test_pid_inputs(tmp_path: Path) -> None:
    inputs = pi.PcpInputs(DATE, _write_dir(tmp_path, _pid_files()), period=20)
    assert inputs.is_pid
    assert len(inputs.marginal_loss_factor) == 3
    assert len(inputs.nod_loss) == 1
    # The second XML candidate name (PID) is used.
    assert inputs.unit_fuel_map["ANGAMOS_1"] == "Carbon_Ang"
    assert inputs.unit_node_map["ANGAMOS_1"] == "Crucero220"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _pid_file(name: str) -> PcpFile:
    return PcpFile.from_dict({"nombre": name, "categoria": "PID"})


@pytest.fixture(name="remote")
def _remote(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake CEN index + downloader (writes the requested zip payload)."""
    state: dict[str, Any] = {"files": [], "payloads": {}, "downloads": [], "force": []}

    def _fetch_index(*, force: bool = False, **_k: Any) -> list[PcpFile]:
        state["force"].append(force)
        return list(state["files"])

    def _download_one(f: PcpFile, *, output_dir: Path, **_k: Any) -> Path:
        state["downloads"].append(f.nombre)
        return write_zip(
            output_dir / f.categoria / f.nombre, state["payloads"][f.nombre]
        )

    monkeypatch.setattr(pi, "fetch_index", _fetch_index)
    monkeypatch.setattr(pi, "download_one", _download_one)
    return state


def test_load_invalid_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source must be"):
        pi.load_pcp_inputs(DATE, source="xyz", download_root=tmp_path)


def test_load_pid_local_zip_by_period(tmp_path: Path) -> None:
    for pp in ("19", "20"):
        name = f"PID_{YMD}_{pp}"
        write_zip(
            tmp_path / "PID" / f"{name}.zip", _zip_members(f"{name}/", _pid_files())
        )
    inputs = pi.load_pcp_inputs(
        DATE, period=19, download_root=tmp_path, auto_download=False
    )
    assert inputs.period == 19
    assert inputs.is_pid
    assert (
        inputs.datos_dir == tmp_path / "_unpacked" / f"PID_{YMD}_19" / f"PID_{YMD}_19"
    )

    # Explicit period on an already-unpacked bundle.
    cached = pi.load_pcp_inputs(
        DATE, period=19, download_root=tmp_path, auto_download=False
    )
    assert cached.period == 19 and cached.datos_dir == inputs.datos_dir

    # Latest local one when no period is given; parsed from the file name.
    latest = pi.load_pcp_inputs(DATE, download_root=tmp_path, auto_download=False)
    assert latest.period == 20
    # Second call hits the already-unpacked directory.
    again = pi.load_pcp_inputs(DATE, download_root=tmp_path, auto_download=False)
    assert again.period == 20 and again.datos_dir == latest.datos_dir


def test_load_pid_unpacked_only_with_odd_name(tmp_path: Path) -> None:
    name = f"PID_{YMD}_7"  # one-digit tail → period not parseable
    _write_dir(tmp_path / "_unpacked" / name / name, _pid_files())
    inputs = pi.load_pcp_inputs(DATE, download_root=tmp_path, auto_download=False)
    assert inputs.is_pid
    assert inputs.period is None


def test_load_pid_requested_period_missing_locally(tmp_path: Path) -> None:
    name = f"PID_{YMD}_20"
    write_zip(tmp_path / "PID" / f"{name}.zip", _zip_members(f"{name}/", _pid_files()))
    with pytest.raises(FileNotFoundError, match="period 5"):
        pi.load_pcp_inputs(
            DATE, period=5, source="pid", download_root=tmp_path, auto_download=False
        )
    with pytest.raises(FileNotFoundError, match="no PID file found"):
        pi.load_pcp_inputs(
            "2026-01-01", source="pid", download_root=tmp_path, auto_download=False
        )


def test_load_pid_remote(tmp_path: Path, remote: dict[str, Any]) -> None:
    names = [f"PID_{YMD}_{pp}.zip" for pp in ("03", "04")]
    remote["files"] = [_pid_file(n) for n in names] + [_pid_file("OTHER.zip")]
    for n in names:
        stem = n.removesuffix(".zip")
        remote["payloads"][n] = _zip_members(f"{stem}/", _pid_files())

    inputs = pi.load_pcp_inputs(DATE, period=3, download_root=tmp_path)
    assert inputs.period == 3
    assert remote["downloads"] == [names[0]]
    assert remote["force"][-1] is True  # fresh URLs before downloading

    latest = pi.load_pcp_inputs("2026-05-06", download_root=tmp_path / "other")
    assert latest.period == 4


def test_load_pid_remote_period_missing_falls_back_to_pcp(
    tmp_path: Path, remote: dict[str, Any]
) -> None:
    remote["files"] = [
        _pid_file(f"PID_{YMD}_04.zip"),
        PcpFile.from_dict({"nombre": f"PLEXOS{YMD}.zip", "categoria": "PCP"}),
    ]
    remote["payloads"][f"PLEXOS{YMD}.zip"] = {
        f"DATOS{YMD}.zip": write_zip(
            tmp_path / "scratch.zip",
            {k: v.encode() for k, v in _datos_files().items()},
        ).read_bytes()
    }
    inputs = pi.load_pcp_inputs(DATE, period=9, download_root=tmp_path)
    assert not inputs.is_pid
    assert inputs.datos_dir == tmp_path / "_unpacked" / f"PCP_{YMD}"
    assert remote["downloads"] == [f"PLEXOS{YMD}.zip"]
    # Cached extraction is reused.
    again = pi.load_pcp_inputs(DATE, source="pcp", download_root=tmp_path)
    assert again.datos_dir == inputs.datos_dir
    assert remote["downloads"] == [f"PLEXOS{YMD}.zip"]


def test_load_pcp_not_in_index(tmp_path: Path, remote: dict[str, Any]) -> None:
    remote["files"] = [_pid_file("SOMETHING_ELSE.zip")]
    with pytest.raises(FileNotFoundError, match="no PLEXOS"):
        pi.load_pcp_inputs(DATE, source="pcp", download_root=tmp_path)
    # auto: no PID anywhere either → same PCP error.
    with pytest.raises(FileNotFoundError, match="no PLEXOS"):
        pi.load_pcp_inputs(DATE, download_root=tmp_path)


def test_load_pcp_plexos_without_datos(tmp_path: Path) -> None:
    write_zip(tmp_path / "PCP" / f"PLEXOS{YMD}.zip", {"RES.zip": b"x"})
    with pytest.raises(FileNotFoundError, match=f"DATOS{YMD}.zip missing"):
        pi.load_pcp_inputs(DATE, source="pcp", download_root=tmp_path)


def test_load_pcp_datos_zip_only(tmp_path: Path) -> None:
    write_zip(
        tmp_path / "PCP" / f"DATOS{YMD}.zip",
        {k: v.encode() for k, v in _datos_files().items()},
    )
    inputs = pi.load_pcp_inputs(DATE, source="pcp", download_root=tmp_path)
    assert (inputs.datos_dir / "Gen_HeatRate.csv").exists()


def test_load_pcp_nothing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no PCP archive"):
        pi.load_pcp_inputs(
            DATE, source="pcp", download_root=tmp_path, auto_download=False
        )


def test_load_pid_remote_not_in_index(tmp_path: Path, remote: dict[str, Any]) -> None:
    # Local unpacked dir advertises the name but neither zip nor index has it.
    (tmp_path / "_unpacked" / f"PID_{YMD}_02").mkdir(parents=True)
    remote["files"] = []
    with pytest.raises(FileNotFoundError, match=f"no PID_{YMD}_02.zip in CEN index"):
        pi.load_pcp_inputs(DATE, download_root=tmp_path)


def test_load_pid_zip_missing_offline(tmp_path: Path) -> None:
    (tmp_path / "_unpacked" / f"PID_{YMD}_02").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="not found"):
        pi.load_pcp_inputs(DATE, download_root=tmp_path, auto_download=False)


def test_load_pid_bad_layout(tmp_path: Path) -> None:
    name = f"PID_{YMD}_02"
    write_zip(tmp_path / "PID" / f"{name}.zip", {f"{name}/readme.txt": b"x"})
    with pytest.raises(FileNotFoundError, match="unexpected PID layout"):
        pi.load_pcp_inputs(DATE, download_root=tmp_path, auto_download=False)


def test_load_pid_odd_name_extracted(tmp_path: Path) -> None:
    name = f"PID_{YMD}_x2"
    write_zip(tmp_path / "PID" / f"{name}.zip", _zip_members(f"{name}/", _pid_files()))
    inputs = pi.load_pcp_inputs(DATE, download_root=tmp_path, auto_download=False)
    assert inputs.is_pid and inputs.period is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _patch_loader(monkeypatch: pytest.MonkeyPatch, inputs: pi.PcpInputs) -> list:
    calls: list[dict[str, Any]] = []

    def _load(date_iso: str, **kw: Any) -> pi.PcpInputs:
        calls.append({"date": date_iso, **kw})
        return inputs

    monkeypatch.setattr(pi, "load_pcp_inputs", _load)
    return calls


def test_summary_helper_on_empty(tmp_path: Path) -> None:
    s = pi._summary(pi.PcpInputs(DATE, tmp_path))
    assert s["n_units"] == 0 and s["n_hours"] == 0
    assert s["n_lines"] == 0 and s["n_buses"] == 0


def test_cli_summary_pcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_loader(monkeypatch, _pcp(tmp_path))
    assert pi.main(["-q", "summary", "--date", DATE]) == 0
    out = capsys.readouterr().out
    assert f"=== PCP inputs summary — {DATE} ===" in out
    assert "n_units         = 3" in out
    assert "n_buses         = 2" in out
    assert "top 10 units by pmax" in out
    assert "fuel prices" in out
    assert "water values per reservoir" in out
    assert "marginal loss factor" not in out


def test_cli_summary_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = pi.PcpInputs(DATE, _write_dir(tmp_path, _pid_files()), period=20)
    _patch_loader(monkeypatch, inputs)
    assert pi.main(["-v", "summary", "--date", DATE, "--source", "pid"]) == 0
    out = capsys.readouterr().out
    assert "=== PID inputs summary" in out and "period=20" in out
    assert "marginal loss factor (PID Acople_PCP)" in out
    assert "units: 2" in out


def test_cli_summary_empty_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _write_dir(tmp_path, {"Acople_PCP/x.txt": "x"})
    _patch_loader(monkeypatch, pi.PcpInputs(DATE, root))
    assert pi.main(["summary", "--date", DATE]) == 0
    out = capsys.readouterr().out
    assert "=== PID inputs summary" in out
    assert "top 10" not in out and "fuel prices" not in out
    assert "marginal loss factor" not in out


def test_cli_merit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_loader(monkeypatch, _pcp(tmp_path))
    assert pi.main(["merit", "--date", DATE, "--period", "18", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "=== merit order at period=18" in out
    assert "total units with derived MC: 2" in out


def test_cli_merit_and_epsilon_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_loader(monkeypatch, pi.PcpInputs(DATE, tmp_path))
    assert pi.main(["merit", "--date", DATE]) == 1
    assert pi.main(["epsilon", "--date", DATE]) == 1
    assert capsys.readouterr().out.count("no input data found") == 2


def test_cli_epsilon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_loader(monkeypatch, _pcp(tmp_path))
    assert pi.main(["epsilon", "--date", DATE]) == 0
    out = capsys.readouterr().out
    assert "non-zero only" in out
    assert "total units with ε > 0: 2" in out


def test_cli_loss_factors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_loader(
        monkeypatch,
        pi.PcpInputs(DATE, _write_dir(tmp_path, _pid_files()), period=20),
    )
    assert pi.main(["loss-factors", "--date", DATE, "--source", "auto"]) == 0
    out = capsys.readouterr().out
    assert "(PID period=20)" in out
    assert "units total: 2" in out
    assert calls[0]["source"] == "auto"


def test_cli_loss_factors_no_period_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_loader(monkeypatch, pi.PcpInputs(DATE, _write_dir(tmp_path, _pid_files())))
    assert pi.main(["loss-factors", "--date", DATE]) == 0
    assert "(PID) ===" in capsys.readouterr().out

    root = _write_dir(tmp_path / "nomlf", _pid_files(with_mlf=False))
    _patch_loader(monkeypatch, pi.PcpInputs(DATE, root))
    assert pi.main(["loss-factors", "--date", DATE]) == 1
    assert "Gen_MarginalLossFactor.csv missing" in capsys.readouterr().out


def test_cli_loss_factors_requires_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_loader(monkeypatch, _pcp(tmp_path))
    assert pi.main(["loss-factors", "--date", DATE]) == 2
    assert "only available in PID" in capsys.readouterr().out
