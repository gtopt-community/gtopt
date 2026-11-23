# `bat_4b_24` — daily battery cycle on the four-bus network

[`bat_4b`](../bat_4b/) over 24 hourly blocks, with a solar profile and
a daily demand curve: a full charge–discharge cycle of the battery.
The figures of
[`docs/storage-and-water-values.md`](../../docs/storage-and-water-values.md)
come from a variant of this case.

## Source

Written for GTOpt.  The same case as a PLP input is in
[`scripts/cases/plp_bat_4b_24/`](../../scripts/cases/plp_bat_4b_24/),
with PLP's own results, for checking `plp2gtopt`; as an Excel workbook
for `igtopt` it is in
[`scripts/cases/igtopt_bat4b24/`](../../scripts/cases/igtopt_bat4b24/).

## System

| Element | Data |
|---------|------|
| Network | as `ieee_4b_ori` (4 buses, 5 lines) |
| Generators | `g1`: 250 MW, 20 $/MWh; `g2`: 150 MW, 40 $/MWh; `g_solar`: 90 MW, zero cost, hourly profile peaking at noon |
| Battery | `bat1` at `b3`: 200 MWh, 60 MW, 95% efficiency each way, self-discharge `annual_standing_loss` 0.48 p.u./year (4 %/month) |
| Demands | `d3` and `d4`, 24-hour profiles with a morning and an evening peak |

The battery's energy dual (`Battery/energy_dual.csv`) is the
opportunity cost of stored energy, which sets the prices at which it
charges and discharges.

## Running

```bash
cd cases/bat_4b_24
gtopt bat_4b_24.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **42 054**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_bat_4b_24_'
```

This runs `e2e_bat_4b_24_solve`, `e2e_bat_4b_24_validate_solution` and one
`e2e_bat_4b_24_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/bat_4b_24/results/solution.csv cases/bat_4b_24/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/bat_4b_24/results/Bus/balance_dual_s0_p0.csv \
  cases/bat_4b_24/output/Bus/balance_dual.csv
```

The LP has more than one optimal solution, so the directories
`output_clp/`, `output_cplex/`, `output_highs/` and `output_mindopt/`
hold the results of each solver.  The tests use the one that matches
`GTOPT_SOLVER` (e.g. `GTOPT_SOLVER=highs ctest …`) and `output/`
otherwise.  The objective is the same for every solver.

The case is also compared against pandapower's DC OPF
(`e2e_bat_4b_24_gtopt_compare`, registered when the `gtopt_compare`
script and pandapower are available: `pip install -e scripts`).  By
hand:

```bash
gtopt_compare --case bat_4b_24 --gtopt-output cases/bat_4b_24/results
```
