# gtopt2plexos — gtopt to PLEXOS Exporter

Writes a gtopt planning as a **PLEXOS** dataset that
[`plexos2gtopt`](plexos2gtopt.md) reads, and optionally the gtopt
**results** of the case as PLEXOS solution tables (the schema of a PLEXOS
`RES` solution database).  It is the inverse of `plexos2gtopt`, so a case
and its solution can be handed to PLEXOS users, or compared with a PLEXOS
run, in their own format.

---

## Usage

```bash
gtopt2plexos case.json -o plexos_case                  # dataset
gtopt2plexos case.json -o plexos_case --strict         # exit 2 if anything was lost
gtopt2plexos case.json -o plexos_case --results case/output   # + solution tables
gtopt2plexos case.json -o plexos_case --results case/output --results-only
```

| Option | Effect |
|--------|--------|
| `-o, --output DIR` | Output directory (created) |
| `--start-date YYYY-MM-DD` | Calendar date of the first hour in the Data Files (default 2030-01-01) |
| `--results DIR` | gtopt output directory of the case: also write its solution in `<output>/plexos_solution/` |
| `--results-only` | With `--results`: write only the solution tables |
| `--strict` | Exit code 2 when the export printed any warning |
| `-v, --verbose` | Debug log |

The tool prints the `plexos2gtopt` command that reads the case back:

```bash
plexos2gtopt -i plexos_case -o back \
  --block-layout plexos_case/block_layout.csv --line-losses-mode none \
  --no-reservoir-flow-estimate        # only when the case has reservoirs
```

## Files written

The output directory is an uncompressed PLEXOS bundle, the simplest input
`plexos2gtopt` accepts (`plexos_loader.locate_bundle`: a directory with
`DBSEN_PRGDIARIO.xml`; no zip needed):

| File | Content |
|------|---------|
| `DBSEN_PRGDIARIO.xml` | MasterDataSet: `t_class`, `t_object`, `t_collection`, `t_membership`, `t_property`, `t_data` (static properties on the `System -> <Class>` memberships) |
| `Gen_Rating.csv` | Hourly available capacity per generator (long `NAME, YEAR, MONTH, DAY, PERIOD, BAND, VALUE`) |
| `Nod_Load.csv` | Hourly load per node (wide `YEAR, MONTH, DAY, PERIOD, <node>...`) |
| `Lin_MaxRating.csv`, `Lin_MinRating.csv` | Hourly line limits, only for lines whose limits vary |
| `BESS_IniValue.csv` | Battery initial state of charge [% of capacity] |
| `Hydro_WaterFlows.csv` | Hourly natural inflow per storage [m³/s] |
| `Hydro_MaxVolume.csv`, `Hydro_MinVolume.csv` | Hourly volume bounds, only when they vary |
| `block_layout.csv` | The gtopt blocks as runs of hours (`plexos2gtopt --block-layout`) |
| `gtopt2plexos.json` | Name map, volume factors, read-back flags, warnings |

With `--results`, `plexos_solution/` holds one CSV per solution table, in
the column layout of `mdb-export` of a PLEXOS solution `.accdb`:
`t_class`, `t_collection`, `t_property`, `t_unit`, `t_object`,
`t_membership`, `t_model`, `t_key`, `t_data_0`, `t_phase_3` and
`t_period_0`.  mdbtools cannot write Access files, so no `.accdb` is
produced; the tables load through
`plexos2gtopt.plexos_solution.csv_table_source` (with
`gtopt2plexos.results.load_tables(dir)`) exactly as `mdb_table_source`
serves a real solution.

## Time

`plexos2gtopt` reads one scenario and one chronological stage of hourly
intervals.  Each gtopt block becomes `duration` consecutive hours with
the block value, and `--block-layout` regroups them into the same blocks
on read-back (the mean of equal hours is the block value), so blocks of
any whole number of hours survive.  Stages are concatenated into one
stage; only scenario 1 is written.  A block that is not a whole number of
hours is an error.

## Mapping

| gtopt | PLEXOS |
|-------|--------|
| Bus | `Node` (`Voltage`), in bus-uid order (`plexos2gtopt` numbers buses 1..n) |
| Demand | `Nod_Load.csv` column of its bus (demands of one bus are summed) |
| `demand_fail_cost`, Demand `fcost` | `Region.VoLL`, one Region per distinct value |
| Generator | `Generator` + `Nodes`; `min(pmax, profile × capacity)` → `Gen_Rating.csv` and `Max Capacity`; `gcost` → `VO&M Charge` |
| Line | `Line` + `Node From` / `Node To`; `tmax_ab` / `-tmax_ba` → `Max Flow` / `Min Flow` (or the rating files); `Enforce Limits = 2`; reactance, resistance per unit on 100 MVA (`X_pu = 100 X / V²`) |
| Battery | `Battery` + `Nodes`: `Capacity` (emax), `Max Power` (discharge), `Max Load` (charge), `Charge` / `Discharge Efficiency` [%], `Self Discharge Rate` [%/h] (`annual_standing_loss`, default 0.48 p.u./yr), `Max Cycles Day` |
| Reservoir | `Storage`: `Max` / `Min` / `Initial Volume` in CMD (`× 1 / (24 · flow_conversion_rate)`) |
| Turbine | its Generator gets `Head Storage` (and `Tail Storage` when the outlet junction has a reservoir); `production_factor` → `Efficiency Incr` |
| Flow into a reservoir junction | `Hydro_WaterFlows.csv` |

Batteries: `plexos2gtopt` gives a battery with `Max Cycles Day > 0` a
chronological (`daily_cycle = false`) state of charge and writes the
cycle limit only when it can bind.  A gtopt `daily_cycle = false`
battery is therefore written with an unreachable `Max Cycles Day`, and so
is any battery when the merged stage is longer than 24 h with blocks
longer than 1 h (where gtopt would rescale a daily cycle by 24 / T).

Results (`--results`):

| gtopt output | PLEXOS property |
|--------------|-----------------|
| `Generator/generation_sol` | `Generator.Generation` [MW] |
| `Demand/load_sol + fail_sol`, per bus | `Node.Load` [MW] |
| `Demand/fail_sol`, per bus | `Node.Unserved Energy` [MW] |
| `Bus/balance_dual` | `Node.Price` [\$/MWh] |
| `Line/flow_sol` | `Line.Flow` [MW] |
| `Reservoir/energy_sol` | `Storage.End Volume` [CMD, same factor as the dataset] |
| `Battery/energy_sol`, `fout_sol`, `finp_sol` | `Battery.Energy` [MWh], `Generation`, `Load` [MW] |

Periods (`period_id`) are the gtopt blocks in order; `t_phase_3` maps
every hour of a block to it, phase 3 (short term), band 1.  Scenario `s`
is sample `s` and sample 0 is the probability-weighted mean (the sample
`PlexosSolution` reads).  Output tables are read as Parquet (file or
hive directory) or CSV, whole or per scene / phase (`*_s0_p0`), long or
legacy wide (`uid:N`).

## What is not carried

Each loss is a warning (and `--strict` fails), never silent.  Among them:
scenarios after the first and stage structure / discount factors;
time-varying `gcost` (duration-weighted mean in `VO&M Charge`);
generator `pmin` (`plexos2gtopt` reads `Min Stable Level` only through
unit commitment); expansion fields; line `tcost`, `active` schedules and
time-varying impedances; a zero reverse limit (`Min Flow 0` means unset
in PLEXOS, read back as `tmax_ab`); battery `emin` and `efin`
(`plexos2gtopt` reads `emin = 0`, `efin = eini`); reservoir `efin`,
`fmin` / `fmax`, spill costs; junctions without reservoir, waterways,
run-of-river turbines fed by a Flow, production-factor curves; and the
arrays with no PLEXOS object written (user constraints, reserves,
rights, emissions, converters, fuels, commitments).

## Round-trip check

```bash
gtopt2plexos case.json -o px && plexos2gtopt -i px -o back \
  --block-layout px/block_layout.csv --line-losses-mode none
```

`gtopt_shared.case_compare.compare_cases` compares the two plannings by
what they model regardless of names.  The test suite checks a
single-scenario variant of the tiny case comes back with differences
only in the reservoir volumes (the CMD rescale above), the full tiny
case only in the warned scenario-2 inflow and the gcost mean, and that
both solve to the same optimal cost; `cases/ieee_9b` comes back with
only its warned generator `pmin`, `cases/ieee_14b` with no difference,
both at the same optimal cost.
