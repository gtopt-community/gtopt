// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// `annual_loss` → `annual_standing_loss` rename: the old key (and
// `model_options.battery_annual_loss`) stays accepted as a deprecated
// alias through the naming-dialect canonicaliser
// (share/gtopt/naming_dialects.json, `"deprecated": true`).  Setting
// both spellings on one object is an error.
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_ammonia_storage.hpp>
#include <gtopt/json/json_hydrogen_storage.hpp>
#include <gtopt/json/json_lng_terminal.hpp>
#include <gtopt/json/json_model_options.hpp>
#include <gtopt/json/json_thermal_storage.hpp>
#include <gtopt/json_canonicalize.hpp>
#include <gtopt/names_registry.hpp>
#include <gtopt/planning.hpp>

using namespace gtopt;

namespace
{

constexpr std::string_view kDict = R"json(
{
  "version": 1,
  "aliases": [
    {"class": "battery", "canonical": "annual_standing_loss", "alt": "annual_loss", "dialect": "gtopt-legacy", "deprecated": true},
    {"class": "generator", "canonical": "pmax", "alt": "max_power", "dialect": "gtopt"}
  ]
}
)json";

[[nodiscard]] double scalar_of(const OptTRealFieldSched& field)
{
  return std::get<Real>(field.value_or(Real {-1.0}));
}

}  // namespace

TEST_CASE("NamesRegistry — deprecated flag")  // NOLINT
{
  const NamesRegistry r {kDict};
  CHECK(r.is_deprecated("annual_loss"));
  CHECK_FALSE(r.is_deprecated("max_power"));
  CHECK_FALSE(r.is_deprecated("annual_standing_loss"));
  CHECK_FALSE(r.is_deprecated("unknown"));
}

TEST_CASE("NamesRegistry — shipped dictionary flags annual_loss")  // NOLINT
{
  const auto& r = NamesRegistry::instance();
  CHECK(r.is_deprecated("annual_loss"));
  CHECK(r.is_deprecated("battery_annual_loss"));
  const auto c = r.canonical_for("annual_loss");
  CHECK((c.has_value() && *c == "annual_standing_loss"));
  const auto m = r.canonical_for("battery_annual_loss");
  CHECK((m.has_value() && *m == "battery_annual_standing_loss"));
}

TEST_CASE("canonicalize_json_keys — deprecated alias")  // NOLINT
{
  const NamesRegistry r {kDict};

  SUBCASE("deprecated alias is rewritten")
  {
    const auto out = canonicalize_json_keys(R"({"annual_loss": 0.1})", r);
    CHECK(out == R"({"annual_standing_loss": 0.1})");
  }

  SUBCASE("deprecated alias inside nested arrays is rewritten")
  {
    const auto out = canonicalize_json_keys(
        R"({"a": [{"annual_loss": 0.1, "x": {"y": 1}}, {"annual_loss": 2}]})",
        r);
    CHECK(out
          == R"({"a": [{"annual_standing_loss": 0.1, "x": {"y": 1}}, )"
             R"({"annual_standing_loss": 2}]})");
  }

  SUBCASE("alias and canonical on the same object throw")
  {
    CHECK_THROWS_AS(
        (void)canonicalize_json_keys(
            R"({"annual_standing_loss": 0.2, "annual_loss": 0.1})", r),
        std::invalid_argument);
    CHECK_THROWS_AS(
        (void)canonicalize_json_keys(
            R"({"annual_loss": 0.1, "annual_standing_loss": 0.2})", r),
        std::invalid_argument);
  }

  SUBCASE("alias and canonical on sibling objects are fine")
  {
    const auto out = canonicalize_json_keys(
        R"([{"annual_loss": 0.1}, {"annual_standing_loss": 0.2}])", r);
    CHECK(out
          == R"([{"annual_standing_loss": 0.1}, )"
             R"({"annual_standing_loss": 0.2}])");
  }

  SUBCASE("canonical key in a nested child does not conflict")
  {
    const auto out = canonicalize_json_keys(
        R"({"annual_loss": 0.1, "c": {"annual_standing_loss": 0.2}})", r);
    CHECK(out
          == R"({"annual_standing_loss": 0.1, )"
             R"("c": {"annual_standing_loss": 0.2}})");
  }

  SUBCASE("a value string equal to the canonical does not conflict")
  {
    const auto out = canonicalize_json_keys(
        R"({"annual_loss": 0.1, "d": "annual_standing_loss"})", r);
    CHECK(out
          == R"({"annual_standing_loss": 0.1, "d": "annual_standing_loss"})");
  }
}

TEST_CASE("Planning JSON — deprecated annual_loss keys still parse")  // NOLINT
{
  constexpr std::string_view json = R"json({
    "options": {"model_options": {"battery_annual_loss": 0.12}},
    "system": {
      "battery_array": [
        {"uid": 1, "name": "b_old", "annual_loss": 0.3},
        {"uid": 2, "name": "b_new", "annual_standing_loss": 0.4}
      ],
      "reservoir_array": [
        {"uid": 1, "name": "r1", "junction": 1, "annual_loss": 0.05}
      ]
    }
  })json";

  const auto planning = parse_planning_json(json);
  const auto& sys = planning.system;
  REQUIRE(sys.battery_array.size() == 2);
  CHECK(scalar_of(sys.battery_array[0].annual_standing_loss)
        == doctest::Approx(0.3));
  CHECK(scalar_of(sys.battery_array[1].annual_standing_loss)
        == doctest::Approx(0.4));
  REQUIRE(sys.reservoir_array.size() == 1);
  CHECK(scalar_of(sys.reservoir_array[0].annual_standing_loss)
        == doctest::Approx(0.05));
  CHECK(
      planning.options.model_options.battery_annual_standing_loss.value_or(-1.0)
      == doctest::Approx(0.12));
}

TEST_CASE("Planning JSON — both annual_loss spellings is an error")  // NOLINT
{
  constexpr std::string_view json = R"json({
    "system": {
      "battery_array": [
        {"uid": 1, "name": "b1", "annual_loss": 0.3,
         "annual_standing_loss": 0.4}
      ]
    }
  })json";
  CHECK_THROWS_AS((void)parse_planning_json(json), std::invalid_argument);
}

TEST_CASE("--set deprecated model_options.battery_annual_loss")  // NOLINT
{
  Planning planning;
  REQUIRE(apply_set_options(planning, {"model_options.battery_annual_loss=0"}));
  CHECK(
      planning.options.model_options.battery_annual_standing_loss.value_or(-1.0)
      == doctest::Approx(0.0));

  Planning planning2;
  REQUIRE(apply_set_options(
      planning2, {"model_options.battery_annual_standing_loss=0.2"}));
  CHECK(planning2.options.model_options.battery_annual_standing_loss.value_or(
            -1.0)
        == doctest::Approx(0.2));
}

TEST_CASE(  // NOLINT
    "annual_standing_loss — write-back uses the new key for every storage")
{
  // `to_json` of each storage class must emit `annual_standing_loss`
  // (never the deprecated `annual_loss`) and round-trip the value.
  const auto check_round_trip = [](const auto& parsed)
  {
    using T = std::remove_cvref_t<decltype(parsed)>;
    CHECK(scalar_of(parsed.annual_standing_loss) == doctest::Approx(0.07));
    const auto js = daw::json::to_json(parsed);
    CHECK(js.find("\"annual_standing_loss\"") != std::string::npos);
    CHECK(js.find("\"annual_loss\"") == std::string::npos);
    const auto again = daw::json::from_json<T>(js);
    CHECK(scalar_of(again.annual_standing_loss) == doctest::Approx(0.07));
  };

  check_round_trip(daw::json::from_json<ThermalStorage>(
      R"({"uid": 1, "name": "ts", "thermal_node": 1,
          "annual_standing_loss": 0.07})"));
  check_round_trip(daw::json::from_json<HydrogenStorage>(
      R"({"uid": 1, "name": "hs", "annual_standing_loss": 0.07})"));
  check_round_trip(daw::json::from_json<AmmoniaStorage>(
      R"({"uid": 1, "name": "as", "annual_standing_loss": 0.07})"));
  check_round_trip(daw::json::from_json<LngTerminal>(
      R"({"uid": 1, "name": "lng", "generator": 1,
          "annual_standing_loss": 0.07})"));
}

TEST_CASE("demand_fail_segments — write-back round trip")  // NOLINT
{
  ModelOptions mo;
  mo.demand_fail_segments = Array<ScalarFailSegment> {
      {.fraction = 0.1, .cost = 300.0},
      {.cost = 900.0},
  };
  const auto js = daw::json::to_json(mo);
  CHECK(js.find("\"demand_fail_segments\"") != std::string::npos);
  const auto again = daw::json::from_json<ModelOptions>(js);
  REQUIRE(again.demand_fail_segments.has_value());
  CHECK(again.demand_fail_segments == mo.demand_fail_segments);
}
