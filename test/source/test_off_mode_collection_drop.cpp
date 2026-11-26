// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_off_mode_collection_drop.cpp
 * @brief     SDDP / cascade drop the disposable collections under
 *            `low_memory = off` too (memory reductions, item 6)
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Under `off` every cell used to keep all element LP wrappers (GeneratorLP,
 * LineLP, DemandLP, ...) for the whole run: ~10.7 GiB of the 26 GiB peak on
 * the CEN 2y case.  SDDP and cascade now drop the disposable
 * (non-`HasUpdateLP`) ones after the build and rebuild them for write_out,
 * exactly as `compress` does.  Monolithic keeps them.  These tests pin:
 * the drop happens only where intended; `update_lp` keeps working (its
 * types stay resident); and SDDP / cascade bounds and every output file are
 * identical with and without the drop, for parquet and csv.
 */

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include <dlfcn.h>
#include <doctest/doctest.h>
#include <gtopt/cascade_method.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/system_lp.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{
namespace fs = std::filesystem;

[[nodiscard]] auto read_tree(const fs::path& root)
    -> std::map<std::string, std::string>
{
  std::map<std::string, std::string> out;
  for (const auto& e : fs::recursive_directory_iterator(root)) {
    if (!e.is_regular_file()) {
      continue;
    }
    const auto rel = fs::relative(e.path(), root).string();
    // Run telemetry (wall time, solve counts) legitimately differs.
    if (rel.contains("solution") || rel.contains("log")
        || rel.contains("status") || rel.contains("monitor"))
    {
      continue;
    }
    std::ifstream f(e.path(), std::ios::binary);
    out[rel] = std::string {std::istreambuf_iterator<char>(f),
                            std::istreambuf_iterator<char>()};
  }
  return out;
}

struct RunResult
{
  std::vector<double> lb {};
  std::vector<double> ub {};
  std::map<std::string, std::string> files {};
  bool dropped_after_build {false};
};

[[nodiscard]] auto run_sddp(bool drop, DataFormat fmt, const std::string& tag)
    -> RunResult
{
  const auto dir = fs::temp_directory_path()
      / std::format("gtopt_off_drop_{}_{}_{}",
                    tag,
                    drop ? "drop" : "keep",
                    fmt == DataFormat::csv ? "csv" : "parquet");
  fs::remove_all(dir);
  fs::create_directories(dir);

  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  planning.options.output_directory = dir.string();
  planning.options.output_format = fmt;
  LpMatrixOptions flat_opts;
  flat_opts.drop_disposable_collections = drop;
  PlanningLP planning_lp(std::move(planning), flat_opts);

  RunResult rr;
  auto& sys0 = planning_lp.system(first_scene_index(), first_phase_index());
  rr.dropped_after_build = !sys0.disposable_collections_resident();
  // HasUpdateLP types stay resident either way.
  CHECK(sys0.collections_resident());

  SDDPOptions opts;
  opts.max_iterations = 6;
  opts.convergence_tol = 1e-6;
  opts.enable_api = false;
  opts.low_memory_mode = LowMemoryMode::off;
  SDDPMethod sddp(planning_lp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  for (const auto& r : *results) {
    rr.lb.push_back(r.lower_bound);
    rr.ub.push_back(r.upper_bound);
  }
  planning_lp.write_out();
  rr.files = read_tree(dir);
  fs::remove_all(dir);
  return rr;
}

}  // namespace

TEST_CASE("off-mode collection drop: only SDDP / cascade opt in")  // NOLINT
{
  for (const auto method :
       {MethodType::monolithic, MethodType::sddp, MethodType::cascade})
  {
    CAPTURE(static_cast<int>(method));
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    planning.options.method = method;
    planning.options.sddp_options.low_memory_mode = LowMemoryMode::off;
    // Live backends (release_backends defaults to true, which resolves
    // SDDP / cascade `off` to an uncompressed `compress`).
    planning.options.sddp_options.release_backends = false;
    const PlanningLP planning_lp(std::move(planning));
    const auto& sys =
        planning_lp.system(first_scene_index(), first_phase_index());
    CHECK(sys.low_memory_mode() == LowMemoryMode::off);
    CHECK(sys.collections_resident());
    const bool expect_drop = method != MethodType::monolithic;
    CHECK(sys.collections_droppable() == expect_drop);
    CHECK(sys.disposable_collections_resident() == !expect_drop);
  }
}

TEST_CASE(
    "off-mode collection drop: rebuild restores the disposables")  // NOLINT
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  LpMatrixOptions flat_opts;
  flat_opts.drop_disposable_collections = true;
  PlanningLP planning_lp(std::move(planning), flat_opts);
  auto& sys = planning_lp.system(first_scene_index(), first_phase_index());
  REQUIRE_FALSE(sys.disposable_collections_resident());
  // update_lp walks only HasUpdateLP types: it must not rehydrate.
  std::ignore = sys.update_lp();
  CHECK_FALSE(sys.disposable_collections_resident());
  sys.rebuild_collections_if_needed();
  CHECK(sys.disposable_collections_resident());
  CHECK_FALSE(sys.elements<GeneratorLP>().empty());
  sys.clear_disposable_collections();
  CHECK_FALSE(sys.disposable_collections_resident());
  CHECK(sys.collections_resident());
}

TEST_CASE(  // NOLINT
    "off-mode collection drop: SDDP bounds and outputs identical")
{
  for (const auto fmt : {DataFormat::parquet, DataFormat::csv}) {
    CAPTURE(static_cast<int>(fmt));
    const auto keep = run_sddp(/*drop=*/false, fmt, "sddp");
    const auto drop = run_sddp(/*drop=*/true, fmt, "sddp");
    CHECK_FALSE(keep.dropped_after_build);
    CHECK(drop.dropped_after_build);
    REQUIRE(keep.lb.size() == drop.lb.size());
    for (std::size_t i = 0; i < keep.lb.size(); ++i) {
      CHECK(drop.lb[i] == keep.lb[i]);
      CHECK(drop.ub[i] == keep.ub[i]);
    }
    REQUIRE_FALSE(keep.files.empty());
    CHECK(drop.files.size() == keep.files.size());
    for (const auto& [name, content] : keep.files) {
      CAPTURE(name);
      const auto it = drop.files.find(name);
      REQUIRE(it != drop.files.end());
      CHECK(it->second == content);
    }
  }
}

TEST_CASE(  // NOLINT
    "off-mode collection drop: cascade runs and matches the kept run")
{
  const auto run = [](MethodType method) -> std::vector<double>
  {
    auto planning = make_3phase_hydro_planning();
    planning.options.method = method;
    PlanningLP planning_lp(std::move(planning));
    SDDPOptions base;
    base.max_iterations = 4;
    base.apertures = std::vector<Uid> {};
    base.enable_api = false;
    base.low_memory_mode = LowMemoryMode::off;
    CascadeOptions cascade;
    cascade.level_array = {
        CascadeLevel {
            .name = OptName {"a"},
            .sddp_options =
                CascadeLevelMethod {
                    .max_iterations = OptInt {4},
                    .apertures = Array<Uid> {},
                },
        },
        CascadeLevel {
            .name = OptName {"b"},
            .sddp_options =
                CascadeLevelMethod {
                    .max_iterations = OptInt {4},
                    .apertures = Array<Uid> {},
                },
        },
    };
    CascadePlanningMethod solver(std::move(base), std::move(cascade));
    const auto res = solver.solve(planning_lp, SolverOptions {});
    REQUIRE(res.has_value());
    std::vector<double> out;
    for (const auto& ls : solver.level_stats()) {
      out.push_back(ls.lower_bound);
      out.push_back(ls.upper_bound);
    }
    return out;
  };
  // `monolithic` here only selects the build (no drop); the cascade solver
  // runs either way.
  const auto kept = run(MethodType::monolithic);
  const auto dropped = run(MethodType::cascade);
  REQUIRE(kept.size() == dropped.size());
  for (std::size_t i = 0; i < kept.size(); ++i) {
    CHECK(dropped[i] == doctest::Approx(kept[i]).epsilon(1e-12));
  }
}

namespace
{

using MallctlFn = int (*)(const char*, void*, std::size_t*, void*, std::size_t);

/// jemalloc's `stats.allocated`, or nullopt when the test binary does not
/// run on jemalloc (the default for gtoptTests; the check then skips).
[[nodiscard]] auto jemalloc_allocated() -> std::optional<std::size_t>
{
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  const auto fn = reinterpret_cast<MallctlFn>(dlsym(RTLD_DEFAULT, "mallctl"));
  if (fn == nullptr) {
    return std::nullopt;
  }
  std::uint64_t epoch = 1;
  std::size_t esz = sizeof(epoch);
  fn("epoch", &epoch, &esz, &epoch, esz);
  std::size_t allocated = 0;
  std::size_t sz = sizeof(allocated);
  if (fn("stats.allocated", &allocated, &sz, nullptr, 0) != 0) {
    return std::nullopt;
  }
  return allocated;
}

}  // namespace

TEST_CASE(  // NOLINT
    "off-mode collection drop: resident bytes after build shrink "
    "(jemalloc only)")
{
  if (!jemalloc_allocated().has_value()) {
    MESSAGE("jemalloc not loaded — memory regression check skipped");
    return;
  }
  const auto measure = [](bool drop) -> std::size_t
  {
    const auto before = jemalloc_allocated().value_or(0);
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    LpMatrixOptions flat_opts;
    flat_opts.drop_disposable_collections = drop;
    const PlanningLP planning_lp(std::move(planning), flat_opts);
    const auto after = jemalloc_allocated().value_or(0);
    return after > before ? after - before : 0;
  };
  const auto kept = measure(false);
  const auto dropped = measure(true);
  CAPTURE(kept);
  CAPTURE(dropped);
  CHECK(dropped < kept);
}
