# `s1b_fail_segments` — stepwise demand-failure (VoLL) curve

One bus, one cheap generator (`g1`, 88 MW at 20 $/MWh), one backup unit
priced above every VoLL step (`backup`, 2000 $/MWh) and one demand `d1`
with a PLP-like four-segment failure curve (`fail_segments`): 5 % at
492.4, 5 % at 541.7, 10 % at 648.6 and the open residual at 742.1 $/MWh.

| Block | lmax | shortfall | seg1 | seg2 | seg3 | seg4 |
|-------|------|-----------|------|------|------|------|
| 1     | 100  | 12        | 5    | 5    | 2    | 0    |
| 2     | 120  | 32        | 6    | 6    | 12   | 8    |
| 3     | 140  | 52        | 7    | 7    | 14   | 24   |

The LP curtails the cheapest segment first and never dispatches the
backup unit.  Every block ends strictly inside a segment, so the bus
marginal price is unique: 648.6 in block 1 and 742.1 in blocks 2-3.
Objective: 20·88·3 + 6467.7 + 19924.6 + 34129.5 = 65801.8 $.
Outputs: `Demand/fail_sol` (total) and `Demand/fail_seg<k>_sol`
(per segment).
