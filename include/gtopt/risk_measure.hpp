/**
 * @file      risk_measure.hpp
 * @brief     Risk measure option (expected cost / mean-CVaR)
 * @date      2026-11-27
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `model_options.risk_measure = "cvar"` replaces the expected total cost
 * of a (scene, phase) LP by the mean-CVaR blend
 *
 *     F + (1 − λ) Σ_s p_s O_s + λ · ( P η + 1/(1 − α) Σ_s p_s u_s )
 *     u_s ≥ O_s − η,  u_s ≥ 0,  η free
 *
 * (Rockafellar–Uryasev 2000), with
 *   - F   the scenario-independent (first-stage) cost: the capacity
 *         annuities (`capacost`) and every column built outside the
 *         per-scenario loop;
 *   - O_s the discounted operating cost of scenario s, read off the
 *         objective coefficients the elements already wrote
 *         (`c_j = p_s · d_j` → `d_j = c_j / p_s`) plus the scenario's
 *         share of the objective constant;
 *   - P = Σ_s p_s the probability mass of the cell (1 for the single
 *         scene of a monolithic run).
 *
 * Because F is the same in every scenario, CVaR(F + O) = F + CVaR(O)
 * (translation equivariance), so the capacity columns keep their
 * coefficients.  The dual μ_s ≥ 0 of row s satisfies Σ_s μ_s = λ P and
 * μ_s ≤ λ p_s / (1 − α); the risk-adjusted probability of scenario s is
 * π_s = (1 − λ) p_s + μ_s (Σ_s π_s = P), and every per-scenario dual
 * gtopt reports (÷ p_s) carries the factor ζ_s = π_s / p_s.
 *
 * Default `expected` builds nothing (byte-identical LP).  Monolithic only:
 * the end-of-horizon CVaR couples the scenarios of a scene, which the
 * SDDP / cascade decomposition cannot represent (see
 * docs/formulation/mathematical-formulation.md, "Risk-averse objective").
 */

#pragma once

#include <array>
#include <cstdint>
#include <span>

#include <gtopt/enum_option.hpp>

namespace gtopt
{

/// Risk measure applied to the scenario costs of a (scene, phase) LP.
enum class RiskMeasure : std::uint8_t
{
  expected = 0,  ///< Expected cost Σ p_s C_s (default, risk-neutral)
  cvar = 1,  ///< (1 − λ)·E[C] + λ·CVaR_α[C]: tail weighted up
  trim = 2,  ///< (1 − λ)·E[C] + λ·E[C | not in tail]: tail excluded
};

/// How the tail of `risk_measure` is selected.
enum class RiskRankBy : std::uint8_t
{
  /// Endogenous: by the scenario's total cost at the optimum
  /// (Rockafellar–Uryasev rows).  Upper tail and `cvar` only.
  cost = 0,
  /// Exogenous: by `Scenario.risk_index` (hydrology index, expected
  /// unserved energy, ...).  Fixed risk-adjusted weights, no extra rows.
  index = 1,
};

/// Side of the ranking the tail is taken from.
enum class RiskTail : std::uint8_t
{
  upper = 0,  ///< Highest cost / highest `risk_index`
  lower = 1,  ///< Lowest `risk_index` (index ranking only)
};

inline constexpr auto risk_rank_by_entries =
    std::to_array<EnumEntry<RiskRankBy>>({
        {.name = "cost", .value = RiskRankBy::cost},
        {.name = "index", .value = RiskRankBy::index},
    });

[[nodiscard]] constexpr auto enum_entries(RiskRankBy /*tag*/) noexcept
{
  return std::span {risk_rank_by_entries};
}

inline constexpr auto risk_tail_entries = std::to_array<EnumEntry<RiskTail>>({
    {.name = "upper", .value = RiskTail::upper},
    {.name = "lower", .value = RiskTail::lower},
});

[[nodiscard]] constexpr auto enum_entries(RiskTail /*tag*/) noexcept
{
  return std::span {risk_tail_entries};
}

inline constexpr auto risk_measure_entries =
    std::to_array<EnumEntry<RiskMeasure>>({
        {.name = "expected", .value = RiskMeasure::expected},
        {.name = "cvar", .value = RiskMeasure::cvar},
        {.name = "trim", .value = RiskMeasure::trim},
    });

[[nodiscard]] constexpr auto enum_entries(RiskMeasure /*tag*/) noexcept
{
  return std::span {risk_measure_entries};
}

/// Default tail level α of `model_options.cvar_alpha`: CVaR averages the
/// worst 1 − α = 5 % of the cost distribution.
inline constexpr double default_cvar_alpha = 0.95;

/// Default weight λ of `model_options.cvar_weight`.  0.5 rather than 1:
/// with λ = 1 and few scenarios, the scenarios outside the tail get zero
/// risk-adjusted probability, so their dispatch is no longer priced (any
/// feasible dispatch is optimal and their reported duals are 0).
inline constexpr double default_cvar_weight = 0.5;

}  // namespace gtopt
