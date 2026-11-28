// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
// Risk-averse planner: model_options.risk_measure = "cvar" (mean-CVaR in
// the Rockafellar–Uryasev form over the scenarios of a monolithic LP).
#include <algorithm>
#include <array>
#include <filesystem>
#include <format>
#include <fstream>
#include <numeric>
#include <sstream>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/risk_measure_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace risk_measure_test
{
namespace
{

constexpr double k_hours = 8760.0;
constexpr double k_gcost = 50.0;
constexpr double k_voll = 1000.0;
/// Shortage saved per MW and year by one more MW of peaker.
constexpr double k_rent = (k_voll - k_gcost) * k_hours;
/// Peaker annuity: 0.35 of the yearly shortage saving.  Risk-neutral, the
/// peaker covers every load level reached with probability >= 0.35.
constexpr double k_capcost = 0.35 * k_rent;

constexpr std::array<double, 3> k_load {100.0, 150.0, 200.0};
constexpr std::array<double, 3> k_prob {0.5, 0.3, 0.2};

/// Screening toy (one bus, one 8760-h block, 200 MW load, an existing
/// 100 MW free hydro unit available 100 / 50 / 0 % in three hydrologies
/// with p = 0.5 / 0.3 / 0.2, so the residual load is 100 / 150 / 200 MW;
/// VOLL 1000 $/MWh) and one candidate peaker (50 $/MWh, annuity
/// 0.35 · 950 · 8760 $/MW-yr).
/// `scale_objective = 1`: the objective is in $.
std::string screening_case(const std::string& risk_options,
                           bool integer = false)
{
  std::string scen;
  std::string dem;
  for (std::size_t s = 0; s < k_load.size(); ++s) {
    scen += std::format(R"({}{{"uid": {}, "probability_factor": {}}})",
                        s == 0 ? "" : ", ",
                        s + 1,
                        k_prob[s]);
    // Residual load = 200 MW − hydro availability (100 / 50 / 0 MW).
    dem += std::format(
        "{}[[{}]]", s == 0 ? "" : ", ", (200.0 - k_load[s]) / 100.0);
  }
  return std::format(
      R"json({{
  "options": {{
    "model_options": {{
      "use_single_bus": true, "scale_objective": 1,
      "demand_fail_cost": {} {}
    }}
  }},
  "simulation": {{
    "block_array": [ {{"uid": 1, "duration": 8760}} ],
    "stage_array": [ {{"uid": 1, "first_block": 0, "count_block": 1}} ],
    "scenario_array": [ {} ]
  }},
  "system": {{
    "name": "cvar_screening",
    "bus_array": [ {{"uid": 1, "name": "b1"}} ],
    "demand_array": [
      {{"uid": 1, "name": "d1", "bus": "b1", "lmax": 200}}
    ],
    "generator_profile_array": [
      {{"uid": 1, "name": "hydro_avail", "generator": "hydro",
        "profile": [{}]}}
    ],
    "generator_array": [
      {{"uid": 1, "name": "peaker", "bus": "b1", "gcost": {},
        "capacity": 0, "expcap": {}, "expmod": {},
        "integer_expmod": {}, "annual_capcost": {}}},
      {{"uid": 2, "name": "hydro", "bus": "b1", "gcost": 0,
        "capacity": 100}}
    ]
  }}
}})json",
      k_voll,
      risk_options,
      scen,
      dem,
      k_gcost,
      integer ? 50.0 : 1.0,
      integer ? 10.0 : 500.0,
      integer ? "true" : "false",
      k_capcost);
}

std::string cvar_options(double alpha, double weight)
{
  return std::format(
      R"(, "risk_measure": "cvar", "cvar_alpha": {}, "cvar_weight": {})",
      alpha,
      weight);
}

/// Scenario cost at peaker capacity @p x: dispatch + shortage.
double scenario_cost(double load, double x)
{
  return (k_gcost * std::min(load, x) * k_hours)
      + (k_voll * std::max(load - x, 0.0) * k_hours);
}

struct Solved
{
  double obj {0.0};
  double capacity {0.0};
  bool has_cvar {false};
  CvarLayout layout {};
  CvarSolution cvar {};
  std::vector<double> col_sol {};
};

Solved solve(const std::string& json)
{
  auto planning = daw::json::from_json<Planning>(json);
  PlanningLP planning_lp(std::move(planning));
  const auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  auto& system_lp = planning_lp.systems().front().front();
  auto& li = system_lp.linear_interface();
  Solved out {.obj = li.get_obj_value()};
  const auto sol = li.get_col_sol();
  out.col_sol.assign(sol.begin(), sol.end());
  const auto& stage = planning_lp.simulation().stages().front();
  for (const auto& gen : system_lp.elements<GeneratorLP>()) {
    if (const auto col = gen.capacity_col_at(stage); col && gen.uid() == 1) {
      out.capacity = sol[*col];
    }
  }
  if (const auto& layout = system_lp.cvar_layout()) {
    out.has_cvar = true;
    out.layout = *layout;
    out.cvar = evaluate_cvar(out.layout, sol, li.get_row_dual());
  }
  return out;
}

double expected_cost(double x)
{
  double e = 0.0;
  for (std::size_t s = 0; s < k_load.size(); ++s) {
    e += k_prob[s] * scenario_cost(k_load[s], x);
  }
  return e;
}

/// CVaR_α of the discrete scenario costs at capacity @p x (sorted tail).
double cvar_cost(double x, double alpha)
{
  std::vector<std::pair<double, double>> cp;
  cp.reserve(k_load.size());
  for (std::size_t s = 0; s < k_load.size(); ++s) {
    cp.emplace_back(scenario_cost(k_load[s], x), k_prob[s]);
  }
  std::ranges::sort(cp, std::greater {});
  double left = 1.0 - alpha;
  double acc = 0.0;
  for (const auto& [c, p] : cp) {
    const double take = std::min(p, left);
    acc += take * c;
    left -= take;
    if (left <= 0.0) {
      break;
    }
  }
  return acc / (1.0 - alpha);
}

}  // namespace
}  // namespace risk_measure_test

using namespace risk_measure_test;

TEST_CASE("risk_measure option resolution and JSON")  // NOLINT
{
  SUBCASE("default is expected, alpha 0.95, weight 0.5")
  {
    const PlanningOptionsLP opts {PlanningOptions {}};
    CHECK(opts.risk_measure() == RiskMeasure::expected);
    CHECK(opts.cvar_alpha() == doctest::Approx(0.95));
    CHECK(opts.cvar_weight() == doctest::Approx(0.5));
  }
  SUBCASE("model_options JSON round trip")
  {
    const auto mo = daw::json::from_json<ModelOptions>(
        R"({"risk_measure": "cvar", "cvar_alpha": 0.9, "cvar_weight": 0.25})");
    CHECK(mo.risk_measure.value_or("") == "cvar");
    CHECK(mo.has_any());
    const auto back = daw::json::to_json(mo);
    CHECK(back.contains(R"("risk_measure":"cvar")"));
    CHECK(back.contains(R"("cvar_alpha":0.9)"));
    PlanningOptions po;
    po.model_options = mo;
    const PlanningOptionsLP opts {std::move(po)};
    CHECK(opts.risk_measure() == RiskMeasure::cvar);
    CHECK(opts.cvar_alpha() == doctest::Approx(0.9));
    CHECK(opts.cvar_weight() == doctest::Approx(0.25));
  }
  SUBCASE("merge and covers")
  {
    ModelOptions a;
    ModelOptions b;
    b.risk_measure = "cvar";
    b.cvar_alpha = 0.8;
    b.cvar_weight = 1.0;
    CHECK_FALSE(a.covers(b));
    a.merge(b);
    CHECK(a.covers(b));
    CHECK(a.cvar_alpha.value_or(0.0) == doctest::Approx(0.8));
  }
}

TEST_CASE("validate_planning gates risk_measure = cvar")  // NOLINT
{
  const auto has = [](const ValidationResult& r, std::string_view needle)
  {
    return std::ranges::any_of(
        r.errors, [&](const std::string& e) { return e.contains(needle); });
  };
  SUBCASE("unknown name and out-of-range alpha / weight")
  {
    Planning planning;
    planning.options.model_options.risk_measure = "evar";
    planning.options.model_options.cvar_alpha = 1.0;
    planning.options.model_options.cvar_weight = 1.5;
    const auto r = validate_planning(planning);
    CHECK(has(r, "risk_measure"));
    CHECK(has(r, "cvar_alpha"));
    CHECK(has(r, "cvar_weight"));
  }
  SUBCASE("sddp and cascade are rejected")
  {
    Planning planning;
    planning.options.model_options.risk_measure = "cvar";
    planning.options.method = MethodType::sddp;
    CHECK(has(validate_planning(planning), "only supported by the monolithic"));
    planning.options.method = MethodType::cascade;
    CHECK(has(validate_planning(planning), "only supported by the monolithic"));
  }
  SUBCASE("cvar in a cascade level is rejected")
  {
    Planning planning;
    planning.options.cascade_options.model_options.risk_measure = "cvar";
    CHECK(has(validate_planning(planning), "only supported by the monolithic"));
  }
  SUBCASE("more than one phase is rejected")
  {
    Planning planning;
    planning.options.model_options.risk_measure = "cvar";
    planning.simulation.phase_array = {
        Phase {.uid = 1, .first_stage = 0, .count_stage = 1},
        Phase {.uid = 2, .first_stage = 1, .count_stage = 1},
    };
    CHECK(has(validate_planning(planning), "single phase"));
  }
  SUBCASE("expected under sddp is fine")
  {
    Planning planning;
    planning.options.model_options.risk_measure = "expected";
    planning.options.method = MethodType::sddp;
    CHECK_FALSE(has(validate_planning(planning), "risk_measure"));
  }
}

TEST_CASE("add_cvar_to_lp builds the Rockafellar-Uryasev rows")  // NOLINT
{
  // Two scenarios, two costed columns each plus one shared capacity
  // column inside the first scenario's range.
  LinearProblem lp;
  const auto x1 = lp.add_col({.uppb = 10.0, .cost = 0.25 * 4.0});
  const auto cap = lp.add_col({
      .uppb = 5.0,
      .cost = 7.0,
      .class_name = "Generator",
      .variable_name = "capacost",
      .variable_uid = Uid {1},
  });
  const auto x2 = lp.add_col({.uppb = 10.0, .cost = 0.75 * 2.0});
  lp.add_obj_constant(0.75 * 3.0);  // scenario 2 constant k_2 = 3
  const std::vector<CvarScenarioInput> in {
      {.uid = make_uid<Scenario>(1),
       .probability = 0.25,
       .col_ranges = {{0, 2}},
       .obj_constant = 0.0},
      {.uid = make_uid<Scenario>(2),
       .probability = 0.75,
       .col_ranges = {{2, 3}},
       .obj_constant = 0.75 * 3.0},
  };
  const double alpha = 0.8;
  const double weight = 0.4;
  const auto layout = add_cvar_to_lp(
      lp, in, alpha, weight, 1.0, make_uid<Scene>(1), make_uid<Phase>(1));

  CHECK(layout.mass == doctest::Approx(1.0));
  CHECK(layout.adjusted_cols == 2);
  CHECK(layout.shared_cols == 1);
  REQUIRE(layout.scenarios.size() == 2);

  // Costs: scenario columns × (1 − λ); shared capacity column untouched.
  CHECK(lp.col_at(x1).cost == doctest::Approx(0.6 * 1.0));
  CHECK(lp.col_at(x2).cost == doctest::Approx(0.6 * 1.5));
  CHECK(lp.col_at(cap).cost == doctest::Approx(7.0));
  // η free with cost λ·P; u_s ≥ 0 with cost λ p_s / (1 − α).
  const auto& eta = lp.col_at(layout.eta_col);
  CHECK(eta.cost == doctest::Approx(weight));
  CHECK(eta.lowb <= -1e20);
  const auto& u2 = lp.col_at(layout.scenarios[1].excess_col);
  CHECK(u2.cost == doctest::Approx(weight * 0.75 / (1.0 - alpha)));
  CHECK(u2.lowb == doctest::Approx(0.0));
  // Row s: u_s + η − Σ d_j x_j ≥ k_s with d_j = c_j / p_s.
  const auto& r1 = lp.row_at(layout.scenarios[0].row);
  CHECK(r1.lowb == doctest::Approx(0.0));
  CHECK(r1[x1] == doctest::Approx(-4.0));
  CHECK(r1[layout.eta_col] == doctest::Approx(1.0));
  CHECK(r1[layout.scenarios[0].excess_col] == doctest::Approx(1.0));
  CHECK(r1.cmap.size() == 3);  // capacost is not in the scenario cost
  const auto& r2 = lp.row_at(layout.scenarios[1].row);
  CHECK(r2.lowb == doctest::Approx(3.0));
  CHECK(r2[x2] == doctest::Approx(-2.0));
  // Only (1 − λ) of the scenario constants stays in the objective.
  CHECK(lp.get_obj_constant() == doctest::Approx(0.6 * 0.75 * 3.0));

  SUBCASE("invalid parameters throw")
  {
    LinearProblem lp2;
    CHECK_THROWS_AS(
        (void)add_cvar_to_lp(
            lp2, in, 1.0, 0.5, 1.0, make_uid<Scene>(1), make_uid<Phase>(1)),
        std::invalid_argument);
    CHECK_THROWS_AS(
        (void)add_cvar_to_lp(
            lp2, in, 0.5, -0.1, 1.0, make_uid<Scene>(1), make_uid<Phase>(1)),
        std::invalid_argument);
    const std::vector<CvarScenarioInput> none {
        {.uid = make_uid<Scenario>(1), .probability = 0.0},
    };
    CHECK_THROWS_AS(
        (void)add_cvar_to_lp(
            lp2, none, 0.5, 0.5, 1.0, make_uid<Scene>(1), make_uid<Phase>(1)),
        std::invalid_argument);
  }
}

TEST_CASE("risk_measure.csv line format")  // NOLINT
{
  const CvarLayout layout {.alpha = 0.9, .weight = 0.5};
  const CvarScenarioResult r {
      .uid = make_uid<Scenario>(3),
      .probability = 0.25,
      .cost = 10.0,
      .excess = 2.0,
      .dual = 0.125,
      .risk_probability = 0.25,
      .risk_factor = 1.0,
  };
  CHECK(risk_measure_csv_header().starts_with("scene,phase,scenario,"));
  CHECK(risk_measure_csv_line(1, 2, 8.0, layout, r)
        == "1,2,3,0.25,10,8,2,0.125,0.25,1,0.9,0.5,cvar,cost,upper\n");
}

TEST_CASE("CVaR screening: expectation, tail and worst case")  // NOLINT
{
  SUBCASE("expected (default) builds no construct")
  {
    const auto e = solve(screening_case(""));
    CHECK_FALSE(e.has_cvar);
    CHECK(e.capacity == doctest::Approx(150.0));
    CHECK(e.obj
          == doctest::Approx((k_capcost * 150.0) + expected_cost(150.0))
                 .epsilon(1e-9));
  }

  SUBCASE("lambda = 0 reproduces the expected-cost plan")
  {
    const auto e = solve(screening_case(""));
    const auto c = solve(screening_case(cvar_options(0.95, 0.0)));
    REQUIRE(c.has_cvar);
    CHECK(c.capacity == doctest::Approx(e.capacity));
    CHECK(c.obj == doctest::Approx(e.obj).epsilon(1e-9));
    // Original columns keep their primal values.
    for (std::size_t j = 0; j < e.col_sol.size(); ++j) {
      CHECK(c.col_sol[j] == doctest::Approx(e.col_sol[j]).epsilon(1e-9));
    }
  }

  SUBCASE("lambda = 1, alpha = 0 is the expectation")
  {
    const auto c = solve(screening_case(cvar_options(0.0, 1.0)));
    CHECK(c.capacity == doctest::Approx(150.0));
    CHECK(c.obj
          == doctest::Approx((k_capcost * 150.0) + expected_cost(150.0))
                 .epsilon(1e-9));
  }

  SUBCASE("lambda = 1, alpha = 0.5: tail {s2, s3} -> peaker to 200 MW")
  {
    const auto c = solve(screening_case(cvar_options(0.5, 1.0)));
    CHECK(c.capacity == doctest::Approx(200.0));
    CHECK(c.obj
          == doctest::Approx((k_capcost * 200.0) + cvar_cost(200.0, 0.5))
                 .epsilon(1e-9));
  }

  SUBCASE("lambda = 0.5, alpha = 0.5: blend keeps 150 MW")
  {
    // Risk-adjusted P(load > 150) = 0.5·0.2 + 0.5·0.4 = 0.3 < 0.35.
    const auto c = solve(screening_case(cvar_options(0.5, 0.5)));
    CHECK(c.capacity == doctest::Approx(150.0));
    CHECK(c.obj
          == doctest::Approx((k_capcost * 150.0) + (0.5 * expected_cost(150.0))
                             + (0.5 * cvar_cost(150.0, 0.5)))
                 .epsilon(1e-9));
  }

  SUBCASE("lambda = 0.8, alpha = 0.5: 0.2·0.2 + 0.8·0.4 = 0.36 -> 200 MW")
  {
    const auto c = solve(screening_case(cvar_options(0.5, 0.8)));
    CHECK(c.capacity == doctest::Approx(200.0));
  }

  SUBCASE("alpha -> 1 approaches the worst case")
  {
    const auto c = solve(screening_case(cvar_options(0.999, 1.0)));
    CHECK(c.capacity == doctest::Approx(200.0));
    // CVaR_0.999 = worst scenario cost (s3 at 200 MW of peaker).
    CHECK(c.obj
          == doctest::Approx((k_capcost * 200.0) + scenario_cost(200.0, 200.0))
                 .epsilon(1e-9));
  }

  SUBCASE("integer modules (MIP) give the same plan")
  {
    const auto c =
        solve(screening_case(cvar_options(0.5, 1.0), /*integer=*/true));
    CHECK(c.capacity == doctest::Approx(200.0));
    const auto e = solve(screening_case("", /*integer=*/true));
    CHECK(e.capacity == doctest::Approx(150.0));
  }
}

TEST_CASE("CVaR duals are a risk-adjusted probability measure")  // NOLINT
{
  for (const auto& [alpha, weight] :
       std::vector<std::pair<double, double>> {
           {0.5, 1.0},
           {0.5, 0.5},
           {0.9, 0.7},
           {0.0, 1.0},
       })
  {
    CAPTURE(alpha);
    CAPTURE(weight);
    const auto c = solve(screening_case(cvar_options(alpha, weight)));
    REQUIRE(c.has_cvar);
    const auto& sc = c.cvar.scenarios;
    REQUIRE(sc.size() == 3);
    double sum_mu = 0.0;
    double sum_pi = 0.0;
    for (std::size_t s = 0; s < sc.size(); ++s) {
      CAPTURE(s);
      // 0 ≤ μ_s ≤ λ p_s / (1 − α).
      CHECK(sc[s].dual >= -1e-9);
      CHECK(sc[s].dual <= (weight * k_prob[s] / (1.0 - alpha)) + 1e-9);
      if (sc[s].risk_probability > 1e-12) {
        // A scenario with zero risk-adjusted weight (λ = 1, outside the
        // tail) has an unpriced, arbitrary dispatch.
        CHECK(sc[s].cost
              == doctest::Approx(scenario_cost(k_load[s], c.capacity))
                     .epsilon(1e-9));
      }
      sum_mu += sc[s].dual;
      sum_pi += sc[s].risk_probability;
      CHECK(sc[s].risk_factor
            == doctest::Approx(sc[s].risk_probability / k_prob[s]));
    }
    CHECK(sum_mu == doctest::Approx(weight));
    CHECK(sum_pi == doctest::Approx(1.0));
    // η is the value-at-risk: u_s = max(O_s − η, 0).
    for (const auto& r : sc) {
      CHECK(r.excess
            == doctest::Approx(std::max(r.cost - c.cvar.eta, 0.0))
                   .epsilon(1e-6)
                   .scale(1e6));
    }
  }
}

TEST_CASE("risk_measure.csv is written by PlanningLP::write_out")  // NOLINT
{
  const auto tmpdir =
      std::filesystem::temp_directory_path() / "gtopt_test_risk_measure";
  std::filesystem::remove_all(tmpdir);
  std::filesystem::create_directories(tmpdir);
  auto planning =
      daw::json::from_json<Planning>(screening_case(cvar_options(0.5, 1.0)));
  planning.options.output_directory = tmpdir.string();
  PlanningLP planning_lp(std::move(planning));
  REQUIRE(planning_lp.resolve().has_value());
  planning_lp.write_out();
  std::ifstream f(tmpdir / "risk_measure.csv");
  REQUIRE(f.good());
  std::stringstream ss;
  ss << f.rdbuf();
  const auto csv = ss.str();
  CHECK(csv.starts_with(risk_measure_csv_header()));
  CHECK(std::ranges::count(csv, '\n') == 4);
  std::filesystem::remove_all(tmpdir);
}

TEST_CASE("validate_planning: tail ranking and trim")  // NOLINT
{
  const auto has = [](const ValidationResult& r, std::string_view needle)
  {
    return std::ranges::any_of(
        r.errors, [&](const std::string& e) { return e.contains(needle); });
  };
  SUBCASE("cost ranking: lower tail or trim are not convex")
  {
    Planning planning;
    planning.options.model_options.risk_measure = "cvar";
    planning.options.model_options.risk_tail = "lower";
    CHECK(has(validate_planning(planning), "not convex"));
    planning.options.model_options.risk_tail = "upper";
    planning.options.model_options.risk_measure = "trim";
    CHECK(has(validate_planning(planning), "not convex"));
  }
  SUBCASE("unknown names")
  {
    Planning planning;
    planning.options.model_options.risk_rank_by = "hydro";
    planning.options.model_options.risk_tail = "left";
    const auto r = validate_planning(planning);
    CHECK(has(r, "risk_rank_by"));
    CHECK(has(r, "risk_tail"));
  }
  SUBCASE("index ranking needs Scenario.risk_index")
  {
    Planning planning;
    planning.options.model_options.risk_measure = "trim";
    planning.options.model_options.risk_rank_by = "index";
    planning.options.model_options.cvar_alpha = 0.0;
    planning.simulation.scenario_array = {
        Scenario {.uid = 1, .probability_factor = 0.5, .risk_index = 1.0},
        Scenario {.uid = 2, .probability_factor = 0.5},
    };
    const auto r = validate_planning(planning);
    CHECK(has(r, "scenario uid 2 has no risk_index"));
    CHECK(has(r, "trim needs alpha > 0"));
  }
}

TEST_CASE("tail_risk_weights: exogenous cvar and trim")  // NOLINT
{
  // Index = hydrology wetness (high = wet).  p = 0.5 / 0.3 / 0.2.
  const std::array<double, 3> p {0.5, 0.3, 0.2};
  const std::array<double, 3> wet {3.0, 2.0, 1.0};  // s3 driest
  SUBCASE("cvar, lower tail (dry), alpha 0.8, lambda 1: all on s3")
  {
    const auto q =
        tail_risk_weights(p, wet, 0.8, 1.0, RiskMeasure::cvar, RiskTail::lower);
    CHECK(q[0] == doctest::Approx(0.0));
    CHECK(q[1] == doctest::Approx(0.0));
    CHECK(q[2] == doctest::Approx(1.0));
  }
  SUBCASE("cvar, upper tail (wet), boundary split, lambda 0.5")
  {
    // tail mass 0.6: s1 0.5 + 0.1 of s2.
    const auto q =
        tail_risk_weights(p, wet, 0.4, 0.5, RiskMeasure::cvar, RiskTail::upper);
    CHECK(q[0] == doctest::Approx((0.5 * 0.5) + (0.5 * 0.5 / 0.6)));
    CHECK(q[1] == doctest::Approx((0.5 * 0.3) + (0.5 * 0.1 / 0.6)));
    CHECK(q[2] == doctest::Approx(0.5 * 0.2));
    CHECK(q[0] + q[1] + q[2] == doctest::Approx(1.0));
  }
  SUBCASE("trim the wettest 50 %, lambda 1")
  {
    const auto q =
        tail_risk_weights(p, wet, 0.5, 1.0, RiskMeasure::trim, RiskTail::upper);
    CHECK(q[0] == doctest::Approx(0.0));
    CHECK(q[1] == doctest::Approx(0.6));
    CHECK(q[2] == doctest::Approx(0.4));
  }
  SUBCASE("errors")
  {
    const std::array<double, 2> short_index {1.0, 2.0};
    CHECK_THROWS_AS(
        (void)tail_risk_weights(
            p, short_index, 0.5, 0.5, RiskMeasure::cvar, RiskTail::upper),
        std::invalid_argument);
    CHECK_THROWS_AS((void)tail_risk_weights(
                        p, wet, 0.0, 0.5, RiskMeasure::trim, RiskTail::upper),
                    std::invalid_argument);
    const std::array<double, 3> nan_index {1.0, std::nan(""), 0.0};
    CHECK_THROWS_AS(
        (void)tail_risk_weights(
            p, nan_index, 0.5, 0.5, RiskMeasure::cvar, RiskTail::upper),
        std::invalid_argument);
  }
}

TEST_CASE("index-ranked tail re-weights the screening LP")  // NOLINT
{
  // risk_index = load level: the upper tail is the 200 MW scenario, as the
  // endogenous cost ranking finds; same plan, no extra rows.
  auto json = screening_case(R"(, "risk_measure": "cvar", "cvar_alpha": 0.5,
      "cvar_weight": 1.0, "risk_rank_by": "index", "risk_tail": "upper")");
  for (std::size_t s = 0; s < k_load.size(); ++s) {
    const auto key =
        std::format(R"("uid": {}, "probability_factor": {})", s + 1, k_prob[s]);
    const auto pos = json.find(key);
    REQUIRE(pos != std::string::npos);
    json.insert(pos + key.size(),
                std::format(R"(, "risk_index": {})", k_load[s]));
  }
  const auto c = solve(json);
  REQUIRE(c.has_cvar);
  CHECK_FALSE(c.layout.endogenous);
  CHECK(c.capacity == doctest::Approx(200.0));
  CHECK(c.obj
        == doctest::Approx((k_capcost * 200.0) + cvar_cost(200.0, 0.5))
               .epsilon(1e-9));
  REQUIRE(c.has_cvar);
  double sum = 0.0;
  for (const auto& r : c.cvar.scenarios) {
    sum += r.risk_probability;
  }
  CHECK(sum == doctest::Approx(1.0));
  CHECK(c.cvar.scenarios[2].risk_probability == doctest::Approx(0.4));

  SUBCASE("trim the upper index tail drops the high-load scenarios")
  {
    auto trim = json;
    const auto pos = trim.find(R"("risk_measure": "cvar")");
    trim.replace(pos, 22, R"("risk_measure": "trim")");
    const auto t = solve(trim);
    // trimming the top 50 % of load leaves only the 100 MW scenario.
    CHECK(t.capacity == doctest::Approx(100.0));
  }
}
