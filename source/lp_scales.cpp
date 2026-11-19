/**
 * @file      lp_scales.cpp
 * @brief     Build and write the `<stem>.scales.json` LP sidecar
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
#include <optional>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <variant>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <gtopt/build_info.hpp>
#include <gtopt/cost_helper.hpp>
#include <gtopt/enum_option.hpp>
#include <gtopt/json/json_lp_scales.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/lp_class_name.hpp>
#include <gtopt/lp_matrix_enums.hpp>
#include <gtopt/lp_scales.hpp>
#include <gtopt/overload.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/unit_registry.hpp>

namespace gtopt
{

namespace
{

/// Maps the in-memory `LpContext` variant to its sidecar form.
[[nodiscard]] auto to_scales_context(const LpContext& context)
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

}  // namespace

std::string lp_scales_path(std::string_view lp_path)
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
  return std::format("{}.scales.json", stem);
}

LpScales make_lp_scales(const SystemLP& system, std::string_view lp_path)
{
  const auto& li = system.linear_interface();
  const auto build = build_info();

  LpScales scales {
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

  // Same names the `.lp`/`.mps` carries (regenerated from the labels).
  std::vector<std::string> col_names;
  std::vector<std::string> row_names;
  li.generate_labels_from_maps(col_names, row_names);

  // Only scaled columns: an unscaled one (scale 1) needs nothing beyond
  // what the model file already carries.
  for (const auto i : std::views::iota(Index {0}, scales.num_cols)) {
    const ColIndex col {i};
    const auto scale = li.get_col_scale(col);
    if (scale == 1.0) {
      continue;
    }
    const auto pos = static_cast<std::size_t>(i);
    const auto* label = li.col_label_at(col);
    const SparseColLabel no_label {};
    const auto& l = label != nullptr ? *label : no_label;
    scales.scaled_columns.push_back({
        .index = i,
        .name = pos < col_names.size() ? col_names[pos] : std::string {},
        .class_name = std::string(l.class_name),
        .variable = std::string(l.variable_name),
        .uid = l.variable_uid,
        .context = to_scales_context(l.context),
        .scale = scale,
        .unit = unit_of(l.class_name, l.variable_name),
    });
  }

  // Row scales are dense (row equilibration scales nearly every row);
  // names and classes are already in the model file, in the same order.
  const auto row_scale_at = [&li](Index i)
  { return li.get_row_scale(RowIndex {i}); };
  auto row_scales = std::views::iota(Index {0}, scales.num_rows)
      | std::views::transform(row_scale_at);
  if (std::ranges::any_of(row_scales, [](double s) { return s != 1.0; })) {
    scales.row_scales = row_scales | std::ranges::to<std::vector>();
  }

  return scales;
}

std::string lp_scales_to_json(const LpScales& scales)
{
  return daw::json::to_json(scales);
}

std::expected<std::string, Error> write_lp_scales(const SystemLP& system,
                                                  std::string_view lp_path)
{
  try {
    const auto path = lp_scales_path(lp_path);
    const auto json = lp_scales_to_json(make_lp_scales(system, lp_path));

    const std::filesystem::path fs_path {path};
    if (fs_path.has_parent_path()) {
      std::error_code ec;
      std::filesystem::create_directories(fs_path.parent_path(), ec);
      if (ec) {
        return std::unexpected(file_io_error(
            std::format("lp_scales: cannot create directory '{}': {}",
                        fs_path.parent_path().string(),
                        ec.message())));
      }
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
