/**
 * @file      commitment_lp.cpp
 * @brief     LP formulation for unit commitment (three-bin u/v/w model)
 * @date      Tue Apr  8 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Implements CommitmentLP: creates binary status/startup/shutdown variables,
 * adds the three core UC constraints (logic, gen limits, exclusion),
 * ramp / startup-shutdown envelopes and startup costs, and retro-fits the
 * piecewise heat-rate kink rows with the `u` gating term.  Emission
 * pricing and caps are NOT handled here — see `EmissionSourceLP` /
 * `EmissionZoneLP`.
 */

#include <algorithm>
#include <cmath>
#include <format>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include <gtopt/cell_partition.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/commitment_power_based.hpp>
#include <gtopt/fuel_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/pricing_mode.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/transition_integrality.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

CommitmentLP::CommitmentLP(const Commitment& commitment, const InputContext& ic)
    : Base(commitment, ic, Element::class_name)
    , generator_index_(ic.element_index(generator_sid()))
    , startup_cost_(
          ic, Element::class_name, id(), std::move(object().startup_cost))
    , shutdown_cost_(
          ic, Element::class_name, id(), std::move(object().shutdown_cost))
    , fixed_status_(
          ic, Element::class_name, id(), std::move(object().fixed_status))
    , pmin_(ic, Element::class_name, id(), std::move(object().pmin))
    , ramp_up_(ic, Element::class_name, id(), std::move(object().ramp_up))
    , ramp_down_(ic, Element::class_name, id(), std::move(object().ramp_down))
    , startup_ramp_(
          ic, Element::class_name, id(), std::move(object().startup_ramp))
    , shutdown_ramp_(
          ic, Element::class_name, id(), std::move(object().shutdown_ramp))
    , startup_wear_cost_(
          ic, Element::class_name, id(), std::move(object().startup_wear_cost))
    , wear_hour_cost_(
          ic, Element::class_name, id(), std::move(object().wear_hour_cost))
    , wear_load_cost_(
          ic, Element::class_name, id(), std::move(object().wear_load_cost))
{
}

bool CommitmentLP::has_chronological_data() const noexcept
{
  const auto& c = commitment();
  return startup_cost_.has_value() || shutdown_cost_.has_value()
      || startup_wear_cost_.has_value() || ramp_up_.has_value()
      || ramp_down_.has_value() || startup_ramp_.has_value()
      || shutdown_ramp_.has_value() || c.min_up_time.has_value()
      || c.min_down_time.has_value() || c.hot_start_cost.has_value()
      || c.warm_start_cost.has_value() || c.cold_start_cost.has_value()
      || c.max_starts.has_value() || c.min_starts.has_value()
      || c.commitment_period.has_value();
}

namespace
{
/// Position of the stage with uid @p suid in the simulation stage list.
template<typename Stages, typename SUid>
[[nodiscard]] std::size_t stage_position(const Stages& stages, SUid suid)
{
  std::size_t pos = 0;
  for (const auto& st : stages) {
    if (st.uid() == suid) {
      break;
    }
    ++pos;
  }
  return pos;
}
}  // namespace

std::string_view CommitmentLP::setup_long_transition(
    SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp,
    double cycle_len,
    std::span<const std::pair<std::string_view, double>> long_list)
{
  if (long_list.empty()) {
    return {};
  }
  const auto& stages = sc.simulation().stages();
  const auto long_mode = commitment().long_transition_at(
      stage_position(stages, stage.uid()), sc.options().long_transition());
  if (long_mode.empty()) {
    return {};
  }
  if (!m_long_warned_) {
    m_long_warned_ = true;
    std::string dropped;
    for (const auto& [what, hours] : long_list) {
      dropped +=
          std::format("{}{} = {} h", dropped.empty() ? "" : ", ", what, hours);
    }
    spdlog::warn(
        "Commitment {}: long_transition '{}' — dropped {} (> typical "
        "period {} h)",
        commitment().name,
        long_mode,
        dropped,
        cycle_len);
  }
  // Status change between consecutive stages declared on / off: one
  // start (off → on) or shutdown (on → off) per transition, × 1 (not ×
  // the stage weight), as a fixed column carrying the constant cost.
  const auto& blocks = stage.blocks();
  const auto [prev_stage, prev_phase] = sc.simulation().prev_stage(stage);
  if ((long_mode != "on" && long_mode != "off") || blocks.empty()
      || prev_stage == nullptr)
  {
    return long_mode;
  }
  const auto prev_mode =
      commitment().long_transition_at(stage_position(stages, prev_stage->uid()),
                                      sc.options().long_transition());
  const bool start = prev_mode == "off" && long_mode == "on";
  const bool stop = prev_mode == "on" && long_mode == "off";
  if (!start && !stop) {
    return long_mode;
  }
  const auto rep_buid = blocks.front().uid();
  const auto cost = (start ? startup_cost_ : shutdown_cost_)
                        .optval(stage.uid(), rep_buid)
                        .value_or(0.0)
      * CostHelper::cost_factor(scenario.probability_factor(),
                                stage.discount_factor());
  if (cost != 0.0) {
    std::ignore = lp.add_col({
        .lowb = 1.0,
        .uppb = 1.0,
        .cost = cost,
        .class_name = Element::class_name.full_name(),
        .variable_name = start ? StartupTransitionName : ShutdownTransitionName,
        .variable_uid = uid(),
        .context = make_block_context(scenario.uid(), stage.uid(), rep_buid),
    });
  }
  return long_mode;
}

namespace
{

/// Commitment periods of one stage: the first block of every period,
/// the period of every block, the block uids of every period and the
/// real hours each period covers.  See CommitmentLP::add_to_lp.
struct PeriodLayout
{
  std::vector<size_t> starts;
  std::vector<size_t> block_period;
  std::vector<std::vector<BlockUid>> block_uids;
  std::vector<double> real;
  bool explicit_cells {false};
  /// cyclic daily grid: the first and the last period are the two
  /// pieces of the cell straddling the period start (one status)
  bool wrap_merged {false};
};

/// Node-time commitment grid of one stage: the cell boundaries in stage
/// time (cells [0, τ_1), [τ_1, τ_2), ..., [τ_K, T]) and the tolerance of
/// the half-open node → cell rule.  Empty `bounds` with `active` = one
/// cell for the whole stage.
struct NodeGrid
{
  bool active {false};
  std::vector<double> bounds;
  double tol {1e-9};
  bool wraps {false};  ///< first and last cell are one daily cell
};

template<typename Blocks>
[[nodiscard]] PeriodLayout commitment_period_layout(
    const Blocks& blocks,
    size_t ncyc,
    bool point,
    const NodeGrid& grid,
    double period_hours,
    std::span<const double> node_t,
    std::span<const double> step,
    std::span<const double> cyc_step,
    double cycle_len)
{
  // Period grouping: each entry is the index of the first block in a period.
  // period_starts[k] = index into blocks[] where period k begins.
  // A final sentinel equal to blocks.size() marks the end.
  PeriodLayout out;
  auto& period_starts = out.starts;
  period_starts.push_back(0);
  // Explicit binary grid on block stages: blocks labelled with
  // `commitment_cell` share the status of their cell (refined samplers
  // add continuous blocks only).  A unit's own `commitment_period`
  // (its uniform overlay grid) or a daily grid takes precedence.
  out.explicit_cells = !point && period_hours <= 0.0 && !grid.active
      && std::ranges::any_of(std::span(blocks).first(ncyc),
                             [](const auto& b)
                             { return b.commitment_cell().has_value(); });
  // Node-time grids (point stages: the uniform grid ⌊t_k / P⌋; any
  // stage: the daily `commitment_breaks` grid): node k → the cell
  // holding t_k.  A node exactly on a cell boundary belongs to the
  // LATER cell (its status is the one that starts there); a node at the
  // period end t = T joins the last cell.  Cells holding no node carry
  // no status of their own: the previous cell's status continues over
  // them (starts / stops only happen at a boundary of a cell that holds
  // a node).
  const auto node_cell = [&](size_t i)
  { return cell_of_time(grid.bounds, node_t[i], grid.tol); };
  const auto cell_start = [&](size_t c)
  { return c == 0 ? 0.0 : grid.bounds[c - 1]; };
  if (grid.active) {
    for (size_t i = 1; i < ncyc; ++i) {
      if (node_cell(i) != node_cell(i - 1)) {
        period_starts.push_back(i);
      }
    }
  } else if (out.explicit_cells) {
    for (size_t i = 1; i < ncyc; ++i) {
      if (blocks[i].commitment_cell() != blocks[i - 1].commitment_cell()) {
        period_starts.push_back(i);
      }
    }
  } else if (period_hours > 0.0) {
    // Periods are unions of whole cells (block stages: the blocks; point
    // stages: the quadrature cells): cell k joins period ⌊mid_k / P⌋ —
    // a nominal boundary inside a cell snaps to its nearer end, so the
    // period duration is exactly the sum of its cell lengths.  See
    // cell_partition.hpp.
    for (const auto j : snap_period_boundaries(cyc_step, period_hours, point)) {
      period_starts.push_back(j + 1);
    }
  } else {
    // One period per block
    for (size_t i = 1; i < ncyc; ++i) {
      period_starts.push_back(i);
    }
  }
  const auto nperiods = period_starts.size();

  // Map every block index → its period index, AND materialise per-period
  // block-uid vectors used by the IntegerVariable registry fan-out
  // (consumed by both the u/v/w loop below and the startup-tier loop).
  auto& block_period = out.block_period;
  auto& period_block_uids = out.block_uids;
  block_period.assign(blocks.size(), 0);
  period_block_uids.resize(nperiods);
  for (size_t p = 0; p < nperiods; ++p) {
    const auto start = period_starts[p];
    const auto end = (p + 1 < nperiods) ? period_starts[p + 1] : ncyc;
    period_block_uids[p].reserve(end - start);
    for (size_t i = start; i < end; ++i) {
      block_period[i] = p;
      period_block_uids[p].push_back(blocks[i].uid());
    }
  }
  // Cell lengths of every period (real hours) — the durations counted
  // by min up/down, start tiers and start windows.
  auto& period_real = out.real;
  period_real.assign(nperiods, 0.0);
  if (grid.active) {
    // the cells of the period (up to the next cell with a node)
    for (size_t p = 0; p < nperiods; ++p) {
      const double t0 =
          (p == 0) ? 0.0 : cell_start(node_cell(period_starts[p]));
      const double t1 = (p + 1 < nperiods)
          ? cell_start(node_cell(period_starts[p + 1]))
          : cycle_len;
      period_real[p] = t1 - t0;
    }
    // The cell straddling the start of a cyclic daily period: its two
    // pieces (first and last period) are one cell when both hold a node.
    out.wrap_merged = grid.wraps && nperiods >= 2 && ncyc >= 2
        && node_cell(0) == 0 && node_cell(ncyc - 1) == grid.bounds.size();
  } else {
    for (size_t i = 0; i < blocks.size(); ++i) {
      period_real[block_period[i]] += step[i];
    }
  }
  return out;
}

/// Bounds of the status column u of one period.  Precedence (highest →
/// lowest):
///   0. a declared long-transition status (@p long_mode "on" / "off")
///      pins u to 1 / 0 — it wins over the schedule and must_run.
///   1. ``fixed_status`` schedule entry (@p pin) for this (stage, block) —
///      if set to a value in ``[0, 1]``, pins ``u`` to that value
///      (rounded at 0.5 to 0 or 1).  Values *outside* ``[0, 1]`` are
///      reserved as the "no-pin" sentinel so the schedule can pin some
///      blocks and leave others free without juggling a proper nullable
///      cell type (UC.jl's ``Commitment status`` arrays can contain
///      ``null`` for un-pinned blocks; the converter emits ``-1.0``
///      there).
///   2. ``must_run`` flag — forces ``u >= 1`` for the whole stage.
///   3. Default — ``u`` in ``[0, 1]``.
[[nodiscard]] std::pair<double, double> status_bounds(
    std::optional<double> pin, bool must_run, std::string_view long_mode)
{
  if (long_mode == "on") {
    return {1.0, 1.0};
  }
  if (long_mode == "off") {
    return {0.0, 0.0};
  }
  if (pin && *pin >= 0.0 && *pin <= 1.0) {
    const auto pinned = (*pin >= 0.5) ? 1.0 : 0.0;
    return {pinned, pinned};
  }
  return {must_run ? 1.0 : 0.0, 1.0};
}

/// Node-time grid of one stage: the daily `commitment_breaks` grid
/// (@p daily_breaks non-empty; @p start_hour = clock time of the stage
/// start), else — on point stages with the default grid
/// (@p uniform_point) — the uniform grid of @p period_hours cells.
/// Inactive otherwise.
[[nodiscard]] NodeGrid make_node_grid(std::span<const Real> daily_breaks,
                                      double start_hour,
                                      double cycle_len,
                                      bool cyclic,
                                      bool uniform_point,
                                      double period_hours)
{
  NodeGrid grid;
  if (!daily_breaks.empty()) {
    grid.active = true;
    grid.bounds = daily_cell_bounds(daily_breaks, start_hour, cycle_len);
    grid.wraps =
        cyclic && daily_cells_wrap(daily_breaks, start_hour, cycle_len);
  } else if (uniform_point && period_hours > 0.0) {
    const auto ncells = std::max<long>(
        1, static_cast<long>(std::ceil((cycle_len / period_hours) - 1e-9)));
    grid.active = true;
    grid.tol = 1e-9 * period_hours;
    grid.bounds.reserve(static_cast<size_t>(ncells - 1));
    for (long c = 1; c < ncells; ++c) {
      grid.bounds.push_back(static_cast<double>(c) * period_hours);
    }
  }
  return grid;
}

/// Length of the unit's commitment periods on one stage [h]; 0 = one
/// period per block.  Point stages never put a status on the nodes: the
/// commitment lives on a UNIFORM grid of P = max(commitment_period, 1 h)
/// cells (default 1 h) laid over the stage period, independent of the
/// node set; every node inherits the status of the cell containing its
/// time t_k.
template<typename Blocks>
[[nodiscard]] double resolve_period_hours(const Commitment& uc,
                                          const Blocks& blocks,
                                          std::span<const double> cyc_step,
                                          bool chronological,
                                          bool point,
                                          bool power_based)
{
  if (!chronological) {
    return 0.0;
  }
  // Smallest cell of the stage: the resolution floor of
  // `commitment_period = "auto"`.
  double resolution = 0.0;
  for (const double dt : cyc_step) {
    if (dt > 0.0 && (resolution == 0.0 || dt < resolution)) {
      resolution = dt;
    }
  }
  const double h = uc.commitment_period_hours(resolution);
  if (point && uc.uses_legacy_point_grid()) {
    // previous behaviour: per node ("block") or quadrature cells
    return h > 0.0 ? std::max(h, 1.0) : h;
  }
  // Power-based units keep their binaries on a commitment grid of at
  // least 1 h: sub-hourly blocks refine the power points only.  An
  // explicit `commitment_grid` ("block": one status per block) and
  // blocks labelled with `commitment_cell` keep their own grid.
  if (power_based && !uc.commitment_grid.has_value() && h <= 0.0
      && resolution > 0.0 && resolution < 1.0 - 1e-9
      && std::ranges::none_of(
          blocks,
          [](const auto& b) { return b.commitment_cell().has_value(); }))
  {
    return 1.0;
  }
  return point ? std::max(h, 1.0) : h;
}

}  // namespace

double CommitmentLP::resolve_start_wear(const ScenarioLP& scenario,
                                        const StageLP& stage,
                                        BlockUid rep_buid,
                                        BIndexHolder<double>& coefs) const
{
  // Wear part of a start (`startup_wear_cost`, default off): a separate
  // $/start adder on v, kept apart from the fuel / energy start cost (and
  // from the hot / warm / cold tiers, which carry their own columns) so
  // the outputs can split it.
  const auto start_wear =
      startup_wear_cost_.optval(stage.uid(), rep_buid).value_or(0.0);
  if (!std::isfinite(start_wear) || start_wear < 0.0) {
    fail_lp(*this,
            scenario,
            stage,
            {
                .field = "startup_wear_cost",
                .detail = std::format(
                    "block {}: startup_wear_cost = {} must be finite and >= 0",
                    rep_buid,
                    start_wear),
            });
  }
  if (start_wear != 0.0) {
    coefs[rep_buid] = start_wear;
  }
  return start_wear;
}

void CommitmentLP::add_operation_wear(LinearProblem& lp,
                                      const ScenarioLP& scenario,
                                      const StageLP& stage,
                                      const BlockLP& block,
                                      const WearBlock& wb,
                                      BIndexHolder<double>& wear_a,
                                      BIndexHolder<double>& wear_b,
                                      BIndexHolder<double>& wear_pmin) const
{
  // Operation wear (`wear_hour_cost` a, `wear_load_cost` b): cost
  // a·u + b·(p − Pmin·u) per hour, i.e. coefficient (a − b·Pmin) on u
  // and b on p.  Linear, so it holds for a relaxed u and with an
  // expanded capacity.  b may be negative (low-load penalty); the hourly
  // wear must stay >= 0 on [Pmin, Pmax]: a >= 0 and a + b·(Pmax − Pmin)
  // >= 0.
  const auto buid = block.uid();
  const auto wa = wear_hour_cost_.optval(stage.uid(), buid);
  const auto wbv = wear_load_cost_.optval(stage.uid(), buid);
  if (!wa && !wbv) {
    return;
  }
  const double a = wa.value_or(0.0);
  const double b = wbv.value_or(0.0);
  const double span = wb.pmax - wb.pmin;
  const bool bad_a = !std::isfinite(a) || a < 0.0;
  const bool bad_b = !std::isfinite(b)
      || (b < 0.0 && (!std::isfinite(span) || a + (b * span) < 0.0));
  if (bad_a || bad_b) {
    fail_lp(*this,
            scenario,
            stage,
            {
                .field = bad_a ? "wear_hour_cost" : "wear_load_cost",
                .detail = std::format(
                    "block {}: operation wear a = {}, b = {} must give "
                    "a >= 0 and a + b·(Pmax − Pmin) = {} >= 0 "
                    "(Pmin = {}, Pmax = {})",
                    buid,
                    a,
                    b,
                    a + (b * span),
                    wb.pmin,
                    wb.pmax),
            });
  }
  if (a == 0.0 && b == 0.0) {
    return;
  }
  lp.col_at(wb.ucol).cost +=
      CostHelper::block_ecost(scenario, stage, block, a - (b * wb.pmin));
  lp.col_at(wb.gcol).cost += CostHelper::block_ecost(scenario, stage, block, b);
  wear_a[buid] = a;
  wear_b[buid] = b;
  wear_pmin[buid] = wb.pmin;
}

bool CommitmentLP::add_to_lp(SystemContext& sc,
                             const ScenarioLP& scenario,
                             const StageLP& stage,
                             LinearProblem& lp)
{
  if (!is_active(stage)) {
    return true;
  }

  // The formulation follows the stage.  A chronological stage gets the
  // full intertemporal model (logic u − u₋₁ − v + w, startup/shutdown,
  // ramps, min up/down, start tiers and counts).  A non-chronological
  // (block / representative) stage has no block order, so it gets the
  // block formulation: one status u per block with only the C2 rows
  // ``g ≤ Pmax·u`` and ``g ≥ Commitment.pmin·u`` (same pmin as the full
  // model); startup / shutdown / no-load / ramp / min up-down /
  // commitment_period data is ignored there.  Reserve gating, inertia
  // and PAMPL accessors are shared by both.
  const bool chronological = stage.is_chronological();

  auto&& generator_lp = sc.element(generator_index_);
  if (!generator_lp.is_active(stage)) {
    return true;
  }

  // Tolerant lookup: same rationale as the patch at
  // ``source/reserve_provision_lp.cpp:264`` — when every block of
  // ``(scenario, stage)`` was elided by the P1 zero-pmax optimization
  // (all-zero pmax_profile, e.g. PLEXOS-must-OFF units forced via
  // ``--use-plexos-gen-cap``), the outer ``generation_cols`` key is
  // absent and ``generation_cols_at`` would throw.
  // ``lookup_generation_cols`` returns an empty inner holder; the
  // per-block ``generation_cols.find(buid)`` checks below safely
  // iterate zero times and no commitment row is emitted for blocks
  // without a dispatchable gen column.
  const auto& generation_cols =
      generator_lp.lookup_generation_cols(scenario, stage);
  const auto& blocks = stage.blocks();

  // Time structure.  Every stage is a partition into cells of length
  // `step[k]` = the block's real duration (on `Stage.sampling = "point"`
  // stages: the Clenshaw–Curtis weight w_k, cell [τ_{k−1}, τ_k] with
  // τ_k = Σ_{j≤k} w_j; each node t_k lies inside its cell).  All
  // duration logic — commitment periods, min up/down and start windows —
  // counts these cell lengths, so it agrees with the energy / cost
  // quadrature.  Node times only enter the ramps (a derivative of the
  // continuous function): `back_step[k]` = t_k − t_{k−1} (t_1 − t_0 at the
  // first node) on point stages, the block length otherwise.
  const bool point = stage.point_sampling();
  std::vector<double> step(blocks.size(), 0.0);
  std::vector<double> back_step(blocks.size(), 0.0);
  std::vector<double> node_t(blocks.size());
  double cycle_len = 0.0;  ///< real length of the stage period [h]
  {
    double clock = 0.0;
    for (size_t i = 0; i < blocks.size(); ++i) {
      const double real_dt = stage.real_duration(blocks[i]);
      node_t[i] = blocks[i].time().value_or(clock + (0.5 * real_dt));
      clock += real_dt;
      step[i] = real_dt;
      back_step[i] = real_dt;
    }
    if (point && blocks.size() >= 2) {
      for (size_t i = 0; i < blocks.size(); ++i) {
        back_step[i] =
            i > 0 ? node_t[i] - node_t[i - 1] : node_t[1] - node_t[0];
      }
    }
    cycle_len = stage.period_hours().value_or(clock);
  }

  // ── Cyclic commitment (`cycle = "stage"`): the stage is a typical
  // period repeated in a cycle.  The first transition wraps to the last
  // period, windows and ramps wrap, no initial state is used.  Periodic
  // point grids carry N distinct nodes (a Lobatto end node t_N ≡ t_0 is
  // dropped by the converter and its weight added to node 0).  The block
  // formulation (non-chronological stage) has no inter-block coupling,
  // so nothing wraps there.
  const bool cyclic = chronological && commitment().is_cyclic();
  const size_t ncyc = blocks.size();
  if (cyclic && point && blocks.size() >= 2) {
    // backward step of the first node across the period end
    back_step[0] = node_t[0] + cycle_len - node_t[ncyc - 1];
  }
  // Start / shutdown events are counted per cycle; a typical period
  // standing for `duration / period_hours` repetitions pays them that
  // many times (energy costs already scale through the durations).
  const double cycle_weight =
      (cyclic && stage.period_hours() && *stage.period_hours() > 0.0)
      ? stage.duration() / *stage.period_hours()
      : 1.0;

  // ── Long-transition units: a cyclic unit whose min up / down time or
  // start window exceeds the typical period T cannot honour them within
  // the cycle.  The declared handling (`long_transition`: on / off /
  // no_uc, validated upstream — undeclared units are an error there)
  // drops those constraints.
  // Only the chronological model has windows that can exceed the period.
  const auto long_list = chronological
      ? commitment().long_constraints(cycle_len)
      : decltype(commitment().long_constraints(cycle_len)) {};
  const auto long_mode =
      setup_long_transition(sc, scenario, stage, lp, cycle_len, long_list);
  const bool drop_long = !long_mode.empty();
  if (long_mode == "no_uc") {
    // plain continuous generator: no commitment, pmin relaxed to 0
    for (const auto& block : blocks) {
      if (const auto it = generation_cols.find(block.uid());
          it != generation_cols.end())
      {
        lp.set_col_lowb(it->second, 0.0);
      }
    }
    return true;
  }
  const auto drops = [&](std::string_view what)
  {
    return drop_long
        && std::ranges::any_of(long_list,
                               [&](const auto& c) { return c.first == what; });
  };

  if (blocks.empty()) {
    return true;
  }

  // ── LP-size: skip the whole commitment when the generator elided
  // every generation column for this (scenario, stage) ───────────────
  // ``lookup_generation_cols`` is empty precisely when the generator
  // emitted no dispatch column at all — every block was P1-skipped
  // (all-zero pmax) or ``--use-plexos-gen-cap`` forced the unit OFF.
  // In that case the status/startup/shutdown binaries (u/v/w) and all
  // the C1/C3/min-up/min-down/max-starts rows would couple to nothing
  // physical: Phase B already `continue`s on every block (no gcol to
  // bind), leaving the binaries orphaned.  Returning here drops those
  // orphan columns and rows entirely, consistent with the elided
  // generation column.
  //
  // Write-out rule: a generator with no dispatch column is OFF for the
  // whole (scenario, stage), so its commitment is identically
  // ``status = startup = shutdown = 0``.  The output holders stay
  // empty, which the writer renders as "no rows" (long) / "no uid
  // column" (wide) — the natural zero, matching the absent generation.
  if (generation_cols.empty()) {
    return true;
  }

  if (!chronological && !block_note_logged_ && has_chronological_data()) {
    block_note_logged_ = true;
    spdlog::info(
        "Commitment '{}': non-chronological stage {} uses the per-block "
        "formulation; startup/shutdown/ramp/min up-down data is ignored "
        "there (no-load cost / fuel is charged per block)",
        commitment().name,
        stage.uid());
  }

  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto cuid = uid();

  // Resolve commitment parameters
  // No-load cost applies in both formulations: noload·u·duration per
  // period (one block per period on the block formulation).
  const auto noload = commitment().noload_cost.value_or(0.0);
  // Startup / shutdown costs are now per-(stage, block) schedules
  // (``OptTBRealSched``), resolved at the period's representative block
  // inside the loop below (see ``v_cost`` / ``w_cost``).  A scalar or
  // per-stage JSON input broadcasts to every block, so this reproduces
  // the legacy per-stage cost byte-for-byte while letting a single-stage
  // PLEXOS conversion carry a per-period Gen_StartCost profile.
  // Rounded at 0.5 (as LineCommitment does) so the first period's C1
  // RHS is integral: with an integral u, v[0] / w[0] are then forced to
  // {0, 1} by C1 + C3a / C3b like every other period.
  const auto initial_u =
      (commitment().initial_status.value_or(0.0) >= 0.5) ? 1.0 : 0.0;
  // ``initial_power`` (≡ ``p_prev`` at the first block of the
  // planning horizon) defaults to 0.  When ``initial_u = 1`` and
  // pmin > 0, this default forces the LP's first-block ramp-up to
  // be infeasible whenever the ramp limit < pmin (the case that
  // exposed the bug: RTS-GMLC ``216_STEAM_1`` with pmin = 62,
  // ramp_up = 60 → ``p[0] ≤ 0 + 60·1 = 60`` clashed with the pmin
  // floor of 62).  Hot-start callers (e.g. UC.jl's
  // ``Initial power (MW)`` field) must set ``Commitment.initial_power``
  // explicitly for the first-block constraint to honor the
  // previous-block dispatch.
  //
  // When ``initial_power`` is unset, the generator's own
  // ``Generator.initial_generation`` (the same ``t = 0⁻`` dispatch that
  // ``generation_prev`` reads in user constraints) is the fallback — but
  // ONLY at the horizon start (no predecessor stage).  A later stage /
  // phase keeps the legacy 0 here, so the horizon initial condition is
  // never re-applied mid-horizon.
  const bool horizon_start = sc.simulation().prev_stage(stage).first == nullptr;
  const auto initial_p = commitment().initial_power.value_or(
      horizon_start ? generator_lp.generator().initial_generation.value_or(0.0)
                    : 0.0);
  const auto is_relax = commitment().relax.value_or(false)
      || sc.simulation().phases()[stage.phase_index()].is_continuous();
  const auto is_must_run = commitment().must_run.value_or(false);
  // Ramp limits / startup-shutdown envelopes are now per-(stage, block)
  // schedules (``OptTBRealSched``), resolved block-by-block below exactly
  // like ``pmin_``.  ``has_value()`` here reports whether the field was
  // supplied at all (scalar or per-block); a scalar input resolves to the
  // same constant on every block, preserving the legacy ``OptReal``
  // behaviour byte-for-byte.  The per-block ``value_or(gen_pmax)``
  // "unset ⇒ pmax (no limit)" fallback is applied inside the loop.
  const auto has_ramp_up = chronological && ramp_up_.has_value();
  const auto has_ramp_down = chronological && ramp_down_.has_value();
  const auto has_startup_ramp = chronological && startup_ramp_.has_value();
  const auto has_shutdown_ramp = chronological && shutdown_ramp_.has_value();
  // Integrality of startup v / shutdown w (`transition_integrality`,
  // per unit, else model_options; default "auto" = binary).  See the
  // u/v/w block for the argument.
  const auto ti_mode =
      commitment()
          .transition_integrality
          .and_then([](const Name& n)
                    { return enum_from_name<TransitionIntegrality>(n); })
          .value_or(sc.options().transition_integrality());
  const auto binary_transitions = transition_cols_binary(ti_mode);
  // Integrality of the hot / warm / cold tier selectors
  // (`startup_tier_integrality`, default continuous: the tier rows are
  // an interval matrix — TU — once v / w are integral).
  const auto integer_tiers =
      commitment()
          .startup_tier_integrality
          .and_then([](const Name& n)
                    { return enum_from_name<StartupTierIntegrality>(n); })
          .value_or(sc.options().startup_tier_integrality())
      == StartupTierIntegrality::integer;

  // ── Power-based UC (`uc_formulation = "power_based"`, §4.6 of
  // docs/unit-commitment.md): chronological BLOCK stages only.  Point
  // stages already sample instantaneous power; an expandable generator
  // has no fixed P̄ for the boundary rows — both keep the energy-block
  // rows (warned once).
  const bool power_based =
      chronological
      && commitment().is_power_based(sc.options().uc_power_based())
      && power_based_applies(
          stage, point, generator_lp.capacity_col_at(stage).has_value());

  // Resolve piecewise heat rate curve.
  //
  // DEPRECATED on Commitment.  Piecewise heat-rate-derived fuel cost
  // Piecewise heat-rate + fuel-cost handling is OWNED by ``GeneratorLP``
  // (source/generator_lp.cpp:272+).  ``Generator.pmax_segments`` +
  // ``Generator.heat_rate_segments`` + ``Generator.fuel`` are wired
  // there as a pure-LP convex-slack formulation that works with or
  // without a Commitment binary.  CommitmentLP intentionally
  // *DOES NOT* read those fields anymore — they were removed from
  // the Commitment struct on 2026-05-20 (commitment.hpp).

  // Emission cost is no longer wired here — `EmissionSourceLP` adds
  // the zone's `price · weight · rate` adder directly onto the
  // generator dispatch column and stamps the cap-row / allowance-pool
  // coefficients; `EmissionZoneLP` only owns the cap row (+ slack).
  // Generator dispatch + segment cost accounting here is fuel-only.

  // Resolve startup cost tiers
  const auto opt_hot_cost = commitment().hot_start_cost;
  const auto opt_warm_cost = commitment().warm_start_cost;
  const auto opt_cold_cost = commitment().cold_start_cost;
  const auto opt_hot_time = commitment().hot_start_time;
  const auto opt_cold_time = commitment().cold_start_time;
  const bool all_tiers = opt_hot_cost.has_value() && opt_warm_cost.has_value()
      && opt_cold_cost.has_value() && opt_hot_time.has_value()
      && opt_cold_time.has_value();
  const bool any_tier = opt_hot_cost.has_value() || opt_warm_cost.has_value()
      || opt_cold_cost.has_value() || opt_hot_time.has_value()
      || opt_cold_time.has_value();
  // The tier block prices starts only when every field is given and the
  // windows are ordered; otherwise the plain startup cost applies (before,
  // an incomplete or inverted tier set made every start free).
  const bool has_startup_tiers = chronological && all_tiers
      && opt_cold_time.value_or(0.0) >= opt_hot_time.value_or(0.0);
  // Fallback for an unusable tier set: `startup_cost`, else the largest
  // given tier cost (never a free start).
  const std::optional<double> tier_fallback_cost =
      [&]() -> std::optional<double>
  {
    if (!any_tier || has_startup_tiers) {
      return std::nullopt;
    }
    double c = 0.0;
    for (const auto& t : {opt_hot_cost, opt_warm_cost, opt_cold_cost}) {
      c = std::max(c, t.value_or(0.0));
    }
    return c;
  }();
  if (chronological && any_tier && !has_startup_tiers && !tier_warned_) {
    tier_warned_ = true;
    spdlog::warn(
        "Commitment '{}': startup cost tiers need hot/warm/cold_start_cost "
        "and hot/cold_start_time with cold_start_time >= hot_start_time; "
        "{} — pricing every start at {}",
        commitment().name,
        all_tiers ? "cold_start_time < hot_start_time" : "some are missing",
        startup_cost_.has_value()
            ? std::string {"startup_cost"}
            : std::format("the largest tier cost ({})",
                          tier_fallback_cost.value_or(0.0)));
  }

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  // Operation wear coefficients (outputs only; empty when off).
  BIndexHolder<double> startup_wear;
  BIndexHolder<double> wear_a;
  BIndexHolder<double> wear_b;
  BIndexHolder<double> wear_pmin;

  // ── Compute commitment periods ──
  // When commitment_period is set, binary variables (u/v/w) are created at
  // a coarser resolution.  Each period groups consecutive blocks whose
  // cumulative duration fits within commitment_period hours.
  // Default: one period per block (commitment_period not set).
  const auto cyc_step = std::span<const double>(step).first(ncyc);
  // The daily `commitment_breaks` grid (any chronological stage; the
  // unit's own list, else the planning default) replaces the period grid.
  const auto daily_breaks = chronological
      ? commitment().effective_commitment_breaks(
            stage.commitment_breaks(), sc.options().commitment_breaks())
      : std::span<const Real> {};
  const double period_hours = !daily_breaks.empty()
      ? 0.0
      : resolve_period_hours(
            commitment(), blocks, cyc_step, chronological, point, power_based);
  // Node-time grids: the daily grid, else the uniform grid of point
  // stages (default).
  const auto node_grid =
      make_node_grid(daily_breaks,
                     stage.start_hour(),
                     cycle_len,
                     cyclic,
                     point && !commitment().uses_legacy_point_grid(),
                     period_hours);

  // Period grouping (see commitment_period_layout): period_starts[k] =
  // index into blocks[] where period k begins.
  auto layout = commitment_period_layout(blocks,
                                         ncyc,
                                         point,
                                         node_grid,
                                         period_hours,
                                         node_t,
                                         step,
                                         cyc_step,
                                         cycle_len);
  const auto& period_starts = layout.starts;
  const auto nperiods = period_starts.size();
  const auto& block_period = layout.block_period;
  const auto& period_block_uids = layout.block_uids;
  const auto& period_real = layout.real;
  const bool explicit_cells = layout.explicit_cells;
  const bool boundary_allowance = commitment().ramp_allowance_at_boundary();
  const bool wrap_merged = layout.wrap_merged;
  const auto period_scope =
      (period_hours > 0.0 || explicit_cells || node_grid.active)
      ? IntegerScope::Group
      : IntegerScope::Block;
  const auto period_domain =
      is_relax ? IntegerDomain::Relaxed : IntegerDomain::Binary;

  // ── Phase A: Create u/v/w per commitment period, C1 and C3 ──
  BIndexHolder<ColIndex> ucols;  // period representative buid → u col
  BIndexHolder<ColIndex> vcols;
  BIndexHolder<ColIndex> wcols;
  BIndexHolder<RowIndex> lrows;
  BIndexHolder<RowIndex> erows;
  map_reserve(ucols, nperiods);
  map_reserve(vcols, nperiods);
  map_reserve(wcols, nperiods);
  map_reserve(lrows, nperiods);
  map_reserve(erows, nperiods);

  // Period-indexed v/w columns: avoid repeated map lookups per block in the
  // phase-B loop (each period is visited once per block in that period).
  std::vector<ColIndex> period_vcol(nperiods);
  std::vector<ColIndex> period_wcol(nperiods);

  // block_ucol / block_vcol / block_wcol map EVERY block uid → its
  // period's u / v / w column. The per-block maps feed both the
  // ramp / logic / gen-bound constraint rows and the AMPL variable
  // registry (see end of this function), so user constraints can
  // reference ``commitment("X").status`` / ``.startup`` / ``.shutdown``.
  BIndexHolder<ColIndex> block_ucol;
  BIndexHolder<ColIndex> block_vcol;
  BIndexHolder<ColIndex> block_wcol;
  map_reserve(block_ucol, blocks.size());
  map_reserve(block_vcol, blocks.size());
  map_reserve(block_wcol, blocks.size());

  ColIndex prev_ucol {};
  bool first_period = true;
  struct FirstPeriod
  {
    ColIndex ucol;
    ColIndex vcol;
    ColIndex wcol;
    BlockUid buid;
  };
  FirstPeriod cyc_first {};

  for (size_t p = 0; p < nperiods; ++p) {
    const auto pstart = period_starts[p];
    const auto pend = (p + 1 < nperiods) ? period_starts[p + 1] : ncyc;

    // Period representative block (first block in the period)
    const auto& rep_block = blocks[pstart];
    const auto rep_buid = rep_block.uid();
    const auto ctx = make_block_context(scenario.uid(), stage.uid(), rep_buid);

    // Accumulate period duration for noload cost
    double period_duration = 0.0;
    for (size_t i = pstart; i < pend; ++i) {
      period_duration += blocks[i].duration();
    }

    // ── Create u (status) variable ──
    // Noload cost is proportional to the period duration, not block duration.
    // Use the representative block for probability/discount factors.
    const auto u_cost =
        CostHelper::block_ecost(scenario, stage, rep_block, noload)
        * (period_duration / rep_block.duration());

    // Resolve commitment bounds (fixed_status > must_run > default; a
    // declared long-transition status wins over both).
    const auto [u_lowb, u_uppb] = status_bounds(
        fixed_status_.optval(stage.uid(), rep_buid), is_must_run, long_mode);

    auto ucol =
        sc.add_integer_col(lp,
                           IntegerVariable::key(scenario,
                                                stage,
                                                Element::class_name,
                                                cuid,
                                                StatusName,
                                                period_scope,
                                                rep_buid),
                           SparseCol {
                               .lowb = u_lowb,
                               .uppb = u_uppb,
                               .cost = u_cost,
                               // pin scale on all three commitment vars (status
                               // u + startup v + shutdown w), not just the
                               // integer one — u is semantically binary even
                               // when LP-relaxed (--no-mip drops is_integer).
                               .pin_scale = true,
                               .class_name = cname,
                               .variable_name = StatusName,
                               .variable_uid = cuid,
                               .context = ctx,
                           },
                           period_domain,
                           period_scope,
                           rep_buid,
                           std::span<const BlockUid> {period_block_uids[p]});
    ucols[rep_buid] = ucol;

    // Block formulation (non-chronological stage): u alone, one per
    // period — no startup / shutdown columns, no logic or exclusion row.
    // Every block of the period shares it (a `commitment_cell` period
    // holds several blocks; otherwise the period is the block itself).
    if (!chronological) {
      for (const auto buid : period_block_uids[p]) {
        block_ucol[buid] = ucol;
      }
      continue;
    }

    // ── Create v (startup) and w (shutdown) variables ──
    //
    // Integrality of v / w (`transition_integrality`, see
    // transition_integrality.hpp; `binary_transitions` above).
    //
    // C1 (`u[p] - u[p-1] = v[p] - w[p]`) and C3 (`v[p] + w[p] <= 1`)
    // admit v = w = x <= 1/2 on a unit that stays on (or off), which the
    // SU·v / SD·w ramp terms (and any user row on startup / shutdown)
    // reward, so "auto" (default) declares v / w BINARY — the exact
    // three-binary model (UnitCommitment.jl's Gar1962).  "continuous"
    // keeps them in [0, 1] (smaller MIP, not exact with start / stop
    // ramps); "binary" = "auto".  Binary v / w go through
    // `add_integer_col` with u's domain, so `relax`, relaxed phases and
    // `--no-mip` relax all three together.
    //
    // The rows v <= u / w <= 1 - u (`transition_status_rows`, 2026-11-25)
    // made continuous v / w exact; they were removed on 2026-11-26:
    // redundant with binary v / w, and on the CEN PCP case they left the
    // relaxed LP degenerate with a near-singular basis (CPLEX kappa 1e16+,
    // feasible LPs reported infeasible).
    //
    // Startup is a once-per-EVENT cost ($/start), NOT a
    // per-hour power cost, so it must NOT be multiplied by the block duration
    // (Δt).  Use the no-duration ``cost_factor`` (probability · discount) —
    // present-valued but face-value in magnitude.  Multiplying by
    // ``rep_block.duration()`` (as ``block_ecost`` does) would inflate it by
    // the block length. Resolve startup / shutdown cost at this period's
    // representative block (a scalar/per-stage input broadcasts to the same
    // value on every block — full back-compat with the legacy per-stage cost).
    const auto block_startup_cost =
        startup_cost_.optval(stage.uid(), rep_buid)
            .value_or(tier_fallback_cost.value_or(0.0));
    const auto block_shutdown_cost =
        shutdown_cost_.optval(stage.uid(), rep_buid).value_or(0.0);
    const auto start_wear =
        resolve_start_wear(scenario, stage, rep_buid, startup_wear);
    const auto v_cost =
        ((has_startup_tiers ? 0.0 : block_startup_cost) + start_wear)
        * cycle_weight
        * CostHelper::cost_factor(scenario.probability_factor(),
                                  stage.discount_factor());
    // Binary with ramp rows, continuous otherwise (see above).  Pin
    // ``scale = 1.0`` either way so the C1 coefficient invariants survive
    // Ruiz / VariableScaleMap — task #50.
    const auto add_transition_col = [&](std::string_view name,
                                        double cost) -> ColIndex
    {
      SparseCol col {
          .lowb = 0.0,
          .uppb = 1.0,
          .cost = cost,
          .is_integer = false,
          .pin_scale = true,  // semantically binary even when LP-relaxed
          .class_name = cname,
          .variable_name = name,
          .variable_uid = cuid,
          .context = ctx,
      };
      if (!binary_transitions) {
        return lp.add_col(std::move(col));
      }
      return sc.add_integer_col(
          lp,
          IntegerVariable::key(scenario,
                               stage,
                               Element::class_name,
                               cuid,
                               name,
                               period_scope,
                               rep_buid),
          std::move(col),
          period_domain,
          period_scope,
          rep_buid,
          std::span<const BlockUid> {period_block_uids[p]});
    };
    auto vcol = add_transition_col(StartupName, v_cost);
    vcols[rep_buid] = vcol;
    period_vcol[p] = vcol;

    // Shutdown, like startup, is a once-per-EVENT cost ($/stop), NOT a
    // per-hour power cost — use the no-duration ``cost_factor`` so it is
    // not inflated by the block length.
    const auto w_cost = block_shutdown_cost * cycle_weight
        * CostHelper::cost_factor(scenario.probability_factor(),
                                  stage.discount_factor());
    // Shutdown ``w`` — same shape and integrality as startup.
    auto wcol = add_transition_col(ShutdownName, w_cost);
    wcols[rep_buid] = wcol;
    period_wcol[p] = wcol;

    // Fan the u / v / w columns out across every block in this period
    // so per-block constraint rows and AMPL accessors can resolve them.
    for (const auto buid : period_block_uids[p]) {
      block_ucol[buid] = ucol;
      block_vcol[buid] = vcol;
      block_wcol[buid] = wcol;
    }

    // ── C1: Logic transition (per period) ──
    // u[p] - u[p-1] - v[p] + w[p] = 0
    // For first period: u[0] - v[0] + w[0] = initial_status (rounded)
    // (cyclic: u[0] - u[P-1] - v[0] + w[0] = 0, added after the loop)
    if (cyclic && first_period) {
      cyc_first = {.ucol = ucol, .vcol = vcol, .wcol = wcol, .buid = rep_buid};
    } else {
      auto row =
          SparseRow {
              .pin_scale = true,  // pure commitment logic
              .class_name = cname,
              .constraint_name = LogicName,
              .variable_uid = cuid,
              .context = ctx,
          }
              .equal(first_period ? initial_u : 0.0);
      row[ucol] = 1.0;
      if (!first_period) {
        row[prev_ucol] = -1.0;
      }
      row[vcol] = -1.0;
      row[wcol] = 1.0;
      lrows[rep_buid] = lp.add_row(std::move(row));
    }

    // ── C3: Exclusion (per period): v[p] + w[p] <= 1 ──
    {
      auto row =
          SparseRow {
              .pin_scale = true,  // pure commitment logic
              .class_name = cname,
              .constraint_name = ExclusionName,
              .variable_uid = cuid,
              .context = ctx,
          }
              .less_equal(1.0);
      row[vcol] = 1.0;
      row[wcol] = 1.0;
      erows[rep_buid] = lp.add_row(std::move(row));
    }

    prev_ucol = ucol;
    first_period = false;
  }
  if (cyclic) {
    // wrapped transition of the first period: u[0] − u[P−1] = v[0] − w[0]
    auto row =
        SparseRow {
            .class_name = cname,
            .constraint_name = LogicName,
            .variable_uid = cuid,
            .context =
                make_block_context(scenario.uid(), stage.uid(), cyc_first.buid),
        }
            .equal(0.0);
    if (prev_ucol != cyc_first.ucol) {
      row[cyc_first.ucol] = 1.0;
      row[prev_ucol] = -1.0;
    }
    row[cyc_first.vcol] = -1.0;
    row[cyc_first.wcol] = 1.0;
    lrows[cyc_first.buid] = lp.add_row(std::move(row));
    // Daily grid: the first and last periods are the two pieces of the
    // cell straddling the period start — one cell, one status, so no
    // start / stop at the wrap (u[0] = u[P−1]).
    if (wrap_merged) {
      lp.set_col_uppb(cyc_first.vcol, 0.0);
      lp.set_col_uppb(cyc_first.wcol, 0.0);
    }
  }

  // ── Phase B: Per-block constraints (C2, ramp, emission, segments) ──
  // Each block references its period's u column via block_ucol.
  BIndexHolder<RowIndex> gurows;
  BIndexHolder<RowIndex> glrows;
  BIndexHolder<RowIndex> rurows;
  BIndexHolder<RowIndex> rdrows;
  map_reserve(gurows, blocks.size());
  map_reserve(glrows, blocks.size());
  map_reserve(rurows, blocks.size());
  map_reserve(rdrows, blocks.size());

  ColIndex prev_gcol {};
  ColIndex prev_block_ucol {};
  bool first_block = true;

  // `ramp_formula = central2`: the ramp rows are built after the block
  // loop from the whole node grid (they need P_{k+1}).
  // Point-sampled stages default to central2 (a derivative at the node).
  const bool ramp_central2 = chronological && sc.options().ramp_central2();
  struct RampNode
  {
    BlockUid buid;
    ColIndex gcol;
    ColIndex ucol;
    ColIndex vcol;
    ColIndex wcol;
    double time;  ///< node time [h] (real)
    double gen_pmax;
  };
  std::vector<RampNode> ramp_nodes;
  double node_clock = 0.0;  ///< real-time start of the current block

  // Cyclic backward ramps: the first block reaches back to the last
  // block of the cycle.
  if (cyclic && !ramp_central2 && ncyc >= 2) {
    const auto last_buid = blocks[ncyc - 1].uid();
    if (const auto it = generation_cols.find(last_buid);
        it != generation_cols.end())
    {
      prev_gcol = it->second;
      prev_block_ucol = block_ucol.at(last_buid);
      first_block = false;
    }
  }

  for (size_t bidx = 0; bidx < blocks.size(); ++bidx) {
    const auto& block = blocks[bidx];
    const auto buid = block.uid();
    const auto ctx = make_block_context(scenario.uid(), stage.uid(), buid);

    const auto gcol_it = generation_cols.find(buid);
    if (gcol_it == generation_cols.end()) {
      continue;
    }
    const auto gcol = gcol_it->second;
    const auto ucol = block_ucol.at(buid);

    // Look up period's v/w for ramp constraints via period-indexed cache
    // (avoids two map .at() lookups per block).
    const auto pidx = block_period[bidx];
    const auto vcol = period_vcol[pidx];
    const auto wcol = period_wcol[pidx];
    // SU / SD allowance: at a period boundary only (default,
    // ramp_allowance = "boundary") or on every block of the period
    // ("period")
    const bool period_first =
        !boundary_allowance || bidx == period_starts[pidx];

    const auto gen_pmax = lp.get_col_uppb(gcol);
    // ── Resolve commitment-conditional pmin ──
    // ``Commitment.pmin`` is the per-unit min stable level when
    // committed (PLEXOS ``Generator.Min Stable Level`` semantics).
    // It is INDEPENDENT from ``Generator.pmin``, the always-on hard
    // floor that lives on the gen column's ``lowb``.  Both bounds
    // remain in force; the LP enforces:
    //   gen ≥ Generator.pmin                  (always, col bound)
    //   gen ≥ Commitment.pmin × u             (conditional, row below)
    // CommitmentLP must NOT modify ``Generator.pmin`` (the col
    // ``lowb``) — that's the user's territory; only adds its own
    // u-linked row.
    //
    // When ``Commitment.pmin`` is unset (legacy JSON written before
    // the field existed), fall back on reading the col ``lowb`` so
    // round-trip compatibility is preserved.  Legal as long as
    // ``Generator.pmin ≤ Commitment.pmin``; values that violate
    // that order are a user error (the LP still solves but the
    // conditional pmin is irrelevant — dominated by the always-on
    // floor).
    const auto explicit_pmin = pmin_.optval(stage.uid(), buid);
    const auto gen_pmin =
        explicit_pmin.has_value() ? *explicit_pmin : lp.get_col_lowb(gcol);

    // ── C2: Generation upper bound: p - Pmax*u <= 0 ──
    //
    // ``Generator.pmin`` (the gen col ``lowb``) is left untouched —
    // it is an independent always-on floor, distinct from the
    // commitment-conditional ``Commitment.pmin`` applied via the
    // C2 row below.  Users wanting a purely-conditional pmin set
    // ``Generator.pmin = 0`` and ``Commitment.pmin = desired floor``.

    if (!power_based) {
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = GenUpperName,
              .variable_uid = cuid,
              .context = ctx,
          }
              .less_equal(0.0);
      row[gcol] = 1.0;
      row[ucol] = -gen_pmax;
      gurows[buid] = lp.add_row(std::move(row));
    }

    add_operation_wear(lp,
                       scenario,
                       stage,
                       block,
                       {
                           .ucol = ucol,
                           .gcol = gcol,
                           .pmin = gen_pmin,
                           .pmax = gen_pmax,
                       },
                       wear_a,
                       wear_b,
                       wear_pmin);

    // ── C2: Generation lower bound: p - Pmin*u >= 0 ──
    // (power_based: PB1–PB4 bound the boundary powers instead)
    if (!power_based) {
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = GenLowerName,
              .variable_uid = cuid,
              .context = ctx,
          }
              .greater_equal(0.0);
      row[gcol] = 1.0;
      row[ucol] = -gen_pmin;
      glrows[buid] = lp.add_row(std::move(row));
    }

    if (ramp_central2) {
      const double real_dt = stage.real_duration(block);
      ramp_nodes.push_back({
          .buid = buid,
          .gcol = gcol,
          .ucol = ucol,
          .vcol = vcol,
          .wcol = wcol,
          .time = block.time().value_or(node_clock + (0.5 * real_dt)),
          .gen_pmax = gen_pmax,
      });
      node_clock += real_dt;
    }

    // ── C4: Ramp up: p[t] - p[t-1] ≤ RU·u[t-1] + SU·v[t] ──
    if (!power_based && !ramp_central2 && (has_ramp_up || has_startup_ramp)) {
      // Per-block resolution mirrors ``pmin_`` above: ``optval`` returns
      // the scheduled rate at this (stage, block), or nullopt for unset
      // blocks → ``value_or(gen_pmax)`` keeps the legacy "no limit ⇒
      // pmax" semantics.  A scalar JSON input resolves to the same value
      // on every block.
      const auto ru = ramp_up_.optval(stage.uid(), buid).value_or(gen_pmax)
          * back_step[bidx];
      const auto su =
          startup_ramp_.optval(stage.uid(), buid).value_or(gen_pmax);

      // First block uses ``initial_p`` as ``p_prev`` and ``initial_u``
      // as ``u_prev``; subsequent blocks reach back to ``prev_gcol``
      // / ``prev_block_ucol`` for the same quantities.  Without
      // ``initial_p`` on the RHS, hot-start gens with ``ramp_up <
      // pmin`` (e.g. RTS-GMLC ``216_STEAM_1``) go infeasible on the
      // first block.  The start term stays the v[0] column there:
      // ``p[0] − SU·v[0] ≤ initial_p + RU·initial_u`` (the former
      // constant ``SU·(1 − initial_u)`` is the same for an integral
      // v[0] but looser in the LP relaxation).
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = RampUpName,
              .variable_uid = cuid,
              .context = ctx,
          }
              .less_equal(first_block ? initial_p + (ru * initial_u) : 0.0);
      row[gcol] = 1.0;
      if (!first_block) {
        row[prev_gcol] = -1.0;
        row[prev_block_ucol] = -ru;
      }
      if (period_first) {
        row[vcol] = -su;
      }
      rurows[buid] = lp.add_row(std::move(row));
    }

    // ── C5: Ramp down: p[t-1] - p[t] ≤ RD·u[t] + SD·w[t] ──
    if (!power_based && !ramp_central2 && (has_ramp_down || has_shutdown_ramp))
    {
      // Per-block resolution, mirroring the ramp-up branch / ``pmin_``.
      const auto rd = ramp_down_.optval(stage.uid(), buid).value_or(gen_pmax)
          * back_step[bidx];
      const auto sd =
          shutdown_ramp_.optval(stage.uid(), buid).value_or(gen_pmax);

      // First block: ``p_prev`` is the constant ``initial_p`` (moved to
      // the RHS), but ``u[t]`` / ``w[t]`` stay the block's own columns —
      // ``initial_p − p[0] ≤ RD·u[0] + SD·w[0]``.  Replacing them by
      // constants from ``initial_u`` (the former form,
      // ``initial_p − p[0] ≤ RD·initial_u + SD·(1 − initial_u)``)
      // dropped the shutdown term for a unit that starts online, so it
      // could not shut down in block 1 unless ``initial_p ≤ RD``
      // (RTS-GMLC: 313_CC_1 at 170 MW with RD 82.8 was forced on).
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = RampDownName,
              .variable_uid = cuid,
              .context = ctx,
          }
              .less_equal(first_block ? -initial_p : 0.0);
      row[gcol] = -1.0;
      if (!first_block) {
        row[prev_gcol] = 1.0;
      }
      row[ucol] = -rd;
      if (period_first) {
        row[wcol] = -sd;
      }
      rdrows[buid] = lp.add_row(std::move(row));
    }

    // Piecewise heat rate curve — NO LONGER built here.  ``GeneratorLP``
    // owns the piecewise dispatch-cost formulation via convex slack
    // columns (source/generator_lp.cpp:272+).  See the deprecation
    // warning at the top of this function for migration guidance.
    // The legacy Commitment.pmax_segments / heat_rate_segments fields
    // are still parsed for back-compat but no longer wired into the
    // LP from CommitmentLP — preventing double-counting if both the
    // legacy Commitment-side and new Generator-side fields are
    // populated.

    prev_block_ucol = ucol;
    prev_gcol = gcol;
    first_block = false;
  }

  // ── C4/C5 (central2): −RD·u_k − SD/h_k·w_k ≤ P'(t_k) ≤ RU·u_k + SU/h_k·v_k
  //
  // P'(t_k) on the non-uniform node grid (PROVISIONAL, see
  // ModelOptions::ramp_formula):
  //   interior  [h₋²P_{k+1} − h₊²P_{k−1} + (h₊² − h₋²)P_k] / (h₊h₋(h₊+h₋))
  //   first     (P_1 − P_0) / (t_1 − t_0)          (forward, 1st order)
  //   last      (P_N − P_{N−1}) / (t_N − t_{N−1})  (backward, 1st order)
  // With `ramp_wrap` the end nodes use the central formula with the
  // neighbour across the period end (t ± period).  The startup /
  // shutdown allowances SU / SD [MW] are spread over the node's step h_k
  // (h₋, or h₊ at the first node).
  if (!power_based && ramp_central2 && ramp_nodes.size() >= 2
      && (has_ramp_up || has_startup_ramp || has_ramp_down
          || has_shutdown_ramp))
  {
    const auto n = ramp_nodes.size();
    const double period = stage.period_hours().value_or(node_clock);
    const bool wrap = (sc.options().ramp_wrap() || cyclic) && n >= 3;
    for (size_t k = 0; k < n; ++k) {
      const auto& nd = ramp_nodes[k];
      // (column, coefficient) of the derivative stencil
      std::vector<std::pair<ColIndex, double>> stencil;
      double h_k = 0.0;
      // the later neighbour of the stencil and its P coefficient: a
      // start / stop in (t_k, t_next] moves P_next by up to SU / SD,
      // so its v / w gets the same allowance here (needed once v ≤ u,
      // w ≤ 1 − u close the fractional v = w loophole)
      const RampNode* next_nd = nullptr;
      // the earlier stencil node: the own v / w allowance applies only
      // when the stencil crosses into this node's commitment cell
      const RampNode* prev_nd = nullptr;
      double c_next = 0.0;
      const bool first = (k == 0);
      const bool last = (k + 1 == n);
      if ((first || last) && !wrap) {
        const auto& a = ramp_nodes[first ? 0 : k - 1];
        const auto& b = ramp_nodes[first ? 1 : k];
        h_k = b.time - a.time;
        stencil = {{b.gcol, 1.0 / h_k}, {a.gcol, -1.0 / h_k}};
        if (first) {
          next_nd = &b;
          c_next = 1.0 / h_k;
        } else {
          prev_nd = &a;
        }
      } else {
        const auto& prv = ramp_nodes[first ? n - 1 : k - 1];
        const auto& nxt = ramp_nodes[last ? 0 : k + 1];
        const double hm = nd.time - prv.time + (first ? period : 0.0);
        const double hp = nxt.time - nd.time + (last ? period : 0.0);
        const double den = hp * hm * (hp + hm);
        h_k = hm;
        stencil = {
            {nxt.gcol, (hm * hm) / den},
            {prv.gcol, -(hp * hp) / den},
            {nd.gcol, ((hp * hp) - (hm * hm)) / den},
        };
        next_nd = &nxt;
        prev_nd = &prv;
        c_next = (hm * hm) / den;
      }
      const auto ctx = make_block_context(scenario.uid(), stage.uid(), nd.buid);
      if (has_ramp_up || has_startup_ramp) {
        const auto ru =
            ramp_up_.optval(stage.uid(), nd.buid).value_or(nd.gen_pmax);
        const auto su =
            startup_ramp_.optval(stage.uid(), nd.buid).value_or(nd.gen_pmax);
        auto row =
            SparseRow {
                .class_name = cname,
                .constraint_name = RampUpName,
                .variable_uid = cuid,
                .context = ctx,
            }
                .less_equal(0.0);
        // scaled by h_k: no 1/h coefficients
        for (const auto& [col, c] : stencil) {
          row[col] += c * h_k;
        }
        row[nd.ucol] += -ru * h_k;
        if (!boundary_allowance || prev_nd == nullptr
            || prev_nd->vcol != nd.vcol)
        {
          row[nd.vcol] += -su;
        }
        if (next_nd != nullptr && next_nd->vcol != nd.vcol) {
          row[next_nd->vcol] += -su * c_next * h_k;
        }
        rurows[nd.buid] = lp.add_row(std::move(row));
      }
      if (has_ramp_down || has_shutdown_ramp) {
        const auto rd =
            ramp_down_.optval(stage.uid(), nd.buid).value_or(nd.gen_pmax);
        const auto sd =
            shutdown_ramp_.optval(stage.uid(), nd.buid).value_or(nd.gen_pmax);
        auto row =
            SparseRow {
                .class_name = cname,
                .constraint_name = RampDownName,
                .variable_uid = cuid,
                .context = ctx,
            }
                .less_equal(0.0);
        for (const auto& [col, c] : stencil) {
          row[col] += -c * h_k;
        }
        row[nd.ucol] += -rd * h_k;
        if (!boundary_allowance || prev_nd == nullptr
            || prev_nd->wcol != nd.wcol)
        {
          row[nd.wcol] += -sd;
        }
        if (next_nd != nullptr && next_nd->wcol != nd.wcol) {
          row[next_nd->wcol] += -sd * c_next * h_k;
        }
        rdrows[nd.buid] = lp.add_row(std::move(row));
      }
    }
  }

  // ── u-gate piecewise heat-rate kink rows ──
  //
  // ``GeneratorLP::add_to_lp`` emits the per-segment piecewise kink
  // rows as
  //
  //     p − δ_k ≤ pmax_segs[k-1]               (constant RHS)
  //
  // independent of any Commitment.  For a renewable-only Generator
  // (no Commitment) that is correct: the breakpoint is a physical
  // capacity threshold.  For a Generator gated by a Commitment, the
  // constant breakpoint over-relaxes the LP — with fractional ``u``
  // the dispatch ``p`` can still climb to ``pmax × u`` (from the
  // C2 upper row) and pay only marginal-segment cost, even though
  // physically the gen is "half on" and shouldn't have access to
  // the cheap segments at full width.  The pre-``refactor(commitment)``
  // code put piecewise on Commitment and explicitly gated each
  // segment by ``u``; the refactor moved piecewise to Generator and
  // dropped that gating, producing a looser LP-relax that drives
  // fractional ``u`` in cases where the integer optimum has ``u=1``
  // (22 UC.jl golden / real-case tests broke).
  //
  // Retro-fit: change every kink row from
  //
  //     p − δ_k ≤ pmax_segs[k-1]
  //
  // to
  //
  //     p − δ_k − pmax_segs[k-1] · u ≤ 0
  //
  // i.e. add the ``-pmax_segs[k-1]`` coefficient on ``ucol`` and
  // reset RHS to 0.  At ``u=1`` the row is algebraically identical
  // (renewable-only case unaffected because no Commitment runs
  // this block).  At ``u<1`` the segment becomes proportionally
  // tighter, restoring the pre-refactor LP-relax behaviour.
  // power_based: the generation column is the trapezoid energy, which is
  // positive in start-up / shut-down blocks with u = 0 — keep the constant
  // breakpoints there.
  if (!block_ucol.empty() && !power_based) {
    const auto& kink_rows = generator_lp.heat_rate_kink_rows();
    const auto& breakpoints = generator_lp.heat_rate_kink_breakpoints();
    if (!kink_rows.empty() && kink_rows.size() == breakpoints.size()) {
      for (size_t k = 0; k < kink_rows.size(); ++k) {
        const auto kit = kink_rows[k].find(st_key);
        if (kit == kink_rows[k].end()) {
          continue;
        }
        const double breakpoint_k = breakpoints[k];
        for (const auto& [buid, krow] : kit->second) {
          const auto ucol_it = block_ucol.find(buid);
          if (ucol_it == block_ucol.end()) {
            continue;
          }
          auto& row = lp.row_at(krow);
          row[ucol_it->second] = -breakpoint_k;
          row.uppb = 0.0;
        }
      }
    }
  }

  // ── Reserve-UC integration ──
  // Gate this generator's reserve headroom rows with u (same rows on the
  // chronological and block formulations, see
  // gate_reserve_provisions_by_status).  The
  // down-reserve floor is the committed minimum: ``Commitment.pmin``
  // when set (the documented pattern is ``Generator.pmin = 0`` plus a
  // conditional ``Commitment.pmin``), never below the always-on
  // ``Generator.pmin`` stored as the row's lowb.
  if (!block_ucol.empty()) {
    gate_reserve_provisions_by_status(
        sc,
        scenario,
        stage,
        lp,
        generator_index_,
        block_ucol,
        generation_cols,
        generator_lp.capacity_col_at(stage),
        [&](BlockUid buid, double gen_floor)
        {
          return std::max(gen_floor,
                          pmin_.optval(stage.uid(), buid).value_or(gen_floor));
        });
  }

  if (power_based) {
    add_power_based(sc,
                    scenario,
                    stage,
                    lp,
                    generation_cols,
                    block_period,
                    period_starts,
                    period_real,
                    ucols,
                    period_vcol,
                    period_wcol,
                    cyclic,
                    initial_u,
                    horizon_start);
  }

  // Inertia/Reserve u_commit linkage: NOT done here.  Architectural
  // principle (consumer pulls from owner): each provision LP class
  // (ReserveProvisionLP, InertiaProvisionLP) should look up its
  // generator's CommitmentLP and integrate the u_commit linkage in
  // its OWN add_to_lp — keeps the responsibility close to the
  // provision construction and avoids concentrating cross-class
  // knowledge inside CommitmentLP.  This requires reordering
  // ``collections_t`` so CommitmentLP precedes the provision LPs
  // (so u_commit columns exist when consumers look them up).
  //
  // The reserve-headroom gating above (urmax × u, drmax × u, the
  // urmin/drmin × u floors) is pushed from here via
  // gate_reserve_provisions_by_status.  Same for the Battery
  // pmin_charge/discharge gating in ConverterLP — Battery LP should
  // pull from Converter LP rather than have Converter own the
  // binaries.

  // ── C6: Min up time (at period level) ──
  // Σ_{q=p}^{min(p+UT_periods-1,P)} u[q] ≥ UT_periods · v[p]
  // Accumulates period durations to find how many periods cover min_up_hours.
  const auto min_up_hours =
      drops("min_up_time") ? 0.0 : commitment().min_up_time.value_or(0.0);
  if (min_up_hours > 0.0 && !ucols.empty() && !vcols.empty()) {
    BIndexHolder<RowIndex> mut_rows;
    map_reserve(mut_rows, nperiods);

    const auto& period_dur = period_real;

    for (size_t p = 0; p < nperiods; ++p) {
      const auto rep_buid = blocks[period_starts[p]].uid();
      const auto vcol_it = vcols.find(rep_buid);
      if (vcol_it == vcols.end()) {
        continue;
      }

      // Count how many periods from p onward cover min_up_hours
      // (cyclic: wrapping around the cycle, at most all its periods)
      double accum_hours = 0.0;
      size_t ut_periods = 0;
      const size_t ut_limit = cyclic ? nperiods : nperiods - p;
      for (size_t s = 0; s < ut_limit && accum_hours < min_up_hours; ++s) {
        accum_hours += period_dur[(p + s) % nperiods];
        ++ut_periods;
      }
      if (cyclic && p == 0 && accum_hours < min_up_hours - 1e-9) {
        spdlog::warn(
            "Commitment {}: min_up_time {} h ≥ cycle length {} h — capped "
            "at the cycle (a cyclic unit then cannot cycle within it)",
            commitment().name,
            min_up_hours,
            accum_hours);
      }
      if (ut_periods <= 1) {
        continue;  // trivially satisfied
      }

      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = MinUpTimeName,
              .variable_uid = cuid,
              .context =
                  make_block_context(scenario.uid(), stage.uid(), rep_buid),
          }
              .greater_equal(0.0);
      // Σ u[q] - UT_periods · v[p] ≥ 0
      for (size_t s = 0; s < ut_periods; ++s) {
        const auto q = (p + s) % nperiods;
        const auto q_buid = blocks[period_starts[q]].uid();
        const auto ucol_it = ucols.find(q_buid);
        if (ucol_it != ucols.end()) {
          row[ucol_it->second] = 1.0;
        }
      }
      row[vcol_it->second] = -static_cast<double>(ut_periods);
      mut_rows[rep_buid] = lp.add_row(std::move(row));
    }
    if (!mut_rows.empty()) {
      min_up_time_rows_[st_key] = std::move(mut_rows);
    }
  }

  // ── C7: Min down time (at period level) ──
  // Σ_{q=p}^{min(p+DT_periods-1,P)} (1 - u[q]) ≥ DT_periods · w[p]
  // Rearranged: Σ u[q] + DT_periods · w[p] ≤ span
  const auto min_down_hours =
      drops("min_down_time") ? 0.0 : commitment().min_down_time.value_or(0.0);
  if (min_down_hours > 0.0 && !ucols.empty() && !wcols.empty()) {
    BIndexHolder<RowIndex> mdt_rows;
    map_reserve(mdt_rows, nperiods);

    const auto& pd = period_real;

    for (size_t p = 0; p < nperiods; ++p) {
      const auto rep_buid = blocks[period_starts[p]].uid();
      const auto wcol_it = wcols.find(rep_buid);
      if (wcol_it == wcols.end()) {
        continue;
      }

      double accum_hours = 0.0;
      size_t dt_periods = 0;
      const size_t dt_limit = cyclic ? nperiods : nperiods - p;
      for (size_t s = 0; s < dt_limit && accum_hours < min_down_hours; ++s) {
        accum_hours += pd[(p + s) % nperiods];
        ++dt_periods;
      }
      if (cyclic && p == 0 && accum_hours < min_down_hours - 1e-9) {
        spdlog::warn(
            "Commitment {}: min_down_time {} h ≥ cycle length {} h — "
            "capped at the cycle",
            commitment().name,
            min_down_hours,
            accum_hours);
      }
      if (dt_periods <= 1) {
        continue;
      }

      const auto span = dt_periods;
      auto row =
          SparseRow {
              .class_name = cname,
              .constraint_name = MinDownTimeName,
              .variable_uid = cuid,
              .context =
                  make_block_context(scenario.uid(), stage.uid(), rep_buid),
          }
              .less_equal(static_cast<double>(span));
      // Σ u[q] + DT_periods · w[p] ≤ span
      for (size_t s = 0; s < dt_periods; ++s) {
        const auto q = (p + s) % nperiods;
        const auto q_buid = blocks[period_starts[q]].uid();
        const auto ucol_it = ucols.find(q_buid);
        if (ucol_it != ucols.end()) {
          row[ucol_it->second] = 1.0;
        }
      }
      row[wcol_it->second] = static_cast<double>(dt_periods);
      mdt_rows[rep_buid] = lp.add_row(std::move(row));
    }
    if (!mdt_rows.empty()) {
      min_down_time_rows_[st_key] = std::move(mdt_rows);
    }
  }

  // ── C8/C9/C10: Hot/warm/cold startup cost tiers (per period) ──
  // When all tier parameters are defined, create per-period tier variables
  // y_hot, y_warm, y_cold with constraints:
  //   C8: v[p] = y_hot[p] + y_warm[p] + y_cold[p]  (type selection)
  //   C9: y_hot[p] ≤ Σ w[q] for q in hot window    (recent shutdown)
  //   C10: y_warm[p] ≤ Σ w[q] for q in warm window  (medium offline)
  //   cold start is the residual via C8.
  if (has_startup_tiers && !vcols.empty() && !wcols.empty()) {
    const auto hot_cost = *opt_hot_cost;
    const auto warm_cost = *opt_warm_cost;
    const auto cold_cost = *opt_cold_cost;
    const auto hot_time = *opt_hot_time;
    const auto cold_time = *opt_cold_time;

    // Validate: cold_time must be >= hot_time (cold = longer offline)
    if (cold_time < hot_time) {
      spdlog::warn(
          "Commitment {}: cold_start_time ({}) < hot_start_time ({}), "
          "skipping startup tiers",
          commitment().name,
          cold_time,
          hot_time);
      return true;
    }
    const auto initial_hours_offline =
        (initial_u < 0.5) ? commitment().initial_hours.value_or(1e6) : 0.0;

    const auto& pdur = period_real;

    BIndexHolder<ColIndex> hcols;
    BIndexHolder<ColIndex> wmcols;
    BIndexHolder<ColIndex> ccols;
    BIndexHolder<RowIndex> st_rows;
    BIndexHolder<RowIndex> hr_rows;
    BIndexHolder<RowIndex> wr_rows;
    map_reserve(hcols, nperiods);
    map_reserve(wmcols, nperiods);
    map_reserve(ccols, nperiods);
    map_reserve(st_rows, nperiods);
    map_reserve(hr_rows, nperiods);
    map_reserve(wr_rows, nperiods);

    for (size_t p = 0; p < nperiods; ++p) {
      const auto& rep_block = blocks[period_starts[p]];
      const auto rep_buid = rep_block.uid();
      const auto vcol_it = vcols.find(rep_buid);
      if (vcol_it == vcols.end()) {
        continue;
      }

      const auto bctx =
          make_block_context(scenario.uid(), stage.uid(), rep_buid);
      // Start-up tier costs (hot/warm/cold) are once-per-EVENT costs
      // ($/start), NOT per-hour power costs — exactly like the plain
      // startup ``v`` above, they must NOT be multiplied by the block
      // duration (Δt).  Use the no-duration ``cost_factor`` (probability ·
      // discount).  When ``has_startup_tiers`` is set these tiers carry the
      // ENTIRE startup cost (``v_cost`` is forced to 0), so a
      // ``block_ecost`` here would re-introduce the exact Δt inflation the
      // ``v``/``w`` fix removed — guarded by the
      // "Startup TIERS are per-EVENT costs" regression test in
      // ``test/source/test_commitment.cpp``.
      const auto tier_factor = cycle_weight
          * CostHelper::cost_factor(scenario.probability_factor(),
                                    stage.discount_factor());

      // Tier-column scope / domain / blocks reuse the values computed
      // once for the u/v/w loop above (`period_scope`, `period_domain`,
      // `period_block_uids[p]`).
      const auto tier_blocks_span =
          std::span<const BlockUid> {period_block_uids[p]};

      // Tier selectors: continuous by default (with v / w integral the
      // tier rows — Σ y = v plus the cumulative offline-window rows — are
      // an interval matrix, hence TU, so optimal vertices are integral);
      // `startup_tier_integrality = integer` registers them as binaries
      // through the IntegerVariable registry like u.
      const auto add_tier_col = [&](std::string_view name,
                                    double cost) -> ColIndex
      {
        SparseCol col {
            .lowb = 0.0,
            .uppb = 1.0,
            .cost = cost,
            .pin_scale = true,  // semantically binary tier selector
            .class_name = cname,
            .variable_name = name,
            .variable_uid = cuid,
            .context = bctx,
        };
        if (!integer_tiers) {
          return lp.add_col(std::move(col));
        }
        return sc.add_integer_col(lp,
                                  IntegerVariable::key(scenario,
                                                       stage,
                                                       Element::class_name,
                                                       cuid,
                                                       name,
                                                       period_scope,
                                                       rep_buid),
                                  std::move(col),
                                  period_domain,
                                  period_scope,
                                  rep_buid,
                                  tier_blocks_span);
      };

      // Create tier variables with their respective costs (continuous,
      // or registry integers — see `add_tier_col`).  Per-event cost stays
      // no-Δt (see comment above).
      const auto h_cost = hot_cost * tier_factor;
      auto hcol = add_tier_col(HotStartName, h_cost);
      hcols[rep_buid] = hcol;

      const auto wm_cost = warm_cost * tier_factor;
      auto wmcol = add_tier_col(WarmStartName, wm_cost);
      wmcols[rep_buid] = wmcol;

      const auto c_cost = cold_cost * tier_factor;
      auto ccol = add_tier_col(ColdStartName, c_cost);
      ccols[rep_buid] = ccol;

      // C8: v[p] - y_hot[p] - y_warm[p] - y_cold[p] = 0
      {
        auto row =
            SparseRow {
                .pin_scale = true,  // pure commitment logic
                .class_name = cname,
                .constraint_name = StartupTypeName,
                .variable_uid = cuid,
                .context = bctx,
            }
                .equal(0.0);
        row[vcol_it->second] = 1.0;
        row[hcol] = -1.0;
        row[wmcol] = -1.0;
        row[ccol] = -1.0;
        st_rows[rep_buid] = lp.add_row(std::move(row));
      }

      // Offline time at p if the unit has stayed off since before the
      // horizon: the in-horizon time before p plus the initial offline
      // hours.  It prices a start whose look-back window reaches the
      // horizon start (in particular the FIRST block, where the in-horizon
      // window is empty): before, the initial offline time was only
      // consulted while p < hot_periods, which is never true at p = 0, so
      // a first-block start was always priced cold.
      double t_before_p = 0.0;
      for (size_t q = 0; q < p; ++q) {
        t_before_p += pdur[q];
      }
      const bool initially_off = initial_u < 0.5;
      const double offline_since_initial =
          initially_off ? t_before_p + initial_hours_offline : 0.0;

      // A shutdown in period q (w[q] = 1: off from the start of q) leaves
      // the unit offline D(q) = Σ pdur[q .. p-1] hours when it starts at
      // p.  The tiers use the same strict thresholds as the initial-
      // offline relaxation below: hot when D < hot_time, warm when
      // hot_time ≤ D < cold_time, cold otherwise.  So the hot window
      // holds the shutdown periods with D < hot_time, the cold-time
      // window those with D < cold_time, and the warm window their
      // difference.  A shutdown whose D reaches a threshold exactly
      // belongs to the next tier.  Cyclic stages wrap (at most the other
      // P − 1 periods).
      const auto back = [&](size_t s) { return (p + nperiods - s) % nperiods; };
      const size_t max_back = cyclic ? nperiods - 1 : p;
      const auto periods_below = [&](double limit)
      {
        double offline = 0.0;
        size_t count = 0;
        for (size_t s = 1; s <= max_back && offline + pdur[back(s)] < limit;
             ++s)
        {
          offline += pdur[back(s)];
          ++count;
        }
        return count;
      };
      const size_t hot_periods = periods_below(hot_time);
      const size_t cold_periods = periods_below(cold_time);

      // C9: y_hot[p] ≤ Σ w[q] for q in [p-hot_periods, p-1]
      {
        auto row =
            SparseRow {
                .pin_scale = true,  // pure commitment logic
                .class_name = cname,
                .constraint_name = HotStartName,
                .variable_uid = cuid,
                .context = bctx,
            }
                .less_equal(0.0);
        row[hcol] = 1.0;
        for (size_t s = 1; s <= hot_periods; ++s) {
          const auto q_buid = blocks[period_starts[back(s)]].uid();
          const auto wcol_it = wcols.find(q_buid);
          if (wcol_it != wcols.end()) {
            row[wcol_it->second] = -1.0;
          }
        }
        if (!cyclic && initially_off && offline_since_initial < hot_time) {
          row.uppb = 1.0;
        }
        hr_rows[rep_buid] = lp.add_row(std::move(row));
      }

      // C10: y_warm[p] ≤ Σ w[q] for q in [p-cold_periods, p-hot_periods-1]
      {
        auto row =
            SparseRow {
                .pin_scale = true,  // pure commitment logic
                .class_name = cname,
                .constraint_name = WarmStartName,
                .variable_uid = cuid,
                .context = bctx,
            }
                .less_equal(0.0);
        row[wmcol] = 1.0;
        for (size_t s = hot_periods + 1; s <= cold_periods; ++s) {
          const auto q_buid = blocks[period_starts[back(s)]].uid();
          const auto wcol_it = wcols.find(q_buid);
          if (wcol_it != wcols.end()) {
            row[wcol_it->second] = -1.0;
          }
        }
        if (!cyclic && initially_off && offline_since_initial >= hot_time
            && offline_since_initial < cold_time)
        {
          row.uppb = 1.0;
        }
        wr_rows[rep_buid] = lp.add_row(std::move(row));
      }
    }

    if (!hcols.empty()) {
      hot_start_cols_[st_key] = std::move(hcols);
    }
    if (!wmcols.empty()) {
      warm_start_cols_[st_key] = std::move(wmcols);
    }
    if (!ccols.empty()) {
      cold_start_cols_[st_key] = std::move(ccols);
    }
    if (!st_rows.empty()) {
      startup_type_rows_[st_key] = std::move(st_rows);
    }
    if (!hr_rows.empty()) {
      hot_start_rows_[st_key] = std::move(hr_rows);
    }
    if (!wr_rows.empty()) {
      warm_start_rows_[st_key] = std::move(wr_rows);
    }
  }

  // ── C9: Startup-count bounds per window (PLEXOS Max Starts + symmetric
  // Min Starts floor) ──
  //
  // Two-sided bound on the cumulative startup count over a rolling time
  // window:
  //
  //     min_starts  ≤  Σ_{p ∈ window} v[p]  ≤  max_starts
  //
  // Defaults: ``min_starts`` unset → 0 (no lower row); ``max_starts``
  // unset → +∞ (no upper row).  Each side emits ONE row per window when
  // the corresponding bound is set; both rows share the same accumulated
  // LHS so the per-window walk runs once even when both bounds apply.
  //
  // Window boundaries are determined by the SHARED ``starts_scope``:
  //   "hour"    →   1.0    (one row per period)
  //   "day"     →  24.0    (cumulative-duration boundary flush, mirror
  //                         of ``UserConstraint.daily_sum``)
  //   "week"    → 168.0    (or stage length if smaller — PLEXOS Max
  //                         Starts Week is the CEN PCP target use case)
  //   "horizon" → +∞       (one row per stage)
  //   N (int)   → N hours  (arbitrary window length)
  //
  // Scopes longer than the stage collapse to ``"horizon"`` (one row per
  // stage).  See ``Commitment::starts_window_hours`` for the resolved
  // mapping rules.
  const bool has_max_starts = commitment().max_starts.has_value();
  const bool has_min_starts = commitment().min_starts.has_value();
  if ((has_max_starts || has_min_starts) && !vcols.empty()
      && !drops("starts_scope"))
  {
    const double window_hours = commitment().starts_window_hours();
    const auto max_starts_v =
        has_max_starts ? static_cast<double>(*commitment().max_starts) : 0.0;
    const auto min_starts_v =
        has_min_starts ? static_cast<double>(*commitment().min_starts) : 0.0;

    BIndexHolder<RowIndex> ms_rows;
    auto fresh_row = [&]() -> SparseRow
    {
      SparseRow r;
      r.pin_scale = true;  // pure commitment logic (Σ v vs a count)
      r.class_name = cname;
      r.constraint_name = MaxStartsName;
      r.variable_uid = cuid;
      return r;
    };
    SparseRow window_row = fresh_row();
    bool row_open = false;
    double acc_hours = 0.0;
    BlockUid window_end_block {};

    auto flush_window = [&]()
    {
      if (!row_open) {
        return;
      }
      // Emit upper-bound row when ``max_starts`` is set, lower-bound
      // row when ``min_starts`` is set.  Both share the same LHS
      // (window_row).  Clone the row for the second emission so each
      // gets its own copy with the appropriate sense.
      if (has_max_starts) {
        SparseRow upper_row {window_row};
        auto bound = std::move(upper_row).less_equal(max_starts_v);
        const auto row_idx = lp.add_row(std::move(bound));
        ms_rows[window_end_block] = row_idx;
      }
      // LP-size: a ``min_starts`` of 0 makes the lower row ``Σ v ≥ 0``,
      // which is trivially satisfied (every ``v`` has lowb ≥ 0) and can
      // never bind — skip it.  The row index is never stored, so no
      // dual output is lost.  Write-out rule: none — a constraint that
      // is always slack contributes no primal/dual to any stream.
      if (has_min_starts && min_starts_v > 0.0) {
        SparseRow lower_row {window_row};
        // Distinct constraint_name: the upper and lower rows share the
        // window context, so reusing ``max_starts`` collides in the
        // LinearProblem row-metadata dedup when both bounds are set.
        lower_row.constraint_name = MinStartsName;
        auto bound = std::move(lower_row).greater_equal(min_starts_v);
        // Capture the row index but don't store it in a dedicated
        // holder — sensitivity work on forced-commitment can read the
        // dual from the LP via the row name + window block.
        [[maybe_unused]] const auto lower_idx = lp.add_row(std::move(bound));
      }
      window_row = fresh_row();
      row_open = false;
      acc_hours = 0.0;
    };

    for (size_t p = 0; p < nperiods; ++p) {
      const auto pstart = period_starts[p];
      const double period_hours = period_real[p];
      const auto& rep_blk = blocks[pstart];
      if (!row_open) {
        window_row.context =
            make_block_context(scenario.uid(), stage.uid(), rep_blk.uid());
        row_open = true;
      }
      window_row[period_vcol[p]] = 1.0;
      acc_hours += period_hours;
      window_end_block = rep_blk.uid();
      if (window_hours > 0.0 && acc_hours >= window_hours) {
        flush_window();
      }
    }
    flush_window();  // close any open window at stage-end

    if (!ms_rows.empty()) {
      max_starts_rows_[st_key] = std::move(ms_rows);
    }
  }

  // Fast-start pricing: register the integer commitment columns of a
  // fast-start unit — u, plus v / w when `transition_integrality` makes
  // them binary and the hot / warm / cold tier selectors when
  // `startup_tier_integrality = integer` (otherwise they are continuous
  // and already free in the pricing LP) — so
  // the `pricing_mode = "fast_start"` pricing LP relaxes them while every
  // other integer stays pinned at the MIP incumbent.  Registration is
  // cheap and mode-independent; a relaxed (LP) commitment has no integer
  // to relax.
  if (!is_relax
      && is_fast_start(commitment().fast_start,
                       commitment().min_up_time,
                       commitment().min_down_time,
                       sc.options().fast_start_max_hours()))
  {
    std::vector<ColIndex> fs_cols;
    fs_cols.reserve(ucols.size() * (binary_transitions ? 3 : 1));
    for (const auto& [buid, col] : ucols) {
      fs_cols.push_back(col);
    }
    if (integer_tiers) {
      for (const auto* tiers :
           {&hot_start_cols_, &warm_start_cols_, &cold_start_cols_})
      {
        if (const auto it = tiers->find(st_key); it != tiers->end()) {
          for (const auto& [buid, col] : it->second) {
            fs_cols.push_back(col);
          }
        }
      }
    }
    if (binary_transitions) {
      for (const auto& [buid, col] : vcols) {
        fs_cols.push_back(col);
      }
      for (const auto& [buid, col] : wcols) {
        fs_cols.push_back(col);
      }
    }
    lp.add_fast_start_cols(fs_cols);
  }

  // Store index holders
  if (!startup_wear.empty()) {
    startup_wear_coefs_[st_key] = std::move(startup_wear);
  }
  if (!wear_a.empty()) {
    wear_a_[st_key] = std::move(wear_a);
    wear_b_[st_key] = std::move(wear_b);
    wear_pmin_[st_key] = std::move(wear_pmin);
  }
  if (!ucols.empty()) {
    status_cols_[st_key] = std::move(ucols);
  }
  if (!block_ucol.empty()) {
    block_status_cols_[st_key] = block_ucol;
  }
  if (!vcols.empty()) {
    startup_cols_[st_key] = std::move(vcols);
  }
  if (!wcols.empty()) {
    shutdown_cols_[st_key] = std::move(wcols);
  }
  if (!lrows.empty()) {
    logic_rows_[st_key] = std::move(lrows);
  }
  if (!gurows.empty()) {
    gen_upper_rows_[st_key] = std::move(gurows);
  }
  if (!glrows.empty()) {
    gen_lower_rows_[st_key] = std::move(glrows);
  }
  if (!erows.empty()) {
    exclusion_rows_[st_key] = std::move(erows);
  }
  if (!rurows.empty()) {
    ramp_up_rows_[st_key] = std::move(rurows);
  }
  if (!rdrows.empty()) {
    ramp_down_rows_[st_key] = std::move(rdrows);
  }

  // Register PAMPL-visible commitment columns so user constraints can
  // reference ``commitment("X").status`` / ``.startup`` / ``.shutdown``.
  // The block-keyed maps were populated above, fanning the period
  // representative column across every block in the period.
  static constexpr auto ampl_name = Element::class_name.snake_case();
  if (!block_ucol.empty()) {
    sc.add_ampl_variable(
        ampl_name, cuid, StatusName, scenario, stage, block_ucol);
  }
  if (!block_vcol.empty()) {
    sc.add_ampl_variable(
        ampl_name, cuid, StartupName, scenario, stage, block_vcol);
  }
  if (!block_wcol.empty()) {
    sc.add_ampl_variable(
        ampl_name, cuid, ShutdownName, scenario, stage, block_wcol);
  }

  return true;
}

bool CommitmentLP::power_based_applies(const StageLP& stage,
                                       bool point,
                                       bool expandable)
{
  const char* why = nullptr;
  if (point) {
    why = "point-sampled stage";
  } else if (expandable) {
    why = "expandable generator";
  }
  if (why == nullptr) {
    return true;
  }
  if (!power_based_warned_) {
    power_based_warned_ = true;
    spdlog::warn(
        "Commitment '{}': uc_formulation power_based is not applied on a "
        "{} (stage {}); the energy-block rows are used",
        commitment().name,
        why,
        stage.uid());
  }
  return false;
}

void CommitmentLP::add_power_based(
    SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp,
    const BIndexHolder<ColIndex>& generation_cols,
    std::span<const std::size_t> block_period,
    std::span<const std::size_t> period_starts,
    std::span<const double> period_real,
    const BIndexHolder<ColIndex>& ucols,
    std::span<const ColIndex> period_vcol,
    std::span<const ColIndex> period_wcol,
    bool cyclic,
    double initial_u,
    bool horizon_start)
{
  const auto& blocks = stage.blocks();
  const auto nblocks = blocks.size();
  // Every block needs its generation column: the trapezoid chains the
  // boundaries, so a P1-elided block (zero pmax) breaks the chain — fall
  // back to its absence as a zero-power block is not representable here.
  if (std::ranges::any_of(blocks,
                          [&](const auto& b)
                          { return !generation_cols.contains(b.uid()); }))
  {
    spdlog::warn(
        "Commitment '{}': power_based needs a generation column in every "
        "block of stage {}; some blocks were elided (zero pmax) — the "
        "boundary rows are skipped there",
        commitment().name,
        stage.uid());
    return;
  }

  PowerBasedUnit unit;
  unit.class_name = Element::class_name.full_name();
  unit.uid = uid();
  unit.scenario_uid = scenario.uid();
  unit.stage_uid = stage.uid();
  unit.cyclic = cyclic;
  unit.initial_u = initial_u;
  unit.has_ramp_up = ramp_up_.has_value();
  unit.has_ramp_down = ramp_down_.has_value();
  const auto& gen = sc.element(generator_index_).generator();
  if (!cyclic) {
    if (commitment().initial_power) {
      unit.initial_power = commitment().initial_power;
    } else if (horizon_start && gen.initial_generation) {
      unit.initial_power = gen.initial_generation;
    }
  }
  unit.startup_trajectory = commitment().startup_trajectory;
  unit.shutdown_trajectory = commitment().shutdown_trajectory;
  unit.trajectory_step = commitment().trajectory_step.value_or(1.0);

  // Reserve provisions of this generator: their headroom rows were gated
  // by u (`g ± r` vs `Pmax·u` / `floor·u`).  The power-based rows carry
  // the reserves at the boundary (PB3–PB6); the headroom rows lose their
  // generation term and keep only the status gate `r ≤ cap·u`.
  std::vector<const ReserveProvisionLP*> provisions;
  for (const auto& rprov : sc.elements<ReserveProvisionLP>()) {
    if (sc.element_index(rprov.generator_sid()) == generator_index_) {
      provisions.push_back(&rprov);
    }
  }

  const auto nperiods = period_starts.size();
  for (std::size_t p = 0; p < nperiods; ++p) {
    const auto rep = blocks[period_starts[p]].uid();
    unit.ucol.push_back(ucols.at(rep));
    unit.vcol.push_back(period_vcol[p]);
    unit.wcol.push_back(period_wcol[p]);
  }
  unit.period_start.assign(nperiods, 0.0);
  for (std::size_t p = 1; p < nperiods; ++p) {
    unit.period_start[p] = unit.period_start[p - 1] + period_real[p - 1];
  }

  for (std::size_t i = 0; i < nblocks; ++i) {
    const auto buid = blocks[i].uid();
    const auto gcol = generation_cols.at(buid);
    const auto pmax = lp.get_col_uppb(gcol);
    const auto pmin =
        pmin_.optval(stage.uid(), buid).value_or(lp.get_col_lowb(gcol));
    unit.buid.push_back(buid);
    unit.gcol.push_back(gcol);
    unit.dt.push_back(stage.real_duration(blocks[i]));
    unit.pmax.push_back(pmax);
    unit.pmin.push_back(pmin);
    unit.ramp_up.push_back(ramp_up_.optval(stage.uid(), buid).value_or(pmax));
    unit.ramp_down.push_back(
        ramp_down_.optval(stage.uid(), buid).value_or(pmax));
    unit.startup_cap.push_back(
        startup_ramp_.optval(stage.uid(), buid).value_or(pmax));
    unit.shutdown_cap.push_back(
        shutdown_ramp_.optval(stage.uid(), buid).value_or(pmax));
    unit.period.push_back(block_period[i]);

    std::vector<ColIndex> rup;
    std::vector<ColIndex> rdn;
    const auto ucol = unit.ucol[block_period[i]];
    for (const auto* rprov : provisions) {
      if (rprov->offline()) {
        continue;  // cold reserve: offered while OFF, no headroom
      }
      if (const auto col =
              rprov->lookup_up_provision_col(scenario, stage, buid))
      {
        rup.push_back(*col);
        if (const auto row =
                rprov->lookup_up_provision_row(scenario, stage, buid))
        {
          // g + r − Pmax·u ≤ 0  →  r − Pmax·u ≤ 0
          lp.set_coeff(*row, gcol, 0.0);
        }
      }
      if (const auto col =
              rprov->lookup_dn_provision_col(scenario, stage, buid))
      {
        rdn.push_back(*col);
        if (const auto row =
                rprov->lookup_dn_provision_row(scenario, stage, buid))
        {
          // g − r − floor·u ≥ 0  →  (P̄ − P)·u − r ≥ 0
          lp.set_coeff(*row, gcol, 0.0);
          lp.set_coeff(*row, ucol, std::max(pmax - pmin, 0.0));
          lp.row_at(*row).lowb = 0.0;
        }
      }
    }
    unit.reserve_up.push_back(std::move(rup));
    unit.reserve_dn.push_back(std::move(rdn));
  }

  auto res = add_power_based_rows(unit, lp);
  BIndexHolder<ColIndex> pcols;
  map_reserve(pcols, nblocks);
  for (std::size_t i = 0; i < res.power.size(); ++i) {
    pcols[blocks[i].uid()] = res.power[i];
  }
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  if (!pcols.empty()) {
    sc.add_ampl_variable(Element::class_name.snake_case(),
                         uid(),
                         PowerName,
                         scenario,
                         stage,
                         pcols);
    power_cols_[st_key] = std::move(pcols);
  }
}

bool CommitmentLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();
  // A relaxed element is continuous in [0, 1]: write the LP value as is.
  const bool relaxed = commitment().relax.value_or(false);

  // ``status`` / ``startup`` / ``shutdown`` are binary LP
  // variables — emit via the integer-snapping overload so the
  // output parquet carries exact 0/1 values.  See
  // ``OutputContext::add_col_sol_integer`` for the rationale.
  out.add_col_sol_integer(cname, StatusName, pid, status_cols_, relaxed);
  out.add_col_cost(cname, StatusName, pid, status_cols_);
  out.add_col_sol_integer(cname, StartupName, pid, startup_cols_, relaxed);
  out.add_col_cost(cname, StartupName, pid, startup_cols_);
  out.add_col_sol_integer(cname, ShutdownName, pid, shutdown_cols_, relaxed);
  out.add_col_cost(cname, ShutdownName, pid, shutdown_cols_);

  // Max-starts cap row duals (one per scope window, indexed by the
  // window-ending block).  Empty when ``commitment.max_starts`` is
  // unset; the dual surfaces the shadow $-per-extra-startup the LP
  // would pay if the cap were relaxed by 1 event, useful for
  // sensitivity work on cycling-limited units.
  out.add_row_dual(cname, MaxStartsName, pid, max_starts_rows_);
  // power_based: instantaneous power at the end of every block [MW]
  out.add_col_sol(cname, PowerName, pid, power_cols_);

  out.add_row_dual(cname, LogicName, pid, logic_rows_);
  out.add_row_dual(cname, GenUpperName, pid, gen_upper_rows_);
  out.add_row_dual(cname, GenLowerName, pid, gen_lower_rows_);
  out.add_row_dual(cname, ExclusionName, pid, exclusion_rows_);
  out.add_row_dual(cname, RampUpName, pid, ramp_up_rows_);
  out.add_row_dual(cname, RampDownName, pid, ramp_down_rows_);

  // Segment col/row outputs moved to GeneratorLP::add_to_output —
  // piecewise heat-rate slacks now live there alongside the gen
  // columns they augment.

  out.add_row_dual(cname, MinUpTimeName, pid, min_up_time_rows_);
  out.add_row_dual(cname, MinDownTimeName, pid, min_down_time_rows_);

  out.add_col_sol(cname, HotStartName, pid, hot_start_cols_);
  out.add_col_cost(cname, HotStartName, pid, hot_start_cols_);
  out.add_col_sol(cname, WarmStartName, pid, warm_start_cols_);
  out.add_col_cost(cname, WarmStartName, pid, warm_start_cols_);
  out.add_col_sol(cname, ColdStartName, pid, cold_start_cols_);
  out.add_col_cost(cname, ColdStartName, pid, cold_start_cols_);
  out.add_row_dual(cname, StartupTypeName, pid, startup_type_rows_);
  out.add_row_dual(cname, HotStartName, pid, hot_start_rows_);
  out.add_row_dual(cname, WarmStartName, pid, warm_start_rows_);

  // ── Usage accounting (`options.output_usage_metrics`) ─────────────
  //   Commitment/usage_pmin_sol    — committed floor [MW] while ON
  //                                  (`committed_pmin`), so a
  //                                  post-processor can apply any
  //                                  "near pmin" band;
  //   Commitment/usage_at_pmin_sol — u when the unit sits at its floor
  //                                  (p ≤ Pmin·u + tolerance), else 0;
  //                                  × block hours = hours at pmin.
  if (out.options().output_usage_metrics()) {
    add_usage_to_output(out);
  }
  if (!startup_wear_coefs_.empty() || !wear_a_.empty()) {
    add_wear_to_output(out);
  }

  return true;
}

void CommitmentLP::add_wear_to_output(OutputContext& out) const
{
  // Operation wear costs, split from the other commitment costs:
  //   Commitment/startup_wear_cost_sol   — wear × v  [$ per start event]
  //                                        at the period's first block;
  //   Commitment/operation_wear_cost_sol — a·u + b·(p − Pmin·u)  [$/h].
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto& sc = out.system_context();
  const auto& gen = sc.element(generator_index_);

  STBIndexHolder<double> start_values;
  for (const auto& [st_key, coefs] : startup_wear_coefs_) {
    const auto cit = startup_cols_.find(st_key);
    if (cit == startup_cols_.end()) {
      continue;
    }
    for (const auto& [buid, coef] : coefs) {
      if (const auto vit = cit->second.find(buid); vit != cit->second.end()) {
        start_values[st_key][buid] = coef * out.primal(vit->second);
      }
    }
  }
  STBIndexHolder<double> op_values;
  const auto& sys = sc.system();
  for (const auto& scenario : sys.scene().scenarios()) {
    for (const auto& stage : sys.phase().stages()) {
      const auto st_key = std::tuple {scenario.uid(), stage.uid()};
      const auto ait = wear_a_.find(st_key);
      if (ait == wear_a_.end()) {
        continue;
      }
      const auto& bvals = wear_b_.at(st_key);
      const auto& pvals = wear_pmin_.at(st_key);
      for (const auto& [buid, a] : ait->second) {
        const auto ucol = lookup_status_col(scenario, stage, buid);
        const auto gcol = gen.lookup_generation_col(scenario, stage, buid);
        if (!ucol || !gcol) {
          continue;
        }
        const double u = out.primal(*ucol);
        const double p = out.primal(*gcol);
        op_values[st_key][buid] =
            (a * u) + (bvals.at(buid) * (p - (pvals.at(buid) * u)));
      }
    }
  }
  out.add_col_sol_values(cname, StartupWearCostName, id(), start_values);
  out.add_col_sol_values(cname, OperationWearCostName, id(), op_values);
}

void CommitmentLP::add_usage_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto& sc = out.system_context();
  const auto& sys = sc.system();
  const auto& gen = sc.element(generator_index_);

  STBIndexHolder<double> pmin_values;
  STBIndexHolder<double> at_pmin_values;
  for (const auto& scenario : sys.scene().scenarios()) {
    for (const auto& stage : sys.phase().stages()) {
      const auto st_key = std::tuple {scenario.uid(), stage.uid()};
      for (const auto& block : stage.blocks()) {
        const auto buid = block.uid();
        const auto ucol = lookup_status_col(scenario, stage, buid);
        if (!ucol) {
          continue;
        }
        const double u = out.primal(*ucol);
        // Floor of a committed unit: the larger of the generator pmin
        // (scaled by a scheduled maintenance outage) and the
        // commitment's own pmin.
        const double pmin =
            std::max(gen.param_pmin(stage.uid(), buid).value_or(0.0)
                         * gen.available_fraction_at(scenario, stage, buid),
                     committed_pmin(stage.uid(), buid).value_or(0.0));
        const auto gcol = gen.lookup_generation_col(scenario, stage, buid);
        const double p = gcol ? out.primal(*gcol) : 0.0;
        pmin_values[st_key][buid] = pmin;
        const double tol =
            usage_at_pmin_abs_tol + (usage_at_pmin_rel_tol * pmin);
        const bool at_floor = u > usage_on_status && p <= (pmin * u) + tol;
        at_pmin_values[st_key][buid] = at_floor ? u : 0.0;
      }
    }
  }
  out.add_col_sol_values(cname, UsagePminName, id(), pmin_values);
  out.add_col_sol_values(cname, UsageAtPminName, id(), at_pmin_values);
}

auto find_unit_commitment(const SystemContext& sc,
                          ElementIndex<GeneratorLP> gen_index,
                          const StageLP& stage) -> const CommitmentLP*
{
  for (const auto& cm : sc.elements<CommitmentLP>()) {
    if (cm.is_active(stage)
        && sc.element_index(cm.generator_sid()) == gen_index)
    {
      return &cm;
    }
  }
  return nullptr;
}

auto committed_pmin(const CommitmentLP* cmt,
                    const GeneratorLP& gen,
                    StageUid stage_uid,
                    BlockUid block_uid) -> double
{
  const auto gen_pmin = gen.param_pmin(stage_uid, block_uid).value_or(0.0);
  if (cmt == nullptr) {
    return gen_pmin;
  }
  return std::max(gen_pmin,
                  cmt->committed_pmin(stage_uid, block_uid).value_or(0.0));
}

auto find_noload_fuel_commitment(const SystemContext& sc,
                                 const GeneratorLP& gen) -> const CommitmentLP*
{
  for (const auto& cmt : sc.elements<CommitmentLP>()) {
    if (cmt.commitment().noload_fuel.value_or(0.0) == 0.0) {
      continue;
    }
    if (sc.element<GeneratorLP>(cmt.generator_sid()).uid() == gen.uid()) {
      return &cmt;
    }
  }
  return nullptr;
}

auto generator_fuel_burn_legs(const GeneratorLP& gen,
                              const CommitmentLP* cmt,
                              const ScenarioLP& scenario,
                              const StageLP& stage,
                              BlockUid buid)
    -> std::vector<std::pair<ColIndex, double>>
{
  auto legs = gen.fuel_burn_legs(scenario, stage, buid);
  if (cmt != nullptr) {
    if (const auto leg = cmt->noload_fuel_leg(scenario, stage, buid)) {
      legs.push_back(*leg);
    }
  }
  return legs;
}

}  // namespace gtopt
