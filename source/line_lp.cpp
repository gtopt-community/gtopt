#include <algorithm>
#include <cmath>
#include <format>
#include <limits>
#include <set>
#include <utility>
#include <vector>

#include <gtopt/kirchhoff_node_angle.hpp>
#include <gtopt/line_losses.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/utils.hpp>
#include <gtopt/warn_once.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

LineLP::LineLP(const Line& pline, const InputContext& ic)
    : CapacityBase(pline, ic, Element::class_name)
    , tmax_ba(ic, Element::class_name, id(), std::move(line().tmax_ba))
    // ``max_flow`` (PLEXOS) is an alias of ``tmax_ab`` (validation
    // rejects both).
    , tmax_ab(ic,
              Element::class_name,
              id(),
              std::move(line().tmax_ab ? line().tmax_ab : line().max_flow))
    , min_flow(ic, Element::class_name, id(), std::move(line().min_flow))
    , max_rating(ic, Element::class_name, id(), std::move(line().max_rating))
    , min_rating(ic, Element::class_name, id(), std::move(line().min_rating))
    , overload_max_rating(
          ic, Element::class_name, id(), std::move(line().overload_max_rating))
    , overload_min_rating(
          ic, Element::class_name, id(), std::move(line().overload_min_rating))
    , outage_max_rating(
          ic, Element::class_name, id(), std::move(line().outage_max_rating))
    , outage_min_rating(
          ic, Element::class_name, id(), std::move(line().outage_min_rating))
    , units_out(ic, Element::class_name, id(), std::move(line().units_out))
    , conductor_rating_ab(
          ic, Element::class_name, id(), std::move(line().conductor_rating_ab))
    , conductor_rating_ba(
          ic, Element::class_name, id(), std::move(line().conductor_rating_ba))
    , equipment_rating_ab(
          ic, Element::class_name, id(), std::move(line().equipment_rating_ab))
    , equipment_rating_ba(
          ic, Element::class_name, id(), std::move(line().equipment_rating_ba))
    , security_limit_ab(
          ic, Element::class_name, id(), std::move(line().security_limit_ab))
    , security_limit_ba(
          ic, Element::class_name, id(), std::move(line().security_limit_ba))
    , m_rating_limits_(pline.has_rating_limits())
    , m_has_conductor_(pline.conductor_rating_ab.has_value()
                       || pline.conductor_rating_ba.has_value())
    , in_service(ic, Element::class_name, id(), std::move(line().in_service))
    , tmax_normal_ba(
          ic, Element::class_name, id(), std::move(line().tmax_normal_ba))
    , tmax_normal_ab(
          ic, Element::class_name, id(), std::move(line().tmax_normal_ab))
    , tcost(ic, Element::class_name, id(), std::move(line().tcost))
    // ``limit_penalty`` (PLEXOS) is an alias of ``overload_penalty``.
    , overload_penalty(
          ic,
          Element::class_name,
          id(),
          std::move(line().overload_penalty ? line().overload_penalty
                                            : line().limit_penalty))
    , lossfactor(ic, Element::class_name, id(), std::move(line().lossfactor))
    , reactance(ic, Element::class_name, id(), std::move(line().reactance))
    , voltage(ic, Element::class_name, id(), std::move(line().voltage))
    , resistance(ic, Element::class_name, id(), std::move(line().resistance))
    , loss_envelope(
          ic, Element::class_name, id(), std::move(line().loss_envelope))
    , tap_ratio(ic, Element::class_name, id(), std::move(line().tap_ratio))
    , phase_shift_deg(
          ic, Element::class_name, id(), std::move(line().phase_shift_deg))
{
  if (auto& dlr = line().dlr; dlr.has_value()) {
    m_dlr_.emplace(std::move(dlr.value()),
                   ic,
                   Element::class_name,
                   id().first,
                   id().second);
    line().dlr.reset();
  }
  SPDLOG_DEBUG("LineLP created: uid={} name='{}'", id().first, id().second);
}

namespace
{

/// Static limit parts of one direction in one block (``DblMax`` = the
/// field is unset: no limit).
struct LimitParts
{
  double flow {LinearProblem::DblMax};
  double rating {LinearProblem::DblMax};
  double rating_soft {LinearProblem::DblMax};
  double outage {LinearProblem::DblMax};
  double conductor {LinearProblem::DblMax};
  double equipment {LinearProblem::DblMax};
  double security {LinearProblem::DblMax};

  [[nodiscard]] double hard() const noexcept
  {
    return std::min({flow, rating, outage, conductor, equipment, security});
  }
};

// ── Helpers ─────────────────────────────────────────────────────────
//
// Pulled out of `LineLP::add_to_lp` to keep that function focused on
// orchestration.  Tested implicitly via the IEEE benchmark + losses
// equivalence tests in `test_kirchhoff_modes_ieee.cpp`.

/// Resolve `LossConfig` for a (scenario, stage).  Bundles loss-mode
/// resolution + per-stage scalar extraction (lossfactor, R, V, nseg,
/// allocation) so the caller can hand a single value to
/// `line_losses::add_block` per block.  When `opt_capacity` is unset
/// (no expansion) `fmax` is taken from the per-block `tmax_{ab,ba}`
/// schedule maximum so the loss curve covers the actual flow range.
[[nodiscard]] line_losses::LossConfig resolve_loss_config(
    const LineLP& self,
    const Line& raw_line,
    const SystemContext& sc,
    const StageLP& stage,
    std::span<const BlockLP> blocks,
    const std::optional<double>& opt_capacity,
    bool has_expansion,
    double dlr_fmax_fallback,
    bool dlr_replace)
{
  const auto loss_mode =
      line_losses::resolve_mode(raw_line, sc.options(), has_expansion);

  // `lossfactor` is now per-(stage, block) (PR-B).  The `LossConfig`
  // structure is built once per (line, stage) and reused across
  // blocks for performance — sample at the FIRST block of the stage
  // as the representative value.  Scalar schedules broadcast
  // uniformly so this matches the previous semantics exactly.  True
  // per-block lossfactor schedules see only the first block's value
  // on the loss-link rows; full per-block lossfactor on those rows
  // is a follow-up requiring `line_losses::add_block` to accept a
  // block-overridable `lf` parameter.
  const auto first_buid = blocks.empty() ? BlockUid {} : blocks.front().uid();
  const auto lf = self.param_lossfactor(stage.uid(), first_buid).value_or(0.0);
  const auto R = self.param_resistance(stage.uid()).value_or(0.0);
  // ``voltage`` omitted means per-unit mode (V = 1.0) — per the
  // ``Line.voltage`` doc and matching both Kirchhoff emitters
  // (`kirchhoff_node_angle.cpp`, `kirchhoff_cycle_basis.cpp`).  The
  // previous ``value_or(0.0)`` made ``V² = 0`` and silently disabled
  // the loss model for any line carrying resistance but no explicit
  // voltage.
  const auto V = self.param_voltage(stage.uid()).value_or(1.0);
  // Pass ``loss_segments`` through verbatim (no ``max(1, …)`` clamp).
  // ``nseg = 0`` is a legitimate "no PWL" input; ``line_losses::
  // make_config`` rewrites the mode to ``none`` so the dispatch
  // produces a lossless LP (matches the per-line invariant tested in
  // ``test_line_losses_decoupled_envelope.cpp``).  ``nseg = 1`` keeps
  // its existing fallback-to-linear behaviour inside ``make_config``.
  const int nseg =
      raw_line.loss_segments.value_or(sc.options().loss_segments());

  double fmax = 0.0;
  if (opt_capacity) {
    fmax = std::max(*opt_capacity, 0.0);
  } else {
    for (const auto& block : blocks) {
      const auto buid = block.uid();
      fmax = std::max(fmax, self.limit_envelope(stage.uid(), buid));
    }
  }
  // Dynamic line rating without a static ``tmax`` / capacity: spread the
  // loss PWL over the STAGE envelope of the DLR rating (never a per-block
  // value, so the loss geometry stays block-independent).
  // Under ``dlr limit_mode = replace`` the DLR rating may exceed the
  // static ``tmax``: the PWL then spans the larger of the two so the
  // flow above the static rating keeps its loss curve (expandable lines
  // keep their installed-capacity envelope).
  if (dlr_fmax_fallback > 0.0
      && (!(fmax > 0.0) || (dlr_replace && !opt_capacity)))
  {
    fmax = std::max(fmax, dlr_fmax_fallback);
  }

  // Loss-PWL envelope (decoupled from the flow cap).  When the line
  // carries an explicit ``loss_envelope`` (e.g. the ORIGINAL rating of a
  // soft-cap / ``enforce_level``-lifted line), the K loss segments are
  // spread over THAT range instead of the (possibly inflated) ``fmax``
  // flow cap — concentrating resolution in the realistic loading band.
  // Falls back to ``fmax`` when unset (backward compatible).  Flow past
  // the envelope (up to the flow cap) keeps a quadratic loss via the
  // coarse overflow ladder of ``line_losses::overflow_breakpoints``.
  // Pass the EXPLICIT per-line envelope only (0 when unset) so
  // ``make_config`` falls back to ``fmax`` internally — passing the
  // fmax-fallback here would mark every line as "decoupled" and release
  // the per-segment caps, breaking the legacy flow-cap-anchored model.
  const auto loss_env = self.param_loss_envelope(stage.uid());
  const double explicit_envelope =
      (loss_env && *loss_env > 0.0) ? *loss_env : 0.0;

  const auto allocation = raw_line.loss_allocation_mode_enum();
  // Resolve the per-MWh loss-column ε cost: per-line override (when set)
  // beats the global ``model_options.loss_cost_eps`` default (0.1 $/MWh
  // when unset).  An explicit ``0.0`` disables the loss-column cost.
  const double loss_cost_eps =
      raw_line.loss_cost_eps.value_or(sc.options().loss_cost_eps());
  // ``tangent_signed_flow`` abs-flow pin ($/MWh): per-line override beats
  // ``model_options.loss_flow_abs_cost`` (default 0.001 $/MWh).  Only
  // stamped when ``loss_cost_eps > 0`` (see ``add_tangent_signed_flow``).
  const double loss_flow_abs_cost =
      raw_line.loss_flow_abs_cost.value_or(sc.options().loss_flow_abs_cost());
  // Overflow-tier price (decoupled envelope only): per-line override
  // beats ``model_options.loss_overflow_cost``, which defaults to the
  // case's global ``demand_fail_cost`` (1000 $/MWh fallback).
  const double loss_overflow_cost =
      raw_line.loss_overflow_cost.value_or(sc.options().loss_overflow_cost());
  if (!raw_line.loss_overflow_cost.has_value()
      && sc.options().loss_overflow_cost_is_fallback()
      && explicit_envelope > 0.0)
  {
    static WarnOnceFlag warned_overflow_fallback;
    warn_once(warned_overflow_fallback,
              "line_losses: loss_overflow_cost is unset and demand_fail_cost "
              "is 0; the overflow tier of lines with a "
              "loss_envelope (first: '{}') is priced at the fallback {:.6g} "
              "$/MWh.  Set model_options.loss_overflow_cost or "
              "demand_fail_cost explicitly.",
              raw_line.name,
              PlanningOptionsLP::default_demand_fail_cost);
  }

  // L-secant chord controls (issue #504): per-line override wins,
  // falling back to the global default.  ``loss_use_sos2 = true``
  // with ``loss_secant_segments <= 1`` is sanitized inside
  // ``make_config`` (collapses to single-secant, no SOS2) so we
  // forward the raw inputs here.
  const int nseg_secant = raw_line.loss_secant_segments.value_or(
      sc.options().loss_secant_segments());
  const bool use_sos2 =
      raw_line.loss_use_sos2.value_or(sc.options().loss_use_sos2());

  auto config = line_losses::make_config(loss_mode,
                                         raw_line,
                                         allocation,
                                         lf,
                                         R,
                                         V,
                                         nseg,
                                         fmax,
                                         sc.options().scale_loss_link(),
                                         explicit_envelope,
                                         loss_cost_eps,
                                         nseg_secant,
                                         use_sos2,
                                         loss_overflow_cost,
                                         loss_flow_abs_cost);
  // Thermal limit vs losses: per-line override beats
  // ``model_options.line_thermal_limit_mode`` (default ``flow``: the
  // limit bounds the flow variable only).
  config.thermal_limit = raw_line.line_thermal_limit_mode_enum().value_or(
      sc.options().line_thermal_limit_mode());
  config.segment_flow_cap = sc.options().loss_segment_bound();
  return config;
}

}  // namespace

// ── add_to_lp ───────────────────────────────────────────────────────

bool LineLP::add_to_lp(SystemContext& sc,
                       const ScenarioLP& scenario,
                       const StageLP& stage,
                       LinearProblem& lp)
{
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (is_loop()) {
    return true;
  }

  if (!CapacityBase::add_to_lp(sc, ampl_name, scenario, stage, lp)) {
    return false;
  }

  // F9: register filter metadata for sum(...) predicates.
  {
    AmplElementMetadata metadata;
    metadata.reserve(3);
    if (const auto& t = line().type) {
      metadata.emplace_back(TypeKey, *t);
    }
    // Resolve via `sc.element<BusLP>` (handles both Uid and Name forms
    // of the JSON-side `bus_a` / `bus_b` SingleId variant — `std::get<Uid>`
    // would throw if the JSON used a string name).
    metadata.emplace_back(
        BusAKey, static_cast<double>(sc.element<BusLP>(bus_a_sid()).uid()));
    metadata.emplace_back(
        BusBKey, static_cast<double>(sc.element<BusLP>(bus_b_sid()).uid()));
    sc.register_ampl_element_metadata(ampl_name, uid(), std::move(metadata));
  }

  if (!is_active(stage)) [[unlikely]] {
    return true;
  }

  const auto& bus_a_lp = sc.element<BusLP>(bus_a_sid());
  const auto& bus_b_lp = sc.element<BusLP>(bus_b_sid());
  if (!bus_a_lp.is_active(stage) || !bus_b_lp.is_active(stage)) {
    return true;
  }

  const auto& balance_rows_a = bus_a_lp.balance_rows_at(scenario, stage);
  const auto& balance_rows_b = bus_b_lp.balance_rows_at(scenario, stage);
  const auto& blocks = stage.blocks();

  const auto [opt_capacity, capacity_col] = capacity_and_col(stage, lp);
  const double stage_capacity = opt_capacity.value_or(LinearProblem::DblMax);
  // ``tcost`` is per-(stage, block) since 2026-05-18 — read inside the
  // block loop below.  Kept here only as the per-stage-availability
  // sentinel (loss_config still wants a stage-scoped number; resolve
  // at the first block for that path).
  const auto stage_tcost =
      tcost.at(stage.uid(), blocks.front().uid()).value_or(0.0);
  // Soft-cap "feature enabled" gate: when the schedule is unset
  // anywhere, the whole soft-cap path is skipped to preserve the
  // pure hard-cap LP shape.  Per-block resolution happens inside
  // the block loop below — ``overload_penalty`` is per-(stage,
  // block) so time-varying penalties bind correctly.
  const bool soft_cap_enabled = overload_penalty.has_value();
  // Overload slack bound: per-line value, else model_options.
  const auto slack_mode = line().overload_slack_bound_enum().value_or(
      sc.options().overload_slack_bound());
  const double slack_ratio =
      line().max_overload_ratio.value_or(sc.options().max_overload_ratio());

  // ── Dynamic line rating (Line.dlr): per-block span ratings ─────────
  // Evaluated once per (scenario, stage) here; the block loop below turns
  // them into flow limits.  ``dlr_env`` is the stage envelope used as the
  // static flow cap / loss-PWL range when the line has neither ``tmax``
  // nor a capacity: 5 % above the largest per-block hard rating, so it
  // never ties with (and never steals the dual of) a span limit.
  // ``model_options.use_line_dlr = false`` turns every DLR block off.
  const bool dlr_on = sc.options().use_line_dlr() && m_dlr_.has_value()
      && !m_dlr_->spans().empty();
  std::vector<LineDlrLP::BlockRatings> dlr_block;
  double dlr_env = 0.0;
  if (dlr_on) {
    const double dlr_kv =
        m_dlr_->voltage().value_or(param_voltage(stage.uid()).value_or(0.0));
    dlr_block.resize(blocks.size());
    for (const auto& [bi, block] : enumerate(blocks)) {
      if (in_service.at(stage.uid(), block.uid()).value_or(True) == False) {
        continue;
      }
      auto& br = dlr_block[static_cast<std::size_t>(bi)];
      m_dlr_->ratings_at(scenario.uid(), stage.uid(), block.uid(), dlr_kv, br);
      dlr_env = std::max(dlr_env, std::ranges::min(br.emergency));
    }
    dlr_env *= 1.05;
  }
  // DLR limit mode: ``dlr.limit_mode`` → ``model_options.dlr_limit_mode``
  // → ``replace`` when the line has a conductor rating, else ``min``.
  // ``replace`` drops the conductor rating (the DLR rating IS the
  // conductor's thermal capacity); without a conductor rating an explicit
  // ``replace`` drops ``max_rating`` / ``min_rating`` instead.  Flow,
  // outage, equipment and security limits are never replaced.
  const bool dlr_replace = dlr_on
      && m_dlr_->limit_mode()
              .or_else([&] { return sc.options().dlr_limit_mode(); })
              .value_or(m_has_conductor_ ? LineDlrLimitMode::replace
                                         : LineDlrLimitMode::min)
          == LineDlrLimitMode::replace;
  const bool replace_conductor = dlr_replace && m_has_conductor_;
  const bool replace_rating = dlr_replace && !m_has_conductor_;

  const auto loss_config =
      resolve_loss_config(*this,
                          line(),
                          sc,
                          stage,
                          blocks,
                          opt_capacity,
                          /*has_expansion=*/capacity_col.has_value(),
                          /*dlr_fmax_fallback=*/dlr_env,
                          dlr_replace);

  // Per-block flow / capacity / loss / segment col maps assembled
  // here; moved into the persistent `STBIndexHolder` members at the
  // end so the cycle_basis post-pass and the AMPL resolver can read
  // them across scenarios and stages.
  BIndexHolder<ColIndex> fpcols;
  BIndexHolder<RowIndex> cprows;
  BIndexHolder<ColIndex> fncols;
  BIndexHolder<RowIndex> cnrows;
  BIndexHolder<RowIndex> tprows;
  BIndexHolder<RowIndex> tnrows;
  BIndexHolder<ColIndex> lpcols;
  BIndexHolder<ColIndex> lncols;
  // Per-direction linear loss factors, parallel to `fpcols` / `fncols`.
  // Populated only under `LineLossesMode::linear` (non-zero
  // `result.fp_loss` / `fn_loss`); stay empty for every other mode so
  // the linear branch in `add_to_output` is a no-op there.
  BIndexHolder<double> fplossfac;
  BIndexHolder<double> fnlossfac;
  BIndexHolder<std::vector<ColIndex>> fpsegcols;
  BIndexHolder<std::vector<ColIndex>> fnsegcols;
  // Per-segment physical loss factors `lf_k`, parallel to the
  // `fpsegcols` / `fnsegcols` column vectors (piecewise_direct only).
  BIndexHolder<std::vector<double>> fpsegloss;
  BIndexHolder<std::vector<double>> fnsegloss;
  /// Per-block signed-flow column (populated only under
  /// ``LineLossesMode::tangent_signed_flow``).  When populated, the
  /// directional ``fpcols`` / ``fncols`` maps are empty for this
  /// block — KVL will stamp ``+x_τ`` directly on the signed column.
  BIndexHolder<ColIndex> fscols;
  // Soft-cap (overload) slack columns + rows.  Only populated when
  // the feature is enabled AND the per-block threshold is strictly
  // below the per-block hard cap.  Kept empty otherwise — the
  // `cond_assign` pattern below skips the outer (s,t) insert so
  // `add_to_output` stays a no-op for hard-cap-only lines.
  BIndexHolder<ColIndex> opcols;
  BIndexHolder<ColIndex> oncols;
  BIndexHolder<RowIndex> oprows;
  BIndexHolder<RowIndex> onrows;
  // Reserve every per-block holder up-front to avoid rehash churn as
  // the block loop populates each map.  All eight grow at most to
  // `blocks.size()` (one entry per block) so a single reservation
  // each gets us to steady-state capacity in the common case.  The
  // seg holders carry an outer entry per block and an inner segment
  // vector — the inner vector is reserved by `add_direct_direction`
  // via `seg_cols.reserve(nseg)`, so we only need the outer-map
  // reservation here.
  map_reserve(fpcols, blocks.size());
  map_reserve(fncols, blocks.size());
  map_reserve(cprows, blocks.size());
  map_reserve(cnrows, blocks.size());
  map_reserve(lpcols, blocks.size());
  map_reserve(lncols, blocks.size());
  map_reserve(fplossfac, blocks.size());
  map_reserve(fnlossfac, blocks.size());
  map_reserve(fpsegcols, blocks.size());
  map_reserve(fnsegcols, blocks.size());
  map_reserve(fpsegloss, blocks.size());
  map_reserve(fnsegloss, blocks.size());
  map_reserve(fscols, blocks.size());
  // DLR per-block bookkeeping (outputs + dual attribution).
  BIndexHolder<double> dlr_rating_b;
  BIndexHolder<double> dlr_rating_e_b;
  BIndexHolder<double> critical_span_b;
  BIndexHolder<std::vector<RowIndex>> span_rows_b;
  BIndexHolder<std::vector<DlrDualRef>> dlr_refs_b;
  BIndexHolder<LimitRef> limp_b;
  BIndexHolder<LimitRef> limn_b;
  std::vector<bool> span_critical(dlr_on ? m_dlr_->spans().size() : 0, false);
  if (soft_cap_enabled) {
    map_reserve(opcols, blocks.size());
    map_reserve(oncols, blocks.size());
    map_reserve(oprows, blocks.size());
    map_reserve(onrows, blocks.size());
  }

  // Lines with rating limits: the loss geometry (segment widths, chord)
  // is spread over the STAGE envelope of the static limits and the
  // per-block cap is applied afterwards as a bound / row RHS, exactly
  // like a DLR rating.  A per-block cap passed to ``add_block`` would
  // also bind on the segment widths, a degenerate tie that splits the
  // limit dual arbitrarily between the flow bound and the segments.
  // Static limit parts of one direction in block ``buid``.
  const auto parts_at = [&](BlockUid buid, bool fwd)
  {
    const double pen_block = soft_cap_enabled
        ? overload_penalty.at(stage.uid(), buid).value_or(0.0)
        : 0.0;
    const bool out_block =
        lim(units_out, stage.uid(), buid).value_or(0.0) > 0.0;
    constexpr double inf = LinearProblem::DblMax;
    const auto get = [&](const OptTBRealSched& ab, const OptTBRealSched& ba)
    {
      return (fwd ? lim(ab, stage.uid(), buid)
                  : lim(ba, stage.uid(), buid, /*negate=*/true))
          .value_or(inf);
    };
    const auto get2 = [&](const OptTBRealSched& ab, const OptTBRealSched& ba)
    { return lim(fwd ? ab : ba, stage.uid(), buid).value_or(inf); };
    LimitParts p;
    p.flow = (fwd ? param_tmax_ab(stage.uid(), buid)
                  : param_tmax_ba(stage.uid(), buid))
                 .value_or(stage_capacity);
    const double rating = get(max_rating, min_rating);
    const double overload = get(overload_max_rating, overload_min_rating);
    // Overload (emergency) rating: the hard cap when the overload band
    // is priced in this block; the normal rating is then its soft
    // threshold.  Otherwise the normal rating is the hard cap.
    if (pen_block > 0.0 && overload < inf) {
      p.rating = overload;
      p.rating_soft = rating < overload ? rating : inf;
    } else {
      p.rating = rating;
    }
    p.outage = out_block ? get(outage_max_rating, outage_min_rating) : inf;
    p.conductor = get2(conductor_rating_ab, conductor_rating_ba);
    p.equipment = get2(equipment_rating_ab, equipment_rating_ba);
    p.security = get2(security_limit_ab, security_limit_ba);
    if (replace_conductor) {
      p.conductor = inf;
    }
    if (replace_rating) {
      p.rating = inf;
      p.rating_soft = inf;
    }
    return p;
  };
  double rating_env = 0.0;
  if (m_rating_limits_) {
    for (const auto& block : blocks) {
      for (const bool fwd : {true, false}) {
        const double h = parts_at(block.uid(), fwd).hard();
        if (h < LinearProblem::DblMax) {
          rating_env = std::max(rating_env, h);
        }
      }
    }
  }
  for (const auto& [block_idx, block] : enumerate(blocks)) {
    const auto buid = block.uid();
    // Per-block in-service flag (PLEXOS ``Line.Units`` / ``Lin_Units.csv``).
    // A block resolving to ``0`` (False) means the line is out for that
    // block (maintenance / forced outage): emit nothing — no flow column,
    // capacity row, loss segment, balance contribution, or (downstream)
    // KVL row.  The kirchhoff pass skips blocks with no flow column, so
    // the two buses are genuinely decoupled — a true open circuit, not a
    // ``tmax=0`` short (which would instead pin θ_a = θ_b).  Absent
    // schedule ⇒ in service.
    if (in_service.at(stage.uid(), buid).value_or(True) == False) {
      continue;
    }
    auto& brow_a = lp.row_at(balance_rows_a.at(buid));
    auto& brow_b = lp.row_at(balance_rows_b.at(buid));

    // Both ``tmax_ab`` and ``tmax_ba`` are MAGNITUDES of the forward
    // and reverse maximum flow respectively (both ≥ 0 by convention).
    // Reading them via ``block_maxmin_at`` was a misuse — that helper
    // treats its 4th argument as a ``min`` slot whose default also
    // serves as a floor (``max(capacity_min, lmin_at)``), with the
    // pre-existing call passing ``-stage_capacity``.  That made
    // ``block_tmax_ba`` default to ``-DblMax`` for any line without
    // explicit reverse capacity, which then propagated into
    // ``line_losses.cpp:370`` as ``flowp.lowb = -block_tmax_ba = +DblMax``,
    // a positive lower bound on a non-negative flow variable.  After
    // Ruiz equilibration that landed at ``flowp >= 1.07e+20`` and made
    // every multi-bus run with at least one unconstrained line
    // (``enforce_limits=0`` in PLEXOS, ``tmax_ab/tmax_ba`` unset in
    // the JSON) trip CPLEX presolve infeasibility.  Discovered while
    // wiring the CEN PCP daily case (DATOS20260422) end-to-end.
    //
    // Static limit parts per direction (``DblMax`` = unset: an unset
    // field never limits and never becomes 0).  The static hard cap is
    // the min over the set parts; the DLR rating is applied on top, after
    // ``add_block``.  An unset flow limit keeps today's default (the
    // line ``capacity``, unbounded without one).
    const LimitParts parts_ab = parts_at(buid, /*fwd=*/true);
    const LimitParts parts_ba = parts_at(buid, /*fwd=*/false);
    auto block_tmax_ab = parts_ab.hard();
    auto block_tmax_ba = parts_ba.hard();
    if (dlr_on) {
      if (block_tmax_ab >= LinearProblem::DblMax) {
        block_tmax_ab = dlr_env;
      }
      if (block_tmax_ba >= LinearProblem::DblMax) {
        block_tmax_ba = dlr_env;
      }
    }
    // ``tcost`` is per-(stage, block); read its block-specific value
    // and let CostHelper apply the per-block duration scaling.
    const auto block_tcost_phys =
        tcost.at(stage.uid(), block.uid()).value_or(stage_tcost);
    const auto block_tcost =
        CostHelper::block_ecost(scenario, stage, block, block_tcost_phys);

    // The flow cap is ALWAYS enforced (``enforce_capacity = true``).
    //
    // ``Line.enforce_level`` (added 2026-05-22, ``feat(line): Line.
    // enforce_level``) was a short-lived attempt to mirror PLEXOS
    // ``Enforce Limits`` (0 = never, 1 = post-solve, 2 = always) by
    // passing ``enforce_capacity = false`` for level 0, which freed the
    // directional flow columns AND relaxed the per-segment upper bounds to
    // ``DblMax`` while the loss coefficients kept using the REAL
    // ``seg_width``.  That half-relaxed state is ill-posed for the loss
    // model (a free flow column with finite loss-row coefficients) and
    // leaves a genuinely FREE LP column — the dominant source of free
    // variables, which defeats GPU first-order / heuristic solvers (cuOpt
    // / PDLP cannot project an unbounded column).
    //
    // It is also obsolete: plexos2gtopt no longer relies on a gtopt-side
    // ``enforce_level`` toggle — it expresses every limit directly as
    // ``tmax`` (hard cap) + ``tmax_normal`` (soft band).  Passing
    // ``enforce_capacity = true`` here simply RESTORES the original
    // pre-2026-05-22 behaviour: every flow column is bound by
    // ``block_tmax`` (= ``tmax`` if set, else the capacity ceiling), and a
    // flow column is NEVER left free.  Two representations, both covered:
    //   - signed flow (``add_none``'s ``flowp``, ``add_tangent_signed_flow``):
    //     a single column over ``[−tmax_ba, +tmax_ab]`` — free on BOTH
    //     sides unless bounded, so both bounds are set here.
    //   - ``flowp`` / ``flown`` (linear / piecewise / bidirectional): two
    //     columns already pinned ``lowb = 0``, so only the ``tmax`` upper
    //     bound is added.
    // The overload columns still apply the soft ``tmax_normal → tmax``
    // penalty, and capacity-*expansion* lines additionally carry the
    // installed-capacity row.  ``Line.enforce_level`` is retained in the
    // schema for backward compatibility but is now a no-op.
    // Rating-limited lines: build at the stage envelope, cap below.
    const auto build_cap = [&](double hard)
    {
      return (m_rating_limits_ && hard < LinearProblem::DblMax
              && rating_env > hard)
          ? rating_env
          : hard;
    };
    auto result = line_losses::add_block(loss_config,
                                         scenario,
                                         stage,
                                         block,
                                         lp,
                                         brow_a,
                                         brow_b,
                                         build_cap(block_tmax_ab),
                                         build_cap(block_tmax_ba),
                                         block_tcost,
                                         capacity_col,
                                         uid(),
                                         /*enforce_capacity=*/true);

    // ── Dynamic line rating: per-block span limits ────────────────
    // ``hard_*`` is the effective hard cap of each direction (the static
    // cap passed to ``add_block`` tightened by the DLR rating) and
    // ``dlr_soft`` the normal-tier threshold of the overload band when the
    // emergency tier is active.  Without DLR both stay at the static
    // values and nothing below changes the LP.
    // Limit terms for DLR rows / bounds: ``limit_terms`` plus the B→A
    // side of ``add_none``'s single SIGNED flow column (no ``flown``).
    const auto dlr_terms = [&](bool forward)
    {
      auto terms = line_losses::limit_terms(loss_config, result, forward);
      if (terms.empty() && !forward && result.fp_col && !result.fn_col
          && lp.get_col_lowb(*result.fp_col) < 0.0)
      {
        terms.emplace_back(*result.fp_col, -1.0);
      }
      return terms;
    };
    // Tighten a direction to ``cap`` where its limit lives: an existing
    // thermal row (sending / full) gets the lower RHS; a single-term limit
    // (flow column, or ``linear`` with coefficient 1 + a·λ) the column
    // bound ``cap / coef``; a multi-term limit with no row
    // (``piecewise_direct`` under ``flow``) a new thermal row.  Returns
    // the reference of the bound / row now carrying the cap.
    const auto tighten_cap = [&](bool forward,
                                 double cap) -> std::optional<LimitRef>
    {
      auto& trow = forward ? result.thermalp_row : result.thermaln_row;
      if (trow) {
        auto& row = lp.row_at(*trow);
        row.uppb = std::min(row.uppb, cap);
        return LimitRef {.index = static_cast<Index>(*trow)};
      }
      const auto terms = dlr_terms(forward);
      if (terms.empty()) {
        return std::nullopt;
      }
      if (terms.size() == 1) {
        const auto [col, coef] = terms.front();
        const double bound = cap / coef;
        if (coef > 0.0) {
          lp.set_col_uppb(col, std::min(lp.get_col_uppb(col), bound));
        } else {
          lp.set_col_lowb(col, std::max(lp.get_col_lowb(col), bound));
        }
        return LimitRef {
            .is_row = false,
            .index = static_cast<Index>(col),
            .coef = coef,
            .bound = bound,
        };
      }
      auto row =
          SparseRow {
              .class_name = Element::class_name.full_name(),
              .constraint_name = forward ? ThermalpName : ThermalnName,
              .variable_uid = uid(),
              .context =
                  make_block_context(scenario.uid(), stage.uid(), block.uid()),
          }
              .less_equal(cap);
      for (const auto& [col, coef] : terms) {
        row[col] = coef;
      }
      trow = lp.add_row(std::move(row));
      return LimitRef {.index = static_cast<Index>(*trow)};
    };
    if (build_cap(block_tmax_ab) != block_tmax_ab) {
      std::ignore = tighten_cap(/*forward=*/true, block_tmax_ab);
    }
    if (build_cap(block_tmax_ba) != block_tmax_ba) {
      std::ignore = tighten_cap(/*forward=*/false, block_tmax_ba);
    }
    double hard_ab = block_tmax_ab;
    double hard_ba = block_tmax_ba;
    // Lowest DLR hard rating of the block (thermal; DblMax without DLR).
    double dlr_min_hard = LinearProblem::DblMax;
    std::optional<double> dlr_soft;
    int dlr_k_norm = -1;
    // Span whose emergency rating is the hard cap of each direction
    // (−1: the static cap is lower, or no DLR emergency tier).
    int dlr_k_hard_ab = -1;
    int dlr_k_hard_ba = -1;
    if (dlr_on) {
      const auto& br = dlr_block[static_cast<std::size_t>(block_idx)];
      const auto& spans = m_dlr_->spans();
      const double pen = soft_cap_enabled
          ? overload_penalty.at(stage.uid(), buid).value_or(0.0)
          : 0.0;
      // The emergency rating is the hard cap only when the overload band
      // can price the excursion above the normal rating; otherwise the
      // normal rating is the hard cap.
      const bool emerg_tier = m_dlr_->has_emergency() && pen > 0.0;
      const auto& hard_r = emerg_tier ? br.emergency : br.normal;
      const auto argmin = [](const std::vector<double>& v)
      {
        return static_cast<int>(
            std::distance(v.begin(), std::ranges::min_element(v)));
      };
      const int k_hard = argmin(hard_r);
      dlr_k_norm = argmin(br.normal);
      const double min_hard = hard_r[static_cast<std::size_t>(k_hard)];
      const double min_norm = br.normal[static_cast<std::size_t>(dlr_k_norm)];
      dlr_min_hard = min_hard;
      hard_ab = std::min(block_tmax_ab, min_hard);
      hard_ba = std::min(block_tmax_ba, min_hard);
      if (emerg_tier) {
        dlr_soft = min_norm;
        const double etol = 1e-9 * std::max(1.0, min_hard);
        dlr_k_hard_ab = (block_tmax_ab < min_hard - etol) ? -1 : k_hard;
        dlr_k_hard_ba = (block_tmax_ba < min_hard - etol) ? -1 : k_hard;
      }

      // Outputs: effective normal rating (A→B, static cap included),
      // emergency rating, and the binding span (−1: static cap lower).
      const double tie_tol = 1e-9 * std::max(1.0, min_norm);
      dlr_rating_b[buid] = std::min(block_tmax_ab, min_norm);
      if (m_dlr_->has_emergency()) {
        dlr_rating_e_b[buid] =
            std::min(block_tmax_ab, std::ranges::min(br.emergency));
      }
      critical_span_b[buid] = (block_tmax_ab < min_norm - tie_tol)
          ? -1.0
          : static_cast<double>(
                spans[static_cast<std::size_t>(dlr_k_norm)].uid);

      auto& refs = dlr_refs_b[buid];
      const auto make_row_ref = [](int k, RowIndex row)
      {
        return DlrDualRef {
            .span = k,
            .is_row = true,
            .index = static_cast<Index>(row),
        };
      };

      if (m_dlr_->mode() == LineDlrSpanMode::min) {
        // Tighten the limit exactly where ``tmax`` lives: an existing
        // thermal row (sending / full) gets the lower RHS; a single-term
        // limit (flow column, or ``linear`` with coefficient 1 + a·λ)
        // gets the column bound ``T / coef``; a multi-term limit with no
        // row (``piecewise_direct`` under ``flow``) gets a new thermal row.
        const auto apply_cap =
            [&](bool forward, double cap, int k) -> std::optional<DlrDualRef>
        {
          const auto r = tighten_cap(forward, cap);
          if (!r) {
            return std::nullopt;
          }
          return DlrDualRef {
              .span = k,
              .is_row = r->is_row,
              .index = r->index,
              .coef = r->coef,
              .bound = r->bound,
          };
        };
        const double htol = 1e-9 * std::max(1.0, min_hard);
        const int k_ab = (block_tmax_ab < min_hard - htol) ? -1 : k_hard;
        const int k_ba = (block_tmax_ba < min_hard - htol) ? -1 : k_hard;
        if (auto r = apply_cap(true, hard_ab, k_ab)) {
          refs.push_back(*r);
        }
        if (auto r = apply_cap(false, hard_ba, k_ba)) {
          refs.push_back(*r);
        }
        if (k_ab >= 0 || k_ba >= 0) {
          span_critical[static_cast<std::size_t>(k_hard)] = true;
        }
      } else {
        // ``rows``: one row per non-dominated span and direction,
        // ``f_dir + a·ℓ_dir ≤ T_k``.  Spans above the effective cap of a
        // direction (static cap included) are dominated there: no row.
        auto& srows = span_rows_b[buid];
        const auto add_span_rows = [&](bool forward, double cap)
        {
          const auto terms = dlr_terms(forward);
          if (terms.empty()) {
            return;
          }
          const double tol = 1e-9 * std::max(1.0, cap);
          for (const auto& [k, tk] : enumerate(hard_r)) {
            if (tk > cap + tol) {
              continue;  // dominated in this block / direction
            }
            auto row =
                SparseRow {
                    .class_name = Element::class_name.full_name(),
                    .constraint_name =
                        forward ? SpanThermalpName : SpanThermalnName,
                    .variable_uid = uid(),
                    .context = make_block_context(scenario.uid(),
                                                  stage.uid(),
                                                  block.uid(),
                                                  static_cast<Index>(k)),
                }
                    .less_equal(tk);
            for (const auto& [col, coef] : terms) {
              row[col] = coef;
            }
            const auto r = lp.add_row(std::move(row));
            srows.push_back(r);
            refs.push_back(make_row_ref(static_cast<int>(k), r));
            span_critical[static_cast<std::size_t>(k)] = true;
          }
        };
        add_span_rows(true, hard_ab);
        add_span_rows(false, hard_ba);
      }
    }

    // Soft-cap band terms per direction, captured before the result's
    // segment vectors are moved into the holders below.  Loaded quantity:
    // the bare flow column under ``line_thermal_limit_mode = flow``
    // (historical LP, only for modes with a ``flowp``/``flown`` column),
    // else the same ``f_dir + a·ℓ_dir`` terms as the hard thermal-limit
    // row (``line_losses::limit_terms``), which also cover
    // ``tangent_signed_flow`` (±f) and ``piecewise_direct`` (segments).
    std::vector<std::pair<ColIndex, double>> band_p;
    std::vector<std::pair<ColIndex, double>> band_n;
    if (soft_cap_enabled) {
      // DLR lines always use ``limit_terms`` (covers every loss mode,
      // ``a = 0`` under ``flow``) so the emergency band reaches
      // ``tangent_signed_flow`` / ``piecewise_direct`` too.
      if (dlr_on) {
        band_p = dlr_terms(/*forward=*/true);
        band_n = dlr_terms(/*forward=*/false);
      } else if (line_losses::thermal_loss_share(loss_config) > 0.0) {
        band_p =
            line_losses::limit_terms(loss_config, result, /*forward=*/true);
        band_n =
            line_losses::limit_terms(loss_config, result, /*forward=*/false);
      } else {
        if (result.fp_col) {
          band_p.emplace_back(*result.fp_col, 1.0);
        }
        if (result.fn_col) {
          band_n.emplace_back(*result.fn_col, 1.0);
        }
      }
    }

    // ── Binding-limit attribution (lines with rating limits) ──────
    // Every part bounds the same loaded quantity
    // (``line_thermal_limit_mode``), so one bound / row per direction
    // carries the min: the flow-column bound (``flow``), the
    // ``thermal{p,n}`` row (``sending`` / ``full``) or the DLR span row /
    // bound.  No extra row: the binding part is identified by value
    // (ties: conductor, rating, outage, equipment, security, flow) and
    // the dual of that one bound / row is reported as
    // ``<part>{p,n}_dual``.  Only ``piecewise_direct`` under ``flow``
    // (cap carried by the segment widths) gets an explicit row.
    const auto attach_limit = [&](bool forward,
                                  const LimitParts& lp_parts,
                                  double hard,
                                  BIndexHolder<LimitRef>& dst)
    {
      if (!m_rating_limits_ || !(hard < LinearProblem::DblMax)) {
        return;
      }
      const double tol = 1e-9 * std::max(1.0, hard);
      const double conductor = std::min(lp_parts.conductor, dlr_min_hard);
      const std::array<std::pair<LimitPart, double>, 6> order {{
          {LimitPart::conductor, conductor},
          {LimitPart::rating, lp_parts.rating},
          {LimitPart::outage, lp_parts.outage},
          {LimitPart::equipment, lp_parts.equipment},
          {LimitPart::security, lp_parts.security},
          {LimitPart::flowlimit, lp_parts.flow},
      }};
      const auto* const it = std::ranges::find_if(
          order, [&](const auto& pv) { return pv.second <= hard + tol; });
      if (it == order.end()) {
        return;  // the DLR envelope stand-in (no set part binds)
      }
      LimitRef ref {.part = it->first};
      // DLR binds (rating at or below every static conductor value): the
      // span duals carry it, in both span modes.
      if (it->first == LimitPart::conductor
          && dlr_min_hard <= lp_parts.conductor + tol)
      {
        ref.span_sum = true;
        dst[buid] = ref;
        return;
      }
      if (const auto& trow =
              forward ? result.thermalp_row : result.thermaln_row)
      {
        ref.index = static_cast<Index>(*trow);
        dst[buid] = ref;
        return;
      }
      const auto terms = dlr_terms(forward);
      if (terms.empty()) {
        return;
      }
      if (terms.size() == 1) {
        const auto [col, coef] = terms.front();
        ref.is_row = false;
        ref.index = static_cast<Index>(col);
        ref.coef = coef;
        ref.bound = hard / coef;
        dst[buid] = ref;
        return;
      }
      const auto pi = static_cast<std::size_t>(it->first);
      auto row =
          SparseRow {
              .class_name = Element::class_name.full_name(),
              .constraint_name =
                  forward ? LimitPartpNames[pi] : LimitPartnNames[pi],
              .variable_uid = uid(),
              .context =
                  make_block_context(scenario.uid(), stage.uid(), block.uid()),
          }
              .less_equal(hard);
      for (const auto& [col, coef] : terms) {
        row[col] = coef;
      }
      ref.index = static_cast<Index>(lp.add_row(std::move(row)));
      dst[buid] = ref;
    };
    attach_limit(/*forward=*/true, parts_ab, hard_ab, limp_b);
    attach_limit(/*forward=*/false, parts_ba, hard_ba, limn_b);

    // Compress the eight near-identical "if present, store" clauses.
    const auto store_opt = [&](auto& dst, const auto& src)
    {
      if (src) {
        dst[buid] = *src;
      }
    };
    const auto store_vec = [&](auto& dst, auto& src)
    {
      if (!src.empty()) {
        dst[buid] = std::move(src);
      }
    };
    store_opt(fpcols, result.fp_col);
    store_opt(fncols, result.fn_col);
    // Linear-mode loss factors (non-zero only under `add_linear`).
    // Stored alongside the aggregator flow cols so `add_to_output` can
    // reconstruct `loss = lossfactor · primal(flow)`.  Gated on a
    // non-zero factor so non-linear modes (which leave `fp_loss` /
    // `fn_loss` at 0) never populate these maps.
    if (result.fp_col && result.fp_loss != 0.0) {
      fplossfac[buid] = result.fp_loss;
    }
    if (result.fn_col && result.fn_loss != 0.0) {
      fnlossfac[buid] = result.fn_loss;
    }
    store_opt(lpcols, result.lossp_col);
    store_opt(lncols, result.lossn_col);
    store_opt(cprows, result.capp_row);
    store_opt(cnrows, result.capn_row);
    store_opt(tprows, result.thermalp_row);
    store_opt(tnrows, result.thermaln_row);
    store_vec(fpsegcols, result.seg_p_cols);
    store_vec(fnsegcols, result.seg_n_cols);
    store_vec(fpsegloss, result.seg_p_loss);
    store_vec(fnsegloss, result.seg_n_loss);
    store_opt(fscols, result.flow_col);

    // ── Soft cap / overload penalty ────────────────────────────────
    // For each direction with band terms (``band_p`` / ``band_n``
    // above), when a per-block soft threshold `tmax_normal_*` is set
    // strictly below the corresponding hard cap, emit a non-negative
    // slack column `overload{p,n}` bounded by `(hard − soft)` and a row
    //   `f_dir + a·ℓ_dir − overload{p,n} ≤ tmax_normal_*`
    // (``a = 0`` under ``line_thermal_limit_mode = flow``) so the LP only
    // pays the penalty above the soft cap and can never exceed the hard
    // cap (flow-col bound / thermal-limit row).  Under ``flow`` the band
    // keeps its historical scope: modes with a ``flowp``/``flown``
    // column only (not ``tangent_signed_flow`` / ``piecewise_direct``).
    if (soft_cap_enabled) {
      const auto block_ctx =
          make_block_context(scenario.uid(), stage.uid(), block.uid());
      // Per-(stage, block) penalty resolution.  When the schedule does
      // not yield a value at this (s, b) — e.g. file-backed loader
      // produced an empty cell — fall back to 0 and skip emission.
      const auto block_overload_penalty =
          overload_penalty.at(stage.uid(), block.uid()).value_or(0.0);
      if (block_overload_penalty <= 0.0) {
        continue;
      }
      const auto block_overload_cost = CostHelper::block_ecost(
          scenario, stage, block, block_overload_penalty);

      const auto add_soft_cap_direction =
          [&](const std::vector<std::pair<ColIndex, double>>& terms,
              const OptTBRealSched& tmax_normal_sched,
              double block_tmax_hard,
              double rating_soft,
              int k_hard_span,
              std::string_view ov_name,
              auto& ov_cols_dst,
              auto& ov_rows_dst,
              std::string_view row_constraint_name)
      {
        if (terms.empty()) {
          return;
        }
        // Soft threshold: the static ``tmax_normal_*``, tightened by the
        // DLR normal rating when the DLR emergency tier is active.
        // ``replace``: the static normal rating is thermal too, so the
        // DLR normal rating (emergency tier) replaces it.
        auto soft_at = dlr_replace ? std::optional<double> {}
                                   : tmax_normal_sched.at(stage.uid(), buid);
        // Static normal rating under the overload band (PLEXOS ``Max
        // Rating`` with an ``Overload Max Rating`` hard cap).
        if (rating_soft < LinearProblem::DblMax) {
          soft_at = soft_at ? std::min(*soft_at, rating_soft) : rating_soft;
        }
        if (dlr_soft) {
          soft_at = soft_at ? std::min(*soft_at, *dlr_soft) : *dlr_soft;
        }
        if (!soft_at) {
          return;
        }
        const double block_tmax_soft = *soft_at;
        // Skip when soft cap is not strict relative to hard cap —
        // an equal or larger threshold makes the slack inert.
        if (!(block_tmax_soft < block_tmax_hard)) {
          return;
        }
        // Slack upper bound (``overload_slack_bound``):
        //   thermal   — min(hard − soft, ratio·soft): the thermal headroom
        //               (DLR: emergency − normal IEEE 738 rating), never
        //               above ``max_overload_ratio`` × normal; ratio·soft
        //               alone without a finite hard cap;
        //   ratio     — ratio·soft;
        //   unbounded — DblMax (the hard cap lives in the flow bound /
        //               thermal or span rows only).
        // A finite bound may tie with the hard cap at the emergency
        // rating; the span duals then also collect the slack's reduced
        // cost (refs below), so ∂obj/∂T stays well defined.
        const bool hard_finite = block_tmax_hard < LinearProblem::DblMax / 2;
        const double ratio_bound = slack_ratio * block_tmax_soft;
        double slack_uppb = LinearProblem::DblMax;
        bool slack_thermal = false;  // bound = hard − soft
        switch (slack_mode) {
          case LineOverloadSlackBound::thermal:
            if (hard_finite && block_tmax_hard - block_tmax_soft <= ratio_bound)
            {
              slack_uppb = block_tmax_hard - block_tmax_soft;
              slack_thermal = true;
            } else {
              slack_uppb = ratio_bound;
            }
            break;
          case LineOverloadSlackBound::ratio:
            slack_uppb = ratio_bound;
            break;
          case LineOverloadSlackBound::unbounded:
            break;
        }

        const auto ov_col = lp.add_col({
            .lowb = 0.0,
            .uppb = slack_uppb,
            .cost = block_overload_cost,
            .class_name = Element::class_name.full_name(),
            .variable_name = ov_name,
            .variable_uid = uid(),
            .context = block_ctx,
        });
        ov_cols_dst[buid] = ov_col;

        auto ov_row = SparseRow {
            .class_name = Element::class_name.full_name(),
            .constraint_name = row_constraint_name,
            .variable_uid = uid(),
            .context = block_ctx,
        };
        for (const auto& [col, coef] : terms) {
          ov_row[col] = coef;
        }
        ov_row[ov_col] = -1.0;
        const auto ov_row_idx =
            lp.add_row(std::move(ov_row.less_equal(block_tmax_soft)));
        ov_rows_dst[buid] = ov_row_idx;
        // DLR emergency tier: the band row's dual prices the NORMAL
        // rating of the argmin span (unless a lower static
        // ``tmax_normal`` is the threshold).
        if (dlr_soft && dlr_k_norm >= 0) {
          const double tol = 1e-9 * std::max(1.0, *dlr_soft);
          const int k_soft =
              (block_tmax_soft < *dlr_soft - tol) ? -1 : dlr_k_norm;
          dlr_refs_b[buid].push_back(DlrDualRef {
              .span = k_soft,
              .is_row = true,
              .index = static_cast<Index>(ov_row_idx),
          });
          // Slack at its upper bound U: its reduced cost r prices U, and
          // U depends on the ratings — thermal U = E − N (∂U/∂E = 1,
          // ∂U/∂N = −1), ratio U = ρ·N (∂U/∂N = ρ).  The ref's
          // ``rc / coef`` with coef = 1 / (∂U/∂T) adds r·∂U/∂T to the
          // span dual.
          if (slack_uppb < LinearProblem::DblMax) {
            const auto push_col_ref = [&](int k, double coef)
            {
              dlr_refs_b[buid].push_back(DlrDualRef {
                  .span = k,
                  .is_row = false,
                  .index = static_cast<Index>(ov_col),
                  .coef = coef,
                  .bound = slack_uppb,
              });
            };
            if (slack_thermal) {
              push_col_ref(k_hard_span, 1.0);
              push_col_ref(k_soft, -1.0);
            } else if (slack_ratio > 0.0) {
              push_col_ref(k_soft, 1.0 / slack_ratio);
            }
          }
        }
      };

      add_soft_cap_direction(band_p,
                             tmax_normal_ab,
                             hard_ab,
                             parts_ab.rating_soft,
                             dlr_k_hard_ab,
                             OverloadpName,
                             opcols,
                             oprows,
                             OverloadpName);
      add_soft_cap_direction(band_n,
                             tmax_normal_ba,
                             hard_ba,
                             parts_ba.rating_soft,
                             dlr_k_hard_ba,
                             OverloadnName,
                             oncols,
                             onrows,
                             OverloadnName);
    }
  }

  // Store all indices for this (scenario, stage) BEFORE emitting KVL
  // rows — the cycle_basis dispatcher reads `flowp_*` / `flown_*` /
  // `*_seg_cols` via the LineLP accessors when it runs at the system
  // level, so the persistent state must be in place by then.
  //
  // Outer-map assignment is **conditional on a non-empty inner**
  // (2026-05-14).  Pre-fix every (s, t) key was inserted even when
  // the per-block map was empty (e.g., `flowp_seg_cols` for non-
  // direct modes, `flowp_cols` for `piecewise_direct`), making the
  // `STBIndexHolder::empty()` gate in `add_to_output` useless — the
  // outer map always had entries.  The four flow-col accessors in
  // `line_lp.hpp` now return a static empty BIndexHolder when the
  // key is missing, so callers (the kirchhoff dispatcher, the
  // resolver) stay unchanged.
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto cond_assign = [&](auto& dst, auto&& src)
  {
    if (!src.empty()) {
      dst[st_key] = std::forward<decltype(src)>(src);
    }
  };
  cond_assign(capacityp_rows, std::move(cprows));
  cond_assign(capacityn_rows, std::move(cnrows));
  cond_assign(thermalp_rows, std::move(tprows));
  cond_assign(thermaln_rows, std::move(tnrows));
  cond_assign(flowp_cols, std::move(fpcols));
  cond_assign(flown_cols, std::move(fncols));
  cond_assign(flowp_lossfactor, std::move(fplossfac));
  cond_assign(flown_lossfactor, std::move(fnlossfac));
  cond_assign(flows_cols, std::move(fscols));
  cond_assign(lossp_cols, std::move(lpcols));
  cond_assign(lossn_cols, std::move(lncols));
  cond_assign(flowp_seg_cols, std::move(fpsegcols));
  cond_assign(flown_seg_cols, std::move(fnsegcols));
  cond_assign(flowp_seg_loss, std::move(fpsegloss));
  cond_assign(flown_seg_loss, std::move(fnsegloss));
  cond_assign(overloadp_cols, std::move(opcols));
  cond_assign(overloadn_cols, std::move(oncols));
  cond_assign(overloadp_rows, std::move(oprows));
  cond_assign(overloadn_rows, std::move(onrows));
  cond_assign(dlr_rating_values, std::move(dlr_rating_b));
  cond_assign(dlr_rating_emergency_values, std::move(dlr_rating_e_b));
  cond_assign(critical_span_values, std::move(critical_span_b));
  cond_assign(span_rows, std::move(span_rows_b));
  cond_assign(dlr_dual_refs, std::move(dlr_refs_b));
  cond_assign(limitp_refs, std::move(limp_b));
  cond_assign(limitn_refs, std::move(limn_b));

  // ``rows`` mode: report spans that never bind in this (scenario,
  // stage) — candidates to drop from the critical-span list.
  if (dlr_on && m_dlr_->mode() == LineDlrSpanMode::rows) {
    std::string never;
    for (const auto& [k, crit] : enumerate(span_critical)) {
      if (!crit) {
        never += std::format("{}'{}'",
                             never.empty() ? "" : ", ",
                             m_dlr_->spans()[static_cast<std::size_t>(k)].name);
      }
    }
    if (!never.empty()) {
      if (!m_dlr_never_critical_logged_) {
        m_dlr_never_critical_logged_ = true;
        spdlog::info(
            "Line '{}' dlr: span(s) {} never critical in scenario {} stage {} "
            "(dominated in every block; no rows emitted)",
            line().name,
            never,
            scenario.uid(),
            stage.uid());
      } else {
        SPDLOG_DEBUG("Line '{}' dlr: span(s) {} never critical (s={} t={})",
                     line().name,
                     never,
                     scenario.uid(),
                     stage.uid());
      }
    }
  }

  // ── Kirchhoff KVL rows ────────────────────────────────────────────
  // Dispatch by `kirchhoff_mode`:
  //   * node_angle: emits one KVL row per block here, returns a
  //     per-block `RowIndex` map.
  //   * cycle_basis: returns empty; KVL rows are emitted at the
  //     system level by `kirchhoff::cycle_basis::add_kvl_rows`
  //     after every line has finished creating its flow vars.
  //
  // Reads via the `*_cols_at` accessors so the conditional-insert
  // pattern above is transparent — the accessors return a static
  // empty `BIndexHolder<...>` when the outer key was skipped (e.g.,
  // `flowp_seg_cols` for non-direct-mode lines, `flowp_cols` for
  // piecewise_direct lines).  `.at(st_key)` would throw on those
  // missing keys.
  auto trows = kirchhoff::add_line_kvl_rows(sc,
                                            scenario,
                                            stage,
                                            lp,
                                            *this,
                                            bus_a_lp,
                                            bus_b_lp,
                                            flowp_cols_at(scenario, stage),
                                            flown_cols_at(scenario, stage),
                                            flowp_seg_cols_at(scenario, stage),
                                            flown_seg_cols_at(scenario, stage),
                                            flows_cols_at(scenario, stage));
  if (!trows.empty()) {
    theta_rows[st_key] = std::move(trows);
  }

  // ── Register PAMPL-visible columns ────────────────────────────────
  // `capainst` is registered centrally by CapacityBase::add_to_lp.
  //
  // `line.flow` is the ONLY AMPL-exposed line attribute.  The
  // per-direction flow columns (`flowp` / `flown` / `flows`), the loss
  // legs (`lossp` / `lossn`) and the overload slacks (`overloadp` /
  // `overloadn`) are internal LP decompositions with no user-constraint
  // use case and are NOT registered as AMPL attributes — they remain real
  // LP columns (used for KVL/loss rows and output), just not addressable
  // from PAMPL.  `flow` below folds the signed flow columns into one
  // weighted sum.
  //
  // ── line.flow — the ONLY AMPL-exposed flow attribute ──────────────
  // The signed physical flow, registered DIRECTLY as a weighted sum of
  // the underlying direction columns instead of as a resolve-time
  // compound: +flowp − flown (single-col), +Σflowp_seg − Σflown_seg
  // (piecewise_direct), or +flows (tangent_signed_flow).  flowp / flown
  // / flows are internal LP decompositions and are NOT exposed to user
  // constraints — `line("X").flow` is the only addressable flow.  This
  // single weighted-sum entry replaces the former three per-direction
  // registrations plus the class-level `flow = +flowp − flown` compound.
  BIndexHolder<std::vector<std::pair<ColIndex, double>>> flow_weighted;
  const auto accumulate_flow = [&](const auto& cols, double sign)
  {
    const auto it = cols.find(st_key);
    if (it == cols.end()) {
      return;
    }
    for (const auto& [buid, held] : it->second) {
      if constexpr (std::is_same_v<std::decay_t<decltype(held)>, ColIndex>) {
        flow_weighted[buid].emplace_back(held, sign);
      } else {  // BIndexHolder<std::vector<ColIndex>> — piecewise_direct
        for (const auto& col : held) {
          flow_weighted[buid].emplace_back(col, sign);
        }
      }
    }
  };
  accumulate_flow(flowp_cols, +1.0);
  accumulate_flow(flown_cols, -1.0);
  accumulate_flow(flowp_seg_cols, +1.0);
  accumulate_flow(flown_seg_cols, -1.0);
  accumulate_flow(flows_cols, +1.0);
  if (!flow_weighted.empty()) {
    sc.add_ampl_variable(
        ampl_name, uid(), FlowName, scenario, stage, flow_weighted);
  }

  return true;
}

// ── add_to_output ───────────────────────────────────────────────────

bool LineLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  if (is_loop()) {
    return true;
  }

  const auto pid = id();

  // Output policy (2026-05-14):
  //
  //   * Flow solutions and loss solutions are kept — they are the
  //     primary post-solve quantities consumers (analysis notebooks,
  //     PLP-comparison scripts, dashboards) read off lines.
  //   * Flow reduced costs are NOT emitted.  In aggregator modes the
  //     reduced cost of `flowp` / `flown` is recoverable from the
  //     bus-balance duals; in `piecewise_direct` the segment-sum
  //     interpretation of "reduced cost of an implicit aggregator"
  //     is ambiguous (Σ rc(seg_k) ≠ marginal cost of a +ε increase
  //     in |f|).  Better to omit than to emit a misleading scalar.
  //   * Theta-row duals and capacity-row duals are also dropped.
  //     The KVL theta dual is locational-marginal-price adjacent
  //     and easier to read off `Bus.balance:dual`; capacity duals
  //     are recoverable from CapacityBase's `capainst` reduced cost
  //     when expansion is active.  Skipping them saves I/O.
  //
  // Unified line-flow emission — ONE signed primal + ONE signed rc.
  //
  // Output stems: ``Line/flow_sol.parquet`` and ``Line/flow_cost.parquet``.
  // Convention (industry-aligned, PLEXOS / SDDP / GenX):
  //   * primal: ``flow = primal(flowp) − primal(flown)`` for two-column
  //     modes (bidirectional / linear / piecewise / piecewise_direct),
  //     or ``flow = primal(flows)`` for ``tangent_signed_flow`` mode.
  //     Signed scalar: positive ⇒ A→B direction, negative ⇒ B→A.
  //   * reduced cost: sign-based pick of the active direction's rc:
  //         flow > 0  ⇒ flow_cost = +rc[flowp]   (A→B active)
  //         flow < 0  ⇒ flow_cost = −rc[flown]   (B→A active)
  //         flow = 0  ⇒ flow_cost = 0            (line idle)
  //     For the single-column ``tangent_signed_flow`` mode, the LP
  //     rc on the signed flows column already encodes both bounds
  //     in one signed scalar — pass through verbatim.
  //
  // Drops the previously-emitted per-direction
  // ``flowp_sol`` / ``flown_sol`` stems (and their cost counterparts)
  // in favour of one signed ``flow`` stem — matches PLEXOS' "Net Flow",
  // SDDP's intercâmbio, and the convention every other gtopt
  // consumer (gtopt_check_output, gtopt_results_summary,
  // gtopt_marginal_units, plexos2gtopt/{compare_with_plexos,
  // auto_lift_lines}) wants once they're updated to read the new
  // shape: a single signed column instead of the two non-negative
  // pieces the LP happens to use internally.
  //
  // ──  ``extras:Line`` opt-in side-stream ────────────────────────────
  //
  // When the writer opts into ``--write-out ...,extras:Line``, two
  // additional stems are emitted alongside ``flow_*``:
  //   * ``flown_sol``   = ``primal(flown)`` (always ≥ 0) — the raw
  //     negative-direction primal; consumers that want the directional
  //     split can recover ``flowp = flow + flown``.
  //   * ``flown_cost``  = the reduced cost the SIGN-BASED rule did NOT
  //     pick for ``flow_cost``.  When ``flow > 0`` we picked
  //     ``rc[flowp]`` → ``flown_cost = rc[flown]``.  When ``flow < 0``
  //     we picked ``−rc[flown]`` → ``flown_cost = rc[flowp]``.  When
  //     ``flow = 0`` the LP is idle so both rcs are emitted as zero in
  //     the main stream; ``flown_cost`` carries ``rc[flown]``.
  STBIndexHolder<double> flow_signed_sol;
  STBIndexHolder<double> flow_signed_cost;
  STBIndexHolder<double> flown_extras_sol;
  STBIndexHolder<double> flown_extras_cost;

  // Under the dual-recovery bail the solve publishes the MIP primal
  // with NO reduced costs (`col_cost_span` empty) — reading
  // `out.cost()` there is a hardening abort (production SIGABRT,
  // 2026-07-13).  Build the signed primal streams as usual, feed the
  // sign-choice logic zeros, and skip the `:cost` emits below.
  const bool has_rc = out.has_col_costs();

  const auto fill_two_column = [&](const STBIndexHolder<ColIndex>& fp_holder,
                                   const STBIndexHolder<ColIndex>& fn_holder)
  {
    for (const auto& [st_key, p_blocks] : fp_holder) {
      auto& dst_p = flow_signed_sol[st_key];
      auto& dst_c = flow_signed_cost[st_key];
      const auto it_n = fn_holder.find(st_key);
      const bool has_flown_st = (it_n != fn_holder.end());
      const auto* n_blocks = has_flown_st ? &it_n->second : nullptr;
      // Only allocate the flown extras buckets when there is an actual
      // flown column at this (scenario, stage) — the LP doesn't always
      // emit one (e.g. ``tangent_signed_flow`` skips two-column setup
      // entirely; ``add_none`` has a single signed flowp).  Empty extras
      // STBIndexHolders short-circuit the emit call at the bottom.
      auto* dst_fn_p = has_flown_st ? &flown_extras_sol[st_key] : nullptr;
      auto* dst_fn_c = has_flown_st ? &flown_extras_cost[st_key] : nullptr;
      for (const auto& [buid, pcol] : p_blocks) {
        const double fp = out.primal(pcol);
        const double rcp = has_rc ? out.cost(pcol) : 0.0;
        double fn = 0.0;
        double rcn = 0.0;
        bool has_flown_cell = false;
        if (n_blocks != nullptr) {
          const auto it_b = n_blocks->find(buid);
          if (it_b != n_blocks->end()) {
            fn = out.primal(it_b->second);
            rcn = has_rc ? out.cost(it_b->second) : 0.0;
            has_flown_cell = true;
          }
        }
        const double signed_flow = fp - fn;
        dst_p[buid] = signed_flow;
        // Sign-based combined rc.  Equivalent to "rc of the active
        // LP column"; the inactive direction contributes 0 because
        // its primal is 0 and its rc tells us only about its OWN
        // lower-bound dual (irrelevant for the saturation question
        // on the SIGNED flow).
        if (signed_flow > 0.0) {
          dst_c[buid] = rcp;
        } else if (signed_flow < 0.0) {
          dst_c[buid] = -rcn;
        } else {
          dst_c[buid] = 0.0;
        }
        // Extras: only when this (line, cell) actually has a flown
        // column.  ``flown_extras_cost`` carries the NOT-selected rc.
        if (has_flown_cell && dst_fn_p != nullptr) {
          (*dst_fn_p)[buid] = fn;
          // The NOT-selected direction's rc: reverse flow active
          // (``signed_flow < 0``) selects flown, so the unselected is
          // flowp → ``rcp``; otherwise (forward active OR zero flow) the
          // unselected is flown → ``rcn``.
          (*dst_fn_c)[buid] = signed_flow < 0.0 ? rcp : rcn;
        }
      }
    }
  };

  // Aggregator-mode (none / linear / piecewise / bidirectional):
  // single column per direction in ``flowp_cols`` / ``flown_cols``.
  fill_two_column(flowp_cols, flown_cols);

  // ``tangent_signed_flow``: one signed LP column in ``flows_cols``
  // (``lowb = -tmax_ba``, ``uppb = +tmax_ab``).  Primal and rc pass
  // straight through; ``rc[flows] < 0`` ⇔ at the positive cap,
  // ``rc[flows] > 0`` ⇔ at the negative cap.
  for (const auto& [st_key, s_blocks] : flows_cols) {
    auto& dst_p = flow_signed_sol[st_key];
    auto& dst_c = flow_signed_cost[st_key];
    for (const auto& [buid, scol] : s_blocks) {
      dst_p[buid] = out.primal(scol);
      dst_c[buid] = has_rc ? out.cost(scol) : 0.0;
    }
  }

  // ``piecewise_direct`` mode: K per-segment columns per direction,
  // signed flow is ``Σ seg_p − Σ seg_n``.  The same sign-based combined
  // rc rule applies — but reduced cost on a per-segment kink is the
  // segment slope's gap and not a single scalar per (line, block);
  // for the saturation signal we use the smallest |rc| across the
  // segments in the active direction.
  const auto fill_segments_signed_flow = [&]()
  {
    if (flowp_seg_cols.empty() && flown_seg_cols.empty()) {
      return;
    }
    auto sum_segs = [&](const STBIndexHolder<std::vector<ColIndex>>& holder,
                        std::tuple<ScenarioUid, StageUid> st_key,
                        BlockUid buid) -> std::pair<double, double>
    {
      double f = 0.0;
      double rc_min_abs = std::numeric_limits<double>::infinity();
      const auto it = holder.find(st_key);
      if (it == holder.end()) {
        return {0.0, 0.0};
      }
      const auto& blocks = it->second;
      const auto it_b = blocks.find(buid);
      if (it_b == blocks.end()) {
        return {0.0, 0.0};
      }
      for (const auto& col : it_b->second) {
        f += out.primal(col);
        rc_min_abs =
            has_rc ? std::min(rc_min_abs, std::abs(out.cost(col))) : 0.0;
      }
      if (!std::isfinite(rc_min_abs)) {
        rc_min_abs = 0.0;
      }
      return {f, rc_min_abs};
    };

    std::set<std::tuple<ScenarioUid, StageUid>> st_keys;
    for (const auto& [k, _] : flowp_seg_cols) {
      st_keys.insert(k);
    }
    for (const auto& [k, _] : flown_seg_cols) {
      st_keys.insert(k);
    }
    for (const auto& st_key : st_keys) {
      std::set<BlockUid> buids;
      if (auto it = flowp_seg_cols.find(st_key); it != flowp_seg_cols.end()) {
        for (const auto& [b, _] : it->second) {
          buids.insert(b);
        }
      }
      if (auto it = flown_seg_cols.find(st_key); it != flown_seg_cols.end()) {
        for (const auto& [b, _] : it->second) {
          buids.insert(b);
        }
      }
      auto& dst_p = flow_signed_sol[st_key];
      auto& dst_c = flow_signed_cost[st_key];
      for (const auto buid : buids) {
        const auto [fp, rcp_abs] = sum_segs(flowp_seg_cols, st_key, buid);
        const auto [fn, rcn_abs] = sum_segs(flown_seg_cols, st_key, buid);
        const double signed_flow = fp - fn;
        dst_p[buid] = signed_flow;
        if (signed_flow > 0.0) {
          dst_c[buid] = -rcp_abs;
        } else if (signed_flow < 0.0) {
          dst_c[buid] = +rcn_abs;
        } else {
          dst_c[buid] = 0.0;
        }
      }
    }
  };
  fill_segments_signed_flow();

  out.add_col_sol_values(cname, FlowName, pid, flow_signed_sol);
  // No `:cost` stream under the dual-recovery bail — zeros would be
  // misleading; the stream is simply absent (same contract as the
  // index-emit path, whose `add_field` returns early on the empty
  // `col_cost_span`).
  if (has_rc) {
    out.add_col_cost_values(cname, FlowName, pid, flow_signed_cost);
  }
  // Extras (opt-in via ``--write-out extras`` or ``--write-out all``).
  // The unified `Line/flow_sol.parquet` (signed: + = A→B, − = B→A)
  // carries the full direction information; these directional raw
  // streams are useful only for LP-debug / arbitrage diagnostics, so
  // they ride the `extras` gate (off in the default `sol,dual,rc`).
  //   * ``flown_sol``  = raw flown primal (always ≥ 0); ``flowp = flow +
  //                       flown``
  //   * ``flowe_cost`` = EXCLUDED reduced cost (the rc that the sign-based
  //                      ``flow_cost`` rule did NOT pick).  Named ``flowe``
  //                      not ``flown`` so the suffix reads as "excluded",
  //                      not "negative direction".
  out.add_col_sol_values_extras(cname, FlownName, pid, flown_extras_sol);
  if (has_rc) {
    out.add_col_cost_values_extras(cname, FloweName, pid, flown_extras_cost);
  }

  // Consolidated loss output: piecewise / bidirectional only.  none /
  // linear / piecewise_direct don't create loss vars so this is a no-op.
  // The LP holds losses in two per-direction column sets (``lossp_cols``
  // for A→B, ``lossn_cols`` for B→A); on any given block at most one is
  // populated under the arbitrage-free PWL modes (``adaptive`` /
  // ``piecewise`` / ``bidirectional``).  Merge them per cell and emit
  // a single ``Line/loss_sol.parquet`` whose per-(line, block) value
  // is ``LP(lossp) + LP(lossn)`` — total dissipated energy regardless
  // of direction.  Emitted under the same ``sol`` gate as ``flow_sol``
  // so any default ``write_out`` that requests Line solutions also
  // gets the loss stream — required for post-solve loss-vs-flow audits
  // in ``gtopt_check_output``.
  //
  // The directional split is OPT-IN via the `extras` gate:
  // `Line/lossn_sol.parquet` carries the B→A leg.  Useful for
  // arbitrage diagnostics; the A→B leg is implicitly `loss − lossn`.
  // PAMPL UCs that want the per-block total still reference the
  // class-level compound `line.loss = +lossp + lossn`.
  if (!lossp_cols.empty() || !lossn_cols.empty()) {
    STBIndexHolder<std::vector<ColIndex>> loss_combined;
    auto merge_in = [&loss_combined](const auto& src)
    {
      for (const auto& [st_key, blocks] : src) {
        auto& dst_blocks = loss_combined[st_key];
        for (const auto& [buid, col] : blocks) {
          dst_blocks[buid].push_back(col);
        }
      }
    };
    merge_in(lossp_cols);
    merge_in(lossn_cols);
    out.add_col_sol(cname, LossName, pid, loss_combined);
  } else if (!flowp_seg_cols.empty() || !flown_seg_cols.empty()) {
    // ``piecewise_direct`` mode: no explicit loss LP column.  The loss
    // is stamped per-segment into the bus-balance rows via the factors
    // ``lf_k`` (saved in ``flowp_seg_loss`` / ``flown_seg_loss``,
    // parallel to ``flowp_seg_cols`` / ``flown_seg_cols``).  Reconstruct
    // the EXACT LP-consistent loss
    //   ``Σ_k lf_k · primal(seg_col_k)``
    // summed over BOTH directional segment sets.  Both directions are
    // summed (not netted) so the value captures loss-arbitrage cases
    // where positive- AND negative-direction segments are simultaneously
    // non-zero — a ``R·f²/V²``-at-net-flow approximation would hide that
    // and under-report pathological losses.  Emitted on the same
    // ``solution`` gate as the PWL-mode ``loss_sol`` (``add_col_sol_values``
    // applies the identity ``sol`` factor pipeline, so the on-disk shape
    // is bit-for-bit identical to the explicit-column form).  The
    // ``else`` guarantees we never double-emit: explicit loss columns and
    // direct segments are mutually exclusive per line.
    STBIndexHolder<double> loss_values;
    const auto accumulate_dir =
        [&](const STBIndexHolder<std::vector<ColIndex>>& cols_holder,
            const STBIndexHolder<std::vector<double>>& loss_holder)
    {
      for (const auto& [st_key, blocks] : cols_holder) {
        const auto lit = loss_holder.find(st_key);
        if (lit == loss_holder.end()) {
          continue;
        }
        auto& dst_blocks = loss_values[st_key];
        for (const auto& [buid, seg_cols] : blocks) {
          const auto lbit = lit->second.find(buid);
          if (lbit == lit->second.end()) {
            continue;
          }
          const auto& lf = lbit->second;
          double cell_loss = 0.0;
          const auto n = std::min(seg_cols.size(), lf.size());
          for (std::size_t k = 0; k < n; ++k) {
            cell_loss += lf[k] * out.primal(seg_cols[k]);
          }
          dst_blocks[buid] += cell_loss;
        }
      }
    };
    accumulate_dir(flowp_seg_cols, flowp_seg_loss);
    accumulate_dir(flown_seg_cols, flown_seg_loss);
    out.add_col_sol_values(cname, LossName, pid, loss_values);
  } else if (!flowp_lossfactor.empty() || !flown_lossfactor.empty()) {
    // ``linear`` mode: no explicit loss LP column and no per-segment
    // cols.  The line's loss is the linear factor ``config.lossfactor``
    // applied to the directional aggregator flow cols (stamped into the
    // bus balance via ``apply_linear_allocation``), captured per cell in
    // ``flowp_lossfactor`` / ``flown_lossfactor`` parallel to
    // ``flowp_cols`` / ``flown_cols``.  Reconstruct the LP-consistent
    // per-(line, block) loss
    //   ``flowp_lossfactor · primal(flowp) + flown_lossfactor · primal(flown)``
    // summed over BOTH directions (not netted) so simultaneous bi-
    // directional flow is captured rather than hidden.  Emitted on the
    // same ``solution`` gate as the other modes' ``loss_sol`` via
    // ``add_col_sol_values`` (identity ``sol`` factor pipeline → bit-
    // identical on-disk shape).  Reached only when branches 1 (explicit
    // loss cols) and 2 (piecewise_direct seg cols) did NOT fire — the
    // ``else if`` chain guarantees no double-emit; the non-empty
    // lossfactor-holder guard additionally makes this a no-op for any
    // other mode that happens to populate ``flowp_cols`` but not the
    // lossfactor maps (those modes leave ``fp_loss`` / ``fn_loss`` at 0).
    STBIndexHolder<double> loss_values;
    const auto accumulate_linear_dir =
        [&](const STBIndexHolder<ColIndex>& cols_holder,
            const STBIndexHolder<double>& loss_holder)
    {
      for (const auto& [st_key, blocks] : cols_holder) {
        const auto lit = loss_holder.find(st_key);
        if (lit == loss_holder.end()) {
          continue;
        }
        auto& dst_blocks = loss_values[st_key];
        for (const auto& [buid, col] : blocks) {
          const auto lbit = lit->second.find(buid);
          if (lbit == lit->second.end()) {
            continue;
          }
          dst_blocks[buid] += lbit->second * out.primal(col);
        }
      }
    };
    accumulate_linear_dir(flowp_cols, flowp_lossfactor);
    accumulate_linear_dir(flown_cols, flown_lossfactor);
    out.add_col_sol_values(cname, LossName, pid, loss_values);
  }
  // Extras-gated per-direction loss (B→A leg).  `lossn_cols` is empty
  // for ``tangent_signed_flow`` / ``none`` / ``linear`` /
  // ``piecewise_direct`` modes; the helper is a no-op in those cases.
  if (!lossn_cols.empty()) {
    out.add_col_sol_extras(cname, LossnName, pid, lossn_cols);
  }

  // Shadow prices of the line limits [$/MWh, physical]: the expandable-
  // line capacity rows ``capacity − f_dir (− a·ℓ_dir) ≥ 0`` and the
  // thermal-limit rows ``f_dir + a·ℓ_dir ≤ T_dir`` of
  // ``line_thermal_limit_mode = sending | full``.  Under those modes the
  // congestion rent moves from the flow column's reduced cost
  // (``flow_cost``) to these rows.  Empty holders (non-expandable line,
  // ``flow`` mode) emit nothing.
  out.add_row_dual(cname, CapacitypName, pid, capacityp_rows);
  out.add_row_dual(cname, CapacitynName, pid, capacityn_rows);
  out.add_row_dual(cname, ThermalpName, pid, thermalp_rows);
  out.add_row_dual(cname, ThermalnName, pid, thermaln_rows);
  // Per-part limit duals ``∂obj/∂L`` (lines with rating limits): the
  // dual of the one bound / row carrying the effective limit, reported
  // under the part that binds (``conductorp_dual``, ``ratingp_dual``,
  // ``outagep_dual``, ``equipmentp_dual``, ``securityp_dual``,
  // ``flowlimitp_dual`` and the ``n`` counterparts).
  if (m_rating_limits_ && out.has_row_duals() && out.has_col_costs()) {
    const auto emit_parts =
        [&](const STBIndexHolder<LimitRef>& refs, const auto& names)
    {
      std::array<STBIndexHolder<double>, 6> vals;
      for (const auto& [st, blocks] : refs) {
        const auto sd = span_duals(
            st,
            [&](ColIndex c) { return out.primal(c); },
            [&](ColIndex c) { return out.cost(c); },
            [&](RowIndex r) { return out.dual(r); });
        for (const auto& [buid, r] : blocks) {
          double span = 0.0;
          if (const auto it = sd.find(buid); it != sd.end()) {
            for (const double x : it->second) {
              span += x;
            }
          }
          vals[static_cast<std::size_t>(r.part)][st][buid] = limit_dual(
              r,
              span,
              [&](ColIndex c) { return out.primal(c); },
              [&](ColIndex c) { return out.cost(c); },
              [&](RowIndex rr) { return out.dual(rr); });
        }
      }
      for (const auto& [k, v] : enumerate(vals)) {
        if (!v.empty()) {
          out.add_row_dual_values(
              cname, names[static_cast<std::size_t>(k)], pid, v);
        }
      }
    };
    emit_parts(limitp_refs, LimitPartpNames);
    emit_parts(limitn_refs, LimitPartnNames);
  }

  // Dynamic line rating: effective rating, binding span and per-span
  // rating duals ``∂obj/∂T_k`` (keyed by SPAN uid).  In ``min`` mode the
  // dual of the effective cap (flow reduced cost / thermal row dual) is
  // attributed to the argmin span; in ``rows`` mode each span row
  // carries its own dual.  The overload-band row (emergency tier) adds
  // its dual to the normal-tier argmin span in both modes.
  if (m_dlr_) {
    out.add_col_sol_values(cname, DlrRatingName, pid, dlr_rating_values);
    out.add_col_sol_values(
        cname, DlrRatingEmergencyName, pid, dlr_rating_emergency_values);
    out.add_col_sol_values(cname, CriticalSpanName, pid, critical_span_values);
    if (out.has_row_duals() && out.has_col_costs() && !dlr_dual_refs.empty()) {
      const auto spans = m_dlr_->spans();
      std::vector<STBIndexHolder<double>> per_span(spans.size());
      for (const auto& [st, _] : dlr_dual_refs) {
        const auto duals = span_duals(
            st,
            [&](ColIndex c) { return out.primal(c); },
            [&](ColIndex c) { return out.cost(c); },
            [&](RowIndex r) { return out.dual(r); });
        for (const auto& [buid, v] : duals) {
          for (const auto& [k, d] : enumerate(v)) {
            per_span[static_cast<std::size_t>(k)][st][buid] = d;
          }
        }
      }
      for (const auto& [k, span] : enumerate(spans)) {
        out.add_row_dual_values(cname,
                                SpanThermalName,
                                Id {span.uid, span.name},
                                per_span[static_cast<std::size_t>(k)]);
      }
    }
  }

  // Overload-slack solutions and costs: only populated when the
  // soft-cap feature is active for this line (see `add_to_lp`).
  // No-op for lines without `tmax_normal_*` + `overload_penalty`.
  // Used today only by audits that look for "did the LP violate the
  // soft tmax_normal cap?" — `extras`-gated so the default footprint
  // doesn't pay for them on cases where the feature is unused.
  if (!overloadp_cols.empty()) {
    out.add_col_sol_extras(cname, OverloadpName, pid, overloadp_cols);
    out.add_col_cost_extras(cname, OverloadpName, pid, overloadp_cols);
  }
  if (!overloadn_cols.empty()) {
    out.add_col_sol_extras(cname, OverloadnName, pid, overloadn_cols);
    out.add_col_cost_extras(cname, OverloadnName, pid, overloadn_cols);
  }

  return CapacityBase::add_to_output(out);
}

}  // namespace gtopt
