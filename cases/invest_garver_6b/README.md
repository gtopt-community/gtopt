# invest_garver_6b — Garver 6-bus transmission expansion

The 6-bus system of L. L. Garver, "Transmission network estimation using
linear programming," IEEE Trans. PAS 89(7), 1970,
[doi:10.1109/TPAS.1970.292825](https://doi.org/10.1109/TPAS.1970.292825),
with the corridor data (reactance, limit, cost) of R. Romero et al.,
"Test systems and mathematical models for transmission network expansion
planning," IEE Proc. GTD 149(1), 2002,
[doi:10.1049/ip-gtd:20020026](https://doi.org/10.1049/ip-gtd:20020026).
Solved as the transport model (`use_kirchhoff: false`).  See
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§3 and §5.

Changes from the static benchmark, to make prices meaningful: the
generators get variable costs (bus 1 150 MW at 40 \$/MWh, bus 3 360 MW at
30, bus 6 600 MW at 15), the 760 MW load (80/240/40/160/240 MW at buses
1–5) is scaled to 100 / 80 / 60 % over 1000 / 4000 / 3760 h, each
corridor can add up to 4 circuits (`expcap` = circuit limit, `expmod`
≤ 4), and the circuit cost (M\$) is annualised at 8 % over 40 years per
MW of limit.  Unserved energy costs 10,000 \$/MWh; no plan sheds load.

| File | Variant |
|------|---------|
| `invest_garver_lp.json` | continuous circuits |
| `invest_garver_mip.json` | whole circuits (`integer_expmod`) |

Results (HiGHS 1.16): the LP expands 2–6 (+400 MW), 4–6 (+200), 3–5
(+24) and 2–5 (+16); the congestion rent on the new MW equals their
annuity (objective 92,391,705 \$).  Corridors not expanded are worth no
more than their annuity per MW.  The MIP builds one circuit on 1–5 and
three on 2–6 and 4–6; make-whole uplift 7,772,032 \$ at fixed-integer
prices, 613,857 \$ at relaxed prices.

```bash
gtopt invest_garver_lp.json --solver highs \
  --output-directory out_lp --write-out all
gtopt invest_garver_mip.json --solver highs \
  --output-directory out_mip --write-out all
gtopt invest_garver_mip.json --solver highs \
  --output-directory out_mip_relaxed --write-out all \
  --set model_options.pricing_mode=relaxed
gtopt_cost_recovery out_lp out_mip out_mip_relaxed
```
