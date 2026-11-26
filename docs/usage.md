# GTOpt Usage Guide

Detailed usage instructions, examples, and reference for the GTOpt solver.

> **Note**: This guide assumes GTOpt is already installed. If you need build
> instructions, see [Building Guide](../BUILDING.md). For detailed input data
> structure specifications, see [Input Data Reference](input-data.md).

## Table of Contents

- [Basic Usage](#basic-usage)
- [Command-Line Reference](#command-line-reference)
- [Configuration File (`.gtopt.conf`)](#configuration-file-gtoptconf)
- [System Configuration File](#system-configuration-file)
- [Input Data Directory](#input-data-directory)
- [Running the Sample Case](#running-the-sample-case)
- [Output Files](#output-files)
- [Advanced Usage](#advanced-usage)
- [Troubleshooting Solver Issues](#troubleshooting-solver-issues)
  - [Known solver issues](#known-solver-issues)
- [Batch Execution](#batch-execution)

## Basic Usage

The simplest way to run GTOpt is to pass a system JSON file:

```bash
gtopt system_config.json
```

The system file defines the power system (generators, demands, buses, lines),
the simulation parameters (stages, blocks, scenarios), and solver options. By
default, input data is read from the directory specified inside the JSON and
output is written to `output/` next to the JSON file (see
[Where the output goes](#where-the-output-goes)).

### Where the output goes

One rule covers every way of choosing the output directory:

| Given by | Relative to |
| -------- | ----------- |
| the command line: `--set output_directory=DIR` (or the deprecated `-d DIR`) | the current directory |
| `options.output_directory` in the planning JSON | the JSON file's directory |
| nothing (default) | `output/` next to the JSON file; `results/` inside the case directory when gtopt is given a directory (`gtopt case/`) |

The command line wins over the JSON.  `--set output_directory=.` and
`-d .` both write into the current directory, and `gtopt case/` and
`gtopt case/case.json` both honour the case's `options.output_directory`.
`--set input_directory=DIR` follows the same rule (CWD-relative), while
a JSON `input_directory` is relative to the JSON file.

The case argument may take several forms:

| Argument | Planning file used |
|----------|--------------------|
| `case.json` | That file |
| `case` (no such file) | `case.json` |
| `case_dir/` | `case_dir/case_dir.json`; when that file is missing, a sibling `case_dir.json` |
| `.` or `..` | The JSON named after that directory: `gtopt .` inside `ieee_9b/` uses `./ieee_9b.json` |
| *(none)* | `<name of the current directory>.json` in the current directory |

With a directory argument, the input directory is the case directory,
results go to `<case_dir>/results` and SDDP cuts to `<case_dir>/cuts`,
unless `input_directory`, `output_directory` or
`sddp_options.cut_directory` is given with `--set`.

### Common patterns

```bash
# Run with a specific output directory
gtopt system_c0.json --set output_directory=results/

# Run with a separate input directory
gtopt --system-file config.json --set input_directory=data/

# Run quietly (no log output)
gtopt system_c0.json --quiet

# Run with verbose logging
gtopt system_c0.json --verbose

# Output results in Parquet format instead of CSV
gtopt system_c0.json --set output_format=parquet
```

## Command-Line Reference

System files can be passed as positional arguments or with `-s`/`--system-file`.
Multiple system files can be provided and will be merged.

| Short | Long Flag | Argument | Description |
| ----- | --------- | -------- | ----------- |
| `-h` | `--help` | | Show help message and exit |
| `-V` | `--version` | | Show the program version and the git revision it was built from (see below), then exit |
| `-v` | `--verbose` | | Activate maximum verbosity (trace-level logging) |
| `-q` | `--quiet` | `[=arg]` | Suppress all log output to stdout |
| `-S` | `--stats` | | Print pre-solve system statistics and post-solve results summary |
| `-s` | `--system-file` | `arg` | System JSON file(s) to process (also accepted as positional args) |
| `-l` | `--lp-file` | `arg` | Save the LP model to a file (stem; `.lp` or `.mps` added; a trailing `.mps` selects MPS) |
| | `--lp-format` | `arg` | Format of the `--lp-file` dump: `lp` (default) or `mps` |
| | `--lp-scales` | `[=arg]` | Also write the scale factors of the dumped model: `summary` (JSON `<lp-file>.scales.json` only) or `full` (the default without a value: JSON plus Parquet tables of the scaled columns and rows); see [LP scales](lp-scales.md) |
| `-j` | `--json-file` | `arg` | Save the merged system configuration to a JSON file |
| `-e` | `--matrix-eps` | `arg` | Epsilon for matrix sparsity (coefficients below this are zero) |
| `-c` | `--lp-only` | `[=arg]` | Build the LP model and exit without solving |
| `-T` | `--trace-log` | `arg` | Write trace-level log messages to this file |
| | `--solver` | `arg` | LP solver backend: `clp`, `cbc`, `cplex`, `highs`, `gurobi`, `mindopt`, `scip`, `cuopt`. Auto-detected by default (priority `cplex > highs > mindopt > cbc > clp`; override with `GTOPT_SOLVER` or this flag). See [Supported Solvers](solvers.md) for limitations |
| | `--solvers` | | List available LP solver backends and exit; shows each plugin's path, build-id and the solver library version it was compiled against and loaded at run time |
| | `--check-solvers` | `[=solver]` | Run the solver test suite against all (or a named) solver, then exit |
| | `--plugin-check` | `warning\|error` | What a failed solver-plugin integrity check does: `error` refuses the plugin, `warning` logs and loads it. Overrides `$GTOPT_PLUGIN_CHECK`; the built-in default is `error` (CMake `GTOPT_PLUGIN_CHECK_DEFAULT`). See [Plugin integrity](solvers.md#plugin-integrity) |
| | `--config` | `FILE` | Read option defaults from this `.gtopt.conf`-style file instead of `$GTOPT_CONFIG` / `~/.gtopt.conf`. A `./.gtopt.conf` is used only when named here (see [Configuration File](#configuration-file-gtoptconf)) |
| | `--list-dialects` | `[=name]` | List the naming-dialect aliases (and units) and exit; with a name, only that dialect (e.g. `plexos`) |
| | `--json-schema` | `[=type]` | Print the JSON Schema of the input options model and exit (default `PlanningOptions`; e.g. `--json-schema options`) |
| | `--sddp-num-apertures` | `arg` | SDDP backward-pass apertures per phase: unset (default) = every aperture in the phase's list, `0` = none (deterministic Benders backward pass), `N > 0` = the first `N` of each phase's list. Same as `--set sddp_options.num_apertures=<n>` |
| | `--aperture-chunk-mode` | `arg` | How backward apertures are grouped into tasks: `per_scene` (default), `per_aperture`, `chunked`. Same as `--set sddp_options.aperture_chunk_mode=<name>`; see [Aperture task grouping](methods/sddp.md#aperture-task-grouping) |
| | `--aperture-chunk-size` | `arg` | Apertures per task for `--aperture-chunk-mode chunked` (`auto` or `0` = automatic). Without `--aperture-chunk-mode`, still accepted as the deprecated mode selector (`-1` = `per_scene`, `1` = `per_aperture`, `K > 1` = `chunked`) |
| | `--recover` | `[=arg]` | Enable recovery from a previous SDDP run (loads cuts and state variables) |
| | `--fcut-log` | `[=bool]` | Write the SDDP feasibility-cut debug log `gtopt_fcut.log` to the log directory. Same as `--set sddp_options.fcut_log=true` |
| | `--memory-saving` | `off\|compress` | Memory saving for SDDP / cascade (default `compress`): releases the solver between solves, keeps a compressed copy of the LP, and asks the solver to economise memory. `rebuild` is accepted as an alias of `compress`. The former `--low-memory` flag is a deprecated alias |
| | `--memory-limit` | `arg` | Process memory limit for work-pool throttling (e.g. `4096`, `300M`, `5G`) |
| | `--memory-quota` | `arg` | Memory budget as a percentage of the host RAM (e.g. `30`), applied as `--memory-limit`; wins over `--memory-limit` |
| | `--swap-limit` | `arg` | Work-pool swap gate: throttle task dispatch while the process `VmSwap` is at/above this size **and still growing** (e.g. `2048`, `300M`, `5G`; `0` = off; default `2048` MB). A large but static `VmSwap` never throttles. Same as `--set sddp_options.pool_max_process_swap_mb=<MB>` |
| | `--swap-growth` | `arg` | `VmSwap` growth per 5 s window that counts as active swapping for `--swap-limit` (e.g. `64`, `256M`; default `64` MB). Same as `--set sddp_options.pool_swap_growth_mb=<MB>` |
| | `--cpu-factor` | `arg` | Work-pool thread over-commit factor (default: `4.0`) |
| | `--cpu-quota` | `arg` | CPU budget as a percentage of the physical cores (e.g. `30`); scales every work pool down. Does not change the LP solver's own thread count |
| | `--pricing-mode` | `arg` | Nodal prices of a UC (MIP) run: `fixed` (default; integers fixed at the incumbent), `relaxed` (LP relaxation) or `fast_start` (fast-start units relaxed). Dispatch is unchanged; see `pricing.csv` and [Unit Commitment §11.1](unit-commitment.md#111-pricing-modes) |
| | `--mip-gap` | `arg` | Target relative MIP gap (e.g. `0.01` = 1 %); ignored on LPs. Same as `--set solver_options.mip_gap=<value>` |
| | `--time-limit` | `arg` | Time limit in seconds per LP / MIP solve (`0` = none); algorithm-fallback retries and the MIP-start stages share the budget. Same as `--set solver_options.time_limit=<value>` |
| | `--no-mip` | `[=bool]` | Relax every integer variable (commitment, integer expansion, integer decision variables) to continuous: a quick LP lower bound of a MIP case. Same as `--set model_options.continuous_phases=all` |
| | `--mip-start` | `[=bool]` | Build a feasible starting solution for the MIP (LP relaxation, propagation dive, fixed-integer LP check, repair) and hand it to the solver. Same as `--set monolithic_options.mip_start.enabled=true`; see [Monolithic method](methods/monolithic.md) |
| | `--root-basis-cache` | `FILE` | CPLEX only: save the root LP basis of the MIP to `FILE` on the first run and reuse it on later runs |
| | `--write-out` | `arg` | Which results to write, comma-separated: `solution` (`sol`), `dual`, `reduced_cost` (`rc`), `all` (default), `none`. E.g. `--write-out sol,dual` |
| | `--build-mode` | `arg` | LP build parallelism: `serial`, `scene-parallel` (default), `full-parallel`, `direct-parallel` |
| | `--no-scale` | `[=bool]` | Disable every automatic LP scaling (objective, angles, equilibration), to inspect coefficients in physical units |
| | `--lp-reduction` / `--no-lp-reduction` | `[=bool]` | Drop LP columns and rows that are provably zero (e.g. units with `pmax = pmin = 0`). Off by default; helps solvers with weak presolve. Same as `--set model_options.lp_reduction=<bool>` |
| | `--allow-oversupply` | `[=bool]` | Turn the bus balance into generation ≥ demand, so surplus energy is discarded for free (buses in surplus price at 0). Same as `--set model_options.allow_oversupply=true` |
| | `--naming-dialect` | `arg` | Warn on input keys that are aliases of another naming dialect, and write `planning.json` with this dialect's names (`gtopt`, `plp`, `sddp`, `plexos`, `pypsa`, `pandapower`) |
| | `--constraint-mode` | `arg` | How strictly user-constraint element references are resolved: `strict` (default; error), `normal` (drop a constraint whose terms are all unresolved, with a warning), `debug` (drop unresolved terms) |
| | `--lp-dump-backward` | `DIR` | Write the SDDP backward-pass LP of every (iteration, scene, phase) to `DIR` (debugging) |
| | `--async-logger` / `--no-async-logger` | `[=bool]` | Asynchronous logging (default on; forced off with `--trace-log`) |
| | `--set` | `key=value` | Set any planning option (see below) |

> **LP variable/constraint names** are generated automatically whenever
> `--lp-file` or `--lp-debug` is present. There is no separate
> `--lp-names-level` flag: passing either flag turns on all four naming
> fields (`col_with_names`, `row_with_names`, `col_with_name_map`,
> `row_with_name_map`) in `LpMatrixOptions`. See
> [LP variable and row names](#lp-variable-and-row-names) below.
>
> **Flags not in the table above** — `--use-single-bus`, `--use-kirchhoff`,
> `--input-directory`, `--output-directory`, `--input-format`,
> `--output-format`, `--output-compression`, etc. — are still accepted as
> deprecated aliases but emit a warning (full list below). Prefer
> `--set <path>=<value>`.  There are no `--method`, `--demand-fail-cost`
> or `--scale-objective` flags: use `--set method=sddp`,
> `--set model_options.demand_fail_cost=500` and
> `--set model_options.scale_objective=1000`.

> **`--set` key=value**: The recommended way to pass planning options
> from the CLI. Any option that can appear in the JSON `"options"`
> section can be set via `--set`. Nested keys use dot notation.
> Examples:
>
> ```bash
> gtopt case.json --set output_directory=results/
> gtopt case.json --set use_single_bus=true
> gtopt case.json --set solver_options.algorithm=barrier
> gtopt case.json --set solver_options.threads=4
> gtopt case.json --set sddp_options.max_iterations=300
> gtopt case.json --set sddp_options.convergence_tol=1e-5
> gtopt case.json --set input_directory=data/ --set input_format=parquet
> gtopt case.json --set lp_debug=true --set log_directory=logs
> # Array-index paths: target one element of a JSON array
> gtopt case.json --set cascade_options.level_array.0.sddp_options.max_iterations=20
> ```
>
> **Array indices** — numeric path components address array elements by
> position (0-based).  For cascade `level_array` entries specifically, a
> per-index overlay merges element-wise with the base array when sizes
> match; a different-size overlay still replaces the array wholesale,
> preserving the pre-existing multi-file merge behaviour.
>
> **Deprecated aliases** (still work, emit a warning): `-b`
> (`--use-single-bus`), `-k` (`--use-kirchhoff`), `-D`
> (`--input-directory`), `-F` (`--input-format`), `-d`
> (`--output-directory`), `-f` (`--output-format`), `-C`
> (`--output-compression`), `-a` (`--algorithm`), `-t` (`--threads`),
> `--sddp-max-iterations`, `--sddp-min-iterations`,
> `--sddp-convergence-tol`, `--sddp-elastic-penalty`,
> `--sddp-elastic-mode`,
> `--log-directory`, `--cut-directory`, `--lp-debug`, `--lp-error`,
> `--lp-compression`, `--lp-coeff-ratio`.  `--low-memory` (now
> `--memory-saving`) and `--sddp-cpu-factor` (now `--cpu-factor`) are
> accepted as aliases.

## Configuration File (`.gtopt.conf`)

The C++ binary reads default option values from an INI configuration file.
Config-file values are *defaults*: the planning JSON, `--set` and the CLI
flags all override them (see [Precedence](#precedence)).

### Search order

1. `--config FILE` on the command line (exact path; an error if the file
   does not exist)
2. `$GTOPT_CONFIG` environment variable (exact path)
3. `~/.gtopt.conf` (home directory)

The first file found is used, and the startup log names it
(`config file: /path/to/.gtopt.conf`, or `config file: none loaded`).

The current working directory is **not** searched: a `.gtopt.conf`
shipped inside a case bundle or an uploaded archive must not change how
gtopt runs behind your back.  To use a project-local file, name it
explicitly, e.g. `gtopt case.json --config ./.gtopt.conf` (or export
`GTOPT_CONFIG=$PWD/.gtopt.conf`).  When a `./.gtopt.conf` exists but is
not the file in use, gtopt logs a warning saying it was ignored.

When `$GTOPT_CONFIG` is set but names a file that does not exist, gtopt
logs a warning (`$GTOPT_CONFIG='…' does not exist; falling back to
~/.gtopt.conf`) and continues with the home file — a typo in the path no
longer switches the configuration silently.

### Format

```ini
[gtopt]
solver              = highs
algorithm           = barrier
threads             = 4
output-format       = parquet
output-compression  = zstd
sddp-max-iterations = 200
sddp-convergence-tol = 1e-4
use-single-bus      = false
lp-debug            = false
```

### Supported keys

All keys use kebab-case matching the CLI long flags (without `--`):

| Key | Type | Description |
|-----|------|-------------|
| `solver` | string | LP solver backend |
| `algorithm` | string | LP algorithm (`primal`, `dual`, `barrier`) |
| `threads` | int | Number of solver threads |
| `output-format` | string | Output format (`parquet`, `csv`) |
| `output-compression` | string | Compression codec |
| `sddp-max-iterations` | int | Maximum SDDP iterations |
| `sddp-min-iterations` | int | Minimum SDDP iterations |
| `sddp-convergence-tol` | float | SDDP convergence tolerance |
| `use-single-bus` | bool | Single-bus mode |
| `lp-debug` | bool | Save LP debug files |
| `input-directory` | string | Input data directory (CWD-relative) |
| `output-directory` | string | Output directory (CWD-relative) |
| `log-directory`, `cut-directory` | string | Log / SDDP cut directories |
| `lp-only`, `lp-error`, `lp-compression` | | Same as the CLI flags |

Model options such as `demand_fail_cost`, `scale_objective` or `method`
are not config-file keys: set them in the planning JSON or with `--set`.

Enumerated options are checked by name wherever they come from (JSON,
`--set`, config file): a misspelled `model_options.line_losses_mode`,
`kirchhoff_mode`, `tiny_reactance_mode` or `objective_mode` (global,
`cascade_options.model_options` or a cascade level), a per-element mode
string (`Line.line_losses_mode` / `loss_pwl_layout` /
`loss_allocation_mode`, `Generator.cogen_mode`, `FlowRight.flow_mode`,
`DecisionVariable.cost_type`, `UserConstraint.constraint_type`) or an
unknown `solver_options.log_mode` / `scaling` name is an **error** that
names the field, the bad value and the valid choices.  Such typos used to
fall back silently to the default formulation.
Unknown keys and unparsable values in `[gtopt]` and `[solver.<name>]`
are ignored with a one-line warning naming the file, the section and the
key (the Python tools' sections are not checked).

### Precedence

The rule is *the more explicit, the closer to the run, wins*.  Values are
resolved in this order (highest priority first):

1. **Dedicated CLI flags** (`--solver highs`, `--output-format csv`,
   `--sddp-max-iterations 50`, …)
2. **`--set key=value`** (repeatable; a later `--set` of the same key wins)
3. **JSON files** (`"options"` section; with several `-s` files the last
   file that sets a field wins)
4. **Config file** (`.gtopt.conf` `[gtopt]` section)
5. **Built-in defaults** (compiled into the binary)

A config-file value is therefore applied only to the options that neither
the planning JSON, `--set` nor a CLI flag set (`solver` fills
`lp_matrix_options.solver_name`, which beats `$GTOPT_SOLVER`).  Keys with
no JSON counterpart (`lp-file`, `lp-format`, `matrix-eps`, `stats`,
`json-file`, `trace-log`, `async-logger`) are plain CLI defaults.  The
`[solver.<name>]` sections sit below the planning `solver_options` too.
This is the same rule the Python tools apply to their own sections
(explicit CLI argument > config file > default).

> **Note**: The Python scripts (`run_gtopt`, `gtopt_check_output`, etc.) also
> read `~/.gtopt.conf` but use different sections (`[global]`, `[run_gtopt]`,
> etc.).  The `[gtopt]` section is used exclusively by the C++ binary.  See
> [Scripts Guide](scripts-guide.md#gtopt_config) for Python configuration.

## System Configuration File

The system JSON file is the main input to GTOpt. It has three top-level
sections:

```json
{
  "options": { ... },
  "simulation": { ... },
  "system": { ... }
}
```

### Options

Solver and I/O settings:

```json
"options": {
  "output_format": "csv",
  "input_format": "parquet",
  "input_directory": "system_c0",
  "use_single_bus": false,
  "use_kirchhoff": true,
  "demand_fail_cost": 1000,
  "scale_objective": 1000,
  "lp_matrix_options": {
    "equilibration_method": "ruiz",
    "compute_stats": true
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `lp_matrix_options.equilibration_method` | string | Matrix equilibration: `"none"`, `"row_max"`, `"ruiz"`; default `"ruiz"` for multi-bus Kirchhoff, else `"row_max"` |
| `lp_matrix_options.fast_sqrt_method` | string | Approximate sqrt for Ruiz scaling (see `FastSqrtMethod`) |
| `lp_matrix_options.ruiz_max_iterations` | int | Ruiz iteration cap (default 5) |
| `lp_matrix_options.ruiz_tolerance` | float | Ruiz convergence tolerance (default 1e-3) |
| `lp_matrix_options.pin_logic_rows` | bool | Keep pure commitment-logic rows at row factor 1 in row equilibration (default false) |
| `lp_matrix_options.water_dual_target` | float | Target LP dual magnitude for reservoir energy-balance rows (post-equilibration row factor; off when unset) |
| `lp_matrix_options.compute_stats` | bool | Compute coefficient min/max/ratio during flatten |
| `lp_matrix_options.lp_coeff_ratio_threshold` | float | Ratio threshold above which a per-scene/phase breakdown is printed (default 1e7) |
| `output_format` | string | Output format: `"csv"` or `"parquet"` |
| `input_format` | string | Input data format: `"csv"` or `"parquet"` |
| `input_directory` | string | Path to the data directory (relative to the JSON file) |
| `use_single_bus` | bool | Ignore network topology |
| `use_kirchhoff` | bool | Use DC power flow (Kirchhoff's laws) |
| `demand_fail_cost` | float | Penalty cost for unserved demand ($/MWh) |
| `scale_objective` | float | Objective function scaling factor |
| `log_directory` | string | Directory for log and error LP files (default: `"logs"`) |
| `lp_debug` | bool | Save LP debug files to `log_directory` before solving (see below) |
| `lp_fingerprint` | bool | Write LP structural fingerprint JSON to output directory (see [LP Fingerprint](lp-fingerprint.md)) |
| `solver_options.param_file` | string | Backend-native solver parameter file (see [Solver parameter files](#solver-parameter-files-solverssolverprm)) |

#### `lp_debug` — LP debug file output

When `lp_debug` is set to `true`, GTOpt saves the LP model as a `.lp` text
file to the `log_directory` before solving.  This is useful for diagnosing
unexpected solver behaviour (infeasibility, unboundedness, suspicious
objective values).

- **Monolithic solver**: one file per `(scene, phase)` →
  `logs/gtopt_lp_<scene>_<phase>.lp`
- **SDDP solver**: one file per `(iteration, scene, phase)` →
  `logs/gtopt_iter_<iter>_<scene>_<phase>.lp`

If `output_compression` is set to a codec (e.g. `"zstd"`, `"gzip"`, or any
value other than `"uncompressed"`), the files are compressed and the originals
are removed.  The default codec is `"zstd"`.  Compression runs asynchronously
so it does not add latency to the solve.

Via JSON:
```json
{ "options": { "lp_debug": true, "log_directory": "logs", "output_compression": "zstd" } }
```

Via CLI:
```bash
gtopt mycase --set lp_debug=true --set log_directory=logs \
  --set output_compression=zstd
```

#### LP variable and row names

By default, the flat LP representation is assembled **without** column or row
name metadata — names cost memory and are not needed to solve the problem.
When you ask for an LP dump (via `--lp-file` or `--lp-debug`), GTOpt sets all
four naming fields in `LpMatrixOptions` so the resulting `.lp` file is
readable:

- `col_with_names`
- `row_with_names`
- `col_with_name_map`
- `row_with_name_map`

These fields are toggled together by the internal helper
`make_lp_matrix_options(enable_names, …)` in `main_options.hpp`.  The
`enable_names` flag is derived from:

```cpp
enable_names = opts.lp_file.has_value() || opts.lp_debug.value_or(false);
```

There is **no** `--lp-names-level` CLI flag, and JSON does not expose the
four bool fields (they are internal to the LP builder).  If you need named
LP output, just pass either `--lp-file <path>` or `--lp-debug`:

```bash
# Save a readable LP dump next to the planning file
gtopt mycase --lp-file model

# Save one .lp per (scene, phase) / (iter, scene, phase) to log_directory
gtopt mycase --lp-debug --set log_directory=logs
```

The JSON-visible fields of `lp_matrix_options` are:

- `equilibration_method` (`"none"`, `"row_max"`, `"ruiz"`; default
  `"ruiz"` for multi-bus Kirchhoff, else `"row_max"`)
- `fast_sqrt_method` (Ruiz only — see `FastSqrtMethod`)
- `ruiz_max_iterations` (int; default 5), `ruiz_tolerance` (float;
  default 1e-3)
- `pin_logic_rows` (bool; default false): commitment-logic rows keep row
  factor 1
- `compute_stats` (bool)
- `lp_coeff_ratio_threshold` (float; default 1e7)

These fields are resolved per LP build by
`PlanningOptionsLP::resolve_lp_matrix_options`, so they apply identically
to the `gtopt` binary, every cascade level, SDDP aperture systems, and a
`PlanningLP` constructed directly from C++ (unit tests, embedders).

```json
{
  "options": {
    "lp_matrix_options": {
      "equilibration_method": "ruiz",
      "compute_stats": true
    }
  }
}
```

### Simulation

Defines the temporal structure of the optimization:

```json
"simulation": {
  "annual_discount_rate": 0.1,
  "block_array": [
    { "uid": 1, "duration": 1 },
    { "uid": 2, "duration": 2 },
    { "uid": 3, "duration": 3 }
  ],
  "stage_array": [
    { "uid": 1, "first_block": 0, "count_block": 1, "active": 1 },
    { "uid": 2, "first_block": 1, "count_block": 1, "active": 1 },
    { "uid": 3, "first_block": 2, "count_block": 1, "active": 1 }
  ],
  "scenario_array": [
    { "uid": 1, "probability_factor": 1 }
  ]
}
```

- **`annual_discount_rate`**: discount rate for multi-stage CAPEX.
- **Blocks**: time subdivisions within a stage (e.g., peak/off-peak hours),
  each with a `duration` in hours.
- **Stages**: planning periods (e.g., years), referencing a range of blocks.
- **Scenarios**: stochastic scenarios with probability weights.

Boundary (future-cost) cuts are not a simulation field: they are configured
on the FutureCost element in `system.future_cost_array` (e.g.
`{"uid": 1, "name": "fcf", "cuts_file": "boundary_cuts.csv"}`), read by
every method.  The former `simulation.boundary_cuts_file` /
`boundary_cuts_valuation` keys are deprecated aliases; see
[Input Data — Future Cost](input-data.md#326-future-cost).

### System

Defines the physical power system components:

```json
"system": {
  "name": "system_c0",
  "bus_array": [
    { "uid": 1, "name": "b1" }
  ],
  "generator_array": [
    {
      "uid": 1, "name": "g1", "bus": "b1",
      "gcost": 100, "capacity": 20,
      "expcap": null, "expmod": null,
      "annual_capcost": null
    }
  ],
  "demand_array": [
    {
      "uid": 1, "name": "d1", "bus": "b1",
      "lmax": "lmax",
      "capacity": 0, "expcap": 20, "expmod": 10,
      "annual_capcost": 8760
    }
  ]
}
```

**Component types:**

| Component | Key Fields | Description |
|-----------|-----------|-------------|
| Bus | `uid`, `name` | Network node |
| Generator | `uid`, `name`, `bus`, `gcost`, `capacity` | Generation unit with cost and capacity |
| Demand | `uid`, `name`, `bus`, `lmax`, `capacity` | Load with optional expansion |
| Line | `uid`, `name`, `bus_from`, `bus_to`, `capacity` | Transmission line (if multi-bus) |

- **`gcost`**: generation cost ($/MWh).
- **`capacity`**: existing installed capacity (MW).
- **`expcap`**: maximum expansion capacity (MW), `null` if no expansion allowed.
- **`expmod`**: expansion module size (MW).
- **`annual_capcost`**: annualized capital cost of expansion ($/MW/year).
- **`lmax`**: references a data file in the input directory for time-varying load.

## Input Data Directory

Time-varying parameters (e.g., load profiles) are stored as data files in the
input directory, organized by component type:

```text
system_c0/
└── Demand/
    └── lmax.parquet      # Load profile for demand "d1"
```

When a field like `"lmax": "lmax"` appears in the system JSON, GTOpt looks for
a file named `lmax.parquet` (or `lmax.csv`) in the
`<input_directory>/<ComponentType>/` subdirectory.

Data files contain columns indexed by `(scenario, stage, block)` with one
column per component UID.

## Running the Sample Case

The repository includes a sample case in `cases/c0/` that models a simple
single-bus system with one generator and one demand with capacity expansion.

### File layout

```text
cases/c0/
├── system_c0.json          # System configuration
├── system_c0/              # Input data directory
│   └── Demand/
│       └── lmax.parquet    # Load profile
├── output/                 # Reference output (for test validation)
│   ├── solution.csv
│   ├── Generator/
│   │   ├── generation_sol.csv
│   │   └── generation_cost.csv
│   ├── Demand/
│   │   ├── load_sol.csv
│   │   ├── fail_sol.csv
│   │   ├── capainst_sol.csv
│   │   └── ...
│   └── Bus/
│       └── balance_dual.csv
└── planning_c0.json        # Alternative planning file
```

### Run the case

```bash
cd cases/c0
gtopt system_c0.json
```

Or specify a separate output directory:

```bash
gtopt system_c0.json --set output_directory=/tmp/c0_results
```

### Expected output

On success, GTOpt logs its progress and exits with code 0:

```text
[info] starting gtopt 1.2.0
[info] parsing input file system_c0.json
[info] parsing all json files 0.001s
[info] creating lp 0.002s
[info] planning  0.010s
[info] writing output  0.001s
```

The solver produces a `solution.csv` summary:

```text
scene,phase,status,status_name,obj_value,kappa,max_kappa,gap,gap_change,solve_ticks,solve_time_s,solve_calls,infeasible_count,scale_objective,gtopt_version,gtopt_revision
0,0,0,optimal,23163.424133184093,284,-1,0,1,0.0016,0.0021,1,0,1000,1.1.0,v1.1.0-20-g5a4fd2ef8abc
```

One row per solved `(scene, phase)` cell:

- **`status`** / **`status_name`**: `0` / `optimal` when the cell solved;
  `5` / `limit_polished` when a MIP stopped at a limit (time, node,
  solution, memory) with a feasible incumbent that was then polished —
  integers fixed and the LP re-solved (`solver_options.mip_polish`), so
  the objective, primal and duals are the fixed LP's but optimality of
  the MIP is not proven; `-1` / `unknown` for a cell that was never
  solved.
- **`obj_value`**: cell objective in **physical cost units** — the LP
  objective with `scale_objective` reversed (LP-raw × `scale_objective`)
  and objective constants included.  The convention is the same for the
  `monolithic`, `sddp` and `cascade` methods.  Changing `scale_objective`
  does not change `obj_value`.
- **`kappa`** / **`max_kappa`**: cell basis condition number and the run-wide
  SDDP maximum (`-1` when unavailable).
- **`gap`**: final SDDP convergence gap (0 for monolithic solver).
- **`gap_change`**: relative gap change over the stationary window (1.0 if
  secondary criterion disabled or for monolithic solver).
- **`solve_ticks`**, **`solve_time_s`**, **`solve_calls`**,
  **`infeasible_count`**: cumulative solver effort on the cell
  (deterministic ticks, wall seconds, solve calls, non-optimal solves).
- **`scale_objective`**: objective scale factor the cell LP was built with
  (`obj_value / scale_objective` is the LP-raw objective).  SDDP and
  cascade default to 1; monolithic defaults to 1000.
- **`gtopt_version`**, **`gtopt_revision`**: the gtopt build that wrote the
  file, the same on every row: its version (`1.2.0`, or `1.3.0-dev`
  between releases) and the `git describe --always --dirty` of its source
  tree (`unknown` for a build outside a git checkout).  `gtopt --version`
  prints the same pair as `1.2.0 (v1.2.0-20-g5a4fd2ef8abc)`, or just
  `1.2.0` for a clean build of the release tag.

New columns are only ever appended.  The e2e golden comparison
(`tools/gtopt_compare_csv.py`) compares only the columns present in the
golden, never the run-dependent telemetry (`kappa`, `max_kappa`,
`solve_*`, `infeasible_count`) or the build metadata (`gtopt_version`,
`gtopt_revision`), and compares `obj_value` in physical units
(it is a hard error even for the solver-agnostic `output/` goldens).
Refresh the goldens with `tools/update_solution_goldens.py`.

## Output Files

Output is organized by component type. Each component produces solution
(`_sol`) and cost (`_cost`) files, plus dual values (`_dual`) for constraints.

### Solution summary

`solution.csv` — overall optimization result (objective value, status, SDDP
convergence metrics) and the gtopt version and git revision that produced
it.

`pricing.csv` — written for MIP runs: one row per (scene, phase) with
the requested and applied `pricing_mode`, whether it fell back to
`fixed`, the relaxed / pinned integer column counts, the pricing-LP
status and the reason (see [Unit Commitment §11.1](unit-commitment.md#111-pricing-modes)).

### Generator output

| File | Description |
|------|-------------|
| `Generator/generation_sol.csv` | Generation dispatch per (scenario, stage, block) |
| `Generator/generation_cost.csv` | Generation cost per (scenario, stage, block) |

Example `generation_sol.csv`:

```csv
"scenario","stage","block","uid","value"
1,1,1,1,10
1,2,2,1,15
1,3,3,1,8
1,4,4,1,20
1,5,5,1,20
```

Output files (CSV and Parquet) use a long layout: one row per non-zero
`(scenario, stage, block, uid)` cell, with the value in `value` (here the
dispatch in MW of the generator with UID 1).  Zero values are not
written.  The key columns are `int32` (the `Uid` type); uids and block
numbers above 65535 are valid, so readers must not cast them to 16-bit
types.

### Demand output

| File | Description |
|------|-------------|
| `Demand/load_sol.csv` | Served load per (scenario, stage, block) |
| `Demand/load_cost.csv` | Load cost per (scenario, stage, block) |
| `Demand/fail_sol.csv` | Unserved demand (load shedding) per (scenario, stage, block) |
| `Demand/fail_cost.csv` | Cost of unserved demand |
| `Demand/capainst_sol.csv` | Installed capacity per stage |
| `Demand/capainst_cost.csv` | Investment cost of installed capacity |
| `Demand/capainst_dual.csv` | Dual of capacity installation constraint |
| `Demand/expmod_sol.csv` | Expansion module decisions per stage |
| `Demand/expmod_cost.csv` | Cost of expansion modules |
| `Demand/capacost_sol.csv` | Capacity cost allocation |
| `Demand/capacost_cost.csv` | Capital cost values |
| `Demand/capacost_dual.csv` | Dual of capacity cost constraint |
| `Demand/capacity_dual.csv` | Dual of capacity limit constraint |
| `Demand/balance_dual.csv` | Dual of demand balance constraint |

### Bus output

| File | Description |
|------|-------------|
| `Bus/balance_dual.csv` | Nodal price (dual of bus balance constraint) per (scenario, stage, block) |

Example `Bus/balance_dual.csv`:

```csv
"scenario","stage","block","uid:1"
1,1,1,100
1,2,2,100
1,3,3,100
1,4,4,100
1,5,5,100
```

The bus balance dual represents the marginal cost of energy at each bus
(locational marginal price).

## Advanced Usage

### Output in Parquet format

For large cases, Parquet format is more compact and faster to read:

```bash
gtopt system_c0.json --set output_format=parquet
gtopt system_c0.json --set output_format=parquet --set output_compression=zstd
```

Every solution table is in long form: `scenario`, `stage`, `block`,
`uid` (all `int32`) and `value`.  Primal (`*_sol`) and cost (`*_cost`)
tables omit exact zeros — a missing row means zero.  Dual tables
(`*_dual`, e.g. `Bus/balance_dual`) are dense: every cell of the LP rows
is written, zeros included, so per-block price averages and counts need
no gap filling.  Dense dual tables are compressed like every other
table with `output_compression`.

### Export the LP model

Save the linear programming formulation for debugging or external solvers:

```bash
gtopt system_c0.json --lp-file model.lp
```

### MPS output for external solvers

`--lp-file` writes CPLEX LP by default.  Use `--lp-format mps` (or give a
`--lp-file` ending in `.mps`) to write the model as an MPS file instead, with
the same row/column names and `MARKER` sections for integer columns, so
external solvers can read it directly without an `.lp` → `.mps` conversion:

```bash
gtopt system_c0.json --lp-file model.mps --lp-only
gtopt system_c0.json --lp-file model --lp-format mps --lp-only
```

Every backend writes both formats with its own library, keeping the same
row and column names in the `.lp` and the `.mps` file, except cuOpt, which
only writes MPS:

| Backend | `.lp` | `.mps` |
|---------|-------|--------|
| CPLEX, Gurobi, MindOpt, HiGHS, SCIP | yes | yes |
| CBC / CLP (OSI) | yes | yes (binary columns as `BV` bounds) |
| cuOpt | no | yes |

Without an explicit format (`--lp-file model`), gtopt writes LP when the
backend can, and otherwise the format the backend supports (MPS for cuOpt),
logging a warning that names the file written.  A format named explicitly,
with `--lp-format` or a `.lp` / `.mps` extension on `--lp-file`, is strict:
when the backend cannot write it, gtopt reports an error naming the backend
and suggesting the other format, and does not switch format.  With
`--lp-only` that error ends the run with a non-zero status; otherwise the
solve continues without the dump.  The diagnostic dumps (`--lp-debug`, error
and high-kappa LPs) always fall back to the format the backend supports.

`--lp-compression gzip|zstd|...` also compresses the `--lp-file` dump (`.lp`
or `.mps`), which is replaced by `model.mps.gz`, `model.lp.zst`, etc.
Without the option the `--lp-file` dump stays uncompressed; the
`--lp-debug` / SDDP dumps use `zstd` by default.

### Build without solving

Create the LP and exit (useful for validation or LP export):

```bash
gtopt system_c0.json --lp-file model.lp --lp-only
```

Add `--lp-scales` to also write `model.scales.json` (objective scale,
block weights, numerics) and `model.scales_{cols,rows}.parquet` (scaled
columns and rows), what is needed to read the dumped model in physical
units; `--lp-scales summary` writes the JSON only.  See
[LP scales](lp-scales.md).

### Single-bus mode

Ignore network topology and solve as a single-bus (copper-plate) system:

```bash
gtopt system_c0.json --set use_single_bus=true
```

### Kirchhoff (DC power flow) mode

Enable DC power flow constraints for multi-bus systems:

```bash
gtopt system_c0.json --set use_kirchhoff=true
```

### Solver parameter files (`solvers/<solver>.prm`)

A case can carry backend-native solver tuning in
`solver_options.param_file` (CPLEX `.prm`; cuOpt reads it too, and
derives its `cuopt.cfg` sibling from the same directory).  The file is
read by the backend before gtopt applies its typed `solver_options`
fields, so an explicit `optimal_eps`, `threads`, … still wins over the
same parameter in the file.

- **Auto-discovery**: when `param_file` is not set (JSON, `--set`),
  gtopt looks for `<input_directory>/solvers/<solver>.prm`, where
  `<solver>` is the backend the LP is actually built with (`--solver`,
  else the planning pin, else `$GTOPT_SOLVER` / the plugin priority).
  The converters (`plp2gtopt`, `plexos2gtopt`) emit this layout.  A
  `.prm` in the case root or under `<cwd>/input/solvers` is **not**
  picked up.
- **Relative paths**: a relative `param_file` (e.g. the
  `"solvers/cplex.prm"` the converters pin) is case data.  It is
  resolved against `input_directory` **first** — the case directory
  wins — and against the working directory only when the case has no
  such file.  The log names the location used:

  ```text
  solver_options.param_file 'solvers/cplex.prm' resolved against input_directory '<case>': '<case>/solvers/cplex.prm'
  ```

  The same applies to every `SolverOptions` bundle (monolithic, MIP-start
  relaxation, SDDP forward/backward, cascade).
- A file that cannot be found anywhere is reported at WARN and the
  solver runs with its **default** parameters.
- The resolved absolute path is recorded in `gtopt_state.json`, so a
  replay of the snapshot uses the same tuning.
- Only CPLEX and cuOpt read the main parameter file; with other backends
  gtopt warns that it is ignored (HiGHS, MindOpt and Gurobi read only a
  `<solver>_warmstart.*` sibling of it).

### cuOpt tuning file (`cuopt.cfg`)

When the cuOpt (GPU) backend is selected and the planning option
`solver_options.param_file` points into a `solvers/` directory (GTOpt
cases typically pin it to the CPLEX `solvers/cplex.prm`), the cuOpt
plugin also reads a **sibling** tuning file from that same directory:
`cuopt.cfg` (preferred) or, when no `.cfg` exists, the legacy
`cuopt.prm`. The pairs are applied **last**, so they override every
default the plugin sets — including the algorithm mapping and the
presolver.

`cuopt.cfg` is standard INI — the same dialect as `.gtopt.conf`
(`key = value`, `#`/`;` comment lines, inline trailing comments,
optional `[cuopt]` section) — and values may be the **documented names**
instead of cuOpt's integer codes (case-insensitive; plain numbers are
always accepted too):

```ini
# solvers/cuopt.cfg — example
[cuopt]
method    = concurrent      ; concurrent | pdlp | dual_simplex | barrier
presolve  = pslp            ; default | off | papilo | pslp
crossover = on              ; booleans: true/false, on/off, yes/no, 1/0
pdlp_solver_mode = stable3  ; stable1 | stable2 | methodical1 | fast1 | stable3
pdlp_precision   = mixed    ; default | single | double | mixed
mip_determinism_mode = deterministic
relative_gap_tolerance = 1e-4
warmstart = on              ; plugin-local: auto warm start
```

A symbolic value that matches neither a documented name nor a boolean
is dropped with a warning listing the expected names — never forwarded
as garbage. Legacy `cuopt.prm` files (`<name> <value>` per line) keep
working unchanged and also accept the named values.

Any parameter from cuOpt's `constants.h` is accepted (the string-keyed
setter handles integer- and float-typed parameters too). The most
useful keys:

| Key | Values | Effect |
|-----|--------|--------|
| `presolve` | `default`, `off`, `papilo`, `pslp` (or `-1`/`0`/`1`/`2`) | On real CEN LPs PSLP was fastest with crossover; note any non-`off` value disables the plugin's auto warm start for that solve, and the key is ignored (with a warning) while a MIP start is set |
| `method` | `concurrent`, `pdlp`, `dual_simplex`, `barrier` (or `0`–`3`) | same override as `GTOPT_CUOPT_METHOD` but per-case |
| `crossover` | boolean | vertex duals from the PDLP/barrier legs |
| `pdlp_solver_mode` | `stable1`, `stable2`, `methodical1`, `fast1`, `stable3` (or `0`–`4`) | PDLP restart strategy |
| `pdlp_precision` | `default`, `single`, `double`, `mixed` (or `-1`/`0`/`1`/`2`) | `single`/`mixed` can help on GeForce GPUs with throttled fp64 |
| `infeasibility_detection`, `strict_infeasibility` | `0`/`1` | PDLP infeasibility-verdict controls (relevant before trusting lone-PDLP verdicts) |
| `time_limit`, `iteration_limit`, `work_limit`, `node_limit` | numeric | solve budgets (MIP: `node_limit`) |
| `absolute_/relative_gap_tolerance`, `absolute_/relative_primal_tolerance`, `absolute_/relative_dual_tolerance` | float | LP convergence tolerances |
| `mip_relative_gap`, `mip_absolute_gap`, `mip_integrality_tolerance` | float | MIP termination |
| `mip_determinism_mode` | `opportunistic`, `deterministic` (or `0`/`1`) | parallel B&B reproducibility |
| `mip_heuristics_only`, `mip_scaling`, `mip_presolve`, `mip_symmetry`, cut-family toggles (`mip_*_cuts`) | int | MIP search shaping |
| `dual_postsolve` | `0`/`1` | recover duals through presolve (on by default) |
| `num_cpu_threads`, `num_gpus`, `random_seed` | int | resources / reproducibility |
| `folding`, `augmented`, `dualize`, `ordering`, `barrier_iterative_refinement`, `eliminate_dense_columns`, `cudss_deterministic` | int | barrier (cuDSS) internals |
| `per_constraint_residual`, `save_best_primal_so_far`, `first_primal_feasible` | `0`/`1` | PDLP residual semantics / stop-early |
| `warmstart` | boolean | **plugin-local** (not forwarded to cuOpt): per-case default for the automatic PDLP warm start on incremental re-solves. Precedence: default (on) < tuning file < `GTOPT_CUOPT_WARMSTART` env |

Unknown keys log a warning and are skipped; the solve proceeds.

### Naming and unit dictionaries

The field-name aliases (naming dialects) and the unit dictionary are
compiled into the `gtopt` binary, so they always match its version.  The
copies installed under `<prefix>/share/gtopt/` are reference material
and are never read automatically.  To use a custom dictionary, point an
environment variable at it:

| Variable | File |
|----------|------|
| `GTOPT_NAMING_DIALECTS_FILE` | naming dialects (`naming_dialects.json` format) |
| `GTOPT_UNIT_DIALECTS_FILE` | unit dictionary (`unit_dialects.json` format) |

gtopt logs the override, warns when it lacks entries of the built-in
dictionary (probably a stale copy), and falls back to the built-in one
when the file is missing, empty or invalid.  See
[naming conventions §12.4a](analysis/naming-conventions.md).

### Merging multiple system files

Multiple JSON files can be passed and will be merged into a single system.
This allows separating system definition from simulation parameters:

```bash
gtopt base_system.json additional_generators.json scenario_data.json
```

### Save merged configuration

Export the merged system configuration as a single JSON file:

```bash
gtopt base.json extensions.json --json-file merged_system.json
```

### Override input directory

If the data files are in a different location than specified in the JSON:

```bash
gtopt system.json --set input_directory=/data/case_inputs
```

### Control logging

```bash
# Maximum verbosity (trace level)
gtopt system.json --verbose

# No output at all
gtopt system.json --quiet

# Use spdlog environment variable for fine-grained control
SPDLOG_LEVEL=debug gtopt system.json
```

### Unit commitment

Enable unit commitment for thermal generators by adding a `commitment_array`
to the system and marking stages as `chronological`:

```bash
# Run with LP relaxation (fastest, no branch-and-bound)
gtopt uc_case.json --set model_options.continuous_phases=all

# Run with full MIP (integer binaries)
gtopt uc_case.json --set model_options.continuous_phases=none

# Minimise emissions instead of cost
gtopt uc_case.json --set model_options.objective_mode=emissions
```

Carbon prices and emission caps are not command-line options: they are
set on an `EmissionZone` (`price`, `cap`, `cap_cost`).  To try them
without editing the case, put the emission elements in a second planning
file; gtopt merges the files given on the command line:

```bash
gtopt uc_case.json carbon_price.json
```

```json
{
  "system": {
    "emission_array": [{"uid": 1, "name": "co2"}],
    "emission_zone_array": [
      {"uid": 1, "name": "system_co2",
       "emissions": [{"emission": "co2", "weight": 1.0}],
       "price": 30.0}
    ],
    "emission_source_array": [
      {"uid": 1, "name": "g1_co2", "generator": "g1",
       "zone": "system_co2", "emission": "co2", "rate": 0.9}
    ]
  }
}
```

Replace `"price": 30.0` with `"cap": 500000.0` for a hard cap per stage,
or add `"cap_cost"` for a soft one.  The former
`model_options.emission_cost` / `emission_cap` options were removed; see
[Emissions](emissions.md) and
[Unit Commitment §6](unit-commitment.md#6-emission-cost-and-cap).

> See [Unit Commitment Guide](unit-commitment.md) for the full formulation,
> JSON schema, and worked examples.

## Troubleshooting Solver Issues

This section covers common solver problems, their causes, and diagnostic
steps.

### Exit codes

GTOpt uses the following exit codes:

| Exit code | Meaning |
|-----------|---------|
| `0` | Optimal solution found |
| `1` | Non-optimal solution (infeasible or abandoned, but no critical error) |
| `2` | Input error (missing file, invalid JSON, bad options) |
| `3` | Internal error (unexpected exception, solver crash) |

Check `solution.csv` (or `solution.parquet`) for the solver `status` field:
`0` = optimal, `1` = infeasible, `2` = unbounded, `5` = MIP limit
reached with a polished incumbent (exit code 1).

### Infeasible model (status=1)

An infeasible model means no feasible solution exists given the constraints.

**Common causes:**

- **Demand exceeds generation capacity**: total demand in a block exceeds the
  sum of all available generation plus imports.  Check that generator
  `capacity` and `pmax` values are sufficient.
- **Missing bus connection**: a demand bus has no generator or line connecting
  it to the rest of the network.  Verify that every demand bus has at least
  one generator or transmission line.
- **Over-constrained reserves**: spinning reserve requirements that exceed
  available headroom.  Try reducing the reserve requirement or increasing
  `reserve_fail_cost` to allow some unserved reserve.
- **Tight physical bounds on state variables**: reservoir volume or battery
  SoC bounds that are inconsistent across phases (e.g., initial volume
  outside the `[emin, emax]` range).

**Diagnostic steps:**

1. Check `output/Demand/fail_sol.csv` -- if all values are zero but the model
   is infeasible, the issue is likely in network constraints rather than
   generation adequacy.
2. Set `demand_fail_cost` to a high value (e.g., 10000) to allow load
   shedding.  If the model becomes feasible, the infeasibility was caused by
   insufficient generation.
3. Enable LP debug files to inspect the LP formulation:
   ```json
   { "options": { "lp_debug": true, "log_directory": "logs" } }
   ```
4. Try `use_single_bus: true` to eliminate network constraints.  If the model
   becomes feasible, the issue is in the transmission network (missing lines,
   insufficient capacity).
5. For SDDP, check the `logs/` directory for `error_scene_*_phase_*.lp`
   files which indicate which scene/phase is infeasible.

### Unbounded model (status=2)

An unbounded model is rare and typically indicates missing variable bounds.

**Common causes:**

- A generator with negative `gcost` and no `pmax` upper bound.
- A non-positive `scale_theta` (ignored, treated as 1, with a warning).
- Incorrect `scale_objective` value (e.g., 0 or negative).

**Diagnostic steps:**

1. Enable `lp_debug: true` and inspect the LP file for variables with
   infinite bounds and negative objective coefficients.
2. Verify that all generators have a finite `capacity` or `pmax`.
3. Check that `scale_objective` and `scale_theta` are positive numbers.

### Slow convergence (SDDP)

When the SDDP gap does not close within the expected number of iterations:

**Suggestions:**

- **Increase `max_iterations`**: the default of 100 may be insufficient for
  large problems.  Try 200--500.
- **Enable the stationary-gap criterion**: some problems converge to a
  non-zero gap plateau.  Set `stationary_tol` to a small positive value
  (e.g. `0.01` = 1% gap-change threshold) with `stationary_window` (default
  10) to declare convergence when the gap stops improving.  The solver will
  log `[CONVERGED]` with `stationary gap convergence` when this criterion
  triggers.
- **Adjust `elastic_penalty`**: if the elastic filter activates frequently,
  try increasing the penalty (e.g., `1e8`) to discourage elastic slack.
- **Try `cut_sharing_mode: "expected"`**: sharing cuts across scenes can
  accelerate convergence for stochastic problems.
- **Check variable scaling**: poorly scaled variables (e.g., reservoir volumes
  in m3 vs dam3) can cause numerical issues.  Use `variable_scales` to
  normalize large-valued state variables.
- **Try `elastic_mode: "multi_cut"`**: when `single_cut` mode produces
  weak cuts, the `multi_cut` mode adds per-slack bound cuts that can
  tighten the approximation faster.
- **Lower `convergence_tol`**: if the gap oscillates near the tolerance,
  try a slightly larger tolerance (e.g., `1e-3` instead of `1e-4`).
- **Check boundary cuts**: for problems where the planning horizon is too
  short, loading `future_cost_array[].cuts_file` can provide a better terminal
  approximation and speed convergence.
- **Monitor progress**: use the SDDP monitoring API (`api_enabled: true`)
  and the `gtopt_monitor` script to visualize the convergence trajectory.
  Check the `gap_change` column in `solution.csv` to assess whether the
  gap has plateaued.

### Out of memory

When the LP is too large to fit in memory:

**Suggestions:**

- **Reduce the number of blocks/stages**: aggregate time periods or use
  representative days instead of hourly resolution.
- **Use SDDP decomposition**: set `method: "sddp"` to decompose the
  problem into smaller per-phase LPs instead of one large monolithic LP.
- **Use the cascade solver**: set `method: "cascade"` for multi-level
  SDDP that starts with a simplified model and progressively refines.
  See [Cascade Method](methods/cascade.md).
- **Reduce the number of scenes**: fewer scenarios in each scene means
  smaller per-scene LPs.
- **Reduce reserve zones**: spinning reserve constraints add rows per bus
  per block; consolidating reserve zones reduces LP size.
- **Don't ask for LP dumps in production**: naming metadata is only generated
  when `--lp-file` or `--lp-debug` is set.  Leaving both off keeps the four
  `col_with_names` / `row_with_names` / `col_with_name_map` /
  `row_with_name_map` fields at `false` and avoids name storage/lookup maps.

### File not found errors

**Common causes and solutions:**

- **Relative path resolution**: `input_directory` is resolved relative to the
  directory containing the JSON file, not the current working directory.
  If the JSON file is at `cases/c0/system.json` and `input_directory` is
  `"system_c0"`, the data files are expected at `cases/c0/system_c0/`.
- **Missing component subdirectory**: data files must be in a subdirectory
  named after the component type (e.g., `Demand/lmax.parquet`,
  `Generator/pmax.parquet`).
- **Format mismatch**: if `input_format: "parquet"` but only CSV files exist
  (or vice versa), the solver will fail to find the data.  GTOpt tries the
  preferred format first, then falls back to the other format.
- **Case sensitivity**: file names and component type subdirectories are
  case-sensitive on Linux (e.g., `Demand/` not `demand/`).
- **CLI override**: use `--set input_directory=<path>` to override the
  directory specified in the JSON file.

### LP debug files

Enable LP debug output to inspect the mathematical formulation:

```json
{
  "options": {
    "lp_debug": true,
    "log_directory": "logs",
    "lp_compression": "none"
  }
}
```

**File locations:**

- **Monolithic solver**: `logs/gtopt_lp_<scene>_<phase>.lp`
- **SDDP solver**: `logs/gtopt_iter_<iter>_<scene>_<phase>.lp`

When `lp_compression` is not set to `"none"`, files are compressed
(default: `zstd`).  Decompress with `zstd -d <file>.lp.zst`.

**Inspecting LP files:**

The `.lp` format is a standard text format readable by most LP solvers.
You can:

- Open the file in a text editor to inspect variable names, constraints,
  and objective coefficients.
- Load it into an external solver (e.g., GLPK, Gurobi, CPLEX) for
  independent verification.
- Use `grep` to search for specific variable or constraint names.  Column
  and row names are populated automatically when `--lp-file` or `--lp-debug`
  is in effect.

**Additional debug options:**

- `--lp-only` (alias `-c`) builds all LP matrices without solving, useful
  for inspecting the formulation without waiting for the solve.
- `lp_matrix_options.lp_coeff_ratio_threshold` (default: `1e7`) controls
  when per-scene/phase coefficient ratio diagnostics are printed.  Lower
  the threshold to detect numerical conditioning issues.

---

### Known solver issues

#### MindOpt 2.3.0: objective counted the offset twice (fixed)

Status: **fixed**. Affected gtopt builds from the native objective
offset (`e75db122`) up to this fix, with MindOpt 2.3.0.

Before the fix, the MindOpt backend (`--solver mindopt` /
`GTOPT_SOLVER=mindopt`) reported an objective that was too large by the
LP objective constant: `obj_value` in `solution.csv`, the log's `Obj`
lines and every SDDP bound derived from them. The primal dispatch was
correct. For example, `s1b` reported 256000 instead of 6000, and
`ieee_9b` reported 12709996 instead of 794196. The constant is
`demand_fail_cost × Σ demand × duration` from the `fail = lmax − load`
substitution.

Cause: on MindOpt 2.3.0, `MDO_DBL_ATTR_PRIMAL_OBJ_VAL` already includes
`MDO_DBL_ATTR_OBJ_CON`, and `MindOptSolverBackend::obj_value()` added
the stored offset to it a second time. Earlier probes missed this
because they set `OBJ_CON` only after a first solve. A change to
`OBJ_CON` on its own does not invalidate the solution, so the next
`MDOoptimize` does nothing and `PrimalObjVal` keeps the old constant.
The backend now computes the objective as `c·x* + offset` from
MindOpt's primal point and the stored offset. `OBJ_CON` is still set,
but only so that MindOpt's MIP relative gap is measured against the
true objective. Cross-backend regression tests are in
`test/source/test_solver_obj_offset_per_solver.cpp`.

## Batch Execution

### Shell script

```bash
#!/bin/bash
# Run all JSON cases in a directory
for f in cases/*/system_*.json; do
  echo "=== Running $f ==="
  dir=$(dirname "$f")
  name=$(basename "$dir")
  gtopt "$f" --set output_directory="results/$name"
done
```

### Python

```python
import subprocess
from pathlib import Path

def run_case(system_file, output_dir=None):
    """Run a single gtopt case and return the exit code."""
    cmd = ["gtopt", str(system_file)]
    if output_dir:
        cmd += ["--set", f"output_directory={output_dir}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED: {system_file}")
        print(result.stderr)
    return result.returncode

def run_all_cases(cases_dir):
    """Run all cases under a directory."""
    for system_file in Path(cases_dir).glob("*/system_*.json"):
        case_name = system_file.parent.name
        run_case(system_file, output_dir=f"results/{case_name}")

if __name__ == "__main__":
    run_all_cases("cases/")
```

---

## See also

- **[Unit Commitment Guide](unit-commitment.md)** — Three-bin UC
  formulation, emission framework, and worked examples
- **[Mathematical Formulation](formulation/mathematical-formulation.md)**
  — Full LP/MIP optimization formulation with academic references
- **[Planning Options Reference](planning-options.md)** — Full option
  hierarchy, merge semantics, and solver configuration
- **[Planning Guide](planning-guide.md)** — Step-by-step planning guide
  with worked examples
- **[Input Data Reference](input-data.md)** — Input data structure and file format
  reference (complete options reference)
- **[SDDP Method](methods/sddp.md)** — SDDP decomposition algorithm,
  configuration, and convergence details
- **[Cascade Method](methods/cascade.md)** — Multi-level hybrid SDDP
  method with cut inheritance and progressive refinement
- **[Monolithic Method](methods/monolithic.md)** — Default monolithic
  method, boundary cuts, and sequential mode
- **[Scripts Guide](scripts-guide.md)** — Python conversion utilities
