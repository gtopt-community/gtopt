# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for gtopt_field_extractor parsing / rendering / CLI."""

from __future__ import annotations

from unittest.mock import patch

from gtopt_field_extractor import gtopt_field_extractor as fe

_HEADER = """\
struct Forward
int not_a_brace;

/**
 * Plain block without a brief tag.
 */
struct Plain
{
  /// The widget capacity [MW]. More text follows here.
  double capacity {};
  /// @brief not a field doc
  ///< stray inline marker
  int unused_int {};
  OptReal pmin {};  ///< Minimum power [MW]
};

/**
 * @details Only details, no brief.
 */
struct OnlyDetails
{
  Uid uid {};  ///< Unique id
};

/**
 * @brief Detailed struct
 * @details Extra details here.
 */
struct Detailed
{
  Uid uid {};  ///< Unique id
};
"""


def test_first_sentence_variants():
    assert fe._first_sentence("One. Two.") == "One."
    assert fe._first_sentence("no stop") == "no stop"
    long = "x" * 200
    out = fe._first_sentence(long, limit=10)
    assert out.endswith("…") and len(out) == 10


def test_parse_header_bare_fields_and_forward_decl(tmp_path):
    hpp = tmp_path / "demo.hpp"
    hpp.write_text(_HEADER, encoding="utf-8")
    structs = {s.struct_name: s for s in fe.parse_header(hpp)}
    assert "Forward" not in structs
    plain = structs["Plain"]
    assert plain.brief == ""
    names = [f.name for f in plain.fields]
    assert names == ["capacity", "pmin"]
    cap = plain.fields[0]
    assert cap.description.startswith("The widget capacity")
    assert cap.units == "MW"
    assert structs["Detailed"].brief == "Detailed struct"
    assert structs["Detailed"].details == "Extra details here."

    md = fe.render_markdown(structs, ["Plain", "Missing", "Detailed"])
    assert "## Plain" in md and "*Detailed struct*" in md
    assert "Extra details here." in md
    html = fe.render_html(structs, ["Plain", "Missing", "Detailed", "OnlyDetails"])
    assert "<p>Only details, no brief.</p>" in html
    assert '<h2 id="plain">Plain</h2>' in html
    assert "<p>Extra details here.</p>" in html


def test_contract_elements_fallback():
    with patch(
        "gtopt_shared.json_contracts.array_types", side_effect=KeyError("System")
    ):
        assert fe._contract_elements("System", ["A", "B"]) == ["A", "B"]


def test_main_autodetects_header_dir(tmp_path, capsys, outside_checkout_dir):
    root = tmp_path / "repo"
    (root / "include" / "gtopt").mkdir(parents=True)
    (root / "include" / "gtopt" / "demo.hpp").write_text(_HEADER, encoding="utf-8")
    with patch.object(fe, "_find_repo_root", return_value=root):
        assert fe.main(["-e", "Plain"]) == 0
    assert "## Plain" in capsys.readouterr().out
    assert fe._find_repo_root(outside_checkout_dir) == outside_checkout_dir.resolve()
