// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <doctest/doctest.h>
#include <gtopt/build_info.hpp>
#include <gtopt/version.hpp>

using namespace gtopt;

TEST_CASE("format_version_string")  // NOLINT
{
  SUBCASE("clean build of the exact release tag prints the version only")
  {
    CHECK(format_version_string("1.1.0", "v1.1.0") == "1.1.0");
  }

  SUBCASE("commits after the tag append the describe output")
  {
    CHECK(format_version_string("1.1.0", "v1.1.0-20-g5a4fd2ef8")
          == "1.1.0 (v1.1.0-20-g5a4fd2ef8)");
  }

  SUBCASE("dirty tree is reported, even on the exact tag")
  {
    CHECK(format_version_string("1.1.0", "v1.1.0-dirty")
          == "1.1.0 (v1.1.0-dirty)");
    CHECK(format_version_string("1.1.0", "v1.1.0-20-g5a4fd2ef8-dirty")
          == "1.1.0 (v1.1.0-20-g5a4fd2ef8-dirty)");
  }

  SUBCASE("no git at build time falls back to the version")
  {
    CHECK(format_version_string("1.1.0", "unknown") == "1.1.0");
    CHECK(format_version_string("1.1.0", "") == "1.1.0");
  }

  SUBCASE("tag that does not match the version prints both")
  {
    CHECK(format_version_string("1.2.0-dev", "v1.1.0-23-gabc")
          == "1.2.0-dev (v1.1.0-23-gabc)");
    CHECK(format_version_string("1.2.0", "v1.1.0") == "1.2.0 (v1.1.0)");
    CHECK(format_version_string("1.1.0", "v1.1.00") == "1.1.0 (v1.1.00)");
    CHECK(format_version_string("1.1.0", "1.1.0") == "1.1.0 (1.1.0)");
  }

  SUBCASE("describe without a reachable tag is a bare hash")
  {
    CHECK(format_version_string("1.1.0", "5a4fd2ef8abc")
          == "1.1.0 (5a4fd2ef8abc)");
  }
}

TEST_CASE("build_info reports this build")  // NOLINT
{
  const auto info = build_info();
  CHECK(info.version == GTOPT_VERSION);
  CHECK_FALSE(info.git_revision.empty());
  const auto text = info.version_string();
  CHECK(text.starts_with(GTOPT_VERSION));
  CHECK(text == format_version_string(info.version, info.git_revision));
}
