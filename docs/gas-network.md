# Gas Network — User Guide

gtopt models a steady-state natural-gas transmission network inside the
same LP as the power system.  Gas-fired units draw their fuel from gas
nodes, pipes follow the Weymouth equation in squared pressure,
compressors raise the pressure at the cost of fuel or electric power,
pipes can store gas (line pack), and the balance duals are nodal gas
prices.

- Formulation: [mathematical-formulation.md §5.19](formulation/mathematical-formulation.md)
  (white paper: [`sections/gas_network.tex`](white_paper/sections/gas_network.tex)).
- JSON fields: [input-data.md §3.27](input-data.md).
- Converter: [gasnet2gtopt](scripts/gasnet2gtopt.md).

## Elements

| Element | JSON array | Role |
|---------|------------|------|
| `GasNode` | `gas_node_array` | Squared pressure π = p² in [pmin², pmax²] and a gas balance per block; dual = gas price [$/km³] |
| `GasSupply` | `gas_supply_array` | Field, pipeline import or LNG regasification; rate bounds, cost, optional stage volume |
| `GasDemand` | `gas_demand_array` | Non-power load with optional priced shedding |
| `GasPipe` | `gas_pipe_array` | Weymouth pipe: `radial` tangents, `linearized` (meshed, SLP) or `none` (capacity only); elevation; line pack |
| `GasCompressor` | `gas_compressor_array` | Ratio bound, bypass, fuel `none` / `fixed_fraction` / `saldarriaga`, gas or electric drive; booster or open valve |
| `GasTurbine` | `gas_turbine_array` | Links a `Generator` to its delivery node: heat-rate fuel, minimum-pressure gate, derate |

## User constraints (PAMPL)

Every gas element is addressable from user constraints and PAMPL
files — LP attributes (`gas_node.pi`, `gas_supply.flow`,
`gas_demand.fail` / `served`, `gas_pipe.flow` / `flow_in` / `flow_out`
/ `linepack`, `gas_compressor.flow` / `fuel` / `power`,
`gas_turbine.generation` / `fuel`), input parameters (`gas_supply.fmax`,
`gas_turbine.heat_rate`, …) and `sum(<class>(all : type = '…'))`
filters on `type` and the node uids.  Full list:
[user-constraints.md §3](user-constraints.md#3-element-types-and-attributes).

```text
# take-or-pay: at least 40 km³/h from the LNG regas terminals
sum(gas_supply(all : type = 'lng_regas').flow) >= 40
# fuel cap of one plant, in gas units
gas_turbine('G_T_gas').fuel <= 100
```

## Choosing the pipe model

- **Radial network (tree)**: keep the default `weymouth_mode: radial`.
  The tangent outer approximation is exact up to `K (fmax/2N)²` bar²
  unless an upper pressure bound downstream binds.  Check
  `GasPipe/pressure_error_sol`; gtopt warns above 0.1 bar.
- **Meshed network**: set `weymouth_mode: linearized` (preferably with
  `linearization: tangent`) and `options.gas_options.slp_iterations`
  (e.g. 100).  The sequential LP converges to a point satisfying the
  exact Weymouth equations (a stationary point; global optimality is
  not certified).  The SLP runs with the monolithic method only.
- **Delivery through a parallel system** that would close a loop:
  `weymouth_mode: none` (a capacity-only arc).

## Practical notes

- Set a tiny negative `pressure_cost` (−1e-4) on the nodes to select the
  highest-pressure solution among equal-cost ones.
- A plant with `min_pressure` keeps its delivery node at least at
  `(min_pressure − pressure_band)²` even when off (an LP cannot switch
  the gate off); drop `min_pressure` where the node cannot reach it.
- Line pack is closed per stage (`linepack_cyclic`, default true) and is
  not an SDDP state variable; for multi-day outage studies use one
  chronological stage and `linepack_cyclic: false`.
- An LNG tank is an `LngTerminal` (formulation §5.14); its send-out is
  not yet linked to a `GasNode`.  Model regasification as a `GasSupply`
  with `type: lng_regas` and a stage `volume`.  The value of stored gas,
  take-or-pay floors (`Fuel.min_offtake`) and what is not modelled
  (spot-cargo decisions, send-out limits) are in formulation §5.14.

## Benchmark cases

All cases except `gas_cc_renca227_*` are under `cases/` and run in CTest
(`add_e2e_case`); the
benchmark and literature groups are also re-solved by
`scripts/gasnet2gtopt/tests/test_gtopt_integration.py`.

| Case | Source | Licence | Reference |
|------|--------|---------|-----------|
| `gas_saldarriaga_fig2_2` | Saldarriaga, PhD thesis, UTP 2018, Fig. 2.2(b) [[1]](#s1) | numeric data only | Published pressures within 0.05 bar; independent LP |
| `gas_gaslib11_radial` | GasLib-11, ZIB [[2]](#s2) | CC BY 3.0 | Independent LP |
| `gas_belgian_a1_radial` | De Wolf & Smeers (2000) [[3]](#s3), via GasModels.jl `A1.m` [[4]](#s4) | BSD-3 (GasModels) | Independent LP |
| `gas_case6`, `gas_case6_meshed` | GasModels.jl `case-6-no-power-limits.m` [[4]](#s4) | BSD-3 | Published exact optimum −253.683 |
| `gas_hu_ryan_6b7n`, `gas_hu_ryan_24b20n` | Hu, Mendeley Data [[5]](#s5) | CC BY 4.0 | Independent tangent LP and exact NLP |
| `gas_ratha_24el_12ng` | Ratha et al., Zenodo [[6]](#s6) (system of Ordoudis et al. [[7]](#s7)) | CC BY 4.0 | Independent SLP, exact-NLP polish |
| `gas_elevation_chain` | synthetic | repository | Closed-form pressures |
| `gas_mini3`, `gas_cmp_*`, `gas_lp_*`, `gas_plant_*` | synthetic (`gasnet2gtopt` tiny cases) | repository | Independent LP |
| `gas_cc_renca227_*` | stylised central-Chile network (plausible values, not real data) | external case repository | Native vs legacy LP |

The coupled cases are LP reductions of the published data (unit
commitment relaxed, deterministic wind, storage as a capped supply); see
each case README.  Their objectives are not comparable with the
published stochastic results.

The central-Chile Renca cases (`gas_cc_renca227_bstoff`,
`gas_cc_renca227_bston`), their source networks
(`central_chile*.gasnet.json`) and the Peru / Camisea study scenarios
are not in this repository: they live in the external case repository
under `chile/gas_network/central_chile_renca/` and
`peru/gas_network/camisea/`.  Point `GTOPT_CASES_DIR` at it
(`eval "$(bash tools/get_cases.sh)"`) to register their CTest entries
and run the matching `test_gtopt_integration.py` groups
(`central_chile_renca`, `multi_day_linepack`, `peru_camisea`, the slow
`peru_kp43`); without it they are skipped.

### Datasets surveyed but not used

Several open gas or gas–electric datasets were reviewed and not
bundled: GasLib-24/40/134/135 (meshed or large; possible future
cases), GasPowerModels.jl GasLib-11 + 5-bus (the gas side is the same
GasLib-11), the Ordoudis et al. EL-NG data (GPL-3.0; the CC BY 4.0
Ratha variant is used instead), and datasets whose licence does not
permit redistribution (non-commercial, unstated or missing licences)
or whose model is transient or copper-plate.  The original 6-bus/7-node
system of Liu et al. (2009) and the De Wolf & Smeers solutions are not
openly available; the CC BY copies above are used instead.

## Sources

<a id="s1"></a>[1] C. A. Saldarriaga Cortés, *Planeamiento Integrado de
Sistemas Energéticos: Un Enfoque Estocástico Multietapa*, doctoral
thesis, Universidad Tecnológica de Pereira, 2018 (repository record
2019), <https://hdl.handle.net/11059/10887>.

<a id="s2"></a>[2] M. Schmidt et al., "GasLib — A Library of Gas Network
Instances", *Data* 2(4):40, 2017, doi:10.3390/data2040040; data
<https://gaslib.zib.de>, CC BY 3.0.  Required citation: M. E. Pfetsch et
al., "Validation of Nominations in Gas Network Optimization: Models,
Methods, and Solutions", ZIB-Report 12-41, 2012 (published in *Optim.
Methods Softw.* 30(1):15–53, 2015, doi:10.1080/10556788.2014.888426).

<a id="s3"></a>[3] D. De Wolf and Y. Smeers, "The Gas Transmission
Problem Solved by an Extension of the Simplex Algorithm", *Management
Science* 46(11):1454–1465, 2000, doi:10.1287/mnsc.46.11.1454.12087.

<a id="s4"></a>[4] GasModels.jl, Los Alamos National Laboratory, MICOT
LA-CC-13-108, BSD-3-Clause, <https://github.com/lanl-ansi/GasModels.jl>
(licence text in `cases/gas_case6/LICENSE-GasModels.md`).

<a id="s5"></a>[5] D. Hu, "Datasets for reliability unit commitment to
manage natural gas cost in power system operations", Mendeley Data, V1,
2019, doi:10.17632/ynb88g67xy.1, CC BY 4.0.  Related paper: D. Hu and
S. M. Ryan, *Int. J. Electr. Power Energy Syst.* 108:303–313, 2019,
doi:10.1016/j.ijepes.2018.12.047.

<a id="s6"></a>[6] A. Ratha, A. Schwele, J. Kazempour, P. Pinson,
S. Shariat Torbaghan and A. Virag, "Affine Policies for Flexibility
Provision by Natural Gas Networks to Power Systems", *Electric Power
Systems Research* 189:106565, 2020, doi:10.1016/j.epsr.2020.106565;
data Zenodo, doi:10.5281/zenodo.3755120, CC BY 4.0.

<a id="s7"></a>[7] C. Ordoudis, P. Pinson and J. M. Morales, "An
Integrated Market for Electricity and Natural Gas Systems with
Stochastic Power Producers", *European J. Oper. Res.* 272(2):642–654,
2019, doi:10.1016/j.ejor.2018.06.036.

Further literature (compressor fuel, Weymouth physics, gas–electric
co-optimisation) is listed in
[mathematical-formulation.md §9](formulation/mathematical-formulation.md#9-references)
and in the white paper bibliography.
