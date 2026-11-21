/**
 * @file      converter_lp.cpp
 * @brief     Implementation of converter LP formulation
 * @date      Wed Apr  2 02:12:47 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module implements ConverterLP construction and add_to_lp, which
 * builds LP variables and constraints for energy conversion rate coupling.
 */

#include <algorithm>
#include <tuple>
#include <vector>

#include <gtopt/battery_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/converter_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>

namespace gtopt
{

ConverterLP::ConverterLP(const Converter& pconverter, InputContext& ic)
    : CapacityBase(pconverter, ic, Element::class_name)
    , conversion_rate(
          ic, Element::class_name, id(), std::move(converter().conversion_rate))
{
}

bool ConverterLP::add_to_lp(SystemContext& sc,
                            const ScenarioLP& scenario,
                            const StageLP& stage,
                            LinearProblem& lp)
{
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!CapacityBase::add_to_lp(sc, ampl_name, scenario, stage, lp)) [[unlikely]]
  {
    return false;
  }

  if (!is_active(stage)) [[unlikely]] {
    return true;
  }

  const auto [opt_capacity, capacity_col] = capacity_and_col(stage, lp);

  const auto stage_conversion_rate =
      conversion_rate.at(stage.uid()).value_or(1.0);

  auto&& blocks = stage.blocks();

  // Tolerant element lookups: for expansion batteries with pmax=0
  // (active only in future stages), the synthetic generator, demand,
  // or battery LP elements may be absent or have no columns for this
  // (scenario, stage).  Skip the converter entirely in that case —
  // there is nothing to couple.
  const GeneratorLP* gen_ptr = nullptr;
  const DemandLP* dem_ptr = nullptr;
  const BatteryLP* bat_ptr = nullptr;
  try {
    gen_ptr = &sc.element<GeneratorLP>(generator_sid());
    dem_ptr = &sc.element<DemandLP>(demand_sid());
    bat_ptr = &sc.element<BatteryLP>(battery_sid());
  } catch (const std::exception&) {
    return true;  // element not in LP — skip silently
  }

  auto&& gen_cols = gen_ptr->lookup_generation_cols(scenario, stage);
  // Tolerant lookup: a battery with pmax_charge = 0 in every block of the
  // stage has no load columns at all (P1 zero-lmax skip).
  auto&& demand_cols = dem_ptr->lookup_load_cols(scenario, stage);
  auto&& finp_cols = bat_ptr->finp_cols_at(scenario, stage);
  auto&& fout_cols = bat_ptr->fout_cols_at(scenario, stage);

  BIndexHolder<RowIndex> grows;
  BIndexHolder<RowIndex> drows;
  BIndexHolder<RowIndex> crows;
  map_reserve(grows, blocks.size());
  map_reserve(drows, blocks.size());
  map_reserve(crows, blocks.size());

  for (const auto& block : blocks) {
    const auto buid = block.uid();

    // LP-size / robustness: when the synthetic discharge generator or
    // charge demand elided this block (gold-standard zero-envelope P1
    // skip), that direction has no rating: pin its battery-side flow to
    // 0, couple only the surviving direction, and skip the capacity /
    // envelope rows.  Write-out rule: an absent coupling row has a 0
    // dual (it never bound), the natural zero.
    const auto gcol_it = gen_cols.find(buid);
    const auto dcol_it = demand_cols.find(buid);
    const auto fout_it = fout_cols.find(buid);
    const auto finp_it = finp_cols.find(buid);
    if (gcol_it == gen_cols.end() || dcol_it == demand_cols.end()
        || fout_it == fout_cols.end() || finp_it == finp_cols.end())
    {
      // Pre-fix this `continue` left BOTH sides uncoupled, so when the
      // charge demand was elided (``pmax_charge`` = 0 in a block) the
      // battery ``finp`` column stayed in [0, +inf) with no row (free
      // charging, energy from nowhere) AND the discharge generator was
      // free generation at ``discharge_cost``.  Now: pin the battery-side
      // flow whose grid partner is missing to 0, and still couple the side
      // that has both columns.
      if (gcol_it == gen_cols.end() && fout_it != fout_cols.end()) {
        lp.col_at(fout_it->second).uppb = 0.0;
      }
      if (dcol_it == demand_cols.end() && finp_it != finp_cols.end()) {
        lp.col_at(finp_it->second).uppb = 0.0;
      }
      const auto ctx =
          make_block_context(scenario.uid(), stage.uid(), block.uid());
      if (gcol_it != gen_cols.end() && fout_it != fout_cols.end()) {
        auto grow =
            SparseRow {
                .class_name = Element::class_name.full_name(),
                .constraint_name = GenerationName,
                .variable_uid = uid(),
                .context = ctx,
            }
                .equal(0);
        grow[fout_it->second] = -stage_conversion_rate;
        grow[gcol_it->second] = +1;
        grows[buid] = lp.add_row(std::move(grow));
      }
      if (dcol_it != demand_cols.end() && finp_it != finp_cols.end()) {
        auto drow =
            SparseRow {
                .class_name = Element::class_name.full_name(),
                .constraint_name = DemandName,
                .variable_uid = uid(),
                .context = ctx,
            }
                .equal(0);
        drow[finp_it->second] = -stage_conversion_rate;
        drow[dcol_it->second] = +1;
        drows[buid] = lp.add_row(std::move(drow));
      }
      continue;
    }

    const auto block_context =
        make_block_context(scenario.uid(), stage.uid(), block.uid());
    const auto gcol = gcol_it->second;
    {
      auto grow =
          SparseRow {
              .class_name = Element::class_name.full_name(),
              .constraint_name = GenerationName,
              .variable_uid = uid(),
              .context = block_context,
          }
              .equal(0);
      const auto ocol = fout_it->second;

      grow[ocol] = -stage_conversion_rate;
      grow[gcol] = +1;
      grows[buid] = lp.add_row(std::move(grow));
    }

    const auto dcol = dcol_it->second;
    {
      const auto icol = finp_it->second;
      auto drow =
          SparseRow {
              .class_name = Element::class_name.full_name(),
              .constraint_name = DemandName,
              .variable_uid = uid(),
              .context = block_context,
          }
              .equal(0);
      drow[icol] = -stage_conversion_rate;
      drow[dcol] = +1;
      drows[buid] = lp.add_row(std::move(drow));
    }

    // adding the capacity constraint
    if (capacity_col) {
      auto crow =
          SparseRow {
              .class_name = Element::class_name.full_name(),
              .constraint_name = CapacityName,
              .variable_uid = uid(),
              .context = block_context,
          }
              .greater_equal(0);

      crow[*capacity_col] = 1;
      crow[gcol] = -1;
      crow[dcol] = -1;

      crows[buid] = lp.add_row(std::move(crow));
    }

    // Battery power envelope (valid inequality, default ON):
    //
    //   p_charge / Pc_max + p_discharge / Pd_max ≤ 1
    //
    // Pc_max / Pd_max are the HARD per-block bounds of the synthetic
    // charge demand / discharge generator columns, so the row never cuts
    // a schedule a real inverter can run (it shares one power rating
    // between both directions).  It removes the LP vertex where the
    // battery charges and discharges simultaneously at full power — the
    // cheapest way for a lossy (η < 1) battery to "burn" surplus energy.
    // Partial simultaneous operation inside the envelope (e.g. 50 %/50 %)
    // is still LP-feasible; only binaries forbid it entirely (none of the
    // existing commitment paths do — ``u_commit`` gates both directions
    // with the SAME u, and the legacy ``u_charge``/``u_discharge`` carry
    // no exclusivity row — so the envelope is emitted for them too).
    //
    // Expansion: the converter's expandable ``capacity`` already adds
    // ``p_charge + p_discharge ≤ capacity`` (the linear valid inequality
    // for a variable rating); the envelope uses the column bounds, which
    // are hard limits independent of that capacity, so both stay valid.
    //
    // Skipped when either bound is ≤ 0 (one direction off — no
    // simultaneity possible), unbounded (no finite rating to normalise
    // by), or when the hard floors (pmin_charge / pmin_discharge) already
    // force a point OUTSIDE the envelope (explicit user schedule that the
    // row would make infeasible).  Row scaled by max(Pc, Pd) → coefficients ≥
    // 1, RHS in MW.
    if (sc.options().battery_power_envelope()) {
      const double pd_max = lp.get_col_uppb(gcol);
      const double pc_max = lp.get_col_uppb(dcol);
      // ≥ 1e20 MW = "no rating" sentinel (DblMax or solver infinity).
      static constexpr double unbounded_mw = 1e20;
      const bool finite = pd_max < unbounded_mw && pc_max < unbounded_mw;
      // A hard floor schedule outside the envelope (e.g. pinned
      // pmin_charge = pmin_discharge = full power) would turn the row
      // into an infeasibility — respect the user's explicit schedule.
      const bool forced_outside = finite && pd_max > 0.0 && pc_max > 0.0
          && ((lp.get_col_lowb(gcol) / pd_max)
                  + (lp.get_col_lowb(dcol) / pc_max)
              > 1.0 + 1e-9);
      if (pd_max > 0.0 && pc_max > 0.0 && finite && !forced_outside) {
        const double pref = std::max(pc_max, pd_max);
        auto erow =
            SparseRow {
                .class_name = Element::class_name.full_name(),
                .constraint_name = PowerEnvelopeName,
                .variable_uid = uid(),
                .context = block_context,
            }
                .less_equal(pref);
        erow[gcol] = pref / pd_max;
        erow[dcol] = pref / pc_max;
        power_envelope_rows[std::tuple {scenario.uid(), stage.uid()}][buid] =
            lp.add_row(std::move(erow));
      }
    }
  }

  // Native BESS reserve deliverability rows (opt-in via
  // ``Battery.reserve_sustain_hours``).  ReserveProvisionLP ran earlier
  // and skipped its generic generator rows for this battery.
  if (bat_ptr->battery().reserve_sustain_hours.has_value()) {
    add_native_reserve_rows(
        sc, scenario, stage, lp, *gen_ptr, *dem_ptr, *bat_ptr);
  }

  // Fixed storage duration: installed energy = ratio × installed power.
  // Couples the battery's energy-axis expansion (BatteryLP capainst) with
  // this converter's power-axis expansion, so a candidate is built as one
  // technology (e.g. a 4 h battery).  Needs both capacity columns; when
  // only one axis has a column (fixed energy or fixed power) the ratio is
  // a property of the inputs and nothing is added.
  if (const auto ratio = converter().energy_power_ratio; ratio && *ratio > 0) {
    const auto bat_cap_col = bat_ptr->capacity_col_at(stage);
    if (bat_cap_col && capacity_col) {
      auto row =
          SparseRow {
              .class_name = Element::class_name.full_name(),
              .constraint_name = DurationName,
              .variable_uid = uid(),
              .context = make_stage_context(scenario.uid(), stage.uid()),
          }
              .equal(0);
      row[*bat_cap_col] = 1.0;
      row[*capacity_col] = -*ratio;
      std::ignore = lp.add_row(std::move(row));
    }
  }

  // Conditional commitment: when ``Converter.commitment`` is set,
  // gate the synthetic charge ``Demand`` and discharge ``Generator``
  // bounds with per-block binaries.  Mirrors PLEXOS
  // ``Battery.Commitment Status``.
  //
  // "One true source for u_commit" — when the generator side has a
  // CommitmentLP (always true for batteries via
  // ``System::expand_batteries()`` synthesis), the discharge
  // gating ``gen ≤ pmax × u`` / ``gen ≥ Commitment.pmin × u`` is
  // already emitted by ``CommitmentLP::add_to_lp``.  ConverterLP
  // then reuses that same ``u_commit`` column to gate the
  // charge-side demand bounds — no duplicate ``u_discharge`` /
  // ``u_charge`` cols are introduced.
  //
  // Fallback (legacy, kept for non-synthesized cases): when no
  // CommitmentLP matches the converter's generator, fall back to
  // creating local ``u_charge`` / ``u_discharge`` binaries (integer
  // by convention — the original ``Converter.commitment`` API).
  if (converter().commitment.value_or(false)) {
    // Look up the CommitmentLP that points at this converter's
    // discharge generator.  The synthesized commitment (uid named
    // ``uc_<bat>_gen``) ``relax = true`` by default — fractional u
    // is fine for an LP-only run.
    const BIndexHolder<ColIndex>* shared_ucol = nullptr;
    for (const auto& cmt : sc.elements<CommitmentLP>()) {
      if (cmt.generator_sid() != generator_sid()) {
        continue;
      }
      shared_ucol = cmt.find_status_cols(scenario, stage);
      if (shared_ucol != nullptr) {
        break;
      }
    }

    for (const auto& block : blocks) {
      const auto buid = block.uid();
      const auto block_ctx =
          make_block_context(scenario.uid(), stage.uid(), buid);
      const auto gcol_it = gen_cols.find(buid);
      const auto dcol_it = demand_cols.find(buid);
      if (gcol_it == gen_cols.end() || dcol_it == demand_cols.end()) {
        continue;
      }
      const auto gcol = gcol_it->second;
      const auto dcol = dcol_it->second;
      const auto block_pmin = lp.get_col_lowb(gcol);
      const auto block_pmax = lp.get_col_uppb(gcol);
      const auto block_lmin = lp.get_col_lowb(dcol);
      const auto block_lmax = lp.get_col_uppb(dcol);

      // ── Shared-u_commit path ────────────────────────────────────
      if (shared_ucol != nullptr) {
        const auto uc_it = shared_ucol->find(buid);
        if (uc_it == shared_ucol->end()) {
          continue;
        }
        const auto ucol = uc_it->second;

        // Discharge side is already gated by CommitmentLP via
        // ``gen_upper`` / ``gen_lower`` rows; nothing to add here.
        // Charge side: mirror the discharge gating onto the
        // synthetic demand col using the same shared u_commit.
        if (block_lmax > 0.0) {
          lp.col_at(dcol).lowb = 0.0;
          {
            auto row =
                SparseRow {
                    .class_name = Element::class_name.full_name(),
                    .constraint_name = ChargeUpperName,
                    .variable_uid = uid(),
                    .context = block_ctx,
                }
                    .less_equal(0.0);
            row[dcol] = 1.0;
            row[ucol] = -block_lmax;
            static_cast<void>(lp.add_row(std::move(row)));
          }
          if (block_lmin > 0.0) {
            auto row =
                SparseRow {
                    .class_name = Element::class_name.full_name(),
                    .constraint_name = ChargeLowerName,
                    .variable_uid = uid(),
                    .context = block_ctx,
                }
                    .greater_equal(0.0);
            row[dcol] = 1.0;
            row[ucol] = -block_lmin;
            static_cast<void>(lp.add_row(std::move(row)));
          }
        }
        continue;
      }

      // ── Legacy fallback (no CommitmentLP) ──────────────────────
      // Discharge side: migrate ``Generator.pmin`` from a static col
      // floor to a u-gated row.  ``u_discharge`` exists only when the
      // discharge envelope is non-degenerate (pmax > 0).
      if (block_pmax > 0.0) {
        lp.col_at(gcol).lowb = 0.0;
        const std::array<BlockUid, 1> u_disc_blocks {buid};
        const auto u_disc = sc.add_integer_col(
            lp,
            IntegerVariable::key(scenario,
                                 stage,
                                 Element::class_name,
                                 uid(),
                                 UDischargeName,
                                 IntegerScope::Block,
                                 buid),
            SparseCol {
                .lowb = 0.0,
                .uppb = 1.0,
                .cost = 0.0,
                .class_name = Element::class_name.full_name(),
                .variable_name = UDischargeName,
                .variable_uid = uid(),
                .context = block_ctx,
            },
            IntegerDomain::Binary,
            IntegerScope::Block,
            buid,
            std::span<const BlockUid> {u_disc_blocks});
        {
          auto row =
              SparseRow {
                  .class_name = Element::class_name.full_name(),
                  .constraint_name = DischargeUpperName,
                  .variable_uid = uid(),
                  .context = block_ctx,
              }
                  .less_equal(0.0);
          row[gcol] = 1.0;
          row[u_disc] = -block_pmax;
          static_cast<void>(lp.add_row(std::move(row)));
        }
        if (block_pmin > 0.0) {
          auto row =
              SparseRow {
                  .class_name = Element::class_name.full_name(),
                  .constraint_name = DischargeLowerName,
                  .variable_uid = uid(),
                  .context = block_ctx,
              }
                  .greater_equal(0.0);
          row[gcol] = 1.0;
          row[u_disc] = -block_pmin;
          static_cast<void>(lp.add_row(std::move(row)));
        }
      }

      // Charge side fallback: mirror the discharge gating onto the
      // synthetic ``Demand.lmin`` / ``Demand.lmax``.
      if (block_lmax > 0.0) {
        lp.col_at(dcol).lowb = 0.0;
        const std::array<BlockUid, 1> u_chg_blocks {buid};
        const auto u_chg = sc.add_integer_col(
            lp,
            IntegerVariable::key(scenario,
                                 stage,
                                 Element::class_name,
                                 uid(),
                                 UChargeName,
                                 IntegerScope::Block,
                                 buid),
            SparseCol {
                .lowb = 0.0,
                .uppb = 1.0,
                .cost = 0.0,
                .class_name = Element::class_name.full_name(),
                .variable_name = UChargeName,
                .variable_uid = uid(),
                .context = block_ctx,
            },
            IntegerDomain::Binary,
            IntegerScope::Block,
            buid,
            std::span<const BlockUid> {u_chg_blocks});
        {
          auto row =
              SparseRow {
                  .class_name = Element::class_name.full_name(),
                  .constraint_name = ChargeUpperName,
                  .variable_uid = uid(),
                  .context = block_ctx,
              }
                  .less_equal(0.0);
          row[dcol] = 1.0;
          row[u_chg] = -block_lmax;
          static_cast<void>(lp.add_row(std::move(row)));
        }
        if (block_lmin > 0.0) {
          auto row =
              SparseRow {
                  .class_name = Element::class_name.full_name(),
                  .constraint_name = ChargeLowerName,
                  .variable_uid = uid(),
                  .context = block_ctx,
              }
                  .greater_equal(0.0);
          row[dcol] = 1.0;
          row[u_chg] = -block_lmin;
          static_cast<void>(lp.add_row(std::move(row)));
        }
      }
    }
  }

  // storing the indices for this scenario and stage
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  capacity_rows[st_key] = std::move(crows);
  generation_rows[st_key] = std::move(grows);
  demand_rows[st_key] = std::move(drows);

  // Register converter attributes as aliases onto the associated
  // generator / demand per-block column maps (lifetime: generator &
  // demand outlive this call and the SimulationLP registry).
  //   discharge -> generator.generation
  //   charge    -> demand.load
  if (!gen_cols.empty()) {
    sc.add_ampl_variable(
        ampl_name, uid(), BatteryLP::DischargeName, scenario, stage, gen_cols);
  }
  if (!demand_cols.empty()) {
    sc.add_ampl_variable(
        ampl_name, uid(), BatteryLP::ChargeName, scenario, stage, demand_cols);
  }

  return true;
}

bool ConverterLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();

  out.add_row_dual(cname, GenerationName, id(), generation_rows);
  out.add_row_dual(cname, DemandName, id(), demand_rows);
  out.add_row_dual(cname, CapacityName, id(), capacity_rows);
  out.add_row_dual(cname, PowerEnvelopeName, id(), power_envelope_rows);
  out.add_row_dual(
      cname, ReserveUpHeadroomName, id(), reserve_up_headroom_rows);
  out.add_row_dual(
      cname, ReserveDnHeadroomName, id(), reserve_dn_headroom_rows);
  out.add_row_dual(cname, ReserveUpEnergyName, id(), reserve_up_energy_rows);
  out.add_row_dual(cname, ReserveDnEnergyName, id(), reserve_dn_energy_rows);

  return CapacityBase::add_to_output(out);
}

bool is_native_bess_reserve_generator(const SystemContext& sc,
                                      const GeneratorLP& gen)
{
  for (const auto& conv : sc.elements<ConverterLP>()) {
    try {
      if (&sc.element<GeneratorLP>(conv.generator_sid()) != &gen) {
        continue;
      }
      const auto& bat = sc.element<BatteryLP>(conv.battery_sid());
      return bat.battery().reserve_sustain_hours.has_value();
    } catch (const std::exception&) {
      continue;  // converter partner not in the LP
    }
  }
  return false;
}

/**
 * Native BESS reserve rows.  With ``pd`` / ``pc`` the synthetic
 * discharge-generator / charge-demand columns, ``e`` the end-of-block
 * SoC column and ``Σr`` the sum over every ReserveProvision of this
 * battery's discharge generator:
 *
 *   reserve_up_headroom:  pd − pc + Σ r_up ≤ Pd_max
 *   reserve_dn_headroom:  pc [− pd] + Σ r_dn ≤ Pc_max
 *   reserve_up_energy:    e − (T/ηd)·Σ r_up ≥ e_lowb      (T > 0)
 *   reserve_dn_energy:    e + T·ηc·Σ r_dn  ≤ e_uppb      (T > 0)
 *
 * Pd_max / Pc_max / e bounds are the hard per-block column bounds.  No
 * reserve column enters a bus balance: the reserve price reaches the bus
 * price only through the battery's own dispatch columns.  The up rows
 * reward withdrawals / penalise injections (they can only raise a bus
 * price); the default (no discharge credit) down headroom never rewards
 * an injection.
 */
void ConverterLP::add_native_reserve_rows(const SystemContext& sc,
                                          const ScenarioLP& scenario,
                                          const StageLP& stage,
                                          LinearProblem& lp,
                                          const GeneratorLP& gen,
                                          const DemandLP& dem,
                                          const BatteryLP& bat)
{
  const double sustain_h =
      std::max(0.0, bat.battery().reserve_sustain_hours.value_or(0.0));
  const bool dn_credit = bat.battery().reserve_discharge_credit.value_or(false);

  std::vector<const ReserveProvisionLP*> provisions;
  for (const auto& rp : sc.elements<ReserveProvisionLP>()) {
    try {
      if (&sc.element<GeneratorLP>(rp.generator_sid()) == &gen) {
        provisions.push_back(&rp);
      }
    } catch (const std::exception&) {
      continue;
    }
  }
  if (provisions.empty()) {
    return;
  }

  auto&& gen_cols = gen.lookup_generation_cols(scenario, stage);
  auto&& demand_cols = dem.lookup_load_cols(scenario, stage);
  const BIndexHolder<ColIndex>* energy_cols = nullptr;
  try {
    energy_cols = &bat.energy_cols_at(scenario, stage);
  } catch (const std::exception&) {
    energy_cols = nullptr;
  }

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto cname = Element::class_name.full_name();

  for (const auto& block : stage.blocks()) {
    const auto buid = block.uid();
    std::vector<ColIndex> ups;
    std::vector<ColIndex> dns;
    for (const auto* rp : provisions) {
      if (const auto c = rp->lookup_up_provision_col(scenario, stage, buid)) {
        ups.push_back(*c);
      }
      if (const auto c = rp->lookup_dn_provision_col(scenario, stage, buid)) {
        dns.push_back(*c);
      }
    }
    if (ups.empty() && dns.empty()) {
      continue;
    }
    const auto ctx = make_block_context(scenario.uid(), stage.uid(), buid);
    const auto gcol_it = gen_cols.find(buid);
    const auto dcol_it = demand_cols.find(buid);
    const bool has_g = gcol_it != gen_cols.end();
    const bool has_d = dcol_it != demand_cols.end();
    const double pd_max = has_g ? lp.get_col_uppb(gcol_it->second) : 0.0;
    const double pc_max = has_d ? lp.get_col_uppb(dcol_it->second) : 0.0;

    if (!ups.empty()) {
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveUpHeadroomName,
              .variable_uid = uid(),
              .context = ctx,
          }
              .less_equal(pd_max);
      if (has_g) {
        row[gcol_it->second] = 1.0;
      }
      if (has_d) {
        row[dcol_it->second] = -1.0;
      }
      for (const auto c : ups) {
        row[c] += 1.0;
      }
      reserve_up_headroom_rows[st_key][buid] = lp.add_row(std::move(row));
    }
    if (!dns.empty()) {
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveDnHeadroomName,
              .variable_uid = uid(),
              .context = ctx,
          }
              .less_equal(pc_max);
      if (has_d) {
        row[dcol_it->second] = 1.0;
      }
      if (dn_credit && has_g) {
        row[gcol_it->second] = -1.0;
      }
      for (const auto c : dns) {
        row[c] += 1.0;
      }
      reserve_dn_headroom_rows[st_key][buid] = lp.add_row(std::move(row));
    }

    if (sustain_h <= 0.0 || energy_cols == nullptr) {
      continue;
    }
    const auto ecol_it = energy_cols->find(buid);
    if (ecol_it == energy_cols->end()) {
      continue;
    }
    const auto ecol = ecol_it->second;
    if (!ups.empty()) {
      const double eta_d =
          bat.param_output_efficiency(stage.uid(), buid).value_or(1.0);
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveUpEnergyName,
              .variable_uid = uid(),
              .context = ctx,
          }
              .greater_equal(lp.get_col_lowb(ecol));
      row[ecol] = 1.0;
      const double coef = eta_d > 0.0 ? sustain_h / eta_d : sustain_h;
      for (const auto c : ups) {
        row[c] -= coef;
      }
      reserve_up_energy_rows[st_key][buid] = lp.add_row(std::move(row));
    }
    if (!dns.empty()) {
      const double eta_c =
          bat.param_input_efficiency(stage.uid(), buid).value_or(1.0);
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveDnEnergyName,
              .variable_uid = uid(),
              .context = ctx,
          }
              .less_equal(lp.get_col_uppb(ecol));
      row[ecol] = 1.0;
      for (const auto c : dns) {
        row[c] += sustain_h * eta_c;
      }
      reserve_dn_energy_rows[st_key][buid] = lp.add_row(std::move(row));
    }
  }
}

}  // namespace gtopt
