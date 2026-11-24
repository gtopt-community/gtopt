# gas_case6 — GasModels.jl case-6 (gas only)

## Source and license

Data from LANL GasModels.jl `test/data/matgas/case-6-no-power-limits.m`
(MICOT, LA-CC-13-108). License: **BSD-3-Clause**; the full text is in
`LICENSE-GasModels.md`. Only numeric data is used. No GasModels code is
vendored.

## Model

This is a six-node loop in SI units. Flows are in kg/s and pressures in
bar, so `weymouth_k = 16 λ L a² / (π² D⁵) / 1e10` in bar²/(kg/s)².
The settings are:

- pipes oriented as in the exact optimum: N5→N2, N2→N3, N6→N4, N4→N3;
- 64 tangents per pipe;
- compressors are boost-only, with ratio ≤ 2;
- demand bids are shedding costs, so
  `gtopt objective = Σ bid · w_max − welfare = 455 + GasModels objective`.

## Expected values (`expected.json`)

| Reference | GasModels objective | Receipt [kg/s] |
|-----------|---------------------|----------------|
| Published WP (exact Weymouth, `test/ogf.jl`) | −253.683 | 123.682 |
| Published LRWP (a looser relaxation) | −260.001 | 130.000 |
| scipy exact NLP (`make_case.py`) | −253.68219 | 123.68219 |
| Tangent LP (what gtopt builds) | −253.68624 | 123.68624 |

The tangent LP is within 0.004 (1.6e-5 relative) of the exact optimum. The
golden `obj_value` is 201.31376 (= 455 − 253.68624).

Regenerate with `PYTHONPATH=scripts python3 cases/gas_case6/make_case.py`.
