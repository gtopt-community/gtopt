cmake_minimum_required(VERSION 3.31.6)

# LANGUAGES NONE — defer the C++ compiler probe.  In external-binary mode
# (the common case) this sub-project compiles nothing, so requiring a working
# clang/gcc at configure time would be pure friction.  enable_language(CXX)
# is called below only if we fall through to the self-contained fallback.
project(gtoptIntegrationTest LANGUAGES NONE)

# ---- Shared cmake module path ----
list(APPEND CMAKE_MODULE_PATH
     "${CMAKE_CURRENT_LIST_DIR}/../cmake_local"
     "${CMAKE_CURRENT_LIST_DIR}/../cmake"
)

# ---- Build defaults (language-agnostic) ----
include(BuildTypeDefault)

# ---- Options ----
option(ENABLE_TEST_COVERAGE "Enable test coverage instrumentation" OFF)

# Absolute path to a prebuilt gtopt binary.  When non-empty, the gtopt
# library and standalone sources are NOT compiled in this build tree — the
# CTest cases invoke the external binary directly.  If left empty the
# resolver below tries $ENV{GTOPT_BIN} and the conventional
# ../build/standalone/gtopt location before falling back to a local build.
set(GTOPT_BINARY "" CACHE FILEPATH
    "Absolute path to a prebuilt gtopt binary (auto-detected if empty)")

# ---- Resolve the gtopt binary to drive the tests ----
# Precedence (first hit wins):
#   1. GTOPT_BINARY explicitly set via -D or cache.
#   2. Target 'gtoptStandalone' already defined (e.g. when included from
#      all/CMakeLists.txt alongside standalone/).
#   3. Auto-detection: $ENV{GTOPT_BIN}, then ../build/standalone/gtopt
#      relative to this directory.
#   4. Self-contained fallback: compile the standalone sources into this
#      build tree so the sub-project remains usable from a fresh checkout.
#
# GTOPT_EXECUTABLE_FILE is consumed by add_e2e_case()/add_sddp_case() and
# resolves to either a generator expression ($<TARGET_FILE:...>) or a
# literal absolute path, transparently substituted into add_test() commands.
set(_gtopt_mode "")

if(GTOPT_BINARY)
  if(NOT EXISTS "${GTOPT_BINARY}")
    message(FATAL_ERROR
      "GTOPT_BINARY is set but does not exist: ${GTOPT_BINARY}")
  endif()
  set(GTOPT_EXECUTABLE_FILE "${GTOPT_BINARY}")
  set(_gtopt_mode "explicit")
elseif(TARGET gtoptStandalone)
  set(GTOPT_EXECUTABLE_TARGET gtoptStandalone)
  set(GTOPT_EXECUTABLE_FILE "$<TARGET_FILE:${GTOPT_EXECUTABLE_TARGET}>")
  set(_gtopt_mode "in-tree target")
else()
  # Auto-detection.
  set(_gtopt_candidates "")
  if(DEFINED ENV{GTOPT_BIN})
    list(APPEND _gtopt_candidates "$ENV{GTOPT_BIN}")
  endif()
  list(APPEND _gtopt_candidates
       "${CMAKE_CURRENT_LIST_DIR}/../build/standalone/gtopt")

  foreach(_c IN LISTS _gtopt_candidates)
    if(EXISTS "${_c}" AND NOT IS_DIRECTORY "${_c}")
      get_filename_component(GTOPT_BINARY "${_c}" REALPATH)
      set(GTOPT_EXECUTABLE_FILE "${GTOPT_BINARY}")
      set(_gtopt_mode "auto-detected")
      break()
    endif()
  endforeach()
  unset(_gtopt_candidates)
endif()

if(_gtopt_mode STREQUAL "explicit")
  message(STATUS
    "Integration tests: using explicit GTOPT_BINARY=${GTOPT_BINARY}")
elseif(_gtopt_mode STREQUAL "in-tree target")
  message(STATUS
    "Integration tests: reusing in-tree gtoptStandalone target "
    "(no recompilation)")
elseif(_gtopt_mode STREQUAL "auto-detected")
  message(STATUS
    "Integration tests: auto-detected prebuilt gtopt at ${GTOPT_BINARY} "
    "(pass -DGTOPT_BINARY=/other/path to override, or remove the file to "
    "force a local build)")
else()
  # Self-contained fallback — compile everything here.
  message(STATUS
    "Integration tests: no prebuilt gtopt found — compiling the binary "
    "in this build tree")

  enable_language(CXX)
  include(CxxStandard)
  include(Ccache)
  include(FastLinker)
  include(CompilerCheck)
  include("${CMAKE_CURRENT_LIST_DIR}/../cmake/tools.cmake")

  option(CPM_USE_LOCAL_PACKAGES "Try find_package first" ON)
  include("${CMAKE_CURRENT_LIST_DIR}/../cmake/CPM.cmake")
  if(NOT TARGET gtopt::gtopt)
    CPMAddPackage(NAME gtopt SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/..")
  endif()

  include(ClangFormat)

  file(GLOB sources CONFIGURE_DEPENDS
       "${CMAKE_CURRENT_LIST_DIR}/../standalone/source/[a-zA-Z]*.cpp"
  )
  add_executable(${PROJECT_NAME} ${sources})
  set_target_properties(${PROJECT_NAME} PROPERTIES OUTPUT_NAME "gtopt")

  target_link_libraries(
    ${PROJECT_NAME}
    gtopt::gtopt
  )

  set(GTOPT_EXECUTABLE_TARGET ${PROJECT_NAME})
  set(GTOPT_EXECUTABLE_FILE "$<TARGET_FILE:${GTOPT_EXECUTABLE_TARGET}>")

  include(Coverage)
  if(ENABLE_TEST_COVERAGE)
    target_enable_coverage(${PROJECT_NAME})
    if(TARGET gtopt)
      target_enable_coverage(gtopt PRIVATE)
    endif()
  endif()
endif()

unset(_gtopt_mode)

# ---- Ensure solver plugins are rebuilt before running tests ----
# Only meaningful when we have an in-tree target to hang dependencies on.
# In external/auto-detected mode the plugins live next to GTOPT_BINARY and
# are managed by the build that produced it.
if(DEFINED GTOPT_EXECUTABLE_TARGET)
  include(GtoptPlugin)
  gtopt_require_solver_plugins(${GTOPT_EXECUTABLE_TARGET})
endif()

# ---- Testing ----
enable_testing()
include(TestParallelism)

set(CASES_DIR "${CMAKE_CURRENT_LIST_DIR}/../cases")
set(CMAKE_SCRIPTS_DIR "${CMAKE_CURRENT_LIST_DIR}/cmake")

# Locate the Python validator (`tools/validate_solution.py`) plus a
# Python interpreter to drive it.  Both are consumed by
# `add_e2e_case()` for the `e2e_<case>_validate_solution` test step.
set(VALIDATE_SOLUTION_PY
    "${CMAKE_CURRENT_LIST_DIR}/../tools/validate_solution.py")
if(NOT EXISTS "${VALIDATE_SOLUTION_PY}")
  message(FATAL_ERROR
    "tools/validate_solution.py not found at: ${VALIDATE_SOLUTION_PY}")
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter)

include(${CMAKE_SCRIPTS_DIR}/add_e2e_case.cmake)
include(${CMAKE_SCRIPTS_DIR}/add_sddp_case.cmake)
include(${CMAKE_SCRIPTS_DIR}/add_expected_values_case.cmake)

# ---- Pandapower detection ----
# Locate gtopt_compare (installed binary, source tree, or any Python with
# pandapower) for running pandapower comparison tests.
include(${CMAKE_SCRIPTS_DIR}/FindPandapower.cmake)
include(${CMAKE_SCRIPTS_DIR}/add_pandapower_comparison.cmake)

# ---- Options ----
option(GTOPT_SLOW_TESTS "Register slow integration tests (case_2y)" OFF)

# ---- Committed cases still build with the current gtopt ----
# One `gtopt --lp-only` test per planning JSON under cases/ and
# scripts/cases/ (label `cases`), so a case left in a stale schema fails
# CI instead of rotting.  Regenerate a failing case with its converter
# (see the case README / <tool>_state.json) rather than hand-editing it.
include(${CMAKE_SCRIPTS_DIR}/add_case_lp_only.cmake)
add_case_lp_only_tests(case_lp_only "${CASES_DIR}")
add_case_lp_only_tests(script_case_lp_only
  "${CMAKE_CURRENT_LIST_DIR}/../scripts/cases")

# Large study cases (gtopt_case_2y*, cen_long_term_1y, the CEN PLP/PCP
# sources, ...) are not shipped with gtopt; they live in the case
# repository gtopt-cases (<country>/<application>/<case>/).  Point
# GTOPT_CASES_DIR (CMake cache variable, or environment variable at
# configure time) at a checkout of it -- tools/get_cases.sh clones it at
# the commit pinned in tools/cases.lock.  Tests whose case is missing are
# not registered.
set(GTOPT_CASES_DIR "$ENV{GTOPT_CASES_DIR}" CACHE PATH
    "Checkout of the gtopt-cases case repository; empty = skip")

# gtopt_find_external_case(<out_var> <relpath>)
#   Sets <out_var> to ${GTOPT_CASES_DIR}/<relpath> when it exists.  For
#   checkouts where GTOPT_CASES_DIR points at an application folder
#   (e.g. <repo>/chile/medium_long_term) the last path component is also
#   tried directly below it.  Empty when not found.
function(gtopt_find_external_case out_var relpath)
  set(_found "")
  if(GTOPT_CASES_DIR)
    get_filename_component(_leaf "${relpath}" NAME)
    foreach(_c "${GTOPT_CASES_DIR}/${relpath}" "${GTOPT_CASES_DIR}/${_leaf}")
      if(IS_DIRECTORY "${_c}")
        set(_found "${_c}")
        break()
      endif()
    endforeach()
  endif()
  set(${out_var} "${_found}" PARENT_SCOPE)
endfunction()

# ---- CLI: --version ----
# `gtopt --version` prints the version, then the git revision it was built
# from unless the build is exactly the release tag: "1.1.0" or
# "1.2.0-dev (v1.1.0-23-g0123456789ab)".  The formatting rules themselves
# are unit-tested in test/source/test_build_info.cpp.
add_test(NAME cli_version COMMAND ${GTOPT_EXECUTABLE_FILE} --version)
set_tests_properties(
  cli_version
  PROPERTIES
    PASS_REGULAR_EXPRESSION
    "^[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.]+)?( \\([^)]+\\))?\n$"
    FAIL_REGULAR_EXPRESSION "unknown"
)

# ---- CLI front end: info commands, exit codes, output formats ----
# Each test runs the real binary through integration_test/cmake/
# run_cli_case.cmake in a fresh work directory (cwd) and checks the exit
# code, stdout/stderr and the files left behind.  The in-process
# counterpart (same code paths, captured streams) is
# test/source/test_gtopt_cli.cpp.
set(RUN_CLI_CASE "${CMAKE_SCRIPTS_DIR}/run_cli_case.cmake")

function(add_cli_test name)
  cmake_parse_arguments(A "" "EXIT;STDOUT;STDERR;NOT_STDOUT;INPUT_NAME;INPUT_CONTENT"
                        "ARGS;FILES" ${ARGN})
  if(NOT DEFINED A_EXIT)
    set(A_EXIT 0)
  endif()
  list(JOIN A_ARGS "|" _args)
  list(JOIN A_FILES "|" _files)
  set(_defs
    "-DGTOPT_BINARY=${GTOPT_EXECUTABLE_FILE}"
    "-DWORK_DIR=${CMAKE_CURRENT_BINARY_DIR}/cli_output/${name}"
    "-DCLI_ARGS=${_args}"
    "-DEXPECT_EXIT=${A_EXIT}")
  if(_files)
    list(APPEND _defs "-DEXPECT_FILES=${_files}")
  endif()
  foreach(_kv STDOUT:STDOUT_REGEX STDERR:STDERR_REGEX
              NOT_STDOUT:NOT_STDOUT_REGEX INPUT_NAME:INPUT_NAME
              INPUT_CONTENT:INPUT_CONTENT)
    string(REPLACE ":" ";" _kv "${_kv}")
    list(GET _kv 0 _k)
    list(GET _kv 1 _var)
    if(DEFINED A_${_k})
      list(APPEND _defs "-D${_var}=${A_${_k}}")
    endif()
  endforeach()
  add_test(NAME cli_${name}
           COMMAND ${CMAKE_COMMAND} ${_defs} -P "${RUN_CLI_CASE}")
  set_tests_properties(cli_${name} PROPERTIES LABELS "cli;e2e" TIMEOUT 300)
endfunction()

set(_ieee4 "${CASES_DIR}/ieee_4b_ori/ieee_4b_ori.json")

# Informational sub-commands (no planning file).
add_cli_test(help ARGS --help STDOUT "--lp-only.*Examples.*Outputs")
add_cli_test(unknown_flag ARGS --definitely-not-a-flag EXIT 1
             STDOUT "^ERROR: ")
add_cli_test(bad_typed_value ARGS --threads abc EXIT 1
             STDOUT "^ERROR: invalid value 'abc' for option '--threads'")
add_cli_test(list_dialects ARGS --list-dialects
             STDOUT "^# canonical.*# [0-9]+ rows [(]")
add_cli_test(list_dialects_unknown ARGS --list-dialects no_such_dialect
             EXIT 1 STDERR "unknown dialect 'no_such_dialect'")
add_cli_test(json_schema ARGS --json-schema STDOUT "\"properties\"")
add_cli_test(json_schema_unknown ARGS --json-schema no_such_schema EXIT 1)
add_cli_test(solvers ARGS --solvers STDOUT "Available LP solvers:")
add_cli_test(check_solvers_unknown ARGS --check-solvers no_such_solver
             EXIT 1 STDERR "solver 'no_such_solver' not available")

# Input errors map to exit code 2.
add_cli_test(missing_file ARGS does_not_exist.json --quiet EXIT 2)
add_cli_test(invalid_json ARGS broken.json --quiet EXIT 2
             INPUT_NAME broken.json INPUT_CONTENT "{ \"system\": { \"bus_array\": ")

# Real runs on ieee_4b_ori.
add_cli_test(ieee4_lp_only
             ARGS "${_ieee4}" --lp-only --quiet --output-directory out
             FILES out/gtopt_state.json out/README.md)
add_cli_test(ieee4_csv
             ARGS "${_ieee4}" --quiet --output-directory out
                  --output-format csv --output-compression uncompressed
                  --json-file merged.json
             FILES out/solution.csv out/Generator/generation_sol*.csv
                   out/Bus/balance_dual*.csv merged.json)
add_cli_test(ieee4_parquet_stats
             ARGS "${_ieee4}" --quiet --output-directory out
                  --output-format parquet --stats
             FILES out/solution.csv out/Generator/generation_sol*.parquet)
add_cli_test(ieee4_single_bus
             ARGS "${_ieee4}" --quiet --output-directory out
                  --set model_options.use_single_bus=true
                  --output-format csv
             FILES out/solution.csv)
# No --quiet and stdout is a pipe: logs are redirected to
# <output>/logs/gtopt_N.log (the non-interactive branch of the CLI).
add_cli_test(ieee4_log_file
             ARGS "${_ieee4}" --output-directory out
                  --trace-log "@WORK_DIR@/trace.log"
             NOT_STDOUT "starting gtopt"
             FILES out/logs/gtopt_*.log trace*.log out/solution.csv)

# ---- Register e2e test cases ----
# To add a new case, place it under cases/<name>/ with a system JSON file and
# an output/ directory containing the expected CSV results, then add a line:
#   add_e2e_case(<name> <system_json>)
# To also compare against pandapower, add:
#   add_pandapower_comparison(<name>)

add_e2e_case(c0 system_c0.json)
add_e2e_case(c1 system_c1.json)
add_e2e_case(ieee_9b ieee_9b.json)
add_e2e_case(ieee_14b ieee_14b.json)
add_e2e_case(ieee_9b_ori ieee_9b_ori.json)
add_e2e_case(ieee_14b_ori ieee_14b_ori.json)
add_e2e_case(ieee_4b_ori ieee_4b_ori.json)
add_e2e_case(s1b s1b.json)
add_e2e_case(s1b_fail_segments s1b_fail_segments.json)
add_e2e_case(bat_4b bat_4b.json)
add_e2e_case(ieee30b ieee30b.json)
add_e2e_case(ieee_57b ieee_57b.json)
add_e2e_case(bat_4b_24 bat_4b_24.json)
add_e2e_case(exp_gen_4b exp_gen_4b.json)
add_e2e_case(exp_bat_4b exp_bat_4b.json)
add_e2e_case(exp_bat_4b_24 exp_bat_4b_24.json)

# Hydro / thermal benchmarks derived from SDDP.jl reference examples
# (MIT-licensed, https://github.com/odow/SDDP.jl).  These cover the
# Reservoir / Waterway / Turbine / Junction cascade that the IEEE
# cases above do not exercise (those are network/dispatch-only).  The
# `test_hydro_thermal_benchmark.cpp` unit tests cover the same
# fixtures via in-code and inline-JSON construction; the e2e cases
# here cover the disk-loaded JSON + golden-CSV comparison path.
add_e2e_case(hydro_valley_sddpjl system_hydro_valley_sddpjl.json)
add_e2e_case(fast_hydro_thermal system_fast_hydro_thermal.json)
add_e2e_case(hydro_thermal_sddpjl system_hydro_thermal_sddpjl.json)

# Reservoir water value under spill (cases/reservoir_water_value/README.md):
# a full, spilling reservoir has water value 0 with a free exit and
# -cost/fcr when every exit is priced (spillway_cost, Junction.drain_cost,
# Waterway.fcost) or when stored volume is priced (ecost).  Values are
# derived by hand in expected.json and asserted with an explicit
# tolerance; the *_sddp variants check that SDDP (3 phases) reproduces the
# monolithic objective and the negative water values through its cuts.
foreach(_rwv spill_free spill_cost spill_cost_free_exit drain_cost
             waterway_fcost ecost spill_cost_sddp ecost_sddp)
  add_expected_values_case(rwv_${_rwv} ${_rwv}.json
    CASE_DIR reservoir_water_value LABELS "hydro")
endforeach()

# PSR SDDP multi-system fixtures, converted to gtopt JSON by
# `scripts/sddp2gtopt/` (multi-system support landed 2026-05-16).
# Each case dir holds the converter's output as a vendored
# snapshot; if the converter changes shape, re-run and update.
add_e2e_case(psri_case2_via_sddp2gtopt system_psri_case2_via_sddp2gtopt.json)
add_e2e_case(psri_case3_via_sddp2gtopt system_psri_case3_via_sddp2gtopt.json)

# IEEE 24-bus Reliability Test System converted from pandapower's
# `case24_ieee_rts` via `pp2gtopt`.  Exercises gtopt's demand-fail
# accounting path: pp's case ships under-sized generation (1509 MW
# capacity vs 2850 MW load), so gtopt's LP routes the 1341 MW
# shortfall through `Demand.fail` at `demand_fail_cost = 1000`.  The
# analytical `obj_value = 1 368 194.16` is the sum of dispatched
# generation cost (27 194.16) and demand-fail penalty (1 341 000).
# See cases/ieee_24b_rts/README.md for the full breakdown.
add_e2e_case(ieee_24b_rts ieee_24b_rts.json)

# Polish 3375-bus transmission grid (MATPOWER `case3375wp`, BSD).
# Largest network in the e2e suite — solves a ~3375-bus DC OPF.  Acts as a publicly-available substitute for
# the Chinese provincial 3266-bus dataset (IEEE DataPort, paywalled
# — see cases/cn_3266b/README.md).  No exact `obj_value` golden:
# heterogeneous linear costs + KVL constraints make the optimum
# only computable by solving the LP, so this case is registered
# with an empty `output/` (solve + validate_solution only, no
# compare_solution).  See cases/case3375wp/README.md.
add_e2e_case(case3375wp case3375wp.json)

# ---- SDDP test cases ----
# To add a new SDDP case, place it under cases/<name>/ with a system JSON file
# that has method="sddp", then add a line:
#   add_sddp_case(<name> <system_json>
#                 [MAX_ITERATIONS <n>]
#                 [LOW_MEMORY <off|compress|rebuild>])
#
# The small sddp_hydro_3phase fixture (1 reservoir, 2 generators, 3 phases)
# is the regression harness for SDDP — every listed case solves with the
# external gtopt binary in parquet output mode and is then validated by
# `tools/validate_sddp_output.py`, which reads the hive-partitioned
# parquet shards and asserts tolerance-bounded physical invariants
# (reservoir efin bounds, generation ≥ 0, demand shortage ≥ 0, optimal
# cells have finite obj_value).
#
# Grid: {MAX_ITERATIONS ∈ {0, 3, 50}} × {LOW_MEMORY ∈ {off, compress, rebuild}}
# — 0 exercises pure sim-pass from loaded cuts, 3 is a quick training
# sanity check, 50 lets SDDP converge so the final UB ≈ LB.  Each
# low_memory variant exercises a different backend-lifecycle path
# (off keeps backends live, compress release + replay from snapshot,
# rebuild re-flattens from collections).

# Per-iteration goldens live next to the case:
#
#   cases/sddp_hydro_3phase/golden_iter0.json   — pure sim pass, no training;
#     hydro runs flat out, reservoir drained; objective_value = 104 700.
#   cases/sddp_hydro_3phase/golden_iter3.json   — this small fixture is
#     easy enough that SDDP converges within 3 iterations, so iter=3
#     goldens match the iter=50 converged state (323 100).
#   cases/sddp_hydro_3phase/golden_iter50.json  — fully converged
#     reference, UB=LB=323 100.
#
# Every case (iter=0 / iter=3 / iter=50, off / compress / rebuild) pins
# its objective_value, per-phase obj_values, and 5 element samples
# (Reservoir/efin_sol, Generator/generation_sol, Bus/balance_dual) so
# a numerical drift in any low_memory mode surfaces immediately.

# max_iter=0 (pure sim pass) across all three low-memory modes.
add_sddp_case(sddp_hydro_3phase_iter0_off sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 0 LOW_MEMORY off
  ALLOWED_EXIT_CODES "0;1"
  GOLDEN golden_iter0.json)
add_sddp_case(sddp_hydro_3phase_iter0_compress sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 0 LOW_MEMORY compress
  ALLOWED_EXIT_CODES "0;1"
  GOLDEN golden_iter0.json)
add_sddp_case(sddp_hydro_3phase_iter0_rebuild sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 0 LOW_MEMORY rebuild
  ALLOWED_EXIT_CODES "0;1"
  GOLDEN golden_iter0.json)

# Quick training sanity check (iter=3) — off-mode only; iter=50 covers
# compress + rebuild below.
add_sddp_case(sddp_hydro_3phase_iter3 sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 3
  GOLDEN golden_iter3.json)

# Converged runs (iter=50) across all three low-memory modes.
add_sddp_case(sddp_hydro_3phase_iter50_off sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 50 LOW_MEMORY off
  GOLDEN golden_iter50.json)
add_sddp_case(sddp_hydro_3phase_iter50_compress sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 50 LOW_MEMORY compress
  GOLDEN golden_iter50.json)
add_sddp_case(sddp_hydro_3phase_iter50_rebuild sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 50 LOW_MEMORY rebuild
  GOLDEN golden_iter50.json)

# ---- Cut hot-start round-trip ----
# End-to-end guard for the cut-I/O + α-free pipeline:
#
#   save:  run SDDP iter=50 to convergence.  The default SDDP options
#          populate `cuts_output_file = <output_dir>/cuts/sddp_cuts.csv`
#          and save the converged cut set there at run end (see
#          `source/planning_method.cpp` + `source/sddp_iteration.cpp`).
#          No extra `--set` needed on the save side.
#   load:  run SDDP iter=0 (pure sim pass, no training) on the same
#          system, with `sddp_options.cuts_input_file=<save-side csv>`
#          and `sddp_options.recovery_mode=cuts`.  The loader must
#            (a) consume α pre-registered by
#                `register_alpha_variables` on every phase,
#            (b) call `free_alpha(…)` per installed cut to release the
#                `lowb = uppb = 0` bootstrap pin, and
#            (c) install cuts with a unique `IterationContext` per row
#                so the metadata-based dup detector in
#                `LinearInterface::add_row` doesn't fire.
#   validate: the sim pass must reproduce the converged objective
#             (UB = 323 100) — golden_iter50.json is shared with the
#             direct iter=50 run above.
#
# The load test CTest-DEPENDS on the save test so the shared Parquet
# cuts file exists before the load step runs.
set(_hotstart_cuts
    "${CMAKE_CURRENT_BINARY_DIR}/test_output/sddp_hydro_3phase_hotstart_save_sddp/cuts/sddp_cuts.parquet")

add_sddp_case(sddp_hydro_3phase_hotstart_save sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 50
  GOLDEN golden_iter50.json)

add_sddp_case(sddp_hydro_3phase_hotstart_load sddp_hydro_3phase.json
  CASE_DIR sddp_hydro_3phase MAX_ITERATIONS 0
  RECOVER
  EXTRA_SET
    "sddp_options.cuts_input_file=${_hotstart_cuts}"
    "sddp_options.recovery_mode=cuts"
  GOLDEN golden_iter50_hotstart.json)

# The load run reads the Parquet cuts file produced by the save run — enforce
# ordering via CTest dependency on the save-side validate step
# (which in turn depends on save-side solve).  Without this, a
# parallel ctest run could launch both tests concurrently and the
# load step would see an empty/missing cuts file.
set_tests_properties(e2e_sddp_hydro_3phase_hotstart_load_sddp_solve
  PROPERTIES DEPENDS e2e_sddp_hydro_3phase_hotstart_save_sddp_validate)

if(GTOPT_SLOW_TESTS)
  # The 2-year SEN cases come from the external case repository.
  gtopt_find_external_case(_case_2y "chile/medium_long_term/gtopt_case_2y")
  if(_case_2y)
    add_sddp_case(gtopt_case_2y gtopt_case_2y.json
      CASE_DIR "${_case_2y}"
      MAX_ITERATIONS 1 TIMEOUT 600 LABELS slow)
  else()
    message(STATUS "GTOPT_SLOW_TESTS: gtopt_case_2y not found under "
                   "GTOPT_CASES_DIR='${GTOPT_CASES_DIR}' -- not registered")
  endif()
  gtopt_find_external_case(_case_2y_fs
    "chile/medium_long_term/gtopt_case_2y_fs")
  if(_case_2y_fs)
    add_sddp_case(gtopt_case_2y_fs gtopt_case_2y_fs.json
      CASE_DIR "${_case_2y_fs}"
      MAX_ITERATIONS 0 ALLOWED_EXIT_CODES "0;1" LABELS slow)
  else()
    message(STATUS "GTOPT_SLOW_TESTS: gtopt_case_2y_fs not found under "
                   "GTOPT_CASES_DIR='${GTOPT_CASES_DIR}' -- not registered")
  endif()
endif()

# ---- LP conditioning (kappa) on a real, badly conditioned case ----
# Off by default: each variant builds and solves a ~42k x 57k LP of the
# CEN 1-year case.  The case is not committed (it lives in the case
# repository at chile/medium_long_term/cen_long_term_1y), so set
# GTOPT_CASES_DIR or point GTOPT_NUMERICS_CASES_DIR at a directory holding
# cen_long_term_1y/; the tests skip (exit 77) when it or the CPLEX/HiGHS
# plugin is missing.  Run with
#   cmake ... -DGTOPT_BUILD_INTEGRATION_TESTS=ON -DGTOPT_NUMERICS_TESTS=ON
#   ctest -L numerics
option(GTOPT_NUMERICS_TESTS
       "Register heavy LP-conditioning (kappa) tests on large cases" OFF)
# Default: the medium/long-term folder of the external case repository
# (GTOPT_CASES_DIR) when it is set, else the in-tree cases/ directory.
set(_numerics_default "${CASES_DIR}")
if(GTOPT_CASES_DIR)
  gtopt_find_external_case(_cen1y "chile/medium_long_term/cen_long_term_1y")
  if(_cen1y)
    get_filename_component(_numerics_default "${_cen1y}" DIRECTORY)
  endif()
endif()
set(GTOPT_NUMERICS_CASES_DIR "${_numerics_default}" CACHE PATH
    "Directory holding the large cases used by the numerics tests")

if(GTOPT_NUMERICS_TESTS)
  set(CHECK_CONDITIONING_PY
      "${CMAKE_CURRENT_LIST_DIR}/../tools/check_case_conditioning.py")
  include(${CMAKE_SCRIPTS_DIR}/add_conditioning_case.cmake)

  # cen_long_term_1y, monolithic, tangent_signed_flow losses + Kirchhoff,
  # 339 buses.  Raw matrix range 10.8 decades (loss rows up to 1e5, theta
  # rows down to 1.6e-6), 5.9 decades after the case's ruiz equilibration.
  # Only phase 1 (April, 10 blocks) is solved (PHASES 1 deactivates the
  # other phases and their stages).  It matches phase 1 of the 12-phase
  # run (kappa within 1e-6, objective within 3e-8), and it is the only
  # phase whose objective is solver-independent: later phases inherit the
  # end state of a degenerate earlier optimum (HiGHS and CPLEX differ by
  # 5e-3 in phase 3 of the full run), so (d) would not hold for HiGHS on
  # the full horizon.
  #
  # Measured kappa (this test, phase 1 | max over the 12-phase run):
  #   cplex_case        case options               9.9e7  | 1.68e9
  #   highs_case        case options               2.9e8  | 1.63e9
  #   highs_eqnone      equilibration_method=none  3.2e16 | 9.8e16
  #   cplex_aggressive  solver scaling=aggressive  2.2e5  | 4.7e6
  # kappa(highs_eqnone) / kappa(highs_case) = 1.1e8.
  # Objective: 1.5540931657e8 in every variant (rel. diff <= 3e-14).
  set(_cen1y_common
      CASE_DIR "${GTOPT_NUMERICS_CASES_DIR}/cen_long_term_1y"
      CASE_JSON cen_long_term_1y.json
      PHASES 1
      LABELS "numerics;heavy")
  set(_cen1y_set method=monolithic solver_options.threads=4)

  add_conditioning_variant(numerics_cen_1y cplex_case ${_cen1y_common}
    SOLVER cplex SET ${_cen1y_set})
  add_conditioning_variant(numerics_cen_1y highs_case ${_cen1y_common}
    SOLVER highs SET ${_cen1y_set})
  add_conditioning_variant(numerics_cen_1y highs_eqnone ${_cen1y_common}
    SOLVER highs
    SET ${_cen1y_set} lp_matrix_options.equilibration_method=none)
  add_conditioning_variant(numerics_cen_1y cplex_aggressive ${_cen1y_common}
    SOLVER cplex SET ${_cen1y_set} solver_options.scaling=aggressive)

  add_conditioning_check(numerics_cen_1y
    VARIANTS cplex_case highs_case highs_eqnone cplex_aggressive
    LABELS "numerics;heavy"
    ARGS
      # (a) badly conditioned with the case's own options (9.9e7 measured)
      --kappa-above cplex_case:1e7
      # (b) dropping gtopt equilibration costs >= 1e2 in kappa (1.1e8 measured)
      --kappa-ratio-at-least highs_eqnone:highs_case:100
      # (c) CPLEX aggressive scaling brings it under 1e7 (2.2e5 measured)
      --kappa-below cplex_aggressive:1e7
      # (d) same optimum in every variant
      --objective-reference cplex_case
      --objective-reltol 1e-5)
endif()

# ---- Pandapower comparison tests ----
add_pandapower_comparison(s1b)
add_pandapower_comparison(ieee_4b_ori)
add_pandapower_comparison(ieee30b)
add_pandapower_comparison(ieee_57b)
add_pandapower_comparison(bat_4b_24)

# ---- Target: generate pandapower network JSON files ----
# Run 'cmake --build . --target generate-pandapower-nets' to save a
# pandapower_net.json file into each case directory.  Requires gtopt_compare
# and pandapower to be installed or available in the source tree.
if(COMPARE_PANDAPOWER_PROGRAM OR (COMPARE_PANDAPOWER_PYTHON AND COMPARE_PANDAPOWER_SCRIPTS_DIR))
  foreach(_pp_case s1b ieee_4b_ori ieee30b ieee_57b)
    if(COMPARE_PANDAPOWER_PROGRAM)
      list(APPEND _generate_pandapower_commands
        COMMAND "${COMPARE_PANDAPOWER_PROGRAM}"
          --case "${_pp_case}"
          --save-pandapower-file "${CASES_DIR}/${_pp_case}/pandapower_net.json"
      )
    else()
      list(APPEND _generate_pandapower_commands
        COMMAND ${CMAKE_COMMAND} -E env
          "PYTHONPATH=${COMPARE_PANDAPOWER_SCRIPTS_DIR}"
          "${COMPARE_PANDAPOWER_PYTHON}" -m gtopt_compare
            --case "${_pp_case}"
            --save-pandapower-file "${CASES_DIR}/${_pp_case}/pandapower_net.json"
      )
    endif()
  endforeach()

  add_custom_target(generate-pandapower-nets
    ${_generate_pandapower_commands}
    COMMENT "Generating pandapower network JSON files in cases/ directories"
    VERBATIM
  )
endif()
