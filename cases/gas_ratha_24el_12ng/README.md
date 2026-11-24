# gas_ratha_24el_12ng — IEEE 24-bus + 12-node meshed gas system

## Source and license

A. Ratha, A. Schwele, J. Kazempour, P. Pinson, S. Shariat Torbaghan and
A. Virag, *Affine policies for flexibility provision by natural gas
networks to power systems*, online appendix, Zenodo, 2020,
doi:10.5281/zenodo.3755120. License: **CC BY 4.0**
(<https://creativecommons.org/licenses/by/4.0/>).

The files in `data/` are the original, unmodified
`Model_Data/24el_12ng/*.csv`. `gas_ratha_24el_12ng.json` is a derived
work, also licensed CC BY 4.0, with attribution as above.

## Reduction (deterministic)

`make_case.py` builds the following model:

- expected wind only (`W_instcap × WF`), with 24 independent hours: no
  line pack, reserves, affine policies or chance constraints;
- Weymouth `p_i² − p_j² = q|q| / Kmu²` on every pipe. The gas loop
  4–5–6–8–7–4 makes the network meshed, so every pipe uses
  `weymouth_mode: linearized` (tangent);
- the gas SLP (`slp_iterations` 200, undamped tangent) converges to the
  exact equations;
- pipes with `Gamma > 1` (2 and 9) become a compressor into an internal
  node (ratio ≤ Γ, no bypass), followed by the pipe;
- gas-fired units use heat rate `ng_ConvEff` and cost `ng_C_OM`; other
  units use cost `C_1`. Lines use reactance `adm` and limit `f_max`;
- unserved gas and power are both priced at 1000.

## Expected values (`expected.json`)

| Reference | Objective (24 h) |
|-----------|------------------|
| scipy tangent SLP (independent, HiGHS) | 1 719 186.026 |
| scipy SLSQP on the exact NLP, started from the SLP point | 1 719 186.026 |

The polish does not change the cost (the difference is 4e-9). The SLP fixed
point is therefore a local optimum of the exact steady-state problem. The
SLP's largest Weymouth residual is 0.41 psi², about 0.001 psi in pressure.
It occurs on a pipe with no flow, where |f₀| is held at the `slp_min_flow`
floor. At the polished point the residual is 1.5e-10. The published objectives are
for the chance-constrained affine-policy model with line pack, so they
are not comparable with this deterministic reduction.
