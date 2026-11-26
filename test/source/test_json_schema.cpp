// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <algorithm>
#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json_schema.hpp>

using namespace gtopt;

// Wrap file-local helpers in a uniquely-named outer namespace so the
// unity build cannot collide anonymous-namespace symbols across test
// translation units (see the unity-anon-namespace project feedback).
namespace test_json_schema_detail
{
// Anonymous namespace for internal linkage; the uniquely-named parent keeps
// these helpers from colliding with other test files under unity builds.
namespace
{

/// Parse @p text as a generic JSON value; returns true iff it is
/// well-formed JSON.  Uses daw_json_link's DOM entry point so we exercise
/// the same library that produced the schema string.
[[nodiscard]] bool is_valid_json(std::string_view text)
{
  try {
    auto val = daw::json::json_value(text);
    // Touch a member so the lazy parser actually validates the document.
    (void)val.find_class_member("$schema");
    return true;
  } catch (...) {
    return false;
  }
}

}  // namespace

}  // namespace test_json_schema_detail

TEST_CASE("dump_json_schema default is the full Planning scheme")  // NOLINT
{
  using test_json_schema_detail::is_valid_json;

  const std::string schema = dump_json_schema();

  SUBCASE("valid draft 2020-12 JSON rooted at Planning")
  {
    CHECK(!schema.empty());
    CHECK(schema.front() == '{');
    CHECK(is_valid_json(schema));
    CHECK(schema.find(R"("$schema")") != std::string::npos);
    CHECK(schema.find(R"("$ref": "#/$defs/Planning")") != std::string::npos);
    CHECK(schema.find("gtopt.Planning") != std::string::npos);
  }

  SUBCASE("every System element array is described")
  {
    for (const auto* key :
         {
             R"("bus_array")",
             R"("generator_array")",
             R"("gas_node_array")",
             R"("gas_pipe_array")",
             R"("gas_compressor_array")",
             R"("gas_supply_array")",
             R"("gas_demand_array")",
             R"("gas_turbine_array")",
             R"("lng_terminal_array")",
             R"("hydrogen_node_array")",
             R"("ammonia_storage_array")",
             R"("carrier_converter_array")",
             R"("allowance_pool_array")",
             R"("line_commitment_array")",
             R"("inertia_zone_array")",
             R"("decision_variable_array")",
             R"("user_model_array")",
         })
    {
      CAPTURE(key);
      CHECK(schema.find(key) != std::string::npos);
    }
  }

  SUBCASE("element types get their own $defs with contract fields")
  {
    CHECK(schema.find(R"("GasPipe": {)") != std::string::npos);
    CHECK(schema.find(R"("LngTerminal": {)") != std::string::npos);
    CHECK(schema.find(R"("linepack")") != std::string::npos);
    CHECK(schema.find(R"("exclusive_charge_discharge")") != std::string::npos);
    CHECK(schema.find(R"("initial_generation")") != std::string::npos);
    CHECK(schema.find(R"($ref": "#/$defs/GasPipe")") != std::string::npos);
  }

  SUBCASE("field schedules render as oneOf (number | array | file)")
  {
    // Bare scalar alternatives are the case daw's own generator cannot
    // render; the bespoke emitter deduces them.
    CHECK(schema.find(R"("oneOf": [{"type": "number"}, {"type": "array")")
          != std::string::npos);
  }

  SUBCASE("explicit 'planning' matches the default, case-insensitive")
  {
    CHECK(dump_json_schema("planning") == schema);
    CHECK(dump_json_schema("PLANNING") == schema);
  }
}

TEST_CASE("dump_json_schema selects a sub-type root")  // NOLINT
{
  using test_json_schema_detail::is_valid_json;

  SUBCASE("options -> PlanningOptions only")
  {
    const auto schema = dump_json_schema("options");
    CHECK(is_valid_json(schema));
    CHECK(schema.find(R"("$ref": "#/$defs/PlanningOptions")")
          != std::string::npos);
    CHECK(schema.find(R"("sddp_options")") != std::string::npos);
    CHECK(schema.find(R"("GasOptions": {)") != std::string::npos);
    // Pruned to what PlanningOptions reaches: no System elements.
    CHECK(schema.find(R"("Generator": {)") == std::string::npos);
  }

  SUBCASE("snake_case and C++ spellings of an element type")
  {
    const auto snake = dump_json_schema("gas_pipe");
    CHECK(is_valid_json(snake));
    CHECK(snake.find(R"("$ref": "#/$defs/GasPipe")") != std::string::npos);
    CHECK(dump_json_schema("GasPipe") == snake);
    CHECK(snake.find(R"("System": {)") == std::string::npos);
  }
}

TEST_CASE("dump_json_schema known names render valid JSON")  // NOLINT
{
  using test_json_schema_detail::is_valid_json;

  SUBCASE("planning is the default, options second, element types follow")
  {
    const auto& names = known_schema_names();
    REQUIRE(names.size() > 50);
    CHECK(names[0] == std::string_view {"planning"});
    CHECK(names[1] == std::string_view {"options"});
    CHECK(std::ranges::find(names, std::string_view {"lng_terminal"})
          != names.end());
  }

  SUBCASE("every advertised name renders valid, non-empty JSON")
  {
    for (const auto name : known_schema_names()) {
      CAPTURE(name);
      const std::string schema = dump_json_schema(name);
      CHECK(!schema.empty());
      CHECK(is_valid_json(schema));
    }
  }
}

TEST_CASE("dump_json_schema unknown type returns empty")  // NOLINT
{
  SUBCASE("unknown name yields an empty string")
  {
    // dump_json_schema prints an error to stderr and returns empty.
    CHECK(dump_json_schema("definitely-not-a-type").empty());
  }
}
