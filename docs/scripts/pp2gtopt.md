# pp2gtopt — pandapower to GTOpt Converter

Converts a **pandapower** network to a self-contained GTOpt JSON file, ready
to be solved directly with the `gtopt` binary or submitted to
**gtopt\_guisrv** / **gtopt\_websrv**.

---

## Overview

[pandapower](https://www.pandapower.org/) is a widely used open-source Python
library for power system modelling and analysis.  It provides a rich collection
of standard IEEE test networks and supports importing from MATPOWER and other
formats.

`pp2gtopt` bridges pandapower and GTOpt by converting any pandapower network —
either a built-in test case or a file saved to disk — into the GTOpt JSON
format.  The conversion handles:

- Bus definitions with voltage levels.
- Generator linearisation (quadratic cost → single linear segment).
- Transmission line impedance conversion (Ohm → per-unit reactance).
- Transformer modelling as lossless lines.
- Demand extraction from pandapower load tables.

Without profiles the output JSON is a complete, single-file gtopt case with
inline data (no external Parquet files needed), suitable for single-snapshot
dispatch or DC OPF studies.  With hourly load / renewable profiles
(`--profiles`, see [Time-series mode](#time-series-mode)) it produces ONE
multi-block, multi-stage case covering the whole profile horizon (a day, a
week, a full year) with per-block Parquet schedules.

---

## Installation

Install as part of the GTOpt scripts package:

```bash
# Standard install
pip install ./scripts

# Editable install with dev dependencies
pip install -e "./scripts[dev]"
```

pandapower is a required dependency and is installed automatically.

```bash
pp2gtopt --version
pp2gtopt --help
```

### Dependencies

| Package      | Purpose                           |
|--------------|-----------------------------------|
| `pandapower` | Network data and IEEE test cases  |
| `numpy`      | Numerical computations            |
| `pandas`     | DataFrame handling                |

---

## Quick Start

```bash
# Convert the default IEEE 30-bus network → ieee30b.json
pp2gtopt

# Convert a specific built-in test network
pp2gtopt -n case57 -o ieee57b.json

# List all available built-in networks
pp2gtopt --list-networks

# Convert a pandapower JSON file
pp2gtopt -f my_network.json -o my_case.json

# Convert a MATPOWER case file
pp2gtopt -f case39.m -o case39.json

# Convert an Excel workbook
pp2gtopt -f network.xlsx -o network.json
```

---

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `-f, --file FILE` | — | pandapower network file (`.json`, `.xlsx`/`.xls`, `.m`/`.mat`); mutually exclusive with `-n` |
| `--matpower FILE` | — | MATPOWER case (`.m` or `.mat`); alias of `-f` |
| `--mpc-var NAME` | `mpc` | struct variable inside a `.mat` file |
| `--solver`, `--method METHOD` | `cascade` (snapshot) / `monolithic` (profiles) | `options.method`: `monolithic`, `sddp`, `cascade` |
| `-n, --network NAME` | `ieee30b` | Built-in pandapower test network name; mutually exclusive with `-f` |
| `-o, --output FILE` | `<stem>.json` | Output JSON file path |
| `--list-networks` | — | Print all available built-in network names and exit |
| `-V, --version` | — | Print version and exit |
| `--source-version X` | `net.version` | pandapower series to assume; see [Supported source versions](#supported-source-versions) |
| `--profiles FILE` | — | Hourly profile table (`.csv`/`.parquet`, wide or long); enables time-series mode |
| `--profiles-spec FILE` | — | JSON/YAML column → element mapping spec |
| `--profiles-from-net` | off | Also use pandapower `ConstControl` profiles stored in the net |
| `--hours RANGE` | whole series | Hour window: `A:B` (hours A..B-1) or `A+N` |
| `--block-hours N` | `1` | Hours averaged into one block (duration N) |
| `--stage-hours H` (`--stage-length`) | whole window | Hours per stage (24 = daily, 168 = weekly) |
| `--stage-period P` | — | Calendar stages: `year`, `quarter` (Jan–Mar, Apr–Jun, …), `month`, `week` (7-day chunks) or `day`; replaces `--stage-hours` (any fixed length in hours) |
| `--start-date YYYY-MM-DD` | from a date column, else `2023-01-01` | Calendar date of profile hour 0 |
| `--block-method M` | `hour-of-day` with `--blocks-per-stage`, else `chrono` | Blocks inside a stage: `chrono` (consecutive `--block-hours`), `hour-of-day` (chronological typical day), `ldc` (load-duration curve) |
| `--blocks-per-stage X` | `12` | Blocks per stage for `hour-of-day` / `ldc`; given alone it selects `hour-of-day` |
| `--ldc-key K` | `net-load` | `ldc` sort key: load minus profiled renewable MW, or `load` |
| `--ldc-split S` | `hours` | `ldc` cut: equal `hours` per block or equal `energy` (area) |
| `--storage` / `--no-storage` | on | Convert `net.storage` into gtopt batteries |
| `--storage-efficiency P` | `0.95` | One-way battery efficiency when the storage row has none |
| `--sddp-max-iterations N` | 100 with storage, 0 without | `sddp_options.max_iterations` |
| `--sddp-convergence-tol T` | `0.01` | `sddp_options.convergence_tol` |
| `--single-phase` | off | One phase for all stages (default: one phase per stage) |
| `--output-format` | `csv` | gtopt solution output format (`csv`/`parquet`) |
| `--from-state PATH` | — | Replay a `pp2gtopt_state.json`; other flags override it |
| `--sanity-report PATH` | — | Write the post-conversion data-quality report as JSON (the summary is always logged): per-kV `|X|/V²` / `R/V²` distribution and lines below the tie threshold, kV-class impedance outliers, X/R, non-standard / mistyped bus kV, zero-rated units (and those still committed), profiles outside [0, 1], reservoirs without `emin` under an agreement reserve level, water-right rewards above the demand fail cost.  Same report on an existing case: `python -m gtopt_shared.sanity_report case.json` (runs with the post-conversion check) |

> **Note**: `-f` and `-n` are mutually exclusive.  If neither is given, the
> default `ieee30b` network is used.

---

## Supported source versions

| pandapower series | Supported | Tested | Notes |
|-------------------|-----------|--------|-------|
| `2.13`, `2.14`, `3.0` – `3.3` | yes | no | Allowed by `pandapower>=2.13.0` (`scripts/pyproject.toml`) |
| `3.4` | yes | yes | Data version of the bundled `pandapower.networks` cases |
| `3.5` (default) | yes | yes | Networks built by the tests with the installed library |

**Detection**: `net.version` of the loaded network (the writer of a
`.json` / `.xlsx`, the bundled data version, or the running library for a
MATPOWER import); `3.4.0` matches the `3.4` series.  The resolution is
logged and, in time-series mode, stored in `pp2gtopt_state.json` under
`extra.source_format`.

`--source-version X` overrides the detected version.  An unknown version
(detected or given) falls back to the closest supported one with a
warning; a supported but untested version is kept with a warning.  The
same list is printed by `--help` (generated from `SOURCE_FORMAT` in
`pp2gtopt/source_version.py`).  See
[Supported source versions](../scripts-guide.md#supported-source-versions)
for every converter.

---

## Available Built-in Networks

| Network name | pandapower function | Buses | Description |
|---|---|---|---|
| `ieee30b` *(default)* | `case_ieee30` | 30 | IEEE 30-bus (Washington) |
| `case4gs` | `case4gs` | 4 | 4-bus Glover–Sarma |
| `case5` | `case5` | 5 | 5-bus example |
| `case6ww` | `case6ww` | 6 | 6-bus Wood–Wollenberg |
| `case9` | `case9` | 9 | IEEE 9-bus (Anderson–Fouad) |
| `case14` | `case14` | 14 | IEEE 14-bus |
| `case33bw` | `case33bw` | 33 | 33-bus Baran–Wu distribution |
| `case57` | `case57` | 57 | IEEE 57-bus |
| `case118` | `case118` | 118 | IEEE 118-bus |

Use `pp2gtopt --list-networks` to see the full list in your installed version.

---

## Input from File (`-f / --file`)

The `-f` flag loads any pandapower network saved to disk.  The format is
auto-detected from the file extension:

| Extension | Format | Produced by |
|-----------|--------|-------------|
| `.json` | pandapower JSON | `pandapower.to_json()` |
| `.xlsx` / `.xls` | pandapower Excel | `pandapower.to_excel()` |
| `.m` | MATPOWER case file | MATPOWER / Octave |
| `.mat` | MATLAB file holding a MATPOWER `mpc` struct (`--mpc-var`) | MATPOWER `savecase` / `to_mpc` |

The output JSON system `name` is derived from the file stem (e.g. `case39.m`
→ `"case39"`).

---

## How the Conversion Works

### 1. Bus mapping

Each pandapower bus is mapped to a GTOpt `Bus` with:
- `uid` from the pandapower bus index.
- `name` from `bus.name` (or generated if absent).
- `kv` (voltage level in kV) from `bus.vn_kv`.
- The slack bus is identified and its `reference_theta` set to `true`.

### 2. Per-unit reactance

pandapower stores line impedances in Ohm.  The converter transforms them to
per-unit values using the system base MVA and the bus voltage:

```
x_pu = x_ohm / Z_base
Z_base = (vn_kv)² / sn_mva
```

The resulting reactance is written to the GTOpt `Line.reactance` field,
which is what the DC OPF (Kirchhoff mode) requires.

### 3. Generator cost linearisation

pandapower supports polynomial cost functions (quadratic: `cp0 + cp1·P + cp2·P²`).
Since GTOpt uses a linear cost model, the converter extracts the **linear
coefficient `cp1`** ($/MWh) as the GTOpt `gcost`.  Quadratic and constant
terms are dropped — this is appropriate for DC OPF where the linear
approximation is standard.

If no cost data is available, `gcost` defaults to `0.0`.

### 4. Transformer modelling

pandapower transformers (`net.trafo`) are converted to GTOpt `Line` entries
with `"type": "transformer"` and the following field mapping:

| pandapower field | GTOpt field | Notes |
|---|---|---|
| `hv_bus` / `lv_bus` | `bus_a` / `bus_b` | Bus indices (after remapping) |
| `vk_percent` / `sn_mva` | `reactance` | Short-circuit reactance in p.u.: `(vk_percent/100) × (base_mva/sn_mva)` |
| `sn_mva × max_loading_percent/100` | `tmax_ab`, `tmax_ba` | Thermal limit in MW |
| tap position | `tap_ratio` | Off-nominal tap ratio τ = 1 + (tap_pos − tap_neutral) × tap_step_percent/100; omitted when τ = 1.0 |
| `shift_degree` | `phase_shift_deg` | Phase-shift angle in degrees; omitted when 0 |

**Tap ratio**: the `tap_ratio` field is emitted only when the tap position
differs from the neutral position (τ ≠ 1.0).  In GTOpt the tap ratio scales
the effective susceptance: $B_{\text{eff}} = B / \tau$, so a transformer with
τ > 1 has a lower effective susceptance (harder to drive power through).

**Phase-shifting transformer (PST)**: when `shift_degree` is non-zero the
`phase_shift_deg` field is set.  In the DC OPF formulation the phase shift
modifies the right-hand side of the Kirchhoff constraint:

$$ -\theta_a + \theta_b + \tau \chi f^+ - \tau \chi f^- = -\sigma_\theta \cdot \varphi_{\text{rad}}
$$

enabling the model to replicate PST-controlled power flow for large meshed
networks (CAISO, XM, PJM, CEN Chile).

**Thermal limits**: `tmax_ab` and `tmax_ba` are set to
`sn_mva × max_loading_percent / 100` MW.  For unconstrained transformers
(very high `max_loading_percent`), the limit is simply carried over as-is.

### 5. Load / demand extraction

Each pandapower load entry becomes a GTOpt `Demand` with:
- `bus` referencing the pandapower bus index.
- `lmax` set to the load's active power `p_mw`.

### 6. Static generators (`net.sgen`)

In-service `sgen` rows (typically wind / PV) become gtopt generators after
the `ext_grid` and `gen` units, with `pmax = capacity = max_p_mw` (or
`p_mw` when no maximum is set), `pmin = 0` and `gcost` from `poly_cost`
(default 0).  Networks without sgens (all MATPOWER test cases) are
unchanged.

---

## Time-series mode

`--profiles FILE` converts the network plus hourly series into **one**
complete case for the whole horizon covered by the profile rows (or the
`--hours` sub-window): every selected hour is a block (or `--block-hours`
hours are averaged into one), blocks are grouped into stages of
`--stage-hours`, and each stage is its own phase (state-free cases solve
phase by phase with `monolithic`, the default method in this mode).

### Profile table

*Wide* layout: one row per hour, one column per profile.  An optional time
column (`hour`, `time`, `timestamp`, `datetime`, `date`, `t`, `period`,
`snapshot`, or the spec's `time_column`) only orders the rows; `--hours`
selects 0-based row positions.

*Long* layout: rows `time, id, value` (optionally `element`); detected
automatically when a `value` column and an id column (`id`, `element_id`,
`name`, `column`, `uid`) are present, or forced with `layout: long`.

### Column → element mapping

| Column name | Drives |
|---|---|
| `load:<ref>` / `gen:<ref>` / `sgen:<ref>` | element whose `name` is `<ref>`, else pandapower index `<ref>` |
| `load@bus:<b>` | every load on bus `<b>` (bus **name**, i.e. the MATPOWER bus number for `from_mpc` networks; pandapower index if buses are unnamed) |
| `load@bus_index:<i>` | every load on pandapower bus index `<i>` |
| `load@zone:<z>` | every load whose bus has `zone == <z>` |
| `load@all` | every load in the network |
| bare `<name>` | unique load, then sgen, then gen with that name |

The `@` selectors work for `gen` / `sgen` too.  When a column targets
several elements, an MW value is split in proportion to each element's base
(`p_mw` for loads, rated MW for generators); a per-unit value multiplies
each element's base.  Several columns on the same element add up.

Units: loads default to **MW**; generators default to **auto** (a column
whose maximum is ≤ 1 is per-unit availability, otherwise MW).  Generator MW
is divided by rated MW to get the availability factor, clipped to [0, 1]
with a warning.  A profiled generator's `pmin` is set to 0.

### Spec file (`--profiles-spec`, JSON or YAML)

```yaml
layout: auto              # auto | wide | long
time_column: hour
units: {load: mw, gen: auto, sgen: pu}
default: {element: load, by: bus}   # for columns that match nothing else
ignore: [total]
columns:
  R1:     {element: load, by: zone, ref: "1"}       # regional MW → split by p_mw
  Wind_7: {element: sgen, by: name, ref: W7, units: mw, scale: 1.0}
```

`by` is one of `auto`, `name`, `index`, `bus`, `bus_index`, `zone`, `all`.

### Output layout

```
case.json                              # options.input_directory = "case"
case/Demand/lmax.parquet               # stage, block, uid, value   [MW]
case/GeneratorProfile/profile.parquet  # scenario, stage, block, uid, value [p.u.]
case/BlockHourMap/block_hour_map.parquet  # year, month, day, hour(1-24), stage, block, hour_index
case/pp2gtopt_state.json               # argv + parsed options (paths relative) + summary
case/README.md
```

Profiled demands get `"lmax": "lmax"`, unprofiled demands a constant scalar
`lmax = p_mw`; profiled generators get a `capacity_profile_array` entry
(`owner_kind: generator`, `profile: "profile"`).  Block values are the mean
over their hours, so `value × duration` preserves energy.

### Validation and summary

Unmatched columns are listed in a warning (and in the state file);
negative values, missing values, empty / out-of-range `--hours`, and
`--block-hours` not dividing the window are errors (exit 1).  The log
reports the load energy (MWh), peak / minimum load, available renewable
energy and per-column energy, and warns when the peak load exceeds the
unprofiled capacity plus the mean renewable availability.

`simulation.block_hour_map` points at the `BlockHourMap` table (the
plp2gtopt `indhor` convention plus `stage` and the 0-based source-series
`hour_index`): one row per hour, saying which stage and block it was folded
into.  gtopt does not use it in the LP; it lets post-processing expand block
results back to hours.

### Monthly stages with representative blocks

`--stage-period month` makes one stage per calendar month (`quarter`,
`year`, `week` and `day` work the same way; `--stage-hours H` gives
fixed-length stages of any size) (dates come from a
date/time column of the profile file, from `--start-date`, or default to
`2023-01-01`, a non-leap year; a partial first or last month is its own
stage).  Inside each stage `--block-method` decides how hours become blocks
(`--blocks-per-stage X` alone selects `hour-of-day`, so blocks stay
chronological: with 12, block 1 = hours 0–1, block 2 = hours 2–3, …,
block 12 = hours 22–23):

| Method | Blocks | Duration |
|---|---|---|
| `chrono` | consecutive `--block-hours` chunks (chronological) | `--block-hours` |
| `hour-of-day` | `--blocks-per-stage` consecutive groups of the hours of the day (12 → 2-h groups 0–1, 2–3, …; 24 → a typical hourly day) averaged over all days of the month | hours of the day in the group × days in the month |
| `ldc` | hours sorted by `--ldc-key` (net load = total load − profiled renewable MW, default), highest first, cut into `--blocks-per-stage` groups; block 1 is the peak | hours in the group (`--ldc-split hours`: equal, ±1 h; `energy`: equal area under the shifted duration curve, so peak blocks are shorter) |

Every element (each load, each renewable) is averaged over the **same**
hours of a block, so the load / wind / solar correlation inside a block is
kept, and `value × duration` equals the hourly energy of every element in
every stage.  Block durations of a stage add up to its hours (744 for
January).

```bash
# 12 months x 12 chronological 2-h blocks of a typical day, SDDP
pp2gtopt -f net.json --profiles year.csv --stage-period month \
    --blocks-per-stage 12 --method sddp -o monthly.json

# 12 months x 12 net-load-duration blocks (not chronological), SDDP
pp2gtopt -f net.json --profiles year.csv --stage-period month \
    --block-method ldc --blocks-per-stage 12 --method sddp -o monthly_ldc.json

# 12 months x typical day of 24 hourly blocks, monolithic
pp2gtopt -f net.json --profiles year.csv --stage-period month \
    --block-method hour-of-day --blocks-per-stage 24 -o typday.json
```

`--stage-hours H` can be combined with `ldc` / `hour-of-day` too (fixed-length
stages instead of calendar ones); `--block-hours` only applies to `chrono`.

`hour-of-day` blocks follow the clock, so a battery's state of charge
links them in the daily charge / discharge order; each block, however,
stands for that hour group on every day of the month (duration = hours ×
days), so the intra-month battery schedule is a monthly-aggregate
approximation and the month-to-month state is what SDDP optimises.  `ldc`
blocks are not chronological (peak first): use them only when the order of
hours inside the month does not matter.

### Storage and the solution method

In time-series mode in-service `net.storage` rows become unified gtopt
`Battery` elements (`bus`, `emax = capacity = max_e_mwh`, `emin =
min_e_mwh`, `pmax_charge = max_p_mw` (else `sn_mva`, else `emax / 4`),
`pmax_discharge = −min_p_mw` (else `pmax_charge`), `eini = soc_percent ×
emax` (default 50 %), input/output efficiency = `√efficiency` from an
`efficiency` / `efficiency_percent` round-trip column, else
`--storage-efficiency`).  Snapshot conversions ignore storage.

| Case | `--method monolithic` (default) | `--method sddp` |
|---|---|---|
| no storage (no state) | one phase per stage, solved serially | `max_iterations = 0`: forward pass only — the same objective as monolithic, with the SDDP output layout |
| with storage | phases solved serially, state passed forward | normal SDDP: `max_iterations = 100`, `convergence_tol = 0.01` (plp2gtopt defaults); cuts carry the value of stored energy between months |

The converter logs which case applies.  `--sddp-max-iterations` overrides
the iteration count (a warning is logged when iterating a case without
state).  On case9 + wind/PV + a 400 MWh battery over a synthetic year
(12 months × 12 ldc blocks) SDDP converges in two iterations to the
monolithic cost.

### Recommended layouts for a full year

| Layout | Flags | Blocks / stages | Notes |
|---|---|---|---|
| 365 daily stages × 24 h | `--stage-hours 24` | 8760 / 365 | Small per-phase LPs; good default for dispatch studies |
| 52 weekly stages × 168 h | `--stage-hours 168` | 8760 / 53 | 8760 = 52·168 + 24, so the last stage is one day |
| Weekly × 4-h blocks | `--block-hours 4 --stage-hours 168` | 2190 / 53 | 4× fewer columns; energy-preserving averages |
| 1 stage | (no `--stage-hours`) | 8760 / 1 | One LP for the whole year; only for small networks |
| 12 months × 12 ldc blocks | `--stage-period month --block-method ldc --blocks-per-stage 12` | 144 / 12 | Non-chronological; SDDP with storage |
| 12 months × typical day | `--stage-period month --block-method hour-of-day --blocks-per-stage 24` | 288 / 12 | Keeps the daily shape |

For case118 with 15 renewables, the 365-daily-stage year converts in ~9 s
and `gtopt --lp-only` builds it in ~70 s (~2 GB).  For a leap-year series
(8784 h) the daily layout gives 366 stages.

### Examples

```bash
# One week of hourly data, daily stages
pp2gtopt -n case118 --profiles profiles.csv --hours 0:168 --stage-hours 24 \
    -o case118_week.json
gtopt case118_week.json

# NREL-118 style: regional MW loads (R1..R3 = bus zones) + per-plant MW
# wind/solar availability, full year in weekly stages
pp2gtopt --matpower nrel118.m --profiles nrel118_year.parquet \
    --profiles-spec nrel118_map.yaml --stage-hours 168 -o nrel118_year.json

# Xu 3266-bus style: per-bus load columns + per-unit renewable columns
pp2gtopt --matpower cn3266.mat --mpc-var mpc --profiles load_renew_data.csv \
    --profiles-spec xu_map.yaml --block-hours 2 --stage-hours 24 -o cn3266.json

# Replay (paths in the state file are relative to it)
pp2gtopt --from-state nrel118_year/pp2gtopt_state.json
pp2gtopt --from-state nrel118_year/pp2gtopt_state.json --hours 0:168
```

with, e.g., `nrel118_map.yaml`:

```yaml
units: {load: mw, sgen: mw, gen: mw}
columns:
  R1: {element: load, by: zone, ref: "1"}
  R2: {element: load, by: zone, ref: "2"}
  R3: {element: load, by: zone, ref: "3"}
```

and `xu_map.yaml` (bus-numbered load columns, `W*`/`PV*` named units):

```yaml
default: {element: load, by: bus}
units: {gen: pu}
```

### Limitations

- One scenario; profiles are deterministic (several profile years as SDDP
  scenarios are not supported yet — convert each year separately).
- Hour selection is positional (`--hours`), not by calendar date; rows are
  assumed hourly and gap-free.
- `ext_grid` units cannot be profiled; only `load`, `gen`, `sgen`.
- Reactive power and `q_mvar` profiles are ignored; storage is converted
  only in time-series mode.
- Xu's `system_para.mat` is not a MATPOWER `mpc` struct; it must first be
  turned into a MATPOWER case (or a pandapower JSON) — pp2gtopt then maps
  `load_renew_data.csv` columns with a spec.

---

## Output JSON Structure

The output is a standard GTOpt JSON file with inline arrays:

```json
{
  "system": {
    "name": "case57",
    "bus_array": [ ... ],
    "generator_array": [ ... ],
    "demand_array": [ ... ],
    "line_array": [ ... ]
  },
  "simulation": {
    "block_array": [{"uid": 1, "duration": 1.0}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
    "scenario_array": [{"uid": 1, "probability_factor": 1.0}]
  },
  "options": {
    "use_kirchhoff": true,
    "scale_objective": 1000,
    "demand_fail_cost": 1000
  }
}
```

Without `--profiles` the simulation is a single snapshot (1 block, 1 stage,
1 scenario) suitable for static DC OPF dispatch; see
[Time-series mode](#time-series-mode) for multi-block cases.

---

## Example: Converting IEEE 57-Bus and Verifying with GTOpt

```bash
# Step 1: Convert the IEEE 57-bus network
pp2gtopt -n case57 -o ieee57b.json

# Step 2: Solve with gtopt (single-snapshot DC OPF)
gtopt ieee57b.json

# Step 3: Check the solution
cat output/solution.csv
# → status=0 (optimal), obj_value=... (total cost, physical units)

# Step 4: Inspect generator dispatch
cat output/Generator/generation_sol.csv

# Step 5: Check bus marginal prices (LMPs)
cat output/Bus/balance_dual.csv

# Step 6 (optional): Cross-validate against pandapower DC OPF
# Note: the gtopt_compare case name 'ieee_57b' differs from the
# pp2gtopt built-in network name 'case57' — they refer to the same network
gtopt_compare --case ieee_57b --gtopt-output output/
```

---

## See Also

- [SCRIPTS.md](../scripts-guide.md) — Overview of all GTOpt Python scripts
- [gtopt_compare](gtopt_compare.md) — Validate GTOpt results against
  pandapower DC OPF
- [plp2gtopt](plp2gtopt.md) — Convert PLP cases to GTOpt
- [PLANNING\_GUIDE.md](../planning-guide.md) — Guide to GTOpt planning concepts
