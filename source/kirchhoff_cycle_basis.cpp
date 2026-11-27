// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial

#include <algorithm>
#include <cassert>
#include <cmath>
#include <map>
#include <numbers>
#include <optional>
#include <queue>
#include <ranges>
#include <unordered_set>
#include <utility>

#include <gtopt/bus_lp.hpp>
#include <gtopt/collection.hpp>
#include <gtopt/constraint_names.hpp>
#include <gtopt/kirchhoff_cycle_basis.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/utils.hpp>
#include <spdlog/spdlog.h>

namespace gtopt::kirchhoff::cycle_basis
{

namespace
{

/// Walk parent pointers from `start` up to (but not including) `stop`,
/// emitting each visited bus (except `start` itself when
/// `include_start = false`).  Used to materialise the
/// path-from-bus-to-LCA in the spanning tree.
[[nodiscard]] std::vector<std::size_t> walk_to_ancestor(
    std::size_t start,
    std::size_t stop,
    std::span<const std::optional<std::size_t>> parent_bus)
{
  std::vector<std::size_t> path;
  auto cur = start;
  while (cur != stop) {
    path.push_back(cur);
    const auto& p = parent_bus[cur];
    if (!p.has_value()) {
      // Should not happen if `stop` was found via `walk_up_collect`
      // — but guard against pathological inputs.
      break;
    }
    cur = *p;
  }
  return path;
}

}  // namespace

std::vector<Cycle> build_fundamental_cycles(std::size_t num_buses,
                                            std::span<const Edge> edges)
{
  if (num_buses == 0) {
    return {};
  }

  // 1. Build adjacency list.  For each bus, store (neighbor_bus,
  //    edge_index) pairs.  Self-loops are skipped — they cannot be
  //    part of a meaningful KVL cycle (θ_a − θ_a = 0 trivially).
  std::vector<std::vector<std::pair<std::size_t, std::size_t>>> adj(num_buses);
  for (std::size_t e = 0; e < edges.size(); ++e) {
    const auto& edge = edges[e];
    if (edge.bus_a == edge.bus_b) {
      continue;
    }
    assert(edge.bus_a < num_buses);
    assert(edge.bus_b < num_buses);
    adj[edge.bus_a].emplace_back(edge.bus_b, e);
    adj[edge.bus_b].emplace_back(edge.bus_a, e);
  }

  // 2. BFS spanning tree per island.  `parent_bus[v]` and
  //    `parent_edge[v]` are set when `v` is first discovered; they
  //    stay `nullopt` for the BFS root of each island.
  std::vector<std::optional<std::size_t>> parent_bus(num_buses);
  std::vector<std::optional<std::size_t>> parent_edge(num_buses);
  std::vector<bool> visited(num_buses, false);
  std::vector<bool> is_tree_edge(edges.size(), false);

  for (std::size_t root = 0; root < num_buses; ++root) {
    if (visited[root]) {
      continue;
    }
    visited[root] = true;
    std::queue<std::size_t> q;
    q.push(root);
    while (!q.empty()) {
      const auto u = q.front();
      q.pop();
      for (const auto& [v, e_idx] : adj[u]) {
        if (visited[v]) {
          continue;
        }
        visited[v] = true;
        parent_bus[v] = u;
        parent_edge[v] = e_idx;
        is_tree_edge[e_idx] = true;
        q.push(v);
      }
    }
  }

  // 3. For each non-tree edge, build the fundamental cycle:
  //    walk u → LCA, walk v → LCA (reversed), close with the edge.
  std::vector<Cycle> cycles;
  cycles.reserve(edges.size());  // rough upper bound

  for (std::size_t e = 0; e < edges.size(); ++e) {
    if (is_tree_edge[e]) {
      continue;
    }
    const auto& edge = edges[e];
    if (edge.bus_a == edge.bus_b) {
      continue;  // self-loop (already filtered, but defensive)
    }
    const auto u = edge.bus_a;
    const auto v = edge.bus_b;

    // Find LCA by collecting u's path-to-root, then walking v upward
    // until we hit a bus on u's path.
    std::unordered_set<std::size_t> u_ancestors;
    {
      auto cur = u;
      u_ancestors.insert(cur);
      while (true) {
        const auto& p = parent_bus[cur];
        if (!p.has_value()) {
          break;
        }
        cur = *p;
        u_ancestors.insert(cur);
      }
    }

    auto lca = v;
    while (!u_ancestors.contains(lca)) {
      const auto& p = parent_bus[lca];
      if (!p.has_value()) {
        // u and v are in different islands — should be impossible for
        // a non-tree edge whose endpoints are both visited by BFS.
        // Defensive: skip.
        break;
      }
      lca = *p;
    }
    if (!u_ancestors.contains(lca)) {
      continue;  // pathological — skip this edge
    }

    const auto u_to_lca = walk_to_ancestor(u, lca, parent_bus);
    const auto v_to_lca = walk_to_ancestor(v, lca, parent_bus);

    Cycle cycle;
    cycle.reserve(u_to_lca.size() + v_to_lca.size() + 1);

    // 3a. Walk u → LCA along tree edges.  Each edge is traversed
    //     FROM the "from-bus" TO its parent.  Sign: +1 if the edge's
    //     bus_a is the "from-bus" (i.e., we walk bus_a → bus_b),
    //     −1 otherwise.
    for (const auto from : u_to_lca) {
      const auto& edge_idx_opt = parent_edge[from];
      if (!edge_idx_opt.has_value()) {
        continue;
      }
      const auto e_idx = *edge_idx_opt;
      const int sign = (edges[e_idx].bus_a == from) ? +1 : -1;
      cycle.push_back({
          .line_index = edges[e_idx].line_index,
          .sign = sign,
      });
    }

    // 3b. Walk LCA → v (reverse of v's path-to-LCA).  Each edge is
    //     traversed FROM parent TO the "to-bus".  Sign: +1 if the
    //     edge's bus_a is the parent (we walk bus_a → bus_b),
    //     −1 otherwise.  Equivalent: +1 if edge.bus_a != to-bus.
    for (const auto to_bus : v_to_lca | std::views::reverse) {
      const auto& edge_idx_opt = parent_edge[to_bus];
      if (!edge_idx_opt.has_value()) {
        continue;
      }
      const auto e_idx = *edge_idx_opt;
      const int sign = (edges[e_idx].bus_a == to_bus) ? -1 : +1;
      cycle.push_back({
          .line_index = edges[e_idx].line_index,
          .sign = sign,
      });
    }

    // 3c. Closing edge: v → u (the non-tree edge `e`).  Sign: +1 if
    //     edge.bus_a == v (we walk bus_a → bus_b), −1 otherwise.
    const int closing_sign = (edge.bus_a == v) ? +1 : -1;
    cycle.push_back({.line_index = edge.line_index, .sign = closing_sign});

    cycles.push_back(std::move(cycle));
  }

  return cycles;
}

// ── LP integration: cycle-basis KVL row assembler ────────────────────

namespace
{

/// Per-line scalars resolved at (scenario, stage) granularity.  Cached
/// once at the start of `add_kvl_rows` so the inner cycle loop can
/// stamp coefficients without re-reading schedules per block.
struct ResolvedLine
{
  const LineLP* line;  ///< owner; used for flow-col lookup
  double x_tau;  ///< τ · X / V², row coefficient on signed flow (0: tie)
  double phi_rad;  ///< phase-shift contribution to RHS
  bool active;  ///< false ⇒ skip in cycle building
};

}  // namespace

std::size_t add_kvl_rows(const SystemContext& sc,
                         const ScenarioLP& scenario,
                         const StageLP& stage,
                         LinearProblem& lp,
                         const Collection<BusLP>& buses,
                         const Collection<LineLP>& lines,
                         const SwitchableLineLookup& switchable_lookup)
{
  const auto& line_elements = lines.elements();
  if (line_elements.empty()) {
    return 0;
  }

  const auto& blocks = stage.blocks();
  if (blocks.empty()) {
    return 0;
  }

  // ── Row-scale factor ───────────────────────────────────────────────
  // PyPSA hardcodes `× 1e5` in `define_kirchhoff_voltage_constraints`
  // to push per-unit-reactance coefficients off the LP solver's
  // numerical noise floor.  gtopt instead uses the adaptive
  // `scale_theta = 1 / (3 · median(|x_τ|))` (θ_LP = scale_theta · θ)
  // already computed by `PlanningLP::auto_scale_theta` for the
  // `node_angle` strategy:
  //
  //   row_scale = scale_theta   ≈   1 / (3 · median(|x_τ|))
  //
  // so cycle KVL coefficients land at `x_τ · row_scale` ≈ 1/3 for the
  // median line, matching the post-θ-col-scale magnitude of the
  // `node_angle` row (there the θ_LP coefficient is
  // 1 / scale_theta ≈ 3 · median(|x_τ|), against the x_τ flow terms).
  // Cross-mode coefficient consistency.
  const double scale_theta_value = sc.options().scale_theta();
  const double row_scale = (scale_theta_value > 0.0) ? scale_theta_value : 1.0;

  // ── Resolve per-line scalars + build the edge list ─────────────────
  std::vector<ResolvedLine> resolved;
  resolved.reserve(line_elements.size());

  std::vector<Edge> edges;
  edges.reserve(line_elements.size());

  for (std::size_t li = 0; li < line_elements.size(); ++li) {
    const auto& line = line_elements[li];
    ResolvedLine rl {
        .line = &line,
        .x_tau = 0.0,
        .phi_rad = 0.0,
        .active = false,
    };

    // A structural dead-end line (``line_direction_mode``) emits no
    // flow column at all: drop it from the KVL graph up front (it is a
    // bridge, so it is in no cycle) instead of re-deriving the basis
    // in every block.
    if (!line.is_active(stage) || line.is_loop()
        || line.effective_flow_direction(sc)
            == line_direction::Verdict::dead_end)
    {
      resolved.push_back(rl);
      continue;
    }

    // `param_reactance` is a member fn that already returns
    // `OptReal` (= reactance.at(stage.uid())).  No need to go through
    // `sc.stage_reactance` here — cycle_basis is only called when
    // use_kirchhoff is true, so the gate inside `stage_reactance`
    // is irrelevant.
    if (!sc.options().use_kirchhoff()) {
      resolved.push_back(rl);
      continue;
    }
    // DC links (reactance unset, or 0 / sub-threshold under
    // `tiny_reactance_mode = dc`) are not KVL edges.  A zero-impedance
    // tie (0 / sub-threshold under `tiny_reactance_mode = tie`) IS an edge,
    // with `x = 0`: it closes loops and contributes only its phase
    // shift φ to the cycle RHS (τ multiplies x and is irrelevant).
    const auto x_opt = line.param_reactance(stage.uid());
    const double V = line.param_voltage(stage.uid()).value_or(1.0);
    const auto kind = sc.options().classify_line_reactance(x_opt, V);
    if (kind == LineKvlKind::dc) {
      resolved.push_back(rl);
      continue;
    }
    const double tau = line.param_tap_ratio(stage.uid()).value_or(1.0);
    const double x_tau =
        (kind == LineKvlKind::tie) ? 0.0 : tau * x_opt.value_or(0.0) / (V * V);
    if (kind == LineKvlKind::ac && x_tau == 0.0) {
      resolved.push_back(rl);
      continue;
    }
    const double phi_deg =
        line.param_phase_shift_deg(stage.uid()).value_or(0.0);
    const double phi_rad = phi_deg * std::numbers::pi / 180.0;

    rl.x_tau = x_tau;
    rl.phi_rad = phi_rad;
    rl.active = true;

    // Resolve bus indices via Collection's uid/name maps.  Skipped
    // (line excluded from cycle topology) if either bus is unknown.
    try {
      const auto idx_a =
          static_cast<std::size_t>(buses.element_index(line.bus_a_sid()));
      const auto idx_b =
          static_cast<std::size_t>(buses.element_index(line.bus_b_sid()));
      edges.push_back(Edge {.line_index = li, .bus_a = idx_a, .bus_b = idx_b});
    } catch (const std::out_of_range&) {
      rl.active = false;
    }

    resolved.push_back(rl);
  }

  if (edges.empty()) {
    return 0;
  }

  // ── Build fundamental cycles ───────────────────────────────────────
  const auto cycles = build_fundamental_cycles(buses.elements().size(), edges);
  if (cycles.empty()) {
    return 0;
  }

  // ── Emit one KVL row per cycle per block ───────────────────────────
  // Pre-resolve `param_reactance` accessors are not invariant per
  // block, but `x_tau` IS (per stage), so the inner loop only varies
  // the per-block flow-col references.
  static constexpr std::string_view class_name = kirchhoff_class_name;
  static constexpr std::string_view constraint_name =
      kirchhoff_cycle_constraint_name;
  static constexpr std::string_view constraint_name_upper =
      kirchhoff_cycle_constraint_name;  // upper half of disjunctive pair
  static constexpr std::string_view constraint_name_lower = "kvl_minus";

  // ── Big-M for the OTS disjunctive form ─────────────────────────────
  // `M_C = 2·θ_max · |C| · row_scale + Σ_{e ∈ C} |φ_e| · row_scale`
  // (Fisher 2008 baseline refined for phase shifts).  Bounds the
  // maximum magnitude of `LHS − RHS_eq` over feasible flows when every
  // line in the cycle is in-service, so collapsing `Σ (1 − u_l) · M_C`
  // to zero (all `u_l = 1`) recovers the original equality, and any
  // `u_l = 0` slacks the cycle by exactly `M_C`.
  //
  // We use `theta_max` from the planning options (auto-set by
  // `PlanningLP::auto_scale_theta` to `Σ_l |x_τ_l|·tmax_l`).  Per-line
  // `LineCommitment.kvl_big_m` overrides are NOT consulted here — the
  // cycle-form big-M is a sum-of-edges bound, not a per-edge value.
  const double theta_max = sc.options().theta_max();

  std::size_t total_rows = 0;

  // Big-M of the OTS disjunctive form for one cycle (block-invariant:
  // depends only on the cycle's edges' `x_tau`s and `phi_rad`s, both
  // stage-resolved).
  const auto cycle_big_m = [&](const Cycle& cycle)
  {
    double sum_abs_phi = 0.0;
    for (const auto& ce : cycle) {
      const auto& rl = resolved[ce.line_index];
      sum_abs_phi += std::abs(rl.phi_rad);
    }
    return (2.0 * theta_max * static_cast<double>(cycle.size()) * row_scale)
        + (sum_abs_phi * row_scale);
  };

  // Emit the KVL row(s) of `cycle` (basis index `cycle_idx`) at `block`.
  // Every edge of `cycle` must have a flow column at the block.
  const auto emit_cycle_block = [&](const Cycle& cycle,
                                    std::size_t cycle_idx,
                                    double M_C,
                                    const auto& block)
  {
    const auto buid = block.uid();

    // ── Collect switchable lines on this cycle (OTS disjunctive
    //    form is needed iff at least one u_l column exists).
    std::vector<ColIndex> u_cols;
    if (switchable_lookup) {
      u_cols.reserve(cycle.size());
      for (const auto& ce : cycle) {
        if (auto sw = switchable_lookup(ce.line_index, buid)) {
          u_cols.push_back(sw->u_col);
        }
      }
    }
    const bool disjunctive = !u_cols.empty();

    double rhs = 0.0;
    // Build a "core" sparse map of flow coefficients first (shared
    // between upper and lower rows when disjunctive).  `flat_map` has
    // no `reserve`; the inserts amortise.
    SparseRow::cmap_t flow_coeffs;

    for (const auto& ce : cycle) {
      const auto& rl = resolved[ce.line_index];
      if (!rl.active) {
        continue;  // defensive — should not happen for cycle members
      }
      const double coef_base = ce.sign * rl.x_tau * row_scale;
      rhs += ce.sign * rl.phi_rad * row_scale;
      if (coef_base == 0.0) {
        continue;  // zero-impedance tie: no flow term, only φ
      }

      // Stamp flow cols.  In `piecewise_direct` line-loss mode the
      // flowp / flown aggregators are absent and segments stamp
      // directly with the same per-edge coefficient.
      const auto& fpc = rl.line->flowp_cols_at(scenario, stage);
      if (auto it = fpc.find(buid); it != fpc.end()) {
        flow_coeffs[it->second] += coef_base;
      }
      const auto& fnc = rl.line->flown_cols_at(scenario, stage);
      if (auto it = fnc.find(buid); it != fnc.end()) {
        flow_coeffs[it->second] -= coef_base;
      }
      const auto& fpsegc = rl.line->flowp_seg_cols_at(scenario, stage);
      if (auto it = fpsegc.find(buid); it != fpsegc.end()) {
        for (const auto& col : it->second) {
          flow_coeffs[col] += coef_base;
        }
      }
      const auto& fnsegc = rl.line->flown_seg_cols_at(scenario, stage);
      if (auto it = fnsegc.find(buid); it != fnsegc.end()) {
        for (const auto& col : it->second) {
          flow_coeffs[col] -= coef_base;
        }
      }
      // ``tangent_signed_flow`` mode: single signed flow column carries
      // its own sign, so use a single ``+`` stamp.
      const auto& fsc = rl.line->flows_cols_at(scenario, stage);
      if (auto it = fsc.find(buid); it != fsc.end()) {
        flow_coeffs[it->second] += coef_base;
      }
    }

    if (!disjunctive && flow_coeffs.empty()) {
      // Cycle made only of zero-impedance ties: the row degenerates to
      // `0 = Σ ε·φ`.  Consistent (Σ ε·φ = 0) ⇒ redundant; otherwise the
      // tie loop imposes contradictory phase shifts and the row would
      // make the LP infeasible — drop it either way, warn once per
      // (cycle, stage) in the inconsistent case.
      if (std::abs(rhs) > 1e-12 && block.uid() == blocks.front().uid()) {
        spdlog::warn(
            "kirchhoff::cycle_basis: cycle {} consists only of "
            "zero-impedance ties with net phase shift {:.6g} rad ≠ 0 "
            "(scenario={}, stage={}) — KVL row dropped",
            cycle_idx,
            rhs / row_scale,
            scenario.uid(),
            stage.uid());
      }
      return;
    }

    if (!disjunctive) {
      // ── Standard equality row (no OTS in this cycle) ──
      SparseRow row {
          .cmap = std::move(flow_coeffs),
          .class_name = class_name,
          .constraint_name = constraint_name,
          .variable_uid = Uid {static_cast<int>(cycle_idx)},
          .context =
              make_block_context(scenario.uid(), stage.uid(), block.uid()),
      };
      row.equal(-rhs);
      [[maybe_unused]] const auto row_idx = lp.add_row(std::move(row));
      ++total_rows;
      return;
    }

    // ── OTS disjunctive form ──
    //   −Σ M_C · (1 − u_l)  ≤  LHS − (−rhs)  ≤  +Σ M_C · (1 − u_l)
    //
    // Expanded with `u_l` coefficients on the LHS:
    //   LHS + Σ M_C · u_l  ≤  −rhs + |sw| · M_C        (upper)
    //   LHS − Σ M_C · u_l  ≥  −rhs − |sw| · M_C        (lower)
    const double slack = M_C * static_cast<double>(u_cols.size());

    // Upper-side row: LHS + Σ M_C · u_l  ≤  −rhs + slack
    {
      SparseRow::cmap_t upper_coeffs = flow_coeffs;  // copy
      for (const auto col : u_cols) {
        upper_coeffs[col] += M_C;
      }
      SparseRow upper {
          .cmap = std::move(upper_coeffs),
          .class_name = class_name,
          .constraint_name = constraint_name_upper,
          .variable_uid = Uid {static_cast<int>(cycle_idx)},
          .context =
              make_block_context(scenario.uid(), stage.uid(), block.uid()),
      };
      upper.less_equal(-rhs + slack);
      [[maybe_unused]] const auto upper_idx = lp.add_row(std::move(upper));
      ++total_rows;
    }
    // Lower-side row: LHS − Σ M_C · u_l  ≥  −rhs − slack
    {
      SparseRow::cmap_t lower_coeffs = std::move(flow_coeffs);
      for (const auto col : u_cols) {
        lower_coeffs[col] -= M_C;
      }
      SparseRow lower {
          .cmap = std::move(lower_coeffs),
          .class_name = class_name,
          .constraint_name = constraint_name_lower,
          .variable_uid = Uid {static_cast<int>(cycle_idx)},
          .context =
              make_block_context(scenario.uid(), stage.uid(), block.uid()),
      };
      lower.greater_equal(-rhs - slack);
      [[maybe_unused]] const auto lower_idx = lp.add_row(std::move(lower));
      ++total_rows;
    }
  };

  // ── Per-block out-of-service edges ─────────────────────────────────
  // A line active in the stage but without a flow column at a block
  // (``Line.in_service`` = 0 for an intra-stage maintenance window) is
  // an open branch there: KVL does not apply around any loop through it.
  const auto edge_in_service = [&](const Edge& edge, BlockUid buid)
  {
    const auto* line = resolved[edge.line_index].line;
    return line->flowp_cols_at(scenario, stage).contains(buid)
        || line->flown_cols_at(scenario, stage).contains(buid)
        || line->flowp_seg_cols_at(scenario, stage).contains(buid)
        || line->flown_seg_cols_at(scenario, stage).contains(buid)
        || line->flows_cols_at(scenario, stage).contains(buid);
  };
  std::vector<std::vector<std::size_t>> block_out(blocks.size());
  for (std::size_t bi = 0; bi < blocks.size(); ++bi) {
    for (std::size_t e = 0; e < edges.size(); ++e) {
      if (!edge_in_service(edges[e], blocks[bi].uid())) {
        block_out[bi].push_back(e);
      }
    }
  }

  // Blocks with every edge in service: the stage's fundamental basis.
  std::size_t cycle_idx = 0;
  for (const auto& cycle : cycles) {
    const double M_C = cycle_big_m(cycle);
    for (std::size_t bi = 0; bi < blocks.size(); ++bi) {
      if (block_out[bi].empty()) {
        emit_cycle_block(cycle, cycle_idx, M_C, blocks[bi]);
      }
    }
    ++cycle_idx;
  }

  // Blocks with an outage: rebuild the fundamental basis on the surviving
  // edges, once per distinct outage pattern.  Dropping only the stage
  // cycles that contain the open line would also drop loops that survive
  // it (two cycles sharing the open line combine into one that avoids
  // it), leaving the network under-constrained against `node_angle`.
  std::map<std::vector<std::size_t>, std::vector<std::size_t>> outage_groups;
  for (std::size_t bi = 0; bi < blocks.size(); ++bi) {
    if (!block_out[bi].empty()) {
      outage_groups[block_out[bi]].push_back(bi);
    }
  }
  for (const auto& [out, group] : outage_groups) {
    std::vector<Edge> live;
    live.reserve(edges.size() - out.size());
    for (std::size_t e = 0; e < edges.size(); ++e) {
      if (!std::ranges::binary_search(out, e)) {
        live.push_back(edges[e]);
      }
    }
    const auto live_cycles =
        build_fundamental_cycles(buses.elements().size(), live);
    std::size_t live_idx = 0;
    for (const auto& cycle : live_cycles) {
      const double M_C = cycle_big_m(cycle);
      for (const auto bi : group) {
        emit_cycle_block(cycle, live_idx, M_C, blocks[bi]);
      }
      ++live_idx;
    }
  }

  spdlog::debug(
      "kirchhoff::cycle_basis: emitted {} KVL row(s) "
      "({} cycle(s) × {} block(s), scenario={}, stage={}, row_scale={:.6g})",
      total_rows,
      cycles.size(),
      blocks.size(),
      scenario.uid(),
      stage.uid(),
      row_scale);

  return total_rows;
}

}  // namespace gtopt::kirchhoff::cycle_basis
