/**
 * @file      inertia_zone_lp.cpp
 * @brief     LP formulation for inertia zones
 * @date      2026-04-13
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Implements InertiaZoneLP: creates a slack variable per block for the
 * inertia requirement and a >= 0 requirement row.  InertiaProvisionLP
 * injects its provision_factor * r_inertia coefficients into this row.
 */

#include <algorithm>
#include <expected>
#include <format>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include <gtopt/error.hpp>
#include <gtopt/inertia_zone_lp.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/user_constraint_enums.hpp>
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
                                           const auto rname)
{
  using STKey = STBIndexHolder<ColIndex>::key_type;
  if (!rr.req) {
    return {};
  }

  BIndexHolder<ColIndex> rr_cols;
  BIndexHolder<RowIndex> rr_rows;
  map_reserve(rr_cols, blocks.size());
  map_reserve(rr_rows, blocks.size());

  for (const auto& block : blocks) {
    const auto buid = block.uid();
    const auto block_rreq = rr.req.optval(stage.uid(), block.uid());
    if (!block_rreq) {
      continue;
    }

    // LP-size: a zero requirement makes the row ``Σ pf · r_inertia ≥ 0``
    // — trivially satisfied (pf > 0, r_inertia ≥ 0) and never binding.
    // The priced branch would also create a fixed-zero slack column
    // (uppb = block_rreq = 0).  Skip both.  Write-out rule: the absent
    // requirement column reads 0 and the absent row dual is 0.
    if (sc.options().lp_reduction() && *block_rreq == 0.0) {
      continue;
    }

    // `rr.cost` is now per-(stage, block): resolve per block via the
    // overload that consults `model_options.reserve_shortage_cost` as
    // fallback (mirrors the ReserveZoneLP path).
    const auto block_rcost = sc.reserve_shortage_cost(stage, block, rr.cost);

    const auto block_context =
        make_block_context(scenario.uid(), stage.uid(), block.uid());

    SparseRow rr_row {
        .class_name = cname,
        .constraint_name = rname,
        .variable_uid = uid,
        .context = block_context,
    };

    if (block_rcost) {
      // Priced shortage branch (soft requirement).  The column carries
      // `−c` so the objective holds `−c·rcol`; the `+c·req` met-requirement
      // valuation constant goes to the LP-external objective offset so
      // the objective equals the shortage penalty `c·(req − rcol)` (see
      // the ReserveZoneLP twin).  LP rows/columns/duals are unchanged.
      const auto block_ecost =
          CostHelper::block_ecost(scenario, stage, block, block_rcost.value());
      lp.add_obj_constant(block_ecost * block_rreq.value());
      const auto rcol = lp.add_col({
          .lowb = 0.0,
          .uppb = block_rreq.value(),
          .cost = -block_ecost,
          .class_name = cname,
          .variable_name = rname,
          .variable_uid = uid,
          .context = block_context,
      });
      rr_cols[buid] = rcol;
      rr_row[rcol] = -1;
      rr_rows[buid] = lp.add_row(std::move(rr_row.greater_equal(0)));
    } else {
      // Hard requirement substitute-out: the legacy form built a
      // fully-pinned bookkeeping column with `cost = 0`; move
      // `block_rreq` to the row RHS instead.  Mirror of the
      // ReserveZoneLP substitution (issue #529, item 1).
      rr_rows[buid] = lp.add_row(std::move(rr_row.greater_equal(*block_rreq)));
    }
  }

  // storing the indices for this scenario and stage
  const STKey st_key {scenario.uid(), stage.uid()};
  rr.requirement_cols[st_key] = std::move(rr_cols);
  rr.requirement_rows[st_key] = std::move(rr_rows);

  return {};
}
}  // namespace

namespace gtopt
{

InertiaZoneLP::Requirement::Requirement(const InputContext& ic,
                                        std::string_view cname,
                                        const Id& id,
                                        auto&& rreq,
                                        auto&& rcost)
    : req(ic, cname, id, std::forward<decltype(rreq)>(rreq))
    , cost(ic, cname, id, std::forward<decltype(rcost)>(rcost))
{
}

InertiaZoneLP::InertiaZoneLP(const InertiaZone& inertia_zone,
                             const InputContext& ic)
    : Base(inertia_zone, ic, Element::class_name)
    , rr(ic,
         Element::class_name,
         id(),
         std::move(this->inertia_zone().requirement),
         std::move(this->inertia_zone().cost))
{
  for (auto& cr : this->inertia_zone().requirement_credits) {
    // `units` (committed units needed for the credit) must be positive: a
    // clamp to 1e-9 used to turn `units = 0` into a near-zero coefficient
    // that silently made the credit free.
    const double units = cr.units.value_or(1.0);
    if (!(units > 0.0)) {
      throw std::invalid_argument(
          std::format("InertiaZone '{}': requirement_credits units must be "
                      "> 0 (got {})",
                      this->inertia_zone().name,
                      units));
    }
    Credit credit {
        .generators = {},
        .units = units,
        .credit =
            OptTBRealSched {
                ic, Element::class_name, id(), std::move(cr.credit)},
        .integer = cr.integer.value_or(false),
    };
    for (const auto& sid : cr.generators) {
      credit.generators.push_back(ic.element_index(GeneratorLPSId {sid}));
    }
    credits_.push_back(std::move(credit));
  }
}

void InertiaZoneLP::add_credits(const SystemContext& sc,
                                const ScenarioLP& scenario,
                                const StageLP& stage,
                                LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  const auto rows_it = rr.requirement_rows.find(st_key);
  if (credits_.empty() || rows_it == rr.requirement_rows.end()) {
    return;
  }
  const bool continuous_phase =
      sc.simulation().phases()[stage.phase_index()].is_continuous();

  // Status column of a generator at one block: Commitment (3-bin) or
  // SimpleCommitment; nullopt when the unit has no commitment here.
  const auto status_col = [&](const ElementIndex<GeneratorLP>& gi,
                              BlockUid buid) -> std::optional<ColIndex>
  {
    for (const auto& cm : sc.elements<CommitmentLP>()) {
      if (sc.element_index(cm.generator_sid()) != gi) {
        continue;
      }
      if (const auto* ucols = cm.find_status_cols(scenario, stage)) {
        if (const auto it = ucols->find(buid); it != ucols->end()) {
          return it->second;
        }
      }
    }
    for (const auto& cm : sc.elements<SimpleCommitmentLP>()) {
      if (sc.element_index(cm.generator_sid()) != gi) {
        continue;
      }
      if (auto ucol = cm.lookup_status_col(scenario, stage, buid)) {
        return ucol;
      }
    }
    return std::nullopt;
  };

  check_credit_generators(sc, stage);

  auto& bcols = credit_cols_[st_key];
  for (const auto& [buid, req_row] : rows_it->second) {
    std::vector<ColIndex> ycols;
    int k = 0;
    for (const auto& cr : credits_) {
      ++k;
      const double relief = cr.credit.optval(stage.uid(), buid).value_or(0.0);
      if (relief == 0.0) {
        continue;
      }
      std::vector<ColIndex> ucols;
      for (const auto& gi : cr.generators) {
        if (const auto u = status_col(gi, buid)) {
          ucols.push_back(*u);
        }
      }
      if (ucols.empty()) {
        continue;  // no commitment: exogenous requirement only
      }
      const auto ycol = lp.add_col({
          .lowb = 0.0,
          .uppb = 1.0,
          .is_integer = cr.integer && !continuous_phase,
          .class_name = cname,
          .variable_name = CreditName,
          .variable_uid = uid(),
          .context = make_block_context(scenario.uid(), stage.uid(), buid, k),
      });
      ycols.push_back(ycol);
      auto link = SparseRow {
          .class_name = cname,
          .constraint_name = CreditName,
          .variable_uid = uid(),
          .context = make_block_context(scenario.uid(), stage.uid(), buid, k),
      };
      link[ycol] = cr.units;
      for (const auto u : ucols) {
        link[u] = -1.0;
      }
      std::ignore = lp.add_row(std::move(link.less_equal(0.0)));
      lp.set_coeff(req_row, ycol, relief);
    }
    if (!ycols.empty()) {
      bcols[buid] = std::move(ycols);
    }
  }
}

void InertiaZoneLP::check_credit_generators(const SystemContext& sc,
                                            const StageLP& stage)
{
  if (credits_checked_) {
    return;
  }
  credits_checked_ = true;

  // A credit counts the status `u` of its generators.  A generator with NO
  // Commitment / SimpleCommitment element has no status column anywhere, so
  // its share of the credit can never be earned: the relief silently
  // vanishes and the zone keeps the full exogenous requirement.  That is a
  // modelling error, not an intentional omission (unlike a committed unit
  // whose status column is absent at one block — an inactive commitment or
  // a non-chronological stage — where "no relief there" is the defined
  // result).  strict / debug: abort; normal: warn once and drop the term.
  const auto has_commitment = [&](const ElementIndex<GeneratorLP>& gi)
  {
    return std::ranges::any_of(
               sc.elements<CommitmentLP>(),
               [&](const auto& cm)
               { return sc.element_index(cm.generator_sid()) == gi; })
        || std::ranges::any_of(
               sc.elements<SimpleCommitmentLP>(),
               [&](const auto& cm)
               { return sc.element_index(cm.generator_sid()) == gi; });
  };

  const auto mode = sc.options().constraint_mode();
  const bool strict =
      mode == ConstraintMode::strict || mode == ConstraintMode::debug;
  int k = 0;
  for (const auto& cr : credits_) {
    ++k;
    for (const auto& gi : cr.generators) {
      if (has_commitment(gi)) {
        continue;
      }
      const auto& gen = sc.element(gi);
      const auto msg = std::format(
          "InertiaZone '{}': requirement_credits[{}] counts generator '{}', "
          "which has no Commitment / SimpleCommitment (no status column) — "
          "its share of the credit can never be earned",
          inertia_zone().name,
          k,
          gen.object().name);
      if (strict) {
        throw std::runtime_error(
            msg
            + " (constraint_mode=strict; add a commitment for the unit, "
              "remove it from the credit, or use constraint_mode=normal to "
              "drop it with a warning)");
      }
      // One warning per (zone, credit, generator) per run: the check runs
      // once per SystemLP, so only the first phase's LP reports.
      if (stage.phase_index() == PhaseIndex {0}) {
        SPDLOG_WARN("{} — dropped from the credit (constraint_mode=normal)",
                    msg);
      }
    }
  }
}

bool InertiaZoneLP::add_to_lp(const SystemContext& sc,
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

  if (auto res = add_requirement(
          cname, sc, scenario, stage, lp, blocks, uid(), rr, RequirementName);
      !res)
  {
    fail_lp(*this,
            scenario,
            stage,
            {
                .field = "requirement",
                .detail = std::format("add_requirement failed: {}",
                                      res.error().message),
            });
  }
  add_credits(sc, scenario, stage, lp);

  // Register PAMPL-visible requirement columns
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  if (const auto it = rr.requirement_cols.find(st_key);
      it != rr.requirement_cols.end() && !it->second.empty())
  {
    sc.add_ampl_variable(
        ampl_name, uid(), ReqName, scenario, stage, it->second);
  }

  return true;
}

bool InertiaZoneLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  out.add_col_sol(cname, RequirementName, pid, rr.requirement_cols);
  out.add_col_cost(cname, RequirementName, pid, rr.requirement_cols);
  out.add_row_dual(cname, RequirementName, pid, rr.requirement_rows);
  if (!credit_cols_.empty()) {
    out.add_col_sol(cname, CreditName, pid, credit_cols_);
  }

  return true;
}

}  // namespace gtopt
