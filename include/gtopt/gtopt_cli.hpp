/**
 * @file      gtopt_cli.hpp
 * @brief     Testable command-line front end of the gtopt standalone binary
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `standalone/source/main.cpp` is a thin wrapper around
 * `gtopt_cli_main()`.  Everything the binary does before handing a
 * `MainOptions` to `gtopt_main()` — argument parsing, the informational
 * sub-commands (`--help`, `--version`, `--solvers`, `--check-solvers`,
 * `--list-dialects`, `--json-schema`), config-file merging, logging set-up
 * and the exit-code mapping — lives here so unit tests can drive it with
 * an argument vector and capture the output streams instead of spawning
 * the process.
 *
 * Exit codes (shared with `gtopt_main` / `classify_error_exit_code`):
 *   0 = success (optimal solution, or an informational command)
 *   1 = non-optimal solution, CLI parse error, or a failed info command
 *   2 = input error (missing file, invalid JSON, bad options)
 *   3 = internal error (unexpected exception, solver crash)
 */

#pragma once

#include <iosfwd>
#include <string>
#include <string_view>
#include <vector>

#include <gtopt/cli_options.hpp>

namespace gtopt
{

/// Print the `--help` text: the option table followed by the examples
/// and output-layout sections.
void print_cli_help(std::ostream& out, const cli::options_description& desc);

/// `--solvers`: list the loadable LP solver plugins (or the search
/// directories, load errors and hints when none is found).
/// @return 0 (the listing itself never fails).
[[nodiscard]] int print_available_solvers(std::ostream& out);

/// `--check-solvers [NAME]`: run the solver self-test suite.  An empty
/// @p solver runs every available solver; an unknown one is reported on
/// @p err.
/// @return 0 when every test passes, 1 otherwise.
[[nodiscard]] int run_check_solvers_command(std::string_view solver,
                                            bool verbose,
                                            std::ostream& out,
                                            std::ostream& err);

/// `--list-dialects [NAME]`: tab-separated dump of the naming/unit
/// registries, optionally restricted to one dialect.
/// @return 0 on success, 1 for an unknown dialect filter.
[[nodiscard]] int print_naming_dialects(std::string_view filter,
                                        std::ostream& out,
                                        std::ostream& err);

/// `--json-schema [NAME]`: dump the JSON Schema of the input model.
/// @return 0 on success, 1 for an unknown schema name.
[[nodiscard]] int print_json_schema(std::string_view name, std::ostream& out);

/**
 * @brief Full command-line entry point of the gtopt binary.
 *
 * @param args            Command-line arguments WITHOUT the program name
 *                        (i.e. `argv[1..argc)`).
 * @param out             Stream for regular output (help, listings).
 * @param err             Stream for error diagnostics of the info commands.
 * @param stdout_is_tty   Whether the process stdout is interactive.  When
 *                        false (and not `--quiet`), spdlog output is
 *                        redirected to `<log_directory>/gtopt_N.log`.
 *                        `main()` passes `isatty(STDOUT_FILENO) != 0`.
 * @return Process exit code (see file header).
 */
[[nodiscard]] int gtopt_cli_main(const std::vector<std::string>& args,
                                 std::ostream& out,
                                 std::ostream& err,
                                 bool stdout_is_tty);

}  // namespace gtopt
