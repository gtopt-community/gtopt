// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/// @file test_lp_mps_output.cpp
/// @brief `--lp-file` MPS output: format selection, file contents (names,
///        sections, integer MARKERs), unsupported-backend error and the
///        unchanged default `.lp` behaviour.

#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_file_format.hpp>
#include <gtopt/lp_matrix_options.hpp>
#include <gtopt/main_options.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

auto read_all(const std::filesystem::path& path) -> std::string
{
  std::ifstream in(path);
  return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

/// min 2*gen + 10*on  s.t.  gen - 5*on <= 0 ; gen >= 3 ; on integer in [0,1]
auto make_uc_lp() -> LinearProblem
{
  LinearProblem lp("mps_test");
  const auto ctx = make_block_context(
      make_uid<Scenario>(0), make_uid<Stage>(0), make_uid<Block>(0));
  const auto gen = lp.add_col(SparseCol {
      .lowb = 3.0,
      .uppb = 100.0,
      .cost = 2.0,
      .class_name = "Generator",
      .variable_name = "gen",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  const auto on = lp.add_col(SparseCol {
      .lowb = 0.0,
      .uppb = 1.0,
      .cost = 10.0,
      .is_integer = true,
      .class_name = "Commitment",
      .variable_name = "status",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  const auto row = lp.add_row(SparseRow {
      .uppb = 0.0,
      .class_name = "Generator",
      .constraint_name = "pmax",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  lp.set_coeff(row, gen, 1.0);
  lp.set_coeff(row, on, -5.0);
  return lp;
}

auto make_interface(LinearProblem& lp) -> LinearInterface
{
  const auto flat =
      lp.flatten(make_lp_matrix_options(/*enable_names=*/true, std::nullopt));
  LinearInterface li;
  li.load_flat(flat);
  return li;
}

auto scratch_dir(const std::string& tag) -> std::filesystem::path
{
  auto dir = std::filesystem::temp_directory_path() / ("gtopt_mps_" + tag);
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  return dir;
}

auto supports_mps(const LinearInterface& li) -> bool
{
  const auto name = li.solver_name();
  return name == "highs" || name == "cbc" || name == "clp";
}

}  // namespace

TEST_CASE("split_lp_file_target infers format from extension")  // NOLINT
{
  SUBCASE("plain stem keeps the requested format")
  {
    const auto t = split_lp_file_target("out/model");
    CHECK(t.stem == "out/model");
    CHECK(t.format == LpFileFormat::lp);
    CHECK(split_lp_file_target("m", LpFileFormat::mps).format
          == LpFileFormat::mps);
  }
  SUBCASE(".lp is stripped")
  {
    const auto t = split_lp_file_target("model.lp");
    CHECK(t.stem == "model");
    CHECK(t.format == LpFileFormat::lp);
    CHECK(split_lp_file_target("model.lp", LpFileFormat::mps).format
          == LpFileFormat::mps);
  }
  SUBCASE(".mps is stripped and selects MPS")
  {
    const auto t = split_lp_file_target("a.b/model.mps");
    CHECK(t.stem == "a.b/model");
    CHECK(t.format == LpFileFormat::mps);
  }
}

TEST_CASE("parse_lp_file_format")  // NOLINT
{
  CHECK(parse_lp_file_format("lp") == LpFileFormat::lp);
  CHECK(parse_lp_file_format("mps") == LpFileFormat::mps);
  CHECK_FALSE(parse_lp_file_format("xml").has_value());
  CHECK(lp_file_extension(LpFileFormat::lp) == ".lp");
  CHECK(lp_file_extension(LpFileFormat::mps) == ".mps");
}

TEST_CASE("write_lp default format still writes .lp")  // NOLINT
{
  const auto dir = scratch_dir("default");
  const auto stem = (dir / "model").string();
  auto lp = make_uc_lp();
  auto li = make_interface(lp);

  REQUIRE(li.write_lp(stem).has_value());
  CHECK(std::filesystem::exists(stem + ".lp"));
  CHECK_FALSE(std::filesystem::exists(stem + ".mps"));
  std::filesystem::remove_all(dir);
}

TEST_CASE("write_lp mps keeps names and integer markers")  // NOLINT
{
  const auto dir = scratch_dir("mps");
  const auto stem = (dir / "model").string();
  auto lp = make_uc_lp();
  auto li = make_interface(lp);

  const auto result = li.write_lp(stem, LpFileFormat::mps);
  if (!supports_mps(li)) {
    // Backends without MPS support must fail loudly, naming the solver.
    REQUIRE_FALSE(result.has_value());
    CHECK(result.error().message.find(std::string(li.solver_name()))
          != std::string::npos);
    CHECK_FALSE(std::filesystem::exists(stem + ".lp"));
    std::filesystem::remove_all(dir);
    return;
  }

  REQUIRE(result.has_value());
  REQUIRE(std::filesystem::exists(stem + ".mps"));
  CHECK_FALSE(std::filesystem::exists(stem + ".lp"));

  const auto text = read_all(stem + ".mps");
  CHECK(text.find("ROWS") != std::string::npos);
  CHECK(text.find("COLUMNS") != std::string::npos);
  CHECK(text.find("ENDATA") != std::string::npos);
  if (li.solver_name() == "highs") {
    CHECK(text.find("MARKER") != std::string::npos);
    CHECK(text.find("INTORG") != std::string::npos);
    CHECK(text.find("INTEND") != std::string::npos);
  } else {
    // CoinMpsIO (cbc/clp) writes binary columns as BV bounds.
    CHECK((text.find("MARKER") != std::string::npos
           || text.find(" BV ") != std::string::npos));
  }
  CHECK(text.find("generator") != std::string::npos);
  CHECK(text.find("commitment") != std::string::npos);
  std::filesystem::remove_all(dir);
}

TEST_CASE("write_lp mps on a pure LP has no integer markers")  // NOLINT
{
  const auto dir = scratch_dir("pure");
  const auto stem = (dir / "model").string();
  LinearProblem lp("pure");
  const auto ctx = make_block_context(
      make_uid<Scenario>(0), make_uid<Stage>(0), make_uid<Block>(0));
  const auto c = lp.add_col(SparseCol {
      .cost = 1.0,
      .class_name = "Generator",
      .variable_name = "gen",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  const auto r = lp.add_row(SparseRow {
      .lowb = 1.0,
      .class_name = "Bus",
      .constraint_name = "balance",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  lp.set_coeff(r, c, 1.0);
  auto li = make_interface(lp);

  if (supports_mps(li)) {
    REQUIRE(li.write_lp(stem, LpFileFormat::mps).has_value());
    const auto text = read_all(stem + ".mps");
    CHECK(text.find("ROWS") != std::string::npos);
    CHECK(text.find("MARKER") == std::string::npos);
  }
  std::filesystem::remove_all(dir);
}
