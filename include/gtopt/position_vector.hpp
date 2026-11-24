/**
 * @file      position_vector.hpp
 * @brief     std::vector addressed by ONE strong positional index type
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * gtopt's positional strong indices (`StageIndex`, `PhaseStageIndex`,
 * `ScenarioIndex`, ...) are `implicitly_convertible_to<Index>`, so a plain
 * `std::vector<T>` accepts ANY of them in `operator[]`: indexing the
 * global stage array with a phase-LOCAL stage position compiled silently
 * (the `SimulationLP::prev_stage` bug).  `PositionVector<IndexT, T>` is a
 * `std::vector<T>` (public base: ranges, spans, iteration and `size()`
 * work unchanged) whose element access
 *
 *   * accepts its own `IndexT`,
 *   * accepts a raw `std::size_t` (explicit escape hatch, e.g. the
 *     result of `to_size(idx)` or a loop counter),
 *   * REJECTS every other strong index type at compile time.
 */

#pragma once

#include <concepts>
#include <cstddef>
#include <vector>

namespace gtopt
{

/// A rollbear `strong::type` (anything exposing `value_of()`).
template<typename T>
concept StrongIndexLike = requires(const T& t) { t.value_of(); };

template<typename IndexT, typename T>
class PositionVector : public std::vector<T>
{
  using base_t = std::vector<T>;

public:
  using index_type = IndexT;
  using base_t::base_t;

  constexpr PositionVector() noexcept = default;
  // NOLINTNEXTLINE(google-explicit-constructor,hicpp-explicit-conversions)
  constexpr PositionVector(base_t v) noexcept
      : base_t(std::move(v))
  {
  }

  [[nodiscard]] constexpr auto operator[](IndexT pos) noexcept
      -> base_t::reference
  {
    return base_t::operator[](static_cast<std::size_t>(pos.value_of()));
  }
  [[nodiscard]] constexpr auto operator[](IndexT pos) const noexcept
      -> base_t::const_reference
  {
    return base_t::operator[](static_cast<std::size_t>(pos.value_of()));
  }
  [[nodiscard]] constexpr auto operator[](std::size_t pos) noexcept
      -> base_t::reference
  {
    return base_t::operator[](pos);
  }
  [[nodiscard]] constexpr auto operator[](std::size_t pos) const noexcept
      -> base_t::const_reference
  {
    return base_t::operator[](pos);
  }
  /// A strong index of ANOTHER kind (e.g. a phase-local stage position
  /// into the global stage array) is a type error, not a conversion.
  template<StrongIndexLike Other>
    requires(!std::same_as<std::remove_cvref_t<Other>, IndexT>)
  auto operator[](Other) -> void = delete;
  template<StrongIndexLike Other>
    requires(!std::same_as<std::remove_cvref_t<Other>, IndexT>)
  auto operator[](Other) const -> void = delete;

  [[nodiscard]] constexpr auto at(IndexT pos) -> base_t::reference
  {
    return base_t::at(static_cast<std::size_t>(pos.value_of()));
  }
  [[nodiscard]] constexpr auto at(IndexT pos) const -> base_t::const_reference
  {
    return base_t::at(static_cast<std::size_t>(pos.value_of()));
  }
  [[nodiscard]] constexpr auto at(std::size_t pos) -> base_t::reference
  {
    return base_t::at(pos);
  }
  [[nodiscard]] constexpr auto at(std::size_t pos) const
      -> base_t::const_reference
  {
    return base_t::at(pos);
  }
  template<StrongIndexLike Other>
    requires(!std::same_as<std::remove_cvref_t<Other>, IndexT>)
  auto at(Other) -> void = delete;
  template<StrongIndexLike Other>
    requires(!std::same_as<std::remove_cvref_t<Other>, IndexT>)
  auto at(Other) const -> void = delete;
};

}  // namespace gtopt
