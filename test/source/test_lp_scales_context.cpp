/**
 * @file      test_lp_scales_context.cpp
 * @brief     Unit tests for to_scales_context() (LpContext -> sidecar form)
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <optional>

#include <doctest/doctest.h>
#include <gtopt/iteration.hpp>
#include <gtopt/lp_context.hpp>
#include <gtopt/lp_scales.hpp>

using namespace gtopt;

namespace
{

/// Unwrap an optional after a REQUIRE without tripping
/// bugprone-unchecked-optional-access.
template<class T>
[[nodiscard]] auto lsc_val(const std::optional<T>& o) -> T
{
  return o.value_or(T {});
}

}  // namespace

TEST_CASE("to_scales_context maps every LpContext alternative")  // NOLINT
{
  SUBCASE("monostate has no sidecar context")
  {
    CHECK_FALSE(to_scales_context(LpContext {}).has_value());
  }

  SUBCASE("stage")
  {
    const auto c = to_scales_context(
        LpContext {StageContext {make_uid<Scenario>(1), make_uid<Stage>(2)}});
    REQUIRE(c.has_value());
    CHECK(lsc_val(c).type == "stage");
    CHECK(lsc_val(c).scenario.value_or(-1) == 1);
    CHECK(lsc_val(c).stage.value_or(-1) == 2);
    CHECK_FALSE(lsc_val(c).block.has_value());
    CHECK_FALSE(lsc_val(c).scene.has_value());
  }

  SUBCASE("block")
  {
    const auto c = to_scales_context(LpContext {BlockContext {
        make_uid<Scenario>(1), make_uid<Stage>(2), make_uid<Block>(3)}});
    REQUIRE(c.has_value());
    CHECK(lsc_val(c).type == "block");
    CHECK(lsc_val(c).block.value_or(-1) == 3);
    CHECK_FALSE(lsc_val(c).extra.has_value());
  }

  SUBCASE("block_ex")
  {
    const auto c = to_scales_context(LpContext {BlockExContext {
        make_uid<Scenario>(1), make_uid<Stage>(2), make_uid<Block>(3), 4}});
    REQUIRE(c.has_value());
    CHECK(lsc_val(c).type == "block_ex");
    CHECK(lsc_val(c).scenario.value_or(-1) == 1);
    CHECK(lsc_val(c).stage.value_or(-1) == 2);
    CHECK(lsc_val(c).block.value_or(-1) == 3);
    CHECK(lsc_val(c).extra.value_or(-1) == 4);
  }

  SUBCASE("scene_phase")
  {
    const auto c = to_scales_context(
        LpContext {ScenePhaseContext {make_uid<Scene>(5), make_uid<Phase>(6)}});
    REQUIRE(c.has_value());
    CHECK(lsc_val(c).type == "scene_phase");
    CHECK(lsc_val(c).scene.value_or(-1) == 5);
    CHECK(lsc_val(c).phase.value_or(-1) == 6);
    CHECK_FALSE(lsc_val(c).scenario.has_value());
  }

  SUBCASE("phase (with element uid)")
  {
    const auto c = to_scales_context(LpContext {
        PhaseContext {make_uid<Scene>(5), make_uid<Phase>(6), Uid {7}}});
    REQUIRE(c.has_value());
    CHECK(lsc_val(c).type == "phase");
    CHECK(lsc_val(c).element.value_or(-1) == 7);
  }

  SUBCASE("iteration")
  {
    const auto c = to_scales_context(LpContext {IterationContext {
        make_uid<Scene>(5), make_uid<Phase>(6), make_uid<Iteration>(8), 9}});
    REQUIRE(c.has_value());
    CHECK(lsc_val(c).type == "iteration");
    CHECK(lsc_val(c).scene.value_or(-1) == 5);
    CHECK(lsc_val(c).phase.value_or(-1) == 6);
    CHECK(lsc_val(c).iteration.value_or(-1) == 8);
    CHECK(lsc_val(c).extra.value_or(-1) == 9);
  }

  SUBCASE("aperture")
  {
    const auto c = to_scales_context(LpContext {ApertureContext {
        make_uid<Scene>(5), make_uid<Phase>(6), make_uid<Scenario>(10), 11}});
    REQUIRE(c.has_value());
    CHECK(lsc_val(c).type == "aperture");
    CHECK(lsc_val(c).aperture.value_or(-1) == 10);
    CHECK(lsc_val(c).extra.value_or(-1) == 11);
    CHECK_FALSE(lsc_val(c).iteration.has_value());
  }
}
