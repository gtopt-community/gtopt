// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_validate_planning_branches.cpp
 * @brief     Behaviour-relevant branches of validate_planning not covered
 *            by test_validate_planning.cpp (scenario-probability
 *            rescaling lives in test_validate_planning_probability.cpp)
 * @date      2026-11-23
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Covers branches that change the model or block a run (not log-only):
 *  * FlowRight non-consumptive / bypass errors;
 *  * VolumeRight negative efin.
 */

#include <algorithm>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] Planning vpb_make_minimal_planning()
{
  Planning p;
  p.system.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  p.simulation.block_array = {
      {
          .uid = Uid {1},
          .duration = 1.0,
      },
  };
  p.simulation.stage_array = {
      {
          .uid = Uid {1},
          .count_block = 1,
      },
  };
  return p;
}

[[nodiscard]] bool vpb_has_message(const std::vector<std::string>& msgs,
                                   std::string_view needle)
{
  return std::ranges::any_of(
      msgs, [needle](const std::string& m) { return m.contains(needle); });
}

}  // namespace

TEST_CASE(  // NOLINT
    "validate_planning - FlowRight consumptive=false without junction_b is "
    "an error")
{
  auto p = vpb_make_minimal_planning();
  p.system.junction_array = {{.uid = Uid {1}, .name = "j1"},
                             {.uid = Uid {2}, .name = "j2"}};
  p.system.flow_right_array = {
      {
          .uid = Uid {1},
          .name = "ret",
          .junction_a = Uid {1},
          .fmax = 10.0,
          .consumptive = false,
      },
  };
  SUBCASE("missing junction_b")
  {
    const auto r = validate_planning(p);
    CHECK_FALSE(r.ok());
    CHECK(vpb_has_message(r.errors, "consumptive=false requires 'junction_b'"));
  }
  SUBCASE("junction_b set: accepted, no 'has no effect' warning")
  {
    p.system.flow_right_array[0].junction_b = Uid {2};
    const auto r = validate_planning(p);
    CHECK(r.ok());
    CHECK_FALSE(vpb_has_message(r.warnings, "has no effect"));
  }
}

TEST_CASE(  // NOLINT
    "validate_planning - FlowRight bypass: negative capacity is an error, "
    "junction_b on a consumptive right warns")
{
  auto p = vpb_make_minimal_planning();
  p.system.junction_array = {{.uid = Uid {1}, .name = "j1"},
                             {.uid = Uid {2}, .name = "j2"}};
  p.system.flow_right_array = {
      {
          .uid = Uid {1},
          .name = "irr",
          .junction_a = Uid {1},
          .fmax = 10.0,
          .junction_b = Uid {2},
      },
  };
  SUBCASE("consumptive + junction_b + no bypass → warning")
  {
    const auto r = validate_planning(p);
    CHECK(r.ok());
    CHECK(vpb_has_message(r.warnings, "has no effect"));
  }
  SUBCASE("negative bypass_fmax → error")
  {
    p.system.flow_right_array[0].use_bypass = true;
    p.system.flow_right_array[0].bypass_fmax = -1.0;
    const auto r = validate_planning(p);
    CHECK_FALSE(r.ok());
    CHECK(vpb_has_message(r.errors, "bypass_fmax must be >= 0"));
  }
  SUBCASE("bypass requested without junction_b → warning")
  {
    p.system.flow_right_array[0].junction_b.reset();
    p.system.flow_right_array[0].use_bypass = true;
    const auto r = validate_planning(p);
    CHECK(vpb_has_message(r.warnings, "no bypass arc is built"));
  }
}

TEST_CASE(
    "validate_planning - negative VolumeRight efin is an error")  // NOLINT
{
  auto p = vpb_make_minimal_planning();
  p.system.volume_right_array = {
      {
          .uid = Uid {1},
          .name = "vr",
          .efin = -1.0,
      },
  };
  const auto r = validate_planning(p);
  CHECK_FALSE(r.ok());
  CHECK(vpb_has_message(r.errors, "efin = -1 must be >= 0"));
}
