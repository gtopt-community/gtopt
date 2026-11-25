// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_plugin_integrity.cpp
 * @brief     ELF build-id inspection, build-id manifest and the plugin
 *            integrity policy (build-id / build tag / solver version)
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <fstream>
#include <memory>
#include <ranges>
#include <string>
#include <utility>

#include <doctest/doctest.h>
#include <gtopt/build_info.hpp>
#include <gtopt/plugin_integrity.hpp>
#include <gtopt/solver_registry.hpp>
#include <link.h>
#include <unistd.h>

#include "log_capture.hpp"

using namespace gtopt;

namespace
{

/// Fresh empty directory under the system temp dir.
std::filesystem::path pi_make_tmp_dir(std::string_view tag)
{
  const auto dir = std::filesystem::temp_directory_path()
      / std::format("gtopt_pi_{}_{}", tag, ::getpid());
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  return dir;
}

void pi_write_file(const std::filesystem::path& p, std::string_view text)
{
  std::ofstream out(p, std::ios::binary);
  out << text;
}

/// `readelf -n <file>` build-id, or "" when readelf is unavailable.
std::string pi_readelf_build_id(const std::filesystem::path& file)
{
  const auto cmd = std::format("readelf -n '{}' 2>/dev/null", file.string());
  // Test-only: a fixed readelf command line on a path we built ourselves.
  // NOLINTNEXTLINE(cert-env33-c,bugprone-command-processor)
  std::unique_ptr<FILE, int (*)(FILE*)> pipe(::popen(cmd.c_str(), "r"),
                                             ::pclose);
  if (!pipe) {
    return {};
  }
  std::string text;
  std::array<char, 512> buf {};
  while (std::fgets(buf.data(), static_cast<int>(buf.size()), pipe.get())
         != nullptr)
  {
    text += buf.data();
  }
  const auto pos = text.find("Build ID:");
  if (pos == std::string::npos) {
    return {};
  }
  auto rest = text.substr(pos + 9);
  const auto b = rest.find_first_not_of(" \t");
  const auto e = rest.find_first_of(" \t\r\n", b);
  return rest.substr(b, e - b);
}

/// A function that certainly lives in the test executable.
int pi_anchor_function()
{
  return 42;
}

}  // namespace

TEST_CASE("read_elf_identity - test executable build-id")  // NOLINT
{
  const std::filesystem::path self = "/proc/self/exe";
  const auto id = read_elf_identity(self);
  REQUIRE(id.has_value());
  // Every toolchain we build with emits a GNU build-id note.
  CHECK(id->build_id.size() >= 16);
  CHECK(id->build_id.size() % 2 == 0);

  SUBCASE("matches the in-memory note of the running program")
  {
    const auto obj = find_loaded_object(
        reinterpret_cast<const void*>(&pi_anchor_function));  // NOLINT
    REQUIRE(obj.has_value());
    CHECK(obj.value_or(LoadedObject {}).is_main_program);
    CHECK(obj.value_or(LoadedObject {}).build_id == id->build_id);
    CHECK(obj.value_or(LoadedObject {}).path
          == std::filesystem::canonical(self));
  }

  SUBCASE("matches readelf -n when readelf is available")
  {
    // Canonical path: /proc/self/exe inside readelf is readelf itself.
    const auto expected = pi_readelf_build_id(std::filesystem::canonical(self));
    if (!expected.empty()) {
      CHECK(id->build_id == expected);
    }
  }
}

TEST_CASE("find_loaded_object - shared library and null")  // NOLINT
{
  CHECK_FALSE(find_loaded_object(nullptr).has_value());

  // dl_iterate_phdr lives in libc: the object must be a shared library
  // with a real path, not the main program.
  const auto libc = find_loaded_object(
      reinterpret_cast<const void*>(&::dl_iterate_phdr));  // NOLINT
  REQUIRE(libc.has_value());
  const auto obj = libc.value_or(LoadedObject {});
  CHECK_FALSE(obj.is_main_program);
  CHECK(obj.path.filename().string().contains("libc"));
  const auto on_disk = read_elf_identity(obj.path);
  REQUIRE(on_disk.has_value());
  CHECK(on_disk.value_or(ElfIdentity {}).build_id == obj.build_id);
  CHECK(on_disk.value_or(ElfIdentity {}).soname.starts_with("libc.so"));
}

TEST_CASE("read_elf_identity - errors")  // NOLINT
{
  const auto dir = pi_make_tmp_dir("elf");
  CHECK_FALSE(read_elf_identity(dir / "missing.so").has_value());
  pi_write_file(dir / "text.so", "this is not an ELF file at all");
  const auto r = read_elf_identity(dir / "text.so");
  REQUIRE_FALSE(r.has_value());
  CHECK(r.error().contains("not an ELF"));
  std::filesystem::remove_all(dir);
}

TEST_CASE("parse_build_id_manifest")  // NOLINT
{
  const std::string id = "0123456789abcdef0123456789abcdef01234567";
  CHECK(parse_build_id_manifest(id).value_or("") == id);
  CHECK(
      parse_build_id_manifest(id + "  libgtopt_solver_highs.so\n").value_or("")
      == id);
  CHECK(parse_build_id_manifest("  \t" + id + "\n").value_or("") == id);
  CHECK(parse_build_id_manifest("0123456789ABCDEF0123456789ABCDEF").value_or("")
        == "0123456789abcdef0123456789abcdef");
  CHECK_FALSE(parse_build_id_manifest("").has_value());
  CHECK_FALSE(parse_build_id_manifest("   \n").has_value());
  CHECK_FALSE(parse_build_id_manifest("abcd").has_value());  // too short
  CHECK_FALSE(parse_build_id_manifest("0123456789abcdef0").has_value());  // odd
  CHECK_FALSE(
      parse_build_id_manifest("0123456789abcdefXY23456789abcdef").has_value());
}

TEST_CASE("read_build_id_manifest - sidecar next to the plugin")  // NOLINT
{
  const auto dir = pi_make_tmp_dir("manifest");
  const auto plugin = dir / "libgtopt_solver_fake.so";
  pi_write_file(plugin, "x");
  CHECK_FALSE(read_build_id_manifest(plugin).has_value());

  auto sidecar = plugin;
  sidecar += k_build_id_manifest_suffix;
  pi_write_file(sidecar,
                "00112233445566778899aabbccddeeff  libgtopt_solver_fake.so\n");
  CHECK(read_build_id_manifest(plugin).value_or("")
        == "00112233445566778899aabbccddeeff");

  pi_write_file(sidecar, "garbage\n");
  CHECK_FALSE(read_build_id_manifest(plugin).has_value());
  std::filesystem::remove_all(dir);
}

TEST_CASE("parse_plugin_check_mode")  // NOLINT
{
  CHECK(parse_plugin_check_mode("warning") == PluginCheckMode::warning);
  CHECK(parse_plugin_check_mode("WARN") == PluginCheckMode::warning);
  CHECK(parse_plugin_check_mode("error") == PluginCheckMode::error);
  CHECK(parse_plugin_check_mode("Error") == PluginCheckMode::error);
  CHECK_FALSE(parse_plugin_check_mode("strict").has_value());
  CHECK_FALSE(parse_plugin_check_mode("").has_value());
  CHECK(to_string(PluginCheckMode::error) == "error");
  CHECK(to_string(PluginCheckMode::warning) == "warning");
}

TEST_CASE("integrity_finding follows the mode")  // NOLINT
{
  const auto w =
      integrity_finding("x", "Something.", {.mode = PluginCheckMode::warning});
  CHECK(w.verdict == IntegrityVerdict::warn);
  CHECK(w.status == "x");
  CHECK(w.message.starts_with("Something."));
  CHECK(w.message.contains("--plugin-check=error"));
  const auto e =
      integrity_finding("x", "Something.", {.mode = PluginCheckMode::error});
  CHECK(e.verdict == IntegrityVerdict::refuse);
  CHECK(e.message.contains("--plugin-check=warning"));
}

TEST_CASE("check_plugin_build_id policy")  // NOLINT
{
  const PluginIntegrityPolicy warn {.mode = PluginCheckMode::warning};
  const PluginIntegrityPolicy err {.mode = PluginCheckMode::error};
  const std::string a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const std::string b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

  SUBCASE("match is ok in both modes")
  {
    for (const auto& p : {warn, err}) {
      const auto d = check_plugin_build_id("p.so", a, a, p);
      CHECK(d.verdict == IntegrityVerdict::ok);
      CHECK(d.status == "match");
    }
  }
  SUBCASE("mismatch: warning mode warns, error mode refuses")
  {
    const auto d = check_plugin_build_id("p.so", a, b, warn);
    CHECK(d.verdict == IntegrityVerdict::warn);
    CHECK(d.status == "MISMATCH");
    CHECK(d.message.contains("bbbbbbbbbbbb"));  // short file id
    CHECK(d.message.contains("aaaaaaaaaaaa"));  // short manifest id
    CHECK(check_plugin_build_id("p.so", a, b, err).verdict
          == IntegrityVerdict::refuse);
  }
  SUBCASE("no manifest")
  {
    CHECK(check_plugin_build_id("p.so", std::nullopt, a, warn).verdict
          == IntegrityVerdict::warn);
    CHECK(check_plugin_build_id("p.so", std::nullopt, a, warn).status
          == "no manifest");
    CHECK(check_plugin_build_id("p.so", std::nullopt, a, err).verdict
          == IntegrityVerdict::refuse);
  }
  SUBCASE("no build-id in the file")
  {
    CHECK(check_plugin_build_id("p.so", a, std::nullopt, warn).verdict
          == IntegrityVerdict::warn);
    CHECK(check_plugin_build_id("p.so", a, std::nullopt, err).verdict
          == IntegrityVerdict::refuse);
  }
}

TEST_CASE("check_plugin_build_tag policy")  // NOLINT
{
  const PluginIntegrityPolicy warn {.mode = PluginCheckMode::warning};
  const PluginIntegrityPolicy err {.mode = PluginCheckMode::error};
  CHECK(check_plugin_build_tag("p", "v1+abc", "v1+abc", err).verdict
        == IntegrityVerdict::ok);
  const auto d = check_plugin_build_tag("p", "v1+abc", "v0+zzz", warn);
  CHECK(d.verdict == IntegrityVerdict::warn);
  CHECK(d.status == "differs");
  CHECK(d.message.contains("v0+zzz"));
  CHECK(d.message.contains("v1+abc"));
  CHECK(check_plugin_build_tag("p", "v1+abc", "v0+zzz", err).verdict
        == IntegrityVerdict::refuse);
  CHECK(check_plugin_build_tag("p", "v1+abc", std::nullopt, warn).verdict
        == IntegrityVerdict::warn);
  CHECK(check_plugin_build_tag("p", "v1+abc", std::nullopt, err).verdict
        == IntegrityVerdict::refuse);
}

TEST_CASE("check_solver_version policy")  // NOLINT
{
  const PluginIntegrityPolicy warn {.mode = PluginCheckMode::warning};
  const PluginIntegrityPolicy err {.mode = PluginCheckMode::error};
  const SolverVersion v1_16_0 {.major = 1, .minor = 16, .patch = 0};
  const SolverVersion v1_16_1 {.major = 1, .minor = 16, .patch = 1};
  const SolverVersion v1_10_0 {.major = 1, .minor = 10, .patch = 0};
  const SolverVersion unknown {};
  constexpr std::string_view lib = "/home/u/.local/lib/libhighs.so.1";

  SUBCASE("equal")
  {
    const auto d = check_solver_version(
        "highs", v1_16_0, v1_16_0, /*statically_linked=*/false, lib, err);
    CHECK(d.verdict == IntegrityVerdict::ok);
    CHECK(d.status == "match");
  }
  SUBCASE("minor mismatch names both versions and the path")
  {
    const auto d = check_solver_version(
        "highs", v1_16_0, v1_10_0, /*statically_linked=*/false, lib, warn);
    CHECK(d.verdict == IntegrityVerdict::warn);
    CHECK(d.message.contains("1.16.0"));
    CHECK(d.message.contains("1.10.0"));
    CHECK(d.message.contains(lib));
    CHECK(check_solver_version(
              "highs", v1_16_0, v1_10_0, /*statically_linked=*/false, lib, err)
              .verdict
          == IntegrityVerdict::refuse);
  }
  SUBCASE("major mismatch")
  {
    const SolverVersion v2 {.major = 2, .minor = 16, .patch = 0};
    CHECK(check_solver_version(
              "highs", v1_16_0, v2, /*statically_linked=*/false, lib, err)
              .verdict
          == IntegrityVerdict::refuse);
  }
  SUBCASE("patch-only difference is ok, never refused")
  {
    const auto d = check_solver_version(
        "highs", v1_16_0, v1_16_1, /*statically_linked=*/false, lib, err);
    CHECK(d.verdict == IntegrityVerdict::ok);
    CHECK(d.status == "patch differs");
    CHECK(d.message.contains("1.16.1"));
  }
  SUBCASE("versions not reported: static / unchecked")
  {
    const auto s = check_solver_version(
        "cplex", v1_16_0, unknown, /*statically_linked=*/true, "", err);
    CHECK(s.verdict == IntegrityVerdict::ok);
    CHECK(s.status == "static");
    const auto u = check_solver_version(
        "cbc", v1_16_0, unknown, /*statically_linked=*/false, lib, err);
    CHECK(u.verdict == IntegrityVerdict::ok);
    CHECK(u.status == "unchecked");
  }
}

TEST_CASE("check_runpath_shadowing policy")  // NOLINT
{
  const PluginIntegrityPolicy warn {.mode = PluginCheckMode::warning};
  const PluginIntegrityPolicy err {.mode = PluginCheckMode::error};
  const std::filesystem::path loaded = "/home/u/.local/lib/libhighs.so.1";
  const std::filesystem::path wanted = "/opt/highs/lib/libhighs.so.1.16.0";

  CHECK(check_runpath_shadowing("highs", loaded, "", "", err).verdict
        == IntegrityVerdict::ok);
  CHECK(check_runpath_shadowing("highs", wanted, wanted, "", err).verdict
        == IntegrityVerdict::ok);
  const auto d = check_runpath_shadowing(
      "highs", loaded, wanted, "/home/u/.local/lib", warn);
  CHECK(d.verdict == IntegrityVerdict::warn);
  CHECK(d.status == "shadowed");
  CHECK(d.message.contains(loaded.string()));
  CHECK(d.message.contains(wanted.string()));
  CHECK(d.message.contains("LD_LIBRARY_PATH=/home/u/.local/lib"));
  CHECK(check_runpath_shadowing("highs", loaded, wanted, "", err).verdict
        == IntegrityVerdict::refuse);
}

TEST_CASE("SolverVersion::str")  // NOLINT
{
  CHECK(SolverVersion {.major = 22, .minor = 1, .patch = 1}.str() == "22.1.1");
  CHECK(SolverVersion {.major = 9, .minor = 1}.str() == "9.1");
  CHECK(SolverVersion {}.str() == "unknown");
}

TEST_CASE("plugin check mode: compiled-in default")  // NOLINT
{
  // The CMake option GTOPT_PLUGIN_CHECK_DEFAULT (default "error") reaches
  // the code through the generated <gtopt/plugin_check_default.hpp>.
  CHECK((k_plugin_check_default_name == "error"
         || k_plugin_check_default_name == "warning"));
  CHECK(to_string(k_default_plugin_check_mode) == k_plugin_check_default_name);
  // A default-constructed policy uses the compiled-in default.
  CHECK(PluginIntegrityPolicy {}.mode == k_default_plugin_check_mode);
#ifdef GTOPT_TEST_PLUGIN_CHECK_DEFAULT
  // The value CMake configured for this tree is the one compiled in.
  CHECK(k_plugin_check_default_name
        == std::string_view {GTOPT_TEST_PLUGIN_CHECK_DEFAULT});
  if (std::string_view {GTOPT_TEST_PLUGIN_CHECK_DEFAULT} == "error") {
    CHECK(k_default_plugin_check_mode == PluginCheckMode::error);
  }
#endif
}

TEST_CASE("plugin check mode: precedence CLI > env > default")  // NOLINT
{
  // NOLINTBEGIN(concurrency-mt-unsafe)
  SolverRegistry::set_plugin_check_mode(std::nullopt);
  ::unsetenv("GTOPT_PLUGIN_CHECK");

  SUBCASE("nothing set: the compiled-in default")
  {
    CHECK_FALSE(plugin_check_mode_from_env().has_value());
    CHECK(PluginIntegrityPolicy::from_env().mode
          == k_default_plugin_check_mode);
    CHECK(SolverRegistry::plugin_check_policy().mode
          == k_default_plugin_check_mode);
    const auto [mode, source] = SolverRegistry::effective_plugin_check();
    CHECK(mode == k_default_plugin_check_mode);
    CHECK(source == PluginCheckSource::compiled_default);
    CHECK(describe_plugin_check(mode, source)
          == std::format("plugin check: {} (compiled default)",
                         k_plugin_check_default_name));
  }

  SUBCASE("empty or unparsable env: the compiled-in default")
  {
    ::setenv("GTOPT_PLUGIN_CHECK", "", 1);
    CHECK(SolverRegistry::effective_plugin_check().second
          == PluginCheckSource::compiled_default);
    ::setenv("GTOPT_PLUGIN_CHECK", "bogus", 1);
    CHECK_FALSE(plugin_check_mode_from_env().has_value());
    CHECK(PluginIntegrityPolicy::from_env().mode
          == k_default_plugin_check_mode);
    CHECK(SolverRegistry::effective_plugin_check()
          == std::pair {k_default_plugin_check_mode,
                        PluginCheckSource::compiled_default});
  }

  SUBCASE("env beats the default (both values)")
  {
    for (const auto m : {PluginCheckMode::warning, PluginCheckMode::error}) {
      ::setenv("GTOPT_PLUGIN_CHECK", std::string(to_string(m)).c_str(), 1);
      CHECK(PluginIntegrityPolicy::from_env().mode == m);
      CHECK(SolverRegistry::plugin_check_policy().mode == m);
      CHECK(SolverRegistry::effective_plugin_check()
            == std::pair {m, PluginCheckSource::environment});
    }
    ::setenv("GTOPT_PLUGIN_CHECK", "warning", 1);
    CHECK(describe_plugin_check(PluginCheckMode::warning,
                                PluginCheckSource::environment)
          == std::format("plugin check: warning (from $GTOPT_PLUGIN_CHECK; "
                         "compiled default: {})",
                         k_plugin_check_default_name));
  }

  SUBCASE("CLI beats env and default")
  {
    ::setenv("GTOPT_PLUGIN_CHECK", "error", 1);
    SolverRegistry::set_plugin_check_mode(PluginCheckMode::warning);
    CHECK(SolverRegistry::plugin_check_policy().mode
          == PluginCheckMode::warning);
    CHECK(SolverRegistry::effective_plugin_check()
          == std::pair {PluginCheckMode::warning,
                        PluginCheckSource::command_line});

    ::setenv("GTOPT_PLUGIN_CHECK", "warning", 1);
    SolverRegistry::set_plugin_check_mode(PluginCheckMode::error);
    CHECK(SolverRegistry::plugin_check_policy().mode == PluginCheckMode::error);
    CHECK(describe_plugin_check(PluginCheckMode::error,
                                PluginCheckSource::command_line)
              .starts_with("plugin check: error (from --plugin-check"));

    // Clearing the override falls back to the environment.
    SolverRegistry::set_plugin_check_mode(std::nullopt);
    CHECK(SolverRegistry::plugin_check_policy().mode
          == PluginCheckMode::warning);
  }

  SolverRegistry::set_plugin_check_mode(std::nullopt);
  ::unsetenv("GTOPT_PLUGIN_CHECK");
  // NOLINTEND(concurrency-mt-unsafe)
}

TEST_CASE("resolve_in_runpath")  // NOLINT
{
  const auto dir = pi_make_tmp_dir("runpath");
  std::filesystem::create_directories(dir / "lib");
  pi_write_file(dir / "lib" / "libfake.so.1", "x");

  const auto canon = std::filesystem::canonical(dir / "lib" / "libfake.so.1");
  CHECK(resolve_in_runpath("libfake.so.1", {"/nonexistent", "$ORIGIN/lib"}, dir)
        == canon);
  CHECK(resolve_in_runpath("libfake.so.1", {"${ORIGIN}/lib"}, dir) == canon);
  CHECK(resolve_in_runpath("libfake.so.1", {"/nonexistent"}, dir).empty());
  CHECK(resolve_in_runpath("", {"$ORIGIN/lib"}, dir).empty());
  std::filesystem::remove_all(dir);
}

TEST_CASE("describe_solver_library")  // NOLINT
{
  SolverLibraryReport lib {.solver = "highs"};
  CHECK(describe_solver_library(lib).contains("not reported"));

  lib.reported = true;
  lib.compiled = {.major = 1, .minor = 16, .patch = 0};
  lib.runtime = {.major = 1, .minor = 16, .patch = 0};
  lib.library_path = "/opt/highs/lib/libhighs.so.1.16.0";
  lib.library_build_id = "0123456789abcdef0123";
  lib.version_check.status = "match";
  const auto s = describe_solver_library(lib);
  CHECK(s.contains("runtime 1.16.0, compiled 1.16.0 (match)"));
  CHECK(s.contains("/opt/highs/lib/libhighs.so.1.16.0"));
  CHECK(s.contains("[build-id 0123456789ab]"));

  lib.runpath_library = "/opt/other/libhighs.so.1";
  CHECK(describe_solver_library(lib).contains("SHADOWS RUNPATH"));

  lib.statically_linked = true;
  CHECK(describe_solver_library(lib).contains("statically"));
}

TEST_CASE("build_info build tag")  // NOLINT
{
  const auto tag = build_info().build_tag;
  CHECK_FALSE(tag.empty());
  CHECK(tag.starts_with(build_info().git_revision));
  CHECK(tag.contains('+'));
}

// ── Integration: the real HiGHS plugin of this build ──────────────────────

TEST_CASE("SolverRegistry - HiGHS plugin identity is verified")  // NOLINT
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  if (!reg.has_solver("highs")) {
    MESSAGE("HiGHS plugin not available - skipping");
    return;
  }
  const auto reports = reg.plugin_reports();
  const auto it = std::ranges::find(
      reports, std::string("highs"), &PluginReport::plugin_name);
  REQUIRE(it != reports.end());
  const auto& rep = *it;

  CHECK(rep.build_id.size() >= 16);
  // Built in this tree: the CMake-written manifest matches, and so does
  // the build tag.
  CHECK(rep.build_id_check.status == "match");
  CHECK(rep.build_tag_check.verdict == IntegrityVerdict::ok);
  CHECK(rep.build_tag == build_info().build_tag);

  const auto lib = std::ranges::find(
      rep.solvers, std::string("highs"), &SolverLibraryReport::solver);
  REQUIRE(lib != rep.solvers.end());
  CHECK(lib->reported);
  CHECK(lib->compiled.known());
  CHECK(lib->runtime.known());
  CHECK(lib->compiled.major == lib->runtime.major);
  CHECK(lib->compiled.minor == lib->runtime.minor);
  CHECK(lib->version_check.verdict != IntegrityVerdict::refuse);
  CHECK_FALSE(lib->statically_linked);
  CHECK(lib->library_path.filename().string().starts_with("libhighs"));
  CHECK(lib->runpath_library.empty());

  // A copy of this build's plugin next to a manifest that names another
  // build-id (tampered / replaced .so), or with no manifest at all.
  const auto make_copy = [&rep](std::string_view tag, bool with_manifest)
  {
    const auto dir = pi_make_tmp_dir(tag);
    const auto plugin_copy = dir / rep.path.filename();
    std::filesystem::copy_file(rep.path, plugin_copy);
    if (with_manifest) {
      auto sidecar = plugin_copy;
      sidecar += k_build_id_manifest_suffix;
      pi_write_file(sidecar,
                    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef  "
                        + plugin_copy.filename().string() + "\n");
    }
    return plugin_copy;
  };
  const auto refused = [&reg](const std::filesystem::path& plugin_copy)
  {
    return std::ranges::any_of(reg.load_errors(),
                               [&plugin_copy](const std::string& e)
                               {
                                 return e.contains(plugin_copy.string())
                                     && e.contains(k_plugin_refused_marker);
                               });
  };

  // NOLINTBEGIN(concurrency-mt-unsafe)
  ::unsetenv("GTOPT_PLUGIN_CHECK");
  SolverRegistry::set_plugin_check_mode(std::nullopt);

  SUBCASE("error mode: a copy with a tampered manifest is refused")
  {
    const auto plugin_copy = make_copy("tamper", /*with_manifest=*/true);
    SolverRegistry::set_plugin_check_mode(PluginCheckMode::error);
    CHECK_FALSE(reg.load_plugin(plugin_copy));
    CHECK(refused(plugin_copy));
    std::filesystem::remove_all(plugin_copy.parent_path());
  }

  SUBCASE("compiled default: mismatched or missing build-id manifest")
  {
    REQUIRE(SolverRegistry::effective_plugin_check().second
            == PluginCheckSource::compiled_default);
    const auto tampered = make_copy("dflt_tamper", /*with_manifest=*/true);
    const auto missing = make_copy("dflt_missing", /*with_manifest=*/false);
    test::LogCapture logs;
    if (k_default_plugin_check_mode == PluginCheckMode::error) {
      // The default build (GTOPT_PLUGIN_CHECK_DEFAULT=error) refuses both
      // before dlopen.
      CHECK_FALSE(reg.load_plugin(tampered));
      CHECK(refused(tampered));
      CHECK_FALSE(reg.load_plugin(missing));
      CHECK(refused(missing));
    } else {
      // Duplicate of the loaded 'highs' plugin: never registered, but the
      // integrity check only warns.
      CHECK_FALSE(reg.load_plugin(tampered));
      CHECK_FALSE(refused(tampered));
      CHECK(logs.contains("Loading it anyway"));
    }
    std::filesystem::remove_all(tampered.parent_path());
    std::filesystem::remove_all(missing.parent_path());
  }

  SUBCASE("explicit warning: the same plugins only warn")
  {
    for (const auto via_env : {false, true}) {
      if (via_env) {
        ::setenv("GTOPT_PLUGIN_CHECK", "warning", 1);
      } else {
        SolverRegistry::set_plugin_check_mode(PluginCheckMode::warning);
      }
      for (const auto with_manifest : {true, false}) {
        const auto plugin_copy = make_copy(
            std::format("warn_{}_{}", via_env, with_manifest), with_manifest);
        test::LogCapture logs;
        // Not registered (a duplicate of the loaded 'highs' plugin), but
        // not refused: the integrity check only logs a warning.
        CHECK_FALSE(reg.load_plugin(plugin_copy));
        CHECK_FALSE(refused(plugin_copy));
        CHECK(logs.contains(plugin_copy.string()));
        CHECK(logs.contains("Loading it anyway"));
        std::filesystem::remove_all(plugin_copy.parent_path());
      }
      SolverRegistry::set_plugin_check_mode(std::nullopt);
      ::unsetenv("GTOPT_PLUGIN_CHECK");
    }
  }

  SolverRegistry::set_plugin_check_mode(std::nullopt);
  ::unsetenv("GTOPT_PLUGIN_CHECK");
  // NOLINTEND(concurrency-mt-unsafe)
}
