# gas_hu_ryan_6b7n — Hu & Ryan 6-bus / 7-node coupled power-gas system

## Source and license

D. Hu, *Datasets for reliability unit commitment to manage natural gas
cost in power system operations*, Mendeley Data, V1, 2019,
doi:10.17632/ynb88g67xy.1 (title and creator as in the DataCite record;
the dataset description credits D. Hu and S. Ryan).  Related paper:
D. Hu and S. M. Ryan, "Stochastic vs. deterministic scheduling of a
combined natural gas and power system with uncertain wind energy",
*Int. J. Electr. Power Energy Syst.* 108:303–313, 2019,
doi:10.1016/j.ijepes.2018.12.047.
License: **CC BY 4.0** (<https://creativecommons.org/licenses/by/4.0/>).

`Data_IISE_DH_SMR.xlsx` is the unmodified original file
(sha256 `e45b394338fdbc7fc5fda978dafba555a5597569fa8218103fb0d37bf63a9f3a`).
`gas_hu_ryan_6b7n.json` is a derived work (LP reduction below), also
CC BY 4.0, with attribution as above.

## Reduction

`make_case.py` builds an LP-representable version of the sheet
`6bus_7gasnodes` (forecast wind, 24 h):

- unit commitment relaxed (pmin = 0, no ramps / min up-down);
- daily supplier caps spread evenly (`MaxOP / 24`);
- the node-1 storage acts as a supply priced at `storcost`, limited to its
  initial level / 24 per hour (no injection);
- gas shedding 50 $/unit, power shedding 1000 $/MWh, Kirchhoff on;
- 16 tangents per pipe; pipes oriented as in the best of the 2^5
  orientations of the radial tree.

## Expected values (`expected.json`)

| Reference | Objective (24 h) |
|-----------|------------------|
| Tangent LP (scipy/HiGHS, what gtopt builds) | 560 236.03 |
| Exact Weymouth (scipy, fixed-flow pressure LP / SLSQP) | 564 054.72 |

The 0.68 % gap is **not** the tangent error, which is at most
`K (F/2N)^2`. Node 3 is a dead end whose `pmax` is below the pressure node 5
needs. The relaxation lets pipe 3–5 carry no flow while it holds a pressure
drop. The exact model cannot do that, so it must route the gas differently.
On a radial network the tangent relaxation is tight unless an upper
pressure bound downstream binds. That is the case here.
`GasPipe/pressure_error_sol` flags exactly this pipe.

Regenerate with `PYTHONPATH=scripts python3 cases/gas_hu_ryan_6b7n/make_case.py`.
