/**
 * @file      test_emission_source_output.cpp
 * @brief     EmissionSource solution streams written by add_to_output
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The emission tests check the priced objective; none writes the
 * per-source output.  `EmissionSourceLP::add_to_output` rebuilds the
 * physical tonnage from the generation primal and the per-MWh factors,
 * so a wrong leg (combustion vs upstream) only shows up in these files.
 *
 * Fixture: one 50 MW demand served by g1, one source on g1 with
 * rate 0.5 t/MWh and upstream_rate 0.1 t/MWh:
 *   combustion = 25 t, upstream = 5 t, emissions = 30 t.
 */

#include <algorithm>
#include <filesystem>
#include <format>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace
{

constexpr std::string_view emission_output_json = R"json(
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
      "stage_array": [ { "uid": 1, "first_block": 0, "count_block": 1 } ],
      "scenario_array": [ { "uid": 1, "probability_factor": 1 } ]
    },
    "system": {
      "name": "emission_source_output",
      "bus_array": [ { "uid": 1, "name": "b1" } ],
      "generator_array": [
        { "uid": 1, "name": "g1", "bus": "b1", "gcost": 10,
          "capacity": 100 }
      ],
      "demand_array": [
        { "uid": 1, "name": "d1", "bus": "b1", "lmax": [[50.0]] }
      ],
      "emission_array": [ { "uid": 1, "name": "co2" } ],
      "emission_zone_array": [
        { "uid": 1, "name": "co2_tax",
          "emissions": [ { "emission": "co2", "weight": 1.0 } ],
          "price": 20 }
      ],
      "emission_source_array": [
        { "uid": 7, "name": "g1_co2", "generator": "g1", "zone": "co2_tax",
          "emission": "co2", "rate": 0.5, "upstream_rate": 0.1 }
      ]
    }
  }
)json";

std::vector<std::string> split_csv(const std::string& line)
{
  std::vector<std::string> out;
  std::stringstream ss {line};
  std::string field;
  while (std::getline(ss, field, ',')) {
    if (field.size() >= 2 && field.front() == '"' && field.back() == '"') {
      field = field.substr(1, field.size() - 2);
    }
    out.push_back(field);
  }
  return out;
}

/// Sum of the values of element @p uid in a long- or wide-format CSV.
/// Returns 0.0 with @p found = false when the file or column is missing.
double read_uid_value(const std::filesystem::path& path, int uid, bool& found)
{
  found = false;
  std::ifstream in {path};
  std::string line;
  if (!std::getline(in, line)) {
    return 0.0;
  }
  const auto header = split_csv(line);
  int uid_idx = -1;
  int value_idx = -1;
  int wide_idx = -1;
  const auto wide_name = std::format("uid:{}", uid);
  for (int i = 0; std::cmp_less(i, header.size()); ++i) {
    const auto& h = header[static_cast<std::size_t>(i)];
    if (h == "uid") {
      uid_idx = i;
    } else if (h == "value") {
      value_idx = i;
    } else if (h == wide_name) {
      wide_idx = i;
    }
  }
  double total = 0.0;
  while (std::getline(in, line)) {
    const auto row = split_csv(line);
    if (wide_idx >= 0 && std::cmp_less(wide_idx, row.size())) {
      total += std::stod(row[static_cast<std::size_t>(wide_idx)]);
      found = true;
    } else if (uid_idx >= 0 && value_idx >= 0
               && std::cmp_less(std::max(uid_idx, value_idx), row.size())
               && std::stoi(row[static_cast<std::size_t>(uid_idx)]) == uid)
    {
      total += std::stod(row[static_cast<std::size_t>(value_idx)]);
      found = true;
    }
  }
  return total;
}

}  // namespace

TEST_CASE("EmissionSource writes its emission streams")  // NOLINT
{
  const auto tmpdir =
      std::filesystem::temp_directory_path() / "gtopt_emission_source_output";
  std::filesystem::remove_all(tmpdir);
  std::filesystem::create_directories(tmpdir);

  auto planning =
      daw::json::from_json<Planning>(emission_output_json, StrictParsePolicy);
  planning.options.output_directory = tmpdir.string();
  PlanningLP planning_lp(std::move(planning));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());
  planning_lp.write_out();

  const auto dir = tmpdir / "EmissionSource";
  const std::map<std::string, double> expected {
      {"emissions", 30.0},
      {"combustion", 25.0},
      {"upstream", 5.0},
      {"rate", 0.5},
      {"upstream_rate", 0.1},
  };
  for (const auto& [field, value] : expected) {
    CAPTURE(field);
    bool found = false;
    double got = 0.0;
    // Shard names are `<field>_sol[_s<N>_p<M>].csv`.
    const auto stem = std::format("{}_sol", field);
    if (std::filesystem::exists(dir)) {
      for (const auto& e : std::filesystem::directory_iterator(dir)) {
        const auto name = e.path().filename().string();
        if (name == stem + ".csv" || name.starts_with(stem + "_s")) {
          bool f = false;
          got += read_uid_value(e.path(), 7, f);
          found = found || f;
        }
      }
    }
    CHECK(found);
    CHECK(got == doctest::Approx(value).epsilon(1e-6));
  }

  // No capture factor configured -> no captured stream.
  if (std::filesystem::exists(dir)) {
    for (const auto& e : std::filesystem::directory_iterator(dir)) {
      CHECK_FALSE(e.path().filename().string().starts_with("captured_sol"));
    }
  }

  std::filesystem::remove_all(tmpdir);
}
