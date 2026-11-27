# Investment Economics — Does the Optimal Plan Pay for Itself?

gtopt minimises the discounted cost of investment plus operation.  Its
LP duals are prices: the bus balance dual is the nodal energy price
(LMP), the dual of each capacity row is the scarcity value of one more
MW (or MWh).  This page shows, with small cases from the literature
solved with HiGHS, that **at those prices every project the LP builds
recovers its operating cost plus its annualised investment exactly**,
that a candidate the LP does not build would not recover its cost, and
what changes when investments come in integer modules (MIP) or are
financed at a private rate different from the planner's social rate.

The cost-recovery tables are produced by the
[`gtopt_cost_recovery`](#8-the-gtopt_cost_recovery-tool) tool from a
solved case.

## 1. Theory

### 1.1 Annualised investment

An investment of overnight cost $I$ [\$/MW] with life $n$ years,
financed at rate $\rho$, is equivalent to a constant annual payment
(annuity) $K = I \cdot \mathrm{CRF}(\rho, n)$ with the capital recovery
factor

$$
\mathrm{CRF}(\rho, n) = \frac{\rho (1 + \rho)^n}{(1 + \rho)^n - 1}.
$$

gtopt takes the annuity as `annual_capcost` [\$/MW-year] per element
(Generator, Line, Battery energy, Battery power through its Converter)
or computes it from `overnight_capcost`, `economic_life` and `wacc`
(§7).  A stage of $T_t$ hours is charged $K \cdot T_t / 8760$ per MW
built so far, discounted by the stage factor $\delta_t$ (the social
`annual_discount_rate`) and divided by `scale_objective`
([formulation §4.3](../formulation/mathematical-formulation.md#43-investment-costs-capex)).
The output duals are unfolded (÷ probability · discount · duration ·
1/scale), so `Bus/balance_dual` is in undiscounted \$/MWh and every
money figure below is in plain \$.  The demonstration cases use one
stage whose blocks add up to 8760 h, so the annuity is charged once,
or a representative day (24 h), where the charge is $K \cdot 24/8760$.

### 1.2 Zero profit: complementary slackness

For a generator $g$ with capacity $x_g$ built at annuity $K_g$ and
dispatch $p_{g,b} \le x_g$ in blocks of $\Delta_b$ hours, the LP
optimality conditions are

$$
\lambda_b - c_g = \mu_{g,b} - \nu_{g,b},
\qquad
\textstyle\sum_b \mu_{g,b} \Delta_b \le K_g \quad (= \text{ if } x_g > 0),
$$

with $\mu_{g,b} \ge 0$ the dual of $p_{g,b} \le x_g$ and $\nu_{g,b} \ge 0$
that of $p_{g,b} \ge 0$.  Multiplying by $p_{g,b}\Delta_b$ and summing
(complementary slackness: $\mu_{g,b}>0 \Rightarrow p_{g,b}=x_g$,
$\nu_{g,b} > 0 \Rightarrow p_{g,b} = 0$):

$$
\underbrace{\textstyle\sum_b (\lambda_b - c_g) p_{g,b} \Delta_b}_{\text{operating margin}}
= x_g \textstyle\sum_b \mu_{g,b} \Delta_b = K_g x_g .
$$

Energy revenue minus operating cost equals the annualised investment:
**profit is zero for every project built strictly inside its bounds**.
A candidate not built has marginal value
$\sum_b \mu_{g,b}\Delta_b \le K_g$ — at those prices it would lose money.  This is the long-run
competitive equilibrium of marginalist theory: the planner's optimum is
the outcome of a market of price-taking investors when costs are convex
(Boiteux [[1]](#ref1), Steiner [[2]](#ref2), Caramanis [[4]](#ref4),
Schweppe et al. [[3]](#ref3), Stoft [[5]](#ref5) Part 2).  Exceptions:
a project at its upper bound (`expmod` or `capmax`) earns a positive
rent; existing capacity earns inframarginal rent without capex.

**Where the peaker's money comes from.**  A project is not paid by
failures of the system.  In a well-posed expansion study the candidate
capacity is ample and the value of lost load (`demand_fail_cost`) is
above the break-even price of the cheapest peak capacity,
$c_{\text{peak}} + K_{\text{peak}}/\Delta_1$ ($\Delta_1$ the hours of
the top block); the optimum then never sheds load.  In the top block
every unit runs at its capacity, no unit is marginal, and the price is

$$
\lambda_b = c_{\text{set},b} + \frac{\mu_b}{\Delta_b},
$$

with $c_{\text{set},b}$ the variable cost of the costliest running unit
and $\mu_b$ the capacity dual of the scarce capacity: the **capacity
rent** of Boiteux–Steiner peak-load pricing [[1]](#ref1),
[[2]](#ref2).  Each unit's operating margin splits accordingly into a
*variable-cost margin* $\sum_b (c_{\text{set},b} - c_g) p_{g,b}\Delta_b$
(inframarginal rent below the price setter) and a *capacity-rent
component* $\sum_b (\mu_b/\Delta_b) p_{g,b}\Delta_b$, which is all the
peaker earns.  The rent is a property of the capacity constraint, not of
lost load: capping energy prices at the costliest running variable cost
removes it and leaves every unit with missing money [[5]](#ref5),
[[13]](#ref13), unless a capacity payment returns it (§2.1).
`gtopt_cost_recovery` prints this split per project and, with
`--price-blocks`, the decomposition of every block price; margin earned
in blocks with unserved energy is reported separately as *failure rent*
and flagged, because a plan that relies on it is under-built.

### 1.3 Transmission: congestion rent

For a line $\ell$ from bus $a$ to $b$ with flow $f_{\ell,b}$ and
capacity $x_\ell$, the same argument on the rows $|f| \le x_\ell$ gives

$$
\textstyle\sum_b (\lambda_{b,t} - \lambda_{a,t}) f_{\ell,t} \Delta_t
= K_\ell x_\ell
$$

in a lossless transport model: the congestion rent (merchandising
surplus) pays the line [[6]](#ref6), [[7]](#ref7).  A candidate corridor
not built has price spread
$\sum_t |\lambda_b - \lambda_a| \Delta_t \le K_\ell$.  With Kirchhoff's laws the rent of one line also depends on
loop flows and can be negative; the identity then holds for the network
as a whole, not line by line [[6]](#ref6).  The demonstration uses
Garver's transport model, for which it holds per line.

### 1.4 Storage: arbitrage

A battery buys $c_t$ and sells $d_t$ at the bus price, subject to its
energy capacity $E$ and power capacity $P$.  Its duals give

$$
\textstyle\sum_t \lambda_t (d_t - c_t) \Delta_t
= K_E E + K_P P ,
$$

arbitrage revenue pays the energy and the power investment
[[12]](#ref12) when both are built inside their bounds (round-trip
losses are inside the left side; standing loss is set to 0 here).

### 1.5 Integer modules: missing money and uplift

With integer modules (`integer_expmod`, MIP) there are no LP duals.
gtopt solves a pricing LP after the MIP (`model_options.pricing_mode`):
`fixed` pins every integer at the incumbent (the restricted / IP prices
of O'Neill et al. [[8]](#ref8)), `relaxed` uses the LP relaxation
(an approximation of convex-hull prices [[9]](#ref9), [[10]](#ref10)).
Lumpy investments break zero profit: at the fixed-integer prices some
projects under-recover (missing money) and others over-recover.  The
side payment a built project needs is the **make-whole uplift**
$\max(0, -\text{profit})$.  Convex-hull pricing minimises total uplift;
the relaxed prices are its practical stand-in.

## 2. Generation mix: peak-load pricing

Case [`cases/invest_screening_1b`](../../cases/invest_screening_1b/):
one bus, a six-block load-duration curve of 8760 h (1020 MW for 20 h
down to 400 MW for 3780 h), value of lost load 10,000 \$/MWh and four
candidate technologies with annuities at 8 % (overnight cost, life,
variable cost): base 3.0 M\$/MW, 30 y, 10 \$/MWh; mid 1.1 M\$/MW, 25 y,
45 \$/MWh; peak 0.5 M\$/MW, 20 y, 110 \$/MWh; nuclear 6.0 M\$/MW, 40 y,
5 \$/MWh.  This is the textbook screening-curve problem of Steiner and
Boiteux [[1]](#ref1), [[2]](#ref2).  Every candidate may be built up to
2000 MW, and the VOLL is well above the peaker's break-even price in the
top block ($110 + 50{,}926/20 = 2{,}656$ \$/MWh), so the optimum serves
all load.

The LP builds 550 / 150 / 320 MW, exactly the 1020-MW peak; no energy is
unserved.  Objective 243,147,616 \$.

| Project | Built MW | Revenue \$ | Opex \$ | Capex \$ | Profit \$ | Value / cost per MW \$ |
|---------|---------:|-----------:|--------:|---------:|----------:|-----------------------:|
| base    | 550 | 189,075,267 | 42,510,000 | 146,565,268 | 0 | 266,482 / 266,482 |
| mid     | 150 |  28,821,999 | 13,365,000 |  15,456,999 | 0 | 103,047 / 103,047 |
| peak    | 320 |  25,250,352 |  8,954,000 |  16,296,352 | 0 |  50,926 /  50,926 |
| nuclear |   0 | — | — | — | — | 310,282 / 503,161 |

Every built unit recovers operation plus annuity exactly; nuclear would
earn 310,282 \$/MW against an annuity of 503,161 \$/MW, so it is not
built.  Load payments equal generator revenue (market residual 0).

Where the money comes from (`gtopt_cost_recovery --price-blocks`):

| Block | Hours | Load MW | Price \$/MWh | Set by (variable cost) | Capacity rent \$/MWh |
|------:|------:|--------:|-------------:|------------------------|---------------------:|
| 1 |   20 | 1020 | 2,656.31 | peak, at capacity (110) | 2,546.31 |
| 2 |   60 |  950 |   110.00 | peak, part-loaded (110) | 0 |
| 3 |  400 |  850 |   110.00 | peak, part-loaded (110) | 0 |
| 4 | 1500 |  700 |    58.95 | mid, at capacity (45) | 13.95 |
| 5 | 3000 |  550 |    41.38 | base, at capacity (10) | 31.38 |
| 6 | 3780 |  400 |    10.00 | base, part-loaded (10) | 0 |

| Project | Margin \$ | Variable-cost margin \$ | Capacity-rent component \$ |
|---------|----------:|------------------------:|---------------------------:|
| base | 146,565,267 | 55,275,000 | 91,290,267 |
| mid  |  15,456,999 |  4,680,000 | 10,776,999 |
| peak |  16,296,352 |          0 | 16,296,352 |

The peaker never earns a variable-cost spread: its whole margin is the
capacity rent of the top block, $2{,}546.31 \times 20 = 50{,}926$
\$/MW, exactly its annuity.  The top-block price is not a failure
price: no load is shed, and it is the peaker's variable cost plus the
dual of the capacity constraint divided by the 20 hours
(Boiteux–Steiner).  Blocks 4 and 5 carry a smaller capacity rent
because, with a coarse six-block curve, the base and mid capacities sit
exactly on a block load; with a finer load-duration curve those rents
move toward the screening-curve break points and the capacity rent
concentrates at the peak, as in Boiteux's continuous model.

### 2.1 Contrast: a cost-based market (Chile)

In a cost-based market such as Chile's, the Coordinador dispatches on
audited variable costs and the spot price (*costo marginal*) is the
variable cost of the marginal unit; it never contains the capacity
rent.  In the top block above the price would be 110 \$/MWh, not
2,656: the peaker earns nothing from energy, and every unit loses the
capacity-rent component of its margin.  The peak unit's investment is
paid instead by the sufficiency payment (*pago por potencia de
suficiencia*, DS 62/2006 [[32]](#ref32)), an administered capacity
price set from the annuity of a reference peak unit (8.77 US\$/kW-month
in CNE RE 803/2025 [[33]](#ref33)) — never by energy prices and never
by failure: shedding is priced at the *costo de falla* only in
rationing.

The planning optimum and that market coincide when the administered
capacity price equals the capacity rent the LP computes per MW of peak
capacity: here 50,926 \$/MW-year (4.24 US\$/kW-month, the annuity of the
illustrative peaker at 8 % over 20 years).  `gtopt_cost_recovery`
gives that rent per project and per block, so a study can compare it
with the regulated price; the full Chilean revenue stack (energy at
costo marginal, sufficiency, ancillary services) is evaluated in
[Private-investor evaluation, Chile](private-investor-evaluation-cl.md)
(branch `feat/private-investor-eval`).

## 3. Transmission: Garver 6-bus

Case [`cases/invest_garver_6b`](../../cases/invest_garver_6b/): the
Garver system [[11]](#ref11) with the data of Romero et al.
[[14]](#ref14) (15 corridors, up to 4 new circuits each, circuit costs
in M\$ annualised at 8 % over 40 years), solved as a transport model
(`use_kirchhoff: false`).  Three generators (bus 1 150 MW at 40 \$/MWh,
bus 3 360 MW at 30, bus 6 600 MW at 15) serve the 760 MW Garver load,
scaled to 100 / 80 / 60 % over 1000 / 4000 / 3760 h.

The LP (objective 92,391,705 \$, congestion rent 21,850,605 \$) expands
four corridors:

| Corridor | New MW | Congestion rent on new MW \$ | Capex \$ | Profit \$ |
|----------|-------:|-----------------------------:|---------:|----------:|
| 2–6 | 400 | 10,063,221 | 10,063,220 | 0 |
| 4–6 | 200 |  5,031,610 |  5,031,610 | 0 |
| 3–5 |  24 |    402,529 |    402,529 | 0 |
| 2–5 |  16 |    415,946 |    415,946 | 0 |

Corridor 3–5 has 100 MW existing; its total rent (2,079,732 \$) is
shared pro rata, and the share of the 24 new MW equals their annuity.
Every corridor not expanded has a price spread
$\sum_t |\lambda_b - \lambda_a|\Delta_t$ not above its annuity per MW,
for example 1–6: 34,383 vs 81,464 \$/MW; 5–6: 51,155 vs 65,583; 3–6:
34,383 vs 40,253.  Corridor 1–5 sits exactly at the margin (16,772 =
16,772): the LP is indifferent to building it.

## 4. Storage: battery arbitrage

Case [`cases/invest_bat_1b`](../../cases/invest_bat_1b/): one bus, one
representative 24-h day with 400 MW of solar (with its profile), a coal
unit (120 MW, 35 \$/MWh) and a gas unit (200 MW, 90 \$/MWh), and a
candidate battery with 92 % charge and discharge efficiency and separate
annuities for energy (200 k\$/MWh, 15 y, 8 %: 23,365.91 \$/MWh-year) and
power (100 k\$/MW: 11,682.95 \$/MW-year, on the converter).

The LP builds 288.88 MWh and 84 MW.  The battery charges at the midday
prices (0–17.5 \$/MWh) and discharges in the evening at 90 \$/MWh.
Per day, arbitrage revenue 21,182 \$ = energy capex 18,493 \$ + power
capex 2,689 \$; profit 0.  Both capacity duals match their annuities
(64.02 \$/MWh and 32.01 \$/MW per day).  Objective 160,200.52 \$.

## 5. Integer modules: gaps and uplift

The MIP variants restrict builds to integer modules (`integer_expmod`)
and price with `pricing_mode: fixed` (integers pinned) or `relaxed`
(LP relaxation).  The MIP solution and objective are the same in both
runs; only the prices change.

**Screening** (modules base 300, mid 200, peak 50 MW; nuclear 300):
the MIP builds 600 / 200 / 250 MW, 30 MW above the peak, and serves all
load (objective 251,474,237 \$, 3.4 % above the LP).

| Project | MW | Profit, fixed \$ | π/capex | Profit, relaxed \$ | π/capex |
|---------|---:|-----------------:|--------:|-------------------:|--------:|
| base | 600 | −99,589,386 | −62.3 % | −4,706,786 | −2.9 % |
| mid  | 200 | −14,369,333 | −69.7 % | −2,092,057 | −10.2 % |
| peak | 250 | −12,731,526 | −100.0 % | −1,527,784 | −12.0 % |
| make-whole uplift | | 126,690,245 | | 8,326,626 | |

The fixed-integer prices see 30 MW of spare peak capacity, so the
part-loaded peaker sets 110 \$/MWh in the top block: the capacity rent
disappears and every unit is short (the peaker recovers nothing above
its fuel).  The relaxed prices restore the rent (2,656.31 \$/MWh in the
top block, as in the LP) and cut the uplift by 93 %.  When the integer
capacity matches the peak exactly, the fixed-integer dual of the top
block is indeterminate between the peaker's cost and the VOLL and a
solver may return the VOLL: a failure price without failure.
`gtopt_cost_recovery` counts margin earned at a price equal to the
failure cost as *failure rent* and warns; such prices must not be read
as revenue.

**Garver** (whole circuits): the MIP builds one circuit on 1–5 and three
on 2–6 and on 4–6 (objective 93,650,433 \$).  At fixed prices 1–5 earns
nothing (−100 %) and 2–6 / 4–6 recover 60 % (−40.4 %); uplift
7,772,032 \$.  At relaxed prices 2–6 and 4–6 break even and 1–5
recovers 63 % (−613,857 \$); uplift 613,857 \$.

**Battery** (energy in 50 MWh and power in 25 MW modules, both
integer): the MIP builds 300 MWh and 100 MW (objective 160,506.82 \$).
At fixed prices the battery recovers 60 % (−8,979 \$/day, −40.1 %); at
relaxed prices it is 306 \$/day short (−1.4 %).  `integer_expmod` on a
battery makes both its energy and its power modules integer.

## 6. Many small modules or one large block

Case [`cases/invest_wacc_1b`](../../cases/invest_wacc_1b/) expresses a
classic choice with the existing module fields, no new concept: on the
load curve of §2, base load can be built either as `base_small` (50-MW
modules, `expmod ≤ 16`, 3.2 M\$/MW) or as `base_large` (one 600-MW
block, `expmod ≤ 1`, 2.6 M\$/MW: economies of scale), both with
30-year life; mid and peak as in §2.  Each candidate carries its own
`overnight_capcost`, `economic_life` and `wacc` (§7.3).

| Variant | base_small | base_large | Annuity small / large \$/MW | Objective \$ |
|---------|-----------:|-----------:|----------------------------:|-------------:|
| uniform 8 %, LP | 0 | 550 | 284,248 / 230,951 | 223,605,582 |
| small 7 %, large 10 %, LP | 550 | 0 | 257,876 / 275,806 | 238,414,422 |

With one rate the large block wins on scale.  When the small modules
can be financed at 7 % (e.g. contracted, lower risk) and the large
block costs 10 %, the ranking flips.  In both LPs the winner recovers
its annuity exactly at its own rate and the loser's marginal value
(230,951 and 257,876 \$/MW) is below its annuity.

In the MIP (integer modules) both variants build 350 MW of peakers
(50-MW modules) for the 1020-MW peak, so the fixed-integer prices lose
the top-block capacity rent and every unit is short; the uplift is
103,279,598 \$ with the lumpy 600-MW block (uniform 8 %) and
58,381,714 \$ with 50-MW base modules (per-project rates).  At relaxed
prices: 4,458,015 \$ and 2,501,290 \$.  Granularity reduces missing
money.

The small end-to-end case
[`cases/invest_wacc_2c`](../../cases/invest_wacc_2c/) (one 8760-h block,
100 MW load) has two candidates differing in rate and cost: `small`
(10-MW modules, 1.2 M\$/MW, 9 %: 122,168 \$/MW-year) and `large`
(100-MW modules, 1.0 M\$/MW, 7 %: 85,811 \$/MW-year).  The LP builds
100 MW of `large`, whose revenue 17,341,052 \$ = opex 8,760,000 +
capex 8,581,052; `small` is worth 85,811 \$/MW, below its 122,168.

## 7. Social vs private discount rates

### 7.1 Two rates, two jobs

A planner discounts the future at a **social** rate (the time value of
consumption to society); an investor finances a project at its
**private** cost of capital (WACC), which includes the project's risk,
taxes and financing structure.  The two differ by several points, and
the private rate differs across technologies.  The established
modelling practice is to use the social rate for the present value of
the plan and the project WACC only inside the annuity (CRF) of each
investment:

| Source | Present value | Investment annuity |
|--------|---------------|--------------------|
| TIMES (IEA-ETSAP) [[19]](#ref19) | general rate $d(y)$ | technology-specific rate, annuity only |
| ReEDS (NREL) [[20]](#ref20) | private investment rate in the objective; social 2 % for reporting | `FinancingMult` = 1 + (WACC<sub>tech</sub> − WACC<sub>sys</sub>)·AF |
| GenX [[21]](#ref21) | `WACC` key = general rate | technology WACC in the annualised cost |
| MacroEnergy.jl [[22]](#ref22) | general rate | CRF from technology WACC, fallback general rate |
| AEMO ISP 2026 [[23]](#ref23) | 7.0 % real pre-tax, technology neutral | technology WACCs reported: regulated transmission 3.0 %, solar 7.0 %, wind 7.5 %, BESS 8.0 %, pumped hydro 8.5 % |
| ENTSO-E CBA 4.0 draft [[24]](#ref24) | 4 % real | — |
| CAISO TPP 2024-25 [[25]](#ref25) | social 7 % real (5 % sensitivity) | utility revenue requirement: debt 6 %, equity 11 % |
| Chile, CNE transmission planning [[26]](#ref26), [[27]](#ref27) | social rate of the Ministry of Social Development (Ley 20.530) | V.A.T.T. at the art. 118 LGSE rate, 7–10 % after tax; 7.00 % for 2024–2027 |
| UK Green Book (2026) [[28]](#ref28) | STPR 3.00 % (years 1–30), then 2.50 %, 2.25 % | DESNZ/CEPA hurdle rates [[29]](#ref29): solar and onshore 7.6 %, offshore 8.9 %, floating 11.4 %, LDES 10.1 % |
| US OMB Circular A-4 (2023) [[30]](#ref30) | 2 % default; 7 % dropped | shadow price of capital for displaced investment |

The academic literature supports declining long-term social rates
(Arrow et al. [[15]](#ref15)) and technology-specific financing costs in
equilibrium and planning models (Ehrenmann and Smeers [[16]](#ref16);
Steffen [[17]](#ref17); García-Gusano et al. [[18]](#ref18), who show
the choice of rate changes the optimal mix of a TIMES model; and the
IEA cost-of-capital survey [[31]](#ref31), where solar can be less
risky than gas in emerging economies).

### 7.2 What changes, and what does not

With per-project rates the planner's optimum is still a zero-profit
equilibrium (§1.2): each built project recovers its annuity **at its
own rate**.  What changes is the mix.  Case `invest_screening_1b`,
variants `social` (6 % everywhere) and `private` (base 11 %, mid 8 %,
peak 9 %, nuclear 12 %; present value at 6 %):

| Variant | base | mid | peak | Objective \$ | Profit of built |
|---------|-----:|----:|-----:|-------------:|----------------:|
| social 6 % | 550 | 150 | 320 | 211,556,640 | 0 |
| private WACC | 400 | 300 | 320 | 277,444,955 | 0 (±4 \$ rounding) |

The capital-intensive base unit loses 150 MW to the mid unit.  Valued
at social annuities, the private-rate mix costs 217,917,039 \$
(capex 126,943,039 + opex 90,974,000), 6.36 M\$
(3.0 %) more than the social optimum: that is the welfare cost of the
financing wedge, which a planner may want to reduce (contracts,
regulated returns, de-risking) rather than to assume away.

Caveats.  The planner's plan reproduces a market only partially:
project margins are not annuities (they are volatile and front- or
back-loaded), and the rate used for present value defines what the
plan means (social optimum vs investor-consistent forecast).  Declining
social rates make plans time-inconsistent when re-optimised.  Adding
risk premia to the WACC and also to the scenarios counts risk twice;
behavioural "hurdle rates" are not costs of capital.

### 7.3 gtopt support

Every expandable Generator, Line, Battery (energy), Converter (power),
Demand and CarrierConverter accepts, besides `annual_capcost`:

| Field | Unit | Meaning |
|-------|------|---------|
| `overnight_capcost` | \$/MW (\$/MWh for battery energy) | overnight investment cost |
| `economic_life` | years | annuity period $n$ |
| `wacc` | p.u./year | private rate $\rho$ of this element |
| `power_overnight_capcost` (Battery) | \$/MW | power axis, forwarded to the converter with `economic_life` and `wacc` |
| `fixed_om_cost` | \$/MW-year (\$/MWh-year for battery energy) | fixed O&M of the capacity built, charged with the annuity |
| `power_fixed_om_cost` (Battery) | \$/MW-year | fixed O&M of the power axis, forwarded to the converter |

All are stage schedules.  Precedence per stage: an explicit
`annual_capcost` wins; else
$K = I \cdot \mathrm{CRF}(\rho, n)$ with $I$ = `overnight_capcost`,
$\rho$ = `wacc` and $n$ = `economic_life`;
if `wacc` is unset it falls back to `simulation.annual_discount_rate`;
with no life, $K = \rho\cdot I$ (perpetuity).  Stage discounting keeps
using the social `annual_discount_rate`.  A case without the new fields
builds the same LP as before.  See
[formulation §4.3](../formulation/mathematical-formulation.md#43-investment-costs-capex)
and [input data](../input-data.md).

**Fixed O&M.**  `fixed_om_cost` is an absolute cost per unit of
capacity and year, added to the annuity of every unit built from that
stage on, with the same stage discounting: the LP charges
$K + F$ per MW-year.  An absolute value rather than a fraction of the
overnight cost (e.g. 2 %/year) because cost databases (NREL ATB, CNE
cost reports) quote fixed O&M in \$/kW-year, and because it applies
equally when the candidate gives `annual_capcost` instead of
`overnight_capcost`; a percentage is converted by the user as
$F = f \cdot I$.  The zero-profit identity of §1.2 then reads
margin = annuity + fixed O&M, and `gtopt_cost_recovery` reports the
fixed O&M as an opex component (capex = the annuity only).  Unset, the
LP is unchanged.

Case `invest_screening_1b`, variant `fom` (8 %, fixed O&M base 60, mid
25, peak 10, nuclear 100 k\$/MW-year): the mix shifts to 400 / 300 /
320 MW (objective 279,477,270 \$) and every built unit's margin equals
its annuity plus its fixed O&M (base 106,592,915 + 24,000,000; mid
30,913,999 + 7,500,000; peak 16,296,351 + 3,200,000).  The top-block
price rises to 110 + 60,926 / 20 = 3,156.31 \$/MWh: the capacity rent
now pays the peaker's fixed O&M too.  The e2e case
[`cases/invest_fom_1c`](../../cases/invest_fom_1c/) checks the same
identity on one candidate.

When two candidates have identical annuities (e.g. same cost and the
same rate) the LP is degenerate: the split between them is arbitrary and
depends on the solver; only the total is determined.

**Recommendation.**  Keep `simulation.annual_discount_rate` at the
social rate of the jurisdiction and give each candidate its
`overnight_capcost`, `economic_life` and `wacc`.  Run the plan once with
a uniform rate as well: the difference in objective and mix is the
cost of the financing wedge (§7.2).

## 8. The `gtopt_cost_recovery` tool

```bash
gtopt case.json --solver highs \
  --output-directory out --write-out all
gtopt_cost_recovery out                    # table per project
gtopt_cost_recovery out_fixed out_relaxed  # side-by-side comparison
gtopt_cost_recovery out --per-year --json-out rec.json --csv-out rec.csv
gtopt_cost_recovery out --price-blocks     # λ = set cost + capacity rent
```

It reads `planning.json` (written to the output directory) and the
solution and dual CSV/Parquet files, and reports per expandable project:
revenue at the bus prices (`Bus/balance_dual`), opex at `srmc_sol` or
`gcost`, annualised capex $\sum_t \text{capacost}\cdot T_t\cdot\delta_t$,
profit, profit/capex and make-whole uplift; its marginal value per unit
(generators: $\sum \max(\lambda - c, 0) \cdot a \cdot \Delta$ with $a$
the profile factor; lines:
$\sum \max(|\lambda_b - \lambda_a| - \tau, 0) \cdot \Delta$;
battery energy and power: capacity duals; demands: value minus price;
carrier converters: output value minus input cost) against its cost per
unit; and the market balance (load payments = generator revenue +
battery net revenue + converter net revenue + congestion rent).
Each generator's margin is split into a *variable-cost margin*, a
*capacity-rent component* and a *failure rent* (§1.2): every bus price
is decomposed as $\lambda = c_{\text{set}} + r$, with $c_{\text{set}}$
the variable cost of the costliest unit running in the bus's price zone
(buses with the same price) — or $\lambda$ itself when a flexible
demand, a converter or a storage unit inside its limits sets the price
— and $r$ the capacity rent, or the failure component in a block with
unserved energy or a price at the failure cost.  `--price-blocks`
prints the decomposition of every block; a positive failure rent is
warned about.  For a project with existing
capacity the margin is split pro rata between the existing and the new
MW.  `--include-existing` lists the inframarginal rent of
non-expandable generators.  Limitations: line rents per line hold in
the transport model (with Kirchhoff only network-wide); line losses are
not attributed; multi-scenario and multi-stage cases are weighted by
probability and discount factor.

Reproduce every table on this page (HiGHS; each case README lists its
variants):

```bash
cd cases/invest_screening_1b
gtopt invest_screening_lp.json --solver highs \
  \
  --output-directory out_lp --write-out all
gtopt invest_screening_mip.json --solver highs \
  \
  --output-directory out_mip --write-out all
gtopt invest_screening_mip.json --solver highs \
  \
  --output-directory out_mip_relaxed --write-out all \
  --set model_options.pricing_mode=relaxed
gtopt_cost_recovery out_lp out_mip out_mip_relaxed
```

The outputs the tables were computed from are committed under
`cases/<case>/golden/<variant>/` (the files the tool reads), so
`gtopt_cost_recovery cases/invest_screening_1b/golden/lp` works without
a solver; the Python tests check them.

## 9. Demand-side and public projects

Case [`cases/invest_multi_3b`](../../cases/invest_multi_3b/) puts every
kind of candidate in one system: three buses (north, city, coast), one
representative 24-h day, an existing coal unit (250 MW, 40 \$/MWh, city)
and gas unit (400 MW, 90 \$/MWh, coast), and candidates solar (north,
7 %), CCGT (city, 8 %), three corridors (5 %), a battery (city, 8 %) and
two demand-side projects:

* **Electrolyser (private).**  A `CarrierConverter` electric → hydrogen
  at the north bus (70 % LHV efficiency, 2 \$/MWh, 0.9 M\$/MW, 20 y,
  `wacc` 9 %) feeding a `HydrogenNode` with a `HydrogenDemand` of up to
  150 MW<sub>LHV</sub> valued at 3 \$/kg (90.01 \$/MWh<sub>LHV</sub>,
  1 kg = 33.33 kWh).  It is a merchant project: it buys electricity at
  the LMP and sells hydrogen at the hydrogen-node price.
* **Desalination (public).**  A reverse-osmosis plant at the coast
  (3.5 kWh/m³ [[35]](#ref35)) modelled as an expandable `Demand` (up to
  80 MW<sub>e</sub>, 6.86 M\$/MW<sub>e</sub> = 1,000 \$ per m³/day of
  capacity, 25 y) with **no `wacc`**: its annuity uses the social
  `annual_discount_rate` (6 %).  Its `fcost` is the social willingness
  to pay for water converted to electricity,
  $1.2\ \$/\text{m}^3 \div 3.5\ \text{kWh/m}^3 = 342.86$ \$/MWh.

Why a `Demand` and not a water network: gtopt's water carrier belongs
to the hydro `Junction`s and has no node a desalination plant could
feed; the plant's only interaction with the power system is its
consumption, and an expandable demand with `fcost` = value of service
expresses exactly that.  Minimising capex + energy cost + `fcost` ×
unserved is maximising water value minus electricity cost minus capex:
the social surplus of the plant, the criterion of public cost–benefit
analysis [[34]](#ref34).  The only C++ addition is
`overnight_capcost` / `economic_life` / `wacc` on `Demand` and
`CarrierConverter` (§7.3), with `wacc` unset meaning the social rate.

The LP (objective 542,786 \$/day, no unserved energy) builds:

| Project | Built | Revenue \$/day | Opex \$/day | Capex \$/day | Profit \$/day |
|---------|------:|---------------:|------------:|-------------:|--------------:|
| solar | 916.6 MW | 150,838 | 0 | 150,838 | 0 |
| CCGT | 76.0 MW | 86,386 | 66,880 | 19,506 | 0 |
| north–city | 484.7 MW new | 28,006 (rent) | 0 | 23,216 | 0 |
| battery | 212.2 MWh / 60.7 MW | 15,527 | 0 | 15,527 | 0 |
| electrolyser | 214.3 MW<sub>e</sub> | 166,014 (H₂) | 108,132 | 57,882 | 0 |
| desalination | 80.0 MW<sub>e</sub> | 658,286 (water) | 106,973 | 117,570 | 433,743 |

Every private project recovers electricity / hydrogen revenue = opex +
annuity at its own WACC (the north–city rent is shared pro rata with
the existing 100 MW).  The electrolyser is a price setter: in the night
hours it runs part-loaded and fixes the LMP at
$0.7 \times 90.01 - 2 = 61.01$ \$/MWh, the value of electricity turned
into hydrogen.  The desalination plant is at its bound (80 MW: the
water need): water value 658,286 \$/day against electricity 106,973 and
the social-rate annuity 117,570 \$/day, a benefit–cost ratio of 2.93;
per m³, 0.41 \$ of cost (0.20 energy + 0.21 capital) against a
willingness to pay of 1.20.  A public project at its bound earns a
positive social surplus, not zero profit (§1.2).  No project earns a
failure rent; the generators' capacity rents are 2,395 \$/day (solar)
and 4,301 \$/day (CCGT).

**The desalination demand raises the prices that pay the generators.**

| Run | Mean LMP north / city / coast \$/MWh | Generator revenue \$/day |
|-----|-------------------------------------:|-------------------------:|
| plan without desalination | 54.17 / 56.16 / 55.26 | 541,286 |
| same fleet + desalination (short run) | 56.88 / 61.88 / 61.88 | 649,234 |
| plan with desalination (long run) | 54.17 / 56.16 / 55.72 | 625,664 |

With the fleet planned without it, adding the plant raises the mean
coast price by 12 % and the generators' revenue by 20 %.  That extra
rent is what pays the capacity built for it: in the long-run plan solar
grows by 59 MW, the CCGT by 30 MW and the north–city corridor by
49 MW, and prices return close to their long-run level (set by
technology costs) while generator revenue stays 16 % higher on 12 %
more energy.

MIP (integer modules, `integer_expmod` on every candidate): objective
544,248 \$/day; uplift 27,328 \$/day at fixed-integer prices (the
corridor −100 %, solar −2.4 %, CCGT −8.4 %; electrolyser +23.7 %) and
649 \$/day at relaxed prices.

## References

1. <a id="ref1"></a>M. Boiteux, "Peak-load pricing," *J. Business*,
   33(2):157–179, 1960. [doi:10.1086/294331](https://doi.org/10.1086/294331)
2. <a id="ref2"></a>P. O. Steiner, "Peak loads and efficient pricing,"
   *Q. J. Economics*, 71(4):585–610, 1957.
   [doi:10.2307/1885712](https://doi.org/10.2307/1885712)
3. <a id="ref3"></a>F. C. Schweppe, M. C. Caramanis, R. D. Tabors and
   R. E. Bohn, *Spot Pricing of Electricity*, Kluwer, 1988.
   [doi:10.1007/978-1-4613-1683-1](https://doi.org/10.1007/978-1-4613-1683-1)
4. <a id="ref4"></a>M. Caramanis, "Investment decisions and long-term
   planning under electricity spot pricing," *IEEE Trans. PAS*,
   101(12):4640–4648, 1982.
   [doi:10.1109/TPAS.1982.317321](https://doi.org/10.1109/TPAS.1982.317321)
5. <a id="ref5"></a>S. Stoft, *Power System Economics*, IEEE
   Press / Wiley, 2002.
   [doi:10.1109/9780470545584](https://doi.org/10.1109/9780470545584)
6. <a id="ref6"></a>I. J. Pérez-Arriaga, F. J. Rubio, J. F. Puerta,
   J. Arceluz and J. Marín, "Marginal pricing of transmission services:
   an analysis of cost recovery," *IEEE Trans. Power Systems*,
   10(1):546–553, 1995.
   [doi:10.1109/59.373981](https://doi.org/10.1109/59.373981)
7. <a id="ref7"></a>W. W. Hogan, "Contract networks for electric power
   transmission," *J. Regulatory Economics*, 4(3):211–242, 1992.
   [doi:10.1007/BF00133621](https://doi.org/10.1007/BF00133621)
8. <a id="ref8"></a>R. P. O'Neill, P. M. Sotkiewicz, B. F. Hobbs,
   M. H. Rothkopf and W. R. Stewart, "Efficient market-clearing prices
   in markets with nonconvexities," *Eur. J. Oper. Res.*,
   164(1):269–285, 2005.
   [doi:10.1016/j.ejor.2003.12.011](https://doi.org/10.1016/j.ejor.2003.12.011)
9. <a id="ref9"></a>P. R. Gribik, W. W. Hogan and S. L. Pope, "Market-clearing
   electricity prices and energy uplift," Harvard working paper, 2007.
   [link](https://hepg.hks.harvard.edu/files/hepg/files/gribik_hogan_pope_price_uplift_123107.pdf)
10. <a id="ref10"></a>B. Hua and R. Baldick, "A convex primal formulation
    for convex hull pricing," *IEEE Trans. Power Systems*,
    32(5):3814–3823, 2017.
    [doi:10.1109/TPWRS.2016.2637718](https://doi.org/10.1109/TPWRS.2016.2637718)
11. <a id="ref11"></a>L. L. Garver, "Transmission network estimation
    using linear programming," *IEEE Trans. PAS*, 89(7):1688–1697, 1970.
    [doi:10.1109/TPAS.1970.292825](https://doi.org/10.1109/TPAS.1970.292825)
12. <a id="ref12"></a>R. Sioshansi, P. Denholm, T. Jenkin and J. Weiss,
    "Estimating the value of electricity storage in PJM: arbitrage and
    some welfare effects," *Energy Economics*, 31(2):269–277, 2009.
    [doi:10.1016/j.eneco.2008.10.005](https://doi.org/10.1016/j.eneco.2008.10.005)
13. <a id="ref13"></a>P. L. Joskow, "Capacity payments in imperfect
    electricity markets: need and design," *Utilities Policy*,
    16(3):159–170, 2008.
    [doi:10.1016/j.jup.2007.10.003](https://doi.org/10.1016/j.jup.2007.10.003)
14. <a id="ref14"></a>R. Romero, A. Monticelli, A. Garcia and S. Haffner,
    "Test systems and mathematical models for transmission network
    expansion planning," *IEE Proc. Gener. Transm. Distrib.*,
    149(1):27–36, 2002.
    [doi:10.1049/ip-gtd:20020026](https://doi.org/10.1049/ip-gtd:20020026)
15. <a id="ref15"></a>K. Arrow et al., "Determining benefits and costs
    for future generations," *Science*, 341(6144):349–350, 2013.
    [doi:10.1126/science.1235665](https://doi.org/10.1126/science.1235665)
16. <a id="ref16"></a>A. Ehrenmann and Y. Smeers, "Generation capacity
    expansion in a risky environment: a stochastic equilibrium
    analysis," EPRG Working Paper 1019, Univ. of Cambridge, 2010.
    [link](https://www.jbs.cam.ac.uk/wp-content/uploads/2023/12/eprg-wp1019.pdf)
17. <a id="ref17"></a>B. Steffen, "Estimating the cost of capital for
    renewable energy projects," *Energy Economics*, 88:104783, 2020.
    [doi:10.1016/j.eneco.2020.104783](https://doi.org/10.1016/j.eneco.2020.104783)
18. <a id="ref18"></a>D. García-Gusano, K. Espegren, A. Lind and
    M. Kirkengen, "The role of the discount rates in energy systems
    optimisation models," *Renew. Sustain. Energy Rev.*, 59:56–72, 2016.
    [link](https://ideas.repec.org/a/eee/rensus/v59y2016icp56-72.html)
19. <a id="ref19"></a>IEA-ETSAP, *TIMES documentation, Part II: equations*.
    [link](https://times.readthedocs.io/en/latest/part-2/06-equations.html)
20. <a id="ref20"></a>NREL, *ReEDS model documentation*.
    [link](https://reeds-model.github.io/ReEDS/model_documentation.html)
21. <a id="ref21"></a>GenX, *Multi-stage input*.
    [link](https://genxproject.github.io/GenX.jl/dev/User_Guide/multi_stage_input)
22. <a id="ref22"></a>MacroEnergy.jl, *Financial assumptions*.
    [link](https://macroenergy.github.io/MacroEnergy.jl/dev/Manual/Financial_assumptions)
23. <a id="ref23"></a>Oxford Economics Australia, *2024 discount rate
    report* (for AEMO ISP 2026).
    [link](https://aemo.com.au/-/media/files/major-publications/isp/2025/oxford-economics-australia-2024-discount-rate-report.pdf)
24. <a id="ref24"></a>ENTSO-E, *4th guideline for cost-benefit analysis
    of grid development projects*, draft for consultation, 2022.
    [link](https://eepublicdownloads.blob.core.windows.net/public-cdn-container/tyndp-documents/CBA/CBA4/221215_CBA4-Guideline_v1.0_for-public-consultation.pdf)
25. <a id="ref25"></a>CAISO, *2024-2025 Transmission Plan, Appendix G*.
    [link](https://www.caiso.com/documents/appendix-g-board-approved-2024-2025-transmission-plan.pdf)
26. <a id="ref26"></a>CNE Chile, Res. Ex. 711/2017, methodology of the
    annual transmission planning process.
    [link](https://www.cne.cl/wp-content/uploads/2016/09/Res-Exenta-711_CNE_12dic2017_Metodolog%C3%ADa-Aplicable-al-Proceso-Anual-de-Planificaci%C3%B3n-de-la-Tx-2.pdf)
27. <a id="ref27"></a>CNE Chile, Res. 462/2024, art. 118 LGSE rate for
    transmission valuation 2024–2027.
    [link](https://www.cne.cl/wp-content/uploads/2024/08/Res-462-Aprueba-Informe-Tasa-Art.-118-LGSE-VxTx-2024-2027.pdf)
28. <a id="ref28"></a>HM Treasury, *Green Book supplementary guidance:
    discounting*, 2026.
    [link](https://assets.publishing.service.gov.uk/media/698488ab2df808759a7bd70f/Green_Book_supplementary_guidance_-_discounting.pdf)
29. <a id="ref29"></a>CEPA for DESNZ, *Cost of capital / hurdle rates for
    electricity generation*.
    [link](https://assets.publishing.service.gov.uk/media/68cd1d818c44a661b4995d9f/cepa-desnz-hurdle-rates-electricity.pdf)
30. <a id="ref30"></a>US OMB, Circular A-4 (2023) announcement.
    [link](https://bidenwhitehouse.archives.gov/omb/briefing-room/2023/11/09/biden-harris-administration-releases-final-guidance-to-improve-regulatory-analysis)
31. <a id="ref31"></a>IEA, "Cost of capital survey shows investments in
    solar PV can be less risky than gas power in emerging and developing
    economies," commentary.
    [link](https://www.iea.org/commentaries/cost-of-capital-survey-shows-investments-in-solar-pv-can-be-less-risky-than-gas-power-in-emerging-and-developing-economies-though-values-remain-high)
32. <a id="ref32"></a>Ministerio de Economía, Chile, DS 62/2006,
    *Reglamento de transferencias de potencia entre empresas
    generadoras*, and amendments (DS 42/2020, DS 70/2023), cited in
    force in the node-price decree DS 4T/2026.
    [link](https://www.diariooficial.interior.gob.cl/publicaciones/2026/04/24/44433/01/2797040.pdf)
33. <a id="ref33"></a>CNE, *Informe Técnico de Costos de la Unidad de
    Punta*, RE 574 (16-Sep-2025), definitive RE 803/2025.
    [link](https://www.cne.cl/wp-content/uploads/2025/09/Rex.-CNE.N°574_ITP-Unidad-de-Punta_16-09-2025-3.pdf)
34. <a id="ref34"></a>A. E. Boardman, D. H. Greenberg, A. R. Vining and
    D. L. Weimer, *Cost-Benefit Analysis: Concepts and Practice*, 5th ed.
    Cambridge University Press, 2018.
    [doi:10.1017/9781108235594](https://doi.org/10.1017/9781108235594)
35. <a id="ref35"></a>M. Elimelech and W. A. Phillip, "The future of
    seawater desalination: energy, technology, and the environment,"
    *Science* 333(6043):712–717, 2011.
    [doi:10.1126/science.1200488](https://doi.org/10.1126/science.1200488)
