/**
 * @file      ammonia_storage_lp.hpp
 * @brief     LP wrapper for the ``AmmoniaStorage`` element
 * @date      Fri May 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Peer of ``HydrogenStorageLP`` on the ``StorageLP<>`` framework.
 * Same LP shape; the only differences are class-name labels and the
 * fact that the carrier-side reference resolves against
 * ``AmmoniaNode`` (NH₃, MWh_LHV) rather than ``HydrogenNode``.
 *
 * @see ammonia_storage.hpp     data struct
 * @see hydrogen_storage_lp.hpp upstream H₂ storage
 */

#pragma once

#include <gtopt/ammonia_storage.hpp>
#include <gtopt/carrier_storage_lp.hpp>

namespace gtopt
{
class AmmoniaNodeLP;
using AmmoniaStorageLPId = ObjectId<class AmmoniaStorageLP>;
using AmmoniaStorageLPSId = ObjectSingleId<class AmmoniaStorageLP>;

/**
 * @class AmmoniaStorageLP
 * @brief LP representation of an ammonia storage element.
 *
 * All numerical accounting is inherited from ``StorageLP<>``; the
 * charge / discharge columns and the ``AmmoniaNode`` coupling from
 * ``CarrierStorageLP`` (carrier_storage_lp.hpp).
 */
class AmmoniaStorageLP
    : public CarrierStorageLP<AmmoniaStorage,
                              AmmoniaNodeLP,
                              &AmmoniaStorage::ammonia_node,
                              storage_state_defaults::ammonia_storage>
{
public:
  using CarrierBase = CarrierStorageLP<AmmoniaStorage,
                                       AmmoniaNodeLP,
                                       &AmmoniaStorage::ammonia_node,
                                       storage_state_defaults::ammonia_storage>;
  using CarrierBase::CarrierBase;

  /// Access the underlying ``AmmoniaStorage`` object.
  [[nodiscard]] constexpr auto&& ammonia_storage(this auto&& self) noexcept
  {
    return self.object();
  }
};

static_assert(AmmoniaStorageLP::Element::class_name
                  == LPClassName {"AmmoniaStorage"},
              "AmmoniaStorage::class_name must remain \"AmmoniaStorage\"");

}  // namespace gtopt
