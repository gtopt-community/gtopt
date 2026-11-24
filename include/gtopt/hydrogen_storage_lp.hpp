/**
 * @file      hydrogen_storage_lp.hpp
 * @brief     LP wrapper for the ``HydrogenStorage`` element
 * @date      Fri May 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Peer of ``BatteryLP`` / ``ThermalStorageLP`` / ``ReservoirLP`` on
 * the ``StorageLP<>`` framework.  Identical LP shape: one ``finp``
 * column per (scenario, stage, block) for charging, one ``fout``
 * column for discharging, one ``energy`` column for SoC, and the
 * standard energy-balance row.  All differences are semantic:
 *   * Energy is MWh_LHV, not MWh_e.
 *   * The carrier-side reference (``HydrogenStorage.hydrogen_node``)
 *     resolves against ``HydrogenNode``, not ``Bus``.
 *
 * @see hydrogen_storage.hpp   data struct
 * @see battery_lp.hpp         peer electric storage
 * @see ammonia_storage_lp.hpp downstream NH₃ storage
 */

#pragma once

#include <gtopt/carrier_storage_lp.hpp>
#include <gtopt/hydrogen_storage.hpp>

namespace gtopt
{
class HydrogenNodeLP;
using HydrogenStorageLPId = ObjectId<class HydrogenStorageLP>;
using HydrogenStorageLPSId = ObjectSingleId<class HydrogenStorageLP>;

/**
 * @class HydrogenStorageLP
 * @brief LP representation of a hydrogen storage element.
 *
 * All numerical accounting is inherited from ``StorageLP<>``; the
 * charge / discharge columns and the ``HydrogenNode`` coupling from
 * ``CarrierStorageLP`` (carrier_storage_lp.hpp).
 */
class HydrogenStorageLP
    : public CarrierStorageLP<HydrogenStorage,
                              HydrogenNodeLP,
                              &HydrogenStorage::hydrogen_node,
                              storage_state_defaults::hydrogen_storage>
{
public:
  using CarrierBase =
      CarrierStorageLP<HydrogenStorage,
                       HydrogenNodeLP,
                       &HydrogenStorage::hydrogen_node,
                       storage_state_defaults::hydrogen_storage>;
  using CarrierBase::CarrierBase;

  /// Access the underlying ``HydrogenStorage`` object.
  [[nodiscard]] constexpr auto&& hydrogen_storage(this auto&& self) noexcept
  {
    return self.object();
  }
};

static_assert(HydrogenStorageLP::Element::class_name
                  == LPClassName {"HydrogenStorage"},
              "HydrogenStorage::class_name must remain \"HydrogenStorage\"");

}  // namespace gtopt
