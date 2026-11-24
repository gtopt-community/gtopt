/**
 * @file      future_cost_aliases.cpp
 * @brief     Translate the deprecated boundary-cut option keys into FutureCost
 * @date      Fri Nov 20 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <expected>
#include <format>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <variant>

#include <gtopt/enum_option.hpp>
#include <gtopt/future_cost_aliases.hpp>
#include <gtopt/planning.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// Human-readable rendering of one option value for warnings / errors.
template<typename T>
[[nodiscard]] std::string value_text(const T& value)
{
  if constexpr (std::is_same_v<T, std::string>) {
    return std::format("'{}'", value);
  } else if constexpr (std::is_same_v<T, bool>) {
    return value ? "true" : "false";
  } else if constexpr (std::is_enum_v<T>) {
    return std::string {enum_name(value)};
  } else {
    return std::format("{}", value);
  }
}

/// True unless the element's `active` is an explicit scalar `False` — the
/// same rule `active_future_cost` applies on the LP side.
[[nodiscard]] bool is_active(const FutureCost& fc) noexcept
{
  if (fc.active.has_value()) {
    if (const auto* scalar = std::get_if<IntBool>(&*fc.active);
        scalar != nullptr && *scalar == False)
    {
      return false;
    }
  }
  return true;
}

/// Accumulates the alias translation for one FutureCost element and
/// remembers where each field's value came from, so a conflict names both
/// sources.
class AliasFolder
{
public:
  explicit AliasFolder(System& system)
      : m_system_(&system)
  {
  }

  /// Fold the legacy @p legacy value (key @p legacy_key) into the element
  /// field selected by @p field (named @p field_name).  Unset legacy values
  /// are a no-op.  The legacy optional is cleared in every case.
  template<typename T>
  [[nodiscard]] std::expected<void, Error> fold(
      std::optional<T>& legacy,
      std::string_view legacy_key,
      std::optional<T> FutureCost::* field,
      std::string_view field_name)
  {
    if (!legacy.has_value()) {
      return {};
    }
    auto value = std::move(*legacy);
    legacy.reset();

    auto& fc = element();
    auto& target = fc.*field;
    const auto fc_key =
        std::format("future_cost_array[uid={}].{}", fc.uid, field_name);
    if (target.has_value()) {
      if (*target != value) {
        const auto origin = m_origin_.contains(std::string {field_name})
            ? m_origin_.at(std::string {field_name})
            : fc_key;
        return std::unexpected(Error {
            .code = ErrorCode::InvalidInput,
            .message = std::format(
                "FutureCost: conflicting values for '{}': {} (from {}) vs {} "
                "(from deprecated {}).  Configure the boundary cuts only on "
                "the FutureCost element.",
                field_name,
                value_text(*target),
                origin,
                value_text(value),
                legacy_key),
        });
      }
    } else {
      target = std::move(value);
      m_origin_.insert_or_assign(std::string {field_name},
                                 std::format("deprecated {}", legacy_key));
    }
    spdlog::warn("Deprecated option '{}' → use '{}' (FutureCost element)",
                 legacy_key,
                 fc_key);
    return {};
  }

private:
  /// The first active FutureCost element, created as `{uid: 1, name: "fcf"}`
  /// (or the next free uid) on first use when there is none.
  [[nodiscard]] FutureCost& element()
  {
    if (m_index_.has_value()) {
      return m_system_->future_cost_array[*m_index_];
    }
    auto& arr = m_system_->future_cost_array;
    const auto it = std::ranges::find_if(arr, is_active);
    if (it != arr.end()) {
      m_index_ = static_cast<std::size_t>(std::distance(arr.begin(), it));
      return *it;
    }
    Uid uid {1};
    for (const auto& fc : arr) {
      uid = std::max(uid, fc.uid + 1);
    }
    arr.push_back(FutureCost {.uid = uid, .name = "fcf"});
    m_index_ = arr.size() - 1;
    return arr.back();
  }

  System* m_system_;  ///< never null (bound in the constructor)
  std::optional<std::size_t> m_index_ {};
  std::map<std::string, std::string, std::less<>> m_origin_ {};
};

/// Clear a legacy key that does not apply to the selected method, warning
/// when it was actually set.
template<typename T>
void drop_foreign(std::optional<T>& legacy,
                  std::string_view legacy_key,
                  std::string_view method)
{
  if (legacy.has_value()) {
    spdlog::warn(
        "Option '{}' is ignored under method={} (a method never reads "
        "another method's options) — configure the boundary cuts on the "
        "FutureCost element (system.future_cost_array) instead",
        legacy_key,
        method);
    legacy.reset();
  }
}

}  // namespace

std::expected<void, Error> resolve_future_cost_aliases(Planning& planning)
{
  auto& sddp = planning.options.sddp_options;
  auto& mono = planning.options.monolithic_options;
  auto& sim = planning.simulation;

  const auto method = planning.options.method.value_or(MethodType::monolithic);
  const bool is_sddp =
      method == MethodType::sddp || method == MethodType::cascade;
  const auto method_name = enum_name(method);

  AliasFolder folder {planning.system};

  // Method-neutral aliases first (`simulation.*`), then the method's own.
  if (auto r = folder.fold(sim.boundary_cuts_file,
                           "simulation.boundary_cuts_file",
                           &FutureCost::cuts_file,
                           "cuts_file");
      !r)
  {
    return r;
  }
  if (auto r = folder.fold(sim.boundary_cuts_valuation,
                           "simulation.boundary_cuts_valuation",
                           &FutureCost::valuation,
                           "valuation");
      !r)
  {
    return r;
  }

  if (is_sddp) {
    for (auto r :
         {
             folder.fold(sddp.boundary_cuts_file,
                         "sddp_options.boundary_cuts_file",
                         &FutureCost::cuts_file,
                         "cuts_file"),
             folder.fold(sddp.boundary_cuts_mode,
                         "sddp_options.boundary_cuts_mode",
                         &FutureCost::mode,
                         "mode"),
             folder.fold(sddp.boundary_cut_sharing_mode,
                         "sddp_options.boundary_cut_sharing_mode",
                         &FutureCost::sharing,
                         "sharing"),
             folder.fold(sddp.boundary_cuts_mean_shift,
                         "sddp_options.boundary_cuts_mean_shift",
                         &FutureCost::mean_shift,
                         "mean_shift"),
             folder.fold(sddp.boundary_max_iterations,
                         "sddp_options.boundary_max_iterations",
                         &FutureCost::iteration_limit,
                         "iteration_limit"),
             folder.fold(sddp.missing_cut_var_mode,
                         "sddp_options.missing_cut_var_mode",
                         &FutureCost::missing_cut_var_mode,
                         "missing_cut_var_mode"),
         })
    {
      if (!r) {
        return r;
      }
    }
    drop_foreign(mono.boundary_cuts_file,
                 "monolithic_options.boundary_cuts_file",
                 method_name);
    drop_foreign(mono.boundary_cuts_mode,
                 "monolithic_options.boundary_cuts_mode",
                 method_name);
    drop_foreign(mono.boundary_cut_sharing_mode,
                 "monolithic_options.boundary_cut_sharing_mode",
                 method_name);
    drop_foreign(mono.boundary_max_iterations,
                 "monolithic_options.boundary_max_iterations",
                 method_name);
  } else {
    for (auto r :
         {
             folder.fold(mono.boundary_cuts_file,
                         "monolithic_options.boundary_cuts_file",
                         &FutureCost::cuts_file,
                         "cuts_file"),
             folder.fold(mono.boundary_cuts_mode,
                         "monolithic_options.boundary_cuts_mode",
                         &FutureCost::mode,
                         "mode"),
             folder.fold(mono.boundary_cut_sharing_mode,
                         "monolithic_options.boundary_cut_sharing_mode",
                         &FutureCost::sharing,
                         "sharing"),
             folder.fold(mono.boundary_max_iterations,
                         "monolithic_options.boundary_max_iterations",
                         &FutureCost::iteration_limit,
                         "iteration_limit"),
         })
    {
      if (!r) {
        return r;
      }
    }
    drop_foreign(sddp.boundary_cuts_file,
                 "sddp_options.boundary_cuts_file",
                 method_name);
    drop_foreign(sddp.boundary_cuts_mode,
                 "sddp_options.boundary_cuts_mode",
                 method_name);
    drop_foreign(sddp.boundary_cut_sharing_mode,
                 "sddp_options.boundary_cut_sharing_mode",
                 method_name);
    drop_foreign(sddp.boundary_cuts_mean_shift,
                 "sddp_options.boundary_cuts_mean_shift",
                 method_name);
    drop_foreign(sddp.boundary_max_iterations,
                 "sddp_options.boundary_max_iterations",
                 method_name);
    drop_foreign(sddp.missing_cut_var_mode,
                 "sddp_options.missing_cut_var_mode",
                 method_name);
  }

  return {};
}

}  // namespace gtopt
