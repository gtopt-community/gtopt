# plp2gtopt — PLP to GTOpt Converter

Converts a **PLP** (*Programación de Largo Plazo*) case directory into the GTOpt JSON + Parquet
format, enabling legacy PLP hydro-thermal cases to be solved with the GTOpt
solver.

---

## Overview

PLP (*Programación de Largo Plazo*) is a widely used hydro-thermal scheduling
tool in Latin America.  Its input data is stored as a collection of
fixed-format `.dat` text files describing the power system topology, generator
parameters, demand curves, hydrology, and maintenance schedules.

`plp2gtopt` reads all standard PLP data files from a case directory and
produces:

- A **GTOpt JSON file** (`<output-dir>.json`) containing the complete system
  definition, simulation parameters, and solver options.
- **Parquet (or CSV) time-series files** organised in subdirectories under
  `<output-dir>/` (e.g. `Demand/lmax.parquet`, `Generator/pmin.parquet`,
  `Afluent/afluent.parquet`).

The output is ready to be solved directly with the `gtopt` binary, uploaded to
**gtopt\_guisrv** (browser GUI), or submitted to **gtopt\_websrv** (REST API).

### Who needs this tool?

- Engineers migrating existing PLP study cases to GTOpt.
- Teams that maintain PLP-format archives and want to benchmark or cross-validate
  results with GTOpt's LP/MIP solver.
- Automation pipelines that generate PLP `.dat` files and need a gtopt-compatible
  output.

---

## Installation

Install as part of the GTOpt scripts package from the repository root:

```bash
# Standard install (registers plp2gtopt on PATH)
pip install ./scripts

# Editable install with development dependencies
pip install -e "./scripts[dev]"
```

After installation the command is available on `$PATH`:

```bash
plp2gtopt --version
plp2gtopt --help
```

### Dependencies

| Package    | Purpose                        |
|------------|--------------------------------|
| `numpy`    | Numerical array processing     |
| `pandas`   | DataFrame I/O                  |
| `pyarrow`  | Parquet read/write             |

---

## Quick Start

```bash
# Default: reads ./input/, writes ./output.json + ./output/
plp2gtopt

# Explicit input and output directories
plp2gtopt -i plp_case_dir -o gtopt_case_dir

# Limit conversion to the first 5 stages
plp2gtopt -i input/ -s 5

# Two hydrology scenarios with 60/40 probability split
plp2gtopt -i input/ -y 1,2 -p 0.6,0.4

# Apply a 10% annual discount rate
plp2gtopt -i input/ -d 0.10

# Create a ZIP archive ready for gtopt_guisrv / gtopt_websrv
plp2gtopt -z -i plp_case -o my_case

# Verbose debug output
plp2gtopt -i input/ -l DEBUG
```

---

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `-i, --input-dir DIR` | `input` | PLP input directory containing `.dat` files |
| `-o, --output-dir DIR` | `output` | Output directory for Parquet/CSV data files |
| `-f, --output-file FILE` | `<output-dir>.json` | JSON output file path |
| `-n, --name NAME` | basename of output JSON | System name in the output JSON |
| `--sys-version STR` | `""` | Version string for the system in the output JSON |
| `-s, --last-stage N` | all | Stop conversion after stage N |
| `-d, --discount-rate RATE` | `0.0` | Annual discount rate (e.g. `0.10` for 10%) |
| `-m, --management-factor F` | `0.0` | Demand management factor |
| `-t, --last-time T` | all | Stop at time T |
| `-F, --output-format {parquet,csv}` | `parquet` | Output file format for time-series data |
| `--input-format {parquet,csv}` | same as `-F` | Input format hint written to GTOpt options |
| `-c, --compression ALG` | `gzip` | Parquet compression codec (`gzip`, `snappy`, `brotli`, `none`) |
| `-y, --hydrologies H1[,H2,…]` | `0` | Comma-separated hydrology scenario indices |
| `-p, --probability-factors P1[,P2,…]` | equal | Probability weights per hydrology scenario |
| `-k, --use-kirchhoff` | off | Enable Kirchhoff voltage-law (DC OPF) constraints |
| `-b, --use-single-bus` | off | Use single-bus (copper-plate) mode |
| `--use-line-losses` | gtopt default | Model transmission line losses |
| `--loss-cost-eps EPS` | not set (gtopt: `0.1`) | $/MWh on line-loss columns (`model_options.loss_cost_eps`); `0` disables |
| `--line-losses-mode MODE` | `tangent_signed_flow` | Line-loss formulation (`model_options.line_losses_mode`); see [Line losses](#line-losses-plpcnflidat).  `piecewise_direct` replicates PLP's LP but is **not arbitrage-safe** |
| `--loss-tangents SPEC` | `double` | Tangent count K per lossy line under `tangent_signed_flow`: `double` (K = 2·Num.Tramos, one tangent per PLP tramo per flow direction), `tramos` (K = Num.Tramos), or an integer K ≥ 2; see [Line losses](#line-losses-plpcnflidat) |
| `--demand-fail-cost COST` | `1000.0` | Cost penalty for demand curtailment ($/MWh) |
| `--reserve-fail-cost COST` | not set | Cost penalty for reserve shortfall ($/MWh) |
| `--falla-segments {first,all,split}` | `first` | PLP failure tiers: `first` = cheapest tier as the demand `fcost`; `all` = native `Demand.fail_segments` curve; `split` = one `<demand>_falla<k>` demand per tier (see [Demand](#demand-plpdemdat-plpcoscedat)) |
| `--scale-objective FACTOR` | `1000.0` | Objective function scaling factor |
| `--scale-theta FACTOR` | not set | Voltage-angle scale: the LP angle is FACTOR × θ (1 = radians, 1000 = milliradians). Unset ⇒ nothing is emitted and gtopt picks 1/(3·median(X/V²)); an explicit value is written to `model_options.scale_theta` |
| `--sanity-report PATH` | not set | Write the post-conversion data-quality report as JSON (the summary is always logged): per-kV `|X|/V²` / `R/V²` distribution and lines below the tie threshold, kV-class impedance outliers, X/R, non-standard / mistyped bus kV, zero-rated units (and those still committed), profiles outside [0, 1], reservoirs without `emin` under an agreement reserve level, water-right rewards above the demand fail cost.  Same report on an existing case: `python -m gtopt_shared.sanity_report case.json` (runs with the post-conversion check; skipped by `--no-check`) |
| `-z, --zip` | off | Bundle JSON + data files into a ZIP archive |
| `-l, --log-level LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `--source-version X` | detected | PLP variants to assume, `+`-separated (e.g. `CEN65+ESS`); see [Supported source variants](#supported-source-variants) |
| `-V, --version` | — | Print version and exit |

---

## Supported source variants

PLP `.dat` files carry no version stamp and PLP has no version history the
parsers key on.  plp2gtopt instead declares the **known variants** its
parsers handle: optional files and alternative layouts a case may combine.
All detected variants are logged and recorded together (e.g.
`CEN65 + ESS + maule-agreement`) in `plp2gtopt_state.json` under
`extra.source_format` (`variants`, `detected_variants`, `warnings`).

| Variant | Distinguishing files / layout | Parser code path | Tested by |
|---------|-------------------------------|------------------|-----------|
| `CEN65` (default) | Core file set `plpblo`, `plpeta`, `plpbar`, `plpcnfli`, `plpcnfce`, `plpdem`, `plpcosce`, `plpmance`, `plpmanli`, `plpaflce`, `plpextrac`, `plpmanem` (detected from `plpcnfce.dat`) | `plp_parser.py` required parsers | every `scripts/cases/plp_*` case |
| `ESS` | `plpess.dat` + optional `plpmaness.dat` (Fortran `genpdess.f`, the "PLP v6.6 Colbun ESS" build); optional `[DCMod] [CenCarga]` columns and 5 or 6 `plpmaness` fields are accepted | `plp_parser.py` (`plpess` first), `ess_parser.py`, `maness_parser.py` | `plp_min_ess`, `plp_bat_4b_24` |
| `BESS` | `plpcenbat.dat` + optional `plpmanbat.dat` (Fortran `genpdbaterias.f`); only read when `plpess.dat` is absent | `plp_parser.py` (`elif cenbat_path`) | `plp_min_battery`, `plp_min_bess` |
| `maule-agreement` | `plpmaulen.dat`; the older layout without the second embalse flag or the trailing "Penalizadores Convenio" line is accepted (defaults 1500 / 1000) | `maule_parser.py` | `plp_min_irrigation`, `test_maule.py` |
| `laja-agreement` | `plplajam.dat` | `laja_parser.py` | `plp_min_irrigation`, `test_laja.py` |
| `cenfi-legacy` | `plpcenfi.dat` entries with one `slope constant` line | `cenfi_parser.py` (field count of the line after the reservoir) | `plp_min_cenre` fixture, `test_cenfi_parser.py` |
| `cenfi-segmented` | `plpcenfi.dat` entries with a segment count + `idx volume slope constant` rows (may be mixed with legacy entries) | `cenfi_parser.py` | `test_cenfi_parser.py` |
| `cuts-plpplaem` | Boundary cuts as `plpplaem1.dat` / `plpplaem2.dat` | `planos_parser.find_planos_files` | `test_planos.py` |
| `cuts-plpplem` | Boundary cuts as `plpplem1.dat` / `plpplem2.dat` | `planos_parser.find_planos_files` | `plp_hydro_4b`, `plp_bat_4b_24` |

**Not supported**: the `PLPv6.1_baterias` battery fork of PLP
stores batteries in `plpstrcen.dat` (Fortran `leebesscen.f`).  No parser
reads that file; it is detected as `PLPv6.1-strcen` and reported as an
unsupported variant (warning), and its batteries are not converted.

`--source-version X` replaces the detected set with a `+`-separated list
(e.g. `--source-version CEN65+ESS`); unsupported tags are ignored with a
warning.  `--help` prints the same list ("Supports the known PLP
variants: ..."), generated from `SOURCE_FORMAT` in
`plp2gtopt/source_version.py`.  See
[Supported source versions](../scripts-guide.md#supported-source-versions)
for every converter.

---

## PLP Input File Format

`plp2gtopt` reads the following `.dat` files from the input directory.  All
required files must be present; optional files are processed when found.

### Required files

| File | Parser | Description |
|------|--------|-------------|
| `plpblo.dat` | `BlockParser` | Block definitions (time resolution, durations) |
| `plpeta.dat` | `StageParser` | Stage definitions (planning periods) |
| `plpbar.dat` | `BusParser` | Bus (bar) definitions with voltage levels |
| `plpcnfli.dat` | `LineParser` | Transmission line configurations (impedance, capacity) |
| `plpcnfce.dat` | `CentralParser` | Generating central (plant) configurations |
| `plpdem.dat` | `DemandParser` | Demand (load) definitions at each bus |
| `plpcosce.dat` | `CostParser` | Generation cost curves per central |
| `plpmance.dat` | `ManceParser` | Maintenance schedule for generating centrals |
| `plpmanli.dat` | `ManliParser` | Maintenance schedule for transmission lines |
| `mantlin.csv` (optional) | `ManliParser` | CEN line plan by block range: capacity, operative flag, V/R/X (read after `plpmanli.dat`, later rows win) |
| `plpaflce.dat` | `AflceParser` | Inflow (afluent) data for hydro plants |
| `plpextrac.dat` | `ExtracParser` | Water extraction data |
| `plpmanem.dat` | `ManemParser` | Maintenance schedule for energy management |

### Optional storage files (mutually exclusive)

The converter supports two battery/storage representations.  If `plpess.dat`
exists, the ESS path takes priority; otherwise the battery path is used.

| File | Parser | Description |
|------|--------|-------------|
| `plpess.dat` | `EssParser` | Energy Storage System definition (discharge/charge efficiency, energy bounds, DCMod, CenCarga) |
| `plpmaness.dat` | `ManessParser` | Maintenance schedule for ESS (energy + power bounds per block) |
| `plpcenbat.dat` | `BatteryParser` | Battery central definition (alternative to ESS) |
| `plpmanbat.dat` | `ManbatParser` | Maintenance schedule for batteries (energy bounds per block) |

### Battery self-discharge (`annual_standing_loss`)

`plpess.dat` carries `mloss` in **%/month**; PLP (`genpdess.f`) drains the
SoC by `−bd/730 · ln(1 − mloss/100)` per block of `bd` hours.  plp2gtopt
emits the exact gtopt equivalent in the linear p.u./year convention:

    annual_standing_loss = −12 · ln(1 − mloss/100)     (≈ 0.12 · mloss)

| Source | `annual_standing_loss` in `battery_array` |
|--------|----------------------------------|
| `plpess.dat`, `mloss > 0` (DCMod 0/1) | converted value above (e.g. `mloss = 1` → 0.1206) |
| `plpess.dat`, `mloss = 0` (DCMod 0/1) | **omitted** → gtopt default 0.48 p.u./yr (4 %/month) |
| `plpcenbat.dat` (no loss field) | **omitted** → gtopt default 0.48 p.u./yr (4 %/month) |
| `plpess.dat`, DCMod = 2 (regulation tank) | Reservoir, never a Battery (see below) |

PLP writes `mloss = 0` both for "lossless" and "not specified", so a zero is
treated as unspecified and the gtopt lithium default applies.  To reproduce
a lossless PLP run exactly, set
`model_options.battery_annual_standing_loss = 0` in the generated case (or `--set model_options.battery_annual_standing_loss=0`).

### Co-located storage (plant + battery)

A storage whose charge comes from a co-located plant (a PV / wind farm with
its own battery) is converted to a **standalone** gtopt battery on its own
bus plus one user constraint per battery:

```text
<battery>_charge_source:
  demand('<battery>_dem').load - generator('<plant>').generation <= 0
```

The plant keeps its own grid bus.  It can inject directly to the grid
**and** charge the battery; the charge can never exceed what the plant
produces.  The plant is not curtailed when its MW exceed the battery
charge rating.

- **`plpess.dat`** (ESS model, the reference) — `DCMod = 1` with a
  `CenCarga`.  This is PLP's own ESS row (`genpdess.f`, GenPDEssBloA):
  `ren_cp·P_cp − ren_ess·dc ≥ 0` with RHS 0 (`plp-pdmatld.f`).  The ESS
  charge `dc` is a load at the ESS bus (`genpdess.f`) and the plant injects
  all its output at its own bus.  Per-block `DCMod` from `plpmaness.dat`
  is honoured with a `for(block in {...})` domain (a `plpmaness` row
  without the DCMod field means `DCMod = 1`, as in LeeManEss).  A
  `CenCarga` that is not a central makes the ESS `DCMod = 0`, as in PLP.
  With DC maintenance (legacy multi-element battery) the charge demand is
  `<battery>_chrg`.
- **`plpcenbat.dat`** (battery model) — the first injection central when it
  is a real plant (not the battery itself, another battery or a
  `<battery>_LOAD` grid-charge phantom).  Only the topology is taken from
  PLP (`genpdbaterias.f`: plant output = direct injection + charge, never
  forced through the battery); everything else follows gtopt's battery
  semantics (see deviations below).
- A plant that is missing from `generator_array` drops the coupling with
  a warning.  Standalone batteries (no plant, `DCMod = 0`) charge freely
  from the grid.

Deliberate deviations from PLP:

- `plpcenbat.dat` storages use gtopt's battery model: efficiencies,
  `emin` / `emax` in MWh, `pmax_charge` (= the BAT central pmax) and
  standing loss.  PLP's battery model has no charge-rate limit on the
  injected energy, scales the SoC bound by `edur/24`
  (`genpdbaterias.f` GenPDBatBloFO) and reports `EnIny` in MWh × 3.6; none
  of that is reproduced.
- The charge is drawn at the battery bus.  When the plant sits on another
  bus (a few CEN plpcenbat.dat batteries) the
  charging energy crosses the network; PLP's battery model moves it
  without a network path.
- `Battery.source_generator` (internal bus with the battery charge as the
  plant's only outlet) is no longer emitted: it forced all plant output
  through the battery and curtailed it at `pmax_charge`.

### Regulation tanks (`DCMod = 2` → hydro Reservoir)

When `DCMod = 2` and `CenCarga` references a hydro central (serie, pasada,
or embalse), the ESS entry represents a **regulation tank** (Embalse de
Regulación Diaria — ERD), not a real battery.  These entries typically have
`nc = nd = 1.0` (perfect efficiency — water storage, no electrical losses).

`plp2gtopt` maps DCMod=2 entries to GTOpt **Reservoir** elements instead of
Battery elements:

- The ESS entry is **excluded** from `battery_array`.
- A **Reservoir** is created with:
  - `junction` = the paired hydro generator's central number (its junction UID)
  - `emax` = ESS energy capacity (MWh)
  - `annual_standing_loss` = `−12 · ln(1 − mloss/100)` (`mloss` is %/month; `0`
    when `mloss = 0` — a regulation tank never takes the battery default)
  - `daily_cycle = true`

The paired hydro generator already has a junction with river inflow and a
turbine in the GTOpt hydro topology.  The regulation reservoir attaches to
that junction, and the water balance naturally models charging (storing
water) and discharging (releasing water through the turbine):

```
river_inflow + reservoir_extraction = waterway_out + drain
power = conversion_rate × waterway_flow
```

The turbine's capacity (`pmax`) enforces the joint capacity constraint
(`P_gen + P_tank ≤ Pmax`), which is the physical meaning of DCMod=2 in PLP.

**Validation**: `plp2gtopt` raises `ValueError` if the paired generator
(`CenCarga`) is not a hydro central type (e.g. if it is `termica`), since
regulation tanks require a hydro junction and turbine.

#### Example

In `plpess.dat`:
```
ANGOSTURA_ERD  1.000 1.000  2.0  800.0  328.1  2  ANGOSTURA
```

ANGOSTURA is a "serie" hydro generator (uid=68, 328.1 MW) with junction 68
and river inflow.  `plp2gtopt` creates a Reservoir named `ANGOSTURA_ERD`
(800 MWh, daily_cycle) attached to junction 68.  No Battery is created.

### Field order notes

- In `plpess.dat`, the Fortran READ order is
  `Nombre nd nc mloss Emax DCMax [DCMod] [CenCarga]` where `nd` is the
  discharge efficiency and `nc` is the charge efficiency.  Note that `nd`
  (discharge) comes **before** `nc` (charge), even though some PLP data file
  comments label them in the opposite order.
- `plpmanbat.dat` has 3 fields per data line: `IBind EMin EMax`.
- `plpmaness.dat` has 5–6 fields per data line: `IBind Emin Emax DCMin DCMax [DCMod]`.

---

## Mapping PLP elements → GTOpt

Conversion is one parser per `.dat` file (see
[`scripts/plp2gtopt/`](../../scripts/plp2gtopt/)) feeding a writer
that assembles the GTOpt JSON + Parquet bundle.

### High-level table

| PLP source                       | GTOpt target                                                | Status |
|----------------------------------|-------------------------------------------------------------|:------:|
| `plpblo.dat` (Bloques)           | `simulation.block_array`                                    | ✅      |
| `plpeta.dat` (Etapas)            | `simulation.stage_array`                                    | ✅      |
| `plpidsim.dat` / `plpidape.dat`  | `simulation.scenario_array` + apertures                     | ✅      |
| `plpbar.dat` (Bar / Bus)         | `system.bus_array`                                          | ✅      |
| `plpcnfli.dat` + `plpmanli.dat`  | `system.line_array` + per-stage `tmax_ab/ba.parquet`        | ✅      |
| `mantlin.csv`                    | merged into `Line/tmax_ab`, `tmax_ba`, `active`; per-stage `voltage`/`resistance`/`reactance.parquet` when they change | ✅      |
| `plpcnfce.dat`  ⟶ thermal        | `system.generator_array` (thermal)                          | ✅      |
| `plpcnfce.dat`  ⟶ embalse        | `Reservoir` + `Turbine` + `Junction` + `Waterway`           | ✅      |
| `plpcnfce.dat`  ⟶ serie/pasada   | `Junction` + `Waterway` cascade (RoR & series hydro)        | ✅      |
| `plpcnfce.dat`  ⟶ falla          | demand-curtailment generator (auto-derived)                 | ✅      |
| `plpcosce.dat`                   | generator `gcost` time-series                               | ✅      |
| `plpdem.dat`                     | `system.demand_array` + `Demand/lmax.parquet`               | ✅      |
| `plpmance.dat`                   | per-stage `pmin.parquet` / `pmax.parquet`                   | ✅      |
| `plpcenre.dat`                   | `generator_profile_array` (renewable shapes)                | ✅      |
| `plpcenbat.dat` / `plpess.dat`   | `system.battery_array` (mutually exclusive)                 | ✅      |
| `plpmanbat.dat` / `plpmaness.dat`| `Battery/emin.parquet` + `Battery/emax.parquet`             | ✅      |
| `plpaflce.dat`                   | `Afluent/afluent.parquet`                                   | ✅      |
| `plpfilemb.dat`                  | `Reservoir.filtration` curves                               | ✅      |
| `plpmanem.dat`                   | per-stage `Reservoir.emin/emax`                             | ✅      |
| `plpvrebemb.dat`                 | `Reservoir.efin_cost` (soft volume slack)                   | ✅      |
| `plpextrac.dat`                  | `Junction.drain` (water extraction)                         | ✅      |
| `plpminembh.dat`                 | per-stage `Reservoir.emin` (minimum stored energy)          | ✅      |
| `plpralco.dat`                   | irrigation overlay (Ralco-specific)                         | ✅      |
| `plpgnl.dat`                     | LNG fuel availability constraint                            | ✅      |
| `plplajam.dat` / `plpmaulen.dat` | Laja / Maule irrigation agreements                          | ✅      |
| `plpmat.dat` / `plpplem*.dat`    | `boundary_cuts.csv` / SDDP cut hot-start                    | ✅      |
| `plpcenpmax.dat` / `plpcenfi.dat`| per-stage capacity / forced outage caps                     | ✅      |
| `plpidap2.dat`                   | second-axis aperture/scenario indexing                      | ✅      |

### Stages, blocks and scenarios

| GTOpt concept    | PLP source                                                                                              |
|------------------|---------------------------------------------------------------------------------------------------------|
| `block_array`    | `plpblo.dat` — one entry per block, `duration` is the block hour budget                                 |
| `stage_array`    | `plpeta.dat` — one entry per planning period (monthly by default)                                       |
| `scenario_array` | `-y` flag selects hydrologies from `plpaflce.dat`; `plpidsim.dat` maps logical → physical column index  |

When `plpidsim.dat` is absent, `-y` indices are interpreted directly
as columns in `plpaflce.dat`.  Probability weights default to a
uniform split; override with `-p`.

### Bus and network (`plpbar.dat`, `plpcnfli.dat`)

| PLP field                         | GTOpt field            | Notes                                          |
|-----------------------------------|------------------------|------------------------------------------------|
| `IBar` (1-based row)              | `Bus.uid`              | Stable through conversion                      |
| `Nombre` (`'…'`)                  | `Bus.name`             | Quotes stripped                                |
| Voltage                           | (logged, not in JSON)  |                                                |
| `IBarA`/`IBarB` in `plpcnfli.dat` | `Line.bus_a/bus_b`     | Resolved to bus names                          |
| `FFalla`                          | (informational)        | Forced-outage flag                             |
| `Tmax_AB / Tmax_BA`               | `Line.tmax_ab/ba`      | Time-varying via `plpmanli.dat` / `mantlin.csv` |
| `Resist`, `Reactanc`              | `Line.resistance / reactance` | Used when `--use-kirchhoff` is on       |
| `Mod.Perd.` × header `FPerdTram` | `Line.use_line_losses` | `true` only when both are `T`, else `false` (PLP models no loss) |
| `Num.Tramos`                      | `Line.loss_segments`   | See [Line losses](#line-losses-plpcnflidat)    |

#### Line losses (`plpcnfli.dat`)

PLP models a line's losses only when the header flag `FPerdTram` and the
line's `Mod.Perd.` are both `T` (`leecnfli.f:213-215` otherwise forces
`LinNFlu = 1`, and `genpdlin.f:105` stamps no loss factor).  `Num.Tramos`
(`LinNFlu`) is the number of equal-width flow tramos **per direction**
(`leecnfli.f:217-218`: `ΔF = F.Max / LinNFlu`): `genpdlin.f` creates
`2·LinNFlu` flow columns per line and block and folds the secant loss slope
`R·(2k−1)·ΔF/V²` of tramo `k` into the bus-balance coefficients — no loss
rows.

plp2gtopt writes `use_line_losses: false` on every line PLP keeps lossless
(gtopt's default is losses on), and, for lossy lines with `Num.Tramos ≥ 2`,
a per-line `loss_segments`.  Under the default `tangent_signed_flow` mode
(one signed flow column, K tangent rows below the quadratic, a chord row
above it, `loss_cost_eps` against loss arbitrage), K follows
`--loss-tangents`:

| `--loss-tangents` | K | Loss rows per line-block | Worst loss under-estimate |
|---|---|---|---|
| `tramos` | `Num.Tramos` | K (K − 1 when K is odd: the zero-slope tangent at f = 0 is dropped) + 3 | `R·fmax²/(V²·K²)` |
| `double` (default) | `2·Num.Tramos` | 2·Num.Tramos + 3 | `R·fmax²/(4·V²·Num.Tramos²)` (= PLP's secant over-estimate) |
| integer `K` | `K` | as above | `R·fmax²/(V²·K²)` |

The `+ 3` are fixed: two `|f|` rows and the chord upper bound; each lossy
line-block also has 3 columns (flow, loss, `|f|`).  The default `double`
places one tangent per PLP tramo in each flow direction (PLP's
`Num.Tramos` counts tramos per direction), so its worst-case loss error
equals PLP's secant error; `tramos` halves the tangent rows at 4× that
error (for 3 tramos: 5 loss rows per line-block against 9).  The KVL row of each line is common to every mode and to PLP.

`--line-losses-mode piecewise_direct` (bundled by `--plp-legacy`) reproduces
PLP's own formulation: `2·Num.Tramos` segment columns, no loss rows, the
smallest LP.  Use it **only** for PLP-replication / LP-diff studies: with no
loss rows, a negative bus price makes the LP send flow both ways on a line
and burn energy as fictitious losses, which also hides negative marginal
costs.  `tangent_signed_flow` keeps a single signed flow, so it cannot do
that, and `loss_cost_eps` blocks the remaining loss inflation as long as it
exceeds half the (negative) price sum of the line's two buses
(`plp2gtopt/tests/test_integration_loss_tangents.py`).

### Centrales (`plpcnfce.dat`) — the heaviest mapping

PLP groups generating units into five categories.  Each maps to a
distinct GTOpt structure:

| PLP central type    | GTOpt structure                                                            |
|---------------------|----------------------------------------------------------------------------|
| **Térmica**         | `Generator` only                                                           |
| **Embalse** (reservoir) | `Reservoir` + `Turbine` + intake `Junction` + spillway `Waterway`     |
| **Serie hidráulica**| Cascaded `Junction` + `Waterway` chain feeding a downstream `Reservoir`    |
| **Pasada pura** (RoR)| `Junction` + `Waterway` + flow-driven `Generator` (no reservoir)          |
| **Falla** (failure) | Inferred — produces a high-cost virtual generator on every bus             |
| **Renovable** (`plpcenre.dat`) | `Generator` + `generator_profile_array` (hourly shape)          |

**Spillway waterway**: every embalse central also gets a synthetic
`_ver` waterway from upstream junction to drain; suppress it with
`--drop-spillway-waterway` for cases that prefer junction-level drain
to absorb spills.

**Pasada classification**: GTOpt has tech-detection that may divert
some "pasada" centrals into the renewable-profile path (solar/wind
look-alikes).  Use `--pasada-mode {hydro,flow-turbine,profile}` to
override; `--plp-legacy` forces `flow-turbine` for byte-faithful PLP
parity.

**Profile values are per-unit.**  A `GeneratorProfile` is a factor of
the generator `capacity` (PLP `PotMax`), not MW.  PLP bounds a pasada
central's flow by `min(aflce, PMax / Rendi)` and produces `Rendi · q`
MW, so plp2gtopt writes `profile = aflce · Rendi / PotMax` clipped to
`[0, 1]`, with one summary warning for the clipped values.  A central
with `PotMax ≤ 0` gets a zero profile.  The scalar `afluent` path uses
the same rule.

### Demand (`plpdem.dat`, `plpcosce.dat`)

| PLP source                 | GTOpt target                          | Conversion                          |
|----------------------------|---------------------------------------|-------------------------------------|
| `plpdem.dat` MW values     | `Demand.lmax[stage][block]`           | direct (no GWh→MW step needed)      |
| `plpcosce.dat` `Falla`     | `options.demand_fail_cost`            | average of first-tier FALLA gcost   |
| `plpcosce.dat` per-segment | piecewise gcost on virtual generators | one segment per cost tier           |

#### Failure tiers (`--falla-segments`)

PLP models unserved energy with several *falla* centrals per bus
(`FALLA_<bus>_1..4` in the CEN cases): fictitious generators with
increasing `CosVar` whose `plpmance.dat` `PotMax` is a fixed share of the
bus demand (CEN: 5 % / 5 % / 10 % / 80 %).  gtopt has one `fcost` per
demand, so:

- `--falla-segments first` (default): one demand per bus with
  `fcost` = cheapest tier (its `plpcosce.dat` schedule when present,
  min across tiers).  Higher tiers are dropped.
- `--falla-segments all`: each demand on a bus with two or more tiers
  keeps its single column and gets the native stepwise curve
  `fail_segments` = `[{"fraction": f_1, "cost": c_1}, …, {"cost": c_K}]`
  (`f_k` as below, last tier open) instead of `fcost`.  A tier cost is the
  falla `gcost`, or — when its `plpcosce.dat` schedule varies — the
  per-stage file `Demand/fail_cost_seg<k>`.  gtopt adds one served-load
  column per tier (no rows) and writes `Demand/fail_seg<k>_sol` per tier
  next to the total `Demand/fail_sol`.
- `--falla-segments split`: each demand on a bus with two or more tiers
  becomes one sibling demand per tier, `<demand>_falla<k>` (`k` = 1 for
  the cheapest).  `lmax` (and `emin`) = `f_k` × original, with
  `f_k = ΣPotMax_k / Σ_j ΣPotMax_j` from `plpmance.dat`, normalised so the
  siblings add up exactly to the original demand.  `fcost` = tier cost
  (or its `plpcosce.dat` schedule).  Tier 1 keeps the original uid; tier
  `k` gets `uid + (k-1)·10^d`, with `10^d` above every bus and central
  number.  Curtailment per tier is the `Demand/fail_sol` of each sibling;
  served load at the bus is the sum over `_falla<k>`.  Buses with one
  tier, or whose tiers lack a `plpmance.dat` schedule, are not split.

Both `all` and `split` keep rationing in the standard `Demand` fail
output (no falla generators), so unserved-energy reports need no change.

The naming, uid and fraction helpers live in
`scripts/gtopt_shared/demand_tiers.py` and are shared with
`sddp2gtopt --deficit-segments`, which applies the same split to the PSR
SDDP `Costo Deficit` curve (`<demand>_deficit<k>`).

When `--demand-fail-cost` is **not** explicitly set, `plp2gtopt`
auto-derives it from the first-tier FALLA gcost in `plpcnfce.dat`;
writing `demand_fail_cost = 0` in `~/.gtopt.conf` silently disables
that auto-detection (see `_parsers.py`).

### Hydrology (`plpaflce.dat`, `plpfilemb.dat`, `plpvrebemb.dat`)

| PLP source                 | GTOpt target                                     |
|----------------------------|--------------------------------------------------|
| `plpaflce.dat`             | `Afluent/afluent.parquet`, m³/s per stage/scen   |
| Hydro `FP` (MW per m³/s)   | `Turbine.conversion_rate`                        |
| Reservoir `Vmin/Vmax`      | `Reservoir.vmin/vmax` (hm³)                      |
| Reservoir `Vinic`          | `Reservoir.vinic` (initial volume)               |
| `plpfilemb.dat` segments   | `Reservoir.filtration` (volume → loss curve)     |
| `plpvrebemb.dat`           | spillage cost feeding `Reservoir.efin_cost`      |
| `plpminembh.dat`           | per-stage `Reservoir.emin` minimum               |

`--plp-spill-costs` (opt-in, default **off**) additionally prices the
reservoir storage-drain column at the raw `plpvrebemb.dat` `Costo de
Rebalse` (fallback `plpmat.dat` `CVert`), reproducing PLP's `qrb`
objective term `CostoReb·edur/(ScaleObj·FactTasa)` unit-for-unit
($/(m³/s·h), no `--spillway-cost-cap` clamp).  Keep it off outside
PLP-parity studies: pricing physical spill inflates the reservoir
energy-balance duals (marginal water values) wherever spill is active.

`reservoir_energy_scale` is auto-derived from `emax` so SDDP
state-variable scaling stays well-conditioned; override with
`--reservoir-energy-scale`.

### Storage (`plpess.dat` / `plpcenbat.dat`)

PLP supports two mutually exclusive battery dialects.  See
[Optional storage files](#optional-storage-files-mutually-exclusive)
above for selection rules and the special `DCMod = 2` regulation-tank
case (mapped to `Reservoir`, not `Battery`).

### Soft constraints and slack costs

| PLP feature                          | GTOpt expression                              |
|--------------------------------------|-----------------------------------------------|
| First-tier `falla` cost              | `options.demand_fail_cost`                    |
| `vrebemb` (spillage cost)            | `Reservoir.efin_cost` per stage               |
| `plpmanem` `emin/emax` per stage     | `Reservoir.soft_emin_cost` slack              |
| `plpminembh` `EmbCostoH` per stage   | `Reservoir.soft_emin` / `soft_emin_cost`      |
| `plpmaness` capacity bands           | per-stage `Battery` `emin/emax/dcmin/dcmax`   |

`--soft-storage-bounds` (default **off**) routes per-reservoir
end-volume slack through the C++ `Reservoir.efin_cost` mechanism
instead of hard constraints.  By default `efin` and the maintenance
`emin` stay hard, as in PLP.

`plpminembh.dat` soft floors use the file's own per-stage cost
(`EmbCostoH`, `leeminembh.f`).  A stage with cost 0 has a free slack in
PLP, so the floor has no effect and plp2gtopt emits nothing for it.
`--soft-emin-cost C` (default 0) is an opt-in fallback price for those
stages; it is not a PLP datum.  A negative value ignores
`plpminembh.dat` altogether.

plp2gtopt adds no hydro penalty that the PLP data does not define.  The
reservoir spillway (storage drain) is free unless `--plp-spill-costs` is
given: a positive cost on the only way out of a full reservoir makes its
water value negative while it spills.

---

## Output Structure

### JSON + Parquet directory layout

A typical conversion produces:

```
my_case.json              ← main system / simulation / options config
my_case/                  ← input_directory (referenced in the JSON)
├── Demand/
│   └── lmax.parquet            ← demand time-series
├── Generator/
│   ├── pmin.parquet            ← generator minimum output
│   └── pmax.parquet            ← generator maximum output
├── Afluent/
│   └── afluent.parquet         ← hydro inflow data
├── Battery/
│   ├── emin.parquet            ← battery energy lower bounds
│   └── emax.parquet            ← battery energy upper bounds
└── Line/
    ├── tmax_ab.parquet         ← line capacity (direction A→B)
    └── tmax_ba.parquet         ← line capacity (direction B→A)
```

The `input_directory` field in the JSON options object matches the subdirectory
name, so GTOpt can locate the data files without any extra configuration.

---

## ZIP Output (`-z` / `--zip`)

The `-z` flag creates a single **ZIP archive** that bundles the JSON and all
data files together, preserving the full directory structure:

```bash
plp2gtopt -z -i plp_case -o my_case
# Produces: my_case.zip
```

ZIP layout:

```
my_case.zip
├── my_case.json
└── my_case/
    ├── Demand/
    │   └── lmax.parquet
    ├── Generator/
    │   ├── pmin.parquet
    │   └── pmax.parquet
    └── Afluent/
        └── afluent.parquet
```

This archive is directly compatible with **gtopt\_guisrv** (upload via the GUI)
and **gtopt\_websrv** (submit via the REST API).

---

## Conversion Statistics

After a successful conversion `plp2gtopt` logs statistics (at INFO level)
similar to the pre-solve statistics printed by the `gtopt` solver:

```
=== System statistics ===
  System name     : plp2gtopt
=== System elements  ===
  Buses           : 2
  Generators      : 3
  Generator profs : 1
  Demands         : 2
  Lines           : 1
  Batteries       : 0
  Converters      : 0
  Junctions       : 2
  Waterways       : 1
  Reservoirs      : 1
  Turbines        : 1
=== Simulation statistics ===
  Blocks          : 8760
  Stages          : 5
  Scenarios       : 2
=== Key options ===
  use_single_bus  : False
  scale_objective : 1000
  demand_fail_cost: 1000
  input_directory : my_case
  annual_discount : 0.1
=== Conversion time ===
  Elapsed         : 1.234s
```

Use `-l DEBUG` to see which individual `.dat` files are being parsed and their
parsing results.

---

## Error Messages

| Situation | Error message |
|-----------|---------------|
| Input directory missing | `Input directory does not exist: 'plp_case/'` |
| Required `.dat` file missing | `Required file not found: …/plpblo.dat` |
| Invalid data format in a `.dat` file | `Invalid data format: …` |
| Mismatched hydrology/probability counts | Number of `-p` values must match `-y` |

---

## Integration with gtopt\_guisrv and gtopt\_websrv

### gtopt\_guisrv (browser GUI)

1. Convert and ZIP:
   ```bash
   plp2gtopt -z -i plp_case -o gtopt_case
   ```
2. Start the GUI: `gtopt_guisrv`  (opens at `http://localhost:5001`)
3. Click **Upload case** and select `gtopt_case.zip`.
4. Edit parameters if needed, then click **Solve**.

### gtopt\_websrv (REST API)

Submit the ZIP directly:

```bash
curl -X POST http://localhost:3000/api/solve \
  -F "file=@gtopt_case.zip"
```

The server runs the GTOpt solver and returns a results ZIP containing the
solution files.

---

## Example: Converting a 2-Year Hydro Case with 2 Scenarios

Suppose you have a PLP case directory `plp_case/` containing 24 monthly
stages (2 years) with hydro plants, thermal generators, and two hydrology
scenarios (dry and wet).

```bash
# Convert with 2 hydrology scenarios, 10% discount rate, Kirchhoff mode
plp2gtopt \
  -i plp_case \
  -o my_case \
  -n my_case \
  -s 24 \
  -y 1,2 \
  -p 0.6,0.4 \
  -d 0.10 \
  -k \
  -z

# This produces:
#   my_case.json   — main planning configuration
#   my_case/       — Parquet time-series data
#   my_case.zip    — bundled archive

# Solve with gtopt
gtopt my_case.json

# Or upload the ZIP to the GUI service
gtopt_guisrv  # then upload my_case.zip in the browser
```

The conversion statistics will show the full system inventory — buses, thermal
and hydro generators, demands, lines, junctions, waterways, reservoirs, and
turbines — along with the simulation structure (blocks × stages × scenarios).

---

## Test fixture library

`scripts/cases/` ships a graded set of PLP cases used by the parser
unit tests, the integration suite, and the smoke tests:

| Fixture                       | Purpose                                                              |
|-------------------------------|----------------------------------------------------------------------|
| `plp_dat_ex/`                 | Minimal valid `.dat` collection (one of every required file)         |
| `plp_min_1bus/`               | Single-bus toy case (smoke test for the JSON pipeline)               |
| `plp_min_2bus/`               | Two-bus + one line                                                   |
| `plp_min_hydro/`              | One reservoir, one turbine, AR-1 inflow                              |
| `plp_min_hydro_ms/`           | Multi-stage hydro with maintenance                                   |
| `plp_min_reservoir/`          | Reservoir-only flow path                                             |
| `plp_min_battery/`            | Standalone battery (`plpcenbat.dat`)                                 |
| `plp_min_bess/`               | BESS via `plpess.dat` (DCMod = 0)                                    |
| `plp_min_ess/`                | ESS via `plpess.dat` (DCMod = 0)                                     |
| `plp_min_cenre/`              | Renewable profile generator                                          |
| `plp_min_filemb/`             | Reservoir filtration curves                                          |
| `plp_min_mance/` `_manli/`    | Maintenance schedules for centrals / lines                           |
| `plp_hydro_4b/`               | 4-bus hydro benchmark                                                |
| `plp_bat_4b_24/`              | 4-bus, 24-block battery scheduling                                   |

Each fixture is a self-contained directory of `.dat` files; running
`plp2gtopt -i scripts/cases/<fixture> -o /tmp/gtopt_<fixture>` then
`gtopt --lp-only -s /tmp/gtopt_<fixture>/<fixture>.json` is the
canonical end-to-end smoke test.

The realistic 2-year PLP case (`plp_case_2y`) and its converted GTOpt
counterparts (`gtopt_case_2y`, `gtopt_case_2y_fs`) are not shipped with
GTOpt; the tests that use them look them up under `$GTOPT_CASES_DIR`
and skip when it is unset.

---

## Limitations and known gaps

| Area                          | Status                                                                |
|-------------------------------|-----------------------------------------------------------------------|
| Multi-segment thermal bid     | Cost segments collapsed to a per-stage scalar `gcost`                 |
| Forced-outage stochasticity   | `FFalla` flag honoured for capacity, not for stochastic failure draws |
| AR-P inflow draws             | GTOpt's own SDDP engine generates samples; PLP `plpaflemb.dat` is read but not directly passed through |
| Multi-currency studies        | Single `currency` per planning; PSR-style multi-currency cases collapse to the reference currency |
| Calendar-aware stage hours    | `plpblo.dat` durations honoured; per-month nominal mapping when blocks are missing |

---

## See Also

- [SCRIPTS.md](../scripts-guide.md) — overview of all GTOpt Python scripts
- [PLANNING\_GUIDE.md](../planning-guide.md) — GTOpt planning concepts
  (stages, blocks, scenarios, expansion)
- [INPUT\_DATA.md](../input-data.md) — GTOpt input file format reference
- [sddp2gtopt](sddp2gtopt.md) — sister tool for PSR SDDP cases
- [pp2gtopt](pp2gtopt.md) — convert pandapower networks to GTOpt
- [ts2gtopt](ts2gtopt.md) — project hourly time-series onto GTOpt horizons
- PLP is maintained by the Chilean system operator; its model page,
  documentation and releases are at the
  [CEN](https://www.coordinador.cl/operacion/documentos/modelacion-del-sen/modelos-para-la-planificacion-y-programacion-de-la-operacion/). The formats
  documented here were derived from the PLP `lee*.f` readers.
