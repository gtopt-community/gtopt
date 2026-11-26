# dlr2gtopt

Dynamic line rating (DLR) inputs for gtopt's `Line.dlr`: tower
coordinates → spans → per-span hourly weather → IEEE 738 ampacity →
**critical spans** → the `dlr` object of a line, with per-block weather or
rating schedules.

The critical-span method replicates Matus et al. (2012) [1]; the ratings
follow IEEE Std 738 [2].
The line rating is the minimum of its span ratings. The method picks the
spans whose minimum estimates that line rating with the smallest
estimation error. The quality is measured by the Pearson correlation ρ
between the estimate and the true line rating (the paper's confidence
level). Spans are added greedily, one at a time, each time taking the span
that raises ρ the most.

```bash
dlr2gtopt towers   --source cen --section-id 1352 --line LVLP -o towers.csv
dlr2gtopt spans    towers.csv -o spans.csv
dlr2gtopt weather  --spans spans.csv --source explorador-eolico -o weather.parquet
dlr2gtopt ampacity --spans spans.csv --weather weather.parquet \
                   --conductor flint --tmax 55 -o ampacity.parquet
dlr2gtopt critical-spans ampacity.parquet --target-rho 0.9 \
                   --plot trace.png -o critical.json
dlr2gtopt emit     --spans spans.csv --weather weather.parquet \
                   --critical critical.json --conductor flint --tmax 55 \
                   --voltage 220 --block-hours 1 --mode weather -o case/
```

`emit` writes `case/dlr_line<uid>.json` (the `dlr` object to paste into the
Line) and `case/Line/dlr_<field>.{parquet,csv}` in gtopt's long layout
`scenario,stage,block,uid,value`, where `uid` is the span uid (default
`1000·line_uid + k`).

## How many critical spans per segment

The number of spans is **not fixed**. `critical-spans` adds spans until
the estimate reaches `--target-rho` (default 0.9) and always writes the
full ρ(n) curve (`trace`, `n_needed` for ρ = 0.80–0.95, `--plot`), so the
count for each segment is read off its own curve. `--max-spans N` is only
an optional cap, set per segment (one `critical-spans` run per
substation-to-substation segment) where monitoring or model size limits
the count; with `--segments K`, `--segment-spans N` caps each piece the
same way and is likewise unset by default.

A cap of 3 spans per segment was used only as an **initial sizing
estimate** for a national roll-out (how many weather points and gtopt
schedules to expect), not as a recommended or default value. On the
sampled lines the count for ρ = 0.9 ranged from 2 (short, homogeneous
segments) to 8–9 (Los Vilos – Las Palmas and similar inland routes), and
long 500 kV routes extrapolate to 25 or more.

## Subcommands and options

Every behaviour has an option. The defaults are listed below.

| Step | Option | Values (default first) |
|------|--------|------------------------|
| `towers` | `--source` | `cen` (Infotécnica annex or `--file`), `osm` (`--way-id`/`--bbox`), `manual` (CSV `line,tower_seq,lat,lon[,elevation]` or KML) |
| `towers` | `--circuit`, `--utm-zone`, `--towers-only` | keep one circuit; E/N coordinates; OSM vertices tagged `power=tower` only |
| `spans` | `--min-length` | merge towers closer than 1 m |
| `spans` | `--heights` | `none` (default), `derive` (per-span effective height; `--geometry`, `--phase`, `--kv`/`--line-params`, `--midspan-ground`, `--heights-detail`) |
| `weather` | `--source` | `explorador-eolico`, `open-meteo`, `ecmwf-opendata`, `csv`, `era5` (not implemented: use `open-meteo --model era5 --start-date …` or `csv`) |
| `weather` | `--interp` | `bilinear`, `nearest` (grid sources) |
| `weather` | `--wind-profile` | `log` (log law through two levels), `power` (fitted exponent, clipped to [−0.5, 1]), `none` |
| `weather` | `--height` | conductor height, 15 m |
| `weather` | `--height-source` | `scalar` (one height for all spans), `spans` (the spans CSV `height` column; blank cells use the scalar) |
| `weather` | `--correction` | `none` (the hook for a per-span statistical correction; see below) |
| `ampacity`, `emit` | `--conductor` / `--diameter --r20 --alpha-r --ac-factor` or `--r25 --r75`, `--emissivity --absorptivity --subconductors` | named `drake`, `flint` or your own |
| `ampacity`, `emit` | `--tmax` | max conductor temperature, 75 °C |
| `ampacity`, `emit` | `--solar-mode` | `ghi` (q_s = α·GHI·D, as in gtopt), `none` |
| `ampacity`, `emit` | `--low-wind-speed`, `--low-wind-angle` | 0.5 m/s, 45° (0 disables the rule) |
| `critical-spans` | `--target-rho`, `--max-spans` | 0.9, no cap: the target ρ decides the count (`--max-spans N` = optional cap, `1` = the single representative span) |
| `critical-spans` | `--trace-rho`, `--trace-max`, `--plot` | the full ρ(n) trace (always in the JSON) runs to ρ 0.99 or 50 spans; `--plot` writes a PNG (needs matplotlib) |
| `critical-spans` | `--segments N`, `--segment-spans K` | also select spans in each of N equal segments until `--target-rho` (optional cap K, unset by default), with the count and error of each segment |
| `critical-spans` | `--eta-quantile q` | off. When given, it reports η = the q-quantile of true/estimate, an optional safety margin. gtopt's `safety_factor` stays 1 unless you set it |
| `critical-spans` | `--months` | restrict the fit to some months (e.g. `1,4,7,10` as in the paper) |
| `emit` | `--mode` | `weather` (gtopt runs IEEE 738), `rating` (precomputed MW) |
| `emit` | `--block-hours` / `--blocks` | uniform blocks, or a CSV `[stage,]block,hours` |
| `emit` | `--weather-agg` | `min-rating` (the weather of the block's lowest-rating hour, so gtopt reproduces the block minimum), `mean` (direction through u/v) |
| `emit` | `--rating-agg` | `min`, `mean` |
| `emit` | `--voltage --v-pu --power-factor --safety-factor --line-uid --uid-base --prefix --format` | 220 kV, 1, 1, 1, 1, `1000·uid`, `dlr`, `parquet` |

The IEEE 738 evaluator uses the same conventions as gtopt's C++ evaluator
(`include/gtopt/ieee738.hpp`). These are: the attack angle
`φ = |((dir − azimuth + 90) mod 180) − 90|`, the low-wind rule, `K_angle`
applied to forced convection only, GHI used as absorbed flux, and `R(T)`
linear either from `r25`/`r75` or from `ac_factor·r20·(1 + α_R (T − 20))`
(the CEN technical-sheet form). It reproduces the IEEE 738 Drake example
(1025 A) and the CEN static rating of AAAC Flint (587 A) to within 1 %.

## Line parameters: CEN data first, then documented defaults

`dlr2gtopt line-params --section-id N -o line.json` reads the CEN
Infotécnica technical sheet of a section ("ficha técnica general"):
voltage, conductor type, section, conductors per phase, R20 and the
design temperature. Fields the sheet lacks come from the per-voltage-class
table `data/line_defaults.json`, which can be replaced with
`--line-defaults FILE` and overridden field by field (`--tmax`,
`--conductor-height`, `--subconductors`). Pass `--line-params line.json`
to `weather` (conductor height for the vertical wind interpolation),
`ampacity` and `emit`. Explicit conductor options still win.

The JSON records the source of every field (`cen`, `conductor-library`,
`estimated`, `default`, `option`), so a report can flag the lines that
rest on assumptions. The diameter comes from a library of named
conductors (Flint, Greeley, Drake, Rail, ...), or else is estimated from
the section with `D ≈ 1.15·sqrt(4A/π)`. CEN gives no conductor height, so
it always comes from the table.

| Class | Conductor (default) | Per phase | Design T | Mean conductor height | Tower |
|-------|---------------------|-----------|----------|-----------------------|-------|
| 220 kV | AAAC 402.8 mm² (D 26.0 mm, R20 0.0729 Ω/km) | 1 | 75 °C | 16 m | lattice, 1 or 2 circuits, 30–40 m |
| 500 kV | ACAR 700 MCM (D 24.5 mm, R20 0.088 Ω/km each) | 4 | 65 °C | 24 m | lattice, 40–50 m |

The conductor and temperature defaults are the medians of the certified
CEN sheets of the in-service 220 kV (314) and 500 kV (17) C1 sections.
CEN fills these fields for about 96 % of them, so the defaults are rarely
needed for conductor or temperature. The heights are typical values: the
attachment height minus about 2/3 of the sag at design temperature,
consistent with the clearances of SEC Pliego Técnico RPTD N°07. The
sources are listed in the JSON. ε = α = 0.5 reproduces CEN's AAAC thermal
tables. IEEE 738 suggests higher values for aged conductors; set them
with `--emissivity` / `--absorptivity`.

The conductor height can also vary along the line. Add an optional `height`
column (effective height above ground, m) to the spans CSV and run
`weather --height-source spans`. Spans with an empty cell use the scalar
height. The default `--height-source scalar` ignores the column and keeps
the old behaviour.

`spans --heights derive` fills that column:
`h_eff = (h_a + h_b)/2 − (2/3)·sag`, where `h_a` and `h_b` are the attachment
heights at the span's two towers. `--heights-detail FILE` writes the
breakdown and the source used for every span. The attachment height of
each tower comes from the first source that has it:

| `src` | Attachment |
|-------|------------|
| `list` | the towers CSV `attachment` column |
| `memo` | `--geometry` JSON (design report / memoria de cálculo): cross-arm height of `--phase` (`low`, `mid` default, `high`) for the tower's body extension, parsed from the `type` column (`22AD+3` → family AD, +3 m), minus the suspension string |
| `memo_family` | as `memo`, for another suspension family (same cross-arms assumed) |
| `memo_strain` | strain / dead-end family: cross-arm height, no string drop |
| `memo_weighted` | the report's structure-weighted cross-arm height (type unknown) |
| `class` | `attachment_height` of the voltage class (24 m at 220 kV, 32.5 m at 500 kV) |

The sag of each span comes from the first available source:

- the report's sag at the ruling span, `F·(L/RS)²` with
  `RS = sqrt(ΣL³/ΣL)` (`ruling_sag_m`);
- a catenary parameter, `L²/(8C)` (`catenary_m`);
- the class sag at the ruling span.

With no data at all, every span gets the class mean height.

With tower elevations in the towers CSV and `--midspan-ground` (CSV
`line,span,elevation`, e.g. from SRTM), the height is taken above the
mid-span ground rather than flat terrain. It is bounded to
`[flat − 10, flat + 25]` m and floored at 5 m.

```json
{"crossarm": {"-3": [16.63, 21.63, 26.63], "0": [19.58, 24.58, 29.58]},
 "reference_family": "AD",
 "families": {"AD": "suspension", "BD": "suspension", "CD": "strain"},
 "string_m": 2.198, "weighted": [18.51, 23.51, 28.51],
 "ruling_sag_m": 9.31, "source": "memoria de calculo, doc ..."}
```

## Interpolation

Weather is interpolated to each span midpoint, as in the paper. Grid
sources go through the four surrounding grid nodes (`bilinear`); each node
is first brought to conductor height. Wind direction is interpolated
through its u/v components, so 350° and 10° average to 0°, not 180°. Wind
speed is interpolated as a scalar. Grid nodes shared by several spans are
downloaded once.

## Data sources and licences

No downloaded data are stored in this repository. Every client downloads
on demand into `--cache-dir` (default `~/.cache/gtopt/dlr2gtopt`), with
`--pause` seconds (default 3) between requests to the same server; a dropped
connection, timeout or HTTP 429/5xx is retried `--retries` times (default 3)
with a growing wait (30 s, 60 s, 120 s).
Reuse the cache rather than re-downloading.

- **CEN Infotécnica** (Coordinador Eléctrico Nacional, Chile):
  <https://infotecnica.coordinador.cl>, public REST API
  `https://api-infotecnica.coordinador.cl/v1`, no login. Line sections
  (`secciones-tramos`), technical sheets (conductor, design temperature,
  thermal limits) and annexes. The annex "Listado de distribución de
  estructuras" holds the coordinates of every structure, as PDF, XLSX or
  XLS (archives are not read). Link to the source; do not redistribute
  the files.
- **OpenStreetMap** via Overpass (`https://overpass-api.de`): `power=line`
  ways, whose vertices are towers on tower-traced lines. Data © OpenStreetMap
  contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/).
  Attribution is required wherever derived spans are published. Keep
  queries small and infrequent.
- **Manual tracing**: CSV or KML. Trace only on imagery whose terms allow
  it. Google Maps / Google imagery must not be scraped or bulk-extracted.
- **Explorador Eólico** (Ministerio de Energía / Geofísica, Universidad de
  Chile): <https://eolico.minenergia.cl>, the WRF 1 km hourly hindcast of
  2010. Its nodes sit at multiples of 0.01° in latitude and longitude
  (found by bisecting the server's nearest-node snapping). The series are
  in Chilean standard time (UTC−4) and are converted to UTC. Variables:
  wind speed and direction at 10/20 m, temperature at 10/20 m, GHI. The
  site states no licence; cite it and do not redistribute the series. It
  is a **hindcast**, not a forecast. It describes how weather varies in
  space along a line, which is what the critical-span selection needs. It
  carries no forecast error.
- **Open-Meteo** (`--source open-meteo`): point API, several points per
  request (`--batch`). It returns temperature at 2/80 m, wind at 10/80 m
  and shortwave radiation (GHI). `--model` is `best_match`,
  `ecmwf_ifs025`, `gfs_seamless` or `icon_seamless`. Data are
  [CC BY 4.0](https://open-meteo.com/en/license) ("Weather data by
  Open-Meteo.com"). **The free endpoint is for non-commercial use only**
  (≤ 10 000 calls/day). For commercial use pass `--api-key` (customer
  endpoint), or run your own Open-Meteo server (AGPL) and pass
  `--base-url`. Open-Meteo picks the model cell itself, so `--interp` does
  not apply.
  With `--start-date`/`--end-date` the historical archive API is used
  instead (temperature at 2 m, wind at 10/100 m, GHI; `--model` `era5`,
  `era5_land`, `era5_seamless`, `ecmwf_ifs` or `best_match`), e.g. to rate
  a line over the same years as its historical flows. The archive charges
  long requests per point and per two weeks of data, so `--snap 0.25`
  (the ERA5 grid) rounds the span midpoints and requests each distinct
  point once.
- **ECMWF open data** (`--source ecmwf-opendata`): IFS / AIFS GRIB2 at
  0.25°, [CC BY 4.0](https://www.ecmwf.int/en/forecasts/datasets/open-data),
  commercial use allowed ("Contains ECMWF open data"). Download with
  `dlr2gtopt fetch-ecmwf --archive DIR`, which uses the official
  `ecmwf-opendata` client (`pip install ecmwf-opendata`; `--source`
  `ecmwf|aws|azure|google`). Only the latest few runs stay online, so run
  it on a schedule to build a history. `weather --grib FILE` reads the
  files with `cfgrib` (needs eccodes) and interpolates bilinearly, through
  the same code as the Explorador. It reads 10u/10v, 100u/100v, 2t and
  ssrd (de-accumulated to W/m²). If a file has no ssrd, the weather has no
  `solar_irradiance` and `emit` sets `solar_mode: none` (with a warning).
  Both packages are optional, and their tests use mocks.

## Per-span correction (hook)

`--correction none` is the only value today. A coarse forecast (Open-Meteo,
ECMWF at about 25 km) misses the local wind and temperature differences
along a line that the 1 km Explorador resolves. A correction would be
fitted per span from the Explorador history: the 1 km bilinear series
against the same history smoothed to the coarse grid. For example, a
wind-speed factor by direction sector × hour, a direction offset, and
additive temperature and GHI-ratio biases by hour × season. The fitted
correction is then applied to the forecast at each critical span. To add
one, implement it in `apply_correction` (`_weather.py`) behind a new
`--correction` value, and store the fitted parameters per span in a small
JSON file. Because the Explorador is a hindcast, such a correction fixes
the representativeness of a coarse grid, not forecast error. Forecast-error
quantiles need an archive of forecasts against observations (for example,
`fetch-ecmwf --archive` against monitoring data).

## Long-term studies: typical meteorological years

A 20-year SDDP study has no weather forecast. The weather is built for the
critical spans of Matus et al. (2012) [1]: the spans whose minimum rating
estimates the line rating. gtopt computes each critical
span's rating itself (IEEE 738 in `Line.dlr`), so what a long-term case
needs is **weather**: per-span `ambient_temperature`, `wind_speed`,
`wind_direction` and `solar_irradiance` schedules. The main product is a
**typical year at hourly resolution (8760 h)** of the weather of every
critical span, in two cases. It is then mapped to the case's blocks and
tiled over the horizon.

```bash
# 1. a 20-year per-span history (recon wind + ERA5 T/GHI, WRF-calibrated)
dlr2gtopt history  --spans spans.csv --critical critical.json --years 1998-2017 \
                   --wind recon --calibrate wrf --calib-weather wrf2010.parquet \
                   --height 16 -o history.parquet
# 2. the typical years (mean and P90 in one run) + validation and plots
dlr2gtopt typical-year --history history.parquet --spans spans.csv \
                   --critical critical.json --conductor flint --tmax 55 \
                   --case mean --case p90 \
                   --test-years 2013-2017 --static-mw 224 --plot -o typ/
# 3a. load blocks with known hour membership (plp2gtopt BlockHourMap)
dlr2gtopt to-blocks typ/typical_p90.parquet --spans spans.csv \
                   --conductor flint --tmax 55 \
                   --block-map case/BlockHourMap/block_hour_map.parquet \
                   --planning case/case.json -o case_p90/
# 3b. or a month x hour-band template, tiled over 240 monthly stages
dlr2gtopt to-blocks typ/typical_p90.parquet --spans spans.csv --conductor flint \
                   --tmax 55 --hour-bands 1-7,8-18,19-24 -o tpl/
dlr2gtopt repeat   tpl/dlr_blocks.parquet --spans spans.csv --conductor flint \
                   --tmax 55 --first-month 1 --stages 240 -o case_p90/
```

### Cases: mean and P90

**Default: P90.** `--case` is repeatable; without it both P90 and the mean
are built in one run and written side by side (`typical_p90.*`,
`typical_mean.*`), so a study can run both and compare.

| `--case` | Meaning |
|----------|---------|
| `mean` | the expected rating |
| `p90` (**default**) | **P90 = the rating exceeded 90 % of the time = the 10th percentile of the line rating.** The line is at least that strong in 90 % of the hours: the critical, conservative case |
| `pNN` | exceeded NN % of the time (quantile `1 − NN/100`); `p50` is the median |
| `quantile:<q>` | the q-quantile of the rating (`quantile:0.1` = `p90`) |
| `min` | the minimum |

Statistics are always taken on the **line rating per hour, after the
minimum over the critical spans** (computed with the same IEEE 738 code as
gtopt), never per span or per weather variable. Weather variables are
never averaged or quantiled independently: the rating is a nonlinear
function of wind angle, wind speed, temperature and sun, and the line is
limited by its weakest span, so per-variable statistics do not reproduce
the rating statistic and break the correlation between spans.

### Why P90 is the default

The rating a planner uses must hold in the hours that matter; an
optimistic rating that the conductor cannot carry is a safety problem,
while a pessimistic one only costs money. Industry practice for ratings
that depend on weather is therefore a **high-exceedance (low-quantile)
value**, never the expected value:

- **Static ratings** (IEEE Std 738 is the calculation method; it
  explicitly does not prescribe the weather) are computed with
  conservative weather: typically 0.6 m/s (2 ft/s) perpendicular wind,
  full sun and a high (e.g. 40 °C) summer ambient. CIGRE TB 299 (2006,
  WG B2.12, joint CIGRE/IEEE guidance) recommends conservative "base
  rating" weather, e.g. 0.6 m/s, when no field data exist
  ([TB 299](https://www.e-cigre.org/publications/detail/299-guide-for-the-selection-of-weather-parameters-for-bare-overhead-conductor-ratings.html),
  [T&D World, "Standards improve line ratings"](https://www.tdworld.com/overhead-transmission/article/20964181/standards-improve-line-ratings),
  [IEEE 738](https://standards.ieee.org/ieee/738-2012_Cor_1/5715)).
  These assumptions are exceeded in the large majority of hours: a
  static rating is, implicitly, a very-high-exceedance rating.
- **Ambient-adjusted ratings, FERC Order 881** (2021, US): hourly
  ratings from the forecast ambient temperature plus day/night solar,
  forecast 10 days ahead; wind stays at the conservative static
  assumption ([Federal Register 87 FR 2244](https://www.govinfo.gov/content/pkg/FR-2022-01-13/html/2021-27735.htm);
  [CAISO white paper](https://stakeholdercenter.caiso.com/InitiativeDocuments/TechnicalWhitePaper-FERCOrder881-ManagingTransmissionLineRatingsPhase2.pdf);
  PJM implemented it first, hourly by 10-day temperature forecasts:
  [PJM](https://insidelines.pjm.com/pjm-becomes-first-rto-to-implement-ambient-air-ratings-for-transmission-under-ferc-order-881/)).
  Only the variable that is forecast well (temperature) is relaxed.
- **Elia (Belgium)**: day-ahead / two-day DLR forecasts with a confidence
  level of at least **98 %** (the forecast is below the real-time rating
  98 % of the time), "as with conventional static ratings", capped at
  130 % of the seasonal rating on interconnectors; Elia staff cite a
  99.9 % risk level for market use
  ([Elia DLR](https://www.elia.be/infrastructure-and-projects/our-infrastructure/dynamic-line-rating),
  [T&D World, "Dynamic ratings increase efficiency"](https://www.tdworld.com/overhead-transmission/article/20969403/dynamic-ratings-increase-efficiency),
  [ENTSO-E 2019 DLR workshop](https://eepublicdownloads.entsoe.eu/clean-documents/events/2019/190320%20DLR%20presentations.pdf)).
  RTE operates the shared lines with static (seasonal) limits on its
  side; RTE-related research selects the day-ahead quantile by a
  cost-benefit of risk, typically low quantiles
  ([Dupin et al., optimal quantile](https://hal.archives-ouvertes.fr/hal-01398440)).
- **CIGRE TB 498** (real-time monitoring) and **TB 969** (2025,
  forecasting dynamic thermal ratings, probabilistic / ensemble forecasts)
  frame forecast ratings as quantiles chosen by the operator's risk
  policy ([TB 498](https://www.e-cigre.org/publications/detail/498-guide-for-application-of-direct-real-time-monitoring-systems.html),
  [TB 969](https://www.e-cigre.org/publications/detail/969-forecasting-dynamic-thermal-line-ratings.html)).

A long-term study has no forecast at all, so its weather uncertainty is
the full climatological spread, larger than a day-ahead forecast error.
Operators accept 2 % (Elia) or less hourly risk for forecasts. In a plan,
P90 trades 10 % of hours below the typical rating for most of the DLR
gain: on Los Vilos – Las Palmas (AAAC Flint, 55 °C, 1998-2017) the P90
year averages 266 MW against a 224 MW static rating (+19 %), P95 253 MW
(+13 %), P98 242 MW (+8 %), and the mean 319 MW (+43 %). Hence:

- **P90 is the default** for limits (adequacy, congestion, expansion
  need): conservative, and close to how operators will actually release
  DLR capacity. Use `p95` or `p98` to match a TSO's forecast confidence.
- **mean / p50** are options for expected-value economics (energy
  transferred, curtailment, revenues), always reported next to P90.

### Consistency with renewable typical years (wind cooling)

Wind generation and line rating share the weather: windy hours raise both
the wind output and the rating (convective cooling), which is exactly
when a wind-export corridor such as Los Vilos - Las Palmas needs it.
Renewable typical years default to **P50 with the expected energy
preserved**, while the DLR default is **P90**. Two independently built
years break this coincidence. Options:

| Approach | How | Effect |
|----------|-----|--------|
| independent (default) | DLR P90 analogue year, RE P50 year | conservative for both; the wind-cooling coincidence is lost, which underestimates the transfer capacity in windy hours (safe side) |
| aligned, exact | `--source-hours typical_re_p50.parquet --align exact` | the DLR weather of the very same historical hours as the RE year: fully consistent, but the DLR is then a P50-like rating (not conservative) |
| aligned, conditional (**recommended** when the corridor exports wind) | `--source-hours ... --align conditional --condition-k 60` | for each hour, among the historical hours of the same month and hour of day with the closest span-mean wind speed to the RE year's source hour, the P90 of their line rating and the weather of the hour that has it: P90 conservatism **given** the wind situation, so windy RE hours keep their higher (but still P90) rating |

`--source-hours` reads any table with `hour_of_year` and `source_time`
(UTC), such as another `typical_<case>` file. Both years then come from
the same (exact) or the same-wind (conditional) historical hours.

### How the typical years are built

Every output hour takes the weather of **all critical spans from one
historical hour** (or, at a blended month boundary, from the same two
hours with the same weights). The file records it in `source_time`.

| Case | `--mean-method` / `--p90-method` | Method |
|------|----------------------------------|--------|
| mean | `tmy` (default) | Sandia / NREL TMY month selection. For each calendar month, the Finkelstein–Schafer statistic compares each historical month's daily indices with the long-term distribution: daily mean and minimum line rating, mean wind speed and temperature, weights `--fs-weights` (default `rating_mean=0.4,rating_min=0.4,wind_speed=0.1,temperature=0.1`). Among the `--tmy-candidates` best (default 5), the month whose mean rating is closest to the long-term mean wins. The 12 real months are concatenated, which keeps the diurnal cycle and multi-day persistence. Month boundaries are blended over `--blend-hours` on each side (default 6, as TMY3; `0` = off) |
| mean | `analogue` | as the P90 analogue, with the mean as target |
| P90 / quantiles | `analogue` (default, **recommended**) | for each month × hour of day, the target is the 10th percentile (the case quantile) of the historical line ratings of that bin. Each day's hour takes the weather of a historical hour of the same month and hour of day whose line rating is closest to the target (`--selection`) |
| P90 / quantiles | `month` | for each calendar month, the real historical month whose monthly-mean line rating is at the 10th percentile of the monthly means |

Trade-off of `--p90-method`: `analogue` hits the hourly P90 per month × hour
exactly (exceedance ≈ 0.90), which is what a conservative limit needs.
Consecutive hours, however, come from different days, so it has no
weather persistence. `month` is a real, temporally coherent month (useful
for chronological studies with storage or unit commitment), but it is P90
only for the monthly mean: hour by hour it is barely conservative (on
Los Vilos – Las Palmas its hourly exceedance is 0.57 and it over-estimates
the actual rating in 45 % of the hold-out hours, against 12 % for
`analogue`). For limits in long-term studies use `analogue`.

`--selection` for the analogue methods (and inside blocks):

| Value | Behaviour |
|-------|-----------|
| `nearest` (default) | the single historical hour whose rating is closest to the target; all days of the month get it |
| `rotate` | day d gets the d-th closest of the `--analogues` K closest hours: day-to-day variety around the target |
| `knn-mean` | the average weather of the K closest hours (direction through u/v), accepted only if its recomputed rating is within `--tolerance` (relative, default 0.02) of the target; otherwise `nearest` (counted in `knn_fallbacks`) |

### Outputs of `typical-year`

- `typical_<case>.{parquet,csv}`: one row per (hour, span) with
  `hour_of_year` (1–8760), `month`, `day`, `hour` (1–24, PLP convention:
  hour 1 = 00:00–01:00 local standard time), `line`, `span`, the four
  weather fields, `span_rating_mw`, `line_rating_mw` (recomputed from the
  emitted weather), `source_time` (UTC), `blend` and `target_mw`.
  With `--source-columns on` (default) it also has `source_time_2`, the
  secondary UTC hour of a blended month seam (empty elsewhere; its
  weight is `blend`), and for `--selection knn-mean` `knn_sources`,
  every averaged UTC hour (`;`-separated ISO times). Consumers such as
  `re2gtopt` read the exact provenance instead of rebuilding it.
  29 February never appears: a leap-year February contributes its first
  28 days, and block maps map 29 February to 28 February.
- `rating_stats_month_hour.*`: month × hour n, min, p05, p10, p50, mean,
  max of the line rating.
- `typical_report.json`: per case, the typical vs historical monthly mean
  and p10, the **exceedance** (share of historical hours of the same
  month × hour whose rating is ≥ the typical one: 0.90 is the P90
  target, about 0.5 for the mean), the duration curve, the analogue
  target error and, with `--test-years`, the out-of-sample hour-by-hour
  errors (`p_over` = share of hours where the typical rating exceeds the
  actual one, `mean_over_mw`, `mean_under_mw`, bias, MAE, and the same for
  `--static-mw`). Fit years default to all years except `--test-years`.
- `--plot`: `heatmap_p90_month_hour.png`, `duration_curves.png`,
  `monthly_ratings.png`.

The local calendar (month, day, hour of every UTC hour) follows
`--timezone` (default `auto`: from the span coordinates, America/Santiago
in mainland Chile; the optional `timezonefinder` package covers other
places, else a built-in table, else an error asking for `--timezone`).
`--dst off` (default) uses the zone's **fixed standard time** all year
(Chile: UTC−4), as PLP `indhor` block maps do; `--dst on` follows the
civil clock (UTC−3 in the Chilean summer; the repeated / missing hour
of a changeover month is dropped / filled). `--utc-offset H` is an
explicit fixed override. The chosen basis is in `typical_report.json`
(`calendar`).

### Blocks and horizon

The blocks table (`<prefix>_blocks`) has `source_time` (UTC) and
`source_year` of each block's weather hour whenever they are known:
always with `--history`, and in typical mode when the typical file has
`source_time` (`--source-columns off` drops them).

`to-blocks` maps a typical year to blocks. **For P90 load blocks pass
`--history`** (recommended): the block's statistic and weather then come
from every historical hour in the block, which is the exact "exceeded in
90 % of the block's hours". Without it, the block statistic is taken over
the typical year's hours, which for P90 are already 10th percentiles: a
P90 of P90s, more conservative than intended (hold-out on Los Vilos –
Las Palmas with PLP load blocks: 6 % of hours over-estimated and 72 MW
mean under-estimation, against 11 % and 58 MW with `--history`; the mean
case is unaffected). Each block takes the weather of
**all spans from its own hour whose line rating is closest to the block
statistic** (`--block-stat`, default: the case in the file name, i.e. P90
for `typical_p90.*`). The block's rating is that statistic over its hours,
and gtopt's IEEE 738 reproduces it from the emitted weather.

| Block structure | Option | Hour membership |
|-----------------|--------|-----------------|
| PLP load blocks (non-chronological) | `--block-map` (+ `--planning`) | exact: plp2gtopt's `BlockHourMap/block_hour_map` (from PLP `indhor.csv`, columns `year,month,day,hour,block`, hour 1–24, `--map-hour-base`). The stage of a block comes from a `stage` column or from the planning JSON (`stage_array` `first_block`/`count_block`). A map that covers 20 years tiles the typical year by itself |
| unknown membership | `--hour-bands 1-7,8-18,19-24` + `--first-month/--stages` or `--stage-months stage,month` | approximation: block k of a monthly stage holds the hours of that month in hour band k. Load blocks built from a load-duration curve mix hours of the day, so this is only a fallback |
| chronological | `--block-hours N` (default 1 = hourly, direct) | consecutive hours of the month |

`repeat` tiles a month template (from `--hour-bands` or chronological
blocks) over a horizon of monthly stages (`--first-month`, `--stages` or
`--stage-months`), renumbering blocks from `--first-block`.

Both write `Line/<prefix>_<field>.<fmt>` in the long layout
`scenario,stage,block,uid,value` (`uid` = span uid, `1000·line_uid + k`),
for every scenario in `--scenarios` (default `1`), and
`<prefix>_line<uid>.json` (the `dlr` object). `--mode rating` writes the
precomputed per-span MW `rating` instead (secondary), `--mode both`
writes both, and `--tmax-profile` adds the per-line MW profile
`Line/<prefix>_tmax` (`stage,block,uid,value`) for `Line.tmax_ab` /
`tmax_ba` in cases without `Line.dlr` support. `<prefix>_blocks.*` lists
each block's source hour, statistic and rating.

### Weather history: combining sources

`history` builds the multi-year per-span table that `typical-year` reads:

| Field | Source |
|-------|--------|
| wind speed, direction | Explorador Eólico `recon` (1980–2017 reconstruction, 1 km, 10/20 m), bilinear to the span and log law to `--height`, as the WRF series. `--wind era5`: ERA5 10 m, neutral log law (z0 0.03 m) |
| temperature, GHI | ERA5 (Open-Meteo archive API, `--era5-model era5`), one series per ERA5 cell (`--era5-snap 0.25`) |
| calibration | `--calibrate wrf --calib-weather wrf2010.parquet`: per span, ERA5 temperature + the month × hour mean difference (WRF − ERA5), ERA5 GHI × the month × hour ratio (WRF / ERA5, clipped to [0.5, 2]), fitted on the WRF overlap year |

ERA5 is coarse (0.25°, ~30 km) and its 2 m temperature misses the coastal
fog and the conductor height. The calibration keeps ERA5's day-to-day and
year-to-year variability and adds the 1 km WRF local climatology.
Open-Meteo's `shortwave_radiation` is the mean of the preceding hour;
WRF's is instantaneous.

Licences: ERA5, Hersbach et al. (2020), DOI 10.1002/qj.3803, Copernicus
Climate Change Service ("Contains modified Copernicus Climate Change
Service information"), served by Open-Meteo (CC BY 4.0). **Open-Meteo's
free endpoint is for non-commercial use only**: pass `--api-key`
(customer endpoint) or `--base-url` (your own server). Requests are
per year and per ERA5 cell, cached, `--pause` apart, and retried after
HTTP 429 (`--retries`, waiting 60 s and doubling). The Explorador recon
series have no stated licence; cite them and do not redistribute.

### Validation: Los Vilos – Las Palmas 220 kV

8 critical spans (Matus 2012 method on WRF 2010, ρ 0.9), AAAC Flint at
55 °C, history 1998-2017 (recon wind + ERA5 calibrated to WRF 2010; the
recon wind correlates 0.82 with WRF 2010 at lag 0, ERA5-only wind would
overstate the line P10 by ~25 MW). Fit 1998-2012, hold-out 2013-2017,
hour by hour (43 820 hours):

| Model | over-estimated hours | mean over [MW] | mean under [MW] | bias [MW] |
|-------|---------------------:|---------------:|----------------:|----------:|
| static 224 MW | 1.8 % | 0.1 | 92.2 | −92.1 |
| P98 analogue | 2.7 % | 0.2 | 73.9 | −73.7 |
| P95 analogue | 6.3 % | 0.7 | 63.2 | −62.5 |
| **P90 analogue** (default) | 11.9 % | 2.0 | 51.1 | −49.1 |
| P90 month | 45.4 % | 19.4 | 24.8 | −5.4 |
| P90 aligned-conditional (on the mean TMY's hours) | 25.0 % | 7.1 | 39.2 | −32.1 |
| P50 analogue | 53.0 % | 17.8 | 14.6 | +3.2 |
| mean TMY | 52.6 % | 23.4 | 20.0 | +3.4 |

With the 60 PLP load blocks of a CEN 2-year case (`indhor.csv`):
P90 `--history` 11.4 % / 1.7 / 58.0 MW, mean 52.9 % / 20.3 / 16.9 MW.
The recomputed P90 rating of the emitted weather matches the month ×
hour target within 0.04 MW on average.

### One weather source: `--weather-source`

`history` takes every field from **one** source with
`--weather-source {explorador,explorador-solar,open-meteo}`. Without the option the
default above is unchanged (recon or ERA5 wind, ERA5 temperature and GHI).
Both `dlr2gtopt` and `re2gtopt` call the same layer,
`gtopt_shared/weather_source.py`, so a DLR study and its RE profiles can
read the same weather.

| Source | Model (`--explorador-wrf` / `--open-meteo-model`) | Years | Resolution | Fields |
|--------|------|-------|------------|--------|
| `explorador` | `wrf2010` (default), `wrf2015` | 2010 or 2015 only | WRF 1 km, hourly, 10/20 m | temperature, wind speed and direction, GHI |
| `open-meteo` | `era5` (default, or `--era5-model`) | 1940 to a few days ago | 0.25° (~30 km), hourly | temperature, wind 10/100 m, GHI, DNI, DHI, surface pressure |
| `open-meteo` | `era5_land` / `era5_seamless` | from 1950 / 1940 | 0.1° / mixed | as above |
| `open-meteo` | `ecmwf_ifs` | from 2017 | 9 km | as above |
| `explorador-solar` | `--explorador-solar-mode hourly` (default) | 2004–2016 | GOES satellite + model, hourly, one series per requested point | GHI, DNI, DHI, temperature (2 m), wind speed at 5.5 m, no direction |
| `explorador-solar` | `--explorador-solar-mode aggregate` | 2004–2016 means | month × hour means | same fields; DHI derived (see below) |

The wind is interpolated to `--height` with the DLR vertical code
(`at_height`): log law between the source levels (10/20 m for the WRF,
10/100 m for Open-Meteo), direction through u/v, temperature linear in
height. Open-Meteo radiation is moved to the hour-start label, as the
default path does.

**Years outside the source.** A year the source does not cover fails
with a clear message unless `--climatology` is set:

| `--climatology` | Each target hour takes |
|-----------------|------------------------|
| `off` (default) | nothing: the run fails and names the uncovered years |
| `calendar` | the source-year hour with the same local month, day and hour (29 February → 28 February). One real year is replayed on every target year, with all fields and all points taken from the same hour, so the coincidence of wind, temperature and sun is kept. This is what the Matus 2012 replication did with WRF 2010 |
| `month-hour` | `--climatology-stat` (`mean`, `median` or `qNN`, the NN-th percentile) of the source hours of the same local month and hour of day, per field (direction: circular mean). Smooth, but it breaks the coincidence between fields: for DLR ratings prefer `calendar` |

The local calendar of the climatology follows `--timezone` / `--dst`
(default: the zone of the coordinates in fixed standard time, Chile
UTC−4, as the Explorador series; `--dst on` follows the civil clock).
`--climatology-utc-offset H` is an explicit fixed override. The
provenance records it (`local_calendar`).

The Explorador series are in local time (UTC−4), so the WRF year lacks
its first 4 UTC hours (31 December 20:00–23:00 local of the year
before). Even when the requested year is the WRF year, those hours are
filled by the calendar rule (the same local month, day and hour of the
source year, here 31 December 2010) instead of being dropped. The
provenance lists them (`filled_hours`, `filled_times`, `fill_rule`).

For a multi-year source
(Open-Meteo) the climatology is built from `--climatology-years`
(e.g. `1991-2020`); for the Explorador it is the WRF year.

```bash
# WRF 2010 replayed on 2025-2044 (calendar-aligned), all from the Explorador
dlr2gtopt history --spans spans.csv --years 2025-2044 --height 16 \
    --weather-source explorador --climatology calendar -o history.parquet
# everything from ERA5 (Open-Meteo archive)
dlr2gtopt history --spans spans.csv --years 1998-2017 --height 16 \
    --weather-source open-meteo -o history.parquet
```

`--height-source spans` (with `--weather-source`) takes each span's own
`height` column (e.g. from `spans --heights derive`), `--height` where it
is blank, for every source and climatology mode; the provenance records
the height range. The default recon/ERA5 path keeps one `--height` and
refuses `--height-source spans`.

`weather --weather-source explorador` is an alias of
`weather --source explorador-eolico`.

**Provenance.** Next to the output, `<output>.provenance.json` records
the source, model, resolution, URL pattern, licence, source years,
requested years, retrieval date, height, climatology mode and rule, and
the endpoint (free, customer or own server; the API key is never
written).

**Licences.**

- Open-Meteo data are CC BY 4.0 ("Weather data by Open-Meteo.com"); ERA5
  is Copernicus (Hersbach et al. 2020). **The free Open-Meteo API is for
  non-commercial use only.** For commercial work pass `--api-key`
  (customer endpoint) or `--base-url` (your own Open-Meteo server).
- The Explorador Eólico states no licence. The series are fetched by
  script from <https://eolico.minenergia.cl> (`python-router` request
  `DescargaExploracion`, then `/temp/<file>`), cited, cached locally and
  never redistributed or committed.

**Explorador Solar** (`--weather-source explorador-solar`),
<https://solar.minenergia.cl>, Ministerio de Energía. Same kind of
`/python-router/<json>` endpoint as the Explorador Eólico.

- `hourly` (default): the "Datos Horarios 2004 - 2016" CSV (`tipo:
  "DHC_E"`, then `GET /temp/<file>`), one download per distinct point,
  cached. **The site requires a download-profile declaration** with
  every download (`opciones.registro`); without it the server answers
  HTTP 400 "Perfil de usuario no válido para la descarga". Pass it with
  `--explorador-solar-profile` (no default; the run fails and lists the
  site's types otherwise): `Instalador Eléctrico`, `Empresa eléctrica o
  energética`, `Empresa Consultora`, `Sector Público`, `Academia o
  Investigación`, `Estudiante`, `Público general`, `Otro, no reflejado
  en los anteriores` (the last needs `--explorador-solar-profile-other`,
  1–60 characters). A random anonymous session id is sent, as the site
  does; no personal data.

  File layout ("DATOS HORARIOS GENERADOS CON EL EXPLORADOR DE ENERGIA
  SOLAR"): a metadata block (`DESCRIPCION`, `VERSION DEL MODELO`,
  `FUENTE`, site `LATITUD`/`LONGITUD`/`ALTURA`, `FECHA INICIAL`/`FINAL`,
  `INTERVALO` 60 minutos, `METODO DE CALCULO` "Promedio de valores
  instantaneos cada 10 minutos"), a `VARIABLE,UNIDAD,DESCRIPCION`
  table, then `Fecha/Hora,glb,dir,dif,sct,ghi,dirh,difh,dni,temp,vel,
  shadow,cloud` rows with literal `NaN`. Used: `ghi` → GHI, `dni` →
  DNI, `difh` → DHI (horizontal diffuse; `glb`/`dir`/`dif`/`sct` are the
  tilted-plane components), `temp` (2 m), `vel`. The metadata and the
  variable table go to the provenance. The file stops at 12-30 23:00 of
  2016 (no 31 December) and the radiation of its last 96 hours is `NaN`:
  NaN radiation takes the value 24 h earlier (persistence of the
  diurnal cycle), other gaps are interpolated; the counts are recorded.
  Missing calendar days take the same hour of the neighbouring day.

  Time basis, fitted on the data (site −31.6, −71.4, 1643 cloud-free
  days 2004–2016) since the site states none: the stamps are **fixed
  UTC−4 (no daylight-saving jump in any year or season) and label the
  centre of the averaging hour**, i.e. stamp h = mean over h−30 to
  h+30 min. Against the computed clear-sky zenith the centred window
  gives RMSE 33 W/m² with balanced morning/afternoon residuals; a
  window starting at the stamp (hour start) gives 70 W/m², ending at it
  (hour end) 62 W/m²; the solar-noon offset of the GHI centroid is the
  same in every month and year. The tool therefore labels each
  hour-start value as the mean of the stamps h and h+1
  (`--explorador-solar-label centre`, default; `start` keeps the
  stamps) and converts with `--explorador-solar-utc-offset` (−4). It is
  a real time series: `--climatology calendar` or `month-hour` with
  `--climatology-years` (within 2004–2016) cover other years.
- `aggregate`: `tipo: "ExploracionCompleta"`, no declaration. Month ×
  hour means 2004–2016 (`HM`, hour-major: index `hour·12 + month`).
  Only `--climatology month-hour` (stat `mean`) is accepted: `off` and
  `calendar` fail, because there is no hourly year. There is no month ×
  hour diffuse field; DHI = GHI(m, h) · DIFH(m) / (DIRH(m) + DIFH(m)),
  the daily diffuse fraction of each month from the monthly means `SM`
  (energy-consistent per month, DHI ≤ GHI). The labels are treated as
  fixed UTC−4 centred on the hour: the GHI centroid sits 3.5 h before UTC
  solar noon all year, with no daylight-saving jump. The tool's
  `--dst` therefore never shifts the diurnal cycle.

Every field comes from the Explorador Solar; nothing is mixed in from
another source (the Explorador Eólico stays a separate
`--weather-source explorador`).

Wind height: the CSV states none for `vel`, and the `hgt` of the
download request is the site's SRTM ground elevation, not a wind
height. The site's own SOLEVAPP report (Módulo de Evaporación,
<https://solar.minenergia.cl/downloads/Modulo_Evaporacion_DV.pdf>,
sec. 2, p. 5–6) documents the Exploradores API wind as "velocidad del
viento a 5.5 metros de altura" ("el Explorador tiene datos a 5.5 m").
That the DHC_E `vel` column is this same series is an inference (same
API). `--explorador-solar-wind-height` defaults to 5.5 m; the basis is
recorded in the provenance. The speed is brought to `--height` (or
each span's height) with a neutral log law, z0 =
`--explorador-solar-z0` (default 0.03 m).

Wind direction: none. `history` fails unless the attack angle (wind to
conductor axis) is given explicitly; there is no silent default:

- `--wind-angle-deg 45`: the study value when the direction is unknown.
- `--wind-angle-deg 0`: parallel wind, the least convective cooling,
  the conservative variant.
- `--perpendicular-wind` (= 90°): the most cooling angle, the
  static-rating convention; optimistic for a real, unknown direction.

The ratings order 0° < 45° < 90° (tested). Basis: in IEEE 738 the
forced-convection angle factor K_angle is 0.388 at 0°, about 0.855 at
45° and 1.0 at 90°; the CIGRE angle correction (TB 207 eq. 14, reused
by TB 601) gives 0.42, about 0.85 and 1.0. The 45° choice for an
unknown direction is a project study decision. CIGRE TB 207 suggests
45° only as a low-wind simplification (wind below about 0.5 m/s with a
highly variable direction), as quoted in arXiv:2405.12319. TB 601
replaces that rule with the larger of forced convection at the actual
angle and natural convection. Neither TB 601 nor IEEE 738 (read
through secondary sources; the standards are paywalled) recommends a
general angle for an unknown direction.

Angle from the Eólico direction climatology (`--wind-angle-source
explorador-eolico`, instead of the default `fixed`). The attack angle
of each span is taken per local month × hour from the dominant wind
direction of the Explorador Eólico WRF at the span. The models are set
by `--wind-angle-wrf`: default `wrf2010,wrf2015`, pooling the years
2010 and 2015. This is a rating parameter, not a second weather
source: wind speed, temperature and irradiance stay the Explorador
Solar's, and only the direction climatology comes from the Eólico.

- Dominant direction per (month, local hour): the 22.5° sector (16-point
  rose) with the largest sum of wind speed, and the speed-weighted
  circular mean of the directions inside it.
- φ = |dominant direction − span azimuth|, folded to 0–90°. The
  history's `wind_direction` is set to azimuth + φ, so the rating sees
  φ.
- Local month and hour follow the tool's calendar (`--timezone` /
  `--dst`) for both the Eólico hours and the target hours.
- Provenance (`wind_angle`): the WRF models and years, and per span the
  12 × 24 tables of φ, dominant direction, sector share (that sector's
  fraction of the speed sum) and R (the speed-weighted mean resultant
  length: 1 = steady direction, 0 = none).

The option refuses `--wind-angle-deg` / `--perpendicular-wind`, and it
applies only to `--weather-source explorador-solar`.

Licence: none stated. The series are fetched by script, cited, cached
locally and never redistributed or committed.

### Pitfalls

- A typical year is not a forecast: it says nothing about the hour-by-hour
  coincidence of weather with load or wind generation. Cool, windy hours
  raise both the rating and wind output; a typical year built per month ×
  hour loses that correlation. Where gtopt scenarios are historical years,
  use the same weather year per scenario if the correlation matters.
- The `analogue` year has no persistence (consecutive hours come from
  different days); do not use it for chronological storage studies.
- The month boundary blend mixes two hours of the same spans with the same
  weights (span consistency holds) but those hours are not historical
  weather.
- In `rating` / `tmax` output, gtopt uses the MW verbatim: a later change
  of conductor, `tmax` or voltage needs a rerun. `weather` output lets
  gtopt recompute.

References: Matus et al. (2012) [1] (the critical spans whose weather
the typical years carry; the line rating is the min over them);
Finkelstein & Schafer, Biometrika 58(3):641–645, 1971;
Hall et al., SAND78-1096C, 1978; Wilcox & Marion, "Users manual for TMY3
data sets", NREL/TP-581-43156, 2008; FERC Order 881, 87 FR 2244 (2022);
CIGRE TB 299 (2006), TB 498 (2012), TB 969 (2025).

## Fidelity to the paper

`critical-spans` implements the selection of [1]. The line rating is the
minimum of the span ratings. A candidate set is judged by the Pearson
correlation between its minimum and the line rating. Spans are added
greedily, and the result is compared with equidistant placement.
Differences:

- **Weather.** The ampacity matrix the method consumes is built here from
  the Explorador Eólico WRF 1 km hourly hindcast of 2010 [3]. The weather
  is interpolated to each span midpoint (bilinear on the 0.01° grid, log
  law between 10 and 20 m, direction through u/v). The paper's own
  interpolation and model set-up may differ in detail.
- **Heat balance.** IEEE 738 [2] with the conventions shared with gtopt:
  the global horizontal irradiance is used directly as absorbed flux (no
  sun-position model), and below 0.5 m/s a 45° attack angle is used.
- **Additions not in the paper**, all opt-in or reporting only:
  `--max-spans` (an optional cap, e.g. 1 for a single representative
  span; the paper's stopping rule, a target ρ, stays the default);
  per-segment selection (`--segments`); the full ρ(n) trace and plot;
  and η (`--eta-quantile`), a safety factor from the estimation residual.
  η is **off** by default, and gtopt's `safety_factor` stays 1 unless set.

Replication on Los Vilos – Las Palmas 220 kV (199 spans, CEN tower
list). With the paper's four months (January, April, July, October) and
bilinear weather, ρ = 0.9 needs 8 spans (paper: 6), against 16
equidistant spans (paper: 12). The first spans chosen, 30, 128, 64, 5,
22 and 199, lie within 1–6 spans of the paper's 29, 127, 63, 10, 21 and
193. The paper's own set reaches ρ = 0.858 on this weather.

## References

1. M. Matus, D. Sáez, M. Favley, C. Suazo-Martínez, J. Moya,
   G. Jiménez-Estévez, R. Palma-Behnke, G. Olguín and P. Jorquera,
   "Identification of Critical Spans for Monitoring Systems in Dynamic
   Thermal Rating", *IEEE Transactions on Power Delivery*, vol. 27,
   no. 2, pp. 1002–1009, April 2012.
   [doi:10.1109/TPWRD.2012.2185254](https://doi.org/10.1109/TPWRD.2012.2185254)
   Open access: [ResearchGate](https://www.researchgate.net/publication/254057820_Identification_of_Critical_Spans_for_Monitoring_Systems_in_Dynamic_Thermal_Rating),
   [PDF (Universidad de Chile)](https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf)
2. IEEE Std 738-2012, "IEEE Standard for Calculating the
   Current-Temperature Relationship of Bare Overhead Conductors".
   [doi:10.1109/IEEESTD.2013.6692858](https://doi.org/10.1109/IEEESTD.2013.6692858)
   (revised as IEEE Std 738-2023).
3. Explorador Eólico and Explorador Solar, Ministerio de Energía de Chile
   and Departamento de Geofísica, Universidad de Chile:
   <https://eolico.minenergia.cl>, <https://solar.minenergia.cl>.
4. Coordinador Eléctrico Nacional, Infotécnica:
   <https://infotecnica.coordinador.cl>, API
   `https://api-infotecnica.coordinador.cl/v1`.
5. OpenStreetMap contributors, data under the Open Database License
   (ODbL 1.0): <https://www.openstreetmap.org/copyright>.
6. CIGRE Technical Brochure 601, "Guide for Thermal Rating Calculations
   of Overhead Lines", 2014.
