/**
 * @file      risk_measure_lp.cpp
 * @brief     Mean-CVaR objective over the scenarios of a (scene, phase) LP
 * @date      2026-11-27
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * See include/gtopt/risk_measure.hpp for the formulation.
 */

#include <algorithm>
#include <cmath>
#include <format>
#include <numeric>
#include <ranges>
#include <stdexcept>

#include <gtopt/capacity_object_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/risk_measure_lp.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

bool is_first_stage_col_name(std::string_view variable_name)
{
  return variable_name == CapacityObjectBase::CapainstName
      || variable_name == CapacityObjectBase::CapacostName
      || variable_name == CapacityObjectBase::ExpmodName
      || variable_name == CapacityObjectBase::CapainstPrevName
      || variable_name == CapacityObjectBase::CapacostPrevName;
}

CvarLayout add_cvar_to_lp(LinearProblem& lp,
                          std::span<const CvarScenarioInput> scenarios,
                          double alpha,
                          double weight,
                          double var_scale,
                          SceneUid scene_uid,
                          PhaseUid phase_uid)
{
  if (!std::isfinite(alpha) || alpha < 0.0 || alpha >= 1.0) {
    throw std::invalid_argument(
        std::format("risk_measure cvar: cvar_alpha must be in [0, 1) "
                    "(got {})",
                    alpha));
  }
  if (!std::isfinite(weight) || weight < 0.0 || weight > 1.0) {
    throw std::invalid_argument(
        std::format("risk_measure cvar: cvar_weight must be in [0, 1] "
                    "(got {})",
                    weight));
  }
  if (!std::isfinite(var_scale) || var_scale <= 0.0) {
    var_scale = 1.0;
  }

  CvarLayout layout {
      .alpha = alpha,
      .weight = weight,
      .mass = 0.0,
  };
  for (const auto& s : scenarios) {
    if (s.probability > 0.0) {
      layout.mass += s.probability;
    }
  }
  if (layout.mass <= 0.0) {
    throw std::invalid_argument(
        std::format("risk_measure cvar: scene {} phase {} has no scenario "
                    "with positive probability_factor",
                    scene_uid.value(),
                    phase_uid.value()));
  }

  const double keep = 1.0 - weight;
  const double tail = weight / (1.0 - alpha);

  // η: free, cost λ·P.  Column and row scale = scale_objective keeps the
  // construct in the objective's LP units (η, u_s are money).
  layout.eta_col = lp.add_col({
      .lowb = -LinearProblem::DblMax,
      .uppb = LinearProblem::DblMax,
      .cost = weight * layout.mass,
      .scale = var_scale,
      .class_name = cvar_class_name,
      .variable_name = cvar_eta_name,
      .variable_uid = Uid {1},
      .context = PhaseContext {scene_uid, phase_uid, Uid {0}},
  });

  double removed_constant = 0.0;
  Index raw_skipped = 0;
  for (const auto& s : scenarios) {
    const double p = s.probability;
    if (!(p > 0.0)) {
      continue;  // zero mass: its columns already cost 0
    }
    CvarScenarioTerm term {
        .uid = s.uid,
        .probability = p,
        .constant = s.obj_constant / p,
    };

    SparseRow row;
    row.class_name = cvar_class_name;
    row.constraint_name = cvar_excess_name;
    row.variable_uid = s.uid.value();
    row.context = PhaseContext {scene_uid, phase_uid, s.uid.value()};
    row.scale = var_scale;

    for (const auto& [begin, end] : s.col_ranges) {
      for (Index j = begin; j < end; ++j) {
        const auto ci = ColIndex {j};
        auto& col = lp.col_at(ci);
        if (col.cost == 0.0 || is_first_stage_col_name(col.variable_name)) {
          if (col.cost != 0.0) {
            ++layout.shared_cols;
          }
          continue;
        }
        if (col.cost_scale_type == ConstraintScaleType::Raw) {
          // Face-value cost (not × p_s): cannot be attributed to a
          // scenario-conditional cost.  Left risk-neutral.
          ++raw_skipped;
          ++layout.shared_cols;
          continue;
        }
        const double d = col.cost / p;
        row[ci] = -d;
        term.coefs.emplace_back(ci, d);
        col.cost *= keep;
        ++layout.adjusted_cols;
      }
    }

    term.excess_col = lp.add_col({
        .lowb = 0.0,
        .uppb = LinearProblem::DblMax,
        .cost = tail * p,
        .scale = var_scale,
        .class_name = cvar_class_name,
        .variable_name = cvar_excess_name,
        .variable_uid = s.uid.value(),
        .context = PhaseContext {scene_uid, phase_uid, s.uid.value()},
    });
    row[term.excess_col] = 1.0;
    row[layout.eta_col] = 1.0;
    term.row = lp.add_row(std::move(row.greater_equal(term.constant)));

    removed_constant += weight * s.obj_constant;
    layout.scenarios.push_back(std::move(term));
  }

  // The scenario constants enter O_s through the rows; only (1 − λ) of
  // their expectation stays as a plain constant.
  if (removed_constant != 0.0) {
    lp.add_obj_constant(-removed_constant);
  }

  if (raw_skipped > 0) {
    spdlog::warn(
        "risk_measure cvar: scene {} phase {}: {} column(s) with a raw "
        "(face-value) objective cost left risk-neutral",
        scene_uid.value(),
        phase_uid.value(),
        raw_skipped);
  }
  spdlog::info(
      "risk_measure cvar: scene {} phase {}: alpha={} weight={} "
      "{} scenario(s), {} risk-adjusted column(s), {} shared column(s)",
      scene_uid.value(),
      phase_uid.value(),
      alpha,
      weight,
      layout.scenarios.size(),
      layout.adjusted_cols,
      layout.shared_cols);

  return layout;
}

namespace
{

void check_alpha_weight(double alpha, double weight)
{
  if (!std::isfinite(alpha) || alpha < 0.0 || alpha >= 1.0) {
    throw std::invalid_argument(std::format(
        "risk_measure: cvar_alpha must be in [0, 1) (got {})", alpha));
  }
  if (!std::isfinite(weight) || weight < 0.0 || weight > 1.0) {
    throw std::invalid_argument(std::format(
        "risk_measure: cvar_weight must be in [0, 1] (got {})", weight));
  }
}

}  // namespace

std::vector<double> tail_risk_weights(std::span<const double> probability,
                                      std::span<const double> index,
                                      double alpha,
                                      double weight,
                                      RiskMeasure measure,
                                      RiskTail tail)
{
  check_alpha_weight(alpha, weight);
  if (probability.size() != index.size()) {
    throw std::invalid_argument(
        "risk_measure: one risk_index per scenario is required");
  }
  if (measure == RiskMeasure::trim && alpha <= 0.0) {
    throw std::invalid_argument(
        "risk_measure trim: cvar_alpha must be > 0 (alpha = 0 trims every "
        "scenario)");
  }
  if (std::ranges::any_of(index, [](double v) { return !std::isfinite(v); })) {
    throw std::invalid_argument("risk_measure: non-finite risk_index");
  }
  const auto n = probability.size();
  std::vector<std::size_t> order(n);
  std::ranges::iota(order, std::size_t {0});
  std::ranges::stable_sort(order,
                           [&](std::size_t a, std::size_t b)
                           {
                             return tail == RiskTail::upper
                                 ? index[a] > index[b]
                                 : index[a] < index[b];
                           });
  double mass = 0.0;
  for (const double p : probability) {
    mass += std::max(p, 0.0);
  }
  double left = (1.0 - alpha) * mass;
  std::vector<double> in_tail(n, 0.0);
  for (const auto k : order) {
    const double take = std::min(std::max(probability[k], 0.0), left);
    in_tail[k] = take;
    left -= take;
  }
  std::vector<double> q(n, 0.0);
  for (std::size_t k = 0; k < n; ++k) {
    const double p = std::max(probability[k], 0.0);
    double extra = p;
    if (measure == RiskMeasure::trim) {
      extra = (p - in_tail[k]) / alpha;
    } else if (measure == RiskMeasure::cvar) {
      extra = in_tail[k] / (1.0 - alpha);
    }
    q[k] = ((1.0 - weight) * p) + (weight * extra);
  }
  return q;
}

CvarLayout apply_tail_weights_to_lp(
    LinearProblem& lp,
    std::span<const CvarScenarioInput> scenarios,
    std::span<const double> risk_probability,
    RiskMeasure measure,
    RiskTail tail,
    double alpha,
    double weight,
    SceneUid scene_uid,
    PhaseUid phase_uid)
{
  check_alpha_weight(alpha, weight);
  if (risk_probability.size() != scenarios.size()) {
    throw std::invalid_argument(
        "risk_measure: one risk-adjusted probability per scenario");
  }
  CvarLayout layout {
      .measure = measure,
      .rank_by = RiskRankBy::index,
      .tail = tail,
      .endogenous = false,
      .alpha = alpha,
      .weight = weight,
      .mass = 0.0,
  };
  double constant_delta = 0.0;
  for (const auto& [k, s] : std::views::enumerate(scenarios)) {
    const double p = s.probability;
    if (!(p > 0.0)) {
      continue;
    }
    layout.mass += p;
    const double q = risk_probability[static_cast<std::size_t>(k)];
    const double ratio = q / p;
    CvarScenarioTerm term {
        .uid = s.uid,
        .probability = p,
        .constant = s.obj_constant / p,
        .fixed_risk_probability = q,
    };
    for (const auto& [begin, end] : s.col_ranges) {
      for (Index j = begin; j < end; ++j) {
        const auto ci = ColIndex {j};
        auto& col = lp.col_at(ci);
        if (col.cost == 0.0 || is_first_stage_col_name(col.variable_name)
            || col.cost_scale_type == ConstraintScaleType::Raw)
        {
          if (col.cost != 0.0) {
            ++layout.shared_cols;
          }
          continue;
        }
        term.coefs.emplace_back(ci, col.cost / p);
        col.cost *= ratio;
        ++layout.adjusted_cols;
      }
    }
    constant_delta += (ratio - 1.0) * s.obj_constant;
    layout.scenarios.push_back(std::move(term));
  }
  if (constant_delta != 0.0) {
    lp.add_obj_constant(constant_delta);
  }
  spdlog::info(
      "risk_measure {} (rank_by=index, tail={}): scene {} phase {}: "
      "alpha={} weight={} {} scenario(s), {} re-weighted column(s)",
      enum_name(measure),
      enum_name(tail),
      scene_uid.value(),
      phase_uid.value(),
      alpha,
      weight,
      layout.scenarios.size(),
      layout.adjusted_cols);
  return layout;
}

}  // namespace gtopt
