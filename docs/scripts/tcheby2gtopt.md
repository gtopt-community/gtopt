# tcheby2gtopt — Chebyshev scheduling test systems → GTOpt (block and point stages)

Converts the small validation systems of a Chebyshev short-term
hydrothermal scheduling research code (SQLite *system* and *simulation*
databases) into GTOpt plannings, each in **two time representations**:

- `<name>_block.json`: uniform 1 h blocks over the window (classic block
  semantics, no `Block.time`);
- `<name>_point.json`: one **point-sampled stage** per original stage
  (`Stage.sampling = "point"`) on the `N + 1` Chebyshev–Lobatto nodes of
  `[0, H]`, with `Block.time` = node time and `Block.duration` = the
  Clenshaw–Curtis weight of the node (the weights add up to `H`).

The same package also builds the case library
[`cases/cheby_small`](../../cases/cheby_small/README.md): hand-checkable
mini cases plus the converted test systems, used to test point stages,
`storage_integration` and per-unit commitment grids.

Background on point stages, the Chebyshev grid and the storage rules:
[Typical-week sampling](../methods/typical-week-sampling.md).

---

## Invocation

`tcheby2gtopt` has **no console script**; it is installed with the
scripts package and run as a module:

```bash
pip install ./scripts
python -m tcheby2gtopt --help
python -m tcheby2gtopt convert --help
python -m tcheby2gtopt library --help
```

(From a source checkout without installing, run it from `scripts/`.)

## Quick start

```bash
# Rebuild the mini cases and caso2emb (no external data needed)
python -m tcheby2gtopt library --out cases/cheby_small

# ... plus the cases read from the SQLite test databases
python -m tcheby2gtopt library --src <tcheby>/tests/test_dbs \
  --out cases/cheby_small

# Convert one system DB (with its simulation DB: stages and inflows)
python -m tcheby2gtopt convert system1_caso1emb_fact.db \
  --simu simu1_caso1emb_fact.db --name caso1emb --out-dir out/

# Without a simulation DB: one 168 h stage, 48 Lobatto intervals
python -m tcheby2gtopt convert system2.db --name system2_week \
  --hours 168 --nodes 48 --time-zero 2029-01-01 --pmin drop --out-dir out/

gtopt out/caso1emb_point.json --solver highs
```

`convert` always writes both `OUT_DIR/<name>_block.json` and
`OUT_DIR/<name>_point.json` and prints their paths, followed by the
mapping notes (`note: ...`, e.g. dropped fields and the commitment
class of every unit).

---

## `convert` options

```text
python -m tcheby2gtopt convert SYSTEM_DB --name NAME --out-dir DIR [options]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `SYSTEM_DB` | — | tcheby system database (buses, generators, lines, hydro network, commitment) |
| `--simu FILE` | — | simulation database: stage lengths, time zero and per-stage inflows of the first scenario |
| `--name NAME` | required | case name (output file prefix) |
| `--out-dir DIR` | required | output directory |
| `--hours H [H ...]` | `24` | stage lengths [h] when there is no `--simu` |
| `--nodes N` | `24` | Lobatto `N` per point stage (`N + 1` nodes) |
| `--time-zero DATE` | `2016-01-01` | time zero of date-keyed schedules without `--simu` (with `--simu` the study's time zero is used) |
| `--start-hour H` | `0` | hours from the time zero where the case window starts |
| `--point-values {wcell,interp}` | `wcell` | node values of hourly series on point stages, see below |
| `--drop-hydro-pmin` | off | drop `pmin` of hydro generators (water-short horizons) |
| `--pmin {hard,drop}` | `hard` | keep generator `pmin` as a floor, or drop it |
| `--line-losses` | off | emit line `resistance`, so GTOpt models losses (off by default so objectives stay hand-checkable) |
| `--point-storage-integration RULE` | `cc` | `battery_storage_integration` and `reservoir_storage_integration` written for point stages: `cc` (Clenshaw–Curtis, the sparsest), `lg4_pos`, `trapezoid`, or any other rule GTOpt accepts; `none` writes nothing (GTOpt's per-type defaults) |
| `--commitment-period-rule {class,none}` | `class` | point stages: per-unit `commitment_period` grids, or `none` (no `commitment_period`; the source UC resolution is dropped) |
| `--commitment-period NAME=H` | — | point stages: commitment grid [h] of unit `NAME` (repeatable) |
| `--commitment-breaks H1,H2,...` | — | daily commitment grid: cell boundaries in hours of the day (`study` = `3.5,7.5,10.5,14.5,17.5,23.5`) → `model_options.commitment_breaks` + `Stage.start_hour`; replaces the per-unit class grids (an explicit `--commitment-period NAME=H` still wins). See [typical-week-sampling §4c](../methods/typical-week-sampling.md) |
| `--commitment-breaks-file CSV` | — | per-month daily grids (rows `month,breaks`, month 1..12 or `jan`..`dec`) → `Stage.commitment_breaks` by each stage's calendar month; months missing from the file use `--commitment-breaks` |

The storage option is written only to the `_point` file; block cases keep
GTOpt's per-type defaults.  The value is not checked by the converter;
GTOpt rejects an unknown rule when it reads the case.

### Node values on point stages (`--point-values`)

- `wcell` (default): the mean of the hourly step series over the node's
  quadrature cell (cells of length `w_k` partition `[0, H]` in node
  order).  Energy is exact and values stay inside the data range.
- `interp`: linear interpolation of the hourly values (placed at the
  hour midpoints) at the node times.  Energy is not conserved for
  non-linear series.

### Commitment grids on point stages

With `--commitment-period-rule class` every commitment gets a
`commitment_period` and `ramp_allowance = "boundary"` on the point case.
The grid of a unit is, in order of precedence:

1. its `--commitment-period NAME=H` override;
2. the source model's UC time resolution;
3. a class from `m = min(min_up_time, min_down_time)`: `m ≥ 8 h` → slow,
   4 h grid; `m ≥ 4 h` → medium, 3 h; otherwise fast, 1 h.

The grid is never below 1 h.  The class of every unit is logged and
printed as a note.  With `none`, the point case has no
`commitment_period` and the source UC resolution is dropped (the
previous behaviour).

---

## `library` options

```text
python -m tcheby2gtopt library --out DIR [--src TEST_DBS]
```

| Option | Meaning |
|--------|---------|
| `--out DIR` | output root (normally `cases/cheby_small`) |
| `--src DIR` | the research code's `tests/test_dbs` directory; without it only the mini cases and `caso2emb` (transcribed from PLP input files) are written |

Every case is written as `DIR/<case>/<case>_{block,point}.json`, with a
`conversion_notes.txt` next to the converted systems; mini cases go to
`DIR/mini/<case>/`.  The library uses fixed per-case options (stage
lengths, nodes, `pmin` mode, losses); see
[`cases/cheby_small/README.md`](../../cases/cheby_small/README.md) for
the cases, their sources and their expected objectives.  The JSON files
of that directory are generated; do not edit them by hand.

---

## Mapping

| tcheby | GTOpt |
|--------|-------|
| `Bus` | `bus_array` |
| `PowerSystem.fail_cost` | `model_options.demand_fail_cost` |
| `Generator` (capacity, pmin, pmax, cvar + schedules) | `generator_array` (`capacity`, `pmin`, `pmax`, `gcost`) |
| `Demand` (load + schedule) | `demand_array.lmax` |
| `Line` | `line_array`: `tmax_ab = tmax`, `tmax_ba = −tmin`; `x` → `reactance` [Ω] with `voltage` [kV]; `r` → `resistance` only with `--line-losses` |
| `Junction` | `junction_array`; a junction with no outgoing waterway and no reservoir gets `drain: true` |
| `Waterway` | `waterway_array` (`fmin`, `fmax`) |
| `Reservoir` | `reservoir_array` (hm³, m³/s); `spillway_capacity = 0` unless an `Overspill` exists |
| `Inflow` | `flow_array` (`discharge`); a simulation DB overrides it per stage |
| `HidroTurbine` | `turbine_array` (`production_factor` = efficiency, MW per m³/s) |
| `Commitment` | `commitment_array` (min up/down, start/stop cost, start/stop ramp, initial state); the generator's `pmin` becomes the commitment `pmin` |
| `Passthrough` | run-of-river generator with `pmax = min(capacity, efficiency·flow)` |
| `Overspill` | `spillway_cost` and `spill_junction` |

Schedules are zero-order holds (a value holds from its key to the next
key); date keys are converted to hours from the time zero.  The
databases are opened read-only.

Written options: `annual_discount_rate = 0`, CSV output (uncompressed),
`use_kirchhoff = true`, `use_single_bus` when the system has one bus,
`scale_objective = 1`.

**Dropped** (reported in the notes): every `*.active_sched`, reserve
zones and generator reserve limits, waterway delays (GTOpt waterways have
no travel time), `Commitment.time_resolution` on block cases, the
simulation's discount rate and the stochastic openings (one scenario:
the stage → scene map of the first scenario).

GTOpt's turbine is `gen ≤ production_factor · flow` where tcheby uses an
equality, a relaxation that lets water bypass the turbine.

---

## Tests

```bash
cd scripts
python -m pytest tcheby2gtopt/tests -q
```

The tests cover the database reader, the mapping, the Clenshaw–Curtis
weights and the reproducibility of the committed `cases/cheby_small`
JSON files.

## See also

- [Typical-week sampling](../methods/typical-week-sampling.md) — point
  stages, Chebyshev nodes, storage integration rules, commitment grids.
- [`plexos2gtopt`](plexos2gtopt.md) — typical weeks / days with the same
  Chebyshev sampler (`week_sampler.ChebyshevSampler`).
