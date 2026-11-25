# Numerical-stability monitor

gtopt keeps a small set of tests whose only job is to catch regressions in
the **scaling and conditioning** of the LP formulation: a change that widens
a coefficient range, adds tiny or huge matrix entries, or makes the basis
worse conditioned fails CI (or the nightly) with a table that names the
case, the metric, the measured value, the baseline and the limit.

See also [lp-scales.md](lp-scales.md) (the scaling layers) and
[solvers.md](solvers.md) (`kappa_mode`, per-backend kappa).

## Metrics

All ranges are measured on the LP **the solver sees** (after
`scale_objective`, variable scales and row equilibration) and expressed in
**decades**: `log10(max abs(v) / min abs(v))` over the nonzero, finite
entries (magnitudes at or above `1e19` count as infinite and are
skipped).

| Metric | What | Why |
|--------|------|-----|
| `matrix_decades` | range of the constraint-matrix nonzeros | pivot tolerances, LU growth |
| `objective_decades` | range of the nonzero cost coefficients | dual feasibility tolerance |
| `rhs_decades` | range of the finite nonzero row bounds | primal feasibility tolerance |
| `bounds_decades` | range of the finite nonzero column bounds | primal feasibility tolerance |
| tiny / huge | matrix entries with `abs(a) < 1e-9` / `abs(a) > 1e9` | must be zero |
| `kappa_log10` | `log10` of the largest basis condition number over the cells | conditioning of the optimal basis |
| `kappa_growth` | SDDP: `log10(worst iteration kappa / first iteration kappa)` | cut scaling |
| `cut_decades` | SDDP: range of the nonzero saved cut coefficients | cut scaling |

Plus: every cell optimal (`solution.csv` status 0), the scaling invariant
(the default scaling never widens the matrix range compared with
`--no-scale`), and no numerical warnings in the logs (`unscaled
infeasibilities`, `numerical trouble`, `ill-posed`, `singular basis`, ...).

Kappa is always requested **exact** (`kappa_mode = exact`;
`GTOPT_KAPPA_MODE=exact` in the CTest environment): the compiled default is
`estimate` (Debug/CIFast) or `off` (Release), and the HiGHS Hager estimate
can be an order of magnitude away from the exact value.  Kappa differs per
backend, so its baselines are **per solver**: HiGHS (the CI solver) and
CPLEX (optional, when the plugin is present).  CLP is never used (the UC
case is a MIP; a MIP reports no basis kappa, so `uc_small` has only
structural checks).

## The tests

### Unit tests (`test/source/test_numerical_stability.cpp`)

Build (no solve) the LPs of six canonical cases, one per formulation
family, and check the structural metrics against per-case limits in
decades (default bounds: matrix 6, objective / RHS / bounds 8):

| case | family |
|------|--------|
| `ieee_14b` | DC OPF, Kirchhoff, binding line limits |
| `bat_4b_24` | battery |
| `sddp_hydro_3phase` | reservoir + turbine + waterway, 3 phases |
| `line_loss_envelope` | piecewise line losses |
| `uc_small` (+ ramp limits added by the test) | unit commitment (MIP) |
| `c0` | multi-stage capacity expansion, Kirchhoff |

Then solve each case on HiGHS and CPLEX and compare `log10(max kappa)`
with the committed per-solver baseline in `kKappaBaselines`: the test
fails when it exceeds the baseline by more than **1 decade**.  One
`TEST_CASE` per case, ~0.1 s each (the first one in a run also pays the
one-time solver plugin load).

```bash
./build/test/gtoptTests -tc="numerical stability*"
```

The `line_flow_abs` pin of `tangent_signed_flow` is the user option
`loss_flow_abs_cost` (default 0.001 $/MWh, 1e-6 in the LP under
`scale_objective = 1000`).  The former internal constant (1e-6 $/MWh,
1e-9 in the LP) sat below the solvers' dual tolerance and stretched the
`line_loss_envelope` objective range to 9.0 decades; with the default
it is 6.0 decades (limit 7 in the C++ test, baseline + 0.5 in
`numerical_stability_baselines.json`).

### e2e tests (CTest, label `numstab`)

`gtopt_check_output --numerics` runs gtopt twice on a case (an
`--lp-only --lp-file` pass that dumps every cell, then the real solve),
reads `solution.csv`, the logs, the LP files and the SDDP cut files, and
compares every metric with
`integration_test/numerics/numerical_stability_baselines.json`
(per case and solver).  Tolerances (decades) live in that file:
`kappa` 1.0, `kappa_growth` 1.0, `range` 0.5; an optional `limits` table
adds absolute caps.

| test | case | what it exercises |
|------|------|-------------------|
| `numstab_ieee_14b_<solver>` | ieee_14b | monolithic network |
| `numstab_sddp_hydro_3phase_<solver>` | sddp_hydro_3phase, 8 iterations | SDDP kappa growth, cut range |
| `numstab_line_loss_envelope_<solver>` | line_loss_envelope | piecewise losses |
| `numstab_cen_2y_phase1_<solver>` | CEN 2-year case, first phase, monolithic | large real LP (labels `numerics;heavy`) |

The small ones take a few seconds each and run in the default CTest set
(`ctest -L numstab`).  The CEN case is registered only with
`-DGTOPT_NUMERICS_TESTS=ON` and `GTOPT_CASES_DIR` pointing at a
local copy of the case data.  A solver without a plugin
skips (exit 77).

With `--top N` the checker also lists the matrix entries that drive the
range, grouped by (row family, column family) — the first place to look
when a range regresses.

## Updating the baselines (intentionally)

Only when a change is *meant* to move a metric (a reformulation, a new
scaling layer, a solver upgrade).  Say why in the commit message.

e2e baselines, per case and solver:

```bash
cd scripts
GTOPT_KAPPA_MODE=exact python -m gtopt_check_output --numerics \
  --case ieee_14b --solver highs \
  --baselines ../integration_test/numerics/numerical_stability_baselines.json \
  --work-dir ~/tmp/numstab/ieee_14b --gtopt ../build/standalone/gtopt \
  --case-json ../cases/ieee_14b/ieee_14b.json --update-baselines
```

(the CTest command line, printed by `ctest -V -R numstab_<case>`, plus
`--update-baselines`).  Unit-test baselines:

```bash
GTOPT_NUMSTAB_PRINT=1 ./build/test/gtoptTests -tc="numerical stability*" -s
```

prints every measured value and the ready-to-paste `kKappaBaselines`
lines.
