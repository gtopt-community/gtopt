// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// File-backed per-stage `active` schedules (issue #544).
//
//  - A GeneratorProfile / CapacityProfile with `"active": "<file>"` used to
//    fail at LP build with "access_sched: invalid arrow array or index":
//    ProfileObjectLP (and ~20 other LP wrappers) built their ObjectLP base
//    through the context-free constructor, so the file was never indexed.
//  - An `active` file whose `value` column is double (1.0 / 0.0) used to
//    fail with "type mismatch for int32 cast".  Integer-valued doubles are
//    now accepted; a fractional value or an unreadable column type fails
//    at load with a message naming the class/file.
//
// Helpers are prefixed `asf_` so unity builds don't collide.

#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include <arrow/api.h>
#include <arrow/io/api.h>
#include <doctest/doctest.h>
#include <gtopt/schedule.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <parquet/arrow/writer.h>

using namespace gtopt;

namespace
{

template<typename Builder, typename T>
[[nodiscard]] auto asf_array(const std::vector<T>& v) -> ArrowArray
{
  Builder b;
  REQUIRE(b.AppendValues(v).ok());
  std::shared_ptr<arrow::Array> a;
  REQUIRE(b.Finish(&a).ok());
  return a;
}

/// Write `<input_dir>/<cname>/<fname>.parquet` in long layout
/// (stage, uid, value) with the given `value` array.
void asf_write_stage_table(const std::filesystem::path& input_dir,
                           std::string_view cname,
                           std::string_view fname,
                           const std::vector<int32_t>& stages,
                           const std::vector<int32_t>& uids,
                           const ArrowArray& values)
{
  const auto dir = input_dir / cname;
  std::filesystem::create_directories(dir);
  const arrow::FieldVector fields {
      arrow::field("stage", arrow::int32()),
      arrow::field("uid", arrow::int32()),
      arrow::field("value", values->type()),
  };
  const arrow::ArrayVector arrays {
      asf_array<arrow::Int32Builder>(stages),
      asf_array<arrow::Int32Builder>(uids),
      values,
  };
  const auto table = arrow::Table::Make(arrow::schema(fields), arrays);
  const auto path = (dir / fname).string() + ".parquet";
  auto out = arrow::io::FileOutputStream::Open(path);
  REQUIRE(out.ok());
  REQUIRE(parquet::arrow::WriteTable(
              *table, arrow::default_memory_pool(), out.ValueOrDie(), 1024)
              .ok());
  REQUIRE(out.ValueOrDie()->Close().ok());
}

/// Two stages of one block (1 h each), one scenario.
[[nodiscard]] auto asf_simulation() -> Simulation
{
  return Simulation {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1,
              },
              {
                  .uid = Uid {2},
                  .duration = 1,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              },
              {
                  .uid = Uid {2},
                  .first_block = 1,
                  .count_block = 1,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {1},
              },
          },
  };
}

[[nodiscard]] auto asf_options(const std::filesystem::path& input_dir)
    -> PlanningOptionsLP
{
  PlanningOptions opts;
  opts.input_directory = input_dir.string();
  opts.model_options.scale_objective = 1.0;
  opts.model_options.demand_fail_cost = 10000.0;
  return PlanningOptionsLP {opts};
}

/// wind (free, 200 MW) with a 0.5 profile (spill cost 10 $/MWh) plus a
/// 100 $/MWh backup; base demand 80 MW.
[[nodiscard]] auto asf_system(OptActive profile_active,
                              OptActive extra_demand_active) -> System
{
  return System {
      .name = "active_schedule_file",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .capacity = 80.0,
              },
              {
                  .uid = Uid {2},
                  .name = "d2",
                  .active = std::move(extra_demand_active),
                  .bus = Uid {1},
                  .capacity = 50.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "wind",
                  .bus = Uid {1},
                  .gcost = 0.0,
                  .capacity = 200.0,
              },
              {
                  .uid = Uid {2},
                  .name = "backup",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 200.0,
              },
          },
      .generator_profile_array =
          {
              {
                  .uid = Uid {1},
                  .name = "wind_profile",
                  .active = std::move(profile_active),
                  .generator = Uid {1},
                  .profile = 0.5,
                  .scost = 10.0,
              },
          },
  };
}

[[nodiscard]] auto asf_solve(const System& system,
                             const std::filesystem::path& input_dir) -> double
{
  const auto simulation = asf_simulation();
  const auto options = asf_options(input_dir);
  SimulationLP simulation_lp {simulation, options};
  SystemLP system_lp {system, simulation_lp};
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(lp.is_optimal());
  return lp.get_obj_value_raw();
}

[[nodiscard]] auto asf_tmp(std::string_view name) -> std::filesystem::path
{
  auto root = std::filesystem::temp_directory_path() / name;
  std::filesystem::remove_all(root);
  return root;
}

}  // namespace

TEST_CASE("GeneratorProfile file-backed per-stage active (#544)")  // NOLINT
{
  const auto root = asf_tmp("test_asf_profile_active");
  const auto input_dir = root / "input";

  // Profile on in stage 1, off in stage 2 (int32 values).
  asf_write_stage_table(input_dir,
                        "GeneratorProfile",
                        "act",
                        {1, 2},
                        {1, 1},
                        asf_array<arrow::Int32Builder>(std::vector {1, 0}));

  // Stage 1: wind is capped at 0.5 x 200 = 100 MW and must spill 20 MW
  // (gen + spill = 100) → 20 x 10 = 200.  Stage 2: profile inactive, wind
  // serves the 80 MW for free.  d2 is inactive in both stages.
  const auto system = asf_system(Active {FileSched {"act"}}, Active {False});
  CHECK(asf_solve(system, input_dir) == doctest::Approx(200.0));

  // Reference: profile active in both stages doubles the spill cost.
  const auto both = asf_system(Active {True}, Active {False});
  CHECK(asf_solve(both, input_dir) == doctest::Approx(400.0));

  std::filesystem::remove_all(root);
}

TEST_CASE("Demand active file with integer-valued doubles (#544)")  // NOLINT
{
  const auto root = asf_tmp("test_asf_demand_active_double");
  const auto input_dir = root / "input";

  // d2 on in stage 1, off in stage 2 — written as float64 1.0 / 0.0.
  asf_write_stage_table(
      input_dir,
      "Demand",
      "dact",
      {1, 2},
      {2, 2},
      asf_array<arrow::DoubleBuilder>(std::vector {1.0, 0.0}));

  // Profile active in both stages caps wind at 100 MW.
  //   Stage 1: d1 + d2 = 130 MW → wind 100 + backup 30 x 100 = 3000.
  //   Stage 2: d2 off, 80 MW → wind 80, spill 20 x 10 = 200.
  // Total 3200 (d2 active in both stages would give 6000).
  const auto system = asf_system(Active {True}, Active {FileSched {"dact"}});
  CHECK(asf_solve(system, input_dir) == doctest::Approx(3200.0));

  std::filesystem::remove_all(root);
}

TEST_CASE("Integer schedule from a double column — validation")  // NOLINT
{
  const auto root = asf_tmp("test_asf_active_validation");
  const auto input_dir = root / "input";

  const auto simulation = asf_simulation();
  const auto options = asf_options(input_dir);

  SUBCASE("whole-number doubles resolve to 0/1")
  {
    asf_write_stage_table(
        input_dir,
        "TestElem",
        "act",
        {1, 2},
        {7, 7},
        asf_array<arrow::DoubleBuilder>(std::vector {0.0, 1.0}));
    SimulationLP simulation_lp {simulation, options};
    const System sys;
    SystemLP system_lp {sys, simulation_lp};
    const SystemContext sc {simulation_lp, system_lp};
    const InputContext ic {sc};
    const ActiveSched sched {
        ic, "TestElem", Id {Uid {7}, "e7"}, Active {FileSched {"act"}}};
    CHECK(sched.at(make_uid<Stage>(1)) == False);
    CHECK(sched.at(make_uid<Stage>(2)) == True);
  }

  SUBCASE("fractional value fails naming the file")
  {
    asf_write_stage_table(
        input_dir,
        "TestElem",
        "act",
        {1, 2},
        {7, 7},
        asf_array<arrow::DoubleBuilder>(std::vector {1.0, 0.5}));
    SimulationLP simulation_lp {simulation, options};
    const System sys;
    SystemLP system_lp {sys, simulation_lp};
    const SystemContext sc {simulation_lp, system_lp};
    const InputContext ic {sc};
    std::string what;
    try {
      const ActiveSched sched {
          ic, "TestElem", Id {Uid {7}, "e7"}, Active {FileSched {"act"}}};
    } catch (const std::runtime_error& e) {
      what = e.what();
    }
    CHECK(what.find("TestElem/act") != std::string::npos);
    CHECK(what.find("0.5") != std::string::npos);
    CHECK(what.find("whole numbers") != std::string::npos);
  }

  SUBCASE("unreadable column type fails naming the file")
  {
    arrow::StringBuilder sb;
    REQUIRE(sb.AppendValues(std::vector<std::string> {"on", "off"}).ok());
    std::shared_ptr<arrow::Array> strings;
    REQUIRE(sb.Finish(&strings).ok());
    asf_write_stage_table(
        input_dir, "TestElem", "act", {1, 2}, {7, 7}, strings);
    SimulationLP simulation_lp {simulation, options};
    const System sys;
    SystemLP system_lp {sys, simulation_lp};
    const SystemContext sc {simulation_lp, system_lp};
    const InputContext ic {sc};
    std::string what;
    try {
      const ActiveSched sched {
          ic, "TestElem", Id {Uid {7}, "e7"}, Active {FileSched {"act"}}};
    } catch (const std::runtime_error& e) {
      what = e.what();
    }
    CHECK(what.find("TestElem/act") != std::string::npos);
    CHECK(what.find("cannot be read as an integer schedule")
          != std::string::npos);
  }

  SUBCASE("real schedule accepts an int32 column")
  {
    asf_write_stage_table(input_dir,
                          "TestElem",
                          "cap",
                          {1, 2},
                          {7, 7},
                          asf_array<arrow::Int32Builder>(std::vector {3, 4}));
    SimulationLP simulation_lp {simulation, options};
    const System sys;
    SystemLP system_lp {sys, simulation_lp};
    const SystemContext sc {simulation_lp, system_lp};
    const InputContext ic {sc};
    const TRealSched sched {ic,
                            "TestElem",
                            Id {Uid {7}, "e7"},
                            TRealFieldSched {FileSched {"cap"}}};
    CHECK(sched.at(make_uid<Stage>(1)) == doctest::Approx(3.0));
    CHECK(sched.at(make_uid<Stage>(2)) == doctest::Approx(4.0));
  }

  std::filesystem::remove_all(root);
}

TEST_CASE("ObjectLP without input context rejects a file active")  // NOLINT
{
  Bus bus {
      .uid = Uid {3},
      .name = "b3",
      .active = Active {FileSched {"act"}},
  };
  CHECK_THROWS_AS(ObjectLP<Bus> {bus}, std::invalid_argument);
}
