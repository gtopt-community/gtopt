/**
 * @file      build_info.cpp
 * @brief     Version and source revision of the running gtopt build
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <gtopt/build_info.hpp>
#include <gtopt/git_revision.hpp>
#include <gtopt/version.hpp>

namespace gtopt
{

BuildInfo build_info() noexcept
{
  return {
      .version = GTOPT_VERSION,
      .git_revision = GTOPT_GIT_REVISION,
  };
}

}  // namespace gtopt
