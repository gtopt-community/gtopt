/**
 * @file      inertia_provision_lp.cpp
 * @brief     LP formulation for inertia provisions
 * @date      2026-04-13
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Implements InertiaProvisionLP: creates an r_inertia variable per block
 * and adds a downward coupling constraint:
 *   p - r_inertia >= 0  (generator must produce at least r_inertia)
 *
 * The provision_factor (FE = H*S/Pmin [MWs/MW]) is injected as a
 * coefficient on r_inertia into the InertiaZone requirement row.
 */

#include <algorithm>
#include <ranges>
#include <span>
#include <vector>

#include <gtopt/commitment_lp.hpp>
#include <gtopt/inertia_provision_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <spdlog/spdlog.h>

namespace
{

using namespace gtopt;

auto make_izone_indexes(const InputContext& ic,
                        std::span<const SingleId> izones)
{
  // Each `SingleId` in `izones` is resolved directly via
  // `ic.element_index`.
  using IZoneId = ObjectSingleId<InertiaZoneLP>;
  return std::ranges::to<std::vector>(
      izones
      | std::views::transform([&](const SingleId& iz)
                              { return ic.element_index(IZoneId {iz}); }));
}

}  // namespace

namespace gtopt
{

InertiaProvisionLP::InertiaProvisionLP(const InertiaProvision& ip,
                                       const InputContext& ic)
    : Base(ip, ic, Element::class_name)
    , generator_index_(ic.element_index(generator_sid()))
    , inertia_zone_indexes_(
          make_izone_indexes(ic, inertia_provision().inertia_zones))
    , provision_max_(ic,
                     Element::class_name,
                     id(),
                     std::move(inertia_provision().provision_max))
    , provision_factor_(ic,
                        Element::class_name,
                        id(),
                        std::move(inertia_provision().provision_factor))
    , cost_(ic, Element::class_name, id(), std::move(inertia_provision().cost))
{
}

bool InertiaProvisionLP::add_to_lp(const SystemContext& sc,
                                   const ScenarioLP& scenario,
                                   const StageLP& stage,
                                   LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }

  auto&& generator_lp = sc.element(generator_index_);
  if (!generator_lp.is_active(stage)) {
    return true;
  }

  // No try/catch: any failure propagates to the add_to_lp driver, which
  // reports it with the element context (lp_build_error.hpp).
  {
    // Tolerant lookup (mirrors reserve_provision_lp / emission_source_lp):
    // when every block was elided by the P1 zero-pmax optimization the
    // outer key is absent.  ``generation_cols_at`` would throw; the
    // empty map returned here makes the per-block ``find`` below iterate
    // zero times so no inertia constraint is emitted for an OFF unit.
    auto&& generation_cols =
        generator_lp.lookup_generation_cols(scenario, stage);
    const auto& blocks = stage.blocks();

    // `provision_factor_` / `cost_` are now per-(stage, block); the
    // legacy per-stage `stage_pf` short-circuit is replaced by a
    // per-block resolution inside the loop.
    const auto& ip = inertia_provision();

    const auto st_key = std::tuple {scenario.uid(), stage.uid()};
    // Reserve zones whose plane-based requirement uses one of this
    // provision's inertia zones: their inertia definition row
    // `I − Σ pf·r = 0` receives `−pf` (ReserveZoneLP runs first and has
    // already resolved its inertia-zone reference).
    std::vector<const ReserveZoneLP*> plane_zones;
    for (const auto& rz : sc.elements<ReserveZoneLP>()) {
      const auto& rz_iz = rz.inertia_zone_index();
      if (rz_iz && std::ranges::contains(inertia_zone_indexes_, *rz_iz)) {
        plane_zones.push_back(&rz);
      }
    }

    // Commitment of this unit (nullptr when none): its
    // status feeds the `"commitment"` / `"exogenous"` plane inertia
    // terms, its committed minimum the default provision factor / cap.
    const auto* const commitment =
        find_unit_commitment(sc, generator_index_, stage);

    BIndexHolder<ColIndex> pcols;
    BIndexHolder<RowIndex> crows;
    map_reserve(pcols, blocks.size());
    map_reserve(crows, blocks.size());

    for (const auto& block : blocks) {
      const auto buid = block.uid();

      const auto gcol_it = generation_cols.find(buid);
      if (gcol_it == generation_cols.end()) {
        continue;
      }
      const auto gcol = gcol_it->second;

      // Pmin is the committed minimum stable output (max of
      // Generator.pmin and Commitment.pmin), from the input parameters:
      // the generation column's lowb is 0 under the documented UC
      // pattern (Generator.pmin = 0 + Commitment.pmin), which made both
      // defaults below fall back to Pmax.
      const auto gen_pmin =
          committed_pmin(commitment, generator_lp, stage.uid(), buid);
      const auto gen_pmax = lp.get_col_uppb(gcol);

      // Resolve provision_factor for this block.  Per-(stage, block)
      // schedule wins; fallback chain is H × S / Pmin → 1.0.
      const auto block_pf = provision_factor_.optval(stage.uid(), buid);
      double pf_value = 1.0;
      if (block_pf) {
        pf_value = block_pf.value();
      } else if (ip.inertia_constant && ip.rated_power) {
        const auto pmin_eff =
            (gen_pmin > 0.0) ? gen_pmin : gen_pmax;  // fallback
        if (pmin_eff > 0.0) {
          pf_value =
              ip.inertia_constant.value() * ip.rated_power.value() / pmin_eff;
        }
      }

      if (pf_value <= 0.0) {
        continue;
      }

      // Resolve provision_max: schedule > committed Pmin > gen_pmax
      auto block_pmax = provision_max_.optval(stage.uid(), buid)
                            .value_or(gen_pmin > 0.0 ? gen_pmin : gen_pmax);

      // LP-size: a zero provision ceiling fixes ``r_inertia`` at 0 — the
      // coupling row ``p − r_inertia ≥ 0`` reduces to ``p ≥ 0`` (already
      // implied by the generation column's lower bound) and the
      // ``pf · r_inertia`` term injected into every zone requirement row
      // is identically 0.  Skip the fixed-zero column, the coupling row,
      // and the zone-coefficient stamps.  Write-out rule: an absent
      // provision column reads 0 (no inertia provided this block).
      if (sc.options().lp_reduction() && block_pmax <= 0.0) {
        continue;
      }

      const auto block_cost = cost_.optval(stage.uid(), buid).value_or(0.0);

      // Create provision variable r_inertia
      const auto pcol = lp.add_col({
          .uppb = block_pmax,
          .cost = CostHelper::block_ecost(scenario, stage, block, block_cost),
          .class_name = cname,
          .variable_name = ProvisionName,
          .variable_uid = uid(),
          .context =
              make_block_context(scenario.uid(), stage.uid(), block.uid()),
      });
      pcols[buid] = pcol;

      // Downward coupling: p - r_inertia >= 0
      auto crow =
          SparseRow {
              .class_name = cname,
              .constraint_name = CouplingName,
              .variable_uid = uid(),
              .context =
                  make_block_context(scenario.uid(), stage.uid(), block.uid()),
          }
              .greater_equal(0.0);
      crow[gcol] = 1.0;
      crow[pcol] = -1.0;
      crows[buid] = lp.add_row(std::move(crow));

      // Inject provision_factor * r_inertia into each zone's requirement row
      for (auto&& izone_index : inertia_zone_indexes_) {
        auto&& izone = sc.element(izone_index);
        if (!izone.is_active(stage)) {
          continue;
        }

        const auto& req_rows = izone.requirement_rows();
        const auto req_rows_it = req_rows.find(st_key);
        if (req_rows_it == req_rows.end() || req_rows_it->second.empty()) {
          continue;
        }
        const auto req_row_it = req_rows_it->second.find(buid);
        if (req_row_it != req_rows_it->second.end()) {
          lp.set_coeff(req_row_it->second, pcol, pf_value);
        }
      }

      // CPF plane inertia term (ReserveZoneLP::inertia_term):
      //  endogenous  I −= pf·r            (dispatch-based, PLEXOS-compat)
      //  commitment  I −= I_c·u           (status), or K += I_c when the
      //              unit has no status column but is forced on (pmin > 0)
      //  exogenous   K += I_c for must-run units only (status pinned on,
      //              or uncommitted with pmin > 0) — pure data
      // I_c = pf · provision_max = H·S when on, capped by the unit's own
      // pmax: a loose provision_max (harmless in the endogenous form, where
      // r ≤ p ≤ pmax) must not inflate the status-based inertia.
      const bool finite_pmax = gen_pmax > 0.0 && gen_pmax < 1e19
          && !is_infinity(gen_pmax, lp.infinity());
      const double unit_inertia = pf_value
          * (finite_pmax ? std::min(block_pmax, gen_pmax) : block_pmax);
      // Without a status column the unit is forced on only by a hard
      // always-on floor, i.e. the generation column's own lower bound.
      const auto ucol = commitment != nullptr
          ? commitment->lookup_status_col(scenario, stage, buid)
          : std::nullopt;
      const bool forced_on = ucol ? lp.get_col_lowb(*ucol) >= 1.0 - 1e-9
                                  : lp.get_col_lowb(gcol) > 0.0;
      for (const auto* rz : plane_zones) {
        const auto irow = rz->lookup_inertia_row(scenario, stage, buid);
        if (!irow) {
          continue;
        }
        const auto kcol = rz->lookup_inertia_fixed_col(scenario, stage, buid);
        const auto raise_fixed = [&]
        {
          if (kcol) {
            const double k = lp.get_col_uppb(*kcol) + unit_inertia;
            lp.set_col_lowb(*kcol, k);
            lp.set_col_uppb(*kcol, k);
          }
        };
        switch (rz->inertia_term()) {
          case ReserveZoneLP::InertiaTerm::endogenous:
            lp.set_coeff(*irow, pcol, -pf_value);
            break;
          case ReserveZoneLP::InertiaTerm::commitment:
            if (ucol) {
              lp.set_coeff(*irow, *ucol, -unit_inertia);
            } else if (forced_on) {
              raise_fixed();
            }
            break;
          case ReserveZoneLP::InertiaTerm::exogenous:
            if (forced_on) {
              raise_fixed();
            }
            break;
        }
      }
    }

    // Store index holders
    if (!pcols.empty()) {
      provision_cols_[st_key] = std::move(pcols);
    }
    if (!crows.empty()) {
      coupling_rows_[st_key] = std::move(crows);
    }

    // Register PAMPL-visible provision columns
    if (const auto it = provision_cols_.find(st_key);
        it != provision_cols_.end() && !it->second.empty())
    {
      sc.add_ampl_variable(
          ampl_name, uid(), ProvName, scenario, stage, it->second);
    }
  }

  return true;
}

bool InertiaProvisionLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  out.add_col_sol(cname, ProvisionName, pid, provision_cols_);
  out.add_col_cost(cname, ProvisionName, pid, provision_cols_);
  out.add_row_dual(cname, CouplingName, pid, coupling_rows_);

  return true;
}

}  // namespace gtopt
