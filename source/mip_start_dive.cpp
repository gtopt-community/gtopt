/**
 * @file      mip_start_dive.cpp
 * @brief     Relaxation-guided propagation dive + verify/repair MIP start
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <format>
#include <limits>
#include <numeric>
#include <string>
#include <utility>

#include <gtopt/domain_rules.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/mip_start.hpp>
#include <gtopt/mip_start_dive.hpp>
#include <gtopt/solver_options.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

constexpr double kInf = std::numeric_limits<double>::infinity();
/// Feasibility tolerance of the propagation (relative to max(1, |rhs|)).
constexpr double kFeasTol = 1e-6;
/// Integrality tolerance when rounding a propagated integer bound.
constexpr double kIntTol = 1e-6;
/// Minimum relative tightening accepted on a CONTINUOUS bound: smaller steps
/// are dropped so long chains of tiny tightenings cannot stall propagation.
constexpr double kContStep = 1e-3;

/// Row-major copy of a CSC flat LP (only what propagation needs).
struct CsrMatrix
{
  std::vector<std::size_t> beg {};
  std::vector<int> ind {};
  std::vector<double> val {};

  explicit CsrMatrix(const FlatLinearProblem& flat)
  {
    const auto nrows = static_cast<std::size_t>(flat.nrows);
    const auto ncols = static_cast<std::size_t>(flat.ncols);
    beg.assign(nrows + 1, 0);
    const auto nnz = static_cast<std::size_t>(flat.matbeg[ncols]);
    for (std::size_t k = 0; k < nnz; ++k) {
      ++beg[static_cast<std::size_t>(flat.matind[k]) + 1];
    }
    for (std::size_t i = 0; i < nrows; ++i) {
      beg[i + 1] += beg[i];
    }
    ind.resize(nnz);
    val.resize(nnz);
    std::vector<std::size_t> pos(beg.begin(), beg.end() - 1);
    for (std::size_t j = 0; j < ncols; ++j) {
      const auto kb = static_cast<std::size_t>(flat.matbeg[j]);
      const auto ke = static_cast<std::size_t>(flat.matbeg[j + 1]);
      for (std::size_t k = kb; k < ke; ++k) {
        const auto i = static_cast<std::size_t>(flat.matind[k]);
        ind[pos[i]] = static_cast<int>(j);
        val[pos[i]] = flat.matval[k];
        ++pos[i];
      }
    }
  }

  [[nodiscard]] std::size_t length(std::size_t i) const noexcept
  {
    return beg[i + 1] - beg[i];
  }
};

/// Activity-based bound propagation with an undo trail.
class Propagator
{
public:
  Propagator(const DiveInput& in, const CsrMatrix& csr)
      : m_flat_(in.flat)
      , m_csr_(&csr)
      , m_nrows_(static_cast<std::size_t>(in.flat->nrows))
      , m_flat_ncols_(static_cast<std::size_t>(in.flat->ncols))
      , m_work_limit_(in.work_limit)
  {
    // Live LP may carry columns appended after flatten (future-cost α, …):
    // they have no flat-matrix entries and simply keep their bounds.
    const auto ncols = in.col_low.size();
    const auto norm = [&](double v)
    {
      if (v >= in.infinity) {
        return kInf;
      }
      if (v <= -in.infinity) {
        return -kInf;
      }
      return v;
    };
    m_lb_.resize(ncols);
    m_ub_.resize(ncols);
    for (std::size_t j = 0; j < ncols; ++j) {
      m_lb_[j] = norm(in.col_low[j]);
      m_ub_[j] = norm(in.col_upp[j]);
    }
    m_rlo_.resize(m_nrows_);
    m_rup_.resize(m_nrows_);
    for (std::size_t i = 0; i < m_nrows_; ++i) {
      m_rlo_[i] = (i < in.row_low.size()) ? norm(in.row_low[i]) : -kInf;
      m_rup_[i] = (i < in.row_upp.size()) ? norm(in.row_upp[i]) : kInf;
    }
    m_is_int_.assign(ncols, 0);
    for (const int j : in.int_cols) {
      m_is_int_[static_cast<std::size_t>(j)] = 1;
    }
    m_in_queue_.assign(m_nrows_, 0);
    m_ignored_.assign(m_nrows_, 0);
    for (std::size_t i = 0; i < m_nrows_; ++i) {
      const bool free_row = m_rlo_[i] == -kInf && m_rup_[i] == kInf;
      if (free_row || m_csr_->length(i) > in.max_row_length) {
        m_ignored_[i] = 1;
        ++m_ignored_count_;
      }
    }
  }

  [[nodiscard]] double lb(std::size_t j) const noexcept { return m_lb_[j]; }
  [[nodiscard]] double ub(std::size_t j) const noexcept { return m_ub_[j]; }
  [[nodiscard]] std::size_t mark() const noexcept { return m_trail_.size(); }
  [[nodiscard]] bool exhausted() const noexcept { return m_exhausted_; }
  [[nodiscard]] std::size_t work() const noexcept { return m_work_; }
  [[nodiscard]] int ignored_count() const noexcept { return m_ignored_count_; }

  /// Undo every bound change recorded after `mark`.
  void undo(std::size_t mark)
  {
    while (m_trail_.size() > mark) {
      const auto& t = m_trail_.back();
      m_lb_[t.col] = t.lb;
      m_ub_[t.col] = t.ub;
      m_trail_.pop_back();
    }
    clear_queue();
  }

  /// Fix column `j` to `v` (bounds) and enqueue its rows.
  void fix(std::size_t j, double v) { change(j, v, v); }

  /// Fix column `j` to `v` WITHOUT enqueuing its rows (a deferred cell set
  /// blindly — propagating it would only re-report its conflict).
  void force(std::size_t j, double v)
  {
    m_trail_.push_back({.col = j, .lb = m_lb_[j], .ub = m_ub_[j]});
    m_lb_[j] = v;
    m_ub_[j] = v;
  }

  /// Enqueue every (non-ignored) row — the root propagation.
  void enqueue_all()
  {
    for (std::size_t i = 0; i < m_nrows_; ++i) {
      enqueue(i);
    }
  }

  /// Process the queue.  @return false on a conflict (queue cleared).  With
  /// `drop_conflicts`, a conflicting row is ignored from then on and the
  /// queue keeps going (root propagation: the relaxation satisfies every
  /// row, so a root conflict is numerical noise, not infeasibility).
  [[nodiscard]] bool propagate(bool drop_conflicts = false)
  {
    while (m_head_ < m_queue_.size()) {
      if (m_exhausted_) {
        clear_queue();
        return true;
      }
      const std::size_t i = m_queue_[m_head_++];
      m_in_queue_[i] = 0;
      if (m_ignored_[i] != 0) {
        continue;
      }
      if (!process_row(i)) {
        if (drop_conflicts) {
          m_ignored_[i] = 1;
          ++m_ignored_count_;
          continue;
        }
        clear_queue();
        return false;
      }
      if (m_work_limit_ > 0 && m_work_ > m_work_limit_) {
        m_exhausted_ = true;
      }
    }
    clear_queue();
    return true;
  }

private:
  struct TrailEntry
  {
    std::size_t col {};
    double lb {};
    double ub {};
  };

  void clear_queue() noexcept
  {
    for (std::size_t k = m_head_; k < m_queue_.size(); ++k) {
      m_in_queue_[m_queue_[k]] = 0;
    }
    m_queue_.clear();
    m_head_ = 0;
  }

  void enqueue(std::size_t i)
  {
    if (m_in_queue_[i] == 0 && m_ignored_[i] == 0) {
      m_in_queue_[i] = 1;
      m_queue_.push_back(i);
    }
  }

  void change(std::size_t j, double nl, double nu)
  {
    m_trail_.push_back({.col = j, .lb = m_lb_[j], .ub = m_ub_[j]});
    m_lb_[j] = nl;
    m_ub_[j] = nu;
    if (j >= m_flat_ncols_) {
      return;  // a column added after flatten (e.g. a future-cost α)
    }
    const auto kb = static_cast<std::size_t>(m_flat_->matbeg[j]);
    const auto ke = static_cast<std::size_t>(m_flat_->matbeg[j + 1]);
    for (std::size_t k = kb; k < ke; ++k) {
      enqueue(static_cast<std::size_t>(m_flat_->matind[k]));
    }
  }

  /// Contribution of `a·x_j` to the row's min / max activity.
  [[nodiscard]] std::pair<double, double> contrib(double a,
                                                  std::size_t j) const noexcept
  {
    const double l = m_lb_[j];
    const double u = m_ub_[j];
    if (a > 0.0) {
      return {(l == -kInf) ? -kInf : a * l, (u == kInf) ? kInf : a * u};
    }
    return {(u == kInf) ? -kInf : a * u, (l == -kInf) ? kInf : a * l};
  }

  /// Check row `i` and tighten its columns.  @return false on a conflict.
  [[nodiscard]] bool process_row(std::size_t i)
  {
    const std::size_t rb = m_csr_->beg[i];
    const std::size_t re = m_csr_->beg[i + 1];
    m_work_ += 2 * (re - rb);
    double minf = 0.0;
    double maxf = 0.0;
    int nmin_inf = 0;
    int nmax_inf = 0;
    for (std::size_t k = rb; k < re; ++k) {
      const auto [cmin, cmax] =
          contrib(m_csr_->val[k], static_cast<std::size_t>(m_csr_->ind[k]));
      if (cmin == -kInf) {
        ++nmin_inf;
      } else {
        minf += cmin;
      }
      if (cmax == kInf) {
        ++nmax_inf;
      } else {
        maxf += cmax;
      }
    }
    const double rlo = m_rlo_[i];
    const double rup = m_rup_[i];
    if (nmin_inf == 0 && rup != kInf
        && minf > rup + (kFeasTol * std::max(1.0, std::abs(rup))))
    {
      return false;
    }
    if (nmax_inf == 0 && rlo != -kInf
        && maxf < rlo - (kFeasTol * std::max(1.0, std::abs(rlo))))
    {
      return false;
    }
    // Nothing to tighten when neither side can bound a single column.
    const bool up_usable = rup != kInf && nmin_inf <= 1;
    const bool lo_usable = rlo != -kInf && nmax_inf <= 1;
    if (!up_usable && !lo_usable) {
      return true;
    }

    m_pending_.clear();
    for (std::size_t k = rb; k < re; ++k) {
      const double a = m_csr_->val[k];
      if (a == 0.0) {
        continue;
      }
      const auto j = static_cast<std::size_t>(m_csr_->ind[k]);
      if (m_lb_[j] == m_ub_[j]) {
        continue;  // already fixed
      }
      const auto [cmin, cmax] = contrib(a, j);
      // Residual min/max activity of the row without column j.
      double resmin = -kInf;
      if (cmin == -kInf) {
        if (nmin_inf == 1) {
          resmin = minf;
        }
      } else if (nmin_inf == 0) {
        resmin = minf - cmin;
      }
      double resmax = kInf;
      if (cmax == kInf) {
        if (nmax_inf == 1) {
          resmax = maxf;
        }
      } else if (nmax_inf == 0) {
        resmax = maxf - cmax;
      }
      double nl = -kInf;
      double nu = kInf;
      if (rup != kInf && resmin != -kInf) {
        const double b = (rup - resmin) / a;
        if (a > 0.0) {
          nu = b;
        } else {
          nl = b;
        }
      }
      if (rlo != -kInf && resmax != kInf) {
        const double b = (rlo - resmax) / a;
        if (a > 0.0) {
          nl = std::max(nl, b);
        } else {
          nu = std::min(nu, b);
        }
      }
      if (nl == -kInf && nu == kInf) {
        continue;
      }
      m_pending_.push_back({.col = j, .lb = nl, .ub = nu});
    }

    return std::ranges::all_of(m_pending_,
                               [this](const TrailEntry& p)
                               { return tighten(p.col, p.lb, p.ub); });
  }

  /// Tighten column `j` to `[nl, nu]` ∩ current.  @return false on conflict.
  [[nodiscard]] bool tighten(std::size_t j, double nl, double nu)
  {
    const double l = m_lb_[j];
    const double u = m_ub_[j];
    double new_l = l;
    double new_u = u;
    if (m_is_int_[j] != 0) {
      if (nu != kInf) {
        new_u = std::min(u, std::floor(nu + kIntTol));
      }
      if (nl != -kInf) {
        new_l = std::max(l, std::ceil(nl - kIntTol));
      }
      if (new_l > new_u) {
        return false;
      }
    } else {
      // Continuous: accept only significant tightenings, slightly relaxed so
      // a derived bound never cuts a feasible point by round-off.
      const double width =
          (l != -kInf && u != kInf) ? std::max(1.0, u - l) : 1.0;
      if (nu != kInf) {
        const double cand = nu + (kFeasTol * std::max(1.0, std::abs(nu)));
        if (cand < u - (kContStep * width)) {
          new_u = cand;
        }
      }
      if (nl != -kInf) {
        const double cand = nl - (kFeasTol * std::max(1.0, std::abs(nl)));
        if (cand > l + (kContStep * width)) {
          new_l = cand;
        }
      }
      const double tol =
          kFeasTol * std::max({1.0, std::abs(new_l), std::abs(new_u)});
      if (new_l > new_u + tol) {
        return false;
      }
      if (new_l > new_u) {
        // Within tolerance: collapse onto the old bound that is still valid.
        if (new_l != l) {
          new_l = new_u;
        } else {
          new_u = new_l;
        }
      }
    }
    if (new_l != l || new_u != u) {
      change(j, new_l, new_u);
    }
    return true;
  }

  const FlatLinearProblem* m_flat_;
  const CsrMatrix* m_csr_;
  std::size_t m_nrows_ {};
  std::size_t m_flat_ncols_ {};
  std::size_t m_work_limit_ {};
  std::size_t m_work_ {0};
  bool m_exhausted_ {false};
  int m_ignored_count_ {0};
  std::vector<double> m_lb_ {};
  std::vector<double> m_ub_ {};
  std::vector<double> m_rlo_ {};
  std::vector<double> m_rup_ {};
  std::vector<char> m_is_int_ {};
  std::vector<char> m_in_queue_ {};
  std::vector<char> m_ignored_ {};
  std::vector<std::size_t> m_queue_ {};
  std::size_t m_head_ {0};
  std::vector<TrailEntry> m_trail_ {};
  std::vector<TrailEntry> m_pending_ {};
};

/// Round `v` to the nearest integer and clamp it into `[l, u]`.
[[nodiscard]] double round_clamp(double v, double l, double u) noexcept
{
  return std::clamp(std::round(v), l, u);
}

}  // namespace

DiveOutput propagation_dive(const DiveInput& in)
{
  DiveOutput out;
  if (in.flat == nullptr || in.flat->matbeg.empty()) {
    out.values.assign(in.target.begin(), in.target.end());
    return out;
  }
  const auto& flat = *in.flat;
  const auto ncols = in.col_low.size();  // live LP (may exceed flat)
  const CsrMatrix csr(flat);

  DiveInput tuned = in;
  if (tuned.work_limit == 0) {
    tuned.work_limit = 200 * std::max<std::size_t>(csr.val.size(), 1);
  }
  Propagator prop(tuned, csr);

  // Root propagation: tighten the domains implied by the original bounds.
  prop.enqueue_all();
  (void)prop.propagate(/*drop_conflicts=*/true);

  // Fixing order: decreasing priority, ties by index (deterministic).
  std::vector<int> order(in.int_cols.begin(), in.int_cols.end());
  std::ranges::stable_sort(order,
                           [&](int a, int b)
                           {
                             return in.priority[static_cast<std::size_t>(a)]
                                 > in.priority[static_cast<std::size_t>(b)];
                           });

  std::vector<int> deferred;
  for (const int jj : order) {
    const auto j = static_cast<std::size_t>(jj);
    const double l = prop.lb(j);
    const double u = prop.ub(j);
    if (l == u) {
      ++out.forced;  // implied by earlier fixings (or fixed in the input)
      continue;
    }
    const double v = round_clamp(in.target[j], l, u);
    const std::size_t m = prop.mark();
    prop.fix(j, v);
    if (prop.propagate()) {
      ++out.fixed;
      continue;
    }
    prop.undo(m);
    // Alternatives: the other end of a binary domain, else v ± 1.
    bool placed = false;
    for (const double alt : {v + 1.0, v - 1.0}) {
      if (alt < l || alt > u) {
        continue;
      }
      prop.fix(j, alt);
      if (prop.propagate()) {
        placed = true;
        break;
      }
      prop.undo(m);
    }
    if (placed) {
      ++out.flipped;
    } else {
      deferred.push_back(jj);
    }
  }
  // Deferred cells: both values conflicted given the earlier fixings — set
  // the target blindly; the fixed-LP verify + repair stage handles them.
  for (const int jj : deferred) {
    const auto j = static_cast<std::size_t>(jj);
    if (prop.lb(j) == prop.ub(j)) {
      ++out.forced;
      continue;
    }
    prop.force(j, round_clamp(in.target[j], prop.lb(j), prop.ub(j)));
    ++out.deferred;
  }

  out.values.assign(in.target.begin(), in.target.end());
  for (const int jj : in.int_cols) {
    const auto j = static_cast<std::size_t>(jj);
    out.values[j] = prop.lb(j);
  }
  (void)ncols;
  out.ignored_rows = prop.ignored_count();
  out.budget_exhausted = prop.exhausted();
  out.work = prop.work();
  return out;
}

std::vector<int> violated_integer_rows(const FlatLinearProblem& flat,
                                       std::span<const double> row_low,
                                       std::span<const double> row_upp,
                                       std::span<const int> int_cols,
                                       std::span<const double> values,
                                       double infinity,
                                       double tol)
{
  std::vector<int> bad;
  if (flat.matbeg.empty()) {
    return bad;
  }
  const auto ncols = static_cast<std::size_t>(flat.ncols);
  const auto nrows = static_cast<std::size_t>(flat.nrows);
  std::vector<char> is_int(ncols, 0);
  for (const int j : int_cols) {
    if (std::cmp_less(j, ncols)) {
      is_int[static_cast<std::size_t>(j)] = 1;
    }
  }
  std::vector<double> act(nrows, 0.0);
  std::vector<char> has_cont(nrows, 0);
  std::vector<char> has_any(nrows, 0);
  for (std::size_t j = 0; j < ncols; ++j) {
    const auto kb = static_cast<std::size_t>(flat.matbeg[j]);
    const auto ke = static_cast<std::size_t>(flat.matbeg[j + 1]);
    for (std::size_t k = kb; k < ke; ++k) {
      const auto i = static_cast<std::size_t>(flat.matind[k]);
      if (flat.matval[k] == 0.0) {
        continue;
      }
      has_any[i] = 1;
      if (is_int[j] == 0) {
        has_cont[i] = 1;
      } else {
        act[i] += flat.matval[k] * values[j];
      }
    }
  }
  for (std::size_t i = 0; i < nrows; ++i) {
    if (has_any[i] == 0 || has_cont[i] != 0) {
      continue;
    }
    const double lo = (i < row_low.size()) ? row_low[i] : -infinity;
    const double up = (i < row_upp.size()) ? row_upp[i] : infinity;
    if ((up < infinity && act[i] > up + (tol * std::max(1.0, std::abs(up))))
        || (lo > -infinity
            && act[i] < lo - (tol * std::max(1.0, std::abs(lo)))))
    {
      bad.push_back(static_cast<int>(i));
    }
  }
  return bad;
}

std::map<std::string, int> row_families(const LinearInterface& li,
                                        std::span<const int> rows)
{
  std::map<std::string, int> fam;
  for (const int r : rows) {
    const auto* lbl = li.row_label_at(RowIndex {r});
    if (lbl != nullptr) {
      ++fam[std::string {lbl->class_name} + ":"
            + std::string {lbl->constraint_name}];
    } else {
      ++fam["unlabeled"];
    }
  }
  return fam;
}

FixedLpResult solve_fixed_integer_lp(LinearInterface& li,
                                     std::span<const int> int_cols,
                                     std::span<const double> candidate,
                                     const SolverOptions& opts)
{
  FixedLpResult res;
  const auto n = int_cols.size();
  if (n == 0) {
    return res;
  }
  const auto lo = li.get_col_low_raw();
  const auto up = li.get_col_upp_raw();
  std::vector<double> old_lo(n);
  std::vector<double> old_up(n);
  std::vector<ColIndex> idx(n);
  std::vector<char> lu(n, 'B');
  std::vector<double> vals(n);
  for (std::size_t k = 0; k < n; ++k) {
    const auto j = static_cast<std::size_t>(int_cols[k]);
    old_lo[k] = lo[j];
    old_up[k] = up[j];
    idx[k] = ColIndex {int_cols[k]};
    vals[k] = round_clamp(candidate[j], old_lo[k], old_up[k]);
  }
  li.set_col_bounds_raw(idx, lu, vals);

  bool ok = false;
  try {
    const auto rr = li.resolve(opts);
    ok = rr.has_value() && li.is_optimal();
  } catch (const std::exception& e) {
    spdlog::warn("MIP-start[dive]: fixed-integer LP solve threw: {}", e.what());
  }
  if (ok) {
    res.feasible = true;
    res.obj = li.get_obj_value();
    const auto sol = li.get_col_sol_raw();
    res.solution.assign(sol.begin(), sol.end());
  }

  // Restore the original integer-column bounds.
  std::vector<ColIndex> ridx;
  std::vector<char> rlu;
  std::vector<double> rvals;
  ridx.reserve(2 * n);
  rlu.reserve(2 * n);
  rvals.reserve(2 * n);
  for (std::size_t k = 0; k < n; ++k) {
    ridx.push_back(idx[k]);
    rlu.push_back('L');
    rvals.push_back(old_lo[k]);
    ridx.push_back(idx[k]);
    rlu.push_back('U');
    rvals.push_back(old_up[k]);
  }
  li.set_col_bounds_raw(ridx, rlu, rvals);
  return res;
}

std::vector<double> solve_elastic_lp(LinearInterface& li,
                                     std::span<const int> int_cols,
                                     std::span<const double> candidate,
                                     double penalty,
                                     const SolverOptions& opts)
{
  const auto obj = li.get_obj_coeff();
  const std::vector<double> orig(obj.begin(), obj.end());
  // penalty > 0: original objective + penalty·deviation.  penalty <= 0: a
  // PURE feasibility LP (deviation only, unit weights) — far better
  // conditioned, and from the relaxation basis it starts primal feasible.
  std::vector<double> mod =
      (penalty > 0.0) ? orig : std::vector<double>(orig.size(), 0.0);
  const double w = (penalty > 0.0) ? penalty : 1.0;
  const auto lo = li.get_col_low_raw();
  const auto up = li.get_col_upp_raw();
  for (const int jj : int_cols) {
    const auto j = static_cast<std::size_t>(jj);
    const double v = candidate[j];
    // Linear |x − v| for a candidate at a bound: push toward that bound.
    if (v <= lo[j] + 0.5) {
      mod[j] += w;
    } else if (v >= up[j] - 0.5) {
      mod[j] -= w;
    }
  }
  li.set_obj_coeffs_raw(mod);
  std::vector<double> sol;
  try {
    const auto rr = li.resolve(opts);
    if (rr.has_value() && li.is_optimal()) {
      const auto s = li.get_col_sol_raw();
      sol.assign(s.begin(), s.end());
    }
  } catch (const std::exception& e) {
    spdlog::warn("MIP-start[dive]: elastic LP solve threw: {}", e.what());
  }
  li.set_obj_coeffs_raw(orig);
  return sol;
}

namespace
{

/// Push the dive's progress lines out of the async logger now: the stage can
/// run for minutes on a large UC before the MIP solve, and an interrupted run
/// must not lose its diagnosis.
void flush_log()
{
  if (const auto logger = spdlog::default_logger(); logger) {
    logger->flush();
  }
}

/// Log `rows` grouped by label family (top entries only).
void log_families(const LinearInterface& li,
                  std::span<const int> rows,
                  std::string_view what)
{
  if (rows.empty()) {
    spdlog::info("MIP-start[dive]: {}: 0 violated pure-integer rows", what);
    return;
  }
  const auto fam = row_families(li, rows);
  std::vector<std::pair<std::string, int>> v(fam.begin(), fam.end());
  std::ranges::sort(
      v, [](const auto& a, const auto& b) { return a.second > b.second; });
  std::string s;
  const std::size_t n = std::min<std::size_t>(v.size(), 8);
  for (std::size_t k = 0; k < n; ++k) {
    s += std::format("{}{}={}", (k == 0) ? "" : ", ", v[k].first, v[k].second);
  }
  spdlog::info("MIP-start[dive]: {}: {} violated pure-integer rows ({})",
               what,
               rows.size(),
               s);
}

/// The bounded repair sub-MIP: free the `free_mask` integer cells, fix the
/// rest at `candidate`, restore integrality, solve with `opts` (time-limited),
/// read the incumbent back, then re-relax.  @return the incumbent's dense
/// raw values (may be empty / stale when the sub-MIP found nothing — the
/// caller verifies with the fixed LP anyway).
[[nodiscard]] std::vector<double> solve_repair_sub_mip(
    LinearInterface& li,
    std::span<const int> int_cols,
    std::span<const double> candidate,
    std::span<const char> free_mask,
    const SolverOptions& opts)
{
  const auto lo = li.get_col_low_raw();
  const auto up = li.get_col_upp_raw();
  std::vector<ColIndex> idx;
  std::vector<char> lu;
  std::vector<double> vals;
  std::vector<ColIndex> ridx;
  std::vector<char> rlu;
  std::vector<double> rvals;
  for (const int jj : int_cols) {
    const auto j = static_cast<std::size_t>(jj);
    if (free_mask[j] != 0) {
      continue;
    }
    idx.emplace_back(jj);
    lu.push_back('B');
    vals.push_back(round_clamp(candidate[j], lo[j], up[j]));
    ridx.emplace_back(jj);
    rlu.push_back('L');
    rvals.push_back(lo[j]);
    ridx.emplace_back(jj);
    rlu.push_back('U');
    rvals.push_back(up[j]);
  }
  li.set_col_bounds_raw(idx, lu, vals);
  li.restore_integers(int_cols);
  std::vector<double> sol;
  try {
    const auto rr = li.resolve(opts);
    (void)rr;  // a time-limited sub-MIP is rarely "optimal"; verify instead
    const auto s = li.get_col_sol_raw();
    if (s.size() == candidate.size()) {
      sol.assign(s.begin(), s.end());
    }
  } catch (const std::exception& e) {
    spdlog::warn("MIP-start[dive]: repair sub-MIP threw: {}", e.what());
  }
  li.set_col_bounds_raw(ridx, rlu, rvals);
  (void)li.relax_integers();
  return sol;
}

}  // namespace

std::optional<std::vector<double>> dive_mip_start(MipStartContext& ctx,
                                                  DiveSummary& summary)
{
  using clock = std::chrono::steady_clock;
  const auto t0 = clock::now();
  const auto secs = [&]
  { return std::chrono::duration<double>(clock::now() - t0).count(); };

  auto& li = ctx.li;
  const auto sol0 = li.get_col_sol_raw();
  if (sol0.empty()) {
    return std::nullopt;
  }
  const std::vector<double> relax(sol0.begin(), sol0.end());
  const auto ncols = relax.size();
  const auto lo_span = li.get_col_low_raw();
  const auto up_span = li.get_col_upp_raw();
  const std::vector<double> col_lo(lo_span.begin(), lo_span.end());
  const std::vector<double> col_up(up_span.begin(), up_span.end());
  const auto rl_span = li.get_row_low_raw();
  const auto ru_span = li.get_row_upp_raw();
  const std::vector<double> row_lo(rl_span.begin(), rl_span.end());
  const std::vector<double> row_up(ru_span.begin(), ru_span.end());
  const auto& dopts = ctx.opts.dive;
  const double threshold = ctx.opts.round.threshold.value_or(0.5);
  const double infinity = li.infinity();

  // Targets: the threshold-rounded relaxation (+ an external seed overlay);
  // priority: relaxation confidence (integral cells first).
  std::vector<double> target = relax;
  std::vector<double> priority(ncols, 0.0);
  for (const int jj : ctx.int_cols) {
    const auto j = static_cast<std::size_t>(jj);
    target[j] =
        detail::round_with_threshold(relax[j], threshold, col_lo[j], col_up[j]);
    const double frac = relax[j] - std::floor(relax[j]);
    priority[j] = std::abs(frac - 0.5) * 2.0;
  }
  if (const auto& sf = ctx.opts.seed_solution_file;
      sf.has_value() && !sf->empty())
  {
    if (auto seed = load_seed_commitment(*sf)) {
      const SeedCommitmentRule rule(std::move(*seed));
      const int n = rule.apply(target,
                               DomainRuleContext {
                                   .commitments = ctx.commitments,
                               });
      spdlog::info("MIP-start[dive]: seed overrode {} status targets", n);
    }
  }

  const FlatLinearProblem* flat = ctx.flat_lp;
  const bool have_flat = flat != nullptr && !flat->matbeg.empty()
      && std::cmp_less_equal(flat->ncols, ncols);
  if (!have_flat) {
    spdlog::info(
        "MIP-start[dive]: no usable flat LP ({}) — dive degrades to round + "
        "domain rules",
        (flat == nullptr) ? "not retained" : "column mismatch");
  }
  const bool propagate = have_flat && dopts.propagate.value_or(true);
  summary.propagated = propagate;

  if (have_flat) {
    const auto bad = violated_integer_rows(
        *flat, row_lo, row_up, ctx.int_cols, target, infinity);
    summary.violated_int_rows_rounded = static_cast<int>(bad.size());
    log_families(li, bad, "plain rounding");
  }

  const auto run_dive =
      [&](std::span<const double> tgt, std::span<const double> prio)
  {
    if (!propagate) {
      // No matrix: legacy round + domain rules on the target.
      std::vector<double> cand(tgt.begin(), tgt.end());
      const DomainRulePipeline pipeline =
          make_default_domain_rules(ctx.opts.domain_rules);
      (void)pipeline.apply(cand,
                           DomainRuleContext {
                               .commitments = ctx.commitments,
                               .injections = ctx.injections,
                           });
      return cand;
    }
    const DiveInput in {
        .flat = flat,
        .col_low = col_lo,
        .col_upp = col_up,
        .row_low = row_lo,
        .row_upp = row_up,
        .int_cols = ctx.int_cols,
        .target = tgt,
        .priority = prio,
        .infinity = infinity,
    };
    auto out = propagation_dive(in);
    spdlog::info(
        "MIP-start[dive]: dive fixed={} flipped={} forced={} deferred={} "
        "ignored_rows={} work={}{} ({:.1f}s)",
        out.fixed,
        out.flipped,
        out.forced,
        out.deferred,
        out.ignored_rows,
        out.work,
        out.budget_exhausted ? " (work budget exhausted)" : "",
        secs());
    return std::move(out.values);
  };

  std::vector<double> cand = run_dive(target, priority);
  if (have_flat) {
    const auto bad = violated_integer_rows(
        *flat, row_lo, row_up, ctx.int_cols, cand, infinity);
    summary.violated_int_rows_dive = static_cast<int>(bad.size());
    log_families(li, bad, "after dive");
  }
  flush_log();

  if (!dopts.verify.value_or(true)) {
    return cand;
  }

  // LP options for the verify / elastic solves: no algorithm-fallback
  // ladder, no .prm (the method is chosen here, per solve).
  SolverOptions lp_opts = ctx.relax_opts;
  lp_opts.max_fallbacks = 0;
  lp_opts.param_file.reset();
  lp_opts.algorithm = LPAlgo::dual;
  SolverOptions elastic_opts = lp_opts;
  elastic_opts.algorithm = LPAlgo::primal;
  // The fixed-integer LP is solved COLD by barrier + crossover: with every
  // integer fixed, presolve removes most of the model, whereas a warm dual
  // simplex from the relaxation basis cannot presolve and, on a weekly UC
  // with ~10^5 binaries, needs tens of thousands of iterations.
  lp_opts.force_barrier_crossover = true;
  lp_opts.advanced_basis = false;

  // Unset penalty ⇒ pure-feasibility elastic LP (see solve_elastic_lp).
  const double penalty = dopts.penalty.value_or(0.0);

  // The relaxation's optimal basis: re-installed before every elastic
  // solve, for which it is primal feasible (warm primal, no phase 1).  Backends
  // without basis I/O return nullopt and simply warm-start from their last
  // basis.
  const auto relax_basis = li.get_basis();
  const auto reset_basis = [&]
  {
    if (relax_basis.has_value()) {
      (void)li.set_basis(*relax_basis);
    }
  };

  const int rounds =
      std::max(0, static_cast<int>(dopts.repair_rounds.value_or(3)));
  std::vector<char> trouble(ncols, 0);
  bool any_trouble = false;
  for (int r = 0;; ++r) {
    auto fx = solve_fixed_integer_lp(li, ctx.int_cols, cand, lp_opts);
    if (fx.feasible) {
      summary.verified = true;
      summary.start_obj = fx.obj;
      spdlog::info(
          "MIP-start[dive]: start VERIFIED feasible after {} repair round(s) "
          "— fixed-integer LP obj={:.10g} ({:.1f}s)",
          r,
          fx.obj.value_or(0.0),
          secs());
      return std::move(fx.solution);
    }
    spdlog::info(
        "MIP-start[dive]: fixed-integer LP infeasible (round {}, {:.1f}s)",
        r,
        secs());
    flush_log();
    if (r >= rounds) {
      break;
    }
    ++summary.repair_rounds;
    reset_basis();
    const auto el =
        solve_elastic_lp(li, ctx.int_cols, cand, penalty, elastic_opts);
    if (el.size() != ncols) {
      spdlog::warn("MIP-start[dive]: elastic LP failed; stopping repair");
      break;
    }
    int moved = 0;
    for (const int jj : ctx.int_cols) {
      const auto j = static_cast<std::size_t>(jj);
      const double d = el[j] - cand[j];
      target[j] = cand[j];
      if (std::abs(d) <= 1e-6) {
        continue;
      }
      target[j] =
          (d > 0.0) ? std::ceil(el[j] - kIntTol) : std::floor(el[j] + kIntTol);
      priority[j] = 2.0 + std::abs(d);  // decide the repaired cells first
      trouble[j] = 1;
      any_trouble = true;
      ++moved;
    }
    spdlog::info(
        "MIP-start[dive]: repair round {}: elastic LP moves {} integer cells "
        "({:.1f}s)",
        r + 1,
        moved,
        secs());
    flush_log();
    if (moved == 0) {
      break;
    }
    cand = run_dive(target, priority);
  }

  // Bounded sub-MIP over the cells the elastic LP flagged plus the integer
  // columns of the pure-integer rows the candidate still violates (so the
  // sub-MIP also runs with the elastic rounds disabled), widened by the
  // integer columns sharing a short row with them.
  std::optional<CsrMatrix> csr;
  std::vector<char> is_int(ncols, 0);
  for (const int j : ctx.int_cols) {
    is_int[static_cast<std::size_t>(j)] = 1;
  }
  if (have_flat) {
    csr.emplace(*flat);
    for (const int i : violated_integer_rows(
             *flat, row_lo, row_up, ctx.int_cols, cand, infinity))
    {
      const auto ui = static_cast<std::size_t>(i);
      for (std::size_t q = csr->beg[ui]; q < csr->beg[ui + 1]; ++q) {
        const auto c = static_cast<std::size_t>(csr->ind[q]);
        if (is_int[c] != 0) {
          trouble[c] = 1;
          any_trouble = true;
        }
      }
    }
  }
  if (dopts.sub_mip.value_or(true) && any_trouble) {
    std::vector<char> free_mask = trouble;
    if (csr.has_value()) {
      constexpr std::size_t kShortRow = 64;
      const auto flat_ncols = static_cast<std::size_t>(flat->ncols);
      for (std::size_t j = 0; j < flat_ncols; ++j) {
        if (trouble[j] == 0) {
          continue;
        }
        const auto kb = static_cast<std::size_t>(flat->matbeg[j]);
        const auto ke = static_cast<std::size_t>(flat->matbeg[j + 1]);
        for (std::size_t k = kb; k < ke; ++k) {
          const auto i = static_cast<std::size_t>(flat->matind[k]);
          if (csr->length(i) > kShortRow) {
            continue;
          }
          for (std::size_t q = csr->beg[i]; q < csr->beg[i + 1]; ++q) {
            const auto c = static_cast<std::size_t>(csr->ind[q]);
            if (is_int[c] != 0) {
              free_mask[c] = 1;
            }
          }
        }
      }
    }
    const auto nfree = std::ranges::count(free_mask, char {1});
    SolverOptions sub_opts = ctx.relax_opts;
    sub_opts.max_fallbacks = 0;
    sub_opts.time_limit = dopts.sub_mip_time_limit.value_or(120.0);
    spdlog::info(
        "MIP-start[dive]: repair sub-MIP over {} free integer cells "
        "(time_limit {:.0f}s)",
        nfree,
        sub_opts.time_limit.value_or(0.0));
    const auto sub =
        solve_repair_sub_mip(li, ctx.int_cols, cand, free_mask, sub_opts);
    if (sub.size() == ncols) {
      reset_basis();
      auto fx = solve_fixed_integer_lp(li, ctx.int_cols, sub, lp_opts);
      if (fx.feasible) {
        summary.verified = true;
        summary.sub_mip_used = true;
        summary.start_obj = fx.obj;
        spdlog::info(
            "MIP-start[dive]: start VERIFIED feasible by the repair sub-MIP — "
            "fixed-integer LP obj={:.10g} ({:.1f}s)",
            fx.obj.value_or(0.0),
            secs());
        return std::move(fx.solution);
      }
    }
    spdlog::warn("MIP-start[dive]: repair sub-MIP found no feasible start");
  }

  spdlog::warn(
      "MIP-start[dive]: could not verify a feasible start; injecting the dive "
      "candidate for backend repair ({:.1f}s)",
      secs());
  return cand;
}

}  // namespace gtopt
