# Storage, water values and opportunity costs

GTOpt co-optimizes the operation of reservoirs, hydro cascades and
batteries with the rest of the system, and reports the **value of stored
water and energy**: the marginal cost that drives their optimal dispatch.

## What is modelled

| Element | Main features | Reference |
|---|---|---|
| Reservoirs and hydro cascades | `Junction`, `Waterway`, `Reservoir`, `Turbine`, `Pump`, `Flow`; volume-dependent production factor, seepage, evaporation, spillway, discharge limits, minimum/maximum volumes, soft minimum volumes, initial and final targets, daily cycling | [Input Data](input-data.md), [Planning Guide](planning-guide.md) |
| Batteries and other storage | `Battery` (charge/discharge efficiency, energy and power limits, standalone or coupled to a generator through a converter), and the same storage base for thermal, hydrogen and ammonia storage | [Input Data](input-data.md) |
| Water rights | Irrigation agreements as `FlowRight` / `VolumeRight` with their own balances and penalties | [Irrigation Agreements](irrigation-agreements.md) |
| End-of-horizon value | `FutureCost`: boundary cuts (intercept + one water-value slope per reservoir) that price the water left at the end of the horizon | [SDDP §4.14](methods/sddp.md#414-boundary-cuts-future-cost-approximation), [Monolithic](methods/monolithic.md) |

## Water values and storage opportunity costs

Every storage element has an energy (or volume) balance per block. Its
**dual** is the marginal value of one more unit stored at that time:

| Output | Meaning | Unit |
|---|---|---|
| `Reservoir/water_value_dual` | **Water value**: marginal value of stored water per block, the price at which the reservoir is indifferent between turbining now and keeping the water | \$ per unit of volume |
| `Battery/energy_dual` | **Opportunity cost of stored energy**: marginal value of one more MWh in the battery per block | \$/MWh |
| `FutureCost` outputs | The cost-to-go α at the end of the horizon and its components | \$ |

The optimal dispatch follows directly from these values: a hydro plant
generates when the bus price exceeds the water value times its
conversion factor, and pumps or stores when it is below; a battery
charges when the price is below its energy value (corrected for
efficiency) and discharges when above. Comparing the water value along a
cascade, or over the year, shows where and when water is scarce.

How the value is obtained depends on the method:

- **Monolithic**: the duals of the full-horizon LP, with the end of the
  horizon valued by boundary cuts or by `efin` targets.
- **SDDP / cascade**: the backward pass builds the **future cost
  function** as Benders cuts on the storage states; the cut slopes are
  the water values, under uncertainty in inflows (apertures). The cuts
  can be saved and reused as boundary cuts by later runs (e.g. weekly
  dispatch inheriting the water values of a long-term run). See
  [SDDP](methods/sddp.md) and [Cascade](methods/cascade.md).
- **Emissions view**: with
  `--set model_options.objective_mode=emissions` the same duals give the
  **carbon** opportunity cost of stored water and energy (tCO₂ per unit
  stored). See [Emissions](emissions.md).

### Numerics and hard limits

Storage values are only useful if they are numerically reliable. GTOpt
scales each variable class (`variable_scales`), the objective
(`scale_objective`) and the matrix (`equilibration_method`: row-max or
Ruiz), and reports the condition number (`kappa`) of every solve in
`solution.csv`, so systems that mix kWh-scale batteries with TWh-scale
reservoirs — Lake Laja stores about 5,600 hm³, roughly 10 TWh through
the El Toro–Antuco cascade — produce stable dispatch and duals. See
[Planning Options](planning-options.md).

Hard limits on reservoir volumes and on water rights (irrigation
agreements) are enforced in SDDP with an elastic filter: when a forward
solve cannot meet them from the incoming state, a clone of the LP relaxes
the state variables with penalized slacks, and its duals give a
**feasibility cut** that teaches earlier stages to keep enough water ([SDDP §2.4](methods/sddp.md#24-elastic-filter-for-feasibility)).

Water values can also be given as inputs (`Reservoir.ecost`, the value of
stored water) when an external study already provides them.

## Examples

| Case | What it shows |
|---|---|
| `cases/hydro_valley_sddpjl`, `cases/hydro_thermal_sddpjl` | Hydro valley and hydro-thermal benchmarks from SDDP.jl |
| `cases/fast_hydro_thermal` | Small hydro-thermal system for quick tests |
| `cases/bat_4b_24`, `cases/exp_bat_4b_24` | 24-hour battery operation, and battery expansion |

```bash
cd cases/fast_hydro_thermal
gtopt system_fast_hydro_thermal.json --set output_directory=/tmp/hydro
ls /tmp/hydro/Reservoir/   # water_value_dual_*, energy_sol_* (volume), ...
```
