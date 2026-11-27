# gtopt2sddp — gtopt to PSR SDDP Exporter

Writes a gtopt planning as a **PSR SDDP** block study: the fixed-width
and CSV `.dat` files that [`sddp2gtopt`](sddp2gtopt.md) reads (its raw
`.dat` front-end).  It is the inverse of `sddp2gtopt`.

---

## Usage

```bash
gtopt2sddp case.json -o psr_case
gtopt2sddp case.json -o psr_case --start-year 2030 --start-month 4
gtopt2sddp case.json -o psr_case --sfx ab --hydro-year 1990 --strict
gtopt2sddp case.json -o psr_case --results case/results   # + PSR result CSVs
gtopt2sddp case.json -o psr_res --results case/results --results-only
```

| Option | Effect |
|--------|--------|
| `-o, --output DIR` | Output directory (created) |
| `--sfx XX` | Two-letter system suffix of the file names (default `gt`: `ctermigt.dat`, ...) |
| `--start-year`, `--start-month` | Date of the first stage (PSR stages are consecutive months) |
| `--hydro-year Y` | First hydrology year written to `hinflw.dat` (scenario *s* reads year `Y + s - 1`) |
| `--results DIR` | gtopt output directory of the solved case: also write its results as PSR SDDP result files (see [Results](#results)) |
| `--results-only` | With `--results`: write only the result files, not the `.dat` inputs |
| `--strict` | Exit code 2 when the export printed any warning |
| `-v, --verbose` | Debug log |

Read back with `sddp2gtopt -i psr_case -o back`.  `gtopt2sddp.json`
records the PSR names given to each element (PSR keeps 12 characters),
the plant to junction map and every warning.

## Files written

`sddp.dat` (horizon, series, deficit and spill costs, losses flag,
discount rate), `sistem.dat`, `dbus.dat` / `busconnection.dat` /
`volt.dat`, `dcirc.dat` + `mcirc.dat` (lines and their changes),
`ctermi`/`mtermi`/`ccombu` (thermal units, per-stage changes, one dummy
fuel), `rgme` (per-block generation caps), `chidro` + `hinflw.dat`
(hydro plants and inflows), `cgnd` + `gndsite` + `blrenw` (renewables
and their per-block availability), `deme` + `dbf` (demand in GWh, one
load class per bus), `duvame` (block durations) and `cbattery`.

## Mapping

| gtopt | PSR SDDP |
|-------|----------|
| Stage, Block | monthly stages with the same number of blocks each; block durations in `duvame` |
| Bus, Line | `dbus`, `dcirc` (X% = X·10⁴/V², V the higher end voltage); a case with no voltage is written at 1 kV so it stays per unit |
| Generator | thermal unit (`ctermi`), `gcost` → `CVaria`; per-stage changes in `mtermi`, per-block `pmax` in `rgme` |
| Generator with a profile | renewable source (`cgnd`), profile × capacity → `blrenw` |
| Junction + Reservoir + Turbine | hydro plant (`chidro`): `VMin`/`VMax`/`VInic`, `FPMed`, `QMax`; turbine outlet `TAA`, spill `VAA` |
| Flow | `hinflw` (sum per plant, per stage) |
| Demand | `deme` (GWh per stage) + `dbf` block factors |
| Battery | `cbattery` |
| Demand fail cost, spill cost | deficit line and `vert` in `sddp.dat` |

## What PSR cannot carry

Each loss is a warning (and `--strict` fails), never silent.  Among
them: asymmetric or per-block line limits (stage maximum), several
demands on one bus (merged), per-demand fail costs (one deficit cost),
unequal scenario probabilities (PSR uses 1/N), a junction without spill
or with `drain_capacity` (PSR spill is unbounded), reservoir `efin` and
time-varying `emin`/`emax`, intra-stage variation of `gcost`, `pmin` or
inflows (stage mean / minimum), withdrawals, line retirements, and the
arrays with no PSR file here (user constraints, reserves, rights).
Reactances below 0.05 % are floored by PSR.

## Round-trip check

```bash
gtopt2sddp case.json -o psr --start-month 4 && sddp2gtopt -i psr -o back
```

`back` is split in one phase per stage; solve it with
`--set method=sddp` and compare the SDDP upper bound with the original
cost.  `gtopt_shared.case_compare.compare_cases` compares the two
plannings by what they model regardless of names; the test suite checks
that a small case comes back with no difference and the same cost.

## Results

`--results DIR` writes a solved gtopt run (`DIR` = its output directory,
any layout gtopt writes) as PSR SDDP result files in the "graf" CSV
layout: a short header, then one row per stage x series x block and
one column per agent:

```text
Varies per block?       ,1,Unit,GWh,2,4,2030
Varies per sequence?    ,1
# of agents             ,1
Stag,Seq.,Blck,TERMO
   1,   1,   1,      0.200000
```

Line 1 carries the per-block flag, the unit, the stage type (2 =
monthly) and the first stage (month, year); `Stag` is the 1-based stage,
`Seq.` the 1-based gtopt scenario (PSR series), `Blck` the block within
the stage (`1` in per-stage files, flagged `Varies per block?,0`).
Agent names (padded to 12) are those of the `.dat` export.  This is the
layout `sddp2gtopt` reads for `watervcp.csv` / `volfincp.csv` /
`inflow.csv`.

| File | Unit | Content (agents) |
|------|------|------------------|
| `gerter.csv` | GWh | thermal generation |
| `gerhid.csv` | GWh | hydro generation (one plant per junction) |
| `gergnd.csv` | GWh | renewable generation |
| `gerbat.csv` | GWh | battery discharge |
| `cmgbus.csv` | \$/MWh | bus marginal cost = `Bus/balance_dual` |
| `cmgdem.csv` | \$/MWh | system marginal cost: demand-weighted mean (agent `gtopt`) |
| `demxba.csv` | GWh | demand per bus (served + unserved) |
| `defbus.csv` | GWh | deficit per bus |
| `defcit.csv` | GWh | system deficit (agent `gtopt`) |
| `cirflw.csv` | MW | circuit flow, positive from `BusFrom` |
| `qturbi.csv` | m3/s | turbined outflow |
| `qverti.csv` | m3/s | spilled outflow (spill waterways + drain) |
| `volfin.csv` | hm3 | end-of-stage volume (per stage) |
| `valagu.csv` | k\$/hm3 | water value: stage mean of `Reservoir/water_value_dual` / 1000 (per stage) |

Energies are MW x block hours / 1000.  gtopt writes duals back-scaled
(`scale_objective`, probability, discount and block duration removed),
so `cmgbus` is the undiscounted \$/MWh price.  No PSR result sample is in
this repository: the file names are the usual SDDP output names
(`gerter`, `gerhid`, `gergnd`, `cmgbus`, `cmgdem`, `defcit`, `cirflw`,
`qturbi`, `qverti`, `volfin`), `gerbat`, `demxba`, `defbus` and
`valagu` follow the same naming, and the trailing fields of the first
header line are informative (the readers here key on the `Stag` row).
