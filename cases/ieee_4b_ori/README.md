# `ieee_4b_ori` — four-bus DC optimal power flow

The simplest network case: four buses in a meshed network with DC power
flow (Kirchhoff's voltage law), two generators and two loads, one hour.
It is the base network of the battery and expansion cases (`bat_4b*`,
`exp_*_4b*`).

## Source

Based on the four-bus system of Grainger and Stevenson (*Power System
Analysis*), as a lossless DC network with linear costs (the `_ori`
suffix marks the original single-period version).  The same network is
built in pandapower by `scripts/gtopt_compare` for the cross-check.

## System

| Element | Data |
|---------|------|
| Buses | `b1`–`b4` |
| Lines | 5 lines, reactance 0.02 p.u., limits 150–300 MW |
| Generators | `g1` at `b1`: 300 MW, 20 $/MWh; `g2` at `b2`: 200 MW, 35 $/MWh |
| Demands | `d3` at `b3`: 150 MW; `d4` at `b4`: 100 MW |

No line is congested, so `g1` serves all 250 MW (5000 $) and all buses
have the same marginal cost, 20 $/MWh.

## Running

```bash
cd cases/ieee_4b_ori
gtopt ieee_4b_ori.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **5000**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_ieee_4b_ori_'
```

This runs `e2e_ieee_4b_ori_solve`, `e2e_ieee_4b_ori_validate_solution` and one
`e2e_ieee_4b_ori_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/ieee_4b_ori/results/solution.csv cases/ieee_4b_ori/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/ieee_4b_ori/results/Bus/balance_dual_s0_p0.csv \
  cases/ieee_4b_ori/output/Bus/balance_dual.csv
```

The case is also compared against pandapower's DC OPF
(`e2e_ieee_4b_ori_gtopt_compare`, registered when the `gtopt_compare`
script and pandapower are available: `pip install -e scripts`).  By
hand:

```bash
gtopt_compare --case ieee_4b_ori --gtopt-output cases/ieee_4b_ori/results
```
