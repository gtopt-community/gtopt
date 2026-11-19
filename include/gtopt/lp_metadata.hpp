/**
 * @file      lp_metadata.hpp
 * @brief     Machine-readable JSON sidecar describing a written LP file
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * When `--lp-file` writes an LP file and `lp_metadata` is on, a companion
 * `<stem>.meta.json` lists every LP column and row in LP order with the
 * structured label gtopt keeps in memory (class, variable/constraint name,
 * uid, context), integrality, the scale factors needed to map LP values
 * back to physical units, the objective scale, and the per-block weights.
 * See `docs/lp-metadata.md` for the schema (`gtopt-lp-metadata/1`).
 */

#pragma once

#include <string>

namespace gtopt
{

class SystemLP;

/// Schema identifier written in the `"schema"` field of every sidecar.
inline constexpr const char* lp_metadata_schema = "gtopt-lp-metadata/1";

/// Returns the sidecar path for an LP file path: a trailing ".lp" or ".mps"
/// is replaced by ".meta.json" (otherwise ".meta.json" is appended).
[[nodiscard]] std::string lp_metadata_path(const std::string& lp_path);

/**
 * @brief Writes the metadata sidecar for the LP of one (scene, phase) cell.
 *
 * Streams the JSON (no in-memory DOM).  Labels are synthesised through
 * `LinearInterface::generate_labels_from_maps`, i.e. exactly the names the
 * `.lp` file carries.
 *
 * @param system   The cell whose LP was written.
 * @param lp_path  Path of the `.lp` file that was written for it.
 * @return Path of the written sidecar.
 * @throws std::runtime_error if the sidecar cannot be opened or written.
 */
std::string write_lp_metadata(const SystemLP& system,
                              const std::string& lp_path);

}  // namespace gtopt
