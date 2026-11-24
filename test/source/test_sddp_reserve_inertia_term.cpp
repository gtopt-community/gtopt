// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_reserve_inertia_term.cpp
 * @brief     SDDP with each ReserveZone.inertia_term mode
 * @date      2026-11-23
 *
 * Case: xres_sddp_case.hpp (hydro + thermals with relaxed SimpleCommitment
 * + BESS, three requirement planes on `cpf`, the middle one with
 * inertia_coef −0.002).  For every inertia_term mode
 *   endogenous (PLEXOS-compat, I = Σ pf·r), commitment (I = Σ I_c·u),
 *   exogenous with system_inertia (data), exogenous fallback (no
 *   must-run unit → I = 0),
 * SDDP over three phases must converge to the one-phase LP (relative
 * 1e-6) with valid bounds, and the one-phase optima must be ordered by how
 * much inertia relief each mode can claim:
 *   exogenous(I = 10⁶, plane inactive) ≤ every mode ≤ exogenous(I = 0),
 * since the requirement is decreasing in I and I ≥ 0 in every mode.
 */

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <string>
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

constexpr double kSitTol = 1.0e-6;

[[nodiscard]] XresCase sit_case(std::string mode, std::string cpf_extra = {})
{
  XresCase c;
  c.inertia_term = std::move(mode);
  c.cpf_extra = std::move(cpf_extra);
  return c;
}

[[nodiscard]] double sit_monolithic(XresCase c)
{
  c.phases = false;
  PlanningLP plp(xres_planning(c));
  REQUIRE(plp.resolve().has_value());
  return xres_snapshot(plp).objective;
}

[[nodiscard]] std::vector<SDDPIterationResult> sit_sddp(const XresCase& c)
{
  SDDPOptions opts;
  opts.max_iterations = 200;
  opts.convergence_tol = 1.0e-8;
  opts.convergence_mode = ConvergenceMode::gap_only;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  opts.apertures = std::vector<Uid> {};
  PlanningLP plp(xres_planning(c));
  SDDPMethod sddp(plp, opts);
  auto r = sddp.solve();
  REQUIRE(r.has_value());
  REQUIRE_FALSE(r->empty());
  return std::move(*r);
}

}  // namespace

TEST_CASE("SDDP inertia_term modes converge to the one-phase LP")  // NOLINT
{
  struct Mode
  {
    const char* name;
    const char* extra;
  };
  const std::vector<Mode> modes {
      {.name = "endogenous", .extra = ""},
      {.name = "commitment", .extra = ""},
      {.name = "exogenous", .extra = R"(, "system_inertia": 15000)"},
      {.name = "exogenous", .extra = ""},
  };

  const double lo =
      sit_monolithic(sit_case("exogenous", R"(, "system_inertia": 1000000)"));
  const double hi = sit_monolithic(sit_case("exogenous", ""));
  CHECK(lo < hi);  // the inertia plane binds somewhere at I = 0

  for (const auto& m : modes) {
    CAPTURE(m.name);
    CAPTURE(m.extra);
    const auto c = sit_case(m.name, m.extra);
    const double mono = sit_monolithic(c);
    CHECK(mono >= lo - (kSitTol * std::abs(lo)));
    CHECK(mono <= hi + (kSitTol * std::abs(hi)));

    const auto trace = sit_sddp(c);
    const auto& last = trace.back();
    CHECK(last.converged);
    CHECK(last.upper_bound == doctest::Approx(mono).epsilon(kSitTol));
    CHECK(last.lower_bound == doctest::Approx(mono).epsilon(kSitTol));
    const double slack = kSitTol * std::max(1.0, std::abs(mono));
    for (const auto& it : trace) {
      CHECK(it.lower_bound <= mono + slack);
      CHECK(it.upper_bound >= mono - slack);
    }
  }
}
