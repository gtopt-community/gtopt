// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_validate_gas_elements.cpp
 * @brief     validate_planning rejects bad gas-element data up front (B7)
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Enum fields, required capacities and the line-pack reference pressure
 * are checked before any LP is built, all errors together, with the
 * element named in the message.
 */

#include <algorithm>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] Planning vge_gas_planning()
{
  Planning p;
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
  p.system.gas_node_array = {
      {.uid = Uid {1}, .name = "S", .pmin = 30.0, .pmax = 60.0},
      {.uid = Uid {2}, .name = "T", .pmin = 30.0, .pmax = 60.0},
  };
  p.system.gas_pipe_array = {
      {
          .uid = Uid {1},
          .name = "S_T",
          .gas_node_a = Uid {1},
          .gas_node_b = Uid {2},
          .length = 80.0,
          .diameter = 406.0,
          .fmax = 300.0,
      },
  };
  p.system.gas_compressor_array = {
      {
          .uid = Uid {1},
          .name = "C1",
          .gas_node_in = Uid {1},
          .gas_node_out = Uid {2},
          .fmax = 100.0,
          .max_ratio = 1.5,
      },
  };
  return p;
}

[[nodiscard]] bool vge_has_error(const ValidationResult& r,
                                 std::string_view needle)
{
  return std::ranges::any_of(
      r.errors, [needle](const std::string& m) { return m.contains(needle); });
}

}  // namespace

TEST_CASE("validate_planning - gas elements")  // NOLINT
{
  auto p = vge_gas_planning();

  SUBCASE("valid data passes")
  {
    const auto r = validate_planning(p);
    CHECK_FALSE(vge_has_error(r, "GasPipe"));
    CHECK_FALSE(vge_has_error(r, "GasCompressor"));
  }

  SUBCASE("unknown enum values")
  {
    p.system.gas_pipe_array[0].weymouth_mode = Name {"bogus"};
    p.system.gas_compressor_array[0].drive = Name {"steam"};
    const auto r = validate_planning(p);
    CHECK(vge_has_error(r,
                        "GasPipe 'S_T' (uid 1): weymouth_mode: unknown value"));
    CHECK(vge_has_error(r, "GasCompressor 'C1' (uid 1): drive: unknown value"));
  }

  SUBCASE("missing or negative fmax")
  {
    p.system.gas_pipe_array[0].fmax = -1.0;
    p.system.gas_compressor_array[0].fmax = {};
    const auto r = validate_planning(p);
    CHECK(vge_has_error(r, "GasPipe 'S_T' (uid 1): fmax: -1 < 0"));
    CHECK(vge_has_error(r, "GasCompressor 'C1' (uid 1): fmax: is required"));
  }

  SUBCASE("line pack with a non-positive reference pressure")
  {
    p.system.gas_pipe_array[0].linepack = true;
    p.system.gas_pipe_array[0].linepack_pressure_ref = 0.0;
    CHECK(vge_has_error(validate_planning(p),
                        "GasPipe 'S_T' (uid 1): linepack_pressure_ref"));
  }

  SUBCASE("bidirectional compressor needs ratio 1 and no fuel")
  {
    p.system.gas_compressor_array[0].bidirectional = true;
    CHECK(vge_has_error(validate_planning(p),
                        "GasCompressor 'C1' (uid 1): bidirectional"));
  }
}
