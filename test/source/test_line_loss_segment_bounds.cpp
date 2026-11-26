// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Bounds of the line-loss columns, per loss mode.
//
// gtopt builds no per-segment LOSS columns: the PWL segment columns are
// FLOW segments (`flowp_seg` / `flown_seg` of the secant modes, the
// `flow_abs` |f| segments of `tangent_signed_flow`, the segments of
// `piecewise_direct` that stamp their loss factor on the bus balances),
// and segment k books the loss `loss_k · seg_k`.  Its loss is therefore
// bounded through its flow bound:
//
//   * total loss column (`lossp` / `lossn` / signed `lossp`): upper bound
//     `rated_loss_cap` = max(R·tmax²/V², ordered secant at tmax) of the
//     direction (`max(tmax_ab, tmax_ba)` for the signed column);
//   * segment column k: `min(w_k, max(0, tmax − b_{k−1}))` — its width,
//     and with `model_options.loss_segment_bound` (default on) the
//     ordered-fill cap; the segments then book at most the ordered
//     secant at tmax (`R·tmax²/V²` when tmax is a breakpoint).
//
// These are valid tightening bounds: they never cut a physical (ordered
// fill, |f| ≤ tmax) point, and they limit — not prevent — the loss the LP
// can book non-physically under a negative nodal price.
//
// Fixture: b1 —(R = 0.01, V = 1, X = 0.1, tmax_ab = 100, tmax_ba = 60)— b2,
// K = 4 segments, k_loss = R/V² = 0.01.

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <format>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace
{

constexpr double kLoss = 0.01;  // R / V²
constexpr double kTmaxAb = 100.0;
constexpr double kTmaxBa = 60.0;
constexpr int kSeg = 4;

struct Case
{
  std::string mode {};
  std::optional<double> envelope {};
  bool segment_bound {true};
  double demand {50.0};
  /// Generator at b2 with this cost (negative ⇒ negative LMP at b2).
  std::optional<double> b2_gen_cost {};
};

[[nodiscard]] std::string to_json(const Case& c)
{
  const std::string env =
      c.envelope ? std::format(R"(, "loss_envelope": {})", *c.envelope) : "";
  const std::string g2 = c.b2_gen_cost
      ? std::format(R"(, {{"uid": 2, "name": "g2", "bus": "b2", "pmin": 0,)"
                    R"( "pmax": 500, "gcost": {}, "capacity": 500}})",
                    *c.b2_gen_cost)
      : "";
  return std::format(
      R"({{"options": {{"annual_discount_rate": 0.0,
           "output_format": "csv", "output_compression": "uncompressed",
           "model_options": {{"use_single_bus": false,
             "use_kirchhoff": true, "scale_objective": 1,
             "demand_fail_cost": 1000, "loss_segment_bound": {}}}}},
         "simulation": {{"block_array": [{{"uid": 1, "duration": 1}}],
           "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}}],
           "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
         "system": {{"name": "lossb",
           "bus_array": [{{"uid": 1, "name": "b1"}}, {{"uid": 2, "name": "b2"}}],
           "generator_array": [{{"uid": 1, "name": "g1", "bus": "b1",
             "pmin": 0, "pmax": 500, "gcost": 10, "capacity": 500}}{}],
           "demand_array": [{{"uid": 1, "name": "d2", "bus": "b2",
             "lmax": [[{}]]}}],
           "line_array": [{{"uid": 1, "name": "l", "bus_a": "b1",
             "bus_b": "b2", "reactance": 0.1, "resistance": 0.01,
             "voltage": 1, "tmax_ab": {}, "tmax_ba": {},
             "line_losses_mode": "{}", "loss_segments": {}{}}}]}}}})",
      c.segment_bound ? "true" : "false",
      g2,
      c.demand,
      kTmaxAb,
      kTmaxBa,
      c.mode,
      kSeg,
      env);
}

struct ColInfo
{
  std::string name {};
  double low {};
  double upp {};  ///< physical
  double upp_raw {};  ///< LP space
  double scale {};
  double sol {};
};

struct Result
{
  double obj {0.0};
  std::vector<ColInfo> cols {};

  [[nodiscard]] std::vector<ColInfo> with(std::string_view needle,
                                          std::string_view exclude = {}) const
  {
    std::vector<ColInfo> out;
    for (const auto& c : cols) {
      if (c.name.contains(needle)
          && (exclude.empty() || !c.name.contains(exclude)))
      {
        out.push_back(c);
      }
    }
    return out;
  }
  [[nodiscard]] double sum_sol(std::string_view needle,
                               std::string_view exclude = {}) const
  {
    double s = 0.0;
    for (const auto& c : with(needle, exclude)) {
      s += c.sol;
    }
    return s;
  }
};

[[nodiscard]] Result solve(const Case& c)
{
  auto planning = daw::json::from_json<Planning>(to_json(c), StrictParsePolicy);
  LpMatrixOptions flat;
  flat.col_with_names = true;
  flat.col_with_name_map = true;
  PlanningLP plp(std::move(planning), flat);
  REQUIRE(plp.resolve().has_value());
  auto& li = plp.systems().front().front().linear_interface();
  Result r;
  r.obj = li.get_obj_value();
  const auto upp = li.get_col_upp();
  const auto low = li.get_col_low();
  const auto upp_raw = li.get_col_upp_raw();
  const auto sol = li.get_col_sol();
  for (const auto& [name, idx] : li.col_name_map()) {
    if (!name.starts_with("line_")) {
      continue;
    }
    r.cols.push_back({
        .name = std::string {name},
        .low = low[idx],
        .upp = upp[idx],
        .upp_raw = upp_raw[static_cast<std::size_t>(value_of(idx))],
        .scale = li.get_col_scale(idx),
        .sol = sol[idx],
    });
  }
  // Name order = segment order (the context index k is the last field).
  std::ranges::sort(r.cols, {}, &ColInfo::name);
  return r;
}

/// Analytic rated loss of a direction (`rated_loss_cap`): the larger of
/// k·tmax² and the ordered secant of K equal chords on [0, env] at tmax.
[[nodiscard]] double rated_cap(double tmax, std::optional<double> env)
{
  const double quad = kLoss * tmax * tmax;
  if (!env || *env <= tmax) {
    return quad;
  }
  const double w = *env / kSeg;
  const double m = std::floor(tmax / w);
  const double b = m * w;
  return std::max(quad,
                  kLoss * ((b * b) + (((2.0 * m) + 1.0) * w * (tmax - b))));
}

/// Ordered-fill segment bounds for a direction of cap `tmax` whose K
/// segments of width `w` start at 0.
[[nodiscard]] std::vector<double> ordered_caps(double w, double tmax)
{
  std::vector<double> out;
  out.reserve(static_cast<std::size_t>(kSeg));
  for (int k = 0; k < kSeg; ++k) {
    out.push_back(std::min(w, std::max(0.0, tmax - (k * w))));
  }
  return out;
}

void check_scaled(const ColInfo& c)
{
  CAPTURE(c.name);
  // LP-space bound is the physical bound over the column scale.
  CHECK(std::isfinite(c.upp_raw));
  CHECK(c.upp_raw * c.scale == doctest::Approx(c.upp));
}

}  // namespace

TEST_CASE("loss bounds — loss columns at the analytic rated loss")  // NOLINT
{
  for (const std::string mode : {"piecewise", "bidirectional", "adaptive"}) {
    CAPTURE(mode);
    const auto r = solve({.mode = mode});
    const auto lp = r.with("line_lossp_");
    const auto ln = r.with("line_lossn_");
    REQUIRE(lp.size() == 1);
    REQUIRE(ln.size() == 1);
    CHECK(lp.front().upp == doctest::Approx(kLoss * kTmaxAb * kTmaxAb));
    CHECK(ln.front().upp == doctest::Approx(kLoss * kTmaxBa * kTmaxBa));
    check_scaled(lp.front());
    check_scaled(ln.front());
    // Segments: finite, width = tmax/K, Σ = tmax per direction.
    for (const auto& [needle, tmax] :
         {std::pair {"flowp_seg", kTmaxAb}, std::pair {"flown_seg", kTmaxBa}})
    {
      CAPTURE(needle);
      const auto segs = r.with(needle);
      REQUIRE(segs.size() == kSeg);
      double sum = 0.0;
      for (const auto& s : segs) {
        CHECK(s.upp == doctest::Approx(tmax / kSeg));
        check_scaled(s);
        sum += s.upp;
      }
      CHECK(sum == doctest::Approx(tmax));
      // Σ per-segment loss bound = Σ k·(b_k² − b_{k−1}²) = k·tmax².
    }
  }
  SUBCASE("tangent_signed_flow: one signed loss column, |f| segment")
  {
    const auto r = solve({.mode = "tangent_signed_flow"});
    const auto loss = r.with("line_lossp_");
    REQUIRE(loss.size() == 1);
    CHECK(loss.front().upp == doctest::Approx(kLoss * kTmaxAb * kTmaxAb));
    check_scaled(loss.front());
    for (const auto& v : r.with("line_flow_abs_")) {
      CHECK(v.upp == doctest::Approx(kTmaxAb));
    }
  }
  SUBCASE("piecewise_direct: segments carry the loss, bounded by width")
  {
    const auto r = solve({.mode = "piecewise_direct"});
    CHECK(r.with("line_lossp_").empty());
    const auto segs = r.with("flowp_seg");
    REQUIRE(segs.size() == kSeg);
    for (const auto& s : segs) {
      CHECK(s.upp == doctest::Approx(kTmaxAb / kSeg));
    }
  }
}

TEST_CASE("loss bounds — envelope wider than tmax: ordered-fill cap")  // NOLINT
{
  // loss_envelope = 200 > tmax: K = 4 segments of width 50 over [0, 200].
  // Legacy: 50, 50, 50, +inf.  Ordered fill at |f| ≤ tmax: 50, 50, 0, 0
  // (A→B, tmax 100) and 50, 10, 0, 0 (B→A, tmax 60).
  constexpr double env = 200.0;
  constexpr double w = env / kSeg;
  for (const std::string mode : {"piecewise", "bidirectional"}) {
    CAPTURE(mode);
    const auto r = solve({.mode = mode, .envelope = env});
    const auto legacy =
        solve({.mode = mode, .envelope = env, .segment_bound = false});
    for (const auto& [needle, tmax] :
         {std::pair {"flowp_seg", kTmaxAb}, std::pair {"flown_seg", kTmaxBa}})
    {
      CAPTURE(needle);
      const auto segs = r.with(needle);
      REQUIRE(segs.size() == kSeg);
      const auto caps = ordered_caps(w, tmax);
      double seg_loss_bound = 0.0;
      for (int k = 0; k < kSeg; ++k) {
        const auto& s = segs[static_cast<std::size_t>(k)];
        CAPTURE(s.name);
        CHECK(s.upp == doctest::Approx(caps[static_cast<std::size_t>(k)]));
        check_scaled(s);
        // Loss bound of segment k = slope_k · ub_k, slope = k_loss·w·(2k+1).
        seg_loss_bound += kLoss * w * ((2.0 * k) + 1.0) * s.upp;
      }
      // Σ of the segment loss bounds = the ordered secant at tmax, the
      // loss column's own cap (k·tmax² when tmax is a breakpoint).
      CHECK(seg_loss_bound == doctest::Approx(rated_cap(tmax, env)));
      // Legacy leaves the last segment unbounded.
      const auto lsegs = legacy.with(needle);
      REQUIRE(lsegs.size() == kSeg);
      CHECK(lsegs.back().upp >= 1e20);  // solver infinity (CPLEX: exactly 1e20)
    }
    // Bound does not bind at an ordinary dispatch: same optimum.
    CHECK(r.obj == doctest::Approx(legacy.obj));
  }
  SUBCASE("tangent_signed_flow |f| segment capped at tmax")
  {
    const auto r = solve({.mode = "tangent_signed_flow", .envelope = env});
    const auto legacy = solve({.mode = "tangent_signed_flow",
                               .envelope = env,
                               .segment_bound = false});
    for (const auto& v : r.with("line_flow_abs_")) {
      CHECK(v.upp == doctest::Approx(kTmaxAb));
    }
    for (const auto& v : legacy.with("line_flow_abs_")) {
      CHECK(v.upp == doctest::Approx(env));
    }
    CHECK(r.obj == doctest::Approx(legacy.obj));
  }
}

TEST_CASE("loss bounds — never cut off the true loss")  // NOLINT
{
  // Drive the A→B flow over a range of values up to tmax (demand above
  // tmax saturates the line; the rest is curtailed).  The loss the LP
  // books stays within its bound and, for the secant modes, on or above
  // the analytic k·f² (secants over-estimate); the optimum is the same
  // with and without the segment bound.
  for (const std::string mode :
       {"piecewise", "bidirectional", "tangent_signed_flow"})
  {
    CAPTURE(mode);
    for (const std::optional<double> env :
         {std::optional<double> {}, std::optional<double> {200.0}})
    {
      CAPTURE(env.value_or(0.0));
      for (const double d : {10.0, 25.0, 50.0, 80.0, 99.0, 150.0}) {
        CAPTURE(d);
        const auto r = solve({.mode = mode, .envelope = env, .demand = d});
        const auto legacy = solve({.mode = mode,
                                   .envelope = env,
                                   .segment_bound = false,
                                   .demand = d});
        CHECK(r.obj == doctest::Approx(legacy.obj).epsilon(1e-9));
        const double f = (mode == "tangent_signed_flow")
            ? r.sum_sol("line_flows_")
            : r.sum_sol("line_flowp_", "_seg")
                - r.sum_sol("line_flown_", "_seg");
        CHECK(f <= kTmaxAb + 1e-6);
        const auto loss = r.with("line_lossp_");
        REQUIRE(!loss.empty());
        const double booked = loss.front().sol;
        CHECK(booked <= loss.front().upp + 1e-6);
        const double exact = kLoss * f * f;
        CHECK(loss.front().upp >= exact - 1e-6);
        if (mode != "tangent_signed_flow") {
          CHECK(booked >= exact - 1e-6);
        } else {
          CHECK(booked <= exact + 1e-6);
        }
      }
    }
  }
}

TEST_CASE(
    "loss bounds — negative LMP: phantom loss capped, not zero")  // NOLINT
{
  // A generator at b2 is PAID to produce (−100 $/MWh) and there is no
  // demand beyond 1 MW: the only extra sink is line loss, so the LP books
  // non-physical (circulation) loss.  The bounds do not prevent that;
  // they cap it at the analytic rated loss — per direction and in total —
  // and never above what the legacy bounds allow.
  for (const std::string mode :
       {"piecewise", "bidirectional", "tangent_signed_flow"})
  {
    CAPTURE(mode);
    for (const std::optional<double> env :
         {std::optional<double> {}, std::optional<double> {200.0}})
    {
      CAPTURE(env.value_or(0.0));
      const Case base {
          .mode = mode,
          .envelope = env,
          .demand = 1.0,
          .b2_gen_cost = -100.0,
      };
      auto leg = base;
      leg.segment_bound = false;
      const auto r = solve(base);
      const auto legacy = solve(leg);
      const double loss = r.sum_sol("line_loss");
      const double loss_legacy = legacy.sum_sol("line_loss");
      const double cap_ab = rated_cap(kTmaxAb, env);
      const double cap_ba = rated_cap(kTmaxBa, env);
      const double cap_total = (mode == "tangent_signed_flow")
          ? kLoss * kTmaxAb * kTmaxAb
          : cap_ab + cap_ba;
      CHECK(loss <= cap_total + 1e-6);
      CHECK(loss <= loss_legacy + 1e-6);
      for (const auto& c : r.with("line_lossp_")) {
        CHECK(c.sol <= cap_ab + 1e-6);
      }
      for (const auto& c : r.with("line_lossn_")) {
        CHECK(c.sol <= cap_ba + 1e-6);
      }
      // Each segment books at most its analytic share.
      for (const auto& s : r.with("_seg")) {
        CHECK(s.sol <= s.upp + 1e-6);
      }
    }
  }
}
