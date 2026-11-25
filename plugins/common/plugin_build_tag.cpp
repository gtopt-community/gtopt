/**
 * @file      plugin_build_tag.cpp
 * @brief     `gtopt_plugin_build_tag` entry point shared by every plugin
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Compiled into each solver plugin by `gtopt_add_solver_plugin`.  The tag
 * is expanded from the generated `git_revision.hpp` IN THIS TU, so it is
 * the plugin's own build — never resolved through the host's symbols.
 */

#include <gtopt/git_revision.hpp>

extern "C"
{
const char* gtopt_plugin_build_tag()  // NOLINT
{
  return GTOPT_BUILD_TAG;
}
}
