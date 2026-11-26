# mini/congested_line — 2 buses, 1 congested line

b1: `cheap` 10 $/MWh (200 MW).  b2: `dear` 50 $/MWh (200 MW) and the
load `d(t) = 50 + 5t` MW (50 → 170).  Line b1–b2: 100 MW both ways,
X 0.1 p.u., Kirchhoff on.  No inter-temporal coupling, so the optimum is
`Σ_k w_k [10 · min(d_k, 100) + 50 · max(d_k − 100, 0)]`
(`congested_line_cost` in `mini_cases.py`).

Block (hourly `d_h = 52.5 + 5h`): congestion from hour 10 on (the
crossing t = 10 is an hour edge); `dear` serves
`Σ_{h=10}^{23} (5h − 47.5) = 490 MWh`, `cheap` 2150 MWh:
`10 · 2150 + 50 · 490 = ` **46000**.

Point (N = 24, `d_k = 50 + 5 t_k`): the closed form above on the node
weights gives **46008.0745048467** (gtopt: 46008.0745048328).  The kink
of `min(d, 100)` is not a polynomial, so the CC sum is not exact
(+8.07 vs the continuous 46000).
Duals: b1 10 $/MWh; b2 10 $/MWh while uncongested (t < 10), 50 $/MWh
when congested (line rent 40 $/MWh).

Tolerance: 1e-6 relative.
