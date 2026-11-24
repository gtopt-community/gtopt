/**
 * @file      reserve_zone_lp.cpp
 * @brief     LP formulation for reserve zones (fixed and plane-based)
 * @date      Mon Apr 21 22:23:18 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */
#include <algorithm>
#include <expected>
#include <format>
#include <ranges>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include <gtopt/converter_lp.hpp>
#include <gtopt/error.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <spdlog/spdlog.h>

namespace
{
using namespace gtopt;

std::expected<void, Error> add_requirement(const std::string_view cname,
                                           const SystemContext& sc,
                                           const ScenarioLP& scenario,
                                           const StageLP& stage,
                                           LinearProblem& lp,
                                           const auto& blocks,
                                           const auto uid,
                                           auto& rr,
                                           const auto rname,
                                           const std::string_view surplus_name)
{
  using STKey = STBIndexHolder<ColIndex>::key_type;
  if (!rr.req && !rr.min) {
    return {};
  }

  BIndexHolder<ColIndex> rr_cols;
  BIndexHolder<RowIndex> rr_rows;
  map_reserve(rr_cols, blocks.size());
  map_reserve(rr_rows, blocks.size());

  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const auto block_rreq = rr.req.optval(stage.uid(), block.uid());
    const auto block_rmin = rr.min.optval(stage.uid(), block.uid());
    // Effective requirement = max(time-varying requirement, Min-Provision
    // floor).  Keeping ``min`` separate from ``req`` lets the JSON / PAMPL
    // ``req`` schedule mirror PLEXOS's reported per-block RHS while the static
    // floor still binds on low-requirement blocks (PLEXOS ``Min Provision``).
    double effective = 0.0;
    bool has_req = false;
    if (block_rreq) {
      effective = *block_rreq;
      has_req = true;
    }
    if (block_rmin) {
      effective = std::max(effective, *block_rmin);
      has_req = true;
    }
    if (!has_req) {
      continue;
    }
    const double block_rreq_eff = effective;

    // NOTE: do NOT skip the requirement row when ``block_rreq_eff == 0``.
    // Although ``Σ pf·prov ≥ 0`` can never bind, the row's *existence* is
    // load-bearing downstream: ``ReserveProvisionLP::add_provision`` builds
    // ``req_row_maps`` from these rows and, when that set is empty for a
    // (scenario, stage), returns early WITHOUT creating the provision
    // column at all.  A reserve provision referenced by a UserConstraint
    // (e.g. ``csf_rs_min_bat_del_desierto`` on PLEXOS 20260517) would then
    // lose its column, the resolver silent-zeros the term, and the LP goes
    // infeasible.  Eliminating this "always-slack" row is therefore NOT a
    // no-op; the LP-size saving is not worth the correctness hazard.

    // `rr.cost` is now per-(stage, block): resolve per block via the
    // overload that consults `model_options.reserve_shortage_cost` as
    // fallback (mirrors PR-A's `demand_fail_cost(stage, block, fcost)`).
    const auto block_rcost = sc.reserve_shortage_cost(stage, block, rr.cost);

    const auto block_context =
        make_block_context(scenario.uid(), stage.uid(), block.uid());

    SparseRow rr_row {
        .class_name = cname,
        .constraint_name = rname,
        .variable_uid = uid,
        .context = block_context,
    };

    // `requirement_sense: "eq"`: Σ pf·prov (− surplus) = served / req.
    if (rr.equality) {
      if (const auto scost = rr.surplus_cost.optval(stage.uid(), buid)) {
        const auto scol = lp.add_col({
            .cost = CostHelper::block_ecost(scenario, stage, block, *scost),
            .class_name = cname,
            .variable_name = surplus_name,
            .variable_uid = uid,
            .context = block_context,
        });
        rr.surplus_cols[{scenario.uid(), stage.uid()}][buid] = scol;
        rr_row[scol] = -1.0;
      }
    }
    const auto close_row = [&](double rhs)
    {
      if (rr.equality) {
        rr_row.equal(rhs);
      } else {
        rr_row.greater_equal(rhs);
      }
    };

    if (block_rcost) {
      // Priced slack branch (soft requirement): emit the bookkeeping
      // requirement column and the row `Σ pf · prov − rcol ≥ 0`.  The
      // column is bounded by `[0, block_rreq]` and carries a negative
      // objective coefficient so the LP picks `rcol = block_rreq`
      // whenever the provisions can supply it, and drops to a lower
      // value (paying the shortage penalty in the objective) otherwise.
      //
      // Met-requirement valuation constant: the column cost alone puts
      // `−c·rcol` in the objective, i.e. a fully met requirement would
      // report `−c·req` (a negative "benefit" that is not an operating
      // cost).  Add `+c·req` as an LP-external objective constant (same
      // mechanism as the demand-fail baseline) so the objective reads
      // `c·(req − rcol)` = the shortage penalty only.  The LP itself —
      // rows, columns, costs, bounds, hence dispatch and duals — is
      // unchanged; only the solver-native objective offset moves.
      const auto block_ecost =
          CostHelper::block_ecost(scenario, stage, block, block_rcost.value());
      lp.add_obj_constant(block_ecost * block_rreq_eff);
      const auto rcol = lp.add_col({
          .lowb = 0.0,
          .uppb = block_rreq_eff,
          .cost = -block_ecost,
          .class_name = cname,
          .variable_name = rname,
          .variable_uid = uid,
          .context = block_context,
      });
      rr_cols[buid] = rcol;
      rr_row[rcol] = -1;
      close_row(0.0);
      rr_rows[buid] = lp.add_row(std::move(rr_row));
    } else {
      // Hard requirement, no shortage cost: the legacy form built a
      // fully-pinned bookkeeping column (`lowb = uppb = block_rreq,
      // cost = 0`) and a row `Σ pf · prov − rcol ≥ 0`.  Substitute the
      // column out — move `block_rreq` to the row's RHS so the row
      // reads `Σ pf · prov ≥ block_rreq` directly.  Saves one LP
      // column per (zone, scenario, stage, block, up-or-dn) in every
      // configuration that omits a reserve-shortage cost.  The PAMPL
      // `reserve_zone(X).up` / `.dn` accessor is not registered for
      // this zone in the no-cost branch (rr_cols stays empty), so user
      // constraints referencing it on a hard-requirement zone now hit
      // a strict resolver error instead of silently anchoring to the
      // (pinned) column value — see issue #529 for the trade-off.
      close_row(block_rreq_eff);
      rr_rows[buid] = lp.add_row(std::move(rr_row));
    }
  }

  // storing the indices for this scenario and stage
  const STKey st_key {scenario.uid(), stage.uid()};
  rr.requirement_cols[st_key] = std::move(rr_cols);
  rr.requirement_rows[st_key] = std::move(rr_rows);

  return {};
}

/// Linear terms `Σ coef·col + offset` of an LP quantity at one block
/// (served load, generation, signed line flow).
struct LinTerms
{
  std::vector<std::pair<ColIndex, double>> cols;
  double offset {0.0};
};

[[nodiscard]] std::optional<LinTerms> demand_load_terms(
    const DemandLP& demand,
    const ScenarioLP& scenario,
    const StageLP& stage,
    BlockUid buid)
{
  const auto& lcols = demand.lookup_load_cols(scenario, stage);
  const auto it = lcols.find(buid);
  if (it == lcols.end()) {
    return std::nullopt;
  }
  LinTerms terms {
      .cols = {},
      .offset = demand.load_col_offset(scenario, stage, buid),
  };
  const auto segs = demand.load_segment_cols(scenario, stage, buid);
  if (segs.empty()) {
    terms.cols.emplace_back(it->second, 1.0);
  } else {
    for (const auto& c : segs) {
      terms.cols.emplace_back(c, 1.0);
    }
  }
  return terms;
}

[[nodiscard]] std::optional<LinTerms> generation_terms(
    const GeneratorLP& generator,
    const ScenarioLP& scenario,
    const StageLP& stage,
    BlockUid buid)
{
  const auto& gcols = generator.lookup_generation_cols(scenario, stage);
  const auto it = gcols.find(buid);
  if (it == gcols.end()) {
    return std::nullopt;
  }
  return LinTerms {
      .cols = {{it->second, 1.0}},
      .offset = 0.0,
  };
}

/// Signed A→B flow of a line: the signed column (tangent mode), else
/// `flowp − flown` (or their segment sums in `piecewise_direct`).
[[nodiscard]] LinTerms line_flow_terms(const LineLP& line,
                                       const ScenarioLP& scenario,
                                       const StageLP& stage,
                                       BlockUid buid)
{
  LinTerms terms;
  if (const auto& fs = line.flows_cols_at(scenario, stage); fs.contains(buid)) {
    terms.cols.emplace_back(fs.at(buid), 1.0);
    return terms;
  }
  const auto add_dir = [&](const auto& cols, const auto& seg_cols, double sgn)
  {
    if (const auto it = cols.find(buid); it != cols.end()) {
      terms.cols.emplace_back(it->second, sgn);
    } else if (const auto sit = seg_cols.find(buid); sit != seg_cols.end()) {
      for (const auto& c : sit->second) {
        terms.cols.emplace_back(c, sgn);
      }
    }
  };
  add_dir(line.flowp_cols_at(scenario, stage),
          line.flowp_seg_cols_at(scenario, stage),
          1.0);
  add_dir(line.flown_cols_at(scenario, stage),
          line.flown_seg_cols_at(scenario, stage),
          -1.0);
  return terms;
}

}  // namespace

namespace gtopt
{

ReserveZoneLP::Plane::Plane(const InputContext& ic,
                            std::string_view cname,
                            const Id& id,
                            ReserveRequirementPlane plane)
    : alpha(ic, cname, id, std::move(plane.alpha))
    , demand_coef(ic, cname, id, std::move(plane.demand_coef))
    , inertia_coef(ic, cname, id, std::move(plane.inertia_coef))
    , contingency_coef(ic, cname, id, std::move(plane.contingency_coef))
    , flow_coef(ic, cname, id, std::move(plane.flow_coef))
{
}

ReserveZoneLP::Requirement::Requirement(const InputContext& ic,
                                        std::string_view cname,
                                        const Id& id,
                                        auto&& rreq,
                                        auto&& rmin,
                                        auto&& rcost,
                                        auto&& rscost,
                                        Array<ReserveRequirementPlane> rplanes)
    : req(ic, cname, id, std::forward<decltype(rreq)>(rreq))
    , min(ic, cname, id, std::forward<decltype(rmin)>(rmin))
    , cost(ic, cname, id, std::forward<decltype(rcost)>(rcost))
    , surplus_cost(ic, cname, id, std::forward<decltype(rscost)>(rscost))
{
  planes.reserve(rplanes.size());
  for (auto& plane : rplanes) {
    planes.emplace_back(ic, cname, id, std::move(plane));
  }
}

ReserveZoneLP::ReserveZoneLP(const ReserveZone& preserve_zone,
                             const InputContext& ic)
    : Base(preserve_zone, ic, Element::class_name)
    , ur(ic,
         Element::class_name,
         id(),
         std::move(reserve_zone().urreq),
         std::move(reserve_zone().urmin),
         std::move(reserve_zone().urcost),
         std::move(reserve_zone().ursurplus_cost),
         std::move(reserve_zone().urreq_planes))
    , dr(ic,
         Element::class_name,
         id(),
         std::move(reserve_zone().drreq),
         std::move(reserve_zone().drmin),
         std::move(reserve_zone().drcost),
         std::move(reserve_zone().drsurplus_cost),
         std::move(reserve_zone().drreq_planes))
    , system_load_(
          ic, Element::class_name, id(), std::move(reserve_zone().system_load))
    , system_inertia_(ic,
                      Element::class_name,
                      id(),
                      std::move(reserve_zone().system_inertia))
{
  const auto sense =
      reserve_zone().requirement_sense.value_or(std::string {SenseGe});
  if (sense != SenseGe && sense != SenseEq) {
    throw std::invalid_argument(
        std::format("ReserveZone '{}': unknown requirement_sense '{}' "
                    "(expected '{}' or '{}')",
                    reserve_zone().name,
                    sense,
                    SenseGe,
                    SenseEq));
  }
  ur.equality = dr.equality = (sense == SenseEq);
  if (ur.planes.empty() && dr.planes.empty()) {
    return;
  }
  const auto& rz = reserve_zone();
  const auto load_term = rz.load_term.value_or(std::string {LoadTermExogenous});
  if (load_term == LoadTermEndogenousGeneration) {
    endogenous_generation_ = true;
    // PLEXOS `Generation_Calculation` has no factor: compat default 1.
    demand_load_factor_ = rz.demand_load_factor.value_or(1.0);
  } else if (load_term != LoadTermExogenous) {
    throw std::invalid_argument(
        std::format("ReserveZone '{}': unknown load_term '{}' (expected "
                    "'{}' or '{}')",
                    rz.name,
                    load_term,
                    LoadTermExogenous,
                    LoadTermEndogenousGeneration));
  }
  if (!endogenous_generation_) {
    demand_load_factor_ =
        rz.demand_load_factor.value_or(DefaultDemandLoadFactor);
  }
  if (const auto& it = rz.inertia_term; it.has_value()) {
    if (*it == InertiaTermCommitment) {
      inertia_term_ = InertiaTerm::commitment;
    } else if (*it == InertiaTermExogenous) {
      inertia_term_ = InertiaTerm::exogenous;
    } else if (*it == InertiaTermEndogenous) {
      inertia_term_ = InertiaTerm::endogenous;
    } else {
      throw std::invalid_argument(
          std::format("ReserveZone '{}': unknown inertia_term '{}' (expected "
                      "'{}', '{}' or '{}')",
                      rz.name,
                      *it,
                      InertiaTermCommitment,
                      InertiaTermExogenous,
                      InertiaTermEndogenous));
    }
  }
  for (const auto& sid : rz.demands) {
    demand_indexes_.push_back(ic.element_index(DemandLPSId {sid}));
  }
  // Contingency candidates: one singleton per listed unit, then the
  // general `*contingencies` groups (PLEXOS `*_Comparison` rows).
  contingency_includes_reserve_ =
      rz.contingency_includes_reserve.value_or(false);
  for (const auto& sid : rz.contingency_generators) {
    ur.contingencies.push_back(ContGroup {
        .generators = {{ic.element_index(GeneratorLPSId {sid}), 1.0}},
        .demands = {},
        .lines = {},
        .offset = {},
    });
  }
  for (const auto& sid : rz.contingency_demands) {
    dr.contingencies.push_back(ContGroup {
        .generators = {},
        .demands = {{ic.element_index(DemandLPSId {sid}), 1.0}},
        .lines = {},
        .offset = {},
    });
  }
  const auto coef_at = [](const Array<Real>& coefs, std::size_t i)
  { return i < coefs.size() ? coefs[i] : 1.0; };
  const auto add_groups = [&](Array<ReserveContingency>& src, Requirement& rr)
  {
    for (auto& cg : src) {
      ContGroup g {
          .generators = {},
          .demands = {},
          .lines = {},
          .offset =
              OptTBRealSched {
                  ic, Element::class_name, id(), std::move(cg.offset)},
      };
      for (std::size_t i = 0; i < cg.generators.size(); ++i) {
        g.generators.emplace_back(
            ic.element_index(GeneratorLPSId {cg.generators[i]}),
            coef_at(cg.generator_coefs, i));
      }
      for (std::size_t i = 0; i < cg.demands.size(); ++i) {
        g.demands.emplace_back(ic.element_index(DemandLPSId {cg.demands[i]}),
                               coef_at(cg.demand_coefs, i));
      }
      for (std::size_t i = 0; i < cg.lines.size(); ++i) {
        g.lines.emplace_back(
            ic.element_index(ObjectSingleId<LineLP> {cg.lines[i]}),
            coef_at(cg.line_coefs, i));
      }
      rr.contingencies.push_back(std::move(g));
    }
  };
  add_groups(reserve_zone().urcontingencies, ur);
  add_groups(reserve_zone().drcontingencies, dr);
  // Identical candidates (same members and coefficients, no offset) give
  // identical `C − Σ a·x ≥ 0` rows: keep the first only.
  for (auto* rr : {&ur, &dr}) {
    auto& groups = rr->contingencies;
    const auto canon = [](ContGroup g)
    {
      std::ranges::sort(g.generators);
      std::ranges::sort(g.demands);
      std::ranges::sort(g.lines);
      return std::tuple {
          std::move(g.generators), std::move(g.demands), std::move(g.lines)};
    };
    std::vector<ContGroup> unique;
    std::vector<decltype(canon(std::declval<ContGroup>()))> seen;
    for (auto& g : groups) {
      if (!g.offset.has_value()) {
        auto key = canon(g);
        if (std::ranges::contains(seen, key)) {
          continue;
        }
        seen.push_back(std::move(key));
      }
      unique.push_back(std::move(g));
    }
    groups = std::move(unique);
  }
  for (const auto& sid : rz.flow_lines) {
    flow_line_indexes_.push_back(
        ic.element_index(ObjectSingleId<LineLP> {sid}));
  }
  // The InertiaZone collection is built AFTER the ReserveZone one, so
  // the reference is resolved lazily in `add_dynamic_requirements`.
  inertia_zone_sid_ = rz.inertia_zone;
}

bool ReserveZoneLP::add_dynamic_requirements(const SystemContext& sc,
                                             const ScenarioLP& scenario,
                                             const StageLP& stage,
                                             LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto& blocks = stage.blocks();
  const auto suid = stage.uid();

  if (inertia_zone_sid_ && !inertia_zone_index_) {
    inertia_zone_index_ =
        sc.element_index(ObjectSingleId<InertiaZoneLP> {*inertia_zone_sid_});
  }

  // A term is built only when some plane (either direction) sets it.
  const auto uses = [&](auto member)
  {
    const auto in = [&](const std::vector<Plane>& planes)
    {
      return std::ranges::any_of(
          planes, [&](const Plane& p) { return (p.*member).has_value(); });
    };
    return in(ur.planes) || in(dr.planes);
  };
  const bool use_demand = uses(&Plane::demand_coef);
  // Inertia term: a given `system_inertia` schedule (exogenous) is DATA
  // on the plane RHS; otherwise an `I` column defined from the inertia
  // zone's provisions (commitment status, must-run fallback, or the
  // PLEXOS-compat dispatch provision).
  const bool exo_inertia_given = inertia_term_ == InertiaTerm::exogenous
      && system_inertia_.has_value() && uses(&Plane::inertia_coef);
  const bool use_inertia = !exo_inertia_given && inertia_zone_index_.has_value()
      && uses(&Plane::inertia_coef);
  const bool use_inertia_fixed =
      use_inertia && inertia_term_ != InertiaTerm::endogenous;
  if (use_inertia && inertia_term_ == InertiaTerm::exogenous
      && !inertia_fallback_warned_)
  {
    inertia_fallback_warned_ = true;
    spdlog::warn(
        "ReserveZone '{}': inertia_term 'exogenous' without system_inertia — "
        "using the conservative must-run inertia (units whose commitment is "
        "pinned on, or uncommitted units with pmin > 0) of InertiaZone "
        "provisions",
        reserve_zone().name);
  }
  const bool use_flow = !flow_line_indexes_.empty() && uses(&Plane::flow_coef);

  // ── Demand set ────────────────────────────────────────────────────
  // Battery-charge demands (a Converter's `demand`) are storage, not
  // consumption: excluded from the default exogenous set and subtracted
  // in the PLEXOS-compat endogenous form.
  std::vector<const DemandLP*> charge_demands;
  if (use_demand && (endogenous_generation_ || demand_indexes_.empty())) {
    for (const auto& conv : sc.elements<ConverterLP>()) {
      // A converter whose charge demand is not in this LP (filtered /
      // inactive partner) has no charge to exclude: skip it, as
      // ConverterLP does for its own partner lookups.
      try {
        charge_demands.push_back(&sc.element<DemandLP>(conv.demand_sid()));
      } catch (const std::exception&) {
        SPDLOG_DEBUG(
            "ReserveZone '{}': converter uid={} has no charge "
            "demand in this LP; skipped",
            reserve_zone().name,
            conv.uid());
      }
    }
  }
  std::vector<const DemandLP*> demand_set;
  if (use_demand && !endogenous_generation_ && !system_load_.has_value()) {
    if (demand_indexes_.empty()) {
      for (const auto& d : sc.elements<DemandLP>()) {
        if (!std::ranges::contains(charge_demands, &d)) {
          demand_set.push_back(&d);
        }
      }
    } else {
      for (const auto& di : demand_indexes_) {
        demand_set.push_back(&sc.element(di));
      }
    }
  }

  BIndexHolder<ColIndex> icols;
  BIndexHolder<RowIndex> irows;
  BIndexHolder<ColIndex> kcols;
  BIndexHolder<ColIndex> dcols;

  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const auto bctx = make_block_context(scenario.uid(), suid, buid);
    block_durations_[st_key][buid] = block.duration();

    // ── Inertia column I (provisions stamp it later) ────────────────
    //   commitment: I − Σ I_c·u_c − K = 0
    //   exogenous (must-run fallback): I − K = 0
    //   endogenous (PLEXOS-compat): I − Σ pf·r = 0
    // K (`inertia_fixed`) is a data column: equal bounds raised by every
    // forced-on / must-run unit.
    std::optional<ColIndex> icol;
    double iexo = 0.0;
    if (exo_inertia_given) {
      // Missing block → 0: the most conservative inertia (planes carry
      // inertia_coef ≤ 0, so less inertia means a larger requirement).
      iexo = system_inertia_.optval(suid, buid).value_or(0.0);
    }
    if (use_inertia) {
      icol = lp.add_col({
          .scale = TermColScale,
          .class_name = cname,
          .variable_name = InertiaName,
          .variable_uid = uid(),
          .context = bctx,
      });
      icols[buid] = *icol;
      auto irow = SparseRow {
          .class_name = cname,
          .constraint_name = InertiaName,
          .variable_uid = uid(),
          .context = bctx,
      };
      irow[*icol] = 1.0;
      if (use_inertia_fixed) {
        const auto kcol = lp.add_col({
            .lowb = 0.0,
            .uppb = 0.0,
            .scale = TermColScale,
            .class_name = cname,
            .variable_name = InertiaFixedName,
            .variable_uid = uid(),
            .context = bctx,
        });
        kcols[buid] = kcol;
        irow[kcol] = -1.0;
      }
      irows[buid] = lp.add_row(std::move(irow.equal(0.0)));
    }

    // ── System load D ───────────────────────────────────────────────
    double dexo = 0.0;
    std::optional<ColIndex> dcol;
    if (use_demand && endogenous_generation_) {
      // PLEXOS-compat ONLY (`Generation_SEN`): D = lf·(Σ p_g − Σ charge)
      // is an LP column, so every bus balance reaches the requirement
      // rows and the LMP picks up a `demand_coef · lf · μ` term.
      dcol = lp.add_col({
          .lowb = -DblMax,
          .scale = TermColScale,
          .class_name = cname,
          .variable_name = DsysName,
          .variable_uid = uid(),
          .context = bctx,
      });
      dcols[buid] = *dcol;
      auto drow = SparseRow {
          .class_name = cname,
          .constraint_name = DsysName,
          .variable_uid = uid(),
          .context = bctx,
      };
      drow[*dcol] = 1.0;
      double rhs = 0.0;
      for (const auto& g : sc.elements<GeneratorLP>()) {
        if (const auto t = generation_terms(g, scenario, stage, buid)) {
          for (const auto& [c, a] : t->cols) {
            drow[c] = -demand_load_factor_ * a;
          }
        }
      }
      for (const auto* d : charge_demands) {
        if (const auto t = demand_load_terms(*d, scenario, stage, buid)) {
          for (const auto& [c, a] : t->cols) {
            drow[c] = demand_load_factor_ * a;
          }
          rhs -= demand_load_factor_ * t->offset;
        }
      }
      std::ignore = lp.add_row(std::move(drow.equal(rhs)));
    } else if (use_demand) {
      // Default: D is DATA (given system load, or lf · Σ lmax) and goes
      // to the plane RHS — no bus balance can reach the requirement.
      if (const auto given = system_load_.optval(suid, buid)) {
        dexo = *given;
      } else {
        for (const auto* d : demand_set) {
          dexo += d->block_lmax_at(scenario, stage, buid).value_or(0.0);
        }
        dexo *= demand_load_factor_;
      }
    }

    // ── Signed flow sum of `flow_lines` (stamped per plane) ─────────
    LinTerms flow_terms;
    if (use_flow) {
      for (const auto& li : flow_line_indexes_) {
        auto t = line_flow_terms(sc.element(li), scenario, stage, buid);
        flow_terms.cols.insert(
            flow_terms.cols.end(), t.cols.begin(), t.cols.end());
      }
    }

    // ── Per-direction requirement ───────────────────────────────────
    const auto add_direction = [&](Requirement& rr,
                                   std::string_view req_name,
                                   std::string_view short_name,
                                   std::string_view plane_name,
                                   std::string_view cont_name,
                                   std::string_view surplus_name)
    {
      if (rr.planes.empty()) {
        return;
      }
      // Floor: R ≥ max(0, req, min).
      double rfloor = 0.0;
      rfloor = std::max(rfloor, rr.req.optval(suid, buid).value_or(rfloor));
      rfloor = std::max(rfloor, rr.min.optval(suid, buid).value_or(rfloor));

      const auto rcol = lp.add_col({
          .lowb = rfloor,
          .class_name = cname,
          .variable_name = req_name,
          .variable_uid = uid(),
          .context = bctx,
      });
      rr.requirement_cols[st_key][buid] = rcol;
      rr.floors[st_key][buid] = rfloor;

      // Largest contingency C ≥ unit_k (only when some plane uses it).
      std::optional<ColIndex> ccol;
      const bool use_cont = std::ranges::any_of(
          rr.planes,
          [](const Plane& p) { return p.contingency_coef.has_value(); });
      if (!rr.contingencies.empty() && use_cont) {
        ccol = lp.add_col({
            .class_name = cname,
            .variable_name = cont_name,
            .variable_uid = uid(),
            .context = bctx,
        });
        rr.contingency_cols[st_key][buid] = *ccol;
        std::vector<GenRowRef> gen_refs;
        int k = 0;
        // C − Σ a_g·p_g − Σ a_d·load_d − Σ a_l·flow_l
        //   ≥ offset + Σ a_d·load_offset_d          (one row per candidate)
        for (const auto& group : rr.contingencies) {
          ++k;
          auto crow = SparseRow {
              .class_name = cname,
              .constraint_name = cont_name,
              .variable_uid = uid(),
              .context = make_block_context(scenario.uid(), suid, buid, k),
          };
          crow[*ccol] = 1.0;
          double rhs = group.offset.optval(suid, buid).value_or(0.0);
          bool any = false;
          std::vector<std::pair<ElementIndex<GeneratorLP>, double>> members;
          for (const auto& [gi, a] : group.generators) {
            if (const auto t =
                    generation_terms(sc.element(gi), scenario, stage, buid))
            {
              for (const auto& [col, w] : t->cols) {
                crow[col] = -(a * w);
              }
              members.emplace_back(gi, a);
              any = true;
            }
          }
          for (const auto& [di, a] : group.demands) {
            if (const auto t =
                    demand_load_terms(sc.element(di), scenario, stage, buid))
            {
              for (const auto& [col, w] : t->cols) {
                crow[col] = -(a * w);
              }
              rhs += a * t->offset;
              any = true;
            }
          }
          for (const auto& [li, a] : group.lines) {
            for (const auto& [col, w] :
                 line_flow_terms(sc.element(li), scenario, stage, buid).cols)
            {
              crow[col] = -(a * w);
              any = true;
            }
          }
          if (!any && rhs == 0.0) {
            continue;
          }
          const auto row = lp.add_row(std::move(crow.greater_equal(rhs)));
          for (const auto& [gi, a] : members) {
            gen_refs.push_back(GenRowRef {
                .generator = gi,
                .row = row,
                .coef = a,
            });
          }
        }
        if (!gen_refs.empty()) {
          rr.contingency_gen_refs[st_key][buid] = std::move(gen_refs);
        }
      }

      // Planes: R − ic·I − cc·C − fc·F − dc·D_endo ≥ alpha + dc·D_exo.
      std::vector<RowIndex> prows;
      std::vector<PlaneEval> pevals;
      prows.reserve(rr.planes.size());
      pevals.reserve(rr.planes.size());
      int k = 0;
      for (const auto& plane : rr.planes) {
        ++k;
        const double dc = plane.demand_coef.optval(suid, buid).value_or(0.0);
        const double ic = plane.inertia_coef.optval(suid, buid).value_or(0.0);
        const double cc =
            plane.contingency_coef.optval(suid, buid).value_or(0.0);
        const double fc = plane.flow_coef.optval(suid, buid).value_or(0.0);
        auto prow = SparseRow {
            .class_name = cname,
            .constraint_name = plane_name,
            .variable_uid = uid(),
            .context = make_block_context(scenario.uid(), suid, buid, k),
        };
        prow[rcol] = 1.0;
        check_plane_support(
            sc,
            k,
            {
                .inertia = ic != 0.0 && !icol && !exo_inertia_given,
                .contingency = cc != 0.0 && !ccol,
                .flow = fc != 0.0 && !use_flow,
                .negative_contingency = cc < 0.0,
            });
        PlaneEval peval;
        const auto add_term = [&](ColIndex c, double a)
        {
          prow[c] = -a;
          peval.terms.emplace_back(c, a);
        };
        if (icol && ic != 0.0) {
          add_term(*icol, ic);
        }
        if (ccol && cc != 0.0) {
          add_term(*ccol, cc);
        }
        if (dcol && dc != 0.0) {
          add_term(*dcol, dc);
        }
        if (fc != 0.0) {
          for (const auto& [c, a] : flow_terms.cols) {
            add_term(c, fc * a);
          }
        }
        peval.rhs = plane.alpha.optval(suid, buid).value_or(0.0) + (dc * dexo)
            + (ic * iexo);
        prows.push_back(lp.add_row(std::move(prow.greater_equal(peval.rhs))));
        pevals.push_back(std::move(peval));
      }
      rr.plane_rows[st_key][buid] = std::move(prows);
      rr.plane_evals[st_key][buid] = std::move(pevals);

      // Provision balance: Σ pf·prov + s − R ≥ 0 (provisions stamp pf).
      auto brow = SparseRow {
          .class_name = cname,
          .constraint_name = req_name,
          .variable_uid = uid(),
          .context = bctx,
      };
      brow[rcol] = -1.0;
      if (const auto rcost = sc.reserve_shortage_cost(stage, block, rr.cost)) {
        const auto scol = lp.add_col({
            .cost = CostHelper::block_ecost(scenario, stage, block, *rcost),
            .class_name = cname,
            .variable_name = short_name,
            .variable_uid = uid(),
            .context = bctx,
        });
        rr.shortage_cols[st_key][buid] = scol;
        brow[scol] = 1.0;
      }
      if (rr.equality) {
        // No surplus column in planes mode: R is free above its planes
        // and absorbs any excess provision, so a surplus column would be
        // dead (validate_planning warns when a surplus cost is set).
        std::ignore = surplus_name;
        rr.requirement_rows[st_key][buid] =
            lp.add_row(std::move(brow.equal(0.0)));
      } else {
        rr.requirement_rows[st_key][buid] =
            lp.add_row(std::move(brow.greater_equal(0.0)));
      }
    };

    add_direction(ur,
                  UrequirementName,
                  UrshortageName,
                  UrplaneName,
                  UrcontingencyName,
                  UrsurplusName);
    add_direction(dr,
                  DrequirementName,
                  DrshortageName,
                  DrplaneName,
                  DrcontingencyName,
                  DrsurplusName);
  }

  if (!icols.empty()) {
    inertia_cols_[st_key] = std::move(icols);
    inertia_rows_[st_key] = std::move(irows);
  }
  if (!kcols.empty()) {
    inertia_fixed_cols_[st_key] = std::move(kcols);
  }
  if (!dcols.empty()) {
    dsys_cols_[st_key] = std::move(dcols);
  }
  return true;
}

void ReserveZoneLP::check_plane_support(const SystemContext& sc,
                                        int plane,
                                        PlaneSupportIssues issues)
{
  std::string what;
  const auto add = [&](bool on, std::string_view text)
  {
    if (on) {
      what += what.empty() ? text : std::format("; {}", text);
    }
  };
  add(issues.inertia,
      "inertia_coef without an inertia source (inertia_zone, or "
      "system_inertia with inertia_term 'exogenous')");
  add(issues.contingency, "contingency_coef without contingency candidates");
  add(issues.flow, "flow_coef without flow_lines");
  add(issues.negative_contingency, "contingency_coef < 0");
  if (what.empty()) {
    return;
  }
  const auto msg =
      std::format("ReserveZone '{}' plane {}: {} — the term is dropped",
                  reserve_zone().name,
                  plane,
                  what);
  const auto mode = sc.options().constraint_mode();
  if (mode == ConstraintMode::strict || mode == ConstraintMode::debug) {
    throw std::runtime_error(
        msg + " (constraint_mode=strict; use constraint_mode=normal to build "
              "with a warning)");
  }
  if (!support_warned_) {
    support_warned_ = true;
    spdlog::warn("{} (constraint_mode=normal)", msg);
  }
}

bool ReserveZoneLP::add_to_lp(const SystemContext& sc,
                              const ScenarioLP& scenario,
                              const StageLP& stage,
                              LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }

  const auto& blocks = stage.blocks();

  if (ur.planes.empty()) {
    if (auto res = add_requirement(cname,
                                   sc,
                                   scenario,
                                   stage,
                                   lp,
                                   blocks,
                                   uid(),
                                   ur,
                                   UrequirementName,
                                   UrsurplusName);
        !res)
    {
      fail_lp(*this,
              scenario,
              stage,
              {
                  .field = "urreq",
                  .detail = std::format("add_requirement (ureq) failed: {}",
                                        res.error().message),
              });
    }
  }
  if (dr.planes.empty()) {
    if (auto res = add_requirement(cname,
                                   sc,
                                   scenario,
                                   stage,
                                   lp,
                                   blocks,
                                   uid(),
                                   dr,
                                   DrequirementName,
                                   DrsurplusName);
        !res)
    {
      fail_lp(*this,
              scenario,
              stage,
              {
                  .field = "drreq",
                  .detail = std::format("add_requirement (dreq) failed: {}",
                                        res.error().message),
              });
    }
  }
  if ((!ur.planes.empty() || !dr.planes.empty())
      && !add_dynamic_requirements(sc, scenario, stage, lp))
  {
    return false;
  }

  // Register PAMPL-visible requirement columns under the canonical
  // `up` / `dn` spelling.
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  if (const auto it = ur.requirement_cols.find(st_key);
      it != ur.requirement_cols.end() && !it->second.empty())
  {
    sc.add_ampl_variable(ampl_name, uid(), UpName, scenario, stage, it->second);
  }
  if (const auto it = dr.requirement_cols.find(st_key);
      it != dr.requirement_cols.end() && !it->second.empty())
  {
    sc.add_ampl_variable(ampl_name, uid(), DnName, scenario, stage, it->second);
  }

  return true;
}

void ReserveZoneLP::add_plane_output(OutputContext& out,
                                     const auto& rr,
                                     const auto& names,
                                     std::string_view binding_name) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  if (rr.plane_rows.empty()) {
    return;
  }
  const auto pid = id();

  // Per-plane row duals `urplane<k>_dual`: with μ_k ≥ 0 the duals of
  // the binding planes, the marginal requirement cost of a unit change
  // in plane term x is Σ_k coef_k(x)·μ_k (e.g. the PLEXOS
  // `Generation_Calculation` credit Σ β_k μ_k / 1000).
  const auto nplanes = std::min(rr.planes.size(), names.size());
  for (std::size_t k = 0; k < nplanes; ++k) {
    STBIndexHolder<RowIndex> rows;
    for (const auto& [st_key, brows] : rr.plane_rows) {
      auto& dst = rows[st_key];
      for (const auto& [buid, prows] : brows) {
        if (k < prows.size()) {
          dst[buid] = prows[k];
        }
      }
    }
    out.add_row_dual(cname, names[k], pid, rows);
  }

  // Binding plane: argmax_k (rhs_k + Σ coef·x); 0 when the floor is
  // strictly above every plane.
  STBIndexHolder<double> binding;
  for (const auto& [st_key, bevals] : rr.plane_evals) {
    auto& dst = binding[st_key];
    const auto fit = rr.floors.find(st_key);
    for (const auto& [buid, pevals] : bevals) {
      double best = -DblMax;
      double best_k = 0.0;
      for (std::size_t k = 0; k < pevals.size(); ++k) {
        double v = pevals[k].rhs;
        for (const auto& [c, a] : pevals[k].terms) {
          v += a * out.primal(c);
        }
        if (v > best + 1e-9) {
          best = v;
          best_k = static_cast<double>(k + 1);
        }
      }
      double rfloor = 0.0;
      if (fit != rr.floors.end()) {
        if (const auto f = fit->second.find(buid); f != fit->second.end()) {
          rfloor = f->second;
        }
      }
      dst[buid] = rfloor > best + 1e-9 ? 0.0 : best_k;
    }
  }
  out.add_col_sol_values(cname, binding_name, pid, binding);
}

void ReserveZoneLP::add_credit_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  // `reserve_credit` is written for every plane zone (0 with the
  // exogenous load term, where no generation column reaches the planes).
  const bool load_credit = !ur.plane_rows.empty() || !dr.plane_rows.empty();
  const bool endo_load = !dsys_cols_.empty();
  const bool flow_credit = !flow_line_indexes_.empty();
  if (!out.has_row_duals() || !load_credit) {
    return;
  }
  // Stationarity of a generation column g in the endogenous load term:
  // it enters every plane k through D with coefficient −demand_coef_k·lf,
  // so its bus price picks up Σ_k demand_coef_k·lf·μ_k — the same value
  // at every bus (D counts all generation).  A flow column of a
  // `flow_lines` line enters through −flow_coef_k: Σ_k flow_coef_k·μ_k is
  // the extra spread between its two buses.  Duals are combined while
  // still folded and unfolded once by `add_dual_derived_values`.
  STBIndexHolder<double> credit;
  STBIndexHolder<double> fcredit;
  STBIndexHolder<double> transfer;
  for (const auto* rr : {&ur, &dr}) {
    for (const auto& [st_key, brows] : rr->plane_rows) {
      const auto suid = std::get<1>(st_key);
      for (const auto& [buid, prows] : brows) {
        double c = 0.0;
        double f = 0.0;
        for (std::size_t k = 0; k < prows.size() && k < rr->planes.size(); ++k)
        {
          const auto& plane = rr->planes[k];
          const double mu = out.dual(prows[k]);
          if (endo_load) {
            c += plane.demand_coef.optval(suid, buid).value_or(0.0)
                * demand_load_factor_ * mu;
          }
          f += plane.flow_coef.optval(suid, buid).value_or(0.0) * mu;
        }
        credit[st_key][buid] += c;
        if (flow_credit) {
          fcredit[st_key][buid] += f;
        }
      }
    }
  }
  {
    for (const auto& [st_key, bvals] : credit) {
      const auto dit = dsys_cols_.find(st_key);
      const auto tit = block_durations_.find(st_key);
      for (const auto& [buid, c] : bvals) {
        double energy = 0.0;
        if (dit != dsys_cols_.end() && tit != block_durations_.end()) {
          const auto dc = dit->second.find(buid);
          const auto dt = tit->second.find(buid);
          if (dc != dit->second.end() && dt != tit->second.end()
              && demand_load_factor_ != 0.0)
          {
            energy = out.primal(dc->second) / demand_load_factor_ * dt->second;
          }
        }
        transfer[st_key][buid] = c * energy;
      }
    }
    out.add_dual_derived_values(cname, ReserveCreditName, id(), credit);
    out.add_dual_derived_values(cname, CreditTransferName, id(), transfer);
  }
  if (flow_credit) {
    out.add_dual_derived_values(cname, FlowCreditName, id(), fcredit);
  }
}

bool ReserveZoneLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  out.add_col_sol(cname, UrequirementName, pid, ur.requirement_cols);
  out.add_col_cost(cname, UrequirementName, pid, ur.requirement_cols);
  out.add_row_dual(cname, UrequirementName, pid, ur.requirement_rows);

  out.add_col_sol(cname, DrequirementName, pid, dr.requirement_cols);
  out.add_col_cost(cname, DrequirementName, pid, dr.requirement_cols);
  out.add_row_dual(cname, DrequirementName, pid, dr.requirement_rows);

  if (!ur.shortage_cols.empty()) {
    out.add_col_sol(cname, UrshortageName, pid, ur.shortage_cols);
  }
  if (!dr.shortage_cols.empty()) {
    out.add_col_sol(cname, DrshortageName, pid, dr.shortage_cols);
  }
  if (!ur.surplus_cols.empty()) {
    out.add_col_sol(cname, UrsurplusName, pid, ur.surplus_cols);
  }
  if (!dr.surplus_cols.empty()) {
    out.add_col_sol(cname, DrsurplusName, pid, dr.surplus_cols);
  }
  if (!ur.contingency_cols.empty()) {
    out.add_col_sol(cname, UrcontingencyName, pid, ur.contingency_cols);
  }
  if (!dr.contingency_cols.empty()) {
    out.add_col_sol(cname, DrcontingencyName, pid, dr.contingency_cols);
  }
  if (!inertia_cols_.empty()) {
    out.add_col_sol(cname, InertiaName, pid, inertia_cols_);
  }
  if (!dsys_cols_.empty()) {
    out.add_col_sol(cname, DsysName, pid, dsys_cols_);
  }
  add_plane_output(out, ur, UrplaneNames, UrplaneBindingName);
  add_plane_output(out, dr, DrplaneNames, DrplaneBindingName);
  add_credit_output(out);

  return true;
}
}  // namespace gtopt
