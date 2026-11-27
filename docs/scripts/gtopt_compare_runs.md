# gtopt_compare_runs — Compare Two gtopt Runs

Compares two gtopt **output directories** (run A vs run B): the
objective, every `<Class>/<field>` stream, generation by technology and
reservoir volumes.  It reports totals, the largest differences, the
elements that differ most, and the elements found in only one run.

PLP, PSR SDDP and PLEXOS solutions can be imported into gtopt's output
layout with `plp2gtopt`, `sddp2gtopt` and `plexos2gtopt --results`
([plp2gtopt](plp2gtopt.md#importing-plp-results---results),
[sddp2gtopt](sddp2gtopt.md#importing-psr-sddp-results---results),
[plexos2gtopt](plexos2gtopt.md#importing-plexos-results---results)).  After
that, checking gtopt against one of those tools is the same as comparing
two gtopt runs:

```bash
plp2gtopt -i plp_case -o case --results          # case/ + case/results_plp/
gtopt case/case.json                             # case/results/
gtopt_compare_runs case/results_plp case/results --label-a PLP --label-b gtopt
```

This tool is separate from [`gtopt_compare`](gtopt_compare.md), which
validates gtopt against pandapower.

---

## Usage

```bash
gtopt_compare_runs RUN_A RUN_B [options]

gtopt_compare_runs base/results new/results                  # two gtopt runs
gtopt_compare_runs a/results b/results --rel-tol 0.01 --abs-tol 0.1
gtopt_compare_runs a/results b/results --streams Generator,Bus/balance_dual
gtopt_compare_runs a/results b/results --csv diffs/ --json diffs/report.json
gtopt_compare_runs a/results b/results --plot plots/         # needs matplotlib
```

| Option | Effect |
|--------|--------|
| `RUN_A`, `RUN_B` | Output directories to compare |
| `--planning-a JSON`, `--planning-b JSON` | Planning that names the elements of A / B (default: see [Planning lookup](#planning-lookup)) |
| `--label-a`, `--label-b` | Names of the runs in the report (default `A`, `B`) |
| `--streams LIST` | Comma-separated `Class` or `Class/field` filters (default: every stream except `Commitment`, `Converter` and `UserConstraint`) |
| `--abs-tol X`, `--rel-tol X` | Per-cell tolerance (default `1e-3` each, see [Tolerances](#tolerances-and-exit-code)) |
| `--top N` | Elements listed per stream (default 10) |
| `--all-streams` | List the top elements of streams that pass too (by default only failing streams are listed) |
| `--csv DIR` | Write `streams.csv`, `elements.csv`, `technology.csv`, `reservoirs.csv` |
| `--json PATH` | Write the whole report as JSON |
| `--plot DIR` | Write `streams.png` and `technology.png` (bar charts); skipped with a warning when matplotlib is not installed |
| `--width N` | Console width |
| `-v, --verbose` | Debug log |

Exit code: **0** when every compared stream is within tolerance, **1**
when some stream is beyond it, **2** on bad input (a directory is
missing, or there is nothing to compare).

---

## What is compared

### Alignment

* **Time** is aligned by **position**: scenario, stage and block are
  matched by their index in each run's planning, not by uid.  So a case
  converted by two different tools lines up even when the uids differ.
  Per-stage streams (written with `block = 0`, such as
  `Reservoir/efin_sol`) are aligned by stage.
* **Elements** are matched by name.  An exact match is tried first.
  Otherwise the names are normalised, and a pair matches only when the
  normalised key is unique on both sides.  Normalising does three things:
  * folds case and blanks;
  * strips one converter prefix: `d_`, `dem_`, `load_`, `r_`, `rs_`,
    `tb_`, `turbine_`, `j_`, `in_`, `inflow_`, `s_` or `sp_`;
  * strips a trailing `_<code>` (for example `RAPEL_12`).

  Elements left without a match are listed as **only in A** / **only in
  B**.  Being in one run only does not, by itself, fail the comparison.
  An example: the internal generator/demand pair that gtopt creates for
  each battery is often missing from the other run.

### Per stream

Each stream is classified as **`sum`** (flows of energy or water, such
as `generation_sol` or `flow_sol`) or **`mean`** (duals and prices,
`*_dual`; and states such as `energy_sol`, `efin_sol`, `eini_sol`,
`theta_sol`).  The **aggregate** of one element is:

* `sum`: Σ value × block hours × scenario probability.  MW becomes
  expected MWh; m³/s becomes expected m³/s·h.
* `mean`: the same sum divided by the horizon hours, giving the
  time-weighted expected mean.

| Column | Meaning |
|--------|---------|
| `n A/B` | Elements with data in A / B |
| `only A/B` | Elements without a counterpart |
| `total A`, `total B`, `B - A` | Sum of the per-element aggregates |
| `max \|Δ\|`, `max rel` | Largest absolute / relative difference of a single cell |
| `fail` | Cells beyond tolerance (`ok` when none) |

For every failing stream (every stream with `--all-streams`), the
**top-N elements** are listed by the absolute difference of their
aggregates.  The table shows both names, both aggregates, the absolute
and relative difference, and the largest single-cell difference.

### Objective, technology, reservoirs

* **Objective**: the expected total cost from each run's
  `solution.csv`, which is the sum of `obj_value` over the rows of the
  first phase.  In an SDDP run each phase's value already includes the
  cost-to-go of the later phases.
* **Generation by technology**: expected MWh of
  `Generator/generation_sol` grouped by the generators' `type`
  (`other` when the type is not set).
* **Reservoir volumes**: the expected end-of-stage volume
  (`Reservoir/efin_sol`) per reservoir and stage, for each run.

### Tolerances and exit code

A cell passes when `|a - b| <= abs_tol + rel_tol * max(|a|, |b|)`.  A
cell missing on one side counts as 0, because gtopt does not write exact
zeros of `_sol` streams.  Pick the tolerances from the precision of the
source.  For example, PLP output files have two decimals and PSR SDDP
`.csv` files have three significant digits.  Use `--streams` to gate on
the streams that matter, and leave out duals of degenerate problems.

---

## Planning lookup

The planning supplies the element names and the time grid (block
durations, stages, scenario probabilities).  For each run, the tool uses
the first of these that exists:

1. `--planning-a` / `--planning-b`;
2. `<run>/planning.json`, which gtopt and the results importers write
   into the output directory;
3. the case JSON in the parent directory (same lookup as
   `find_planning_json`).

When no planning is found, elements are named `uid:<N>`.  The grid is
then rebuilt from the data: one-hour blocks and equally likely
scenarios.  Two such runs still line up position by position.

Both the Parquet and CSV output layouts are read, including Hive
`scene=/phase=` partitions and `_s<S>_p<P>.csv[.zst|.gz]` shards.

---

## Outputs

* **Console**: Rich tables, ending with a one-line `PASS` / `FAIL`
  verdict.
* **`--csv DIR`**:
  * `streams.csv`: one row per stream with the summary columns;
  * `elements.csv`: one row per matched element and stream, with
    `name_a`, `name_b`, `agg_a`, `agg_b`, `diff`, `abs_diff`,
    `rel_diff`, `max_abs_cell` and `n_fail`;
  * `technology.csv`;
  * `reservoirs.csv`.
* **`--json PATH`**: the same summary as one JSON object (`run_a`,
  `run_b`, tolerances, objectives, `passed`, `streams`, `technology`,
  `reservoirs`).

---

## Python API

```python
from gtopt_compare_runs import CompareOptions, Run, compare_runs
from gtopt_compare_runs.engine import element_aggregates
from gtopt_compare_runs.report import render, write_csv

a = Run.load("case/results_plp", label="PLP")
b = Run.load("case/results", label="gtopt")
report = compare_runs(a, b, CompareOptions(rel_tol=1e-2, streams=("Generator",)))
render(report)
report.stream("Generator/generation_sol").elements.head()
element_aggregates(b, "Reservoir", "water_value_dual")   # {name: mean}
```

`plexos2gtopt.compare_with_plexos` uses this core.  Its gtopt-side
battery and water-value tables are built with `element_aggregates`.
Its `--run-diff` / `--run-diff-dir DIR` flags import the PLEXOS solution
and print this report (see
[plexos2gtopt](plexos2gtopt.md#step-3-compare_with_plexos-comparison-surface)).

---

## See also

* [plp2gtopt](plp2gtopt.md), [sddp2gtopt](sddp2gtopt.md),
  [plexos2gtopt](plexos2gtopt.md): `--results` import of external
  solutions.
* [gtopt2plp](gtopt2plp.md), [gtopt2sddp](gtopt2sddp.md),
  [gtopt2plexos](gtopt2plexos.md): the inverse exporters.
* [gtopt_compare](gtopt_compare.md): validation against pandapower.
