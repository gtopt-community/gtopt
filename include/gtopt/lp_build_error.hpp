/**
 * @file      lp_build_error.hpp
 * @brief     Single failure channel for `add_to_lp` (LP assembly errors).
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Failure policy for LP assembly (`XLP::add_to_lp`):
 *
 *  - An `add_to_lp` either succeeds (`return true`) or fails HARD.  A
 *    `return false` is never a silent skip: the driver
 *    (`system_lp.cpp::add_to_lp`) turns it into an `LpBuildError`.
 *  - Element code reports a failure ONE way, through `fail_lp`:
 *
 *        fail_lp(*this, scenario, stage,
 *                {.field = "capacity",
 *                 .value = "unset",
 *                 .expected = "capacity or expansion on the generator"});
 *
 *  - Any other exception escaping an element's `add_to_lp` (runtime,
 *    logic, `bad_optional_access`, …) is caught ONCE by the driver and
 *    re-thrown as an `LpBuildError` carrying the element context
 *    (class, uid, name, scenario, stage) plus the original message, so
 *    a half-built element can never be silently skipped.
 *
 * `LpBuildError` derives from `std::runtime_error`, so every existing
 * `catch (const std::runtime_error&)` / `catch (const std::exception&)`
 * up the call chain keeps working.
 */

#pragma once

#include <cstdint>
#include <format>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <gtopt/basic_types.hpp>

namespace gtopt
{

/// Structured context of one LP-assembly failure.
struct LpBuildErrorInfo
{
  std::string class_name {};  ///< Element class, e.g. "Generator"
  std::optional<Uid> uid {};  ///< Element uid
  std::string name {};  ///< Element name (may be empty)
  std::optional<Uid> scenario {};  ///< Scenario uid, when relevant
  std::optional<Uid> stage {};  ///< Stage uid, when relevant
  std::optional<Uid> block {};  ///< Block uid, when relevant
  std::string field {};  ///< Offending input field (may be empty)
  std::string value {};  ///< Offending value, as text (may be empty)
  std::string expected {};  ///< What the value should have been
  std::string detail {};  ///< Free-form explanation / original message
};

/// One-line, human-readable rendering of an `LpBuildErrorInfo`:
///
///   LP build error in Generator uid=3 'g3' (scenario=1, stage=2, block=4):
///   field 'capacity' = unset, expected capacity or expansion: <detail>
[[nodiscard]] inline std::string format_lp_build_error(
    const LpBuildErrorInfo& info)
{
  std::string msg = "LP build error in ";
  msg += info.class_name.empty() ? std::string_view {"<element>"}
                                 : std::string_view {info.class_name};
  if (info.uid) {
    msg += std::format(" uid={}", *info.uid);
  }
  if (!info.name.empty()) {
    msg += std::format(" '{}'", info.name);
  }
  std::string where;
  const auto add_where = [&where](std::string_view key, std::optional<Uid> v)
  {
    if (v) {
      where += std::format("{}{}={}", where.empty() ? "" : ", ", key, *v);
    }
  };
  add_where("scenario", info.scenario);
  add_where("stage", info.stage);
  add_where("block", info.block);
  if (!where.empty()) {
    msg += std::format(" ({})", where);
  }
  if (!info.field.empty()) {
    msg += std::format(": field '{}'", info.field);
    if (!info.value.empty()) {
      msg += std::format(" = {}", info.value);
    }
    if (!info.expected.empty()) {
      msg += std::format(", expected {}", info.expected);
    }
  }
  if (!info.detail.empty()) {
    msg += ": ";
    msg += info.detail;
  }
  return msg;
}

/// The single exception type of the LP-assembly failure channel.
class LpBuildError : public std::runtime_error
{
public:
  explicit LpBuildError(LpBuildErrorInfo info)
      : std::runtime_error(format_lp_build_error(info))
      , m_info_(std::move(info))
  {
  }

  [[nodiscard]] const LpBuildErrorInfo& info() const noexcept
  {
    return m_info_;
  }

private:
  LpBuildErrorInfo m_info_;
};

/// What went wrong, as written by the element (`fail_lp` adds the
/// element / scenario / stage context).
struct LpFailure
{
  std::string_view field {};
  std::string value {};
  std::string_view expected {};
  std::string detail {};
  std::optional<Uid> block {};
};

namespace detail
{
template<typename ElementLP>
[[nodiscard]] LpBuildErrorInfo lp_error_context(const ElementLP& element)
{
  const auto& [euid, ename] = element.id();
  return LpBuildErrorInfo {
      .class_name = std::string {ElementLP::Element::class_name.full_name()},
      .uid = static_cast<Uid>(euid),
      .name = std::string {ename},
  };
}
}  // namespace detail

/// Throw an `LpBuildError` for @p element at (@p scenario, @p stage).
///
/// The one way an `add_to_lp` reports a failure (see the file header).
template<typename ElementLP, typename ScenarioT, typename StageT>
[[noreturn]] void fail_lp(const ElementLP& element,
                          const ScenarioT& scenario,
                          const StageT& stage,
                          LpFailure failure)
{
  auto info = detail::lp_error_context(element);
  info.scenario = static_cast<Uid>(scenario.uid());
  info.stage = static_cast<Uid>(stage.uid());
  info.block = failure.block;
  info.field = std::string {failure.field};
  info.value = std::move(failure.value);
  info.expected = std::string {failure.expected};
  info.detail = std::move(failure.detail);
  throw LpBuildError(std::move(info));
}

}  // namespace gtopt
