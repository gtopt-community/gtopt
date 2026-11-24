/**
 * @file      active_ref.hpp
 * @brief     Tolerant cross-element references for `add_to_lp` / `update_lp`
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Reference rule (R4 of the add_to_lp audit): a reference to another
 * element that is inactive in the stage, or whose per-(scenario, stage)
 * or per-block LP entity was elided (zero-bound skip, inactive cell),
 * never throws.  The consumer omits the dependent term (= 0) and, when
 * the omission is not a routine elision, says so ONCE per element via
 * `warn_ref_once`.
 *
 *     const auto* junction = active_ref(sc, JunctionLPSId {ref}, stage);
 *     if (junction == nullptr) { warn_ref_once(...); return true; }
 *     const auto* rows = junction->find_balance_rows(scenario, stage);
 *
 * An UNRESOLVABLE reference (unknown uid / name) is a referential-
 * integrity error caught by `validate_planning`; `active_ref` keeps
 * throwing for it (through `SystemContext::element`).
 */

#pragma once

#include <format>
#include <string_view>
#include <type_traits>

#include <gtopt/basic_types.hpp>
#include <gtopt/warn_once.hpp>

namespace gtopt
{

/// The referenced element when it is active in @p stage, else nullptr.
///
/// @p sc is a `SystemContext` (or anything with `element(id)`); @p id is
/// any id accepted by `SystemContext::element` (`ObjectSingleId<XLP>`,
/// `ElementIndex<XLP>`).
template<typename SystemContextT, typename IdT, typename StageT>
[[nodiscard]] auto active_ref(const SystemContextT& sc,
                              const IdT& id,
                              const StageT& stage)
    -> const std::remove_cvref_t<decltype(sc.element(id))>*
{
  const auto& element = sc.element(id);
  return element.is_active(stage) ? &element : nullptr;
}

/// One warning per (element, reason) for a reference whose term was
/// omitted because the referenced element is inactive / not built.
///
/// @param cname    class of the element doing the referencing
/// @param uid      its uid
/// @param ref_what what was omitted, e.g. "waterway flow term"
/// @param ref_uid  uid of the referenced element (for the message)
inline void warn_ref_once(std::string_view cname,
                          Uid uid,
                          std::string_view ref_what,
                          Uid ref_uid)
{
  warn_once_per_key(
      std::format("ref:{}:{}:{}:{}", cname, uid, ref_what, ref_uid),
      "{} uid={}: referenced element uid={} is inactive or has no LP "
      "entity in some (scenario, stage) cell; the {} is omitted there "
      "(treated as 0).  Logged once per element.",
      cname,
      uid,
      ref_uid,
      ref_what);
}

}  // namespace gtopt
