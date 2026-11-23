// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_coverage_gaps_tangent_signed_flow_expansion.cpp
 * @brief     tangent_signed_flow on an expandable line: capacity rows
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Coverage audit 2026-11-22: the expandable-line branch of
 * `add_tangent_signed_flow` (line_losses.cpp 2147-2171) had zero hits —
 * every tangent_signed_flow fixture used a fixed-capacity line.  With a
 * single SIGNED flow column the capacity bound `|f| <= capainst` needs
 * two rows (`capainst − f >= 0`, `capainst + f >= 0`); a sign slip in
 * the mirrored row would let reverse flow ignore the installed capacity.
 */

#include <cmath>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/line.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace test_coverage_gaps_tsf_expansion_ns
{
namespace
{

constexpr double kDemand = 100.0;
constexpr double kInstalled = 40.0;  // below demand → must expand

/// Two buses, the only generator on @p gen_bus, the demand on the other
/// bus; one expandable tangent_signed_flow line 1→2.
struct ExpandableLineFixture
{
  System system;
  Simulation simulation;
  PlanningOptionsLP options;
  SimulationLP sim_lp;
  SystemLP sys_lp;

  explicit ExpandableLineFixture(Uid gen_bus)
      : system {
            .name = "TsfExpansion",
            .bus_array =
                {
                    {.uid = Uid {1}, .name = "b1",},
                    {.uid = Uid {2}, .name = "b2",},
                },
            .demand_array =
                {
                    {
                        .uid = Uid {1},
                        .name = "d1",
                        .bus = (gen_bus == Uid {1}) ? Uid {2} : Uid {1},
                        .capacity = kDemand,
                    },
                },
            .generator_array =
                {
                    {
                        .uid = Uid {1},
                        .name = "g1",
                        .bus = gen_bus,
                        .gcost = 10.0,
                        .capacity = 500.0,
                    },
                },
            .line_array =
                {
                    {
                        .uid = Uid {1},
                        .name = "l1",
                        .bus_a = Uid {1},
                        .bus_b = Uid {2},
                        .voltage = 100.0,
                        .resistance = 0.01,
                        .line_losses_mode =
                            OptName {std::string("tangent_signed_flow")},
                        .loss_segments = 5,
                        .tmax_ba = 300.0,
                        .tmax_ab = 300.0,
                        .capacity = kInstalled,
                        .expcap = 50.0,
                        .expmod = 4.0,
                        .capmax = 300.0,
                        .annual_capcost = 1.0,
                    },
                },
        }
      , simulation {
            .block_array = {{.uid = Uid {1}, .duration = 1,},},
            .stage_array =
                {{.uid = Uid {1}, .first_block = 0, .count_block = 1,},},
            .scenario_array = {{.uid = Uid {0},},},
        }
      , options(make_options())
      , sim_lp(simulation, options)
      , sys_lp(system, sim_lp, make_matrix_options())
  {
  }

  [[nodiscard]] auto& lp() { return sys_lp.linear_interface(); }

private:
  static PlanningOptionsLP make_options()
  {
    PlanningOptions opts;
    opts.model_options.use_single_bus = false;
    opts.model_options.use_kirchhoff = false;
    opts.model_options.scale_objective = 1.0;
    opts.model_options.demand_fail_cost = 1000.0;
    opts.lp_matrix_options.col_with_names = true;
    opts.lp_matrix_options.row_with_names = true;
    opts.lp_matrix_options.col_with_name_map = true;
    opts.lp_matrix_options.row_with_name_map = true;
    return PlanningOptionsLP(opts);
  }

  static LpMatrixOptions make_matrix_options()
  {
    LpMatrixOptions bo;
    bo.col_with_names = true;
    bo.col_with_name_map = true;
    bo.row_with_names = true;
    bo.row_with_name_map = true;
    return bo;
  }
};

[[nodiscard]] int count_rows_containing(const LinearInterface& li,
                                        std::string_view substr)
{
  int count = 0;
  for (const auto& [name, _idx] : li.row_name_map()) {
    if (name.contains(substr)) {
      ++count;
    }
  }
  return count;
}

[[nodiscard]] ColIndex find_unique_col(const LinearInterface& li,
                                       std::string_view substr)
{
  ColIndex found {-1};
  int count = 0;
  for (const auto& [name, idx] : li.col_name_map()) {
    if (name.contains(substr)) {
      found = idx;
      ++count;
    }
  }
  REQUIRE(count == 1);
  return found;
}

}  // namespace
}  // namespace test_coverage_gaps_tsf_expansion_ns

using test_coverage_gaps_tsf_expansion_ns::count_rows_containing;
using test_coverage_gaps_tsf_expansion_ns::ExpandableLineFixture;
using test_coverage_gaps_tsf_expansion_ns::find_unique_col;
using test_coverage_gaps_tsf_expansion_ns::kDemand;
using test_coverage_gaps_tsf_expansion_ns::kInstalled;

TEST_CASE("tangent_signed_flow expandable line emits both capacity rows")
{
  ExpandableLineFixture fix(Uid {1});
  auto& li = fix.lp();
  CHECK(count_rows_containing(li, "line_capacityp_") == 1);
  CHECK(count_rows_containing(li, "line_capacityn_") == 1);

  const auto fcol = find_unique_col(li, "line_flows_");
  const auto cap_col = find_unique_col(li, "line_capainst_");
  // +direction row: capainst − f ≥ 0 ; −direction row: capainst + f ≥ 0.
  for (const auto& [name, row] : li.row_name_map()) {
    if (name.contains("line_capacityp_")) {
      CHECK(li.get_coeff(row, cap_col) > 0.0);
      CHECK(li.get_coeff(row, fcol) < 0.0);
    } else if (name.contains("line_capacityn_")) {
      CHECK(li.get_coeff(row, cap_col) > 0.0);
      CHECK(li.get_coeff(row, fcol) > 0.0);
    }
  }
}

TEST_CASE(
    "tangent_signed_flow expandable line: installed capacity bounds |flow| "
    "in both directions")
{
  for (const auto gen_bus : {Uid {1}, Uid {2}}) {
    CAPTURE(gen_bus);
    ExpandableLineFixture fix(gen_bus);
    auto& li = fix.lp();
    auto r = li.resolve();
    REQUIRE(r.has_value());
    REQUIRE(li.is_optimal());

    const auto sol = li.get_col_sol();
    const auto f = sol[find_unique_col(li, "line_flows_")];
    const auto cap = sol[find_unique_col(li, "line_capainst_")];
    CAPTURE(f);
    CAPTURE(cap);

    // Gen on bus 1 → positive flow; gen on bus 2 → negative flow.
    if (gen_bus == Uid {1}) {
      CHECK(f > kDemand - 1e-6);
    } else {
      CHECK(f < -(kDemand - 1e-6));
    }
    // The demand exceeds the installed capacity, so the line must have
    // been expanded and |f| must respect the installed capacity.
    CHECK(cap > kInstalled + 1.0);
    CHECK(std::abs(f) <= cap + 1e-6);
  }
}
