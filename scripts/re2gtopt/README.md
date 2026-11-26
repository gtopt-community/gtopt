# re2gtopt: wind and PV plant profiles on the DLR typical year

`re2gtopt` turns hourly weather into per-plant wind and PV output and
writes it as gtopt `GeneratorProfile` schedules (per-unit of the plant's
Pmax). It runs on **the same historical hours** that `dlr2gtopt` picks
for the line ratings. A block that represents a windy, cool hour for a
line therefore represents the same hour for the wind farms and PV
plants next to it.

```
inventory   CEN Infotécnica -> plants.csv (coordinates, Pmax, equipment)
history     Explorador Eólico recon + ERA5 at each plant -> weather.parquet
model       weather -> hourly CF per plant + sizing / Pmax check
fetch-cen   CEN hourly generation / curtailment / forecasts -> observed.parquet
calibrate   model vs observed (curtailment-aware) -> cf_calibrated.parquet
typical     CF history -> 8760 h on the DLR typical-year source hours
to-blocks   8760 h -> blocks (same hour->block map as dlr2gtopt)
repeat      blocks -> GeneratorProfile over the horizon (+ JSON snippet)
validate    model vs observed metrics; CF vs DLR rating cross-correlation
```

Downloads (Infotécnica, CEN workbooks, Explorador, Open-Meteo) go to a
cache directory (default `~/.cache/gtopt/re2gtopt`) with at least 3 s
between requests. **No downloaded data belongs in the repository**: link
to the sources and fetch them with the script.

## The profile is potential generation, not dispatch

The typical-year profile is the plant's **potential** output: what the
wind or sun allows, after equipment losses, availability and the
max-output cap. It is **not** reduced by curtailment. Curtailment
(transmission congestion, zero prices, system limits) is what gtopt
decides in its dispatch, given that profile as an upper bound. When
fitting to observed output, curtailed hours are therefore treated as
censored observations and never pull the fitted curve or scale down
(see Calibration).

## Quick start

```bash
re2gtopt inventory --names "PE TOTORAL;PFV JAVIERA" -o plants.csv
re2gtopt history --plants plants.csv --years 1998-2017 -o weather.parquet
re2gtopt model --plants plants.csv --weather weather.parquet -o run/
re2gtopt fetch-cen --plants plants.csv --generation 2016-2019 2020-2023 \
    -o observed.parquet
re2gtopt calibrate --plants plants.csv --model run/ \
    --observed observed.parquet -o cal/
re2gtopt typical --cf cal/cf_calibrated.parquet --plants plants.csv \
    --dlr-typical <dlr>/typical_mean.parquet -o typ/
re2gtopt to-blocks typ/re_typical_mean.parquet --plants plants.csv \
    --planning planning.json --block-map block_map.csv -o tpl/
re2gtopt repeat tpl/re_profile_blocks.parquet --plants plants.csv \
    --stages 240 -o case/input/
```

`repeat` writes `GeneratorProfile/re_profile.parquet` in the long layout
gtopt reads (`scenario, stage, block, uid, value`, int32 keys) and
`re_profile_generator_profiles.json`. That JSON holds
`generator_profile_array` entries (`uid`, `name`, `generator`,
`profile: "re_profile"`) to merge into the system.

## Consistency with the DLR typical year

* **Same hours.** `typical --dlr-typical typical_<case>.parquet` reads
  the `source_time` of each of the 8760 typical hours (UTC) chosen by
  `dlr2gtopt typical-year`. It takes the plant CF of exactly that
  historical hour. At month seams, where the DLR blends two years
  (`blend > 0`), it reads the blend partner from `source_time_2` and
  blends the CF with the same weight. For a `knn-mean` P90 analogue it
  averages the CF over every hour in `knn_sources`, as the DLR averaged
  the weather. Typical files from older dlr2gtopt versions have neither
  column: the blend partner is then rebuilt with the DLR seam rule.
* **Same weather.** `history` uses the same Explorador Eólico grid,
  bilinear stencil and recon model as the DLR, ERA5 from the same
  Open-Meteo archive, and the same radiation label (hour start). Both
  tools build the month/hour calendar the same way. `--timezone auto`
  (the default) takes the zone from the plant coordinates
  (America/Santiago in mainland Chile; the optional `timezonefinder`
  package covers other places). `--dst off` (the default) uses the
  zone's fixed standard time all year, UTC−4 for Chile, as PLP `indhor`
  does; `--dst on` follows the civil clock. `--utc-offset` sets an
  explicit fixed offset. Without plant coordinates (`typical` with no
  `--plants`), `auto` fails and asks for `--timezone`.
* **Same blocks.** `to-blocks` and `repeat` call dlr2gtopt's own
  hour→block builders (`--block-map`, `--hour-bands`, `--block-hours`)
  and its `repeat`. A block's profile is therefore the mean of the same
  typical hours whose weather defines the DLR block.
  `--block-stat dlr-hour --dlr-blocks <dlr blocks>` instead takes the
  CF of the single source hour the DLR picked for each block. Add
  `--cf-history <cf>` to take the CF of the block's exact historical
  UTC hour (`source_time` in the DLR blocks table). This is required for
  DLR blocks built with `to-blocks --history`, whose `source_hour` is an
  hour of the year, not of the typical year.
* `align` fails when a typical hour has no CF. A typical year is never
  built from a different history than the DLR's.

The provenance columns (`source_time_2`, `knn_sources`, and
`source_time` / `source_year` in the DLR blocks table) are written by
`dlr2gtopt` by default (`--source-columns on`).

## Energy: P50 by default

**Default: `--case mean --energy-preserve on --energy-scope monthly`.**
A typical year is built from selected historical days, so its energy
need not match the long-term mean. `typical` rescales each plant per
month so that the typical-year CF equals the long-term monthly mean CF
of the full history. The scale is capped: values that would exceed the
plant ceiling are clipped and the remainder is redistributed to the
unclipped hours (`capped_scale`). The report gives the raw and final
CF per plant.

* `--case p50|mean` gives the expected energy. `mean` equals `p50` for
  symmetric inter-annual variability (IAV).
* `--case p90` or `quantile:<q>` gives the annual energy exceeded in
  90 % (1−q) of years. `--p-method empirical` takes the quantile of the
  annual CF of the history years. `--p-method normal` uses
  `P50·(1 − 1.282·σ)`, with σ the root-sum-square of the IAV and
  `--uncertainty`, the lenders' convention (Dobos 2012; IEA PVPS T13-12).
  The shape stays the mean-year shape and only the energy is scaled.
* `--energy-target observed` uses the observed long-term mean of each
  plant, after the curtailment filter, as the target.
* `--energy-preserve off` keeps the raw typical-year energy, for
  diagnostics only.

**Why not reuse the DLR P90 hours?** `dlr2gtopt` builds its P90 typical
year from low-rating hours: weak wind, hot and sunny. Taking the wind
CF of those hours understates wind energy by a large factor. This is a
critical-case coincidence, not a P90 *energy* year. Planning models
(PyPSA-Eur, ReEDS/reV, GenX, AEMO ISP, ENTSO-E PECD, the CNE PET and
the MEN PELP) all use expected-value profiles for renewable energy, and
place conservatism in scenarios (dry or high-cost years). Pair RE
profiles with the DLR **mean** typical year. Use the DLR P90 as a
line-security case only, keeping RE at P50, or use `--case p90` when the
study explicitly wants a P90 energy year.

## Weather source: `--weather-source`

`history --weather-source {explorador,explorador-solar,open-meteo}`
takes the weather of the plants from one source, through the layer shared with `dlr2gtopt`
(`gtopt_shared/weather_source.py`; full description, coverage table
and climatology modes in the `dlr2gtopt` README, "One weather source").
Without the option the default is unchanged: Explorador recon wind
(`--wind-source`) and ERA5 temperature, pressure and irradiance.

| `--weather-source` | Wind | Temperature, irradiance, pressure | Years |
|--------------------|------|-----------------------------------|-------|
| `explorador` | Explorador `--explorador-model` (default `recon`, 1980–2017, 10–140 m), as the default path | Explorador WRF (`--explorador-wrf`, 2010 or 2015): temperature and GHI only | WRF year; other years need `--climatology calendar` (replay the WRF year on the same local month/day/hour) or `month-hour` |
| `explorador-solar` | Explorador Eólico `--explorador-model`, as the default path | Explorador Solar: GHI, DNI, DHI and temperature, hourly 2004–2016 (`--explorador-solar-mode hourly`, needs `--explorador-solar-profile`, the site's download declaration) or month × hour means (`aggregate`, `--climatology month-hour` only) | 2004–2016; other years with `--climatology` and `--climatology-years` |
| `open-meteo` | ERA5 10/100 m (the `--wind-source era5` path) | Open-Meteo archive (`--open-meteo-model`, else `--era5-model`): temperature, GHI, DNI, DHI, surface pressure | ERA5 from 1940, IFS from 2017 |

With `explorador` there is no DNI, DHI or pressure: the PV model
decomposes GHI (`--decomposition`) and air density uses the standard
atmosphere at the plant elevation. A field the chosen source cannot
supply fails with a message naming it. `<output>.provenance.json`
records the source, model, URL pattern, licence, years, retrieval date
and climatology. The climatology calendar follows `--timezone` / `--dst`
(`--climatology-utc-offset` overrides it), and the first 4 UTC hours that
the local-time WRF year lacks are filled by the calendar rule and listed
in the provenance.

`explorador-solar` is the main source of real DNI/DHI for PV in Chile;
its wind (5.5 m) is not used for wind plants. See the `dlr2gtopt`
README for the download declaration, the file layout, the fitted time
basis (fixed UTC−4, stamps centred on the hour) and the DHI rule of the
aggregates.

Licences: Open-Meteo data are CC BY 4.0 and **the free API is
non-commercial only**; pass `--api-key` (customer endpoint) or
`--base-url` (own server) for commercial work. The Explorador (Eólico and
Solar) states no licence: fetched by script, cited, never redistributed.

## Wind model (options and defaults)

| Step | Default | Options / reference |
|---|---|---|
| Hub wind | recon 10–140 m interpolated to the hub (`--shear none`) | `power` α=1/7, `log` z0, `fitted` α hour by hour, `ratio` month×hour WRF ratio (`--ratio-model wrf2010`); Gualtieri 2019 |
| Air density | hourly from ERA5 T and p (`--wind-density weather`) | `elevation` (standard atmosphere), `none`; IEC 61400-12-1: `v·(ρ/ρ0)^(1/3)`, ρ0 = 1.225 |
| Turbine curve | CEN model → OEDB (windpowerlib) → generic | `oedb:<type>`, `iec1/2/3/3s`, `sp<W/m²>`, CSV file; `auto` picks by year and site wind |
| Farm smoothing | `none` (single-plant profiles) | `ninja` (Renewables.ninja Gaussian σ = 0.6 + 0.2·v, for fleet or regional aggregates), `atlite` (Δv 1.27, σ 2.29, η 0.95), `gaussian a,b`; Staffell & Pfenninger 2016, Andresen et al. 2015, Norgaard & Holttinen 2004 |
| Wake | 7 % energy loss as a speed deficit (`--wake-mode speed`) | `power` (flat factor); Lee & Fields 2021 |
| Cut-out | soft ramp 25→30 m/s | `hard`, `hysteresis` (restart 20 m/s) |
| Availability | 0.97 | |
| Electrical losses | 0.02 | |
| **Max output (cap)** | **0.95 of Pmax** | `--wind-cap 1.0` disables |

**Wind max output.** A farm almost never delivers its full nameplate:
turbines are down for maintenance, wakes and spatial spread mean not
every turbine sees rated wind together, and electrical losses apply.
Observed hourly maxima of Chilean and European farms are about 0.90–0.97
of Pmax (the p99.5 of CEN hourly output, see `calibrate`'s
`fitted_ceiling`). The default caps the farm at 0.95·Pmax. The cap is
separate from the sizing: the modelled peak before the cap must
reproduce Pmax (below). When observed data exist, `calibrate` replaces
the cap with the plant's own p99.5 non-curtailed ceiling.

## PV model (pvlib; options and defaults)

| Step | Default | Options / reference |
|---|---|---|
| Decomposition | DIRINT (uses ERA5 DNI/DHI when `source`) | `erbs`, `erbs-driesse`, `disc`; Erbs 1982, Perez 1990, Engerer 2015, Yang 2022 |
| Transposition | Perez | `haydavies`, `isotropic`, ...; Gueymard & Ruiz-Arias 2016 |
| Mounting | single-axis N–S, backtracking, GCR 0.35, ±60° | `fixed` (tilt = |lat|, north-facing); Lorenzo 2011, Anderson & Mikofski 2020 |
| Cell temperature | Faiman (U0 25, U1 6.84) | `sapm`, `pvsyst`, `none`; Faiman 2008 |
| γ_pdc | −0.35 %/°C (CEC module DB when the model matches) | |
| Soiling | 3 % | inland Atacama ~2 %, coastal north ~5 %; Cordero 2018, Olivares 2017/2020 |
| DC losses | 7 % | PVWatts; Dobos 2014 |
| DC/AC | CEN modules×Wp / AC when reported; else by commissioning year: ≤ 2015 1.05, 2016–2019 1.2, ≥ 2020 or unknown 1.3 | `--dc-ac-ratio <x>` fixed; `--dc-ac-table 2015:1.05,2019:1.2,9999:1.3`; LBNL Utility-Scale Solar (median ILR ≈ 1.3 in the 2020s) |
| Inverter | η 0.98, clipping at the AC ceiling | |
| AC losses / availability | 1 % / 0.98 | |

## Sizing reproduces each plant's Pmax

* Wind: `n = round(Pmax_gross / rating)`. With no CEN model, the
  rating is chosen in 4–6 MW (a divisor of Pmax when one exists).
  `installed_ratio = n·rating / Pmax`. On a `net` basis the electrical
  loss is added back. When CEN equipment exists and `n × rating`
  disagrees with Pmax by more than the tolerance, a warning is
  printed and Pmax stays the ceiling.
* PV: AC ceiling = min(Pmax, injection limit, inverter count × kVA when
  reported). DC = AC × DC/AC; modules = DC / Wp.
* `model` writes `sizing.csv`, one row per plant: Pmax, the modelled
  peak before availability and cap (max hourly over the history), the
  implied units, DC/AC and `peak_ok` (within `--tolerance`, default
  5 %).

`--equipment-source cen` (default) uses CEN turbine and module data
where Infotécnica has them. `generic` ignores them. The modern generic
defaults are a 4–6 MW turbine with a 120 m hub (95 m before 2016)
(OEDB V112/3450 or E-141/4200 by site wind), and a single-axis tracker
with 580 Wp modules and the DC/AC of the plant's commissioning year
(1.3 from 2020).

## Calibration on observed output (default `curve` when data exist)

`calibrate --observed` fits each plant on its own hourly output. With
`--calibration auto` it uses `curve`:

* `scalar` fits one factor k (least squares through 0, or the envelope).
* `curve`: for wind, an empirical power curve CF(v) against the
  density-normalised hub speed (binned, isotonic up to rated, non-
  increasing tail). For PV, an effective performance ratio on the
  modelled DC plus the observed clipping ceiling.
* `quantile-map` maps model to observed quantiles per month.
* `none` leaves the model output unchanged.

The **fitted ceiling** is the p99.5 of the non-curtailed observed
hours, reported against Pmax. Plants with fewer than `--min-hours`
valid hours fall back to the nearest calibrated plant of the same type
(`--fallback nearest`) or to the uncalibrated model.
`--cv on` runs a leave-one-year-out cross-validation and reports bias,
RMSE, correlation and the duration-curve RMSE, calibrated against
uncalibrated.

### Curtailment is censoring

Curtailed hours under-report the potential. `valid_hours` drops:

* the first `ramp_months` (3) after the first output (commissioning);
* zero output while the model sees resource (outages);
* flat runs at least `flat_hours` long (2) below the potential with
  output under the plateau (curtailment signature);
* hours with CEN-reported curtailment, or a price ≤ `--price-eps`.

`--censoring`:

* `filter` (default) fits only on the valid hours.
* `mask` fits on observed plus curtailed, when the CEN curtailment
  file is given.
* `unconstrained` fits on the hours where the potential is below
  `--unconstrained-frac` of the ceiling (early, late or cloudy hours).
* `envelope` fits an upper-envelope (quantile `--envelope-q`).
* `naive` uses every hour, for comparison only.

`calibration.csv` reports, per plant, the hours excluded, the curtailed
energy share and k for the chosen and the naive censoring.

`--calibration-target` chooses what the fit targets:

* `observed` (default) fits on the observed generation.
* `forecast` fits on the CEN day-ahead forecast (`forecast_mwh` from
  `fetch-cen --forecast`). The forecast is not curtailed, so no
  censoring is applied. It is the operator's own view of the plant's
  potential, a proxy for its conversion parameters, which are not
  published.
* `both` fits on observed and reports `forecast_k`, `forecast_ceiling`
  and `forecast_cf` beside it.

The public forecast archive covers 2023–2025, after the end of the
recon wind, so wind needs `history --wind-source era5` for those years.

## Validation: 6 CEN plants, 2016–2019

Setup:

* Wind (Coquimbo coast): PE Los Cururos (109.4 MW, 57×V100, hub 80 m),
  PE Monte Redondo (48 MW, 24×V90) and PE Totoral (46 MW, 23×V90), on
  recon 2016–2017.
* PV (Atacama): PFV Llano de Llampos (101 MW), PFV Javiera (69 MW) and
  PFV San Andrés (50.6 MW), on ERA5 2016–2019.
* Equipment from Infotécnica, observed output from the CEN 2016–2019
  workbook.
* Every number below is a CF in p.u. of Pmax. The leave-one-year-out
  metrics are hourly, on the non-curtailed hours.

| plant | obs CF | model CF (uncal.) | LOYO RMSE none → curve | corr none → curve | fitted ceiling | flagged hours (flat / outage) |
|---|---|---|---|---|---|---|
| PE Los Cururos | 0.235 | 0.447 | 0.366 → 0.194 | 0.66 → 0.70 | 0.96 | 962 / 783 |
| PE Monte Redondo | 0.179 | 0.317 | 0.283 → 0.154 | 0.67 → 0.67 | 0.86 | 1068 / 260 |
| PE Totoral | 0.179 | 0.246 | 0.202 → 0.143 | 0.72 → 0.73 | 0.86 | 589 / 164 |
| PFV Llano de Llampos | 0.254 | 0.300 | 0.102 → 0.071 | 0.98 → 0.98 | 0.88 | 1606 / 58 |
| PFV Javiera | 0.253 | 0.289 | 0.127 → 0.117 | 0.94 → 0.94 | 0.90 | 1234 / 75 |
| PFV San Andrés | 0.178 | 0.263 | 0.182 → 0.135 | 0.89 → 0.86 | 0.84 | 2869 / 155 |

Defaults: no farm smoothing, DC/AC by commissioning year (Llano de
Llampos, 2014, no CEN modules count: 1.05; Javiera and San Andrés: CEN
0.99). Observed CF is on the non-flagged hours, so it moves with the
flags.

Findings:

* **The uncalibrated model is biased high**: +7 to +21 CF points for
  wind and +4 to +9 for PV. For wind, the Explorador recon speeds at
  80 m on this coast are too strong for the observed output. Hourly
  correlation is only 0.67–0.73: sea-breeze timing and gusts are not
  reproduced hour by hour. For PV, single-axis tracking is optimistic
  for 2014–2015 plants: CEN reports no tracking at Llano de Llampos and
  San Andrés. The commissioning-year DC/AC (1.05 for Llano de Llampos,
  was 1.3) cuts its uncalibrated bias from +11 to +5 points and its
  uncalibrated RMSE from 0.162 to 0.102; with less clipping, fewer
  midday hours look flat, so 1606 instead of 4085 hours are flagged.
  Calibration is still the default whenever observed data exist.
* **Calibration**: under leave-one-year-out, `curve` has the lowest
  hourly RMSE at every plant except San Andrés, where `scalar` is
  close. `quantile-map` matches the duration curve best (duration
  RMSE ≤ 0.02) but loses hourly correlation. `scalar` sits in between.
* **Max output and smoothing**: the observed p99.5 ceilings are
  0.84–0.96 of Pmax. Without farm smoothing (the default) the farms
  reach the 0.95 cap, so it binds, and the Pmax check passes. With
  Renewables.ninja smoothing the V90 farms peak at 0.80 p.u. and fail
  the check: one farm is less smoothed than the national fleets the
  ninja σ was fitted on. Smoothing lowers the *uncalibrated* RMSE by
  0.02–0.05 (it trims the high bias), but after the `curve`
  calibration the LOYO RMSE is the same within 0.001 either way. Use
  `--smoothing ninja` for fleet or regional aggregates.
* **Curtailment** (Atacama, 2016–2017): the flat-run filter flags
  3–12 % of hours at the PV plants. The estimated curtailed energy is
  2–7 % (`curtailed_share_est`). The fit on filtered hours raises the
  calibrated CF above the naive fit only slightly (0 to +0.9
  points), because the clear-sky shape is mostly preserved. The
  `envelope` censoring estimates a much higher potential (+4 to +11
  points). Treat it as an upper bound.
* **Generic equipment** (`--equipment-source generic`, modern 4–6 MW
  turbines at 95–120 m, DC/AC by commissioning year) widens the bias
  further (+6 to +22 points against all observed hours; Monte Redondo
  and Los Cururos are worst). Use it only for new plants, and calibrate it on nearby
  plants (`--fallback nearest`).

### End to end with the DLR typical year (Los Vilos–Las Palmas)

Setup:

* `dlr2gtopt history` covered 10 spans of the 220 kV line (recon +
  ERA5, 1998–2017), followed by `typical-year` with the default
  calendar (`--timezone auto --dst off`, i.e. fixed UTC−4; identical
  to the former explicit `--utc-offset -4`). The
  TMY mean is 445.7 MW (history 445.8 MW) and the P90 analogue
  394.5 MW.
* `re2gtopt` used the calibrated CF of the six plants over 1998–2019,
  modelled with the current defaults (no farm smoothing, DC/AC by
  commissioning year). After calibration the results below match the
  earlier run (ninja smoothing, DC/AC 1.3) to the precision shown.

Results:

* **On the DLR TMY hours** the RE CF falls within 0.2 % (PV) and 1.1 %
  (wind) of the 20–22-year mean, before rescaling. Energy preservation
  then removes the remaining difference.
* **On the DLR P90 hours** the wind CF is 25–27 % below its long-term
  mean, lower than any historical year, and the PV CF 2–3 % below. This
  is why RE pairs with the DLR mean year by default.
* `--case p90` (empirical annual P90) is 7–8 % below P50 for wind
  (IAV 6–7 %) and 1–2 % below for PV (IAV about 1 %).
* **Coupling with the line rating** (2016–2017, hourly Pearson,
  lag 0): wind CF against the LVLP rating is 0.20–0.28 in the model
  and 0.17–0.30 observed; PV is 0.42–0.44 model and 0.36–0.41
  observed. Wind correlates best when the rating is taken 1 h earlier
  (0.25–0.33). The model keeps the sign and roughly the size of the
  observed wind–cooling coupling, so co-optimising RE and DLR on the
  same hours is meaningful.
* `to-blocks --hour-bands 1-6,7-12,13-18,19-24` followed by
  `repeat --stages 240` gives 960 blocks per scenario and a 5760-row
  long `GeneratorProfile` (int32 keys, values 0–0.79).
* **DLR P90 blocks from the history.** `dlr2gtopt to-blocks --history`
  (exact block P90, 48 blocks) now records each block's UTC
  `source_time`; its source hours come from 17 different years. With
  `re2gtopt to-blocks --block-stat dlr-hour --cf-history`, each RE
  block takes the CF of that same historical hour: the mean block wind
  CF is 29–32 % below the block means of the P50 year (PV 4–5 %),
  consistent with the P90-hour result above.

## CEN data sources (public, no login; link, do not redistribute)

* **Infotécnica** `https://api-infotecnica.coordinador.cl/v1`:
  * `/centrales/extended/`, with coordinates and Pmax;
  * `/centrales/{id}/fichas-tecnicas/general/`;
  * `/unidades-generadoras/?id_central=`;
  * `/unidades-generadoras/{id}/fichas-tecnicas/{turbinas-eolicas|generacion-solar}/`.

  Rows certified "Rechazado" are skipped (`--keep-rejected`). There is
  no inverter model, kVA or DC/AC field, and rotor diameter and
  per-turbine coordinates are usually empty (the rotor is read from
  the model name). PV tracking is often empty, and the generic default
  then applies.
* **Hourly gross generation per plant (MWh)**: the yearly workbooks
  `CEN-hist_gen_de_energia_por_central_*.xlsx` from
  <https://www.coordinador.cl/reportes-y-estadisticas/>. They are in
  local civil time, and hour 25 is the extra hour when daylight saving
  ends. `fetch-cen --generation 2016-2019 2020-2023 2024 2025`
  downloads them to the cache.
* **Hourly curtailment per plant (2022 onward)**: the monthly
  "Reducciones de Energía Eólica y Solar" workbooks at
  <https://www.coordinador.cl/operacion/documentos/reducciones-de-generacion-renovable/>
  (`fetch-cen --curtailment <xlsx|url>`).
* **Forecasts**: the monthly centralized-forecast archives (2023–2025)
  at <https://www.coordinador.cl/mercados/documentos/pronostico-centralizado-de-generacion-y-demanda-2/pronostico-centralizado-de-generacion/>.
  They hold day-ahead hourly forecasts (CEN centralized, coordinated
  parties, external provider), the real output and curtailment per
  plant, in long `Fecha|Nombre|Valor` tables
  (`fetch-cen --forecast <csv|xlsx>`). The 48 h P25/P50/P75 forecasts
  and the plant met-station data that the NTCO/NTSyCS require are not
  public (SIP needs a key).
* Real marginal costs per bus (zip per day since Oct-2024, `cmgYYMM`
  before) can be given as `--prices time,plant,price`.

No licence text was found for these files. They are published under
the NTCO publication duties. Cite the URL and fetch by script.

Weather: Explorador Eólico (Ministerio de Energía / U. de Chile; recon
1980–2017, 10–140 m) and ERA5 via the Open-Meteo archive (free for
non-commercial use; use an API key for commercial work,
`--era5-api-key`). The Explorador Solar hourly series need a
self-declared user profile and are not used.

## Literature

Wind:

* Staffell & Pfenninger 2016, *Energy* 114:1224,
  doi:10.1016/j.energy.2016.08.068. Renewables.ninja: farm smoothing
  and bias correction.
* Andresen et al. 2015, *Energy* 93:1074,
  doi:10.1016/j.energy.2015.09.071. Hofmann et al. 2021, *JOSS*,
  doi:10.21105/joss.03294 (atlite).
* Norgaard & Holttinen 2004, *A multi-turbine power curve approach*,
  NWPC (VTT).
* Gualtieri 2019, *RSER* 102:215, doi:10.1016/j.rser.2018.12.015. Wind
  shear.
* IEC 61400-12-1. Density normalisation.
* Lee & Fields 2021, *WES* 6:311, doi:10.5194/wes-6-311-2021. Losses
  are 9.5–22.5 %, and P50 overprediction 3.5–4.5 %.
* Gruber et al. 2022, *Energy* 238, doi:10.1016/j.energy.2021.121520.
  Bias correction of reanalysis wind.
* Draxl et al. 2015, *Appl. Energy*,
  doi:10.1016/j.apenergy.2015.03.121 (WIND Toolkit).

PV:

* Holmgren et al. 2018, doi:10.21105/joss.00884. Anderson et al. 2023,
  doi:10.21105/joss.05994 (pvlib).
* Pfenninger & Staffell 2016, doi:10.1016/j.energy.2016.08.060.
* Erbs et al. 1982, doi:10.1016/0038-092X(82)90302-4.
* Perez et al. 1990, doi:10.1016/0038-092X(90)90055-H.
* Gueymard & Ruiz-Arias 2016, doi:10.1016/j.solener.2015.10.010.
* Yang 2022, doi:10.1016/j.rser.2022.112195.
* Engerer 2015, doi:10.1016/j.solener.2015.04.012.
* Lorenzo et al. 2011, doi:10.1002/pip.1085.
* Anderson & Mikofski 2020, doi:10.2172/1660126.
* Faiman 2008, doi:10.1002/pip.813.
* King et al. SAND2004-3535, doi:10.2172/919131.
* Dobos 2014 (PVWatts v5), doi:10.2172/1158421.
* Cordero et al. 2018, *Sci. Rep.* 8:13943,
  doi:10.1038/s41598-018-32291-8.
* Olivares et al. 2017, doi:10.1016/j.egypro.2017.09.263. Olivares et
  al. 2020, doi:10.3390/en13153819.
* Molina et al. 2017, *Sci. Rep.*, doi:10.1038/s41598-017-13761-x
  (Explorador Solar).

Typical years and P50/P90:

* Wilcox & Marion 2008 (TMY3), doi:10.2172/928611.
* Dobos et al. 2012, P50/P90 (NREL, OSTI 1044455).
* IEA PVPS T13-12.
* Thevenard & Pelland 2013, *Solar Energy* 91:432,
  doi:10.1016/j.solener.2011.05.006.
* Pryor et al. 2018, *WES* 3:651, doi:10.5194/wes-3-651-2018.
* Hammond & Simley 2026, *WES* 11:1251,
  doi:10.5194/wes-11-1251-2026.
* Maclaurin et al. 2019 (reV), doi:10.2172/1563140.

Chilean planning practice:

* CNE, *Informe Técnico Preliminar PET 2024*, §7.3.6. PV uses zonal
  measured profiles. Wind uses the Explorador Eólico 37-year series at
  hub height with TradeWind multi-turbine curves, about 3 % electrical
  losses and no density correction.
* MEN, *PELP 2023–2027*, informe definitivo. One representative day
  per month, with Explorador profiles adjusted for climate change.
* CEN NTCO Programación de la Operación (2025.03), Arts. 2-72…2-94:
  forecast duties.
* NTSyCS AT Información Técnica and AT SITR (2025.03): the technical
  and met data plants send to the CEN.
