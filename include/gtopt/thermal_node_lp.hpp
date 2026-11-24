/**
 * @file      thermal_node_lp.hpp
 * @brief     LP wrapper for ``ThermalNode`` — carrier-side balance row
 * @date      Sat May 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Peer of ``BusLP`` and ``JunctionLP`` for the thermal carrier
 * (MWh_th).  Creates one balance row per (scenario, stage, block);
 * downstream elements (``ThermalStorageLP``, future
 * ``ThermalGenerator`` / ``ThermalDemand`` / cross-carrier
 * ``Converter``) stamp their flow coefficients into the row.
 *
 * Sign convention (mirrors BusLP):
 *   * +1 × (injection into the node)
 *   * −1 × (withdrawal from the node)
 *
 * For ``ThermalStorage``: charging (finp) pulls heat from the node
 * (coefficient −1) — like a Demand on a Bus; discharging (fout)
 * injects heat (coefficient +1) — like a Generator on a Bus.
 *
 * @see junction_lp.hpp     water-carrier analog (with optional drain)
 * @see thermal_node.hpp    data struct
 */

#pragma once

#include <gtopt/carrier_node_lp.hpp>
#include <gtopt/single_id.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/thermal_node.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

class ThermalNodeLP : public CarrierNodeLP<ThermalNode>
{
public:
  using CarrierNodeLP<ThermalNode>::CarrierNodeLP;

  [[nodiscard]] constexpr auto&& thermal_node(this auto&& self) noexcept
  {
    return self.object();
  }
};

using ThermalNodeLPId = ObjectId<class ThermalNodeLP>;
using ThermalNodeLPSId = ObjectSingleId<class ThermalNodeLP>;

}  // namespace gtopt
