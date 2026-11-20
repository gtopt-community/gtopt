# nrel118_to_gtopt

Converter that ports the **NREL-118 extended IEEE 118-bus test system**
into a **full-network, multi-block, multi-stage** gtopt case, plus the
tests that exercise gtopt's emission framework against this benchmark.

Full documentation: [`docs/scripts/nrel118_to_gtopt.md`](../../docs/scripts/nrel118_to_gtopt.md).

## Source

* **Paper**: Peña, I.; Martinez-Anido, C. B.; Hodge, B.-M. *An Extended
  IEEE 118-Bus Test System With High Renewable Penetration.*
  IEEE Transactions on Power Systems, Vol. 33, No. 1, January 2018,
  pp. 281-289.
  DOI: [10.1109/TPWRS.2017.2695963](https://doi.org/10.1109/TPWRS.2017.2695963)
* **Dataset**: `NREL-Sienna/PowerSystemsTestData`, `118-Bus/`
  directory —
  [github.com/NREL-Sienna/PowerSystemsTestData/tree/master/118-Bus](https://github.com/NREL-Sienna/PowerSystemsTestData/tree/master/118-Bus).
  Downloaded on demand at a pinned commit into `~/.cache/gtopt/nrel118/`
  (~26 MB).  The upstream repository has **no licence file**, so the
  data is never vendored here; the unit tests use a synthetic 3-bus /
  2-region dataset in the same CSV formats.

## What the converter builds

| Element | From | gtopt |
|---|---|---|
| 118 buses | `Buses.csv` | `bus_array` (uid = bus number, bus 69 reference) |
| 186 lines | `Lines.csv` | `line_array`: `reactance` p.u./100 MVA, `tmax_ab/ba` |
| 327 generators | `Generators.csv` | `generator_array` at their buses; `gcost = VO&M + HR × fuel price` |
| 91 bus loads | `LoadR{1,2,3}DA.csv` × `partfact.csv` | `Demand/lmax.parquet` |
| wind / solar / run-of-river hydro | `Wind*DA`, `Solar*DA`, `Hydro NN.csv` | `GeneratorProfile/profile.parquet` |
| energy-limited hydro 01-15 | `118-hydro.csv` monthly GWh | load-shaped hourly cap (profile) |
| hydro 36-39 | `Hydro_nondispatchable.csv` monthly MW | profile |
| CO₂ | IPCC AR6 factors (below) | `fuel_array` + `emission_array` + `emission_zone_array` |
| time | `--hours`, `--block-hours`, `--stage-hours` | `gtopt_shared.time_grid.TimeGrid` |

Default: the whole 8784-hour year, 1-hour blocks, daily stages (366 ×
24), one phase per stage, DC OPF with Kirchhoff, no unit commitment
(`--commitment relaxed|mip` adds ramps, min up/down and start costs).

### Heat rate and CO₂

The per-generator piecewise heat rate (Heat Rate Base + 5 incremental
bands) is collapsed to a scalar full-load average in GJ/MWh (gtopt's
`expand_fuel_emission_sources` requires a scalar heat rate; piecewise
support is tracked in
issue #510).
Per-fuel CO₂ factors come from IPCC AR6 Table A.III.2, kept inline
(study-specific, not added to `gtopt_shared/data/ipcc_emission_factors.json`):

| Fuel family | tCO₂/GJ | Source |
|---|---|---|
| Coal | 0.0946 | IPCC AR6 WG3 Table A.III.2 |
| Natural Gas (CC, CT, ICE, ST) | 0.0561 | IPCC AR6 WG3 Table A.III.2 |
| Oil / Diesel (CT Oil), ST Other | 0.0741 | IPCC AR6 WG3 Table A.III.2 |
| Biomass | 0.0 | Biogenic-zero per IPCC AFOLU |
| Geothermal / Wind / Solar / Hydro | 0.0 | No fuel combustion |

`Fuel.heat_content` is unset (heat rate already in GJ/MWh) and
`Fuel.price = 0` — the fuel cost is folded into `Generator.gcost` using
the dataset's own Sienna-loader prices (NG 5.4, coal 1.8, oil 21,
biomass 2.4 $/MMBtu; `--fuel-prices typical` for EIA-style prices).

## CLI

```bash
nrel118_to_gtopt -o nrel118/nrel118.json                 # whole year
nrel118_to_gtopt --hours 168+168 -o week2/nrel118.json    # one week
nrel118_to_gtopt --block-hours 4 --stage-hours 168 -o y/nrel118.json
nrel118_to_gtopt --single-bus --renewables-share 0.33 -o r33/nrel118.json
nrel118_to_gtopt --from-state nrel118/nrel118/nrel118_to_gtopt_state.json
```

(`PYTHONPATH=. python -m nrel118_to_gtopt …` from `scripts/` works too.)

## Tests

* `tests/test_converter.py` — unit tests on a synthetic mini dataset
  written by the test (3 buses, 2 regions, 48 h; CR line endings, BOM,
  over-capacity wind series, energy-limited and monthly hydro): readers,
  load distribution, profiles, hydro budgets, time grid, commitment,
  renewables share, CLI + `--from-state`, offline download logic.
* `-m integration` tests use the real cache (download if allowed) and,
  when a `gtopt` binary is found (`GTOPT_BIN`), solve one week at 0 %
  and 33 % wind+solar and require the CO₂ reduction in [15 %, 50 %]
  around the paper's annual 29-34 % (the reference itself is in
  `integration_test/cases/nrel118_reference/headline_results.json`).
* `test/source/test_emission_nrel118_port.cpp` — independent C++ check
  of the emission framework on a hand-built single-bus subset of the
  fleet (does not read converter output).

### Regression target

> *"At 33% wind+solar penetration, the system avoids 29-34% of CO₂
> emissions, 16-22% of NOₓ, and 14-24% of SO₂ vs. the baseline thermal
> mix."* — Peña et al. 2018, abstract.

## Known gaps

* Energy-limited hydro (`Hydro 01-15`): the monthly budget is pre-shaped
  into an hourly cap proportional to system load; the LP cannot shift
  that energy further (no multi-stage generator energy budget in gtopt).
* Reserves and the real-time (`RT`) series are not converted.
* `gen.csv`, `Interregional lines.csv`, `Escalators.csv` are unused.

## Caveat: surprise behavior

`expand_fuel_emission_sources` in `source/system.cpp` **skips fuels
with `combustion == 0 && upstream == 0`** — so biomass generators get
NO synthesized EmissionSource even when they declare a fuel +
heat_rate.  This is correct (zero-rate sources would be pure LP
overhead) but means the EmissionSource count is smaller than the
fueled-generator count.

## Related work

- `--emissions` flag and IPCC defaults: `gtopt_shared.emissions` and
  `gtopt_shared/data/ipcc_emission_factors.json`.
- Time grid shared with [pp2gtopt](../../docs/scripts/pp2gtopt.md):
  `gtopt_shared/time_grid.py`.
- Sister ports: `scripts/rts_gmlc_to_gtopt/` (GridMod RTS-GMLC),
  `scripts/sienna_to_gtopt/` (5-bus variants).
