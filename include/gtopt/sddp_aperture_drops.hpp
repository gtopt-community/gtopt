/**
 * @file      sddp_aperture_drops.hpp
 * @brief     Cross-iteration bookkeeping of dropped (infeasible) apertures
 * @date      2026-11-24
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * When an aperture LP of the SDDP backward pass is infeasible (or times
 * out / is abandoned / ends non-optimal), `solve_apertures_for_phase`
 * drops it and renormalises the expected cut over the remaining
 * apertures.  On a model without relatively complete recourse (e.g. a
 * hard `efin` with no `efin_cost`) the same aperture is dropped on every
 * iteration: the cut never carries the feasibility signal, the LB
 * freezes, and the bounds describe a rescaled (typically wetter)
 * distribution instead of the declared one (issue #551).
 *
 * `ApertureDropTracker` records every aperture solve and drop per
 * (scene, phase, aperture) and per iteration so the solver can
 *  - escalate ONE "persistent aperture infeasibility" warning per triple
 *    once it has been dropped in `warn_after` consecutive iterations;
 *  - report the per-iteration drop count (`SDDPIterationResult::
 *    apertures_dropped`);
 *  - refuse to declare convergence on an iteration whose backward pass
 *    dropped apertures; and
 *  - print an end-of-run aperture summary (`summary()`,
 *    `log_aperture_summary`).
 *
 * Thread-safe: `record` is called concurrently from the per-scene
 * backward-pass tasks.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <span>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include <gtopt/basic_types.hpp>
#include <gtopt/iteration.hpp>
#include <gtopt/phase.hpp>
#include <gtopt/scene.hpp>

namespace gtopt
{

/// Consecutive iterations an aperture must be dropped before the
/// escalated "persistent aperture infeasibility" warning is emitted.
inline constexpr int default_sddp_aperture_drop_warn_after = 3;

/// Number of phases / apertures listed in the end-of-run summary.
inline constexpr std::size_t aperture_summary_top_n = 3;

/// One dropped aperture of a (scene, phase) cell.
struct ApertureDrop
{
  Uid aperture_uid {unknown_uid};
  /// true = proven infeasible; false = other non-optimal outcome (time
  /// limit, abandoned / numerical, non-optimal status).
  bool infeasible {true};
};

/// One (scene, phase, aperture) triple that just crossed the
/// persistent-drop threshold.
struct PersistentApertureDrop
{
  Uid aperture_uid {unknown_uid};
  int consecutive {};  ///< consecutive iterations dropped (== threshold)
};

/// What `ApertureDropTracker::record` learned from one cell's drops.
struct ApertureDropRecord
{
  /// Triples that crossed the threshold with this record (each one is
  /// reported exactly once over the tracker's lifetime).
  std::vector<PersistentApertureDrop> newly_persistent {};
  /// True when every dropped aperture of this cell had ALREADY been
  /// escalated before this record — the caller can then demote its
  /// per-cell drop message to avoid flooding the log.
  bool all_already_persistent {false};
};

/// Aperture solve / drop counts of one iteration.
struct ApertureIterationCounts
{
  IterationIndex iteration_index {};
  std::int64_t solves {};  ///< aperture LPs solved (feasible + dropped)
  std::int64_t infeasible {};  ///< dropped: proven infeasible
  std::int64_t failed {};  ///< dropped: time limit / numerical / other

  [[nodiscard]] constexpr auto dropped() const noexcept -> std::int64_t
  {
    return infeasible + failed;
  }
};

/// (uid, drop count) entry of the summary's top lists.
struct ApertureDropCount
{
  Uid uid {unknown_uid};
  std::int64_t drops {};
};

/// End-of-solve aperture statistics (`ApertureDropTracker::summary()`).
struct ApertureSummary
{
  std::int64_t solves {};  ///< aperture LPs solved over the whole solve
  std::int64_t infeasible {};  ///< dropped as proven infeasible
  std::int64_t failed {};  ///< dropped for another non-optimal status
  int iterations {};  ///< iterations with at least one aperture solve
  /// Distinct (scene, phase, aperture) triples ever dropped.
  int distinct_dropped {};
  /// Distinct triples dropped in the LAST recorded iteration.
  int dropped_in_last_iteration {};
  /// Triples escalated as persistent (dropped `warn_after` consecutive
  /// iterations at least once).
  int persistent {};
  std::optional<ApertureIterationCounts> first {};
  std::optional<ApertureIterationCounts> last {};
  /// Phases / apertures with the most drops (descending, <= top_n each).
  std::vector<ApertureDropCount> top_phases {};
  std::vector<ApertureDropCount> top_apertures {};

  [[nodiscard]] constexpr auto dropped() const noexcept -> std::int64_t
  {
    return infeasible + failed;
  }

  /// Dropped share of the aperture solves (0 when nothing was solved).
  [[nodiscard]] constexpr auto dropped_fraction() const noexcept -> double
  {
    return solves > 0
        ? static_cast<double>(dropped()) / static_cast<double>(solves)
        : 0.0;
  }

  /// Add another solve's counts (cascade total).  The additive counters
  /// are summed; `last` and `dropped_in_last_iteration` take `other`'s
  /// end state; `first` and the top lists are kept from the first
  /// non-empty solve.
  void accumulate(const ApertureSummary& other);
};

class ApertureDropTracker
{
public:
  explicit ApertureDropTracker(
      int warn_after = default_sddp_aperture_drop_warn_after) noexcept
      : m_warn_after_(warn_after < 1 ? 1 : warn_after)
  {
  }

  /// Record one (scene, phase) cell of the backward pass of
  /// `iteration_index`: `n_solves` aperture LPs were solved and `drops`
  /// of them were dropped.  A streak breaks implicitly when an
  /// iteration records no drop for the triple; recording the same
  /// triple twice in one iteration counts it once.
  ApertureDropRecord record(SceneUid scene_uid,
                            PhaseUid phase_uid,
                            IterationIndex iteration_index,
                            int n_solves,
                            std::span<const ApertureDrop> drops);

  /// Convenience overload: every listed aperture proven infeasible and
  /// `n_solves = dropped_apertures.size()` (unit tests).
  ApertureDropRecord record(SceneUid scene_uid,
                            PhaseUid phase_uid,
                            IterationIndex iteration_index,
                            std::span<const Uid> dropped_apertures);

  /// Number of aperture drops recorded for `iteration_index`, summed
  /// over every (scene, phase) cell.
  [[nodiscard]] int drops_in_iteration(IterationIndex iteration_index) const;

  /// Number of aperture drops recorded for one scene at
  /// `iteration_index`.
  [[nodiscard]] int scene_drops_in_iteration(
      SceneUid scene_uid, IterationIndex iteration_index) const;

  /// Number of (scene, phase, aperture) triples escalated as persistent.
  [[nodiscard]] int persistent_count() const;

  /// Total drops recorded over the tracker's lifetime.
  [[nodiscard]] int total_drops() const;

  /// End-of-solve statistics.
  [[nodiscard]] ApertureSummary summary(
      std::size_t top_n = aperture_summary_top_n) const;

  [[nodiscard]] constexpr int warn_after() const noexcept
  {
    return m_warn_after_;
  }

  /// Forget everything (start of a new solve).
  void reset();

private:
  struct Streak
  {
    int last_iteration {-1};
    int consecutive {0};
    bool escalated {false};
  };

  using TripleKey = std::tuple<uid_t, uid_t, uid_t>;  // scene, phase, ap
  using SceneIterKey = std::pair<uid_t, int>;  // scene, iteration

  int m_warn_after_;
  mutable std::mutex m_mutex_;
  std::map<TripleKey, Streak> m_streaks_;
  std::map<int, ApertureIterationCounts> m_iteration_counts_;
  std::map<SceneIterKey, int> m_scene_iteration_drops_;
  std::map<uid_t, std::int64_t> m_phase_drops_;
  std::map<uid_t, std::int64_t> m_aperture_drops_;
  int m_persistent_count_ {0};
  int m_total_drops_ {0};
};

/// Log `summary` as a compact INFO block (nothing when no aperture was
/// solved).  `prefix` names the run, e.g. "SDDP" or "Cascade [L1]".
void log_aperture_summary(const ApertureSummary& summary,
                          std::string_view prefix);

}  // namespace gtopt
