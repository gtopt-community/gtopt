# `s1b` — single-bus economic dispatch

The smallest GTOpt case: one bus, two thermal generators and one
demand, one block of one hour.  It is the first example to read when
learning the JSON format, and a check that the merit order is
respected.

## Source

Written for GTOpt.  The equivalent pandapower network is built by
`scripts/gtopt_compare` for the cross-check.

## System

| Element | Data |
|---------|------|
| Bus | `b1` (single-bus mode, `use_single_bus: true`) |
| Generators | `g1`: 200 MW at 20 $/MWh; `g2`: 300 MW at 40 $/MWh |
| Demand | `d1`: 250 MW |

The cheap unit runs at its limit (200 MW) and the expensive one covers
the remaining 50 MW, so the cost is 200·20 + 50·40 = 6000 $ and the
marginal cost is 40 $/MWh.

## Running

```bash
cd cases/s1b
gtopt s1b.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **6000**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_s1b_'
```

This runs `e2e_s1b_solve`, `e2e_s1b_validate_solution` and one
`e2e_s1b_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/s1b/results/solution.csv cases/s1b/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/s1b/results/Bus/balance_dual_s0_p0.csv \
  cases/s1b/output/Bus/balance_dual.csv
```

The case is also compared against pandapower's DC OPF
(`e2e_s1b_gtopt_compare`, registered when the `gtopt_compare`
script and pandapower are available: `pip install -e scripts`).  By
hand:

```bash
gtopt_compare --case s1b --gtopt-output cases/s1b/results
```
