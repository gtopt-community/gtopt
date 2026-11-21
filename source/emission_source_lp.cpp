/**
 * @file      emission_source_lp.cpp
 * @brief     Substitute-out wiring for `EmissionSource` — every per-MWh
 *            emission contribution is stamped directly onto the
 *            generator dispatch column.
 * @date      2026-05-18
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * For each (scenario, stage, block), the per-source contribution to
 * its zone is the scalar
 *
 *   α_{s,b}  =  weight_z,p · (1 − capture_s,p) · (rate_s + upstream_s) · dur_b
 *              [tCO₂-eq per MW of dispatch]
 *
 * which is wired in three places, each only when active:
 *
 *   * cap row (if `EmissionZone.cap` set):
 *         cap_row[gen_col] += α_{s,b}
 *   * carbon tax (if `EmissionZone.price` set OR
 *     `objective_mode = "emissions"`):
 *         gen_col.cost += block_ecost(price · α_rate)
 *     (where `α_rate = α / dur` is the dur-free $/MWh form; block_ecost
 *     adds the per-block duration · probability · discount factors.)
 *   * allowance-pool drawdown (if `EmissionZone.allowance_pool` set):
 *         pool.energy_row_{b}[gen_col] += α_{s,b}
 *   * CCS opex (always, when `Generator.emission_captures[]` carries
 *     a row for this pollutant with non-zero `cost`):
 *         gen_col.cost += block_ecost(capture · effective_rate · capture_cost)
 *
 * Mirrors the existing CCS pattern (see the `lp.col_at(gcol).cost += ...`
 * adder below).  When the zone is *pure reporting* (no cap, no price,
 * no pool, not emissions-objective), nothing is wired into the LP and
 * only the per-block factor cache is populated — the cache feeds the
 * per-source output streams in `add_to_output`.
 */

#include <algorithm>
#include <utility>
#include <vector>

#include <gtopt/allowance_pool_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/emission_lp.hpp>
#include <gtopt/emission_source_lp.hpp>
#include <gtopt/emission_zone_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{
/// Resolve an `OptTRealFieldSched` to a scalar.  Handles the scalar
/// and per-stage-vector cases; falls back to 0 for FileSched (which
/// requires the InputContext machinery — not threaded through here
/// yet for `Generator.emission_captures[].rate/cost`).
[[nodiscard]] double resolve_stage_scalar(const OptTRealFieldSched& field,
                                          std::size_t stage_index) noexcept
{
  if (!field.has_value()) {
    return 0.0;
  }
  const auto& val = *field;
  // Use get_if (non-throwing) so this noexcept helper can't trip
  // bad_variant_access — the alternatives are already discriminated.
  if (const auto* r = std::get_if<Real>(&val)) {
    return *r;
  }
  if (const auto* vec = std::get_if<std::vector<Real>>(&val)) {
    if (stage_index < vec->size()) {
      return (*vec)[stage_index];
    }
  }
  // FileSched (string) path requires InputContext + Schedule and is
  // not yet wired for inline emission_captures.  TODO.
  return 0.0;
}
}  // namespace

EmissionSourceLP::EmissionSourceLP(const EmissionSource& src,
                                   const InputContext& ic)
    : Base(src, ic, Element::class_name)
    , rate_(ic, Element::class_name, id(), std::move(object().rate))
    , upstream_rate_(
          ic, Element::class_name, id(), std::move(object().upstream_rate))
    , fuel_rate_(ic, Element::class_name, id(), std::move(object().fuel_rate))
    , upstream_fuel_rate_(
          ic, Element::class_name, id(), std::move(object().upstream_fuel_rate))
{
}

bool EmissionSourceLP::add_to_lp(const SystemContext& sc,
                                 const ScenarioLP& scenario,
                                 const StageLP& stage,
                                 LinearProblem& lp)
{
  if (!is_active(stage)) {
    return true;
  }

  const auto& src = emission_source();
  if (!src.generator.has_value()) {
    SPDLOG_WARN("EmissionSource uid={} has no generator FK — skipping", uid());
    return false;
  }

  // Combustion (TTW) + optional upstream (WTT) rates [t/MWh].  Both
  // are counted toward the zone's α; reporting can split them post-
  // solve when needed.
  const auto rate = param_rate(stage.uid()).value_or(0.0);
  const auto upstream = param_upstream_rate(stage.uid()).value_or(0.0);
  // Fuel-basis factors [t / fuel-unit], applied to the generator's fuel
  // burn legs (exact for per-block and piecewise heat rates).
  const auto fuel_rate = param_fuel_rate(stage.uid()).value_or(0.0);
  const auto upstream_fuel =
      param_upstream_fuel_rate(stage.uid()).value_or(0.0);
  if (rate + upstream == 0.0 && fuel_rate + upstream_fuel == 0.0) {
    return true;
  }

  auto&& zone = sc.element<EmissionZoneLP>(EmissionZoneLPSId {src.zone});
  if (!zone.is_active(stage)) {
    return true;
  }

  // Look up the GWP weight for THIS pollutant in the zone's
  // `emissions[]` table.  Sources whose pollutant isn't in the
  // zone's list contribute nothing (programming error or
  // out-of-scope pollutant — log and skip).  Weight defaults to 1.0
  // when the zone-factor row has no explicit `weight` (single-
  // pollutant zone, or CO₂-only basket).
  //
  // Pollutant references are matched name-or-uid (resolved against the
  // Emission registry): the zone may list `"co2"` while the source
  // carries `Uid{1}` (e.g. a row synthesized by a legacy fold).
  const auto& emissions = sc.elements<EmissionLP>();
  const auto as_emission = [](const EmissionLP& e) -> const Emission&
  { return e.emission(); };
  const auto& zone_emissions = zone.emission_zone().emissions;
  const auto& src_emission = src.emission;
  double weight = 0.0;
  bool emission_in_zone = false;
  for (const auto& zef : zone_emissions) {
    if (same_emission(zef.emission, src_emission, emissions, as_emission)) {
      weight = zef.weight.value_or(1.0);
      emission_in_zone = true;
      break;
    }
  }
  if (!emission_in_zone) {
    SPDLOG_WARN(
        "EmissionSource uid={} targets zone uid={} but the zone does not "
        "list pollutant — skipping",
        uid(),
        zone.uid());
    return true;
  }

  auto&& gen = sc.element<GeneratorLP>(GeneratorLPSId {*src.generator});
  if (!gen.is_active(stage)) {
    return true;
  }

  // ── CCS / abatement: per-(generator, pollutant) capture row ────────
  // If `Generator.emission_captures[]` contains a row for this
  // pollutant, scale the contribution by `(1 − capture_rate)` and add
  // a per-ton objective adder `capture_cost` on the captured tonnage.
  double capture_rate = 0.0;
  double capture_cost = 0.0;
  const auto stage_index = static_cast<std::size_t>(stage.index());
  for (const auto& cap : gen.generator().emission_captures) {
    if (same_emission(cap.emission, src.emission, emissions, as_emission)) {
      capture_rate = resolve_stage_scalar(cap.rate, stage_index);
      capture_cost = resolve_stage_scalar(cap.cost, stage_index);
      break;
    }
  }
  const double net_factor = 1.0 - capture_rate;

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};

  // Tolerant lookup: when every block of this (scenario, stage) was
  // skipped by the P1 zero-pmax optimization, ``generation_cols`` has
  // no outer key.  Use ``lookup_generation_cols`` (returns an empty
  // inner map) instead of the throwing ``generation_cols_at`` — the
  // per-block loop below then has nothing to do.  Symmetric to
  // ``WaterwayLP::flow_cols_at`` / ``lookup_flow_cols``.
  const auto& gen_cols = gen.lookup_generation_cols(scenario, stage);
  if (gen_cols.empty()) {
    return true;
  }

  // Wiring decisions for this (scenario, stage):
  //   * cap_row exists       ⇒ stamp `+α` per leg onto the leg column
  //   * effective_price > 0  ⇒ add `block_ecost(price · α_rate)` to the
  //                            leg column cost
  //   * pool_fk set          ⇒ stamp `+α` per leg onto pool.energy_row
  const auto& cap_rows_map = zone.cap_rows();
  const auto cap_it = cap_rows_map.find(st_key);
  const RowIndex* cap_row_idx =
      (cap_it != cap_rows_map.end()) ? &cap_it->second : nullptr;

  const bool emissions_mode = sc.options().is_emissions_objective();
  const auto stage_price = zone.param_price(stage.uid());
  double effective_price = 0.0;
  if (stage_price) {
    effective_price = *stage_price;
  } else if (emissions_mode) {
    effective_price = 1.0;
  }
  const bool has_tax = (effective_price != 0.0);

  const auto pool_fk = zone.emission_zone().allowance_pool;
  const STBIndexHolder<RowIndex>::mapped_type* pool_energy_rows = nullptr;
  if (pool_fk.has_value()) {
    auto&& pool = sc.element<AllowancePoolLP>(AllowancePoolLPSId {*pool_fk});
    pool_energy_rows = &pool.energy_rows_at(scenario, stage);
  }

  const bool fuel_basis = (fuel_rate + upstream_fuel) != 0.0;
  // No-load fuel (`Commitment.noload_fuel · u`) joins the fuel-basis
  // legs; CommitmentLP is built before EmissionSourceLP.
  const CommitmentLP* noload_cmt =
      fuel_basis ? find_noload_fuel_commitment(sc, gen) : nullptr;

  // Per-block output-reconstruction caches (populated below; consumed
  // by add_to_output).  Populated regardless of whether any LP wiring
  // fires, so reporting-only zones still emit the full streams.
  BIndexHolder<BlockEmission> blk_emissions;
  BIndexHolder<double> blk_rate_value;
  BIndexHolder<double> blk_upstream_rate_value;
  BIndexHolder<double> blk_fuel_rate_value;
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> blk_weighted_emit;

  for (const auto& block : stage.blocks()) {
    const auto buid = block.uid();
    const auto gcol_it = gen_cols.find(buid);
    if (gcol_it == gen_cols.end()) {
      continue;
    }
    const auto dur = block.duration();
    const auto gcol = gcol_it->second;

    // Per-MW-of-column rates [t / MWh-of-column]: generation basis on
    // the dispatch column, fuel basis on every fuel-burn leg (the
    // dispatch column itself and, for piecewise heat rates, the
    // heat-rate slack columns).  Legs on the same column are merged.
    BlockEmission rates;
    const auto add_rate = [&](ColIndex col, double comb, double upstr)
    {
      if (comb == 0.0 && upstr == 0.0) {
        return;
      }
      const auto it = std::ranges::find(rates, col, &EmissionLeg::col);
      if (it != rates.end()) {
        it->combustion += comb;
        it->upstream += upstr;
      } else {
        rates.push_back({
            .col = col,
            .combustion = comb,
            .upstream = upstr,
            .captured = 0.0,
        });
      }
    };
    add_rate(gcol, rate, upstream);
    if (fuel_basis) {
      for (const auto& [col, burn] :
           generator_fuel_burn_legs(gen, noload_cmt, scenario, stage, buid))
      {
        add_rate(col, fuel_rate * burn, upstream_fuel * burn);
      }
    }
    if (rates.empty()) {
      continue;
    }

    BlockEmission blk;
    blk.reserve(rates.size());
    auto& weighted = blk_weighted_emit[buid];
    for (const auto& [col, comb, upstr, unused] : rates) {
      const double leg_rate = comb + upstr;  // gross t / MWh-of-column
      // α_rate [tCO₂-eq / MWh-of-column], dur-free; α = α_rate · dur.
      const double alpha_rate = weight * net_factor * leg_rate;
      const double alpha_block = alpha_rate * dur;

      // ── Cap row coefficient (only when zone has a cap row at (s,t))
      // Multiple sources may target the same (zone, column) pair (e.g.
      // two pollutants in a basket): read-modify-write to accumulate.
      if (cap_row_idx != nullptr) {
        const auto existing = lp.get_coeff(*cap_row_idx, col);
        lp.set_coeff(*cap_row_idx, col, existing + alpha_block);
      }

      // ── Carbon tax (price or emissions_mode) ──────────────────────
      if (has_tax) {
        lp.col_at(col).cost += CostHelper::block_ecost(
            scenario, stage, block, effective_price * alpha_rate);
      }

      // ── Allowance-pool drawdown ───────────────────────────────────
      if (pool_energy_rows != nullptr) {
        const auto it = pool_energy_rows->find(buid);
        if (it != pool_energy_rows->end()) {
          const auto existing = lp.get_coeff(it->second, col);
          lp.set_coeff(it->second, col, existing + alpha_block);
        }
      }

      // ── CCS opex: capture_cost per captured ton ───────────────────
      if (capture_rate > 0.0 && capture_cost > 0.0) {
        lp.col_at(col).cost += CostHelper::block_ecost(
            scenario, stage, block, capture_rate * leg_rate * capture_cost);
      }

      // ── Reporting legs (net of capture, per block) ────────────────
      blk.push_back({
          .col = col,
          .combustion = net_factor * comb * dur,
          .upstream = net_factor * upstr * dur,
          .captured = capture_rate * leg_rate * dur,
      });
      weighted.emplace_back(col, net_factor * leg_rate * dur);
    }

    blk_emissions[buid] = std::move(blk);
    blk_rate_value[buid] = rate;
    if (upstream != 0.0) {
      blk_upstream_rate_value[buid] = upstream;
    }
    if (fuel_basis) {
      blk_fuel_rate_value[buid] = fuel_rate;
    }
  }

  if (!blk_emissions.empty()) {
    // ── PAMPL `emission_source("X").emissions` registration ────────
    // Expose the per-block NET (post-capture) emission expression
    // `Σ_legs coef_b · col_b` to user constraints via the AMPL
    // `block_cols_weighted_sum` registry.  UCs can express "emissions
    // of source X over the stage" as
    // `sum(b in blocks) emission_source("X").emissions`.
    static constexpr auto ampl_name = Element::class_name.snake_case();
    sc.add_ampl_variable(
        ampl_name, uid(), "emissions", scenario, stage, blk_weighted_emit);

    block_emissions_[st_key] = std::move(blk_emissions);
    rate_values_[st_key] = std::move(blk_rate_value);
    if (!blk_upstream_rate_value.empty()) {
      upstream_rate_values_[st_key] = std::move(blk_upstream_rate_value);
    }
    if (!blk_fuel_rate_value.empty()) {
      fuel_rate_values_[st_key] = std::move(blk_fuel_rate_value);
    }
  }

  return true;
}

bool EmissionSourceLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  // Reconstruct each per-source stream from
  //   value_{s,t,b} = primal(gen_col_{s,t,b}) · factor_{s,t,b}
  // and emit via the value-emit OutputContext path (no LP-scale
  // unwrap — values are physical tons per block).
  STBIndexHolder<double> emissions_values;
  STBIndexHolder<double> combustion_values;
  STBIndexHolder<double> upstream_values;
  STBIndexHolder<double> captured_values;

  bool any_upstream = false;
  bool any_captured = false;

  for (const auto& [st_key, blocks] : block_emissions_) {
    auto& emissions_st = emissions_values[st_key];
    auto& combustion_st = combustion_values[st_key];
    auto& upstream_st = upstream_values[st_key];
    auto& captured_st = captured_values[st_key];

    for (const auto& [buid, blk] : blocks) {
      double comb = 0.0;
      double upstr = 0.0;
      double capt = 0.0;
      for (const auto& leg : blk) {
        const auto sol = out.primal(leg.col);
        comb += leg.combustion * sol;
        upstr += leg.upstream * sol;
        capt += leg.captured * sol;
        any_upstream = any_upstream || leg.upstream != 0.0;
        any_captured = any_captured || leg.captured != 0.0;
      }

      combustion_st[buid] = comb;
      upstream_st[buid] = upstr;
      captured_st[buid] = capt;
      emissions_st[buid] = comb + upstr;
    }
  }

  out.add_col_sol_values(cname, "emissions", pid, emissions_values);
  out.add_col_sol_values(cname, "combustion", pid, combustion_values);
  if (any_upstream) {
    out.add_col_sol_values(cname, "upstream", pid, upstream_values);
  }
  if (any_captured) {
    out.add_col_sol_values(cname, "captured", pid, captured_values);
  }

  // Per-MWh rate streams (raw input coefficient broadcast across blocks).
  if (!rate_values_.empty()) {
    out.add_col_sol_values(cname, "rate", pid, rate_values_);
  }
  if (!upstream_rate_values_.empty()) {
    out.add_col_sol_values(cname, "upstream_rate", pid, upstream_rate_values_);
  }
  if (!fuel_rate_values_.empty()) {
    out.add_col_sol_values(cname, "fuel_rate", pid, fuel_rate_values_);
  }

  return true;
}

}  // namespace gtopt
