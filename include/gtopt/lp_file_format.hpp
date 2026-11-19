// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      lp_file_format.hpp
 * @brief     File format selector for LP model dumps (`--lp-file`)
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

namespace gtopt
{

/// File format used when an assembled LP model is written to disk.
enum class LpFileFormat : uint8_t
{
  lp = 0,  ///< CPLEX LP format (default), extension `.lp`
  mps = 1,  ///< MPS format, extension `.mps`
};

/// File extension (with leading dot) written for `format`.
[[nodiscard]] constexpr std::string_view lp_file_extension(
    LpFileFormat format) noexcept
{
  return format == LpFileFormat::mps ? ".mps" : ".lp";
}

/// The other format (used for the fallback of diagnostic dumps).
[[nodiscard]] constexpr LpFileFormat other_lp_file_format(
    LpFileFormat format) noexcept
{
  return format == LpFileFormat::mps ? LpFileFormat::lp : LpFileFormat::mps;
}

/// Name of the format as accepted by `--lp-format` ("lp" / "mps").
[[nodiscard]] constexpr std::string_view lp_file_format_name(
    LpFileFormat format) noexcept
{
  return format == LpFileFormat::mps ? "mps" : "lp";
}

/// What LinearInterface::write_lp does when the backend cannot write the
/// requested format.
enum class LpFormatFallback : uint8_t
{
  /// Fail with an error naming the backend.  Used when the user asked for
  /// the format explicitly (`--lp-file`, `--lp-format`).
  strict = 0,
  /// Write the other format instead and log a warning.  Used by the
  /// diagnostic dumps (`--lp-debug`, error / kappa LPs), where having a
  /// file matters more than its format.
  other_format = 1,
};

/// Parse "lp" / "mps" (case-sensitive); nullopt on anything else.
[[nodiscard]] constexpr std::optional<LpFileFormat> parse_lp_file_format(
    std::string_view name) noexcept
{
  if (name == "lp") {
    return LpFileFormat::lp;
  }
  if (name == "mps") {
    return LpFileFormat::mps;
  }
  return std::nullopt;
}

/// A user-supplied `--lp-file` value split into output stem and format.
struct LpFileTarget
{
  std::string stem {};
  LpFileFormat format {LpFileFormat::lp};
};

/// Split a `--lp-file` value.  A trailing `.mps` selects MPS (overriding
/// `requested`); a trailing `.lp` is stripped and `requested` is kept.
/// Backends append the extension themselves, so it is always removed.
[[nodiscard]] inline LpFileTarget split_lp_file_target(
    std::string filename, LpFileFormat requested = LpFileFormat::lp)
{
  if (filename.ends_with(".mps")) {
    filename.erase(filename.size() - 4);
    return {.stem = std::move(filename), .format = LpFileFormat::mps};
  }
  if (filename.ends_with(".lp")) {
    filename.erase(filename.size() - 3);
  }
  return {.stem = std::move(filename), .format = requested};
}

}  // namespace gtopt
