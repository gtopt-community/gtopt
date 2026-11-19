/**
 * @file      lp_metadata.cpp
 * @brief     Streaming writer for the `<stem>.meta.json` LP sidecar
 * @date      2026-11-19
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cmath>
#include <concepts>
#include <cstdint>
#include <filesystem>
#include <format>
#include <fstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <variant>
#include <vector>

#include <gtopt/cost_helper.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/lp_metadata.hpp>
#include <gtopt/system_lp.hpp>

namespace gtopt
{

namespace
{

/// JSON string escape (quotes, backslashes, control characters).
void put_string(std::ostream& out, std::string_view s)
{
  out << '"';
  for (const char c : s) {
    switch (c) {
      case '"':
        out << "\\\"";
        break;
      case '\\':
        out << "\\\\";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          out << std::format("\\u{:04x}", static_cast<unsigned>(c));
        } else {
          out << c;
        }
    }
  }
  out << '"';
}

/// JSON number for a double (shortest round-trip form; non-finite -> null).
void put_double(std::ostream& out, double v)
{
  if (std::isfinite(v)) {
    out << std::format("{}", v);
  } else {
    out << "null";
  }
}

template<typename T>
[[nodiscard]] std::int64_t as_int(const T& v) noexcept
{
  return static_cast<std::int64_t>(v);
}

/// Writes the structured context as a JSON object (or `null`).
void put_context(std::ostream& out, const LpContext& context)
{
  std::visit(
      [&]<typename T>(const T& c)
      {
        if constexpr (std::same_as<T, std::monostate>) {
          out << "null";
        } else if constexpr (std::same_as<T, StageContext>) {
          out << std::format(
              "{{\"type\":\"stage\",\"scenario\":{},\"stage\":{}}}",
              as_int(std::get<0>(c)),
              as_int(std::get<1>(c)));
        } else if constexpr (std::same_as<T, BlockContext>) {
          out << std::format(
              "{{\"type\":\"block\",\"scenario\":{},\"stage\":{},\"block\":{}}"
              "}",
              as_int(std::get<0>(c)),
              as_int(std::get<1>(c)),
              as_int(std::get<2>(c)));
        } else if constexpr (std::same_as<T, BlockExContext>) {
          out << std::format(
              "{{\"type\":\"block_ex\",\"scenario\":{},\"stage\":{},"
              "\"block\":{},\"extra\":{}}}",
              as_int(std::get<0>(c)),
              as_int(std::get<1>(c)),
              as_int(std::get<2>(c)),
              as_int(std::get<3>(c)));
        } else if constexpr (std::same_as<T, ScenePhaseContext>) {
          out << std::format(
              "{{\"type\":\"scene_phase\",\"scene\":{},\"phase\":{}}}",
              as_int(std::get<0>(c)),
              as_int(std::get<1>(c)));
        } else if constexpr (std::same_as<T, PhaseContext>) {
          out << std::format(
              "{{\"type\":\"phase\",\"scene\":{},\"phase\":{},\"element\":{}}}",
              as_int(std::get<0>(c)),
              as_int(std::get<1>(c)),
              as_int(std::get<2>(c)));
        } else if constexpr (std::same_as<T, IterationContext>) {
          out << std::format(
              "{{\"type\":\"iteration\",\"scene\":{},\"phase\":{},"
              "\"iteration\":{},\"extra\":{}}}",
              as_int(std::get<0>(c)),
              as_int(std::get<1>(c)),
              as_int(std::get<2>(c)),
              as_int(std::get<3>(c)));
        } else if constexpr (std::same_as<T, ApertureContext>) {
          out << std::format(
              "{{\"type\":\"aperture\",\"scene\":{},\"phase\":{},"
              "\"aperture\":{},\"extra\":{}}}",
              as_int(std::get<0>(c)),
              as_int(std::get<1>(c)),
              as_int(std::get<2>(c)),
              as_int(std::get<3>(c)));
        } else {
          out << "null";
        }
      },
      context);
}

}  // namespace

std::string lp_metadata_path(const std::string& lp_path)
{
  for (const std::string_view ext :
       {std::string_view {".lp"}, std::string_view {".mps"}})
  {
    if (lp_path.ends_with(ext)) {
      return lp_path.substr(0, lp_path.size() - ext.size()) + ".meta.json";
    }
  }
  return lp_path + ".meta.json";
}

std::string write_lp_metadata(const SystemLP& system,
                              const std::string& lp_path)
{
  const auto& li = system.linear_interface();
  const auto path = lp_metadata_path(lp_path);

  if (const auto parent = std::filesystem::path(path).parent_path();
      !parent.empty())
  {
    std::filesystem::create_directories(parent);
  }

  // Same names the `.lp` carries (regenerated from the structured labels).
  std::vector<std::string> col_names;
  std::vector<std::string> row_names;
  li.generate_labels_from_maps(col_names, row_names);

  std::ofstream out(path);
  if (!out) {
    throw std::runtime_error(
        std::format("lp_metadata: cannot open '{}' for writing", path));
  }
  out << "{\n";
  out << "\"schema\":\"" << lp_metadata_schema << "\",\n";
  out << "\"lp_file\":";
  put_string(out, std::filesystem::path(lp_path).filename().string());
  out << ",\n";
  out << std::format("\"scene_uid\":{},\n\"phase_uid\":{},\n",
                     as_int(system.scene().uid()),
                     as_int(system.phase().uid()));

  // ── objective ──
  out << "\"objective\":{\"scale_objective\":";
  put_double(out, li.scale_objective());
  out << ",\"constant\":";
  put_double(out, li.get_obj_constant());
  out << ",\"sense\":\"min\"},\n";

  // ── blocks ──
  out << "\"blocks\":[";
  bool first_block = true;
  for (const auto& scenario : system.scene().scenarios()) {
    for (const auto& stage : system.phase().stages()) {
      for (const auto& block : stage.blocks()) {
        out << (first_block ? "\n" : ",\n");
        first_block = false;
        const auto cf = CostHelper::cost_factor(scenario, stage, block);
        out << std::format(
            "{{\"scenario\":{},\"stage\":{},\"block\":{},\"duration\":",
            as_int(scenario.uid()),
            as_int(stage.uid()),
            as_int(block.uid()));
        put_double(out, block.duration());
        out << ",\"probability\":";
        put_double(out, scenario.probability_factor());
        out << ",\"discount\":";
        put_double(out, stage.discount_factor());
        out << ",\"cost_factor\":";
        put_double(out, cf);
        out << '}';
      }
    }
  }
  out << "\n],\n";

  // ── columns ──
  out << "\"columns\":[";
  for (std::size_t i = 0; i < col_names.size(); ++i) {
    const ColIndex ci {static_cast<Index>(i)};
    const auto* meta = li.col_label_at(ci);
    out << (i == 0 ? "\n" : ",\n") << "{\"name\":";
    put_string(out, col_names[i]);
    out << ",\"class\":";
    put_string(out, meta != nullptr ? meta->class_name : std::string_view {});
    out << ",\"variable\":";
    put_string(out,
               meta != nullptr ? meta->variable_name : std::string_view {});
    out << ",\"uid\":"
        << (meta != nullptr ? as_int(meta->variable_uid) : std::int64_t {-1});
    out << ",\"context\":";
    put_context(out, meta != nullptr ? meta->context : LpContext {});
    out << ",\"integer\":" << (li.is_integer(ci) ? "true" : "false");
    out << ",\"scale\":";
    put_double(out, li.get_col_scale(ci));
    out << '}';
  }
  out << "\n],\n";

  // ── rows ──
  out << "\"rows\":[";
  for (std::size_t i = 0; i < row_names.size(); ++i) {
    const RowIndex ri {static_cast<Index>(i)};
    const auto* meta = li.row_label_at(ri);
    out << (i == 0 ? "\n" : ",\n") << "{\"name\":";
    put_string(out, row_names[i]);
    out << ",\"class\":";
    put_string(out, meta != nullptr ? meta->class_name : std::string_view {});
    out << ",\"constraint\":";
    put_string(out,
               meta != nullptr ? meta->constraint_name : std::string_view {});
    out << ",\"uid\":"
        << (meta != nullptr ? as_int(meta->variable_uid) : std::int64_t {-1});
    out << ",\"context\":";
    put_context(out, meta != nullptr ? meta->context : LpContext {});
    out << ",\"scale\":";
    put_double(out, li.get_row_scale(ri));
    out << '}';
  }
  out << "\n]\n}\n";

  out.close();
  if (!out) {
    throw std::runtime_error(
        std::format("lp_metadata: write to '{}' failed", path));
  }

  return path;
}

}  // namespace gtopt
