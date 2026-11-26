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
technology, total spill) — these too can move when costs tie exactly.  Small
tie-break costs (for example
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
| HiGHS | 1.16.0; 1.15.0; 1.12.0 | 1.16.0 is the CI pin (parallel MIP by default) and the version `install.sh` builds from source when the distribution package is older (Ubuntu 26.04 / Debian 13 ship 1.12.0) |
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

`gtopt --solvers` lists the plugins that load and, for each, which solver
library it actually uses (see [Plugin integrity](#plugin-integrity)); each
solve logs the solver name and the version of the library loaded at run
time in the run log (for example `solver=highs/1.16.0`).

## Choosing a solver

- **Default**: when `--solver` is not given, gtopt picks the first
  available plugin in the order `cplex > highs > mindopt > cbc > clp`.
  Gurobi, SCIP and cuOpt are opt-in (`--solver gurobi|scip|cuopt` or the
  `GTOPT_SOLVER` environment variable).  A `GTOPT_SOLVER` solver that is
  not installed falls back to this order with a warning; one whose plugin
  was refused by the [integrity check](#plugin-integrity) is an error
  (`GTOPT_SOLVER_FALLBACK_ON_REFUSED=1` falls back instead).
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

### HiGHS

How gtopt's generic solver options map onto HiGHS (1.16):

| gtopt option | HiGHS setting |
|--------------|---------------|
| `algorithm: default` | `solver=choose` (dual simplex for LPs) |
| `algorithm: dual` / `primal` | `solver=simplex`, `simplex_strategy=1` / `4` |
| `algorithm: barrier` | `solver` from `barrier_solver`: `auto` (default) = `ipm`, i.e. **HiPO** (parallel) when HiGHS was built with `-DHIPO=ON`, otherwise IPX (serial); `hipo` = HiPO (IPX if unavailable); `ipx` = IPX |
| `crossover: none` | `run_crossover=off` (any other value: `on`), set on every solve |
| `threads: 0` (default) | 4 for an LP, every physical core for a MIP |
| `mip_lp_algorithm` (MIP root LP) | `mip_lp_solver`: unset/`default` = `choose`, `primal`/`dual` = `simplex`, `barrier` = the `barrier_solver` IPM |
| `barrier_solver` (MIP) | `mip_ipm_solver`: `auto` = `choose`, `ipx`, `hipo` |
| `mip_heuristic_effort` | `mip_heuristic_effort` (unset: HiGHS 0.05, or 0.5 under `mip_start_effort: repair`) |
| `advanced_basis` (warm start) | simplex, presolve off, off the resident basis; a solve with no resident basis stays cold, with presolve (`cold_solve_without_basis: false` applies the warm settings anyway) |
| `kappa_mode` | `estimate`: `getKappa(exact=false)`; `exact`: `getKappa(exact=true)`; `off`: not computed nor printed (default: `$GTOPT_KAPPA_MODE`, else the compiled default, `off` in release builds) |

- **Barrier is parallel only with HiPO.**  `solver=ipm` picks HiPO when
  `libhighs_extras.so` (built with `-DHIPO=ON`, see `BUILDING.md`) sits
  next to `libhighs.so`; HiPO parallelises the ordering, the normal
  equations and the multifrontal factorisation over the HiGHS `threads`.
  Without it HiGHS falls back to IPX, which is essentially serial.
  Crossover (HiPO or IPX) is IPX's crossover and runs serially.
- **Without crossover** (`crossover: none`) HiGHS returns the interior
  point: primal values plus interior row duals and reduced costs, no
  basis.  `LinearInterface` uses those duals directly; they are optimal
  duals up to the barrier tolerance (relative primal-dual gap ~1e-9 with
  IPX, ~5e-9 with HiPO on the CEN SDDP cells), so a Benders cut built
  from them is valid once its intercept is lowered by that gap, which the
  SDDP forward pass already does (`interior_cut_rel_gap`).  The next
  solve of that LP starts cold: there is no basis to warm-start from.
- **SDDP cells: dual simplex is the right HiGHS method.**  On the CEN 2y
  cells (about 10^4 rows, 5 MB LP files) a cold dual simplex takes
  0.9 s, a warm re-solve with the next iteration's cuts 0.6 s and an
  aperture re-solve 0.3 s, while HiPO takes 1.3 s without crossover and
  2.1 s with it, and hardly scales with threads at this size (1.5 s on 1
  thread, 1.3 s on 16), so a HiGHS barrier-without-crossover forward
  pass does not pay off there.  HiPO pays off on large LPs (monolithic runs,
  big relaxations) — see the benchmark in the HiPO commit message.
- **MIP root with an IPM does not help on gtopt UC MIPs.**  On
  `cuc39_r10_s10` (26 k rows, 2 160 binaries, 8 threads) the default
  root found its first incumbent at 5.5 s (objective 3447) and stopped
  at a 1 % gap after 18 s (985.8); `mip_lp_algorithm: barrier` with
  HiPO found a better first incumbent (997) but later, at 8.0 s, and
  needed 24.7 s; IPX 9.0 s / 25.2 s.  `mip_heuristic_effort: 0.3` and
  1 or 16 threads changed nothing before the 1 % gap.  The defaults
  therefore stay; the options exist for other models.
- **kappa** is the Hager estimate of the basis condition number (as
  CPLEX's `CPX_KAPPA`; `kappa_mode: exact` for the exact value, `off`
  to skip it); it is unavailable after an IPM solve, with or without
  crossover.
- **crossover is applied on every solve**, so a forward solve with
  `crossover: none` does not leave the backward re-solves of the same
  LP without crossover.  Set `none` in every pass to keep it off.

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

A plugin file that is world-writable, or that sits in a world-writable
directory (such as `/tmp`), is never loaded; the refusal is listed by
`gtopt --solvers`.  Each loaded plugin path is logged at info level, and
every `GTOPT_*` environment variable in effect (including
`GTOPT_PLUGIN_DIR`) is logged at start-up, with secret-like values masked.

## Plugin integrity

Before and after loading a plugin, gtopt checks that it is the plugin it
expects, linked to the solver library it was compiled against.  This
catches a plugin picked up from another build tree, a stale plugin left in
an install directory, and a different solver library shadowing the one the
plugin was built with (for example an old `~/.local/lib/libhighs.so.1`
found through `LD_LIBRARY_PATH`).

| Check | How |
|-------|-----|
| **Build-id manifest** (before `dlopen`) | The linker writes a GNU build-id note into every plugin; at build and install time CMake writes it to `libgtopt_solver_<name>.so.build-id` next to the plugin.  gtopt compares the two: a mismatch means the `.so` was replaced or copied without its manifest. |
| **Build tag** | The host and every plugin embed `<git describe>+<build-tree id>` (`gtopt_plugin_build_tag`); a different tag means another build tree or a stale install. |
| **Solver library version** | The plugin reports the solver version from the headers it was compiled with and the version of the library loaded at run time (`gtopt_solver_build_info`); gtopt finds that library's path and build-id.  A different major.minor version fails the check; a patch-only difference is logged and accepted. |
| **RUNPATH shadowing** | gtopt compares the loaded solver library with the one the plugin's RUNPATH points to (`LD_LIBRARY_PATH` takes precedence over RUNPATH). |
| **Missing identity** | A plugin without a manifest, a build-id or the identity entry points (built by an older gtopt). |
| **Solver library identity** (every plugin) | At build time CMake records the GNU build-id (else the SHA-256) of every shared library the plugin links and compiles it into the plugin (`gtopt_plugin_linked_libraries`).  At load gtopt reads the build-id of the solver library actually mapped in the process (the object that defines the solver's version function) and compares: a library the plugin was not linked against, or one with another build-id, is **foreign**, even when it reports the right version.  See [Solver library identity](#solver-library-identity). |
| **Solver version pin** (HiGHS) | The plugin reports the release gtopt is pinned to (`gtopt_solver_version_pin`; CMake `GTOPT_HIGHS_PIN_VERSION`, 1.16.0) and gtopt compares it with the version of the library loaded at run time.  Unlike the check above, the patch counts: another release can end on another optimal vertex of a degenerate LP.  See [Solver version pin](#solver-version-pin-highs). |

One option decides what a failed check does:

| Value | Effect |
|-------|--------|
| `error` (default) | Refuse the plugin (or, for a solver-library check, that solver) with an error. |
| `warning` | Log a warning that names the paths and versions involved, and load the plugin. |

The mode comes from, in order of precedence:

1. the command-line flag `--plugin-check warning|error`;
2. the environment variable `GTOPT_PLUGIN_CHECK=warning|error` (an empty
   or invalid value is ignored with a warning);
3. the compiled-in default, set at configure time with the CMake option
   `GTOPT_PLUGIN_CHECK_DEFAULT` (`error` unless configured otherwise;
   any other value fails the configure step):

   ```bash
   cmake -S all -B build -DGTOPT_PLUGIN_CHECK_DEFAULT=warning
   ```

It is not a planning option because plugins can load before the planning
file is read (`--solvers`, `--check-solvers`).  `gtopt --help` shows the
compiled-in default, and `gtopt --version` and `gtopt --solvers` end with
the effective mode and its source, for example:

```text
plugin check: error (compiled default)
plugin check: warning (from $GTOPT_PLUGIN_CHECK; compiled default: error)
```

Plugins built in the same build tree as the binary, or installed with it
by `cmake --install`, always pass.  When you copy plugins by hand (for
example next to the binary), copy each `libgtopt_solver_<name>.so.build-id`
manifest with its plugin, or the plugin is refused as unidentified.  Use
`--plugin-check=warning` to run with a plugin from another build anyway.

The ABI version check is separate and always refuses a plugin built
against an incompatible plugin interface.  CPLEX is linked statically into
its plugin, so its library cannot differ (`linked statically into the
plugin`).  CBC has no run time version query; its library path is shown
and its version is left unchecked.

### Solver library identity

The version checks trust what the solver library reports about itself, so
a fake `libhighs.so.1` that answers "1.16.0" would pass them, and the
RUNPATH check only compares paths.  The identity check does not trust the
library: it compares the GNU build-id of the library mapped at run time
with the one recorded when the plugin was linked (SHA-256 of the file when
the library has no build-id).  It applies to every plugin linked against
a shared solver library (HiGHS, Gurobi, MindOpt, SCIP, cuOpt, CLP/CBC);
a solver linked statically into its plugin (CPLEX) reports `static`.

| Status | Meaning |
|--------|---------|
| `verified` | same build-id (or SHA-256) as at build time |
| `verified, sha256 verified` | full-library hash build: same build-id and same file content |
| `SHA256 MISMATCH` | full-library hash build: the build-id matches but the file content does not (a modified copy that kept the build-id note) |
| `sha256 unverified` | full-library hash build: the loaded file cannot be read |
| `FOREIGN` | another build-id, or a library the plugin was not linked against (e.g. an `LD_PRELOAD` interposing the solver's functions) |
| `unverified` | the loaded library's identity cannot be read (no build-id) |
| `static` / `unchecked` | nothing to compare (static solver; plugin from an older gtopt) |

Every status but `verified`, `static` and `unchecked` is refused under
plugin check mode `error`, with the loaded and the expected path and
build-id and the values of `LD_LIBRARY_PATH` / `LD_PRELOAD`.  Only two
overrides turn the refusal into a warning: `GTOPT_<SOLVER>_ALLOW_UNPINNED=1`
(e.g. `GTOPT_HIGHS_ALLOW_UNPINNED=1`) and `--plugin-check=warning`.  A
plugin built with `-DGTOPT_HIGHS_ALLOW_UNPINNED=ON` (as `install.sh` does)
accepts another HiGHS *version*, but not another *library*: it still
verifies that it runs with the very library it was linked against, which
an `install.sh` build always does.  A refused solver is unavailable: `--solver` and
`GTOPT_SOLVER` naming it fail with exit code 3.  For example:

```text
Solver 'highs': the loaded library /tmp/fake/libhighs.so.1 (soname libhighs.so.1, build-id 5c0f...) is not the library the plugin was built against (/opt/highs-1.16.0/lib/libhighs.so.1.16.0, build-id 7b9ca7b14efe...); a foreign or replaced solver library (LD_LIBRARY_PATH=/tmp/fake, LD_PRELOAD=<unset>). Fix: remove it from LD_LIBRARY_PATH / LD_PRELOAD, restore /opt/highs-1.16.0/lib/libhighs.so.1.16.0, or rebuild gtopt against the library you want to use. Refused (plugin check mode 'error'; set GTOPT_HIGHS_ALLOW_UNPINNED=1 or use --plugin-check=warning to load it anyway).
```

Upgrading the solver library in place (e.g. a distro package update)
changes its build-id: rebuild gtopt, or set the override.  `gtopt
--solvers` shows the status after the build-id, e.g. `[build-id
7b9ca7b14efe, verified]`.

#### Full-library hash (`-DGTOPT_SOLVER_LIB_FULL_HASH=ON`)

A build-id is a note inside the file, so a modified copy of the library
can keep it (e.g. `objcopy --add-section`, or a patched code byte) and
pass the build-id check.  Configure with `-DGTOPT_SOLVER_LIB_FULL_HASH=ON`
(default `OFF`) to close that gap: each plugin then also records the
SHA-256 of every shared library it links, and at load gtopt hashes the
file of the solver library actually mapped (resolved from the loaded
object, not from a search path) and refuses a different content with
status `SHA256 MISMATCH` (same overrides as above):

```text
Solver 'highs': the loaded library /x/plugins/libhighs.so.1 (soname libhighs.so.1, build-id 7b9ca7b14efe...) has the build-id of the library the plugin was built against (/opt/highs-1.16.0/lib/libhighs.so.1.16.0) but sha256 41c2... (expected sha256 9d07...): its content differs -- a modified or forged copy (LD_LIBRARY_PATH=<unset>, LD_PRELOAD=<unset>). Fix: restore /opt/highs-1.16.0/lib/libhighs.so.1.16.0, or rebuild gtopt against the library you want to use. Refused (plugin check mode 'error'; set GTOPT_HIGHS_ALLOW_UNPINNED=1 or use --plugin-check=warning to load it anyway).
```

The cost is one SHA-256 of each loaded solver library per process start,
about 7 ms per MB (logged at info level, e.g. `highs: sha256 of ... in
51.3 ms`).  Measured on a Xeon build host: libhighs 1.16.0 (7.2 MB) 51 ms,
libClp (1.7 MB) 11 ms, libCbc (1.2 MB) 8 ms.  `gtopt
--solvers` prints whether the option is on (`Solver-library full hash
(GTOPT_SOLVER_LIB_FULL_HASH): on`) and the per-library result, e.g.
`[build-id 7b9ca7b14efe, verified, sha256 verified]`.

### Solver version pin (HiGHS)

gtopt pins HiGHS to one release, `GTOPT_HIGHS_PIN_VERSION` (1.16.0): the
version CI builds and the goldens are generated with.  Another HiGHS can
end on another optimal vertex of a degenerate LP, so the duals and costs
differ from the goldens even though both solutions are optimal.  The pin
is enforced twice:

- at **configure** time, a HiGHS other than the pinned release is an error
  unless `-DGTOPT_HIGHS_ALLOW_UNPINNED=ON` (see `BUILDING.md`);
- at **run** time, the HiGHS plugin carries the pin (and whether it was
  built with `GTOPT_HIGHS_ALLOW_UNPINNED=ON`), and gtopt refuses it when the
  `libhighs` loaded at run time is not exactly that release, for example
  a distro `libhighs` 1.12 found instead of `~/opt/highs-1.16.0`.  HiGHS is
  then not available; an explicit `--solver highs` fails with the reason
  instead of switching to another solver:

```text
Solver 'highs' not available.
  Plugin /path/to/libgtopt_solver_highs.so: Solver 'highs': the loaded library /usr/lib/x86_64-linux-gnu/libhighs.so.1.12.0 is highs 1.12.0, but this gtopt pins highs 1.16.0 (the release CI builds and the goldens are generated with; another release can end on another optimal vertex of a degenerate LP and change duals and costs). Fix: rebuild gtopt against a highs 1.16.0 prefix (-DHIGHS_ROOT_DIR=, e.g. ~/opt/highs-1.16.0) and do not shadow that library through LD_LIBRARY_PATH. Refused (plugin check mode 'error'; set GTOPT_HIGHS_ALLOW_UNPINNED=1, rebuild with -DGTOPT_HIGHS_ALLOW_UNPINNED=ON, or use --plugin-check=warning to load it anyway).
Available solvers: clp, cbc
```

The mismatch only warns ("Loading it anyway (...)", naming the reason)
when any of these holds:

| Override | Scope |
|----------|-------|
| `GTOPT_HIGHS_ALLOW_UNPINNED=1` (also `true`/`yes`/`on`) | this process: the pin and the library identity check |
| plugin built with `-DGTOPT_HIGHS_ALLOW_UNPINNED=ON` (`install.sh` does) | that build: the pin check only, never the identity check |
| plugin check mode `warning` (`--plugin-check`, `GTOPT_PLUGIN_CHECK`) | every integrity check |

The same holds for the `GTOPT_SOLVER` environment variable: when the
solver it names belongs to a plugin that was **refused** by any integrity
check (build-id, build tag, library version, shadowing or this pin),
gtopt fails with the same error and exit code 3 instead of falling back.
When the named solver simply has no plugin (not installed), gtopt still
warns and falls back to the automatic choice.  Set
`GTOPT_SOLVER_FALLBACK_ON_REFUSED=1` to fall back on a refused plugin as
well (the behaviour before this check).

A runtime library that does not report its version is treated like a
mismatch (`pin unverified`).  `gtopt --solvers` shows the pin after the
compiled/runtime versions, e.g. `runtime 1.16.0, compiled 1.16.0 (match),
pin 1.16.0 (pinned)`; a refused HiGHS is listed under "Refused plugins".
Plugins without a pin (other solvers, or a HiGHS plugin configured with
an empty `GTOPT_HIGHS_PIN_VERSION`) are not checked.

`gtopt --solvers` prints, after the list of solver names:

```text
Plugin details:
  highs: /path/to/build/plugins/libgtopt_solver_highs.so
    build-id   a3f9b01458f1 (manifest: match)
    build tag  v1.2.0-12-gf0130c3ec2ee+ba9d05d3fbb5 (matches this binary)
    highs      runtime 1.16.0, compiled 1.16.0 (match), pin 1.16.0 (pinned), /opt/highs-1.16.0/lib/libhighs.so.1.16.0 [build-id 7b9ca7b14efe, verified]
  osi: /path/to/build/plugins/libgtopt_solver_osi.so
    build-id   18f2b2bbf9a2 (manifest: match)
    build tag  v1.2.0-12-gf0130c3ec2ee+ba9d05d3fbb5 (matches this binary)
    clp        runtime 1.17.10, compiled 1.17.10 (match), /usr/lib/x86_64-linux-gnu/libClp.so.1.14.10 [build-id 87fe0c060258]
    cbc        runtime unknown, compiled 2.10.12 (unchecked), /usr/lib/x86_64-linux-gnu/libCbc.so.3.10.12 [build-id d60be457ef8f]
  cplex: /path/to/build/plugins/libgtopt_solver_cplex.so
    build-id   6f9a68818dc8 (manifest: match)
    build tag  v1.2.0-12-gf0130c3ec2ee+ba9d05d3fbb5 (matches this binary)
    cplex      runtime unknown, compiled 22.1.1 (static), linked statically into the plugin

plugin check: error (compiled default)
```

A HiGHS 1.15 library picked up through `LD_LIBRARY_PATH` by a plugin
built against HiGHS 1.16 gives:

```text
Solver 'highs': loaded /opt/highs/lib/libhighs.so.1.15.0 but the plugin's RUNPATH points to /opt/highs-1.16.0/lib/libhighs.so.1.16.0 (LD_LIBRARY_PATH=/usr/local/lib). Refused (plugin check mode 'error'; use --plugin-check=warning or GTOPT_PLUGIN_CHECK=warning to load it anyway).
Solver 'highs': plugin was compiled against highs 1.16.0 but the loaded library /opt/highs/lib/libhighs.so.1.15.0 is 1.15.0: ...
```

In mode `error`, `gtopt --solvers` lists refused plugins under
`Refused plugins:` with the reason, also when no plugin loaded at all
(it then starts with `No LP solver available: every solver plugin found
was refused.`).  A run that names the refused solver (`--solver highs`,
`GTOPT_SOLVER=highs`) fails with `Solver 'highs' not available.` and the
refusal; a run with no solver named, where every plugin found was
refused, fails with `No solver available: solver plugins were found,
but every one was refused or failed to load:` and the refusals.  Both
exit with code 3.  `No solver plugins found.` and its install hints are
kept for a run that finds no plugin at all.  The run log shows the same
identity lines when a plugin loads.

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
