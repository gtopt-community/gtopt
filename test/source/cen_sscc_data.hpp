// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      cen_sscc_data.hpp
 * @brief     Reader for the CEN SSCC CPF@10s reference data (local cache)
 * @date      2026-11-23
 *
 * Operator data is not committed: `tools/fetch_cen_sscc_cpf.py` downloads
 * the public CEN documents (URLs in that script) and writes
 *   cpf10s_planes_<year>.csv   k, alpha, beta, gamma, delta   (relation B.2)
 *   ecfydr_cpf10s_<year>.csv   pot_desc_mw, inertia_gvas, gen_mw, req_mw
 * Search order: $GTOPT_CEN_SSCC_DIR, $GTOPT_CASES_DIR/chile/reference/
 * cen_sscc, $XDG_CACHE_HOME/gtopt/cen_sscc, ~/.cache/gtopt/cen_sscc.
 * A test that needs a missing file reports it with MESSAGE and returns.
 */

#pragma once

#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace gtopt::test_cen
{

/// One (B.2) plane: α [MW], β [MW/GW], γ [MW/GVAs], δ [MW/MW].
using CenPlane = std::array<double, 4>;

/// One ECFyDR table point.
struct CenEcfPoint
{
  double pot_desc_mw {};
  double inertia_gvas {};
  double gen_mw {};
  double req_mw {};
};

inline constexpr std::string_view kCenFetchHint =
    "run `python tools/fetch_cen_sscc_cpf.py` (downloads the public CEN "
    "SSCC documents) or set GTOPT_CEN_SSCC_DIR";

[[nodiscard]] inline std::optional<std::filesystem::path> cen_sscc_file(
    std::string_view name)
{
  std::vector<std::filesystem::path> dirs;
  if (const char* d = std::getenv("GTOPT_CEN_SSCC_DIR"); d != nullptr) {
    dirs.emplace_back(d);
  }
  if (const char* d = std::getenv("GTOPT_CASES_DIR"); d != nullptr) {
    dirs.emplace_back(std::filesystem::path {d} / "chile" / "reference"
                      / "cen_sscc");
  }
  if (const char* d = std::getenv("XDG_CACHE_HOME"); d != nullptr) {
    dirs.emplace_back(std::filesystem::path {d} / "gtopt" / "cen_sscc");
  }
  if (const char* d = std::getenv("HOME"); d != nullptr) {
    dirs.emplace_back(std::filesystem::path {d} / ".cache" / "gtopt"
                      / "cen_sscc");
  }
  for (const auto& dir : dirs) {
    auto p = dir / name;
    std::error_code ec;
    if (std::filesystem::is_regular_file(p, ec)) {
      return p;
    }
  }
  return std::nullopt;
}

/// Numeric rows of a CSV with a header line; empty on any parse error.
[[nodiscard]] inline std::vector<std::vector<double>> cen_read_csv(
    const std::filesystem::path& path, std::size_t ncols)
{
  std::vector<std::vector<double>> rows;
  std::ifstream in(path);
  std::string line;
  if (!std::getline(in, line)) {
    return {};
  }
  while (std::getline(in, line)) {
    if (line.empty()) {
      continue;
    }
    std::vector<double> row;
    std::stringstream ss(line);
    std::string cell;
    while (std::getline(ss, cell, ',')) {
      try {
        row.push_back(std::stod(cell));
      } catch (const std::exception&) {
        return {};
      }
    }
    if (row.size() != ncols) {
      return {};
    }
    rows.push_back(std::move(row));
  }
  return rows;
}

/// The (B.2) CPF@10s planes of the given ISSCC year (2024, 2025, 2026).
[[nodiscard]] inline std::optional<std::vector<CenPlane>> cen_planes(
    std::string_view year)
{
  const auto path = cen_sscc_file(std::string {"cpf10s_planes_"}
                                  + std::string {year} + ".csv");
  if (!path) {
    return std::nullopt;
  }
  std::vector<CenPlane> planes;
  for (const auto& r : cen_read_csv(*path, 5)) {
    planes.push_back({r[1], r[2], r[3], r[4]});
  }
  if (planes.empty()) {
    return std::nullopt;
  }
  return planes;
}

/// The ECFyDR CPF@10s reference table points of the given informe year.
[[nodiscard]] inline std::optional<std::vector<CenEcfPoint>> cen_ecfydr(
    std::string_view year)
{
  const auto path = cen_sscc_file(std::string {"ecfydr_cpf10s_"}
                                  + std::string {year} + ".csv");
  if (!path) {
    return std::nullopt;
  }
  std::vector<CenEcfPoint> pts;
  for (const auto& r : cen_read_csv(*path, 4)) {
    pts.push_back({
        .pot_desc_mw = r[0],
        .inertia_gvas = r[1],
        .gen_mw = r[2],
        .req_mw = r[3],
    });
  }
  if (pts.empty()) {
    return std::nullopt;
  }
  return pts;
}

}  // namespace gtopt::test_cen
