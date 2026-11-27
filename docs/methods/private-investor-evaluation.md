# Private-Investor Evaluation — Would Anybody Build the Plan?

gtopt's expansion plan is the least-cost plan of a central planner.  In
a market, each project is built only if somebody finances it, and the
investor is paid by the market's rules, not by the LP's duals.  This
page describes how `gtopt_private_eval` checks, project by project,
whether the plan's selected projects are viable for a private investor
under a given **market model**, and how that private view differs from
the planning view of
[investment economics](investment-economics.md).

The Chilean scheme (energy at *costo marginal*, *pago por potencia de
suficiencia*, SSCC, *impuesto verde*, transmission charges) is
documented in depth in
[private-investor-evaluation-cl.md](private-investor-evaluation-cl.md).

## 1. Planning view vs private view

In the LP every built project inside its bounds earns, at the marginal
prices of the solution, exactly its operating cost plus its annuity
$K = I \cdot \mathrm{CRF}(\rho, n)$ ([investment
economics](investment-economics.md) §1–§5).  A private investor sees
something else:

| | Planning view (gtopt LP) | Private view (`gtopt_private_eval`) |
|---|---|---|
| Revenue | duals of the LP: energy, reserves, emission caps | the market's rules: energy at nodal prices plus capacity payments, SSCC, contracts, regulated tariffs |
| Costs | `gcost`, `annual_capcost` | variable cost + extra VOM + fixed O&M + insurance + warranty / LTSA + guarantee fees + market charges (§1.2) |
| Investment | annuity at the per-candidate `wacc` | overnight capex in year 0, private WACC |
| Time | representative horizon, discounted stages | yearly cash flows over the economic life |
| Result | profit = margin − annuity (0 in the LP) | EBITDA, EBIT, NPV, IRR, payback, LCOE |

**The analysis is pre-tax (EBIT).**  Revenues minus operating costs give
EBITDA; minus straight-line depreciation over the economic life give
EBIT.  The cash flow of year $y \ge 1$ is EBITDA, year 0 carries the
overnight capex; NPV and IRR are computed on these pre-tax flows at the
project's **pre-tax** private WACC.  There is no income tax and no tax
shield.  This is deliberate: gtopt's annuities are pre-tax too, so the
private NPV and the planning annuity are directly comparable — with no
fixed O&M, insurance or guarantees, the energy-only market and the private WACC equal to the
planning rate, every project the LP builds has NPV = 0 and IRR = WACC
(checked on every `invest_*` golden, `test_goldens.py`).

### 1.1 Formulas

Per project, for one representative year (the solved horizon is scaled
by $8760/T$; every period weight is duration × scenario probability):

$$
R_{\text{energy}} = \sum_t \lambda_{b,t}\ (g_t - d_t)\ h_t, \qquad
C_{\text{var}} = \sum_t (c_t g_t + c^{\text{ch}}_t d_t)\ h_t,
$$

with $\lambda_{b,t}$ the bus price, $g_t$ / $d_t$ injection and
withdrawal of the **new** capacity (an expanded existing element keeps
the share new/installed).  The cost lines of §1.2 complete $C$.

$$
\mathrm{EBITDA}_y = \sum_k R_{k,y} - \sum_k C_{k,y}, \qquad
\mathrm{EBIT}_y = \mathrm{EBITDA}_y - I/n,
$$

$$
\mathrm{NPV} = -I + \sum_{y=1}^{n} \frac{\mathrm{EBITDA}_y}{(1+r)^y},
\qquad \mathrm{NPV}(\mathrm{IRR}) = 0,
$$

$$
\mathrm{LCOE} = \frac{I + \sum_y C_y/(1+r)^y}{\sum_y E_y/(1+r)^y},
\qquad
p^{\text{capt}} = \frac{\sum_t \lambda_{b,t}\  g_t\  h_t}{\sum_t g_t\  h_t}.
$$

The **bridge** (year 1) explains the gap between the two views:

$$
\underbrace{\mathrm{EBITDA}_1 - I\ \mathrm{CRF}(r,n)}_{\text{private surplus}}
= \underbrace{(M_{\text{LP}} - K_{\text{LP}})}_{\text{planning profit}} +
\sum \text{extra revenues} - \sum \text{extra costs} -
\bigl(I\ \mathrm{CRF}(r,n) - K_{\text{LP}}\bigr).
$$

### 1.2 Fixed costs: O&M, insurance, warranties, guarantees

Each is a separate EBIT line, looked up project → technology →
`finance` default (`defaults.py`, all with a source key):

| Line | Default | Basis | Source |
|---|---|---|---|
| `fixed_om` | 2 % of capex / yr | CNE ICTGA 2026: CCGT, PV, wind, BESS 1–2 %, OCGT 2–3 % (insurance not itemised) | [C23] |
| `insurance` (all-risk property + business interruption + liability) | PV 0.25 %, wind 0.25 %, BESS 0.40 %, thermal 0.30 %, hydro 0.25 %, lines 0.10 % of capex / yr | PV 0.10–0.30 % [G19]; wind 2.6 \$/kW-yr ≈ 0.15–0.2 % [G20]; CNE peak unit 0.3 % [C6]; BESS, hydro, lines: assumptions | [G19], [G20], [C6] |
| `warranty` (extended OEM warranty, LTSA, augmentation) | BESS 0.5 % of capex / yr; others 0 (inside fixed O&M or VOM) | ATB BESS fixed O&M 2.5 % incl. augmentation [G14]; PNNL NMC extended warranty ≈ 3 \$/kWh-yr [G21]; assumption | [G14], [G21] |
| `guarantee_fee` (generic bank guarantee / standby LC) | amount `guarantee_pct_capex` × fee 1 %/yr; amount 0 by default | assumption (one Chilean bank sheet: fee up to 1 % [C34]) | [C34] |
| market guarantees | Chile: CEN payment guarantee, supply-contract bond | in the market model | [private-investor-evaluation-cl.md](private-investor-evaluation-cl.md#16-guarantees) |

Regulated lines recover insurance through the COMA (`regulated_coma` =
capex × (fixed O&M + insurance)).

**Fixed O&M in the LP.**  When the solved case already charges fixed
O&M (a fixed-cost field on the element — `fixed_om_cost`,
`annual_fixed_om`, `fom_cost`, `annual_fom` or `fixed_om`, \$/MW-yr or
\$/MWh-yr; `finance.case_fom_fields` overrides the list), the tool
uses the case's value instead of its 2 % (`finance.fom_in_case:
"auto"`, the default); `true` declares that `annual_capcost` already
includes it (the tool adds none), `false` always applies the
percentage.  `finance.risk_costs: false` drops insurance, warranty and
guarantee lines (the zero-profit test uses it).

### 1.3 CO2 value and marginal emissions

The marginal emission factor $\varepsilon_{b,t}$ (t/MWh) is the emission
rate of the unit(s) setting the price of bus $b$ in period $t$: running
generators with variable cost equal to the price, in the same price
zone; if none matches (price set by storage, a line or unserved energy),
the running unit of the zone with the closest cost.  This is the light
version of `gtopt-marginal-units` ([emissions](../emissions.md)); its
output can be given with `--mef-file`.  With a carbon price $p$ passed
through to the marginal price (dispatch unchanged):

$$
V_{\text{CO2}} = p \sum_t h_t\ (\varepsilon_{b,t}\ (g_t - d_t) - e\  g_t),
$$

$e$ the project's own rate.  For a battery ($e = 0$) this is the
**emission arbitrage**: charging at low-$\varepsilon$ hours,
discharging at high-$\varepsilon$ ones.  Every evaluation reports the
NPV at $p \in \lbrace 0, 5, 25, 50, 100 \rbrace$ \$/t (5 \$/t = Chile's green tax).

### 1.4 Capex trajectories

A project commissioned in a later year pays
$I(y) = I_{\text{base}} \cdot X(y)/X(y_0)$, where the **level**
$I_{\text{base}}$ is local (Chile: CNE ICTGA 2026 [C23]) and the **shape**
$X$ is a dimensionless index from NREL ATB 2024 [G14]
(Conservative / Moderate / Advanced; OCC, Market case, normalised to
2025; IEA WEO STEPS / APS / NZE indices can be passed as a user table).
`gtopt_private_eval capex-trajectory` prints the table and writes it as
a per-stage `overnight_capcost` schedule into a planning file, so a
multi-stage gtopt case sees declining costs:

```bash
gtopt_private_eval capex-trajectory --tech bess --tech solar_pv \
  --scenario moderate --scenario advanced --years 2026 2030 2035
gtopt_private_eval capex-trajectory --planning case.json \
  --map pv1=solar_pv --map bess1=bess --stage-years 2026 2030 2035 \
  -o case_capex.json
gtopt_private_eval capex-trajectory --tech wind \
  --atb-csv https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/2024/v4.0.0/ATBe.csv
```

| ATB 2024 index (Moderate) | 2025 | 2027 | 2030 | 2035 | 2040 | 2050 |
|---|--:|--:|--:|--:|--:|--:|
| Utility PV (Class 5) | 1.00 | 0.92 | 0.79 | 0.58 | 0.53 | 0.42 |
| Land-based wind (Class 4) | 1.00 | 0.96 | 0.89 | 0.84 | 0.79 | 0.69 |
| Li-ion battery 4 h | 1.00 | 0.94 | 0.84 | 0.77 | 0.71 | 0.58 |
| NG CT F-frame | 1.00 | 0.98 | 0.96 | 0.92 | 0.88 | 0.80 |
| NG CC F-frame | 1.00 | 0.98 | 0.96 | 0.91 | 0.87 | 0.80 |

Caveats: ATB is in constant 2022 USD (apply the index to a real local
base, escalate separately in a nominal model); the local base carries
the Chilean premium or discount (grid connection, logistics, land);
battery declines are the most uncertain (ATB's Conservative BESS index
is below Moderate until about 2032).  Each project's NPV under the three
scenarios at the sensitivity year (default 2030) is in the report.

## 2. Market remuneration models

The table surveys how the main designs pay a unit; the five models in
**bold** are implemented, the rest are documented only.

| Model | Where | Unit revenue | Key parameters (recent) | Refs |
|---|---|---|---|---|
| **Energy-only + scarcity / ORDC** (`energy_only`) | ERCOT; any nodal market | $\sum_t (\lambda_t + A_t) q_t$, $A_t = \mathrm{LOLP}\cdot(\mathrm{VOLL}-\lambda_t)$ | ERCOT offer cap 5,000 \$/MWh; PNM trigger 3×CONE | [G1–G3] |
| **Forward capacity market** (`capacity_market`) | PJM RPM, ISO-NE FCM, GB CM, PL, IE, IT | $P_{\text{cap}} \cdot K \cdot d$ − penalties | PJM 2027/28: 333.44 \$/MW-day; GB T-4 2029/30: 27.10 £/kW-yr; PJM ELCC 2027/28: CC 74 %, CT 61 %, storage 4 h 58 % | [G4–G7] |
| **Reliability option** (`reliability_option`) | Italy, I-SEM, Belgium | $P_{\text{cap}} K d - \sum_t \max(\lambda_t - S, 0) K d$ | Italy 2027: 47,000 €/MW-yr; strike = peaker variable cost | [G8–G9] |
| **Two-sided CfD** (`cfd`) | UK CfD, EU Reg. 2024/1747 | $\lambda_t q_t + (S - \lambda^{\text{ref}}_t) q_t$ | UK AR7 offshore wind 90.91 £/MWh (2024 prices), 15 y; EU: mandatory for new support from 17 Jul 2027 | [G10–G11] |
| **Administered capacity payment** (`cl`) | Chile, Spain (legacy), Colombia | Chile: $`P^{\text{suf}}_{\text{def}} \cdot P_{\text{nudo,pot}}`$; Colombia: $\mathrm{CxC}\cdot\mathrm{OEF}$ with price capped at the *precio de escasez* | Chile 9,231.51 CLP/kW-month (jul-2026); Spain 26,000 €/MW-yr (ITC/3127/2011); Colombia CxC 2027-28: 18.2 \$/MWh firm | [C1–C2], [G12] |
| Strategic reserve | Germany *Kapazitätsreserve*, Belgium (to 2021/22) | fixed €/MW-yr out of the market; no energy revenue | DE 1,205 MW contracted 2024-26 | [G13] |
| Long-term supply auctions / PPAs | Chile *licitaciones*, Brazil *leilões*, corporate PPAs | pay-as-bid $p_c q$; baseload $p_c Q + \sum \lambda_t (q_t - Q)$ | Chile 2025/01: 64.499 \$/MWh; Brazil LRCAP: R\$2.33 M/MW-yr | [G15] |
| Regulated transmission | Chile VATT, building-block RAB | $\mathrm{AVI} + \mathrm{COMA}$ (+AEIR); $\mathrm{WACC}\cdot\mathrm{RAB} + \mathrm{Dep} + \mathrm{Opex}$ | Chile art. 118 rate 7 % after tax 2024-27 | [C5] |
| Merchant transmission | US merchant DC, EU interconnectors | $\sum_t (\lambda_j - \lambda_i) f_t$ or FTR proceeds | free-riding, lumpiness, market power | [G16] |
| Storage stacking | all | $\sum \lambda_t(d_t - c_t) + \sum$ AS $+ P_{\text{cap}} K d(h)$ | accreditation by duration (PJM, GB, Chile DS 70) | [G17–G18] |

Lines are treated, in every model, as either **merchant** (congestion
rent net of transfer cost on the new MW) or **regulated** (AVI at the
regulated rate + COMA); `transmission` selects it.  An after-tax
regulated rate $r$ is grossed up to the pre-tax $r/(1-t)$ — in Chile the
VATT's AEIR component returns the income tax.

Notes on the implemented models:

* `capacity_market` — `price_per_mw_year` × MW × `derating[tech]`
  (storage: `storage_derating` by duration, linear between the PJM
  classes; 0 h = 0 is an assumption below the 4-h class);
  `contract_years` then `price_after` (GB new build: 15-year
  agreements).  Default price = PJM 2027/28 cap (121,706 \$/MW-yr).
* `reliability_option` — the same premium minus the hourly refund above
  `strike_price` on the derated MW, produced or not.  It swaps scarcity
  rents (λ up to `demand_fail_cost`) for a premium.
* `cfd` — `strike` a number, a per-project map, or `"lcoe"` (each
  project's own pre-tax LCOE: the break-even strike of a whole-life
  contract); the reference price is the hourly bus price; merchant after
  `contract_years`.  Storage and lines get no CfD.

### 2.1 The market-model interface

```python
class MarketModel:
    name: ClassVar[str]          # --market NAME
    title: ClassVar[str]
    def revenues(self, ctx: EvalContext, proj: ProjectData) -> Components
    def costs(self, ctx: EvalContext, proj: ProjectData) -> Components
    def notes(self) -> list[str]
```

`Components` maps a name to $/yr (constant) or a per-year list (the
last value repeats).  The base class already returns energy, reserve
duals, the CO2 value (when `co2.in_cash_flow` and `co2.price > 0`) and
merchant / regulated transmission.  `EvalContext` gives the system
data (prices, dispatch, peak demand, all units), the parameters, the
marginal emission factors, and each project's technology, capex, life,
WACC and LCOE.  **Adding a country** = one module in
`scripts/gtopt_private_eval/markets/`, registered in
`markets/__init__.py`, plus its parameter block in `defaults.py` with a
source for every value, plus tests.

### 2.2 Parameters

Defaults live in `scripts/gtopt_private_eval/defaults.py`; every
regulator or operator value is one cited parameter (`sources` table in
the JSON report).  A JSON or YAML file given with `--config` is deep-
merged over them; `--set key.path=value` overrides one value.

```json
{
  "finance": {"wacc": 0.08, "fom_pct_capex": 0.02,
              "capex_trajectory": {"scenario": "moderate", "base_year": 2026,
                                   "cod_year": 2026, "sensitivity_year": 2030}},
  "co2": {"price": 0.0, "sensitivity": [0, 5, 25, 50, 100]},
  "projects": {"bess": {"technology": "bess", "overnight_capcost": 200000,
                        "power_overnight_capcost": 100000,
                        "economic_life": 15, "wacc": 0.09, "cod_year": 2028}},
  "markets": {"cl": {"capacity_price_usd_kw_month": 10.28}}
}
```

Per project: `technology` (else guessed from the name), overnight cost
(else the element's `overnight_capcost`, else `annual_capcost` / CRF),
`economic_life`, `wacc` (else the element's per-candidate `wacc`, else
`finance.wacc`), `fom_pct_capex`, `vom` (extra, on top of `gcost`),
`emission_rate`, `cod_year`.  Example configurations for the
investment cases are in `scripts/gtopt_private_eval/configs/`.

```bash
gtopt_private_eval cases/invest_bat_1b/golden/lp \
  --config scripts/gtopt_private_eval/configs/invest_bat.json \
  --market cl --market energy_only --json-out bat.json --csv-out bat.csv
```

The CSV has one row per project and year: revenues and costs by
component, EBITDA, depreciation, EBIT, EBIT margin.

## 3. Results on the investment cases

All numbers from the committed HiGHS goldens of `cases/invest_*`
(`gtopt_private_eval <golden> --config configs/<case>.json --market
...`), the case's own overnight costs and lives, fixed O&M 2 %,
insurance and warranty per §1.2, private WACC = the case's rate (8 %
unless the case sets a per-candidate `wacc`).  Cells: **NPV M\$ / IRR
%**.  *Plan profit* is the LP's margin − annuity per year (0 for an LP).
The `cl` column never pays scarcity rents (prices above the costliest
generator's cost are removed, §3.1 item 2).

| Case | Project | Capex M\$ | WACC | Plan profit M\$/yr | energy_only | cl | capacity_market | reliability_option | cfd (lcoe, 15 y) |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| screening LP (VOLL 1,000) | base (coal) | 1,650 | 8 % | 0.00 | −427.2 / 5.1 | −282.4 / 6.1 | 198.2 / 9.2 | −369.3 / 5.5 | −102.4 / 7.3 |
| | mid (CCGT) | 165 | 8 % | 0.00 | −40.5 / 5.0 | 43.7 / 11.0 | 103.7 / 14.8 | −27.2 / 6.0 | −8.0 / 7.4 |
| | peak (OCGT) | 125 | 8 % | 0.00 | −28.2 / 4.8 | 123.3 / 19.7 | 154.0 / 22.3 | −11.3 / 6.8 | −3.6 / 7.6 |
| screening private | base | 1,200 | 11 % | 0.00 | −239.9 / 8.4 | −189.0 / 8.9 | 111.3 / 12.2 | −218.5 / 8.6 | −41.5 / 10.5 |
| | mid | 330 | 8 % | 0.00 | −81.0 / 5.0 | 65.8 / 10.3 | 207.4 / 14.8 | −63.4 / 5.7 | −16.1 / 7.4 |
| | peak | 125 | 9 % | 0.00 | −26.2 / 5.9 | 105.9 / 19.7 | 143.2 / 23.1 | −15.9 / 7.2 | −3.1 / 8.6 |
| screening MIP | base | 1,800 | 8 % | −56.87 | −1,106.3 / 0.2 | −1,069.9 / 0.5 | −424.0 / 5.4 | −1,156.8 / −0.3 | −265.2 / 6.0 |
| | mid | 220 | 8 % | −0.13 | −55.4 / 4.9 | 16.8 / 8.9 | 136.9 / 14.7 | −69.6 / 4.0 | −11.0 / 7.4 |
| | peak | 50 | 8 % | 2.03 | 8.6 / 10.2 | 50.2 / 19.9 | 81.5 / 26.5 | 3.2 / 8.9 | 1.1 / 8.3 |
| wacc uniform LP | base_large | 1,430 | 8 % | 0.00 | −370.3 / 5.1 | −225.5 / 6.3 | 255.2 / 9.8 | −312.3 / 5.6 | −88.7 / 7.3 |
| wacc per-candidate LP | base_small | 1,760 | 7 % | 0.00 | −502.3 / 4.0 | −342.7 / 5.0 | 187.1 / 8.0 | −438.5 / 4.4 | −133.6 / 6.2 |
| wacc per-candidate MIP | base_small | 1,600 | 7 % | 9.41 | −339.9 / 4.8 | −328.7 / 4.9 | 286.9 / 8.7 | −386.2 / 4.5 | −90.4 / 6.4 |
| battery LP | bess 84 MW / 289 MWh | 66.2 | 8 % | 0.00 | −16.4 / 3.7 | 67.2 / 22.4 | 27.2 / 14.3 | 0.4 / 8.1 | −16.4 / 3.7 |
| battery MIP | bess 63 MW / 250 MWh | 56.3 | 8 % | 0.66 | −8.4 / 5.5 | 59.4 / 22.9 | 29.4 / 15.8 | 6.2 / 9.8 | −8.4 / 5.5 |
| Garver LP | l2_6 (+400 MW) | 120.0 | 8 % | 0.00 | −30.1 / 5.6 | 20.8 / 9.6 | −30.1 / 5.6 | −30.1 / 5.6 | −30.1 / 5.6 |
| | l4_6 (+200 MW) | 60.0 | 8 % | 0.00 | −15.0 / 5.6 | 10.4 / 9.6 | −15.0 / 5.6 | −15.0 / 5.6 | −15.0 / 5.6 |
| Garver MIP | l1_5 (1 circuit) | 20.0 | 8 % | −1.68 | −25.0 / – | 3.5 / 9.6 | −25.0 / – | −25.0 / – | −25.0 / – |
| | l2_6 (3 circuits) | 90.0 | 8 % | −3.05 | −58.9 / 0.7 | 15.6 / 9.6 | −58.9 / 0.7 | −58.9 / 0.7 | −58.9 / 0.7 |
| **Chile screening LP** (costo de falla 795.11, no failure) | base | 1,650 | 8 % | 0.00 | −427.2 / 5.1 | −282.4 / 6.1 | 198.2 / 9.2 | −369.3 / 5.5 | −102.4 / 7.3 |
| | mid | 165 | 8 % | 0.00 | −40.5 / 5.0 | 43.7 / 11.0 | 103.7 / 14.8 | −27.2 / 6.0 | −8.0 / 7.4 |
| | peak (300 MW) | 150 | 8 % | 0.00 | −33.9 / 4.8 | 148.3 / 19.7 | 184.8 / 22.3 | −13.6 / 6.8 | −4.3 / 7.6 |
| **Chile screening MIP** | base | 1,800 | 8 % | −66.70 | −1,217.0 / −0.9 | −1,069.9 / 0.5 | −534.7 / 4.6 | −1,175.6 / −0.5 | −291.7 / 5.7 |
| | mid | 220 | 8 % | −3.41 | −90.4 / 2.6 | 16.8 / 8.9 | 101.9 / 13.1 | −78.7 / 3.4 | −17.9 / 6.9 |
| | peak (200 MW) | 100 | 8 % | 0.78 | −15.0 / 5.9 | 100.4 / 19.9 | 130.8 / 23.1 | −6.1 / 7.2 | −1.9 / 7.7 |
| **Chile battery LP** | bess 84 MW / 289 MWh | 66.2 | 8 % | 0.00 | −16.4 / 3.7 | 67.2 / 22.4 | 27.2 / 14.3 | 0.4 / 8.1 | −16.4 / 3.7 |
| **Chile battery MIP** | bess 100 MW / 300 MWh | 70.0 | 8 % | −3.28 | −45.4 / −5.5 | 46.1 / 17.7 | −0.1 / 8.0 | −27.9 / 0.7 | −45.4 / −5.5 |

(The mid / peak rows of the `wacc` cases equal the screening LP rows;
Garver l2_5 and l3_5 behave as l2_6.  The cost data are illustrative
round numbers, not Chilean costs.  The VOLL demos (`invest_screening`,
`invest_bat`, …) are kept for the energy-only / zero-profit theory; the
Chile cases `invest_cl_*` have no unserved energy and are the ones the
Chilean page discusses.)

### 3.1 Where and why the two views diverge

1. **Fixed costs are not in the LP.**  Under `energy_only`, every LP
   project — which recovers its annuity *exactly* in the plan — has
   NPV < 0 and IRR ≈ 4–5 % at an 8 % WACC: fixed O&M, insurance and
   warranty are costs the plan never charged.  With them removed the
   private view reproduces the planning view (NPV 0, IRR = WACC; the
   `test_lp_zero_npv_planning_equivalent` test).  Once gtopt charges
   fixed O&M in the LP (planned on `feat/investment-economics`), the
   tool reads the case's value instead of adding its own (§1.2).
2. **Scarcity rents.**  A least-cost LP pays peak capacity through the
   energy price: in the peak blocks the price rises above the peaker's
   cost until the rent equals its annuity (peak-load pricing) — with
   or without unserved energy.  Energy-only markets pay that rent
   (ERCOT's cap, ORDC); Chile does not: the CMg is the cost of the most
   expensive unit dispatched and capacity is paid by the sufficiency
   payment.  `cl` therefore caps the price at the highest generator
   cost of the case and reports the removed rent as
   `scarcity_excluded`; a case with unserved energy is flagged in the
   report notes.  Capacity markets in this table still add their
   payment on top of an LP price with scarcity rents (their energy
   caps are high); read those columns as upper bounds.
3. **Reliability options remove scarcity rents** for a premium; peakers
   lose most, the battery (empty in scarcity hours) gains.
4. **Integer lumps (MIP).**  The 600 MW base block of the screening MIP
   is short 57–67 M\$/yr in the plan and deeply negative in every
   market; the small 50-MW peak modules gain.  The make-whole uplift of
   the planning view is the private investor's missing money.
5. **Rates.**  At its own WACC each project recovers the LP annuity
   (rate gap 0 in the bridge); a private WACC above the planning rate
   lowers NPV by $I(\mathrm{CRF}(r,n) - \mathrm{CRF}(\rho,n))$ per
   year.
6. **Transmission.**  Merchant lines recover only the congestion rent
   (= annuity in the LP, minus O&M and insurance: IRR 5.6 %; MIP
   circuits 0.7 % or nothing).  Regulated lines (Chile VATT) earn the
   regulated rate regardless of congestion: IRR 9.6 % pre-tax (7 %
   after tax grossed up; COMA covers O&M and insurance).
7. **CO2.**  At a carbon price passed through to the marginal price,
   the battery's emission arbitrage (61.9 kt/yr avoided) is worth
   0.31 M\$/yr per 5 \$/t; the marginal peaker gains nothing; the base
   coal unit loses.

### 3.2 Recommendations for gtopt

* **Charge fixed O&M in the LP** (decided; implemented on
  `feat/investment-economics`), so plan and market agree.  The tool
  reads the case's fixed-cost field and does not add its own 2 % then.
* **Do not put a capacity / sufficiency requirement in the LP.**  The
  sufficiency payment is an administered transfer that is meant to pay
  the investment; adding it to the plan would pay capacity twice.  The
  plan stays least-cost; the payment is checked *ex post* by this tool
  (§3 of the Chilean page shows whether it over- or under-pays each
  project).
* **Plan without unserved energy** when the study represents Chile:
  failure cost = the CNE *costo de falla* (the VOLL of Chilean
  studies), and enough candidate capacity — the `invest_cl_*` cases.
  Zero failure needs the peak to last longer than annuity / (costo de
  falla − peaker cost) hours; otherwise the LP rightly sheds.
* Report the planning annuity at the private WACC next to the social
  one (`gtopt_cost_recovery` already does per-candidate rates).

## References

General:

* [G1] W. W. Hogan, "On an 'energy only' electricity market design for
  resource adequacy," Harvard working paper, 2005.
  <https://scholar.harvard.edu/whogan> (publications list)
* [G2] W. W. Hogan, "Electricity scarcity pricing through operating
  reserves," *Economics of Energy & Environmental Policy* 2(2), 2013,
  [doi:10.5547/2160-5890.2.2.4](https://doi.org/10.5547/2160-5890.2.2.4).
* [G3] Potomac Economics, *2024 State of the Market Report for the
  ERCOT Electricity Markets*, 2025.
  <https://potomaceconomics.com/wp-content/uploads/2025/06/2024-State-of-the-Market-Report.pdf>
* [G4] PJM, Reliability Pricing Model — 2027/2028 Base Residual
  Auction results (2025).
  <https://www.pjm.com/markets-and-operations/rpm>
* [G5] PJM, ELCC class ratings 2027/2028 (Markets & Reliability
  Committee, Jul 2025).
  <https://www.pjm.com/planning/resource-adequacy-planning/effective-load-carrying-capability>
* [G6] GB Capacity Market, T-4 2029/30 auction (Mar 2026), EMR
  Delivery Body. <https://www.emrdeliverybody.com/>
* [G7] P. Cramton, A. Ockenfels, S. Stoft, "Capacity market
  fundamentals," *Economics of Energy & Environmental Policy* 2(2),
  2013, [doi:10.5547/2160-5890.2.2.2](https://doi.org/10.5547/2160-5890.2.2.2).
* [G8] C. Vázquez, M. Rivier, I. J. Pérez-Arriaga, "A market approach
  to long-term security of supply," *IEEE Trans. Power Syst.* 17(2),
  2002, [doi:10.1109/TPWRS.2002.1007903](https://doi.org/10.1109/TPWRS.2002.1007903).
* [G9] Terna, Italian capacity market (2027 auction).
  <https://www.terna.it/en/electric-system/capacity-market>
* [G10] UK DESNZ, Contracts for Difference Allocation Round 7 results
  (Jan 2026).
  <https://www.gov.uk/government/publications/contracts-for-difference-cfd-allocation-round-7-results>
* [G11] Regulation (EU) 2024/1747 (electricity market design), art.
  19d. <https://eur-lex.europa.eu/eli/reg/2024/1747/oj>
* [G12] Orden ITC/3127/2011 (Spain, capacity payments), BOE.
  <https://www.boe.es/eli/es/o/2011/11/17/itc3127/con>
* [G13] Bundesnetzagentur, Kapazitätsreserve.
  <https://www.bundesnetzagentur.de/kapres.html>
* [G14] NREL, *Annual Technology Baseline 2024* (v4.0.0), ATBe.csv.
  <https://atb.nlr.gov/electricity/2024/data>,
  <https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/2024/v4.0.0/ATBe.csv>
* [G15] CNE, Licitación de Suministro 2025/01 results (Dec 2025).
  <https://www.cne.cl/tarificacion/electrica/licitaciones/>
* [G16] P. Joskow, J. Tirole, "Merchant transmission investment,"
  *J. Industrial Economics* 53(2), 2005,
  [doi:10.1111/j.0022-1821.2005.00253.x](https://doi.org/10.1111/j.0022-1821.2005.00253.x).
* [G17] R. Sioshansi, P. Denholm, T. Jenkin, J. Weiss, "Estimating the
  value of electricity storage in PJM: arbitrage and some welfare
  effects," *Energy Economics* 31(2), 2009,
  [doi:10.1016/j.eneco.2008.10.005](https://doi.org/10.1016/j.eneco.2008.10.005).
* [G18] R. Stephan et al., "Limiting the public cost of stationary
  battery deployment by combining applications," *Nature Energy* 1,
  2016, [doi:10.1038/nenergy.2016.79](https://doi.org/10.1038/nenergy.2016.79).

* [G19] NREL, *Insurance in the Operation of PV Plants*, TP-6A20-78588,
  2021, §7. <https://docs.nlr.gov/docs/fy21osti/78588.pdf>
* [G20] R. Wiser, M. Bolinger, E. Lantz, *Assessing wind power operating
  costs in the United States*, LBNL, 2019, Fig. 5.
  <https://www.osti.gov/servlets/purl/1544993>
* [G21] PNNL, *2022 Grid Energy Storage Technology Cost and Performance
  Assessment* (PNNL-33283), §4.1.2.
  <https://www.energy.gov/sites/default/files/2022-09/2022%20Grid%20Energy%20Storage%20Technology%20Cost%20and%20Performance%20Assessment.pdf>

Chile: see [C1]–[C37] in
[private-investor-evaluation-cl.md](private-investor-evaluation-cl.md#references).
