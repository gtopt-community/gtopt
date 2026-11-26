/**
 * @file      solver_registry.cpp
 * @brief     Dynamic solver plugin registry implementation
 * @date      Sun Mar 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <exception>
#include <format>
#include <iterator>
#include <memory>
#include <optional>
#include <ranges>
#include <span>
#include <stdexcept>
#include <string_view>
#include <thread>

#include <dlfcn.h>
#include <gtopt/build_info.hpp>
#include <gtopt/process_util.hpp>
#include <gtopt/solver_registry.hpp>
#include <spdlog/details/null_mutex.h>
#include <spdlog/sinks/base_sink.h>
#include <spdlog/spdlog.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

namespace gtopt
{

namespace
{

/// Resolve the directory containing the current executable.
std::filesystem::path exe_directory()
{
  // Linux: /proc/self/exe
  std::array<char, 4096> buf {};
  const auto len = ::readlink("/proc/self/exe", buf.data(), buf.size() - 1);
  if (len <= 0) {
    return {};
  }
  buf[static_cast<size_t>(len)] = '\0';
  return std::filesystem::path(buf.data()).parent_path();
}

/// Join a vector of strings with a separator.
std::string join_strings(const std::vector<std::string>& strs,
                         std::string_view sep)
{
  if (strs.empty()) {
    return {};
  }
  return std::ranges::fold_left(
      strs | std::views::drop(1),
      strs.front(),
      [sep](const std::string& a, const std::string& b)
      { return std::format("{}{}{}", a, sep, b); });
}

}  // namespace

SolverRegistry::SolverRegistry()
{
  discover_default_paths();
  // Plugins are NOT loaded here — loading is deferred until a solver
  // is actually requested (lazy loading).
}

// Intentionally do NOT dlclose() plugin handles.
// Plugin code may still be referenced by SolverBackend instances
// whose destructors run during static destruction.  Closing the
// plugin library before those destructors would cause segfaults.
// The OS reclaims all resources on process exit anyway.
SolverRegistry::~SolverRegistry() = default;

namespace
{
/// CLI override of the plugin check mode (`--plugin-check`); 0 = unset,
/// otherwise `PluginCheckMode` + 1.
std::atomic<int>& plugin_check_override() noexcept
{
  static std::atomic<int> value {0};
  return value;
}
}  // namespace

void SolverRegistry::set_plugin_check_mode(
    std::optional<PluginCheckMode> mode) noexcept
{
  plugin_check_override().store(mode.has_value() ? static_cast<int>(*mode) + 1
                                                 : 0);
}

PluginIntegrityPolicy SolverRegistry::plugin_check_policy()
{
  return {.mode = effective_plugin_check().first};
}

std::pair<PluginCheckMode, PluginCheckSource>
SolverRegistry::effective_plugin_check()
{
  // Precedence: --plugin-check > $GTOPT_PLUGIN_CHECK > compiled default.
  const int v = plugin_check_override().load();
  if (v != 0) {
    return {static_cast<PluginCheckMode>(v - 1),
            PluginCheckSource::command_line};
  }
  if (const auto env = plugin_check_mode_from_env()) {
    return {*env, PluginCheckSource::environment};
  }
  return {k_default_plugin_check_mode, PluginCheckSource::compiled_default};
}

SolverRegistry& SolverRegistry::instance()
{
  static SolverRegistry registry;
  return registry;
}

void SolverRegistry::discover_default_paths()
{
  // 1. $GTOPT_PLUGIN_DIR environment variable
  if (const auto* env = std::getenv("GTOPT_PLUGIN_DIR");
      env != nullptr && *env != '\0')
  {
    discover_plugins(env);
  }

  // 2. <exe_dir>/../lib/gtopt/plugins/  (installed layout)
  const auto exe_dir = exe_directory();
  if (!exe_dir.empty()) {
    discover_plugins(exe_dir / ".." / "lib" / "gtopt" / "plugins");

    // 3. <exe_dir>/plugins/  (build tree — plugins/ next to standalone binary)
    discover_plugins(exe_dir / "plugins");

    // 4. <exe_dir>/../plugins/  (build tree — plugins/ sibling to standalone/)
    discover_plugins(exe_dir / ".." / "plugins");

    // 5. <exe_dir>/  (build tree — plugins alongside binary)
    discover_plugins(exe_dir);
  }

  // 6. /usr/local/lib/gtopt/plugins/
  discover_plugins("/usr/local/lib/gtopt/plugins");
}

void SolverRegistry::discover_plugins(const std::filesystem::path& dir)
{
  std::error_code ec;
  const auto canonical_dir = std::filesystem::weakly_canonical(dir, ec);
  const auto& search_dir = ec ? dir : canonical_dir;

  m_searched_dirs_.push_back(search_dir.string());

  if (!std::filesystem::is_directory(search_dir, ec)) {
    return;
  }

  for (const auto& entry : std::filesystem::directory_iterator(search_dir, ec))
  {
    if (!entry.is_regular_file()) {
      continue;
    }

    const auto& path = entry.path();
    const auto filename = path.filename().string();

    // Match libgtopt_solver_*.so — record path for lazy loading
    if (filename.starts_with("libgtopt_solver_") && filename.ends_with(".so")) {
      // Avoid recording duplicates (same canonical path)
      const auto canon = std::filesystem::weakly_canonical(path, ec);
      const auto& to_store = ec ? path : canon;
      if (std::ranges::none_of(m_pending_paths_,
                               [&to_store](const auto& p)
                               { return p == to_store; }))
      {
        m_pending_paths_.push_back(to_store);
      }
    }
  }
}

std::string insecure_plugin_location(const std::filesystem::path& path)
{
  std::error_code ec;
  const auto st = std::filesystem::status(path, ec);
  if (ec) {
    return {};  // dlopen reports the real error
  }
  if ((st.permissions() & std::filesystem::perms::others_write)
      != std::filesystem::perms::none)
  {
    return "the file is world-writable";
  }
  if (is_world_writable_directory(path.parent_path())) {
    return "its directory is world-writable";
  }
  return {};
}

namespace
{

/// Log a non-ok integrity decision at the matching level.
void log_decision(const IntegrityDecision& d)
{
  if (d.message.empty()) {
    return;
  }
  if (d.verdict == IntegrityVerdict::refuse) {
    SPDLOG_ERROR("{}", d.message);
  } else if (d.verdict == IntegrityVerdict::warn) {
    SPDLOG_WARN("{}", d.message);
  }
}

/// INFO lines under "Loaded solver plugin": the plugin's own identity and,
/// per solver, the solver library actually in use.
void log_plugin_identity(const PluginReport& report,
                         const std::vector<std::string>& solver_names)
{
  SPDLOG_INFO("    build-id {} (manifest: {}), build tag {} ({})",
              report.build_id.empty() ? std::string("none")
                                      : short_build_id(report.build_id),
              report.build_id_check.status,
              report.build_tag.empty() ? std::string("none") : report.build_tag,
              report.build_tag_check.status);
  for (const auto& lib : report.solvers) {
    if (!std::ranges::contains(solver_names, lib.solver)) {
      continue;
    }
    SPDLOG_INFO("    {}: {}", lib.solver, describe_solver_library(lib));
  }
}

}  // namespace

bool SolverRegistry::verify_plugin_file(const std::filesystem::path& path,
                                        const PluginIntegrityPolicy& policy,
                                        PluginReport& report)
{
  report.path = path;
  const auto elf = read_elf_identity(path);
  if (!elf) {
    // Not a readable ELF: dlopen will fail with its own, clearer error.
    SPDLOG_DEBUG("Plugin identity not checked: {}", elf.error());
    return true;
  }
  report.build_id = elf->build_id;
  const auto manifest = read_build_id_manifest(path);
  report.build_id_check = check_plugin_build_id(
      path.string(),
      manifest ? std::optional<std::string_view> {*manifest} : std::nullopt,
      report.build_id.empty()
          ? std::nullopt
          : std::optional<std::string_view> {report.build_id},
      policy);
  log_decision(report.build_id_check);
  if (report.build_id_check.verdict == IntegrityVerdict::refuse) {
    m_load_errors_.push_back(report.build_id_check.message);
    return false;
  }
  return true;
}

void SolverRegistry::verify_loaded_plugin(void* handle,
                                          const std::filesystem::path& path,
                                          const PluginIntegrityPolicy& policy,
                                          std::vector<std::string>& names,
                                          PluginReport& report)
{
  // ── Build tag: same gtopt build as this binary? ──
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* tag_fn = reinterpret_cast<solver_plugin_build_tag_fn>(
      ::dlsym(handle, "gtopt_plugin_build_tag"));
  const char* tag = (tag_fn != nullptr) ? tag_fn() : nullptr;
  if (tag != nullptr) {
    report.build_tag = tag;
  }
  report.build_tag_check = check_plugin_build_tag(
      path.string(),
      build_info().build_tag,
      tag != nullptr ? std::optional<std::string_view> {tag} : std::nullopt,
      policy);
  log_decision(report.build_tag_check);
  if (report.build_tag_check.verdict == IntegrityVerdict::refuse) {
    m_load_errors_.push_back(report.build_tag_check.message);
    names.clear();
    return;
  }

  // ── Solver library: compiled vs runtime version, real path ──
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* info_fn = reinterpret_cast<solver_plugin_build_info_fn>(
      ::dlsym(handle, "gtopt_solver_build_info"));
  // Optional: the solver release this gtopt pins (e.g. HiGHS 1.16.0).
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* pin_fn = reinterpret_cast<solver_plugin_version_pin_fn>(
      ::dlsym(handle, "gtopt_solver_version_pin"));
  // Optional: identity of the shared libraries the plugin was linked
  // against (build-time record), for the solver-library identity check.
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* linked_fn = reinterpret_cast<solver_plugin_linked_libraries_fn>(
      ::dlsym(handle, "gtopt_plugin_linked_libraries"));
  std::optional<std::vector<LinkedLibraryRecord>> linked;
  if (linked_fn != nullptr) {
    int version = 0;
    const SolverLinkedLibrary* rows = nullptr;
    const int count = linked_fn(&version, &rows);
    if (version >= 1 && rows != nullptr && count >= 0) {
      const auto str = [](const char* p)
      { return std::string(p != nullptr ? p : ""); };
      linked.emplace();
      for (const auto& row : std::span(rows, static_cast<std::size_t>(count))) {
        linked->push_back(LinkedLibraryRecord {
            .soname = str(row.soname),
            .build_id = str(row.build_id),
            .sha256 = str(row.sha256),
            .path = str(row.path),
        });
      }
    }
  }
  // NOLINTBEGIN(concurrency-mt-unsafe)
  const char* llp_env = std::getenv("LD_LIBRARY_PATH");
  const char* preload_env = std::getenv("LD_PRELOAD");
  // NOLINTEND(concurrency-mt-unsafe)

  std::error_code ec;
  const auto plugin_canon = std::filesystem::weakly_canonical(path, ec);
  const auto plugin_elf = read_elf_identity(path);

  std::erase_if(
      names,
      [&](const std::string& solver)
      {
        SolverLibraryReport lib {.solver = solver};
        SolverBuildInfo info {};
        if (info_fn != nullptr && info_fn(solver.c_str(), &info) > 0) {
          lib.reported = true;
          lib.compiled = {
              .major = info.compiled_major,
              .minor = info.compiled_minor,
              .patch = info.compiled_patch,
          };
          lib.runtime = {
              .major = info.runtime_major,
              .minor = info.runtime_minor,
              .patch = info.runtime_patch,
          };
          if (const auto obj = find_loaded_object(info.library_anchor)) {
            lib.library_path = obj->path;
            lib.library_build_id = obj->build_id;
            lib.statically_linked = (obj->path == plugin_canon);
          }
        }

        // Shadowing: would the plugin's own RUNPATH have picked another
        // file?  (LD_LIBRARY_PATH beats DT_RUNPATH, and an already-loaded
        // library with the same soname is reused.)
        if (!lib.statically_linked && !lib.library_path.empty() && plugin_elf) {
          if (const auto lib_elf = read_elf_identity(lib.library_path)) {
            const auto expected =
                resolve_in_runpath(lib_elf->soname,
                                   plugin_elf->runpath,
                                   plugin_canon.parent_path());
            if (expected != lib.library_path) {
              lib.runpath_library = expected;
            }
          }
        }
        // NOLINTNEXTLINE(concurrency-mt-unsafe)
        const char* llp = std::getenv("LD_LIBRARY_PATH");
        lib.shadow_check = check_runpath_shadowing(solver,
                                                   lib.library_path,
                                                   lib.runpath_library,
                                                   llp != nullptr ? llp : "",
                                                   policy);

        lib.version_check = lib.reported
            ? check_solver_version(solver,
                                   lib.compiled,
                                   lib.runtime,
                                   lib.statically_linked,
                                   lib.library_path.string(),
                                   policy)
            : integrity_finding(
                  "unknown",
                  std::format("Plugin {} does not report solver-library "
                              "versions for '{}' (built by an older gtopt); "
                              "the loaded solver library cannot be verified.",
                              path.string(),
                              solver),
                  policy);
        // Pin: is the runtime library the release this gtopt pins?
        SolverVersionPin pin {};
        if (pin_fn != nullptr && pin_fn(solver.c_str(), &pin) > 0) {
          lib.pinned = {
              .major = pin.pinned_major,
              .minor = pin.pinned_minor,
              .patch = pin.pinned_patch,
          };
          lib.pin_built_allow_unpinned = pin.allow_unpinned != 0;
        }
        const VersionPinOverrides overrides {
            .built_allow_unpinned = lib.pin_built_allow_unpinned,
            .env_allow_unpinned = solver_allow_unpinned_from_env(solver),
        };

        // Identity: is the mapped solver library the one the plugin was
        // linked against?  (Build-id / SHA-256, not the self-reported
        // version, so a fake reporting "1.16.0" is caught.)
        LoadedLibraryIdentity loaded {
            .path = lib.library_path,
            .build_id = lib.library_build_id,
            .ld_library_path = llp_env != nullptr ? llp_env : "",
            .ld_preload = preload_env != nullptr ? preload_env : "",
        };
        if (!lib.statically_linked && !loaded.path.empty() && linked) {
          if (const auto elf = read_elf_identity(loaded.path)) {
            loaded.soname = elf->soname;
          }
          if (const auto* rec = match_linked_library(*linked, loaded)) {
            lib.expected_library_path = rec->path;
            // The record carries a SHA-256 when the library had no
            // build-id, or always under GTOPT_SOLVER_LIB_FULL_HASH=ON:
            // hash the file of the object actually mapped.
            if (!rec->sha256.empty()) {
              const auto t0 = std::chrono::steady_clock::now();
              loaded.sha256 = sha256_file(loaded.path);
              const auto ms = std::chrono::duration<double, std::milli>(
                                  std::chrono::steady_clock::now() - t0)
                                  .count();
              SPDLOG_INFO("    {}: sha256 of {} in {:.1f} ms",
                          solver,
                          loaded.path.string(),
                          ms);
            }
          }
        }
        // (The identity check ignores overrides.built_allow_unpinned.)
        lib.identity_check =
            check_solver_library_identity(solver,
                                          linked ? &*linked : nullptr,
                                          loaded,
                                          lib.statically_linked,
                                          overrides,
                                          policy);

        if (lib.pinned.known()) {
          lib.pin_check = check_solver_version_pin(solver,
                                                   lib.pinned,
                                                   lib.runtime,
                                                   lib.library_path.string(),
                                                   overrides,
                                                   policy);
        }

        if (lib.version_check.verdict == IntegrityVerdict::ok
            && !lib.version_check.message.empty())
        {
          SPDLOG_INFO("    {}", lib.version_check.message);
        }
        log_decision(lib.shadow_check);
        log_decision(lib.version_check);
        log_decision(lib.pin_check);
        log_decision(lib.identity_check);
        bool refused = false;
        for (const auto* check : {&lib.version_check,
                                  &lib.shadow_check,
                                  &lib.pin_check,
                                  &lib.identity_check})
        {
          if (check->verdict == IntegrityVerdict::refuse) {
            refused = true;
            m_load_errors_.push_back(
                std::format("Plugin {}: {}", path.string(), check->message));
          }
        }
        report.solvers.push_back(std::move(lib));
        return refused;
      });
}

bool SolverRegistry::load_plugin(const std::filesystem::path& path)
{
  // A plugin anyone may replace is never loaded.
  if (const auto why = insecure_plugin_location(path); !why.empty()) {
    const auto msg =
        std::format("Refusing to load plugin {}: {}", path.string(), why);
    SPDLOG_WARN("{}", msg);
    m_load_errors_.push_back(msg);
    return false;
  }
  SPDLOG_INFO("loading solver plugin {}", path.string());

  // Identity check BEFORE dlopen: a .so that does not match its build-id
  // manifest never gets to run its static initialisers.
  const auto policy = plugin_check_policy();
  PluginReport report;
  if (!verify_plugin_file(path, policy, report)) {
    return false;
  }

  // dlopen with RTLD_LOCAL so symbols don't leak between plugins
  auto* handle = ::dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (handle == nullptr) {
    const auto* err = ::dlerror();
    const auto msg = std::format("Failed to load plugin {}: {}",
                                 path.string(),
                                 (err != nullptr) ? err : "unknown");
    SPDLOG_WARN("{}", msg);
    m_load_errors_.push_back(msg);
    return false;
  }

  // Resolve required symbols.  reinterpret_cast is unavoidable here:
  // POSIX dlsym returns void* and the C++ standard provides no other
  // way to convert that to a function pointer.
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* name_fn = reinterpret_cast<solver_plugin_name_fn>(
      ::dlsym(handle, "gtopt_plugin_name"));
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* names_fn = reinterpret_cast<solver_plugin_names_fn>(
      ::dlsym(handle, "gtopt_solver_names"));
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* factory_fn = reinterpret_cast<solver_backend_factory_fn>(
      ::dlsym(handle, "gtopt_create_backend"));

  if (name_fn == nullptr || names_fn == nullptr || factory_fn == nullptr) {
    const auto msg =
        std::format("Plugin {} missing required symbols", path.string());
    SPDLOG_WARN("{}", msg);
    m_load_errors_.push_back(msg);
    ::dlclose(handle);
    return false;
  }

  // Optional symbol: `gtopt_solver_infinity` lets the registry expose
  // the solver's +infinity value without instantiating a backend.
  // Plugins built before this entry existed report nullptr; callers
  // fall back to instance-level query.
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* infinity_fn = reinterpret_cast<solver_plugin_infinity_fn>(
      ::dlsym(handle, "gtopt_solver_infinity"));

  // Optional symbol: `gtopt_solver_resource_descriptor` declares the
  // solver's resource class (CPU vs GPU) + estimated per-solve VRAM so the
  // work-pool admission layer can classify tasks before dispatch.  Plugins
  // that don't export it (all CPU solvers) report nullptr — treated as
  // CPU-class, today's behaviour.
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* resource_desc_fn = reinterpret_cast<solver_plugin_resource_desc_fn>(
      ::dlsym(handle, "gtopt_solver_resource_descriptor"));

  // Check ABI version compatibility.  Plugins built against a different
  // SolverBackend vtable layout would crash at runtime; reject them
  // early with a clear diagnostic instead.
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  auto* abi_fn = reinterpret_cast<solver_plugin_abi_version_fn>(
      ::dlsym(handle, "gtopt_plugin_abi_version"));
  const int plugin_abi = (abi_fn != nullptr) ? abi_fn() : 0;
  if (plugin_abi != k_solver_abi_version) {
    const auto msg = std::format(
        "Plugin {} ABI version mismatch: plugin={}, expected={}. "
        "Rebuild the plugin against the current gtopt headers.",
        path.string(),
        plugin_abi,
        k_solver_abi_version);
    SPDLOG_ERROR("{}", msg);
    m_load_errors_.push_back(msg);
    ::dlclose(handle);
    return false;
  }

  // Check for duplicate plugin name
  const std::string plugin_name = name_fn();
  for (const auto& existing : m_plugins_) {
    if (existing.plugin_name == plugin_name) {
      SPDLOG_DEBUG("Plugin '{}' already loaded, skipping {}",
                   plugin_name,
                   path.string());
      ::dlclose(handle);
      return false;
    }
  }

  // Collect solver names
  std::vector<std::string> solver_names;
  for (const auto* names = names_fn(); *names != nullptr; ++names) {
    solver_names.emplace_back(*names);
  }

  // Indent two spaces so the message sits under the "Building LP model"
  // section when plugin loading is triggered as a side effect of LP build.
  SPDLOG_INFO("  Loaded solver plugin '{}' from {} (solvers: {})",
              plugin_name,
              path.string(),
              join_strings(solver_names, ", "));
  report.plugin_name = plugin_name;
  verify_loaded_plugin(handle, path, policy, solver_names, report);
  if (solver_names.empty()) {
    ::dlclose(handle);
    return false;
  }
  log_plugin_identity(report, solver_names);

  m_plugins_.push_back(PluginHandle {
      .dl_handle = handle,
      .create_fn = factory_fn,
      .infinity_fn = infinity_fn,  // may be nullptr (older plugins)
      .resource_desc_fn = resource_desc_fn,  // may be nullptr (CPU solvers)
      .plugin_name = plugin_name,
      .solver_names = std::move(solver_names),
      .report = std::move(report),
  });

  return true;
}

// ── Plugin-level infinity query ─────────────────────────────────────────────

std::optional<double> SolverRegistry::plugin_infinity(
    std::string_view solver_name)
{
  // Resolve empty solver_name to the default solver (loads its plugin
  // as a side effect via default_solver()).  This matches the
  // convention in `LinearInterface`'s default-solver constructor.
  std::string resolved;
  if (solver_name.empty()) {
    resolved = std::string(default_solver());
    solver_name = resolved;
  } else {
    // Ensure the plugin providing this solver is loaded.
    const std::scoped_lock lock(m_mutex_);
    if (!has_solver_unlocked(solver_name)) {
      ensure_solver_loaded(solver_name);
    }
  }

  const std::scoped_lock lock(m_mutex_);
  for (const auto& plugin : m_plugins_) {
    for (const auto& sn : plugin.solver_names) {
      if (sn == solver_name) {
        if (plugin.infinity_fn != nullptr) {
          // Plugin built with the new entry point — fast path.
          return plugin.infinity_fn(std::string(solver_name).c_str());
        }
        // Older plugin without the entry point.  Caller falls back
        // to creating a backend instance and calling infinity().
        return std::nullopt;
      }
    }
  }
  return std::nullopt;
}

// ── Plugin-level resource-descriptor query ─────────────────────────────────

std::optional<SolverResourceDescriptor> SolverRegistry::resource_descriptor(
    std::string_view solver_name)
{
  // Same resolution convention as plugin_infinity(): empty name means the
  // process default solver; otherwise lazily load the providing plugin.
  std::string resolved;
  if (solver_name.empty()) {
    resolved = std::string(default_solver());
    solver_name = resolved;
  } else {
    const std::scoped_lock lock(m_mutex_);
    if (!has_solver_unlocked(solver_name)) {
      ensure_solver_loaded(solver_name);
    }
  }

  const std::scoped_lock lock(m_mutex_);
  for (const auto& plugin : m_plugins_) {
    for (const auto& sn : plugin.solver_names) {
      if (sn == solver_name) {
        if (plugin.resource_desc_fn == nullptr) {
          return std::nullopt;  // CPU solver / older plugin — CPU-class
        }
        SolverResourceDescriptor desc {};
        const int version =
            plugin.resource_desc_fn(std::string(solver_name).c_str(), &desc);
        if (version <= 0) {
          return std::nullopt;  // plugin doesn't know this solver name
        }
        return desc;
      }
    }
  }
  return std::nullopt;
}

bool SolverRegistry::is_gpu_backed(std::string_view solver_name)
{
  const auto desc = resource_descriptor(solver_name);
  return desc.has_value()
      && desc->resource_class == static_cast<int>(ResourceClass::gpu);
}

namespace
{

/// Post-fork stderr sink: formats under a `null_mutex` (the fork child is
/// single-threaded) and writes with a raw `::write(2, ...)`.  Unlike
/// `stderr_color_sink_mt` it takes neither spdlog's static console mutex
/// nor the `stderr` `FILE*` lock — both are process-wide and may have been
/// held by another parent thread at `fork()` time, which would leave the
/// child blocked in `futex_wait` forever.
class Fd2Sink final
    : public spdlog::sinks::base_sink<spdlog::details::null_mutex>
{
protected:
  void sink_it_(const spdlog::details::log_msg& msg) override
  {
    spdlog::memory_buf_t buf;
    formatter_->format(msg, buf);
    const char* data = buf.data();
    auto left = buf.size();
    while (left > 0) {
      const auto n = ::write(STDERR_FILENO, data, left);
      if (n < 0) {
        if (errno == EINTR) {
          continue;
        }
        return;  // best effort
      }
      data += n;  // NOLINT(cppcoreguidelines-pro-bounds-pointer-arithmetic)
      left -= static_cast<std::size_t>(n);
    }
  }

  void flush_() override {}
};

/// Compile-time ThreadSanitizer detection (GCC defines
/// `__SANITIZE_THREAD__`; Clang exposes `__has_feature(thread_sanitizer)`).
#ifdef __SANITIZE_THREAD__
constexpr bool k_tsan_build = true;
#elifdef __has_feature
#  if __has_feature(thread_sanitizer)
constexpr bool k_tsan_build = true;
#  else
constexpr bool k_tsan_build = false;
#  endif
#else
constexpr bool k_tsan_build = false;
#endif

/// Exit codes of the solver-validation probe (`run_solver_probe`).
constexpr int probe_ok = 0;
constexpr int probe_failed = 1;
constexpr int probe_not_optimal = 2;
constexpr int probe_license = 3;
constexpr int probe_orphan = 4;

/// Create a backend for @p solver_name and solve a 1x1 LP end-to-end
/// (min x s.t. x >= 1, 0 <= x <= 10).  Catches both vtable ABI mismatches
/// and solvers that can be created but crash while solving.  Runs in the
/// fork child (default) or in-process (ThreadSanitizer builds).
[[nodiscard]] int run_solver_probe(solver_backend_factory_fn create_fn,
                                   const std::string& solver_name) noexcept
{
  try {
    const std::unique_ptr<SolverBackend> backend {
        create_fn(solver_name.c_str()),
    };
    if (!backend) {
      return probe_failed;
    }
    // Step 1: exercise basic vtable accessors.
    (void)backend->solver_name();
    const auto inf = backend->infinity();

    // Step 2: solve a minimal LP to confirm the solver works end-to-end.
    const int nc = 1;
    const int nr = 1;
    const std::array<int, 2> matbeg {0, 1};
    const std::array<int, 1> matind {0};
    const std::array<double, 1> matval {1.0};
    const std::array<double, 1> collb {0.0};
    const std::array<double, 1> colub {10.0};
    const std::array<double, 1> objv {1.0};
    const std::array<double, 1> rowlb {1.0};
    const std::array<double, 1> rowub {inf};
    backend->load_problem(nc,
                          nr,
                          matbeg.data(),
                          matind.data(),
                          matval.data(),
                          collb.data(),
                          colub.data(),
                          objv.data(),
                          rowlb.data(),
                          rowub.data());
    backend->initial_solve();
    return backend->is_proven_optimal() ? probe_ok : probe_not_optimal;
  } catch (const std::exception& e) {
    // Licensed backends (gurobi, mindopt) throw from their constructor
    // when no license is reachable (message contains "license") — report
    // that with a distinct code so the caller logs an honest
    // "license unavailable" skip instead of the ABI-mismatch diagnostic.
    const std::string_view what {e.what()};
    return what.contains("icense") ? probe_license : probe_failed;
  } catch (...) {
    return probe_failed;
  }
}

/// Turn a probe outcome into the validation verdict and log why a solver
/// is dropped.  @p code is the probe exit code (`std::nullopt` when the
/// child died by a signal); @p detail describes it for the log.
[[nodiscard]] bool accept_probe_outcome(std::optional<int> code,
                                        std::string_view detail,
                                        const std::string& solver_name,
                                        const std::string& plugin_name)
{
  if (code == probe_ok) {
    return true;
  }

  // License-gated backend: the plugin is healthy but the solver cannot
  // start without a license (e.g. gurobi with no gurobi.lic, mindopt
  // MDOstartenv rc=-10).  Removing it from the registry is the clean
  // gate — `available_solvers()` / `exact_mip_solvers()` never list it, so
  // every per-solver test sweep skips it instead of failing.
  if (code == probe_license) {
    SPDLOG_WARN(
        "Solver '{}' from plugin '{}' is unavailable: license not found or "
        "invalid — skipping it (install a valid license to enable it).",
        solver_name,
        plugin_name);
    return false;
  }

  SPDLOG_WARN(
      "Solver '{}' from plugin '{}' failed validation ({}). "
      "This usually indicates an ABI mismatch between the solver "
      "library and the gtopt binary (e.g. mixed gcc/clang compilers).",
      solver_name,
      plugin_name,
      detail);
  return false;
}

}  // namespace

bool SolverRegistry::arm_child_parent_death_signal(pid_t parent_pid) noexcept
{
  // Die with the parent: if `gtopt --solvers` is killed (e.g. by a caller's
  // subprocess timeout) a hung validation child must not linger as an
  // orphan.  PDEATHSIG fires when the parent *thread* exits; the probe runs
  // on the main thread of a CLI process, which is what we want.
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
  (void)::prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0);
  // The parent may already be gone before the prctl took effect: then we
  // were reparented (to init or a subreaper) and must exit ourselves.
  return ::getppid() == parent_pid;
}

std::optional<int> SolverRegistry::wait_child_with_timeout(
    pid_t pid, std::chrono::milliseconds timeout) noexcept
{
  using clock = std::chrono::steady_clock;
  const auto deadline = clock::now() + timeout;
  auto backoff = std::chrono::milliseconds {1};
  constexpr auto max_backoff = std::chrono::milliseconds {50};

  while (true) {
    int status = 0;
    const pid_t r = ::waitpid(pid, &status, WNOHANG);
    if (r == pid) {
      return status;
    }
    if (r == -1) {
      if (errno == EINTR) {
        continue;
      }
      // ECHILD etc.: nothing to wait for.  Matches the historic blocking
      // waitpid() whose ignored failure left status = 0.
      return 0;
    }
    // r == 0: child still running.
    if (clock::now() >= deadline) {
      break;
    }
    std::this_thread::sleep_for(backoff);
    backoff = std::min(backoff * 2, max_backoff);
  }

  // Timed out: kill and reap so no zombie / orphan survives.
  (void)::kill(pid, SIGKILL);
  int status = 0;
  while (::waitpid(pid, &status, 0) == -1 && errno == EINTR) {}
  return std::nullopt;
}

void SolverRegistry::reset_default_logger_after_fork() noexcept
{
  // Order matters: spdlog::drop_all() clears the registry AND resets the
  // default logger to null (registry::drop_all → default_logger_.reset()).
  // Detach the inherited async handles FIRST, then install the fresh
  // synchronous default so it survives.  Installing before dropping leaves a
  // null default logger, and the next spdlog::*() call dereferences it — the
  // validation child SIGSEGV'd exactly here (should_log() on null, si_addr
  // 0x40) on every run that validated an unavailable plugin (e.g. MindOpt).
  try {
    spdlog::drop_all();
    // Fd2Sink, NOT stderr_color_sink_mt: the latter locks spdlog's static
    // console mutex, which a parent thread may have held at fork() time.
    auto sink = std::make_shared<Fd2Sink>();
    auto child_logger =
        std::make_shared<spdlog::logger>("gtopt_child_validator", sink);
    child_logger->set_level(spdlog::level::warn);
    spdlog::set_default_logger(std::move(child_logger));
  } catch (...) {
    // Best effort — if the logger swap fails, validation continues without
    // logging.  We must NOT propagate exceptions across the fork barrier.
  }
}

bool SolverRegistry::validate_solver_subprocess(
    const PluginHandle& plugin,
    const std::string& solver_name,
    std::chrono::milliseconds timeout)
{
  // ThreadSanitizer cannot follow a multi-threaded fork(): the child dies
  // ("starting new threads after multi-threaded fork is not supported",
  // exit 66) as soon as the probe's solver spawns a thread (HiGHS and
  // MindOpt always do), so every such solver would be dropped and
  // GTOPT_SOLVER would fall back to another backend.  A TSan build is a
  // development build against known-good solver libraries: run the same
  // probe in-process instead.
  if constexpr (k_tsan_build) {
    const int code = run_solver_probe(plugin.create_fn, solver_name);
    return accept_probe_outcome(code,
                                std::format("in-process probe code {}", code),
                                solver_name,
                                plugin.plugin_name);
  }

  const pid_t parent_pid = ::getpid();
  // Fork a child process to test the solver backend.
  // This isolates the parent from SEGFAULTs caused by ABI mismatches
  // in the underlying solver library (e.g. COIN-OR compiled with GCC
  // but the plugin loaded into a Clang-compiled binary).
  const pid_t pid = ::fork();

  if (pid == -1) {
    // fork() failed — cannot validate, assume solver is usable.
    SPDLOG_WARN("fork() failed while validating solver '{}', assuming usable",
                solver_name);
    return true;
  }

  if (pid == 0) {
    // Die with the parent; bail out if it is already gone (orphan guard).
    if (!SolverRegistry::arm_child_parent_death_signal(parent_pid)) {
      ::_exit(probe_orphan);
    }

    // Child process: create a backend and exercise it end-to-end.
    // Any crash here (SIGSEGV, SIGABRT, etc.) terminates only the child.
    //
    // ── async-logger fork race ─────────────────────────────────────────
    // The parent installs `gtopt_async` as the default logger at the top
    // of `main()`.  `fork()` duplicates the parent's address space but
    // does NOT replicate threads — so the inherited `async_logger` here
    // has a queue + handle but no worker thread to drain it.  Any
    // `spdlog::*(...)` call from this branch would enqueue into a dead
    // queue (and, under the `block` policy, deadlock forever; under
    // `overrun_oldest`, silently lose every message including the
    // diagnostic that should help the user understand what went wrong).
    //
    // Mitigation: swap in a fresh synchronous stderr logger before any
    // logging happens (see SolverRegistry::reset_default_logger_after_fork
    // for the ordering rationale).  We deliberately don't call
    // `spdlog::shutdown()` — that walks the registry and flushes every async
    // logger (including ours), which would just hang.
    SolverRegistry::reset_default_logger_after_fork();

    // Reset signal handlers so doctest's crash-recovery logic (inherited
    // from the parent via fork) does not fire inside the child — without
    // this, an uncaught exception → abort → doctest's SIGABRT handler
    // → spurious crash output that confuses doctest's test-rerun loop.
    // SIG_DFL expands to a C-style cast and the ::signal result is
    // deliberately discarded (best-effort child-side reset).
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-cstyle-cast,cert-err33-c)
    ::signal(SIGABRT, SIG_DFL);
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-cstyle-cast,cert-err33-c)
    ::signal(SIGSEGV, SIG_DFL);
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-cstyle-cast,cert-err33-c)
    ::signal(SIGFPE, SIG_DFL);

    ::_exit(run_solver_probe(plugin.create_fn, solver_name));
  }

  // Parent: wait for the child, bounded.  This timeout exists ONLY for this
  // administrative startup probe: a child that inherited a lock held at
  // fork() time would otherwise block `gtopt --solvers` forever.
  const auto waited = wait_child_with_timeout(pid, timeout);
  if (!waited.has_value()) {
    const auto secs =
        std::chrono::duration_cast<std::chrono::seconds>(timeout).count();
    SPDLOG_WARN(
        "Validation of solver '{}' (plugin '{}') did not finish in {} "
        "(child pid {}), treating as unavailable.",
        solver_name,
        plugin.plugin_name,
        secs > 0 ? std::format("{} s", secs)
                 : std::format("{} ms", timeout.count()),
        pid);
    return false;
  }
  const int status = *waited;

  //  - POSIX macros use bitwise ops
  if (WIFSIGNALED(status)) {
    return accept_probe_outcome(std::nullopt,
                                std::format("signal {}", WTERMSIG(status)),
                                solver_name,
                                plugin.plugin_name);
  }
  const int code = WEXITSTATUS(status);
  return accept_probe_outcome(
      code, std::format("exit code {}", code), solver_name, plugin.plugin_name);
}

void SolverRegistry::validate_loaded_solvers()
{
  for (auto& plugin : m_plugins_) {
    std::erase_if(plugin.solver_names,
                  [this, &plugin](const std::string& name)
                  {
                    if (!validate_solver_subprocess(plugin, name)) {
                      m_load_errors_.push_back(
                          std::format("Solver '{}' removed: failed subprocess "
                                      "validation",
                                      name));
                      return true;
                    }
                    return false;
                  });
  }

  // Remove plugins with no remaining solver names.
  std::erase_if(m_plugins_,
                [](const PluginHandle& p) { return p.solver_names.empty(); });
}

void SolverRegistry::load_all_plugins()
{
  const std::scoped_lock lock(m_mutex_);
  if (m_all_loaded_) {
    return;
  }
  for (const auto& path : m_pending_paths_) {
    load_plugin(path);
  }
  m_pending_paths_.clear();
  validate_loaded_solvers();
  m_all_loaded_ = true;
}

bool SolverRegistry::ensure_solver_loaded(std::string_view solver_name,
                                          bool filename_only)
{
  const std::scoped_lock lock(m_mutex_);

  // Already loaded?
  if (has_solver_unlocked(solver_name)) {
    return true;
  }

  // Try loading pending plugins one by one until we find the solver.
  // We infer the solver name from the plugin filename to try the most
  // likely candidate first (e.g., "highs" → libgtopt_solver_highs.so).
  const auto target = std::format("libgtopt_solver_{}.so", solver_name);

  // First pass: try the best-match filename.
  for (auto it = m_pending_paths_.begin(); it != m_pending_paths_.end();) {
    if (it->filename() != target) {
      ++it;
      continue;
    }
    auto path = std::move(*it);
    it = m_pending_paths_.erase(it);
    load_plugin(path);
    // Validate only the newly loaded plugin.
    if (!m_plugins_.empty()) {
      auto& last = m_plugins_.back();
      std::erase_if(last.solver_names,
                    [this, &last](const std::string& name)
                    {
                      if (!validate_solver_subprocess(last, name)) {
                        m_load_errors_.push_back(std::format(
                            "Solver '{}' removed: failed subprocess "
                            "validation",
                            name));
                        return true;
                      }
                      return false;
                    });
      if (last.solver_names.empty()) {
        m_plugins_.pop_back();
      }
    }
    if (has_solver_unlocked(solver_name)) {
      return true;
    }
    // Validation failed and removed the solver: keep scanning the
    // remaining candidates.  `it` already points past the erased entry —
    // incrementing the erased iterator here was UB (SIGSEGV in
    // path::filename) whenever the requested solver failed validation.
  }

  // Second pass: try all remaining pending plugins.
  // Skip if caller only wants filename-based matching (e.g. default_solver
  // probing — avoids loading every plugin when a name isn't found).
  if (filename_only) {
    return false;
  }
  while (!m_pending_paths_.empty()) {
    const auto path = m_pending_paths_.back();
    m_pending_paths_.pop_back();
    load_plugin(path);
    // Validate the newly loaded plugin.
    if (!m_plugins_.empty()) {
      auto& last = m_plugins_.back();
      std::erase_if(last.solver_names,
                    [this, &last](const std::string& name)
                    {
                      if (!validate_solver_subprocess(last, name)) {
                        m_load_errors_.push_back(std::format(
                            "Solver '{}' removed: failed subprocess "
                            "validation",
                            name));
                        return true;
                      }
                      return false;
                    });
      if (last.solver_names.empty()) {
        m_plugins_.pop_back();
      }
    }
    if (has_solver_unlocked(solver_name)) {
      return true;
    }
  }

  return false;
}

std::unique_ptr<SolverBackend> SolverRegistry::create(
    std::string_view solver_name)
{
  // We must NOT hold `m_mutex_` across `plugin.create_fn()`: for
  // heavyweight backends (notably CPLEX → `CPXopenCPLEX()`) that call
  // can take several milliseconds per invocation, and the
  // `PlanningLP::create_systems` parallel build fires one
  // `LinearInterface(...)` per (scene, phase) cell (`linear_interface.
  // cpp:75-80`).  With hundreds of cells and a non-trivial per-create
  // cost, the old hold-lock-across-create_fn pattern serialized the
  // whole build onto a single mutex and capped pool efficiency.
  //
  // Lookup runs under the lock — `m_plugins_` is a vector and would
  // be UB to read unlocked if a concurrent `ensure_solver_loaded`
  // could grow it — but we copy the function pointer and plugin name
  // out of the lookup, release the lock, and *then* call `create_fn`.
  // Each plugin is a DSO whose text segment is stable once loaded, so
  // concurrent calls into `create_fn` are safe by construction.
  solver_backend_factory_fn create_fn {};
  std::string plugin_name;
  {
    const std::scoped_lock lock(m_mutex_);
    ensure_solver_loaded(solver_name);
    for (const auto& plugin : m_plugins_) {
      for (const auto& name : plugin.solver_names) {
        if (name == solver_name) {
          create_fn = plugin.create_fn;
          plugin_name = plugin.plugin_name;
          break;
        }
      }
      if (create_fn != nullptr) {
        break;
      }
    }
  }  // ← lock released here, before the heavy create_fn call.

  if (create_fn != nullptr) {
    auto* backend = create_fn(std::string(solver_name).c_str());
    if (backend == nullptr) {
      throw std::runtime_error(
          std::format("Plugin '{}' failed to create solver '{}'",
                      plugin_name,
                      solver_name));
    }
    return std::unique_ptr<SolverBackend>(backend);
  }

  // Unknown solver — load everything so the error message can list
  // every available alternative.  Re-acquires the lock inside
  // `load_all_plugins` / `available_solvers`.
  load_all_plugins();
  const auto available = available_solvers();
  std::vector<std::string> errors;
  {
    const std::scoped_lock lock(m_mutex_);
    errors = m_load_errors_;
  }
  throw std::runtime_error(
      solver_unavailable_message(solver_name, available, errors));
}

std::vector<std::string> solver_load_errors(
    std::string_view solver_name, const std::vector<std::string>& load_errors)
{
  const auto by_solver = std::format("Solver '{}'", solver_name);
  const auto by_plugin = std::format("libgtopt_solver_{}", solver_name);
  std::vector<std::string> out;
  std::ranges::copy_if(
      load_errors,
      std::back_inserter(out),
      [&](const std::string& e)
      { return e.contains(by_solver) || e.contains(by_plugin); });
  return out;
}

bool env_solver_must_fail(std::string_view solver_name,
                          const std::vector<std::string>& load_errors,
                          bool fallback_on_refused)
{
  if (fallback_on_refused) {
    return false;
  }
  return std::ranges::any_of(solver_load_errors(solver_name, load_errors),
                             [](const std::string& e)
                             { return e.contains(k_plugin_refused_marker); });
}

bool solver_fallback_on_refused_from_env()
{
  // NOLINTNEXTLINE(concurrency-mt-unsafe)
  const char* v =
      std::getenv(std::string(k_solver_fallback_on_refused_env).c_str());
  if (v == nullptr) {
    return false;
  }
  const auto flag = parse_env_flag(v);
  if (!flag) {
    SPDLOG_WARN("Ignoring {}='{}': expected 1/true/yes/on or 0/false/no/off",
                k_solver_fallback_on_refused_env,
                v);
  }
  return flag.value_or(false);
}

std::string solver_unavailable_message(
    std::string_view solver_name,
    const std::vector<std::string>& available,
    const std::vector<std::string>& load_errors)
{
  std::string out = std::format("Solver '{}' not available.", solver_name);
  const auto why = solver_load_errors(solver_name, load_errors);
  for (const auto& e : why) {
    out += std::format("\n  {}", e);
  }
  out +=
      std::format("{}Available solvers: {}",
                  why.empty() ? " " : "\n",
                  available.empty() ? std::string("(none — no plugins loaded)")
                                    : join_strings(available, ", "));
  return out;
}

std::string no_solver_message(const std::vector<std::string>& load_errors)
{
  const bool any_refused = std::ranges::any_of(
      load_errors,
      [](const std::string& e) { return e.contains(k_plugin_refused_marker); });
  if (!any_refused) {
    return "No solver plugins found.\n"
           "Hints:\n"
           "  - Set GTOPT_PLUGIN_DIR to the directory containing solver "
           "plugin libraries\n"
           "  - Ensure libgtopt_solver_osi.so and/or libgtopt_solver_highs.so "
           "are installed\n"
           "  - Install COIN-OR (coinor-libcbc-dev) for CLP/CBC support\n"
           "  - Install HiGHS for HiGHS support\n"
           "  - Run 'gtopt --solvers' to list available LP solvers";
  }
  std::string out =
      "No solver available: solver plugins were found, but every one was "
      "refused or failed to load:";
  for (const auto& e : load_errors) {
    out += std::format("\n  {}", e);
  }
  out +=
      "\nHints:\n"
      "  - Each refusal above names its fix and its override\n"
      "  - --plugin-check=warning (or GTOPT_PLUGIN_CHECK=warning) loads "
      "refused plugins anyway\n"
      "  - Run 'gtopt --solvers' to list the refused plugins";
  return out;
}

std::vector<std::string> SolverRegistry::available_solvers() const
{
  const std::scoped_lock lock(m_mutex_);
  // Use the `std::ranges::to(range)` call form, NOT the `range |
  // std::ranges::to<...>()` pipe form: clang-21 with libstdc++-14 (the coverage
  // build) fails to compile `views::join | std::ranges::to<...>()`
  // ("function 'forward_like<...>' with deduced return type cannot be used
  // before it is defined").  The call form — already used throughout
  // flat_helper.hpp and simulation_lp.cpp — compiles on every toolchain.
  return std::ranges::to<std::vector>(
      m_plugins_
      | std::views::transform([](const auto& p) -> const auto&
                              { return p.solver_names; })
      | std::views::join);
}

std::string_view SolverRegistry::default_solver()
{
  const std::scoped_lock lock(m_mutex_);

  // GTOPT_SOLVER env var overrides the default priority.
  if (const auto* env = std::getenv("GTOPT_SOLVER")) {
    if (ensure_solver_loaded(env)) {
      return env;
    }
    // A REFUSED plugin (integrity / version / pin check) is an error, like
    // an explicit --solver; only a solver with no plugin falls back.
    // $GTOPT_SOLVER_FALLBACK_ON_REFUSED=1 restores "always fall back".
    if (env_solver_must_fail(
            env, m_load_errors_, solver_fallback_on_refused_from_env()))
    {
      load_all_plugins();
      throw std::runtime_error(
          solver_unavailable_message(env, available_solvers(), m_load_errors_));
    }
    const auto why = solver_load_errors(env, m_load_errors_);
    SPDLOG_WARN("GTOPT_SOLVER='{}' not available, falling back to auto{}{}",
                env,
                why.empty() ? "" : ": ",
                join_strings(why, "; "));
  }

  // Priority order: cplex > highs > mindopt > cbc > clp
  // First pass: try filename-based matching only (fast, no exhaustive scan).
  static constexpr std::array preferred = {
      "cplex",
      "highs",
      "mindopt",
      "cbc",
      "clp",
  };
  for (const auto* name : preferred) {
    if (ensure_solver_loaded(name, /*filename_only=*/true)) {
      return name;
    }
  }

  // Second pass: some solvers live in differently-named plugins
  // (e.g. "clp" in libgtopt_solver_osi.so).  Load all and pick best.
  load_all_plugins();
  for (const auto* name : preferred) {
    if (has_solver_unlocked(name)) {
      return name;
    }
  }

  throw std::runtime_error(no_solver_message(m_load_errors_));
}

bool SolverRegistry::has_solver_unlocked(std::string_view name) const
{
  return std::ranges::any_of(m_plugins_,
                             [name](const PluginHandle& plugin)
                             {
                               return std::ranges::any_of(
                                   plugin.solver_names,
                                   [name](const std::string& s)
                                   { return s == name; });
                             });
}

bool SolverRegistry::has_solver(std::string_view name) const
{
  const std::scoped_lock lock(m_mutex_);
  return has_solver_unlocked(name);
}

bool SolverRegistry::supports_mip(std::string_view name)
{
  // Use the public create() path so that lazy-load semantics match
  // the call-site that would actually instantiate the backend.
  try {
    auto backend = create(name);
    return backend && backend->supports_mip();
  } catch (const std::exception&) {
    return false;
  }
}

bool SolverRegistry::has_mip_solver()
{
  load_all_plugins();
  const auto solvers = available_solvers();
  return std::ranges::any_of(
      solvers, [this](const std::string& s) { return supports_mip(s); });
}

std::vector<PluginReport> SolverRegistry::plugin_reports() const
{
  const std::scoped_lock lock(m_mutex_);
  std::vector<PluginReport> out;
  out.reserve(m_plugins_.size());
  for (const auto& plugin : m_plugins_) {
    auto rep = plugin.report;
    std::erase_if(
        rep.solvers,
        [&plugin](const SolverLibraryReport& s)
        { return !std::ranges::contains(plugin.solver_names, s.solver); });
    out.push_back(std::move(rep));
  }
  return out;
}

const std::vector<std::string>& SolverRegistry::searched_directories() const
{
  return m_searched_dirs_;
}

const std::vector<std::string>& SolverRegistry::load_errors() const
{
  return m_load_errors_;
}

}  // namespace gtopt
