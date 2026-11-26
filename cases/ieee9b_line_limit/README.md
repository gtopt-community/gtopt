# ieee9b_line_limit

`line_thermal_limit_mode` on a literature network, checked against an
AC optimal power flow.

## Source network

The WSCC 9-bus system as distributed with MATPOWER (`case9`,
Zimmerman et al. 2011, DOI
[10.1109/TPWRS.2010.2051168](https://doi.org/10.1109/TPWRS.2010.2051168))
and pandapower (`pandapower.networks.case9()`, Thurner et al. 2018,
DOI [10.1109/TPWRS.2018.2829021](https://doi.org/10.1109/TPWRS.2018.2829021)).
All lines are 345 kV; six of them have resistance.

`make_case.py` makes three changes so that a lossy line congests and the
two models price the same thing:

1. Linear generator costs (the `cp1` terms 5, 1.2 and 1 $/MWh; the
   quadratic and constant terms are dropped), since gtopt prices energy
   linearly.
2. Line 5–6 (R = 46.4 Ω, X = 202.3 Ω, rating 150 MVA) gets
   `max_loading_percent = 80`, i.e. a 120 MVA limit.  The cheap unit at
   bus 3 exports through bus 6, and this line binds.
3. That line has no shunt charging (`c_nf_per_km = 0`).  A DC model has
   none.  With the stock charging current the AC limit binds at the
   receiving end instead (see the sensitivity row below).

`pp2gtopt --line-losses-mode tangent_signed_flow` converts it.  With a
loss mode the converter writes every branch in physical units
(`voltage` in kV, `reactance` / `resistance` in Ω), so the gtopt loss
`R·f²/V²` is in MW.  `loss_segments = 8`, `loss_cost_eps = 0` (all
prices are positive, so the arbitrage guard is not needed and would
bias the LMPs).  `tangent_signed_flow` books half of the loss at each
end of the line, so the flow variable is the mid-line flow; the
sending-end power is `f + ℓ/2`.

| Directory | `line_thermal_limit_mode` | Limited quantity |
|-----------|---------------------------|------------------|
| `flow/` | `flow` | f ≤ 120 |
| `sending/` | `sending` | f + ℓ/2 ≤ 120 (sending-end power) |
| `full/` | `full` | f + ℓ ≤ 120 |

Regenerate with `python make_case.py` (needs pandapower; it also prints
the AC-OPF reference).  CTest: `ieee9b_line_limit_<mode>` (goldens from
HiGHS).  `scripts/pp2gtopt/tests/test_thermal_limit_acopf.py` re-runs
the comparison when pandapower and a gtopt binary are available.

## Results

Line 5–6 carries power from bus 6 to bus 5 and is at its limit in every
run.  gtopt: HiGHS 1.16.  AC: pandapower 3.5 `runopp` (PIPS), voltage
band 0.9–1.1 pu.

| | P sending [MW] | P receiving [MW] | Loss 5–6 [MW] | Total loss [MW] | LMP bus 6 | LMP bus 5 | Cost [$/h] | Limit dual |
|-|----|----|----|----|----|----|----|----|
| AC-OPF | 124.59 | 118.98 | 5.62 | 10.27 | 1.001 | 1.611 | 378.30 | (current at 80 %) |
| gtopt `flow` | 122.76 | 117.24 | 5.53 | 9.73 | 1.000 | 1.598 | 381.08 | `flow_cost` 0.665 |
| gtopt `sending` | **120.00** | 114.69 | 5.31 | 9.45 | 1.000 | 1.598 | 382.85 | `thermaln_dual` −0.639 |
| gtopt `full` | 117.45 | 112.34 | 5.11 | 9.20 | 1.000 | 1.598 | 384.48 | `thermaln_dual` −0.615 |
| AC-OPF, stock line charging | 125.02 | 119.57 | 5.46 | 9.99 | 1.000 | 1.616 | 377.87 | |

The rating is 120 MW (√3 · 345 kV · 0.251 kA · 80 %).  The flow runs
B→A on the line (`bus_a` = 5), hence the `n` duals.

Reading the table:

- **What the AC limit is.**  Without shunt charging the current is the
  same at both ends, and the AC-OPF holds it at exactly 80 %:
  √3 · V_nom · I = 120.0 MVA at both ends.  In a DC model (V = 1 pu,
  no reactive power) that current is the sending-end power, because
  P_send = V_send · I and the loss is the drop along the line.  So the
  DC image of the AC limit is P_send ≤ 120, which is `sending`.
- **`flow`** limits the mid-line flow of `tangent_signed_flow`, so the
  sending end carries f + ℓ/2 = 122.76 MW, 2.3 % above the rating:
  half the loss is outside the limit.  **`full`** keeps the whole loss
  inside it and stops 2.55 MW short.  Under the default `receiver`
  allocation (`bidirectional`, `piecewise`) the flow variable already
  is the sending-end power, `flow` and `sending` coincide (the pytest
  checks this), and only `full` differs.
- **Why the AC MW are higher.**  The AC-OPF raises the sending-bus
  voltage to 1.077 pu, so the same current carries more power
  (√3 · V · I · cos φ).  No DC mode has voltage magnitudes, so none
  matches the AC megawatts; the AC cost (378.30 $/h) is below all three
  gtopt runs for that reason.  The congested line loses 5.11–5.53 MW in
  gtopt against 5.62 MW in AC, and the system 9.2–9.7 MW against
  10.27 MW (AC also has the I²R of the reactive flows).
- **LMPs.**  Units 2 and 3 are marginal in every run, so the gtopt LMPs
  do not move with the mode (1.000 / 1.598 $/MWh); the AC values are
  1.001 / 1.611.  What moves is the dispatch (unit 3 exports 233 →
  222 → 213 MW) and the limit dual.
- **Stock charging.**  With the line's shunt capacitance the charging
  current makes the receiving-end current the larger one (120.0 vs
  115.9 MVA-equivalent); the AC limit then binds at the receiving end,
  which no DC mode represents.  That is why the case removes it.

## References

- R. D. Zimmerman, C. E. Murillo-Sánchez, R. J. Thomas, "MATPOWER:
  Steady-State Operations, Planning, and Analysis Tools for Power
  Systems Research and Education", IEEE Trans. Power Syst. 26(1),
  12–19, 2011. DOI 10.1109/TPWRS.2010.2051168.
- L. Thurner et al., "pandapower — An Open-Source Python Tool for
  Convenient Modeling, Analysis, and Optimization of Electric Power
  Systems", IEEE Trans. Power Syst. 33(6), 6510–6521, 2018.
  DOI 10.1109/TPWRS.2018.2829021.
- C. Coffrin, P. Van Hentenryck, "A Linear-Programming Approximation of
  AC Power Flows", INFORMS J. Computing 26(4), 2014 (the
  `tangent_signed_flow` loss model).
- Formulation: `docs/formulation/mathematical-formulation.md`, "Thermal
  Limit with Losses (`line_thermal_limit_mode`)".
