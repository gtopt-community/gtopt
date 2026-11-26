# cheby_small/mini — hand-checkable cases

Five tiny cases, each as `<case>_block.json` (24 or 168 hourly blocks)
and `<case>_point.json` (one point stage on the Chebyshev–Lobatto nodes,
N = 24 over 24 h or N = 48 over 168 h, Clenshaw–Curtis weights).  Built
by `scripts/tcheby2gtopt/mini_cases.py`: loads are analytic functions of
the stage time t [h]; block files hold their hourly cell means, point
files their values AT the node times.  The expected values are derived
in each README and re-checked by
`scripts/tcheby2gtopt/tests/test_cases_library.py`
(`expected_block_objective`, `expected_point_objective`).  These cases
are new (not from those test databases); they exercise the features the
tcheby systems need on point stages (storage, hydro, congestion,
unit commitment, periodic closure).

| Case | Block obj. | Point obj. |
|------|-----------:|-----------:|
| `battery_ramp` | 7260 (hand) | 7241.46825689373 (regression) |
| `hydro` | 27400 (hand) | 27400 (hand) |
| `congested_line` | 46000 (hand) | 46008.0745048467 (closed form) |
| `uc_minup_ramp` | 31500 (hand, MIP) | 33065.9545765050 (regression, MIP, backward ramps) |
| `periodic_week` | 168000 (hand) | 168000 (closed form) |

All use `scale_objective 1`, `demand_fail_cost 10000`, no discounting.
Bus duals in the output are $/MWh (divided by the block weight).
