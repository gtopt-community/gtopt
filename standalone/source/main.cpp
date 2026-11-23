#include <iostream>
#include <span>
#include <string>
#include <vector>

#include <gtopt/gtopt_cli.hpp>
#include <gtopt/gtopt_main.hpp>
#include <unistd.h>

// ---- jemalloc runtime configuration ----
// When jemalloc is linked (GTOPT_HAVE_JEMALLOC, set by CMake), bake a good
// default into the binary via jemalloc's `malloc_conf` symbol.
// `background_thread:true` runs a dedicated purge thread so freed pages are
// returned to the OS OFF the allocating hot path.  Measured on the 2-year
// Maule/Laja SDDP parity case (CPLEX, one iteration): peak RSS 35.6 -> 25.5 GB
// (-28%) and wall 19:47 -> 16:43 (-15%).  Without it, jemalloc's stock default
// (background_thread:false, dirty_decay_ms:10000) purges inline and bloats RSS
// to glibc-like levels — i.e. merely *linking* jemalloc buys little; the
// background thread is what delivers the win.  Reducing narenas / percpu_arena
// / shorter decay added nothing (or hurt wall) in the sweep, so we ship only
// this one knob.
//
// The `MALLOC_CONF` environment variable still overrides this (higher priority
// than the symbol), so operators can tune further at runtime.
// extern "C" gives the exact unmangled name jemalloc looks up in the dynamic
// symbol table; [[gnu::used, gnu::retain]] keeps it under --gc-sections.
#ifdef GTOPT_HAVE_JEMALLOC
extern "C"
{
extern const char* malloc_conf;
[[gnu::used, gnu::retain]] const char* malloc_conf = "background_thread:true";
}
#endif

int main(int argc, char** argv)
{
  // Install the async default-logger wrapper at the very first
  // opportunity — BEFORE any `spdlog::set_level` / `spdlog::set_pattern` /
  // env-level load.  Otherwise the short window between `main()` entry
  // and `gtopt_main()` installing the wrapper runs through the
  // synchronous default logger, which serialises every log call on the
  // sink mutex.  If a background monitor thread (CPU sampler, signal
  // handler) fires during that window it would contend with the main
  // thread's setup-time `spdlog::info(...)` calls.
  //
  // Idempotent: a second call from inside `gtopt_main` is a no-op.
  //
  // Note: this unconditionally installs async even when the user passes
  // `--no-async-logger` / `--trace-log`, because CLI parsing has not
  // happened yet.  `gtopt_main` will switch the default logger back to
  // sync once the options are parsed.
  gtopt::install_async_default_logger();

  // Everything else (argument parsing, info sub-commands, logging set-up,
  // exit-code mapping) lives in the library so it can be unit tested;
  // see gtopt/gtopt_cli.hpp.
  const std::span all_args {argv, static_cast<std::size_t>(argc)};
  std::vector<std::string> args;
  if (!all_args.empty()) {
    args.assign(all_args.begin() + 1, all_args.end());
  }
  return gtopt::gtopt_cli_main(
      args, std::cout, std::cerr, ::isatty(STDOUT_FILENO) != 0);
}
