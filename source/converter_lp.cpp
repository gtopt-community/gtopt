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
#include <format>
#include <optional>
#include <stdexcept>
#include <tuple>
#include <utility>
#include <vector>

#include <gtopt/battery_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/converter_lp.hpp>
#include <gtopt/coupler_fail_closed.hpp>
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

  // Fail-closed coupler (R2): the discharge generator and the battery
  // flows exist only through this converter.  When the converter is
  // inactive, or the battery is not in service, the coupling rows cannot
  // be written — close the producing columns (discharge generation and
  // both battery flows) instead of leaving them free (energy from
  // nowhere).  The charge demand is a consumer and is left as is.
  if (!is_active(stage) || !bat_ptr->is_active(stage)) [[unlikely]] {
    close_producing_cols(lp, gen_ptr->lookup_generation_cols(scenario, stage));
    close_producing_cols(lp, bat_ptr->finp_cols_at(scenario, stage));
    close_producing_cols(lp, bat_ptr->fout_cols_at(scenario, stage));
    return true;
  }

  const auto [opt_capacity, capacity_col] = capacity_and_col(stage, lp);

  const auto stage_conversion_rate =
      conversion_rate.at(stage.uid()).value_or(1.0);

  auto&& blocks = stage.blocks();

  auto&& gen_cols = gen_ptr->lookup_generation_cols(scenario, stage);
  // Tolerant lookup: a battery with pmax_charge = 0 in every block of the
  // stage has no load columns at all (P1 zero-lmax skip).
  auto&& demand_cols = dem_ptr->lookup_load_cols(scenario, stage);
  if (dem_ptr->has_fail_segments()) {
    throw std::runtime_error(std::format(
        "Converter uid={}: demand uid={} uses a stepwise failure curve "
        "(fail_segments); a converter charge demand must be a single "
        "load column (set its fcost)",
        uid(),
        dem_ptr->uid()));
  }
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
    // is still LP-feasible; only the mode binaries with the
    // charge/discharge exclusivity row (``commitment`` +
    // ``exclusive_charge_discharge``, default on) forbid it entirely.
    // The envelope is emitted for those too: it is redundant there but
    // still tightens a relaxed (continuous-phase) u.
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
  // Discharge side: when the generator has a CommitmentLP (always true
  // for batteries via ``System::expand_batteries()`` synthesis), its
  // ``u_commit`` status IS the discharge-mode binary and the gating
  // ``gen ≤ pmax × u`` / ``gen ≥ Commitment.pmin × u`` is already
  // emitted by ``CommitmentLP::add_to_lp`` — nothing to add here.
  // Fallback (legacy, user-built converters without a Commitment):
  // a local ``u_discharge`` binary gating the ``Generator.pmin`` /
  // ``pmax`` col bounds.
  //
  // Charge side: always its OWN ``u_charge`` mode binary.  It must not
  // reuse the discharge ``u_commit``: with one shared u, a committed
  // block would have to honour ``load ≥ pmin_charge`` AND ``gen ≥
  // pmin_discharge`` at once — charge and discharge simultaneously or
  // idle — and a discharge floor above the discharge rating (u ≡ 0)
  // would also lock out charging.  PLEXOS Min Charge / Min Discharge
  // Level and UC.jl min charge / discharge rates are per-mode floors.
  //
  // Charge/discharge exclusivity constraint (default on,
  // ``Converter.exclusive_charge_discharge``): ``u_charge + u_discharge
  // ≤ 1`` per block on the two mode binaries, so the battery is never
  // in both modes in the same block.
  if (converter().commitment.value_or(false)) {
    const bool exclusive =
        converter().exclusive_charge_discharge.value_or(true);
    // Look up the CommitmentLP that points at this converter's
    // discharge generator.
    const BIndexHolder<ColIndex>* shared_ucol = nullptr;
    // Pair by resolved element index (uid and name references alike).
    const auto gen_index = sc.element_index(generator_sid());
    for (const auto& cmt : sc.elements<CommitmentLP>()) {
      if (sc.element_index(cmt.generator_sid()) != gen_index) {
        continue;
      }
      shared_ucol = cmt.find_status_cols(scenario, stage);
      if (shared_ucol != nullptr) {
        break;
      }
    }
    // Same LP-relaxation rule as CommitmentLP for a continuous phase.
    const auto mode_domain =
        sc.simulation().phases()[stage.phase_index()].is_continuous()
        ? IntegerDomain::Relaxed
        : IntegerDomain::Binary;

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
      // Discharge / charge mode binaries of this block (when present).
      std::optional<ColIndex> u_dis_col;
      std::optional<ColIndex> u_chg_col;
      if (shared_ucol != nullptr) {
        if (const auto it = shared_ucol->find(buid); it != shared_ucol->end()) {
          u_dis_col = it->second;
        }
      }

      // ── Legacy discharge fallback (no CommitmentLP) ────────────
      // Migrate ``Generator.pmin`` from a static col floor to a
      // u-gated row.  ``u_discharge`` exists only when the discharge
      // envelope is non-degenerate (pmax > 0).
      if (shared_ucol == nullptr && block_pmax > 0.0) {
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
            mode_domain,
            IntegerScope::Block,
            buid,
            std::span<const BlockUid> {u_disc_blocks});
        u_dis_col = u_disc;
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

      // ── Charge side: own ``u_charge`` mode binary gating the
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
            mode_domain,
            IntegerScope::Block,
            buid,
            std::span<const BlockUid> {u_chg_blocks});
        u_chg_col = u_chg;
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

      // ── Charge/discharge exclusivity: u_charge + u_discharge ≤ 1 ──
      if (exclusive && u_chg_col && u_dis_col) {
        auto row =
            SparseRow {
                .pin_scale = true,  // pure commitment logic
                .class_name = Element::class_name.full_name(),
                .constraint_name = ChargeDischargeExclusiveName,
                .variable_uid = uid(),
                .context = block_ctx,
            }
                .less_equal(1.0);
        row[*u_chg_col] = 1.0;
        row[*u_dis_col] = 1.0;
        static_cast<void>(lp.add_row(std::move(row)));
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
  static constexpr auto rp_cname =
      ReserveProvisionLP::Element::class_name.full_name();
  for (const auto& sc : reserve_split_cols_) {
    out.add_col_sol(rp_cname, ReserveUpDischargeName, sc.id, sc.up_dis);
    out.add_col_sol(rp_cname, ReserveUpChargeName, sc.id, sc.up_chg);
    out.add_col_sol(rp_cname, ReserveDnDischargeName, sc.id, sc.dn_dis);
    out.add_col_sol(rp_cname, ReserveDnChargeName, sc.id, sc.dn_chg);
  }

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

  // Battery-level default; a provision's `ur/dr_energy_usage` overrides.
  const double usage_up = bat.battery().reserve_energy_usage_up.value_or(0.0);
  const double usage_dn = bat.battery().reserve_energy_usage_down.value_or(0.0);
  const bool any_usage = usage_up > 0.0 || usage_dn > 0.0
      || std::ranges::any_of(provisions,
                             [](const ReserveProvisionLP* rp)
                             { return rp->has_energy_usage(); });
  const BIndexHolder<RowIndex>* energy_rows = nullptr;
  const BIndexHolder<ColIndex>* fout_cols = nullptr;
  const BIndexHolder<ColIndex>* finp_cols = nullptr;
  if (any_usage) {
    try {
      energy_rows = &bat.energy_rows_at(scenario, stage);
      fout_cols = &bat.fout_cols_at(scenario, stage);
      finp_cols = &bat.finp_cols_at(scenario, stage);
    } catch (const std::exception&) {
      energy_rows = nullptr;
    }
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
    // An unrated side (the 1e20 / DblMax "no rating" sentinel) makes the
    // headroom row vacuous: omit it rather than emit an infinite RHS.
    const auto unrated = [&](double v)
    { return v >= 1e19 || is_infinity(v, lp.infinity()); };
    // With the per-side split (Max Response caps) the side rows imply the
    // aggregate headroom row (their sum, via r = r_dis + r_chg): emitting
    // both makes the rows linearly dependent and the headroom duals
    // ambiguous, so the aggregate row is skipped for that direction.
    const auto split_dir = [&](bool upward)
    {
      return !(upward ? ups : dns).empty()
          && std::ranges::any_of(provisions,
                                 [upward](const ReserveProvisionLP* rp)
                                 { return rp->has_response_caps(upward); });
    };
    const bool split_up = split_dir(true);
    const bool split_dn = split_dir(false);

    if (!ups.empty() && !split_up && !unrated(pd_max)) {
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
    if (!dns.empty() && !split_dn && !unrated(pc_max)) {
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

    // PLEXOS Max Response / Max Charge Response: per-side split.
    const auto gcol = has_g ? std::optional {gcol_it->second} : std::nullopt;
    const auto dcol = has_d ? std::optional {dcol_it->second} : std::nullopt;
    for (const bool upward : {true, false}) {
      if (!(upward ? split_up : split_dn)) {
        continue;
      }
      add_native_reserve_split(
          scenario, stage, buid, lp, upward, provisions, gcol, dcol, dn_credit);
    }

    // Expected deployment energy charged to the SoC (replaces the PLEXOS
    // CF_GEN_COMP / CF_LOAD_COMP AUX injection; no bus term).
    if (energy_rows != nullptr) {
      if (const auto erow_it = energy_rows->find(buid);
          erow_it != energy_rows->end())
      {
        const auto erow = erow_it->second;
        // Same coefficient as one MW of discharge ((1/ηd)·Δ) per MW of up
        // reserve, and as one MW of charge (−ηc·Δ) per MW of down.
        const auto unit_of =
            [&](const BIndexHolder<ColIndex>* cols) -> std::optional<double>
        {
          if (cols == nullptr) {
            return std::nullopt;
          }
          const auto it = cols->find(buid);
          if (it == cols->end()) {
            return std::nullopt;
          }
          return lp.get_coeff(erow, it->second);
        };
        const auto unit_up = unit_of(fout_cols);
        const auto unit_dn = unit_of(finp_cols);
        // Split side columns of one provision at this block (present when
        // the direction has Max Response caps).
        const auto split_of = [&](const ReserveProvisionLP& rp, bool upward)
            -> std::pair<std::optional<ColIndex>, std::optional<ColIndex>>
        {
          const auto it = std::ranges::find(
              reserve_split_cols_, rp.uid(), &ReserveSplitCols::uid);
          if (it == reserve_split_cols_.end()) {
            return {};
          }
          const auto at = [&](const STBIndexHolder<ColIndex>& h)
          { return lookup_inner(h, scenario, stage, buid); };
          return upward ? std::pair {at(it->up_dis), at(it->up_chg)}
                        : std::pair {at(it->dn_dis), at(it->dn_chg)};
        };
        // Deployed energy per MW of reserve: up via more discharge
        // (+Δ/ηd, the discharge unit) or via less charge (−(−ηc·Δ), the
        // negated charge unit); down via more charge (−ηc·Δ) or via less
        // discharge (−Δ/ηd).  Without the split the whole provision takes
        // the primary side's coefficient.
        for (const auto* rp : provisions) {
          for (const bool upward : {true, false}) {
            const auto primary = upward ? unit_up : unit_dn;
            const auto other = upward ? unit_dn : unit_up;
            if (!primary) {
              continue;
            }
            const auto c = upward
                ? rp->lookup_up_provision_col(scenario, stage, buid)
                : rp->lookup_dn_provision_col(scenario, stage, buid);
            if (!c) {
              continue;
            }
            const double a = rp->energy_usage(upward, stage.uid(), buid)
                                 .value_or(upward ? usage_up : usage_dn);
            if (a <= 0.0) {
              continue;
            }
            const auto [dis_c, chg_c] = split_of(*rp, upward);
            if (!dis_c && !chg_c) {
              lp.set_coeff(erow, *c, a * *primary);
              continue;
            }
            // Up: discharge side = primary, charge side = −other.
            // Down: charge side = primary, discharge side = −other.
            const auto& primary_side = upward ? dis_c : chg_c;
            const auto& other_side = upward ? chg_c : dis_c;
            if (primary_side) {
              lp.set_coeff(erow, *primary_side, a * *primary);
            }
            if (other_side && other) {
              lp.set_coeff(erow, *other_side, -a * *other);
            }
          }
        }
      }
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

void ConverterLP::add_native_reserve_split(
    const ScenarioLP& scenario,
    const StageLP& stage,
    BlockUid buid,
    LinearProblem& lp,
    bool upward,
    const std::vector<const ReserveProvisionLP*>& provisions,
    std::optional<ColIndex> gcol,
    std::optional<ColIndex> dcol,
    bool dn_credit)
{
  const auto cname = Element::class_name.full_name();
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto ctx = make_block_context(scenario.uid(), stage.uid(), buid);
  // Down reserve from the discharge side only with the credit flag.
  const bool dis_side = upward || dn_credit;
  const auto dis_name =
      upward ? ReserveUpDischargeName : ReserveDnDischargeName;
  const auto chg_name = upward ? ReserveUpChargeName : ReserveDnChargeName;
  static constexpr auto rp_cname =
      ReserveProvisionLP::Element::class_name.full_name();

  // Side rows.  Up: pd + Σr_dis ≤ Pd, Σr_chg − pc ≤ 0.
  //           Down: Σr_dis − pd ≤ 0, pc + Σr_chg ≤ Pc.
  auto dis_row =
      SparseRow {
          .class_name = cname,
          .constraint_name = dis_name,
          .variable_uid = uid(),
          .context = ctx,
      }
          .less_equal(upward && gcol ? lp.get_col_uppb(*gcol) : 0.0);
  auto chg_row =
      SparseRow {
          .class_name = cname,
          .constraint_name = chg_name,
          .variable_uid = uid(),
          .context = ctx,
      }
          .less_equal(!upward && dcol ? lp.get_col_uppb(*dcol) : 0.0);
  if (gcol) {
    dis_row[*gcol] = upward ? 1.0 : -1.0;
  }
  if (dcol) {
    chg_row[*dcol] = upward ? -1.0 : 1.0;
  }

  bool any_dis = false;
  for (const auto* rp : provisions) {
    const auto rcol = upward
        ? rp->lookup_up_provision_col(scenario, stage, buid)
        : rp->lookup_dn_provision_col(scenario, stage, buid);
    if (!rcol) {
      continue;
    }
    const auto caps = rp->response_caps(upward, stage.uid(), buid);
    auto& out_cols = split_cols_of(*rp);
    // Labelled per provision: one battery may carry several capped
    // provisions (CPF + CSF + CTF) in the same direction.
    auto split =
        SparseRow {
            .class_name = rp_cname,
            .constraint_name = upward ? ReserveUpSplitName : ReserveDnSplitName,
            .variable_uid = rp->uid(),
            .context = ctx,
        }
            .equal(0.0);
    split[*rcol] = 1.0;
    if (dis_side) {
      const auto dcol_r = lp.add_col({
          .uppb = caps.discharge.value_or(DblMax),
          .class_name = rp_cname,
          .variable_name = dis_name,
          .variable_uid = rp->uid(),
          .context = ctx,
      });
      split[dcol_r] = -1.0;
      dis_row[dcol_r] = 1.0;
      any_dis = true;
      (upward ? out_cols.up_dis : out_cols.dn_dis)[st_key][buid] = dcol_r;
    }
    const auto ccol_r = lp.add_col({
        .uppb = caps.charge.value_or(DblMax),
        .class_name = rp_cname,
        .variable_name = chg_name,
        .variable_uid = rp->uid(),
        .context = ctx,
    });
    split[ccol_r] = -1.0;
    chg_row[ccol_r] = 1.0;
    (upward ? out_cols.up_chg : out_cols.dn_chg)[st_key][buid] = ccol_r;
    std::ignore = lp.add_row(std::move(split));
  }
  // A side row whose RHS is the unrated sentinel is vacuous: omit it.
  const auto unrated = [&](const SparseRow& row)
  { return row.uppb >= 1e19 || is_infinity(row.uppb, lp.infinity()); };
  if (any_dis && !unrated(dis_row)) {
    std::ignore = lp.add_row(std::move(dis_row));
  }
  if (!unrated(chg_row)) {
    std::ignore = lp.add_row(std::move(chg_row));
  }
}

ConverterLP::ReserveSplitCols& ConverterLP::split_cols_of(
    const ReserveProvisionLP& rp)
{
  const auto it =
      std::ranges::find(reserve_split_cols_, rp.uid(), &ReserveSplitCols::uid);
  if (it != reserve_split_cols_.end()) {
    return *it;
  }
  return reserve_split_cols_.emplace_back(ReserveSplitCols {
      .uid = rp.uid(),
      .id = rp.id(),
      .up_dis = {},
      .up_chg = {},
      .dn_dis = {},
      .dn_chg = {},
  });
}

}  // namespace gtopt
