// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_losses_flow_abs_cost.cpp — the ``loss_flow_abs_cost`` option:
// the per-MWh pin on the ``tangent_signed_flow`` abs-flow proxy columns
// ``v_l`` (``Σ v_l ≥ |f|``).
//
// The pin used to be an internal constant (1e-6 $/MWh, i.e. 1e-9 in the
// LP after ``scale_objective = 1000``: below solver dual tolerances).  It
// is now a user option in real $/MWh, default 0.001 (1e-6 in the LP).
// These tests pin:
//   * option resolution (default, global, per-line override, JSON);
//   * the stamped objective coefficient = option value (block_ecost);
//   * 0 disables the pin, and so does ``loss_cost_eps = 0`` (gating);
//   * 1e-6 reproduces the legacy LP (identical except the v cost);
//   * ``Σ v_l = |f|`` at the optimum with the default, on a meshed ring
//     with L = 1 and L = 4 secant segments (pure LP, no SOS2);
//   * the objective rises by exactly ``pin · Σ|f|`` and every LMP
//     difference moves by at most ``pin`` per lossy line vs pin = 0.
//
// Fixture: a 4-bus KVL ring 1—2—3—4—1 (equal R, X; no binding limit,
// so every LMP is positive and no loss-arbitrage channel is open):
//
//   g1 (c=10, 1000 MW) at bus 1, g3 (c=30, 1000 MW) at bus 3
//   d2 = 200 MW at bus 2, d4 = 100 MW at bus 4
//
// g1 is the marginal unit and serves everything; every line carries
// flow, so every v column is exercised.

#include <cmath>
#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_line.hpp>
#include <gtopt/json/json_model_options.hpp>
#include <gtopt/line.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{
namespace flow_abs_cost_test
{

constexpr double kV = 100.0;
constexpr double kR = 0.5;
constexpr double kX = 10.0;
constexpr double kTmax = 1000.0;
constexpr double kScaleObjective = 1000.0;
constexpr int kNumLines = 4;

struct Knobs
{
  /// Global ``model_options.loss_flow_abs_cost``; nullopt = unset.
  std::optional<double> pin {};
  /// Per-line override on line 1 (l12) only; nullopt = unset.
  std::optional<double> line1_pin {};
  double loss_cost_eps = 0.1;
  int loss_secant_segments = 1;
};

struct RingFixture
{
  System system;
  Simulation simulation;
  PlanningOptions opts;
  PlanningOptionsLP options;
  SimulationLP sim_lp;
  SystemLP sys_lp;

  explicit RingFixture(const Knobs& k)
      : system {
            .name = "FlowAbsCostRing",
            .bus_array =
                {
                    {.uid = Uid {1}, .name = "b1",},
                    {.uid = Uid {2}, .name = "b2",},
                    {.uid = Uid {3}, .name = "b3",},
                    {.uid = Uid {4}, .name = "b4",},
                },
            .demand_array =
                {
                    {.uid = Uid {2},
                     .name = "d2",
                     .bus = Uid {2},
                     .capacity = 200.0,},
                    {.uid = Uid {4},
                     .name = "d4",
                     .bus = Uid {4},
                     .capacity = 100.0,},
                },
            .generator_array =
                {
                    {.uid = Uid {1},
                     .name = "g1",
                     .bus = Uid {1},
                     .gcost = 10.0,
                     .capacity = 1000.0,},
                    {.uid = Uid {3},
                     .name = "g3",
                     .bus = Uid {3},
                     .gcost = 30.0,
                     .capacity = 1000.0,},
                },
            .line_array =
                {
                    make_line(Uid {1}, "l12", Uid {1}, Uid {2}, k, k.line1_pin),
                    make_line(Uid {2}, "l23", Uid {2}, Uid {3}, k, {}),
                    make_line(Uid {3}, "l34", Uid {3}, Uid {4}, k, {}),
                    make_line(Uid {4}, "l41", Uid {4}, Uid {1}, k, {}),
                },
        }
      , simulation {
            .block_array = {{.uid = Uid {1}, .duration = 1,},},
            .stage_array =
                {{.uid = Uid {1}, .first_block = 0, .count_block = 1,},},
            .scenario_array = {{.uid = Uid {0},},},
        }
      , options(make_options(k))
      , sim_lp(simulation, options)
      , sys_lp(system, sim_lp, matrix_opts())
  {
  }

  [[nodiscard]] auto& lp() { return sys_lp.linear_interface(); }

private:
  static Line make_line(Uid uid,
                        std::string_view name,
                        Uid bus_a,
                        Uid bus_b,
                        const Knobs& k,
                        std::optional<double> line_pin)
  {
    Line ln = {
        .uid = uid,
        .name = std::string(name),
        .bus_a = bus_a,
        .bus_b = bus_b,
        .voltage = kV,
        .resistance = kR,
        .reactance = kX,
        .line_losses_mode = OptName {"tangent_signed_flow"},
        .loss_segments = 4,
        .tmax_ba = kTmax,
        .tmax_ab = kTmax,
        .capacity = kTmax,
    };
    if (k.loss_secant_segments > 1) {
      ln.loss_secant_segments = k.loss_secant_segments;
      ln.loss_use_sos2 = false;
    }
    ln.loss_flow_abs_cost = line_pin;
    return ln;
  }

  PlanningOptionsLP make_options(const Knobs& k)
  {
    opts.model_options.use_single_bus = false;
    opts.model_options.use_kirchhoff = true;
    opts.model_options.scale_objective = kScaleObjective;
    opts.model_options.demand_fail_cost = 1000.0;
    opts.model_options.loss_cost_eps = k.loss_cost_eps;
    opts.model_options.loss_flow_abs_cost = k.pin;
    return PlanningOptionsLP(opts);
  }

  static LpMatrixOptions matrix_opts()
  {
    LpMatrixOptions bo;
    bo.col_with_names = true;
    bo.col_with_name_map = true;
    bo.row_with_names = true;
    bo.row_with_name_map = true;
    return bo;
  }
};

/// Column indices whose name contains ``substr``, in name-map order.
[[nodiscard]] std::vector<std::size_t> cols_with(const LinearInterface& li,
                                                 std::string_view substr)
{
  std::vector<std::size_t> out;
  for (const auto& [name, idx] : li.col_name_map()) {
    if (name.contains(substr)) {
      out.push_back(static_cast<std::size_t>(value_of(idx)));
    }
  }
  return out;
}

[[nodiscard]] std::size_t row_with(const LinearInterface& li,
                                   std::string_view substr)
{
  std::size_t found = 0;
  int count = 0;
  for (const auto& [name, idx] : li.row_name_map()) {
    if (name.contains(substr)) {
      found = static_cast<std::size_t>(value_of(idx));
      ++count;
    }
  }
  REQUIRE(count == 1);
  return found;
}

/// Physical primal value summed over the columns of line ``uid`` whose
/// name contains ``what`` (e.g. ``line_flow_abs_`` → Σ_l v_l).
[[nodiscard]] double line_sum(LinearInterface& li,
                              std::string_view what,
                              int uid)
{
  const auto sol = li.get_col_sol();
  const auto prefix = std::string(what) + std::to_string(uid) + "_";
  double total = 0.0;
  for (const auto i : cols_with(li, prefix)) {
    total += sol[i];
  }
  return total;
}

/// Signed net flow of line ``uid`` (physical MW).  ``tangent_signed_flow``
/// emits one signed flow column ``line_flows_<uid>_…``.
[[nodiscard]] double line_flow(LinearInterface& li, int uid)
{
  return line_sum(li, "line_flows_", uid);
}

struct Solved
{
  double obj {};
  double sum_abs_flow {};
  std::vector<double> lmp;  ///< physical bus-balance duals, buses 1..4
};

[[nodiscard]] Solved solve(const Knobs& k)
{
  RingFixture fix(k);
  auto& li = fix.lp();
  const auto rc = li.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(rc.value_or(-1) == 0);

  Solved s;
  s.obj = li.get_obj_value();
  for (int uid = 1; uid <= kNumLines; ++uid) {
    s.sum_abs_flow += std::abs(line_flow(li, uid));
  }
  const auto duals = li.get_row_dual();
  for (int bus = 1; bus <= 4; ++bus) {
    const auto row =
        row_with(li, std::string("bus_balance_") + std::to_string(bus) + "_");
    s.lmp.push_back(duals[row]);
  }
  return s;
}

}  // namespace flow_abs_cost_test
}  // namespace

using flow_abs_cost_test::cols_with;
using flow_abs_cost_test::Knobs;
using flow_abs_cost_test::kNumLines;
using flow_abs_cost_test::kScaleObjective;
using flow_abs_cost_test::line_flow;
using flow_abs_cost_test::line_sum;
using flow_abs_cost_test::RingFixture;
using flow_abs_cost_test::solve;

TEST_CASE("loss_flow_abs_cost option resolution")  // NOLINT
{
  SUBCASE("unset resolves to the 0.001 $/MWh default")
  {
    const PlanningOptions opts;
    const PlanningOptionsLP options(opts);
    CHECK(PlanningOptionsLP::default_loss_flow_abs_cost
          == doctest::Approx(0.001));
    CHECK(options.loss_flow_abs_cost() == doctest::Approx(0.001));
    CHECK(PlanningOptionsLP::legacy_loss_flow_abs_cost
          == doctest::Approx(1e-6));
  }

  SUBCASE("explicit global value, including 0")
  {
    PlanningOptions opts;
    opts.model_options.loss_flow_abs_cost = 0.05;
    CHECK(PlanningOptionsLP(opts).loss_flow_abs_cost()
          == doctest::Approx(0.05));
    opts.model_options.loss_flow_abs_cost = 0.0;
    CHECK(PlanningOptionsLP(opts).loss_flow_abs_cost() == 0.0);
  }

  SUBCASE("model_options JSON round-trip and merge")
  {
    constexpr std::string_view json = R"({"loss_flow_abs_cost": 0.002})";
    const auto mo = daw::json::from_json<ModelOptions>(json);
    CHECK(mo.loss_flow_abs_cost.value_or(-1.0) == doctest::Approx(0.002));
    const auto again =
        daw::json::from_json<ModelOptions>(daw::json::to_json(mo));
    CHECK(again.loss_flow_abs_cost.value_or(-1.0) == doctest::Approx(0.002));

    ModelOptions base;
    base.merge(mo);
    CHECK(base.loss_flow_abs_cost.value_or(-1.0) == doctest::Approx(0.002));
  }

  SUBCASE("Line JSON round-trip")
  {
    constexpr std::string_view json = R"({
      "uid": 7, "name": "l", "bus_a": 1, "bus_b": 2,
      "loss_flow_abs_cost": 0.0
    })";
    const auto ln = daw::json::from_json<Line>(json);
    CHECK(ln.loss_flow_abs_cost.has_value());
    CHECK(ln.loss_flow_abs_cost.value_or(-1.0) == 0.0);
    const auto ln2 = daw::json::from_json<Line>(daw::json::to_json(ln));
    CHECK(ln2.loss_flow_abs_cost.value_or(-1.0) == 0.0);
  }
}

TEST_CASE("loss_flow_abs_cost stamps the abs-flow pin")  // NOLINT
{
  // duration 1 h, probability 1, no discount: the LP coefficient is
  // pin / scale_objective.
  auto v_costs = [](const Knobs& k)
  {
    RingFixture fix(k);
    auto& li = fix.lp();
    const auto obj = li.get_obj_coeff();
    std::vector<double> out;
    for (const auto i : cols_with(li, "line_flow_abs_")) {
      out.push_back(obj[i]);
    }
    return out;
  };

  for (const int nsec : {1, 4}) {
    CAPTURE(nsec);
    const auto n_v =
        static_cast<std::size_t>(kNumLines) * static_cast<std::size_t>(nsec);

    SUBCASE("default 0.001 $/MWh → 1e-6 in the LP")
    {
      const auto c = v_costs(Knobs {.loss_secant_segments = nsec});
      REQUIRE(c.size() == n_v);
      for (const double x : c) {
        CHECK(x == doctest::Approx(0.001 / kScaleObjective).epsilon(1e-12));
      }
    }

    SUBCASE("explicit value is stamped verbatim")
    {
      const auto c = v_costs(Knobs {.pin = 0.25, .loss_secant_segments = nsec});
      REQUIRE(c.size() == n_v);
      for (const double x : c) {
        CHECK(x == doctest::Approx(0.25 / kScaleObjective).epsilon(1e-12));
      }
    }

    SUBCASE("0 disables the pin")
    {
      const auto c = v_costs(Knobs {.pin = 0.0, .loss_secant_segments = nsec});
      REQUIRE(c.size() == n_v);
      for (const double x : c) {
        CHECK(x == 0.0);
      }
    }

    SUBCASE("loss_cost_eps = 0 keeps the pin off (legacy gating)")
    {
      const auto c = v_costs(
          Knobs {.pin = 0.5, .loss_cost_eps = 0.0, .loss_secant_segments = 1});
      REQUIRE(c.size() == static_cast<std::size_t>(kNumLines));
      for (const double x : c) {
        CHECK(x == 0.0);
      }
    }
  }

  SUBCASE("per-line value beats the global one")
  {
    RingFixture fix(Knobs {.pin = 0.002, .line1_pin = 0.0});
    auto& li = fix.lp();
    const auto obj = li.get_obj_coeff();
    for (const auto i : cols_with(li, "line_flow_abs_1_")) {
      CHECK(obj[i] == 0.0);
    }
    for (const auto i : cols_with(li, "line_flow_abs_2_")) {
      CHECK(obj[i] == doctest::Approx(0.002 / kScaleObjective));
    }
  }
}

TEST_CASE(
    "loss_flow_abs_cost = 1e-6 reproduces the legacy LP, only the v cost "
    "differs from the default")  // NOLINT
{
  for (const int nsec : {1, 4}) {
    CAPTURE(nsec);
    RingFixture legacy(
        Knobs {.pin = PlanningOptionsLP::legacy_loss_flow_abs_cost,
               .loss_secant_segments = nsec});
    RingFixture dflt(Knobs {.loss_secant_segments = nsec});
    auto& a = legacy.lp();
    auto& b = dflt.lp();
    REQUIRE(a.get_numrows() == b.get_numrows());
    REQUIRE(a.get_numcols() == b.get_numcols());

    // Legacy value: the former internal constant 1e-6 $/MWh → 1e-9.
    const auto v_cols = cols_with(a, "line_flow_abs_");
    REQUIRE(v_cols.size()
            == static_cast<std::size_t>(kNumLines)
                * static_cast<std::size_t>(nsec));
    const auto oa = a.get_obj_coeff();
    const auto ob = b.get_obj_coeff();
    std::vector<bool> is_v(static_cast<std::size_t>(a.get_numcols()), false);
    for (const auto i : v_cols) {
      is_v[i] = true;
      CHECK(oa[i] == doctest::Approx(1e-9).epsilon(1e-12));
      CHECK(ob[i] / oa[i] == doctest::Approx(1000.0));
    }

    // Everything else is bit-identical: objective, bounds, rows.
    for (std::size_t i = 0; i < is_v.size(); ++i) {
      if (!is_v[i]) {
        CHECK(oa[i] == ob[i]);
      }
    }
    const auto la = a.get_col_low_raw();
    const auto lb = b.get_col_low_raw();
    const auto ua = a.get_col_upp_raw();
    const auto ub = b.get_col_upp_raw();
    for (std::size_t i = 0; i < is_v.size(); ++i) {
      CHECK(la[i] == lb[i]);
      CHECK(ua[i] == ub[i]);
    }
    const auto rla = a.get_row_low_raw();
    const auto rlb = b.get_row_low_raw();
    const auto rua = a.get_row_upp_raw();
    const auto rub = b.get_row_upp_raw();
    for (std::size_t r = 0; r < rla.size(); ++r) {
      CHECK(rla[r] == rlb[r]);
      CHECK(rua[r] == rub[r]);
    }
  }
}

TEST_CASE(
    "loss_flow_abs_cost default: Σ v_l = |f| at the optimum on a meshed "
    "ring")  // NOLINT
{
  for (const int nsec : {1, 4}) {
    CAPTURE(nsec);
    RingFixture fix(Knobs {.loss_secant_segments = nsec});
    auto& li = fix.lp();
    const auto rc = li.resolve();
    REQUIRE(rc.has_value());
    REQUIRE(rc.value_or(-1) == 0);

    for (int uid = 1; uid <= kNumLines; ++uid) {
      const double f = line_flow(li, uid);
      const double sum_v = line_sum(li, "line_flow_abs_", uid);
      CAPTURE(uid);
      CAPTURE(f);
      CAPTURE(sum_v);
      // Every line of the ring carries real flow.
      CHECK(std::abs(f) > 1.0);
      // The pin is effective: no slack between Σ v_l and |f|.
      CHECK(sum_v == doctest::Approx(std::abs(f)).epsilon(1e-6).scale(1.0));
    }
  }
}

TEST_CASE(
    "loss_flow_abs_cost: Σ v_l − |f| slack per pin value (informative)")  // NOLINT
{
  // Not asserted: reports how far Σ v_l drifts above |f| with the pin
  // off, at the legacy 1e-6 $/MWh (1e-9 in the LP) and at the default,
  // for the active solver.  Run with -s to see the MESSAGE lines.
  for (const int nsec : {1, 4}) {
    for (const double pin :
         {0.0, PlanningOptionsLP::legacy_loss_flow_abs_cost, 0.001})
    {
      RingFixture fix(Knobs {.pin = pin, .loss_secant_segments = nsec});
      auto& li = fix.lp();
      REQUIRE(li.resolve().value_or(-1) == 0);
      double slack = 0.0;
      for (int uid = 1; uid <= kNumLines; ++uid) {
        slack +=
            line_sum(li, "line_flow_abs_", uid) - std::abs(line_flow(li, uid));
      }
      MESSAGE("L=" << nsec << " pin=" << pin
                   << " $/MWh: sum(v) - sum|f| = " << slack << " MW");
      CHECK(slack >= -1e-6);
    }
  }
}

TEST_CASE(
    "loss_flow_abs_cost: objective and LMP shift vs pin = 0 are bounded "
    "by the pin")  // NOLINT
{
  for (const int nsec : {1, 4}) {
    CAPTURE(nsec);
    for (const double pin : {0.001, 0.01}) {
      CAPTURE(pin);
      const auto off = solve(Knobs {.pin = 0.0, .loss_secant_segments = nsec});
      const auto on = solve(Knobs {.pin = pin, .loss_secant_segments = nsec});

      // Dispatch is unchanged (the pin is far below every price gap),
      // so the objective rises by exactly pin · Σ|f| (Σ v = |f|).
      CHECK(on.sum_abs_flow == doctest::Approx(off.sum_abs_flow).epsilon(1e-6));
      CHECK(on.obj - off.obj
            == doctest::Approx(pin * on.sum_abs_flow).epsilon(1e-3));

      // Each LMP moves by at most pin per lossy line on the path from
      // the marginal unit (≤ 2 lines in a 4-ring; bound with all 4).
      REQUIRE(on.lmp.size() == off.lmp.size());
      for (std::size_t b = 0; b < on.lmp.size(); ++b) {
        CAPTURE(b);
        CAPTURE(on.lmp[b]);
        CAPTURE(off.lmp[b]);
        CHECK(std::abs(std::abs(on.lmp[b]) - std::abs(off.lmp[b]))
              <= (pin * kNumLines) + 1e-6);
      }
    }
  }
}
