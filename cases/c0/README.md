# `c0` — single-bus, multi-stage demand expansion

A one-bus case over five stages that shows the multi-stage structure
of GTOpt, inputs read from Parquet files, and expansion of a demand
with an annualized investment cost and discounting.  It is the example
used in [`docs/usage.md`](../../docs/usage.md) and
[`docs/interfaces.md`](../../docs/interfaces.md).

## Source

Written for GTOpt.  The same case as an Excel workbook for `igtopt` is
in [`scripts/cases/igtopt_c0/`](../../scripts/cases/igtopt_c0/), and as
plain JSON in [`scripts/cases/json_c0/`](../../scripts/cases/json_c0/).
`system_c0.xlsx` here is the workbook it was generated from.

## System

| Element | Data |
|---------|------|
| Time | 5 stages, one block each, of 1, 2, 3, 4 and 5 hours |
| Bus | `b1` |
| Generator | `g1`: 20 MW at 100 $/MWh |
| Demand | `d1`: no initial capacity; it can expand in modules of 20 MW (up to 10 modules) at 8760 $/MW-year. The maximum load per stage, `lmax`, is read from `system_c0/Demand/lmax.parquet` (10, 15, 8, 20 and 17 MW) |
| Options | annual discount rate 10%, input in Parquet (`input_directory: system_c0`) |

The model decides how much demand capacity to install in each stage,
trading the investment cost against the value of serving the load
(the demand-failure cost of 1000 $/MWh).

Run it from the case directory, because `input_directory` is relative.

## Running

```bash
cd cases/c0
gtopt system_c0.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **23 163.42**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_c0_'
```

This runs `e2e_c0_solve`, `e2e_c0_validate_solution` and one
`e2e_c0_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/c0/results/solution.csv cases/c0/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/c0/results/Bus/balance_dual_s0_p0.csv \
  cases/c0/output/Bus/balance_dual.csv
```
