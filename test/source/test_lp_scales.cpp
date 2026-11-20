// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/// @file test_lp_scales.cpp
/// @brief Tests for the `--lp-scales` JSON sidecar written next to LP files.

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <ranges>
#include <string>
#include <tuple>

#include <arrow/api.h>
#include <doctest/doctest.h>
#include <gtopt/array_index_traits.hpp>
#include <gtopt/enum_option.hpp>
#include <gtopt/json/json_lp_scales.hpp>
#include <gtopt/lp_file_format.hpp>
#include <gtopt/lp_matrix_options.hpp>
#include <gtopt/lp_scales.hpp>
#include <gtopt/planning_lp.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

auto slurp(const std::filesystem::path& p) -> std::string
{
  std::ifstream f(p, std::ios::binary);
  return {std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
}

auto read_scales(const std::filesystem::path& p) -> LpScales
{
  return daw::json::from_json<LpScales>(slurp(p));
}

auto make_tmp_dir(const std::string& name) -> std::filesystem::path
{
  const auto dir = std::filesystem::temp_directory_path() / name;
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  return dir;
}

auto named_options()
{
  return make_lp_matrix_options(
      true, std::nullopt, false, std::nullopt, std::nullopt);
}

/// Reads one Parquet table of the sidecar back.
auto read_table(const std::filesystem::path& p) -> std::shared_ptr<arrow::Table>
{
  // parquet_read_table() takes the path without the `.parquet` suffix.
  auto table = parquet_read_table(p.parent_path() / p.stem());
  REQUIRE(table.has_value());
  auto combined = (*table)->CombineChunks();
  REQUIRE(combined.ok());
  return *combined;
}

template<typename ArrayT>
auto column_of(const arrow::Table& table, const std::string& name)
    -> std::shared_ptr<ArrayT>
{
  const auto chunked = table.GetColumnByName(name);
  REQUIRE(chunked != nullptr);
  REQUIRE(chunked->num_chunks() == 1);
  return std::static_pointer_cast<ArrayT>(chunked->chunk(0));
}

/// The Parquet tables list exactly the columns/rows whose scale is not 1,
/// with the names written in the model file.
void check_tables(const LpScales& meta,
                  const std::filesystem::path& dir,
                  const LinearInterface& li,
                  const std::string& lp_text)
{
  REQUIRE(meta.tables.has_value());
  const auto tables = meta.tables.value_or(LpScalesTables {});

  const auto cols = read_table(dir / tables.columns);
  CHECK(static_cast<Size>(cols->num_rows()) == tables.num_scaled_columns);
  const auto n_scaled_cols = std::ranges::count_if(
      std::views::iota(Index {0}, li.get_numcols()),
      [&](Index i) { return li.get_col_scale(ColIndex {i}) != 1.0; });
  CHECK(cols->num_rows() == n_scaled_cols);
  {
    const auto scene = column_of<arrow::Int32Array>(*cols, "scene_uid");
    const auto index = column_of<arrow::Int32Array>(*cols, "index");
    const auto name = column_of<arrow::StringArray>(*cols, "name");
    const auto cls = column_of<arrow::StringArray>(*cols, "class");
    const auto scale = column_of<arrow::DoubleArray>(*cols, "scale");
    std::ignore = column_of<arrow::StringArray>(*cols, "unit");
    std::ignore = column_of<arrow::StringArray>(*cols, "context");
    for (const auto k : std::views::iota(std::int64_t {0}, cols->num_rows())) {
      CAPTURE(name->GetString(k));
      CHECK(scene->Value(k) == meta.scene_uid);
      CHECK(scale->Value(k) != 1.0);
      CHECK(scale->Value(k)
            == doctest::Approx(li.get_col_scale(ColIndex {index->Value(k)})));
      CHECK_FALSE(cls->GetString(k).empty());
      CHECK(lp_text.find(name->GetString(k)) != std::string::npos);
    }
  }

  const auto rows = read_table(dir / tables.rows);
  CHECK(static_cast<Size>(rows->num_rows()) == tables.num_scaled_rows);
  const auto n_scaled_rows = std::ranges::count_if(
      std::views::iota(Index {0}, li.get_numrows()),
      [&](Index i) { return li.get_row_scale(RowIndex {i}) != 1.0; });
  CHECK(rows->num_rows() == n_scaled_rows);
  {
    const auto phase = column_of<arrow::Int32Array>(*rows, "phase_uid");
    const auto index = column_of<arrow::Int32Array>(*rows, "index");
    const auto name = column_of<arrow::StringArray>(*rows, "name");
    const auto scale = column_of<arrow::DoubleArray>(*rows, "scale");
    for (const auto k : std::views::iota(std::int64_t {0}, rows->num_rows())) {
      CAPTURE(name->GetString(k));
      CHECK(phase->Value(k) == meta.phase_uid);
      CHECK(scale->Value(k)
            == doctest::Approx(li.get_row_scale(RowIndex {index->Value(k)})));
      CHECK(lp_text.find(name->GetString(k)) != std::string::npos);
    }
  }
}

/// Header, audit trail and numerics of the sidecar, plus its Parquet
/// tables.
void check_against_lp(const LpScales& meta,
                      const std::filesystem::path& dir,
                      const LinearInterface& li,
                      const std::string& lp_text)
{
  CHECK(meta.schema == lp_scales_schema);
  CHECK(meta.num_cols == li.get_numcols());
  CHECK(meta.num_rows == li.get_numrows());
  CHECK(meta.objective.scale_objective
        == doctest::Approx(li.scale_objective()));
  CHECK(meta.objective.constant == doctest::Approx(li.get_obj_constant()));
  CHECK(meta.objective.sense == "min");

  check_tables(meta, dir, li, lp_text);

  // Audit trail and numerics.
  CHECK_FALSE(meta.gtopt.version.empty());
  CHECK_FALSE(meta.gtopt.git_revision.empty());
  CHECK_FALSE(meta.numerics.equilibration_method.empty());
  REQUIRE(meta.numerics.objective.has_value());
  const auto obj = meta.numerics.objective.value_or(LpScalesRange {});
  CHECK(obj.min_abs > 0.0);
  CHECK(obj.max_abs >= obj.min_abs);
  CHECK(obj.log10_ratio
        == doctest::Approx(std::log10(obj.max_abs / obj.min_abs)));
  CHECK(meta.numerics.col_scales.has_value());
}

}  // namespace

TEST_CASE("lp_scales_path strips model and compression extensions")  // NOLINT
{
  CHECK(lp_scales_path("/a/b/model.lp") == "/a/b/model.scales.json");
  CHECK(lp_scales_path("/a/b/model.mps") == "/a/b/model.scales.json");
  CHECK(lp_scales_path("/a/b/model.lp.gz") == "/a/b/model.scales.json");
  CHECK(lp_scales_path("/a/b/model.mps.zst") == "/a/b/model.scales.json");
  CHECK(lp_scales_path("model") == "model.scales.json");
  CHECK(lp_scales_path("model.txt") == "model.txt.scales.json");
  CHECK(lp_scales_path("/a/model.mps.gz", lp_scales_cols_suffix)
        == "/a/model.scales_cols.parquet");
  CHECK(lp_scales_path("/a/model.lp", lp_scales_rows_suffix)
        == "/a/model.scales_rows.parquet");
}

TEST_CASE("LpScales JSON round-trips through daw::json")  // NOLINT
{
  const LpScales meta {
      .gtopt =
          {
              .version = "1.0",
              .git_revision = "abc123",
          },
      .lp_file = "model.mps",
      .scene_uid = 1,
      .phase_uid = 2,
      .num_cols = 10,
      .num_rows = 7,
      .objective =
          {
              .scale_objective = 1000.0,
              .constant = 1234.5,
          },
      .blocks =
          {
              {
                  .scenario = 1,
                  .stage = 2,
                  .block = 3,
                  .duration = 2.0,
                  .probability = 0.5,
                  .discount = 0.9,
                  .cost_factor = 0.9,
              },
          },
      .stages =
          {
              {
                  .scenario = 1,
                  .stage = 2,
                  .duration = 8760.0,
                  .probability = 0.5,
                  .discount = 0.9,
                  .cost_factor = 3942.0,
              },
          },
      .tables =
          LpScalesTables {
              .columns = "model.scales_cols.parquet",
              .rows = "model.scales_rows.parquet",
              .num_scaled_columns = 4,
              .num_scaled_rows = 6,
          },
      .numerics =
          {
              .equilibration_method = "row_max",
              .nnz = 12,
              .matrix =
                  LpScalesRange {
                      .min_abs = 0.01,
                      .max_abs = 100.0,
                      .log10_ratio = 4.0,
                  },
              .row_types =
                  {
                      {
                          .type = "balance",
                          .count = 3,
                          .nnz = 9,
                          .min_abs = 1.0,
                          .max_abs = 1.0,
                      },
                  },
          },
  };

  const auto text = lp_scales_to_json(meta);
  CHECK(text.find(R"("schema":"gtopt-lp-scales/1")") != std::string::npos);
  CHECK(text.find(R"("columns":"model.scales_cols.parquet")")
        != std::string::npos);
  // Unset optionals are left out, not written as null.
  CHECK(text.find(R"("bounds")") == std::string::npos);
  CHECK(text.find("null") == std::string::npos);

  const auto back = daw::json::from_json<LpScales>(text);
  CHECK(back.lp_file == "model.mps");
  CHECK(back.num_cols == 10);
  CHECK(back.objective.constant == doctest::Approx(1234.5));
  REQUIRE(back.blocks.size() == 1);
  CHECK(back.blocks.front().cost_factor == doctest::Approx(0.9));
  REQUIRE(back.tables.has_value());
  const auto tables = back.tables.value_or(LpScalesTables {});
  CHECK(tables.rows == "model.scales_rows.parquet");
  CHECK(tables.num_scaled_columns == 4);
  CHECK(tables.num_scaled_rows == 6);
  CHECK(back.gtopt.git_revision == "abc123");
  REQUIRE(back.stages.size() == 1);
  CHECK(back.stages.front().cost_factor == doctest::Approx(3942.0));
  CHECK(back.numerics.equilibration_method == "row_max");
  CHECK(back.numerics.matrix.value_or(LpScalesRange {}).log10_ratio
        == doctest::Approx(4.0));
  CHECK_FALSE(back.numerics.objective.has_value());
  REQUIRE(back.numerics.row_types.size() == 1);
  CHECK(back.numerics.row_types.front().type == "balance");
}

TEST_CASE("lp_scales sidecar matches the written LP")  // NOLINT
{
  auto planning = make_single_phase_planning();
  planning.options.lp_scales = LpScalesLevel::full;
  planning.options.model_options.scale_objective = OptReal {1000.0};
  const PlanningLP plp(std::move(planning), named_options());

  const auto dir = make_tmp_dir("gtopt_test_lp_scales_on");
  const auto stem = (dir / "model").string();
  REQUIRE(plp.write_lp(stem));

  REQUIRE(std::filesystem::exists(stem + ".lp"));
  REQUIRE(std::filesystem::exists(stem + ".scales.json"));
  const auto meta = read_scales(stem + ".scales.json");
  const auto& li = plp.systems().front().front().linear_interface();

  CHECK(meta.lp_file == "model.lp");
  CHECK(meta.objective.scale_objective == doctest::Approx(1000.0));
  REQUIRE(meta.blocks.size() == 1);
  CHECK(meta.blocks.front().duration == doctest::Approx(1.0));
  CHECK(meta.blocks.front().probability == doctest::Approx(1.0));
  REQUIRE(meta.stages.size() == 1);
  CHECK(meta.stages.front().cost_factor
        == doctest::Approx(meta.stages.front().probability
                           * meta.stages.front().discount
                           * meta.stages.front().duration));
  check_against_lp(meta, dir, li, slurp(stem + ".lp"));

  std::filesystem::remove_all(dir);
}

TEST_CASE("lp_scales is not written when the option is off")  // NOLINT
{
  const PlanningLP plp(make_single_phase_planning(), named_options());

  const auto dir = make_tmp_dir("gtopt_test_lp_scales_off");
  const auto stem = (dir / "model").string();
  REQUIRE(plp.write_lp(stem));

  CHECK(std::filesystem::exists(stem + ".lp"));
  CHECK_FALSE(std::filesystem::exists(stem + ".scales.json"));

  std::filesystem::remove_all(dir);
}

TEST_CASE("lp_scales writes one sidecar per cell (multi-phase)")  // NOLINT
{
  auto planning = make_3phase_hydro_planning();
  planning.options.lp_scales = LpScalesLevel::full;
  const PlanningLP plp(std::move(planning), named_options());

  const auto dir = make_tmp_dir("gtopt_test_lp_scales_multi");
  const auto stem = (dir / "model").string();
  REQUIRE(plp.write_lp(stem));

  std::size_t n_scales = 0;
  for (const auto& e : std::filesystem::directory_iterator(dir)) {
    if (!e.path().filename().string().ends_with(".scales.json")) {
      continue;
    }
    ++n_scales;
    const auto meta = read_scales(e.path());
    CAPTURE(meta.lp_file);
    CHECK(meta.lp_file.ends_with(".lp"));
    REQUIRE(std::filesystem::exists(dir / meta.lp_file));
    const SystemLP* cell = nullptr;
    for (const auto& phase_systems : plp.systems()) {
      for (const auto& system : phase_systems) {
        if (system.scene().uid() == meta.scene_uid
            && system.phase().uid() == meta.phase_uid)
        {
          cell = &system;
        }
      }
    }
    REQUIRE(cell != nullptr);
    check_against_lp(
        meta, dir, cell->linear_interface(), slurp(dir / meta.lp_file));
  }
  CHECK(n_scales == 3);

  std::filesystem::remove_all(dir);
}

TEST_CASE("lp_scales names the real model file (mps)")  // NOLINT
{
  auto planning = make_single_phase_planning();
  planning.options.lp_scales = LpScalesLevel::full;
  const PlanningLP plp(std::move(planning), named_options());

  const auto dir = make_tmp_dir("gtopt_test_lp_scales_mps");
  const auto stem = (dir / "model").string();
  REQUIRE(plp.write_lp(stem, LpFileFormat::mps));

  REQUIRE(std::filesystem::exists(stem + ".mps"));
  REQUIRE(std::filesystem::exists(stem + ".scales.json"));
  CHECK(read_scales(stem + ".scales.json").lp_file == "model.mps");

  std::filesystem::remove_all(dir);
}

TEST_CASE("lp_scales sidecar is compressed with the model file")  // NOLINT
{
  auto planning = make_single_phase_planning();
  planning.options.lp_scales = LpScalesLevel::full;
  planning.options.lp_compression = CompressionCodec::gzip;
  const PlanningLP plp(std::move(planning), named_options());

  const auto dir = make_tmp_dir("gtopt_test_lp_scales_gz");
  const auto stem = (dir / "model").string();
  REQUIRE(plp.write_lp(stem, LpFileFormat::mps));

  CHECK(std::filesystem::exists(stem + ".mps.gz"));
  CHECK(std::filesystem::exists(stem + ".scales.json.gz"));
  CHECK_FALSE(std::filesystem::exists(stem + ".scales.json"));
  // Parquet compresses internally: the tables keep their name.
  CHECK(std::filesystem::exists(stem + ".scales_cols.parquet"));
  CHECK(std::filesystem::exists(stem + ".scales_rows.parquet"));

  std::filesystem::remove_all(dir);
}

TEST_CASE("lp_scales summary level omits the per-column data")  // NOLINT
{
  auto planning = make_3phase_hydro_planning();
  planning.options.lp_scales = LpScalesLevel::summary;
  const PlanningLP plp(std::move(planning), named_options());

  const auto dir = make_tmp_dir("gtopt_test_lp_scales_summary");
  const auto stem = (dir / "model").string();
  REQUIRE(plp.write_lp(stem));

  const auto path = dir / "model_scene_0_phase_1.scales.json";
  REQUIRE(std::filesystem::exists(path));
  const auto meta = read_scales(path);
  CHECK(meta.level == "summary");
  CHECK_FALSE(meta.tables.has_value());
  CHECK_FALSE(std::filesystem::exists(
      dir / "model_scene_0_phase_1.scales_cols.parquet"));
  CHECK_FALSE(meta.blocks.empty());
  CHECK_FALSE(meta.stages.empty());
  CHECK(meta.numerics.objective.has_value());
  CHECK_FALSE(meta.gtopt.git_revision.empty());

  std::filesystem::remove_all(dir);
}

TEST_CASE("LpScalesLevel parses levels and boolean aliases")  // NOLINT
{
  CHECK(require_enum<LpScalesLevel>("lp_scales", "summary")
        == LpScalesLevel::summary);
  CHECK(require_enum<LpScalesLevel>("lp_scales", "full")
        == LpScalesLevel::full);
  CHECK(require_enum<LpScalesLevel>("lp_scales", "true")
        == LpScalesLevel::full);
  CHECK(require_enum<LpScalesLevel>("lp_scales", "false")
        == LpScalesLevel::none);
  CHECK_THROWS(std::ignore = require_enum<LpScalesLevel>("lp_scales", "big"));
}

TEST_CASE("lp_scales failures are reported, never thrown")  // NOLINT
{
  auto planning = make_single_phase_planning();
  planning.options.lp_scales = LpScalesLevel::full;
  const PlanningLP plp(std::move(planning), named_options());

  const auto dir = make_tmp_dir("gtopt_test_lp_scales_fail");
  // A regular file used as the parent directory of the sidecar.
  const auto blocker = dir / "blocker";
  std::ofstream {blocker}.put('x');
  const auto& sys = plp.systems().front().front();

  const auto result = write_lp_scales(sys, (blocker / "model.lp").string());
  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::FileIOError);
  CHECK_FALSE(std::filesystem::exists(blocker / "model.scales.json"));

  // The LP itself is still written when only the sidecar fails.
  const auto stem = (dir / "model").string();
  std::filesystem::create_directories(stem + ".scales.json");  // not a file
  CHECK(plp.write_lp(stem));
  CHECK(std::filesystem::exists(stem + ".lp"));

  std::filesystem::remove_all(dir);
}
