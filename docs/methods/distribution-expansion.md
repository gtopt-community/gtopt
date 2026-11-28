# Distribution expansion planning with gtopt

This page collects what real distribution expansion planning (DEP)
studies include, how gtopt represents each element with the LinDistFlow
network model, and the Chilean *Valor Agregado de Distribución* (VAD)
exercise built on the IEEE test feeders.

- [Distribution expansion planning: what studies include](#distribution-expansion-planning-what-studies-include)
- [VAD: the Chilean distribution tariff study](#vad-the-chilean-distribution-tariff-study)
- [The VAD área-típica demo](#the-vad-área-típica-demo)

Model: [§5.6 bis LinDistFlow](../formulation/mathematical-formulation.md#56-bis-lindistflow-distribution-network).
Converters: [opendss2gtopt / gtopt2opendss](../scripts/opendss2gtopt.md).
References are numbered separately in each of the two research
sections.

## Distribution expansion planning: what studies include

This section surveys what real distribution network expansion planning
(DEP) studies contain. Academic models, one regulator model-company study
(Chile), one incentive regulator (Great Britain), US laboratory and
industry methods, and one US state planning framework (California) are
compared. The aim is to decide which elements a LinDistFlow-based
LP/MIP planning tool must model with investment capability. Statements
marked "(not verified)" could not be checked against a primary source
during this survey.

### Academic DEP formulations

Georgilakis and Hatziargyriou [1] review power distribution planning
(PDP). They define its goal as meeting demand growth "in the most
economical, reliable, and safe manner possible", and they classify
models and methods for the move from passive networks to networks with
distributed energy resources. Note: the review appeared in *Electric
Power Systems Research* 121 (2015), not in IET GTD. Only the abstract and
bibliographic data were checked; the full text was not accessed.

The Castilla-La Mancha group (Muñoz-Delgado, Contreras, Arroyo) defines
the reference MILP formulation:

- [2] (TPWRS 2015) plans the distribution network and DG jointly over
  several stages. Network options are alternative conductors for
  replacing or adding feeders and transformer options at substations. DG
  options are conventional and wind units. The objective is the net
  present value of costs, with piecewise-linear losses. Radiality
  constraints are adapted so that DG units are not isolated.
- [3] (TPWRS 2016) adds uncertainty and reliability.
- [4] (TPWRS 2018) writes reliability assessment explicitly in terms of
  the investment variables. Abstracts of [3]-[4] were blocked by IEEE
  Xplore, so their details are summarised from the open-access follow-up
  paper [5].
- [5] (Jooshaki et al., TPWRS 2022, CC BY, co-authored by the same
  group) is a fully documented example of a current DEP MILP. It
  includes:
  - investment options for each feeder section (construction or
    replacement, with capacity, impedance and cost per option);
  - transformer options for each substation, and the expansion or new
    construction of substations;
  - feeder sections that can be switched in normal operation, plus
    radiality constraints;
  - voltage bounds and load-duration blocks;
  - SAIDI and SAIFI benchmarks with regulatory incentive rates, and
    EENS valued as lost revenue.
- Energy storage and demand response (DR) were added in [6] (Asensio et
  al., IEEE TSG 2018): a joint stochastic expansion of the network and of
  renewables. The three TPWRS papers [2]-[4] do not include storage,
  judging from their titles and keywords (not verified in full text).
- A recent review [7] covers more than 50 active-distribution planning
  papers since 2014. Active network management in those papers includes
  dynamic thermal rating, reactive power control and coordinated voltage
  control. The review finds that the cost of implementing active network
  management is often left out.

### Regulator and utility practice

**Chile: VAD and the model company (*empresa modelo*).** The Value Added
of Distribution (*Valor Agregado de Distribución*, VAD) is computed for
each typical area (*área típica*) from a model company. The consultant
designs this company to be efficient and "economically adapted to
demand" over a 15-year planning horizon, with the quality required by the
NTDx distribution technical standard (*Norma Técnica de Distribución*)
[8].

- **Standard design modules** with unit costs [8, §2.f]:
  - 1 km of medium-voltage (MV) or low-voltage (LV) network by conductor
    type and number of phases (overhead, underground or aquatic);
  - MV/LV distribution transformers (T/D);
  - capacitor banks, with and without automatic regulation;
  - voltage regulators;
  - protection and switching devices.
- **Sizing method** [8, §5.2]:
  - Conductor capacity and replacement timing are chosen "from a set of
    discrete, technically feasible alternatives". The comparison counts
    investment, removal, O&M, residual value and loss costs.
  - Distribution transformers are sized by least total present cost,
    including their effect on quality of supply.
  - Feeders are sized at minimum total cost. Their operating topology is
    the one that minimises losses.
  - Existing primary substations are taken as given references
    (location, voltage and base-year capacity).
  - The design also includes:
    - reactive compensation;
    - substations with on-load or off-load tap changers;
    - transfers of load between feeders, with the reinforcement each
      transfer needs;
    - efficient placement of reclosers and sectionalising devices;
    - remote-controlled ties, backup generation, covered conductors and
      undergrounding, considered as options to meet quality standards.
- **Storage and generation** are considered only "to meet quality of
  service standards efficiently" [8, §1].
- **Outputs** [8]: annuitised replacement value (*Valor Nuevo de
  Reemplazo*, VNR) of adapted installations, standard O&M and
  administration costs, and expansion factors for average energy and
  power losses.
- **Ley 21.194** (2019) [9] made these changes:
  - a single cost study commissioned by the National Energy Commission
    (*Comisión Nacional de Energía*, CNE), which replaced the old
    weighting of company and CNE studies;
  - an after-tax discount rate set every four years, bounded between 6 %
    and 8 %;
  - an exclusive line of business for distribution companies.
- **Current process.** The 2024-2028 preliminary technical report is
  [10].

**Great Britain: RIIO-ED2 (Ofgem).**

- **Definition.** Load-related expenditure (LRE) is "investment ... that
  responds to increases in demand to upgrade the capacity of network,
  for example to connect LCTs or new generation" [11, §3.6]. LCTs are
  low-carbon technologies such as electric vehicles and heat pumps.
- **Size.** Ex ante LRE allowances are £2.64 bn.
- **Volume drivers.** Allowances also move with volume drivers:
  - secondary reinforcement, in MVA of pole- and ground-mounted
    transformers and km of LV/HV cable or overhead line;
  - flexibility, counted as deferred secondary reinforcement (MVA or
    km).
- **Flexibility before reinforcement.** Flexibility may be procured only
  where it is economically advantageous, through transparent and
  competitive procurement (licence condition SLC 31E) [11]. The
  deferral-value formula accounts for the length of the deferral and for
  NPV. The commonly used label "flexibility first" is industry usage; it
  was not found in the Ofgem documents cited here.
- **Reliability.** Reliability is incentivised through the Interruptions
  Incentive Scheme, which sets targets for customer interruptions and
  customer minutes lost (CI/CML) [12].
- **Losses.** RIIO-ED1 had a Losses Discretionary Reward. RIIO-ED2
  dropped it because of measurement difficulties [11, §3.124].
- **Unit-rate review.** Ofgem reviewed the LRE volume-driver unit rates
  in 2026 [13].

**US laboratories and industry (EPRI, NREL, LBNL).**

- **EPRI hosting capacity [14].** EPRI's streamlined hosting-capacity
  method, implemented in DRIVE, checks four groups of limits:
  - thermal (transformer, conductor, service and secondary);
  - voltage and power quality (voltage change, regulator and tap
    impacts);
  - protection (reach, sympathetic tripping, reverse power);
  - reliability and safety (islanding).
- **NREL feeder upgrade costs [15].** NREL's feeder-level study of the
  cost of hosting PV used upgrades in this order:
  1. change regulator and load tap changer (LTC) set points;
  2. add capacitors or regulators;
  3. reconductor.
  It found reconductoring the costly last resort (about $258/ft in one
  case) and counted line-loss cost in integration cost.
- **Current planning practice [16].** NREL's interview-based survey of
  utility capacity planning covers load and DER forecasting,
  constraint criteria, solution types and decision structures.
- **Non-wires alternatives (NWAs) [17]-[18].** NWA practice is
  summarised in [17]. [18] values the deferral from behind-the-meter
  batteries with a least-cost expansion model whose candidate upgrades
  are reconductoring, transformer upgrades and voltage regulators. The
  median deferral value is about 7 $/kW-yr under the best tariff and 0
  under unrestricted time-of-use (TOU) rates.

**California: Distribution Resources Plans (DRP) and the Distribution
Investment Deferral Framework (DIDF).**

- **Annual reports.** Decision D.18-02-004 [19] requires two reports
  each year:
  - a Grid Needs Assessment (GNA): per substation or circuit, the
    service needed (capacity, reactive power, voltage, reliability,
    resiliency), the date the need must be met, the equipment rating and
    the forecast deficiency over 5 years;
  - a Distribution Deferral Opportunity Report (DDOR): planned
    investments, each flagged "deferrable by DERs, Y/N".
- **Candidate deferrals.** Each DDOR candidate deferral lists:
  - the magnitude of the need in MW or kVA;
  - the season, days and duration of the expected exceedances;
  - the estimated range of the locational net benefits analysis (LNBA);
  - the unit cost of the traditional mitigation.
- **Planning data.** Planning assumptions include demand and DER growth
  and the Integration Capacity Analysis (ICA) hosting-capacity values.
- **Services DERs can provide.** DERs are solicited for four
  distribution services: capacity, voltage support, reliability
  (back-tie) and resiliency (microgrid) [19]. Context is on the CPUC
  distribution planning page [20].

### Comparison of candidate types

Cell values are yes, partial or no.

| Candidate | Literature [1]-[7] | Chile VAD [8] | RIIO-ED [11]-[13] | EPRI/NREL HC & NWA [14]-[18] | CPUC DRP/DIDF [19] |
|---|---|---|---|---|---|
| Feeder reinforcement, reconductoring | yes: replacement options per section, conductor types and costs [2],[5] | yes: per-km modules by conductor; "optimal conductor section" | yes: km of LV/HV cable and overhead line with unit rates | yes: last-resort upgrade [15],[18] | yes: planned investments with traditional unit cost |
| New feeders | yes: new sections or routes [2],[5] | yes: efficient design of model feeders | yes: km additions | partial: not central to HC | partial: under capacity needs (not verified) |
| Substation transformers, new substations | yes: transformer options, new or expanded substations [5] | partial: T/D sized optimally; primary substations given | yes: secondary MVA driver; primary £k/MVA | partial: LTC and transformer upgrades [15],[18] | yes: substation capacity needs |
| Voltage regulators, OLTC | partial: voltage bounds only, no regulator investment [5] | yes: regulator modules; on-load tap changers | partial: not a volume driver (not verified) | yes: set points, new regulators, LTC [14],[15] | partial: "voltage support" service |
| Capacitor banks, var devices | partial: reactive control in ANM [7] | yes: capacitor banks, reactive compensation | partial: not a driver (not verified) | yes: capacitors; inverter power factor | partial: "reactive power" need |
| DER/PV hosting capacity | yes: DG investment decisions [2]-[6] | partial: net-billing and PMGD data collected | yes: LRE includes new generation and LCTs | yes: core HC method [14] | yes: ICA values in planning |
| BESS as NWA | partial: [6] only; not in [2]-[5] | partial: only for quality standards | partial: via procured flexibility | yes: [17],[18] | yes: DER solicitations for deferral |
| Demand response, flexible load | yes: [6],[7] | no | yes: secondary-flex driver; SLC 31E | yes: NWA [17] | yes: DER incl. DR |
| Switches, reconfiguration, radiality | yes: radiality; switchable sections [2],[5] | yes: protection and sectionalising; load transfers | partial: IIS-driven automation (not verified) | no | partial: back-tie reliability service |
| Reliability (SAIDI, ENS, CI) cost | yes: SAIDI/SAIFI incentives; EENS [3]-[5] | yes: NTDx standards; failure rates; options | yes: IIS (CI/CML) | partial: HC reliability/safety | partial: reliability and resiliency needs |
| Losses valuation | yes: piecewise-linear losses in NPV [2] | yes: losses in sizing; loss factors | partial: ED1 reward, dropped in ED2 | partial: line-loss cost [15] | no (not verified) |

Abbreviations in the table:

- T/D: MV/LV distribution transformer.
- LTC / OLTC: (on-)load tap changer.
- PMGD: small distributed generators in Chile (*pequeños medios de
  generación distribuida*).
- HC: hosting capacity.
- ANM: active network management.
- ICA: Integration Capacity Analysis.
- EENS: expected energy not served.
- IIS: Interruptions Incentive Scheme.
- CI/CML: customer interruptions / customer minutes lost.

### Cost data sources

**NREL distribution upgrade unit-cost database.** NREL's 2019
Distribution System Upgrade Unit Cost Database (Horowitz) [21] collects
unit costs from utilities, developers, vendors and reports. Its
equipment types include reconductoring, regulators, capacitors,
transformers and inverter-related items (categories not verified). NREL
warns that the data are inputs to feeder-specific engineering studies,
not grounds for direct cost-effectiveness comparisons.

- The NREL data portal was moved to data.nlr.gov after the laboratory
  was renamed (National Laboratory of the Rockies).
- OSTI marks the 2019 version as superseded (22 Oct 2026) by a "2026
  Distribution Asset and Cost Database" at
  <https://data.nlr.gov/submissions/339> (page returned HTTP 503; not
  verified).

**Ofgem RIIO-ED2 median unit rates.** The RIIO-ED2 Final Determinations
[11] publish median unit rates (2020/21 prices; price base not
verified):

| Item | Rate |
|---|---|
| Pole-mounted transformer (PMT) | £89.5k/MVA |
| Ground-mounted transformer (GMT) | £63.3k/MVA |
| LV cable | £141.3k/km |
| LV overhead line | £49.8k/km |
| HV cable | £127.3k/km |
| HV overhead line | £39.6k/km |

These rates were reviewed in 2026 [13].

**Chile.** The VAD bases [8] require the consultant to build unit costs
and the standard modules listed above. The results are published with
each CNE technical report [10]. A separate VNR valuation of real
installations exists in Chilean law (not verified).

**EPRI.** Public EPRI reports [14] give methods rather than cost tables.
EPRI's cost guidance on NWAs is member-restricted (not verified).

### What gtopt models

With `model_options.network_model = "lindistflow"` (formulation
[§5.6 bis](../formulation/mathematical-formulation.md#56-bis-lindistflow-distribution-network))
every candidate type of the table maps to an existing gtopt element and
its expansion fields (`capacity`, `expcap`, `expmod`, `capmax`,
`annual_capcost` or `overnight_capcost` + `economic_life` (+ `wacc`,
not used in VAD cases; see the single-rate rule below),
`fixed_om_cost`, `integer_expmod`):

| Candidate | gtopt representation | LinDistFlow coupling | Status |
|---|---|---|---|
| Reconductoring, new feeder sections | candidate `Line` (capacity 0) in parallel with the existing section | disjunctive voltage-drop rows `\|drop\| ≤ M(1 − C/C_max)` plus hull rows `\|z\| ≤ M·C/C_max`, M from the voltage bounds; MVA polygon on the capacity column | yes (LP relaxed / MIP with `integer_expmod`) |
| Substation transformer upgrade | expansion modules on the transformer `Line` | MVA polygon bounded by the capacity column | yes |
| Voltage regulator / OLTC | `tap_ratio_min`/`tap_ratio_max` on the transformer line | exact continuous tap (`y = w_a/t²`) | operation yes; tap investment and discrete steps: no |
| Capacitor banks | Bus `shunt_q` (fixed, `B·\|V\|²`) | reactive balance | fixed banks yes; switched banks and capacitor investment: no (needs a Capacitor element) |
| Inverter var support | Generator `qmin`/`qmax` or `power_factor` | reactive balance | operation yes; not tied to the expanded capacity |
| DER / PV hosting capacity | candidate `Generator` with zero cost and a profile | voltage band + MVA polygon bind the build | yes (`opendss2gtopt --study hosting`) |
| BESS non-wires alternative | candidate `Battery` (energy expansion, fixed power) | active balance (unity pf) | yes; reactive output of the BESS: no |
| Demand response | `Demand` with `fail_segments` / elastic demand | active and reactive balance | yes (no dedicated DR candidate) |
| Switches, reconfiguration, radiality | `Line type=switch` (closed) | — | no (radiality constraints not implemented) |
| Reliability (SAIDI/ENS) | unserved energy at `demand_fail_cost` | — | proxy only (no outage simulation) |
| Losses valuation | active-power loss modes of `use_line_losses` at the energy price | not in the voltage/Q rows | yes (P losses only) |

### Survey references

[1] P. S. Georgilakis and N. D. Hatziargyriou, "A review of power
distribution planning in the modern power systems era: Models, methods
and future research," *Electric Power Systems Research*, vol. 121,
pp. 89-100, 2015. doi:10.1016/j.epsr.2014.12.010,
<https://doi.org/10.1016/j.epsr.2014.12.010>

[2] G. Muñoz-Delgado, J. Contreras, J. M. Arroyo, "Joint expansion
planning of distributed generation and distribution networks," *IEEE
Trans. Power Syst.*, vol. 30, no. 5, pp. 2579-2590, 2015.
<https://doi.org/10.1109/TPWRS.2014.2364960>

[3] G. Muñoz-Delgado, J. Contreras, J. M. Arroyo, "Multistage generation
and network expansion planning in distribution systems considering
uncertainty and reliability," *IEEE Trans. Power Syst.*, vol. 31, no. 5,
pp. 3715-3728, 2016. <https://doi.org/10.1109/TPWRS.2015.2503604>

[4] G. Muñoz-Delgado, J. Contreras, J. M. Arroyo, "Distribution network
expansion planning with an explicit formulation for reliability
assessment," *IEEE Trans. Power Syst.*, vol. 33, no. 3, pp. 2583-2596,
2018. <https://doi.org/10.1109/TPWRS.2017.2764331>

[5] M. Jooshaki et al., "An enhanced MILP model for multistage
reliability-constrained distribution network expansion planning," *IEEE
Trans. Power Syst.*, vol. 37, no. 1, pp. 118-131, 2022 (CC BY).
<https://doi.org/10.1109/TPWRS.2021.3098065>; open copy:
<https://acris.aalto.fi/ws/portalfiles/portal/78143418/ELEC_Jooshaki_etal_An_Enhanced_MILP_Model_IEEE_TPWRS_2021.pdf>

[6] M. Asensio, P. Meneses de Quevedo, G. Muñoz-Delgado, J. Contreras,
"Joint distribution network and renewable energy expansion planning
considering demand response and energy storage - Part I: Stochastic
programming model," *IEEE Trans. Smart Grid*, vol. 9, no. 2,
pp. 655-666, 2018. <https://doi.org/10.1109/TSG.2016.2560339>
(Part II: <https://doi.org/10.1109/TSG.2016.2560341>)

[7] I. Laamanen, S. Repo, T. Stetz, "Active network management
integration in expansion planning of active distribution networks: A
comprehensive review," *IEEE Access*, 2026 (CC BY).
<https://doi.org/10.1109/ACCESS.2025.3650636>

[8] CNE, Res. Exenta N° 221 (2023), *Bases Técnicas Definitivas del
Estudio de Costos de Componentes del VAD, cuadrienio 2024-2028*.
<https://www.cne.cl/wp-content/uploads/2023/05/RE-CNE-N°-221-Aprueba-BT-Definitivas-VAD-24-28.pdf>

[9] Ley N° 21.194 (Dec. 21, 2019), "Rebaja la rentabilidad de las
empresas de distribución y perfecciona el proceso tarifario de
distribución eléctrica."
<https://www.cne.cl/wp-content/uploads/2020/01/LEY-21194_21-DIC-2019-1Ley-corta-Distribución.pdf>

[10] CNE, VAD 2024-2028 process page and Res. Exenta N° 58 (2026),
*Informe Técnico Preliminar v2*.
<https://www.cne.cl/tarificacion/electrica/valor-agregado-de-distribucion/vad-2024-2028/>,
<https://www.cne.cl/wp-content/uploads/2026/01/RE-CNE-N58-2026-Aprueba-ITPv2-VAD-24-28.pdf>

[11] Ofgem, *RIIO-ED2 Final Determinations - Core Methodology Document*,
Nov. 2022.
<https://www.ofgem.gov.uk/sites/default/files/2022-11/RIIO-ED2%20Final%20Determinations%20Core%20Methodology.pdf>

[12] Ofgem, *RIIO-ED2 Final Determinations - Overview Document*, Nov.
2022.
<https://www.ofgem.gov.uk/sites/default/files/2022-11/RIIO-ED2%20Final%20Determinations%20Overview%20document.pdf>

[13] Ofgem, *Review of RIIO-ED2 Load Related Expenditure volume drivers
- decision*, Jun. 2026.
<https://www.ofgem.gov.uk/sites/default/files/2026-06/LRE-VD-review-consultation-decision.pdf>

[14] EPRI, *Integration of Hosting Capacity Analysis into Distribution
Planning Tools*, Product 3002005793, Jan. 2016.
<https://restservice.epri.com/publicdownload/000000003002005793/0/Product>

[15] K. Horowitz, F. Ding, B. Mather, B. Palmintier, *The Cost of
Distribution System Upgrades to Accommodate Increasing Penetrations of
Distributed Photovoltaic Systems on Real Feeders in the United States*,
NREL/TP-6A20-70710, 2018. <https://doi.org/10.2172/1432760>

[16] J. Keen, J. Giraldez, E. Cook, A. Eiden et al., *Distribution
Capacity Expansion Planning: Current Practice, Opportunities, and
Decision Support*, NREL/TP-6A40-83892, 2022.
<https://doi.org/10.2172/1898008>

[17] LBNL/NARUC, *Non-Wires Alternatives: Best Practices* (slides,
2023).
<https://eta-publications.lbl.gov/sites/default/files/2023_naruc_bode_nwa_best_practices_final.pdf>

[18] L. Rodriguez-Garcia, G. Barbose, S. Forrester, M. Blonsky, M. Heleno,
"Estimating the impact of tariff-driven behind-the-meter storage
operation on distribution grid investments," *Energy Reports*, vol. 16,
2026. <https://doi.org/10.1016/j.egyr.2026.109531>

[19] CPUC, Decision 18-02-004 (R.14-08-013), *Distribution Investment
Deferral Framework*, Feb. 2018.
<https://docs.cpuc.ca.gov/PublishedDocs/Published/G000/M209/K858/209858586.PDF>

[20] CPUC, *Distribution Planning* (DRP/IDER/DIDF overview page).
<https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/infrastructure/distribution-planning>

[21] K. Horowitz, *2019 Distribution System Upgrade Unit Cost Database*,
NREL, 2019. doi:10.7799/1491263, <https://doi.org/10.7799/1491263>
(OSTI record: <https://www.osti.gov/biblio/1491263>)

## VAD: the Chilean distribution tariff study

The *Valor Agregado de Distribución* (VAD) is the regulated remuneration
of Chilean distribution companies. Every four years the Comisión Nacional
de Energía (CNE) computes it from a cost study of an *empresa modelo*: a
hypothetical company, built from scratch, that serves the real customers
of a reference company at minimum efficient cost. This section summarises
the legal frame, the 2024-2028 process and its key parameters, and maps
them to the feeder-level LinDistFlow expansion model in gtopt. Every
number cites a public source; items marked *(not verified)* could not be
confirmed in a primary document.

### Legal frame

- **Components (LGSE art. 182).** The VAD rests on model companies and
  includes (i) fixed costs of administration, billing and customer
  service, independent of consumption; (ii) average distribution
  losses of power and energy; and (iii) standard costs of investment,
  maintenance and operation per unit of power supplied. Annual
  investment cost uses the *valor nuevo de reemplazo* (VNR) of
  facilities adapted to demand, their useful life, and the discount
  rate of art. 182 bis [4].
- **Discount rate (art. 182 bis, added by Ley 21.194 of 2019).** The CNE
  computes the rate every four years. It is applied **after taxes**, as
  risk-free rate + market risk premium x systematic risk (CAPM), and
  must lie **between 6 % and 8 %**. Before the reform it was a fixed
  10 % real per year [1]. For 2024-2028 the CNE's consultant estimated
  5.56 % (beta 0.507, MRP 6.86 %, Rf 2.08 %) [5], below the floor, so
  the bases fix **6.00 % after taxes** [3], [4]. The 2020-2024 process
  also landed on the floor (5.15 % computed, 6.00 % applied) [5].
- **Empresa modelo (art. 183, as rewritten by Ley 21.194).** Efficiency
  must respect the real company's constraints: customer location and
  demand, the technical norms (security and quality), the street layout
  and physical obstacles, the penetration speed of new technologies,
  regulatory changes in quality standards, and vegetation [1].
- **Áreas típicas (art. 225 m).** Areas where the cost of service and
  the customer density per km of network are similar; an area may
  include one or more companies [1]. For 2024-2028 the CNE fixed 12
  áreas típicas (ATD), each with one reference company (ATD 1 Enel
  Distribución, 2 CGE, 3 Chilquinta, 4 SAESA, 5 Frontel, ... 12
  Coelcha) [4], [14].
- **Process (art. 183 bis).** Citizen-participation registry, bases
  técnicas preliminares / corregidas / definitivas (with discrepancies
  to the Panel de Expertos), a cost study tendered by the CNE and
  supervised by a committee of companies, Ministry and CNE (at most 8
  months), a CNE *informe técnico preliminar* within 3 months,
  observations, corrected report, Panel discrepancies (the Panel can
  only pick final values per cost category), then the *informe técnico
  definitivo* and tariff formulas sent to the Ministry, which issues
  the decree [1].

### The 2024-2028 process

| Step | Document | Ref |
|------|----------|-----|
| Áreas típicas | RE 499 (30-06-2022), rectified by RE 589 | [2], [14] |
| Bases técnicas definitivas | RE 221 (29-05-2023) | [3] |
| Discount-rate study | ATAINVER, *Informe Final*, 30-08-2022 | [5] |
| Cost-study committee | RE 535 (07-11-2023) | [2] |
| Consultant final report | v3 delivered 22-07-2025 | [4] |
| Informe técnico preliminar | RE 626 (10-10-2025), annulled | [2] |
| Informe técnico preliminar v2 | RE 58 (30-01-2026) | [4], [15] |
| Informe técnico definitivo, decree | not found on the CNE page | [2] |

Key study settings from the bases: base year 2022; planning horizon
2023-2037 (15 years); tariff horizon 2024-2028; discount rate 6.00 %
after taxes [3]. The network is designed "from zero" minimising total
cost over the 15-year horizon, in two stages: first an efficient base
network, then the extra investment needed to meet the quality norm
(NTD) [4]. Losses are valued at the energy and power price at the entry
of the distribution system from the *Decreto de Precios de Nudo
Promedio* in force at 31-12 of the base year, without the art. 157
adjustments [3].

### What the model company optimises

In planning-model terms the empresa modelo network is the solution of

    min  sum_t  [ aVNR_t + COyM_t + C_loss_t ]
    s.t. serve the projected demand of every customer,
         voltage regulation and quality limits of the NTD,
         technical norms (RPTD pliegos), standard design modules,

where `aVNR_t` is the annuity of the VNR (capital recovery factor at the
6 % rate over each asset group's life), `COyM_t` the efficient
operation, maintenance and administration cost, and `C_loss_t` the
technical losses valued at node prices [3], [4]. Design choices the
consultant must evaluate include conductor section, pole spans,
reactive compensation, on-load or off-load tap changers, transpositions,
feeder transfers, backup generation, protected or underground network,
and the number and location of distribution transformers and of
protection and switching devices [3]. Standard design modules include
1 km of MV/LV network by capacity and conductor, distribution
transformers with their LV network, capacitor banks with or without
automatic control, voltage regulators, and protection and switching
devices [3].

**Quality enters as a constraint, not as an ENS cost.** The ITP sizes
the additional investment needed to meet the NTD and explicitly does
not add the art. 16 B compensations to the model company's costs [4].
Those compensations (twice the energy not supplied, valued at the
*costo de racionamiento*) are paid by the real company for
unauthorised interruptions [13].

### Key parameters

| Parameter | Value | Ref |
|-----------|-------|-----|
| Discount rate | 6.00 % real, after taxes (law band 6-8 %) | [1], [3] |
| Life: bare / insulated conductor | 40 years | [3] |
| Life: protected conductor | 35 years | [3] |
| Life: electromechanical equipment (transformers, capacitors, regulators) | 30 years | [3] |
| Life: poles and structures, civil works | 50 years | [3] |
| Life: digital protections, meters | 15 years | [3] |
| Life: control and telecontrol | 10 years | [3] |
| Life: land, easements, intangibles, working capital | perpetual | [3] |
| MV voltage regulation, high/medium density | +/-6 % | [6] |
| MV voltage regulation, low/very low/extremely low density | +/-8 % | [6] |
| LV voltage regulation, high/medium / lower densities | +/-7.5 % / +/-10 % | [6] |
| SAIDI limit from 2026 (h/yr), density A/M/B/MB/EB | 5 / 7 / 9 / 11 / 14 | [6] |
| SAIFI limit from 2026 (1/yr), density A/M/B/MB/EB | 3.5 / 4.5 / 5.5 / 6.5 / 7 | [6] |
| Average node price (PNP), SEN, 1st half 2027 (ITP) | 97.888 USD/MWh | [9] |
| PNP power, Enel Distribución | 12.31 USD/kW-month | [9] |
| Short-run node price, Quillota 220 kV (DS 16T/2026) | 52.970 USD/MWh, 9.95 USD/kW-month | [9] |
| Short-duration outage cost (CFCD), SEN, Aug-2026 | 13.73 USD/kWh | [7] |
| Long-duration outage cost (CFLD), SEN, depth 0-5 % | 795.11 USD/MWh | [7] |

Results of the ITP v2 [4] give useful scale checks (MM CLP of Dec of the
base year; ratios are unit-free):

- Annuity of electrical-installation investment over VNR (base +
  quality), base year 2022: 6.68 % (ATD 1) to 6.97 % (ATD 5). At 6 %
  this is a weighted life of about 39 years.
- Quality-driven VNR as a share of total network VNR: 3.0 % (ATD 1,
  dense urban) to 33 % (ATD 5, rural Frontel).
- COyM over network VNR: 4.6 % (ATD 1) to about 30 % (ATD 9). This
  COyM includes administration, so it is not a pure maintenance rate.
- VAD per kW of peak demand of the whole distribution system (VADSD),
  2024: 73,376 CLP/kW-yr (ATD 1) to 640,507 CLP/kW-yr (ATD 12).

The CFCD is re-indexed at each tariff process: 8.79 USD/kWh in March
2026 [8], 13.73 USD/kWh from August 2026 after the new 2026 outage-cost
study [7], [16].

### Public unit costs for the demo

The CNE's VAD unit prices (ITP annex 3-7, built on the SEC VNR) are not
published as a downloadable table on the process page [2], [4]. The
demo therefore uses a small set of public numbers (`vad_params.json`):

- **MV overhead lines (Chile).** CNE-approved values of 23 kV lines in
  Aysén, USD of Dec 2020: 1,072,678 USD for 21.6 km (49,661 USD/km)
  and 633,420 USD for 8 km (79,178 USD/km); a new 9 km, 9 MVA 23 kV
  circuit at 438,822 USD (48,758 USD/km) [10]. Conductor section is
  not stated.
- **HV/MV substation (Chile).** New 66/13.2 kV transformer of at least
  16 MVA with on-load tap changer, both bays and a 13.2 kV bar with
  four feeder bays: 5,885,691 USD, 33-year life (about 368 kUSD/MVA);
  reference COMA 5.46 % of VI per year (zonal system F; 4.70 % in
  system C) [11].
- **Feeder equipment (United States, labelled international).** 2017
  USD low/mid/high: 300 kvar three-phase capacitor 6,000 / 8,290 /
  10,700; new 12 kV line regulator 150,000 / 166,000 / 183,000; new
  three-phase 100 kVA overhead transformer 15,600 / 32,500 / 49,300;
  50 kVA 10,400; LTC added to a substation transformer 310,000 [12].

### One rate: the regulated VAD rate as the social rate

The VAD study uses ONE rate, the regulated rate of LGSE art. 182 /
182 bis for the current process (6.00 % real after taxes, VAD 2024-2028,
CNE RE 221/2023, [1], [3]), for both the VNR annuities of every
candidate and the discounting of the horizon.  In gtopt the rate is
`simulation.annual_discount_rate` and no candidate sets `wacc`, so
every annuity falls back to the social rate
(`opendss2gtopt --study vad` writes the case that way).  The empresa
modelo is, by construction, remunerated at the regulated rate: there is
no private-investor rate distinct from the planning rate, so the
private-rate (financing) wedge of
[investment economics](investment-economics.md#7-social-vs-private-discount-rates)
is zero by construction and no private-investor evaluation is run for
VAD cases.

### Mapping VAD concepts to the planning model

| VAD concept | gtopt / LinDistFlow concept |
|-------------|-----------------------------|
| VNR (valor nuevo de reemplazo) of a module | overnight capex of a candidate element (line, transformer, capacitor, DER, BESS) |
| Vida útil per asset group | economic life used for the annuity |
| Tasa de actualización (art. 182 bis, 6 %) | the single social rate `simulation.annual_discount_rate`; annualised capex = CRF(6 %, life) x VNR; no per-candidate `wacc` |
| COyM | fixed O&M as % of capex per year (or per-element fixed cost) |
| Pérdidas medias de energía y potencia | I^2 R loss term valued at the energy price (PNP) plus peak-loss kW at the power price |
| Calidad de suministro (NTD SAIDI/SAIFI) | reliability constraint; in a pure LP, a proxy via ENS / demand curtailment cost (CFCD or CFLD) |
| Regulación de tensión (NTD) | bus voltage bounds, e.g. 0.94-1.06 p.u. (MV, dense) |
| Área típica | representative feeder (IEEE 34 / 123 bus) with density-class parameters |
| Demanda de dimensionamiento | peak-block demand of the planning horizon |
| Horizonte de planificación 15 años | multi-stage expansion horizon |
| Módulos estándares de diseño | discrete candidate sizes (conductor sections, kVA, kvar steps) |
| Holguras por indivisibilidad | integer build variables (MIP) |

### VAD área típica demo (IEEE 34 and IEEE 123)

`opendss2gtopt --study vad` on the IEEE 34-bus (long rural 24.9 kV
feeder) and IEEE 123-bus (urban 4.16 kV feeder) test feeders: demand
grown ×1.6 to the dimensioning year, 24 hourly blocks of the peak day,
NTD band 0.94-1.06 p.u. on every bus, one 6 % rate (social rate, no
`wacc`), PNP 97.888 USD/MWh for energy and losses, CFLD
795.11 USD/MWh for unserved energy, 23 kV line VNR 49,661 USD/km,
substation 367,856 USD/MVA (also used, as an assumption, for every
other transformer), BESS 400 USD/kWh (assumption).  Candidates: a
parallel circuit on every section, transformer upgrades in two modules,
a 4-hour BESS at the feeder end.  Solver HiGHS 1.16, one thread, on a
shared 20-core host (load 20-38, so times are pessimistic); LP by
barrier, MIP with `integer_expmod` and relative gap 1e-3.  The AC check
writes the solved case (built candidates, taps, dispatch) with
`gtopt2opendss` and runs the OpenDSS AC power flow at the peak block.

| Case | LP time | LP obj | MIP time | MIP obj | LP/MIP gap | Built (MIP) | AC max \|ΔV\| LP / MIP | AC min V (MIP) |
|------|--------:|-------:|---------:|--------:|-----------:|-------------|-------------------------:|---------------:|
| IEEE 34 VAD | 7 s | 7,700 | 203 s | 8,890 | 13 % | 10 parallel 17.3 MVA circuits on the trunk, L32 lateral, XFM1 +0.275 MVA; 0.71 MWh shed | 0.079 / 0.016 | 0.924 |
| IEEE 123 VAD | 44 s | 13,409 | 1,728 s | 13,742 | 2.4 % | regulator 1 +2.75 MVA, 16 parallel 2.9 MVA sections | 0.025 / 0.0015 | 0.9385 |
| IEEE 34 hosting | 2 s | 1,845 | 2 s | 1,845 | 0 | 3.4 MW (LP) / 3.5 MW (MIP) PV at 890 | 0.012 / 0.014 | 0.936 |
| IEEE 123 hosting | 13 s | 17,432 | 10 s | 17,432 | 0 | 0.54 MW (LP) / 0.6 MW (MIP) PV at 610 | 0.0014 / 0.0018 | 0.939 |

**Scaling** (same VAD study on the synthetic radial feeders of
`opendss2gtopt.feeders.synthetic_feeder_dss`, 12.47 kV, 24 blocks,
16 loss segments, HiGHS barrier, one thread, same shared host):

| Buses | Lines (incl. candidates) | LP time |
|------:|-------------------------:|--------:|
| 133 (IEEE 123) | 250 | 44 s |
| 251 | 499 | 88 s |
| 501 | 999 | 253 s (65 s with 4 loss segments) |
| 1,001 | 1,999 | 805 s (281 s with 4 loss segments) |
| 2,001 | 3,999 | 9,573 s (2.7 h) |

The LP time grows about as n^1.6 up to 1,000 buses; the loss
segments are the largest share of the columns (2 × 16 per line and
block).  Fewer segments are faster but not usable on long feeders: on
the 501-bus feeder 4 segments raise the objective from 23,664 to
183,194 USD/day, because the secant over-estimates the losses and the
LP sheds load instead.  At 2,001 buses CPLEX (internal check, barrier,
4 threads) ended in 19 min with "optimal with unscaled
infeasibilities" (1.8e-6) and its primal/dual fallback reported the LP
infeasible; HiGHS solved it in 2.7 h (objective 31,411 USD/day, 478
fractional candidates; the AC image of the fractional plan does not
converge, as expected for an LP relaxation).  The
MIP of the synthetic feeders (relative gap 1e-2) had not finished
after 30 min at 251 buses: with thousands of binary circuits the
disjunctive model needs candidate pre-screening (LP ranking, then MIP
on the short list) or decomposition.

Objectives are USD per peak day (annuities and COyM per 24 h, energy at
PNP, unserved energy at CFLD).  No case builds the BESS at
400 USD/kWh: on these feeders reconductoring is the cheaper way to hold
the voltage band.

Findings:

- **MIP is the study; the LP is a bound.**  The LP relaxation builds a
  few percent of many parallel circuits; the hull rows
  ([§5.6 bis](../formulation/mathematical-formulation.md#56-bis-lindistflow-distribution-network))
  limit what a fraction of a circuit can carry, but a fractional
  circuit has no AC image: written back as whole circuits it
  over-compensates (IEEE 34: 1.14 p.u. in the AC check).  The LP
  objective is a valid lower bound (2-13 % below the MIP here) and
  ranks the candidates; the MIP gives the network the company builds.
- **AC agreement of the MIP plans.**  LinDistFlow neglects the series
  losses in the voltage rows, so it is optimistic at the far end of
  heavily loaded laterals: 0.0015 p.u. on IEEE 123, 0.016 p.u. at the
  4.16 kV bus 890 of IEEE 34, where the AC voltage ends at 0.924
  instead of 0.94.  In a study, tighten `vmin_pu` by the AC error of
  the first pass and re-solve, or add the AC check as an acceptance
  step.
- **Losses need several segments.**  gtopt's default single loss
  segment is the secant at the full rating; on IEEE 34 it overstated
  the losses about four-fold and pulled the voltages down by up to
  0.4 p.u.  `opendss2gtopt` writes `loss_segments = 16` for LinDistFlow
  cases.
- **Hosting.**  The PV hosting limit at the end of IEEE 34 is about
  3.5 MW (voltage rise at the 24.9 kV end), at bus 610 of IEEE 123
  about 0.6 MW (regulator and lateral limits).  The base IEEE 123 case
  already sheds 14 MWh/day to stay above 0.94 p.u. without investment;
  the hosting study keeps that operating point.

### What a real CNE study has that a feeder model lacks

A LinDistFlow feeder study reproduces only the MV network-sizing core.
A real empresa modelo study also covers:

- **LV networks and distribution transformers sized per customer**,
  with LV routing, phase balancing and LV voltage checks [4].
- **Customers, connections and meters**: measurement, monitoring and
  control systems (SMMC), measurement campaigns, and the meter-reading,
  billing, collection and customer-service fixed costs [3], [4].
- **Commercial and administrative organisation**: staffing,
  salaries, offices, buildings, IT, SCADA, vehicles, intangibles and
  working capital (1/12 of annual revenue) [4].
- **Non-technical losses and bad debt**: optimal theft level and theft
  control cost, write-offs from financial statements [4].
- **Fault statistics and reliability design**: failure rates and
  times by network type, the 36 SEC fault causes, protection and
  sectionalising placement, tie switches, emergency crews [3], [4].
- **Geography**: GIS street layout, coastal pollution, lightning above
  1,000 m, vegetation management, river and railway crossings,
  municipal rights, pavement replacement and easements [3], [4].
- **Demand forecast**: municipal econometrics, hidden demand behind
  self-generation, electric-vehicle charging and electric buses [4].
- **Associated services, AT/BT cost split and tariff formulas**,
  including income-tax adjustments (AEIR) and indexation [4].

### VAD references

[1] Ley 21.194, *Rebaja la rentabilidad de las empresas de distribución
y perfecciona el proceso tarifario de distribución eléctrica*, D.O.
21-12-2019.
https://www.cne.cl/wp-content/uploads/2020/01/LEY-21194_21-DIC-2019-1Ley-corta-Distribuci%C3%B3n.pdf

[2] CNE, *VAD 2024-2028* (process page).
https://www.cne.cl/tarificacion/electrica/valor-agregado-de-distribucion/vad-2024-2028/

[3] CNE, RE 221 (29-05-2023), *Bases Técnicas Definitivas VAD
2024-2028*.
https://www.cne.cl/wp-content/uploads/2023/05/RE-CNE-N%C2%B0-221-Aprueba-BT-Definitivas-VAD-24-28.pdf

[4] CNE, RE 58 (30-01-2026), *Informe Técnico Preliminar v2, cálculo de
las componentes del VAD, cuadrienio nov 2024 - nov 2028*.
https://www.cne.cl/wp-content/uploads/2026/01/RE-CNE-N58-2026-Aprueba-ITPv2-VAD-24-28.pdf

[5] ATAINVER, *Informe Final - Metodología de cálculo para la tasa de
actualización de una empresa modelo de distribución eléctrica*,
30-08-2022.
https://www.cne.cl/wp-content/uploads/2025/05/Informe-Final.pdf

[6] CNE, *Norma Técnica de Calidad de Servicio para Sistemas de
Distribución*, April 2024 (RE 210/2024), Tablas 3, 15, 16.
https://www.cne.cl/wp-content/uploads/2024/05/NTCSDx2024-1.pdf

[7] CNE, RE 432 exenta (17-08-2026), new CFCD and CFLD values, D.O.
21-08-2026.
https://www.diariooficial.interior.gob.cl/publicaciones/2026/08/21/44530/01/2857667.pdf

[8] CNE, RE 112 exenta (03-03-2026), previous CFCD/CFLD values.
https://www.cne.cl/wp-content/uploads/2026/03/Res.-Ext.-N112-comunica-CFCD-y-CFLD.pdf

[9] CNE, *Fijación de Precios de Nudo Promedio, 1er semestre 2027,
Informe Técnico Preliminar*, Oct 2026, Tablas 19, 20, 22.
https://www.cne.cl/wp-content/uploads/2026/10/ITP-PNP-enero-2027-vCNE091026.pdf

[10] CNE, RE 187 (11-05-2023), *Informe Técnico, sistemas medianos de
Aysén, Palena, General Carrera y Puerto Cisnes 2022-2026*, Tabla 3-10.
https://www.cne.cl/wp-content/uploads/2023/05/Rex-187-aprueba-IT-SSMM-Aysen-Palena-General-Carrera-y-Puerto-Cisnes.pdf

[11] CNE, *Informe Técnico Definitivo, Plan de Expansión de la
Transmisión 2025*, Tablas 4-1 and 4-6.
https://www.cne.cl/wp-content/uploads/2026/09/ITD-Plan-de-Expansion-de-la-Transmision-2025.pdf

[12] K. Horowitz, F. Ding, B. Mather, B. Palmintier, *The Cost of
Distribution System Upgrades to Accommodate Increasing Penetrations of
Distributed Photovoltaic Systems on Real Feeders in the United States*,
NREL/TP-6A20-70710, April 2018, Tablas 4-6.
https://docs.nlr.gov/docs/fy18osti/70710.pdf
(record: https://www.osti.gov/biblio/1432760)

[13] Poder Judicial de Chile, ruling citing Ley 18.410 art. 16 B
(compensation = twice the energy not supplied valued at rationing
cost).
https://www.pjud.cl/prensa-y-comunicaciones/getRulingNew/17588

[14] CNE, RE 589 (27-07-2022), rectifies the áreas típicas 2024-2028.
https://www.cne.cl/wp-content/uploads/2022/07/Rex.-589-Rectifica-ATD-2024-2028_27.07.22.pdf

[15] Ministerio de Energía, *CNE publica Informe Técnico Preliminar del
proceso tarifario VAD 2024-2028*, 30-01-2026.
https://energia.gob.cl/node/27079

[16] CNE, *Costos de falla* (process page).
https://www.cne.cl/tarificacion/electrica/costos-de-falla/

