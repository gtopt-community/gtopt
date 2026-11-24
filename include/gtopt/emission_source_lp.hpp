/**
 * @file      emission_source_lp.hpp
 * @brief     LP-active wrapper for the EmissionSource bridge entity
 * @date      2026-05-18
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Resolves the per-stage combustion and upstream emission rates at
 * construction and exposes them via `param_rate` / `param_upstream_rate`.
 * In `add_to_lp` stamps `α = weight · (1 − capture) · (rate + upstream)
 * · dur` on the generator-dispatch column of every block into the
 * zone's cap row and/or allowance-pool row, adds the per-tonne price
 * adder `price · α` to that column's cost, and adds a `capture · (rate +
 * upstream) · capture_cost` adder when CCS is configured for the matching
 * (generator, pollutant) pair.
 */

#pragma once

#include <string_view>
#include <vector>

#include <gtopt/emission_source.hpp>
#include <gtopt/index_holder.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/scenario_lp.hpp>
#include <gtopt/schedule.hpp>
#include <gtopt/stage_lp.hpp>

namespace gtopt
{

// Forward declarations
class InputContext;
class LinearProblem;
class OutputContext;
class SystemContext;

class EmissionSourceLP : public ObjectLP<EmissionSource>
{
public:
  using Base = ObjectLP<EmissionSource>;

  /// Output stream / PAMPL attribute names.
  static constexpr std::string_view EmissionsName {"emissions"};
  static constexpr std::string_view CombustionName {"combustion"};
  static constexpr std::string_view UpstreamName {"upstream"};
  static constexpr std::string_view CapturedName {"captured"};
  static constexpr std::string_view RateName {"rate"};
  static constexpr std::string_view UpstreamRateName {"upstream_rate"};
  static constexpr std::string_view FuelRateName {"fuel_rate"};

  explicit EmissionSourceLP(const EmissionSource& src, const InputContext& ic);

  [[nodiscard]] constexpr auto&& emission_source(this auto&& self) noexcept
  {
    return self.object();
  }

  /// Stamp `α = weight · (1 − capture) · (rate + upstream) · dur` on
  /// every generator-dispatch column of this (scenario, stage) into
  /// the zone's cap row and/or allowance-pool row, add the zone's
  /// per-tonne price adder to the column cost, and add a per-MWh CCS opex adder
  /// when `Generator.emission_captures[]` carries a row for this pollutant.
  /// `EmissionZoneLP::add_to_lp` must run BEFORE this — guaranteed by
  /// the `lp_element_types_t` visitor ordering (same dependency
  /// `InertiaProvisionLP` has on `InertiaZoneLP`).
  [[nodiscard]] bool add_to_lp(const SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  /// Emits per-source per-block emission breakdown streams.  Reads
  /// the dispatch primal via `OutputContext::primal(gen_col)` and
  /// multiplies by the per-(stage, block) factors cached in
  /// `add_to_lp` to produce:
  ///
  ///   `EmissionSource/emissions_sol.parquet`   — NET tons (post-capture)
  ///   `EmissionSource/combustion_sol.parquet`  — combustion-only post-capture
  ///   `EmissionSource/upstream_sol.parquet`    — WTT post-capture (only
  ///                                               emitted when upstream_rate
  ///                                               is non-zero at any
  ///                                               (s, t, b))
  ///   `EmissionSource/captured_sol.parquet`    — captured tons (only when
  ///                                               capture_rate > 0)
  ///   `EmissionSource/rate_sol.parquet`        — combustion rate [t/MWh],
  ///                                               replicated per block from
  ///                                               the per-stage schedule.
  ///   `EmissionSource/upstream_rate_sol.parquet`
  ///                                            — WTT rate [t/MWh] (only
  ///                                               emitted when upstream_rate
  ///                                               is non-zero).
  ///
  /// All tons values are physical tons per block, computed Path A from
  /// source schedules + post-solve generation primal; rate streams are
  /// the raw input coefficient broadcast to the (scenario, stage, block)
  /// grid for ease of post-processing in tools/notebooks.
  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  /// @name Parameter accessors (resolved schedules)
  /// @{
  [[nodiscard]] auto param_rate(StageUid s) const { return rate_.at(s); }
  [[nodiscard]] auto param_upstream_rate(StageUid s) const
  {
    return upstream_rate_.at(s);
  }
  [[nodiscard]] auto param_fuel_rate(StageUid s) const
  {
    return fuel_rate_.at(s);
  }
  [[nodiscard]] auto param_upstream_fuel_rate(StageUid s) const
  {
    return upstream_fuel_rate_.at(s);
  }

  /// One LP column's share of a block's emissions: tonnes per unit of
  /// the column's primal in that block (already × `dur`).  `combustion`
  /// and `upstream` are NET of capture; `captured` is the abated part.
  struct EmissionLeg
  {
    ColIndex col;
    double combustion;
    double upstream;
    double captured;
  };

  using BlockEmission = std::vector<EmissionLeg>;

private:
  OptTRealSched rate_;
  OptTRealSched upstream_rate_;
  OptTRealSched fuel_rate_;
  OptTRealSched upstream_fuel_rate_;

  // Per-(scenario, stage, block) emission legs, populated in add_to_lp
  // and consumed in add_to_output to reconstruct the per-source
  // emission streams.
  STBIndexHolder<BlockEmission> block_emissions_;
  STBIndexHolder<double> rate_values_;
  STBIndexHolder<double> upstream_rate_values_;
  STBIndexHolder<double> fuel_rate_values_;
};

using EmissionSourceLPSId = ObjectSingleId<EmissionSourceLP>;

static_assert(EmissionSourceLP::Element::class_name
                  == LPClassName {"EmissionSource"},
              "EmissionSource::class_name must remain \"EmissionSource\"");

}  // namespace gtopt
