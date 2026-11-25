// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_lp_label_sharing.cpp
 * @brief     Label metadata is aliased, not deep-copied, and dropped when
 *            names are off (memory reductions, item 5)
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `load_flat` used to deep-copy the flat LP's label metadata into the
 * `LinearInterface` while `low_memory = off` also retained the flat LP as a
 * snapshot, so every cell carried the labels twice (~3 GiB on the CEN 2y
 * case).  The LI now aliases the snapshot's immutable `FlatLpMeta`, and
 * with LP names off no label metadata is kept at all (only the SDDP
 * investment master opts back in to the row labels).
 */

#include <filesystem>
#include <fstream>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_types.hpp>
#include <gtopt/solver_options.hpp>

#include "lp_names_helpers.hpp"
#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

[[nodiscard]] auto make_lp(bool names,
                           bool keep_rows = false,
                           LowMemoryMode mode = LowMemoryMode::off)
    -> std::unique_ptr<PlanningLP>
{
  auto planning = make_2scene_3phase_hydro_planning(0.5, 0.5);
  planning.options.sddp_options.low_memory_mode = mode;
  auto flat_opts =
      names ? test_support::names_on_flat_opts() : LpMatrixOptions {};
  flat_opts.keep_row_label_meta = keep_rows;
  flat_opts.low_memory_mode = mode;
  return std::make_unique<PlanningLP>(std::move(planning), flat_opts);
}

[[nodiscard]] auto slurp(const std::string& path) -> std::string
{
  std::ifstream in(path);
  std::stringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

[[nodiscard]] auto strip_header(std::string s) -> std::string
{
  const std::string marker = "\\Problem name";
  if (const auto p = s.find(marker); p != std::string::npos) {
    const auto end = s.find('\n', p);
    s.erase(p, end == std::string::npos ? std::string::npos : end - p + 1);
  }
  return s;
}

[[nodiscard]] auto solve_all(PlanningLP& plp) -> std::vector<double>
{
  std::vector<double> objs;
  const auto& sim = plp.simulation();
  for (const auto si : iota_range<SceneIndex>(0, sim.scene_count())) {
    for (const auto pi : iota_range<PhaseIndex>(0, sim.phase_count())) {
      auto& li = plp.system(si, pi).linear_interface();
      auto r = li.initial_solve(SolverOptions {.log_level = 0});
      REQUIRE(r.has_value());
      REQUIRE(li.is_optimal());
      objs.push_back(li.get_obj_value_raw());
    }
  }
  return objs;
}

}  // namespace

TEST_CASE("off mode, names off: no label metadata kept")  // NOLINT
{
  auto plp = make_lp(/*names=*/false);
  auto named = make_lp(/*names=*/true);
  const auto& sim = plp->simulation();
  for (const auto si : iota_range<SceneIndex>(0, sim.scene_count())) {
    for (const auto pi : iota_range<PhaseIndex>(0, sim.phase_count())) {
      const auto& li = plp->system(si, pi).linear_interface();
      CAPTURE(si);
      CAPTURE(pi);
      CHECK(li.frozen_col_labels_meta_size() == 0);
      CHECK(li.frozen_row_labels_meta_size() == 0);
      // Structural size is independent of whether labels were kept.
      CHECK(li.flatten_col_count()
            == static_cast<std::size_t>(li.get_numcols()));
      // Label lookups degrade to "no label", never to a wrong one.
      CHECK(li.col_label_at(ColIndex {0}) == nullptr);
      CHECK(li.row_label_at(RowIndex {0}) == nullptr);
      // The retained snapshot holds no label bundle either.
      const auto* snap = li.flat_lp_snapshot();
      REQUIRE(snap != nullptr);
      CHECK(snap->label_meta == nullptr);
      // The named build of the same cell does keep them.
      const auto& lin = named->system(si, pi).linear_interface();
      CHECK(lin.frozen_col_labels_meta_size() == lin.flatten_col_count());
      CHECK(lin.frozen_row_labels_meta_size() > 0);
    }
  }
  // Dropping labels never changes the LP.
  const auto a = solve_all(*plp);
  const auto b = solve_all(*named);
  REQUIRE(a.size() == b.size());
  for (std::size_t i = 0; i < a.size(); ++i) {
    CHECK(a[i] == doctest::Approx(b[i]).epsilon(1e-12));
  }
}

TEST_CASE("off mode, names off, keep_row_label_meta: rows only")  // NOLINT
{
  auto plp = make_lp(/*names=*/false, /*keep_rows=*/true);
  auto named = make_lp(/*names=*/true);
  const auto s0 = first_scene_index();
  const auto p0 = first_phase_index();
  const auto& li = plp->system(s0, p0).linear_interface();
  const auto& lin = named->system(s0, p0).linear_interface();
  CHECK(li.frozen_col_labels_meta_size() == 0);
  REQUIRE(li.frozen_row_labels_meta_size()
          == lin.frozen_row_labels_meta_size());
  for (Index r = 0; r < li.get_numrows(); ++r) {
    const auto* a = li.row_label_at(RowIndex {r});
    const auto* b = lin.row_label_at(RowIndex {r});
    REQUIRE(a != nullptr);
    REQUIRE(b != nullptr);
    CHECK(a->class_name == b->class_name);
    CHECK(a->constraint_name == b->constraint_name);
    CHECK(a->variable_uid == b->variable_uid);
  }
}

TEST_CASE("compress mode, names off: no label metadata kept")  // NOLINT
{
  auto plp =
      make_lp(/*names=*/false, /*keep_rows=*/false, LowMemoryMode::compress);
  auto& sys = plp->system(first_scene_index(), first_phase_index());
  sys.ensure_lp_built();
  CHECK(sys.linear_interface().frozen_col_labels_meta_size() == 0);
  CHECK(sys.linear_interface().frozen_row_labels_meta_size() == 0);
}

TEST_CASE("off mode, names on: labels aliased with the snapshot")  // NOLINT
{
  auto plp = make_lp(/*names=*/true);
  const auto& sim = plp->simulation();
  for (const auto si : iota_range<SceneIndex>(0, sim.scene_count())) {
    for (const auto pi : iota_range<PhaseIndex>(0, sim.phase_count())) {
      const auto& li = plp->system(si, pi).linear_interface();
      const auto* snap = li.flat_lp_snapshot();
      REQUIRE(snap != nullptr);
      REQUIRE(snap->label_meta != nullptr);
      CHECK(li.frozen_col_labels_meta_size() == snap->col_labels_meta().size());
      CHECK(li.frozen_col_labels_meta_size() == li.flatten_col_count());
      // Aliased: the LI's frozen labels ARE the snapshot's vectors.
      CHECK(li.col_label_at(ColIndex {0}) == snap->col_labels_meta().data());
      CHECK(li.row_label_at(RowIndex {0}) == snap->row_labels_meta().data());
      // ...and share its control block (snapshot + LI, at least).
      CHECK(li.col_labels_meta_use_count() >= 2);
      CHECK(snap->label_meta.use_count() >= 3);
    }
  }
}

TEST_CASE("aliased labels: write_lp equals a deep clone's")  // NOLINT
{
  auto plp = make_lp(/*names=*/true);
  register_alpha_variables(*plp, first_scene_index(), 1.0);
  auto& li =
      plp->system(first_scene_index(), first_phase_index()).linear_interface();
  const auto deep = li.clone(LinearInterface::CloneKind::deep);
  // A deep clone owns private copies; the source still aliases.
  CHECK(deep.col_labels_meta_use_count() == 1);

  const auto dir = std::filesystem::temp_directory_path();
  const auto a = (dir / "gtopt_label_alias_src").string();
  const auto b = (dir / "gtopt_label_alias_deep").string();
  REQUIRE(li.write_lp(a).has_value());
  REQUIRE(deep.write_lp(b).has_value());
  const auto sa = strip_header(slurp(a + ".lp"));
  const auto sb = strip_header(slurp(b + ".lp"));
  std::filesystem::remove(a + ".lp");
  std::filesystem::remove(b + ".lp");
  REQUIRE_FALSE(sa.empty());
  CHECK(sa == sb);
  // Real names, not the generic `c{i}` fallback, plus the post-flatten α.
  CHECK(sa.find("alpha") != std::string::npos);
  CHECK(sa.find(" c0 ") == std::string::npos);

  // Releasing labels on one LI (low-memory release path) never empties
  // the snapshot's copy or a sibling's.
  const auto& snap_cols = li.flat_lp_snapshot()->col_labels_meta();
  const auto n = snap_cols.size();
  CHECK(n > 0);
  CHECK(deep.frozen_col_labels_meta_size() == n);
}

TEST_CASE("names off: write_lp falls back to generic column names")  // NOLINT
{
  auto plp = make_lp(/*names=*/false);
  const auto& li =
      plp->system(first_scene_index(), first_phase_index()).linear_interface();
  const auto stem =
      (std::filesystem::temp_directory_path() / "gtopt_label_generic").string();
  REQUIRE(li.write_lp(stem).has_value());
  const auto s = slurp(stem + ".lp");
  std::filesystem::remove(stem + ".lp");
  CHECK(s.find("c0") != std::string::npos);
}

TEST_CASE(  // NOLINT
    "diagnose_infeasibility: real labels with names on, index-only off")
{
  // Force one structural balance row to an unreachable value in both a
  // names-on and a names-off build, then ask the solver for the conflict.
  // Names on: the conflict carries the real name / class label.  Names
  // off: plain index entries ("row_<N>"), never a wrong or partial label,
  // and no crash.  CPLEX is the only backend with a conflict refiner.
  const auto run = [](bool names) -> std::optional<std::vector<std::string>>
  {
    auto plp = make_lp(names);
    auto& li = plp->system(first_scene_index(), first_phase_index())
                   .linear_interface();
    // Find a balance row through the named build's labels (same layout).
    auto named = make_lp(/*names=*/true);
    const auto& lin = named->system(first_scene_index(), first_phase_index())
                          .linear_interface();
    std::optional<RowIndex> target;
    for (Index r = 0; r < lin.get_numrows() && !target; ++r) {
      const auto* lbl = lin.row_label_at(RowIndex {r});
      if (lbl != nullptr && lbl->constraint_name == "balance") {
        target = RowIndex {r};
      }
    }
    REQUIRE(target.has_value());
    const auto row = target.value_or(RowIndex {0});
    // Pin every column to 0 and demand 1 on the balance row: no slack can
    // absorb it, so the LP is infeasible with that row in the conflict.
    for (Index c = 0; c < li.get_numcols(); ++c) {
      li.set_col_low_raw(ColIndex {c}, 0.0);
      li.set_col_upp_raw(ColIndex {c}, 0.0);
    }
    // Upper first: CPLEX recomputes sense/rhs/range from the other bound.
    li.set_row_upp_raw(row, 1.0);
    li.set_row_low_raw(row, 1.0);
    const auto res = li.initial_solve(SolverOptions {.log_level = 0});
    CAPTURE(names);
    CAPTURE(res.has_value());
    CHECK_FALSE(li.is_optimal());  // the fixture must really be infeasible
    return li.diagnose_infeasibility(20);
  };

  auto probe = make_lp(/*names=*/false);
  const auto solver =
      std::string {probe->system(first_scene_index(), first_phase_index())
                       .linear_interface()
                       .solver_name()};
  if (!solver.contains("cplex")) {
    MESSAGE("diagnose_infeasibility needs CPLEX; solver is ", solver);
    return;
  }

  const auto on = run(/*names=*/true).value_or(std::vector<std::string> {});
  const auto off = run(/*names=*/false).value_or(std::vector<std::string> {});
  REQUIRE_FALSE(on.empty());
  REQUIRE_FALSE(off.empty());
  // Names off: every row entry is a bare index (no bracketed label).
  for (const auto& e : off) {
    CAPTURE(e);
    CHECK_FALSE(e.contains('['));
  }
  // Names on: at least one entry names the balance constraint.
  bool named_hit = false;
  for (const auto& e : on) {
    named_hit = named_hit || e.contains("balance") || e.contains("Bus")
        || e.contains("bus");
  }
  CHECK(named_hit);
}
