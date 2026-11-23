// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_generator_profile_formulation.cpp
 * @brief     Slack-free GeneratorProfile row and generation-range clamping
 *
 * A generator profile is a per-unit factor `p` of the capacity `C`.  The
 * row is `gen ≤ A`, `A = min(C·p, pmax)` (no spillover column) unless the
 * profile prices spillover (`scost ≠ 0`), which keeps
 * `gen + spill = A`, `0 ≤ spill ≤ A`.  Spillover is reported as `A − gen`
 * in both forms.
 */
#include <algorithm>
#include <array>
#include <filesystem>
#include <format>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/capacity_profile_lp.hpp>
#include <gtopt/demand_profile_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/generator_profile_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

#include "test_csv_helpers.hpp"

using namespace gtopt;

namespace
{

struct ProfileCase
{
  double capacity {200.0};
  OptTBRealFieldSched pmax {};
  double profile {0.5};
  OptTRealFieldSched scost {};
  double demand {80.0};
};

[[nodiscard]] System make_system(const ProfileCase& pc)
{
  return System {
      .name = "ProfileFormulation",
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
                  .capacity = pc.demand,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "solar",
                  .bus = Uid {1},
                  .pmax = pc.pmax,
                  .gcost = 0.0,
                  .capacity = pc.capacity,
              },
              {
                  .uid = Uid {2},
                  .name = "backup",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 1000.0,
              },
          },
      .generator_profile_array =
          {
              {
                  .uid = Uid {1},
                  .name = "solar_profile",
                  .generator = Uid {1},
                  .profile = pc.profile,
                  .scost = pc.scost,
              },
          },
  };
}

[[nodiscard]] Simulation make_sim()
{
  return Simulation {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1,
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

[[nodiscard]] PlanningOptions base_opts()
{
  PlanningOptions opts;
  opts.model_options.use_single_bus = true;
  opts.model_options.scale_objective = 1.0;
  opts.model_options.demand_fail_cost = 10000.0;
  return opts;
}

/// Profile LP element of the (single) generator profile, whichever
/// collection (legacy or unified) it ended up in.
template<typename Fn>
void with_profile_lp(const SystemLP& system_lp, const Fn& fn)
{
  const auto& legacy = system_lp.elements<GeneratorProfileLP>();
  if (!legacy.empty()) {
    fn(legacy.front());
    return;
  }
  const auto& unified = system_lp.elements<CapacityProfileLP>();
  REQUIRE(!unified.empty());
  fn(unified.front());
}

struct Solved
{
  double obj {};
  int rows {};
  int cols {};
  double row_upp {};
  double row_dual {};
  double gen {};
  bool has_slack {};
  double slack_upp {};
  double slack_sol {};
};

[[nodiscard]] Solved solve(const ProfileCase& pc)
{
  const PlanningOptionsLP options {base_opts()};
  const auto sim = make_sim();
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(make_system(pc), simulation_lp);

  auto&& lp = system_lp.linear_interface();
  REQUIRE(lp.resolve().has_value());

  Solved s {
      .obj = lp.get_obj_value(),
      .rows = static_cast<int>(lp.get_numrows()),
      .cols = static_cast<int>(lp.get_numcols()),
  };

  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};

  with_profile_lp(system_lp,
                  [&](const auto& plp)
                  {
                    const auto& rows = plp.profile_rows().at(st_key);
                    const auto row = rows.at(buid);
                    s.row_upp = lp.get_row_upp()[row];
                    s.row_dual = lp.get_row_dual()[row];
                    const auto& scols = plp.profile_slack_cols();
                    const auto it = scols.find(st_key);
                    s.has_slack = it != scols.end() && !it->second.empty();
                    if (s.has_slack) {
                      const auto scol = it->second.at(buid);
                      s.slack_upp = lp.get_col_upp()[scol];
                      s.slack_sol = lp.get_col_sol()[scol];
                    }
                  });

  const auto& gens = system_lp.elements<GeneratorLP>();
  for (const auto& g : gens) {
    if (g.generator().uid == Uid {1}) {
      const auto& gcols = g.generation_cols_at(scenario, stage);
      s.gen = lp.get_col_sol()[gcols.at(buid)];
    }
  }
  return s;
}

/// Solve through `PlanningLP`, write CSV output and return the value of
/// `GeneratorProfile/spillover_sol` (uid 1, single block).
[[nodiscard]] double spillover_from_output(const ProfileCase& pc,
                                           std::string_view tag)
{
  const auto dir = std::filesystem::temp_directory_path()
      / std::format("gtopt_test_profile_form_{}", tag);
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);

  Planning planning {
      .options = base_opts(),
      .simulation = make_sim(),
      .system = make_system(pc),
  };
  planning.options.output_directory = dir.string();
  planning.options.output_format = DataFormat::csv;
  planning.options.output_compression = CompressionCodec::uncompressed;

  PlanningLP planning_lp(std::move(planning));
  REQUIRE(planning_lp.resolve().has_value());
  planning_lp.write_out();

  const auto values = gtopt::test_helpers::read_uid_values_long(
      dir / "GeneratorProfile" / "spillover_sol", 1);
  std::filesystem::remove_all(dir);
  REQUIRE(values.size() == 1);
  return values.front();
}

}  // namespace

TEST_CASE(  // NOLINT
    "GeneratorProfile slack-free form drops the spillover column")
{
  // Same case with a (tiny) spillover cost keeps the slack column.
  const auto free = solve({.profile = 0.5, .demand = 80.0});
  const auto slack = solve({.profile = 0.5, .scost = 1e-9, .demand = 80.0});

  CHECK_FALSE(free.has_slack);
  CHECK(slack.has_slack);
  CHECK(free.cols + 1 == slack.cols);
  CHECK(free.rows == slack.rows);
  // The slack form pays 1e-9 $/MWh on 20 MW of spillover.
  CHECK(free.obj == doctest::Approx(slack.obj - (20.0 * 1e-9)).scale(1.0));
  CHECK(free.gen == doctest::Approx(80.0));
  CHECK(free.row_upp == doctest::Approx(100.0));
  CHECK(slack.slack_upp == doctest::Approx(100.0));
  CHECK(slack.slack_sol == doctest::Approx(20.0));
}

TEST_CASE(  // NOLINT
    "GeneratorProfile slack-free dual matches the equality form when binding")
{
  // Demand 150 > available 0.5 × 200 = 100: the profile row binds and
  // the backup (100 $/MWh) sets the price.  One more MW of availability
  // saves 100 $ in both forms.
  const auto free = solve({.profile = 0.5, .demand = 150.0});
  const auto slack = solve({.profile = 0.5, .scost = 1e-9, .demand = 150.0});

  CHECK(free.gen == doctest::Approx(100.0));
  CHECK(slack.slack_sol == doctest::Approx(0.0));
  CHECK(free.row_dual == doctest::Approx(slack.row_dual).epsilon(1e-6));
  CHECK(std::abs(free.row_dual) == doctest::Approx(100.0).epsilon(1e-6));
}

TEST_CASE(  // NOLINT
    "GeneratorProfile in MW (> 1) keeps spillover within the generation range")
{
  // FINIS_TERRAE-like: capacity 281.8 MW, profile written in MW (63.6).
  // capacity × profile = 17 922 MW; the row RHS is capped at pmax = 281.8.
  constexpr double cap = 281.8;
  constexpr double demand = 30.0;

  SUBCASE("slack-free")
  {
    const auto s = solve({.capacity = cap, .profile = 63.6, .demand = demand});
    CHECK(s.row_upp == doctest::Approx(cap));
    CHECK(s.gen == doctest::Approx(demand));
    const auto spill = spillover_from_output(
        {.capacity = cap, .profile = 63.6, .demand = demand}, "free");
    CHECK(spill == doctest::Approx(cap - demand));
    CHECK(spill <= cap);
  }

  SUBCASE("slack form (scost > 0)")
  {
    const ProfileCase pc {
        .capacity = cap,
        .profile = 63.6,
        .scost = 1e-6,
        .demand = demand,
    };
    const auto s = solve(pc);
    CHECK(s.row_upp == doctest::Approx(cap));
    CHECK(s.slack_upp == doctest::Approx(cap));
    CHECK(s.slack_sol == doctest::Approx(cap - demand));
    const auto spill = spillover_from_output(pc, "slack");
    CHECK(spill == doctest::Approx(cap - demand));
  }

  SUBCASE("block pmax below capacity × profile")
  {
    // pmax (e.g. maintenance) 100 < 0.9 × 281.8: available = 100.
    const ProfileCase pc {
        .capacity = cap,
        .pmax = 100.0,
        .profile = 0.9,
        .demand = demand,
    };
    const auto s = solve(pc);
    CHECK(s.row_upp == doctest::Approx(100.0));
    const auto spill = spillover_from_output(pc, "pmax");
    CHECK(spill == doctest::Approx(100.0 - demand));
  }
}

TEST_CASE(  // NOLINT
    "GeneratorProfile with expansion clips the profile coefficient at 1")
{
  // Expandable generator (capacity column): profile 5 (> 1) would make
  // `gen − 5·C ≤ 0`; the coefficient is clipped to 1 so gen ≤ C.
  System sys {
      .name = "ProfileExpansion",
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
                  .capacity = 50.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "solar",
                  .bus = Uid {1},
                  .gcost = 0.0,
                  .capacity = 0.0,
                  .expcap = 10.0,
                  .expmod = 10.0,
                  .annual_capcost = 1.0,
              },
              {
                  .uid = Uid {2},
                  .name = "backup",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 1000.0,
              },
          },
      .generator_profile_array =
          {
              {
                  .uid = Uid {1},
                  .name = "solar_profile",
                  .generator = Uid {1},
                  .profile = 5.0,
              },
          },
  };

  const PlanningOptionsLP options {base_opts()};
  const auto sim = make_sim();
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(sys, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  REQUIRE(lp.resolve().has_value());

  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};

  double gen = 0.0;
  for (const auto& g : system_lp.elements<GeneratorLP>()) {
    if (g.generator().uid == Uid {1}) {
      gen = lp.get_col_sol()[g.generation_cols_at(scenario, stage).at(buid)];
    }
  }
  // Serving 50 MW of demand requires 50 MW of built capacity: with the
  // profile clipped to 1 the LP cannot generate 5× its capacity.
  CHECK(gen == doctest::Approx(50.0));

  with_profile_lp(system_lp,
                  [&](const auto& plp)
                  {
                    const auto& scols = plp.profile_slack_cols();
                    const auto it = scols.find(st_key);
                    CHECK((it == scols.end() || it->second.empty()));
                    const auto row = plp.profile_rows().at(st_key).at(buid);
                    CHECK(lp.get_row_upp()[row] == doctest::Approx(0.0));
                  });
}

namespace
{

/// Build `sys` on a 1-block simulation and hand the pieces to `fn`.
template<typename Fn>
void with_built(const System& sys, const Fn& fn)
{
  const PlanningOptionsLP options {base_opts()};
  const auto sim = make_sim();
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(sys, simulation_lp);
  fn(simulation_lp, system_lp);
}

/// Generation of generator uid 1 in the single block.
[[nodiscard]] double solar_gen(const SimulationLP& simulation_lp,
                               const SystemLP& system_lp)
{
  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto sol = system_lp.linear_interface().get_col_sol();
  for (const auto& g : system_lp.elements<GeneratorLP>()) {
    if (g.generator().uid == Uid {1}) {
      return sol[g.generation_cols_at(scenario, stage).at(buid)];
    }
  }
  return -1.0;
}

[[nodiscard]] System expansion_system(OptTRealFieldSched scost)
{
  auto sys = make_system({.demand = 50.0});
  auto& solar = sys.generator_array.front();
  solar.capacity = 0.0;
  solar.expcap = 10.0;
  solar.expmod = 10.0;
  solar.annual_capcost = 1.0;
  sys.generator_profile_array.front().scost = std::move(scost);
  return sys;
}

}  // namespace

TEST_CASE(  // NOLINT
    "GeneratorProfile update_aperture rewrites the row in every form")
{
  const auto no_value = [](StageUid, BlockUid) -> std::optional<double>
  { return std::nullopt; };

  SUBCASE("slack-free, fixed capacity: RHS = min(C·p, pmax), low stays -inf")
  {
    with_built(
        make_system({.profile = 0.5, .demand = 150.0}),
        [&](const SimulationLP& simulation_lp, SystemLP& system_lp)
        {
          auto&& lp = system_lp.linear_interface();
          const auto& scenario = simulation_lp.scenarios().front();
          const auto& stage = simulation_lp.stages().front();
          const auto buid = stage.blocks().front().uid();
          with_profile_lp(
              system_lp,
              [&](const auto& plp)
              {
                const auto row =
                    plp.profile_rows()
                        .at(std::tuple {scenario.uid(), stage.uid()})
                        .at(buid);

                // No value for the aperture: the forward-pass row stays.
                CHECK(plp.update_aperture(lp, scenario, no_value, stage));
                CHECK(lp.get_row_upp()[row] == doctest::Approx(100.0));

                CHECK(plp.update_aperture(
                    lp,
                    scenario,
                    [](StageUid, BlockUid) -> std::optional<double>
                    { return 0.25; },
                    stage));
                CHECK(lp.get_row_upp()[row] == doctest::Approx(50.0));
                // `x ≤ A` form: the row has no lower bound.
                CHECK(lp.get_row_low()[row] <= -1e20);
                REQUIRE(lp.resolve().has_value());
                CHECK(solar_gen(simulation_lp, system_lp)
                      == doctest::Approx(50.0));

                // A profile written in MW is capped at pmax (= capacity).
                CHECK(plp.update_aperture(
                    lp,
                    scenario,
                    [](StageUid, BlockUid) -> std::optional<double>
                    { return 3.0; },
                    stage));
                CHECK(lp.get_row_upp()[row] == doctest::Approx(200.0));
              });
        });
  }

  SUBCASE("slack form, fixed capacity: equality RHS and slack bound move")
  {
    with_built(make_system({.profile = 0.5, .scost = 1e-9, .demand = 150.0}),
               [&](const SimulationLP& simulation_lp, SystemLP& system_lp)
               {
                 auto&& lp = system_lp.linear_interface();
                 const auto& scenario = simulation_lp.scenarios().front();
                 const auto& stage = simulation_lp.stages().front();
                 const auto buid = stage.blocks().front().uid();
                 const auto st_key = std::tuple {scenario.uid(), stage.uid()};
                 with_profile_lp(
                     system_lp,
                     [&](const auto& plp)
                     {
                       const auto row = plp.profile_rows().at(st_key).at(buid);
                       const auto scol =
                           plp.profile_slack_cols().at(st_key).at(buid);
                       CHECK(plp.update_aperture(
                           lp,
                           scenario,
                           [](StageUid, BlockUid) -> std::optional<double>
                           { return 0.25; },
                           stage));
                       CHECK(lp.get_row_low()[row] == doctest::Approx(50.0));
                       CHECK(lp.get_row_upp()[row] == doctest::Approx(50.0));
                       CHECK(lp.get_col_upp()[scol] == doctest::Approx(50.0));
                       REQUIRE(lp.resolve().has_value());
                       CHECK(solar_gen(simulation_lp, system_lp)
                             == doctest::Approx(50.0));
                     });
               });
  }

  SUBCASE("capacity column, slack-free: coefficient clipped at 1")
  {
    with_built(
        expansion_system({}),
        [&](const SimulationLP& simulation_lp, SystemLP& system_lp)
        {
          auto&& lp = system_lp.linear_interface();
          const auto& scenario = simulation_lp.scenarios().front();
          const auto& stage = simulation_lp.stages().front();
          const auto buid = stage.blocks().front().uid();
          const auto& solar = system_lp.elements<GeneratorLP>().front();
          const auto cap_col = solar.capacity_col_at(stage);
          REQUIRE(cap_col.has_value());
          with_profile_lp(
              system_lp,
              [&](const auto& plp)
              {
                const auto row =
                    plp.profile_rows()
                        .at(std::tuple {scenario.uid(), stage.uid()})
                        .at(buid);
                CHECK(plp.update_aperture(
                    lp,
                    scenario,
                    [](StageUid, BlockUid) -> std::optional<double>
                    { return 0.4; },
                    stage));
                CHECK(lp.get_coeff(row, *cap_col) == doctest::Approx(-0.4));
                CHECK(plp.update_aperture(
                    lp,
                    scenario,
                    [](StageUid, BlockUid) -> std::optional<double>
                    { return 5.0; },
                    stage));
                CHECK(lp.get_coeff(row, *cap_col) == doctest::Approx(-1.0));
              });
        });
  }

  SUBCASE("capacity column, slack form: slack capped at p·max capacity")
  {
    with_built(
        expansion_system(OptTRealFieldSched {1e-6}),
        [&](const SimulationLP& simulation_lp, SystemLP& system_lp)
        {
          auto&& lp = system_lp.linear_interface();
          const auto& scenario = simulation_lp.scenarios().front();
          const auto& stage = simulation_lp.stages().front();
          const auto buid = stage.blocks().front().uid();
          const auto st_key = std::tuple {scenario.uid(), stage.uid()};
          const auto& solar = system_lp.elements<GeneratorLP>().front();
          const auto cap_col = solar.capacity_col_at(stage);
          REQUIRE(cap_col.has_value());
          const double max_cap = lp.get_col_upp()[*cap_col];
          REQUIRE(max_cap < LinearProblem::DblMax);
          with_profile_lp(
              system_lp,
              [&](const auto& plp)
              {
                const auto row = plp.profile_rows().at(st_key).at(buid);
                const auto scol = plp.profile_slack_cols().at(st_key).at(buid);
                // Build: profile 0.5 (make_system default) → p·C̄.
                CHECK(lp.get_col_upp()[scol] == doctest::Approx(0.5 * max_cap));
                CHECK(plp.update_aperture(
                    lp,
                    scenario,
                    [](StageUid, BlockUid) -> std::optional<double>
                    { return 0.3; },
                    stage));
                CHECK(lp.get_coeff(row, *cap_col) == doctest::Approx(-0.3));
                CHECK(lp.get_col_upp()[scol] == doctest::Approx(0.3 * max_cap));
                // Even fully built, the solar unit can only deliver
                // 0.3 · C̄; the backup covers the rest of the 50 MW.
                REQUIRE(lp.resolve().has_value());
                CHECK(solar_gen(simulation_lp, system_lp)
                      == doctest::Approx(std::min(50.0, 0.3 * max_cap)));
              });
        });
  }
}

TEST_CASE(  // NOLINT
    "GeneratorProfile mixed slack / slack-free stages write one spillover")
{
  // scost = 0 in stage 1 (slack-free row, spillover rebuilt as A − gen)
  // and > 0 in stage 2 (slack column read back): both stages must report
  // the same 0.5 × 200 − 80 = 20 MW of spillover.
  const auto dir =
      std::filesystem::temp_directory_path() / "gtopt_test_profile_form_mixed";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);

  Simulation sim {
      .block_array =
          {
              {.uid = Uid {1}, .duration = 1},
              {.uid = Uid {2}, .duration = 1},
          },
      .stage_array =
          {
              {.uid = Uid {1}, .first_block = 0, .count_block = 1},
              {.uid = Uid {2}, .first_block = 1, .count_block = 1},
          },
      .scenario_array = {{.uid = Uid {0}}},
  };
  Planning planning {
      .options = base_opts(),
      .simulation = std::move(sim),
      .system = make_system({
          .profile = 0.5,
          .scost = std::vector<double> {0.0, 1e-6},
          .demand = 80.0,
      }),
  };
  planning.options.output_directory = dir.string();
  planning.options.output_format = DataFormat::csv;
  planning.options.output_compression = CompressionCodec::uncompressed;

  PlanningLP planning_lp(std::move(planning));
  REQUIRE(planning_lp.resolve().has_value());
  planning_lp.write_out();

  // Parse every `spillover_sol*.csv` shard: stage → value of uid 1.
  std::map<int, double> by_stage;
  for (const auto& entry :
       std::filesystem::directory_iterator(dir / "GeneratorProfile"))
  {
    const auto fname = entry.path().filename().string();
    if (!fname.starts_with("spillover_sol")) {
      continue;
    }
    std::ifstream in(entry.path());
    std::string line;
    std::getline(in, line);  // header: scenario,stage,block,uid,value
    while (std::getline(in, line)) {
      std::array<std::string, 5> f {};
      std::size_t k = 0;
      std::stringstream ss(line);
      while (k < f.size() && std::getline(ss, f.at(k), ',')) {
        ++k;
      }
      if (k == f.size() && std::stoi(f[3]) == 1) {
        by_stage[std::stoi(f[1])] = std::stod(f[4]);
      }
    }
  }
  std::filesystem::remove_all(dir);

  REQUIRE(by_stage.size() == 2);
  CHECK(by_stage.at(1) == doctest::Approx(20.0));
  CHECK(by_stage.at(2) == doctest::Approx(20.0));
}

TEST_CASE(  // NOLINT
    "CapacityProfile owner kind selects the profile row form")
{
  // Generator owner → generator mode (slack-free, clamped to pmax);
  // demand owner → slack form kept (unserved stays a priced column).
  const auto build = [](ProfileOwnerKind kind, double profile)
  {
    auto sys = make_system({.profile = 0.5, .demand = 80.0});
    sys.generator_profile_array.clear();
    sys.capacity_profile_array = {
        {
            .uid = Uid {1},
            .name = "cp",
            .owner_kind = kind,
            .owner = Uid {1},
            .profile = profile,
        },
    };
    return sys;
  };

  SUBCASE("generator owner: slack-free x ≤ min(C·p, pmax)")
  {
    with_built(build(ProfileOwnerKind::Generator, 3.0),
               [](const SimulationLP& simulation_lp, SystemLP& system_lp)
               {
                 auto&& lp = system_lp.linear_interface();
                 const auto& plp =
                     system_lp.elements<CapacityProfileLP>().front();
                 const auto& scenario = simulation_lp.scenarios().front();
                 const auto& stage = simulation_lp.stages().front();
                 const auto st_key = std::tuple {scenario.uid(), stage.uid()};
                 const auto buid = stage.blocks().front().uid();
                 const auto scols = plp.profile_slack_cols().find(st_key);
                 CHECK((scols == plp.profile_slack_cols().end()
                        || scols->second.empty()));
                 const auto row = plp.profile_rows().at(st_key).at(buid);
                 // 3 × 200 is capped at the 200 MW generation range.
                 CHECK(lp.get_row_upp()[row] == doctest::Approx(200.0));
               });
  }

  SUBCASE("demand owner: slack column kept, not clamped")
  {
    with_built(build(ProfileOwnerKind::Demand, 0.5),
               [](const SimulationLP& simulation_lp, SystemLP& system_lp)
               {
                 const auto& plp =
                     system_lp.elements<CapacityProfileLP>().front();
                 const auto& scenario = simulation_lp.scenarios().front();
                 const auto& stage = simulation_lp.stages().front();
                 const auto st_key = std::tuple {scenario.uid(), stage.uid()};
                 const auto scols = plp.profile_slack_cols().find(st_key);
                 REQUIRE(scols != plp.profile_slack_cols().end());
                 CHECK_FALSE(scols->second.empty());
               });
  }
}
