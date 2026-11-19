# `ieee30b` — IEEE 30-bus system

The IEEE 30-bus test system, one hour, solved as a DC optimal power
flow and checked against pandapower.

## Source

IEEE 30-bus test case, taken from pandapower (`case_ieee30`) with
linear generator costs.  The same network is rebuilt by
`scripts/gtopt_compare` for the cross-check, and the expected
`pp2gtopt` conversion is in
[`scripts/pp2gtopt/tests/fixtures/`](../../scripts/pp2gtopt/tests/fixtures/).

## System

| Element | Data |
|---------|------|
| Buses | 30 (numeric names) |
| Lines | 41, without binding limits |
| Generators | 6 |
| Demands | 21 |

## Running

```bash
cd cases/ieee30b
gtopt ieee30b.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **5668**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_ieee30b_'
```

This runs `e2e_ieee30b_solve`, `e2e_ieee30b_validate_solution` and one
`e2e_ieee30b_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/ieee30b/results/solution.csv cases/ieee30b/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/ieee30b/results/Bus/balance_dual_s0_p0.csv \
  cases/ieee30b/output/Bus/balance_dual.csv
```

The LP has more than one optimal solution, so the directories
`output_clp/`, `output_cplex/`, `output_highs/` and `output_mindopt/`
hold the results of each solver.  The tests use the one that matches
`GTOPT_SOLVER` (e.g. `GTOPT_SOLVER=highs ctest …`) and `output/`
otherwise.  The objective is the same for every solver.

The case is also compared against pandapower's DC OPF
(`e2e_ieee30b_gtopt_compare`, registered when the `gtopt_compare`
script and pandapower are available: `pip install -e scripts`).  By
hand:

```bash
gtopt_compare --case ieee30b --gtopt-output cases/ieee30b/results
```
