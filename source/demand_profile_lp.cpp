/**
 * @file      demand_profile_lp.cpp
 * @brief     Implementation of demand profile LP formulation
 * @date      Sat Apr  5 23:12:03 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module implements DemandProfileLP construction and add_to_lp,
 * which applies time-varying profile factors to demand load variables.
 */

#include <format>
#include <stdexcept>

#include <gtopt/aperture_data_cache.hpp>
#include <gtopt/demand_profile_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/output_context.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

DemandProfileLP::DemandProfileLP(const DemandProfile& pdemand_profile,
                                 InputContext& ic)
    : ProfileObjectLP(pdemand_profile, ic, Element::class_name)
{
}

bool DemandProfileLP::add_to_lp(const SystemContext& sc,
                                const ScenarioLP& scenario,
                                const StageLP& stage,
                                LinearProblem& lp)
{
  auto&& demand = sc.element<DemandLP>(demand_sid());
  if (!demand.is_active(stage)) {
    return true;
  }

  if (demand.has_fail_segments()) {
    throw std::runtime_error(std::format(
        "DemandProfile uid={}: demand uid={} uses a stepwise failure curve "
        "(fail_segments), not supported together with a DemandProfile",
        uid(),
        demand.uid()));
  }
  // Tolerant lookup (see active_ref.hpp): a demand whose every block was
  // elided (lmax = 0, e.g. not yet energized) has no load columns in this
  // cell — nothing for the profile to bound.
  auto&& load_cols = demand.lookup_load_cols(scenario, stage);
  if (load_cols.empty()) {
    return true;
  }

  const auto [opt_capacity, capacity_col] = demand.capacity_and_col(stage, lp);
  const double stage_capacity = opt_capacity.value_or(LinearProblem::DblMax);

  if (!capacity_col && !demand.demand().capacity) {
    fail_lp(*this,
            scenario,
            stage,
            {
                .field = "demand.capacity",
                .value = "unset",
                .expected = "a capacity or an expansion on the demand",
                .detail = std::format(
                    "DemandProfile needs the capacity of demand uid={} to "
                    "scale the profile",
                    demand.uid()),
            });
  }

  return add_profile_to_lp(Element::class_name.full_name(),
                           scenario,
                           stage,
                           lp,
                           UnservedName,
                           load_cols,
                           capacity_col,
                           stage_capacity);
}

bool DemandProfileLP::add_to_output(OutputContext& out) const
{
  return add_profile_to_output(
      Element::class_name.full_name(), out, UnservedName);
}

}  // namespace gtopt
