/**
 * @file      lng_terminal_lp.cpp
 * @brief     Implementation of LngTerminalLP class
 * @date      Sun Apr 13 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module implements the LngTerminalLP class which provides the linear
 * programming representation of LNG storage terminals, including their
 * volume balance constraints and generator fuel consumption coupling.
 */

#include <cmath>

#include <gtopt/commitment_lp.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/integer_variable.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lng_terminal_lp.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/storage_state_defaults.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

LngTerminalLP::LngTerminalLP(const LngTerminal& pterminal,
                             const InputContext& ic)
    : StorageBase(pterminal, ic, Element::class_name)
    , delivery(ic, Element::class_name, id(), std::move(terminal().delivery))
    , delivery_cost(
          ic, Element::class_name, id(), std::move(terminal().delivery_cost))
    , scost(ic, Element::class_name, id(), std::move(terminal().scost))
    , spot_cargo_max(
          ic, Element::class_name, id(), std::move(terminal().spot_cargo_max))
    , spot_cost(ic, Element::class_name, id(), std::move(terminal().spot_cost))
{
}

auto LngTerminalLP::spot_year_of(const StageLP& stage) noexcept -> int
{
  const double mid = stage.timeinit() + (0.5 * stage.duration());
  return static_cast<int>(std::floor(mid / hours_per_year));
}

/**
 * @brief Per-block send-out limit rows
 *
 *   sendout_min ≤ Σ_g heat_rate_g · P_g[b] ≤ sendout_max   [m³/h]
 *
 * Added only for the limits that are set (an unset bound is open), so a
 * terminal without `sendout_max` / `sendout_min` keeps the LP unchanged.
 * A block where no linked generator has a column draws nothing: the row
 * would be empty, so it is skipped (a positive `sendout_min` there cannot
 * be met and is reported once).
 */
void LngTerminalLP::add_sendout_rows(
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp,
    const BIndexHolder<std::vector<std::pair<ColIndex, double>>>& draw)
{
  const auto& smax = terminal().sendout_max;
  const auto smin = terminal().sendout_min.value_or(0.0);
  if (!enforce_sendout() || (!smax.has_value() && smin <= 0.0)) {
    return;
  }

  BIndexHolder<RowIndex> rows;
  map_reserve(rows, stage.blocks().size());
  bool warned = false;
  for (auto&& block : stage.blocks()) {
    const auto buid = block.uid();
    const auto it = draw.find(buid);
    if (it == draw.end() || it->second.empty()) {
      if (smin > 0.0 && !warned) {
        SPDLOG_WARN(
            "LngTerminal '{}': sendout_min {} m3/h in stage {} but no "
            "linked generator can run in some blocks; row skipped",
            terminal().name,
            smin,
            stage.uid());
        warned = true;
      }
      continue;
    }
    SparseRow row {
        .class_name = Element::class_name.full_name(),
        .constraint_name = SendoutName,
        .variable_uid = uid(),
        .context = make_block_context(scenario.uid(), stage.uid(), buid),
    };
    for (const auto& [col, hr] : it->second) {
      row[col] += hr;
    }
    rows[buid] = lp.add_row(
        std::move(row).bound(smin, smax.value_or(LinearProblem::DblMax)));
  }
  if (!rows.empty()) {
    m_sendout_rows_[{scenario.uid(), stage.uid()}] = std::move(rows);
  }
}

/**
 * @brief Gas backing of the linked generators' up reserve
 *
 * With ``reserve_sustain_hours = T`` set, per block:
 *
 *   V_b − fcr·T·Σ_g hr_g·r_up_g ≥ emin_b              (T > 0)
 *   Σ_g hr_g·(P_g + r_up_g) ≤ sendout_max   (sendout_max set, enforced)
 *
 * ``V_b`` is the end-of-block level, which already nets the block's
 * deliveries and burn: an empty tank offers no up reserve, and the
 * regasification capacity caps generation plus up reserve.  Down reserve
 * needs the generation it reduces, ``r_dn ≤ P − Pmin`` (generic
 * provision row), so it is gas-limited through ``P``.
 */
void LngTerminalLP::add_reserve_rows(
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp,
    const BIndexHolder<std::vector<std::pair<ColIndex, double>>>& draw,
    const BIndexHolder<std::vector<std::pair<ColIndex, double>>>& reserve)
{
  const auto sustain_h =
      std::max(0.0, terminal().reserve_sustain_hours.value_or(0.0));
  const auto& smax = terminal().sendout_max;
  const bool headroom = enforce_sendout() && smax.has_value();
  const auto fcr = flow_conversion_rate();
  const auto cname = Element::class_name.full_name();
  auto&& energy_cols = energy_cols_at(scenario, stage);

  BIndexHolder<RowIndex> erows;
  BIndexHolder<RowIndex> srows;
  for (auto&& block : stage.blocks()) {
    const auto buid = block.uid();
    const auto rit = reserve.find(buid);
    if (rit == reserve.end() || rit->second.empty()) {
      continue;
    }
    const auto ctx = make_block_context(scenario.uid(), stage.uid(), buid);
    if (const auto eit = energy_cols.find(buid);
        sustain_h > 0.0 && eit != energy_cols.end())
    {
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveUpEnergyName,
              .variable_uid = uid(),
              .context = ctx,
          }
              .greater_equal(lp.get_col_lowb(eit->second));
      row[eit->second] = 1.0;
      for (const auto& [col, hr] : rit->second) {
        row[col] -= fcr * sustain_h * hr;
      }
      erows[buid] = lp.add_row(std::move(row));
    }
    if (headroom) {
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveUpSendoutName,
              .variable_uid = uid(),
              .context = ctx,
          }
              .less_equal(*smax);
      if (const auto dit = draw.find(buid); dit != draw.end()) {
        for (const auto& [col, hr] : dit->second) {
          row[col] += hr;
        }
      }
      for (const auto& [col, hr] : rit->second) {
        row[col] += hr;
      }
      srows[buid] = lp.add_row(std::move(row));
    }
  }
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  if (!erows.empty()) {
    m_reserve_energy_rows_[st_key] = std::move(erows);
  }
  if (!srows.empty()) {
    m_reserve_sendout_rows_[st_key] = std::move(srows);
  }
}

/**
 * @brief Optional spot cargoes
 *
 * One column per (scenario, stage):
 *
 *   0 ≤ n[t] ≤ spot_cargo_max[t]        (integer when `spot_integer`)
 *
 * with objective `prob · disc · spot_cost[t] · spot_cargo_volume · n[t]`
 * (a per-cargo cost: no block duration), entering the tank balance of
 * the stage's FIRST block with coefficient `−fcr · spot_cargo_volume`
 * (an inflow, the same unit as `delivery`).  The tank's `emax` bound on
 * that block's level then guarantees the room at arrival.
 *
 * `spot_annual_max` adds, per planning year y (8760-h windows of the
 * horizon, a stage in the window of its midpoint) and LP (phase):
 *
 *   Σ_{t ∈ y} n[t] ≤ spot_annual_max
 *
 * The row is created by the first stage of the year that has a spot
 * column and later stages stamp their column into it.
 */
void LngTerminalLP::add_spot_cargoes(SystemContext& sc,
                                     const ScenarioLP& scenario,
                                     const StageLP& stage,
                                     LinearProblem& lp)
{
  const auto volume = terminal().spot_cargo_volume.value_or(0.0);
  const auto slots = spot_cargo_max.at(stage.uid()).value_or(0.0);
  if (volume <= 0.0 || slots <= 0.0) {
    return;
  }

  const auto cname = Element::class_name.full_name();
  const auto stg_ctx = make_stage_context(scenario.uid(), stage.uid());
  const auto cost = spot_cost.at(stage.uid()).value_or(0.0);

  SparseCol col {
      .lowb = 0.0,
      .uppb = slots,
      // $/cargo × prob × discount: a stock-like purchase, no duration.
      .cost = cost * volume
          * CostHelper::cost_factor(scenario.probability_factor(),
                                    stage.discount_factor()),
      .cost_scale_type = ConstraintScaleType::Energy,
      .class_name = cname,
      .variable_name = SpotName,
      .variable_uid = uid(),
      .context = stg_ctx,
  };

  ColIndex spot_col {};
  if (terminal().spot_integer.value_or(false)) {
    const auto is_integer =
        !sc.simulation().phases()[stage.phase_index()].is_continuous();
    spot_col = sc.add_integer_col(
        lp,
        IntegerVariable::key(scenario,
                             stage,
                             Element::class_name,
                             uid(),
                             SpotName,
                             IntegerScope::Phase),
        std::move(col),
        is_integer ? IntegerDomain::Integer : IntegerDomain::Relaxed,
        IntegerScope::Phase,
        unknown_group,
        std::span<const BlockUid> {});
  } else {
    spot_col = lp.add_col(std::move(col));
  }

  // Arrival: the first block of the stage (inflow → negative coefficient
  // in `E[b] − (1−loss)·E[b−1] + out − in = 0`).
  const auto& erows = energy_rows_at(scenario, stage);
  // A stage-total lump: on a periodic stage (one-cycle balance) it
  // enters with the balance time scale (1 when periodic_storage is off).
  lp.row_at(erows.at(stage.blocks().front().uid()))[spot_col] =
      -flow_conversion_rate() * volume * balance_time_scale_at(scenario, stage);

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  m_spot_cols_[st_key] = spot_col;
  sc.add_ampl_variable(Element::class_name.snake_case(),
                       uid(),
                       SpotName,
                       scenario,
                       stage,
                       spot_col);

  const auto& annual = terminal().spot_annual_max;
  if (!annual.has_value()) {
    return;
  }
  const auto key =
      std::tuple {scenario.uid(), stage.phase_index(), spot_year_of(stage)};
  const auto it = m_spot_year_rows_.find(key);
  if (it != m_spot_year_rows_.end() && it->second.first != stage.uid()) {
    lp.row_at(it->second.second)[spot_col] = 1.0;
    return;
  }
  SparseRow row {
      // $/cargo scarcity value of a slot: Energy (prob · disc) basis.
      .cost_scale_type = ConstraintScaleType::Energy,
      .class_name = cname,
      .constraint_name = SpotAnnualName,
      .variable_uid = uid(),
      .context = stg_ctx,
  };
  row[spot_col] = 1.0;
  const auto ridx = lp.add_row(std::move(row).less_equal(*annual));
  m_spot_year_rows_[key] = {stage.uid(), ridx};
  m_spot_annual_rows_[st_key] = ridx;
}

/**
 * @brief Adds LNG terminal constraints to the linear problem
 *
 * Adds constraints for:
 * - Tank volume balance (via StorageLP)
 * - Regasification (send-out) flow variables
 * - Delivery inflow schedule
 * - Generator fuel consumption coupling via heat rates
 * - Boil-off gas losses (via StorageLP annual_standing_loss)
 * - Venting/spill with penalty cost
 */
bool LngTerminalLP::add_to_lp(SystemContext& sc,
                              const ScenarioLP& scenario,
                              const StageLP& stage,
                              LinearProblem& lp)
{
  static constexpr const auto& cname = Element::class_name;
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }

  auto&& blocks = stage.blocks();

  const auto fcr = flow_conversion_rate();

  // Resolve energy_scale from VariableScaleMap (default 1.0 if not set).
  const double energy_scale = sc.options().variable_scale_map().lookup(
      Element::class_name.full_name(), StorageBase::EnergyName, uid());

  // ── Delivery inflow columns ───────────────────────────────────────────
  // Delivery is total m³ per stage. Convert to a constant rate [m³/h]
  // across all blocks by dividing by stage duration.
  const auto stage_delivery = delivery.at(stage.uid()).value_or(0.0);
  const auto stage_duration = stage.duration();

  BIndexHolder<ColIndex> del_cols;
  map_reserve(del_cols, blocks.size());

  if (stage_delivery > 0.0 && stage_duration > 0.0) {
    const auto delivery_rate = stage_delivery / stage_duration;
    // Contract purchase price [$/m³] on the delivered volume rate × dt:
    // a constant of the objective for these fixed columns.
    const auto stage_delivery_cost =
        delivery_cost.at(stage.uid()).value_or(0.0);

    for (auto&& block : blocks) {
      const auto buid = block.uid();
      // Fixed-rate delivery: bounds = [rate, rate] (equality)
      const auto dc = lp.add_col(SparseCol {
          .lowb = delivery_rate,
          .uppb = delivery_rate,
          .cost = CostHelper::block_ecost(
              scenario, stage, block, stage_delivery_cost),
          .class_name = Element::class_name.full_name(),
          .variable_name = DeliveryName,
          .variable_uid = uid(),
          .context =
              make_block_context(scenario.uid(), stage.uid(), block.uid()),
      });
      del_cols[buid] = dc;
    }
  }

  // ── Call StorageBase::add_to_lp ───────────────────────────────────────
  // - finp = delivery columns (inflow to tank)
  // - fout = empty (generator consumption is injected directly into
  //          energy_rows after this call, matching PLP's model where
  //          the only physical outflow is generator fuel consumption)
  // - drain = venting (with spillway_cost/capacity)
  // - annual_standing_loss = BOG
  const BIndexHolder<ColIndex> empty_fout;

  const auto mpf = terminal().mean_production_factor.value_or(
      LngTerminal::default_mean_production_factor);
  // `scost` is per-(stage, block) since PR-D; sample the first block
  // for the stage-scoped StateVariable penalty (same convention as
  // `ReservoirLP::add_to_lp`).
  const auto& first_block = stage.blocks().front();
  const auto stage_scost = sc.state_violation_cost(stage, first_block, scost);
  const double lng_scost = stage_scost.value_or(1.0) * mpf;

  const StorageOptions opts {
      .use_state_variable = terminal().use_state_variable.value_or(
          storage_state_defaults::lng_terminal.use_state_variable),
      .class_name = Element::class_name.full_name(),
      .variable_uid = uid(),
      .energy_scale = energy_scale,
      .scost = lng_scost,
  };

  if (!StorageBase::add_to_lp(
          cname,
          ampl_name,
          sc,
          scenario,
          stage,
          lp,
          fcr,
          del_cols,
          [](BlockUid) { return 1.0; },
          empty_fout,
          [](BlockUid) { return 1.0; },
          LinearProblem::DblMax,
          std::nullopt,
          spillway_cost(),
          spillway_capacity(),
          opts))
  {
    SPDLOG_CRITICAL("Failed to add storage constraints for LNG terminal {}",
                    uid());
    return false;
  }

  // ── Generator fuel consumption coupling ───────────────────────────────
  // For each linked generator, inject its fuel consumption into the storage
  // energy balance rows. The energy balance row has the form:
  //   V[t] = V[t-1]×(1-loss) - fcr×fout×dt + fcr×finp×dt - fcr×drain×dt
  // We add:  + fcr × heat_rate × generation × dt
  // (positive = outflow from tank, same sign as fout)
  auto&& energy_rows_map = energy_rows_at(scenario, stage);
  // one-cycle balance on periodic stages (see balance_time_scale_at)
  const double lng_ts = balance_time_scale_at(scenario, stage);
  // PAMPL `lng_terminal.fuel = Σ_g fcr · heat_rate_g · generation_g`
  // [m³/h] — the regasified send-out drawn by the linked generators.
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> fuel_terms;
  // Send-out draw `Σ_g heat_rate_g · P_g` [m³/h] per block.
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> draw_terms;
  // Up-reserve draw `Σ_g heat_rate_g · r_up_g` per block (only with
  // `reserve_sustain_hours`; empty otherwise, so the LP is unchanged).
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> reserve_terms;
  const bool reserve_coupling = terminal().reserve_sustain_hours.has_value();
  const bool use_curve = terminal().use_generator_fuel_curve.value_or(false);
  const double gas_usage =
      reserve_coupling ? terminal().reserve_gas_usage.value_or(0.0) : 0.0;
  const auto provisions_of = [&](const GeneratorLP& gen)
  {
    std::vector<const ReserveProvisionLP*> out;
    if (!reserve_coupling) {
      return out;
    }
    for (const auto& rp : sc.elements<ReserveProvisionLP>()) {
      if (&sc.element<GeneratorLP>(rp.generator_sid()) == &gen) {
        out.push_back(&rp);
      }
    }
    return out;
  };

  for (const auto& link : terminal().generators) {
    const auto gen_sid = GeneratorLPSId {link.generator};
    const auto& gen = sc.element<GeneratorLP>(gen_sid);

    if (!gen.is_active(stage)) {
      continue;
    }

    // LP-size: a zero heat rate means this generator draws no fuel from
    // the tank, so ``fcr · heat_rate · generation`` is identically 0 in
    // every block.  Skip the link entirely rather than stamping explicit
    // zero coefficients into the energy-balance rows.  Write-out rule:
    // none — the energy-balance row is unchanged (a zero term is the
    // same as no term).
    if (link.heat_rate == 0.0) {
      continue;
    }

    // Tolerant lookup: the generator may have P1-elided some blocks
    // (zero pmax); a missing block contributes no fuel draw.
    auto&& gen_cols = gen.lookup_generation_cols(scenario, stage);
    const auto provisions = provisions_of(gen);
    // Own fuel curve of the generator (`use_generator_fuel_curve`):
    // segments, scalar Generator.heat_rate, or committed no-load fuel.
    const auto* noload_cmt =
        use_curve ? find_noload_fuel_commitment(sc, gen) : nullptr;
    const bool gen_curve = use_curve
        && (gen.has_heat_rate_segments() || noload_cmt != nullptr
            || gen.generator().heat_rate.has_value());

    for (auto&& block : blocks) {
      const auto buid = block.uid();
      const auto gen_col_it = gen_cols.find(buid);
      if (gen_col_it == gen_cols.end()) {
        continue;
      }
      const auto gen_col = gen_col_it->second;
      const auto erow_idx = energy_rows_map.at(buid);

      auto& erow = lp.row_at(erow_idx);
      // fuel consumed = heat_rate × generation [MW] × duration [h]
      // × flow_conversion_rate [m³/(m³/h·h)]
      // Since fcr=1.0 by default, this is heat_rate × generation × duration
      // The energy balance expects outflow as positive coefficient.
      // Up-reserve draw rate: the link heat rate, or (own curve) the top
      // segment's slope — the most a deployed MW can burn.
      double reserve_hr = link.heat_rate;
      if (gen_curve) {
        // link.heat_rate = m³ LNG per fuel unit of the generator curve
        for (const auto& [col, coef] :
             generator_fuel_burn_legs(gen, noload_cmt, scenario, stage, buid))
        {
          const double hr = link.heat_rate * coef;
          erow[col] += fcr * hr * block.duration() * lng_ts;
          fuel_terms[buid].emplace_back(col, fcr * hr);
          draw_terms[buid].emplace_back(col, hr);
        }
        const auto& segs = gen.generator().heat_rate_segments;
        reserve_hr = link.heat_rate
            * (gen.has_heat_rate_segments()
                   ? segs.back()
                   : gen.param_heat_rate(stage.uid(), buid).value_or(0.0));
      } else {
        erow[gen_col] = fcr * link.heat_rate * block.duration() * lng_ts;
        fuel_terms[buid].emplace_back(gen_col, fcr * link.heat_rate);
        draw_terms[buid].emplace_back(gen_col, link.heat_rate);
      }
      for (const auto* rp : provisions) {
        const auto rcol = rp->lookup_up_provision_col(scenario, stage, buid);
        if (!rcol) {
          continue;
        }
        reserve_terms[buid].emplace_back(*rcol, reserve_hr);
        if (gas_usage > 0.0) {
          // expected deployment burnt from the tank (outflow > 0)
          erow[*rcol] +=
              fcr * gas_usage * reserve_hr * block.duration() * lng_ts;
        }
      }
    }
  }

  add_sendout_rows(scenario, stage, lp, draw_terms);
  if (reserve_coupling) {
    add_reserve_rows(scenario, stage, lp, draw_terms, reserve_terms);
  }
  add_spot_cargoes(sc, scenario, stage, lp);

  // ── Store column indices ──────────────────────────────────────────────
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  delivery_cols[st_key] = std::move(del_cols);

  // Register element-specific PAMPL columns.
  sc.add_ampl_variable(ampl_name,
                       uid(),
                       DeliveryName,
                       scenario,
                       stage,
                       delivery_cols.at(st_key));
  // `volume` — the tank level [m³] under its physical name; same
  // columns as the storage `energy` attribute (cf. `reservoir.volume`).
  sc.add_ampl_variable(ampl_name,
                       uid(),
                       VolumeName,
                       scenario,
                       stage,
                       energy_cols_at(scenario, stage));
  sc.add_ampl_variable(ampl_name, uid(), FuelName, scenario, stage, fuel_terms);

  return true;
}

/**
 * @brief Adds LNG terminal solution variables to the output context
 */
bool LngTerminalLP::add_to_output(OutputContext& out) const
{
  static constexpr const auto& cname = Element::class_name;

  out.add_col_sol(cname, DeliveryName, id(), delivery_cols);
  out.add_col_cost(cname, DeliveryName, id(), delivery_cols);
  out.add_col_sol(cname, SpotName, id(), m_spot_cols_);
  out.add_col_cost(cname, SpotName, id(), m_spot_cols_);
  out.add_row_dual(cname, SpotAnnualName, id(), m_spot_annual_rows_);
  out.add_row_dual(cname, SendoutName, id(), m_sendout_rows_);
  out.add_row_dual(cname, ReserveUpEnergyName, id(), m_reserve_energy_rows_);
  out.add_row_dual(cname, ReserveUpSendoutName, id(), m_reserve_sendout_rows_);

  return StorageBase::add_to_output(out, Element::class_name.full_name());
}

}  // namespace gtopt
