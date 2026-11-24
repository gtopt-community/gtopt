/**
 * @file      sddp_aperture_drops.cpp
 * @brief     Cross-iteration bookkeeping of dropped (infeasible) apertures
 * @date      2026-11-24
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <format>
#include <ranges>
#include <string>

#include <gtopt/sddp_aperture_drops.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

[[nodiscard]] auto top_by_drops(const std::map<uid_t, std::int64_t>& counts,
                                std::size_t top_n)
    -> std::vector<ApertureDropCount>
{
  std::vector<ApertureDropCount> out;
  out.reserve(counts.size());
  for (const auto& [uid, drops] : counts) {
    if (drops > 0) {
      out.push_back(ApertureDropCount {
          .uid = uid,
          .drops = drops,
      });
    }
  }
  // Most drops first; ties by ascending uid (deterministic output).
  std::ranges::sort(
      out,
      [](const ApertureDropCount& a, const ApertureDropCount& b)
      { return a.drops != b.drops ? a.drops > b.drops : a.uid < b.uid; });
  if (out.size() > top_n) {
    out.resize(top_n);
  }
  return out;
}

[[nodiscard]] auto format_top(const std::vector<ApertureDropCount>& top,
                              char tag) -> std::string
{
  std::string s;
  for (const auto& e : top) {
    if (!s.empty()) {
      s += ", ";
    }
    s += std::format("{}{} ({})", tag, e.uid, e.drops);
  }
  return s.empty() ? std::string {"-"} : s;
}

[[nodiscard]] auto format_iteration(const ApertureIterationCounts& c)
    -> std::string
{
  return std::format("i{} {}/{} dropped ({} infeasible, {} failed)",
                     uid_of(c.iteration_index),
                     c.dropped(),
                     c.solves,
                     c.infeasible,
                     c.failed);
}

}  // namespace

void ApertureSummary::accumulate(const ApertureSummary& other)
{
  const bool was_empty = solves == 0 && dropped() == 0;
  solves += other.solves;
  infeasible += other.infeasible;
  failed += other.failed;
  iterations += other.iterations;
  distinct_dropped += other.distinct_dropped;
  persistent += other.persistent;
  dropped_in_last_iteration = other.dropped_in_last_iteration;
  if (was_empty) {
    first = other.first;
    top_phases = other.top_phases;
    top_apertures = other.top_apertures;
  }
  if (other.last.has_value()) {
    last = other.last;
  }
}

ApertureDropRecord ApertureDropTracker::record(
    SceneUid scene_uid,
    PhaseUid phase_uid,
    IterationIndex iteration_index,
    int n_solves,
    std::span<const ApertureDrop> drops)
{
  ApertureDropRecord out;
  if (n_solves <= 0 && drops.empty()) {
    return out;
  }

  const auto scene = static_cast<uid_t>(scene_uid);
  const auto phase = static_cast<uid_t>(phase_uid);
  const auto iter = static_cast<int>(iteration_index);

  const std::scoped_lock lock(m_mutex_);
  auto& counts = m_iteration_counts_[iter];
  counts.iteration_index = iteration_index;
  counts.solves += std::max(n_solves, static_cast<int>(drops.size()));

  bool all_escalated = !drops.empty();
  int counted = 0;
  for (const auto& drop : drops) {
    auto& streak = m_streaks_[TripleKey {scene, phase, drop.aperture_uid}];
    if (streak.last_iteration == iter) {
      // Duplicate record within the same iteration — count once.
      all_escalated = all_escalated && streak.escalated;
      continue;
    }
    const bool was_escalated = streak.escalated;
    streak.consecutive =
        (streak.last_iteration == iter - 1) ? streak.consecutive + 1 : 1;
    streak.last_iteration = iter;
    ++counted;
    if (drop.infeasible) {
      ++counts.infeasible;
    } else {
      ++counts.failed;
    }
    ++m_phase_drops_[phase];
    ++m_aperture_drops_[drop.aperture_uid];

    if (!streak.escalated && streak.consecutive >= m_warn_after_) {
      streak.escalated = true;
      ++m_persistent_count_;
      out.newly_persistent.push_back(PersistentApertureDrop {
          .aperture_uid = drop.aperture_uid,
          .consecutive = streak.consecutive,
      });
    }
    all_escalated = all_escalated && was_escalated;
  }

  if (counted > 0) {
    m_scene_iteration_drops_[SceneIterKey {scene, iter}] += counted;
    m_total_drops_ += counted;
  }
  out.all_already_persistent = all_escalated;
  return out;
}

ApertureDropRecord ApertureDropTracker::record(
    SceneUid scene_uid,
    PhaseUid phase_uid,
    IterationIndex iteration_index,
    std::span<const Uid> dropped_apertures)
{
  std::vector<ApertureDrop> drops;
  drops.reserve(dropped_apertures.size());
  for (const auto ap : dropped_apertures) {
    drops.push_back(ApertureDrop {
        .aperture_uid = ap,
        .infeasible = true,
    });
  }
  return record(scene_uid,
                phase_uid,
                iteration_index,
                static_cast<int>(drops.size()),
                drops);
}

int ApertureDropTracker::drops_in_iteration(
    IterationIndex iteration_index) const
{
  const std::scoped_lock lock(m_mutex_);
  const auto it = m_iteration_counts_.find(static_cast<int>(iteration_index));
  return it == m_iteration_counts_.end()
      ? 0
      : static_cast<int>(it->second.dropped());
}

int ApertureDropTracker::scene_drops_in_iteration(
    SceneUid scene_uid, IterationIndex iteration_index) const
{
  const std::scoped_lock lock(m_mutex_);
  const auto it = m_scene_iteration_drops_.find(SceneIterKey {
      static_cast<uid_t>(scene_uid),
      static_cast<int>(iteration_index),
  });
  return it == m_scene_iteration_drops_.end() ? 0 : it->second;
}

int ApertureDropTracker::persistent_count() const
{
  const std::scoped_lock lock(m_mutex_);
  return m_persistent_count_;
}

int ApertureDropTracker::total_drops() const
{
  const std::scoped_lock lock(m_mutex_);
  return m_total_drops_;
}

ApertureSummary ApertureDropTracker::summary(std::size_t top_n) const
{
  const std::scoped_lock lock(m_mutex_);
  ApertureSummary s;
  for (const auto& c : m_iteration_counts_ | std::views::values) {
    s.solves += c.solves;
    s.infeasible += c.infeasible;
    s.failed += c.failed;
  }
  s.iterations = static_cast<int>(m_iteration_counts_.size());
  if (!m_iteration_counts_.empty()) {
    s.first = m_iteration_counts_.begin()->second;
    s.last = m_iteration_counts_.rbegin()->second;
  }
  const int last_iter =
      m_iteration_counts_.empty() ? -1 : m_iteration_counts_.rbegin()->first;
  for (const auto& streak : m_streaks_ | std::views::values) {
    ++s.distinct_dropped;
    if (streak.last_iteration == last_iter) {
      ++s.dropped_in_last_iteration;
    }
  }
  s.persistent = m_persistent_count_;
  s.top_phases = top_by_drops(m_phase_drops_, top_n);
  s.top_apertures = top_by_drops(m_aperture_drops_, top_n);
  return s;
}

void ApertureDropTracker::reset()
{
  const std::scoped_lock lock(m_mutex_);
  m_streaks_.clear();
  m_iteration_counts_.clear();
  m_scene_iteration_drops_.clear();
  m_phase_drops_.clear();
  m_aperture_drops_.clear();
  m_persistent_count_ = 0;
  m_total_drops_ = 0;
}

void log_aperture_summary(const ApertureSummary& summary,
                          std::string_view prefix)
{
  if (summary.solves <= 0) {
    return;
  }
  spdlog::info(
      "{}: apertures — {} LP(s) over {} iteration(s), {} dropped ({:.2f}%): "
      "{} infeasible, {} failed (time limit / numerical / non-optimal)",
      prefix,
      summary.solves,
      summary.iterations,
      summary.dropped(),
      100.0 * summary.dropped_fraction(),
      summary.infeasible,
      summary.failed);
  if (summary.dropped() <= 0) {
    return;
  }
  if (summary.first.has_value() && summary.last.has_value()) {
    spdlog::info("{}:   first {}; last {}",
                 prefix,
                 format_iteration(*summary.first),
                 format_iteration(*summary.last));
  }
  spdlog::info(
      "{}:   {} distinct (scene, phase, aperture) ever dropped, {} still "
      "dropped in the last iteration, {} escalated as persistent",
      prefix,
      summary.distinct_dropped,
      summary.dropped_in_last_iteration,
      summary.persistent);
  spdlog::info("{}:   most drops: phases {}; apertures {}",
               prefix,
               format_top(summary.top_phases, 'p'),
               format_top(summary.top_apertures, 'a'));
}

}  // namespace gtopt
