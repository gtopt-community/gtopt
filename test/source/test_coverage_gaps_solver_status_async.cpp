// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_coverage_gaps_solver_status_async.cpp
 * @brief     solver-status JSON: async block, lp_task_stats, scene arrays
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Coverage audit 2026-11-22: the async-mode block (`max_async_spread >
 * 0`), the `lp_task_stats` block and the per-result `scene_iterations`
 * array of `build_iteration_status_json` (solver_status.cpp 178-228,
 * 268-273) had zero hits.  `gtopt_monitor` / `run_gtopt` parse this file
 * with a strict JSON parser, so a stray comma in a rarely-emitted block
 * breaks the live dashboard only in async runs.
 */

#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/sddp_types.hpp>
#include <gtopt/solver_monitor.hpp>
#include <gtopt/solver_status.hpp>

using namespace gtopt;

namespace test_coverage_gaps_solver_status_async_ns
{
namespace
{

/// Minimal structural JSON check: brackets balance outside strings and no
/// trailing comma precedes a closing bracket.
[[nodiscard]] bool looks_like_valid_json(std::string_view doc)
{
  int depth = 0;
  bool in_string = false;
  char last_significant = '\0';
  for (std::size_t i = 0; i < doc.size(); ++i) {
    const char c = doc[i];
    if (in_string) {
      if (c == '\\') {
        ++i;
      } else if (c == '"') {
        in_string = false;
        last_significant = '"';
      }
      continue;
    }
    switch (c) {
      case '"':
        in_string = true;
        break;
      case '{':
      case '[':
        ++depth;
        last_significant = c;
        break;
      case '}':
      case ']':
        if (last_significant == ',') {
          return false;
        }
        --depth;
        if (depth < 0) {
          return false;
        }
        last_significant = c;
        break;
      case ' ':
      case '\n':
      case '\t':
      case '\r':
        break;
      default:
        last_significant = c;
        break;
    }
  }
  return depth == 0 && !in_string;
}

}  // namespace
}  // namespace test_coverage_gaps_solver_status_async_ns

using test_coverage_gaps_solver_status_async_ns::looks_like_valid_json;

TEST_CASE("build_iteration_status_json emits async + lp_task_stats blocks")
{
  const SolverStatusSnapshot snap {
      .iteration_index = IterationIndex {3},
      .gap = 0.02,
      .lower_bound = 980.0,
      .upper_bound = 1000.0,
      .max_iterations = 50,
      .min_iterations = 2,
      .solver = "highs/1.15",
      .method = "sddp",
      .scene_iterations = {3, 4, 2},
      .scene_states = {"training", "simulation", "done"},
      .converged_scenes = 1,
      .spread = 2,
      .max_async_spread = 3,
      .pool_tasks_pending = 5,
      .pool_tasks_active = 4,
      .pool_cpu_load = 87.5,
      .lp_tasks_dispatched = 42,
      .avg_lp_task_cpu_pct = 95.25,
      .avg_lp_task_rss_delta_mb = 12.5,
  };

  std::vector<SDDPIterationResult> results(2);
  results[0].iteration_index = IterationIndex {1};
  results[0].scene_upper_bounds = {1.0, 2.0};
  results[0].scene_lower_bounds = {0.5, 1.5};
  results[0].scene_iterations = {1, 1, 1};
  results[1].iteration_index = IterationIndex {2};
  results[1].scene_iterations = {2, 3, 1};

  std::string doc = build_iteration_status_json(results, 2.5, snap);

  SUBCASE("async block content")
  {
    CHECK(doc.contains("\"async\": {"));
    CHECK(doc.contains("\"max_async_spread\": 3"));
    CHECK(doc.contains("\"converged_scenes\": 1"));
    CHECK(doc.contains("\"spread\": 2"));
    CHECK(doc.contains("\"pool_tasks_pending\": 5"));
    CHECK(doc.contains("\"pool_tasks_active\": 4"));
    CHECK(doc.contains("\"pool_cpu_load\": 87.5"));
    CHECK(doc.contains("\"scene_iterations\": [3, 4, 2]"));
    CHECK(doc.contains(
        "\"scene_states\": [\"training\", \"simulation\", \"done\"]"));
  }

  SUBCASE("lp_task_stats block content")
  {
    CHECK(doc.contains("\"lp_task_stats\": {"));
    CHECK(doc.contains("\"dispatched\": 42"));
    CHECK(doc.contains("\"avg_cpu_pct\": 95.2"));
    CHECK(doc.contains("\"avg_rss_delta_mb\": 12.5"));
  }

  SUBCASE("per-result scene_iterations arrays")
  {
    CHECK(doc.contains("\"scene_iterations\": [1, 1, 1]"));
    CHECK(doc.contains("\"scene_iterations\": [2, 3, 1]"));
    CHECK(doc.contains("\"scene_upper_bounds\": [1.000000, 2.000000]"));
  }

  SUBCASE("completed document is structurally valid JSON")
  {
    SolverMonitor monitor;  // no thread started
    monitor.build_realtime_status_json(doc);
    CHECK(looks_like_valid_json(doc));
  }
}

TEST_CASE("build_iteration_status_json omits async / lp_task_stats when idle")
{
  const SolverStatusSnapshot snap {
      .iteration_index = IterationIndex {1},
      .max_iterations = 10,
  };
  std::string doc = build_iteration_status_json({}, 0.0, snap);
  CHECK_FALSE(doc.contains("\"async\""));
  CHECK_FALSE(doc.contains("\"lp_task_stats\""));
  SolverMonitor monitor;
  monitor.build_realtime_status_json(doc);
  CHECK(looks_like_valid_json(doc));
}
