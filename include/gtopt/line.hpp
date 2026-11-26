/**
 * @file      line.hpp
 * @brief     Header for transmission line components
 * @date      Sun Apr 20 02:12:30 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines data structures for representing AC transmission lines
 * in power system planning models. In DC power-flow (Kirchhoff) mode the
 * reactance determines the flow split between parallel paths. The line also
 * supports capacity-expansion planning.
 *
 * ### JSON Example (transmission line)
 * ```json
 * {
 *   "uid": 1,
 *   "name": "l1_4",
 *   "bus_a": "b1",
 *   "bus_b": "b4",
 *   "reactance": 0.0576,
 *   "tmax_ab": 250,
 *   "tmax_ba": 250
 * }
 * ```
 *
 * ### JSON Example (phase-shifting transformer)
 * ```json
 * {
 *   "uid": 5,
 *   "name": "pst_1_2",
 *   "bus_a": 1,
 *   "bus_b": 2,
 *   "reactance": 0.05,
 *   "type": "transformer",
 *   "tap_ratio": 1.02,
 *   "phase_shift_deg": -5.0,
 *   "tmax_ab": 300,
 *   "tmax_ba": 300
 * }
 * ```
 *
 * Fields that accept a `number/array/string` value can hold:
 * - A scalar constant
 * - A 2-D inline array indexed by `[stage][block]`
 * - A filename string referencing a Parquet/CSV schedule in
 *   `input_directory/Line/`
 */

#pragma once

#include <gtopt/capacity.hpp>
#include <gtopt/line_dlr.hpp>
#include <gtopt/line_enums.hpp>
#include <gtopt/lp_class_name.hpp>

namespace gtopt
{

/**
 * @struct Line
 * @brief Represents a transmission line (branch) connecting two buses
 *
 * A line carries active power flow `f ∈ [-tmax_ba, tmax_ab]` between
 * `bus_a` and `bus_b`. In Kirchhoff mode the flow is constrained by
 * `f = V² / X · (θ_a − θ_b)` where V is the line voltage and X the
 * reactance.  When voltage is omitted (defaults to 1.0) both V and X
 * are in per-unit; when voltage is in kV, reactance must be in Ω.
 * Optional expansion variables allow the solver to invest in additional
 * transfer capacity.
 *
 * @see Bus for connected bus definitions
 * @see LineLP for the LP formulation
 */
struct Line
{
  /// Canonical class-name constant used in LP row labels and config
  /// fields like `VariableScale::class_name`.  Single source of truth —
  /// `LineLP` exposes no separate `ClassName` member; callers reach
  /// the constant via `Line::class_name` directly (or
  /// `LineLP::Element::class_name` in generic contexts).
  static constexpr LPClassName class_name {"Line"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Line name
  OptActive active {};  ///< Activation status (default: active)
  OptName type {};  ///< Optional line type tag (e.g. "ac", "dc", "transformer")
  OptName description {};  ///< Optional free-text description (e.g. conversion
                           ///< provenance)

  SingleId bus_a {unknown_uid};  ///< Sending-end (from) bus ID
  SingleId bus_b {unknown_uid};  ///< Receiving-end (to) bus ID

  OptTRealFieldSched voltage {};  ///< Nominal voltage level [kV].
                                  ///< Omit or set to 1.0 for per-unit mode.
  OptTRealFieldSched resistance {};  ///< Series resistance [Ω].
                                     ///< Use p.u. when voltage is omitted.
  /// Series reactance for DC power flow [Ω] (p.u. when voltage is
  /// omitted).  Susceptance: B = V² / X.  Omit it for a DC link (HVDC,
  /// no Kirchhoff coupling).  `0` or a value with `|X/V²|` below
  /// `model_options.dc_line_reactance_threshold` makes the line a
  /// zero-impedance tie that forces the two bus angles equal (θ_a − θ_b
  /// = φ), unless `model_options.tiny_reactance_mode = "dc"` (legacy:
  /// such lines are DC links).
  OptTRealFieldSched reactance {};
  OptTBRealFieldSched lossfactor {};  ///< Lumped loss factor [p.u.]
                                      ///< per-(stage, block).
  OptBool use_line_losses {};  ///< @deprecated Use `line_losses_mode` instead.
                               ///< Kept for backward compatibility:
                               ///< `true` maps to the global default mode,
                               ///< `false` maps to `none`.
  OptName
      line_losses_mode {};  ///< Loss model selection (per-line override).
                            ///< See LineLossesMode for valid values:
                            ///< `"none"`, `"linear"`, `"piecewise"`,
                            ///< `"bidirectional"`, `"adaptive"`, `"dynamic"`.
                            ///< When unset, inherits from ModelOptions.
  OptInt loss_segments {};  ///< Number of piecewise-linear segments for
                            ///< quadratic losses (default: from Options)

  OptName loss_pwl_layout {};  ///< Segment-layout strategy for the PWL
                               ///< loss approximation.  See LinePwlLayout
                               ///< for valid values: `"uniform"` (default,
                               ///< equal-width secant chords), `"equal_error"`
                               ///< (√-spaced minimax), `"tangent"` (outer
                               ///< approximation, future).  Active only when
                               ///< `line_losses_mode` selects a PWL flavour.
                               ///< When unset, defaults to `uniform` —
                               ///< preserving pre-2026-05 behaviour.

  OptName loss_allocation_mode {};  ///< How losses are allocated between
                                    ///< sender and receiver buses:
                                    ///< `"receiver"` (default), `"sender"`,
                                    ///< or `"split"` (50/50, PLP default).

  OptTRealFieldSched loss_envelope {};  ///< Upper envelope [MW] over which the
                                        ///< K loss-PWL segments are spread.
                                        ///< DECOUPLED from the flow cap
                                        ///< (`tmax_ab`/`tmax_ba`): set this to
                                        ///< the ORIGINAL line rating when the
                                        ///< cap is inflated by a soft-cap /
                                        ///< `enforce_level` lift, so the
                                        ///< segments concentrate over the
                                        ///< realistic loading range instead of
                                        ///< the lifted cap.  When unset
                                        ///< (default), the envelope falls back
                                        ///< to `fmax = max(tmax_ab, tmax_ba)`
                                        ///< (backward compatible).  The
                                        ///< envelope never caps the flow:
                                        ///< flow may run past it up to
                                        ///< `tmax_ab`/`tmax_ba`, and losses
                                        ///< there stay QUADRATIC (`R·f²/V²`,
                                        ///< as PLEXOS) — coarser geometric
                                        ///< tangent points / secant segments
                                        ///< (ratio ≤ 1.4, error ≤ ≈ 2.8 %)
                                        ///< cover `[loss_envelope, tmax]`,
                                        ///< while the K fine segments stay
                                        ///< on `[0, loss_envelope]`.

  /// Parse line_losses_mode string to enum (nullopt = inherit from global).
  [[nodiscard]] constexpr std::optional<LineLossesMode> line_losses_mode_enum()
      const noexcept
  {
    if (line_losses_mode.has_value()) {
      return enum_from_name<LineLossesMode>(*line_losses_mode);
    }
    return std::nullopt;
  }

  /// Parse loss_pwl_layout string to enum (defaults to `uniform`).
  [[nodiscard]] constexpr LinePwlLayout loss_pwl_layout_enum() const noexcept
  {
    if (loss_pwl_layout.has_value()) {
      return enum_from_name<LinePwlLayout>(*loss_pwl_layout)
          .value_or(LinePwlLayout::uniform);
    }
    return LinePwlLayout::uniform;
  }

  /// Parse loss_allocation_mode string to enum (receiver if unset).
  [[nodiscard]] constexpr LossAllocationMode loss_allocation_mode_enum()
      const noexcept
  {
    if (loss_allocation_mode.has_value()) {
      return enum_from_name<LossAllocationMode>(*loss_allocation_mode)
          .value_or(LossAllocationMode::receiver);
    }
    return LossAllocationMode::receiver;
  }

  OptTBRealFieldSched tmax_ba {};  ///< Maximum power flow in B→A direction [MW]
  OptTBRealFieldSched tmax_ab {};  ///< Maximum power flow in A→B direction [MW]
  /// Per-(stage, block) in-service flag mirroring PLEXOS ``Line.Units``
  /// (the ``Lin_Units.csv`` DataFile): ``1`` = line in service, ``0`` =
  /// line out (maintenance / forced outage) for that block.  Unlike the
  /// per-stage ``active`` element-activation flag, this resolves at block
  /// granularity so an intra-stage maintenance window can be modelled.
  /// When a block resolves to ``0`` the LP emits **no** flow column,
  /// capacity row, loss segment, balance contribution, or KVL angle row
  /// for that block — the line is a true open circuit and its two buses
  /// are decoupled.  Absent (default) = in service in every block.
  OptTBIntBoolFieldSched in_service {};
  /// Thermal-limit enforcement mode, mirroring PLEXOS's
  /// ``Line.Enforce Limits`` property:
  ///
  ///   * ``0`` — never enforce.  The LP does NOT add the hard
  ///     ``|flow| <= tmax_{ab,ba}`` bound; ``tmax_ab`` / ``tmax_ba``
  ///     are carried purely for **loss-segment discretization** (the
  ///     piecewise-linear loss approximation needs an upper envelope
  ///     to pick segment widths; without it
  ///     ``seg_width = DblMax / nseg`` produces meaningless
  ///     segments).  Use this for lines that PLEXOS labels EL=0
  ///     ("Never enforce") and for selectively-lifted EL=1 lines
  ///     whose flow physically exceeds the rating (e.g.
  ///     Capricornio110→LaNegra110 on the CEN PCP weekly bundle,
  ///     where AC voltage support requires flows well above the
  ///     steady-state rating).
  ///   * ``1`` — voltage-conditional (PLEXOS).  In our LP we treat
  ///     this identically to level ``2`` (hard cap), because we have
  ///     no AC voltage-feasibility iteration to consult.  Lines that
  ///     must exceed their rating (e.g. Capricornio) are lifted to
  ///     ``0`` explicitly.
  ///   * ``2`` — always enforce (the historical hard-cap behaviour
  ///     and the schema default).
  ///
  /// Default = ``2``.
  OptInt enforce_level {};
  OptTBRealFieldSched tcost {};  ///< Variable transmission cost [$/MWh]
                                 ///< — per-(stage, block).  Accepts a
                                 ///< scalar (broadcasts), a 2-D nested
                                 ///< array, or a file-backed schedule.

  /// Small positive cost per MWh of dissipated loss, breaks LP-relax
  /// bidirectional-flow degeneracy.
  ///
  /// In ``piecewise`` / ``bidirectional`` modes the LP has a per-direction
  /// loss column (``loss_p`` / ``loss_n``).  For any required net flow
  /// ``f = fp − fn ≥ 0`` the convex PWL of the loss curve is strictly
  /// minimised at ``fn = 0`` (single-direction dispatch).  Adding a tiny
  /// positive cost ``ε`` on the loss columns makes the LP STRICTLY pick
  /// that single-direction optimum, eliminating LP-degeneracy phantom
  /// bidirectional flow without SOS1, MIP, or binaries.
  ///
  /// Default: 0.1 $/MWh of loss energy, stamped like any other energy
  /// cost (weighted by block duration, scenario probability and
  /// discount) — small next to energy prices yet large enough to break
  /// the degeneracy robustly and to guard against mild negative-LMP
  /// loss arbitrage.  Unset falls back to the global
  /// ``model_options.loss_cost_eps`` (itself defaulting to 0.1 $/MWh);
  /// an explicit ``0`` disables ε on this line.  Per-line override beats
  /// the global default.  Inert for ``none``,
  /// ``linear`` and ``piecewise_direct`` — those have no loss column.
  ///
  /// SIZING for negative-price exposure: ε lands on the LOSS columns
  /// in every mode — in ``tangent_signed_flow`` the abs-flow proxy
  /// ``v`` carries only the ``loss_flow_abs_cost`` degeneracy pin
  /// (activated when ε > 0) — so its incidence is loss-scaled: overhead
  /// ≈ ε·2λ per marginal MWh (λ = R·fmax/V², a few %), NOT a flow
  /// toll.  Loss dumping earns |π_a + π_b|/2 per MWh whenever the
  /// bus-dual pair-sum goes negative (KVL congestion or curtailment
  /// produce this with all-positive costs), hence
  ///   ε ≥ ½·|worst credible pair-sum| (+ margin)
  /// closes the arbitrage on the guarded line — typical per-line
  /// values 1–20 $/MWh cost only centavos of LMP distortion.  Set it
  /// per line on flagged corridors and RE-RUN: the sink migrates to
  /// the next unguarded lossy line, iterate until no new flags.
  /// ``linear`` / ``piecewise_direct`` cannot be ε-guarded (no loss
  /// column).  The exact fix remains the SOS2 λ-form
  /// (``loss_use_sos2`` + ``loss_secant_segments ≥ 2``).  See
  /// ``test_line_losses_negative_lmp_kvl.cpp``.
  OptReal loss_cost_eps {};

  /// Per-MWh degeneracy pin [$/MWh, real cost units] on this line's
  /// ``tangent_signed_flow`` abs-flow proxy columns ``v_l``
  /// (``Σ v_l ≥ |f|``).  Makes ``Σ v_l = |f|`` at the LP optimum so the
  /// secant chord upper bound on the loss stays anchored.  It acts as a
  /// tiny transport toll: the LMP difference across the line shifts by
  /// at most ≈ ``loss_flow_abs_cost`` in the flow direction.  Unset →
  /// ``model_options.loss_flow_abs_cost`` (default 0.001 $/MWh); ``0``
  /// disables the pin; ``1e-6`` reproduces the pre-option internal
  /// constant.  Applied only when the resolved ``loss_cost_eps > 0``.
  /// Inert outside ``tangent_signed_flow``.
  OptReal loss_flow_abs_cost {};

  /// Price [$/MWh] on the loss carried by this line's OVERFLOW tier,
  /// i.e. flow between ``loss_envelope`` and the flow cap ``tmax``.
  /// Every overflow column (|f| ladder column, secant segment, λ
  /// breakpoint or the split overflow loss column of the ``tangent``
  /// layout) is charged ``loss_overflow_cost`` per MWh of loss it adds
  /// above ``R·loss_envelope²/V²``.  This is what stops the tier from
  /// acting as a free energy sink under negative bus prices: burning a
  /// MWh there costs ``loss_overflow_cost``, so only an incentive above
  /// it can buy fictitious loss (the in-envelope residual ``≤ R·env²/V²``
  /// stays governed by ``loss_cost_eps`` / SOS2).  A GENUINE overload
  /// pays the same price on its loss above the envelope — a marginal
  /// LMP spread of ``loss_overflow_cost · 2·R·|f|/V²`` across the line —
  /// which is the minimum any pure-LP guard must charge (see the
  /// formulation doc).  Unset → ``model_options.loss_overflow_cost``,
  /// itself defaulting to the case's effective ``demand_fail_cost`` (1000
  /// $/MWh when unset; 1000 with a warning when it is 0); ``0`` disables
  /// the guard on this line.
  /// Inert when ``loss_envelope`` is unset or ≥ the flow cap.
  OptReal loss_overflow_cost {};

  /// Soft (normal) flow threshold in the B→A direction [MW].  When set
  /// and `overload_penalty > 0`, the LP allows `flown` up to `tmax_ba`
  /// (the hard cap, unchanged) but adds a per-MWh cost
  /// `overload_penalty × max(0, flown − tmax_normal_ba)` to the
  /// objective.  Mirrors UC.jl's `Normal flow limit (MW)` and PLEXOS's
  /// `Normal Rating` paired with an overload penalty.  When unset (or
  /// `>= tmax_ba`) the LP falls back to the pure hard-cap behavior.
  OptTBRealFieldSched tmax_normal_ba {};
  /// Soft (normal) flow threshold in the A→B direction [MW].  Same
  /// semantics as `tmax_normal_ba` for the A→B leg.
  OptTBRealFieldSched tmax_normal_ab {};
  /// Overload penalty above the soft thresholds [$/MWh] —
  /// per-(stage, block).  Accepts a scalar (broadcasts), a 2-D nested
  /// array ``[[block, …], …]``, or a file-backed schedule.  Per-block
  /// duration is factored in by the LP build (matches the convention
  /// of other cost coefficients).  When zero or unset, no slack
  /// columns are emitted and ``tmax_normal_*`` are ignored.
  OptTBRealFieldSched overload_penalty {};

  OptTRealFieldSched capacity {};  ///< Installed transfer capacity [MW]
  OptTRealFieldSched expcap {};  ///< Capacity added per expansion module [MW]
  OptTRealFieldSched
      expmod {};  ///< Maximum number of expansion modules [dimensionless]
  OptTRealFieldSched
      capmax {};  ///< Absolute maximum capacity after expansion [MW]
  OptTRealFieldSched
      annual_capcost {};  ///< Annualized investment cost [$/MW-year]
  OptTRealFieldSched
      annual_derating {};  ///< Annual capacity derating factor [p.u./year]
  OptBool integer_expmod {};  ///< Integer-constrain the expmod variable

  /// Off-nominal tap ratio [p.u.].  For transformers only; ignored for
  /// plain lines.  When set to a value other than 1.0 the effective
  /// susceptance of the branch in the DC power-flow (Kirchhoff) constraint
  /// is scaled by `1/tap_ratio`, i.e. `B_eff = V²/(tap_ratio · X)`.
  /// Defaults to 1.0 (nominal tap, no correction).
  OptTRealFieldSched tap_ratio {};

  /// Phase-shift angle [degrees].  Models a phase-shifting transformer
  /// (PST) by adding a constant angle offset φ to the Kirchhoff equality
  /// constraint: `f = B_eff · (θ_a − θ_b − φ)`.  Positive values reduce
  /// the natural power flow from bus_a to bus_b.  Defaults to 0.0.
  OptTRealFieldSched phase_shift_deg {};

  /// Number of L-secant segments for the ``tangent_signed_flow`` loss-
  /// upper-bound chord (issue #504).  When ``L > 1`` the single
  /// ``v ≥ |f|`` auxiliary column is replaced by ``L`` segment columns
  /// ``v_l ∈ [0, envelope/L]`` and the chord upper bound becomes the
  /// piecewise sum ``ℓ ≤ Σ chord_slope_l · v_l`` with per-segment slope
  /// ``(R/V²) · (envelope/L) · (2l − 1)``.  Combined with
  /// ``loss_use_sos2`` this yields a tight piecewise-linear upper bound
  /// on the convex quadratic loss curve, lowering worst-case
  /// overstatement from ``c·envelope²/4`` (L=1 secant) to
  /// ``c·envelope²/(4·L²)``.
  ///
  /// Per-line override beats ``model_options.loss_secant_segments``.
  /// Unset → ``1`` (single-secant chord, current production behaviour).
  /// Inert outside ``tangent_signed_flow`` mode.
  OptInt loss_secant_segments {};

  /// Enable SOS2 enforcement on the ``L`` secant-segment columns
  /// emitted when ``loss_secant_segments > 1`` (issue #504).  Without
  /// SOS2 the LP exploits the segment freedom to maximise the chord
  /// ceiling; with SOS2 at most two consecutive ``v_l`` are non-zero,
  /// forcing fill order ``v_1`` → ``v_2`` → … → ``v_L`` and making
  /// the chord piecewise-tight at every breakpoint ``b_l = l·w``.
  ///
  /// REQUIRES a MIP-capable LP backend (CPLEX, Gurobi, HiGHS ≥ 1.6);
  /// gtopt's CBC/CLP plugins do not currently expose SOS2 — the LP
  /// build raises a structured error if SOS2 is requested with an
  /// unsupporting backend so the misconfig surfaces at build time.
  ///
  /// Per-line override beats ``model_options.loss_use_sos2``.
  /// Unset → false (no SOS2 declaration; behaviour identical to the
  /// pre-#504 single-secant chord).  Inert outside
  /// ``tangent_signed_flow`` mode or when ``loss_secant_segments ≤ 1``.
  OptBool loss_use_sos2 {};

  /// How the thermal limit (`tmax_ab` / `tmax_ba`, and the installed
  /// capacity of an expandable line) treats the loss: `"flow"` (limit on
  /// the flow variable only), `"sending"` (limit on the sending-end
  /// power `f + a_s·ℓ`, `a_s` = sending-bus share of the loss under
  /// `loss_allocation_mode`) or `"full"` (limit on `f + ℓ`).  See
  /// `LineThermalLimitMode` (line_enums.hpp).  Unset → inherits
  /// `model_options.line_thermal_limit_mode` (default `"flow"`).
  OptName line_thermal_limit_mode {};

  /// Dynamic line rating through critical spans (see `line_dlr.hpp`).
  /// When set, the per-block thermal limit is the DLR rating computed
  /// from each span's weather with IEEE Std 738 (or a precomputed
  /// `rating`).  `dlr.limit_mode = "replace"` drops the conductor rating
  /// (or, when set explicitly without one, `max_rating`); `"min"` keeps
  /// `min(static, DLR)`.  Every other set limit applies on top.
  OptLineDlr dlr {};

  /// Upper bound of the overload slack: `"thermal"` (`min(hard − normal,
  /// max_overload_ratio × normal)`, the thermal headroom capped by the
  /// ratio; ratio only without a finite hard cap), `"ratio"`
  /// (`max_overload_ratio × normal`) or `"unbounded"`.
  /// Unset → `model_options.overload_slack_bound` (default `"thermal"`).
  OptName overload_slack_bound {};
  /// Overload headroom as a fraction of the normal threshold, used by
  /// `overload_slack_bound = "ratio"` (and the `"thermal"` fallback).
  /// Unset → `model_options.max_overload_ratio` (default 1.5).
  OptReal max_overload_ratio {};

  // ── PLEXOS line limits (System.Lines) ─────────────────────────────
  // Every limit field is OPTIONAL: unset (or a NaN / missing schedule
  // cell) means "no limit from that field" in that direction and block —
  // never 0.  An explicit 0 is a real 0 limit.  The `_ab` / `max_*`
  // fields bound A→B, the `_ba` / `min_*` fields bound B→A; an unset B→A
  // side does NOT mirror A→B (same as `tmax_ba` today).  PLEXOS `Min *`
  // values are SIGNED (≤ 0, the B→A magnitude is `-min_*`).

  /// PLEXOS `Max Flow` [MW]: alias of `tmax_ab` (give one, not both).
  OptTBRealFieldSched max_flow {};
  /// PLEXOS `Min Flow` [MW, ≤ 0]: alias of `-tmax_ba`.
  OptTBRealFieldSched min_flow {};
  /// PLEXOS `Max Rating` / `Min Rating` [MW]: normal rating (CEN's daily
  /// operational limit `Lin_MaxRating.csv`).  Limit = min(flow, rating).
  OptTBRealFieldSched max_rating {};
  OptTBRealFieldSched min_rating {};  ///< ≤ 0 (B→A normal rating)
  /// PLEXOS `Overload Max Rating` / `Overload Min Rating` [MW]: emergency
  /// rating.  With a positive `overload_penalty` (`limit_penalty`) in the
  /// block it is the hard cap and the normal rating the soft threshold of
  /// the overload band; otherwise unused.
  OptTBRealFieldSched overload_max_rating {};
  OptTBRealFieldSched overload_min_rating {};  ///< ≤ 0
  /// PLEXOS `Outage Max Rating` / `Outage Min Rating` [MW]: the rating in
  /// blocks with `units_out > 0` (maintenance derate).  Never replaced by
  /// the DLR.
  OptTBRealFieldSched outage_max_rating {};
  OptTBRealFieldSched outage_min_rating {};  ///< ≤ 0
  /// PLEXOS `Units Out`: units (circuits) out of service in the block.
  /// `> 0` activates the `outage_*_rating`.  Unset → 0.
  OptTBRealFieldSched units_out {};
  /// PLEXOS `Limit Penalty` [$/MWh]: alias of `overload_penalty` (give
  /// one, not both).
  OptTBRealFieldSched limit_penalty {};

  // ── Limit classification (gtopt-specific, optional) ───────────────
  /// Conductor thermal rating [MW] (weather-dependent; CEN ERST cause
  /// "Conductor").  The DLR rating replaces it under
  /// `dlr.limit_mode = replace` (the default when it is set).
  OptTBRealFieldSched conductor_rating_ab {};
  OptTBRealFieldSched conductor_rating_ba {};
  /// Fixed series-equipment limit [MW]: current transformers (TTCC),
  /// series capacitors (CCSS), wave traps, breakers.  Never replaced.
  OptTBRealFieldSched equipment_rating_ab {};
  OptTBRealFieldSched equipment_rating_ba {};
  /// Security / stability limit [MW]: voltage stability (ERST
  /// "Estabilidad de Tensión"), transient stability, N-1.  Never replaced.
  OptTBRealFieldSched security_limit_ab {};
  OptTBRealFieldSched security_limit_ba {};

  /// True when any rating / classified limit beyond the plain flow limit
  /// is set (these lines report per-component limit duals).
  [[nodiscard]] constexpr bool has_rating_limits() const noexcept
  {
    return max_rating.has_value() || min_rating.has_value()
        || overload_max_rating.has_value() || overload_min_rating.has_value()
        || outage_max_rating.has_value() || outage_min_rating.has_value()
        || conductor_rating_ab.has_value() || conductor_rating_ba.has_value()
        || equipment_rating_ab.has_value() || equipment_rating_ba.has_value()
        || security_limit_ab.has_value() || security_limit_ba.has_value();
  }

  /// Parse overload_slack_bound (nullopt = inherit from global).
  [[nodiscard]] constexpr std::optional<LineOverloadSlackBound>
  overload_slack_bound_enum() const noexcept
  {
    if (overload_slack_bound.has_value()) {
      return enum_from_name<LineOverloadSlackBound>(*overload_slack_bound);
    }
    return std::nullopt;
  }

  /// Parse line_thermal_limit_mode (nullopt = inherit from global).
  [[nodiscard]] constexpr std::optional<LineThermalLimitMode>
  line_thermal_limit_mode_enum() const noexcept
  {
    if (line_thermal_limit_mode.has_value()) {
      return enum_from_name<LineThermalLimitMode>(*line_thermal_limit_mode);
    }
    return std::nullopt;
  }
};

}  // namespace gtopt
