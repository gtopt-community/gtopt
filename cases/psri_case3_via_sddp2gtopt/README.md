# `psri_case3_via_sddp2gtopt` — multi-system PSR SDDP fixture (case3 variant)

End-to-end integration fixture exercising the **multi-system**
converter path through `scripts/sddp2gtopt/` on the upstream PSR
sample case3 — a variant of `psri_case2` with a smaller `Vazao`
(inflow) series.

## Source

`scripts/sddp2gtopt/tests/data/psri_case3/psrclasses.json` — vendored
from
[`psrenergy/PSRClassesInterface.jl`](https://github.com/psrenergy/PSRClassesInterface.jl)
under MPL-2.0 (see `scripts/sddp2gtopt/tests/data/LICENSE-PSRClassesInterface`).

Composition is structurally identical to `psri_case2`:

* 2 `PSRSystem` (one populated, one empty).
* 3 thermal + 2 hydro plants on system 1.
* 1 demand on system 1.

The only difference upstream is the size of the `PSRGaugingStation.Vazao`
historical series — v0 of the converter does not yet consume the
inflow series, so the produced gtopt JSON is byte-identical to
`psri_case2_via_sddp2gtopt`.  We still vendor it as a separate
e2e case so a future converter that *does* consume inflow series
(planned for v2 hydro) immediately produces a distinguishable
fixture and we can pin the new behaviour independently of case2.

## Conversion path

```
scripts/sddp2gtopt/tests/data/psri_case3/psrclasses.json
    │
    │  python -m sddp2gtopt --input-dir … --output-file system_psri_case3_via_sddp2gtopt.json
    ▼
cases/psri_case3_via_sddp2gtopt/system_psri_case3_via_sddp2gtopt.json
```

## Expected results (analytical)

Same reasoning as `psri_case2_via_sddp2gtopt` — all thermals fall
back to `gcost = 0.0` (no PSR cost segments) and hydros are zero-
cost in v0, so the optimal dispatch cost is **exactly 0** for the
populated system; the empty system is trivially feasible.

## CTest coverage

`add_e2e_case(psri_case3_via_sddp2gtopt, system_psri_case3_via_sddp2gtopt.json)`
— same trio (solve / validate_solution / compare_solution) as
`psri_case2_via_sddp2gtopt`.

## Cross-tool validation status

Identical to `psri_case2_via_sddp2gtopt` — `gtopt2pp(case) → pp.rundcopp`
agrees with gtopt at `obj = 0`, but only because the v0 converter
drops `PSRFuel.Custo`.  See that fixture's README section for the
detailed reasoning.  When fuel costs land in a future converter
release, regenerate the golden here as well.

## Running

```bash
cd cases/psri_case3_via_sddp2gtopt
gtopt system_psri_case3_via_sddp2gtopt.json --output-directory results --write-out all
```

Always pass `--output-directory` (or `--set output_directory=…`): by
default GTOpt writes to `output/`, which here holds the reference
results.  `--write-out all` writes every result (costs and duals
included), as the tests do; without it GTOpt writes the main ones.
The objective value is in `results/solution.csv` (`obj_value`), and the
per-element results in `results/<Element>/<field>_s0_p0.csv` (scene 0,
phase 0), which correspond to `output/<Element>/<field>.csv`.

## Checking against the reference

Expected objective: **0**.

The case is an end-to-end test.  With a build configured with
`-DGTOPT_BUILD_INTEGRATION_TESTS=ON` (see [`../README.md`](../README.md)):

```bash
ctest --test-dir build -R '^e2e_psri_case3_via_sddp2gtopt_'
```

This runs `e2e_psri_case3_via_sddp2gtopt_solve`, `e2e_psri_case3_via_sddp2gtopt_validate_solution` and one
`e2e_psri_case3_via_sddp2gtopt_compare_<Element>_<file>` test per reference CSV in
`output/` (tolerance 1e-6).  To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/psri_case3_via_sddp2gtopt/results/solution.csv cases/psri_case3_via_sddp2gtopt/output/solution.csv
python3 tools/gtopt_compare_csv.py \
  cases/psri_case3_via_sddp2gtopt/results/Bus/balance_dual_s0_p0.csv \
  cases/psri_case3_via_sddp2gtopt/output/Bus/balance_dual.csv
```
