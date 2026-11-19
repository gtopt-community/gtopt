# `uc_small` — small unit-commitment problem

Three thermal units on one bus over six chronological hours, with unit
commitment: when-committed minimum output, no-load and start-up costs,
minimum up and down times and initial status.  Demand rises from 20 to
120 MW and falls back, so the peaking units have to start and stop.
It is a mixed-integer problem.

## Source

Written for GTOpt.  The unit test
[`test/source/test_root_basis_cache.cpp`](../../test/source/test_root_basis_cache.cpp)
builds the same system in code.

## System

| Unit | Range | Cost | Commitment |
|------|-------|------|------------|
| `g_base` | 20–60 MW | 20 $/MWh | no-load 30 $/h, start-up 200 $, on at the start |
| `g_mid` | 30–80 MW | 40 $/MWh | no-load 40 $/h, start-up 400 $, min. up/down 2 h, off at the start |
| `g_peak` | 15–60 MW | 80 $/MWh | no-load 20 $/h, start-up 150 $ |

Demand: 20, 60, 120, 120, 60 and 20 MW.  The blocks are marked
`chronological`, so the commitment constraints link consecutive hours.

The objective is 11 060 $.  Look at `Commitment/status_sol_*.csv`,
`startup_sol_*.csv` and `shutdown_sol_*.csv` for the commitment
decisions.

The case has no reference results and is not an end-to-end test; the
unit test above checks it.

## Running

```bash
cd cases/uc_small
gtopt uc_small.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.
