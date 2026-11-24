# gasnet2gtopt — gas-network description to gtopt planning

`gasnet2gtopt` converts a `*.gasnet.json` description of a natural-gas
network and its power system into a gtopt planning JSON with the native
gas elements (`GasNode`, `GasSupply`, `GasDemand`, `GasPipe`,
`GasCompressor`, `GasTurbine`).  The formulation is in
[mathematical-formulation.md §5.19](../formulation/mathematical-formulation.md),
the JSON fields in [input-data.md §3.27](../input-data.md), and the
benchmark cases in the [Gas Network guide](../gas-network.md).

## Usage

```bash
gasnet2gtopt case.gasnet.json -o out/planning.json \
  [--legacy] [--season winter|summer] [--compressor off|series] \
  [--linepack] [--nblocks N] [--segments N] \
  [--fuel-model fixed_fraction|saldarriaga] [--cs-gamma G] \
  [--booster off|on] [--booster-drive gas|electric] \
  [--plant-pmin GEN=BAR ...] [--derate] [--no-plant-pressure] \
  [--source-version 1]
```

It writes `planning.json` and a `planning.meta.json` sidecar (node,
pipe, compressor and demand name → uid maps, and the resolved schema
version under `source_format`).  The same build is available from
Python:

```python
from gasnet2gtopt import BuildOptions, build_native
planning, meta = build_native(desc, BuildOptions(compressor="series"))
```

| Option | Default | Effect |
|--------|---------|--------|
| `--legacy` | off | Emit the earlier prototype form (`DecisionVariable` / `UserConstraint`); the same LP, radial networks only, kept for comparison |
| `--season` | `winter` | Selects the season entry of profile / season-dict values |
| `--compressor` | `off` | `off`: transmission stations become lossless regulators (`max_ratio` 1, no fuel, bypass); `series`: stations compress with their fuel model |
| `--linepack` | off | Enable line pack on the pipes marked `linepack: true` |
| `--nblocks` | 24 | Hourly blocks of the single chronological stage (a daily profile is repeated over several days) |
| `--segments` | per pipe (8) | Weymouth tangents on every pipe |
| `--fuel-model` | per station | Override the fuel model of the transmission stations |
| `--cs-gamma` | adiabatic | Override Saldarriaga's γ |
| `--booster` | `off` | Switch the stations with `role: fuel_gas_booster` on (`series`) or off (regulator) |
| `--booster-drive` | per station | Force the booster drive (`gas` or `electric`) |
| `--plant-pmin GEN=BAR` | from the link | Override a plant's minimum fuel-gas pressure [bar abs] |
| `--derate` | off | Add the derate line up to `derate_nominal_factor` (1.15) × `min_pressure` |
| `--no-plant-pressure` | off | Drop the plant pressure gates |

## Description format (schema `1`)

Top level: `name`, `profiles` (named per-block lists), optional
`gas_unit`, optional `schema_version`, `gas_network` and `power_system`
(a gtopt `system` fragment: buses, generators, demands, lines).
`gas_network` holds:

| Array | Maps to | Notes |
|-------|---------|-------|
| `gas_node_array` | `gas_node_array` | `pmin`, `pmax`, `p_init`, `elevation`; network `pressure_tiebreak` (1e-4) becomes `pressure_cost = −tiebreak` |
| `gas_pipeline_array` | `gas_pipe_array` | `node_a`/`node_b`; geometry, `weymouth_k`, `segments`, `linepack`, `linepack_p_ref` → `linepack_pressure_ref` |
| `gas_transfer_array` | `gas_pipe_array` with `weymouth_mode: none` | Capacity-only arc (no transfer cost natively) |
| `compressor_station_array` | `gas_compressor_array` | `node_in`/`node_out`, `role` → `type`, `rating_mw` → `power_max`, `bidirectional` valves |
| `gas_source_array` | `gas_supply_array` | `node`, `fmax` (number, season dict or profile name), `cost`, `type`, `volume` |
| `gas_demand_array` | `gas_demand_array` | `node`, `load` (profile), `fail_cost` |
| `generator_gas_link_array` | `gas_turbine_array` | `generator`, `gas_node`, `heat_rate`, `min_pressure`, `pressure_band`, `derate_nominal_factor`, `derate_min_output` |

`network_model` is `radial` (default; the converter checks that pipes
and stations form a forest) or `meshed` (every pipe gets
`weymouth_mode: linearized`).  Network gas properties and the SLP
settings (`gas_specific_gravity`, `gas_temperature`, `compressibility`,
`slp_*`, `linepack_cyclic`) are copied to `options.gas_options`.
Extra PAMPL rows can be given in a top-level `user_constraints` list.

Units: flow km³/h (thousand standard m³ per hour; 1 MMm³/d = 41.667
km³/h), pressure bar abs, cost $/km³, heat rate km³/MWh.

## Bundled descriptions

| File | Content |
|------|---------|
| `cases/mini3.gasnet.json` | Three-node Weymouth chain with two gas plants |
| `cases/tiny_compressor.gasnet.json` | One compressor station (fuel models, drives) |
| `cases/tiny_linepack.gasnet.json` | Line pack over a daily profile |
| `cases/tiny_plant.gasnet.json` | Plant pressure gate, booster and derate |
| `cases/literature/*.gasnet.json` | Saldarriaga Fig. 2.2(b), GasLib-11 (radial), Belgian `A1` (radial); see its [README](../../scripts/gasnet2gtopt/cases/literature/README.md) |

The generated cases under `cases/gas_*` are listed with their sources
and licences in the [Gas Network guide](../gas-network.md#benchmark-cases).
The stylised central-Chile networks (`central_chile*.gasnet.json`) and the
Peru / Camisea study scenarios live in the external case repository
(`$GTOPT_CASES_DIR/chile/gas_network/central_chile_renca/gasnet/`,
`$GTOPT_CASES_DIR/peru/gas_network/camisea/gasnet/`).

## Tests

`scripts/gasnet2gtopt/tests/` covers the physics helpers, the radial
guard, both emitters, the CLI and the schema version.
`test_gtopt_integration.py` (marker `integration`, needs a gtopt binary)
solves the tiny, literature and benchmark cases and compares the
objective with the independent reference to 1e-5 relative; CTest runs
it as `gas_integration_*`.  The central-Chile (`central_chile_renca`,
`multi_day_linepack`) and Peru / Camisea (`peru_camisea`, slow
`peru_kp43`) groups read the external case repository and skip without
`GTOPT_CASES_DIR`.
