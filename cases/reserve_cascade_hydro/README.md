# `reserve_cascade_hydro` — two-level cascade with per-level reserve options

`reserve_sddp_hydro` plus a `ctf` zone (150 MW, no zone `urcost`) served by
at most 50 MW, so 100 MW are short in every block and priced by
`model_options.reserve_shortage_cost`.  The cascade has two levels that
override that option (100, then 300 $/MW), so each level rebuilds its LP
from the cloned planning.  The final (level 2) objective must equal the
one-phase LP optimum with `reserve_shortage_cost = 300`; see
`test_cascade_reserve_extended.cpp` for the per-level check.
Registered as `e2e_reserve_cascade_hydro_sddp_*`.
