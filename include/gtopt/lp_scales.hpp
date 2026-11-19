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
 * `LpScales` holds exactly that (plus the objective constant, redundant
 * with the LP but convenient, the gtopt build that wrote it and the
 * numerical ranges of the model), is built by `make_lp_scales()` and is
 * serialised with daw::json (`json/json_lp_scales.hpp`).  See
 * `docs/lp-scales.md` for the schema (`gtopt-lp-scales/1`).
 */

#pragma once

#include <expected>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/basic_types.hpp>
#include <gtopt/error.hpp>

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
  LpScalesBuild gtopt {};
  std::string lp_file;  ///< File name (no directory) of the LP/MPS file.
  Uid scene_uid {unknown_uid};
  Uid phase_uid {unknown_uid};
  Index num_cols {0};
  Index num_rows {0};
  LpScalesObjective objective {};
  std::vector<LpScalesBlock> blocks {};
  std::vector<LpScalesStage> stages {};
  std::vector<LpScaledColumn> scaled_columns {};
  /// Scale of every row in LP order (`dual_physical = dual_LP *
  /// scale_objective / row_scale`); empty when every row has scale 1.
  std::vector<Real> row_scales {};
  LpScalesNumerics numerics {};
};

/// Sidecar path for a written model file: compression (`.gz`, `.zst`,
/// `.lz4`, `.bz2`, `.xz`) and model (`.lp`, `.mps`) extensions are replaced
/// by `.scales.json` (otherwise `.scales.json` is appended).
[[nodiscard]] std::string lp_scales_path(std::string_view lp_path);

/// Collects the scale factors of the LP held by one (scene, phase) cell.
/// @param lp_path  Path of the written model file (only its name is kept).
[[nodiscard]] LpScales make_lp_scales(const SystemLP& system,
                                      std::string_view lp_path);

/// Serialises @p scales to JSON text (daw::json).
[[nodiscard]] std::string lp_scales_to_json(const LpScales& scales);

/// Builds and writes the sidecar for the model file @p lp_path.
/// Failures (unwritable path, full disk, ...) are returned, not thrown.
/// @return Path of the written sidecar.
[[nodiscard]] std::expected<std::string, Error> write_lp_scales(
    const SystemLP& system, std::string_view lp_path);

}  // namespace gtopt
