# gtopt2plp — gtopt to PLP Exporter

Writes a gtopt planning as a **PLP** case: the `plp*.dat` files that
[`plp2gtopt`](plp2gtopt.md) reads.  It is the inverse of `plp2gtopt`, so
a case can be handed to PLP users, or checked against PLP, in their own
format.

---

## Usage

```bash
gtopt2plp case.json -o plp_case            # write the .dat files
gtopt2plp case.json -o plp_case --strict   # exit 2 if anything was lost
gtopt2plp case.json -o plp_case --start-month 4 -v
gtopt2plp case.json -o plp_case --results case/results   # + PLP output files
gtopt2plp case.json -o plp_res --results case/results --results-only
```

| Option | Effect |
|--------|--------|
| `-o, --output DIR` | Output directory (created) |
| `--start-month M` | Calendar month of the first stage (default: the stage `month`, else April, the start of the PLP hydrological year) |
| `--results DIR` | gtopt output directory of the solved case: also write its results as PLP output files (see [Results](#results)) |
| `--results-only` | With `--results`: write only the result files, not the `.dat` inputs |
| `--strict` | Exit code 2 when the export printed any warning |
| `-v, --verbose` | Debug log |

The tool prints the `plp2gtopt` command that reads the case back, with
the flags that carry what PLP files cannot hold (`-y all`, the scenario
probabilities `-p`, the discount rate `-d`), and the `gtopt --set`
overrides that solve the read-back case like the original:

```bash
plp2gtopt -i plp_case -o back -y all
gtopt back/back.json --set model_options.strict_storage_emin=true
```

(`plp2gtopt` makes reservoir `emin` soft, PLP's per-stage minimum;
a gtopt case has a hard `emin` by default.)

## Files written

Always (possibly with a zero count): `plpblo`, `plpeta`, `plpbar`,
`plpcnfli`, `plpcnfce`, `plpdem`, `plpcosce`, `plpmance`, `plpmanli`,
`plpaflce`, `plpextrac`, `plpmanem`.  When needed: `plpess` (batteries),
`plpcenre` (production-factor curves), `plpfilemb` (seepage), `plpmat`
(spill cost) and `centipo.csv` (solar / wind types).  `gtopt2plp.json`
records the central names and numbers, the read-back flags and every
warning.

## Mapping

| gtopt | PLP |
|-------|-----|
| Stage, Block | `plpeta` (hydrological month), `plpblo` |
| Bus | `plpbar` (kV taken from the incident lines) |
| Line | `plpcnfli`, per-block limits in `plpmanli`; a line without voltage is per unit (`Tension` 1) |
| Junction + Reservoir + Turbine | *embalse* central |
| Junction without reservoir | *serie* central |
| Turbine fed by a Flow, Generator with a profile | *pasada* central (`plpaflce` in m³/s or MW; solar / wind in `centipo.csv`) |
| Other Generator | *termica* central; `gcost` per stage in `plpcosce`, per-block `pmax`/`pmin` in `plpmance` |
| Battery | *bateria* central + `plpess` |
| Demand | `plpdem` (one per bus) + one *falla* central per bus (`CosVar` = fail cost) |
| Turbine outlet, spill, seepage, other waterways | `SerHid`, `SerVer`, `plpfilemb`, `plpextrac` |
| Reservoir bounds per stage | `plpmanem` |
| Scenario inflows | `plpaflce`, one hydrology per scenario |

## What PLP cannot carry

Each loss is a warning (and `--strict` fails), never silent.  Among
them: several demands on one bus (merged), a `gcost` that varies within
a stage (stage mean), expansion fields (`expcap`, `expmod`, ...),
battery `eini`/`emin` and different charge / discharge limits, turbine
flow capacity (PLP bounds the turbine by `PotMax / Rendi`), water
withdrawals (`direction = -1`), `tcost` and time-varying line
impedances, per-plant spill costs (PLP has one `CVert`), and the arrays
with no PLP file (user constraints, reserves, rights, emissions).

## Round-trip check

```bash
gtopt2plp case.json -o plp && plp2gtopt -i plp -o back -y all
```

`gtopt_shared.case_compare.compare_cases` compares the two plannings by
what they model (demand per bus, effective generator limits and costs,
line limits and susceptances, inflows, reservoir and battery bounds)
regardless of names and numbering; the test suite checks that a small
case comes back with no difference and the same optimal cost.

## Results

`--results DIR` writes a solved gtopt run (`DIR` = its output
directory, any layout gtopt writes: hive Parquet, Parquet / CSV shards,
`.csv.zst`) as the output files PLP CEN65 itself writes, so the tools
and scripts that read PLP output read gtopt results unchanged — e.g.
`gtopt_compare --case plp --plp-output plp_case`.  Headers, row order
and number formats follow the CEN65 Fortran writers (`plp-gdbdcen.f`,
`plp-gdbdbar.f`, `plp-gdbdlin.f`, `plp-gdbdemb.f`, `genpdess.f`); names
and numbers are the ones the `.dat` export assigns (`CenNum` = the
`plpcnfce` number, `BarNum` = bus uid, `LinNum` = `plpcnfli` position).

| File | Columns | Content |
|------|---------|---------|
| `plpcen.csv` | `Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,CenQgen,CenPgen,CenEgen,CenInyP,CenInyE,CenRen,CenCVar,CenCostOp,CenPMax` | every central but the deficit ones (`CenTip` `Emb`/`Ser`/`Pas`/`Ter`/`BAT`) |
| `plpfal.csv` | `Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,CenPgen,CenEgen` | deficit (unserved demand) per bus, non-zero rows only |
| `plpbar.csv` | `Hidro,Bloque,TipoEtapa,BarNum,BarNom,CMgBar,DemBarP,DemBarE,PerBarP,PerBarE,BarRetP,BarRetE` | marginal cost, demand, losses, withdrawal value |
| `plplin.csv` | `Hidro,Bloque,TipoEtapa,LinNum,LinNom,BarA,BarB,LinFluP,LinFluE,LinFluMax,LinUso,LinPerP,LinPerE,LinPer2P,LinPer2E,LinITP,LinITE` | flow, use %, losses, tariff income |
| `plpemb.csv` | `Hidro,Bloque,TipoEtapa,EmbNum,EmbNom,EmbFac,EmbVini,EmbVfin,EmbQgen,EmbQver,EmbQdef,EmbPsom,EmbPsom2,EmbAflu,EmbQFil,EmbQReb` | reservoirs: stage volumes, flows, water values |
| `plpess.csv` | `Hidro, Bloque, EssNom, Efin, EMin, EMax, DCar, DCmin, DCmax, DCMod, GDes, GDMin, GDMax, PGNet, EfPSom, EfPSom2` | batteries |
| `plpcostop.csv` | `Hidro, IBlo, CostoOperActual` | operating cost per block |
| `barras.csv`, `centrales.csv`, `lineas.csv` | `#Numero, Nombre, ...` | name tables |

Conventions:

- `Hidro` is `Sim  N` (`"Sim"` + `I3`), `N` the 1-based position of the
  gtopt scenario; the `MEDIA` rows that follow are the
  probability-weighted means (PLP's plain mean for equally likely
  scenarios), derived columns computed from the means as PLP does.
- `Bloque` is the global block, `TipoEtapa` the `NN Bloques` stage label
  of `plpeta.dat`.
- Units as PLP: power MW, energy GWh (`*E` = MW x h / 1000), `CenInyP`,
  `BarRetP`, `LinITP` in \$/h and their `*E` in k\$, `CenCostOp` k\$,
  flows m3/s, volumes in m3 / `EmbFac` with `EmbFac = 1e6` (hm3, the
  `Escala` of the `.dat` export), `CostoOperActual` in \$ (discounted).
- `CMgBar` is `Bus/balance_dual` unchanged: gtopt writes duals already
  back-scaled by `scale_objective`, probability, discount and block
  duration, i.e. the undiscounted \$/MWh price PLP prints (`CMg x FPhi`).
- `EmbPsom2` = `Reservoir/water_value_dual` [\$/hm3] / 1000 (\$/dam3, PLP's
  volume shadow price); `EmbPsom` = `EmbPsom2 x 3.6 / FactRendim` (\$/MWh,
  `FactRendim` = summed production factor of the plant and its
  downstream turbines).  `EfPSom` = `Battery/energy_dual` (\$/MWh),
  `EfPSom2 = EfPSom / 3.6`.
- `DemBarP` = served + unserved demand (PLP's bus demand, the deficit
  being the `plpfal` generation); `PerBarP` = `Line/loss_sol` shares by
  each line's `loss_allocation_mode`.
- Written as 0 (no gtopt counterpart): `EmbQdef` (PLP's fictitious
  inflow), `EmbQReb`, `LinPer2*`, `DCmin`, `GDMin`, `DCMod`.
- A single-bus case keeps its bus name (PLP would print `Uninodal`).
