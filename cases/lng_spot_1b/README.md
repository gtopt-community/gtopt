# lng_spot_1b — LNG tank with contracted and spot cargoes

One bus, three monthly stages of one 720-h block, demand 100 MW.

| Element | Data |
|---|---|
| `gas` | 150 MW, `gcost` 5 \$/MWh (non-fuel VOM only), linked to the tank, heat rate 0.2 m³/MWh |
| `backup` | 200 MW, 200 \$/MWh (not linked) |
| `tank` | 0–20,000 m³, `eini` 0, no boil-off; vent 5 \$/m³ |
| contracted cargo | `delivery` [40,000, 0, 0] m³ at `delivery_cost` 30 \$/m³ |
| spot cargoes | 6,000 m³ each, up to 2 per stage (`spot_integer`), `spot_cost` [20, 45, 35] \$/m³, `spot_annual_max` 1 |

The gas unit needs 100 × 720 × 0.2 = 14,400 m³ per stage.

## Hand solution

- **Stage 1** — the 40,000 m³ contracted cargo arrives; the unit burns
  14,400 m³ and the tank is full (20,000 m³), so 5,600 m³ are vented.
  The tank binds at `emax`: a spot cargo (the cheapest, 20 \$/m³) has no
  room and would only be vented.  Tank value ϑ₁ = −5 \$/m³ (the vent
  cost), so CMg₁ = 5 + 0.2 × (−5) = **4 \$/MWh**.
- **Stage 2** — the unit burns 14,400 m³, 5,600 m³ remain.
- **Stage 3** — the annual cap allows one spot cargo; it is cheaper
  here (35 \$/m³) than in stage 2 (45 \$/m³) and the tank has room:
  5,600 + 6,000 = 11,600 m³ → 58,000 MWh of gas; the backup unit gives
  the other 14,000 MWh and sets CMg₃ = **200 \$/MWh**.  The gas in the
  tank is worth ϑ = (200 − 5)/0.2 = **975 \$/m³** in stages 2 and 3
  (the tank carries gas between them), so CMg₂ = 5 + 0.2 × 975 = 200.
  A second cargo would save 6,000 × (975 − 35) = 5.64 M\$: the annual
  cap binds.

| | Stage 1 | Stage 2 | Stage 3 |
|---|--:|--:|--:|
| gas [MW] | 100 | 100 | 80.56 |
| backup [MW] | 0 | 0 | 19.44 |
| spot cargoes | 0 | 0 | 1 |
| vent [m³] | 5,600 | 0 | 0 |
| tank at end [m³] | 20,000 | 5,600 | 0 |
| CMg [\$/MWh] | 4 | 200 | 200 |

CMg is 200 in both stages 2 and 3, so how the 11,600 m³ + 6,000 m³ are
burnt between them is degenerate: HiGHS burns 80.56 MW in stage 2 and
100 MW in stage 3 (tank 8,400 m³ at the end of stage 2).  Objective,
prices, duals, cargoes and the private-evaluation totals below are the
same for every such split; the e2e goldens (`output/`) compare only
those.  The integer cap row has dual 0 in the output: the duals come
from the LP with the integers fixed (`spot_annual_dual` prices a slot
only with continuous cargoes).

Objective = VOM 5 × 202,000 + backup 200 × 14,000 + vent 5 × 5,600 +
contract 30 × 40,000 + spot 35 × 6,000 = **5,248,000 \$**.

## Private evaluation

Cash (default): the gas unit earns 26,288,000 \$ of energy and pays
VOM 1,010,000 + gas 0.2 × 30 × 202,000 = 1,212,000 \$ at the contract
price; the terminal / owner account lists the purchases 1,410,000 \$
(contract 1,200,000 + spot 210,000) and the vent 28,000 \$.

Shadow diagnostic (`--storage-valuation lng_terminal=shadow`): the gas
unit is marginal in every stage, so its margin at the tank
shadow value, (CMg − 0.2 ϑ − 5) × P, is zero; the terminal earns
Σ ϑ × draw − purchase − vent = 25,278,000 − 1,410,000 − 28,000 =
23,840,000 \$ over the horizon, which is also the one-owner cash profit
energy revenue − VOM − purchase − vent = 26,288,000 − 1,010,000 −
1,410,000 − 28,000.  The gas burnt at shadow value (25,278,000) equals
the shadow value of the gas bought net of the vent (5,678,000) minus
the change of the inventory at shadow value (−19,600,000).  See
`scripts/gtopt_private_eval/tests/test_storage_value.py`.
