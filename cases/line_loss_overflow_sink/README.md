# line_loss_overflow_sink

A KVL triangle where a congested line pushes an idle bus to a negative
price, with all generation costs positive. A lossy line at that bus has
a decoupled loss envelope with 10x headroom. The case pins that the
overflow-tier price (`loss_overflow_cost`, which defaults to the case's `demand_fail_cost`,
1000 $/MWh here) keeps the
line's loss physical instead of letting the overflow tier burn energy.

| Element | Data |
|---------|------|
| `g2` at `b2` | 50 $/MWh |
| `g3` at `b3` | 10 $/MWh |
| `d2` at `b2` | 300 / 240 MW in blocks 1 / 2 |
| `l12` (b1-b2) | lossless, X/V² = 1e-4, **tmax = 60 MW (binds)** |
| `l23` (b2-b3) | lossless, X/V² = 1e-4, tmax = 1000 MW |
| `l13` (b1-b3) | `tangent_signed_flow`, X/V² = 1e-4, R/V² = 1e-5, K = 6, `loss_envelope` = 100 MW, tmax = 1000 MW |

With equal reactances, an injection at `b1` puts 2/3 of it on `l12` and
an injection at `b3` puts 1/3. With `l12` binding and `g2`, `g3`
marginal, the price at `b1` is 50 − 2·(50 − 10) = **−30 $/MWh**. Every
MW that `l13` declares as loss (half withdrawn at `b1`, half at `b3`)
is then worth (30 − 10)/2 = 10 $/MWh to the LP, well above
`loss_cost_eps` = 0.1.

| | `l13` flow | `l13` loss | R·f²/V² | objective |
|-|-----------|------------|---------|-----------|
| guard on (default) | 60.05 MW | **0.10 MW** | 0.036 MW | 12 598.22 |
| guard off (`loss_overflow_cost: 0`) | 65 MW | **10 MW** | 0.042 MW | 12 404.00 |

The objectives include the `loss_flow_abs_cost` pin (0.001 $/MWh) on
the `l13` abs-flow proxy, which here sits at the envelope (100 MW, guard
on) or the flow cap (1000 MW, guard off), not at |f|: the 10 $/MWh
loss-dumping incentive beats both the pin and `loss_cost_eps`.  With
`loss_flow_abs_cost: 1e-6` (the former internal value) the objectives
are 12 598.02 and 12 402.00.

What the goldens pin (guard on):

- `b1` is priced at −30 $/MWh in both blocks (`Bus/balance_dual.csv`):
  the sink is saturated and the true price is visible.
- The `l13` loss is 0.1 MW = R·env²/V², the in-envelope residual that
  `loss_cost_eps` (or SOS2) governs. Without the overflow price the
  overflow tier burns R·tmax²/V² = 10 MW, about 240 times the physical
  loss of its 65 MW flow, and the dispatch shifts 15 MW from `g2` to `g3` to
  feed it.
- No demand fails.

The goldens were produced by gtopt itself (HiGHS 1.16; objective
12 598.22 with the default `loss_flow_abs_cost`). See
`docs/formulation/mathematical-formulation.md`, "Overflow-Tier Price".
