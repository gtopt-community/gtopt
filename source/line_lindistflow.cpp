/**
 * @file      line_lindistflow.cpp
 * @brief     LinDistFlow rows of a line (`network_model = "lindistflow"`)
 * @date      2026-11-27
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Per block, with P the signed active flow A→B (the line's flow
 * columns), Q a free reactive-flow column, w the buses' squared
 * voltages (BusLP), r/x the series ohms at the line's `voltage` V [kV]:
 *
 *   Q balance:   −Q at bus A, +Q at bus B
 *   voltage:     w_a/t² − w_b − (2r/V²)·P − (2x/V²)·Q = 0
 *   taps:        fixed t = tap_ratio, or y = w_a/t² with
 *                w_a/t_max² ≤ y ≤ w_a/t_min²   (exact for continuous t)
 *   MVA polygon: cos θ_k·P + sin θ_k·Q ≤ S,   θ_k = 2πk/n, k = 0..n−1
 *                (S = capacity column when the line expands, else the
 *                forward thermal limit of the flow columns)
 *   candidates:  a line with no installed capacity and an expansion
 *                column C relaxes the drop row to
 *                |drop| ≤ M·(1 − C/C_max), M from the voltage bounds,
 *                plus |(2r/V²)·P + (2x/V²)·Q| ≤ M·C/C_max.  With
 *                α = C/C_max the pair is the McCormick hull of the
 *                bilinear "drop of a fraction α of the circuit"
 *                z = α·(w_a/t² − w_b): exact with integer modules, and
 *                the LP relaxation lets a fractional build carry only
 *                the flow its fraction of the voltage band allows
 *
 * Units: kV² = Ω·MW, so 2(rP + xQ)/V² is in p.u.² of the bus base.
 * The model neglects the series losses r·(P² + Q²)/V² (the P-loss
 * modes of `use_line_losses` still apply to the active balance).
 */

#include <algorithm>
#include <cmath>
#include <numbers>

#include <gtopt/bus_lp.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

void LineLP::add_lindistflow(
    const SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp,
    const BusLP& bus_a_lp,
    const BusLP& bus_b_lp,
    const BIndexHolder<std::vector<std::pair<ColIndex, double>>>& flow,
    std::optional<ColIndex> capacity_col)
{
  const auto& wa_cols = bus_a_lp.vsq_cols_at(scenario, stage);
  const auto& wb_cols = bus_b_lp.vsq_cols_at(scenario, stage);
  const auto& qa_rows = bus_a_lp.qbalance_rows_at(scenario, stage);
  const auto& qb_rows = bus_b_lp.qbalance_rows_at(scenario, stage);
  if (wa_cols.empty() || wb_cols.empty()) {
    return;
  }

  const auto suid = stage.uid();
  const double vkv = param_voltage(suid).value_or(bus_b_lp.voltage());
  const double v2 = vkv > 0.0 ? vkv * vkv : 1.0;
  const double kr = 2.0 * param_resistance(suid).value_or(0.0) / v2;
  const double kx = 2.0 * param_reactance(suid).value_or(0.0) / v2;
  const double tap = param_tap_ratio(suid).value_or(1.0);
  const auto& ln = line();
  const bool tap_var =
      ln.tap_ratio_min.has_value() || ln.tap_ratio_max.has_value();
  const double tmin = ln.tap_ratio_min.value_or(tap);
  const double tmax = ln.tap_ratio_max.value_or(tap);
  const int nsides = sc.options().mva_polygon_sides();
  const auto cname = Element::class_name.full_name();

  // Candidate (expansion-only) line: disjunctive big-M drop rows.
  const bool candidate =
      capacity_col.has_value() && capacity_at(stage, 0.0) <= 0.0;
  const double cap_max =
      capacity_col ? std::max(lp.get_col_uppb(*capacity_col), 1e-9) : 1.0;
  // Tightest M from the w bounds; an unbounded w falls back to
  // (1.5 p.u.)² (no feeder runs above 1.5 p.u.).
  constexpr double w_cap = 2.25;
  const auto big_m = [&](ColIndex wa, ColIndex wb)
  {
    const double wa_lo = lp.get_col_lowb(wa);
    const double wa_hi = std::min(lp.get_col_uppb(wa), w_cap);
    const double wb_lo = lp.get_col_lowb(wb);
    const double wb_hi = std::min(lp.get_col_uppb(wb), w_cap);
    const double ta = tap_var ? tmin : tap;
    const double tb = tap_var ? tmax : tap;
    return std::max(
        {(wa_hi / (ta * ta)) - wb_lo, wb_hi - (wa_lo / (tb * tb)), 1e-6});
  };

  BIndexHolder<ColIndex> qcols;
  BIndexHolder<ColIndex> ycols;
  BIndexHolder<RowIndex> drows;
  for (const auto& block : stage.blocks()) {
    const auto buid = block.uid();
    const auto wa = wa_cols.find(buid);
    const auto wb = wb_cols.find(buid);
    if (wa == wa_cols.end() || wb == wb_cols.end()) {
      continue;
    }
    const auto ctx = make_block_context(scenario.uid(), suid, buid);
    const auto qcol = lp.add_col({
        .lowb = -LinearProblem::DblMax,
        .uppb = LinearProblem::DblMax,
        .class_name = cname,
        .variable_name = QflowName,
        .variable_uid = uid(),
        .context = ctx,
    });
    qcols[buid] = qcol;
    if (const auto it = qa_rows.find(buid); it != qa_rows.end()) {
      lp.row_at(it->second)[qcol] = -1.0;
    }
    if (const auto it = qb_rows.find(buid); it != qb_rows.end()) {
      lp.row_at(it->second)[qcol] = 1.0;
    }

    static const std::vector<std::pair<ColIndex, double>> no_flow {};
    const auto fit = flow.find(buid);
    const auto& pterms = fit == flow.end() ? no_flow : fit->second;

    // Sending voltage seen through the tap: w_a/t² or the y column.
    std::optional<ColIndex> ycol;
    if (tap_var) {
      ycol = lp.add_col({
          .lowb = 0.0,
          .uppb = LinearProblem::DblMax,
          .class_name = cname,
          .variable_name = VsqTapName,
          .variable_uid = uid(),
          .context = ctx,
      });
      ycols[buid] = *ycol;
      auto lo =
          SparseRow {
              .class_name = cname,
              .constraint_name = TapName,
              .variable_uid = uid(),
              .context = make_block_context(scenario.uid(), suid, buid, 0),
          }
              .greater_equal(0.0);
      lo[*ycol] = 1.0;
      lo[wa->second] = -1.0 / (tmax * tmax);
      std::ignore = lp.add_row(std::move(lo));
      auto hi =
          SparseRow {
              .class_name = cname,
              .constraint_name = TapName,
              .variable_uid = uid(),
              .context = make_block_context(scenario.uid(), suid, buid, 1),
          }
              .less_equal(0.0);
      hi[*ycol] = 1.0;
      hi[wa->second] = -1.0 / (tmin * tmin);
      std::ignore = lp.add_row(std::move(hi));
    }

    SparseRow drow {
        .class_name = cname,
        .constraint_name = VdropName,
        .variable_uid = uid(),
        .context = ctx,
    };
    if (ycol) {
      drow[*ycol] = 1.0;
    } else {
      drow[wa->second] = 1.0 / (tap * tap);
    }
    drow[wb->second] = -1.0;
    for (const auto& [col, sign] : pterms) {
      if (kr != 0.0) {
        drow[col] = -kr * sign;
      }
    }
    if (kx != 0.0) {
      drow[qcol] = -kx;
    }
    if (candidate) {
      // Candidate line (no installed capacity): the drop holds only when
      // built.  |drop| ≤ M·(1 − C/C_max); unbuilt (C = 0) the capacity
      // and polygon rows already force P = Q = 0, so M only has to cover
      // |w_a/t² − w_b| from the voltage bounds.
      const double m_big = big_m(wa->second, wb->second);
      const double coef = m_big / cap_max;
      auto upper = drow;
      upper[*capacity_col] = coef;
      upper.less_equal(m_big);
      drows[buid] = lp.add_row(std::move(upper));
      drow[*capacity_col] = -coef;
      drow.greater_equal(-m_big);
      drow.context = make_block_context(scenario.uid(), suid, buid, 1);
      std::ignore = lp.add_row(std::move(drow));
      // Hull rows: |(2r/V²)·P + (2x/V²)·Q| ≤ M·C/C_max.  Redundant at
      // C ∈ {0, C_max}; they tighten the LP relaxation of fractional
      // builds (a partial circuit cannot carry a full circuit's flow
      // with a voltage drop that is not there).
      for (const double sgn : {1.0, -1.0}) {
        SparseRow zrow {
            .class_name = cname,
            .constraint_name = VdropCapName,
            .variable_uid = uid(),
            .context =
                make_block_context(scenario.uid(), suid, buid, sgn > 0 ? 0 : 1),
        };
        for (const auto& [col, sign] : pterms) {
          if (kr != 0.0) {
            zrow[col] = sgn * kr * sign;
          }
        }
        if (kx != 0.0) {
          zrow[qcol] = sgn * kx;
        }
        zrow[*capacity_col] = -coef;
        zrow.less_equal(0.0);
        std::ignore = lp.add_row(std::move(zrow));
      }
      if (nsides < 3) {
        // keep Q = 0 on an unbuilt line without the polygon
        for (const double sgn : {1.0, -1.0}) {
          auto qrow =
              SparseRow {
                  .class_name = cname,
                  .constraint_name = MvaName,
                  .variable_uid = uid(),
                  .context = make_block_context(
                      scenario.uid(), suid, buid, sgn > 0 ? 0 : 1),
              }
                  .less_equal(0.0);
          qrow[qcol] = sgn;
          qrow[*capacity_col] = -1.0;
          std::ignore = lp.add_row(std::move(qrow));
        }
      }
    } else {
      drows[buid] = lp.add_row(std::move(drow));
    }

    // ── MVA polygon ──────────────────────────────────────────────────
    if (nsides < 3 || pterms.empty()) {
      continue;
    }
    double rating = 0.0;
    if (!capacity_col) {
      for (const auto& [col, sign] : pterms) {
        if (sign > 0.0) {
          rating += lp.get_col_uppb(col);
        }
      }
      if (!(rating < LinearProblem::DblMax / 2)) {
        continue;
      }
    }
    for (int k = 0; k < nsides; ++k) {
      const double th = 2.0 * std::numbers::pi * k / nsides;
      const double c = std::cos(th);
      const double s = std::sin(th);
      auto prow =
          SparseRow {
              .class_name = cname,
              .constraint_name = MvaName,
              .variable_uid = uid(),
              .context = make_block_context(scenario.uid(), suid, buid, k),
          }
              .less_equal(capacity_col ? 0.0 : rating);
      for (const auto& [col, sign] : pterms) {
        if (std::abs(c) > 1e-12) {
          prow[col] = c * sign;
        }
      }
      if (std::abs(s) > 1e-12) {
        prow[qcol] = s;
      }
      if (capacity_col) {
        prow[*capacity_col] = -1.0;
      }
      std::ignore = lp.add_row(std::move(prow));
    }
  }

  const auto st_key = std::tuple {scenario.uid(), suid};
  if (!qcols.empty()) {
    qflow_cols[st_key] = std::move(qcols);
    vdrop_rows[st_key] = std::move(drows);
  }
  if (!ycols.empty()) {
    vsqtap_cols[st_key] = std::move(ycols);
  }
}

}  // namespace gtopt
