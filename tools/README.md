# tools/

This directory contains **agent and developer tools** that are NOT installed
as part of the `gtopt` user-facing distribution.

These scripts must **not** be added to `scripts/pyproject.toml` (as
`py-modules` or `packages.find.include`) and must **not** be added to any
`cmake install` target.

## Tools

### `setup_sandbox.sh`

Bootstrap a fresh Ubuntu 24.04 sandbox for building gtopt.  Installs all
required build dependencies in the same order as `.github/workflows/ubuntu.yml`:

1. ccache + base APT packages (COIN-OR, Boost, spdlog, LAPACK)
2. Arrow/Parquet — tries the Apache Arrow APT repository first; falls back to
   conda-forge if `packages.apache.org` is unreachable
3. Clang 21 from the LLVM APT repository (with retry logic)
4. Python scripts dev dependencies (pre-installed before cmake configure)
5. (Optional) cmake configure + build + test

**Quick start (from repo root):**

```bash
# Install all dependencies only (no build)
bash tools/setup_sandbox.sh

# Install deps + configure + build + test
bash tools/setup_sandbox.sh --build

# Skip Clang 21 install (use GCC 14 instead)
bash tools/setup_sandbox.sh --no-clang --build

# Release build
bash tools/setup_sandbox.sh --build --build-type Release

# Help
bash tools/setup_sandbox.sh --help
```

**Options:**

| Option | Description |
|--------|-------------|
| `--no-clang` | Skip Clang 21; use GCC 14 instead |
| `--no-python` | Skip Python scripts pre-install |
| `--configure` | Run cmake configure after deps |
| `--build` | Run cmake --build + ctest (implies `--configure`) |
| `--build-type T` | CMake build type: `Debug` (default) / `Release` / `RelWithDebInfo` |
| `--help` | Show usage and exit |

---

### `clang-tidy`

Transparent wrapper that forwards every invocation to `run-clang-tidy`
(the parallel driver), injecting `-j $(nproc)` when the caller did not
specify a jobs flag.  Shadows the system `clang-tidy` when placed first
on `PATH`, so developers and LLM agents who type `clang-tidy <file>`
out of habit automatically get the same parallel execution that CI and
the pre-commit hook use.

**Quick start (from repo root):**

```bash
# One-off symlink (auto-updates on git pull):
ln -s "$(pwd)/tools/clang-tidy" ~/.local/bin/clang-tidy

# Or prepend tools/ to PATH:
export PATH="$(pwd)/tools:$PATH"

# Verify the wrapper wins over /usr/bin/clang-tidy:
command -v clang-tidy     # must print the wrapper path
```

**Behaviour:**

| Caller invocation | Forwarded invocation |
|---|---|
| `clang-tidy foo.cpp` | `run-clang-tidy -j $(nproc) foo.cpp` |
| `clang-tidy -j 4 foo.cpp` | `run-clang-tidy -j 4 foo.cpp` (preserved) |
| `clang-tidy --jobs=8 foo.cpp` | `run-clang-tidy --jobs=8 foo.cpp` (preserved) |

**Bypass:** call the real binary by absolute path, e.g. `/usr/bin/clang-tidy`.

---

### `get_gtopt_binary.py`

Standalone helper for developer and CI sessions that need the compiled `gtopt`
binary without waiting for a full 5–10 minute build from source.

**Quick start (from repo root):**

```bash
# Print the path to a working gtopt binary (downloads CI artifact if needed)
export GTOPT_BIN=$(python tools/get_gtopt_binary.py)

# Also build from source as a last resort
python tools/get_gtopt_binary.py --build --verbose

# Force a fresh download even if a binary is already present
python tools/get_gtopt_binary.py --force-download
```

**Strategy (in order):**

1. `GTOPT_BIN` environment variable.
2. `gtopt` on `PATH`.
3. Standard build directories (`build/standalone/gtopt`, etc.).
4. `/tmp/gtopt-ci-bin/gtopt` – previously downloaded CI artifact.
5. Download `gtopt-binary-debug` CI artifact via `gh` CLI or `GITHUB_TOKEN`.
6. Build from source with `cmake` (requires build dependencies).

See the full module docstring for detailed usage and CI artifact download
step-by-step instructions.

---

### `affected_tests.py`

Selects the tests (and lint targets) affected by a change, so agents and
developers run an incremental pass instead of the full suite.  Pure
stdlib, no compiler needed (~1 s).

```bash
python tools/affected_tests.py                    # vs origin/master...HEAD
python tools/affected_tests.py --worktree -v      # + uncommitted; why each pick
python tools/affected_tests.py --files source/battery_lp.cpp
python tools/affected_tests.py --format json      # or shell / github / markdown
python tools/affected_tests.py --run -B build -n auto     # build + run subset
python tools/affected_tests.py --run --lint -B build      # + scoped lints
python tools/affected_tests.py --run --dry-run -B build   # print commands only
```

`--run` rebuilds `gtoptTests` (all targets when integration tests are
selected), raises `RLIMIT_CORE` (`ulimit -c unlimited`), lists the doctest
cases of the selected files with `gtoptTests --list-test-cases -sf=...`,
adds the selected integration entries and runs them all by exact name
through `ctest --tests-from-file` (parallel, ctest timeouts kept).  Test
cases CTest cannot address (doctest names containing `;`, which
`doctest_discover_tests` truncates at the `;` into a filter that matches
nothing, so plain `ctest` reports them passed without running them) are
run directly with `gtoptTests --test-case=...`.  Python
runs through pytest.  Exit status is non-zero when anything failed.
`--suites cpp,python,guiservice,tools` restricts what runs;
`--ctest-arg=...` forwards extra ctest options.

**Mapping rules** (each pick carries its reason; `-v` prints them):

| changed path | selected |
|---|---|
| `test/source/**/test_*.cpp` | that file |
| `include/**.hpp`, `test/source/*.hpp` | test files that include it transitively (`#include` graph of `include/` + `test/source/`), plus `test_<stem>*` name matches; a header no test includes follows the `source/` files that use it |
| `source/**.cpp` | tests of the same-stem header (`battery_lp.cpp` -> `battery_lp.hpp`) or the longest prefix header (`sddp_method_iteration.cpp` -> `sddp_method.hpp`), plus `test_<stem>*` / `test_<stem minus _lp>*` |
| `include/`, `source/` (any) | all C++ integration tests (e2e smoke) |
| `cases/<dir>/**` | the CTest entries registered for `<dir>` in `integration_test/CMakeLists.txt`, plus tests that reference the case (`"<dir>"` / `/<dir>/` in a file mentioning `cases`) |
| `scripts/<pkg>/tests/test_*.py` | that file |
| `scripts/<pkg>/**` | **direct**: `scripts/<pkg>/tests`; **dependents**: tests of every package importing `<pkg>` (static, transitive over library imports; test-only imports are leaves); `guiservice/tests` and `tools/test_*.py` when they import it; `scripts/tests/test_cli_help.py` when the file is a `[project.scripts]` entry point |
| `scripts/cases/<dir>`, `tools/test_data/<x>` | tests referencing `<dir>` / `<x>` |
| `guiservice/**` | `guiservice/tests` (only the changed test file when that is all that changed) |
| `tools/<x>.py` | `tools/test_<x>.py` and tools tests importing `<x>` |
| docs, `*.md`, `webservice/`, `guiservice-plus/`, other `.github/` | nothing (listed as "no tests needed") |

**Escalation to the full suite** (always printed, with the reason):

- C++ unit + integration: `CMakeLists.txt`, `all/`, `cmake/`,
  `cmake_local/`, `test/CMakeLists.txt`, `test/source/main.cpp`,
  `plugins/` (solver backends), unknown files under `test/`, a `source/`
  file no test maps to, and every *core* header: the hubs
  `linear_interface`, `linear_problem`, `planning_lp`, `system_lp`,
  `simulation_lp`, `system_context`, `basic_types`, `json_planning`,
  `json_system`, `json_options`, `gtopt_json_io`, `solver_backend`,
  `solver_registry`, `lp_matrix`, `flat_linear_problem`, plus any header
  included by more than `--core-threshold` (50 %) of the test files
  (today ~25 headers: `enum_option`, `uid`, `phase`, `block`, `utils`,
  ...).  The include walk does not descend into hubs, so a test that only
  sees `battery.hpp` through `system_lp.hpp` is not selected for it.
- C++ integration only: `integration_test/`, `standalone/`,
  `tools/validate_solution.py`, `tools/validate_sddp_output.py`,
  `tools/gtopt_compare_csv.py`, `tools/check_case_conditioning.py`.
- Python: `scripts/pyproject.toml`, `scripts/conftest.py`,
  `scripts/requirements*.txt`, `scripts/CMakeLists.txt`,
  `scripts/cmake/`, root `pyproject.toml`, `.pylintrc`; the dependents
  run as the full suite when the selection touches >= 75 %
  (`--py-full-threshold`) of the test dirs.
- Everything: `.github/workflows/{ubuntu,scripts,guiservice,tsan}.yml`,
  `.github/actions/`.

**pytest-testmon for the dependents.**  Package-level reverse
dependencies are coarse (the scripts import graph is dense: a
`gtopt_shared` change reaches 24 of 40 test dirs).  When
`pytest-testmon` is installed and `scripts/.testmondata` exists,
`--run` (`--py-engine auto`, the default) runs the *dependents* with
`--testmon --testmon-forceselect`, which keeps only the tests whose
recorded coverage touches a changed code block; the *direct* targets
always run in full (with `--testmon` to refresh the database).  Without a
database it falls back to the static selection.  Build one with
`cd scripts && pytest -n auto --testmon` (records only; ~1.5x a plain
run, ~4 MB).  testmon tracks Python only — data files (`scripts/cases`,
templates, JSON) are covered by the static rules above.

Measured on `scripts/` (`-n 8`): plain full run 36 s, full run
recording the database 57 s; after editing one function in
`gtopt_check_lp` testmon selects 62 tests (static: 16 dirs / 5646
tests), after editing `gtopt_shared.compressed_open` 205 tests (static:
24 dirs / 6019).  Works with pytest-xdist and pytest-cov (the database
stays correct); `-m` silently disables testmon's selection unless
`--testmon-forceselect` is given.

**CI.**  `.github/actions/affected-tests` runs the selector against the
PR base (`pull_request.base.sha`) or the previous push
(`github.event.before`) with `--two-dot` (shallow clones) and exposes the
selection as step outputs plus a job-summary table with the reasons.  It
falls back to the full selection on `workflow_dispatch` (input
`full_run`, default on), on schedules, on the first push of a branch, or
when the base commit cannot be fetched (force-push).  Workflows using it:
`ubuntu.yml` (doctest + integration subset through
`ctest --tests-from-file`; full when coverage is enabled), `scripts.yml` (direct
targets with coverage, dependents through testmon with the database
cached via `actions/cache` keyed on the commit, scoped pylint / mypy,
integration job limited to the selected converters) and `guiservice.yml`
(skipped when nothing reaches guiservice).  In affected runs the Python
coverage gate is enforced per package for the packages whose own test
dir ran (`--filter-coverage` + `tools/check_coverage_per_package.py
--total-min 0`), never on the total.

**Limits.**  The C++ mapping is static: a behaviour change in a `.cpp`
reached only through a hub (e.g. an element `_lp.cpp` exercised by a
planning test that includes only `planning_lp.hpp`) is caught by the
e2e smoke and the full runs, not by the unit subset.  Python
tests that run the `gtopt` binary are not selected by C++ changes.

