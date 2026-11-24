# nrel118_to_gtopt — NREL-118 test system → gtopt

Converts the **NREL-118** extended IEEE 118-bus test system
(Peña, Martinez-Anido, Hodge 2018 [1]) into a **full-network,
multi-block, multi-stage** gtopt case:

- 118 buses and 186 lines (DC power flow with Kirchhoff, line limits),
- all 327 generators at their buses with heat-rate based costs and
  IPCC AR6 CO₂ factors,
- hourly regional load distributed to the buses by the published load
  participation factors,
- hourly wind, solar and hydro availability,
- the whole 8784-hour study year by default (366 daily stages × 24
  hourly blocks), or any hour window / block / stage aggregation.

The source data is **fetched on demand** and never vendored: the
upstream repository (`NREL-Sienna/PowerSystemsTestData`, `118-Bus/`)
ships no licence file.

---

## Quick start

```bash
# Whole year: 8784 one-hour blocks in 366 daily stages (default)
nrel118_to_gtopt -o nrel118/nrel118.json
gtopt -s nrel118/nrel118.json

# One week (the second week of the year)
nrel118_to_gtopt --hours 168+168 -o week2/nrel118.json

# Whole year in 4-hour blocks and weekly stages
nrel118_to_gtopt --block-hours 4 --stage-hours 168 -o y4h/nrel118.json

# Re-run a previous conversion (flags given alongside override it)
nrel118_to_gtopt --from-state nrel118/nrel118/nrel118_to_gtopt_state.json
```

`python -m nrel118_to_gtopt …` works too (run from `scripts/`).

---

## Output layout

```
<dir>/<stem>.json                         planning JSON (relative input_directory)
<dir>/<stem>/Demand/lmax.parquet          per-block bus load [MW]
<dir>/<stem>/GeneratorProfile/profile.parquet
                                          per-block availability [p.u. of capacity]
<dir>/<stem>/nrel118_to_gtopt_state.json  CLI snapshot + case summary
<dir>/<stem>/README.md
```

The Parquet files use the long layout `[scenario,] stage, block, uid,
value` written by the shared `gtopt_shared.time_grid` helper (same as
[pp2gtopt](pp2gtopt.md#time-series-mode)).  `options.input_directory`
is the bare stem, so the case directory can be moved as a unit.

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `-o, --output FILE` | `nrel118.json` | Output JSON; schedules go to `<dir>/<stem>/` |
| `--hours RANGE` | whole year | Hour window: `A:B` (hours A..B-1) or `A+N` |
| `--week N` | — | Legacy shortcut for `--hours (N-1)*168+168` |
| `--block-hours N` | `1` | Hours averaged into one block (duration N) |
| `--stage-hours H` | `24` | Hours per stage (`0` = one stage for the window) |
| `--single-phase` | off | One phase for all stages (default: one per stage) |
| `--single-bus` | off | Copper plate (`use_single_bus`; network kept in JSON) |
| `--commitment MODE` | `none` | `none`, `relaxed` or `mip` unit commitment for thermals |
| `--fuel-prices TABLE` | `sienna` | `sienna` (dataset loader) or `typical` (EIA-style) |
| `--renewables-share S` | dataset | Rescale wind + solar to S × load energy (`0` removes them) |
| `--demand-fail-cost C` | `1000` | Value of lost load [$/MWh] |
| `--line-losses-mode M` | `none` | `model_options.line_losses_mode` |
| `--method M` | `monolithic` | `options.method` |
| `--output-format F` | gtopt default | `csv` or `parquet` solution files |
| `--cache-dir DIR` | `~/.cache/gtopt/nrel118` | Source CSVs: flat cache, a copy of `118-Bus/`, or a repo checkout |
| `--ref REF` | pinned commit | PowerSystemsTestData git ref to download from |
| `--no-download` | off | Never touch the network; require a primed cache |
| `--source-version X` | detected | PowerSystemsTestData commit to assume; see [Supported source versions](#supported-source-versions) |
| `--from-state PATH` | — | Replay a state snapshot |

Exit codes: `0` success, `1` conversion error (e.g. bad `--hours`),
`2` source data unavailable.

---

## Source data

Downloaded from
`https://raw.githubusercontent.com/NREL-Sienna/PowerSystemsTestData/<ref>/118-Bus/`
(`<ref>` defaults to commit `a2ecae9ff3cfe6d219744f1b10e4edd4eeecabc9`)
into a flat cache, ~26 MB, 125 files:

| File | Used for |
|------|----------|
| `Buses.csv` | bus numbers, region (`Area`) |
| `Lines.csv` | `Bus from/to`, `Max/Min Flow (MW)`, reactance / resistance [p.u.] |
| `Generators.csv` | bus, capacity, heat-rate bands, VO&M, ramps, min up/down, start cost, `Rating` profile file |
| `Load/partfact.csv` | per-bus load participation factor within its region |
| `Load/DA/LoadR{1,2,3}DA.csv` | hourly regional day-ahead load [MW] |
| `Wind/DA/Wind{N}DA.csv`, `Solar/DA/Solar{N}DA.csv` | hourly available output [MW] (17 wind, 75 solar) |
| `Hydro/Hydro {N}.csv` | hourly run-of-river output [MW] (`Hydro 16-35, 40-43`) |
| `Hydro/118-hydro.csv` | monthly energy budget [GWh] + capacity of `Hydro 01-15` |
| `Hydro/Hydro_nondispatchable.csv` | monthly MW of `Hydro 36-39` |

Readers normalise the upstream quirks: classic-Mac `\r` line endings,
UTF-8 BOMs, padded header names, and the 24 duplicated rows of the
8808-row hydro files (dropped at positions 1416-1439, as the
NREL-Sienna loader `data_118bus.jl` does).

---

## Supported source versions

| PowerSystemsTestData commit | Supported | Tested | Notes |
|-----------------------------|-----------|--------|-------|
| `a2ecae9ff3cfe6d219744f1b10e4edd4eeecabc9` (default, `--ref` default) | yes | no | Validated by hand; nothing in the repo runs the real `118-Bus` data, so every run warns "untested" |

**Detection**: the `SOURCE_REF` file written into a cache filled from
scratch (it names the `--ref` used), or the `HEAD` of a
PowerSystemsTestData checkout given as `--cache-dir` (root or
`118-Bus/`).  A cache filled by hand is undetectable.  Commit ids are not
ordered: an unknown commit falls back to the pinned one; an abbreviation
of 7+ characters matches.  The resolution is logged and stored in
`nrel118_to_gtopt_state.json` under `extra.source_format`.

`--source-version X` overrides the detected version.  An unknown version
(detected or given) falls back to the closest supported one with a
warning; a supported but untested version is kept with a warning.  The
same list is printed by `--help` (generated from `SOURCE_FORMAT` in
`nrel118_to_gtopt/source_version.py`).  See
[Supported source versions](../scripts-guide.md#supported-source-versions)
for every converter.

---

## Modelling

### Network

Every bus is a gtopt bus (`uid` = bus number); bus 69 is the angle
reference (as in the Sienna loader).  Lines carry `reactance` and
`resistance` in p.u. on 100 MVA with `voltage = 10` (gtopt's DC
susceptance `V²/X` then equals the per-unit `100/x`), `tmax_ab = Max
Flow` and `tmax_ba = |Min Flow|`.  Losses are off by default.

### Load

Bus load = participation factor × regional load (factors sum to 1 per
region; 91 of the 118 buses carry load).  One `Demand` per loaded bus
(`uid` = bus number, `lmax = "lmax"`).

### Generators

| Technology | Capacity | Availability |
|------------|----------|--------------|
| Thermal (CC/CT/ICE/ST NG, CT Oil, ST Coal, ST Other, Biomass, Geo) | `Max Capacity` | full |
| Wind / solar | `max(Max Capacity, peak of DA series)` | DA MW series |
| Hydro 16-35, 40-43 (run-of-river) | `max(Max Capacity, peak of series)` | hourly MW series |
| Hydro 01-15 (energy-limited) | `Max Capacity` of `118-hydro.csv` | monthly budget, load-shaped (below) |
| Hydro 36-39 | `Max Capacity` | monthly MW |

Some DA series exceed the `Max Capacity` column (up to 30 % for a few
solar units — PLEXOS `Rating` overrides it); capacity is lifted to the
series peak so the MW series is reproduced exactly.

Thermal cost: `gcost = VO&M + heat_rate × fuel price / 1.055`, where
`heat_rate` [GJ/MWh] is the full-load average of the five-band
piecewise heat-rate model and the fuel price comes from the Sienna
loader (NG 5.4, coal 1.8, oil 21, biomass 2.4, geothermal 0 $/MMBtu)
or, with `--fuel-prices typical`, from EIA-style US prices.  Each
fueled generator also carries `fuel` + `heat_rate`, so gtopt's emission
framework synthesises `EmissionSource` rows from the IPCC AR6 CO₂
factors (coal 0.0946, gas 0.0561, oil 0.0741 tCO₂/GJ; biomass
biogenic-zero).

With `--commitment none` (default) the case is a pure LP economic
dispatch (`pmin = 0`).  `--commitment relaxed|mip` adds a `Commitment`
per thermal with a minimum stable level: `pmin`, `ramp_up/down`
(MW/min × 60), `min_up_time`, `min_down_time`, `startup_cost`; stages
become `chronological`.

### Energy-limited hydro

gtopt has no multi-stage energy budget for a plain generator, so each
monthly `Max Energy Month` budget of `Hydro 01-15` is turned into an
hourly availability cap: the budget is water-filled over the month's
hours in proportion to the system load (`min(cap_month, λ·load)` with
`λ` chosen so the energy is exact).  The monthly energy is preserved
but the LP cannot move it between hours beyond that pre-shaped cap.

### Time structure

`gtopt_shared.time_grid.TimeGrid` builds the blocks (duration
`--block-hours`, mean over the block's hours, so `value × duration`
preserves energy), the stages (`--stage-hours`) and one phase per
stage.  The default — whole year, hourly blocks, daily stages — gives
366 stages × 24 blocks.

### Renewable penetration

`--renewables-share S` rescales every wind and solar unit (capacity
and series) so their available energy over the window equals `S ×`
load energy; `S = 0` removes them.  This is the penetration knob used
by the Python integration test that compares 0 % and 33 % wind+solar
against the paper's 29-34 % CO₂ reduction.

---

## Known gaps

- **Hydro energy shifting**: monthly budgets are pre-shaped (see
  above); a reservoir-style budget across stages is not modelled.
- **Reserves / RT data**: the regulation and reserve requirements and
  the real-time (`RT`) series of the dataset are not converted; the
  case is a day-ahead energy-only dispatch.
- **Piecewise heat rate**: collapsed to a scalar full-load average
  (gtopt skips piecewise heat rates in `expand_fuel_emission_sources`).
- **`Interregional lines.csv`**, `Escalators.csv`, `gen.csv` are not
  used (`Generators.csv` is the generator source).

---

## References

[1] I. Peña, C. B. Martinez-Anido, B.-M. Hodge, "An Extended IEEE
118-Bus Test System With High Renewable Penetration," *IEEE Trans.
Power Syst.*, 33(1):281-289, 2018.
[doi:10.1109/TPWRS.2017.2695963](https://doi.org/10.1109/TPWRS.2017.2695963)
