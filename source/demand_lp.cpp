/**
 * @file      demand_lp.cpp
 * @brief     Implementation of demand LP formulation
 * @date      Thu Mar 27 22:31:31 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module implements DemandLP construction and add_to_lp, which builds
 * LP variables and constraints for load, curtailment, and energy limits.
 */
#include <algorithm>
#include <format>
#include <numeric>
#include <stdexcept>

#include <gtopt/converter_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/input_context.hpp>
#include <gtopt/lindistflow.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/pump_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

DemandLP::DemandLP(const Demand& pdemand, const InputContext& ic)
    : CapacityBase(pdemand, ic, Element::class_name)
    , lmax(ic, Element::class_name, id(), std::move(object().lmax))
    , lmin(ic, Element::class_name, id(), std::move(object().lmin))
    , lossfactor(ic, Element::class_name, id(), std::move(object().lossfactor))
    , fcost(ic, Element::class_name, id(), std::move(object().fcost))
    , emin(ic, Element::class_name, id(), std::move(object().emin))
    , ecost(ic, Element::class_name, id(), std::move(object().ecost))
{
  auto& segs = object().fail_segments;
  if (segs.size() > max_fail_segments) {
    throw std::invalid_argument(
        std::format("Demand '{}': fail_segments has {} segments (max {})",
                    id().second,
                    segs.size(),
                    max_fail_segments));
  }
  if (!segs.empty()) {
    own_seg_widths_ =
        resolve_fail_segment_widths(std::span<const FailSegment> {segs});
    seg_costs_.reserve(segs.size());
    for (auto& seg : segs) {
      seg_costs_.emplace_back(
          ic, Element::class_name, id(), std::move(seg.cost));
    }
  }
  SPDLOG_DEBUG("DemandLP created: uid={} name='{}'", id().first, id().second);
}

namespace
{
/// One served-load tranche of a (possibly single-segment) failure curve
/// at a given stage: width as a fraction of `lmax` and cost [$/MWh].
struct FailTranche
{
  double width {1.0};
  double cost {0.0};
};

/// Index of the most expensive tranche (ties -> the later one).  It
/// absorbs the firm remainder and receives the served-load floor first.
[[nodiscard]] std::size_t top_tranche(std::span<const FailTranche> t) noexcept
{
  std::size_t top = 0;
  for (std::size_t k = 1; k < t.size(); ++k) {
    if (t[k].cost >= t[top].cost) {
      top = k;
    }
  }
  return top;
}
}  // namespace

double DemandLP::fail_sol_at(const ScenarioLP& scenario,
                             const StageLP& stage,
                             const BlockLP& block,
                             const ScaledView& col_sol) const noexcept
{
  // Reconstruct `fail_sol = max(0, lmax − load_sol)` from cached
  // `block_lmax_values_` and the surviving `load_cols` primal value.
  // Pre-P0 callers used `lp.col_sol[fail_col]` for the same value;
  // this method returns the algebraically-equivalent quantity from
  // the post-substitution LP.  See `add_to_output` for the
  // companion bulk-emit site that produces `Demand/fail_sol.csv`.
  //
  // Unpenalized demands (fcost=0, e.g. the battery-charge `<name>_dem`)
  // have no real curtailment — `fail = lmax − load` is just unused
  // dispatchable load capacity.  Report 0 so the SDDP `unserved_demand`
  // convergence indicator is not polluted (matches the suppressed
  // `fail_sol` output in `add_to_output`).
  if (!fail_penalized_) {
    return 0.0;
  }
  const std::tuple st_key {scenario.uid(), stage.uid()};
  const auto lmax_it = block_lmax_values_.find(st_key);
  if (lmax_it == block_lmax_values_.end()) {
    return 0.0;
  }
  const auto lmax_block_it = lmax_it->second.find(block.uid());
  if (lmax_block_it == lmax_it->second.end()) {
    return 0.0;
  }
  const auto lc_it = load_cols.find(st_key);
  if (lc_it == load_cols.end()) {
    return 0.0;
  }
  const auto lc_block_it = lc_it->second.find(block.uid());
  if (lc_block_it == lc_it->second.end()) {
    return 0.0;
  }
  // Σ over the tranche columns (stepwise curve) or the single load col.
  double col_sum = 0.0;
  if (const auto segs = load_segment_cols(scenario, stage, block.uid());
      !segs.empty())
  {
    for (const auto& col : segs) {
      col_sum += col_sol[col];
    }
  } else {
    col_sum = col_sol[lc_block_it->second];
  }
  // Option C: the columns carry `served_k − u_k`, so `fail = −Σ col`.
  if (const auto off_it = block_offset_values_.find(st_key);
      off_it != block_offset_values_.end()
      && off_it->second.contains(block.uid()))
  {
    return std::max(0.0, -col_sum);
  }
  return std::max(0.0, lmax_block_it->second - col_sum);
}

bool DemandLP::is_coupled_load(const SystemContext& sc)
{
  if (!m_coupled_load_.has_value()) {
    const auto is_self = [&](const auto& sid)
    {
      try {
        return &sc.element<DemandLP>(sid) == this;
      } catch (const std::exception&) {
        return false;  // partner not in this LP
      }
    };
    m_coupled_load_ = std::ranges::any_of(sc.elements<ConverterLP>(),
                                          [&](const auto& c)
                                          { return is_self(c.demand_sid()); })
        || std::ranges::any_of(sc.elements<PumpLP>(),
                               [&](const auto& p)
                               { return is_self(p.demand_sid()); });
  }
  return *m_coupled_load_;
}

bool DemandLP::add_to_lp(SystemContext& sc,
                         const ScenarioLP& scenario,
                         const StageLP& stage,
                         LinearProblem& lp)
{
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!CapacityBase::add_to_lp(sc, ampl_name, scenario, stage, lp)) {
    return false;
  }

  // Resolve via `sc.element<BusLP>` (handles both Uid and Name forms
  // of the JSON-side `bus` SingleId variant — `std::get<Uid>` would
  // throw if the JSON used a string name).
  const auto& bus_lp = sc.element<BusLP>(bus_sid());

  // F9: register filter metadata so sum(...) predicates work.
  {
    AmplElementMetadata metadata;
    metadata.reserve(2);
    if (const auto& t = demand().type) {
      metadata.emplace_back(TypeKey, *t);
    }
    metadata.emplace_back(BusKey, ampl_metadata_uid(bus_lp.uid()));
    sc.register_ampl_element_metadata(ampl_name, uid(), std::move(metadata));
  }

  if (!is_active(stage)) [[unlikely]] {
    return true;
  }

  if (!bus_lp.is_active(stage)) [[unlikely]] {
    return true;
  }

  const auto [opt_capacity, capacity_col] = capacity_and_col(stage, lp);
  const double stage_capacity = opt_capacity.value_or(LinearProblem::DblMax);

  const auto& bus_balance_rows = bus_lp.balance_rows_at(scenario, stage);
  const auto& blocks = stage.blocks();
  // LinDistFlow: reactive load `ratio · served` (empty under DC).
  const auto& bus_qbalance_rows = bus_lp.qbalance_rows_at(scenario, stage);
  const double q_ratio = reactive_ratio(demand().power_factor.value_or(1.0));

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const bool is_forced = demand().forced.value_or(false);

  // ── Stepwise failure curve (fail_segments) ───────────────────────
  //
  // Precedence (per demand): `forced` → no failure; own
  // `Demand.fail_segments` → segments; own `Demand.fcost` → single
  // tranche; global `model_options.demand_fail_segments` → segments;
  // global `model_options.demand_fail_cost` (default 1000 $/MWh) →
  // single tranche.  Only a forced demand skips the substitution.
  //
  // The curve is modelled by splitting the served load into K tranche
  // columns `served_k ∈ [lb_k, u_k]`, `u_k = width_k × lmax`, each with
  // cost `−cost_k` and the `+cost_k × u_k` baseline on the objective
  // constant (the per-tranche version of the P0 substitution
  // `fail_k = u_k − served_k`).  No extra rows: every tranche enters
  // the bus-balance (and capacity) row with the same coefficient as the
  // single load column.  With non-decreasing costs the LP curtails the
  // cheapest tranche first, i.e. it realises the convex stepwise VoLL
  // curve exactly.  K = 1 reproduces the single `load` column.
  const auto global_segs = sc.options().demand_fail_segments();
  const bool own_segs = !seg_costs_.empty();
  const bool use_global_segs =
      !own_segs && !fcost.has_value() && !global_segs.empty();
  const bool segmented_curve = !is_forced && (own_segs || use_global_segs);
  std::vector<FailTranche> stage_tranches;
  double stage_firm = 0.0;
  if (segmented_curve) {
    if (own_segs) {
      stage_firm = own_seg_widths_.firm;
      stage_tranches.reserve(seg_costs_.size());
      for (std::size_t k = 0; k < seg_costs_.size(); ++k) {
        stage_tranches.push_back({
            .width = own_seg_widths_.widths[k],
            .cost = seg_costs_[k]
                        .optval(stage.uid())
                        .value_or(sc.options().demand_fail_cost()),
        });
      }
    } else {
      const auto widths = resolve_fail_segment_widths(global_segs);
      stage_firm = widths.firm;
      stage_tranches.reserve(global_segs.size());
      for (std::size_t k = 0; k < global_segs.size(); ++k) {
        stage_tranches.push_back({
            .width = widths.widths[k],
            .cost = global_segs[k].cost.value_or(0.0),
        });
      }
    }
  }
  const bool multi_tranche = stage_tranches.size() > 1;

  // adding the minimum energy constraint
  const auto stage_emin = emin.optval(stage.uid());
  auto stage_ecost = ecost.optval(stage.uid());
  // ecost fallback under a stepwise curve: the most expensive segment.
  if (!stage_ecost && !stage_tranches.empty()) {
    stage_ecost = stage_tranches[top_tranche(stage_tranches)].cost;
  }
  // ecost fallback: when `Demand.ecost` is unset, reuse `fcost` sampled
  // at the first block of the stage.  `fcost` is now per-(stage, block);
  // the stage-level emin/ecost feature is itself per-stage, so we sample
  // a single representative value (matches legacy semantics for scalar
  // fcost; for true per-block fcost only the first block's value drives
  // ecost — the per-block cost still applies to the load column).
  // When `fcost` is unset too, fall back to the global
  // `model_options.demand_fail_cost` (default 1000 $/MWh).
  if (!stage_ecost) {
    stage_ecost = (fcost.has_value() && !blocks.empty())
        ? sc.demand_fail_cost(stage, blocks.front(), fcost)
        : sc.options().demand_fail_cost();
  }

  // ── emin aggregator collapse ─────────────────────────────────────
  //
  // Pre-collapse: a stage-level `emin_col` (variable) + an equality
  // row `sum_b bdur*mcol[b] − emin_col = 0` glued the per-block
  // `mcol[b]` (lman) variables to one stage aggregator.
  // Post-collapse: the aggregator is gone — `emin_col` and the
  // gluing row are both deleted.  In their place:
  //
  //   * `mcol[b].cost = −c_per_unit × bdur` (soft, ecost set), where
  //     `c_per_unit = scenario_stage_ecost(s, t, stage_ecost /
  //     stage.duration())`. The negative coefficient rewards drawing more
  //     energy.
  //
  //   * A single stage-level row replaces the aggregator-gluing row:
  //         sum_b bdur*mcol[b] ≤ stage_emin   (soft, ecost set)
  //         sum_b bdur*mcol[b] = stage_emin   (hard, no ecost)
  //     Same semantic as the pre-collapse `emin_col ∈ [0, stage_emin]`
  //     (soft) / `emin_col = stage_emin` (hard) — the aggregator was
  //     redundant tautological glue.
  //
  // Saves 1 column per (scenario, stage) per demand with emin
  // active.  Algebraically identical: pre-collapse obj contribution
  // was `−c × emin_col_optimal = −c × stage_emin` (soft) / `0` (hard);
  // post-collapse it's `Σ_b −c × bdur × mcol[b] = −c × stage_emin`
  // (soft) / `0` (hard) — same.
  auto emin_row = [&](const auto& stage_emin,
                      const auto& stage_ecost) -> std::optional<SparseRow>
  {
    if (!stage_emin) [[unlikely]] {
      return std::nullopt;
    }
    auto row = SparseRow {
        .class_name = Element::class_name.full_name(),
        .constraint_name = EminName,
        .variable_uid = uid(),
        .context = make_stage_context(scenario.uid(), stage.uid()),
    };
    if (stage_ecost) {
      // Soft: total energy ≤ stage_emin (the per-mcol cost provides
      // the incentive to push the total up to the cap).
      row.less_equal(*stage_emin);
    } else {
      // Hard: total energy exactly stage_emin.  No incentive cost —
      // the LP must hit the floor.
      row.equal(*stage_emin);
    }
    return row;
  }(stage_emin, stage_ecost);

  // Per-block lman-cost coefficient applied directly to each mcol
  // (soft path).  Zero when `stage_ecost` is unset (hard path) —
  // the LP must hit `stage_emin` via the equality row instead.
  const double per_unit_neg_cost = (stage_ecost && stage_emin)
      ? -CostHelper::scenario_stage_ecost(
            scenario, stage, *stage_ecost / stage.duration())
      : 0.0;

  BIndexHolder<ColIndex> lcols;
  BIndexHolder<ColIndex> mcols;
  BIndexHolder<RowIndex> crows;
  BIndexHolder<double> block_lmaxs;  // P0 reconstruction cache:
                                     // post-capacity-clamp `lmax` per block
  BIndexHolder<double> block_offsets;  // Option C: AMPL offset map
                                       // = block_lmax so user constraints
                                       // resolve `demand.load` -> col + lmax
  BIndexHolder<std::vector<ColIndex>> scols;  // K ≥ 2 tranche cols
  BIndexHolder<std::vector<double>> scaps;  // K ≥ 2 tranche caps u_k
  // AMPL `demand.fail = Σ −col_k + offset` (Option A offset = lmax).
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> fail_terms;
  BIndexHolder<double> fail_offsets;
  // AMPL `demand.load = Σ col_k (+ lmax under Option C)` for K ≥ 2.
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> load_terms;
  // Option C (experimental, opt-in via
  // `model_options.demand_fail_rhs_shift`; legacy
  // `demand_option_c` accepted as alias via naming-dialects).
  // When false (default): Option A — col represents `load`, cost
  // `−fail_cost × ecost`, and the `+fail_cost × ecost × lmax`
  // baseline rides on `lp.add_obj_constant(...)`.
  //
  // When true: col represents `neg_fail = load − lmax ∈ [−lmax, 0]`,
  // `obj_constant` stays at 0, and the baseline is absorbed by RHS
  // shifts on the bus-balance row (`+(1+loss)·lmax`) and capacity
  // row (`+lmax`).  The AMPL resolver receives a per-block offset
  // so user constraints referencing `demand.load` resolve to
  // `(col + lmax)` physically (via `param_shift`).
  //
  // Coupled loads (a Converter's battery-charge demand, a Pump's
  // demand) stay on Option A: those elements write rows on the load
  // column itself (`load = rate · finp`, `cap ≥ g + load`, envelope
  // and u-gating rows built from the column bounds), so the column
  // must carry `load`, not `load − lmax`.
  const bool option_c =
      sc.options().demand_fail_rhs_shift() && !is_coupled_load(sc);
  // `fcost` is now per-(stage, block); every non-forced demand is
  // substituted because the global `model_options.demand_fail_cost`
  // fallback always resolves (default 1000 $/MWh).  Per-block
  // resolution happens inside the block loop via
  // `sc.demand_fail_cost(stage, block, fcost)`.
  const bool fail_substituted = !is_forced;
  const bool use_option_c = fail_substituted && option_c;

  // Reserve only the holders that will actually be populated in this
  // call.  `block_lmaxs` is always populated for blocks with non-zero
  // demand (one entry per block in the load path), so reserve it
  // up-front; the others stay guarded.
  map_reserve(lcols, blocks.size());
  map_reserve(block_lmaxs, blocks.size());
  if (use_option_c) {
    map_reserve(block_offsets, blocks.size());
  }
  if (stage_emin) {
    map_reserve(mcols, blocks.size());
  }
  if (capacity_col) {
    map_reserve(crows, blocks.size());
  }

  for (const auto& block : blocks) {
    const auto buid = block.uid();
    // A negative `lmax` (e.g. a UC.jl bus with net injection) serves
    // nothing: it is a zero-load block, never an `[0, lmax < 0]` column
    // (an infeasible bound pair) — the same optimum as its elision.
    const auto block_lmax =
        std::max(0.0, sc.block_max_at(stage, block, lmax, stage_capacity));
    const auto block_lossfactor =
        lossfactor.optval(stage.uid(), buid).value_or(0.0);
    // `lp_reduction`: when block_lmax == 0 the load column is fixed at
    // zero and is not built; when, in addition, no emin (lman) work is
    // needed for this block, the entire iteration is degenerate (lcol,
    // fcol, frow, crow all collapse) and is skipped eagerly.  Off (the
    // default) the [0, 0] load column stays, like every other element's.
    const bool has_load = block_lmax > 0.0 || !sc.options().lp_reduction();
    const bool has_lman = stage_emin && emin_row;
    if (!has_load && !has_lman) [[unlikely]] {
      continue;
    }

    const auto bus_balance_row = bus_balance_rows.at(buid);
    // Hoisted once per block: every `lp.add_col` / `SparseRow{...}`
    // initialiser below shares the same (scenario, stage, block)
    // context.  Pre-2026-05-14 the inner blocks re-built it 5×.
    const auto block_ctx =
        make_block_context(scenario.uid(), stage.uid(), block.uid());
    auto& bus_brow = lp.row_at(bus_balance_row);

    // ── P0 demand-failure substitution ──────────────────────────
    //
    // Pre-substitution: `lcol` (cost 0, bounds [0, lmax]) + `fcol`
    // (cost = fail_cost × ecost, bounds [0, ∞]) + balance row
    // `lcol + fcol = lmax`.
    //
    // Option A (default): substitute `fail = lmax − load`:
    //   * `lcol.cost = −fail_cost × ecost`,
    //   * `lp.add_obj_constant(+fail_cost × ecost × lmax)`,
    //   * bus_brow / capacity row RHS = 0 (default).
    //
    // Option C (`model_options.demand_fail_rhs_shift = true`): substitute
    // `neg_fail = load − lmax`:
    //   * `lcol.cost = −fail_cost × ecost` (same),
    //   * `lcol.bounds = [-lmax, 0]`,
    //   * `obj_constant = 0`,
    //   * bus_brow RHS shifts `+(1+loss)·lmax`,
    //   * capacity row RHS shifts `+lmax`,
    //   * AMPL registers offset = lmax so user constraints
    //     referencing `demand.load` resolve to (col + lmax).
    //
    // Both options preserve the algebraic obj value to within FP.
    // Option C eliminates ~$105 B obj_constant cancellation on
    // CEN-scale runs but is currently incompatible with LP-side
    // consumers that reference the demand column directly
    // (Converter, Battery) — they expect `load` semantics.
    //
    // When block_lmax == 0 every entry below is a no-op (zero
    // coefficients, zero RHS shift, zero cost contribution).
    if (has_load) {
      // ``lmin`` provides a HARD floor on served load (per-(stage, block));
      // used to propagate ``Battery.pmin_charge`` onto the synthetic
      // charge demand so the LP must charge at least that rate every
      // block.  Floor at 0 (no negative floors).
      // A zero-lmax block serves nothing, whatever `lmin` says (exactly
      // as when `lp_reduction` elides it).
      const auto block_lmin = block_lmax > 0.0
          ? std::max(0.0, lmin.optval(stage.uid(), buid).value_or(0.0))
          : 0.0;

      // Tranche list for this block: the stage curve (K ≥ 1 segments)
      // or a single tranche carrying the per-block `fcost` fallback.
      std::vector<FailTranche> tranches;
      double firm = 0.0;
      if (fail_substituted) {
        if (!stage_tranches.empty()) {
          tranches = stage_tranches;
          firm = stage_firm;
        } else {
          tranches.push_back({
              .width = 1.0,
              .cost = sc.demand_fail_cost(stage, block, fcost),
          });
        }
      } else {
        // Forced demand: `load` column with zero cost (fixed at lmax).
        tranches.push_back({.width = 1.0, .cost = 0.0});
      }
      const auto ntr = tranches.size();
      const auto top = top_tranche(tranches);

      // Tranche upper bounds `u_k`; the firm remainder rides on the
      // most expensive tranche so Σ u_k == lmax.
      std::vector<double> caps(ntr);
      for (std::size_t k = 0; k < ntr; ++k) {
        caps[k] = tranches[k].width * block_lmax;
      }
      caps[top] += firm * block_lmax;

      // Served-load floor `F = max(lmin, firm × lmax, forced ? lmax : 0)`
      // distributed greedily from the most expensive tranche down —
      // exact for a convex curve, since tranches are perfect
      // substitutes in every row and the LP curtails the cheapest first.
      const double floor_load = std::max(
          {block_lmin, firm * block_lmax, is_forced ? block_lmax : 0.0});
      std::vector<double> lows(ntr, 0.0);
      if (ntr == 1) {
        lows[0] = floor_load;
      } else {
        std::vector<std::size_t> order(ntr);
        std::ranges::iota(order, std::size_t {0});
        std::ranges::stable_sort(order,
                                 [&](std::size_t a, std::size_t b)
                                 {
                                   if (tranches[a].cost != tranches[b].cost) {
                                     return tranches[a].cost > tranches[b].cost;
                                   }
                                   return a > b;
                                 });
        double rest = floor_load;
        for (const auto k : order) {
          lows[k] = std::min(rest, caps[k]);
          rest -= lows[k];
        }
        lows[top] += std::max(0.0, rest);  // lmin > lmax: keep infeasible
      }

      std::vector<ColIndex> tcols;
      tcols.reserve(ntr);
      auto& fterms = fail_terms[buid];
      fterms.reserve(ntr);
      for (std::size_t k = 0; k < ntr; ++k) {
        double col_lowb = lows[k];
        double col_uppb = caps[k];
        double col_cost = 0.0;
        if (fail_substituted) {
          const auto block_ecost =
              CostHelper::block_ecost(scenario, stage, block, tranches[k].cost);
          col_cost = -block_ecost;
          if (block_ecost > 0.0) {
            fail_penalized_ = true;
          }
          if (use_option_c) {
            // Option C: col represents `served_k − u_k ∈ [lb_k − u_k, 0]`.
            col_lowb = (k == 0 && ntr == 1) ? -block_lmax : lows[k] - caps[k];
            col_uppb = 0.0;
          } else {
            // Option A: col represents served_k ∈ [lb_k, u_k].
            // obj_constant carries the +fail_cost × ecost × u_k
            // baseline so `get_obj_value()` matches the
            // algebraically-original pre-substitution objective.
            lp.add_obj_constant(block_ecost * caps[k]);
          }
        }
        const auto tcol = lp.add_col({
            .lowb = col_lowb,
            .uppb = col_uppb,
            .cost = col_cost,
            .class_name = Element::class_name.full_name(),
            .variable_name = (k == 0) ? LoadName : LoadSegName,
            .variable_uid = uid(),
            .context = (k == 0)
                ? LpContext {block_ctx}
                : LpContext {make_block_context(scenario.uid(),
                                                stage.uid(),
                                                block.uid(),
                                                static_cast<int>(k + 1))},
        });
        tcols.push_back(tcol);
        bus_brow[tcol] = -(1.0 + block_lossfactor);
        fterms.emplace_back(tcol, -1.0);
      }
      const auto lcol = tcols.front();

      lcols[buid] = lcol;
      // Cache the post-capacity-clamp `lmax` so `add_to_output` can
      // reconstruct `load` / `fail` without re-walking `block_max_at`.
      block_lmaxs[buid] = block_lmax;
      if (!use_option_c) {
        fail_offsets[buid] = block_lmax;
      }

      if (use_option_c) {
        // AMPL offset = lmax so `demand.load = col + lmax` for
        // user constraints (see ResolvedCol::offset).
        block_offsets[buid] = block_lmax;
        // Bus balance RHS shift: +(1+loss)·lmax per elastic-demand
        // block (Σ u_k == lmax).  A strict balance is an equality row
        // (default lowb == uppb == 0), so we shift both bounds equally.
        // Under `model_options.allow_oversupply` BusLP built the row as
        // `≥` (uppb already unbounded); shifting only the lower bound
        // keeps it `generation ≥ demand`, so guard the upper shift.
        const double bus_rhs_shift = (1.0 + block_lossfactor) * block_lmax;
        bus_brow.lowb += bus_rhs_shift;
        if (bus_brow.uppb < LinearProblem::DblMax) {
          bus_brow.uppb += bus_rhs_shift;
        }
      }

      // ── LinDistFlow reactive load: −ratio · served in the Q row ───
      // served = Σ col_k (+ lmax under Option C, moved to the RHS).
      if (const auto qit = bus_qbalance_rows.find(buid);
          q_ratio != 0.0 && qit != bus_qbalance_rows.end())
      {
        auto& qrow = lp.row_at(qit->second);
        for (const auto tcol : tcols) {
          qrow[tcol] = -q_ratio;
        }
        if (use_option_c) {
          qrow.lowb += q_ratio * block_lmax;
          qrow.uppb += q_ratio * block_lmax;
        }
      }

      // adding the capacity constraint
      if (capacity_col) {
        // Option C: `cap_col − Σ col_k ≥ lmax` (RHS shift +lmax).
        // Option A / non-substituted: `cap_col − Σ served_k ≥ 0`.
        const double crow_rhs = use_option_c ? block_lmax : 0.0;
        auto crow =
            SparseRow {
                .class_name = Element::class_name.full_name(),
                .constraint_name = CapacityName,
                .variable_uid = uid(),
                .context = block_ctx,
            }
                .greater_equal(crow_rhs);

        crow[*capacity_col] = 1.0;
        for (const auto tcol : tcols) {
          crow[tcol] = -1.0;
        }

        crows[buid] = lp.add_row(std::move(crow));
      }

      if (multi_tranche) {
        auto& lterms = load_terms[buid];
        lterms.reserve(ntr);
        for (const auto tcol : tcols) {
          lterms.emplace_back(tcol, 1.0);
        }
        scaps[buid] = std::move(caps);
        scols[buid] = std::move(tcols);
      }
    }

    // adding the minimum energy constraint (lman is independent of
    // lmax — represents extra load drawn to satisfy the stage-level
    // emin floor, valid even when natural demand is zero).  Post-
    // collapse `mcol[b]` directly carries the soft-emin reward cost
    // `−c × bdur` (zero on the hard path); the stage-level emin row
    // built above caps `sum_b bdur*mcol[b]` at `stage_emin`.
    if (has_lman) {
      const auto bdur = block.duration();

      const auto mcol = lp.add_col({
          .uppb = *stage_emin / bdur,
          .cost = per_unit_neg_cost * bdur,
          .class_name = Element::class_name.full_name(),
          .variable_name = LmanName,
          .variable_uid = uid(),
          .context = block_ctx,
      });

      mcols[buid] = mcol;
      (*emin_row)[mcol] = bdur;

      bus_brow[mcol] = -(1.0 + block_lossfactor);
    }
  }

  if (emin_row) {
    emin_rows[st_key] = lp.add_row(std::move(*emin_row));
    lman_cols[st_key] = std::move(mcols);
  }

  if (!lcols.empty()) {
    load_cols[st_key] = std::move(lcols);
    // `demand.fail` (always a virtual sum, no LP column): Σ −col_k plus
    // the per-block offset `lmax` under Option A (0 under Option C).
    if (fail_substituted) {
      sc.add_ampl_variable(ampl_name,
                           uid(),
                           FailName,
                           scenario,
                           stage,
                           fail_terms,
                           fail_offsets);
    }
  }
  if (!scols.empty()) {
    // Stepwise curve (K ≥ 2): `demand.load` = Σ_k col_k (+ lmax under
    // Option C) — a virtual sum over the tranche columns.
    seg_cols_[st_key] = std::move(scols);
    seg_caps_[st_key] = std::move(scaps);
    sc.add_ampl_variable(
        ampl_name,
        uid(),
        LoadName,
        scenario,
        stage,
        load_terms,
        use_option_c ? block_offsets : BIndexHolder<double> {});
  } else if (load_cols.contains(st_key)) {
    if (!block_offsets.empty()) {
      // Option C: col represents `neg_fail = load − lmax`; register
      // the per-block offset so the AMPL resolver reports
      // `demand.load = col + lmax` for user constraints.
      sc.add_ampl_variable(ampl_name,
                           uid(),
                           LoadName,
                           scenario,
                           stage,
                           load_cols.at(st_key),
                           block_offsets);
    } else {
      // Option A (forced demand, or no fcost): col represents
      // `load` directly; no offset registered.
      sc.add_ampl_variable(
          ampl_name, uid(), LoadName, scenario, stage, load_cols.at(st_key));
    }
  }
  if (!crows.empty()) {
    capacity_rows[st_key] = std::move(crows);
  }
  if (!block_lmaxs.empty()) {
    block_lmax_values_[st_key] = std::move(block_lmaxs);
  }
  if (!block_offsets.empty()) {
    block_offset_values_[st_key] = std::move(block_offsets);
  }

  // P0: `fail_cols` / `balance_rows` are gone — substituted away via
  // `fail = lmax − load` (per tranche: `fail_k = u_k − served_k`).  The
  // constant baseline is in `lp.add_obj_constant(...)` (per block,
  // accumulated above); `Demand/fail_sol` (and `fail_seg<k>_sol`) are
  // reconstructed in `add_to_output` from the cached bounds and the
  // tranche columns' primal solution.  `demand.fail` is exposed to
  // user constraints as a virtual weighted sum (registered above).

  // `capainst` is registered centrally by CapacityBase::add_to_lp.

  return true;
}

bool DemandLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  // When Option C is OFF for every (scene, phase): col directly
  // carries `load`, so emit it via the standard add_col_sol /
  // add_col_cost paths.  When Option C is ON for at least one cell,
  // we reconstruct both load and fail via add_col_sol_values below
  // and skip the load_cost emission (its physical interpretation
  // under Option C is the reduced cost of `neg_fail`, sign-flipped,
  // with no downstream consumer).
  const bool option_c_active = !block_offset_values_.empty();
  // Stepwise curve (K ≥ 2): `load` is the sum of the tranche columns,
  // so it is reconstructed below rather than read off one column.
  const bool segmented = !seg_cols_.empty();
  const bool reconstruct_load = option_c_active || segmented;
  if (!reconstruct_load) {
    out.add_col_sol(cname, LoadName, pid, load_cols);
    out.add_col_cost(cname, LoadName, pid, load_cols);
  }

  out.add_row_dual(cname, CapacityName, pid, capacity_rows);

  // Stage-level emin row dual (post-collapse the only emin entity
  // still in the LP).  `Demand/emin_dual.csv` captures the marginal
  // value of relaxing the stage-energy cap — useful for diagnostics
  // even though no production tooling reads it today.  The legacy
  // `Demand/emin_sol.csv` and `Demand/emin_cost.csv` outputs are
  // gone because the aggregator `emin_col` no longer exists; the
  // "energy delivered toward emin" value is `sum_b bdur × mcol[b]`,
  // i.e. the same information available via `lman_cols` if needed.
  out.add_row_dual(cname, EminName, pid, emin_rows);

  // `lman:cost` is **not** emitted — the load-management slack
  // column has no meaningful dispatch-cost interpretation and no
  // downstream tooling consumes `Demand/lman_cost.*` (verified by
  // grep across scripts/ / guiservice/ / integration_test/,
  // 2026-05-14).  The `:sol` value is retained so post-mortem
  // analysis can see how much emin slack each (s, t, b) used.
  out.add_col_sol(cname, LmanName, pid, lman_cols);

  // ── Reconstructed `Demand/fail_sol.csv` (always) and
  //    `Demand/load_sol.csv` (Option C only) ───────────────────────
  //
  // fail_sol is reconstructed in every mode because the `fail` LP
  // column was substituted away (both Option A and C share that).
  // load_sol is reconstructed only under Option C, where the LP
  // column carries `neg_fail = load − lmax` (offset!=0).  Otherwise
  // (Option A / forced / non-substituted) load_sol was already
  // emitted above via the direct add_col_sol path.
  STBIndexHolder<double> load_sol_values;
  STBIndexHolder<double> fail_sol_values;
  // Per-segment failure `fail_k` (K ≥ 2 only), one holder per segment.
  std::vector<STBIndexHolder<double>> fail_seg_values;
  for (const auto& [st_key, lmax_block] : block_lmax_values_) {
    const auto lc_it = load_cols.find(st_key);
    if (lc_it == load_cols.end()) {
      continue;
    }
    const auto off_it = block_offset_values_.find(st_key);
    const bool has_offset = (off_it != block_offset_values_.end());
    const auto sc_it = seg_cols_.find(st_key);
    const bool has_segs = (sc_it != seg_cols_.end());

    BIndexHolder<double> load_block;
    BIndexHolder<double> fail_block;
    map_reserve(fail_block, lmax_block.size());
    if (has_offset || has_segs) {
      map_reserve(load_block, lmax_block.size());
    }
    for (const auto& [buid, block_lmax] : lmax_block) {
      const auto lc_block_it = lc_it->second.find(buid);
      if (lc_block_it == lc_it->second.end()) {
        continue;
      }
      if (has_segs) {
        const auto bc_it = sc_it->second.find(buid);
        if (bc_it != sc_it->second.end()) {
          const auto& tcols = bc_it->second;
          const auto& caps = seg_caps_.at(st_key).at(buid);
          if (fail_seg_values.size() < tcols.size()) {
            fail_seg_values.resize(tcols.size());
          }
          double served = 0.0;
          double fail_total = 0.0;
          for (std::size_t k = 0; k < tcols.size(); ++k) {
            const double primal = out.primal(tcols[k]);
            // Option A: col = served_k → fail_k = u_k − served_k.
            // Option C: col = served_k − u_k → fail_k = −col.
            const double fail_k =
                std::max(0.0, has_offset ? -primal : caps[k] - primal);
            served += has_offset ? primal + caps[k] : primal;
            fail_total += fail_k;
            fail_seg_values[k][st_key][buid] = fail_k;
          }
          load_block[buid] = std::max(0.0, served);
          fail_block[buid] = fail_total;
          continue;
        }
      }
      const double col_primal = out.primal(lc_block_it->second);
      double offset = 0.0;
      if (has_offset) {
        const auto ob_it = off_it->second.find(buid);
        if (ob_it != off_it->second.end()) {
          offset = ob_it->second;
        }
      }
      // Option C (offset != 0): col_primal == neg_fail ∈ [-lmax, 0].
      //   load = col_primal + offset
      //   fail = -col_primal
      // Option A / forced (offset == 0): col_primal == load.
      //   fail = lmax - col_primal
      // Clamp at 0 to absorb FP noise of order 1e-14.
      if (has_offset) {
        load_block[buid] = std::max(0.0, col_primal + offset);
        fail_block[buid] = std::max(0.0, -col_primal);
      } else {
        fail_block[buid] = std::max(0.0, block_lmax - col_primal);
      }
    }
    if (!load_block.empty()) {
      load_sol_values[st_key] = std::move(load_block);
    }
    if (!fail_block.empty()) {
      fail_sol_values[st_key] = std::move(fail_block);
    }
  }
  if (reconstruct_load) {
    out.add_col_sol_values(cname, LoadName, pid, load_sol_values);
  }
  // Only emit fail_sol when failure is actually penalized.  An unpenalized
  // demand (fcost == 0, e.g. a battery-charge `<name>_dem`) has
  // `fail = lmax − load` equal to unused dispatchable load capacity, not
  // curtailment — emitting it pollutes Demand/fail_sol.
  if (fail_penalized_) {
    out.add_col_sol_values(cname, FailName, pid, fail_sol_values);
    for (std::size_t k = 0; k < fail_seg_values.size(); ++k) {
      out.add_col_sol_values(cname, FailSegNames[k], pid, fail_seg_values[k]);
    }
  }

  return CapacityBase::add_to_output(out);
}

}  // namespace gtopt
