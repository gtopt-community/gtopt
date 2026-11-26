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
| `weather` | `--source` | `explorador-eolico`, `open-meteo`, `ecmwf-opendata`, `csv`, `era5` (not implemented: use `csv`) |
| `weather` | `--interp` | `bilinear`, `nearest` (grid sources) |
| `weather` | `--wind-profile` | `log` (log law through two levels), `power` (fitted exponent, clipped to [−0.5, 1]), `none` |
| `weather` | `--height` | conductor height, 15 m |
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
`--pause` seconds (default 3) between requests to the same server.
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
