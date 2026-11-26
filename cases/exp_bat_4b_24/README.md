# `exp_bat_4b_24` — generation and battery expansion over a day

[`bat_4b_24`](../bat_4b_24/) as a small expansion-planning problem:
the thermal units, the solar plant and the battery can all be expanded,
and the model co-optimizes the investments and the hourly operation of
a representative day.

## Source

Written for GTOpt, derived from `bat_4b_24`.

## System

| Element | Installed | Expandable by | Annual cost |
|---------|-----------|---------------|-------------|
| `g1` (20 $/MWh) | 500 MW | 500 MW | 78 227 $/MW-year |
| `g2` (40 $/MWh) | 300 MW | 300 MW | 46 936 $/MW-year |
| `g_solar` (0 $/MWh) | 180 MW | 180 MW | 23 468 $/MW-year |
| `bat1` | 0 MWh | 400 MWh | 7 823 $/MWh-year |

Network and demand profiles as `bat_4b_24`; annual discount rate 6%.
See `*/capainst_sol.csv` for the capacities built.

## Running

```bash
cd cases/exp_bat_4b_24
gtopt exp_bat_4b_24.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **71 347.88**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_exp_bat_4b_24_'
```

This runs `e2e_exp_bat_4b_24_solve`, `e2e_exp_bat_4b_24_validate_solution` and one
`e2e_exp_bat_4b_24_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  The tests run gtopt with
`--set solver_options.deterministic_parallel=true`: the LP is
dual-degenerate (e.g. the converter `power_envelope_dual` at hour 17 is
0 or -0.0011, both optimal), and CPLEX's opportunistic parallel mode
lands on either basis depending on machine load, which changes the
`Demand/load_cost` and `Generator/generation_cost` reduced costs.  Pass
the same flag to reproduce `output_cplex/`.  To compare a manual run by
hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/exp_bat_4b_24/results/solution.csv cases/exp_bat_4b_24/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/exp_bat_4b_24/results/Bus/balance_dual_s0_p0.csv \
  cases/exp_bat_4b_24/output/Bus/balance_dual.csv
```

The LP has more than one optimal solution, so the directories
`output_clp/`, `output_cplex/`, `output_highs/` and `output_mindopt/`
hold the results of each solver.  The tests use the one that matches
`GTOPT_SOLVER` (e.g. `GTOPT_SOLVER=highs ctest …`) and `output/`
otherwise.  The objective is the same for every solver.
