# AEMO ISP hydrogen in the gtopt weekly cases

Design note for the hydrogen part of `plexos2gtopt.aemo_isp`
(branch `feat/aemo-isp-constraints`).

## How AEMO models hydrogen

Sources:

- AEMO, *ISP Methodology*, June 2025, §2.4.8 "Modelling hydrogen in
  the capacity outlook model" (pp. 57-60);
- the 2026 IASR workbook (electrolyser and H2 sheets);
- the PLEXOS model instructions (2024 and 2026), which say nothing
  about hydrogen.

The methodology describes the following:

- Grid-connected electrolysers are electricity loads located in REZs.
  Hydrogen storage and pipeline costs are folded into the electrolyser
  capex.
- Domestic and export hydrogen demands are flexible loads with minimum
  production targets over a period. The 2026 ISP methodology moves this
  period to **weekly**; the 2024 ISP used monthly.
- Export and green-commodity facilities also carry an **inflexible
  baseload**.
- There is a minimum annual utilisation factor (value in the IASR
  workbook).
- Electrolyser builds are linear decisions. The capacity is not fixed
  in advance.

The IASR workbook gives, per study year, the PEM electrolyser specific
consumption (kWh_e/kg), the hydrogen heat content, the inflexible load
associated with each kg and the monthly demand profiles / sub-regional
demand.

Those values are **not reproduced here**: AEMO workbooks may be used but
not redistributed.  The source (page, terms, edition) is registered in
`scripts/gtopt_operators/aemo/sources.toml`; get the workbook with
`gtopt-fetch aemo get iasr_workbook --version <ISP year>` (prints the
page and terms; register the downloaded file with `--file`), read the
values there and pass them on the command line.
- Monthly demand profiles and sub-regional demand in Mt.

## What the PLEXOS XML (fixed-fleet DLT data) contains

The XML has an electricity-only representation:

- **Purchasers** are MW loads. The flexible electrolysers have
  `Max Load` and `Bid Price` 0. The baseload ones have `Fixed Load`.
- The **H2 consumption constraints** are of three kinds: "H2 Subregion
  Domestic Consumption", "H2 Subregion/REZ Green Commodities
  Consumption" and "H2 Supply Infrastructure Limitations". All are
  `RHS Month` equalities in GWh_e on the purchasers' `Load`.
- There is no hydrogen balance, no conversion efficiency and no H2
  storage object.
- The 5 "Daily H2 Limit" constraints (Fuel memberships, no data) and
  the 4 "Liquid Fuel Offtake" constraints have `Include in LT Plan = 0`.
- "GG/MEL H2 Domestic" have only a `Sense`: no RHS and no
  coefficients in any collection.

## Mapping chosen

```
Bus ──(CarrierConverter "electrolyser", η, capacity = Max Load,
       ocost = −Bid Price)──► HydrogenNode h2_<constraint>
                              └──► HydrogenDemand min/max_energy
Bus ──Demand "purchaser_fixed" (lmax = Fixed Load schedule)
```

- **Electrolysers.** Each flexible Purchaser becomes a
  `CarrierConverter`.
  - Efficiency: η = 33.33 / c MWh_LHV per MWh_e, where c is the PEM
    specific consumption [kWh_e/kg] of the IASR workbook (the XML has
    no efficiency; 33.33 kWh/kg is the H2 lower heating value).  CLI
    option `--electrolyser-kwh-per-kg`, required when the case has H2
    targets (there is no built-in default).
- **H2 targets.** Each monthly H2 consumption constraint becomes one
  `HydrogenNode` and one `HydrogenDemand`.
  - The target is the RHS pro-rated to the window. This is an
    approximation: a one-week window comes close to AEMO's new weekly
    balancing.
  - Units: GWh_e × 1000 × η gives MWh_LHV.
  - Sense mapping: = sets `min_energy = max_energy`; ≥ sets only
    `min_energy`; ≤ sets only `max_energy`.
  - Penalty: a positive Penalty Price gives `min_energy_cost`. A missing
    penalty or −1 makes the target hard.
- **Purchaser coefficients in other constraints** (network,
  operational) reference the electrolyser input. With η constant this
  is identical to referencing the PLEXOS Load:
  `carrier_converter('<name>').input`.
- **Baseload purchasers** become ordinary `Demand`s with a fixed
  `lmax` schedule.
- **Fallback.** `--h2-model demand` gives the old electricity-only
  view. Electrolysers become curtailable `Demand`s, and the H2
  equalities become window-sum UserConstraints on their load.

The H2 production cost is the same in both models. The native model
adds an H2 shadow price and keeps the energy in MWh_LHV, so a
HydrogenStorage or a hydrogen-fuelled generator can be attached later.

## gtopt additions (C++)

1. **`HydrogenDemand`**: the missing sink of the H2 topology.
   - Fields:
     - `hydrogen_node`;
     - per-block `lmin`/`lmax` [MW_LHV];
     - per-stage `min_energy`/`max_energy` [MWh_LHV];
     - `min_energy_cost` (soft shortfall);
     - per-block `price` (value of the offtake).
   - LP:
     - one `load` column per block with −1 in the node balance;
     - stage rows `Σ dur·load (+slack) ≥ min_energy` and
       `Σ dur·load ≤ max_energy`.
   - Outputs: `HydrogenDemand/load_sol`, `min_energy_dual` (marginal H2
     cost) and slack.
   - Before this element, a HydrogenNode with only an electrolyser
     forced the electrolyser to zero.
2. **PAMPL element types.** `carrier_converter`, `hydrogen_storage` and
   `hydrogen_demand` can be used in UserConstraint expressions. They
   are registered in the AMPL registry: params plus `sum(...(all))`
   iteration.

Considered and rejected for now:

- **CarrierConverter minimum input** (inflexible baseload or
  utilisation floor). The XML carries the baseload as separate
  fixed-load Purchasers. The annual utilisation factor cannot be
  enforced in a one-week window.
- **Electrolyser expansion.** The fixed-fleet DLT XML already has the
  built capacity.
- **Hydrogen as a GPG fuel.** Not modelled. H2-capable GPG keeps its
  XML fuel and price; nothing links it to the H2 nodes.

## Open points (user decisions)

- **Efficiency source.** The XML has none, so the IASR PEM value of the
  study year must be passed with `--electrolyser-kwh-per-kg`.  Next
  step: extract it from the user's workbook during case preparation.
- **Pro-rating of monthly targets.** The weekly target is month × 7 /
  days(month). AEMO's weekly balancing would use the monthly profile
  divided by weeks, which gives the same result for a uniform month.
- **Supply Infrastructure Limitations.** These are `≤` limits. They
  stay as UserConstraints on `carrier_converter(...).input`, because
  they bound electrolyser electricity, not H2 delivered.
- **Hard targets.** The H2 targets are hard (no penalty in the XML).
  An electrolyser whose Max Load cannot reach the pro-rated target
  makes the week infeasible. The converter warns when a target exceeds
  window capacity × η.
