# `ieee_14b_ori` — IEEE 14-bus system

The IEEE 14-bus test system, one hour, solved as a DC optimal power
flow with line limits that bind, so that the marginal costs differ
between buses.

## Source

IEEE 14-bus test case (University of Washington Power Systems Test Case
Archive, 1962 AEP system), as distributed with MATPOWER (`case14`).
Topology, reactances and loads follow the original; the generator costs
are linear (20–45 $/MWh) and the line limits (20–150 MW) were chosen
for this case so that congestion appears.

## System

| Element | Data |
|---------|------|
| Buses | `b1`–`b14` |
| Lines | 20 (lines and transformers) |
| Generators | at buses 1, 2, 3, 6 and 8: 260, 130, 130, 100 and 80 MW, 20, 35, 30, 40 and 45 $/MWh |
| Demands | 11 loads, 259 MW in total |

The marginal costs range from 20 to 31 $/MWh across the buses.

## Running

```bash
cd cases/ieee_14b_ori
gtopt ieee_14b_ori.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **5600.63**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_ieee_14b_ori_'
```

This runs `e2e_ieee_14b_ori_solve`, `e2e_ieee_14b_ori_validate_solution` and one
`e2e_ieee_14b_ori_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/ieee_14b_ori/results/solution.csv cases/ieee_14b_ori/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/ieee_14b_ori/results/Bus/balance_dual_s0_p0.csv \
  cases/ieee_14b_ori/output/Bus/balance_dual.csv
```
