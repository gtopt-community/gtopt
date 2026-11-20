/**
 * @file      build_info.hpp
 * @brief     Version and source revision of the running gtopt build
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * For audit trails in generated files (LP scales sidecar, fingerprints,
 * logs): which gtopt produced them.
 */

#pragma once

#include <string_view>

namespace gtopt
{

struct BuildInfo
{
  std::string_view version;  ///< CMake project version, e.g. "1.0".
  /// `git describe --always --dirty` of the source tree at build time,
  /// or "unknown" when built outside a git checkout.
  std::string_view git_revision;
};

[[nodiscard]] BuildInfo build_info() noexcept;

}  // namespace gtopt
