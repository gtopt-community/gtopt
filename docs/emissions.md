# Emissions and marginal emissions

GTOpt computes the emissions of the system inside the optimization model
— **CO₂ and other greenhouse gases (global pollutants) as well as local
pollutants such as NOₓ, SO₂ or particulate matter** — and can price or
cap them. After the solve it attributes **marginal emissions** to every
bus and hour: how many tonnes one extra MWh of demand at that bus and
time would cause, and from that the emission impact of storage.

## In the model

Emissions are modelled with four elements:

| Element | Role |
|---|---|
| `Emission` | Registry of pollutants: `co2`, `ch4`, `n2o`, `nox`, `so2`, `pm10`, … |
| `EmissionSource` | Links a generator to a zone and a pollutant, with its combustion `rate` and optional `upstream_rate` (well-to-tank) in t/MWh, or `fuel_rate` / `upstream_fuel_rate` in t per fuel unit applied to the fuel burn. Can also be derived from `Fuel.emission_factors[]` × heat rate (piecewise and per-block heat rates and the committed no-load burn `Commitment.noload_fuel` included) |
| `EmissionZone` | Scope of a balance: **system-wide or regional**. Lists the pollutants it covers with a `weight`, and optionally a `cap` (hard, or soft with `cap_cost`) and a per-tonne `price`. `buses` restricts the fuel-derived sources to generators on those buses |
| `Generator.emission_captures[]` | Capture (CCS) per pollutant, with capture rate and cost |

**Global and local pollutants.** A zone covering the whole system with
CO₂, CH₄ and N₂O weighted by their GWP-100 (1, 27.9, 273) accounts for
**greenhouse gases in tCO₂-equivalent**; its `price` is a carbon tax and
its `cap` a carbon budget. **Local pollutants** use their own zones with
the buses or plants of an airshed or region — e.g. a NOₓ or SO₂ cap for
a saturated zone, or a per-tonne price reflecting local damage — and are
accounted in tonnes of that pollutant (weight 1). Both kinds can be
active at the same time, and a generator can feed several zones.

| What you get | Where |
|---|---|
| Emissions per source, pollutant and block | `EmissionSource/emissions_sol`, `combustion_sol` |
| **Endogenous price of a cap** (carbon price or local-pollutant shadow price) | dual of the zone cap row |
| Cost of emissions in the objective | zone `price` × emissions; capture costs |
| Cap-and-trade | `AllowancePool`: allowance pools coupled to emission zones |
| Minimum-emission dispatch | `--set model_options.objective_mode=emissions`: minimizes emissions instead of cost; the storage duals (`Reservoir/water_value_dual`, `Battery/energy_dual`) then give the **carbon opportunity cost** of stored water and energy |

**Imports.** Interconnector import proxies (e.g. the SEM `GB Single Gen
Bid` / `France Bid` units on the `BETTA` / `France` buses) burn fuel in
the model but their CO₂ is emitted outside the market.  Scope the
market zone to the home buses (`"buses": ["SEM"]`) and add a twin zone
with no cap or price scoped to the import buses: the market total then
excludes imports while their tonnes stay visible in the twin's sources.
`plexos2gtopt --emission-import-buses BETTA,France` does this
automatically (`<zone>_imports`).

Example of a system CO₂-eq zone with a carbon tax plus a regional NOₓ
cap:

```json
"emission_array": [
  {"uid": 1, "name": "co2"}, {"uid": 2, "name": "ch4"},
  {"uid": 3, "name": "n2o"}, {"uid": 4, "name": "nox"}
],
"emission_zone_array": [
  {"uid": 1, "name": "ghg",
   "emissions": [{"emission": "co2", "weight": 1.0},
                 {"emission": "ch4", "weight": 27.9},
                 {"emission": "n2o", "weight": 273.0}],
   "price": 50.0},
  {"uid": 2, "name": "nox_north",
   "emissions": [{"emission": "nox", "weight": 1.0}],
   "cap": 120.0, "cap_cost": 20000.0}
],
"emission_source_array": [
  {"uid": 1, "generator": "coal1", "zone": "ghg", "emission": "co2", "rate": 0.95},
  {"uid": 2, "generator": "coal1", "zone": "nox_north", "emission": "nox", "rate": 0.0021}
]
```

## Marginal emissions per bus and hour

`gtopt-marginal-units` reads a solved case and identifies, for every bus
and block, the **marginal unit(s)** that set the price, using the LP
duals, line saturation and the network zones (buses connected by
unsaturated lines share the same marginal unit). From that it derives:

- the **marginal emission factor** ε_b per bus and block (tCO₂-eq/MWh),
  i.e. the marginal operating emission rate (MOER) of extra demand there,
  computed from the CO₂-eq rates of the emission sources;
- the **price recipe** of each bus price (which units and lines set it);
- a **merit ladder** of the units just above and below the marginal one,
  for "what if this unit were out" sensitivity;
- optionally, prices with a carbon adder (`--carbon-price`, USD/tCO₂).

```bash
gtopt case.json --set output_directory=out
gtopt-marginal-units --planning case.json --output out \
  --out marginal/ --report marginal/report.md
```

Main outputs, as a Parquet dataset in `--out`:

| File | Contents |
|---|---|
| `bus_emission_intensity_recipe.parquet` | ε_b per bus and block, and the units it comes from |
| `bus_price_recipe.parquet` | Price per bus and block and its marginal units/lines |
| `merit_ladder.parquet` | ±K units around the marginal one (`--merit-ladder-depth`) |

The dataset can be re-read from Python, including recomputing ε_b or the
prices under a different emission or cost catalogue without re-solving:

```python
from gtopt_marginal_units.consumer import MarginalUnitDataset

ds = MarginalUnitDataset("marginal/")
eps = ds.bus_emission_intensity()                 # t/MWh per bus and block
alt = ds.recompute_emission({101: 0.35, 102: 0.0})  # other factors (t/MWh) per unit uid
lmp = ds.bus_lmp_and_emission(carbon_price=50)    # price + carbon adder
```

Use cases: emission factors for hourly carbon accounting (Scope 2
market-based / 24×7 matching), siting of new demand or storage where it
avoids most emissions, and carbon-price sensitivity of nodal prices.

Run `gtopt-marginal-units --help` for all options (tolerances, zone mode,
single-bus mode, report size). Zones: `--zone-mode congestion|physical|both`.

## Emission impact of storage (marginal-emissions method)

The emission impact of a storage unit can be measured with the
marginal-emissions approach of the Verra draft methodology **CN0157,
*Methodology for Grid-Connected Energy Storage Systems*** (REsurety et
al., public consultation February–April 2025; development archived by
Verra in January 2026) [1]. The operation of the storage in each interval
*i* is valued at the marginal emission rate (MER) of its location:

$$PE = -\sum_i A_i \, E_i$$

where $A_i$ is the net dispatch of the storage (MWh, positive when
discharging, negative when charging) and $E_i$ the MER at its bus
(tCO₂/MWh). $PE < 0$ means the storage reduces system emissions: it
charges when the marginal unit is clean and discharges when it is
carbon-intensive. Because of round-trip losses, a storage that ignores
emissions can also *increase* them.

The reduction attributable to emission-aware operation compares two
dispatches of the same storage:

$$BE = -\sum_i A^{\text{base}}_i E_i, \qquad ER = BE - PE$$

where $A^{\text{base}}$ is the revenue-maximizing dispatch (carbon price
0). CN0157 additionally applies a discount factor based on forecast
error, which is out of scope here.

All the inputs come from a GTOpt run:

| Term | GTOpt source |
|---|---|
| $E_i$, MER per bus and block | `gtopt-marginal-units` (`bus_emission_intensity_recipe.parquet`, t/MWh). It meets the CN0157 method criteria: any fuel can be marginal, including renewables (MER 0 under curtailment), and transmission constraints are accounted for through the congestion zones |
| $A_i$, storage dispatch | Grid-side discharge minus charge per block: the battery's converter generator and demand, or `Battery/fout_sol` − `Battery/finp_sol` for a standalone battery |
| $A^{\text{base}}_i$ | Run without carbon price (zone `price` 0) |
| Emission-aware dispatch | Run with a carbon price (zone `price` > 0), or with `objective_mode = emissions` for the minimum-emission bound |
| Interval | Block (hourly or sub-hourly, as in the case) |

`gtopt-marginal-units storage-impact` does the whole computation:

```bash
gtopt case_base.json --set output_directory=base   # carbon price 0
gtopt case_co2.json  --set output_directory=co2    # with carbon price
gtopt-marginal-units --planning case_base.json --output base --out mer
gtopt-marginal-units storage-impact \
  --project-output co2 --baseline-output base --mer mer \
  --csv impact.csv --json impact.json
```

It prints, per storage unit, the energy charged and discharged, the
average MER while charging and discharging, and PE, BE and ER in tCO₂-eq
(probability-weighted over scenarios); `--csv` writes the per-block
table. Use the MER of the baseline (or of the actual grid) run: it is the
grid signal the storage responds to.

The same product, summed over a year, gives the **contribution of each
storage unit to the CO₂ reduction of the system**, and comparing cases
with and without the storage gives the system-level effect, including
the reduction of renewable curtailment.

[1] Verra, *CN0157 Methodology for Grid-Connected Energy Storage
Systems*, draft for public consultation, 26 February 2025.
<https://verra.org/methodologies/methodology-for-grid-connected-energy-storage-systems/>
