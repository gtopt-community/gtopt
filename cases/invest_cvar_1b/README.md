# invest_cvar_1b — social CVaR planner on a screening curve

The `invest_screening_1b` screening-curve case (one bus, 8760 h in six
load blocks 1020 … 400 MW, value of lost load 10,000 \$/MWh, candidates
base / mid / peak) without nuclear, plus an existing 400-MW
zero-cost hydro unit whose availability depends on the hydrology
(Steiner 1957; Boiteux 1960; risk measure: Rockafellar & Uryasev 2000,
Shapiro, Dentcheva & Ruszczyński 2009).

| Scenario | p | Hydro availability | `risk_index` |
|----------|--:|-------------------:|-------------:|
| wet | 0.3 | 100 % (400 MW) | 3 |
| normal | 0.5 | 65 % (260 MW) | 2 |
| dry | 0.2 | 30 % (120 MW) | 1 |

Per-scenario availability is a `GeneratorProfile` indexed
`[scenario][stage][block]`.  Investment is first-stage (one plan for
all hydrologies).

| File | `model_options` |
|------|-----------------|
| `invest_cvar_expected.json` | risk neutral (default `risk_measure = expected`) |
| `invest_cvar_lp.json` | `cvar`, α = 0.8, λ = 0.5, tail ranked by system cost |
| `invest_cvar_worst.json` | `cvar`, α = 0.8, λ = 1 (pure CVaR) |
| `invest_cvar_dry.json` | `cvar`, α = 0.8, λ = 0.5, `risk_rank_by = index`, `risk_tail = lower` (driest 20 %) |
| `invest_cvar_trim_wet.json` | `trim`, α = 0.7, λ = 0.5, `risk_rank_by = index`, `risk_tail = upper` (drop the wettest 30 %) |
| `invest_cvar_expected_mip.json` | risk neutral, integer modules (base 100, mid 50, peak 50 MW) |
| `mip/invest_cvar_mip.json` | `cvar` α = 0.8, λ = 0.5 with the integer modules |

Run (HiGHS):

```bash
gtopt invest_cvar_lp.json --solver highs --output-directory out_lp
python -m gtopt_cost_recovery out_lp            # risk-adjusted (π̃)
python -m gtopt_cost_recovery out_lp --risk-weights probability
```

`risk_measure.csv` lists per scenario the cost O_s, η, excess u_s, the
CVaR-row dual μ_s, the risk-adjusted probability π̃_s = (1−λ)p_s + μ_s
and ζ_s = π̃_s / p_s.  Every dual of the run (bus prices, capacity
duals) is ζ_s times the scenario's own marginal value.

Results are in
[docs/methods/investment-economics.md](../../docs/methods/investment-economics.md)
§7.4; `golden/` holds the HiGHS outputs used by the
`gtopt_cost_recovery` tests, `output/` and `mip/output/` the e2e
expected CSVs.  The data are illustrative round numbers, not from any
operator.
