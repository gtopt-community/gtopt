/**
 * @file      gtopt_cli.cpp
 * @brief     Command-line front end of the gtopt standalone binary
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Moved out of `standalone/source/main.cpp` so the argument parsing, the
 * informational sub-commands and the exit-code mapping can be unit
 * tested in-process (see test/source/test_gtopt_cli.cpp).
 */

#include <cstddef>
#include <exception>
#include <filesystem>
#include <format>
#include <ostream>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <gtopt/build_info.hpp>
#include <gtopt/check_solvers.hpp>
#include <gtopt/config_file.hpp>
#include <gtopt/gtopt_cli.hpp>
#include <gtopt/gtopt_main.hpp>
#include <gtopt/json_schema.hpp>
#include <gtopt/main_options.hpp>
#include <gtopt/names_registry.hpp>
#include <gtopt/resolve_planning_args.hpp>
#include <gtopt/solver_registry.hpp>
#include <gtopt/unit_registry.hpp>
#include <spdlog/cfg/argv.h>
#include <spdlog/cfg/env.h>
#include <spdlog/spdlog.h>

namespace gtopt
{

void print_cli_help(std::ostream& out, const cli::options_description& desc)
{
  out << "GTOpt " << build_info().version_string()
      << " - Generation, Transmission and storage Optimization\n"
         "Optimization and analysis of power-system generation, "
         "transmission and storage\n"
         "(expansion planning, hydrothermal scheduling, unit "
         "commitment and dispatch).\n\n";
  out << desc << '\n';
  out << R"(Examples
========

  Run a planning JSON file (default: monolithic, multi-bus, all auto):
    gtopt case.json

  Run on a directory ('case_dir/case_dir.json' is auto-detected):
    gtopt case_dir/

  SDDP with the compressed memory mode (releases solver between phases,
  keeps a compressed flat-LP snapshot for fast reconstruct):
    gtopt case.json --memory-saving compress

  Single-bus (copper-plate) mode — ignores all line limits:
    gtopt case.json --set model_options.use_single_bus=true

  Skip Kirchhoff voltage-law constraints (DC OPF only):
    gtopt case.json --set model_options.use_kirchhoff=false

  Pick a specific LP/MIP backend:
    gtopt case.json --solver cplex
    gtopt case.json --solver highs

  Validate input + build the LP without solving (fast schema /
  feasibility check; emits 'Validation: …' warnings + errors):
    gtopt case.json --lp-only

  Suppress all log output to stdout (logs still land in the per-run
  '<output>/logs/gtopt_N.log' file when stdout is non-interactive):
    gtopt case.json --quiet

  Override scalar fields via --set (dotted path, repeatable; values
  are auto-typed bool/int/double/string).  Example: pin SDDP threads
  and disable Kirchhoff in one shot:
    gtopt case.json --set sddp_options.forward_solver_options.threads=8 \
                    --set model_options.use_kirchhoff=false

  Save the merged planning JSON for inspection:
    gtopt case.json --json-file merged_case.json

  Save the assembled LP for solver-side inspection:
    gtopt case.json --lp-file model

  Tighten / loosen SDDP convergence:
    gtopt case.json --set sddp_options.max_iterations=200 \
                    --set sddp_options.convergence_tol=1e-5

  MIP-aware solve with a 1 % gap target and 10-minute wall-clock cap:
    gtopt case.json --mip-gap 0.01 --time-limit 600

  Quick LP-only smoke test on a MIP case (relaxes commitment binaries
  to continuous; gives a lower bound on the true MIP optimum):
    gtopt case.json --no-mip

  Enforce a naming dialect on input + output JSON (warns on
  cross-dialect aliases at parse time, renames canonical keys to the
  dialect's aliases at write time):
    gtopt case.json --naming-dialect plp

  Discover what aliases / units each dialect uses (no run, just
  prints the registry table — pipe to grep / awk to filter):
    gtopt --list-dialects
    gtopt --list-dialects plexos

  Dump the JSON Schema of the input options model (no run; pipe to a
  schema validator or save for editor autocompletion):
    gtopt --json-schema
    gtopt --json-schema options

Outputs
=======

By default gtopt writes its results, logs, and traces under the
configured output directory (CLI: --set output_directory=<dir>;
default: 'output' relative to the case file's parent).  The layout
is:

  <output_directory>/                 results: per-element parquet/csv
                    /solution.csv     scalar solve summary (status,
                                      objective, kappa, per-(scene,
                                      phase) breakdown)
                    /Generator/       generation_sol.parquet, etc.
                    /Bus/             balance_dual.parquet (LMPs), etc.
                    /Reservoir/       efin_sol, …
                    /cuts/            saved Benders cuts (SDDP only)
                    /logs/            per-run log files (see below)

  <output_directory>/logs/gtopt_N.log     INFO/WARN log of run #N
                        /trace_N.log      trace-level log of run #N
                        /error_*.lp[.zst] LP debug files when a
                                          solve goes infeasible

The log directory can be moved with --set log_directory=<dir> (or
options.log_directory in the planning JSON); an explicit value is
used as given (relative to the current directory).

The 'N' suffix is shared between gtopt_N.log and trace_N.log of the
same run (next free integer not present in the directory), so you
can pair the two by suffix when investigating an issue across runs.

When stdout is interactive (a TTY), spdlog also prints INFO+ to the
terminal in addition to writing the log file.  When stdout is
non-interactive (piped, redirected, or run via run_gtopt / CI),
stdout is suppressed entirely and the log file is the canonical
record — `--quiet` further silences the log file too.
)";
}

int print_available_solvers(std::ostream& out)
{
  auto& registry = SolverRegistry::instance();
  registry.load_all_plugins();
  const auto solvers = registry.available_solvers();
  if (solvers.empty()) {
    out << "No LP solver plugins found.\n\n";
    out << "Search directories:\n";
    for (const auto& dir : registry.searched_directories()) {
      out << "  " << dir << '\n';
    }
    const auto& errors = registry.load_errors();
    if (!errors.empty()) {
      out << "\nPlugin load errors:\n";
      for (const auto& err : errors) {
        out << "  " << err << '\n';
      }
    }
    out << "\nHints:\n"
        << "  - Set GTOPT_PLUGIN_DIR to the directory containing solver "
           "plugin libraries\n"
        << "  - Ensure solver plugins (osi and/or highs) are installed\n"
        << "  - Install COIN-OR (coinor-libcbc-dev) for CLP/CBC support\n"
        << "  - Install HiGHS for HiGHS support\n";
  } else {
    out << "Available LP solvers:\n";
    for (const auto& s : solvers) {
      out << "  " << s << '\n';
    }
  }
  return 0;
}

int run_check_solvers_command(std::string_view solver,
                              bool verbose,
                              std::ostream& out,
                              std::ostream& err)
{
  if (solver.empty()) {
    // Run tests against every available solver.
    return check_all_solvers(verbose);
  }
  // Run tests against the specified solver only.
  auto& registry = SolverRegistry::instance();
  registry.load_all_plugins();
  if (!registry.has_solver(solver)) {
    const auto avail = registry.available_solvers();
    err << "ERROR: solver '" << solver << "' not available.\n";
    if (!avail.empty()) {
      err << "Available:";
      for (const auto& s : avail) {
        err << ' ' << s;
      }
      err << '\n';
    }
    return 1;
  }
  const auto report = run_solver_tests(solver, verbose);
  for (const auto& r : report.results) {
    const char* mark = r.passed ? "✓" : "✗";
    out << std::format("  {} {:<30} {:.3f}s", mark, r.name, r.duration_s);
    if (!r.passed && !r.detail.empty()) {
      out << "\n    " << r.detail;
    }
    out << '\n';
  }
  out << std::format(
      "\n  {} passed, {} failed\n", report.n_passed(), report.n_failed());
  return report.passed() ? 0 : 1;
}

int print_naming_dialects(std::string_view filter,
                          std::ostream& out,
                          std::ostream& err)
{
  // Diagnostic dump of the naming + unit dictionaries.  Format is one
  // row per (canonical, dialect, alias, unit) tuple, tab-separated for
  // grep/awk friendliness.  The optional filter restricts the output to
  // a single dialect — useful when migrating from one tool to another.
  //
  // Class context is NOT printed: the global-alias index inside
  // NamesRegistry loses the per-alias class after build, and the
  // class-agnostic unit lookup is enough for the diagnostic use-case
  // (which is "what does PLEXOS call my pmax field?").
  const auto& names = NamesRegistry::instance();
  const auto& units = UnitRegistry::instance();

  // Single forward sweep through the per-alias index also gives us the
  // set of registered dialect names — used for an unknown-filter error
  // so `--list-dialects mistype` does not print zero rows silently.
  std::set<std::string, std::less<>> known_dialects;
  for (const auto& [canonical, aliases] : names.canonical_to_aliases()) {
    for (const auto& alias : aliases) {
      if (const auto d = names.dialect_for(alias); d.has_value()) {
        known_dialects.emplace(*d);
      }
    }
  }
  if (!filter.empty() && !known_dialects.contains(filter)) {
    err << std::format(
        "ERROR: --list-dialects: unknown dialect '{}'.  Known "
        "dialects: ",
        filter);
    bool first = true;
    for (const auto& d : known_dialects) {
      err << (first ? "" : ", ") << d;
      first = false;
    }
    err << '\n';
    return 1;
  }

  out << "# canonical\tdialect\talias\tunit\n";
  std::size_t printed = 0;
  for (const auto& [canonical, aliases] : names.canonical_to_aliases()) {
    for (const auto& alias : aliases) {
      const auto dialect = names.dialect_for(alias).value_or("");
      if (!filter.empty() && dialect != filter) {
        continue;
      }
      const auto unit =
          units.class_agnostic_unit_for(canonical, dialect).value_or("");
      out << std::format("{}\t{}\t{}\t{}\n", canonical, dialect, alias, unit);
      ++printed;
    }
  }
  if (filter.empty()) {
    out << std::format(
        "\n# {} rows ({} canonicals, {} dialects, names from {}, "
        "units from {})\n",
        printed,
        names.canonical_to_aliases().size(),
        known_dialects.size(),
        names.source_path().has_value() ? names.source_path()->string()
                                        : std::string("<built-in>"),
        units.source_path().has_value() ? units.source_path()->string()
                                        : std::string("<built-in>"));
  } else {
    out << std::format("\n# {} rows matching dialect '{}'\n", printed, filter);
  }
  return 0;
}

int print_json_schema(std::string_view name, std::ostream& out)
{
  // An empty name dumps the full Planning schema; a name selects a known
  // top-level type; an unknown name is reported on stderr (by
  // dump_json_schema, which then returns an empty string).
  const auto schema = dump_json_schema(name);
  if (schema.empty()) {
    return 1;
  }
  out << schema << '\n';
  return 0;
}

namespace
{

/// Apply the spdlog level/pattern implied by the CLI flags.
void configure_cli_logging(const cli::variables_map& vm,
                           const std::vector<std::string>& args,
                           bool quiet)
{
  spdlog::cfg::load_env_levels();

  // Use a clean pattern without source file/line information.
  spdlog::set_pattern("[%H:%M:%S.%e] %v");

  // Default: info.  --verbose: trace.  --quiet: off.
  if (quiet) {
    spdlog::set_level(spdlog::level::off);
  } else if (vm.contains("verbose")) {
    spdlog::set_level(spdlog::level::trace);
  } else {
    spdlog::set_level(spdlog::level::info);
  }

  // `SPDLOG_LEVEL=...` command-line tokens (spdlog's argv convention).
  std::vector<const char*> argv;
  argv.reserve(args.size() + 1);
  argv.push_back("gtopt");
  for (const auto& a : args) {
    argv.push_back(a.c_str());
  }
  spdlog::cfg::load_argv_levels(static_cast<int>(argv.size()), argv.data());
}

/// Parse, merge config defaults, set up logging and run gtopt_main().
[[nodiscard]] int run_cli(const std::vector<std::string>& args,
                          std::ostream& out,
                          std::ostream& err,
                          bool stdout_is_tty)
{
  auto desc = make_options_description();

  cli::positional_options_description pos_desc;
  pos_desc.add("system-file", -1);

  cli::variables_map vm;
  try {
    auto parser =
        cli::command_line_parser(args).options(desc).positional(pos_desc);
    cli::store(parser, vm);
    cli::notify(vm);
  } catch (cli::parse_error& e) {
    out << "ERROR: " << e.what() << "\n";
    out << desc << "\n";
    return 1;
  }

  if (vm.contains("verbose")) {
    spdlog::set_level(spdlog::level::trace);
  }

  if (vm.contains("help")) {
    print_cli_help(out, desc);
    return 0;
  }

  if (vm.contains("version")) {
    out << build_info().version_string() << '\n';
    return 0;
  }

  if (vm.contains("solvers")) {
    return print_available_solvers(out);
  }

  if (vm.contains("check-solvers")) {
    return run_check_solvers_command(
        get_opt<std::string>(vm, "check-solvers").value_or(""),
        vm.contains("verbose"),
        out,
        err);
  }

  if (vm.contains("list-dialects")) {
    return print_naming_dialects(
        get_opt<std::string>(vm, "list-dialects").value_or(""), out, err);
  }

  if (vm.contains("json-schema")) {
    return print_json_schema(
        get_opt<std::string>(vm, "json-schema").value_or(""), out);
  }

  // --solver validation is handled lazily: SolverRegistry::create()
  // will attempt to load only the requested plugin on demand.  If the
  // solver is unavailable, create() throws a clear error listing all
  // available solvers (after loading all pending plugins).

  std::vector<std::string> system_files;
  if (vm.contains("system-file")) {
    system_files = vm["system-file"].as<std::vector<std::string>>();
  }
  // When no files are given, gtopt_main() will try to auto-detect a
  // planning JSON in the current directory (convention:
  // dirname/dirname.json).

  // Build MainOptions from CLI, then attach the config-file defaults.  We
  // need these *before* setting up logging so the file-sink redirect
  // (when stdout is not a TTY) knows where the resolved output/log
  // directory lives.
  auto main_opts = parse_main_options(vm, std::move(system_files));
  // Config file: an explicit `--config FILE` (must exist), otherwise
  // $GTOPT_CONFIG / ~/.gtopt.conf.  The current directory is never
  // searched implicitly (see find_config_file).
  std::filesystem::path config_path;
  if (const auto explicit_config = get_opt<std::string>(vm, "config")) {
    config_path = *explicit_config;
    std::error_code ec;
    if (!std::filesystem::is_regular_file(config_path, ec)) {
      err << std::format("ERROR: --config file '{}' does not exist\n",
                         config_path.string());
      return 1;
    }
  } else {
    config_path = find_config_file();
  }
  // Config-file values sit BELOW the planning JSON and `--set` (see
  // MainOptions::config_defaults); only runtime-only keys are merged here.
  split_config_defaults(main_opts, load_gtopt_config_from(config_path));

  const auto quiet = get_opt<bool>(vm, "quiet").value_or(false);
  configure_cli_logging(vm, args, quiet);

  // Non-interactive stdout (run via run_gtopt, CI, redirection, …):
  // suppress every spdlog message on stdout and route them only to
  // <log_directory>/gtopt_N.log (default <output_directory>/logs, see
  // gtopt::resolve_log_directory).  The calling process gets a clean
  // stdout and a single canonical log file — no duplicate tee-ing
  // required on the Python side.
  //
  // We pre-resolve directory arguments here so the log file lands next
  // to the rest of the output (e.g. case_dir/results/logs/...);
  // gtopt_main() repeats resolve_planning_args() and is idempotent.
  if (!quiet && !stdout_is_tty) {
    if (auto resolved = resolve_planning_args(main_opts); resolved) {
      main_opts = std::move(*resolved);
    }
    setup_file_logging(main_opts, /*suppress_stdout=*/true);
  }

  spdlog::info("starting gtopt {}", build_info().version_string());
  if (config_path.empty()) {
    spdlog::info("config file: none loaded");
  } else {
    std::error_code ec;
    const auto shown = std::filesystem::absolute(config_path, ec);
    spdlog::info("config file: {}", (ec ? config_path : shown).string());
  }
  if (has_ignored_cwd_config(config_path)) {
    spdlog::warn(
        "ignoring ./.gtopt.conf in the current directory: it is no longer "
        "read implicitly; pass --config ./.gtopt.conf (or set "
        "$GTOPT_CONFIG) to use it");
  }

  auto result = gtopt_main(main_opts);
  if (result.has_value()) {
    flush_default_logger_best_effort();
    return *result;  // 0 = optimal, 1 = non-optimal
  }
  spdlog::critical(result.error());
  const int exit_code = classify_error_exit_code(result.error());
  flush_default_logger_best_effort();
  return exit_code;
}

}  // namespace

int gtopt_cli_main(const std::vector<std::string>& args,
                   std::ostream& out,
                   std::ostream& err,
                   bool stdout_is_tty)
{
  try {
    return run_cli(args, out, err, stdout_is_tty);
  } catch (const std::exception& ex) {
    try {
      spdlog::critical("Exception: {}", ex.what());
    } catch (...) {
      spdlog::critical(ex.what());
    }
    flush_default_logger_best_effort();
    return 3;  // internal error
  } catch (...) {
    spdlog::critical("Unknown exception");
    flush_default_logger_best_effort();
    return 3;  // internal error
  }
}

}  // namespace gtopt
