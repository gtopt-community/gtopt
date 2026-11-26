# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""plexos2gtopt ``--legacy-profile``: clean default vs the ``cen`` replica,
the deprecated ``--plexos-legacy`` alias, explicit-flag precedence and the
provenance record."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

from gtopt_shared.cli_flags import apply_legacy_profile
from plexos2gtopt.main import PLEXOS_LEGACY_PROFILES, main, make_parser
from plexos2gtopt.tests import horizon_fixture as fx

_CEN = {
    "reserve_load_term": "endogenous",
    "reserve_inertia_term": "endogenous",
    "bess_reserve": "uc",
    "el0_lines": "unenforced",
    "cf_comp_equality": True,
    "reserve_surplus_equality": True,
    "no_plp_embalses": True,
    "renewable_floors": "plexos",
}


def _resolve(argv: list[str]):
    args = make_parser().parse_args(["bundle", *argv])
    apply_legacy_profile(
        args,
        PLEXOS_LEGACY_PROFILES,
        alias_dest="plexos_legacy",
        alias_flag="--plexos-legacy",
        argv=argv,
    )
    return args


def test_default_is_the_clean_formulation() -> None:
    args = _resolve([])
    assert args.legacy_profile is None
    assert args.plexos_legacy is False
    assert args.legacy_toggles == []
    assert args.reserve_load_term == "exogenous"
    assert args.reserve_inertia_term == "commitment"
    assert args.bess_reserve == "native"
    assert args.el0_lines == "enforced"
    assert args.no_plp_embalses is False
    assert args.renewable_floors == "free"
    assert not getattr(args, "cf_comp_equality", False)
    assert not getattr(args, "reserve_surplus_equality", False)


@pytest.mark.parametrize("flag", [["--legacy-profile", "cen"], ["--plexos-legacy"]])
def test_cen_profile_and_alias(
    flag: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        args = _resolve(flag)
    assert args.legacy_profile == "cen"
    assert args.plexos_legacy is True
    for dest, value in _CEN.items():
        assert getattr(args, dest) == value, dest
    assert sorted(args.legacy_toggles) == sorted(f"{k}={v}" for k, v in _CEN.items())
    assert "--legacy-profile cen: applying" in caplog.text
    deprecated = "--plexos-legacy is deprecated" in caplog.text
    assert deprecated is (flag == ["--plexos-legacy"])


def test_explicit_flags_override_cen() -> None:
    args = _resolve(
        ["--legacy-profile", "cen", "--el0-lines", "enforced", "--bess-reserve=native"]
    )
    assert args.el0_lines == "enforced"
    assert args.bess_reserve == "native"
    assert args.reserve_load_term == "endogenous"
    assert "el0_lines=unenforced" not in args.legacy_toggles
    assert "bess_reserve=uc" not in args.legacy_toggles


def test_help_lists_the_cen_toggles() -> None:
    flat = re.sub(r"\s+", " ", make_parser().format_help())
    assert "--legacy-profile {cen}" in flat
    assert "Profile 'cen' applies: --reserve-load-term endogenous" in flat
    assert "--el0-lines unenforced" in flat
    assert "reserve_surplus_equality (internal)" in flat
    assert "--plexos-legacy deprecated alias of --legacy-profile cen" in flat


@pytest.mark.parametrize(
    ("flags", "profile"),
    [([], None), (["--legacy-profile", "cen", "--el0-lines", "extended"], "cen")],
)
def test_provenance_records_the_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    profile: str | None,
) -> None:
    monkeypatch.setenv("GTOPT_EL0_LINES", "")
    monkeypatch.delenv("GTOPT_EL0_LINES")
    bundle = fx.write_bundle(tmp_path / "bundle")
    out = tmp_path / "out"
    argv = [
        str(bundle),
        "-o",
        str(out),
        "--no-check",
        "--horizon-mode",
        "hourly",
        "--horizon-days",
        "1",
        "--lp-relax",
        *flags,
    ]
    try:
        main(argv)
    except SystemExit as exc:
        assert not exc.code
    (prov_path,) = out.glob("*.provenance.json")
    prov = json.loads(prov_path.read_text())
    assert prov["renewable_floors"]["mode"] == ("free" if profile is None else "plexos")
    assert prov["legacy_profile"]["profile"] == profile
    toggles = prov["legacy_profile"]["toggles"]
    if profile is None:
        assert toggles == []
        assert prov["el0_lines"] == "strict"
    else:
        assert "reserve_load_term=endogenous" in toggles
        assert not any(t.startswith("el0_lines=") for t in toggles)
        assert prov["el0_lines"] == "extended"
