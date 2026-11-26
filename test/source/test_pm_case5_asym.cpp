// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_pm_case5_asym.cpp — PowerModels case5_asym, DC vs DCPLL.
//
// Literature benchmark for the line-loss / thermal-limit model against
// PowerModels.jl (Coffrin, Bent, Sundar, Ng, Lubin, "PowerModels.jl: An
// Open-Source Framework for Exploring Power Flow Formulations", PSCC
// 2018, DOI 10.23919/PSCC.2018.8442948).  The case is
// cases/pm_case5_asym (MATPOWER `case5_asym` from PowerModels'
// test/data/matpower, branch angle limits widened to ±30°, see
// cases/pm_case5_asym/README.md).  The references come from
// tools/powermodels_solve_golden.jl (golden:
// tools/powermodels_goldens/case5_asym_wide30.json):
//
//   * DCPPowerModel (lossless DC OPF):       17479.896925 $/h (HiGHS)
//   * DCPLLPowerModel (DC + line losses):    17693.807404 $/h (Ipopt)
//
// PowerModels' own test/opf.jl pins 17479 and 17693 (atol 1) with the
// original angle limits, which do not bind in either model.
//
// DCPLL (Coffrin, Van Hentenryck, Bent, PESGM 2012, DOI
// 10.1109/PESGM.2012.6345342) keeps Ohm's law on the from-end power
// `p_fr = −b·(θ_fr − θ_to)`, books the loss at the to-end through the
// convex relaxation `p_fr + p_to ≥ r·p_fr²` and bounds BOTH ends by
// `rate_a`.  In gtopt terms that is, per branch and with the flow
// direction of the reference solution, `receiver` allocation on branches
// loaded from→to and `sender` allocation on branches loaded to→from
// (the case JSON sets these), with the sending-end limit
// `line_thermal_limit_mode = sending`.  The relaxation is tight at the
// reference optimum (every branch loss equals r·p_fr², golden file), so
// the piecewise-linear loss model converges to it as the segments grow:
// the secant PWL over-estimates the loss by at most r·(T/K)²/4 per
// branch.

#include <array>
#include <cmath>
#include <filesystem>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/solver_options.hpp>

#ifndef GTOPT_TEST_CASES_DIR
#  error "GTOPT_TEST_CASES_DIR must point at the in-tree cases/ directory"
#endif

using namespace gtopt;

namespace
{

/// PowerModels DCPPowerModel objective, angles ±30° (HiGHS 1.27.0).
constexpr double kPmDc = 17479.896925381032;
/// PowerModels DCPLLPowerModel objective, angles ±30° (Ipopt 1.16.0,
/// tol 1e-10).
constexpr double kPmDcpll = 17693.807403882074;

struct Variant
{
  std::string_view losses {"piecewise"};  ///< line_losses_mode
  int segments {8};
  std::string_view limit {"sending"};  ///< line_thermal_limit_mode
  /// Keep the per-line DCPLL allocation of the JSON; false clears it
  /// (every line falls back to the default `receiver`).
  bool dcpll_allocation {true};
};

[[nodiscard]] double solve_case(const Variant& v)
{
  const auto path = std::filesystem::path {GTOPT_TEST_CASES_DIR}
      / "pm_case5_asym" / "case5_asym_dcpll_sending.json";
  auto parsed = parse_planning_files({path.string()});
  REQUIRE_MESSAGE(parsed.has_value(), parsed.error_or(""));
  auto planning = std::move(*parsed);
  planning.options.sddp_options.api_enabled = false;

  auto& mo = planning.options.model_options;
  mo.line_losses_mode = std::string {v.losses};
  mo.loss_segments = v.segments;
  mo.line_thermal_limit_mode = std::string {v.limit};
  if (!v.dcpll_allocation) {
    for (auto& ln : planning.system.line_array) {
      ln.loss_allocation_mode.reset();
    }
  }

  PlanningLP plp {std::move(planning), LpMatrixOptions {}};
  const auto res = plp.resolve(SolverOptions {});
  REQUIRE(res.has_value());
  const auto& li = plp.systems().front().front().linear_interface();
  REQUIRE(li.is_optimal());
  return li.get_obj_value();
}

}  // namespace

TEST_CASE("PowerModels case5_asym: lossless gtopt equals DC OPF")  // NOLINT
{
  // Lossless DC OPF: the same LP as PowerModels' DCPPowerModel (all
  // branches have r/x = 0.1, so PowerModels' susceptance
  // x/(r² + x²) is 1/x scaled by a common factor and the PTDFs match).
  const double obj = solve_case({.losses = "none"});
  CAPTURE(obj);
  CHECK(obj == doctest::Approx(kPmDc).epsilon(1e-6));
}

TEST_CASE("PowerModels case5_asym: piecewise sending -> DCPLL")  // NOLINT
{
  // The secant PWL loss lies above r·f², so the objective approaches the
  // DCPLL optimum from above and the gap shrinks as the segments double.
  // Measured (HiGHS 1.16): K = 1, 2, 4, 8, 16, 32 → relative gap 5.8e-3,
  // 1.1e-3, 4.4e-4, 1.1e-4, 1.3e-5, 5.6e-6.  Tolerances: 2e-4 at K = 8
  // (the e2e case pm_case5_asym_dcpll_sending), 1e-5 at K = 32.
  double prev_gap = 1e30;
  for (const int k : {1, 2, 4, 8, 16, 32}) {
    CAPTURE(k);
    const double obj = solve_case({.segments = k});
    const double gap = (obj - kPmDcpll) / kPmDcpll;
    CAPTURE(obj);
    CAPTURE(gap);
    CHECK(gap >= -1e-7);
    CHECK(gap <= prev_gap + 1e-9);
    prev_gap = gap;
    if (k == 8) {
      CHECK(std::abs(gap) <= 2e-4);
    }
    if (k == 32) {
      CHECK(std::abs(gap) <= 1e-5);
    }
  }
}

TEST_CASE("PowerModels case5_asym: full >= sending >= flow")  // NOLINT
{
  for (const bool dcpll : {true, false}) {
    CAPTURE(dcpll);
    const double flow =
        solve_case({.limit = "flow", .dcpll_allocation = dcpll});
    const double send =
        solve_case({.limit = "sending", .dcpll_allocation = dcpll});
    const double full =
        solve_case({.limit = "full", .dcpll_allocation = dcpll});
    CAPTURE(flow);
    CAPTURE(send);
    CAPTURE(full);
    CHECK(full >= send - 1e-7);
    CHECK(send >= flow - 1e-7);
    // Every lossy run costs more than the lossless DC optimum.
    CHECK(flow > kPmDc);
  }
}
