// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      require_value.hpp
 * @brief     Checked access to a std::optional in tests
 * @date      2026-11-22
 *
 * clang-tidy's bugprone-unchecked-optional-access does not model doctest's
 * REQUIRE as non-returning, so `REQUIRE(opt.has_value()); use(*opt);` is
 * still reported.  `require_value(opt)` REQUIREs the value (so a failure is
 * reported as a normal assertion) and then guards the access with an
 * explicit check the analysis understands — no NOLINT needed.
 */

#pragma once

#include <optional>
#include <stdexcept>

#include <doctest/doctest.h>

namespace gtopt::test_support
{

/// REQUIRE that @p opt holds a value and return a reference to it.
template<typename T>
[[nodiscard]] auto require_value(std::optional<T>& opt) -> T&
{
  REQUIRE(opt.has_value());
  if (!opt.has_value()) {
    throw std::logic_error("require_value: empty std::optional");
  }
  return *opt;
}

/// Const overload of require_value().
template<typename T>
[[nodiscard]] auto require_value(const std::optional<T>& opt) -> const T&
{
  REQUIRE(opt.has_value());
  if (!opt.has_value()) {
    throw std::logic_error("require_value: empty std::optional");
  }
  return *opt;
}

/// Rvalue optionals would dangle: bind the optional to a name first.
template<typename T>
auto require_value(std::optional<T>&& opt) -> T& = delete;

}  // namespace gtopt::test_support
