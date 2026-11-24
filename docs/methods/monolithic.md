# Monolithic Solver

## 1. Introduction

The **monolithic solver** is the default solver in GTOpt.  It assembles the
full LP formulation for each scene (across all phases and stages) and solves
it in a single call to the LP solver.  Different scenes are solved in
parallel using the adaptive work pool.

The monolithic solver is the simplest and most robust approach: it produces
the exact optimal solution in one solve, without iterative decomposition.
For problems with many phases and state variables, the SDDP solver may be
more efficient (see [SDDP Method](sddp.md)).

### When to use the monolithic solver

| Criterion | Monolithic | SDDP |
|-----------|:----------:|:----:|
| Default solver | Yes | No (set `method: "sddp"`) |
| Number of phases | Any (1+) | 2+ required |
| Solution quality | Exact (single LP solve) | Iterative convergence |
| Memory | Full LP in memory per scene | Per-phase LPs (smaller) |
| State variables | Handled implicitly (shared LP) | Explicit Benders cuts |
| Boundary cuts | Supported (optional) | Supported (standard) |

---

## 2. Solver Modes

The monolithic solver supports two solve modes, configured via
`monolithic_options.solve_mode`:

### 2.1 `"monolithic"` (default)

All phases are assembled into a single LP per scene.  State variables
(reservoir volumes, battery SoC) are shared columns within the same LP,
so inter-phase coupling is handled implicitly.  This is the most robust
mode and produces the globally optimal solution.

### 2.2 `"sequential"` (planned)

Phases are solved sequentially within each scene, propagating state
variable values from phase $t$ to phase $t+1$.  This mode uses less
memory (one phase LP in memory at a time) but may require boundary cuts
for the last phase to approximate future costs.

> **Note**: In the current implementation, `resolve_scene_phases()`
> already solves phases sequentially.  The "sequential" mode flag is
> reserved for future optimizations (lazy phase construction).

---

## 3. Equivalence with SDDP

Under specific conditions, the monolithic and SDDP solvers produce
**identical optimal solutions**.  This equivalence is guaranteed when:

1. **No apertures** (`num_apertures` = number of scenarios, or
   aperture sampling disabled) --- SDDP backward pass sees the same
   scenarios as the forward pass.

2. **No cut sharing** (`cut_sharing: "none"`) --- cuts are not
   broadcast across scenes.

3. **SDDP has converged** --- the lower bound (from cuts) equals the
   upper bound (from forward simulation) within tolerance.

### When equivalence breaks

| Condition | Effect |
|-----------|--------|
| Aperture sampling (subset of scenarios) | SDDP backward pass uses a subset; monolithic uses all |
| Cut sharing (`"max"` or `"expected"`) | SDDP shares cuts across scenes; monolithic has implicit coupling |
| SDDP not converged | SDDP lower bound < true optimum |
| No state variables | Both are equivalent (no inter-phase coupling) |

### Formal equivalence

For a deterministic problem (single scenario) with $T$ phases, the
monolithic LP is:

$$\min \sum_{t=1}^{T} c_t^T x_t \quad \text{s.t.} \quad
A_t x_t + B_t x_{t-1} \ge b_t \; \forall t$$

The SDDP decomposition solves this via iterative forward/backward
passes.  At convergence, the SDDP cuts exactly represent the
recourse function $Q_t(x_{t-1})$ for each phase, and the SDDP
solution matches the monolithic optimum [[1]](#monolithic-ref1).

For stochastic problems with multiple scenarios, equivalence holds
when the backward pass evaluates all scenarios (no aperture sampling)
and cuts are not shared across scenes.

---

## 4. Configuration

### 4.1 JSON Options

```json
{
  "options": {
    "method": "monolithic",
    "monolithic_options": {
      "solve_mode": "monolithic",
      "solver_options": {"time_limit": 18000}
    }
  },
  "system": {
    "future_cost_array": [
      {
        "uid": 1,
        "name": "fcf",
        "cuts_file": "boundary_cuts.csv",
        "mode": "separated",
        "iteration_limit": 0
      }
    ]
  }
}
```

### 4.2 Option Reference

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `solve_mode` | string | `"monolithic"` | `"monolithic"` or `"sequential"` |
| `solver_options` | object | — | Per-method LP solver overrides (merged over the global `solver_options`) |
| `mip_start` | object | — | Initial-MIP-solution (warm-start) pipeline; see [4.5](#45-mip-warm-start-pipeline-mip_start) |
| `boundary_cuts_file` | string | — | Deprecated alias → `future_cost_array[].cuts_file` |
| `boundary_cuts_mode` | string | — | Deprecated alias → `future_cost_array[].mode` |
| `boundary_cut_sharing_mode` | string | — | Deprecated alias → `future_cost_array[].sharing` |
| `boundary_max_iterations` | int | — | Deprecated alias → `future_cost_array[].iteration_limit` |

### 4.3 Solve Timeout

The former `monolithic_options.solve_timeout` key was removed (gtopt now
rejects it).  Bound the LP solve with the solver time limit instead:
`monolithic_options.solver_options.time_limit` (seconds), or the global
`options.solver_options.time_limit`.  Unset means no time limit.

`time_limit` is a wall-clock **budget for the whole solve** of the LP/MIP,
not a per-attempt limit: the MIP-start stages (LP relaxation, repair)
draw from it, the MIP gets what they leave, and the algorithm fallback
retries (`max_fallbacks`) only get the time that is still left — they
are skipped once less than 1 s remains, keeping the last status and
incumbent.  A `time_limit: 3600` run therefore ends after about one hour
(plus output writing and the fix-integers dual-recovery LP, which is not
charged against the budget).

### 4.4 Boundary Cuts

Boundary cuts approximate the expected future cost beyond the planning
horizon.  They are typically generated by a prior SDDP run or an
external model.

The boundary cuts are configured on the FutureCost element
(`system.future_cost_array`; full field list in
[Input Data — Future Cost](../input-data.md#326-future-cost)), the same
element SDDP and cascade read.  The monolithic solver reads **only** the
FutureCost element: it never reads `sddp_options`.  The
`monolithic_options.boundary_*` keys and `simulation.boundary_cuts_file` /
`boundary_cuts_valuation` are deprecated aliases translated into the
element at load time (with a warning); `sddp_options.boundary_*` keys are
ignored under `method = monolithic`.  A case converted for SDDP
(`plp2gtopt`, `plexos2gtopt`, `sddp2gtopt` and `igtopt` all write the
method-neutral FutureCost element) therefore keeps its boundary cuts when
re-run with `--set method=monolithic`; no fallback to `sddp_options` is
needed.  An older JSON that still carries `sddp_options.boundary_cuts_file`
logs an "ignored under method=monolithic" warning — migrate it with
`tools/migrate_future_cost.py` (or move the key to
`future_cost_array[].cuts_file`).  A relative `cuts_file` resolves
against `options.input_directory`, exactly as under SDDP.  The α scale is
`future_cost_array[].scale_alpha` (auto-derived when unset), and cuts are
loaded with the default `cut_coeff_eps = 1e-8`.

When `cuts_file` is set, the monolithic solver loads the cuts
into the last phase of each scene's LP **before** the parallel solve
dispatch.  This adds a future-cost variable ($\alpha$) to the objective
and constrains it via the loaded cuts:

$$\alpha \ge \text{rhs}_k + \sum_j \pi_{k,j} \cdot x_j
\quad \forall k \in \text{cuts}$$

**Load modes** (`mode`):

| Mode | Description |
|------|-------------|
| `"noload"` | Skip loading even if a file is specified |
| `"separated"` (default) | Each cut assigned to its matching scene UID |
| `"combined"` | All cuts broadcast to all scenes |
| `"phi_expectation"` | PLP-literal NVarPhi plane-hydrology φ columns priced `p_s/NVarPhi` (raw CSV) |

Terminal-α sharing (`sharing`): `"per_scene"`, `"shared"` or
`"multicut"` (N `varphi_s` columns, one per scenario).

**Iteration filtering** (`iteration_limit`):

When set to a positive integer $N$, only cuts from the last $N$ distinct
SDDP iterations (by the `iteration` column) are loaded.  Set to `0` to
load all cuts (default).

The CSV format is identical to the SDDP boundary cuts format
(see [SDDP Method](sddp.md)).

### 4.5 MIP warm-start pipeline (`mip_start`)

For unit-commitment MIPs, `monolithic_options.mip_start` configures a
staged pipeline that computes an initial integer solution and injects it
into the backend before branch-and-cut:

| Stage | Sub-object | What it does |
|-------|------------|--------------|
| 0 | `relax` | Solve/diagnose the LP relaxation (`check`, `report_saturated`, `on_infeasible`: `stop` / `warn` / `feasopt`; own `solver_options` overlay) |
| 1 | `round` | Rounding threshold of the dive targets (`threshold`, default 0.5) |
| 1-3 | `dive` | **Default generator**: relaxation-guided propagation dive, fixed-integer LP verification, elastic / sub-MIP repair (see below) |
| 2 | `domain_rules` | Legacy power-system commitment repair (`min_up_down`, `commitment_logic`, `peak_injection`); used when `dive.enabled=false` or no flat LP is retained |
| 4 | `scip_repair` | Optional SCIP `completesol` feasibility repair of an UNVERIFIED candidate (requires the SCIP plugin; self-skips when absent or when the dive verified its start) |
| 5 | `inject` | Hand the start to the backend (`effort`: `solve_fixed` default for a verified dive start, `repair` otherwise; `check_feasibility` / …) |

```json
{
  "monolithic_options": {
    "mip_start": {
      "enabled": true,
      "dive": { "repair_rounds": 3, "sub_mip_time_limit": 120 },
      "inject": { "effort": "solve_fixed" }
    }
  }
}
```

#### The dive generator (`mip_start.dive`)

Rounding the LP relaxation cell by cell ignores the rows that couple the
integer columns, so on real UC MIPs the rounded start is infeasible and the
backend discards it (CPLEX: `No solution found from 1 MIP starts`).  On the
PCP 2026-04-22 weekly UC the rounded start broke combined-cycle
configuration exclusivity (`u_A + u_B <= 1`), plant-config ⇒ unit rows
(`-cc + Σ u_unit >= 0`), operational `u <= 0` rows and min up/down windows.
The dive replaces the round + rules candidate with three generic stages:

1. **Propagation dive.**  The integer columns are fixed one at a time in
   relaxation-confidence order — integral cells first, then fractional cells
   by decreasing `|x - 1/2|` — to their rounded relaxation value.  After each
   fixing, activity-based bound propagation runs over every row of the flat
   LP (min/max row activity → implied column bounds; integer bounds rounded,
   continuous bounds tightened only by significant steps).  A fixing that
   propagates to a conflict is undone and the other value tried; a cell
   where both values conflict is deferred.  Every pure-integer row —
   exclusivity, config ⇒ unit, logic, min up/down, fixed status, `u <= 0` —
   is therefore satisfied by construction, with no element names or PAMPL
   knowledge.  Mixed rows (reserve gated by `u`, `Pmin·u <= p`) propagate
   through the continuous bounds.  Rows longer than 2000 entries (aggregate
   constraints) are not propagated, nor are columns appended to the live LP
   after the flat snapshot (e.g. future-cost / boundary-cut columns): they
   keep their bounds and are checked by the verify step.
2. **Verify.**  Fix all integers to the candidate and solve the continuous
   LP (cold barrier + crossover: with the integers fixed, presolve removes
   most of the model, while a warm dual simplex cannot presolve).  Optimal
   ⇒ the start is a feasible MIP solution; its objective is logged as the
   start's true cost (`start_obj`), next to the relaxation bound.
3. **Repair** (when the fixed LP is infeasible, up to `repair_rounds`).  An
   *elastic* LP keeps integrality relaxed and minimises `|x - candidate|`
   over the integer columns (warm primal simplex from the relaxation basis,
   which is feasible for it); the cells it moves are re-targeted in the
   direction of the move and re-dived first.  If the rounds run out, a
   bounded **sub-MIP** frees only the moved cells and the integer columns
   of the pure-integer rows the candidate still violates (so it also runs
   with `repair_rounds = 0`), plus the integer columns sharing a short row
   with them, keeps every other integer fixed, and runs
   for at most `sub_mip_time_limit` seconds.  All solves go through the
   generic `LinearInterface`, so the stage works on every MIP backend.

| `dive` field | Default | Meaning |
|--------------|---------|---------|
| `enabled` | `true` | Use the dive; `false` restores the legacy round + domain-rules generator |
| `propagate` | `true` | Bound propagation (needs the retained flat LP; otherwise round + rules) |
| `verify` | `true` | Fixed-integer LP verification + repair |
| `repair_rounds` | `3` | Elastic repair rounds (`0` disables) |
| `sub_mip` | `true` | Bounded repair sub-MIP after the elastic rounds |
| `sub_mip_time_limit` | `120` | Sub-MIP wall-clock limit [s] |
| `penalty` | unset | Elastic deviation penalty in raw objective units, added to the original objective; unset ⇒ pure feasibility elastic LP (deviation only), better conditioned and primal feasible from the relaxation basis |

The log reports the violated pure-integer rows of the plain rounding and of
the dive (grouped by `class:constraint` label), the propagation counters,
every repair round, and finally
`MIP-start[dive]: injected VERIFIED start (... start_obj=…, relax_obj=…,
gap=…%, repair_rounds=…, sub_mip=…)`.  A verified start is injected with
`effort=solve_fixed` (the backend re-derives the dispatch from the integers,
immune to tolerance noise in the continuous part).  The time spent counts
against `solver_options.time_limit` like the other MIP-start stages.

Three optional files extend the pipeline across runs and tools:

| Field | Direction | Purpose |
|-------|-----------|---------|
| `dump_file` | write | Dump this solve's integer solution after the MIP completes |
| `from_file` | read | **Replay** a previous `dump_file` instead of the round+rules candidate (enables cross-solver hand-off, e.g. HiGHS incumbent → cuOpt start) |
| `seed_solution_file` | read | External commitment **seed** CSV (`generator_uid,block_uid,u`) consumed by `SeedCommitmentRule`, which runs first in the domain pipeline; unmatched elements keep the rounded value. Any producer works: previous-day PLEXOS/gtopt, uninodal cascade, an ML predictor |

MIP starts are supported natively by CPLEX/Gurobi/HiGHS/SCIP/MindOpt;
cuOpt and CBC buffer the start and replay it at solve time.

---

## 5. Implementation

### 5.1 Architecture

```text
gtopt_main()
  |
  +-> validate_planning(planning)   ← JSON input validation
  |
  +-> PlanningLP::resolve()
        |
        +-> make_planning_method(options, num_phases)
        |     |
        |     +-> MonolithicMethod  (default, or SDDP fallback for 1 phase)
        |     +-> SDDPPlanningMethod (when method="sddp" and phases >= 2)
        |
        +-> solver->solve(planning_lp, lp_opts)
```

### 5.2 Input Validation

Before LP construction, `gtopt_main()` calls `validate_planning()` to
check the JSON input for structural correctness: missing required fields,
invalid UIDs, inconsistent array sizes, and other semantic errors.
Validation errors are reported with specific messages and the solver
exits before any LP is assembled.

### 5.3 MonolithicMethod::solve()

1. **Load boundary cuts** (if configured) --- before parallel dispatch
2. **Write LP debug files** (if `lp_debug` enabled)
3. **Parallel scene dispatch** --- each scene submitted to work pool
4. **Per-scene**: `resolve_scene_phases()` solves phases sequentially,
   propagating state variable values between phases
5. **Collect results** --- wait for all futures, report timing

### 5.4 Log Directory

By default, the `log_directory` resolves to `<output_directory>/logs`
(e.g., `output/logs`), consolidating all solver output under a single
root directory.  Both the monolithic and SDDP solvers use this directory
for error LP dumps and diagnostic files.  Set `log_directory` explicitly
in the JSON to override this default.

### 5.5 Single-Phase SDDP Fallback

When `method: "sddp"` is requested but only 1 phase exists, the
factory automatically falls back to the monolithic solver with an
informational log message.  This prevents the SDDP "requires at least
2 phases" error.

---

## 6. References

<a id="monolithic-ref1"></a>
[1] J. F. Benders, "Partitioning procedures for solving mixed-variables
programming problems," *Numerische Mathematik*, vol. 4, pp. 238--252,
1962. DOI: [10.1007/BF01386316](https://doi.org/10.1007/BF01386316)

---

## See Also

- [SDDP Method](sddp.md) --- SDDP solver documentation
  (iterative decomposition, cut persistence, hot-start)
- [Usage Guide](../usage.md) --- CLI reference and output interpretation
- [Input Data Reference](../input-data.md) --- JSON input format specification
- [Mathematical Formulation](../formulation/mathematical-formulation.md)
  --- LP/MIP formulation details
- [Planning Guide](../planning-guide.md) --- worked examples and time
  structure concepts
