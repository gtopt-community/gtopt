// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// `nonanticipative: true` on a `scope: "global"` DecisionVariable: ONE value
// across scenes.  gtopt folds the active scenes into one extensive-form LP
// scene (scene_merge.hpp); the variable becomes a single shared column.
//
// Case — an LNG cargo decided before the hydrology is known.  Two scenes of
// one scenario each (p = 0.5): a must-take solar unit (100 MW · profile)
// leaves a residual load of 50 MW (scene 1) or 80 MW (scene 2) of a 100 MW
// demand.  `cargo` ∈ [0, 100] at 50 $/unit (`cost_type: energy`, so it is
// weighted by the probability mass of its LP) caps the LNG unit (10 $/MWh);
// a 100 $/MWh peaker covers the rest.
//
//   anticipative (default, one column per scene):
//     scene 1: cargo = 50 → 0.5·10·50 + 0.5·50·50              = 1500
//     scene 2: cargo = 80 → 0.5·10·80 + 0.5·50·80              = 2400
//     total                                                       = 3900
//   nonanticipative (one shared column, cost weighted by total mass 1):
//     a unit of cargo above 50 saves 0.5·90 = 45 < 50 → cargo = 50
//     50·50 + 0.5·10·50 + 0.5·(10·50 + 100·30)                  = 4500

#include <filesystem>
#include <format>
#include <fstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/cascade_method.hpp>
#include <gtopt/decision_variable_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/scene_merge.hpp>
#include <gtopt/sddp_investment_master.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

// clang-format off
constexpr std::string_view one_phase_sim = R"js(
  "block_array": [ { "uid": 1, "duration": 1 } ],
  "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
  "scenario_array": [
    { "uid": 1, "probability_factor": 0.5 },
    { "uid": 2, "probability_factor": 0.5 }
  ],
  "scene_array": [
    { "uid": 1, "name": "wet", "first_scenario": 0, "count_scenario": 1 },
    { "uid": 2, "name": "dry", "first_scenario": 1, "count_scenario": 1 }
  ]
)js";

// Two one-stage phases.  Residual load: 50 / 50 (scene 1), 50 / 80
// (scene 2).
constexpr std::string_view two_phase_sim = R"js(
  "block_array": [ { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 } ],
  "stage_array": [
    { "uid": 1, "first_block": 0, "count_block": 1 },
    { "uid": 2, "first_block": 1, "count_block": 1 }
  ],
  "phase_array": [
    { "uid": 1, "first_stage": 0, "count_stage": 1 },
    { "uid": 2, "first_stage": 1, "count_stage": 1 }
  ],
  "scenario_array": [
    { "uid": 1, "probability_factor": 0.5 },
    { "uid": 2, "probability_factor": 0.5 }
  ],
  "scene_array": [
    { "uid": 1, "name": "wet", "first_scenario": 0, "count_scenario": 1 },
    { "uid": 2, "name": "dry", "first_scenario": 1, "count_scenario": 1 }
  ]
)js";
// Same two phases, skewed probabilities (wet 0.3, dry 0.7).
constexpr std::string_view two_phase_sim_skewed = R"js(
  "block_array": [ { "uid": 1, "duration": 1 }, { "uid": 2, "duration": 1 } ],
  "stage_array": [
    { "uid": 1, "first_block": 0, "count_block": 1 },
    { "uid": 2, "first_block": 1, "count_block": 1 }
  ],
  "phase_array": [
    { "uid": 1, "first_stage": 0, "count_stage": 1 },
    { "uid": 2, "first_stage": 1, "count_stage": 1 }
  ],
  "scenario_array": [
    { "uid": 1, "probability_factor": 0.3 },
    { "uid": 2, "probability_factor": 0.7 }
  ],
  "scene_array": [
    { "uid": 1, "name": "wet", "first_scenario": 0, "count_scenario": 1 },
    { "uid": 2, "name": "dry", "first_scenario": 1, "count_scenario": 1 }
  ]
)js";
// clang-format on

constexpr std::string_view one_phase_profile = "[[[0.5]], [[0.2]]]";
constexpr std::string_view two_phase_profile =
    "[[[0.5], [0.5]], [[0.5], [0.2]]]";

struct CaseSpec
{
  std::string_view sim {one_phase_sim};
  std::string_view profile {one_phase_profile};
  std::string_view scope {"global"};
  std::string_view cargo_extra {R"("nonanticipative": true)"};
  std::string_view system_extra {};
  std::string_view output_dir {"output"};
};

[[nodiscard]] std::string case_json(const CaseSpec& spec)
{
  return std::format(R"js({{
  "options": {{
    "annual_discount_rate": 0.0,
    "output_format": "csv",
    "output_compression": "uncompressed",
    "output_directory": "{}",
    "model_options": {{ "use_single_bus": true, "scale_objective": 1,
                        "demand_fail_cost": 10000 }}
  }},
  "simulation": {{ {} }},
  "system": {{
    "name": "lng_nonanticipative",
    "bus_array": [ {{ "uid": 1, "name": "b1" }} ],
    "demand_array": [ {{ "uid": 1, "name": "d1", "bus": 1, "lmax": 100 }} ],
    "generator_array": [
      {{ "uid": 1, "name": "solar", "bus": 1, "gcost": 0, "capacity": 100 }},
      {{ "uid": 2, "name": "lng", "bus": 1, "gcost": 10, "capacity": 200 }},
      {{ "uid": 3, "name": "peaker", "bus": 1, "gcost": 100,
         "capacity": 200 }}
    ],
    "generator_profile_array": [
      {{ "uid": 1, "name": "solar_p", "generator": 1, "profile": {} }}
    ],
    "decision_variable_array": [
      {{ "uid": 1, "name": "cargo", "lower_bound": 0, "upper_bound": 100,
         "cost": 50.0, "cost_type": "energy", "scope": "{}"{}{} }}
    ],
    "user_constraint_array": [
      {{ "uid": 1, "name": "lng_cap",
         "expression":
           "generator('lng').generation <= decision_variable('cargo').value"
      }}
    ]{}
  }}
}})js",
                     spec.output_dir,
                     spec.sim,
                     spec.profile,
                     spec.scope,
                     spec.cargo_extra.empty() ? "" : ", ",
                     spec.cargo_extra,
                     spec.system_extra);
}

[[nodiscard]] Planning make_planning(const CaseSpec& spec)
{
  Planning base;
  base.merge(parse_planning_json(case_json(spec)));
  return base;
}

/// `cargo` value in the cell (scene, phase).
[[nodiscard]] double cargo_at(PlanningLP& plp, Index scene, Index phase)
{
  auto& sys = plp.systems()[SceneIndex {scene}][PhaseIndex {phase}];
  const auto& dv = sys.elements<DecisionVariableLP>().front();
  const auto& holder = dv.value_cols_holder();
  REQUIRE_FALSE(holder.empty());
  const auto& bcols = holder.begin()->second;
  REQUIRE_FALSE(bcols.empty());
  return sys.linear_interface().get_col_sol()[bcols.begin()->second];
}

[[nodiscard]] double total_obj(PlanningLP& plp)
{
  double total = 0.0;
  for (auto& scene : plp.systems()) {
    for (auto& sys : scene) {
      total += sys.linear_interface().get_obj_value();
    }
  }
  return total;
}

[[nodiscard]] SDDPOptions sddp_opts()
{
  SDDPOptions opts;
  opts.max_iterations = 30;
  opts.convergence_tol = 1.0e-6;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  return opts;
}

/// The 2-phase nonanticipative toy (UB = LB = 5000, cargo 50 in both
/// phases under p = 0.5 / 0.5).
[[nodiscard]] Planning two_phase_planning(
    std::string_view cargo_extra = R"("nonanticipative": true)")
{
  return make_planning({
      .sim = two_phase_sim,
      .profile = two_phase_profile,
      .cargo_extra = cargo_extra,
  });
}

/// One aperture per scenario (makes the aperture backward pass active).
void add_aperture_array(Planning& planning)
{
  planning.simulation.aperture_array = Array<Aperture> {
      Aperture {.uid = Uid {1}, .source_scenario = Uid {1}},
      Aperture {.uid = Uid {2}, .source_scenario = Uid {2}},
  };
}

/// The SDDP solve fails with an InvalidInput naming `cargo` and @p option.
void check_sddp_refused(PlanningLP& plp,
                        const SDDPOptions& opts,
                        std::string_view option)
{
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE_FALSE(r.has_value());
  CHECK(r.error().code == ErrorCode::InvalidInput);
  CHECK(r.error().message.find("'cargo'") != std::string::npos);
  CHECK(r.error().message.find("nonanticipative") != std::string::npos);
  CHECK(r.error().message.find(option) != std::string::npos);
}

/// The SDDP solve converges to the nonanticipative optimum.
void check_sddp_5000(PlanningLP& plp, const SDDPOptions& opts)
{
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  CHECK(r->back().upper_bound == doctest::Approx(5000.0));
  CHECK(r->back().lower_bound == doctest::Approx(5000.0));
  CHECK(cargo_at(plp, 0, 0) == doctest::Approx(50.0));
  CHECK(cargo_at(plp, 0, 1) == doctest::Approx(50.0));
}

}  // namespace

TEST_CASE("Nonanticipative DV: anticipative reference (default)")  // NOLINT
{
  PlanningLP plp(make_planning({.cargo_extra = ""}));
  CHECK_FALSE(plp.simulation().scene_merge().has_value());
  REQUIRE(plp.systems().size() == 2);
  REQUIRE(plp.resolve().has_value());
  CHECK(cargo_at(plp, 0, 0) == doctest::Approx(50.0));
  CHECK(cargo_at(plp, 1, 0) == doctest::Approx(80.0));
  CHECK(total_obj(plp) == doctest::Approx(3900.0));
}

TEST_CASE("Nonanticipative DV: one shared value across scenes")  // NOLINT
{
  PlanningLP plp(make_planning({}));
  REQUIRE(plp.simulation().scene_merge().has_value());
  const auto merge = plp.simulation().scene_merge().value_or(SceneMerge {});
  CHECK(merge.original_scenes.size() == 2);
  CHECK(merge.merged_scene.uid == 1);
  CHECK(merge.merged_scene.count_scenario == 2);
  REQUIRE(merge.variables.size() == 1);
  CHECK(merge.variables.front() == "cargo");
  CHECK(merge.scene_of_scenario.at(1) == 1);
  CHECK(merge.scene_of_scenario.at(2) == 2);

  // One extensive-form LP for both scenes; the input scene_array is kept.
  REQUIRE(plp.systems().size() == 1);
  CHECK(plp.planning().simulation.scene_array.size() == 2);
  CHECK(plp.simulation().scenes().front().scenarios().size() == 2);

  REQUIRE(plp.resolve().has_value());
  CHECK(cargo_at(plp, 0, 0) == doctest::Approx(50.0));
  CHECK(total_obj(plp) == doctest::Approx(4500.0));
}

TEST_CASE(  // NOLINT
    "Nonanticipative DV: output keeps one shard per original scene")
{
  const auto out_dir =
      std::filesystem::temp_directory_path() / "gtopt_test_dv_nonant_out";
  std::filesystem::remove_all(out_dir);
  const auto out_str = out_dir.generic_string();
  PlanningLP plp(make_planning({.output_dir = out_str}));
  REQUIRE(plp.resolve().has_value());
  plp.write_out();

  const auto gen1 = out_dir / "Generator" / "generation_sol_s1_p0.csv";
  const auto gen2 = out_dir / "Generator" / "generation_sol_s2_p0.csv";
  CHECK(std::filesystem::exists(gen1));
  CHECK(std::filesystem::exists(gen2));

  // Every row of a shard belongs to that scene's scenario.
  const auto rows_of = [](const std::filesystem::path& p)
  {
    std::ifstream in(p);
    std::vector<std::string> rows;
    std::string line;
    std::getline(in, line);  // header
    while (std::getline(in, line)) {
      rows.push_back(line);
    }
    return rows;
  };
  const auto r1 = rows_of(gen1);
  const auto r2 = rows_of(gen2);
  REQUIRE_FALSE(r1.empty());
  REQUIRE_FALSE(r2.empty());
  for (const auto& r : r1) {
    CHECK(r.starts_with("1,"));
  }
  for (const auto& r : r2) {
    CHECK(r.starts_with("2,"));
  }
  // solution.csv: ONE row for the merged LP (scene uid 1), whose objective
  // is the whole extensive form — the probability-weighted cost of both
  // original scenes plus the shared cargo, charged once (4500).
  const auto sol = rows_of(out_dir / "solution.csv");
  REQUIRE(sol.size() == 1);
  const auto field = [](const std::string& row, std::size_t n)
  {
    std::size_t pos = 0;
    for (std::size_t i = 0; i < n; ++i) {
      pos = row.find(',', pos) + 1;
    }
    return row.substr(pos, row.find(',', pos) - pos);
  };
  CHECK(field(sol.front(), 0) == "1");  // scene
  CHECK(std::stod(field(sol.front(), 4)) == doctest::Approx(4500.0));
  CHECK(total_obj(plp) == doctest::Approx(4500.0));

  // No shard under the merged-scene-only layout besides the originals.
  CHECK_FALSE(std::filesystem::exists(out_dir / "Generator"
                                      / "generation_sol_s0_p0.csv"));
  std::filesystem::remove_all(out_dir);
}

TEST_CASE("Nonanticipative DV: SDDP over 2 phases")  // NOLINT
{
  // Anticipative SDDP (one column per scene):
  //   scene 1: cargo = 50 → 25·50 + 0.5·(10·50 + 10·50)         = 1750
  //   scene 2: cargo = 80 → 25·80 + 0.5·(10·50 + 10·80)         = 2650
  //   UB = LB = 4400.
  // Nonanticipative: above 50 a unit only helps scene 2 in phase 2
  //   (0.5·90 = 45 < 50) → cargo = 50, carried as state into phase 2:
  //   50·50 + 10·50 + 0.5·10·50 + 0.5·(10·50 + 100·30)          = 5000.
  SUBCASE("anticipative reference")
  {
    PlanningLP plp(make_planning({
        .sim = two_phase_sim,
        .profile = two_phase_profile,
        .cargo_extra = "",
    }));
    SDDPMethod sddp(plp, sddp_opts());
    auto r = sddp.solve();
    REQUIRE(r.has_value());
    REQUIRE_FALSE(r->empty());
    CHECK(r->back().upper_bound == doctest::Approx(4400.0));
    CHECK(r->back().lower_bound == doctest::Approx(4400.0));
  }
  SUBCASE("nonanticipative")
  {
    PlanningLP plp(make_planning({
        .sim = two_phase_sim,
        .profile = two_phase_profile,
    }));
    REQUIRE(plp.systems().size() == 1);
    SDDPMethod sddp(plp, sddp_opts());
    auto r = sddp.solve();
    REQUIRE(r.has_value());
    REQUIRE_FALSE(r->empty());
    CHECK(r->back().upper_bound == doctest::Approx(5000.0));
    CHECK(r->back().lower_bound == doctest::Approx(5000.0));
    CHECK(cargo_at(plp, 0, 0) == doctest::Approx(50.0));
    CHECK(cargo_at(plp, 0, 1) == doctest::Approx(50.0));
  }
  SUBCASE("backward apertures are refused")
  {
    PlanningLP plp(make_planning({
        .sim = two_phase_sim,
        .profile = two_phase_profile,
    }));
    auto opts = sddp_opts();
    opts.apertures = Array<Uid> {1, 2};
    SDDPMethod sddp(plp, opts);
    auto r = sddp.solve();
    REQUIRE_FALSE(r.has_value());
    CHECK(r.error().message.find("cargo") != std::string::npos);
    CHECK(r.error().message.find("apertures") != std::string::npos);
  }
}

TEST_CASE("Nonanticipative DV: monolithic 2 phases, one value")  // NOLINT
{
  // Monolithic multi-phase is sequential (myopic, see the global-phases
  // tests) — but both scenes still share ONE value in every phase.
  PlanningLP plp(make_planning({
      .sim = two_phase_sim,
      .profile = two_phase_profile,
  }));
  REQUIRE(plp.systems().size() == 1);
  REQUIRE(plp.resolve().has_value());
  CHECK(cargo_at(plp, 0, 0) == doctest::Approx(50.0));
  CHECK(cargo_at(plp, 0, 1) == doctest::Approx(50.0));
}

TEST_CASE("Nonanticipative DV: validation errors")  // NOLINT
{
  const auto throws_with = [](const CaseSpec& spec, std::string_view what)
  {
    bool thrown = false;
    try {
      const PlanningLP plp(make_planning(spec));
    } catch (const std::runtime_error& e) {
      thrown = true;
      const std::string msg = e.what();
      CHECK(msg.find("cargo") != std::string::npos);
      CHECK(msg.find(what) != std::string::npos);
    }
    CHECK(thrown);
  };

  SUBCASE("non-global scope")
  {
    throws_with({.scope = "phase"}, "scope: \"global\"");
  }

  SUBCASE("single scene: nonanticipative on a non-global scope still fails")
  {
    // No scene_array → nothing to merge, but the flag is still invalid.
    constexpr std::string_view sim = R"js(
  "block_array": [ { "uid": 1, "duration": 1 } ],
  "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
  "scenario_array": [ { "uid": 1, "probability_factor": 1.0 } ]
)js";
    throws_with(
        {
            .sim = sim,
            .profile = "0.5",
            .scope = "stage",
        },
        "requires");
  }

  SUBCASE("FutureCost user alpha")
  {
    throws_with(
        {
            .system_extra = R"(,
    "future_cost_array": [ { "uid": 1, "name": "fcf",
                             "use_user_alpha": true, "user_alpha_uid": 1 } ])",
        },
        "alpha");
  }

  SUBCASE("FutureCost boundary cuts")
  {
    throws_with(
        {
            .system_extra = R"(,
    "future_cost_array": [ { "uid": 1, "name": "fcf",
                             "cuts_file": "boundary_cuts.csv" } ])",
        },
        "boundary cuts");
  }

  SUBCASE("non-contiguous scenes")
  {
    constexpr std::string_view sim = R"js(
  "block_array": [ { "uid": 1, "duration": 1 } ],
  "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
  "scenario_array": [
    { "uid": 1, "probability_factor": 0.5 },
    { "uid": 2, "probability_factor": 0.0 },
    { "uid": 3, "probability_factor": 0.5 }
  ],
  "scene_array": [
    { "uid": 1, "first_scenario": 0, "count_scenario": 1 },
    { "uid": 3, "first_scenario": 2, "count_scenario": 1 }
  ]
)js";
    throws_with(
        {
            .sim = sim,
            .profile = "[[[0.5]], [[0.5]], [[0.2]]]",
        },
        "contiguous");
  }
}

TEST_CASE("Nonanticipative DV: a single active scene needs no merge")  // NOLINT
{
  constexpr std::string_view sim = R"js(
  "block_array": [ { "uid": 1, "duration": 1 } ],
  "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
  "scenario_array": [
    { "uid": 1, "probability_factor": 0.5 },
    { "uid": 2, "probability_factor": 0.5 }
  ],
  "scene_array": [
    { "uid": 1, "first_scenario": 0, "count_scenario": 1 },
    { "uid": 2, "active": 0, "first_scenario": 1, "count_scenario": 1 }
  ]
)js";
  const auto planning = make_planning({.sim = sim});
  CHECK_FALSE(plan_nonanticipative_scene_merge(planning).has_value());
}

TEST_CASE("Nonanticipative DV: UserModel variables merge too")  // NOLINT
{
  auto planning = make_planning({.cargo_extra = ""});
  UserModel um;
  um.uid = 1;
  um.name = "lng_model";
  um.variable_array.push_back(DecisionVariable {
      .uid = 7,
      .name = "calendar",
      .scope = "global",
      .nonanticipative = true,
  });
  planning.system.user_model_array.push_back(std::move(um));
  const auto merge = plan_nonanticipative_scene_merge(planning);
  REQUIRE(merge.has_value());
  const auto vars = merge.value_or(SceneMerge {}).variables;
  REQUIRE(vars.size() == 1);
  CHECK(vars.front() == "calendar");
}

TEST_CASE(  // NOLINT
    "Nonanticipative DV: lng_adp repro, global column in a block constraint")
{
  // Reduced from an LNG study case: a block-scope row
  // `x <= g` must see the global column `g` (it was once dropped, making
  // the LP infeasible).  Run with one scene and with two merged scenes.
  constexpr std::string_view base = R"js({
  "options": { "output_format": "csv", "output_compression": "uncompressed",
               "model_options": { "use_single_bus": true,
                                  "scale_objective": 1 } },
  "simulation": {
    "block_array": [ { "uid": 1, "duration": 1 } ],
    "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
    "scenario_array": [ { "uid": 1, "probability_factor": 0.5 },
                        { "uid": 2, "probability_factor": 0.5 } ]
    SCENES
  },
  "system": {
    "name": "global_scope_repro",
    "bus_array": [ { "uid": 1, "name": "b1" } ],
    "decision_variable_array": [
      { "uid": 1, "name": "x", "lower_bound": 0, "upper_bound": 1 },
      { "uid": 2, "name": "g", "lower_bound": 0, "upper_bound": 1,
        "cost": 1.0, "scope": "global" NONANT }
    ],
    "user_constraint_array": [
      { "uid": 1, "name": "x_min",
        "expression": "decision_variable('x').value >= 1" },
      { "uid": 2, "name": "x_le_g",
        "expression":
          "decision_variable('x').value <= decision_variable('g').value" }
    ]
  }
})js";
  const auto make = [&](std::string_view scenes, std::string_view nonant)
  {
    std::string json(base);
    json.replace(json.find("SCENES"), 6, scenes);
    json.replace(json.find("NONANT"), 6, nonant);
    Planning planning;
    planning.merge(parse_planning_json(json));
    return planning;
  };

  SUBCASE("one scene")
  {
    PlanningLP plp(make("", ""));
    REQUIRE(plp.resolve().has_value());
    CHECK(total_obj(plp) == doctest::Approx(1.0));
  }
  SUBCASE("two merged scenes: g = 1 charged once (raw cost)")
  {
    PlanningLP plp(make(R"js(,
    "scene_array": [
      { "uid": 1, "first_scenario": 0, "count_scenario": 1 },
      { "uid": 2, "first_scenario": 1, "count_scenario": 1 } ])js",
                        R"js(, "nonanticipative": true)js"));
    REQUIRE(plp.systems().size() == 1);
    REQUIRE(plp.resolve().has_value());
    CHECK(total_obj(plp) == doctest::Approx(1.0));
  }
}

// ── SDDP / cascade option audit: supported combinations ───────────────────

TEST_CASE(  // NOLINT
    "Nonanticipative DV: SDDP options that work on the merged scene")
{
  SUBCASE("cut_sharing_mode = multicut (one scene: no-op)")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.cut_sharing = CutSharingMode::multicut;
    check_sddp_5000(plp, opts);
  }
  SUBCASE("forward_sampling_mode = resampled (one scene: no-op)")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.forward_sampling = ForwardSamplingMode::resampled;
    check_sddp_5000(plp, opts);
  }
  SUBCASE("low_memory_mode = compress")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.low_memory_mode = LowMemoryMode::compress;
    check_sddp_5000(plp, opts);
  }
  SUBCASE("basis_cross_mode = off")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.basis_cross_mode = BasisCrossMode::off;
    check_sddp_5000(plp, opts);
  }
  SUBCASE("Phase.apertures data without aperture_array is inert")
  {
    auto planning = two_phase_planning();
    for (auto& ph : planning.simulation.phase_array) {
      ph.apertures = Array<Uid> {1, 2};
    }
    PlanningLP plp(std::move(planning));
    check_sddp_5000(plp, sddp_opts());
  }
  SUBCASE("aperture_array present but num_apertures = 0")
  {
    auto planning = two_phase_planning();
    add_aperture_array(planning);
    PlanningLP plp(std::move(planning));
    auto opts = sddp_opts();
    opts.num_apertures = 0;
    check_sddp_5000(plp, opts);
  }
  SUBCASE("aperture_array present but sddp_options.apertures = []")
  {
    auto planning = two_phase_planning();
    add_aperture_array(planning);
    PlanningLP plp(std::move(planning));
    auto opts = sddp_opts();
    opts.apertures = Array<Uid> {};
    check_sddp_5000(plp, opts);
  }
}

TEST_CASE(  // NOLINT
    "Nonanticipative DV: SDDP with skewed scene probabilities")
{
  // wet 0.3 / dry 0.7.  Above cargo 50 a unit saves 0.7·90 = 63 > 50 in
  // phase 2, so cargo = 80, carried into phase 2 (state, charged once):
  //   50·80 + 10·50 + 0.3·10·50 + 0.7·10·80                     = 5210.
  // state: false — independent per-phase copies, each charged:
  //   phase 1: cargo 50 → 50·50 + 10·50                          = 3000
  //   phase 2: cargo 80 → 50·80 + 0.3·10·50 + 0.7·10·80          = 4710
  for (const bool state : {true, false}) {
    CAPTURE(state);
    PlanningLP plp(make_planning({
        .sim = two_phase_sim_skewed,
        .profile = two_phase_profile,
        .cargo_extra = state ? R"("nonanticipative": true)"
                             : R"("nonanticipative": true, "state": false)",
    }));
    REQUIRE(plp.systems().size() == 1);
    SDDPMethod sddp(plp, sddp_opts());
    auto r = sddp.solve();
    REQUIRE(r.has_value());
    REQUIRE_FALSE(r->empty());
    const double expected = state ? 5210.0 : 7710.0;
    CHECK(r->back().upper_bound == doctest::Approx(expected));
    CHECK(r->back().lower_bound == doctest::Approx(expected));
    CHECK(cargo_at(plp, 0, 0) == doctest::Approx(state ? 80.0 : 50.0));
    CHECK(cargo_at(plp, 0, 1) == doctest::Approx(80.0));
  }
}

TEST_CASE(  // NOLINT
    "Nonanticipative DV: cascade without cut inheritance is supported")
{
  PlanningLP plp(two_phase_planning());
  auto base = sddp_opts();
  base.apertures = Array<Uid> {};
  // Default convergence (ΔUB stationarity): level 1 starts cold, seeded
  // with level 0's bounds — it must not stop before its own window and
  // its own bounds meet (it used to stop at iter 1 with a 40 % gap).
  CascadeOptions cascade;
  cascade.level_array = {
      CascadeLevel {
          .name = OptName {"l0"},
          .sddp_options = CascadeLevelMethod {.max_iterations = OptInt {30}},
      },
      CascadeLevel {
          .name = OptName {"l1"},
          .sddp_options = CascadeLevelMethod {.max_iterations = OptInt {30}},
      },
  };
  CascadePlanningMethod solver(std::move(base), std::move(cascade));
  auto r = solver.solve(plp, SolverOptions {});
  REQUIRE(r.has_value());
  REQUIRE_FALSE(solver.all_results().empty());
  CHECK(solver.all_results().back().upper_bound == doctest::Approx(5000.0));
  CHECK(solver.all_results().back().lower_bound == doctest::Approx(5000.0));
}

// ── SDDP / cascade option audit: hard errors ──────────────────────────────

TEST_CASE("Nonanticipative DV: SDDP options that are refused")  // NOLINT
{
  SUBCASE("aperture_array (default per-phase apertures)")
  {
    auto planning = two_phase_planning();
    add_aperture_array(planning);
    PlanningLP plp(std::move(planning));
    check_sddp_refused(plp, sddp_opts(), "aperture_array");
  }
  SUBCASE("aperture_array + num_apertures")
  {
    auto planning = two_phase_planning();
    add_aperture_array(planning);
    PlanningLP plp(std::move(planning));
    auto opts = sddp_opts();
    opts.num_apertures = 1;
    opts.aperture_chunk_mode = ApertureChunkMode::per_aperture;
    check_sddp_refused(plp, opts, "num_apertures");
  }
  SUBCASE("sddp_options.apertures (synthetic apertures)")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.apertures = Array<Uid> {1, 2};
    check_sddp_refused(plp, opts, "sddp_options.apertures");
  }
  SUBCASE("cut_sharing_mode = markov")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.cut_sharing = CutSharingMode::markov;
    check_sddp_refused(plp, opts, "markov");
  }
  SUBCASE("boundary_cuts_file in the SDDP options")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.boundary_cuts_file = "boundary_cuts.csv";
    check_sddp_refused(plp, opts, "boundary cuts");
  }
  SUBCASE("hot start: cuts_input_file")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.recovery_mode = RecoveryMode::cuts;
    opts.cuts_input_file = "previous_cuts.parquet";
    check_sddp_refused(plp, opts, "cuts_input_file");
  }
  SUBCASE("hot start: cut_recovery_mode")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.recovery_mode = RecoveryMode::full;
    opts.cut_recovery_mode = HotStartMode::append;
    opts.cuts_output_file = "cuts/sddp_cuts.parquet";
    check_sddp_refused(plp, opts, "cut_recovery_mode");
  }
  SUBCASE("cuts_input_file without --recover is ignored (no load)")
  {
    PlanningLP plp(two_phase_planning());
    auto opts = sddp_opts();
    opts.recovery_mode = RecoveryMode::none;
    opts.cuts_input_file = "previous_cuts.parquet";
    check_sddp_5000(plp, opts);
  }
  SUBCASE("direct load_cuts / ensure_initialized are guarded too")
  {
    PlanningLP plp(two_phase_planning());
    SDDPMethod sddp(plp, sddp_opts());
    auto loaded = sddp.load_cuts("previous_cuts.parquet");
    REQUIRE_FALSE(loaded.has_value());
    CHECK(loaded.error().message.find("'cargo'") != std::string::npos);
    auto dir = sddp.load_scene_cuts_from_directory("cuts");
    REQUIRE_FALSE(dir.has_value());

    auto opts = sddp_opts();
    opts.apertures = Array<Uid> {1, 2};
    SDDPMethod sddp_ap(plp, opts);
    auto init = sddp_ap.ensure_initialized();
    REQUIRE_FALSE(init.has_value());
    CHECK(init.error().message.find("apertures") != std::string::npos);
  }
}

TEST_CASE("Nonanticipative DV: cascade options that are refused")  // NOLINT
{
  const auto run = [](CascadeOptions cascade, bool with_apertures)
  {
    auto planning = two_phase_planning();
    if (with_apertures) {
      add_aperture_array(planning);
    }
    PlanningLP plp(std::move(planning));
    auto base = sddp_opts();
    base.apertures = Array<Uid> {};
    CascadePlanningMethod solver(std::move(base), std::move(cascade));
    auto r = solver.solve(plp, SolverOptions {});
    // Fail fast: refused before ANY level solves.
    CHECK(solver.level_stats().empty());
    CHECK(solver.all_results().empty());
    return r;
  };

  SUBCASE("per-level num_apertures = 0 keeps the level deterministic")
  {
    CascadeOptions cascade;
    cascade.level_array = {
        CascadeLevel {
            .name = OptName {"l0"},
            .sddp_options = CascadeLevelMethod {.num_apertures = OptInt {0}},
        },
    };
    auto planning = two_phase_planning();
    add_aperture_array(planning);
    PlanningLP plp(std::move(planning));
    auto base = sddp_opts();  // apertures unset: the pool is aperture_array
    CascadePlanningMethod solver(std::move(base), std::move(cascade));
    auto r = solver.solve(plp, SolverOptions {});
    REQUIRE(r.has_value());
    REQUIRE_FALSE(solver.all_results().empty());
    CHECK(solver.all_results().back().upper_bound == doctest::Approx(5000.0));
  }
  SUBCASE("per-level num_apertures re-enables apertures")
  {
    // The base options disable apertures; level 1 (like the default
    // cascade's L1 = 8 apertures) turns them back on.
    CascadeOptions cascade;
    cascade.level_array = {
        CascadeLevel {.name = OptName {"l0"}},
        CascadeLevel {
            .name = OptName {"l1"},
            .sddp_options =
                CascadeLevelMethod {
                    .apertures = Array<Uid> {1, 2},
                    .num_apertures = OptInt {8},
                },
        },
    };
    auto r = run(std::move(cascade), true);
    REQUIRE_FALSE(r.has_value());
    CHECK(r.error().message.find("cascade level 'l1'") != std::string::npos);
    CHECK(r.error().message.find("'cargo'") != std::string::npos);
    CHECK(r.error().message.find("apertures") != std::string::npos);
  }
  SUBCASE("transition.inherit_optimality_cuts")
  {
    CascadeOptions cascade;
    cascade.level_array = {
        CascadeLevel {.name = OptName {"l0"}},
        CascadeLevel {
            .name = OptName {"l1"},
            .transition =
                CascadeTransition {
                    .inherit_optimality_cuts = OptInt {-1},
                },
        },
    };
    auto r = run(std::move(cascade), false);
    REQUIRE_FALSE(r.has_value());
    CHECK(r.error().message.find("cascade level 'l1'") != std::string::npos);
    CHECK(r.error().message.find("inherit_optimality_cuts")
          != std::string::npos);
  }
}

TEST_CASE(  // NOLINT
    "Nonanticipative DV: SDDP investment master is refused")
{
  const auto res =
      solve_investment_master(two_phase_planning(), InvestmentMasterOptions {});
  REQUIRE_FALSE(res.has_value());
  CHECK(res.error().code == ErrorCode::InvalidInput);
  CHECK(res.error().message.find("'cargo'") != std::string::npos);
  CHECK(res.error().message.find("investment master") != std::string::npos);
}
