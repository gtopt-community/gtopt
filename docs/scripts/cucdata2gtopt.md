# cucdata2gtopt — clustered-UC benchmark (Morales-España & Tejada-Arango) → gtopt

Converts the case studies of

> G. Morales-España and D. A. Tejada-Arango, "Modeling the hidden
> flexibility of clustered unit commitment", *IEEE Trans. Power Syst.*
> 34(4):3294–3296, 2019, doi:10.1109/TPWRS.2019.2908051
> (preprint [arXiv:1811.02622](https://arxiv.org/abs/1811.02622))

into gtopt cases of the **individual-unit unit commitment (IUC)** of the
paper's Table I: a modified IEEE 39-bus system (30 buses, 41 lines,
90 thermal units in 9 clusters of 10) and a modified IEEE 118-bus system
(118 buses, 186 circuits, 540 units in 54 clusters of 10, three wind
buses), 24 hourly periods, DC network, up/down secondary reserve.

The data and the GAMS model that defines its semantics
(`StarGenLite_Clustered-UC.gms`) are published at
[datejada/CUC-data](https://github.com/datejada/CUC-data) **without a
licence**, so neither the data nor any case derived from it is committed
to gtopt or gtopt-cases: the converter downloads the files from a pinned
commit into a local cache on demand.

---

## Quick start

```bash
cucdata2gtopt --fetch-only                          # prime the cache (5 files)
cucdata2gtopt --system 39 --reserve 10 -o cuc/      # -> cuc/cuc39_r10_s10.json
cucdata2gtopt --system 39 --reserve 5  -o cuc/
cucdata2gtopt --system 118 --reserve 5   -o cuc/
cucdata2gtopt --system 118 --reserve 2.5 -o cuc/

cd cuc && gtopt cuc39_r10_s10.json --solver cplex --mip-gap 1e-4
```

`python -m cucdata2gtopt …` works too (run from `scripts/`).  The
objective (`output/solution.csv`, `obj_value`) is in EUR; the paper's
Table I reports the same quantity in millions.

### Options

| Option | Default | Meaning |
|--------|---------|---------|
| `--system {39,118}` | `39` | test system |
| `--reserve PCT` | workbook (39: 10, 118: 2.5) | up **and** down reserve requirement, % of the hourly demand |
| `--scale N` | `10` | identical units per cluster; demand, reserve, wind and line limits scale by `N/10`.  `10` is the published (paper) system, which is already scaled ×10 |
| `--formulation {paper,native,power_based}` | `paper` | see [Mapping](#mapping) |
| `--mode {chronological,block}` | `chronological` | `block`: the same 24 hours as non-chronological blocks, see [Block mode](#block-mode) |
| `--name NAME` | `cuc<sys>_r<pct>_s<N>` | case name (`.` → `p`, e.g. `r2p5`) |
| `-o DIR` | `.` | output directory (`DIR/<name>.json`, self-contained) |
| `--input XLSX` | — | read this workbook instead of the cache |
| `--cache-dir DIR` | `$GTOPT_CUCDATA_DIR` or `~/.cache/gtopt/cucdata` | download cache |
| `--fetch-only` | — | download every file of the manifest and exit |

`GTOPT_OFFLINE=1` forbids downloads (a missing file then exits with
code 2).  The CLI is kept stable for the gtopt-cases prep script that
builds these cases.

### Data provenance

Upstream `datejada/CUC-data`, commit
`624abbbdc9f83095c02b34174e52c76f77ddfa36` (data files last changed on
2018-10-24, GAMS code added 2022-05-22).  SHA-256 of every file is
checked on download (`scripts/cucdata2gtopt/_source.py`):

| File | SHA-256 |
|------|---------|
| `CUC_IEEE39-busSystem.xlsx` | `665c1cdb…f89514` |
| `CUC_IEEE118-busSystem.xlsx` | `f4e87590…f2a3` |
| `StarGenLite_Clustered-UC.gms` | `63aff376…bbe` |
| `ClusteredUC-Addendum.pdf` | `514e9973…e42` |
| `README.md` | `137bf6c0…5fd48d` |

---

## Mapping

The converter transcribes the *first* solve of the GAMS file (`ta = t`,
the individual units) and its pre-processing (`SET VALUE VARIABLES`):

| GAMS | gtopt |
|------|-------|
| bus `i`, slack `pSlack = 1` | `Bus`, bus 1 `reference_theta = 0` |
| circuit `iic`, PTDF flows `eCirPF`, `±Pmax` | `Line` (`reactance = X`, `tmax_ab = tmax_ba = Pmax`), DC Kirchhoff, no losses — the same feasible set as the PTDF form |
| `pDemand(p)·pBusProfile(i)`, `vENS ≤` load, `pENSCost` | one `Demand` per loaded bus (`lmax` per hour, `fcost = pENSCost`) |
| `pIntermGen`, `pWindCurtCost·(IG − vIG)` | `Generator` + `CapacityProfile` (spillover cost = `pWindCurtCost`); only the scenario with `pScenProb > 0` |
| `pVariableCost = OMVarCost + SlopeVarCost·FuelCost` on `p1 + Pmin·u` | `Generator.gcost`, `pmin = 0` |
| `pMinProd`, `pFixCost = InterVarCost·FuelCost` | `Commitment.pmin`, `noload_cost` |
| `pStartupCost = SUcost1·Fuel + SUduration·Fix + SUduration·Pmin·Var/2` (shutdown alike) | `startup_cost`, `shutdown_cost` |
| `pMinTU`, `pMinTD` (0 → 1); window `ord(pp) ≥ ord(p)+1−TU` | `min_up_time = ⌊TU⌋`, `min_down_time = ⌊TD⌋` (the GAMS window holds ⌊TU⌋ periods; 118-bus has TU = 2.75, 1.5, …) |
| `pIniUC = [IniProd ≥ Pmin]`, `vCommit.fx` for `ord(p) ≤ pIniUp + pIniDw` | `initial_status`, `initial_power`; the pinned periods go to `fixed_status` (gtopt does not carry remaining min up/down across t = 0) |
| `e2ReserveUp/Dw` (hard) | one `ReserveZone` (`urreq`/`drreq`, no shortage cost = hard) |
| `v2ndResUP/DW ≤ min(RU+RD, Pmax−Pmin)`, cost `0.4/0.2 · pFixCost` | one `ReserveProvision` per unit (`urmax`/`drmax`, `urcost = 0.4·Fix·10⁻³`, `drcost = 0.2·Fix·10⁻³` EUR/MW: the GAMS term `pFixCost[M€/h]·v[GW]` carries one extra 10⁻³ versus the energy terms) |
| `pSUCap`, `pSDCap` clamped to `[Pmin, Pmax]`; `pRampUp/Dw ≤ Pmax − Pmin` | clamped values used below |
| `eMaxOut` (TU ≠ 1) / `eMaxOut2`+`eMaxOut3` (TU = 1): `p1 + r⁺ ≤ (P−Pmin)u − (P−SU)y − (P−SD)z_{t+1}` | `UserConstraint` rows (`<unit>_maxout*`), `z_{t+1}` via the `_prev` lag on blocks 2..T plus a last-block row |
| `eMinOutput`: `p1 − r⁻ ≥ 0` | `UserConstraint` `<unit>_minout` (`g − r⁻ ≥ Pmin·u`) |
| `eRampUp`: `p1_t − p1_{t−1} + r⁺_t ≤ RU·u_t` (initial output at t = 1) | `UserConstraint` `<unit>_rampup` / `_rampup_first` |
| `eRampDw`: `p1_t − p1_{t−1} − r⁻_t ≥ −RD·u_{t−1}` | `UserConstraint` `<unit>_rampdn` / `_rampdn_first` |

`--formulation native` drops the user constraints and sets the built-in
`Commitment.ramp_up/ramp_down/startup_ramp/shutdown_ramp` instead.
gtopt's native ramp rows act on the total output and do not contain the
reserves, so `native` is a relaxation of the paper model (reserve may be
offered in start-up / shut-down hours and beyond the ramp capability).

`--formulation power_based` writes the same native fields under
`model_options.uc_formulation = "power_based"` ([Unit Commitment
§4.6](../unit-commitment.md#46-power-based-formulation-uc_formulation--power_based)):
power at the hour boundaries, trapezoid hourly energies, and the reserves
inside the capacity and ramp rows on the instantaneous power.  The
workbook's initial output is an hour average, so the power-based case
leaves `initial_power` unknown (the power at t = 0 is free in
[pmin, pmax] for a unit that is on).  This is a different model from the
paper's IUC, which is an hourly energy-block formulation; it is not
expected to reproduce Table I.

## Validation

**gtopt reproduces the published GAMS model.**  An independent
transcription of `StarGenLite_Clustered-UC.gms` (PTDF network, the GAMS
equations verbatim, written as a CPLEX LP file from the same workbook)
solved with CPLEX 22.1.1 reaches the same incumbents as gtopt to the
cent: 980 822.80 EUR (39-bus, 10 % reserve) and 977 185.55 EUR (5 %).
Objective breakdown of the 10 % case: variable 866 184, no-load
104 343, startup 6 450, shutdown 3 616, reserve 229, ENS 0 EUR.

**Residual difference to the paper.**  The paper's IUC values are
higher, by an amount proportional to the reserve requirement:

| System | Reserve | Paper IUC [M] | gtopt IUC [M] | Δ |
|--------|---------|---------------|---------------|---|
| 39-bus | 10 % | 1.0070 | 0.980823 | −2.60 % |
| 39-bus | 5 % | 0.9901 | 0.977186 | −1.30 % |
| 118-bus | 5 % | 14.4787 | 14.253323 | −1.56 % |
| 118-bus | 2.5 % | 13.9725 | 13.861865 | −0.79 % |

The gap is **not** a constraint missing in gtopt, a solver tolerance or
a data mismatch:

- *Tolerance.*  The paper solved to a relative gap of 10⁻⁴ % (39-bus)
  and 0.01 % (118-bus), so its values are optima, not loose incumbents.
- *gtopt vs the published GAMS.*  39-bus: the independent PTDF
  transcription reaches gtopt's optimum to the cent.  118-bus, 2.5 %:
  gtopt's solution satisfies every row of the transcription (largest
  residual 4·10⁻⁴ MW, rounding) and the transcription's objective
  evaluated at it is 13 861 865.72 EUR = gtopt's 13 861 864.94 EUR.
  (A relative MIP gap is meaningless on that LP: its objective carries
  the −211.19 MEUR wind-curtailment constant, so 10⁻⁴ allows ≈ 20 kEUR.
  Solve it with `absmipgap`.)
- *Data.*  The only upstream data change (2018-10-24) rescaled the
  reserve column and, for 118-bus, removed a ±1 % noise on
  `SlopeVarCost` (the paper's "random noise" run); re-solving with the
  noisy 118-bus costs does not move the result towards the paper.
- *A relaxation already costs more.*  The paper's classic CUC (CCUC) is
  a relaxation of its IUC, yet 0.9998 / 0.9826 M exceeds the exact
  published-model IUC (0.980823 / 0.977186 M).  The difference therefore
  sits in a term common to every model of Table I, not in the
  individual-unit constraints.

The gap is a reserve price.  Divided by the total up-reserve
requirement it is the same in both systems:

| System | Reserve | Σ requirement [MWh] | paper − gtopt [EUR] | EUR per MWh |
|--------|---------|---------------------|---------------------|-------------|
| 39-bus | 10 % | 5 571 | 26 177 | 4.70 |
| 39-bus | 5 % | 2 785 | 12 914 | 4.64 |
| 118-bus | 5 % | 47 896 | 225 449 | 4.71 |
| 118-bus | 2.5 % | 23 948 | 110 808 | 4.63 |

So the code behind Table I charged about 4.6–4.7 EUR per MW of reserve
requirement, while the published GAMS charges `0.4·pFixCost·10⁻³`
(below 0.1 EUR/MW, 229 EUR in total for 39-bus at 10 %).  The exact formula
cannot be recovered from the published material.  Pricing up reserve at
`0.4·pVariableCost` (down reserve as published) reproduces the 39-bus
values: 1 006 979.75 EUR at 10 % (paper 1.0070 M) and 990 358.20 EUR at
5 % (paper 0.9901 M, +0.03 %).  It overshoots the 118-bus values,
though: ≥ 14.003 M at 2.5 % and ≥ 14.527 M at 5 %.  Pricing down
reserve at `0.2·pVariableCost` as well overshoots both systems
(≈ 1.019 M for 39-bus at 10 %).  Rescaling the published
`0.4/0.2·pFixCost` terms gives no clean factor either (×10 → 0.98265 M,
×100 → 0.99365 M, ×1000 → 1.08888 M for 39-bus at 10 %).  The converter
keeps the published model, which is the one gtopt matches.

## Block mode

`--mode block` writes the same hours as one non-chronological stage of
24 one-hour blocks and leaves out the intertemporal user rows.  With a
gtopt whose `Commitment` follows the stage (block formulation), each
block gets its own status `u` with `Pmin·u ≤ g ≤ Pmax·u` and the reserve
gating, but no start-up / shut-down / no-load costs, min up / down or
ramps.  It is a relaxation of the IUC and measures what a commitment
without chronology loses — compare with the paper's clustered UC (CCUC),
which keeps all of it in integer-cluster form:

| System | Reserve | IUC [M] | block [M] | block vs IUC | paper CCUC vs IUC |
|--------|---------|---------|-----------|--------------|-------------------|
| 39-bus | 10 % | 0.980823 | 0.858711 | −12.45 % | −0.72 % |
| 39-bus | 5 % | 0.977186 | 0.858564 | −12.14 % | −0.76 % |
| 118-bus | 5 % | 14.253323 | 10.146613 | −28.81 % | −2.72 % |
| 118-bus | 2.5 % | 13.861865 | 9.752425 | −29.65 % | −3.00 % |

Most of the 39-bus gap is the no-load cost the block formulation does
not charge (10.6 % of the IUC objective) plus the start-up / shut-down
costs (1.0 %); in the 118-bus case the commitment is also free to
follow the overnight wind dip hour by hour.  Pricing the block solution's
`u` at the no-load cost after the fact is not meaningful (with no cost on
`u` the optimal status is degenerate).  With an older gtopt that skips
`Commitment` on non-chronological stages the block case solves as a
pure LP economic dispatch.

## Tests

- `scripts/cucdata2gtopt/tests/test_converter.py`,
  `test_source_cli.py` — unit tests on a synthetic workbook with the same
  sheet layout (`tests/_synthetic.py`; the real data is never used in
  unit tests);
- `scripts/tests/_cli_cases_convert.py` — e2e: synthetic workbook →
  JSON → optimal gtopt MIP solve;
- `scripts/cucdata2gtopt/tests/test_integration_paper.py`
  (`integration`, `slow`) — downloads/uses the cached workbooks, solves
  the IUC cases at a 10⁻⁴ gap and checks the objective against the
  values gtopt reproduces from the published GAMS model, printing the
  paper's value next to it; skips without the data or a gtopt binary;
  the 118-bus cases need `GTOPT_CUC_118=1`; `test_block_mode_relaxes_iuc`
  checks the block-mode objective stays below the IUC.
