/**
 * @file      risk_measure_lp.hpp
 * @brief     LP construct of the mean-CVaR objective (risk_measure = cvar)
 * @date      2026-11-27
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Formulation and dual reading: see include/gtopt/risk_measure.hpp.
 */

#pragma once

#include <cstddef>
#include <format>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <gtopt/basic_types.hpp>
#include <gtopt/lp_context.hpp>
#include <gtopt/risk_measure.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>

namespace gtopt
{

class LinearProblem;

/// Column / row name tags of the CVaR construct.
inline constexpr std::string_view cvar_class_name {"RiskMeasure"};
inline constexpr std::string_view cvar_eta_name {"cvar_eta"};
inline constexpr std::string_view cvar_excess_name {"cvar_excess"};

/// The columns of one scenario, as the half-open index ranges the
/// per-(scenario, stage) build loop produced, plus the objective constant
/// those calls added (already weighted by p_s).
struct CvarScenarioInput
{
  ScenarioUid uid {};
  double probability {0.0};  ///< p_s (global probability_factor)
  std::vector<std::pair<Index, Index>> col_ranges {};  ///< [begin, end)
  double obj_constant {0.0};  ///< Σ p_s-weighted constants of scenario s
};

/// One scenario of a built CVaR construct.
struct CvarScenarioTerm
{
  ScenarioUid uid {};
  double probability {0.0};  ///< p_s
  ColIndex excess_col {};  ///< u_s
  RowIndex row {};  ///< u_s + η − Σ d_j x_j ≥ k_s
  double constant {0.0};  ///< k_s (scenario constant ÷ p_s)
  /// Exogenous tail (`risk_rank_by = index`): the fixed risk-adjusted
  /// probability q_s (no row; `row`, `excess_col` unused).
  double fixed_risk_probability {0.0};
  /// Physical d_j = c_j / p_s of every risk-adjusted column (for the
  /// post-solve evaluation of O_s).
  std::vector<std::pair<ColIndex, double>> coefs {};
};

/// Layout of the CVaR construct of one (scene, phase) LP.
struct CvarLayout
{
  RiskMeasure measure {RiskMeasure::cvar};
  RiskRankBy rank_by {RiskRankBy::cost};
  RiskTail tail {RiskTail::upper};
  /// True for the Rockafellar–Uryasev rows (cost ranking); false for the
  /// fixed weights of an exogenous index ranking (no η, no rows).
  bool endogenous {true};
  double alpha {default_cvar_alpha};
  double weight {default_cvar_weight};
  double mass {1.0};  ///< P = Σ_s p_s over the cell's scenarios
  ColIndex eta_col {};
  std::vector<CvarScenarioTerm> scenarios {};
  Index adjusted_cols {0};  ///< Columns whose cost was scaled by 1 − λ
  Index shared_cols {0};  ///< Costed columns left unchanged (first stage)
};

/// True for the capacity (first-stage) columns that
/// `CapacityObjectBase` creates inside the first scenario's build call:
/// their cost is the same in every scenario, so CVaR leaves them alone.
[[nodiscard]] bool is_first_stage_col_name(std::string_view variable_name);

/// Add the mean-CVaR construct to @p lp (see the file header).
///
/// Mutates the objective coefficient of every costed column in the
/// scenario ranges (× (1 − λ)), the objective constant (− λ Σ const_s),
/// and appends η, one u_s per scenario with p_s > 0 and one row per such
/// scenario.  @p var_scale is used as the column and row scale of the
/// construct (pass `scale_objective` so η, u_s are in the objective's
/// LP units).
///
/// @throws std::invalid_argument when α ∉ [0, 1), λ ∉ [0, 1] or no
///         scenario carries positive probability.
[[nodiscard]] CvarLayout add_cvar_to_lp(
    LinearProblem& lp,
    std::span<const CvarScenarioInput> scenarios,
    double alpha,
    double weight,
    double var_scale,
    SceneUid scene_uid,
    PhaseUid phase_uid);

/// Fixed risk-adjusted probabilities of an exogenous tail
/// (`risk_rank_by = index`).  The scenarios are ranked by @p index
/// (descending for `upper`, ascending for `lower`; ties keep input
/// order); the tail is the first (1 − α)·P of probability mass, the
/// boundary scenario split.  With t_s the tail mass of s:
///   cvar: q_s = (1 − λ) p_s + λ t_s / (1 − α)
///   trim: q_s = (1 − λ) p_s + λ (p_s − t_s) / α
/// Σ q_s = Σ p_s.  @throws std::invalid_argument on bad α / λ, size
/// mismatch, `trim` with α = 0 or a non-finite index.
[[nodiscard]] std::vector<double> tail_risk_weights(
    std::span<const double> probability,
    std::span<const double> index,
    double alpha,
    double weight,
    RiskMeasure measure,
    RiskTail tail);

/// Re-weight the scenario costs of @p lp with fixed risk-adjusted
/// probabilities @p risk_probability (from `tail_risk_weights`): every
/// costed, non-first-stage column of scenario s and its share of the
/// objective constant are multiplied by q_s / p_s.  No row or column is
/// added; the LP stays the size of the expected-cost LP.
[[nodiscard]] CvarLayout apply_tail_weights_to_lp(
    LinearProblem& lp,
    std::span<const CvarScenarioInput> scenarios,
    std::span<const double> risk_probability,
    RiskMeasure measure,
    RiskTail tail,
    double alpha,
    double weight,
    SceneUid scene_uid,
    PhaseUid phase_uid);

/// Post-solve values of one scenario.
struct CvarScenarioResult
{
  ScenarioUid uid {};
  double probability {0.0};  ///< p_s
  double cost {0.0};  ///< O_s (physical, discounted, conditional on s)
  double excess {0.0};  ///< u_s
  double dual {0.0};  ///< μ_s
  double risk_probability {0.0};  ///< π_s = (1 − λ) p_s + μ_s
  double risk_factor {1.0};  ///< ζ_s = π_s / p_s
};

/// Post-solve values of a cell's construct.
struct CvarSolution
{
  double eta {0.0};  ///< η (the value-at-risk level at the optimum)
  std::vector<CvarScenarioResult> scenarios {};
};

/// Evaluate the construct on a solved LP.  @p col_sol and @p row_dual are
/// indexable by `Index` (a `std::span<const double>`, a `ScaledView`, …):
/// physical primal and physical (cost-factor-folded) duals.  An empty
/// @p row_dual (duals unavailable) leaves μ_s = 0, so π_s = (1 − λ) p_s.
template<typename ColSol, typename RowDual>
[[nodiscard]] CvarSolution evaluate_cvar(const CvarLayout& layout,
                                         const ColSol& col_sol,
                                         const RowDual& row_dual)
{
  const auto at = [](const auto& v, Index i) -> double
  {
    const auto k = static_cast<std::size_t>(i);
    return k < v.size() ? static_cast<double>(v[k]) : 0.0;
  };
  CvarSolution out {
      .eta = layout.endogenous ? at(col_sol, layout.eta_col.value_of()) : 0.0,
  };
  out.scenarios.reserve(layout.scenarios.size());
  for (const auto& t : layout.scenarios) {
    double cost = t.constant;
    for (const auto& [c, d] : t.coefs) {
      cost += d * at(col_sol, c.value_of());
    }
    const double base = (1.0 - layout.weight) * t.probability;
    const double mu = layout.endogenous ? at(row_dual, t.row.value_of())
                                        : t.fixed_risk_probability - base;
    const double pi = base + mu;
    out.scenarios.push_back({
        .uid = t.uid,
        .probability = t.probability,
        .cost = cost,
        .excess =
            layout.endogenous ? at(col_sol, t.excess_col.value_of()) : 0.0,
        .dual = mu,
        .risk_probability = pi,
        .risk_factor = t.probability > 0.0 ? pi / t.probability : 0.0,
    });
  }
  return out;
}

/// Header of `risk_measure.csv` (trailing newline included).
[[nodiscard]] constexpr std::string_view risk_measure_csv_header() noexcept
{
  return "scene,phase,scenario,probability,cost,eta,excess,dual,"
         "risk_probability,risk_factor,alpha,weight,measure,rank_by,tail\n";
}

/// One `risk_measure.csv` line.
[[nodiscard]] inline std::string risk_measure_csv_line(
    int scene_uid,
    int phase_uid,
    double eta,
    const CvarLayout& layout,
    const CvarScenarioResult& r)
{
  return std::format(
      "{},{},{},{},{},{},{},{},"
      "{},{},{},{},{},{},{}\n",
      scene_uid,
      phase_uid,
      r.uid.value(),
      r.probability,
      r.cost,
      eta,
      r.excess,
      r.dual,
      r.risk_probability,
      r.risk_factor,
      layout.alpha,
      layout.weight,
      enum_name(layout.measure),
      enum_name(layout.rank_by),
      enum_name(layout.tail));
}

}  // namespace gtopt
