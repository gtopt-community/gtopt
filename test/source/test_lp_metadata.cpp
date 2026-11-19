// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/// @file test_lp_metadata.cpp
/// @brief Tests for the `--lp-metadata` JSON sidecar written next to LP files.

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/lp_matrix_options.hpp>
#include <gtopt/lp_metadata.hpp>
#include <gtopt/main_options.hpp>
#include <gtopt/planning_lp.hpp>

#include "sddp_helpers.hpp"
#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

struct MetaCol
{
  std::string name;
  std::string cls;
  std::string variable;
  bool integer {};
  double scale {};
};

struct MetaRow
{
  std::string name;
  std::string cls;
  std::string constraint;
  double scale {};
};

struct MetaObjective
{
  double scale_objective {};
};

struct MetaBlock
{
  double duration {};
  double probability {};
  double discount {};
};

struct Meta
{
  std::string schema;
  std::string lp_file;
  MetaObjective objective;
  std::vector<MetaBlock> blocks;
  std::vector<MetaCol> columns;
  std::vector<MetaRow> rows;
};

}  // namespace

template<>
struct daw::json::json_data_contract<MetaCol>
{
  using type = json_member_list<json_string<"name">,
                                json_string<"class">,
                                json_string<"variable">,
                                json_bool<"integer">,
                                json_number<"scale">>;
};

template<>
struct daw::json::json_data_contract<MetaRow>
{
  using type = json_member_list<json_string<"name">,
                                json_string<"class">,
                                json_string<"constraint">,
                                json_number<"scale">>;
};

template<>
struct daw::json::json_data_contract<MetaObjective>
{
  using type = json_member_list<json_number<"scale_objective">>;
};

template<>
struct daw::json::json_data_contract<MetaBlock>
{
  using type = json_member_list<json_number<"duration">,
                                json_number<"probability">,
                                json_number<"discount">>;
};

template<>
struct daw::json::json_data_contract<Meta>
{
  using type = json_member_list<json_string<"schema">,
                                json_string<"lp_file">,
                                json_class<"objective", MetaObjective>,
                                json_array<"blocks", MetaBlock>,
                                json_array<"columns", MetaCol>,
                                json_array<"rows", MetaRow>>;
};

namespace
{

auto slurp(const std::filesystem::path& p) -> std::string
{
  std::ifstream f(p);
  return {std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
}

auto make_commitment_planning() -> Planning
{
  auto planning = make_single_phase_planning();
  planning.system.generator_array[0].pmin = 20.0;
  planning.system.simple_commitment_array = {
      {
          .uid = Uid {1},
          .name = "sc1",
          .generator = Uid {1},
          .dispatch_pmin = 20.0,
          .relax = false,
      },
  };
  // Binary status columns need a MIP-capable backend.
  planning.options.lp_matrix_options.solver_name =
      solver_test::first_mip_solver();
  return planning;
}

auto make_tmp_dir(const std::string& name) -> std::filesystem::path
{
  const auto dir = std::filesystem::temp_directory_path() / name;
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  return dir;
}

}  // namespace

TEST_CASE("lp_metadata_path replaces .lp with .meta.json")  // NOLINT
{
  CHECK(lp_metadata_path("/a/b/model.lp") == "/a/b/model.meta.json");
  CHECK(lp_metadata_path("model") == "model.meta.json");
}

TEST_CASE("lp_metadata sidecar matches the LP (commitment case)")  // NOLINT
{
  auto planning = make_commitment_planning();
  planning.options.lp_metadata = true;
  planning.options.model_options.scale_objective = OptReal {1000.0};

  const auto flat_opts = make_lp_matrix_options(
      true, std::nullopt, false, std::nullopt, std::nullopt);
  PlanningLP plp(std::move(planning), flat_opts);

  const auto dir = make_tmp_dir("gtopt_test_lp_metadata_on");
  const auto stem = (dir / "model").string();
  plp.write_lp(stem);

  REQUIRE(std::filesystem::exists(stem + ".lp"));
  REQUIRE(std::filesystem::exists(stem + ".meta.json"));

  const auto text = slurp(stem + ".meta.json");
  const auto meta = daw::json::from_json<Meta>(text);
  const auto lp_text = slurp(stem + ".lp");

  const auto& li = plp.systems().front().front().linear_interface();

  CHECK(meta.schema == "gtopt-lp-metadata/1");
  CHECK(meta.lp_file == "model.lp");
  CHECK(meta.objective.scale_objective == doctest::Approx(1000.0));
  REQUIRE(meta.blocks.size() == 1);
  CHECK(meta.blocks.front().duration == doctest::Approx(1.0));
  CHECK(meta.blocks.front().probability == doctest::Approx(1.0));

  CHECK(meta.columns.size() == static_cast<size_t>(li.get_numcols()));
  CHECK(meta.rows.size() == static_cast<size_t>(li.get_numrows()));

  // Every name in the sidecar is a name written in the .lp file.
  for (const auto& c : meta.columns) {
    CHECK(lp_text.find(c.name) != std::string::npos);
    CHECK(c.scale > 0.0);
  }
  for (const auto& r : meta.rows) {
    CHECK(lp_text.find(r.name) != std::string::npos);
  }

  // Structured fields are populated and the commitment status is integer.
  const auto has_class = [&](std::string_view cls)
  {
    return std::ranges::any_of(meta.columns,
                               [&](const MetaCol& c) { return c.cls == cls; });
  };
  CHECK(has_class("Generator"));
  const auto n_int = std::ranges::count_if(
      meta.columns, [](const MetaCol& c) { return c.integer; });
  CHECK(n_int >= 1);

  std::filesystem::remove_all(dir);
}

TEST_CASE("lp_metadata is not written when the option is off")  // NOLINT
{
  auto planning = make_single_phase_planning();
  const auto flat_opts = make_lp_matrix_options(
      true, std::nullopt, false, std::nullopt, std::nullopt);
  PlanningLP plp(std::move(planning), flat_opts);

  const auto dir = make_tmp_dir("gtopt_test_lp_metadata_off");
  const auto stem = (dir / "model").string();
  plp.write_lp(stem);

  CHECK(std::filesystem::exists(stem + ".lp"));
  CHECK_FALSE(std::filesystem::exists(stem + ".meta.json"));

  std::filesystem::remove_all(dir);
}

TEST_CASE("lp_metadata writes one sidecar per cell (multi-phase)")  // NOLINT
{
  auto planning = make_3phase_hydro_planning();
  planning.options.lp_metadata = true;
  const auto flat_opts = make_lp_matrix_options(
      true, std::nullopt, false, std::nullopt, std::nullopt);
  PlanningLP plp(std::move(planning), flat_opts);

  const auto dir = make_tmp_dir("gtopt_test_lp_metadata_multi");
  const auto stem = (dir / "model").string();
  plp.write_lp(stem);

  std::size_t n_meta = 0;
  for (const auto& e : std::filesystem::directory_iterator(dir)) {
    const auto name = e.path().filename().string();
    if (name.ends_with(".meta.json")) {
      ++n_meta;
      const auto meta = daw::json::from_json<Meta>(slurp(e.path()));
      CHECK(meta.lp_file.ends_with(".lp"));
      CHECK_FALSE(meta.columns.empty());
      CHECK(std::filesystem::exists(dir / meta.lp_file));
    }
  }
  CHECK(n_meta == 3);

  std::filesystem::remove_all(dir);
}
