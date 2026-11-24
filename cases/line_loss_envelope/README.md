# line_loss_envelope

Three buses, one generator (`g1` at `b1`, 10 $/MWh) and two radial
lossy lines that share the same data (R = 1, V = 100, so
R/V² = 1e-4 MW/MW², K = 5 loss segments) and both have a **decoupled
loss envelope**: `loss_envelope = 100` MW against a flow cap
`tmax = 1000` MW (the shape plexos2gtopt emits for PLEXOS
`Enforce Limits = 0` lines).

| Line        | Mode                  | Load bus |
|-------------|-----------------------|----------|
| `l12_tsf`   | `tangent_signed_flow` | `b2`     |
| `l13_bidir` | `bidirectional`       | `b3`     |

Each load bus draws 50 / 300 / 800 MW in blocks 1 / 2 / 3, that is
0.5x, 3x and 8x the envelope.

What the goldens pin:

- The envelope never caps the flow. Every block is served in full (no
  `Demand/fail_sol` entries), and the flows reach about 305 MW and
  834 MW (tsf) and 310 MW and 879 MW (bidirectional). Before the fix,
  `tangent_signed_flow` stopped at exactly 100 MW and the rest of the
  load failed at 1000 $/MWh.
- Past the envelope the losses stay quadratic, as in PLEXOS. At
  834 MW, `R·f²/V²` = 69.6 MW and the LP gives 68.3 MW (tangent
  lower envelope, −1.9 %). For bidirectional at 879 MW,
  `R·f²/V²` = 77.3 MW and the LP gives 79.2 MW (secant, +2.5 %).
  Both are inside the ≈ 2.8 % bound of the geometric overflow ladder
  (see `docs/formulation/mathematical-formulation.md`, "Decoupled Loss
  Envelope").
- The load-bus prices carry the overflow-tier price
  (`loss_overflow_cost`, which defaults to the case's `demand_fail_cost`,
  here 1000 $/MWh), which every MWh of loss
  above R·env²/V² pays so that the overflow tier cannot act as a free
  energy sink under negative prices (see `cases/line_loss_overflow_sink`).
  At 0.5x rating the price is the loss-adjusted generator cost
  (10.08 / 10.10 $/MWh); at 3x it is 76.4 / 79.2 and at 8x 196.9 /
  219.8 $/MWh (10.6 / 10.7 and 11.6 / 12.1 $/MWh with
  `loss_overflow_cost: 0`). Flows and losses are
  the same as without the price (the network is radial), and no block
  hits the demand-fail cost.

The goldens were produced by gtopt itself (CPLEX), since pandapower DC
OPF has no line losses.
