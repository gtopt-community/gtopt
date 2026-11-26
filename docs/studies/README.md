# Studies

Studies carried out with GTOpt, with the questions they answered, the
method and the main findings, and what each one changed in GTOpt.  Input
data of system operators are not copied here: each study links the
operator's own publications.

## Line losses in CEN's daily schedule (PCP), 2026

Study with the **Coordinador Eléctrico Nacional (CEN)**, Chile's system
operator, on the line-loss model of its daily scheduling program (PCP),
which CEN solves with PLEXOS.

**Question.**  Why do CEN's daily schedules show either *fictitious
losses* (the LP "burns" surplus energy as line losses) or *negative
marginal costs* (CMg), depending on the loss model, and which loss
formulation gives physical losses and correct prices?

**Data and method.**

- The PCP input and solution bundles of 2021 (piecewise-linear losses) and
  2025-10 to 2026-11 (single-pass generalized power flow, GPF).
- The PLEXOS solutions were read directly: hourly CMg per bus, and the
  loss of each line compared with its physical loss $`R F^2`$ and with
  the secant approximation of its loss tranches.
- The same cases were converted with
  [`plexos2gtopt`](../scripts/plexos2gtopt.md) and solved with GTOpt under
  several loss models.  The runs remove one source of negative CMg at a
  time: gas take-or-pay, battery reserves, battery operation, angular
  (KVL) coupling.

**Findings.**

1. **Piecewise-linear losses create fictitious losses.**  When energy is
   in surplus (CMg = 0) the losses cost nothing, so the LP uses them as a
   sink.  It fills expensive tranches or circulates flow in both
   directions.  In the 2021 cases, PLEXOS losses in those hours were about
   5 times the secant value: up to ~1.8 GW of fictitious losses, 4.9 % and
   8.6 % of the day's demand.  Some lines report more loss than flow.
   Prices stay at exactly 0 instead of going negative.
2. **Single-pass GPF removes the fictitious losses but not the surplus.**
   The losses are fixed from the first pass, so they cannot be inflated.
   The surplus has no sink left and negative CMg appear: slightly negative
   system-wide in solar hours, by zones in the far north, or strongly
   negative in a congested pocket (about −132 $/MWh, 2026-05-25).
3. **The dilemma is in the loss model, not in the data:** fictitious
   losses without negative prices, or negative prices without fictitious
   losses.  With a convex loss model that cannot be inflated, negative
   prices are a real signal of surplus and congestion.
4. **Operator-specific constraints also produce negative CMg.**  Two
   examples are the battery ancillary-services (SSCC) constraints with
   auxiliary batteries, and line ratings capped at a value the operator
   never enforces.  See
   [`sscc_battery_constraint.md`](sscc_battery_constraint.md) for the
   battery constraints and their native replacement.

**What changed in GTOpt.**

- Line losses use the `tangent_signed_flow` outer approximation with a
  small loss price `loss_cost_eps`, which blocks phantom bidirectional
  flow and loss inflation.  The output checker detects any hour where the
  guard is invalidated.  See the formulation
  ([`mathematical-formulation.md`](../formulation/mathematical-formulation.md),
  "Loss Pricing ε and the Negative-LMP Arbitrage Guard").
- `plexos2gtopt` converts to the clean formulation by default; the
  operator-faithful choices are reproduced only with
  `--legacy-profile cen`.
- Never-enforced line ratings become hard caps, with a curated list of
  lines lifted to a soft cap (`--el0-lines`, `--lift-line-caps`).
- A native GTOpt reserve formulation for batteries replaces the
  auxiliary-battery constraints
  ([`sscc_battery_constraint.md`](sscc_battery_constraint.md)).

**Sources.**  CEN, Programa de Coordinación de la Operación (PCP), model
bases and daily programs: <https://www.coordinador.cl>.
