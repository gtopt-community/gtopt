// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_cascade_reduced_levels.cpp
 * @brief     Cascade levels on reduced networks (`CascadeLevel::system_file`)
 *            and per-level output sets (`write_level_outputs`).
 * @date      2026-11-22
 *
 * A 4-bus meshed hydro case is reduced to a 2-node zonal equivalent
 * (buses {1,2} → node 1, {3,4} → node 3; inter-zone lines aggregated by
 * summing capacities; no Kirchhoff, no losses).  That equivalent is a
 * relaxation of the full network, so its Benders cuts are valid lower
 * bounds for the full problem.  The tests pin:
 *
 *  1. a reduced L0 → full L1 cascade transfers cuts (keyed by state
 *     variable class/name/uid, not by column index, even though the two
 *     LPs have different sizes) and converges to the same optimum as an
 *     SDDP run on the full network alone;
 *  2. the reduced level's bound never exceeds the full optimum
 *     (relaxation);
 *  3. `write_level_outputs` leaves a complete output set per
 *     intermediate level plus `cascade_levels.csv`.
 */

#include <filesystem>
#include <format>
#include <fstream>
#include <string>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/cascade_method.hpp>
#include <gtopt/cascade_options.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <unistd.h>  // ::getpid()

#include "cascade_helpers.hpp"

using namespace gtopt;

namespace
{

/// 4-bus meshed variant of the 3-phase hydro case.
///   bus 1: hydro (80 MW, 5 $/MWh) — bus 2: thermal (50 $/MWh, 200 MW)
///   bus 3: 60 MW load            — bus 4: thermal (30 $/MWh, 40 MW)
/// Lines 1-2, 2-3, 1-3 (100 MW), 3-4 and 2-4 (15 MW), meshed with KVL.
/// The hydro is water-limited, so the cheap bus-4 thermal is wanted at
/// 40 MW but can only export 30 MW — the 2-node relaxation cannot see
/// that limit, the full network can.
auto make_4bus_hydro_planning() -> Planning
{
  auto planning = make_3phase_2bus_hydro_planning();
  auto& sys = planning.system;
  sys.name = "cascade_4bus_hydro";
  sys.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
      {
          .uid = Uid {2},
          .name = "b2",
      },
      {
          .uid = Uid {3},
          .name = "b3",
      },
      {
          .uid = Uid {4},
          .name = "b4",
      },
  };
  sys.generator_array = {
      {
          .uid = Uid {1},
          .name = "hydro_gen",
          .bus = Uid {1},
          .gcost = 5.0,
          .capacity = 80.0,
      },
      {
          .uid = Uid {2},
          .name = "thermal_gen",
          .bus = Uid {2},
          .gcost = 50.0,
          .capacity = 200.0,
      },
      {
          .uid = Uid {3},
          .name = "thermal_b4",
          .bus = Uid {4},
          .gcost = 30.0,
          .capacity = 40.0,
      },
  };
  sys.demand_array = {
      {
          .uid = Uid {1},
          .name = "load1",
          .bus = Uid {3},
          .capacity = 60.0,
      },
  };
  const auto line = [](int uid, int a, int b, double x, double cap)
  {
    return Line {
        .uid = Uid {uid},
        .name = std::format("l{}_{}", a, b),
        .bus_a = Uid {a},
        .bus_b = Uid {b},
        .reactance = x,
        .tmax_ba = cap,
        .tmax_ab = cap,
    };
  };
  sys.line_array = {
      line(1, 1, 2, 0.05, 100.0),
      line(2, 2, 3, 0.05, 100.0),
      line(3, 1, 3, 0.10, 100.0),
      line(4, 3, 4, 0.05, 15.0),
      line(5, 2, 4, 0.05, 15.0),
  };
  return planning;
}

/// Zonal 2-node equivalent of `make_4bus_hydro_planning()` as
/// `gtopt-net reduce --bus-groups` writes it: representatives b1 / b3,
/// one aggregated corridor (lines 2, 3, 5 → 215 MW), components
/// re-pointed; hydro (reservoir, turbine, waterway) untouched, so the
/// state variables match the full network by uid.
auto make_4bus_two_node_system() -> System
{
  auto sys = make_4bus_hydro_planning().system;
  sys.name = "cascade_4bus_hydro_2node";
  sys.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
      {
          .uid = Uid {3},
          .name = "b3",
      },
  };
  sys.generator_array[1].bus = Uid {1};  // b2 → b1
  sys.generator_array[2].bus = Uid {3};  // b4 → b3
  sys.line_array = {
      Line {
          .uid = Uid {100001},
          .name = "eq_1_3",
          .bus_a = Uid {1},
          .bus_b = Uid {3},
          .reactance = 0.05,
          .tmax_ba = 215.0,
          .tmax_ab = 215.0,
      },
  };
  return sys;
}

/// Write a Planning carrying only @p sys to a temp JSON file.
auto write_system_file(const System& sys, std::string_view tag)
    -> std::filesystem::path
{
  const auto path = std::filesystem::temp_directory_path()
      / std::format("gtopt_cascade_reduced_{}_{}.json", tag, ::getpid());
  Planning holder {
      .system = sys,
  };
  std::ofstream ofs(path);
  ofs << daw::json::to_json(holder);
  return path;
}

auto base_sddp_opts() -> SDDPOptions
{
  SDDPOptions opts;
  opts.max_iterations = 40;
  opts.convergence_tol = 1e-5;
  opts.apertures = std::vector<Uid> {};
  return opts;
}

auto level_method(int max_iters) -> CascadeLevelMethod
{
  return CascadeLevelMethod {
      .max_iterations = OptInt {max_iters},
      .apertures = Array<Uid> {},
      .convergence_tol = OptReal {1e-5},
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "Cascade reduced L0 (2-node) -> full L1 reaches the full-network optimum")
{
  // ── Reference: single-level SDDP on the full 4-bus network ──
  double ref_obj = 0.0;
  {
    PlanningLP ref_lp(make_4bus_hydro_planning());
    CascadeOptions cascade;
    cascade.level_array = {
        CascadeLevel {
            .name = OptName {"full_only"},
            .sddp_options = level_method(30),
        },
    };
    CascadePlanningMethod ref(base_sddp_opts(), std::move(cascade));
    const SolverOptions lp_opts;
    REQUIRE(ref.solve(ref_lp, lp_opts).has_value());
    REQUIRE(ref.level_stats().size() == 1);
    REQUIRE(ref.level_stats()[0].converged);
    ref_obj = ref.level_stats()[0].upper_bound;
  }

  const auto reduced_path =
      write_system_file(make_4bus_two_node_system(), "2node");

  PlanningLP planning_lp(make_4bus_hydro_planning());
  CascadeOptions cascade;
  cascade.level_array = {
      CascadeLevel {
          .name = OptName {"L0_2node"},
          .model_options =
              ModelOptions {
                  .use_kirchhoff = OptBool {false},
              },
          .system_file = OptName {reduced_path.string()},
          .sddp_options = level_method(30),
      },
      CascadeLevel {
          .name = OptName {"L1_full"},
          .model_options =
              ModelOptions {
                  .use_kirchhoff = OptBool {true},
              },
          .sddp_options = level_method(30),
          .transition =
              CascadeTransition {
                  .inherit_optimality_cuts = OptInt {-1},
              },
      },
  };

  CascadePlanningMethod solver(base_sddp_opts(), std::move(cascade));
  const SolverOptions lp_opts;
  REQUIRE(solver.solve(planning_lp, lp_opts).has_value());
  REQUIRE(solver.level_stats().size() == 2);
  const auto& s0 = solver.level_stats()[0];
  const auto& s1 = solver.level_stats()[1];

  // Reduced level built cuts and is a relaxation of the full network.
  CHECK(s0.cuts_added > 0);
  CHECK(s0.converged);
  CHECK(s0.lower_bound <= (ref_obj * (1.0 + 1e-6)) + 1e-6);

  // Full level, seeded with the reduced cuts, lands on the full optimum.
  CHECK(s1.converged);
  CHECK(s1.upper_bound == doctest::Approx(ref_obj).epsilon(1e-4));
  CHECK(s1.lower_bound == doctest::Approx(ref_obj).epsilon(1e-4));
  // The full network is genuinely more expensive than its 2-node
  // relaxation here (the 15 MW lines out of bus 4 bind) —
  // otherwise this test would not exercise a real topology change.
  CHECK(ref_obj > s0.upper_bound * (1.0 + 1e-6));

  std::error_code ec;
  std::filesystem::remove(reduced_path, ec);
}

TEST_CASE("Cascade write_level_outputs: one output set per level")  // NOLINT
{
  const auto out_dir = std::filesystem::temp_directory_path()
      / std::format("gtopt_cascade_level_out_{}", ::getpid());
  std::filesystem::remove_all(out_dir);

  auto planning = make_4bus_hydro_planning();
  planning.options.output_directory = out_dir.string();
  PlanningLP planning_lp(std::move(planning));

  const auto reduced_path =
      write_system_file(make_4bus_two_node_system(), "2node_out");

  auto sddp_opts = base_sddp_opts();
  sddp_opts.cuts_output_file =
      (out_dir / "cuts" / "sddp_cuts.parquet").string();
  sddp_opts.enable_api = true;
  sddp_opts.api_status_file = (out_dir / "solver_status.json").string();

  CascadeOptions cascade;
  cascade.write_level_outputs = OptBool {true};
  cascade.level_array = {
      CascadeLevel {
          .name = OptName {"L0_2node"},
          .model_options =
              ModelOptions {
                  .use_kirchhoff = OptBool {false},
              },
          .system_file = OptName {reduced_path.string()},
          .sddp_options = level_method(10),
      },
      CascadeLevel {
          .name = OptName {"L1_full"},
          .sddp_options = level_method(10),
          .transition =
              CascadeTransition {
                  .inherit_optimality_cuts = OptInt {-1},
              },
      },
  };

  CascadePlanningMethod solver(std::move(sddp_opts), std::move(cascade));
  const SolverOptions lp_opts;
  REQUIRE(solver.solve(planning_lp, lp_opts).has_value());
  REQUIRE(solver.level_stats().size() == 2);
  planning_lp.write_out();

  const auto l0 = out_dir / "L0_2node";
  CHECK(std::filesystem::exists(l0 / "solution.csv"));
  CHECK(std::filesystem::exists(l0 / "level_summary.json"));
  CHECK(std::filesystem::exists(l0 / "solver_status.json"));
  CHECK(std::filesystem::is_directory(l0 / "Generator"));
  CHECK(std::filesystem::is_directory(l0 / "cuts"));
  CHECK_FALSE(std::filesystem::is_empty(l0 / "cuts"));
  // The reduced level's line output carries the aggregated corridor.
  CHECK(std::filesystem::is_directory(l0 / "Line"));

  // Final level writes to the base directory, as without the option.
  CHECK(std::filesystem::exists(out_dir / "solution.csv"));
  CHECK(std::filesystem::is_directory(out_dir / "Generator"));
  CHECK_FALSE(std::filesystem::exists(out_dir / "L1_full" / "solution.csv"));
  CHECK(planning_lp.options().output_directory() == out_dir.string());

  // Cascade-wide summary: header + one row per level.
  std::ifstream csv(out_dir / "cascade_levels.csv");
  REQUIRE(csv.is_open());
  int n_lines = 0;
  for (std::string row; std::getline(csv, row);) {
    ++n_lines;
  }
  CHECK(n_lines == 3);

  std::error_code ec;
  std::filesystem::remove(reduced_path, ec);
  std::filesystem::remove_all(out_dir, ec);
}
