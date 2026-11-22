// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_irrigation_laja_cap.cpp
 * @brief     L2 — Laja attribution cap must not carry the seepage term
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * PLP caps the riego-side charging at a NUMERIC net requirement
 * (`qdrh + qdmh + qgah <= qdefm`, genpdlajam.f:290-296; qdefm from
 * GetQsLajaM with the static filtration FiltVals).  A former gtopt
 * variant added the reservoir's volume-dependent seepage column to the
 * row (`rights + qf(V) <= K`).  With the cap binding, one more hm³ in
 * the reservoir then raises qf, tightens the cap and forces thermal
 * generation: the reservoir's water value turns NEGATIVE.  These tests
 * pin the PLP form (value >= 0) against the seepage form (value < 0).
 */

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

constexpr Real kLajaCap = 20.0;  // qdefm [m3/s]

[[nodiscard]] Simulation make_laja_cap_day()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 24,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
}

[[nodiscard]] System make_laja_cap_system(std::string cap_expression)
{
  return {
      .name = "LajaCap",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .lmax = 100.0,
                  .fcost = 1000.0,
                  .capacity = 100.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "eltoro",
                  .bus = Uid {1},
                  .gcost = 0.0,
                  .capacity = 400.0,
              },
              {
                  .uid = Uid {2},
                  .name = "thermal",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 400.0,
              },
          },
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_lago",
              },
              {
                  .uid = Uid {2},
                  .name = "j_down",
                  .drain = true,
              },
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ww_gen",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 0.0,
                  .fmax = 200.0,
              },
              {
                  .uid = Uid {2},
                  .name = "ww_filt",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 0.0,
                  .fmax = 200.0,
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "lago",
                  .junction = Uid {1},
                  .capacity = 1000.0,
                  .emin = 0.0,
                  .emax = 1000.0,
                  .eini = 500.0,
              },
          },
      .reservoir_seepage_array =
          {
              {
                  .uid = Uid {1},
                  .name = "lago_filt",
                  .waterway = Uid {2},
                  .reservoir = Uid {1},
                  .slope = 0.01,
                  .constant = 0.0,
              },
          },
      .turbine_array =
          {
              {
                  .uid = Uid {1},
                  .name = "tur",
                  .waterway = Uid {1},
                  .generator = Uid {1},
                  .production_factor = 2.0,
              },
          },
      .flow_right_array =
          {
              {
                  .uid = Uid {1},
                  .name = "laja_der_riego",
                  .fmax = 200.0,
              },
          },
      .user_constraint_array =
          {
              {
                  .uid = Uid {1},
                  .name = "laja_particion",
                  .expression =
                      R"(flow_right("laja_der_riego").flow = waterway("ww_gen").flow)",
                  .constraint_type = "raw",
              },
              {
                  .uid = Uid {2},
                  .name = "laja_retiro_maximo",
                  .expression = std::move(cap_expression),
                  .constraint_type = "raw",
              },
          },
  };
}

struct LajaCapResult
{
  Real riego {0.0};
  Real value {0.0};  ///< water value = −(raw energy-balance dual)
};

[[nodiscard]] LajaCapResult solve_laja_cap(std::string cap_expression)
{
  const auto sim = make_laja_cap_day();
  const auto system = make_laja_cap_system(std::move(cap_expression));
  const PlanningOptionsLP options;
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(system, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  const auto& scenarios = system_lp.scene().scenarios();
  const auto& stages = system_lp.phase().stages();
  const auto buid = stages[0].blocks().front().uid();
  const auto& rsv_lp = system_lp.elements<ReservoirLP>().front();
  const auto& rows = rsv_lp.energy_rows_at(scenarios[0], stages[0]);
  const auto duals = lp.get_row_dual();

  const auto& sc = system_lp.system_context();
  const auto col = sc.find_ampl_col(
      "flow_right", Uid {1}, "flow", scenarios[0].uid(), stages[0].uid(), buid);
  REQUIRE(col.has_value());
  return {
      .riego = col ? lp.get_col_sol()[*col] : 0.0,
      .value = -duals[rows.at(buid)],
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "Laja cap - PLP numeric qdefm cap keeps the water value non-negative")
{
  // Demand 100 MW wants 50 m3/s; the cap allows 20 -> binding.  The
  // lake is abundant, so its water value is 0 (never negative).
  const auto r = solve_laja_cap(R"(flow_right("laja_der_riego").flow <= 20)");
  CHECK(r.riego == doctest::Approx(kLajaCap));
  CHECK(r.value >= -1e-9);
}

TEST_CASE(  // NOLINT
    "Laja cap - seepage term in the cap makes the water value negative")
{
  // rights + qf(V) <= K: more volume -> more seepage -> less riego
  // turbining -> more thermal.  The pre-fix form; kept as the
  // regression witness of why the term was removed.
  const auto r = solve_laja_cap(
      R"(flow_right("laja_der_riego").flow + seepage("lago_filt").flow <= 20)");
  CHECK(r.riego < kLajaCap);
  CHECK(r.value < 0.0);
}
