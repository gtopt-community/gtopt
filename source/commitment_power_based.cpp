/**
 * @file      commitment_power_based.cpp
 * @brief     Power-based unit-commitment rows (Morales-España et al.)
 * @date      Wed Nov 25 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * PB1–PB6 of docs/unit-commitment.md §4.6:
 *  - PB1  P̂_b = P_b (u_k(b) + E_b v_k⁺) + p_b + Σ f^SU v + Σ f^SD w
 *  - PB2  g_b = ½ (P̂_{b−1} + P̂_b)
 *  - PB3  p_b + Σr⁺ ≤ (P̄−P)u_k − E_b (P̄−SD) w_k⁺ + E_b (SU−P) v_k⁺
 *  - PB4  p_b − Σr⁻ ≥ 0
 *  - PB5  p_b − p_{b−1} + Σr⁺ ≤ RU Δ_b u_k + E_b (SU−P) v_k⁺
 *  - PB6  p_{b−1} − p_b + Σr⁻ ≤ RD Δ_b u_k + F_b (SD−P) w_k
 * References: Morales-España, Gentile & Ramos, OR Spectrum 37 (2015),
 * eqs. 4–6, 12–15, 26–31; Morales-España, Baldick, García-González &
 * Ramos, IEEE Trans. Sustain. Energy 7(2) (2016), eqs. 13–16.
 */

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <utility>

#include <gtopt/commitment_power_based.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>

namespace gtopt
{

namespace
{
constexpr double kTimeEps = 1e-9;

/// Piecewise-linear interpolation through (0, p0), (j·h, y_j), j = 1..n,
/// zero beyond n·h.  `value(j)` returns y_j.
template<typename Value>
[[nodiscard]] double trajectory_at(
    std::size_t n, double step, double p0, double delta, Value value)
{
  if (n == 0 || step <= 0.0 || delta <= kTimeEps
      || delta > (static_cast<double>(n) * step) + kTimeEps)
  {
    return 0.0;
  }
  const double x = delta / step;
  const auto j = std::min(static_cast<std::size_t>(std::ceil(x - kTimeEps)),
                          n);  // segment ((j−1)h, jh]
  const double y_lo = j == 1 ? p0 : value(j - 1);
  const double y_hi = value(j);
  const double frac = x - static_cast<double>(j - 1);
  return y_lo + (frac * (y_hi - y_lo));
}
}  // namespace

double startup_trajectory_power(const std::vector<double>& traj,
                                double step,
                                double pmin,
                                double delta)
{
  // j hours before the start the power is P^SU_{S+1−j} = traj[S − j]
  const auto n = traj.size();
  return trajectory_at(
      n, step, pmin, delta, [&](std::size_t j) { return traj[n - j]; });
}

double shutdown_trajectory_power(const std::vector<double>& traj,
                                 double step,
                                 double pmin,
                                 double delta)
{
  // j hours after the shut-down the power is P^SD_{j+1} = traj[j − 1]
  return trajectory_at(traj.size(),
                       step,
                       pmin,
                       delta,
                       [&](std::size_t j) { return traj[j - 1]; });
}

PowerBasedResult add_power_based_rows(const PowerBasedUnit& unit,
                                      LinearProblem& lp)
{
  const auto nb = unit.gcol.size();
  const auto np = unit.ucol.size();
  PowerBasedResult res;
  if (nb == 0 || np == 0) {
    return res;
  }
  const bool slow_start = !unit.startup_trajectory.empty();
  const bool slow_stop = !unit.shutdown_trajectory.empty();
  const double h = unit.trajectory_step > 0.0 ? unit.trajectory_step : 1.0;

  // Real time of every boundary: tau[0] = 0, tau[b+1] = end of block b.
  std::vector<double> tau(nb + 1, 0.0);
  for (std::size_t b = 0; b < nb; ++b) {
    tau[b + 1] = tau[b] + unit.dt[b];
  }
  const double cycle_len = tau[nb];

  const auto ctx = [&](std::size_t b)
  {
    return make_block_context(unit.scenario_uid, unit.stage_uid, unit.buid[b]);
  };

  // Clamped start-up / shut-down capabilities of block b (GAMS / [5]:
  // P ≤ SU, SD ≤ P̄; a trajectory unit is slow-start, SU = SD = P).
  const auto su_cap = [&](std::size_t b)
  {
    return slow_start ? unit.pmin[b]
                      : std::clamp(unit.startup_cap[b],
                                   unit.pmin[b],
                                   std::max(unit.pmin[b], unit.pmax[b]));
  };
  const auto sd_cap = [&](std::size_t b)
  {
    return slow_stop ? unit.pmin[b]
                     : std::clamp(unit.shutdown_cap[b],
                                  unit.pmin[b],
                                  std::max(unit.pmin[b], unit.pmax[b]));
  };

  // E_b: block b ends its period with a next period (`next` = k⁺).
  const auto next_period = [&](std::size_t b) -> std::optional<std::size_t>
  {
    const auto k = unit.period[b];
    if (b + 1 < nb) {
      if (unit.period[b + 1] != k) {
        return unit.period[b + 1];
      }
      return std::nullopt;
    }
    if (unit.cyclic) {
      return unit.period[0];
    }
    return std::nullopt;
  };
  // F_b: block b starts its period.
  const auto starts_period = [&](std::size_t b)
  {
    if (b == 0) {
      return true;  // the first block always starts period 0
    }
    return unit.period[b - 1] != unit.period[b];
  };

  // Trajectory terms at boundary time t: (column, coefficient) pairs.
  const auto add_trajectories = [&](SparseRow& row, double t, double sign)
  {
    for (std::size_t s = 0; s < np && slow_start; ++s) {
      double delta = unit.period_start[s] - t;
      if (unit.cyclic) {
        delta = std::fmod(std::fmod(delta, cycle_len) + cycle_len, cycle_len);
      }
      // the period's first-block minimum is f^SU(0)
      const auto first = static_cast<std::size_t>(
          std::ranges::find(unit.period, s) - unit.period.begin());
      const double c = startup_trajectory_power(
          unit.startup_trajectory, h, unit.pmin[first], delta);
      if (c != 0.0) {
        row[unit.vcol[s]] += sign * c;
      }
    }
    for (std::size_t d = 0; d < np && slow_stop; ++d) {
      double delta = t - unit.period_start[d];
      if (unit.cyclic) {
        delta = std::fmod(std::fmod(delta, cycle_len) + cycle_len, cycle_len);
      }
      // the minimum of the last up block (the one before period d)
      const auto first = static_cast<std::size_t>(
          std::ranges::find(unit.period, d) - unit.period.begin());
      const auto prev = first > 0 ? first - 1 : nb - 1;
      const double c = shutdown_trajectory_power(
          unit.shutdown_trajectory, h, unit.pmin[prev], delta);
      if (c != 0.0) {
        row[unit.wcol[d]] += sign * c;
      }
    }
  };

  // ── Columns: p_b (above minimum) and P̂_b (total) at boundaries 1..B ──
  res.power.reserve(nb);
  res.power_above.reserve(nb);
  for (std::size_t b = 0; b < nb; ++b) {
    res.power_above.push_back(lp.add_col({
        .lowb = 0.0,
        .uppb = std::max(unit.pmax[b] - unit.pmin[b], 0.0),
        .class_name = unit.class_name,
        .variable_name = "power_above",
        .variable_uid = unit.uid,
        .context = ctx(b),
    }));
    res.power.push_back(lp.add_col({
        .lowb = 0.0,
        .uppb = std::max(unit.pmax[b], 0.0),
        .class_name = unit.class_name,
        .variable_name = "power",
        .variable_uid = unit.uid,
        .context = ctx(b),
    }));
  }

  // Boundary 0: the last boundary on a cyclic stage, else its own columns.
  ColIndex p0;
  ColIndex phat0;
  if (unit.cyclic) {
    p0 = res.power_above.back();
    phat0 = res.power.back();
  } else {
    const double pmin0 = unit.pmin[0];
    // A known initial power fixes P̂_0 (at least P for an on unit).
    // Otherwise P̂_0 is free within PB1 / PB3 at τ_0: an on unit anywhere
    // in [P, P̄], an off unit at P·v_0 (the start-up boundary, [5]).
    const bool known = unit.initial_power.has_value();
    double ini = unit.initial_power.value_or(0.0);
    if (known && unit.initial_u >= 0.5) {
      ini = std::max(ini, pmin0);
    }
    p0 = lp.add_col({
        .lowb = 0.0,
        .uppb = std::max(std::max(unit.pmax[0], ini) - pmin0, 0.0),
        .class_name = unit.class_name,
        .variable_name = "power_above_initial",
        .variable_uid = unit.uid,
        .context = ctx(0),
    });
    phat0 = lp.add_col({
        .lowb = known ? ini : 0.0,
        .uppb = known ? ini : std::max(unit.pmax[0], 0.0),
        .class_name = unit.class_name,
        .variable_name = "power_initial",
        .variable_uid = unit.uid,
        .context = ctx(0),
    });
    res.power_initial = phat0;

    // PB1 at τ_0: P̂_0 − P·v_0 − p_0 − traj = P·u_0
    auto row =
        SparseRow {
            .class_name = unit.class_name,
            .constraint_name = "power_initial_def",
            .variable_uid = unit.uid,
            .context = ctx(0),
        }
            .equal(pmin0 * unit.initial_u);
    row[phat0] = 1.0;
    row[unit.vcol[0]] += -pmin0;
    row[p0] += -1.0;
    add_trajectories(row, 0.0, -1.0);
    std::ignore = lp.add_row(std::move(row));

    // PB3 at τ_0: p_0 + (P̄−SD) w_0 − (SU−P) v_0 ≤ (P̄−P) u_0
    auto cap =
        SparseRow {
            .class_name = unit.class_name,
            .constraint_name = "power_initial_capacity",
            .variable_uid = unit.uid,
            .context = ctx(0),
        }
            .less_equal((unit.pmax[0] - pmin0) * unit.initial_u);
    cap[p0] = 1.0;
    cap[unit.wcol[0]] += unit.pmax[0] - sd_cap(0);
    cap[unit.vcol[0]] += -(su_cap(0) - pmin0);
    std::ignore = lp.add_row(std::move(cap));
  }

  res.trapezoid_rows.reserve(nb);
  res.capacity_rows.reserve(nb);
  res.ramp_up_rows.assign(nb, std::nullopt);
  res.ramp_down_rows.assign(nb, std::nullopt);

  for (std::size_t b = 0; b < nb; ++b) {
    const auto k = unit.period[b];
    const auto next = next_period(b);
    const auto pb = res.power_above[b];
    const auto phat = res.power[b];
    const auto p_prev = b == 0 ? p0 : res.power_above[b - 1];
    const auto phat_prev = b == 0 ? phat0 : res.power[b - 1];
    const double pmin = unit.pmin[b];
    const double pmax = unit.pmax[b];

    // PB1: P̂_b − P u_k − E P v_k⁺ − p_b − traj = 0
    {
      auto row =
          SparseRow {
              .class_name = unit.class_name,
              .constraint_name = "power_def",
              .variable_uid = unit.uid,
              .context = ctx(b),
          }
              .equal(0.0);
      row[phat] = 1.0;
      row[unit.ucol[k]] += -pmin;
      if (next) {
        row[unit.vcol[*next]] += -pmin;
      }
      row[pb] += -1.0;
      add_trajectories(row, tau[b + 1], -1.0);
      std::ignore = lp.add_row(std::move(row));
    }

    // PB2: g_b − ½ P̂_{b−1} − ½ P̂_b = 0
    {
      auto row =
          SparseRow {
              .class_name = unit.class_name,
              .constraint_name = "energy_trapezoid",
              .variable_uid = unit.uid,
              .context = ctx(b),
          }
              .equal(0.0);
      row[unit.gcol[b]] = 1.0;
      row[phat_prev] += -0.5;
      row[phat] += -0.5;
      res.trapezoid_rows.push_back(lp.add_row(std::move(row)));
    }

    // PB3: p_b + Σr⁺ − (P̄−P) u_k + E (P̄−SD) w_k⁺ − E (SU−P) v_k⁺ ≤ 0
    {
      auto row =
          SparseRow {
              .class_name = unit.class_name,
              .constraint_name = "power_capacity",
              .variable_uid = unit.uid,
              .context = ctx(b),
          }
              .less_equal(0.0);
      row[pb] = 1.0;
      for (const auto& rc : unit.reserve_up[b]) {
        row[rc] += 1.0;
      }
      row[unit.ucol[k]] += -(pmax - pmin);
      if (next) {
        row[unit.wcol[*next]] += pmax - sd_cap(b);
        row[unit.vcol[*next]] += -(su_cap(b) - pmin);
      }
      res.capacity_rows.push_back(lp.add_row(std::move(row)));
    }

    // PB4: p_b − Σr⁻ ≥ 0 (p_b ≥ 0 is its column bound otherwise)
    if (!unit.reserve_dn[b].empty()) {
      auto row =
          SparseRow {
              .class_name = unit.class_name,
              .constraint_name = "power_floor",
              .variable_uid = unit.uid,
              .context = ctx(b),
          }
              .greater_equal(0.0);
      row[pb] = 1.0;
      for (const auto& rc : unit.reserve_dn[b]) {
        row[rc] += -1.0;
      }
      std::ignore = lp.add_row(std::move(row));
    }

    // PB5: p_b − p_{b−1} + Σr⁺ − RU Δ u_k − E (SU−P) v_k⁺ ≤ 0
    if (unit.has_ramp_up) {
      auto row =
          SparseRow {
              .class_name = unit.class_name,
              .constraint_name = "power_ramp_up",
              .variable_uid = unit.uid,
              .context = ctx(b),
          }
              .less_equal(0.0);
      row[pb] += 1.0;
      row[p_prev] += -1.0;
      for (const auto& rc : unit.reserve_up[b]) {
        row[rc] += 1.0;
      }
      row[unit.ucol[k]] += -unit.ramp_up[b] * unit.dt[b];
      if (next) {
        row[unit.vcol[*next]] += -(su_cap(b) - pmin);
      }
      res.ramp_up_rows[b] = lp.add_row(std::move(row));
    }

    // PB6: p_{b−1} − p_b + Σr⁻ − RD Δ u_k − F (SD−P) w_k ≤ 0
    if (unit.has_ramp_down) {
      auto row =
          SparseRow {
              .class_name = unit.class_name,
              .constraint_name = "power_ramp_down",
              .variable_uid = unit.uid,
              .context = ctx(b),
          }
              .less_equal(0.0);
      row[p_prev] += 1.0;
      row[pb] += -1.0;
      for (const auto& rc : unit.reserve_dn[b]) {
        row[rc] += 1.0;
      }
      row[unit.ucol[k]] += -unit.ramp_down[b] * unit.dt[b];
      if (starts_period(b)) {
        row[unit.wcol[k]] += -(sd_cap(b) - pmin);
      }
      res.ramp_down_rows[b] = lp.add_row(std::move(row));
    }

    // PB7: a one-block outage (w_k = v_k⁺ = 1, u_k = 0) passes through
    // P̂ = P at both of its boundaries.  Without it the trapezoid of the
    // "off" block runs straight from SD to SU, so a quick unit could
    // drop its no-load cost and skip its ramp limit by cycling off for
    // one block.  Both rows are redundant in every other state.
    const bool wrap = b == 0 && unit.cyclic;
    const bool single =
        next && starts_period(b) && (!wrap || unit.period[nb - 1] != k);
    if (single) {
      const double su_room = su_cap(b) - pmin;
      if (su_room > 0.0) {
        // p_b + (SU−P) w_k − (P̄−P) u_k ≤ SU − P
        auto row =
            SparseRow {
                .class_name = unit.class_name,
                .constraint_name = "power_outage_end",
                .variable_uid = unit.uid,
                .context = ctx(b),
            }
                .less_equal(su_room);
        row[pb] += 1.0;
        row[unit.wcol[k]] += su_room;
        row[unit.ucol[k]] += -(pmax - pmin);
        std::ignore = lp.add_row(std::move(row));
      }
      std::size_t pr = 0;
      if (b > 0) {
        pr = b - 1;
      } else if (wrap) {
        pr = nb - 1;
      }
      const double sd_room = sd_cap(pr) - unit.pmin[pr];
      if (sd_room > 0.0) {
        // p_{b−1} − (P̄−P) u_{k−1} + (P̄−P) w_k + (SD−P) v_k⁺ ≤ SD − P
        const double span = unit.pmax[pr] - unit.pmin[pr];
        const bool known_prev = b == 0 && !unit.cyclic;
        auto row =
            SparseRow {
                .class_name = unit.class_name,
                .constraint_name = "power_outage_start",
                .variable_uid = unit.uid,
                .context = ctx(b),
            }
                .less_equal(sd_room
                            + (known_prev ? span * unit.initial_u : 0.0));
        row[p_prev] += 1.0;
        if (!known_prev) {
          row[unit.ucol[unit.period[pr]]] += -span;
        }
        row[unit.wcol[k]] += span;
        row[unit.vcol[*next]] += sd_room;
        std::ignore = lp.add_row(std::move(row));
      }
    }
  }
  return res;
}

}  // namespace gtopt
