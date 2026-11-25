/**
 * @file      plugin_integrity.hpp
 * @brief     Verify that a solver plugin is the one built with this gtopt
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Building blocks used by `SolverRegistry` before and after `dlopen`:
 *
 *  - ELF inspection: the GNU build-id note (`NT_GNU_BUILD_ID`, emitted by
 *    the linker), `DT_SONAME` and `DT_RUNPATH`/`DT_RPATH` read from a file
 *    on disk, and the build-id / path of an object already mapped into the
 *    process (via `dl_iterate_phdr`).  No hashing: the linker's build-id
 *    is the identity.
 *  - The build-id manifest (`<plugin>.so.build-id`, written by CMake next
 *    to each plugin at build and install time).
 *  - Pure policy functions deciding ok / warn / refuse for a build-id,
 *    build-tag or solver-version comparison, so the decisions are unit
 *    tested without loading anything.
 */

#pragma once

#include <expected>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/plugin_check_default.hpp>

namespace gtopt
{

// ── ELF inspection ─────────────────────────────────────────────────────────

/// Identity fields read from an ELF file (64-bit little-endian only).
struct ElfIdentity
{
  std::string build_id {};  ///< lowercase hex of NT_GNU_BUILD_ID, "" if none
  std::string soname {};  ///< DT_SONAME, "" if none
  std::vector<std::string> runpath {};  ///< DT_RUNPATH (or DT_RPATH) entries
  bool runpath_is_rpath {false};  ///< true when only a legacy DT_RPATH exists
};

/// Read build-id, soname and runpath from the ELF file @p path without
/// loading it.  Returns an error string for unreadable / non-ELF64-LE files.
[[nodiscard]] std::expected<ElfIdentity, std::string> read_elf_identity(
    const std::filesystem::path& path);

/// An object mapped into this process.
struct LoadedObject
{
  std::filesystem::path path {};  ///< canonical path ("" if unknown)
  std::string build_id {};  ///< from the in-memory PT_NOTE, "" if none
  bool is_main_program {false};
};

/// Find the loaded object (executable or shared library) whose PT_LOAD
/// segments contain @p address.  `std::nullopt` if none does.
[[nodiscard]] std::optional<LoadedObject> find_loaded_object(
    const void* address);

/// First 12 hex digits of a build-id (for compact listings).
[[nodiscard]] std::string short_build_id(std::string_view build_id);

// ── Build-id manifest ──────────────────────────────────────────────────────

/// Suffix of the per-plugin manifest file: `libgtopt_solver_x.so.build-id`.
inline constexpr std::string_view k_build_id_manifest_suffix = ".build-id";

/// Parse a manifest line `<hex build-id>  <file name>` (sha256sum-like;
/// the file name is optional).  Returns the lowercase build-id, or
/// `std::nullopt` when @p text has no valid hex token.
[[nodiscard]] std::optional<std::string> parse_build_id_manifest(
    std::string_view text);

/// Read the manifest next to @p plugin_path.  `std::nullopt` when there is
/// no manifest file or it does not parse.
[[nodiscard]] std::optional<std::string> read_build_id_manifest(
    const std::filesystem::path& plugin_path);

// ── Policy ─────────────────────────────────────────────────────────────────

/// What a failed plugin integrity check does.  ONE mode for every check
/// (build-id vs manifest, build tag, compiled vs runtime solver version,
/// RUNPATH shadowing, missing manifest / build-id / identity symbols).
enum class PluginCheckMode : int
{
  warning = 0,  ///< log a clear warning and keep loading
  error,  ///< refuse the plugin (or the affected solver) with an error
};

/// Parse "warning" / "error" (case-insensitive; "warn" accepted).
[[nodiscard]] std::optional<PluginCheckMode> parse_plugin_check_mode(
    std::string_view text);

[[nodiscard]] constexpr std::string_view to_string(PluginCheckMode m) noexcept
{
  return m == PluginCheckMode::error ? "error" : "warning";
}

/// Compiled-in default mode, used when neither `--plugin-check` nor
/// `$GTOPT_PLUGIN_CHECK` selects one.  Set at configure time with the CMake
/// option `GTOPT_PLUGIN_CHECK_DEFAULT` (`error` unless configured otherwise).
inline constexpr PluginCheckMode k_default_plugin_check_mode =
    k_plugin_check_default_name == "warning" ? PluginCheckMode::warning
                                             : PluginCheckMode::error;
static_assert(k_plugin_check_default_name == "warning"
                  || k_plugin_check_default_name == "error",
              "GTOPT_PLUGIN_CHECK_DEFAULT must be 'error' or 'warning'");

/// Text every refusal message contains (`--solvers` lists those errors).
inline constexpr std::string_view k_plugin_refused_marker =
    "Refused (plugin check mode 'error'";

/// Environment variable selecting the mode when no CLI override is set.
inline constexpr std::string_view k_plugin_check_env = "GTOPT_PLUGIN_CHECK";

struct PluginIntegrityPolicy
{
  PluginCheckMode mode {k_default_plugin_check_mode};

  /// Mode from `GTOPT_PLUGIN_CHECK` (unset → the compiled-in default
  /// `k_default_plugin_check_mode`; an unparsable value logs a warning and
  /// falls back to that default).
  [[nodiscard]] static PluginIntegrityPolicy from_env();
};

/// Mode named by `$GTOPT_PLUGIN_CHECK`; `std::nullopt` when it is unset,
/// empty or unparsable (the last logs a warning).
[[nodiscard]] std::optional<PluginCheckMode> plugin_check_mode_from_env();

/// Where the effective plugin check mode came from.
enum class PluginCheckSource : int
{
  compiled_default = 0,  ///< `GTOPT_PLUGIN_CHECK_DEFAULT` (CMake)
  environment,  ///< `$GTOPT_PLUGIN_CHECK`
  command_line,  ///< `--plugin-check`
};

[[nodiscard]] constexpr std::string_view to_string(PluginCheckSource s) noexcept
{
  switch (s) {
    case PluginCheckSource::environment:
      return "from $GTOPT_PLUGIN_CHECK";
    case PluginCheckSource::command_line:
      return "from --plugin-check";
    case PluginCheckSource::compiled_default:
      break;
  }
  return "compiled default";
}

/// One line describing the effective mode, its source and the compiled
/// default, e.g. `plugin check: error (compiled default)` or
/// `plugin check: warning (from $GTOPT_PLUGIN_CHECK; compiled default:
/// error)`.  Printed by `--version` and `--solvers`.
[[nodiscard]] std::string describe_plugin_check(PluginCheckMode mode,
                                                PluginCheckSource source);

enum class IntegrityVerdict : int
{
  ok = 0,
  warn,
  refuse,
};

[[nodiscard]] constexpr std::string_view to_string(IntegrityVerdict v) noexcept
{
  switch (v) {
    case IntegrityVerdict::ok:
      return "ok";
    case IntegrityVerdict::warn:
      return "warn";
    case IntegrityVerdict::refuse:
      return "refuse";
  }
  return "?";
}

struct IntegrityDecision
{
  IntegrityVerdict verdict {IntegrityVerdict::ok};
  std::string status {};  ///< short label for listings, e.g. "match"
  std::string message {};  ///< full diagnostic when verdict != ok
};

/// Turn a failed check into a decision: warn under `warning`, refuse under
/// `error`; the message gets the matching hint appended.
[[nodiscard]] IntegrityDecision integrity_finding(
    std::string status,
    std::string message,
    const PluginIntegrityPolicy& policy);

/// Compare the plugin file's build-id with its manifest entry.  Match → ok;
/// mismatch, no manifest or no build-id → finding.
[[nodiscard]] IntegrityDecision check_plugin_build_id(
    std::string_view plugin,
    std::optional<std::string_view> manifest_id,
    std::optional<std::string_view> file_id,
    const PluginIntegrityPolicy& policy);

/// Compare the plugin's build tag with the host's.  Equal → ok; differs or
/// missing → finding.
[[nodiscard]] IntegrityDecision check_plugin_build_tag(
    std::string_view plugin,
    std::string_view host_tag,
    std::optional<std::string_view> plugin_tag,
    const PluginIntegrityPolicy& policy);

/// A solver version triple; components are -1 when unknown.
struct SolverVersion
{
  int major {-1};
  int minor {-1};
  int patch {-1};

  [[nodiscard]] constexpr bool known() const noexcept
  {
    return major >= 0 && minor >= 0;
  }
  [[nodiscard]] std::string str() const;
};

/// Compare the solver version the plugin was compiled against with the
/// version of the library loaded at runtime.
///  - major.minor differ → finding: solver APIs / struct layouts change
///    between minor releases (the HiGHS 1.10 vs 1.16 ABI break);
///  - only patch differs → ok, status "patch differs" (patch releases keep
///    the ABI; logged at info level, never refused);
///  - versions not reported: ok "static" when the library is linked into
///    the plugin (it cannot differ), ok "unchecked" otherwise (e.g. CBC,
///    which has no runtime version query — the library path is still shown);
///  - equal → ok.
[[nodiscard]] IntegrityDecision check_solver_version(
    std::string_view solver,
    const SolverVersion& compiled,
    const SolverVersion& runtime,
    bool statically_linked,
    std::string_view library_path,
    const PluginIntegrityPolicy& policy);

/// Environment variable that lets a solver whose runtime library is not the
/// pinned release load with a warning: `GTOPT_<SOLVER>_ALLOW_UNPINNED`
/// (solver name upper-cased, e.g. `GTOPT_HIGHS_ALLOW_UNPINNED`).
[[nodiscard]] std::string solver_allow_unpinned_env(std::string_view solver);

/// Parse a boolean environment flag: `1/true/yes/on` → true,
/// `0/false/no/off/""` → false (case-insensitive), anything else →
/// `std::nullopt`.
[[nodiscard]] std::optional<bool> parse_env_flag(std::string_view text);

/// True when `$GTOPT_<SOLVER>_ALLOW_UNPINNED` is set to a true value (an
/// unparsable value logs a warning and counts as false).
[[nodiscard]] bool solver_allow_unpinned_from_env(std::string_view solver);

/// What may downgrade a pin mismatch from refuse to warn (besides the
/// plugin check mode `warning`).
struct VersionPinOverrides
{
  /// The plugin was built with `-DGTOPT_<SOLVER>_ALLOW_UNPINNED=ON`.
  bool built_allow_unpinned {false};
  /// `$GTOPT_<SOLVER>_ALLOW_UNPINNED` is true.
  bool env_allow_unpinned {false};
};

/// Compare the solver library loaded at runtime with the release gtopt is
/// pinned to (CMake `GTOPT_<SOLVER>_PIN_VERSION`; another release can end
/// on another optimal vertex of a degenerate LP and change duals/costs).
///  - no pin (@p pinned unknown) → ok, status "" (nothing to check);
///  - runtime equal to the pin (major.minor, and patch when the pin has
///    one) → ok "pinned";
///  - runtime differs → status "UNPINNED"; runtime version unknown →
///    status "pin unverified".  Both are refused when the plugin check
///    mode is `error` and neither override is set; otherwise a warning
///    that names the reason it was let through.
[[nodiscard]] IntegrityDecision check_solver_version_pin(
    std::string_view solver,
    const SolverVersion& pinned,
    const SolverVersion& runtime,
    std::string_view library_path,
    const VersionPinOverrides& overrides,
    const PluginIntegrityPolicy& policy);

/// One shared library a plugin was linked against (recorded at build
/// time, see `gtopt_plugin_linked_libraries`).
struct LinkedLibraryRecord
{
  std::string soname {};
  std::string build_id {};  ///< lowercase hex, "" if the file had none
  std::string sha256 {};  ///< only when the file had no build-id
  std::filesystem::path path {};  ///< real path at build time
};

/// The solver library actually mapped in this process.
struct LoadedLibraryIdentity
{
  std::filesystem::path path {};  ///< canonical path ("" if not located)
  std::string soname {};  ///< its DT_SONAME ("" if none / unreadable)
  std::string build_id {};  ///< from the in-memory note, "" if none
  std::string sha256 {};  ///< of the file, filled only when needed
  std::string ld_library_path {};  ///< `$LD_LIBRARY_PATH` (for the message)
  std::string ld_preload {};  ///< `$LD_PRELOAD` (for the message)
};

/// The record of @p records the loaded library corresponds to: same soname,
/// else same file name as the recorded path.  nullptr when none matches.
[[nodiscard]] const LinkedLibraryRecord* match_linked_library(
    const std::vector<LinkedLibraryRecord>& records,
    const LoadedLibraryIdentity& loaded);

/// Lowercase hex SHA-256 (FIPS 180-4, as CMake's file(SHA256)) of a file's
/// content; "" when it cannot be read.
[[nodiscard]] std::string sha256_file(const std::filesystem::path& path);

/// Is the solver library mapped at run time the one the plugin was built
/// against?  Unlike the version check it does not trust what the library
/// reports about itself: it compares the GNU build-id (else the SHA-256)
/// recorded at build time with that of the library actually mapped.
///  - statically linked → ok "static"; plugin does not report its linked
///    libraries (older gtopt), or the library was not located → ok
///    "unchecked";
///  - same build-id (or SHA-256) → ok "verified";
///  - the loaded library is not one the plugin was linked against, or its
///    id differs → status "FOREIGN"; the recorded id cannot be compared
///    (loaded library without build-id / unreadable) → "unverified".
///    Both are refused unless `$GTOPT_<SOLVER>_ALLOW_UNPINNED` is set
///    (@p pin_overrides.env_allow_unpinned) or the mode is `warning`; unlike
///    the pin, a plugin built with `-DGTOPT_<SOLVER>_ALLOW_UNPINNED=ON`
///    (@p pin_overrides.built_allow_unpinned, ignored here) is NOT exempt.
///  - Full-library hash (`-DGTOPT_SOLVER_LIB_FULL_HASH=ON`): the record
///    carries the SHA-256 of the linked file next to its build-id; the
///    build-id must match AND @p loaded.sha256 (hash of the mapped file)
///    must equal it → ok "verified, sha256 verified"; another hash →
///    "SHA256 MISMATCH" (same refusal rules), so a fake that copies the
///    build-id note is still caught.
/// @param records  nullptr when the plugin does not report them.
[[nodiscard]] IntegrityDecision check_solver_library_identity(
    std::string_view solver,
    const std::vector<LinkedLibraryRecord>* records,
    const LoadedLibraryIdentity& loaded,
    bool statically_linked,
    const VersionPinOverrides& pin_overrides,
    const PluginIntegrityPolicy& policy);

/// The solver library actually loaded vs the one the plugin's RUNPATH would
/// pick.  Empty @p runpath_library or equal paths → ok; otherwise finding
/// (typically LD_LIBRARY_PATH or a preloaded library shadowing it).
[[nodiscard]] IntegrityDecision check_runpath_shadowing(
    std::string_view solver,
    const std::filesystem::path& loaded_library,
    const std::filesystem::path& runpath_library,
    std::string_view ld_library_path,
    const PluginIntegrityPolicy& policy);

/// Where the dynamic loader would find @p soname following @p runpath
/// (`$ORIGIN` / `${ORIGIN}` expanded to @p origin).  First existing
/// candidate, or "" when the runpath does not provide it.
[[nodiscard]] std::filesystem::path resolve_in_runpath(
    std::string_view soname,
    const std::vector<std::string>& runpath,
    const std::filesystem::path& origin);

// ── Report (for `gtopt --solvers` and the run log) ────────────────────────

/// Solver-library identity of one solver provided by a plugin.
struct SolverLibraryReport
{
  std::string solver {};
  SolverVersion compiled {};
  SolverVersion runtime {};
  std::filesystem::path library_path {};  ///< loaded solver library
  std::string library_build_id {};
  bool statically_linked {false};  ///< library code lives in the plugin
  /// What the plugin's RUNPATH would have loaded when it differs from
  /// @ref library_path (e.g. shadowed via LD_LIBRARY_PATH), else "".
  std::filesystem::path runpath_library {};
  IntegrityDecision version_check {};
  IntegrityDecision shadow_check {};
  /// Release this gtopt pins the solver to (unknown when not pinned or the
  /// plugin predates `gtopt_solver_version_pin`).
  SolverVersion pinned {};
  bool pin_built_allow_unpinned {false};  ///< `-DGTOPT_<S>_ALLOW_UNPINNED=ON`
  IntegrityDecision pin_check {};
  /// The library recorded at build time that @ref library_path was compared
  /// with ("" when none matched or the plugin does not report them).
  std::filesystem::path expected_library_path {};
  IntegrityDecision identity_check {};
  bool reported {false};  ///< plugin exports gtopt_solver_build_info
};

/// One-line summary of @p lib, e.g.
/// `runtime 1.16.0, compiled 1.16.0 (match), pin 1.16.0 (pinned),
/// /opt/.../libhighs.so.1.16.0 [build-id 1a2b3c4d5e6f, verified]` (the pin
/// part only when the solver is pinned; the identity status when checked).
[[nodiscard]] std::string describe_solver_library(
    const SolverLibraryReport& lib);

struct PluginReport
{
  std::string plugin_name {};
  std::filesystem::path path {};
  std::string build_id {};
  IntegrityDecision build_id_check {};
  std::string build_tag {};
  IntegrityDecision build_tag_check {};
  std::vector<SolverLibraryReport> solvers {};
};

}  // namespace gtopt
