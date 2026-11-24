// SPDX-License-Identifier: BSD-3-Clause
//
// File-backed battery schedules propagated by System::expand_batteries.
//
// A unified Battery is expanded into synthesized `<bat>_gen` (Generator),
// `<bat>_dem` (Demand), `<bat>_conv` (Converter) and `uc_<bat>_gen`
// (Commitment) elements.  Copying a file-backed schedule string verbatim
// (e.g. `"active": "act"`) made each synthesized element look up its own
// class directory and uid (Generator/act for `<bat>_gen`'s uid) — a missing
// file or "Can't find element" at LP build.  expand_batteries now rewrites
// the reference to `"Battery@<field>#<battery uid>"`, which the input path
// resolves against the battery's own table rows.
//
// Helpers are prefixed `bfs_` so unity builds do not collide.
#include <filesystem>
#include <format>
#include <fstream>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include <arrow/api.h>
#include <arrow/io/api.h>
#include <doctest/doctest.h>
#include <gtopt/battery.hpp>
#include <gtopt/battery_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/converter_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/field_sched.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/planning_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>
#include <parquet/arrow/writer.h>
#include <unistd.h>

using namespace gtopt;

namespace
{

constexpr Uid bfs_bat_uid {7};  // deliberately != every synthesized uid

[[nodiscard]] auto bfs_i32(const std::vector<int32_t>& v) -> ArrowArray
{
  arrow::Int32Builder b;
  REQUIRE(b.AppendValues(v).ok());
  std::shared_ptr<arrow::Array> a;
  REQUIRE(b.Finish(&a).ok());
  return a;
}

[[nodiscard]] auto bfs_f64(const std::vector<double>& v) -> ArrowArray
{
  arrow::DoubleBuilder b;
  REQUIRE(b.AppendValues(v).ok());
  std::shared_ptr<arrow::Array> a;
  REQUIRE(b.Finish(&a).ok());
  return a;
}

void bfs_write_parquet(const std::filesystem::path& file,
                       const arrow::FieldVector& fields,
                       const arrow::ArrayVector& arrays)
{
  std::filesystem::create_directories(file.parent_path());
  auto table = arrow::Table::Make(arrow::schema(fields), arrays);
  auto ostream = arrow::io::FileOutputStream::Open(file.string());
  REQUIRE(ostream.ok());
  REQUIRE(parquet::arrow::WriteTable(
              *table, arrow::default_memory_pool(), ostream.ValueOrDie(), 1024)
              .ok());
  REQUIRE(ostream.ValueOrDie()->Close().ok());
}

void bfs_write_text(const std::filesystem::path& file, std::string_view text)
{
  std::filesystem::create_directories(file.parent_path());
  std::ofstream out(file);
  out << text;
}

/// Battery/<name>: per-stage active flag (stage 1 on, stage 2 off).
void bfs_write_active(const std::filesystem::path& dir,
                      bool csv,
                      std::string_view name)
{
  const auto base = dir / "Battery" / name;
  if (csv) {
    bfs_write_text(base.string() + ".csv", "stage,uid,value\n1,7,1\n2,7,0\n");
    return;
  }
  bfs_write_parquet(base.string() + ".parquet",
                    {
                        arrow::field("stage", arrow::int32()),
                        arrow::field("uid", arrow::int32()),
                        arrow::field("value", arrow::int32()),
                    },
                    {
                        bfs_i32({1, 2}),
                        bfs_i32({7, 7}),
                        bfs_i32({1, 0}),
                    });
}

/// Battery/<name>: per-(stage, block) real schedule, value = base + block.
void bfs_write_tb(const std::filesystem::path& dir,
                  bool csv,
                  std::string_view name,
                  double base)
{
  const auto file = dir / "Battery" / name;
  const std::vector<int32_t> stages {1, 1, 2, 2};
  const std::vector<int32_t> blocks {1, 2, 3, 4};
  std::vector<double> values;
  values.reserve(blocks.size());
  for (const auto b : blocks) {
    values.push_back(base + b);
  }
  if (csv) {
    std::string text = "stage,block,uid,value\n";
    for (std::size_t i = 0; i < stages.size(); ++i) {
      text += std::format("{},{},7,{:.2f}\n", stages[i], blocks[i], values[i]);
    }
    bfs_write_text(file.string() + ".csv", text);
    return;
  }
  bfs_write_parquet(file.string() + ".parquet",
                    {
                        arrow::field("stage", arrow::int32()),
                        arrow::field("block", arrow::int32()),
                        arrow::field("uid", arrow::int32()),
                        arrow::field("value", arrow::float64()),
                    },
                    {
                        bfs_i32(stages),
                        bfs_i32(blocks),
                        bfs_i32(std::vector<int32_t>(stages.size(), 7)),
                        bfs_f64(values),
                    });
}

/// 2 stages x 2 blocks of 1 h.
[[nodiscard]] auto bfs_make_simulation() -> Simulation
{
  Simulation sim;
  for (int b = 1; b <= 4; ++b) {
    sim.block_array.push_back(Block {
        .uid = Uid {b},
        .duration = 1.0,
    });
  }
  sim.stage_array = {
      {
          .uid = Uid {1},
          .first_block = 0,
          .count_block = 2,
      },
      {
          .uid = Uid {2},
          .first_block = 2,
          .count_block = 2,
      },
  };
  sim.scenario_array = {
      {
          .uid = Uid {1},
      },
  };
  return sim;
}

[[nodiscard]] auto bfs_make_system(Battery bat) -> System
{
  System system {
      .name = "BatFileSched",
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
                  .lmax = 20.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {1},
                  .gcost = 50.0,
                  .capacity = 100.0,
              },
          },
      .battery_array = {std::move(bat)},
  };
  system.expand_batteries();
  return system;
}

[[nodiscard]] auto bfs_make_battery() -> Battery
{
  return Battery {
      .uid = bfs_bat_uid,
      .name = "bat",
      .bus = Uid {1},
      .input_efficiency = 0.9,
      .output_efficiency = 0.9,
      .emin = 0.0,
      .emax = 40.0,
      .eini = 10.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .capacity = 40.0,
      .use_state_variable = true,
      .daily_cycle = false,
  };
}

template<typename ElementLP>
[[nodiscard]] auto bfs_find(const SystemLP& sys_lp, std::string_view name)
    -> const ElementLP&
{
  for (const auto& e : sys_lp.elements<ElementLP>()) {
    if (e.object().name == name) {
      return e;
    }
  }
  FAIL("element not found: " << name);
  return sys_lp.elements<ElementLP>().front();
}

/// Build + solve the LP and check the battery's active schedule
/// (stage 1 on, stage 2 off) reached every synthesized element.
void bfs_check_active(const System& system,
                      const std::filesystem::path& input_dir,
                      DataFormat format)
{
  PlanningOptions opts;
  opts.input_directory = input_dir.string();
  opts.input_format = format;
  opts.model_options.demand_fail_cost = 1.0e4;
  opts.model_options.use_single_bus = true;
  const PlanningOptionsLP options {opts};
  const Simulation sim = bfs_make_simulation();
  SimulationLP sim_lp(sim, options);
  SystemLP sys_lp(system, sim_lp);

  const auto& stages = sys_lp.phase().stages();
  REQUIRE(stages.size() == 2);
  const auto& gen = bfs_find<GeneratorLP>(sys_lp, "bat_gen");
  const auto& dem = bfs_find<DemandLP>(sys_lp, "bat_dem");
  const auto& conv = bfs_find<ConverterLP>(sys_lp, "bat_conv");
  const auto& cmt = bfs_find<CommitmentLP>(sys_lp, "uc_bat_gen");
  const auto& bat = sys_lp.elements<BatteryLP>().front();
  CHECK(bat.is_active(stages[0]));
  CHECK_FALSE(bat.is_active(stages[1]));
  CHECK(gen.is_active(stages[0]));
  CHECK_FALSE(gen.is_active(stages[1]));
  CHECK(dem.is_active(stages[0]));
  CHECK_FALSE(dem.is_active(stages[1]));
  CHECK(conv.is_active(stages[0]));
  CHECK_FALSE(conv.is_active(stages[1]));
  CHECK(cmt.is_active(stages[0]));
  CHECK_FALSE(cmt.is_active(stages[1]));

  auto& li = sys_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  CHECK(result.value() == 0);
}

[[nodiscard]] auto bfs_tmpdir(std::string_view tag) -> std::filesystem::path
{
  auto dir = std::filesystem::temp_directory_path()
      / std::format("gtopt_bfs_{}_{}", tag, ::getpid());
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  return dir;
}

}  // namespace

TEST_CASE("parse_file_sched_ref grammar")  // NOLINT
{
  SUBCASE("bare field uses the owner class")
  {
    const auto ref = parse_file_sched_ref("Generator", "pmax");
    CHECK(ref.class_name == "Generator");
    CHECK(ref.field_name == "pmax");
    CHECK_FALSE(ref.uid.has_value());
  }
  SUBCASE("class override")
  {
    const auto ref = parse_file_sched_ref("Generator", "Battery@act");
    CHECK(ref.class_name == "Battery");
    CHECK(ref.field_name == "act");
    CHECK_FALSE(ref.uid.has_value());
  }
  SUBCASE("class and uid override")
  {
    const auto ref = parse_file_sched_ref("Demand", "Battery@act#42");
    CHECK(ref.class_name == "Battery");
    CHECK(ref.field_name == "act");
    CHECK(ref.uid.value_or(-1) == 42);
  }
  SUBCASE("non-numeric '#' suffix stays in the field stem")
  {
    const auto ref = parse_file_sched_ref("Generator", "pmax#v2");
    CHECK(ref.class_name == "Generator");
    CHECK(ref.field_name == "pmax#v2");
    CHECK_FALSE(ref.uid.has_value());
  }
  SUBCASE("make_file_sched_ref builds and is idempotent")
  {
    const auto r1 = make_file_sched_ref("Battery", "act", Uid {3});
    CHECK(r1 == "Battery@act#3");
    CHECK(make_file_sched_ref("Battery", "Other@act", Uid {3})
          == "Other@act#3");
    CHECK(make_file_sched_ref("Battery", r1, Uid {9}) == r1);
  }
}

TEST_CASE("expand_batteries redirects file schedules to the battery")  // NOLINT
{
  auto bat = bfs_make_battery();
  bat.active = Active {FileSched {"act"}};
  bat.pmax_discharge = TBRealFieldSched {FileSched {"pmaxd"}};
  bat.pmax_charge = TBRealFieldSched {FileSched {"Battery@pmaxc"}};
  bat.pmin_charge = TBRealFieldSched {FileSched {"pminc"}};
  bat.pmin_discharge = TBRealFieldSched {FileSched {"pmind"}};
  bat.discharge_cost = TBRealFieldSched {FileSched {"dcost"}};
  bat.charge_cost = TBRealFieldSched {FileSched {"ccost"}};
  const auto system = bfs_make_system(std::move(bat));

  const auto file_of = [](const auto& opt) -> std::string
  {
    if (!opt.has_value()) {
      return "<unset>";
    }
    const auto* const f = std::get_if<FileSched>(&*opt);
    return f != nullptr ? *f : std::string {"<inline>"};
  };

  REQUIRE(system.generator_array.size() == 2);
  REQUIRE(system.demand_array.size() == 2);
  const auto& gen = system.generator_array.back();
  const auto& dem = system.demand_array.back();
  const auto& conv = system.converter_array.back();
  const auto& cmt = system.commitment_array.back();
  CHECK(gen.name == "bat_gen");
  CHECK(file_of(gen.active) == "Battery@act#7");
  CHECK(file_of(gen.pmax) == "Battery@pmaxd#7");
  CHECK(file_of(gen.gcost) == "Battery@dcost#7");
  CHECK(file_of(dem.active) == "Battery@act#7");
  CHECK(file_of(dem.lmax) == "Battery@pmaxc#7");
  CHECK(file_of(dem.lmin) == "Battery@pminc#7");
  CHECK(file_of(dem.fcost) == "Battery@ccost#7");
  CHECK(file_of(conv.active) == "Battery@act#7");
  CHECK(file_of(cmt.active) == "Battery@act#7");
  CHECK(file_of(cmt.pmin) == "Battery@pmind#7");
  // The battery itself keeps its own (unredirected) reference.
  CHECK(file_of(system.battery_array.front().active) == "act");
}

TEST_CASE("Battery file-backed active reaches synthesized elements")  // NOLINT
{
  for (const bool csv : {false, true}) {
    CAPTURE(csv);
    const auto dir = bfs_tmpdir(csv ? "act_csv" : "act_pq");
    bfs_write_active(dir, csv, "act");

    auto bat = bfs_make_battery();
    bat.active = Active {FileSched {"act"}};
    const auto system = bfs_make_system(std::move(bat));
    bfs_check_active(system, dir, csv ? DataFormat::csv : DataFormat::parquet);
    std::filesystem::remove_all(dir);
  }
}

TEST_CASE("Battery inline active reaches synthesized elements")  // NOLINT
{
  auto bat = bfs_make_battery();
  bat.active = Active {std::vector<IntBool> {1, 0}};
  const auto system = bfs_make_system(std::move(bat));
  bfs_check_active(
      system, std::filesystem::temp_directory_path(), DataFormat::parquet);
}

TEST_CASE("Battery file-backed active with commitment = true")  // NOLINT
{
  const auto dir = bfs_tmpdir("act_uc");
  bfs_write_active(dir, /*csv=*/false, "act");

  auto bat = bfs_make_battery();
  bat.active = Active {FileSched {"act"}};
  bat.commitment = true;
  bat.pmin_discharge = 2.0;
  const auto system = bfs_make_system(std::move(bat));
  REQUIRE(system.commitment_array.size() == 1);
  CHECK_FALSE(system.commitment_array.front().relax.value_or(true));
  CHECK(std::get<FileSched>(
            system.commitment_array.front().active.value_or(Active {0}))
        == "Battery@act#7");

  // The binary commitment needs a MIP-capable active solver; LP-only
  // backends (e.g. CI pinned to clp) reject integer columns at load.
  auto& reg = SolverRegistry::instance();
  if (!reg.supports_mip(reg.default_solver())) {
    MESSAGE("Skipping MIP build/solve — default solver '"
            << reg.default_solver() << "' is LP-only");
    std::filesystem::remove_all(dir);
    return;
  }
  bfs_check_active(system, dir, DataFormat::parquet);
  std::filesystem::remove_all(dir);
}

TEST_CASE("Battery file-backed power and cost schedules")  // NOLINT
{
  for (const bool csv : {false, true}) {
    CAPTURE(csv);
    const auto dir = bfs_tmpdir(csv ? "tb_csv" : "tb_pq");
    bfs_write_tb(dir, csv, "pmaxd", 3.0);  // 4, 5, 6, 7
    bfs_write_tb(dir, csv, "pmaxc", 10.0);  // 11, 12, 13, 14
    bfs_write_tb(dir, csv, "dcost", 0.5);  // 1.5, 2.5, 3.5, 4.5

    auto bat = bfs_make_battery();
    bat.pmax_discharge = TBRealFieldSched {FileSched {"pmaxd"}};
    bat.pmax_charge = TBRealFieldSched {FileSched {"pmaxc"}};
    bat.discharge_cost = TBRealFieldSched {FileSched {"dcost"}};
    const auto system = bfs_make_system(std::move(bat));

    PlanningOptions opts;
    opts.input_directory = dir.string();
    opts.input_format = csv ? DataFormat::csv : DataFormat::parquet;
    opts.model_options.demand_fail_cost = 1.0e4;
    opts.model_options.use_single_bus = true;
    const PlanningOptionsLP options {opts};
    const Simulation sim = bfs_make_simulation();
    SimulationLP sim_lp(sim, options);
    SystemLP sys_lp(system, sim_lp);

    const auto& gen = bfs_find<GeneratorLP>(sys_lp, "bat_gen");
    const auto& dem = bfs_find<DemandLP>(sys_lp, "bat_dem");
    CHECK(gen.param_pmax(make_uid<Stage>(1), make_uid<Block>(1)).value_or(-1.0)
          == doctest::Approx(4.0));
    CHECK(gen.param_pmax(make_uid<Stage>(2), make_uid<Block>(4)).value_or(-1.0)
          == doctest::Approx(7.0));
    CHECK(dem.param_lmax(make_uid<Stage>(1), make_uid<Block>(2)).value_or(-1.0)
          == doctest::Approx(12.0));
    CHECK(dem.param_lmax(make_uid<Stage>(2), make_uid<Block>(3)).value_or(-1.0)
          == doctest::Approx(13.0));

    auto& li = sys_lp.linear_interface();
    const auto result = li.resolve();
    REQUIRE(result.has_value());
    CHECK(result.value() == 0);
    std::filesystem::remove_all(dir);
  }
}
