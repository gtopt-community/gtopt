# Unit Commitment in GTOpt

> **Status**: Implemented (Phases 1–3).  
> **Since**: v0.9 (April 2025)  
> **See also**: [Input Data Reference](input-data.md) · [Planning Guide](planning-guide.md) ·
> [Mathematical Formulation](formulation/mathematical-formulation.md) · [Usage](usage.md)

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Background and Motivation](#2-background-and-motivation)
3. [Quick Start](#3-quick-start)
4. [The Three-Bin Formulation](#4-the-three-bin-formulation)
   - 4.1 [Decision Variables](#41-decision-variables)
   - 4.2 [Core Constraints (C1–C3)](#42-core-constraints-c1c3)
   - 4.3 [Ramp Constraints (C4–C5)](#43-ramp-constraints-c4c5)
   - 4.4 [Minimum Up/Down Time (C6–C7)](#44-minimum-updown-time-c6c7)
   - 4.5 [Startup Cost Tiers (C8–C10)](#45-startup-cost-tiers-c8c10)
   - 4.6 [Power-Based Formulation](#46-power-based-formulation-uc_formulation--power_based)
5. [Piecewise Heat Rate Curves](#5-piecewise-heat-rate-curves)
6. [Emission Cost and Cap](#6-emission-cost-and-cap)
7. [Reserve Integration](#7-reserve-integration)
8. [Commitment Periods](#8-commitment-periods)
9. [Relaxation Control](#9-relaxation-control)
10. [JSON Input Reference](#10-json-input-reference)
    - 10.1 [Commitment Object](#101-commitment-object)
    - 10.2 [Stage — Chronological Flag](#102-stage--chronological-flag)
    - 10.3 [Generator — Emissions](#103-generator--emissions)
    - 10.4 [Model Options — Relaxation and Objective](#104-model-options--relaxation-and-objective)
11. [Output Files](#11-output-files)
12. [Worked Examples](#12-worked-examples)
    - 12.1 [Example 1: Basic UC with Two Generators](#121-example-1-basic-uc-with-two-generators)
    - 12.2 [Example 2: Piecewise Heat Rate](#122-example-2-piecewise-heat-rate)
    - 12.3 [Example 3: Startup Cost Tiers](#123-example-3-startup-cost-tiers)
    - 12.4 [Example 4: Emission Cost Shifting Dispatch](#124-example-4-emission-cost-shifting-dispatch)
    - 12.5 [Example 5: Emission Cap](#125-example-5-emission-cap)
13. [Integration with Planning Framework](#13-integration-with-planning-framework)
14. [Known Limitations](#14-known-limitations)
15. [Comparison with Other Tools](#15-comparison-with-other-tools)
16. [Academic References](#16-academic-references)
17. [Roadmap](#17-roadmap)

---

## 1. Introduction

**Unit commitment (UC)** is the problem of deciding which generating units to
turn on (commit) and off (decommit) over a planning horizon, subject to
technical constraints such as minimum up/down times, ramp rates, and startup
costs.  It is a fundamental problem in power system operations, sitting between
long-term capacity expansion planning and real-time economic dispatch.

GTOpt implements a **tight-and-compact three-bin (u, v, w) mixed-integer
programming (MIP) formulation** based on the work of Morales-España, Latorre,
and Ramos (2013).  This formulation is widely recognized as producing the
tightest LP relaxation among compact UC formulations, which translates to
faster solution times and smaller integrality gaps in practice.

### Key Features

- **Three-bin model**: status ($u$), startup ($v$), and shutdown ($w$) binary
  variables with tight coupling constraints
- **Ramp constraints**: tight ramp-up/down limits that depend on commitment
  state, including dedicated startup/shutdown ramp limits
- **Minimum up/down time**: aggregated formulation at the period level
- **Hot/warm/cold startup tiers**: time-dependent startup costs based on
  offline duration
- **Piecewise linear heat rate curves**: multi-segment fuel cost modeling
  (defined on the `Generator`, tightened by the commitment status)
- **Emission price and cap**: per-zone carbon (or local-pollutant) price
  and/or per-stage cap through `EmissionZone`, with the endogenous price
  given by the cap dual (see [Emissions](emissions.md))
- **Reserve integration**: spinning reserve headroom conditional on commitment
  status
- **Commitment periods**: optional coarser time resolution for binary variables
  while keeping generation at fine granularity
- **LP relaxation control**: per-element and per-phase relaxation for
  benchmarking and gap analysis
- **Formulation follows the stage**: chronological stages get the full
  intertemporal model; non-chronological stages (load-duration curves,
  representative blocks) get the per-block formulation — one status `u`
  per block with `p ≤ Pmax·u` and `p ≥ pmin·u`, same `Commitment.pmin`,
  same reserve / inertia gating (see §10.2)

---

## 2. Background and Motivation

### Why Unit Commitment Matters

In power systems with significant thermal generation, the decision to commit
or decommit a generator is not instantaneous — it has physical and economic
consequences:

- **Startup costs**: heating a boiler, synchronizing a turbine, and ramping to
  minimum output can cost thousands to hundreds of thousands of dollars
- **Minimum operating levels**: once committed, a thermal unit must operate
  above its technical minimum ($P_\min$), even if demand could be served more
  cheaply by another unit
- **Minimum up/down times**: a coal plant that takes 8 hours to start cannot
  be cycled on and off hourly
- **Ramping limits**: a nuclear plant or large coal unit can only increase or
  decrease output by a limited amount per hour

Without UC, an economic dispatch model implicitly assumes all generators can
be turned on and off instantaneously at zero cost — a poor approximation for
thermal-dominated systems and increasingly important for systems with high
renewable penetration where thermal units must cycle frequently.

### When to Use Unit Commitment in GTOpt

Use UC when your system includes:

- **Thermal generators** (coal, gas, nuclear) with significant startup costs,
  minimum up/down times, or steep ramp limits
- **High renewable penetration** where thermal units must cycle to accommodate
  variable generation
- **Detailed operational studies** (hourly or sub-hourly time resolution) where
  the binary on/off decision materially affects dispatch cost and feasibility
- **Spinning reserve requirements** that depend on which units are online

Do **not** use UC for:

- **Long-term expansion planning** with representative blocks if you do
  not want even the per-block on/off status (drop the Commitment; on
  non-chronological stages it is the per-block formulation, see §10.2)
- **Hydro-only systems** where generation is fully flexible (no startup costs
  or minimum up times)
- **Screening studies** where approximate dispatch is sufficient

---

## 3. Quick Start

### Minimal JSON example

Add a `commitment_array` to your system and mark the stage as `chronological`:

```json
{
  "system": {
    "generator_array": [
      {
        "uid": 1,
        "name": "coal1",
        "bus": 1,
        "pmin": 200,
        "pmax": 600,
        "gcost": 25,
        "capacity": 600
      },
      {
        "uid": 2,
        "name": "gas_peaker",
        "bus": 1,
        "pmin": 0,
        "pmax": 300,
        "gcost": 60,
        "capacity": 300
      }
    ],
    "commitment_array": [
      {
        "uid": 1,
        "name": "coal1_uc",
        "generator": 1,
        "startup_cost": 50000,
        "shutdown_cost": 5000,
        "noload_cost": 500,
        "min_up_time": 8,
        "min_down_time": 6,
        "ramp_up": 100,
        "ramp_down": 100,
        "initial_status": 1,
        "initial_hours": 12
      }
    ]
  },
  "simulation": {
    "stage_array": [
      {
        "uid": 0,
        "first_block": 0,
        "count_block": 24,
        "chronological": true
      }
    ]
  }
}
```

### Running

```bash
gtopt my_case.json
# Or with LP relaxation for faster solves:
gtopt my_case.json --set model_options.continuous_phases=all
```

### What happens

1. GTOpt detects the `commitment_array` and the `chronological: true` stage
2. For each committed generator, it creates binary variables $u_t$, $v_t$,
   $w_t$ at every block (or commitment period)
3. Core constraints (logic, generation bounds, exclusion) are added
4. Optional constraints (ramp, min up/down, startup tiers) are added based
   on which fields are present
5. The LP/MIP is solved; output files include `status_sol`, `startup_sol`,
   and `shutdown_sol` per committed generator

---

## 4. The Three-Bin Formulation

The formulation uses three families of binary decision variables per committed
generator $g$ and time period $t$:

### 4.1 Decision Variables

| Variable | Type | Domain | Meaning |
|----------|------|--------|---------|
| $u_{g,t}$ | Binary | $\{0, 1\}$ | **Status**: 1 if generator $g$ is online at time $t$ |
| $v_{g,t}$ | Binary (see below) | $\{0, 1\}$ | **Startup**: 1 if generator $g$ starts up at time $t$ |
| $w_{g,t}$ | Binary (see below) | $\{0, 1\}$ | **Shutdown**: 1 if generator $g$ shuts down at time $t$ |

$u$ is always declared integer on a MIP build.  The integrality of $v$
and $w$ is selected by `transition_integrality` (per unit, else
`model_options`):

| Value | $v$, $w$ |
|---|---|
| `auto` (default) | binary (the three-binary model) |
| `continuous` | always continuous in $[0, 1]$ (smaller MIP, not exact with start / stop ramps) |
| `binary` | always binary |

**Why $v$, $w$ are binary.**  C1 ($u_t - u_{t-1} = v_t - w_t$) and C3
($v_t + w_t \le 1$) alone admit $v = w = \tfrac12$ on a unit that stays
on (or off), which the start/stop ramp terms ($SU\cdot v$, $SD\cdot w$)
and user rows on startup / shutdown reward, so `auto` declares them
binary (UnitCommitment.jl's Gar1962 model: binary `switch_on` /
`switch_off`, `switch_on + switch_off ≤ 1`).  From 2026-11-25 to
2026-11-26 gtopt also had the rows $v_t \le u_t$, $w_t \le 1 - u_t$
(`transition_status_rows`), which make continuous $v$, $w$ exact for an
integral $u$ (Knueven, Ostrowski & Watson [K20]; Rajan & Takriti
[RT05]); they were removed — see C3.

The hot / warm / cold start selectors (§4.6) follow
`startup_tier_integrality` (per unit, else `model_options`):
`continuous` (default) — with $v$, $w$ integral the tier rows
($\sum y = v$ plus the cumulative offline-window rows) form an interval
matrix, hence totally unimodular, so the optimal vertices are integral —
or `integer`.

[K20] B. Knueven, J. Ostrowski, J.-P. Watson, "On Mixed-Integer
Programming Formulations for the Unit Commitment Problem", *INFORMS J.
Computing* 32(4):857-876, 2020, doi:10.1287/ijoc.2019.0944.
[RT05] D. Rajan, S. Takriti, "Minimum up/down polytopes of the unit
commitment problem with start-up costs", IBM Research Report RC23628,
2005.

When `relax = true`, the phase is in `continuous_phases`, or `--no-mip`
is given, all three become continuous in $[0, 1]$.

### 4.2 Core Constraints (C1–C3)

These three constraints form the backbone of the UC formulation and are
**always** present for every committed generator on every chronological stage.

#### C1: Logical Transition

$$u_{g,t} - u_{g,t-1} = v_{g,t} - w_{g,t}$$

For the first period ($t = 0$):

$$u_{g,0} - u_{g,\text{init}} = v_{g,0} - w_{g,0}$$

where $u_{g,\text{init}}$ is `initial_status` (1.0 = online, 0.0 = offline).

This constraint links the three variables: if a unit transitions from off to
on ($u$ goes from 0 to 1), then $v = 1$ (startup event); if it transitions
from on to off, then $w = 1$ (shutdown event).

#### C2: Generation Bounds

$$P_{\min} \cdot u_{g,t} \leq p_{g,t} \leq P_{\max} \cdot u_{g,t}$$

When the unit is offline ($u = 0$), generation is forced to zero. When online
($u = 1$), generation must be within $[P_\min, P_\max]$.

*Implementation note*: The lower bound of the generation variable is set to
zero (`gcol.lowb = 0.0`), and the bounds are enforced via separate inequality
rows. This allows the LP relaxation to produce fractional commitment states.

#### C3: Exclusion

$$v_{g,t} + w_{g,t} \leq 1$$

A unit cannot simultaneously start up and shut down in the same period.
With binary $v$, $w$ (`transition_integrality = auto`), C1 and C3 make
$v$, $w$ the exact 0/1 transition.

The rows $v_{g,t} \le u_{g,t}$, $w_{g,t} \le 1 - u_{g,t}$
(`transition_status_rows`, added 2026-11-25) were **removed on
2026-11-26**.  They are redundant in a MIP with binary $v$, $w$; on the
CEN PCP case (109 782 commitment periods, most with a fixed status) they
made the relaxed LP degenerate (C3 is their exact sum, and they were
emitted for fixed-status periods too), CPLEX's crossover ended on a
near-singular basis (kappa 1e16 and more), and CPLEX declared feasible
variants infeasible.  The key `transition_status_rows` is still read
(bool or name) so old inputs load, and ignored with a warning.

#### C3b (removed): start / stop tied to the status

The ramp-only rows $v_{g,t} \le u_{g,t}$, $w_{g,t} \le 1 - u_{g,t}$
(2026-11-21, row names `startup_status` / `shutdown_status`) went with
the transition status rows above: with binary $v$, $w$ they are
redundant in the MIP, and PCP's ramping units carried most of them.
In a relaxed run (`relax`, `--no-mip`, continuous phases) a fractional
$v = w$ with $u$ unchanged can again buy start / stop ramp room
($SU\,v$, $SD\,w$) for a fraction of the start cost — a weaker but
valid relaxation.

### 4.3 Ramp Constraints (C4–C5)

Ramp constraints limit how fast a generator can change output between
consecutive blocks.  The **tight formulation** uses state-dependent ramp
limits:

#### C4: Ramp Up

$$p_{g,t} - p_{g,t-1} \leq RU_g \cdot u_{g,t-1} + SU_g \cdot v_{g,t}$$

where:
- $RU_g$ = `ramp_up` × block duration [MW] — normal ramp-up rate
- $SU_g$ = `startup_ramp` [MW] — maximum output in the startup block

When a unit starts up ($v_t = 1$, $u_{t-1} = 0$), the ramp limit is $SU_g$
(typically ≤ $P_\max$).  When already online ($u_{t-1} = 1$, $v_t = 0$), the
ramp limit is $RU_g$.

#### C5: Ramp Down

$$p_{g,t-1} - p_{g,t} \leq RD_g \cdot u_{g,t} + SD_g \cdot w_{g,t}$$

where:
- $RD_g$ = `ramp_down` × block duration [MW] — normal ramp-down rate
- $SD_g$ = `shutdown_ramp` [MW] — maximum output in the shutdown block

**When to use**: Set `ramp_up` and/or `ramp_down` for generators with
meaningful ramping limitations (large coal, nuclear).  Set `startup_ramp`
and `shutdown_ramp` when the startup/shutdown trajectory differs from
normal ramping.  If omitted, the startup/shutdown ramp defaults to $P_\max$.

### 4.4 Minimum Up/Down Time (C6–C7)

These constraints prevent short cycling of thermal units.

#### C6: Minimum Up Time

$$\sum_{\tau=t}^{\min(t + UT_g - 1,\, T)} u_{g,\tau} \geq UT_g \cdot v_{g,t}$$

If a unit starts up at time $t$ ($v_t = 1$), it must remain online for at
least $UT_g$ consecutive periods.  The implementation counts periods (not
blocks) and accumulates durations to find how many periods cover
`min_up_time` hours.

#### C7: Minimum Down Time

$$\sum_{\tau=t}^{\min(t + DT_g - 1,\, T)} (1 - u_{g,\tau}) \geq DT_g \cdot w_{g,t}$$

Equivalently:

$$\sum_{\tau=t}^{t + DT_g - 1} u_{g,\tau} + DT_g \cdot w_{g,t} \leq \text{span}$$

where $\text{span}$ is the number of periods in the window.

If a unit shuts down at time $t$ ($w_t = 1$), it must remain offline for at
least $DT_g$ periods.

**Trivial skip**: When a single period's duration already covers
`min_up_time` (or `min_down_time`), the constraint is trivially satisfied
(1 ≥ 1·$v$) and is not added to the LP.

**Non-uniform block durations**: The implementation correctly handles
variable-length blocks by accumulating durations rather than counting
fixed-length blocks.

### 4.5 Startup Cost Tiers (C8–C10)

Many thermal plants have temperature-dependent startup costs: a unit that
was recently offline (still warm) is cheaper to restart than one that has
been cold for days.

When **all five** tier fields are provided (`hot_start_cost`, `warm_start_cost`,
`cold_start_cost`, `hot_start_time`, `cold_start_time`), the single
`startup_cost` on $v$ is replaced by three tier variables:

| Variable | Cost | Condition |
|----------|------|-----------|
| $y^{\text{hot}}_{g,t}$ | `hot_start_cost` | Offline < `hot_start_time` hours |
| $y^{\text{warm}}_{g,t}$ | `warm_start_cost` | `hot_start_time` ≤ offline < `cold_start_time` |
| $y^{\text{cold}}_{g,t}$ | `cold_start_cost` | Offline ≥ `cold_start_time` hours |

#### C8: Startup Type Selection

$$v_{g,t} = y^{\text{hot}}_{g,t} + y^{\text{warm}}_{g,t} + y^{\text{cold}}_{g,t}$$

Each startup is exactly one tier.

#### C9: Hot Start Window

$$y^{\text{hot}}_{g,t} \leq \sum_{q :\, D_{q,t} < T^{\text{hot}}} w_{g,q}$$

where $D_{q,t} = \sum_{k=q}^{t-1} \Delta_k$ is the offline time of a
start at $t$ after a shutdown in period $q$ ($w_{g,q} = 1$: off from the
start of $q$).  With hourly blocks the window is
$q \in [\,t - T^{\text{hot}} + 1,\, t - 1\,]$.  A hot start is only
possible after a shutdown less than `hot_start_time` hours earlier.

#### C10: Warm Start Window

$$y^{\text{warm}}_{g,t} \leq \sum_{q :\, T^{\text{hot}} \leq D_{q,t} < T^{\text{cold}}} w_{g,q}$$

(hourly: $q \in [\,t - T^{\text{cold}} + 1,\, t - T^{\text{hot}}\,]$).
A warm start requires a shutdown in the warm window (between hot and cold
thresholds).  A shutdown exactly `hot_start_time` (`cold_start_time`)
hours before the start is warm (cold), the same thresholds as the
initial-condition rule below.

Cold start is the residual: when neither hot nor warm is feasible, the
optimizer is forced to use $y^{\text{cold}}$ (the most expensive tier).

**Validation**: all five tier fields are required and `cold_start_time`
must be ≥ `hot_start_time`.  Otherwise a warning is logged, the tiers are
skipped, and $v$ is priced at the flat `startup_cost` — or, when that is
unset, at the largest given tier cost, so a start is never free.

**Initial conditions**: When `initial_status = 0` (offline at $t = 0$),
a start in period $p$ that has stayed off since before the horizon has been
offline $t_p + h_0$ hours ($t_p$ = in-horizon time before $p$, $h_0$ =
`initial_hours`, default $10^6$).  The hot (warm) row's right-hand side is
relaxed to 1 when $t_p + h_0 <$ `hot_start_time` (resp. in
[`hot_start_time`, `cold_start_time`)), so a first-block start after a
short initial outage is hot, not cold.

### 4.6 Power-Based Formulation (`uc_formulation = "power_based"`)

The formulation above is **energy-block**: $p_{g,t}$ is the average power of
block $t$, and the ramp rows bound the change between two block averages.
Such a schedule can ask for energy that no feasible power trajectory
delivers.  Example: a 100–300 MW unit with a 200 MW/h ramp at 100 MW in
hour 1 is scheduled at 300 MW in hour 2, although it reaches 300 MW only at
the end of hour 2 ([5], Fig. 1).  It also ignores the energy produced while
the unit starts up and shuts down.  The **power-based** formulation of
Morales-España, Gentile and Ramos [5] schedules the instantaneous power at
the block boundaries.  Each block energy is derived from these powers, and
the ramps, start-up / shut-down trajectories and reserves are written on
them.  `model_options.uc_formulation = "power_based"` (or the per-unit
`Commitment.uc_formulation`) selects it.  The default, `"energy_block"`,
leaves the LP unchanged.

**Scope.**  The formulation applies to chronological block stages
(`Stage.chronological = true`, `sampling = "block"`).  Non-chronological
stages keep the block formulation.  Point-sampled stages already sample
instantaneous power at their nodes and keep their own formulation (a
warning is issued).  Expandable generators (a capacity column) also fall
back to the energy-block rows, with a warning.

**Time grid.**  The blocks $b = 1..B$ of a stage have real durations
$\Delta_b$ and boundaries $\tau_0 < \tau_1 < \dots < \tau_B$ ($\tau_b$ = end
of block $b$).  The commitment variables $u_k, v_k, w_k$ live on the
**commitment grid** of §8: one period per block by default, or periods $k$
that are unions of whole blocks (`commitment_period`).  Block $b$ belongs
to period $k(b)$.  The power grid may be finer than the commitment grid
(sub-hourly blocks, refined points around sunrise / sunset): the extra
boundaries add continuous columns only, never binaries.  Each boundary
inherits the state of its period.  Start-up and shut-down events happen at
period boundaries only.  The commitment grid is uniform and at least
1 h long: without `commitment_period`, a stage with blocks shorter than
1 h gets a 1 h commitment grid under `power_based`, unless its blocks carry
`Block.commitment_cell` labels (refined blocks then share the status of
the base block they split) or an explicit `commitment_grid` (`"block"`
keeps one status per block).  `Commitment.ramp_allowance` does not apply:
the start-up / shut-down capabilities enter the power-based rows only as
a power jump at the boundary where the period starts or ends ($E_b$,
$F_b$ below), which is the `"boundary"` semantics.  Repeating the
allowance on every block of a period (the energy-block option
`"period"`) would let the boundary power jump by SU again inside the period.
A boundary that ends period $k$ carries
$\underline P\,(u_k + v_{k+1}) = \underline P\,(u_{k+1} + w_{k+1})$: the unit
is at its minimum there if it is up on either side.  Let $E_b = 1$ when
$\tau_b$ ends a period (the last block of period $k(b)$) and $F_b = 1$ when block $b$ starts one, and
let $k^+(b) = k(b) + 1$ be the next period (wrapped on cyclic stages).

**Variables** (per unit and boundary $b = 0..B$):

- $p_b \ge 0$: power above the minimum output at $\tau_b$ [MW];
- $\hat p_b$: total power at $\tau_b$, including the start-up and
  shut-down trajectories [MW] (output `power`);
- the existing generation column $g_b$ of block $b$ becomes the
  **average power** of the block, energy $= g_b\,\Delta_b$.

**PB1 — total power at a boundary** (OR Spectrum [5], eqs. 12/14 and 29/30,
with the events moved to the commitment grid):

$$
\hat p_b = \underline P_b\,\bigl(u_{k(b)} + E_b\,v_{k^+(b)}\bigr) + p_b
 {}+ \sum_{s} f^{SU}\!\bigl(T_s - \tau_b\bigr)\,v_s
 {}+ \sum_{d} f^{SD}\!\bigl(\tau_b - T_d\bigr)\,w_d .
$$

Here $T_k$ is the start instant of period $k$.  $f^{SU}(\delta)$, for
$\delta > 0$ hours before the start, is the piecewise-linear start-up
trajectory through $(0, \underline P)$ and
$(j\,h, P^{SU}_{S^U+1-j})$, $j = 1..S^U$, and $0$ beyond $S^U h$.
$f^{SD}(\delta)$, for $\delta > 0$ hours after the shut-down, passes through
$(0, \underline P)$ and $(j\,h, P^{SD}_{j+1})$, $j = 1..S^D$, and is $0$
beyond $S^D h$.  The trajectories are `startup_trajectory`
$= [P^{SU}_1, \dots, P^{SU}_{S^U}]$ and `shutdown_trajectory`
$= [P^{SD}_2, \dots, P^{SD}_{S^D+1}]$, sampled every $h$ =
`trajectory_step` hours (default 1).  Only the terms with
$0 < \delta \le S h$ are written.  On an hourly grid with hourly periods
this is exactly eq. (12) of [5]: the entry $P^{SU}_i$ multiplies
$v_{b-i+S^U+2}$ and $P^{SD}_i$ multiplies $w_{b-i+2}$.  Without
trajectories (a quick-start unit) PB1 is eq. (14) of [5].  Events outside
the stage (index $< 1$ or $> $ the last period) are zero, as in [5];
cyclic stages wrap.

**PB2 — block energy is the trapezoid of the boundary powers** ([5],
eq. 31):

$$
g_b = \tfrac12\,\bigl(\hat p_{b-1} + \hat p_b\bigr), \qquad b = 1..B .
$$

$g_b$ is the column that every other element reads: the bus balance,
generation costs, fuel offtake, emissions, the water drawn by a hydro
turbine and the charge of a storage behind a converter.  All of them
therefore see the trapezoid energy, and with it the start-up and
shut-down energy.  No other element changes, so storage balances stay
exactly conservative.  The bus balance stays an energy balance per block
(demand and renewable data are block energies).  The model does NOT
balance power at the boundaries (eq. 19 of [5]).

**PB3 / PB4 — power-capacity limits with reserves** ([5], eqs. 4–6
and 26; TSTE 2016 [6], eqs. 13–14):

$$
p_b + \sum_i r^{+}_{i,b} \le (\overline P_b - \underline P_b)\,u_{k(b)}
 {}- E_b\,(\overline P_b - SD)\,w_{k^+(b)}
 {}+ E_b\,(SU - \underline P_b)\,v_{k^+(b)},
\qquad
p_b - r^{-}_{i,b} \ge 0 .
$$

The last boundary of a non-cyclic stage has no next period ($E_B$ terms
vanish, eq. 5 of [5]).  $r^{\pm}_{i,b}$ are the up / down provisions of
the unit's `ReserveProvision`s in block $b$, read at $\tau_b$.  $SU$ / $SD$
are `startup_ramp` / `shutdown_ramp` (default $\overline P$).  A unit
with a start-up (shut-down) trajectory uses $SU = \underline P$
($SD = \underline P$), the slow-start case of [5].

**PB5 / PB6 — instantaneous ramps and ramp capability** ([6], eqs.
15–16, with the ramp rate times the boundary spacing):

$$
p_b - p_{b-1} + \sum_i r^{+}_{i,b} \le RU\,\Delta_b\,u_{k(b)}
 {}+ E_b\,(SU - \underline P_b)\,v_{k^+(b)},
$$
$$
p_{b-1} - p_b + \sum_i r^{-}_{i,b} \le RD\,\Delta_b\,u_{k(b)}
 {}+ F_b\,(SD - \underline P_b)\,w_{k(b)} .
$$

The power is linear between the boundaries.  Inside the up state
$\hat p_b - \hat p_{b-1} = p_b - p_{b-1}$, so the rows bound the
instantaneous ramp exactly.  The reserve terms make every scheduled
reserve deliverable: from $\hat p_{b-1}$ the unit can still reach
$\hat p_b \pm r^{\pm}_b$ at $\tau_b$ (one reserve product per direction is
both a capacity and a ramp-capability reserve; [6] keeps them separate).

**PB7 — one-block outages** (a gtopt addition).  When a commitment
period $k$ is a single block, a shut-down at $T_k$ followed by a start-up
at $T_{k+1}$ ($w_k = v_{k^+} = 1$, $u_k = 0$) would let the trapezoid of
the "off" block run straight from $SD$ to $SU$.  With the default
$SU = SD = \overline P$ a unit could then cycle off for one block, keep
its output, skip its no-load cost and bypass its ramp limit.  Two rows
force both boundaries of such an outage to $\hat p = \underline P$:

$$
p_b + (SU - \underline P)\,w_k - (\overline P - \underline P)\,u_k
 \le SU - \underline P,
$$
$$
p_{b-1} - (\overline P - \underline P)\,u_{k-1}
 {}+ (\overline P - \underline P)\,w_k + (SD - \underline P)\,v_{k^+}
 \le SD - \underline P .
$$

Both rows are redundant in every other state of $(u_{k-1}, u_k, v, w)$
and are only written when $SU > \underline P$ ($SD > \underline P$).
[5] allows the $SD \to SU$ trapezoid; PB7 is a deliberate tightening
of the published model and is always on under `power_based`.

**Initial boundary.**  $\hat p_0$ uses $u_0$ = `initial_status` and the
events of the first period.  A known initial power (`initial_power`, or
`Generator.initial_generation` at the horizon start) fixes $\hat p_0$ —
an instantaneous power, not the previous hour's average.  A unit that is
off with zero output then cannot be up in the first period: it first
needs its start-up block or trajectory.  Otherwise $\hat p_0$ is free within PB1 / PB3
at $\tau_0$: an on unit anywhere in $[\underline P, \overline P]$, an off
unit at $\underline P\,v_0$ (the start-up boundary).  On a cyclic stage
(`cycle = "stage"`) boundary 0 is boundary $B$.

**Costs.**  The energy cost is charged on $g_b\,\Delta_b$, i.e. on the
trapezoid energy, and the trajectory energy is paid at the unit's
variable cost.  The no-load cost stays $C^{NL}\,u_k$ per hour of the
period.  To charge the no-load cost during the start-up / shut-down
process ([5], eqs. 16a–16b), add $C^{NL} S^U h$ ($C^{NL} S^D h$) to
`startup_cost` (`shutdown_cost`).

**Rows dropped.**  The energy-block rows C2 (`gen_upper` / `gen_lower`) and
C4–C5 are replaced by PB1–PB7.  The u-gated reserve headroom rows of §7
are rewritten on $p_b$ (PB3 / PB4).  The heat-rate kink rows keep their
constant breakpoints.  C1, C3, C6–C10, the start caps and the no-load
column are unchanged.  `min_down_time` should cover
$S^U h + S^D h$ so that a shut-down and the next start-up trajectory
cannot overlap; validation warns otherwise.

**Prices.**  The bus balance stays an energy balance per block, so its
dual is the block energy price.  The instantaneous power balance at the
block boundaries ([5], eq. 19) is out of scope.

**Clustered units.**  gtopt has no integer unit counts, so the clustered
formulation of [7] is not implemented; the power-based rows apply to
individual units.

---

## 5. Piecewise Heat Rate Curves

Real thermal generators have nonlinear fuel consumption: the marginal
heat rate increases at higher output levels.  GTOpt models this with
**piecewise linear segments**.

The piecewise curve is a property of the **`Generator`**, not of the
`Commitment`: set `fuel`, `pmax_segments` and `heat_rate_segments` on the
generator.  (`Commitment.pmax_segments`, `heat_rate_segments`, `fuel_cost`
and `fuel_emission_factor` were removed; see the
[migration note](#migrating-commitment-piecewise-fields).)  The curve
works with or without a commitment; when a `Commitment` exists, its
status $u$ tightens the segment breakpoints.

### Segment Definition

`pmax_segments` $= [\bar{P}_1, \ldots, \bar{P}_K]$ are the cumulative MW
breakpoints ($\bar{P}_K = P_\max$) and `heat_rate_segments`
$= [h_1, \ldots, h_K]$ the heat rate of each segment (fuel units per MWh,
strictly increasing).  Segment 1 rides on the generation variable
$p_{g,t}$ itself; each higher segment $k = 2, \ldots, K$ adds a slack
$s_{g,k,t} \geq 0$ (`Generator/heat_rate_slack`) and a kink row
(`Generator/heat_rate_kink`):

$$p_{g,t} - s_{g,k,t} \leq \bar{P}_{k-1}$$

When the generator has a `Commitment`, the kink row is retro-fitted to

$$p_{g,t} - s_{g,k,t} - \bar{P}_{k-1} \, u_{g,t} \leq 0$$

so that a fractional $u$ in the LP relaxation also shrinks the cheap
segments.  Convexity ($h_1 < h_2 < \cdots < h_K$) lets the LP fill the
segments in order without binaries or SOS2.

### Cost Calculation

With fuel price $c^{\text{fuel}}$ (`Fuel.price`) and variable O&M
$c^{\text{gen}}$ (`Generator.gcost`), the per-MWh costs are:

$$c_{g,1} = c^{\text{fuel}} \, h_1 + c^{\text{gen}}, \qquad
  c_{g,k}^{\text{slack}} = c^{\text{fuel}} \, (h_k - h_{k-1}),
  \quad k = 2, \ldots, K$$

so dispatch above $\bar{P}_{k-1}$ pays the marginal heat rate $h_k$.

**Emissions.**  Fuel-derived emission sources
(`Fuel.emission_factors[]` × heat rate) are built only for a **scalar**
`heat_rate`.  A generator that uses `heat_rate_segments` gets no
fuel-derived emissions; declare an explicit `EmissionSource` with a
representative per-MWh `rate` (see [§6](#6-emission-cost-and-cap)).
The emission rate is flat per MWh, not per segment.

### JSON Example

```json
{
  "fuel_array": [
    {"uid": 1, "name": "coal", "price": 3.5}
  ],
  "generator_array": [
    {"uid": 1, "name": "coal1", "bus": "b1", "pmin": 0, "pmax": 250,
     "capacity": 250, "fuel": "coal",
     "pmax_segments": [100, 200, 250],
     "heat_rate_segments": [8.5, 9.2, 11.0]}
  ],
  "commitment_array": [
    {"uid": 1, "name": "coal1_uc", "generator": "coal1",
     "initial_status": 1, "relax": true, "must_run": true}
  ]
}
```

This defines three segments for a 250 MW coal unit (fuel price 3.5 $/GJ):
- Segment 1: $[0, 100]$ MW at 8.5 GJ/MWh → \$29.75/MWh
- Segment 2: $[100, 200]$ MW at 9.2 GJ/MWh → \$32.20/MWh
- Segment 3: $[200, 250]$ MW at 11.0 GJ/MWh → \$38.50/MWh

<a id="migrating-commitment-piecewise-fields"></a>
**Migrating from `Commitment` piecewise fields.**  Move
`pmax_segments` and `heat_rate_segments` from the `commitment_array`
entry to its generator; replace `fuel_cost` with a `Fuel` element
(`price`) referenced by `Generator.fuel`; replace `fuel_emission_factor`
with `Fuel.emission_factors[]` (scalar `heat_rate`) or an explicit
`EmissionSource` (piecewise heat rate).

---

## 6. Emission Cost and Cap

Carbon pricing and emission caps are configured with emission
**zones**, not with model options.  This section summarises the parts
that matter for unit commitment; the full model (multi-pollutant
baskets, regional zones, local pollutants, marginal emissions) is in
[Emissions](emissions.md).

| Element | Role |
|---------|------|
| `emission_array` (`Emission`) | Pollutant registry: `co2`, `ch4`, `nox`, … |
| `emission_zone_array` (`EmissionZone`) | Scope (system-wide or regional) with `emissions[]` (`{emission, weight}`), optional `cap`, `cap_cost` and `price` |
| `emission_source_array` (`EmissionSource`) | Generator → zone → pollutant link with `rate` and optional `upstream_rate` [t/MWh] |
| `Fuel.emission_factors[]` | Builds sources automatically from a scalar `heat_rate` for every zone covering the pollutant |
| `Generator.emission_captures[]` | CCS: capture fraction `rate` and `cost` (\$/t captured) |
| `allowance_pool_array` (`AllowancePool`) | Cap-and-trade with banking, linked from `EmissionZone.allowance_pool` |

For source $\nu$ (generator $g$, zone $z$, pollutant $\epsilon$), the
emission per MW of dispatch in block $b$ is

$$\alpha^{\text{em}}_{\nu,t,b} = \mu_{z,\epsilon} \,
  (1 - \eta^{\text{cap}}_{g,\epsilon}) \,
  (f_{\nu,t} + f^{\text{up}}_{\nu,t}) \, \Delta_b
  \quad [\text{t/MW}]$$

where $\mu_{z,\epsilon}$ is the zone weight (GWP; 1 for a
single-pollutant zone), $\eta^{\text{cap}}_{g,\epsilon}$ the capture
fraction, $f$ the combustion `rate` and $f^{\text{up}}$ the
`upstream_rate` (notation of the
[formulation, §5.14 bis](formulation/mathematical-formulation.md#514-bis-emission-zone-cap-and-allowance-pool-banking)).

### 6.1 Emission Price (Carbon Tax)

Set `price` $c^{\text{em}}_{z,t}$ (\$/t) on a zone.  Every source in the
zone adds $c^{\text{em}}_{z,t} \, \alpha^{\text{em}}_{\nu,t,b} / \Delta_b$
(\$/MWh) to the cost of its generator's dispatch, weighted by duration,
probability and discount like any other energy cost.  For a
single-pollutant CO₂ zone this is

$$c_{g,t}^{\text{effective}} = c_{g,t}^{\text{gen}} + c^{\text{em}}_{z,t} \, f_{g,t}$$

No row is added; the price applies to every block of the stage.

### 6.2 Emission Cap

Set `cap` $\overline{E}_{z,t}$ (t per stage) on a zone.  One row per
(scenario, stage) sums the emissions of all its sources over the blocks
of the stage:

$$\sum_{b} \sum_{\nu \in z} \alpha^{\text{em}}_{\nu,t,b} \, p_{g(\nu),b}
  \;-\; s^{\text{cap}}_{z,t} \leq \overline{E}_{z,t}$$

- **Hard cap**: `cap` only; the slack $s^{\text{cap}}$ does not exist.
- **Soft cap**: `cap` + `cap_cost` $c^{\text{cap}}_{z,t}$ (\$/t); the
  slack $s^{\text{cap}} \geq 0$ is penalised at $c^{\text{cap}}_{z,t}$
  per tonne (no duration factor), and the cap's price can never exceed
  $c^{\text{cap}}_{z,t}$.

The cap dual (`EmissionZone/cap_dual`) is the **endogenous price** of
the pollutant (\$/t) at optimality: zero when the cap is slack, non-zero
when it binds (reported with a negative sign for this $\leq$ row in a
minimisation).  The cap is per stage, so a 24-block daily stage takes a
daily budget and an annual stage an annual budget.

When the zone sets `allowance_pool`, the per-stage cap row is not built;
the emissions are drawn from the pool's banked allowances instead (see
[Emissions](emissions.md)).

### 6.3 Combined Use

`price` and `cap` can be set on the same zone and act independently:
the price is a floor that every tonne pays, and when the cap binds the
marginal tonne costs `price` plus the absolute value of the cap dual.
Separate zones can price CO₂ system-wide and cap a local pollutant
(NOx, SO₂, PM) in one region at the same time, and a generator can feed
several zones.

`model_options.objective_mode = "emissions"` (set with
`--set model_options.objective_mode=emissions`) zeroes the generator
dispatch cost coefficients (`gcost`, fuel) and charges every zone source
at its `price`, or at 1 \$/t when the zone has no `price`, so the LP
minimises emissions.

### JSON Example

A system-wide CO₂ price of 30 \$/t plus a soft cap of 1500 t per stage:

```json
{
  "system": {
    "emission_array": [{"uid": 1, "name": "co2"}],
    "emission_zone_array": [
      {"uid": 1, "name": "system_co2",
       "emissions": [{"emission": "co2", "weight": 1.0}],
       "price": 30.0, "cap": 1500.0, "cap_cost": 200.0}
    ],
    "emission_source_array": [
      {"uid": 1, "name": "coal1_co2", "generator": "coal1",
       "zone": "system_co2", "emission": "co2", "rate": 0.9}
    ]
  }
}
```

Drop `price` for a pure cap, drop `cap_cost` for a hard cap, or drop
`cap` and `cap_cost` for a pure carbon tax.  A pollutant can be
referenced by name or by uid in zones, sources and fuels; both forms
are resolved against `emission_array`.  A source whose pollutant is not
listed in the zone's `emissions[]` contributes nothing to that zone.

### 6.4 Migrating from `emission_cost` / `emission_cap`

`model_options.emission_cost` and `model_options.emission_cap` were
removed; `--set model_options.emission_cost=...` now fails with
"unknown option or invalid value".  Replace them with a system-wide
zone:

| Before | After |
|--------|-------|
| `model_options.emission_cost: 30` | zone `"price": 30` |
| `model_options.emission_cap: 500000` | zone `"cap": 500000` (hard) |
| — | zone `"cap": 500000, "cap_cost": 200` (soft) |
| `Generator.emission_rate: 0.9` | `EmissionSource` with `"rate": 0.9` (recommended) |
| `Commitment.fuel_emission_factor` | `Fuel.emission_factors[]` or an `EmissionSource` |

`Generator.emission_rate` is still accepted: at load time it is folded
into an `EmissionSource` for pollutant `co2`.  The source goes to the
first zone whose `emissions[]` lists `co2` (by name or uid); if there
is none, a separate zone `default_co2` is
created, without price or cap, so the emissions are reported but not
priced.  Explicit `emission_source_array` entries avoid this ambiguity.
Likewise, the legacy `Fuel.combustion_emission_factor` /
`upstream_emission_factor` are folded into `Fuel.emission_factors[]`
for `co2`.  A generator with `heat_rate_segments` gets no fuel-derived
emissions (a warning names it); give it an explicit `EmissionSource`.

---

## 7. Reserve Integration

When a generator has both a `Commitment` entry and a `ReserveProvision`
entry, the reserve headroom constraints become conditional on the
commitment status:

### Without UC (standard)

$$p_{g,t} + r^{\text{up}}_{g,t} \leq P_\max$$

### With UC

$$p_{g,t} + r^{\text{up}}_{g,t} \leq P_\max \cdot u_{g,t}$$

Similarly for down-reserve:

$$p_{g,t} - r^{\text{dn}}_{g,t} \geq P_\min \cdot u_{g,t}$$

This ensures that an offline unit ($u = 0$) cannot provide spinning reserve.
The modification is done **in-place** on existing reserve provision rows,
not by adding new constraints.

---

## 8. Commitment Periods

By default, binary variables ($u$, $v$, $w$) are created **per block** — one
set of binaries for every time step.  For problems with fine time resolution
(e.g., 15-minute blocks in a 24-hour horizon = 96 blocks), this creates many
integer variables.

The `commitment_period` field allows grouping consecutive blocks into
**commitment periods** with one binary per period, while keeping generation
variables at the original fine resolution.

### How it Works

Every stage is a partition into cells $[\tau_{k-1}, \tau_k]$,
$\tau_k = \sum_{j \le k} w_j$ with $w_j$ the real block durations: on
block stages the cells are the blocks; on point-sampled stages
(`Stage.sampling = "point"`) they are the quadrature cells around the
node times `Block.time` (validation requires $t_k \in [\tau_{k-1}, \tau_k]$).  A commitment period is a union of whole cells: cell k joins
period $\lfloor m_k / P \rfloor$ with $m_k$ the cell midpoint, i.e. a
nominal boundary $mP$ inside a cell snaps to the nearer cell end (error
at most half that cell; a long cell spanning several boundaries leaves
no empty period).  Period lengths, hours-on and min up/down windows are
sums of cell durations on both semantics, consistent with the cost
quadrature.  Uniform blocks whose length divides P group exactly as
before.  `"auto"` sets P = max(smallest cell, ½·min(min_up_time,
min_down_time)), the UC-interval rule of the original Chebyshev
scheduling code.

When `commitment_period = 2.0` (hours) with 15-minute blocks:
- Each 2-hour window groups 8 blocks into one commitment period
- One $u$, $v$, $w$ variable per 2-hour period (12 periods × 3 = 36 binaries
  instead of 96 × 3 = 288)
- Generation $p_{g,t}$ remains at 15-minute resolution (96 variables)
- All blocks in a period share the same $u$ value

### Cost Scaling

The no-load cost is scaled by the period duration:

$$c^{\text{noload}} = c_{\text{noload}} \times \frac{\text{period\_duration}}{\text{rep\_block\_duration}} \times \text{ecost\_factor}$$

### JSON Example

```json
{
  "uid": 1,
  "name": "coal1_uc",
  "generator": 1,
  "commitment_period": 2.0,
  "startup_cost": 50000,
  "noload_cost": 500,
  "initial_status": 1
}
```

### Commitment grid

`commitment_grid` makes the choice explicit: `"block"` gives one status
per block / cell (min up/down, starts and hours-on in the real cell
durations, also for variable durations); `"overlay"` lays a grid of
`commitment_period` periods over the blocks.  The default is `"block"`
without `commitment_period` and `"overlay"` with it, so existing cases
are unchanged.  `"block"` together with `commitment_period`, or
`"overlay"` without it, is a validation error.  Block stages with
uniform durations keep the legacy accumulated grouping (a period closes
once its duration reaches P); point stages and variable-duration block
stages use the midpoint rule above.

### Cyclic commitment (typical days / weeks)

`"cycle": "stage"` treats the stage as a typical period repeated in a
cycle.  No initial state is used (`initial_status`, `initial_hours` and
`initial_power` are ignored, with a validation warning):

- the first transition wraps to the last period,
  $u_1 - u_P = v_1 - w_1$;
- min up / down windows and the hot/warm/cold off-time windows wrap
  around the cycle (periods $(p, p+1, \dots) \bmod P$, counted in cell
  durations).  A window longer than the cycle is capped at it, with a
  warning: the unit then cannot cycle within the period;
- ramps wrap: the backward ramp of the first block uses the last block,
  and `central2` always uses the wrapped stencil;
- on a point stage whose last node is the period end ($t_N - t_0$ =
  period, Lobatto), $t_N \equiv t_0$: node $N$ shares the status /
  startup / shutdown of node 0 and its cell joins period 0, so there is
  no phantom start at the boundary;
- start-up, shut-down and tier costs are counted per cycle and scaled
  by the stage weight `duration / period_hours` (a typical week
  standing for a month pays its starts that many times; energy and
  no-load costs already scale through the block durations).

Representative days (cycle groups, weekday + weekend) are each cyclic
on themselves.  UC states are never carried between stages (that holds
for non-cyclic units too: every chronological stage starts from
`initial_status`).

### Units whose times do not fit the typical period

A cyclic unit whose `min_up_time`, `min_down_time` or finite start
window (`max_starts` / `min_starts` with `starts_scope`) is longer than
the typical period T (Σ real cell durations, or `period_hours`) cannot
honour them within the cycle.  gtopt never drops them silently: every
such unit must be declared, or validation fails with ONE error that
lists all units, stages, constraints and times vs T, and shows the
snippet to add:

| `long_transition` | Meaning |
|-------------------|---------|
| `"on"` | committed ($u = 1$) for the whole typical period: pmin .. pmax, no starts / stops inside it |
| `"off"` | unavailable ($u = 0$, output 0) |
| `"no_uc"` | no commitment for the stage: continuous 0 .. pmax (pmin relaxed to 0), no start or no-load costs, ramps kept |

`long_transition_schedule` (by stage position) varies the choice per
stage, e.g. `["on", "on", "off", ...]`.  A planning-wide default may be
set explicitly with `model_options.long_transition`.  Each declared
unit gets a warning listing the dropped constraints, and the validation
log ends with a "Long-transition summary" table (unit, mode, source,
dropped constraints).  A change between consecutive stages declared
`off` → `on` (or `on` → `off`) pays the start-up (shut-down) cost once.
The check only covers stages where the commitment is `active`.

### Block formulation on typical periods

A typical period that is not declared `chronological` gets the block
formulation (one $u$ per block, $P^{\min} u \le p \le P^{\max} u$, no
transitions or times), so it needs no initial state and nothing wraps;
it is the recommended UC there when unit times do not matter.  The
cyclic model above applies only to chronological stages.

### Integer-free stages and phases

`active` (scalar or per-stage schedule) switches a `Commitment` off in
individual stages: no $u/v/w$ columns or rows
there, and the generator dispatches as a continuous unit with its own
`Generator.pmin` (not `Commitment.pmin`).  The first
active stage after inactive ones starts from `initial_status` (like
every stage).  `model_options.continuous_phases` (and `--no-mip`)
LP-relaxes every integer of the matching phases, e.g. fill months of an
SDDP study.

---

## 9. Relaxation Control

### Per-Element Relaxation

Set `relax: true` on a `Commitment` entry to make its $u$, $v$, $w$ variables
continuous in $[0, 1]$ instead of binary $\{0, 1\}$:

```json
{
  "uid": 1,
  "generator": 1,
  "relax": true
}
```

This is useful for:
- **Faster solves**: LP relaxation avoids the branch-and-bound search
- **Lower bounds**: the LP relaxation objective is a lower bound on the
  MIP objective, useful for gap analysis
- **Approximate dispatch**: fractional commitment often gives good dispatch
  approximations

### Per-Phase Relaxation

Set `model_options.continuous_phases` to relax binaries for specific planning
phases without modifying individual commitment entries:

```json
{
  "options": {
    "model_options": {
      "continuous_phases": "all"
    }
  }
}
```

The `continuous_phases` field accepts a phase range expression:

| Expression | Meaning |
|------------|---------|
| `"none"` or `""` | No relaxation (default) — all UC binaries are integer |
| `"all"` | All phases relaxed — pure LP, no integers |
| `"3"` | Only phase 3 is relaxed |
| `"1,3,5"` | Phases 1, 3, and 5 are relaxed |
| `"2:5"` | Phases 2 through 5 (inclusive) are relaxed |
| `"3:"` | Phase 3 and all subsequent phases |
| `":5"` | Phases 0 through 5 |
| `"0,3:5,8:"` | Phases 0, 3–5, and 8 onward |

The CLI equivalent:

```bash
gtopt my_case.json --set model_options.continuous_phases="all"
```

### Must-Run

Set `must_run: true` to force $u = 1$ at all times (lower bound = 1):

```json
{
  "uid": 1,
  "generator": 1,
  "must_run": true
}
```

The generator must remain online regardless of cost.  This is useful for
baseload units, system stability requirements, or contractual obligations.

---

## 10. JSON Input Reference

### 10.1 Commitment Object

The `commitment_array` lives inside the `system` section. Each entry links
to exactly one generator via a foreign key.

| Field | Type | Required | Default | Unit | Description |
|-------|------|----------|---------|------|-------------|
| `uid` | integer | yes | — | — | Unique identifier |
| `name` | string | no | `""` | — | Human-readable name |
| `active` | boolean | no | `true` | — | Whether this commitment is active |
| `generator` | integer/string | **yes** | — | — | Foreign key to Generator (uid or name) |
| `startup_cost` | real / array | no | 0 | $/start | Cost per startup event (stage-schedulable) |
| `shutdown_cost` | real / array | no | 0 | $/stop | Cost per shutdown event (stage-schedulable) |
| `noload_cost` | real | no | 0 | $/hr | Fixed hourly cost when committed; charged per block (`noload_cost · u · duration`) on chronological and non-chronological stages |
| `noload_fuel` | real | no | — | fuel unit/h | No-load fuel burn while committed; counted in fuel offtake limits and fuel-based emissions, not re-priced |
| `pmin` | real / array | no | generator lower bound | MW | Minimum stable level while committed ($p \ge P_{\min} u$), per stage and block |
| `min_up_time` | real | no | — | hours | Minimum time online after startup |
| `min_down_time` | real | no | — | hours | Minimum time offline after shutdown |
| `ramp_up` | real | no | — | MW/hr | Maximum ramp-up rate while online |
| `ramp_down` | real | no | — | MW/hr | Maximum ramp-down rate while online |
| `startup_ramp` | real | no | $P_\max$ | MW | Maximum output in startup block |
| `shutdown_ramp` | real | no | $P_\max$ | MW | Maximum output in shutdown block |
| `initial_status` | real | no | 0 | — | Initial state: 1.0 = online, 0.0 = offline |
| `initial_hours` | real | no | — | hours | Hours in current state at $t = 0$ |
| `relax` | boolean | no | `false` | — | LP relaxation: $u, v, w \in [0, 1]$ |
| `must_run` | boolean | no | `false` | — | Force $u = 1$ at all times |
| `transition_integrality` | string | no | `model_options` (`auto`) | — | Startup / shutdown integrality: `auto` (binary), `continuous`, `binary` ([§4.1](#41-decision-variables)) |
| `startup_tier_integrality` | string | no | `model_options` (`continuous`) | — | Hot / warm / cold selector integrality: `continuous` (TU tier rows) or `integer` ([§4.1](#41-decision-variables)) |
| `fast_start` | boolean | no | auto | — | Fast-start unit for `pricing_mode = "fast_start"`; unset = auto rule (min up and min down ≤ `fast_start_max_hours`) |
| `commitment_period` | real \| `"auto"` | no | — | hours | Coarser binary variable resolution (`"auto"`: max(smallest cell, ½·min(MUT, MDT))) |
| `commitment_grid` | string | no | block / overlay / cells | — | `"block"` (one status per block) or `"overlay"` (per `commitment_period`); default overlay iff `commitment_period` is set; on point stages see [typical-week sampling §4c](methods/typical-week-sampling.md) |
| `ramp_allowance` | string | no | boundary / period | boundary | SU/SD ramp allowance only at commitment-period boundaries (default), or on every block of the commitment period (`period`, older, looser form) |
| `uc_formulation` | string | no | global | — | `"energy_block"` or `"power_based"` (§4.6); overrides `model_options.uc_formulation` |
| `startup_trajectory` | real[] | no | — | MW | Power-based: start-up powers $[P^{SU}_1..P^{SU}_S]$ every `trajectory_step` h before reaching pmin |
| `shutdown_trajectory` | real[] | no | — | MW | Power-based: shut-down powers $[P^{SD}_2..P^{SD}_{S+1}]$ every `trajectory_step` h after leaving pmin |
| `trajectory_step` | real | no | 1 | hours | Trajectory sample spacing |
| `cycle` | string | no | `"none"` | — | `"stage"`: cyclic commitment over the stage (typical day / week) |
| `long_transition` | string | cond. | — | — | `"on"` / `"off"` / `"no_uc"` for cyclic units whose MUT/MDT/start window exceeds the typical period (required for them) |
| `long_transition_schedule` | string[] | no | — | — | per-stage `long_transition` |
| `hot_start_cost` | real | no | — | $/start | Startup cost for hot start |
| `warm_start_cost` | real | no | — | $/start | Startup cost for warm start |
| `cold_start_cost` | real | no | — | $/start | Startup cost for cold start |
| `hot_start_time` | real | no | — | hours | Maximum offline hours for hot start |
| `cold_start_time` | real | no | — | hours | Minimum offline hours for cold start |
| `initial_power` | real | no | `Generator.initial_generation`, else 0 | MW | Output before the first block, used by the first-block ramp rows |
| `fixed_status` | real / array | no | — | — | Pin $u$ per stage and block (1 on, 0 off); unset blocks stay free; wins over `must_run` |
| `max_starts` / `min_starts` | integer | no | — | starts | Hard bounds on the number of starts per `starts_scope` window |
| `starts_scope` | string / integer | no | `horizon` | — | `hour`, `day`, `week`, `horizon`, or a window length in hours |

**Notes**:
- All fields except `generator` are optional.  Omitting a field disables the
  corresponding constraint (e.g., no `min_up_time` → no C6 constraint).
- `startup_cost` and `shutdown_cost` accept either a scalar (applies to all
  stages) or an array (per-stage values).
- `pmax_segments`, `heat_rate_segments`, `fuel_cost` and
  `fuel_emission_factor` are no longer `Commitment` fields: the piecewise
  curve is set on the `Generator` ([§5](#5-piecewise-heat-rate-curves))
  and emissions through emission zones and sources
  ([§6](#6-emission-cost-and-cap)).
- Startup tiers require **all five** fields (`hot_start_cost`, `warm_start_cost`,
  `cold_start_cost`, `hot_start_time`, `cold_start_time`) to be present.
  Partial specification is silently ignored.

### 10.2 Stage — Chronological Flag

```json
{
  "uid": 0,
  "first_block": 0,
  "count_block": 24,
  "chronological": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `chronological` | boolean | `false` | Blocks are consecutive time periods |

When `chronological = true`, blocks in this stage represent sequential hours
(or sub-hours) and the full UC formulation is applied.  When `false`
(default), blocks may represent duration-weighted representative periods
(load-duration curves) where temporal ordering is meaningless: the
Commitment then uses the **block formulation** — one status `u` per block,
`p ≤ Pmax·u` and `p ≥ Commitment.pmin·u` (the same `pmin`; `Generator.pmin`
stays an always-on floor), `relax` / `must_run` / `fixed_status` honoured,
reserve and inertia gating unchanged, and the no-load cost (and
`noload_fuel`) charged per block (`noload_cost · u · duration`).  Startup /
shutdown costs, ramps, min up/down times, start tiers / counts and
`commitment_period` need chronology and are ignored there (one info note
per element).

The deprecated `simple_commitment_array` is read as plain Commitments
(`dispatch_pmin` → `pmin`; the generator's own `pmin`, which the legacy
element made conditional, moves onto the commitment) with a one-time
deprecation warning.

### 10.3 Generator — Emissions

```json
{
  "uid": 1,
  "name": "coal1",
  "bus": 1,
  "gcost": 25,
  "capacity": 600,
  "emissions": [
    {"zone": "system_co2", "emission": "co2", "rate": 0.95}
  ]
}
```

| Field | Type | Default | Unit | Description |
|-------|------|---------|------|-------------|
| `emissions` | array | `[]` | — | Inline `EmissionSource` rows (`zone`, `emission`, `rate`, `upstream_rate`); `generator` is filled in automatically |
| `emission_captures` | array | `[]` | — | CCS per pollutant: `{emission, rate, cost}` (capture fraction, $/t captured) |
| `fuel` + `heat_rate` | FK + real | — | — | With `Fuel.emission_factors[]`, builds one source per covering zone |
| `emission_rate` | real | — | tCO₂/MWh | Legacy: folded into a `co2` `EmissionSource` ([§6.4](#64-migrating-from-emission_cost--emission_cap)) |

The sources only report emissions until a zone they point to has a
`price` or `cap` ([§6](#6-emission-cost-and-cap)).

### 10.4 Model Options — Relaxation and Objective

```json
{
  "options": {
    "model_options": {
      "continuous_phases": "none"
    }
  }
}
```

| Field | Type | Default | Unit | Description |
|-------|------|---------|------|-------------|
| `continuous_phases` | string | `"none"` | — | Phase range expression for LP relaxation |
| `objective_mode` | string | `"cost"` | — | `"emissions"` minimises emissions instead of cost ([§6.3](#63-combined-use)) |
| `pricing_mode` | string | `"fixed"` | — | LP used for the nodal prices: `fixed`, `relaxed`, `fast_start` ([§11.1](#111-pricing-modes)) |
| `fast_start_max_hours` | real | 1 | hours | Auto fast-start rule threshold |
| `transition_integrality` | string | `"auto"` | — | Default startup / shutdown integrality ([§4.1](#41-decision-variables)) |
| `startup_tier_integrality` | string | `"continuous"` | — | Default start-up tier selector integrality ([§4.1](#41-decision-variables)) |

Emission prices and caps are not model options; they live on
`EmissionZone` ([§6](#6-emission-cost-and-cap)).

---

## 11. Output Files

When unit commitment is active, the following output files are produced in
the `output/Commitment/` directory:

| File | Contents |
|------|----------|
| `status_sol.{csv,parquet}` | Commitment status $u$ solution (1 = online) |
| `status_cost.{csv,parquet}` | No-load cost contribution per block |
| `startup_sol.{csv,parquet}` | Startup event $v$ solution (1 = started) |
| `startup_cost.{csv,parquet}` | Startup cost per block |
| `shutdown_sol.{csv,parquet}` | Shutdown event $w$ solution (1 = shut down) |
| `shutdown_cost.{csv,parquet}` | Shutdown cost per block |
| `logic_dual.{csv,parquet}` | Dual variable of C1 logic constraint |
| `gen_upper_dual.{csv,parquet}` | Dual of upper generation bound constraint |
| `gen_lower_dual.{csv,parquet}` | Dual of lower generation bound constraint |
| `exclusion_dual.{csv,parquet}` | Dual of C3 exclusion constraint |
| `ramp_up_dual.{csv,parquet}` | Dual of C4 ramp-up constraint |
| `ramp_down_dual.{csv,parquet}` | Dual of C5 ramp-down constraint |
| `min_up_time_dual.{csv,parquet}` | Dual of C6 min-up-time constraint |
| `min_down_time_dual.{csv,parquet}` | Dual of C7 min-down-time constraint |
| `hot_start_sol.{csv,parquet}` | Hot start tier selection |
| `hot_start_cost.{csv,parquet}` | Hot start cost |
| `warm_start_sol.{csv,parquet}` | Warm start tier selection |
| `warm_start_cost.{csv,parquet}` | Warm start cost |
| `cold_start_sol.{csv,parquet}` | Cold start tier selection |
| `cold_start_cost.{csv,parquet}` | Cold start cost |
| `startup_type_dual.{csv,parquet}` | Dual of C8 type selection |
| `hot_start_dual.{csv,parquet}` | Dual of C9 hot window constraint |
| `warm_start_dual.{csv,parquet}` | Dual of C10 warm window constraint |

All files follow the standard GTOpt output format:
`scenario, stage, block, uid:<commitment_uid>`.

### 11.1 Pricing Modes

The dispatch of a UC run is the MIP solution; the prices
(`Bus/balance_dual`) come from a separate pricing LP chosen by
`model_options.pricing_mode` (CLI `--pricing-mode`):

| Mode | Prices from | Typical effect |
|---|---|---|
| `fixed` (default) | all integers fixed at the incumbent | a unit kept on at `pmin` prices below its cost (can be negative) |
| `fast_start` | fixed, except fast-start units relaxed to [0, 1] | a committed peaker sets the price at its average cost |
| `relaxed` | LP relaxation of the whole MIP | start-up / no-load costs of all units reflected; lowest uplift in practice |

A commitment is fast-start when `fast_start: true`, or, when unset, if
its `min_up_time` and `min_down_time` are both ≤
`fast_start_max_hours` (default 1 h).  If the pricing LP is not optimal
the `fixed` prices are kept and a warning is logged.  The output
directory gets `pricing.csv`, one row per (scene, phase):

```
scene,phase,pricing_mode,applied_mode,fallback,attempted,relaxed_columns,pinned_columns,status,reason
0,0,fast_start,fast_start,0,1,2,2,0,""
```

Compare modes with the uplift tool (make-whole + lost-opportunity):

```bash
for m in fixed relaxed fast_start; do
  gtopt case.json --pricing-mode $m --set output_directory=out_$m
done
gtopt_pricing_uplift out_fixed out_relaxed out_fast_start --json-out uplift.json
```

The toy `cases/uc_pricing_*` (two units, two hours) gives prices
(10, 10), (60, 12.5) and (60, 10) for `fixed`, `relaxed` and
`fast_start`, with total uplift 3200, 1700 and 1900.

#### Literature benchmarks

The modes are checked against published non-convex pricing examples
(`test/source/test_pricing_literature.cpp`, e2e cases
`cases/uc_pricing_lit_*`, uplift in
`scripts/gtopt_pricing_uplift/tests/test_literature.py`).  Parameters
are typed in from the papers.  ✓ = matches the published value;
≠ = expected difference, explained below the table.

| Benchmark | Published | `fixed` | `relaxed` | `fast_start` |
|---|---|---|---|---|
| Scarf / O'Neill et al. [1] as adapted by Hogan & Ring [2], demands 1, 10, 22, 40, 61, 88, 145 | IP price 2 / 7 / 3 / 7 / 3 / 7 / 7; IP uplift (Σ start-up prices) 30 / −5 / 76 / −26 / 205 / −59 / −91; MinUP price 6.286 / 6.312 / 7; MinUP uplift e.g. 3.875 at 61 | ✓ prices, start-up prices (53 / 23 at 61) and Σ | ✓ exact convex hull 44/7, 101/16, 7; ✓ uplift | = `relaxed` (all units fast-start) ✓ |
| Gribik, Hogan & Pope [3] two-plant example (figures only) | restricted / convex-hull / "dispatchable" MC curves | ✓ restricted MC 65 / 110 / 65 / 90 / 110 at 50 / 150 / 190 / 250 / 350 MW | ✓ convex hull 65 / 95 / 95 / 95 / 110; ≠ their dispatchable model (a) | = `relaxed` ✓ |
| PJM [4] Ex. 1, 3 buses | restricted 67.5 / 50 / 32.5; integer relaxation 117 / 76 / 35; G2 uplift 2600 → 0 | ✓ ✓ | ✓ (same LP) | ✓ PJM pricing run |
| PJM [4] Ex. 2 | restricted 75 / −15 / 30; integer relaxation 69.8 / 11 / 40.4 | ✓ bus 3, ≠ (b) bus 1 / 2 | ✓ | ✓ |
| Hua & Baldick [5] Ex. 1 | LMP 50, CHP 12, CHPq 52; uplift 100 + 1900, 1430, 30 | ✓ 50; ✓ uplift | ✓ 12; ✓ uplift | ✓ 52 = CHPq with only unit 1 fast-start (c) |
| Hua & Baldick [5] Ex. 2, ramp-coupled, 3 h | LMP 60 / 60 / 60 (U2 560); aCHP1 60 / 60 / 64; CHP 60 / 60 / 65.6 | ✓ prices, ✓ U2 560 | ≠ 60 / 60 / 62.8 (d) | = `relaxed` |
| Eldridge, O'Neill & Hobbs [6] replicated example (N = 0) | LMP 25, idle Gen1 LOC 250 | ✓ 25, ✓ 250 | 15 (derived; not tabulated) | = `relaxed` |

(a) GHP's "dispatchable" model spreads the fixed cost over the full
capacity of each cost step (70 / 120 $/MWh for plant B).  gtopt gates
every heat-rate kink row by `u` (`p − s_k ≤ P̄_k·u`), the perspective of
the unit cost, so its relaxation of a single period is the convex hull
(B at 95 $/MWh) — tighter than the dispatchable model.

(b) Dual-degenerate: G1 is at its 450 MW maximum while line 1-2 binds,
so every λ1 ≥ MC(450) supports the dispatch; PJM reports the bottom
(75).  With the 1-MW secant piecewise cost used for the quadratic units
the bottom is 74.975; the tests pin λ3 = 30 and λ1 + λ2 = 2·λ3.

(c) Pinning the uncommitted unit 2 at 0 removes it from the pricing LP,
which is exactly CHPq (and Eldridge et al.'s rCHP).  The uplift tool
still reports unit 2's 2000 lost opportunity, which CHPq does not pay.

(d) With ramp limits the LP relaxation of a unit is not the convex
hull of its multi-period set (H&B add valid inequalities to reach it).
gtopt's relaxed hour-3 price is 820/13 ≈ 63.08 $/MWh: the relaxation
runs both units at fractional commitment and the fractional `u` also
scales the start-up ramp; the same LP written out by hand (scipy /
HiGHS) gives those duals.  So LMP 60 < gtopt 63.08 < aCHP1 64 <
CHP 65.6.  The uplift tool also ignores ramps, so at the aCHP1 / CHP
prices it reports \$360 for unit 2 instead of the published 160 / 0.

PJM's per-unit uplift of its flexible units (Ex. 1: 619 + 31) comes
from its settlement model (profit maximisation subject to the system
constraints); those units carry no commitment and are outside the
uplift tool.  MindOpt 2.3.0 crashes inside its own library on the PJM
piecewise MIPs; the PJM tests run on CPLEX and HiGHS.

[1] R. P. O'Neill et al., "Efficient market-clearing prices in markets
with nonconvexities", EJOR 164(1):269-285, 2005,
[doi:10.1016/j.ejor.2003.12.011](https://doi.org/10.1016/j.ejor.2003.12.011).
[2] W. W. Hogan, B. J. Ring, "On Minimum-Uplift Pricing for Electricity
Markets", HEPG, 2003,
<https://lmpmarketdesign.com/papers/Hogan_Ring_minuplift_031903.pdf>.
[3] P. R. Gribik, W. W. Hogan, S. L. Pope, "Market-Clearing Electricity
Prices and Energy Uplift", HEPG, 2007,
<https://lmpmarketdesign.com/papers/Gribik_Hogan_Pope_Price_Uplift_123107.pdf>.
[4] PJM, "Proposed Enhancements to Energy Price Formation", 15 Nov
2017, Appendix B (FERC docket EL18-34).
[5] B. Hua, R. Baldick, "A Convex Primal Formulation for Convex Hull
Pricing", IEEE TPWRS 32(5):3814-3823, 2017,
[doi:10.1109/TPWRS.2016.2637718](https://doi.org/10.1109/TPWRS.2016.2637718).
[6] B. Eldridge, R. O'Neill, B. F. Hobbs, "Pricing in Day-Ahead
Electricity Markets with Near-Optimal Unit Commitment", EPRG WP 1840,
2018, <https://www.jbs.cam.ac.uk/wp-content/uploads/2023/12/eprg-wp1840.pdf>.

Piecewise heat-rate results are written by the generator
(`Generator/srmc_sol` with the active-segment cost; the per-segment
`heat_rate_slack` columns with `--write-out ...,extras:Generator`).
Emission results are in `EmissionSource/` (`emissions_sol`,
`combustion_sol`, `rate_sol`, and `upstream_sol` / `captured_sol` when
used) and `EmissionZone/` (`cap_dual`, plus `cap_slack_sol` for a soft
cap).

---

## 12. Worked Examples

### 12.1 Example 1: Basic UC with Two Generators

**Scenario**: A single-bus system with a committed coal unit and an uncommitted
gas peaker serving 100 MW of demand over 4 hourly blocks.

```json
{
  "system": {
    "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [
      {"uid": 1, "name": "d1", "bus": 1, "capacity": 100}
    ],
    "generator_array": [
      {
        "uid": 1, "name": "coal",
        "bus": 1, "pmin": 20, "pmax": 100,
        "gcost": 10, "capacity": 100
      },
      {
        "uid": 2, "name": "gas",
        "bus": 1, "pmin": 0, "pmax": 80,
        "gcost": 30, "capacity": 80
      }
    ],
    "commitment_array": [
      {
        "uid": 1, "name": "coal_uc",
        "generator": 1,
        "startup_cost": 100,
        "noload_cost": 5,
        "initial_status": 1,
        "relax": true
      }
    ]
  },
  "simulation": {
    "block_array": [
      {"uid": 0, "duration": 1},
      {"uid": 1, "duration": 1},
      {"uid": 2, "duration": 1},
      {"uid": 3, "duration": 1}
    ],
    "stage_array": [
      {"uid": 0, "first_block": 0, "count_block": 4, "chronological": true}
    ],
    "scenario_array": [{"uid": 0}]
  }
}
```

**Expected result**: Coal dispatches 100 MW in all blocks (cheapest at
$10/MWh + $5/hr noload).  Status $u = 1$ throughout.  No startups or
shutdowns ($v = w = 0$).

### 12.2 Example 2: Piecewise Heat Rate

**Scenario**: A committed generator with a 2-segment heat rate curve competing
against a flat-cost generator.

```json
{
  "system": {
    "fuel_array": [
      {"uid": 1, "name": "fuel1", "price": 5.0}
    ],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "pmin": 0, "pmax": 100,
       "gcost": 0, "capacity": 100, "fuel": "fuel1",
       "pmax_segments": [50, 100],
       "heat_rate_segments": [8.0, 12.0]},
      {"uid": 2, "name": "g2", "bus": 1, "pmin": 0, "pmax": 100,
       "gcost": 50, "capacity": 100}
    ],
    "commitment_array": [
      {
        "uid": 1, "name": "g1_uc", "generator": 1,
        "initial_status": 1, "relax": true, "must_run": true
      }
    ]
  }
}
```

**Effective costs**:
- g1 segment 1: $5 \times 8.0 = 40$ $/MWh (0–50 MW)
- g1 segment 2: $5 \times 12.0 = 60$ $/MWh (50–100 MW)
- g2: $50$ $/MWh

With demand = 80 MW: g1 dispatches 50 MW (segment 1 at $40), g2 dispatches
30 MW (at $50 < segment 2 at $60).

### 12.3 Example 3: Startup Cost Tiers

**Scenario**: A thermal unit offline for 3 hours with hot/warm/cold startup
tiers.

```json
{
  "commitment_array": [
    {
      "uid": 1, "name": "g1_uc", "generator": 1,
      "initial_status": 0,
      "initial_hours": 3.0,
      "relax": true,
      "hot_start_cost": 100,
      "warm_start_cost": 300,
      "cold_start_cost": 500,
      "hot_start_time": 2.0,
      "cold_start_time": 6.0
    }
  ]
}
```

Since the unit has been offline for 3 hours:
- Hot start (offline < 2h): **not available** (3h > 2h)
- Warm start (2h ≤ offline < 6h): **available** (3h is in range)
- Cold start (offline ≥ 6h): **available** but more expensive

The optimizer selects warm start at $300 (cheapest feasible tier).

### 12.4 Example 4: Emission Cost Shifting Dispatch

**Scenario**: Two generators — g1 is cheap but dirty, g2 is expensive but
clean.  Adding a carbon price reverses the merit order.

```json
{
  "system": {
    "generator_array": [
      {"uid": 1, "name": "coal", "bus": 1, "gcost": 10, "capacity": 100},
      {"uid": 2, "name": "gas", "bus": 1, "gcost": 30, "capacity": 100}
    ],
    "commitment_array": [
      {"uid": 1, "name": "coal_uc", "generator": 1, "initial_status": 1,
       "relax": true}
    ],
    "emission_array": [{"uid": 1, "name": "co2"}],
    "emission_zone_array": [
      {"uid": 1, "name": "system_co2",
       "emissions": [{"emission": "co2", "weight": 1.0}],
       "price": 25.0}
    ],
    "emission_source_array": [
      {"uid": 1, "name": "coal_co2", "generator": "coal",
       "zone": "system_co2", "emission": "co2", "rate": 1.0}
    ]
  }
}
```

- Without carbon price (no zone `price`): coal effective cost = $10/MWh →
  dispatches first
- With $25/tCO₂: coal effective cost = $10 + $25 × 1.0 = $35/MWh > gas at
  $30 → gas dispatches first

### 12.5 Example 5: Emission Cap

**Scenario**: An emission cap limits dirty generation, forcing a cost-optimal
split between generators.

Same system as Example 4, with the zone `price` replaced by a `cap`
(tCO₂ per stage):

```json
{
  "emission_zone_array": [
    {"uid": 1, "name": "system_co2",
     "emissions": [{"emission": "co2", "weight": 1.0}],
     "cap": 30.0}
  ]
}
```

With coal (`rate = 1.0 tCO₂/MWh`) and demand = 80 MW over a one-hour stage:
- Cap = 30 tCO₂ → coal can produce at most 30 MWh (30 / 1.0 / 1 h)
- gas (clean) must produce the remaining 50 MW
- `EmissionZone/cap_dual` = −20 $/tCO₂: the gas − coal cost gap
  ($30 − $10) divided by the coal emission rate, i.e. the carbon price
  that would give the same dispatch.  Add `"cap_cost"` to make the cap
  soft.

---

## 13. Integration with Planning Framework

### Architecture

```
Planning
  └─ System
       ├─ generator_array      ← Generator definitions
       ├─ commitment_array     ← UC parameters (1:1 with generators)
       ├─ reserve_zone_array   ← Reserve requirements
       └─ reserve_provision_array ← Generator-reserve links
  └─ Simulation
       └─ stage_array          ← chronological flag per stage
```

### LP Assembly Order

```
1. GeneratorLP     → creates p_{g,t} generation variables
2. CommitmentLP    → creates u/v/w variables, C1–C10 constraints
3. ReserveProvisionLP → creates reserve variables, headroom rows
4. CommitmentLP    → modifies reserve headroom rows (reserve-UC integration)
```

### SystemLP Integration

`CommitmentLP` is registered in `SystemLP` as a `Collection<CommitmentLP>`.
It is instantiated after `GeneratorLP` (its dependency) and before the LP
is solved.  The call sequence for each (scenario, stage) pair:

1. `CommitmentLP::add_to_lp()` — adds variables and constraints
2. Accesses `GeneratorLP::generation_cols_at()` to reference the generation
   variable for C2 bounds
3. Iterates over `ReserveProvisionLP` entries to modify headroom rows

### Chronological Gating

The `add_to_lp()` method checks `stage.is_chronological()`:
- `true`: UC constraints are applied
- `false`: method returns early, generator dispatches normally without UC

This allows mixed-mode planning: expansion planning stages use
non-chronological blocks (load-duration curves), while operational stages
use chronological blocks with full UC.

---

## 14. Known Limitations

### Current Implementation

1. **Initial min-up obligation**: When a unit is initially online
   (`initial_status = 1`) with `initial_hours < min_up_time`, the current
   formulation does **not** prevent an early shutdown.  The min-up constraint
   only fires on startups ($v_t = 1$) within the horizon, not on pre-horizon
   obligations.  *Workaround*: set `must_run = true` for the initial period
   or extend the horizon.

2. **Flat emission rate with piecewise heat rate**: an `EmissionSource`
   rate is per MWh of total generation $p$, not per heat-rate segment.
   Fuel-derived sources (`Fuel.emission_factors[]`) are only built for a
   scalar `heat_rate`; a generator with `heat_rate_segments` needs an
   explicit `EmissionSource` with a representative rate.

3. **Cross-stage state persistence**: Commitment state ($u$, $v$, $w$) does
   not persist across stages.  Each stage starts fresh with `initial_status`.
   For multi-stage problems, the last-period state should be passed as initial
   conditions for the next stage (not yet automated).

4. **No startup/shutdown profiles**: The current implementation models startup
   and shutdown as single-block events.  Multi-period startup trajectories
   (e.g., a 3-hour ramp from cold start) are not yet supported.

### Planned Improvements

- **Phase 4**: Startup/shutdown profiles (multi-block ramp trajectory)
- **Phase 5**: CCGT configuration tracking (multi-shaft combined cycle)
- **Phase 6**: Frequency/inertia constraints (RoCoF, nadir, system strength)
- **Phase 7**: Storage commitment (3-state battery, pumped hydro modes)

---

## 15. Comparison with Other Tools

| Feature | GTOpt | PyPSA | GenX | PLEXOS |
|---------|-------|-------|------|--------|
| **UC formulation** | 3-bin tight (Morales-España) | 1-bin UC (basic) | 1-bin UC | 3-bin tight |
| **Startup tiers** | Hot/warm/cold ✓ | No | No | Hot/warm/cold ✓ |
| **Piecewise heat rate** | ✓ Per-segment | ✓ Marginal cost | ✓ Piecewise | ✓ Piecewise |
| **Min up/down time** | Aggregated ✓ | Basic ✓ | Linked ✓ | Multiple forms ✓ |
| **Ramp constraints** | Tight (state-dependent) ✓ | Basic ✓ | Basic ✓ | Tight ✓ |
| **Emission cost** | ✓ Per-zone price, multi-pollutant | ✓ Global | ✓ Per-zone | ✓ Detailed |
| **Emission cap** | ✓ Per-zone, hard or soft, endogenous price | ✓ | ✓ | ✓ |
| **Reserve-UC** | ✓ Conditional headroom | ✓ | ✓ | ✓ |
| **Commitment periods** | ✓ Variable resolution | No | No | ✓ |
| **Phase relaxation** | ✓ Per-phase | No | No | ✓ |
| **Language** | C++26 | Python | Julia | C++/C# |
| **Open source** | ✓ AGPL-3.0 (or commercial) | ✓ MIT | ✓ GPL-2 | Commercial |

### Key Differentiators

- **Tight formulation**: The 3-bin model produces the tightest LP relaxation
  among compact UC formulations, reducing solve times for MIP
- **Flexible time resolution**: Commitment periods decouple binary and
  generation variable resolution
- **Integrated with expansion planning**: UC and capacity expansion in the
  same optimization framework
- **High-performance C++**: LP assembly uses `flat_map` and sparse matrices
  for fast construction of large-scale problems

---

## 16. Academic References

<a id="ref1"></a>
**[1]** G. Morales-España, J. M. Latorre, and A. Ramos, "Tight and Compact
MILP Formulation for the Thermal Unit Commitment Problem," *IEEE Transactions
on Power Systems*, vol. 28, no. 4, pp. 4897–4908, Nov. 2013.
[DOI: 10.1109/TPWRS.2013.2251373](https://doi.org/10.1109/TPWRS.2013.2251373)

> The foundational paper for the three-bin (u, v, w) formulation used in GTOpt.
> Demonstrates that this compact formulation produces the tightest LP relaxation.

<a id="ref2"></a>
**[2]** M. Carrión and J. M. Arroyo, "A Computationally Efficient Mixed-Integer
Linear Formulation for the Thermal Unit Commitment Problem," *IEEE Transactions
on Power Systems*, vol. 21, no. 3, pp. 1371–1378, Aug. 2006.
[DOI: 10.1109/TPWRS.2006.876672](https://doi.org/10.1109/TPWRS.2006.876672)

> The 1-bin UC formulation that preceded the 3-bin model.

<a id="ref3"></a>
**[3]** B. Knueven, J. Ostrowski, and J.-P. Watson, "On Mixed-Integer
Programming Formulations for the Unit Commitment Problem," *INFORMS Journal
on Computing*, vol. 32, no. 4, pp. 857–876, 2020.
[DOI: 10.1287/ijoc.2019.0944](https://doi.org/10.1287/ijoc.2019.0944)

> Comprehensive comparison of UC formulations; establishes the theoretical
> tightness hierarchy.

<a id="ref4"></a>
**[4]** D. A. Tejada-Arango, S. Lumbreras, P. Sánchez-Martín, and A. Ramos,
"Which Unit-Commitment Formulation is Best? A Comparison Framework," *IEEE
Transactions on Power Systems*, vol. 35, no. 4, pp. 2926–2936, Jul. 2020.
[DOI: 10.1109/TPWRS.2019.2962024](https://doi.org/10.1109/TPWRS.2019.2962024)

> Practical comparison of UC formulations on real-world instances.

<a id="ref5"></a>
**[5]** G. Morales-España, C. Gentile, and A. Ramos, "Tight MIP Formulations
of the Power-Based Unit Commitment Problem," *OR Spectrum*, vol. 37, no. 4,
pp. 929–950, Oct. 2015.
[DOI: 10.1007/s00291-015-0400-4](https://doi.org/10.1007/s00291-015-0400-4)

> Extension of the 3-bin model with power-based tightening.

<a id="ref6"></a>
**[6]** G. Morales-España, R. Baldick, J. García-González, and A. Ramos,
"Power-Capacity and Ramp-Capability Reserves for Wind Integration in
Power-Based UC," *IEEE Transactions on Sustainable Energy*, vol. 7, no. 2,
pp. 614–624, Apr. 2016.
[DOI: 10.1109/TSTE.2015.2498399](https://doi.org/10.1109/TSTE.2015.2498399)

> Power-capacity (eqs. 13–14) and ramp-capability (eqs. 15–16) reserve rows
> of the power-based UC, used by PB3–PB6.

<a id="ref7"></a>
**[7]** G. Morales-España and D. A. Tejada-Arango, "Modeling the Hidden
Flexibility of Clustered Unit Commitment," *IEEE Transactions on Power
Systems*, vol. 34, no. 4, pp. 3294–3296, Jul. 2019.
[DOI: 10.1109/TPWRS.2019.2908051](https://doi.org/10.1109/TPWRS.2019.2908051)
(preprint [arXiv:1811.02622](https://arxiv.org/abs/1811.02622); data and
GAMS model at <https://github.com/datejada/CUC-data>)

> Clustered UC benchmarks (IEEE 39 / 118 bus).  Their individual-UC
> reference is an hourly **energy-block** model (the GAMS `eMaxOut`,
> `eRampUp` / `eRampDw` rows), not the power-based one.

<a id="ref8"></a>
**[8]** G. Morales-España, L. Ramírez-Elizondo, and B. F. Hobbs, "Hidden
Power System Inflexibilities Imposed by Traditional Unit Commitment
Formulations," *Applied Energy*, vol. 191, pp. 223–238, 2017.
[DOI: 10.1016/j.apenergy.2017.01.089](https://doi.org/10.1016/j.apenergy.2017.01.089)

> Evaluates the hourly energy-block approximation, the omission of
> start-up / shut-down trajectories and expected-value wind schedules;
> each one usually raises expected costs by several percent.

---

## 17. Roadmap

### Completed ✓

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Three-bin core (C1–C3) + noload cost + startup/shutdown cost | ✓ |
| 1 | Emission cost and cap framework | ✓ |
| 2 | Ramp constraints (C4–C5) with startup/shutdown ramps | ✓ |
| 2 | Reserve-UC integration | ✓ |
| 3 | Minimum up/down time (C6–C7) | ✓ |
| 3 | Hot/warm/cold startup tiers (C8–C10) | ✓ |
| 3 | Piecewise heat rate curves | ✓ |
| 3 | Commitment periods (variable binary resolution) | ✓ |
| 3 | Phase-based relaxation control | ✓ |

### Planned

| Phase | Feature | Priority |
|-------|---------|----------|
| 4 | Startup/shutdown profiles (multi-block ramp trajectory) | High |
| 5 | CCGT configuration tracking (multi-shaft combined cycle) | Medium |
| 6 | Frequency security (inertia, RoCoF, system strength) | Medium |
| 7 | Storage commitment (3-state battery, pumped hydro modes) | Medium |
| 7 | Integer clustering for large-scale UC | Low |
| 7 | Stochastic UC (scenario-dependent commitment) | Low |

---

*Last updated: April 2025*
