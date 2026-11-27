/**
 * @file      line_lp.hpp
 * @brief     LP formulation for transmission lines
 * @date      Sat Mar 29 19:02:33 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the LineLP class which formulates transmission line
 * constraints for the LP model.  Loss modeling is delegated to the modular
 * losses engine in line_losses.hpp, which supports multiple modes:
 * none, linear, piecewise, bidirectional, adaptive, and dynamic.
 *
 * @see line_losses.hpp for the loss model implementations.
 * @see line_enums.hpp  for LineLossesMode enum documentation.
 */

#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <optional>
#include <tuple>
#include <vector>

#include <gtopt/bus_lp.hpp>
#include <gtopt/capacity_object_lp.hpp>
#include <gtopt/generator.hpp>
#include <gtopt/line.hpp>
#include <gtopt/line_direction.hpp>
#include <gtopt/line_dlr_lp.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

class LineLP;

/// SingleId-style FK alias for ``LineLP``.  Used by elements that
/// reference a Line through ``ElementIndex`` (e.g. ``LineCommitmentLP``
/// per issue #509).
using LineLPSId = ObjectSingleId<LineLP>;

class LineLP : public CapacityObjectLP<Line>
{
public:
  static constexpr std::string_view FlowName {
      "flow"};  ///< compound signed primal: `flowp − flown`
  /// Excluded-direction stems (extras gate): ``flown_sol`` is the raw
  /// negative-direction primal (always ≥ 0; only when the LP has two
  /// directional columns), and ``flowe_cost`` is the EXCLUDED reduced
  /// cost — the rc that the sign-based ``flow_cost`` rule did NOT
  /// pick.  ``flowe`` (not ``flown``) for the rc so the naming
  /// signals "excluded", not "negative direction".
  static constexpr std::string_view FlownName {"flown"};
  static constexpr std::string_view FloweName {"flowe"};
  /// Internal-only LP variable names used during ``add_to_lp`` (not
  /// emitted in default output stems; consumers read ``flow_sol`` /
  /// ``flow_cost`` / ``flown_sol`` / ``flowe_cost`` instead).
  static constexpr std::string_view FlowpName {"flowp"};
  /// Signed flow column emitted by `LineLossesMode::tangent_signed_flow`
  /// (Coffrin outer approximation; one column per (line, block) covering
  /// `[−tmax_ba, +tmax_ab]`).  Distinct from `FlowpName` / `FlownName`
  /// so AMPL / output consumers can opt in to the signed shape when
  /// they want it; existing tools keep reading `flowp_sol` / `flown_sol`
  /// (derived `max(0, f)` / `max(0, −f)` are emitted for back-compat).
  static constexpr std::string_view FlowsName {"flows"};
  /// Auxiliary `|f|`-envelope column emitted by
  /// `LineLossesMode::tangent_signed_flow` to tighten the upper bound on
  /// the loss column.  Bounded `[0, fmax]` and tied to the signed flow
  /// column `f` via two rows `v ≥ +f` and `v ≥ −f` (so `v ≥ |f|` at the
  /// optimum).  Replaces the loose constant loss UB `ℓ ≤ R·fmax²/V²`
  /// with the linear-in-`v` chord `ℓ ≤ (R·fmax/V²)·v`, which is the
  /// secant chord of the convex quadratic `R·v²/V²` on `[0, fmax]`.
  /// See `add_tangent_signed_flow` for the math + derivation.
  static constexpr std::string_view FlowAbsName {"flow_abs"};
  /// Lambda-form SOS2 L-secant breakpoint weight col (issue #504 SOS2
  /// path).  ``2L+1`` cols per (line, block) emitted when
  /// ``tangent_signed_flow`` is paired with ``loss_use_sos2 = true``
  /// and ``loss_secant_segments > 1``.  Indexed ``0..2L`` so each
  /// label encodes the breakpoint position ``b_l = (l − L) · w``
  /// on ``[−fmax, +fmax]``.
  static constexpr std::string_view FlowLambdaName {"flow_lambda"};
  static constexpr std::string_view LosspName {"lossp"};
  static constexpr std::string_view LossnName {"lossn"};
  /// Consolidated per-(line, block) loss output emitted as
  /// ``Line/loss_sol.parquet`` under the ``solution`` write-out gate
  /// (NOT ``extras`` — a previous version of this note was stale; the
  /// emitter is the ``solution``-gated ``add_col_sol`` /
  /// ``add_col_sol_values`` path).  For the PWL modes
  /// (``piecewise`` / ``bidirectional`` / ``adaptive``) it equals
  /// ``LP(lossp) + LP(lossn)`` per cell — at most one of the two
  /// direction-specific cols is populated on any given block, so the
  /// sum is the total dissipated energy on that line that block.  For
  /// ``piecewise_direct`` (no explicit loss column) it is reconstructed
  /// as ``Σ_k lf_k · seg_k_sol`` summed over both directional segment
  /// sets (see ``add_to_output``).  Replaces the paired ``lossp_sol`` /
  /// ``lossn_sol`` emission so downstream consumers see a single,
  /// schema-stable loss stream regardless of which direction the LP
  /// routed flow.
  static constexpr std::string_view LossName {"loss"};
  static constexpr std::string_view CapacitypName {"capacityp"};
  static constexpr std::string_view CapacitynName {"capacityn"};
  /// Thermal-limit rows ``f_dir + a·ℓ_dir ≤ T_dir`` emitted only under
  /// ``line_thermal_limit_mode = sending | full`` (see
  /// ``LineThermalLimitMode``).
  static constexpr std::string_view ThermalpName {"thermalp"};
  static constexpr std::string_view ThermalnName {"thermaln"};
  /// Binding part of the line limit (lines with rating / classified
  /// limits).  The effective limit per direction and block is the min
  /// over the SET parts; one flow bound / thermal row carries it, and its
  /// dual is attributed to the binding part (ties: earliest in this
  /// order) as ``<part>{p,n}_dual``: ``conductor`` (static conductor
  /// rating or DLR), ``rating`` (max/min rating, or the overload rating
  /// under the band), ``outage``, ``equipment``, ``security``,
  /// ``flowlimit`` (max/min flow, ``tmax``).
  enum class LimitPart : uint8_t
  {
    conductor = 0,
    rating,
    outage,
    equipment,
    security,
    flowlimit,
  };
  static constexpr std::array<std::string_view, 6> LimitPartpNames {
      "conductorp",
      "ratingp",
      "outagep",
      "equipmentp",
      "securityp",
      "flowlimitp",
  };
  static constexpr std::array<std::string_view, 6> LimitPartnNames {
      "conductorn",
      "ratingn",
      "outagen",
      "equipmentn",
      "securityn",
      "flowlimitn",
  };
  /// One direction's binding limit in a block: the part and the bound /
  /// row carrying it.  ``span_sum``: the DLR binds (conductor) — the dual
  /// is the sum of the block's span duals.
  struct LimitRef
  {
    LimitPart part {LimitPart::flowlimit};
    bool span_sum {false};
    bool is_row {true};
    Index index {};
    double coef {1.0};
    double bound {0.0};
  };
  static constexpr std::string_view ThetaName {"theta"};
  /// Overload-slack column emitted only when ``Line.tmax_normal_ab`` and
  /// ``Line.overload_penalty`` are configured.  Equals ``max(0, flowp −
  /// tmax_normal_ab)`` and is bounded by ``tmax_ab − tmax_normal_ab``.
  static constexpr std::string_view OverloadpName {"overloadp"};
  /// B→A counterpart of ``overloadp``.
  static constexpr std::string_view OverloadnName {"overloadn"};
  /// Dynamic line rating (``Line.dlr``) names.  ``span_thermalp`` /
  /// ``span_thermaln`` label the per-span rows of ``span_mode = rows``
  /// (block context carries the span position).  The output stems are
  /// ``dlr_rating_sol`` (effective normal rating [MW]),
  /// ``dlr_rating_emergency_sol`` (emergency tier), ``critical_span_sol``
  /// (uid of the binding span, ``-1`` when the static ``tmax`` is lower)
  /// and ``span_thermal_dual`` (per-span rating dual, keyed by SPAN uid).
  static constexpr std::string_view SpanThermalpName {"span_thermalp"};
  static constexpr std::string_view SpanThermalnName {"span_thermaln"};
  static constexpr std::string_view SpanThermalName {"span_thermal"};
  static constexpr std::string_view DlrRatingName {"dlr_rating"};
  static constexpr std::string_view DlrRatingEmergencyName {
      "dlr_rating_emergency"};
  static constexpr std::string_view CriticalSpanName {"critical_span"};

  /// One contribution to a span's rating dual: either a row whose dual
  /// is ``∂obj/∂T`` directly, or a flow column bounded at ``T/coef``
  /// whose reduced cost divided by ``coef`` is ``∂obj/∂T`` while the
  /// column sits at that bound.  ``span < 0`` means the static ``tmax``
  /// (not a span) is the binding cap.
  struct DlrDualRef
  {
    int span {-1};
    bool is_row {true};
    Index index {};
    double coef {1.0};
    double bound {0.0};
  };

  /// Filter metadata keys published by `add_to_lp` for `sum(...)`
  /// predicate matching.
  static constexpr std::string_view TypeKey {"type"};
  static constexpr std::string_view BusAKey {"bus_a"};
  static constexpr std::string_view BusBKey {"bus_b"};

  using CapacityBase = CapacityObjectLP<Line>;

  /**
   * @brief Returns the underlying Line object with proper forwarding semantics
   * @param self The calling object (implicit)
   * @return Reference to the Line object with same value category and
   * const-ness as self
   */
  [[nodiscard]] constexpr auto&& line(this auto&& self) noexcept
  {
    // Forward the object() call with same value category as self
    return self.object();
  }

  [[nodiscard]] constexpr auto bus_a_sid() const noexcept -> BusLPSId
  {
    return BusLPSId {line().bus_a};
  }

  [[nodiscard]] constexpr auto bus_b_sid() const noexcept -> BusLPSId
  {
    return BusLPSId {line().bus_b};
  }

  [[nodiscard]] constexpr bool is_loop() const
  {
    return line().bus_a == line().bus_b;
  }

  /// Effective flow direction of this line: an explicit
  /// `flow_direction = "ab" | "ba" | "both"` wins; otherwise (unset or
  /// `"auto"`) the structural decision computed by `SystemLP` from
  /// `model_options.line_direction_mode` (`both` when detection is off).
  [[nodiscard]] line_direction::Verdict effective_flow_direction(
      const SystemContext& sc) const;

  explicit LineLP(const Line& pline, const InputContext& ic);

  bool add_to_lp(SystemContext& sc,
                 const ScenarioLP& scenario,
                 const StageLP& stage,
                 LinearProblem& lp);

  bool add_to_output(OutputContext& out) const;

  /// All four flow-col accessors below tolerate a missing
  /// `(scenario, stage)` outer key by returning a reference to a
  /// static empty BIndexHolder via `find_or_empty_inner`.
  /// Pre-2026-05-14 the assignment in ``add_to_lp`` was unconditional
  /// even when the inner map was empty (e.g. `flowp_seg_cols` for
  /// non-direct modes, `flowp_cols` for `piecewise_direct`), keeping
  /// the outer key present.  The new conditional-assignment pattern
  /// only inserts the outer key when at least one block populated
  /// the inner map, so callers must handle the missing-key case —
  /// these helpers centralise it.
  [[nodiscard]] constexpr const auto& flowp_cols_at(const ScenarioLP& scenario,
                                                    const StageLP& stage) const
  {
    return find_or_empty_inner(flowp_cols, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& flown_cols_at(const ScenarioLP& scenario,
                                                    const StageLP& stage) const
  {
    return find_or_empty_inner(flown_cols, scenario, stage);
  }

  /// Per-block signed-flow column map (populated only under
  /// `LineLossesMode::tangent_signed_flow`).  When present, the
  /// `flowp_cols` / `flown_cols` maps are empty for this `(scenario,
  /// stage)` and KVL stamps `+x_τ` on each signed flow column.  Same
  /// missing-key tolerance as the other flow accessors.
  [[nodiscard]] constexpr const auto& flows_cols_at(const ScenarioLP& scenario,
                                                    const StageLP& stage) const
  {
    return find_or_empty_inner(flows_cols, scenario, stage);
  }

  /// Per-block segment columns for the A→B direction (only populated
  /// in `piecewise_direct` line-loss mode; empty otherwise).  Returns
  /// the per-block map; each block maps to a `std::vector<ColIndex>`
  /// of segment cols.  Used by `kirchhoff::cycle_basis::add_kvl_rows`
  /// to stamp segments directly into per-cycle KVL rows when the
  /// flowp aggregator was elided.
  [[nodiscard]] constexpr const auto& flowp_seg_cols_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(flowp_seg_cols, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& flown_seg_cols_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(flown_seg_cols, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& lossp_cols_at(const ScenarioLP& scenario,
                                                    const StageLP& stage) const
  {
    return find_or_empty_inner(lossp_cols, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& lossn_cols_at(const ScenarioLP& scenario,
                                                    const StageLP& stage) const
  {
    return find_or_empty_inner(lossn_cols, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& capacityp_rows_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(capacityp_rows, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& capacityn_rows_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(capacityn_rows, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& thermalp_rows_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(thermalp_rows, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& thermaln_rows_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(thermaln_rows, scenario, stage);
  }

  /// Overload-slack column accessors.  Return a reference to a static
  /// empty BIndexHolder when the soft-cap feature is not active for
  /// this `(scenario, stage)` (no `tmax_normal_*` + positive
  /// `overload_penalty` resolved here).  Mirrors the missing-key
  /// tolerance of the flow accessors above so tests / PAMPL consumers
  /// can blindly query without branching.
  [[nodiscard]] constexpr const auto& overloadp_cols_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(overloadp_cols, scenario, stage);
  }

  [[nodiscard]] constexpr const auto& overloadn_cols_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(overloadn_cols, scenario, stage);
  }

  /// Dynamic line rating (``Line.dlr``), or nullptr when unset.
  [[nodiscard]] constexpr const LineDlrLP* dlr() const noexcept
  {
    return m_dlr_ ? &*m_dlr_ : nullptr;
  }

  /// Per-block effective DLR normal rating [MW] (A→B, capped by the
  /// static ``tmax_ab``).  Empty when DLR is off.
  [[nodiscard]] constexpr const auto& dlr_rating_at(const ScenarioLP& scenario,
                                                    const StageLP& stage) const
  {
    return find_or_empty_inner(dlr_rating_values, scenario, stage);
  }

  /// Per-block uid of the binding (argmin, normal tier) span; ``-1`` when
  /// the static ``tmax_ab`` is strictly lower than every span.
  [[nodiscard]] constexpr const auto& critical_span_at(
      const ScenarioLP& scenario, const StageLP& stage) const
  {
    return find_or_empty_inner(critical_span_values, scenario, stage);
  }

  /// Per-block per-span rows of ``span_mode = rows`` (both directions).
  [[nodiscard]] constexpr const auto& span_rows_at(const ScenarioLP& scenario,
                                                   const StageLP& stage) const
  {
    return find_or_empty_inner(span_rows, scenario, stage);
  }

  /// Per-block, per-span rating duals ``∂obj/∂T_k`` (raw LP scale, i.e.
  /// before the per-block cost-factor unfolding) evaluated from the
  /// given primal / reduced-cost / row-dual accessors.  Index ``k`` of
  /// each vector is the span position in ``dlr()->spans()``.
  template<typename Primal, typename Cost, typename Dual>
  [[nodiscard]] auto span_duals(const std::tuple<ScenarioUid, StageUid>& st,
                                Primal&& primal,
                                Cost&& cost,
                                Dual&& dual) const
      -> BIndexHolder<std::vector<double>>
  {
    BIndexHolder<std::vector<double>> out;
    if (!m_dlr_) {
      return out;
    }
    const auto it = dlr_dual_refs.find(st);
    if (it == dlr_dual_refs.end()) {
      return out;
    }
    const auto nspan = m_dlr_->spans().size();
    for (const auto& [buid, refs] : it->second) {
      auto& v = out[buid];
      v.assign(nspan, 0.0);
      for (const auto& r : refs) {
        if (r.span < 0 || static_cast<std::size_t>(r.span) >= nspan) {
          continue;
        }
        double d = 0.0;
        if (r.is_row) {
          d = dual(RowIndex {r.index});
        } else {
          const auto col = ColIndex {r.index};
          const double p = primal(col);
          const double tol = 1e-7 * std::max(1.0, std::abs(r.bound));
          if (std::abs(p - r.bound) <= tol) {
            d = cost(col) / r.coef;
          }
        }
        v[static_cast<std::size_t>(r.span)] += d;
      }
    }
    return out;
  }

  /// Dual ``∂obj/∂L`` (raw LP scale) of one binding-limit ref; ``span``
  /// is the block's summed span dual (used when ``r.span_sum``).
  template<typename Primal, typename Cost, typename Dual>
  [[nodiscard]] static double limit_dual(
      const LimitRef& r, double span, Primal&& primal, Cost&& cost, Dual&& dual)
  {
    if (r.span_sum) {
      return span;
    }
    if (r.is_row) {
      return dual(RowIndex {r.index});
    }
    const auto col = ColIndex {r.index};
    const double tol = 1e-7 * std::max(1.0, std::abs(r.bound));
    return std::abs(primal(col) - r.bound) <= tol ? cost(col) / r.coef : 0.0;
  }

  /// Binding-limit refs per (scenario, stage) and block (A→B / B→A).
  [[nodiscard]] constexpr const auto& limitp_refs_at(const ScenarioLP& scenario,
                                                     const StageLP& stage) const
  {
    return find_or_empty_inner(limitp_refs, scenario, stage);
  }
  [[nodiscard]] constexpr const auto& limitn_refs_at(const ScenarioLP& scenario,
                                                     const StageLP& stage) const
  {
    return find_or_empty_inner(limitn_refs, scenario, stage);
  }

  /// Check if this line created Kirchhoff (theta) rows for a given
  /// (scenario, stage) pair.
  [[nodiscard]] constexpr bool has_theta_rows(
      const std::pair<ScenarioUid, StageUid>& st_key) const
  {
    return theta_rows.contains(st_key) && !theta_rows.at(st_key).empty();
  }

  /// Per-(scenario, stage) Kirchhoff KVL row indices.  Returns the
  /// inner per-block map for the ``(scenario, stage)`` cell, or a
  /// static empty map when this line has no KVL rows for that cell
  /// (radial network, ``node_angle`` mode skipped, etc.).  Read by
  /// ``LineCommitmentLP`` (issue #509) when the OTS big-M
  /// disjunction rewrite needs to mutate the existing equality row.
  [[nodiscard]] constexpr const auto& theta_rows_at(const ScenarioLP& scenario,
                                                    const StageLP& stage) const
  {
    return find_or_empty_inner(theta_rows, scenario, stage);
  }

  /// @name Parameter accessors for user constraint resolution
  /// @{
  /// Flow limit (PLEXOS ``Max Flow`` / ``Min Flow``) per direction as a
  /// magnitude: ``tmax_ab`` (= ``max_flow``) and ``tmax_ba`` (=
  /// ``-min_flow``).  nullopt = no flow limit.
  [[nodiscard]] std::optional<double> param_tmax_ab(StageUid s,
                                                    BlockUid b) const
  {
    return lim(tmax_ab, s, b);
  }
  [[nodiscard]] std::optional<double> param_tmax_ba(StageUid s,
                                                    BlockUid b) const
  {
    if (tmax_ba.has_value()) {
      return lim(tmax_ba, s, b);
    }
    return lim(min_flow, s, b, /*negate=*/true);
  }
  /// Static limit envelope [MW] at (s, b): max over both directions of
  /// the min over the set static parts (flow, max(rating, overload),
  /// conductor, equipment, security); 0 when none is set.  Spans the loss
  /// PWL.
  [[nodiscard]] double limit_envelope(StageUid s, BlockUid b) const
  {
    double env = 0.0;
    for (const bool fwd : {true, false}) {
      auto v = fwd ? param_tmax_ab(s, b) : param_tmax_ba(s, b);
      const auto take = [&v](std::optional<double> x)
      {
        if (x) {
          v = v ? std::min(*v, *x) : *x;
        }
      };
      auto r = fwd ? lim(max_rating, s, b) : lim(min_rating, s, b, true);
      const auto o = fwd ? lim(overload_max_rating, s, b)
                         : lim(overload_min_rating, s, b, true);
      if (r && o) {
        r = std::max(*r, *o);
      } else if (o) {
        r = o;
      }
      take(r);
      take(lim(fwd ? conductor_rating_ab : conductor_rating_ba, s, b));
      take(lim(fwd ? equipment_rating_ab : equipment_rating_ba, s, b));
      take(lim(fwd ? security_limit_ab : security_limit_ba, s, b));
      if (v) {
        env = std::max(env, *v);
      }
    }
    return env;
  }
  /// Raw (signed, PLEXOS-convention) limit fields for PAMPL.
  [[nodiscard]] std::optional<double> param_min_flow(StageUid s,
                                                     BlockUid b) const
  {
    const auto v = param_tmax_ba(s, b);
    return v ? std::optional {-*v} : std::nullopt;
  }
  [[nodiscard]] std::optional<double> param_max_rating(StageUid s,
                                                       BlockUid b) const
  {
    return lim(max_rating, s, b);
  }
  [[nodiscard]] std::optional<double> param_min_rating(StageUid s,
                                                       BlockUid b) const
  {
    return lim(min_rating, s, b);
  }
  [[nodiscard]] std::optional<double> param_overload_max_rating(
      StageUid s, BlockUid b) const
  {
    return lim(overload_max_rating, s, b);
  }
  [[nodiscard]] std::optional<double> param_overload_min_rating(
      StageUid s, BlockUid b) const
  {
    return lim(overload_min_rating, s, b);
  }
  [[nodiscard]] std::optional<double> param_outage_max_rating(StageUid s,
                                                              BlockUid b) const
  {
    return lim(outage_max_rating, s, b);
  }
  [[nodiscard]] std::optional<double> param_outage_min_rating(StageUid s,
                                                              BlockUid b) const
  {
    return lim(outage_min_rating, s, b);
  }
  [[nodiscard]] std::optional<double> param_units_out(StageUid s,
                                                      BlockUid b) const
  {
    return lim(units_out, s, b);
  }
  [[nodiscard]] std::optional<double> param_conductor_rating_ab(
      StageUid s, BlockUid b) const
  {
    return lim(conductor_rating_ab, s, b);
  }
  [[nodiscard]] std::optional<double> param_conductor_rating_ba(
      StageUid s, BlockUid b) const
  {
    return lim(conductor_rating_ba, s, b);
  }
  [[nodiscard]] std::optional<double> param_equipment_rating_ab(
      StageUid s, BlockUid b) const
  {
    return lim(equipment_rating_ab, s, b);
  }
  [[nodiscard]] std::optional<double> param_equipment_rating_ba(
      StageUid s, BlockUid b) const
  {
    return lim(equipment_rating_ba, s, b);
  }
  [[nodiscard]] std::optional<double> param_security_limit_ab(StageUid s,
                                                              BlockUid b) const
  {
    return lim(security_limit_ab, s, b);
  }
  [[nodiscard]] std::optional<double> param_security_limit_ba(StageUid s,
                                                              BlockUid b) const
  {
    return lim(security_limit_ba, s, b);
  }
  [[nodiscard]] auto param_tcost(StageUid s, BlockUid b) const
  {
    return tcost.at(s, b);
  }
  [[nodiscard]] auto param_reactance(StageUid s) const
  {
    return reactance.at(s);
  }
  [[nodiscard]] auto param_voltage(StageUid s) const { return voltage.at(s); }
  [[nodiscard]] auto param_tap_ratio(StageUid s) const
  {
    return tap_ratio.at(s);
  }
  [[nodiscard]] auto param_phase_shift_deg(StageUid s) const
  {
    return phase_shift_deg.at(s);
  }
  [[nodiscard]] auto param_lossfactor(StageUid s, BlockUid b) const
  {
    return lossfactor.at(s, b);
  }
  [[nodiscard]] auto param_resistance(StageUid s) const
  {
    return resistance.at(s);
  }
  [[nodiscard]] auto param_loss_envelope(StageUid s) const
  {
    return loss_envelope.at(s);
  }
  /// @}

private:
  /// Schedule value at (s, b); nullopt when unset or not finite (a NaN /
  /// missing cell means "no limit", never 0).  ``negate`` flips PLEXOS
  /// signed ``Min *`` values into B→A magnitudes.
  [[nodiscard]] static std::optional<double> lim(const OptTBRealSched& sched,
                                                 StageUid s,
                                                 BlockUid b,
                                                 bool negate = false)
  {
    const auto v = sched.at(s, b);
    if (!v || !std::isfinite(*v)) {
      return std::nullopt;
    }
    return negate ? -*v : *v;
  }

  OptTBRealSched tmax_ba;
  OptTBRealSched tmax_ab;
  /// PLEXOS limit fields and the limit classification (``Line``); every
  /// one optional (unset / NaN cell = no limit from that field).
  OptTBRealSched min_flow;
  OptTBRealSched max_rating;
  OptTBRealSched min_rating;
  OptTBRealSched overload_max_rating;
  OptTBRealSched overload_min_rating;
  OptTBRealSched outage_max_rating;
  OptTBRealSched outage_min_rating;
  OptTBRealSched units_out;
  OptTBRealSched conductor_rating_ab;
  OptTBRealSched conductor_rating_ba;
  OptTBRealSched equipment_rating_ab;
  OptTBRealSched equipment_rating_ba;
  OptTBRealSched security_limit_ab;
  OptTBRealSched security_limit_ba;
  /// True when any rating / classified limit is set (``Line::
  /// has_rating_limits``): the line reports per-part limit duals.
  bool m_rating_limits_ {false};
  /// True when a conductor rating is set (the default DLR target).
  bool m_has_conductor_ {false};
  /// Per-(stage, block) in-service flag (PLEXOS ``Line.Units``).  When a
  /// block resolves to ``0`` the line is open for that block: no flow
  /// column, capacity row, loss segment, balance contribution, or KVL
  /// row is emitted.  Absent ⇒ in service in every block.
  OptTBIntBoolSched in_service;
  OptTBRealSched tmax_normal_ba;
  OptTBRealSched tmax_normal_ab;
  OptTBRealSched tcost;
  OptTBRealSched overload_penalty;
  OptTBRealSched lossfactor;
  OptTRealSched reactance;
  OptTRealSched voltage;
  OptTRealSched resistance;
  OptTRealSched loss_envelope;
  OptTRealSched tap_ratio;
  OptTRealSched phase_shift_deg;

  STBIndexHolder<ColIndex> flowp_cols;
  STBIndexHolder<ColIndex> flown_cols;
  /// Per-direction LINEAR loss factors, keyed identically to
  /// `flowp_cols` / `flown_cols` (`(scenario, stage) → (buid →
  /// double)`).  Populated ONLY under `LineLossesMode::linear`, where
  /// the line's loss has no explicit column: `add_to_output`
  /// reconstructs the per-cell loss as
  /// `flowp_lossfactor · primal(flowp_cols) +
  ///  flown_lossfactor · primal(flown_cols)`.  Empty (no outer key)
  /// for every other mode — making the linear `add_to_output` branch a
  /// no-op even if reached.
  STBIndexHolder<double> flowp_lossfactor;
  STBIndexHolder<double> flown_lossfactor;
  STBIndexHolder<ColIndex> flows_cols;  ///< signed flow (tangent_signed_flow)
  STBIndexHolder<std::vector<ColIndex>> flowp_seg_cols;
  STBIndexHolder<std::vector<ColIndex>> flown_seg_cols;
  /// Per-segment physical loss factors `lf_k` parallel to
  /// `flowp_seg_cols` / `flown_seg_cols` (same `(scenario, stage) →
  /// (buid → vector)` keying and same per-segment order).  Populated
  /// only under `LineLossesMode::piecewise_direct`, which has no
  /// explicit loss LP column.  `add_to_output` reconstructs the exact
  /// LP-consistent loss `Σ_k lf_k · primal(seg_col_k)` summed over BOTH
  /// directional segment sets, capturing bidirectional loss-arbitrage
  /// flow (which a `R·f²/V²`-at-net-flow approximation would hide).
  STBIndexHolder<std::vector<double>> flowp_seg_loss;
  STBIndexHolder<std::vector<double>> flown_seg_loss;
  STBIndexHolder<ColIndex> lossp_cols;
  STBIndexHolder<ColIndex> lossn_cols;
  STBIndexHolder<ColIndex> overloadp_cols;
  STBIndexHolder<ColIndex> overloadn_cols;
  STBIndexHolder<RowIndex> capacityp_rows;
  STBIndexHolder<RowIndex> capacityn_rows;
  STBIndexHolder<RowIndex> overloadp_rows;
  STBIndexHolder<RowIndex> overloadn_rows;
  /// Thermal-limit rows ``f_dir + a·ℓ_dir ≤ T_dir`` (``sending`` /
  /// ``full`` only); duals written as ``thermalp_dual`` / ``thermaln_dual``.
  STBIndexHolder<RowIndex> thermalp_rows;
  STBIndexHolder<RowIndex> thermaln_rows;

  STBIndexHolder<RowIndex> theta_rows;

  /// Dynamic line rating (``Line.dlr``); nullopt when unset.
  std::optional<LineDlrLP> m_dlr_;
  STBIndexHolder<double> dlr_rating_values;
  STBIndexHolder<double> dlr_rating_emergency_values;
  STBIndexHolder<double> critical_span_values;
  STBIndexHolder<std::vector<RowIndex>> span_rows;
  STBIndexHolder<std::vector<DlrDualRef>> dlr_dual_refs;
  STBIndexHolder<LimitRef> limitp_refs;
  STBIndexHolder<LimitRef> limitn_refs;
  bool m_dlr_never_critical_logged_ {false};
};

// Pin the data-struct constant value so an accidental rename of the
// `Line::class_name` literal fails the build (LP row labels and
// CSV outputs depend on the exact string `"Line"`).
static_assert(LineLP::Element::class_name == LPClassName {"Line"},
              "Line::class_name must remain \"Line\"");

}  // namespace gtopt
