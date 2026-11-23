// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Resolution order of the naming / unit dialect dictionaries.  The
// default is the copy compiled into the binary; an external file is
// read only through an explicit `$GTOPT_NAMING_DIALECTS_FILE` /
// `$GTOPT_UNIT_DIALECTS_FILE` override.  Regression: a stale
// `~/.local/share/gtopt/naming_dialects.json` (lacking the
// `annual_loss -> annual_standing_loss` rename) used to shadow the
// running binary's dictionary and broke the annual_loss tests.

#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <random>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/names_builtin.hpp>
#include <gtopt/names_registry.hpp>
#include <gtopt/unit_registry.hpp>
#include <gtopt/units_builtin.hpp>

using namespace gtopt;

namespace dialects_resolution_test
{
namespace
{

namespace fs = std::filesystem;

/// Set (or unset, with `nullopt`) an environment variable for the
/// lifetime of the guard; restores the previous value on destruction.
class ScopedEnv
{
public:
  ScopedEnv(const char* name, const std::optional<std::string>& value)
      : m_name_ {name}
  {
    if (const char* old = std::getenv(name); old != nullptr) {
      m_old_ = std::string {old};
    }
    if (value.has_value()) {
      ::setenv(name, value->c_str(), 1);
    } else {
      ::unsetenv(name);
    }
  }

  ~ScopedEnv()
  {
    if (m_old_.has_value()) {
      ::setenv(m_name_.c_str(), m_old_->c_str(), 1);
    } else {
      ::unsetenv(m_name_.c_str());
    }
  }

  ScopedEnv(const ScopedEnv&) = delete;
  ScopedEnv(ScopedEnv&&) = delete;
  auto operator=(const ScopedEnv&) -> ScopedEnv& = delete;
  auto operator=(ScopedEnv&&) -> ScopedEnv& = delete;

private:
  std::string m_name_;
  std::optional<std::string> m_old_;
};

/// Fake HOME holding stale dictionaries in every generic share dir
/// (`$HOME/.local/share/gtopt`, `$XDG_DATA_HOME/gtopt`).
struct StaleHome
{
  fs::path root;

  StaleHome()
  {
    root = fs::temp_directory_path() / "gtopt_dialects_resolution_test"
        / std::to_string(static_cast<std::int64_t>(std::random_device {}()));
    for (const auto& dir :
         {
             root / ".local" / "share" / "gtopt",
             root / "xdg" / "gtopt",
         })
    {
      fs::create_directories(dir);
      write(dir / "naming_dialects.json", kStaleNames);
      write(dir / "unit_dialects.json", kStaleUnits);
    }
  }

  ~StaleHome()
  {
    std::error_code ec;
    fs::remove_all(root, ec);
  }

  StaleHome(const StaleHome&) = delete;
  StaleHome(StaleHome&&) = delete;
  auto operator=(const StaleHome&) -> StaleHome& = delete;
  auto operator=(StaleHome&&) -> StaleHome& = delete;

  [[nodiscard]] fs::path stale_names() const
  {
    return root / ".local" / "share" / "gtopt" / "naming_dialects.json";
  }

  [[nodiscard]] fs::path stale_units() const
  {
    return root / ".local" / "share" / "gtopt" / "unit_dialects.json";
  }

  /// An old dictionary: knows `max_power` but not the
  /// `annual_loss -> annual_standing_loss` rename.
  static constexpr std::string_view kStaleNames = R"json(
{
  "version": 1,
  "aliases": [
    {"class": "generator", "canonical": "pmax", "alt": "max_power", "dialect": "gtopt"}
  ]
}
)json";

  static constexpr std::string_view kStaleUnits = R"json(
{
  "version": 1,
  "units": [
    {"class": "generator", "canonical": "pmax", "dialect": "gtopt", "unit": "MW"}
  ]
}
)json";

private:
  static void write(const fs::path& file, std::string_view text)
  {
    std::ofstream out {file};
    out << text;
  }
};

}  // namespace
}  // namespace dialects_resolution_test

using dialects_resolution_test::ScopedEnv;
using dialects_resolution_test::StaleHome;

TEST_CASE(
    "Dialects — stale share-dir copies never shadow the binary")  // NOLINT
{
  const StaleHome home;
  const ScopedEnv h {"HOME", home.root.string()};
  const ScopedEnv x {"XDG_DATA_HOME", (home.root / "xdg").string()};
  const ScopedEnv n {"GTOPT_NAMING_DIALECTS_FILE", std::nullopt};
  const ScopedEnv u {"GTOPT_UNIT_DIALECTS_FILE", std::nullopt};

  SUBCASE("naming dialects come from the compiled-in dictionary")
  {
    CHECK_FALSE(find_names_file().has_value());
    const auto r = load_names_registry();
    CHECK_FALSE(r.source_path().has_value());
    CHECK(r.canonical_for("annual_loss").value_or("")
          == "annual_standing_loss");
    CHECK(r.canonical_for("battery_annual_loss").value_or("")
          == "battery_annual_standing_loss");
    CHECK(r.is_deprecated("annual_loss"));
  }

  SUBCASE("unit dialects come from the compiled-in dictionary")
  {
    CHECK_FALSE(find_units_file().has_value());
    const auto r = load_unit_registry();
    CHECK_FALSE(r.source_path().has_value());
    CHECK(r.size() == UnitRegistry {kBuiltinUnitsJson}.size());
    CHECK(r.size() > 1);
  }

  SUBCASE("the process singleton resolves the annual_loss rename")
  {
    CHECK(NamesRegistry::instance().canonical_for("annual_loss").value_or("")
          == "annual_standing_loss");
  }
}

TEST_CASE("Dialects — explicit env override takes precedence")  // NOLINT
{
  const StaleHome home;

  SUBCASE("naming override file is loaded verbatim")
  {
    const ScopedEnv n {"GTOPT_NAMING_DIALECTS_FILE",
                       home.stale_names().string()};
    REQUIRE(find_names_file().has_value());
    CHECK(find_names_file().value_or(std::filesystem::path {})
          == home.stale_names());
    const auto r = load_names_registry();
    CHECK(r.source_path().value_or(std::filesystem::path {})
          == home.stale_names());
    CHECK(r.canonical_for("max_power").value_or("") == "pmax");
    CHECK_FALSE(r.canonical_for("annual_loss").has_value());
  }

  SUBCASE("unit override file is loaded verbatim")
  {
    const ScopedEnv u {"GTOPT_UNIT_DIALECTS_FILE", home.stale_units().string()};
    const auto r = load_unit_registry();
    CHECK(r.source_path().value_or(std::filesystem::path {})
          == home.stale_units());
    CHECK(r.size() == 1);
  }

  SUBCASE("missing or empty override falls back to the compiled-in copy")
  {
    for (const auto* value : {"/nonexistent/naming_dialects.json", ""}) {
      const ScopedEnv n {"GTOPT_NAMING_DIALECTS_FILE", std::string {value}};
      const ScopedEnv u {"GTOPT_UNIT_DIALECTS_FILE", std::string {value}};
      CHECK_FALSE(find_names_file().has_value());
      CHECK_FALSE(find_units_file().has_value());
      CHECK(load_names_registry().canonical_for("annual_loss").value_or("")
            == "annual_standing_loss");
      CHECK_FALSE(load_unit_registry().source_path().has_value());
    }
  }

  SUBCASE("unparsable override falls back to the compiled-in copy")
  {
    const auto bad = home.root / "bad.json";
    {
      std::ofstream out {bad};
      out << "{ not json";
    }
    const ScopedEnv n {"GTOPT_NAMING_DIALECTS_FILE", bad.string()};
    const ScopedEnv u {"GTOPT_UNIT_DIALECTS_FILE", bad.string()};
    const auto names = load_names_registry();
    CHECK_FALSE(names.source_path().has_value());
    CHECK(names.canonical_for("annual_loss").value_or("")
          == "annual_standing_loss");
    CHECK_FALSE(load_unit_registry().source_path().has_value());
  }
}

TEST_CASE("Dialects — compiled-in copies match the shipped files")  // NOLINT
{
  const std::filesystem::path share {GTOPT_TEST_SHARE_DIR};
  const auto slurp = [](const std::filesystem::path& file)
  {
    std::ifstream in {file, std::ios::binary};
    REQUIRE(in.good());
    return std::string {std::istreambuf_iterator<char> {in},
                        std::istreambuf_iterator<char> {}};
  };
  CHECK(slurp(share / "naming_dialects.json") == kBuiltinNamesJson);
  CHECK(slurp(share / "unit_dialects.json") == kBuiltinUnitsJson);
}
