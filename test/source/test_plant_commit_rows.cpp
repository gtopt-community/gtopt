/**
 * @file      test_plant_commit_rows.cpp
 * @brief     Plant n_units / uniq_mutex rows over committed variants
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_plant.cpp` covers the Σ-capacity row.  This file covers the
 * status-coupling rows: with a Commitment on each variant, `uniq_mutex`
 * adds `Σ u_i ≤ 1` and `n_units` adds `Σ c_i · u_i ≤ n_units`.  The
 * commitments are relaxed (u ∈ [0, 1]) so the expected optimum can be
 * derived by hand:
 *
 *   G1: pmax 60, gcost 10   G2: pmax 80, gcost 20   demand 100
 *   demand_fail_cost 10000
 *
 *   no plant row          : g1 = 60, g2 = 40                → 1400
 *   u1 + u2 ≤ 1           : u2 = 1, g2 = 80, fail 20        → 201600
 *   u1 + 2·u2 ≤ 1         : u1 = 1, g1 = 60, fail 40        → 400600
 */

#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/plant_lp.hpp>

using namespace gtopt;

namespace
{

constexpr std::string_view fixture_head = R"json(
  {
    "options": {
      "annual_discount_rate": 0.0,
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": {
        "use_single_bus": true,
        "scale_objective": 1,
        "demand_fail_cost": 10000
      }
    },
    "simulation": {
      "block_array": [ { "uid": 1, "duration": 1 } ],
      "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1,
                         "chronological": true } ],
      "scenario_array": [ { "uid": 1, "probability_factor": 1 } ]
    },
    "system": {
      "name": "plant_commit_test",
      "bus_array": [ { "uid": 1, "name": "b1" } ],
      "generator_array": [
        { "uid": 1, "name": "G1", "bus": "b1", "pmin": 0, "pmax": 60,
          "gcost": 10, "capacity": 60 },
        { "uid": 2, "name": "G2", "bus": "b1", "pmin": 0, "pmax": 80,
          "gcost": 20, "capacity": 80 }
      ],
      "commitment_array": [
        { "uid": 1, "name": "G1_uc", "generator": 1, "relax": true },
        { "uid": 2, "name": "G2_uc", "generator": 2, "relax": true }
      ],
      "demand_array": [
        { "uid": 1, "name": "d1", "bus": "b1", "lmax": [[100.0]] }
      ],
      "plant_array": [
)json";

constexpr std::string_view fixture_tail = R"json(
      ]
    }
  }
)json";

struct Solved
{
  double obj {};
  std::size_t cap_rows {};
  std::size_t commit_rows {};
  std::size_t uniq_rows {};
};

Solved solve_with_plant(std::string_view plant_json)
{
  std::string json {fixture_head};
  json += plant_json;
  json += fixture_tail;

  auto planning = daw::json::from_json<Planning>(json, StrictParsePolicy);
  PlanningLP planning_lp(std::move(planning));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());

  const auto& sys_lp = planning_lp.systems().front().front();
  Solved out {
      .obj = sys_lp.linear_interface().get_obj_value_raw(),
  };
  const auto& plants = sys_lp.elements<PlantLP>();
  REQUIRE(plants.size() == 1);
  out.cap_rows = plants.front().capacity_rows().size();
  out.commit_rows = plants.front().commit_rows().size();
  out.uniq_rows = plants.front().uniq_rows().size();
  return out;
}

}  // namespace

TEST_CASE("Plant uniq_mutex couples the variant status columns")  // NOLINT
{
  const auto s = solve_with_plant(R"json(
    { "uid": 1, "name": "P", "generator_names": ["G1", "G2"],
      "uniq_mutex": true }
  )json");
  CHECK(s.uniq_rows == 1);
  CHECK(s.commit_rows == 0);
  CHECK(s.cap_rows == 0);
  CHECK(s.obj == doctest::Approx(201600.0).epsilon(1e-6));
}

TEST_CASE(
    "Plant n_units weights the status columns by commit_coeffs")  // NOLINT
{
  const auto s = solve_with_plant(R"json(
    { "uid": 1, "name": "P", "generator_names": ["G1", "G2"],
      "n_units": 1, "commit_coeffs": [1.0, 2.0] }
  )json");
  CHECK(s.commit_rows == 1);
  CHECK(s.uniq_rows == 0);
  CHECK(s.obj == doctest::Approx(400600.0).epsilon(1e-6));
}

TEST_CASE("Plant n_units defaults missing commit_coeffs to 1")  // NOLINT
{
  // Only one coefficient for two variants: the second defaults to 1,
  // so the row is u1 + u2 ≤ 1, the same optimum as uniq_mutex.
  const auto s = solve_with_plant(R"json(
    { "uid": 1, "name": "P", "generator_names": ["G1", "G2"],
      "n_units": 1, "commit_coeffs": [1.0] }
  )json");
  CHECK(s.commit_rows == 1);
  CHECK(s.obj == doctest::Approx(201600.0).epsilon(1e-6));
}

TEST_CASE("Plant with nothing to enforce adds no rows")  // NOLINT
{
  const auto s = solve_with_plant(R"json(
    { "uid": 1, "name": "P", "generator_names": ["G1", "G2"] }
  )json");
  CHECK(s.cap_rows == 0);
  CHECK(s.commit_rows == 0);
  CHECK(s.uniq_rows == 0);
  CHECK(s.obj == doctest::Approx(1400.0).epsilon(1e-6));
}

TEST_CASE("Plant skips an unknown generator name")  // NOLINT
{
  // The unknown variant is dropped from every row; the Σ-capacity row
  // then caps G1 alone: g1 ≤ 50, g2 = 50 → 50·10 + 50·20 = 1500.
  const auto s = solve_with_plant(R"json(
    { "uid": 1, "name": "P", "generator_names": ["G1", "NO_SUCH_GEN"],
      "pmax": 50 }
  )json");
  CHECK(s.cap_rows == 1);
  CHECK(s.obj == doctest::Approx(1500.0).epsilon(1e-6));
}
