// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_memory_options.cpp
 * @brief     Every memory-reduction behaviour is selectable by an option
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Each memory reduction keeps the previous behaviour reachable:
 *
 *  | option                                      | default | false/true =  |
 *  |---------------------------------------------|---------|---------------|
 *  | lp_matrix_options.label_meta_without_names  | false   | true: keep    |
 *  | lp_matrix_options.share_label_meta          | true    | false: copy   |
 *  | lp_matrix_options.drop_element_collections  | true    | false: keep   |
 *  | sddp_options.release_after_alpha            | true    | false: live   |
 *  | sddp_options.free_solver_scratch            | true    | false: keep   |
 *  | sddp_options.release_backends               | false   | true: release |
 *
 * For each: JSON parsing, the resolved default, and both values on a real
 * build / SDDP run, with identical results either way.
 */

#include <string_view>
#include <utility>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_lp_matrix_options.hpp>
#include <gtopt/json/json_sddp_options.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>
#include <gtopt/solver_options.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

[[nodiscard]] PlanningOptionsLP options_with(PlanningOptions po)
{
  return PlanningOptionsLP {std::move(po)};
}

struct SddpRun
{
  double ub {0.0};
  double lb {0.0};
};

/// SDDP on the 2-scene 3-phase hydro fixture; @p tune edits the planning
/// options, @p tune_sddp the SDDPOptions; @p probe inspects the PlanningLP
/// after the run.
template<typename Tune, typename TuneSddp, typename Probe>
[[nodiscard]] SddpRun run_sddp(Tune&& tune, TuneSddp&& tune_sddp, Probe&& probe)
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  planning.options.method = MethodType::sddp;
  std::forward<Tune>(tune)(planning.options);
  PlanningLP planning_lp(std::move(planning));

  SDDPOptions opts;
  opts.max_iterations = 5;
  opts.convergence_tol = 1e-9;
  opts.stationary_tol = 0.0;
  opts.enable_api = false;
  opts.low_memory_mode = planning_lp.options().sddp_low_memory();
  std::forward<TuneSddp>(tune_sddp)(opts);
  SDDPMethod sddp(planning_lp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  std::forward<Probe>(probe)(planning_lp);
  return {
      .ub = results->back().upper_bound,
      .lb = results->back().lower_bound,
  };
}

constexpr auto kNoTune = [](auto&) {};
constexpr auto kNoProbe = [](PlanningLP&) {};

}  // namespace

TEST_CASE("memory options - JSON parsing and defaults")  // NOLINT
{
  SUBCASE("lp_matrix_options")
  {
    const auto unset = daw::json::from_json<LpMatrixOptions>("{}");
    CHECK_FALSE(unset.label_meta_without_names.has_value());
    CHECK_FALSE(unset.share_label_meta.has_value());
    CHECK_FALSE(unset.drop_element_collections.has_value());

    const auto set = daw::json::from_json<LpMatrixOptions>(
        R"({"label_meta_without_names": true, "share_label_meta": false,
            "drop_element_collections": false})");
    CHECK(set.label_meta_without_names.value_or(false));
    CHECK_FALSE(set.share_label_meta.value_or(true));
    CHECK_FALSE(set.drop_element_collections.value_or(true));

    // Round trip.
    const auto rt =
        daw::json::from_json<LpMatrixOptions>(daw::json::to_json(set));
    CHECK(rt.label_meta_without_names == set.label_meta_without_names);
    CHECK(rt.share_label_meta == set.share_label_meta);
    CHECK(rt.drop_element_collections == set.drop_element_collections);

    // Unset fields are filled from the planning-level options.
    LpMatrixOptions explicit_opts;
    explicit_opts.fill_unset_from(set);
    CHECK(explicit_opts.label_meta_without_names
          == set.label_meta_without_names);
    CHECK(explicit_opts.share_label_meta == set.share_label_meta);
    CHECK(explicit_opts.drop_element_collections
          == set.drop_element_collections);

    CHECK(options_with({}).drop_element_collections());
    PlanningOptions po;
    po.lp_matrix_options.drop_element_collections = false;
    CHECK_FALSE(options_with(std::move(po)).drop_element_collections());
  }

  SUBCASE("sddp_options")
  {
    const auto unset = daw::json::from_json<SddpOptions>("{}");
    CHECK_FALSE(unset.release_after_alpha.has_value());
    CHECK_FALSE(unset.free_solver_scratch.has_value());
    CHECK_FALSE(unset.release_backends.has_value());

    const auto set = daw::json::from_json<SddpOptions>(
        R"({"release_after_alpha": false, "free_solver_scratch": false,
            "release_backends": true})");
    CHECK_FALSE(set.release_after_alpha.value_or(true));
    CHECK_FALSE(set.free_solver_scratch.value_or(true));
    CHECK(set.release_backends.value_or(false));

    const auto rt = daw::json::from_json<SddpOptions>(daw::json::to_json(set));
    CHECK(rt.release_after_alpha == set.release_after_alpha);
    CHECK(rt.free_solver_scratch == set.free_solver_scratch);
    CHECK(rt.release_backends == set.release_backends);

    SddpOptions merged;
    merged.merge(SddpOptions {set});
    CHECK(merged.release_after_alpha == set.release_after_alpha);
    CHECK(merged.free_solver_scratch == set.free_solver_scratch);
    CHECK(merged.release_backends == set.release_backends);

    const auto defaults = options_with({});
    CHECK(defaults.sddp_release_after_alpha());
    CHECK(defaults.sddp_free_solver_scratch());
    CHECK_FALSE(defaults.sddp_release_backends());

    // The SDDPOptions struct defaults match the resolved defaults.
    const SDDPOptions sddp_defaults;
    CHECK(sddp_defaults.release_after_alpha);
    CHECK(sddp_defaults.free_solver_scratch);
    CHECK_FALSE(sddp_defaults.release_backends);
  }

  SUBCASE("release_backends resolves off to an uncompressed compress")
  {
    PlanningOptions po;
    po.sddp_options.low_memory_mode = LowMemoryMode::off;
    po.sddp_options.release_backends = true;
    const auto o = options_with(po);
    CHECK(o.sddp_low_memory() == LowMemoryMode::compress);
    CHECK(o.sddp_memory_codec() == CompressionCodec::uncompressed);

    po.sddp_options.release_backends = false;
    const auto live = options_with(po);
    CHECK(live.sddp_low_memory() == LowMemoryMode::off);

    // An explicit compress keeps its codec.
    po.sddp_options.low_memory_mode = LowMemoryMode::compress;
    po.sddp_options.release_backends = true;
    const auto c = options_with(std::move(po));
    CHECK(c.sddp_low_memory() == LowMemoryMode::compress);
    CHECK(c.sddp_memory_codec() == CompressionCodec::auto_select);
  }
}

TEST_CASE(
    "memory options - label_meta_without_names keeps the labels")  // NOLINT
{
  for (const bool keep : {false, true}) {
    CAPTURE(keep);
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    planning.options.lp_matrix_options.label_meta_without_names = keep;
    const PlanningLP plp(std::move(planning));
    const auto& li =
        plp.system(first_scene_index(), first_phase_index()).linear_interface();
    if (keep) {
      CHECK(li.frozen_col_labels_meta_size() == li.flatten_col_count());
      CHECK(li.frozen_row_labels_meta_size() > 0);
      CHECK(li.row_label_at(RowIndex {0}) != nullptr);
    } else {
      CHECK(li.frozen_col_labels_meta_size() == 0);
      CHECK(li.frozen_row_labels_meta_size() == 0);
      CHECK(li.row_label_at(RowIndex {0}) == nullptr);
    }
  }
}

TEST_CASE("memory options - share_label_meta aliases or copies")  // NOLINT
{
  for (const bool share : {true, false}) {
    CAPTURE(share);
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    planning.options.lp_matrix_options.label_meta_without_names = true;
    planning.options.lp_matrix_options.share_label_meta = share;
    const PlanningLP plp(std::move(planning));
    const auto& li =
        plp.system(first_scene_index(), first_phase_index()).linear_interface();
    CHECK(li.share_label_meta() == share);
    REQUIRE(li.frozen_col_labels_meta_size() > 0);
    if (share) {
      // One control block shared with the snapshot's FlatLpMeta.
      CHECK(li.col_labels_meta_use_count() > 1);
    } else {
      // The previous behaviour: the LI owns its own copy.
      CHECK(li.col_labels_meta_use_count() == 1);
    }
  }
}

TEST_CASE("memory options - drop_element_collections")  // NOLINT
{
  for (const bool drop : {true, false}) {
    CAPTURE(drop);
    const auto r = run_sddp(
        [&](PlanningOptions& o)
        {
          o.sddp_options.low_memory_mode = LowMemoryMode::off;
          o.lp_matrix_options.drop_element_collections = drop;
        },
        kNoTune,
        [&](PlanningLP& plp)
        {
          const auto& sys =
              plp.system(first_scene_index(), first_phase_index());
          CHECK(sys.low_memory_mode() == LowMemoryMode::off);
          CHECK(sys.collections_droppable() == drop);
        });
    static SddpRun ref {};
    if (drop) {
      ref = r;
    } else {
      CHECK(r.ub == doctest::Approx(ref.ub).epsilon(1e-9));
      CHECK(r.lb == doctest::Approx(ref.lb).epsilon(1e-9));
    }
  }
}

TEST_CASE("memory options - release_after_alpha")  // NOLINT
{
  for (const bool release : {true, false}) {
    CAPTURE(release);
    auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
    planning.options.method = MethodType::sddp;
    planning.options.sddp_options.low_memory_mode = LowMemoryMode::compress;
    PlanningLP plp(std::move(planning));
    const auto s0 = first_scene_index();
    register_alpha_variables(plp,
                             s0,
                             /*scale_alpha=*/1.0,
                             CutSharingMode::none,
                             BoundaryCutSharingMode::per_scene,
                             /*register_as_state_variable=*/true,
                             /*markov=*/nullptr,
                             /*terminal_phi_count=*/0,
                             /*release_after=*/release);
    // Every non-terminal phase got α through add_col, which rebuilt its
    // backend; it is released again only when the option is on.
    const auto& li = plp.system(s0, first_phase_index()).linear_interface();
    CHECK(li.is_backend_released() == release);
  }

  // Same bounds either way on a full SDDP run.
  std::vector<SddpRun> runs;
  for (const bool release : {true, false}) {
    runs.push_back(
        run_sddp([](PlanningOptions& o)
                 { o.sddp_options.low_memory_mode = LowMemoryMode::compress; },
                 [&](SDDPOptions& s) { s.release_after_alpha = release; },
                 kNoProbe));
  }
  CHECK(runs[1].ub == doctest::Approx(runs[0].ub).epsilon(1e-9));
  CHECK(runs[1].lb == doctest::Approx(runs[0].lb).epsilon(1e-9));
}

TEST_CASE("memory options - free_solver_scratch")  // NOLINT
{
  std::vector<SddpRun> runs;
  for (const bool free_scratch : {true, false}) {
    CAPTURE(free_scratch);
    bool any_scratch = false;
    runs.push_back(run_sddp(
        [](PlanningOptions& o)
        { o.sddp_options.low_memory_mode = LowMemoryMode::off; },
        [&](SDDPOptions& s) { s.free_solver_scratch = free_scratch; },
        [&](PlanningLP& plp)
        {
          const auto& sim = plp.simulation();
          for (const auto si : iota_range<SceneIndex>(0, sim.scene_count())) {
            for (const auto pi : iota_range<PhaseIndex>(0, sim.phase_count())) {
              const auto& li = plp.system(si, pi).linear_interface();
              any_scratch = any_scratch || li.backend_scratch_bytes() > 0;
            }
          }
        }));
    if (free_scratch) {
      // The post-iteration sweep freed every cell's scratch.
      CHECK_FALSE(any_scratch);
    }
    // With the option off the scratch may stay (CPLEX / Gurobi / MindOpt);
    // HiGHS and OSI have none, so nothing to assert there.
  }
  CHECK(runs[1].ub == doctest::Approx(runs[0].ub).epsilon(1e-9));
  CHECK(runs[1].lb == doctest::Approx(runs[0].lb).epsilon(1e-9));
}

TEST_CASE("memory options - release_backends under off")  // NOLINT
{
  std::vector<SddpRun> runs;
  for (const bool release : {false, true}) {
    CAPTURE(release);
    runs.push_back(run_sddp(
        [&](PlanningOptions& o)
        {
          o.sddp_options.low_memory_mode = LowMemoryMode::off;
          o.sddp_options.release_backends = release;
        },
        kNoTune,
        [&](PlanningLP& plp)
        {
          const auto& li = plp.system(first_scene_index(), first_phase_index())
                               .linear_interface();
          if (release) {
            // Released after the run, as under compress.
            CHECK(li.low_memory_mode() == LowMemoryMode::compress);
            CHECK(li.is_backend_released());
          } else {
            CHECK(li.low_memory_mode() == LowMemoryMode::off);
            CHECK_FALSE(li.is_backend_released());
          }
        }));
  }
  CHECK(runs[1].ub == doctest::Approx(runs[0].ub).epsilon(1e-6));
  CHECK(runs[1].lb == doctest::Approx(runs[0].lb).epsilon(1e-6));
}
