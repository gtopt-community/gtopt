// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_decision_variable_global_reserve_sddp.cpp
 * @brief     A global DecisionVariable shared across phases, referenced from
 *            block-level reserve rows: monolithic vs SDDP
 * @date      2026-11-23
 *
 * Documented behaviour (docs/user-constraints.md, "Across phases"): a
 * `scope: "global"` DecisionVariable is ONE value for the whole horizon.  In
 * a multi-phase run it is an implicit `state: true, link: true` variable,
 * its cost is charged once (first phase), and
 *  - under SDDP the converged bound equals the single-LP monolithic optimum;
 *  - multi-phase monolithic solves the phases in sequence (myopic first
 *    phase) but every phase still holds the same value.
 *
 * Case: xres_sddp_case.hpp with `with_dv`: `contract` ∈ [0, 200] at 2 $/MW
 * caps the peaker's up-reserve provision in EVERY block of every phase
 * (block-level UserConstraint referencing the global DV — the scope-reach
 * fix).  Also run with Ruiz equilibration (review §5 item 9 / P1-5: the
 * implicit DV state columns are scaled independently per phase).
 */

#include <algorithm>
#include <cmath>
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

struct DvxRun
{
  std::vector<SDDPIterationResult> trace;
  XresSnapshot snap;
};

[[nodiscard]] DvxRun dvx_run_sddp(const XresCase& c, LowMemoryMode mode)
{
  PlanningLP plp(xres_planning(c));
  SDDPOptions opts;
  opts.max_iterations = 200;
  opts.convergence_tol = 1.0e-8;
  opts.convergence_mode = ConvergenceMode::gap_only;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  opts.apertures = std::vector<Uid> {};
  opts.low_memory_mode = mode;
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  DvxRun out;
  out.trace = std::move(*r);
  out.snap = xres_snapshot(plp);
  return out;
}

[[nodiscard]] XresSnapshot dvx_run_monolithic(const XresCase& c)
{
  PlanningLP plp(xres_planning(c));
  REQUIRE(plp.resolve().has_value());
  return xres_snapshot(plp);
}

[[nodiscard]] XresCase dvx_case()
{
  XresCase c;
  c.with_dv = true;
  return c;
}

}  // namespace

TEST_CASE(  // NOLINT
    "Global DV across phases: SDDP equals the one-phase monolithic LP")
{
  auto one = dvx_case();
  one.phases = false;
  const auto mono = dvx_run_monolithic(one);
  REQUIRE(mono.contract.size() == 1);
  // The contract binds somewhere: the peaker is the marginal up provider in
  // the tightest block, so the optimal contract is strictly inside [0, 200].
  CHECK(mono.contract.front() > 1.0);
  CHECK(mono.contract.front() < 199.0);

  SUBCASE("low_memory off")
  {
    const auto sddp = dvx_run_sddp(dvx_case(), LowMemoryMode::off);
    const auto& last = sddp.trace.back();
    CHECK(last.converged);
    CHECK(last.upper_bound == doctest::Approx(mono.objective).epsilon(1e-6));
    CHECK(last.lower_bound == doctest::Approx(mono.objective).epsilon(1e-6));
    REQUIRE(sddp.snap.contract.size() == 3);
    for (const double v : sddp.snap.contract) {
      CHECK(v == doctest::Approx(mono.contract.front()).epsilon(1e-6));
    }
  }

  SUBCASE("low_memory compress")
  {
    const auto sddp = dvx_run_sddp(dvx_case(), LowMemoryMode::compress);
    CHECK(sddp.trace.back().upper_bound
          == doctest::Approx(mono.objective).epsilon(1e-6));
    REQUIRE(sddp.snap.contract.size() == 3);
    for (const double v : sddp.snap.contract) {
      CHECK(v == doctest::Approx(mono.contract.front()).epsilon(1e-6));
    }
  }
}

TEST_CASE(  // NOLINT
    "Global DV across phases: SDDP with Ruiz equilibration keeps LB <= UB")
{
  // Review §5 item 9 (P1-5): the implicit state column of the DV in phase p
  // is Ruiz-scaled independently of its source in phase p-1.  A cut-slope
  // unit error shows up as LB > UB or a wrong converged value.
  auto one = dvx_case();
  one.phases = false;
  one.options_extra =
      R"(, "lp_matrix_options": {"equilibration_method": "ruiz"})";
  const auto mono = dvx_run_monolithic(one);

  auto c = dvx_case();
  c.options_extra = one.options_extra;
  const auto sddp = dvx_run_sddp(c, LowMemoryMode::off);
  const double slack = 1e-6 * std::max(1.0, std::abs(mono.objective));
  for (std::size_t i = 0; i < sddp.trace.size(); ++i) {
    CAPTURE(i);
    CHECK(sddp.trace[i].lower_bound <= sddp.trace[i].upper_bound + slack);
    CHECK(sddp.trace[i].lower_bound <= mono.objective + slack);
  }
  CHECK(sddp.trace.back().upper_bound
        == doctest::Approx(mono.objective).epsilon(1e-6));
  REQUIRE(sddp.snap.contract.size() == 3);
  CHECK(sddp.snap.contract[1] == doctest::Approx(sddp.snap.contract[0]));
  CHECK(sddp.snap.contract[2] == doctest::Approx(sddp.snap.contract[0]));
}

TEST_CASE(  // NOLINT
    "Global DV across phases: multi-phase monolithic shares one value")
{
  // Sequential (myopic) phases: the value may differ from the SDDP one, but
  // it is ONE value in every phase and the total cost is no better than the
  // one-phase optimum.
  auto one = dvx_case();
  one.phases = false;
  const auto mono1 = dvx_run_monolithic(one);
  const auto mono3 = dvx_run_monolithic(dvx_case());
  REQUIRE(mono3.contract.size() == 3);
  CHECK(mono3.contract[1] == doctest::Approx(mono3.contract[0]));
  CHECK(mono3.contract[2] == doctest::Approx(mono3.contract[0]));
  CHECK(mono3.objective >= mono1.objective - 1e-6);
}
