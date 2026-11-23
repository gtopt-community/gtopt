/**
 * @file      json_canonicalize.cpp
 * @brief     Implementation of the JSON key-alias rewriter
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstddef>
#include <format>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

#include <gtopt/json_canonicalize.hpp>
#include <gtopt/names_registry.hpp>
#include <gtopt/unit_registry.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// Skip over a JSON string literal whose opening quote is at
/// `pos`. Returns the index of the character after the closing quote.
/// Handles `\\`, `\"`, and other JSON escape sequences without
/// interpreting them.
[[nodiscard]] std::size_t skip_string(std::string_view s, std::size_t pos)
{
  // pos points at the opening '"'
  ++pos;  // step past opening quote
  while (pos < s.size()) {
    const char c = s[pos];
    if (c == '\\') {
      // Skip escape pair; `\u00XX` is also two chars after `\` which
      // we conservatively step over the same way (the first char `u`
      // is consumed, then the loop advances over the hex digits as
      // regular non-quote chars).
      pos += 2;
      continue;
    }
    if (c == '"') {
      return pos + 1;  // index past closing quote
    }
    ++pos;
  }
  return pos;
}

/// Read the unescaped content of a JSON string literal starting at
/// position `pos` (the opening quote).  Returns the content as a
/// `string_view` into `s` — valid only as long as `s` is alive.
/// For aliases (the lookup key in the registry) we currently expect
/// ASCII names with no escape sequences, so we slice the raw bytes
/// between the quotes and skip the lookup when an escape is present.
[[nodiscard]] std::string_view raw_string_content(std::string_view s,
                                                  std::size_t open_quote_pos,
                                                  std::size_t close_quote_pos)
{
  // [open_quote_pos+1 .. close_quote_pos-1)
  if (close_quote_pos <= open_quote_pos + 1) {
    return {};
  }
  return s.substr(open_quote_pos + 1, close_quote_pos - open_quote_pos - 2);
}

/// True if the string content has no JSON escape sequences (so the
/// raw bytes between the quotes equal the logical string value).
[[nodiscard]] bool is_simple_string(std::string_view content) noexcept
{
  return !content.contains('\\');
}

/// Warn once per process about a deprecated alias key (a renamed gtopt
/// key flagged `"deprecated": true` in naming_dialects.json).
void warn_deprecated_once(std::string_view alias, std::string_view canonical)
{
  static std::mutex mutex;
  static std::set<std::string, std::less<>> warned;
  {
    const std::scoped_lock lock {mutex};
    if (!warned.emplace(alias).second) {
      return;
    }
  }
  spdlog::warn("deprecated JSON key '{}': use '{}' instead", alias, canonical);
}

/// Throw when the JSON object `object_text` (from its `{` to its `}`)
/// sets a deprecated alias together with its canonical key — after the
/// rewrite both would land on the same member and one would be silently
/// dropped.  Only called for objects that contain a deprecated alias.
void check_deprecated_conflicts(std::string_view object_text,
                                const NamesRegistry& registry)
{
  // (raw key, canonical key) for every top-level key of the object.
  std::vector<std::pair<std::string_view, std::string_view>> keys;
  int depth = 0;
  bool expecting_key = false;
  std::size_t i = 0;
  while (i < object_text.size()) {
    const char c = object_text[i];
    if (c == '"') {
      const std::size_t end = skip_string(object_text, i);
      if (depth == 1 && expecting_key) {
        const auto key = raw_string_content(object_text, i, end);
        keys.emplace_back(key, registry.canonical_for(key).value_or(key));
        expecting_key = false;
      }
      i = end;
      continue;
    }
    if (c == '{' || c == '[') {
      ++depth;
      expecting_key = (depth == 1);
    } else if (c == '}' || c == ']') {
      --depth;
    } else if (c == ',' && depth == 1) {
      expecting_key = true;
    }
    ++i;
  }

  for (const auto& [raw, canonical] : keys) {
    if (!registry.is_deprecated(raw)) {
      continue;
    }
    for (const auto& [other_raw, other_canonical] : keys) {
      if (other_raw != raw && other_canonical == canonical) {
        throw std::invalid_argument(std::format(
            "JSON object sets both '{}' and '{}' ('{}' is a deprecated "
            "alias of '{}') — keep only '{}'",
            other_raw,
            raw,
            raw,
            canonical,
            canonical));
      }
    }
  }
}

}  // namespace

std::string canonicalize_json_keys(std::string_view json_text,
                                   const NamesRegistry& registry,
                                   std::string_view enforce_dialect)
{
  // Quick reject: if the registry is empty, return a copy.
  if (registry.size() == 0) {
    return std::string {json_text};
  }

  // Set of aliases we have already warned about so the log stays
  // compact: one line per misfit alias regardless of how many times it
  // appears in the document.  Local to this call.
  std::unordered_set<std::string> warned_aliases;

  std::string out;
  out.reserve(json_text.size());

  // Track whether the next quoted string is a key (object-key
  // position) or a value.
  //
  // We maintain a small stack of container states.  Top of stack
  // tells us whether we're inside an object (then quoted strings
  // after `{` or `,` are keys until the next `:`).
  struct Frame
  {
    bool in_object;  // true if frame opened with '{'
    bool expecting_key;  // true if the next quoted string is a key
    std::size_t open_pos {0};  // input offset of the opening '{'
    bool has_deprecated {false};  // object holds a deprecated alias key
  };
  std::vector<Frame> stack;
  stack.reserve(16);

  std::size_t i = 0;
  while (i < json_text.size()) {
    const char c = json_text[i];

    switch (c) {
      case '{': {
        out.push_back(c);
        stack.push_back({
            .in_object = true,
            .expecting_key = true,
            .open_pos = i,
        });
        ++i;
        continue;
      }
      case '[': {
        out.push_back(c);
        stack.push_back({.in_object = false, .expecting_key = false});
        ++i;
        continue;
      }
      case '}':
      case ']': {
        out.push_back(c);
        if (!stack.empty()) {
          if (c == '}' && stack.back().has_deprecated) {
            const auto open = stack.back().open_pos;
            check_deprecated_conflicts(json_text.substr(open, i + 1 - open),
                                       registry);
          }
          stack.pop_back();
        }
        ++i;
        continue;
      }
      case ',': {
        out.push_back(c);
        if (!stack.empty() && stack.back().in_object) {
          stack.back().expecting_key = true;
        }
        ++i;
        continue;
      }
      case ':': {
        out.push_back(c);
        if (!stack.empty() && stack.back().in_object) {
          stack.back().expecting_key = false;
        }
        ++i;
        continue;
      }
      case '"': {
        const std::size_t end = skip_string(json_text, i);
        const bool is_key = !stack.empty() && stack.back().in_object
            && stack.back().expecting_key;

        if (is_key) {
          const auto content = raw_string_content(json_text, i, end);
          if (is_simple_string(content)) {
            if (const auto canonical = registry.canonical_for(content);
                canonical.has_value())
            {
              // Optional input warn: alias's source dialect differs
              // from the user-enforced dialect.  Hot path only emits
              // the lookup + once-per-alias warn when enforce_dialect
              // is non-empty.
              if (!enforce_dialect.empty()) {
                const auto alias_dialect = registry.dialect_for(content);
                if (alias_dialect.has_value()
                    && *alias_dialect != enforce_dialect)
                {
                  const std::string alias_key {content};
                  if (warned_aliases.insert(alias_key).second) {
                    // Escalate to error when the per-dialect unit
                    // registry confirms the two dialects disagree on
                    // the unit of this canonical — the numeric value
                    // in the JSON almost certainly needs a conversion
                    // the user did not perform.  Falls back to a
                    // plain warn when units are unknown or ambiguous.
                    const auto& units = UnitRegistry::instance();
                    const auto src_unit = units.class_agnostic_unit_for(
                        *canonical, *alias_dialect);
                    const auto tgt_unit = units.class_agnostic_unit_for(
                        *canonical, enforce_dialect);
                    if (src_unit.has_value() && tgt_unit.has_value()
                        && *src_unit != *tgt_unit)
                    {
                      spdlog::error(
                          "naming-dialect: UNIT MISMATCH on input alias "
                          "'{}' (dialect '{}', unit '{}') vs "
                          "--naming-dialect '{}' (unit '{}') for canonical "
                          "'{}' — the value will be parsed as-is; manual "
                          "conversion required",
                          content,
                          *alias_dialect,
                          *src_unit,
                          enforce_dialect,
                          *tgt_unit,
                          *canonical);
                    } else {
                      spdlog::warn(
                          "naming-dialect: input alias '{}' belongs to dialect "
                          "'{}' but --naming-dialect is '{}' (canonical: '{}')",
                          content,
                          *alias_dialect,
                          enforce_dialect,
                          *canonical);
                    }
                  }
                }
              }
              if (registry.is_deprecated(content)) {
                warn_deprecated_once(content, *canonical);
                stack.back().has_deprecated = true;
              }
              out.push_back('"');
              out.append(*canonical);
              out.push_back('"');
              i = end;
              continue;
            }
          }
        }
        // Default: copy verbatim.
        out.append(json_text.substr(i, end - i));
        i = end;
        continue;
      }
      default: {
        out.push_back(c);
        ++i;
        continue;
      }
    }
  }
  return out;
}

std::string canonicalize_json_keys(std::string_view json_text,
                                   const NamesRegistry& registry)
{
  return canonicalize_json_keys(json_text, registry, /*enforce_dialect=*/"");
}

std::string canonicalize_json_keys(std::string_view json_text)
{
  return canonicalize_json_keys(json_text, NamesRegistry::instance());
}

std::string canonicalize_json_keys(std::string_view json_text,
                                   std::string_view enforce_dialect)
{
  return canonicalize_json_keys(
      json_text, NamesRegistry::instance(), enforce_dialect);
}

// ─── Inverse pass: canonical → dialect alias (output rename) ───────────────

std::string decanonicalize_json_keys(std::string_view json_text,
                                     const NamesRegistry& registry,
                                     std::string_view dialect)
{
  if (dialect.empty() || registry.size() == 0) {
    return std::string {json_text};
  }

  std::string out;
  out.reserve(json_text.size());

  struct Frame
  {
    bool in_object;
    bool expecting_key;
  };
  std::vector<Frame> stack;
  stack.reserve(16);

  std::size_t i = 0;
  while (i < json_text.size()) {
    const char c = json_text[i];
    switch (c) {
      case '{':
        out.push_back(c);
        stack.push_back({.in_object = true, .expecting_key = true});
        ++i;
        continue;
      case '[':
        out.push_back(c);
        stack.push_back({.in_object = false, .expecting_key = false});
        ++i;
        continue;
      case '}':
      case ']':
        out.push_back(c);
        if (!stack.empty()) {
          stack.pop_back();
        }
        ++i;
        continue;
      case ',':
        out.push_back(c);
        if (!stack.empty() && stack.back().in_object) {
          stack.back().expecting_key = true;
        }
        ++i;
        continue;
      case ':':
        out.push_back(c);
        if (!stack.empty() && stack.back().in_object) {
          stack.back().expecting_key = false;
        }
        ++i;
        continue;
      case '"': {
        const std::size_t end = skip_string(json_text, i);
        const bool is_key = !stack.empty() && stack.back().in_object
            && stack.back().expecting_key;
        if (is_key) {
          const auto content = raw_string_content(json_text, i, end);
          if (is_simple_string(content)) {
            if (const auto alias = registry.alias_for(content, dialect);
                alias.has_value())
            {
              out.push_back('"');
              out.append(*alias);
              out.push_back('"');
              i = end;
              continue;
            }
          }
        }
        out.append(json_text.substr(i, end - i));
        i = end;
        continue;
      }
      default:
        out.push_back(c);
        ++i;
        continue;
    }
  }
  return out;
}

std::string decanonicalize_json_keys(std::string_view json_text,
                                     std::string_view dialect)
{
  return decanonicalize_json_keys(
      json_text, NamesRegistry::instance(), dialect);
}

}  // namespace gtopt
