# Mathematical Formulation of the GTEP Optimization Problem

> **GTOpt** solves a multi-stage, multi-scenario **Generation and Transmission
> Expansion Planning (GTEP)** problem formulated as a sparse linear program
> (LP/MIP). This document describes the complete mathematical formulation in
> detail.

> **Rendering note:** This document uses [GitHub Flavored Markdown LaTeX math](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/writing-mathematical-expressions).
> View it on GitHub or in a Markdown renderer with math support.
> In VS Code, install the **Markdown Preview Enhanced** extension and enable `markdown-preview-enhanced.enableEHTML`.
> All display formulas use `$$...$$` blocks and all inline symbols use `$...$` notation.

## Table of Contents

1. [Overview](#1-overview)
2. [Notation and Sets](#2-notation-and-sets)
3. [Compact Formulation](#3-compact-formulation)
4. [Objective Function](#4-objective-function)
5. [Constraints by Component](#5-constraints-by-component)
   - [5.1 Bus Power Balance](#51-bus-power-balance)
   - [5.2 Generator Constraints](#52-generator-constraints)
   - [5.3 Generator Profile Constraints](#53-generator-profile-constraints)
   - [5.4 Demand Constraints](#54-demand-constraints)
   - [5.4 bis Demand Profile Constraints](#54-bis-demand-profile-constraints)
   - [5.5 Transmission Line Constraints](#55-transmission-line-constraints)
   - [5.6 Kirchhoff Voltage Law (DC OPF)](#56-kirchhoff-voltage-law-dc-opf)
   - [5.7 Battery / Energy Storage Constraints](#57-battery--energy-storage-constraints)
   - [5.8 Converter Constraints](#58-converter-constraints)
   - [5.9 Reserve Constraints](#59-reserve-constraints)
   - [5.10 Hydro Cascade Constraints](#510-hydro-cascade-constraints)
   - [5.10 bis Hydro Pump](#510-bis-hydro-pump)
   - [5.10 ter Reservoir Discharge Limit](#510-ter-reservoir-discharge-limit)
   - [5.11 Capacity Expansion Constraints](#511-capacity-expansion-constraints)
   - [5.12 User Constraints](#512-user-constraints)
   - [5.13 Water Rights (FlowRight and VolumeRight)](#513-water-rights-flowright-and-volumeright)
   - [5.14 LNG Terminal](#514-lng-terminal)
   - [5.14 bis Emission Zone, Cap, and Allowance-Pool Banking](#514-bis-emission-zone-cap-and-allowance-pool-banking)
   - [5.15 Unit Commitment (3-bin)](#515-unit-commitment-3-bin)
   - [5.16 Simple Commitment](#516-simple-commitment)
   - [5.17 Inertia Zone and Provision](#517-inertia-zone-and-provision)
   - [5.18 Hydrogen Carrier (Electrolyser, Node, Offtake)](#518-hydrogen-carrier-electrolyser-node-offtake)
   - [5.19 Gas Network (Nodes, Pipes, Compressors, Gas Turbines)](#519-gas-network-nodes-pipes-compressors-gas-turbines)
6. [Scaling and Solver Options](#6-scaling-and-solver-options)
   - [6.1 Objective Scaling](#61-objective-scaling)
   - [6.2 Voltage Angle Scaling](#62-voltage-angle-scaling)
   - [6.3 Variable Scaling](#63-variable-scaling)
   - [6.4 Key Options Affecting the Formulation](#64-key-options-affecting-the-formulation)
   - [6.5 Modeling Modes Summary](#65-modeling-modes-summary)
   - [6.6 Stochastic Dual Dynamic Programming (SDDP)](#66-stochastic-dual-dynamic-programming-sddp)
   - [6.7 Apertures — Backward-Pass Scenario Sampling](#67-apertures--backward-pass-scenario-sampling)
   - [6.8 SDDP Benders Cuts](#68-sddp-benders-cuts)
   - [6.9 Boundary Cuts and Future Cost](#69-boundary-cuts-and-future-cost)
   - [6.10 Hot Start](#610-hot-start)
   - [6.11 Solver Timeouts](#611-solver-timeouts)
   - [6.12 Monolithic vs SDDP Equivalence](#612-monolithic-vs-sddp-equivalence)
   - [6.13 Elastic Filter and Feasibility](#613-elastic-filter-and-feasibility)
   - [6.14 Cascade Solver — Multi-Level Decomposition](#614-cascade-solver--multi-level-decomposition)
7. [Mapping: JSON Fields → Mathematical Symbols](#7-mapping-json-fields--mathematical-symbols)
8. [Cross-References](#8-cross-references)
9. [References](#9-references)

---

## 1. Overview

The GTEP problem finds the minimum-cost combination of:

- **Operational decisions (OPEX)** — how much each generator produces in every
  time block, which loads to curtail if capacity is insufficient, and how much
  power flows on each transmission line.
- **Investment decisions (CAPEX)** — how many capacity expansion modules to
  build for generators, demands, lines, and batteries across multi-year
  planning stages.

This is a classical problem in power system planning, extensively studied in
the literature [[1]](#ref1) [[2]](#ref2) [[3]](#ref3) [[16]](#ref16). The GTOpt formulation
builds on the FESOP framework, presented in "Optimal Expansion and
Reliable Renewable Energy Integration in Long-Term Planning Using FESOP"
[[4]](#ref4), which extended traditional long-term operational planning
(as implemented in PLP-type hydrothermal coordination tools [[5]](#ref5)
[[6]](#ref6)) to include capacity expansion, variable renewable energy
representation, and modern flexibility mechanisms such as battery storage
and spinning reserve constraints.

The problem is formulated as a **sparse linear program** (LP) over a
three-level time hierarchy:

```math
\text{Scenario} \;\supset\; \text{Stage} \;\supset\; \text{Block}
```

- **Block** $b \in \mathcal{B}$ — smallest time unit (typically 1 hour) with
  duration $\Delta_b$ in hours.
- **Stage** $t \in \mathcal{T}$ — investment period grouping consecutive
  blocks. Capacity decisions at stage $t$ persist to subsequent stages.
- **Scenario** $s \in \mathcal{S}$ — a possible future realization (e.g.,
  hydrology, demand level) weighted by probability $\pi_s$.

The solver minimizes the total **expected discounted cost** across all
scenarios, stages, and blocks.

---

## 2. Notation and Sets

### Sets

| Symbol | Description |
|--------|-------------|
| $\mathcal{S}$ | Set of scenarios, indexed by $s$ |
| $\mathcal{T}$ | Set of stages (investment periods), indexed by $t$ |
| $\mathcal{B}_t$ | Set of blocks in stage $t$, indexed by $b$ |
| $\mathcal{N}$ | Set of buses (electrical nodes), indexed by $n$ |
| $\mathcal{G}$ | Set of generators, indexed by $g$ |
| $\mathcal{D}$ | Set of demands (loads), indexed by $d$ |
| $\mathcal{L}$ | Set of transmission lines, indexed by $l$ |
| $\mathcal{E}$ | Set of batteries (energy storage), indexed by $e$ |
| $\mathcal{V}$ | Set of converters, indexed by $v$ |
| $\mathcal{Z}$ | Set of reserve zones, indexed by $z$ |
| $\mathcal{P}$ | Set of reserve provisions, indexed by $p$ |
| $\mathcal{J}$ | Set of junctions (hydraulic nodes), indexed by $j$ |
| $\mathcal{W}$ | Set of waterways, indexed by $w$ |
| $\mathcal{R}$ | Set of reservoirs, indexed by $r$ |
| $\mathcal{U}$ | Set of turbines, indexed by $u$ |
| $\mathcal{F}$ | Set of fixed flows (inflows/releases), indexed by $f$ |
| $\mathcal{I}$ | Set of filtrations (seepage), indexed by $i$ |
| $\mathcal{H}$ | Set of hydro pumps, indexed by $h$ |
| $\mathcal{DL}$ | Set of reservoir discharge-limit elements, indexed by $\ell$ |
| $\mathcal{FR}$ | Set of flow rights (flow-based water rights), indexed by $i$ |
| $\mathcal{VR}$ | Set of volume rights (volume-based water rights), indexed by $o$ |
| $\mathcal{LNG}$ | Set of LNG terminals, indexed by $\tau$ |
| $\mathcal{UC}$ | Set of generators with 3-bin unit commitment, indexed by $g$ |
| $\mathcal{SC}$ | Set of generators with simple commitment, indexed by $g$ |
| $\mathcal{IZ}$ | Set of inertia zones, indexed by $z$ |
| $\mathcal{IP}$ | Set of inertia provisions, indexed by $p$ |
| $\mathcal{EM}$ | Set of pollutants (`Emission`), indexed by $\epsilon$ |
| $\mathcal{EZ}$ | Set of emission zones, indexed by $z$ |
| $\mathcal{ES}$ | Set of emission sources (generator → zone → pollutant), indexed by $\nu$ |
| $\mathcal{G}_n$ | Generators connected to bus $n$ |
| $\mathcal{D}_n$ | Demands connected to bus $n$ |
| $\mathcal{L}_n^+$ | Lines where bus $n$ is the receiving end |
| $\mathcal{L}_n^-$ | Lines where bus $n$ is the sending end |

### Parameters

| Symbol | JSON field | Description | Unit |
|--------|-----------|-------------|------|
| $\pi_s$ | `probability_factor` | Scenario probability weight | — |
| $\delta_t$ | `discount_factor` | Stage discount factor | — |
| $\Delta_b$ | `duration` | Block duration | hours |
| $r$ | `annual_discount_rate` | Annual discount rate | — |
| $\sigma$ | `scale_objective` | Objective scaling divisor (default 1000) | — |
| $\sigma_\theta$ | `scale_theta` | Voltage angle enlargement, $\theta^{\text{LP}} = \sigma_\theta \theta$ (auto $1/\operatorname{median}(X/V^2)$; 1 = rad, 1000 = mrad) | — |
| $c_g$ | `gcost` | Generator variable cost | \$/MWh |
| $c_d^{\text{fail}}$ | `demand_fail_cost` | Load curtailment penalty | \$/MWh |
| $c_l$ | `tcost` | Line transfer cost | \$/MWh |
| $\overline{P}_g$ | `pmax` / `capacity` | Generator max output | MW |
| $\underline{P}_g$ | `pmin` | Generator min output | MW |
| $\overline{L}_d$ | `lmax` | Maximum demand (load) | MW |
| $\overline{F}_l^{ab}$ | `tmax_ab` | Line capacity $a \to b$ | MW |
| $\overline{F}_l^{ba}$ | `tmax_ba` | Line capacity $b \to a$ | MW |
| $X_l$ | `reactance` | Line reactance | Ω (p.u. when $V = 1$) |
| $\tau_l$ | `tap_ratio` | Off-nominal tap ratio (transformer) | p.u. |
| $\varphi_l$ | `phase_shift_deg` | Phase-shift angle (PST) | degrees |
| $\lambda_g$ | `lossfactor` (gen) | Generator injection loss fraction | — |
| $\lambda_d$ | `lossfactor` (demand) | Demand withdrawal loss fraction | — |
| $\lambda_l$ | `lossfactor` (line) | Line transmission loss fraction | — |
| $\phi_g(b)$ | `generator_profile` | Generator capacity profile factor | — |
| $\eta_e^{\text{in}}$ | `input_efficiency` | Battery charge efficiency | — |
| $\eta_e^{\text{out}}$ | `output_efficiency` | Battery discharge efficiency | — |
| $\mu_e$ | `annual_standing_loss` | Battery annual self-discharge rate (default 0.48 = 4 %/month) | 1/year |
| $\overline{E}_e$ | `emax` | Battery max energy | MWh |
| $\underline{E}_e$ | `emin` | Battery min energy | MWh |
| $E_e^0$ | `eini` | Battery initial energy | MWh |
| $E_e^{\text{fin}}$ | `efin` | Battery final energy | MWh |
| $\rho_v$ | `conversion_rate` | Converter power conversion rate | — |
| $\overline{Q}_w$ | `fmax` | Waterway max flow | m³/s |
| $\underline{Q}_w$ | `fmin` | Waterway min flow | m³/s |
| $\overline{V}_r$ | `emax` | Reservoir max volume | hm³ |
| $\underline{V}_r$ | `emin` | Reservoir min volume | hm³ |
| $\kappa_u$ | `conversion_rate` (turbine) | Turbine water-to-power factor | MW/(m³/s) |
| $\overline{R}_{z,t,b}^{\text{up}}$ | `urreq` | Up-reserve requirement | MW |
| $\overline{R}_{z,t,b}^{\text{dn}}$ | `drreq` | Down-reserve requirement | MW |
| $K_g^{\text{cap}}$ | `annual_capcost` | Annual expansion cost per module | \$/year |
| $M_g$ | `expcap` | Capacity per expansion module | MW |
| $\overline{m}_g$ | `expmod` | Maximum expansion modules | — |
| $\bar{C}_g^0$ | `capacity` | Initial installed capacity | MW |
| $\phi_h$ | `pump_factor` | Pump MW consumed per m³/s lifted | MW/(m³/s) |
| $\eta_h$ | `efficiency` | Pump efficiency | — |
| $\overline{Q}_h$ | `capacity` (pump) | Pump waterway capacity | m³/s |
| $(V_k^\ell, m_k^\ell, c_k^\ell)$ | `segments` (discharge limit) | Piecewise breakpoint $k$ of reservoir discharge limit | (hm³, m³/s/hm³, m³/s) |
| $\bar{\xi}_{i,s,t,b}$ | `discharge` (flow right) | Scheduled flow-right discharge | m³/s |
| $\overline{F}_i$ | `fmax` (flow right) | Flow-right maximum flow (variable mode) | m³/s |
| $c_i^f$ | `fail_cost` (flow right) | Flow-right deficit penalty | \$/(m³/s) |
| $c^{hf}$ | `hydro_fail_cost` | Global hydro fail cost | \$/m³ |
| $\overline{F}_o$ | `fmax` (volume right) | Volume-right per-block extraction cap | m³/s |
| $D_o$ | `demand` (volume right) | Stage demand target | m³ |
| $c_o^f$ | `fail_cost` (volume right) | Volume-right deficit penalty | \$/m³ |
| $\alpha_o$ | `flow_conversion_rate` | Flow-to-volume conversion (= $0.0036$) | hm³/((m³/s)·h) |
| $\rho_\tau$ | `fcr` (LNG) | Flow conversion rate (tank volume) | — |
| $\lambda_\tau$ | `annual_standing_loss` (LNG) | Boil-off rate | 1/year |
| $\eta_g^{hr}$ | `heat_rate` (LNG link) | Fuel consumption per MWh (m³\_LNG/MWh); note: symbol uses η for historical reasons but this is a consumption rate, not efficiency | m³/MWh |
| $\text{SU}_g$ | `startup_cost` | Startup cost | \$ |
| $\text{SD}_g$ | `shutdown_cost` | Shutdown cost | \$ |
| $\text{NL}_g$ | `noload_cost` | No-load cost (paid while $u=1$) | \$/h |
| $R_g^{\text{up}}$ | `ramp_up` | Hourly ramp-up rate | MW/h |
| $R_g^{\text{dn}}$ | `ramp_down` | Hourly ramp-down rate | MW/h |
| $\text{SU}_g^{\text{ramp}}$ | `startup_ramp` | Ramp allowance at startup | MW |
| $\text{SD}_g^{\text{ramp}}$ | `shutdown_ramp` | Ramp allowance at shutdown | MW |
| $T_g^{\text{up}}$ | `min_up_time` | Minimum up time | h |
| $T_g^{\text{dn}}$ | `min_down_time` | Minimum down time | h |
| $\underline{P}_g^d$ | `dispatch_pmin` | Deprecated SimpleCommitment floor (read as `Commitment.pmin`) | MW |
| $H_g$ | `inertia_constant` | Synchronous-machine inertia constant | s |
| $S_g$ | `rated_power` | Rated apparent power | MVA |
| $\Phi_p$ | `provision_factor` | Inertia conversion factor (MWs delivered per MW provided) | s |
| $\overline{R}_{z,t,b}^{H}$ | `requirement` (inertia) | Zone inertia requirement | MWs |

### Decision Variables

| Symbol | Description | Bounds |
|--------|-------------|--------|
| $p_{g,s,t,b}$ | Generator $g$ output | $`[\underline{P}_g,\; \overline{P}_g]`$ |
| $\ell_{d,s,t,b}$ | Served load at demand $d$ | $`[0,\; \overline{L}_d]`$ |
| $q_{d,s,t,b}$ | Unserved load (curtailment) at demand $d$ | $\geq 0$ |
| $f_{l,s,t,b}^+$ | Line $l$ forward power flow ($a \to b$) | $`[0,\; \overline{F}_l^{ab}]`$ |
| $f_{l,s,t,b}^-$ | Line $l$ reverse power flow ($b \to a$) | $`[0,\; \overline{F}_l^{ba}]`$ |
| $\theta_{n,s,t,b}$ | Voltage angle at bus $n$ (Kirchhoff mode, LP units $\sigma_\theta \cdot$ rad) | $`[-\theta_{\max} \sigma_\theta,\; \theta_{\max} \sigma_\theta]`$ |
| $e_{e,s,t,b}$ | Battery $e$ energy (state of charge) | $`[\underline{E}_e,\; \overline{E}_e]`$ |
| $p_{e,s,t,b}^{\text{in}}$ | Battery $e$ charging power | $\geq 0$ |
| $p_{e,s,t,b}^{\text{out}}$ | Battery $e$ discharging power | $\geq 0$ |
| $\bar{C}_{g,t}$ | Installed capacity of generator $g$ at stage $t$ | $`[\bar{C}_g^0,\; \bar{C}_g^{\max}]`$ |
| $m_{g,t}$ | Expansion modules built at stage $t$ | $`[0,\; \overline{m}_g]`$ |
| $r_{p,s,t,b}^{\text{up}}$ | Up-reserve provision $p$ | $\geq 0$ |
| $r_{p,s,t,b}^{\text{dn}}$ | Down-reserve provision $p$ | $\geq 0$ |
| $q_z^{\text{up}}$ | Unserved up-reserve at zone $z$ | $\geq 0$ |
| $q_z^{\text{dn}}$ | Unserved down-reserve at zone $z$ | $\geq 0$ |
| $\varphi_{w,s,t,b}$ | Waterway $w$ water flow | $`[\underline{Q}_w,\; \overline{Q}_w]`$ |
| $v_{r,s,t,b}$ | Reservoir $r$ volume | $`[\underline{V}_r,\; \overline{V}_r]`$ |
| $\text{spill}_{r,s,t,b}$ | Reservoir $r$ spillway discharge | $\geq 0$ |
| $\ell_{d,s,t,b}^{\text{man}}$ | Mandatory served load at demand $d$ (min-energy) | $`[0,\; E_d^{\min}/\Delta_b]`$ |
| $\text{spill}_{g,s,t,b}$ | Generator $g$ profile curtailment | $\geq 0$ |
| $q_{d,s,t,b}^{\text{prof}}$ | Demand-profile unserved (spillover) slack | $\geq 0$ |
| $q^{\ell}_{s,t}$ | Reservoir-discharge-limit stage-average flow (`qeh`) | free |
| $\xi_{i,s,t,b}$ | FlowRight extraction flow | $`[\underline{\xi}_i,\; \overline{\xi}_i]`$ |
| $\delta_{i,s,t,b}$ | FlowRight per-block deficit | $\geq 0$ |
| $\bar{\xi}_{i,s,t}^{h}$ | FlowRight stage-average hourly flow (`qeh`) | free |
| $\xi_{o,s,t,b}^{\text{in}}$ | VolumeRight extraction rate | $`[0,\; \overline{F}_o]`$ |
| $\xi_{o,s,t,b}^{\text{sav}}$ | VolumeRight saving (deposit) rate | $\geq 0$ |
| $\nu_{o,s,t,b}$ | VolumeRight remaining rights volume (state) | $`[0,\; \overline{V}_o]`$ |
| $q_{o,s,t}^{\text{fail}}$ | VolumeRight demand deficit | $\geq 0$ |
| $e\_{\tau,s,t,b}^{\text{lng}}$ | LNG tank volume | $`[\underline{V}_\tau,\; \overline{V}_\tau]`$ |
| $d_{\tau,s,t,b}^{\text{lng}}$ | LNG delivery (inflow to tank) | $\geq 0$ |
| $u_{g,s,t,p}$ | UC binary status of generator $g$ over period $p$ | $\lbrace 0,1\rbrace$ |
| $v_{g,s,t,p}$ | UC binary startup of generator $g$ | $\lbrace 0,1\rbrace$ |
| $w_{g,s,t,p}$ | UC binary shutdown of generator $g$ | $\lbrace 0,1\rbrace$ |
| $u_{g,s,t,b}^{\text{sc}}$ | Simple-commitment binary status | $\lbrace 0,1\rbrace$ |
| $r_{p,s,t,b}^{H}$ | Inertia provision (MW dedicated for inertia) | $`[0,\; \overline{R}_p^{H}]`$ |
| $q_{z,s,t,b}^{H}$ | Inertia-zone requirement slack | $`[0,\; \overline{R}_{z,t,b}^{H}]`$ |

> **Note on line flows**: When `use_line_losses` is enabled and
> $\lambda_l > 0$, the solver creates separate forward ($f^+$) and reverse
> ($f^-$) flow variables. When $\lambda_l = 0$, a single bidirectional
> variable $`f_l \in [-\overline{F}_l^{ba},\; \overline{F}_l^{ab}]`$ is used.

---

## 3. Compact Formulation

The following compact formulation follows the standard GTEP LP structure
[[1]](#ref1) [[3]](#ref3) [[10]](#ref10), extended with the FESOP
enhancements for renewable integration and storage [[4]](#ref4):

```math
\min_{p, \ell, q, f, \theta, e, \bar{C}, m, \ldots}
\quad
\underbrace{
\sum_{s \in \mathcal{S}} \sum_{t \in \mathcal{T}} \sum_{b \in \mathcal{B}_t}
\omega_{s,t,b}
\left[
  \sum_{g} c_g \, p_{g,s,t,b} +
  \sum_{d} c_d^{\text{fail}} q_{d,s,t,b} +
  \sum_{l} c_l \left( f_{l,s,t,b}^+ + f_{l,s,t,b}^- \right)
\right]
}_{\text{OPEX}}
\;+\;
\underbrace{
\sum_{g} \sum_{t} \omega_t^{\text{cap}} \, K_g^{\text{cap}} \, m_{g,t}
}_{\text{CAPEX}}
```

where the combined weighting factor is:

```math
\omega_{s,t,b} \;=\; \frac{\pi_s \cdot \delta_t \cdot \Delta_b}{\sigma}
```

subject to:

| # | Constraint | $\forall$ |
|---|-----------|-----------|
| (C1) | Bus power balance | $n, s, t, b$ |
| (C2) | Kirchhoff voltage law | $l, s, t, b$ |
| (C3) | Generator output bounds | $g, s, t, b$ |
| (C4) | Generator capacity expansion | $g, t$ |
| (C5) | Demand balance | $d, s, t, b$ |
| (C6) | Line capacity | $l, s, t, b$ |
| (C7) | Battery state-of-charge | $e, s, t, b$ |
| (C8) | Reserve requirements | $z, s, t, b$ |
| (C9) | Reserve–generator coupling | $p, s, t, b$ |
| (C10) | Converter coupling | $v, s, t, b$ |
| (C11) | Junction water balance | $j, s, t, b$ |
| (C12) | Reservoir volume balance | $r, s, t, b$ |
| (C13) | Turbine power conversion | $u, s, t, b$ |
| (C14) | User constraints (optional) | varies |

---

## 4. Objective Function

The objective minimizes the total expected discounted system cost.

### 4.1 Cost Weighting

Every operational cost coefficient is multiplied by the universal weighting
factor:

$$
\omega_{s,t,b} = \frac{\pi_s \cdot \delta_t \cdot \Delta_b}{\sigma}
$$

| Factor | Formula | Description |
|--------|---------|-------------|
| $\pi_s$ | `scenario.probability_factor` | Scenario probability (sum to 1) |
| $\delta_t$ | $(1 + r)^{-\tau_t / 8760}$ | Continuous discounting from stage start time $\tau_t$ (hours) |
| $\Delta_b$ | `block.duration` | Block duration in hours |
| $\sigma$ | `scale_objective` (default 1000) | Numerical scaling divisor |

The discount factor uses continuous compounding based on the stage start
time $\tau_t$ measured in hours from the planning horizon origin:

$$
\delta_t = (1 + r)^{-\tau_t / 8760} \cdot \delta_t^{\text{user}}
$$

where $\delta_t^{\text{user}}$ is an optional user-supplied per-stage
discount factor (default 1.0).

### 4.2 Operational Costs (OPEX)

```math
z_{\text{OPEX}} = \sum_{s \in \mathcal{S}} \sum_{t \in \mathcal{T}}
\sum_{b \in \mathcal{B}_t} \omega_{s,t,b}
\Bigg[
  \underbrace{\sum_{g \in \mathcal{G}} c_{g,t} \; p_{g,s,t,b}}_{\text{Generation cost}} +
  \underbrace{\sum_{d \in \mathcal{D}} c_{d,t}^{\text{fail}} \; q_{d,s,t,b}}_{\text{Curtailment cost}} +
  \underbrace{\sum_{l \in \mathcal{L}} c_{l,t} \left( f_{l,s,t,b}^{+} + f_{l,s,t,b}^{-} \right)}_{\text{Transfer cost}}
\Bigg]
```

Additional OPEX terms may include:

<!-- markdownlint-disable MD013 -->
- **Reserve failure cost**:
  $\sum_{z} c_z^{\text{rfail}} (q_{z,s,t,b}^{\text{up}} + q_{z,s,t,b}^{\text{dn}})$
- **Reserve provision cost**:
  $`\sum_{p} \bigl( c_{p,t}^{\text{ur}} \; r_{p,s,t,b}^{\text{up}} + c_{p,t}^{\text{dr}} \; r_{p,s,t,b}^{\text{dn}} \bigr)`$
- **Spillway cost**: $`\sum_{r} c_{r,t}^{\text{spill}} \; \text{spill}_{r,s,t,b}`$
- **Junction drain cost**: $`\sum_{j} c_{j}^{\text{drain}} \; \delta_{j,s,t,b}`$
  (§5.10)
- **Emission price and CCS cost**:
  $`\sum_{\nu} c^{\text{em}}_{z(\nu),t} \, (\alpha^{\text{em}}_{\nu,t,b} / \Delta_b) \, p_{g(\nu),s,t,b}`$
  plus capture cost (§5.14 bis); soft emission-cap slack
  $`c^{\text{cap}}_{z,t} \, s^{\text{cap}}_{z,s,t}`$ is weighted by
  $\pi_s \delta_t / \sigma$ only
- **Generator profile spillover cost**: $`\sum_{g} c_{g,t}^{\text{spill}} \; \text{spill}_{g,s,t,b}`$

<!-- markdownlint-enable MD013 -->

### 4.3 Investment Costs (CAPEX)

```math
z_{\text{CAPEX}} = \sum_{g} \sum_{t \in \mathcal{T}}
\omega_t^{\text{cap}} \; K_g^{\text{cap}} \; m_{g,t}
```

where the investment cost weight is:

$$
\omega_t^{\text{cap}} = \frac{\delta_t \cdot T_t}{\sigma}
$$

with $T_t$ the stage duration in hours and the annualized cost converted to
an hourly rate:

$$
K_g^{\text{hour}} = \frac{K_g^{\text{cap}}}{8760}
$$

The same expansion structure applies to demands, lines, batteries, and
converters. The CAPEX term generalizes to all expandable components.

### 4.4 Complete Objective

$$
\min \quad z = z_{\text{OPEX}} + z_{\text{CAPEX}}
$$

---

## 5. Constraints by Component

### 5.1 Bus Power Balance

The power balance at each bus $n$ ensures that total injection equals total
withdrawal (Kirchhoff's Current Law) [[11]](#ref11):

```math
\sum_{g \in \mathcal{G}_n} (1 - \lambda_g) \; p_{g,s,t,b}
\;-\; \sum_{d \in \mathcal{D}_n} (1 + \lambda_d) \; \ell_{d,s,t,b}
\;+\; \sum_{l \in \mathcal{L}_n^+} f_{l,s,t,b}^{\text{net,in}}
\;-\; \sum_{l \in \mathcal{L}_n^-} f_{l,s,t,b}^{\text{net,out}}
\;=\; 0
\qquad \forall \; n, s, t, b
```

where:
- $(1 - \lambda_g)$ accounts for generator injection losses
- $(1 + \lambda_d)$ accounts for demand-side losses (load + losses must be served)
- The line flow contributions depend on loss modeling (see
  [Section 5.5](#55-transmission-line-constraints))

The **dual variable** of this constraint is the **Locational Marginal Price
(LMP)** at bus $n$, reported as `balance_dual` in the output.

### 5.2 Generator Constraints

#### Output Bounds

```math
\underline{P}_{g,t,b} \;\leq\; p_{g,s,t,b} \;\leq\; \overline{P}_{g,t,b}
\qquad \forall \; g, s, t, b
```

The effective bounds at each block depend on stage-specific and
block-specific parameters:

```math
\overline{P}_{g,t,b} = \min\bigl(\overline{P}_{g,t}(b),\; \bar{C}_{g,t}\bigr)
\qquad
\underline{P}_{g,t,b} = \max\bigl(\underline{P}_{g,t}(b),\; 0\bigr)
```

where $\overline{P}\_{g,t}(b)$ and $\underline{P}\_{g,t}(b)$ are the
block-level schedule values of `pmax` and `pmin` respectively, and
$\bar{C}\_{g,t}$ is the installed capacity at stage $t$.

#### Cost Coefficient

The LP column cost for $p_{g,s,t,b}$ is:

$$
\text{cost}(p_{g,s,t,b}) = c_{g,t} \cdot \omega_{s,t,b}
= \frac{c_{g,t} \cdot \pi_s \cdot \delta_t \cdot \Delta_b}{\sigma}
$$

#### Capacity Linking

When capacity expansion is enabled for generator $g$:

```math
p_{g,s,t,b} \;\leq\; \bar{C}_{g,t}
\qquad \forall \; s, t, b
```

This constraint links the operational variable to the investment variable
(see [Section 5.11](#511-capacity-expansion-constraints)).

### 5.3 Generator Profile Constraints

When a generator profile $\phi_g(s,t,b)$ is defined (e.g., for solar or
wind plants), the profile bounds the output at each block.  The profile is
a **per-unit factor of the capacity** $\bar{C}_{g,t}$, not MW.  The
available energy is capped by the generation range, so curtailment can
never exceed what the unit could physically produce:

```math
A_{g,s,t,b} = \min\!\left(\phi_g(s,t,b)\,\bar{C}_{g,t},\; \bar{P}_{g,t,b}\right)
```

where $\bar{P}_{g,t,b}$ is the upper bound of the generation column (block
`pmax`, already capped by a fixed capacity).

**Slack-free form** (default: no spillover cost, $c_g^{\text{spill}} = 0$).
No spillover column is created; the profile row is an inequality:

```math
p_{g,s,t,b} \;\le\; A_{g,s,t,b} \qquad \forall \; s, t, b
```

With an expansion (capacity) column the row reads
$`p_{g,s,t,b} - \min(\phi_g, 1)\,C_{g,t} \le 0`$: a profile above 1 is clipped
(with a warning) so the unit never produces more than its built capacity.

Curtailment is reconstructed after the solve,
$`\text{spill}_{g,s,t,b} = \max(0,\, A_{g,s,t,b} - p_{g,s,t,b})`$ (with a
capacity column $`A = \min(\min(\phi_g,1)\,C^\star_{g,t}, \bar{P}_{g,t,b})`$),
and written to `GeneratorProfile/spillover_sol`.  `spillover_dual` is the
dual of the $\le$ row (same sign convention as the equality row it
replaces: $\partial z / \partial A \le 0$), and `spillover_cost` is the
reduced cost the zero-cost slack would have had, $-\text{dual}$.

**Slack form** (when $c_g^{\text{spill}} \neq 0$ in the stage).  The priced
spillover keeps its column:

$$
p_{g,s,t,b} + \text{spill}_{g,s,t,b} = A_{g,s,t,b},
\qquad 0 \le \text{spill}_{g,s,t,b} \le A_{g,s,t,b}
$$

(with a capacity column:
$`p + \text{spill} - \min(\phi_g,1)\,C_{g,t} = 0`$,
$`\text{spill} \le \min(\phi_g,1)\,\bar{C}^{\max}_{g,t}`$), costed at
$c_g^{\text{spill}} \cdot \omega\_{s,t,b}$.

> **Why slack-free.** The spillover column is a column singleton in an
> equality row; every presolve turns `p + s = A, s ≥ 0` into `p ≤ A`.  The
> slack-free form builds that reduced LP directly: one column fewer per
> profiled generator × block, identical optimum and identical row dual.
> A priced spillover (`scost ≠ 0`) would need the substitution
> `cost(spill) = c·A − c·p` folded into the generation cost; the column is
> kept in that (rare) case instead.
> <!-- source: include/gtopt/profile_object_lp.hpp (add_profile_to_lp) -->

### 5.4 Demand Constraints

#### Demand Balance

When curtailment is allowed (every demand not marked `forced`):

```math
\ell_{d,s,t,b} + q_{d,s,t,b} = \overline{L}_{d,t,b}
\qquad \forall \; d, s, t, b
```

For a hard demand (`forced = true`), the load is fixed:

```math
\ell_{d,s,t,b} = \overline{L}_{d,t,b}
\qquad \forall \; d, s, t, b
```

where $\overline{L}_{d,t,b}$ is the scheduled demand at stage $t$, block $b$,
incorporating any demand profile scaling.

#### Curtailment Cost

$$
\text{cost}(q_{d,s,t,b}) = c_{d,t}^{\text{fail}} \cdot \omega_{s,t,b}
$$

where $c_{d,t}^{\text{fail}}$ is the demand's `fcost`, falling back to the
global `demand_fail_cost` (default 1000 \$/MWh).

#### Bus Balance Contribution

Demand contributes to the bus balance with a loss factor:

$$
\text{bus balance coefficient of } \ell_{d,s,t,b}: \quad -(1 + \lambda_d)
$$

where $\lambda_d$ is the demand-side loss factor.

#### Stepwise Curtailment Cost (`fail_segments`)

A demand may carry a stepwise value-of-lost-load curve with $K$ segments
$`\{(f_{d,k},\, c_{d,k,t})\}_{k=1}^{K}`$ — `Demand.fail_segments`, or the
global default `model_options.demand_fail_segments` for demands without
`fcost` / `fail_segments` (typical sources: PLP `FALLA_<bus>_1..4`, SDDP
`deficit_segments`, CNE CFLD 0–5/5–10/10–20/>20 %, PLEXOS `VoLL` +
`Internal VoLL`/`Level`).  Segment $k$ covers $`f_{d,k}\,\overline{L}_{d,t,b}`$
MW of curtailment priced at $c\_{d,k,t}$ (per-stage schedule).  An open last
segment takes the residual $f\_{d,K} = 1 - \sum\_{k<K} f\_{d,k}$; when all
fractions are explicit and sum to less than one, the remainder
$\phi_d = 1 - \sum_k f\_{d,k}$ is **firm** (non-curtailable) load.

The served load is split into $K$ tranche variables with plain column
bounds — no extra rows:

```math
\ell_{d,s,t,b} = \sum_{k=1}^{K} \ell_{d,k,s,t,b}, \qquad
\underline{\ell}_{d,k,t,b} \;\le\; \ell_{d,k,s,t,b} \;\le\; u_{d,k,t,b},
\qquad u_{d,k,t,b} = f_{d,k}\,\overline{L}_{d,t,b}
\;\bigl(+\,\phi_d\,\overline{L}_{d,t,b}\ \text{for}\ k = k^\star\bigr)
```

where $k^\star$ is the most expensive segment, so
$\sum_k u\_{d,k,t,b} = \overline{L}\_{d,t,b}$.  The per-segment curtailment
is $q\_{d,k,s,t,b} = u\_{d,k,t,b} - \ell\_{d,k,s,t,b}$ and
$q\_{d,s,t,b} = \sum_k q\_{d,k,s,t,b}$.  Each tranche enters the bus balance
(§5.1) and the capacity row with the same coefficient as $\ell\_{d,s,t,b}$,
and its objective contribution is the per-tranche substitution of the
curtailment cost:

```math
\sum_{k=1}^{K} c_{d,k,t}\,\omega_{s,t,b}\, q_{d,k,s,t,b}
\;=\;
\underbrace{\sum_{k} c_{d,k,t}\,\omega_{s,t,b}\, u_{d,k,t,b}}_{\text{objective constant}}
\;-\; \sum_{k} c_{d,k,t}\,\omega_{s,t,b}\, \ell_{d,k,s,t,b} .
```

The served-load floor
$`F_{d,t,b} = \max\bigl(L^{\min}_{d,t,b},\ \phi_d\,\overline{L}_{d,t,b}\bigr)`$
is distributed greedily over the tranche lower bounds
$\underline{\ell}\_{d,k,t,b}$ in decreasing cost order.  With
non-decreasing costs $c\_{d,1,t} \le \dots \le c\_{d,K,t}$ (convex curve)
this is exact: the tranches are perfect substitutes in every row, so the
LP curtails the cheapest segment first and realises the stepwise curve
without ordering constraints or binaries.  Decreasing costs are accepted
with a validation warning (the LP still curtails cheapest-first).
$K = 1$ reproduces the single `load` column of the `fcost` formulation
exactly; $K$ segments add $K-1$ columns per block and no rows.
Precedence per demand: `forced` (no curtailment) > `Demand.fail_segments`
> `Demand.fcost` > `model_options.demand_fail_segments` >
`model_options.demand_fail_cost`.

#### Minimum Energy Constraint (Optional)

When a minimum energy requirement $E_d^{\min}$ is specified:

```math
\sum_{b \in \mathcal{B}_t} \ell_{d,s,t,b}^{\text{man}} \cdot \Delta_b \;\geq\; E_d^{\min}
\qquad \forall \; d, s, t
```

### 5.4 bis Demand Profile Constraints

When a `DemandProfile` element is attached to a demand $d$, the served load
is tied to a time-varying profile factor $\phi_d(s,t,b) \in [0,1]$ with an
auxiliary spillover (unserved) variable $q_{d,s,t,b}^{\text{prof}} \geq 0$.

This is the slack form of §5.3 (one LP column per block and one equality
row per block).  Unlike generator spillover, the unserved slack is always
kept (it carries the curtailment semantics and usually a cost) and the
available amount is **not** capped by the load limit `lmax`: profile
demand above `lmax` remains unserved.  The slack is bounded by
$`0 \le q^{\text{prof}} \le \phi_d\,\overline{L}_{d,t}`$ (no-capacity-column
form).  A profile value above 1 triggers the same per-unit warning.

#### Profile Row — With Capacity Column

When the parent `Demand` carries an explicit capacity column $\bar{C}_{d,t}$
(either a constant capacity or an expansion variable), the profile ties
served load plus spillover to the scaled capacity:

```math
\begin{aligned}
\ell_{d,s,t,b} \;+\; q_{d,s,t,b}^{\text{prof}} \;-\; \phi_d(s,t,b)\,\bar{C}_{d,t} \;=\; 0
\qquad \forall \; s,t,b
\end{aligned}
```
<!-- source: include/gtopt/profile_object_lp.hpp:118-123 -->

#### Profile Row — Without Capacity Column

When no capacity column exists, the profile row uses the stage capacity
$\overline{L}_{d,t}$ on the RHS:

```math
\begin{aligned}
\ell_{d,s,t,b} \;+\; q_{d,s,t,b}^{\text{prof}} \;=\; \phi_d(s,t,b) \cdot \overline{L}_{d,t}
\qquad \forall \; s,t,b
\end{aligned}
```
<!-- source: include/gtopt/profile_object_lp.hpp:125-126 -->

#### Spillover Cost

The spillover column carries the `scost` coefficient, weighted by the usual
block cost factor:

$$
\text{cost}(q_{d,s,t,b}^{\text{prof}}) = c_{d,t}^{\text{spill}} \cdot \omega_{s,t,b}
$$
<!-- source: include/gtopt/profile_object_lp.hpp:97-108 -->

> **Activation.** The `DemandProfile` element requires the parent `Demand`
> to define either a `capacity` value or a capacity expansion; otherwise
> the profile is rejected during LP assembly.
> <!-- source: source/demand_profile_lp.cpp:40-47 -->

### 5.5 Transmission Line Constraints

#### Without Losses ($\lambda_l = 0$)

A single bidirectional flow variable $f_l$ is created:

```math
-\overline{F}_l^{ba} \;\leq\; f_{l,s,t,b} \;\leq\; \overline{F}_l^{ab}
\qquad \forall \; l, s, t, b
```

Bus balance contributions (line from bus $a$ to bus $b$):

$$
\text{At bus } a: \quad -f_{l,s,t,b}
\qquad\qquad
\text{At bus } b: \quad +f_{l,s,t,b}
$$

#### With Losses ($\lambda_l > 0$)

Separate forward and reverse flow variables are created:

```math
0 \;\leq\; f_{l,s,t,b}^{+} \;\leq\; \overline{F}_l^{ab}
\qquad
0 \;\leq\; f_{l,s,t,b}^{-} \;\leq\; \overline{F}_l^{ba}
```

Bus balance contributions for forward flow ($a \to b$):

```math
\text{At bus } a: \quad -f_{l,s,t,b}^{+}
\qquad
\text{At bus } b: \quad +(1 - \lambda_l) \, f_{l,s,t,b}^{+}
```

Bus balance contributions for reverse flow ($b \to a$):

```math
\text{At bus } b: \quad -f_{l,s,t,b}^{-}
\qquad
\text{At bus } a: \quad +(1 - \lambda_l) \, f_{l,s,t,b}^{-}
```

Losses are allocated at the receiving bus: the power arriving is
$(1 - \lambda_l)$ times the power sent.

#### Transfer Cost

$$
\text{cost}(f_{l,s,t,b}^{+}) = \text{cost}(f_{l,s,t,b}^{-}) = c_{l,t} \cdot \omega_{s,t,b}
$$

#### Quadratic Losses via `tangent_signed_flow` (Coffrin Outer Approximation)

When `line_losses_mode = "tangent_signed_flow"`, the line uses a SIGNED
flow column $f_l \in [-\overline{F}_l^{ba}, +\overline{F}_l^{ab}]$ (one
column per line + block, sign carries direction) and approximates the
convex quadratic loss curve

$$ \ell(f) = c_l \cdot f^2, \qquad c_l = R_l / V_l^2 $$

with $K$ TANGENT inequalities forming a piecewise-affine LOWER envelope:

```math
\ell_{l,s,t,b} \;\geq\; 2 \, c_l \, f_l^{(k)} \, f_{l,s,t,b}
   \;-\; c_l \, (f_l^{(k)})^2, \qquad k = 1, \dots, K
```

at $K$ tangent points $f_l^{(k)} = \overline{F}_l \cdot (2k - K - 1) / K$
spread on $(-\overline{F}_l, +\overline{F}_l)$.

To bound $\ell$ from above (preventing the LP from inflating it in
arbitrage scenarios with negative LMPs), an auxiliary
$|f|$-envelope variable $v_l \geq |f_l|$ encodes the absolute value
via two inequalities

```math
v_{l,s,t,b} \;\geq\; +f_{l,s,t,b}, \qquad
   v_{l,s,t,b} \;\geq\; -f_{l,s,t,b}
```

and a single secant chord bounds the loss column

```math
\ell_{l,s,t,b} \;\leq\; (c_l \, \overline{F}_l) \cdot v_{l,s,t,b}.
```

#### Loss Pricing $\varepsilon$ and the Negative-LMP Arbitrage Guard

The chord above only *upper*-bounds $\ell$; it does not stop the LP
from parking value in the loss column when the receiving/sending bus
duals make loss-dumping profitable.  Whenever the bus-dual pair-sum
goes negative — KVL congestion or curtailment can produce this even
with all-positive generation costs — an idle line can ride $\ell$ up
its chord to $c_l \overline{F}_l^2$, a fictitious energy sink.  The
sole monetisation of that channel is the loss column itself, so a
per-MWh loss price $\varepsilon_l$ (JSON `loss_cost_eps`, per line or
via the `model_options` default, which is $0.1$ \$/MWh when unset;
an explicit `0` disables it) is stamped **on the loss column only**:

```math
\Delta\,\text{OPEX}_{l,s,t,b}
   \;=\; \varepsilon_l \cdot \omega_{s,t,b} \cdot \ell_{l,s,t,b}.
```

Because $v_l$ never enters a bus balance, pricing the loss column
alone closes both the idle-line sink and the chord residual.  Loss
dumping earns $|\pi_a + \pi_b| / 2$ per MWh, so the guard is sized

```math
\varepsilon_l \;\geq\; \tfrac{1}{2}\,
   \bigl| \text{worst credible } (\pi_a + \pi_b) \bigr|
   \quad (+\,\text{margin}),
```

after which the arbitrage is blocked.  Its incidence on legitimate
operation is *loss*-scaled (overhead $`\approx 2\lambda_l\,\varepsilon_l`$
per marginal MWh, $\lambda_l = R_l \overline{F}_l / V_l^2$ — a few
percent), **not** a per-MWh toll on flow.  Set $\varepsilon_l$ on
flagged corridors and re-run: the sink migrates to the next unguarded
lossy line, so iterate until no new flags.

> **$v$-pin (`loss_flow_abs_cost`).**  When $\varepsilon_l > 0$ the
> abs-flow proxy columns $v_l$ additionally carry a degeneracy pin
> $\varepsilon^{\text{pin}}\_l$ [\$/MWh, real cost units before
> `scale_objective`; JSON `loss_flow_abs_cost`, per line or via
> `model_options`, default $10^{-3}$].  It forces
> $\sum\_\ell v\_{l,\ell} = |f_l|$ at the optimum so the chord stays
> anchored to the piecewise secant.  **LMP effect:** with
> $\sum\_\ell v\_{l,\ell} = |f_l|$ the pin is a toll of
> $\varepsilon^{\text{pin}}\_l$ per MWh of flow in the flow direction,
> so the nodal-price difference across each lossy line shifts by at
> most $\approx \varepsilon^{\text{pin}}\_l$ (0.001 \$/MWh by default;
> a path through $n$ lossy lines by at most
> $`\approx n\,\varepsilon^{\text{pin}}`$).  The value must survive
> `scale_objective`: the former internal constant $10^{-6}$ \$/MWh
> landed at $10^{-9}$ in the LP, below every solver's dual tolerance
> (pin ineffective) and stretching the objective range to 9 decades;
> $10^{-3}$ lands at $10^{-6}$.  `loss_flow_abs_cost = 1e-6`
> reproduces the former LP exactly; `0` disables the pin.  The user
> $\varepsilon_l$ is **not** charged on $v_l$ —
> doing so would turn $\varepsilon_l$ into a transport toll on all
> legitimate flow.  The pin is gated on $\varepsilon_l > 0$, so an
> explicit $\varepsilon_l = 0$ reproduces the legacy pure-LP
> behaviour byte-for-byte.  `linear` and `piecewise_direct` have no loss column and
> cannot be $\varepsilon$-guarded; the exact fix there is the SOS2
> $\lambda$-form below.

#### L-Secant Chord with SOS2 Fill-Order (issue #504)

When `loss_secant_segments = L > 1` AND `loss_use_sos2 = true` the
single $v_l \in [0, \overline{F}\_l]$ auxiliary is replaced by **$L$
segment columns** $v\_{l,\ell} \in [0, w_l]$ with
$w_l = \overline{F}\_l / L$, tied to $|f_l|$ by sum-of-segments rows

```math
\sum_{\ell = 1}^{L} v_{l,\ell,s,t,b} \;\geq\; +f_{l,s,t,b},
   \qquad
   \sum_{\ell = 1}^{L} v_{l,\ell,s,t,b} \;\geq\; -f_{l,s,t,b}.
```

The upper bound on $\ell$ becomes piecewise:

```math
\ell_{l,s,t,b} \;\leq\;
   \sum_{\ell=1}^{L} \underbrace{c_l \, w_l \, (2\ell - 1)}_{\text{chord_slope}_\ell}
   \, v_{l,\ell,s,t,b}.
```

Each chord slope matches the secant of the convex quadratic on
$[(\ell-1) w_l, \ell w_l]$.  Without an ordering constraint the LP
exploits the segment freedom to maximise the upper bound (loose by
factor $L$); a **Type-2 Special-Ordered-Set** declaration

```math
\text{SOS2}\big( v_{l,1}, \, v_{l,2}, \, \ldots, \, v_{l,L} \big)
```

forces at most two consecutive $v_{l,\ell}$ to be non-zero, yielding
the fill-order $v_{l,1} \to v_{l,2} \to \cdots \to v_{l,L}$.  Under
SOS2 the piecewise chord is **tight at every breakpoint**
$b_l = \ell \cdot w_l$ and overestimates by at most
$c_l \cdot (w_l / 2)^2$ between breakpoints.  Worst-case overstatement
therefore decays as

```math
\mathrm{gap}_{\max}(L) \;=\; \frac{c_l \cdot \overline{F}_l^2}{4 L^2}
   \;=\; \mathcal{O}(1/L^2).
```

Doubling $L$ cuts the worst-case gap by 4.  This converts the line
flow + loss assembly from a pure LP into a MILP for the lines flagged
with `loss_use_sos2 = true`; the cost is paid only for the targeted
"offender" lines whose envelope-to-peak-flow ratio is large (see
issue #504 §"Targeted application").  Backend support: CPLEX (via
`CPXaddsos` with `CPX_TYPE_SOS2`), Gurobi (`GRBaddsos`), HiGHS ≥ 1.6
(`Highs_addSos`); CBC has no native SOS2 entry point and rejects the
configuration at LP-build time.

References: Beale & Tomlin 1970 (SOS2 piecewise-linear envelope);
Coffrin & Van Hentenryck 2014 (signed-flow outer approximation);
Aigner & Van Hentenryck 2022 arXiv:2112.10975.

#### Decoupled Loss Envelope (`loss_envelope`)

By default every PWL loss mode spreads its $K$ tangent points / secant
segments (and the $L$ chord segments of `tangent_signed_flow`) over
$[0, \overline{F}_l]$, the flow cap.  `Line.loss_envelope` $= E_l$
decouples the two: the fine resolution is placed on the realistic
loading range $[0, E_l]$ (typically the original rating of a line whose
cap was lifted, e.g. PLEXOS `Enforce Limits = 0` lines emitted with
$`\overline{F}_l = 10\,E_l`$), while the flow bound stays at
$\overline{F}_l$.  The envelope **never caps the flow**, and the loss
past it stays **quadratic** (PLEXOS keeps $\ell = c_l f^2$ above the
rating; a linear extrapolation on the last segment's slope would
under-state it by about $2\times$ at $3 E_l$).

When $\overline{F}_l > E_l$, the range $[E_l, \overline{F}_l]$ is
covered by a coarse geometric ladder

```math
b_j = E_l \, (\overline{F}_l / E_l)^{j/M}, \quad j = 0, \dots, M,
   \qquad M = \Bigl\lceil \frac{\ln(\overline{F}_l / E_l)}{\ln 1.4}
   \Bigr\rceil \;(\le 16),
```

so $b_j / b_{j-1} \le 1.4$.  Each emitter adds:

* **Tangent rows** (`tangent_signed_flow`, `piecewise` + `tangent`
  layout) at $\pm E_l$ and $\pm b_j$:
  $\ell \ge 2 c_l b_j f - c_l b_j^2$.
* **Secant segments** (`tangent_signed_flow` segment form, and the
  per-direction `bidirectional` / `piecewise` secant layouts):
  overflow columns $u_j \in [0, b_j - b_{j-1}]$ with chord/loss slope
  $c_l (b_{j-1} + b_j)$; in `tangent_signed_flow` they join the
  abs-rows $\sum v + \sum u \ge \pm f$, and in the per-direction
  model they join the link row while the $K$ inner segments keep their
  width cap.
* **SOS2 breakpoints** ($\lambda$-form): extra breakpoints $\pm b_j$
  with chord value $c_l b_j^2$.
* **Direct segments** (`piecewise_direct`): the $K$ segments cover
  $[0, E_l]$ and overflow segments $[b_{j-1}, b_j]$ with loss factor
  $c_l (b_{j-1} + b_j)$ cover the rest, so the widths sum to
  $\overline{F}_l$ and the flow cap is unchanged.

Between consecutive points $a < b$ both the tangent and the secant
error of $c_l f^2$ peak at $f = (a+b)/2$ with relative size
$`\bigl((b-a)/(b+a)\bigr)^2 \le (0.4/2.4)^2 \approx 2.8\,\%`$, so the
loss past the envelope is within $`\approx 2.8\,\%`$ of $c_l f^2$ from
both sides.  The construction is LP-valid: the slopes increase, so the
smallest chord value reachable for a given $|f|$ is the bottom-up
fill — the piecewise secant — which lies above $c_l f^2$ and therefore
above every tangent row; the upper (chord) and lower (tangent) bounds
never conflict for $|f| \le \overline{F}_l$.  The loss-column bound
uses $c_l \max(\overline{F}_l, E_l)^2$.  With no headroom
($\overline{F}_l \le E_l$) or `loss_envelope` unset, no overflow
rows/columns are emitted and the LP is identical to the
non-decoupled formulation.

#### Overflow-Tier Price (`loss_overflow_cost`)

The overflow tier is an energy sink unless it is priced.  Under a
negative bus price (a reserve requirement that falls with generation,
curtailment, or KVL congestion with all-positive costs) the LP inflates
the overflow columns past $|f|$ (or circulates $f^+ = f^-$ past $E_l$)
and rides the loss up to $c_l \overline{F}_l^2$ at **any** flow: a
$(\overline{F}_l / E_l)^2$-fold larger sink than the in-envelope
residual $c_l E_l^2$ (100× for the PLEXOS $10\times$ case).  No chord
can close it: any convex (pure-LP) set that contains the two exact
points $(\pm \overline{F}_l, c_l \overline{F}_l^2)$ also contains their
midpoint $(0, c_l \overline{F}_l^2)$.

gtopt therefore prices the tier.  With $\kappa_l$ the overflow price
[\$/MWh] (JSON `loss_overflow_cost`, per line or via `model_options`;
an explicit `0` disables it; unset, it defaults to the case's own global
`demand_fail_cost`, e.g. the PLEXOS VoLL 467.19 \$/MWh of CEN PCP cases,
itself $1000$ \$/MWh when unset, and falls back to $1000$ \$/MWh with a warning
when it is an explicit 0;
per-demand `fcost` overrides are not consulted, since the price belongs
to a line and the global value is the case's reference VoLL), every overflow
column carries the objective cost $\kappa_l$ times the loss it adds:

| Mode | Overflow column | Cost per unit |
|------|-----------------|---------------|
| `tangent_signed_flow` (segment form) | $u_j$ | $`\kappa_l\, c_l (b_{j-1} + b_j)`$ |
| `tangent_signed_flow` + SOS2 | $\lambda_j$ at $\pm b_j$ | $`\kappa_l\, c_l (b_j^2 - E_l^2)`$ |
| `bidirectional`, `piecewise` (secant layouts) | overflow segment $j$ | $`\kappa_l\, c_l (b_{j-1} + b_j)`$ |
| `piecewise_direct` | overflow segment $j$ | $`\kappa_l\, c_l (b_{j-1} + b_j)`$ (+ `tcost`) |
| `piecewise` + `tangent` layout | $\ell^{\text{ov}} \ge \ell - c_l E_l^2$ | $\kappa_l$ |

all weighted by $\omega_{s,t,b}$.  In every mode the tier then costs
exactly $\kappa_l$ per MWh of loss above $c_l E_l^2$:

* **Fictitious loss** through the tier costs $\kappa_l$ per MWh, so it
  is bought only when a bus incentive exceeds $\kappa_l$.  The sink cap
  returns to the in-envelope residual $c_l E_l^2$ (itself governed by
  $\varepsilon_l$ or SOS2, as before the ladder existed).  In the SOS2
  form the price also protects the LP relaxation.
* **Genuine overload** ($|f| > E_l$) pays
  $`\kappa_l\,\bigl(\text{chord}(|f|) - c_l E_l^2\bigr)`$: the flow stays
  feasible, the loss stays within $`\approx 2.8\,\%`$ of $c_l f^2$, and
  the marginal LMP spread across the line grows by
  $\kappa_l \cdot c_l (b_{j-1} + b_j) \approx 2\kappa_l c_l |f|$.  This
  distortion is unavoidable for any LP guard: by the midpoint argument
  the sink point costs the average of the two genuine extreme points,
  so blocking an incentive $I$ requires charging genuine overloads at
  least $I$ per MWh of loss above the envelope.  $\kappa_l$ trades the
  largest incentive blocked against that distortion; the default
  ($\kappa_l$ = the case's `demand_fail_cost`) makes burning a MWh in
  the tier never cheaper than shedding one.
* **No change** when $|f| \le E_l$ and no incentive exists: the tier is
  then idle (the rising prices also fix its bottom-up fill), so the
  optimum is the unguarded one.

The `midpoint` layout's de-biased loss row is two-sided,
$`-s\,(w/2)^2 c_l \le s\,\ell - \sum_k s\,\text{loss}_k\,\text{seg}_k \le 0`$:
the one-sided form left $\ell$ free up to its column bound (a
sink of $c_l \max(\overline{F}_l, E_l)^2$ per direction at any flow).
`linear` has no loss column and remains exposed to circulation
$f^+ = f^-$ up to $\overline{F}_l$; gtopt warns when a `linear` line
has $\overline{F}_l > E_l$.

#### Thermal Limit with Losses (`line_thermal_limit_mode`)

With losses the power that enters a line at its sending bus and the
power that leaves it at the receiving bus differ by the loss
$\ell_l^{dir}$.  The thermal rating $\overline{F}_l^{dir}$ (`tmax_ab` /
`tmax_ba`; on expandable lines also the installed capacity) is a
current limit, so physically it binds at the larger, sending end.  Which
quantity the LP limits is selected per line by
`line_thermal_limit_mode` (else `model_options.line_thermal_limit_mode`,
else `flow`):

| Mode | Constraint per direction | Notes |
|------|--------------------------|-------|
| `flow` (default) | $f_l^{dir} \le \overline{F}_l^{dir}$ | Column bound only; no extra row |
| `sending` | $`f_l^{dir} + a_l\,\ell_l^{dir} \le \overline{F}_l^{dir}`$ | $a_l$ = sending-bus loss share |
| `full` | $f_l^{dir} + \ell_l^{dir} \le \overline{F}_l^{dir}$ | Allocation independent |

$a_l$ is the share of the loss that `loss_allocation_mode` books at the
sending bus: $0$ for `receiver`, $\tfrac12$ for `split` (and always for
`tangent_signed_flow`), $1$ for `sender`.  Under `receiver` allocation
the flow variable already is the sending-end power, so `sending` adds
nothing and `flow` is the sending-end limit.  Under `split` the flow
variable is the midpoint flow, so `sending` limits both ends, which is
the two-end limit of MATPOWER / PowerModels [[34]](#ref34).  `full`
matches the PyPSA convention $|p| + \psi \le \overline{F}$
[[35]](#ref35).  It is conservative by $`(1-a_l)\,\ell`$ against the
sending end.

Per loss mode:

* **Per-direction loss columns** (`bidirectional`, `piecewise`): one
  row $`f^{\pm} + a\,\ell^{\pm} \le \overline{F}^{\pm}`$ per direction.
* **Shared-loss `piecewise` + `tangent` layout**: the single loss
  column $\ell$ serves both rows.
* **`tangent_signed_flow`**: two rows,
  $`+f + a\,\ell \le \overline{F}^{ab}`$ and
  $`-f + a\,\ell \le \overline{F}^{ba}`$, on the signed flow.
* **`piecewise_direct`**: one row per direction,
  $`\sum_k (1 + a\,\lambda_k)\,\text{seg}_k \le \overline{F}^{dir}`$.
* **`linear`**: the loss is $\lambda f$, so no row is needed.  The
  column bound becomes $f \le \overline{F}/(1 + a\lambda)$ and the
  capacity coefficient becomes $-(1 + a\lambda)$.
* **`none`**: unaffected.

On expandable lines the capacity rows carry the same loss term,
$`\bar{x}_l - f_l^{dir} - a\,\ell_l^{dir} \ge 0`$.  The rows are named
`thermalp` / `thermaln`.

The normal-rating band (`tmax_normal_*` with `overload_penalty`
$p^{ov}\_l$) uses the same loaded quantity.  With an overload slack
$`o_l^{dir} \in [0,\, \overline{o}_l^{dir}]`$ costing
$`p^{ov}_l\,\omega_{s,t,b}`$:

```math
f_l^{dir} + a_l\,\ell_l^{dir} - o_l^{dir} \;\le\; F^{n,dir}_l
```

The slack bound $\overline{o}$ follows `overload_slack_bound` (per line,
else `model_options`) with ratio $\rho$ = `max_overload_ratio`
(default 1.5):

- `thermal` (default):
  $`\overline{o} = \min(\overline{F} - F^{n},\;\rho\,F^{n})`$ — the conductor's
  thermal headroom (on DLR lines the
  IEEE 738 emergency rating at `tmax_conductor_emergency` minus the
  normal rating at `tmax_conductor`, so the band is exactly
  $[F^n, \overline{F}]$), never above $`\rho\,F^n`$; $`\rho\,F^n`$ alone when
  there is no finite hard cap;
- `ratio`: $`\overline{o} = \rho\,F^{n}`$;
- `unbounded`: no bound (the hard cap still applies through the flow
  bound / thermal rows).

The default $\rho = 1.5$ (flow $`\le 2.5\,F^n`$) is a guard rail above
the reported DLR gains: about 10–25 % over static ratings in the U.S.
DOE Smart Grid Demonstration projects [[39]](#ref39) (Oncor
[[40]](#ref40)), averages up to ~40 % at windy sites and outliers of
50–72 % [[41]](#ref41), while DLR can also fall below the static rating
30–40 % of the time [[42]](#ref42).  Emergency tiers in practice are
owner-defined (long-term emergency over 4–12 h, short-term emergency
over ~15 min) [[43]](#ref43), [[44]](#ref44).

When the slack sits at a finite bound that depends on the ratings
($\overline{o} = \overline{F} - F^n$ or $\rho F^n$), the reported span
duals add the slack's reduced cost $r$ times
$\partial\overline{o}/\partial T$ ($+r$ to the emergency span, $-r$ or $\rho r$
to the normal
span), so $\partial z/\partial T$ is well defined even at the degenerate
vertex where the flow equals the emergency rating.

The term $`a_l\,\ell_l^{dir}`$ is written per loss mode exactly as in the
thermal-limit rows ($\pm f$ for `tangent_signed_flow`,
$`\sum_k (1 + a\lambda_k)\,\text{seg}_k`$ for `piecewise_direct`,
$(1 + a\lambda) f$ for `linear`).  Under `flow` ($a_l = 0$) the band is
the historical $f^{\pm} - o \le F^n$, emitted only for modes with a
$f^{+}/f^{-}$ column.

**Duals.**  The output reports the shadow prices of the limit rows in
\$/MWh (back-scaled like every row dual): `Line/thermalp_dual`,
`Line/thermaln_dual` for the rows above and `Line/capacityp_dual`,
`Line/capacityn_dual` for the capacity rows of expandable lines.  Both
are $\le 0$ when binding, the same sign as the reduced cost of a flow
column at its upper bound.  For receiver allocation and `full`, a
congested line whose marginal loss slope at the operating point is
$\ell'$ has

```math
y^{th} \;=\; -\,\frac{\pi_b\,(1 - \ell') - \pi_a - \varepsilon_l\,\ell'}
   {1 + \ell'}
```

because relaxing $\overline{F}$ by 1 MW admits only $1/(1 + \ell')$ MW
more flow.  Under
`flow` the same rent appears as the reduced cost of $f^{dir}$ (`flow_cost`)
only where the flow column itself carries the limit (`none`, `linear`,
`tangent_signed_flow`).  In the segment-based PWL modes (`piecewise`,
`bidirectional`, `piecewise_direct`) the segment widths add up to
$\overline{F}_l^{dir}$, so the limit is implied by the segment bounds
and the rent is spread over segment reduced costs that are not written
out: `flow_cost` is empty for those lines.  Use `sending` / `full` when
the limit dual is needed, or recover it from the LMPs as
$\pi_b(1-(1-a)\ell') - \pi_a(1+a\ell')$ (see
`cases/line_limit_tolls`).  With split allocation the dual is
$V/(1 + a\ell')$ under `sending`, which equals
$`\pi_b\,r - \pi_a`$ with $r = (1-(1-a)\ell')/(1+a\ell')$ the
power delivered per MW injected at the sending end: the congestion part
of the price difference with the marginal loss referred to the sending
end [[37]](#ref37), independent of the allocation $a$.

**Analytic loss cap at the rated flow.**  Independently of the mode,
every loss column is bounded by the loss the line can carry at its
rated flow in that direction:

```math
\ell_l^{dir} \;\le\; \max\!\bigl(c_l (\overline{F}_l^{dir})^2,\;
   \mathrm{PWL}_l(\overline{F}_l^{dir})\bigr)
```

Here $\mathrm{PWL}_l$ is the model's own secant ladder with ordered
fill.  It is exact at the breakpoints.  When the flow cap is itself a
breakpoint it reduces to $c_l (\overline{F}_l^{dir})^2$.  Tangent
families use the quadratic, since all tangents lie below it.  Every loss
model is convex and non-decreasing in $|f|$, so the cap never cuts a
physically filled point with $|f| \le \overline{F}$.  What it does cut
is fictitious loss, meaning out-of-order segment fill or an inflated
loss variable under non-positive prices [[33]](#ref33).  Neumann et al.
use the same cap [[35]](#ref35).

**Ordered-fill segment bound.**  The PWL segment columns are flow
segments: segment $k$ on $[b_{k-1}, b_k]$ books the loss
$`c_l (b_{k-1} + b_k)\, s_k`$, so its loss is bounded through its flow
bound.  With `model_options.loss_segment_bound = true` (default) every
segment column — `flowp_seg` / `flown_seg` of the secant modes and the
$|f|$ segments $v_l$ of `tangent_signed_flow` — gets

```math
0 \;\le\; s_k \;\le\; \min\bigl(w_k,\;
   \max(0,\; \overline{F}_l^{dir} - b_{k-1})\bigr),
```

the most it carries in the ordered fill at $|f| \le \overline{F}$, so
segment $k$ books at most
$`c_l (b_{k-1} + b_k)\,\min(w_k, \overline{F} - b_{k-1})^{+}`$ and the segments
together at most the
ordered secant at $\overline{F}$ — the same value as the loss-column cap
above, $c_l \overline{F}^2$ when $\overline{F}$ is a breakpoint (for
`piecewise`, whose segments
carry $f^+ + f^-$, $\overline{F} = \max(\overline{F}^{ab}, \overline{F}^{ba})$).
It is a valid tightening bound: it removes no
physical point and only bites when the segments reach past the flow cap
(a `loss_envelope` above $\overline{F}$, where the last segment used to
be unbounded, or a two-direction circulation).  Like the loss cap it
limits non-physical loss inflation under negative prices; it does not
prevent it.  `loss_segment_bound = false` restores the width-only
bounds.

The cap is tighter than before when a decoupled `loss_envelope` lies
**above** the flow cap.  It was $c_l E_l^2$ and is now the secant value
at $\overline{F}$.  For `tangent_signed_flow` and the shared tangent
layout it is now $c_l \overline{F}^2$ instead of
$c_l \max(\overline{F}, E_l)^2$.  The cap alone does not remove
fictitious loss: below the rated flow only $\varepsilon_l$, the overflow
price or SOS2 do that.  With split allocation the relaxation is exact
only while $\tfrac12(\pi_a + \pi_b) > 0$ [[36]](#ref36).  `full` / `sending` add
a second bound that
binds once the line is loaded, $\ell \le (\overline{F} - f)/a$.

**Validation against literature.**  The three modes reproduce the
thermal-limit conventions of the reference tools:

| Mode | Reference convention |
|------|----------------------|
| `flow` | Lossless DC OPF (MATPOWER `rundcopf` [[45]](#ref45), PowerModels `DCPPowerModel` [[46]](#ref46)): no loss term |
| `sending` | Limit at **both** branch ends, so the sending end binds: MATPOWER AC OPF ($\lvert S_f\rvert, \lvert S_t\rvert \le$ `RATE_A`) [[45]](#ref45), PowerModels DCPLL [[34]](#ref34), [[46]](#ref46) and LPAC [[47]](#ref47) |
| `full` | PyPSA $\lvert p\rvert + \psi \le \overline{P}$ [[35]](#ref35) |

The tests pin this.  `test/source/test_pm_case5_asym.cpp` solves
PowerModels' `case5_asym` (`cases/pm_case5_asym`, angle limits widened
to ±30°).  Lossless gtopt matches PowerModels `DCPPowerModel` to
$10^{-6}$ relative.  With piecewise losses, `sending` and the DCPLL
loss side per branch, gtopt converges to the `DCPLLPowerModel` optimum
from above as the segments grow.  `test_line_thermal_limit.cpp` checks
the sending-end identity $`f + a\,\ell = \overline{F}`$ in both
directions under every allocation, and checks that
`flow` = `sending` under `receiver`, $f + \ell = \overline{F}$ under
`full`, cost(`full`) ≥ cost(`sending`) ≥ cost(`flow`), and the thermal-row
duals against finite differences.  `test_ieee738.cpp` checks the
IEEE 738 heat balance [[38]](#ref38) against the Annex B worked examples
and the NREL static ratings (OEDI 6231, DOI
[10.2172/2476805](https://doi.org/10.2172/2476805)).

#### Dynamic Line Rating through Critical Spans (`Line.dlr`)

A line with a `dlr` object carries a set $\mathcal{K}_l$ of **critical
spans** — the spans whose conductor temperature limits the line
[[9]](#ref9).  One representative span ($|\mathcal{K}_l| = 1$) is
valid and common.  The span set is an input; how it is chosen is a
pre-processing step outside the LP.

**Heat balance.**  For span $k$ and block $b$, with the exogenous
weather of that block (ambient temperature $T_a$, wind speed $V_w$ and
direction, irradiance on the conductor $S$) and the maximum conductor
temperature $T_s$, IEEE Std 738 [[38]](#ref38) gives the steady-state
ampacity from

```math
q_c + q_r \;=\; q_s + I^2\,R(T_s)
```

with, in SI units (conductor diameter $D$, emissivity $\varepsilon$,
absorptivity $\alpha$, air properties $\mu_f, \rho_f, k_f$ at the film
temperature $T_f = (T_s + T_a)/2$ and the span elevation):

```math
q_{c1} = K_\varphi\,(1.01 + 1.35\,Re^{0.52})\,k_f\,(T_s - T_a),\qquad
   q_{c2} = K_\varphi\,0.754\,Re^{0.6}\,k_f\,(T_s - T_a)
```

```math
q_{cn} = 3.645\,\rho_f^{0.5}\,D^{0.75}\,(T_s - T_a)^{1.25},\qquad
   q_c = \max(q_{c1},\, q_{c2},\, q_{cn})
```

```math
q_r = 17.8\,D\,\varepsilon\,\Bigl[\bigl(\tfrac{T_s + 273}{100}\bigr)^4
   {} - \bigl(\tfrac{T_a + 273}{100}\bigr)^4\Bigr],\qquad
   q_s = \alpha\,S\,D
```

where $`Re = D\,\rho_f\,V_w / \mu_f`$ and
$K_\varphi = 1.194 - \cos\varphi + 0.194\cos 2\varphi + 0.368\sin 2\varphi$.
The attack angle between wind and span axis is
$\varphi = \bigl|((\text{dir} - \text{azimuth} + 90) \bmod 180) - 90\bigr|$,
and $\varphi = \varphi_{lw}$ (`low_wind_angle`, default 45°) when
$V_w <$ `low_wind_speed` (default 0.5 m/s; the direction is unreliable).
$R(T)$ is linear: between the 25 °C and 75 °C values (extrapolated
beyond), or $`R(T) = k_{ac}\,R_{20}\,(1 + \alpha_R (T - 20))`$ from the
DC data-sheet form.  $S$ is the global horizontal irradiance used
directly as absorbed flux (`solar_mode = ghi`, conservative; no
sun-geometry model), or $S = 0$ under `solar_mode = none`.  Then

```math
I_{k,b} = \sqrt{\max\!\bigl(0,\; (q_c + q_r - q_s)/R(T_s)\bigr)},
   \qquad
   T_{k,b} = \eta\,\sqrt{3}\,V\,v_{pu}\,\mathrm{pf}\,I_{k,b}\,n_{sub}/1000
```

in MW, with safety factor $\eta$, voltage $V$ [kV] and $n_{sub}$
sub-conductors.  A precomputed per-span MW schedule (`rating`) replaces
$T_{k,b}$ directly and is used verbatim ($\eta$, $v_{pu}$, pf, $n_{sub}$
and $V$ apply only to the IEEE 738 path).  The ratings are per scenario:
SDDP backward apertures (hydrology) reuse their scene's $T_{k,b}$.  When an
emergency temperature is set, the same
computation at $T_s^{e}$ gives $T^{e}_{k,b}$.

Weather is data, so $T_{k,b}$ is a per-block right-hand side: the model
stays an LP.  `model_options.use_line_dlr = false` switches every `dlr`
block off (static `tmax`), and `dlr.use_emergency = false` drops the
emergency tier.

**`span_mode = min` (default).**  The effective rating is

```math
\overline{T}_{l,b} \;=\; \min\Bigl(\min_{k\in\mathcal{K}_l} T_{k,b},\;
   \widehat{F}^{dir}_{l,b}\Bigr)
```

where $\widehat{F}^{dir}_{l,b}$ is the remaining static cap: the min over
the **set** limit fields of the line in that block and direction (an unset
field is $+\infty$, never 0),

```math
\widehat{F}^{dir}_{l,b} \;=\; \min\bigl(F^{flow},\; R,\; O\,[u_b>0],\;
   C,\; E,\; S\bigr)^{dir}_{l,b}
```

with $F^{flow}$ the flow limit (`tmax` / `max_flow`, default `capacity`),
$R$ the rating (`max_rating`; the `overload_max_rating` when the overload
band is priced, with $R$ as its threshold), $O$ the outage rating
(`outage_max_rating`, only where `units_out` $u_b > 0$), $C$ the conductor
rating, $E$ the series-equipment rating and $S$ the security limit (B→A:
the `_ba` fields and the magnitudes of the signed `min_*` fields).  Under
`limit_mode = replace` the DLR rating is the conductor's thermal capacity
and replaces $C$ (without a conductor rating, an explicit `replace`
replaces $R$).  Under `min` every set part applies.  Unset, the mode is
`replace` on lines with a conductor rating and `min` otherwise, which is
the historical $\min(\texttt{tmax}, T)$.  When no field is set, the stage
DLR envelope stands in for $\widehat{F}$.  Under `replace` the static
normal rating `tmax_normal` is replaced too, and the overload band uses
the DLR normal rating.  All parts bound the same loaded quantity, so one
bound or row per direction suffices.  The binding part is identified by
value (ties: conductor/DLR, rating, outage, equipment, security, flow),
and the dual of that bound or row is reported as `<part>{p,n}_dual`.
On lines with such fields the loss geometry uses the stage maximum of
$\widehat{F}$, and the block value enters only as that bound or RHS, so
the dual is not split with the segment bounds.

$\overline{T}$ is applied exactly where $\overline{F}^{dir}$ is applied:
as the flow column bound under `flow` (and as `linear`'s
$\overline{T}/(1+a\lambda)$), and in the `thermalp` / `thermaln` rows
under `sending` / `full`.  The argmin span
$k^\*\_b$ is recorded.  With an emergency tier and a positive
`overload_penalty` in the block, the hard cap is
$\min_k T^e\_{k,b}$ and $\min_k T\_{k,b}$ becomes the normal threshold
$F^n$ of the overload band above (slack bound per
`overload_slack_bound`, default $`\min(T^e - T,\; 1.5\,T)`$).

**`span_mode = rows`.**  One row pair per span and block,

```math
f_l^{dir} + a_l\,\ell_l^{dir} \;\le\; T_{k,b}
   \qquad k \in \mathcal{K}_l,\; dir \in \{ab, ba\}
```

with the same per-loss-mode left-hand side as the thermal rows (signed
flow, segments, $(1+a\lambda) f$, …).  At build time a span with
$T_{k,b} > \overline{T}_{l,b}$ is **dominated** in that block and its
row is dropped (ties are kept), so the LP is the `min` LP plus at most
the tied rows, and both modes have the same optimum.  Spans that are
never critical are logged.

**Thermal-limit mode.**  In both span modes, both directions, the
emergency tier and for precomputed ratings, $T$ caps the same loaded
quantity as the static `tmax`: $|f| \le T$ under `flow`,
$`|f| + a_l\,\ell \le T`$ under `sending` ($a_l$ the sending-bus loss
share) and $|f| + \ell \le T$ under `full` ($a_l = 1$).  Under `flow`
the DLR adds no row in `min` mode; under `sending` / `full` it only
lowers the RHS of the existing thermal rows.  The flow-only DLR is
`line_thermal_limit_mode = flow`.

**Loss envelope.**  The per-block DLR rating never drives the loss PWL:
the segments keep the static `tmax` (or `capacity`, or `loss_envelope`)
envelope.  Without any of them the stage envelope
$`1.05\,\max_b \min_k T^{(e)}_{k,b}`$ stands in as the static cap and
loss range, so the loss geometry is block-independent and identical in
both span modes.  Under `limit_mode = replace` (non-expandable lines)
the range is the larger of the static limit maximum (over all set fields)
and that stage envelope, so flow above the static rating keeps its loss
curve.

**Duals.**  `Line/span_thermal_dual` reports $\partial z/\partial T_{k,b}$
per span (keyed by span uid).  In `min` mode it is the dual of the
effective cap attributed to $k^*_b$: the thermal row dual, or the flow
reduced cost divided by its limit coefficient while the column sits at
the DLR bound.  In `rows` mode it is the span row duals.  The
overload-band row dual (emergency tier) is added to the normal-tier
argmin span in both modes.  The sum over spans is the same in both
modes; with ties, `rows` may split it between the tied spans.

### 5.6 Kirchhoff Voltage Law (DC OPF)

When `use_kirchhoff = true` and `use_single_bus = false`, voltage angle
variables $\theta_n$ are introduced and DC power flow constraints enforce
Kirchhoff's Voltage Law on each line $l$ connecting bus $a$ to bus $b$
[[11]](#ref11) [[12]](#ref12):

$$
\frac{\theta_{a,s,t,b} - \theta_{b,s,t,b}}{X_l / V^2} = f_{l,s,t,b}^{+} - f_{l,s,t,b}^{-}
$$

> **Unit convention:** $V$ is the line's `voltage` field (defaults to 1.0)
> and $X_l$ is the `reactance`. The two must use a consistent unit system:
>
> | Mode | $V$ | $X_l$ | $B_l = V^2/X_l$ |
> |------|-----|-------|------------------|
> | **Per-unit** (default, `voltage` omitted) | 1.0 | p.u. | p.u. |
> | **Physical** (`voltage` in kV) | kV | Ω | MW/rad |
>
> Mixing conventions (e.g. $V = 220$ kV with $X = 0.01$ p.u.) produces
> an enormous susceptance and will cause numerical issues.
> See [[17]](#ref17) for an example of automatic per-unit conversion.

In the implementation, this is scaled by $\sigma_\theta$ for numerical
stability. Defining the scaled susceptance:

$$
\chi_l = \sigma_\theta \cdot \frac{X_l}{V^2}
$$

the constraint becomes:

```math
-\theta_{a,s,t,b} + \theta_{b,s,t,b} + \chi_l \, f_{l,s,t,b}^{+} - \chi_l \, f_{l,s,t,b}^{-} = 0
\qquad \forall \; l, s, t, b
```

#### Transformer and Phase-Shifting Transformer Model

A **tap-changing transformer** or **phase-shifting transformer (PST)** extends
the standard Kirchhoff constraint with two additional parameters:

- **Off-nominal tap ratio** $\tau_l$ (JSON field `tap_ratio`, default 1.0):
  scales the effective susceptance by $1/\tau_l$, so the line carries less
  (or more) power for the same angle difference.
- **Phase-shift angle** $\varphi_l$ (JSON field `phase_shift_deg`, default
  0°, converted internally to radians): shifts the right-hand side of the
  equality constraint, enabling direct control of power flow through the PST.

The generalized Kirchhoff constraint for a transformer branch is:

```math
-\theta_{a,s,t,b} + \theta_{b,s,t,b} +
(\tau_l \, \chi_l) \, f_{l,s,t,b}^{+} -
(\tau_l \, \chi_l) \, f_{l,s,t,b}^{-}
= -\sigma_\theta \cdot \varphi_{l,\text{rad}}
\qquad \forall \; l, s, t, b
```

where $\varphi_{l,\text{rad}} = \varphi_l \cdot \pi / 180$.

When $\tau_l = 1$ and $\varphi_l = 0$ the expression reduces to the standard
line constraint above. The parameters support per-stage schedules (scalar,
inline array, or Parquet filename) — useful for OLTC (on-load tap changer)
models where the tap position can vary across planning stages.

To model a transformer element in the JSON input, set `"type": "transformer"`
on the line entry (optional tag used by the `pp2gtopt` converter and for
documentation purposes):

```json
{
  "uid": 5, "name": "T1_2_5",
  "type": "transformer",
  "bus_a": 2, "bus_b": 5,
  "voltage": 220.0,
  "reactance": 0.0625,
  "tmax_ab": 150.0, "tmax_ba": 150.0,
  "tap_ratio": 1.05
}
```

#### DC Links and Zero-Impedance Ties

A line's `reactance` field selects one of three KVL treatments:

| `reactance` | `tiny_reactance_mode = tie` (default) | `tiny_reactance_mode = dc` (legacy) |
|-------------|---------------------------------------|-------------------------------------|
| absent / unset | DC link (no KVL row) | DC link (no KVL row) |
| $X_l = 0$ or $\lvert X_l / V^2 \rvert < \epsilon_X$ | zero-impedance tie | DC link (no KVL row) |
| $\lvert X_l / V^2 \rvert \ge \epsilon_X$ | regular KVL row (above) | regular KVL row (above) |

where $\epsilon_X$ is the effective zero-impedance floor

```math
\epsilon_X = \max\bigl(\epsilon_X^{\text{abs}},\;
   \rho_X \cdot \operatorname{median}_{l : X_l \neq 0} \lvert X_l / V_l^2 \rvert\bigr),
```

with $\epsilon_X^{\text{abs}}$ = `model_options.dc_line_reactance_threshold`
(default $10^{-6}$ p.u.) and $\rho_X$ =
`model_options.tiny_reactance_rel_threshold` (default $0.01$; $0$
restores the absolute-only floor).  The relative term is independent of
the unit system (per-unit, Ω/kV, or the PLEXOS $V = \sqrt{S_\text{base}}$
convention) and catches placeholder reactances that sit at the absolute
floor (PLEXOS generator-connection links with $X = 10^{-4}$ p.u.,
$\chi = 10^{-6}$).  Tying a line whose $\chi_l$ is $\rho_X$ of the
median shifts the flow split of a parallel path of median impedance by
at most $\approx \rho_X$.  Setting both floors to $0$ leaves only explicit
$X_l = 0$ as ties.  HVDC links and PLP DC lines are emitted **without**
`reactance`.

A **zero-impedance tie** is the $X_l \to 0$ limit of the transformer
constraint: the flow term vanishes (the tap ratio $\tau_l$ multiplies
$\chi_l$ and is therefore irrelevant) while the phase shift is kept,

```math
-\theta_{a,s,t,b} + \theta_{b,s,t,b}
= -\sigma_\theta \cdot \varphi_{l,\text{rad}}
\qquad \forall \; s, t, b,
```

so the two bus angles are forced equal (up to $\varphi_l$).  The flow
$f_{l,s,t,b}^{\pm}$ remains a free variable bounded by the line's
`tmax_ab` / `tmax_ba`, still enters both bus balances and is reported
in the output: it is set by the bus balances.  No coefficient derived
from $X_l$ reaches the matrix, and a tie is excluded from the
`scale_theta` median, the `theta_max` sum and the $\chi$ spread check
(a system whose lines are all ties keeps the default
$\sigma_\theta = 1$).  A tie's resistance is as degenerate as its
reactance: by default (`model_options.tie_line_losses = false`) the tie
is lossless — no loss column, segment or link row is built for it;
`tie_line_losses = true` keeps its loss model (legacy).  In
`cycle_basis` mode the tie is a cycle edge with coefficient $0$: it
closes loops and contributes only $\varepsilon_l \varphi_l$ to the
cycle right-hand side.  A cycle made only of ties degenerates to
$0 = \sum \varepsilon_l \varphi_l$ and is dropped (with a warning when
the net phase shift is nonzero).  Consequently the circulating split
around a loop made only of ties is free, exactly as on a merged bus that
keeps its branch flows and limits: any tiny-$X$ model fixes it by the
ratio of the tiny reactances (placeholders in practice), and the tie LP
is a relaxation of it (cost never higher).  A single tie in a loop with
normal lines, ties in series and a tie in parallel with a normal line
reproduce the $X \to 0$ flows exactly.  Ties join their buses for island
detection and reference-angle pinning, like any AC line.  A tie between
two buses whose `reference_theta` values cannot satisfy the row is
infeasible; the line then falls back to a DC link with a warning.

The legacy `dc` treatment drops the KVL row, so the LP may open an
arbitrary angle difference across a physically near-zero-impedance
branch and use it as a free phase shifter, distorting loop flows and
nodal prices.

#### Reference Bus

One bus is designated as the **reference bus** with a fixed voltage angle:

```math
\theta_{n^{\text{ref}},s,t,b} = \theta^{\text{ref}} \cdot \sigma_\theta
\qquad \forall \; s, t, b
```

typically $\theta^{\text{ref}} = 0$.

#### Voltage Angle Bounds

For non-reference buses:

```math
-\theta_{\max} \, \sigma_\theta \;\leq\; \theta_{n,s,t,b} \;\leq\; \theta_{\max} \, \sigma_\theta
\qquad \forall \; n \neq n^{\text{ref}},\; s, t, b
```

#### Enabling Conditions

Kirchhoff constraints are added when **all** of:
- `use_kirchhoff = true` (default)
- `use_single_bus = false` (default)
- The system has more than `kirchhoff_threshold` buses (default 0)
- Line has a defined `reactance` value (a zero or sub-threshold value
  yields a zero-impedance tie, see above; an absent value a DC link)

#### Loop-Flow (Cycle-Basis) Form

With `kirchhoff_mode = cycle_basis` (the default) the angles are
eliminated: for every fundamental cycle $C$ of the in-service network
(BFS spanning tree, one cycle per co-tree edge) one row per block

```math
\sum_{l \in C} o_{l,C} \, \chi_l \left(f_{l,s,t,b}^{+} - f_{l,s,t,b}^{-}\right)
= -\sum_{l \in C} o_{l,C} \, \sigma_\theta \, \varphi_{l,\text{rad}}
```

with $o_{l,C} = \pm 1$ the orientation of line $l$ around $C$.  The
basis is built per stage on the lines in service; a block in which a
line is out (`Line.in_service = 0`) gets a basis rebuilt on its own
surviving lines (blocks sharing the same outage set share one rebuild),
so every loop that survives the outage keeps its KVL row.  (Before
2026-11 a block outage simply dropped the base cycles through the line,
losing the loops that only exist through other cycles' combination.)

#### Optimal Transmission Switching (issue #509)

When a `LineCommitment` row references a line, the line becomes a
**switching candidate**: an additional binary
$u_{l,s,t,b} \in \lbrace 0, 1\rbrace$ is introduced that opens (0) or closes
(1) the breaker dynamically at solve time
[[Fisher2008]](https://doi.org/10.1109/TPWRS.2008.922256).

**Capacity gating** (always emitted, both Kirchhoff and transport
modes; v0):

```math
-\overline{F}_l^{ba} \, u_{l,s,t,b}
\;\leq\; f_{l,s,t,b} \;\leq\;
\overline{F}_l^{ab} \, u_{l,s,t,b}
\qquad \forall \; s, t, b
```

so $u_{l,s,t,b} = 0$ forces $f_{l,s,t,b} = 0$ — the line carries
no flow.

**Loss-column gating (channel E).**  The capacity rows above gate
only the *flow* columns; a loss column (§5.5) is bounded solely by
its own physical cap, so an OFFLINE line under a negative bus-dual
pair-sum could still book loss up to that cap and act as a full-size
energy sink.  Each loss column is therefore gated with the same
capacity pattern:

```math
\ell_{l,s,t,b} \;-\; \overline{\ell}_l \, u_{l,s,t,b} \;\leq\; 0
\qquad \forall \; s, t, b,
```

named `loss_gate_p` / `loss_gate_n` for the per-direction (or
shared/tangent) loss columns, where $\overline{\ell}\_l$ is the loss
column's own finite physical upper bound.  At $u\_{l,s,t,b} = 1$ the
row duplicates the column bound (non-binding); at $u\_{l,s,t,b} = 0$
it pins $\ell\_{l,s,t,b} = 0$.  With $\ell$ pinned the
`tangent_signed_flow` abs-flow proxy $v_l$ needs no gate of its own:
it feeds only the (now slack) chord row, never a bus balance, so any
residual value is cost-free degenerate freedom.  Modes whose loss
columns are already zeroed transitively (`bidirectional` / uniform
equality chains) get a redundant but harmless row; columns that are
vestigial (lossless) or unbounded are skipped.

> **`piecewise_direct` transfer cost.**  Each of the $K$ per-direction
> segment columns carries the full per-MWh cost $c_l$ (`tcost`), so with
> $\sum_k \text{seg}_k = |f|$ the charged cost is $`c_l\,|f|`$, as in every
> other loss mode (it was $c_l |f| / K$ before the fix).
<!-- source: source/line_losses.cpp (add_direct_direction) -->

> **`piecewise_direct` caveat.**  This mode carries flow only in
> per-direction segment columns (no $f^+ / f^-$ aggregator), so
> `LineCommitment` cannot yet gate it — the breaker instruction would
> be silently ignored.  `LineCommitmentLP` emits a one-shot warning
> naming the gateable alternatives (`piecewise` / `bidirectional` /
> `tangent_signed_flow`); segment-column gating is a follow-up.

**Kirchhoff KVL big-M disjunction** (v0.5; emitted only in
`KirchhoffMode::node_angle`).  The existing equality KVL row
$f = b^{\text{eff}}_l (\theta_a - \theta_b - \varphi_l)$ is
rewritten in place as the upper-side inequality

```math
-\theta_a + \theta_b + x_\tau \, f_{l,s,t,b} + M_l \, u_{l,s,t,b}
\;\leq\; M_l - \varphi_l
```

and a new row is added for the lower-side:

```math
-\theta_a + \theta_b + x_\tau \, f_{l,s,t,b} - M_l \, u_{l,s,t,b}
\;\geq\; -M_l - \varphi_l.
```

At $u_{l,s,t,b} = 1$ both inequalities collapse to the original
equality.  At $u_{l,s,t,b} = 0$ they simultaneously slack, freeing
$\theta_a, \theta_b$ to take any values in
$[-\theta_{\max}, +\theta_{\max}]$ — i.e. the two bus angles
**decouple** exactly like the physics of an opened breaker.

The big-M parameter $M_l$ defaults to

```math
M_l = 2 \, \theta_{\max} + |\varphi_l|
```

(Fisher 2008 baseline refined for the phase-shift offset).  This
is loose by design — `LineCommitment.kvl_big_m` overrides per line,
serving as the write-back target for the v1 iterative-tightening
pre-solve [[Pineda2024]](https://doi.org/10.1016/j.epsr.2024.110620).

**Cycle-basis disjunction (v1).**  In `kirchhoff_mode = cycle_basis`
(the GTOpt default) KVL is enforced as one equality per fundamental
cycle $C$ (see `source/kirchhoff_cycle_basis.cpp`), so a single
switched-off line $l \in C$ invalidates every cycle through it.
`add_kvl_rows` checks each cycle row for switchable lines and, when
any are present, replaces the equality with the disjunctive form

```math
-\!\!\sum_{l \in C \cap \text{switchable}}\!\!
\bigl(1 - u_l\bigr) \, M_C
\;\leq\;
\sum_{e \in C} \text{sign}_e \,
\bigl(x_{\tau,e} \, f_e + \varphi_e\bigr) \cdot \text{row_scale}
\;\leq\;
\sum_{l \in C \cap \text{switchable}}\!\!
\bigl(1 - u_l\bigr) \, M_C
```

with per-cycle big-M

```math
M_C \;=\; 2 \theta_{\max} \, |C| \, \text{row_scale}
\;+\; \sum_{e \in C} |\varphi_e| \, \text{row_scale}.
```

When every switchable line in the cycle is closed
($`u_l = 1\;\forall\, l \in C \cap \text{switchable}`$) the slack collapses to
zero and
both inequalities reduce to the original equality.  Any open line
($u_l = 0$) widens the slack by exactly $M_C$, decoupling the
cycle through that branch.  Validation now accepts every combination
of (`node_angle`, `cycle_basis`, transport mode) × `LineCommitment`.

**Method gate.**  OTS is **strictly** rejected when
`method ∈ {sddp, cascade}` because Benders cuts on a mixed-integer
subproblem are unsound (the cost-to-go function loses convexity)
[[Zou2019]](https://doi.org/10.1007/s10107-018-1249-5).  A future
SDDiP-style relaxation (Lagrangian cuts) would lift the
restriction.

**Chronological-stage gate.**  Like `Commitment` for generators,
OTS is enforced only on chronological stages; on duration-weighted
representative blocks the binary has no cross-block meaning and is
silently skipped.

**u/v/w decomposition (v1.1).**  When the user sets
`LineCommitment.startup_cost` and/or `shutdown_cost` (per-event line
closing / opening costs, in \$), `LineCommitmentLP` switches from
the single-`u_l` form to the Knueven–Ostrowski–Watson 2020 /
Morales-España et al. 2013 three-binary decomposition.  A startup
indicator $v_{l,t} \in [0,1]$ and a shutdown indicator
$w_{l,t} \in [0,1]$ are added (continuous but implied-binary at the optimum)
joined by the logic transition

$$
u_{l,t} - u_{l,t-1} - v_{l,t} + w_{l,t} = 0
\qquad (t > 0),
$$

with the first-block row carrying the pre-stage `initial_status`
on the right-hand side:

$$
u_{l,0} - v_{l,0} + w_{l,0} = u_l^{\text{init}}.
$$

The exclusion row $v_{l,t} + w_{l,t} \le 1$ enforces "at most one
event per block".  The objective gains
`startup_cost`$\sum_t v_{l,t}$ and `shutdown_cost`$\sum_t w_{l,t}$
(face-value, NOT scaled by block duration — events are one-time, not
per-hour).  Declaring only $u_{l,t}$ as integer is correct: C1 + C3
+ nonnegative event costs force $v, w$ to the integer up/down
transition of an integer $u$ at every optimal vertex, cutting
branching variables by ≈⅔ relative to a 3-integer formulation
[[Knueven2020]](https://doi.org/10.1287/ijoc.2019.0944),
[[Morales-Espana2013]](https://doi.org/10.1109/TPWRS.2013.2251373).

The v0 `initial_status`-as-first-block-pin semantics is suppressed
when u/v/w is active; pinning $u_{l,0}$ would over-constrain the LP
(force the breaker open at $t = 0$ even when serving demand requires
it closed).

**Min up / min down time (v1.2).**  When `LineCommitment.min_up_time`
[hours] is set together with u/v/w, an anti-flicker family of rows
is emitted (mirroring `CommitmentLP` C6):

```math
\sum_{q=t}^{t + \mathrm{UT}_t - 1} u_{l,q}
\;\geq\; \mathrm{UT}_t \cdot v_{l,t},
```

where $\mathrm{UT}\_t$ is the smallest block-count whose cumulative
duration covers `min_up_time` hours starting at block $t$.  When
$v\_{l,t} = 1$ (line closes at $t$) the right-hand side forces every
$u\_{l,q}$ in the window to 1.  Rows are skipped at the end of the
stage where $\mathrm{UT}\_t \le 1$ (trivially satisfied).  The
symmetric `min_down_time` row family uses the dual form

```math
\sum_{q=t}^{t + \mathrm{DT}_t - 1} u_{l,q}
\;+\; \mathrm{DT}_t \cdot w_{l,t}
\;\leq\; \mathrm{span}_t,
```

with $\mathrm{span}_t = \min(\mathrm{DT}_t, N - t)$.

**Max starts / min starts (v1.2).**  Two-sided rolling-window cap on
the closing-event count (mirrors `CommitmentLP` C9):

```math
\mathrm{min\_starts} \;\leq\;
\sum_{t \in W} v_{l,t}
\;\leq\; \mathrm{max\_starts}
\qquad \forall \, W,
```

where $W$ is a window defined by `starts_scope`:
`"hour"` ⇒ 1 row per block, `"day"` ⇒ 24-hour cumulative-duration
window, `"week"` ⇒ 168-hour window, `"horizon"` (default) ⇒ one row
per stage, or an explicit integer hour count.  Aliases `"month"` and
`"year"` collapse to `"horizon"` since a typical GTOpt stage is
shorter than a calendar month.  Both bounds share the same window
LHS; either side is omitted when its bound is unset.  Mirrors
PLEXOS's `Max Starts {Hour|Day|Week|...}` properties on the
`Generator` class.

### 5.7 Battery / Energy Storage Constraints

Batteries model energy storage with charge/discharge efficiencies and
self-discharge losses. The formulation follows the standard linear storage
model used in capacity expansion tools [[4]](#ref4) [[13]](#ref13)
[[14]](#ref14).

#### State-of-Charge (SoC) Balance

For each battery $e$ at each block $b$ within a stage:

```math
e_{e,s,t,b} = e_{e,s,t,b-1} \cdot (1 - \mu_e^h \Delta_b)
\;+\; p_{e,s,t,b}^{\text{in}} \cdot \eta_e^{\text{in}} \cdot \Delta_b
\;-\; p_{e,s,t,b}^{\text{out}} \cdot \frac{\Delta_b}{\eta_e^{\text{out}}}
```

where:
- $\mu_e^h = \mu_e / 8760$ is the hourly self-discharge rate
  (linear convention: a block of $\Delta_b$ hours keeps the fraction
  $1 - \mu_e \Delta_b / 8760$ of the stored energy; compounding only
  appears across consecutive blocks)
- $\eta_e^{\text{in}}$ is the charging efficiency (fraction of input power
  stored)
- $\eta_e^{\text{out}}$ is the discharging efficiency (fraction of stored
  energy delivered)
- $\Delta_b$ is the block duration in hours

#### Default self-discharge

When a battery leaves `annual_standing_loss` unset, gtopt uses
$\mu_e = \bar\mu = 0.48$ year$^{-1}$, i.e. **4 % per month**
($0.04 \times 12$; $\mu_e^h \approx 5.48 \times 10^{-5}$ h$^{-1}$, so a
730 h block loses exactly 4 % of its stored energy).  The figure is the
combined lithium-ion **cell + battery-management-system** self-discharge:
Battery University BU-802b [[18]](#ref18) reports ≈ 5 % in the first
24 h, then 1–2 %/month for the cell plus ≈ 3 %/month drawn by the
protection circuit, roughly doubling per +10 °C; the Wikipedia
"Self-discharge" table [[19]](#ref19) lists Li-ion at 2–3 %/month.  The
planning-wide value is `model_options.battery_annual_standing_loss`
(`0` restores the legacy lossless default); an explicit per-battery `annual_standing_loss`,
including `0`, always wins.  Non-lithium storage emitted as a Battery
(pumped hydro) is written with an explicit `annual_standing_loss` by the
converters.  Reservoirs and other storages are unaffected (their
`annual_standing_loss` still defaults to $0$).

Besides physical realism, $\mu_e > 0$ removes part of an LP degeneracy:
with $\mu_e = 0$ and $\eta^{\text{in}} = \eta^{\text{out}} = 1$, any
charge/discharge zig-zag across blocks that returns the SoC to the same
trajectory has zero cost, so the LP admits alternate optima with
arbitrary cycling.  With $\mu_e > 0$ every MWh held in storage decays,
so holding energy longer than needed costs the decayed energy times its
replacement price.  Limits (verified in
`test/source/test_battery_self_discharge.cpp`):

- When the marginal energy in the charging window costs **exactly 0**
  (curtailed solar at $0/MWh), the extra decay and the round-trip losses
  of a zig-zag are replaced by free energy: zig-zag and direct schedules
  still **tie**.  Any strictly positive cost on that energy breaks the
  tie — e.g. a 1 $/MWh VOM on the zero-cost unit penalises a zig-zag that
  cycles $X$ MWh by $`X\,(1/(\eta^{\text{in}}\eta^{\text{out}}) - 1)`$
  dollars (8.64 USD for 80 MWh at $\eta = 0.95$), and a battery
  `discharge_cost` (throughput / degradation cost, default 0) by
  `discharge_cost` · $X$.
  gtopt adds no artificial default penalty: use the physical VOM of the
  zero-cost units or a datasheet degradation cost.
- Same-block simultaneous charge and discharge does not move the SoC, so
  self-discharge never prices it; see the power envelope below.

#### Power Envelope (valid inequality)

By default (`model_options.battery_power_envelope = true`) each battery
converter adds, per block,

```math
\frac{p_{e,s,t,b}^{\text{in}}}{\overline{P}_{e,t,b}^{\text{in}}} +
\frac{p_{e,s,t,b}^{\text{out}}}{\overline{P}_{e,t,b}^{\text{out}}}
\;\leq\; 1
```

(row `Converter/power_envelope`, multiplied by
$\max(\overline{P}^{\text{in}}, \overline{P}^{\text{out}})$ so it stays in
MW), where $\overline{P}$ are the hard per-block bounds of the synthetic
charge demand / discharge generator.  It is valid for any inverter that
shares one rating between directions and cuts the vertex where a lossy
battery charges and discharges simultaneously at full power to burn
surplus energy at zero or negative prices.  Partial simultaneous
operation inside the envelope remains LP-feasible — only binaries
($u^{\text{in}} + u^{\text{out}} \le 1$) remove it entirely; none of the
current commitment paths add that exclusivity.  With an expandable
converter capacity the existing row
$p^{\text{in}} + p^{\text{out}} \le \text{capacity}$ is the linear valid
inequality for the variable rating; the envelope uses the fixed column
bounds, so both stay valid.  The row is skipped when one direction has
no rating ($\overline{P} \le 0$ or unbounded) or when the `pmin_*`
floors force a point outside the envelope.

#### Energy Bounds

```math
\underline{E}_{e,t} \;\leq\; e_{e,s,t,b} \;\leq\; \overline{E}_{e,t}
\qquad \forall \; e, s, t, b
```

#### Initial and Final Conditions

For the **first block** of the first stage:

```math
e_{e,s,t_0,b_0} \in [\underline{E}_e^{\text{ini}},\; \overline{E}_e^{\text{ini}}]
```

where $E_e^{\text{ini}}$ defaults to the stage bounds if not specified.

For the **last block** of each stage, a final SoC target may be enforced:

```math
e_{e,s,t,b_{\text{last}}} \;\geq\; E_e^{\text{fin}}
```

where $E_e^{\text{fin}}$ defaults to $\underline{E}_e$ if not specified.

#### Inter-Stage Linking

The final SoC of stage $t$ links to the initial SoC of stage $t+1$:

$$
e_{e,s,t+1,b_0} = e_{e,s,t,b_{\text{last}}}
$$

#### Commissioning After the First Stage

A state-carrying storage (any class with `use_state_variable` on and no
daily cycle) that is inactive in stage $t-1$ and active in stage $t$ has
no previous SoC to inherit, within one LP or across an SDDP phase
boundary.  Its initial-state column is then **fixed** and no state link is
queued:

$$
e^{\text{ini}}_{e,s,t} = E_e^{\text{ini}}
$$

Before this rule the column was free in
$[\underline{E}_e, \overline{E}_e]$ (same LP) or an unlinked SDDP `sini`,
so the model could start the new unit with free stored energy.  Only a
**battery** may omit `eini` here: it then starts at $\underline{E}_e$ and
`validate_planning` emits one warning.  For reservoirs, LNG terminals,
thermal / hydrogen / ammonia storage, volume rights and allowance pools a
missing `eini` at commissioning is a validation error.  The global first
stage keeps the rule above (free in the stage bounds when `eini` is
unset).  Storage without a state variable closes every stage with
$e^{\text{fin}} = e^{\text{ini}}$ and keeps its free start.

#### Daily Cycle on Representative Days (`daily_cycle`)

A representative-day stage $t$ has 24 hour-of-day blocks whose durations
$\Delta_b$ add up to $`D_t = 24\,n_t`$ hours ($n_t$ = days represented).
With `daily_cycle` (the battery default) the balance above is written for
ONE day: every $\Delta_b$ in the SoC balance is multiplied by
$\kappa_t = 24 / D_t$, while the objective still weighs the flows with the
full $\Delta_b$.  Each stage is closed,

$$
e_{e,s,t,b_{\text{last}}} = e_{e,s,t}^{\text{ini}}
\qquad (\Delta e_{e,s,t} = 0),
$$

so no energy moves between days, and the energy bounds apply to the
single simulated day.  Stage-to-stage coupling and SDDP state variables
are off for such storages.

#### Weighted Cycle Groups (`Stage.cycle_group`)

Consecutive stages of one phase sharing a `cycle_group` id $G$ form one
storage cycle for every `daily_cycle` storage.  Stage $d \in G$ (in
stage order) stands for $w_d$ identical repetitions of its day
(`Stage.cycle_weight`, default $D_d/24$; e.g. a weekday and a weekend day
of a typical week, $w = 5$ and $w = 2$).  With
$\Delta e_d = e_{d,b_{\text{last}}} - e_d^{\text{ini}}$ the SoC change of
one repetition, the per-stage closure is replaced by

```math
\begin{aligned}
e_{d+1}^{\text{ini}} &= e_d^{\text{ini}} + w_d\,\Delta e_d
  && \text{(chain, } d, d{+}1 \in G\text{)} \\
e_{d_{\text{first}}}^{\text{ini}} &= e_{d_{\text{last}}}^{\text{ini}}
  {}+ w_{d_{\text{last}}}\,\Delta e_{d_{\text{last}}}
  \;\Longleftrightarrow\; \sum_{d \in G} w_d\,\Delta e_d = 0
  && \text{(closure)} \\
\underline{E} \le\; &e_{d,b} + (w_d - 1)\,\Delta e_d \;\le \overline{E}
  && \forall b,\; w_d > 1 \text{ (last repetition)}
\end{aligned}
```

The SoC in repetition $j$ is $`e_{d,b} + j\,\Delta e_d`$, linear in $j$, so
bounding the first repetition (the column bounds) and the last one bounds
every repetition exactly: the group is a feasible chronological cycle of
$w_d$ copies of each day in stage order (for integer weights), with no
energy created or lost beyond the modelled efficiencies.  Self-discharge
is applied per simulated day only (the carried level is not decayed
across repetitions).  When the objective weights of the stages are
proportional to $w_d$ (e.g. $w_d$ = days per typical week, $n_d$ = days
in the month), the closure is equivalent to
$`\sum_d n_d\,\Delta e_d = 0`$ over the month.  For an expandable storage
the last-repetition upper bound is also written against the installed
capacity column.  The next stage after a group starts from the group's
start level.  A group never crosses a phase boundary (validation error),
so no SDDP state is needed.  This is the intra-/inter-period decomposition
of Kotzur et al. (2018) with the period order fixed inside the group, and
it reduces to the daily closure when every $\Delta e_d = 0$.

#### Typical Periods with Weighted Blocks (`Stage.period_hours`)

A stage may sample a chronological period of `period_hours` $= H_t$ real
hours (a typical week, $H_t = 168$, or a day) and stand for several of
them.  Its block durations $d_b$ are then WEIGHTS: they enter the
objective, energy budgets, flows into coupled reservoirs and every
`dur[b]` of a user constraint, and add up to the represented time
$`D_t = H_t\,n_t`$.  The real time step is

```math
\Delta_b = d_b\,\kappa_t, \qquad \kappa_t = H_t / D_t .
```

A `daily_cycle` storage uses $\kappa_t$ instead of $24/D_t$: its balance
runs on the real steps and the per-stage closure closes the period (a
weekly cycle for $H_t = 168$; cycle groups chain periods the same way).
Its `max_cycles_day` cap is written per real 24 h window.  Ramp limits,
minimum up / down times and start windows use $\Delta_b$; costs and
reported prices keep the weighted $d_b$, so duals stay in $/MWh.
Non-uniform real steps (1 h / 2 h mixes, Chebyshev quadrature weights)
are supported throughout; a stage without `period_hours` is unchanged.

#### Point-Sampled Stages (`Stage.sampling = "point"`)

The typical-week samplers that produce these stages (uniform, peak /
ramp, Chebyshev–Gauss / –Lobatto with Fejér / Clenshaw–Curtis weights,
Wednesday-noon window) and an FY28 comparison are described in
[Typical-Week Sampling](../methods/typical-week-sampling.md).

In a block stage (default) a block value holds constant over the block,
whose length is the real step $\Delta_b$.  In a POINT stage the values are
samples AT the node times $t_k$ (`Block.time`).  For Chebyshev–Lobatto
nodes $t_0$ is the period start and $t_N$ the period end, and the
durations $w_k$ are quadrature weights (Clenshaw–Curtis), not intervals.
A periodic or chained period emits only $N$ blocks: node $N$ is the
same instant as node 0 of the next cycle, so it is dropped and node 0
carries $w_0 + w_N$ (the converters do this; `validate_planning` warns
when $t_{last} - t_0$ equals `period_hours`).
Integrals (costs, energy budgets, `dur[b]` of user constraints) keep
$`\sum_k w_k\,x_k`$, the exact quadrature of the node polynomial.  What
changes:

- Storage balances integrate between the nodes with the element's
  resolved `storage_integration` (below; defaults `lg4_pos` for
  reservoirs, `block` for batteries and the other storages).  With a
  banded rule the level $e_k$ is the state at $t_k$; closure and stage
  chaining use the level at the period end.
- Ramps default to `backward`, the interval slope (C4/C5 below with
  $h_k = t_k - t_{k-1}$); on a cyclic stage the first node wraps,
  $h_0 = t_0 + H - t_{N-1}$.  `central2` is opt-in and not recommended.
- Duration logic uses the cell partition $[\tau_{k-1}, \tau_k]$,
  $\tau_k = \sum_{j\le k} w_j$, of every stage (blocks on block stages,
  quadrature cells holding $t_k$ on point stages, validated).
  `max_cycles_day` days, `dur[b]` sums and block-stage commitment
  periods count cell durations: cell $k$ joins group
  $\lfloor (\tau_{k-1} + w_k/2)/P \rfloor$, so a nominal boundary snaps
  to the nearer cell end.
- **Commitment on point stages never lives on the nodes.**  Every unit
  has its own uniform grid of $P_g$ = max(`commitment_period`, 1 h)
  cells (default 1 h) aligned to the stage start (for the converters'
  typical week: Wednesday 12:00).  Node $k$ inherits the status of cell
  $c_g(k) = \min(\lfloor t_k / P_g \rfloor, C_g - 1)$,
  $C_g = \lceil H / P_g \rceil$: a node exactly on a cell boundary belongs to
  the
  LATER cell, the end node $t_N = H$ to the last one.
  $p_k \ge \underline P u_{c(k)}$, $p_k \le \overline P u_{c(k)}$; start-up /
  shut-down $v, w$ exist only at cell boundaries, and a ramp row gets the
  SU·$v$ / SD·$w$ allowance only when its stencil crosses into a new cell
  (inside a cell the plain ramp limit $`R\,h\,u`$ holds) under the default
  `ramp_allowance = "boundary"`, which the converters also write on
  point stages.  `"period"` gives every row of the period the allowance
  of its $v$ / $w$.  Min up / down
  and start windows count the cells' real hours.  Cells that hold no
  node carry no status of their own: the previous cell's status
  continues over them (so the binary count is the number of cells with a
  node, $H / P_g$ when the node spacing is below $P_g$).
- On block stages, blocks may carry `Block.commitment_cell` labels
  (refined sun-window blocks point at the base block they split): units
  without a `commitment_period` then keep one status per labelled cell,
  so refinement adds continuous columns only.
- Only ramps, storage interpolation / envelope, reconstruction and time
  stamps use the node times (and the point-stage commitment cells above).
- Cyclic commitment (`Commitment.cycle = "stage"`, typical periods):
  $u_1 - u_P = v_1 - w_1$ replaces the initial-status row; min up /
  down and start-tier windows are taken modulo the $P$ periods; start-up
  / shut-down costs carry the stage weight $\sum_b d_b /$`period_hours`.
  Commitment periods on point stages are at least 1 h (`auto` too,
  unset = 1 h).
- Duals: $\pi_k = \lambda_k / w_k$ is the node LMP.  With node $N$
  dropped (periodic grids) node 0 already carries $w_0 + w_N$, so no
  end merge is needed.  Hourly prices are rebuilt CELL-CONSTANT by
  default: $\pi_k$ holds on $[\tau_{k-1}, \tau_k]$ and an hour's price
  is the overlap-weighted mean, so $`\int \pi\,g\,dt = \sum_k \lambda_k u_k`$
  for every cell-constant quantity and a nonnegative node
  price never gives a negative hourly price.  The coefficient-space
  adjoint is an option (`price_reconstruction = adjoint`).
  Quantities are rebuilt with the interpolant of the storage rule
  (cell-constant for `block`, piecewise linear for `trapezoid`); the
  node polynomial only for smooth inputs.  `block_time.csv` gives
  $t_k$.

#### Storage Integration on Node Grids (`storage_integration`, provisional)

On a point-sampled stage (Chebyshev–Lobatto points $t_k$ from
`Block.time`, block weights $w_k$ = Clenshaw–Curtis weights) each
storage uses its resolved rule: the element's `storage_integration`,
else `reservoir_storage_integration` / `battery_storage_integration`,
else the global `storage_integration`, else `lg4_pos` for reservoirs and
`block` for every other storage.  Block stages always use the block
recursion, also selectable as `block`:

```math
e_k = e_{k-1}(1 - \ell\,w_k) + w_k\,\phi_k ,
```

with $\phi_k$ the net storage flow
($`\rho\,\eta_{in}\,p^{in}_k - \rho\,p^{out}_k/\eta_{out} - \rho\,s_k`$).  Its
period total is the block-weight (CC) quadrature, exact for polynomial
flows of the node degree.  The level $e_k$ is the state at the block end
$\tau_k = \sum\_{j \le k} w_j$, to first order — inconsistent with point
samples.

`trapezoid`, `lg4` and `lg6` define $e_k$ as the state AT the node $t_k$
(the last one at the period end) instead:

```math
e_k = e_{k-1}(1 - \ell\,(t_k - t_{k-1})) + \sum_{j \in S_k} D_{kj}\,\phi_j .
```

Here $`D_{kj} = \int_{t_{k-1}}^{t_k} L_j(t)\,dt`$ for the Lagrange basis
$L_j$ on a stencil $S_k$ of 2, 4 or 6 nodes nearest to the interval.
`trapezoid` uses the interval's two end nodes; at the window ends the
stencil is shifted to keep its size.  The matrix is then balanced on
its sparsity pattern so that BOTH

$$
\sum_k D_{kj} = w_j \quad\text{(columns)},\qquad
\sum_j D_{kj} = t_k - t_{k-1} \quad\text{(rows)}
$$

hold: a constant flow integrates exactly over every interval and the
period total stays exactly the CC quadrature, so the closure, the
`efin` state and the SDDP coupling are unchanged.  Signed rules use the
minimum-norm additive correction ($\min \lVert\delta\rVert_2$, i.e.
$\delta_{kj} = \alpha_k + \beta_j$); the `_pos` rules clip negative
weights and use an alternating row / column scaling, which keeps every
$D_{kj} \ge 0$.

Before the correction, the rule is exact at every node for linear
(`trapezoid`) or cubic (`lg4`) flows.  `lg4` has about 8 % negative
weight mass on Lobatto grids, which lets the LP shift energy inside an
interval (a phantom shift) and invalidates the envelope bound.  Plain
`lg4` / `lg6` are therefore not recommended for LP; `lg4_pos` is the
high-order option.  Only the storage's own charge,
discharge and drain columns are banded.  External injections into the
balance row, such as allowance pools, keep the block weight.  The dense
alternative, the exact cumulative integral $S = V J V^{-1}$ of the
global node interpolant, is not implemented in the LP.

**Envelope rows.**  The levels are bounded only at the nodes.  A charge
at $t\_{k-1}$ and a discharge at $t_k$ would therefore cancel inside the
row of $[t\_{k-1}, t_k]$, letting the storage shift up to
$`\tfrac12 \Delta_k\, p^{max}`$ beyond $e^{max}$ per charge→discharge
switch.  Every banded rule adds two sparse rows per interval, bounding
the level of the interpolated flows by their charge-only and
discharge-only integrals, taken with the nonnegative part
$D^+\_{kj} = \max(D\_{kj}, 0)$ of the balance weights (a negative weight
must not loosen the bound):

```math
e_{k-1} + \sum_{j \in S_k} D^+_{kj}\,\rho\,\eta_{in}\,p^{in}_j
  \le e^{max}_k \;(\text{or the installed capacity}),
```

```math
e_{k-1} - \sum_{j \in S_k} D^+_{kj}\,\rho\,
  \bigl(p^{out}_j/\eta_{out} + s_j\bigr)
  \ge e^{min}_k .
```

Storages whose input and output share one signed column get no envelope.

### 5.8 Converter Constraints

A converter $v$ couples a battery $e$ to a generator $g$ (discharge path) and
a demand $d$ (charge path) via a conversion rate $\rho_v$:

#### Discharge Coupling (Battery → Generator)

```math
p_{g,s,t,b} = \rho_v \cdot p_{e,s,t,b}^{\text{out}}
\qquad \forall \; s, t, b
```

#### Charge Coupling (Demand → Battery)

```math
\ell_{d,s,t,b} = \rho_v \cdot p_{e,s,t,b}^{\text{in}}
\qquad \forall \; s, t, b
```

#### Non-Effective Coupler (Fail-Closed Rule)

A *coupler* — Converter, Turbine (§5.10), Pump (§5.10 bis), GasTurbine
and electric compressor (§5.19) — is the only path through which its
partner turns one resource into another.  In a stage where the coupler
is not *effective* (the coupler is inactive, or a reference it needs is
inactive or has no column), its coupling row cannot be written, and the
partner's producing column $x$ is **closed** instead of being left free:

$$
x \le \min(\bar{x}, 0), \qquad x \ge \min(\underline{x}, 0)
$$

(a negative, i.e. consuming / reverse, lower bound is kept).  For the
converter: if the converter is inactive or the battery is not in
service, $p\_{g,s,t,b}$, $p^{\text{in}}\_{e,s,t,b}$ and
$p^{\text{out}}\_{e,s,t,b}$ are closed (the charge demand is a consumer and
is untouched).  Without this rule each of those stages produced energy
from nothing.

The closing is a *backstop*: `validate_planning` rejects the inconsistent
inputs up front (class / uid / name and stage / phase in the message):
a coupler whose one-to-one partner has a different `active` schedule
(Turbine ↔ generator / flow, GasTurbine ↔ generator, Converter ↔
battery / discharge generator, Pump ↔ waterway / load demand), a coupler
active while a shared reference is inactive (Turbine junction / waterway,
GasTurbine gas node, electric compressor motor bus), two turbines on one
generator, and a `must_run` commitment whose unit's coupler is inactive.
<!-- source: include/gtopt/coupler_fail_closed.hpp -->

#### Converter Capacity

When the converter has a capacity limit:

```math
p_{g,s,t,b} + \ell_{d,s,t,b} \;\leq\; \bar{C}_{v,t}
\qquad \forall \; s, t, b
```

$\bar{C}\_{v,t}$ is the converter's installed power.  For the converter
synthesised from a battery, `Battery.power_capacity` / `power_expcap` /
`power_expmod` / `power_capmax` / `power_annual_capcost` /
`power_annual_derating` make it an expandable power axis (§5.11, with its
own `capainst` / `capacost` SDDP state columns), while the battery's own
`capacity` / `expcap` / … expand the energy axis $\bar{E}\_{e,t}$.

#### Charge / Discharge Rate Floors (`pmin_charge`, `pmin_discharge`)

For a battery with floors $\underline{P}^{\text{in}}\_{e,t,b}$
(`pmin_charge`) and $\underline{P}^{\text{out}}\_{e,t,b}$
(`pmin_discharge`) and ratings $\overline{P}^{\text{in}}\_{e,t,b}$ /
$\overline{P}^{\text{out}}\_{e,t,b}$ (`pmax_charge` / `pmax_discharge`),
`Battery.commitment` selects between two regimes.

**Hard floors** (`commitment` unset, default) — the synthetic charge
demand and discharge generator columns carry the floors as bounds every
block:

$$
\underline{P}^{\text{in}}_{e,t,b} \le \ell_{d,s,t,b} \le \overline{P}^{\text{in}}_{e,t,b},
\qquad
\underline{P}^{\text{out}}_{e,t,b} \le p_{g,s,t,b} \le \overline{P}^{\text{out}}_{e,t,b}
$$

**Per-mode floors** (`commitment = true`) — one binary per mode and
block, $u^{\text{in}}\_{e,s,t,b}$ (converter `u_charge`) and
$u^{\text{out}}\_{e,s,t,b}$ (the status $u$ of the synthesised commitment
`uc_<bat>_gen`, §5.15/§5.16, whose `pmin` is
$\underline{P}^{\text{out}}$):

```math
\underline{P}^{\text{in}}_{e,t,b}\, u^{\text{in}}_{e,s,t,b}
\le \ell_{d,s,t,b} \le
\overline{P}^{\text{in}}_{e,t,b}\, u^{\text{in}}_{e,s,t,b},
\qquad
\underline{P}^{\text{out}}_{e,t,b}\, u^{\text{out}}_{e,s,t,b}
\le p_{g,s,t,b} \le
\overline{P}^{\text{out}}_{e,t,b}\, u^{\text{out}}_{e,s,t,b}
```

The two modes have separate binaries: a floor fires only while the
battery is in that mode (PLEXOS *Min Charge / Min Discharge Level*,
UC.jl minimum charge / discharge rate).  A single shared $u$ would force
both floors at once whenever the battery operates.

**Charge/discharge exclusivity constraint** (row
`charge_discharge_exclusive`, `Battery.exclusive_charge_discharge`,
default `true` when unset) — the battery is never in both modes in the
same block:

```math
u^{\text{in}}_{e,s,t,b} + u^{\text{out}}_{e,s,t,b} \;\le\; 1
\qquad \forall \; s, t, b
```

With `exclusive_charge_discharge = false` the row is omitted and
simultaneous partial operation is limited only by the power envelope
$\ell/\overline{P}^{\text{in}} + p/\overline{P}^{\text{out}} \le 1$.  The
option is ignored when `commitment` is unset (no mode binaries;
`validate_planning` warns if it is set to `true` there).  In a
continuous phase both binaries are LP-relaxed and the row is still
emitted on the relaxed $u$'s; the floors then no longer bind (any
$p \in [0, \overline{P}]$ is reachable with $u = p/\overline{P}$).
<!-- source: source/system.cpp (expand_batteries), source/converter_lp.cpp -->

#### Fixed Storage Duration (Optional)

When `Battery.energy_power_ratio` $= H_e$ [h] is set and both axes have an
installed-capacity column, energy and power are built together:

```math
\bar{E}_{e,t} \;=\; H_e \cdot \bar{C}_{v,t}
\qquad \forall \; t
```

Without it the two axes are optimised independently, each at its own
annualised cost.

### 5.9 Reserve Constraints

#### Reserve Zone Requirements

For each reserve zone $z$, the total up-reserve provided must meet the
requirement:

```math
\sum_{p \in \mathcal{P}_z} \gamma_{p,t} \; r_{p,s,t,b}^{\text{up}} + q_{z,s,t,b}^{\text{up}} \;\geq\; \overline{R}_{z,t,b}^{\text{up}}
\qquad \forall \; z, s, t, b
```

```math
\sum_{p \in \mathcal{P}_z} \gamma_{p,t} \; r_{p,s,t,b}^{\text{dn}} + q_{z,s,t,b}^{\text{dn}} \;\geq\; \overline{R}_{z,t,b}^{\text{dn}}
\qquad \forall \; z, s, t, b
```

where:
- $\gamma_{p,t}$ is the provision factor at stage $t$ (typically 1.0)
- $q_z^{\text{up}}, q_z^{\text{dn}} \geq 0$ are reserve shortage
  (curtailment) variables
- $\overline{R}_{z,t,b}^{\text{up/dn}}$ is the reserve requirement (MW)

**Soft requirement and the objective constant.**  With a shortage cost
$c^{R}\_{z,t,b}$ (`urcost` / `drcost`, fallback
`model_options.reserve_shortage_cost`) the static requirement is built in
the equivalent *served-requirement* form: a column
$\rho\_{z,s,t,b} = \overline{R}\_{z,t,b} - q\_{z,s,t,b} \in [0, \overline{R}\_{z,t,b}]$
with objective coefficient $`-c^{R}\,\epsilon_{s,t,b}`$ ($\epsilon$ = the block
cost factor: probability × discount × duration) and the row
$`\sum_p \gamma_{p,t}\,r_{p,s,t,b} - \rho_{z,s,t,b} \geq 0`$.  Substituting
$\rho = \overline{R} - q$ gives the shortage form above plus the constant
$`+c^{R}\epsilon\,\overline{R}`$, which gtopt adds to the LP-external
objective constant (`LinearProblem::add_obj_constant`, the same mechanism as
the demand-fail baseline):

```math
c^{R}\epsilon\,\overline{R}_{z,t,b} \;-\; c^{R}\epsilon\,\rho_{z,s,t,b}
\;=\; c^{R}\epsilon\, q_{z,s,t,b}
```

so the reported objective (`get_obj_value()`, SDDP upper/lower bounds) is
the real operating cost: a met requirement contributes $0$, a short one
$`c^{R}\epsilon\,q`$.  The LP rows, columns, costs and bounds are untouched
(dispatch and duals are bit-identical to the form without the constant);
the constant rides on the solver's native objective offset, is scaled by
`scale_objective` like every column cost, and is reported per LP as
`objective.constant` in the LP-scales JSON.  The SDDP feasibility
(elastic) clone zeroes the constant; optimality cuts carry it as a
state-independent intercept, exactly like the demand-fail baseline.  The
plane-based (dynamic) requirement below has a *variable* requirement $R$
and prices only the explicit shortage column $s \geq 0$, so it has no
valuation term and needs no constant.  A hard requirement (no cost) moves
$\overline{R}$ to the row RHS and has no column at all.
<!-- source: source/reserve_zone_lp.cpp (add_requirement) -->

#### Reserve–Generator Coupling

Up-reserve provision is limited by the generator's headroom:

```math
p_{g,s,t,b} + r_{p,s,t,b}^{\text{up}} \;\leq\; \bar{C}_{g,t}
\qquad \forall \; p \in \mathcal{P},\; s, t, b
```

With a `Commitment` on the generator the up
row is gated by the status $u$: $`p + r^{\text{up}} \le \bar{P}\,u`$.
When the generator is expandable the headroom row has the capacity-column form
$p + r^{\text{up}} - \bar{C}_{g,t} \le 0$; it is kept and a separate
u-gated row $`p + \sum_p r_p^{\text{up}} - \bar{P}\,u \le 0`$ is added
($\bar{P}$ = generation column upper bound, the same big-M as the
`gen_upper` row), so a de-committed unit offers no reserve.
<!-- source: source/reserve_provision_lp.cpp
(gate_reserve_provisions_by_status) -->

Down-reserve provision is limited by the generator's operating range:

```math
p_{g,s,t,b} - r_{p,s,t,b}^{\text{dn}} \;\geq\; \underline{P}_{g,t,b}
\qquad \forall \; p \in \mathcal{P},\; s, t, b
```

With a commitment the floor becomes the committed minimum gated by the
status: $`p - r^{\text{dn}} - \underline{P}^{c}_{g,t,b}\,u \ge 0`$, with
$\underline{P}^{c} = \max(\underline{P}_g, \texttt{Commitment.pmin})$ for a
`Commitment` (both formulations).  Offline (cold) up reserve
becomes $r^{\text{up}} \le \overline{R}^{\text{up}}(1-u)$ and the
`urmin` / `drmin` floors $`r - \underline{R}\,u \ge 0`$.

#### Native BESS Reserve Deliverability (opt-in)

When a battery $e$ sets `reserve_sustain_hours` $= T_e \ge 0$, the two
generic rows above are **not** emitted for the provisions of its
synthetic discharge generator.  Instead the linking converter adds, per
block, with $\mathcal{P}\_e$ the provisions of that generator,
$p^{\text{out}}$/$p^{\text{in}}$ the discharge/charge columns,
$e\_{e,s,t,b}$ the end-of-block SoC and $\overline{P}$, $\underline{E}$,
$\overline{E}$ the hard column bounds:

```math
p_{e,s,t,b}^{\text{out}} - p_{e,s,t,b}^{\text{in}} +
\sum_{p \in \mathcal{P}_e} r_{p,s,t,b}^{\text{up}}
\;\leq\; \overline{P}_{e,t,b}^{\text{out}}
\qquad\text{(\texttt{reserve_up_headroom})}
```

```math
p_{e,s,t,b}^{\text{in}} - \kappa_e\, p_{e,s,t,b}^{\text{out}} +
\sum_{p \in \mathcal{P}_e} r_{p,s,t,b}^{\text{dn}}
\;\leq\; \overline{P}_{e,t,b}^{\text{in}}
\qquad\text{(\texttt{reserve_dn_headroom})}
```

and, when $T_e > 0$,

```math
e_{e,s,t,b} - \frac{T_e}{\eta_{e,t,b}^{\text{out}}}
\sum_{p \in \mathcal{P}_e} r_{p,s,t,b}^{\text{up}}
\;\geq\; \underline{E}_{e,t,b}
\qquad\text{(\texttt{reserve_up_energy})}
```

```math
e_{e,s,t,b} + T_e\,\eta_{e,t,b}^{\text{in}}
\sum_{p \in \mathcal{P}_e} r_{p,s,t,b}^{\text{dn}}
\;\leq\; \overline{E}_{e,t,b}
\qquad\text{(\texttt{reserve_dn_energy})}
```

$\kappa_e = 1$ when `reserve_discharge_credit` is true (full inverter
range: a discharging battery can also regulate down by cutting its
output), $\kappa_e = 0$ otherwise (default, charge-side headroom only).
The SoC rows act on the post-self-discharge SoC $e_{e,s,t,b}$ of §5.7.

**Price property.**  No reserve column appears in any bus balance
(§5.1), so a reserve price $\pi_z \ge 0$ reaches a bus price
$\lambda_n$ only through the battery's own dispatch columns.  With row
duals $\mu^{\text{uh}}, \mu^{\text{ue}} \ge 0$, the up rows contribute
$+\mu^{\text{uh}}$ to the reduced cost of $p^{\text{out}}$ and
$-\mu^{\text{uh}}$ to that of $p^{\text{in}}$ (and $\mu^{\text{ue}}$ raises
the value of stored energy): they penalise injection and reward
withdrawal, so they can only *raise* $\lambda_n$.  The default down
headroom ($\kappa_e = 0$) only penalises withdrawal and never rewards an
injection.  Hence, at a bus with a free-disposal resource (a curtailable
injection or a generator above its minimum), $\lambda_n \ge 0$ is
preserved; a negative price can arise only from a pre-existing
non-disposable injection, or — with $\kappa_e = 1$ or a binding
`reserve_dn_energy` row in a pocket without disposal — from a genuine
physical shortage of down capability.

By contrast, the CEN PLEXOS formulation (`BAT_*_CF_GEN_COMP`) adds an
auxiliary battery whose discharge $g^{\text{aux}}$ is pinned to
$`\alpha\, r^{\text{dn}}`$ and enters the bus balance.  With $r^{\text{dn}}$
and $g^{\text{aux}}$ basic, the KKT conditions give
$\lambda_n = (c^{\text{dn}} - \pi_z)/\alpha$: the bus price is the
down-reserve price, amplified by $1/\alpha$ and with negative sign, even
when the battery itself has spare headroom; and the auxiliary tank
creates (or, on the up side, destroys) $`\alpha\,r`$ MWh per hour.
See `docs/studies/sscc_battery_constraint.md` and
`test/source/test_bess_reserve_native.cpp`.

**Per-side response caps** (PLEXOS `Max Response` / `Max Charge
Response`).  When a provision of the battery sets `urmax_response`,
`urmax_charge_response` (or the `dr` analogues), every provision of
that direction is split $r_p = r_p^{\text{dis}} + r_p^{\text{chg}}$
(`reserve_{up,dn}_split`), each part bounded by its cap
($\le \overline{M}_p^{\text{dis}}$, $\le \overline{M}_p^{\text{chg}}$) and
by its own side's headroom:

$$
p^{\text{out}} + \sum_p r_p^{\text{up,dis}} \le \overline{P}^{\text{out}},
\quad
\sum_p r_p^{\text{up,chg}} \le p^{\text{in}},
\quad
\sum_p r_p^{\text{dn,dis}} \le p^{\text{out}},
\quad
p^{\text{in}} + \sum_p r_p^{\text{dn,chg}} \le \overline{P}^{\text{in}}
$$

(the down discharge side exists only with `reserve_discharge_credit`).
The split columns and rows belong to each provision (LP label
`ReserveProvision`, provision uid; output
`ReserveProvision/reserve_{up,dn}_{discharge,charge}_sol`), so a battery
with several capped services (CPF, CSF, CTF) keeps one pair per
provision while the side rows sum over all of them.  Summed, these imply
the aggregate headroom rows, which stay.  Test: a
40/40 MW BESS on a 60 MW up requirement covers 50 with a 10 MW
charge-side cap (shortage 10), and needs a 35 MW charge with a 25 MW
discharge-side cap.

**Expected deployment energy** (`reserve_energy_usage_up/down`
$= a^{\uparrow}, a^{\downarrow}$ [MWh/MW/h] on the Battery, overridden per
provision by `ReserveProvision.ur_energy_usage` / `dr_energy_usage`, so
$a$ is per provision: PLEXOS uses 0.3 for CSF and 0 for CPF/CTF).  The SoC
balance (§5.7) gets
the same coefficient as $a$ MW of discharge per MW of up reserve and $a$
MW of charge per MW of down reserve:

```math
e_b = e_{b-1} + \eta^{\text{in}} \Delta_b\, (p^{\text{in}} +
a^{\downarrow} \textstyle\sum r^{\text{dn}}) - \frac{\Delta_b}{\eta^{\text{out}}}
(p^{\text{out}} + a^{\uparrow} \textstyle\sum r^{\text{up}})
```

This is the physical counterpart of the PLEXOS `CF_GEN_COMP` /
`CF_LOAD_COMP` AUX (which injects $`a\,r`$ into the bus balance): the
energy moves the battery's own SoC and nothing enters a bus balance, so
the price property above is kept.  Test: $a^{\uparrow} = 0.5$,
$\eta^{\text{out}} = 0.8$, $r^{\text{up}} = 60$ lowers an idle SoC from
100 to 62.5; $a^{\downarrow} = 0.5$, $\eta^{\text{in}} = 0.9$,
$r^{\text{dn}} = 40$ raises it to 118.

**CEN PCP BESS mapping** (2026-11-07, e.g. `BAT_ALFALFAL_VR2`):

| PLEXOS | Structure | gtopt |
|--------|-----------|-------|
| `UPStorageBound_B` | $-0.95E + r^{\uparrow} \le 0$ | `reserve_up_energy`, $T = 1$ |
| `DOWNStorageBound_B` | $E + 0.95 r^{\downarrow} \le 177$ | `reserve_dn_energy`, $T = 1$ |
| `B_CF_GEN_COMP` | $`-g^{\text{aux}} + 0.3\,r^{\text{CSF,LW}} = 0`$ (CPF/CTF coef 0) | per provision `dr_energy_usage` (SoC, no bus term) |
| `B_CF_LOAD_COMP` | $`-l^{\text{aux}} + 0.3\,r^{\text{CSF,RS}} = 0`$ | per provision `ur_energy_usage` |
| `B_AUX` battery | 99 999 MWh, ±1000 MW, η = 1 | dropped |
| Battery `Capacity` | 177 MWh, dated 6.6 (maintenance) | `Battery.emax` schedule |
| `B_CapacityUp/Down` | $`E \lessgtr 10\,000`$ | inactive (never binding) |
| `C*_BESS` `Max Response` / `Max Charge Response` | per reserve, CSV `*_MRU/MRD` | `ReserveProvision.{ur,dr}max_response`, `…_charge_response` |
| `C*_BESS` `Min Provision` + `CSF_*_MIN_B` | $r - 5u \ge 0$ | `urmin` / `drmin` |

#### Dynamic Requirement: Max of Planes (CEN CPF)

A zone can set `urreq_planes` / `drreq_planes` instead of the fixed
`urreq` / `drreq`.  The requirement is then a variable $R_{z,b}$ bounded
below by the maximum of $K$ affine planes: the CEN Chile primary-reserve
requirement (Minuta GM-DPRO Nº05-2026 [[20]](#ref20), Anexo B, eqs.
(B.2)–(B.4)).  For each direction and block,

```math
R_{z,b} \;\ge\; \alpha_{k,b} + \delta^{D}_{k,b}\, D_{z,b} +
 \delta^{I}_{k,b}\, I_{z,b} + \delta^{C}_{k,b}\, C_{z,b} +
 \delta^{F}_{k,b}\, F_{z,b}
\qquad \forall\, k = 1..K \qquad (\texttt{urplane}_k)
```

```math
R_{z,b} \;\ge\; \max\{0,\ \overline{R}_{z,b},\ \underline{R}_{z,b}\}
\qquad\text{(column lower bound: \texttt{urreq} / \texttt{urmin} floors)}
```

```math
\sum_{p \in \mathcal{P}_z} \gamma_{p}\,\phi_{p,z}\, r_{p,b} +
 q_{z,b} - R_{z,b} \;\ge\; 0,
\qquad q_{z,b} \ge 0 \text{ priced at } c^{\text{rfail}}_z
\qquad (\texttt{urequirement})
```

with the terms

| Symbol | Meaning | Kind | JSON |
|--------|---------|------|------|
| $D_{z,b}$ | system load, $\ell \sum_{d} \bar{\ell}_{d,b}$ or a given value | **parameter** | `demand_load_factor`, `demands`, `system_load` |
| $I_{z,b}$ | online inertia $\sum_g H_g S_g u_{g,b}$ [MWs] | variable (commitment) or parameter | `inertia_zone` (its provisions, §5.17), `inertia_term`, `system_inertia` |
| $C_{z,b}$ | contingency: $C \ge p_g\ \forall g \in \mathcal{G}^C$ (up), $C \ge \ell_d\ \forall d \in \mathcal{D}^C$ (down) | variable | `contingency_generators` / `contingency_demands` |
| $F_{z,b}$ | signed flow sum on a corridor (**couples dispatch**, see below) | variable | `flow_lines` |

The down direction is identical with `drreq_planes`, `drmin` and the
down contingency set.  Every plane coefficient is a per-(stage, block)
schedule, so a dated coefficient set (PLEXOS switched sets on 2024-01,
2025-01, 2025-04-16, 2026-02-11 and 2026-07) is one schedule per field.
The max over planes is exact in an LP: $R$ only appears in $R \ge \cdot$
rows and in the balance row, so the optimum sits on the largest plane.

**Why the load term must be exogenous.**  PLEXOS, and the minutas
since ISSCC 2022 [[21]](#ref21), write the load term as total
generation, $D = \sum_g p_g - \sum \text{charge}$
(`Generation_Calculation`), a *variable*.  Since $\delta^D_k < 0$, each
extra MWh served lowers the requirement by $|\delta^D_k|$ MW, and the
generation columns pick up a credit $`\sum_k |\delta^D_k|\,\mu_k`$, where
$\mu_k \ge 0$ are the duals of the binding plane rows.  A bus with
spare cheap energy then clears at

```math
\lambda_n \;=\; c_{\text{marg}} \;-\; \sum_k |\delta^{D}_k|\,\mu_k ,
```

which is negative whenever $c_{\text{marg}} \approx 0$ (curtailed
solar): the LP serves load only to shrink the reserve requirement.  In
PLEXOS PCP 2026-11-07 h14 the `Generation_Calculation` dual is
$-0.366193$ \$/MWh, exactly
$(10.75\cdot 12.114 + 20.64\cdot 9.421 + 4\cdot 10.378)/1000$ from the
three binding requirement duals (CPF+@5m, CPF+@10s plane XI, CPF−).

The requirement has to depend on the *forecast* load, which is how CEN
first defined it: Minuta GM Nº34-2020 (ISSCC 2021) [[22]](#ref22)
regressed on demand because "la demanda es un dato de entrada al
problema de optimización" (CPF+@10s planes $515.89 - 18.99 D - 4.06 I$
and $713.97 - 35.70 D - 5.77 I$, CPF+@5m $434.2 - 18.65 D$, in GW and
GVAs).  Minuta GM Nº35-2021 then switched to generation as a "variable
directa".  gtopt's default `load_term: "exogenous"` restores the 2021
criterion: $D_{z,b}$ moves to the right-hand side and no bus-balance
column enters a plane row, so a marginal MWh of demand changes neither
$R$ nor any reserve dual, and the LMP is the energy-only marginal cost.
The exogenous $D = \ell \sum_d \bar{\ell}_d$ uses `demand_load_factor`
$\ell$ = **1.05** by default: the CEN PCP generation / demand ratio is
1.025–1.032 historically and 1.044–1.052 on recent days, and the
default sits at the top of that range on purpose, over-sizing rather
than under-sizing the reserve.  The endogenous PLEXOS form keeps
$\ell = 1$ by default (`Generation_Calculation` has no factor).
The PLEXOS form is available as `load_term: "endogenous_generation"`
for reconciliation only (a free column
$`D = \ell\,(\sum_g p_g - \sum \text{charge})`$).  The contingency term $C$
can still raise the price at the largest unit's bus
($\lambda = c + \delta^C \mu$): that is a real N−1 security cost, not an
arbitrage, and since $\delta^C > 0$ it never pushes a price negative.

**The inertia term: commitment, exogenous or endogenous.**  The
inertia inside the CPF planes (not the InertiaZone minimum, §5.17, which
is always a commitment constraint) has three forms, chosen per zone by
`inertia_term`:

| `inertia_term` | $I_{z,b}$ | couples to |
|----------------|-----------|------------|
| `"commitment"` (default) | $`\sum_{c} I_c\, u_{c,b} + \sum_{c \in \mathcal{F}} I_c`$ | commitment status $u$ |
| `"exogenous"` | `system_inertia`$\_{b}$, else $\sum\_{c \in \mathcal{M}} I_c$ | nothing (data) |
| `"endogenous"` | $`\sum_p \mathrm{pf}_p\, r^H_{p,b}`$, $r^H\_{p,b} \le p\_{g(p),b}$ | dispatch $p$ |

$I_c = \mathrm{pf}\_c \cdot \min($`provision_max`$, p\_{\max})$
($= H_c S_c$ when pf defaults to $H S / P\_{\min}$) is the unit's inertia
when on, summed over
the InertiaProvisions of `inertia_zone`.  `"commitment"` is the CEN
definition (minuta Anexo B, (B.1): Inercia $`= \sum_c I_c\,u_c`$): $u_c$
is the unit's Commitment status; a unit with no
status column counts only when it is forced on ($\mathcal{F}$:
$p\_{\min} > 0$), never through its dispatch.  `"exogenous"` puts a
given schedule on the plane RHS; without `system_inertia` it uses the
conservative must-run sum ($\mathcal{M}$: units whose status is pinned
to 1 by `must_run` / `fixed_status`, or uncommitted units with
$p\_{\min} > 0$) and logs a warning.  Less inertia means a larger
requirement ($\delta^I \le 0$), so the fallback over-sizes the reserve.
`"commitment"` is the default.  On the CEN PCP 2026-11-07 day it
gives a realistic requirement — the must-run-only `"exogenous"`
fallback inflates it to 505 MW against 195 MW in PLEXOS — with the same
negative-price exposure as `"exogenous"` (515 vs 509 negative
bus-hours, none below $-0.1$ \$/MWh; a decomposition run puts the whole
negative credit on the load term).  **Known risk**: with a RELAXED
Commitment ($0 \le u \le 1$, the PCP setting), $u$ is itself an LP
variable bounded only by $p\_{\min} u \le p \le p\_{\max} u$: a cheap unit
behind a congested line can raise $u$ to $p / p\_{\min}$ and collect
$I_c |\delta^I| \mu / p\_{\min}$ per MWh — larger than the endogenous
credit, since $I_c / p\_{\min} > \mathrm{pf}$.  In the 3-bus case of
`test_reserve_inertia_term_default.cpp` relaxed `"commitment"` gives
6 negative bus-hours (min $-7.05$ \$/MWh) against 0 for `"exogenous"`;
with integer commitment both give 0 (the duals come from the fixed-$u$
LP).  `validate_planning` reports that channel (`reserve_coupling`) and
names the remedy: `inertia_term: "exogenous"` with a `system_inertia`
schedule, or an integer Commitment.
`"endogenous"` is the PLEXOS-compatible dispatch-based stamp: the
coupling row $p - r^H \ge 0$ lets a zero-cost unit earn
$`\mathrm{pf}\,|\delta^I|\,\mu`$ per MWh, and a curtailed hydro or solar
bus then clears at $`\lambda = -\mathrm{pf}\,|\delta^I|\,\mu < 0`$ (test:
pf = 10, $\delta^I = -0.0123$, $\mu = 100$ gives $\lambda = -12.3$,
while `"commitment"` and `"exogenous"` keep $\lambda = 0$).  The LP
works in GWs / GW for the $I$ and endogenous $D$ columns (column scale
$10^3$) so the plane coefficients ($\sim 10^{-5}$ per MWs) and the
definition rows stay within a narrow range; outputs are in MWs / MW.

**The corridor-flow term couples dispatch too.**  A plane with
`flow_coef` $\ne 0$ makes the requirement a function of an endogenous
line flow, the same mechanism as the generation load term routed
through the network: the flow column's reduced cost picks up
$\delta^F \mu$, which opens a spread of $\delta^F\mu$ between the two
ends of the corridor.  With a must-take injection on the exporting
side, its price becomes $\lambda_S - \delta^F \mu$ and can be
negative.  Test (`North special CPF`, 200 MW must-take solar in the
north, 10 \$/MWh gas in the south, reserve at 30 \$/MW, plane
$3 + F$): $\lambda_N = -20$, $\lambda_S = 10$; with the same 203 MW as
an exogenous `alpha`, $\lambda_N = \lambda_S = 0$.  The term is kept
for PLEXOS reconciliation, and `validate_planning` warns whenever a
plane sets `flow_coef` on a zone with `flow_lines`.  The decoupled form
is an exogenous requirement: a per-stage/block `alpha` schedule holding
the expected corridor flow or a fixed MW contingency on the corridor.
In the PCP the whole north-special chain (`SpecialCPFTxFlow`: Tx ≥ Σ
southward flow on Jadresic→Cumbres, LosChangos→Cumbres,
LosChangos→Jadresic; `SpecialCPF*North_Definition`: Req ≥ Tx + RHS;
`SpecialCPF/CSF/CTFNorth(LW)`: provision equalities with unit factors
0.2–0.69) has `Include in ST Schedule = 0`; its dated RHS values (3,
−19.36, 163, 100.64, 101) cover only 2023-12-08/09, and no `Special*`
row has a dual in the 2024–2026 history.  A time-varying north
requirement is therefore a dated `alpha` schedule (test: `[[103],
[-19.36]]` gives R = 103, then 0 after the floor).

**Unit convention.**  CEN publishes $\beta, \gamma$ per GW and GVAs
with the requirement in MW; gtopt's coefficients are per MW and MWs:
`demand_coef` $= -\beta/1000$, `inertia_coef` $= -\gamma/1000$,
`contingency_coef` $= \delta$, `alpha` $= \alpha$.

**CPF+@10s set (B.2), used by PLEXOS since 2026-03**
($`\text{REQ} \ge \alpha - \beta\,\text{Gen} - \gamma\,I + \delta\, \text{MaxInj}`$):

| k | $\alpha$ | $\beta$ | $\gamma$ | $\delta$ |
|---|---------:|--------:|---------:|---------:|
| I | 516.000 | 36.600 | 12.300 | 1.250 |
| II | 339.464 | 23.500 | 9.986 | 1.250 |
| III | 315.500 | 26.733 | 5.000 | 1.000 |
| IV | 184.750 | 17.071 | 3.886 | 1.000 |
| V | 249.160 | 20.640 | 2.414 | 0.732 |
| VI | 149.557 | 13.286 | 1.819 | 0.730 |
| VII | 468.500 | 36.600 | 13.300 | 1.450 |
| VIII | 291.964 | 23.500 | 10.986 | 1.450 |
| IX | 280.500 | 26.733 | 6.000 | 1.200 |
| X | 149.750 | 17.071 | 4.886 | 1.200 |
| XI | 219.160 | 20.640 | 3.164 | 0.928 |
| XII | 117.843 | 13.286 | 2.569 | 0.930 |

CPF+@5m: $`\text{REQ} \ge 4.125 - 10.75\,\text{Gen} + 1.0912\, \text{MaxInj}`$
(the printed (B.3) is superseded).  CPF−:
$`\text{REQ} \ge -80 - 4\,\text{Gen} + 0.96\,\text{MaxLoad}`$ (since
2026-07; MaxLoad over the listed BESS charges).  With the PLEXOS values
of 2026-11-07 h14 (Gen 9.643 GW, I 37.697 GVAs, MaxInj 300 MW,
MaxLoad 200 MW) the planes give 179.255 / 227.823 / 73.428 MW, the
PLEXOS requirements (test
`test_reserve_dynamic_requirement.cpp`).

**Contingency candidates.**  Each candidate $j$ of `urcontingencies`
(`drcontingencies`) gives one row

```math
C_{z,b} \;\ge\; \sum_{g} a_{jg}\, p_{g,b} + \sum_{d} a_{jd}\, \ell_{d,b} +
 \sum_{l} a_{jl}\, f_{l,b} + o_{j,b}
 \;\Big[\, + \sum_{g} a_{jg} \sum_{p \in \mathcal{P}_{g,z}} r_{p,b}
 \Big]
```

and `contingency_generators` / `contingency_demands` are singleton
candidates.  This is the PLEXOS `<unit>_Comparison` row exactly: in PCP
2026-11-07 every row is
`MaxInjection_SEN − Σ_{modes} p − limited_generation_group ≥ RHS`
(e.g. `NEHUENCO_1_GNL`: 21 mode generators, RHS 8.4; `TxJadresic`: the
flow of `Jadresic500->Jadresic220`; `BAT_*_ComparisonUp`: battery
generation), with `limited_generation_group` = Σ PMGD generation − BESS
charge.  The bracketed term (the lost unit also loses the reserve it
carries, $C \ge p + r$) is added only with
`contingency_includes_reserve: true`; PLEXOS PCP has no provision term
in any `*_Comparison` row, so it is off by default.  With it on, a unit's
own reserve raises the requirement one-for-one and has no net value for
that zone (test: 200 MW of cheap headroom on the largest unit drops from
200 to 0 MW of provision).

**Equality balance (`requirement_sense: "eq"`).**  The balance row
becomes an equality, with a surplus column $e_{z,b} \ge 0$ priced at
`ursurplus_cost` when given:

```math
\sum_{p} \gamma_p\,\phi_{p,z}\, r_{p,b} + q_{z,b} - e_{z,b}
 \;=\; R_{z,b} \;(\text{or } \overline{R}_{z,b})
```

This is PLEXOS `CPF_UpMinProvision` / `CPF_DownMinProvision` (Sense 0:
provision + VoRS (10 000 \$/MW) − Requirement − Slack (1 \$/MW) = 0).
With a fixed requirement it pins the provision (no surplus cost) or
prices the excess (test: a 150 MW floor offer on a 100 MW requirement
gives 50 MW surplus).  With planes, $R$ is a free column above its
planes, so the excess lifts $R$ at no cost: `"eq"` is then equivalent
to `"ge"`, exactly as in PLEXOS (whose requirement variable costs 0),
and no surplus column is built (`validate_planning` warns when
`ursurplus_cost` / `drsurplus_cost` is set on such a zone).  The CEN
`CSF_{RS,LW}_Def` rows are not
requirements but definitions (`CSF_RS_Requirement` := Σ provision,
used by `MAXCSF_*` caps); they map to a UserConstraint or PAMPL
expression, not to `requirement_sense`.

**Outputs (planes mode).**  Besides `urequirement_sol` ($R$) and
`urequirement_dual` ($\mu$, the reserve price of the balance row),
`ReserveZone/` gets `urplane<k>_dual` (dual $\mu_k$ of plane row $k$,
$k = 1..24$), `urplane_binding_sol` (1-based index of the plane with
the largest value at the solution, 0 when the floor sets $R$),
`urshortage_sol`, `urcontingency_sol`, `inertia_sol` and, in the
PLEXOS-compat mode, `dsys_sol`; the down direction uses `dr*`.  Since
$\sum_k \mu_k = \mu$ on the binding planes, the marginal requirement
cost of any plane term $x$ is $\sum_k \delta^x_k \mu_k$; e.g. the
`Generation_Calculation` credit of the PLEXOS form is
$`\sum_z \sum_k \beta_{z,k}\,\mu_{z,k}/1000`$, read directly from the
plane duals.

**Requirement coupling and the reserve credit.**  A requirement that
depends on an endogenous column $x$ puts $\sum_k \delta^x_k \mu_k$ into
the reduced cost of every column that defines $x$, and from there into
the LMP of every bus those columns inject at.  `validate_planning`
reports each such channel of a zone with planes — `load_term:
"endogenous_generation"`, `flow_coef` on `flow_lines`, `inertia_term:
"endogenous"`, `inertia_term: "commitment"` when any Commitment is
relaxed, line contingency candidates (signed flow) and
`contingency_includes_reserve` — with a warning that it can produce a
negative LMP credit ($`\beta\,\mu`$ per MWh).  `model_options.
reserve_coupling` = `"warn"` (default) / `"error"` / `"off"` selects the
handling.  The generator contingency $`C \ge a\,p_g`$ is not reported: it
raises the largest unit's cost ($\delta^C > 0$).  The commitment
inertia is reported only with a relaxed Commitment: an integer $u$ is
fixed when the duals are computed, but a continuous $u$ is tied to the
dispatch by $p_{\min} u \le p$.

The credit is written natively per zone and block:

```math
\texttt{reserve_credit}_{z,b} = \ell_z \sum_{k} \delta^D_{k,b}\,\mu_{k,b}
\quad [\$/\text{MWh}],
\qquad
\texttt{flow_credit}_{z,b} = \sum_{k} \delta^F_{k,b}\,\mu_{k,b},
```

```math
\texttt{credit_transfer}_{z,b} = \texttt{reserve_credit}_{z,b}
 \cdot \frac{D_{z,b}}{\ell_z}\,\Delta t_b \quad [\$].
```

`reserve_credit` is the uniform shift of every bus LMP produced by the
endogenous load term ($D$ counts all generation, so the same value at
every bus; the system total over zones is $\sum_z$
`reserve_credit`$_z$); it is 0 with the default exogenous load term.
`flow_credit` is the extra spread $\lambda_B - \lambda_A$ across each
`flow_lines` corridor.  `credit_transfer` is the money moved through
the credit (credit × generated energy).  Test: plane
$`516 - 0.0366\,D`$, $\ell = 1.025$, $\mu = 20$: `reserve_credit`
$= -0.0366 \cdot 1.025 \cdot 20 = -0.7503$ \$/MWh, equal to the LMP shift of
the marginal 60 \$/MWh unit; 0 in the exogenous case.

#### Extended Provision Model (CEN SSCC)

- **Effectiveness factors** (minuta (A.3)–(A.12)): one up column $r_p$
  enters each zone $z$ of `reserve_zones` with $`\gamma_p\,\phi_{p,z}`$,
  $\phi_{p,z}$ = `ur_zone_factors[z]` (default 1; 0 skips the zone).
  A single CPF+ variable serves the 5 min, 10 s and normal-operation
  requirements with unit-specific factors.
- **Shared headroom** (A.23)–(A.24): provisions of one generator
  flagged `shared_headroom` share one pair of rows
  $p + \sum_p r^{\text{up}}_p \le \bar{P}u$ and
  $p - \sum_p r^{\text{dn}}_p \ge \underline{P}u$ (CPF + CSF + CTF).
  Batteries use the native rows above (`reserve_sustain_hours`).
- **Offline (cold) reserve** (A.19)–(A.22), CTF: `offline` drops the
  headroom row and, with a Commitment, adds
  $`r^{\text{up}} + \bar{r}\,u \le \bar{r}`$, i.e. $r \le \bar{r}(1-u)$.
- **Symmetry** (A.13): `symmetric` adds $r^{\text{up}} - r^{\text{dn}} = 0$.
- **Inertia minima** (B.1): an InertiaZone requirement (§5.17).  The
  Norte Grande minimum that depends on the number of Guacolda units
  online uses `requirement_credits` (§5.17, "Commitment-dependent
  relief"), exactly as PLEXOS (`InertiaNRMin_D`,
  `InertiaCommitGuacolda2`).

#### Mapping: Minuta / PLEXOS → gtopt

| Minuta eq. | PLEXOS family | gtopt element.field |
|------------|---------------|---------------------|
| (B.2) | `CPF_Up10Calculation_I..XII` | `ReserveZone.urreq_planes` (12 planes) |
| (B.3) | `CPF_Up5Calculation` | `ReserveZone.urreq_planes` (1 plane) |
| (B.4) | `CPF_DownCalculation` | `ReserveZone.drreq_planes` |
| — | `Generation_Calculation` | `load_term`, `demand_load_factor` / `system_load` |
| (B.1) | `Inertia_Calculation_e1/e2` | `ReserveZone.inertia_zone` + `InertiaProvision` |
| (B.1) zonal | `InertiaNRMin_D/_N`, `InertiaCommitGuacolda(2)`, DV `InertiaGuacolda(2)` | `InertiaZone.requirement` + `requirement_credits` |
| — | `*_Comparison`, `BAT_*_Comparison(Up)` | `urcontingencies` / `drcontingencies` (or `contingency_generators` / `contingency_demands`) |
| — | `limited_generation_calculation` | members of every candidate (PMGD gens coef 1, BESS charge coef −1) |
| (A.1)–(A.2) | `CPF_{Up,Down}MinProvision`, `CPFN_*` | `ReserveZone.urmin` / `drmin` / `urreq` |
| (A.3)–(A.12) | `CPF_<unit>` coefficients | `ReserveProvision.ur_zone_factors` / `dr_zone_factors` |
| (A.13) | — | `ReserveProvision.symmetric` |
| (A.14)–(A.15) | Max Provision | `ReserveProvision.urmax` / `drmax` |
| (A.16)–(A.17) | `CSF_*_Def` | ReserveZone (`urreq`) + provisions |
| (A.18)–(A.22) | `CTF_*` | ReserveZone + provisions with `offline` |
| (A.23)–(A.24) | headroom rows | `ReserveProvision.shared_headroom`; Battery `reserve_sustain_hours` |
| — | `SpecialCPFTxFlow`, `SpecialCPF*North_Definition`, `SpecialCPF/CSF/CTFNorth(LW)` (all `Include in ST Schedule = 0` in PCP) | decoupled: a zone with a dated `alpha` schedule; PLEXOS-compat only: `flow_lines` + `flow_coef` |
| — | Reserve VoRS | `ReserveZone.urcost` / `drcost` (shortage) |
| — | `CPF_*MinProvision` Sense 0 + `*_Slack` | `requirement_sense: "eq"` + `ursurplus_cost` / `drsurplus_cost` |
| — | provision price | `ReserveProvision.urcost` / `drcost` |

#### Reserve Costs

- **Up-reserve provision cost**: $c_{p,t}^{\text{ur}} \cdot \omega_{s,t,b}$
  per MW of up-reserve provided
- **Down-reserve provision cost**: $c_{p,t}^{\text{dr}} \cdot \omega_{s,t,b}$
  per MW of down-reserve provided
- **Reserve failure cost**: $c_z^{\text{rfail}} \cdot \omega_{s,t,b}$
  per MW of unserved reserve (applies to both up and down shortages)

### 5.10 Hydro Cascade Constraints

The hydro cascade models a network of junctions (hydraulic nodes) connected
by waterways, with reservoirs for water storage. This formulation extends
classical hydrothermal coordination models [[5]](#ref5) [[6]](#ref6)
[[7]](#ref7) to support multi-scenario expansion planning.

#### Junction Water Balance

At each junction $j$, the water balance ensures conservation:

```math
\sum_{w \in \mathcal{W}_j^{\text{in}}} (1 - \lambda_w) \, \varphi_{w,s,t,b}
\;-\; \sum_{w \in \mathcal{W}_j^{\text{out}}} \varphi_{w,s,t,b}
\;+\; \sum_{f \in \mathcal{F}_j^+} Q_f
\;-\; \sum_{f \in \mathcal{F}_j^-} Q_f
\;+\; \sum_{r \in \mathcal{R}_j} d_{r,s,t,b}
\;-\; \sum_{u \in \mathcal{U}_j^{\text{intake}}} \tilde{\varphi}_{u,s,t,b}
\;+\; \sum_{u \in \mathcal{U}_j^{\text{tail}}} \tilde{\varphi}_{u,s,t,b}
\;-\; \delta_{j,s,t,b}
\;=\; 0
\qquad \forall \; j, s, t, b
```

where:
- $\lambda_w$ is the waterway transport loss factor
- $Q_f$ is a fixed exogenous inflow or outflow
- $d_{r,s,t,b}$ is the net extraction/injection from reservoir $r$
- $\tilde{\varphi}_{u,s,t,b}$ is the **built-in turbine flow** column (units
  m³/s) owned by turbine $u$ in built-in waterway mode, when the turbine's
  `junction_a` debits junction $j$ ($\mathcal{U}_j^{\text{intake}}$) and its
  optional `junction_b` credits junction $j$ ($\mathcal{U}_j^{\text{tail}}$).
- $\delta\_{j,s,t,b}$ is the **drain** column [m³/s], present only when
  `Junction.drain = true`: $0 \le \delta\_{j,s,t,b} \le \overline{\delta}\_j$
  (`drain_capacity`, default $+\infty$), with objective coefficient
  $c_j^{\text{drain}} \cdot \omega\_{s,t,b}$ (`drain_cost`, \$/(m³/s)·h,
  default 0).  The cost goes through the same block weight as
  `Waterway.fcost` and `Reservoir.spillway_cost` (probability × discount ×
  duration / `scale_objective`), so a costed junction drain and the
  equivalent `_ver` waterway to a free sink build the same LP.  Before
  2026-11 `drain_cost` entered the objective raw (no duration, probability
  or discount).
  <!-- source: source/junction_lp.cpp (JunctionLP::add_to_lp) -->

**Built-in waterway turbine.** When a `Turbine` element sets `junction_a`,
the turbine owns its own per-block flow column and the separate `Waterway`
element can be omitted (mode priority: `flow` > `junctions` > `waterway`).
The same conversion row `power = efficiency · production_factor · flow`
applies — only the source of `flow` changes.  When `junction_b` is unset,
the carried flow drains out of the system (terminal / run-to-sea plants —
no synthetic ocean junction needed).

**Outflow waterway.** A `Waterway` may also leave `junction_b` unset; it
then debits `junction_a` and drains the carried flow out of the modelled
system, mirroring the turbine's terminal-drain behaviour.  Useful for
spillage arcs (e.g. PLEXOS `Vert_*`) when the flow needs to remain
visible / accountable rather than collapsed onto a `Junction.drain`
column.

#### Autoregressive Inflows (opt-in, `Flow.inflow_model`)

By default the exogenous inflow $Q_f$ is data: the flow column is
pinned by bounds to the `discharge` schedule $\mu_{f,s,t,b}$.  When a
`Flow` carries `inflow_model = {type: "ar1", phi, sigma}`, the inflow
becomes an AR(1) state (NEWAVE / PSR-SDDP style): the column
$q_{f,s,t,b}$ is released to $(-\infty, \infty)$ and pinned instead by
the per-block recursion row

```math
q_{f,s,t,b} \;-\; \phi_f \, \ell_{f,s,t}
\;=\; \mu_{f,s,t,b} \;-\; \phi_f\,\mu_{f,s,t-1}^{\text{ref}}
\;+\; \varepsilon_{f,s,t}
\qquad \forall \; b,
```

where $\ell_{f,s,t}$ is the lagged inflow — the previous stage's
last-block column within a phase, or a dependent `inflow_lag` column
linked to the previous phase's registered `inflow` state variable at a
cross-phase (SDDP) boundary — and $\varepsilon = 0$ on the forward
path (the schedule is the realization; backward apertures rewrite the
row RHS with their own hydrology).  With the lag at its reference the
row reproduces $q = \mu$ exactly, so the deterministic LP is
value-identical to the pinned formulation; the gain is that SDDP
Benders cuts acquire $\partial V/\partial q_{t-1}$ coefficients.
Full derivation and cut-validity argument:
[`sddp-ar-inflows.md`](sddp-ar-inflows.md).

#### Waterway Flow Bounds

```math
\underline{Q}_{w,t} \;\leq\; \varphi_{w,s,t,b} \;\leq\; \overline{Q}_{w,t}
\qquad \forall \; w, s, t, b
```

#### Reservoir Volume Balance

Reservoir volume dynamics follow the same storage template as batteries:

```math
v_{r,s,t,b} = v_{r,s,t,b-1} \cdot (1 - \mu_r^h \, \Delta_b)
\;-\; \alpha_r \, d_{r,s,t,b} \cdot \Delta_b
\;-\; \alpha_r \, \text{spill}_{r,s,t,b} \cdot \Delta_b
```

where:
- $\mu_r^h$ is the hourly evaporation/seepage loss rate
- $\alpha_r$ is `flow_conversion_rate` [hm³/((m³/s)·h)]; an unset value
  resolves to $0.0036$ (before 2026-11 the JSON path fell back to $3.6$)
- $d_{r,s,t,b}$ is the net extraction from the reservoir [m³/s]
  (positive = water leaving for turbines)
- $\text{spill}\_{r,s,t,b}$ is the spillway discharge [m³/s]; the column
  exists only when `spillway_cost` is set (0 = free spill), bounded by
  `spillway_capacity` and costed at
  $c\_{r,t}^{\text{spill}} \cdot \omega\_{s,t,b}$ (\$/(m³/s)·h)

**Sign of the water value.**  The water value (dual of this row, in
\$/hm³) is zero while a full reservoir spills through a free exit.  When
every exit is priced at $c$ \$/(m³/s)·h, one more hm³ costs
$c / \alpha_r$ to discharge, so the water value of a spilling reservoir
is $-c/\alpha_r$: negative water values are a legitimate result, and
SDDP carries them through its cuts (`cases/reservoir_water_value`).
<!-- source: include/gtopt/storage_lp.hpp (StorageBase::add_to_lp),
     include/gtopt/reservoir_lp.hpp (flow_conversion_rate) -->

#### Volume Bounds

```math
\underline{V}_{r,t} \;\leq\; v_{r,s,t,b} \;\leq\; \overline{V}_{r,t}
\qquad \forall \; r, s, t, b
```

with initial volume $v_{r,s,t_0,b_0} = V_r^0$ and optional final volume
target.

#### Turbine Power Conversion

Each turbine $u$ links a water-source flow column $q_{u,s,t,b}$ to a
generator $g$ via a water-to-power conversion:

```math
p_{g,s,t,b} = \kappa_u \cdot q_{u,s,t,b}
\qquad \forall \; u, s, t, b
```

or, when the turbine allows partial water bypass (drain mode):

```math
p_{g,s,t,b} \;\leq\; \kappa_u \cdot q_{u,s,t,b}
\qquad \forall \; u, s, t, b
```

The flow source $q_{u,s,t,b}$ depends on the turbine's connection mode:

- `flow` mode (highest priority): $q_{u,s,t,b} = Q_{f,s,t,b}$ where $f$
  is the referenced `Flow` element's fixed schedule.
- **built-in waterway** mode (`junction_a` set):
  $q_{u,s,t,b} = \tilde{\varphi}_{u,s,t,b}$, the turbine's own per-block
  flow column.  This column enters the upstream junction's balance as
  an outflow and (when `junction_b` is set) the downstream junction as
  an inflow — see the Junction Water Balance above.
- `waterway` mode (legacy): $q_{u,s,t,b} = \varphi_{w,s,t,b}$ where $w$
  is the referenced `Waterway`.

Fail-closed (§5.8): when the turbine is inactive, its junction is
inactive, or the flow / waterway column $q_{u,s,t,b}$ does not exist
(inactive, no data, or elided at $\underline{\varphi} = \bar\varphi = 0$),
the generation $p_{g,s,t,b}$ is closed ($\le 0$), never left free.

##### Volume-Dependent Efficiency

In practice, the conversion rate $\kappa_u$ is not constant — it depends
on the hydraulic head, which is a function of the reservoir volume.  When
a `ReservoirEfficiency` element is defined for a turbine, the solver
replaces the static $\kappa_u$ with a volume-dependent function.

The efficiency is modelled as a **concave piecewise-linear envelope**
parameterised by $N$ segments:

$$
\kappa_u(v) = \min_{i=1}^{N} \lbrace c_i + m_i \cdot (v - v_i) \rbrace
$$

where for each segment $i$:

| Symbol | JSON field  | Meaning |
|--------|-----------|---------|
| $v_i$  | `volume`  | Volume breakpoint [dam³] |
| $m_i$  | `slope`   | Marginal efficiency change per dam³ |
| $c_i$  | `constant`| Efficiency intercept at breakpoint $v_i$ |

The result is clamped to zero: $\kappa_u(v) \ge 0$.  The fallback
`mean_production_factor` $\bar{\kappa}_u$ is used when the solver cannot modify
LP matrix coefficients in-place.

**SDDP integration**: during the forward pass, the solver evaluates
$\kappa_u(v_{r}^{(k)})$ using the reservoir volume $v_{r}^{(k)}$ from
iteration $k$ and updates the LP matrix coefficient via
`set_coeff(row, col, -kappa_u)` (i.e., $-\kappa_u$).  For the **first iteration**
($k{=}1$), the initial volume $v_{\text{ini}}$ is used for all phases
and scenes.

#### Filtration (Water Seepage)

Filtration models seepage from a waterway $w$ into a reservoir $r$ as a
function of the reservoir's average volume:

```math
\varphi_{i,s,t,b} = a_i + b_i \cdot \frac{v_{r,s,t,b-1} + v_{r,s,t,b}}{2}
\qquad \forall \; i, s, t, b
```

where $a_i$ is the constant term (RHS) and $b_i$ is the slope (seepage rate
per unit volume).

**Piecewise-linear filtration (plpfilemb.dat / plpcenfi.dat segments).**
When a set of segments $\lbrace(V_k, b_k, c_k)\rbrace_{k=1}^{K}$ is provided, the
filtration function is defined by a concave envelope:

$$
\varphi(V) = \min_{k} \lbrace c_k + b_k \cdot (V - V_k) \rbrace
$$

At each phase, the active segment $k^*$ is selected based on the last known
reservoir volume $V^{\text{ini}}$ (from the previous phase or the initial
condition).  The LP constraint is then updated directly in the LP matrix:

- Coefficient on storage variables: $-b_{k^*} / 2$ on both $v_{b-1}$ and $v_b$
- Right-hand side: $a\_{k^\*} = c\_{k^\*} - b\_{k^\*} \cdot V\_{k^\*}$

To minimise overhead, `set_coeff` / `set_rhs` calls are dispatched only when
the new values differ from the previously applied ones.  This mechanism is
analogous to the piecewise-linear efficiency update for turbines (§5.10).

The PLP input for this model is `plpfilemb.dat`, parsed by `FilembParser` in
`scripts/plp2gtopt/filemb_parser.py`.  Volume breakpoints are stored in dam³
(convert from PLP Mm³ by multiplying by 1000); slopes are in m³/s per dam³
(convert from PLP /Mm³ by dividing by 1000); constants are in m³/s (no
conversion).  The legacy `plpcenfi.dat` file is also supported via
`CenfiParser`; `plpfilemb.dat` takes precedence when both are present.

### 5.10 bis Hydro Pump

A `Pump` element $h \in \mathcal{H}$ couples a waterway flow variable
$\varphi_{w,s,t,b}$ [m³/s] to the served load of a demand
$\ell_{d,s,t,b}$ [MW] representing the pump motor. Physically, the pump
must consume at least $\phi_h / \eta_h$ MW of electrical energy per m³/s
of water raised, where $\phi_h$ is the nominal pump factor and
$\eta_h \in (0,1]$ is the stage efficiency.

#### Conversion Constraint

```math
\begin{aligned}
\frac{\phi_{h,t}}{\eta_{h,t}} \; \varphi_{w,s,t,b} \;-\; \ell_{d,s,t,b} \;\leq\; 0
\qquad \forall \; h \in \mathcal{H},\; s,t,b
\end{aligned}
```
<!-- source: source/pump_lp.cpp:86-97 -->

Equivalently, $`\ell_{d,s,t,b} \geq (\phi_{h,t}/\eta_{h,t})\,\varphi_{w,s,t,b}`$:
the optimizer must supply enough electrical power through demand $d$ to
sustain the water flow pumped through waterway $w$.

Fail-closed (§5.8): when the pump is inactive, or the pump load
$\ell_{d,s,t,b}$ has no column (demand inactive or `lmax` = 0), the
pumping direction of the waterway is closed, $\varphi_{w,s,t,b} \le 0$
(a negative `fmin` is kept), instead of dropping the conversion row
(which lifted water for free).

#### Waterway Capacity (Optional)

When a pump capacity $\overline{Q}_{h,t}$ is specified for the stage, an
additional per-block cap is added on the waterway flow:

```math
\begin{aligned}
\varphi_{w,s,t,b} \;\leq\; \overline{Q}_{h,t}
\qquad \forall \; h \in \mathcal{H},\; s,t,b
\end{aligned}
```
<!-- source: source/pump_lp.cpp:99-112 -->

The waterway and demand referenced by the pump retain their native
contributions to the junction water balance (§5.10) and the bus power
balance (§5.1) respectively; the pump element adds only the coupling row
(and the optional capacity row).

### 5.10 ter Reservoir Discharge Limit

A `ReservoirDischargeLimit` element $\ell \in \mathcal{DL}$ imposes a
**volume-dependent cap** on the stage-average hourly turbine discharge,
selected from a piecewise-linear concave envelope indexed by reservoir
volume.  Exactly one of `waterway` / `turbine` must be set on the
element:

- `waterway`-referenced: the cap binds a classic `Waterway`'s flow
  column $\varphi_{w,s,t,b}$.
- `turbine`-referenced: the cap binds a built-in waterway turbine's
  own flow column $\tilde{\varphi}_{u,s,t,b}$ (the LP picks the
  source via `ReservoirDischargeLimitLP::uses_turbine()`).

#### Stage-Average Flow Variable

A free auxiliary variable $q^{\ell}_{s,t}$ represents the stage-average
hourly flow through $w$:

```math
\begin{aligned}
q^{\ell}_{s,t} \;-\; \sum_{b \in \mathcal{B}_t} \frac{\Delta_b}{T_t} \, \varphi_{w,s,t,b} \;=\; 0
\qquad \forall \; \ell,\; s,t
\end{aligned}
```
<!-- source: source/reservoir_discharge_limit_lp.cpp:76-92 -->

where $T_t = \sum_{b \in \mathcal{B}_t} \Delta_b$ is the stage duration.

#### Volume-Dependent Cap

Given piecewise segments $\lbrace(V_k^\ell, m_k^\ell, c_k^\ell)\rbrace\_{k=1}^{K}$
selected by the current reservoir volume $V$ (average of initial and
final stage volumes), the active segment $k^\*$ yields slope
$m\_{k^\*}^\ell$ and intercept $c\_{k^\*}^\ell$. The discharge cap then
reads:

```math
\begin{aligned}
q^{\ell}_{s,t} \;-\; \tfrac{1}{2} m_{k^*}^\ell\, v_{r,s,t,b_0} \;-\; \tfrac{1}{2} m_{k^*}^\ell\, v_{r,s,t,b_{\text{last}}} \;\leq\; c_{k^*}^\ell
\qquad \forall \; \ell,\; s,t
\end{aligned}
```
<!-- source: source/reservoir_discharge_limit_lp.cpp:94-109 -->

where $v_{r,s,t,b_0}$ / $v_{r,s,t,b_{\text{last}}}$ are the initial/final
reservoir volume columns (`eini`, `efin`) of the associated reservoir $r$.

Between SDDP iterations the solver re-selects $k^\*$ from the updated
forward-pass volume and patches both storage coefficients
(`-m_{k^*}/2` on `eini` and `efin`) and the RHS ($c\_{k^\*}^\ell$)
in place.
<!-- source: source/reservoir_discharge_limit_lp.cpp:133-188 -->

### 5.11 Capacity Expansion Constraints

Capacity expansion applies uniformly to generators, demands, lines,
batteries, and converters. The modular expansion structure follows the
standard GTEP approach [[1]](#ref1) [[2]](#ref2) [[3]](#ref3) [[10]](#ref10).
For each expandable component $g$ at stage $t$:

#### Installation Balance

```math
\bar{C}_{g,t} = \bar{C}_{g,t-1} \cdot (1 - \xi_{g,t})
\;+\; M_g \cdot m_{g,t}
\;+\; \Delta C_{g,t}
```

where:
- $\bar{C}_{g,t}$ is the installed capacity at stage $t$
- $\xi_{g,t}$ is the stage derating factor (capacity degradation)
- $M_g$ (`expcap`) is the capacity per expansion module
- $m_{g,t}$ is the number of modules installed at stage $t$
- $\Delta C\_{g,t} = C\_{g,t} - C\_{g,t-1}$ is the exogenous change of the
  `capacity` schedule $C$ between stages (also across SDDP phase
  boundaries).  At the first expandable stage there is no
  $\bar{C}\_{g,t-1}$ and the existing capacity enters whole,
  $\bar{C}\_{g,t} = C\_{g,t} + M_g \cdot m\_{g,t}$: existing capacity is never
  charged as a build.

#### Expansion Bounds

```math
0 \;\leq\; m_{g,t} \;\leq\; \overline{m}_{g,t}
\qquad \forall \; g, t
```

where $\overline{m}_{g,t}$ (`expmod`) is the maximum number of modules that
can be installed at stage $t$.

#### Maximum Capacity

```math
\bar{C}_{g,t} \;\leq\; \bar{C}_g^{\max}
\qquad \forall \; g, t
```

where $\bar{C}_g^{\max}$ is derived from `capmax` or
$\bar{C}_g^0 + M_g \cdot \overline{m}_g$.

#### Investment Cost Tracking

The annual investment cost is converted to an hourly rate and weighted by
the stage discount factor:

$$
\text{cost}(m_{g,t}) = \frac{K_g^{\text{cap}}}{8760} \cdot M_g \cdot
\frac{\delta_t \cdot T_t}{\sigma}
$$

where $T_t$ is the stage duration in hours.

#### Variable Type

By default, $m_{g,t}$ is a **continuous** variable (LP relaxation). When
`colint` is set on the component, $m_{g,t}$ becomes an **integer** variable
(MIP formulation).

### 5.12 User Constraints

In addition to the built-in constraints above, GTOpt supports
**user-defined linear constraints** that allow arbitrary linear
relationships between LP variables. User constraints are specified via
the `user_constraint_array` (inline) or `user_constraint_file`
(external JSON) fields in the planning input.

Each user constraint defines:
- A **sense** (`<=`, `>=`, or `=`)
- A **right-hand side** value, optionally a per-(stage, block)
  schedule (`rhs [v_0, v_1, ...]`) that overrides the inline scalar
  block-by-block
- A set of **coefficient entries** mapping (component class, element
  UID, variable name) triples to coefficients

Coefficients are scalar by default but may also be **per-block
profiles** (the symmetric LHS analog of the per-block `rhs` schedule).
A term written `[c_0, c_1, \dots] * x` contributes, in the row built
for block ordinal $b$ within the stage,

```math
c_{\min(b,\,K-1)} \cdot x_{s,t,b}
```

where $K$ is the profile length; the last entry broadcasts to every
trailing block, so a profile shorter than the stage's block count still
resolves. A plain scalar coefficient $c$ is the special case of a
length-one profile (equivalently $c_b = c$ for all $b$). The resolved
per-block coefficient is treated identically to a scalar by the
dual-output scaling and variable-descaling paths. This models PLEXOS
constraints whose `Generation Coefficient` / `Units Coefficient` varies
within the horizon.

User constraints are added to the LP after all built-in constraints and
can reference any LP variable (generator output, line flow, battery SoC,
etc.).

**Soft constraints.** A constraint with a `penalty` $c^{\text{uc}}\_k$
gets a non-negative slack $z\_{k,s,t,b}$ (two, $z^{+}$ and $z^{-}$, for an
equality) that relaxes the row; the penalty is \$ per row unit per hour
of violation and is weighted **exactly like every other cost**
(`CostHelper`):

$$
\text{per-block row:}\quad
\pi_s \cdot \delta_t \cdot \Delta_b \cdot \kappa_k \cdot c^{\text{uc}}_k
\cdot z_{k,s,t,b},
\qquad
\text{span row:}\quad
\pi_s \cdot \delta_t \cdot \kappa_k(H) \cdot c^{\text{uc}}_k \cdot z_{k,s,t}
$$

where $\kappa_k = 1$ for `penalty_class = raw` and $\kappa_k = 3600$
(\$/m³ → \$/(m³/s)·h) for `hydro_flow`.  A *span row* covers more than
one block — a `daily_sum` day row or a `stage` / `phase` / `global` scope
row — and is already built as a span total, so its slack carries no
further $\Delta_b$ ($`\kappa_k(H) = 3600\,H`$ for `hydro_flow` over the
$H$ hours of the span).  Per-block slacks read back like a generator cost
(÷ $\pi_s \delta_t \Delta_b$), span slacks with the Energy basis
(÷ $\pi_s \delta_t$), so the reported dual of a binding `power` soft row equals
$c^{\text{uc}}_k$.  (Before 2026-11 the slack carried the raw
$c^{\text{uc}}_k$, without probability, discount or duration.)

> **See also**: [`INPUT_DATA.md`](../input-data.md) for the JSON
> format specification of user constraints.

#### Nonanticipative global decision variables

A `scope: "global"` DecisionVariable $x$ is one column per (scene, phase)
LP, shared by the scenarios $s \in S_c$ of scene $c$; across scenes the
copies $x_c$ are independent (each scene is its own LP). With
`nonanticipative: true` the active scenes $c \in \mathcal{C}$ are built
as one extensive-form LP per phase, which imposes

```math
x_c = x \qquad \forall\, c \in \mathcal{C},
```

by construction (a single column), while every other variable keeps its
scenario index. The objective of the merged LP is

```math
\min \; \sum_{s \in \bigcup_c S_c} p_s \, \mathrm{OPEX}_s
  \;+\; c_x \Big( \sum_{s \in \bigcup_c S_c} p_s \Big)\, \delta_t \, x
```

for an `energy` cost $c_x$ (discount $\delta_t$; a `raw` cost is charged
once at face value), i.e. the decision is paid once with the total
probability mass instead of once per scene with $\sum_{s \in S_c} p_s$.
Scene-level columns (other coarse decision variables, capacity
expansion) become shared as well. In multi-phase runs $x$ is then carried
across phases as an implicit state variable.

### 5.13 Water Rights (FlowRight and VolumeRight)

Hydro-dominated systems often operate under legally-binding irrigation
agreements that partition turbine discharge into distinct rights
categories with volume-dependent allocations. GTOpt implements this via
two generic LP elements that live **in parallel** to the physical hydro
topology: `FlowRight` ($i \in \mathcal{FR}$) and `VolumeRight`
($o \in \mathcal{VR}$). They are optionally *consumptive* (coupled to
the physical junction/reservoir balances) or purely virtual (a separate
ledger).

See the companion white paper §5 (`sec:rights`) for the full domain
narrative; the equations below port the formal statements and tie each
to the LP assembly code.

#### 5.13.1 Flow Rights

A `FlowRight` creates one extraction flow column $\xi_{i,s,t,b}$
[m³/s] per block. Two operating modes are supported:

$$
\begin{aligned}
\underline{\xi}_{i,s,t,b} = \begin{cases}
\bar{\xi}_{i,s,t,b} & \text{fixed mode (default)} \\
0 & \text{variable mode: } \overline{F}_i > 0
\text{ and } \bar{\xi}_{i,s,t,b} = 0
\end{cases}
\end{aligned}
$$

$$
\begin{aligned}
\overline{\xi}_{i,s,t,b} = \begin{cases}
\bar{\xi}_{i,s,t,b} & \text{fixed mode} \\
\overline{F}_{i,t,b} & \text{variable mode}
\end{cases}
\end{aligned}
$$

Optionally capped further by a `bound_rule` (e.g. a volume-zone lookup):

```math
\begin{aligned}
\overline{\xi}_{i,s,t,b} \;\leftarrow\; \min\!\bigl(\overline{\xi}_{i,s,t,b},\; B_i(x)\bigr)
\end{aligned}
```

where $B_i(x)$ is the piecewise-linear rule evaluated on an axis $x$
(reservoir volume, calendar month, etc.).
<!-- source: source/flow_right_lp.cpp:46-69 -->

**Deficit / soft discharge.** When the per-block fail cost
$c_{i,s,t,b}^f > 0$ (either direct schedule or derived from the global
$c^{hf}$ via $c^{hf} \cdot \Delta_b \cdot 3600$) and a nonzero
discharge is scheduled, a deficit variable $\delta_{i,s,t,b} \geq 0$ is
created with lower bound on $\xi$ relaxed to $0$, and the **demand
coupling** row is added:

```math
\begin{aligned}
\xi_{i,s,t,b} \;+\; \delta_{i,s,t,b} \;\geq\; \bar{\xi}_{i,s,t,b}
\qquad \forall \; i \in \mathcal{FR},\; s,t,b
\end{aligned}
```
<!-- source: source/flow_right_lp.cpp:198-214 -->

**Use-value benefit.** A per-block `use_value`
$u_{i,s,t,b}$ ($\$/(m^3/s)$, inherited from $c^{hu}$ when unset)
contributes a *negative* coefficient $-u_{i,s,t,b}$ on the flow column —
a benefit that incentivizes non-zero flow in variable mode.
<!-- source: source/flow_right_lp.cpp:163-185 -->

**Stage-average auxiliary.** When `use_average = true`, a stage-level
variable $\bar{\xi}_{i,s,t}^{h}$ plus an equality row:

```math
\begin{aligned}
\bar{\xi}_{i,s,t}^{h} \;-\; \sum_{b \in \mathcal{B}_t} \frac{\Delta_b}{T_t} \, \xi_{i,s,t,b} \;=\; 0
\qquad \forall \; i,\; s,t
\end{aligned}
```
<!-- source: source/flow_right_lp.cpp:246-274 -->

**Consumptive coupling.** When a `junction_a` reference is set, each
block's flow column is injected with coefficient $-1$ into the junction
balance row of §5.10, subtracting the extraction from the physical
water balance.  With `consumptive = false` (carry mode) the same column
is also injected with $+1$ into `junction_b`'s balance row, so the
served flow returns to the river.
<!-- source: source/flow_right_lp.cpp (consumptive coupling) -->

**Opt-in pass-through bypass.** Only when explicitly requested
(`use_bypass = true`, or — legacy implicit opt-in — `bypass_cost` or
`bypass_fmax` set) and both `junction_a` ($j_a$) and `junction_b`
($j_b$) are set (per-block / stage-average modes), a bypass column

```math
0 \;\leq\; \beta_{i,s,t,b} \;\leq\; \overline{\beta}_i
\qquad (\overline{\beta}_i = \texttt{bypass_fmax},\ +\infty \text{ if unset})
```

enters $j_a$'s balance with $-1$ and $j_b$'s with $+1$, priced at
`bypass_cost` $`\cdot\,\Delta_b`$ (scaled like every block cost).  It is
reported as `FlowRight/bypass_sol`.  A bare `junction_b` builds no arc:
it opens no water path that the output does not show.
<!-- source: source/flow_right_lp.cpp (bypass) -->

#### 5.13.2 Volume Rights

A `VolumeRight` is a virtual storage (built on the `StorageLP` base)
that tracks the remaining rights volume $\nu_{o,s,t,b}$ [hm³]. The
conversion factor $\alpha_o = \text{fcr}_o$ has default value
$0.0036$ hm³/((m³/s)·h) so that an extraction rate in m³/s integrated
over one hour produces a volume decrement in hm³.

**Balance (StorageLP template specialised for rights).**

```math
\begin{aligned}
\nu_{o,s,t,b} \;=\; \nu_{o,s,t,b-1} \cdot (1 - \mu_o^h \Delta_b) \;+\; \alpha_o\,\Delta_b\,\xi_{o,s,t,b}^{\text{sav}} \;-\; \alpha_o\,\Delta_b\,\xi_{o,s,t,b}^{\text{in}}
\qquad \forall \; o \in \mathcal{VR},\; s,t,b
\end{aligned}
```
<!-- source: source/volume_right_lp.cpp:161-214 (StorageBase::add_to_lp) -->

where $\xi_{o,s,t,b}^{\text{sav}}$ is an optional *saving* (deposit)
column created when a `saving_rate` schedule is present
(economy/reserved rights).

**Extraction bounds.**

```math
\begin{aligned}
0 \;\leq\; \xi_{o,s,t,b}^{\text{in}} \;\leq\; \min\!\Bigl(\overline{F}_{o,t,b},\; \frac{\max(B_o(x),0)}{\alpha_o\,T_t}\Bigr)
\qquad \forall \; o,\; s,t,b
\end{aligned}
```

The rule $B_o(x)$ yields a **volume** [hm³] (the same quantity used by
the seasonal reset below); it is converted to a rate by the stage length
$T_t = \sum_{b\in\mathcal{B}_t}\Delta_b$ [h] and $\alpha_o$ (0.0036
hm³ per m³/s·h), i.e. "the whole rule volume spent in this stage".  $x$
is the reservoir volume at the start of the phase (predecessor phase's
`efin`, the case `eini` in the first phase) and is refreshed by
`update_lp` between SDDP iterations.
<!-- source: source/volume_right_lp.cpp:117-137 -->

**Optional stage demand.** When $D_o > 0$, a deficit
$q_{o,s,t}^{\text{fail}} \geq 0$ [hm³] with objective cost
$`\pi_s\,\delta_t\,c_o^f`$ (scenario probability × stage discount, no
duration: the deficit is a stage volume, like every other stage
penalty) is added with the stage-integrated satisfaction row:

```math
\begin{aligned}
\sum_{b \in \mathcal{B}_t} \alpha_o\,\Delta_b\,\xi_{o,s,t,b}^{\text{in}} \;+\; q_{o,s,t}^{\text{fail}} \;\geq\; D_{o,t}
\qquad \forall \; o,\; s,t
\end{aligned}
```
<!-- source: source/volume_right_lp.cpp:313-346 -->

**Seasonal reset.** When a `reset_month` is configured and the current
stage's calendar month matches, the initial-volume column is clamped to
the provisioned value for the new hydrological period:

```math
\begin{aligned}
\nu_{o,s,t,b_0} \;=\; \begin{cases}
B_o(x) & \text{if } \texttt{bound_rule} \text{ is set} \\
\overline{V}_{o,t} & \text{otherwise}
\end{cases}
\end{aligned}
```
<!-- source: source/volume_right_lp.cpp:230-237 -->

**Consumptive coupling.** When a `reservoir` reference is set, the
extraction column is injected with coefficient $`+\alpha_o\,\Delta_b`$
into the energy balance row of the linked reservoir (§5.10), debiting
the physical water storage. A `right_reservoir` link analogously
couples a child right to a parent rights ledger with coefficient
$`-d_o\,\alpha_o\,\Delta_b`$ in the parent's energy row, where
$d_o$ = `direction` ($-1$, the default, is a withdrawal that debits
the parent like the physical coupling above; $+1$ is a supply that
credits it).
<!-- source: source/volume_right_lp.cpp:278-307 -->

### 5.14 LNG Terminal

An `LngTerminal` element $\tau \in \mathcal{LNG}$ models an LNG storage
tank whose inventory is coupled to gas-fired generators through their
heat rates. The formulation reuses the `StorageLP` balance template
(§5.7) with the following specialisations:

- `finp` = delivery inflow column $d_{\tau,s,t,b}^{\text{lng}}$
  (fixed-rate across the stage: bounds set to $D_\tau/T_t$);
- `fout` = 0 (no direct outflow — generator consumption is injected
  into the balance row below);
- `drain` = venting column with penalty `spillway_cost`;
- `annual_standing_loss` = boil-off rate $\lambda_\tau$.

<!-- source: source/lng_terminal_lp.cpp:62-132 -->

#### Tank Volume Balance

```math
\begin{aligned}
e_{\tau,s,t,b}^{\text{lng}} \;=\;& e_{\tau,s,t,b-1}^{\text{lng}} \cdot (1 - \lambda_\tau^h\,\Delta_b) \\
& \;+\; \rho_\tau\,\Delta_b\,d_{\tau,s,t,b}^{\text{lng}} \\
& \;-\; \rho_\tau\,\Delta_b\,\text{vent}_{\tau,s,t,b} \\
& \;-\; \rho_\tau\,\Delta_b\,\sum_{g \in \mathcal{G}_\tau} \eta_g^{hr}\, p_{g,s,t,b}
\qquad \forall \; \tau,\; s,t,b
\end{aligned}
```
<!-- source: source/lng_terminal_lp.cpp:134-164 -->

where $\mathcal{G}\_\tau$ is the set of generators linked to terminal
$\tau$, $\eta_g^{hr}$ is the generator's heat rate in m³/MWh, and
$\lambda\_\tau^h = \lambda\_\tau / 8760$.

The last term is added directly to the existing energy balance row by
setting

```math
\text{coeff}(\text{balance}_b, \; p_{g,s,t,b}) \;=\; \rho_\tau \cdot \eta_g^{hr} \cdot \Delta_b
```

for each linked generator and each block.

#### Delivery Schedule

```math
\begin{aligned}
d_{\tau,s,t,b}^{\text{lng}} \;=\; \frac{D_{\tau,t}}{T_t}
\qquad \forall \; \tau,\; s,t,b
\end{aligned}
```
<!-- source: source/lng_terminal_lp.cpp:71-88 -->

#### Bounds, Vent and Costs

The deliveries are data: the column is fixed at the constant rate above
(none when $D\_{\tau,t} = 0$) and carries no cost, so the purchase cost
of a scheduled cargo is a constant outside the objective.  The vent
column exists only when `spillway_cost` $c^{\text{vent}}\_\tau$ is set,
with $0 \le \text{vent} \le \overline{\text{vent}}\_\tau$
(`spillway_capacity`, default 1000 m³/h).  The volume stays within the
per-block bounds
$\underline{V}\_{\tau,t,b} \le e^{\text{lng}}\_{\tau,s,t,b} \le \overline{V}\_{\tau,t,b}$
(`emin` / `emax`; the per-block floor is enforced while
`model_options.strict_storage_emin` is true, the default).  The
objective adds

```math
\pi_s\delta_t \sum_{b} \bigl( c^{e}_{\tau,t,b}\, e^{\text{lng}}_{\tau,s,t,b} +
  \Delta_b\, c^{\text{vent}}_\tau\, \text{vent}_{\tau,s,t,b} \bigr) +
  \pi_s\delta_t \bigl( c^{\text{sm}}_{\tau,t}\, s^{\text{sm}}_{\tau,s,t} +
  c^{\text{fin}}_\tau\, s^{\text{fin}}_{\tau,s} \bigr)
```

($c^e$ = `ecost`, charged once per block, not per hour) with the soft
floor at the end of each stage and the final target at the end of the
horizon:

$$
e^{\text{lng}}_{\tau,s,t,|\mathcal{B}_t|} + s^{\text{sm}}_{\tau,s,t} \ge V^{\text{sm}}_{\tau,t},
\qquad
e^{\text{lng}}_{\tau,s,T,|\mathcal{B}_T|} + s^{\text{fin}}_{\tau,s} \ge V^{\text{fin}}_\tau
$$

The soft-floor row exists only when `soft_emin` and `soft_emin_cost`
are both positive; the final-target slack only when `efin_cost` > 0
(otherwise the `efin` row is hard).  `sendout_min` / `sendout_max` are
part of the JSON schema but are **not** written into the LP: the
send-out $\sum_g \eta^{hr}_g p_g$ is bounded only by the generator limits.
<!-- source: source/lng_terminal_lp.cpp:66-136,
include/gtopt/storage_lp.hpp:835-1060 -->

#### State Variable

With `use_state_variable` (default true) the end-of-stage volume is
shared with the next stage inside a phase and, at a phase boundary, is
an SDDP state variable (the next phase's `sini` column is fixed to its
trial value; the cuts carry its value).  The elastic filter prices a
violation of that copy at `scost` (default
`model_options.state_violation_cost`) × `mean_production_factor`
(default 5 MWh/m³).  Without the state variable each stage is closed
with $e_{\text{end}} = e_{\text{ini}}$.
<!-- source: source/lng_terminal_lp.cpp:101-115,
include/gtopt/storage_lp.hpp:1052-1084 -->

#### Fuel Offtake Limits (Take-or-Pay)

A `Fuel` $f$ with price $P\_{f,t,b}$ gives each generator burning it
$`c_g = P_{f,t,b}\, h_g + c^0_g`$ (`heat_rate` × `price` + `gcost`).
`max_offtake` $\overline{O}\_{f,t}$ and `min_offtake`
$\underline{O}\_{f,t}$ bound the fuel burnt by the generators
$\mathcal{G}\_f$ of the fuel over each stage:

```math
\sum_{b}\sum_{g\in\mathcal{G}_f} h_g \Delta_b\, p_{g,s,t,b} - s^{\max}_{f,s,t} \le \overline{O}_{f,t},
\qquad
\sum_{b}\sum_{g\in\mathcal{G}_f} h_g \Delta_b\, p_{g,s,t,b} + s^{\min}_{f,s,t} \ge \underline{O}_{f,t}
```

The burn also includes the heat-rate segment legs and the no-load fuel
of a committed unit.  The slacks exist only when `max_offtake_cost` /
`min_offtake_cost` are set, at cost $\pi_s\delta_t c^{\max}\_f$ /
$\pi_s\delta_t c^{\min}\_f$ per fuel unit; otherwise the rows are hard.
With `max_offtake_per_block` / `min_offtake_per_block` the row is
written per block with right-hand side $\overline{O}\_{f,t}\Delta_b/T_t$
(resp. $\underline{O}\_{f,t}\Delta_b/T_t$) and one slack per block.  The
floor is a take-or-pay minimum purchase, the cap an allotment; neither
carries a shortfall or surplus to later periods (no make-up or
carry-forward bank).
<!-- source: source/fuel_lp.cpp:280-470, include/gtopt/fuel.hpp:206-305 -->

#### Value of Stored Gas

Let $\theta_{\tau,b}$ be minus the dual of the tank balance of block
$b$ (objective decrease per extra m³); the reported
`LngTerminal` energy-balance dual is
$\vartheta_{\tau,b} = \theta_{\tau,b}/(\pi_s\delta_t)$ [\$/m³] (sign already
flipped).
Stationarity of $e^{\text{lng}}_b$, of the vent column and of a linked
generator strictly between its bounds give

```math
\theta_{\tau,b} = (1-\lambda^h_\tau\Delta_{b+1})\,\theta_{\tau,b+1} -
  \pi_s\delta_t\, c^e_{\tau,t,b} + \nu^{\text{lo}}_{\tau,b} - \nu^{\text{up}}_{\tau,b},
\qquad
\vartheta_{\tau,b} \ge -c^{\text{vent}}_\tau/\rho_\tau,
\qquad
\lambda^{\text{bus}}_{n(g),b} = c_g + \rho_\tau\,\eta^{hr}_g\,\vartheta_{\tau,b}
```

so the dispatch cost of an LNG unit is the opportunity cost
$\rho_\tau\eta^{hr}_g\vartheta$, not the cargo price.  At the end of an
SDDP phase the volume is a cut state, and

```math
\theta_{\tau,\text{end}} = -\sum_k \upsilon_k\,\hat\mu_{k,\tau} -
  \pi_s\delta_t c^e_\tau + \nu^{\text{lo}} - \nu^{\text{up}}
```

with $\upsilon_k \ge 0$ the duals of the active cuts (summing to the
objective weight of $\alpha$) and $\hat\mu_{k,\tau} \le 0$ their slopes
on the tank volume (plus the `efin` dual in the last phase): the LNG
water value.  In a single LP without cuts or a final target the
leftover is worth nothing, and the LP burns or vents it before the end.

- **Take-or-pay cargo** (fixed delivery, no LP cost): only
  $\vartheta$ prices the gas: zero, or negative down to
  $-c^{\text{vent}}/\rho$, while the tank must be emptied before a
  deadline; up to the cost of the displaced generation when gas is
  short.
- **Purchasable gas** at price $c^{\text{sp}}$ through a priced inflow
  $x \ge 0$ of the tank: $\rho\vartheta \le c^{\text{sp}}$, with
  equality while $x > 0$.  `LngTerminal` has no such column; the
  converter band tanks below do.
- **Priced `Fuel` with offtake rows**: for a unit strictly between its
  bounds,
  $\lambda^{\text{bus}} = c^0_g + h_g(P_f - \hat\nu^{\min}_f + \hat\nu^{\max}_f)$
  with $\hat\nu = \nu/(\pi_s\delta_t)$.  A binding
  ToP floor lowers the effective gas price (below zero if hard and the
  gas has no other use); a soft floor bounds the reduction by
  $c^{\min}_f$.  A binding cap adds the scarcity rent
  $\hat\nu^{\max}_f \le c^{\max}_f$ (soft cap).

#### Converter Gas Tanks (`plexos2gtopt`)

`plexos2gtopt` models inflexible LNG cargoes and weekly gas allotments
as **Reservoir** tanks (state variables) drained by one Turbine per unit
with `production_factor` $=1/h_g$ and `flow_conversion_rate` $=1/24$
(volume unit = fuel/24).  `--lng-inflexible-mode storage` keeps the
fuel price and the source model's spill penalty; `offtake` (take-or-pay)
zeroes the tank fuel's price, frees the spill, and lets $\vartheta$
price the gas.  `--gas-bands-as-tanks {horizon,period}` turns each
capped gas band into a tank fed by its allotment (`--gas-refill
instant|ramp:<h>`), with `emax` = 0 at each deadline inside the horizon
and a soft cap as a purchase Flow priced $P_f + c^{\max}_f$; with one
allotment the objective equals the `Fuel` model,
$(P_f+c^{\max}_f)x + P_f(V-T) = P_f u + c^{\max}_f x$ for
$T = V + x - u$.  Each tank whose gas outlives the horizon gets its own
future-cost variable (a `DecisionVariable` with raw cost 1 and a user
constraint on the last block):

```math
\alpha^{\text{gas}}_\tau + g_\tau\, e_{\tau,\text{end}} \ge g_\tau V^{\text{use}}_\tau,
\qquad \alpha^{\text{gas}}_\tau \ge 0
```

i.e. future cost $g\_\tau\max(0, V^{\text{use}}\_\tau - e\_{\tau,\text{end}})$
and terminal value $\vartheta\_{\text{end}} \in [0, g\_\tau]$; $g\_\tau$ is
the band price × 24 or `--lng-inflexible-value {bundle,cuts,diesel}`,
and $V^{\text{use}}$ is `--gas-usable capacity` (what the units can
still burn before the deadline) or `full` (the end `emax`).  One
$\alpha$ per tank is required: rows on one shared $\alpha$ give the
max of the tank values, not their sum.  These future costs serve the
single monolithic LP; under SDDP the tank values come from the cuts.
Details: [plexos2gtopt](../scripts/plexos2gtopt.md).
<!-- source: scripts/plexos2gtopt/_lng_inflexible.py,
scripts/plexos2gtopt/_gas_tanks.py:114-153 -->

#### Not Modelled

Cargo arrivals are data everywhere above: choosing a cargo's arrival,
buying an extra spot cargo or rescheduling a delayed one is **not** an
LP/MIP decision in gtopt.  A possible extension (not implemented) is a
binary $z\_{k,t}$ per candidate cargo $k$ of size $Q_k$ and price
$c^{\text{sp}}\_k$, with $D\_{\tau,t} = \sum_k Q_k z\_{k,t}$, cost
$c^{\text{sp}}\_k Q_k z\_{k,t}$ and contract rows such as
$\sum\_{t\in\mathcal{Y}}\sum_k z\_{k,t} \ge N^{\min}$; under SDDP its
integer stage problems would need convexified cuts.  Also not modelled:
the terminal's regasification limits, linking its send-out to a
`GasNode`, and make-up / carry-forward banks between contract periods.

### 5.14 bis Emission Zone, Cap, and Allowance-Pool Banking

Emissions are modelled with three element types (user guide:
[Emissions](../emissions.md)):

- `Emission` $\epsilon \in \mathcal{EM}$ — pollutant registry
  (`co2`, `ch4`, `nox`, …);
- `EmissionZone` $z \in \mathcal{EZ}$ — a system-wide or regional scope
  covering pollutants $\epsilon$ with weights $\mu_{z,\epsilon}$
  (`emissions[].weight`, GWP-100 for a CO₂-eq basket, default 1), and an
  optional cap, cap cost and price;
- `EmissionSource` $\nu \in \mathcal{ES}$ — links generator $g(\nu)$ to
  zone $z(\nu)$ and pollutant $\epsilon(\nu)$ with a combustion rate
  $f\_{\nu,t}$ and an upstream rate $f^{\text{up}}\_{\nu,t}$ [t/MWh],
  and/or fuel-basis factors $\phi\_{\nu,t}$, $\phi^{\text{up}}\_{\nu,t}$
  [t per fuel unit] (`fuel_rate`, `upstream_fuel_rate`).
  Sources are also synthesised from `Generator.emissions[]`, from
  `Fuel.emission_factors[]` (one per covering zone: a t/MWh rate
  $h_g \cdot \kappa_f \cdot e\_{f,\epsilon}$ for a scalar `heat_rate`,
  else the fuel-basis factor $\kappa_f \cdot e\_{f,\epsilon}$, with
  $\kappa_f$ = `heat_content`, 1 when unset), and from the legacy
  `Generator.emission_rate` (pollutant `co2`).

There is **no** emission variable and no balance row: every contribution
is substituted onto the generator dispatch column $p_{g,s,t,b}$ with the
per-block coefficient

```math
\alpha^{\text{em}}_{\nu,t,b} \;=\; \mu_{z(\nu),\epsilon(\nu)}\,
  \bigl(1 - \eta^{\text{cap}}_{g(\nu),\epsilon(\nu),t}\bigr)\,
  \bigl(f_{\nu,t} + f^{\text{up}}_{\nu,t}\bigr)\,\Delta_b
\qquad [\text{t/MW}]
```
<!-- source: source/emission_source_lp.cpp -->

Fuel-basis factors act on the generator's fuel burn rate
$F_{g,s,t,b}$ [fuel units/h], substituted the same way onto its legs:

```math
F_{g,s,t,b} \;=\;
\begin{cases}
h_{g,t,b}\, p_{g,s,t,b} & \text{scalar or per-block } \texttt{heat_rate}\\
h_{g,1}\, p_{g,s,t,b} + \sum_{k=1}^{K-1} (h_{g,k+1} - h_{g,k})\,
  s_{g,k,s,t,b} & \texttt{heat_rate_segments}
\end{cases}
\;+\; h^{0}_{g}\, u_{g,s,t,b}
```

where $s\_{g,k}$ are the piecewise heat-rate slack columns and
$`h^{0}_{g}\,u_{g,s,t,b}`$ is the no-load burn of a committed unit
(`Commitment.noload_fuel`, PLEXOS `Heat Rate Base`, times the status
$u$; zero without a commitment).  A generator whose commitment carries
`noload_fuel` always uses the fuel basis.  The same
$`F_{g,s,t,b}\,\Delta_b`$ is the generator's contribution to its fuel's
offtake rows (`Fuel.max_offtake` / `min_offtake`, `fuel("X").offtake`);
the no-load fuel *cost* remains in `noload_cost`.  The
coefficient on each leg is
$`\mu\,(1-\eta^{\text{cap}})\,(\phi_{\nu,t} + \phi^{\text{up}}_{\nu,t})\,\Delta_b`$
times the leg's fuel coefficient, so the cap, price,
allowance-pool and CCS terms below apply to the exact piecewise or
per-block fuel burn.
<!-- source: source/emission_source_lp.cpp, GeneratorLP::fuel_burn_legs -->

where $\eta^{\text{cap}}\_{g,\epsilon,t}$ is the CCS capture fraction
(`Generator.emission_captures[].rate`, 0 when absent). A source whose
pollutant is not listed in its zone's `emissions[]` contributes nothing.
The emissions of a source in block $b$ are
$`\alpha^{\text{em}}_{\nu,t,b}\,p_{g(\nu),s,t,b}`$; they are reported
post-solve (`EmissionSource/emissions_sol`, net of capture and
unweighted).

**Price (emission tax).** When the zone sets `price`
$c^{\text{em}}_{z,t}$ [\$/t], every source adds to its generator's
objective coefficient

```math
\omega_{s,t,b}\; c^{\text{em}}_{z(\nu),t}\;
  \frac{\alpha^{\text{em}}_{\nu,t,b}}{\Delta_b}
\qquad \forall \; \nu,\; s,t,b
```
<!-- source: source/emission_source_lp.cpp:195-202,247-252 -->

No row is created. Under `model_options.objective_mode = "emissions"`
the generator dispatch slopes (`gcost`, fuel) are zeroed and
$c^{\text{em}}_{z,t}$ defaults to 1 \$/t for zones without `price`, so
the objective becomes total weighted emissions.

**CCS cost.** A capture row with cost $c^{\text{ccs}}_{g,\epsilon,t}$
[\$/t captured] adds to the generator's objective coefficient

```math
\omega_{s,t,b}\;\eta^{\text{cap}}_{g,\epsilon,t}\;
  \bigl(f_{\nu,t} + f^{\text{up}}_{\nu,t}\bigr)\;
  c^{\text{ccs}}_{g,\epsilon,t}
\qquad \forall \; \nu,\; s,t,b
```
<!-- source: source/emission_source_lp.cpp:269-273 -->

**Standalone cap (no pool).** When `cap` $\overline{E}_{z,t}$
[t per stage] is set and the zone is *not* coupled to an allowance
pool, one row per (scenario, stage) sums the block tonnages:

```math
\sum_{b \in \mathcal{B}_t} \sum_{\nu:\,z(\nu)=z}
  \alpha^{\text{em}}_{\nu,t,b}\; p_{g(\nu),s,t,b}
  \;-\; s^{\text{cap}}_{z,s,t}
  \;\leq\; \overline{E}_{z,t}
\qquad \forall \; z \in \mathcal{EZ},\; s,t
```
<!-- source: source/emission_zone_lp.cpp:80-122 ;
source/emission_source_lp.cpp:238-242 -->

The slack $s^{\text{cap}}\_{z,s,t} \geq 0$ (`EmissionZone/cap_slack`)
exists only when `cap_cost` $c^{\text{cap}}\_{z,t}$ is set (soft cap);
its objective coefficient is $`\pi_s\,\delta_t\,c^{\text{cap}}_{z,t}/\sigma`$,
with no duration factor because the slack is an absolute tonnage.
Without `cap_cost` the cap is hard.

**Endogenous price.** The dual $\lambda^{\text{cap}}\_{z,s,t} \leq 0$
of the cap row (`EmissionZone/cap_dual`, reported in \$/t after
removing $`\pi_s\,\delta_t/\sigma`$) is the scarcity price of the
pollutant: zero when the cap is slack, and with a soft cap
$|\lambda^{\text{cap}}\_{z,s,t}| \leq c^{\text{cap}}\_{z,t}$. When `price`
and `cap` are both set, the marginal tonne costs
$c^{\text{em}}\_{z,t} + |\lambda^{\text{cap}}\_{z,s,t}|$.

**Allowance-pool coupling (cap-and-trade with banking).** An
`AllowancePool` $a \in \mathcal{A}$ reuses the `StorageLP` balance
template (§5.7) to bank allowances across stages: its inflow `finp` is
the free-allocation column $\phi_{a,s,t,b}$ (fixed-rate
$F_{a,t}/T_t$, the grandfathering entitlement) and its state
$e_{a,s,t,b}$ [tCO₂] carries forward with annual decay $\lambda_a$.

When `EmissionZone.allowance_pool` references pool $a$, each source's
block tonnage $`\alpha^{\text{em}}_{\nu,t,b}\,p_{g(\nu),s,t,b}`$ is
injected as a **drawdown** into that pool's energy-balance row, and the
zone's own standalone cap row is **skipped**:

```math
\begin{aligned}
e_{a,s,t,b} \;=\;& e_{a,s,t,b-1}\,(1 - \lambda_a^h\,\Delta_b)
  \;+\; \Delta_b\,\phi_{a,s,t,b}
  \;+\; \beta_{a,s,t,b}
  \;-\; \sum_{z:\,a(z)=a}\; \sum_{\nu:\,z(\nu)=z}
    \alpha^{\text{em}}_{\nu,t,b}\; p_{g(\nu),s,t,b}
\qquad \forall \; a,\; s,t,b
\end{aligned}
```
<!-- source: source/emission_source_lp.cpp:254-261 ;
source/allowance_pool_lp.cpp -->

**Auction purchases (cap-and-trade market).** When `auction_price`
$\rho_{a,t,b}$ is set, an `auction` column $\beta_{a,s,t,b}$ [tCO₂]
lets the bank buy allowances on the market instead of abating
emissions:

```math
0 \;\leq\; \beta_{a,s,t,b} \;\leq\; \overline{B}_{a,t,b},
\qquad \text{obj} \mathrel{+}= \rho_{a,t,b}\,\pi_s\,\delta_t\,\beta_{a,s,t,b}
```
<!-- source: source/allowance_pool_lp.cpp -->

where $\overline{B}\_{a,t,b}$ is `auction_cap`, $\pi_s$ the scenario
probability, and $\delta_t$ the stage discount. The price carries
**no** duration factor because $\beta$ is an absolute tonnage (contrast
the free-allocation rate $\phi$, whose energy-row coefficient is
$\Delta_b$). The marginal allowance value — the dual of the bank
balance — is thus capped at $\rho\_{a,t,b}$: the LP abates only while
abatement is cheaper than buying.

Because the bank obeys $e\_{a,s,t,b} \geq \underline{E}\_a$ (default
$0$) at every block and an optional terminal target
$e\_{a,\cdot,\text{end}} \geq E_a^{\text{fin}}$ (soft if
$E_a^{\text{fin,cost}}$ set), the cumulative emission cap becomes a
multi-stage budget with banking: unused allowances roll forward and
the marginal value of an allowance is the dual of the bank balance,
not a fixed per-stage price. `AllowancePoolLP` is assembled before
`EmissionSourceLP` (collections ordering), so the energy rows exist when
the sources inject their drawdown.

### 5.15 Unit Commitment (3-bin)

A `Commitment` element attaches to a generator $g \in \mathcal{UC}$ and
introduces the classical three-binary (status / startup / shutdown)
unit-commitment formulation. Status, startup and shutdown variables
live on the **commitment period** grid $p \in \mathcal{P}\_t^g$ (a
coarsening of the block grid so that startup decisions align with
enforcement periods); each block within the period references the same
status column $u\_{g,s,t,p(b)}$.

#### Variables

```math
\begin{aligned}
u_{g,s,t,p}, \; v_{g,s,t,p}, \; w_{g,s,t,p} \;\in\; \{0,1\}
\qquad \forall \; g \in \mathcal{UC},\; s,t,p
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:219-268 -->

(Binary is relaxed to $[0,1]$ continuous when `relax = true` or in SDDP
continuous phases.)

#### C1 — Status Transition Logic

```math
\begin{aligned}
u_{g,s,t,p} \;-\; u_{g,s,t,p-1} \;-\; v_{g,s,t,p} \;+\; w_{g,s,t,p} \;=\; 0
\qquad \forall \; g,\; s,t,p > 0
\end{aligned}
```

For the first period the RHS becomes the initial status:

```math
\begin{aligned}
u_{g,s,t,0} \;-\; v_{g,s,t,0} \;+\; w_{g,s,t,0} \;=\; u_g^{\text{init}}
\qquad \forall \; g,\; s,t
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:272-289 -->

#### C2 — Startup/Shutdown Exclusion

```math
\begin{aligned}
v_{g,s,t,p} \;+\; w_{g,s,t,p} \;\leq\; 1
\qquad \forall \; g,\; s,t,p
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:291-304 -->

Integrality: $u$ is binary; `initial_status` is rounded at 0.5.  C1
and the exclusion alone admit $v = w = \tfrac12$ for a unit that stays
on or off, which the start/stop ramp terms of C4 / C5 reward, so $v$,
$w$ follow `transition_integrality` (per unit, else `model_options`):
`auto` (default) and `binary` declare them binary, `continuous` keeps
them in $[0, 1]$.  (The rows $v \le u$, $w \le 1 - u$ that made
continuous $v$, $w$ exact for an integral $u$ — Knueven, Ostrowski &
Watson 2020; Rajan & Takriti 2005 — were removed on 2026-11-26: see
`docs/unit-commitment.md` §4.2 C3.)  The start-up tier
selectors follow `startup_tier_integrality` (default `continuous`: the
tier rows are an interval matrix, totally unimodular once $v$, $w$ are
integral).  `relax`, `continuous_phases` and `--no-mip` relax all of
them together.

#### C3 — Generation Bounds Coupled to Status

```math
\begin{aligned}
p_{g,s,t,b} \;-\; \overline{P}_{g,t,b}\,u_{g,s,t,p(b)} \;\leq\; 0
\qquad \forall \; g,\; s,t,b
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:346-362 -->

```math
\begin{aligned}
p_{g,s,t,b} \;-\; \underline{P}_{g,t,b}\,u_{g,s,t,p(b)} \;\geq\; 0
\qquad \forall \; g,\; s,t,b
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:364-377 -->

#### C4 — Ramp-Up

```math
\begin{aligned}
p_{g,s,t,b} \;-\; p_{g,s,t,b-1} \;-\; R_g^{\text{up}}\Delta_b\,u_{g,s,t,p(b-1)} \;-\; \text{SU}_g^{\text{ramp}}\,v_{g,s,t,p(b)} \;\leq\; 0
\qquad \forall \; g,\; s,t,b > 0
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:379-404 -->

#### C5 — Ramp-Down

```math
\begin{aligned}
p_{g,s,t,b-1} \;-\; p_{g,s,t,b} \;-\; R_g^{\text{dn}}\Delta_b\,u_{g,s,t,p(b)} \;-\; \text{SD}_g^{\text{ramp}}\,w_{g,s,t,p(b)} \;\leq\; 0
\qquad \forall \; g,\; s,t,b > 0
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:406-430 -->

At the first block of a stage ($b = 0$) the lagged terms $p_{g,s,t,-1}$ and
$u_{g,s,t,p(-1)}$ are the scalar boundary values $P_g^0$ and $U_g^0$
(`initial_power`, `initial_status`), moved to the RHS.  When `initial_power`
is unset, $P_g^0$ falls back to the generator's `initial_generation` at the
horizon start (first stage of the first phase) and to 0 elsewhere.  The same
$P_g^0$ = `initial_generation` is the value of `generation_prev` at the first
horizon block in user constraints.

Here $\Delta_b$ is the REAL time step of block $b$: on a stage with
`period_hours` (typical period with weighted blocks, see §5.7)
$`\Delta_b = d_b\,\kappa_t`$, otherwise the block duration.  Minimum
up / down times, commitment periods and start windows also count real
hours.

On point-sampled stages (default `ramp_formula = "backward"`) the rows
are the interval slopes, written scaled by
$h_k = t_k - t_{k-1}$ (no $1/h$ coefficients):

```math
{}-R^{dn}_g h_k\,u_{g,k} - \text{SD}_g\,w_{g,k}
\;\le\; P_k - P_{k-1} \;\le\;
R^{up}_g h_k\,u_{g,k-1} + \text{SU}_g\,v_{g,k},
```

with the cyclic wrap $h_0 = t_0 + H - t_{N-1}$ ($H$ = `period_hours`).
The start / stop allowance uses the $v$ / $w$ of the same interval.

#### C4/C5 — Ramp Limits with `ramp_formula = "central2"` (provisional, not recommended)

`central2` is a convex combination of the two adjacent interval slopes:
a zig-zag dispatch (the checkerboard null vector, with the wrap) passes
it although every interval slope violates the limit.  It is opt-in and
`validate_planning` warns.

For non-uniform node grids (Chebyshev typical weeks) the ramp limits
bound the derivative at the node times $t_k$ (`Block.time`, default the
block midpoints in real time), $h_- = t_k - t_{k-1}$,
$h_+ = t_{k+1} - t_k$:

```math
P'(t_k) \approx
\begin{cases}
\dfrac{h_-^2 P_{k+1} - h_+^2 P_{k-1} + (h_+^2 - h_-^2) P_k}
      {h_+ h_- (h_+ + h_-)} & 0 < k < N \\[2ex]
(P_1 - P_0)/(t_1 - t_0) & k = 0 \text{ (forward, first order)} \\[1ex]
(P_N - P_{N-1})/(t_N - t_{N-1}) & k = N \text{ (backward, first order)}
\end{cases}
```

```math
{}-R_g^{\text{dn}} h_k\,u_{g,k} - \text{SD}_g^{\text{ramp}} w_{g,k}
\;\le\; h_k\,P'(t_k) \;\le\;
R_g^{\text{up}} h_k\,u_{g,k} + \text{SU}_g^{\text{ramp}} v_{g,k}
\;(+\,\text{SU}_g^{\text{ramp}} c_{k+1} h_k\, v_{g,k+1})
```

written scaled by $h_k = h_-$ ($h_+$ at the first node); the last term
is the start allowance of the next interval, whose slope enters the
stencil with weight $c_{k+1}$.  With `ramp_wrap = true` (or a cyclic
stage) the end nodes use the central formula with the neighbour across
the period end ($t \pm$ `period_hours`).  The central formula is exact for
quadratic $P(t)$; the first-order end differences do not use the
stage's `initial_power`.  This discretisation is PROVISIONAL, pending
the reference paper of the Chebyshev sampling method.

#### C6 — Minimum Up/Down Time (Optional)

When $T_g^{\text{up}} > 0$, for each period $p$ let $\mathcal{Q}(p)$
be the set of periods covering the next $T_g^{\text{up}}$ hours:

```math
\begin{aligned}
\sum_{q \in \mathcal{Q}(p)} v_{g,s,t,q} \;-\; u_{g,s,t,p} \;\leq\; 0
\qquad \forall \; g,\; s,t,p
\end{aligned}
```
<!-- source: source/commitment_lp.cpp:562-618 -->

Symmetrically for minimum down time with $w$ and $1-u$.
<!-- source: source/commitment_lp.cpp:626-681 -->

#### Costs

The objective contribution of a committed unit is:

```math
\omega_{s,t,b}\,\Bigl[
  \text{NL}_g \cdot T_p \cdot u_{g,s,t,p}
  \;+\; \text{SU}_g \cdot v_{g,s,t,p}
  \;+\; \text{SD}_g \cdot w_{g,s,t,p}
\Bigr]
```

(no-load cost scales with period duration $T_p$; startup/shutdown are
per-event).
<!-- source: source/commitment_lp.cpp:216-267 -->

#### Pricing of a Unit-Commitment MIP (`pricing_mode`)

A MIP has no duals, so the nodal prices $\lambda_{n,s,t,b}$ (bus balance
duals, §5.1) of a UC run come from an LP derived from the solved MIP.
The dispatch (primal) is the polished MIP solution in every mode; only
the LP that supplies the duals changes.  Let $\hat x$ be the MIP
incumbent and $\mathcal{I}$ the integer columns (the status $u$ of every
non-relaxed commitment, plus $v, w$ on units with ramp rows; otherwise
$v, w$ are continuous and made binary by C1/C2).

| `pricing_mode` | Pricing LP | Integer columns |
|---|---|---|
| `fixed` (default) | the polish LP | $x_i = \hat x_i$ for all $i \in \mathcal{I}$ |
| `relaxed` | LP relaxation of the full MIP | $x_i$ continuous within its bounds for all $i \in \mathcal{I}$ |
| `fast_start` | fixed, except fast-start units | $u_g \in [0,1]$ for $g \in \mathcal{F}$; $x_i = \hat x_i$ otherwise |

**`fixed`** gives $\lambda = c_g - \mu^{\text{pmin}}_g$ for a committed
unit at its minimum: a unit kept on for reliability or by a min-up time
then sets a price *below* its own cost, which on CEN can be negative.
**`relaxed`** (convex-hull-like pricing) lets the start-up and no-load
costs of the committed units enter $\lambda$ through the relaxed $u$; it
minimises uplift in practice, but its prices depend on how tight the
formulation is.  **`fast_start`** (PJM / NYISO practice) relaxes only
the units that can be started and stopped within the pricing interval,
so a peaker committed at $P^{\min}$ prices at its average cost
$c_g + (\text{SU}_g + \text{NL}_g T)/(P^{\min}_g T)$ while slow units
stay fixed.

A commitment $g$ is fast-start ($g \in \mathcal{F}$) when
`Commitment.fast_start` is `true`; when it is unset, by the auto rule

```math
T^{\text{up}}_g \le H \;\wedge\; T^{\text{down}}_g \le H,
\qquad H = \texttt{fast_start_max_hours}\ (\text{default } 1\,\text{h}),
```

with an unset min up / down counting as 0.  gtopt has no start-up-time
field, so the rule uses min up / down only.  Relaxed commitments are
never registered (they have no integer columns).

Algorithm (per (scene, phase), after the MIP and before the polish):
clone the solved MIP, pin $x_i = \operatorname{round}(\hat x_i)$ for the
columns kept fixed, relax integrality, re-solve.  The polish then fixes
all integers and re-solves as usual, and the pricing LP's row duals and
reduced costs replace the polish duals in the solution.  If the pricing
LP is not optimal (infeasible, unbounded, or the polish failed) a
warning is logged and the `fixed` duals are kept (*fallback*).  Each
(scene, phase) with integer columns writes one row of `pricing.csv`
(`pricing_mode, applied_mode, fallback, attempted, relaxed_columns,
pinned_columns, status, reason`).  The SDDP simulation pass applies the
same step around its integer fix.
<!-- source: source/linear_interface_solve.cpp (solve_pricing_lp,
install_pricing_duals), source/system_lp.cpp (SystemLP::resolve),
source/commitment_lp.cpp (fast_start registration) -->

<!-- markdownlint-disable MD013 -->
**Uplift.** With price $\lambda$ at the unit's bus, the unit's profit
over the horizon is
$\pi_g = \sum_t \bigl[(\lambda_t - c\_{g,t}) p\_{g,t} - \text{NL}\_g u\_{g,t}\bigr] T_t - \text{SU}\_g v\_{g,t} - \text{SD}\_g w\_{g,t}$.
The make-whole payment is $\text{MWP}\_g = \max(0, -\pi_g)$ and the
lost-opportunity cost is
$\text{LOC}\_g = \max(0, \pi^\*\_g - (\pi_g + \text{MWP}\_g))$, where
$\pi^\*\_g$ is the profit of the unit's price-taking self-schedule at
$\lambda$ (single-unit DP with its min up / down, pmin / pmax and
commitment costs; ramps ignored).  Total uplift
$\sum_g \text{MWP}\_g + \text{LOC}\_g$ is the criterion for comparing
pricing modes: the lower it is, the better the prices support the
dispatch (`scripts/gtopt_pricing_uplift`).
<!-- markdownlint-enable MD013 -->

### 5.15 bis Power-Based Unit Commitment (`uc_formulation = "power_based"`)

With `model_options.uc_formulation = "power_based"` (or per unit,
`Commitment.uc_formulation`) a committed unit on a chronological
**block** stage is scheduled by its instantaneous power at the block
boundaries [Morales-España, Gentile & Ramos, OR Spectrum 37 (2015)
929–950; Morales-España, Baldick, García-González & Ramos, IEEE Trans.
Sustain. Energy 7(2) (2016) 614–624].  The default `"energy_block"`
keeps C2–C5 above unchanged.

Blocks $b = 1..B$ have real durations $\Delta_b$ and boundaries $\tau_b$
(end of block $b$).  The commitment variables stay on the unit's
commitment grid (§8 of `docs/unit-commitment.md`: per block, or
`commitment_period` cells or `Block.commitment_cell` labels; a stage with
blocks shorter than 1 h otherwise defaults to a 1 h grid), so a finer power grid
adds no binaries.  Block $b$ lies
in period $k(b)$; $E_b = 1$ if $\tau_b$ ends a period (next period
$k^+$), $F_b = 1$ if block $b$ starts one; $T_k$ is the start of period
$k$.  New columns per boundary: $p_b \ge 0$ (power above minimum) and
$\hat p_b$ (total power, output `Commitment/power`).

```math
\begin{aligned}
&\hat p_b = \underline P_b\,(u_{k(b)} + E_b v_{k^+}) + p_b
  {}+ \textstyle\sum_s f^{SU}(T_s - \tau_b)\,v_s
  {}+ \sum_d f^{SD}(\tau_b - T_d)\,w_d
  && \text{(PB1)}\\
&g_b = \tfrac12\,(\hat p_{b-1} + \hat p_b)
  && \text{(PB2)}\\
&p_b + \textstyle\sum_i r^{+}_{i,b} \le (\overline P_b - \underline P_b) u_{k(b)}
  {}- E_b (\overline P_b - SD) w_{k^+} + E_b (SU - \underline P_b) v_{k^+}
  && \text{(PB3)}\\
&p_b - \textstyle\sum_i r^{-}_{i,b} \ge 0
  && \text{(PB4)}\\
&p_b - p_{b-1} + \textstyle\sum_i r^{+}_{i,b} \le RU\,\Delta_b\,u_{k(b)}
  {}+ E_b (SU - \underline P_b) v_{k^+}
  && \text{(PB5)}\\
&p_{b-1} - p_b + \textstyle\sum_i r^{-}_{i,b} \le RD\,\Delta_b\,u_{k(b)}
  {}+ F_b (SD - \underline P_b) w_{k(b)}
  && \text{(PB6)}
\end{aligned}
```

When period $k$ is the single block $b$, a one-block outage passes
through $\underline P$ (a deliberate tightening of the published model,
always on; redundant in every other state):

```math
\begin{aligned}
&p_b + (SU - \underline P_b)\,w_k - (\overline P_b - \underline P_b)\,u_k
  \le SU - \underline P_b && \text{(PB7a)}\\
&p_{b-1} - (\overline P - \underline P)(u_{k-1} - w_k)
  {}+ (SD - \underline P)\,v_{k^+} \le SD - \underline P && \text{(PB7b)}
\end{aligned}
```

$f^{SU}(\delta)$ / $f^{SD}(\delta)$ are the piecewise-linear start-up /
shut-down trajectories (`startup_trajectory`, `shutdown_trajectory`,
sampled every `trajectory_step` hours, $f(0) = \underline P$, zero
beyond the trajectory, only $\delta > 0$ written); a unit with a
trajectory uses $SU = \underline P$ ($SD = \underline P$).  $g_b$ is the
generator's existing generation column, so the bus balance, energy and
fuel costs, emissions, hydro turbine flows and storage charging all use
the trapezoid energy, including the trajectory energy; the bus balance
stays an energy balance per block (the instantaneous power balance at
the boundaries is out of scope).  $\hat p_0$ uses $u_0$ =
`initial_status` and is fixed to the initial power when it is known
(else free within PB1 / PB3); cyclic stages identify boundary 0
with boundary $B$.
PB3–PB7 replace C2–C5 and the u-gated reserve headroom rows of §5.9
(which keep only $r \le \overline P u$ and
$r^- \le (\overline P - \underline P) u$); C1, C3, C6 and the start-cost tiers
are unchanged.
Point-sampled stages and expandable generators keep the energy-block
rows (warning).
<!-- source: source/commitment_power_based.cpp, source/commitment_lp.cpp
(add_power_based) -->

### 5.16 Commitment on Non-Chronological Stages (Block Formulation)

The formulation of §5.15 needs ordered blocks.  On a stage with
`"chronological": false` (load-duration / representative blocks) a
`Commitment` builds the **block formulation** instead: one status
variable per block, no startup / shutdown variables, no logic,
exclusion, ramp or min up/down rows.

```math
\begin{aligned}
u_{g,s,t,b} &\in \{0,1\} \\
p_{g,s,t,b} - \overline{P}_{g,t,b}\,u_{g,s,t,b} &\le 0 \\
p_{g,s,t,b} - \underline{P}^{c}_{g,t,b}\,u_{g,s,t,b} &\ge 0
\qquad \forall \; g \in \mathcal{UC},\; s,t,b
\end{aligned}
```

with the no-load cost
$`\sum_{b} \pi_{s}\,\delta_{t}\,c^{\text{nl}}_{g}\,\Delta_b\,u_{g,s,t,b}`$
in the objective (same
probability / discount factors as §5.15; `noload_fuel` adds
$\texttt{noload\_fuel}\cdot u$ to the fuel and emission balances as there).
<!-- source: source/commitment_lp.cpp (add_to_lp, `chronological`) -->

$\underline{P}^{c}$ is the same `Commitment.pmin` (per stage and block)
as in the chronological formulation, and `Generator.pmin` stays an
always-on floor on the generation column in both.  `relax`, `must_run`
and `fixed_status` apply as in §5.15; startup / shutdown costs, ramps,
min up/down times, start tiers / counts and `commitment_period` need
chronology and are ignored on such a stage (one info note per element).  Reserve
gating (§5.9) and inertia (§5.17) use the same rows
in both formulations; on a chronological stage with one period per
block and no startup / shutdown / ramp / min up-down data the two give
the same optimum.

The deprecated `simple_commitment_array` is read as plain
`Commitment`s: `dispatch_pmin` becomes `pmin`, and the generator's own
`pmin` — which the legacy element made conditional — moves onto the
commitment (`System::fold_legacy_simple_commitments`).

### 5.17 Inertia Zone and Provision

Mirroring the reserve-zone formulation (§5.9), GTOpt models
**synchronous inertia** as two coupled elements: `InertiaZone`
($z \in \mathcal{IZ}$) defines a per-block inertia requirement
$\overline{R}_{z,t,b}^{H}$ in MWs, and `InertiaProvision`
($p \in \mathcal{IP}$) links a generator's MW production to a fraction
of this MWs requirement.

#### Zone Requirement (InertiaZone)

With a shortage cost $c^{H}\_{z,t,b}$ (`InertiaZone.cost`, fallback
`model_options.reserve_shortage_cost`) a served-requirement column
$\rho^{H}\_{z,s,t,b} \in [0, \overline{R}^{H}\_{z,t,b}]$ with objective
coefficient $-c^{H}\epsilon\_{s,t,b}$ enters the requirement row with
coefficient $-1$; InertiaProvisions (and commitment-dependent
`requirement_credits`) add their contributions to the same row:

```math
\begin{aligned}
\sum_{p \in \mathcal{P}_z} \Phi_{p,t}\,r_{p,s,t,b}^{H} \;-\; \rho_{z,s,t,b}^{H} \;\geq\; 0
\qquad \forall \; z \in \mathcal{IZ},\; s,t,b
\end{aligned}
```
<!-- source: source/inertia_zone_lp.cpp (add_requirement) -->
<!-- source: source/inertia_provision_lp.cpp:175-191 (factor injection) -->

With the shortage $q^{H} = \overline{R}^{H} - \rho^{H} \geq 0$ this is
$\sum_p \Phi_{p,t} r^{H} + q^{H} \geq \overline{R}^{H}$ priced at
$`c^{H}\epsilon\,q^{H}`$: as for reserves (§5.9), the constant
$`+c^{H}\epsilon\,\overline{R}^{H}`$ is added to the LP-external objective
constant, so a met requirement contributes $0$ to the reported objective
and only the unmet inertia is charged; the LP and its duals are unchanged.
Without a cost the requirement is hard: no column, and
$\overline{R}^{H}$ is the row RHS.

#### Provision Coupling (InertiaProvision)

Each provision creates a column $r_{p,s,t,b}^{H}$ with upper bound
`provision_max` (falling back to the unit's committed minimum stable
level $\underline{P}^{c}_{g,t,b}$ when unset, else to its
$\overline{P}_g$) and an explicit cost.  $\underline{P}^{c}$ is read
from the unit's commitment element when it has one (see Provision
Factor below), since the converters put the committed floor there and
leave the generation column's lower bound at 0; without a commitment it
is `Generator.pmin`:

```math
\begin{aligned}
0 \;\leq\; r_{p,s,t,b}^{H} \;\leq\; \overline{R}_{p,t,b}^{H}
\qquad \forall \; p \in \mathcal{IP},\; s,t,b
\end{aligned}
```
<!-- source: source/inertia_provision_lp.cpp:143-159 -->

A **downward coupling** row ties the generator output to the reserved
MW:

```math
\begin{aligned}
p_{g,s,t,b} \;-\; r_{p,s,t,b}^{H} \;\geq\; 0
\qquad \forall \; p \in \mathcal{IP},\; s,t,b
\end{aligned}
```
<!-- source: source/inertia_provision_lp.cpp:161-173 -->

#### Provision Factor

$r_{p,s,t,b}^{H}$ is in MW; the zone requirement in MWs. The
conversion factor

```math
\Phi_{p,t} \;=\; \begin{cases}
\text{provision_factor}_{p,t} & \text{(explicit schedule)} \\[2pt]
H_p \cdot S_p / \underline{P}^{c}_{g,t,b} & \text{if } H_p,\,S_p \text{ set and } \underline{P}^{c}_{g,t,b} > 0 \\[2pt]
H_p \cdot S_p / \overline{P}_{g,t,b} & \text{if } H_p,\,S_p \text{ set and } \underline{P}^{c}_{g,t,b} = 0 \\[2pt]
1 & \text{otherwise}
\end{cases}
```

is injected as the coefficient on $r_{p,s,t,b}^{H}$ in every linked
zone's requirement row.  $\underline{P}^{c}_{g,t,b}$ is the unit's
committed minimum: $\max(\underline{P}_g, \texttt{Commitment.pmin})$ with
a `Commitment`, else $\underline{P}_g$ —
read from the input, not from the generation column bound.  The default
`provision_max` is $\underline{P}^{c}$ (or $\overline{P}$ when it is 0).
<!-- source: source/inertia_provision_lp.cpp:127-138,175-191 -->

#### Commitment-Dependent Relief (`requirement_credits`)

A zone requirement can be relieved when enough units of a set
$\mathcal{G}_j$ are committed.  Each credit $j$ (relief $c_j$ MWs, unit
count $k_j$) adds a selector $y_j \in [0, 1]$:

```math
\sum_p \Phi_p\, r^{H}_p + \sum_j c_j\, y_j \;\ge\; \overline{R}^{H},
\qquad
k_j\, y_j \;\le\; \sum_{g \in \mathcal{G}_j} u_g
```

with $u_g$ the Commitment status.  This is the
PLEXOS PCP form (2026-11-07): `InertiaNRMin_D` = Σ H·units + 2 900·y ≥
14 900 (day hours), `InertiaCommitGuacolda2` = Σ units(Guacolda) − 3 y
≥ 0, with `InertiaGuacolda2` ∈ [0, 1] **continuous**; until 2025-11-15
it was 13 400 with credits 3 900 (2 units) and 2 000 (3 units).

- `integer: false` (default, = PLEXOS): the effective requirement is
  $\overline{R}^H - \sum_j c_j \min(1, n_j/k_j)$, i.e.
  $\max(\overline{R}^H - c_j n_j/k_j,\ \overline{R}^H - c_j)$ per
  credit.  This is convex and decreasing in $n_j$, so an LP represents
  it exactly, but at integer $n$ below $k_j$ it is a partial (linear)
  relief, not a step.
- `integer: true`: $y_j$ binary, an exact step (relief only when
  $n_j \ge k_j$).  The 13 400 / 9 500 / 7 500 MWs staircase for
  0–1 / 2–3 / ≥ 4 units is two binary credits {2, 3 900} and
  {4, 2 000}.  A staircase is not convex in $n$ (slopes 0, −3 900, 0,
  −2 000), so no max-of-lines form in $n$ is exact; its convex hull
  (lines through (0, 13 400), (2, 9 500), (4, 7 500)) is a valid LP
  relaxation only.
- When none of the credit's generators has a status column in the
  (scenario, stage) the credit is dropped and the exogenous
  `requirement` applies.

Test (`test_reserve_dynamic_requirement.cpp`, 3 must-run units, credits
{2, 3 900}, {4, 2 000}): inertia bought = 9 500 (binary), 8 000
(continuous: 3 900 + 2 000·3/4 relief), 13 400 (no commitment).

### 5.18 Hydrogen Carrier (Electrolyser, Node, Offtake)

The hydrogen economy is a separate energy carrier measured in
MWh$\_{\text{LHV}}$.  A `HydrogenNode` $n$ has one equality balance row
per block; a `CarrierConverter` $c$ (electrolyser) withdraws electric
power $x\_{c,s,t,b}$ [MW$\_e$] from its bus and injects
$`\eta_{c,t,b}\, x_{c,s,t,b}`$ into the node; a `HydrogenStorage`
behaves like a battery on the node; a `HydrogenDemand` $h$ withdraws
$q\_{h,s,t,b}$ [MW$\_{\text{LHV}}$].

Unlike a `Battery` (daily cycle, closed $e_{\text{fin}} = e_{\text{ini}}$
per stage by default), `HydrogenStorage` and `ThermalStorage` default to
`use_state_variable = true`, like `AmmoniaStorage` and `Reservoir`: the
stored energy is carried across stages and phases, and the only terminal
condition is $e_{s,T,\text{last}} \ge e_{\text{fin}}$.  Setting
`use_state_variable = false` restores the closed-cycle row
$e_{s,t,\text{last}} = e_{s,t,\text{ini}}$ on every stage.

#### Node Balance

```math
\sum_{c \to n} \eta_{c,t,b}\, x_{c,s,t,b}
\;+\; \sum_{k \in n} \left(d^{out}_{k,s,t,b} - d^{in}_{k,s,t,b}\right)
\;-\; \sum_{h \in n} q_{h,s,t,b} \;=\; 0
\qquad \forall n, s, t, b
```

The electrolyser input $x\_{c,s,t,b} \in [0, \bar{X}\_c]$ enters the bus
balance (§5.1) with coefficient $-1$ and carries the operating cost
$`\pi_s \delta_t \Delta_b\, o_{c,t,b}`$ (a negative `ocost` represents a
bid that values the load).  An expandable converter (`expcap` /
`expmod`) adds, per block, the capacity row
$x\_{c,s,t,b} \le \bar{X}^{\text{inst}}\_{c,t}$ on its installed-capacity
column, as generators do; $\bar{X}\_c$ alone is only the `capmax` bound.
The converter builds nothing in a stage where it, or either endpoint
node, is out of service.
<!-- source: source/carrier_converter_lp.cpp, source/carrier_node_lp.cpp -->

#### Hydrogen Offtake (`HydrogenDemand`)

$$
\underline{q}_{h,t,b} \le q_{h,s,t,b} \le \bar{q}_{h,t,b}
$$

with objective contribution
$`-\pi_s \delta_t \Delta_b\, p_{h,t,b}\, q_{h,s,t,b}`$ (`price`, default
0).  Optional per-stage energy requirement (production target over
the stage, e.g. the AEMO ISP weekly/monthly hydrogen targets):

```math
\sum_{b \in t} \Delta_b\, q_{h,s,t,b} + \sigma_{h,s,t} \;\ge\; E^{min}_{h,t},
\qquad
\sum_{b \in t} \Delta_b\, q_{h,s,t,b} \;\le\; E^{max}_{h,t}
```

where $\sigma\_{h,s,t} \ge 0$ exists only when `min_energy_cost`
$\kappa\_{h,t} > 0$ and is priced at $\pi_s \delta_t \kappa\_{h,t}$
[\$/MWh$\_{\text{LHV}}$]; otherwise the minimum is hard.  Because the
offtake may fall in any block of the stage, the stage target implicitly
assumes free intra-stage hydrogen storage; use a `HydrogenStorage` plus
a per-block `lmin`/`lmax` profile to model storage explicitly.  The
dual of the $E^{min}$ row is the stage marginal cost of hydrogen
[\$/MWh$\_{\text{LHV}}$].
<!-- source: source/hydrogen_demand_lp.cpp -->

### 5.19 Gas Network (Nodes, Pipes, Compressors, Gas Turbines)

A steady-state natural-gas transmission network coupled to the power
dispatch, as a pure LP on a **radial** network (fixed flow directions,
$f \ge 0$).  Units: flow [km³/h, standard], pressure $p$ [bar abs],
squared pressure $\varpi = p^2$ [bar²], line pack [km³], gas cost
[\$/km³], heat rate [km³/MWh].  All rows are per block $b$ of stage
$t$ and scenario $s$ (indices dropped below); $\Delta_b$ is the block
duration.  The model follows the radial fast path of the gas-network
study (Weymouth in squared pressure, outer approximation by tangents,
De Wolf & Smeers [[23]](#ref23); compressor fuel after Saldarriaga
[[25]](#ref25)).

| Element | JSON array | Columns | Rows |
|---------|------------|---------|------|
| `GasNode` $n$ | `gas_node_array` | $\varpi_n$ | `balance` |
| `GasSupply` $g$ | `gas_supply_array` | $x_g$ | `volume` |
| `GasDemand` $d$ | `gas_demand_array` | $u_d$ (shedding) | — (balance RHS) |
| `GasPipe` $\ell$ | `gas_pipe_array` | $f\_\ell$ or $f^{in}\_\ell, f^{out}\_\ell, L\_\ell$; $d^\pm\_\ell$ (SLP) | `weymouth` ×N, `linepack_def`, `linepack_balance`, `linepack_end`, `slp_proximal` |
| `GasCompressor` $c$ | `gas_compressor_array` | $\Psi_c$, $LC_c$, $P^{el}_c$ | `ratio`, `noexpand`, `fuel_plane`, `motor` |
| `GasTurbine` $k$ | `gas_turbine_array` | — (uses the generator's $P_k$) | `pressure_gate`, `derate` |

#### Node Pressure and Balance

```math
\underline{p}_n^2 \le \varpi_n \le \bar{p}_n^2,
\qquad \text{cost } \pi_s\delta_t\Delta_b\, c^{\varpi}_n\, \varpi_n
```

```math
\sum_{g\in n} x_g +
 \sum_{\ell:\,\to n} f^{out}_\ell - \sum_{\ell:\,n\to} f^{in}_\ell +
 \sum_{c:\,\to n} \Psi_c - \sum_{c:\,n\to} (1+\chi_c\gamma^{ff}_c)\Psi_c -
 \sum_{c:\,n\to}^{\text{gas drive}} LC_c -
 \sum_{k\in n} h_k P_k +
 \sum_{d\in n} u_d
\;=\; \sum_{d\in n} D_d
```

Here $\chi_c = 1$ for `drive: gas` and 0 for an electric drive (whose
suction node supplies only $\Psi_c$).  The dual of the balance row is
the nodal gas price $\lambda_n$ [\$/km³].  $c^{\varpi}\_n$ (`pressure_cost`) is
a tiny *negative*
tie-break (e.g. $-10^{-4}$ \$/(bar² h)): among the LP-optimal
dispatches it selects the highest pressure profile, i.e. the pressures
of the exact Weymouth equation from the sources down.  Supplies
$x_g \in [\underline{x}\_g, \bar{x}\_g]$ cost
$`\pi_s\delta_t\Delta_b\, c_g x_g`$; shedding $u_d \in [0, D_d]$ costs
$`\pi_s\delta_t\Delta_b\, c^{fail}_d u_d`$ (no column when `fail_cost` is
unset: hard load).  A supply with `volume` $V_g$ adds the stage row
$\sum_b \Delta_b x\_{g,b} \le V_g$ (an LNG or pipeline allotment).  A
pipe with `weymouth_mode: none` is a capacity-only arc $0 \le f \le \bar F$
that enters the two balances without any pressure row, such as a
delivery through a parallel system.
<!-- source: source/gas_node_lp.cpp, source/gas_supply_lp.cpp,
source/gas_demand_lp.cpp -->

#### Weymouth Pipe (Outer Approximation)

Weymouth in squared pressure, $\varpi_a - \varpi_b = K f|f|$, is
relaxed on a radial network to the convex $\varpi_a - \varpi_b \ge K f^2$ and
replaced by $N$ (`segments`) tangent planes

```math
\varpi_a - \varpi_b - 2 K t_k \bar{f} \;\ge\; -K t_k^2,
\qquad k = 1..N,\quad 0 \le f \le \bar{F}
```

with $\bar f = f$ (steady state) or $\bar f = (f^{in}+f^{out})/2$ (line
pack).  `tangent_layout`:

- `uniform`: $t_k = (2k-1)\bar F/(2N)$ — the line-loss tangent
  placement; the squared-pressure error is at most $K(\bar F/2N)^2$;
- `relative_pressure`: breakpoints uniform in
  $\theta = \arcsin(\sqrt{K} f / P)$, $b_k = (P/\sqrt K)\sin(k\theta_F/N)$,
  $t_k = (b_{k-1}+b_k)/2$ ($P$ = inlet `pmax`), which evens out the
  *relative* outlet-pressure error (denser tangents near capacity).

$K$ [bar²/(km³/h)²] is `weymouth_k`, or the SI Weymouth (Menon [[24]](#ref24)
eq. 2.62)
$K = G T_f L Z / (3.7435\cdot10^{-3} E (T_b/P_b) D^{2.667})^2 \cdot 24000^2/10^4$
from `length` [km], `diameter` [mm], `efficiency`
$E$ (0.92), `gravity` $G$ (0.6), `temperature` $T_f$ (288.15 K) and
`z` (0.9).  The relaxation lets the LP drop more pressure than the flow
needs; on a radial network the solution stays physically realisable
(a regulator at the pipe end), and pressure carries no cost, so there is
no gas-creating arbitrage.
<!-- source: source/gas_pipe_lp.cpp, source/gas_physics.cpp -->

#### Elevation

A pipe climbing from $h_a$ to $h_b$ [m] follows the steady-state gas
equation with gravity (Menon [[24]](#ref24) §2.9):

```math
\varpi_a - e^{s}\varpi_b = K_e\, f|f|,
\qquad
s = \frac{2 g M (h_b - h_a)}{Z R T},
\qquad
K_e = K\,\frac{e^{s}-1}{s}
```

with $`M = G\,M_{air}`$ ($M_{air}$ = 0.028964 kg/mol), $g$ = 9.80665
m/s², $R$ = 8.314 J/(mol K); $K_e \to K$ as $s \to 0$ (a series
branch avoids the division).  Both sides stay linear in $\varpi$: the
tangent rows become
$\varpi_a - e^{s}\varpi_b - 2K_e t_k \bar f \ge -K_e t_k^2$.  An uphill
pipe ($s > 0$) needs a higher inlet pressure for the same flow; downhill
($s < 0$) gravity helps.  $h$ is `GasNode.elevation` (default 0); $G$,
$T$ and $Z$ come from `options.gas_options` (`gas_specific_gravity`
0.6, `gas_temperature` 288.15 K, `compressibility` 0.9), overridable per
pipe (`gravity`, `temperature`, `z`); `elevation_exponent` sets $s$
directly.  At 4000 m (Paso de Jama) $s \approx 0.63$, i.e. the outlet
squared pressure is divided by $e^{s} \approx 1.9$ before friction.

#### Exactness Report

For every pipe and block the output carries the Weymouth gap
$\varpi_a - e^{s}\varpi_b - K_e \bar f|\bar f|$ [bar²]
(`GasPipe/weymouth_gap_sol`) and the outlet-pressure error
$p_b - \sqrt{(\varpi_a - K_e \bar f|\bar f|)/e^{s}}$ [bar]
(`GasPipe/pressure_error_sol`; positive = the LP outlet pressure is
optimistic).  The tangent error alone is at most $K_e(\bar F/2N)^2$; a
larger positive gap means the relaxation is not tight at that pipe (the
LP drops more pressure than the flow needs — harmless on a radial
network unless an upper pressure bound downstream binds, see the Hu &
Ryan benchmark).  A warning is logged when the error exceeds 0.1 bar.

#### Meshed Networks (Linearised Pipes and SLP)

In a loop the flow direction is not known in advance. The equality
$\varpi_a - \varpi_b = K f|f|$ is then non-convex, so the radial tangent
rows do not apply. A pipe with `weymouth_mode: linearized` instead uses a
signed flow $f \in [-\bar F, \bar F]$ and one equality per block,
linearised at a reference flow $f_0$:

```math
\text{secant (default):}\;
\varpi_a - e^{s}\varpi_b = K_e |f_0|\, \bar f,
\qquad
\text{tangent:}\;
\varpi_a - e^{s}\varpi_b = 2K_e|f_0|\,\bar f - K_e f_0|f_0|
```

(`linearization`). Both equal the exact equation at $\bar f = f_0$. The
secant's coefficient $K_e|f_0|$ stays valid when the flow reverses. The
tangent is the Newton step for $f|f|$.

The reference flow $f_0$ is `reference_flow` when given. Otherwise it is
`slp_initial_flow`·$\bar F$ (default 0.1), and a warning is logged when
no SLP pass follows.

**Sequential LP.** With `options.gas_options.slp_iterations` $= M > 0$,
each monolithic (scene, phase) solve is followed by up to $M$ passes of
three steps:

1. Update every reference flow:
   $f_0 \leftarrow f_0 + \omega(f - f_0)$, with $|f_0| \ge$
   `slp_min_flow`·$\bar F$. The damping $\omega$ is `slp_damping`
   (default 0.5).
2. Re-issue the rows' coefficients through the standard `update_lp`
   dispatch. This is the same mechanism as reservoir seepage and
   discharge limits, and the LP replay buffer records the writes for
   `low_memory: compress`.
3. Re-solve.

The loop stops when $\max|f - f_0| / \max(|f_0|, f_{min})$ falls below
`slp_tolerance` (default 1e-4). A flow inside $\pm f_{min}$ on both
passes counts as settled, because its sign is degenerate. At the fixed
point $f = f_0$ the row is the exact Weymouth equation.

**Proximal term.** When gas costs tie, the LP has several optimal
vertices: the same cost can be met by different splits between sources
or plants. The solver may then alternate between them from pass to pass.
The fixed point is never reached, and the last iterate violates Weymouth
by $K_e (f - f_0)^2$, which was 22 bar on the IEEE-24 + Belgian case. A
proximal row is therefore added to every linearised pipe when
$M > 0$:

```math
\bar f_{\ell,b} - d^+_{\ell,b} + d^-_{\ell,b} = f_{0,\ell,b},
\qquad \text{cost } \rho\,(d^+ + d^-)
```

Here $\rho$ is `slp_proximal_cost` (default $10^{-6}$), and the RHS is
re-issued with $f_0$. Among equal-cost vertices the LP keeps the one
nearest $f_0$, and $d^\pm \to 0$ at the fixed point. A hard trust region
$|f - f_0| \le \Delta$ was rejected: combined with the re-linearised
equalities it can make the LP infeasible.

On a triangle loop, the undamped secant oscillates on the small loop
flow, while $\omega$ = 0.5 converges in about 25 passes. The tangent
form converges in about 4 passes undamped (`test_gas_network_slp.cpp`).
SDDP re-issues the rows at the stored $f_0$ in every forward pass but
does not move $f_0$.

Compressors stay fixed-direction. A `GasCompressor` with
`bidirectional: true` is an open valve: signed flow and
$\varpi_{out} = \varpi_{in}$. It requires `max_ratio` 1 and
`fuel_model: none`. The exactness report applies in both modes.

#### Line Pack

With `linepack: true`, $`L_\ell = c\,(p_a+p_b)/2`$ is linearised at
$p_r$ (`linepack_pressure_ref`, default the mean of the end nodes'
bands) through $p \approx (\varpi + p_r^2)/(2p_r)$:

```math
L_{\ell,b} - \tfrac{c}{4p_r}(\varpi_{a,b}+\varpi_{b,b}) = \tfrac{c\,p_r}{2},
\qquad
\Delta_b (f^{in}_{\ell,b} - f^{out}_{\ell,b}) - L_{\ell,b} + L_{\ell,b-1} = 0,
\qquad
L_{\ell,\text{last}} \ge L^{0}_\ell
```

with $L_{\ell,0} = L^0_\ell$ (`linepack_ini`, default from the nodes'
`p_init`) and $c$ [km³/bar] = `linepack_coeff` or the geometric volume
$`\pi D^2 L/4\,/(Z P_b)\cdot(T_b/T)`$ ($T_b$ = 288.15 K, $P_b$ = 1.01325
bar, $T$ the flowing temperature).  The line pack cycles within each
stage (daily cycle); it is not an SDDP state variable.  The terminal row is
dropped with
`options.gas_options.linepack_cyclic: false`, so the pack may be drawn
down to the pressure bounds (multi-day outage studies). The tangent-at-$p_r$
linearisation over-estimates $p$ by
$(p-p_r)^2/(2p_r)$ (≈ 5 % of the absolute line pack for ±20 bar).
<!-- source: source/gas_pipe_lp.cpp -->

#### Compressor Station

```math
\varpi_{out} \le \bar\beta^2 \varpi_{in},
\qquad
\varpi_{out} \ge \varpi_{in} \;\;(\text{unless } \texttt{allow_bypass}),
\qquad 0 \le \Psi \le \bar F
```

`allow_bypass` defaults to true for `none` / `fixed_fraction` and false
for `saldarriaga`; a `bidirectional` valve keeps both rows.

Fuel (`fuel_model`):

- `none`: no fuel (with $\bar\beta = 1$ and `allow_bypass` the station
  is a lossless regulator);
- `fixed_fraction`: $LC = \gamma^{ff}\Psi$,
  $\gamma^{ff}$ = `fuel_fraction` or $\gamma(\bar\beta^\alpha - 1)$;
- `saldarriaga`: the fuel $LC = \gamma(\beta^\alpha-1)\Psi$ written in
  $\varpi$ is $\gamma\Psi((\varpi_{out}/\varpi_{in})^{\alpha/2}-1)$,
  homogeneous of degree 1 in $\Psi$ and 0 in $\varpi$, so its Taylor
  plane at $(\Psi_0, \varpi_{in,0}, r_0\varpi_{in,0})$, $r_0 = \beta_0^2$, has
  no constant term (Euler):

$$
LC \ge c_\Psi \Psi + c_\varpi(\varpi_{out} - r_0\varpi_{in}),
\quad c_\Psi = \gamma(\beta_0^\alpha - 1),
\quad c_\varpi = \gamma\tfrac{\alpha}{2}\Psi_0 r_0^{\alpha/2-1}/p_{in,0}^2,
\quad LC \ge 0
$$

  one row per `op_ratio` value $\beta_0$ (max of planes), with
  $0 \le LC \le \bar F$.  Each plane is exact on the slice
  $\beta = \beta_0$ (its pressure term vanishes there); elsewhere it
  is a first-order approximation, not a bound: the fuel is concave in
  the ratio (at the operating point each plane over-estimates it) and
  bilinear in $(\Psi, r)$, so the max of planes is conservative along
  the ratio and exact only on the listed slices.  The `≥`
  (instead of the thesis' equality) is tight whenever the plane is
  positive because fuel costs gas, and floors it at 0 where the plane
  would create gas.  Defaults: $\alpha = (\kappa-1)/\kappa = 0.2308$;
  $\gamma$ from the adiabatic head with `efficiency` 0.80 and
  `driver_efficiency` 0.33 (≈ 0.043); $\Psi_0 = 0.6\bar F$,
  $\beta_0 = 1 + (\bar\beta-1)/2$, $p_{in,0}$ = mid suction band.

`drive: gas` draws $LC$ at the suction node; `drive: electric` adds
$P^{el} \in [0, \bar P^{el}]$ (`power_max`) with coefficient $-1$ in
the bus balance (§5.1) and $`P^{el} = \kappa\, LC`$ (or
$\kappa\gamma^{ff}\Psi$ for `fixed_fraction`),
$\kappa = \text{LHV}\cdot\eta_{driver}/\eta_{motor}$ [MW per km³/h] (≈ 3.3).
Fail-closed (§5.8): with the motor bus inactive, $\bar P^{el} = 0$, so
the motor row forbids any compression work (no free power).
<!-- source: source/gas_compressor_lp.cpp -->

#### Gas Turbine (Generator Link)

A `GasTurbine` links generator $k$ to its delivery node $n$: the fuel
$h_k P_k$ enters the node balance, and with `min_pressure` $p_k$ the
fuel-gas pressure need is the concave capability

$$
P_k \le s_1 \varpi_n - s_1 (p_k - \delta_k)^2,
\qquad s_1 = \frac{o_k \bar P_k}{p_k^2 - (p_k-\delta_k)^2}
$$

($\delta_k$ = `pressure_band`, default 0.5 bar): zero output at
$(p_k-\delta_k)^2$, rising to $o_k\bar P_k$ at $p_k^2$.  With
`nominal_pressure` $p^{nom}_k > p_k$ a derate line from $(p_k^2, o_k\bar P_k)$
to $((p^{nom}_k)^2, \bar P_k)$ is added
($o_k$ = `derate_min_output`, default $p_k/p^{nom}_k$; without derate
$o_k = 1$).  $\bar P_k$ is the generator's `pmax`.  The rows are upper
bounds on $P_k$, but their intercept is negative, so even at $P_k = 0$
they require $\varpi_n \ge (p_k-\delta_k)^2$.  "Off below the band"
would be $P_k \le \max(0, \text{ramp})$, which is non-convex and needs a
binary.  A plant with `min_pressure` therefore keeps its delivery node at
least at the bottom of the band. A node whose `pmax` is below the band
makes the LP infeasible; drop `min_pressure` for such a plant.
Fail-closed (§5.8): when the gas turbine or its gas node is inactive the
fuel term cannot be written and $P_k$ is closed ($\le 0$): a gas unit
never generates without burning gas.
<!-- source: source/gas_turbine_lp.cpp -->

#### Outputs

`GasNode/{pi_sol, pressure_sol, balance_dual}` (pressure $=\sqrt\varpi$,
gas price \$/km³); `GasSupply/flow_sol`; `GasDemand/{load_sol,
fail_sol}`; `GasPipe/{flow_sol}` or `{flow_in_sol, flow_out_sol,
linepack_sol, linepack_balance_dual}`, plus `GasPipe/{weymouth_gap_sol,
pressure_error_sol}`; `GasCompressor/{flow_sol,
fuel_sol, ratio_sol, power_sol, ratio_dual}`; `GasTurbine/{fuel_sol,
pressure_gate_dual, derate_dual}`.

Out of scope (follow-ups): exact meshed optimisation (direction
binaries / SOS2 on $f|f|$; the SLP above converges to a stationary
point, not a certified global optimum), SLP inside SDDP iterations,
line-pack state across stages and SDDP phases, compressor
on/off binaries, `LngTerminal` send-out into a `GasNode`, adaptive
tangents / operating points.

#### Gas Prices and the Gas–Power Coupling

The balance dual $\lambda_n$ [\$/km³] is the nodal gas price.  The fuel
of a gas turbine enters the gas balance with coefficient $-h_k$ on the
same column $P_k$ that enters the bus balance (§5.1) with $+1$, so the
reduced-cost condition of a unit dispatched strictly between its bounds,
with no other active coupling row, reads

```math
\lambda^{\text{bus}}_{n(k)} = c_k + h_k\,\lambda_{n} + |\nu^{\text{gate}}_k|
```

where $\nu^{\text{gate}}\_k$ is the dual of its `pressure_gate` (or
`derate`) row.  The gas price is set by the marginal supply when the
network is uncongested, and by the electricity price,
$(\lambda^{\text{bus}} - c_k)/h_k$, when the delivery is limited
(`test_gas_network.cpp`, "heat-rate link and price dual units": 220
\$/MWh diesel / 0.2 km³/MWh = 1100 \$/km³).  For a flowing radial pipe
without line pack, the reduced cost of $f\_\ell$ gives
$`\lambda_b - \lambda_a = \sum_k 2K_e t_k\,\nu^{W}_{\ell,k}`$ (plus the
capacity dual), with $\nu^{W} \ge 0$ the duals of the tangent rows: the
gas price rises along the flow only when a pressure bound binds
somewhere.  A white-paper version of this section is in
[`docs/white_paper/sections/gas_network.tex`](../white_paper/sections/gas_network.tex);
the user guide with the benchmark cases is
[Gas Network](../gas-network.md).

> **Note on renumbering.** Capacity Expansion (§5.11) and User
> Constraints (§5.12) retain their numbering even though §5.13–§5.17
> add new content: §5.12 continues to reference *all* of the above
> element-specific variables and rows, so it remains the natural final
> subsection of §5.

---

## 6. Scaling and Solver Options

### 6.1 Objective Scaling

All cost coefficients are divided by `scale_objective` ($\sigma$, default
1000) to improve solver numerical conditioning. The reported objective value
in `solution.csv` is the **scaled** value:

$$
z_{\text{reported}} = \frac{z_{\text{actual}}}{\sigma}
$$

Multiply by $\sigma$ to recover the cost in original monetary units.

### 6.2 Voltage Angle Scaling

Voltage angles are enlarged by `scale_theta` ($\sigma_\theta$):
θ_LP = scale_theta · θ (rad); auto = 1/(3·median(X/V²)); 1 = radians; 1000 =
milliradians.  The auto value puts the median line's KVL coefficients at
$\sigma_\theta X/V^2 \approx 1/3$: θ is a node potential accumulating
~$D$ line drops to the reference bus ($D \approx 8$–10 on the Chilean
system), so the extra factor $\approx\sqrt{D} = 3$ balances the θ
coefficient against the θ_LP magnitude (on CEN 5-year SDDP it lands near
1000, the empirically best value), keeping angle variables and
susceptance coefficients in a numerically well-conditioned range (internally the
`Bus.theta` VariableScale entry is $1/\sigma_\theta$, following the
generic $\text{physical} = \text{LP} \times \text{scale}$ convention).
Values below 1 shrink θ and log a load-time warning: before 2026-11 the
option had the inverse meaning.

### 6.3 Variable Scaling

Beyond the global objective and angle scaling factors, GTOpt supports
**per-variable scale factors** that improve LP solver numerics when
physical quantities span very different magnitudes (e.g., reservoir
volumes in dam$^3$ vs. power flows in MW).

#### Convention

All variable scales follow a single convention:

$$
x_{\text{physical}} = x_{\text{LP}} \times \sigma_x
$$

where $\sigma_x$ is the scale factor and $x_{\text{LP}}$ is the LP
decision variable. Bounds, coefficients, and right-hand sides are
adjusted consistently so that the **optimal physical solution is
invariant to the choice of scale**.

#### Component-Specific Scales

| Component | Field | Symbol | Default | Effect |
|-----------|-------|--------|---------|--------|
| Battery | `variable_scales` (energy) | $\sigma_E$ | 1.0 | SoC variable: $E_{\text{phys}} = E_{\text{LP}} \times \sigma_E$ |
| Reservoir | `variable_scales` (energy) | $\sigma_V$ | 1.0 | Volume variable: $V_{\text{phys}} = V_{\text{LP}} \times \sigma_V$ |
| Bus | `scale_theta` | $\sigma_\theta$ | auto $1/\operatorname{median}(X/V^2)$ | Angle variable: $\theta_{\text{phys}} = \theta_{\text{LP}} / \sigma_\theta$ |

When a scale factor $\sigma_x$ is applied to a storage variable, the LP
formulation adjusts:

- **Variable bounds**: $`x_{\text{LP}} \in [\underline{x}/\sigma_x,\; \overline{x}/\sigma_x]`$
- **Objective coefficients**: multiplied by $\sigma_x$ so that
  $c \cdot x_{\text{LP}} \cdot \sigma_x = c \cdot x_{\text{phys}}$
- **Constraint coefficients**: adjusted so that physical-unit
  relationships are preserved (e.g., the SoC balance row coefficients
  absorb the ratio $\sigma_{\text{flow}} / \sigma_E$)

#### Generic Variable Scales (`variable_scales`)

The `options.variable_scales` array provides a uniform mechanism for
scaling any LP variable by element class, variable name, and optional
element UID:

```json
{
  "options": {
    "variable_scales": [
      {"class_name": "Reservoir", "variable": "volume", "scale": 1000.0},
      {"class_name": "Battery", "variable": "energy", "uid": 3, "scale": 10.0}
    ]
  }
}
```

Resolution priority:

1. Per-element override (matching class + variable + UID)
2. Per-class default (matching class + variable, no UID)
3. Fallback: 1.0 (no scaling)

Global options (`scale_theta`) take precedence over entries in
`variable_scales`.

> **⚠️ Important: Reservoir scaling for large hydro systems.**
> The default scale `1.0` for reservoirs is adequate for small
> systems, but will cause severe LP numerical ill-conditioning for
> large-scale hydrothermal systems. A reservoir with maximum volume
> $V_{\max} = 6 \times 10^6$ dam³ (Lake Laja scale) combined with a
> generator cost coefficient of $\sim 0.1$ $/MWh produces an LP coefficient
> ratio exceeding $10^8$ — well outside the safe range of $10^5$–$10^7$.
>
> **Best practice:** Set the energy scale to `max(1, emax / 1000)` via
> `variable_scales` so that LP volume variables are normalised to the
> $[0, 1000]$ range:
>
> ```json
> {
>   "options": {
>     "variable_scales": [
>       {"class_name": "Reservoir", "variable": "energy", "uid": 1, "scale": 6000.0},
>       {"class_name": "Reservoir", "variable": "energy", "uid": 2, "scale": 1500.0}
>     ]
>   }
> }
> ```
>
> Or use a uniform default with `uid = -1` for all reservoirs:
>
> ```json
> {
>   "options": {
>     "variable_scales": [
>       {"class_name": "Reservoir", "variable": "energy", "uid": -1, "scale": 1000.0}
>     ]
>   }
> }
> ```
>
> This matches the PLP `ScaleVol` convention (`ScaleVol = max(1, Vmax/1000)`).
> Run with `--stats` and check the LP coefficient ratio in the log output;
> a ratio above $10^7$ is a warning sign that scaling should be tuned.

### 6.4 Key Options Affecting the Formulation

| Option | JSON field | Default | Effect on formulation |
|--------|-----------|---------|----------------------|
| Kirchhoff mode | `use_kirchhoff` | `true` | Enables DC OPF constraints (§5.6) |
| Single bus | `use_single_bus` | `false` | Disables all network constraints (copper plate) |
| Line losses | `use_line_losses` | `true` | Enables loss modeling (§5.5) |
| Kirchhoff threshold | `kirchhoff_threshold` | `0` | Minimum bus count for Kirchhoff activation |
| Tiny-reactance floor | `dc_line_reactance_threshold` | `1e-6` | $\lvert X/V^2 \rvert$ below which the KVL flow term is dropped (§5.6) |
| Tiny-reactance mode | `tiny_reactance_mode` | `"tie"` | `tie`: zero / sub-threshold $X$ is a zero-impedance tie ($\theta_a - \theta_b = \varphi$); `dc`: legacy DC link (§5.6) |
| Relative tie floor | `tiny_reactance_rel_threshold` | `0.01` | Tie floor $`\max(\epsilon_X^{\text{abs}}, \rho_X \cdot \text{median}\,\lvert X/V^2 \rvert)`$; `0` = absolute floor only (§5.6) |
| Tie losses | `tie_line_losses` | `false` | `false`: ties are lossless; `true`: legacy, ties keep their loss model (§5.6) |
| Segment bound | `loss_segment_bound` | `true` | Ordered-fill upper bound on PWL loss segments, $\min(w_k, \max(0, \overline{F} - b_{k-1}))$ (§5.5) |
| Objective scale | `scale_objective` | `1000` | Divides all cost coefficients |
| Theta scale | `scale_theta` | auto | θ_LP = scale_theta · θ (rad); auto = 1/(3·median(X/V²)); 1 = radians; 1000 = milliradians |
| Annual discount rate | `annual_discount_rate` | `0.0` | Multi-year cost discounting (now in `simulation`) |
| Demand fail cost | `demand_fail_cost` | `1000` | Load curtailment penalty for demands without `fcost` |
| Reserve fail cost | `reserve_fail_cost` | *(none)* | Enables reserve shortage variables |
| Input format | `input_format` | `"parquet"` | Time-series input format |
| Output format | `output_format` | `"parquet"` | Solution output format |
| Output compression | `output_compression` | `"zstd"` | Parquet compression codec |

### 6.5 Modeling Modes Summary

The three network modeling modes correspond to standard formulations in the
power systems literature [[11]](#ref11) [[12]](#ref12):

| Mode | Conditions | Variables | Constraints |
|------|-----------|-----------|-------------|
| **Copper plate** | `use_single_bus = true` | $p, \ell, q$ | Global balance only |
| **Transport model** | `use_kirchhoff = false`, multi-bus | $p, \ell, q, f$ | Bus balance + line capacity |
| **DC OPF** | `use_kirchhoff = true`, multi-bus | $p, \ell, q, f, \theta$ | Bus balance + Kirchhoff VL + line capacity |

### 6.6 Stochastic Dual Dynamic Programming (SDDP)

When solving multi-stage problems with many scenarios, GTOpt supports the
**Stochastic Dual Dynamic Programming (SDDP)** algorithm
[[4]](#ref4) [[5]](#ref5) [[6]](#ref6). SDDP decomposes the full problem
into a series of smaller LP sub-problems linked by **Benders cuts** that
approximate the expected future cost.

#### Problem Decomposition

For a planning horizon of $T$ stages and $S$ scenarios, the SDDP algorithm
alternates between:

1. **Forward pass** — solve each stage LP sequentially, using available
   cuts to approximate future costs:

```math
\underset{x_t}{\min} \;\; c_t^\top x_t + \alpha_{t+1}
\quad \text{s.t.} \quad A_t x_t = b_t(s),\; x_t \ge 0,\; \alpha_{t+1} \ge \hat{\alpha}_{t+1}
```

   where $\alpha_{t+1}$ is the future-cost approximation (Benders value
   function) and $\hat{\alpha}_{t+1}$ are the cuts accumulated across
   iterations.

2. **Backward pass** — starting from the last stage, compute a new Benders
   cut from the dual solution of the stage LP and add it to the previous
   stage:

$$
\hat{\alpha}_t^{(k)} : \alpha_t \ge z_t^{(k)} +
\sum_i \bar{\pi}_i^{(k)} \bigl( x_{t-1,i} - \hat{x}_{t-1,i}^{(k)} \bigr)
$$

   where $z_t^{(k)}$ is the optimal value of the stage-$t$ LP in iteration
   $k$, $\bar{\pi}\_i^{(k)}$ are the dual prices (reduced costs) of the
   linking constraints, and $\hat{x}\_{t-1,i}^{(k)}$ is the trial solution
   from the forward pass.

#### Convergence

The algorithm uses two convergence criteria, evaluated at the **end of each
training iteration** (after a paired forward + backward pass).  The final
simulation pass (a forward-only evaluation) does **not** determine
convergence — it inherits the converged status from the last training
iteration.

##### Primary criterion — gap tolerance

The solver declares convergence when the **relative optimality gap** falls
below a tolerance $\varepsilon$:

$$
\text{gap}^{(k)} = \frac{\text{UB}^{(k)} - \text{LB}^{(k)}}{\max\bigl(1, \lvert
\text{UB}^{(k)} \rvert\bigr)} < \varepsilon
\quad \text{and} \quad k \ge k_{\min}
$$

where:
- $\text{LB}^{(k)}$ = average phase-0 objective across scenes (lower bound
  on the expected cost);
- $\text{UB}^{(k)}$ = average total forward-pass cost across scenes (upper
  bound, feasible but not optimal);
- $\varepsilon$ = `convergence_tol` (default $10^{-4}$);
- $k_{\min}$ = `min_iterations` (default 2).

##### Secondary criterion — stationary gap

Some SDDP/Benders problems converge to a **non-zero stationary gap** due
to stochastic noise, problem structure, or numerical conditioning.  In
these cases the primary gap tolerance $\varepsilon$ is never reached, but
the gap stops improving.  The **stationary-gap criterion** detects this
plateau and declares convergence when the gap has not changed significantly
over a look-back window:

```math
\text{gap_change}^{(k)} =
\frac{\lvert \text{gap}^{(k)} - \text{gap}^{(k - w)} \rvert}
     {\max\bigl(10^{-10},\; \text{gap}^{(k - w)}\bigr)}
< \varepsilon_s
\quad \text{and} \quad k \ge k_{\min}
\quad \text{and} \quad k \ge w
```

where:
- $w$ = `stationary_window` (default 10) — number of iterations to look
  back;
- $\varepsilon_s$ = `stationary_tol` (default 0.0 = disabled) — relative
  gap-change tolerance;
- the denominator uses $\text{gap}^{(k-w)}$ (the earlier gap value) as the
  reference, with $10^{-10}$ as a floor to prevent division by zero when the
  gap is near zero.

The secondary criterion only fires when $\varepsilon_s > 0$ and the
primary criterion has **not** been met.  When triggered, the solver sets
both `converged = true` and `stationary_converged = true` in the
iteration result.

##### Convergence output

The `solution.csv` output file includes two convergence columns:

| Column | Description |
|--------|-------------|
| `gap` | Final relative gap $(UB - LB) / \max(1, \lvert UB \rvert)$ |
| `gap_change` | Final gap-change metric (1.0 if stationary criterion disabled) |

| JSON field | Symbol | Default | Description |
|------------|--------|---------|-------------|
| `convergence_tol` | $\varepsilon$ | $10^{-4}$ | Relative gap tolerance (primary) |
| `min_iterations` | $k_{\min}$ | 2 | Minimum training iterations before convergence |
| `max_iterations` | $k_{\max}$ | 100 | Maximum training iterations (hard stop) |
| `stationary_tol` | $\varepsilon_s$ | 0.0 | Stationary gap-change tolerance (0 = disabled) |
| `stationary_window` | $w$ | 10 | Look-back window for stationary gap check |

#### Cut Sharing

Cuts generated in one scenario can be shared across scenarios to accelerate
convergence. Three modes are supported:

| Mode | Description |
|------|-------------|
| `none` | No sharing — each scene uses only its own cuts |
| `expected` | Compute probability-weighted average cut; share to all scenes |
| `max` | Share every cut from every scene to all other scenes |

### 6.7 Apertures — Backward-Pass Scenario Sampling

An **aperture** (from the Portuguese *abertura hidrológica* in PLP) is a
hydrological or stochastic realization used during the SDDP backward pass
to compute the expected future-cost cut. Apertures provide a Monte Carlo
sample of uncertain parameters (typically river inflows / affluents) that
are different from the forward-pass scenarios.

#### Aperture Set $\mathcal{A}$

Let $\mathcal{A} = \lbrace 1, \ldots, A\rbrace$ be the set of apertures with
probability weights $\lbrace\rho_a\rbrace_{a \in \mathcal{A}}$ normalised so that

$$
\sum_{a \in \mathcal{A}} \rho_a = 1.
$$

Each aperture $a$ references a **source scenario** $s_a$ whose affluent
data (flow column bounds) are applied to the cloned backward-pass LP.

#### Expected Benders Cut

Instead of a single backward-pass solve, the solver generates one cut per
aperture and computes the **probability-weighted average** cut:

```math
\hat{\alpha}_t^{(k)} : \alpha_t \;\ge\;
\sum_{a \in \mathcal{A}} \rho_a \Bigl[
  z_{t,a}^{(k)} +
  \sum_i \bar{\pi}_{i,a}^{(k)} \bigl( x_{t-1,i} - \hat{x}_{t-1,i,a}^{(k)} \bigr)
\Bigr]
```

where $z_{t,a}^{(k)}$ and $\bar{\pi}_{i,a}^{(k)}$ are the objective value
and dual prices from solving the stage-$t$ LP with aperture $a$'s parameter
values.

#### JSON Configuration

```json
{
  "simulation": {
    "aperture_array": [
      {"uid": 1, "source_scenario": 1, "probability_factor": 0.5},
      {"uid": 2, "source_scenario": 5, "probability_factor": 0.3},
      {"uid": 3, "source_scenario": 10, "probability_factor": 0.2}
    ]
  }
}
```

| Field | Symbol | Description |
|-------|--------|-------------|
| `source_scenario` | $s_a$ | UID of the scenario providing affluent data |
| `probability_factor` | $\rho_a$ (pre-normalisation) | Weight of this aperture in the expected cut |

When `aperture_array` is absent, the solver falls back to the legacy
`sddp_num_apertures` option: `N > 0` uses the first $N$ scenarios
(equal weights), `N = -1` uses all scenarios, `N = 0` disables apertures.

#### Boundary Cuts

Boundary cuts approximate the expected future cost **beyond the planning
horizon** — i.e. the value function at the terminal stage.  They are
analogous to PLP's "planos de embalse" (reservoir future-cost function).
Each cut constrains the future-cost variable $\alpha$ at the last phase:

```math
\alpha_{T} \;\ge\; \beta_0^{(k)} \;+\; \sum_{i} \rho_i^{(k)} \cdot x_{i,T}
```

where $x_{i,T}$ are the state variables (reservoir volumes, battery SoC)
at the last phase $T$, $\rho_i^{(k)}$ are gradient coefficients, and
$\beta_0^{(k)}$ is the intercept.  Index $k$ runs over the set of
external cuts loaded from the boundary cuts CSV file
(`system.future_cost_array[].cuts_file`, the FutureCost element read by
every method — monolithic, SDDP and cascade).

The boundary cuts are loaded according to the FutureCost `mode`
(`"phi_expectation"`, the PLP-literal NVarPhi variant, is described in
`docs/formulation/sddp-cut-validity.md`):

- **`"noload"`** — skip loading (no terminal value function).
- **`"separated"`** (default) — each cut is assigned to the scene whose
  UID matches the `scene` column in the CSV.  This corresponds to PLP's
  per-simulation future-cost planes.
- **`"combined"`** — all cuts are broadcast to all scenes.

An optional FutureCost `iteration_limit` parameter limits loading to
the last $N$ distinct SDDP iterations (by the `iteration` column in the
CSV, corresponding to PLP's `IPDNumIte`).

> **See also**: [`docs/SDDP_SOLVER.md`](../methods/sddp.md) §4.11 for
> the CSV format specification and configuration details.

> **See also**: [`docs/SDDP_SOLVER.md`](../methods/sddp.md) for the
> complete SDDP algorithm description, convergence criteria, and
> implementation notes.

### 6.8 SDDP Benders Cuts

The SDDP algorithm builds a piecewise-linear outer approximation of the
future cost function $\alpha_t$ by accumulating **Benders cuts** across
iterations. Each cut is a linear inequality added to the LP of phase
$t{-}1$.

#### Optimality Cuts

An **optimality cut** is generated from the dual solution of a feasible
phase-$t$ LP. At iteration $k$:

```math
\alpha_{t-1} \;\ge\; z_t^{(k)} + \sum_{i \in \mathcal{S}_t}
\text{rc}_i^{(k)} \cdot \bigl( x_{t-1,i} - \hat{v}_i^{(k)} \bigr)
\qquad \forall \; k \in \mathcal{K}_{\text{opt}}
```

where:
- $z_t^{(k)}$ is the optimal objective of the phase-$t$ LP (including
  its own $\alpha_t$ term)
- $\mathcal{S}_t$ is the set of state-variable links between phases
  $t{-}1$ and $t$
- $\text{rc}\_i^{(k)}$ is the reduced cost of the dependent column for
  state variable $i$ in phase $t$, unscaled with that column's own
  scale:
  $\text{rc}\_i = \text{rc}^{\text{LP}}\_i \cdot \sigma / \text{col\_scale}\_{\text{dep}}$
  (`StateVariable::reduced_cost_physical`,
  `include/gtopt/state_variable.hpp`).  The source column's scale must
  not be used: Ruiz equilibration scales the two columns independently
- $x_{t-1,i}$ is the source column (decision variable) in phase $t{-}1$
- $\hat{v}_i^{(k)}$ is the trial value from the forward pass

Expanding and rearranging, the cut takes the canonical LP row form:

```math
\alpha_{t-1} - \sum_{i} \text{rc}_i^{(k)} \cdot x_{t-1,i}
\;\ge\; z_t^{(k)} - \sum_{i} \text{rc}_i^{(k)} \cdot \hat{v}_i^{(k)}
```

The right-hand side is stored as the cut's `rhs` field and the
coefficients $\text{rc}_i^{(k)}$ are stored as `(column_index, coefficient)`
pairs in the `StoredCut` structure.

**Units.**  Cuts are built, stored and saved in *physical* units
(money, and money per state unit such as USD/hm³), so they do not depend
on how the LP is scaled.  The solver returns the reduced cost of the
dependent column in its own LP units; it is lifted with **that column's**
scale,

```math
\text{rc}_i^{(k)} \;=\; \text{rc}^{\text{LP}}_{\text{dep}(i)}
\cdot \frac{\sigma_{\text{obj}}}{s_{\text{dep}(i)}},
```

where $\sigma_{\text{obj}}$ is `scale_objective` and $s_{\text{dep}(i)}$
the dependent (phase-$t$ `sini`) column's final scale, including the
equilibration factor.  The source column's scale $s_{\text{src}(i)}$
(phase-$t{-}1$ `efin`) enters only when the physical row is composed
into the source LP.  Ruiz equilibration gives the two columns independent
factors, so using $s_{\text{src}}$ instead of $s_{\text{dep}}$ skews every
slope by $s_{\text{dep}}/s_{\text{src}}$.  Within a phase that spans
several stages, the state $x_{t-1,i}$ is the **last** stage's end
volume.  `test_sddp_cut_exactness.cpp` checks these cuts against
hand-derived value functions under `none`, `row_max` and `ruiz`.

#### Feasibility Cuts

When the forward-pass subproblem at phase $t$ is **infeasible** (the
trial values from phase $t{-}1$ violate constraints), the solver applies
an elastic filter (see [Section 6.13](#613-elastic-filter-and-feasibility))
to obtain dual information. The resulting **feasibility cut** has the
same algebraic form as an optimality cut but is derived from the
elastic-clone's dual solution rather than the original LP.

In GTOpt, the `CutType` enum distinguishes the two:

| `CutType` | Origin | Shared across scenes? |
|-----------|--------|----------------------|
| `Optimality` | Feasible backward-pass solve | Yes (per `cut_sharing_mode`) |
| `Feasibility` | Elastic filter clone | Never |

### 6.9 Boundary Cuts and Future Cost

Boundary cuts approximate the expected future cost **beyond the planning
horizon** $T$ -- the terminal value function $\alpha_T$. They are
loaded from an external CSV file and added to the last phase of each
scene's LP before solving begins.

Each boundary cut $k$ constrains the future-cost variable at the last
phase:

```math
\alpha_T \;\ge\; \beta_0^{(k)} + \sum_{i} \rho_i^{(k)} \cdot x_{i,T}
\qquad \forall \; k \in \mathcal{K}_{\text{boundary}}
```

where $x_{i,T}$ are the state variables (reservoir volumes, battery SoC)
at phase $T$, $\rho_i^{(k)}$ are gradient coefficients, and
$\beta_0^{(k)}$ is the intercept (RHS).

Boundary cuts are analogous to PLP's "planos de embalse" (reservoir
future-cost function). They are supported by both the SDDP and
monolithic solvers. When used with the monolithic solver, an $\alpha$
variable is added to the last phase and the cuts are applied identically.
$x_{i,T}$ is the end volume of the **last stage** of the last phase; for
the monolithic single-phase LP this is the end of the horizon.

**Reported bounds.**  Let
$\Phi(x) = \max_k \bigl(\beta_0^{(k)} + \sum_i \rho_i^{(k)} x_i\bigr)$
be the terminal FCF.  The SDDP upper bound adds up the phase costs and
leaves out the terminal $\alpha_T$, while the lower bound includes
$\Phi$ through the cut recursion.  With the α-rebase (`mean_shift`), the
UB carries the constant $\bar c$ instead.  At convergence, therefore,

```math
\text{LB} - \text{UB} \;=\; \Phi(x_T^\star) - \bar c ,
```

which is positive (LB above UB) whenever the terminal FCF at the final
volumes is above $\bar c$, even when the cuts are exact.  The size of
the gap depends on the cuts, not on how the LP is scaled.

> **See also**: [`docs/SDDP_SOLVER.md`](../methods/sddp.md) Section 4.11
> for the CSV format specification, load modes, and iteration filtering.

### 6.10 Hot Start

The SDDP solver supports **hot-starting** from previously saved cuts to
accelerate convergence. When `hot_start` is enabled:

1. The solver loads all valid cut files from the cut directory
   (files matching `scene_<N>.csv` or `sddp_cuts.csv`)
2. Error files (prefixed with `error_`) from infeasible scenes in
   previous runs are automatically **skipped**
3. Loaded cuts are injected into the per-phase LPs before the first
   forward pass, providing an initial outer approximation of the
   future cost function
4. The SDDP iterations then refine this approximation further

Hot start is particularly useful for:
- **Interrupted runs**: if the solver was stopped (sentinel file, time
  limit, or external signal), the saved cuts allow resumption without
  losing progress
- **Parameter sensitivity**: re-solving with slightly modified parameters
  while retaining the bulk of the cut approximation
- **Incremental refinement**: running additional iterations on top of a
  previous solution

Configuration:
```json
{
  "options": {
    "sddp_options": {
      "cut_recovery_mode": "replace",
      "cut_directory": "cuts"
    }
  }
}
```

### 6.11 Solver Timeouts

LP subproblem solves have configurable time limits to prevent the solver
from stalling on difficult instances.

| Option | Scope | Default | Description |
|--------|-------|---------|-------------|
| `sddp_options.forward_solver_options.time_limit` | SDDP forward pass | unset (none) | Time limit per LP solve (seconds) |
| `sddp_options.aperture_timeout` | SDDP backward pass | 0 (none) | Time limit per aperture LP solve (seconds) |
| `monolithic_options.solver_options.time_limit` | Monolithic solver | unset (none) | Time limit per scene LP solve (seconds) |

When a timeout is exceeded:
- The LP is saved to a debug file in `log_directory`
- A CRITICAL message is logged with the phase, scene, and elapsed time
- For SDDP: the scene is marked as failed for that iteration
- For apertures: the timed-out aperture is skipped and the solver
  continues with remaining apertures

### 6.12 Monolithic vs SDDP Equivalence

Under specific conditions, the monolithic and SDDP solvers produce
**identical optimal solutions**.

**Theorem (Finite convergence).**
For a deterministic problem (single scenario $|\mathcal{S}| = 1$) with
$T$ phases, linear cost-to-go functions, and no integer variables, the
SDDP algorithm converges finitely to the exact monolithic optimum
[[3]](#ref3)[[4]](#ref4).

**Formal statement.** Let $z^*_{\text{mono}}$ be the monolithic optimal
value:

```math
z^*_{\text{mono}} = \min \sum_{t=1}^{T} c_t^\top x_t
\quad \text{s.t.} \quad A_t x_t + B_t x_{t-1} \ge b_t,\;
x_t \ge 0 \;\; \forall \; t
```

and let $z^{(k)}\_{\text{SDDP}}$ be the SDDP lower bound at iteration
$k$. Then $z^{(k)}\_{\text{SDDP}} \to z^\*\_{\text{mono}}$ in a finite
number of iterations, provided:

1. **Single scenario** (or all scenarios evaluated) -- the backward
   pass sees the same data as the forward pass
2. **No aperture sampling** -- apertures use the full scenario set
3. **No cut sharing** (`cut_sharing_mode = "none"`) -- cuts are not
   broadcast across scenes
4. **Convergence achieved** -- the gap
   $(UB - LB) / \max(1, |UB|) < \varepsilon$

When any of these conditions is violated, the SDDP solution may differ
from the monolithic optimum (typically providing a lower bound).

> **See also**: [`docs/MONOLITHIC_SOLVER.md`](../methods/monolithic.md)
> Section 3 for additional details on equivalence conditions.

### 6.13 Elastic Filter and Feasibility

When a forward-pass subproblem at phase $t$ is infeasible (the trial
values $\hat{v}_i$ from phase $t{-}1$ violate the current phase's
constraints), the SDDP solver applies an **elastic filter** to recover
dual information for cut generation.

The elastic filter procedure:

1. **Clone** the phase-$t$ LP (deep copy via `OsiSolverInterface::clone()`)
2. **Relax** fixed state-variable columns to their physical bounds
3. **Add penalized slack variables** $s_i^+, s_i^-$ for each state
   variable link:

$$
x_i + s_i^+ - s_i^- = \hat{v}_i, \qquad s_i^+, s_i^- \ge 0
$$

4. **Modify the objective** to include elastic penalties:

$$
\min \quad c^\top x + M \sum_i (s_i^+ + s_i^-)
$$

   where $M$ is the `elastic_penalty` parameter (default $10^6$)

5. **Solve** the cloned LP with the elastic objective
6. **Extract** dual information from the clone for cut generation
7. **Discard** the clone -- the original LP is never modified

Two elastic filter modes control what happens with the extracted
information:

| Mode | JSON value | Behavior |
|------|-----------|----------|
| Single feasibility cut | `"single_cut"` (alias `"cut"`) | One Benders feasibility cut per infeasibility on phase $t{-}1$ |
| Multi-cut | `"multi_cut"` | One Benders cut + one bound cut per activated slack variable |
| Chinneck IIS (default) | `"chinneck"` (alias `"iis"`) | Per-bound cuts on the irreducible infeasible subset only |

All three modes are standard Nested Benders Decomposition variants
with cut-based convergence guarantees. The `"chinneck"` default runs an
extra IIS-detection LP solve per fcut event to drop non-essential
relaxed bounds before emitting per-bound cuts; in the worst case
(degenerate primal optimum) it falls back to behaviour identical to
`"multi_cut"`. A historical fourth mode (`"backpropagate"`, PLP-style
direct bound updates) was removed; legacy JSON values silently fall
back to the default.

> **See also**: [`docs/SDDP_SOLVER.md`](../methods/sddp.md) Section 5.4
> for a detailed comparison of elastic filter modes.

### 6.14 Cascade Solver — Multi-Level Decomposition

The **Cascade solver** (`method = "cascade"`) extends the SDDP
algorithm with an outer loop over multiple **levels**, each with its own LP
formulation and solver parameters.  The key idea is progressive refinement:
start from a simplified model (e.g. single-bus / copper-plate) that
converges quickly, then warm-start subsequent levels with increasingly
detailed network models.

> **See also**: [`docs/CASCADE_SOLVER.md`](../methods/cascade.md) for the
> complete configuration reference, implementation details, and worked
> examples.

#### Multi-Level Structure

A cascade consists of $L$ ordered levels $\ell = 0, 1, \ldots, L-1$.
Each level defines:

- **LP formulation** $\mathcal{M}_\ell$: network topology, Kirchhoff
  constraints, line losses, scaling (via `model_options`)
- **Solver parameters** $\Theta_\ell$: iteration limits, apertures,
  convergence tolerance (via `sddp_options`)
- **Transition rules** $\mathcal{T}_\ell$: cut inheritance and dual
  thresholds (via `transition`)

At each level, the cascade solver internally runs an SDDP solver (§6.6)
with the specified LP and parameters.

#### Cut Inheritance

When level $\ell$ receives cuts from level $\ell{-}1$, the transfer uses
LP **column names** (not indices) for cross-LP resolution.  This allows
cuts generated with one LP structure (e.g. single-bus, where theta columns
do not exist) to be applied to a different structure (e.g. multi-bus with
Kirchhoff).

Formally, a stored optimality cut from level $\ell{-}1$:

```math
\alpha_t \;\ge\; z_t^{(k)} + \sum_{i \in \mathcal{S}}
\bar{\pi}_i^{(k)} (x_{t{-}1,i} - \hat{x}_{t{-}1,i}^{(k)})
```

is serialized with column names $\lbrace\text{name}(i)\rbrace$ and deserialized at
level $\ell$ by resolving each name to the corresponding column index in
the new LP $\mathcal{M}_\ell$.  Columns that do not resolve (e.g. a theta
variable in a single-bus LP) are skipped.

**Cut forgetting**: when `inherit_optimality_cuts = N` (positive integer),
the inherited cuts are kept for at most $N$ training iterations, then
discarded.  The solver re-solves with only self-generated cuts for the
remaining iteration budget.

#### Iteration Budget

The cascade supports two levels of iteration control:

- **Per-level budget**: $k^{\ell}_{\max}$ = `CascadeLevelMethod::max_iterations`
- **Global budget**: $K_{\max}$ = `CascadeOptions::sddp_options::max_iterations`

The effective per-level limit is:

```math
k^{\ell}_{\text{eff}} = \min\bigl(k^{\ell}_{\max},\; K_{\max} - \sum_{j < \ell} k^{j}_{\text{used}}\bigr)
```

When the global budget is exhausted, the cascade stops regardless of which
level is active.

#### Convergence Behavior

- If level $\ell$ converges ($\text{gap}\_\ell < \varepsilon\_\ell$) at an
  intermediate position ($\ell < L-1$), the cascade **continues** to the
  next level for further refinement.
- If the **final** level ($\ell = L-1$) converges, the solver returns
  immediately.
- If the global iteration budget is exhausted, the solver stops and
  reports non-convergence.
- The overall convergence flag is taken from the last iteration result
  across all levels.

#### Typical Cascade Patterns

```
Pattern 1: Uninodal → Full Network (2 levels)
  Level 0: use_single_bus=true, Benders (no apertures), fast convergence
  Level 1: use_kirchhoff=true, SDDP with apertures, inherits cuts

Pattern 2: Training → Refinement (2 levels, same LP)
  Level 0: N iterations, generate cuts
  Level 1: reuse LP, inherit cuts → converge faster

Pattern 3: Progressive (3 levels)
  Level 0: uninodal Benders → rough trajectory
  Level 1: full network + cuts from L0 → guided convergence
  Level 2: reuse L1's LP + inherit cuts from L1 → fast final refinement
```

---

## 7. Mapping: JSON Fields → Mathematical Symbols

This table maps the JSON input fields (used in planning files) to the
mathematical symbols used in this formulation.

### System Options

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `options.scale_objective` | $\sigma$ | Objective scaling |
| `options.scale_theta` | $\sigma_\theta$ | Angle scaling |
| `simulation.annual_discount_rate` | $r$ | Discount rate (also accepted in `options`) |
| `options.use_kirchhoff` | — | Enable DC OPF |
| `options.use_single_bus` | — | Copper-plate mode |
| `options.use_line_losses` | — | Enable line losses |
| `options.demand_fail_cost` | $c^{\text{fail}}_d$ | Curtailment penalty |
| `options.reserve_fail_cost` | $c^{\text{rfail}}$ | Reserve penalty |
| `options.model_options.objective_mode` | — | `"cost"` (default) or `"emissions"` (§5.14 bis). Emission price and cap are `EmissionZone` fields (see Emissions below) |
| `options.method` | — | Solver: `"monolithic"` or `"sddp"` |
| `options.variable_scales` | $\sigma_x$ | Per-variable scale factors (Section 6.3) |
| `system.future_cost_array[].cuts_file` | — | CSV with boundary cuts for last phase (Section 6.9) |
| `system.future_cost_array[].valuation` | — | Valuation mode: `"end_of_horizon"` or `"present_value"` |
| `system.future_cost_array[].mode` | — | Load mode: `"noload"`, `"separated"`, `"combined"`, `"phi_expectation"` |
| `system.future_cost_array[].iteration_limit` | — | Keep the cuts of the last $N$ source iterations (0 = all) |
| `options.sddp_options.hot_start` | — | Enable hot-start from saved cuts (Section 6.10) |
| `options.sddp_options.save_per_iteration` | — | Save cuts after each iteration (default: true) |
| `options.sddp_options.solve_timeout` | — | LP solve time limit in seconds (Section 6.11) |
| `options.sddp_options.aperture_timeout` | — | Aperture LP time limit in seconds (Section 6.11) |

### Simulation Structure

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `simulation.scenario_array[].probability_factor` | $\pi_s$ | Scenario weight |
| `simulation.stage_array[].discount_factor` | $\delta_t^{\text{user}}$ | Stage discount |
| `simulation.block_array[].duration` | $\Delta_b$ | Block duration (h) |
| `simulation.aperture_array[].source_scenario` | $s_a$ | Source scenario UID for aperture |
| `simulation.aperture_array[].probability_factor` | $\rho_a$ | Aperture probability weight |

### Generator

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `generator_array[].gcost` | $c_g$ | Variable cost (\$/MWh) |
| `generator_array[].pmin` | $\underline{P}_g$ | Min output (MW) |
| `generator_array[].pmax` | $\overline{P}_g$ | Max output (MW) |
| `generator_array[].capacity` | $\bar{C}_g^0$ | Installed capacity (MW) |
| `generator_array[].lossfactor` | $\lambda_g$ | Injection loss |
| `generator_array[].emission_rate` | $f_{\nu,t}$ | Legacy CO₂ rate (tCO₂/MWh), folded into a `co2` `EmissionSource` |
| `generator_array[].fuel` | — | FK to `Fuel` (price, emission factors) |
| `generator_array[].pmax_segments` | $\overline{P}_{g,k}$ | Piecewise heat-rate power breakpoints (MW) |
| `generator_array[].heat_rate_segments` | $h_{g,k}$ | Heat rate per segment (fuel unit/MWh) |
| `generator_array[].expcap` | $M_g$ | MW per module |
| `generator_array[].expmod` | $\overline{m}_g$ | Max modules |
| `generator_array[].annual_capcost` | $K_g^{\text{cap}}$ | \$/year per module |

### Demand

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `demand_array[].lmax` | $\overline{L}_d$ | Max demand (MW) |
| `demand_array[].lossfactor` | $\lambda_d$ | Withdrawal loss |

### Line

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `line_array[].tmax_ab` | $\overline{F}_l^{ab}$ | Capacity $a \to b$ (MW) |
| `line_array[].tmax_ba` | $\overline{F}_l^{ba}$ | Capacity $b \to a$ (MW) |
| `line_array[].reactance` | $X_l$ | Reactance (p.u.) |
| `line_array[].tcost` | $c_l$ | Transfer cost (\$/MWh) |
| `line_array[].lossfactor` | $\lambda_l$ | Transmission loss |

### Battery

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `battery_array[].emin` | $\underline{E}_e$ | Min energy (MWh) |
| `battery_array[].emax` | $\overline{E}_e$ | Max energy (MWh) |
| `battery_array[].eini` | $E_e^0$ | Initial energy (MWh) |
| `battery_array[].efin` | $E_e^{\text{fin}}$ | Final energy (MWh) |
| `battery_array[].input_efficiency` | $\eta_e^{\text{in}}$ | Charge efficiency |
| `battery_array[].output_efficiency` | $\eta_e^{\text{out}}$ | Discharge efficiency |
| `battery_array[].annual_standing_loss` | $\mu_e$ | Annual self-discharge |
| `variable_scales` (Battery, energy) | $\sigma_E$ | Energy variable scale factor (Section 6.3) |

### Converter

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `converter_array[].conversion_rate` | $\rho_v$ | Power conversion factor |

### Reserve Zone

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `reserve_zone_array[].urreq` | $\overline{R}_z^{\text{up}}$ | Up-reserve req (MW) |
| `reserve_zone_array[].drreq` | $\overline{R}_z^{\text{dn}}$ | Down-reserve req (MW) |

### Reserve Provision

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `reserve_provision_array[].urmax` | — | Max up-reserve (MW) |
| `reserve_provision_array[].urcost` | $c_p^{\text{ur}}$ | Up-reserve cost |
| `reserve_provision_array[].drmax` | — | Max down-reserve (MW) |
| `reserve_provision_array[].drcost` | $c_p^{\text{dr}}$ | Down-reserve cost |
| `reserve_provision_array[].factor` | $\gamma_{p,t}$ | Provision factor |

### Hydro Components

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `waterway_array[].fmin` | $\underline{Q}_w$ | Min water flow (m³/s) |
| `waterway_array[].fmax` | $\overline{Q}_w$ | Max water flow (m³/s) |
| `waterway_array[].lossfactor` | $\lambda_w$ | Transport loss |
| `reservoir_array[].emin` | $\underline{V}_r$ | Min volume (hm³) |
| `reservoir_array[].emax` | $\overline{V}_r$ | Max volume (hm³) |
| `variable_scales` (Reservoir, energy) | $\sigma_V$ | Volume variable scale factor (Section 6.3) |
| `turbine_array[].conversion_rate` | $\kappa_u$ | Water-to-power factor |
| `turbine_array[].main_reservoir` | — | Reservoir for efficiency lookup |
| `reservoir_production_factor_array[].mean_production_factor` | $\bar{\kappa}_u$ | Fallback production factor |
| `reservoir_production_factor_array[].segments[].volume` | $v_i$ | Volume breakpoint (hm³) |
| `reservoir_production_factor_array[].segments[].slope` | $m_i$ | Marginal production factor per hm³ |
| `reservoir_production_factor_array[].segments[].constant` | $c_i$ | Production factor intercept |
| `reservoir_seepage_array[].constant` | $a_i$ | Default seepage constant |
| `reservoir_seepage_array[].slope` | $b_i$ | Default seepage slope |
| `reservoir_seepage_array[].segments[].volume` | $V_k$ | Volume breakpoint (hm³) |
| `reservoir_seepage_array[].segments[].slope` | $b_k$ | Seepage slope at breakpoint |
| `reservoir_seepage_array[].segments[].constant` | $c_k$ | Seepage rate at breakpoint |

### Hydro Pump

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `pump_array[].waterway` | — | FK to the pumping waterway |
| `pump_array[].demand` | — | FK to the electrical demand (pump motor load) |
| `pump_array[].pump_factor` | $\phi_h$ | Power consumed per unit flow (MW/(m³/s)) |
| `pump_array[].efficiency` | $\eta_h$ | Pump efficiency (p.u., default 1.0) |
| `pump_array[].capacity` | $\overline{Q}_h$ | Maximum pump flow (m³/s) |
| `pump_array[].main_reservoir` | — | Reservoir whose volume drives variable pump factor (SDDP) |

### LNG Terminal

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `lng_terminal_array[].emin` | $\underline{V}_\tau$ | Minimum tank volume (m³) |
| `lng_terminal_array[].emax` | $\overline{V}_\tau$ | Maximum tank volume (m³) |
| `lng_terminal_array[].eini` | $e_\tau^0$ | Initial tank volume (m³) |
| `lng_terminal_array[].efin` | $e_\tau^{\text{fin}}$ | Target final tank volume (m³) |
| `lng_terminal_array[].ecost` | — | Holding cost per m³ stored (\$/m³) |
| `lng_terminal_array[].annual_standing_loss` | $\lambda_\tau$ | Annual boil-off rate (p.u./year) |
| `lng_terminal_array[].delivery` | $D_{\tau,t}$ | Scheduled LNG arrival volume per stage (m³) |
| `lng_terminal_array[].sendout_max` | — | Max regasification rate (m³/h) |
| `lng_terminal_array[].sendout_min` | — | Min regasification rate (m³/h) |
| `lng_terminal_array[].spillway_cost` | — | Penalty for venting LNG (\$/m³) |
| `lng_terminal_array[].spillway_capacity` | — | Maximum venting rate (m³/h) |
| `lng_terminal_array[].flow_conversion_rate` | $\rho_\tau$ | Unit-conversion factor m³/(m³/h·h) (default 1.0) |
| `lng_terminal_array[].mean_production_factor` | — | Energy content of LNG (MWh/m³, for SDDP state valuation) |
| `lng_terminal_array[].soft_emin` | — | Soft minimum tank volume (m³, penalised) |
| `lng_terminal_array[].soft_emin_cost` | — | Penalty for crossing `soft_emin` (\$/m³) |
| `lng_terminal_array[].generators[].generator` | — | FK to the consuming generator |
| `lng_terminal_array[].generators[].heat_rate` | $\eta_g^{hr}$ | Fuel consumption per MWh (m³\_LNG/MWh, default 1.0) |

### Unit Commitment

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `commitment_array[].generator` | — | FK to the committed generator |
| `commitment_array[].startup_cost` | $\text{SU}_g$ | Startup cost (\$/start) |
| `commitment_array[].shutdown_cost` | $\text{SD}_g$ | Shutdown cost (\$/stop) |
| `commitment_array[].noload_cost` | $\text{NL}_g$ | No-load cost while committed (\$/hr) |
| `commitment_array[].min_up_time` | $T_g^{\text{up}}$ | Minimum up time (hours) |
| `commitment_array[].min_down_time` | $T_g^{\text{dn}}$ | Minimum down time (hours) |
| `commitment_array[].ramp_up` | — | Ramp-up limit while online (MW/hr) |
| `commitment_array[].ramp_down` | — | Ramp-down limit while online (MW/hr) |
| `commitment_array[].startup_ramp` | — | Max output in startup block (MW) |
| `commitment_array[].shutdown_ramp` | — | Max output in shutdown block (MW) |
| `commitment_array[].initial_status` | $u_g^{\text{init}}$ | Initial on/off state (1 = online) |
| `commitment_array[].initial_hours` | — | Hours in current state at t=0 |
| `commitment_array[].relax` | — | LP relaxation: binary → continuous in [0,1] |
| `commitment_array[].must_run` | — | Force committed: $u = 1$ always |
| `commitment_array[].commitment_period` | — | Binary variable resolution (hours) |
| `commitment_array[].uc_formulation` | — | `energy_block` / `power_based` (overrides `model_options.uc_formulation`, §5.15 bis) |
| `commitment_array[].startup_trajectory` | $P^{SU}_i$ | Power-based start-up trajectory (MW, every `trajectory_step` h) |
| `commitment_array[].shutdown_trajectory` | $P^{SD}_i$ | Power-based shut-down trajectory (MW) |
| `commitment_array[].trajectory_step` | $h$ | Trajectory sample spacing (h, default 1) |
| `commitment_array[].hot_start_cost` | — | Startup cost when recently offline (\$/start) |
| `commitment_array[].warm_start_cost` | — | Startup cost at medium offline (\$/start) |
| `commitment_array[].cold_start_cost` | — | Startup cost when long offline (\$/start) |
| `commitment_array[].hot_start_time` | — | Hot start when offline $<$ this (h) |
| `commitment_array[].cold_start_time` | — | Cold start when offline $\geq$ this (h) |
| `simple_commitment_array[].uid` | — | Unique identifier (deprecated alias of `commitment_array`, §5.16) |
| `simple_commitment_array[].name` | — | Human-readable name |
| `simple_commitment_array[].active` | — | Activation schedule (bool\|array\|string) |
| `simple_commitment_array[].generator` | — | FK to the committed generator |
| `simple_commitment_array[].dispatch_pmin` | $\underline{P}_g^d$ | Dispatch minimum when committed (MW), read as `Commitment.pmin` |
| `simple_commitment_array[].relax` | — | LP relaxation: binary → continuous |
| `simple_commitment_array[].must_run` | — | Force committed: $u = 1$ always |
| `stage_array[].chronological` | — | `true` when blocks are hourly-consecutive (full UC; `false` = block formulation, §5.16) |

### Inertia Zone and Provision

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `inertia_zone_array[].requirement` | $\overline{R}_{z,t,b}^{H}$ | Min inertia requirement (MWs) |
| `inertia_zone_array[].cost` | — | Shortage penalty (\$/MWs) |
| `inertia_provision_array[].generator` | — | FK to the providing generator |
| `inertia_provision_array[].inertia_zones` | — | Colon-separated list of InertiaZone IDs/names |
| `inertia_provision_array[].inertia_constant` | $H_p$ | Machine inertia constant (seconds) |
| `inertia_provision_array[].rated_power` | $S_p$ | Rated apparent power (MVA) |
| `inertia_provision_array[].provision_max` | $\overline{R}_{p,t,b}^{H}$ | Max inertia provision (MW) |
| `inertia_provision_array[].provision_factor` | $\Phi_{p,t}$ | Effectiveness factor (MWs/MW) |
| `inertia_provision_array[].cost` | — | Provision cost (\$/MW) |

### Emissions

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `emission_array[]` | $\epsilon$ | Pollutant (`co2`, `ch4`, `nox`, …) |
| `emission_zone_array[].emissions[].weight` | $\mu_{z,\epsilon}$ | CO₂-eq weight (GWP), default 1 |
| `emission_zone_array[].price` | $c^{\text{em}}_{z,t}$ | Emission price (\$/t) |
| `emission_zone_array[].cap` | $\overline{E}_{z,t}$ | Cap per (scenario, stage) (t) |
| `emission_zone_array[].cap_cost` | $c^{\text{cap}}_{z,t}$ | Soft-cap penalty (\$/t); hard cap when unset |
| `emission_zone_array[].allowance_pool` | $a(z)$ | FK to `AllowancePool`; replaces the cap row |
| `emission_source_array[].rate` | $f_{\nu,t}$ | Combustion rate (t/MWh) |
| `emission_source_array[].upstream_rate` | $f^{\text{up}}_{\nu,t}$ | Upstream (well-to-tank) rate (t/MWh) |
| `emission_source_array[].fuel_rate` | $\phi_{\nu,t}$ | Combustion factor on the fuel burn (t/fuel unit) |
| `emission_source_array[].upstream_fuel_rate` | $\phi^{\text{up}}_{\nu,t}$ | Upstream factor on the fuel burn (t/fuel unit) |
| `generator_array[].emission_captures[].rate` | $\eta^{\text{cap}}_{g,\epsilon,t}$ | CCS capture fraction |
| `generator_array[].emission_captures[].cost` | $c^{\text{ccs}}_{g,\epsilon,t}$ | Capture cost (\$/t captured) |
| `fuel_array[].emission_factors[]` | — | Per-fuel-unit factors; with scalar `heat_rate` they generate sources |

The former `options.model_options.emission_cost` and `emission_cap`
were removed; their role is taken by a system-wide zone `price` and
`cap`.

---

### Gas Network

| JSON Path | Symbol | Description |
|-----------|--------|-------------|
| `options.gas_options.gas_specific_gravity` | $G$ | Gas specific gravity (air = 1, default 0.6) |
| `options.gas_options.gas_temperature` | $T$, $T_f$ | Flowing temperature (K, default 288.15) |
| `options.gas_options.compressibility` | $Z$ | Compressibility factor (default 0.9) |
| `options.gas_options.slp_iterations` | $M$ | SLP passes (monolithic, default 0) |
| `options.gas_options.slp_damping` | $\omega$ | SLP damping (default 0.5) |
| `options.gas_options.slp_proximal_cost` | $\rho$ | Proximal cost on $d^\pm$ (default $10^{-6}$) |
| `gas_node_array[].pmin`, `.pmax` | $\underline{p}_n$, $\bar p_n$ | Pressure band (bar); $\varpi_n \in [\underline{p}_n^2, \bar p_n^2]$ |
| `gas_node_array[].pressure_cost` | $c^{\varpi}_n$ | Cost on $\varpi_n$ (\$/(bar² h)) |
| `gas_node_array[].elevation` | $h$ | Elevation (m) |
| `gas_supply_array[].fmin`, `.fmax` | $\underline{x}_g$, $\bar x_g$ | Injection bounds (km³/h) |
| `gas_supply_array[].cost` | $c_g$ | Gas cost (\$/km³) |
| `gas_supply_array[].volume` | $V_g$ | Stage volume (km³) |
| `gas_demand_array[].load` | $D_d$ | Gas load (km³/h) |
| `gas_demand_array[].fail_cost` | $c^{fail}_d$ | Shedding cost (\$/km³) |
| `gas_pipe_array[].weymouth_k` | $K$ | Weymouth constant (bar²/(km³/h)²) |
| `gas_pipe_array[].length`, `.diameter` | $L$, $D$ | Geometry (km, mm) |
| `gas_pipe_array[].efficiency` | $E$ | Pipeline efficiency (default 0.92) |
| `gas_pipe_array[].elevation_exponent` | $s$ | Elevation exponent |
| `gas_pipe_array[].fmax` | $\bar F$ | Flow capacity (km³/h) |
| `gas_pipe_array[].segments` | $N$ | Number of tangents (default 8) |
| `gas_pipe_array[].reference_flow` | $f_0$ | First-pass reference flow (km³/h) |
| `gas_pipe_array[].linepack_coeff` | $c$ | Line pack per bar (km³/bar) |
| `gas_pipe_array[].linepack_pressure_ref` | $p_r$ | Linearisation pressure (bar) |
| `gas_pipe_array[].linepack_ini` | $L^0_\ell$ | Initial line pack (km³) |
| `gas_compressor_array[].fmax` | $\bar F$ | Throughput capacity (km³/h) |
| `gas_compressor_array[].max_ratio` | $\bar\beta$ | Maximum pressure ratio |
| `gas_compressor_array[].fuel_fraction` | $\gamma^{ff}$ | Fixed fuel fraction |
| `gas_compressor_array[].gamma`, `.alpha` | $\gamma$, $\alpha$ | Saldarriaga fuel parameters |
| `gas_compressor_array[].op_flow`, `.op_ratio`, `.op_suction_pressure` | $\Psi_0$, $\beta_0$, $p_{in,0}$ | Taylor operating point(s) |
| `gas_compressor_array[].power_max` | $\bar P^{el}$ | Electric drive rating (MW) |
| `gas_turbine_array[].heat_rate` | $h_k$ | Fuel per MWh (km³/MWh) |
| `gas_turbine_array[].min_pressure` | $p_k$ | Minimum fuel-gas pressure (bar) |
| `gas_turbine_array[].pressure_band` | $\delta_k$ | Gate band (bar, default 0.5) |
| `gas_turbine_array[].nominal_pressure` | $p^{nom}_k$ | Full-output pressure (bar) |
| `gas_turbine_array[].derate_min_output` | $o_k$ | Output share at `min_pressure` |

## 8. Cross-References

- **[Planning Guide](../planning-guide.md)** — How to set up planning
  problems with stages, scenarios, and expansion options.
- **[Input Data Reference](../input-data.md)** — Complete specification of
  JSON and Parquet input file formats with all supported fields.
- **[Usage Guide](../usage.md)** — How to run the `gtopt` solver, CLI
  flags, and output interpretation.
- **[Contributing Guide](../../CONTRIBUTING.md)** — Code style, testing, and
  contribution guidelines.
- **[Building Guide](../../BUILDING.md)** — Detailed build instructions,
  dependencies, and troubleshooting.
- **[Scripts Guide](../scripts-guide.md)** — Python conversion utilities
  (plp2gtopt, igtopt, pp2gtopt, ts2gtopt, cvs2parquet).
- **[SDDP Solver](../methods/sddp.md)** — Complete SDDP algorithm
  description, convergence criteria, cut sharing, and configuration.
- **[Monolithic Solver](../methods/monolithic.md)** — Default solver
  description, boundary cuts, and equivalence with SDDP.
- **[Cascade Solver](../methods/cascade.md)** — Multi-level hybrid SDDP
  solver with cut inheritance and progressive LP refinement.

---

## 9. References

### FESOP and GTOpt Publications

<a id="ref4"></a>
**[4]** Buitrago Villada, M.P., García Bujanda, C.E., Sierra Baeza, E.,
and Matus, M. (2022). "Optimal Expansion and Reliable Renewable Energy
Integration in Long-Term Planning Using FESOP." *2022 IEEE Kansas Power
and Energy Conference (KPEC)*, pp. 1–5.
DOI: [10.1109/KPEC54747.2022.9814781](https://doi.org/10.1109/KPEC54747.2022.9814781).
Open access: [ResearchGate](https://www.researchgate.net/publication/358423681_Optimal_Expansion_and_Reliable_Renewable_Energy_Integration_in_Long-Term_Planning_Using_FESOP).

> The foundational paper for the FESOP framework on which GTOpt is based.
> Presents results of applying FESOP to analyze optimal expansion of the
> Aysén electric system over a 30-year horizon with four demand growth
> scenarios, demonstrating renewable energy integration with spinning
> reserve requirements and battery storage systems.

<a id="ref5"></a>
**[5]** Pereira-Bonvallet, E., Puschel-Lovengreen, S., Matus, M., and
Moreno, R. (2016). "Optimizing Hydrothermal Scheduling with Non-Convex
Irrigation Constraints: Case on the Chilean Electricity System." *Energy
Procedia*, 87, pp. 132–138.
DOI: [10.1016/j.egypro.2015.12.342](https://doi.org/10.1016/j.egypro.2015.12.342).

> Presents the hydrothermal coordination approach that forms the
> operational planning core of the PLP/FESOP lineage. Demonstrates SDDP-
> based scheduling with non-convex irrigation constraints in the Chilean
> Central Interconnected System (SIC).

<a id="ref6"></a>
**[6]** Benavides, C., Matus, M., Sierra, E., Sepúlveda, R., Ruz, A.M.,
and Gallardo, F. (2019). "Value contribution of solar plants to the
Chilean electric system." *AIP Conference Proceedings* 2126, 120008
(SolarPACES 2018).
DOI: [10.1063/1.5117671](https://doi.org/10.1063/1.5117671).

> Quantifies the value of solar energy in the Chilean National Electric
> System using long-term planning, hydrothermal coordination, and short-
> term operation models — demonstrating the multi-model planning workflow
> that motivated GTOpt's unified approach.

<a id="ref7"></a>
**[7]** Benavides, C., Alvarez, R., Torres, R., Moreno, R., Matus, M.,
Muñoz, D., Gonzalez, J.M., Jiménez-Estévez, G., and Palma-Behnke, R.
(2019). "Capacity payment allocation in hydrothermal power systems with
high shares of renewable energies." *E3S Web of Conferences*, 140, 11008.
DOI: [10.1051/e3sconf/201914011008](https://doi.org/10.1051/e3sconf/201914011008).

> Proposes a capacity valuation framework for variable renewable generation
> in hydrothermal systems, relevant to GTOpt's reserve and capacity
> expansion modeling.

<a id="ref8"></a>
**[8]** Matus, M., Cáceres, N., Puschel-Lovengreen, S., and Moreno, R.
(2015). "Chebyshev based continuous time power system operation approach."
*2015 IEEE Power & Energy Society General Meeting*, pp. 1–5.
DOI: [10.1109/PESGM.2015.7286570](https://doi.org/10.1109/PESGM.2015.7286570).

> Introduces a continuous-time representation for power system operations
> using Chebyshev polynomials, addressing the time-discretization
> challenges that GTOpt handles via its block/stage/scenario hierarchy.

<a id="ref9"></a>
**[9]** Matus, M., Sáez, D., Favley, M., Suazo-Martinez, C., Moya, J.,
Jiménez-Estévez, G., Palma-Behnke, R., Olguín, G., and Jorquera, P.
(2012). "Identification of Critical Spans for Monitoring Systems in
Dynamic Thermal Rating." *IEEE Transactions on Power Delivery*, 27(2),
pp. 1002–1009.
DOI: [10.1109/TPWRD.2012.2185254](https://doi.org/10.1109/TPWRD.2012.2185254).
Open access:
[ResearchGate](https://www.researchgate.net/publication/254057820_Identification_of_Critical_Spans_for_Monitoring_Systems_in_Dynamic_Thermal_Rating),
[PDF (Universidad de
Chile)](https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf).

> Addresses dynamic thermal rating of transmission lines — the real-time
> capacity assessment problem complementary to the long-term transmission
> expansion planning solved by GTOpt.

### Transmission Expansion Planning

<a id="ref1"></a>
**[1]** Romero, R., Monticelli, A., Garcia, A.V., and Haffner, S. (2002).
"Test systems and mathematical models for transmission network expansion
planning." *IEE Proceedings – Generation, Transmission and Distribution*,
149(1), pp. 27–36.
DOI: [10.1049/ip-gtd:20020026](https://doi.org/10.1049/ip-gtd:20020026).

> Defines the standard test systems and LP/MIP mathematical models for
> transmission expansion planning. The DC power flow and transport models
> in GTOpt follow the formulations described in this seminal reference.

<a id="ref2"></a>
**[2]** Romero, R. and Monticelli, A. (1994). "A hierarchical
decomposition approach for transmission network expansion planning."
*IEEE Transactions on Power Systems*, 9(1), pp. 373–380.
DOI: [10.1109/59.317588](https://doi.org/10.1109/59.317588).

> Introduces hierarchical decomposition for large-scale transmission
> expansion, which influenced the multi-stage structure used in GTOpt.

<a id="ref3"></a>
**[3]** Lumbreras, S. and Ramos, A. (2016). "The new challenges to
transmission expansion planning. Survey of recent practice and literature
review." *Electric Power Systems Research*, 134, pp. 19–29.
DOI: [10.1016/j.epsr.2015.10.013](https://doi.org/10.1016/j.epsr.2015.10.013).

> Comprehensive survey of TEP methods covering DC models, transport models,
> multi-stage formulations, and uncertainty representation — all approaches
> implemented in GTOpt.

<a id="ref10"></a>
**[10]** Gonzalez-Romero, I.C., Wogrin, S., and Román, T. (2020).
"Review on generation and transmission expansion co-planning models under
a market environment." *IET Generation, Transmission & Distribution*,
14(6), pp. 931–944.
DOI: [10.1049/iet-gtd.2019.0123](https://doi.org/10.1049/iet-gtd.2019.0123).

> Reviews co-optimization of generation and transmission expansion (the
> core GTEP problem solved by GTOpt), covering LP/MIP formulations,
> decomposition methods, and renewable integration.

### DC Optimal Power Flow

<a id="ref11"></a>
**[11]** Stott, B., Jardim, J., and Alsaç, O. (2009). "DC Power Flow
Revisited." *IEEE Transactions on Power Systems*, 24(3), pp. 1290–1300.
DOI: [10.1109/TPWRS.2009.2021235](https://doi.org/10.1109/TPWRS.2009.2021235).

> The definitive reference on the DC power flow approximation. The
> linearized power flow equations in GTOpt's Kirchhoff constraints
> (§5.6) follow the standard DC model: $f_l = B_l (\theta_a - \theta_b)$
> where $B_l = V^2 / X_l$ is the line susceptance ($V$ and $X_l$ must
> share a consistent unit system — see §5.6).

<a id="ref12"></a>
**[12]** Anderson, P.M. and Fouad, A.A. (2002). *Power Systems Control
and Stability*. 2nd ed. Wiley-IEEE Press. ISBN: 978-0471238621.

> Standard textbook for power system dynamics and stability. The IEEE
> 9-bus and 14-bus test systems used in GTOpt's benchmark cases originate
> from this reference.

### Similar Tools and Comparable Formulations

<a id="ref13"></a>
**[13]** Brown, T., Hörsch, J., and Schlachtberger, D. (2018). "PyPSA:
Python for Power System Analysis." *Journal of Open Research Software*,
6(1), p. 4.
DOI: [10.5334/jors.188](https://doi.org/10.5334/jors.188).

> PyPSA implements a similar LOPF (linear optimal power flow) formulation
> with multi-period investment planning. GTOpt's bus balance, Kirchhoff,
> and storage constraints follow the same mathematical structure, with
> GTOpt additionally supporting hydro cascades and multi-scenario
> stochastic optimization.

<a id="ref14"></a>
**[14]** Jenkins, J.D. and Sepulveda, N.A. (2017). "Enhanced Decision
Support for a Changing Electricity Landscape: The GenX Configurable
Electricity Resource Capacity Expansion Model." MIT Energy Initiative
Working Paper.

> GenX implements a similar capacity expansion formulation in Julia/JuMP.
> Both GenX and GTOpt use LP/MIP with modular capacity additions,
> storage SoC tracking, and representative time periods, though GTOpt
> uses a C++ sparse-matrix assembly for performance.

<a id="ref17"></a>
**[17]** Thurner, L., Scheidler, A., Schäfer, F., Menke, J-H., Dollichon,
J., Meier, F., Meinecke, S., and Braun, M. (2018). "pandapower — An
Open-Source Python Tool for Convenient Modeling, Analysis, and
Optimization of Electric Power Systems." *IEEE Transactions on Power
Systems*, 33(6), pp. 6510–6521.
DOI: [10.1109/TPWRS.2018.2829021](https://doi.org/10.1109/TPWRS.2018.2829021).

> pandapower is a Python-based power system analysis tool supporting AC and
> DC power flow, optimal power flow (OPF), and short-circuit analysis. It
> is used by GTOpt's `pp2gtopt` converter to import pandapower network
> models into GTOpt format, and by the `gtopt_compare` validation script to
> provide a reference DC OPF solution for benchmarking GTOpt results on
> standard IEEE test cases (4-bus, 9-bus, 14-bus, 30-bus, 57-bus).

### Storage Self-Discharge Data

<a id="ref18"></a>
**[18]** Battery University (Cadex Electronics). "BU-802b: What does
Elevated Self-discharge Do?"
URL: [https://batteryuniversity.com/article/elevating-self-discharge](https://batteryuniversity.com/article/elevating-self-discharge).

> Li-ion: ≈ 5 % self-discharge in the first 24 h, then 1–2 %/month, plus
> ≈ 3 %/month consumed by the protection circuit; self-discharge roughly
> doubles per +10 °C.  Basis (with [[19]](#ref19)) of gtopt's default
> battery `annual_standing_loss` of 4 %/month.

<a id="ref19"></a>
**[19]** Wikipedia. "Self-discharge" (table of self-discharge rates by
chemistry).
URL: [https://en.wikipedia.org/wiki/Self-discharge](https://en.wikipedia.org/wiki/Self-discharge).

> Lists lithium-ion cells at 2–3 %/month at room temperature.

### Ancillary Services (CEN Chile)

<a id="ref20"></a>
**[20]** Coordinador Eléctrico Nacional (2026). "Minuta GM-DPRO Nº05-2026:
Modelación y Asignación de SSCC de Control de Frecuencia (ISSCC 2026)."
URL: [https://www.coordinador.cl/wp-content/uploads/2026/06/Minuta_GM-DPRO_No05-2026_Modelacion_y_Asignacion_de_SSCC_CF-ISSCC2026.pdf](https://www.coordinador.cl/wp-content/uploads/2026/06/Minuta_GM-DPRO_No05-2026_Modelacion_y_Asignacion_de_SSCC_CF-ISSCC2026.pdf).

> Anexo A: provision model (A.1)–(A.24); Anexo B: inertia (B.1) and the
> CPF requirement planes (B.2)–(B.4).

<a id="ref21"></a>
**[21]** Coordinador Eléctrico Nacional (2021). "Minuta GM Nº35-2021:
Modelación y Asignación de Servicios Complementarios de Control de
Frecuencia (ISSCC 2022)."

> First minuta to regress the requirement on total generation
> ("variable directa del problema de optimización").

<a id="ref22"></a>
**[22]** Coordinador Eléctrico Nacional (2020). "Minuta GM Nº34-2020:
Modelación y Asignación de Servicios Complementarios de Control de
Frecuencia (ISSCC 2021)."

> Regresses the requirement on demand, "un dato de entrada al problema
> de optimización": the exogenous criterion gtopt uses by default.

### Solvers

<a id="ref15"></a>
**[15]** Forrest, J.J. and Lougee-Heimer, R. (2005). "CBC User Guide."
In *Emerging Theory, Methods, and Applications*, INFORMS, pp. 257–277.
DOI: [10.1287/educ.1053.0020](https://doi.org/10.1287/educ.1053.0020).

> Documents the COIN-OR CBC (Coin-or Branch and Cut) solver, one of
> GTOpt's open-source MIP backends; CLP (COIN-OR Linear Programming) is
> its LP-only counterpart. When no solver is requested GTOpt picks the
> first available backend in the order CPLEX > HiGHS > MindOpt > CBC >
> CLP.

### Natural-Gas Networks (§5.19)

<a id="ref23"></a>
**[23]** De Wolf, D. and Smeers, Y. (2000). "The Gas Transmission
Problem Solved by an Extension of the Simplex Algorithm." *Management
Science*, 46(11), pp. 1454–1465.
DOI: [10.1287/mnsc.46.11.1454.12087](https://doi.org/10.1287/mnsc.46.11.1454.12087).

> Weymouth equation in squared pressure; also the origin of the Belgian
> network used by `cases/gas_belgian_a1_radial` and
> `cases/gas_hu_ryan_24b20n`.

<a id="ref24"></a>
**[24]** Menon, E.S. (2005). *Gas Pipeline Hydraulics*. CRC Press, Boca
Raton. DOI: [10.1201/9781420038224](https://doi.org/10.1201/9781420038224).

> SI Weymouth equation (eq. 2.62) behind the default `weymouth_k`, and
> the elevation correction (§2.9).

<a id="ref25"></a>
**[25]** Saldarriaga Cortés, C.A. (2018). *Planeamiento Integrado de
Sistemas Energéticos: Un Enfoque Estocástico Multietapa*. Doctoral
thesis (Doctorado en Ingeniería), Universidad Tecnológica de Pereira,
Colombia. Advisor: H. Salazar Isaza. Submitted 29 Nov. 2018 (title
page); repository record dated 2019.
URL: [https://hdl.handle.net/11059/10887](https://hdl.handle.net/11059/10887).

> Compressor fuel $LC = \gamma(\beta^\alpha - 1)\Psi$ and its Taylor
> plane (eqs. 2.70–2.72), the "3–5 % of the compressed gas" check, and
> the pipe chain of Fig. 2.2(b) (`cases/gas_saldarriaga_fig2_2`).

<a id="ref26"></a>
**[26]** Schmidt, M., Aßmann, D., Burlacu, R., Humpola, J., Joormann, I.,
Kanelakis, N., Koch, T., Oucherif, D., Pfetsch, M.E., Schewe, L.,
Schwarz, R., and Sirvent, M. (2017). "GasLib—A Library of Gas Network
Instances." *Data*, 2(4), 40.
DOI: [10.3390/data2040040](https://doi.org/10.3390/data2040040).
Data: [https://gaslib.zib.de](https://gaslib.zib.de), CC BY 3.0.

<a id="ref27"></a>
**[27]** Pfetsch, M.E., Fügenschuh, A., Geißler, B., et al. (2015).
"Validation of Nominations in Gas Network Optimization: Models, Methods,
and Solutions." *Optimization Methods and Software*, 30(1), pp. 15–53.
DOI: [10.1080/10556788.2014.888426](https://doi.org/10.1080/10556788.2014.888426).
Earlier version: ZIB-Report 12-41 (2012), the citation the GasLib file
headers require.

<a id="ref28"></a>
**[28]** Bent, R., Sundar, K., et al. *GasModels.jl* — steady-state gas
network optimization in Julia/JuMP. Los Alamos National Laboratory,
MICOT LA-CC-13-108, BSD-3-Clause.
URL: [https://github.com/lanl-ansi/GasModels.jl](https://github.com/lanl-ansi/GasModels.jl).
Companion: *GasPowerModels.jl*,
[https://github.com/lanl-ansi/GasPowerModels.jl](https://github.com/lanl-ansi/GasPowerModels.jl).

> Source of `case-6` (`cases/gas_case6`) and of the Belgian `A1` data.

<a id="ref29"></a>
**[29]** Hu, D. (2019). "Datasets for reliability unit commitment to
manage natural gas cost in power system operations." Mendeley Data, V1,
CC BY 4.0. DOI: [10.17632/ynb88g67xy.1](https://doi.org/10.17632/ynb88g67xy.1).
Related paper: Hu, D. and Ryan, S.M. (2019). "Stochastic vs.
deterministic scheduling of a combined natural gas and power system with
uncertain wind energy." *Int. J. Electrical Power & Energy Systems*,
108, pp. 303–313.
DOI: [10.1016/j.ijepes.2018.12.047](https://doi.org/10.1016/j.ijepes.2018.12.047).

<a id="ref30"></a>
**[30]** Ratha, A., Schwele, A., Kazempour, J., Pinson, P., Shariat
Torbaghan, S., and Virag, A. (2020). "Affine Policies for Flexibility
Provision by Natural Gas Networks to Power Systems." *Electric Power
Systems Research*, 189, 106565.
DOI: [10.1016/j.epsr.2020.106565](https://doi.org/10.1016/j.epsr.2020.106565).
Data: Zenodo, CC BY 4.0,
DOI: [10.5281/zenodo.3755120](https://doi.org/10.5281/zenodo.3755120).

<a id="ref31"></a>
**[31]** Ordoudis, C., Pinson, P., and Morales, J.M. (2019). "An
Integrated Market for Electricity and Natural Gas Systems with
Stochastic Power Producers." *European Journal of Operational
Research*, 272(2), pp. 642–654.
DOI: [10.1016/j.ejor.2018.06.036](https://doi.org/10.1016/j.ejor.2018.06.036).
Data: DOI [10.5281/zenodo.1174979](https://doi.org/10.5281/zenodo.1174979).

> Origin of the IEEE-24 / 12-node gas system that [[30]](#ref30) extends.

<a id="ref32"></a>
**[32]** Tomasgard, A., Rømo, F., Fodstad, M., and Midthun, K. (2007).
"Optimization Models for the Natural Gas Value Chain." In *Geometric
Modelling, Numerical Simulation, and Optimization*, Springer,
pp. 521–558.
DOI: [10.1007/978-3-540-68783-2_16](https://doi.org/10.1007/978-3-540-68783-2_16).

> Tangent-plane outer approximation of the Weymouth equation in an LP.

<a id="ref33"></a>
**[33]** Akinbode, O.W., and Hedman, K.W. (2013). "Fictitious losses in
the DCOPF with a piecewise linear approximation of losses." *IEEE PES
General Meeting*.
DOI: [10.1109/PESMG.2013.6672235](https://doi.org/10.1109/PESMG.2013.6672235).

> Out-of-order segment fill creates fictitious losses at non-positive
> LMPs.

<a id="ref34"></a>
**[34]** Coffrin, C., Van Hentenryck, P., and Bent, R. (2012).
"Approximating line losses and apparent power in AC power flow
linearizations." *IEEE PES General Meeting*.
DOI: [10.1109/PESGM.2012.6345342](https://doi.org/10.1109/PESGM.2012.6345342).

> Directed end flows $p_{ij}, p_{ji}$ with the thermal limit at both
> ends (PowerModels lossy DC models).

<a id="ref35"></a>
**[35]** Neumann, F., Hagenmeyer, V., and Brown, T. (2022). "Assessments
of linear power flow and transmission loss approximations in coordinated
capacity expansion problems." *Applied Energy*, 314, 118859.
DOI: [10.1016/j.apenergy.2022.118859](https://doi.org/10.1016/j.apenergy.2022.118859).

> Loss cap $`\psi \le r\,\overline{P}^2`$ and the capacity constraint
> $|p| + \psi \le \overline{P}$ (implemented in PyPSA).

<a id="ref36"></a>
**[36]** Tosatto, A., Weckesser, T., and Chatzivasileiadis, S. (2020).
"Market Integration of HVDC Lines: Internalizing HVDC Losses in Market
Clearing." *IEEE Trans. Power Systems*, 35(1), pp. 451–461.
DOI: [10.1109/TPWRS.2019.2932184](https://doi.org/10.1109/TPWRS.2019.2932184).

> With split loss allocation the relaxation is exact iff
> $\tfrac12(\pi_a + \pi_b) > 0$.

<a id="ref37"></a>
**[37]** Litvinov, E., Zheng, T., Rosenwald, G., and Shamsollahi, P.
(2004). "Marginal Loss Modeling in LMP Calculation." *IEEE Trans. Power
Systems*, 19(2), pp. 880–888.
DOI: [10.1109/TPWRS.2004.825894](https://doi.org/10.1109/TPWRS.2004.825894).

> LMP split into energy, loss and congestion components with marginal
> loss factors; the `sending` limit dual is the congestion component
> with the loss referred to the sending-end injection.

<a id="ref38"></a>
**[38]** IEEE Std 738-2012. "IEEE Standard for Calculating the
Current-Temperature Relationship of Bare Overhead Conductors." IEEE,
2013.
DOI: [10.1109/IEEESTD.2013.6692858](https://doi.org/10.1109/IEEESTD.2013.6692858).

> Steady-state heat balance used for the per-span DLR rating.

<a id="ref39"></a>
**[39]** U.S. Department of Energy (2014). Smart Grid Demonstration
Program topical report on dynamic line rating.
[PDF](https://www.energy.gov/sites/prod/files/2016/10/f34/SGDP_Transmission_DLR_Topical_Report_04-25-14.pdf).

<a id="ref40"></a>
**[40]** Oncor Electric Delivery (2014). Dynamic line rating case study,
SmartGrid.gov.
[PDF](https://smartgrid.gov:443/files/documents/Oncor_DLR_Case_Study_05-20-14_FINAL.pdf).

<a id="ref41"></a>
**[41]** Idaho National Laboratory. Dynamic line rating field
experience (incl. AltaLink), OSTI 1375207.
[Link](https://www.osti.gov/servlets/purl/1375207).

<a id="ref42"></a>
**[42]** EPRI (2025). Product 3002031444 on dynamic line rating.
[Link](https://restservice.epri.com/publicdownload/000000003002031444/0/Product).

<a id="ref43"></a>
**[43]** ISO New England. Planning Procedure 7, capacity rating
procedures.
[PDF](https://www.iso-ne.com/static-assets/documents/rules_proceds/isone_plan/pp07/capacity_rating_procedures.pdf).

<a id="ref44"></a>
**[44]** NYISO (2020). NYTO 2019 tie-line ratings report.
[PDF](https://www.nyiso.com/documents/20142/1402024/NYTO-2019-Tie-Line-Report-V01-2020-January-9.pdf/7029e9e9-3f76-5355-5646-8b1f18699750).

> [39]–[44]: DLR gains reported in practice and emergency-rating
> practice, behind the `max_overload_ratio` default.

<a id="ref45"></a>
**[45]** Zimmerman, R.D., Murillo-Sánchez, C.E., and Thomas, R.J.
(2011). "MATPOWER: Steady-State Operations, Planning, and Analysis Tools
for Power Systems Research and Education." *IEEE Trans. Power Systems*,
26(1), pp. 12–19.
DOI: [10.1109/TPWRS.2010.2051168](https://doi.org/10.1109/TPWRS.2010.2051168).

<a id="ref46"></a>
**[46]** Coffrin, C., Bent, R., Sundar, K., Ng, Y., and Lubin, M. (2018).
"PowerModels.jl: An Open-Source Framework for Exploring Power Flow
Formulations." *Power Systems Computation Conference (PSCC)*.
DOI: [10.23919/PSCC.2018.8442948](https://doi.org/10.23919/PSCC.2018.8442948).

<a id="ref47"></a>
**[47]** Coffrin, C., and Van Hentenryck, P. (2014). "A Linear-Programming
Approximation of AC Power Flows." *INFORMS Journal on Computing*, 26(4),
pp. 718–734.
DOI: [10.1287/ijoc.2014.0594](https://doi.org/10.1287/ijoc.2014.0594).

> [45]–[47]: thermal-limit conventions that `line_thermal_limit_mode`
> reproduces (see "Validation against literature").

### Classification and Surveys

<a id="ref16"></a>
**[16]** Mahdavi, M., Antunez, C.S., Ajalli, M., and Romero, R. (2019).
"Transmission Expansion Planning: Literature Review and Classification."
*IEEE Systems Journal*, 13(3), pp. 3129–3140.
DOI: [10.1109/JSYST.2018.2871793](https://doi.org/10.1109/JSYST.2018.2871793).

> Systematic classification of TEP literature covering mathematical
> models, solution methods, and uncertainty handling — provides context
> for GTOpt's position in the GTEP landscape.

---

## Appendix A: LP Problem Size Estimates

For a system with $N$ buses, $G$ generators, $D$ demands, $L$ lines,
$E$ batteries, $P$ pumps, $\tau$ LNG terminals, $Z^R$ reserve zones,
$Z^H$ inertia zones, and $B$ blocks per stage over $T$ stages and $S$ scenarios:

| Quantity | Approximate count |
|----------|------------------|
| **Operational variables** | $(G + 2D + 2L + 3E + N) \times S \times T \times B$ |
| **Pump variables** | $P \times S \times T \times B$ (pump flow) |
| **LNG terminal variables** | $3\tau \times S \times T \times B$ (volume, delivery, vent) |
| **Reserve variables** | $Z^R \times S \times T \times B$ (shortage slack) |
| **Inertia variables** | $Z^H \times S \times T \times B$ (shortage slack) |
| **UC variables** | $3 \times \lvert\mathcal{UC}\rvert \times S \times T \times B$ (u/v/w) |
| **Investment variables** | $(G + D + L + E) \times T$ |
| **Bus balance constraints** | $N \times S \times T \times B$ |
| **Kirchhoff constraints** | $L \times S \times T \times B$ (if DC OPF) |
| **SoC balance constraints** | $E \times S \times T \times B$ |
| **LNG tank balance constraints** | $\tau \times S \times T \times B$ |
| **Pump coupling constraints** | $P \times S \times T \times B$ |
| **Reserve constraints** | $Z^R \times S \times T \times B$ |
| **Inertia constraints** | $Z^H \times S \times T \times B$ |
| **Capacity constraints** | $(G + D + L + E) \times T$ |

The LP is assembled in compressed sparse column (CSC) format via the
`FlatLinearProblem` class and passed to the configured solver backend
(CBC/CLP/HiGHS/CPLEX) [[15]](#ref15).

---

*Document generated from the GTOpt source code. See the C++ implementation
in `source/*_lp.cpp` and `include/gtopt/*_lp.hpp` for the canonical
formulation. For the theoretical background, see [Section 9: References](#9-references).*
