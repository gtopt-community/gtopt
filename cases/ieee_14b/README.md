# `ieee_14b` — IEEE 14-bus system with solar over 24 hours

The 14-bus network of [`ieee_14b_ori`](../ieee_14b_ori/) over 24 hourly
blocks, with the generator at bus 3 replaced by a zero-cost solar plant
and daily demand curves.  With the tight line limits the Kirchhoff
constraints bind in several hours, and the marginal costs differ
between buses and hours: a compact case for studying congestion.

## Source

Written for GTOpt, derived from the IEEE 14-bus system (see
`ieee_14b_ori`).

## System

| Element | Data |
|---------|------|
| Network | as `ieee_14b_ori` |
| Generators | `g1`, `g2`, `g6`, `g8` as in `ieee_14b_ori`; `g3`: 130 MW solar, zero cost, profile peaking at noon |
| Demands | 24-hour profiles at the 11 load buses |

In the evening peak (hours 21 and 22) the network cannot serve all
the load at one bus, whose marginal cost reaches the demand-failure
cost of 1000 $/MWh; see `Demand/fail_sol.csv`.

## Running

```bash
cd cases/ieee_14b
gtopt ieee_14b.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **201 124.40**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_ieee_14b_'
```

This runs `e2e_ieee_14b_solve`, `e2e_ieee_14b_validate_solution` and one
`e2e_ieee_14b_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/ieee_14b/results/solution.csv cases/ieee_14b/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/ieee_14b/results/Bus/balance_dual_s0_p0.csv \
  cases/ieee_14b/output/Bus/balance_dual.csv
```

The LP has more than one optimal solution, so the directories
`output_clp/`, `output_cplex/`, `output_highs/` and `output_mindopt/`
hold the results of each solver.  The tests use the one that matches
`GTOPT_SOLVER` (e.g. `GTOPT_SOLVER=highs ctest …`) and `output/`
otherwise.  The objective is the same for every solver.
