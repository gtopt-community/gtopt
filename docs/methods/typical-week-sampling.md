# Typical-Week Sampling and Chebyshev Nodes (expansion planning)

Status: provisional.  This page is the reference note for the white
paper section on time representation in expansion planning.  It
summarises the method, where it lives in the code, and the FY28 AEMO
ISP comparison.

## 1. Idea

An expansion model cannot carry 8760 h per year and scenario.  A
common reduction keeps **one chronological typical week per month**:
storages, ramps and minimum up / down times run on a real week, while
energy, costs and investment annuities are scaled to the month.

gtopt allows any block durations inside a stage, so the week can be
sampled in several ways (a *week sampler*):

| Sampler (`--week-sampler`) | Blocks per week | Block type |
|---|---|---|
| `uniform:1` | 168 | 1 h blocks (block means) |
| `uniform:2` | 84 | 2 h blocks |
| `peak_ramp:K` | 7·(12 + K) | 1 h at the daily net-load peak and the K − 1 steepest ramp slots, 2 h elsewhere |
| `chebyshev:N[:gauss\|lobatto]` | N | point samples at Chebyshev nodes, quadrature weights as durations |
| `uniform:H:point` | 168 / H | point samples at the cell centres (power-based formulation, §6) |

`--ramp-refine 30m|15m|on` (default `off`) refines any of them around
sunrise and sunset (§4b).

The Chebyshev representation follows Matus, Cáceres, Puschel-Lovengreen
and Moreno, "Chebyshev based continuous time power system operation
approach", IEEE PES GM 2015, DOI
[10.1109/PESGM.2015.7286570](https://doi.org/10.1109/PESGM.2015.7286570):
the operation is a continuous function of time, represented by its
values at Chebyshev nodes, and every time integral is a quadrature.

## 2. Window and weights

- **Window**: 168 h from Wednesday 12:00 to the next Wednesday 12:00
  (`--week-start wed:12`, the default for every sampler).  The weekend,
  where low load and high solar drive storage cycling and curtailment,
  is in the middle of the window, away from the cyclic closure.  The
  week is the one holding the month's net-load peak (`--week-rule
  peak`) or the highest net energy (`energy`).
- **Month weight**: every block duration is its real duration times
  `days_in_month / 7`; the stage has `period_hours = 168`, so storages
  and ramps use the real durations while the objective and energy
  budgets see the month.

## 3. Chebyshev nodes and quadrature weights

On $x \in [-1, 1]$, mapped to $t = \tfrac{T}{2}(1 + x)$ with $T = 168$ h:

- Chebyshev–Gauss (N nodes): $x_k = -\cos\frac{\pi(2k-1)}{2N}$,
  $k = 1..N$; weights = Fejér's first rule.  Exact for polynomials of
  degree $\le N - 1$.
- Chebyshev–Gauss–Lobatto (N + 1 nodes, default): $x_k = -\cos\frac{\pi k}{N}$, $k = 0..N$; weights = Clenshaw–Curtis.  Exact for degree
  $\le N$.

Both weight sets are positive, symmetric and sum to $T$; they are
computed as the interpolatory weights $V^{-\top}\mu$ ($V$ the
Chebyshev–Vandermonde matrix, $\mu_k = \int_{-1}^1 T_k$).  These are the
standard definitions (Trefethen, *Approximation Theory and
Approximation Practice*, ch. 19; Waldvogel, BIT 46 (2006)); the paper
itself was not openly accessible when this note was written, and the
converter was earlier aligned with the authors' research code (Lobatto
nodes, Clenshaw–Curtis weights, trapezoid weights as an option).

The week is **periodic**: Lobatto node N and node 0 are the same instant
of the closed weekly cycle, so the converter emits N blocks, node 0
carrying $w_0 + w_N$ (symmetric on the circle).

## 4. LP formulation on point stages

Chebyshev weeks are emitted as `Stage.sampling = "point"`
(`docs/formulation/mathematical-formulation.md`, §5.7):

- Integrals (costs, energy) are $\sum_k w_k x_k$.
- **Storage** integrates between the nodes with the element's
  `storage_integration` (`cc`, `trapezoid`, `lg4_pos`, ...).  The
  converters (`plexos2gtopt` typical weeks / days with a Chebyshev or
  `uniform:H:point` sampler, `tcheby2gtopt` point cases) emit
  `battery_storage_integration = reservoir_storage_integration = "cc"`
  (`gtopt_shared.point_sampling.POINT_STORAGE_OPTIONS`) for both
  storage types; block stages ignore it.  `--point-storage-integration
  lg4_pos | trapezoid` selects a banded rule instead: its integration
  matrix is balanced so that its column sums equal the quadrature
  weights and its row sums equal the node intervals (a constant flow
  integrates exactly over every interval, the weekly total equals the
  quadrature), the level is AT the nodes, and two envelope rows per
  storage and node bound the level between them.  Every rule conserves
  storage over the closed week.
- **Clenshaw–Curtis rule** (`storage_integration = cc`, alias
  `clenshaw_curtis`; the same rows as `block`):
  $`e_k = e_{k-1} + w_k\,(\eta_c c_k - d_k/\eta_d - s_k + a_k)`$ with
  $w_k$ the CC weight of node k (node 0 carries $w_0 + w_N$, the
  closing node of the periodic grid), so $e_k$ is the level after node
  k's cell and the wrap closes on $e_{N-1}$.  Each row holds one previous
  level plus the flows of one node — as sparse as a block balance,
  cheaper than `trapezoid` (two flow nodes) and much cheaper than
  `lg4_pos` (four) — and every weight is positive, so there is no
  overshoot.  Over the closed period
  $\sum_k (e_k - e_{k-1}) = \sum_k w_k f_k$ is exactly the CC
  quadrature that also integrates energy and cost; `trapezoid` on
  Lobatto nodes does not have that property without the column
  correction.  The intermediate levels are first order only; the exact
  integral of the interpolant over each interval would make every row
  dense and is only an accuracy ceiling.  Select it with
  `--point-storage-integration cc` in both converters.
- **Ramps**: `ramp_formula = "backward"` (default) bounds the interval
  slope $P_k - P_{k-1}$ by $`R\,h_k`$, $h_k = t_k - t_{k-1}$ (cyclic wrap
  at node 0).  `ramp_formula = "central2"` bounds the second-order
  non-uniform central difference
  $P'(t_k) \approx \frac{h_-^2 P_{k+1} - h_+^2 P_{k-1} + (h_+^2 - h_-^2) P_k}{h_+ h_-(h_+ + h_-)}$, exact for quadratics.  It is opt-in:
  being a convex combination of two slopes, it lets a zig-zag dispatch
  through, and `validate_planning` warns.

## 4b. Sunrise / sunset refinement (`--ramp-refine`)

Net load changes fastest when solar rises and sets.  The refinement
adds resolution only there:

- **Events from the data**: in every 24 h of the window (noon to noon)
  the edges with the steepest net-load fall (sunrise) and rise (sunset)
  of the half-hourly trace (`ramp_events`); a day without both (flat
  net load) falls back to 06:00 / 18:00.  Following the data, not the
  clock, the windows move with the season.  Exactly two events per day
  keep the block count equal in every stage (the converter's (stage,
  block) layout is rectangular).
- **Block samplers** (`uniform:H`, `uniform:H:point`): one window of
  $\lceil 2W/H \rceil H$ hours per event (W = `--ramp-refine-width`,
  default 1 h) on the base grid, centred on the event as closely as the
  grid allows and kept disjoint, split into 30 / 15 min blocks; outside
  the windows the base blocks are kept.  Durations still sum to 168 h.
  (`peak_ramp:K` is not refined: its variable 1 h / 2 h slots would make
  the block count differ between months.)  Block values are cell means of an
  energy-conserving, shape-preserving interpolant: the cumulative
  energy $E(t)$ is exact at the data edges and interpolated with a
  monotone cubic (PCHIP), so every data interval keeps its energy, a
  nonnegative trace never gives negative sub-step values and a night
  stays dark.  At 30 min on the half-hourly AEMO traces this reduces to
  the exact data; only 15 min interpolates.  Every refined block carries
  `Block.commitment_cell` = its base block, so the binaries stay on the
  base grid.
- **Chebyshev (Lobatto) weeks**: nested nodes.  The order-2N Lobatto
  set contains every order-N node ($\theta_k = \pi k / N$ are the even
  $\theta_j = \pi j / (2N)$) and adds the $\theta$ midpoints.  All
  $N + 1$ nodes are kept, plus the 2 odd order-2N nodes nearest to every
  event (a constant node count per stage; with sparse nodes in the
  middle of the window a fixed ± W window would hold a variable number
  of them).
  Weights: the 2N Clenshaw–Curtis weights, each dropped node's weight
  split between its two kept neighbours by linear interpolation.  This
  is local and **positive** (needed by the `lg4_pos` storage rows), keeps
  the sum (168 h) and is exact on linear functions for any subset, and
  exact to degree 2N where nothing is dropped; the alternative
  (interpolatory weights of the irregular subset, $V^{-\top}\mu$) is
  exact to degree $|S| - 1$ but can produce negative weights and was not
  used.  Node values are those of the order-2N fit at the kept nodes.
- Commitment is not refined (next section).

## 4c. Commitment grid on point and refined stages

The binaries (u, v, w) never live on the Chebyshev nodes.  Each unit
has its own **uniform** grid of `commitment_period` hours (≥ 1 h,
default 1 h), aligned to the stage start — Wednesday 12:00 for the
typical week.  A node inherits the status of the cell containing its
time (a node exactly on a boundary belongs to the later cell; the end
node to the last cell); start-up / shut-down happen only at cell
boundaries.  The converters also write `ramp_allowance: "boundary"`
on point stages, so only ramp rows that cross a cell boundary get the
SU / SD allowance (gtopt's default too); `"period"` would give the
start-up allowance to every node of the starting cell, so a cell with
many (refined) nodes could ramp up by SU per node interval instead of
once.  Refined nodes / blocks therefore add continuous variables
only; the integer column count depends on the unit grids, not on N or
on the refinement.  A cell with no node (Chebyshev nodes are up to
$\tfrac{\pi T}{2N}$ apart in the middle of the window, 4.1 h for
N = 64) continues the previous cell's status, so the count equals
$168 / P_g$ per unit whenever $N \ge \pi \cdot 168 / (2 P_g)$.

Converters choose `commitment_period` per unit
(`gtopt_shared.point_sampling.commitment_period_class`): an explicit
override (`tcheby2gtopt convert --commitment-period NAME=H`), else the
source model's UC resolution, else a class from
m = min(min_up_time, min_down_time): m ≥ 8 h → slow, 4 h grid;
m ≥ 4 h → medium, 3 h; otherwise fast, 1 h.  The class of every unit
is logged and written to the conversion notes.

The previous behaviours stay reachable:

| Switch | Values | Effect |
|--------|--------|--------|
| `Commitment.commitment_grid` | unset / `overlay` (default) | point stages: uniform grid by node time, as above |
| | `block` | point stages: one status per node (previous default) |
| | `cells` (needs `commitment_period`) | point stages: periods as unions of quadrature cells (previous overlay) |
| `Commitment.ramp_allowance` | `boundary` (gtopt default) | only ramp rows crossing a period boundary get the SU / SD allowance |
| | `period` | every block of a commitment period gets its period's SU / SD allowance (older, looser form); the converters write `boundary` explicitly on point stages |
| `tcheby2gtopt convert --commitment-period-rule` | `class` (default) / `none` | per-unit grids / no `commitment_period` (previous) |
| `--point-storage-integration` (both converters) | `cc` (default), `lg4_pos`, `trapezoid`, any rule, `none` | storage rule written for point stages; `none` = gtopt's per-type defaults |
| `plexos2gtopt --ramp-refine` | `off` (default) / `30m` / `15m` | sunrise / sunset refinement |

## 5. Code map

| Piece | Where |
|---|---|
| Samplers, nodes, weights, fits | `scripts/plexos2gtopt/week_sampler.py` |
| Hourly reconstruction (quantities, levels, prices) | `scripts/plexos2gtopt/week_reconstruct.py` |
| ISP expansion case options | `scripts/plexos2gtopt/aemo_isp_expansion.py` (`--week-sampler`, `--week-start`, `--day-sampler`) |
| Point stages, ramps, storage integration | `include/gtopt/stage.hpp`, `source/commitment_lp.cpp`, `include/gtopt/storage_integration.hpp` |
| Small validation cases | `cases/cheby_small/`, `scripts/tcheby2gtopt/` |
| Ramp refinement, nested Chebyshev nodes | `scripts/plexos2gtopt/week_sampler.py` (`RampRefineSampler`, `NestedChebyshevRefine`, `smooth_cell_means`) |
| Per-unit commitment grid | `source/commitment_lp.cpp`, `scripts/gtopt_shared/point_sampling.py` |

## 6. Related continuous-time work and what gtopt can take from it

**Power-based UC (Morales-España et al.).**  G. Morales-España,
C. Gentile and A. Ramos, "Tight MIP formulations of the power-based
unit commitment problem", OR Spectrum 37:929–950, 2015, and
G. Morales-España et al., "Hidden power system inflexibilities imposed
by traditional unit commitment formulations", Applied Energy 191, 2017,
model **instantaneous powers at hourly nodes**
with piecewise-linear trajectories in between, so the energy of an hour
is the trapezoid of its end powers.  In gtopt this is the
`--week-sampler uniform:1:point` week: the same hourly grid as
`uniform:1`, emitted as a point stage whose storages integrate between
the nodes (`trapezoid` or `lg4_pos`; on uniform hourly nodes `cc` is
the block recursion of `uniform:1`).  The FY28 comparison of §7
includes it.

**Parvania and Scaglione (cubic Hermite / Bernstein trajectories).**
M. Parvania and A. Scaglione, "Unit Commitment With Continuous-Time
Generation and Ramping Trajectory Models", IEEE Trans. Power Syst.
31(4):3169–3178, 2016, DOI
[10.1109/TPWRS.2015.2479644](https://doi.org/10.1109/TPWRS.2015.2479644)
(open copy: [NSF PAR](https://par.nsf.gov/servlets/purl/10019537)).
Verified points of the paper:

- the hourly UC is read as a *linear spline* of the load; they replace
  it by a cubic Hermite spline per interval (value and derivative at
  both interval ends), written in the degree-3 **Bernstein** basis;
- the derivative of a degree-n Bernstein polynomial is n times
  differences of its coefficients, so the ramp trajectory is again a
  Bernstein polynomial with coefficients linear in the generation ones;
- by the **convex-hull property** the whole trajectory lies between the
  minimum and maximum of its Bernstein coefficients, so capacity and
  ramp limits imposed on the coefficients hold at **every instant**,
  not only at the nodes;
- the MILP keeps the same number of binaries; on CAISO data the
  real-time cost and the ramping-scarcity events drop.

**Khatami, Parvania (and Khargonekar): continuous-time storage and
prices.**  Cited as: M. Parvania and R. Khatami, "Continuous-Time
Marginal Pricing of Electricity", IEEE Trans. Power Syst. 32(3), 2017;
R. Khatami, M. Parvania and P. Khargonekar, "Scheduling and Pricing of
Energy Generation and Storage in Power Systems", IEEE Trans. Power
Syst. 33(4):4308–4322, 2018; R. Khatami and M. Parvania,
"Continuous-time Look-Ahead Scheduling of Energy Storage in Regulation
Markets", HICSS 2019 (Bernstein function space, LP); R. Khatami and M.
Parvania, "Continuous-Time Locational Marginal Price of Electricity",
IEEE Access.  Their common result: the marginal price is the
Lagrange-multiplier *trajectory* of the continuous power balance, and
a storage's level is the exact integral of its power trajectory.  The
hydro extension is C. Ø. Naversen et al., "Hydrothermal scheduling in
the continuous-time framework", Electric Power Systems Research 189,
2020 ([arXiv:1912.06877](https://arxiv.org/abs/1912.06877)).  (These
were checked against their abstracts / citing papers, not the full
texts.)

**What is worth adopting in gtopt** (recommendation first):

1. *Bounds between the nodes* (recommended, small).  gtopt bounds the
   storage level only at the nodes, plus the envelope rows (§4).  The
   Bernstein idea gives a cheap, exact alternative for any polynomial
   piece: bound its Bernstein coefficients.  For the `lg4` interval
   cubic of a level, the four Bernstein coefficients are linear in the
   two end levels and the two end flows, so 2 × 4 rows per interval
   enforce `emin ≤ e(t) ≤ emax` on the whole interval (sufficient, not
   necessary, i.e. slightly conservative).  The same holds for
   generator power between Chebyshev nodes when the node polynomial is
   piecewise cubic.  Not implemented here: it changes LP size and
   needs a design choice on which pieces to cover.
2. *Exact energy integration* — already the case: point stages use
   the quadrature weights and the balanced banded storage rows (§4),
   the analogue of integrating the Bernstein trajectory exactly.
3. *Continuous-time ramps* — gtopt's `backward` ramp bounds the
   interval slope, which for a piecewise-linear trajectory IS the
   instantaneous ramp everywhere in the interval (exact, as in the
   power-based UC); `central2` bounds a node derivative only.  For
   smooth (cubic) trajectories the Bernstein derivative coefficients
   would bound the ramp everywhere; worth it only together with item 1.
4. *Continuous-time prices* — the cell-constant node LMP
   $\pi_k = \lambda_k / w_k$ already used by the reconstruction is the
   discrete counterpart of the multiplier trajectory; the
   coefficient-space adjoint (`price_reconstruction = adjoint`) is the
   polynomial one.  No change recommended.
5. *Bernstein vs Chebyshev basis.*  Chebyshev nodes give near-minimax
   interpolation and spectrally accurate quadrature over a whole week
   (one global polynomial); Bernstein pieces give local control and
   the convex-hull bound.  They are complementary: keep Chebyshev nodes
   for sampling / quadrature, use the Bernstein form of each local
   piece only to certify bounds (item 1).

## 7. FY28 AEMO ISP comparison

One AEMO ISP 2024 FY2028 expansion year (12 typical weeks, one per
month; 138 batteries, no commitment_array, so the only status columns are
the synthesized battery charge / discharge statuses and the integer
column count is 0 in every case).  HiGHS 1.16 MIP/LP, same machine,
`scale_objective = 1000`.  Storage rule (battery / reservoir) per case
(§7a repeats N = 48 / 64 / 84 with one rule for both):
the N = 24 / 48 / 84 cases were converted before the converters wrote
lg4_pos and use gtopt's per-type defaults (cc = block recursion for
batteries, lg4_pos for reservoirs); only the N = 64 cases use lg4_pos
for both, so their solve times are not comparable with the others.
Solve times are HiGHS wall times on a shared, loaded host and only
indicative.

| Case | Blocks / stage | Storage (bat / res) | Objective [\$] | Δ vs u2 | Vars / rows | Solve [s] | Built MW | Battery discharge [GWh] | Zig-zag index | Gen. ramp [GW / week] | Battery energy dual mean |
|------|---------------:|---------------------|--------------:|--------:|------------:|----------:|---------:|------------------------:|--------------:|----------------------:|-------------------------:|
| uniform 2 h block | 84 | block / block | 4.1078e9 | — | 1.73M / 1.22M | 1036 | 6323 | 10402 | 0.26 | 571 | 36.89 |
| Chebyshev 24 | 24 | cc / lg4_pos | 4.0079e9 | −2.4 % | 0.50M / 0.35M | 149 | 6437 | 7406 | 0.70 | 235 | 36.37 |
| Chebyshev 48 | 48 | cc / lg4_pos | 4.0646e9 | −1.1 % | 0.99M / 0.70M | 429 | 6411 | 9687 | 0.36 | 435 | 36.52 |
| Chebyshev 64 | 64 | lg4_pos / lg4_pos | 4.0763e9 | −0.8 % | 1.32M / 1.14M | 2207 | 6323 | 10043 | 0.27 | 487 | 37.12 |
| Chebyshev 84 | 84 | cc / lg4_pos | 4.0971e9 | −0.3 % | 1.73M / 1.22M | 1388 | 6296 | 10283 | 0.24 | 557 | 36.79 |
| Chebyshev 64 + nested ramp refinement | 92 | lg4_pos / lg4_pos | 4.1262e9 | +0.4 % | 1.90M / 1.64M | 4503 | 6332 | 10531 | 0.19 | 610 | 36.86 |

Battery investment (1478.8 MW) is identical in every case.  The
Chebyshev objective converges from below toward the block reference as N
grows: at N = 24 the week is under-resolved, losing about a third of
the battery cycling and most of the ramping.  N = 64–84 lands within
1 % of the reference at the same or smaller LP size.  Adding the nested
sunrise / sunset nodes to N = 64 recovers the ramp volume and the
battery cycling of the 2 h reference and slightly exceeds its cost,
because the refined edges resolve the evening ramp that a 2 h block
averages away.  The zig-zag index (share of battery dispatch at the
Nyquist frequency) is only meaningful on uniform grids; on Chebyshev
grids it is shown for completeness.

The 1 h block reference (`uniform:1`, 168 blocks per stage, 3.46M
variables, 2.44M rows) has objective 4.1176e9 (HiGHS 5349 s): u2 is
−0.24 % below it, Chebyshev 84 −0.50 %, Chebyshev 64 −1.0 %.  It
cycles the batteries more (10542 GWh) and ramps more (732 GW / week)
than any Chebyshev week; that is resolution, not the storage rule.

### 7a. Storage rule, separated from N

Same machine (one solve at a time, HiGHS 1.16), the same rule for
batteries and reservoirs; error against the 1 h reference:

| N | Rule | Objective [\$] | vs 1 h | Rows | Nonzeros | Solve [s] | Battery discharge [GWh] | Zig-zag | Gen. ramp [GW / week] |
|--:|------|--------------:|-------:|-----:|---------:|----------:|------------------------:|--------:|----------------------:|
| 48 | cc | 4.0686e9 | −1.19 % | 0.70M | — | 225 | 9738 | 0.38 | 435 |
| 48 | lg4_pos | 4.0646e9 | −1.29 % | 0.86M | — | 923 | 9691 | 0.31 | 443 |
| 48 | trapezoid | 4.0477e9 | −1.70 % | 0.86M | — | 673 | 9942 | 0.30 | 442 |
| 64 | cc | 4.0794e9 | −0.93 % | 0.93M | 3.05M | 309 | 10066 | 0.28 | 484 |
| 64 | lg4_pos | 4.0763e9 | −1.00 % | 1.14M | 3.92M | 1401 | 10043 | 0.27 | 487 |
| 64 | trapezoid | 4.0553e9 | −1.51 % | 1.14M | 3.90M | 1276 | 10294 | 0.27 | 487 |
| 84 | cc | 4.1005e9 | −0.41 % | 1.22M | 3.99M | 705 | 10264 | 0.25 | 556 |
| 84 | lg4_pos | 4.0971e9 | −0.50 % | 1.50M | 5.14M | 2703 (†) | 10312 | 0.21 | 559 |
| 84 | trapezoid | 4.0746e9 | −1.04 % | 1.50M | 5.12M | 1889 | 10574 | 0.22 | 562 |

(†) overlapped with LP-only builds of other cases at its start.

`cc` is the fastest rule at every N (3.8–4.5× faster than `lg4_pos`)
and the closest to the 1 h reference; `lg4_pos` stays within 0.1 % of
it; `trapezoid` understates the cost by a further 0.5–0.6 % and
cycles the batteries ~3 % more.  The zig-zag index is the same for
every rule (`trapezoid` does not suppress it).  The converters
therefore emit `cc` on point stages.

Where the extra coefficients come from (LP-only builds, rows grouped
by element and constraint class): with `cc` a Chebyshev LP is exactly
as large as a block LP with the same number of blocks (N = 84 vs u2:
same rows and columns, nonzeros +0.05 %).  The banded rules add no
columns but two envelope rows per battery and node (`envelope_up` /
`envelope_dn`, about 3 nonzeros each: 275k rows and 0.84M nonzeros at
N = 84) and widen the battery balance from 4 to 6 nonzeros per row;
`lg4_pos` and `trapezoid` differ by only 0.3 % of the nonzeros, so the
band width is not the cost, the envelope rows are.  `lg4_pos` also
produces the smallest coefficients of the LP (battery balance down to
2.9e-4 vs 2.2e-2 with `cc`; reservoir balance 1.2e-7 vs 8.6e-6).  The
bus balances (0.97M nonzeros), user constraints (0.42M) and converter
rows (0.81M) are identical for every rule.

Pending: the hourly power-based point week with trapezoid and with
lg4_pos (the trapezoid null-space test), Chebyshev 128, and the 1 h
week with 30 / 15 min ramp refinement.
