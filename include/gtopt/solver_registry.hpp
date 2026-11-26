/**
 * @file      solver_registry.hpp
 * @brief     Dynamic solver plugin registry with dlopen support
 * @date      Sun Mar 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Manages discovery and loading of solver backend plugins at runtime.
 * Plugins are shared libraries (libgtopt_solver_*.so) that export a
 * standard set of C functions for creating SolverBackend instances.
 *
 * By default the registry uses **lazy loading**: plugin files are
 * discovered (filesystem scan) at construction, but dlopen is deferred
 * until a solver is actually requested.  Call load_all_plugins() to
 * force eager loading (e.g. for --solvers listing).
 */

#pragma once

#include <chrono>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <gtopt/plugin_integrity.hpp>
#include <gtopt/solver_backend.hpp>
#include <sys/types.h>

namespace gtopt
{

/// Load errors that concern @p solver_name: those naming `Solver '<name>'`
/// (library version / pin / shadowing refusals) or its plugin file
/// `libgtopt_solver_<name>` (build-id, build tag, dlopen, ABI).
[[nodiscard]] std::vector<std::string> solver_load_errors(
    std::string_view solver_name, const std::vector<std::string>& load_errors);

/// Error text for a solver that was requested by name but is not
/// available: names it, lists why its plugin was refused (from
/// @p load_errors, see solver_load_errors()) and the solvers available
/// instead.  An explicit request never falls back to another solver.
[[nodiscard]] std::string solver_unavailable_message(
    std::string_view solver_name,
    const std::vector<std::string>& available,
    const std::vector<std::string>& load_errors);

/// Error text when no solver is usable and none was requested by name.
/// When some plugin was REFUSED by an integrity check (a load error
/// carries `k_plugin_refused_marker`), says that plugins were found but
/// every one was refused, lists the load errors (each refusal names its
/// own override) and the generic overrides -- not the install hints,
/// which would send the user after a missing solver that is installed.
/// Otherwise (genuinely no plugin) returns the "No solver plugins found."
/// text with the install hints.
[[nodiscard]] std::string no_solver_message(
    const std::vector<std::string>& load_errors);

/// Environment variable: when true (`1/true/yes/on`), a `GTOPT_SOLVER`
/// whose plugin was REFUSED falls back to the automatic choice with a
/// warning (the old behaviour) instead of failing.
inline constexpr std::string_view k_solver_fallback_on_refused_env =
    "GTOPT_SOLVER_FALLBACK_ON_REFUSED";

/// True when `$GTOPT_SOLVER_FALLBACK_ON_REFUSED` is true (an unparsable
/// value logs a warning and counts as false).
[[nodiscard]] bool solver_fallback_on_refused_from_env();

/// Decide what an unavailable `GTOPT_SOLVER=<solver_name>` does: true
/// (fail, like an explicit `--solver`) when its plugin was refused by an
/// integrity check (a load error for it carries `k_plugin_refused_marker`)
/// and @p fallback_on_refused is false; false (warn and fall back to the
/// automatic choice) when the solver simply has no plugin, its plugin
/// failed for another reason, or the override is set.
[[nodiscard]] bool env_solver_must_fail(
    std::string_view solver_name,
    const std::vector<std::string>& load_errors,
    bool fallback_on_refused);

/// Why the plugin at @p path must not be loaded (the file or its directory
/// is world-writable), or an empty string when its location is acceptable.
[[nodiscard]] std::string insecure_plugin_location(
    const std::filesystem::path& path);

/**
 * @brief Singleton registry for dynamically loaded solver plugins.
 *
 * Usage:
 *
 * @code{.cpp}
 *   auto& reg = SolverRegistry::instance();
 *   auto backend = reg.create("highs");  // loads plugin on demand
 * @endcode
 */
class SolverRegistry
{
public:
  /** @brief Get the global singleton registry.
   *
   * On first call, discovers plugin files from standard paths (but does
   * not dlopen them yet — loading is deferred until needed).
   */
  static SolverRegistry& instance();

  /** @brief Create a solver backend by name.
   *
   * Loads the matching plugin if it has not been loaded yet.
   *
   * @param solver_name  Solver identifier: "clp", "cbc", "cplex", "highs"
   * @return Owning pointer to the created backend
   * @throws std::runtime_error if the solver is not available
   */
  [[nodiscard]] std::unique_ptr<SolverBackend> create(
      std::string_view solver_name);

  /** @brief Discover plugin files in a directory (no dlopen).
   *
   * Scans @p dir for files matching `libgtopt_solver_*.so` and records
   * them for later loading.  Already-recorded paths are skipped.
   */
  void discover_plugins(const std::filesystem::path& dir);

  /** @brief Load a single plugin shared library.
   *
   * @param path  Full path to the .so file
   * @return true if the plugin was loaded successfully
   */
  bool load_plugin(const std::filesystem::path& path);

  /** @brief Force all discovered plugins to be loaded.
   *
   * Call this before available_solvers() or has_solver() when you need
   * to enumerate every solver (e.g. for --solvers).
   */
  void load_all_plugins();

  /** @brief List all available solver names across loaded plugins. */
  [[nodiscard]] std::vector<std::string> available_solvers() const;

  /** @brief Check whether a solver name is available.
   *
   * Only checks already-loaded plugins.  If you need an exhaustive
   * check, call load_all_plugins() first.
   */
  [[nodiscard]] bool has_solver(std::string_view name) const;

  /** @brief Check whether the named solver can solve MIP problems.
   *
   * Loads the plugin on demand if necessary, then queries the backend's
   * supports_mip() method.  Returns false if the solver is not available
   * or does not support integer variables (e.g. CLP).
   */
  [[nodiscard]] bool supports_mip(std::string_view name);

  /** @brief True if at least one loaded backend can solve MIP problems.
   *
   * Calls load_all_plugins() to ensure every available solver has been
   * inspected.  Use this to gate test cases that require a MIP-capable
   * solver — when it returns false, the test should skip.
   */
  [[nodiscard]] bool has_mip_solver();

  /** @brief Return the best available solver name by priority.
   *
   * Loads plugins on demand until a suitable solver is found.
   * `$GTOPT_SOLVER` names the solver to use; when it is unavailable the
   * registry falls back to the priority order, except when its plugin was
   * refused by an integrity check (see env_solver_must_fail()).
   * Priority order: cplex, highs, mindopt, cbc, clp.
   * @throws std::runtime_error if no solver plugins are loaded, or
   *         `$GTOPT_SOLVER`'s plugin was refused.
   */
  [[nodiscard]] std::string_view default_solver();

  /** @brief Return the solver's `+infinity` value WITHOUT instantiating a
   *  `SolverBackend`.
   *
   * Loads the plugin if not already loaded, then queries its
   * `gtopt_solver_infinity` entry point (introduced in this revision).
   * Plugins built before that entry existed return `std::nullopt` —
   * the caller must fall back to creating a backend instance and
   * reading `infinity()` directly.  CPLEX returns 1e+20; HiGHS / OSI /
   * MindOpt / Gurobi return 1e+30.
   *
   * Used by `PlanningLP::auto_scale_*` to skip "no bound" sentinel
   * values (e.g. `Reservoir.fmax = 1e30`) without paying the cost of
   * an extra backend allocation per planning construction.
   *
   * @param solver_name Solver to query (e.g. "cplex", "highs"). Empty
   *                    string defaults to `default_solver()`.
   * @return            Solver's `+infinity` value, or `std::nullopt`
   *                    if the plugin doesn't export the entry point
   *                    or no plugin provides this solver.
   */
  [[nodiscard]] std::optional<double> plugin_infinity(
      std::string_view solver_name = {});

  /** @brief Return the solver's resource descriptor WITHOUT instantiating a
   *  `SolverBackend`.
   *
   * Loads the plugin if needed, then queries its optional
   * `gtopt_solver_resource_descriptor` entry point.  Plugins that do not
   * export the symbol (all CPU solvers today) return `std::nullopt` and the
   * caller must treat the solver as CPU-class — today's behaviour, zero
   * regression.  The work-pool admission layer uses this to classify solve
   * tasks (CPU fan-out vs per-GPU-device token bucket) before dispatch.
   *
   * @param solver_name Solver to query. Empty defaults to `default_solver()`.
   */
  [[nodiscard]] std::optional<SolverResourceDescriptor> resource_descriptor(
      std::string_view solver_name = {});

  /** @brief True when the solver's declared resource class is `gpu`.
   *
   * Convenience over `resource_descriptor()`; false when the plugin exports
   * no descriptor (CPU solvers) or the solver is unavailable.
   */
  [[nodiscard]] bool is_gpu_backed(std::string_view solver_name = {});

  /** @brief Override the plugin check mode for this process (CLI
   * `--plugin-check`).  `std::nullopt` clears the override, falling back to
   * `GTOPT_PLUGIN_CHECK`, then to the compiled-in default
   * `k_default_plugin_check_mode` (CMake `GTOPT_PLUGIN_CHECK_DEFAULT`).
   * Applies to plugins loaded after the call.
   */
  static void set_plugin_check_mode(
      std::optional<PluginCheckMode> mode) noexcept;

  /** @brief Effective policy: CLI override, else `GTOPT_PLUGIN_CHECK`,
   * else the compiled-in default. */
  [[nodiscard]] static PluginIntegrityPolicy plugin_check_policy();

  /** @brief Effective mode and where it came from (CLI, environment or
   * compiled-in default). */
  [[nodiscard]] static std::pair<PluginCheckMode, PluginCheckSource>
  effective_plugin_check();

  /** @brief Integrity / identity report of every loaded plugin.
   *
   * One entry per loaded plugin: its path, ELF build-id and manifest
   * check, build tag vs the host's, and per solver the compiled vs runtime
   * solver-library version, the loaded library path and its build-id.
   * Only solvers still available (after validation) are listed.  Does not
   * load anything — call load_all_plugins() first for a full listing.
   */
  [[nodiscard]] std::vector<PluginReport> plugin_reports() const;

  /** @brief Return the directories that were searched for plugins. */
  [[nodiscard]] const std::vector<std::string>& searched_directories() const;

  /** @brief Return diagnostic messages for plugins that failed to load. */
  [[nodiscard]] const std::vector<std::string>& load_errors() const;

  /** @brief Re-establish a safe synchronous default logger after `fork()`.
   *
   * `fork()` copies the address space but not threads, so an inherited async
   * default logger has a queue with no worker thread: logging into it
   * deadlocks (block policy) or is silently dropped.  This installs a fresh
   * synchronous stderr logger as the default and detaches the inherited
   * registry handles — **in that order**.  `spdlog::drop_all()` resets the
   * default logger to null (see spdlog `registry::drop_all`), so the fresh
   * logger MUST be installed *after* it; the reverse order leaves a null
   * default and the next `spdlog::*()` call dereferences it (SIGSEGV).
   * `noexcept` and idempotent — safe to call across the fork barrier.
   * Public so the post-fork logging contract can be unit-tested.
   *
   * The fresh sink writes straight to fd 2 with a `null_mutex`: it does
   * NOT use spdlog's process-wide static console mutex (shared by every
   * `stdout/stderr[_color]_sink_mt`) nor the `stderr` `FILE*` lock.  Either
   * lock may have been held by another parent thread at `fork()` time and
   * would then stay locked forever in the single-threaded child.
   *
   * @post `spdlog::default_logger_raw() != nullptr`.
   */
  static void reset_default_logger_after_fork() noexcept;

  /// Upper bound on how long the parent waits for one solver-validation
  /// child (`validate_solver_subprocess`).  GPU backends (cuOpt) may take
  /// several seconds to initialise, hence the generous default.  This is
  /// an administrative startup probe ONLY — it never bounds a real solve.
  static constexpr std::chrono::milliseconds default_validation_timeout {
      std::chrono::seconds {60},
  };

  /** @brief Child-side hardening right after `fork()` in the validation
   * probe: arm `PR_SET_PDEATHSIG(SIGKILL)` so the child dies with its
   * parent, then compare `getppid()` with @p parent_pid (captured BEFORE
   * the fork) to close the race where the parent died before the prctl.
   *
   * @return `true` when the child may proceed, `false` when it has already
   *         been reparented (caller should `_exit`).
   */
  [[nodiscard]] static bool arm_child_parent_death_signal(
      pid_t parent_pid) noexcept;

  /** @brief Wait for child @p pid for at most @p timeout (polls
   * `waitpid(WNOHANG)`).  On timeout the child is sent SIGKILL and reaped.
   *
   * @return the `waitpid` status when the child finished in time,
   *         `std::nullopt` on timeout (child killed and reaped).  If
   *         `waitpid` fails (e.g. ECHILD) the status is reported as 0.
   */
  [[nodiscard]] static std::optional<int> wait_child_with_timeout(
      pid_t pid, std::chrono::milliseconds timeout) noexcept;

  ~SolverRegistry();

  SolverRegistry(const SolverRegistry&) = delete;
  SolverRegistry& operator=(const SolverRegistry&) = delete;
  SolverRegistry(SolverRegistry&&) = delete;
  SolverRegistry& operator=(SolverRegistry&&) = delete;

private:
  SolverRegistry();
  void discover_default_paths();
  void validate_loaded_solvers();

  /// Check solver availability without locking (caller must hold m_mutex_).
  [[nodiscard]] bool has_solver_unlocked(std::string_view name) const;

  /// Try to load the plugin that provides @p solver_name.
  /// Returns true if the solver is available after loading.
  /// When @p filename_only is true, only try the best-match filename
  /// (libgtopt_solver_{name}.so) without exhaustively loading all plugins.
  bool ensure_solver_loaded(std::string_view solver_name,
                            bool filename_only = false);

  struct PluginHandle
  {
    void* dl_handle {};
    solver_backend_factory_fn create_fn {};
    /// Optional plugin entry: returns the solver's infinity constant
    /// without instantiating a `SolverBackend`.  Resolved via dlsym
    /// at plugin load time (`load_plugin`).  Older plugins built
    /// before this entry point existed report `nullptr`; callers
    /// fall back to instance-level query (create-and-drop a backend
    /// just to read `infinity()`).
    solver_plugin_infinity_fn infinity_fn {};
    /// Optional plugin entry: fills the solver's resource descriptor
    /// (CPU vs GPU class + estimated per-solve VRAM) without instantiating
    /// a backend.  nullptr for plugins that don't export it — treated as
    /// CPU-class by `resource_descriptor()` / `is_gpu_backed()`.
    solver_plugin_resource_desc_fn resource_desc_fn {};
    std::string plugin_name;
    std::vector<std::string> solver_names;
    /// Identity checks done at load time (`--solvers`, run log).
    PluginReport report;
  };

  /// Pre-dlopen check of @p path against its build-id manifest.  Returns
  /// false (and records the error) when the plugin must be refused.
  bool verify_plugin_file(const std::filesystem::path& path,
                          const PluginIntegrityPolicy& policy,
                          PluginReport& report);

  /// Post-dlopen checks: build tag and solver-library versions.  Removes
  /// solvers whose library version check refuses them from @p names.
  void verify_loaded_plugin(void* handle,
                            const std::filesystem::path& path,
                            const PluginIntegrityPolicy& policy,
                            std::vector<std::string>& names,
                            PluginReport& report);

  [[nodiscard]] static bool validate_solver_subprocess(
      const PluginHandle& plugin,
      const std::string& solver_name,
      std::chrono::milliseconds timeout = default_validation_timeout);

  std::vector<PluginHandle> m_plugins_;
  std::vector<std::filesystem::path> m_pending_paths_;
  std::vector<std::string> m_searched_dirs_;
  std::vector<std::string> m_load_errors_;
  mutable std::recursive_mutex m_mutex_;
  bool m_all_loaded_ {false};
};

}  // namespace gtopt
