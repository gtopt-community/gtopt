# `pm_case5_asym` — PowerModels case5_asym, DC vs DCPLL

Literature benchmark for the line-loss model and
`line_thermal_limit_mode` against
[PowerModels.jl](https://github.com/lanl-ansi/PowerModels.jl)
(C. Coffrin, R. Bent, K. Sundar, Y. Ng, M. Lubin, "PowerModels.jl: An
Open-Source Framework for Exploring Power Flow Formulations", PSCC 2018,
DOI [10.23919/PSCC.2018.8442948](https://doi.org/10.23919/PSCC.2018.8442948)).

## Source and licence

`source/case5_asym.m` is `test/data/matpower/case5_asym.m` of
PowerModels.jl, unchanged (MATPOWER format; BSD-3-Clause, Copyright (c)
2016 Los Alamos National Security, LLC; see `THIRD_PARTY_NOTICES.md`).
It is the MATPOWER `case5` system (5 buses, 6 branches, 5 generators,
1000 MW of load) with asymmetric branch angle-difference limits.
PowerModels' own `test/opf.jl` pins its objectives:
`DCPPowerModel` 17479 and `DCPLLPowerModel` 17693 (atol 1).

## Conversion

The two JSON files are written by hand from the `.m` file (5 buses, so
no converter is needed; `pp2gtopt` on master derives `tmax` from a
current rating and drops the branch resistance):

| MATPOWER | gtopt |
|----------|-------|
| `bus` `Pd` | `Demand.lmax` [MW] at that bus |
| `gen` `Pmax`, `Pmin` | `Generator.pmax`/`capacity`, `pmin` |
| `gencost` (type 2, `c2 = 0`) | `Generator.gcost` = `c1` [$/MWh] |
| `branch` `x` (p.u.) | `Line.reactance` [p.u.] (lossless file), `x·Zbase` [Ω] with `voltage = 230` kV (lossy file) |
| `branch` `r` (p.u.) | `Line.resistance` = `r·Zbase` [Ω], `Zbase = 230²/100 = 529 Ω` |
| `branch` `rateA` | `Line.tmax_ab` = `tmax_ba` [MW] |
| `branch` `b`, `Qd`, `Vm` limits | not used by a DC model |
| `branch` `angmin`/`angmax` | not modelled by gtopt (widened, see below) |

All costs are linear (`c2 = 0` in every `gencost` row), so no
quadratic-cost approximation is involved.  With `voltage` set gtopt
reads both `R` and `X` in Ω; the loss is `R·f²/V² = r_pu·f²/100`, the
same as PowerModels' `r·p_fr²` in p.u.  PowerModels writes Ohm's law
with the susceptance `x/(r² + x²)` instead of `1/x`; every branch of
this case has `r/x = 0.1`, so the two differ by a common factor and the
PTDFs are identical.

**Angle limits.** gtopt does not model `angmin`/`angmax`, so the
reference is re-solved with every limit widened to ±30°.  The widened
limits never bind (largest |Δθ| at the optimum: 4.12° DC, 4.10° DCPLL).
The original asymmetric limits do not bind in the DC models either:
the widened objectives equal the original ones.

## Reference values

Produced by `tools/powermodels_solve_golden.jl` (env spec
`tools/powermodels_goldens/Project.toml`); golden:
`tools/powermodels_goldens/case5_asym_wide30.json`.

```bash
julia --project=tools/powermodels_goldens -e 'using Pkg; Pkg.instantiate()'
julia --project=tools/powermodels_goldens tools/powermodels_solve_golden.jl \
    cases/pm_case5_asym/source/case5_asym.m \
    tools/powermodels_goldens/case5_asym_wide30.json 30
```

Julia 1.13.4, PowerModels 0.21.7, JuMP 1.32.1, Ipopt.jl 1.16.0,
HiGHS.jl 1.27.0:

| Model | Solver | Objective [$/h] |
|-------|--------|-----------------|
| `DCPPowerModel` | HiGHS (LP) | 17479.896925381032 |
| `DCPPowerModel` | Ipopt (tol 1e-10) | 17479.896769413448 |
| `DCPLLPowerModel` | Ipopt (tol 1e-10) | 17693.807403882074 |

**DCPLL caveat.** DCPLL (Coffrin, Van Hentenryck, Bent, PESGM 2012,
DOI [10.1109/PESGM.2012.6345342](https://doi.org/10.1109/PESGM.2012.6345342))
writes the loss as the convex relaxation `p_fr + p_to ≥ r·p_fr²`
(`constraint_ohms_yt_to`, `src/form/dcp.jl`), not as an equality.  It
is exact only where the relaxation is tight.  At this optimum every
branch has `p_fr + p_to = r·p_fr²` to solver precision (golden fields
`loss_mw` and `r_pf2_mw`), and all prices are positive.  DCPLL keeps
Ohm's law on the from-end power `p_fr`, books the loss at the to-end and
bounds both `|p_fr|` and `|p_to|` by `rateA`.  The sending end binds.

## Variants

| File | Model | gtopt objective (HiGHS) | Reference | Rel. gap |
|------|-------|------------------------|-----------|----------|
| `case5_asym_dc.json` | `line_losses_mode = none` | 17479.896925381 | DCP 17479.896925381 | 4e-16 |
| `case5_asym_dcpll_sending.json` | `piecewise`, 8 segments, `line_thermal_limit_mode = sending` | 17695.754485 | DCPLL 17693.807404 | 1.1e-4 |

In the lossy file every line carries the DCPLL loss side as its
`loss_allocation_mode`: `receiver` on branches loaded from→to in the
reference (1–2, 1–4) and `sender` on branches loaded to→from (1–5, 2–3,
3–4, 4–5).  In both cases the gtopt flow variable is the from-end power
`p_fr`, as in DCPLL's Ohm's law.  With `sending` the limit is the
sending-end power `f + a·ℓ`, which is DCPLL's two-end bound.  Branch
4–5 is congested at its sending end: `|f| + ℓ = 238.312 + 1.688 =
240 MW = rateA` (DCPLL: `p_fr = −238.313`, `p_to = 240`).

Piecewise segments K against the DCPLL objective (HiGHS):

| K | 1 | 2 | 4 | 8 | 16 | 32 | 64 |
|---|---|---|---|---|----|----|----|
| objective | 17796.79 | 17713.71 | 17701.71 | 17695.75 | 17694.04 | 17693.91 | 17693.84 |
| rel. gap | 5.8e-3 | 1.1e-3 | 4.5e-4 | 1.1e-4 | 1.3e-5 | 5.6e-6 | 2.0e-6 |

The secant PWL over-estimates `r·f²` between breakpoints, so the gap is
positive and shrinks as K grows.  Thermal-limit modes at K = 8:
`flow` 17592.24 ≤ `sending` 17695.75 = `full` 17695.75.  `sending` and
`full` agree here because the only binding line (4–5) has `sender`
allocation (`a = 1`).

## Tests

- `e2e_pm_case5_asym_dc_*`, `e2e_pm_case5_asym_dcpll_sending_*`
  (`integration_test/CMakeLists.txt`, `add_expected_values_case`):
  objectives and selected flows from `expected.json`.  DC uses rtol
  1e-6.  DCPLL uses rtol 2e-4, which is the 1.1e-4 measured gap at K = 8
  with margin.
- `test/source/test_pm_case5_asym.cpp`: the K sweep (monotone,
  2e-4 at K = 8, 1e-5 at K = 32) and `full ≥ sending ≥ flow` with the
  DCPLL allocation and with the default `receiver` allocation.

## Running

```bash
cd cases/pm_case5_asym
gtopt case5_asym_dcpll_sending.json --set output_directory=results
```
