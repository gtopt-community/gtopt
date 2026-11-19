# `exp_gen_4b` — generation expansion on the four-bus network

The four-bus network of [`ieee_4b_ori`](../ieee_4b_ori/) with
generation expansion: the cheap generator starts with only 10 MW and
can be expanded, so the model chooses between building capacity and
dispatching the expensive unit.

## Source

Written for GTOpt, derived from `ieee_4b_ori`.

## System

| Element | Data |
|---------|------|
| Network | as `ieee_4b_ori` (4 buses, 5 lines) |
| `g1` | at `b1`, 20 $/MWh, installed 10 MW, expandable by up to 290 MW at 1000 $/MW-year |
| `g2` | at `b2`, 200 MW, 35 $/MWh, not expandable |
| Demands | `d3`: 150 MW, `d4`: 100 MW |
| Options | annual discount rate 6% |

Results to look at: `Generator/expmod_sol.csv` and
`Generator/capainst_sol.csv` (capacity built) and `capacost_cost.csv`
(investment cost in the objective).

## Running

```bash
cd cases/exp_gen_4b
gtopt exp_gen_4b.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **5028.54**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_exp_gen_4b_'
```

This runs `e2e_exp_gen_4b_solve`, `e2e_exp_gen_4b_validate_solution` and one
`e2e_exp_gen_4b_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/exp_gen_4b/results/solution.csv cases/exp_gen_4b/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/exp_gen_4b/results/Bus/balance_dual_s0_p0.csv \
  cases/exp_gen_4b/output/Bus/balance_dual.csv
```
