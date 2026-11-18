# Licensing

GTOpt (Generation, Transmission and storage Optimization) is an open-source
solver for the optimization and analysis of power-system generation,
transmission and storage.

GTOpt uses three licences, each for a clearly delimited part:

| Part | Licence |
|---|---|
| **GTOpt core**: solver library, `gtopt` binary, solver plugins, Python tools, web and GUI services | **AGPL-3.0-or-later** + Solver Plugin Exception, **or** the GTOpt Commercial License |
| **GTOpt Extension SDK**: the public plugin interface headers listed below | **Apache-2.0** |
| **White paper**: text and figures in [`docs/white_paper/`](docs/white_paper/) (LaTeX sources and compiled PDF) | **CC BY 4.0** |
| Third-party files and data shipped in this repository | Their own licences, see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) |

## GTOpt core: dual licence

You may use the GTOpt core under **either** of:

1. the **GNU Affero General Public License v3.0 or later**
   ([`LICENSE`](LICENSE)), together with the **GTOpt Solver Plugin
   Exception** ([`LICENSE-EXCEPTION.md`](LICENSE-EXCEPTION.md)), an
   additional permission under AGPLv3 section 7 that allows GTOpt to be
   combined with EPL-2.0 (COIN-OR) and proprietary optimization solvers
   (CPLEX, Gurobi, MindOpt, cuOpt/CUDA) through its solver-plugin
   interface; or
2. the **GTOpt Commercial License**
   ([`LICENSE-COMMERCIAL.md`](LICENSE-COMMERCIAL.md)), for organizations
   that want to embed, modify, redistribute or offer GTOpt as a service
   without the AGPL's copyleft obligations.

Core source files carry the SPDX identifier

```
SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
```

or, in C++ headers, a `@copyright ... Dual-licensed, see LICENSING.md` line.

The AGPL is the GPL plus one extra condition (section 13): if you **modify**
GTOpt and let users interact with the modified version **over a network**
(for example a hosted solve service or web application), you must offer
those users the corresponding source code of your modified version. Running
an unmodified GTOpt, or using GTOpt internally, adds no obligation beyond
the GPL's.

## GTOpt Extension SDK: Apache-2.0

The following headers form the public extension interface and are licensed
under the **Apache License 2.0** ([`LICENSE-APACHE-2.0`](LICENSE-APACHE-2.0)),
so that plugins and extensions, including closed-source and commercial
ones, can be built against them without any copyleft obligation:

- `include/gtopt/solver_backend.hpp` (solver plugin interface)
- `include/gtopt/solver_options.hpp`, `include/gtopt/solver_enums.hpp`,
  `include/gtopt/solver_resource.hpp`, `include/gtopt/basis.hpp`
- `include/gtopt/basic_types.hpp`, `include/gtopt/enum_option.hpp`,
  `include/gtopt/as_label.hpp`, `include/gtopt/utils.hpp`

These files carry `SPDX-License-Identifier: Apache-2.0`. The SDK will grow
(Python client, case-format schemas, app manifest) as the extension
ecosystem develops; every SDK file is marked with that identifier.

## White paper: CC BY 4.0

The text and figures of the GTOpt white paper in
[`docs/white_paper/`](docs/white_paper/) (LaTeX sources, bibliography,
abstract and compiled PDF) are licensed under the
[Creative Commons Attribution 4.0 International licence](https://creativecommons.org/licenses/by/4.0/),
the same licence as its arXiv preprint: they may be shared and adapted
for any purpose, provided the paper is cited (see
[`PUBLICATIONS.md`](PUBLICATIONS.md)).  The build scripts in that
directory follow the licence of the GTOpt core.

## Extensions and apps

| Kind of extension | Licence it may use |
|---|---|
| Solver or other plugin built only against the Extension SDK | Any, including proprietary |
| Tool or app that runs GTOpt as a separate program and exchanges data through its documented formats (JSON / Parquet / CSV cases and outputs, command line, web-service REST API) | Any, including proprietary |
| Code that links GTOpt core libraries, or imports GTOpt core Python modules | AGPL-3.0-or-later (or a GTOpt Commercial License) |

Publishing an extension does not give the right to use the GTOpt name or
logo beyond what [`TRADEMARKS.md`](TRADEMARKS.md) allows; in particular
"GTOpt Store", "GTOpt Marketplace", "GTOpt Certified" and "GTOpt Cloud" are
reserved.

## Which licence do I need?

| Use | Licence |
|---|---|
| Research, teaching, internal studies, consulting reports | AGPL (free) |
| Modifying GTOpt and publishing your changes under the AGPL | AGPL (free) |
| Running GTOpt with CPLEX, Gurobi, MindOpt or COIN-OR solvers | AGPL + Solver Plugin Exception (free) |
| Writing a plugin or app against the Extension SDK or the data formats | Any licence you choose (free) |
| Offering an unmodified GTOpt as a network service | AGPL (free) |
| Offering a modified GTOpt as a network service without publishing the changes | Commercial |
| Shipping GTOpt inside a closed-source product or appliance | Commercial |
| Needing warranty, IP indemnity or a support SLA | Commercial and/or support contract |

Licences for proprietary solvers (CPLEX, Gurobi, MindOpt, ...) are **not**
included in either option; obtain them from their vendors.

Copyright (c) 2024-2026 Marcelo Matus and the GTOpt contributors listed in
[`AUTHORS`](AUTHORS). Contributions are accepted under the
[Contributor License Agreement](CLA.md), which allows the project to keep
offering all of the licences above.

## Commercial licensing

Contact: Marcelo Matus — <marcelomatusacuna@gmail.com>

## Support, maintenance and services

Professional support, maintenance, training, case migration
(PLP / PLEXOS → GTOpt) and custom development are available from
**Black Bear Engineering SpA** — <mmatus@blackbeareng.cl>.
See [`PARTNERS.md`](PARTNERS.md).
