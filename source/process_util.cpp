/**
 * @file      process_util.cpp
 * @brief     Safe helpers for locating and spawning external tools
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <gtopt/process_util.hpp>
#include <spdlog/spdlog.h>
#include <unistd.h>  // access, environ

namespace gtopt
{

namespace
{

/// True when @p candidate is a regular file the user may execute.
[[nodiscard]] bool is_executable_file(const std::filesystem::path& candidate)
{
  std::error_code ec;
  return std::filesystem::is_regular_file(candidate, ec)
      && ::access(candidate.c_str(), X_OK) == 0;
}

}  // namespace

std::filesystem::path executable_directory()
{
  std::error_code ec;
  const auto exe = std::filesystem::read_symlink("/proc/self/exe", ec);
  if (ec || exe.empty()) {
    return {};
  }
  return exe.parent_path();
}

bool is_world_writable_directory(const std::filesystem::path& dir) noexcept
{
  std::error_code ec;
  const auto st = std::filesystem::status(dir, ec);
  if (ec || !std::filesystem::is_directory(st)) {
    return false;
  }
  return (st.permissions() & std::filesystem::perms::others_write)
      != std::filesystem::perms::none;
}

std::string find_trusted_executable(std::string_view name)
{
  if (name.empty() || name.contains('/')) {
    return {};
  }

  // 1. Next to the running executable: tools that ship with gtopt.
  if (const auto exe_dir = executable_directory(); !exe_dir.empty()) {
    if (const auto candidate = exe_dir / name; is_executable_file(candidate)) {
      return candidate.string();
    }
  }

  // 2. Absolute, non-world-writable PATH entries.
  const char* path_env = std::getenv("PATH");
  if (path_env == nullptr) {
    return {};
  }
  std::string_view rest {path_env};
  while (true) {
    const auto colon = rest.find(':');
    const auto dir = rest.substr(0, colon);
    if (!dir.empty() && dir.front() == '/') {
      const std::filesystem::path dir_path {dir};
      if (!is_world_writable_directory(dir_path)) {
        if (const auto candidate = dir_path / name;
            is_executable_file(candidate))
        {
          return candidate.string();
        }
      }
    }
    if (colon == std::string_view::npos) {
      break;
    }
    rest.remove_prefix(colon + 1);
  }
  return {};
}

char* const* spawn_environment() noexcept
{
  // `environ` is the POSIX process environment; posix_spawn wants exactly
  // this pointer.  Centralised here so the call sites stay lint-clean.
  // NOLINTNEXTLINE(cppcoreguidelines-pro-bounds-array-to-pointer-decay)
  return environ;
}

bool create_exclusive_file(const std::filesystem::path& path) noexcept
{
  try {
    // std::ios::noreplace = exclusive create (O_EXCL): fails on any existing
    // entry, a dangling symlink included.
    const std::ofstream out(path, std::ios::out | std::ios::noreplace);
    if (!out) {
      return false;
    }
    std::error_code ec;
    std::filesystem::permissions(path,
                                 std::filesystem::perms::owner_read
                                     | std::filesystem::perms::owner_write,
                                 std::filesystem::perm_options::replace,
                                 ec);
    return !ec;
  } catch (...) {
    return false;
  }
}

std::vector<std::pair<std::string, std::string>> gtopt_environment(
    char* const* envp)
{
  constexpr std::string_view prefix = "GTOPT_";
  constexpr std::array<std::string_view, 4> secret_words {
      "TOKEN",
      "KEY",
      "SECRET",
      "PASSWORD",
  };
  std::vector<std::pair<std::string, std::string>> out;
  for (auto* const* p = envp; p != nullptr && *p != nullptr; ++p) {
    const std::string_view entry {*p};
    const auto eq = entry.find('=');
    if (eq == std::string_view::npos || !entry.starts_with(prefix)) {
      continue;
    }
    std::string name {entry.substr(0, eq)};
    std::string value {entry.substr(eq + 1)};
    if (std::ranges::any_of(secret_words,
                            [&name](std::string_view w)
                            { return name.contains(w); }))
    {
      value = "***";
    }
    out.emplace_back(std::move(name), std::move(value));
  }
  std::ranges::sort(out);
  return out;
}

void log_gtopt_environment()
{
  for (const auto& [name, value] : gtopt_environment(spawn_environment())) {
    if (name.starts_with("GTOPT_TEST_")) {
      spdlog::warn("environment: {}={} (test hook; not for production runs)",
                   name,
                   value);
    } else {
      spdlog::info("environment: {}={}", name, value);
    }
  }
}

}  // namespace gtopt
