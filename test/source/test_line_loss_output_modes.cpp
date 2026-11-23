/**
 * @file      test_line_loss_output_modes.cpp
 * @brief     Line flow_sol / loss_sol write-out for piecewise_direct & linear
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_line_loss_sol_emission.cpp` pins the explicit-loss-column modes.
 * `piecewise_direct` and `linear` have no loss column: `LineLP::
 * add_to_output` rebuilds the signed flow from the per-segment columns
 * and the loss from the stamped loss factors.  A wrong sign or a dropped
 * direction there would silently corrupt `Line/flow_sol` / `loss_sol`
 * (the only loss record post-solve audits see).  Single-line, single-
 * block fixtures make the system balance an exact oracle:
 *     loss = generation − demand,   |flow| ≥ demand.
 * Helpers are prefixed `llom_` for unity builds.
 */

#include <cmath>
#include <filesystem>
#include <fstream>
#include <optional>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/line.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

/// First value of the `value` column (or of the last column, if none is
/// named `value`) of the first CSV shard in @p dir whose name starts with
/// @p stem.
[[nodiscard]] auto llom_read_value(const std::filesystem::path& dir,
                                   const std::string& stem)
    -> std::optional<double>
{
  if (!std::filesystem::exists(dir)) {
    return std::nullopt;
  }
  for (const auto& entry : std::filesystem::directory_iterator(dir)) {
    const auto name = entry.path().filename().string();
    if (!name.starts_with(stem + "_") && name != stem + ".csv") {
      continue;
    }
    std::ifstream in(entry.path());
    std::string header;
    std::string row;
    if (!std::getline(in, header) || !std::getline(in, row)) {
      return std::nullopt;
    }
    auto split = [](const std::string& s)
    {
      std::vector<std::string> out;
      std::stringstream ss(s);
      std::string tok;
      while (std::getline(ss, tok, ',')) {
        out.push_back(tok);
      }
      return out;
    };
    const auto hcols = split(header);
    const auto rcols = split(row);
    if (hcols.empty() || rcols.size() != hcols.size()) {
      return std::nullopt;
    }
    std::size_t idx = hcols.size() - 1;
    for (std::size_t i = 0; i < hcols.size(); ++i) {
      if (hcols[i].contains("value")) {
        idx = i;
      }
    }
    return std::stod(rcols[idx]);
  }
  return std::nullopt;
}

struct LlomResult
{
  std::optional<double> flow;
  std::optional<double> loss;
  std::optional<double> generation;
};

/// Two buses, one lossy line, generator on @p gen_bus and a 100 MW load
/// on the other bus.
[[nodiscard]] auto llom_run(const std::string& mode, int gen_bus) -> LlomResult
{
  const int load_bus = (gen_bus == 1) ? 2 : 1;
  const System system = {
      .name = "llom",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
              {
                  .uid = Uid {2},
                  .name = "b2",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {load_bus},
                  .capacity = 100.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "g1",
                  .bus = Uid {gen_bus},
                  .gcost = 10.0,
                  .capacity = 500.0,
              },
          },
      .line_array =
          {
              {
                  .uid = Uid {1},
                  .name = "l1",
                  .bus_a = Uid {1},
                  .bus_b = Uid {2},
                  .voltage = 100.0,
                  .resistance = 0.05,
                  .lossfactor = 0.05,
                  .line_losses_mode = mode,
                  .loss_segments = 4,
                  .tmax_ba = 300.0,
                  .tmax_ab = 300.0,
                  .capacity = 300.0,
              },
          },
  };
  const Simulation simulation = {
      .block_array =
          {
              {
                  .uid = Uid {1},
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
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };

  const auto tmpdir = std::filesystem::temp_directory_path()
      / ("gtopt_llom_" + mode + "_" + std::to_string(gen_bus) + "_"
         + std::to_string(std::random_device {}()));
  std::filesystem::create_directories(tmpdir);

  PlanningOptions opts;
  opts.output_directory = tmpdir.string();
  opts.output_format = DataFormat::csv;
  opts.output_compression = CompressionCodec::uncompressed;
  opts.model_options.use_single_bus = false;
  opts.model_options.use_kirchhoff = false;
  opts.model_options.demand_fail_cost = 1000.0;
  opts.write_out =
      OutputSelection {OutputFlags::solution | OutputFlags::extras};

  const PlanningOptionsLP options(opts);
  SimulationLP simulation_lp(simulation, options);
  SystemLP system_lp(system, simulation_lp);
  const auto status = system_lp.linear_interface().resolve();
  REQUIRE(status.has_value());
  REQUIRE(status.value() == 0);
  system_lp.write_out();

  LlomResult res {
      .flow = llom_read_value(tmpdir / "Line", "flow_sol"),
      .loss = llom_read_value(tmpdir / "Line", "loss_sol"),
      .generation = llom_read_value(tmpdir / "Generator", "generation_sol"),
  };
  std::error_code ec;
  std::filesystem::remove_all(tmpdir, ec);
  return res;
}

void llom_check(const LlomResult& r, double flow_sign)
{
  REQUIRE(r.flow.has_value());
  REQUIRE(r.loss.has_value());
  REQUIRE(r.generation.has_value());
  const double flow = r.flow.value_or(0.0);
  const double loss = r.loss.value_or(-1.0);
  const double gen = r.generation.value_or(0.0);
  // Signed flow: + = A→B, − = B→A.
  CHECK(flow * flow_sign > 0.0);
  CHECK(std::abs(flow) >= 100.0 - 1e-6);
  // Losses are strictly positive and close the system balance.
  CHECK(loss > 1e-6);
  CHECK(gen - 100.0 == doctest::Approx(loss).epsilon(1e-3));
}

}  // namespace

TEST_CASE("LineLP write-out - piecewise_direct signed flow and loss")  // NOLINT
{
  SUBCASE("A→B")
  {
    llom_check(llom_run("piecewise_direct", 1), +1.0);
  }
  SUBCASE("B→A")
  {
    llom_check(llom_run("piecewise_direct", 2), -1.0);
  }
}

TEST_CASE("LineLP write-out - linear mode loss from loss factors")  // NOLINT
{
  SUBCASE("A→B")
  {
    llom_check(llom_run("linear", 1), +1.0);
  }
  SUBCASE("B→A")
  {
    llom_check(llom_run("linear", 2), -1.0);
  }
}
