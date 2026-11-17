/**
 * @file      emission.hpp
 * @brief     Pollutant TYPE entity (registry; no constraints)
 * @date      2026-05-18
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `Emission` is the lightweight registry of pollutant KINDS modeled in
 * a run — CO₂, SO₂, NOₓ, CH₄(-eq), HFC, SF₆, …  It carries the
 * pollutant's short name only; cap/price/cap_cost moved to
 * `EmissionZone` so the same pollutant can have different
 * regulatory treatments in different scopes (e.g. global CO₂ tax +
 * California-only cap).
 *
 * ### JSON
 *
 * ```json
 * {"uid": 1, "name": "co2"}
 * ```
 *
 * The `name` is the canonical pollutant identifier (lower-case
 * snake-case).  Used as the prefix in per-pollutant output parquet
 * filenames (e.g. `Generator/co2_emission_rate_sol.parquet`) and as
 * the FK target of `EmissionZone.emissions[].emission`,
 * `EmissionSource.emission`, and (planned) `Fuel.emission_factors[]`.
 *
 * Passive in the LP — no rows, no columns.  Constraint wiring lives
 * on `EmissionZone` (per-pollutant cap / price / slack).
 *
 * @see EmissionZone for the per-pollutant cap / price / slack
 * @see EmissionSource for the generator → zone bridge
 */

#pragma once

#include <functional>
#include <optional>
#include <ranges>
#include <variant>

#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>
#include <gtopt/single_id.hpp>

namespace gtopt
{

/**
 * @struct Emission
 * @brief Pollutant type tag.  Just `uid` + `name`.
 */
struct Emission
{
  /// Canonical class-name constant used in LP row labels and
  /// `Output/Emission/*.parquet` paths.
  static constexpr LPClassName class_name {"Emission"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Pollutant short name (`"co2"`, `"so2"`, `"nox"`, …)
  OptActive active {};  ///< Activation status
};

/// Resolve an `Emission` reference (uid or name) to its uid.
///
/// A `Uid` reference is returned as-is; a `Name` reference is looked
/// up in @p emissions (any range whose elements project — via
/// @p proj — to `const Emission&`).  Returns `nullopt` for an unknown
/// name.
template<std::ranges::input_range Range, typename Proj = std::identity>
[[nodiscard]] constexpr std::optional<Uid> resolve_emission_uid(
    const SingleId& ref, const Range& emissions, Proj proj = {})
{
  if (const auto* uid = std::get_if<Uid>(&ref)) {
    return *uid;
  }
  const auto& name = std::get<Name>(ref);
  for (const auto& item : emissions) {
    const Emission& em = std::invoke(proj, item);
    if (em.name == name) {
      return em.uid;
    }
  }
  return std::nullopt;
}

/// Name-or-uid equality of two `Emission` references.
///
/// `EmissionZone.emissions[].emission`, `EmissionSource.emission`,
/// `Fuel.emission_factors[].emission` and
/// `Generator.emission_captures[].emission` may each name the
/// pollutant by uid OR by name; a plain `SingleId` comparison would
/// treat `Uid{1}` and `"co2"` as different pollutants even when they
/// denote the same `Emission` row.  Both sides are resolved against
/// @p emissions; identical references compare equal even when they
/// cannot be resolved.
template<std::ranges::input_range Range, typename Proj = std::identity>
[[nodiscard]] constexpr bool same_emission(const SingleId& lhs,
                                           const SingleId& rhs,
                                           const Range& emissions,
                                           Proj proj = {})
{
  if (lhs == rhs) {
    return true;
  }
  const auto lhs_uid = resolve_emission_uid(lhs, emissions, proj);
  const auto rhs_uid = resolve_emission_uid(rhs, emissions, proj);
  return lhs_uid.has_value() && rhs_uid.has_value() && *lhs_uid == *rhs_uid;
}

}  // namespace gtopt
