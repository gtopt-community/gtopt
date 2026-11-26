# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for the header parser, walker and CLI of json_contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtopt_shared import json_contracts as jc

_HEADER = """
// struct json_data_contract<Ignored> { using type = json_member_list<
//   json_number<"x">>; };
struct json_data_contract<Orphan> { };
struct json_data_contract<gtopt::Inner>
{
  using type = json_member_list<
      json_number<"a">,
      json_number_null<"a", std::optional<double>>,
      not_a_member,
      json_string<"b">>;
};
struct json_data_contract<Outer>
{
  using type = json_member_list<
      json_class<"inner", std::optional<Inner>>,
      json_array<"items", Array<Inner>, std::vector<OptInner>>,
      json_variant<"v", std::variant<int, double>>,
      json_class<"other", Unknown>>;
};
"""


def _repo(tmp_path: Path, header: str = _HEADER) -> Path:
    jdir = tmp_path / "include" / "gtopt" / "json"
    jdir.mkdir(parents=True)
    (jdir / "json_test.hpp").write_text(header, encoding="utf-8")
    share = tmp_path / "share" / "gtopt"
    share.mkdir(parents=True)
    (share / "naming_dialects.json").write_text(
        json.dumps({"aliases": [{"alt": "old_b"}, {"alt": ""}, {}]}),
        encoding="utf-8",
    )
    return tmp_path


def test_parse_headers_contracts_and_nesting(tmp_path):
    root = _repo(tmp_path)
    types = jc.parse_contract_headers(root / "include" / "gtopt" / "json")
    assert "Orphan" not in types
    assert types["Inner"]["fields"] == ["a", "b"]
    assert types["Inner"]["kinds"] == {"a": "number", "b": "string"}
    assert types["Outer"]["nested"] == {"inner": "Inner", "items": "Inner"}
    # A trailing contract with no member list at all is skipped.
    (root / "include" / "gtopt" / "json" / "json_zz.hpp").write_text(
        "struct json_data_contract<Last> {};", encoding="utf-8"
    )
    assert "Last" not in jc.parse_contract_headers(root / "include" / "gtopt" / "json")
    snap = jc.build_snapshot(root)
    assert snap["dialect_aliases"] == ["old_b"]


def test_parse_headers_duplicate_and_unbalanced(tmp_path):
    jdir = _repo(tmp_path) / "include" / "gtopt" / "json"
    (jdir / "json_dup.hpp").write_text(
        'struct json_data_contract<Inner> { using type = json_member_list<json_string<"z">>; };',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate json_data_contract<Inner>"):
        jc.parse_contract_headers(jdir)
    with pytest.raises(ValueError, match="unbalanced"):
        jc._matching_angle("<<a>", 0)


def test_split_top_level_handles_nesting():
    assert jc._split_top_level("a<b, c>, d(e, f), ") == ["a<b, c>", "d(e, f)"]


def test_find_repo_root_and_snapshot_helpers(tmp_path, outside_checkout_dir):
    assert jc.find_repo_root(outside_checkout_dir) is None
    root = _repo(tmp_path / "r")
    assert jc.find_repo_root(root / "include") == root
    assert jc.snapshot_path().name == "json_contracts.json"
    assert "uid" in jc.contract_fields("Generator")
    assert not jc.contract_fields("NoSuchType")
    arrays = jc.array_types()
    assert arrays and all(k.endswith("_array") for k in arrays)


def test_find_schema_problems_walks_lists_and_scalars():
    snap = {
        "root": "Outer",
        "types": {
            "Outer": {
                "fields": ["items", "n"],
                "nested": {"items": "Inner", "n": "Nope"},
            },
            "Inner": {"fields": ["a"], "nested": {}},
        },
        "dialect_aliases": [],
    }
    issues = jc.find_schema_problems(
        {"items": [{"a": 1, "zz": 2}, 5], "n": {"anything": 1}}, snapshot=snap
    )
    assert [(i.kind, i.path) for i in issues] == [("unknown", "items[0].zz")]


def test_main_check_update_and_missing_root(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path / "repo")
    target = tmp_path / "snap.json"
    monkeypatch.setattr(jc, "snapshot_path", lambda: target)

    assert jc.main(["--repo-root", str(root)]) == 1  # missing target → stale
    assert "is stale" in capsys.readouterr().err
    assert jc.main(["--update", "--repo-root", str(root)]) == 0
    assert target.is_file()
    assert jc.main(["--check", "--repo-root", str(root)]) == 0
    assert "up to date" in capsys.readouterr().out

    monkeypatch.setattr(jc, "find_repo_root", lambda: None)
    assert jc.main([]) == 2
    assert "checkout not found" in capsys.readouterr().err


# ── gtopt --json-schema source ─────────────────────────────────────────────

_FAKE_SCHEMA = {
    "$defs": {
        "Planning": {
            "type": "object",
            "properties": {
                "system": {"$ref": "#/$defs/System"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
        "System": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "bus_array": {"type": "array", "items": {"$ref": "#/$defs/Bus"}},
            },
            "required": ["name"],
        },
        "Bus": {
            "type": "object",
            "properties": {
                "uid": {"type": "integer"},
                "voltage": {"oneOf": [{"type": "number"}, {"type": "string"}]},
                "active": {"type": "boolean"},
                "kind": {"type": "string"},
                "extra": {"type": "object"},
                "ref": {"$ref": 3},
            },
            "required": ["uid"],
        },
    }
}


def _fake_gtopt(tmp_path, stdout: str, rc: int = 0) -> str:
    out = tmp_path / "schema_out.txt"
    out.write_text(stdout, encoding="utf-8")
    exe = tmp_path / "fake_gtopt"
    exe.write_text(f"#!/bin/sh\ncat '{out}'\nexit {rc}\n", encoding="utf-8")
    exe.chmod(0o755)
    return str(exe)


def test_types_from_json_schema_kinds_and_nesting():
    types = jc.types_from_json_schema(_FAKE_SCHEMA)
    assert types["System"]["required"] == ["name"]
    assert types["System"]["nested"] == {"bus_array": "Bus"}
    assert types["Planning"]["nested"] == {"system": "System"}
    assert types["Bus"]["kinds"] == {
        "uid": "number",
        "voltage": "variant",
        "active": "bool",
        "kind": "string",
        "extra": "class",
        "ref": "string",
    }


def test_gtopt_json_schema_success(tmp_path):
    schema = dict(_FAKE_SCHEMA)
    schema["$defs"] = {**_FAKE_SCHEMA["$defs"], jc.ROOT_TYPE: {"properties": {}}}
    exe = _fake_gtopt(tmp_path, json.dumps(schema))
    assert jc.gtopt_json_schema(exe) == schema
    types, label = jc.load_contract_types("auto", gtopt_bin=exe)
    assert label == "gtopt --json-schema"
    assert jc.ROOT_TYPE in types


@pytest.mark.parametrize(
    ("stdout", "rc"),
    [
        ("{}", 1),
        ("not json", 0),
        ("[1, 2]", 0),
        ('{"$defs": {"PlanningOptions": {}}}', 0),
    ],
)
def test_gtopt_json_schema_rejects_bad_output(tmp_path, stdout, rc):
    assert jc.gtopt_json_schema(_fake_gtopt(tmp_path, stdout, rc)) is None


def test_gtopt_json_schema_unrunnable_binary(tmp_path):
    missing = tmp_path / "no_such_gtopt"
    assert jc.gtopt_json_schema(str(missing)) is None


def test_gtopt_json_schema_no_binary_found(monkeypatch):
    # pylint: disable-next=import-outside-toplevel
    from gtopt_shared import gtopt_binary

    monkeypatch.setattr(gtopt_binary, "find_gtopt_binary", lambda: None)
    assert jc.gtopt_json_schema() is None


def test_load_contract_types_sources(tmp_path):
    with pytest.raises(ValueError, match="unknown contract source"):
        jc.load_contract_types("bogus")
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(_FAKE_SCHEMA), encoding="utf-8")
    types, label = jc.load_contract_types("gtopt", schema_file=schema_file)
    assert "Bus" in types and label.startswith("schema file")
    bad = _fake_gtopt(tmp_path, "", 1)
    with pytest.raises(ValueError, match="unavailable"):
        jc.load_contract_types("gtopt", gtopt_bin=bad)
    types, label = jc.load_contract_types("auto", gtopt_bin=bad)
    assert label == "bundled contract snapshot" and jc.ROOT_TYPE in types
    _, label = jc.load_contract_types("snapshot")
    assert label == "bundled contract snapshot"


def test_contract_diff_reports_missing_and_mismatch():
    snapshot = {
        "Bus": {
            "fields": ["uid", "kind"],
            "required": ["uid"],
            "kinds": {"uid": "number", "kind": "custom"},
            "nested": {},
        }
    }
    live = {
        "Bus": {
            "fields": ["uid", "kind"],
            "required": ["uid"],
            "kinds": {"uid": "number", "kind": "string"},
            "nested": {},
        },
        "Gone": {"fields": []},
    }
    assert jc.contract_diff(live, snapshot) == ["Gone: missing from the snapshot"]
    live["Bus"]["fields"] = ["uid"]
    assert jc.contract_diff(live, snapshot)[0].startswith("Bus.fields")
