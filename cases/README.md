# Sample and test cases

This directory holds small, public cases that show how to write a GTOpt
model and that the test suite solves on every change.  Each case is a
directory with a system JSON file (and, for some, an input-data
directory in Parquet), plus an `output/` directory with the reference
results that the end-to-end tests compare against.  The `output_<solver>/`
directories hold the same results computed with each LP solver plugin.

Every case directory has its own `README.md` with what the case is,
its source, the data, the expected results and how to run and check it.

## Running a case

Most cases run directly:

```bash
cd cases/ieee_9b
gtopt ieee_9b.json --output-directory results --write-out all
```

* Always pass `--output-directory`: the default is `output/`, which
  holds the reference results.
* `--write-out all` writes every result (costs and duals included), as
  the tests do.
* Run from the case directory: some cases (`c0`, `c1`) read their input
  files from a relative `input_directory`.

The objective value is in `results/solution.csv`, and the results of
each element in `results/<Element>/<field>_s0_p0.csv`; they correspond
to `output/<Element>/<field>.csv`.

Cases that need special instructions:

| Case | What is different |
|------|-------------------|
| `sddp_hydro_3phase` | SDDP: set the iterations and Parquet output, and check with `tools/validate_sddp_output.py` against the `golden_iter*.json` files |
| `ieee_24b_rts` | Optional PAMPL operating limits (`uc_opf_limits.pampl`), attached through the JSON |
| `uc_small`, `ieee_118b` | No reference results; they are run by hand (`uc_small` is also checked by a unit test) |
| `case3375wp` | No golden file: the test checks only that it solves and that the solution is consistent |

## Checking the results: the test suite

All the cases with reference results are end-to-end tests.  Configure
the build with the integration tests and run them with CTest (see
[`BUILDING.md`](../BUILDING.md) for the dependencies):

```bash
cmake -S all -B build -DGTOPT_BUILD_INTEGRATION_TESTS=ON
cmake --build build -j
ctest --test-dir build -R '^e2e_'          # every case
ctest --test-dir build -R '^e2e_ieee_9b_'  # one case
ctest --test-dir build -L sddp             # the SDDP regression tests
```

With the Makefile generator, `make test` in `build/` runs the same
tests together with the unit tests.  For each case CTest runs the
solve (`e2e_<case>_solve`), checks the solution
(`e2e_<case>_validate_solution`) and compares every reference CSV in
`output/` (`e2e_<case>_compare_<Element>_<file>`, tolerance 1e-6).
Where the LP has several optimal solutions, `output_<solver>/` holds the
results of each solver, selected with `GTOPT_SOLVER=<solver>`.  When the
`gtopt_compare` script and pandapower are installed
(`pip install -e scripts`), `s1b`, `ieee_4b_ori`, `bat_4b_24`, `ieee30b`
and `ieee_57b` are also compared against pandapower's DC OPF
(`e2e_<case>_gtopt_compare`).

Every planning JSON here and under `scripts/cases/` is also built with
`gtopt --lp-only` (`case_lp_only_<dir>_<stem>` and
`script_case_lp_only_<dir>_<stem>`, label `cases`; run them with
`ctest --test-dir build -L cases`), so a case left in an old schema
fails the suite.  Fix such a case by re-running its converter (the
command is in its README or `<tool>_state.json`), not by editing the
JSON by hand.

To compare a manual run by hand:

```bash
python3 tools/gtopt_compare_csv.py \
  cases/ieee_9b/results/solution.csv cases/ieee_9b/output/solution.csv
```

## Didactic cases (written for GTOpt)

Small hand-written systems.  Each isolates one feature, so its optimum
can be checked by hand.

| Case | What it is |
|------|------------|
| [`s1b`](s1b/) | One bus, two thermal generators and one demand: the smallest economic dispatch. Also compared against pandapower. |
| [`c0`](c0/) | One bus, five stages: demand expansion with an annual investment cost and a 10% discount rate. The demand profile is read from Parquet (`system_c0/Demand/lmax.parquet`). Used as the example in [`docs/usage.md`](../docs/usage.md). |
| [`c1`](c1/) | Same as `c0` with a zero discount rate, to check discounting. |
| [`ieee_4b_ori`](ieee_4b_ori/) | Four-bus meshed network (based on the Grainger and Stevenson four-bus system) with DC power flow and two generators: the simplest optimal power flow. Also compared against pandapower. |
| [`exp_gen_4b`](exp_gen_4b/) | The four-bus network with generation expansion: the cheap generator can expand from 10 to 300 MW at an annual capacity cost. |
| [`bat_4b`](bat_4b/) | The four-bus network with a battery (200 MWh, 60 MW, 95% charge and discharge efficiency) and a solar profile, four blocks. |
| [`bat_4b_24`](bat_4b_24/) | `bat_4b` over 24 hours: daily battery cycling. Also compared against pandapower. Used in [`docs/storage-and-water-values.md`](../docs/storage-and-water-values.md). |
| [`exp_bat_4b`](exp_bat_4b/), [`exp_bat_4b_24`](exp_bat_4b_24/) | The battery cases with battery expansion: capacity starts at zero and is built if it pays off. |
| [`uc_small`](uc_small/) | One bus, three units with unit commitment over six hours: minimum output, no-load and start-up costs, minimum up and down times and initial status. |
| [`uc_power_based`](uc_power_based/) | One bus, three units over eight hours with the power-based unit commitment (`uc_formulation = "power_based"`): boundary powers, trapezoid energies, ramp limits on the instantaneous power, start-up and shut-down trajectories. |
| [`uc_power_based_philipsen2019`](uc_power_based_philipsen2019/) | Power-based schedule from the literature: Philipsen et al., Applied Energy 2019, Scenario 2 — two ramp-limited units, published optimum 12 570 (Table 9) and schedule (Table 8). |
| [`uc_pricing_fixed`](uc_pricing_fixed/), [`uc_pricing_relaxed`](uc_pricing_relaxed/), [`uc_pricing_fast_start`](uc_pricing_fast_start/) | Two buses, a slow unit and a fast-start peaker with unit commitment over two hours, solved with each post-MIP pricing mode (`model_options.pricing_mode`): same dispatch, different prices. |
| [`uc_pricing_lit_scarf_fixed`](uc_pricing_lit_scarf_fixed/), [`uc_pricing_lit_scarf_relaxed`](uc_pricing_lit_scarf_relaxed/), [`uc_pricing_lit_hb1_fixed`](uc_pricing_lit_hb1_fixed/), [`uc_pricing_lit_hb1_relaxed`](uc_pricing_lit_hb1_relaxed/), [`uc_pricing_lit_hb1_fast_start`](uc_pricing_lit_hb1_fast_start/), [`uc_pricing_lit_hb2_fixed`](uc_pricing_lit_hb2_fixed/), [`uc_pricing_lit_hb2_relaxed`](uc_pricing_lit_hb2_relaxed/), [`uc_pricing_lit_pjm1_fixed`](uc_pricing_lit_pjm1_fixed/), [`uc_pricing_lit_pjm1_fast_start`](uc_pricing_lit_pjm1_fast_start/) | Pricing modes on published non-convex pricing examples: Scarf / Hogan & Ring (2003), Hua & Baldick (2017) Examples 1 and 2, PJM (2017) fast-start pricing Example 1. Parameters typed in from the papers; each README compares published and gtopt prices and uplift. |
| [`reserve_cpf_planes`](reserve_cpf_planes/), [`reserve_cpf_planes_endogenous`](reserve_cpf_planes_endogenous/) | Three buses, 24 hours, solar surplus: CEN CPF max-of-planes requirement with the exogenous (default) and the PLEXOS-compat endogenous load term — 0 vs −0.732 $/MWh at the curtailed hours. |
| [`reserve_cpf_contingency_inertia`](reserve_cpf_contingency_inertia/) | Two buses, six hours: CPF planes with a contingency term, an inertia zone and a commitment-dependent inertia credit. |
| [`reserve_sscc_full`](reserve_sscc_full/) | One bus, eight hours: CPF, CSF (equality) and CTF with shared headroom, cold CTF from a peaker and a BESS offering both sides with per-side caps. |
| [`sddp_hydro_3phase`](sddp_hydro_3phase/) | One reservoir (150 hm³), a hydro and a thermal unit, three phases of 24 hours solved with SDDP. Regression case for SDDP: the `golden_iter*.json` files pin the results after 0, 3 and 50 iterations, also with the low-memory modes and hot start. |

## IEEE and standard test networks

Classic benchmark networks for DC optimal power flow, converted to GTOpt
JSON.  The `_ori` cases keep the original single-period data; the others
add a 24-hour profile.

| Case | Size | Source |
|------|------|--------|
| [`ieee_9b_ori`](ieee_9b_ori/) | 9 buses, 3 generators, 9 lines | WSCC 9-bus system (Anderson and Fouad, *Power System Control and Stability*), as in MATPOWER `case9`. |
| [`ieee_9b`](ieee_9b/) | 9 buses | The 9-bus system with a 24-hour demand profile and a solar plant (zero cost, hourly profile). Used for the screenshots in [`docs/interfaces.md`](../docs/interfaces.md). |
| [`ieee_14b_ori`](ieee_14b_ori/) | 14 buses, 5 generators, 20 lines | IEEE 14-bus test case (University of Washington Power Systems Test Case Archive), as in MATPOWER `case14`, with linear costs and line limits that bind. |
| [`ieee_14b`](ieee_14b/) | 14 buses | The 14-bus system over 24 hours, with a solar plant and daily demand curves; congestion makes the nodal prices differ by bus and hour. |
| [`ieee30b`](ieee30b/) | 30 buses, 6 generators, 41 lines | IEEE 30-bus test case, from pandapower `case_ieee30` with linear costs. Compared against pandapower's DC OPF. |
| [`ieee_57b`](ieee_57b/) | 57 buses, 7 generators, 80 lines | IEEE 57-bus test case, from pandapower `case57` with the quadratic costs removed. Compared against pandapower's DC OPF. |
| [`ieee_24b_rts`](ieee_24b_rts/) | 24 buses, 11 generators, 38 lines | IEEE Reliability Test System 1996 (RTS-96), from pandapower `case24_ieee_rts` via `pp2gtopt`. Generation is short of load, which exercises the unserved-demand accounting. |
| [`ieee_118b`](ieee_118b/) | 118 buses, 54 generators, 186 lines | IEEE 118-bus test case (AEP system, early 1960s), from pandapower with pglib-opf costs via `pp2gtopt`. Reference system only; not registered as a test. |
| [`case3375wp`](case3375wp/) | 3374 buses, 590 generators, 4161 lines | Polish transmission grid, winter 2007–08 evening peak (R. Korab), from MATPOWER `case3375wp` (BSD). The largest network in the test suite. |

## Hydrothermal and SDDP benchmarks

Reservoirs, waterways, turbines and junctions, taken from published SDDP
examples so the results can be checked against the original
implementations.

| Case | Source |
|------|--------|
| [`hydro_thermal_sddpjl`](hydro_thermal_sddpjl/) | SDDP.jl *Hydro_thermal* example (O. Dowson, MPL-2.0): one reservoir and a thermal unit. |
| [`fast_hydro_thermal`](fast_hydro_thermal/) | SDDP.jl *FAST hydro-thermal* example, itself a re-implementation of L. Cambier's FAST example. |
| [`hydro_valley_sddpjl`](hydro_valley_sddpjl/) | SDDP.jl *Hydro Valleys* example: two reservoirs in cascade with piecewise-linear turbines. |
| [`psri_case2_via_sddp2gtopt`](psri_case2_via_sddp2gtopt/) | PSR SDDP sample *case2* (two systems, three thermal and two hydro plants, twelve months), from `psrenergy/PSRClassesInterface.jl` (MPL-2.0), converted with `sddp2gtopt`. |
| [`psri_case3_via_sddp2gtopt`](psri_case3_via_sddp2gtopt/) | PSR SDDP sample *case3*, a variant of *case2* with a shorter inflow series, converted with `sddp2gtopt`. |

## Other case libraries in the repository

Besides this directory, several converters and tools keep their own
libraries of cases, used by their test suites.  Each has a README
with its contents and how to run its tests:

| Directory | Contents |
|-----------|----------|
| [`scripts/cases/`](../scripts/cases/README.md) | Inputs for the converters. `plp_min_*`: a series of minimal PLP cases (`.dat` files), one per PLP feature (one and two buses, hydro, multi-stage hydro, reservoir, filtration, batteries and ESS, maintenance of units and lines, head-dependent efficiency); `plp_hydro_4b`, `plp_bat_4b_24` and `plp_dat_ex`: larger PLP examples, the battery one with PLP's own results for comparison. `igtopt_*`: the `c0`, `bat_4b_24` and IEEE 57-bus cases as Excel workbooks for `igtopt`. `json_c0`: `c0` in plain JSON. `lp_infeasible`: small LP files, feasible and infeasible, for the LP diagnostic tools. |
| [`tools/test_data/`](../tools/test_data/README.md) | 27 instances of the [UnitCommitment.jl](https://github.com/ANL-CEEESA/UnitCommitment.jl) benchmark library (Argonne National Laboratory): IEEE 14-bus variants (base, congested, contingency, storage, sub-hourly, …), IEEE 24-bus TEP, IEEE 118-bus, RTS-GMLC, and day-ahead and real-time market examples, with UnitCommitment.jl's reference solutions. Converted with `tools/ucjl2gtopt.py`. |
| [`scripts/sddp2gtopt/tests/data/`](../scripts/sddp2gtopt/tests/data/) | PSR SDDP cases: the public PSR samples `case0` and `psri_case1`–`psri_case3` (IEEE 123-bus, multi-system), from `PSRClassesInterface.jl` (MPL-2.0), and hand-crafted cases for edge cases and errors. See its [`README.md`](../scripts/sddp2gtopt/tests/data/README.md). |
| [`scripts/psse2gtopt/tests/data/`](../scripts/psse2gtopt/tests/data/) | PSS/E `.raw` files: IEEE 14-bus, IEEE 39-bus (New England) and a synthetic case covering every record type. See [`PROVENANCE.md`](../scripts/psse2gtopt/tests/data/PROVENANCE.md). |
| [`scripts/plexos2gtopt/tests/`](../scripts/plexos2gtopt/tests/fixtures/README.md) | Expected conversions per element type, and the public IEEE 118-bus and RTS-96 PLEXOS models from NREL's `plexos-coad` (BSD-3-Clause). |
| [`scripts/pp2gtopt/tests/fixtures/`](../scripts/pp2gtopt/tests/fixtures/README.md) | pandapower entities and the expected IEEE 30-bus conversion. |
| [`scripts/cen2gtopt/tests/data/`](../scripts/cen2gtopt/tests/data/README.md) | Short samples of the public data of the Chilean system operator (CEN): marginal costs, demand, generation and technical data. |

Large studies of real systems (for example the Chilean national system
over two years, `gtopt_case_2y*`, or `cen_long_term_1y`) are not shipped
with the repository; they live in the case repository gtopt-cases
(`<country>/<application>/<case>/`).  `tools/get_cases.sh` clones it at
the commit pinned in `tools/cases.lock`; point `GTOPT_CASES_DIR` at the
checkout.  The tests that use those cases are skipped (pytest) or not
registered (CTest) when it is unset.

## Licences of the data

The cases written for GTOpt follow the licence of the repository.  The
converted cases keep the terms of their sources: MATPOWER (BSD-3-Clause),
pandapower (BSD-3-Clause), pglib-opf (CC BY 4.0), plexos-coad
(BSD-3-Clause, NREL), UnitCommitment.jl
(BSD-style licence of UChicago Argonne), SDDP.jl and
PSRClassesInterface.jl (MPL-2.0).  The IEEE test systems are public
benchmark data.  See each case's `README.md` for details.

## Adding a case

Put the system JSON and an `output/` directory with the expected results
under `cases/<name>/`, register it with `add_e2e_case(<name> <file>.json)`
(or `add_sddp_case` for SDDP) in `integration_test/CMakeLists.txt`, and
add a line to this file.  Cases from real power systems are especially
welcome; see [`CONTRIBUTING.md`](../CONTRIBUTING.md).
