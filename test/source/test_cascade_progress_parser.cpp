/**
 * @file      test_cascade_progress_parser.cpp
 * @brief     cascade_progress.json reader: escapes, unknown keys, errors
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_cascade_progress.cpp` covers the plain round-trip.  The file is
 * the cascade's restart record, so the reader must (a) round-trip any
 * name / cuts path, including characters that need escaping, (b) ignore
 * keys written by a newer schema whatever their JSON type, and (c) turn
 * every malformed token into an InvalidInput error instead of a bogus
 * restart state.  Helpers are prefixed `cpp_` for unity builds.
 */

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <random>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/cascade_progress.hpp>

using namespace gtopt;

namespace
{

struct CppTempDir
{
  std::filesystem::path path;

  CppTempDir()
  {
    namespace fs = std::filesystem;
    path = fs::temp_directory_path() / "gtopt_cascade_progress_parser"
        / std::to_string(static_cast<std::int64_t>(std::random_device {}()));
    fs::create_directories(path);
  }

  ~CppTempDir()
  {
    std::error_code ec;
    std::filesystem::remove_all(path, ec);
  }

  CppTempDir(const CppTempDir&) = delete;
  CppTempDir(CppTempDir&&) = delete;
  auto operator=(const CppTempDir&) -> CppTempDir& = delete;
  auto operator=(CppTempDir&&) -> CppTempDir& = delete;
};

auto cpp_load_text(const CppTempDir& tmp, std::string_view text)
{
  const auto file = tmp.path / "progress.json";
  {
    std::ofstream out(file, std::ios::binary | std::ios::trunc);
    out << text;
  }
  return load_cascade_progress(file);
}

}  // namespace

TEST_CASE("CascadeProgress round-trips strings that need escaping")  // NOLINT
{
  CppTempDir tmp;
  const auto file = tmp.path / "p.json";

  const std::string tricky_name = "L0 \"fast\"\tpass\\x\nnext\r\x01";
  const std::string tricky_cuts = R"(C:\cuts\level "0".parquet)";

  CascadeProgress p;
  p.run_id = "run/with\\slash";
  p.levels.resize(1);
  p.levels[0].index = 4;
  p.levels[0].name = tricky_name;
  p.levels[0].cuts_file = tricky_cuts;
  p.levels[0].status = CascadeLevelStatus::in_progress;
  p.levels[0].converged = false;
  p.levels[0].iters = -1;
  p.levels[0].global_iter_after = 12;

  REQUIRE(save_cascade_progress(p, file).has_value());
  const auto loaded = load_cascade_progress(file);
  REQUIRE(loaded.has_value());
  CHECK(loaded->run_id == p.run_id);
  REQUIRE(loaded->levels.size() == 1);
  const auto& l = loaded->levels[0];
  CHECK(l.index == 4);
  CHECK(l.name == tricky_name);
  CHECK(l.cuts_file == tricky_cuts);
  CHECK(l.status == CascadeLevelStatus::in_progress);
  CHECK_FALSE(l.converged);
  CHECK(l.iters == -1);
  CHECK(l.global_iter_after == 12);
}

TEST_CASE("CascadeProgress reader decodes hand-written escapes")  // NOLINT
{
  CppTempDir tmp;
  const auto res = cpp_load_text(tmp, R"({
    "run_id": "a\/b\u0041\u00e9",
    "levels": []
  })");
  REQUIRE(res.has_value());
  // '/' unescaped, \u0041 -> 'A', non-ASCII code points become '?'.
  CHECK(res->run_id == "a/bA?");
}

TEST_CASE("CascadeProgress reader skips unknown keys of any type")  // NOLINT
{
  CppTempDir tmp;
  const auto res = cpp_load_text(tmp, R"({
    "schema_version": 2,
    "future_object": { "a": [1, 2, {"b": null}], "c": "x", "d": true },
    "future_array": [ "s", false, null, -1.5e3, [ ] , { } ],
    "future_null": null,
    "future_number": 3.25,
    "run_id": "r",
    "levels": [
      { "index": 1, "extra": { "nested": [ true ] }, "name": "L1",
        "status": "done", "converged": true, "iters": 7,
        "global_iter_after": 9, "cuts_file": "c.parquet",
        "another": "ignored" },
      { "index": 2, "status": "bogus_status" }
    ]
  })");
  REQUIRE(res.has_value());
  CHECK(res->schema_version == 2);
  CHECK(res->run_id == "r");
  REQUIRE(res->levels.size() == 2);
  CHECK(res->levels[0].name == "L1");
  CHECK(res->levels[0].status == CascadeLevelStatus::done);
  CHECK(res->levels[0].converged);
  CHECK(res->levels[0].iters == 7);
  CHECK(res->levels[0].global_iter_after == 9);
  CHECK(res->levels[0].cuts_file == "c.parquet");
  // An unrecognised status string falls back to pending.
  CHECK(res->levels[1].index == 2);
  CHECK(res->levels[1].status == CascadeLevelStatus::pending);
}

TEST_CASE("CascadeProgress reader rejects malformed tokens")  // NOLINT
{
  CppTempDir tmp;
  const auto rejected = [&](std::string_view text)
  {
    const auto res = cpp_load_text(tmp, text);
    CAPTURE(text);
    REQUIRE_FALSE(res.has_value());
    CHECK(res.error().code == ErrorCode::InvalidInput);
    CHECK(res.error().message.find("CascadeProgress") != std::string::npos);
  };

  SUBCASE("unknown escape")
  {
    rejected(R"({ "run_id": "a\qb" })");
  }
  SUBCASE("truncated \\u escape")
  {
    rejected(R"({ "run_id": "\u00)");
  }
  SUBCASE("non-hex \\u escape")
  {
    rejected(R"({ "run_id": "\uZZZZ" })");
  }
  SUBCASE("unterminated string")
  {
    rejected(R"({ "run_id": "abc)");
  }
  SUBCASE("escape at end of input")
  {
    rejected(R"({ "run_id": "abc\)");
  }
  SUBCASE("key is not a string")
  {
    rejected(R"({ run_id: "x" })");
  }
  SUBCASE("missing colon")
  {
    rejected(R"({ "run_id" "x" })");
  }
  SUBCASE("non-integer schema_version")
  {
    rejected(R"({ "schema_version": "one" })");
  }
  SUBCASE("integer out of range")
  {
    rejected(R"({ "schema_version": 99999999999999999999999 })");
  }
  SUBCASE("bad bool")
  {
    rejected(R"({ "levels": [ { "converged": yes } ] })");
  }
  SUBCASE("bad null in skipped value")
  {
    rejected(R"({ "future": nul })");
  }
  SUBCASE("bad number in skipped value")
  {
    rejected(R"({ "future": 1e999999 })");
  }
  SUBCASE("levels is not an array")
  {
    rejected(R"({ "levels": { } })");
  }
  SUBCASE("level is not an object")
  {
    rejected(R"({ "levels": [ 3 ] })");
  }
  SUBCASE("top level is not an object")
  {
    rejected("[]");
  }
}

TEST_CASE("save_cascade_progress reports an uncreatable directory")  // NOLINT
{
  CppTempDir tmp;
  // The parent "directory" is a regular file.
  const auto blocker = tmp.path / "not_a_dir";
  {
    std::ofstream out(blocker);
    out << "x";
  }
  CascadeProgress p;
  p.run_id = "r";
  const auto res = save_cascade_progress(p, blocker / "progress.json");
  REQUIRE_FALSE(res.has_value());
  CHECK(res.error().code == ErrorCode::FileIOError);
  CHECK(res.error().message.find("create_directories") != std::string::npos);
}

TEST_CASE(  // NOLINT
    "save_cascade_progress onto a directory fails instead of faking success")
{
  CppTempDir tmp;
  // The target path is an existing, non-empty directory: rename() fails,
  // the copy_file() fallback fails too, and the temp-file cleanup
  // succeeds.  The error must survive the cleanup.
  const auto target = tmp.path / "progress.json";
  std::filesystem::create_directories(target / "occupied");
  CascadeProgress p;
  p.run_id = "r";
  const auto res = save_cascade_progress(p, target);
  REQUIRE_FALSE(res.has_value());
  CHECK(res.error().code == ErrorCode::FileIOError);
  CHECK(res.error().message.find("copy fallback failed") != std::string::npos);
  CHECK(std::filesystem::is_directory(target));
  CHECK_FALSE(std::filesystem::exists(tmp.path / "progress.json.tmp"));
}
