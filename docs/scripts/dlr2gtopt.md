# dlr2gtopt — dynamic line rating inputs (critical spans) → GTOpt `Line.dlr`

`dlr2gtopt` builds the inputs of GTOpt's dynamic line rating
([`Line.dlr`](../input-data.md#dynamic-line-rating-dlr)) from tower
coordinates and weather:

```text
towers -> spans -> per-span hourly weather -> IEEE 738 ampacity
       -> critical spans -> Line.dlr object + per-block schedules
```

The critical-span selection replicates Matus et al. (2012) [1]: the line
rating is the minimum of its span ratings, and spans are added greedily,
each time taking the span that raises the Pearson correlation ρ between
the estimate (the minimum over the chosen spans) and the true line
rating the most, until ρ reaches a target.  Span ratings follow IEEE
Std 738 [2] with the same conventions as GTOpt's own evaluator
(`include/gtopt/ieee738.hpp`).

The package README,
[`scripts/dlr2gtopt/README.md`](../../scripts/dlr2gtopt/README.md), has
the method details, the per-voltage line defaults, the data sources and
their licences, and the replication of the paper.

---

## Quick start

```bash
dlr2gtopt line-params --section-id 1352 -o line.json
dlr2gtopt towers   --source cen --section-id 1352 --line LVLP -o towers.csv
dlr2gtopt spans    towers.csv -o spans.csv
dlr2gtopt weather  --spans spans.csv --source explorador-eolico \
                   --line-params line.json -o weather.parquet
dlr2gtopt ampacity --spans spans.csv --weather weather.parquet \
                   --line-params line.json -o ampacity.parquet
dlr2gtopt critical-spans ampacity.parquet --target-rho 0.9 \
                   --plot trace.png -o critical.json
dlr2gtopt emit     --spans spans.csv --weather weather.parquet \
                   --critical critical.json --line-params line.json \
                   --block-hours 1 --mode weather -o case/
```

`emit` writes `case/dlr_line<uid>.json` (the `dlr` object to paste into
the line) and `case/Line/dlr_<field>.{parquet,csv}` in GTOpt's long
layout `scenario,stage,block,uid,value`, where `uid` is the span uid
(default `1000·line_uid + k`).

`python -m dlr2gtopt ...` works too.  `-v` / `--verbose` (before the
subcommand) turns on debug logging.

### How many critical spans

The count is **not fixed**: `critical-spans` adds spans until ρ reaches
`--target-rho` (default 0.9) and always records the full ρ(n) curve
(`trace`, and `n_needed` for ρ = 0.80–0.95), so the count of each line
or segment is read off its own curve.  `--max-spans N` is an optional
cap; with `--segments K`, `--segment-spans N` caps each segment the same
way.  Both are unset by default.

---

## Subcommands

### Shared download options

`line-params`, `towers` and `weather` download on demand:

| Option | Default | Meaning |
|--------|---------|---------|
| `--cache-dir DIR` | `~/.cache/gtopt/dlr2gtopt` | download cache (never commit it) |
| `--pause S` | `3.0` | seconds between requests to the same server |

### `line-params` — conductor, voltage and height of a line

Reads the CEN Infotécnica technical sheet of a section; fields it lacks
come from bundled per-voltage defaults.  The JSON records the source of
every field.

| Option | Meaning |
|--------|---------|
| `--section-id N` | CEN Infotécnica section id |
| `--ficha FILE` | a saved technical-sheet JSON instead of a download |
| `--kv KV` | voltage class when there is no sheet |
| `--line-defaults FILE` | per-kV defaults JSON (default: bundled `data/line_defaults.json`) |
| `--tmax C`, `--conductor-height M`, `--subconductors N` | override the design temperature, the mean conductor height, the conductors per phase |
| `-o FILE` | output JSON (required) |

### `towers` — tower coordinates

| Option | Meaning |
|--------|---------|
| `--source {cen,osm,manual}` | required: CEN structure-list annex, OpenStreetMap ways, or a CSV / KML file |
| `--line NAME` | required: line name written to the CSV |
| `--file FILE` | local structure list (`cen`) or CSV `line,tower_seq,lat,lon[,elevation]` / KML (`manual`) |
| `--section-id N`, `--search TEXT`, `--doc-id N` | CEN: section id, search sections by name, annex document id |
| `--circuit C` | keep one circuit (e.g. `C1`) |
| `--utm-zone Z` | UTM zone (south) of E/N coordinates |
| `--way-id ID` (repeatable), `--bbox S W N E` | OSM ways, or a bounding box |
| `--voltage-regex RE`, `--name-regex RE` | OSM tag filters (e.g. `220000`) |
| `--towers-only` | OSM: keep only nodes tagged `power=tower` / `pole` |
| `-o FILE` | output CSV (required) |

### `spans` — spans from towers

`dlr2gtopt spans TOWERS.csv -o spans.csv` computes span midpoints,
azimuths and lengths.  `--line NAME` names the line when the CSV has no
`line` column; `--min-length M` (default 1 m) merges towers closer than
that.

### `weather` — per-span hourly weather

| Option | Default | Meaning |
|--------|---------|---------|
| `--spans FILE` | required | spans CSV |
| `--critical FILE` | all spans | only the spans of a `critical-spans` JSON |
| `--source` | required | `explorador-eolico`, `open-meteo`, `ecmwf-opendata`, `csv`; `era5` is accepted but not implemented (use `csv`) |
| `--interp {bilinear,nearest}` | `bilinear` | grid interpolation to the span midpoint |
| `--height M` | `--line-params`, else 15 | conductor height |
| `--line-params FILE` | — | `line-params` JSON (conductor height) |
| `--wind-profile {log,power,none}` | `log` | vertical wind interpolation |
| `--model NAME` | source default | Explorador: `wrf2010`; Open-Meteo: `best_match`, `ecmwf_ifs025`, `gfs_seamless`, `icon_seamless` |
| `--forecast-days N`, `--past-days N` | 3, 0 | Open-Meteo horizon |
| `--api-key KEY`, `--base-url URL` | — | Open-Meteo customer key (commercial use) or a self-hosted server |
| `--batch N` | 50 | Open-Meteo points per request |
| `--grib FILE` (repeatable) | — | ECMWF open-data GRIB2 files (needs `cfgrib`) |
| `--file FILE` | — | `csv` source: a long weather CSV |
| `--correction {none}` | `none` | hook for a per-span statistical correction; only `none` exists |
| `-o FILE` | required | `.parquet` or `.csv` |

### `fetch-ecmwf` — archive the latest ECMWF open-data run

| Option | Default | Meaning |
|--------|---------|---------|
| `--archive DIR` | required | archive directory |
| `--model {ifs,aifs-single}` | `ifs` | model |
| `--source {ecmwf,aws,azure,google}` | `ecmwf` | download mirror |
| `--max-step H`, `--step H` | 144, 3 | forecast steps |

Needs the optional `ecmwf-opendata` package.  Only the latest few runs
stay online, so run it on a schedule to build a history.

### `ampacity` — hourly IEEE 738 ampacity per span

`dlr2gtopt ampacity --spans spans.csv --weather W -o ampacity.parquet`
plus the conductor options:

| Option | Default | Meaning |
|--------|---------|---------|
| `--conductor {drake,flint}` | — | named conductor |
| `--diameter`, `--r20`, `--alpha-r`, `--ac-factor` | — | own conductor: diameter [m], DC resistance at 20 °C [Ω/m], its temperature coefficient [1/K], AC/DC ratio |
| `--r25`, `--r75` | — | or AC resistance at 25 / 75 °C [Ω/m] |
| `--emissivity`, `--absorptivity`, `--subconductors` | conductor / line defaults | surface coefficients, conductors per phase |
| `--tmax C` | `--line-params`, else 75 | maximum conductor temperature |
| `--line-params FILE` | — | `line-params` JSON; explicit conductor options override it |
| `--solar-mode {ghi,none}` | `ghi` | absorbed solar heat `α·GHI·D` (as GTOpt), or none |
| `--low-wind-speed V`, `--low-wind-angle DEG` | 0.5 m/s, 45° | below `V` the attack angle is `DEG` (`0` disables the rule) |

### `critical-spans` — critical-span selection

`dlr2gtopt critical-spans AMPACITY -o critical.json`

| Option | Default | Meaning |
|--------|---------|---------|
| `--target-rho R` | 0.9 | stop when ρ reaches `R` |
| `--max-spans N` | no cap | optional cap (`1` = a single representative span) |
| `--months LIST` | all | restrict the fit to some months, e.g. `1,4,7,10` (the paper's) |
| `--segments N` | — | also select spans in each of `N` equal segments |
| `--segment-spans N` | no cap | optional per-segment cap with `--segments` |
| `--trace-rho R`, `--trace-max N` | 0.99, 50 | the ρ(n) trace runs to this ρ or this many spans |
| `--plot FILE` | — | PNG of the ρ(n) trace (needs matplotlib) |
| `--eta-quantile Q` | off | report η = the `Q`-quantile of truth / estimate (an optional safety margin; GTOpt's `safety_factor` stays 1 unless set) |

### `emit` — `Line.dlr` JSON and schedules

| Option | Default | Meaning |
|--------|---------|---------|
| `--spans FILE`, `--weather FILE` | required | inputs |
| `--critical FILE` | all spans | `critical-spans` JSON |
| `--mode {weather,rating}` | `weather` | write weather schedules (GTOpt runs IEEE 738) or precomputed ratings [MW] |
| `--block-hours H` | 1 | uniform blocks of `H` hours |
| `--blocks FILE` | — | block layout CSV `[stage,]block,hours` instead |
| `--weather-agg {min-rating,mean}` | `min-rating` | per block: the weather of the block's lowest-rating hour, or the mean (direction through u/v) |
| `--rating-agg {min,mean}` | `min` | per block, `rating` mode |
| `--voltage KV` | `--line-params`, else 220 | line voltage |
| `--v-pu`, `--power-factor`, `--safety-factor` | 1, 1, 1 | written to the `dlr` object (`safety_factor` 1 = off) |
| `--line-uid N`, `--uid-base N` | 1, `1000·uid` | line uid; span uid base |
| `--prefix P`, `--format {parquet,csv}` | `dlr`, `parquet` | schedule file names and format |
| `-o DIR` | required | case directory |

plus the conductor options of `ampacity`.  When the weather has no solar
irradiance, `emit` writes `solar_mode: none` with a warning.

---

## Data sources

No downloaded data is stored in the repository; every client downloads
into `--cache-dir`.  Sources: CEN Infotécnica (line sections, technical
sheets, structure lists), OpenStreetMap via Overpass (ODbL, attribution
required), Explorador Eólico (WRF 1 km hourly hindcast of 2010),
Open-Meteo (CC BY 4.0; the free endpoint is non-commercial) and ECMWF
open data (CC BY 4.0).  Terms and attribution for each are in the
[package README](../../scripts/dlr2gtopt/README.md#data-sources-and-licences).

## References

1. M. Matus et al., "Identification of Critical Spans for Monitoring
   Systems in Dynamic Thermal Rating", *IEEE Trans. Power Delivery*
   27(2):1002–1009, 2012.
   [doi:10.1109/TPWRD.2012.2185254](https://doi.org/10.1109/TPWRD.2012.2185254)
2. IEEE Std 738-2012, "IEEE Standard for Calculating the
   Current-Temperature Relationship of Bare Overhead Conductors".
   [doi:10.1109/IEEESTD.2013.6692858](https://doi.org/10.1109/IEEESTD.2013.6692858)

## See also

- [Dynamic line rating input](../input-data.md#dynamic-line-rating-dlr) —
  the `Line.dlr` fields `emit` fills.
- `model_options.use_line_dlr` — global switch to ignore every
  `Line.dlr` block (static-vs-DLR comparisons).
