# Private-Investor Evaluation — the Chilean Market Model (`cl`)

How a generation or storage project in Chile's *Sistema Eléctrico
Nacional* (SEN) is actually paid, how `gtopt_private_eval --market cl`
reproduces it from a solved gtopt plan, and what the investment cases
give.  The generic method (pre-tax EBIT cash flow, other market models,
interface) is in
[private-investor-evaluation.md](private-investor-evaluation.md).

Data rule: regulator / operator values appear here only as a few cited
reference parameters with their source (link, not embed); nothing is
downloaded into the repository.  Values were checked on 2026-11-27;
items marked *(unverified)* come from secondary sources.

## 1. The revenue streams of a Chilean project

| Stream | Legal source | CEN process | Who pays → who receives | In `cl` |
|---|---|---|---|---|
| Energy | LGSE art. 72-3 | *balance de transferencias económicas* (monthly) [C9] | net position at the hourly *costo marginal* of the bus | `energy` = Σ CMg·(iny − ret)·h |
| Sufficiency capacity | DS 62/2006 + DS 42/2020 + DS 70/2023; NT de Transferencias de Potencia [C1–C4] | *cálculo de potencia de suficiencia* (preliminary, definitive), *balance de potencia* [C10] | retailers for their clients' peak demand → generators by definitive sufficiency | `sufficiency` |
| Ancillary services (SSCC) | LGSE art. 72-7; DS 113; DS 32/2025 [C11–C13] | *Informe de SSCC*, auctions, SSCC remuneration balance | providers paid at awarded / cost value; cost paid by withdrawals for final customers | `sscc` (assumption) or `reserves` (case duals) |
| Green tax | Ley 20.780 art. 8, Ley 21.210 [C14] | *balance de impuesto a las emisiones* | emitter pays the tax; compensation to units with cost + tax ≥ CMg, paid pro rata by withdrawals for final customers | `green_tax` (cost), CO2 sensitivity |
| Transmission | Ley 20.936 [C15] | *pagos de transmisión* | *cargo único* paid by final customers; transitional injection toll only for pre-2016 contracts | `transmission_injection` (0 for new projects); lines: regulated VATT |
| Overcosts, SSCC and green-tax compensation pro rata | DS 125/2017 as amended by DS 32/2025 [C13] | inside the transfer balance | paid by withdrawals that supply final customers → units dispatched out of merit | `withdrawal_charges` on the PPA share |
| Unavailability compensations | LGSE art. 72-20 | *compensaciones por indisponibilidad* | owner of the failed installation reimburses (ENS × 15 × tariff; cap 5 % of revenue) | not modelled (event-driven) |
| CEN budget | LGSE art. 212-11/13 | *cargo por servicio público* | final customers only (REX 722/2025: 0.855 CLP/kWh) [C16] | not a generator cost |
| CEN payment guarantee | DS 125 art. 156–162 as replaced by DS 32/2025; NT de Coordinación y Operación art. 3-64…3-69 [C31, C32] | annual *cálculo de garantías* [C33] | companies with contracts (withdrawals for final customers) post ≥ 3 months of their balance deficit | `cen_payment_guarantee` = amount × fee |
| Supply-contract bonds | bases of the *licitaciones de suministro* [C35] | — | the bidder / supplier posts *seriedad* and *fiel cumplimiento* bonds | `supply_contract_bond` on the PPA share |
| Insurance, warranties | private | — | owner | `insurance`, `warranty` (generic §1.2) |

A **new merchant generator or battery** therefore receives energy at
CMg, sufficiency and SSCC, pays its own green tax when above the
threshold, and pays no transmission toll and no final-customer charge;
the pro-rata charges apply only to the volume it withdraws to supply
final customers under a PPA (`ppa_withdrawal_share`).  Battery charging
for arbitrage carries none of the final-customer charges (DS 125 art.
93 as amended by DS 32/2025: only withdrawals *to sell to distributors
or free customers* carry them — our reading; no explicit exemption text
was found).

### 1.1 Energy at costo marginal

The CEN values each company's hourly injections and withdrawals at the
bus marginal cost; the net position settles monthly in the *balance de
transferencias económicas* [C9].  Storage injections and withdrawals
also settle at CMg (DS 125 art. 100).  In gtopt the bus balance dual is
the CMg, so

$$R_{\text{energy}} = \sum_t \mathrm{CMg}_{b,t}\ (\mathrm{iny}_t - \mathrm{ret}_t)\ h_t .$$

Units dispatched out of merit (minimum technical, security) receive
their uncovered cost $(c - \mathrm{CMg})\cdot E$ (*sobrecostos*); gtopt's
LP has no out-of-merit dispatch, so this stream is 0 here.

**No scarcity rent.**  The CMg is the variable cost of the most
expensive unit dispatched; Chile pays capacity through the sufficiency
payment, not through the energy price, and expansion studies plan with
enough capacity that no scenario has unserved energy (the failure cost
— the CNE *costo de falla*, the VOLL of Chilean studies — then only
prices hypothetical deficits).  A least-cost LP still prices its peak
blocks above the peaker's cost (peak-load pricing: the rent equals the
peaker's annuity) even without unserved energy.  `cl` therefore caps
the price at the highest generator cost of the case (`scarcity_cap:
"case"`, or a number) and reports the removed rent as
`scarcity_excluded`; a case with unserved energy is flagged in the
report.  The failure cost of the Chile cases is the CNE *costo de falla
de larga duración* of the SEN, 0–5 % depth: **795.11 US\$/MWh**
(short-duration: 13.73 US\$/kWh) [C36].

### 1.2 Pago por potencia de suficiencia

**Regulation in force.**  DS 62/2006 (*Reglamento de transferencias de
potencia entre empresas generadoras*) with its amendments remains the
governing rule — the latest node-price decree, DS 4T/2026 (DO
24-Apr-2026), cites it "y sus modificaciones posteriores" [C5].  DS
42/2020 rewrote arts. 1–4 (preliminary balance paid in 12 monthly
instalments, definitive by end of March; *estado de reserva
estratégica*) [C2]; DS 70/2023 (DO 5-Jun-2024) implements the storage
law Ley 21.505 [C3].  The full "nuevo reglamento de potencia" (DS
3/2022) was withdrawn from Contraloría and never published [C17].

**Demand side.**  *Demanda de punta* = mean of the **52 highest hourly
loads** of the year (measured on withdrawals since 2022); a client's
*demanda de punta equivalente* uses its 52 highest readings in the
*período de control de punta* — **18:00–22:00, April–September,
working days** (DS 4T/2026 §3.3); DS 70 makes the CNE review that
period at least every 4 years (first study Sept-2026, LOLP based)
[C4, C5, C18].

**Supply side.**  Each unit's *potencia inicial* (Pini) → *potencia de
suficiencia preliminar* (forced-outage rate, 5-year window) →
*definitiva*; sufficiency is defined as 1 − LOLP at peak demand [C4]:

| Technology | Pini (NT de Transferencias de Potencia, DS 70) |
|---|---|
| Thermal | Pmax × lowest annual fuel-availability factor of the last 5 years (+ backup fuel share) |
| Hydro | mean of the 2 driest hydrological years; regulating energy at the top of the load-duration curve |
| Wind, solar PV | Pmax × min(lowest annual capacity factor of the last 5 years, mean CF in the 52 peak hours) |
| Storage (permanent, DS 70 art. 37) | system peak − peak of the net load after a daily peak-shaving optimisation, both on the 52 peak hours |
| Storage (transitory, 10 years from 1-Jul-2024) | Pmax × % by duration: <1 h 0, 1 h 36 %, 2 h 65 %, 3 h 85 %, 4 h 98 %, ≥5 h 100 % (linear in between) |

**The pie.**  The definitive sufficiency of all units is scaled to the
peak demand, so the money is *peak demand × capacity price*,
distributed in proportion to sufficiency:

$$
P^{\text{suf}}_{\text{def},i} = P^{\text{suf}}_{\text{pre},i}\cdot
\frac{D_{\text{punta}}}{\sum_j P^{\text{suf}}_{\text{pre},j}}, \qquad
R_{\text{suf},i} = P^{\text{suf}}_{\text{def},i}\cdot P_{\text{nudo,pot}}
\cdot 12 .
$$

Retailers pay for their clients' peak demand at the node's
*precio de nudo de corto plazo de la potencia* (basic price × power
loss factors) [C5, C10].  *(The exact DS 62 article of the pro-rating
could not be opened in the consolidated text.)*

**The price: the peak unit.**  The *precio básico de la potencia* is the
monthly annuity of the development cost of the **peak unit** — an
open-cycle gas turbine, 70 MW at Nogales 220 kV in the latest study
[C6]:

$$
P_{\text{bpot}} = \bigl[(C_{TG}\ FRC_{TG} + C_{SE}\ FRC_{SE} + C_{LT}\ FRC_{LT})\ CF +
C_{\text{fijo}}\bigr]\ (1 + MRT)\ (1 + FP)
$$

| Parameter (CNE RE 803/2025, base Jan-2024) | Value |
|---|---|
| Turbine $C_{TG}$ / substation $C_{SE}$ / line $C_{LT}$ | 649.06 / 78.52 / 14.08 US\$/kW |
| Lives at the legal 10 % rate (LGSE art. 165) | 25 / 41 / 48 years |
| $FRC$ (monthly CRF) | 0.008785 / 0.008138 / 0.008057 |
| Financial cost factor $CF$ | 1.048809 |
| Fixed cost $C_{\text{fijo}}$ | 1.171 US\$/kW-month |
| Theoretical reserve margin $MRT$ | 10 % |
| Loss factor $FP$ | 0.44 % (Centro-Norte) |
| **Result** | **8.77 US\$/kW-month** |

`gtopt_private_eval` reproduces the 8.77 (`capacity_price_mode:
peak_unit`; the $FRC$ are $\mathrm{CRF}$ at the monthly equivalent of
10 % over the life in months).  The default price is the latest
*Informe Técnico Definitivo* of the short-term node price (jul-2026):
**9,231.51 CLP/kW-month at 897.64 CLP/USD = 10.28 US\$/kW-month =
123.4 US\$/kW-yr** [C7].  Node prices of DS 4T/2026: 8,360–8,930
CLP/kW-month [C5].

**In `cl`.**  $`P^{\text{suf}}_{\text{pre}}`$ = new MW × factor
(`sufficiency_factors` per technology; storage: the DS 70 transitory
table by duration × `storage_availability`); the pie factor
$`k = D_{\text{punta}}/\sum P^{\text{suf}}_{\text{pre}}`$ is computed **from the
solved case** (`prorate: case`: peak of served + unserved load over all
units of the case, existing and new) and capped at 1 (`prorate_cap`;
the unit is not paid above its preliminary sufficiency — an
assumption), or given (`prorate: 0.8`), or off (`none`).  The factors
for thermal (0.85–0.92), wind (0.25) and PV (0.02, the 52 peak hours are
in the evening) are **assumptions** from the NT rules; CEN publishes the
per-unit values only as calculation datasets [C10].

Caveats: the storage factor is the 10-year transitory rule (to 2034);
the permanent peak-shaving method gives less to long fleets of
batteries.  A representative year repeated over the life ignores the
fall of the factor as storage saturates the peak.

### 1.3 Ancillary services (SSCC)

Since Ley 20.936 the CEN procures SSCC through auctions when there is
competition and direct instruction otherwise; for 2026 CPF−, CSF± and
CTF± are auctioned (co-optimised with energy), CPF+ moves to auctions,
voltage control stays instructed [C11, C12].  Awards are paid at the
offer (wear cost, capped by CNE maximum values RE 668/2025);
instructed service at opportunity + wear cost.  Mean filtered bids
(CEN UMC 2025a): CPF− 1.1, CSF+ / CTF+ 2.9, CSF− 2.6, CTF− 1.9
US\$/MW-h of reserve [C19].  Side payments (opportunity cost,
re-dispatch) are ≈ 79 % of the frequency-control cost, and the total
SSCC cost ≈ 3.70 US\$/MWh of energy (Oct-2024–Oct-2025, *via press*)
[C11].

**In `cl`.**  If the gtopt case co-optimises reserves
(`reserve_zone_array` / `reserve_provision_array`), the revenue is
Σ h·Σ_z (|μ_up,z|·up + |μ_dn,z|·dn) at the zone requirement duals.
Otherwise an assumption: new MW × `share` × `price` × 8760 — default
BESS 20 %, OCGT / CCGT / hydro 10 %, at 2.9 US\$/MW-h (CSF+ mean bid).
This is small (0.4 M\$/yr for the 84-MW battery); the market
(≈ 50 % of CSF from batteries in Sep-2026, *unverified*) is shallow.

### 1.4 Green tax and CO2

*Impuesto verde*: 5 US\$/tCO2 (Ley 20.780 art. 8) on facilities
emitting ≥ 25,000 tCO2/yr or ≥ 100 t PM/yr (Ley 21.210, from 2023)
[C14].  The tax is **not in the marginal cost**: units whose cost
including the tax is at or above the CMg receive a compensation
max(0, cost + tax − mean CMg) × energy, paid pro rata by the withdrawals
for final customers; 2025: 704.7 million CLP, 0.79 % of the sector's
tax [C20].  A 2024 surcharge to 10 US\$/t was rejected by the Senate
(Jun-2025) [C21].

**In `cl`.**  `green_tax` = own t/yr × 5 US\$/t when own emissions
exceed 25 kt/yr.  The CO2 arbitrage (generic §1.3) is *not* a cash
stream in Chile today (the tax is outside the CMg) and is reported as a
sensitivity at 0 / 5 / 25 / 50 / 100 US\$/t passed through to the
price (the green tax cost is then dropped to avoid double counting).

### 1.5 Transmission

The *cargo único* (national, zonal, dedicated used by regulated users,
SSCC infrastructure) is paid by final customers — REX 312/2026,
national segment 4.119 CLP/kWh [C15b].  Generators keep a transitional
injection toll only for supply contracts signed before Ley 20.936,
scaled by the law's yearly factor (2026 50.93 %, 2030 33.80 %, 2034 0)
[C15]; new injections are exempt (letter C), so the default is 0
(`injection_toll_usd_mwh` with the factor table for old contracts).
Dedicated lines used by a generator are bilateral contracts (not
modelled).

Transmission **lines** are regulated: VATT = AVI + COMA (+ AEIR), AVI
the annuity of the investment value at the art. 118 rate — **7.00 %
after tax for 2024–2027** (CAPM 5.93 % below the 7 % floor) [C8].  With
the AEIR returning the income tax, a line earns pre-tax about
$7\ \%/(1-0.27) = 9.59\ \%$; `cl` uses that (`regulated_tax_gross_up`).

### 1.6 Guarantees

**CEN payment guarantee (*garantías para participar en el Mercado de
Corto Plazo*).**  To protect the payment chain of the monthly balances,
the CEN requires a guarantee from every company that takes part in the
short-term market with contracts.  DS 32/2025 (DO 17-Jun-2026) replaced
DS 125 art. 156–157 [C31]: companies must "caucionar, al menos, tres
meses de facturación de los balances de transferencias de energía y
potencia".  The amount (art. 157) is the sum of

1. the 3 months of largest coincident deficit between the company's
   generation valued at CMg at its injection bus and its expected
   withdrawals for its supply contracts (incl. physical purchase
   contracts between generators), and
2. the 3 months of largest deficit between its definitive sufficiency
   valued at the short-term node price and its expected peak
   withdrawals for those contracts,

in the hydrology (wet / mean / dry; NT: 20 / 50 / 90 % exceedance) with
the largest deficit for the company.  So **the driver is the balance,
not the MW**: MWh and MW of the company's *net short position* (contract
withdrawals − own generation, valued at CMg / node price); a merchant
plant or a battery doing arbitrage, with no withdrawals for final
customers, posts nothing; a supplier short of its own generation posts
in proportion to the deficit.

* Instruments (art. 156): demand deposit certificates, *boletas
  bancarias de garantía a la vista*, deposits < 360 days, or a standby
  letter of credit from a bank rated A or better; the NT asks for an
  irrevocable first-demand instrument issued in Chile, in CLP, by an
  institution supervised by the CMF [C32].
* Timing (NT art. 3-67): data from companies by 1 Aug; preliminary
  calculation 1 Sep; definitive 1 Oct; guarantees delivered by 30 Nov,
  valid for the next year.  Updated (art. 159) on relevant changes —
  failures, maintenance, topology, new contracts (before supply starts).
* Non-compliance: the CEN executes the guarantee; an unpaid balance
  remains owed (art. 160); a company whose guarantee was executed
  cannot keep participating and its free clients are disconnected once
  the guarantee is exhausted, regulated clients go to the art. 135
  quinquies procedure (art. 161–162); CEN internal procedure on the
  payment chain (definitive version 10-Jun-2026) [C33].
* Size: 52 of the 84 companies supplying final customers posted
  ≈ US\$700 million for 2023 (US\$481 million for 2022); the largest
  were Enel (CLP 267,511 million), Colbún and Engie — the companies
  with most contracts; a net-long generator (AES Andes) posted none
  [C37].  The per-company calculation is published yearly by the CEN
  [C33] (*Cálculo Anual Garantías*, preliminary / definitive versions;
  the files sit in SharePoint folders linked from that page and are not
  downloaded by the tool).  Because the amount depends on the contract
  position, a \$/MW-installed average is not a meaningful default; the
  per-company amounts serve only as a cross-check of the order of
  magnitude.

**In `cl`** (`cen_guarantee`): a baseload contract of
`ppa_withdrawal_share` × the project's yearly energy (or `contract_mw`
MW) at the project's bus; monthly energy deficit = Σ CMg·(q − net)·h /
12, capacity deficit = (q − definitive sufficiency) × node price; amount
= `months` (3) × `peak_month_factor` (1.0, the worst months vs the mean
— an assumption, the representative year has no months) × (energy⁺ +
capacity⁺), or a given `amount_usd`; yearly cost = amount × `fee_pct`
(1 %/yr, an assumption: one Chilean bank sheet quotes an issuance fee
up to 1 %, min UF 3 [C34]).  Default share 0 → no guarantee.

**Supply-contract bonds** (CNE *Bases de Licitación de Suministro
2025/01*, RE 221/2025 [C35]): *seriedad de la oferta* UF 100 per GWh
offered (until the award) and *fiel cumplimiento* UF 600 per GWh of the
last contract year, held ≥ 51 months from the start of supply.  `cl`
charges the fee on the second for the PPA share (`supply_contract_bond`,
UF 600/GWh × 44 US\$/UF (assumption) × 1 %/yr for 51 months).

**Transmission and connection** (not modelled as a default cost):
transmission tenders ask for a bid bond of 2.5 % of the reference VI,
milestone bonds of 2 %, an execution bond of 8 % and an operation bond
of 3 % of the VI for 15 months after COD, plus civil-liability
insurance of 1 % of VI (Res. Ex. CNE 518/2017 bases; current bases may
differ); the draft DS 37 (Ley 21.721) asks generation / storage
projects proposing transmission works for a 5 % guarantee.  Use the
generic `guarantee_pct_capex` / `guarantee_fee_pct` lines for them.

### 1.7 Insurance

Operational insurance is a separate EBIT line (generic §1.2).  Chilean
references: the CNE peak-unit study counts insurance at **0.3 % of total
capex per year** inside the fixed cost [C6]; the zonal transmission
valuation study uses all-risk 0.175 % on substations, 0.26 % on
buildings, 0.14 % civil liability (lines excluded from the all-risk
policy).  The CNE ICTGA fixed O&M (1–3 % of capex) does not itemise
insurance [C23]; the tool keeps them as separate lines (risk of a small
double count, flagged).

## 2. Reference values

| Parameter | Value | Source |
|---|---|---|
| Capacity price (default) | 10.28 US\$/kW-month (9,231.51 CLP) | [C7] ITD PNCP jul-2026 |
| Capacity price (peak unit, base Jan-2024) | 8.77 US\$/kW-month | [C6] CNE RE 803/2025 |
| Peak demand definition | mean of 52 highest hourly loads | [C4] NT §1-5.6 |
| Storage sufficiency (transitory) | 1 h 36 %, 2 h 65 %, 3 h 85 %, 4 h 98 %, ≥5 h 100 % | [C3] DS 70/2023 |
| SSCC price | 1.1–2.9 US\$/MW-h | [C19] CEN UMC 2025a |
| SSCC + overcost pro rata | ≈ 3.70 US\$/MWh (2.76 overcost) | [C11] (press) |
| Green tax | 5 US\$/tCO2, threshold 25 kt/yr | [C14] |
| Transmission rate | 7.00 % after tax (2024–27) | [C8] CNE Res. 462/2024 |
| CNE discount rate | 10 % real | LGSE art. 165, [C6] |
| Corporate tax (only to gross up the VATT rate) | 27 % | [C22] SII |
| Fixed O&M | 1–2 % of capex/yr (OCGT 2–3 %) | [C23] CNE ICTGA 2026 |
| Costo de falla (failure cost, VOLL of the Chile cases) | 795.11 US\$/MWh (long duration, 0–5 %); 13.73 US\$/kWh (short duration) | [C36] CNE Res. Ex. 432/2026 |
| CEN payment guarantee | ≥ 3 months of energy + capacity balance deficit (contract position) | [C31] DS 32/2025 art. 156–157 |
| Supply-contract performance bond | UF 600 / GWh, ≥ 51 months | [C35] |
| Guarantee fee | 1 %/yr (assumption) | [C34] |
| Insurance | 0.3 % of capex/yr (peak unit) | [C6] |
| Emission rates | CCGT 0.39, OCGT 0.56, diesel 0.78, coal 0.95 t/MWh | IPCC 2006 × CNE heat rates (derived) |
| SEN mean emission factor | 0.16–0.32 tCO2/MWh (monthly, 2024–25) | [C24] CEN |

### 2.1 Capex: CNE, SEIA, NREL ATB

Local base (CNE *Informe de Costos de Tecnologías de Generación y
Almacenamiento*, May 2026) [C23] against NREL ATB 2024 (Moderate 2025,
2022 USD) [G14] and the declared investment of a sample of recent SEIA
filings:

| Technology | CNE 2026 US\$/kW | ATB 2024 US\$/kW | SEIA declared US\$/kW (min / median / max) | Range used |
|---|--:|--:|--:|--:|
| Solar PV | 732 | 1,323 | — (no recent PV-only filing) | 732–1,323 |
| PV + BESS | 1,404 | — | 823 / 1,150 / 1,234 (per MWac / MWp) | — |
| Wind | 1,433 | 1,380 | 1,477 / 1,820 / 2,326 | 1,380–2,326 |
| BESS 4 h | 722 (≈ 180 \$/kWh) | 1,551 (388 \$/kWh) | 563 / 670 / 1,120 (108 / 156 / 280 \$/kWh) | 563–1,551 |
| CCGT | 1,144 | 1,213 | — | 1,144–1,213 |
| OCGT | 860 (peak study: unit 545–790) | 1,084 | — | 545–1,084 |
| Pumped hydro | — | — | 1,750–2,000 | — |
| Hydro (run-of-river) | — | — | 2,720 | — |
| CSP | — | — | 5,300–8,200 (pre-2021 filings) | — |
| 220 kV double circuit | — | — | 0.8–1.4 MUS\$/km | — |

SEIA sample (declared *monto de inversión*; the SEIA project pages did
not load for automated reading, so the values come from SEA resolution
PDFs [SEA], the CNE peak-unit study's SEIA table, or press citing the
filing [news]):

| Project | Tech | MW / MWh | MUS\$ | US\$/kW (US\$/kWh) | Status | Source |
|---|---|---|--:|--:|---|---|
| BESS Las Cañas | BESS | 400 / 1,600 | 300 | 750 (188) | RCA favourable 21-Nov-2025 | [SEA](https://firma.sea.gob.cl/publicaciones/2025/12/03/1764789106_2166940923) |
| Beltrán BESS | BESS | 150 / 837 | 90 | 600 (108) | rejected 19-Jan-2026 | [SEA](https://firma.sea.gob.cl/publicaciones/2026/01/30/1769800404_2167718881) |
| BESS Imilac | BESS | 360 / 1,800 | 270 | 750 (150) | filed Dec-2025 | [news](https://www.reporteminero.cl/noticia/noticias/2025/12/free-power-group-bess-imilac-360mw-1800mwh-antofagasta) |
| BESS Quidora | BESS | 160 / 640 | 100 | 625 (156) | filed Dec-2025 | [news](https://www.reporteminero.cl/noticia/noticias/2025/12/bess-quidora-cardones-almacenamiento-energia-atacama) |
| BESS Los Ñisñiles | BESS | 90 / 360 | 64.1 | 712 (178) | admitted Jan-2026 | [news](https://www.reporteminero.cl/noticia/energias-limpias/2026/01/grupotec-bess-los-nisniles-seia-ovalle-coquimbo) |
| Águila Mora | BESS | 300 / 1,200 | 336 | 1,120 (280) | DIA admitted | [news](https://www.portalminero.com/proyecto-aguila-mora-antuko-impulsa-sistema-de-almacenamiento-energetico-por-us-336-millones-en-el-norte-de-chile) |
| Arica BESS | BESS | 350 / 1,409 | 220 | 629 (156) | DIA filed | [news](https://www.latercera.com/pulso/noticia/firma-chilena-ingresa-a-evaluacion-ambiental-proyecto-de-almacenamiento-de-energia-por-us220-millones-en-region-de-arica/) |
| PF Elena 2 | PV + BESS | 467.5 MWp + 3,010 MWh | 577 | 1,234 | approved 30-Oct-2026 | [news](https://www.reporteminero.cl/noticia/noticias/2026/02/parque-fotovoltaico-elena-2-ingresa-al-seia-con-inversion-estimada-en-us577-millones) |
| Copao Diego de Almagro | PV + BESS | 351 MWac + 250 MWh | 289 | 823 | DIA Nov-2025 | [news](https://www.reporteminero.cl/noticia/noticias/2025/11/copao-energia-dia-parque-fotovoltaico-bess-diego-de-almagro-2025) |
| Trumao | Wind | 320 | 700 | 2,188 | filed Dec-2025 | [news](https://www.portalminero.com/wpd-ingresa-al-seia-proyecto-eolico-de-320-mw-en-los-lagos-con-inversion-de-us700-millones) |
| Las Fresias | Wind | 309.6 | 500 | 1,615 | filed Apr-2025 | [news](https://www.portalminero.com/firma-alemana-ingresa-a-evaluacion-ambiental-proyecto-eolico-de-us500-millones-en-la-region-de-nuble) |
| El Rancho | Wind | 216 | 319 | 1,477 | filed Jul-2025 | [news](https://www.portalminero.com/frv-impulsa-millonario-parque-eolico-en-los-rios-30-turbinas-y-mas-de-200-mw-entran-a-evaluacion-ambiental) |
| Bombeo Paposo | Pumped hydro | 800 | 1,400 | 1,750 | review ended Aug-2024 | [news](https://www.portalminero.com/index.php/colbun-ingresa-al-seia-el-proyecto-central-de-bombeo-paposo) |
| CH San Carlos | Hydro | 154.4 | 420 | 2,720 | filed Nov-2026 | [news](https://www.reporteminero.cl/noticia/noticias/2026/11/central-hidroelectrica-san-carlos-ingresa-seia-inversion-420-millones) |
| LT 2x220 Los Héroes–Parinacota | Line, 22 km | — | 30 | 1.4 MUS\$/km | DIA Oct-2026 | [news](https://www.reporteminero.cl/noticia/noticias/2026/10/enorchile-ingresa-evaluacion-proyecto-interconexion-electrica-chile-peru-los-heroes-parinacota) |

Aggregates (Ministerio de Energía, *Reporte de proyectos*, Jan-2026,
from SEA data): standalone BESS approved 973 US\$/kW, 182 US\$/kWh; in
evaluation 1,079 US\$/kW, 215 US\$/kWh [C25].  Caveats: SEIA amounts
are unaudited declared estimates; scope varies (connection line,
substation, land included or not); MWp vs MWac is often ambiguous; a
filing is not a build decision.

The `capex_range` of every technology (`defaults.py`) spans
min(SEIA, CNE, ATB) to max(SEIA, CNE, ATB) and drives the
`npv_vs_capex` sensitivity; the CNE values are the base of the capex
trajectories (`capex-trajectory --base cl_cne_2026`).

## 3. Results: the Chile cases

Two cases plan the Chilean way — failure cost at the CNE *costo de
falla* (795.11 US\$/MWh) and no unserved energy in any block:
[`cases/invest_cl_screening_1b`](../../cases/invest_cl_screening_1b/)
(the screening case with an 80-h peak, because the LP builds a peaker
only if the peak lasts more than annuity / (costo de falla − peaker
cost) = 74 h) and [`cases/invest_cl_bat_1b`](../../cases/invest_cl_bat_1b/)
(the battery case; its prices never exceed 90 \$/MWh).  The cost data
are the illustrative ones of the original cases, at 8 %, fixed O&M 2 %,
insurance and warranty per generic §1.2.

```bash
gtopt_private_eval cases/invest_cl_screening_1b/golden/lp --market cl \
  --config scripts/gtopt_private_eval/configs/invest_screening.json
```

### 3.1 Does the sufficiency payment pay the investment?

Year-1, M\$/yr.  *Missing money* = private annuity + fixed costs
(O&M, insurance, warranty, green tax) − energy margin at the Chilean
CMg (no scarcity rent) − SSCC; *cover* = sufficiency / missing money
(> 1 over-pays, < 1 under-pays).

| Case | Project | Annuity | Fixed costs | Energy margin (CMg) | Scarcity rent removed | Missing money | Sufficiency | Cover | NPV M\$ / IRR |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| screening LP | base 550 MW | 146.6 | 58.1 | 118.6 | 28.0 | 86.2 | 61.1 | 0.71 | −282.4 / 6.1 % |
| | mid 150 MW | 15.5 | 4.4 | 7.8 | 7.6 | 11.6 | 15.7 | 1.35 | 43.7 / 11.0 % |
| | **peak 300 MW** | 15.3 | 3.7 | **0.0** | **15.3** | 18.2 | 33.3 | 1.83 | 148.3 / 19.7 % |
| screening MIP | base 600 MW | 159.9 | 62.1 | 60.3 | 32.9 | 161.7 | 66.6 | 0.41 | −1,069.9 / 0.5 % |
| | mid 200 MW | 20.6 | 5.5 | 6.2 | 11.0 | 19.4 | 21.0 | 1.08 | 16.8 / 8.9 % |
| | peak 200 MW | 10.2 | 2.3 | 0.0 | 11.0 | 12.0 | 22.2 | 1.85 | 100.4 / 19.9 % |
| battery LP | 84 MW / 289 MWh | 7.7 | 1.9 | 7.7 | 0 | 1.5 | 9.3 | 6.3 | 67.2 / 22.4 % |
| battery MIP | 100 MW / 300 MWh | 8.2 | 2.0 | 4.9 | 0 | 4.8 | 10.2 | 2.1 | 46.1 / 17.7 % |

* **The peaker** earns exactly nothing on energy at the Chilean CMg:
  it is marginal whenever it runs.  The LP's peak-block rent
  (795.11 and 730.40 \$/MWh in the two peak blocks) is 15.3 M\$/yr =
  its annuity (50,926 \$/MW-yr × 300 MW): this is the **endogenous
  capacity value** of the plan, **4.24 US\$/kW-month** of peaker
  capacity (4.72 per MW of sufficiency at factor 0.9).  The
  administered price is **10.28 US\$/kW-month** (8.77 from the CNE
  peak-unit study): sufficiency pays the case's peaker 1.8 × its
  missing money.  The gap is the price of the reference unit — the
  case's peaker costs 500 \$/kW at 8 % over 20 years, the CNE peak unit
  742 \$/kW (turbine + substation + line) at 10 % with 10 % reserve
  margin and fixed costs.  With a peaker costing what CNE assumes, the
  sufficiency payment would roughly cover it (by design of the
  peak-unit price).
* **Mid-merit** units are slightly over-paid (1.1–1.35); **base load**
  (here an expensive coal-like unit, 3 M\$/MW, paying 20 M\$/yr of
  green tax) is under-paid (0.4–0.7): it depended on the inframarginal
  scarcity rent that Chile does not pay.  Under the planner's prices it
  broke even; under Chilean rules it would not be built.
* **The battery** never sees scarcity prices in its case; its energy
  margin pays its annuity (the LP optimum) and sufficiency (DS 70
  factor 0.91 at 3.44 h) is a windfall of 6× its small missing
  money (fixed O&M + insurance + warranty).

### 3.2 The battery stack

`invest_cl_bat_1b` LP, year 1: energy 7.73 (captured price 88.9
\$/MWh), sufficiency 9.34 (84 MW × 0.91 × 123.4 \$/kW-yr), SSCC 0.43;
fixed O&M −1.32, insurance −0.26, warranty −0.33: **EBITDA 15.58**,
depreciation 4.41, **EBIT 11.17** (margin 64 %); NPV **67.2 M\$, IRR
22.4 %, payback 4.2 years**; sufficiency is 53 % of revenue.  Without
sufficiency: NPV −12.8 / IRR 4.7 %.

| Variant | NPV M\$ | IRR |
|---|--:|--:|
| CNE capex 180 \$/kWh, no separate power cost | 84.9 | 30.2 % |
| SEIA high 280 \$/kWh | 48.8 | 16.9 % |
| ATB 2024 high (1,551 \$/kW) | −7.9 | — |
| Capacity price from the peak unit (8.77 \$/kW-month) | 55.5 | 20.1 % |
| Pie factor 0.7 | 43.5 | 17.7 % |
| WACC 10 % (CNE legal rate) | 52.3 | 22.4 % |
| COD 2030, ATB Moderate trajectory | 78.3 | 26.9 % |
| ATB scenario 2030: conservative / moderate / advanced | 79.3 / 77.6 / 80.9 | |
| CO2 passed through at 5 / 25 / 50 / 100 \$/t | 69.9 / 80.5 / 93.7 / 120.2 | |
| 50 MW baseload contract (CEN guarantee 3.1 M\$, fee 0.03 M\$/yr) | 66.9 | 22.4 % |

Guarantees are small for these projects: with a 100 MW baseload
contract the peaker posts a CEN guarantee of 2.7 M\$ (fee 0.03
M\$/yr); a contract equal to its own energy needs ≈ 0 (it is not short);
the supply-contract bond on a contract of all its energy costs 0.02
M\$/yr for 51 months.

### 3.3 Comparison with published Chilean evaluations

| Study | Scope | Revenue split / result | This tool (battery case) |
|---|---|---|---|
| Fitch Ratings via Funds Society, Apr-2025 [C26] | Chilean BESS project finance | 40–50 % of revenue from capacity payments | 53 % sufficiency |
| Systep, *Reporte Sector Eléctrico* Oct-2024 [C27] *(unverified snippet)* | generic BESS | capacity ≈ 33 % of revenue | — |
| Americas Market Intelligence, Apr-2024 [C28] | standalone BESS, pre-DS 70 | ≈ 15 % capacity / 85 % arbitrage; capacity ≈ 90 \$/kW-yr | 123 \$/kW-yr × 0.91 |
| Saavedra et al., ISES SWC 2025 [C29] | energy only, BESS 100 MW / 400 MWh at 300 \$/kWh, 8 %, 25 y, 2023 CMg at 8 nodes | IRR 12.4–13.2 % north (spreads 125–132 \$/MWh), 8.8 % centre, 0.4 % Puerto Montt | energy + SSCC IRR 4.7 % (spread ≈ 90 \$/MWh, 15 y life) |
| Aurora Energy Research, Apr-2025 / Jul-2026 [C30] | Chilean BESS | 5-h batteries get full capacity payment to 2034; SSCC up to 11 % of revenue; IRR ±5 pp by scenario | SSCC 2.4 % |

The tool's revenue split is in line with the project-finance view
(capacity is the bankable half); its energy-only IRR is lower than
Saavedra et al.'s because the demo case's spread (solar at 0 → gas at
90) is narrower than the 2023 northern spreads and its life is 15
years.

## 4. Planning view vs Chilean market: why they diverge

1. **Capacity is paid differently.**  The planner pays peak capacity
   through scarcity prices (peak-load pricing, even with no unserved
   energy); Chile pays it through the administered sufficiency payment
   and does not pay the scarcity rent.  The `cl` model removes the rent
   and adds the payment: peakers and storage gain (cover 1.8–6), base
   load that lived on inframarginal scarcity rents loses (cover
   0.4–0.7).  The demo's old "capacity paid twice" (VOLL prices *plus*
   sufficiency) is gone.
2. **Administered vs endogenous capacity price.**  The plan values a
   peaker MW at its annuity (4.24 US\$/kW-month in the case); CNE pays
   10.28.  The payment is right on average only if the reference peak
   unit's cost is the system's marginal capacity cost.
3. **Fixed O&M, insurance, warranties, green tax, SSCC, guarantees and
   pro-rata charges** are not in the LP.
4. **Rates.**  CNE uses 10 % real for the peak unit; transmission is
   paid at 7 % after tax; private developers use their own WACC.
5. **Lumpiness** (MIP) shows up as missing money in both views.

**Recommendations.**  (a) Keep the sufficiency payment *out* of the
expansion LP (it is meant to pay the investment; in the LP it would pay
twice) and check it *ex post* with this tool, project by project.
(b) Plan Chilean cases with the CNE costo de falla as failure cost and
enough candidate capacity for zero unserved energy in every scenario.
(c) Fixed O&M goes into the LP (being implemented); the tool then reads
it from the case.  (d) Keep the transitory DS 70 storage table in mind:
it expires in 2034, so long-life batteries should be evaluated with a
falling factor after year 10.  (e) For contracted projects, set
`ppa_withdrawal_share` / `contract_mw` to price the CEN guarantee and
the supply bonds.

## References

* [C1] DS 62/2006, *Reglamento de transferencias de potencia entre
  empresas generadoras* — cited in force in [C5].
* [C2] DS 42/2020, Ministerio de Energía (DO 26-Dec-2020).
  <https://www.bcn.cl/leychile/navegar?idNorma=1153949>
* [C3] DS 70/2023, Ministerio de Energía (DO 5-Jun-2024), storage
  (Ley 21.505). <https://energia.gob.cl/sites/default/files/dec_70_2023.pdf>
* [C4] CNE, *Norma Técnica de Transferencias de Potencia* (RE 54/2016),
  Jan-2016. <https://www.cne.cl/wp-content/uploads/2015/06/NT62_Ene16.pdf>
* [C5] DS 4T/2026, node prices (DO 24-Apr-2026).
  <https://www.diariooficial.interior.gob.cl/publicaciones/2026/04/24/44433/01/2797040.pdf>
* [C6] CNE, *Informe Técnico Preliminar de Costos de la Unidad de
  Punta*, RE 574 (16-Sep-2025); definitive RE 803/2025 (22-Dec-2025).
  <https://www.cne.cl/wp-content/uploads/2025/09/Rex.-CNE.N°574_ITP-Unidad-de-Punta_16-09-2025-3.pdf>
* [C7] CNE, *Informe Técnico Definitivo, Precio de Nudo de Corto Plazo
  julio 2026*, RE 397 (3-Aug-2026).
  <https://www.cne.cl/wp-content/uploads/2026/08/Resolucion-Exenta-397-2026.pdf>;
  quoted in the ITD Precio Estabilizado nov-2026,
  <https://www.cne.cl/wp-content/uploads/2026/11/ITD-Precio-Estabilizado-y-Precio-Basico-nov26.pdf>
* [C8] CNE Res. Ex. 462, *tasa de descuento art. 118 LGSE 2024–2027*
  (30-Aug-2024).
  <https://www.cne.cl/wp-content/uploads/2024/08/Res-462-Aprueba-Informe-Tasa-Art.-118-LGSE-VxTx-2024-2027.pdf>
* [C9] CEN, *Transferencias económicas*.
  <https://www.coordinador.cl/mercados/documentos/transferencias-economicas>
* [C10] CEN, *Potencia de suficiencia* (preliminary / definitive
  calculations, monthly and annual balances).
  <https://www.coordinador.cl/mercados/documentos/potencia-de-suficiencia/calculo-definitivo-de-potencia-de-suficiencia>,
  <https://www.coordinador.cl/mercados/documentos/potencia-de-suficiencia/balances-mensuales-de-potencia-de-suficiencia>
* [C11] CNE, *Informe regulatorio SSCC*, May-2025.
  <https://www.cne.cl/wp-content/uploads/2025/05/Informe-regulatorio-SSCC.pdf>
* [C12] CEN, *Informe de Servicios Complementarios 2026* (30-Jun-2025).
  <https://www.coordinador.cl/wp-content/uploads/2025/06/2025.06.30-Informe_SSCC_2026.pdf>
* [C13] DS 32/2025, amends DS 125/2017 (DO 17-Jun-2026).
  <https://energia.gob.cl/sites/default/files/dec_32_2025publicado.pdf>
* [C14] Ley 20.780, art. 8 (green tax), as amended by Ley 21.210.
  <https://www.bcn.cl/leychile/navegar?idNorma=1067194>
* [C15] Ley 20.936 (transmission), art. 25 transitorio.
  <https://www.bcn.cl/leychile/navegar?idNorma=1092695>;
  [C15b] CNE REX 312/2026, *cargo único*.
  <https://www.cne.cl/wp-content/uploads/2026/06/REX-312-2026-1.pdf>
* [C16] CNE REX 722/2025, *cargo por servicio público 2026*.
  <https://www.cne.cl/archivos_bajar/REX%20722-2025.PDF>
* [C17] Ministerio de Energía, draft *Reglamento de Potencia* (not
  published). <https://energia.gob.cl/sites/default/files/nuevo_reglamento_de_potencia.pdf>
* [C18] CNE, *ITP Metodología para la determinación del período de
  control de punta en el SEN*, Sept-2026.
  <https://www.cne.cl/wp-content/uploads/2026/09/ITP-Metodologia-para-la-determinacion-del-periodo-de-control-de-punta-en-el-SEN.pdf>
* [C19] CEN, *Informe semestral UMC 2025a* (Nov-2025).
  <https://www.coordinador.cl/wp-content/uploads/2025/11/INFORME-SEMESTRAL-UMC-2025a.pdf>
* [C20] Reporte Minero, green-tax compensations 2025 (27-May-2026).
  <https://www.reporteminero.cl/noticia/noticias/2026/05/compensaciones-impuesto-verde-generadoras-2025-coordinador>
* [C21] Diario Financiero, Senate Hacienda committee rejects the green
  tax surcharge (2025).
  <https://www.df.cl/empresas/energia/subsidio-electrico-comision-de-hacienda-del-senado-rechaza-sobretasa-de>
* [C22] SII, corporate income tax rate (25-Mar-2026).
  <https://www.sii.cl/preguntas_frecuentes/declaracion_renta/001_140_8809.htm>
* [C23] CNE, *Informe de Costos de Tecnologías de Generación y
  Almacenamiento* (May-2026).
  <https://www.cne.cl/wp-content/uploads/2026/06/ICTGA-2026.pdf>
* [C24] CEN, *Reporte Energético SEN*, Dec-2025.
  <https://www.coordinador.cl/wp-content/uploads/2025/12/CEN_Reporte_Energetico_SEN_Dic25_v2.pdf>
* [C25] Ministerio de Energía, *Reporte de proyectos*, Jan-2026.
  <https://energia.gob.cl/sites/default/files/documentos/reporte_de_proyectos_-_enero_2026.pdf>
* [C26] Funds Society, Fitch on Chilean BESS (10-Apr-2025).
  <https://www.fundssociety.com/es/?p=269413>
* [C27] Systep, *Reporte Sector Eléctrico* Oct-2024.
  <https://systep.cl/wp-content/uploads/102024_Systep_Reporte_Sector_Electrico.pdf>
* [C28] Americas Market Intelligence, *Energy storage in Chile*
  (25-Apr-2024). <https://americasmi.com/insights/energy-storage-in-chile/>
* [C29] Saavedra, Moraga, Moraga, Herrera, ISES Solar World Congress
  2025, [doi:10.18086/swc.2025.09.23](https://doi.org/10.18086/swc.2025.09.23).
* [C30] Aurora Energy Research via ESS News (3-Apr-2025)
  <https://www.ess-news.com/2025/04/03/battery-returns-vary-throughout-chile-solar-colocation-can-double-revenues-aurora-finds/>
  and Energía Estratégica (Jul-2026)
  <https://www.energiaestrategica.com/es/notes/aurora-lanza-su-primer-reporte-de-energia-flexible-para-chile-y-senala-que-la-tir-de-las-baterias-puede-variar-5-en-funcion-del-escenario>
* [C31] DS 32/2025, Ministerio de Energía, amends DS 125/2017
  (*Reglamento de la Coordinación y Operación*), art. 156–162 (DO
  17-Jun-2026).
  <https://www.diariooficial.interior.gob.cl/publicaciones/2026/06/17/44477/01/2825797.pdf>
  (same text as [C13]).
* [C32] CNE, *Norma Técnica de Coordinación y Operación del SEN*,
  Título 3-8 *Garantías*, art. 3-64…3-69.
  <https://www.cne.cl/wp-content/uploads/2021/08/NT-de-Coordinacion-y-Operacion-del-SEN.pdf>
* [C33] CEN, *Garantías* (annual calculations by year; internal
  procedure *Cumplimiento de la Cadena de Pagos en el MCP*, definitive
  version 10-Jun-2026).
  <https://www.coordinador.cl/mercados/documentos/garantias/>,
  <https://www.coordinador.cl/mercados/documentos/garantias/procedimiento>
* [C34] Banco Internacional, tariff sheet *Garantías estatales y otros
  servicios* §2.1 (boleta de garantía: fee up to 1 %, min UF 3).
  <https://www.internacional.cl/files/TarifasProductos/Otros/GARANTIAS_ESTATALES_Y_OTROS_SERVICIOS_4.0.pdf>
* [C35] CNE RE 221/2025, *Bases definitivas Licitación de Suministro
  2025/01*, §4.4.6 and §8.3.
  <https://www.cne.cl/wp-content/uploads/2025/04/RE-N221_Aprueba-Bases-Definitivas-2025-01.pdf>
* [C36] CNE Res. Ex. 432/2026, *costo de falla de corta y larga
  duración* SEN (DO 21-Aug-2026).
  <https://www.diariooficial.interior.gob.cl/publicaciones/2026/08/21/44530/01/2857667.pdf>
* [C37] La Tercera, "Generadoras eléctricas constituyen garantías por
  casi US\$ 700 millones para 2023" (2023).
  <https://www.latercera.com/pulso-pm/noticia/generadoras-electricas-constituyen-garantias-por-casi-us-700-millones-para-2023-un-45-mas-que-el-ano-anterior/CBTTB3GV2JFFVFNTZG5ZN2ENK4/>
