/**
 * @file      ammonia_node_lp.hpp
 * @brief     LP wrapper for ``AmmoniaNode`` — carrier-side balance row
 * @date      Sat May 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Mirror of ``thermal_node_lp.hpp`` for the ammonia carrier (MWh_LHV).
 */

#pragma once

#include <gtopt/ammonia_node.hpp>
#include <gtopt/carrier_node_lp.hpp>
#include <gtopt/single_id.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

class AmmoniaNodeLP : public CarrierNodeLP<AmmoniaNode>
{
public:
  using CarrierNodeLP<AmmoniaNode>::CarrierNodeLP;

  [[nodiscard]] constexpr auto&& ammonia_node(this auto&& self) noexcept
  {
    return self.object();
  }
};

using AmmoniaNodeLPId = ObjectId<class AmmoniaNodeLP>;
using AmmoniaNodeLPSId = ObjectSingleId<class AmmoniaNodeLP>;

}  // namespace gtopt
