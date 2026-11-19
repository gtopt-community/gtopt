# `bat_4b` — battery on the four-bus network

The four-bus network with a solar plant and a battery over four
one-hour blocks.  It shows how a battery shifts cheap energy from one
block to another, and how the storage balance couples the blocks.

## Source

Written for GTOpt, derived from `ieee_4b_ori`.  The 24-hour version is
[`bat_4b_24`](../bat_4b_24/).

## System

| Element | Data |
|---------|------|
| Network | as `ieee_4b_ori` (4 buses, 5 lines) |
| Generators | `g1`: 250 MW, 20 $/MWh; `g2`: 150 MW, 40 $/MWh; `g_solar` at `b1`: 90 MW, zero cost, profile 0, 0.67, 1, 0.22 |
| Battery | `bat1` at `b3`: 200 MWh, 60 MW charge and discharge, 95% efficiency each way, starts empty |
| Demands | `d3` at `b3`: 120, 60, 30, 200 MW; `d4` at `b4`: 80, 40, 20, 130 MW |

The battery charges in the low-demand, sunny blocks and discharges in
the last block, where demand peaks and `g2` would otherwise set the
price.  Look at `Battery/energy_sol.csv`, `finp_sol.csv` (charge) and
`fout_sol.csv` (discharge), and at `Bus/balance_dual.csv` for the
marginal costs.

## Running

```bash
cd cases/bat_4b
gtopt bat_4b.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **10 329.84**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_bat_4b_'
```

This runs `e2e_bat_4b_solve`, `e2e_bat_4b_validate_solution` and one
`e2e_bat_4b_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/bat_4b/results/solution.csv cases/bat_4b/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/bat_4b/results/Bus/balance_dual_s0_p0.csv \
  cases/bat_4b/output/Bus/balance_dual.csv
```

The LP has more than one optimal solution, so the directories
`output_clp/`, `output_cplex/`, `output_highs/` and `output_mindopt/`
hold the results of each solver.  The tests use the one that matches
`GTOPT_SOLVER` (e.g. `GTOPT_SOLVER=highs ctest …`) and `output/`
otherwise.  The objective is the same for every solver.
