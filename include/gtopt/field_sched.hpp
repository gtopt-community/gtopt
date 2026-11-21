/**
 * @file      schedule.hpp
 * @brief     Header of FieldSched type
 * @date      Sat Mar 22 05:49:49 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module provides FieldSched types.
 */

#pragma once

#include <charconv>
#include <cstddef>
#include <format>
#include <optional>
#include <string_view>
#include <variant>
#include <vector>

#include <gtopt/basic_types.hpp>

namespace gtopt
{
using FileSched = String;

template<typename Type, typename Vector = std::vector<Type>>
using FieldSched = std::variant<Type, Vector, FileSched>;

using RealFieldSched = FieldSched<Real>;
using OptRealFieldSched = std::optional<RealFieldSched>;

using IntFieldSched = FieldSched<Int>;
using OptIntFieldSched = std::optional<IntFieldSched>;

using BoolFieldSched = FieldSched<Bool>;
using OptBoolFieldSched = std::optional<BoolFieldSched>;

using IntBoolFieldSched = FieldSched<IntBool>;
using OptIntBoolFieldSched = std::optional<IntBoolFieldSched>;

using Active = IntBoolFieldSched;
using OptActive = OptIntBoolFieldSched;

template<typename Type, typename Vector2 = std::vector<std::vector<Type>>>
using FieldSched2 = FieldSched<Type, Vector2>;

using RealFieldSched2 = FieldSched2<double>;
using OptRealFieldSched2 = std::optional<RealFieldSched2>;

// Two-level (stage, block) IntBool schedule — the per-block analog of the
// single-level `IntBoolFieldSched` used by `Active`.  Backs per-block binary
// flags such as `Line.in_service` (line open/closed by block) without
// touching the per-stage `active` element-activation schedule.
using IntBoolFieldSched2 = FieldSched2<IntBool>;
using OptIntBoolFieldSched2 = std::optional<IntBoolFieldSched2>;

template<typename Type,
         typename Vector3 = std::vector<std::vector<std::vector<Type>>>>
using FieldSched3 = FieldSched<Type, Vector3>;

using RealFieldSched3 = FieldSched3<double>;
using OptRealFieldSched3 = std::optional<RealFieldSched3>;

using TRealFieldSched = RealFieldSched;
using TBRealFieldSched = RealFieldSched2;
using STRealFieldSched = RealFieldSched2;
using STBRealFieldSched = RealFieldSched3;

using OptTRealFieldSched = OptRealFieldSched;
using OptTBRealFieldSched = OptRealFieldSched2;
using OptSTRealFieldSched = OptRealFieldSched2;
using OptSTBRealFieldSched = OptRealFieldSched3;

using TBIntBoolFieldSched = IntBoolFieldSched2;
using OptTBIntBoolFieldSched = OptIntBoolFieldSched2;

// ─── Uid-valued per-(stage, block) schedule ───────────────────────────────
//
// Companion to `OptTBRealFieldSched` for FK fields whose value is a Uid
// (a strong-type alias for `int32_t`).  Used by `Generator.fuel_per_block`
// to override the static `Generator.fuel` on a per-(stage, block) basis
// without touching the `OptSingleId` variant (which carries both Uid AND
// Name string forms — the Name string would be JSON-indistinguishable
// from a `FileSched` string).  Uid-only sidesteps the ambiguity:
//
//   * scalar  -> JSON `123`                  (broadcast all stages/blocks)
//   * matrix  -> JSON `[[uid_b0, uid_b1, ...], ...]`  (per-stage rows)
//   * file    -> JSON `"fuel_uids.parquet"`  (Parquet/CSV schedule)
//
// See Generator.fuel_per_block (generator.hpp) for the consumer side and
// Issue #510 Phase 1 for the design rationale.
using UidFieldSched = FieldSched<Uid>;
using OptUidFieldSched = std::optional<UidFieldSched>;

using UidFieldSched2 = FieldSched2<Uid>;
using OptUidFieldSched2 = std::optional<UidFieldSched2>;

using TBUidFieldSched = UidFieldSched2;
using OptTBUidFieldSched = OptUidFieldSched2;
// Backwards-compatible short alias used by Generator.fuel_per_block.
using TBUidSched = UidFieldSched2;
using OptTBUidSched = OptUidFieldSched2;

// ─── File-schedule references ───────────────────────────────────────────────
//
// A `FileSched` string names a long-layout input table.  Full grammar:
//
//   "<field>"                  -> <input_dir>/<OwnerClass>/<field>, rows of
//                                 the owning element's own uid
//   "<Class>@<field>"          -> <input_dir>/<Class>/<field>, rows of the
//                                 owning element's own uid
//   "<Class>@<field>#<uid>"    -> <input_dir>/<Class>/<field>, rows of
//                                 element <uid> (uid override)
//
// The uid override lets a synthesized element read its parent's table
// verbatim: `System::expand_batteries` rewrites a battery's `"act"` into
// `"Battery@act#<battery uid>"` before copying it to `<bat>_gen`,
// `<bat>_dem`, `<bat>_conv` and `uc_<bat>_gen`, so those elements resolve
// the schedule in the Battery context instead of looking up their own
// (non-existent) Generator/Demand/Converter/Commitment table and uid.

/// Parsed form of a `FileSched` reference (see grammar above).  The views
/// alias the inputs of `parse_file_sched_ref`.
struct FileSchedRef
{
  std::string_view class_name;  ///< table sub-directory
  std::string_view field_name;  ///< table file stem
  std::optional<Uid> uid {};  ///< element uid override (`#<uid>`)
};

/// Split @p fsched into (class directory, field stem, optional uid).
/// @p owner_class is used when no `@` is present.  A `#` suffix that is
/// not a plain integer is kept as part of the field stem (no override).
[[nodiscard]] constexpr auto parse_file_sched_ref(std::string_view owner_class,
                                                  std::string_view fsched)
    -> FileSchedRef
{
  FileSchedRef ref {
      .class_name = owner_class,
      .field_name = fsched,
  };
  if (const auto hash = fsched.rfind('#'); hash != std::string_view::npos) {
    const auto digits = fsched.substr(hash + 1);
    Uid uid {};
    const auto* const last = digits.data() + digits.size();
    if (const auto [ptr, ec] = std::from_chars(digits.data(), last, uid);
        !digits.empty() && ec == std::errc {} && ptr == last)
    {
      ref.uid = uid;
      ref.field_name = fsched.substr(0, hash);
    }
  }
  if (const auto at = ref.field_name.find('@'); at != std::string_view::npos) {
    ref.class_name = ref.field_name.substr(0, at);
    ref.field_name = ref.field_name.substr(at + 1);
  }
  return ref;
}

/// Build a `FileSched` that resolves @p fsched exactly as element @p uid of
/// class @p owner_class would: `"<Class>@<field>#<uid>"`.  A reference that
/// already carries a uid override is returned unchanged (idempotent).
[[nodiscard]] inline auto make_file_sched_ref(std::string_view owner_class,
                                              std::string_view fsched,
                                              Uid uid) -> FileSched
{
  const auto ref = parse_file_sched_ref(owner_class, fsched);
  if (ref.uid.has_value()) {
    return FileSched {fsched};
  }
  return std::format("{}@{}#{}", ref.class_name, ref.field_name, uid);
}

/// Copy an optional field schedule from a parent element of class
/// @p owner_class / uid @p uid onto a synthesized child element.  Inline
/// scalars and vectors are copied verbatim; a file reference is rewritten
/// with `make_file_sched_ref` so the child still reads the parent's table
/// rows instead of its own class directory and uid.
template<typename T, typename Vector>
[[nodiscard]] auto inherit_field_sched(
    const std::optional<FieldSched<T, Vector>>& sched,
    std::string_view owner_class,
    Uid uid) -> std::optional<FieldSched<T, Vector>>
{
  auto out = sched;
  if (out.has_value()) {
    if (auto* const file = std::get_if<FileSched>(&*out)) {
      *file = make_file_sched_ref(owner_class, *file, uid);
    }
  }
  return out;
}

// ─── Diagnostics: in-memory footprint of a FieldSched ───────────────────────
//
// Walks the variant of an OptT*FieldSched and returns the dynamic bytes
// reachable from it (vector capacities × element size).  Used by the
// memory-attribution log around `PlanningLP` construction to estimate
// the parquet → vector materialisation cost without enumerating every
// element-class field by name.  Pure inspection — no allocation.
//
// Limitations:
//  * Reports `std::vector<T>::capacity()` not `size()`, since capacity
//    is what holds the heap allocation.  In practice plp2gtopt-emitted
//    cases populate vectors by `reserve()` + `push_back()`, so capacity
//    closely tracks size.
//  * `FileSched` (string variant) is reported as `s.capacity()` only —
//    the on-disk parquet weight is not counted (it's not in this
//    process's RSS).
//  * Only counts the dynamic spillover; the inline `sizeof(variant)`
//    overhead is reported separately by sizeof on the parent struct.
template<typename T, typename Vector>
[[nodiscard]] constexpr std::size_t field_sched_dynamic_bytes(
    const FieldSched<T, Vector>& fs) noexcept
{
  return std::visit(
      []<typename U>(const U& v) -> std::size_t
      {
        // The scalar (`T`) branch and the defensive final `else`
        // (unknown variant) both return 0, which bugprone-branch-clone
        // flags as a repeated body — but they are distinct, documented
        // type-dispatch cases in an `if constexpr` chain, not a copy-paste
        // slip.  Keeping them separate preserves the per-alternative
        // intent; merging would obscure it.
        // NOLINTNEXTLINE(bugprone-branch-clone)
        if constexpr (std::is_same_v<U, T>) {
          return 0;  // Inline scalar — already counted by sizeof(parent).
        } else if constexpr (std::is_same_v<U, std::vector<T>>) {
          return v.capacity() * sizeof(T);
        } else if constexpr (std::is_same_v<U, std::vector<std::vector<T>>>) {
          std::size_t bytes = v.capacity() * sizeof(std::vector<T>);
          for (const auto& inner : v) {
            bytes += inner.capacity() * sizeof(T);
          }
          return bytes;
        } else if constexpr (std::is_same_v<
                                 U,
                                 std::vector<std::vector<std::vector<T>>>>)
        {
          std::size_t bytes =
              v.capacity() * sizeof(std::vector<std::vector<T>>);
          for (const auto& mid : v) {
            bytes += mid.capacity() * sizeof(std::vector<T>);
            for (const auto& inner : mid) {
              bytes += inner.capacity() * sizeof(T);
            }
          }
          return bytes;
        } else if constexpr (std::is_same_v<U, FileSched>) {
          return v.capacity();  // string capacity in bytes
        } else {
          return 0;  // Unknown variant — be conservative.
        }
      },
      fs);
}

template<typename T, typename Vector>
[[nodiscard]] constexpr std::size_t field_sched_dynamic_bytes(
    const std::optional<FieldSched<T, Vector>>& opt_fs) noexcept
{
  return opt_fs.has_value() ? field_sched_dynamic_bytes(*opt_fs) : 0;
}

}  // namespace gtopt
