# `ieee_9b_ori` — WSCC nine-bus system

The classic nine-bus, three-machine system, one hour, solved as a DC
optimal power flow.

## Source

WSCC 9-bus system (P. M. Anderson and A. A. Fouad, *Power System
Control and Stability*), as distributed with MATPOWER (`case9`).  The
topology, reactances and loads follow the original; the generator costs
are linear (20, 35 and 30 $/MWh) and the line limits are set to
250–300 MW for this case.

## System

| Element | Data |
|---------|------|
| Buses | `b1`–`b9` |
| Lines | 9 |
| Generators | `g1` at `b1`: 10–250 MW, 20 $/MWh; `g2` at `b2`: 10–300 MW, 35 $/MWh; `g3` at `b3`: 0–270 MW, 30 $/MWh |
| Demands | 125 MW at `b5`, 100 MW at `b7`, 90 MW at `b9` |

One line limit binds, so the marginal costs are 20 $/MWh at some
buses and 30 $/MWh at others.

## Running

```bash
cd cases/ieee_9b_ori
gtopt ieee_9b_ori.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **7000**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_ieee_9b_ori_'
```

This runs `e2e_ieee_9b_ori_solve`, `e2e_ieee_9b_ori_validate_solution` and one
`e2e_ieee_9b_ori_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/ieee_9b_ori/results/solution.csv cases/ieee_9b_ori/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/ieee_9b_ori/results/Bus/balance_dual_s0_p0.csv \
  cases/ieee_9b_ori/output/Bus/balance_dual.csv
```
