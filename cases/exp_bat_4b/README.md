# `exp_bat_4b` — battery expansion, four blocks

[`bat_4b`](../bat_4b/) with the battery as an investment option: its
installed capacity is zero and it can be built up to 200 MWh at
1000 $/MWh-year.  The model builds the battery only to the extent that
the savings in dispatch cost pay for it.

## Source

Written for GTOpt, derived from `bat_4b`.

## System

As `bat_4b`, with `bat1` at `capacity: 0`, `expcap: 200`,
`annual_capcost: 1000`, and an annual discount rate of 6%.  See
`Battery/capainst_sol.csv` for the capacity built.

## Running

```bash
cd cases/exp_bat_4b
gtopt exp_bat_4b.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **10 358.68**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_exp_bat_4b_'
```

This runs `e2e_exp_bat_4b_solve`, `e2e_exp_bat_4b_validate_solution` and one
`e2e_exp_bat_4b_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/exp_bat_4b/results/solution.csv cases/exp_bat_4b/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/exp_bat_4b/results/Bus/balance_dual_s0_p0.csv \
  cases/exp_bat_4b/output/Bus/balance_dual.csv
```
