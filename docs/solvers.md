# Supported LP/MIP Solvers

gtopt builds a sparse LP/MIP and hands it to a solver **plugin**
(`libgtopt_solver_<name>.so`) loaded at runtime.  This page lists the
supported solvers, how well each one is tested, and the limitations that
matter when choosing one.

## Summary

| Solver | License | LP | MIP | Basis / warm start | Status | Recommended for |
|--------|---------|----|-----|--------------------|--------|-----------------|
| [CPLEX](https://www.ibm.com/products/ilog-cplex-optimization-studio) | commercial | yes | yes | yes | tested | everything (monolithic, SDDP, cascade, MIP) |
| [HiGHS](https://highs.dev) | MIT | yes | yes | yes | tested | everything; the recommended open-source choice |
| [MindOpt](https://opt.alibabacloud.com) | commercial | yes | yes | yes | tested | everything |
| [SCIP](https://www.scipopt.org) | Apache-2.0 | yes | yes | **no** | tested | MIP; LP works but every re-solve is cold |
| [CBC](https://github.com/coin-or/Cbc) | EPL-2.0 | yes | yes | yes (via CLP) | tested | LP; for MIP prefer HiGHS or a commercial solver |
| [CLP](https://github.com/coin-or/Clp) | EPL-2.0 | yes | **no** | yes | tested | pure LP only |
| [cuOpt](https://github.com/NVIDIA/cuopt) | Apache-2.0 | yes (GPU) | yes (GPU) | **no** | tested, **monolithic only** | large monolithic LPs on an NVIDIA GPU |
| [Gurobi](https://www.gurobi.com) | commercial | yes | yes | yes | **code present, not tested** | — |

"Tested" means the plugin runs the C++ unit tests and the solver-agnostic
test suites (`--solver <name>`), plus end-to-end cases.  Gurobi's plugin
compiles and implements the full backend interface, but no Gurobi license
was available to run the test suites, so treat it as experimental.

> **Tested is not a guarantee.**  Passing the test suites does not mean a
> solver works on every case.  Several backends already needed special
> handling to behave correctly (objective-constant reporting, infinity
> sentinels, presolve and warm-start settings), and
> a particular case — or a new solver version — may still expose a problem
> in the future.  Keep **at least two solvers** available (for example
> HiGHS plus a commercial one) and compare their results from time to
> time — and always when a run looks suspicious: same status and the
> same objective within tolerance (see below for why the rest may differ).

### Why results differ between solvers (degeneracy)

Power-system LPs are highly **degenerate**: they usually have many optimal
solutions, not one.

- **Primal ties (dual degeneracy).** Two units with the same cost, two
  parallel paths with no losses, hydro water that can be used in either of
  two blocks at the same value, an unbinding line — any of these leaves a
  whole set of dispatches with exactly the same total cost.  Each solver
  (and each version, presolve setting, algorithm, thread count or even a
  small change in the case) may return a different vertex of that set.
- **Non-unique duals (primal degeneracy).** When more constraints are
  active than strictly needed — a unit at its limit while a line is also
  at its limit, a reservoir at its bound — the marginal prices (LMPs,
  water values, line duals) are not unique: any value in an interval is
  correct, and different solvers pick different ends of it.
- **Tolerances.** Solvers stop when feasibility and optimality are met
  within tolerances (typically 1e-6 to 1e-9, after scaling), so small
  violations, tiny non-zero flows and last-digit differences in the
  objective are normal.  Barrier and first-order (PDLP) methods without
  crossover return interior or ε-optimal points rather than vertices.

So it is **expected** that two solvers — or the same solver in two
versions, or on two slightly different cases — report the same objective
(within the relative tolerance) but different dispatch, flows or prices.
That is not a bug.  What *is* a warning sign:

- different status (optimal vs. infeasible/unbounded) on the same case;
- objectives that differ by more than the solver tolerance;
- a solution that violates bounds or balances beyond tolerance;
- duals with the wrong sign for the constraint type, or prices far outside
  the range the costs allow.

When comparing, compare the objective first; compare primal and dual
values only where the solution is known to be unique, or through
aggregates that are less sensitive to ties (total cost, energy by
technology, total spill) — these too can move when costs tie exactly.  Small tie-break costs (for example
`loss_cost_eps` on line losses) are a deliberate way to make some of these
choices unique.

## Solver versions used in development

The plugins were developed and tested against these versions.  Other
versions of the same API usually work, but they have not been checked;
when you use a different one, compare it against a second solver (see the
note above).

| Solver | Versions developed / tested with | Notes |
|--------|----------------------------------|-------|
| CPLEX | 22.1.1 | |
| HiGHS | 1.15.0; 1.12.0 | 1.12.0 is the Ubuntu/Debian `libhighs-dev` package and the version `install.sh` builds from source when no package exists |
| MindOpt | 2.3.0 | |
| SCIP | 9.1.1 | `install.sh` installs the SCIP Opt Suite 10.1.0 package, not yet covered by the test suites |
| CBC / CLP | 2.10.12 / 1.17.10 | Ubuntu/Debian `coinor-libcbc-dev` / `coinor-libclp-dev` |
| cuOpt | 26.08; 26.12 (nightly) | from 26.10 the C API ships in the `libcuopt_mathopt` wheel; `install.sh` installs `>= 26.8` |
| Gurobi | — | written against the C API of Gurobi 10–14 (`libgurobi100` … `libgurobi140`); not run against any version |

**Tried another version?**  If you run gtopt with a solver version not
listed here — and it works, or it does not — please tell us in the
community
[Discussions](https://github.com/gtopt-community/gtopt/discussions) (or
open an issue for a failure), with the solver, its version and the case
type, so this table can grow.

`gtopt --solvers` lists the plugins that load; each solve logs the solver
name and version in the run log (for example `solver=highs/1.15.0`).

## Choosing a solver

- **Default**: when `--solver` is not given, gtopt picks the first
  available plugin in the order `cplex > highs > mindopt > cbc > clp`.
  Gurobi, SCIP and cuOpt are opt-in (`--solver gurobi|scip|cuopt` or the
  `GTOPT_SOLVER` environment variable).
- **Open source**: use **HiGHS**.  It handles LP and MIP and supports the
  basis warm start that SDDP and cascade depend on.
- **Problems with integer variables** (unit commitment, integer expansion
  modules, integer cuts, MIP starts): use HiGHS or a commercial solver
  (CPLEX, MindOpt).  CBC solves MIPs but is much slower and less robust on
  these models; it is not recommended for them.  **Never use CLP**: it is a
  pure LP solver and cannot handle integer variables.
- **SDDP / cascade**: use a solver with basis support (CPLEX, HiGHS,
  MindOpt, CLP/CBC for pure LP).  Backward-pass apertures are dual
  re-solves warm-started from the forward-pass basis; without a basis every
  solve is cold.

List the plugins that load on the current machine with:

```bash
gtopt --solvers
```

## Limitations by solver

### CLP

Pure LP solver.  It reports no MIP support, so it must not be used for
cases with integer variables.  For LP-only cases (economic dispatch, DC
OPF, LP relaxations) it works and supports warm starts.

### CBC

CBC handles integer variables and shares the CLP plugin (`osi`), so its LP
behaviour is the same as CLP's.  For MIP it is markedly weaker than HiGHS
and the commercial solvers; prefer those whenever the case has integer
variables.

### SCIP

SCIP does not expose a simplex basis through the plugin, so basis
round-trips and warm starts are unavailable: each LP re-solve starts from
scratch.  It is correct for SDDP but slow there; it is a reasonable
open-source MIP alternative for monolithic runs.

### cuOpt (NVIDIA GPU)

cuOpt is supported **for the monolithic method only**.  SDDP and cascade
rely on two things cuOpt does not provide:

- **No warm start**: setting a basis or solution hint is a no-op, so every
  re-solve (forward pass, backward-pass apertures) rebuilds from scratch.
- **Duals that are not vertex duals**: the barrier method maps to cuOpt's
  concurrent mode, which can finish with PDLP.  PDLP duals are
  ε-optimal rather than exact basic duals, so Benders cuts built from them
  are not guaranteed valid.

cuOpt needs an NVIDIA GPU and driver; without one the plugin loads but
fails its validation and is skipped.  `install.sh` installs it from the
`libcuopt-cu12` / `libcuopt-cu13` wheels (version `>= 26.8`) when a GPU is
present.

### Gurobi

The plugin is implemented (LP, MIP, basis) but has **not been tested**
against a real Gurobi installation.  Reports and fixes are welcome.

### Commercial solvers in general

CPLEX, Gurobi and MindOpt are never installed by gtopt: install them
yourself with a valid license and point the build at them (see
[BUILDING.md](../BUILDING.md); `install.sh` detects CPLEX under
`/opt/cplex`, `~/opt/cplex` and `CPLEX_Studio*` trees, Gurobi under
`/opt/gurobi*` and `~/gurobi*`, MindOpt under `/opt/mindopt` and
`~/mindopt`).

## Plugin search path

Plugins are looked up in, in order: `$GTOPT_PLUGIN_DIR`,
`<exe>/../lib/gtopt/plugins/`, `<exe>/plugins/`,
`/usr/local/lib/gtopt/plugins/`.

## Adding support for another solver

If you need a solver that is not listed, write a new plugin following the
existing ones.  `plugins/highs/` and `plugins/mindopt/` are the simplest
models; `plugins/cplex/` is the most complete one (basis, MIP starts,
tuning files).  A plugin is self-contained: no change to the gtopt core is
needed.

1. **Backend class** — `plugins/<name>/<name>_solver_backend.{hpp,cpp}`:
   derive from `gtopt::SolverBackend`
   ([include/gtopt/solver_backend.hpp](../include/gtopt/solver_backend.hpp))
   and implement its pure-virtual methods with the solver's C API: load
   the problem (columns, rows, bounds, coefficients), add/modify
   rows and columns, solve and resolve, and return status, objective,
   primal values, duals and reduced costs.  Declare honestly what the
   solver can do through the capability queries (`supports_mip()`,
   `get_basis()` / `set_basis()` — keep the default "unsupported" when the
   solver cannot provide a basis, as SCIP and cuOpt do).
2. **Plugin entry points** — `plugins/<name>/<name>_plugin.cpp`: the
   `extern "C"` functions the registry looks up with `dlsym`:
   `gtopt_plugin_abi_version()` (return `k_solver_abi_version`),
   `gtopt_plugin_name()`, `gtopt_solver_names()` (the names accepted by
   `--solver`), `gtopt_create_backend(name)` and
   `gtopt_solver_infinity(name)`.  Copy them from an existing plugin.
3. **Build** — a `Find<Name>.cmake` module in `cmake_local/` that locates
   headers and libraries (search the usual system paths and the user's
   home, e.g. `/opt/<name>` and `~/opt/<name>`), a three-line
   `plugins/<name>/CMakeLists.txt` calling `gtopt_add_solver_plugin(...)`,
   and an `add_subdirectory(<name>)` block in `plugins/CMakeLists.txt`
   guarded by `find_package`.  The plugin must be optional: when the solver
   is not installed the build skips it with one status line.
4. **Tests** — run `gtopt --check-solvers=<name>` and the C++ test suite
   with `GTOPT_SOLVER=<name>`; the solver-agnostic tests then exercise the
   new backend.  Fix what fails, and note any limitation (no MIP, no basis,
   no warm start) in the table at the top of this page.
5. **Optional** — add the solver to the installer's detection in
   `install.sh` and to `BUILDING.md`.  New solvers are opt-in by default;
   the automatic priority order lives in `source/solver_registry.cpp`.

**Please share it.**  If you write a plugin for a new solver (or improve
an existing one), we would be very grateful if you contributed it back
with a pull request to
[gtopt-community/gtopt](https://github.com/gtopt-community/gtopt) — see
[CONTRIBUTING.md](../CONTRIBUTING.md).  Include the solver version you
tested with and any limitation you found, so this page stays accurate.

Plugins may be distributed under their own terms: the plugin interface
headers are Apache-2.0 (see [LICENSE-EXCEPTION.md](../LICENSE-EXCEPTION.md)).

## See also

- [Usage Guide](usage.md) — `--solver`, `--solvers` and solver options
- [SDDP Method](methods/sddp.md) — why warm starts matter for apertures
- [Monolithic Method](methods/monolithic.md)
