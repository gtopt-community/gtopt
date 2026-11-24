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
- The load-bus prices are the loss-adjusted generator cost
  (10.08 to 12.10 $/MWh), not the demand-fail cost.

The goldens were produced by gtopt itself (CPLEX), since pandapower DC
OPF has no line losses.
