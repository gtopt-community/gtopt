# Slow test: PCP 2026-11-07 day 1, exogenous native reserves

`slow_pcp_2026_11_07_d1_exo` (label `slow`, registered only with
`-DGTOPT_SLOW_TESTS=ON` and a `GTOPT_CASES_DIR` that holds the case) runs
gtopt on the CEN PCP of 2026-11-07 cut to its first day, with the native
reserve model in exogenous mode (exogenous load term and exogenous
inertia term, `system_inertia` from the must-run units), and checks
(`tools/check_pcp_reserve_day1.py`) over the 24 hours of day 1:

- mean CMg at `Polpaico220` >= 0 (PLEXOS: -0.363 $/MWh; gtopt endogenous:
  -0.341; gtopt exogenous: +0.061);
- mean CPF@10s requirement within 5 MW of PLEXOS' 194.9125 MW (gtopt
  exogenous: 196.53).

Peak memory: about 1.2 GB RSS.

## Where the test looks for the case

`$GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-11-07_d1_exo/case/`
holding `pcp_2026-11-07.json` and the files written next to it by the
converter (`uc_*.pampl`, `solvers/`, ...).  The case is **not yet** in
the case repository (`tools/cases.lock`): the test is not registered
until it is added there.

## How to produce the case

1. PLEXOS bundle of the PCP of 2026-11-07 (`DATOS20261107.zip`, and
   `RES20261107.zip` for the PLEXOS reference values) in
   `bundles/pcp_2026-11-07/`.
2. Convert with `plexos2gtopt` (native reserves, exogenous load
   term by default), one day, hourly, LP relaxation.  The converter's
   inertia term now defaults to `commitment`; pass
   `--reserve-inertia-term exogenous` to rebuild the case the thresholds
   were taken from:

   ```bash
   python3 -m plexos2gtopt -i bundles/pcp_2026-11-07 -o pcp_2026-11-07_d1_exo/case \
     --no-check --write-out all --plexos-legacy \
     --line-losses-mode tangent_signed_flow --loss-cost-eps 1.0 \
     --horizon-mode hourly --horizon-days 1 --lp-relax \
     --reserve-inertia-term exogenous
   ```

3. Day-1 hydro post-processing: the 1-day horizon would otherwise drain
   the reservoirs against the 7-day boundary cut.  Pin every
   `Reservoir.efin` to PLEXOS' End Volume at hour 24 (from the PLEXOS
   solution, `Storage` class, day 1) and drop `future_cost_array` and
   `boundary_cuts.csv`.  The post-processing script (`day1_hydro.py
   CASE_DIR STORAGE_DAY1.csv`, input columns `object`, `hour`,
   `end_volume`) belongs next to the case in the case repository, under
   `$GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-11-07_d1_exo/`,
   together with the README of the reference run the thresholds come
   from.
4. Commit `case/` (+ a README with the steps above) under
   `chile/daily_scheduling/pcp_2026-11-07_d1_exo/` in the case repository and
   bump `tools/cases.lock`.

## Run by hand

```bash
python3 tools/check_pcp_reserve_day1.py --gtopt build/standalone/gtopt \
  --case-dir <case> --work-dir <work-dir> [--solver cplex]
# or only check an existing output/ directory:
python3 tools/check_pcp_reserve_day1.py --case-dir <case> --work-dir <run> --check-only
```
