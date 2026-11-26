// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_solver_version_pin.cpp
 * @brief     Runtime solver-version pin (HiGHS 1.16.0): decision function,
 *            env override, report, and explicit requests that cannot fall
 *            back to another solver
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <filesystem>
#include <format>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/plugin_check_default.hpp>
#include <gtopt/plugin_integrity.hpp>
#include <gtopt/solver_registry.hpp>

#include "config_test_helpers.hpp"

namespace cfg = gtopt::test::config;

using namespace gtopt;

namespace version_pin_test
{
namespace
{

const PluginIntegrityPolicy k_warn {.mode = PluginCheckMode::warning};
const PluginIntegrityPolicy k_err {.mode = PluginCheckMode::error};
constexpr SolverVersion k_v1_16_0 {.major = 1, .minor = 16, .patch = 0};
constexpr SolverVersion k_v1_16_1 {.major = 1, .minor = 16, .patch = 1};
constexpr SolverVersion k_v1_12_0 {.major = 1, .minor = 12, .patch = 0};
constexpr std::string_view k_lib = "/usr/lib/x86_64-linux-gnu/libhighs.so.1";

[[nodiscard]] IntegrityDecision pin_check(const SolverVersion& pinned,
                                          const SolverVersion& runtime,
                                          const PluginIntegrityPolicy& policy,
                                          VersionPinOverrides overrides = {})
{
  return check_solver_version_pin(
      "highs", pinned, runtime, k_lib, overrides, policy);
}

}  // namespace
}  // namespace version_pin_test

using namespace version_pin_test;

TEST_CASE("solver_allow_unpinned_env name")  // NOLINT
{
  CHECK(solver_allow_unpinned_env("highs") == "GTOPT_HIGHS_ALLOW_UNPINNED");
  CHECK(solver_allow_unpinned_env("cplex") == "GTOPT_CPLEX_ALLOW_UNPINNED");
}

TEST_CASE("parse_env_flag")  // NOLINT
{
  for (const auto* t : {"1", "true", "TRUE", "yes", "On"}) {
    CHECK(parse_env_flag(t) == std::optional<bool> {true});
  }
  for (const auto* f : {"", "0", "false", "No", "OFF"}) {
    CHECK(parse_env_flag(f) == std::optional<bool> {false});
  }
  CHECK_FALSE(parse_env_flag("maybe").has_value());
  CHECK_FALSE(parse_env_flag("2").has_value());
}

TEST_CASE("solver_allow_unpinned_from_env")  // NOLINT
{
  {
    const cfg::ScopedEnv env {"GTOPT_HIGHS_ALLOW_UNPINNED", std::nullopt};
    CHECK_FALSE(solver_allow_unpinned_from_env("highs"));
  }
  {
    const cfg::ScopedEnv env {"GTOPT_HIGHS_ALLOW_UNPINNED", "1"};
    CHECK(solver_allow_unpinned_from_env("highs"));
    // Per solver: another solver's variable is not affected.
    CHECK_FALSE(solver_allow_unpinned_from_env("cplex"));
  }
  {
    const cfg::ScopedEnv env {"GTOPT_HIGHS_ALLOW_UNPINNED", "0"};
    CHECK_FALSE(solver_allow_unpinned_from_env("highs"));
  }
  {
    // Unparsable: warns and counts as false (never silently allows).
    const cfg::ScopedEnv env {"GTOPT_HIGHS_ALLOW_UNPINNED", "perhaps"};
    CHECK_FALSE(solver_allow_unpinned_from_env("highs"));
  }
}

TEST_CASE("check_solver_version_pin decision")  // NOLINT
{
  SUBCASE("no pin: nothing to check, in both modes")
  {
    for (const auto& policy : {k_warn, k_err}) {
      const auto d = pin_check(SolverVersion {}, k_v1_12_0, policy);
      CHECK(d.verdict == IntegrityVerdict::ok);
      CHECK(d.status.empty());
      CHECK(d.message.empty());
    }
  }

  SUBCASE("runtime equals the pin: ok 'pinned'")
  {
    const auto d = pin_check(k_v1_16_0, k_v1_16_0, k_err);
    CHECK(d.verdict == IntegrityVerdict::ok);
    CHECK(d.status == "pinned");
    CHECK(d.message.empty());
  }

  SUBCASE("a pin without patch accepts any patch of that minor")
  {
    const SolverVersion pin_1_16 {.major = 1, .minor = 16};
    CHECK(pin_check(pin_1_16, k_v1_16_1, k_err).verdict
          == IntegrityVerdict::ok);
    CHECK(pin_check(pin_1_16, k_v1_12_0, k_err).verdict
          == IntegrityVerdict::refuse);
  }

  SUBCASE("unpinned runtime is refused in error mode")
  {
    const auto d = pin_check(k_v1_16_0, k_v1_12_0, k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "UNPINNED");
    // What was found, where, what is expected, how to fix / override.
    CHECK(d.message.contains("highs 1.12.0"));
    CHECK(d.message.contains(k_lib));
    CHECK(d.message.contains("pins highs 1.16.0"));
    CHECK(d.message.contains("-DHIGHS_ROOT_DIR="));
    CHECK(d.message.contains("~/opt/highs-1.16.0"));
    CHECK(d.message.contains("GTOPT_HIGHS_ALLOW_UNPINNED=1"));
    CHECK(d.message.contains("-DGTOPT_HIGHS_ALLOW_UNPINNED=ON"));
    CHECK(d.message.contains("--plugin-check=warning"));
    CHECK(d.message.contains(k_plugin_refused_marker));
  }

  SUBCASE("a patch-only difference from the pin is also refused")
  {
    // Unlike check_solver_version (ABI: patch differences are fine), the
    // pin is about the exact optimal vertex: the patch counts.
    const auto d = pin_check(k_v1_16_0, k_v1_16_1, k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "UNPINNED");
  }

  SUBCASE("plugin built with ALLOW_UNPINNED: warns")
  {
    const auto d =
        pin_check(k_v1_16_0, k_v1_12_0, k_err, {.built_allow_unpinned = true});
    CHECK(d.verdict == IntegrityVerdict::warn);
    CHECK(d.status == "UNPINNED");
    CHECK(d.message.contains("Loading it anyway"));
    CHECK(d.message.contains("built with -DGTOPT_HIGHS_ALLOW_UNPINNED=ON"));
    CHECK_FALSE(d.message.contains(k_plugin_refused_marker));
  }

  SUBCASE("env override: warns")
  {
    const auto d =
        pin_check(k_v1_16_0, k_v1_12_0, k_err, {.env_allow_unpinned = true});
    CHECK(d.verdict == IntegrityVerdict::warn);
    CHECK(d.message.contains("$GTOPT_HIGHS_ALLOW_UNPINNED is set"));
    CHECK_FALSE(d.message.contains(k_plugin_refused_marker));
  }

  SUBCASE("plugin check mode 'warning': warns")
  {
    const auto d = pin_check(k_v1_16_0, k_v1_12_0, k_warn);
    CHECK(d.verdict == IntegrityVerdict::warn);
    CHECK(d.message.contains("plugin check mode 'warning'"));
  }

  SUBCASE("unknown runtime version: cannot be verified, refused")
  {
    const auto d = pin_check(k_v1_16_0, SolverVersion {}, k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "pin unverified");
    CHECK(d.message.contains("cannot be verified"));
    CHECK(d.message.contains(k_lib));
    // Runtime patch unknown while the pin names one: also unverified.
    const SolverVersion no_patch {.major = 1, .minor = 16};
    CHECK(pin_check(k_v1_16_0, no_patch, k_err).status == "pin unverified");
    // ... and the overrides still apply.
    CHECK(pin_check(
              k_v1_16_0, SolverVersion {}, k_err, {.env_allow_unpinned = true})
              .verdict
          == IntegrityVerdict::warn);
  }

  SUBCASE("unknown library path is spelled out")
  {
    const auto d =
        check_solver_version_pin("highs", k_v1_16_0, k_v1_12_0, "", {}, k_err);
    CHECK(d.message.contains("(unknown path)"));
  }
}

TEST_CASE("describe_solver_library shows the pin")  // NOLINT
{
  SolverLibraryReport lib {.solver = "highs"};
  lib.reported = true;
  lib.compiled = k_v1_12_0;
  lib.runtime = k_v1_12_0;
  lib.library_path = std::string {k_lib};
  lib.version_check.status = "match";
  CHECK_FALSE(describe_solver_library(lib).contains("pin "));

  lib.pinned = k_v1_16_0;
  lib.pin_check.status = "UNPINNED";
  const auto s = describe_solver_library(lib);
  CHECK(s.contains(
      "runtime 1.12.0, compiled 1.12.0 (match), pin 1.16.0 (UNPINNED)"));
  CHECK(s.contains(k_lib));
}

TEST_CASE("solver_unavailable_message names the refusal")  // NOLINT
{
  const std::vector<std::string> errors {
      "Plugin /p/libgtopt_solver_highs.so: Solver 'highs': the loaded "
      "library /usr/lib/libhighs.so.1 is highs 1.12.0, but this gtopt pins "
      "highs 1.16.0 ... Refused (plugin check mode 'error'; ...).",
      "Plugin /p/libgtopt_solver_cplex.so: Solver 'cplex': other.",
      "Failed to load plugin /p/libgtopt_solver_highs.so: dlopen error",
  };
  const std::vector<std::string> available {"clp", "cbc"};

  SUBCASE("solver_load_errors filters by solver and plugin file")
  {
    const auto why = solver_load_errors("highs", errors);
    REQUIRE(why.size() == 2);
    CHECK(why[0].contains("pins highs 1.16.0"));
    CHECK(why[1].contains("dlopen error"));
    CHECK(solver_load_errors("gurobi", errors).empty());
  }

  SUBCASE("refused solver: the reason and the alternatives")
  {
    const auto msg = solver_unavailable_message("highs", available, errors);
    CHECK(msg.starts_with("Solver 'highs' not available."));
    CHECK(msg.contains("pins highs 1.16.0"));
    CHECK(msg.contains(k_plugin_refused_marker));
    CHECK_FALSE(msg.contains("'cplex'"));
    CHECK(msg.contains("Available solvers: clp, cbc"));
  }

  SUBCASE("unknown solver: unchanged one-line message")
  {
    CHECK(solver_unavailable_message("gurobi", available, errors)
          == "Solver 'gurobi' not available. Available solvers: clp, cbc");
    CHECK(solver_unavailable_message("gurobi", {}, {})
              .contains("(none — no plugins loaded)"));
  }
}

TEST_CASE("no_solver_message: all refused vs none found")  // NOLINT
{
  SUBCASE("no plugin at all: the install hints")
  {
    const auto msg = no_solver_message({});
    CHECK(msg.starts_with("No solver plugins found."));
    CHECK(msg.contains("GTOPT_PLUGIN_DIR"));
    CHECK(msg.contains("Install HiGHS"));
    CHECK_FALSE(msg.contains("refused"));
  }

  SUBCASE("plugins that failed to load, none refused: still none found")
  {
    const auto msg = no_solver_message({
        "Failed to load plugin /p/libgtopt_solver_osi.so: dlopen error",
    });
    CHECK(msg.starts_with("No solver plugins found."));
  }

  SUBCASE("every plugin found was refused: the refusals, not install hints")
  {
    const std::vector<std::string> errors {
        "Plugin /p/libgtopt_solver_highs.so: Solver 'highs': the loaded "
        "library /x/libhighs.so.1 is not the library the plugin was built "
        "against ... Refused (plugin check mode 'error'; set "
        "GTOPT_HIGHS_ALLOW_UNPINNED=1 or use --plugin-check=warning).",
        "Failed to load plugin /p/libgtopt_solver_osi.so: dlopen error",
    };
    const auto msg = no_solver_message(errors);
    CHECK_FALSE(msg.contains("No solver plugins found"));
    CHECK_FALSE(msg.contains("Install HiGHS"));
    CHECK(msg.contains("every one was refused"));
    CHECK(msg.contains("not the library the plugin was built against"));
    CHECK(msg.contains("GTOPT_HIGHS_ALLOW_UNPINNED=1"));
    CHECK(msg.contains("dlopen error"));
    CHECK(msg.contains("--plugin-check=warning"));
    // Still a solver/internal error (exit code 3), not an input error.
    CHECK(classify_error_exit_code(msg) == 3);
  }
}

TEST_CASE("SolverRegistry - explicit unavailable solver throws")  // NOLINT
{
  // An explicit request is never served by another solver: create() of a
  // solver the registry does not provide (a refused plugin is removed from
  // the registry exactly like a missing one) throws with the message.
  auto& reg = SolverRegistry::instance();
  CHECK_THROWS_WITH_AS(
      (void)reg.create("highs_pin_refused_xyz"),
      doctest::Contains("Solver 'highs_pin_refused_xyz' not available."),
      std::runtime_error);
}

TEST_CASE(  // NOLINT
    "gtopt_main - --solver naming an unavailable solver fails, no fallback")
{
  const cfg::RunTree t {"pin-explicit"};
  const cfg::ScopedEnv no_conf {"GTOPT_CONFIG", std::nullopt};
  const auto json = t.case_dir / "case.json";
  cfg::write_text(json,
                  cfg::case_json(std::format(R"("output_directory": "{}",
      "output_format": "csv", "output_compression": "uncompressed")",
                                             t.out.generic_string())));
  const auto r = gtopt_main(MainOptions {
      .planning_files = {json.string()},
      .async_logger = false,
      .solver = std::string {"highs_pin_refused_xyz"},
  });
  // Either an error or a non-zero exit code; never a solved run with some
  // other solver.
  CHECK((!r.has_value() || *r != 0));
  if (!r.has_value()) {
    CHECK(r.error().contains("Solver 'highs_pin_refused_xyz' not available."));
  }
  CHECK_FALSE(std::filesystem::exists(t.out / "solution.csv"));
}

TEST_CASE("SolverRegistry - HiGHS plugin reports its version pin")  // NOLINT
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver("highs")) {
    MESSAGE("HiGHS plugin not available - skipping");
    return;
  }
  const auto reports = reg.plugin_reports();
  const auto rep = std::ranges::find(
      reports, std::string("highs"), &PluginReport::plugin_name);
  REQUIRE(rep != reports.end());
  const auto lib = std::ranges::find(
      rep->solvers, std::string("highs"), &SolverLibraryReport::solver);
  REQUIRE(lib != rep->solvers.end());

#ifdef GTOPT_TEST_HIGHS_PIN_VERSION
  // The CMake pin reached the plugin.
  CHECK(lib->pinned.str() == std::string_view {GTOPT_TEST_HIGHS_PIN_VERSION});
#endif
  if (!lib->pinned.known()) {
    MESSAGE("HiGHS plugin is not pinned - nothing more to check");
    return;
  }
  // Loaded, hence not refused: either the runtime is the pinned release,
  // or an override (ALLOW_UNPINNED build / env / warning mode) let it in.
  CHECK(lib->pin_check.verdict != IntegrityVerdict::refuse);
  if (lib->runtime.major == lib->pinned.major
      && lib->runtime.minor == lib->pinned.minor
      && lib->runtime.patch == lib->pinned.patch)
  {
    CHECK(lib->pin_check.status == "pinned");
  } else {
    CHECK(lib->pin_check.verdict == IntegrityVerdict::warn);
  }
  CHECK(describe_solver_library(*lib).contains(
      std::format("pin {} ({})", lib->pinned.str(), lib->pin_check.status)));
}

TEST_CASE("env_solver_must_fail - GTOPT_SOLVER refused vs missing")  // NOLINT
{
  const std::vector<std::string> refused {
      "Plugin /p/libgtopt_solver_highs.so: Solver 'highs': the loaded "
      "library ... is highs 1.12.0, but this gtopt pins highs 1.16.0 ... "
      "Refused (plugin check mode 'error'; set GTOPT_HIGHS_ALLOW_UNPINNED=1, "
      "...).",
  };
  const std::vector<std::string> build_id_refused {
      "Plugin /p/libgtopt_solver_highs.so build-id 1234 does not match its "
      "manifest (abcd): ... Refused (plugin check mode 'error'; use "
      "--plugin-check=warning or GTOPT_PLUGIN_CHECK=warning to load it "
      "anyway).",
  };
  const std::vector<std::string> dlopen_failed {
      "Failed to load plugin /p/libgtopt_solver_highs.so: libhighs.so.1: "
      "cannot open shared object file",
  };

  SUBCASE("refused plugin: fail, like an explicit --solver")
  {
    CHECK(
        env_solver_must_fail("highs", refused, /*fallback_on_refused=*/false));
    CHECK(env_solver_must_fail("highs", build_id_refused, false));
  }
  SUBCASE("missing plugin: fall back")
  {
    CHECK_FALSE(env_solver_must_fail("highs", {}, false));
    // Another solver's refusal does not concern this one.
    CHECK_FALSE(env_solver_must_fail("gurobi", refused, false));
  }
  SUBCASE("plugin that failed to load (not an integrity refusal): fall back")
  {
    CHECK_FALSE(env_solver_must_fail("highs", dlopen_failed, false));
  }
  SUBCASE("override: fall back even when refused")
  {
    CHECK_FALSE(
        env_solver_must_fail("highs", refused, /*fallback_on_refused=*/true));
  }
}

TEST_CASE("solver_fallback_on_refused_from_env")  // NOLINT
{
  CHECK(k_solver_fallback_on_refused_env == "GTOPT_SOLVER_FALLBACK_ON_REFUSED");
  {
    const cfg::ScopedEnv env {"GTOPT_SOLVER_FALLBACK_ON_REFUSED", std::nullopt};
    CHECK_FALSE(solver_fallback_on_refused_from_env());
  }
  {
    const cfg::ScopedEnv env {"GTOPT_SOLVER_FALLBACK_ON_REFUSED", "1"};
    CHECK(solver_fallback_on_refused_from_env());
  }
  {
    const cfg::ScopedEnv env {"GTOPT_SOLVER_FALLBACK_ON_REFUSED", "off"};
    CHECK_FALSE(solver_fallback_on_refused_from_env());
  }
  {
    const cfg::ScopedEnv env {"GTOPT_SOLVER_FALLBACK_ON_REFUSED", "bogus"};
    CHECK_FALSE(solver_fallback_on_refused_from_env());
  }
}

TEST_CASE(
    "default_solver - GTOPT_SOLVER naming a missing solver falls back")  // NOLINT
{
  // No plugin provides it (nothing refused): the old warn-and-fall-back.
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (reg.available_solvers().empty()) {
    MESSAGE("no solver plugins - skipping");
    return;
  }
  const cfg::ScopedEnv env {"GTOPT_SOLVER", "no_such_solver_xyz"};
  std::string chosen;
  CHECK_NOTHROW(chosen = std::string {reg.default_solver()});
  CHECK(chosen != "no_such_solver_xyz");
  CHECK(reg.has_solver(chosen));
}

// ── Solver-library identity (build-id / SHA-256, not the version) ─────────

namespace version_pin_test
{
namespace
{

[[nodiscard]] std::vector<LinkedLibraryRecord> linked_records()
{
  return {
      {
          .soname = "libhighs.so.1",
          .build_id = "7b9ca7b14efe0123456789abcdef0123456789ab",
          .path = "/opt/highs-1.16.0/lib/libhighs.so.1.16.0",
      },
      {
          .soname = "libnobuildid.so.2",
          .sha256 = "aa11",
          .path = "/opt/x/lib/libnobuildid.so.2.0",
      },
  };
}

[[nodiscard]] LoadedLibraryIdentity loaded_highs(std::string build_id)
{
  return {
      .path = "/opt/highs-1.16.0/lib/libhighs.so.1.16.0",
      .soname = "libhighs.so.1",
      .build_id = std::move(build_id),
  };
}

}  // namespace
}  // namespace version_pin_test

TEST_CASE("match_linked_library")  // NOLINT
{
  const auto linked = linked_records();
  CHECK(match_linked_library(linked, loaded_highs("")) == linked.data());
  // No soname: matched by file name.
  const LoadedLibraryIdentity by_name {
      .path = "/elsewhere/libnobuildid.so.2.0",
  };
  CHECK(match_linked_library(linked, by_name) == &linked[1]);
  const LoadedLibraryIdentity foreign {
      .path = "/tmp/libfake.so",
      .soname = "libfake.so",
  };
  CHECK(match_linked_library(linked, foreign) == nullptr);
  CHECK(match_linked_library({}, loaded_highs("")) == nullptr);
}

TEST_CASE("check_solver_library_identity decision")  // NOLINT
{
  const auto linked = linked_records();
  const auto check = [&linked](const LoadedLibraryIdentity& loaded,
                               const PluginIntegrityPolicy& policy,
                               VersionPinOverrides overrides = {},
                               bool statically_linked = false)
  {
    return check_solver_library_identity(
        "highs", &linked, loaded, statically_linked, overrides, policy);
  };
  const auto good = loaded_highs(linked[0].build_id);

  SUBCASE("same build-id: verified")
  {
    const auto d = check(good, k_err);
    CHECK(d.verdict == IntegrityVerdict::ok);
    CHECK(d.status == "verified");
  }

  SUBCASE("static, unreported or unlocated: nothing to compare")
  {
    CHECK(check(good, k_err, {}, /*statically_linked=*/true).status
          == "static");
    CHECK(
        check_solver_library_identity("highs", nullptr, good, false, {}, k_err)
            .status
        == "unchecked");
    CHECK(check(LoadedLibraryIdentity {}, k_err).status == "unchecked");
  }

  SUBCASE("other build-id (fake reporting the right version): refused")
  {
    auto fake = loaded_highs("0000000000000000000000000000000000000001");
    fake.path = "/tmp/fake/libhighs.so.1";
    fake.ld_library_path = "/tmp/fake";
    const auto d = check(fake, k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "FOREIGN");
    // Loaded id/path vs expected id/path, and the loader environment.
    CHECK(d.message.contains("/tmp/fake/libhighs.so.1"));
    CHECK(d.message.contains(
        "build-id 0000000000000000000000000000000000000001"));
    CHECK(d.message.contains("/opt/highs-1.16.0/lib/libhighs.so.1.16.0"));
    CHECK(d.message.contains("build-id " + linked[0].build_id));
    CHECK(d.message.contains("LD_LIBRARY_PATH=/tmp/fake"));
    CHECK(d.message.contains("LD_PRELOAD=<unset>"));
    CHECK(d.message.contains("not the library the plugin was built against"));
    CHECK(d.message.contains(k_plugin_refused_marker));
    CHECK(d.message.contains("GTOPT_HIGHS_ALLOW_UNPINNED=1"));
  }

  SUBCASE("loaded library without build-id: unverified, refused")
  {
    const auto d = check(loaded_highs(""), k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "unverified");
    CHECK(d.message.contains("cannot be read"));
  }

  SUBCASE("a library the plugin was not linked against (LD_PRELOAD)")
  {
    const LoadedLibraryIdentity preload {
        .path = "/tmp/libgtopt_fake_highs.so",
        .soname = "libgtopt_fake_highs.so",
        .build_id = "1234",
        .ld_preload = "/tmp/libgtopt_fake_highs.so",
    };
    const auto d = check(preload, k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "FOREIGN");
    CHECK(d.message.contains("not a library the plugin was linked against"));
    CHECK(d.message.contains("LD_PRELOAD=/tmp/libgtopt_fake_highs.so"));
    CHECK(d.message.contains("libhighs.so.1.16.0 (libhighs.so.1)"));
  }

  SUBCASE("SHA-256 fallback for a library recorded without build-id")
  {
    LoadedLibraryIdentity l {
        .path = "/opt/x/lib/libnobuildid.so.2.0",
        .soname = "libnobuildid.so.2",
        .sha256 = "aa11",
    };
    CHECK(check(l, k_err).status == "verified");
    l.sha256 = "bb22";
    CHECK(check(l, k_err).status == "FOREIGN");
    l.sha256.clear();
    CHECK(check(l, k_err).status == "unverified");
  }

  SUBCASE("overrides: env and warning mode only, not the build flag")
  {
    const auto fake = loaded_highs("ff");
    CHECK(check(fake, k_warn).verdict == IntegrityVerdict::warn);
    const auto e = check(fake, k_err, {.env_allow_unpinned = true});
    CHECK(e.verdict == IntegrityVerdict::warn);
    CHECK(e.message.contains("Loading it anyway"));
    CHECK(e.message.contains("$GTOPT_HIGHS_ALLOW_UNPINNED is set"));
    CHECK_FALSE(e.message.contains(k_plugin_refused_marker));
    // A plugin built with -DGTOPT_HIGHS_ALLOW_UNPINNED=ON (install.sh)
    // relaxes the version pin, never the identity.
    const auto b = check(fake, k_err, {.built_allow_unpinned = true});
    CHECK(b.verdict == IntegrityVerdict::refuse);
    CHECK(b.status == "FOREIGN");
    CHECK(b.message.contains(k_plugin_refused_marker));
    CHECK_FALSE(b.message.contains("rebuild with -DGTOPT_HIGHS"));
    CHECK(check(fake,
                k_err,
                {.built_allow_unpinned = true, .env_allow_unpinned = true})
              .verdict
          == IntegrityVerdict::warn);
    // An install.sh build still verifies its own library.
    CHECK(check(good, k_err, {.built_allow_unpinned = true}).status
          == "verified");
  }

  SUBCASE("pin refusal still offers the build flag")
  {
    const auto d = pin_check(k_v1_16_0, k_v1_12_0, k_err);
    CHECK(d.message.contains(
        "rebuild with -DGTOPT_HIGHS_ALLOW_UNPINNED=ON, or use"));
  }
}

TEST_CASE("check_solver_library_identity - full-library hash")  // NOLINT
{
  // GTOPT_SOLVER_LIB_FULL_HASH=ON: the record carries both the build-id and
  // the SHA-256 of the linked file.
  const std::vector<LinkedLibraryRecord> full {
      {
          .soname = "libhighs.so.1",
          .build_id = "7b9ca7b14efe",
          .sha256 = std::string(64, 'a'),
          .path = "/opt/highs-1.16.0/lib/libhighs.so.1.16.0",
      },
  };
  const auto check = [&full](const LoadedLibraryIdentity& loaded,
                             const PluginIntegrityPolicy& policy,
                             VersionPinOverrides overrides = {})
  {
    return check_solver_library_identity(
        "highs", &full, loaded, /*statically_linked=*/false, overrides, policy);
  };
  auto loaded = loaded_highs("7b9ca7b14efe");

  SUBCASE("build-id and content hash equal: sha256 verified")
  {
    loaded.sha256 = std::string(64, 'a');
    const auto d = check(loaded, k_err);
    CHECK(d.verdict == IntegrityVerdict::ok);
    CHECK(d.status == "verified, sha256 verified");
  }

  SUBCASE("copied build-id note, other content: refused")
  {
    loaded.sha256 = std::string(64, 'b');
    const auto d = check(loaded, k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "SHA256 MISMATCH");
    CHECK(d.message.contains("has the build-id of the library"));
    CHECK(d.message.contains("sha256 " + std::string(64, 'b')));
    CHECK(d.message.contains("expected sha256 " + std::string(64, 'a')));
    CHECK(d.message.contains(k_plugin_refused_marker));
    // Same overrides as the identity check.
    CHECK(check(loaded, k_err, {.built_allow_unpinned = true}).verdict
          == IntegrityVerdict::refuse);
    CHECK(check(loaded, k_err, {.env_allow_unpinned = true}).verdict
          == IntegrityVerdict::warn);
    CHECK(check(loaded, k_warn).verdict == IntegrityVerdict::warn);
  }

  SUBCASE("content unreadable: sha256 unverified, refused")
  {
    const auto d = check(loaded, k_err);
    CHECK(d.verdict == IntegrityVerdict::refuse);
    CHECK(d.status == "sha256 unverified");
    CHECK(d.message.contains("cannot be read"));
  }

  SUBCASE("other build-id: FOREIGN before any hashing")
  {
    auto other = loaded_highs("ff");
    other.sha256 = std::string(64, 'a');
    CHECK(check(other, k_err).status == "FOREIGN");
  }
}

TEST_CASE("sha256_file")  // NOLINT
{
  const cfg::RunTree t {"sha256"};
  const auto f = t.root / "abc.bin";
  cfg::write_text(f, "abc");
  REQUIRE(std::filesystem::file_size(f) == 3);
  CHECK(sha256_file(f)
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  CHECK(sha256_file(t.root / "missing").empty());
  // Empty file, and lengths around the 55/56/64-byte padding boundaries
  // (values from sha256sum).
  const auto f0 = t.root / "empty";
  cfg::write_text(f0, "");
  CHECK(sha256_file(f0)
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  const auto f56 = t.root / "56";
  cfg::write_text(f56,
                  "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq");
  CHECK(sha256_file(f56)
        == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  const auto f64 = t.root / "64";
  cfg::write_text(f64, std::string(64, 'a'));
  CHECK(sha256_file(f64)
        == "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb");
}

TEST_CASE("SolverRegistry - solver library identity is verified")  // NOLINT
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  for (const auto& rep : reg.plugin_reports()) {
    for (const auto& lib : rep.solvers) {
      INFO(
          rep.plugin_name, "/", lib.solver, ": ", describe_solver_library(lib));
      // Loaded, so not refused; in this build tree every shared solver
      // library is the one the plugin was linked against.
      CHECK(lib.identity_check.verdict != IntegrityVerdict::refuse);
      if (lib.statically_linked) {
        CHECK(lib.identity_check.status == "static");
      } else if (!lib.library_path.empty() && lib.reported) {
        CHECK(lib.identity_check.status
              == (k_solver_lib_full_hash ? "verified, sha256 verified"
                                         : "verified"));
        CHECK_FALSE(lib.expected_library_path.empty());
      }
    }
  }
}
