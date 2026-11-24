/**
 * @file      carrier_storage_lp.cpp
 * @brief     CarrierStorageLP — thermal / hydrogen / ammonia storage
 * @date      Tue Nov 24 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Mirror of ``source/battery_lp.cpp`` for the non-electric carriers.
 * All LP semantics flow through ``StorageLP<>``; this file adds the
 * charge / discharge columns and the carrier-node coupling.
 */

#include <gtopt/ammonia_node_lp.hpp>
#include <gtopt/ammonia_storage_lp.hpp>
#include <gtopt/carrier_storage_lp.hpp>
#include <gtopt/hydrogen_node_lp.hpp>
#include <gtopt/hydrogen_storage_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/thermal_node_lp.hpp>
#include <gtopt/thermal_storage_lp.hpp>

namespace gtopt
{

template<typename Storage,
         typename NodeLP,
         auto NodeRef,
         const StorageStateDefaults& Defaults>
CarrierStorageLP<Storage, NodeLP, NodeRef, Defaults>::CarrierStorageLP(
    const Storage& pstorage, const InputContext& ic)
    : StorageBase(pstorage, ic, Element::class_name)
    , input_efficiency(ic,
                       Element::class_name,
                       this->id(),
                       std::move(this->object().input_efficiency))
    , output_efficiency(ic,
                        Element::class_name,
                        this->id(),
                        std::move(this->object().output_efficiency))
{
}

template<typename Storage,
         typename NodeLP,
         auto NodeRef,
         const StorageStateDefaults& Defaults>
bool CarrierStorageLP<Storage, NodeLP, NodeRef, Defaults>::add_to_lp(
    SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp)
{
  static constexpr const auto& cname = Element::class_name;
  static constexpr auto ampl_name = Element::class_name.snake_case();
  static constexpr double flow_conversion_rate = 1.0;
  const auto& data = this->object();
  const auto uid = this->uid();

  if (!CapacityBase::add_to_lp(sc, ampl_name, scenario, stage, lp)) [[unlikely]]
  {
    return false;
  }

  // Out of service: no flow columns and nothing stamped into the node balance.
  // Before this gate the flow columns were created and `fout` injected
  // into an active node with no energy balance behind it (free carrier).
  if (!this->is_active(stage)) {
    return true;
  }

  // Register filter metadata for sum(...) predicates.  Mirrors Battery:
  // only the ``type`` tag is registered (the node reference is optional).
  if (const auto& t = data.type) {
    AmplElementMetadata metadata;
    metadata.emplace_back(TypeKey, *t);
    sc.register_ampl_element_metadata(ampl_name, uid, std::move(metadata));
  }

  auto&& [opt_capacity, capacity_col] = this->capacity_and_col(stage, lp);
  const double stage_capacity = opt_capacity.value_or(LinearProblem::DblMax);

  const auto input_efficiency_at = [&](BlockUid b)
  { return input_efficiency.optval(stage.uid(), b).value_or(1.0); };
  const auto output_efficiency_at = [&](BlockUid b)
  { return output_efficiency.optval(stage.uid(), b).value_or(1.0); };

  const auto& blocks = stage.blocks();

  const auto es = sc.options().variable_scale_map().lookup(
      Element::class_name.full_name(), StorageBase::EnergyName, uid);

  BIndexHolder<ColIndex> finps;
  BIndexHolder<ColIndex> fouts;
  map_reserve(finps, blocks.size());
  map_reserve(fouts, blocks.size());

  double fs = 1.0;
  for (auto&& block : blocks) {
    const auto buid = block.uid();
    finps[buid] = lp.add_col(SparseCol {
        .class_name = Element::class_name.full_name(),
        .variable_name = FinpName,
        .variable_uid = uid,
        .context = make_block_context(scenario.uid(), stage.uid(), block.uid()),
    });
    fouts[buid] = lp.add_col(SparseCol {
        .class_name = Element::class_name.full_name(),
        .variable_name = FoutName,
        .variable_uid = uid,
        .context = make_block_context(scenario.uid(), stage.uid(), block.uid()),
    });
    fs = lp.get_col_scale(finps[buid]);
  }
  const StorageOptions opts {
      .use_state_variable =
          data.use_state_variable.value_or(Defaults.use_state_variable),
      .daily_cycle = data.daily_cycle.value_or(Defaults.daily_cycle),
      .class_name = Element::class_name.full_name(),
      .variable_uid = uid,
      .energy_scale = es,
      .flow_scale = fs,
  };
  if (!StorageBase::add_to_lp(cname,
                              ampl_name,
                              sc,
                              scenario,
                              stage,
                              lp,
                              flow_conversion_rate,
                              finps,
                              input_efficiency_at,
                              fouts,
                              output_efficiency_at,
                              stage_capacity,
                              capacity_col,
                              {},
                              {},
                              opts))
  {
    SPDLOG_CRITICAL(
        "Failed to add storage constraints for {} {}", ampl_name, uid);
    return false;
  }

  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  finp_cols[st_key] = std::move(finps);
  fout_cols[st_key] = std::move(fouts);

  // Stamp into the carrier-node balance row if the node ref is set.
  // Sign convention (mirrors BusLP):
  //   * finp (charging) pulls the carrier from the node     → −1
  //   * fout (discharging) injects the carrier to the node  → +1
  if (const auto& node_ref = data.*NodeRef; node_ref.has_value()) {
    using NodeSId = ObjectSingleId<NodeLP>;
    const auto& node_lp = sc.element<NodeLP>(NodeSId {node_ref.value()});
    if (node_lp.is_active(stage)) {
      const auto& brows = node_lp.balance_rows_at(scenario, stage);
      const auto& finps_at = finp_cols.at(st_key);
      const auto& fouts_at = fout_cols.at(st_key);
      for (auto&& block : blocks) {
        const auto buid = block.uid();
        auto& brow = lp.row_at(brows.at(buid));
        brow[finps_at.at(buid)] = -1.0;
        brow[fouts_at.at(buid)] = +1.0;
      }
    }
  }

  sc.add_ampl_variable(
      ampl_name, uid, ChargeName, scenario, stage, finp_cols.at(st_key));
  sc.add_ampl_variable(
      ampl_name, uid, DischargeName, scenario, stage, fout_cols.at(st_key));

  return true;
}

template<typename Storage,
         typename NodeLP,
         auto NodeRef,
         const StorageStateDefaults& Defaults>
bool CarrierStorageLP<Storage, NodeLP, NodeRef, Defaults>::add_to_output(
    OutputContext& out) const
{
  static constexpr const auto& cname = Element::class_name;
  const auto pid = this->id();

  out.add_col_sol(cname, FinpName, pid, finp_cols);
  out.add_col_cost(cname, FinpName, pid, finp_cols);
  out.add_col_sol(cname, FoutName, pid, fout_cols);
  out.add_col_cost(cname, FoutName, pid, fout_cols);

  return StorageBase::add_to_output(out, cname)
      && CapacityBase::add_to_output(out);
}

template class CarrierStorageLP<ThermalStorage,
                                ThermalNodeLP,
                                &ThermalStorage::thermal_node,
                                storage_state_defaults::thermal_storage>;
template class CarrierStorageLP<HydrogenStorage,
                                HydrogenNodeLP,
                                &HydrogenStorage::hydrogen_node,
                                storage_state_defaults::hydrogen_storage>;
template class CarrierStorageLP<AmmoniaStorage,
                                AmmoniaNodeLP,
                                &AmmoniaStorage::ammonia_node,
                                storage_state_defaults::ammonia_storage>;

}  // namespace gtopt
