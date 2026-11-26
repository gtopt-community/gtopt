# Third-Party Notices

GTOpt is Copyright (c) Marcelo Matus and is licensed under
`AGPL-3.0-or-later OR LicenseRef-gtopt-commercial`, with the Extension
SDK headers under `Apache-2.0` (see
[`LICENSING.md`](LICENSING.md)). This repository also contains, or
downloads at build or run time, material written by third parties. That
material stays under its own licence, listed below. The GTOpt licences
do not apply to it, and nothing here grants rights beyond those
licences.

GTOpt is built on, and tested against, the work of many open-source
projects. The power-system and optimization projects that GTOpt uses
most directly are credited first, in section 1.

## 1. Power-system and optimization projects

| Project | Licence | Copyright | What GTOpt uses it for |
|---|---|---|---|
| [pandapower](https://github.com/e2nIEE/pandapower) | BSD-3-Clause | University of Kassel, Fraunhofer Institute for Energy Economics and Energy System Technology (IEE) Kassel, and individual contributors | Python runtime dependency. `pp2gtopt` converts pandapower networks to GTOpt and `gtopt2pp` converts back; `gtopt_compare`, `tools/generate_golden.py` and `tools/validate_network_reduction.py` cross-check GTOpt's DC OPF against pandapower's `rundcopp`; `plexos2gtopt --auto-lift-lines` runs a pandapower DC OPF to choose which line limits to lift; `gtopt_check_json` builds a pandapower network for its checks. The IEEE networks in `pandapower.networks` (MATPOWER conversions) are the source of several cases in `cases/`. pandapower's OPF code is partly derived from PYPOWER (BSD-3-Clause, Richard Lincoln and PSERC). |
| [NetworkX](https://networkx.org) | BSD-3-Clause | NetworkX Developers | Graph algorithms (connected components, bridges, cycle bases) in `gtopt_check_topology`, `gtopt_diagram`, `gtopt_reduce_network` (`gtopt-net`), `gtopt_marginal_units` and the hydro helpers of `gtopt_shared` |
| [SciPy](https://scipy.org) | BSD-3-Clause | SciPy Developers, Enthought | Sparse graph algorithms and hierarchical clustering in `gtopt_reduce_network` (`gtopt-net`) |
| [MATPOWER](https://matpower.org) | BSD-3-Clause (code); case files have their own terms, see section 2 | Power Systems Engineering Research Center (PSERC) and contributors | Source of the `case5`, `case9`, `case14`, `case39` and `case3375wp` test systems and of reference values in several C++ tests |
| [pglib-opf](https://github.com/power-grid-lib/pglib-opf) | Data: CC BY 4.0; software: MIT | A Library of IEEE PES Power Grid Benchmarks, and the holders named in each data file | Linear cost coefficients for `cases/ieee_118b` |
| [SDDP.jl](https://github.com/odow/SDDP.jl) | MPL-2.0 | Oscar Dowson and SDDP.jl contributors | Hydro-thermal and hydro-valley examples adapted as cases and tests; reference values of the SDDP literature benchmarks |
| [PSRClassesInterface.jl](https://github.com/psrenergy/PSRClassesInterface.jl) | MPL-2.0 | PSR | Sample PSR SDDP cases used to test `sddp2gtopt` |
| [UnitCommitment.jl](https://github.com/ANL-CEEESA/UnitCommitment.jl) | BSD-3-Clause style (UChicago Argonne open-source licence) | UChicago Argonne, LLC | Unit-commitment benchmark instances and reference solutions (`tools/test_data/`) |
| [NREL Sienna](https://github.com/NREL-Sienna): PowerSimulations.jl, PowerSystemCaseBuilder.jl, PowerSystemsTestData | BSD-3-Clause (PowerSimulations.jl, PowerSystemCaseBuilder.jl); PowerSystemsTestData has no licence file and is therefore **not redistributed**: `scripts/sienna2gtopt` fetches it from upstream at a pinned commit at test/run time (see section 2) | Alliance for Sustainable Energy, LLC | Reference results (`tools/sienna_goldens/`, produced by our own PowerSimulations.jl runs) and the test systems read by `scripts/sienna2gtopt` |
| [PowerModels.jl](https://github.com/lanl-ansi/PowerModels.jl) | BSD-3-Clause | Los Alamos National Security, LLC | The `case5_asym` test system (`cases/pm_case5_asym/source/`) and reference objectives (`tools/powermodels_goldens/`, produced by our own `DCPPowerModel` / `DCPLLPowerModel` runs) for the line-loss / thermal-limit benchmark |
| [plexos-coad](https://github.com/Computational-Energy/plexos-coad) | BSD-3-Clause | National Renewable Energy Laboratory | Public PLEXOS test models for `plexos2gtopt` |
| [PyPSA](https://github.com/PyPSA/PyPSA) | MIT | PyPSA developers | Reference for the cycle-basis Kirchhoff formulation (Hörsch et al., 2018) and the CO2-constraint example restated in `test/source/test_emission_pypsa_port.cpp`. No PyPSA code is included. |
| [GenX](https://github.com/GenXProject/GenX) | GPL-2.0 | GenX contributors | Its documented `1_three_zones` example was used only as a cross-check for `test/source/test_emission_three_zone_ngcc.cpp`, an independent test built from public factual parameters (EPA natural-gas CO2 factor, EIA / NREL ATB heat rates, O&M and fuel prices; cited in the file). No GenX code, data files or tables are included. |
| [HiGHS](https://highs.dev) | MIT | HiGHS developers | LP/MIP solver plugin; the optional `highspy` Python package is used by `gtopt_check_lp` |
| [COIN-OR](https://www.coin-or.org) CLP, CBC, Osi, CoinUtils | EPL-2.0 | COIN-OR Foundation and contributors | LP/MIP solver plugins (`plugins/osi`) |
| [SCIP Optimization Suite](https://scipopt.org) | Apache-2.0 (SCIP 9 and later) | Zuse Institute Berlin and contributors | Optional MIP solver plugin (`plugins/scip`) |
| [NVIDIA cuOpt](https://github.com/NVIDIA/cuopt) | Apache-2.0 (cuOpt); the CUDA runtime is proprietary | NVIDIA Corporation | Optional GPU LP solver plugin (`plugins/cuopt`) |

Commercial solver plugins (CPLEX, Gurobi, MindOpt) need a licence from
their vendors; see [`LICENSE-EXCEPTION.md`](LICENSE-EXCEPTION.md).

## 2. Files vendored in this repository

### JavaScript / CSS (GUI service)

| Path | Component | Version | Licence | Copyright |
|---|---|---|---|---|
| `guiservice/static/vendor/jspreadsheet-ce/` | [Jspreadsheet CE](https://github.com/jspreadsheet/ce) | 4.15.0 | MIT | Paul Hodel and Jspreadsheet contributors |
| `guiservice/static/vendor/jsuites/` | [jSuites](https://github.com/jsuites/jsuites) | 5.13.5 (npm `jsuites@5.13.5` `dist/`, byte-identical; its internal version string still reads 5.13.3; latest 5.x, as required by Jspreadsheet CE 4.x) | MIT | Paul Hodel and jSuites contributors |
| `guiservice/static/vendor/chartjs/chart.umd.min.js` | [Chart.js](https://www.chartjs.org) | 4.4.8 | MIT | Chart.js Contributors |
| `guiservice/static/vendor/chartjs/chartjs-plugin-zoom.min.js` | [chartjs-plugin-zoom](https://github.com/chartjs/chartjs-plugin-zoom) | 2.2.0 | MIT | chartjs-plugin-zoom Contributors |
| `guiservice/static/vendor/chartjs/hammer.min.js` | [Hammer.JS](https://hammerjs.github.io) | 2.0.7 | MIT | Jorik Tangelder |

### Build scripts

| Path | Component | Licence |
|---|---|---|
| `cmake/CPM.cmake` | [CPM.cmake](https://github.com/cpm-cmake/CPM.cmake) 0.42.1 | MIT, Copyright (c) 2019-2023 Lars Melchior and contributors |
| `cmake/tools.cmake` | [ModernCppStarter](https://github.com/TheLartians/ModernCppStarter) | The Unlicense (public domain) |

### Test, benchmark and reference data

| Path | Source | Licence / terms |
|---|---|---|
| `scripts/plexos2gtopt/tests/data/plexos_coad/` | [Computational-Energy/plexos-coad](https://github.com/Computational-Energy/plexos-coad) | BSD-3-Clause, Copyright (c) 2018 National Renewable Energy Laboratory (full text in that directory's `LICENSE`) |
| `scripts/sddp2gtopt/tests/data/psri_*` and `cases/psri_case*_via_sddp2gtopt/` | [psrenergy/PSRClassesInterface.jl](https://github.com/psrenergy/PSRClassesInterface.jl) sample cases | MPL-2.0 (full text in `scripts/sddp2gtopt/tests/data/LICENSE-PSRClassesInterface`) |
| `tools/test_data/UnitCommitmentJl_*` | Benchmark instances from [UnitCommitment.jl](https://github.com/ANL-CEEESA/UnitCommitment.jl) (and their converted goldens); `rts_gmlc` and `case118_initcond` are that project's conversions of RTS-GMLC and MATPOWER data | UnitCommitment.jl licence (BSD-3-Clause style), Copyright (c) 2020-2022 UChicago Argonne, LLC |
| `tools/sienna_goldens/` | Reference results produced with [PowerSimulations.jl](https://github.com/NREL-Sienna/PowerSimulations.jl) on [PowerSystemCaseBuilder.jl](https://github.com/NREL-Sienna/PowerSystemCaseBuilder.jl) test systems | BSD-3-Clause, Copyright (c) Alliance for Sustainable Energy, LLC |
| `cases/pm_case5_asym/source/case5_asym.m`, `tools/powermodels_goldens/` | MATPOWER-format `case5_asym` from the [PowerModels.jl](https://github.com/lanl-ansi/PowerModels.jl) test data, and reference results produced with PowerModels.jl | BSD-3-Clause, Copyright (c) 2016 Los Alamos National Security, LLC |
| `tools/gtopt_sos2_convergence/data/case14.m`, `cases/case3375wp/` | [MATPOWER](https://github.com/MATPOWER/matpower) case files | MATPOWER case files are not covered by MATPOWER's BSD licence; they are publicly available test data or included with the authors' permission (see the MATPOWER `LICENSE`). `case3375wp`: Roman Korab, Silesian University of Technology. |
| `cases/ieee_*`, `cases/ieee30b`, IEEE test systems used in tests | IEEE test systems from the University of Washington Power Systems Test Case Archive and IEEE RTS-96, through their [pandapower](https://github.com/e2nIEE/pandapower) and MATPOWER conversions | Publicly available benchmark data; pandapower is BSD-3-Clause |
| `cases/ieee_118b/` | Linear cost coefficients from [pglib-opf](https://github.com/power-grid-lib/pglib-opf) | CC BY 4.0, A Library of IEEE PES Power Grid Benchmarks; attribution as given in the pglib-opf data files |
| `scripts/psse2gtopt/tests/data/ieee14.raw`, `ieee39.raw` | Generated by `make_matpower_raw.py` in that directory from the MATPOWER `case14` (IEEE 14-bus) and `case39` (New England 39-bus) cases as shipped by [pandapower](https://github.com/e2nIEE/pandapower) (`pandapower.networks`); see `PROVENANCE.md` there | Publicly available IEEE benchmark data (see the MATPOWER row above); pandapower is BSD-3-Clause |
| `cases/hydro_thermal_sddpjl/`, `cases/hydro_valley_sddpjl/`, `cases/fast_hydro_thermal/` | Models adapted from the [SDDP.jl](https://github.com/odow/SDDP.jl) examples | SDDP.jl is MPL-2.0, Copyright (c) 2017- Oscar Dowson and SDDP.jl contributors |
| `integration_test/cases/nrel118_reference/` | Figures quoted from Peña, Martinez-Anido and Hodge (2018), *An Extended IEEE 118-Bus Test System With High Renewable Penetration*, IEEE TPWRS, doi:10.1109/TPWRS.2017.2695963 | Short factual excerpts for regression testing |
| `test/source/test_emission_*_port.cpp`, `test/source/test_emission_three_zone_ngcc.cpp`, `test/source/test_sienna_5bus_*_port.cpp`, `test/source/test_reserve_inertia_benchmark.cpp`, `test/source/test_hydro_thermal_benchmark.cpp`, `test/source/test_sddp_literature_benchmarks.cpp`, `test/source/test_ieee738.cpp` | A small number of parameters restated from MATPOWER `case5`, RTS-GMLC, NREL-118, PyPSA, NREL Sienna and SDDP.jl examples, the IEEE 738-2012 Annex B examples and the NREL DynamicLineRatings static ratings (OEDI 6231), or taken from public EPA / EIA / NREL ATB data (each file names its source) | Factual parameters, cited in each file; no third-party code or data files are included |
| `scripts/gtopt_shared/data/ipcc_emission_factors.json`, `share/gtopt/emissions/` | Default CO2 emission factors and net calorific values from the *2006 IPCC Guidelines for National Greenhouse Gas Inventories*, Vol. 2, Ch. 1 | Factual values, cited in the files; © IPCC |
| `scripts/cen2gtopt/tests/data/`, `share/gtopt/cogen/`, `share/gtopt/emissions/cen_chile.json`, `scripts/plexos2gtopt/data/` | Small samples and lists taken from the public data of the Chilean system operator, [Coordinador Eléctrico Nacional](https://www.coordinador.cl) (CEN) | Public information of the CEN, cited in each file |

The converters `scripts/rts_gmlc_to_gtopt`, `scripts/nrel118_to_gtopt`,
`scripts/sienna2gtopt`, `scripts/cucdata2gtopt`, `tools/ucjl2gtopt.py`, `scripts/cen2gtopt`
and `scripts/plexos2gtopt` read third-party datasets that are **not**
included in this repository (for example
[RTS-GMLC](https://github.com/GridMod/RTS-GMLC) and the NREL-118 data);
obtain them from their upstream projects under their own terms.

The Sienna
[PowerSystemsTestData](https://github.com/Sienna-Platform/PowerSystemsTestData)
files used by `scripts/sienna2gtopt` (`5-Bus/`,
`psy_data/data_5bus_pu.jl`, `psy_data/data_14bus_pu.jl`; the 5-bus
system is the PJM 5-bus case of F. Li and R. Bo, 2010) are **fetched
from upstream at build/test time and not redistributed**: that
repository has no licence. `scripts/sienna2gtopt/_bundle.py` records
the upstream repository, the pinned commit and the SHA-256 of every
file, and caches the downloaded files under `~/.cache/gtopt/sienna/`
(see `integration_test/data/README.md`).

The clustered unit-commitment benchmark data of G. Morales-España and
D. A. Tejada-Arango ([CUC-data](https://github.com/datejada/CUC-data):
`CUC_IEEE39-busSystem.xlsx`, `CUC_IEEE118-busSystem.xlsx`,
`StarGenLite_Clustered-UC.gms`) read by `scripts/cucdata2gtopt` are
likewise **fetched from upstream on demand and not redistributed**: that
repository has no licence. `scripts/cucdata2gtopt/_source.py` records the
pinned commit and the SHA-256 of every file and caches them under
`~/.cache/gtopt/cucdata/` (see `docs/scripts/cucdata2gtopt.md`).

## 3. Dependencies fetched at build time (not vendored)

Downloaded by CPM/CMake when building and linked into, or used to
build, GTOpt binaries and documentation:

| Component | Licence |
|---|---|
| [spdlog](https://github.com/gabime/spdlog) 1.15.0 (with its bundled {fmt}) | MIT |
| [DAW JSON Link](https://github.com/beached/daw_json_link) 3.31.0, [header_libraries](https://github.com/beached/header_libraries) 2.136.0, [utf_range](https://github.com/beached/utf_range) 2.2.6 | Boost Software License 1.0 |
| [strong_type](https://github.com/rollbear/strong_type) v15 | Boost Software License 1.0 |
| [doctest](https://github.com/doctest/doctest) 2.5.0 (tests only) | MIT |
| [StableCoder cmake-scripts](https://github.com/StableCoder/cmake-scripts) (optional sanitizer/coverage tooling) | Apache-2.0 |
| [m.css](https://github.com/mosra/m.css) (documentation build only) | MIT |

System libraries are installed separately and keep their own licences:

| Component | Licence | Use |
|---|---|---|
| Apache Arrow / Parquet | Apache-2.0 | Parquet and CSV input and output |
| Boost (Container) | BSL-1.0 | Containers |
| zlib, zstd, lz4, snappy | zlib; BSD-3-Clause (zstd is dual BSD/GPL-2.0 and is used under BSD); BSD-2-Clause; BSD-3-Clause | Compression |
| jemalloc (optional) | BSD-2-Clause | Memory allocator of the `gtopt` binary |
| LAPACK / BLAS | BSD-3-Clause (Reference LAPACK) | Required by COIN-OR CoinUtils |
| COIN-OR, HiGHS, SCIP, cuOpt | See section 1 | Solver plugins |

## 4. Python and Node.js packages (installed by package managers)

These packages are declared in `scripts/pyproject.toml`,
`scripts/requirements*.txt`, `guiservice/requirements.txt` and the
`package.json` / `package-lock.json` files, and are downloaded by pip or
npm under their own licences. They are not distributed with GTOpt.

### Python runtime dependencies (`scripts/`, `guiservice/`)

| Package | Use in GTOpt | Licence |
|---|---|---|
| [pandapower](https://github.com/e2nIEE/pandapower) | Network conversion and DC OPF cross-checks (see section 1) | BSD-3-Clause |
| [NetworkX](https://networkx.org) | Graph algorithms (see section 1) | BSD-3-Clause |
| [SciPy](https://scipy.org) | Network reduction (`gtopt-net`) | BSD-3-Clause |
| [NumPy](https://numpy.org) | Numerical arrays | BSD-3-Clause |
| [pandas](https://pandas.pydata.org) | Tables | BSD-3-Clause |
| [PyArrow](https://arrow.apache.org) | Parquet input and output | Apache-2.0 |
| [openpyxl](https://openpyxl.readthedocs.io) | Excel workbooks (`igtopt`, exporters) | MIT |
| [xlrd](https://github.com/python-excel/xlrd) | Legacy `.xls` files in `psse2gtopt` | BSD-3-Clause |
| [Rich](https://github.com/Textualize/rich) | Terminal output and the `run_gtopt` dashboard | MIT |
| [Jinja2](https://jinja.palletsprojects.com) | Templates in `gtopt_expand` | BSD-3-Clause |
| [requests](https://requests.readthedocs.io), with urllib3, idna, charset-normalizer and certifi | CEN data client, GUI service | Apache-2.0 (urllib3 and charset-normalizer MIT, idna BSD-3-Clause, certifi MPL-2.0) |
| [zstandard](https://github.com/indygreg/python-zstandard), [lz4](https://github.com/python-lz4/python-lz4) | Compressed files | BSD-3-Clause |
| [graphviz](https://github.com/xflr6/graphviz) (Python package) | Diagrams in `gtopt_diagram`; runs the Graphviz `dot` program | MIT (the Graphviz program is EPL-1.0) |
| [pyvis](https://github.com/WestHealth/pyvis) | Interactive HTML diagrams | BSD-3-Clause, West Health Institute |
| [CairoSVG](https://github.com/Kozea/CairoSVG) | SVG to PNG/PDF in `gtopt_diagram` | LGPL-3.0-or-later |
| [Pillow](https://python-pillow.org) (optional `diagram` extra) | PNG viewer fallback in `gtopt_diagram` | MIT-CMU (HPND) |
| [Matplotlib](https://matplotlib.org) (optional `plot` / `figures` extras) | Plots in `gtopt_monitor` and `tools/figures` | Matplotlib licence (PSF-based, BSD-compatible) |
| [highspy](https://github.com/ERGO-Code/HiGHS) (optional `highs` extra) | IIS analysis in `gtopt_check_lp` | MIT |
| [Flask](https://flask.palletsprojects.com) | GUI service | BSD-3-Clause |
| [Playwright](https://playwright.dev/python/) (optional `figures` extra; documentation screenshots only) | `tools/figures` | Apache-2.0 |

`[project].dependencies` and the extras in `scripts/pyproject.toml`
are the authoritative lists; `scripts/requirements*.txt` mirror them.

Their transitive dependencies are permissive (BSD, MIT, Apache-2.0,
PSF, ISC), except `certifi` and `tqdm` (pulled in by pandapower), which
are MPL-2.0. The development tools (pytest, hypothesis, ruff, pylint,
mypy, coverage) are only used for testing; `hypothesis` is MPL-2.0.

### Node.js (`webservice/`, `guiservice-plus/`)

The direct dependencies are Next.js, React, archiver, uuid and yauzl
(`webservice/`), and Radix UI, React Flow, Recharts, TanStack Query and
Table, react-hook-form, zod, zustand, cmdk, lucide-react, sonner, clsx,
class-variance-authority, tailwind-merge and tailwindcss-animate
(`guiservice-plus/`), all under MIT, ISC or Apache-2.0. The installed
trees are mostly MIT, ISC, BSD and Apache-2.0, with these exceptions:
Next.js bundles `@vercel/og` (MPL-2.0) and depends on `caniuse-lite`
(CC BY 4.0, browser-support data); Next.js optionally uses `sharp`,
which loads libvips (LGPL-3.0-or-later); the build tool `lightningcss`
and the test tool `axe-core` are MPL-2.0.

### Loaded by the browser from a CDN

These files are not in the repository. The GUI pages or the HTML
written by `gtopt_diagram` reference them, and the browser downloads
them when the page is opened.

| Component | Where | Licence |
|---|---|---|
| [vis-network](https://github.com/visjs/vis-network) 9.1.9 | `guiservice` topology view; pyvis HTML output | Apache-2.0 OR MIT |
| [Mermaid](https://github.com/mermaid-js/mermaid) 11 | `gtopt_diagram` HTML output | MIT |
| [Font Awesome Free](https://fontawesome.com) 6.5.2 | `gtopt_diagram` HTML output | Icons CC BY 4.0, fonts SIL OFL 1.1, code MIT; Fonticons, Inc. |
| [Inter](https://github.com/rsms/inter), [Plus Jakarta Sans](https://github.com/tokotype/PlusJakartaSans), [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) (Google Fonts) | `guiservice-plus` | SIL OFL 1.1 |

The GTOpt logo in `docs/images/logo/` uses glyph outlines of DejaVu
Sans Bold (Bitstream Vera / DejaVu font licence, which allows this
use).

## 5. External programs

GTOpt can run these programs when they are installed. They are separate
programs, not linked into or distributed with GTOpt:

| Program | Used by | Licence |
|---|---|---|
| [mdbtools](https://github.com/mdbtools/mdbtools) (`mdb-tables`, `mdb-export`) | `cen2gtopt` and `plexos2gtopt`, to read PLEXOS / CEN `.accdb` solutions | GPL-2.0-or-later (programs), LGPL-2.0-or-later (library) |
| [Graphviz](https://graphviz.org) (`dot`) | `gtopt_diagram` | EPL-1.0 |
| `xz`, `gzip`, `bzip2`, `zstd`, `lz4` | `gtopt_compress_lp`, `plexos_compress_case`, `plp_compress_case` | 0BSD / public domain, GPL-3.0-or-later, bzip2 licence, BSD-3-Clause / GPL-2.0, BSD-2-Clause / GPL-2.0 |
| `highs`, `clp`, `cbc` command-line solvers | `gtopt_check_lp` | As in section 1 |
| Julia with UnitCommitment.jl / PowerSimulations.jl | `tools/ucjl_solve_golden.jl`, `tools/sienna_solve_golden.jl` (only to regenerate reference results) | MIT (Julia); packages as in section 1 |

## 6. Licence compatibility

GTOpt is offered under `AGPL-3.0-or-later` (with the solver plugin
exception) or under the GTOpt commercial licence. The third-party
licences above fit both:

| Licence family | Components | AGPL-3.0 | Commercial licence |
|---|---|---|---|
| Permissive: MIT, MIT-CMU, ISC, BSD-2/3-Clause, BSL-1.0, Apache-2.0, zlib, Unlicense, CC0, 0BSD, BlueOak, PSF, Matplotlib | Most C++, Python and JavaScript dependencies, including pandapower, NetworkX, SciPy and pyvis; HiGHS, Arrow, Boost, SCIP, cuOpt | Compatible (Apache-2.0 is compatible with GPLv3/AGPLv3) | Compatible; keep the copyright and licence notices |
| MPL-2.0 (file-level copyleft) | SDDP.jl and PSRClassesInterface.jl test data, `certifi`, `tqdm`, `@vercel/og` | Compatible (MPL-2.0 §3.3, GPL as secondary licence) | Compatible; MPL files stay under MPL and their source must remain available |
| LGPL-3.0-or-later | `cairosvg`, libvips via `sharp` (optional) | Compatible | Compatible when used as separate, replaceable libraries, as here |
| EPL-2.0 | COIN-OR CLP, CBC, Osi, CoinUtils | Not GPL-compatible by itself; allowed by clause (a) of the [solver plugin exception](LICENSE-EXCEPTION.md) | Compatible; the solver is a separate library under its own licence |
| CC BY 4.0 (data) | pglib-opf data, `caniuse-lite`, Font Awesome icons (CDN) | Compatible for data; keep the attribution | Compatible; keep the attribution |
| SIL OFL 1.1 (fonts) | Inter, Plus Jakarta Sans, JetBrains Mono (CDN) | Fonts are not combined with GTOpt code | Fonts are not combined with GTOpt code |
| Proprietary | CPLEX, Gurobi, MindOpt, CUDA | Allowed by clause (b) of the solver plugin exception; not distributed with GTOpt | Each user needs a licence from the vendor |

No library that GTOpt links or imports is under GPL, AGPL, SSPL or a
non-commercial licence, so neither the open-source nor the commercial
distribution of GTOpt is restricted by them. GPL software appears only
in these forms, none of which affects GTOpt's licence:

- separate programs that GTOpt runs but does not distribute (mdbtools,
  gzip; section 5);
- a project whose documented example was used only as a cross-check
  for a test built from public factual data, with no code, data files
  or tables included (GenX, GPL-2.0).

When a dependency or a data set is added, check its licence against
this table and list it in this file.
