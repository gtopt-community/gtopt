/**
 * @file      gas_slp.hpp
 * @brief     Sequential LP for linearised (meshed) gas pipes
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * ``weymouth_mode: linearized`` pipes carry one equality per block,
 * linearised at a reference flow f₀.  After the first solve,
 * ``gas_slp_refine`` repeats
 *
 *   1. f₀ ← f₀ + ω (f − f₀) on every linearised pipe
 *      (``GasPipeLP::capture_slp_point``, solution read only);
 *   2. ``SystemLP::update_lp()`` — the standard re-linearisation
 *      dispatch shared with ReservoirSeepageLP / FlowRight /
 *      ReservoirDischargeLimit, which re-issues the rows' coefficients
 *      (recorded by the LP replay buffer for low-memory modes);
 *   3. re-solve,
 *
 * up to ``gas_options.slp_iterations`` times, stopping when the largest
 * relative flow change falls below ``slp_tolerance``.  At the fixed
 * point f = f₀ the secant (or tangent) row is the exact Weymouth
 * equation π_a − e^s π_b = K_e f|f|.
 *
 * Monolithic only: ``PlanningLP::resolve_scene_phases`` calls it after
 * each (scene, phase) solve.  SDDP re-issues the rows at the stored f₀
 * in every forward pass (``update_lp``) but does not move f₀.
 */

#pragma once

#include <expected>

#include <gtopt/error.hpp>
#include <gtopt/solver_options.hpp>

namespace gtopt
{

class SystemLP;

/// Outcome of one ``gas_slp_refine`` call.
struct GasSlpStats
{
  int linearized_pipes {0};  ///< Linearised pipes in the system
  int iterations {0};  ///< Re-solves performed
  double max_change {0.0};  ///< Last max relative flow change
  bool converged {true};  ///< max_change < slp_tolerance
};

/// Run the gas SLP on an already-solved system (no-op without
/// linearised pipes or with ``slp_iterations`` ≤ 0).  Returns the
/// re-solve's error when a re-solve fails.
[[nodiscard]] std::expected<GasSlpStats, Error> gas_slp_refine(
    SystemLP& system, const SolverOptions& solver_options = {});

}  // namespace gtopt
