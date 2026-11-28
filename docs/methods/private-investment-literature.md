# Private projects in a public-service system: discount rates in the literature

A literature review for one design question.  gtopt plans an
electricity system as a public service and discounts system costs at a
**social** rate.  Most of the projects it selects, however, are
financed and owned by **private** investors at a private cost of
capital.  The review asks how the economics and finance literature
combines the two, in two places:

* the **investment annuity**, where the social rate and the private
  WACC of each project meet;
* the **revenues and benefits**, where private revenue streams carry
  private rates and private risk, while social benefits are discounted
  at the social rate.

It complements §7 of [investment economics](investment-economics.md#7-social-vs-private-discount-rates)
(two rates, two jobs; what gtopt supports) and the
[private investor evaluation](private-investor-evaluation.md) (the
*ex post* cash-flow check at private WACC).

**How the references were checked.**  Each reference was resolved
through Crossref or the publisher, RePEc or government page.  Its
abstract or primary text was read in November 2026.  References marked
†:

* are confirmed by their DOI metadata only, because no abstract is
  available;
* are cited here only for the claim they are standardly known for.

Candidate references that could not be found were dropped.

## Contents

1. [Social vs private discount rates](#1-social-vs-private-discount-rates)
2. [Planning models that mix rates](#2-planning-models-that-mix-rates)
3. [Project finance of generation](#3-project-finance-of-generation)
4. [Regulated vs merchant investment](#4-regulated-vs-merchant-investment)
5. [Equilibrium with risk-averse investors](#5-equilibrium-with-risk-averse-investors)
6. [Mixing rates: annuities and revenues](#6-mixing-rates-annuities-and-revenues)
7. [Implications for gtopt](#7-implications-for-gtopt)
8. [Open questions](#8-open-questions)
9. [References](#references)

## 1. Social vs private discount rates

**Arrow–Lind.**  Arrow and Lind [[L1]](#lit1) argue that a public
investment whose risk is spread over a large population can be
discounted near the riskless rate, because each person's share of the
risk becomes negligible.  The theorem covers **only** risk that is
uncorrelated with aggregate consumption.  Baumstark and Gollier
[[L2]](#lit2)† call risk-free discounting of public projects a misreading
of the theorem.  Gollier [[L3]](#lit3) shows that one discount rate that
ignores the project's consumption beta misallocates capital, at a large
welfare cost.  He names two fallacies:

* the "Arrow–Lind fallacy": every public project discounted risk-free;
* the "WACC fallacy": every project discounted at the firm's rate.

**Time preference vs opportunity cost of capital.**  The Harberger
tradition [[L4]](#lit4)† weights the social rate by the sources of the
funds (displaced investment vs displaced consumption).  Modern guidance
has moved to a consumption (time-preference) rate.  Displaced capital
enters through a shadow price instead:

* Li and Pizer [[L5]](#lit5) convert capital-displacement effects into
  consumption equivalents and then discount at the consumption rate.
* US OMB Circular A-4 (2023) [[L6]](#lit6) uses 2 % and drops the 7 %
  capital rate.
* The UK Green Book uses a time-preference rate.  Its 2026 review
  [[L7]](#lit7) sets it at 3.00 % for years 1–30, then 2.50 % and
  2.25 %, with one social risk premium for all projects.
* The EU cohesion CBA guide [[L8]](#lit8)† uses 5 % for cohesion
  countries and 3 % for the rest.
* Spackman [[L9]](#lit9), [[L10]](#lit10) reviews how government
  practice works.  He rejects the view that financial markets alone fix
  the government rate, and shows that the cost of public funds and the
  income-covariance premium are not settled.

The expert survey of Drupp et al. [[L11]](#lit11) finds that more than
three quarters of experts accept a 2 % risk-free social rate.  Arrow
et al. [[L12]](#lit12) support declining long-term rates.

**The private finance premium (PPP / PFI).**  Privately financed
public projects cost more to finance.  The UK NAO [[L13]](#lit13)
quotes an indicative PFI cost of capital 2 %–3.75 % above gilts.  It
also notes that discounting at the social rate hides that premium.
Whether the premium is a social cost is disputed:

* **Real cost.** Engel, Fischer and Galetovic [[L14]](#lit14),
  [[L15]](#lit15) show that PPPs do not free public funds.  Under an
  optimal risk allocation they look like public provision, so the
  premium must be paid for by efficiency gains.
* **Mostly risk transfer.** Grout [[L16]](#lit16), [[L17]](#lit17)
  argues that higher private rates can be correct, because the private
  rate prices risk the public sector also bears implicitly, and that
  ignoring this biases choices toward public provision.

**Synthesis.**  The common ground is as follows:

* The part of the private premium that prices **systematic** risk is a
  social cost whoever finances the project.  The public sector bears
  that risk too, through taxpayers or consumers.
* The part that prices **diversifiable** risk, regulatory risk or
  financing frictions is a transfer or a deadweight cost of the
  financing structure.  It is not a resource cost of the project.

## 2. Planning models that mix rates

The standard practice of TIMES, ReEDS and GenX (table in
[§7.1](investment-economics.md#71-two-rates-two-jobs)) is to annualise
each technology at its own rate and to discount the objective at a
general rate:

* **TIMES** [[L18]](#lit18) separates the two rates:
  * `NCAP_DRATE` is used "if the cost of borrowing for a process is
    different from the standard discount rate".  It sets the
    investment annuity.
  * `G_DRATE` discounts the objective.
  * `G_RFRIR` keeps the technology risk premia fixed when the general
    rate is varied.
  * The reports show the hurdle-rate excess separately ("INV+").
* **ReEDS** [[L19]](#lit19) uses technology-specific ATB financing in a
  capital-cost multiplier and attributes the WACC spread to diversifiable
  risk.  Its worked example, 7 % against 5 % over 20 years, gives a
  multiplier of 1.25.  Reported system cost uses a separate 2 % social
  rate, "not to be confused with the investment discount rate".
* **GenX** [[L20]](#lit20) uses one planner rate.  A
  technology-specific cost of capital goes into the annualised cost
  input; its WACC columns are "not used in discounting".

**Academic treatment.**  García-Gusano et al. [[L21]](#lit21) show the
social rate drives TIMES results.  They recommend a social rate of
4–5 % or less plus technology hurdle rates for risks and barriers.
Lonergan et al. [[L22]](#lit22) review 58 model studies.  They find the
cost of capital is mostly uniform or ad hoc, with no best practice and
little data.  A uniform cost of capital biases results across countries
and technologies (Egli et al. [[L23]](#lit23), Polzin et al.
[[L24]](#lit24)).  Results are very sensitive to it: Hirth and Steckel
[[L25]](#lit25) find a mix with about 40 % renewables at a 3 % WACC and
almost none at 15 %.  Steinbach and Staniaszek [[L26]](#lit26)† separate
social rates for societal analysis from investor rates for behaviour.

**Is the mix consistent?**  It is consistent if the annuity is read as
the **payment stream that remunerates private capital**: a real cash
cost to the system, valued at the social rate like every other cost.
§5 states the conditions under which the plan then also reproduces a
market equilibrium.  It is inconsistent if a "hurdle rate" stands for
behaviour or barriers rather than financing cost.  Then it is not a
cost, and the objective no longer measures welfare.

## 3. Project finance of generation

**The rate follows the revenue, not only the technology.**  Steffen
[[L27]](#lit27) estimates the cost of capital across 46 countries.  It
rises from solar PV to onshore wind to offshore wind, and is much
higher in developing countries.  Egli, Steffen and Schmidt
[[L28]](#lit28) show the cost of capital moves over time: falling
financing costs explain 24 % of the decline in German wind LCOE (5 %
for PV).  Ondraczek et al. [[L29]](#lit29) find that country WACC can
outweigh the solar resource.

The same technology finances at very different rates depending on how
it is paid:

* Neuhoff and De Vries [[L30]](#lit30): with risk-averse agents,
  investment is efficient only with long-term contracts.  Uncovered
  price risk raises financing costs and tilts the mix toward
  low-capital, high-fuel plant.
* Roques, Newbery and Nuttall [[L31]](#lit31): gas is "low risk" for
  merchant investors only because gas and power prices are correlated.
* Newbery [[L32]](#lit32): the problem is **missing markets** (for
  long-term risk), not only missing money.

**Contracts lower the WACC.**

* May and Neuhoff [[L33]](#lit33) find that the cost of deploying
  renewables differs by about 30 % across policy designs, through
  regulatory risk and hedging.
* Neuhoff, May and Richstein [[L34]](#lit34) find CfDs give the most
  effective risk reduction.
* Kitzing [[L35]](#lit35): feed-in tariffs need less support than
  premiums for the same investor attractiveness.
* Đukan and Kitzing [[L36]](#lit36) put de-risking savings at
  2–3 €/MWh, with debt de-risking about twice as effective as equity
  de-risking.
* Gohdes, Simshauser and Wilson [[L37]](#lit37) find that project
  finance on good terms needs long PPAs with an investment-grade
  counterparty.
* Beiter et al. [[L38]](#lit38) frame CfDs as risk management, not
  subsidy.
* Newbery [[L39]](#lit39) and Fabra [[L40]](#lit40) design CfDs that
  keep dispatch and location signals.

Part of the WACC reduction is a transfer of price risk to consumers.
Part is a real saving, from three sources:

* risk moves to diversified parties who bear it more cheaply;
* policy risk that the state itself creates is removed;
* financing frictions shrink.

**Certainty equivalent vs risk-adjusted rate.**  Robichek and Myers
[[L41]](#lit41)† show that one risk-adjusted rate assumes risk grows at
a constant rate with time.  Their alternative discounts certainty
equivalents at the riskless rate, which keeps risk and time apart.
Fama [[L42]](#lit42) gives the conditions under which a constant
risk-adjusted rate is valid.  Myers and Turnbull [[L43]](#lit43)† show
that project betas depend on life and cash-flow timing.

The consequence is that **each cash-flow stream should be discounted at
a rate matching its own systematic risk.**  Costs and revenues may and
should carry different rates when their betas differ.  Awerbuch
[[L44]](#lit44)†, [[L45]](#lit45) applies this to fuel.  Fuel cost has
a low or negative beta, so it should be discounted near the riskless
rate.  That raises the present value of fossil fuel bills and favours
capital-intensive plant.  Awerbuch and Berger's portfolio analysis
[[L46]](#lit46)† extends the argument to the whole mix.

**LCOE vs market value.**  Joskow [[L47]](#lit47) shows that LCOE
overvalues intermittent plant, because it ignores when the plant
produces.  Hirth [[L48]](#lit48) measures the value factor of wind: it
falls from 1.1 to 0.5–0.8 of the average price as wind's share rises
from 0 to 30 %.  Gross, Blyth and Heptonstall [[L49]](#lit49) add that
LCOE hides risk and revenue structure.  The relevant private test is
captured price against LCOE at the WACC that fits the contract.  This
is what `gtopt_private_eval` reports.

**Real options.**  Dixit and Pindyck [[L50]](#lit50) treat an
irreversible investment under uncertainty as an option.  The investment
trigger lies above the NPV = 0 cost, so NPV > 0 at the WACC is
necessary but not sufficient for a merchant investor to build now.

## 4. Regulated vs merchant investment

Regulated assets earn an administratively set rate.  If that rate is
above the cost of capital, the firm over-invests (Averch and Johnson
[[L51]](#lit51)†).  Two lines of work aim to lower the regulatory risk
premium:

* incentive regulation (Joskow [[L52]](#lit52), [[L53]](#lit53));
* the commitment of a regulated asset base, a split cost of capital and
  debt indexation (Helm [[L54]](#lit54)).

The UK regulators' joint study [[L55]](#lit55) puts the cost of equity
at about 5.5 % geometric (6.5–7.5 % arithmetic).

Chile:

* **Transmission** (VATT).  The rate is after tax, computed as
  risk-free + β · premium, and bounded by law between 7 % and 10 %.  For
  2024–2027 the computed value was 5.93 %, so the 7.00 % floor applies
  (CNE Res. 462/2024 [[L56]](#lit56)).
* **Distribution** (VAD) is set by a model-company yardstick (Rudnick
  and Donoso [[L57]](#lit57)) at a 10 % real pre-tax rate.  That rate is
  confirmed by regulator documents only; no journal source was found.

For the planner, the regulated rate is what consumers pay.  The excess
over the efficient cost of capital (here at least 7.00 − 5.93 = 1.07
points) is a **transfer** to the owner, not a resource cost.  The
cost-based Chilean market design has its own investment distortions
(Muñoz et al. [[L58]](#lit58)).

## 5. Equilibrium with risk-averse investors

When does a planner's optimum also describe a market of private
investors?

| Investors | Risk markets | Planner optimum = market equilibrium? | Source |
|-----------|--------------|---------------------------------------|--------|
| risk-neutral | any | yes: the equilibrium is the KKT system of the planner's problem | Ehrenmann and Smeers [[L59]](#lit59); Philpott et al. [[L60]](#lit60) |
| risk-averse (coherent measures) | complete | yes, with a "system risk agent": the planner minimises a risk measure of total cost | Ralph and Smeers [[L61]](#lit61); Philpott et al. [[L60]](#lit60) |
| risk-averse | incomplete | no: no equivalent optimisation; equilibria can be multiple | Gérard, Leclère and Philpott [[L62]](#lit62); Abada et al. [[L63]](#lit63) |

Related findings:

* **Discount rates from risk.**  Ehrenmann and Smeers [[L64]](#lit64)
  derive plant-specific discount rates from the stochastic discount
  factors of the equilibrium.  Those rates price only systematic risk;
  unpriced idiosyncratic risk needs risk functions per agent.
* **Risk shifts the mix.**  Risk aversion moves the mix toward less
  capital-intensive plant [[L59]](#lit59).  It changes the ranking of
  carbon policies (Fan, Hobbs and Norman [[L65]](#lit65)).  It makes
  capacity payments favour low-fixed-cost plant (Mays, Morton and
  O'Neill [[L66]](#lit66)).  With incomplete risk trading it leads to
  underinvestment in resilience (Mays et al. [[L67]](#lit67)).
* **Contracts restore the planner result.**  De Maere d'Aertrycke,
  Ehrenmann and Smeers [[L68]](#lit68) show that CfDs, reliability
  options and forward capacity markets move the outcome back toward
  the complete-market case.  They express each design as the **hurdle
  rate** it implies.  Willems and Morbee [[L69]](#lit69) find most of
  the welfare gain comes from the first one to three hedging
  instruments.

**Reading.**  A planner LP that annualises each candidate at its private
WACC and discounts at a social rate is a **reduced form**:

* It reproduces the equilibrium when the WACC spreads are the risk
  premia a complete-market economy would charge.
* Under incomplete markets it is only a heuristic.
* The "right" WACC is not a property of the technology.  It depends on
  the market design: contract, merchant or regulated.

## 6. Mixing rates: annuities and revenues

**In the annuity.**  Use the private WACC of each project in its CRF,
and the social rate for the present value of the plan.  This is
justified when the WACC is the actual cost of remunerating the
project's capital (§2, §5).  The result then embeds the financing
premium as a cost.  That premium has two parts:

* **systematic risk**: a real social cost, which would be borne under
  public finance too [[L2]](#lit2), [[L3]](#lit3);
* **diversifiable or regulatory risk and frictions**: a transfer or
  deadweight cost of the financing structure, which policy can reduce
  through contracts [[L33]](#lit33)–[[L38]](#lit38) or a RAB-type
  commitment [[L54]](#lit54).

The planner should therefore report the **financing wedge** separately,
as TIMES does with INV+.  Over 30 years it is substantial, measured
against $\mathrm{CRF}(6\%, 30) = 0.0727$:

| Rate | $\mathrm{CRF}(\rho, 30)$ | Annuity / annuity at 6 % |
|-----:|---------:|-----:|
| 7 % | 0.0806 | 1.11 |
| 9 % | 0.0973 | 1.34 |
| 11 % | 0.1150 | 1.58 |

**In the revenues.**  The LP has no separate discount rate for revenues.
Its duals are prices, and a project's margin is discounted with the
system's social stage factors.  At the optimum the zero-profit identity
(§1.2 of [investment economics](investment-economics.md#12-zero-profit-complementary-slackness))
equates that margin with the annuity at the project's WACC.  The
*ex post* private evaluation discounts the same margin at the private
WACC.  The two views are consistent: at private WACC = planning WACC
and with the same costs, the NPV is 0.

The finance literature adds two refinements:

1. **Different streams have different risk** [[L41]](#lit41)–[[L45]](#lit45).
   * A contracted revenue (PPA, CfD, regulated tariff) has a beta near
     that of the counterparty's credit, so its rate is close to the
     cost of debt.
   * A merchant energy margin has a higher beta.
   * Fuel cost has a low beta.

   One project-wide WACC applied to all streams is a shortcut: it
   understates fuel-cost risk and overstates contracted-revenue risk.
2. **Rate or certainty equivalent, not both.**  Discounting stream $R_t$
   at $r_R$ is the same as discounting the certainty equivalent
   $\mathrm{CE}_t = R_t \left(\frac{1 + r_f}{1 + r_R}\right)^t$ at the
   riskless $r_f$ [[L41]](#lit41).
   * With $r_f = 3\%$ and $r_R = 9\%$, the implied haircut is
     $0.57$ at year 10 and $0.32$ at year 20.  A constant rate implies
     a haircut that grows geometrically with time.
   * Applying a merchant WACC **and** a revenue haircut, or a scenario
     risk measure on top of a risk-premium WACC, counts risk twice.

**Social benefits.**  Consumer surplus, avoided unserved energy and
avoided emissions stay at the social rate.  A risk adjustment applies
only if they carry systematic risk, through one social risk premium
(Green Book 2026 [[L7]](#lit7)).  They are not part of the investor's
cash flows, so the private WACC does not apply to them.

## 7. Implications for gtopt

The current design is consistent with the literature.  The
recommendations below sharpen how results are reported, and add one
evaluation option in Python.  None needs C++.  Items 3 and 5 are now
implemented.

| # | Item | Status | Recommendation |
|---|------|--------|----------------|
| 1 | Per-candidate `wacc` in the annuity (`overnight_capcost` · CRF) | implemented ([§7.3](investment-economics.md#73-gtopt-support)) | Keep.  Same as TIMES `NCAP_DRATE` and ReEDS.  Document the `wacc` as the rate of the **remuneration scheme** the candidate will have (contracted, merchant or regulated), not as a property of the technology [[L68]](#lit68), [[L27]](#lit27). |
| 2 | Social `annual_discount_rate` for stage discounting | implemented | Keep.  Use the jurisdiction's social rate (Chile: MDSF; UK 3 %; US 2 %). |
| 3 | Financing wedge | **implemented**: `gtopt_cost_recovery` column `fin. wedge`, JSON/CSV `financing_wedge` and `social_capex` ([investment economics §7.3](investment-economics.md#73-gtopt-support)) | Per built project, the annuity at its `wacc` minus the annuity at the social rate, like TIMES INV+.  Open: a split into systematic and transfer parts given a social risk premium. |
| 4 | *Ex post* evaluation at private WACC | implemented (`gtopt_private_eval`) | Keep.  NPV = 0 at planning WACC is the consistency check. |
| 5 | Stream-specific rates | **implemented**: `finance.stream_rates` in `gtopt_private_eval`, default off ([private investor evaluation §2.3](private-investor-evaluation.md#23-stream-specific-discount-rates)) | Rate or certainty-equivalent method, stream NPV plus equivalent single rate, double-counting warnings. |
| 6 | Regulated assets (VATT) | regulated market model exists | Report the regulated-rate excess over an efficient cost of capital as a transfer (Chile: 7.00 % floor vs 5.93 % computed). |
| 7 | Risk-averse planner (CVaR over scenarios) | implemented: the **social CVaR** (`model_options.risk_measure`, [investment economics §7.4](investment-economics.md#74-risk-averse-planner-cvar-instead-of-a-wacc-premium)) | Plan with the social CVaR, verify with the private CVaR of each project (spec in §7.4 for `gtopt_private_eval`). |
| 8 | Per-investor risk aversion (equilibrium) | out of scope | Needs an MCP equilibrium solver and may have several solutions [[L62]](#lit62), [[L63]](#lit63).  Not a gtopt LP. |

**Stream-specific rates** (item 5), as implemented:

```json
"finance": {
  "wacc": 0.07,
  "stream_rates": {
    "method": "rate",
    "rates": {"contract": 0.05, "energy": 0.09, "capacity": 0.07,
              "fuel": 0.03},
    "map": {"energy": "contract"}
  }
}
```

The rules:

* The NPV is the sum over streams, each discounted at its own rate
  (unlisted streams at `wacc`).  Capex is at year 0.
* `"method": "certainty_equivalent"` uses `risk_free` and per-stream
  `haircuts` instead.
* The report gives the equivalent single rate instead of the IRR.
* Warnings cover a merchant-level `wacc` on unlisted streams, a
  contract rate above the merchant rate, rates mixed with haircuts, and
  any risk premium when `finance.risk_adjusted_prices` declares that
  the prices already carry risk aversion.
* Default off, so the behaviour of existing configs is unchanged.

**Sketch: risk-averse planner** (item 7).  Use the Rockafellar–Uryasev
[[L70]](#lit70) form over the scenario costs $C_s$ with probabilities
$p_s$:

$$\min\ (1 - \lambda) \sum_s p_s C_s + \lambda \left( \eta + \frac{1}{1 - \alpha} \sum_s p_s u_s \right), \quad u_s \ge C_s - \eta,\ u_s \ge 0$$

It is linear and adds one variable per scenario plus one $\eta$.  The
literature reads it as a complete-market risk-averse equilibrium
[[L60]](#lit60), [[L61]](#lit61).  Its duals are risk-adjusted
probabilities, so the zero-profit identity holds under those
probabilities, not under $p_s$.  Use it **instead of** risk premia in
the WACC, never in addition.  It is now implemented for the monolithic
method as the planner's **social CVaR** (formulation §4.5; SDDP and
cascade are rejected, the nested-CVaR design is in the same section).

## 8. Open questions

1. **Which part of the WACC spread is systematic?**  Only that part
   belongs in a social appraisal.  The literature gives no operational
   split per technology.  A practical proxy is the contracted
   (cost-of-debt-like) WACC as the social-plus-systematic rate, with the
   merchant premium treated as the transfer.  This needs data, e.g.
   the CEPA hurdle rates and Steffen-type surveys.
2. **Endogenous WACC.**  The WACC depends on the remuneration scheme,
   which the planner may choose: contract share, CfD or capacity
   market.  A fixed per-candidate `wacc` cannot capture that.  An
   option is to plan with alternative candidates per scheme (same
   plant, different `wacc` and remuneration) and let the LP choose.
   That assumes the contracting cost is otherwise zero.
3. **Declining social rates and re-optimisation.**  Rolling plans with
   declining rates are time-inconsistent [[L12]](#lit12).  It is open
   how to keep a rolling cascade or SDDP study coherent.
4. **Chile.**  The social rate (MDSF), the transmission floor (7 %
   after tax) and the merchant WACC of generators all differ.  No
   journal study measures the welfare cost of that wedge for the SEN.
   Muñoz et al. [[L58]](#lit58) cover the cost-based design, not the
   rates.
5. **Real options.**  An NPV > 0 at the WACC does not guarantee timely
   merchant entry [[L50]](#lit50).  A waiting-value check in the private
   evaluation (an investment trigger) is not implemented.

## References

Status: † = metadata (DOI) confirmed but abstract not read; all others
have the abstract or primary text read.

1. <a id="lit1"></a>K. J. Arrow and R. C. Lind, "Uncertainty and the
   evaluation of public investment decisions," *American Economic
   Review*, 60(3):364–378, 1970.
   [link](https://ideas.repec.org/a/aea/aecrev/v60y1970i3p364-78.html)
2. <a id="lit2"></a>L. Baumstark and C. Gollier, "The relevance and the
   limits of the Arrow-Lind theorem," *J. Natural Resources Policy
   Research*, 6(1):45–49, 2014. †
   [doi:10.1080/19390459.2013.861160](https://doi.org/10.1080/19390459.2013.861160)
3. <a id="lit3"></a>C. Gollier, "The welfare cost of ignoring the
   beta," *J. Political Economy Microeconomics*, 4(1):147–172, 2026.
   [doi:10.1086/733779](https://doi.org/10.1086/733779)
4. <a id="lit4"></a>L. A. Sjaastad and D. L. Wisecarver, "The social
   cost of public finance," *J. Political Economy*, 85(3):513–547,
   1977. †
   [doi:10.1086/260582](https://doi.org/10.1086/260582)
5. <a id="lit5"></a>Q. Li and W. A. Pizer, "Discounting for public
   benefit-cost analysis," RFF Issue Brief 21-05, 2021.
   [link](https://ideas.repec.org/p/rff/ibrief/ib-21-05.html)
6. <a id="lit6"></a>US OMB, *Circular A-4*, 9 Nov 2023.
   [link](https://www.whitehouse.gov/wp-content/uploads/2023/11/CircularA-4.pdf)
7. <a id="lit7"></a>HM Treasury, *Green Book discount rate review 2026*,
   response, Oct 2026.
   [link](https://www.gov.uk/government/publications/green-book-discount-rate-review-2026)
8. <a id="lit8"></a>European Commission, DG REGIO, *Guide to
   cost-benefit analysis of investment projects*, 2014. †
   [link](https://ec.europa.eu/regional_policy/en/information/publications/guides/2014/guide-to-cost-benefit-analysis-of-investment-projects-for-cohesion-policy-2014-2020)
9. <a id="lit9"></a>M. Spackman, "Time discounting and of the cost of
   capital in government," *Fiscal Studies*, 25(4):467–518, 2004.
   [doi:10.1111/j.1475-5890.2004.tb00547.x](https://doi.org/10.1111/j.1475-5890.2004.tb00547.x)
10. <a id="lit10"></a>M. Spackman, "Social discounting and the cost of
    public funds: a practitioner's perspective," *J. Benefit-Cost
    Analysis*, 11(2):244–271, 2020.
    [doi:10.1017/bca.2020.5](https://doi.org/10.1017/bca.2020.5)
11. <a id="lit11"></a>M. A. Drupp, M. C. Freeman, B. Groom and F. Nesje,
    "Discounting disentangled," *AEJ: Economic Policy*, 10(4):109–134,
    2018.
    [doi:10.1257/pol.20160240](https://doi.org/10.1257/pol.20160240)
12. <a id="lit12"></a>K. Arrow et al., "Determining benefits and costs
    for future generations," *Science*, 341(6144):349–350, 2013.
    [doi:10.1126/science.1235665](https://doi.org/10.1126/science.1235665)
13. <a id="lit13"></a>UK National Audit Office, *PFI and PF2*, HC 718,
    Jan 2018.
    [link](https://www.nao.org.uk/reports/pfi-and-pf2/)
14. <a id="lit14"></a>E. Engel, R. Fischer and A. Galetovic, "The basic
    public finance of public–private partnerships," *J. European
    Economic Association*, 11(1):83–111, 2013.
    [doi:10.1111/j.1542-4774.2012.01105.x](https://doi.org/10.1111/j.1542-4774.2012.01105.x)
15. <a id="lit15"></a>E. Engel, R. Fischer and A. Galetovic, *The
    Economics of Public-Private Partnerships*, Cambridge UP, 2014.
    [doi:10.1017/cbo9781139565615](https://doi.org/10.1017/cbo9781139565615)
16. <a id="lit16"></a>P. A. Grout, "The economics of the private finance
    initiative," *Oxford Rev. Economic Policy*, 13(4):53–66, 1997.
    [doi:10.1093/oxrep/13.4.53](https://doi.org/10.1093/oxrep/13.4.53)
17. <a id="lit17"></a>P. A. Grout, "Public and private sector discount
    rates in public–private partnerships," *Economic Journal*,
    113(486):C62–C68, 2003.
    [doi:10.1111/1468-0297.00109](https://doi.org/10.1111/1468-0297.00109)
18. <a id="lit18"></a>R. Loulou, G. Goldstein, A. Kanudia, A. Lehtilä
    and U. Remme, *Documentation for the TIMES model*, IEA-ETSAP, 2016,
    Part II parameters.
    [link](https://times.readthedocs.io/en/latest/part-2/03-parameters.html)
19. <a id="lit19"></a>NREL, *ReEDS model documentation*, capital
    financing and financial multipliers.
    [link](https://reeds-model.github.io/ReEDS/model_documentation.html)
20. <a id="lit20"></a>GenX, *Multi-stage input*.
    [link](https://genxproject.github.io/GenX.jl/dev/User_Guide/multi_stage_input)
21. <a id="lit21"></a>D. García-Gusano, K. Espegren, A. Lind and
    M. Kirkengen, "The role of the discount rates in energy systems
    optimisation models," *Renew. Sustain. Energy Rev.*, 59:56–72, 2016.
    [doi:10.1016/j.rser.2015.12.359](https://doi.org/10.1016/j.rser.2015.12.359)
22. <a id="lit22"></a>K. E. Lonergan, F. Egli, S. Osorio, G. Sansavini,
    M. Pahle, T. S. Schmidt and B. Steffen, "Improving the
    representation of cost of capital in energy system models," *Joule*,
    7(3):469–483, 2023.
    [doi:10.1016/j.joule.2023.02.004](https://doi.org/10.1016/j.joule.2023.02.004)
23. <a id="lit23"></a>F. Egli, B. Steffen and T. S. Schmidt, "Bias in
    energy system models with uniform cost of capital assumption,"
    *Nature Communications*, 10:4588, 2019.
    [doi:10.1038/s41467-019-12468-z](https://doi.org/10.1038/s41467-019-12468-z)
24. <a id="lit24"></a>F. Polzin et al., "The effect of differentiating
    costs of capital by country and technology on the European energy
    transition," *Climatic Change*, 167:26, 2021.
    [doi:10.1007/s10584-021-03163-4](https://doi.org/10.1007/s10584-021-03163-4)
25. <a id="lit25"></a>L. Hirth and J. C. Steckel, "The role of capital
    costs in decarbonizing the electricity sector," *Environ. Res.
    Lett.*, 11(11):114010, 2016.
    [doi:10.1088/1748-9326/11/11/114010](https://doi.org/10.1088/1748-9326/11/11/114010)
26. <a id="lit26"></a>J. Steinbach and D. Staniaszek, *Discount rates in
    energy system analysis*, BPIE / Fraunhofer ISI, 2015. †
    [link](https://www.bpie.eu/publication/discount-rates-in-energy-system-analysis/)
27. <a id="lit27"></a>B. Steffen, "Estimating the cost of capital for
    renewable energy projects," *Energy Economics*, 88:104783, 2020.
    [doi:10.1016/j.eneco.2020.104783](https://doi.org/10.1016/j.eneco.2020.104783)
28. <a id="lit28"></a>F. Egli, B. Steffen and T. S. Schmidt, "A dynamic
    analysis of financing conditions for renewable energy
    technologies," *Nature Energy*, 3(12):1084–1092, 2018.
    [doi:10.1038/s41560-018-0277-y](https://doi.org/10.1038/s41560-018-0277-y)
29. <a id="lit29"></a>J. Ondraczek, N. Komendantova and A. Patt, "WACC
    the dog: the effect of financing costs on the levelized cost of
    solar PV power," *Renewable Energy*, 75:888–898, 2015.
    [doi:10.1016/j.renene.2014.10.053](https://doi.org/10.1016/j.renene.2014.10.053)
30. <a id="lit30"></a>K. Neuhoff and L. De Vries, "Insufficient
    incentives for investment in electricity generations," *Utilities
    Policy*, 12(4):253–267, 2004.
    [doi:10.1016/j.jup.2004.06.002](https://doi.org/10.1016/j.jup.2004.06.002)
31. <a id="lit31"></a>F. A. Roques, D. M. Newbery and W. J. Nuttall,
    "Fuel mix diversification incentives in liberalized electricity
    markets: a mean–variance portfolio theory approach," *Energy
    Economics*, 30(4):1831–1849, 2008.
    [doi:10.1016/j.eneco.2007.11.008](https://doi.org/10.1016/j.eneco.2007.11.008)
32. <a id="lit32"></a>D. Newbery, "Missing money and missing markets:
    reliability, capacity auctions and interconnectors," *Energy
    Policy*, 94:401–410, 2016.
    [doi:10.1016/j.enpol.2015.10.028](https://doi.org/10.1016/j.enpol.2015.10.028)
33. <a id="lit33"></a>N. May and K. Neuhoff, "Financing power: impacts
    of energy policies in changing regulatory environments," *Energy
    Journal*, 42(4):131–152, 2021.
    [doi:10.5547/01956574.42.4.nmay](https://doi.org/10.5547/01956574.42.4.nmay)
34. <a id="lit34"></a>K. Neuhoff, N. May and J. C. Richstein, "Financing
    renewables in the age of falling technology costs," *Resource and
    Energy Economics*, 70:101330, 2022.
    [doi:10.1016/j.reseneeco.2022.101330](https://doi.org/10.1016/j.reseneeco.2022.101330)
35. <a id="lit35"></a>L. Kitzing, "Risk implications of renewable
    support instruments: comparative analysis of feed-in tariffs and
    feed-in premiums using a mean–variance approach," *Energy*,
    64:495–505, 2014.
    [doi:10.1016/j.energy.2013.10.008](https://doi.org/10.1016/j.energy.2013.10.008)
36. <a id="lit36"></a>M. Đukan and L. Kitzing, "A bigger bang for the
    buck: the impact of risk reduction on renewable energy support
    payments in Europe," *Energy Policy*, 173:113395, 2023.
    [doi:10.1016/j.enpol.2022.113395](https://doi.org/10.1016/j.enpol.2022.113395)
37. <a id="lit37"></a>N. Gohdes, P. Simshauser and C. Wilson, "Renewable
    entry costs, project finance and the role of revenue quality in
    Australia's National Electricity Market," *Energy Economics*,
    114:106312, 2022.
    [doi:10.1016/j.eneco.2022.106312](https://doi.org/10.1016/j.eneco.2022.106312)
38. <a id="lit38"></a>P. Beiter, J. Guillet, M. Jansen, E. Wilson and
    L. Kitzing, "The enduring role of contracts for difference in
    risk management and market creation for renewables," *Nature
    Energy*, 9(1):20–26, 2024.
    [doi:10.1038/s41560-023-01401-w](https://doi.org/10.1038/s41560-023-01401-w)
39. <a id="lit39"></a>D. Newbery, "Efficient renewable electricity
    support: designing an incentive-compatible support scheme," *Energy
    Journal*, 44(3):1–22, 2023.
    [doi:10.5547/01956574.44.3.dnew](https://doi.org/10.5547/01956574.44.3.dnew)
40. <a id="lit40"></a>N. Fabra, "Reforming European electricity
    markets: lessons from the energy crisis," *Energy Economics*,
    126:106963, 2023.
    [doi:10.1016/j.eneco.2023.106963](https://doi.org/10.1016/j.eneco.2023.106963)
41. <a id="lit41"></a>A. A. Robichek and S. C. Myers, "Conceptual
    problems in the use of risk-adjusted discount rates," *J. Finance*,
    21(4):727–730, 1966. †
    [doi:10.1111/j.1540-6261.1966.tb00277.x](https://doi.org/10.1111/j.1540-6261.1966.tb00277.x)
42. <a id="lit42"></a>E. F. Fama, "Risk-adjusted discount rates and
    capital budgeting under uncertainty," *J. Financial Economics*,
    5(1):3–24, 1977.
    [doi:10.1016/0304-405X(77)90027-7](https://doi.org/10.1016/0304-405X(77)90027-7)
43. <a id="lit43"></a>S. C. Myers and S. M. Turnbull, "Capital budgeting
    and the capital asset pricing model: good news and bad news,"
    *J. Finance*, 32(2):321–333, 1977. †
    [doi:10.1111/j.1540-6261.1977.tb03272.x](https://doi.org/10.1111/j.1540-6261.1977.tb03272.x)
44. <a id="lit44"></a>S. Awerbuch, "The surprising role of risk in
    utility integrated resource planning," *Electricity Journal*,
    6(3):20–33, 1993. †
    [doi:10.1016/1040-6190(93)90048-P](https://doi.org/10.1016/1040-6190(93)90048-P)
45. <a id="lit45"></a>S. Awerbuch, "Portfolio-based electricity
    generation planning: policy implications for renewables and energy
    security," *Mitigation and Adaptation Strategies for Global Change*,
    11(3):693–710, 2006.
    [doi:10.1007/s11027-006-4754-4](https://doi.org/10.1007/s11027-006-4754-4)
46. <a id="lit46"></a>S. Awerbuch and M. Berger, *Energy security and
    diversity in the EU: a mean-variance portfolio approach*, IEA
    Report EET/2003/03, Paris, 2003. † (no DOI; title as cited in
    [[L45]](#lit45))
47. <a id="lit47"></a>P. L. Joskow, "Comparing the costs of intermittent
    and dispatchable electricity generating technologies," *American
    Economic Review*, 101(3):238–241, 2011.
    [doi:10.1257/aer.101.3.238](https://doi.org/10.1257/aer.101.3.238)
48. <a id="lit48"></a>L. Hirth, "The market value of variable
    renewables," *Energy Economics*, 38:218–236, 2013.
    [doi:10.1016/j.eneco.2013.02.004](https://doi.org/10.1016/j.eneco.2013.02.004)
49. <a id="lit49"></a>R. Gross, W. Blyth and P. Heptonstall, "Risks,
    revenues and investment in electricity generation: why policy needs
    to look beyond costs," *Energy Economics*, 32(4):796–804, 2010.
    [doi:10.1016/j.eneco.2009.09.017](https://doi.org/10.1016/j.eneco.2009.09.017)
50. <a id="lit50"></a>A. K. Dixit and R. S. Pindyck, *Investment under
    Uncertainty*, Princeton UP, 1994.
    [doi:10.1515/9781400830176](https://doi.org/10.1515/9781400830176)
51. <a id="lit51"></a>H. Averch and L. L. Johnson, "Behavior of the firm
    under regulatory constraint," *American Economic Review*,
    52(5):1052–1069, 1962. †
    [link](https://www.jstor.org/stable/1812181)
52. <a id="lit52"></a>P. L. Joskow, "Incentive regulation and its
    application to electricity networks," *Review of Network
    Economics*, 7(4), 2008.
    [doi:10.2202/1446-9022.1161](https://doi.org/10.2202/1446-9022.1161)
53. <a id="lit53"></a>P. L. Joskow, "Incentive regulation in theory and
    practice: electricity distribution and transmission networks," in
    N. Rose (ed.), *Economic Regulation and Its Reform*, U. Chicago
    Press / NBER, 2014, pp. 291–344.
    [link](https://www.nber.org/chapters/c12566)
54. <a id="lit54"></a>D. Helm, "Infrastructure investment, the cost of
    capital, and regulation: an assessment," *Oxford Rev. Economic
    Policy*, 25(3):307–326, 2009.
    [doi:10.1093/oxrep/grp027](https://doi.org/10.1093/oxrep/grp027)
55. <a id="lit55"></a>S. Wright, R. Mason and D. Miles, *A study into
    certain aspects of the cost of capital for regulated utilities in
    the U.K.*, Smithers & Co for the UK regulators and the OFT, 2003.
    [link](https://www.ofgem.gov.uk/ofgem-publications/50794/2198-jointregscoc.pdf)
56. <a id="lit56"></a>CNE Chile, Res. Ex. 462/2024, art. 118 LGSE rate
    for transmission valuation 2024–2027.
    [link](https://www.cne.cl/wp-content/uploads/2024/08/Res-462-Aprueba-Informe-Tasa-Art.-118-LGSE-VxTx-2024-2027.pdf)
57. <a id="lit57"></a>H. Rudnick and J. A. Donoso, "Integration of price
    cap and yardstick competition schemes in electrical distribution
    regulation," *IEEE Trans. Power Systems*, 15(4):1428–1433, 2000.
    [doi:10.1109/59.898123](https://doi.org/10.1109/59.898123)
58. <a id="lit58"></a>F. D. Muñoz, S. Wogrin, S. S. Oren and
    B. F. Hobbs, "Economic inefficiencies of cost-based electricity
    market designs," *Energy Journal*, 39(3):51–68, 2018.
    [doi:10.5547/01956574.39.3.fmun](https://doi.org/10.5547/01956574.39.3.fmun)
59. <a id="lit59"></a>A. Ehrenmann and Y. Smeers, "Generation capacity
    expansion in a risky environment: a stochastic equilibrium
    analysis," *Operations Research*, 59(6):1332–1346, 2011.
    [doi:10.1287/opre.1110.0992](https://doi.org/10.1287/opre.1110.0992)
60. <a id="lit60"></a>A. Philpott, M. Ferris and R. Wets, "Equilibrium,
    uncertainty and risk in hydro-thermal electricity systems,"
    *Mathematical Programming*, 157(2):483–513, 2016.
    [doi:10.1007/s10107-015-0972-4](https://doi.org/10.1007/s10107-015-0972-4)
61. <a id="lit61"></a>D. Ralph and Y. Smeers, "Risk trading and
    endogenous probabilities in investment equilibria," *SIAM J.
    Optimization*, 25(4):2589–2611, 2015.
    [doi:10.1137/110851778](https://doi.org/10.1137/110851778)
62. <a id="lit62"></a>H. Gérard, V. Leclère and A. Philpott, "On risk
    averse competitive equilibrium," *Operations Research Letters*,
    46(1):19–26, 2018.
    [doi:10.1016/j.orl.2017.10.011](https://doi.org/10.1016/j.orl.2017.10.011)
63. <a id="lit63"></a>I. Abada, G. de Maere d'Aertrycke and Y. Smeers,
    "On the multiplicity of solutions in generation capacity investment
    models with incomplete markets: a risk-averse stochastic equilibrium
    approach," *Mathematical Programming*, 165(1):5–69, 2017.
    [doi:10.1007/s10107-017-1185-9](https://doi.org/10.1007/s10107-017-1185-9)
64. <a id="lit64"></a>A. Ehrenmann and Y. Smeers, "Stochastic
    equilibrium models for generation capacity expansion," in
    *Stochastic Optimization Methods in Finance and Energy*, Springer,
    2011, pp. 273–310.
    [doi:10.1007/978-1-4419-9586-5_13](https://doi.org/10.1007/978-1-4419-9586-5_13)
65. <a id="lit65"></a>L. Fan, B. F. Hobbs and C. S. Norman, "Risk
    aversion and CO2 regulatory uncertainty in power generation
    investment: policy and modeling implications," *J. Environmental
    Economics and Management*, 60(3):193–208, 2010.
    [doi:10.1016/j.jeem.2010.08.001](https://doi.org/10.1016/j.jeem.2010.08.001)
66. <a id="lit66"></a>J. Mays, D. P. Morton and R. P. O'Neill,
    "Asymmetric risk and fuel neutrality in electricity capacity
    markets," *Nature Energy*, 4(11):948–956, 2019.
    [doi:10.1038/s41560-019-0476-1](https://doi.org/10.1038/s41560-019-0476-1)
67. <a id="lit67"></a>J. Mays, M. T. Craig, L. Kiesling, J. C. Macey,
    B. Shaffer and H. Shu, "Private risk and social resilience in
    liberalized electricity markets," *Joule*, 6(2):369–380, 2022.
    [doi:10.1016/j.joule.2022.01.004](https://doi.org/10.1016/j.joule.2022.01.004)
68. <a id="lit68"></a>G. de Maere d'Aertrycke, A. Ehrenmann and
    Y. Smeers, "Investment with incomplete markets for risk: the need
    for long-term contracts," *Energy Policy*, 105:571–583, 2017.
    [doi:10.1016/j.enpol.2017.01.029](https://doi.org/10.1016/j.enpol.2017.01.029)
69. <a id="lit69"></a>B. Willems and J. Morbee, "Market completeness:
    how options affect hedging and investments in the electricity
    sector," *Energy Economics*, 32(4):786–795, 2010.
    [doi:10.1016/j.eneco.2009.10.019](https://doi.org/10.1016/j.eneco.2009.10.019)
70. <a id="lit70"></a>R. T. Rockafellar and S. Uryasev, "Optimization of
    conditional value-at-risk," *J. Risk*, 2(3):21–41, 2000.
    [doi:10.21314/JOR.2000.038](https://doi.org/10.21314/JOR.2000.038)
