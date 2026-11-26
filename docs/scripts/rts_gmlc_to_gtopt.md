# rts_gmlc_to_gtopt — RTS-GMLC test system → GTOpt single-day dispatch

Converts the **RTS-GMLC** benchmark (Reliability Test System — Grid
Modernization Lab Consortium, the 2019 update of the IEEE RTS-96;
[GridMod/RTS-GMLC](https://github.com/GridMod/RTS-GMLC)) into a
single-bus, 24-hour GTOpt dispatch case with **per-generator,
multi-pollutant emission factors** taken from the dataset's own
`gen.csv` columns (no IPCC overlay).  Optional reserve products and an
inertia requirement turn it into a reserve / inertia benchmark.

The source CSVs are downloaded on demand from the pinned release into
`~/.cache/gtopt/rts_gmlc/` and never committed.

Package README with the unit conversions and the C++ port tests:
[`scripts/rts_gmlc_to_gtopt/README.md`](../../scripts/rts_gmlc_to_gtopt/README.md).

---

## Invocation

There is no console script; the package is installed with the scripts
package and run as a module:

```bash
python -m rts_gmlc_to_gtopt --day 1 -o rts_gmlc.json
python -m rts_gmlc_to_gtopt --day 200 --timeseries --reserves \
  --inertia --inertia-requirement 20000 -o rts_d200.json
python -m rts_gmlc_to_gtopt --pollutants co2,so2,nox -o rts_multi.json
gtopt rts_gmlc.json --solver highs
```

## Options

| Option | Default | Meaning |
|--------|---------|---------|
| `--day N` | `1` | day index: scales the synthetic load shape (0.9–1.0), or the day of year (1–366, 2020) of the `--timeseries` series |
| `--pollutants LIST` | `co2` | comma-separated pollutants to track: `co2`, `so2`, `nox`, `ch4`, `n2o`, `co`, `vocs` |
| `-o, --output FILE` | `rts_gmlc.json` | output JSON |
| `--cache-dir DIR` | `~/.cache/gtopt/rts_gmlc` | cache of the source CSVs |
| `--no-download` | off | skip the download; the cache must be primed |
| `--timeseries` | off | use the day-ahead regional load (sum of the three areas) and reserve-requirement series of day `--day` instead of the synthetic load shape |
| `--reserves` | off | one `ReserveZone` per `reserves.csv` product and one `ReserveProvision` per eligible generator, `urmax` / `drmax = min(pmax, ramp × timeframe)`, `shared_headroom` |
| `--inertia` | off | one system `InertiaZone` and one `InertiaProvision` per unit with inertia constant H > 0 (`Inertia MJ/MW`, `Base MVA`; pmax when blank) |
| `--inertia-requirement MWS` | `0` | system inertia requirement [MW·s] (with `--inertia`) |
| `--shortfall-cost C` | `1000` | reserve / inertia shortfall price |
| `--source-version X` | detected | RTS-GMLC release to assume (supported and tested: `0.2.3`) |

## What is written

- One bus, one demand with an hourly profile, 24 one-hour blocks, one
  stage, one scenario.
- One generator per `gen.csv` unit: `gcost = VOM + heat rate × fuel
  price`; the piecewise heat-rate curve is collapsed to a pmax-weighted
  scalar.
- One fuel per emitting generator (price 0, already in `gcost`) with
  one `emission_factors` row per tracked pollutant (lb/MMBtu converted
  to t/GJ), plus one `Emission` and one global `EmissionZone` per
  pollutant.  Renewable and nuclear units carry no fuel.

The release is detected from a `SOURCE_VERSION` marker that a fresh
cache gets; a cache filled by hand is undetectable (see
[Supported source versions](../scripts-guide.md#supported-source-versions)).

## See also

- [`nrel118_to_gtopt`](nrel118_to_gtopt.md) — NREL-118 with per-fuel
  IPCC factors, full network.
- [`sienna2gtopt`](sienna2gtopt.md) — Sienna PowerSimulations.jl test
  cases, including reserve benchmarks.
