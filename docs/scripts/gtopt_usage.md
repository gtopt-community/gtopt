# gtopt_usage — Usage Accounting, Maintenance Cost and SSCC θ

Turns gtopt output directories into per-unit **usage** (operating hours,
energy, hours at the committed minimum, starts / shutdowns, ramp mileage,
reserve MW·h held per service, BESS throughput and equivalent full cycles),
converts usage into **maintenance cost** with per-technology OEM / NTCyO
parameters, and derives the **SSCC wear cost θ** per technology, service and
direction from a run without and a run with ancillary services.

Run gtopt with `options.output_usage_metrics = true` so the indicator and
mileage streams exist ([usage.md](../usage.md#usage-accounting-output-output_usage_metrics)).
Without them the operating hours fall back to `generation > 1e-6 MW` (with a
warning) and the ramp mileage / pmin hours read 0.

Runs are aligned with [`gtopt_compare_runs`](gtopt_compare_runs.md): elements
by name, time by scenario / stage / block position; every metric is the
expectation over the scenarios (normalised probabilities).

---

## Usage

```bash
gtopt_usage [--hours-per-year H] [--pmin-band FRAC] usage RUN \
    [--per stage|year|horizon] [--params maint.json] [--csv DIR]
gtopt_usage [--hours-per-year H] [--pmin-band FRAC] theta BASE SSCC \
    --params maint.json [--csv DIR] [--json PATH]
```

| Option | Effect |
|--------|--------|
| `--per` | Aggregation of `usage`: per stage, per year (default; 8760 h years from the horizon start) or the whole horizon |
| `--params` | Maintenance parameter file (below) |
| `--pmin-band FRAC` | Count hours with `p ≤ Pmin·u·(1+FRAC)` as "near pmin" (uses `Commitment/usage_pmin_sol`), instead of gtopt's at-floor indicator |
| `--hours-per-year` | Length of an accounting year (default 8760) |
| `--csv` / `--json` | Write the tables |

Exit codes: 0 ok, 2 bad input (missing directory, bad parameter file).

## Usage metrics

| Column | Source | Definition |
|--------|--------|------------|
| `hours` | `Generator/usage_on_sol` | Σ on·h |
| `energy_mwh` | `Generator/generation_sol` | Σ p·h |
| `pmin_hours` | `Commitment/usage_at_pmin_sol` | Σ (u at floor)·h |
| `starts`, `shutdowns` | `Commitment/startup_sol`, `shutdown_sol` | Σ v, Σ w |
| `ramp_mw` | `Generator/usage_ramp_sol` | Σ \|Δp\| (chronological stages) |
| `reserve_up_mwh`, `reserve_dn_mwh` | `ReserveProvision/uprovision_sol`, `dprovision_sol` | Σ r·h, by generator |
| `charge_mwh`, `discharge_mwh`, `throughput_mwh` | `Battery/finp_sol`, `fout_sol` | Σ·h, throughput = (charge + discharge)/2 |
| `efc` | battery `capacity` / `emax` | discharge / usable energy capacity |

`reserve_table` keeps the reserve per provision and **service**: the zone
name of the provision, or the zone names joined by `+` for a provision that
serves nested zones.  `known_cost_table` reads the known variable reserve
cost streams written by gtopt per provision
(`ReserveProvision/{up,dn}_{partload,activation,degradation}_cost_sol`).

## Maintenance model

Definitions of the NTCyO Capítulo Costos Variables (CNE 2023, arts. 2-33 to
2-42): HOM = operating hours between expected preventive maintenances,
HEOM = HOM − Nf·Kf − Nd·Kd, COM = storage cycles between maintenances.

```
EOH    = hours + Kd·starts + Kf·Nf + a_pmin·pmin_hours + a_ramp·ramp_mw
         + Σ_s a_res[s]·reserve_mwh[s]           (Nf = failures_per_year · hours/H)
events = max(EOH/interval_eoh, starts/interval_starts, efc/interval_cycles)
cost   = events · cost_per_event,   outage_hours = events · outage_days · 24
HEOM   = interval_eoh · hours / EOH
```

`events` is fractional (expected share of the next maintenance over the
period) and takes the first interval reached (OEM "whichever occurs first");
it is not additive, so it is computed at the reported aggregation level.

Parameter file:

```json
{
  "technologies": {
    "gas_turbine": {"interval_eoh": 24000, "interval_starts": 1200,
                    "cost_per_event": 2.5e6, "kd": 10, "kf": 50,
                    "failures_per_year": 1, "outage_days": 30,
                    "eoh_per_pmin_hour": 0.0,
                    "eoh_per_reserve_mwh": {"cpf": {"up": 0.02, "dn": 0.01},
                                            "csf_up": 0.01, "*": 0.0}},
    "bess": {"interval_cycles": 4500, "cost_per_event": 1.0e6}
  },
  "units": {"NEHUENCO_2": {"technology": "gas_turbine", "kd": 20}},
  "service_weights": {"cpf": {"up": 2.0, "dn": 1.0}, "csf": 1.0, "ctf": 1.0}
}
```

Reserve wear differs between the six SSCC products (CPF, CSF and CTF, each
up and down).  `eoh_per_reserve_mwh`, `alpha_reserve` and `service_weights`
take a value per product, nested (`{"cpf": {"up": a, "dn": b}}`) or flat
(`"cpf_up"`, `"cpf_dn"`), per service (`"cpf"`: both directions, the form
of earlier versions) or `"*"` (any; `"*_up"` / `"*_dn"` per direction).
The lookup order is `service_direction` → `service` → `*_direction` → `*`;
an unset weight is 1 and an unset wear factor 0.

The technology of a unit is its `Generator.type` / `Battery.type`, unless a
`units` entry overrides it.  Unknown keys, non-positive intervals and
negative factors are rejected.

## θ from two runs

```
ΔC_tech   = Σ_g [C_g(B) − C_g(A)]                (whole-horizon maintenance cost)
ΔR_tech,p = R_tech,p(B) − R_tech,p(A)            reserve MW·h of product p
θ_tech,p  = ΔC_tech · w_p / Σ_p' w_p'·ΔR_tech,p'   [$/MW·h]
```

A product `p = (service, direction)` is one of the six SSCC products, so
`theta.csv` has one row per technology, service and direction (six per
technology when all products are held) and `Σ_p θ_tech,p·ΔR_tech,p =
ΔC_tech`.  With the default weights `w_p = 1` every product of a technology
gets the same θ: a single delta cannot tell the products apart.  A θ that
differs per product needs one B run per product (each compared with A) or
explicit per-direction `service_weights`; the per-direction wear factors
(`eoh_per_reserve_mwh`) change ΔC itself.  The known variable
costs written by gtopt are reported next to it
(`theta_known_{partload,activation,degradation}` = Σ $ / R(B)) and
`theta_total = theta_known + theta_maintenance`.

Tables: `units.csv` (per-unit A, B and deltas of hours, starts, pmin hours,
mileage, EFC, EOH, events, cost, outage hours), `technologies.csv` and
`theta.csv`.

## Incremental maintenance: counters and calendar

`gtopt_usage maintenance BASE SSCC --counters wear.json` keeps
**maintenance counters** per unit and maintenance type and prices only the
**difference** between a run without SSCC (A) and the same case with SSCC
(B): scheduled maintenance is sunk.  The existing `theta` subcommand is
unchanged.

### Usage per period (one gtopt stage)

Thermal units count equivalent operating hours (NTCyO naming: HOM is the
OEM interval in operating hours, HEOM the hours actually reached, Kd the
EOH per start, Kf the EOH per trip):

```
EOH = Σ_blocks on·h·w(p) + c_pmin·h_pmin + Kd·starts + Kf·trips
      + Σ_s α_s · R_s / Pmax
w(p) = a + b·(p − Pmin)/(Pmax − Pmin)        p clipped to [Pmin, Pmax]
```

- `b` may be negative (a low-load penalty: more wear near Pmin).  The
  weight is validated non-negative on the whole range: `a >= 0` and
  `a + b >= 0`.
- `trips = trips_per_year x years` is an exogenous input (default 0).
- `R_s / Pmax` counts equivalent full-reserve hours of service `s`.
- Batteries count equivalent full cycles `EFC·(DoD/dod_ref)^dod_exponent`
  instead (DoD per stage from `Battery/energy_sol`; exponent 0 = off).

### Counters

Each type `k` (ordered lowest first, e.g. `CI` < `HGP` < `major`) is due
when the first of its intervals is reached ("whichever comes first"):

```
ratio_k = max(EOH/interval_eoh, starts/interval_starts,
              cycles/interval_cycles, years/max_years)
```

- An event of type `k` resets type `k` and every lower type (hierarchical
  reset) to `reset_fraction` x the counter (imperfect maintenance, default
  0).
- Initial counters come from `units.<name>.initial` or `--initial CSV`
  (`unit,type,eoh,starts,cycles,years`; default 0).
- Usage accrues uniformly inside a stage.

### Calendar

The calendar is a list of fixed events that reset the counters at their
dates.  It comes from one of two sources:

- `--calendar CSV` (`unit,type,start,duration`): `start` is hours from the
  start of the horizon, or an ISO date with `--start-date`; `duration` is
  in days.
- By default, the `Generator/outage_fraction_sol` stream of run A (written
  for `plp2gtopt --mance-split`).  Each run of contiguous blocks with
  `f > 0` is one event, of type `--outage-type` or else the unit's highest
  type.

When a counter reaches 1 before the next calendar event of its level or
higher, the cheaper of two options is taken:

- **advance** that event by Δt, priced `C·[1 − (1+r)^−Δt]` and discounted
  from the advanced date;
- an **extra** event of the counter's type at that time.

The calendar stays fixed: the advanced event still resets the counters at
its scheduled date.  Each calendar event is reported as:

- `late`, with the required advancement `advance_years`;
- `early`, with `life_wasted = 1 − ratio`;
- `consistent`.

### Cost (B − A)

| Method | Cost of one run |
|--------|-----------------|
| rate   | `EOH · Σ_k C_k / interval_eoh_k + EFC · Σ_k C_k / interval_cycles_k` |
| event  | PV(extra events) + PV(advancement) + PV(replacement energy of extra outages) + terminal value `Σ_k min(ratio_k, 1)·C_k` at the horizon end |

- Discounting uses `discount_rate` (counters file or `--discount-rate`;
  default 0.06).
- The replacement price is `replacement_price`, or else the expected bus
  marginal cost (`Bus/balance_dual`) at the unit's bus in that stage.
- Per technology the tool reports `gross` (sum of the positive unit
  deltas), `savings` (sum of the negative ones) and `net`.
- θ per technology, service and direction is the net cost allocated over
  the six SSCC products by the per-direction `service_weights` and divided
  by the reserve MW·h delta of that product (`alpha_reserve` takes the
  same per-direction keys).  It is shown for both methods side by side (`theta_rate`,
  `theta_event`).

```json
{"discount_rate": 0.06,
 "technologies": {
   "gas_turbine": {
     "kd": 20, "kf": 50, "trips_per_year": 0,
     "load_weight": {"a": 1.0, "b": -0.3},
     "eoh_per_pmin_hour": 0.2,
     "alpha_reserve": {"cpf": {"up": 0.5, "dn": 0.2}, "csf": 0.3},
     "reset_fraction": 0.0,
     "types": [
       {"name": "CI",    "interval_eoh": 8000,  "interval_starts": 300,
        "max_years": 2, "cost": 1.0e6, "outage_days": 5},
       {"name": "HGP",   "interval_eoh": 24000, "interval_starts": 900,
        "cost": 4.0e6, "outage_days": 20},
       {"name": "major", "interval_eoh": 48000, "interval_starts": 1800,
        "cost": 9.0e6, "outage_days": 40}]},
   "bess": {"dod_exponent": 1.0, "dod_ref": 0.8,
            "types": [{"name": "cells", "interval_cycles": 6000,
                       "cost": 2.0e7}]}},
 "units": {"NEHUENCO_2": {"technology": "gas_turbine",
                          "pmin": 150, "pmax": 390,
                          "initial": {"CI": {"eoh": 5200, "starts": 120}}}},
 "service_weights": {"cpf": {"up": 2.0, "dn": 1.0}, "csf": 1.0}}
```

```bash
gtopt_usage maintenance no_sscc/results sscc/results --counters wear.json \
  --calendar calendar.csv --start-date 2027-01-01 --csv maint/
```

The tables `maintenance_{units,technologies,theta,events}.csv` (and
`--json`) hold:

- per unit: EOH / EFC of A and B, `rate_delta`, `event_delta` and its
  parts (extra, advance, replacement, terminal);
- per technology: gross, savings and net for both methods;
- per technology, service and direction: θ for both methods;
- every event of both runs (type, source, status, ratio, life wasted,
  advancement, PV).

## Python API

```python
from gtopt_compare_runs import Run
from gtopt_usage import derive_theta, load_params, usage_table

run = Run.load("case/results")
usage = usage_table(run)                      # per unit and stage
rep = derive_theta(Run.load("a/results"), Run.load("b/results"),
                   load_params("maint.json"))
rep.theta                       # θ per technology, service and direction
```
