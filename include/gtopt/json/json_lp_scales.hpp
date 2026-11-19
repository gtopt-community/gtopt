/**
 * @file      json_lp_scales.hpp
 * @brief     daw::json contracts for the LP scales sidecar
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Serialisation (and parsing, for tests and tools) of `LpScales`; the
 * schema is documented in `docs/lp-scales.md`.
 */

#pragma once

#include <gtopt/json/json_basic_types.hpp>
#include <gtopt/lp_scales.hpp>

namespace daw::json
{

template<>
struct json_data_contract<gtopt::LpScalesBuild>
{
  using type = json_member_list<json_string<"version", std::string>,
                                json_string<"git_revision", std::string>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesBuild const& b)
  {
    return std::forward_as_tuple(b.version, b.git_revision);
  }
};

template<>
struct json_data_contract<gtopt::LpScalesObjective>
{
  using type = json_member_list<json_number<"scale_objective", double>,
                                json_number<"constant", double>,
                                json_string<"sense", std::string>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesObjective const& o)
  {
    return std::forward_as_tuple(o.scale_objective, o.constant, o.sense);
  }
};

template<>
struct json_data_contract<gtopt::LpScalesBlock>
{
  using type = json_member_list<json_number<"scenario", gtopt::Uid>,
                                json_number<"stage", gtopt::Uid>,
                                json_number<"block", gtopt::Uid>,
                                json_number<"duration", double>,
                                json_number<"probability", double>,
                                json_number<"discount", double>,
                                json_number<"cost_factor", double>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesBlock const& b)
  {
    return std::forward_as_tuple(b.scenario,
                                 b.stage,
                                 b.block,
                                 b.duration,
                                 b.probability,
                                 b.discount,
                                 b.cost_factor);
  }
};

template<>
struct json_data_contract<gtopt::LpScalesStage>
{
  using type = json_member_list<json_number<"scenario", gtopt::Uid>,
                                json_number<"stage", gtopt::Uid>,
                                json_number<"duration", double>,
                                json_number<"probability", double>,
                                json_number<"discount", double>,
                                json_number<"cost_factor", double>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesStage const& s)
  {
    return std::forward_as_tuple(s.scenario,
                                 s.stage,
                                 s.duration,
                                 s.probability,
                                 s.discount,
                                 s.cost_factor);
  }
};

template<>
struct json_data_contract<gtopt::LpScalesContext>
{
  using type = json_member_list<json_string<"type", std::string>,
                                json_number_null<"scenario", gtopt::OptUid>,
                                json_number_null<"stage", gtopt::OptUid>,
                                json_number_null<"block", gtopt::OptUid>,
                                json_number_null<"scene", gtopt::OptUid>,
                                json_number_null<"phase", gtopt::OptUid>,
                                json_number_null<"element", gtopt::OptUid>,
                                json_number_null<"iteration", gtopt::OptUid>,
                                json_number_null<"aperture", gtopt::OptUid>,
                                json_number_null<"extra", gtopt::OptInt>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesContext const& c)
  {
    return std::forward_as_tuple(c.type,
                                 c.scenario,
                                 c.stage,
                                 c.block,
                                 c.scene,
                                 c.phase,
                                 c.element,
                                 c.iteration,
                                 c.aperture,
                                 c.extra);
  }
};

template<>
struct json_data_contract<gtopt::LpScaledColumn>
{
  using type = json_member_list<
      json_number<"index", gtopt::Index>,
      json_string<"name", std::string>,
      json_string<"class", std::string>,
      json_string<"variable", std::string>,
      json_number<"uid", gtopt::Uid>,
      json_class_null<"context", std::optional<gtopt::LpScalesContext>>,
      json_number<"scale", double>,
      json_string_null<"unit", gtopt::OptName>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScaledColumn const& c)
  {
    return std::forward_as_tuple(c.index,
                                 c.name,
                                 c.class_name,
                                 c.variable,
                                 c.uid,
                                 c.context,
                                 c.scale,
                                 c.unit);
  }
};

template<>
struct json_data_contract<gtopt::LpScalesRange>
{
  using type = json_member_list<json_number<"min_abs", double>,
                                json_number<"max_abs", double>,
                                json_number<"log10_ratio", double>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesRange const& r)
  {
    return std::forward_as_tuple(r.min_abs, r.max_abs, r.log10_ratio);
  }
};

template<>
struct json_data_contract<gtopt::LpScalesRowType>
{
  using type = json_member_list<json_string<"type", std::string>,
                                json_number<"count", gtopt::Size>,
                                json_number<"nnz", gtopt::Size>,
                                json_number<"min_abs", double>,
                                json_number<"max_abs", double>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesRowType const& r)
  {
    return std::forward_as_tuple(r.type, r.count, r.nnz, r.min_abs, r.max_abs);
  }
};

template<>
struct json_data_contract<gtopt::LpScalesNumerics>
{
  using OptRange = std::optional<gtopt::LpScalesRange>;
  using type =
      json_member_list<json_string<"equilibration_method", std::string>,
                       json_number<"nnz", gtopt::Size>,
                       json_class_null<"matrix", OptRange>,
                       json_string_null<"matrix_min_col", gtopt::OptName>,
                       json_string_null<"matrix_max_col", gtopt::OptName>,
                       json_class_null<"objective", OptRange>,
                       json_class_null<"bounds", OptRange>,
                       json_class_null<"rhs", OptRange>,
                       json_class_null<"col_scales", OptRange>,
                       json_class_null<"row_scales", OptRange>,
                       json_array<"row_types",
                                  gtopt::LpScalesRowType,
                                  std::vector<gtopt::LpScalesRowType>>>;

  [[nodiscard]] constexpr static auto to_json_data(
      gtopt::LpScalesNumerics const& n)
  {
    return std::forward_as_tuple(n.equilibration_method,
                                 n.nnz,
                                 n.matrix,
                                 n.matrix_min_col,
                                 n.matrix_max_col,
                                 n.objective,
                                 n.bounds,
                                 n.rhs,
                                 n.col_scales,
                                 n.row_scales,
                                 n.row_types);
  }
};

template<>
struct json_data_contract<gtopt::LpScales>
{
  using type =
      json_member_list<json_string<"schema", std::string>,
                       json_class<"gtopt", gtopt::LpScalesBuild>,
                       json_string<"lp_file", std::string>,
                       json_number<"scene_uid", gtopt::Uid>,
                       json_number<"phase_uid", gtopt::Uid>,
                       json_number<"num_cols", gtopt::Index>,
                       json_number<"num_rows", gtopt::Index>,
                       json_class<"objective", gtopt::LpScalesObjective>,
                       json_array<"blocks",
                                  gtopt::LpScalesBlock,
                                  std::vector<gtopt::LpScalesBlock>>,
                       json_array<"stages",
                                  gtopt::LpScalesStage,
                                  std::vector<gtopt::LpScalesStage>>,
                       json_array<"scaled_columns",
                                  gtopt::LpScaledColumn,
                                  std::vector<gtopt::LpScaledColumn>>,
                       json_array<"row_scales", double, std::vector<double>>,
                       json_class<"numerics", gtopt::LpScalesNumerics>>;

  [[nodiscard]] constexpr static auto to_json_data(gtopt::LpScales const& m)
  {
    return std::forward_as_tuple(m.schema,
                                 m.gtopt,
                                 m.lp_file,
                                 m.scene_uid,
                                 m.phase_uid,
                                 m.num_cols,
                                 m.num_rows,
                                 m.objective,
                                 m.blocks,
                                 m.stages,
                                 m.scaled_columns,
                                 m.row_scales,
                                 m.numerics);
  }
};

}  // namespace daw::json
