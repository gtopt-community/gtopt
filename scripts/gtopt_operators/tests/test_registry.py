# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the per-operator source registry (``sources.toml``)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from gtopt_operators import registry as reg_mod
from gtopt_operators.registry import (
    BUILTIN_OPERATORS,
    RegistryError,
    available_operators,
    compile_pattern,
    endpoint_url,
    load_registry,
    load_sources_file,
    parse_sources,
    validate_sources,
)

SCRIPTS_DIR = Path(__file__).resolve().parents[2]


def _minimal(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 1,
        "operator": {
            "id": "zz",
            "name": "Test operator",
            "country": "ZZ",
            "timezone": "UTC",
        },
    }
    data.update(extra)
    return data


@pytest.mark.parametrize("op", BUILTIN_OPERATORS)
def test_builtin_registries_validate(op: str) -> None:
    reg = load_registry(op)
    assert reg.operator.id == op
    assert reg.operator.country == reg.operator.country.lower()
    assert reg.path is not None and reg.path.name == "sources.toml"
    # The package exposes OPERATOR from its registry.
    mod = __import__(f"gtopt_operators.{op}", fromlist=["OPERATOR"])
    assert mod.OPERATOR == reg.operator


def test_available_operators_lists_builtins() -> None:
    assert set(BUILTIN_OPERATORS) <= set(available_operators())


def test_cen_registry_contents() -> None:
    reg = load_registry("cen")
    assert reg.operator.country == "cl"
    assert reg.operator.timezone == "America/Santiago"
    pcp = reg.dataset("pcp")
    assert pcp.backend == "cen_portal" and pcp.consumer == "plexos2gtopt"
    assert pcp.date_rule == "exact"
    plp = reg.dataset("plp_inputs")
    assert plp.date_rule == "latest_on_or_before"
    assert reg.auth["portal"].env == ("GTOPT_CEN_PORTAL_KEY",)
    assert reg.auth["sip"].env == ("CEN_USER_KEY", "CEN_API_KEY")
    assert endpoint_url("cen", "sip") == "https://sipub.api.coordinador.cl"


def test_aemo_datasets_are_manual_and_not_redistributable() -> None:
    reg = load_registry("aemo")
    assert reg.datasets
    for ds in reg.datasets.values():
        assert ds.backend == "manual"
        assert ds.redistributable is False
        assert ds.terms.startswith("https://")


def test_registries_hold_no_secrets() -> None:
    """No literal key may ever be committed in a sources.toml."""
    hexkey = re.compile(r"\b[0-9a-f]{32}\b")
    for op in BUILTIN_OPERATORS:
        path = load_registry(op).path
        assert path is not None
        assert not hexkey.search(path.read_text(encoding="utf-8")), path


def test_no_hardcoded_api_keys_in_operator_clients() -> None:
    """Guard: the CEN clients must read keys from the environment only."""
    hexkey = re.compile(r"[\"'][0-9a-f]{32}[\"']")
    offenders = [
        str(p.relative_to(SCRIPTS_DIR))
        for pkg in ("cen2gtopt", "cen_demanda", "gtopt_operators")
        for p in (SCRIPTS_DIR / pkg).rglob("*.py")
        if "tests" not in p.parts and hexkey.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_compile_pattern_exact_and_capture() -> None:
    rx = compile_pattern(r"DATOS{D}\.zip", "20260407")
    assert rx.match("datos20260407.zip")
    assert not rx.match("DATOS20260408.zip")
    cap = compile_pattern(r"IPLP_?{D}.*\.zip", None)
    m = cap.match("IPLP_20260401_v2.zip")
    assert m and m.group(1) == "20260401"


def test_dataset_regexes() -> None:
    ds = load_registry("cen").dataset("pcp")
    assert [bool(r.match("PLEXOS20260407.zip")) for r in ds.regexes("20260407")] == [
        True,
        False,
    ]


def test_unknown_dataset_and_endpoint() -> None:
    reg = load_registry("cen")
    with pytest.raises(RegistryError, match="no dataset 'nope'"):
        reg.dataset("nope")
    with pytest.raises(RegistryError, match="no endpoint 'nope'"):
        reg.endpoint_url("nope")
    with pytest.raises(RegistryError, match="no dataset .*known: \\(none\\)"):
        load_registry("xm").dataset("nope")


def test_unknown_operator() -> None:
    with pytest.raises(RegistryError, match="unknown operator 'nope'"):
        load_registry("nope")


def test_minimal_registry_parses() -> None:
    reg = parse_sources(_minimal())
    assert reg.operator.country == "zz"
    assert not reg.datasets and not reg.auth and not reg.endpoints


def test_validation_errors_are_collected() -> None:
    data = _minimal(
        schema_version=2,
        extra={},
        auth={
            "a": {"kind": "magic", "value": "x"},
            "b": {"kind": "env"},
            "c": "not a table",
            "d": {"kind": "env", "env": [1, 2]},
        },
        endpoint={
            "e1": {"url": "http://insecure"},
            "e2": {"url": "https://ok", "auth": "missing"},
            "e3": "bad",
        },
        dataset={
            "d1": {"backend": "ftp", "date_rule": "whenever", "foo": 1},
            "d2": {"backend": "cen_portal"},
            "d3": {"backend": "cen_portal", "patterns": ["(unclosed"]},
            "d4": {"backend": "static_url"},
            "d5": {"backend": "manual"},
            "d6": {
                "backend": "manual",
                "page": "http://x",
                "auth": "missing",
                "redistributable": "no",
                "sha256": "abc",
            },
            "d7": "bad",
        },
    )
    data["operator"]["timezone"] = ""
    errors = validate_sources(data)
    text = "\n".join(errors)
    for needle in (
        "schema_version must be 1",
        "unknown top-level table(s) ['extra']",
        "[operator].timezone",
        "[auth.a]: unknown field(s) ['value']",
        "[auth.a].kind",
        "[auth.b].env must name at least one variable",
        "[auth.c] must be a table",
        "[auth.d].env: expected",
        "[endpoint.e1].url must be https",
        "[endpoint.e2].auth references unknown",
        "[endpoint.e3].url must be a string",
        "[dataset.d1]: unknown field(s) ['foo']",
        "[dataset.d1].backend",
        "[dataset.d1].date_rule",
        "[dataset.d2]: cen_portal datasets need patterns",
        "[dataset.d3].patterns: bad regex",
        "[dataset.d4]: static_url datasets need url",
        "[dataset.d5]: manual datasets need url or page",
        "[dataset.d6].page must be https",
        "[dataset.d6].auth references unknown",
        "[dataset.d6].redistributable must be a boolean",
        "[dataset.d6].sha256",
        "[dataset.d7] must be a table",
    ):
        assert needle in text, needle
    with pytest.raises(RegistryError, match="invalid sources.toml"):
        parse_sources(data)


def test_missing_operator_table() -> None:
    errors = validate_sources({"schema_version": 1})
    assert "missing [operator] table" in errors


def test_load_sources_file_and_id_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "extra_op"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sources.toml").write_text(
        'schema_version = 1\n[operator]\nid = "other"\nname = "X"\n'
        'country = "zz"\ntimezone = "UTC"\n',
        encoding="utf-8",
    )
    assert load_sources_file(pkg / "sources.toml").operator.id == "other"
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(reg_mod, "_entry_point_packages", lambda: {"zz": "extra_op"})
    reg_mod.operator_packages.cache_clear()
    reg_mod.load_registry.cache_clear()
    try:
        assert "zz" in available_operators()
        with pytest.raises(RegistryError, match="expected 'zz'"):
            load_registry("zz")
    finally:
        reg_mod.operator_packages.cache_clear()
        reg_mod.load_registry.cache_clear()


def test_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reg_mod, "_entry_point_packages", lambda: {"ghost": "no_such_pkg_xyz"}
    )
    reg_mod.operator_packages.cache_clear()
    reg_mod.load_registry.cache_clear()
    try:
        with pytest.raises(RegistryError, match="cannot locate"):
            load_registry("ghost")
    finally:
        reg_mod.operator_packages.cache_clear()
        reg_mod.load_registry.cache_clear()


def test_entry_points_failure_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_: Any) -> Any:
        raise RuntimeError("broken metadata")

    monkeypatch.setattr(reg_mod.importlib.metadata, "entry_points", _boom)
    assert reg_mod._entry_point_packages() == {}
