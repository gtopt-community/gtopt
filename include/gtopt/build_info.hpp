/**
 * @file      build_info.hpp
 * @brief     Version and source revision of the running gtopt build
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * For audit trails in generated files (LP scales sidecar, solver status,
 * fingerprints, logs): which gtopt produced them.
 */

#pragma once

#include <string>
#include <string_view>

namespace gtopt
{

/// Human-readable version of a build: `<version> (<git_revision>)`.
///
/// - `git_revision` empty or `"unknown"` (built outside a git checkout,
///   e.g. from a source tarball) -> `<version>` alone.
/// - `git_revision == "v" + version` (a clean build of exactly the
///   release tag) -> `<version>` alone.
/// - anything else -> both, e.g. `1.1.0 (v1.1.0-20-g5a4fd2ef8)`,
///   `1.1.0 (v1.1.0-dirty)`, or `1.2.0-dev (v1.1.0-23-gabc)` when the
///   last tag does not match the version (a development build).
[[nodiscard]] std::string format_version_string(std::string_view version,
                                                std::string_view git_revision);

struct BuildInfo
{
  /// CMake project version plus `GTOPT_VERSION_SUFFIX`, e.g. "1.1.0" or
  /// "1.2.0-dev".
  std::string_view version;
  /// `git describe --always --dirty` of the source tree at build time,
  /// or "unknown" when built outside a git checkout.
  std::string_view git_revision;

  /// `format_version_string(version, git_revision)`.
  [[nodiscard]] std::string version_string() const
  {
    return format_version_string(version, git_revision);
  }
};

[[nodiscard]] BuildInfo build_info() noexcept;

}  // namespace gtopt
