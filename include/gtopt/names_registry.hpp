/**
 * @file      names_registry.hpp
 * @brief     Runtime-loadable dictionary of gtopt JSON key aliases
 * @date      2026-05-17
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Loads `share/gtopt/naming_dialects.json` (or an embedded fallback)
 * and exposes a flat `alias -> canonical` map used by
 * `json_canonicalize.hpp` to rewrite alternative JSON key names to
 * the canonical gtopt names accepted by `from_json<Planning>` under
 * `StrictParsePolicy`.
 *
 * Resolution order (deterministic and version-safe):
 *
 *   1. `$GTOPT_NAMING_DIALECTS_FILE` — explicit override file (logged at
 *      info level; a warning flags an override that lacks aliases of the
 *      compiled-in dictionary, i.e. a probably stale copy)
 *   2. Compiled-in dictionary — `share/gtopt/naming_dialects.json`
 *      embedded at configure time, so it always matches the binary
 *   3. Empty registry (graceful degradation, only if 1-2 fail to parse)
 *
 * Install / user share dirs (`<prefix>/share/gtopt/`,
 * `~/.local/share/gtopt/`) are never probed: a stale installed copy
 * must not shadow the dictionary of the running binary.
 *
 * The registry is loaded once on first access (singleton); subsequent
 * lookups are O(log n) via `gtopt::flat_map` (vector-backed; the
 * dictionary is small — ~50-200 entries — so cache-friendly linear
 * memory wins over hash-table indirection).
 */

#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/fmap.hpp>

namespace gtopt
{

class NamesRegistry
{
public:
  /// Get the process-wide singleton, lazily loaded on first call.
  /// Lookups against the returned reference are thread-safe (the map
  /// is `const` after construction).
  [[nodiscard]] static const NamesRegistry& instance();

  /// Construct from an explicit JSON string (used by tests and for
  /// the `instance()` boot path). Throws on parse error.
  explicit NamesRegistry(std::string_view json_content);

  /// Construct from a JSON file. Throws on read or parse error.
  explicit NamesRegistry(const std::filesystem::path& json_path);

  /// Resolve a **global** alias to its canonical name.  Returns
  /// `nullopt` if `alias` is not a registered global alias (which
  /// includes the case where `alias` is itself already a canonical
  /// name).  Class-scoped aliases are not consulted by this overload
  /// — use the (class, alias) overload below from element-typed
  /// resolution paths.  This overload is used by `canonicalize_json_keys`
  /// (which has no element-type context).
  [[nodiscard]] std::optional<std::string_view> canonical_for(
      std::string_view alias) const noexcept;

  /// Resolve an alias scoped to a specific element class
  /// (`"generator"`, `"flow_right"`, …).  Looks up the class-scoped
  /// table first (`class_aliases[]` in naming_dialects.json), then
  /// falls back to the global table.  Used by `resolve_single_param`
  /// where the element class is known and aliases like `discharge`
  /// (canonical for `flow`, legacy for `flow_right.target`) need
  /// class-aware disambiguation.
  [[nodiscard]] std::optional<std::string_view> canonical_for(
      std::string_view class_name, std::string_view alias) const noexcept;

  /// Number of (alias, canonical) entries. Useful for diagnostics
  /// and tests.
  [[nodiscard]] std::size_t size() const noexcept
  {
    return m_alias_to_canonical_.size();
  }

  /// Returns the (canonical -> {aliases}) inverse view for global
  /// aliases.  Used for diagnostics (e.g. `--list-dialects`).
  /// Class-scoped aliases are not included.
  [[nodiscard]] const gtopt::flat_map<std::string, std::vector<std::string>>&
  canonical_to_aliases() const noexcept
  {
    return m_canonical_to_aliases_;
  }

  /// True when `alias` is a global alias flagged `"deprecated": true`
  /// in naming_dialects.json — a renamed gtopt key kept only for
  /// back-compat.  `canonicalize_json_keys` warns once per such alias
  /// and rejects an object that sets both it and its canonical key.
  [[nodiscard]] bool is_deprecated(std::string_view alias) const noexcept;

  /// Look up the source dialect tag of a given alias (the `dialect`
  /// field on the entry in `naming_dialects.json`).  Returns nullopt
  /// when the alias is unknown or its dialect tag was empty.  Used by
  /// the input warn pass to flag mixed-dialect input under
  /// `--naming-dialect`.
  [[nodiscard]] std::optional<std::string_view> dialect_for(
      std::string_view alias) const noexcept;

  /// Inverse lookup: find the alias from `dialect` that maps to the
  /// given canonical name.  Returns nullopt when no entry matches —
  /// the caller should keep the canonical name unchanged on output.
  /// Used by the output JSON rewrite under `--naming-dialect`.
  [[nodiscard]] std::optional<std::string_view> alias_for(
      std::string_view canonical, std::string_view dialect) const noexcept;

  /// Path the registry was loaded from, or `nullopt` if loaded from
  /// the built-in fallback or an explicit JSON string.
  [[nodiscard]] const std::optional<std::filesystem::path>& source_path()
      const noexcept
  {
    return m_source_path_;
  }

private:
  /// Build the flat maps from a parsed JSON document text.
  /// Throws `std::runtime_error` if the document is malformed.
  void build_from_json(std::string_view json_content);

  /// Detect alias / canonical collisions while building. An alias
  /// must not be registered as the canonical of another field, and
  /// no alias may appear under more than one canonical (per the
  /// uniqueness invariant of §10.4).
  void check_alias_uniqueness(std::string_view alias,
                              std::string_view canonical) const;

  gtopt::flat_map<std::string, std::string> m_alias_to_canonical_;
  gtopt::flat_map<std::string, std::vector<std::string>>
      m_canonical_to_aliases_;
  /// Class-scoped lookup table.  Key is `(class_name, alias)`; value
  /// is the canonical attribute name within that class.  Populated
  /// from `class_aliases[]` in naming_dialects.json.  Looked up only
  /// via the (class, alias) overload of `canonical_for`.
  gtopt::flat_map<std::pair<std::string, std::string>, std::string>
      m_class_alias_to_canonical_;
  /// Per-alias source dialect tag.  Populated alongside
  /// `m_alias_to_canonical_` from each entry's `dialect` field; entries
  /// with an empty `dialect` are omitted (no warning surface possible).
  gtopt::flat_map<std::string, std::string> m_alias_to_dialect_;
  /// Global aliases flagged `"deprecated": true` (value unused).
  gtopt::flat_map<std::string, bool> m_deprecated_aliases_;
  /// (canonical, dialect) → alias inverse index.  Populated alongside
  /// the forward indices in `build_from_json`.  Keyed by the dialect
  /// string so the output rewrite can pick the alias matching the
  /// active `model_options.naming_dialect`.
  gtopt::flat_map<std::pair<std::string, std::string>, std::string>
      m_canonical_dialect_to_alias_;
  std::optional<std::filesystem::path> m_source_path_;
};

/// Resolve the override file the registry singleton would load from:
/// `$GTOPT_NAMING_DIALECTS_FILE` when set and existing, else `nullopt`
/// (the compiled-in dictionary is used).  No other path is probed.
[[nodiscard]] std::optional<std::filesystem::path> find_names_file();

/// Build a fresh registry with the same resolution as `instance()`
/// (override file, else compiled-in dictionary), without caching.
/// Exposed for tests of the resolution order.
[[nodiscard]] NamesRegistry load_names_registry();

}  // namespace gtopt
