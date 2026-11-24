// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_losses_penalty_sink.cpp — loss columns as an energy SINK
// under a PENALTY incentive, and the overflow-tier price
// (`loss_overflow_cost`, line_losses.cpp `overflow_unit_cost`) that
// closes the sink opened by the decoupled-envelope overflow ladder.
//
// Mechanism (the CEN CPF case).  A reserve requirement that FALLS with
// total generation, short and priced at `urcost`:
//
//   R ≥ alpha − β·D,   D = Σ generation − Σ battery charge
//   Σ provision + shortage ≥ R,   shortage priced at urcost
//
// (`ReserveZone.load_term = "endogenous_generation"`, reserve_zone.hpp).
// While the zone is short every generated MWh earns β·urcost, so the
// bus price is `gcost − β·urcost` — NEGATIVE with all-positive costs —
// and every MW the LP can destroy is worth `β·urcost − gcost`.
//
//   g1 (10 $/MWh) ── bus1 ══ l12 ══ bus2 ── d2 = 80 MW
//   res (5 MW, the only CPF provider)
//   β = 0.0144, urcost = 10 000  →  credit 144, incentive 134 $/MWh
//   l12: c = R/V² = 2e-4, loss_envelope = 100 MW (c·env² = 2 MW),
//        tmax = 1000 MW (10× rating, c·fmax² = 200 MW) or 100 MW.
//
// Two requirement levels:
//   * `kSinkAlphaDeep`: short at every reachable D — the incentive never
//     ends, every sink saturates at its CAP;
//   * `kSinkAlphaKink`: the shortage is cured after 100 MW of extra
//     generation — an unguarded sink ends INTERIOR, which is the CEN
//     picture (≈ 800 MW "burned in line losses", bus prices flat at −ε).
//
// Burned MW = Σ generation − served load, tmax = 10× rating, CPLEX.
// "off" = `loss_overflow_cost = 0` (the overflow ladder of b7580b367
// as merged), "on" = the default price, the case's demand_fail_cost
// (1e5 $/MWh here; any price above the 134 $/MWh incentive gives the
// same burned MW):
//
//   mode                     ε      deep off  deep on   kink off  kink on
//   tangent_signed_flow S=1  0–10     200       2.0       100       2.0
//   tangent_signed_flow S=4  0–10     200       2.0       100       2.0
//   bidirectional            0–10     384       2.65      100       2.65
//   piecewise + midpoint     0–10     384¹      2.65      100       2.65
//   piecewise + tangent      0–10     200       2.0       100       2.0
//   piecewise_direct         —        384²      2.65      100       2.65
//   linear                   —        344       344       100       100
//   tangent_signed_flow+SOS2 0–10       1.32    1.32        1.32    1.32
//
//   λ1 with the guard on: −134 (the true price) in every row but linear;
//   off, λ1 = −ε while the kink sink is interior.
//   ¹ was 400 before the midpoint loss row became two-sided (it was a
//     one-sided `≥`, so the loss column was free up to c·fmax²).
//   ² was 383 when piecewise_direct ignored loss_envelope.
//
// Reading:
//   1. ε is a PRICE FLOOR, not a blocker: while ε < incentive an
//      unguarded sink runs until the incentive is exhausted and pins
//      λ = −ε; only ε > incentive closes it.
//   2. No pure-LP chord closes the overflow sink: the feasible set is
//      convex and contains (±fmax, c·fmax²), hence (0, c·fmax²).  Only a
//      price on the overflow tier (or SOS2) does.  With it the cap is
//      back to the in-envelope residual c·env² (burned ≤ physical +
//      c·env², governed by ε / SOS2 as before the ladder existed).
//   3. The price is a threshold: the tier burns again for an incentive
//      above `loss_overflow_cost`.
//   4. Genuine overloads stay feasible with a physical loss, but pay the
//      price on their loss above c·env²: λ2 rises by ≈ E·c·(b_{j−1}+b_j)
//      (measured below; unavoidable for any LP guard).
//   5. `linear` has no loss column: still exposed (warned at LP build).

#include <algorithm>
#include <cmath>
#include <format>
#include <optional>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{
namespace penalty_sink_test
{

constexpr double kSinkRating = 100.0;  ///< loss_envelope [MW]
constexpr double kSinkHeadroom = 10.0 * kSinkRating;  ///< tmax of an EL=0 line
constexpr double kSinkV = 100.0;
constexpr double kSinkR = 2.0;
constexpr double kSinkC =
    kSinkR / (kSinkV * kSinkV);  ///< 2e-4 → c·rating = 2 %
constexpr double kSinkLoad = 80.0;
constexpr double kSinkGcost = 10.0;
constexpr double kSinkUrCost = 10000.0;
constexpr double kSinkBeta = 0.0144;  ///< credit = β·urcost = 144 $/MWh
constexpr double kSinkIncentive =
    (kSinkBeta * kSinkUrCost) - kSinkGcost;  ///< 134 $/MWh
constexpr double kSinkProv = 5.0;  ///< urmax of the only CPF provider
constexpr double kSinkAlphaDeep = 20.0;
/// Shortage cured once D = load + 100 MW.
constexpr double kSinkAlphaKink = kSinkProv + (kSinkBeta * (kSinkLoad + 100.0));
constexpr double kSinkCapEnv = kSinkC * kSinkRating * kSinkRating;  ///< 2 MW
constexpr double kSinkCapFmax =
    kSinkC * kSinkHeadroom * kSinkHeadroom;  ///< 200 MW

struct SinkCase
{
  std::string_view mode {"tangent_signed_flow"};
  double eps {0.1};
  double tmax {kSinkHeadroom};
  double alpha {kSinkAlphaDeep};
  std::string_view layout {};
  int secant {1};
  bool sos2 {false};
  std::string_view load_term {"endogenous_generation"};
  double load {kSinkLoad};
  /// ``model_options.loss_overflow_cost``; unset → gtopt default.
  std::optional<double> overflow_cost {};
  /// ``model_options.demand_fail_cost``; nullopt → unset.  It is also the
  /// default ``loss_overflow_cost``.
  std::optional<double> fail_cost {1.0e5};
};

[[nodiscard]] std::string sink_json(const SinkCase& c)
{
  std::string line_extra;
  if (!c.layout.empty()) {
    line_extra += std::format(R"(, "loss_pwl_layout": "{}")", c.layout);
  }
  if (c.secant > 1) {
    line_extra += std::format(R"(, "loss_secant_segments": {})", c.secant);
  }
  if (c.sos2) {
    line_extra += R"(, "loss_use_sos2": true)";
  }
  std::string opt_extra;
  if (c.overflow_cost) {
    opt_extra = std::format(R"(, "loss_overflow_cost": {})", *c.overflow_cost);
  }
  if (c.fail_cost) {
    opt_extra += std::format(R"(, "demand_fail_cost": {})", *c.fail_cost);
  }
  return std::format(
      R"({{
  "options": {{
    "model_options": {{"use_single_bus": false, "use_kirchhoff": true,
      "scale_objective": 1.0,
      "loss_cost_eps": {}{}}}}},
  "simulation": {{
    "block_array": [{{"uid": 1, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "penalty_sink",
    "bus_array": [{{"uid": 1, "name": "b1"}}, {{"uid": 2, "name": "b2"}}],
    "line_array": [
      {{"uid": 1, "name": "l12", "bus_a": 1, "bus_b": 2, "reactance": 0.01,
        "voltage": {}, "resistance": {}, "line_losses_mode": "{}",
        "loss_segments": 6, "loss_envelope": {},
        "tmax_ab": {}, "tmax_ba": {}, "capacity": {}{}}}],
    "demand_array": [{{"uid": 1, "name": "d2", "bus": 2, "lmax": {}}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": 1, "gcost": {}, "capacity": 5000}},
      {{"uid": 2, "name": "res", "bus": 1, "gcost": 500, "capacity": {}}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf", "urcost": {},
      "urreq_planes": [{{"alpha": {}, "demand_coef": {}}}],
      "demand_load_factor": 1.0, "load_term": "{}"}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": {},
      "ur_provision_factor": 1}}]
  }}
}})",
      c.eps,
      opt_extra,
      kSinkV,
      kSinkR,
      c.mode,
      kSinkRating,
      c.tmax,
      c.tmax,
      c.tmax,
      line_extra,
      c.load,
      kSinkGcost,
      kSinkProv,
      kSinkUrCost,
      c.alpha,
      -kSinkBeta,
      c.load_term,
      kSinkProv);
}

struct SinkResult
{
  double load {0.0};  ///< served load [MW]
  double burned {0.0};  ///< Σ generation − served load [MW]
  double flow {0.0};  ///< |net flow| on l12 [MW]
  double circ {0.0};  ///< min(f⁺, f⁻): phantom circulation [MW]
  double loss {0.0};  ///< reported loss columns [MW]
  double lambda1 {0.0};  ///< bus-1 price [$/MWh]
  double lambda2 {0.0};  ///< bus-2 price [$/MWh]

  /// Fictitious loss: burned MW above the physical R·f²/V².
  [[nodiscard]] double fictitious() const
  {
    return burned - (kSinkC * flow * flow);
  }
};

[[nodiscard]] SinkResult sink_solve(const SinkCase& c)
{
  LpMatrixOptions matrix_opts;
  if (c.sos2) {
    // Only the CPLEX plugin implements add_sos2 (see sos2_available()).
    matrix_opts.solver_name = "cplex";
  }
  PlanningLP plp(parse_planning_json(sink_json(c)), matrix_opts);
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  REQUIRE(plp.resolve().has_value());
  const auto sol = li.get_col_sol();
  const auto duals = li.get_row_dual();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();

  SinkResult r;
  double gen = 0.0;
  for (const auto& g : sys.elements<GeneratorLP>()) {
    gen += sol[g.generation_cols_at(scenario, stage).at(buid)];
  }
  r.load = sol
      [sys.elements<DemandLP>().front().load_cols_at(scenario, stage).at(buid)];
  r.burned = gen - r.load;
  const auto& line = sys.elements<LineLP>().front();
  const auto val = [&](const auto& cols)
  {
    const auto it = cols.find(buid);
    return it == cols.end() ? 0.0 : sol[it->second];
  };
  const double fp = val(line.flowp_cols_at(scenario, stage));
  const double fn = val(line.flown_cols_at(scenario, stage));
  r.flow = std::abs(val(line.flows_cols_at(scenario, stage)) + fp - fn);
  r.circ = std::min(fp, fn);
  r.loss = val(line.lossp_cols_at(scenario, stage))
      + val(line.lossn_cols_at(scenario, stage));
  const auto& buses = sys.elements<BusLP>();
  r.lambda1 = duals[buses[0].balance_rows_at(scenario, stage).at(buid)];
  r.lambda2 = duals[buses[1].balance_rows_at(scenario, stage).at(buid)];
  return r;
}

[[nodiscard]] bool sos2_available()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver("cplex");
}

}  // namespace penalty_sink_test
}  // namespace

TEST_CASE(
    "penalty sink control: the endogenous CPF load term prices buses at "
    "gcost - beta*urcost, the exogenous term does not")  // NOLINT
{
  using namespace penalty_sink_test;
  // Lossless line: nothing can be burned, so this is the TRUE price of
  // the formulation — negative with all-positive costs.
  const auto endo = sink_solve({.mode = "none"});
  CHECK(endo.burned == doctest::Approx(0.0).epsilon(1e-6));
  CHECK(endo.lambda1 == doctest::Approx(-kSinkIncentive));
  CHECK(endo.lambda2 == doctest::Approx(-kSinkIncentive));

  // gtopt's default (`load_term: exogenous`): no generation column
  // reaches the requirement, the price is the marginal cost and the
  // loss is the physical one.
  const auto exo = sink_solve({.load_term = "exogenous"});
  CHECK(exo.lambda1 == doctest::Approx(kSinkGcost));
  CHECK(exo.lambda2 > kSinkGcost);
  CHECK(std::abs(exo.fictitious()) < 0.05);
}

TEST_CASE(
    "penalty sink eps sweep: the overflow price caps the sink at c*env^2 "
    "for every eps, without it eps is only a price floor")  // NOLINT
{
  using namespace penalty_sink_test;
  // Production mode (tangent_signed_flow, S = 1), requirement cured after
  // 100 MW of extra generation.
  for (const double eps : {0.0, 0.1, 1.0, 10.0}) {
    CAPTURE(eps);
    // REGRESSION GUARD: the default overflow price leaves only the
    // in-envelope residual (≤ c·env²), the shortage stays and the bus
    // keeps its true price.
    const auto r = sink_solve({.eps = eps, .alpha = kSinkAlphaKink});
    CHECK(r.burned <= kSinkCapEnv + 1e-6);
    CHECK(r.fictitious() <= kSinkCapEnv);
    CHECK(r.lambda1 == doctest::Approx(-kSinkIncentive));

    // Guard disabled (the ladder as merged): ε is a price FLOOR — the
    // sink cures the requirement (100 MW burned) and pins λ1 + λ2 = −2ε.
    const auto off =
        sink_solve({.eps = eps, .alpha = kSinkAlphaKink, .overflow_cost = 0.0});
    CHECK(off.burned == doctest::Approx(100.0).epsilon(1e-4));
    CHECK(off.fictitious() > 90.0);
    CHECK(off.lambda1 + off.lambda2
          == doctest::Approx(-2.0 * eps).epsilon(1e-3));
  }

  // ε above the incentive (134 $/MWh) also closes the in-envelope
  // residual: the loss is the physical one …
  const auto blocked = sink_solve({.eps = 200.0, .alpha = kSinkAlphaKink});
  CHECK(std::abs(blocked.fictitious()) < 0.05);
  CHECK(blocked.lambda1 == doctest::Approx(-kSinkIncentive));
  // … at the price of a 7 $/MWh loss toll on the receiving bus (−131.8
  // instead of the undistorted −138.7 of the SOS2 run).
  CHECK(blocked.lambda2 > blocked.lambda1 + 2.0);
}

TEST_CASE(
    "penalty sink cap: with the overflow price the cap is c*env^2 with or "
    "without headroom")  // NOLINT
{
  using namespace penalty_sink_test;
  // Requirement short at every reachable D, so any sink saturates.
  for (const int secant : {1, 4}) {
    for (const double eps : {0.0, 0.1, 1.0, 10.0}) {
      CAPTURE(secant);
      CAPTURE(eps);
      const auto wide = sink_solve({.eps = eps, .secant = secant});
      const auto tight =
          sink_solve({.eps = eps, .tmax = kSinkRating, .secant = secant});
      // REGRESSION GUARD: headroom no longer multiplies the cap.
      CHECK(wide.loss == doctest::Approx(kSinkCapEnv).epsilon(1e-4));
      CHECK(wide.burned == doctest::Approx(kSinkCapEnv).epsilon(1e-4));
      CHECK(tight.loss == doctest::Approx(kSinkCapEnv).epsilon(1e-4));
      CHECK(wide.lambda1 == doctest::Approx(-kSinkIncentive));

      // Guard disabled: the ladder lifts the cap to c·fmax² (×100).
      const auto off =
          sink_solve({.eps = eps, .secant = secant, .overflow_cost = 0.0});
      CHECK(off.loss == doctest::Approx(kSinkCapFmax).epsilon(1e-4));
    }
  }
}

TEST_CASE(
    "penalty sink price threshold: the overflow tier burns again only for "
    "an incentive above loss_overflow_cost")  // NOLINT
{
  using namespace penalty_sink_test;
  // Incentive 134 $/MWh: a price below it reopens the overflow sink, a
  // price above it closes it.
  const auto cheap = sink_solve({.overflow_cost = 0.5 * kSinkIncentive});
  CHECK(cheap.burned == doctest::Approx(kSinkCapFmax).epsilon(1e-4));
  const auto dear = sink_solve({.overflow_cost = 1.5 * kSinkIncentive});
  CHECK(dear.burned == doctest::Approx(kSinkCapEnv).epsilon(1e-4));
  CHECK(dear.lambda1 == doctest::Approx(-kSinkIncentive));
}

TEST_CASE(
    "penalty sink modes: the overflow price closes every loss-column "
    "mode, linear stays exposed")  // NOLINT
{
  using namespace penalty_sink_test;
  // ε = 0.1 (the gtopt default) where it applies.
  struct Row
  {
    std::string_view mode;
    std::string_view layout;
    double off_min;  ///< burned MW with headroom and the guard off
    double cap;  ///< burned MW bound with the guard on (any headroom)
  };
  for (const auto& row :
       {
           Row {
               .mode = "bidirectional",
               .layout = "",
               .off_min = 380.0,
               .cap = 2.7,
           },
           Row {
               .mode = "piecewise",
               .layout = "midpoint",
               .off_min = 380.0,
               .cap = 2.7,
           },
           Row {
               .mode = "piecewise",
               .layout = "tangent",
               .off_min = 199.0,
               .cap = 2.0 + 1e-6,
           },
           Row {
               .mode = "piecewise_direct",
               .layout = "",
               .off_min = 380.0,
               .cap = 2.7,
           },
       })
  {
    CAPTURE(row.mode);
    CAPTURE(row.layout);
    for (const double alpha : {kSinkAlphaDeep, kSinkAlphaKink}) {
      for (const double tmax : {kSinkHeadroom, kSinkRating}) {
        CAPTURE(alpha);
        CAPTURE(tmax);
        // REGRESSION GUARD: burned ≤ physical + c·env² (the in-envelope
        // residual) in both requirement levels, true price kept.
        const auto r = sink_solve({.mode = row.mode,
                                   .tmax = tmax,
                                   .alpha = alpha,
                                   .layout = row.layout});
        const double sent = r.load + r.burned;
        CHECK(r.burned <= row.cap);
        CHECK(r.burned <= (kSinkC * sent * sent) + kSinkCapEnv + 1e-6);
        CHECK(r.lambda1 == doctest::Approx(-kSinkIncentive));
      }
    }
    // Guard disabled: the overflow tier is a sink of 200–400 MW.
    const auto off = sink_solve(
        {.mode = row.mode, .layout = row.layout, .overflow_cost = 0.0});
    CHECK(off.burned > row.off_min);
  }

  // DEFECT (documented, warned at LP build): `linear` has no loss column,
  // so neither ε nor the overflow price applies — circulation up to tmax.
  const auto lin = sink_solve({.mode = "linear"});
  CHECK(lin.burned > 340.0);
  CHECK(lin.circ > 100.0);
  const auto lin_tight = sink_solve({.mode = "linear", .tmax = kSinkRating});
  CHECK(lin_tight.burned < 2.5);
}

TEST_CASE(
    "penalty sink SOS2: the lambda-form bracket holds with and without "
    "overflow headroom (IMMUNE)")  // NOLINT
{
  using namespace penalty_sink_test;
  if (!sos2_available()) {
    MESSAGE("SOS2-capable solver (cplex) not loaded; skipping");
    return;
  }
  // REGRESSION GUARD — the only exact bracket.  ℓ ≤ secant of the
  // active segment (w = env/4 = 25 MW): at most c·w²/4 = 0.03 MW above
  // the physical loss, for any ε, any headroom, any requirement level,
  // with or without the overflow price on the λ weights.
  for (const double tmax : {kSinkHeadroom, kSinkRating}) {
    for (const double eps : {0.0, 0.1}) {
      for (const double alpha : {kSinkAlphaDeep, kSinkAlphaKink}) {
        for (const double price : {0.0, 1000.0}) {
          CAPTURE(tmax);
          CAPTURE(eps);
          CAPTURE(alpha);
          CAPTURE(price);
          const auto r = sink_solve({.eps = eps,
                                     .tmax = tmax,
                                     .alpha = alpha,
                                     .secant = 4,
                                     .sos2 = true,
                                     .overflow_cost = price});
          CHECK(r.fictitious() >= -0.05);
          CHECK(r.fictitious() <= (kSinkC * 25.0 * 25.0 / 4.0) + 1e-6);
          CHECK(r.lambda1 == doctest::Approx(-kSinkIncentive));
        }
      }
    }
  }
}

TEST_CASE(
    "penalty sink genuine overload: flow above the envelope is served "
    "with a physical loss, the price spread pays the overflow price")  // NOLINT
{
  using namespace penalty_sink_test;
  // No incentive (exogenous term); the load exceeds the 100 MW rating.
  // Measured λ2 [$/MWh]: guard off 10.41 / 10.58 / 11.15, on 59.18 /
  // 78.88 / 146.62 for loads 110 / 150 / 300.  The rise is the overflow
  // price on the marginal loss, E·c·(b_{j−1}+b_j) ≈ 2·E·c·|f|, times the
  // loss-adjusted delivery factor; it is the least any LP guard must
  // charge (formulation doc, "Overflow-Tier Price").
  struct Row
  {
    double load;
    double lambda2_on;
  };
  for (const auto& row :
       {
           Row {
               .load = 110.0,
               .lambda2_on = 59.1775,
           },
           Row {
               .load = 150.0,
               .lambda2_on = 78.8794,
           },
           Row {
               .load = 300.0,
               .lambda2_on = 146.6198,
           },
       })
  {
    CAPTURE(row.load);
    // Explicit 1000 $/MWh (the fixture's demand_fail_cost default would be
    // 1e5) so the pinned distortion is the one of a 1000 $/MWh guard.
    const SinkCase base {
        .load_term = "exogenous",
        .load = row.load,
        .overflow_cost = 1000.0,
    };
    auto off_case = base;
    off_case.overflow_cost = 0.0;
    const auto r = sink_solve(base);
    const auto off = sink_solve(off_case);
    CHECK(r.load == doctest::Approx(row.load));
    CHECK(r.flow > row.load);
    CHECK(r.loss >= kSinkC * r.flow * r.flow * 0.97);
    CHECK(r.loss <= kSinkC * r.flow * r.flow * 1.03);
    // Dispatch and loss unchanged by the guard (radial: no alternative).
    CHECK(r.flow == doctest::Approx(off.flow).epsilon(1e-6));
    CHECK(r.loss == doctest::Approx(off.loss).epsilon(1e-6));
    CHECK(r.lambda1 == doctest::Approx(kSinkGcost));
    // The price distortion, pinned.
    CHECK(r.lambda2 == doctest::Approx(row.lambda2_on).epsilon(1e-4));
    CHECK(off.lambda2 < kSinkGcost * 1.2);
  }
}

TEST_CASE(
    "penalty sink invariance: no incentive and |f| <= envelope gives the "
    "same solution with and without the overflow price")  // NOLINT
{
  using namespace penalty_sink_test;
  struct M
  {
    std::string_view mode;
    std::string_view layout;
  };
  for (const auto& m :
       {
           M {
               .mode = "tangent_signed_flow",
               .layout = "",
           },
           M {
               .mode = "bidirectional",
               .layout = "",
           },
           M {
               .mode = "piecewise",
               .layout = "midpoint",
           },
           M {
               .mode = "piecewise",
               .layout = "tangent",
           },
       })
  {
    CAPTURE(m.mode);
    CAPTURE(m.layout);
    const SinkCase base {
        .mode = m.mode,
        .layout = m.layout,
        .load_term = "exogenous",
    };
    auto off_case = base;
    off_case.overflow_cost = 0.0;
    auto tight_case = base;
    tight_case.tmax = kSinkRating;
    const auto r = sink_solve(base);
    const auto off = sink_solve(off_case);
    const auto tight = sink_solve(tight_case);
    CHECK(r.load == doctest::Approx(kSinkLoad));
    for (const auto* other : {&off, &tight}) {
      CHECK(r.burned == doctest::Approx(other->burned).epsilon(1e-9));
      CHECK(r.loss == doctest::Approx(other->loss).epsilon(1e-9));
      CHECK(r.lambda1 == doctest::Approx(other->lambda1).epsilon(1e-9));
      CHECK(r.lambda2 == doctest::Approx(other->lambda2).epsilon(1e-9));
    }
  }

  // piecewise_direct honours the envelope only with headroom; with or
  // without the price the in-range solution is identical.
  const SinkCase direct {.mode = "piecewise_direct", .load_term = "exogenous"};
  auto direct_off = direct;
  direct_off.overflow_cost = 0.0;
  const auto d_on = sink_solve(direct);
  const auto d_off = sink_solve(direct_off);
  CHECK(d_on.burned == doctest::Approx(d_off.burned).epsilon(1e-9));
  CHECK(d_on.lambda2 == doctest::Approx(d_off.lambda2).epsilon(1e-9));
}

TEST_CASE(
    "penalty sink default price: an unset loss_overflow_cost follows the "
    "case demand_fail_cost, 1000 when that is unset or 0")  // NOLINT
{
  using namespace penalty_sink_test;
  // Genuine overload (no incentive): the receiver price carries the
  // overflow price, so it identifies which price was applied.
  const auto lambda2 =
      [](std::optional<double> fail_cost, std::optional<double> price)
  {
    return sink_solve({
                          .load_term = "exogenous",
                          .load = 150.0,
                          .overflow_cost = price,
                          .fail_cost = fail_cost,
                      })
        .lambda2;
  };
  // CEN PCP cases use the PLEXOS VoLL 467.19 $/MWh.
  const double voll = lambda2(467.19, std::nullopt);
  CHECK(voll == doctest::Approx(lambda2(467.19, 467.19)).epsilon(1e-9));
  const double two_k = lambda2(2000.0, std::nullopt);
  CHECK(two_k == doctest::Approx(lambda2(2000.0, 2000.0)).epsilon(1e-9));
  CHECK(two_k > voll + 10.0);
  // An explicit price still overrides the fail cost; 0 disables the guard.
  CHECK(lambda2(2000.0, 467.19) == doctest::Approx(voll).epsilon(1e-9));
  CHECK(lambda2(2000.0, 0.0) < kSinkGcost * 1.2);
  // No demand_fail_cost: its own 1000 $/MWh default applies.
  CHECK(lambda2(std::nullopt, std::nullopt)
        == doctest::Approx(lambda2(std::nullopt, 1000.0)).epsilon(1e-9));
  // An explicit demand_fail_cost 0 (freely sheddable load) keeps the
  // guard at the 1000 $/MWh fallback (warned once), not disabled.
  CHECK(lambda2(0.0, std::nullopt)
        == doctest::Approx(lambda2(0.0, 1000.0)).epsilon(1e-9));

  // The VoLL default (467.19 > the 134 $/MWh incentive) closes the sink.
  const auto sink = sink_solve({.fail_cost = 467.19});
  CHECK(sink.burned == doctest::Approx(kSinkCapEnv).epsilon(1e-4));
  CHECK(sink.lambda1 == doctest::Approx(-kSinkIncentive));
}
