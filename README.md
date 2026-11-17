# GTOpt

[![Ubuntu](https://github.com/gtopt-community/gtopt/actions/workflows/ubuntu.yml/badge.svg)](https://github.com/gtopt-community/gtopt/actions/workflows/ubuntu.yml)
[![Style](https://github.com/gtopt-community/gtopt/actions/workflows/style.yml/badge.svg)](https://github.com/gtopt-community/gtopt/actions/workflows/style.yml)
[![Docs](https://github.com/gtopt-community/gtopt/actions/workflows/docs.yml/badge.svg)](https://github.com/gtopt-community/gtopt/actions/workflows/docs.yml)
[![License: AGPL-3.0 / commercial](https://img.shields.io/badge/license-AGPL--3.0%20%7C%20commercial-blue)](LICENSING.md)

**GTOpt — Generation, Transmission and storage Optimization** — is an
open-source solver for planning and operating power systems. It builds sparse
LP/MIP models of multi-stage systems and minimizes the expected discounted
cost of investment and operation (CAPEX + OPEX):

- **Expansion planning** of generation, transmission and storage (GTEP).
- **Hydrothermal scheduling** with SDDP and cascade decomposition.
- **Unit commitment and economic dispatch**, single-bus or DC OPF with
  losses.

GTOpt is pronounced *G-top* (/ˈdʒiː tɒp/). The command-line binary is
`gtopt`.

## Features

- **Network**: single bus or DC power flow (Kirchhoff), line losses with
  several piecewise formulations, transmission expansion.
- **Storage and hydro**: batteries, pumped hydro, reservoirs and cascades,
  irrigation rights, water values.
- **Unit commitment**: tight three-bin MIP formulation, start-up tiers,
  ramps, minimum up/down times, piecewise heat rates, emissions.
- **Methods**: monolithic LP/MIP, SDDP and multi-level cascade SDDP, with
  an OptGen-style investment master.
- **Solvers as plugins**: HiGHS, CPLEX, Gurobi, MindOpt, SCIP, CBC/CLP and
  cuOpt, loaded at run time.
- **Data**: JSON cases with Parquet/CSV time series; converters from PLP,
  PLEXOS, PSS/E, SDDP, pandapower and Excel.
- **Interfaces**: command line, Python tools, a browser GUI and a REST web
  service.

## Install

On Ubuntu 24.04+ or Debian 13+:

```bash
curl -fsSL https://raw.githubusercontent.com/gtopt-community/gtopt/master/install.sh | bash
```

The script installs the system packages, builds GTOpt from source and
installs it in `/usr/local` (it asks for `sudo`). Useful options:

```bash
# Install in your home directory, plus the Python tools (plp2gtopt, plexos2gtopt, ...)
curl -fsSL https://raw.githubusercontent.com/gtopt-community/gtopt/master/install.sh \
  | bash -s -- --prefix "$HOME/.local" --python
```

Run `install.sh --help` for all options. For other platforms, manual builds
and build options, see the **[Building Guide](BUILDING.md)**.

At least one solver backend must be available at run time; the installer
brings the open-source CBC/CLP. `gtopt --solvers` lists what was found.

## Quick start

```bash
git clone https://github.com/gtopt-community/gtopt.git
cd gtopt/cases/c0
gtopt system_c0.json
```

`cases/c0` is a single-bus system with one generator and one demand and
capacity expansion over 5 stages. Results are written to `output/`, one
file per component; status `0` in `solution.csv` means the optimum was
found.

Common options:

```bash
gtopt case.json --set output_directory=results/   # where to write results
gtopt case.json --set use_single_bus=true         # ignore the network
gtopt case.json --solver highs                    # pick a solver backend
```

Next steps: the **[first-solve walkthrough](docs/planning-guide.md#quickstart-your-first-solve)**
and the **[Usage Guide](docs/usage.md)**.

## Tools and interfaces

| | What it is | Docs |
|---|---|---|
| **Python tools** | Converters (`plp2gtopt`, `plexos2gtopt`, `psse2gtopt`, `sddp2gtopt`, `pp2gtopt`, `igtopt`), diagrams, `run_gtopt` and checkers | [Scripts Guide](docs/scripts-guide.md) |
| **GUI** | Browser interface to build, solve and inspect cases (`gtopt_gui`) | [GUI and web service](docs/interfaces.md) |
| **Web service** | REST API to submit cases and fetch results (`gtopt_websrv`) | [GUI and web service](docs/interfaces.md) |
| **C++ library** | The solver as a library, plus the Apache-2.0 Extension SDK for solver plugins | [API reference](https://gtopt-community.github.io/gtopt/) |

## Documentation

All documentation is indexed in **[docs/README.md](docs/README.md)**. The
main entry points:

- [Understanding GTOpt](docs/overview.md): architecture, data flow and
  repository layout.
- [Planning Guide](docs/planning-guide.md): time structure, system elements,
  JSON format and worked examples.
- [Mathematical Formulation](docs/formulation/mathematical-formulation.md):
  the full LP/MIP model.
- [SDDP](docs/methods/sddp.md) and [Cascade](docs/methods/cascade.md)
  methods.
- [Comparison with other tools](docs/tools/comparison.md): PLP, pandapower,
  PyPSA and GenX.

## Citing GTOpt

If you use GTOpt in your work, please cite it as described in
[`CITATION.cff`](CITATION.cff) (the "Cite this repository" button on
GitHub). Papers about GTOpt and its predecessor FESOP are listed in
[`PUBLICATIONS.md`](PUBLICATIONS.md).

## Contributing

Contributions are welcome: see [`CONTRIBUTING.md`](CONTRIBUTING.md). Pull
requests need a one-time [Contributor License Agreement](CLA.md), which the
CLA bot requests on your first PR. Questions and ideas go to
[Discussions](https://github.com/gtopt-community/gtopt/discussions);
security issues to [`SECURITY.md`](SECURITY.md).

## License

The GTOpt core is **dual-licensed**: AGPL-3.0-or-later with the
[solver plugin exception](LICENSE-EXCEPTION.md), or a
[commercial license](LICENSE-COMMERCIAL.md). The Extension SDK (the
solver-plugin interface headers) is Apache-2.0, so plugins and apps may use
any licence. See [`LICENSING.md`](LICENSING.md) and
[`TRADEMARKS.md`](TRADEMARKS.md). Third-party components keep their own
licences; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Copyright (c) 2024-2026 Marcelo Matus and [contributors](AUTHORS).

## Support and maintenance

Professional support, maintenance, training and PLP / PLEXOS → GTOpt
migration services are available from **Black Bear Engineering SpA**
(<mmatus@blackbeareng.cl>). See [`PARTNERS.md`](PARTNERS.md).
