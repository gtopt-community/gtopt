// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_ruiz_cut_scale.cpp
 * @brief     SDDP optimality cuts under ruiz equilibration (src/dep scale)
 * @date      2026-11-22
 *
 * Regression guard for the cut-slope bug where
 * `StateVariable::reduced_cost_physical` divided the DEPENDENT column's
 * reduced cost (next-phase `sini`) by the SOURCE column's scale
 * (last-block `efin` of the current phase).  Ruiz equilibration gives
 * the two columns independent factors, so every optimality-cut slope
 * was off by `s_dep / s_src` and the SDDP lower bound overshot the
 * true optimum (LB > UB).
 *
 * The fixture is a single-bus hydrothermal system with several
 * reservoirs of very different sizes and production factors.  Each
 * reservoir's `efin_cost` equals `ρ · C` (ρ = MWh/hm³, C = th_mid
 * cost), so every valid optimality-cut slope on an `efin` column is a
 * water value `ρ · g` with `g` one of the system's price levels
 * (thermal costs, demand-fail cost); a slope skewed by
 * `s_dep / s_src` is not.  The bound reference is the same system
 * solved as ONE phase (a single LP; no cuts involved).
 */

#include <array>
#include <cmath>
#include <format>
#include <limits>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>

using namespace gtopt;

namespace
{

struct RsvSpec
{
  const char* name;
  double vmax;  ///< emax [hm3]
  double eini;  ///< initial = target final volume [hm3]
  double rho;  ///< production factor [MWh/hm3]
};

// Sizes span ~1.5 decades so auto-scale and ruiz produce clearly
// different column factors per reservoir and per (efin, sini) pair.
constexpr std::array<RsvSpec, 3> kReservoirs {{
    {.name = "LMAULE", .vmax = 1453.4, .eini = 516.1, .rho = 3535.11},
    {.name = "ELTORO", .vmax = 5585.9, .eini = 3000.0, .rho = 2325.0},
    {.name = "CIPRESES", .vmax = 174.7, .eini = 114.4, .rho = 1791.85},
}};

constexpr double kMarginalCost = 120.0;  ///< th_mid gcost = water value / ρ
constexpr int kNumStages = 4;
/// Every marginal price a unit of stored energy can be worth [$/MWh]:
/// th_cheap, th_mid (= efin_cost level), th_peak, demand fail cost.
constexpr std::array<double, 4> kPriceLevels {40.0, 120.0, 300.0, 850.0};

/// Build the hydrothermal case.  `phases = kNumStages` gives one stage
/// per phase (SDDP); `phases = 1` puts every stage in a single phase
/// (the single-LP reference).
[[nodiscard]] auto make_case_json(int phases,
                                  std::string_view equilibration,
                                  std::string_view extra_options,
                                  double scale_objective) -> std::string
{
  std::string blocks;
  std::string stages;
  for (int s = 0; s < kNumStages; ++s) {
    blocks += std::format(R"({{"uid":{},"duration":200.0}},)"
                          R"({{"uid":{},"duration":520.0}},)",
                          (2 * s) + 1,
                          (2 * s) + 2);
    stages += std::format(
        R"({{"uid":{},"first_block":{},"count_block":2}},)", s + 1, 2 * s);
  }
  blocks.pop_back();
  stages.pop_back();

  std::string phase_array;
  if (phases == 1) {
    phase_array = std::format(R"({{"uid":1,"first_stage":0,"count_stage":{}}})",
                              kNumStages);
  } else {
    for (int s = 0; s < kNumStages; ++s) {
      phase_array += std::format(
          R"({{"uid":{},"first_stage":{},"count_stage":1}},)", s + 1, s);
    }
    phase_array.pop_back();
  }

  std::string gens =
      R"({"uid":1,"name":"th_cheap","bus":1,"gcost":40.0,"capacity":600.0},)"
      R"({"uid":2,"name":"th_mid","bus":1,"gcost":120.0,"capacity":800.0},)"
      R"({"uid":3,"name":"th_peak","bus":1,"gcost":300.0,"capacity":800.0})";
  std::string junctions;
  std::string waterways;
  std::string turbines;
  std::string reservoirs;
  std::string flows;
  for (std::size_t i = 0; i < kReservoirs.size(); ++i) {
    const auto& r = kReservoirs[i];
    const auto ii = static_cast<int>(i);
    const double pf = r.rho * 0.0036;  // MW per m3/s
    const double fmax = std::max(0.1 * r.vmax / 2.592, 0.001);
    const double mean = 0.04 * r.vmax / 2.592;
    const double box = 1e3 * std::max(1.0, r.vmax / 100.0);

    junctions += std::format(
        R"({{"uid":{},"name":"j_{}"}},{{"uid":{},"name":"d_{}","drain":true}},)",
        100 + (2 * ii),
        r.name,
        101 + (2 * ii),
        r.name);
    waterways += std::format(
        R"({{"uid":{},"name":"w_{}","junction_a":{},"junction_b":{},)"
        R"("fmin":0.0,"fmax":{}}},)",
        200 + ii,
        r.name,
        100 + (2 * ii),
        101 + (2 * ii),
        fmax);
    gens += std::format(
        R"(,{{"uid":{},"name":"h_{}","bus":1,"gcost":0.0,"capacity":{}}})",
        10 + ii,
        r.name,
        pf * fmax);
    turbines +=
        std::format(R"({{"uid":{},"name":"t_{}","waterway":{},"generator":{},)"
                    R"("production_factor":{}}},)",
                    300 + ii,
                    r.name,
                    200 + ii,
                    10 + ii,
                    pf);
    reservoirs += std::format(
        R"({{"uid":{},"name":"{}","junction":{},"capacity":{},"emin":0.0,)"
        R"("emax":{},"eini":{},"efin":{},"efin_cost":{},"fmin":{},"fmax":{},)"
        R"("flow_conversion_rate":0.0036}},)",
        400 + ii,
        r.name,
        100 + (2 * ii),
        r.vmax,
        r.vmax,
        r.eini,
        r.eini,
        r.rho * kMarginalCost,
        -box,
        box);
    std::string infl;
    for (int s = 0; s < kNumStages; ++s) {
      const double q = mean * (1.0 + (0.5 * std::sin(s + ii)));
      infl += std::format("[{},{}],", q, q);
    }
    infl.pop_back();
    flows +=
        std::format(R"({{"uid":{},"name":"in_{}","direction":1,"junction":{},)"
                    R"("discharge":[[{}]]}},)",
                    500 + ii,
                    r.name,
                    100 + (2 * ii),
                    infl);
  }
  junctions.pop_back();
  waterways.pop_back();
  turbines.pop_back();
  reservoirs.pop_back();
  flows.pop_back();

  std::string dem;
  for (int s = 0; s < kNumStages; ++s) {
    dem += std::format("[{},{}],",
                       1500.0 + (500.0 * std::cos(s)),
                       1900.0 + (500.0 * std::cos(s)));
  }
  dem.pop_back();

  return std::format(
      R"({{"options":{{"method":"{}",)"
      R"("model_options":{{"use_single_bus":true,"demand_fail_cost":850.0,)"
      R"("state_violation_cost":1000.0,"scale_objective":{}}},)"
      R"("lp_matrix_options":{{"equilibration_method":"{}"}}{}}},)"
      R"("simulation":{{"block_array":[{}],"stage_array":[{}],)"
      R"("scenario_array":[{{"uid":1}}],"phase_array":[{}]}},)"
      R"("system":{{"name":"rsv_small","bus_array":[{{"uid":1,"name":"b1"}}],)"
      R"("generator_array":[{}],)"
      R"("demand_array":[{{"uid":1,"name":"load","bus":1,"lmax":[{}],)"
      R"("fcost":850.0}}],)"
      R"("junction_array":[{}],"waterway_array":[{}],)"
      R"("reservoir_array":[{}],"turbine_array":[{}],"flow_array":[{}]}}}})",
      phases == 1 ? "monolithic" : "sddp",
      scale_objective,
      equilibration,
      extra_options,
      blocks,
      stages,
      phase_array,
      gens,
      dem,
      junctions,
      waterways,
      reservoirs,
      turbines,
      flows);
}

/// The equilibration method must reach the LP builder through
/// `LpMatrixOptions` (the standalone runner derives it from
/// `options.lp_matrix_options`; a bare `PlanningLP` does not).
[[nodiscard]] auto matrix_options(std::string_view equilibration)
    -> LpMatrixOptions
{
  LpMatrixOptions opts;
  if (equilibration == "ruiz") {
    opts.equilibration_method = LpEquilibrationMethod::ruiz;
  } else if (equilibration == "row_max") {
    opts.equilibration_method = LpEquilibrationMethod::row_max;
  } else {
    opts.equilibration_method = LpEquilibrationMethod::none;
  }
  return opts;
}

/// Optimum of the whole horizon as ONE LP (no Benders cuts involved).
[[nodiscard]] auto single_lp_optimum(std::string_view equilibration,
                                     std::string_view extra_options,
                                     double scale_objective) -> double
{
  PlanningLP plp(parse_planning_json(make_case_json(
                     1, equilibration, extra_options, scale_objective)),
                 matrix_options(equilibration));
  auto res = plp.resolve();
  REQUIRE(res.has_value());
  return plp.system(first_scene_index(), first_phase_index())
      .linear_interface()
      .get_obj_value();
}

struct SddpOutcome
{
  double lower_bound {};
  double upper_bound {};
  bool converged {false};
  /// max over reservoirs/phases of the relative distance between the
  /// cut slope and the nearest ρ·g (g in kPriceLevels), taken on the
  /// LAST optimality cut of each phase that carries a coefficient.
  double max_slope_rel_err {0.0};
  int slopes_checked {0};
  /// State links whose source and dependent column scales differ.
  int scale_mismatches {0};
};

[[nodiscard]] auto run_sddp(std::string_view equilibration,
                            std::string_view extra_options,
                            double scale_objective) -> SddpOutcome
{
  PlanningLP plp(
      parse_planning_json(make_case_json(
          kNumStages, equilibration, extra_options, scale_objective)),
      matrix_options(equilibration));

  SDDPOptions opts;
  opts.max_iterations = 30;
  opts.convergence_tol = 1.0e-9;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;

  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());

  SddpOutcome out {
      .lower_bound = results->back().lower_bound,
      .upper_bound = results->back().upper_bound,
      .converged = results->back().converged,
  };

  // Cut slopes: the physical coefficient on each reservoir's `efin`
  // source column (normalised by the α coefficient) must be a water
  // value ρ·g (see kPriceLevels).
  const auto& sim = plp.simulation();
  const auto cuts = sddp.stored_cuts();
  for (int p = 0; p + 1 < kNumStages; ++p) {
    const PhaseIndex phase {p};
    const auto phase_uid = sim.uid_of(phase);
    const StoredCut* last = nullptr;
    for (const auto& c : cuts) {
      if (c.type == CutType::Optimality && c.phase_uid == phase_uid) {
        last = &c;
      }
    }
    REQUIRE(last != nullptr);
    double alpha_coef = 0.0;
    if (last->pivot_col != ColIndex {unknown_index}) {
      for (const auto& [col, coef] : last->coefficients) {
        if (col == last->pivot_col) {
          alpha_coef = coef;
        }
      }
    }
    REQUIRE(alpha_coef > 0.0);

    for (const auto& [key, svar] :
         sim.state_variables(first_scene_index(), phase))
    {
      if (key.col_name != "efin") {
        continue;
      }
      const auto idx = static_cast<std::size_t>(key.uid - 400);
      REQUIRE(idx < kReservoirs.size());
      // Guard that the fixture really exercises s_src != s_dep.
      const double s_src = plp.system(first_scene_index(), phase)
                               .linear_interface()
                               .get_col_scale(svar.col());
      for (const auto& dep : svar.dependent_variables()) {
        const double s_dep = plp.system(first_scene_index(), dep.phase_index())
                                 .linear_interface()
                                 .get_col_scale(dep.col());
        if (std::abs(s_src - s_dep) > 1e-9 * std::abs(s_src)) {
          ++out.scale_mismatches;
        }
      }
      for (const auto& [col, coef] : last->coefficients) {
        if (col == svar.col()) {
          // Cut row: α − rc·x ≥ b with rc = ∂z/∂sini = −ρ·C (more water
          // ⇒ lower future cost), so the normalised coefficient is +ρ·C.
          //
          // At a degenerate trial point the LP may return any valid
          // subgradient, i.e. the water value ρ·g of whichever price
          // level g is marginal (a thermal gcost, the efin_cost level C,
          // or the demand-fail cost).  A correct slope is ρ times one of
          // those levels; the s_dep/s_src-skewed slope is not.
          const double slope = coef / alpha_coef;
          double err = std::numeric_limits<double>::infinity();
          for (const double g : kPriceLevels) {
            const double ref = kReservoirs[idx].rho * g;
            err = std::min(err, std::abs(slope - ref) / ref);
          }
          out.max_slope_rel_err = std::max(out.max_slope_rel_err, err);
          ++out.slopes_checked;
        }
      }
    }
  }
  return out;
}

void check_sddp_matches_single_lp(std::string_view equilibration,
                                  std::string_view extra_options = "",
                                  double scale_objective = 1000.0)
{
  const double ref =
      single_lp_optimum(equilibration, extra_options, scale_objective);
  const auto out = run_sddp(equilibration, extra_options, scale_objective);
  INFO("eq=",
       equilibration,
       " ref=",
       ref,
       " LB=",
       out.lower_bound,
       " UB=",
       out.upper_bound,
       " max_slope_rel_err=",
       out.max_slope_rel_err,
       " scale_mismatches=",
       out.scale_mismatches);
  REQUIRE(ref > 0.0);
  // ruiz must actually give efin and next-phase sini different scales,
  // otherwise this test cannot see the bug; without ruiz they coincide.
  if (equilibration == "ruiz") {
    CHECK(out.scale_mismatches > 0);
  } else {
    CHECK(out.scale_mismatches == 0);
  }
  CHECK(out.converged);
  CHECK(out.lower_bound == doctest::Approx(ref).epsilon(1e-6));
  CHECK(out.upper_bound == doctest::Approx(ref).epsilon(1e-6));
  // LB must never exceed UB (the bug's signature).
  CHECK(out.lower_bound <= out.upper_bound + (1e-6 * std::abs(ref)));
  // Cut slopes are physical water values ρ·g.
  CHECK(out.slopes_checked > 0);
  CHECK(out.max_slope_rel_err < 1e-6);
}

}  // namespace

TEST_CASE("SDDP ruiz cuts - LB == UB == single-LP optimum")  // NOLINT
{
  SUBCASE("ruiz, auto reservoir scales")
  {
    check_sddp_matches_single_lp("ruiz");
  }
  SUBCASE("ruiz, scale_objective = 1")
  {
    check_sddp_matches_single_lp("ruiz", "", 1.0);
  }
  SUBCASE("row_max (src/dep scales equal) stays exact")
  {
    check_sddp_matches_single_lp("row_max");
  }
}

TEST_CASE(  // NOLINT
    "SDDP ruiz cuts - explicit per-reservoir variable_scales")
{
  // Distinct explicit energy scales per reservoir (1e-1 .. 1e2):
  // ruiz then multiplies independent factors onto each reservoir's
  // `efin` (source) and next-phase `sini` (dependent) columns.
  constexpr std::string_view scales =
      R"(,"variable_scales":[)"
      R"({"class_name":"Reservoir","variable":"energy","uid":400,"scale":10.0},)"
      R"({"class_name":"Reservoir","variable":"energy","uid":401,"scale":100.0},)"
      R"({"class_name":"Reservoir","variable":"energy","uid":402,"scale":0.1}])";
  SUBCASE("ruiz")
  {
    check_sddp_matches_single_lp("ruiz", scales);
  }
  SUBCASE("none")
  {
    check_sddp_matches_single_lp("none", scales);
  }
}
