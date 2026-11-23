/**
 * @file      lp_scales.hpp
 * @brief     Machine-readable sidecar describing a written LP/MPS file
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * A written `.lp`/`.mps` file already carries the whole model: matrix,
 * bounds, integrality, names and the objective constant.  What it cannot
 * carry is how gtopt *scaled* that model, so a solution read back from an
 * external solver cannot be mapped to physical units without:
 *
 *   - the objective scale (`scale_objective`),
 *   - the per-block weights (`cost_factor`) folded into every cost,
 *   - the columns and rows whose scale factor is not 1.
 *
 * `LpScales` holds the header of that (plus the objective constant,
 * redundant with the LP but convenient, the gtopt build that wrote it and
 * the numerical ranges of the model), is built by `make_lp_scales()` and is
 * serialised with daw::json (`json/json_lp_scales.hpp`).  The per-column /
 * per-row scale factors (`LpScalesDetail`, `full` level) can reach
 * millions of entries on a real case, so they go to two Parquet tables
 * next to the JSON, which only points to them.  See `docs/lp-scales.md`
 * for the schema (`gtopt-lp-scales/1`).
 */

#pragma once

#include <expected>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/basic_types.hpp>
#include <gtopt/error.hpp>
#include <gtopt/lp_context.hpp>
#include <gtopt/planning_enums.hpp>

namespace gtopt
{

class SystemLP;

/// Schema identifier written in the `"schema"` field of every sidecar.
inline constexpr std::string_view lp_scales_schema = "gtopt-lp-scales/1";

/// Which gtopt wrote the sidecar (audit trail).
struct LpScalesBuild
{
  std::string version;
  std::string git_revision;
};

/// Objective scaling: `obj_physical = obj_LP * scale_objective`.
struct LpScalesObjective
{
  Real scale_objective {1.0};
  Real constant {0.0};  ///< Objective constant, physical units.
  std::string sense {"min"};
};

/// Weights of one (scenario, stage, block) of the cell.
struct LpScalesBlock
{
  Uid scenario {unknown_uid};
  Uid stage {unknown_uid};
  Uid block {unknown_uid};
  Real duration {0.0};  ///< Hours.
  Real probability {0.0};
  Real discount {0.0};
  Real cost_factor {0.0};  ///< probability * discount * duration.
};

/// Weights of one (scenario, stage) of the cell, for stage-level entries
/// (investment, capacity, stage constraints).
struct LpScalesStage
{
  Uid scenario {unknown_uid};
  Uid stage {unknown_uid};
  Real duration {0.0};  ///< Hours (sum of the stage's blocks).
  Real probability {0.0};
  Real discount {0.0};
  Real cost_factor {0.0};  ///< probability * discount * duration.
};

/// Structured LP context of a column; only the members that apply to
/// `type` are set (`stage`, `block`, `block_ex`, `scene_phase`, `phase`,
/// `iteration` or `aperture`).
struct LpScalesContext
{
  std::string type;
  OptUid scenario {};
  OptUid stage {};
  OptUid block {};
  OptUid scene {};
  OptUid phase {};
  OptUid element {};
  OptUid iteration {};
  OptUid aperture {};
  OptInt extra {};
};

/// Maps the in-memory `LpContext` variant to its sidecar form (`type`
/// tag + the uids of that alternative); `std::monostate` maps to
/// `std::nullopt` (context-free column).
[[nodiscard]] auto to_scales_context(const LpContext& context)
    -> std::optional<LpScalesContext>;

/// A column whose scale factor is not 1: `x_physical = scale * x_LP`.
struct LpScaledColumn
{
  Index index {0};  ///< Column index in the LP.
  std::string name;  ///< Name written in the LP/MPS file.
  std::string class_name;
  std::string variable;
  Uid uid {unknown_uid};
  std::optional<LpScalesContext> context {};
  Real scale {1.0};
  OptName unit {};  ///< Physical unit of `x_physical`, when known.
};

/// A row whose scale factor is not 1: `dual_physical = dual_LP *
/// scale_objective / scale`.
struct LpScaledRow
{
  Index index {0};  ///< Row index in the LP.
  std::string name;  ///< Name written in the LP/MPS file.
  Real scale {1.0};
};

/// Per-column / per-row scale factors of one cell (`full` level), written
/// as Parquet tables by `write_lp_scales_tables()`.
struct LpScalesDetail
{
  std::vector<LpScaledColumn> columns {};
  std::vector<LpScaledRow> rows {};
};

/// Where the `full`-level Parquet tables of a sidecar are (file names,
/// relative to the sidecar) and how many entries they hold.
struct LpScalesTables
{
  std::string columns;  ///< `<stem>.scales_cols.parquet`
  std::string rows;  ///< `<stem>.scales_rows.parquet`
  Size num_scaled_columns {0};
  Size num_scaled_rows {0};
};

/// `min |v|`, `max |v|` over the finite non-zero values of a set, and the
/// number of decades they span (`log10(max / min)`).
struct LpScalesRange
{
  Real min_abs {0.0};
  Real max_abs {0.0};
  Real log10_ratio {0.0};
};

/// Matrix coefficient range of one constraint family.
struct LpScalesRowType
{
  std::string type;
  Size count {0};
  Size nnz {0};
  Real min_abs {0.0};
  Real max_abs {0.0};
};

/// Numerical figures of merit of the model as handed to the solver
/// (after gtopt's scaling and row equilibration).
struct LpScalesNumerics
{
  std::string equilibration_method;
  Size nnz {0};
  std::optional<LpScalesRange> matrix {};  ///< Needs LP stats (default on).
  OptName matrix_min_col {};
  OptName matrix_max_col {};
  std::optional<LpScalesRange> objective {};
  std::optional<LpScalesRange> bounds {};
  std::optional<LpScalesRange> rhs {};
  std::optional<LpScalesRange> col_scales {};
  std::optional<LpScalesRange> row_scales {};
  std::vector<LpScalesRowType> row_types {};
};

/// Everything needed to read an exported LP back in physical units.
struct LpScales
{
  std::string schema {lp_scales_schema};
  std::string level {"full"};  ///< `summary` or `full`.
  LpScalesBuild gtopt {};
  std::string lp_file;  ///< File name (no directory) of the LP/MPS file.
  Uid scene_uid {unknown_uid};
  Uid phase_uid {unknown_uid};
  Index num_cols {0};
  Index num_rows {0};
  LpScalesObjective objective {};
  std::vector<LpScalesBlock> blocks {};
  std::vector<LpScalesStage> stages {};
  /// The Parquet tables with the scaled columns and rows (`full` only).
  std::optional<LpScalesTables> tables {};
  LpScalesNumerics numerics {};
};

/// Suffixes of the sidecar files written next to a model file.
inline constexpr std::string_view lp_scales_json_suffix = ".scales.json";
inline constexpr std::string_view lp_scales_cols_suffix =
    ".scales_cols.parquet";
inline constexpr std::string_view lp_scales_rows_suffix =
    ".scales_rows.parquet";

/// Sidecar path for a written model file: compression (`.gz`, `.zst`,
/// `.lz4`, `.bz2`, `.xz`) and model (`.lp`, `.mps`) extensions are replaced
/// by @p suffix (otherwise @p suffix is appended).
[[nodiscard]] std::string lp_scales_path(
    std::string_view lp_path, std::string_view suffix = lp_scales_json_suffix);

/// Collects the scale factors of the LP held by one (scene, phase) cell.
/// @param lp_path  Path of the written model file (only its name is kept).
/// @param level    `full` also points to the Parquet tables (`tables`).
[[nodiscard]] LpScales make_lp_scales(
    const SystemLP& system,
    std::string_view lp_path,
    LpScalesLevel level = LpScalesLevel::full);

/// The columns and rows of the cell whose scale is not 1, with the names
/// written in the model file.
[[nodiscard]] LpScalesDetail make_lp_scales_detail(const SystemLP& system);

/// Writes @p detail as the two Parquet tables (zstd), with `scene_uid` /
/// `phase_uid` columns so the tables of several cells can be concatenated.
[[nodiscard]] std::expected<void, Error> write_lp_scales_tables(
    const LpScalesDetail& detail,
    Uid scene_uid,
    Uid phase_uid,
    std::string_view columns_path,
    std::string_view rows_path);

/// Serialises @p scales to JSON text (daw::json).
[[nodiscard]] std::string lp_scales_to_json(const LpScales& scales);

/// Builds and writes the sidecar for the model file @p lp_path.
/// @param compression  Codec for the sidecar (`--lp-compression`); empty,
///                     `none` or `uncompressed` keep the plain `.json`.
/// Failures (unwritable path, full disk, ...) are returned, not thrown.
/// At the `full` level the scaled columns and rows also go to
/// `<stem>.scales_cols.parquet` / `<stem>.scales_rows.parquet` (Parquet
/// compresses internally; @p compression only applies to the JSON).
/// @return Path of the written sidecar (with the codec extension when
///         compressed).
[[nodiscard]] std::expected<std::string, Error> write_lp_scales(
    const SystemLP& system,
    std::string_view lp_path,
    LpScalesLevel level = LpScalesLevel::full,
    std::string_view compression = {});

}  // namespace gtopt
