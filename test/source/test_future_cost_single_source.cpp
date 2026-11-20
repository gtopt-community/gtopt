// SPDX-License-Identifier: BSD-3-Clause
//
// FutureCost as the single source of the future-cost-function (FCF)
// boundary cuts, for every method.
//
//   * `resolve_future_cost_aliases` folds each deprecated key into the
//     FutureCost element only for its own method (sddp_options.* for
//     sddp/cascade, monolithic_options.* for monolithic, simulation.* for
//     any), warns old → new, errors on conflicting values, and is idempotent.
//   * MonolithicMethod loads the cuts from FutureCost and never from
//     `sddp_options`.
//   * SDDPMethod loads the cuts from FutureCost.
//   * `iteration_limit` keeps the cuts of the last N source iterations, in
//     both methods.

#include <filesystem>
#include <fstream>

#include <doctest/doctest.h>
#include <gtopt/future_cost.hpp>
#include <gtopt/future_cost_aliases.hpp>
#include <gtopt/future_cost_lp.hpp>
#include <gtopt/monolithic_method.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>

#include "log_capture.hpp"
#include "sddp_helpers.hpp"

using namespace gtopt;

namespace fcss
{

/// Three-iteration boundary-cut file for the 3-phase hydro fixture: one
/// state-independent cut `α ≥ rhs` per source iteration (1, 2, 3).
auto write_three_iteration_cuts(const std::string& path) -> void
{
  std::ofstream ofs(path);
  ofs << "iteration,scene,rhs,rsv1\n";
  ofs << "1,1,1000.0,0.0\n";
  ofs << "2,1,2000.0,0.0\n";
  ofs << "3,1,3000.0,0.0\n";
}

[[nodiscard]] auto temp_csv(std::string_view stem) -> std::string
{
  return (std::filesystem::temp_directory_path()
          / std::format("gtopt_test_fcss_{}.csv", stem))
      .string();
}

/// Number of cut rows on the last phase of scene 0 (the boundary cuts —
/// no runtime SDDP cut ever targets the last phase).
[[nodiscard]] auto last_phase_cut_count(const PlanningLP& planning_lp)
    -> std::size_t
{
  const auto last_phase = planning_lp.simulation().last_phase_index();
  return planning_lp.system(first_scene_index(), last_phase)
      .linear_interface()
      .active_cuts_size();
}

[[nodiscard]] auto make_fcf(const std::string& cuts_file) -> FutureCost
{
  return FutureCost {
      .uid = Uid {1},
      .name = "fcf",
      .cuts_file = cuts_file,
      .mean_shift = false,
      .mode = BoundaryCutsMode::combined,
  };
}

[[nodiscard]] auto run_monolithic(Planning planning) -> std::size_t
{
  PlanningLP planning_lp(std::move(planning));
  MonolithicMethod solver;
  auto result = solver.solve(planning_lp, {});
  REQUIRE(result.has_value());
  return last_phase_cut_count(planning_lp);
}

[[nodiscard]] auto run_sddp(Planning planning) -> std::size_t
{
  planning.options.method = MethodType::sddp;
  PlanningLP planning_lp(std::move(planning));
  SDDPOptions sddp_opts;
  sddp_opts.max_iterations = 1;
  sddp_opts.enable_api = false;
  SDDPMethod sddp(planning_lp, sddp_opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  return last_phase_cut_count(planning_lp);
}

}  // namespace fcss

using namespace fcss;

// ─── Alias translation ──────────────────────────────────────────────────────

TEST_CASE("resolve_future_cost_aliases — sddp_options aliases")  // NOLINT
{
  SUBCASE("method=sddp: every sddp_options key lands in a new element")
  {
    Planning planning;
    planning.options.method = MethodType::sddp;
    auto& so = planning.options.sddp_options;
    so.boundary_cuts_file = "bc.csv";
    so.boundary_cuts_mode = BoundaryCutsMode::combined;
    so.boundary_cut_sharing_mode = BoundaryCutSharingMode::multicut;
    so.boundary_cuts_mean_shift = false;
    so.boundary_max_iterations = 4;
    so.missing_cut_var_mode = MissingCutVarMode::skip_cut;

    const test::LogCapture logs;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());

    REQUIRE(planning.system.future_cost_array.size() == 1);
    const auto& fc = planning.system.future_cost_array.front();
    CHECK(fc.uid == Uid {1});
    CHECK(fc.name == "fcf");
    CHECK(fc.cuts_file.value_or("") == "bc.csv");
    CHECK((fc.mode && *fc.mode == BoundaryCutsMode::combined));
    CHECK((fc.sharing && *fc.sharing == BoundaryCutSharingMode::multicut));
    CHECK_FALSE(fc.mean_shift.value_or(true));
    CHECK(fc.iteration_limit.value_or(0) == 4);
    CHECK((fc.missing_cut_var_mode
           && *fc.missing_cut_var_mode == MissingCutVarMode::skip_cut));

    // Legacy keys are cleared (idempotent second call).
    CHECK_FALSE(so.boundary_cuts_file.has_value());
    CHECK_FALSE(so.boundary_max_iterations.has_value());
    CHECK(logs.contains("sddp_options.boundary_cuts_file"));
    CHECK(logs.contains("future_cost_array[uid=1].cuts_file"));
    CHECK(logs.contains("sddp_options.boundary_max_iterations"));
    CHECK(logs.contains("iteration_limit"));
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(planning.system.future_cost_array.size() == 1);
  }

  SUBCASE("method=cascade behaves like sddp")
  {
    Planning planning;
    planning.options.method = MethodType::cascade;
    planning.options.sddp_options.boundary_cuts_file = "bc.csv";
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    REQUIRE(planning.system.future_cost_array.size() == 1);
    CHECK(planning.system.future_cost_array.front().cuts_file.value_or("")
          == "bc.csv");
  }

  SUBCASE("method=monolithic: sddp_options keys are ignored with a warning")
  {
    Planning planning;
    planning.options.method = MethodType::monolithic;
    planning.options.sddp_options.boundary_cuts_file = "bc.csv";
    planning.options.sddp_options.boundary_cuts_mean_shift = false;

    const test::LogCapture logs;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(planning.system.future_cost_array.empty());
    CHECK_FALSE(planning.options.sddp_options.boundary_cuts_file.has_value());
    CHECK(logs.contains("is ignored under method=monolithic"));
  }

  SUBCASE("unset method defaults to monolithic")
  {
    Planning planning;
    planning.options.sddp_options.boundary_cuts_file = "bc.csv";
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(planning.system.future_cost_array.empty());
  }
}

TEST_CASE("resolve_future_cost_aliases — monolithic_options aliases")  // NOLINT
{
  SUBCASE("method=monolithic: keys land in the existing element")
  {
    Planning planning;
    planning.options.method = MethodType::monolithic;
    planning.system.future_cost_array = {
        FutureCost {.uid = Uid {7}, .name = "my_fcf", .scale_alpha = 1e5},
    };
    auto& mo = planning.options.monolithic_options;
    mo.boundary_cuts_file = "mono.csv";
    mo.boundary_cuts_mode = BoundaryCutsMode::separated;
    mo.boundary_cut_sharing_mode = BoundaryCutSharingMode::shared;
    mo.boundary_max_iterations = 2;

    const test::LogCapture logs;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    REQUIRE(planning.system.future_cost_array.size() == 1);
    const auto& fc = planning.system.future_cost_array.front();
    CHECK(fc.uid == Uid {7});
    CHECK(fc.scale_alpha.value_or(0.0) == doctest::Approx(1e5));
    CHECK(fc.cuts_file.value_or("") == "mono.csv");
    CHECK((fc.mode && *fc.mode == BoundaryCutsMode::separated));
    CHECK((fc.sharing && *fc.sharing == BoundaryCutSharingMode::shared));
    CHECK(fc.iteration_limit.value_or(0) == 2);
    CHECK(logs.contains("monolithic_options.boundary_cuts_file"));
    CHECK(logs.contains("monolithic_options.boundary_cuts_mode"));
    CHECK(logs.contains("monolithic_options.boundary_cut_sharing_mode"));
    CHECK(logs.contains("monolithic_options.boundary_max_iterations"));
  }

  SUBCASE("method=sddp: monolithic_options keys are ignored")
  {
    Planning planning;
    planning.options.method = MethodType::sddp;
    planning.options.monolithic_options.boundary_cuts_file = "mono.csv";
    const test::LogCapture logs;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(planning.system.future_cost_array.empty());
    CHECK(logs.contains("is ignored under method=sddp"));
  }
}

TEST_CASE("resolve_future_cost_aliases — simulation aliases")  // NOLINT
{
  for (const auto method : {MethodType::monolithic, MethodType::sddp}) {
    CAPTURE(enum_name(method));
    Planning planning;
    planning.options.method = method;
    planning.simulation.boundary_cuts_file = "sim.csv";
    planning.simulation.boundary_cuts_valuation =
        BoundaryCutsValuation::present_value;
    const test::LogCapture logs;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    REQUIRE(planning.system.future_cost_array.size() == 1);
    const auto& fc = planning.system.future_cost_array.front();
    CHECK(fc.cuts_file.value_or("") == "sim.csv");
    CHECK((fc.valuation
           && *fc.valuation == BoundaryCutsValuation::present_value));
    CHECK_FALSE(planning.simulation.boundary_cuts_file.has_value());
    CHECK(logs.contains("simulation.boundary_cuts_file"));
    CHECK(logs.contains("simulation.boundary_cuts_valuation"));
  }
}

TEST_CASE("resolve_future_cost_aliases — conflicts")  // NOLINT
{
  SUBCASE("element vs alias with different values is an error")
  {
    Planning planning;
    planning.options.method = MethodType::sddp;
    planning.system.future_cost_array = {
        FutureCost {.uid = Uid {1}, .name = "fcf", .cuts_file = "a.csv"},
    };
    planning.options.sddp_options.boundary_cuts_file = "b.csv";
    const auto r = resolve_future_cost_aliases(planning);
    REQUIRE_FALSE(r.has_value());
    CHECK(r.error().code == ErrorCode::InvalidInput);
    CHECK(r.error().message.find("cuts_file") != std::string::npos);
    CHECK(r.error().message.find("sddp_options.boundary_cuts_file")
          != std::string::npos);
  }

  SUBCASE("two aliases with different values is an error")
  {
    Planning planning;
    planning.options.method = MethodType::monolithic;
    planning.simulation.boundary_cuts_file = "a.csv";
    planning.options.monolithic_options.boundary_cuts_file = "b.csv";
    const auto r = resolve_future_cost_aliases(planning);
    REQUIRE_FALSE(r.has_value());
    CHECK(r.error().message.find("simulation.boundary_cuts_file")
          != std::string::npos);
    CHECK(r.error().message.find("monolithic_options.boundary_cuts_file")
          != std::string::npos);
  }

  SUBCASE("conflicting iteration limit is an error")
  {
    Planning planning;
    planning.options.method = MethodType::sddp;
    planning.system.future_cost_array = {
        FutureCost {.uid = Uid {1}, .name = "fcf", .iteration_limit = 3},
    };
    planning.options.sddp_options.boundary_max_iterations = 5;
    CHECK_FALSE(resolve_future_cost_aliases(planning).has_value());
  }

  SUBCASE("equal values are accepted")
  {
    Planning planning;
    planning.options.method = MethodType::sddp;
    planning.system.future_cost_array = {
        FutureCost {.uid = Uid {1}, .name = "fcf", .cuts_file = "a.csv"},
    };
    planning.options.sddp_options.boundary_cuts_file = "a.csv";
    planning.simulation.boundary_cuts_file = "a.csv";
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(planning.system.future_cost_array.front().cuts_file.value_or("")
          == "a.csv");
  }

  SUBCASE("an inactive element is skipped; a new one is created")
  {
    Planning planning;
    planning.options.method = MethodType::sddp;
    planning.system.future_cost_array = {
        FutureCost {
            .uid = Uid {1}, .name = "off", .active = Active {IntBool {False}}},
    };
    planning.options.sddp_options.boundary_cuts_file = "a.csv";
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    REQUIRE(planning.system.future_cost_array.size() == 2);
    CHECK(planning.system.future_cost_array.back().uid == Uid {2});
    CHECK(planning.system.future_cost_array.back().cuts_file.value_or("")
          == "a.csv");
  }
}

// ─── apply_future_cost_config ───────────────────────────────────────────────

TEST_CASE("apply_future_cost_config maps every FCF field")  // NOLINT
{
  SUBCASE("explicit fields override")
  {
    const FutureCost fc {
        .uid = Uid {1},
        .name = "fcf",
        .cuts_file = "x.csv",
        .scale_alpha = 1e4,
        .mean_shift = false,
        .sharing = BoundaryCutSharingMode::multicut,
        .mode = BoundaryCutsMode::combined,
        .iteration_limit = 6,
        .missing_cut_var_mode = MissingCutVarMode::skip_cut,
        .valuation = BoundaryCutsValuation::present_value,
    };
    SDDPOptions o;
    apply_future_cost_config(fc, o);
    CHECK(o.boundary_cuts_file == "x.csv");
    CHECK(o.scale_alpha == doctest::Approx(1e4));
    CHECK_FALSE(o.boundary_cuts_mean_shift);
    CHECK(o.boundary_cut_sharing == BoundaryCutSharingMode::multicut);
    CHECK(o.boundary_cuts_mode == BoundaryCutsMode::combined);
    CHECK(o.boundary_max_iterations == 6);
    CHECK(o.missing_cut_var_mode == MissingCutVarMode::skip_cut);
    CHECK(o.boundary_cuts_valuation == BoundaryCutsValuation::present_value);
  }

  SUBCASE("sharing derives from mode when unset")
  {
    SDDPOptions o;
    apply_future_cost_config(
        FutureCost {
            .uid = Uid {1},
            .name = "fcf",
            .mode = BoundaryCutsMode::combined,
        },
        o);
    CHECK(o.boundary_cut_sharing == BoundaryCutSharingMode::shared);
    apply_future_cost_config(
        FutureCost {
            .uid = Uid {1},
            .name = "fcf",
            .mode = BoundaryCutsMode::separated,
        },
        o);
    CHECK(o.boundary_cut_sharing == BoundaryCutSharingMode::per_scene);
  }

  SUBCASE("unset fields leave the target untouched")
  {
    SDDPOptions o;
    o.boundary_cuts_file = "keep.csv";
    o.boundary_max_iterations = 9;
    apply_future_cost_config(FutureCost {.uid = Uid {1}, .name = "fcf"}, o);
    CHECK(o.boundary_cuts_file == "keep.csv");
    CHECK(o.boundary_max_iterations == 9);
  }
}

// ─── Methods read only FutureCost ───────────────────────────────────────────

TEST_CASE(  // NOLINT
    "MonolithicMethod loads boundary cuts from FutureCost only")
{
  const auto cuts_file = temp_csv("mono_source");
  write_three_iteration_cuts(cuts_file);

  SUBCASE("FutureCost.cuts_file is loaded")
  {
    auto planning = make_3phase_hydro_planning();
    planning.system.future_cost_array = {make_fcf(cuts_file)};
    CHECK(run_monolithic(std::move(planning)) == 3);
  }

  SUBCASE("sddp_options.boundary_cuts_file is ignored by monolithic")
  {
    auto planning = make_3phase_hydro_planning();
    planning.options.method = MethodType::monolithic;
    planning.options.sddp_options.boundary_cuts_file = cuts_file;
    planning.options.sddp_options.boundary_cuts_mode =
        BoundaryCutsMode::combined;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(run_monolithic(std::move(planning)) == 0);
  }

  SUBCASE("monolithic_options alias still loads under monolithic")
  {
    auto planning = make_3phase_hydro_planning();
    planning.options.method = MethodType::monolithic;
    planning.options.monolithic_options.boundary_cuts_file = cuts_file;
    planning.options.monolithic_options.boundary_cuts_mode =
        BoundaryCutsMode::combined;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(run_monolithic(std::move(planning)) == 3);
  }

  SUBCASE("use_user_alpha with cuts_file is rejected")
  {
    auto planning = make_3phase_hydro_planning();
    auto fc = make_fcf(cuts_file);
    fc.use_user_alpha = true;
    planning.system.future_cost_array = {fc};
    PlanningLP planning_lp(std::move(planning));
    MonolithicMethod solver;
    const auto result = solver.solve(planning_lp, {});
    REQUIRE_FALSE(result.has_value());
    CHECK(result.error().message.find("mutually exclusive")
          != std::string::npos);
  }

  std::filesystem::remove(cuts_file);
}

TEST_CASE("SDDPMethod loads boundary cuts from FutureCost")  // NOLINT
{
  const auto cuts_file = temp_csv("sddp_source");
  write_three_iteration_cuts(cuts_file);

  SUBCASE("FutureCost.cuts_file is loaded")
  {
    auto planning = make_3phase_hydro_planning();
    planning.system.future_cost_array = {make_fcf(cuts_file)};
    CHECK(run_sddp(std::move(planning)) == 3);
  }

  SUBCASE("sddp_options alias still loads under sddp")
  {
    auto planning = make_3phase_hydro_planning();
    planning.options.method = MethodType::sddp;
    planning.options.sddp_options.boundary_cuts_file = cuts_file;
    planning.options.sddp_options.boundary_cuts_mode =
        BoundaryCutsMode::combined;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(run_sddp(std::move(planning)) == 3);
  }

  SUBCASE("monolithic_options alias is ignored under sddp")
  {
    auto planning = make_3phase_hydro_planning();
    planning.options.method = MethodType::sddp;
    planning.options.monolithic_options.boundary_cuts_file = cuts_file;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(run_sddp(std::move(planning)) == 0);
  }

  std::filesystem::remove(cuts_file);
}

TEST_CASE("FutureCost.iteration_limit filters in both methods")  // NOLINT
{
  const auto cuts_file = temp_csv("iteration_limit");
  write_three_iteration_cuts(cuts_file);

  for (const int limit : {0, 1, 2, 5}) {
    CAPTURE(limit);
    const auto expected =
        static_cast<std::size_t>(limit == 0 ? 3 : std::min(limit, 3));

    auto mono = make_3phase_hydro_planning();
    auto fc = make_fcf(cuts_file);
    fc.iteration_limit = limit;
    mono.system.future_cost_array = {fc};
    CHECK(run_monolithic(std::move(mono)) == expected);

    auto sddp = make_3phase_hydro_planning();
    sddp.system.future_cost_array = {fc};
    CHECK(run_sddp(std::move(sddp)) == expected);
  }

  SUBCASE("legacy boundary_max_iterations alias reaches the loader")
  {
    auto planning = make_3phase_hydro_planning();
    planning.options.method = MethodType::monolithic;
    planning.options.monolithic_options.boundary_cuts_file = cuts_file;
    planning.options.monolithic_options.boundary_cuts_mode =
        BoundaryCutsMode::combined;
    planning.options.monolithic_options.boundary_max_iterations = 1;
    REQUIRE(resolve_future_cost_aliases(planning).has_value());
    CHECK(run_monolithic(std::move(planning)) == 1);
  }

  std::filesystem::remove(cuts_file);
}

TEST_CASE(  // NOLINT
    "FutureCost relative cuts_file resolves against input_directory")
{
  const auto dir =
      std::filesystem::temp_directory_path() / "gtopt_test_fcss_inputdir";
  std::filesystem::create_directories(dir);
  write_three_iteration_cuts((dir / "bc.csv").string());

  for (const bool sddp : {false, true}) {
    CAPTURE(sddp);
    auto planning = make_3phase_hydro_planning();
    planning.options.input_directory = dir.string();
    planning.system.future_cost_array = {make_fcf("bc.csv")};
    const auto n = sddp ? run_sddp(std::move(planning))
                        : run_monolithic(std::move(planning));
    CHECK(n == 3);
  }

  std::filesystem::remove_all(dir);
}

TEST_CASE("Planning::merge keeps the FutureCost element")  // NOLINT
{
  // Regression: System::merge dropped future_cost_array, so a FutureCost
  // element read from a planning file (gtopt_main merges every file into
  // an empty Planning) never reached the LP.
  Planning from_file;
  from_file.system.future_cost_array = {
      FutureCost {
          .uid = Uid {1},
          .name = "fcf",
          .cuts_file = "boundary_cuts.csv",
      },
  };

  Planning merged;
  merged.merge(std::move(from_file));

  REQUIRE(merged.system.future_cost_array.size() == 1);
  CHECK(merged.system.future_cost_array.front().cuts_file.value_or("")
        == "boundary_cuts.csv");
}
