// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Soft-requirement valuation constant (ReserveZone / InertiaZone).
//
// A soft requirement (`urcost` / `drcost` / InertiaZone `cost`) is built as a
// bookkeeping column `rcol ∈ [0, req]` with cost `−c` and a row
// `Σ pf·prov − rcol ≥ 0`.  The column cost alone puts `−c·rcol` in the
// objective, so a MET requirement used to report `−c·req` (a fictitious
// "benefit": ≈ −177 M$ on the CEN PCP case).  The matching `+c·req` now rides
// on the LP-external objective constant (`LinearProblem::add_obj_constant`,
// the demand-fail baseline mechanism), so the reported objective equals the
// real operating cost `… + c·(req − rcol)` while the LP — rows, columns,
// costs, bounds, hence dispatch and duals — is unchanged.
//
// Every expected value is hand-computed: 1 h blocks, probability 1, no
// discounting.

#include <cmath>
#include <optional>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/inertia_provision_lp.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>

#include "fixture_helpers.hpp"

using namespace gtopt;

namespace
{

constexpr double kDemand = 100.0;  // MW
constexpr double kGcost = 50.0;  // $/MWh
constexpr double kReq = 50.0;  // MW up-reserve requirement
constexpr double kShortCost = 1000.0;  // $/MW shortage
constexpr double kProvCost = 2.0;  // $/MW provision bid

struct ReserveFixture
{
  double urmax {100.0};  ///< provision cap (< kReq ⇒ short)
  bool soft {true};  ///< priced shortage vs hard requirement
  bool planes {false};  ///< plane-based (dynamic) requirement
  double scale_objective {1.0};
};

[[nodiscard]] Planning make_reserve_planning(const ReserveFixture& f)
{
  ReserveZone zone {
      .uid = Uid {1},
      .name = "rz1",
  };
  if (f.planes) {
    zone.urreq_planes = {
        ReserveRequirementPlane {.alpha = kReq},
    };
  } else {
    zone.urreq = kReq;
  }
  if (f.soft) {
    zone.urcost = kShortCost;
  }

  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1.0e5;
  opts.model_options.use_single_bus = OptBool {true};
  opts.model_options.scale_objective = OptReal {f.scale_objective};

  return Planning {
      .options = std::move(opts),
      .simulation =
          {
              .block_array = {{.uid = Uid {1}, .duration = 1}},
              .stage_array = {{
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              }},
              .scenario_array = {{.uid = Uid {1}}},
          },
      .system =
          {
              .name = "reserve_obj_constant",
              .bus_array = {{.uid = Uid {1}, .name = "b1"}},
              .demand_array = {{
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .lmax = kDemand,
              }},
              .generator_array = {{
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = kGcost,
                  .capacity = 300.0,
              }},
              .reserve_zone_array = {zone},
              .reserve_provision_array = {{
                  .uid = Uid {1},
                  .name = "rp1",
                  .generator = Uid {1},
                  .reserve_zones = {SingleId {Uid {1}}},
                  .urmax = f.urmax,
                  .ur_provision_factor = 1.0,
                  .urcost = kProvCost,
              }},
          },
  };
}

struct Solved
{
  PlanningLP plp;
  explicit Solved(const ReserveFixture& f)
      : plp(make_reserve_planning(f))
  {
    auto status = plp.resolve();
    REQUIRE(status.has_value());
  }
  [[nodiscard]] auto& sys() { return plp.systems().front().front(); }
  [[nodiscard]] auto& li() { return sys().linear_interface(); }
  [[nodiscard]] double obj() { return li().get_obj_value(); }
  [[nodiscard]] double reserve_dual()
  {
    const auto& zone = sys().template elements<ReserveZoneLP>().front();
    const auto& rows = zone.urequirement_rows();
    REQUIRE_FALSE(rows.empty());
    const auto& brows = rows.begin()->second;
    REQUIRE_FALSE(brows.empty());
    return std::abs(li().get_row_dual()[brows.begin()->second]);
  }
  [[nodiscard]] double bus_dual()
  {
    const auto& sc = sys().scene().scenarios().front();
    const auto& st = sys().phase().stages().front();
    const auto& bus = sys().template elements<BusLP>().front();
    const auto& rows = bus.balance_rows_at(sc, st);
    return std::abs(li().get_row_dual()[rows.begin()->second]);
  }
};

}  // namespace

TEST_CASE("Reserve objective constant: met soft requirement costs 0")  // NOLINT
{
  for (const double scale : {1.0, 1000.0}) {
    CAPTURE(scale);
    Solved soft {ReserveFixture {.urmax = 100.0, .scale_objective = scale}};
    Solved hard {ReserveFixture {
        .urmax = 100.0, .soft = false, .scale_objective = scale}};

    // Real cost: energy 50·100 + provision 2·50; no shortage.
    const double expected = (kGcost * kDemand) + (kProvCost * kReq);
    CHECK(soft.obj() == doctest::Approx(expected));
    CHECK(hard.obj() == doctest::Approx(expected));

    // The soft and hard LPs differ only by the bookkeeping column; the
    // valuation constant is exactly c·req on top of the hard model's.
    CHECK(soft.li().get_obj_constant() - hard.li().get_obj_constant()
          == doctest::Approx(kShortCost * kReq));
    // LP-internal objective (no constant) is the pre-fix reported value:
    // real cost − c·req.
    CHECK(soft.obj() - soft.li().get_obj_constant()
          == doctest::Approx(hard.obj() - hard.li().get_obj_constant()
                             - (kShortCost * kReq)));

    // Duals unchanged: μ = provision bid, λ = energy cost.
    CHECK(soft.reserve_dual() == doctest::Approx(kProvCost));
    CHECK(hard.reserve_dual() == doctest::Approx(kProvCost));
    CHECK(soft.bus_dual() == doctest::Approx(kGcost));
  }
}

TEST_CASE(
    "Reserve objective constant: short requirement pays penalty")  // NOLINT
{
  for (const double scale : {1.0, 1000.0}) {
    CAPTURE(scale);
    Solved soft {ReserveFixture {.urmax = 30.0, .scale_objective = scale}};
    // Real cost: energy 5000 + provision 2·30 + shortage 1000·(50 − 30).
    const double expected =
        (kGcost * kDemand) + (kProvCost * 30.0) + (kShortCost * (kReq - 30.0));
    CHECK(soft.obj() == doctest::Approx(expected));
    // Shortage prices the requirement: μ = shortage cost.
    CHECK(soft.reserve_dual() == doctest::Approx(kShortCost));
    CHECK(soft.bus_dual() == doctest::Approx(kGcost));
    // Served requirement column reads the provided 30 MW.
    const auto& zone = soft.sys().template elements<ReserveZoneLP>().front();
    const auto& cols = zone.urequirement_cols();
    REQUIRE_FALSE(cols.empty());
    CHECK(soft.li().get_col_sol()[cols.begin()->second.begin()->second]
          == doctest::Approx(30.0));
  }
}

TEST_CASE(  // NOLINT
    "Reserve objective constant: plane requirement needs no constant")
{
  // The plane (dynamic) form already prices only the unmet part through a
  // positive-cost shortage column `s` in `Σ pf·prov + s − R ≥ 0`; R is a
  // variable, so there is no `−c·R` valuation term and no constant.
  Solved hard {ReserveFixture {.urmax = 100.0, .soft = false}};
  Solved met {ReserveFixture {.urmax = 100.0, .planes = true}};
  Solved shortp {ReserveFixture {.urmax = 30.0, .planes = true}};
  CHECK(met.li().get_obj_constant()
        == doctest::Approx(hard.li().get_obj_constant()));
  CHECK(met.obj() == doctest::Approx((kGcost * kDemand) + (kProvCost * kReq)));
  CHECK(shortp.obj()
        == doctest::Approx((kGcost * kDemand) + (kProvCost * 30.0)
                           + (kShortCost * 20.0)));
  CHECK(met.reserve_dual() == doctest::Approx(kProvCost));
  CHECK(shortp.reserve_dual() == doctest::Approx(kShortCost));
}

namespace
{

/// InertiaZone fixture: requirement 200 MWs, provision factor 8 on a
/// must-run unit (pmin 50 ⇒ r ≤ 50 ⇒ ≤ 400 MWs).
[[nodiscard]] Planning make_inertia_planning(double requirement,
                                             double scale_objective)
{
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1.0e5;
  opts.model_options.use_single_bus = OptBool {true};
  opts.model_options.scale_objective = OptReal {scale_objective};
  return Planning {
      .options = std::move(opts),
      .simulation =
          {
              .block_array = {{.uid = Uid {1}, .duration = 1}},
              .stage_array = {{
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              }},
              .scenario_array = {{.uid = Uid {1}}},
          },
      .system =
          {
              .name = "inertia_obj_constant",
              .bus_array = {{.uid = Uid {1}, .name = "b1"}},
              .demand_array = {{
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .lmax = kDemand,
              }},
              .generator_array = {{
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .pmin = 50.0,
                  .gcost = 30.0,
                  .capacity = 200.0,
              }},
              .inertia_zone_array = {{
                  .uid = Uid {1},
                  .name = "iz1",
                  .requirement = requirement,
                  .cost = kShortCost,
              }},
              .inertia_provision_array = {{
                  .uid = Uid {1},
                  .name = "ip1",
                  .generator = Uid {1},
                  .inertia_zones = {SingleId {Uid {1}}},
                  .provision_factor = 8.0,
                  .cost = 1.0,
              }},
          },
  };
}

}  // namespace

TEST_CASE("Inertia objective constant: met and short requirement")  // NOLINT
{
  for (const double scale : {1.0, 1000.0}) {
    CAPTURE(scale);
    {
      // Met: r = 200/8 = 25 MW at 1 $/MW; energy 30·100.
      PlanningLP plp(make_inertia_planning(200.0, scale));
      REQUIRE(plp.resolve().has_value());
      auto& sys = plp.systems().front().front();
      auto& li = sys.linear_interface();
      CHECK(li.get_obj_value() == doctest::Approx((30.0 * kDemand) + 25.0));
      const auto& zone = sys.elements<InertiaZoneLP>().front();
      const auto& rows = zone.requirement_rows();
      REQUIRE_FALSE(rows.empty());
      // μ = provision cost / provision factor = 1/8 $/MWs.
      CHECK(std::abs(li.get_row_dual()[rows.begin()->second.begin()->second])
            == doctest::Approx(1.0 / 8.0));
    }
    {
      // Short: requirement 600 MWs, r ≤ pmin… the headroom cap is the
      // generation (r ≤ p ≤ 200 ⇒ ≤ 1600 MWs) — force the shortfall via a
      // requirement above 8·200: 2000 MWs ⇒ r = 200, shortage 400 MWs.
      PlanningLP plp(make_inertia_planning(2000.0, scale));
      REQUIRE(plp.resolve().has_value());
      auto& sys = plp.systems().front().front();
      auto& li = sys.linear_interface();
      const auto& zone = sys.elements<InertiaZoneLP>().front();
      const auto& cols = zone.requirement_cols();
      REQUIRE_FALSE(cols.empty());
      const double served =
          li.get_col_sol()[cols.begin()->second.begin()->second];
      const auto& ip = sys.elements<InertiaProvisionLP>().front();
      const auto& sc = sys.scene().scenarios().front();
      const auto& st = sys.phase().stages().front();
      const auto rcol =
          ip.lookup_provision_col(sc, st, st.blocks().front().uid());
      REQUIRE(rcol.has_value());
      const double r = li.get_col_sol()[rcol.value_or(ColIndex {0})];
      CHECK(served == doctest::Approx(8.0 * r));
      CHECK(served < 2000.0);
      // Real cost: energy + provision + shortage on the unmet part only.
      double gen = 0.0;
      for (const auto& g : sys.elements<GeneratorLP>()) {
        gen = li.get_col_sol()[g.lookup_generation_col(
                                    sc, st, st.blocks().front().uid())
                                   .value_or(ColIndex {0})];
      }
      CHECK(li.get_obj_value()
            == doctest::Approx((30.0 * gen) + r
                               + (kShortCost * (2000.0 - served))));
      const auto& rows = zone.requirement_rows();
      CHECK(std::abs(li.get_row_dual()[rows.begin()->second.begin()->second])
            == doctest::Approx(kShortCost));
    }
  }
}

namespace
{

/// Two single-stage phases, each with the up-reserve requirement.
[[nodiscard]] Planning make_reserve_sddp_planning(double urmax, bool soft)
{
  auto p = make_reserve_planning(ReserveFixture {.urmax = urmax, .soft = soft});
  p.simulation.block_array = test_fixtures::make_uniform_blocks(2, 1.0);
  p.simulation.stage_array = test_fixtures::make_uniform_stages(2, 1);
  p.simulation.phase_array = test_fixtures::make_single_stage_phases(2);
  p.options.method = MethodType::sddp;
  return p;
}

[[nodiscard]] SDDPIterationResult solve_sddp(double urmax, bool soft)
{
  PlanningLP plp(make_reserve_sddp_planning(urmax, soft));
  SDDPOptions opts;
  opts.max_iterations = 4;
  opts.convergence_tol = 1.0e-6;
  opts.enable_api = false;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  return r->back();
}

}  // namespace

TEST_CASE(  // NOLINT
    "Reserve objective constant: SDDP bounds equal the real cost")
{
  for (const double urmax : {100.0, 30.0}) {
    CAPTURE(urmax);
    const auto res = solve_sddp(urmax, /*soft=*/true);
    const double served = std::min(urmax, kReq);
    const double per_phase = (kGcost * kDemand) + (kProvCost * served)
        + (kShortCost * (kReq - served));
    CHECK(res.upper_bound == doctest::Approx(2.0 * per_phase));
    CHECK(res.lower_bound == doctest::Approx(2.0 * per_phase));
  }
  // Met requirement: the soft model's bounds coincide with the hard
  // model's (the constant exactly cancels the −c·req valuation term).
  const auto soft = solve_sddp(100.0, /*soft=*/true);
  const auto hard = solve_sddp(100.0, /*soft=*/false);
  CHECK(soft.upper_bound == doctest::Approx(hard.upper_bound));
  CHECK(soft.lower_bound == doctest::Approx(hard.lower_bound));
}
