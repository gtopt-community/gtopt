// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_aperture_chunk_mode.cpp
 * @brief     Unit tests for the named `aperture_chunk_mode` option
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Covers the `ApertureChunkMode` enum (`per_scene` default /
 * `per_aperture` / `chunked`), its JSON + CLI parsing, the legacy integer
 * `aperture_chunk_size` back-compat mapping and contradiction flags in
 * `resolve_aperture_chunk()`, and the cascade per-level override helper
 * `apply_aperture_chunk_override()`.
 */

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_cascade_options.hpp>
#include <gtopt/json/json_sddp_options.hpp>
#include <gtopt/main_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/sddp_aperture.hpp>
#include <gtopt/sddp_types.hpp>

using namespace gtopt;

namespace
{

po::variables_map parse_cli(const std::vector<std::string>& args)
{
  const auto desc = make_options_description();
  po::variables_map vm;
  po::store(po::command_line_parser(args).options(desc), vm);
  po::notify(vm);
  return vm;
}

// Problem shape used by the resolution table: 16 apertures per phase,
// 16 scenes, 20 cores (a large hydrothermal shape — auto K = 4).
constexpr int kAmax = 16;
constexpr int kScenes = 16;
constexpr int kCores = 20;

constexpr auto resolve(std::optional<ApertureChunkMode> mode, int size)
{
  return resolve_aperture_chunk(mode, size, kAmax, kScenes, kCores);
}

}  // namespace

TEST_CASE("ApertureChunkMode - names and default")  // NOLINT
{
  CHECK(enum_name(ApertureChunkMode::per_scene) == "per_scene");
  CHECK(enum_name(ApertureChunkMode::per_aperture) == "per_aperture");
  CHECK(enum_name(ApertureChunkMode::chunked) == "chunked");
  CHECK(enum_from_name<ApertureChunkMode>("per_scene")
        == ApertureChunkMode::per_scene);
  CHECK(enum_from_name<ApertureChunkMode>("per_aperture")
        == ApertureChunkMode::per_aperture);
  CHECK(enum_from_name<ApertureChunkMode>("chunked")
        == ApertureChunkMode::chunked);
  CHECK_FALSE(enum_from_name<ApertureChunkMode>("serial").has_value());
  CHECK_THROWS_AS(
      (void)require_enum<ApertureChunkMode>("aperture_chunk_mode", "serial"),
      std::invalid_argument);
  CHECK(default_aperture_chunk_mode == ApertureChunkMode::per_scene);
}

TEST_CASE("aperture_chunk_mode - JSON parse and round-trip")  // NOLINT
{
  SUBCASE("named mode parses")
  {
    constexpr std::string_view json =
        R"({"aperture_chunk_mode":"chunked","aperture_chunk_size":4})";
    const auto opts = daw::json::from_json<SddpOptions>(json);
    CHECK(opts.aperture_chunk_mode.value_or(ApertureChunkMode::per_scene)
          == ApertureChunkMode::chunked);
    CHECK(opts.aperture_chunk_size.value_or(0) == 4);

    const auto rt = daw::json::from_json<SddpOptions>(
        std::string_view {daw::json::to_json(opts)});
    CHECK(rt.aperture_chunk_mode == opts.aperture_chunk_mode);
    CHECK(rt.aperture_chunk_size == opts.aperture_chunk_size);
  }

  SUBCASE("legacy integer alone still parses, mode stays unset")
  {
    constexpr std::string_view json = R"({"aperture_chunk_size":-1})";
    const auto opts = daw::json::from_json<SddpOptions>(json);
    CHECK_FALSE(opts.aperture_chunk_mode.has_value());
    CHECK(opts.aperture_chunk_size.value_or(0) == -1);
  }

  SUBCASE("unknown name is rejected")
  {
    constexpr std::string_view json = R"({"aperture_chunk_mode":"serial"})";
    CHECK_THROWS((void)daw::json::from_json<SddpOptions>(json));
  }

  SUBCASE("absent → accessors report unset / 0")
  {
    const PlanningOptionsLP plo {};
    CHECK_FALSE(plo.sddp_aperture_chunk_mode().has_value());
    CHECK(plo.sddp_aperture_chunk_size() == 0);
  }

  SUBCASE("merge: overlay mode wins, base kept when absent")
  {
    SddpOptions base {.aperture_chunk_mode = ApertureChunkMode::per_aperture};
    base.merge(SddpOptions {.aperture_chunk_mode = ApertureChunkMode::chunked});
    CHECK(base.aperture_chunk_mode == ApertureChunkMode::chunked);
    base.merge(SddpOptions {});
    CHECK(base.aperture_chunk_mode == ApertureChunkMode::chunked);
  }

  SUBCASE("cascade level parses the name")
  {
    constexpr std::string_view json =
        R"({"aperture_chunk_mode":"per_aperture","max_iterations":3})";
    const auto lvl = daw::json::from_json<CascadeLevelMethod>(json);
    CHECK(lvl.aperture_chunk_mode.value_or("") == "per_aperture");
    CHECK_FALSE(lvl.aperture_chunk_size.has_value());
  }
}

TEST_CASE("resolve_aperture_chunk - default is per_scene")  // NOLINT
{
  // Mode unset + size unset (0) — and the former "auto" 0 — both resolve
  // to per_scene silently: K = A_max, one task per (scene, phase).
  constexpr auto r = resolve(std::nullopt, 0);
  static_assert(r.mode == ApertureChunkMode::per_scene);
  CHECK(r.chunk_size == kAmax);
  CHECK_FALSE(r.legacy_size);
  CHECK_FALSE(r.size_ignored);
  CHECK_FALSE(r.invalid_size);
  CHECK_FALSE(r.auto_size);
}

TEST_CASE("resolve_aperture_chunk - explicit modes")  // NOLINT
{
  SUBCASE("per_scene → K = A_max")
  {
    const auto r = resolve(ApertureChunkMode::per_scene, 0);
    CHECK(r.mode == ApertureChunkMode::per_scene);
    CHECK(r.chunk_size == kAmax);
    CHECK_FALSE(r.legacy_size);
    CHECK_FALSE(r.size_ignored);
  }
  SUBCASE("per_aperture → K = 1")
  {
    const auto r = resolve(ApertureChunkMode::per_aperture, 0);
    CHECK(r.mode == ApertureChunkMode::per_aperture);
    CHECK(r.chunk_size == 1);
    CHECK_FALSE(r.size_ignored);
  }
  SUBCASE("chunked with K")
  {
    const auto r = resolve(ApertureChunkMode::chunked, 4);
    CHECK(r.mode == ApertureChunkMode::chunked);
    CHECK(r.chunk_size == 4);
    CHECK_FALSE(r.auto_size);
    CHECK_FALSE(r.legacy_size);
  }
  SUBCASE("chunked K is clamped to A_max")
  {
    CHECK(resolve(ApertureChunkMode::chunked, 99).chunk_size == kAmax);
  }
  SUBCASE("chunked without K → auto formula")
  {
    const auto r = resolve(ApertureChunkMode::chunked, 0);
    CHECK(r.auto_size);
    CHECK(r.chunk_size
          == compute_auto_aperture_chunk_size(kAmax, kScenes, kCores));
    CHECK(r.chunk_size == 4);
  }
}

TEST_CASE("resolve_aperture_chunk - legacy integer compat")  // NOLINT
{
  SUBCASE("-1 → per_scene (deprecated)")
  {
    const auto r = resolve(std::nullopt, -1);
    CHECK(r.mode == ApertureChunkMode::per_scene);
    CHECK(r.chunk_size == kAmax);
    CHECK(r.legacy_size);
  }
  SUBCASE("1 → per_aperture (deprecated)")
  {
    const auto r = resolve(std::nullopt, 1);
    CHECK(r.mode == ApertureChunkMode::per_aperture);
    CHECK(r.chunk_size == 1);
    CHECK(r.legacy_size);
  }
  SUBCASE("K > 1 → chunked K (deprecated)")
  {
    const auto r = resolve(std::nullopt, 8);
    CHECK(r.mode == ApertureChunkMode::chunked);
    CHECK(r.chunk_size == 8);
    CHECK(r.legacy_size);
  }
  SUBCASE("< -1 is invalid → default per_scene")
  {
    const auto r = resolve(std::nullopt, -7);
    CHECK(r.mode == ApertureChunkMode::per_scene);
    CHECK(r.invalid_size);
    CHECK_FALSE(r.legacy_size);
  }
}

TEST_CASE("resolve_aperture_chunk - contradictory combinations")  // NOLINT
{
  SUBCASE("per_scene + K=4 → K ignored")
  {
    const auto r = resolve(ApertureChunkMode::per_scene, 4);
    CHECK(r.mode == ApertureChunkMode::per_scene);
    CHECK(r.chunk_size == kAmax);
    CHECK(r.size_ignored);
  }
  SUBCASE("per_scene + legacy -1 is consistent")
  {
    CHECK_FALSE(resolve(ApertureChunkMode::per_scene, -1).size_ignored);
  }
  SUBCASE("per_aperture + K=4 → K ignored")
  {
    const auto r = resolve(ApertureChunkMode::per_aperture, 4);
    CHECK(r.chunk_size == 1);
    CHECK(r.size_ignored);
  }
  SUBCASE("per_aperture + 1 is consistent")
  {
    CHECK_FALSE(resolve(ApertureChunkMode::per_aperture, 1).size_ignored);
  }
  SUBCASE("chunked + -1 → treated as A_max, flagged")
  {
    const auto r = resolve(ApertureChunkMode::chunked, -1);
    CHECK(r.chunk_size == kAmax);
    CHECK(r.size_ignored);
  }
  SUBCASE("chunked + invalid negative → auto K, flagged")
  {
    const auto r = resolve(ApertureChunkMode::chunked, -3);
    CHECK(r.auto_size);
    CHECK(r.invalid_size);
  }
}

TEST_CASE("resolve_aperture_chunk - no apertures collapses to 1")  // NOLINT
{
  CHECK(resolve_aperture_chunk(std::nullopt, 0, 0, kScenes, kCores).chunk_size
        == 1);
  CHECK(
      resolve_aperture_chunk(ApertureChunkMode::chunked, 4, 0, 1, 1).chunk_size
      == 1);
}

TEST_CASE("apply_aperture_chunk_override - cascade level semantics")  // NOLINT
{
  SUBCASE("no override keeps the inherited values")
  {
    SDDPOptions o {};
    o.aperture_chunk_mode = ApertureChunkMode::chunked;
    o.aperture_chunk_size = 4;
    apply_aperture_chunk_override(o, std::nullopt, std::nullopt);
    CHECK(o.aperture_chunk_mode == ApertureChunkMode::chunked);
    CHECK(o.aperture_chunk_size == 4);
  }
  SUBCASE("non-chunked mode drops an inherited K")
  {
    SDDPOptions o {};
    o.aperture_chunk_mode = ApertureChunkMode::chunked;
    o.aperture_chunk_size = 4;
    apply_aperture_chunk_override(o, ApertureChunkMode::per_scene, {});
    CHECK(o.aperture_chunk_mode == ApertureChunkMode::per_scene);
    CHECK(o.aperture_chunk_size == 0);
    CHECK_FALSE(
        resolve(o.aperture_chunk_mode, o.aperture_chunk_size).size_ignored);
  }
  SUBCASE("chunked mode + K at the level")
  {
    SDDPOptions o {};
    apply_aperture_chunk_override(o, ApertureChunkMode::chunked, 8);
    CHECK(o.aperture_chunk_mode == ApertureChunkMode::chunked);
    CHECK(o.aperture_chunk_size == 8);
  }
  SUBCASE("legacy K alone under inherited chunked only changes K")
  {
    SDDPOptions o {};
    o.aperture_chunk_mode = ApertureChunkMode::chunked;
    o.aperture_chunk_size = 4;
    apply_aperture_chunk_override(o, std::nullopt, 2);
    CHECK(o.aperture_chunk_mode == ApertureChunkMode::chunked);
    CHECK(o.aperture_chunk_size == 2);
  }
  SUBCASE("legacy -1 alone re-selects the mode")
  {
    SDDPOptions o {};
    o.aperture_chunk_mode = ApertureChunkMode::per_aperture;
    apply_aperture_chunk_override(o, std::nullopt, -1);
    CHECK_FALSE(o.aperture_chunk_mode.has_value());
    CHECK(o.aperture_chunk_size == -1);
    CHECK(resolve(o.aperture_chunk_mode, o.aperture_chunk_size).mode
          == ApertureChunkMode::per_scene);
  }
}

TEST_CASE("--aperture-chunk-mode CLI")  // NOLINT
{
  SUBCASE("name is forwarded to sddp_options")
  {
    const auto vm = parse_cli({"--aperture-chunk-mode", "per_aperture"});
    const auto opts = parse_main_options(vm, {});
    CHECK(opts.sddp_aperture_chunk_mode.value_or("") == "per_aperture");

    Planning planning {};
    apply_cli_options(planning, opts);
    CHECK(planning.options.sddp_options.aperture_chunk_mode
          == ApertureChunkMode::per_aperture);
    CHECK_FALSE(planning.options.sddp_options.aperture_chunk_size.has_value());
  }

  SUBCASE("mode + size together")
  {
    const auto vm = parse_cli(
        {"--aperture-chunk-mode", "chunked", "--aperture-chunk-size", "4"});
    Planning planning {};
    apply_cli_options(planning, parse_main_options(vm, {}));
    CHECK(planning.options.sddp_options.aperture_chunk_mode
          == ApertureChunkMode::chunked);
    CHECK(planning.options.sddp_options.aperture_chunk_size.value_or(0) == 4);
  }

  SUBCASE("legacy size alone leaves the mode unset")
  {
    const auto vm = parse_cli({"--aperture-chunk-size", "1"});
    Planning planning {};
    apply_cli_options(planning, parse_main_options(vm, {}));
    CHECK_FALSE(planning.options.sddp_options.aperture_chunk_mode.has_value());
    CHECK(planning.options.sddp_options.aperture_chunk_size.value_or(0) == 1);
  }

  SUBCASE("unknown name is rejected when applied")
  {
    const auto vm = parse_cli({"--aperture-chunk-mode", "serial"});
    Planning planning {};
    CHECK_THROWS_AS(apply_cli_options(planning, parse_main_options(vm, {})),
                    std::invalid_argument);
  }
}
