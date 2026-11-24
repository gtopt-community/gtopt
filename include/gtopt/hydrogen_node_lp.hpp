/**
 * @file      hydrogen_node_lp.hpp
 * @brief     LP wrapper for ``HydrogenNode`` — carrier-side balance row
 * @date      Sat May 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Mirror of ``thermal_node_lp.hpp`` for the hydrogen carrier (MWh_LHV).
 */

#pragma once

#include <gtopt/carrier_node_lp.hpp>
#include <gtopt/hydrogen_node.hpp>
#include <gtopt/single_id.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

class HydrogenNodeLP : public CarrierNodeLP<HydrogenNode>
{
public:
  using CarrierNodeLP<HydrogenNode>::CarrierNodeLP;

  [[nodiscard]] constexpr auto&& hydrogen_node(this auto&& self) noexcept
  {
    return self.object();
  }
};

using HydrogenNodeLPId = ObjectId<class HydrogenNodeLP>;
using HydrogenNodeLPSId = ObjectSingleId<class HydrogenNodeLP>;

}  // namespace gtopt
