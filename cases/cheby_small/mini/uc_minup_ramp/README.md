# mini/uc_minup_ramp — one unit with minimum up time and ramps (MIP)

1 bus, 24 h.  Load 80 MW on [6, 18) and [20, 22), 0 otherwise.  g1:
10 $/MWh, 100 MW, commitment `pmin 40`, `min_up_time 6`,
`min_down_time 2`, `ramp_up = ramp_down = 20 MW/h`, `startup_ramp 40`
and `shutdown_ramp 100` set explicitly (both default to pmax, which
would make the ramp rows loose), `startup_cost 500`, initially off.
g2: 100 $/MWh.  `relax: false` (MIP).

Block, by hand: g1 starts at hour 6 (≤ startup_ramp 40), ramps to 60
at hour 7 and 80 from hour 8 to 17, and shuts down at 18 (80 ≤
shutdown_ramp).  g2 covers 40 + 20 MWh.  The load at hours 20–21 can
not use g1: a start would keep it on for 6 h (min up), i.e. at ≥ 40 MW
in hours 22–23 where the load is 0 (no free disposal).  g2 serves
those 160 MWh.  `10 · 900 + 500 + 100 · (60 + 160) =` **31500**.
(Without min up: start at 20 → 7500 for that window instead of 16000.)

Point (N = 24; the load is evaluated at the node instants, a node on a
jump (t = 6, 18) takes the value after it): **33065.9545765050**
(regression, HiGHS).  Min up counts the quadrature-cell
durations, ramps are the interval slopes `P_k − P_{k−1} ≤ RU·h_k + SU·v_k`
with `h_k = t_k − t_{k−1}` (the default `backward` formula), costs use
the CC weights; no closed form.  The earlier value 31031.83 came from the
`central2` ramp, whose central difference lets a zig-zag dispatch
through (now opt-in, not recommended).

Tolerance: 1e-6 relative (HiGHS default MIP gap reaches the optimum
here; use `--mip-gap 1e-9` to be safe).
