/**
 * @file      test_lp_fingerprint_wiring.cpp
 * @brief     `options.lp_fingerprint` really computes the fingerprint
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Regression: `LpMatrixOptions::compute_fingerprint` was never set on the
 * production path, so `--set lp_fingerprint=true` wrote
 * `lp_fingerprint_*.json` files with `"hash": ""` and an empty template,
 * and every comparison of two runs passed.
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/lp_fingerprint.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

namespace fs = std::filesystem;

struct TinyCase
{
  System system;
  Simulation simulation;
};

[[nodiscard]] TinyCase make_tiny_case()
{
  return TinyCase {
      .system =
          {
              .name = "FingerprintWiring",
              .bus_array =
                  {
                      {.uid = Uid {1}, .name = "b1"},
                  },
              .demand_array =
                  {
                      {
                          .uid = Uid {1},
                          .name = "d1",
                          .bus = Uid {1},
                          .capacity = 100.0,
                      },
                  },
              .generator_array =
                  {
                      {
                          .uid = Uid {1},
                          .name = "g1",
                          .bus = Uid {1},
                          .gcost = 50.0,
                          .capacity = 300.0,
                      },
                  },
          },
      .simulation =
          {
              .block_array =
                  {
                      {.uid = Uid {1}, .duration = 1.0},
                  },
              .stage_array =
                  {
                      {.uid = Uid {1}, .first_block = 0, .count_block = 1},
                  },
              .scenario_array =
                  {
                      {.uid = Uid {1}},
                  },
          },
  };
}

[[nodiscard]] std::string read_text(const fs::path& path)
{
  std::ifstream in(path);
  return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

}  // namespace

TEST_CASE("sha256_hex matches the FIPS 180-2 test vectors")  // NOLINT
{
  CHECK(sha256_hex("")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  CHECK(sha256_hex("abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  // Two-block message (56 bytes: the padding spills into a second block).
  CHECK(sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")
        == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
}

TEST_CASE(
    "options.lp_fingerprint computes and writes the fingerprint")  // NOLINT
{
  const auto [system, simulation] = make_tiny_case();
  const auto tmpdir = fs::temp_directory_path() / "gtopt_fp_wiring";
  fs::remove_all(tmpdir);

  SUBCASE("enabled: non-empty template and hash on disk")
  {
    PlanningOptions opts;
    opts.output_directory = tmpdir.string();
    opts.lp_fingerprint = true;
    const PlanningOptionsLP options(opts);
    SimulationLP simulation_lp(simulation, options);
    SystemLP system_lp(system, simulation_lp);

    const auto& fp = system_lp.fingerprint();
    CHECK(fp.structural_hash.size() == 64);
    CHECK_FALSE(fp.col_template.empty());
    CHECK_FALSE(fp.row_template.empty());

    REQUIRE(system_lp.linear_interface().resolve().has_value());
    system_lp.write_out();

    const auto file = tmpdir / "lp_fingerprint_scene_0_phase_0.json";
    REQUIRE(fs::exists(file));
    const auto text = read_text(file);
    CHECK(text.find(fp.structural_hash) != std::string::npos);
    CHECK(text.find(R"("hash": "")") == std::string::npos);
    CHECK(text.find(R"("class": "Generator")") != std::string::npos);
  }

  SUBCASE("disabled: nothing computed")
  {
    PlanningOptions opts;
    opts.output_directory = tmpdir.string();
    const PlanningOptionsLP options(opts);
    SimulationLP simulation_lp(simulation, options);
    SystemLP system_lp(system, simulation_lp);
    CHECK(system_lp.fingerprint().structural_hash.empty());
  }

  fs::remove_all(tmpdir);
}

TEST_CASE("gtopt_main --set lp_fingerprint=true writes a real hash")  // NOLINT
{
  const auto base = fs::temp_directory_path() / "gtopt_fp_wiring_main";
  fs::remove_all(base);
  fs::create_directories(base);
  const auto json = base / "fp_case.json";
  std::ofstream {json} << R"({
    "options": {"output_compression": "uncompressed"},
    "simulation": {
      "block_array": [{"uid": 1, "duration": 1}],
      "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
      "scenario_array": [{"uid": 1}]
    },
    "system": {
      "name": "fp_case",
      "bus_array": [{"uid": 1, "name": "b1"}],
      "generator_array": [
        {"uid": 1, "name": "g1", "bus": 1, "gcost": 10.0, "capacity": 200.0}
      ],
      "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": 50.0}]
    }
  })";

  const auto out = base / "out";
  const auto rc = gtopt_main(MainOptions {
      .planning_files = {json.string()},
      .set_options =
          {
              "output_directory=" + out.string(),
              "lp_fingerprint=true",
          },
  });
  REQUIRE(rc.has_value());
  CHECK(*rc == 0);

  bool found = false;
  for (const auto& entry : fs::directory_iterator(out)) {
    const auto name = entry.path().filename().string();
    if (!name.starts_with("lp_fingerprint_")) {
      continue;
    }
    found = true;
    const auto text = read_text(entry.path());
    CHECK(text.find(R"("hash": "")") == std::string::npos);
    CHECK(text.find(R"("class": "Bus")") != std::string::npos);
  }
  CHECK(found);

  fs::remove_all(base);
}
