/**
 * @file      converter_lp.hpp
 * @brief     LP formulation for energy converters
 * @date      Wed Apr  2 02:10:10 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the ConverterLP class, which builds LP variables and
 * constraints linking a converter to its battery, demand, and generator.
 */

#pragma once

#include <optional>
#include <vector>

#include <gtopt/battery_lp.hpp>
#include <gtopt/capacity_object_lp.hpp>
#include <gtopt/converter.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

class ReserveProvisionLP;

class ConverterLP : public CapacityObjectLP<Converter>
{
public:
  static constexpr std::string_view GenerationName {"generation"};
  static constexpr std::string_view DemandName {"demand"};
  static constexpr std::string_view CapacityName {"capacity"};
  /// Fixed-duration coupling row (``Converter.energy_power_ratio``):
  /// ``battery capainst − ratio × converter capainst = 0`` per stage.
  static constexpr std::string_view DurationName {"duration"};
  /// Per-block commitment-status columns added when
  /// ``Converter.commitment`` is set: ``u_charge`` gates the synthetic
  /// charge ``Demand`` floor/ceiling; ``u_discharge`` gates the
  /// synthetic discharge ``Generator`` floor/ceiling.  Continuous in
  /// ``[0, 1]`` by default; integer when ``integer_commitment`` is set.
  static constexpr std::string_view UChargeName {"u_charge"};
  static constexpr std::string_view UDischargeName {"u_discharge"};
  /// C2-style gating row labels.
  static constexpr std::string_view ChargeUpperName {"charge_upper"};
  static constexpr std::string_view ChargeLowerName {"charge_lower"};
  static constexpr std::string_view DischargeUpperName {"discharge_upper"};
  static constexpr std::string_view DischargeLowerName {"discharge_lower"};
  /// Charge/discharge exclusivity row ``u_charge + u_discharge ≤ 1``
  /// per block (``Converter.exclusive_charge_discharge``, default on
  /// with ``commitment``).
  static constexpr std::string_view ChargeDischargeExclusiveName {
      "charge_discharge_exclusive"};
  /// Battery power-envelope valid inequality (default ON,
  /// ``model_options.battery_power_envelope``):
  /// ``p_charge/Pc_max + p_discharge/Pd_max ≤ 1`` per block, emitted
  /// row-scaled by ``max(Pc_max, Pd_max)`` so it stays in MW units.
  /// Cuts simultaneous full-power charge + discharge.
  static constexpr std::string_view PowerEnvelopeName {"power_envelope"};
  /// Native BESS reserve rows (``Battery.reserve_sustain_hours`` set):
  /// power headroom (``pd − pc + Σr_up ≤ Pd``, ``pc + Σr_dn ≤ Pc``) and
  /// state-of-charge sufficiency (``e − (T/ηd)Σr_up ≥ e_min``,
  /// ``e + T·ηc·Σr_dn ≤ e_max``).  See ``Battery::reserve_sustain_hours``.
  static constexpr std::string_view ReserveUpHeadroomName {
      "reserve_up_headroom"};
  static constexpr std::string_view ReserveDnHeadroomName {
      "reserve_dn_headroom"};
  static constexpr std::string_view ReserveUpEnergyName {"reserve_up_energy"};
  static constexpr std::string_view ReserveDnEnergyName {"reserve_dn_energy"};
  /// Per-side split of a native BESS provision (PLEXOS Max Response /
  /// Max Charge Response): `r − r_dis − r_chg = 0` per provision (columns
  /// and split row labelled `ReserveProvision` with the provision uid, so
  /// several provisions of one battery stay distinct), then
  /// the side headroom rows (up: `pd + Σr_dis ≤ Pd`, `Σr_chg − pc ≤ 0`;
  /// down: `Σr_dis − pd ≤ 0`, `pc + Σr_chg ≤ Pc`).
  static constexpr std::string_view ReserveUpSplitName {"reserve_up_split"};
  static constexpr std::string_view ReserveDnSplitName {"reserve_dn_split"};
  static constexpr std::string_view ReserveUpDischargeName {
      "reserve_up_discharge"};
  static constexpr std::string_view ReserveUpChargeName {"reserve_up_charge"};
  static constexpr std::string_view ReserveDnDischargeName {
      "reserve_dn_discharge"};
  static constexpr std::string_view ReserveDnChargeName {"reserve_dn_charge"};
  /// PAMPL bidirectional flow compound: `flow = +discharge − charge`.
  /// Mirrors `line.flow = +flowp − flown`.  Resolves to the converter's
  /// signed net flow (positive when discharging the battery to the
  /// grid, negative when charging from the grid).  Registered as a
  /// class-level compound in `system_lp.cpp::register_all_ampl_element_names`.
  static constexpr std::string_view FlowName {"flow"};

  using CapacityBase = CapacityObjectLP<Converter>;

  [[nodiscard]] constexpr auto&& converter(this auto&& self) noexcept
  {
    return self.object();
  }

  explicit ConverterLP(const Converter& pconverter, InputContext& ic);

  [[nodiscard]] constexpr auto battery_sid() const noexcept
  {
    return BatteryLPSId {converter().battery};
  }

  [[nodiscard]] constexpr auto generator_sid() const noexcept
  {
    return GeneratorLPSId {converter().generator};
  }

  [[nodiscard]] constexpr auto demand_sid() const noexcept
  {
    return DemandLPSId {converter().demand};
  }

  [[nodiscard]] bool add_to_lp(SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp);

  bool add_to_output(OutputContext& out) const;

private:
  OptTRealSched conversion_rate;

  STBIndexHolder<RowIndex> generation_rows;
  STBIndexHolder<RowIndex> demand_rows;
  STBIndexHolder<RowIndex> capacity_rows;
  STBIndexHolder<RowIndex> power_envelope_rows;
  STBIndexHolder<RowIndex> reserve_up_headroom_rows;
  STBIndexHolder<RowIndex> reserve_dn_headroom_rows;
  STBIndexHolder<RowIndex> reserve_up_energy_rows;
  STBIndexHolder<RowIndex> reserve_dn_energy_rows;
  /// Per-provision split columns (one set per capped direction).
  struct ReserveSplitCols
  {
    Uid uid {unknown_uid};
    Id id;
    STBIndexHolder<ColIndex> up_dis;
    STBIndexHolder<ColIndex> up_chg;
    STBIndexHolder<ColIndex> dn_dis;
    STBIndexHolder<ColIndex> dn_chg;
  };
  std::vector<ReserveSplitCols> reserve_split_cols_;
  ReserveSplitCols& split_cols_of(const ReserveProvisionLP& rp);

  /// Per-side split of the native BESS provisions of one block.
  void add_native_reserve_split(
      const ScenarioLP& scenario,
      const StageLP& stage,
      BlockUid buid,
      LinearProblem& lp,
      bool upward,
      const std::vector<const ReserveProvisionLP*>& provisions,
      std::optional<ColIndex> gcol,
      std::optional<ColIndex> dcol,
      bool dn_credit);

  /// Emit the native BESS reserve rows for one (scenario, stage).
  void add_native_reserve_rows(const SystemContext& sc,
                               const ScenarioLP& scenario,
                               const StageLP& stage,
                               LinearProblem& lp,
                               const GeneratorLP& gen,
                               const DemandLP& dem,
                               const BatteryLP& bat);
};

/// True when @p gen is the discharge generator of a battery whose
/// ``reserve_sustain_hours`` is set, i.e. its reserve provisions are
/// modelled by the native BESS rows of the linking ``ConverterLP``
/// (``ReserveProvisionLP`` then skips its generic generator rows).
[[nodiscard]] bool is_native_bess_reserve_generator(const SystemContext& sc,
                                                    const GeneratorLP& gen);

// Pin the data-struct constant value so an accidental rename of the
// `Converter::class_name` literal fails the build (LP row labels and
// CSV outputs depend on the exact string `"Converter"`).
static_assert(ConverterLP::Element::class_name == LPClassName {"Converter"},
              "Converter::class_name must remain \"Converter\"");

}  // namespace gtopt
