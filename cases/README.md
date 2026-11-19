# Sample and test cases

This directory holds small, public cases that show how to write a GTOpt
model and that the test suite solves on every change.  Each case is a
directory with a system JSON file (and, for some, an input-data
directory in Parquet), plus an `output/` directory with the reference
results that the end-to-end tests compare against.  The `output_<solver>/`
directories hold the same results computed with each LP solver plugin.

To run one:

```bash
cd cases/ieee_9b
gtopt ieee_9b.json
```

The cases are registered as tests in
[`integration_test/CMakeLists.txt`](../integration_test/CMakeLists.txt).
Cases with their own `README.md` describe the source, the conversion and
the expected results in more detail.

## Didactic cases (written for GTOpt)

Small hand-written systems.  Each isolates one feature, so its optimum
can be checked by hand.

| Case | What it is |
|------|------------|
| [`s1b`](s1b/) | One bus, two thermal generators and one demand: the smallest economic dispatch. Also compared against pandapower. |
| [`c0`](c0/) | One bus, five stages: demand expansion with an annual investment cost and a 10% discount rate. The demand profile is read from Parquet (`system_c0/Demand/lmax.parquet`). Used as the example in [`docs/usage.md`](../docs/usage.md). |
| [`c1`](c1/) | Same as `c0` with a zero discount rate, to check discounting. |
| [`ieee_4b_ori`](ieee_4b_ori/) | Four-bus meshed network with DC power flow (Kirchhoff) and two generators: the simplest optimal power flow. Also compared against pandapower. |
| [`exp_gen_4b`](exp_gen_4b/) | The four-bus network with generation expansion: the cheap generator can expand from 10 to 300 MW at an annual capacity cost. |
| [`bat_4b`](bat_4b/) | The four-bus network with a battery (200 MWh, 60 MW, 95% charge and discharge efficiency) and a solar profile, four blocks. |
| [`bat_4b_24`](bat_4b_24/) | `bat_4b` over 24 hours: daily battery cycling. Also compared against pandapower. Used in [`docs/storage-and-water-values.md`](../docs/storage-and-water-values.md). |
| [`exp_bat_4b`](exp_bat_4b/), [`exp_bat_4b_24`](exp_bat_4b_24/) | The battery cases with battery expansion: capacity starts at zero and is built if it pays off. |
| [`uc_small`](uc_small/) | One bus, three units with unit commitment over six hours: minimum output, no-load and start-up costs, minimum up and down times and initial status. |
| [`sddp_hydro_3phase`](sddp_hydro_3phase/) | One reservoir (150 hm³), a hydro and a thermal unit, three phases of 24 hours solved with SDDP. Regression case for SDDP: the `golden_iter*.json` files pin the results after 0, 3 and 50 iterations, also with the low-memory modes and hot start. |

## IEEE and standard test networks

Classic benchmark networks for DC optimal power flow, converted to GTOpt
JSON.  The `_ori` cases keep the original single-period data; the others
add a 24-hour profile.

| Case | Size | Source |
|------|------|--------|
| [`ieee_9b_ori`](ieee_9b_ori/) | 9 buses, 3 generators, 9 lines | WSCC 9-bus system (Anderson and Fouad, *Power System Control and Stability*), as in MATPOWER `case9`. |
| [`ieee_9b`](ieee_9b/) | 9 buses | The 9-bus system with a 24-hour demand profile and a solar plant (zero cost, hourly profile). Used for the screenshots in [`docs/interfaces.md`](../docs/interfaces.md). |
| [`ieee_14b_ori`](ieee_14b_ori/) | 14 buses, 5 generators, 20 lines | IEEE 14-bus test case (University of Washington Power Systems Test Case Archive), as in MATPOWER `case14`. |
| [`ieee_14b`](ieee_14b/) | 14 buses | The 14-bus system over 24 hours, with a solar profile and tightened line limits so that Kirchhoff constraints bind and nodal prices differ. |
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
libraries of cases, used by their test suites:

| Directory | Contents |
|-----------|----------|
| [`scripts/cases/`](../scripts/cases/) | Inputs for the converters. `plp_min_*`: a series of minimal PLP cases (`.dat` files), one per PLP feature (one and two buses, hydro, multi-stage hydro, reservoir, filtration, batteries and ESS, maintenance of units and lines, head-dependent efficiency); `plp_hydro_4b`, `plp_bat_4b_24` and `plp_dat_ex`: larger PLP examples, the battery one with PLP's own results for comparison. `igtopt_*`: the `c0`, `bat_4b_24` and IEEE 57-bus cases as Excel workbooks for `igtopt`. `json_c0`: `c0` in plain JSON. `lp_infeasible`: small LP files, feasible and infeasible, for the LP diagnostic tools. |
| [`tools/test_data/`](../tools/test_data/) | 27 instances of the [UnitCommitment.jl](https://github.com/ANL-CEEESA/UnitCommitment.jl) benchmark library (Argonne National Laboratory): IEEE 14-bus variants (base, congested, contingency, storage, sub-hourly, …), IEEE 24-bus TEP, IEEE 118-bus, RTS-GMLC, and day-ahead and real-time market examples, with UnitCommitment.jl's reference solutions. Converted with `tools/ucjl2gtopt.py`. |
| [`scripts/sddp2gtopt/tests/data/`](../scripts/sddp2gtopt/tests/data/) | PSR SDDP cases: the public PSR samples `case0` and `psri_case1`–`psri_case3` (IEEE 123-bus, multi-system), from `PSRClassesInterface.jl` (MPL-2.0), and hand-crafted cases for edge cases and errors. See its [`README.md`](../scripts/sddp2gtopt/tests/data/README.md). |
| [`scripts/psse2gtopt/tests/data/`](../scripts/psse2gtopt/tests/data/) | PSS/E `.raw` files: IEEE 14-bus, IEEE 39-bus (New England) and a synthetic case covering every record type. See [`PROVENANCE.md`](../scripts/psse2gtopt/tests/data/PROVENANCE.md). |
| [`scripts/plexos2gtopt/tests/`](../scripts/plexos2gtopt/tests/) | Small synthetic PLEXOS inputs, one per entity type, for the PLEXOS converter. |
| [`scripts/pp2gtopt/tests/fixtures/`](../scripts/pp2gtopt/tests/fixtures/) | pandapower entities and the expected IEEE 30-bus conversion. |
| [`scripts/cen2gtopt/tests/data/`](../scripts/cen2gtopt/tests/data/) | Short samples of the public data of the Chilean system operator (CEN): marginal costs, demand and generation. |

Large studies of real systems (for example the Chilean national system
over two years) are not shipped with the repository; the tests that use
them look for them in `GTOPT_CASES_DIR` and are skipped when they are
absent.

## Licences of the data

The cases written for GTOpt follow the licence of the repository.  The
converted cases keep the terms of their sources: MATPOWER (BSD-3-Clause),
pandapower (BSD-3-Clause), pglib-opf (CC BY 4.0), UnitCommitment.jl
(BSD-style licence of UChicago Argonne), SDDP.jl and
PSRClassesInterface.jl (MPL-2.0).  The IEEE test systems are public
benchmark data.  See each case's `README.md` for details.

## Adding a case

Put the system JSON and an `output/` directory with the expected results
under `cases/<name>/`, register it with `add_e2e_case(<name> <file>.json)`
(or `add_sddp_case` for SDDP) in `integration_test/CMakeLists.txt`, and
add a line to this file.  Cases from real power systems are especially
welcome; see [`CONTRIBUTING.md`](../CONTRIBUTING.md).
