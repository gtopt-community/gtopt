// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_reserve_extended.cpp
 * @brief     SDDP on a hydro-thermal case with the extended reserve model
 * @date      2026-11-23
 *
 * Case: xres_sddp_case.hpp — ReserveZone with three requirement planes
 * (demand, inertia, contingency terms), an InertiaZone with a commitment
 * credit, and a BESS offering capped two-sided reserve (split columns).
 * The reservoir and the BESS SoC are the cross-phase states.
 *
 * Reference: the SAME JSON built as ONE phase (one LP over the horizon).
 * SDDP over three phases (deterministic, one scenario) must
 *  - converge to the one-phase optimum (relative 1e-6),
 *  - keep LB ≤ optimum ≤ UB at every iteration (valid cuts),
 *  - reproduce, phase by phase, the reserve-price, plane, inertia and bus
 *    duals of the one-phase LP (abs/rel 1e-6),
 * and `low_memory = compress` must replay the new rows / columns: the
 * same iteration-0 bounds and the same converged bounds and duals as `off`
 * (later trial points may differ: the phase LPs have alternative optima).
 */

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_enums.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>

#include "xres_sddp_case.hpp"

using namespace gtopt;
using namespace gtopt::test_xres;

namespace
{

constexpr double kSxrTol = 1.0e-6;

struct SxrRun
{
  std::vector<SDDPIterationResult> trace;
  XresSnapshot snap;
};

[[nodiscard]] SDDPOptions sxr_sddp_options(LowMemoryMode mode)
{
  SDDPOptions opts;
  opts.max_iterations = 200;
  opts.convergence_tol = 1.0e-8;
  opts.convergence_mode = ConvergenceMode::gap_only;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  opts.apertures = std::vector<Uid> {};  // deterministic: pure Benders
  opts.low_memory_mode = mode;
  return opts;
}

[[nodiscard]] SxrRun sxr_run_sddp(const XresCase& c, LowMemoryMode mode)
{
  PlanningLP plp(xres_planning(c));
  SDDPMethod sddp(plp, sxr_sddp_options(mode));
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  SxrRun out;
  out.trace = std::move(*r);
  out.snap = xres_snapshot(plp);
  return out;
}

[[nodiscard]] XresSnapshot sxr_run_monolithic(XresCase c)
{
  c.phases = false;
  PlanningLP plp(xres_planning(c));
  REQUIRE(plp.resolve().has_value());
  return xres_snapshot(plp);
}

void sxr_check_bounds(const std::vector<SDDPIterationResult>& trace,
                      double optimum)
{
  const double slack = kSxrTol * std::max(1.0, std::abs(optimum));
  for (std::size_t i = 0; i < trace.size(); ++i) {
    const auto& it = trace[i];
    CAPTURE(i);
    CAPTURE(it.lower_bound);
    CAPTURE(it.upper_bound);
    CHECK(it.lower_bound <= it.upper_bound + slack);
    CHECK(it.lower_bound <= optimum + slack);
    CHECK(it.upper_bound >= optimum - slack);
  }
}

}  // namespace

TEST_CASE("SDDP extended reserves: converges to the one-phase LP")  // NOLINT
{
  const auto mono = sxr_run_monolithic({});
  const auto sddp = sxr_run_sddp({}, LowMemoryMode::off);
  const auto& last = sddp.trace.back();

  CHECK(last.converged);
  CHECK(last.upper_bound == doctest::Approx(mono.objective).epsilon(kSxrTol));
  CHECK(last.lower_bound == doctest::Approx(mono.objective).epsilon(kSxrTol));

  SUBCASE("cuts stay valid: LB <= optimum <= UB at every iteration")
  {
    sxr_check_bounds(sddp.trace, mono.objective);
  }

  SUBCASE("the requirement covers its exogenous plane in every phase")
  {
    xres_check_requirement_floor(mono.ureq);
    xres_check_requirement_floor(sddp.snap.ureq);
  }

  SUBCASE("reserve, plane, inertia and bus duals match per phase")
  {
    xres_check_same(mono.mu_up, sddp.snap.mu_up, "mu_up", kSxrTol);
    xres_check_same(mono.mu_dn, sddp.snap.mu_dn, "mu_dn", kSxrTol);
    xres_check_same(
        mono.mu_inertia, sddp.snap.mu_inertia, "mu_inertia", kSxrTol);
    xres_check_same(mono.lambda, sddp.snap.lambda, "lambda", kSxrTol);
    REQUIRE(mono.plane_duals.size() == sddp.snap.plane_duals.size());
    for (const auto& [key, planes] : mono.plane_duals) {
      const auto& other = sddp.snap.plane_duals.at(key);
      REQUIRE(planes.size() == 3);
      REQUIRE(other.size() == planes.size());
      for (std::size_t k = 0; k < planes.size(); ++k) {
        CAPTURE(key.first);
        CAPTURE(key.second);
        CAPTURE(k);
        CHECK(other[k] == doctest::Approx(planes[k]).epsilon(kSxrTol));
      }
    }
  }

  SUBCASE("the reserve model is active (non-trivial prices)")
  {
    double max_mu = 0.0;
    for (const auto& [key, mu] : mono.mu_up) {
      max_mu = std::max(max_mu, mu);
    }
    CHECK(max_mu > 0.5);
  }
}

TEST_CASE(  // NOLINT
    "SDDP extended reserves: low_memory compress replays the same run")
{
  const auto off = sxr_run_sddp({}, LowMemoryMode::off);
  const auto cmp = sxr_run_sddp({}, LowMemoryMode::compress);

  REQUIRE_FALSE(off.trace.empty());
  REQUIRE_FALSE(cmp.trace.empty());

  // Iteration 0 runs on the freshly built (off) vs replayed (compress)
  // LPs with no cuts yet: identical rows / columns give identical bounds.
  CHECK(cmp.trace.front().lower_bound
        == doctest::Approx(off.trace.front().lower_bound).epsilon(1e-9));
  CHECK(cmp.trace.front().upper_bound
        == doctest::Approx(off.trace.front().upper_bound).epsilon(1e-9));
  CHECK(cmp.trace.front().cuts_added == off.trace.front().cuts_added);

  // Later iterations may visit a different (equally optimal) trial
  // state: the phase LPs are degenerate in the reservoir / SoC split and
  // a replayed LP has no warm basis.  Measured: iteration 1 LB is equal,
  // UB differs (62723.6 vs 63074.7), both reach 62189.4 by iteration 4.
  // So the per-iteration trace is not pinned; the converged result is.
  const auto& lo = off.trace.back();
  const auto& lc = cmp.trace.back();
  CHECK(lo.converged);
  CHECK(lc.converged);
  CHECK(lc.lower_bound == doctest::Approx(lo.lower_bound).epsilon(kSxrTol));
  CHECK(lc.upper_bound == doctest::Approx(lo.upper_bound).epsilon(kSxrTol));
  xres_check_requirement_floor(cmp.snap.ureq);
  xres_check_same(off.snap.mu_up, cmp.snap.mu_up, "mu_up", kSxrTol);
  xres_check_same(off.snap.mu_dn, cmp.snap.mu_dn, "mu_dn", kSxrTol);
  xres_check_same(
      off.snap.mu_inertia, cmp.snap.mu_inertia, "mu_inertia", kSxrTol);
  xres_check_same(off.snap.lambda, cmp.snap.lambda, "lambda", kSxrTol);
}
