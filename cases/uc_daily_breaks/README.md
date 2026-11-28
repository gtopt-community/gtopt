# uc_daily_breaks — daily (non-uniform) commitment grid on a Chebyshev week

A typical week (168 h, Wednesday 12:00 → Wednesday 12:00,
`Stage.start_hour = 12`) point-sampled on the 96 periodic
Chebyshev–Lobatto nodes (node N ≡ node 0 dropped, Clenshaw–Curtis
weights), with the study's daily commitment grid as the planning
default:

```json
"model_options": {"commitment_breaks": [3.5, 7.5, 10.5, 14.5, 17.5, 23.5]}
```

→ cells 23:30–03:30 (4 h), 03:30–07:30 (4), 07:30–10:30 sunrise (3),
10:30–14:30 midday (4), 14:30–17:30 sunset (3) and the peak 17:30–23:30
(6 h).

* Load (a function of the clock hour): 140 MW on [18, 23), 90 MW on
  [8, 11), 60 MW otherwise.
* `base` 20 \$/MWh ≤ 70 MW, `peaker` 35 \$/MWh ≤ 100 MW (committed:
  pmin 30, no-load 400 \$/h, start 900 \$, MUT 5 h, MDT 4 h, cyclic over
  the week), `backup` 120 \$/MWh.

The peaker's status lives on the daily cells (one u per cell holding a
node; the 10:30–14:30 cell straddling the week start is one cell), not
on the nodes; v / w only at cell boundaries.  The goldens in `output/`
were produced with HiGHS (MIP, objective 389789.657): the peaker
starts once a day, at the peak cell.  Nodes, weights and loads come from
`TimeGrid("point", [168.0], nodes=96, periodic=True)` of
`scripts/tcheby2gtopt/timegrid.py` (load evaluated at the node clock
times `(12 + t_k) mod 24`).

See [docs/methods/typical-week-sampling.md](../../docs/methods/typical-week-sampling.md)
§4c.
