# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for the ``cen2gtopt`` CLI (:mod:`cen2gtopt.main`).

The SIP layer is replaced by fakes injected on the client / fetcher
modules; no network is touched.
"""

from __future__ import annotations

import json
import logging
import runpy
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import cen2gtopt
from cen2gtopt import _cen_client, _sip_client
from cen2gtopt import main as m


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CEN_USER_KEY", raising=False)
    monkeypatch.delenv("CEN_API_KEY", raising=False)


class _FakeClient:
    def __init__(self, config: Any) -> None:
        self.config = config

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch, behaviour: dict[str, Any]
) -> list[str]:
    """Replace every SIP fetcher; ``behaviour[name]`` is a frame or exception."""
    called: list[str] = []
    monkeypatch.setattr(_cen_client, "CenApiClient", _FakeClient)
    for fn in (
        "fetch_centrales",
        "fetch_costo_combustible",
        "fetch_costo_marginal_online",
        "fetch_costo_marginal_real",
        "fetch_demanda_neta",
        "fetch_generacion_real",
        "fetch_instrucciones_operacionales_cmg",
        "fetch_limitaciones_transmision",
        "fetch_programas_mantenimiento_mayor",
    ):

        def _fake(_name: str = fn, **_kw: Any) -> pd.DataFrame:
            called.append(_name)
            val = behaviour.get(_name, pd.DataFrame({"a": [1, 2]}))
            if isinstance(val, Exception):
                raise val
            return val

        monkeypatch.setattr(_sip_client, fn, _fake)
    return called


def _base(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--start",
        "2025-01-01",
        "--end",
        "2025-01-02",
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]


@pytest.mark.parametrize(
    ("verbose", "quiet", "level"),
    [
        (0, True, logging.ERROR),
        (2, False, logging.DEBUG),
        (1, False, logging.INFO),
        (0, False, logging.WARNING),
    ],
)
def test_setup_logging_levels(
    monkeypatch: pytest.MonkeyPatch, verbose: int, quiet: bool, level: int
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: seen.update(kw))
    m._setup_logging(verbose, quiet)
    assert seen["level"] == level


def test_invalid_date_and_order(tmp_path: Path) -> None:
    bad = ["--start", "2025-13-01", "--end", "2025-01-02", "--out", str(tmp_path)]
    assert m.cli(bad) == m.EXIT_INPUT_ERROR
    rev = ["--start", "2025-01-03", "--end", "2025-01-02", "--out", str(tmp_path)]
    assert m.cli(rev) == m.EXIT_INPUT_ERROR


def test_csv_without_topology_errors(tmp_path: Path) -> None:
    assert m.cli(_base(tmp_path, "--source", "csv")) == m.EXIT_INPUT_ERROR


def test_csv_with_topology_hits_v1_gate(tmp_path: Path) -> None:
    topo = tmp_path / "topo.json"
    topo.write_text(json.dumps({"buses": [], "generators": [], "lines": []}))
    rc = m.cli(_base(tmp_path, "--source", "csv", "--topology", str(topo)))
    assert rc == m.EXIT_INPUT_ERROR


def test_manifest_only_with_deprecated_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CEN_API_KEY", "k")
    rc = m.cli(_base(tmp_path, "--manifest-only", "--include", "lmp"))
    assert rc == m.EXIT_OK
    man = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert man["args"]["source"] == "sip"
    assert man["args"]["include"] == ["lmp"]


def test_sip_without_key_errors(tmp_path: Path) -> None:
    assert m.cli(_base(tmp_path, "--source", "sip")) == m.EXIT_INPUT_ERROR


def test_sip_full_include_with_tolerated_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _install_fakes(
        monkeypatch,
        {
            "fetch_costo_marginal_online": _cen_client.CenNotFoundError("404"),
            "fetch_costo_combustible": _cen_client.CenServerError("500"),
            "fetch_demanda_neta": pd.DataFrame(),
        },
    )
    args = _base(tmp_path, "--api-key", "k")
    for inc in ("lmp", "lmp_online", "dispatch", "demand", "fuel_costs", "regimes"):
        args += ["--include", inc]
    assert m.cli(args) == m.EXIT_OK
    assert len(called) == 9
    man = json.loads((tmp_path / "out" / "manifest.json").read_text())
    rc = man["row_counts"]
    assert rc["demanda_neta"] == 0
    assert rc["centrales"] == 2
    assert "costo_marginal_online" not in rc
    assert "costo_combustible" not in rc
    assert (tmp_path / "out" / "raw" / "centrales.parquet").exists()
    assert not (tmp_path / "out" / "raw" / "demanda_neta.parquet").exists()


def test_sip_minimal_include_skips_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _install_fakes(monkeypatch, {})
    args = _base(tmp_path, "--api-key", "k", "--include", "flows")
    assert m.cli(args) == m.EXIT_OK
    assert called == ["fetch_centrales"]


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_cen_client.CenAuthError("bad key"), m.EXIT_INPUT_ERROR),
        (_cen_client.CenServerError("down"), m.EXIT_NETWORK_ERROR),
    ],
)
def test_sip_client_level_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception, code: int
) -> None:
    _install_fakes(monkeypatch, {})

    class _Boom(_FakeClient):
        def __enter__(self) -> "_FakeClient":
            raise exc

    monkeypatch.setattr(_cen_client, "CenApiClient", _Boom)
    assert m.cli(_base(tmp_path, "--api-key", "k")) == code


def test_package_cli_and_dunder_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["cen2gtopt", *_base(tmp_path, "--manifest-only")])
    assert cen2gtopt.cli() == m.EXIT_OK
    with pytest.raises(SystemExit) as info:
        runpy.run_module("cen2gtopt", run_name="__main__")
    assert info.value.code == m.EXIT_OK
