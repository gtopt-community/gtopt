/**
 * @file      lng_terminal_lp.hpp
 * @brief     Defines the LngTerminalLP class for linear programming
 *            representation
 * @date      Sun Apr 13 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the LngTerminalLP class which provides the linear
 * programming representation of LNG storage terminals, including their
 * volume balance constraints and generator fuel consumption coupling.
 */

#pragma once

#include <map>
#include <tuple>

#include <gtopt/lng_terminal.hpp>
#include <gtopt/storage_lp.hpp>

namespace gtopt
{

using LngTerminalLPId = ObjectId<class LngTerminalLP>;
using LngTerminalLPSId = ObjectSingleId<class LngTerminalLP>;

/**
 * @brief Linear programming representation of an LNG storage terminal
 *
 * This class extends StorageLP to provide LP-specific functionality for
 * LNG terminals, including:
 * - Tank volume balance with boil-off losses
 * - Regasification (send-out) flow variables
 * - Delivery inflow schedule
 * - Generator fuel consumption coupling via heat rates
 */
class LngTerminalLP : public StorageLP<ObjectLP<LngTerminal>>
{
public:
  static constexpr std::string_view DeliveryName {"delivery"};
  /// PAMPL alias of the storage `energy` columns (tank level, m³).
  static constexpr std::string_view VolumeName {"volume"};
  /// PAMPL-only weighted reference: generator fuel draw [m³/h].
  static constexpr std::string_view FuelName {"fuel"};
  /// Per-block send-out limit row ``Σ_g heat_rate_g · P_g`` [m³/h].
  static constexpr std::string_view SendoutName {"sendout"};
  /// Gas backing of up reserve ``V − fcr·T·Σ hr·r_up >= emin``.
  static constexpr std::string_view ReserveUpEnergyName {"reserve_up_energy"};
  /// Send-out headroom ``Σ hr·(P + r_up) <= sendout_max``.
  static constexpr std::string_view ReserveUpSendoutName {"reserve_up_sendout"};
  /// Per-stage spot cargo count column [cargoes].
  static constexpr std::string_view SpotName {"spot"};
  /// Per-year spot cargo cap row (keyed by the year's first stage).
  static constexpr std::string_view SpotAnnualName {"spot_annual"};
  /// Hours of one planning year for ``spot_annual_max``.
  static constexpr double hours_per_year = 8760.0;

  using StorageBase = StorageLP<ObjectLP<LngTerminal>>;

  explicit LngTerminalLP(const LngTerminal& pterminal, const InputContext& ic);

  [[nodiscard]] constexpr auto&& terminal(this auto&& self) noexcept
  {
    return self.object();
  }

  [[nodiscard]] constexpr auto flow_conversion_rate() const noexcept
  {
    return terminal().flow_conversion_rate.value_or(
        LngTerminal::default_flow_conversion_rate);
  }

  [[nodiscard]] constexpr auto spillway_cost() const noexcept
  {
    return terminal().spillway_cost;
  }

  /// Send-out limits are enforced unless ``enforce_sendout = false``.
  [[nodiscard]] constexpr auto enforce_sendout() const noexcept
  {
    return terminal().enforce_sendout.value_or(true);
  }

  /// Planning-year index of a stage: the 8760-h window of its midpoint.
  [[nodiscard]] static auto spot_year_of(const StageLP& stage) noexcept -> int;

  [[nodiscard]] constexpr auto spillway_capacity() const noexcept
  {
    return terminal().spillway_capacity.value_or(
        LngTerminal::default_spillway_capacity);
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  [[nodiscard]] bool add_to_output(OutputContext& out) const;

  /// @name Parameter accessors for PAMPL user-constraint resolution
  /// @{
  /// Scheduled LNG delivery per stage [m³/stage]; the LP per-block
  /// inflow rate = ``delivery / stage_duration``.
  [[nodiscard]] auto param_delivery(StageUid s) const { return delivery.at(s); }
  /// Contract price of the scheduled deliveries [$/m³].
  [[nodiscard]] auto param_delivery_cost(StageUid s) const
  {
    return delivery_cost.at(s);
  }
  /// Spot arrival slots per stage [cargoes/stage].
  [[nodiscard]] auto param_spot_cargo_max(StageUid s) const
  {
    return spot_cargo_max.at(s);
  }
  /// Spot purchase cost per stage [$/m³].
  [[nodiscard]] auto param_spot_cost(StageUid s) const
  {
    return spot_cost.at(s);
  }
  /// Tank level bounds / holding cost (StorageLP schedules) are exposed
  /// as `emin` / `emax` / `ecost` through the inherited `param_*`.
  /// @}

  /// @name LP index accessors (tests / diagnostics)
  /// @{
  [[nodiscard]] constexpr const auto& spot_cols() const noexcept
  {
    return m_spot_cols_;
  }
  [[nodiscard]] constexpr const auto& spot_annual_rows() const noexcept
  {
    return m_spot_annual_rows_;
  }
  [[nodiscard]] constexpr const auto& sendout_rows() const noexcept
  {
    return m_sendout_rows_;
  }
  [[nodiscard]] constexpr const auto& reserve_up_energy_rows() const noexcept
  {
    return m_reserve_energy_rows_;
  }
  [[nodiscard]] constexpr const auto& reserve_up_sendout_rows() const noexcept
  {
    return m_reserve_sendout_rows_;
  }
  /// @}

private:
  /// Adds the per-block send-out limit rows (`enforce_sendout`).
  void add_sendout_rows(
      const ScenarioLP& scenario,
      const StageLP& stage,
      LinearProblem& lp,
      const BIndexHolder<std::vector<std::pair<ColIndex, double>>>& draw);

  /// Adds the gas-backing rows of the linked units' up reserve
  /// (`reserve_sustain_hours`).  ``draw``: ``(P col, hr)``; ``reserve``:
  /// ``(r_up col, hr)`` per block.
  void add_reserve_rows(
      const ScenarioLP& scenario,
      const StageLP& stage,
      LinearProblem& lp,
      const BIndexHolder<std::vector<std::pair<ColIndex, double>>>& draw,
      const BIndexHolder<std::vector<std::pair<ColIndex, double>>>& reserve);

  /// Adds the spot cargo column, its tank inflow and the annual cap.
  void add_spot_cargoes(SystemContext& sc,
                        const ScenarioLP& scenario,
                        const StageLP& stage,
                        LinearProblem& lp);

  OptTRealSched delivery;
  OptTRealSched delivery_cost;
  OptTBRealSched scost;
  OptTRealSched spot_cargo_max;
  OptTRealSched spot_cost;
  STBIndexHolder<ColIndex> delivery_cols;
  STIndexHolder<ColIndex> m_spot_cols_;
  STIndexHolder<RowIndex> m_spot_annual_rows_;
  STBIndexHolder<RowIndex> m_sendout_rows_;
  STBIndexHolder<RowIndex> m_reserve_energy_rows_;
  STBIndexHolder<RowIndex> m_reserve_sendout_rows_;

  /// Open annual-cap row per (scenario, phase, year): the stage that
  /// created it and its index.  A rebuild re-creates the row when it
  /// reaches that same first stage again.
  std::map<std::tuple<ScenarioUid, PhaseIndex, int>,
           std::pair<StageUid, RowIndex>>
      m_spot_year_rows_;
};

// Pin the data-struct constant value so an accidental rename of the
// `LngTerminal::class_name` literal fails the build (LP row labels and
// CSV outputs depend on the exact string `"LngTerminal"`).
static_assert(LngTerminalLP::Element::class_name == LPClassName {"LngTerminal"},
              "LngTerminal::class_name must remain \"LngTerminal\"");

}  // namespace gtopt
