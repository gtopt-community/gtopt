/**
 * @file      lp_fingerprint.cpp
 * @brief     LP structural fingerprint computation and JSON output
 * @date      Mon Apr  7 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <array>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <format>
#include <fstream>
#include <set>
#include <string_view>

#include <gtopt/as_label.hpp>
#include <gtopt/lp_fingerprint.hpp>
#include <gtopt/sha256.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

// ── Context type name mapping
// ─────────────────────────────────────────────────

auto context_type_name(const LpContext& context) -> std::string_view
{
  return std::visit(
      []<typename T>(const T& /*unused*/) -> std::string_view
      {
        if constexpr (std::same_as<T, std::monostate>) {
          return "monostate";
        } else if constexpr (std::same_as<T, StageContext>) {
          return "StageContext";
        } else if constexpr (std::same_as<T, BlockContext>) {
          return "BlockContext";
        } else if constexpr (std::same_as<T, BlockExContext>) {
          return "BlockExContext";
        } else if constexpr (std::same_as<T, ScenePhaseContext>) {
          return "ScenePhaseContext";
        } else if constexpr (std::same_as<T, PhaseContext>) {
          return "PhaseContext";
        } else if constexpr (std::same_as<T, IterationContext>) {
          return "IterationContext";
        } else if constexpr (std::same_as<T, ApertureContext>) {
          return "ApertureContext";
        } else {
          return "unknown";
        }
      },
      context);
}

namespace
{

/// Compute SHA-256 hash of a sorted template vector.
auto hash_template(const std::vector<FingerprintEntry>& entries) -> std::string
{
  std::string data;
  for (const auto& e : entries) {
    data += e.class_name;
    data += '\0';
    data += e.variable_name;
    data += '\0';
    data += e.context_type;
    data += '\n';
  }
  return sha256_hex(data);
}

}  // namespace

// ── Fingerprint computation
// ───────────────────────────────────────────────────

auto compute_lp_fingerprint(const std::vector<SparseCol>& cols,
                            const std::vector<SparseRow>& rows) -> LpFingerprint
{
  LpFingerprint fingerprint;

  // -- Collect column template and stats --
  std::set<FingerprintEntry> col_set;
  fingerprint.stats.total_cols = cols.size();

  for (const auto& col : cols) {
    if (col.class_name.empty()
        || std::holds_alternative<std::monostate>(col.context))
    {
      ++fingerprint.untracked_cols;
      continue;
    }
    col_set.insert(FingerprintEntry {
        .class_name = std::string(col.class_name),
        .variable_name = std::string(col.variable_name),
        .context_type = std::string(context_type_name(col.context)),
    });
    ++fingerprint.stats.cols_by_class[std::string(col.class_name)];
  }
  fingerprint.col_template.assign(col_set.begin(), col_set.end());

  // -- Collect row template and stats --
  std::set<FingerprintEntry> row_set;
  fingerprint.stats.total_rows = rows.size();

  for (const auto& row : rows) {
    if (row.class_name.empty()
        || std::holds_alternative<std::monostate>(row.context))
    {
      ++fingerprint.untracked_rows;
      continue;
    }
    row_set.insert(FingerprintEntry {
        .class_name = std::string(row.class_name),
        .variable_name = std::string(row.constraint_name),
        .context_type = std::string(context_type_name(row.context)),
    });
    ++fingerprint.stats.rows_by_class[std::string(row.class_name)];
  }
  fingerprint.row_template.assign(row_set.begin(), row_set.end());

  // -- Compute hashes --
  fingerprint.col_hash = hash_template(fingerprint.col_template);
  fingerprint.row_hash = hash_template(fingerprint.row_template);
  fingerprint.structural_hash =
      sha256_hex(fingerprint.col_hash + fingerprint.row_hash);

  return fingerprint;
}

// ── JSON output
// ───────────────────────────────────────────────────────────────

namespace
{

/// Escape a string for JSON output (handles quotes and backslashes).
auto json_escape(std::string_view s) -> std::string
{
  std::string result;
  result.reserve(s.size());
  for (const char c : s) {
    if (c == '"' || c == '\\') {
      result += '\\';
    }
    result += c;
  }
  return result;
}

}  // namespace

void write_lp_fingerprint(const LpFingerprint& fingerprint,
                          const std::string& filepath,
                          int scene_uid,
                          int phase_uid)
{
  // Ensure parent directory exists
  if (auto parent = std::filesystem::path(filepath).parent_path();
      !parent.empty())
  {
    std::filesystem::create_directories(parent);
  }

  std::ofstream out(filepath);

  out << "{\n";
  out << "  \"version\": 1,\n";
  out << "  \"scene_uid\": " << as_label(scene_uid) << ",\n";
  out << "  \"phase_uid\": " << as_label(phase_uid) << ",\n";

  // -- structural section --
  out << "  \"structural\": {\n";

  // columns
  out << "    \"columns\": {\n";
  out << "      \"template\": [\n";
  for (const auto& [i, e] : enumerate(fingerprint.col_template)) {
    out << std::format(
        "        {{\"class\": \"{}\", \"variable\": \"{}\", "
        "\"context_type\": \"{}\"}}",
        json_escape(e.class_name),
        json_escape(e.variable_name),
        json_escape(e.context_type));
    out << (i + 1 < fingerprint.col_template.size() ? ",\n" : "\n");
  }
  out << "      ],\n";
  out << std::format("      \"untracked_count\": {},\n",
                     fingerprint.untracked_cols);
  out << std::format("      \"hash\": \"{}\"\n", fingerprint.col_hash);
  out << "    },\n";

  // rows
  out << "    \"rows\": {\n";
  out << "      \"template\": [\n";
  for (const auto& [i, e] : enumerate(fingerprint.row_template)) {
    out << std::format(
        "        {{\"class\": \"{}\", \"constraint\": \"{}\", "
        "\"context_type\": \"{}\"}}",
        json_escape(e.class_name),
        json_escape(e.variable_name),
        json_escape(e.context_type));
    out << (i + 1 < fingerprint.row_template.size() ? ",\n" : "\n");
  }
  out << "      ],\n";
  out << std::format("      \"untracked_count\": {},\n",
                     fingerprint.untracked_rows);
  out << std::format("      \"hash\": \"{}\"\n", fingerprint.row_hash);
  out << "    },\n";

  out << std::format("    \"hash\": \"{}\"\n", fingerprint.structural_hash);
  out << "  },\n";

  // -- stats section --
  out << "  \"stats\": {\n";
  out << std::format("    \"total_cols\": {},\n", fingerprint.stats.total_cols);
  out << std::format("    \"total_rows\": {},\n", fingerprint.stats.total_rows);

  // cols_by_class
  out << "    \"cols_by_class\": {\n";
  {
    size_t i = 0;
    for (const auto& [cls, count] : fingerprint.stats.cols_by_class) {
      out << std::format("      \"{}\": {}", json_escape(cls), count);
      out << (++i < fingerprint.stats.cols_by_class.size() ? ",\n" : "\n");
    }
  }
  out << "    },\n";

  // rows_by_class
  out << "    \"rows_by_class\": {\n";
  {
    size_t i = 0;
    for (const auto& [cls, count] : fingerprint.stats.rows_by_class) {
      out << std::format("      \"{}\": {}", json_escape(cls), count);
      out << (++i < fingerprint.stats.rows_by_class.size() ? ",\n" : "\n");
    }
  }
  out << "    }\n";

  out << "  }\n";
  out << "}\n";
}

}  // namespace gtopt
