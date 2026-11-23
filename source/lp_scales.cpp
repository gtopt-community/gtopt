/**
 * @file      lp_scales.cpp
 * @brief     Build and write the `<stem>.scales.json` LP sidecar and its
 *            Parquet tables
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <cmath>
#include <exception>
#include <filesystem>
#include <format>
#include <fstream>
#include <limits>
#include <memory>
#include <optional>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <variant>
#include <vector>

#include <arrow/api.h>
#include <arrow/io/api.h>
#include <daw/json/daw_json_link.h>
#include <gtopt/build_info.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/enum_option.hpp>
#include <gtopt/json/json_lp_scales.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/lp_debug_writer.hpp>
#include <gtopt/lp_matrix_enums.hpp>
#include <gtopt/lp_scales.hpp>
#include <gtopt/overload.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/unit_registry.hpp>
#include <parquet/arrow/writer.h>

namespace gtopt
{

namespace
{

/// Removes from @p s the first of @p suffixes it ends with, if any.
constexpr void strip_one_suffix(std::string_view& s,
                                std::span<const std::string_view> suffixes)
{
  if (const auto it = std::ranges::find_if(
          suffixes, [s](std::string_view ext) { return s.ends_with(ext); });
      it != suffixes.end())
  {
    s.remove_suffix(it->size());
  }
}

/// Values whose magnitude reaches this are treated as infinite bounds
/// (solver conventions range from 1e20 to DBL_MAX).
constexpr double k_infinite_bound = 1e20;

/// Range of the finite, non-zero magnitudes over all of @p ranges.
template<std::ranges::input_range... Rs>
[[nodiscard]] auto range_of(Rs&&... ranges) -> std::optional<LpScalesRange>
{
  double lo = std::numeric_limits<double>::infinity();
  double hi = 0.0;
  const auto scan = [&](auto&& values)
  {
    for (const double v : values) {
      const double a = std::abs(v);
      if (a == 0.0 || !std::isfinite(a) || a >= k_infinite_bound) {
        continue;
      }
      lo = std::min(lo, a);
      hi = std::max(hi, a);
    }
  };
  (scan(std::forward<Rs>(ranges)), ...);
  if (hi == 0.0) {
    return std::nullopt;
  }
  return LpScalesRange {
      .min_abs = lo,
      .max_abs = hi,
      .log10_ratio = std::log10(hi / lo),
  };
}

/// Physical unit of an LP variable, from the `lp` dialect of the unit
/// registry (`share/gtopt/unit_dialects.json`).
[[nodiscard]] auto unit_of(std::string_view class_name,
                           std::string_view variable) -> OptName
{
  if (class_name.empty() || variable.empty()
      || class_name.size() > LPClassName::max_short_len / 2)
  {
    return std::nullopt;
  }
  const LPClassName lp_class {class_name};
  return UnitRegistry::instance()
      .unit_for(lp_class.snake_case(), variable, "lp")
      .transform([](std::string_view u) { return std::string(u); });
}

/// Numerical figures of merit of the LP as handed to the solver.
[[nodiscard]] auto make_numerics(const LinearInterface& li) -> LpScalesNumerics
{
  const auto ncols = li.get_numcols();
  const auto nrows = li.get_numrows();
  const auto col_scale = [&li](Index i)
  { return li.get_col_scale(ColIndex {i}); };
  const auto row_scale = [&li](Index i)
  { return li.get_row_scale(RowIndex {i}); };

  LpScalesNumerics numerics {
      .equilibration_method = std::string(enum_name(li.equilibration_method())),
      .nnz = li.lp_stats_nnz(),
      .objective = range_of(li.get_obj_coeff()),
      .bounds = range_of(li.get_col_low_raw(), li.get_col_upp_raw()),
      .rhs = range_of(li.get_row_low_raw(), li.get_row_upp_raw()),
      .col_scales = range_of(std::views::iota(Index {0}, ncols)
                             | std::views::transform(col_scale)),
      .row_scales = range_of(std::views::iota(Index {0}, nrows)
                             | std::views::transform(row_scale)),
  };

  // The coefficient envelope is collected by flatten() when LP stats are
  // on (the default for gtopt runs); without it the matrix is not scanned.
  if (li.lp_stats_nnz() > 0 && li.lp_stats_max_abs() > 0.0) {
    numerics.matrix =
        range_of(std::array {li.lp_stats_min_abs(), li.lp_stats_max_abs()});
    if (!li.lp_stats_min_col_name().empty()) {
      numerics.matrix_min_col = li.lp_stats_min_col_name();
    }
    if (!li.lp_stats_max_col_name().empty()) {
      numerics.matrix_max_col = li.lp_stats_max_col_name();
    }
  }
  numerics.row_types = li.lp_row_type_stats()
      | std::views::transform(
                           [](const auto& e)
                           {
                             return LpScalesRowType {
                                 .type = e.type,
                                 .count = e.count,
                                 .nnz = e.nnz,
                                 .min_abs = e.min_abs,
                                 .max_abs = e.max_abs,
                             };
                           })
      | std::ranges::to<std::vector>();
  return numerics;
}

[[nodiscard]] auto file_io_error(std::string message) -> Error
{
  return Error {
      .code = ErrorCode::FileIOError,
      .message = std::move(message),
  };
}

/// Appends @p value, or a null when it is unset.
template<typename Builder, typename T>
[[nodiscard]] auto append_opt(Builder& builder, const std::optional<T>& value)
    -> arrow::Status
{
  return value ? builder.Append(*value) : builder.AppendNull();
}

/// Writes @p table to @p path as Parquet (zstd, no statistics: the
/// tables are read whole, never filtered by min/max).  The increasing
/// `index` column is delta-encoded and the unique `name` column is not
/// dictionary-encoded: on a CEN-size case that cuts the rows table about
/// 8x.
[[nodiscard]] auto write_parquet(const arrow::Table& table,
                                 const std::string& path) -> arrow::Status
{
  ARROW_ASSIGN_OR_RAISE(auto out, arrow::io::FileOutputStream::Open(path));
  parquet::WriterProperties::Builder props;
  props.compression(parquet::Compression::ZSTD);
  props.disable_statistics();
  props.disable_write_page_index();
  props.disable_dictionary("index");
  props.disable_dictionary("name");  // unique: a dictionary only adds
  props.encoding("index", parquet::Encoding::DELTA_BINARY_PACKED);
  ARROW_RETURN_NOT_OK(parquet::arrow::WriteTable(table,
                                                 arrow::default_memory_pool(),
                                                 out,
                                                 /*chunk_size=*/1024UL * 1024UL,
                                                 props.build()));
  return out->Close();
}

[[nodiscard]] auto columns_table(const std::vector<LpScaledColumn>& columns,
                                 Uid scene_uid,
                                 Uid phase_uid)
    -> arrow::Result<std::shared_ptr<arrow::Table>>
{
  arrow::Int32Builder scene_b;
  arrow::Int32Builder phase_b;
  arrow::Int32Builder index_b;
  arrow::StringBuilder name_b;
  arrow::StringBuilder class_b;
  arrow::StringBuilder variable_b;
  arrow::Int32Builder uid_b;
  arrow::StringBuilder context_b;
  arrow::Int32Builder scenario_b;
  arrow::Int32Builder stage_b;
  arrow::Int32Builder block_b;
  arrow::Int32Builder ctx_scene_b;
  arrow::Int32Builder ctx_phase_b;
  arrow::Int32Builder element_b;
  arrow::Int32Builder iteration_b;
  arrow::Int32Builder aperture_b;
  arrow::Int32Builder extra_b;
  arrow::DoubleBuilder scale_b;
  arrow::StringBuilder unit_b;

  for (const auto& c : columns) {
    ARROW_RETURN_NOT_OK(scene_b.Append(scene_uid));
    ARROW_RETURN_NOT_OK(phase_b.Append(phase_uid));
    ARROW_RETURN_NOT_OK(index_b.Append(c.index));
    ARROW_RETURN_NOT_OK(name_b.Append(c.name));
    ARROW_RETURN_NOT_OK(class_b.Append(c.class_name));
    ARROW_RETURN_NOT_OK(variable_b.Append(c.variable));
    ARROW_RETURN_NOT_OK(uid_b.Append(c.uid));
    const auto ctx = c.context.value_or(LpScalesContext {});
    ARROW_RETURN_NOT_OK(c.context ? context_b.Append(ctx.type)
                                  : context_b.AppendNull());
    ARROW_RETURN_NOT_OK(append_opt(scenario_b, ctx.scenario));
    ARROW_RETURN_NOT_OK(append_opt(stage_b, ctx.stage));
    ARROW_RETURN_NOT_OK(append_opt(block_b, ctx.block));
    ARROW_RETURN_NOT_OK(append_opt(ctx_scene_b, ctx.scene));
    ARROW_RETURN_NOT_OK(append_opt(ctx_phase_b, ctx.phase));
    ARROW_RETURN_NOT_OK(append_opt(element_b, ctx.element));
    ARROW_RETURN_NOT_OK(append_opt(iteration_b, ctx.iteration));
    ARROW_RETURN_NOT_OK(append_opt(aperture_b, ctx.aperture));
    ARROW_RETURN_NOT_OK(append_opt(extra_b, ctx.extra));
    ARROW_RETURN_NOT_OK(scale_b.Append(c.scale));
    ARROW_RETURN_NOT_OK(append_opt(unit_b, c.unit));
  }

  std::vector<std::shared_ptr<arrow::Field>> fields;
  std::vector<std::shared_ptr<arrow::Array>> arrays;
  const auto add = [&](std::string name,
                       arrow::ArrayBuilder& builder) -> arrow::Status
  {
    ARROW_ASSIGN_OR_RAISE(auto array, builder.Finish());
    fields.push_back(arrow::field(std::move(name), array->type()));
    arrays.push_back(std::move(array));
    return arrow::Status::OK();
  };
  ARROW_RETURN_NOT_OK(add("scene_uid", scene_b));
  ARROW_RETURN_NOT_OK(add("phase_uid", phase_b));
  ARROW_RETURN_NOT_OK(add("index", index_b));
  ARROW_RETURN_NOT_OK(add("name", name_b));
  ARROW_RETURN_NOT_OK(add("class", class_b));
  ARROW_RETURN_NOT_OK(add("variable", variable_b));
  ARROW_RETURN_NOT_OK(add("uid", uid_b));
  ARROW_RETURN_NOT_OK(add("context", context_b));
  ARROW_RETURN_NOT_OK(add("scenario", scenario_b));
  ARROW_RETURN_NOT_OK(add("stage", stage_b));
  ARROW_RETURN_NOT_OK(add("block", block_b));
  ARROW_RETURN_NOT_OK(add("scene", ctx_scene_b));
  ARROW_RETURN_NOT_OK(add("phase", ctx_phase_b));
  ARROW_RETURN_NOT_OK(add("element", element_b));
  ARROW_RETURN_NOT_OK(add("iteration", iteration_b));
  ARROW_RETURN_NOT_OK(add("aperture", aperture_b));
  ARROW_RETURN_NOT_OK(add("extra", extra_b));
  ARROW_RETURN_NOT_OK(add("scale", scale_b));
  ARROW_RETURN_NOT_OK(add("unit", unit_b));
  return arrow::Table::Make(arrow::schema(fields), arrays);
}

[[nodiscard]] auto rows_table(const std::vector<LpScaledRow>& rows,
                              Uid scene_uid,
                              Uid phase_uid)
    -> arrow::Result<std::shared_ptr<arrow::Table>>
{
  arrow::Int32Builder scene_b;
  arrow::Int32Builder phase_b;
  arrow::Int32Builder index_b;
  arrow::StringBuilder name_b;
  arrow::DoubleBuilder scale_b;
  for (const auto& r : rows) {
    ARROW_RETURN_NOT_OK(scene_b.Append(scene_uid));
    ARROW_RETURN_NOT_OK(phase_b.Append(phase_uid));
    ARROW_RETURN_NOT_OK(index_b.Append(r.index));
    ARROW_RETURN_NOT_OK(name_b.Append(r.name));
    ARROW_RETURN_NOT_OK(scale_b.Append(r.scale));
  }
  ARROW_ASSIGN_OR_RAISE(auto scene_a, scene_b.Finish());
  ARROW_ASSIGN_OR_RAISE(auto phase_a, phase_b.Finish());
  ARROW_ASSIGN_OR_RAISE(auto index_a, index_b.Finish());
  ARROW_ASSIGN_OR_RAISE(auto name_a, name_b.Finish());
  ARROW_ASSIGN_OR_RAISE(auto scale_a, scale_b.Finish());
  const auto schema = arrow::schema({
      arrow::field("scene_uid", arrow::int32()),
      arrow::field("phase_uid", arrow::int32()),
      arrow::field("index", arrow::int32()),
      arrow::field("name", arrow::utf8()),
      arrow::field("scale", arrow::float64()),
  });
  return arrow::Table::Make(schema,
                            {
                                scene_a,
                                phase_a,
                                index_a,
                                name_a,
                                scale_a,
                            });
}

/// Creates the parent directory of @p path, if it has one.
[[nodiscard]] auto ensure_parent_dir(const std::filesystem::path& path)
    -> std::expected<void, Error>
{
  if (!path.has_parent_path()) {
    return {};
  }
  std::error_code ec;
  std::filesystem::create_directories(path.parent_path(), ec);
  if (ec) {
    return std::unexpected(
        file_io_error(std::format("lp_scales: cannot create directory '{}': {}",
                                  path.parent_path().string(),
                                  ec.message())));
  }
  return {};
}

[[nodiscard]] auto count_scaled(Index n, auto&& scale_at) -> Size
{
  return static_cast<Size>(
      std::ranges::count_if(std::views::iota(Index {0}, n),
                            [&](Index i) { return scale_at(i) != 1.0; }));
}

}  // namespace

auto to_scales_context(const LpContext& context)
    -> std::optional<LpScalesContext>
{
  return std::visit(
      Overload {
          [](const std::monostate&) -> std::optional<LpScalesContext>
          { return std::nullopt; },
          [](const StageContext& c) -> std::optional<LpScalesContext>
          {
            const auto& [scenario, stage] = c;
            return LpScalesContext {
                .type = "stage",
                .scenario = scenario,
                .stage = stage,
            };
          },
          [](const BlockContext& c) -> std::optional<LpScalesContext>
          {
            const auto& [scenario, stage, block] = c;
            return LpScalesContext {
                .type = "block",
                .scenario = scenario,
                .stage = stage,
                .block = block,
            };
          },
          [](const BlockExContext& c) -> std::optional<LpScalesContext>
          {
            const auto& [scenario, stage, block, extra] = c;
            return LpScalesContext {
                .type = "block_ex",
                .scenario = scenario,
                .stage = stage,
                .block = block,
                .extra = extra,
            };
          },
          [](const ScenePhaseContext& c) -> std::optional<LpScalesContext>
          {
            const auto& [scene, phase] = c;
            return LpScalesContext {
                .type = "scene_phase",
                .scene = scene,
                .phase = phase,
            };
          },
          [](const PhaseContext& c) -> std::optional<LpScalesContext>
          {
            const auto& [scene, phase, element] = c;
            return LpScalesContext {
                .type = "phase",
                .scene = scene,
                .phase = phase,
                .element = element,
            };
          },
          [](const IterationContext& c) -> std::optional<LpScalesContext>
          {
            const auto& [scene, phase, iteration, extra] = c;
            return LpScalesContext {
                .type = "iteration",
                .scene = scene,
                .phase = phase,
                .iteration = iteration,
                .extra = extra,
            };
          },
          [](const ApertureContext& c) -> std::optional<LpScalesContext>
          {
            const auto& [scene, phase, aperture, extra] = c;
            return LpScalesContext {
                .type = "aperture",
                .scene = scene,
                .phase = phase,
                .aperture = aperture,
                .extra = extra,
            };
          },
      },
      context);
}

std::string lp_scales_path(std::string_view lp_path, std::string_view suffix)
{
  constexpr std::array<std::string_view, 5> compression_exts {
      ".gz",
      ".zst",
      ".lz4",
      ".bz2",
      ".xz",
  };
  constexpr std::array<std::string_view, 2> model_exts {
      ".lp",
      ".mps",
  };

  auto stem = lp_path;
  strip_one_suffix(stem, compression_exts);
  strip_one_suffix(stem, model_exts);
  return std::format("{}{}", stem, suffix);
}

LpScales make_lp_scales(const SystemLP& system,
                        std::string_view lp_path,
                        LpScalesLevel level)
{
  const auto& li = system.linear_interface();
  const auto build = build_info();

  LpScales scales {
      .level = std::string(enum_name(level)),
      .gtopt =
          {
              .version = std::string(build.version),
              .git_revision = std::string(build.git_revision),
          },
      .lp_file = std::filesystem::path(lp_path).filename().string(),
      .scene_uid = system.scene().uid(),
      .phase_uid = system.phase().uid(),
      .num_cols = li.get_numcols(),
      .num_rows = li.get_numrows(),
      .objective =
          {
              .scale_objective = li.scale_objective(),
              .constant = li.get_obj_constant(),
          },
      .numerics = make_numerics(li),
  };

  for (const auto& scenario : system.scene().scenarios()) {
    for (const auto& stage : system.phase().stages()) {
      scales.stages.push_back({
          .scenario = scenario.uid(),
          .stage = stage.uid(),
          .duration = stage.duration(),
          .probability = scenario.probability_factor(),
          .discount = stage.discount_factor(),
          .cost_factor = CostHelper::cost_factor(scenario, stage),
      });
      for (const auto& block : stage.blocks()) {
        scales.blocks.push_back({
            .scenario = scenario.uid(),
            .stage = stage.uid(),
            .block = block.uid(),
            .duration = block.duration(),
            .probability = scenario.probability_factor(),
            .discount = stage.discount_factor(),
            .cost_factor = CostHelper::cost_factor(scenario, stage, block),
        });
      }
    }
  }

  if (level == LpScalesLevel::full) {
    scales.tables = LpScalesTables {
        .columns = std::filesystem::path(
                       lp_scales_path(lp_path, lp_scales_cols_suffix))
                       .filename()
                       .string(),
        .rows = std::filesystem::path(
                    lp_scales_path(lp_path, lp_scales_rows_suffix))
                    .filename()
                    .string(),
        .num_scaled_columns = count_scaled(
            scales.num_cols,
            [&li](Index i) { return li.get_col_scale(ColIndex {i}); }),
        .num_scaled_rows = count_scaled(
            scales.num_rows,
            [&li](Index i) { return li.get_row_scale(RowIndex {i}); }),
    };
  }
  return scales;
}

LpScalesDetail make_lp_scales_detail(const SystemLP& system)
{
  const auto& li = system.linear_interface();

  // Same names the `.lp`/`.mps` carries (regenerated from the labels).
  std::vector<std::string> col_names;
  std::vector<std::string> row_names;
  li.generate_labels_from_maps(col_names, row_names);
  const auto name_at = [](const std::vector<std::string>& names, Index i)
  {
    const auto pos = static_cast<std::size_t>(i);
    return pos < names.size() ? names[pos] : std::string {};
  };

  // Only scaled entries: one with scale 1 needs nothing beyond what the
  // model file already carries.
  LpScalesDetail detail;
  const SparseColLabel no_label {};
  for (const auto i : std::views::iota(Index {0}, li.get_numcols())) {
    const ColIndex col {i};
    const auto scale = li.get_col_scale(col);
    if (scale == 1.0) {
      continue;
    }
    const auto* label = li.col_label_at(col);
    const auto& l = label != nullptr ? *label : no_label;
    detail.columns.push_back({
        .index = i,
        .name = name_at(col_names, i),
        .class_name = std::string(l.class_name),
        .variable = std::string(l.variable_name),
        .uid = l.variable_uid,
        .context = to_scales_context(l.context),
        .scale = scale,
        .unit = unit_of(l.class_name, l.variable_name),
    });
  }
  for (const auto i : std::views::iota(Index {0}, li.get_numrows())) {
    const auto scale = li.get_row_scale(RowIndex {i});
    if (scale == 1.0) {
      continue;
    }
    detail.rows.push_back({
        .index = i,
        .name = name_at(row_names, i),
        .scale = scale,
    });
  }
  return detail;
}

std::expected<void, Error> write_lp_scales_tables(const LpScalesDetail& detail,
                                                  Uid scene_uid,
                                                  Uid phase_uid,
                                                  std::string_view columns_path,
                                                  std::string_view rows_path)
{
  const auto write = [&](std::string_view path,
                         auto&& make_table) -> std::expected<void, Error>
  {
    const std::string target {path};
    if (auto dir = ensure_parent_dir(target); !dir) {
      return dir;
    }
    auto status = [&]() -> arrow::Status
    {
      ARROW_ASSIGN_OR_RAISE(auto table, make_table());
      return write_parquet(*table, target);
    }();
    if (!status.ok()) {
      std::error_code rm_ec;
      std::filesystem::remove(target, rm_ec);
      return std::unexpected(file_io_error(std::format(
          "lp_scales: cannot write '{}': {}", target, status.ToString())));
    }
    return {};
  };

  if (auto cols = write(
          columns_path,
          [&] { return columns_table(detail.columns, scene_uid, phase_uid); });
      !cols)
  {
    return cols;
  }
  return write(rows_path,
               [&] { return rows_table(detail.rows, scene_uid, phase_uid); });
}

std::string lp_scales_to_json(const LpScales& scales)
{
  return daw::json::to_json(scales);
}

std::expected<std::string, Error> write_lp_scales(const SystemLP& system,
                                                  std::string_view lp_path,
                                                  LpScalesLevel level,
                                                  std::string_view compression)
{
  try {
    auto path = lp_scales_path(lp_path);
    const auto scales = make_lp_scales(system, lp_path, level);

    if (scales.tables) {
      if (auto tables = write_lp_scales_tables(
              make_lp_scales_detail(system),
              scales.scene_uid,
              scales.phase_uid,
              lp_scales_path(lp_path, lp_scales_cols_suffix),
              lp_scales_path(lp_path, lp_scales_rows_suffix));
          !tables)
      {
        return std::unexpected(std::move(tables.error()));
      }
    }

    const auto json = lp_scales_to_json(scales);
    const std::filesystem::path fs_path {path};
    if (auto dir = ensure_parent_dir(fs_path); !dir) {
      return std::unexpected(std::move(dir.error()));
    }

    bool written = false;
    {
      std::ofstream out(fs_path, std::ios::binary | std::ios::trunc);
      if (out) {
        out.write(json.data(), static_cast<std::streamsize>(json.size()));
        out.close();
        written = !out.fail();
      }
    }
    if (!written) {
      std::error_code rm_ec;
      std::filesystem::remove(fs_path, rm_ec);
      return std::unexpected(file_io_error(
          std::format("lp_scales: cannot write '{}' (check that the "
                      "directory is writable and the disk is not full)",
                      path)));
    }
    if (compression.empty() || compression == "none"
        || compression == "uncompressed")
    {
      return path;
    }
    // Same codec cascade as the model file; when it fails the plain
    // sidecar stays in place.
    if (auto compressed = compress_lp_file(path, std::string(compression));
        !compressed.empty())
    {
      return compressed;
    }
    return path;
  } catch (const std::exception& e) {
    return std::unexpected(file_io_error(
        std::format("lp_scales: failed for '{}': {}", lp_path, e.what())));
  } catch (...) {
    return std::unexpected(
        file_io_error("lp_scales: unknown error while writing the sidecar"));
  }
}

}  // namespace gtopt
