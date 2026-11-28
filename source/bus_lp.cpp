/**
 * @file      bus_lp.cpp
 * @brief     Implementation of BusLP class for bus linear programming
 * @date      Sat Feb  8 01:50:00 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module implements the BusLP class, providing the linear programming
 * representation of electrical buses in power system optimization. It handles
 * Kirchhoff's law constraints, phase angle variables (theta), and power
 * balance equations using modern C++26 features.
 */

#include <gtopt/bus_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

[[nodiscard]]
auto BusLP::needs_kirchhoff(const SystemContext& sc) const -> bool
{
  const auto& opts = sc.options();
  // Theta columns are needed only in the B–θ formulation.  In
  // `cycle_basis` mode KVL is enforced by per-cycle sums of signed
  // line flows (Σ ε · x_τ · f = Σ ε · φ) — there is no θ variable to
  // create, so this returns false to short-circuit `lazy_add_theta`.
  return !opts.use_single_bus() && opts.use_kirchhoff()
      && opts.kirchhoff_mode() == KirchhoffMode::node_angle
      && bus().needs_kirchhoff(opts.kirchhoff_threshold());
}

auto BusLP::lazy_add_theta(const SystemContext& sc,
                           const ScenarioLP& scenario,
                           const StageLP& stage,
                           LinearProblem& lp,
                           std::span<const BlockLP> blocks) const
    -> const BIndexHolder<ColIndex>&
{
  BIndexHolder<ColIndex> tblocks;
  map_reserve(tblocks, blocks.size());

  if (stage.is_active() && needs_kirchhoff(sc)) [[likely]] {
    std::ranges::for_each(blocks,
                          [&](const BlockLP& block)
                          {
                            const auto buid = block.uid();
                            const auto ctx = make_block_context(
                                scenario.uid(), stage.uid(), block.uid());

                            const auto& theta = reference_theta();
                            if (theta) [[unlikely]] {
                              tblocks[buid] = lp.add_col(SparseCol {
                                  .lowb = *theta,
                                  .uppb = *theta,
                                  .class_name = Element::class_name.full_name(),
                                  .variable_name = ThetaName,
                                  .variable_uid = uid(),
                                  .context = ctx,
                              });
                            } else [[likely]] {
                              // Adaptive theta bound = Σ_l (tmax_l ·
                              // x_τ_l) computed at planning-options
                              // build time by `auto_scale_theta` and
                              // stored on `model_options.theta_max`.
                              // Falls back to `2π` (`default_theta_max`)
                              // only when `auto_scale=false`.  This
                              // bound never artificially caps line
                              // flows below `tmax` — replaces the old
                              // hardcoded `±2π` which silently capped
                              // flow at `2π / x_τ` on networks with
                              // large reactance · capacity products
                              // (e.g. IEEE 9-bus, where the ±2π bound
                              // dropped the optimum from $7K to $55K
                              // by forcing artificial load-shedding).
                              const double theta_bound =
                                  sc.options().theta_max();
                              tblocks[buid] = lp.add_col(SparseCol {
                                  .lowb = -theta_bound,
                                  .uppb = +theta_bound,
                                  .class_name = Element::class_name.full_name(),
                                  .variable_name = ThetaName,
                                  .variable_uid = uid(),
                                  .context = ctx,
                              });
                            }
                          });
  }

  // Store the theta columns for this scenario and stage
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  return theta_cols[st_key] = std::move(tblocks);
}

bool BusLP::add_to_lp(const SystemContext& sc,
                      const ScenarioLP& scenario,
                      const StageLP& stage,
                      LinearProblem& lp)
{
  static constexpr auto ampl_name = Element::class_name.snake_case();

  // Theta columns are lazy-created by lines that need Kirchhoff; the
  // resolver retains a special-case fallback for `bus.theta`/`bus.angle`
  // via `lookup_theta_col`, so we do NOT register a per-block variable
  // here — `theta_cols` may still be empty at this point in the solve
  // sequence.  The element-name registration is hoisted into
  // `system_lp.cpp::register_all_ampl_element_names` (runs once per
  // SimulationLP via std::call_once from the SystemLP constructor).
  //
  // `theta_cols` is `mutable` and lazy — populated on first cache-miss
  // in `theta_cols_at` (called from `kirchhoff::add_line_kvl_rows` via
  // LineLP).  Erase any stale entry for this (scenario, stage) so the
  // lazy populator fires fresh on every add_to_lp.  Cheap insurance
  // against future re-flatten paths re-using BusLP across rebuilds.
  theta_cols.erase(std::tuple {scenario.uid(), stage.uid()});

  // F9: register filter metadata for sum(...) predicates.
  if (const auto& t = bus().type) {
    AmplElementMetadata metadata;
    metadata.emplace_back(TypeKey, *t);
    sc.register_ampl_element_metadata(ampl_name, uid(), std::move(metadata));
  }

  if (!is_active(stage)) {
    return true;
  }

  const auto& blocks = stage.blocks();

  BIndexHolder<RowIndex> brows;
  map_reserve(brows, blocks.size());

  // `allow_oversupply` relaxes the balance from `=` to `≥`: the row starts
  // with an unbounded upper (`greater_equal(0)`) so the demand's RHS shift
  // only raises the lower bound (`generation ≥ demand`).  DemandLP guards
  // its `uppb +=` against an already-unbounded upper, so the row stays `≥`.
  const bool oversupply = sc.options().allow_oversupply();

  std::ranges::for_each(blocks,
                        [&](const BlockLP& block)
                        {
                          SparseRow brow {
                              .class_name = Element::class_name.full_name(),
                              .constraint_name = BalanceName,
                              .variable_uid = uid(),
                              .context = make_block_context(
                                  scenario.uid(), stage.uid(), block.uid()),
                          };
                          if (oversupply) {
                            brow.greater_equal(0.0);
                          }
                          brows[block.uid()] = lp.add_row(std::move(brow));
                        });

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  balance_rows[st_key] = std::move(brows);

  if (sc.options().use_lindistflow()) {
    add_lindistflow(scenario, stage, lp);
  }

  return true;
}

void BusLP::add_lindistflow(const ScenarioLP& scenario,
                            const StageLP& stage,
                            LinearProblem& lp)
{
  // ── LinDistFlow: w = |V|² column and reactive balance per block ──
  //   reference bus:  w = reference_voltage²  (1 p.u. when only
  //                   reference_theta marks it as the slack)
  //   other buses:    vmin_pu² ≤ w ≤ vmax_pu²       (unset → [0, +∞))
  //   Q balance:      Σ q_inj − Σ q_load + shunt_q · w + Σ q_in − Σ q_out = 0
  const auto& b = bus();
  OptReal ref_v = b.reference_voltage;
  if (!ref_v && b.reference_theta.has_value()) {
    ref_v = 1.0;
  }
  double lowb = 0.0;
  double uppb = LinearProblem::DblMax;
  if (ref_v) {
    lowb = uppb = *ref_v * *ref_v;
  } else {
    if (b.vmin_pu) {
      lowb = *b.vmin_pu * *b.vmin_pu;
    }
    if (b.vmax_pu) {
      uppb = *b.vmax_pu * *b.vmax_pu;
    }
  }
  const double shunt = b.shunt_q.value_or(0.0);

  BIndexHolder<ColIndex> wcols;
  BIndexHolder<RowIndex> qrows;
  for (const auto& block : stage.blocks()) {
    const auto ctx =
        make_block_context(scenario.uid(), stage.uid(), block.uid());
    const auto wcol = lp.add_col(SparseCol {
        .lowb = lowb,
        .uppb = uppb,
        .class_name = Element::class_name.full_name(),
        .variable_name = VsqName,
        .variable_uid = uid(),
        .context = ctx,
    });
    SparseRow qrow {
        .class_name = Element::class_name.full_name(),
        .constraint_name = QBalanceName,
        .variable_uid = uid(),
        .context = ctx,
    };
    if (shunt != 0.0) {
      qrow[wcol] = shunt;
    }
    wcols[block.uid()] = wcol;
    qrows[block.uid()] = lp.add_row(std::move(qrow));
  }
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  vsq_cols[st_key] = std::move(wcols);
  qbalance_rows[st_key] = std::move(qrows);
}

bool BusLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  out.add_row_dual(cname, BalanceName, pid, balance_rows);

  // Physical = LP × scale_theta, auto-descaled by LinearInterface.
  // `theta:cost` is **not** emitted — reduced cost of a free
  // structural variable is zero by complementary slackness at LP
  // optimality, and no downstream tooling consumes
  // `Bus/theta_cost.*` (verified by grep across scripts/ /
  // guiservice/ / integration_test/, 2026-05-14).  Mirrors the
  // line.flow:cost / line.theta:dual drops in commit ceabc88c.
  out.add_col_sol(cname, ThetaName, pid, theta_cols);
  if (!vsq_cols.empty()) {
    out.add_col_sol(cname, VsqName, pid, vsq_cols);
    out.add_row_dual(cname, QBalanceName, pid, qbalance_rows);
  }

  return true;
}

}  // namespace gtopt
