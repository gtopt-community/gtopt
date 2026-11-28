# lng_reserve_1b — gas-backed reserve of an LNG-fed unit

One bus, demand 100 MW, three monthly stages of one 720-h block.  `gas`
(VOM 5 \$/MWh, heat rate 0.2 m³/MWh) burns from `tank`, which receives
one contracted cargo of 30,000 m³ in stage 1 (30 \$/m³); `backup`
(200 \$/MWh) covers the rest.  A 30-MW up-reserve requirement
(`csf_up`, shortage 10,000 \$/MW-h) is offered by `gas` (no bid) and by
`peaker` (bid 3 \$/MW-h).  The tank has `reserve_sustain_hours = 24`
(LngTerminal "Gas Backing of Reserves" in
`docs/formulation/mathematical-formulation.md`).

Gas: one stage burns 100 × 720 × 0.2 = 14,400 m³, so the cargo covers
two full stages plus 1,200 m³.  Up reserve needs a stock
`V_end ≥ T · 0.2 · r_up`: 30 MW for T = 24 h is 144 m³ held at the end
of a block.  Holding it through the last stage would cost 144 × 975 \$/m³
(the gas value: 195 \$/MWh saving / 0.2) = 140,400 \$, more than the
peaker's 3 × 30 × 720 = 64,800 \$, so all the gas is burnt and the tank
is empty at the end of stage 3:

| stage | tank at end | up reserve by | reserve price \$/MW-h | bus price \$/MWh |
|------:|------------:|--------------:|----------------------:|-----------------:|
| 1 | ≥ 144 m³ | gas | 0 | 200 |
| 2 | ≥ 144 m³ | gas | 0 | 200 |
| 3 | 0 | peaker (30 MW) | **3** | 200 |

Objective = gas VOM 5 × 150,000 + backup 200 × 66,000 + contract
30 × 30,000 + peaker reserve 64,800 = **14,914,800 \$**.  The split of
the burn between the stages is degenerate; the goldens (`output/`)
compare only the objective and the prices.  Without
`reserve_sustain_hours` the empty tank still "sells" 30 MW of up
reserve in stage 3, the reserve price stays 0 and the objective is
14,850,000 \$.  With `T = 1` the 6 m³ stock is cheaper than the peaker
(5,850 \$), so the tank keeps it and the stage-3 reserve price is the
value of the held gas, 6 × 975 / (30 × 720) = 0.27 \$/MW-h.

Down reserve is limited the same way through the generic provision row
`r_dn ≤ P − Pmin`: with no gas `P = 0`, so `r_dn = 0`.
