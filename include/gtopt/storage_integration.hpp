/**
 * @file      storage_integration.hpp
 * @brief     Banded (local Lagrange) integration weights for storage
 *            balances on non-uniform node grids
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The default storage balance is the block recursion
 * `e_k = e_{k−1} + w_k·f_k` (block weight `w_k` = duration).  On a
 * Chebyshev node grid whose durations are Clenshaw–Curtis weights this is
 * the Clenshaw–Curtis rule (`storage_integration = cc` /
 * `clenshaw_curtis`, aliases of `block`): every node carries its own
 * positive CC weight (node 0 carries `w_0 + w_N`, the closing node of the
 * periodic grid), `e_k` is the level AFTER node k's cell, one previous
 * level plus node k's own flows per row (as sparse as a block balance,
 * monotone, no overshoot), and over the closed period
 * `Σ_k (e_k − e_{k−1}) = Σ_k w_k f_k` is exactly the CC quadrature that
 * also integrates energy and cost.  The intermediate levels are only
 * first order (the exact integral of the interpolant over one interval
 * would make every row dense).
 *
 * `storage_integration = trapezoid | lg4 | lg6` replaces it with a banded
 * rule (2 / 4 / 6 coefficients per row).  The level `e_k` is the state AT the
 * node `t_k` (the last one at the period end).  The increment over `[p_{k−1},
 * p_k]` integrates the linear (trapezoid: the interval's two end nodes), cubic
 * or quintic Lagrange interpolant of the flows on the 2 / 4 / 6 nodes nearest
 * to the interval.  The stencil is shifted at the ends to keep its size.  The
 * weights are then corrected so that `Σ_k D_kj = w_j` (the period total is
 * exactly the block-weight (CC) quadrature) AND `Σ_j D_kj = Δ_k` (a constant
 * flow integrates exactly over every interval): a balanced minimum-norm
 * additive correction for the signed rules, an alternating row / column
 * scaling for the `_pos` rules.
 *
 * Lagrange weights on non-uniform grids can be negative (≈ 8 % of the
 * mass for Chebyshev–Lobatto nodes).  The `_pos` variants clip them to
 * zero before the (nonnegative) balancing.  Signed `lg4` / `lg6` are not
 * recommended for an LP: negative weights shift energy between intervals.
 */

#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <span>
#include <string_view>
#include <vector>

namespace gtopt
{

/// One nonzero `(node index, weight)` of a banded integration row.
struct IntegrationTerm
{
  std::size_t node {};
  double weight {};
};

/// Rows of a banded integration operator: row k lists the flow-node
/// weights of the level increment `e_k − e_{k−1}`.
using IntegrationRows = std::vector<std::vector<IntegrationTerm>>;

/// Parsed `model_options.storage_integration` value.
struct StorageIntegration
{
  int points {0};  ///< 0 = block recursion; 2 = trapezoid; 4 / 6 = Lagrange
  bool clip_negative {false};  ///< `_pos`: clip negative weights

  [[nodiscard]] constexpr bool banded() const noexcept { return points > 0; }

  /// Parse "block" (= "cc" = "clenshaw_curtis") | "trapezoid" | "lg4" |
  /// "lg6" | "lg4_pos" | "lg6_pos"; any other value yields the block
  /// recursion (the value is validated in `validate_planning`).
  [[nodiscard]] static constexpr StorageIntegration parse(
      std::string_view name) noexcept
  {
    if (name == "trapezoid") {
      return {.points = 2};
    }
    if (name == "lg4") {
      return {.points = 4};
    }
    if (name == "lg6") {
      return {.points = 6};
    }
    if (name == "lg4_pos") {
      return {.points = 4, .clip_negative = true};
    }
    if (name == "lg6_pos") {
      return {.points = 6, .clip_negative = true};
    }
    return {};
  }

  /// True for the block recursion's names: "block" and its Clenshaw–
  /// Curtis aliases "cc" / "clenshaw_curtis" (on a CC-weighted node grid).
  [[nodiscard]] static constexpr bool is_block_name(
      std::string_view name) noexcept
  {
    return name == "block" || name == "cc" || name == "clenshaw_curtis";
  }

  /// True for the recognised option names (including "block" / "cc").
  [[nodiscard]] static constexpr bool is_valid(std::string_view name) noexcept
  {
    return is_block_name(name) || parse(name).banded();
  }
};

namespace detail
{

/// Gauss–Legendre nodes / weights on [−1, 1] (Newton on P_m).
struct GaussRule
{
  std::vector<double> x;
  std::vector<double> w;
};

[[nodiscard]] inline GaussRule gauss_legendre(std::size_t m)
{
  GaussRule g {
      .x = std::vector<double>(m),
      .w = std::vector<double>(m),
  };
  const double pi = std::acos(-1.0);
  const auto md = static_cast<double>(m);
  for (std::size_t i = 0; i < m; ++i) {
    double z = std::cos(pi * (static_cast<double>(i) + 0.75) / (md + 0.5));
    double dp = 1.0;
    for (int it = 0; it < 100; ++it) {
      double p0 = 1.0;
      double p1 = z;
      for (std::size_t k = 2; k <= m; ++k) {
        const auto kd = static_cast<double>(k);
        const double p2 =
            (((2.0 * kd) - 1.0) * z * p1 - ((kd - 1.0) * p0)) / kd;
        p0 = p1;
        p1 = p2;
      }
      if (m == 1) {
        p1 = z;
        p0 = 1.0;
      }
      dp = md * ((z * p1) - p0) / ((z * z) - 1.0);
      const double dz = p1 / dp;
      z -= dz;
      if (std::abs(dz) < 1e-16) {
        break;
      }
    }
    g.x[i] = z;
    g.w[i] = 2.0 / ((1.0 - (z * z)) * dp * dp);
  }
  return g;
}

/// ∫_a^b of the Lagrange basis polynomials on `xs` (exact: Gauss rule
/// with ⌈|xs|/2⌉ + 1 points).  Writes one weight per node.
inline void lagrange_integrals(std::span<const double> xs,
                               double a,
                               double b,
                               std::span<double> out)
{
  const auto g = gauss_legendre((xs.size() / 2) + 1);
  const double half = 0.5 * (b - a);
  const double mid = 0.5 * (a + b);
  std::ranges::fill(out, 0.0);
  for (std::size_t q = 0; q < g.x.size(); ++q) {
    const double t = mid + (half * g.x[q]);
    for (std::size_t i = 0; i < xs.size(); ++i) {
      double li = 1.0;
      for (std::size_t m = 0; m < xs.size(); ++m) {
        if (m != i) {
          li *= (t - xs[m]) / (xs[i] - xs[m]);
        }
      }
      out[i] += half * g.w[q] * li;
    }
  }
}

/// Balanced nonnegative correction (`_pos` rules): alternating row /
/// column scaling (RAS / Sinkhorn) of the clipped weights towards the
/// row targets (interval lengths) and the column targets (block
/// weights).  Keeps every weight ≥ 0 and the sparsity pattern; the last
/// sweep is a column scaling, so column sums are exact.
inline void balance_scaling(std::vector<std::vector<IntegrationTerm>>& rows,
                            std::span<const double> row_target,
                            std::span<const double> col_target)
{
  const std::size_t n = col_target.size();
  std::vector<double> colsum(n);
  const auto scale_cols = [&]
  {
    std::ranges::fill(colsum, 0.0);
    for (const auto& row : rows) {
      for (const auto& t : row) {
        colsum[t.node] += t.weight;
      }
    }
    for (auto& row : rows) {
      for (auto& t : row) {
        t.weight *=
            colsum[t.node] > 0.0 ? col_target[t.node] / colsum[t.node] : 0.0;
      }
    }
  };
  for (int it = 0; it < 2000; ++it) {
    scale_cols();
    double err = 0.0;
    double total = 0.0;
    for (std::size_t k = 0; k < rows.size(); ++k) {
      double rs = 0.0;
      for (const auto& t : rows[k]) {
        rs += t.weight;
      }
      err += std::abs(rs - row_target[k]);
      total += row_target[k];
      if (rs > 0.0 && row_target[k] > 0.0) {
        for (auto& t : rows[k]) {
          t.weight *= row_target[k] / rs;
        }
      }
    }
    if (err <= 1e-13 * std::max(1.0, total)) {
      break;
    }
  }
  scale_cols();
}

/// Balanced minimum-norm additive correction (signed rules): the
/// smallest change δ on the sparsity pattern (min ‖δ‖₂) giving row sums
/// = interval lengths and column sums = block weights.  The minimiser is
/// δ_kj = α_k + β_j, with (α, β) solving the (singular, consistent)
/// normal equations by conjugate gradients.
inline void balance_additive(std::vector<std::vector<IntegrationTerm>>& rows,
                             std::span<const double> row_target,
                             std::span<const double> col_target)
{
  const std::size_t m = rows.size();
  const std::size_t n = col_target.size();
  std::vector<double> rhs(m + n, 0.0);
  for (std::size_t k = 0; k < m; ++k) {
    double rs = 0.0;
    for (const auto& t : rows[k]) {
      rs += t.weight;
    }
    rhs[k] = rows[k].empty() ? 0.0 : row_target[k] - rs;
  }
  {
    std::vector<double> colsum(n, 0.0);
    for (const auto& row : rows) {
      for (const auto& t : row) {
        colsum[t.node] += t.weight;
      }
    }
    for (std::size_t j = 0; j < n; ++j) {
      rhs[m + j] = col_target[j] - colsum[j];
    }
  }
  const auto apply = [&](const std::vector<double>& x, std::vector<double>& y)
  {
    std::ranges::fill(y, 0.0);
    for (std::size_t k = 0; k < m; ++k) {
      for (const auto& t : rows[k]) {
        const double d = x[k] + x[m + t.node];
        y[k] += d;
        y[m + t.node] += d;
      }
    }
  };
  std::vector<double> x(m + n, 0.0);
  std::vector<double> r = rhs;
  std::vector<double> p = r;
  std::vector<double> ap(m + n);
  const auto dot =
      [](const std::vector<double>& a, const std::vector<double>& b)
  {
    double s = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
      s += a[i] * b[i];
    }
    return s;
  };
  double rr = dot(r, r);
  const double tol = 1e-28 * std::max(1.0, dot(rhs, rhs));
  for (std::size_t it = 0; it < 4 * (m + n) && rr > tol; ++it) {
    apply(p, ap);
    const double pap = dot(p, ap);
    if (!(pap > 0.0)) {
      break;
    }
    const double alpha = rr / pap;
    for (std::size_t i = 0; i < x.size(); ++i) {
      x[i] += alpha * p[i];
      r[i] -= alpha * ap[i];
    }
    const double rr_new = dot(r, r);
    for (std::size_t i = 0; i < p.size(); ++i) {
      p[i] = r[i] + (rr_new / rr) * p[i];
    }
    rr = rr_new;
  }
  for (std::size_t k = 0; k < m; ++k) {
    for (auto& t : rows[k]) {
      t.weight += x[k] + x[m + t.node];
    }
  }
}

}  // namespace detail

/// Banded integration rows for nodes `times` (strictly increasing, inside
/// `[start, end]`) with block weights `weights` (target column sums).
///
/// Row k (k = 0..n−1) is the increment over `[p_{k−1}, p_k]`, with
/// `p_{−1} = start`, `p_k = t_k` for k < n−1 and `p_{n−1} = end`.  So the
/// level after row k is the state at node t_k, and after the last row the
/// state at the period end.  Zero-length intervals (Chebyshev–Lobatto
/// first node) give an empty row.  The raw Lagrange weights are scaled by
/// `Σ w / (end − start)` (the weights may be in weighted-duration units)
/// before the column correction (`match_weights = false` returns the raw
/// Lagrange rule; with `points = n` that is the exact cumulative integral
/// of the global node interpolant, the dense `S = V J V⁻¹`).
[[nodiscard]] inline IntegrationRows local_integration_rows(
    std::span<const double> times,
    std::span<const double> weights,
    double start,
    double end,
    StorageIntegration mode,
    bool match_weights = true)
{
  const std::size_t n = times.size();
  IntegrationRows rows(n);
  if (n == 0 || !mode.banded()) {
    return rows;
  }
  const std::size_t p = std::min<std::size_t>(mode.points, n);
  double wsum = 0.0;
  for (const double w : weights) {
    wsum += w;
  }
  const double span_len = end - start;
  const double scale = span_len > 0.0 ? wsum / span_len : 1.0;

  std::vector<double> xs(p);
  std::vector<double> ws(p);
  for (std::size_t k = 0; k < n; ++k) {
    const double a = k == 0 ? start : times[k - 1];
    const double b = k + 1 == n ? end : times[k];
    if (!(b > a)) {
      continue;
    }
    // left node of the interval: k−1 (none for the first interval)
    const long left = static_cast<long>(k) - 1;
    const long first = std::clamp<long>(
        left - (static_cast<long>(p) / 2 - 1), 0, static_cast<long>(n - p));
    const auto s = static_cast<std::size_t>(first);
    for (std::size_t i = 0; i < p; ++i) {
      xs[i] = times[s + i];
    }
    detail::lagrange_integrals(xs, a, b, ws);
    rows[k].reserve(p);
    for (std::size_t i = 0; i < p; ++i) {
      double w = ws[i] * scale;
      if (mode.clip_negative && w < 0.0) {
        w = 0.0;
      }
      rows[k].push_back({.node = s + i, .weight = w});
    }
  }

  if (!match_weights) {
    return rows;  // raw Lagrange rule (reference / tests)
  }
  // Row targets: the interval lengths (weights' units) — a constant flow
  // integrates exactly; column targets: the block weights w_j — the
  // period total stays the block-weight (CC) quadrature.  Σ r = Σ w.
  std::vector<double> row_target(n, 0.0);
  for (std::size_t k = 0; k < n; ++k) {
    const double a = k == 0 ? start : times[k - 1];
    const double b = k + 1 == n ? end : times[k];
    row_target[k] = b > a ? (b - a) * scale : 0.0;
  }
  // a column with no nonzero weight (degenerate grid): fall back to the
  // block rule for it
  {
    std::vector<bool> has(n, false);
    for (const auto& row : rows) {
      for (const auto& term : row) {
        if (term.weight != 0.0) {
          has[term.node] = true;
        }
      }
    }
    for (std::size_t j = 0; j < n; ++j) {
      if (!has[j] && weights[j] != 0.0) {
        rows[j].push_back({.node = j, .weight = weights[j]});
      }
    }
  }
  if (mode.clip_negative) {
    // nonnegative pattern: drop the clipped zeros, RAS scaling, then a
    // minimum-norm additive polish (RAS converges slowly on fine grids);
    // the polish is kept only if every weight stays ≥ 0
    for (auto& row : rows) {
      std::erase_if(row,
                    [](const IntegrationTerm& t) { return t.weight <= 0.0; });
    }
    detail::balance_scaling(rows, row_target, weights);
    auto polished = rows;
    detail::balance_additive(polished, row_target, weights);
    const bool nonneg = std::ranges::all_of(
        polished,
        [](const auto& row)
        {
          return std::ranges::all_of(
              row, [](const IntegrationTerm& t) { return t.weight >= 0.0; });
        });
    if (nonneg) {
      rows = std::move(polished);
    }
  } else {
    detail::balance_additive(rows, row_target, weights);
  }
  return rows;
}

}  // namespace gtopt
