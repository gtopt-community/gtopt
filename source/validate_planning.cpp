/**
 * @file      validate_planning.cpp
 * @brief     Semantic validation of a parsed Planning object
 * @date      Wed Mar 19 00:00:00 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Implements referential integrity, range, and completeness checks
 * that run after JSON parsing but before LP construction.
 */

#include <algorithm>
#include <cmath>
#include <format>
#include <functional>
#include <iterator>
#include <map>
#include <optional>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <variant>
#include <vector>

#include <gtopt/enum_option.hpp>
#include <gtopt/field_sched.hpp>
#include <gtopt/gas_enums.hpp>
#include <gtopt/generator_enums.hpp>
#include <gtopt/line_enums.hpp>
#include <gtopt/pricing_mode.hpp>
#include <gtopt/storage_state_defaults.hpp>
#include <gtopt/user_constraint_enums.hpp>
#include <gtopt/utils.hpp>
#include <gtopt/validate_planning.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// Helper: format a SingleId for error messages.
[[nodiscard]] auto format_single_id(const SingleId& sid) -> std::string
{
  return std::visit(
      [](const auto& val) -> std::string
      {
        if constexpr (std::is_same_v<std::decay_t<decltype(val)>, Uid>) {
          return std::format("uid={}", val);
        } else {
          return std::format("name=\"{}\"", val);
        }
      },
      sid);
}

/// O(1) membership over an element array's uids and names.  Built once per
/// array (see `IdIndexCache`) so referential-integrity validation runs in
/// O(elements) instead of O(elements × targets): on a large system
/// (case3375wp, 3375 buses) the old per-reference linear scan of `bus_array`
/// via `std::ranges::any_of` was ~9% of LP-build wall time, all on the
/// single-threaded load path.
struct ElementIdIndex
{
  std::unordered_set<Uid> uids;
  // Views into the array elements' `name` strings; the array outlives the
  // index (both are call-scoped in `check_referential_integrity`).
  std::unordered_set<std::string_view> names;

  template<typename Elem>
  explicit ElementIdIndex(const Array<Elem>& arr)
  {
    uids.reserve(arr.size());
    names.reserve(arr.size());
    for (const auto& e : arr) {
      uids.insert(e.uid);
      names.insert(std::string_view {e.name});
    }
  }

  /// True iff `sid` (a uid- or name-form reference) matches an element.
  [[nodiscard]] auto contains(const SingleId& sid) const -> bool
  {
    return std::visit(
        [this](const auto& val) -> bool
        {
          if constexpr (std::is_same_v<std::decay_t<decltype(val)>, Uid>) {
            return uids.contains(val);
          } else {
            return names.contains(std::string_view {val});
          }
        },
        sid);
  }
};

/// Lazily builds and memoises one `ElementIdIndex` per target array, keyed by
/// the array's address.  Scoped to a single `check_referential_integrity`
/// call, so the addresses are stable and never reused across `System`s.
class IdIndexCache
{
public:
  template<typename Elem>
  [[nodiscard]] auto index_for(const Array<Elem>& arr) -> const ElementIdIndex&
  {
    const auto* key = static_cast<const void*>(&arr);
    if (const auto it = m_indices_.find(key); it != m_indices_.end()) {
      return it->second;
    }
    return m_indices_.emplace(key, ElementIdIndex {arr}).first->second;
  }

private:
  std::unordered_map<const void*, ElementIdIndex> m_indices_;
};

/// Check whether a SingleId names a synthetic ``<battery>_gen`` discharge
/// generator that ``System::expand_batteries`` will materialise at LP-build
/// time (it is absent from ``generator_array`` during pre-build validation).
/// Only name-form ids can match; uid-form ids never name a synthetic gen.
[[nodiscard]] inline auto synthetic_battery_member_exists(
    const SingleId& sid,
    const Array<Battery>& batteries,
    std::string_view suffix) -> bool
{
  return std::visit(
      [&batteries, suffix](const auto& val) -> bool
      {
        if constexpr (std::is_same_v<std::decay_t<decltype(val)>, Uid>) {
          return false;
        } else {
          const std::string_view name {val};
          if (!name.ends_with(suffix)) {
            return false;
          }
          const auto base = name.substr(0, name.size() - suffix.size());
          return std::ranges::any_of(
              batteries, [&base](const auto& b) { return b.name == base; });
        }
      },
      sid);
}

[[nodiscard]] inline auto synthetic_battery_gen_exists(
    const SingleId& sid, const Array<Battery>& batteries) -> bool
{
  return synthetic_battery_member_exists(sid, batteries, "_gen");
}

/// Validate referential integrity of all components.
void check_referential_integrity(ValidationResult& result, const System& sys)
{
  // One O(1) membership index per target array, built on first use, so the
  // checks below run in O(elements) instead of O(elements × targets).  The
  // generic lambda keeps `check_ref` call sites identical to the former free
  // function that linearly scanned `arr` per reference.
  IdIndexCache cache;
  const auto check_ref = [&cache](ValidationResult& res,
                                  const SingleId& sid,
                                  const auto& arr,
                                  std::string_view owner_kind,
                                  std::string_view owner_name,
                                  std::string_view field_name,
                                  std::string_view target_kind)
  {
    if (!cache.index_for(arr).contains(sid)) {
      res.errors.push_back(
          std::format("{} '{}': {} references non-existent {} ({})",
                      owner_kind,
                      owner_name,
                      field_name,
                      target_kind,
                      format_single_id(sid)));
    }
  };

  // Generator.bus -> Bus
  for (const auto& gen : sys.generator_array) {
    check_ref(
        result, gen.bus, sys.bus_array, "Generator", gen.name, "bus", "Bus");
  }

  // Demand.bus -> Bus
  for (const auto& dem : sys.demand_array) {
    check_ref(result, dem.bus, sys.bus_array, "Demand", dem.name, "bus", "Bus");
  }

  // Line.bus_a, Line.bus_b -> Bus
  for (const auto& line : sys.line_array) {
    check_ref(
        result, line.bus_a, sys.bus_array, "Line", line.name, "bus_a", "Bus");
    check_ref(
        result, line.bus_b, sys.bus_array, "Line", line.name, "bus_b", "Bus");
  }

  // Turbine.waterway -> Waterway, Turbine.flow -> Flow (alternative),
  // Turbine.generator -> Generator (all paths require generator).
  //
  // Critical invariant: a turbine MUST carry an electrical generator
  // and MUST connect to either a waterway or a flow.  Without the
  // generator there is no MW output to dispatch; without a waterway
  // or flow there is no water volume to convert.  Either omission
  // produces a silently-broken LP — `TurbineLP::add_to_lp` logs
  // a `WARN` and returns false, leaving the model with a registered
  // turbine that contributes no constraints or columns.  Promote
  // both omissions to errors at the validation gate so the user
  // sees the problem before the solver burns CPU on a degenerate
  // LP.
  for (const auto& turb : sys.turbine_array) {
    if (turb.waterway.has_value()) {
      check_ref(result,
                turb.waterway.value(),
                sys.waterway_array,
                "Turbine",
                turb.name,
                "waterway",
                "Waterway");
    }
    if (turb.flow.has_value()) {
      check_ref(result,
                turb.flow.value(),
                sys.flow_array,
                "Turbine",
                turb.name,
                "flow",
                "Flow");
    }
    // Built-in waterway mode: the turbine carries its own flow arc
    // between junction_a and (optionally) junction_b.  junction_a is the
    // intake; junction_b, when present, is the downstream junction —
    // unset means the turbined flow drains out of the system.
    if (turb.junction_a.has_value()) {
      check_ref(result,
                turb.junction_a.value(),
                sys.junction_array,
                "Turbine",
                turb.name,
                "junction_a",
                "Junction");
    }
    if (turb.junction_b.has_value()) {
      check_ref(result,
                turb.junction_b.value(),
                sys.junction_array,
                "Turbine",
                turb.name,
                "junction_b",
                "Junction");
    }
    if (!turb.waterway.has_value() && !turb.flow.has_value()
        && !turb.junction_a.has_value())
    {
      result.errors.push_back(std::format(
          "Turbine '{}' has none of waterway, flow or junction_a set "
          "(at least one is required to drive the water-to-power conversion)",
          turb.name));
    }
    check_ref(result,
              turb.generator,
              sys.generator_array,
              "Turbine",
              turb.name,
              "generator",
              "Generator");
  }

  // Pump.waterway -> Waterway, Pump.demand -> Demand
  for (const auto& pump : sys.pump_array) {
    check_ref(result,
              pump.waterway,
              sys.waterway_array,
              "Pump",
              pump.name,
              "waterway",
              "Waterway");
    check_ref(result,
              pump.demand,
              sys.demand_array,
              "Pump",
              pump.name,
              "demand",
              "Demand");
  }

  // Flow.junction -> Junction (optional in flow-turbine mode)
  for (const auto& flow : sys.flow_array) {
    if (flow.junction.has_value()) {
      check_ref(result,
                flow.junction.value(),
                sys.junction_array,
                "Flow",
                flow.name,
                "junction",
                "Junction");
    }
  }

  // Waterway.junction_a -> Junction (required), .junction_b -> Junction
  // (OPTIONAL: unset means outflow / drain mode — the flow leaves the
  // system at junction_a, no downstream credit).
  for (const auto& ww : sys.waterway_array) {
    check_ref(result,
              ww.junction_a,
              sys.junction_array,
              "Waterway",
              ww.name,
              "junction_a",
              "Junction");
    if (ww.junction_b.has_value()) {
      check_ref(result,
                ww.junction_b.value(),
                sys.junction_array,
                "Waterway",
                ww.name,
                "junction_b",
                "Junction");
    }
  }

  // Converter.battery -> Battery, generator -> Generator, demand -> Demand
  for (const auto& conv : sys.converter_array) {
    check_ref(result,
              conv.battery,
              sys.battery_array,
              "Converter",
              conv.name,
              "battery",
              "Battery");
    check_ref(result,
              conv.generator,
              sys.generator_array,
              "Converter",
              conv.name,
              "generator",
              "Generator");
    check_ref(result,
              conv.demand,
              sys.demand_array,
              "Converter",
              conv.name,
              "demand",
              "Demand");
  }

  // Reservoir.junction -> Junction
  for (const auto& res : sys.reservoir_array) {
    check_ref(result,
              res.junction,
              sys.junction_array,
              "Reservoir",
              res.name,
              "junction",
              "Junction");
  }

  // Generator.fuel -> Fuel (optional FK)
  for (const auto& gen : sys.generator_array) {
    if (gen.fuel.has_value()) {
      check_ref(result,
                gen.fuel.value(),
                sys.fuel_array,
                "Generator",
                gen.name,
                "fuel",
                "Fuel");
    }
  }

  // Commitment.fuel was removed on 2026-05-20.  Fuel FK validation
  // for the dispatch-cost path now happens on Generator (see the
  // Generator.fuel branch above).

  // ── P0 referential checks added 2026-05-20 ────────────────────────
  //
  // All of the following elements have foreign-key fields that used to
  // be silently accepted at validation time and then either crashed at
  // LP-build (when the lookup throws) or — worse — emitted a
  // `SPDLOG_WARN` + `return false` from the *_lp.cpp ``add_to_lp``,
  // leaving a registered element that contributes no constraints or
  // columns to the LP.  Promote every such case to a hard validation
  // error so the user sees the broken model before the solver runs.
  // See the matching audit notes in `validate_planning - <element>` test
  // SUBCASEs.

  // EmissionSource: generator (optional), zone (required),
  // emission (required).  `emission_source_lp.cpp:79` used to
  // silently skip a source without a generator.
  for (const auto& src : sys.emission_source_array) {
    if (src.generator.has_value()) {
      check_ref(result,
                src.generator.value(),
                sys.generator_array,
                "EmissionSource",
                src.name,
                "generator",
                "Generator");
    }
    check_ref(result,
              src.zone,
              sys.emission_zone_array,
              "EmissionSource",
              src.name,
              "zone",
              "EmissionZone");
    check_ref(result,
              src.emission,
              sys.emission_array,
              "EmissionSource",
              src.name,
              "emission",
              "Emission");
  }

  // GeneratorProfile.generator -> Generator (required).
  for (const auto& gp : sys.generator_profile_array) {
    check_ref(result,
              gp.generator,
              sys.generator_array,
              "GeneratorProfile",
              gp.name,
              "generator",
              "Generator");
  }

  // DemandProfile.demand -> Demand (required).
  for (const auto& dp : sys.demand_profile_array) {
    check_ref(result,
              dp.demand,
              sys.demand_array,
              "DemandProfile",
              dp.name,
              "demand",
              "Demand");
  }

  // ReserveProvision.generator -> Generator (required).
  // `reserve_zones` is an array of `ReserveZone` ids/names — each
  // entry must resolve.
  for (const auto& rp : sys.reserve_provision_array) {
    // Accept both real generators and the synthetic ``<battery>_gen``
    // discharge generators created by ``expand_batteries`` (BESS reserve
    // provision from ``SSCC_Activation_BESS.csv`` targets these).
    if (!cache.index_for(sys.generator_array).contains(rp.generator)
        && !synthetic_battery_gen_exists(rp.generator, sys.battery_array))
    {
      result.errors.push_back(
          std::format("ReserveProvision '{}': generator references "
                      "non-existent Generator ({})",
                      rp.name,
                      format_single_id(rp.generator)));
    }
    for (const auto& rz_id : rp.reserve_zones) {
      check_ref(result,
                rz_id,
                sys.reserve_zone_array,
                "ReserveProvision",
                rp.name,
                "reserve_zones",
                "ReserveZone");
    }
  }

  // ReserveZone dynamic-requirement references (planes mode).  The
  // contingency sets accept the synthetic ``<battery>_gen`` /
  // ``<battery>_dem`` elements created by ``expand_batteries``.
  for (const auto& rz : sys.reserve_zone_array) {
    const auto check_unit = [&](const SingleId& sid,
                                const auto& arr,
                                std::string_view suffix,
                                std::string_view field,
                                std::string_view target)
    {
      if (!cache.index_for(arr).contains(sid)
          && !synthetic_battery_member_exists(sid, sys.battery_array, suffix))
      {
        result.errors.push_back(
            std::format("ReserveZone '{}': {} references non-existent {} ({})",
                        rz.name,
                        field,
                        target,
                        format_single_id(sid)));
      }
    };
    for (const auto& sid : rz.contingency_generators) {
      check_unit(sid,
                 sys.generator_array,
                 "_gen",
                 "contingency_generators",
                 "Generator");
    }
    for (const auto& sid : rz.contingency_demands) {
      check_unit(
          sid, sys.demand_array, "_dem", "contingency_demands", "Demand");
    }
    for (const auto& sid : rz.demands) {
      check_unit(sid, sys.demand_array, "_dem", "demands", "Demand");
    }
    for (const auto& sid : rz.flow_lines) {
      check_ref(result,
                sid,
                sys.line_array,
                "ReserveZone",
                rz.name,
                "flow_lines",
                "Line");
    }
    for (const auto* groups : {&rz.urcontingencies, &rz.drcontingencies}) {
      for (const auto& cg : *groups) {
        for (const auto& sid : cg.generators) {
          check_unit(sid,
                     sys.generator_array,
                     "_gen",
                     "contingency generators",
                     "Generator");
        }
        for (const auto& sid : cg.demands) {
          check_unit(
              sid, sys.demand_array, "_dem", "contingency demands", "Demand");
        }
        for (const auto& sid : cg.lines) {
          check_ref(result,
                    sid,
                    sys.line_array,
                    "ReserveZone",
                    rz.name,
                    "contingency lines",
                    "Line");
        }
      }
    }
    if (const auto& iz = rz.inertia_zone; iz.has_value()) {
      check_ref(result,
                iz.value(),
                sys.inertia_zone_array,
                "ReserveZone",
                rz.name,
                "inertia_zone",
                "InertiaZone");
    }
    if (const auto sense = rz.requirement_sense.value_or("ge");
        sense != "ge" && sense != "eq")
    {
      result.errors.push_back(
          std::format("ReserveZone '{}': requirement_sense '{}' must be 'ge' "
                      "or 'eq'",
                      rz.name,
                      sense));
    }
    if (const auto load_term = rz.load_term.value_or("exogenous");
        load_term != "exogenous" && load_term != "endogenous_generation")
    {
      result.errors.push_back(
          std::format("ReserveZone '{}': load_term '{}' must be 'exogenous' or "
                      "'endogenous_generation'",
                      rz.name,
                      load_term));
    }
  }

  // InertiaZone.requirement_credits[].generators -> Generator.
  for (const auto& iz : sys.inertia_zone_array) {
    for (const auto& cr : iz.requirement_credits) {
      for (const auto& sid : cr.generators) {
        check_ref(result,
                  sid,
                  sys.generator_array,
                  "InertiaZone",
                  iz.name,
                  "requirement_credits generators",
                  "Generator");
      }
    }
  }

  // InertiaProvision.generator -> Generator (required).
  // `inertia_zones` is the analogous array reference.
  for (const auto& ip : sys.inertia_provision_array) {
    check_ref(result,
              ip.generator,
              sys.generator_array,
              "InertiaProvision",
              ip.name,
              "generator",
              "Generator");
    for (const auto& iz_id : ip.inertia_zones) {
      check_ref(result,
                iz_id,
                sys.inertia_zone_array,
                "InertiaProvision",
                ip.name,
                "inertia_zones",
                "InertiaZone");
    }
  }

  // SimpleCommitment.generator -> Generator (required).
  for (const auto& sc : sys.simple_commitment_array) {
    check_ref(result,
              sc.generator,
              sys.generator_array,
              "SimpleCommitment",
              sc.name,
              "generator",
              "Generator");
  }

  // LineCommitment.line -> Line (required FK), plus per-row sanity
  // checks introduced by issue #509 §"Validation".  The presence of a
  // LineCommitment row makes the referenced Line a switching candidate,
  // so an unresolved FK silently leaves the LP without OTS on that
  // line — exactly the asymmetry we want to surface as a hard error.
  for (const auto& lc : sys.line_commitment_array) {
    check_ref(result,
              lc.line,
              sys.line_array,
              "LineCommitment",
              lc.name,
              "line",
              "Line");

    if (lc.kvl_big_m.has_value() && lc.kvl_big_m.value() <= 0.0) {
      result.errors.push_back(std::format(
          "LineCommitment '{}': kvl_big_m must be > 0 when set (got {}); "
          "a non-positive big-M makes the KVL disjunction unbounded "
          "and the LP-relaxation trivially feasible (issue #509)",
          lc.name,
          lc.kvl_big_m.value()));
    }

    if (lc.initial_status.has_value()) {
      const auto v = lc.initial_status.value();
      if (v != 0.0 && v != 1.0) {
        result.errors.push_back(std::format(
            "LineCommitment '{}': initial_status must be 0 or 1 (got {})",
            lc.name,
            v));
      }
    }
  }

  // ReservoirProductionFactor.turbine -> Turbine,
  //                         .reservoir -> Reservoir (both required).
  // The production-factor row drives the water-to-MW LP coefficient;
  // either invalid FK silently breaks SDDP dispatch.
  for (const auto& rpf : sys.reservoir_production_factor_array) {
    check_ref(result,
              rpf.turbine,
              sys.turbine_array,
              "ReservoirProductionFactor",
              rpf.name,
              "turbine",
              "Turbine");
    check_ref(result,
              rpf.reservoir,
              sys.reservoir_array,
              "ReservoirProductionFactor",
              rpf.name,
              "reservoir",
              "Reservoir");
  }

  // ReservoirSeepage.waterway -> Waterway, .reservoir -> Reservoir
  // (both required).  Seepage flow + reservoir-balance row depend on
  // both FKs resolving.
  for (const auto& seep : sys.reservoir_seepage_array) {
    check_ref(result,
              seep.waterway,
              sys.waterway_array,
              "ReservoirSeepage",
              seep.name,
              "waterway",
              "Waterway");
    check_ref(result,
              seep.reservoir,
              sys.reservoir_array,
              "ReservoirSeepage",
              seep.name,
              "reservoir",
              "Reservoir");
  }

  // ── P1 referential checks added 2026-05-20 ────────────────────────
  //
  // Optional FKs that should still be VALIDATED when set.  Today an
  // invalid `bus` or `reservoir` uid on these structs just silently
  // produces a wrong dispatch because the lookup gates the
  // corresponding LP branch on `has_value()` but never on lookup
  // success.

  // Battery.bus -> Bus (optional FK).  When set, `System::expand_batteries()`
  // auto-generates the discharge Generator + charge Demand + Converter
  // wired to this bus.  Invalid uid here leaves the expansion silently
  // wired to a non-existent bus.
  // Battery.source_generator -> Generator (optional FK).  When set,
  // `expand_batteries()` creates an internal bus and re-routes the
  // source generator to it.  Invalid uid → wrong internal-bus wiring.
  for (const auto& bat : sys.battery_array) {
    if (bat.bus.has_value()) {
      check_ref(result,
                bat.bus.value(),
                sys.bus_array,
                "Battery",
                bat.name,
                "bus",
                "Bus");
    }
    if (bat.source_generator.has_value()) {
      check_ref(result,
                bat.source_generator.value(),
                sys.generator_array,
                "Battery",
                bat.name,
                "source_generator",
                "Generator");
    }
  }

  // VolumeRight.reservoir -> Reservoir (optional FK, consumptive source).
  // VolumeRight.right_reservoir -> VolumeRight (optional FK, hierarchical
  // parent / child volume balance).  Each branch in `volume_right_lp.cpp`
  // gates on `has_value()` but never on lookup success; invalid uid =
  // silent contribution to wrong reservoir or wrong parent right.
  for (const auto& vr : sys.volume_right_array) {
    if (vr.reservoir.has_value()) {
      check_ref(result,
                vr.reservoir.value(),
                sys.reservoir_array,
                "VolumeRight",
                vr.name,
                "reservoir",
                "Reservoir");
    }
    if (vr.right_reservoir.has_value()) {
      check_ref(result,
                vr.right_reservoir.value(),
                sys.volume_right_array,
                "VolumeRight",
                vr.name,
                "right_reservoir",
                "VolumeRight");
    }
  }

  // ReservoirDischargeLimit: the flow source is exactly one of ``waterway``
  // (classic Waterway flow column) or ``turbine`` (built-in waterway turbine
  // owning its own flow column).  The discharge-limit piecewise row binds
  // that flow to the reservoir's volume state, so neither set is meaningless
  // and both set is ambiguous — flag both omissions.
  for (const auto& rdl : sys.reservoir_discharge_limit_array) {
    if (rdl.waterway.has_value()) {
      check_ref(result,
                rdl.waterway.value(),
                sys.waterway_array,
                "ReservoirDischargeLimit",
                rdl.name,
                "waterway",
                "Waterway");
    }
    if (rdl.turbine.has_value()) {
      check_ref(result,
                rdl.turbine.value(),
                sys.turbine_array,
                "ReservoirDischargeLimit",
                rdl.name,
                "turbine",
                "Turbine");
    }
    if (!rdl.waterway.has_value() && !rdl.turbine.has_value()) {
      result.errors.push_back(std::format(
          "ReservoirDischargeLimit '{}' has neither a waterway nor a turbine "
          "reference set (exactly one is required to bind the flow column)",
          rdl.name));
    } else if (rdl.waterway.has_value() && rdl.turbine.has_value()) {
      result.errors.push_back(std::format(
          "ReservoirDischargeLimit '{}' has both waterway and turbine set — "
          "exactly one is required",
          rdl.name));
    }
    check_ref(result,
              rdl.reservoir,
              sys.reservoir_array,
              "ReservoirDischargeLimit",
              rdl.name,
              "reservoir",
              "Reservoir");
  }
}

/// Validate fuel/heat-rate schema rules: mutual exclusion (scalar vs
/// piecewise), shape consistency (pmax_segments and heat_rate_segments
/// must have equal length), and strict-increasing convexity on the
/// piecewise slopes (so the LP picks the cheapest segment first by
/// construction — see `GeneratorAttrs::heat_rate_segments` docstring).
/// Every value a (stage, block) real schedule can take, when it is given
/// inline (scalar or nested array); a file-backed schedule yields nothing.
[[nodiscard]] std::vector<double> inline_values(const OptTBRealFieldSched& f)
{
  std::vector<double> out;
  if (!f.has_value()) {
    return out;
  }
  if (const auto* r = std::get_if<Real>(&*f)) {
    out.push_back(*r);
  } else if (const auto* v = std::get_if<std::vector<std::vector<Real>>>(&*f)) {
    for (const auto& row : *v) {
      out.insert(out.end(), row.begin(), row.end());
    }
  }
  return out;
}

/// True when the schedule is set and not identically 0 (a file-backed
/// schedule counts as non-zero).
[[nodiscard]] bool nonzero_sched(const OptTBRealFieldSched& f)
{
  if (!f.has_value()) {
    return false;
  }
  if (std::holds_alternative<FileSched>(*f)) {
    return true;
  }
  return std::ranges::any_of(inline_values(f),
                             [](double x) { return x != 0.0; });
}

/// ReserveZone plane-based requirements:
///  - support: a plane term whose support is missing (inertia_coef with no
///    inertia source, contingency_coef with no candidate, flow_coef with
///    no flow_lines) would silently vanish — an error under
///    constraint_mode strict / debug, a warning otherwise;
///  - contingency_coef must be ≥ 0;
///  - `requirement_sense: "eq"` with planes: R absorbs any excess, so a
///    surplus cost has no effect (the column is not built) — warning;
///  - dispatch coupling (`model_options.reserve_coupling`).
void check_reserve_planes(ValidationResult& result, const Planning& planning)
{
  const auto& sys = planning.system;
  const auto cmode =
      planning.options.constraint_mode.value_or(ConstraintMode::strict);
  const bool strict = cmode != ConstraintMode::normal;
  const auto coupling =
      planning.options.model_options.reserve_coupling.value_or("warn");
  if (coupling != "warn" && coupling != "error" && coupling != "off") {
    result.errors.push_back(
        std::format("model_options.reserve_coupling '{}' must be 'warn', "
                    "'error' or 'off'",
                    coupling));
  }
  const auto support_issue = [&](std::string msg)
  {
    msg += strict ? " (constraint_mode=strict; the term would be dropped)"
                  : " — the term is dropped (constraint_mode=normal)";
    (strict ? result.errors : result.warnings).push_back(std::move(msg));
  };

  for (const auto& rz : sys.reserve_zone_array) {
    if (!rz.has_dynamic_requirement()) {
      continue;
    }
    const auto any_plane = [&](auto member)
    {
      const auto in = [&](const auto& planes)
      {
        return std::ranges::any_of(planes,
                                   [&](const ReserveRequirementPlane& p)
                                   { return nonzero_sched(p.*member); });
      };
      return in(rz.urreq_planes) || in(rz.drreq_planes);
    };

    // ── Support ─────────────────────────────────────────────────────
    const auto inertia_term = rz.inertia_term.value_or("commitment");
    const bool exo_given =
        inertia_term == "exogenous" && rz.system_inertia.has_value();
    if (any_plane(&ReserveRequirementPlane::inertia_coef) && !exo_given) {
      bool has_provision = false;
      if (rz.inertia_zone.has_value()) {
        for (const auto& ip : sys.inertia_provision_array) {
          for (const auto& z : ip.inertia_zones) {
            const auto matches = [&](const auto& a, const auto& b)
            {
              if (a.index() == b.index()) {
                return a == b;
              }
              return true;  // name vs uid: resolved later, assume present
            };
            if (matches(z, *rz.inertia_zone)) {
              has_provision = true;
            }
          }
        }
      }
      if (!has_provision) {
        support_issue(std::format(
            "ReserveZone '{}': a requirement plane sets inertia_coef but the "
            "zone has no inertia source ({})",
            rz.name,
            rz.inertia_zone.has_value()
                ? "its inertia_zone has no InertiaProvision"
                : "no inertia_zone, and no system_inertia with "
                  "inertia_term 'exogenous'"));
      }
    }
    const auto any_dir =
        [&](const Array<ReserveRequirementPlane>& planes, auto member)
    {
      return std::ranges::any_of(planes,
                                 [&](const ReserveRequirementPlane& p)
                                 { return nonzero_sched(p.*member); });
    };
    if (any_dir(rz.urreq_planes, &ReserveRequirementPlane::contingency_coef)
        && rz.contingency_generators.empty() && rz.urcontingencies.empty())
    {
      support_issue(std::format(
          "ReserveZone '{}': an up-requirement plane sets contingency_coef "
          "but there is no up contingency candidate "
          "(contingency_generators / urcontingencies)",
          rz.name));
    }
    if (any_dir(rz.drreq_planes, &ReserveRequirementPlane::contingency_coef)
        && rz.contingency_demands.empty() && rz.drcontingencies.empty())
    {
      support_issue(std::format(
          "ReserveZone '{}': a down-requirement plane sets contingency_coef "
          "but there is no down contingency candidate "
          "(contingency_demands / drcontingencies)",
          rz.name));
    }
    if (any_plane(&ReserveRequirementPlane::flow_coef) && rz.flow_lines.empty())
    {
      support_issue(std::format(
          "ReserveZone '{}': a requirement plane sets flow_coef but the zone "
          "lists no flow_lines",
          rz.name));
    }

    // ── contingency_coef ≥ 0 ────────────────────────────────────────
    for (const auto* planes : {&rz.urreq_planes, &rz.drreq_planes}) {
      for (const auto& p : *planes) {
        const auto vals = inline_values(p.contingency_coef);
        if (std::ranges::any_of(vals, [](double x) { return x < 0.0; })) {
          result.errors.push_back(std::format(
              "ReserveZone '{}': contingency_coef must be >= 0 (a negative "
              "coefficient turns the largest unit into a requirement "
              "credit)",
              rz.name));
        }
      }
    }

    // ── eq sense with planes ────────────────────────────────────────
    if (rz.requirement_sense.value_or("ge") == "eq"
        && (rz.ursurplus_cost.has_value() || rz.drsurplus_cost.has_value()))
    {
      result.warnings.push_back(std::format(
          "ReserveZone '{}': requirement_sense 'eq' with requirement planes: "
          "the requirement R is a free column above its planes and absorbs "
          "any excess provision, so ursurplus_cost / drsurplus_cost has no "
          "effect (no surplus column is built)",
          rz.name));
    }

    // ── Dispatch coupling ───────────────────────────────────────────
    if (coupling == "off") {
      continue;
    }
    std::vector<std::string> channels;
    if (rz.load_term.value_or("exogenous") == "endogenous_generation"
        && any_plane(&ReserveRequirementPlane::demand_coef))
    {
      channels.emplace_back(
          "load_term 'endogenous_generation' (D = Σ generation)");
    }
    if (any_plane(&ReserveRequirementPlane::flow_coef)
        && !rz.flow_lines.empty())
    {
      channels.emplace_back("flow_coef on flow_lines (corridor flow)");
    }
    if (inertia_term == "endogenous"
        && any_plane(&ReserveRequirementPlane::inertia_coef))
    {
      channels.emplace_back(
          "inertia_term 'endogenous' (I = Σ pf·r with r ≤ p)");
    }
    if (inertia_term == "commitment"
        && any_plane(&ReserveRequirementPlane::inertia_coef)
        && (std::ranges::any_of(sys.commitment_array,
                                [](const Commitment& c)
                                { return c.relax.value_or(false); })
            || std::ranges::any_of(sys.simple_commitment_array,
                                   [](const SimpleCommitment& c)
                                   { return c.relax.value_or(false); })))
    {
      channels.emplace_back(
          "inertia_term 'commitment' (the default) with a relaxed "
          "Commitment (u is continuous: pmin·u ≤ p earns I_c/pmin per MW; "
          "remedy: inertia_term \"exogenous\" with system_inertia, or an "
          "integer Commitment)");
    }
    const auto uses_cont =
        any_plane(&ReserveRequirementPlane::contingency_coef);
    if (uses_cont) {
      const bool line_cont = std::ranges::any_of(rz.urcontingencies,
                                                 [](const ReserveContingency& c)
                                                 { return !c.lines.empty(); })
          || std::ranges::any_of(rz.drcontingencies,
                                 [](const ReserveContingency& c)
                                 { return !c.lines.empty(); });
      if (line_cont) {
        channels.emplace_back("line contingencies (signed line flow)");
      }
      if (rz.contingency_includes_reserve.value_or(false)) {
        channels.emplace_back("contingency_includes_reserve (C ≥ a·(p + r))");
      }
    }
    if (channels.empty()) {
      continue;
    }
    std::string list;
    for (const auto& c : channels) {
      list += list.empty() ? c : "; " + c;
    }
    auto msg = std::format(
        "ReserveZone '{}': the requirement couples to dispatch through {}.  "
        "The plane dual μ then enters the LMP of every bus that reaches the "
        "coupled column as a β·μ $/MWh credit (β = plane coefficient × "
        "column sensitivity), which can price buses negative.  See "
        "docs/formulation/mathematical-formulation.md §5.9 (requirement "
        "coupling).  Set model_options.reserve_coupling to 'off' to silence "
        "or 'error' to reject.",
        rz.name,
        list);
    (coupling == "error" ? result.errors : result.warnings)
        .push_back(std::move(msg));
  }
}

/// Offline (cold) up reserve (`ReserveProvision.offline`): the gate
/// `r ≤ urmax·(1 − u)` lives on the unit's commitment status, so a unit
/// without Commitment / SimpleCommitment would offer it unconditionally
/// (error in strict / debug, warning otherwise); a Min-Provision floor
/// `urmin > 0` (which needs u = 1) contradicts it (error).
void check_offline_provisions(ValidationResult& result,
                              const Planning& planning)
{
  const auto& sys = planning.system;
  const bool strict =
      planning.options.constraint_mode.value_or(ConstraintMode::strict)
      != ConstraintMode::normal;
  const auto gen_uid = [&](const SingleId& sid) -> std::optional<Uid>
  {
    for (const auto& g : sys.generator_array) {
      if ((std::holds_alternative<Uid>(sid) && std::get<Uid>(sid) == g.uid)
          || (std::holds_alternative<Name>(sid)
              && std::get<Name>(sid) == g.name))
      {
        return g.uid;
      }
    }
    return std::nullopt;
  };
  for (const auto& rp : sys.reserve_provision_array) {
    if (!rp.offline.value_or(false)) {
      continue;
    }
    if (nonzero_sched(rp.urmin)) {
      result.errors.push_back(std::format(
          "ReserveProvision '{}': offline (cold) up reserve is offered only "
          "while the unit is off, but urmin > 0 forces it on — infeasible",
          rp.name));
    }
    const auto gu = gen_uid(rp.generator);
    if (!gu) {
      continue;  // dangling reference: reported elsewhere
    }
    const bool committed =
        std::ranges::any_of(sys.commitment_array,
                            [&](const auto& c)
                            { return gen_uid(c.generator) == gu; })
        || std::ranges::any_of(sys.simple_commitment_array,
                               [&](const auto& c)
                               { return gen_uid(c.generator) == gu; });
    if (!committed) {
      auto msg = std::format(
          "ReserveProvision '{}': offline (cold) up reserve needs a "
          "Commitment / SimpleCommitment on its generator — without a "
          "status column the reserve is offered whether the unit is on or "
          "off",
          rp.name);
      (strict ? result.errors : result.warnings).push_back(std::move(msg));
    }
  }
}

/// A Commitment builds its unit-commitment rows only on stages with
/// `"chronological": true` (min up / down times, ramps and start-up
/// logic need ordered blocks); on any other stage it is ignored.  That
/// used to happen silently: report it (warning) with the stages affected.
void check_commitment_stages(ValidationResult& result, const Planning& planning)
{
  const auto& commitments = planning.system.commitment_array;
  const auto active = [](const Commitment& c)
  {
    return !c.active.has_value() || !std::holds_alternative<IntBool>(*c.active)
        || std::get<IntBool>(*c.active) != 0;
  };
  const auto n_active = std::ranges::count_if(commitments, active);
  if (n_active == 0) {
    return;
  }
  std::string stages;
  std::size_t n_stages = 0;
  for (const auto& st : planning.simulation.stage_array) {
    if (st.chronological.value_or(false)) {
      continue;
    }
    ++n_stages;
    if (n_stages <= 5) {
      stages += std::format("{}{}", stages.empty() ? "" : ", ", st.uid);
    }
  }
  if (n_stages == 0) {
    return;
  }
  if (n_stages > 5) {
    stages += std::format(", … ({} stages)", n_stages);
  }
  std::string first;
  for (const auto& c : commitments) {
    if (active(c)) {
      first = c.name;
      break;
    }
  }
  result.warnings.push_back(std::format(
      "{} Commitment(s) (e.g. '{}') are IGNORED on stage(s) {}: unit "
      "commitment needs \"chronological\": true on the stage — set it, or "
      "use SimpleCommitment for a non-chronological stage",
      n_active,
      first,
      stages));
}

void check_heat_rate(ValidationResult& result, const System& sys)
{
  for (const auto& gen : sys.generator_array) {
    const bool has_scalar = gen.heat_rate.has_value();
    const bool has_pieces =
        !gen.heat_rate_segments.empty() || !gen.pmax_segments.empty();

    if (has_scalar && has_pieces) {
      result.errors.push_back(std::format(
          "Generator '{}': `heat_rate` (scalar) and `heat_rate_segments` / "
          "`pmax_segments` (piecewise) are mutually exclusive — set exactly "
          "one.",
          gen.name));
    }

    if (has_pieces) {
      if (gen.pmax_segments.size() != gen.heat_rate_segments.size()) {
        result.errors.push_back(
            std::format("Generator '{}': `pmax_segments` (size {}) and "
                        "`heat_rate_segments` "
                        "(size {}) must have equal length.",
                        gen.name,
                        gen.pmax_segments.size(),
                        gen.heat_rate_segments.size()));
      }

      // Strictly increasing pmax breakpoints — required for the
      // piecewise [P̄_{k-1}, P̄ₖ] decomposition to be well-defined.
      for (std::size_t k = 1; k < gen.pmax_segments.size(); ++k) {
        if (!(gen.pmax_segments[k] > gen.pmax_segments[k - 1])) {
          result.errors.push_back(std::format(
              "Generator '{}': pmax_segments must be strictly increasing — "
              "pmax_segments[{}] = {} is not > pmax_segments[{}] = {}.",
              gen.name,
              k,
              gen.pmax_segments[k],
              k - 1,
              gen.pmax_segments[k - 1]));
          break;
        }
      }

      // Strictly increasing heat-rate slopes — convexity precondition.
      for (std::size_t k = 1; k < gen.heat_rate_segments.size(); ++k) {
        if (!(gen.heat_rate_segments[k] > gen.heat_rate_segments[k - 1])) {
          result.errors.push_back(std::format(
              "Generator '{}': heat_rate_segments must be strictly increasing "
              "for the piecewise cost to be convex — heat_rate_segments[{}] = "
              "{} is not > heat_rate_segments[{}] = {}.",
              gen.name,
              k,
              gen.heat_rate_segments[k],
              k - 1,
              gen.heat_rate_segments[k - 1]));
          break;
        }
      }
    }
  }

  // Commitment.pmax_segments / heat_rate_segments were removed on
  // 2026-05-20.  The piecewise-curve validation above (on Generator)
  // is the only path now.

  // ── P2 fuel/heat-rate pairing (added 2026-05-20) ────────────────────
  //
  // The LP-side coefficient is
  //   slope_cost_per_mwh = stage_fuel_price * heat_rate + block_gcost
  // (see `source/generator_lp.cpp:138-153`).  When either factor is
  // missing the code silently falls back to `block_gcost`, which is
  // almost never the user's intent:
  //
  //   * fuel set, no heat_rate (and no heat_rate_segments)
  //       → fuel_price coefficient drops to 0 → fuel cost ignored
  //   * heat_rate (or heat_rate_segments) set, no fuel
  //       → no per-fuel pricing applied → heat_rate ignored
  //
  // Both cases are flagged as warnings (not errors).  The LP still
  // solves, but the resulting dispatch ignores half of the user's
  // declared cost structure — better to surface the disagreement at
  // the validation gate than to track it down post-solve.
  for (const auto& gen : sys.generator_array) {
    const bool has_fuel = gen.fuel.has_value();
    const bool has_heat_rate =
        gen.heat_rate.has_value() || !gen.heat_rate_segments.empty();
    if (has_fuel && !has_heat_rate) {
      result.warnings.push_back(std::format(
          "Generator '{}': fuel='{}' set but no heat_rate or "
          "heat_rate_segments — fuel price will be ignored at the LP "
          "(per-MWh cost falls back to gcost only).  Set heat_rate or "
          "heat_rate_segments to consume the fuel price.",
          gen.name,
          format_single_id(gen.fuel.value())));
    } else if (has_heat_rate && !has_fuel) {
      result.warnings.push_back(std::format(
          "Generator '{}': heat_rate set but no fuel — heat_rate will "
          "be ignored at the LP (per-MWh cost falls back to gcost only). "
          "Set a fuel reference to consume the heat_rate.",
          gen.name));
    }
  }

  // Commitment.fuel / pmax_segments / heat_rate_segments were removed
  // on 2026-05-20.  The dispatch-cost fuel + heat-rate pairing now
  // lives entirely on Generator, validated above.
}

// ── Positivity helpers ────────────────────────────────────────────────
//
// Water-right, line, and waterway magnitudes must be physically
// positive (zero allowed when explicitly called out; negative is
// always a schema bug).  These helpers walk the FieldSched variant
// (scalar / vector / file) and report any negative scalar value
// found.  FileSched entries are skipped — they resolve at
// load-time and the file loader is responsible for its own checks.

enum class Positivity : std::uint8_t
{
  strict,  ///< value must be > 0
  non_negative,  ///< value must be >= 0
};

[[nodiscard]] constexpr bool violates(double v, Positivity p) noexcept
{
  return p == Positivity::strict ? !(v > 0.0) : !(v >= 0.0);
}

[[nodiscard]] constexpr std::string_view positivity_name(Positivity p) noexcept
{
  return p == Positivity::strict ? "> 0" : ">= 0";
}

template<typename Vec>
void check_vector_positive(ValidationResult& result,
                           const Vec& values,
                           Positivity p,
                           std::string_view owner_kind,
                           std::string_view owner_name,
                           std::string_view field_name)
{
  if constexpr (std::is_arithmetic_v<
                    std::remove_cvref_t<decltype(values.front())>>)
  {
    for (const auto& [i, v_raw] : enumerate(values)) {
      const auto v = static_cast<double>(v_raw);
      if (violates(v, p)) {
        result.errors.push_back(std::format("{} '{}': {}[{}] = {} must be {}",
                                            owner_kind,
                                            owner_name,
                                            field_name,
                                            i,
                                            v,
                                            positivity_name(p)));
        return;  // one error per field is enough
      }
    }
  } else {
    // Nested vector (TBReal / STBReal).  Recurse one level.
    for (const auto& sub : values) {
      check_vector_positive(result, sub, p, owner_kind, owner_name, field_name);
    }
  }
}

/// Check a FieldSched<Real, ...> variant for positivity.
/// FileSched (string) entries are skipped — those resolve at load-time.
template<typename FieldSched>
void check_field_positive(ValidationResult& result,
                          const FieldSched& fs,
                          Positivity p,
                          std::string_view owner_kind,
                          std::string_view owner_name,
                          std::string_view field_name)
{
  std::visit(
      [&](const auto& val)
      {
        using T = std::remove_cvref_t<decltype(val)>;
        if constexpr (std::is_same_v<T, Real>) {
          if (violates(static_cast<double>(val), p)) {
            result.errors.push_back(std::format("{} '{}': {} = {} must be {}",
                                                owner_kind,
                                                owner_name,
                                                field_name,
                                                val,
                                                positivity_name(p)));
          }
        } else if constexpr (std::is_same_v<T, FileSched>) {
          // Filename reference — defer validation to load-time.
        } else {
          check_vector_positive(
              result, val, p, owner_kind, owner_name, field_name);
        }
      },
      fs);
}

/// Check an optional FieldSched field.  Skips when unset.
template<typename OptFieldSched>
void check_opt_field_positive(ValidationResult& result,
                              const OptFieldSched& opt,
                              Positivity p,
                              std::string_view owner_kind,
                              std::string_view owner_name,
                              std::string_view field_name)
{
  if (!opt.has_value()) {
    return;
  }
  check_field_positive(result, *opt, p, owner_kind, owner_name, field_name);
}

/// True when any value of a FieldSched is strictly positive.  File
/// references (resolved at load time) count as "unknown" → false.
template<typename FieldSched>
[[nodiscard]] bool field_has_positive(const FieldSched& fs)
{
  bool positive = false;
  const auto scan = [&positive](this const auto& self, const auto& val) -> void
  {
    using T = std::remove_cvref_t<decltype(val)>;
    if constexpr (std::is_arithmetic_v<T>) {
      positive = positive || static_cast<double>(val) > 0.0;
    } else if constexpr (std::is_same_v<T, FileSched>) {
      // Unknown until load time.
    } else {
      for (const auto& sub : val) {
        self(sub);
      }
    }
  };
  std::visit(scan, fs);
  return positive;
}

/// True when a user-constraint expression references FlowRight `fr`
/// (`flow_right('name')`, `flow_right("name")` or `flow_right(uid)`).
[[nodiscard]] bool expression_references_flow_right(std::string_view expr,
                                                    const FlowRight& fr)
{
  const auto has = [expr](const std::string& needle)
  { return expr.contains(needle); };
  return has(std::format("flow_right('{}')", fr.name))
      || has(std::format("flow_right(\"{}\")", fr.name))
      || has(std::format("flow_right({})", fr.uid));
}

/// A FlowRight whose `uvalue` REWARDS flow (> 0), with no `junction_a`
/// and no user constraint referencing it, collects the reward without
/// moving any water — a phantom benefit (e.g. the former Maule
/// Res. 105 right).  Report it as an error.
void check_flow_right_anchoring(ValidationResult& result, const System& sys)
{
  for (const auto& fr : sys.flow_right_array) {
    if (fr.junction_a.has_value() || !fr.uvalue.has_value()
        || !field_has_positive(*fr.uvalue))
    {
      continue;
    }
    const bool referenced = std::ranges::any_of(
        sys.user_constraint_array,
        [&fr](const UserConstraint& uc)
        { return expression_references_flow_right(uc.expression, fr); });
    if (!referenced) {
      result.errors.push_back(std::format(
          "FlowRight '{}': positive uvalue (a reward) with no junction_a "
          "and no user constraint referencing it — the LP would collect "
          "the reward without moving water.  Attach it to a junction or "
          "tie it to a physical flow with a user constraint.",
          fr.name));
    }
  }
}

/// A `bound_rule` with no segments evaluates to its `cap` (or 0 when no
/// cap is set) for every volume — a silently constant right that the
/// author almost certainly did not mean.  Report it as a warning.
void check_bound_rules(ValidationResult& result, const System& sys)
{
  const auto warn_empty = [&result](std::string_view cls,
                                    std::string_view name,
                                    const std::optional<RightBoundRule>& rule)
  {
    if (rule.has_value() && rule->segments.empty()) {
      result.warnings.push_back(std::format(
          "{} '{}': bound_rule has no segments — it evaluates to {} for "
          "every volume",
          cls,
          name,
          rule->cap.has_value() ? std::format("its cap {}", *rule->cap)
                                : std::string {"0"}));
    }
  };
  for (const auto& vr : sys.volume_right_array) {
    warn_empty("VolumeRight", vr.name, vr.bound_rule);
  }
  for (const auto& fr : sys.flow_right_array) {
    warn_empty("FlowRight", fr.name, fr.bound_rule);
  }
}

/// Check positivity of flow-right / volume-right / line / waterway fields.
void check_positivity(ValidationResult& result, const System& sys)
{
  // FlowRight: fmin, target, fmax must be >= 0 (consumptive flow).
  for (const auto& fr : sys.flow_right_array) {
    check_opt_field_positive(result,
                             fr.fmin,
                             Positivity::non_negative,
                             "FlowRight",
                             fr.name,
                             "fmin");
    check_opt_field_positive(result,
                             fr.target,
                             Positivity::non_negative,
                             "FlowRight",
                             fr.name,
                             "target");
    check_opt_field_positive(result,
                             fr.fmax,
                             Positivity::non_negative,
                             "FlowRight",
                             fr.name,
                             "fmax");
    // Non-consumptive rights return the served flow to `junction_b`;
    // without it there is nowhere to credit the flow, so the right would
    // silently behave as consumptive.  Require the pairing explicitly.
    if (fr.consumptive.has_value() && !*fr.consumptive
        && !fr.junction_b.has_value())
    {
      result.errors.push_back(
          std::format("FlowRight '{}': consumptive=false requires 'junction_b' "
                      "to return the served flow to the river",
                      fr.name));
    }
    // Opt-in pass-through bypass arc: needs both endpoints, a
    // non-negative capacity, and it is the only thing that gives a
    // consumptive right's `junction_b` a meaning.
    if (fr.bypass_fmax.has_value() && *fr.bypass_fmax < 0.0) {
      result.errors.push_back(
          std::format("FlowRight '{}': bypass_fmax must be >= 0 (got {})",
                      fr.name,
                      *fr.bypass_fmax));
    }
    if (fr.bypass_requested()
        && (!fr.junction_a.has_value() || !fr.junction_b.has_value()))
    {
      result.warnings.push_back(
          std::format("FlowRight '{}': bypass requested but 'junction_a' and "
                      "'junction_b' are not both set — no bypass arc is built",
                      fr.name));
    }
    if (fr.junction_b.has_value() && !fr.bypass_requested()
        && fr.consumptive.value_or(true))
    {
      result.warnings.push_back(std::format(
          "FlowRight '{}': 'junction_b' is set but the right is consumptive "
          "and no bypass is requested — 'junction_b' has no effect (set "
          "'use_bypass': true for a pass-through arc, or "
          "'consumptive': false to return the served flow)",
          fr.name));
    }
  }

  // VolumeRight: storage and demand magnitudes must be >= 0.
  for (const auto& vr : sys.volume_right_array) {
    check_opt_field_positive(result,
                             vr.emin,
                             Positivity::non_negative,
                             "VolumeRight",
                             vr.name,
                             "emin");
    check_opt_field_positive(result,
                             vr.emax,
                             Positivity::non_negative,
                             "VolumeRight",
                             vr.name,
                             "emax");
    check_opt_field_positive(result,
                             vr.demand,
                             Positivity::non_negative,
                             "VolumeRight",
                             vr.name,
                             "demand");
    check_opt_field_positive(result,
                             vr.fmax,
                             Positivity::non_negative,
                             "VolumeRight",
                             vr.name,
                             "fmax");
    if (vr.eini.has_value() && *vr.eini < 0.0) {
      result.errors.push_back(std::format(
          "VolumeRight '{}': eini = {} must be >= 0", vr.name, *vr.eini));
    }
    if (vr.efin.has_value() && *vr.efin < 0.0) {
      result.errors.push_back(std::format(
          "VolumeRight '{}': efin = {} must be >= 0", vr.name, *vr.efin));
    }
  }

  // Line: transfer capacities must be >= 0 (direction is encoded
  // separately in bus_a/bus_b).
  for (const auto& line : sys.line_array) {
    check_opt_field_positive(result,
                             line.tmax_ab,
                             Positivity::non_negative,
                             "Line",
                             line.name,
                             "tmax_ab");
    check_opt_field_positive(result,
                             line.tmax_ba,
                             Positivity::non_negative,
                             "Line",
                             line.name,
                             "tmax_ba");
  }

  // Waterway (hydro flow): fmin and fmax must be >= 0.  Negative flow
  // would invert the direction, which should be expressed by swapping
  // junction_a/junction_b instead.  fmax == 0 is legal: it means the
  // flow is pinned at 0 (matches PLP's VertMax=0 → qv_k ∈ [0,0] on
  // the vertimiento waterway — see plpcnfce.dat + leecnfce.f:342-343).
  for (const auto& ww : sys.waterway_array) {
    check_opt_field_positive(
        result, ww.fmin, Positivity::non_negative, "Waterway", ww.name, "fmin");
    check_opt_field_positive(
        result, ww.fmax, Positivity::non_negative, "Waterway", ww.name, "fmax");
  }
}

// ── Piecewise-linear feasibility (Reservoir seepage / discharge_limit) ──
//
// Both `ReservoirSeepage` and `ReservoirDischargeLimit` produce a
// stage-level LP constraint of the form
//
//     row(efin) − slope · efin  ≤  intercept
//
// where `(slope, intercept)` is selected by piecewise-linear segment
// against the start-of-stage volume.  At the LOWEST volume boundary
// (`efin = emin`) the constraint reduces to
//
//     row(efin)  ≤  intercept + slope · emin
//
// For `row(efin) ≥ 0` to be a feasible LP, the right-hand side must
// be non-negative — which means the FIRST segment (lowest volume
// breakpoint) must satisfy  `intercept + slope · emin ≥ 0`.  When
// the input data violates this invariant the LP solves cleanly only
// while the reservoir stays above the first segment's lower bound;
// the moment a backward cut pulls efin to emin (which happens
// regularly under SDDP iter-1+), the resolve goes primal-infeasible
// and the trajectory diverges between off (which retains stale
// state on the live backend) and compress (which reverts on
// reconstruct).  Catching the invariant at validation time produces
// a clear "fix the input data" message instead of opaque solver
// fallbacks downstream.
//
// Implementation: walks `system.reservoir_seepage_array` and
// `system.reservoir_discharge_limit_array`, looks up the owning
// reservoir's `emin` (when scalar — vector/file schedules are
// resolved at load time and skipped here), then evaluates the
// first segment's `intercept + slope · emin` and emits a warning
// when the result is negative.

namespace
{
// Try to extract a scalar value from an `OptTRealFieldSched` /
// `OptTBRealFieldSched`-shaped field.  Returns nullopt for
// vector / file schedules — those are validated per-stage at load
// time and can't be sanity-checked here.
template<typename OptField>
[[nodiscard]] std::optional<Real> try_scalar_value(const OptField& field)
{
  if (!field.has_value()) {
    return std::nullopt;
  }
  return std::visit(
      [](const auto& v) -> std::optional<Real>
      {
        using T = std::remove_cvref_t<decltype(v)>;
        if constexpr (std::is_same_v<T, Real>) {
          return v;
        } else {
          return std::nullopt;
        }
      },
      *field);
}

// Look up a reservoir struct by SingleId (Uid or Name).
[[nodiscard]] const Reservoir* find_reservoir(
    const std::vector<Reservoir>& reservoirs, const SingleId& sid)
{
  return std::visit(
      [&](const auto& v) -> const Reservoir*
      {
        using T = std::remove_cvref_t<decltype(v)>;
        if constexpr (std::is_same_v<T, Uid>) {
          for (const auto& r : reservoirs) {
            if (r.uid == v) {
              return &r;
            }
          }
        } else if constexpr (std::is_same_v<T, Name>) {
          for (const auto& r : reservoirs) {
            if (r.name == v) {
              return &r;
            }
          }
        }
        return nullptr;
      },
      sid);
}

// Look up a waterway struct by SingleId (Uid or Name).
[[nodiscard]] const Waterway* find_waterway(
    const std::vector<Waterway>& waterways, const SingleId& sid)
{
  return std::visit(
      [&](const auto& v) -> const Waterway*
      {
        using T = std::remove_cvref_t<decltype(v)>;
        if constexpr (std::is_same_v<T, Uid>) {
          for (const auto& w : waterways) {
            if (w.uid == v) {
              return &w;
            }
          }
        } else if constexpr (std::is_same_v<T, Name>) {
          for (const auto& w : waterways) {
            if (w.name == v) {
              return &w;
            }
          }
        }
        return nullptr;
      },
      sid);
}

// Compute the active volume range [V_low, V_high] for segment k of a
// segments vector sorted ascending by `.volume`, clipped to the
// reservoir's [emin, emax] envelope.  N is the segment count.
struct SegmentRange
{
  Real v_low {0.0};
  Real v_high {0.0};
};

template<typename SegmentT>
[[nodiscard]] SegmentRange segment_range(const std::vector<SegmentT>& segments,
                                         std::size_t k,
                                         Real emin,
                                         Real emax)
{
  const auto seg_lo = static_cast<Real>(segments[k].volume);
  const auto v_low = std::max(seg_lo, emin);
  const auto v_high = (k + 1 < segments.size())
      ? static_cast<Real>(segments[k + 1].volume)
      : emax;
  return {.v_low = v_low, .v_high = v_high};
}

// Linear function value at endpoints (no interior extrema for linear).
struct LinearRange
{
  Real f_low {0.0};
  Real f_high {0.0};
  [[nodiscard]] Real fmin() const { return std::min(f_low, f_high); }
  [[nodiscard]] Real fmax() const { return std::max(f_low, f_high); }
};

[[nodiscard]] LinearRange evaluate_linear(Real slope,
                                          Real constant,
                                          const SegmentRange& r)
{
  return {
      .f_low = constant + (slope * r.v_low),
      .f_high = constant + (slope * r.v_high),
  };
}

/// Extract one Real per stage from a 1D `OptTRealFieldSched`-shaped
/// field.  Scalar broadcasts to every stage; vector indexes by stage
/// (truncated/padded to `num_stages` with `default_value`); file-
/// schedule returns `nullopt` (truly deferred to load-time).
template<typename OptField>
[[nodiscard]] std::optional<std::vector<Real>> per_stage_values(
    const OptField& field, std::size_t num_stages, Real default_value)
{
  if (!field.has_value()) {
    return std::vector<Real>(num_stages, default_value);
  }
  return std::visit(
      [&](const auto& v) -> std::optional<std::vector<Real>>
      {
        using T = std::remove_cvref_t<decltype(v)>;
        if constexpr (std::is_same_v<T, Real>) {
          return std::vector<Real>(num_stages, v);
        } else if constexpr (std::is_same_v<T, std::vector<Real>>) {
          std::vector<Real> out(num_stages, default_value);
          for (std::size_t s = 0; s < num_stages && s < v.size(); ++s) {
            out[s] = v[s];
          }
          return out;
        } else {
          // FileSched (string) — truly deferred.
          return std::nullopt;
        }
      },
      *field);
}

/// 2D variant for `OptTBRealFieldSched` (per-stage, per-block).
/// Reduces each stage's block vector to a single scalar via
/// `reducer` (use `std::ranges::min` for upper-bound fields where the
/// strictest stage-level value is the smallest, `std::ranges::max`
/// for lower-bound fields where the strictest is the largest).
template<typename OptField, typename Reducer>
[[nodiscard]] std::optional<std::vector<Real>> per_stage_values_2d(
    const OptField& field,
    std::size_t num_stages,
    Real default_value,
    Reducer reducer)
{
  if (!field.has_value()) {
    return std::vector<Real>(num_stages, default_value);
  }
  return std::visit(
      [&](const auto& v) -> std::optional<std::vector<Real>>
      {
        using T = std::remove_cvref_t<decltype(v)>;
        if constexpr (std::is_same_v<T, Real>) {
          return std::vector<Real>(num_stages, v);
        } else if constexpr (std::is_same_v<T,
                                            std::vector<std::vector<Real>>>) {
          std::vector<Real> out(num_stages, default_value);
          for (std::size_t s = 0; s < num_stages && s < v.size(); ++s) {
            if (!v[s].empty()) {
              out[s] = reducer(v[s]);
            }
          }
          return out;
        } else {
          return std::nullopt;
        }
      },
      *field);
}

/// Per-stage scan summary for one piecewise segment in one direction
/// (below the lower bound or above the upper bound).  Aggregated
/// across all stages so the validator emits exactly ONE warning per
/// (element, segment, direction) instead of one per stage.
struct StageViolation
{
  std::size_t count {0};  ///< Number of stages where the bound is violated.
  Real worst_diff {0.0};  ///< Largest |bound − f| across the failing stages.
  std::size_t worst_stage {0};  ///< Stage index achieving `worst_diff`.
  LinearRange worst_fr {};  ///< Linear range at the worst stage.
  SegmentRange worst_range {};  ///< Active [V_low, V_high] at the worst stage.
  Real worst_bound {0.0};  ///< Bound value at the worst stage.
};

/// Walk every stage and accumulate the worst lower-bound violation
/// (`fr.fmin() < lower_bounds[s]`) and worst upper-bound violation
/// (`fr.fmax() > upper_bounds[s]`) for the given segment.  Returns a
/// pair `{below, above}`; either may have `count == 0` for "no
/// violation in that direction".
template<typename Segment>
[[nodiscard]] std::pair<StageViolation, StageViolation> scan_segment_per_stage(
    const std::vector<Segment>& segments,
    std::size_t k,
    Real slope,
    Real constant,
    std::span<const Real> emins,
    std::span<const Real> emaxs,
    std::span<const Real> lower_bounds,
    std::span<const Real> upper_bounds)
{
  StageViolation below;
  StageViolation above;
  const std::size_t num_stages = emins.size();
  for (std::size_t s = 0; s < num_stages; ++s) {
    const auto range = segment_range(segments, k, emins[s], emaxs[s]);
    if (range.v_high < range.v_low) {
      continue;  // segment outside this stage's [emin, emax] envelope
    }
    const auto fr = evaluate_linear(slope, constant, range);

    if (fr.fmin() < lower_bounds[s]) {
      ++below.count;
      const Real diff = lower_bounds[s] - fr.fmin();
      if (diff > below.worst_diff) {
        below.worst_diff = diff;
        below.worst_stage = s;
        below.worst_fr = fr;
        below.worst_range = range;
        below.worst_bound = lower_bounds[s];
      }
    }
    if (fr.fmax() > upper_bounds[s]) {
      ++above.count;
      const Real diff = fr.fmax() - upper_bounds[s];
      if (diff > above.worst_diff) {
        above.worst_diff = diff;
        above.worst_stage = s;
        above.worst_fr = fr;
        above.worst_range = range;
        above.worst_bound = upper_bounds[s];
      }
    }
  }
  return {below, above};
}

/// Per-stage seepage envelopes.  Returns `nullopt` for any field that
/// is file-schedule (truly deferred to load-time validation).
struct SeepageEnvelopes
{
  std::vector<Real> emins;  ///< Reservoir per-stage emin.
  std::vector<Real> emaxs;  ///< Reservoir per-stage emax.
  std::vector<Real>
      fmins;  ///< Strictest per-stage waterway floor (max-reduced).
  std::vector<Real>
      fmaxs;  ///< Strictest per-stage waterway ceiling (min-reduced).
};

[[nodiscard]] std::optional<SeepageEnvelopes> resolve_seepage_envelopes(
    const Reservoir& res, const Waterway& ww, std::size_t num_stages)
{
  using std::ranges::max;
  using std::ranges::min;

  // Reservoir.emin / emax are per-(stage, block) — reduce each stage's
  // block vector to the strictest stage-level value: max(emin) (highest
  // required floor), min(emax) (lowest allowed ceiling).
  auto emins =
      per_stage_values_2d(res.emin,
                          num_stages,
                          0.0,
                          [](const std::vector<Real>& v) { return max(v); });
  auto emaxs =
      per_stage_values_2d(res.emax,
                          num_stages,
                          std::numeric_limits<Real>::infinity(),
                          [](const std::vector<Real>& v) { return min(v); });
  // Reduce per-stage block vectors to the strictest stage-level
  // value: max(fmin) (highest required floor), min(fmax) (lowest
  // allowed ceiling).
  auto fmins =
      per_stage_values_2d(ww.fmin,
                          num_stages,
                          0.0,
                          [](const std::vector<Real>& v) { return max(v); });
  auto fmaxs =
      per_stage_values_2d(ww.fmax,
                          num_stages,
                          std::numeric_limits<Real>::infinity(),
                          [](const std::vector<Real>& v) { return min(v); });
  if (!emins.has_value() || !emaxs.has_value() || !fmins.has_value()
      || !fmaxs.has_value())
  {
    return std::nullopt;
  }
  return SeepageEnvelopes {
      .emins = std::move(*emins),
      .emaxs = std::move(*emaxs),
      .fmins = std::move(*fmins),
      .fmaxs = std::move(*fmaxs),
  };
}

/// Per-stage discharge-limit envelopes (only the reservoir
/// [emin, emax] matter; the LP constrains the discharge col bound to
/// `intercept + slope·V ≥ 0`).
struct DischargeLimitEnvelopes
{
  std::vector<Real> emins;
  std::vector<Real> emaxs;
};

[[nodiscard]] std::optional<DischargeLimitEnvelopes>
resolve_discharge_limit_envelopes(const Reservoir& res, std::size_t num_stages)
{
  // Reservoir.emin / emax are per-(stage, block) — reduce each stage's
  // block vector to the strictest stage-level value: max(emin) (highest
  // required floor), min(emax) (lowest allowed ceiling).
  using std::ranges::max;
  using std::ranges::min;
  auto emins =
      per_stage_values_2d(res.emin,
                          num_stages,
                          0.0,
                          [](const std::vector<Real>& v) { return max(v); });
  auto emaxs =
      per_stage_values_2d(res.emax,
                          num_stages,
                          std::numeric_limits<Real>::infinity(),
                          [](const std::vector<Real>& v) { return min(v); });
  if (!emins.has_value() || !emaxs.has_value()) {
    return std::nullopt;
  }
  return DischargeLimitEnvelopes {
      .emins = std::move(*emins),
      .emaxs = std::move(*emaxs),
  };
}

/// Format the standard "below fmin" warning for a seepage segment.
[[nodiscard]] std::string format_seepage_below_warning(
    const ReservoirSeepage& seep,
    const Reservoir& res,
    const Waterway& ww,
    std::size_t k,
    std::size_t num_stages,
    const StageViolation& v)
{
  return std::format(
      "ReservoirSeepage '{}' (reservoir '{}', waterway '{}'): "
      "segment {} produces qfilt below waterway fmin in {} of {} "
      "stages (worst at stage {}: {:.3g} ≤ efin ≤ {:.3g} → qfilt "
      "range [{:.6g}, {:.6g}] vs fmin={:.6g}, slope={:.6g}, "
      "constant={:.6g}); the LP will go primal-infeasible whenever "
      "efin lands in the segment's lower portion.  Adjust the "
      "segment data so that `constant + slope * V ≥ fmin` for all "
      "V in [V_low, V_high].",
      seep.name,
      res.name,
      ww.name,
      k,
      v.count,
      num_stages,
      v.worst_stage,
      v.worst_range.v_low,
      v.worst_range.v_high,
      v.worst_fr.fmin(),
      v.worst_fr.fmax(),
      v.worst_bound,
      seep.segments[k].slope,
      seep.segments[k].constant);
}

/// Format the standard "above fmax" warning for a seepage segment.
[[nodiscard]] std::string format_seepage_above_warning(
    const ReservoirSeepage& seep,
    const Reservoir& res,
    const Waterway& ww,
    std::size_t k,
    std::size_t num_stages,
    const StageViolation& v)
{
  return std::format(
      "ReservoirSeepage '{}' (reservoir '{}', waterway '{}'): "
      "segment {} produces qfilt above waterway fmax in {} of {} "
      "stages (worst at stage {}: {:.3g} ≤ efin ≤ {:.3g} → qfilt "
      "range [{:.6g}, {:.6g}] vs fmax={:.6g}, slope={:.6g}, "
      "constant={:.6g}); the LP will go primal-infeasible whenever "
      "efin lands in the segment's upper portion.",
      seep.name,
      res.name,
      ww.name,
      k,
      v.count,
      num_stages,
      v.worst_stage,
      v.worst_range.v_low,
      v.worst_range.v_high,
      v.worst_fr.fmin(),
      v.worst_fr.fmax(),
      v.worst_bound,
      seep.segments[k].slope,
      seep.segments[k].constant);
}

/// Format the standard "negative discharge bound" warning for a
/// discharge-limit segment.
[[nodiscard]] std::string format_discharge_limit_warning(
    const ReservoirDischargeLimit& ddl,
    const Reservoir& res,
    std::size_t k,
    std::size_t num_stages,
    const StageViolation& v)
{
  return std::format(
      "ReservoirDischargeLimit '{}' (reservoir '{}'): segment {} "
      "produces a negative discharge upper bound in {} of {} "
      "stages (worst at stage {}: {:.3g} ≤ efin ≤ {:.3g} → bound "
      "range [{:.6g}, {:.6g}], slope={:.6g}, intercept={:.6g}); "
      "the LP will go primal-infeasible whenever efin lands in "
      "the segment's lower portion.  Adjust the segment so that "
      "`intercept + slope * V >= 0` for all V in [V_low, V_high].",
      ddl.name,
      res.name,
      k,
      v.count,
      num_stages,
      v.worst_stage,
      v.worst_range.v_low,
      v.worst_range.v_high,
      v.worst_fr.fmin(),
      v.worst_fr.fmax(),
      ddl.segments[k].slope,
      ddl.segments[k].intercept);
}

}  // namespace

void check_piecewise_feasibility(ValidationResult& result,
                                 const Planning& planning)
{
  const auto& sys = planning.system;
  const auto num_stages = planning.simulation.stage_array.size();
  // **ReservoirSeepage** — per-segment range feasibility.
  //
  // The LP constraint per (block, stage) is the equality
  //   `qfilt − slope_k · efin = constant_k`
  // → `qfilt = constant_k + slope_k · efin`.
  // The flow `qfilt` lives on the seepage's waterway and is bounded
  // `qfilt ∈ [fmin, fmax]`.  For each segment k, while it's the
  // active piecewise piece (i.e. `efin ∈ [V_low_k, V_high_k]`, where
  // V_low_0 is clamped to the reservoir's emin and V_high_{N-1} is
  // clamped to emax), we need:
  //
  //   fmin  ≤  constant_k + slope_k · efin  ≤  fmax,  ∀ efin ∈ [V_low_k,
  //   V_high_k]
  //
  // For schedule-form `emin/emax/fmin/fmax` (vector-per-stage), the
  // check fires per-stage and is summarised: ONE warning per
  // (element, segment, direction) listing how many stages fail and
  // pointing at the worst offender.  File-schedules (string paths)
  // are still deferred — we don't load them here.
  for (const auto& seep : sys.reservoir_seepage_array) {
    if (seep.segments.empty()) {
      continue;
    }
    const auto* res = find_reservoir(sys.reservoir_array, seep.reservoir);
    if (res == nullptr) {
      continue;  // referential integrity check elsewhere catches missing refs
    }
    const auto* ww = find_waterway(sys.waterway_array, seep.waterway);
    if (ww == nullptr) {
      continue;
    }
    const auto envs = resolve_seepage_envelopes(*res, *ww, num_stages);
    if (!envs.has_value()) {
      continue;  // some field is file-schedule — truly deferred
    }

    for (std::size_t k = 0; k < seep.segments.size(); ++k) {
      const auto& seg = seep.segments[k];
      const auto [below, above] = scan_segment_per_stage(seep.segments,
                                                         k,
                                                         seg.slope,
                                                         seg.constant,
                                                         envs->emins,
                                                         envs->emaxs,
                                                         envs->fmins,
                                                         envs->fmaxs);
      if (below.count > 0) {
        result.warnings.push_back(format_seepage_below_warning(
            seep, *res, *ww, k, num_stages, below));
      }
      if (above.count > 0) {
        result.warnings.push_back(format_seepage_above_warning(
            seep, *res, *ww, k, num_stages, above));
      }
    }
  }

  // **ReservoirDischargeLimit** — per-segment range feasibility.
  //
  // The LP row is `qeh − slope_k · efin ≤ intercept_k`, where qeh has
  // lower bound 0 (default for a free non-negative col).  For each
  // segment k active at efin ∈ [V_low_k, V_high_k] at stage s:
  //
  //   0 ≤ intercept_k + slope_k · efin   for all efin in the range.
  //
  // Per-stage scan with the same dedup semantics as the seepage
  // check above: ONE warning per (DDL, segment) summarising the
  // count of failing stages plus the worst case.  Only the lower
  // bound (≥ 0) matters; upper-bound is +∞ so the "above" branch of
  // `scan_segment_per_stage` never fires.
  const std::vector<Real> ddl_lower(num_stages, 0.0);
  const std::vector<Real> ddl_upper(num_stages,
                                    std::numeric_limits<Real>::infinity());
  for (const auto& ddl : sys.reservoir_discharge_limit_array) {
    if (ddl.segments.empty()) {
      continue;
    }
    const auto* res = find_reservoir(sys.reservoir_array, ddl.reservoir);
    if (res == nullptr) {
      continue;
    }
    const auto envs = resolve_discharge_limit_envelopes(*res, num_stages);
    if (!envs.has_value()) {
      continue;
    }

    for (std::size_t k = 0; k < ddl.segments.size(); ++k) {
      const auto& seg = ddl.segments[k];
      const auto [below, _above] = scan_segment_per_stage(ddl.segments,
                                                          k,
                                                          seg.slope,
                                                          seg.intercept,
                                                          envs->emins,
                                                          envs->emaxs,
                                                          ddl_lower,
                                                          ddl_upper);
      if (below.count > 0) {
        result.warnings.push_back(
            format_discharge_limit_warning(ddl, *res, k, num_stages, below));
      }
    }
  }
}

/// Validate range constraints on simulation parameters.
[[nodiscard]] std::optional<double> scalar_segment_cost(
    const FailSegment& seg) noexcept
{
  if (seg.cost) {
    if (const auto* v = std::get_if<Real>(&*seg.cost)) {
      return *v;
    }
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<double> scalar_segment_cost(
    const ScalarFailSegment& seg) noexcept
{
  return seg.cost;
}

/// Validate one stepwise demand-failure curve (`Demand.fail_segments` or
/// `model_options.demand_fail_segments`).  `owner` labels the messages.
template<typename Segment>
void check_fail_segment_list(ValidationResult& result,
                             std::span<const Segment> segs,
                             std::string_view owner)
{
  if (segs.empty()) {
    return;
  }
  if (segs.size() > max_fail_segments) {
    result.errors.push_back(
        std::format("{}: fail_segments has {} segments (max {})",
                    owner,
                    segs.size(),
                    max_fail_segments));
  }
  double cum = 0.0;
  std::optional<double> prev_cost;
  bool warned_decreasing = false;
  for (std::size_t k = 0; k < segs.size(); ++k) {
    const auto& seg = segs[k];
    const bool last = (k + 1 == segs.size());
    if (seg.fraction) {
      if (*seg.fraction <= 0.0 || *seg.fraction > 1.0) {
        result.errors.push_back(
            std::format("{}: fail_segments[{}].fraction = {} must be in (0, 1]",
                        owner,
                        k,
                        *seg.fraction));
      }
      cum += *seg.fraction;
    } else if (!last) {
      result.errors.push_back(std::format(
          "{}: fail_segments[{}].fraction is required (only the last "
          "segment may be open)",
          owner,
          k));
    } else if (cum >= 1.0 - 1e-9) {
      result.errors.push_back(std::format(
          "{}: open last fail segment is empty (previous fractions sum to "
          "{})",
          owner,
          cum));
    }
    if (!seg.cost) {
      result.errors.push_back(
          std::format("{}: fail_segments[{}].cost is required", owner, k));
      continue;
    }
    if (const auto scalar = scalar_segment_cost(seg)) {
      if (prev_cost && *scalar < *prev_cost && !warned_decreasing) {
        result.warnings.push_back(std::format(
            "{}: fail_segments costs decrease at segment {} ({} < {}); the "
            "LP curtails the cheapest segment first regardless of order "
            "(non-convex curves are not representable)",
            owner,
            k,
            *scalar,
            *prev_cost));
        warned_decreasing = true;
      }
      prev_cost = scalar;
    }
  }
  if (cum > 1.0 + 1e-9) {
    result.errors.push_back(
        std::format("{}: fail_segments fractions sum to {} (> 1)", owner, cum));
  }
}

void check_fail_segments(ValidationResult& result, const Planning& planning)
{
  const auto& global = planning.options.model_options.demand_fail_segments;
  if (global) {
    check_fail_segment_list(result,
                            std::span<const ScalarFailSegment> {*global},
                            "model_options.demand_fail_segments");
  }
  const auto& sys = planning.system;
  std::unordered_set<Uid> seg_uids;
  std::unordered_set<std::string_view> seg_names;
  for (const auto& dem : sys.demand_array) {
    if (dem.fail_segments.empty()) {
      continue;
    }
    const auto owner = std::format("Demand '{}'", dem.name);
    check_fail_segment_list(
        result, std::span<const FailSegment> {dem.fail_segments}, owner);
    if (dem.fcost) {
      result.warnings.push_back(std::format(
          "{}: both fcost and fail_segments set; fail_segments takes "
          "precedence",
          owner));
    }
    if (dem.forced.value_or(false)) {
      result.warnings.push_back(std::format(
          "{}: forced demand ignores fail_segments (no curtailment)", owner));
    }
    if (dem.fail_segments.size() > 1) {
      seg_uids.insert(dem.uid);
      seg_names.insert(dem.name);
    }
  }
  const auto is_seg = [&](const SingleId& sid)
  {
    if (const auto* u = std::get_if<Uid>(&sid)) {
      return seg_uids.contains(*u);
    }
    return seg_names.contains(std::get<Name>(sid));
  };
  for (const auto& dp : sys.demand_profile_array) {
    if (is_seg(dp.demand)) {
      result.errors.push_back(std::format(
          "DemandProfile '{}': demand {} uses fail_segments (not supported "
          "with a DemandProfile)",
          dp.name,
          format_single_id(dp.demand)));
    }
  }
  for (const auto& conv : sys.converter_array) {
    if (is_seg(conv.demand)) {
      result.errors.push_back(std::format(
          "Converter '{}': demand {} uses fail_segments (a converter "
          "charge demand must be a single load column)",
          conv.name,
          format_single_id(conv.demand)));
    }
  }
}

void check_ranges(ValidationResult& result, const Planning& planning)
{
  const auto& sim = planning.simulation;

  // Block duration > 0
  for (const auto& blk : sim.block_array) {
    if (blk.duration <= 0.0) {
      result.errors.push_back(std::format(
          "Block uid={}: duration ({}) must be > 0", blk.uid, blk.duration));
    }
  }

  // Stage count_block > 0
  for (const auto& stg : sim.stage_array) {
    if (stg.count_block == 0) {
      result.errors.push_back(
          std::format("Stage uid={}: count_block must be > 0", stg.uid));
    }
  }

  // Generator capacity non-negative (warning only)
  for (const auto& gen : planning.system.generator_array) {
    if (gen.capacity.has_value()) {
      const auto& cap = gen.capacity.value();
      if (std::holds_alternative<Real>(cap) && std::get<Real>(cap) < 0.0) {
        result.warnings.push_back(
            std::format("Generator '{}': capacity ({}) is negative",
                        gen.name,
                        std::get<Real>(cap)));
      }
    }
  }
}

/// Validate that every UID listed in `phase.apertures` resolves to an
/// entry in `simulation.aperture_array`.
///
/// Pre-fix history: silent fallback at runtime — phases with
/// dangling aperture UIDs would emit
/// `SDDP Aperture [...]: source_scenario X not found and no aperture
/// cache, skipping` for each broken reference, then proceed with the
/// remaining apertures.  Easy to misread as expected behaviour
/// (see the original the large hydrothermal case configuration where 2 of 16
/// per-phase aperture UIDs referenced uids 1 and 2 but the global
/// `aperture_array` defined uids 51..66 only).  Surfacing this as a
/// validation error catches the input-data bug at parse time, before
/// the SDDP run silently quietly drops cuts.
void check_aperture_references(ValidationResult& result,
                               const Planning& planning)
{
  const auto& sim = planning.simulation;

  // Build a set of aperture UIDs declared in the global array.
  // Empty aperture_array is fine — it disables apertures globally.
  if (sim.aperture_array.empty()) {
    return;
  }

  std::vector<int> known_uids;
  known_uids.reserve(sim.aperture_array.size());
  for (const auto& ap : sim.aperture_array) {
    known_uids.push_back(ap.uid);
  }
  std::ranges::sort(known_uids);

  for (const auto& ph : sim.phase_array) {
    if (ph.apertures.empty()) {
      continue;
    }
    for (const auto& uid : ph.apertures) {
      const auto u = uid;
      if (!std::ranges::binary_search(known_uids, u)) {
        result.errors.push_back(std::format(
            "Phase uid={}: aperture uid={} listed in `apertures` does not "
            "exist in `simulation.aperture_array` (declared uids range from "
            "{} to {}).  Either add the aperture definition or remove the "
            "uid from this phase's list.",
            ph.uid,
            u,
            known_uids.front(),
            known_uids.back()));
      }
    }
  }
}

/// Validate structural completeness.
void check_completeness(ValidationResult& result, const Planning& planning)
{
  if (planning.system.bus_array.empty()) {
    result.errors.emplace_back("System has no buses defined");
  }

  if (planning.simulation.block_array.empty()) {
    result.errors.emplace_back("Simulation has no blocks defined");
  }

  if (planning.simulation.stage_array.empty()) {
    result.errors.emplace_back("Simulation has no stages defined");
  }
}

/// Tolerance for probability sum comparison.
constexpr double prob_tolerance = 1e-6;

/// A probability total outside [0.5, 2] is far enough from 1.0 to look
/// like representative-period weights (e.g. day counts) rather than
/// probabilities.
constexpr double prob_far_low = 0.5;
constexpr double prob_far_high = 2.0;

/// Format relative scene weights as "w1/w2/..." (first few scenes only).
[[nodiscard]] std::string format_scene_weights(std::span<const double> sums,
                                               double total)
{
  constexpr std::size_t max_shown = 8;
  std::string out;
  for (const auto [i, s] : std::views::enumerate(sums)) {
    if (std::cmp_greater_equal(i, max_shown)) {
      out += std::format("/... ({} scenes)", sums.size());
      break;
    }
    out += std::format("{}{:.6f}", i == 0 ? "" : "/", s / total);
  }
  return out;
}

/// Check and optionally rescale scenario probabilities.
///
/// Scenario `probability_factor` values are GLOBAL probabilities: the
/// weight of a scene is the sum of its scenarios' factors (see
/// `compute_scene_weights` / `SceneLP::probability_factor`).  When the
/// total over all scenes is not 1.0 a warning is always emitted and,
/// unless `probability_rescale = none`, the values are rescaled:
///
///  1. total ~ 1.0: valid global distribution, left untouched;
///  2. every scene sums to ~1.0 (more than one scene): the input is read
///     as conditional per-scene probabilities and the scenes get equal
///     weight (every factor divided by the number of scenes);
///  3. any other total: every factor is divided by the global total,
///     which preserves the relative scene weights but scales the
///     expected OPEX relative to CAPEX.
///
/// Rescaling never turns into an error.
void check_scenario_probabilities(ValidationResult& result, Planning& planning)
{
  const auto mode = planning.simulation.probability_rescale.value_or(
      ProbabilityRescaleMode::runtime);
  const bool do_rescale = (mode != ProbabilityRescaleMode::none);

  auto& scenarios = planning.simulation.scenario_array;
  const auto& scenes = planning.simulation.scene_array;

  if (scenarios.empty()) {
    return;
  }

  // Resolve each scene's scenario range first (reporting out-of-range
  // scenes).  Without explicit scenes all scenarios form one implicit
  // scene.
  struct SceneRange
  {
    std::size_t first {};
    std::size_t count {};
  };
  std::vector<SceneRange> ranges;
  if (scenes.empty()) {
    ranges.push_back({
        .first = 0,
        .count = scenarios.size(),
    });
  } else {
    ranges.reserve(scenes.size());
    for (const auto& scene : scenes) {
      const auto first = scene.first_scenario;
      const auto count = (scene.count_scenario == std::dynamic_extent)
          ? (scenarios.size() - std::min(first, scenarios.size()))
          : scene.count_scenario;

      if (first + count > scenarios.size()) {
        result.errors.push_back(std::format(
            "Scene '{}': first_scenario ({}) + count_scenario ({}) exceeds "
            "scenario_array size ({})",
            scene.name.value_or("?"),
            first,
            count,
            scenarios.size()));
        continue;
      }
      ranges.push_back({
          .first = first,
          .count = count,
      });
    }
  }
  if (ranges.empty()) {
    return;
  }

  std::vector<double> sums;
  sums.reserve(ranges.size());
  for (const auto& r : ranges) {
    double s = 0.0;
    for (std::size_t i = r.first; i < r.first + r.count; ++i) {
      s += scenarios[i].probability_factor.value_or(1.0);
    }
    sums.push_back(s);
  }
  const double total = std::ranges::fold_left(sums, 0.0, std::plus {});

  // Case 1: an already globally normalised distribution is valid as is:
  // the per-scene sums ARE the scene weights and must not be touched.
  if (std::abs(total - 1.0) <= prob_tolerance) {
    return;
  }

  const auto num_scenes = ranges.size();
  const bool far_from_one = total > prob_far_high || total < prob_far_low;
  const std::string_view period_hint = far_from_one
      ? "  The values look like representative-period weights (e.g. day "
        "counts); in gtopt such weights belong in the block/stage "
        "durations, not in Scenario probability_factor"
      : "";

  result.warnings.push_back(
      std::format("Scenario probability_factor values sum to {:.6f} across "
                  "{} scene(s) (expected 1.0)",
                  total,
                  num_scenes));

  const auto divide_all = [&](double divisor)
  {
    for (const auto& r : ranges) {
      for (std::size_t i = r.first; i < r.first + r.count; ++i) {
        const double p = scenarios[i].probability_factor.value_or(1.0);
        scenarios[i].probability_factor = p / divisor;
      }
    }
  };

  // Case 2 test: every scene sums to 1.0 -> conditional per-scene
  // probabilities.
  const bool conditional =
      num_scenes > 1
      && std::ranges::all_of(
          sums, [](double s) { return std::abs(s - 1.0) <= prob_tolerance; });

  if (!do_rescale) {
    result.warnings.push_back(std::format(
        "probability_rescale = none: probability_factor values left "
        "unchanged, so the expected OPEX is weighted by a total probability "
        "of {:.6f} relative to CAPEX.{}",
        total,
        conditional ? "  Every scene sums to 1.0 (conditional per-scene "
                      "probabilities); enable probability_rescale to give "
                      "the scenes equal weight"
                    : period_hint));
    return;
  }

  if (total <= 0.0) {
    result.warnings.emplace_back(
        "Scenario probability_factor total is not positive: probabilities "
        "were not rescaled");
    return;
  }

  // Case 2: conditional per-scene probabilities -> equal scene weights.
  if (conditional) {
    divide_all(static_cast<double>(num_scenes));
    result.warnings.push_back(std::format(
        "Every scene's scenario probability_factor values sum to 1.0: the "
        "input was interpreted as CONDITIONAL per-scene probabilities and "
        "all probability_factor values were divided by the number of scenes "
        "({}), so each scene gets equal weight {:.6f}.  If the scenes should "
        "have different weights, give global probabilities that sum to 1.0 "
        "over all scenes",
        num_scenes,
        1.0 / static_cast<double>(num_scenes)));
    return;
  }

  // Case 3: any other total -> divide by the global total, preserving the
  // relative scene weights.
  divide_all(total);
  result.warnings.push_back(std::format(
      "Rescaled scenario probability_factor values: all were divided by "
      "their total {:.6f} so they sum to 1.0 (relative scene weights "
      "preserved: {}).  This changes the expected OPEX relative to CAPEX "
      "by a factor 1/{:.6f}.{}",
      total,
      format_scene_weights(sums, total),
      total,
      period_hint));
}

}  // namespace

namespace
{

/// String-typed `model_options` enums (`line_losses_mode`,
/// `kirchhoff_mode`, `tiny_reactance_mode`, `objective_mode`) are resolved
/// lazily by `PlanningOptionsLP`, which maps an unrecognised name to the
/// default.  A typo such as `"line_losses_mode": "linaer"` therefore ran
/// silently with the default formulation.  Reject it up front, for the
/// global model options and every cascade-level override.
void check_model_option_names(ValidationResult& result,
                              const ModelOptions& mo,
                              std::string_view where)
{
  const auto bad = [&](std::string_view field,
                       const std::string& value,
                       std::string_view expected)
  {
    result.errors.push_back(
        std::format("{}.{}: unknown value '{}' (expected one of: {})",
                    where,
                    field,
                    value,
                    expected));
  };
  const auto names = []<typename E>(std::type_identity<E>)
  {
    std::string out;
    for (const auto& entry : enum_entries(E {})) {
      if (!out.empty()) {
        out += ", ";
      }
      out += entry.name;
    }
    return out;
  };
  if (mo.line_losses_mode
      && !enum_from_name<LineLossesMode>(*mo.line_losses_mode))
  {
    bad("line_losses_mode",
        *mo.line_losses_mode,
        names(std::type_identity<LineLossesMode> {}));
  }
  if (mo.kirchhoff_mode && !enum_from_name<KirchhoffMode>(*mo.kirchhoff_mode)) {
    bad("kirchhoff_mode",
        *mo.kirchhoff_mode,
        names(std::type_identity<KirchhoffMode> {}));
  }
  if (mo.tiny_reactance_mode
      && !enum_from_name<TinyReactanceMode>(*mo.tiny_reactance_mode))
  {
    bad("tiny_reactance_mode",
        *mo.tiny_reactance_mode,
        names(std::type_identity<TinyReactanceMode> {}));
  }
  if (const auto& om = mo.objective_mode;
      om && *om != "cost" && *om != "emissions" && *om != "emission")
  {
    bad("objective_mode", *om, "cost, emissions");
  }
  if (mo.pricing_mode && !enum_from_name<PricingMode>(*mo.pricing_mode)) {
    bad("pricing_mode",
        *mo.pricing_mode,
        names(std::type_identity<PricingMode> {}));
  }
  if (mo.fast_start_max_hours && *mo.fast_start_max_hours < 0.0) {
    result.errors.push_back(
        std::format("{}.fast_start_max_hours: must be >= 0 (got {})",
                    where,
                    *mo.fast_start_max_hours));
  }
}

void check_model_options(ValidationResult& result, const Planning& planning)
{
  const auto& opts = planning.options;
  check_model_option_names(result, opts.model_options, "model_options");
  check_model_option_names(result,
                           opts.cascade_options.model_options,
                           "cascade_options.model_options");
  for (const auto& [i, level] :
       std::views::enumerate(opts.cascade_options.level_array))
  {
    if (level.model_options) {
      check_model_option_names(
          result,
          *level.model_options,
          std::format("cascade_options.level_array[{}].model_options", i));
    }
  }
}

/// Comma-separated canonical (non-alias) names of enum @p E.
template<typename E>
[[nodiscard]] std::string enum_names_list()
{
  std::string out;
  for (const auto& entry : enum_entries(E {})) {
    if (entry.is_alias) {
      continue;
    }
    if (!out.empty()) {
      out += ", ";
    }
    out += entry.name;
  }
  return out;
}

/// Reject an unknown per-element mode string `<class> '<name>'.<field>`.
template<typename E, typename Elem>
void check_element_enum(ValidationResult& result,
                        std::string_view class_name,
                        const Elem& elem,
                        std::string_view field,
                        const OptName& value)
{
  if (value && !enum_from_name<E>(*value)) {
    result.errors.push_back(
        std::format("{} '{}' (uid {}): {}: unknown value '{}' "
                    "(expected one of: {})",
                    class_name,
                    elem.name,
                    elem.uid,
                    field,
                    *value,
                    enum_names_list<E>()));
  }
}

/// Per-element mode strings (e.g. `Line.line_losses_mode`) are parsed
/// lazily by the LP layer, which maps an unknown name to the default (or
/// the global setting) with at most a log line, so a typo silently changed
/// the model.  Reject them up front.
void check_element_mode_names(ValidationResult& result, const System& sys)
{
  for (const auto& line : sys.line_array) {
    check_element_enum<LineLossesMode>(
        result, "Line", line, "line_losses_mode", line.line_losses_mode);
    check_element_enum<LinePwlLayout>(
        result, "Line", line, "loss_pwl_layout", line.loss_pwl_layout);
    check_element_enum<LossAllocationMode>(result,
                                           "Line",
                                           line,
                                           "loss_allocation_mode",
                                           line.loss_allocation_mode);
  }
  for (const auto& gen : sys.generator_array) {
    check_element_enum<CogenMode>(
        result, "Generator", gen, "cogen_mode", gen.cogen_mode);
  }
  for (const auto& fr : sys.flow_right_array) {
    check_element_enum<FlowRightMode>(
        result, "FlowRight", fr, "flow_mode", fr.flow_mode);
  }
  for (const auto& dv : sys.decision_variable_array) {
    check_element_enum<ConstraintScaleType>(
        result, "DecisionVariable", dv, "cost_type", dv.cost_type);
  }
  for (const auto& uc : sys.user_constraint_array) {
    check_element_enum<ConstraintScaleType>(
        result, "UserConstraint", uc, "constraint_type", uc.constraint_type);
  }
}

}  // namespace

namespace
{

/// Gas-network elements: everything that depends only on the input data
/// is rejected here, all errors together, instead of one at a time from
/// inside `add_to_lp` (or, worse, as a silent clamp / an `inf` matrix
/// coefficient).
void check_gas_elements(ValidationResult& result, const System& sys)
{
  const auto err = [&](std::string_view cls,
                       const auto& elem,
                       std::string_view field,
                       const std::string& what)
  {
    result.errors.push_back(std::format(
        "{} '{}' (uid {}): {}: {}", cls, elem.name, elem.uid, field, what));
  };

  for (const auto& pipe : sys.gas_pipe_array) {
    static constexpr std::string_view cls = "GasPipe";
    check_element_enum<GasWeymouthMode>(
        result, cls, pipe, "weymouth_mode", pipe.weymouth_mode);
    check_element_enum<GasLinearization>(
        result, cls, pipe, "linearization", pipe.linearization);
    check_element_enum<gas::TangentLayout>(
        result, cls, pipe, "tangent_layout", pipe.tangent_layout);
    if (!pipe.fmax.has_value()) {
      err(cls, pipe, "fmax", "is required (the pipe capacity)");
    } else if (const auto v = try_scalar_value(pipe.fmax); v && *v < 0.0) {
      err(cls, pipe, "fmax", std::format("{} < 0 (expected >= 0)", *v));
    }
    if (pipe.linepack.value_or(false)) {
      if (const auto pr = pipe.linepack_pressure_ref; pr && !(*pr > 0.0)) {
        err(cls,
            pipe,
            "linepack_pressure_ref",
            std::format("{} (expected > 0: the line-pack linearisation "
                        "divides by it)",
                        *pr));
      }
    }
  }

  for (const auto& cs : sys.gas_compressor_array) {
    static constexpr std::string_view cls = "GasCompressor";
    check_element_enum<GasCompressorFuelModel>(
        result, cls, cs, "fuel_model", cs.fuel_model);
    check_element_enum<GasCompressorDrive>(result, cls, cs, "drive", cs.drive);
    if (!cs.fmax.has_value()) {
      err(cls, cs, "fmax", "is required (the compressor capacity)");
    }
    const auto model =
        cs.fuel_model_enum().value_or(GasCompressorFuelModel::none);
    if (cs.bidirectional.value_or(false)
        && (cs.max_ratio.value_or(1.0) > 1.0 + 1e-12
            || model != GasCompressorFuelModel::none))
    {
      err(cls,
          cs,
          "bidirectional",
          "an open valve needs max_ratio 1 and fuel_model 'none'");
    }
    if (cs.drive_enum() == GasCompressorDrive::electric
        && model != GasCompressorFuelModel::none && !cs.bus.has_value())
    {
      err(cls, cs, "bus", "drive 'electric' needs a bus");
    }
  }
}

/// P0-14: the line pack is not a state variable — every stage restarts
/// from `linepack_ini`.  `gas_options.linepack_cyclic = false` lets a
/// stage draw the pack down to its pressure floor, so over several stages
/// the pack would refill for free at every stage boundary.  Allowed only
/// with a single stage.
void check_gas_linepack_horizon(ValidationResult& result,
                                const Planning& planning)
{
  if (planning.options.gas_options.linepack_cyclic.value_or(true)
      || planning.simulation.stage_array.size() <= 1)
  {
    return;
  }
  for (const auto& pipe : planning.system.gas_pipe_array) {
    if (pipe.linepack.value_or(false)) {
      result.errors.push_back(std::format(
          "GasPipe '{}' (uid {}): linepack: gas_options.linepack_cyclic = "
          "false needs a single stage ({} stages given): the line pack is "
          "not carried between stages, each one restarts from "
          "linepack_ini, so an open (non-cyclic) pack would refill for "
          "free at every stage boundary",
          pipe.name,
          pipe.uid,
          planning.simulation.stage_array.size()));
    }
  }
}

/// State-carrying storage: one contiguous active period.
///
/// A storage element that carries a state variable across stages /
/// phases (`storage_carries_state`, the same predicate the LP uses) may
/// be commissioned once (inactive → active) and retired once (active →
/// inactive), i.e. its per-stage `active` vector must read
/// `off* on* off*`.  Re-activation after a gap (on → off → on) is
/// rejected: the state dropped at retirement would re-enter unlinked
/// (the "teleporting state" of the add_to_lp audit).  Every offending
/// element is reported, with the stage / phase where it switches back
/// on.  Storage without a state variable (daily-cycle / per-stage
/// cyclic) may toggle freely.  A storage commissioned after the first
/// stage needs `eini` (the LP fixes its initial state there): missing
/// it is an error, except for a battery (@p eini_defaults_to_emin),
/// which gets one warning and starts at `emin`.  Per-stage vectors are checked;
/// scalar `active` cannot toggle and file schedules are not resolved here.
template<typename StorageElement>
void check_state_storage_activation(ValidationResult& result,
                                    const Planning& planning,
                                    std::span<const StorageElement> elements,
                                    std::string_view class_name,
                                    StorageStateDefaults defaults,
                                    bool eini_defaults_to_emin = false)
{
  const auto& stages = planning.simulation.stage_array;
  const auto& phases = planning.simulation.phase_array;
  const auto phase_uid_of = [&](std::size_t stage_index) -> std::optional<Uid>
  {
    for (const auto& ph : phases) {
      const auto first = static_cast<std::size_t>(ph.first_stage);
      const auto count = static_cast<std::size_t>(ph.count_stage);
      if (stage_index >= first
          && (count == std::dynamic_extent || stage_index - first < count))
      {
        return ph.uid;
      }
    }
    return std::nullopt;
  };

  for (const auto& e : elements) {
    if (!e.active.has_value() || !storage_carries_state(e, defaults)) {
      continue;
    }
    const auto* flags = std::get_if<std::vector<IntBool>>(&*e.active);
    if (flags == nullptr) {
      continue;
    }
    bool was_active = false;
    bool retired = false;
    for (std::size_t i = 0; i < flags->size(); ++i) {
      const bool is_active = (*flags)[i] != 0;
      if (is_active && !was_active && i > 0 && !e.eini.has_value()) {
        // Commissioned after the first stage: the LP pins the initial
        // state at `eini`.  Only a battery may default it to `emin`.
        const auto stage_uid =
            i < stages.size() ? std::format("{}", stages[i].uid) : "?";
        if (eini_defaults_to_emin) {
          result.warnings.push_back(std::format(
              "{} uid={} '{}': state-carrying storage is commissioned at "
              "stage uid={} (stage index {}) with no 'eini'; its initial "
              "state is fixed at 'emin'.  Set 'eini' to state the initial "
              "level explicitly",
              class_name,
              e.uid,
              e.name,
              stage_uid,
              i));
        } else {
          result.errors.push_back(std::format(
              "{} uid={} '{}': state-carrying storage is commissioned at "
              "stage uid={} (stage index {}, its first active stage) but "
              "has no 'eini'; the initial state at commissioning cannot be "
              "inherited from an earlier stage and must be given "
              "explicitly",
              class_name,
              e.uid,
              e.name,
              stage_uid,
              i));
        }
      }
      if (was_active && !is_active) {
        retired = true;
      }
      if (retired && is_active) {
        const auto stage_uid =
            i < stages.size() ? std::format("{}", stages[i].uid) : "?";
        const auto phase_uid = phase_uid_of(i);
        result.errors.push_back(std::format(
            "{} uid={} '{}': state-carrying storage becomes active again "
            "at stage uid={} (stage index {}{}) after having been retired; "
            "a storage with a state variable may be commissioned once "
            "(inactive -> active) and retired once (active -> inactive), "
            "but not switched back on (its state would re-enter "
            "unlinked).  Set use_state_variable=false / daily_cycle=true "
            "for a cyclic storage, or split it into two elements",
            class_name,
            e.uid,
            e.name,
            stage_uid,
            i,
            phase_uid ? std::format(", phase uid={}", *phase_uid) : ""));
        break;
      }
      was_active = was_active || is_active;
    }
  }
}

void check_storage_activation(ValidationResult& result,
                              const Planning& planning)
{
  namespace sd = storage_state_defaults;
  const auto& sys = planning.system;
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.reservoir_array},
                                 Reservoir::class_name.full_name(),
                                 sd::reservoir);
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.battery_array},
                                 Battery::class_name.full_name(),
                                 sd::battery,
                                 /*eini_defaults_to_emin=*/true);
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.lng_terminal_array},
                                 LngTerminal::class_name.full_name(),
                                 sd::lng_terminal);
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.thermal_storage_array},
                                 ThermalStorage::class_name.full_name(),
                                 sd::thermal_storage);
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.hydrogen_storage_array},
                                 HydrogenStorage::class_name.full_name(),
                                 sd::hydrogen_storage);
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.ammonia_storage_array},
                                 AmmoniaStorage::class_name.full_name(),
                                 sd::ammonia_storage);
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.volume_right_array},
                                 VolumeRight::class_name.full_name(),
                                 sd::volume_right);
  check_state_storage_activation(result,
                                 planning,
                                 std::span {sys.allowance_pool_array},
                                 AllowancePool::class_name.full_name(),
                                 sd::allowance_pool);
}

/// Activity of an element in stage @p i: nullopt when it cannot be decided
/// here (file schedule, vector shorter than the stage list).
[[nodiscard]] std::optional<bool> active_at(const OptActive& active,
                                            std::size_t i)
{
  if (!active.has_value()) {
    return true;
  }
  if (const auto* flag = std::get_if<IntBool>(&*active)) {
    return *flag != 0;
  }
  if (const auto* flags = std::get_if<std::vector<IntBool>>(&*active)) {
    if (i < flags->size()) {
      return (*flags)[i] != 0;
    }
  }
  return std::nullopt;
}

template<typename Element>
[[nodiscard]] const Element* find_by_sid(const std::vector<Element>& elements,
                                         const SingleId& sid)
{
  for (const auto& e : elements) {
    const bool match = std::holds_alternative<Uid>(sid)
        ? e.uid == std::get<Uid>(sid)
        : e.name == std::get<Name>(sid);
    if (match) {
      return &e;
    }
  }
  return nullptr;
}

/// Water rights must not draw on a water source that is not in the
/// system: a VolumeRight whose `reservoir` is inactive, or a FlowRight
/// whose `junction_a` (source) is inactive, in a stage where the right is
/// active would be served without physical water (and a non-consumptive
/// right would credit `junction_b` with water debited nowhere).  Reported
/// once per (right, first offending stage).
void check_water_right_sources(ValidationResult& result,
                               const Planning& planning)
{
  const auto& stages = planning.simulation.stage_array;
  const auto& phases = planning.simulation.phase_array;
  const auto& sys = planning.system;
  const auto where = [&](std::size_t i)
  {
    std::string text = std::format("stage uid={}", stages[i].uid);
    for (const auto& ph : phases) {
      const auto first = static_cast<std::size_t>(ph.first_stage);
      const auto count = static_cast<std::size_t>(ph.count_stage);
      if (i >= first && (count == std::dynamic_extent || i - first < count)) {
        text += std::format(", phase uid={}", ph.uid);
        break;
      }
    }
    return text;
  };
  const auto first_conflict =
      [&](const OptActive& right_active,
          const OptActive& source_active) -> std::optional<std::size_t>
  {
    for (std::size_t i = 0; i < stages.size(); ++i) {
      if (active_at(right_active, i).value_or(false)
          && !active_at(source_active, i).value_or(true))
      {
        return i;
      }
    }
    return std::nullopt;
  };

  for (const auto& vr : sys.volume_right_array) {
    if (!vr.reservoir.has_value()) {
      continue;
    }
    const auto* rsv = find_by_sid(sys.reservoir_array, *vr.reservoir);
    if (rsv == nullptr) {
      continue;  // unresolved reference: reported by referential integrity
    }
    if (const auto i = first_conflict(vr.active, rsv->active)) {
      result.errors.push_back(std::format(
          "VolumeRight uid={} '{}': its reservoir uid={} '{}' is inactive "
          "at {} while the right is active; the right would withdraw "
          "water that is not in the system.  Deactivate the right there or "
          "keep the reservoir active",
          vr.uid,
          vr.name,
          rsv->uid,
          rsv->name,
          where(*i)));
    }
  }

  for (const auto& fr : sys.flow_right_array) {
    if (!fr.junction_a.has_value()) {
      continue;
    }
    const auto* ja = find_by_sid(sys.junction_array, *fr.junction_a);
    if (ja == nullptr) {
      continue;  // unresolved reference: reported by referential integrity
    }
    if (const auto i = first_conflict(fr.active, ja->active)) {
      const bool consumptive = fr.consumptive.value_or(true);
      result.errors.push_back(std::format(
          "FlowRight uid={} '{}': its source junction_a uid={} '{}' is "
          "inactive at {} while the right is active; the right would be "
          "served without water{}.  Deactivate the right there or keep "
          "the junction active",
          fr.uid,
          fr.name,
          ja->uid,
          ja->name,
          where(*i),
          consumptive ? ""
                      : " (and its return flow would create water at "
                        "junction_b)"));
    }
  }
}

/// "stage uid=…, phase uid=…" label for global stage index @p i.
[[nodiscard]] std::string stage_phase_label(const Planning& planning,
                                            std::size_t i)
{
  const auto& stages = planning.simulation.stage_array;
  std::string text = std::format("stage uid={}", stages[i].uid);
  for (const auto& ph : planning.simulation.phase_array) {
    const auto first = static_cast<std::size_t>(ph.first_stage);
    const auto count = static_cast<std::size_t>(ph.count_stage);
    if (i >= first && (count == std::dynamic_extent || i - first < count)) {
      text += std::format(", phase uid={}", ph.uid);
      break;
    }
  }
  return text;
}

/// First stage where the coupler (@p coupler_active) and its partner
/// (@p partner_active) disagree.  With @p both_ways false only "coupler
/// active, partner inactive" counts (shared partners — a junction, a gas
/// node, a bus — are legitimately active without this coupler).  Stages
/// whose activity cannot be decided here (file schedules) are skipped.
[[nodiscard]] std::optional<std::size_t> first_coupling_divergence(
    std::size_t nstages,
    const OptActive& coupler_active,
    const OptActive& partner_active,
    bool both_ways)
{
  for (std::size_t i = 0; i < nstages; ++i) {
    const auto c = active_at(coupler_active, i);
    const auto p = active_at(partner_active, i);
    if (!c || !p) {
      continue;
    }
    if ((*c && !*p) || (both_ways && !*c && *p)) {
      return i;
    }
  }
  return std::nullopt;
}

/// Couplers (Turbine, GasTurbine, Converter, Pump, electric
/// GasCompressor) are the only path through which their partner converts
/// a resource.  A coupled pair with divergent `active` schedules leaves a
/// stage where the conversion row cannot be written: the LP closes the
/// partner's producing column there (fail-closed backstop, see
/// coupler_fail_closed.hpp), but the input is inconsistent and is
/// reported here.  Also rejected: two turbines on one generator, and a
/// `must_run` commitment whose unit's coupler is inactive (the backstop
/// would make it infeasible).
void check_coupler_activation(ValidationResult& result,
                              const Planning& planning)
{
  const auto& sys = planning.system;
  const auto nstages = planning.simulation.stage_array.size();

  const auto check_pair = [&](std::string_view cls,
                              Uid uid,
                              std::string_view name,
                              const OptActive& active,
                              std::string_view role,
                              std::string_view pcls,
                              const auto* partner,
                              bool both_ways)
  {
    if (partner == nullptr) {
      return;  // unresolved reference: reported by referential integrity
    }
    const auto i =
        first_coupling_divergence(nstages, active, partner->active, both_ways);
    if (!i) {
      return;
    }
    const bool coupler_on = active_at(active, *i).value_or(true);
    result.errors.push_back(std::format(
        "{} uid={} '{}': its {} {} uid={} '{}' is {} at {} while the {} is "
        "{}; a coupler and its partner must share the active schedule "
        "(the conversion row cannot be written there)",
        cls,
        uid,
        name,
        role,
        pcls,
        partner->uid,
        partner->name,
        coupler_on ? "inactive" : "active",
        stage_phase_label(planning, *i),
        cls,
        coupler_on ? "active" : "inactive"));
  };

  // generator → turbines feeding it (two turbines on one generator).
  // Keyed by array index so the report order is deterministic.
  std::map<std::size_t, std::vector<const Turbine*>> gen_turb;
  for (const auto& t : sys.turbine_array) {
    const auto cls = Turbine::class_name.full_name();
    const auto* gen = find_by_sid(sys.generator_array, t.generator);
    if (gen != nullptr) {
      const auto gidx = static_cast<std::size_t>(
          std::distance(sys.generator_array.data(), gen));
      gen_turb[gidx].push_back(&t);
    }
    check_pair(
        cls, t.uid, t.name, t.active, "generator", "Generator", gen, true);
    if (t.flow) {
      check_pair(cls,
                 t.uid,
                 t.name,
                 t.active,
                 "flow",
                 "Flow",
                 find_by_sid(sys.flow_array, *t.flow),
                 true);
    }
    if (t.waterway) {
      check_pair(cls,
                 t.uid,
                 t.name,
                 t.active,
                 "waterway",
                 "Waterway",
                 find_by_sid(sys.waterway_array, *t.waterway),
                 false);
    }
    if (t.junction_a) {
      check_pair(cls,
                 t.uid,
                 t.name,
                 t.active,
                 "junction_a",
                 "Junction",
                 find_by_sid(sys.junction_array, *t.junction_a),
                 false);
    }
    if (t.junction_b) {
      check_pair(cls,
                 t.uid,
                 t.name,
                 t.active,
                 "junction_b",
                 "Junction",
                 find_by_sid(sys.junction_array, *t.junction_b),
                 false);
    }
  }
  for (const auto& [gidx, turbs] : gen_turb) {
    if (turbs.size() > 1) {
      const auto& gen = sys.generator_array[gidx];
      std::string names;
      for (const auto* t : turbs) {
        names += std::format(
            "{}uid={} '{}'", names.empty() ? "" : ", ", t->uid, t->name);
      }
      result.errors.push_back(std::format(
          "Generator uid={} '{}' is fed by {} turbines ({}); a generator "
          "has at most one turbine",
          gen.uid,
          gen.name,
          turbs.size(),
          names));
    }
  }

  for (const auto& gt : sys.gas_turbine_array) {
    const auto cls = GasTurbine::class_name.full_name();
    check_pair(cls,
               gt.uid,
               gt.name,
               gt.active,
               "generator",
               "Generator",
               find_by_sid(sys.generator_array, gt.generator),
               true);
    check_pair(cls,
               gt.uid,
               gt.name,
               gt.active,
               "gas_node",
               "GasNode",
               find_by_sid(sys.gas_node_array, gt.gas_node),
               false);
  }

  for (const auto& cv : sys.converter_array) {
    const auto cls = Converter::class_name.full_name();
    check_pair(cls,
               cv.uid,
               cv.name,
               cv.active,
               "battery",
               "Battery",
               find_by_sid(sys.battery_array, cv.battery),
               true);
    check_pair(cls,
               cv.uid,
               cv.name,
               cv.active,
               "generator",
               "Generator",
               find_by_sid(sys.generator_array, cv.generator),
               true);
  }

  for (const auto& pm : sys.pump_array) {
    const auto cls = Pump::class_name.full_name();
    check_pair(cls,
               pm.uid,
               pm.name,
               pm.active,
               "waterway",
               "Waterway",
               find_by_sid(sys.waterway_array, pm.waterway),
               true);
    check_pair(cls,
               pm.uid,
               pm.name,
               pm.active,
               "load demand",
               "Demand",
               find_by_sid(sys.demand_array, pm.demand),
               true);
  }

  for (const auto& cs : sys.gas_compressor_array) {
    if (cs.drive_enum() != GasCompressorDrive::electric) {
      continue;
    }
    const auto& bus_sid = cs.bus;
    if (!bus_sid.has_value()) {
      continue;
    }
    check_pair(GasCompressor::class_name.full_name(),
               cs.uid,
               cs.name,
               cs.active,
               "motor bus",
               "Bus",
               find_by_sid(sys.bus_array, *bus_sid),
               false);
  }

  // must_run unit whose coupler is inactive while unit + commitment are on.
  for (const auto& cm : sys.commitment_array) {
    if (!cm.must_run.value_or(false)) {
      continue;
    }
    const auto* gen = find_by_sid(sys.generator_array, cm.generator);
    if (gen == nullptr) {
      continue;
    }
    const auto report = [&](std::string_view ccls,
                            Uid cuid,
                            std::string_view cname,
                            const OptActive& cactive)
    {
      for (std::size_t i = 0; i < nstages; ++i) {
        if (active_at(cm.active, i).value_or(false)
            && active_at(gen->active, i).value_or(false)
            && !active_at(cactive, i).value_or(true))
        {
          result.errors.push_back(std::format(
              "Commitment uid={} '{}': must_run unit Generator uid={} '{}' "
              "has its {} uid={} '{}' inactive at {}; the unit cannot "
              "produce there, so must_run is infeasible",
              cm.uid,
              cm.name,
              gen->uid,
              gen->name,
              ccls,
              cuid,
              cname,
              stage_phase_label(planning, i)));
          return;
        }
      }
    };
    for (const auto& t : sys.turbine_array) {
      if (find_by_sid(sys.generator_array, t.generator) == gen) {
        report(Turbine::class_name.full_name(), t.uid, t.name, t.active);
      }
    }
    for (const auto& gt : sys.gas_turbine_array) {
      if (find_by_sid(sys.generator_array, gt.generator) == gen) {
        report(GasTurbine::class_name.full_name(), gt.uid, gt.name, gt.active);
      }
    }
    for (const auto& cv : sys.converter_array) {
      if (find_by_sid(sys.generator_array, cv.generator) == gen) {
        report(Converter::class_name.full_name(), cv.uid, cv.name, cv.active);
      }
    }
  }
}

}  // namespace

[[nodiscard]] ValidationResult validate_planning(Planning& planning)
{
  ValidationResult result;

  check_referential_integrity(result, planning.system);
  check_ranges(result, planning);
  check_model_options(result, planning);
  check_element_mode_names(result, planning.system);
  check_gas_elements(result, planning.system);
  check_gas_linepack_horizon(result, planning);
  if (planning.options.sddp_options.aperture_drop_fcuts.has_value()) {
    result.warnings.emplace_back(
        "sddp_options.aperture_drop_fcuts is deprecated and ignored (no "
        "solver code reads it; the backward pass never adds feasibility "
        "cuts) - remove it from the case");
  }
  check_positivity(result, planning.system);
  check_flow_right_anchoring(result, planning.system);
  check_bound_rules(result, planning.system);
  check_heat_rate(result, planning.system);
  check_reserve_planes(result, planning);
  check_offline_provisions(result, planning);
  check_commitment_stages(result, planning);
  check_storage_activation(result, planning);
  check_water_right_sources(result, planning);
  check_coupler_activation(result, planning);
  check_fail_segments(result, planning);
  check_piecewise_feasibility(result, planning);
  check_aperture_references(result, planning);
  check_completeness(result, planning);
  check_scenario_probabilities(result, planning);
  check_model_sanity(result, planning);

  // Gates for Optimal Transmission Switching (issue #509).  Count the
  // active LineCommitment rows once; both the method gate (rejects
  // SDDP / cascade) and the Kirchhoff-mode gate (rejects cycle_basis)
  // use it.
  int active_lc_count = 0;
  for (const auto& lc : planning.system.line_commitment_array) {
    // Treat unset / non-zero active as "active".  The OptActive
    // variant carries either a bool, a per-stage vector, or a name
    // — for the gate purpose we only need to detect rows explicitly
    // marked inactive at file scope.
    const bool is_inactive = lc.active.has_value()
        && std::holds_alternative<Int>(*lc.active)
        && std::get<Int>(*lc.active) == 0;
    if (!is_inactive) {
      ++active_lc_count;
    }
  }

  // Method gate for OTS.  OTS introduces per-line binary u_l decisions
  // that make Benders cuts unsound on SDDP / cascade subproblems
  // (cost-to-go nonconvexity — see issue body §"Why monolithic only,
  // not SDDP" + Zou-Ahmed-Sun 2019).  Reject upfront so the
  // misconfiguration surfaces at JSON load time rather than failing
  // mid-SDDP with an opaque error.  A future SDDiP-style relaxation
  // (Lagrangian cuts) would lift this restriction.
  const auto method = planning.options.method.value_or(MethodType::monolithic);
  if (method != MethodType::monolithic && active_lc_count > 0) {
    const std::string_view method_name = (method == MethodType::sddp)
        ? std::string_view {"sddp"}
        : std::string_view {"cascade"};
    result.errors.push_back(std::format(
        "Optimal Transmission Switching (OTS) is incompatible with the "
        "'{}' planning method: {} active LineCommitment row(s) found, "
        "but Benders cuts on a mixed-integer subproblem are unsound "
        "(Zou-Ahmed-Sun 2019).  Switch to method='monolithic' or "
        "deactivate the LineCommitment rows (issue #509).",
        method_name,
        active_lc_count));
  }

  // Issue #509 v0.6 ``cycle_basis`` gate REMOVED in v1: the cycle-form
  // big-M disjunctive rewrite is now implemented in
  // ``source/kirchhoff_cycle_basis.cpp::add_kvl_rows`` (per-cycle
  // ``M_C = 2θ_max · |C| · row_scale + Σ |φ_e| · row_scale``).  Both
  // Kirchhoff modes (``node_angle`` and ``cycle_basis``) now support
  // ``LineCommitment``; transport mode (``use_kirchhoff = false``)
  // continues to work under capacity gating alone.

  // Log all findings
  for (const auto& warn : result.warnings) {
    spdlog::warn("Validation: {}", warn);
  }
  for (const auto& err : result.errors) {
    spdlog::error("Validation: {}", err);
  }

  if (result.ok()) {
    spdlog::info("Planning validation passed");
  } else {
    spdlog::error("Planning validation failed with {} error(s)",
                  result.errors.size());
  }

  return result;
}

}  // namespace gtopt
