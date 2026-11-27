# plexos2gtopt — PLEXOS to GTOpt Converter

Converts a **CEN PCP daily PLEXOS bundle**
(`PLEXOS{YYYYMMDD}.zip` / `DATOS{YYYYMMDD}.zip[.xz]`) into a GTOpt
planning JSON + Parquet schedule tree, so a PLEXOS-modelled power
system can be solved with the GTOpt LP/MIP backend (CPLEX, HiGHS,
CBC, CLP) and compared end-to-end against the PLEXOS reference
solution.

It is the PLEXOS sibling of [`plp2gtopt`](plp2gtopt.md) (PLP Fortran
dialect) and [`sddp2gtopt`](sddp2gtopt.md) (PSR-SDDP dialect), and
reuses GTOpt's native LP element catalogue + AMPL-style
`UserConstraint` framework rather than emitting a parallel modelling
surface.

> **Audience.** This document is the critical-review reference for
> `plexos2gtopt`: it specifies how it works, what files it depends
> on, every GTOpt element it emits, every constraint family, the
> validation passes that confirm the conversion, and how to run the
> converted case (both MIP and LP-relax). See `scripts/plexos2gtopt/DESIGN.md`
> for the original v0 design proposal (most of which is implemented).

---

## 1. What it does, in one paragraph

PLEXOS keeps the model schema in a single MasterDataSet XML
(`DBSEN_PRGDIARIO.xml`, ~36 MB) and ships time-series data in
~50 sibling CSVs (one per object class). `plexos2gtopt` opens the
bundle (zip / zip.xz / extracted directory), walks the XML object
graph (objects, classes, memberships, properties), reads every
per-class CSV at the chosen horizon, builds an in-memory
`PlexosCase` of frozen typed dataclasses (one per converted entity
class), then runs the GTOpt writer which emits:

* a self-contained planning JSON (`<bundle>.json`),
* a per-class Parquet schedule tree (`Generator/pmax.parquet`,
  `Reservoir/inflow.parquet`, …, one column per object),
* per-family modular AMPL files (`uc_<family>.pampl`) for the
  PLEXOS-translated `UserConstraint` rows,
* a `boundary_cuts.csv` carrying the PLEXOS Future-Cost-Function
  hyperplane (FCF terminal value, when present),
* a curated `solvers/cplex.prm` (`MIP Gomory = 2`,
  `MIPGap = 0.01`),
* a sidecar `<bundle>.provenance.json` documenting per-element
  PLEXOS source / units / transforms.

A post-conversion validation pass (`run_post_check`) immediately
diffs the converted JSON against the PLEXOS bundle to confirm every
class, constraint family and global indicator landed where expected
— this is the converter's primary correctness gate.

---

## 2. Quick start

### Install / locate

The script lives at `scripts/plexos2gtopt/`. It is registered as an
entry point in `scripts/pyproject.toml`, so a `pip install -e .` (or
the in-tree alias) exposes `plexos2gtopt` on `PATH`. The same module
can always be run as `python -m plexos2gtopt`.

The converter needs its bundled data files — notably the PLEXOS-HARD
user-constraint list `plexos2gtopt/data/cen_pcp_hard_ucs.txt` and the
`solvers/*.prm` files.  A missing file is a hard error
(`MissingDataFileError`).  Installs made before 2026-11 with a regular
(non-editable) `pip install ./scripts` lacked them and silently turned
~100 CEN user constraints soft; reinstall with
`pip install --force-reinstall --no-deps ./scripts` and reconvert.  The
log line `PLEXOS-HARD UC list: N names loaded from …` confirms the list
was applied.  See [Bundled data files](../scripts-guide.md#bundled-data-files).

### Minimum run

```bash
# Sanity check + summary (no conversion):
plexos2gtopt --info $GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/PLEXOS20260422.zip

# Schema-light validation (no conversion):
plexos2gtopt --validate $GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/

# Default conversion (writes to gtopt_PLEXOS20260422/ next to the input).
# Auto-discovers RES<DATE>.zip[.xz] sibling for the PLEXOS block layout.
plexos2gtopt $GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/PLEXOS20260422.zip \
  -o gtopt_PLEXOS20260422

# Standalone re-comparison: parse the bundle vs an existing JSON.
plexos2gtopt --compare gtopt_PLEXOS20260422/PLEXOS20260422.json \
  $GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/PLEXOS20260422.zip

# Solve the resulting case (MIP by default; --solver picks the backend):
gtopt gtopt_PLEXOS20260422/PLEXOS20260422.json --solver cplex
```

### Accepted input shapes

`plexos_loader.locate_bundle` accepts any of:

* A directory already containing `DBSEN_PRGDIARIO.xml` + the per-class
  CSVs (extracted by hand).
* A `DATOS{YYYYMMDD}.zip` archive (the input dataset).
* A `DATOS{YYYYMMDD}.zip.xz` (the on-disk form vendored under
  `$GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/`).
* A `PLEXOS{YYYYMMDD}.zip` outer wrapper (auto-unwrapped to its
  DATOS inner zip).
* A directory containing a single `DATOS*.zip[.xz]` inside (auto-
  located).

The optional `RES{YYYYMMDD}.zip[.xz]` solution archive is **not
required** for the conversion, but `plexos2gtopt` will auto-discover
it next to the input when present and pull two things from it:

1. the exact `t_phase_3` block layout PLEXOS solved over
   (111 variable-duration blocks per CEN PCP week — under
   `--horizon-mode plexos`, the default);
2. a cached zstd CSV mirror of every solution table
   (`<output_dir>/plexos_cache/*.csv.zst`) for downstream
   `compare_with_plexos` (Step-3 cost / energy / SRMC / LMP diffs).

### Importing PLEXOS results (`--results`)

```bash
plexos2gtopt -i DATOS20260422.zip.xz --results           # RES bundle next to the input
plexos2gtopt -i bundle -o gtopt_case --results Solution.accdb
plexos2gtopt -i plexos_ds -o gtopt_case --results plexos_ds/plexos_solution --results-only
```

| Flag | Default | Description |
|------|---------|-------------|
| `--results [PATH]` | off | Import the PLEXOS results in PATH as a gtopt output directory; without PATH: `--plexos-solution-accdb`, the `RES*.zip[.xz]` next to the input, or `<input>/plexos_solution` |
| `--results-only` | off | With `--results`: skip the conversion and import against the planning JSON already in the output directory |
| `--results-output DIR` | `<output dir>/results_plexos` | Where the imported results go (kept apart from `<out>/results`, where a gtopt run of the case writes) |

PATH is a solution `.accdb` (read with `mdb-export`), a `RES*.zip[.xz]`
bundle or a directory of `t_*.csv` solution tables (what
`gtopt2plexos --results` writes).

| PLEXOS property | gtopt stream |
|-----------------|--------------|
| `Generator.Generation` | `Generator/generation_sol` |
| `Node.Price` | `Bus/balance_dual` |
| `Node.Load` - `Node.Unserved Energy`; `Node.Unserved Energy` | `Demand/load_sol`; `Demand/fail_sol` |
| `Line.Flow`, `Line.Loss` | `Line/flow_sol`, `Line/loss_sol` |
| `Storage.End Volume` (`Initial Volume`) | `Reservoir/energy_sol`, `efin_sol`, `eini_sol` |
| `Storage.Shadow Price` | `Reservoir/water_value_dual` |
| `Battery.Energy`, `Generation`, `Load` | `Battery/energy_sol`, `fout_sol`, `finp_sol` |

plexos2gtopt keeps PLEXOS names and units (reservoir volumes stay in the
dataset's unit, e.g. CMD), so values are copied as they are.  PLEXOS period
`p` is the `p`-th block of the case (a different count warns).  A
multi-scenario case reads sample `s` for scenario `s`, else the sample-0
mean (with a warning).  No operating cost is read: `solution.csv` reports
generation x `gcost` + deficit x fail cost (a warning says so).

The imported directory is written exactly as a `gtopt` run of the converted
case would write it: one hive partition per (scene, phase)
(`<Class>/<field>.parquet/scene=<S>/phase=<P>/part.parquet`), the long
schema `scenario, stage, block, uid` (`int32`) `value` (`float32`, rounded
to `output_round_decimals`), gtopt's uids, Parquet `zstd` (or the case's
`output_compression`), exact zeros of `_sol` streams dropped and `_dual`
streams dense, plus `solution.csv` (gtopt's header, one row per
(scene, phase)), `solver_status.json` (`"method": "imported"`),
`planning.json` and a `README.md`.  Batteries with a `bus` get the
`<battery>_gen` / `<battery>_dem` streams gtopt synthesises for them.
Readers of gtopt output (`gtopt_results_summary`,
[`gtopt_compare_runs`](gtopt_compare_runs.md), `GtoptResults`) load it
unchanged, so an external run compares against a gtopt run of the same case
with

```bash
gtopt_compare_runs <out>/results_plexos <out>/results   # external vs gtopt
```

`solution.csv` reports, per (scene, phase), the probability-weighted cost of
the scene from the phase on (the convention of a phased gtopt run; the whole
horizon in a single-phase case).  Names that match no element of the case
are listed as warnings (console, `solver_status.json`, `README.md`).

---

## 3. Input file inventory and what they feed

### 3.1 Master schema (XML)

| File | Role |
|---|---|
| `DBSEN_PRGDIARIO.xml` | The PLEXOS MasterDataSet object graph: `t_class` (96 object classes), `t_object` (~4 500 instances), `t_attribute`, `t_property`, `t_collection`, `t_membership`, `t_data` (per-property numeric values), `t_band`, `t_text`, `t_memo_data`, `t_action`. XML namespace `http://tempuri.org/MasterDataSet.xsd`. |
| `PLEXOS_Param.xml` | Run-parameters wrapper (`Day Beginning`, `Step Count`, `Step Type`, `Horizon`). Used to infer the run horizon length (`*_7d` → 7 days) when no solution layout is available. |

### 3.2 Per-class CSV time-series

Every CSV is in one of two layouts:

* **Long** — `NAME, YEAR, MONTH, DAY, PERIOD, BAND, VALUE`. `PERIOD`
  is 1-based hour-of-day; `BAND` carries piecewise / multi-segment
  records. Read by `plexos_csv.read_long`.
* **Wide** — `YEAR, MONTH, DAY, PERIOD, <obj1>, <obj2>, …`. One
  column per object, one row per hour. Read by `plexos_csv.read_wide`.

Each file feeds at least one GTOpt field; the converter's input ledger
(displayed in the post-check report under "PLEXOS Input Files →
Indicators") makes this auditable in every run:

| CSV | Carries | Drives GTOpt field |
|---|---|---|
| `Gen_Rating.csv` | per-(unit, hour) pmax (MW) + DLR profile | `Generator.pmax` (scalar + per-block) |
| `Gen_UnitsOut.csv` | forced-outage schedule | `Generator.pmax_factor` (derate by units-out) |
| `Gen_FixedLoad.csv` | per-unit must-run / forced output | `Generator.pmin`/`pmax` pinning + soft `pmin_fcost` |
| `Gen_MinStableLevel.csv` | per-unit / per-period Pmin (MW) | `Commitment.pmin` / `Commitment.pmin_profile` |
| `Gen_HeatRate.csv` | heat rate (multi-band optional) | `Generator.heat_rate` + `heat_rate_segments` |
| `Gen_VOMCharge.csv` | VOM ($/MWh) | folded into `Generator.gcost` |
| `Gen_AuxUse.csv` | auxiliary station-service fraction | derates net capacity (folded into gcost) |
| `Gen_FuelTransportCharge.csv` | fuel transport adder in $/fuel-unit (PLEXOS prices fuel at `price + transport`) | folded into `Generator.gcost` as `transport × heat_rate` |
| `Gen_IniGeneration.csv` | dispatch at `t=0` | `Commitment.initial_power` (trimmed) and `Generator.initial_generation` (raw) |
| `Gen_IniUnits.csv` | units initially online | `Commitment.initial_status` |
| `Gen_IniHoursUp.csv` / `Gen_IniHoursDown.csv` | hours up/down at `t=0` | `Commitment.initial_hours` |
| `Gen_Commit.csv` | commit override (`-1` free / `0` off / `+1` on) | `Commitment.must_run` / `fixed_status` per block |
| `Gen_StartCost.csv` | startup cost ($) | `Commitment.startup_cost` |
| `Gen_ShutDownCost.csv` | shutdown cost ($) | `Commitment.shutdown_cost` |
| `CFdata/CPF/*.csv` (`<gen>_MRU/MRD`) | per-unit ramp curves | `Commitment.ramp_up`/`ramp_down` (+ profile) |
| `Lin_MaxRating.csv` | line forward rating (steady-state) | `Line.tmax_normal_ab` + per-block DLR |
| `Lin_MinRating.csv` | line reverse rating | `Line.tmin_ab` |
| `Lin_Units.csv` | parallel circuits + in-service schedule | `Line.capacity` + `Line.in_service` profile |
| `Nod_Load.csv` | per-bus per-hour load | `Demand.lmax` |
| `BESS_IniValue.csv` | battery initial SOC | `Battery.eini` |
| `Hydro_MaxVolume.csv` | reservoir max volume (hm³) | `Reservoir.emax` (+ optional per-block) |
| `Hydro_MinVolume.csv` | reservoir min volume (hm³) | `Reservoir.emin` (+ optional per-block) |
| `Hydro_InitialVolume.csv` | reservoir initial volume | `Reservoir.eini` / `efin` |
| `Hydro_WaterFlows.csv` | natural inflow + forced flow profile | `Flow.discharge` + `Waterway.forced_flow_profile` |
| `Hydro_EfficiencyIncr.csv` | turbine productibility | `Turbine.production_factor` (`fp_med`) |
| `Hydro_StoWaterValues.csv` | water values + FCF intercept | `BoundaryCutSpec` → `boundary_cuts.csv` |
| `Hydro_AntucoBounds.csv` | hourly RHS of `ANTUCOmin` / `ANTUCOmax` / `ELTOROmax` (MW) | per-block RHS of those UCs (rows aligned by date to the horizon start; `PERIOD` rows step within the day) |
| `Hydro_MaxRampDay.csv` | hydro daily-ramp limits | per-day RHS overlay on `*ramp*` UCs (aligned by date to the horizon start) |
| `Res_Requirement.csv` | reserve requirement (MW) | `ReserveZone.urreq` / `drreq` |
| `Res_Timeslice.csv` | reserve day-type / hour mapping | per-day day-type selector for `Res_Requirement` |
| `SSCC_Activation_BESS.csv` | BESS ancillary-services activation % | `ReserveProvision.ur_provision_factor` / `dr_provision_factor` |
| `Fuel_Price.csv` | monthly fuel prices ($/MMBtu) | `Fuel.price` |
| `Fuel_MaxOfftakeWeek.csv` | weekly fuel offtake limits | `Fuel.max_offtake` (soft) |

### 3.3 Solution database (optional — read only when needed)

| File | Used by |
|---|---|
| `<Model> Solution.accdb` (inside `RES<DATE>.zip`) | Block layout (`t_phase_3`, 111 blocks for CEN PCP weekly); plus the cached PLEXOS solution tables for `--use-plexos-commit` / `--use-plexos-gen-cap` / `--use-plexos-efin` curve-fit modes and for the downstream `compare_with_plexos` Step-3 tables. |

The default conversion is strictly **inputs-only**: the solution is
consulted **only** for the block grouping. Setting any of the
`--use-plexos-*` curve-fit flags activates a heavier mdb-export + zstd
pass that mirrors the relevant `.accdb` tables to
`<output_dir>/plexos_cache/`.

---

## 4. Architecture and module map

```
scripts/plexos2gtopt/
├── __main__.py             # python -m plexos2gtopt
├── main.py                 # argparse / CLI dispatch (~900 lines)
├── DESIGN.md               # original v0 design (specification)
├── plexos2gtopt.py         # convert/validate/compare entry points (~550 lines)
├── plexos_loader.py        # bundle locator (zip / zip.xz / outer wrapper)
├── plexos_xml.py           # MasterDataSet XML reader (objects, properties, memberships)
├── plexos_csv.py           # long/wide CSV readers (multi-day, fill-forward)
├── plexos_block_layout.py  # .accdb t_phase_3 reader + cache_plexos_tables
├── entities.py             # 24 frozen dataclasses (one per emitted entity)
├── parsers.py              # ~30 extract_*(...) functions, ~9 300 lines
├── gtopt_writer.py         # build_planning + per-class build_*_array (~3 500 lines)
├── uc_families.py          # the 8-family UC taxonomy (single source of truth)
├── _comparison.py          # post-conversion PLEXOS↔gtopt comparison (~1 500 lines)
├── compare_with_plexos.py  # standalone Step-3 PLEXOS solution diff (cost / MWh / SRMC / LMP)
├── auto_lift_lines.py      # post-conversion radial-cap detection (pandapower / gtopt DC OPF)
├── info_display.py         # --info renderer
├── solvers/                # bundled cplex.prm shipped into every output dir
└── tests/                  # 25 unit/integration test files (≥83% coverage)
```

### High-level pipeline

```
                 ┌──────────────────────────────────────────────────┐
                 │           PLEXOS bundle on disk                  │
                 │  PLEXOS{date}.zip │ DATOS{date}.zip[.xz] │ dir   │
                 └──────────────────────────────────────────────────┘
                                       │
                                       ▼
                       plexos_loader.locate_bundle
                       (auto-unwrap outer / xz / zip;
                        return PlexosBundle root dir)
                                       │
            ┌──────────────────────────┼────────────────────────────┐
            ▼                          ▼                            ▼
  plexos_xml.load_xml         plexos_csv.read_long           plexos_block_layout
  (PlexosDb: t_class /        plexos_csv.read_wide           .load_block_layout_from_accdb
   t_object / t_data /        (per-class CSV → dict[name →   (RES*.zip → .accdb →
   t_membership / t_band)     list[float]])                   111-block t_phase_3 grid)
            │                          │                            │
            └──────────────────────────┴────────────────────────────┘
                                       │
                                       ▼
                          parsers.extract_case(bundle)
                          (~30 extract_*(...) per-class
                           extractors → typed entities
                           in entities.py)
                                       │
                                       ▼
                          gtopt_writer.build_planning(case)
                          (per-class build_*_array → planning dict)
                                       │
                  ┌────────────────────┴──────────────────┐
                  ▼                                       ▼
       write_planning(planning, *.json)       write_user_constraint_pampl
       + Parquet schedule tree                (per-family uc_<fam>.pampl)
       + boundary_cuts.csv                    + write_boundary_cut_csv
       + solvers/cplex.prm                    + write_provenance(*.provenance.json)
                                       │
                                       ▼
                     _comparison.run_post_check(planning, case)
                     (element counts / UC families / global indicators /
                      drop funnel + input-file ledger + JSON validation)
                                       │
                                       ▼
                                Exit code = #CRITICAL findings
```

### Why a multi-file layout (not a single `tools/plexos2gtopt.py`)

PLEXOS input is structurally identical to PSR-SDDP — XML object
database + per-class CSVs — so the `sddp2gtopt` hybrid layout
(entity dataclasses + per-class extractors + per-class writers)
maps naturally. The XML reader alone (objects + properties +
memberships + bands) is ~700 lines; the writer is ~3 500 lines;
the comparison is ~1 500. A single-file converter was rejected in
the design doc as unmaintainable.

The clean split also means PLEXOS schema upgrades (the 96 enabled
classes are a moving target, and `class_id` re-use across releases
is documented) only churn `plexos_xml.py` + the affected
`extract_*`.

---

## 5. Time model

CEN PCP runs in two horizon modes; both are emitted as a single
deterministic stage with a chronological block grid:

| Mode | Selector | Block grid | Used when |
|---|---|---|---|
| `plexos` (default) | `--horizon-mode plexos` | The exact `t_phase_3` variable-duration grouping PLEXOS solved over (111 blocks across 7 days for CEN PCP weekly, `[24, 20, 13, 14, 12, 15, 13]` per day) | A sibling `RES<DATE>.zip[.xz]` (or explicit `--plexos-solution-accdb`) provides the layout |
| `plexos` fallback / `hourly` | `--horizon-mode hourly`, or `plexos` with no solution available | Uniform 1-hour blocks (`24 × n_days`, e.g. 168 for a week) | No solution available — runs the same wall-clock horizon at finer block granularity. `--block-layout` can override either with an inline `{uid:dur,…}` / `d1,d2,…` spec or a CSV `duration` column. |

The simulation block emits one `scenario`, one `scene` (implicit),
one `phase` (implicit), one `stage` with `chronological: True`, and
N blocks of the chosen layout. `chronological: True` is **required**
— `commitment_lp.cpp` early-returns for non-chronological stages,
leaving `status`/`startup`/`shutdown` columns unbuilt and breaking
every UserConstraint that references them.

### Cutting the week short: `--horizon-days N`

`--horizon-mode hourly --horizon-days N` with N below the PLEXOS
horizon (7 for CEN PCP, read from the `Horizon` name) moves the end of
the horizon to the end of day N.  Every end-of-horizon quantity of the
weekly case is re-anchored there by one post-build pass
(`_horizon.py`), reported in the log and in the provenance sidecar
(`horizon`: per-element targets, per-family rules, warnings):

| Item | Default rule | Override |
|---|---|---|
| Reservoir / gas-storage `efin` | PLEXOS Storage `End Volume` at the end of day N (RES bundle); `eini` with a warning when there is no solution or no value | `--horizon-end-targets {plexos,eini,free}` |
| Battery `efin` | PLEXOS Battery `Energy` at the end of day N, soft (`efin_cost`), `daily_cycle: false`, `use_state_variable: true` | same |
| LNG tanks (`lng_terminal_array`, `*_GNL*`, storages feeding fuel-burning units) | left free: their end level is the take-or-pay decision | `--horizon-pin-lng-tanks` |
| FCF boundary cut | dropped with `plexos`/`eini` targets, kept with `free` (then the only terminal value) | `--horizon-boundary-cut {keep,drop}` |
| `fuel_max_offtake` (`Fuel_MaxOfftakeWeek`) | `prorate`: weekly cap × days in the horizon / 7 | `full` (whole weekly budget), `drop` |
| `fuel_min_offtake` (take-or-pay floor) | `prorate`: floor × horizon / window | `drop` |
| `period_rhs` (`RHS Week/Month/Year` stage rows) | `prorate`: RHS × horizon hours / period hours | `drop` (inactive) |
| `day_indexed` (`Gas_MaxOpDay` Day_X budgets) | `keep`: day d keeps its own Day_X cap | `drop` (inactive) |

Families are overridden with `--horizon-constraint-rules
fuel_max_offtake=full,period_rhs=drop`.  Targets are clamped into each
element's `[emin, emax]` at the last block.  Rescaling the FCF cut is
not offered: its slopes are marginal water values and its intercept a
constant, so only keeping or dropping it changes the solution.
Batteries get the soft cycle anchor in every horizon mode (an empty
hourly block layout used to leave them on gtopt's hard daily-cycle
close, infeasible for a battery that self-discharges and cannot
recharge — PCP 2024-12-15, BAT_ALFALFAL_VR2).
Battery self-discharge mirrors PLEXOS: the `Self Discharge Rate` property
(% of stored energy lost per hour, default 0) maps to
`annual_standing_loss = rate / 100 × 8760`, and a battery without it is
written with `annual_standing_loss: 0`, never left to gtopt's 4 %/month
lithium default (that default made CEN's `BAT_ANDES_2A_FV_Export` row,
which holds SoC at Capacity for hours with charging off, infeasible on
PCP 2026-11-03 / 11-09).  `--default-storage-loss` still writes its
synthetic 2 %/month on cycle-limited BESS.

---

## 6. The GTOpt element catalogue: every class plexos2gtopt emits

`plexos2gtopt` does **not** introduce a parallel modelling surface —
every PLEXOS object lands on a gtopt-native LP class. The full set
(observed on the CEN PCP 2026-04-22 weekly bundle):

| GTOpt class | Count on PCP weekly | PLEXOS source | LP role |
|---|---|---|---|
| `Bus` | 247 | `t_class[Node]` (class_id 22) | Power-balance node |
| `Generator` | 1 732 | `t_class[Generator]` (class_id 2) | Thermal (with `fuel`), renewable (Gen_Rating-driven), and hydro turbine (with `Storage` membership) |
| `Fuel` | 221 | `t_class[Fuel]` (class_id 4) + virtual unit-price fuel | `Generator.fuel` reference, drives `cost = HR · (price + transport) + VOM` |
| `Emission` | (when present) | `Fuel.CO2 Production Rate` or `Emission→Fuel` Production Rate | Per-fuel CO₂ factor |
| `Battery` | 36 | `t_class[Battery]` (class_id 7) | Energy storage; GTOpt auto-instantiates `<bat>_gen` discharge + `<bat>_load` charge |
| `Line` | 317 | `t_class[Line]` + Node-from/to memberships | Transmission with PWL losses; soft/hard cap pair (`tmax_normal_ab` / `tmax_ab`) drives the overload band |
| `Demand` | 127 | synthesised one per non-zero column of `Nod_Load.csv` | Hourly load curtailable at `Region.VoLL` ($/MWh) |
| `Reservoir` | 12 | `t_class[Storage]` (class_id 8), after pondage demotion | Hydraulic storage state; carries `emin`/`emax`/`eini`/`efin`/`spillway_cost` |
| `Junction` | 32 | synthesised one per Storage / sink / `<src>_ocean` drain | Hydraulic balance node; carries `drain`/`drain_capacity`/`drain_cost` for terminal cascades |
| `Waterway` | 16 | `t_class[Waterway]` (class_id 9), minus turbine arcs (built into Turbine) and collapsed Vert arcs (onto Junction.drain) | Physical spillage / scheduled bypass / forced-flow arcs |
| `Turbine` | 35 | `Generator` ∩ `Storage` membership | Hydro generator with built-in waterway flow arc (`junction_a`/`junction_b`) + power conversion; tail storage → `junction_b` |
| `Flow` | 16 | natural-inflow rows in `Hydro_WaterFlows.csv` | Per-block discharge injected into a Junction |
| `ReserveZone` | 12 | `t_class[Reserve]` | Up/down reserve product (RS = raise, LW = lower), with `urreq`/`drreq` per block |
| `ReserveProvision` | 393 | Reserve → Generator/Battery memberships | Per-generator eligibility + per-block `ur`/`dn` provision column; SSCC BESS rows are emitted per (battery, `*_BESS` zone) |
| `InertiaZone` / `InertiaProvision` | 3 / 259 | `Inertia_Calculation_e1`, `InertiaNRMin_D` / `_N` (`--reserve-model native`) | `Inertia_SEN` zone (CPF plane inertia; not emitted with `--reserve-inertia-term exogenous`) and the northern inertia minima; one provision per committed unit (`pf = H·S / P_ref`) |
| `Commitment` | 1 578 | `Generator` thermal subset + auto-promoted hydro min/max UCs | Unit-commitment binaries (`status`/`startup`/`shutdown`) with `pmin`/`pmax`/`ramp`/`min_up_time`/`max_starts` |
| `DecisionVariable` | 44 | `t_class[Decision Variable]` (class_id 72) | Free continuous LP column with optional bounds + cost; referenced via `decision_variable("X").value` |
| `Plant` | (when present) | synthesised from multi-fuel-band generator families | Native plant-cap row `Σ generation ≤ pmax` per (stage, block) — replaces the legacy `PlantCap_*` UCs |
| `FlowRight` | (when present) | pinned forced-flow waterways | Soft irrigation / environmental flow envelope on a Junction |
| `UserConstraint` | 3 595 (across 8 families, see §7) | `t_class[Constraint]` + synthesised hydro / plant / FCF rows | Generic AMPL-style LP row binding any combination of LP variables |
| `BoundaryCut` | 1 | `Hydro_StoWaterValues.csv` | End-of-horizon FCF terminal-value cut on reservoir end volumes |

### Concepts that do not have a direct GTOpt match

* PLEXOS **`Region` / `Zone` / `Area`** — folded into informational
  `bus.area` tags; the per-region `Region.VoLL` is collapsed to
  `model_options.demand_fail_cost` when consistent across regions
  (CEN PCP ships 467.19 $/MWh).
* PLEXOS **`Horizon` / `Chronology`** — collapsed to the static block
  layout described in §5.
* PLEXOS **`Variable` / `Definition`** symbolic scenarios — replaced
  by GTOpt's explicit `scenario_array` (single scenario in CEN PCP).
* PLEXOS **wheeling / contract / charge / tariff** classes — skipped
  with a debug log (no current GTOpt equivalent).

### Which commitments keep an integer status (`--uc-relax`)

Every `Commitment` adds one binary status column `u` per block.  Most
PCP commitments carry no unit-commitment decision worth a binary, so
the converter emits them with `relax: true` (`u`, `v`, `w` continuous in
[0, 1]) when either rule holds
(`scripts/plexos2gtopt/_commitment_policy.py`):

1. **PLEXOS Linear Relaxation** — the run Model's effective
   `Generator.Unit Commitment Optimality` is `0`.  PLEXOS itself solves
   those units with a continuous `Units Generating`, so this follows the
   source model.  Rows are resolved like `Include in ST Schedule`: a row
   tagged with an active scenario beats the untagged default, rows tagged
   only with inactive scenarios are ignored (CEN's `SouthLinearUnits`,
   `LinearGTs`, … are not in `PRGdia_Full_Definitivo`).
2. **Cost-free** (`auto` only) — all of: zero energy cost
   (`VO&M + transport + max heat rate × max fuel price ≤ 1e-6` $/MWh), no
   start-up / shut-down / no-load cost or no-load fuel, no Min Up / Down
   Time, no start-count limit, no ramp limit.  This catches zero-cost
   renewables and run-of-river that PLEXOS marks Integer Optimal (`2`) or
   leaves unset, e.g. `ABANICO`, `TALINAY_*`, `VALDIVIA_BL1_EUCA`.  The
   only behaviour it changes is that such a unit may be curtailed into
   (0, Min Stable Level).  For a unit with no cost or dynamics that
   region has no economic consequence.

A `Min Stable Level` or a `Gen_Commit` pin does not block the
relaxation.  The commitment is still emitted, so forced blocks keep
`fixed_status` / `must_run` and every `commitment("uc_X").status`
reference in the PAMPL rows (inertia, reserves) stays valid.
Commitments synthesised from hydro `<plant>min` / `<plant>max`
Constraints are never relaxed.

| `--uc-relax` | Effect |
|---|---|
| `auto` (default) | rules 1 + 2 |
| `plexos` | rule 1 only (strictly PLEXOS's Unit Commitment Optimality) |
| `none` | every commitment integer (the behaviour before 2026-11) |

`--lp-relax` still relaxes every commitment.  The converter logs
`commitment policy (--uc-relax auto): N of M commitment(s) LP-relaxed —
X PLEXOS Linear Relaxation, Y cost-free`.  On the 14 published CEN PCP
weeks only the `relax` flags change; the JSON and PAMPL are otherwise
identical:

| Week | Integer units | Integer `u` columns | Free (unpinned) `u` cells |
|---|---|---|---|
| 2025-10-05 | 1009 → 228 | 169 512 → 38 304 | 47 516 → 35 679 |
| 2025-10-19 | 1001 → 217 | 107 107 → 23 219 | 29 600 → 21 571 |
| 2025-11-09 | 1005 → 216 | 118 590 → 25 488 | 32 707 → 24 050 |
| 2025-11-23 | 996 → 219 | 131 472 → 28 908 | 38 134 → 27 271 |
| 2025-12-07 | 1020 → 235 | 134 640 → 31 020 | 38 483 → 29 315 |
| 2025-12-21 | 1043 → 260 | 137 676 → 34 320 | 42 525 → 32 572 |
| 2026-01-04 | 1026 → 237 | 135 432 → 31 284 | 39 425 → 29 568 |
| 2026-01-18 | 1034 → 249 | 136 488 → 32 868 | 41 195 → 31 356 |
| 2026-02-15 | 1008 → 229 | 133 056 → 30 228 | 38 598 → 28 585 |
| 2026-03-15 | 1066 → 271 | 140 712 → 35 772 | 44 463 → 34 447 |
| 2026-04-07 | 1047 → 247 | 116 217 → 27 417 | 34 268 → 25 738 |
| 2026-04-12 | 1097 → 291 | 120 670 → 32 010 | 39 059 → 30 425 |
| 2026-04-22 | 1100 → 305 | 122 100 → 33 855 | 41 521 → 32 328 |
| 2026-05-17 | 1093 → 290 | 120 230 → 31 900 | 39 009 → 30 411 |
| **Total** | | **1 823 902 → 436 593 (−76 %)** | **546 503 → 413 316 (−24 %)** |

Pinned cells (`Gen_Commit` 0 / 1, e.g. solar at night) are integer
columns with fixed bounds that presolve removes.  The free-cell column
therefore measures the real branching freedom.

### Fast-start units (`fast_start`, for `--pricing-mode fast_start`)

PLEXOS has no fast-start flag, so the converter writes
`Commitment.fast_start` only when the data settle it
(`gtopt_writer.plexos_fast_start`), and otherwise leaves it unset so
gtopt's auto rule decides (min up and min down ≤
`fast_start_max_hours`, default 1 h):

| PLEXOS data | Emitted |
|---|---|
| Min Up Time or Min Down Time > 1 h | nothing (auto rule: not fast-start) |
| `Run Up Rate` × 60 ≥ peak `Min Stable Level` | `fast_start: true` (reaches its floor within the start-up hour) |
| `Run Up Rate` × 60 < peak `Min Stable Level` | `fast_start: false` (slow run-up the min up / down rule would miss) |
| no `Run Up Rate` or no `Min Stable Level` | nothing (auto rule) |

The flag only affects pricing under `--pricing-mode fast_start`; the
dispatch is unchanged.

---

## 7. UserConstraint family taxonomy and PAMPL emission

Every PLEXOS `Constraint` object lands on the GTOpt **AMPL-style
`UserConstraint`** primitive, which `gtopt`'s
`include/gtopt/constraint_parser.hpp` parses into a single LP row of
the form `Σ_i c_i · element(name).accessor [op] rhs` per
(stage, block). The accessor catalogue
(`source/{generator,line,battery,reserve_provision,waterway,reservoir,decision_variable,commitment}_lp.cpp`)
exposes:

| Element | Accessors registered by `add_ampl_variable` |
|---|---|
| `generator(N)` | `.generation` |
| `line(N)` | `.flow` (= `flowp − flown`) |
| `battery(N)` | `.charge`, `.discharge`, `.energy` |
| `reserve_provision(N)` | `.up`, `.dn` |
| `waterway(N)` | `.flow` |
| `reservoir(N)` | `.efin` |
| `decision_variable(N)` | `.value` |
| `commitment(N)` | `.status`, `.startup`, `.shutdown` |

`parsers._DIRECT_COEFFS` is the routing table from a PLEXOS property
kind (`Generation Coefficient`, `Flow Coefficient`, …) to the
(`element_class`, `accessor`, `name_template`) tuple GTOpt resolves
at LP-build time. `Fuel.Offtake Coefficient` and
`Reserve.Provision Coefficient` use *expansion* rules — the LHS
expands to a sum over every Generator the Fuel feeds (or the Reserve
covers).

### Family routing (single source of truth: `uc_families.py`)

Every constraint name is routed to exactly one family (first match
wins, default = `operational`):

| Family | Match rule | Typical content |
|---|---|---|
| `config_exclusivity` | name ends in `_Uniq` or contains `ConfTG` | Combined-cycle turbine mutexes (at most one config committed; HARD) |
| `gas_offtake` | starts with `Gas_MaxOp` | Daily gas/GNL fuel-operation caps (soft, day-scoped) |
| `commitment` | ends in `_starting` or `== NorthSecurity` | Unit-commitment scheduling rows |
| `reserve` | matches `CTF`/`CSF`/`CPF`/`RegRange`/`Provision`/`MinUnits`/`MAXCSF`/`SSCC` or starts with `Special` | Ancillary-services / reserve products |
| `security` | starts with `SD_`, contains `Security`, or matches `^\d` | N-1 / contingency security rows (mostly inactive) |
| `comparison` | contains `Comparison` | PLEXOS↔GTOpt validation rows (diagnostic, inactive) |
| `terminal_value` | starts with `FCF` or `alpha` | End-of-horizon future-cost (FCF) rows |
| `operational` | default | Genuine generation/flow floors, ramps, caps |

`gtopt_writer.write_user_constraint_pampl` emits one
`<output_dir>/uc_<family>.pampl` per non-empty family and wires them
into the planning JSON via `system.user_constraint_files = [...]`.
Constraints carrying a per-block `rhs` profile (e.g. PLEXOS
`RHS Day` overrides) stay inline in `system.user_constraint_array`
because GTOpt only consumes per-block RHS via the JSON schedule
field.

### Family counts on CEN PCP weekly 2026-04-22 (after MIP-mode CLI defaults)

The post-conversion comparison classifies the converted UCs by
family (see §10), and is shown here for the canonical PCP weekly:

| Family | GTOpt JSON | PLEXOS source | Δ |
|---|---:|---:|---:|
| `config_exclusivity` | 129 | 129 | 0 |
| `gas_offtake` | 520 | 520 | 0 |
| `commitment` | 17 | 17 | 0 |
| `reserve` | 420 | 420 | 0 |
| `security` | 1 372 | 1 372 | 0 (mostly inactive) |
| `comparison` | 104 | 104 | 0 (inactive) |
| `terminal_value` | 9 | n/a (converter-synthesised) | — |
| `operational` | 1 025 | 1 025 | 0 |

The `terminal_value` family routes constraints named `FCF*` /
`alpha*`; the converter's own future-cost cut is written to
`boundary_cuts.csv`, not as a UserConstraint (see §8). The `--pampl-uc-mode` CLI
flag toggles between
`hard` (PLEXOS-stamped penalty per row, default), `soft` (force a
slack penalty on EVERY row at `--default-uc-penalty`), and `off`
(drop UCs entirely — diagnostic). `--pampl-uc-only` /
`--pampl-uc-off` provide per-family leave-one-out / keep-one-only
controls for isolating which family over-constrains the MIP.

### Soft-penalty policy

Two centrally-computed penalties drive every soft surface:

* **`soft_penalty_cost`** (used by `Generator.pmin_fcost` forced
  floors, EL=1 line soft caps, and the soft line-overload band):

  ```
  soft_penalty = min(max(gcost) + 1, min(demand.fcost) - 1)
  ```

  `max(gcost) + 1` makes running a unit STRICTLY cheaper than
  leaving its forced floor unserved, the `min(VoLL) - 1` cap keeps
  the penalty below the cheapest unserved-demand cost so load-
  serving always outranks it. CLI override: `--soft-penalty-cost`.

* **`--water-fail-cost`** (default 10 $/(m³/s·h)): uniform shortfall
  penalty on every soft hydro obligation — soft `FlowRight`s
  (Filt_Laja, Caudal_Eco_*, Riego_*, Ext_*), soft `discharge_*min`
  UC floors, and natural-inflow slack columns. Matches plp2gtopt's
  default so PLEXOS- and PLP-derived JSONs use identical water-
  obligation pricing.

### Native reserve requirements (`--reserve-model native`)

CEN's PCP sizes the primary-frequency reserve (CPF) with linear
regressions written as `Constraint` rows over `Decision Variable`s
(minuta GM-DPRO Nº05-2026, Anexo B).  The default
`--reserve-model native` turns them into gtopt's plane-based
`ReserveZone` requirement (`R ≥ max_k plane_k`, see
[§5.9 of the formulation](../formulation/mathematical-formulation.md#59-reserve-constraints))
instead of emulating them with user constraints:

| PLEXOS (CEN PCP) | gtopt (`--reserve-model native`) |
|---|---|
| `CPF_Up10Calculation_I..XII`, `CPF_Up5Calculation`, `CPF_DownCalculation`: `R + a·G + b·I − c·C ≥ RHS` | one `ReserveZone` per requirement DV (`CPF10s`, `CPF5mUp`, `CPF5mDown`), one plane per row in `urreq_planes` / `drreq_planes`: `demand_coef = −a/1000`, `inertia_coef = −b/1000`, `contingency_coef = c`, `alpha = RHS` + folded constants |
| `Generation_Calculation` (`Σ gen − charge − 1000·Generation_SEN ≥ 0`) | the load term `D`: exogenous (default) or `load_term = endogenous_generation`; the row becomes an inactive stub when nothing else uses `Generation_SEN` |
| `Inertia_Calculation_e1` (`Σ H·S·u − 1000·Inertia_SEN ≥ −854.8`) | the inertia term `I` of the planes; the constant (units without a gtopt commitment) folds into `alpha`.  Commitment (default) and endogenous: `InertiaZone` `Inertia_SEN` + one `InertiaProvision` per unit (`pf·provision_max = H·S`, `provision_max = P_ref` = the commitment's minimum stable level, else `Pmin`, else 1 MW), `inertia_term: "commitment"` / `"endogenous"`.  Exogenous: `inertia_term: "exogenous"`, `system_inertia` = Σ `H·S` of the units PLEXOS forces on (`Gen_Commit = 1`) per hour, no zone.  The row stays (`InertiaGlobalMin_New` still uses the DV) |
| `InertiaNRMin_D` / `InertiaNRMin_N` (`Σ H·S·u [+ c·y] ≥ RHS`, coefficients of `Inertia_Calculation_e2`) | one `InertiaZone` per row: `requirement` = the (timesliced) RHS per hour, `cost` = the row penalty; units share the `InertiaProvision` of the same `H·S` (`inertia_zones` lists every zone) |
| `InertiaCommitGuacolda2` (`Σ u_g − k·y ≥ 0`, `y ∈ [0, U]`) + `c·y` in `InertiaNRMin_D` | `InertiaZone.requirement_credits` `{name: y, generators, units: k·U/c_u, credit: c·U}`, continuous selector (as PLEXOS; `integer` unset); the commit row becomes an inactive stub |
| `*_Comparison` (`k·C − Σ p_g − limited_generation_group ≥ offset`), DV lower bound 300 / 200 MW | one `urcontingencies` / `drcontingencies` candidate per row: plant modes summed, BESS discharge as `<bat>_gen`, charge as `<bat>_dem`, line flows, the constant as `offset`; the shared `limited_generation_group` DV is expanded through `limited_generation_calculation` (Σ PMGD gen − BESS charge); the bound becomes a second plane with `C` fixed at it; the rows themselves stay active (they also cap `C ≤ 400`) |
| `CPF_*MinProvision` (`Σ f_g·r_g + VoRS − R − Slack = 0`, PLEXOS Sense 0 / unset) | `ur_zone_factors` / `dr_zone_factors` = `f_g` on each unit's `ReserveProvision` of that family; an ACTIVE `VoRS` DV's cost → `urcost` / `drcost`, an excluded one (CEN: always) → a HARD zone (see *Reserves are hard* below); the requirement is `>=` with a free excess (clean default); only `--legacy-profile cen` keeps Sense 0 as `requirement_sense: "eq"`, and even then the priced `Slack` is dropped with a warning: the requirement `R` is a column above its planes and absorbs any excess provision, so gtopt builds no surplus column for a plane zone |
| `CPF10s_Requirement` cost (10 $/MW) | kept through the link row `<zone>_RequirementCost`: `R_dv − reserve_zone(Z).up = 0` |
| `CSF_*MinProvision`, `CTF_*MinProvision` (`Σ c_g·r_g [+ VoRS] ≥ RHS`) | fixed-requirement zones (`urreq` / `drreq` = RHS profile, cost = penalty / active VoRS cost, else HARD; `>=` by default, Sense 0 → `requirement_sense: "eq"` + the `Slack` cost as `ursurplus_cost` / `drsurplus_cost` only under `--legacy-profile cen`).  A Reserve-level `Provision Coefficient` means the reserve's whole provision, generators **and** every battery provider (`<bat>_gen__<reserve>`).  The extractor's `Reserve` zones the row sums (`CSF_RS`, `CSF_RS_BESS`, …) are retired: one zone per requirement, so the dual is not split between two zones (a retired zone a provision still needs stays with no requirement, floor or cost) |
| Timesliced RHS (`M4-9,H10-17;M1-3,H9-19`, `;` = OR) | per-hour values (the tagged row overrides the base in the hours and date window it covers).  One grammar for the whole converter (`plexos_model.timeslice_matcher`): `M` month, `H` hour, `W` weekday (1 = Sunday), `D` day of month, a bare number continues the previous kind, `!` negates; a calendar tag without a horizon date falls back to the scalar RHS with a warning, never to "every hour" |
| Dated coefficient sets (`Date From` / `Date To`) | per-hour plane schedules (latest `Date From` in force on the day wins, an open-ended `Date From` runs until superseded — the extractor uses the same rule; undated otherwise).  A `±1e30` value (RHS, coefficient or DV bound) is "no value / no limit", never a number |
| `CSF_*_Def` (`Σ r − CSF_*_Requirement`, a definition read by `MAXCSF_*`) | stays a user constraint with the PLEXOS RHS 0 and Sense (unset = `=`): the extractor's requirement-profile RHS and `≥` are replaced |
| `SpecialCPF/CSF/CTFNorth(LW)` | only with `--reserve-north-special`: a zone whose single plane is `alpha` = the dated RHS minus the requirement DVs at their dated `*_Definition` value (the `CPF_North_Tx` flow term dropped); never `flow_coef` |
| Every other row with `Include in ST Schedule ≤ 0` — `*_Definition`, `SpecialCPFTxFlow`, `CPF_North_Tx`, `CPFN_*`, `TxJadresic_Comparison`, `CTFOFF_*` | never converted: they stay inactive provenance stubs (see below) |

**Load term.**  With `--reserve-load-term exogenous` (default) `D` is a
parameter: `--reserve-load-factor` (default 1.05) × the hourly
demand, or `--reserve-system-load CSV` (exactly one finite value > 0
per horizon hour; never padded or tiled; ignored, as is the factor,
with a warning under the endogenous load term).
`--reserve-load-factor auto` estimates the factor from the case
(PLEXOS `Generation_SEN` / demand when the RES bundle is present:
1.0443 on 2026-11-07 day 1; else the monthly table, Σ generator
generation / (node load − BESS charge) per month of the 2024-01 →
2026-11 PCP history).  The median
`(Gen − BESS charge)/demand` over CEN PCP 2024–2026 is 1.025–1.032,
but PLEXOS' `Generation_SEN`/demand reaches 1.044 (2026-11-07) and
1.052 (2026-10-24), so the default errs on the larger side ("más vale
que sobre"): every regression coefficient on `D` is negative, so a
larger `D` gives a slightly *smaller* requirement, and the gap stays a
few MW.  It moves to the row RHS, so
the requirement no longer credits generation and the bus LMPs stay
energy-only.  `--reserve-load-term endogenous` reproduces PLEXOS'
endogenous `Generation_SEN`: every MWh of demand then earns
`Σ_k a_k·μ_k / 1000` and LMPs go negative in hours where the reserve
binds (or, with the 10 $/MW requirement cost, every hour).

**Inertia term.**  With `--reserve-inertia-term commitment` (default)
`I = Σ I_c·u_c`: each unit's `H·S` times its commitment status, through
the `Inertia_SEN` provisions (gtopt `inertia_term: "commitment"`).  No
dispatch column enters `I`.  On the CEN PCP 2026-11-07 day 1 (relaxed
commitments, exogenous load term) it leaves 515 negative bus-hours, none
below −0.1 $/MWh.  `--reserve-inertia-term exogenous` makes `I` data:
`system_inertia` per hour = Σ `H·S` of the units PLEXOS forces on
(`Gen_Commit = 1`), the minimum committed inertia.  Every plane carries
`inertia_coef ≤ 0`, so this is the conservative bound: on the CEN PCP
few units are forced on and CPF10s rises by about 310 MW.
`--reserve-inertia-term endogenous` is the PLEXOS-compatible
reconciliation mode: `I = Σ pf·r` over the `Inertia_SEN` provisions,
`r ≤ p`.  The negative LMPs of PLEXOS come from the endogenous *load*
term: its credit `Σ_k demand_coef_k·μ_k` reaches every bus, while the
endogenous inertia term only lowers the requirement (and so `μ`); the
load term therefore stays exogenous by default.

**Plane support.**  The conversion fails when a plane term lacks the
support gtopt requires in strict mode: an `inertia_coef` needs
`system_inertia` or an `inertia_zone`, a `contingency_coef` needs a
candidate in its direction and must be ≥ 0 (a negative one, a data
error, is dropped with a warning before the check), and a `flow_coef`
needs `flow_lines` (never emitted).

**Objective.**  gtopt cancels the valuation of met soft requirements
in the objective constant, so `obj_value` is directly the operating
cost.  The per-zone credit of the coupled terms is written as
`ReserveZone/reserve_credit_sol` (`Σ_k demand_coef_k·lf·μ_k`, zero
with the exogenous load term), `flow_credit_sol` and
`credit_transfer_sol` (credit × energy).

**`Include in ST Schedule = 0` is always respected.**  The extractor
already emits such rows with `active = False`, and the native transform
only reads active rows, so none of them becomes a zone, a plane or a
contingency candidate.  This matters for the northern-corridor family
(`SpecialCPF/CSF/CTFNorth(LW)`, `*_Definition`, `SpecialCPFTxFlow`,
`CPF_North_Tx`): it is disabled in the CEN PCP (no dual in 2024–2026,
dated RHS only for 2023-12-08/09), and its requirement equals the
endogenous southward corridor flow — emitting it would couple dispatch
back into the requirement (north–south spread, possible negative
northern LMPs).  The only exception is the opt-in
`--reserve-north-special` (off by default): it emits the scheduled
requirement — the dated RHS with the corridor flow dropped — as a plain
`alpha` plane; the flow-coupled form (`flow_lines` + `flow_coef`) is
never emitted.

**PLEXOS Sense 0.**  Sense 0, or no Sense, is an equality in PLEXOS
(`*MinProvision` balances, `*_Def` / `*Calculation_e*` definitions,
`_ConfTGA*` plant links).  The native model writes those rows `=` (its
balances become `"eq"` zones: a fixed zone has both a shortage and a
surplus slack, a plane zone's requirement column absorbs the surplus);
`--reserve-model uc` keeps the
legacy `>=`, because there the balances are hard user constraints and
the equalities were infeasible against the per-unit reserve caps.  In
both modes a `*_Def` row keeps its PLEXOS RHS (0), not the
reserve-requirement profile.

**Reserve requirements.**  A PLEXOS `Reserve` with `Include in ST
Schedule = 0` (CEN: `CTF_RS`, `CTF_LW`) drops its static `Min
Provision` and gets no requirement of its own; its `Res_Requirement`
profile is the hourly RHS of the `CTF_*MinProvision` rows.  The
per-unit `Min Regulation / Spinning / Replacement Provision` is
semi-continuous in PLEXOS (a unit provides 0 or at least the minimum)
and is not emitted: gtopt's `provision >= urmin·u` would force every
committed unit into every reserve.

**Reserves are hard; VoRS and Slack are formulation devices.**  CEN
publishes no reserve-shortage cost, and its PLEXOS model treats the
reserve balances as hard.  The slack Decision Variables of the
`*MinProvision` rows are formulation devices, not part of the reserve
model, in two classes:

* **Feasibility / diagnostic switches** — `CPF_RS5m_VoRS`,
  `CPF_RS10s_VoRS`, `CPF_LW_VoRS`, `CPFN_RS_VoRS`, `CPFN_LW_VoRS`
  (`+ VoRS`, 10000 $/MW).  Their `Include in ST Schedule` is `0` by
  default and `-1` only under the scenario `CPF_VoRS_ON`, which the
  solved model `PRGdia_Full_Definitivo` does not include, so they do
  not exist in the ST run (the RES solution reports no value for them).
  The zone is then HARD: no `urcost` / `drcost`, and no global
  `model_options.reserve_shortage_cost` either (gtopt would otherwise
  price every zone without a cost of its own; a zone cost of 0 would
  even make the shortage free).  When a scenario does activate a switch,
  its cost is emitted as an explicitly labelled soft penalty, never as an
  economic reserve-shortage cost.
  `--soft-unpenalised-constraints P` softens every hard zone at `P`
  (zone costs and the global cost), as the opt-in feasibility switch.
* **Surplus slacks** — `CPF5mUp_Slack`, `CPF10s_Slack` (`− Slack`,
  1 $/MW, always active): the priced excess that turns PLEXOS's Sense-0
  equality into `>=`.  An artefact of the PLEXOS formulation: the clean
  default writes the requirement as `>=` with a free excess and drops the
  slack.  `--legacy-profile cen` keeps the equality and the surplus cost
  (`ursurplus_cost` / `drsurplus_cost`) on a fixed zone; on a plane zone
  gtopt builds no surplus column: the requirement column `R` (priced
  10 $/MW through `<zone>_RequirementCost`) absorbs the excess, so an
  excess MW costs 10 instead of PLEXOS's 1.

Every Decision Variable's `Include in ST Schedule` is resolved like a
Constraint's: a row tagged with an active scenario wins over the untagged
default, rows tagged only with inactive scenarios are ignored, and a row
whose `Date From` / `Date To` window does not hold the horizon start does
not apply (`GEN_T4_MACHICURA`'s untagged `0` is dated 2022-04-09 only).
An excluded DV is written fixed at 0 with no cost.  The conversion log
lists every slack DV with its class and status (`feasibility switch …
NOT in the ST run: zone HARD`, `surplus slack … active`), and the case
keeps them for provenance.

**One provision per reserve family.**  PLEXOS has one provision variable
per (Reserve, Generator) pair, all under one headroom
`gen + Σ_up r ≤ available capacity` (RES 2025-09-16: no providing unit
exceeds it; ANGOSTURA_U1 provides CSF 45 + CTF 16 MW, RALCO_U1 CPF 73 +
CSF 54 MW in the same hour).  gtopt's `ReserveProvision` has one up
column shared by all its zones, so a unit in several families gets one
provision per family (`provision_<gen>__CPF`, `__CSF`, `__CTF`, family
= the `CPF` / `CSF` / `CTF` token of the zone name), each with
`shared_headroom: true` (one headroom row per generator) and its own
CFdata `MRU` / `MRD` cap.  Without the split the `CTF_UpMinProvision`
equality pins the shared column and starves `CPF5mUp` (2025-09-16 h21:
167 MW short, and through the `Generation_SEN` term the over-generation
earns `0.0134 × shortage cost`, so the evening bus prices sit at −1).
User-constraint references follow: a row whose name names one family
(`COLBUN_U1_CPF_Simmetry`, `BAT_*_ActCSFLW`) takes that family's
column, any other row (`Reserve Units` gates, `CFRS_*`, `Max_Tx_*`) the
sum of the families, i.e. the old column.  BESS provisions are already
one per zone (`provision_<bat>_gen__<ZONE>`) and keep their native
reserve path.

**Contingency units.**  A `*_Comparison` DV in GW (`1000·C ≥ p`) is
scaled once: candidates are in MW (`k_C·C`) and the plane multiplies by
`c/k_C`.  A `±1e30` DV bound is no bound: no floor plane, no
inertia-credit selector.

Every replaced row ships in the PAMPL as an inactive stub whose
description names the replacing zone.  `--reserve-model uc` keeps the
legacy UserConstraint + DecisionVariable emulation.

---

## 8. Hydro topology, terminal value, and the FCF cut

`extract_reservoirs` + `extract_waterways` + `extract_junctions` +
`extract_turbines` together reconstruct PLEXOS's `Storage` /
`Waterway` cascade into GTOpt's three-class topology
(`Junction` + `Reservoir` + `Waterway`/`Turbine`). The key
transformations:

* **Pondage demotion.** PLEXOS encodes every cascade balance point
  as a `Storage` object (B_C_Isla, B_Maule, Post_Antuco, …) even
  when it has no real storage. Reservoirs with every volume / cost /
  penalty field zero and no turbine `main_reservoir` reference are
  demoted to **Junction-only**: 52 → 12 on the CEN PCP weekly bundle.
* **Vert spillway collapse.** Every `Vert_<src>` waterway feeding an
  `<src>_ocean` sink is collapsed onto `Junction.drain` (with the
  source storage's `Max Flow` + `Max Flow Penalty` → `drain_capacity`
  + `drain_cost`). This drops 35+ synthetic waterways and 30+ ocean
  sink junctions on CEN PCP weekly. `--vert-routing cascade` keeps
  the legacy "Vert feeds next cascade junction" routing; default
  `ocean` drains.
* **`Max Flow Penalty` is a violation price.**  PLEXOS charges it on
  flow above `Max Flow`.  With `Max Flow = 0` (every CEN `Vert_*`
  spillway) it is a per-unit spill cost and becomes `fcost` /
  `drain_cost` / `spillway_cost`.  With an unbounded `Max Flow`
  (`Post_Isla`) the penalty never applies in PLEXOS, so no cost is
  emitted: an invented cost on an outflow path can make the upstream
  water value negative.
* **Built-in turbine waterway.** Each `Turbine` carries its own
  flow arc (`junction_a` = reservoir, `junction_b` = tail storage)
  + power conversion (`production_factor = fp_med`), eliminating
  synthetic penstock waterways (+35) and terminal-ocean junctions
  (+4).
* **RoR generators** (hydro `Generator` with NO `Storage` membership,
  ~99 on PCP) are **left as plain `Generator` with per-block `pmax`
  profile** rather than re-built into a water topology — the
  per-block pmax IS the water-driven dispatch envelope.

### End-of-horizon future-cost cut

`Hydro_StoWaterValues.csv` is parsed into a `BoundaryCutSpec`:

* `FCF` row → the cut intercept ($);
* every other row → a reservoir water value ($/GWh).

The cut is written once, as `boundary_cuts.csv` — GTOpt's native cut
file (`scene,rhs,<reservoirs>` rows, coefficients = `-water_value`),
wired via the FutureCost element
(`system.future_cost_array[].cuts_file`), which every method reads.
The monolithic method registers the future-cost column α itself and
binds the cut as `α + Σ_r water_value_r · efin_r >= FCF`, α-rebased at
the reservoirs' terminal targets so the cost-to-go centres on ~0 at the
optimum.

Earlier converter versions also wrote the same hyperplane as an
`alpha_fcf` DecisionVariable plus an `FCF_future_cost` UserConstraint
(`terminal_value` family).  That second encoding double-counted the
cost-to-go and is no longer emitted; any `alpha_fcf` decision variable
carried by a stale case object is filtered out by the writer.

`--water-value-factor RES:F[,RES:F...]` multiplies the cut coefficient
of the named reservoirs (unlisted ones keep 1.0): an economic change of
the terminal water value, not a numerical scale.

---

## 9. Loss model — adaptive piecewise-linear

`Line.loss_pwl_layout` and `loss_envelope` are emitted per-line by
`gtopt_writer._apply_dynamic_loss_layout`, driven by a single
budget knob `--loss-error-pct` (default `0.01` = 1%):

* The **cube-root rule** `K_i ∝ L_max,i^(1/3)` (with
  `L_max,i = R · fmax²`) allocates per-line segment count so the
  worst-case system-wide signed mean error stays within the budget.
* Per-line `K_i ∈ [2, --nseg-losses]` (ceiling defaults to 6 in
  adaptive mode, 4 in uniform mode).
* `--loss-pwl-layout dynamic` (default) gives heavy lines `midpoint`
  (negative bias secant chords, accuracy-first) and the rest
  `uniform` (positive-bias secant chords, presolve-eliminable, so
  cheaper to solve). The two bias directions cancel system-wide.
* `--loss-extend-overload` (default OFF) extends each soft-cap
  line's PWL envelope from `[0, tmax_normal]` to
  `[0, headroom × tmax_normal]`. The trade-off (4× worse PWL
  fidelity in the normal band vs. accuracy on a band the LP rarely
  reaches) is documented inline in `main.py` and remains OFF for
  routine runs.

Compared with an all-midpoint layout, the dynamic layout reduces the
number of loss segments, improves LP conditioning and tightens the
gap between the LP-internal and the analytic line losses.

A complementary line-cap policy ships out of the box:

* **EL=2 ("Always enforce")** → plain hard cap at the rating.
* **EL=1 ("Voltage enforce")** → automatic soft-cap pair
  `tmax_normal_* ← rated`, `tmax_* ← 3× rated`,
  `overload_penalty = soft_penalty_cost` ($/MWh). This is the
  `augment_el1_with_soft_caps` step at the end of `build_planning`;
  it's the LP's priced escape valve when PLEXOS itself dispatches a
  line above its stated rating.
* **EL=0 ("Never enforce")** → `--el0-lines enforced` (alias
  `strict`, the clean default) treats EL=0 like EL=2, a hard cap at the
  rating, with lines in `--lift-line-caps` given the soft band:
  congestion then shows as unserved energy at a known bus instead of a
  flow no limit restrains.  `--el0-lines unenforced` (part of
  `--legacy-profile cen`) mirrors PLEXOS, which never enforces an EL=0
  rating. The CEN
  bundles carry no line penalty or overload data (their Line
  properties are only Enforce Limits, Max/Min Flow, Max/Min Rating,
  Reactance, Resistance, Units and Units Out), so there is no soft
  limit to mirror. The cap is 10× the rating, there is no penalty,
  and the loss envelope stays at the rating. gtopt always bounds a
  flow column, so a flow at that cap is a DC-OPF artefact to
  inspect. Only `--no-lift-lines` still pins a line in this mode.
  The mode also covers the EL=1 lines that PLEXOS skips: EL=1 is
  enforced only when the line voltage (the larger node `Voltage` of
  its ends) reaches the Transmission `Constraint Voltage Threshold`
  (160 kV in the CEN PCP). On CEN PCP 2025-06-26 these are
  Capricornio110->LaNegra110, Tocopilla110->S-AA100 and
  Bocamina154->Coronel154. This is why PLEXOS carries 230–259 MW on
  Capricornio's 76 MW rating: the 110 kV line is EL=1 (EL=0 in the
  2021 bundles) below the threshold. Its rating has no scenario
  override; the `Lin_MaxRating.csv` Data File gives 76 MW over the
  static 137 MW Max Rating.
  The other opt-in: `--el0-lines extended`
  frees flow up to the rating, penalises it between the rating and a
  headroom multiple, and hard-caps it at that multiple. The mode
  used is recorded as `el0_lines` in the provenance sidecar.
* **`--lift-line-caps <names>`** (default
  `Capricornio110->LaNegra110`): demote a curated set of radial
  step-down corridors to EL=0 with a wider free band (4× rating)
  and 10× hard cap.
* **`--auto-lift-lines`** runs a DC OPF (pandapower or
  `gtopt --no-mip`) on the first (scenario, block) and patches
  `enforce_level = 0` onto every line whose unlifted-OPF flow
  exceeds rating by ≥ THRESHOLD. Useful when the lift list isn't
  curated for a new bundle.

  After a solve, `plexos2gtopt-line-lifts <case.json> <gtopt output>
  [--lifted FILE|NAMES [--lifted-key KEY]] --suggest` ranks the lines by
  the |reduced cost| of their flow column, reports utilisation, blocks at
  cap and soft-band overload energy, and prints a `--lift-line-caps` value
  (current list + binding candidates above `--min-dual`).

---

## 10. Validation — the post-conversion comparison

`_comparison.run_post_check(planning, case)` runs on every default
conversion (opt out with `--no-check`) and is the converter's
primary correctness gate. It prints four side-by-side tables and
returns the CRITICAL finding count as the exit code so CI can gate
on it.

### Table 1 — Conversion stats (per-class base stats)

Plp2gtopt-style baseline counters: buses, lines, generators (hydro
/ thermal / renewable / total), demands, batteries, fuels,
reservoirs, junctions, waterways, turbines, flows, reserve
zones/provisions, commitments, decision variables, plants, plus
the per-family UC counts.

### Table 2 — Element Comparison (PLEXOS vs GTOpt vs Δ)

For every emitted entity class, the parsed PLEXOS count, the
gtopt-JSON count (read back by
`gtopt_check_json._info.compute_counts` — the single authority — so
gtopt-side numbers stay honest), and the difference. The contract
is **Δ = 0** for every class except where the converter
intentionally drops or synthesises:

* Boundary state variables: PLEXOS water-value rows for non-bundle
  reservoirs (e.g. PILMAIQUEN) are excluded with a "discarded" note;
* Turbines: tied to per-`Storage` hydro generators that pass the
  pmax-positive filter;
* Junctions: pondage-demoted reservoirs + sink/ocean nodes.

### Table 3 — User Constraint families

Per-family table (PLEXOS-side count vs gtopt-emitted vs Δ, plus
inactive count) using the `uc_families.py` taxonomy. Converter-
synthesised families (`terminal_value`) are excluded from the Δ
total because they have no PLEXOS source. The conversion funnel
(`UserConstraintStats`) is also reported:

```
raw → empty_lhs_dropped → base_emitted → hydro_synthesized → plant_cap_synthesized
```

### Table 4 — Global Indicators

PLEXOS vs GTOpt aggregates: installed capacity (hydro / thermal /
renewable), peak/average/total demand, total energy, total water
(hm³), reservoir storage (Σ), initial volume, FCF intercept,
reserve requirement (up/dn), gen-cost statistics (avg/min/max).

### Table 5 — Drop funnel + Input-file ledger

For every raw PLEXOS class the converter drops or demotes
(`Storage` → Reservoir vs Junction, `Generator` with pmax=0,
`Line` with EL=0/1/2 split, …), the funnel shows how many
instances landed in each bucket so the comparison can attribute
every gap to a reason. The input-file ledger lists every CSV the
converter is expected to read with the indicator it drives — a
zero in the linked indicator flags an unread file.

### Table 6 — JSON structural validation

`gtopt_check_json._checks.run_all_checks` runs the GTOpt JSON
validator over the produced planning: schema sanity, dangling-
reference detection, every emitted UC term resolvable through
`add_ampl_variable`, every Junction/Waterway endpoint present,
etc. Findings are split into CRITICAL / WARNING / NOTE; the
process exit code is the CRITICAL count.

### Strict UserConstraint resolution

By default any UC term that references an element name not in the
emitted-name set raises `UnresolvedConstraintReferenceError` with
the **full list** of offending references + closest-match hints —
the converter refuses to ship a JSON with dangling refs (matches
GTOpt's strict JSON parser). `--lax-uc-refs` downgrades this to a
warning + silent per-term drop for diagnostic / iterative parser
work.

A few term families are instead dropped because they are **provably
zero** in PLEXOS (shadow lines with `Lin_Units = 0`, fully-offline
generators, zone-less generator provisions, …).  One of them concerns
battery reserve terms: PLEXOS creates a battery reserve-provision
variable only for each `Reserve → Battery` membership, so a battery
`Raise` / `Lower` / `Regulation Raise` / `Regulation Lower` /
`Reserve Provision Coefficient` term on a battery that is **not a
member of any reserve the term can target** multiplies an
identically-zero quantity.  Such terms are dropped and listed in one
`WARNING` (`N Battery reserve term(s) dropped as identically zero …`,
also counted in the `battery_zero_provision_terms` UC statistic).  A
battery `Reserve Units Coefficient` in the same constraint is dropped
with it (the battery has no units providing that reserve).  The
targeted reserves are:

| Battery coefficient kind | Targeted reserves |
|---|---|
| `Regulation Raise/Lower`, generic `Reserve Provision` | `<CPF\|CSF>_<RS\|LW>_BESS` (type from the constraint name, direction from the kind or name) |
| `Raise` / `Lower` | member reserves of that direction whose PLEXOS `Type` is not Regulation (3/4) |

If the battery **is** a member of a targeted reserve but its
zone-suffixed provision was not emitted, the strict hard-fail above
still applies.  Recent CEN PCP bundles (2026-07 onward) hit this on
`Gx_SSCC_Jadresic`, `SD_*_Kallpa_Taltal`, `*_Gx_Cochrane1_Arenales`,
`SD_*_Gx_BESS_Sol_De_Los_Andes` and the `CSF_*_MIN_BAT_ANDES_4_FV`
rows.  Separately, any `<Class> → Constraint` coefficient kind
present in the bundle that the converter does not map is reported in
a `WARNING` rather than ignored without trace.

---

## 11. End-to-end example: CEN PCP 2026-04-22 weekly

This is the canonical reference bundle. The complete pipeline:

```bash
# 1. Convert (default == PLEXOS-native 111-block layout, MIP commitments,
#              auto soft EL=1 caps, default --loss-pwl-layout dynamic):
plexos2gtopt \
  -i $GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/PLEXOS20260422.zip \
  -o gtopt_PLEXOS20260422

# 2. Solve the MIP, write full Parquet output stream:
gtopt gtopt_PLEXOS20260422/PLEXOS20260422.json \
  --solver highs --output-dir gtopt_PLEXOS20260422/output

# 3. Compare against the PLEXOS reference solution
#    (auto-discovers RES{date}.zip[.xz] sibling):
plexos2gtopt --compare gtopt_PLEXOS20260422/PLEXOS20260422.json \
  $GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/PLEXOS20260422.zip
```

### What the converter produces (observed)

The conversion of CEN PCP weekly 2026-04-22 emits (from the post-
conversion comparison and the resulting JSON):

| GTOpt array | Count |
|---|---:|
| `bus_array` | 247 |
| `generator_array` | 1 732 |
| `line_array` | 317 |
| `demand_array` | 127 |
| `battery_array` | 36 |
| `fuel_array` | 221 (incl. virtual unit-price fuel when needed) |
| `reservoir_array` | 12 (52 → 12 after pondage demotion) |
| `junction_array` | 32 |
| `waterway_array` | 16 (51 → 16 after Vert collapse + turbine built-in) |
| `turbine_array` | 35 |
| `flow_array` | 16 |
| `reserve_zone_array` | 12 |
| `reserve_provision_array` | 393 |
| `commitment_array` | 1 578 (thermal + auto-promoted hydro min/max) |
| `decision_variable_array` | 44 (observed before `alpha_fcf` was dropped; see §8) |
| `user_constraint_array` | 6 (per-block-RHS only) |
| `user_constraint_files` | 8 (`uc_<family>.pampl`) |
| `future_cost_array` | 1 (`cuts_file = boundary_cuts.csv`: 1 cut, 8 reservoir slopes) |

### Simulation block

```
block_array : 168 blocks (under --horizon-mode hourly)
              or 111 blocks (default --horizon-mode plexos, t_phase_3 layout)
stage_array : 1 (chronological)
scenario_array : 1
```

### MIP vs LP-relax

| Mode | Variables | Rows | Status | Obj (unscaled) |
|---|---:|---:|---|---:|
| Default LP (Ruiz scaling, default loss layout) | 695 711 | 497 230 | optimal | 23.618 M (scaled obj × `scale_objective` 1000) |
| LP-relax (`--lp-relax`, equilibration=ruiz, K=8 loss, full UC set) | 790 616 | 497 230 | optimal | 22.754 M |
| MIP (default since 2026-05-23, no `--lp-relax`) | ~790 k | ~497 k | optimal | within ~1% MIPGap |

The MIP-vs-LP-relax gap is intentionally small because the same
soft slacks make the LP-relax already feasible; what MIP recovers
is the cycling / startup cost that the LP-relaxed commitment
misses. Empirically (see feedback memory): dropping the LP-relax
closed ~7 pp of the PLEXOS-vs-gtopt dispatch-cost gap on CEN PCP,
moving `NUEVA_RENCA-TG+TV_GN_A` from 19 GWh/week (LP) to 33
GWh/week (MIP, vs PLEXOS 40 GWh/week).

### Step-3 (`compare_with_plexos`) comparison surface

`scripts/plexos2gtopt/compare_with_plexos.py` is the heavier
side-by-side comparator (it reads back both the PLEXOS `.accdb`
solution and the GTOpt Parquet output). Its rendered tables
include:

* **Solution totals** — block count, hours covered, consumer
  demand [MWh], BESS charge / discharge / round-trip loss,
  line losses (analytic vs LP-internal vs bus-residual),
  generation [MWh], unserved [MWh], operational \$ broken into
  dispatch + start/shutdown cost.
* **Per-unit SRMC** vs PLEXOS pid-137 (per-unit short-run
  marginal cost weighted by dispatch).
* **Per-bus LMP** vs PLEXOS price.
* **Per-line** flow / loss / utilisation diffs.
* **Per-technology generation** (`_classify_tech` + PLEXOS category map).
* **Reservoir volumes** start / end / per-block.
* **Battery operation** (charge / discharge / round trip).
* **Commitment** comparison (units started / shutdown / on, per unit).
* **UserConstraint drilldown** — per-family penalty totals + per-
  block breakdowns for diagnosing which UCs the LP paid to violate.

A typical PCP weekly run prints all of these as Rich tables; the
single-line summary "PLEXOS objective \$X vs GTOpt \$Y; Δ \$Z (Δ%)"
is the at-a-glance gate.

The per-stream, per-element part sits on the generic run comparison of
[`gtopt_compare_runs`](gtopt_compare_runs.md): the battery and reservoir
water-value tables read the gtopt output through its reader, and
`--run-diff` imports the PLEXOS solution into gtopt's output layout
(the same importer as `plexos2gtopt --results`) and prints the full
`gtopt_compare_runs` report — every stream, top differing elements,
elements found on one side only.  `--run-diff-dir DIR` keeps the
imported run in `DIR/plexos` and writes the CSV tables and
`report.json` to `DIR`.

---

## 12. CLI reference (canonical knobs)

The complete list lives in `scripts/plexos2gtopt/main.py`; the
following groups together the operationally important knobs.

### Conversion mode

```
plexos2gtopt INPUT [-o OUT] [-l LOG_LEVEL]
plexos2gtopt --info  INPUT
plexos2gtopt --validate INPUT
plexos2gtopt --compare GTOPT_JSON INPUT
```

### Clean default vs `--legacy-profile cen`

The default conversion is the clean gtopt formulation; it produces no
artificial negative prices: an exogenous CPF load term
(`--reserve-load-term exogenous`), inertia from commitment
(`--reserve-inertia-term commitment`), the native BESS reserve
(`--bess-reserve native`), enforced line ratings (`--el0-lines
enforced`, unserved energy visible), hard reserves written as `>=`
(VoRS switches and surplus slacks dropped), free renewable curtailment,
and the `loss_cost_eps` guard.

`--legacy-profile cen` replicates CEN's PLEXOS PCP model, quirks
included.  It is a diagnostic step to explain the operator's published
prices, not a production setting.  It applies:

| Toggle | `cen` value | Effect |
|---|---|---|
| `--reserve-load-term` | `endogenous` | `D = Generation_SEN` as an LP column: every MWh earns `Σ β·μ` (PLEXOS negative LMPs) |
| `--reserve-inertia-term` | `endogenous` | inertia from the dispatch-based provisions (`Inertia_SEN`) |
| `--bess-reserve` | `uc` | BESS SSCC rows as PLEXOS user constraints |
| `--el0-lines` | `unenforced` | EL=0 / below-threshold ratings never enforced |
| `cf_comp_equality` (internal) | on | `BAT_*_CF_GEN_COMP` / `CF_LOAD_COMP` with Sense unset read as `=` |
| `reserve_surplus_equality` (internal) | on | Sense-0 reserve balances kept as equalities with the priced `− Slack` surplus |
| `--no-plp-embalses` | on | no PLP `embalses.csv` cross-reference patch |
| `--renewable-floors` | `plexos` | Min Stable Level and Commit=+1 floors kept on solar, wind and run-of-river units |

`--renewable-floors free` (the clean default) removes the PLEXOS Min
Stable Level (`Commitment.pmin`) and forced-ON (`Gen_Commit` = +1)
periods from solar, wind and run-of-river commitments (hydro with no
turbine on a reservoir with storage).  Forced-OFF periods, the status
column and every other UC datum stay.  CEN ships a Min Stable Level on
renewable units with `Commit = -1` (free) and no Fixed Load, for example
`TAMARICO_FV` at 5.17 MW, clamped to its first positive rating.  Once
pinned on, such a floor forces injection at a negative-price bus.  On
PCP 2025-06-26 the clean default frees 57 floors (36 run-of-river,
8 solar, 13 wind) and 667 forced-ON profiles.  Storage hydro and
units with a start-up, shut-down or no-load cost are untouched, and so
is concentrated solar (`*_CS`, a steam turbine on a heat store).  The
provenance sidecar lists the freed units under `renewable_floors`.

An individual flag typed on the command line wins over the profile
(`--legacy-profile cen --el0-lines enforced` keeps the ratings).  The
log says `--legacy-profile cen: applying ...`, and the provenance
sidecar records `legacy_profile: {profile, toggles}` with the toggles the
profile actually applied.  `--plexos-legacy` is a deprecated alias of
`--legacy-profile cen` (it logs a deprecation warning); plp2gtopt shares
the flag (`--plp-legacy` is its alias).

### Horizon

| Flag | Default | Effect |
|---|---|---|
| `--horizon-mode {plexos,hourly}` | `plexos` | `plexos` reproduces the PLEXOS-solved `t_phase_3` 111-block layout (needs `RES<date>.zip`); `hourly` emits `--horizon-days × 24` uniform blocks. |
| `--horizon-days N` (1..7) | inferred from `Horizon` name (`*_7d` → 7) | Number of consecutive days. |
| `--horizon-end-targets {auto,plexos,eini,free}` | `auto` | End-of-day-N storage targets on a cut horizon (`auto` = `plexos` when the RES solution exists, else `eini`).  See §5. |
| `--horizon-boundary-cut {auto,keep,drop}` | `auto` | FCF cut on a cut horizon (`auto`: drop with pinned targets). |
| `--horizon-constraint-rules FAMILY=RULE,...` | prorate / keep | Multi-day families `fuel_max_offtake`, `fuel_min_offtake`, `period_rhs`, `day_indexed`. |
| `--horizon-pin-lng-tanks` | off | Pin LNG tanks too. |
| `--plexos-solution-accdb PATH` | auto-discovered | Override of the .accdb path (block layout + optional table cache). |
| `--block-layout CSV-OR-SPEC` | unset | User-defined layout for `plexos` mode when no .accdb is available. CSV (`duration` column) or inline `{uid:dur,…}` / `d1,d2,…`. |

### General PLEXOS databases (one Model of a multi-model XML)

A general PLEXOS database (for example the public SEM PLEXOS Forecast
Model 2024-2032 of the SEM Committee) holds several Models whose data
rows depend on Scenarios, dates, timeslice patterns (`M04-09`,
`H18-20`, `!H18-19`), Variables and Data Files.  `--plexos-model`
resolves one Model for one horizon (`plexos_model.py`) into a
CEN-style bundle (static XML plus `Nod_Load`, `Gen_Rating`,
`Gen_HeatRate`, `Fuel_Price`, `Gen_StartCost`, `Lin_*Rating`,
`Lin_Units`, `BESS_IniValue` CSVs) before the normal extraction.

| Flag | Default | Effect |
|---|---|---|
| `--plexos-model NAME` | unset | Model to materialise (its Scenarios are the active ones). |
| `--plexos-start-date YYYY-MM-DD` | Horizon `Date From` | First day; the horizon length is `--horizon-days`. |
| `--plexos-sample N` | `1` | Data File column (stochastic sample) used for Variables. |
| `--plexos-data-dir DIR` | input dir | Where the Data File CSVs are searched (repeatable; recursive, case-insensitive, `.xz` accepted). |
| `--plexos-period-rhs {prorate,drop}` | `prorate` | Constraints with an `RHS Week` / `RHS Month` / `RHS Year` budget: one row summing the LHS over the horizon with the RHS pro-rated (`RHS × horizon h / period h`), or not converted. |

Conversion rules: active-scenario rows override base rows at the
same hour; among dated rows the latest `Date From` wins; fuel prices
include the emission price times the fuel's production rate;
generator ratings are `Max Capacity` (times `Rating Factor`, capped by
`Rating` / `Outage Rating`) times `Units - Units Out`; a single-node
region's `Fixed Load` is added to the node load; an energy-model
pumped-storage plant (head/tail Storage in GWh plus generators with
`Pump Load`) becomes one Battery; region `DSP Bid Quantity` / `Price`
bands become price-bid generators; banded `Load Point` / `Heat Rate
Incr` become `heat_rate_segments`; batteries are scaled by `Units`
(zero units: dropped).  User constraints without a positive `Penalty
Price` stay hard (PLEXOS semantics; the CEN soft tiers of
[§7](#soft-penalty-policy) are not applied).  Properties that vary in
the horizon but have no schedule are frozen at the start value, and
every property with active data that is not converted is listed in
the log and in `<stem>.plexos_model.json`.

Constraint right-hand sides by period (any PLEXOS database, not only
`--plexos-model`):

| PLEXOS property | gtopt UserConstraint |
|---|---|
| `RHS` / `RHS Hour` | one row per block (`RHS Hour`: hourly blocks; a longer block bounds its average) |
| `RHS Day` | `daily_sum` row per day; generation terms Δt-weighted with the GWh RHS × 1000 (MWh), start/shutdown counts unweighted, `Hours of Operation` / status terms Δt-weighted in hours |
| `RHS Week` / `RHS Month` / `RHS Year` | one `scope: "stage"` row `sum{b in stage} dur[b] * … <op> RHS × horizon h / period h` (exact when the horizon is the period; a pro-rata **approximation** otherwise, flagged in the row `description`); generation / flow / battery terms in MWh (GWh RHS × 1000), status in hours, start/shutdown counts unweighted; `--plexos-period-rhs drop` skips them |

`Hours of Operation Coefficient` maps to `commitment("uc_<gen>").status`
(the hours the unit is on), e.g. the SEM yearly running-hours limits
(`KGT1 ≤ 500 h/year` becomes `Σ_week status·Δt ≤ 500 × 168 / 8760 =
9.59 h` on a one-week case).  PLEXOS labels the period RHS "(000)", but
for hours terms the converter reads it as **plain hours** (a deliberate
deviation: 500 × 1000 running hours a year is physically impossible, so
the SEM data are hours).  Generation / flow / fuel / emission terms keep
the "(000)" scaling.  Emission→Fuel `Production Rate` is read
in kg per fuel unit and written to `Fuel.emission_factors` in t per
fuel unit.

### User constraints (PAMPL emission)

| Flag | Default | Effect |
|---|---|---|
| `--uc-emit {pampl,inline}` | `pampl` | Modular `uc_<family>.pampl` files (default) vs inline `user_constraint_array`. |
| `--pampl-uc-mode {hard,soft,off}` | `hard` | Keep PLEXOS penalties (`hard`), force a slack on every row (`soft`), drop UCs entirely (`off`). |
| `--default-uc-penalty F` | unset | Fallback penalty for PLEXOS rows without `Penalty Price`. |
| `--soft-unpenalised-constraints PENALTY` | unset (HARD) | PLEXOS constraints with no `Penalty Price` (absent or `-1`) are HARD, as in PLEXOS. Opt-in: re-soften the rows the old CEN ladder softened (operational floors, mixed line+commitment rows, reserve aggregates, `Gas_MaxOpDay`) at `PENALTY`. Rows with a `Penalty Price` keep it. Recorded in provenance. |
| `--pampl-uc-only FAM1,FAM2,…` | unset | Emit ONLY these families. |
| `--pampl-uc-off FAM1,FAM2,…` | unset | Drop these families (leave-one-out diagnostic). |
| `--lax-uc-refs` | OFF | Downgrade unresolved-ref errors to warnings + silent drop. |
| `--reserve-model {native,uc}` | `native` | CEN CPF requirement regressions (`CPF_*Calculation*` + `*MinProvision`) → `ReserveZone` requirement planes with inertia / contingency / load terms, dated coefficients as schedules; CSF/CTF provision-sum rows → fixed zones.  `uc`: legacy UserConstraint emulation.  See §7 "Native reserve requirements". |
| `--reserve-load-term {exogenous,endogenous}` | `exogenous` | Load term `D` of the native planes: parameter on the RHS (no negative LMPs from reserves) or PLEXOS' endogenous `Generation_SEN` (`load_term = endogenous_generation`). |
| `--reserve-inertia-term {commitment,exogenous,endogenous}` | `commitment` | Inertia term `I` of the native planes: `H·S` times the unit status through the `Inertia_SEN` provisions (`inertia_term = commitment`), the must-run `H·S` per hour as `system_inertia` (data, conservative), or PLEXOS' dispatch-based provisions (`inertia_term = endogenous`, reconciliation only). |
| `--reserve-load-factor F\|auto` | `1.05` | Exogenous `D = F × Σ demand lmax`.  `auto`: PLEXOS `Generation_SEN` (or Σ Generator `Generation`) / Σ demand over the converted hours when the RES bundle is present, else the monthly table of the PCP history (`_reserve_load.MONTHLY_LOAD_FACTOR`, 1.022–1.046); the factor is logged and stored in the provenance. |
| `--reserve-north-special` | off | Emit the `SpecialCPF/CSF/CTFNorth(LW)` rows (`Include in ST Schedule = 0` in CEN PCP) as zones with the scheduled, dated requirement (`alpha` only; the corridor-flow coupling is never emitted). |
| `--reserve-system-load CSV` | unset | Exogenous `D` per hour from a CSV (`system_load`/`value` [MW] or `generation_sen` [GW], optional `hour`). |
| `--bess-reserve {uc,native}` | `native` | CEN "BESS SSCC Constraints" (`UP/DOWNStorageBound_<bat>`, `<bat>_CF_{GEN,LOAD}_COMP` + `<bat>_AUX`). `uc`: legacy UserConstraint mapping. `native`: set `Battery.reserve_sustain_hours` (recovered from the storage-bound coefficients), make those four rows inactive provenance stubs, keep `CSF_*_MIN_BAT_*`, and count BESS SSCC provisions at gross value.  It also maps the `Reserve → Battery` `Max Response` / `Max Charge Response` (the row in force at the horizon start — dated sets and timesliced scalar rows resolved per hour — or the `CFdata\…\*_MRU/MRD.csv` data file read from the bundle, aligned by `YEAR,MONTH,DAY,PERIOD` and used only when it covers every horizon hour; a path with a drive, root or `..` is never read; `1e30` = no cap, per hour the physical range Pd + Pc) to `{ur,dr}max_response` / `{ur,dr}max_charge_response` (then `urmax`/`drmax` = Pd + Pc), the `Provision Coefficient` of the reserves on `<bat>_CF_LOAD_COMP` / `_CF_GEN_COMP` (0.3 CSF, 0 CPF/CTF) to each provision's `ur_energy_usage` (`_CF_LOAD_COMP`, raise) / `dr_energy_usage` (`_CF_GEN_COMP`, lower); only positive values are emitted (the split discharge / charge columns are reported under `ReserveProvision/reserve_{up,dn}_{discharge,charge}_sol`), and a dated battery `Capacity` to `emax` (scalar, or a per-block schedule); the `<bat>_AUX` battery is not emitted. The native rows never put reserve energy in a bus balance, so they cannot pin a negative CMg; see `test/source/test_bess_reserve_native.cpp`. |

### Soft-penalty policy

| Flag | Default | Effect |
|---|---|---|
| `--soft-penalty-cost F` | computed `min(max(gcost)+1, min(VoLL)−1)` | Override the shared penalty for forced-floor `pmin_fcost` + soft line overload + EL=1 soft cap. |
| `--water-fail-cost F` | 10 | Per-flow penalty $/(m³/s·h) on soft FlowRights / `discharge_*min` floors / Flow slack columns. |
| `--spill-fcost F` | PLEXOS `Max Flow Penalty` | Override per-flow spill cost on every `Vert_*` waterway. |
| `--spill-fcost-scale F` | 1.0 | Multiplicative scale on the `Vert_*` `Max Flow Penalty`. |

### Hydro topology + FCF

| Flag | Default | Effect |
|---|---|---|
| `--vert-routing {ocean,cascade}` | `ocean` | Vert_* spillway destinations. |
| `--reservoir-spillway [{basic,strict}]` | unset (bare flag = `basic`) | `basic`: set `Reservoir.spillway_cost = 0`. `strict`: also disable every duplicate spillway path. |
| `--soft-storage-bounds` / `--no-soft-storage-bounds` | OFF (hard) | Make reservoir `efin` / `emin` soft (slack priced at `efin_cost`) instead of hard `vol_end >= efin`. |
| `--emin-eod-day1` / `--no-emin-eod-day1` | OFF | Hard hour-24 emin clamp (operational floor from `Hydro_MinVolume.csv`). Default OFF — disabling brings PLEXOS-vs-GTOpt water-value duals in line. |
| `--battery-efin-pin` / `--no-battery-efin-pin` | ON | Pin every battery's end-of-horizon SoC to its initial SoC. |
| `--water-value-factor RES:F[,RES:F…]` | unset (1.0) | Multiply the boundary-cut water value of the named reservoirs (see §8). |
| `--hydro-min-mode {soft,hard}` | `soft` | Single-plant hydro min / max / ramp UserConstraints (`ANTUCOmin`, `COLBUNmax`, …): soft at the $10/MWh tier, or hard (debug only: without commit-gated rows most weekly cases become infeasible). |
| `--no-reservoir-flow-estimate` | estimate ON | Disable the topology-driven reservoir extraction-flow estimate (`fmin` / `fmax`). |
| `--plp-embalses PATH` / `--no-plp-embalses` | auto-detect `embalses.csv[.xz]` next to the bundle | Synthesise reservoirs a water-value slope references but the XML omits, from the PLP reservoir table, so the boundary cut keeps their term.  Off under `--plexos-legacy`. |

### Inflexible LNG tanks and 2024 hydro-emulated BESS

CEN PCP bundles from 2024 model two things with PLEXOS hydro objects.
The converter detects both from the model structure, not from object
names.

**Inflexible LNG (GNL inflexible) cargo.** This is a `Storage` whose
`Head Storage` generators all have a `Fuel` membership (for example
`ENGIE_MEJILLONES_GNL_INF`). The tank starts at the
`Hydro_InitialVolume.csv` value. Its volume unit is fuel/24. Each unit
drains the tank by `HeatRate · Generation / 24` per hour.
`Hydro_MaxVolume.csv` drops to 0 at the cargo
deadline. The only other way out is the `Vert_<tank>` spill, priced at
7200 \$ per fuel unit. Memberless `*_GNL_INF` storages (2025+ bundles)
are not tanks and are still dropped.

| Flag | Default | Effect |
|---|---|---|
Both modes emit the tank as a gtopt Reservoir:

* `eini` is the cargo, and the per-block `emax` comes from
  `Hydro_MaxVolume.csv`, keeping the drop to 0 at the deadline (the next
  ship's arrival).
* `flow_conversion_rate = 1/24`, so the volume unit is fuel/24.
* Each unit drains the tank through one Turbine with
  `production_factor = 1/HeatRate`.
* The reservoir is a state variable (gtopt default
  `use_state_variable = true`), so under SDDP its level carries over
  between phases. Its `Reservoir/water_value_dual` is the gas opportunity
  cost in \$ per volume unit (divide by 24 for \$ per fuel unit).

| Flag | Default | Effect |
|---|---|---|
| `--lng-inflexible-mode {storage,offtake}` | `storage` | `storage` follows PLEXOS: the spill costs 7200 \$ per fuel unit (`spillway_cost`, the `Vert_*` penalty; with `flow_conversion_rate = 1/24`, 1 flow·h is 1 fuel unit), and the units pay the fuel price. `offtake` treats the cargo as take-or-pay, so the gas is a sunk cost. The tank fuel's price is set to 0, which leaves the units with VOM + transport only in `gcost`. If the fuel also feeds units outside the tank, its price is kept and a warning is logged. If a negative fuel Transport Charge would make a tank unit's variable cost (VOM + transport·HR) negative, it is clamped to 0, and an INFO line is logged. The other gas bands (`_A`/`_B`, the manageable gas) are ordinary fuels and keep their price. The spill is free (`spillway_cost = 0`), and no holding cost (`ecost`) is applied. Gas left at a deadline that falls inside the horizon is lost at zero cost, and the tank gets no terminal value. A tank whose deadline lies after the horizon gets a terminal value instead (see the next row). |
| `--lng-inflexible-value {bundle,cuts,diesel}` | `bundle` | Sets that terminal value g, in \$ per volume unit (`offtake` only). It becomes the tank's own future-cost variable (see **Gas future value** below), not a slope in the hydro cut. `bundle`: the bundle's own price for the tank fuel × 24. This price is the opportunity cost from the CEN weekly LNG simulation (e.g. `Gas_Mejillones_INF` 231.0035 → 5544.08). `cuts`: the tank's column in `--lng-inflexible-cuts` (a medium-term boundary-cuts file); if the column is missing, it falls back to `bundle`. `diesel`: `--lng-inflexible-diesel-cost` (default: the median SRMC `HR·price + VOM` of the bundle's online diesel units) / the lowest HR among the tank's units × 24. |
| `--lng-inflexible-cuts CSV` | unset | A `scene,rhs,<state>…` file, read for `--lng-inflexible-value cuts`. Only the first cut row is used. |
| `--lng-inflexible-diesel-cost USD_PER_MWH` | median diesel SRMC | Reference diesel cost for `--lng-inflexible-value diesel`. |

In both modes, a tank that starts empty sets its units to `pmax = 0`.

**Gas bands as tanks (`--gas-bands-as-tanks {off,horizon,period}`).**
By default, each CEN gas band (`Gas_<company>_<band>`) is a priced Fuel
with a `max_offtake` budget taken from `Fuel_MaxOfftakeWeek.csv` (TJ ×
1000, pro-rated by the days of each gas week inside the horizon).

With the option, each capped band with gas type becomes a Reservoir
`gastank_<fuel>`:

* Its units drain it through Turbines with `production_factor = 1/HR`.
  The volume unit is fuel/24, the same convention as the LNG tanks.
* The units pay no fuel price, so `gcost` = VOM + transport, clamped at
  ≥ 0.
* The leftover is valued at the band price × 24 per volume unit, through
  the tank's own future-cost variable (see **Gas future value** below).
* A soft cap (`max_offtake_cost`) becomes a purchase Flow priced at
  `price + max_offtake_cost`.
* If the price varies over time, the units keep the residual
  `p_t − p_ref`, where `p_ref` is the last price and is also the terminal
  value. This keeps the cost exact.

`horizon` uses a single allotment. With `--gas-usable full` its LP equals
the Fuel model: `(p+c)·x + p·(V − T)` with `T = V + x − u` gives
`p·u + c·x`. This was checked on 2026-05-25, where the objectives agree
to within 6e-6 k\$.

`period` follows the CSV's gas weeks (Thursday→Wednesday, taken from the
row dates). The first week's share is `eini`. Each later week arrives as
an inflow at its start. Every band, INF or manageable, must be empty and
ready for the next allotment, so `emax = 0` on the block that ends each
sub-period inside the horizon, and the remainder is lost at zero cost.
The terminal value applies only when the band's last sub-period runs
past the horizon:

* a manageable band is valued at its price;
* an INF band, in `offtake` mode, follows `--lng-inflexible-value`.

A band whose last sub-period ends inside the horizon gets no terminal
value.

`--gas-refill {instant,ramp:<hours>}` sets the shape of that inflow. The
default is `instant` (the whole allotment in the first hour), because
the bundles give only volumes. `ramp:<hours>` spreads it at a constant
unloading rate. `emax` is only a cap: volume enters only through the
allotments.

Some bands stay priced Fuels:

* bands whose units burn several fuels;
* bands with a fuel-price override;
* bands whose units have a piecewise or time-varying heat rate;
* bands with no online unit.

A band drawn by a modelled 2024 LNG Storage keeps that Storage instead,
and the band's cap is dropped so it is not counted twice.

**Gas future value (`--gas-usable {capacity,full}`).** The PCP is
solved as a single monolithic LP. Each gas tank whose gas outlives the
horizon (an LNG tank in `offtake` mode with its deadline beyond the
horizon, or a band tank) gets its own future-cost variable. This
variable is separate from the hydro boundary cut, which keeps only the
water values:

```
alpha_gas_<tank> + g · reservoir("<tank>").efin >= g · V_usable,
    for(block in {<last block>});      alpha_gas_<tank> >= 0
```

`alpha_gas_<tank>` is a DecisionVariable with `raw` cost 1, so it enters
the objective at face value, with no probability, discount or duration
weighting. Its future cost is therefore `g · max(0, V_usable − T)`,
where `T` is the tank volume at the end of the horizon. The value of `g`
is:

* a manageable band: its price × 24;
* an INF band or LNG tank: `--lng-inflexible-value`.

Each tank needs its own `α`. With a single shared `α`, a set of cuts
gives the maximum of the cuts, not their sum. A slope merged into the
hydro cut would also value every leftover unit, with no saturation.

`V_usable` is the leftover that can still be burnt after the horizon:

* `capacity` (default): `min(emax at the horizon end, Σ unit pmax·HR/24
  × hours from the horizon end to the deadline / gas-week end)`. Gas the
  units cannot burn in time is worth 0. If the deadline is unknown, emax
  is used.
* `full`: emax, with no saturation, so every leftover unit is worth `g`.

A tank emptied inside the horizon gets no `α`. The CEN GNL storages
carry no end target (`End Effects Method = 1`, no End Volume / Target,
`Hydro_MinVolume` = 0), so no `efin` is set.

**PLEXOS `Reserve Units`.** A Generator or Battery `Reserve Units
Coefficient` counts the units that are *providing* reserve. That number
is at most the units generating, and it can be 0 while a unit is on. It
is therefore not the commitment status. The converter creates an
integer (binary) `reserve_units_<owner>[_<dir>]` DecisionVariable in
[0, 1] per block (`"integer": true`), with two hard rows:

* `r ≤ commitment status`;
* `provision.<dir> ≤ cap · r`, where `cap` is the provision's peak
  max response in that direction, or else the owner's pmax.

The owner is the generator, or `<bat>_gen` for a battery. The direction
comes from the first of these that applies:

1. the same owner's provision terms in the row;
2. the constraint name (`LW`/`Down`/`Lower` → dn, `RS`/`_Up`/`Raise`
   → up);
3. otherwise both directions.

A unit with no usable reserve provision has `r ≡ 0` (a fixed,
continuous column). PLEXOS treats Reserve Units as an integer, and so
does gtopt: `r` is a binary column. Under `--lp-relax` the converter
also emits `"relax": true` on these variables, so they are relaxed
together with the commitments; a continuous phase (`--no-mip`) relaxes
them as well.

**Hydro-emulated BESS (2024).** Each battery is built from these
PLEXOS objects:

* a storage `BAT_X` holding the stored MWh;
* a discharge generator `BAT_X` (Head Storage `BAT_X`);
* a charge generator `BAT_X_LOAD`, which draws from the co-located
  plant's zero-volume pondage `X` (inflow = the plant's `Gen_Rating`)
  into `BAT_X`;
* the loss rows `BAT_X_GenLosses` (`a·gen − Loss_BAT_X_GEN = 0`) and
  `BAT_X_LoadLosses` (`b·gen − Loss_BAT_X_LOAD = 0`).

The converter turns each such group that has an online unit into a
gtopt Battery:

* `output_efficiency = 1/(1/eff_G + a)` and
  `input_efficiency = 1/(1 + b·eff_L)`. COYA gives 0.970 / 0.980, which
  matches both the PLEXOS solution and the 2025+ `Battery` data
  (97 / 98 %).
* `eini` comes from `BESS_IniValue.csv` (in MWh).
* `max_cycles_day` comes from the charge unit's `Max Starts Day`.
* A hard row `battery(BAT_X).charge − generator(X).generation <= 0` is
  added, the same as PLEXOS 2025+ `CRCA_<bat>`.

Other constraint rows that reference the BESS generators are rewritten:

* The loss rows are absorbed into the efficiencies and logged at INFO.
* `Generation(BAT_X)` becomes `battery(BAT_X).discharge`.
* `Generation(BAT_X_LOAD)` becomes
  `eff_L · input_efficiency · battery(BAT_X).charge`.
* Rows with a commitment term on these generators (`*_Uniq`) have no
  Battery equivalent. They are dropped with a WARNING.

The pondage `X` is removed and the plant `X` becomes a plain generator.

Groups whose units are offline for the whole horizon (`BAT_ARICA`,
`BAT_NUEVA_IMPERIAL`, `BAT_ALFALFAL_VR1` on 2024-12-15) keep the legacy
hydro emulation. Only their loss rows are skipped, with an INFO log.
Those rows reduce to `0 = 0`, and they name loss waterways that are
never emitted. A non-standard online group is left unchanged, so the
strict unresolved-reference check still reports it.

### Loss model

| Flag | Default | Effect |
|---|---|---|
| `--loss-pwl-layout {dynamic,uniform,midpoint,tangent,equal_error}` | `dynamic` | Per-line PWL layout (see §9). |
| `--loss-error-pct F` | 0.01 (1 %) | Target absolute system-loss-error budget as a fraction of Σ Lmax,i. |
| `--nseg-losses N` | 10 | Segment-count ceiling of the adaptive rule (rounded up to an even K), or the count for every line with `--loss-error-pct 0`. |
| `--loss-extend-overload` | OFF | Extend each soft-cap line's PWL envelope across the soft-headroom band. |
| `--loss-tangent-lines NAME,…` | unset | Per-line escape hatch to force `tangent` layout. |
| `--nseg-tangent` / `--nseg-uniform` | 6 / 4 | Per-layout segment counts. |
| `--loss-cost-eps EPS` | not set (GTOpt: `0.1`) | $/MWh on line-loss columns (`model_options.loss_cost_eps`); emitted only when passed, `0` disables. |
| `--line-losses-mode MODE` | `tangent_signed_flow` | Written to `model_options.line_losses_mode` (`adaptive`, `piecewise`, `bidirectional`, `piecewise_direct`, `tangent_signed_flow`, …).  The `--help` text of this shared flag says "not set", but `plexos2gtopt` passes `tangent_signed_flow`. |
| `--loss-secant-segments S` | unset | L-secant chord segments of the `tangent_signed_flow` upper bracket (`model_options.loss_secant_segments`); also the SOS2 λ-form resolution. |
| `--loss-sos2-lines NAME,…` | unset | Lines that get the L-secant chord plus SOS2 fill-order enforcement (`loss_use_sos2 = true`); only effective under `tangent_signed_flow`. |
| `--loss-sos2-auto {off,heavy,all-lossy}` | `off` | Auto-pick SOS2 lines: top quartile by peak loss `R·envelope²` (`heavy`) or every lossy line (`all-lossy`, MIP-heavy).  Union with `--loss-sos2-lines`. |

### Line cap policy

| Flag | Default | Effect |
|---|---|---|
| `--el0-lines {unenforced,enforced,strict,extended}` | `unenforced` | Lines PLEXOS does not enforce (EL=0, EL=1 below the voltage threshold): not enforced, mirroring PLEXOS (10× rating cap, no penalty); `enforced`/`strict` hard cap at rating; `extended` soft headroom band. |
| `--lift-line-caps NAMES` | built-in list of ~35 CEN PCP lines (`DEFAULT_LIFT_LINE_CAPS_PLEXOS`, `gtopt_shared/cli_flags.py`) | Demote to EL=0 with wider free band (4×) + 10× hard cap. Empty string activates a global soft-EL=1 mode. |
| `--no-lift-lines NAMES` | `PMontt220->Chiloe110` | Lines never lifted, even when they are EL=0 or auto-detected. |
| `--auto-lift-lines [THR]` | unset | After write, run DC OPF and patch `enforce_level = 0` on lines whose unlifted flow exceeds THR × rating. |
| `--auto-lift-engine {pandapower,gtopt}` | `pandapower` | OPF engine for the auto-lift detector. |

### Network (voltage angles)

| Flag | Default | Effect |
|---|---|---|
| `--scale-theta FACTOR` | unset | Voltage-angle scale factor: the LP angle is FACTOR × θ (1 = radians, 1000 = milliradians). Unset ⇒ nothing is emitted and gtopt's C++ auto-scaling picks 1/(3·median(X/V²)); an explicit value is written to `model_options.scale_theta`. |
| `--use-single-bus` | OFF | Collapse the network to one bus (copperplate). |

### Commitment mode

| Flag | Default | Effect |
|---|---|---|
| `--lp-relax` | OFF (since 2026-05-23) | Emit `Commitment.relax = true` on every commitment, LP-relaxing the binaries. Default is MIP. |
| `--uc-relax {auto,plexos,none}` | `auto` | Per-commitment LP relaxation: PLEXOS Linear-Relaxation units (`plexos`) plus cost-free units (`auto`); `none` keeps every commitment integer. See §6 *Which commitments keep an integer status*. |
| `--uc-cycle {none,stage}` | `none` | `stage`: cyclic unit commitment over each stage (typical day / week): commitments get `cycle = "stage"` and no initial state. |
| `--long-transition {on,off,no_uc}` | unset | With `--uc-cycle stage`: declare `long_transition` on every unit whose minimum up / down time exceeds the typical period (the list is printed); without it GTOpt rejects the case and lists those units. |
| `--cogen-must-run NAME,…` | unset | Tag these generators `cogen_mode = must_run` (the tag only; `pmin` / `pmax` are not changed).  Detected CEN cogen units default to `dispatched`. |

### Curve-fit / debug

| Flag | Effect |
|---|---|
| `--use-plexos-commit` | Per-period pmax overlay = PLEXOS `Units Generating`. |
| `--use-plexos-gen-cap` | Per-period pmax hard-cap = PLEXOS `Generation` (hydro turbines only). |
| `--use-plexos-efin` | Reservoir.efin = PLEXOS End Volume. |
| `--no-check` | Skip the post-conversion comparison + JSON validation. |
| `--sanity-report PATH` | Write the post-conversion data-quality report as JSON (the summary is always logged): per-kV `|X|/V²` / `R/V²` distribution and lines below the tie threshold, kV-class impedance outliers, X/R, non-standard / mistyped bus kV, zero-rated units (and those still committed), profiles outside [0, 1], reservoirs without `emin` under an agreement reserve level, water-right rewards above the demand fail cost.  Same report on an existing case: `python -m gtopt_shared.sanity_report case.json` (skipped by `--no-check`). |

### Storage and generation data

| Flag | Default | Effect |
|---|---|---|
| `--drop-batteries` | OFF | Exclude every PLEXOS `Battery`-class object: empty `battery_array`, no battery reserve provisions, battery UC terms dropped as zero contributions. |
| `--default-storage-loss` | OFF | Write a synthetic standing loss where PLEXOS leaves it unset (batteries 21.5 %/yr, reservoirs 4 %/yr).  Off: such batteries and reservoirs are lossless, the PLEXOS default. |
| `--apply-generation-aux-use` | OFF | Apply `Gen_AuxUse.csv` as `Generator.lossfactor`.  Off by default because PLEXOS ships the file but does not apply it. |
| `--plexos-legacy` | OFF | Emit PLEXOS-faithful formulations used only for PLEXOS comparison (today: `BAT_*_CF_GEN_COMP` / `CF_LOAD_COMP` as `=` instead of `<=`); also disables the `--plp-embalses` patch. |

### Emissions

Per-fuel CO₂ / CH₄ / N₂O factors, the `EmissionZone` and the emission
sources are always written (the default factor file is the bundled
`gtopt_shared/data/emissions/cen_chile.json`).

| Flag | Default | Effect |
|---|---|---|
| `--emissions` | — | Deprecated no-op (emission data are always written). |
| `--no-emissions` | OFF | Strip emission-only fields (`Fuel.emission_factors`, `EmissionZone`, `EmissionSource`, `Generator.emission_rate`); keeps `Fuel.heat_content`. |
| `--only-emissions` | OFF | Emissions objective: `model_options.objective_mode = "emissions"` and the zone `price` set to `--carbon-price`. |
| `--carbon-price USD_PER_TCO2EQ` | 35.0 | Zone price under `--only-emissions`; ignored otherwise. |
| `--emissions-file PATH` | bundled file (above) | Custom emission-factor JSON. |
| `--emissions-report FILE` | `<output-dir>/plexos_emissions_report.json` | JSON report of the factor fill-in. |
| `--emissions-discount-rate RATE` / `--emissions-horizon-years Y` | 0.05 / perpetuity | Parameters of the synthetic emissions ray that replaces the cost FCF under `--only-emissions`. |
| `--emission-import-buses BUS,…` | unset | Generators on these buses are import proxies: their emissions go to a separate `<zone>_imports` zone (tracking only). |

### Run control and provenance

| Flag | Default | Effect |
|---|---|---|
| `-i, --input-bundle PATH` | — | Alias of the positional bundle path. |
| `--from-state PATH` | unset | Reload every option from a previous run's `plexos2gtopt_state.json` (written to every output directory); explicit flags on the command line override it. |
| `--write-out SPEC` | `all` | Passed to `options.write_out` (`sol`, `dual`, `rc`, `extras`, `all`, optionally `rc:Generator,Line`). |
| `--source-version X` (alias `--plexos-version`) | detected from the XML (`10.000` when absent) | PLEXOS version to assume: `8.100`, `9.000`, `10.000`.  `--help` lists the per-version differences. |
| `--operator {auto,none,aemo,amm,cen,coes,eor,xm}` | `auto` | Operator whose conventions apply; logged, does not yet change the output. |
| `-l, --log-level LEVEL` / `--no-color` / `-V, --version` | `INFO` | Logging and version. |

### `plexos2gtopt-line-lifts`

After a solve, `plexos2gtopt-line-lifts CASE_JSON OUTPUT_DIR` ranks the
binding lines of the case by the reduced cost of their flow column, to
choose `--lift-line-caps`:

| Flag | Default | Effect |
|---|---|---|
| `--lifted LIST` | unset | Lift list in use: comma list, text file or JSON file. |
| `--lifted-key KEY` | unset | Dotted key into a JSON `--lifted` file. |
| `--at F` | 0.999 | A block is "at cap" when abs(flow) ≥ F × rating. |
| `--top N` | 25 | Rows printed. |
| `--suggest` | OFF | Print a `--lift-line-caps` value: the current list plus new candidates above `--min-dual`. |
| `--min-dual D` | 1.0 | $/MWh threshold for `--suggest`. |
| `--json FILE` | unset | Write the full table. |

---

## 13. Output layout

```
gtopt_PLEXOS20260422/
├── PLEXOS20260422.json              # planning JSON (options + simulation + system)
├── PLEXOS20260422.provenance.json   # per-element source / units / transforms
├── boundary_cuts.csv                # FCF terminal cut (scene, rhs, <reservoirs>)
├── uc_commitment.pampl              # one per non-empty UC family
├── uc_comparison.pampl
├── uc_config_exclusivity.pampl
├── uc_gas_offtake.pampl
├── uc_operational.pampl
├── uc_reserve.pampl
├── uc_security.pampl
├── uc_terminal_value.pampl
├── solvers/
│   └── cplex.prm                    # curated MIPGap=0.01, Gomory=2, …
├── plexos_cache/                    # mdb-exported .accdb tables (only when --use-plexos-* is set)
│   └── *.csv.zst
├── Generator/                       # Parquet schedule tree (one column per object)
│   ├── pmax.parquet
│   ├── pmin.parquet
│   ├── gcost.parquet
│   └── …
├── Demand/lmax.parquet
├── Line/{tmax_ab,tmin_ab,in_service,…}.parquet
├── Reservoir/{inflow,emin,emax,…}.parquet
├── Reserve*/, Battery/, Waterway/, Junction/, Turbine/, Flow/, …
```

The whole tree is gtopt-native: `gtopt <case>.json` (with the
solver of your choice) runs directly on it. The `provenance.json`
sidecar documents the conversion mapping for every emitted entity
class, so a reader can answer "where does `Generator.gcost` for unit
X come from?" without re-reading the converter source.

---

## 14. Limitations + known divergences

* **Single scenario, single stage.** CEN PCP is by design one
  operating week; multi-scenario / multi-stage stitching is left to
  upstream tooling (the GTOpt SDDP / cascade solvers stitch the
  per-week planning files themselves).
* **Round-trip is one-way.** GTOpt → PLEXOS export is not supported
  (out of scope).
* **Wheeling / contract / charge / tariff** classes are skipped.
* **Multi-band cost.** PLEXOS's `t_band` piecewise costs are
  collapsed to the first band for the common case; quadratic /
  multi-segment forms (e.g. 118-Bus benchmark) are honoured via
  `pmax_segments` + `heat_rate_segments` + virtual unit-price fuel.
* **Region.Load vs Node.Load.** The PLEXOS comparison uses `Node.Load`
  (the LP's actual demand at each bus); `Region.Load` also folds in
  battery charging and losses, so it is not comparable with gtopt's
  consumer demand.
* **FCF obj_constant.** The FCF intercept is added to the gtopt
  objective post-solve as a constant (it does not enter the LP), so
  it must not be used to explain differences in the operational
  cost.

---

## 15. AEMO ISP dispatch window (prototype)

`python -m plexos2gtopt.aemo_isp` builds an hourly gtopt **dispatch**
case for a short window of one scenario of AEMO's Integrated System Plan
(ISP) PLEXOS model (the capacity-outlook "DLT" database, one XML per
scenario, with half-hourly traces shipped as separate archives).  It
reuses the MasterDataSet reader (`plexos_xml`) but not the CEN PCP
extractors: the ISP database keeps its time series in `Data File`
objects (`Traces\<kind>\<file>.csv`) and scopes most properties with
seasonal timeslices and dated rows.

```bash
python -m plexos2gtopt.aemo_isp "2026 ISP Step Change Model.xml" \
  -t "<extracted>/2026 ISP Solar traces" -t "<extracted>/2026 ISP Wind traces" \
  -t "<extracted>" -t "<extracted>/2026 ISP Timeslice traces" \
  -s 2027-01-18 -d 7 -o case -n isp2026_sc_2027w03
```

| ISP / PLEXOS | gtopt |
|---|---|
| `Node` (sub-region) | `Bus` |
| `Node.Load` → demand trace (MW, half-hourly → hourly mean) | `Demand.lmax` |
| `Generator` with `Units > 0` on the start day | `Generator`, `pmax = Units × Rating(trace / timeslice) or Max Capacity × (1 − Aux Incr)`, `gcost = VO&M` + `fuel` with `heat_rate = Heat Rate / (1 − Aux Incr)` (legacy: `gcost = Heat Rate × Fuel.Price + VO&M`) |
| `Battery` with `Units > 0` | `Battery` (`Capacity`, `Max Power`, SoC limits, efficiencies; SoC 50 % at both ends) |
| `Line` Max/Min Flow (timesliced, dated) | `Line.tmax_ab` / `tmax_ba`, transport model, lossless |
| `Storage` (1000 m³), `Waterway`, `Head/Tail Storage`, `Efficiency Incr`, `Natural Inflow` (m³/s) | `Reservoir` (hm³, `flow_conversion_rate = 0.0036`, `Max Spill` → `spillway_capacity`), `Junction`, `Waterway`, `Turbine`, `Flow` |
| `Pump Load` (MW) × `Pump Units`, `Pump Efficiency` (%) on a hydro generator with a `Tail Storage` | tail → head `Waterway` + `Pump` (`pump_factor = Efficiency Incr`, `efficiency = Pump Efficiency`) + a flexible `Demand` (`capacity` = pump MW, `fcost = 0`) |
| `Max Energy Day` (DSP), `Max Energy Year` (hydro without storage) | daily `UserConstraint` (`daily_sum`) |
| `Mark-up` ($/MWh; rooftop PV −1) | added to `gcost` |
| `Min Load` (MW, timesliced; Tas hydro) | per-block `pmin`, capped at `pmax` |
| `Min Capacity Factor Year` (%, coal / gas) | yearly energy floor: not enforced by default; `--long-periods prorate` adds a window-sum `generation ≥ CF × Units × Max Capacity × (1 − Aux Incr) × hours` (capped at Σ `pmax`) |
| `Outage Factor` (bands 2–3), `Maintenance Rate` (new entrants), `Min Capacity Factor` (0) | not modelled (no outages in a deterministic week) |

### Hydro levels and the end-of-week value

The XML stores volumes in 1000 m³ (converted ÷ 1000 to hm³), flows in
m³/s and `Efficiency Incr` in MW/(m³/s), so every reservoir carries
`flow_conversion_rate = 0.0036` hm³/(m³/s·h).

PLEXOS uses `Initial Volume` only at the model horizon start (2026-07-01
in the 2026 ISP); a later week's level is a simulation result the XML
does not carry.  The week-start volume `eini` is therefore:

1. `Initial Volume` when the window starts on the horizon start;
2. otherwise a live `Target Week` (the level the previous week had to
   end at: 0 for the irrigation dams Blowering, Dartmouth, Eildon and
   Hume, which PLEXOS empties every week) or `Target`;
3. otherwise `Initial Volume` as the modeller's representative level
   when it is above `Min Volume`;
4. otherwise (an "empty" placeholder: Koombooloomba, Kuranda) the
   middle of `[Min Volume, Max Volume]`.

The end of the week is constrained only where the source data say so:

| PLEXOS | gtopt |
|---|---|
| `Target Week` | hard `efin` = the target |
| `End Effects Method` = 2 (Recycle) | hard `efin = eini` (PLEXOS recycles over the `Balance Period`, a day in the 2026 ISP; only the week end is closed) |
| `End Effects Method` 1 (Free) / 0 (Auto) / unset | hard `efin = eini`, no price (`--hydro-end hold`, the default); no `efin` with `--hydro-end free` |

A one-week case has **no future value of water**: no reservoir carries
an end-of-week price.  By default the end volume may not fall below the
start volume, so the week cannot spend stored water as free energy
(with `--hydro-end free` the Free storages, Hydro TAS among them, drain
to `emin`).  Water is coupled between stages by the state variable, and
its future value should come from SDDP cuts; only the last stage needs
a terminal condition.

`--hydro-water-value V` (opt-in, $/MWh; overrides `--hydro-end` for
those reservoirs) gives every reservoir without a source-data target
a soft `efin = eini` with the shortfall priced at
`efin_cost = V × MWh per hm³`, where the MWh per hm³ is the largest sum
of turbine production factors along a path down the cascade × 10⁶ /
3600 (parallel units are alternatives, a series cascade adds up); ponds
with no turbine below count one 1 MW/(m³/s) unit.
`<name>.stats.json` → `hydro_level_rule` / `hydro_end_rule` count the
rule used per reservoir.  `--legacy` keeps the old behaviour (Initial
Volume clamped into the band, `efin = eini` with `efin_cost = 10⁴`, no
pumping, no explicit conversion rate).

Value selection per day: band-1 rows inside their date window; rows
tagged with an active `Timeslice` override untagged rows; the latest
`date_from` wins and a `Data File` reference wins over a plain number.
Timeslices come from `M1`..`M12` names, month patterns on `Include`
(`M4-10`) and the `timeslice_RefYear5000.csv` state-change file.

### Constraints, purchasers, hydrogen, emissions, gas offtake

By default the converter also translates the PLEXOS `Constraint` objects
that apply to a fixed fleet over one week.  The module is
`aemo_isp_constraints.py`; the hydrogen design is in
`scripts/plexos2gtopt/AEMO_ISP_H2.md`.

| PLEXOS | gtopt |
|---|---|
| REZ export / import limit on the REZ's own injections | REZ bus + radial line (`tmax_ab` = export, `tmax_ba` = import) |
| other operational / operational + capacity constraint, `RHS` | per-block `UserConstraint` (capacity terms folded into the RHS) |
| `RHS Day` (e.g. gas-zone daily offtake, TJ) | `daily_sum` `UserConstraint`, GJ: the fuel's units as `Heat Rate / (1 − Aux Incr) × generation` |
| `RHS Month` (period ≤ 1 month) | window-sum `UserConstraint`, RHS pro-rated by hours |
| `RHS Year` / `Custom` (period > 1 month) | not enforced by default; `--long-periods prorate` enforces the pro-rated share |
| flexible `Purchaser` (electrolyser) | `CarrierConverter` → `HydrogenNode` |
| H2 consumption constraint (monthly GWh_e) | `HydrogenDemand` `min/max_energy` (MWh_LHV) |
| fixed-load `Purchaser` | `Demand` (fixed `lmax`) |
| `Emission` budgets + generator / fuel `Production Rate` | `EmissionZone` (tracking; pro-rated cap with `--long-periods prorate`) + `EmissionSource` |

Sense −1 / 0 / 1 → `<=` / `=` / `>=`.  A Penalty Price of −1 or none
means hard; a positive one becomes a soft `penalty`.

Skipped, with the reason counted in `<name>.stats.json` →
`constraint_groups`:

- capacity / build conditions;
- disabled constraints (`Include in LT Plan` = 0);
- constraints with no coefficients;
- constraints whose terms are all absent from the case;
- budgets that do not overlap the window;
- constraints whose RHS Data File trace is missing.

**Approximations** (also listed in `stats.json` → `approximations`):

- Period RHS are pro-rated to the window.
- Period values are taken as GWh (TJ for fuel), and period penalties
  as $/GWh.
- PLEXOS `Heat Rate` is per gross MWh while gtopt dispatches sent-out
  MWh.  Fuel cost (`heat_rate × price`), gas-zone offtake and emission
  rates all use `Heat Rate / (1 − Aux Incr)` per sent-out MWh
  (`--legacy` keeps the old unadjusted `gcost`).
- Targets over more than one month (annual policy targets such as the
  SA renewable target, multi-year emission budgets) are not enforced by
  default.  A uniform weekly share of a seasonal target forced load
  shedding in the July and October weeks.
- The electrolyser efficiency comes from the IASR workbook.

#### Network augmentations of a development path (`--odp-augmentations`)

REZ and flow-path augmentations are PLEXOS generators in a `Network
Augmentation …` category with `Units` 0 and `Max Capacity` 1 MW; the
long-term plan builds them (`Max Units Built`, dated).  A REZ export
limit carries them as `−1 × Installed Capacity`, so with the XML
`Units` the limit stays at its pre-augmentation RHS.  For example,
`ExportLimit_N7_Tumut` is 0 MW until the HumeLink REZ augmentation is
built, and it cannot be built before 2027-07-01.

`--odp-augmentations <outlook.xlsx>` reads a published ISP outlook
workbook (e.g. `2026 ISP - Step Change - Core.xlsx`; user-supplied,
never copied into a repository) and adds the MW built in the
`--odp-cdp` path (default `CDP4`, the optimal development path) by the
week's financial year:

1. **Timings sheet**: an augmentation whose option name (the object
   name without the `<REZ>_` prefix and the ` Augmentation` suffix) is
   a `Line` of the CDP is built in full (`Max Units Built × Max
   Capacity`) from 1 July of the financial year before `StartFYE`.
2. **REZ Generation Capacity sheet**: any other augmentation that is
   buildable on the start day and appears in a REZ export limit gets
   the smallest build that lets the REZ's installed generation in the
   CDP for that financial year (electrolysers excluded) export at full
   output: `max(0, G_REZ − min_t(RHS − folded capacity))`.  Builds are
   filled in name order and each is capped at its `Max Units Built`.
   The workbook does not publish per-object augmentation MW, so this is
   the minimal build consistent with the ODP fleet, not the PLEXOS
   build itself.

The same per-object MW applies in every constraint that uses the
object, including import and group limits.  `stats.json` lists
`odp_augmentations` (object, rule, FY, MW) and `odp_unmatched` (DNSP
and group-REZ objects, objects not buildable yet, objects with no
timing that appear only in import or group limits), all of which keep
the XML `Units`.  Shared-upgrade caps (e.g. N6 + N7 HumeLink ≤ 2200
MW) are build conditions and are not checked.

`--legacy` (or the individual `--no-isp-constraints`, `--no-purchasers`,
`--no-emissions`, `--no-fuel-offtake`, `--no-rez-buses` flags)
reproduces the earlier output.  `--h2-model demand` models
electrolysers as curtailable demands with window-sum constraints.

Not converted: capacity expansion (`Max Units Built`, `Build Cost`,
`WACC`), retirements beyond the start-day `Units`, line losses and
marginal loss factors, outages and reserves (`--legacy` also drops
pumping).

Other options: `-t, --trace-root DIR` (repeatable; the XML's folder is
searched first), `-s, --start YYYY-MM-DD`, `-d, --days N`,
`-o, --output-dir`, `-n, --name` (JSON stem), `--no-hydro` (hydro
generators without water balance), `--no-batteries`,
`--demand-fail-cost`, `--electrolyser-kwh-per-kg` (required when the
case has hydrogen targets; take it from the AEMO IASR workbook, which is
not redistributed) and `-l, --log-level`.

### 15.1 AEMO ISP expansion case (monthly stages, prototype)

`python -m plexos2gtopt.aemo_isp_expansion` builds a multi-year
generation, storage and network expansion case on top of the dispatch
converter: one stage group per month of the FYs `--first-fy ..
--first-fy + --years − 1`, plus `--fill-years` unreported FYs (default
1) that damp the end effects.  `--phases year` writes one SDDP phase per
FY; installed capacity and reservoir volumes are the state variables.
The `stats.json` records the exact `command` and `converter_revision`.

```bash
python -m plexos2gtopt.aemo_isp_expansion "2026 ISP Step Change Model.xml" \
    -t <traces> --first-fy 2028 --years 3 --rep-days 2 \
    --capacity-penalty 1e7 -o cases/fy28 -n fy28
```

| Flag | Default | Effect |
|---|---|---|
| `xml` | — | ISP PLEXOS XML (positional) |
| `-t, --trace-root DIR` | XML folder | Trace search root (repeatable) |
| `-o, --output-dir DIR` / `-n, --name NAME` | required / `isp_exp_fy<FY>_<N>y` | Output directory and case name |
| `--first-fy Y` | 2028 | First FY (ending year: 2028 = 2027-28) |
| `--years N` | 3 | Reported FYs |
| `--fill-years N` | 1 | Unreported FYs appended to damp end effects |
| `--phases {none,month,year}` | `none` | SDDP phase layout |
| `--rep-days {1,2}` | 1 | Representative days per month |
| `--rep-kind {netload,weekday}` | `netload` | Split of `--rep-days 2` |
| `--cycle-group` | OFF | Representative days of a month form one weighted storage cycle |
| `--week-sampler SPEC` | unset (representative days) | One typical week per month: `uniform:H[:point]`, `peak_ramp:K`, `chebyshev:N[:gauss\|lobatto][:key=value]`, `chrono:D` |
| `--day-sampler SPEC` | unset (24 hourly blocks) | Chebyshev nodes on each representative day: `chebyshev:N[:key=value]` or `chebyshev:auto[:tol=T][:metric=rms\|ramp]` (provisional) |
| `--week-start DAY:HOUR` | `wed:12` | Start of the typical-week window (not used by `chrono:D`) |
| `--week-rule {peak,energy}` | `peak` | Which real week is the typical week |
| `--ramp-refine {off,on,30m,15m}` | `off` | Sunrise / sunset refinement (below) |
| `--ramp-refine-width H` | 1 | Half width [h] of each refinement window |
| `--point-storage-integration RULE` | `cc` | Storage rule on point stages: `cc` (`clenshaw_curtis`), `lg4_pos`, `lg4`, `lg6_pos`, `lg6`, `trapezoid`, `block`, or `none` (GTOpt's per-type defaults) |
| `--discount-rate R` | the model's LT Plan Discount Rate | Annual discount rate |
| `--capacity-year-fleet {mean,end,tightest}` | `mean` | Yearly value of the existing fleet in the capacity constraints |
| `--capacity-penalty F` | unset (hard) | Slack cost of the yearly capacity constraints (recommended with `--phases month\|year`) |
| `--candidate-prefix P` | all candidates | Keep candidates whose category starts with P (repeatable) |
| `--no-hydro` / `--no-batteries` / `--no-isp-constraints` | OFF | Drop hydro balance / batteries / PLEXOS Constraint objects |
| `--demand-fail-cost F` | dispatch converter default | Unserved-energy cost |

`chrono:D` is the chronological reference: hourly blocks over the first
D days of every month.  `--day-sampler` replaces the 24 hourly blocks of
each representative day by Chebyshev nodes on a periodic 24 h window
(the day closes its storage cycle); `chebyshev:auto` picks the order
from the wind and solar traces among 4, 6, 8, 12, 16, 24, 32 and 48.

**Time representation** (`stats.json` → `period`, `blocks_per_stage`):

| Option | Stages per month | Blocks | Storage cycle |
|---|---|---|---|
| `--rep-days 1` | 1 (month-mean day) | 24, duration = days | daily (`daily_cycle`) |
| `--rep-days 2` | 2: high / low net-load days | 24 each | daily |
| `--rep-days 2 --rep-kind weekday` | 2: weekdays / weekend | 24 each | daily |
| `... --cycle-group` | as above | 24 each | one weighted cycle per month (`Stage.cycle_group`, weights = days per typical week) |
| `--week-sampler uniform:1` / `uniform:2` | 1 typical week | 168 / 84 | weekly (`period_hours` 168) |
| `--week-sampler uniform:H:point` | 1 typical week | 168 / H | weekly, point-sampled (power-based: node powers at the cell centres) |
| `--week-sampler peak_ramp:K` | 1 typical week | 7·(12 + K) | weekly |
| `--week-sampler chebyshev:N[:gauss\|lobatto][:k=v]` | 1 typical week | N (Gauss; periodic Lobatto, default: node N ≡ node 0 dropped) | weekly, point-sampled (`Stage.sampling = "point"`) |

A typical week is one real week of the month (`--week-rule peak`: the
week holding the month's net-load peak; `energy`: the highest net
energy), read from the **half-hourly** traces.  Every week sampler
starts its window at `--week-start` (default `wed:12`: Wednesday
12:00 → next Wednesday 12:00, a week centred on the weekend, so the
low-load weekend that drives storage cycling and curtailment sits in
the middle of the window, away from the cyclic closure; `mon:0` gives
calendar weeks).  The same window is used by every sampler, so their
results are directly comparable.  Its block durations are
the real durations × days in month / 7, so energy, costs and capex are
month totals while storages, ramps and minimum up / down times run on
the real week (`Stage.period_hours = 168`, formulation §5.7).

**Storage integration on point stages.**  Whenever a stage is
point-sampled (Chebyshev weeks / days, `uniform:H:point`), the converter
writes `battery_storage_integration = reservoir_storage_integration =
"cc"` into `model_options`: batteries and reservoirs integrate with the
Clenshaw–Curtis rule, `e_k = e_{k−1} + w_k f_k` with the node's CC
weight (one node per row, positive weights, the weekly cycle closes
exactly on the quadrature that also integrates energy and cost).
`--point-storage-integration lg4_pos` (or `trapezoid`) selects a banded
rule with levels AT the nodes and envelope rows bounding the level
between them (slower to solve than `cc`; see
[Typical-Week Sampling §7a](../methods/typical-week-sampling.md)).
`none` writes nothing and leaves GTOpt's per-type defaults.  The rule
lives in `gtopt_shared.point_sampling` and is shared with
`tcheby2gtopt`.
Block-sampled weeks keep the block recursion; override with
`--set model_options.battery_storage_integration=...`.

`uniform:H:point` is the hourly (H-hourly) week of `uniform:H` emitted
as a point stage — the power-based formulation of Morales-España et
al. (instantaneous powers at the nodes, energy = quadrature of the node
powers in every balance).  Node values are the cell means; only the
storage integration and the ramp rows differ from `uniform:H`.

**Sunrise / sunset refinement.**  `--ramp-refine 30m|15m|on` (default
`off`) adds resolution within ± `--ramp-refine-width` h (default 1) of
the steepest daily net-load fall and rise, detected from the data.
Block samplers (`uniform:H`, `peak_ramp:K`, `uniform:H:point`) split the
base blocks of each window into 30 / 15 min blocks (cell means of an
energy-conserving PCHIP of the cumulative energy; exact data at 30 min
on the half-hourly traces) and tag every block with
`Block.commitment_cell` = its base block, so commitment binaries stay
on the base grid.  A Chebyshev (Lobatto) week of order N keeps its
nodes and adds the nested order-2N nodes inside the windows (positive
weights: the 2N Clenshaw–Curtis weights with each dropped node's weight
split linearly between its neighbours); see
[Typical-Week Sampling](../methods/typical-week-sampling.md) §4b–4c.

`peak_ramp:K` splits each day into 2 h slots and uses 1 h blocks in the
slot of the daily net-load peak and the K − 1 slots with the steepest
net-load change (solar ramps).

**Chebyshev week (provisional).**  The window is centred on the weekend
(`--week-start`, default Wednesday 12:00 → Wednesday 12:00).  The nodes are Chebyshev–Gauss
($t_k = \tfrac T2(1-\cos\frac{\pi(2k-1)}{2N})$, k = 1..N) or
Chebyshev–Gauss–Lobatto ($t_k = \tfrac T2(1-\cos\frac{\pi k}{N})$,
k = 0..N; default), and block durations are the matching Fejér /
Clenshaw–Curtis weights (sum exactly T = 168 h; `durations=voronoi`
uses cells between node midpoints).

**Node values.**  The standard coefficient computation (`values=fct`,
default) is the fast Chebyshev transform followed by truncation:

1. Sample the exact antiderivative $F(x) = \int_{-1}^x f$ of the
   half-hourly step function (piecewise linear and continuous) at
   $M_f + 1$ Chebyshev–Lobatto points, with $M_f$ = `fct_points`,
   default 4096.
2. Get the coefficients of $F$ by DCT-I in $O(M_f\log M_f)$, then
   differentiate in coefficient space (`chebder`) to get those of $f$.
3. Truncate at the node set's degree.

Sampling $F$ rather than $f$ keeps $F(1) - F(-1)$ (the energy) and avoids
aliasing the jumps; `values=fct_point` samples $f$ directly, for
comparison.  The coefficients converge to the continuous projection as
$M_f$ grows.  At N = 64 the maximum coefficient difference is
1.4e-4 / 3.6e-5 / 5.8e-6 / 3.1e-6 for $M_f$ = 512 / 1024 / 2048 / 4096.

The equivalent continuous projection, computed without a transform, is
`values=lsw` (also `proj`).  It is the cell least-squares generalized
inverse $c = G^+y$ over the half-hourly interval means $y$, with
$G^+ = \Gamma^{-1}A_\theta^\top W$.  Here $W = \mathrm{diag}(\Delta\theta_j)$
is the Chebyshev measure of each data cell ($\theta = \arccos x$), and
$\Gamma = \mathrm{diag}(\pi, \pi/2, \dots)$ is the exact Chebyshev Gram
matrix: the weighted-norm generalized inverse for the step-function
data.  It needs no linear solve and is stable for any N, and the
operator is cached per (N, nodes, window).

After truncation, $c$ is rescaled multiplicatively so that $`\int p\,dt`$
equals the trace energy; the factor is 1 ± 1e-3.  When the data have
both signs, an additive change of $c_0$ is used instead.  Nodes take
$p(t_k)$.  With `bound_mode=clip_rescale` (default), node values
outside the window's data range (the Gibbs overshoot at solar sunrise /
sunset, up to about 0.19 of the peak on FY28) are clipped, and the
energy is restored on the headroom.  `bound_mode=report` keeps them.

Discrete normal equations do not work here.  $(A^\top WA)^{-1}A^\top W$
(`values=lswd`) has cond $A^\top WA$ = 7e3 / 1e10 / 1e18 at
N = 64 / 96 / 128, because equispaced cells are too wide in θ near the
window ends to resolve high modes.  The plain fit (`values=lsu`, W = I)
is worse.  Other options (`chebyshev:N:key=value`):

- `solar_night=mask|off`.  A polynomial fit leaks small values into
  the night (Gibbs), about 1 % of the weekly solar energy at N = 64 on
  a synthetic week.  With `mask` (default), every node whose quadrature
  cell has zero data is set to exactly 0.  The energy is then restored
  by a multiplicative rescale over the daylight nodes only, and those
  nodes are clipped into [0, max].  So the sun never shines at night
  and the weekly energy stays exact.
- `solar_basis=plain|clearsky`.  `clearsky` factors a trace with zero
  periods as $`y = S\,k`$.  $S$ is the window's daily envelope, the
  maximum over the days of each half-hour, which is exactly 0 at night.
  Only the clearness $k$ is fitted; night values of $k$ are linearly
  interpolated, since they are multiplied by 0.  The node value is
  $`S(t_k)\,\hat k(t_k)`$, followed by the same daylight energy
  restoration.  The sunrise and sunset kinks stay in $S$ and are not
  fitted.

  On FY28 utility solar, clearsky does **not** beat the default (plain
  fit + night mask), whichever envelope is used:
  - the window's 7-day maximum: RMS .108 vs .090 at N = 64, .048 vs
    .039 at N = 168;
  - the maximum over all 26 RefYear5000 years within ±7 days, smoothed
    over ±1 day (close to a cloud-free envelope): RMS .101 vs .090 at
    N = 64, .044 vs .039 at N = 168.  It is better in only 6–24 % of
    the trace-windows, and its convergence slope (−0.73) is no better
    than plain's (−0.77).

  The variability that matters is the clouds inside $k$, not the
  sunrise and sunset kinks.  The option is kept for experiments but is
  not recommended.
- Hourly reconstruction of solar output uses
  `week_reconstruct.masked_reconstruction_matrix`:
  $`R_m = \mathrm{diag}(\text{day})\,R\,C`$ is exactly 0 at night, and
  each node column keeps its energy on the daylight cells.  Prices
  consistent with it are the adjoint of $R_m$ restricted to the
  daylight nodes (`rebuild_price_adjoint(..., operator=R_m,
  active=day)`), for which the revenue identity is exact.
- `chebyshev:auto[:tol=T][:metric=rms|ramp]` (T default 0.03) picks the
  order from the wind and solar traces, which are the hard ones; demand
  and inflows are smooth.  A pre-pass reads every month's window and
  finds the smallest N in {32, 48, 64, 96, 128, 168, 224, 336} that
  meets the tolerance:
  - `rms` (default): the capacity-weighted RMS of (fit − data) relative
    to installed capacity, i.e. in capacity-factor units, is ≤ T;
  - `ramp`: the energy-weighted hourly-ramp error is ≤ T.

  Candidates are screened with the coefficient-tail estimate
  $\sqrt{\sum_{k>n}c_k^2/2}$ and checked on the samples.  The case uses
  the maximum N over the months, because the builder needs the same
  block count in every stage; per-month N is in
  `stats.chebyshev_auto_orders`.  The selection is
  `week_sampler.choose_order`.
- `values=wcell`: the data mean over each node's quadrature cell.  The
  cells of length $w_k$ partition the window, so the energy is exact
  and the values stay within bounds.
- `values=proj` is the continuous orthogonal Chebyshev projection of
  the half-hourly step function,
  $`c_k = \tfrac{2}{\pi}\int_0^\pi f(\cos\theta)\cos k\theta\,d\theta`$.
  It is integrated exactly per data cell with no linear solve, so it is
  stable for any N.  The truncation is then corrected by the minimal
  change in the Chebyshev-weighted norm that restores
  $`\int p\,dx = \int f\,dx`$; the correction is about 1e-6 relative.
  The node values are $p(t_k)$.  They show mild Gibbs overshoot near
  sharp features (solar sunrise / sunset), and `bound_mode=clip_rescale`
  removes it while keeping the energy.
- `values=ls` is the energy-constrained least-squares projection of the
  half-hourly interval means onto the Chebyshev polynomials of the
  node set's degree, with node values evaluated from it.  It keeps the
  window energy exactly, but fitting a degree-N polynomial to equispaced
  data is only stable for N up to about 2√(samples).  On 336
  half-hours the condition number is 1e2 at N = 64, 1e5 at N = 96 and
  1e15 at N = 168, and the node values near the window ends blow up.
  `values=interp|mean` uses interpolation or Voronoi cell means instead.
- `periodic=C0|C1` adds p(start) = p(end), and with C1 also p′.
- `odd_terms=none|K` keeps no odd coefficient (a symmetric week) or the
  first K.
- `bound_mode=clip_rescale|bounded|report` handles node values outside
  the bounds (default: the window's data range).  `clip_rescale`
  (default) clips them and restores the energy on the headroom,
  `bounded` refits with the bounds as constraints (`ls` only), and
  `report` leaves the values unchanged.

**Relation to the reference implementation.**  The method follows the
reference Chebyshev scheduling code of the method's paper (`ChebyLib`
and `ChebyInOutLP`).  Its points
are the Chebyshev–Lobatto extremes $x_i = \cos(\pi i/(n-1))$ mapped to
the stage window.  Lobatto is the default here too; Gauss nodes are
kept only for comparison.

The paper's LP variables are Chebyshev **coefficients** $c$, $m$ of
them with $m \le n$ samples.  Bounds hold at the sample points,
$c_{\min} \le A c \le c_{\max}$ with $A_{ij} = T_j(x_i)$, and
commitment rows are $c_{\min}u \le Ac \le c_{\max}u$.  Costs use
trapezoid weights $t$ on the points (`add_trap_cost`, $q = t^\top A$).
Ramps use the 3-point non-uniform difference with first-order ends
(`diffd_op`), which is gtopt's `ramp_formula = central2`.  Reservoir
dynamics are collocated with the spectral derivative,
$D v - q = 0$ with $`D = A\,\mathrm{diff}\,A^{-1}`$.  Bus and other row
duals are divided by each point's trapezoid weight
(`get_row_duals_dt`).

gtopt's block formulation is the same model when $m = n$.  The block
values are the point values $u = Ac$, and $A$ (the Lobatto
Vandermonde) is invertible.  So bounds become column bounds on $u$, and
costs become $`\sum_k w_k\,\mathrm{cost}\cdot u_k`$ with a per-block weight
$w_k$, the block duration.  gtopt divides duals by $w_k$ in the same
way.  Differences from the paper:

- The default weights are Clenshaw–Curtis (`durations=quadrature`).
  They are exact on the node polynomial, all positive on Lobatto
  points, and still diagonal.  The paper's trapezoid weights are
  `durations=trapezoid`.
- Storage uses the sparse recursion
  $`e_{k+1} = e_k + \text{flow}_k\,w_k`$.  The end-of-week total is
  then the exact quadrature of the flow, and intermediate levels are a
  first-order approximation.
  - The level $e_k$ refers to the block END
    $\tau_k = \sum_{j\le k} w_k$, not to the node $t_k$.  Each node
    lies inside its block $[\tau_{k-1}, \tau_k]$, but they can differ
    by 2.1 / 1.4 / 0.8 h at N = 64 / 96 / 168.
  - Taken at $\tau_k$, the level matches $\int_0^{\tau_k} f$ to about
    0.1–0.8 % of weekly throughput.  Taken at $t_k$, the error is
    1.4–6 %.
  - The reconstruction (`rebuild_level`) and the bound checks place
    levels at $\tau_k$.
  - The dense $S = VJV^{-1}$ (`cumulative_integration_matrix`) is exact
    at the nodes.  The paper's collocated spectral
  derivative, or the cumulative integration $V J V^{-1}$, is dense;
  it is a deferred option.
- The case $m < n$ (fewer coefficients than points, i.e. bounds
  enforced at more points than unknowns) needs coefficient columns,
  with dense rows $A_i c$, in gtopt.  It is a design note and is not
  implemented.

Trace data: the paper samples the hourly series at the points
(`get_t_values`).  Schedules go through `get_energy_coeffs`, an L1 fit
$\min \sum|Ac - y| + e_t$ with $Ac \ge 0$ and the trapezoid energy
$t^\top Ac - e_t = t^\top y$ ($e_t \ge 0$).  With $m = n$ that is
interpolation of the point samples.  It is available as `values=l1`,
solved with HiGHS:
- `l1_oversample=K` uses K·(N+1) sample points (the paper's demand
  variant uses 2);
- `l1_y=point|cell` takes the value at each point or the mean over its
  cell.

**Hourly reconstruction** (`plexos2gtopt.week_reconstruct`).  Node
solutions $u_k$ (generation, flows) become the interpolating polynomial
$g = \sum_j c_j T_j$ with $u = Vc$ and $V_{kj} = T_j(x_k)$.  Its hourly
or half-hourly cell means integrate to $\sum_k u_k w_k$ exactly,
because the LP weights are the interpolatory quadrature of the node
set.  The written duals are in \$/MWh (gtopt divides by
probability × discount × block duration), so the raw block duals are
$\lambda_k = p_k w_k$.

The price polynomial $p = \sum_j a_j T_j$ lives in the same N-dimensional
space and solves $G a = V^\top\lambda$, where
$`G_{ij} = \int T_iT_j\,dt`$ (closed form).  Then
$`\int p\,g\,dt = \lambda^\top u`$ exactly for every $g$ of the space:
revenues, congestion rents and total cost (Σ marginal value × quantity)
are preserved.  $V$ and $G$ are well conditioned: cond V ≈ 1.5 and
cond G ≈ 80–215 for N = 64–168.

Hourly prices are evaluations of $p$, and their discrete hourly revenue
sum differs from the exact integral only by the midpoint-rule
residual.  Alternatives are the hourly-grid adjoint $R^\top D p = \lambda$
(exact for the hourly sum; needs cells ≥ nodes) and naive interpolation
of the node prices (leaves a revenue gap).  Storage levels are
interpolated, and bound violations between nodes are reported.

The hourly means of energy-like quantities are identical to the paper's
`cheby2hour.py`, $`E = Z\,\tfrac{T}{2}\,B\,I\,A^{-1}`$ (tested to
1e-10).  The paper's `cheby_interpol.py` maps every output, duals
included, to hours by piecewise-linear interpolation of the node
values.  For energy-like quantities that does not keep the energy, and
for prices it is not dual-consistent.  It is available as
`method="linear"` for comparison.

The node, weight, value and ramp (`ramp_formula = central2`) definitions
follow the description of the method's reference paper and will be
matched to its equations once available.

**Storage and reservoirs.**  Batteries (existing and candidates) keep
`daily_cycle`: the period (day or week) cycle is closed even though
PLEXOS uses End Effects "Free".  Free would let a representative period
borrow energy from a non-consecutive one.  Reservoir volumes are a
state coupled across all stages with a free terminal volume at the
horizon end (no `efin`).  Storages with End Effects = Recycle or a
`Target Week` cycle within each period.  Hydro topology is the union
over the months, so units commissioned later (Snowy 2.0) keep their
water coupling.

**Expansion.**  Generation candidates expand in MW.  Storage
candidates expand power and energy together (`energy_power_ratio`).
ODP network augmentations remain decision variables; the
`--odp-augmentations` fixing applies to the dispatch converter only.

## 16. Related references

* `scripts/plexos2gtopt/DESIGN.md` — the original v0 design (CSV
  inventory, entity mapping, time model). Most P0/P1/P2/P3 items
  are implemented; the doc here is the operational truth.
* `docs/scripts/plp2gtopt.md` — sibling PLP converter; shares the
  proactive-tests + per-class writer architecture + `_comparison`
  contract.
* `docs/scripts/sddp2gtopt.md` — sibling PSR-SDDP converter; the
  PLEXOS layout was modelled after it (XML + per-class CSV).
* `docs/formulation/mathematical-formulation.md` — GTOpt LP/MIP
  formulation; the canonical reference for what every emitted
  element does at LP-build time.
* `include/gtopt/constraint_parser.hpp` — the AMPL-style
  expression parser that consumes `UserConstraint.expression`.
* `include/gtopt/{generator,line,battery,reservoir,turbine,waterway,reserve_provision,decision_variable,commitment}.hpp`
  + matching `_lp.cpp` — the GTOpt LP class catalogue (types,
  accessors registered via `add_ampl_variable`, schedule fields).
* `$GTOPT_CASES_DIR/chile/daily_scheduling/pcp_2026-04-22/source/` — the
  canonical reference
  bundle the converter is exercised against in every release.
