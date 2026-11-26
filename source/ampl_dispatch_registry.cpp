/**
 * @file      ampl_dispatch_registry.cpp
 * @brief     AMPL parameter + iterator dispatch table population
 * @date      2026-05-19
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `register_ampl_param_dispatchers` fills the `(class, attribute) ->
 * AmplParamFn` table with one row per user-facing PAMPL attribute.  A
 * row names the LP class and the `param_X` accessor; `param_shim`
 * generates the resolver: `sc.get_element(ObjectSingleId<LP>{uid})`
 * followed by the accessor call.  The user-constraint resolver in
 * `element_column_resolver.cpp` reaches it through a single
 * `find_ampl_param` probe.
 *
 * The resolver signature is uniform `(sc, uid, stage, block)`;
 * per-stage accessors `param_X(s)` ignore the block and scalar ones
 * `param_X()` ignore both — `param_shim` picks the call form from the
 * accessor's signature at compile time.
 *
 * Adding a new (class, attribute) is one table row — no resolver edit.
 * `test_ampl_param_registry_golden.cpp` pins the registered set and the
 * value every resolver returns on a fixture.
 */

#include <functional>
#include <initializer_list>
#include <optional>
#include <string_view>
#include <type_traits>
#include <utility>

#include <gtopt/allowance_pool_lp.hpp>
#include <gtopt/ammonia_node_lp.hpp>
#include <gtopt/ammonia_storage_lp.hpp>
#include <gtopt/ampl_dispatch_registry.hpp>
#include <gtopt/ampl_variable.hpp>
#include <gtopt/battery_lp.hpp>
#include <gtopt/bus_lp.hpp>
#include <gtopt/carrier_converter_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/converter_lp.hpp>
#include <gtopt/decision_variable_lp.hpp>
#include <gtopt/demand_lp.hpp>
#include <gtopt/emission_source_lp.hpp>
#include <gtopt/emission_zone_lp.hpp>
#include <gtopt/flow_lp.hpp>
#include <gtopt/flow_right_lp.hpp>
#include <gtopt/fuel_lp.hpp>
#include <gtopt/gas_compressor_lp.hpp>
#include <gtopt/gas_demand_lp.hpp>
#include <gtopt/gas_node_lp.hpp>
#include <gtopt/gas_pipe_lp.hpp>
#include <gtopt/gas_supply_lp.hpp>
#include <gtopt/gas_turbine_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/hydrogen_demand_lp.hpp>
#include <gtopt/hydrogen_node_lp.hpp>
#include <gtopt/hydrogen_storage_lp.hpp>
#include <gtopt/inertia_provision_lp.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/junction_lp.hpp>
#include <gtopt/line_commitment_lp.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/lng_terminal_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/reservoir_discharge_limit_lp.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/reservoir_seepage_lp.hpp>
#include <gtopt/simple_commitment.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/single_id.hpp>
#include <gtopt/system_context.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/thermal_node_lp.hpp>
#include <gtopt/thermal_storage_lp.hpp>
#include <gtopt/turbine_lp.hpp>
#include <gtopt/user_constraint_lp.hpp>
#include <gtopt/volume_right_lp.hpp>
#include <gtopt/waterway_lp.hpp>

namespace gtopt
{

namespace
{

/// Resolver for one (LP class, attribute): look the element up by uid
/// and call its `Accessor` — a pointer to a const member function of
/// `LP` (or of a base of `LP`) taking `(StageUid, BlockUid)`,
/// `(StageUid)` or nothing.  The element type comes from `LP`, never
/// from the accessor, so an inherited accessor (e.g. StorageLP's
/// `param_emin`) still looks up the derived class's collection.
template<typename LP, auto Accessor>
std::optional<double> param_shim(const SystemContext& sc,
                                 Uid uid,
                                 [[maybe_unused]] StageUid s,
                                 [[maybe_unused]] BlockUid b)
{
  using A = decltype(Accessor);
  static_assert(std::is_member_function_pointer_v<A>,
                "Accessor must be &LP::param_X");
  const auto& element = sc.get_element(ObjectSingleId<LP> {uid});
  if constexpr (std::is_invocable_v<A, const LP&, StageUid, BlockUid>) {
    return std::invoke(Accessor, element, s, b);
  } else if constexpr (std::is_invocable_v<A, const LP&, StageUid>) {
    return std::invoke(Accessor, element, s);
  } else {
    static_assert(std::is_invocable_v<A, const LP&>,
                  "Accessor must take (StageUid, BlockUid), (StageUid) or "
                  "nothing");
    return std::invoke(Accessor, element);
  }
}

/// `param_shim<LP, Accessor>` as an `AmplParamFn` table entry.
template<typename LP, auto Accessor>
constexpr AmplParamFn shim = &param_shim<LP, Accessor>;

/// One row of a per-class parameter table: (attribute, resolver).
using ParamEntry = std::pair<std::string_view, AmplParamFn>;

/// Register every row of @p entries under the snake_case class name of
/// `LP::Element` (e.g. "generator", "flow_right").
template<typename LP>
void register_params(SimulationLP& sim,
                     std::initializer_list<ParamEntry> entries)
{
  constexpr auto class_name = LP::Element::class_name.snake_case();
  for (const auto& [attribute, fn] : entries) {
    sim.register_ampl_param(class_name, attribute, fn);
  }
}

/// ``generator.initial_generation`` — the scalar ``t = 0⁻`` dispatch
/// (``Generator.initial_generation``), stage/block independent.  Read by
/// the ``_prev`` boundary rule in ``resolve_single_param`` as the value
/// of ``generation_prev`` at the horizon start (``initial_<attr>``).
/// Hand-written: it reads an input field, not a `param_X` accessor.
std::optional<double> gen_param_initial_generation(const SystemContext& sc,
                                                   Uid uid,
                                                   StageUid /*s*/,
                                                   BlockUid /*b*/)
{
  return sc.get_element(ObjectSingleId<GeneratorLP> {uid})
      .generator()
      .initial_generation;
}

/// Class iterator: walks `sc.elements<LP>()` and emits every element's
/// uid to the supplied captureless callback.  One instantiation per LP
/// class that participates in `sum(class(all)...)`.
template<class LP>
void iter_class(const SystemContext& sc, void* state, AmplIterCallback cb)
{
  for (const auto& el : sc.elements<LP>()) {
    cb(state, el.uid());
  }
}

}  // namespace

void register_ampl_param_dispatchers(SimulationLP& sim)
{
  {
    using L = GeneratorLP;
    register_params<L>(
        sim,
        {
            {"pmax", shim<L, &L::param_pmax>},
            {"pmin", shim<L, &L::param_pmin>},
            {"gcost", shim<L, &L::param_gcost>},
            {"lossfactor", shim<L, &L::param_lossfactor>},
            {"heat_rate", shim<L, &L::param_heat_rate>},
            {"emission_rate", shim<L, &L::param_emission_rate>},
            {"initial_generation", &gen_param_initial_generation},
        });
  }
  {
    using L = FuelLP;
    register_params<L>(
        sim,
        {
            {"price", shim<L, &L::param_price>},
            {"heat_content", shim<L, &L::param_heat_content>},
            {"combustion_emission_factor",
             shim<L, &L::param_combustion_emission_factor>},
            {"upstream_emission_factor",
             shim<L, &L::param_upstream_emission_factor>},
            // max_offtake cap row (PR #487 / #492) and the symmetric
            // min_offtake floor (PLEXOS take-or-pay; pids 595-602): the
            // stage-level parameters of FuelLP's offtake rows, exposed so
            // compliance / audit UCs can reference them.
            {"max_offtake", shim<L, &L::param_max_offtake>},
            {"max_offtake_cost", shim<L, &L::param_max_offtake_cost>},
            {"min_offtake", shim<L, &L::param_min_offtake>},
            {"min_offtake_cost", shim<L, &L::param_min_offtake_cost>},
        });
  }
  {
    using L = DemandLP;
    register_params<L>(sim,
                       {
                           {"lmax", shim<L, &L::param_lmax>},
                           {"fcost", shim<L, &L::param_fcost>},
                           {"lossfactor", shim<L, &L::param_lossfactor>},
                       });
  }
  {
    using L = LineLP;
    register_params<L>(
        sim,
        {
            {"tmax_ab", shim<L, &L::param_tmax_ab>},
            {"tmax_ba", shim<L, &L::param_tmax_ba>},
            // `max_flow` is the PAMPL alias of the A->B thermal limit.
            {"max_flow", shim<L, &L::param_tmax_ab>},
            {"min_flow", shim<L, &L::param_min_flow>},
            {"max_rating", shim<L, &L::param_max_rating>},
            {"min_rating", shim<L, &L::param_min_rating>},
            {"overload_max_rating", shim<L, &L::param_overload_max_rating>},
            {"overload_min_rating", shim<L, &L::param_overload_min_rating>},
            {"outage_max_rating", shim<L, &L::param_outage_max_rating>},
            {"outage_min_rating", shim<L, &L::param_outage_min_rating>},
            {"units_out", shim<L, &L::param_units_out>},
            {"conductor_rating_ab", shim<L, &L::param_conductor_rating_ab>},
            {"conductor_rating_ba", shim<L, &L::param_conductor_rating_ba>},
            {"equipment_rating_ab", shim<L, &L::param_equipment_rating_ab>},
            {"equipment_rating_ba", shim<L, &L::param_equipment_rating_ba>},
            {"security_limit_ab", shim<L, &L::param_security_limit_ab>},
            {"security_limit_ba", shim<L, &L::param_security_limit_ba>},
            {"tcost", shim<L, &L::param_tcost>},
            {"reactance", shim<L, &L::param_reactance>},
            {"voltage", shim<L, &L::param_voltage>},
            {"tap_ratio", shim<L, &L::param_tap_ratio>},
            {"phase_shift_deg", shim<L, &L::param_phase_shift_deg>},
            {"lossfactor", shim<L, &L::param_lossfactor>},
            {"resistance", shim<L, &L::param_resistance>},
        });
  }
  {
    using L = BatteryLP;
    register_params<L>(
        sim,
        {
            {"emin", shim<L, &L::param_emin>},
            {"emax", shim<L, &L::param_emax>},
            {"ecost", shim<L, &L::param_ecost>},
            {"input_efficiency", shim<L, &L::param_input_efficiency>},
            {"output_efficiency", shim<L, &L::param_output_efficiency>},
        });
  }
  {
    using L = ReservoirLP;
    register_params<L>(sim,
                       {
                           {"emin", shim<L, &L::param_emin>},
                           {"emax", shim<L, &L::param_emax>},
                           {"ecost", shim<L, &L::param_ecost>},
                           {"capacity", shim<L, &L::param_capacity>},
                       });
  }
  {
    using L = FlowRightLP;
    register_params<L>(sim,
                       {
                           {"fmin", shim<L, &L::param_fmin>},
                           {"fmax", shim<L, &L::param_fmax>},
                           {"target", shim<L, &L::param_target>},
                           {"fcost", shim<L, &L::param_fcost>},
                           {"uvalue", shim<L, &L::param_uvalue>},
                       });
  }
  {
    // Per-stage hydro conversion schedules and flow cap, readable as
    // constants in `turbine('X').<field>` expressions.
    using L = TurbineLP;
    register_params<L>(
        sim,
        {
            {"production_factor", shim<L, &L::param_production_factor>},
            {"efficiency", shim<L, &L::param_efficiency>},
            {"capacity", shim<L, &L::param_capacity>},
        });
  }
  {
    // Per-(stage, block) fmin/fmax + per-stage capacity, lossfactor,
    // fcost of the channel.
    using L = WaterwayLP;
    register_params<L>(sim,
                       {
                           {"fmin", shim<L, &L::param_fmin>},
                           {"fmax", shim<L, &L::param_fmax>},
                           {"capacity", shim<L, &L::param_capacity>},
                           {"lossfactor", shim<L, &L::param_lossfactor>},
                           {"fcost", shim<L, &L::param_fcost>},
                       });
  }
  {
    using L = VolumeRightLP;
    register_params<L>(sim,
                       {
                           {"fmax", shim<L, &L::param_fmax>},
                           {"emin", shim<L, &L::param_emin>},
                           {"emax", shim<L, &L::param_emax>},
                           {"demand", shim<L, &L::param_demand>},
                           {"saving_rate", shim<L, &L::param_saving_rate>},
                           {"fail_cost", shim<L, &L::param_fail_cost>},
                       });
  }
  {
    using L = EmissionZoneLP;
    register_params<L>(sim,
                       {
                           {"cap", shim<L, &L::param_cap>},
                           {"cap_cost", shim<L, &L::param_cap_cost>},
                           {"price", shim<L, &L::param_price>},
                       });
  }
  {
    using L = EmissionSourceLP;
    register_params<L>(sim,
                       {
                           {"rate", shim<L, &L::param_rate>},
                           {"upstream_rate", shim<L, &L::param_upstream_rate>},
                       });
  }
  {
    // Per-(stage, block) RHS override, readable from other constraints'
    // expressions as `user_constraint("X").rhs`.
    using L = UserConstraintLP;
    register_params<L>(sim,
                       {
                           {"rhs", shim<L, &L::param_rhs>},
                       });
  }
  {
    // Scheduled LNG delivery per stage (the LP per-block inflow rate is
    // `delivery / stage_duration`) and the tank-level bounds / holding
    // cost.
    using L = LngTerminalLP;
    register_params<L>(sim,
                       {
                           {"delivery", shim<L, &L::param_delivery>},
                           {"emin", shim<L, &L::param_emin>},
                           {"emax", shim<L, &L::param_emax>},
                           {"ecost", shim<L, &L::param_ecost>},
                       });
  }
  // Gas network: node pressure limits [bar] and pressure cost; supply
  // offtake limits [km³/h], unit cost and stage volume cap; scheduled
  // load and shedding cost; pipe / compressor capacity, compression
  // ratio and electric power cap; turbine heat rate [km³/MWh].
  {
    using L = GasNodeLP;
    register_params<L>(sim,
                       {
                           {"pmin", shim<L, &L::param_pmin>},
                           {"pmax", shim<L, &L::param_pmax>},
                           {"pressure_cost", shim<L, &L::param_pressure_cost>},
                       });
  }
  {
    using L = GasSupplyLP;
    register_params<L>(sim,
                       {
                           {"fmin", shim<L, &L::param_fmin>},
                           {"fmax", shim<L, &L::param_fmax>},
                           {"cost", shim<L, &L::param_cost>},
                           {"volume", shim<L, &L::param_volume>},
                       });
  }
  {
    using L = GasDemandLP;
    register_params<L>(sim,
                       {
                           {"load", shim<L, &L::param_load>},
                           {"fail_cost", shim<L, &L::param_fail_cost>},
                       });
  }
  {
    using L = GasPipeLP;
    register_params<L>(sim,
                       {
                           {"fmax", shim<L, &L::param_fmax>},
                       });
  }
  {
    using L = GasCompressorLP;
    register_params<L>(sim,
                       {
                           {"fmax", shim<L, &L::param_fmax>},
                           {"max_ratio", shim<L, &L::param_max_ratio>},
                           {"power_max", shim<L, &L::param_power_max>},
                       });
  }
  {
    using L = GasTurbineLP;
    register_params<L>(sim,
                       {
                           {"heat_rate", shim<L, &L::param_heat_rate>},
                       });
  }
  // Storage peers of Battery / Reservoir (PR #483): emin / emax / ecost
  // from the StorageLP base plus the Battery-pattern efficiencies.
  {
    using L = ThermalStorageLP;
    register_params<L>(
        sim,
        {
            {"emin", shim<L, &L::param_emin>},
            {"emax", shim<L, &L::param_emax>},
            {"ecost", shim<L, &L::param_ecost>},
            {"input_efficiency", shim<L, &L::param_input_efficiency>},
            {"output_efficiency", shim<L, &L::param_output_efficiency>},
        });
  }
  {
    using L = HydrogenStorageLP;
    register_params<L>(
        sim,
        {
            {"emin", shim<L, &L::param_emin>},
            {"emax", shim<L, &L::param_emax>},
            {"ecost", shim<L, &L::param_ecost>},
            {"input_efficiency", shim<L, &L::param_input_efficiency>},
            {"output_efficiency", shim<L, &L::param_output_efficiency>},
        });
  }
  {
    using L = AmmoniaStorageLP;
    register_params<L>(
        sim,
        {
            {"emin", shim<L, &L::param_emin>},
            {"emax", shim<L, &L::param_emax>},
            {"ecost", shim<L, &L::param_ecost>},
            {"input_efficiency", shim<L, &L::param_input_efficiency>},
            {"output_efficiency", shim<L, &L::param_output_efficiency>},
        });
  }
  {
    // Multi-carrier converter (PR #485).
    using L = CarrierConverterLP;
    register_params<L>(sim,
                       {
                           {"efficiency", shim<L, &L::param_efficiency>},
                           {"ocost", shim<L, &L::param_ocost>},
                       });
  }
  {
    // Hydrogen offtake with per-stage energy targets.
    using L = HydrogenDemandLP;
    register_params<L>(sim,
                       {
                           {"lmax", shim<L, &L::param_lmax>},
                           {"lmin", shim<L, &L::param_lmin>},
                           {"price", shim<L, &L::param_price>},
                           {"min_energy", shim<L, &L::param_min_energy>},
                           {"max_energy", shim<L, &L::param_max_energy>},
                       });
  }
  {
    // CO₂ cap-and-trade pool (PR #495 / #496): StorageLP emin / emax /
    // ecost plus the per-stage free allocation `delivery`.
    using L = AllowancePoolLP;
    register_params<L>(sim,
                       {
                           {"emin", shim<L, &L::param_emin>},
                           {"emax", shim<L, &L::param_emax>},
                           {"ecost", shim<L, &L::param_ecost>},
                           {"delivery", shim<L, &L::param_delivery>},
                       });
  }
}

void register_ampl_iterator_dispatchers(SimulationLP& sim)
{
  sim.register_ampl_iter(Generator::class_name.snake_case(),
                         &iter_class<GeneratorLP>);
  sim.register_ampl_iter(Demand::class_name.snake_case(),
                         &iter_class<DemandLP>);
  sim.register_ampl_iter(Line::class_name.snake_case(), &iter_class<LineLP>);
  sim.register_ampl_iter(Battery::class_name.snake_case(),
                         &iter_class<BatteryLP>);
  sim.register_ampl_iter(Reservoir::class_name.snake_case(),
                         &iter_class<ReservoirLP>);
  sim.register_ampl_iter(Waterway::class_name.snake_case(),
                         &iter_class<WaterwayLP>);
  sim.register_ampl_iter(Turbine::class_name.snake_case(),
                         &iter_class<TurbineLP>);
  sim.register_ampl_iter(Converter::class_name.snake_case(),
                         &iter_class<ConverterLP>);
  sim.register_ampl_iter(Junction::class_name.snake_case(),
                         &iter_class<JunctionLP>);
  sim.register_ampl_iter(Flow::class_name.snake_case(), &iter_class<FlowLP>);
  sim.register_ampl_iter(FlowRight::class_name.snake_case(),
                         &iter_class<FlowRightLP>);
  sim.register_ampl_iter(VolumeRight::class_name.snake_case(),
                         &iter_class<VolumeRightLP>);
  sim.register_ampl_iter(ReservoirSeepageLP::SeepageName,
                         &iter_class<ReservoirSeepageLP>);
  // Unlocks ``sum(reservoir_discharge_limit(all).…)`` enumeration
  // patterns over the volume-dependent discharge caps.
  sim.register_ampl_iter(ReservoirDischargeLimit::class_name.snake_case(),
                         &iter_class<ReservoirDischargeLimitLP>);
  sim.register_ampl_iter(ReserveProvision::class_name.snake_case(),
                         &iter_class<ReserveProvisionLP>);
  sim.register_ampl_iter(ReserveZone::class_name.snake_case(),
                         &iter_class<ReserveZoneLP>);
  sim.register_ampl_iter(Bus::class_name.snake_case(), &iter_class<BusLP>);
  sim.register_ampl_iter(LngTerminal::class_name.snake_case(),
                         &iter_class<LngTerminalLP>);
  sim.register_ampl_iter(Fuel::class_name.snake_case(), &iter_class<FuelLP>);
  sim.register_ampl_iter(EmissionZone::class_name.snake_case(),
                         &iter_class<EmissionZoneLP>);
  sim.register_ampl_iter(EmissionSource::class_name.snake_case(),
                         &iter_class<EmissionSourceLP>);
  // Commitment exposes status/startup/shutdown PAMPL variables and must be
  // iterable for `sum(commitment(all).status)` (the deprecated
  // `simple_commitment(...)` spelling is folded onto it by the parser).
  sim.register_ampl_iter(Commitment::class_name.snake_case(),
                         &iter_class<CommitmentLP>);
  // Deprecated alias (the parser folds it onto `commitment`; registered so
  // the Python PAMPL validator and the runtime agree on accepted names).
  sim.register_ampl_iter(SimpleCommitment::class_name.snake_case(),
                         &iter_class<CommitmentLP>);
  // Inertia: zone-level requirement column + per-generator provision
  // columns; both register AMPL variables on every (scenario, stage).
  sim.register_ampl_iter(InertiaZone::class_name.snake_case(),
                         &iter_class<InertiaZoneLP>);
  sim.register_ampl_iter(InertiaProvision::class_name.snake_case(),
                         &iter_class<InertiaProvisionLP>);
  // Free continuous decision variable referenced via
  // `decision_variable("X").value` from PAMPL constraints.
  sim.register_ampl_iter(DecisionVariable::class_name.snake_case(),
                         &iter_class<DecisionVariableLP>);
  // UserConstraint: enables `sum(user_constraint(all).rhs)` and other
  // class-level iteration patterns over user constraints.
  sim.register_ampl_iter(UserConstraint::class_name.snake_case(),
                         &iter_class<UserConstraintLP>);

  // Multi-carrier element family (PR #483 / #484 / #485) — typed
  // balance nodes + their storage peers + the cross-carrier
  // converter.  Each gets ``sum(<class>(all).<attr>)`` support
  // via class-level iteration.
  sim.register_ampl_iter(ThermalNode::class_name.snake_case(),
                         &iter_class<ThermalNodeLP>);
  sim.register_ampl_iter(ThermalStorage::class_name.snake_case(),
                         &iter_class<ThermalStorageLP>);
  sim.register_ampl_iter(HydrogenNode::class_name.snake_case(),
                         &iter_class<HydrogenNodeLP>);
  sim.register_ampl_iter(HydrogenStorage::class_name.snake_case(),
                         &iter_class<HydrogenStorageLP>);
  sim.register_ampl_iter(HydrogenDemand::class_name.snake_case(),
                         &iter_class<HydrogenDemandLP>);
  sim.register_ampl_iter(AmmoniaNode::class_name.snake_case(),
                         &iter_class<AmmoniaNodeLP>);
  sim.register_ampl_iter(AmmoniaStorage::class_name.snake_case(),
                         &iter_class<AmmoniaStorageLP>);
  sim.register_ampl_iter(CarrierConverter::class_name.snake_case(),
                         &iter_class<CarrierConverterLP>);

  // AllowancePool — CO₂ cap-and-trade pool (PR #495 / #496).
  sim.register_ampl_iter(AllowancePool::class_name.snake_case(),
                         &iter_class<AllowancePoolLP>);

  // Gas network: `sum(gas_supply(all).flow)` and friends.
  sim.register_ampl_iter(GasNode::class_name.snake_case(),
                         &iter_class<GasNodeLP>);
  sim.register_ampl_iter(GasSupply::class_name.snake_case(),
                         &iter_class<GasSupplyLP>);
  sim.register_ampl_iter(GasDemand::class_name.snake_case(),
                         &iter_class<GasDemandLP>);
  sim.register_ampl_iter(GasPipe::class_name.snake_case(),
                         &iter_class<GasPipeLP>);
  sim.register_ampl_iter(GasCompressor::class_name.snake_case(),
                         &iter_class<GasCompressorLP>);
  sim.register_ampl_iter(GasTurbine::class_name.snake_case(),
                         &iter_class<GasTurbineLP>);

  // LineCommitment registers `status` per (scenario, stage); iterable so
  // `sum(line_commitment(all).status)` resolves.
  sim.register_ampl_iter(LineCommitment::class_name.snake_case(),
                         &iter_class<LineCommitmentLP>);
}

}  // namespace gtopt
