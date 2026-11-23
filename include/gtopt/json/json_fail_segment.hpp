/**
 * @file      json_fail_segment.hpp
 * @brief     JSON contract for FailSegment (stepwise VoLL curve segment)
 * @date      Sun Nov 22 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <daw/json/daw_json_link.h>
#include <gtopt/fail_segment.hpp>
#include <gtopt/json/json_field_sched.hpp>

namespace daw::json
{
using gtopt::FailSegment;

template<>
struct json_data_contract<FailSegment>
{
  using type = json_member_list<
      json_number_null<"fraction", OptReal>,
      json_variant_null<"cost", OptTRealFieldSched, jvtl_TRealFieldSched>>;

  constexpr static auto to_json_data(FailSegment const& seg)
  {
    return std::forward_as_tuple(seg.fraction, seg.cost);
  }
};
using gtopt::ScalarFailSegment;

template<>
struct json_data_contract<ScalarFailSegment>
{
  using type = json_member_list<json_number_null<"fraction", OptReal>,
                                json_number_null<"cost", OptReal>>;

  constexpr static auto to_json_data(ScalarFailSegment const& seg)
  {
    return std::forward_as_tuple(seg.fraction, seg.cost);
  }
};
}  // namespace daw::json
