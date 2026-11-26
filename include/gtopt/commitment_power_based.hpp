/**
 * @file      commitment_power_based.hpp
 * @brief     Power-based unit-commitment rows (Morales-España et al.)
 * @date      Wed Nov 25 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `uc_formulation = "power_based"` on a chronological block stage: the
 * instantaneous power at every block boundary is a column, the block
 * generation column (average power) is the trapezoid of its two boundary
 * powers, the capacity / ramp rows bound the boundary powers, start-up /
 * shut-down trajectories add their power to the boundaries around the
 * event, and the unit's reserves are power-capacity and ramp-capability
 * reserves.  The commitment binaries stay on their own (coarser, uniform)
 * commitment grid; each boundary inherits the state of its period and
 * events happen only at period boundaries.  See docs/unit-commitment.md
 * §4.6 for the equations (PB1–PB6).
 */

#pragma once

#include <optional>
#include <string_view>
#include <vector>

#include <gtopt/basic_types.hpp>
#include <gtopt/linear_problem.hpp>

namespace gtopt
{

/// Inputs of one unit on one (scenario, stage): blocks b = 0..B−1 (block
/// b ends at boundary b+1) and commitment periods k = 0..K−1.
struct PowerBasedUnit
{
  // ── per block (size B) ──
  std::vector<ColIndex> gcol;  ///< generation (block-average power) column
  std::vector<double> dt;  ///< real duration Δ_b [h]
  std::vector<double> pmax;  ///< P̄_b [MW]
  std::vector<double> pmin;  ///< committed minimum P_b [MW]
  std::vector<double> ramp_up;  ///< RU_b [MW/h], used when has_ramp_up
  std::vector<double> ramp_down;  ///< RD_b [MW/h], used when has_ramp_down
  std::vector<double> startup_cap;  ///< SU_b [MW] (start-up capability)
  std::vector<double> shutdown_cap;  ///< SD_b [MW]
  std::vector<std::size_t> period;  ///< commitment period k(b)
  std::vector<std::vector<ColIndex>> reserve_up;  ///< r⁺ columns of block b
  std::vector<std::vector<ColIndex>> reserve_dn;  ///< r⁻ columns of block b
  std::vector<BlockUid> buid;  ///< block uids (row / column contexts)

  // ── per period (size K) ──
  std::vector<ColIndex> ucol;
  std::vector<ColIndex> vcol;
  std::vector<ColIndex> wcol;
  std::vector<double> period_start;  ///< T_k [h] from the stage start

  bool cyclic {false};  ///< boundary 0 ≡ boundary B, events wrap
  bool has_ramp_up {false};
  bool has_ramp_down {false};
  double initial_u {0.0};  ///< u before the stage (non-cyclic)
  std::optional<double> initial_power {};  ///< known P̂_0 [MW]

  std::vector<double> startup_trajectory {};  ///< [P^SU_1 .. P^SU_S]
  std::vector<double> shutdown_trajectory {};  ///< [P^SD_2 .. P^SD_{S+1}]
  double trajectory_step {1.0};  ///< sample spacing h [h]

  // ── LP metadata ──
  std::string_view class_name {};
  Uid uid {unknown_uid};
  ScenarioUid scenario_uid {};
  StageUid stage_uid {};
};

/// Columns / rows created by `add_power_based_rows`.
struct PowerBasedResult
{
  std::vector<ColIndex> power;  ///< P̂ at boundaries 1..B (size B)
  std::vector<ColIndex> power_above;  ///< p at boundaries 1..B (size B)
  std::optional<ColIndex> power_initial {};  ///< P̂_0 (non-cyclic)
  std::vector<RowIndex> trapezoid_rows;  ///< PB2 per block
  std::vector<RowIndex> capacity_rows;  ///< PB3 per block
  std::vector<std::optional<RowIndex>> ramp_up_rows;  ///< PB5 per block
  std::vector<std::optional<RowIndex>> ramp_down_rows;  ///< PB6 per block
};

/// Start-up trajectory power f^SU(δ) [MW] at δ > 0 hours BEFORE the
/// start instant (0 beyond the trajectory); @p pmin is f^SU(0).
[[nodiscard]] double startup_trajectory_power(const std::vector<double>& traj,
                                              double step,
                                              double pmin,
                                              double delta);

/// Shut-down trajectory power f^SD(δ) [MW] at δ > 0 hours AFTER the
/// shut-down instant (0 beyond the trajectory); @p pmin is f^SD(0).
[[nodiscard]] double shutdown_trajectory_power(const std::vector<double>& traj,
                                               double step,
                                               double pmin,
                                               double delta);

/// Add PB1–PB6 for one unit (docs/unit-commitment.md §4.6).  The caller
/// drops the energy-block C2 / C4 / C5 rows and rewrites the reserve
/// headroom rows (see `rewrite_reserve_rows_power_based`).
[[nodiscard]] PowerBasedResult add_power_based_rows(const PowerBasedUnit& unit,
                                                    LinearProblem& lp);

}  // namespace gtopt
