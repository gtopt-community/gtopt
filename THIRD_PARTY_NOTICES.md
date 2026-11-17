# Third-Party Notices

GTOpt is Copyright (c) Marcelo Matus and is licensed under
`AGPL-3.0-or-later OR LicenseRef-gtopt-commercial`, with the Extension
SDK headers under `Apache-2.0` (see
[`LICENSING.md`](LICENSING.md)). This repository also contains, or
downloads at build time, material written by third parties. That
material stays under its own licence, listed below. The GTOpt licences
do not apply to it, and nothing here grants rights beyond those
licences.

## 1. Files vendored in this repository

### JavaScript / CSS (GUI service)

| Path | Component | Version | Licence | Copyright |
|---|---|---|---|---|
| `guiservice/static/vendor/jspreadsheet-ce/` | [Jspreadsheet CE](https://github.com/jspreadsheet/ce) | 4.15.0 | MIT | Paul Hodel and Jspreadsheet contributors |
| `guiservice/static/vendor/jsuites/` | [jSuites](https://github.com/jsuites/jsuites) | (bundled with Jspreadsheet CE 4.x) | MIT | Paul Hodel and jSuites contributors |
| `guiservice/static/vendor/chartjs/chart.umd.min.js` | [Chart.js](https://www.chartjs.org) | 4.4.8 | MIT | Chart.js Contributors |
| `guiservice/static/vendor/chartjs/chartjs-plugin-zoom.min.js` | [chartjs-plugin-zoom](https://github.com/chartjs/chartjs-plugin-zoom) | 2.2.0 | MIT | chartjs-plugin-zoom Contributors |
| `guiservice/static/vendor/chartjs/hammer.min.js` | [Hammer.JS](https://hammerjs.github.io) | 2.0.7 | MIT | Jorik Tangelder |

### Build scripts

| Path | Component | Licence |
|---|---|---|
| `cmake/CPM.cmake` | [CPM.cmake](https://github.com/cpm-cmake/CPM.cmake) 0.42.1 | MIT, Copyright (c) 2019-2023 Lars Melchior and contributors |
| `cmake/tools.cmake` | [ModernCppStarter](https://github.com/TheLartians/ModernCppStarter) | The Unlicense (public domain) |

### Test and benchmark data

| Path | Source | Licence / terms |
|---|---|---|
| `scripts/plexos2gtopt/tests/data/plexos_coad/` | [Computational-Energy/plexos-coad](https://github.com/Computational-Energy/plexos-coad) | BSD-3-Clause, Copyright (c) 2018 National Renewable Energy Laboratory (full text in that directory's `LICENSE`) |
| `scripts/sddp2gtopt/tests/data/psri_*` and `cases/psri_case*_via_sddp2gtopt/` | [psrenergy/PSRClassesInterface.jl](https://github.com/psrenergy/PSRClassesInterface.jl) sample cases | MPL-2.0 (full text in `scripts/sddp2gtopt/tests/data/LICENSE-PSRClassesInterface`) |
| `tools/test_data/UnitCommitmentJl_*` | Benchmark instances from [UnitCommitment.jl](https://github.com/ANL-CEEESA/UnitCommitment.jl) (and their converted goldens) | UnitCommitment.jl licence (BSD-3-Clause style), Copyright (c) 2020-2022 UChicago Argonne, LLC |
| `tools/sienna_goldens/` | Reference results produced with [PowerSimulations.jl](https://github.com/NREL-Sienna/PowerSimulations.jl) on [PowerSystemCaseBuilder.jl](https://github.com/NREL-Sienna/PowerSystemCaseBuilder.jl) test systems | BSD-3-Clause, Copyright (c) Alliance for Sustainable Energy, LLC |
| `tools/gtopt_sos2_convergence/data/case14.m`, `cases/case3375wp/` | [MATPOWER](https://github.com/MATPOWER/matpower) case files | MATPOWER case files are not covered by MATPOWER's BSD licence; they are publicly available test data or included with the authors' permission (see the MATPOWER `LICENSE`). `case3375wp`: Roman Korab, Silesian University of Technology. |
| `cases/ieee_*`, `cases/ieee30b`, IEEE test systems used in tests | IEEE test systems from the University of Washington Power Systems Test Case Archive, IEEE RTS-96, and their [pandapower](https://github.com/e2nIEE/pandapower) / MATPOWER conversions | Publicly available benchmark data; pandapower is BSD-3-Clause |
| `cases/hydro_thermal_sddpjl/`, `cases/hydro_valley_sddpjl/`, `cases/fast_hydro_thermal/` | Models adapted from the [SDDP.jl](https://github.com/odow/SDDP.jl) examples | SDDP.jl is MPL-2.0, Copyright (c) 2017- Oscar Dowson and SDDP.jl contributors |
| `integration_test/cases/nrel118_reference/` | Figures quoted from Peña, Martinez-Anido and Hodge (2018), *An Extended IEEE 118-Bus Test System With High Renewable Penetration*, IEEE TPWRS, doi:10.1109/TPWRS.2017.2695963 | Short factual excerpts for regression testing |

The converters `scripts/rts_gmlc_to_gtopt`, `scripts/nrel118_to_gtopt`,
`scripts/sienna_to_gtopt`, `tools/ucjl2gtopt.py` and
`scripts/plexos2gtopt` read third-party datasets that are **not**
included in this repository; obtain them from their upstream projects
under their own terms.

## 2. Dependencies fetched at build time (not vendored)

Downloaded by CPM/CMake when building and linked into, or used to
build, GTOpt binaries and documentation:

| Component | Licence |
|---|---|
| [spdlog](https://github.com/gabime/spdlog) | MIT |
| [DAW JSON Link](https://github.com/beached/daw_json_link), [header_libraries](https://github.com/beached/header_libraries), [utf_range](https://github.com/beached/utf_range) | Boost Software License 1.0 |
| [strong_type](https://github.com/rollbear/strong_type) | Boost Software License 1.0 |
| [doctest](https://github.com/doctest/doctest) (tests only) | MIT |
| [StableCoder cmake-scripts](https://github.com/StableCoder/cmake-scripts) (optional sanitizer/coverage tooling) | Apache-2.0 |
| [m.css](https://github.com/mosra/m.css) (documentation build only) | MIT |

System libraries such as Apache Arrow/Parquet (Apache-2.0), Boost
(BSL-1.0), COIN-OR CLP/CBC (EPL-2.0), HiGHS (MIT), zlib, zstd and lz4
are installed separately and keep their own licences. Commercial solver
plugins (CPLEX, Gurobi, MindOpt) require a licence from their vendors;
see [`LICENSE-EXCEPTION.md`](LICENSE-EXCEPTION.md).

The Node.js (`webservice/`, `guiservice-plus/`) and Python (`scripts/`,
`guiservice/`) packages declare their dependencies in `package.json`,
`package-lock.json`, `pyproject.toml` and `requirements*.txt`; those
packages are downloaded by the respective package managers under their
own licences.
