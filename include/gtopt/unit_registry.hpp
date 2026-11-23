/**
 * @file      unit_registry.hpp
 * @brief     Runtime-loadable dictionary of (class, canonical, dialect) → unit
 * @date      2026-05-21
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Companion to `names_registry.hpp`.  Loads
 * `share/gtopt/unit_dialects.json` (or an embedded fallback) and
 * exposes a `(class, canonical, dialect) → unit` accessor.  The unit
 * strings are free-form physical-unit literals like "MW", "USD/MWh",
 * "m3/s", "Mm3" — they are *not* parsed into a unit algebra; downstream
 * code uses them only for diagnostics (`--list-dialects --units`),
 * mismatch warnings under `--naming-dialect`, and (eventually)
 * auto-conversion by a separate unit engine.
 *
 * Resolution order mirrors `find_names_file()`:
 *
 *   1. `$GTOPT_UNIT_DIALECTS_FILE` — explicit override file (logged at
 *      info level; warns when it has fewer entries than the compiled-in
 *      dictionary)
 *   2. Compiled-in dictionary (`share/gtopt/unit_dialects.json` embedded
 *      at configure time — always matches the binary)
 *   3. Empty registry (graceful degradation; every lookup returns
 *      nullopt and no unit-aware behaviour fires)
 *
 * Install / user share dirs are never probed, so a stale installed
 * copy cannot shadow the running binary's dictionary.
 *
 * The registry is loaded once on first access (singleton); subsequent
 * lookups are O(log n) via `gtopt::flat_map`.
 */

#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include <gtopt/fmap.hpp>

namespace gtopt
{

class UnitRegistry
{
public:
  /// Process-wide singleton, lazily loaded on first call.  The map is
  /// `const` after construction, so reads are thread-safe.
  [[nodiscard]] static const UnitRegistry& instance();

  /// Construct from an explicit JSON string (tests + the `instance()`
  /// boot path).  Throws `std::runtime_error` on parse error.
  explicit UnitRegistry(std::string_view json_content);

  /// Construct from a JSON file.  Throws on read or parse error.
  explicit UnitRegistry(const std::filesystem::path& json_path);

  /// Look up the unit string for a `(class, canonical, dialect)` tuple.
  /// Returns nullopt when the entry is not registered — callers should
  /// fall through to canonical / no-unit handling, never assume.
  [[nodiscard]] std::optional<std::string_view> unit_for(
      std::string_view class_name,
      std::string_view canonical,
      std::string_view dialect) const noexcept;

  /// Convenience overload assuming the gtopt-native dialect.  Useful at
  /// the LP-construction layer where canonical names are already in
  /// scope and the dialect is implied.
  [[nodiscard]] std::optional<std::string_view> unit_for(
      std::string_view class_name, std::string_view canonical) const noexcept
  {
    return unit_for(class_name, canonical, "gtopt");
  }

  /// Class-agnostic lookup: returns the single unit string if every
  /// registered `(class, canonical, dialect)` entry for the
  /// `(canonical, dialect)` pair agrees on the same unit.  Returns
  /// nullopt when the canonical has no entries in the given dialect,
  /// or when distinct entries disagree on the unit.
  ///
  /// Used by `canonicalize_json_keys` where the alias rewrite is
  /// class-blind — the JSON tokeniser does not know which element type
  /// the key belongs to.  Ambiguity (multiple classes, different units)
  /// is treated as "skip the unit-mismatch warning" rather than a hard
  /// error so the rewrite path stays robust.
  [[nodiscard]] std::optional<std::string_view> class_agnostic_unit_for(
      std::string_view canonical, std::string_view dialect) const noexcept;

  /// Number of (class, canonical, dialect) entries the registry holds.
  [[nodiscard]] std::size_t size() const noexcept { return m_units_.size(); }

  /// Path the registry was loaded from, or nullopt if loaded from an
  /// explicit JSON string or the built-in fallback.
  [[nodiscard]] const std::optional<std::filesystem::path>& source_path()
      const noexcept
  {
    return m_source_path_;
  }

private:
  void build_from_json(std::string_view json_content);

  /// Key shape: `(class_name, canonical, dialect)`.  Vector-backed
  /// `flat_map` because the registry is small (~80–200 entries) and
  /// cache-locality wins over hash overhead.
  using Key = std::tuple<std::string, std::string, std::string>;
  gtopt::flat_map<Key, std::string> m_units_;
  std::optional<std::filesystem::path> m_source_path_;
};

/// Resolve the override file the registry singleton would load from:
/// `$GTOPT_UNIT_DIALECTS_FILE` when set and existing, else nullopt (the
/// compiled-in dictionary is used).  No other path is probed.
[[nodiscard]] std::optional<std::filesystem::path> find_units_file();

/// Build a fresh registry with the same resolution as `instance()`,
/// without caching.  Exposed for tests of the resolution order.
[[nodiscard]] UnitRegistry load_unit_registry();

}  // namespace gtopt
