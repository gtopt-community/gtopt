/**
 * @file      lp_fingerprint.hpp
 * @brief     LP structural fingerprint for formulation integrity verification
 * @date      Mon Apr  7 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Provides a structural "fingerprint" of an LP formulation that captures
 * which types of variables and constraints exist (class, name, context type)
 * without depending on element counts.  Two systems with 100 generators or
 * 20 generators produce the same structural template.
 *
 * The fingerprint has two sections:
 * - **structural**: sorted, deduplicated template entries + SHA-256 hash
 *   (used for regression comparison)
 * - **stats**: per-class column/row counts (informational only)
 */

#pragma once

#include <map>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/lp_context.hpp>
#include <gtopt/sha256.hpp>
#include <gtopt/sparse_col.hpp>
#include <gtopt/sparse_row.hpp>
#include <gtopt/stable_vector.hpp>

namespace gtopt
{

/// A single entry in the structural template: identifies one type of
/// LP column or row by its class, variable/constraint name, and context
/// granularity level.
struct FingerprintEntry
{
  std::string class_name;
  std::string
      variable_name;  ///< variable_name (cols) or constraint_name (rows)
  std::string context_type;  ///< "StageContext", "BlockContext", etc.

  auto operator<=>(const FingerprintEntry&) const = default;
};

/// Per-class column/row count statistics (informational, not compared).
struct FingerprintStats
{
  size_t total_cols {};
  size_t total_rows {};
  std::map<std::string, size_t> cols_by_class;
  std::map<std::string, size_t> rows_by_class;
};

/// Complete LP fingerprint for one LP phase.
struct LpFingerprint
{
  // ── Structural (used for comparison) ────────────────────────────────────
  std::vector<FingerprintEntry> col_template;  ///< sorted, unique
  std::vector<FingerprintEntry> row_template;  ///< sorted, unique
  size_t untracked_cols {};  ///< cols with empty class_name or monostate ctx
  size_t untracked_rows {};  ///< rows with empty class_name or monostate ctx
  std::string col_hash;  ///< SHA-256 of col_template
  std::string row_hash;  ///< SHA-256 of row_template
  std::string structural_hash;  ///< SHA-256 of col_hash + row_hash

  // ── Stats (informational only) ──────────────────────────────────────────
  FingerprintStats stats;
};

// sha256_hex() (FIPS 180-4) lives in <gtopt/sha256.hpp>, included below
// for the callers that used it from here.

/// Map an LpContext variant to its human-readable type name.
/// Returns "monostate" for context-free entries.
[[nodiscard]] auto context_type_name(const LpContext& context)
    -> std::string_view;

/// Compute the LP fingerprint from raw LP columns and rows.
/// @param cols  Vector of SparseCol (from LinearProblem)
/// @param rows  Vector of SparseRow (from LinearProblem)
[[nodiscard]] auto compute_lp_fingerprint(const std::vector<SparseCol>& cols,
                                          const std::vector<SparseRow>& rows)
    -> LpFingerprint;

/// Same, from `LinearProblem`'s stable row / column storage.
[[nodiscard]] auto compute_lp_fingerprint(const StableVector<SparseCol>& cols,
                                          const StableVector<SparseRow>& rows)
    -> LpFingerprint;

/// Write an LP fingerprint to a JSON file.
/// @param fingerprint  The fingerprint to serialize
/// @param filepath     Output file path (will be created/overwritten)
/// @param scene_uid    Scene UID for the JSON metadata
/// @param phase_uid    Phase UID for the JSON metadata
void write_lp_fingerprint(const LpFingerprint& fingerprint,
                          const std::string& filepath,
                          int scene_uid = 0,
                          int phase_uid = 0);

}  // namespace gtopt
