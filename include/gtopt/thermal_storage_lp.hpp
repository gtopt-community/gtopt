/**
 * @file      thermal_storage_lp.hpp
 * @brief     LP wrapper for the ``ThermalStorage`` element
 * @date      Fri May 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Peer of ``BatteryLP`` and ``ReservoirLP`` on the ``StorageLP<>``
 * framework.  Identical LP shape to ``BatteryLP``: one ``finp``
 * column per (scenario, stage, block) for charging, one ``fout``
 * column for discharging, one ``energy`` column for SoC, and the
 * standard energy-balance row.  All differences are semantic:
 *   * Energy is MWh_th, not MWh_e.
 *   * ``input_efficiency`` / ``output_efficiency`` are the HTF↔salt
 *     heat-exchanger losses on either side of the TES.
 *   * ``emin`` is the molten-salt freezing heel.
 *   * The carrier-side reference (``ThermalStorage.thermal_node``)
 *     resolves against ``ThermalNode``, not ``Bus`` — but
 *     ``ThermalStorageLP`` does NOT wire itself into a thermal-node
 *     balance row here.  That coupling lives on the ``ThermalNodeLP``
 *     (when written) or on a future converter / generator that
 *     references the thermal node directly.
 *
 * @see thermal_storage.hpp   data struct
 * @see battery_lp.hpp        peer electric storage
 */

#pragma once

#include <gtopt/carrier_storage_lp.hpp>
#include <gtopt/thermal_storage.hpp>

namespace gtopt
{
class ThermalNodeLP;
using ThermalStorageLPId = ObjectId<class ThermalStorageLP>;
using ThermalStorageLPSId = ObjectSingleId<class ThermalStorageLP>;

/**
 * @class ThermalStorageLP
 * @brief LP representation of a thermal storage element.
 *
 * All numerical accounting (SoC, charge / discharge bounds, capacity
 * row, cross-phase coupling, soft-emin slack, ``efin`` penalty) is
 * inherited from ``StorageLP<>``; the charge / discharge columns and the
 * ``ThermalNode`` coupling from ``CarrierStorageLP`` (carrier_storage_lp.hpp).
 */
class ThermalStorageLP
    : public CarrierStorageLP<ThermalStorage,
                              ThermalNodeLP,
                              &ThermalStorage::thermal_node,
                              storage_state_defaults::thermal_storage>
{
public:
  using CarrierBase = CarrierStorageLP<ThermalStorage,
                                       ThermalNodeLP,
                                       &ThermalStorage::thermal_node,
                                       storage_state_defaults::thermal_storage>;
  using CarrierBase::CarrierBase;

  /// Access the underlying ``ThermalStorage`` object.
  [[nodiscard]] constexpr auto&& thermal_storage(this auto&& self) noexcept
  {
    return self.object();
  }
};

// Pin the data-struct constant value so an accidental rename of the
// `ThermalStorage::class_name` literal fails the build (LP row labels
// and CSV outputs depend on the exact string `"ThermalStorage"`).
static_assert(ThermalStorageLP::Element::class_name
                  == LPClassName {"ThermalStorage"},
              "ThermalStorage::class_name must remain \"ThermalStorage\"");

}  // namespace gtopt
