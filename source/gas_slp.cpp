/**
 * @file      gas_slp.cpp
 * @brief     Sequential LP for linearised (meshed) gas pipes
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <format>
#include <utility>

#include <gtopt/gas_pipe_lp.hpp>
#include <gtopt/gas_slp.hpp>
#include <gtopt/system_lp.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

std::expected<GasSlpStats, Error> gas_slp_refine(
    SystemLP& system, const SolverOptions& solver_options)
{
  GasSlpStats stats;
  const auto& go = system.options().gas_options();
  const int max_iter = go.slp_max_iterations();
  if (max_iter <= 0) {
    return stats;
  }
  // GasPipeLP is update_lp-resident, so its collection survives compress
  // mode; only a memory-limited full eviction drops it (then rebuilt).
  system.rebuild_collections_if_needed();
  auto& pipes = system.elements<GasPipeLP>();
  stats.linearized_pipes = static_cast<int>(std::ranges::count_if(
      pipes, [](const auto& p) { return p.is_linearized(); }));
  if (stats.linearized_pipes == 0) {
    return stats;
  }

  const double tol = go.slp_tol();
  // Same (scenario, stage) sweep as `SystemLP::update_lp`.
  const auto capture = [&]
  {
    const auto& li = system.linear_interface();
    double change = 0.0;
    for (auto& pipe : pipes) {
      for (const auto& scenario : system.scene().scenarios()) {
        for (const auto& stage : system.phase().stages()) {
          change =
              std::max(change, pipe.capture_slp_point(li, scenario, stage, go));
        }
      }
    }
    return change;
  };

  stats.converged = false;
  for (int it = 1; it <= max_iter; ++it) {
    stats.max_change = capture();
    if (stats.max_change < tol) {
      stats.converged = true;
      break;
    }
    std::ignore = system.update_lp();
    auto result = system.resolve(solver_options);
    if (!result) {
      return std::unexpected(std::move(result.error()));
    }
    stats.iterations = it;
    if (*result != 0) {
      return std::unexpected(Error {
          .code = ErrorCode::SolverError,
          .message = std::format(
              "gas SLP iteration {}: LP not optimal (status {})", it, *result),
      });
    }
  }
  if (!stats.converged) {
    // Change of the last re-solve (f₀ moves once more, harmlessly).
    stats.max_change = capture();
    stats.converged = stats.max_change < tol;
  }

  if (stats.converged) {
    SPDLOG_INFO(
        "Gas SLP: {} linearized pipe(s) converged after {} re-solve(s) "
        "(max relative flow change {:.2e})",
        stats.linearized_pipes,
        stats.iterations,
        stats.max_change);
  } else {
    SPDLOG_WARN(
        "Gas SLP: not converged after {} re-solve(s) (max relative flow "
        "change {:.2e} >= slp_tolerance {:.2e}); raise slp_iterations or "
        "lower slp_damping",
        stats.iterations,
        stats.max_change,
        tol);
  }
  return stats;
}

}  // namespace gtopt
