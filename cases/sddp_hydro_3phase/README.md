# `sddp_hydro_3phase` — SDDP regression case

One reservoir, a hydro and a thermal unit, over three phases of 24
hourly blocks, solved with SDDP (`method: sddp`).  The problem is small
enough that SDDP converges in a few iterations, so it is the
regression case for the SDDP implementation: forward and backward
passes, cut construction, the low-memory modes and hot start from a
saved cut file.

## Source

Written for GTOpt.

## System

| Element | Data |
|---------|------|
| Time | 3 phases (one stage each) × 24 one-hour blocks |
| Bus | single bus |
| Reservoir | `rsv1`: volume 0–150, starts at 100, constant inflow of 10 per hour (flow conversion rate 1) |
| Hydro | `hydro_gen`: 25 MW, 5 $/MWh, through turbine `tur1` (production factor 1) |
| Thermal | `thermal_gen`: 500 MW, 50 $/MWh |
| Demand | `load1`: 100 MW |

## Running

```bash
cd cases/sddp_hydro_3phase
gtopt sddp_hydro_3phase.json --set output_directory=results \
  --set sddp_options.max_iterations=50 --set output_format=parquet
```

The objective is in `results/solution.csv`, and the SDDP convergence
(bounds per iteration) in the log.  The tests write Parquet, which is
what the validator below reads.

Add `--low-memory compress` or `--low-memory rebuild` to exercise the
low-memory modes.  The converged cuts are saved in
`results/cuts/sddp_cuts.parquet`; a pure simulation from them is

```bash
gtopt sddp_hydro_3phase.json --set output_directory=results_sim \
  --set sddp_options.max_iterations=0 --set output_format=parquet \
  --set sddp_options.cuts_input_file=results/cuts/sddp_cuts.parquet \
  --set sddp_options.recovery_mode=cuts --recover
```

## Checking against the reference

The `golden_iter*.json` files pin the objective, the per-phase values
and samples of the solution (reservoir volume, generation, marginal
cost):

| File | Run | Objective |
|------|-----|-----------|
| `golden_iter0.json` | no training (simulation only): the reservoir is drained | 104 700 |
| `golden_iter3.json` | 3 iterations (already converged) | 323 100 |
| `golden_iter50.json` | 50 iterations, upper bound = lower bound | 323 100 |
| `golden_iter50_hotstart.json` | simulation from the cuts saved by a 50-iteration run | 323 100 |

The `_cbc`, `_clp`, `_cplex` and `_highs` variants hold the
solver-specific samples where the solution is not unique.

```bash
ctest --test-dir build -L sddp          # all SDDP regression tests
ctest --test-dir build -R sddp_hydro_3phase_iter50
```

The tests (`e2e_sddp_hydro_3phase_*_sddp_solve` and `_sddp_validate`)
cover 0, 3 and 50 iterations with the three low-memory modes (`off`,
`compress`, `rebuild`) and the save/load hot start.  To validate a
manual run:

```bash
python3 tools/validate_sddp_output.py \
  --output-dir cases/sddp_hydro_3phase/results \
  --input-json cases/sddp_hydro_3phase/sddp_hydro_3phase.json \
  --max-iterations 50 \
  --golden-json cases/sddp_hydro_3phase/golden_iter50.json
```
