/**
 * @file      lng_terminal.hpp
 * @brief     Defines the LngTerminal structure representing an LNG storage
 *            terminal
 * @date      Sun Apr 13 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This file defines the `gtopt::LngTerminal` structure, which models a
 * Liquefied Natural Gas (LNG) storage terminal within a power system.
 * An LNG terminal stores fuel that is regasified and consumed by linked
 * thermal generators, subject to tank volume limits, boil-off losses,
 * delivery schedules, and regasification capacity constraints.
 *
 * ### Unit conventions
 *
 * | Field                    | Unit                | Description |
 * |--------------------------|---------------------|-----------------------------------|
 * | emin, emax, eini, efin   | m³                  | LNG volume in tank | |
 * ecost                    | $/m³                | Holding cost per m³ stored |
 * | annual_standing_loss              | p.u./year           | Boil-off gas
 * fraction per year    | | sendout_max, sendout_min | m³/h                |
 * Regasification rate limits        | | delivery                 | m³/stage |
 * Total LNG delivered per stage     | | spillway_cost            | $/m³ |
 * Penalty for venting LNG           | | spillway_capacity        | m³/h | Max
 * venting rate                  | | scost                    | $/m³ | SDDP
 * state penalty (× mpf → $/MWh) | | mean_production_factor   | MWh/m³ | Energy
 * content of LNG             | | soft_emin, soft_emin_cost| m³, $/m³ | Soft
 * lower bound and penalty      | | flow_conversion_rate     | m³/(m³/h · h) |
 * Default 1.0 (identity)            | | heat_rate (LngGeneratorLink) |
 * m³_LNG/MWh      | Fuel per MWh of electrical output |
 *
 * The default `flow_conversion_rate = 1.0` means flow [m³/h] × hours
 * → m³ (no unit conversion needed).  Set to a different value when using
 * energy-based units (e.g. MMBtu) instead of volumetric (m³).
 *
 * ### JSON Example
 * ```json
 * {
 *   "uid": 1,
 *   "name": "GNL_Quintero",
 *   "emin": 5000,
 *   "emax": 150000,
 *   "eini": 80000,
 *   "sendout_max": 2000,
 *   "delivery": [50000, 0, 50000, 0],
 *   "spillway_cost": 100,
 *   "annual_standing_loss": 0.001,
 *   "use_state_variable": true,
 *   "generators": [
 *     {"generator": 10, "heat_rate": 0.18},
 *     {"generator": 11, "heat_rate": 0.20}
 *   ]
 * }
 * ```
 */

#pragma once

#include <gtopt/field_sched.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>

namespace gtopt
{

/**
 * @brief Link between an LNG terminal and a thermal generator
 *
 * Defines the fuel consumption coupling: the generator consumes LNG at a
 * rate of `heat_rate` m³ per MWh of electrical output.
 */
struct LngGeneratorLink
{
  SingleId generator {unknown_uid};  ///< UID of the linked generator
  /// Fuel consumption rate [m³_LNG/MWh].  With the terminal's
  /// ``use_generator_fuel_curve`` and a generator that has its own fuel
  /// curve, the volume per fuel unit of that curve [m³_LNG/fuel-unit]
  /// (1 when the curve is already in m³/MWh).
  Real heat_rate {1.0};
};

/**
 * @brief LNG storage terminal in a power system
 *
 * The terminal accumulates and releases LNG between time blocks. The
 * volume balance per block is:
 *
 * ```text
 * V[t+1] = V[t] × (1 − annual_standing_loss/8760 × duration)
 *        + delivery_rate × duration
 *        − sendout × duration
 *        − vent × duration
 * ```
 *
 * where sendout feeds linked generators via heat-rate coupling,
 * `sendout = Σ_g (heat_rate_g × P_g)` over each block, bounded by
 * `[sendout_min, sendout_max]` when set (`enforce_sendout`, default on).
 * Optional spot cargoes (`spot_cargo_volume`, `spot_cargo_max`,
 * `spot_cost`, `spot_integer`, `spot_annual_max`) add
 * `spot_cargo_volume × n_spot` to the first block of a stage.
 *
 * @see LngTerminalLP for the LP formulation
 */
struct LngTerminal
{
  /// Canonical class-name constant used in LP row labels and config
  /// fields like `VariableScale::class_name`.  Single source of truth —
  /// `LngTerminalLP` exposes no separate `ClassName` member; callers
  /// reach the constant via `LngTerminal::class_name` directly (or
  /// `LngTerminalLP::Element::class_name` in generic contexts).
  static constexpr LPClassName class_name {"LngTerminal"};

  /// @name Default physical constants
  /// @{
  static constexpr Real default_sendout_max = 10'000.0;  ///< [m³/h]
  static constexpr Real default_spillway_capacity = 1'000.0;  ///< [m³/h]
  static constexpr Real default_flow_conversion_rate = 1.0;  ///< [m³/(m³/h·h)]
  static constexpr Real default_mean_production_factor =
      5.0;  ///< [MWh/m³] — fallback for scost computation
  /// @}

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)

  // ── Storage parameters ─────────────────────────────────────────────────
  OptTBRealFieldSched emin {};  ///< Minimum tank level [m³] — per-(stage,
                                ///< block); accepts a scalar (broadcasts),
                                ///< a 2-D nested array, or a file-backed
                                ///< schedule.
  OptTBRealFieldSched emax {};  ///< Maximum tank level [m³] — same shapes
                                ///< as ``emin``.
  OptTBRealFieldSched ecost {};  ///< Storage holding cost [$/m³] —
                                 ///< per-(stage, block); accepts a scalar
                                 ///< (broadcasts), a 2-D nested array,
                                 ///< or a file-backed schedule.
  OptReal eini {};  ///< Initial tank level [m³]
  OptReal efin {};  ///< End-of-horizon minimum level [m³]
  OptReal efin_cost {};  ///< Penalty cost per unit of `efin` shortfall
                         ///< [$/m³].  When set (and > 0) the hard
                         ///< ``vol_end >= efin`` row becomes soft:
                         ///< ``vol_end + slack >= efin`` with the slack
                         ///< priced at `efin_cost`.  Mirrors the
                         ///< `Reservoir.efin_cost` mechanism (see
                         ///< storage_lp.hpp).

  // ── Losses ─────────────────────────────────────────────────────────────
  OptTRealFieldSched
      annual_standing_loss {};  ///< Boil-off gas rate [p.u./year]

  // ── Send-out (regasification) ──────────────────────────────────────────
  OptReal sendout_max {};  ///< Max regasification rate [m³/h]
  OptReal sendout_min {};  ///< Min regasification rate [m³/h]
  /// Enforce ``sendout_min ≤ Σ_g heat_rate_g · P_g ≤ sendout_max`` per
  /// block (default true).  The limits apply only when set; ``false``
  /// restores the former behaviour (limits parsed but ignored).
  OptBool enforce_sendout {};

  /// Draw the linked generators' tank gas along their own fuel curve
  /// (default false = the link's scalar ``heat_rate`` × P).  When true and
  /// the generator has a curve — ``pmax_segments`` / ``heat_rate_segments``
  /// (convex piecewise incremental), a scalar ``Generator.heat_rate``, or a
  /// committed ``Commitment.noload_fuel`` — the tank balance, the send-out
  /// rows and the PAMPL ``fuel`` draw use
  /// ``heat_rate_link × (h₁·P + Σₖ Δhₖ·sₖ + noload_fuel·u)`` (the same
  /// legs as ``Fuel`` offtake), so part-load burn (no-load fuel per MWh)
  /// and the steeper upper segments cost gas.  The up-reserve backing
  /// (``reserve_sustain_hours``) then values r_up at the top segment's
  /// heat rate (the most a deployed MW can burn on a convex curve).
  /// Without a ``Fuel`` on the generator the segment slacks are priced
  /// only through the tank balance dual.
  OptBool use_generator_fuel_curve {};

  // ── Reserve deliverability (opt-in) ────────────────────────────────────
  /// Gas backing of the linked generators' reserves [h].  Unset
  /// (default) => no coupling, the LP is unchanged.  When set (``>= 0``),
  /// per block, with ``r_up_g`` the sum of generator ``g``'s up-reserve
  /// provisions:
  ///
  /// ```text
  /// reserve_up_energy:   V_b − fcr·T·Σ_g hr_g·r_up_g >= emin_b   (T > 0)
  /// reserve_up_sendout:  Σ_g hr_g·(P_g + r_up_g)    <= sendout_max
  /// ```
  ///
  /// ``V_b`` is the end-of-block tank level (it already nets the block's
  /// deliveries and burn), so up reserve is offered only with gas in
  /// stock to sustain it ``T`` hours.  The send-out row is emitted only
  /// when ``sendout_max`` is set and ``enforce_sendout`` is on.  Down
  /// reserve is gas-limited through the generic provision row
  /// ``r_dn <= P − Pmin``: with no gas ``P = 0`` and ``r_dn = 0``.
  OptReal reserve_sustain_hours {};
  /// Expected up-reserve deployment [MWh per MW of provision per hour],
  /// burnt from the tank: ``+ fcr·a·hr_g·Δ·r_up_g`` in the tank balance.
  /// Only read when ``reserve_sustain_hours`` is set (default 0).
  OptReal reserve_gas_usage {};

  // ── Delivery schedule ──────────────────────────────────────────────────
  OptTRealFieldSched delivery {};  ///< Scheduled LNG arrival [m³/stage]
  /// Purchase (contract) price of the scheduled deliveries [$/m³] per
  /// stage.  Priced on the fixed delivery columns, so for must-take
  /// cargoes it is a constant of the objective (total cost right, no
  /// effect on dispatch).  Unset = costless (former behaviour).  The
  /// linked generators' ``gcost`` must then carry only non-fuel VOM: the
  /// tank gas is priced by the tank balance dual (its shadow value).
  OptTRealFieldSched delivery_cost {};

  // ── Optional spot cargoes ──────────────────────────────────────────────
  /// Volume of one spot cargo [m³] (same unit as ``delivery``).  Spot
  /// cargoes are modelled only when this and ``spot_cargo_max`` are > 0.
  OptReal spot_cargo_volume {};
  /// Spot arrival slots per stage [cargoes/stage] (0 = none).
  OptTRealFieldSched spot_cargo_max {};
  /// Purchase cost of spot LNG [$/m³] per stage (default 0).
  OptTRealFieldSched spot_cost {};
  /// Whole cargoes (integer slots → MIP) when true; continuous
  /// (fractional cargoes, LP) when unset / false.
  OptBool spot_integer {};
  /// Cap on spot cargoes per planning year [cargoes/year]: one row per
  /// 8760-h window of the horizon (a stage belongs to the window of its
  /// midpoint) within each LP (phase).
  OptReal spot_annual_max {};

  // ── Venting / spill ────────────────────────────────────────────────────
  OptReal spillway_cost {};  ///< Venting penalty cost [$/m³]
  OptReal spillway_capacity {};  ///< Max venting rate [m³/h]

  // ── SDDP state coupling ────────────────────────────────────────────────
  OptBool use_state_variable {};  ///< Propagate tank level across stages
  OptReal mean_production_factor {};  ///< [MWh/m³] for scost computation
  OptTBRealFieldSched scost {};  ///< State penalty [$/m³] —
                                 ///< per-(stage, block).

  // ── Soft minimum ───────────────────────────────────────────────────────
  OptTBRealFieldSched soft_emin {};  ///< Soft minimum tank level [m³] —
                                     ///< per-(stage, block); accepts a
                                     ///< scalar (broadcasts), a 2-D
                                     ///< nested array, or a file-backed
                                     ///< schedule.
  OptTBRealFieldSched soft_emin_cost {};  ///< Penalty for soft_emin [$/m³] —
                                          ///< per-(stage, block).

  // ── Flow conversion ────────────────────────────────────────────────────
  OptReal flow_conversion_rate {};  ///< Converts m³/h × h → m³ (default 1.0)

  // ── Generator coupling ─────────────────────────────────────────────────
  Array<LngGeneratorLink> generators {};  ///< Linked generators with heat rates
};

}  // namespace gtopt
