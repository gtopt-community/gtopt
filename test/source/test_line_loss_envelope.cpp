// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_loss_envelope.cpp — flow PAST a decoupled ``loss_envelope``.
//
// ``Line.loss_envelope`` decouples the loss-PWL envelope from the flow
// cap (``tmax_ab`` / ``tmax_ba``).  Its contract (include/gtopt/line.hpp)
// is "flow past the envelope extrapolates on the last segment's slope":
// the envelope only shapes the LOSS approximation, it must never cap the
// flow.  plexos2gtopt relies on this for PLEXOS ``Enforce Limits = 0``
// lines (``tmax = 10 × rating``, ``loss_envelope = rating``).
//
// Regression for a hidden flow cap at exactly ``|f| = loss_envelope``:
//   * ``tangent_signed_flow`` segment form (regimes A / B): every
//     ``|f|``-abs segment column ``v_l`` was bounded at
//     ``loss_envelope / L``, so ``Σ v_l ≥ |f|`` capped ``|f|``.
//   * ``tangent_signed_flow`` SOS2 λ-form (regime C): the breakpoint
//     ladder only spanned ``±loss_envelope``.
//   * ``piecewise`` + ``tangent`` layout (shared-loss path): the loss
//     column was bounded at ``R·envelope²/V²`` while the tangent rows
//     force ``ℓ`` above that once ``|f|`` exceeds the envelope.
//
// The fixture is a 2-bus line with ``tmax = 10 × rating`` and
// ``loss_envelope = rating`` whose demand forces ``|f| ≥ 3 × rating``.
// For every mode the LP must serve the demand in full (no unserved
// energy), carry ``|f| ≥ 3 × rating``, report a physically sensible loss
// (≥ the tangent lower envelope, ≤ the true quadratic), price the
// receiving bus at the loss-adjusted generator cost (not the
// demand-fail cost), and leave no reduced-cost "cap" on any loss
// auxiliary column or on the flow column (tmax is not binding).

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <span>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/line.hpp>
#include <gtopt/line_losses.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace test_line_loss_envelope_ns
{

namespace
{

constexpr double kRating = 100.0;  // loss_envelope [MW]
constexpr double kTmax = 10.0 * kRating;  // flow cap [MW]
constexpr double kDemand = 3.0 * kRating;  // forces |f| ≥ 3·rating
constexpr double kR = 1.0;
constexpr double kV = 100.0;
constexpr double kLossK = kR / (kV * kV);  // R/V² = 1e-4 MW per MW²
constexpr double kGcost = 10.0;
constexpr double kFailCost = 1000.0;
constexpr int kTangents = 5;

/// Line-loss knobs exercised by one case.
struct EnvCase
{
  std::string_view mode;
  std::string_view layout {};
  int secant_segments {1};
  bool use_sos2 {false};
  double loss_cost_eps {0.1};
  /// ``loss_envelope``: 0 → unset (legacy, non-decoupled).
  double envelope {kRating};
  double tmax {kTmax};
  double demand {kDemand};
  /// ``Line.loss_overflow_cost``.  0 here: this suite pins the price-free
  /// ladder geometry (objective = generation cost + ε·loss, receiver
  /// priced at the loss-adjusted generator cost).  The default 1000 $/MWh
  /// overflow price adds ``E·c·(b_{j−1}+b_j)`` to the marginal transfer
  /// cost past the envelope; that guarded behaviour (same flow and loss,
  /// higher receiver price) is pinned in test_line_losses_penalty_sink.cpp.
  double overflow_cost {0.0};
};

[[nodiscard]] bool sos2_available()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver("cplex");
}

struct EnvFixture
{
  System system;
  Simulation simulation;
  PlanningOptions opts;
  PlanningOptionsLP options;
  SimulationLP sim_lp;
  SystemLP sys_lp;

  explicit EnvFixture(const EnvCase& c)
      : system {
            .name = "LossEnvelope2Bus",
            .bus_array =
                {
                    {
                        .uid = Uid {1},
                        .name = "b1",
                    },
                    {
                        .uid = Uid {2},
                        .name = "b2",
                    },
                },
            .demand_array =
                {
                    {
                        .uid = Uid {1},
                        .name = "d1",
                        .bus = Uid {2},
                        .capacity = c.demand,
                    },
                },
            .generator_array =
                {
                    {
                        .uid = Uid {1},
                        .name = "g1",
                        .bus = Uid {1},
                        .gcost = kGcost,
                        .capacity = 2.0 * kTmax,
                    },
                },
            .line_array =
                {
                    make_line(c),
                },
        }
      , simulation {
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
        }
      , opts {}
      , options(make_options())
      , sim_lp(simulation, options)
      , sys_lp(system, sim_lp, build_opts(c.use_sos2))
  {
  }

  [[nodiscard]] auto& lp() { return sys_lp.linear_interface(); }

private:
  static Line make_line(const EnvCase& c)
  {
    Line ln {
        .uid = Uid {1},
        .name = "l1",
        .bus_a = Uid {1},
        .bus_b = Uid {2},
        .voltage = kV,
        .resistance = kR,
        .line_losses_mode = OptName {std::string(c.mode)},
        .loss_segments = kTangents,
        .tmax_ba = c.tmax,
        .tmax_ab = c.tmax,
        .capacity = c.tmax,
    };
    if (c.envelope > 0.0) {
      ln.loss_envelope = OptTRealFieldSched {c.envelope};
    }
    if (!c.layout.empty()) {
      ln.loss_pwl_layout = std::string(c.layout);
    }
    ln.loss_secant_segments = c.secant_segments;
    ln.loss_use_sos2 = c.use_sos2;
    ln.loss_cost_eps = c.loss_cost_eps;
    ln.loss_overflow_cost = c.overflow_cost;
    return ln;
  }

  PlanningOptionsLP make_options()
  {
    opts.model_options.use_single_bus = false;
    opts.model_options.use_kirchhoff = false;
    opts.model_options.scale_objective = 1000.0;
    opts.model_options.demand_fail_cost = kFailCost;
    opts.lp_matrix_options.col_with_names = true;
    opts.lp_matrix_options.row_with_names = true;
    opts.lp_matrix_options.col_with_name_map = true;
    opts.lp_matrix_options.row_with_name_map = true;
    return PlanningOptionsLP(opts);
  }

  static LpMatrixOptions build_opts(bool wants_sos2)
  {
    LpMatrixOptions bo;
    bo.col_with_names = true;
    bo.col_with_name_map = true;
    bo.row_with_names = true;
    bo.row_with_name_map = true;
    if (wants_sos2) {
      auto& reg = SolverRegistry::instance();
      reg.load_all_plugins();
      if (reg.has_solver("cplex")) {
        bo.solver_name = "cplex";
      }
    }
    return bo;
  }
};

/// Sum of the primal values of every column whose name contains
/// ``substr`` but not ``_seg`` (the per-direction PWL segment columns
/// are named ``line_flowp_seg_…``; 0 when none).
[[nodiscard]] double sum_cols(const LinearInterface& li,
                              std::span<const double> sol,
                              std::string_view substr)
{
  double total = 0.0;
  for (const auto& [name, idx] : li.col_name_map()) {
    if (name.contains(substr) && !name.contains("_seg")) {
      total += sol[static_cast<std::size_t>(value_of(idx))];
    }
  }
  return total;
}

[[nodiscard]] std::vector<ColIndex> cols_containing(const LinearInterface& li,
                                                    std::string_view substr)
{
  std::vector<ColIndex> out;
  const bool want_seg = substr.contains("seg");
  for (const auto& [name, idx] : li.col_name_map()) {
    if (name.contains(substr) && (want_seg || !name.contains("_seg"))) {
      out.push_back(idx);
    }
  }
  return out;
}

[[nodiscard]] RowIndex find_row(const LinearInterface& li,
                                std::string_view substr)
{
  RowIndex found {-1};
  int count = 0;
  for (const auto& [name, idx] : li.row_name_map()) {
    if (name.contains(substr)) {
      found = idx;
      ++count;
    }
  }
  REQUIRE(count == 1);
  return found;
}

/// Maximum over the K tangent rows of ``tangents(f)`` for the signed
/// layout ``f_k = env·(2k − K − 1)/K`` used by ``tangent_signed_flow``.
[[nodiscard]] double signed_tangent_lb(double f, double env)
{
  double lb = 0.0;
  for (int k = 1; k <= kTangents; ++k) {
    const double fk = env * static_cast<double>((2 * k) - kTangents - 1)
        / static_cast<double>(kTangents);
    lb = std::max(lb, (2.0 * kLossK * fk * f) - (kLossK * fk * fk));
  }
  return lb;
}

/// Same for the ``piecewise`` tangent layout,
/// ``t_k = env·(2k − 1)/(2K)`` on ``|f|``.
[[nodiscard]] double unsigned_tangent_lb(double f, double env)
{
  double lb = 0.0;
  for (int k = 1; k <= kTangents; ++k) {
    const double tk = env * static_cast<double>((2 * k) - 1)
        / (2.0 * static_cast<double>(kTangents));
    lb = std::max(lb, (2.0 * kLossK * tk * std::abs(f)) - (kLossK * tk * tk));
  }
  return lb;
}

struct Outcome
{
  double flow;
  double loss;
  double obj;
  double price_ratio;  // π_b2 / π_b1
};

/// Solve ``c`` and run the invariant battery shared by every mode.
Outcome solve_and_check(const EnvCase& c)
{
  EnvFixture fx(c);
  auto& li = fx.lp();
  auto r = li.resolve();
  REQUIRE(r.has_value());
  REQUIRE(r.value() == 0);

  const auto sol = li.get_col_sol_raw();
  const bool signed_flow = c.mode == "tangent_signed_flow";
  const double flow = signed_flow
      ? sum_cols(li, sol, "line_flows_")
      : sum_cols(li, sol, "line_flowp_") - sum_cols(li, sol, "line_flown_");
  const double loss =
      sum_cols(li, sol, "line_lossp_") + sum_cols(li, sol, "line_lossn_");
  const double obj = li.get_obj_value();

  CAPTURE(c.mode);
  CAPTURE(c.layout);
  CAPTURE(c.secant_segments);
  CAPTURE(c.use_sos2);
  CAPTURE(flow);
  CAPTURE(loss);
  CAPTURE(obj);

  // (1) Demand served in full: the objective is generation cost
  // (gen = demand + losses) plus the loss-column ε — no demand-fail
  // term.
  CHECK(obj
        == doctest::Approx((kGcost * (c.demand + loss))
                           + (c.loss_cost_eps * loss))
               .epsilon(1e-6));

  // (2) Flow reaches past the envelope to (at least) the demand — the
  // receiver's share of the loss rides on top.
  CHECK(flow >= c.demand - 1e-6);
  CHECK(flow <= c.demand + loss + 1e-6);

  // (3) Physically sensible loss: at or above the inner tangent lower
  // envelope and — past the envelope — QUADRATIC like PLEXOS (within
  // the ≈ 2.8 % geometric-ladder error, NOT a linear extrapolation
  // that would be ~2× low at 3× rating).
  const double env = c.envelope > 0.0 ? c.envelope : c.tmax;
  const double lb = signed_flow ? signed_tangent_lb(flow, env)
                                : unsigned_tangent_lb(flow, env);
  const double quad = kLossK * flow * flow;
  CAPTURE(quad);
  CHECK(loss >= lb - 1e-6);
  CHECK(loss > 0.0);
  CHECK(std::abs(loss - quad) <= 0.03 * quad);

  double price_ratio = 0.0;
  if (!c.use_sos2) {
    // (4) Receiving bus priced at the loss-adjusted marginal generator,
    // NOT at the demand-fail cost (a hidden flow cap would make the
    // demand fail and pin π_b2 = 1000 → ratio 100).
    const auto duals = li.get_row_dual_raw();
    const double pi1 = duals[static_cast<std::size_t>(
        value_of(find_row(li, "bus_balance_1_")))];
    const double pi2 = duals[static_cast<std::size_t>(
        value_of(find_row(li, "bus_balance_2_")))];
    REQUIRE(std::abs(pi1) > 0.0);
    price_ratio = pi2 / pi1;
    CAPTURE(pi1);
    CAPTURE(pi2);
    CHECK(price_ratio > 1.0);
    CHECK(price_ratio < 1.5);

    // (5) No reduced-cost cap: the flow column is strictly inside its
    // [−tmax, tmax] box (line "dual" = 0) and no loss auxiliary column
    // (|f| segments, overflow, flow segments) carries a material reduced
    // cost.  Before the fix the v_l columns sat at their envelope bound
    // with |rc| ≈ π_b2 − π_b1 ≈ 99·π_b1.
    const auto rc = li.get_col_cost_raw();
    const auto rc_of = [&](ColIndex col)
    { return std::abs(rc[static_cast<std::size_t>(value_of(col))]); };
    // Only the CARRYING flow column(s): an idle reverse ``flown`` sits
    // at its 0 lower bound with a legitimate positive reduced cost.
    for (const auto* sub : {"line_flows_", "line_flowp_", "line_flown_"}) {
      for (const auto& col : cols_containing(li, sub)) {
        if (std::abs(sol[static_cast<std::size_t>(value_of(col))]) <= 1e-6) {
          continue;
        }
        CAPTURE(sub);
        CHECK(rc_of(col) <= (1e-6 * std::abs(pi1)) + 1e-9);
      }
    }
    // |f| auxiliaries: only the 1e-6 $/MWh internal pin may show.
    for (const auto& col : cols_containing(li, "line_flow_abs_")) {
      CHECK(rc_of(col) <= 0.01 * std::abs(pi1));
    }
    // (Secant flow segments are not checked: a full low-slope segment
    // legitimately prices its slope gap to the marginal one; a hidden
    // cap there shows up as unserved demand in (1)/(2) instead.)
    CHECK(std::abs(flow) < c.tmax - 1.0);
  }

  return {
      .flow = flow,
      .loss = loss,
      .obj = obj,
      .price_ratio = price_ratio,
  };
}

}  // namespace

}  // namespace test_line_loss_envelope_ns

using namespace test_line_loss_envelope_ns;

TEST_CASE(
    "loss_envelope - tangent_signed_flow L=1 lets flow pass the "
    "envelope")  // NOLINT
{
  const auto out = solve_and_check({.mode = "tangent_signed_flow"});
  // Positive prices drive ℓ onto the tangent lower envelope, which past
  // the envelope now includes the overflow tangents: at |f| ≈ 3·env the
  // loss is ≈ 9 MW = R·f²/V² (from below, ≤ 2.8 %), not the ≈ 4.2 MW a
  // linear extrapolation of the outermost inner tangent would give.
  const double quad = kLossK * out.flow * out.flow;
  CHECK(out.loss <= quad + 1e-9);
  CHECK(out.loss == doctest::Approx(quad).epsilon(0.03));
  CHECK(out.loss > 2.0 * signed_tangent_lb(out.flow, kRating));
}

TEST_CASE(
    "loss_envelope - tangent_signed_flow L=4 eps-rely lets flow pass the "
    "envelope")  // NOLINT
{
  std::ignore = solve_and_check({
      .mode = "tangent_signed_flow",
      .secant_segments = 4,
      .loss_cost_eps = 0.1,
  });
}

TEST_CASE(
    "loss_envelope - tangent_signed_flow without loss eps (L=1) still "
    "passes")  // NOLINT
{
  std::ignore = solve_and_check({
      .mode = "tangent_signed_flow",
      .loss_cost_eps = 0.0,
  });
}

TEST_CASE("loss_envelope - tangent_signed_flow deep overflow (8x)")  // NOLINT
{
  std::ignore = solve_and_check({
      .mode = "tangent_signed_flow",
      .demand = 8.0 * kRating,
  });
}

TEST_CASE("loss_envelope - tangent_signed_flow SOS2 lambda-form")  // NOLINT
{
  if (!sos2_available()) {
    MESSAGE("skipping: no SOS2-capable solver (cplex) available");
    return;
  }
  std::ignore = solve_and_check({
      .mode = "tangent_signed_flow",
      .secant_segments = 4,
      .use_sos2 = true,
  });
}

TEST_CASE("loss_envelope - piecewise tangent layout")  // NOLINT
{
  std::ignore = solve_and_check({
      .mode = "piecewise",
      .layout = "tangent",
  });
}

TEST_CASE(
    "loss_envelope - piecewise / bidirectional secant layouts keep the "
    "overflow quadratic")  // NOLINT
{
  for (const auto* layout : {"uniform", "midpoint"}) {
    for (const auto* mode : {"piecewise", "bidirectional"}) {
      CAPTURE(layout);
      CAPTURE(mode);
      std::ignore = solve_and_check({
          .mode = mode,
          .layout = layout,
      });
    }
  }
}

TEST_CASE(
    "loss_envelope - tangent_signed_flow overflow column bounded by "
    "tmax headroom")  // NOLINT
{
  EnvFixture fx({.mode = "tangent_signed_flow"});
  auto& li = fx.lp();
  const auto upp = li.get_col_upp_raw();
  const auto abs_cols = cols_containing(li, "line_flow_abs_");
  // L = 1: the envelope segment v (width = env) + M overflow columns
  // on the geometric ladder env·10^(j/M), M = ⌈ln 10 / ln 1.4⌉ = 7,
  // jointly spanning the headroom up to tmax.
  REQUIRE(abs_cols.size() == 8);
  double total = 0.0;
  double widest = 0.0;
  for (const auto& col : abs_cols) {
    const double w = upp[static_cast<std::size_t>(value_of(col))];
    total += w;
    widest = std::max(widest, w);
  }
  CHECK(total == doctest::Approx(kTmax));
  // Last overflow segment [tmax·10^(−1/7), tmax].
  CHECK(widest == doctest::Approx(kTmax * (1.0 - std::pow(10.0, -1.0 / 7.0))));
}

TEST_CASE(
    "loss_envelope - non-decoupled tangent_signed_flow LP is "
    "unchanged")  // NOLINT
{
  // ``loss_envelope`` unset and ``loss_envelope == tmax`` (no headroom
  // past the envelope) must build the SAME LP as before the fix: no
  // overflow column, no extra λ breakpoint, identical bounds / costs.
  for (const int L : {1, 4}) {
    CAPTURE(L);
    EnvFixture unset({
        .mode = "tangent_signed_flow",
        .secant_segments = L,
        .envelope = 0.0,
        .tmax = 400.0,
        .demand = 300.0,
    });
    EnvFixture at_cap({
        .mode = "tangent_signed_flow",
        .secant_segments = L,
        .envelope = 400.0,
        .tmax = 400.0,
        .demand = 300.0,
    });
    auto& a = unset.lp();
    auto& b = at_cap.lp();
    REQUIRE(a.get_numcols() == b.get_numcols());
    REQUIRE(a.get_numrows() == b.get_numrows());
    // Legacy layout: flow + loss + L |f| segments (+ gen + demand
    // cols; same for both), and exactly L ``line_flow_abs_`` columns.
    CHECK(cols_containing(a, "line_flow_abs_").size()
          == static_cast<std::size_t>(L));
    CHECK(cols_containing(b, "line_flow_abs_").size()
          == static_cast<std::size_t>(L));

    const auto spans_equal = [](auto x, auto y)
    {
      return std::ranges::equal(
          x, y, [](double p, double q) { return p == q; });
    };
    CHECK(spans_equal(a.get_col_low_raw(), b.get_col_low_raw()));
    CHECK(spans_equal(a.get_col_upp_raw(), b.get_col_upp_raw()));
    CHECK(spans_equal(a.get_row_low_raw(), b.get_row_low_raw()));
    CHECK(spans_equal(a.get_row_upp_raw(), b.get_row_upp_raw()));
    CHECK(spans_equal(a.get_obj_coeff(), b.get_obj_coeff()));

    // Abs-segment bounds stay at the envelope share tmax/L.
    const auto upp = a.get_col_upp_raw();
    for (const auto& col : cols_containing(a, "line_flow_abs_")) {
      CHECK(upp[static_cast<std::size_t>(value_of(col))]
            == doctest::Approx(400.0 / static_cast<double>(L)));
    }

    auto ra = a.resolve();
    auto rb = b.resolve();
    REQUIRE(ra.has_value());
    REQUIRE(rb.has_value());
    CHECK(a.get_obj_value() == doctest::Approx(b.get_obj_value()));
  }
}
