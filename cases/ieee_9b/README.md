# `ieee_9b` — nine-bus system with solar over 24 hours

The nine-bus network of [`ieee_9b_ori`](../ieee_9b_ori/) over 24
hourly blocks, with the third machine replaced by a zero-cost solar
plant with an hourly profile, and daily demand curves.  It is the case
shown in the screenshots of the GUI
([`docs/interfaces.md`](../../docs/interfaces.md)).

## Source

Written for GTOpt, derived from the WSCC 9-bus system (see
`ieee_9b_ori`).

## System

| Element | Data |
|---------|------|
| Network | as `ieee_9b_ori` |
| Generators | `g1`: 10–250 MW, 20 $/MWh; `g2`: 10–300 MW, 35 $/MWh; `g3`: 270 MW solar, zero cost, profile peaking at noon |
| Demands | 24-hour profiles at `b5`, `b7` and `b9` |

Results to look at: the hourly dispatch (`Generator/generation_sol.csv`),
the solar spillage (`GeneratorProfile/spillover_sol.csv`) and the
marginal costs (`Bus/balance_dual.csv`).

## Running

```bash
cd cases/ieee_9b
gtopt ieee_9b.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **794 196**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_ieee_9b_'
```

This runs `e2e_ieee_9b_solve`, `e2e_ieee_9b_validate_solution` and one
`e2e_ieee_9b_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/ieee_9b/results/solution.csv cases/ieee_9b/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/ieee_9b/results/Bus/balance_dual_s0_p0.csv \
  cases/ieee_9b/output/Bus/balance_dual.csv
```
