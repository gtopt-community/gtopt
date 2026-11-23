# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt._id_bridge`."""

from __future__ import annotations

import pandas as pd

from cen2gtopt._id_bridge import _aliases, build_alias_index, index_stats, norm, resolve


def test_norm() -> None:
    assert norm(None) == ""
    assert norm(float("nan")) == ""
    assert norm("Ñuble-1 Á") == "nuble1a"


def test_aliases_short_and_prefix_forms() -> None:
    assert _aliases("") == []
    assert _aliases("A") == []
    al = set(_aliases("TER NEHUENCO 9B"))
    assert {"ternehuenco9b", "nehuenco9b", "nehuenco", "ternehuenco"} <= al
    # Block suffix stripped iteratively.
    assert "cmpclaja" in _aliases("CMPC LAJA BL1")
    # PLEXOS-style name: first alpha token captured.
    al2 = set(_aliases("NEHUENCO_1-FA_GN_A"))
    assert {"nehuenco", "ternehuenco"} <= al2
    # Short first token (< 4 letters) is not added on its own.
    assert "abc" not in _aliases("ABC_1-X")
    # Name not starting with a letter → no first-token alias.
    assert all(a for a in _aliases("123 456"))
    # First token already starting with "ter" is not re-prefixed.
    assert "terterra" not in _aliases("Terra Nova")


def test_build_alias_index_units_and_centrales() -> None:
    units = pd.DataFrame(
        [
            {"id_central": 7, "central": "TER ATACAMA", "unidad_nombre": None},
            {"id_central": 3, "central": "ATACAMA", "unidad_nemotecnico": "X1"},
            {"id_central": None, "central": "GHOST"},
            {"id_central": "bad", "central": "BADID"},
        ]
    )
    cent = pd.DataFrame(
        [
            {"id_central": 11, "central": "LAJA", "instalacion": "HE LAJA"},
            {"id_central": None, "central": "NOPE"},
            {"id_central": "x", "central": "BAD"},
        ]
    )
    idx = build_alias_index(units, cent)
    # Collision resolves to the lowest id.
    assert idx["atacama"] == 3
    assert idx["laja"] == 11
    assert "ghost" not in idx
    assert "badid" not in idx
    assert "nope" not in idx
    stats = index_stats(idx)
    assert stats["distinct_id_centrales"] == 2
    assert stats["alias_keys"] == len(idx)


def test_build_alias_index_degenerate_inputs() -> None:
    assert not build_alias_index(pd.DataFrame())
    assert not build_alias_index(pd.DataFrame({"central": ["A"]}))
    assert not build_alias_index(pd.DataFrame(), pd.DataFrame())
    assert not build_alias_index(pd.DataFrame(), pd.DataFrame({"central": ["A"]}))


def test_resolve_with_fallbacks() -> None:
    idx = {"laja": 11}
    assert resolve("HE LAJA", idx) == 11
    assert resolve(None, idx, "", "LAJA") == 11
    assert resolve("UNKNOWN", idx, None) is None
