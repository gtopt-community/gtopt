/**
 * @file      build_info.cpp
 * @brief     Version and source revision of the running gtopt build
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <format>

#include <gtopt/build_info.hpp>
#include <gtopt/git_revision.hpp>
#include <gtopt/version.hpp>

namespace gtopt
{

std::string format_version_string(std::string_view version,
                                  std::string_view git_revision)
{
  const bool no_revision = git_revision.empty() || git_revision == "unknown";
  const bool exact_tag = git_revision.size() == version.size() + 1
      && git_revision.starts_with('v') && git_revision.substr(1) == version;
  if (no_revision || exact_tag) {
    return std::string(version);
  }
  return std::format("{} ({})", version, git_revision);
}

BuildInfo build_info() noexcept
{
  return {
      .version = GTOPT_VERSION,
      .git_revision = GTOPT_GIT_REVISION,
      .build_tag = GTOPT_BUILD_TAG,
  };
}

}  // namespace gtopt
