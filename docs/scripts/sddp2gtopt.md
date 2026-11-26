# sddp2gtopt — PSR SDDP to GTOpt Converter

Converts a **PSR SDDP** (*Stochastic Dual Dynamic Programming*, by PSR
Inc.) case directory into a GTOpt JSON planning, mirroring the role
[`plp2gtopt`](plp2gtopt.md) plays for PLP cases.

---

## Overview

PSR SDDP is the commercial hydrothermal-dispatch software developed by
PSR Inc. (Rio de Janeiro), used across Latin America (Brazil, Chile,
Peru, Uruguay, Colombia, …) for medium- and long-term operation
studies.  An SDDP study is stored as:

- A flat collection of fixed-format `.dat` files (`sistem.dat`,
  `chidros1.dat`, `ctermis1.dat`, `hinflw.dat`, `htopol.dat`,
  `deme01s1.dat`, …).
- A **typed JSON snapshot** `psrclasses.json` written by the SDDP GUI
  alongside the `.dat` files when a study is saved.

`sddp2gtopt` has **two interchangeable front-ends** (auto-detected from
the input directory), both feeding the same entity IR + writer:

1. **`psrclasses.json`** — the typed JSON snapshot the SDDP GUI saves.
2. **Raw PSR `.dat`** — the flat file collection real PSR SDDP / NCP
   deployments ship (e.g. the Guatemalan AMM weekly/daily cases), which
   have **no** `psrclasses.json`.  Parsed by the modular per-file readers
   in [`dat_parsers.py`](../../scripts/sddp2gtopt/dat_parsers.py)
   (each subclassing the shared
   `gtopt_shared.base_parser.BaseTextParser`) and orchestrated by
   [`dat_loader.py`](../../scripts/sddp2gtopt/dat_loader.py).

Both produce a single-bus GTOpt planning that `gtopt --lp-only` ingests
directly.  See [the `.dat` front-end](#the-dat-front-end-psr-sddp--ncp)
below.

> ⚠️  **Two flavours of "SDDP"**: this tool targets the **PSR Inc.**
> commercial format only, *not* the academic Julia package
> [SDDP.jl](https://sddp.dev) (Oscar Dowson) which uses
> StochOptFormat (`.sof.json`) and a different schema.

### Who needs this tool?

- Engineers migrating PSR SDDP archives to GTOpt.
- Teams running PLP and SDDP in parallel (e.g. CEN-Chile) who want a
  single solver to cross-validate both dialects.
- Automation pipelines that already produce SDDP cases.

---

## Installation

Installs as part of the GTOpt scripts package:

```bash
pip install ./scripts        # registers sddp2gtopt on PATH
sddp2gtopt --version
sddp2gtopt --help
```

No extra dependencies beyond what `plp2gtopt` already requires
(`numpy`, `pandas`, `pyarrow`).

---

## Quick start

```bash
# Show what's in a case (no conversion)
sddp2gtopt --info  /path/to/sddp_case

# Schema sanity check (exit 0 if OK)
sddp2gtopt --validate /path/to/sddp_case

# Convert (default: writes gtopt_<case>/gtopt_<case>.json next to the case;
# an input named sddp_X writes gtopt_X/gtopt_X.json)
sddp2gtopt /path/to/sddp_case

# Explicit output dir
sddp2gtopt -i /path/to/sddp_case -o /path/to/out

# End-to-end smoke test
sddp2gtopt sddp_demo -o gtopt_demo
gtopt --lp-only -s gtopt_demo/gtopt_demo.json
```

---

## CLI options

| Flag                          | Default | Description                                       |
|-------------------------------|---------|---------------------------------------------------|
| `INPUT_DIR` (positional)      | —       | SDDP case dir (alternative to `-i`)               |
| `-i, --input-dir DIR`         | —       | Same as positional, takes precedence              |
| `-o, --output-dir DIR`        | inferred| Output dir, next to the input (`sddp_X` → `gtopt_X`, else `gtopt_<X>`); the JSON is `<dir>/<dir-name>.json` |
| `--hydro-cost $/MWh`          | `0`     | Uniform hydro water-value stand-in (no-storage .dat)|
| `--import-limit MW`           | —       | `.dat` path: cap aggregate interconnection import (GUA↔MEX ≈ 200); see [Interconnection imports](#interconnection-imports) |
| `--blocks-per-stage K`        | `0`     | `0` = single-stage monolithic; divisor of horizon → daily stages → sddp/cascade |
| `--num-scenarios N`           | all     | Block studies: keep the first N historical series |
| `--method {sddp,monolithic,cascade}` | `sddp` | Block studies: planning method written to the JSON |
| `--deficit-segments {first,all,split}` | `first` | Block studies: `first` = one demand per bus priced at the first deficit segment; `all` = native `model_options.demand_fail_segments` curve; `split` = one `<demand>_deficit<k>` demand per segment (see [Deficit cost curve](#deficit-cost-curve---deficit-segments)) |
| `--info`                      | off     | Print case summary and exit                       |
| `--validate`                  | off     | Run schema sanity checks and exit                 |
| `--sanity-report PATH` | not set | Write the post-conversion data-quality report as JSON (the summary is always logged): per-kV `|X|/V²` / `R/V²` distribution and lines below the tie threshold, kV-class impedance outliers, X/R, non-standard / mistyped bus kV, zero-rated units (and those still committed), profiles outside [0, 1], reservoirs without `emin` under an agreement reserve level, water-right rewards above the demand fail cost.  Same report on an existing case: `python -m gtopt_shared.sanity_report case.json` |
| `-l, --log-level LEVEL`       | `INFO`  | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`|
| `--no-color`                  | off     | Plain console output                              |
| `--operator {auto,none,aemo,amm,cen,coes,eor,xm}` | `auto` | Operator whose conventions apply (detected from the input); logged, does not change the output yet |
| `--source-version X`          | detected| SDDP layout generation (`16`, `17`); see [Supported source versions](#supported-source-versions) |
| `-V, --version`               | —       | Print version and exit                            |

---

## Supported source versions

| Version | Supported | Tested | Notes |
|---------|-----------|--------|-------|
| `16` (default) | yes | yes — `tests/data/dat_case` | Fixed-width layouts of SDDP releases before 17 (`dcirc` with or without `$version=`, header-located columns) |
| `17` | yes | yes — `tests/data/sddp_block_case` | Adds the `!Code,` CSV layouts (`dcirc`, `mcirc`, `ccombu`, `gndsite`), per-table unit overrides, binary `blrenw` profiles |

**Detection**: a `.dat` whose first three lines hold a `!Code,` header →
`17`.  Otherwise undetectable (the 17 reader also accepts fixed-width
files; `psrclasses.json` carries no release) and the default applies.  The
resolution is logged by the converter.

`--source-version X` overrides the detected version.  An unknown version
(detected or given) falls back to the closest supported one with a
warning; a supported but untested version is kept with a warning.  The
same list is printed by `--help` (generated from `SOURCE_FORMAT` in
`sddp2gtopt/source_version.py`).  See
[Supported source versions](../scripts-guide.md#supported-source-versions)
for every converter.

---

## PSR SDDP input file format

A vanilla SDDP case directory (e.g. the vendored
[`tests/data/case0/`](../../scripts/sddp2gtopt/tests/data/case0/)) contains
~15 files.  v0 reads only `psrclasses.json`; the rest are listed for
context — they will be parsed by v1+ when present without a JSON
snapshot.

| File              | Format    | Used in v0 | Holds                                                              |
|-------------------|-----------|:----------:|--------------------------------------------------------------------|
| `psrclasses.json` | JSON      |  ✅        | Typed snapshot of every PSR collection (the v0 source of truth)    |
| `sddp.dat`        | text      |    —      | Study control: stages, blocks, deficit cost, discount rate         |
| `sistem.dat`      | text      |    —      | System list (one per area)                                         |
| `chidros1.dat`    | text      |    —      | Hydro plant params (Vmin/Vmax, Qmin/Qmax, FP, head/tail curves)    |
| `ctermis1.dat`    | text      |    —      | Thermal plant params (G/CEsp segments, Comb/fuel index)            |
| `ccombus1.dat`    | text      |    —      | Fuel definitions per system (Custo, EmiCO2, PC)                    |
| `htopol.dat`      | text      |    —      | Hydro topology (turbine → spill → downstream)                      |
| `hparam.dat`      | binary    |    —      | AR-P inflow model coefficients                                     |
| `hinflw.dat`      | text      |    —      | Historical inflow series (year × month × station)                  |
| `deme01s1.dat`    | text      |    —      | Demand profile (block × stage)                                     |
| `mhdadds1.dat`    | text      |    —      | Hydro additional/maintenance data                                  |
| `coral.dat`       | text      |    —      | Solver settings                                                    |
| `estima.dat`      | text      |    —      | Inflow-estimation parameters (AR-P order, year range)              |
| `unimon.dat`      | text      |    —      | Currency / unit conversions                                        |
| `index.dat`       | text      |    —      | Catalog of 325 SDDP output variables (used as parity reference)    |

### What's inside `psrclasses.json`

A flat top-level dictionary `{collection_name: [entity, ...]}`.  Every
entity carries:

- `reference_id` (int) — the canonical cross-reference key.
- `code` (int) — 1-based PSR uid (often shown in SDDP reports).
- `name` (str) — human label.
- `classType` (int) — internal type tag (mostly ignorable).

References between entities (e.g. `PSRThermalPlant.fuels`,
`PSRDemand.system`) are arrays/scalars of `reference_id`.
:class:`sddp2gtopt.psrclasses_loader.PsrClassesLoader` indexes them so
resolution is O(1).

### Collections seen in `case0`

```
PSRStudy                       1
PSRSystem                      1
PSRDemand                      1
PSRDemandSegment               1
PSRFuel                        2
PSRThermalPlant                3
PSRHydroPlant                  1
PSRGaugingStation              2
PSRNetwork                     1   (container — ignored)
PSRNetworkDC                   1   (container — ignored)
PSRElectrificationNetwork      1   (container — ignored)
PSRHydrologicalNetwork         1   (container — ignored)
PSRHydrologicalPlantNetwork    1   (container — ignored)
PSRInterconnectionNetwork      1   (container — ignored)
PSRGasNetwork                  1   (container — ignored)
```

Container collections (the `*Network` family) wrap the actual entities
and currently carry no data we need; v0 ignores them.

---

## Mapping PSR SDDP elements → GTOpt

The conversion is a per-collection transformation.  Each PSR entity
type is parsed by a function in
[`sddp2gtopt/parsers.py`](../../scripts/sddp2gtopt/parsers.py) into a
typed dataclass from
[`sddp2gtopt/entities.py`](../../scripts/sddp2gtopt/entities.py); the
writer in
[`sddp2gtopt/gtopt_writer.py`](../../scripts/sddp2gtopt/gtopt_writer.py)
then assembles the GTOpt JSON.

### High-level table

| PSR collection / field           | GTOpt target                       | v0 status         |
|----------------------------------|------------------------------------|-------------------|
| `PSRStudy`                       | top-level `options` + `simulation` | ✅ full            |
| `PSRSystem`                      | `system.bus_array` (synthesised)   | ✅ single-system   |
| `PSRDemand` + `PSRDemandSegment` | `demand_array`                     | ✅ inline `lmax`   |
| `PSRFuel`                        | absorbed into thermal `gcost`      | ✅                 |
| `PSRThermalPlant`                | `generator_array` (thermal)        | ✅ collapsed bid   |
| `PSRHydroPlant`                  | `generator_array` (hydro)          | ⚠️ flattened       |
| `PSRGaugingStation`              | inflow time-series                 | ⏳ deferred (v2)   |
| `htopol.dat` topology            | reservoir/turbine cascade          | ⏳ deferred (v2)   |
| `ccombus1.dat` / multi-bus       | extra `bus_array` + `line_array`   | ⏳ deferred (v3)   |
| `PSRInterconnection*`            | `line_array`                       | ⏳ deferred (v3)   |
| Multi-system cases               | multiple `system` blocks           | ⏳ deferred (v4)   |

### `PSRStudy` → `options` + `simulation`

| PSR field             | GTOpt target / formula                        |
|-----------------------|-----------------------------------------------|
| `Ano_inicial`         | reference year (logged, not in JSON)          |
| `Etapa_inicial`       | 1-based stage index (logged)                  |
| `Tipo_Etapa`          | sets per-stage hour budget (see below)        |
| `NumeroEtapas`        | length of `simulation.stage_array`            |
| `NumeroBlocosDemanda` | blocks per stage                              |
| `DeficitCost[0]`      | `options.demand_fail_cost`                    |
| `TaxaDesconto`        | `options.annual_discount_rate`                |
| `CurrencyReference`   | preserved on `SystemSpec.currency`            |

**Stage hour budget** (used to compute block durations and convert
demand from GWh → MW):

| `Tipo_Etapa` | Cadence    | Hours / stage |
|:-:           |------------|--------------:|
| 1            | weekly     | 168           |
| 2            | monthly    | 730           |
| 3            | trimester  | 2190          |
| other        | (fallback) | 730           |

These are nominal averages — calendar-aware durations are deferred to
v2 once `mhdadds*.dat` parsing lands.  Block duration is
`stage_hours / num_blocks`.

### `PSRSystem` → bus

v0 collapses each PSR system to a single synthesised bus
named `sys_<code>_bus`.  The bus carries every generator and demand
in that system.  Multi-bus topology (from `ccombus*.dat`) is the v3
deliverable.

| PSR field         | GTOpt target                                                  |
|-------------------|---------------------------------------------------------------|
| `code`            | derives the synthetic bus name (`sys_{code}_bus`)             |
| `name`            | logged, not in JSON                                           |
| `UnM`             | currency tag (logged, not in JSON)                            |

> Multi-system cases are **rejected** with a clear `ValueError`
> rather than silently collapsing all systems to one bus.

### `PSRFuel` → thermal cost component

Fuels are not standalone GTOpt entities; they only serve to compute
each thermal plant's `gcost`.

| PSR field      | Used as                                          |
|----------------|--------------------------------------------------|
| `Custo[0]`     | $/fuel-unit price (the `cost` factor)            |
| `UE`           | Fuel unit (informational, e.g. `MWh`, `m³`)      |
| `EmiCO2`       | logged; emission cost not yet wired in v0        |
| `reference_id` | resolved from `PSRThermalPlant.fuels[]`          |

### `PSRThermalPlant` → `generator_array`

Each thermal plant becomes one entry in `system.generator_array`.

| PSR field       | GTOpt field      | Notes                                                                |
|-----------------|------------------|----------------------------------------------------------------------|
| `name`          | `name`           | Falls back to `thermal_<code>` when blank                            |
| `code`          | `uid` (offset)   | GTOpt uids are sequential within the array, not direct copies        |
| `GerMin[0]`     | `pmin`           |                                                                      |
| `GerMax[0]`     | `pmax`           |                                                                      |
| `PotInst[0]`    | `capacity`       | Same as `pmax` for v0                                                |
| `G(i)`          | segment cap      | Up to 3 piecewise segments (i = 1..3); zero-cap segments dropped     |
| `CEsp(i,1)`     | × fuel cost      | Specific consumption (fuel-unit / MWh)                               |
| `fuels[0]`      | fuel reference   | `PSRFuel.reference_id` to look up the price                          |
| `CTransp[0]`    | (logged)         | Transport cost; not yet added to `gcost`                             |
| `system`        | bus picker       | Picks `sys_<system.code>_bus`                                        |

**Cost computation (single-segment v0)**:

```
gcost = min(over segments) [ CEsp(i,1) × fuels[0].Custo[0] ]
```

Worked example from `case0`:

| Plant     | `G(1)` | `CEsp(1,1)` | Fuel ref → Cost   | gcost ($/MWh) |
|-----------|-------:|------------:|-------------------|--------------:|
| Thermal 1 | 100    | 10.0        | Fuel 1 → 0.8      | **8.0**       |
| Thermal 2 | 100    | 15.0        | Fuel 1 → 0.8      | **12.0**      |
| Thermal 3 | 100    | 12.5        | Fuel 2 → 1.2      | **15.0**      |

> v0 collapses the up-to-3-segment bid curve to its **cheapest**
> segment, which preserves the merit order but loses the convex bid
> shape.  Multi-segment piecewise gcost is a v2 enhancement (writer
> already emits the `g_segments` list — only the writer needs an
> upgrade).

### `PSRHydroPlant` → `generator_array` (flattened)

Hydro plants are modelled as **zero-cost generators** in v0 — full
reservoir + turbine + inflow chains arrive in v2 once we parse
`htopol.dat` and the gauging-station series.

| PSR field    | v0 mapping                | v2+ target                          |
|--------------|---------------------------|--------------------------------------|
| `PotInst[0]` | generator `pmax` & `cap`  | `Turbine.pmax`                       |
| `Vmin[0]`    | (ignored)                 | `Reservoir.vmin`                     |
| `Vmax[0]`    | (ignored)                 | `Reservoir.vmax`                     |
| `Vinic`      | (ignored)                 | `Reservoir.vinic`                    |
| `Qmin[0]`    | (ignored)                 | `Turbine.qmin` / waterway lower band |
| `Qmax[0]`    | (ignored)                 | `Turbine.qmax`                       |
| `FPMed[0]`   | (ignored)                 | `Turbine.conversion_rate`            |
| `station`    | (ignored)                 | inflow series source                 |
| `htopol.dat` | (not parsed)              | `Junction` + `Waterway` cascade      |

The v0 limitation means hydro is "infinite-water" — every block can
dispatch up to `PotInst` MW for free, regardless of physical
availability.  This is acceptable for an LP-build smoke test on
`case0` (1 MWh/block hydro, no reservoir tightness) but unsafe for
real studies.

### `PSRGaugingStation` → inflow series (deferred)

v0 parses the station name + raw `Vazao` series for diagnostics
(`--info` shows the series length), but the inflows are **not wired
into the planning** until the hydro topology lands in v2.

### `PSRDemand` + `PSRDemandSegment` → `demand_array`

Each `PSRDemand` is one entry in `system.demand_array`; the time-series
profile comes from the matching `PSRDemandSegment` (linked via
`demand` reference_id).  v0 emits the profile inline as
`lmax[stage][block]`, no Parquet file.

| PSR source                          | GTOpt field   | Conversion                    |
|-------------------------------------|---------------|-------------------------------|
| `PSRDemand.name`                    | `name`        |                               |
| `PSRDemand.system`                  | `bus`         | bus picked from `PSRSystem`   |
| `PSRDemandSegment.Demanda(1)[s]`    | `lmax[s][b]`  | see formula below             |

**Unit conversion (GWh / stage  →  MW / block)**:

```
mw_block_b = (gwh_stage_s × 1000) / block_hours
```

where `block_hours = stage_hours(Tipo_Etapa) / num_blocks`.  When the
PSR series is **shorter** than `num_stages`, the tail is zero-padded;
**longer** series are truncated.  The same value is replicated across
every block within a stage (per-block load shape from `Duracao(i)`
weights is a v2 enhancement).

Worked example from `case0` (Tipo_Etapa = 2 → 730 h, 1 block):

| Stage | `Demanda(1)` (GWh) | `lmax` (MW)        |
|------:|-------------------:|-------------------:|
| 1     | 8.928              | 8.928 × 1000 / 730 ≈ **12.23** |
| 2     | 8.064              | 8.064 × 1000 / 730 ≈ **11.05** |

---

## Stages, blocks, scenarios

| GTOpt concept    | PSR source                              |
|------------------|-----------------------------------------|
| `stage_array`    | one entry per `NumeroEtapas`            |
| `block_array`    | `NumeroBlocosDemanda` blocks per stage  |
| `scenario_array` | always **1** in v0 (probability = 1)    |

Multi-scenario stochasticity (forward/backward Monte-Carlo from
`Series_Forward` / `Series_Backward` and the AR-P inflow model in
`hparam.dat`) is the v4 deliverable.  Until then the converted
planning is **deterministic** — single-scenario, expected-value
demand, and (in v2+) deterministic inflows.

---

## Output schema

The converter writes a single JSON file shaped like the small
reference cases under [`cases/c0`](../../cases/c0/system_c0.json) and
[`cases/s1b`](../../cases/s1b/s1b.json):

```json
{
  "options":   { "annual_discount_rate": …, "use_single_bus": true,  … },
  "simulation":{ "block_array": [...], "stage_array": [...], "scenario_array": [...] },
  "system":    { "name": "…", "bus_array": [...], "generator_array": [...], "demand_array": [...] }
}
```

The `psrclasses.json` and NCP `.dat` front-ends write every series inline (no data
files).  The multi-file block-case front-end
([below](#multi-file-psr-sddp-block-cases-demedbf-studies)) writes long
Parquet tables under the output directory, as plp2gtopt does:
`Generator/{pmax,pmin,pmin_fcost,gcost}`, `GeneratorProfile/profile`,
`Demand/lmax`, `Flow/discharge`, `Line/{active,tmax_ab,tmax_ba}`,
`Reservoir/{emax,soft_emin}`, `Waterway/{fmin,fmax}` and
`Turbine/{production_factor,capacity}`.

---

## Test fixture library

`scripts/sddp2gtopt/tests/data/` ships six standalone cases, each
small enough to read by hand:

| Fixture                | Source       | What it covers                                                     |
|------------------------|--------------|--------------------------------------------------------------------|
| `case0/`               | upstream PSR | Real PSR sample (1 system, 3 thermal, 1 hydro, 2 stations)         |
| `case_min/`            | hand-crafted | Smallest valid case (1 thermal, 1 demand, 1 stage, 1 block)        |
| `case_thermal_only/`   | hand-crafted | Thermals only, multi-fuel, 3 stages × 2 blocks                     |
| `case_two_systems/`    | hand-crafted | Two `PSRSystem` — must trigger a clear conversion error            |
| `case_bad_no_study/`   | hand-crafted | Missing `PSRStudy` — must fail `--validate`                        |
| `case_bad_truncated/`  | hand-crafted | Truncated JSON — must surface a parse error                        |

`case0/` is vendored from
[`psrenergy/PSRClassesInterface.jl`](https://github.com/psrenergy/PSRClassesInterface.jl)
under the Mozilla Public License 2.0 (see
`tests/data/LICENSE-PSRClassesInterface`).  The hand-crafted cases use
the same JSON schema PSR SDDP writes natively, so adding a new fixture
only requires authoring a `psrclasses.json` — no PSR install needed.

### Adding a fixture

1. Create `scripts/sddp2gtopt/tests/data/case_<name>/psrclasses.json`
   with at least `PSRStudy` and `PSRSystem`.
2. Add a session-scoped fixture to
   [`tests/conftest.py`](../../scripts/sddp2gtopt/tests/conftest.py)
   that returns the directory.
3. Reference it from any `test_*.py`; the fixture is injected via
   pytest argument names.

---

## The `.dat` front-end (PSR SDDP / NCP)

When the input directory has **no `psrclasses.json`** but does contain a
`sddp.dat`, `sddp2gtopt` switches to the raw-`.dat` front-end.  This is
the format real PSR SDDP / NCP deployments ship — including the
Guatemalan AMM weekly (`NCP`) and daily (`PDD_DIARIO`) cases.

### Files read

| file            | parser class    | → entity / use                                   |
|-----------------|-----------------|--------------------------------------------------|
| `sddp.dat`      | `ControlParser` | `StudySpec` (cadence, stages, deficit, discount) |
| `sistem.dat`    | `SystemParser`  | `SystemSpec` list                                |
| `ccombu*.dat`   | `FuelParser`    | fuel `Custo` ($/MWh) → thermal cost              |
| `ctermi*.dat`   | `ThermalParser` | `ThermalSpec` (`gcost = CEsp × Custo + CVaria`)  |
| `chidro*.dat`   | `HydroParser`   | `HydroSpec` (installed `Pot`)                     |
| `cpde*.dat`     | `DemandParser`  | hourly demand → per-block `lmax`                 |

Each parser subclasses `gtopt_shared.base_parser.BaseTextParser` — the
same shared base `plp2gtopt`'s parsers use.

### Cost model (the key win over the PSS/E network export)

PSR `.dat` carries the economics a PSS/E `.raw` lacks.  Each thermal
unit's GTOpt `gcost` is computed per segment as
`CEsp(i) × fuel.Custo + CVaria`, where `Comb` selects the fuel and
`G(i)` are the segment capacity percentages — so the converted DC
dispatch follows the real PSR merit order.

### Multi-bus network (`dbus.dat` + `dcirc.dat`)

When both `dbus.dat` and `dcirc.dat` are present, the converter emits a
**multi-bus DC OPF** (`use_kirchhoff = true`):

- **buses** from `dbus.dat` (one GTOpt `Bus` per node);
- **generator → bus** routing from the `dbus.dat` plant columns (matched
  by name — 100 % of the Guatemalan thermal+hydro plants map);
- **circuits** from the fixed-width `dcirc.dat` (lines + transformers):
  `RESIS`/`REACT` are **% on the 100 MVA system base**, so
  `x_pu = X/100` (no bus voltage involved); extremes are floored
  (1e-4 p.u., well below the 1e-3 p.u. tie threshold) and capped
  (20 p.u.) to keep the LP well-conditioned;
- **coupler circuits** (bus ties with `x_pu < 1e-3`, i.e. X < 0.1 %,
  written as `model_options.dc_line_reactance_threshold = 1e-3`) use
  gtopt's default `tiny_reactance_mode = "tie"`: a zero-impedance tie
  that forces equal bus angles, with the flow free within `tmax`.  The
  converter no longer pins `"dc"`.  Pass
  `--set model_options.tiny_reactance_mode=dc` to model them as KVL-free
  DC links instead (as ties, a coupler's `tmax` can bind and parallel
  circuits between the same buses carry no flow);
- **bus voltages** (`Bus.voltage`, metadata only) from **`Volt.dat`**
  (name-suffix + neighbour inference fill the generator-terminal buses
  absent from it).  `Volt.dat` rows whose kV is non-standard and copies
  the bus-name suffix (`ESC-692` typed as 692 kV) are corrected to the
  voltage class (692 → 69, 342 → 34.5, 232 → 230, 132 → 13.8) with a
  warning;
- **per-bus demand** from `cpdexbus.dat` (one `Demand` per load node).

Falls back to a single bus when `dbus`/`dcirc` are absent.

### Full hydro topology + water value

When `chidroGU.dat` carries real storage (any plant with `VMax > 0`) the
converter builds the **entire hydraulic network** rather than flattening
hydros to generators:

- a **Junction** per plant (its headwater node).  PSR gives two
  downstream codes: `TAA` receives the turbined water, `VAA` the spilled
  water (blank = out of the system).  The junction is `drain`-enabled
  (spill to sea, PSR `SerVer = 0`) only when `VAA` is blank; otherwise
  an uncapped spill **Waterway** carries the surplus to the `VAA`
  plant's junction, so spilled water stays in the cascade;
- a **Reservoir** on that junction for every storage plant — `emin`/`emax`
  = `VMin`/`VMax` [hm³], and the **initial volume** interpolated from the
  initial *elevation* `VInic` (metres) over the reservoir's embedded
  cota–vol curve; `use_state_variable` so the end-volume couples to the cut;
- a **Turbine** per plant debiting its junction and crediting the
  junction of the `TAA` plant (a blank `TAA` sends the turbined water to
  sea).  `production_factor = FPMed`, `capacity = QMax`;
- a **Flow** injecting each plant's natural inflow (`inflow.csv`, m³/s).

The hydro generators carry **no fuel cost** — dispatch is governed
physically by the turbines and the water value rides a **single boundary
cut** on the reservoir end-volumes: a `boundary_cuts.csv` whose per-reservoir
column is `−WV` ($/hm³, from `watervcp.csv` × 1000) and `rhs = ΣWV·emax`,
so the marginal cost of releasing stored water from reservoir *r* is `WVᵣ`.
A **negative** PSR water value (stored water is a liability, e.g. a
costly spill) is kept in the cut with its sign; only the soft end target
(`efin` / `efin_cost`, emitted when PSR ships an end volume) requires a
positive water value.

The case writes `scale_objective = 1` + `auto_scale` (the cut rows are
divided by `scale_objective`, so a raw-money objective keeps the water
value undistorted).

When `chidroGU.dat` has **no** storage the legacy generator model still
applies: per-plant `gcost = WV · 3.6 / FPMed` (capped at the deficit
cost) and a run-of-river inflow cap (`pmax = min(Pot, inflow · FPMed)`),
with `--hydro-cost <$/MWh>` as a uniform fallback.

### Method: monolithic by default (sddp/cascade for multi-week)

A one-week NCP dispatch is a **deterministic single-horizon** problem, so
it solves **monolithically** (like the plexos2gtopt cases) — 1 stage ×
N hourly blocks, the boundary cut wired into `monolithic_options`.

`--blocks-per-stage K` (a divisor of the horizon, e.g. `24` = daily)
splits the horizon into ≥ 2 stages and emits the SDDP decomposition layer
(`phase_array` / `scene_array` / `aperture_array`), so the case runs under
the **sddp** method (the cut moves to `sddp_options`) — and `cascade` by
setting `options.method = cascade` (`gtopt --set method=cascade`).  Reservoir volumes then couple across
stages.  This is intended for genuine **multi-week** horizons; for a
single week it adds the seasonal water value only as a near-constant
terminal term (the high-value reservoirs are small daily-regulation ponds,
the large reservoir has ≈ 0 water value), so monolithic is the right default.

### The LMP cross-check (vs PDD `cmgbuscp.csv`)

The multi-bus **topology + economics are reproduced**: the network builds
and congests, the **interconnection import cap** (`--import-limit 200`,
the EOR GUA↔MEX tie limit) stops the $0 Mexico import fuels (~1.8 GW)
flooding the dispatch, and the system marginal cost lands in a sensible
band. The main modelling difference left is the **seasonal water-value
floor**: a multi-week model keeps hydro priced even off-peak (water saved
now serves later weeks), which a single-week horizon cannot impose unless
the water value prices *every* hydro release.

### Interconnection imports

Import generators (the external market's offer) are shaped by the
market data shipped with the case:

- `PRECIOSMEX.csv`: an hour priced blank or 0 for an import unit (its own
  series or the regional one it inherits) caps the unit at 0 in that
  hour; a unit never offered is left out.  Without `PRECIOSMEX.csv`, an
  import unit whose fuel cost is 0 has no offer and is left out.
- `RESTMEX` schedules (`=` / `>`) fix or floor an import and keep it
  available in their hours.
- `--import-limit MW` is applied after those schedules: the peak MW of
  the fixed-schedule imports is subtracted from the limit and only the
  flexible imports are scaled into the remaining room.  When the fixed
  imports alone exceed the limit, a warning is logged and the flexible
  imports are closed.

### Example

```bash
# Guatemalan AMM daily NCP case (extracted .dat directory)
sddp2gtopt --info  ./ncp_pdd
sddp2gtopt -i ./ncp_pdd -o gtopt_ncp --import-limit 200   # multi-bus + water net
gtopt -s gtopt_ncp/gtopt_ncp.json --set output_directory=out   # monolithic, optimal

# multi-week horizon: daily stages → SDDP (and cascade)
sddp2gtopt -i ./ncp_month -o gtopt_ncp --import-limit 200 --blocks-per-stage 24
gtopt -s gtopt_ncp/gtopt_ncp.json --set output_directory=out --set method=cascade
```

---

## Multi-file PSR SDDP block cases (`deme*`/`dbf*` studies)

Classic PSR SDDP studies — monthly stages, load blocks and historical inflow
series, as published by the Peruvian COES and the Guatemalan AMM — have no
`psrclasses.json`.  When a case directory holds `sddp.dat` **and** a
`deme<nb><sfx>.dat` demand file, `sddp2gtopt` reads it with a separate
front-end (`sddp_dat_parsers.py` → `sddp_case.py` → `sddp_case_writer.py`).
The system suffix (`pe`, `gu`, …) comes from the `ID` column of
`sistem.dat`.  Block-tagged files are looked up by exact name, 3-digit then
2-digit block count (`dbf005pe`, `deme05pe`, `deme09gu`, `blrenw0090`).  No
wildcard is used, because cases often ship several block resolutions side
by side.

```bash
# 56 historical series, SDDP: one phase per monthly stage
sddp2gtopt -i coes_2026/ -o coes_mrfo_2026/
# first 8 series only, single LP
sddp2gtopt -i coes_2026/ -o coes_small/ --num-scenarios 8 --method monolithic
```

### Files read and mapping

| PSR file | gtopt element / field |
|----------|-----------------------|
| `sddp.dat` | stage type/start/count, blocks, number of series, hydro start year, network and losses flags, discount rate (`simulation.annual_discount_rate`), deficit cost (`demand_fail_cost`), spill penalty |
| `duvame<nb>.dat` / `durfix<nb>.dat` | block durations per (year, month) / fixed % split |
| `deme<nb><sfx>.dat` + `dbf<nb><sfx>.dat` | `Demand` per bus, MW: class energy (GWh) per block × normalised bus weights (latest `dbf` date ≤ stage) |
| `dbus.dat` (fixed or CSV) + `busconnection.dat` + `volt.dat` | `Bus` (kV); generator → bus via `tg`/`Plnt` or `PlantType`/`PlantCode` |
| `dcirc.dat` / `mcirc.dat` (v2 fixed, legacy, or CSV) | `Line`: R/X % on the 100 MVA base → per-unit (`x_pu = X/100`, no kV involved); rating → `tmax`; dated changes and entry → `active` |
| `ctermi<sfx>.dat` / `mtermi<sfx>.dat` | thermal `Generator`: `pmax` = GerMax × availability × (1−Teif)(1−Ih); `gcost` = CEsp1 × fuel + CVaria; `pmin` = GerMin only when MR = 1 |
| `ccombu<sfx>.dat`, `combme<sfx>.dat` | fuel cost; the monthly series overrides the constant |
| `pmtrme`/`pmhime<sfx>.dat` | maintenance: file- or table-level units/info flags (reduction or absolute, units/%/MW) |
| `rrme<nb><sfx>.dat` | reserve derating of `pmax` (%) |
| `gmme`/`rgme<nb><sfx>.dat` | `<` caps `pmax`; `>`/`=` become a soft `pmin` (`pmin_fcost` = deficit cost) |
| `chidro<sfx>.dat` / `mhidro<sfx>.dat` | hydro `Generator` + `Turbine` (FPMed, QMax), `Reservoir` when VMax > VMin (state variable, `eini` from `VInic`: p.u., or elevation via the Cota/Vol table when `tini` = 1), downstream turbined (`TAA`) and spilled (`VAA`) `Waterway`s |
| `vminme`/`vmxome<sfx>.dat` | soft `Reservoir` minimum (`soft_emin` + penalty) / `emax` |
| `mntome`, `riegme<sfx>.dat` | soft minimum outflow / irrigation withdrawal `Waterway`s (`fmin_fcost`) |
| `mxtbme<sfx>.dat` | turbine flow cap |
| `hinflw.dat` | `Flow` inflows: scenario *s* = historical year `ANO COND. INIC. HIDRO` + *s* − 1, same calendar month; station = plant `PV`.  Stations of plants that never operate in the horizon flow to the next in-service plant downstream |
| `cgnd<sfx>.dat`, `gndsite<sfx>.dat`, `mgnd<sfx>.dat` | renewable `Generator` (PotIns × FatOpe) |
| `blrenw<nb><sfx>.dat`, `blrenw<nb>[<sfx>]0.dat` (CSV or binary), `gndme<nb><sfx>.dat` | `GeneratorProfile` (scenario × stage × block) |
| `cbattery`/`mbattery<sfx>.dat` | `Battery` |

Each phase carries an explicit `apertures` list.  The scenarios are
ranked by their total inflow in that stage and visited in bit-reversed
rank order, so `--set sddp_options.num_apertures=N` keeps N hydrologies
spread across the dry-to-wet range.  Without this list `num_apertures`
has no effect, and every series becomes an aperture.

Existence flags (`Tipo`/`Type`/`Existing` = 1) mark *future* elements.  A
future element enters service at the date of its first modification record.
A future element with no modification record never operates.

### Deficit cost curve (`--deficit-segments`)

`sddp.dat` lists the deficit cost as up to four `% / cost` pairs under
`Costo Deficit` (e.g. Guatemala: `2 314.95  98 1968.44`, i.e. the first
2 % of each demand at 314.95 $/MWh, the rest at 1968.44).  The curve is
study-wide (same for every bus and stage); segments with `% = 0` are
ignored.  gtopt has one `fcost` per demand, so:

- `--deficit-segments first` (default): one demand per bus, priced by
  `model_options.demand_fail_cost` = first segment cost.  Output is
  byte-identical to earlier versions.
- `--deficit-segments all`: one demand per bus (as `first`) plus the
  native stepwise curve `model_options.demand_fail_segments` =
  `[{"fraction": pct_1/100, "cost": c_1}, …, {"cost": c_K}]` (last
  segment open).  Every demand inherits it; gtopt adds one served-load
  column per segment (no extra rows) and reports `Demand/fail_sol`
  (total) plus `Demand/fail_seg<k>_sol` per segment.
- `--deficit-segments split`: each demand becomes one sibling per segment,
  `d_<bus>_deficit<k>`, with `lmax` = `pct_k / 100` × original and
  `fcost` = segment cost.  The last segment is open-ended: it takes
  whatever the others leave, so the siblings always add up to the full
  demand (a warning is logged when the `%` do not sum to 100).  Segment 1
  keeps the original uid; segment `k` gets `uid + (k-1)·10^d`, with
  `10^d` above every demand uid.  `demand_fail_cost` and the soft-`pmin`
  penalty stay at the first segment cost.  Curtailment per segment is the
  `Demand/fail_sol` of each sibling; served load at a bus is the sum over
  `_deficit<k>`.

With one segment, `all` / `split` change nothing.  The flag applies to block
studies only; the `psrclasses.json` and NCP `.dat` front-ends keep a
single `demand_fail_cost` (a warning is logged).  The split uses the
same helpers as `plp2gtopt --falla-segments`
(`scripts/gtopt_shared/demand_tiers.py`).

```text
$ sddp2gtopt -i gu_case/ -o gu_tiers/ --deficit-segments all
INFO deficit-segments: 2%@314.95, 98%@1968.44
"model_options": {"demand_fail_cost": 314.95,
  "demand_fail_segments": [{"fraction": 0.02, "cost": 314.95}, {"cost": 1968.44}], ...}

$ sddp2gtopt -i gu_case/ -o gu_tiers/ --deficit-segments split
"demand_array": [
  {"uid": 1,  "name": "d_CENTRO220_deficit1", "bus": "CENTRO220", "lmax": "lmax", "fcost": 314.95},
  {"uid": 11, "name": "d_CENTRO220_deficit2", "bus": "CENTRO220", "lmax": "lmax", "fcost": 1968.44},
  ...
]
```

### Simplifications (logged as `simplified:` at conversion)

- Hourly chronology (`hrload`, `hrrenw`, `hrbmap`): not modelled; the
  block representation is used.
- Unit commitment, ramps, min up/down (`opectr`), start-up costs: not
  modelled.
- Hydro production-factor × volume curves, evaporation, filtration and
  non-controllable spill: not modelled; FPMed is used.
- Maximum total outflow (`mxtome`), joint reserves (`rsme`/`resger`), gas network, sum-of-circuit limits
  (`csumcirc`), emission costs: not modelled.
- Random generator/circuit outages: not modelled; only the deterministic
  Teif/Ih derating is applied.
- Multi-segment deficit cost curves: the first segment's cost is used,
  unless `--deficit-segments all|split` (below).
- `volini.dat` (binary, often stale) is ignored; `VInic` is used instead.
- Additional years (`NUMERO DE ANOS ADICIONAL`) are not added.

## Roadmap and deferred mappings

The following PSR features are **not yet** mapped.  See
[`scripts/sddp2gtopt/DESIGN.md`](../../scripts/sddp2gtopt/DESIGN.md) for the
full phased plan.

| Phase | Deliverable                                                                       |
|------:|-----------------------------------------------------------------------------------|
| ~~v1~~ ✅ | `.dat` fallback for cases without `psrclasses.json` (done)                    |
| ~~v2~~ ✅ | Hydro reservoir + turbine + junction cascade from `chidroGU.dat` (`TAA` / `VAA`) + per-reservoir water-value boundary cut (done) |
| ~~v2~~ ✅ | Inflow from `inflow.csv` injected as `Flow` per plant (done)                  |
| v2    | Multi-segment thermal bid curves (already collected, only the writer changes)    |
| v2    | Per-block demand shape from `Duracao(i)` weights                                 |
| ~~v3~~ ✅ | Multi-bus DC OPF from `dbus.dat` + `dcirc.dat` + `Volt.dat` + `cpdexbus.dat` (done) |
| v3    | Transmission lines from `PSRCircuito` / `PSRTransformador`                       |
| v4    | Multi-system cases with `PSRInterconnection*`                                    |
| v4    | Multi-scenario stochasticity (forward/backward AR-P samples)                     |
| v5    | Round-trip parity vs `index.dat`'s 325-variable output catalog                   |

---

## Cross-references

- [DESIGN.md](../../scripts/sddp2gtopt/DESIGN.md) — phased roadmap.
- [plp2gtopt.md](plp2gtopt.md) — sister tool for the PLP dialect.
- [PSRClassesInterface.jl](https://github.com/psrenergy/PSRClassesInterface.jl)
  — upstream Julia library that sources `case0`.
- [PSR SDDP user manual (v17.2 PDF)](https://www.psr-inc.com/wp-content/uploads/softwares/SddpUsrEng.pdf)
  — authoritative format reference.
