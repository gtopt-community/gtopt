# `c1` — `c0` without discounting

The same system and data as [`c0`](../c0/), with the annual discount
rate set to zero.  Comparing the two shows the effect of discounting on
the expansion decisions and on the objective (23 165.00 here against
23 163.42 in `c0`).

## Source

Written for GTOpt, derived from `c0`.  The input data are in
`system_c1/` (same values as `system_c0/`).

## System

See [`c0`](../c0/README.md); the only difference is
`annual_discount_rate: 0.0`.  Run it from the case directory, because
`input_directory` is relative.

## Running

```bash
cd cases/c1
gtopt system_c1.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **23 165.00**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_c1_'
```

This runs `e2e_c1_solve`, `e2e_c1_validate_solution` and one
`e2e_c1_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/c1/results/solution.csv cases/c1/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/c1/results/Bus/balance_dual_s0_p0.csv \
  cases/c1/output/Bus/balance_dual.csv
```
