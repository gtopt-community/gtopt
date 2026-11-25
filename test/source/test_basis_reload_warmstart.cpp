// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_basis_reload_warmstart.cpp
 * @brief     A released + reloaded cell warm-starts from its saved basis
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Under `low_memory_mode = compress` SDDP keeps no solver instance between
 * solves: the cell is released after each solve and rebuilt from its flat
 * snapshot, and only the simplex basis (`Basis`, a few bytes per row and
 * column) carries the warm start.  These tests pin that this path really
 * warm-starts, on the cell itself (forward pass) and on a clone of it
 * (backward / aperture pass):
 *
 *  - a cell solved cold, released, reloaded and re-solved from its saved
 *    basis needs fewer simplex iterations than the cold solve and reaches
 *    the same objective;
 *  - a clone of the reloaded cell seeded with the same basis does too;
 *  - a full SDDP run under compress with `basis_cross_mode = full_cross`
 *    spends fewer simplex iterations than with `off`, with the same bounds.
 */

#include <optional>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>
#include <gtopt/solver_backend.hpp>
#include <gtopt/solver_options.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

/// Dual simplex, presolve off: the configuration the SDDP passes use for a
/// basis-seeded solve (`SDDPForwardPass`), applied to the cold solve too so
/// the two iteration counts are comparable.
[[nodiscard]] SolverOptions simplex_opts(bool warm)
{
  SolverOptions opts;
  opts.algorithm = LPAlgo::dual;
  opts.presolve = false;
  opts.advanced_basis = warm;
  opts.log_level = 0;
  return opts;
}

struct SolveOutcome
{
  bool ok {false};
  double obj {0.0};
  long iterations {0};
};

template<typename Solve>
[[nodiscard]] SolveOutcome measured(LinearInterface& li, Solve&& solve)
{
  const auto before = SolveEffortTotals::instance().snapshot();
  const bool ok = std::forward<Solve>(solve)();
  const auto delta = SolveEffortTotals::instance().snapshot() - before;
  return {
      .ok = ok && li.is_optimal(),
      .obj = ok ? li.get_obj_value_raw() : 0.0,
      .iterations = delta.iterations,
  };
}

[[nodiscard]] PlanningLP make_compress_lp()
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  planning.options.sddp_options.low_memory_mode = LowMemoryMode::compress;
  return PlanningLP(std::move(planning));
}

}  // namespace

TEST_CASE(  // NOLINT
    "Basis reload - released cell re-solves warm from its saved basis")
{
  auto plp = make_compress_lp();
  auto& sys = plp.system(first_scene_index(), first_phase_index());
  sys.set_low_memory(LowMemoryMode::compress, CompressionCodec::zstd);
  auto& li = sys.linear_interface();

  const auto cold = measured(
      li,
      [&]
      { return li.initial_solve(simplex_opts(/*warm=*/false)).has_value(); });
  REQUIRE(cold.ok);
  if (cold.iterations == 0) {
    MESSAGE("backend reports no iteration count; nothing to compare");
    return;
  }

  const auto basis = li.get_basis();
  if (!basis.has_value()) {
    MESSAGE("backend exposes no basis; warm start not applicable");
    return;
  }

  li.release_backend();
  REQUIRE(li.is_backend_released());

  // set_basis rebuilds the backend from the snapshot first, then installs
  // the basis on the rebuilt instance.
  REQUIRE(li.set_basis(*basis));
  CHECK_FALSE(li.is_backend_released());

  const auto warm = measured(
      li, [&] { return li.resolve(simplex_opts(/*warm=*/true)).has_value(); });
  REQUIRE(warm.ok);
  CAPTURE(cold.iterations);
  CAPTURE(warm.iterations);
  CHECK(warm.iterations < cold.iterations);
  CHECK(warm.obj == doctest::Approx(cold.obj).epsilon(1e-9));

  SUBCASE("a reload without the basis is a cold solve again")
  {
    li.release_backend();
    REQUIRE(li.is_backend_released());
    const auto again = measured(
        li,
        [&] { return li.resolve(simplex_opts(/*warm=*/false)).has_value(); });
    REQUIRE(again.ok);
    CHECK(again.iterations > warm.iterations);
    CHECK(again.obj == doctest::Approx(cold.obj).epsilon(1e-9));
  }

  SUBCASE("a clone of the reloaded cell warm-starts from the same basis")
  {
    // Backward / aperture route: the aperture pass clones the phase LP
    // (from the flat snapshot when it is decompressed, natively otherwise)
    // and seeds the clone with the forward basis.
    const auto make_clone = [&]
    {
      if (li.has_decompressed_snapshot()) {
        return li.clone_from_flat(LinearInterface::CloneKind::shallow);
      }
      return li.clone(LinearInterface::CloneKind::shallow);
    };

    auto cold_clone = make_clone();
    // A native clone may copy the source's basis (HiGHS does): drop it so
    // this really is a cold solve.
    cold_clone.clear_basis();
    const auto clone_cold = measured(
        cold_clone,
        [&]
        {
          return cold_clone.resolve(simplex_opts(/*warm=*/false)).has_value();
        });
    REQUIRE(clone_cold.ok);

    auto warm_clone = make_clone();
    REQUIRE(warm_clone.set_basis(*basis));
    const auto clone_warm = measured(
        warm_clone,
        [&]
        {
          return warm_clone.resolve(simplex_opts(/*warm=*/true)).has_value();
        });
    REQUIRE(clone_warm.ok);
    CAPTURE(clone_cold.iterations);
    CAPTURE(clone_warm.iterations);
    CHECK(clone_warm.iterations < clone_cold.iterations);
    CHECK(clone_warm.obj == doctest::Approx(clone_cold.obj).epsilon(1e-9));
    CHECK(clone_warm.obj == doctest::Approx(cold.obj).epsilon(1e-9));
  }
}

TEST_CASE(  // NOLINT
    "Basis reload - SDDP under compress: every cross mode saves iterations")
{
  const auto run = [](BasisCrossMode mode)
  {
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    planning.options.sddp_options.low_memory_mode = LowMemoryMode::compress;
    PlanningLP planning_lp(std::move(planning));

    SDDPOptions sddp_opts;
    sddp_opts.max_iterations = 6;
    sddp_opts.convergence_tol = 1e-9;
    sddp_opts.stationary_tol = 0.0;
    sddp_opts.cut_sharing = CutSharingMode::multicut;
    sddp_opts.apertures = std::vector<Uid> {Uid {1}, Uid {2}};
    sddp_opts.aperture_solve_mode = ApertureSolveMode::warm;
    sddp_opts.enable_api = false;
    sddp_opts.low_memory_mode = LowMemoryMode::compress;
    sddp_opts.basis_cross_mode = mode;
    sddp_opts.forward_solver_options = simplex_opts(/*warm=*/false);
    sddp_opts.backward_solver_options = simplex_opts(/*warm=*/false);

    const auto before = SolveEffortTotals::instance().snapshot();
    SDDPMethod sddp(planning_lp, sddp_opts);
    auto results = sddp.solve();
    const auto delta = SolveEffortTotals::instance().snapshot() - before;
    REQUIRE(results.has_value());
    REQUIRE_FALSE(results->empty());
    return std::tuple {
        results->back().upper_bound,
        results->back().lower_bound,
        delta.iterations,
    };
  };

  const auto [ub0, lb0, it0] = run(BasisCrossMode::off);
  if (it0 == 0) {
    MESSAGE("backend reports no iteration count; nothing to compare");
    return;
  }
  // Each crossing path re-installs its stored basis after the release:
  //   warm                 -> next iteration's forward re-solve
  //   forward_to_backward  -> backward target re-solve + first aperture
  //   backward_to_forward  -> forward re-solve from the aperture chain
  //   full_cross           -> all of them
  for (const auto mode : {BasisCrossMode::warm,
                          BasisCrossMode::forward_to_backward,
                          BasisCrossMode::backward_to_forward,
                          BasisCrossMode::full_cross})
  {
    CAPTURE(static_cast<int>(mode));
    const auto [ub1, lb1, it1] = run(mode);
    CAPTURE(it0);
    CAPTURE(it1);
    CHECK(it1 < it0);
    CHECK(ub1 == doctest::Approx(ub0).epsilon(1e-6));
    CHECK(lb1 == doctest::Approx(lb0).epsilon(1e-6));
  }
}
