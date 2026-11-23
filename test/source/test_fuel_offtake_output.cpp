/**
 * @file      test_fuel_offtake_output.cpp
 * @brief     Fuel min/max offtake duals and slacks reach the output files
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The offtake tests check the LP; none writes the solution.
 * `FuelLP::add_to_output` routes the stage-level and per-block rows and
 * their soft slacks to `Fuel/{max,min}_offtake_dual` / `..._slack_sol`;
 * a dropped branch would silently lose the take-or-pay scarcity price.
 * Helpers are prefixed `fofo_` for unity builds.
 */

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <format>
#include <random>
#include <set>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace
{

/// Solve a 2-block case with a soft ranged offtake band on `gas` and
/// return the stems of every file written under `Fuel/`.
[[nodiscard]] auto fofo_run(bool per_block) -> std::set<std::string>
{
  const auto tmpdir = std::filesystem::temp_directory_path()
      / std::format("gtopt_fofo_{}_{}",
                    per_block ? "blk" : "stg",
                    static_cast<std::uint64_t>(std::random_device {}()));
  std::filesystem::create_directories(tmpdir);

  const auto json = std::format(
      R"json({{
  "options": {{
    "output_format": "csv",
    "output_compression": "uncompressed",
    "output_directory": "{}",
    "model_options": {{
      "use_single_bus": true,
      "scale_objective": 1,
      "demand_fail_cost": 1000
    }}
  }},
  "simulation": {{
    "block_array": [ {{"uid": 0, "duration": 1}}, {{"uid": 1, "duration": 1}} ],
    "stage_array": [ {{"uid": 0, "first_block": 0, "count_block": 2}} ],
    "scenario_array": [ {{"uid": 0, "probability_factor": 1}} ]
  }},
  "system": {{
    "name": "fofo",
    "bus_array": [ {{"uid": 1, "name": "b1"}} ],
    "demand_array": [ {{"uid": 1, "name": "d1", "bus": "b1", "capacity": 50.0}} ],
    "fuel_array": [
      {{"uid": 1, "name": "gas", "price": 10.0,
        "min_offtake": 50.0, "min_offtake_cost": 5.0,
        "min_offtake_per_block": {},
        "max_offtake": 40.0, "max_offtake_cost": 500.0,
        "max_offtake_per_block": {}}}
    ],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "pmin": 0.0, "pmax": 200.0,
        "gcost": 0.0, "fuel": 1, "heat_rate": 2.0, "capacity": 200.0}}
    ]
  }}
}})json",
      tmpdir.generic_string(),
      per_block ? "true" : "false",
      per_block ? "true" : "false");

  Planning base;
  base.merge(parse_planning_json(json));
  PlanningLP planning_lp(std::move(base));
  const auto res = planning_lp.resolve();
  REQUIRE(res.has_value());
  planning_lp.write_out();

  std::set<std::string> stems;
  const auto dir = tmpdir / "Fuel";
  if (std::filesystem::exists(dir)) {
    for (const auto& e : std::filesystem::directory_iterator(dir)) {
      stems.insert(e.path().stem().string());
    }
  }
  std::error_code ec;
  std::filesystem::remove_all(tmpdir, ec);
  return stems;
}

[[nodiscard]] bool fofo_has(const std::set<std::string>& stems,
                            std::string_view prefix)
{
  return std::ranges::any_of(
      stems, [&](const std::string& s) { return s.starts_with(prefix); });
}

void fofo_check(const std::set<std::string>& stems)
{
  CAPTURE(stems.size());
  CHECK(fofo_has(stems, "max_offtake_dual"));
  CHECK(fofo_has(stems, "max_offtake_slack_sol"));
  CHECK(fofo_has(stems, "min_offtake_dual"));
  CHECK(fofo_has(stems, "min_offtake_slack_sol"));
}

}  // namespace

TEST_CASE("FuelLP write-out - stage-level soft offtake band")  // NOLINT
{
  fofo_check(fofo_run(/*per_block=*/false));
}

TEST_CASE("FuelLP write-out - per-block soft offtake band")  // NOLINT
{
  fofo_check(fofo_run(/*per_block=*/true));
}
