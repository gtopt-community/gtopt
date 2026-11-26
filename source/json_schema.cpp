/**
 * @file      json_schema.cpp
 * @brief     Implementation of the JSON Schema dump for the Planning model
 * @date      2026-06-08
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The schema is generated from the daw_json_link ``json_data_contract``
 * mappings by a small bespoke emitter (``SchemaBuilder`` below) instead
 * of ``daw::json::to_json_schema<T>``.
 *
 * Why not ``daw::json::to_json_schema``
 * -------------------------------------
 * The element-level field schedules (``OptTBRealFieldSched``, ...) map to
 * ``json_variant_type_list``s whose alternatives include *bare* scalar
 * types (``double``, ``int``, ``std::string``).  daw's variant handler
 * reads ``JsonElement::expected_type`` on every alternative, which bare
 * scalars do not have, so ``to_json_schema<Planning>`` fails to compile.
 * daw also inlines every nested class, which would repeat the element
 * definitions many times over.
 *
 * The bespoke emitter walks the same compile-time member lists, deduces
 * each variant alternative through ``json_link_no_name`` (so bare scalars
 * work), and emits one ``$defs`` entry per contract type with ``$ref``
 * links between them.  Every type reachable from ``Planning`` is covered,
 * so the dump is the complete input scheme and the single source the
 * Python tooling (igtopt template generator, contract snapshot) derives
 * its field lists from.
 */

#include <algorithm>
#include <array>
#include <cctype>
#include <format>
#include <iostream>
#include <iterator>
#include <map>
#include <set>
#include <source_location>
#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/json_schema.hpp>

namespace gtopt
{

namespace
{

using daw::json::JsonParseTypes;

/// Lowercase a name for case-insensitive matching of the requested type.
[[nodiscard]] std::string to_lower(std::string_view sv)
{
  std::string out;
  out.reserve(sv.size());
  std::ranges::transform(sv,
                         std::back_inserter(out),
                         [](unsigned char c)
                         { return static_cast<char>(std::tolower(c)); });
  return out;
}

/// ``GasPipe`` -> ``gas_pipe``; ``LpMatrixOptions`` -> ``lp_matrix_options``.
[[nodiscard]] std::string to_snake(std::string_view pascal)
{
  std::string out;
  out.reserve(pascal.size() + 4);
  for (std::size_t i = 0; i < pascal.size(); ++i) {
    const auto c = static_cast<unsigned char>(pascal[i]);
    if (std::isupper(c) != 0) {
      if (i > 0) {
        out.push_back('_');
      }
      out.push_back(static_cast<char>(std::tolower(c)));
    } else {
      out.push_back(static_cast<char>(c));
    }
  }
  return out;
}

/// Compiler-provided spelling of @p T (``gtopt::GasPipe``) with the
/// ``gtopt::`` prefix removed — the ``$defs`` key of the type.
template<typename T>
[[nodiscard]] std::string type_name()
{
  const std::string_view fn = std::source_location::current().function_name();
  // GCC:   "... type_name() [with T = gtopt::Bus; std::string = ...]"
  // Clang: "... type_name() [T = gtopt::Bus]"
  constexpr std::string_view marker = "T = ";
  const auto pos = fn.find(marker);
  if (pos == std::string_view::npos) {
    return std::string {fn};
  }
  auto name = fn.substr(pos + marker.size());
  int depth = 0;
  std::size_t end = 0;
  for (; end < name.size(); ++end) {
    const char ch = name[end];
    if (ch == '<') {
      ++depth;
    } else if (ch == '>') {
      --depth;
    } else if (depth == 0 && (ch == ';' || ch == ']' || ch == ',')) {
      break;
    }
  }
  name = name.substr(0, end);
  constexpr std::string_view ns = "gtopt::";
  if (name.starts_with(ns)) {
    name.remove_prefix(ns.size());
  }
  return std::string {name};
}

/// Render the JSON Schema of every contract type reachable from a root.
///
/// Each class becomes one ``$defs`` entry (properties in contract order,
/// ``additionalProperties: false`` — the parser is strict — and
/// ``required`` listing the non-nullable members); members referencing
/// another class use ``$ref``.  Field schedules render as ``oneOf`` of
/// their alternatives (scalar | nested arrays | file-name string).
class SchemaBuilder
{
public:
  template<typename T>
  std::string class_ref()
  {
    const auto name = type_name<T>();
    if (!m_defs_.contains(name)) {
      m_defs_[name] = {};  // placeholder: breaks recursive cycles
      m_defs_[name] = render_class(
          static_cast<daw::json::json_data_contract_trait_t<T>*>(nullptr));
    }
    return std::format(R"({{"$ref": "#/$defs/{}"}})", name);
  }

  [[nodiscard]] const std::map<std::string, std::string>& defs() const noexcept
  {
    return m_defs_;
  }

private:
  template<typename... Members>
  std::string render_class(daw::json::json_member_list<Members...>* /*tag*/)
  {
    std::string props;
    std::string required;
    std::set<std::string_view> seen;
    (
        [&]
        {
          const std::string_view key {Members::name.data(),
                                      Members::name.size()};
          if (!seen.insert(key).second) {
            return;
          }
          props += std::format("{}\n        \"{}\": {}",
                               props.empty() ? "" : ",",
                               key,
                               member_schema<Members>());
          if constexpr (!daw::json::json_details::is_json_nullable_v<Members>) {
            required +=
                std::format(R"({}"{}")", required.empty() ? "" : ", ", key);
          }
        }(),
        ...);
    return std::format(
        "{{\n      \"type\": \"object\",\n"
        "      \"additionalProperties\": false,\n"
        "      \"properties\": {{{}\n      }},\n"
        "      \"required\": [{}]\n    }}",
        props,
        required);
  }

  template<typename... Elements>
  std::string variant_schema(
      daw::json::json_variant_type_list<Elements...>* /*tag*/)
  {
    std::string alts;
    (
        [&]
        {
          alts += std::format(
              "{}{}",
              alts.empty() ? "" : ", ",
              member_schema<daw::json::json_link_no_name<Elements>>());
        }(),
        ...);
    return std::format(R"({{"oneOf": [{}]}})", alts);
  }

  template<typename M>
  std::string member_schema()
  {
    constexpr auto kind = M::expected_type;
    if constexpr (kind == JsonParseTypes::Null) {
      return member_schema<typename M::member_type>();
    } else if constexpr (kind == JsonParseTypes::Real) {
      return R"({"type": "number"})";
    } else if constexpr (kind == JsonParseTypes::Signed
                         || kind == JsonParseTypes::Unsigned)
    {
      return R"({"type": "integer"})";
    } else if constexpr (kind == JsonParseTypes::Bool) {
      return R"({"type": "boolean"})";
    } else if constexpr (kind == JsonParseTypes::StringRaw
                         || kind == JsonParseTypes::StringEscaped
                         // Custom members are string-converted enums
                         // (e.g. DirectiveKind).
                         || kind == JsonParseTypes::Custom)
    {
      return R"({"type": "string"})";
    } else if constexpr (kind == JsonParseTypes::Date) {
      return R"({"type": "string", "format": "date-time"})";
    } else if constexpr (kind == JsonParseTypes::Class) {
      return class_ref<daw::json::json_details::json_base_type_t<M>>();
    } else if constexpr (kind == JsonParseTypes::Array
                         || kind == JsonParseTypes::SizedArray)
    {
      return std::format(R"({{"type": "array", "items": {}}})",
                         member_schema<typename M::json_element_t>());
    } else if constexpr (kind == JsonParseTypes::KeyValue) {
      return std::format(R"({{"type": "object", "additionalProperties": {}}})",
                         member_schema<typename M::json_element_t>());
    } else if constexpr (kind == JsonParseTypes::Variant) {
      return variant_schema(static_cast<M::json_elements*>(nullptr));
    } else {
      return "{}";  // tagged variants / tuples: not used by gtopt contracts
    }
  }

  std::map<std::string, std::string> m_defs_;
};

/// All ``$defs`` reachable from ``Planning`` (built once).
[[nodiscard]] const std::map<std::string, std::string>& planning_defs()
{
  static const auto defs = []
  {
    SchemaBuilder builder;
    (void)builder.class_ref<Planning>();
    return builder.defs();
  }();
  return defs;
}

/// Names of the ``$defs`` reachable from @p root (including @p root).
[[nodiscard]] std::set<std::string> reachable_defs(const std::string& root)
{
  const auto& defs = planning_defs();
  std::set<std::string> out;
  std::vector<std::string> todo {root};
  constexpr std::string_view ref = "\"#/$defs/";
  while (!todo.empty()) {
    auto name = std::move(todo.back());
    todo.pop_back();
    if (!out.insert(name).second) {
      continue;
    }
    const auto it = defs.find(name);
    if (it == defs.end()) {
      continue;
    }
    const std::string_view body = it->second;
    for (auto pos = body.find(ref); pos != std::string_view::npos;
         pos = body.find(ref, pos + 1))
    {
      const auto start = pos + ref.size();
      const auto end = body.find('"', start);
      todo.emplace_back(body.substr(start, end - start));
    }
  }
  return out;
}

/// Selector names with a fixed spelling; every other ``$defs`` type is
/// also selectable by its snake_case name (e.g. ``gas_pipe``).
struct RootAlias
{
  std::string_view name;
  std::string_view type;
};

constexpr std::array root_aliases {
    RootAlias {
        .name = "planning",
        .type = "Planning",
    },
    RootAlias {
        .name = "options",
        .type = "PlanningOptions",
    },
};

/// Map a requested (case-insensitive) name to its ``$defs`` key.
[[nodiscard]] std::string resolve_root(std::string_view requested)
{
  const auto wanted = to_lower(requested);
  for (const auto& alias : root_aliases) {
    if (alias.name == wanted) {
      return std::string {alias.type};
    }
  }
  for (const auto& [name, body] : planning_defs()) {
    if (to_snake(name) == wanted || to_lower(name) == wanted) {
      return name;
    }
  }
  return {};
}

}  // namespace

const std::vector<std::string_view>& known_schema_names()
{
  static const std::vector<std::string> storage = []
  {
    std::vector<std::string> out;
    out.reserve(root_aliases.size() + planning_defs().size());
    for (const auto& alias : root_aliases) {
      out.emplace_back(alias.name);
    }
    std::vector<std::string> types;
    for (const auto& [name, body] : planning_defs()) {
      auto snake = to_snake(name);
      if (std::ranges::find(out, snake) == out.end()) {
        types.push_back(std::move(snake));
      }
    }
    std::ranges::sort(types);
    out.insert(out.end(), types.begin(), types.end());
    return out;
  }();
  static const std::vector<std::string_view> names = []
  {
    std::vector<std::string_view> out;
    out.reserve(storage.size());
    for (const auto& s : storage) {
      out.emplace_back(s);
    }
    return out;
  }();
  return names;
}

std::string dump_json_schema(std::string_view type_name)
{
  const auto root = type_name.empty() ? std::string {root_aliases[0].type}
                                      : resolve_root(type_name);

  if (root.empty()) {
    // Unknown type: mirror --list-dialects' unknown-filter behaviour —
    // print an error listing the selector names to stderr and signal
    // failure by returning an empty string.
    std::cerr << std::format(
        "ERROR: --json-schema: unknown type '{}'.  Known types: ", type_name);
    bool first = true;
    for (const auto& name : known_schema_names()) {
      std::cerr << (first ? "" : ", ") << name;
      first = false;
    }
    std::cerr << '\n';
    return {};
  }

  const auto& defs = planning_defs();
  std::string body;
  for (const auto& name : reachable_defs(root)) {
    body += std::format(
        "{}\n    \"{}\": {}", body.empty() ? "" : ",", name, defs.at(name));
  }
  return std::format(
      "{{\n  \"$schema\": \"https://json-schema.org/draft/2020-12/schema\",\n"
      "  \"$id\": \"gtopt.{0}\",\n"
      "  \"title\": \"gtopt {0} input schema\",\n"
      "  \"$ref\": \"#/$defs/{0}\",\n"
      "  \"$defs\": {{{1}\n  }}\n}}",
      root,
      body);
}

}  // namespace gtopt
