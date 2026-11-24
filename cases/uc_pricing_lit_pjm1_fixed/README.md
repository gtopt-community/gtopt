# `uc_pricing_lit_pjm1_fixed` — PJM fast-start pricing Example 1, `pricing_mode = "fixed"`

PJM's three-bus integer-relaxation example: two flexible units and two
block-loaded 100 MW units, one congested line.  Solved with
[`fixed`](.) (PJM's "restricted LMP") and
[`fast_start`](../uc_pricing_lit_pjm1_fast_start/) (PJM's pricing run
with integer relaxation).  It is a mixed-integer problem.

## Source

PJM Interconnection, "Proposed Enhancements to Energy Price Formation",
15 Nov 2017, Appendix B "Integer Relaxation Model and Examples", Example
1 (Figure 10, Tables 2-4) — the proposal behind FERC docket EL18-34.
Originally published on pjm.com (special reports,
`20171115-proposed-enhancements-to-energy-price-formation.ashx`, since
moved); announcement:
<https://insidelines.pjm.com/pjm-proposes-enhancements-to-electric-energy-price-formation/>.

## System

Triangle `b1`-`b2`-`b3`, every reactance 0.1 p.u.; line `l13` limited to
50 MW, the others unconstrained.  Load 600 MW at `b1`, 100 MW at `b3`.

| Unit | Bus | Min / Max MW | Start-up $ | Cost |
|---|---|---|---|---|
| `g1` | b1 | 0 / 500 | — | 20 p + 0.05 p² (MC 20 + 0.1 p) |
| `g2` | b2 | 100 / 100 | 100 | 75 $/MWh |
| `g3` | b3 | 0 / 1000 | — | 20 p + 0.05 p² |
| `g4` | b3 | 100 / 100 | 100 | 40 $/MWh |

gtopt has no quadratic cost: `g1` / `g3` use a secant piecewise-linear
cost (unit-price fuel, `heat_rate_segments`) on a 5 MW grid with
breakpoints at 2.5 + 5k MW.  Every multiple of 5 MW is a segment
midpoint, where the secant slope equals the quadratic marginal cost;
the published operating points (475, 125, 150 MW) are such midpoints.
`g2` / `g4` have no min up / down time, so they are fast-start by the
auto rule.

## Results

MIP dispatch (published and gtopt): `g1` 475, `g2` 100, `g3` 125, `g4`
0 MW; line `l13` at its 50 MW limit.

| | b1 | b2 | b3 | `g2` uplift |
|---|---|---|---|---|
| Restricted LMP (Table 3) | 67.50 | 50.00 | 32.50 | 2600 |
| gtopt `fixed` | 67.5 | 50 | 32.5 | 2600 (make-whole) |
| Integer relaxation (Table 4) | 117.00 | 76.00 | 35.00 | — |
| gtopt `fast_start` | 117 | 76 | 35 | 0 |

In the pricing run `g2` becomes divisible at 75 + 100/100 = 76 $/MWh;
bus 1 is then 2·76 − 35 = 117 (one more MW at bus 1 needs 2 MW from bus
2 and −1 MW at bus 3 with `l13` binding).  PJM's Table 4 also shows 619
and 31 $ of uplift for the flexible `g1` / `g3`, from its settlement
model (profit maximisation subject to the system constraints); those
units carry no commitment, so `gtopt_pricing_uplift` does not report
them.  `relaxed` gives the same prices (`g2` / `g4` are the only
integers).  PJM Example 2 (negative restricted price at bus 2) is in
`test/source/test_pricing_literature.cpp`.

## Run

```bash
cd cases/uc_pricing_lit_pjm1_fixed
gtopt uc_pricing_lit_pjm1_fixed.json --output-directory output
gtopt_pricing_uplift output --planning uc_pricing_lit_pjm1_fixed.json
```
