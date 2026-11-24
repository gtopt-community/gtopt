# gas_elevation_chain — pipe elevation term

This is a three-node chain S (0 m) → M (2000 m) → T (4000 m). The total
rise is similar to Paso de Jama. S is fixed at 70 bar and supplies a
60 km³/h load at T. The two pipes are each 100 km long with a 406 mm
diameter.

Each pipe follows `π_a − e^s π_b = K_e f|f|`, where
`s = 2 g M Δh / (Z R T)` ≈ 0.3162 per 2000 m climb (G = 0.6, T = 288.15 K,
Z = 0.9), and `K_e = K (e^s − 1)/s`.

Each pipe has one tangent at `t = f` (`fmax = 2 f`), so the LP pressures
equal the closed form `π_b = (π_a − K_e f²) / e^s`. `expected.json` holds
these values: p_M = 56.747 bar and p_T = 44.673 bar. On flat ground
p_T would be 63.87 bar.

`make_case.py` regenerates both files.
