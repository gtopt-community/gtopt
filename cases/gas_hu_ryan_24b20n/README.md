# gas_hu_ryan_24b20n — IEEE 24-bus + Belgian 20-node (Hu & Ryan)

## Source and license

D. Hu, *Datasets for reliability unit commitment to manage natural gas
cost in power system operations*, Mendeley Data, V1, 2019,
doi:10.17632/ynb88g67xy.1, **CC BY 4.0** (see `../gas_hu_ryan_6b7n`
for the related paper). The workbook
is vendored once, in `../gas_hu_ryan_6b7n/Data_IISE_DH_SMR.xlsx`
(sha256 pinned in `make_case.py`). This case reads its
`24bus_20gasnodes` sheet and the matching forecast-wind sheet. The
generated JSON files are derived works under CC BY 4.0, with attribution
as above.

## Reduction

The reduction is the same LP as in `../gas_hu_ryan_6b7n`:

- forecast wind, 24 independent hours, continuous dispatch (no unit
  commitment);
- gas storage is treated as a supplier limited to `initial / 24`;
- unserved gas is priced at 50 and unserved power at 1000.

Parallel pipes are merged with C = C1 + C2, so K = 1 / (C1 + C2)². After
the merge the Belgian network is a tree: 17 pipes and 2 compressors over
20 nodes.

`make_case.py` writes three files:

- `gas_hu_ryan_24b20n.json`: radial tangent form with 16 tangents per
  pipe, oriented by the sign of the exact optimum's daily flow;
- `gas_hu_ryan_24b20n_slp.json`: the same system with linearized
  (tangent) pipes and the gas SLP, so no orientation is needed;
- `expected.json`: per-hour exact Weymouth optimum (scipy tangent SLP
  polished by SLSQP) and the scipy tangent LP of the radial form.

| Reference | Objective (24 h) |
|-----------|------------------|
| exact Weymouth optimum (scipy) | 11 744 746.94 |
| radial tangent LP, 16 tangents (scipy) | 11 745 905.08 |

The radial form costs 0.0099 % more than the exact optimum. The gap
comes from hours 21–23, where a single daily orientation is not the best
choice for every hour. The SLP variant needs no orientation and should
reach the exact value.
