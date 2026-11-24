# gas_case6_meshed — GasModels case-6 with linearized pipes + gas SLP

This case is the same system as [`../gas_case6`](../gas_case6/README.md)
(GasModels.jl `case6`, BSD-3; see `../gas_case6/LICENSE-GasModels.md`).
The difference is that every pipe uses `weymouth_mode: linearized` with
`linearization: tangent`, so no pipe orientation is fixed in advance.
`options.gas_options` (`slp_iterations` 100, `slp_damping` 0.5) makes
gtopt re-linearize at each solution until the flows settle. The fixed
point satisfies the exact Weymouth equations.

`make_case.py` derives `gas_case6_meshed.json` from
`../gas_case6/gas_case6.json`.

| Reference | Welfare objective | gtopt objective (+455) |
|-----------|-------------------|------------------------|
| GasModels WP, published | −253.683 | — |
| exact NLP (scipy, `../gas_case6/expected.json`) | −253.68219 | 201.31781 |
| radial tangent LP (`../gas_case6`, 64 tangents) | −253.68624 | 201.31376 |

This case is expected to match the exact NLP objective to 1e-5. The
radial tangent form is an outer approximation, so it stays slightly
below.
