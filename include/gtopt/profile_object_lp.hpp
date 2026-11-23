/**
 * @file      profile_object_lp.hpp
 * @brief     Base class for profile-based LP objects
 * @date      Wed Apr 17 10:20:35 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Defines the ProfileObjectLP base class which contains common functionality
 * for profile-based LP objects like DemandProfileLP and GeneratorProfileLP.
 */

#pragma once

#include <algorithm>
#include <functional>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <utility>

#include <gtopt/aperture_data_cache.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/object_lp.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/system_context.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

/// Owner-kind switches of `ProfileObjectLP::add_profile_to_lp`.
struct ProfileLPMode
{
  /// Cap the available amount at the owner column upper bound (pmax) and
  /// clip the profile to 1 with a capacity column (generators: spillover
  /// can never exceed the generation range).
  bool clamp_to_element_range {false};
  /// Drop the slack column when it carries no cost: emit `x ≤ A` and
  /// rebuild the slack at output time (generators).
  bool allow_slack_free {false};
};

/// Warn that a profile value exceeds 1 (profiles are per-unit of the
/// owner capacity, not MW).  Process-wide summary: one line per
/// (class, uid) — every scene/phase LP builds its own element copy — for
/// the first `max_detailed` elements, then a single "suppressed" line.
inline void warn_profile_above_one(std::string_view class_name,
                                   Uid uid,
                                   std::string_view name,
                                   double max_value,
                                   bool clamped)
{
  constexpr std::size_t max_detailed = 10;
  static std::mutex mtx;
  static std::set<std::pair<std::string, Uid>> seen;

  const std::scoped_lock lock {mtx};
  if (!seen.emplace(std::string {class_name}, uid).second) {
    return;
  }
  if (seen.size() <= max_detailed) {
    spdlog::warn(
        "{} uid={} '{}': profile value {:.6g} > 1; profiles are per-unit "
        "factors of the owner capacity, not MW{}",
        class_name,
        uid,
        name,
        max_value,
        clamped ? " (availability capped at min(capacity·profile, pmax))" : "");
  } else if (seen.size() == max_detailed + 1) {
    spdlog::warn(
        "{}: more profile elements with values > 1 (per-unit expected); "
        "further warnings suppressed",
        class_name);
  }
}

/// Mode used by every generator profile (`GeneratorProfile`,
/// `CapacityProfile` with `owner_kind = generator`).
inline constexpr ProfileLPMode generator_profile_lp_mode {
    .clamp_to_element_range = true,
    .allow_slack_free = true,
};

/**
 * @brief Base class for profile-based LP objects
 *
 * @tparam ProfileType The profile type (DemandProfile/GeneratorProfile)
 * @tparam ElementLPType The associated element LP type (DemandLP/GeneratorLP)
 */
template<typename ProfileType, typename ElementLPType>
class ProfileObjectLP : public ObjectLP<ProfileType>
{
public:
  using Base = ObjectLP<ProfileType>;
  using Base::id;
  using Base::is_active;
  using Base::uid;

  /**
   * @brief Construct a new ProfileObjectLP
   *
   * @param pprofile The profile object
   * @param ic Input context
   * @param cname Class name for labeling columns/rows
   */
  explicit ProfileObjectLP(ProfileType pprofile,
                           InputContext& ic,
                           const LPClassName& cname)
      : Base(std::move(pprofile), ic, cname)
      , profile(ic, cname.full_name(), id(), std::move(Base::object().profile))
      , scost(ic, cname.full_name(), id(), std::move(Base::object().scost))
  {
  }

  /**
   * @brief Add profile constraints to LP
   *
   * A profile is a per-unit factor `p` of the owner capacity `C`.  Two
   * row forms are emitted (see `ProfileLPMode` and the formulation doc):
   *
   * - **slack-free** (generator, no spillover cost): `x ≤ A`, with
   *   `A = min(C·p, x̄)` for a fixed capacity (`x̄` = the owner column
   *   upper bound, i.e. the block pmax) or `x − min(p, 1)·C_col ≤ 0`
   *   with a capacity column.  Spillover `A − x` is reconstructed at
   *   output time; its dual is the dual of the `≤` row.
   * - **slack** (demand, or a generator with `scost ≠ 0`):
   *   `x + s = A` (resp. `x + s − p·C_col = 0`), `0 ≤ s ≤ A` (resp.
   *   `s ≤ p·C̄`).
   *
   * @param full_class_name Full class name used to label the profile
   *        columns/rows
   * @param scenario Scenario
   * @param stage Stage
   * @param lp Linear problem
   * @param profile_name Profile type name ("spillover"/"unserved")
   * @param element_cols Element column indices per block
   * @param capacity_col Capacity column index (if any)
   * @param stage_capacity Stage capacity value
   * @param mode Owner-kind specific formulation switches
   * @return true if successful
   */
  bool add_profile_to_lp(std::string_view full_class_name,
                         const ScenarioLP& scenario,
                         const StageLP& stage,
                         LinearProblem& lp,
                         std::string_view profile_name,
                         const auto& element_cols,
                         const std::optional<ColIndex>& capacity_col,
                         double stage_capacity,
                         ProfileLPMode mode = {})
  {
    if (!is_active(stage)) {
      return true;
    }

    const auto stage_scost = scost.optval(stage.uid()).value_or(0.0);
    const bool slack_free = mode.allow_slack_free && stage_scost == 0.0;
    const auto& blocks = stage.blocks();

    // Upper bound of the capacity column: caps the slack in slack form.
    const double max_capacity =
        capacity_col ? lp.get_col_uppb(*capacity_col) : LinearProblem::DblMax;

    BIndexHolder<ColIndex> scols;
    BIndexHolder<RowIndex> srows;
    BIndexHolder<ColIndex> ecols;
    BIndexHolder<double> elem_upps;
    BIndexHolder<double> avails;
    if (!slack_free) {
      map_reserve(scols, blocks.size());
    }
    map_reserve(srows, blocks.size());
    map_reserve(ecols, blocks.size());
    map_reserve(elem_upps, blocks.size());
    map_reserve(avails, blocks.size());

    double max_profile = 0.0;

    for (const auto& block : blocks) {
      const auto buid = block.uid();

      const auto block_profile =
          profile.optval(scenario.uid(), stage.uid(), buid);
      if (!block_profile.has_value()) {
        SPDLOG_WARN(
            "{} uid={}: missing profile value for scenario={} stage={} "
            "block={}; skipping block",
            full_class_name,
            uid(),
            scenario.uid(),
            stage.uid(),
            buid);
        continue;
      }

      const auto ecol_it = element_cols.find(buid);
      if (ecol_it == element_cols.end()) {
        SPDLOG_WARN(
            "{} uid={}: owner has no element column for scenario={} stage={} "
            "block={}; skipping block",
            full_class_name,
            uid(),
            scenario.uid(),
            stage.uid(),
            buid);
        continue;
      }
      const auto ecol = ecol_it->second;

      const double raw_profile = *block_profile;
      max_profile = std::max(max_profile, raw_profile);

      // Generation range of the owner in this block (pmax, already
      // min'ed with a fixed capacity by the owner LP).
      const double elem_upp = mode.clamp_to_element_range
          ? lp.get_col_uppb(ecol)
          : LinearProblem::DblMax;
      // With a capacity column the profile multiplies the LP capacity; a
      // value above 1 would force spillover beyond the installed capacity.
      const double eff_profile = (mode.clamp_to_element_range && capacity_col)
          ? std::min(raw_profile, 1.0)
          : raw_profile;
      const double avail = capacity_col
          ? eff_profile
          : std::min(stage_capacity * raw_profile, elem_upp);

      const auto block_ctx =
          make_block_context(scenario.uid(), stage.uid(), block.uid());

      auto srow = SparseRow {
          .class_name = full_class_name,
          .constraint_name = profile_name,
          .variable_uid = uid(),
          .context = block_ctx,
      };
      srow[ecol] = 1;

      if (!slack_free) {
        const auto block_scost =
            CostHelper::block_ecost(scenario, stage, block, stage_scost);
        const double scol_upp = capacity_col
            ? (max_capacity < LinearProblem::DblMax
                   ? std::max(0.0, eff_profile * max_capacity)
                   : LinearProblem::DblMax)
            : std::max(0.0, avail);
        const auto scol = lp.add_col(SparseCol {
            .lowb = 0.0,
            .uppb = scol_upp,
            .cost = block_scost,
            .class_name = full_class_name,
            .variable_name = profile_name,
            .variable_uid = uid(),
            .context = block_ctx,
        });
        scols[buid] = scol;
        srow[scol] = 1;
      }

      if (capacity_col) {
        srow[*capacity_col] = -eff_profile;
        srows[buid] = lp.add_row(
            std::move(slack_free ? srow.less_equal(0) : srow.equal(0)));
      } else {
        srows[buid] = lp.add_row(
            std::move(slack_free ? srow.less_equal(avail) : srow.equal(avail)));
      }
      ecols[buid] = ecol;
      elem_upps[buid] = elem_upp;
      avails[buid] = avail;
    }

    if (max_profile > 1.0 + 1e-6) {
      warn_profile_above_one(full_class_name,
                             uid(),
                             Base::object().name,
                             max_profile,
                             mode.clamp_to_element_range);
    }

    // Store indices for this scenario and stage
    const auto st_key = std::tuple {scenario.uid(), stage.uid()};
    if (!slack_free) {
      spillover_cols[st_key] = std::move(scols);
    }
    spillover_rows[st_key] = std::move(srows);
    capacity_info[st_key] = CapacityInfo {
        .cap_col = capacity_col,
        .stage_capacity = stage_capacity,
        .max_capacity = max_capacity,
        .slack_free = slack_free,
        .clamp = mode.clamp_to_element_range,
        .elem_cols = std::move(ecols),
        .elem_upp = std::move(elem_upps),
        .avail = std::move(avails),
    };

    return true;
  }

  /**
   * @brief Add profile results to output
   *
   * Slack-form stages read the slack column; slack-free stages rebuild
   * `slack = A − x ≥ 0` (or `min(p·C_col, x̄) − x`) from the owner primal
   * and report the slack reduced cost as `−dual` of the `≤` row (the
   * reduced cost of a zero-cost slack in the equivalent equality form),
   * so `<profile_name>_sol/_cost/_dual` keep their meaning.
   *
   * @param cname Class name prefix for labeling
   * @param out Output context
   * @param profile_name Profile type name ("spillover"/"unserved")
   * @return true if successful
   */
  bool add_profile_to_output(std::string_view cname,
                             OutputContext& out,
                             std::string_view profile_name) const
  {
    const auto pid = id();

    const bool any_slack_free = std::ranges::any_of(
        capacity_info, [](const auto& kv) { return kv.second.slack_free; });

    if (!any_slack_free) {
      out.add_col_sol(cname, profile_name, pid, spillover_cols);
      out.add_col_cost(cname, profile_name, pid, spillover_cols);
    } else {
      STBIndexHolder<double> sol_values;
      STBIndexHolder<double> cost_values;
      const bool with_cost = out.has_col_costs() && out.has_row_duals();
      for (const auto& [st_key, info] : capacity_info) {
        BIndexHolder<double> sol_block;
        BIndexHolder<double> cost_block;
        if (!info.slack_free) {
          const auto sc_it = spillover_cols.find(st_key);
          if (sc_it == spillover_cols.end()) {
            continue;
          }
          for (const auto& [buid, scol] : sc_it->second) {
            sol_block[buid] = out.primal(scol);
            if (with_cost) {
              cost_block[buid] = out.cost(scol);
            }
          }
        } else {
          const auto row_it = spillover_rows.find(st_key);
          const double cap_sol = info.cap_col ? out.primal(*info.cap_col) : 0.0;
          for (const auto& [buid, ecol] : info.elem_cols) {
            const double avail = info.cap_col
                ? std::min(info.avail.at(buid) * cap_sol,
                           info.elem_upp.at(buid))
                : info.avail.at(buid);
            sol_block[buid] = std::max(0.0, avail - out.primal(ecol));
            if (with_cost && row_it != spillover_rows.end()) {
              const auto r_it = row_it->second.find(buid);
              if (r_it != row_it->second.end()) {
                cost_block[buid] = -out.dual(r_it->second);
              }
            }
          }
        }
        sol_values[st_key] = std::move(sol_block);
        if (with_cost) {
          cost_values[st_key] = std::move(cost_block);
        }
      }
      out.add_col_sol_values(cname, profile_name, pid, sol_values);
      if (with_cost) {
        out.add_col_cost_values(cname, profile_name, pid, cost_values);
      }
    }
    out.add_row_dual(cname, profile_name, pid, spillover_rows);
    return true;
  }

  /// Profile rows per (scenario, stage, block) — tests / diagnostics.
  [[nodiscard]] constexpr const auto& profile_rows() const noexcept
  {
    return spillover_rows;
  }

  /// Slack columns per (scenario, stage, block); empty for slack-free
  /// (scenario, stage) pairs — tests / diagnostics.
  [[nodiscard]] constexpr const auto& profile_slack_cols() const noexcept
  {
    return spillover_cols;
  }

  /// Return the profile value for a given scenario/stage/block.
  [[nodiscard]] std::optional<double> aperture_value(ScenarioUid scenario_uid,
                                                     StageUid stage_uid,
                                                     BlockUid block_uid) const
  {
    return profile.at(scenario_uid, stage_uid, block_uid);
  }

  /**
   * @brief Update profile constraint for an aperture scenario.
   *
   * Re-reads the profile value via @p value_fn and updates the constraint
   * row accordingly (same clipping as `add_profile_to_lp`).  When a
   * capacity column was used during LP construction, updates the
   * coefficient; otherwise updates the row RHS (and the slack bound in
   * slack form).
   *
   * @param li            Cloned LinearInterface to modify in-place.
   * @param base_scenario Original scenario (identifies stored rows).
   * @param value_fn      (StageUid, BlockUid) -> optional<double> provider.
   * @param stage         Stage to update.
   * @return true on success.
   */
  template<std::invocable<StageUid, BlockUid> F>
    requires std::same_as<std::invoke_result_t<F, StageUid, BlockUid>,
                          std::optional<double>>
  [[nodiscard]] bool update_aperture(LinearInterface& li,
                                     const ScenarioLP& base_scenario,
                                     F&& value_fn,
                                     const StageLP& stage) const
  {
    if (!is_active(stage)) {
      return true;
    }

    const auto st_key = std::tuple {base_scenario.uid(), stage.uid()};
    const auto row_it = spillover_rows.find(st_key);
    if (row_it == spillover_rows.end()) {
      return true;  // no rows registered for this (scenario, stage)
    }

    const auto cap_it = capacity_info.find(st_key);
    if (cap_it == capacity_info.end()) {
      return true;  // no capacity info (should not happen)
    }

    const auto& info = cap_it->second;
    const auto sc_it = spillover_cols.find(st_key);
    const auto slack_col = [&](BlockUid buid) -> std::optional<ColIndex>
    {
      if (info.slack_free || sc_it == spillover_cols.end()) {
        return std::nullopt;
      }
      const auto it = sc_it->second.find(buid);
      return it == sc_it->second.end() ? std::nullopt
                                       : std::optional {it->second};
    };

    for (const auto& [block_uid, row] : row_it->second) {
      const auto new_profile = value_fn(stage.uid(), block_uid);
      if (!new_profile.has_value()) {
        continue;  // keep forward-pass value if not available
      }
      const auto scol = slack_col(block_uid);
      if (info.cap_col) {
        // Row: element [+ slack] - profile * capacity {≤,=} 0
        const double eff =
            info.clamp ? std::min(*new_profile, 1.0) : *new_profile;
        li.set_coeff(row, *info.cap_col, -eff);
        if (scol && info.max_capacity < LinearProblem::DblMax) {
          li.set_col_upp(*scol, std::max(0.0, eff * info.max_capacity));
        }
      } else {
        // Row: element [+ slack] {≤,=} min(capacity * profile, pmax)
        const auto ub_it = info.elem_upp.find(block_uid);
        const double elem_upp = ub_it != info.elem_upp.end()
            ? ub_it->second
            : LinearProblem::DblMax;
        const double new_rhs =
            std::min(info.stage_capacity * *new_profile, elem_upp);
        if (!info.slack_free) {
          li.set_row_low(row, new_rhs);
        }
        li.set_row_upp(row, new_rhs);
        if (scol) {
          li.set_col_upp(*scol, std::max(0.0, new_rhs));
        }
      }
    }
    return true;
  }

private:
  /// Capacity info stored during LP construction for aperture updates
  /// and slack-free output reconstruction.
  struct CapacityInfo
  {
    std::optional<ColIndex> cap_col {};
    double stage_capacity {0.0};
    double max_capacity {LinearProblem::DblMax};
    bool slack_free {false};
    bool clamp {false};
    /// Owner column per block (slack-free reconstruction).
    BIndexHolder<ColIndex> elem_cols {};
    /// Owner column upper bound (generation range) per block.
    BIndexHolder<double> elem_upp {};
    /// Fixed capacity: available `A`; capacity column: effective profile.
    BIndexHolder<double> avail {};
  };

  /// Spillover column indices (scenario × stage × block); slack form only
  STBIndexHolder<ColIndex> spillover_cols;
  /// Spillover row indices (scenario × stage × block)
  STBIndexHolder<RowIndex> spillover_rows;
  /// Capacity info per (scenario, stage) for aperture updates
  STIndexHolder<CapacityInfo> capacity_info;

  /// Profile schedule data
  STBRealSched profile;
  /// Short-term cost schedule
  OptTRealSched scost;
};

}  // namespace gtopt
