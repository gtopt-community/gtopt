// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_breaks.cpp
 * @brief     Daily (non-uniform) commitment grid: `commitment_breaks`
 * @date      2026-11-27
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `Commitment.commitment_breaks` / `Stage.commitment_breaks` /
 * `model_options.commitment_breaks` put the status u on daily cells
 * given by their boundaries in hours of the day.  The study pattern:
 * [3.5, 7.5, 10.5, 14.5, 17.5, 23.5] → 23:30–03:30 (4 h), 03:30–07:30
 * (4), 07:30–10:30 sunrise (3), 10:30–14:30 midday (4), 14:30–17:30
 * sunset (3), 17:30–23:30 peak (6, holding the hourly samples 18..23).
 * Nodes join the cell holding their clock time `Stage.start_hour + t`
 * (half-open [a, b)); v / w only at cell boundaries; min up / down
 * count the real cell hours; on a cyclic whole-day stage the cell
 * straddling the period start is one cell.
 */

#include <algorithm>
#include <format>
#include <optional>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/cell_partition.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace commitment_breaks_test
{
namespace
{

/// The study pattern (3 h sunrise / sunset cells, 6 h peak).
[[nodiscard]] std::vector<double> study_breaks()
{
  return {3.5, 7.5, 10.5, 14.5, 17.5, 23.5};
}
constexpr std::string_view kBreaksJson = "[3.5, 7.5, 10.5, 14.5, 17.5, 23.5]";

/// One stage of a test case: node times (point stages) or none (block
/// stages: the block midpoints), block durations, load and the extra
/// stage JSON (`start_hour`, `period_hours`, `commitment_breaks`, ...).
struct StageSpec
{
  std::vector<double> dur;
  std::vector<double> load;
  std::vector<double> times {};  ///< empty = block stage
  std::string extra {};
};

struct Case
{
  std::vector<StageSpec> stages;
  std::string model_extra {};  ///< extra model_options members
  std::string uc_extra {};  ///< extra commitment members
};

/// Hourly stage: 24 blocks / nodes of 1 h, load @p peak at the nodes in
/// [@p p0, @p p1) and @p base elsewhere.  Point stages put node k at
/// t = k (weight 1).
[[nodiscard]] StageSpec hourly(bool point,
                               std::string extra,
                               double base = 20.0,
                               double peak = 80.0,
                               int p0 = 6,
                               int p1 = 12)
{
  StageSpec s {.dur = std::vector<double>(24, 1.0),
               .load = std::vector<double>(24, base),
               .extra = std::move(extra)};
  for (int k = p0; k < p1; ++k) {
    s.load[static_cast<std::size_t>(k)] = peak;
  }
  if (point) {
    for (int k = 0; k < 24; ++k) {
      s.times.push_back(static_cast<double>(k));
    }
  }
  return s;
}

/// g1: cheap (gcost 1, 100 MW, pmin 0) but 300 $/h no-load while
/// committed; g2: 10 $/MWh.  On a peak node (80 MW) committing g1 saves
/// 9·80 = 720 > 300; on a base node (20 MW) 180 < 300.
[[nodiscard]] std::string make_json(const Case& c)
{
  std::string blocks;
  std::string stages;
  std::string lmax;
  std::size_t uid = 0;
  for (std::size_t s = 0; s < c.stages.size(); ++s) {
    const auto& st = c.stages[s];
    const auto first = uid;
    lmax += std::format("{}[", s == 0 ? "" : ", ");
    for (std::size_t k = 0; k < st.dur.size(); ++k) {
      blocks += std::format(
          R"({}{{"uid": {}, "duration": {:.17g}{}}})",
          uid == 0 ? "" : ", ",
          uid + 1,
          st.dur[k],
          st.times.empty() ? std::string {}
                           : std::format(R"(, "time": {:.17g})", st.times[k]));
      lmax += std::format("{}{}", k == 0 ? "" : ", ", st.load[k]);
      ++uid;
    }
    lmax += "]";
    stages += std::format(
        R"({}{{"uid": {}, "first_block": {}, "count_block": {},
            "chronological": true{}{}}})",
        s == 0 ? "" : ", ",
        s + 1,
        first,
        st.dur.size(),
        st.times.empty() ? "" : R"(, "sampling": "point")",
        st.extra);
  }
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0, "output_format": "csv",
               "model_options": {{"use_single_bus": true,
                                  "demand_fail_cost": 10000,
                                  "scale_objective": 1{}}}}},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]}},
  "system": {{
    "name": "uc_breaks",
    "bus_array": [{{"uid": 1, "name": "b1"}}],
    "demand_array": [{{"uid": 1, "name": "d1", "bus": "b1",
                       "lmax": [{}]}}],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": "b1", "gcost": 1, "capacity": 100}},
      {{"uid": 2, "name": "g2", "bus": "b1", "gcost": 10,
        "capacity": 1000}}],
    "commitment_array": [{{"uid": 1, "name": "g1_uc", "generator": 1,
                           "noload_cost": 300{}}}]}}
}})",
      c.model_extra,
      blocks,
      stages,
      lmax,
      c.uc_extra);
}

[[nodiscard]] Planning parse(const Case& c)
{
  Planning base;
  base.merge(parse_planning_json(make_json(c)));
  return base;
}

struct StageResult
{
  std::vector<int> starts;  ///< first block (0-based in the stage) per period
  std::vector<int> v_starts;  ///< same, from the startup columns
  std::vector<double> u;  ///< status per period (solved)
  std::vector<double> v_upp;  ///< startup upper bound per period
  std::vector<double> w_upp;  ///< shutdown upper bound per period
};

struct Result
{
  std::vector<StageResult> stages;
  double obj {};
  bool solved {false};
};

[[nodiscard]] Result run(const Case& c, bool solve = true)
{
  PlanningLP plp(parse(c));
  Result out;
  if (solve) {
    auto res = plp.resolve();
    REQUIRE(res.has_value());
    out.solved = true;
  }
  auto& sys = plp.systems().front().front();
  const auto& li = sys.linear_interface();
  if (solve) {
    out.obj = li.get_obj_value();
  }
  const auto& uc = sys.elements<CommitmentLP>().front();
  const auto& scen = sys.scene().scenarios().front();
  for (const auto& stage : sys.phase().stages()) {
    const auto first = static_cast<int>(value_of(stage.blocks().front().uid()));
    StageResult sr;
    const auto* ucols = uc.find_status_cols(scen, stage);
    const auto* vcols = uc.find_startup_cols(scen, stage);
    const auto* wcols = uc.find_shutdown_cols(scen, stage);
    REQUIRE(ucols != nullptr);
    REQUIRE(vcols != nullptr);
    REQUIRE(wcols != nullptr);
    std::vector<std::pair<int, ColIndex>> us;
    for (const auto& [buid, col] : *ucols) {
      us.emplace_back(static_cast<int>(value_of(buid)) - first, col);
    }
    std::ranges::sort(us, {}, &std::pair<int, ColIndex>::first);
    for (const auto& [buid, col] : *vcols) {
      sr.v_starts.push_back(static_cast<int>(value_of(buid)) - first);
    }
    std::ranges::sort(sr.v_starts);
    const auto upp = li.get_col_upp();
    for (const auto& [k, col] : us) {
      sr.starts.push_back(k);
      if (solve) {
        sr.u.push_back(li.get_col_sol()[value_of(col)]);
      }
      const auto buid = make_uid<Block>(k + first);
      sr.v_upp.push_back(upp[value_of(vcols->at(buid))]);
      sr.w_upp.push_back(upp[value_of(wcols->at(buid))]);
    }
    out.stages.push_back(std::move(sr));
  }
  return out;
}

/// Rounded status per period.
[[nodiscard]] std::vector<int> on(const StageResult& s)
{
  std::vector<int> r;
  r.reserve(s.u.size());
  for (const double u : s.u) {
    r.push_back(u > 0.5 ? 1 : 0);
  }
  return r;
}

}  // namespace
}  // namespace commitment_breaks_test

// ── node → cell mapping (cell_partition.hpp) ─────────────────────────

TEST_CASE("daily_cell_bounds - midnight start, one day")  // NOLINT
{
  using namespace commitment_breaks_test;
  const auto b = daily_cell_bounds(study_breaks(), 0.0, 24.0);
  REQUIRE(b.size() == 6);
  for (std::size_t i = 0; i < b.size(); ++i) {
    CHECK(b[i] == doctest::Approx(study_breaks()[i]));
  }
  // cells [0, 3.5) [3.5, 7.5) ... [17.5, 23.5) [23.5, 24]
  CHECK(cell_of_time(b, 0.0) == 0);
  CHECK(cell_of_time(b, 3.0) == 0);
  CHECK(cell_of_time(b, 18.0) == 5);
  CHECK(cell_of_time(b, 23.0) == 5);
  CHECK(cell_of_time(b, 23.75) == 6);
  CHECK(cell_of_time(b, 24.0) == 6);  // period end → last cell
}

TEST_CASE(
    "daily_cell_bounds - half-open cells, boundary node joins later")  // NOLINT
{
  using namespace commitment_breaks_test;
  const auto b = daily_cell_bounds(study_breaks(), 0.0, 24.0);
  // a node exactly on a boundary starts the LATER cell
  CHECK(cell_of_time(b, 3.5) == 1);
  CHECK(cell_of_time(b, 17.5) == 5);
  CHECK(cell_of_time(b, 23.5) == 6);
  // within 1e-9 h counts as on it; just before stays in the earlier cell
  CHECK(cell_of_time(b, 3.5 - 1e-12) == 1);
  CHECK(cell_of_time(b, 3.5 - 1e-6) == 0);
  CHECK(cell_of_time(b, 17.5 - 1e-6) == 4);
  // negative times clamp to the first cell
  CHECK(cell_of_time(b, -1.0) == 0);
}

TEST_CASE("daily_cell_bounds - wrap around midnight")  // NOLINT
{
  using namespace commitment_breaks_test;
  // two days from midnight: the 23:30–03:30 cell spans [23.5, 27.5)
  const auto b = daily_cell_bounds(study_breaks(), 0.0, 48.0);
  REQUIRE(b.size() == 12);
  CHECK(b[6] == doctest::Approx(27.5));
  CHECK(cell_of_time(b, 23.5) == 6);
  CHECK(cell_of_time(b, 24.0) == 6);  // midnight: same cell
  CHECK(cell_of_time(b, 27.0) == 6);
  CHECK(cell_of_time(b, 27.5) == 7);
  // start 22:00: [0, 1.5) is the end of the 17:30–23:30 peak, the cell
  // 23:30–03:30 is [1.5, 5.5) across midnight
  const auto c = daily_cell_bounds(study_breaks(), 22.0, 24.0);
  REQUIRE(c.size() == 6);
  CHECK(c.front() == doctest::Approx(1.5));
  CHECK(c[1] == doctest::Approx(5.5));
  CHECK(c.back() == doctest::Approx(19.5));
  CHECK(cell_of_time(c, 2.0) == 1);  // 00:00
}

TEST_CASE("daily_cell_bounds - typical week from Wednesday 12:00")  // NOLINT
{
  using namespace commitment_breaks_test;
  const auto b = daily_cell_bounds(study_breaks(), 12.0, 168.0);
  // start 12:00 is not a break: 6 boundaries per day, 7 days
  REQUIRE(b.size() == 42);
  CHECK(b.front() == doctest::Approx(2.5));  // 14:30
  CHECK(b[1] == doctest::Approx(5.5));  // 17:30
  CHECK(b[2] == doctest::Approx(11.5));  // 23:30
  CHECK(b.back() == doctest::Approx(166.5));  // next Wed 10:30
  CHECK(std::ranges::is_sorted(b));
  // the start-hour is taken modulo 24: Wednesday 12:00 counted from
  // Monday 00:00 (60 h) gives the same grid
  CHECK(daily_cell_bounds(study_breaks(), 60.0, 168.0) == b);
  // the peak cell holds the hourly samples 18..23 of every day
  for (int day = 0; day < 7; ++day) {
    const auto peak = cell_of_time(b, (24.0 * day) + 6.0);  // 18:00
    for (int h = 18; h <= 23; ++h) {
      CHECK(cell_of_time(b, (24.0 * day) + h - 12.0) == peak);
    }
    CHECK(cell_of_time(b, (24.0 * day) + 5.0) == peak - 1);  // 17:00
  }
  // the cell straddling the week start: 10:30–14:30 = [166.5, 168] and
  // [0, 2.5) — two pieces of one daily cell
  CHECK(daily_cells_wrap(study_breaks(), 12.0, 168.0));
}

TEST_CASE("daily_cells_wrap - start on a break / partial days")  // NOLINT
{
  using namespace commitment_breaks_test;
  // a stage starting exactly on a break has no straddling cell, and the
  // break at its start is not a boundary
  CHECK_FALSE(daily_cells_wrap(study_breaks(), 17.5, 24.0));
  const auto b = daily_cell_bounds(study_breaks(), 17.5, 24.0);
  REQUIRE(b.size() == 5);
  CHECK(b.front() == doctest::Approx(6.0));
  CHECK(b.back() == doctest::Approx(21.0));
  // 23.5 ≡ −0.5 (mod 24): a start at 23:30 is on a break too
  CHECK_FALSE(daily_cells_wrap(study_breaks(), 23.5, 168.0));
  // not a whole number of days: the period start is no clock repeat
  CHECK_FALSE(daily_cells_wrap(study_breaks(), 12.0, 36.0));
  // a period inside one cell: a single cell, nothing to merge
  CHECK(daily_cell_bounds(study_breaks(), 0.0, 2.0).empty());
  CHECK_FALSE(daily_cells_wrap(study_breaks(), 0.0, 2.0));
  // midnight start: 0 h is not a break → the 23:30–03:30 cell wraps
  CHECK(daily_cells_wrap(study_breaks(), 0.0, 24.0));
  // empty breaks: no grid
  CHECK(daily_cell_bounds({}, 0.0, 24.0).empty());
  CHECK_FALSE(daily_cells_wrap({}, 0.0, 24.0));
}

TEST_CASE("clock_of_day - modulo 24")  // NOLINT
{
  CHECK(clock_of_day(60.0) == doctest::Approx(12.0));
  CHECK(clock_of_day(24.0) == doctest::Approx(0.0));
  CHECK(clock_of_day(-1.0) == doctest::Approx(23.0));
  CHECK(clock_of_day(5.25) == doctest::Approx(5.25));
}

TEST_CASE("Commitment::effective_commitment_breaks precedence")  // NOLINT
{
  using namespace commitment_breaks_test;
  const std::vector<double> stage_b {6.0, 18.0};
  const std::optional<std::vector<double>> dflt {std::vector {12.0}};
  Commitment c;
  // nothing set
  CHECK(c.effective_commitment_breaks({}, std::nullopt).empty());
  // planning default
  CHECK(c.effective_commitment_breaks({}, dflt).size() == 1);
  // the stage pattern beats the default
  CHECK(c.effective_commitment_breaks(stage_b, dflt).size() == 2);
  // the unit's own list beats both
  c.commitment_breaks = study_breaks();
  CHECK(c.effective_commitment_breaks(stage_b, dflt).size() == 6);
  // a unit with its own uniform grid ignores the stage / default
  Commitment p;
  p.commitment_period = 3.0;
  CHECK(p.effective_commitment_breaks(stage_b, dflt).empty());
  // ... and so does a unit on a legacy point grid
  Commitment l;
  l.commitment_grid = "block";
  CHECK(l.effective_commitment_breaks(stage_b, dflt).empty());
  // overlay without a period takes the stage / default grid
  Commitment o;
  o.commitment_grid = "overlay";
  CHECK(o.effective_commitment_breaks(stage_b, dflt).size() == 2);
}

// ── LP structure ─────────────────────────────────────────────────────

TEST_CASE(  // NOLINT
    "commitment_breaks - block stage: cells by block midpoint")
{
  using namespace commitment_breaks_test;
  // 24 hourly blocks from midnight: midpoints k + 0.5; the midpoint 3.5
  // of block 3 lies on the 03:30 boundary → block 3 opens cell 1, etc.
  Case c {.stages = {hourly(/*point=*/false, "")},
          .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  const auto r = run(c, /*solve=*/false);
  const std::vector<int> expected {0, 3, 7, 10, 14, 17, 23};
  CHECK(r.stages.front().starts == expected);
  // v / w exist only at the cell boundaries: one per period
  CHECK(r.stages.front().v_starts == expected);
  // without breaks: one status per block (unchanged behaviour)
  Case plain {.stages = {hourly(/*point=*/false, "")}};
  CHECK(run(plain, /*solve=*/false).stages.front().starts.size() == 24);
}

TEST_CASE(  // NOLINT
    "commitment_breaks - point week-start stage: one u per peak cell")
{
  using namespace commitment_breaks_test;
  // hourly samples t = 0..23 of a periodic day starting at 12:00:
  // c0 [0, 2.5) 12–14 h | c1 [2.5, 5.5) 15–17 | c2 [5.5, 11.5) 18–23
  // (peak) | c3 [11.5, 15.5) 0–3 | c4 [15.5, 19.5) 4–7 | c5 [19.5, 22.5)
  // 8–10 | c6 [22.5, 24] 11 h — c6 and c0 are the 10:30–14:30 cell.
  const auto spec = hourly(
      /*point=*/true,
      R"(, "period_hours": 24, "start_hour": 12)",
      20.0,
      80.0,
      6,
      12);
  Case c {.stages = {spec},
          .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  const auto chrono = run(c, /*solve=*/false);
  const std::vector<int> expected {0, 3, 6, 12, 16, 20, 23};
  CHECK(chrono.stages.front().starts == expected);
  CHECK(chrono.stages.front().v_starts == expected);
  // non-cyclic: every boundary may start / stop the unit
  for (const double v : chrono.stages.front().v_upp) {
    CHECK(v == doctest::Approx(1.0));
  }
  // cyclic: the cell straddling the period start is ONE cell — no start
  // or stop at the wrap (v / w of the first period fixed at 0)
  c.uc_extra += R"(, "cycle": "stage")";
  const auto cyc = run(c, /*solve=*/false);
  CHECK(cyc.stages.front().starts == expected);
  CHECK(cyc.stages.front().v_upp.front() == doctest::Approx(0.0));
  CHECK(cyc.stages.front().w_upp.front() == doctest::Approx(0.0));
  for (std::size_t p = 1; p < expected.size(); ++p) {
    CHECK(cyc.stages.front().v_upp[p] == doctest::Approx(1.0));
  }
}

TEST_CASE(  // NOLINT
    "commitment_breaks - empty cells continue the previous status")
{
  using namespace commitment_breaks_test;
  // point day from midnight, hourly samples 0..23: the last piece
  // [23.5, 24] of the 23:30–03:30 cell holds no node → no period of its
  // own, and no wrap merge (its status is node 23's, the peak cell)
  auto spec = hourly(/*point=*/true, R"(, "period_hours": 24)");
  Case c {.stages = {spec},
          .uc_extra = std::format(R"(, "commitment_breaks": {},
                                     "cycle": "stage")",
                                  kBreaksJson)};
  const auto r = run(c, /*solve=*/false);
  const std::vector<int> expected {0, 4, 8, 11, 15, 18};
  CHECK(r.stages.front().starts == expected);
  CHECK(r.stages.front().v_upp.front() == doctest::Approx(1.0));
  // sparse nodes: t = 1, 9, 20 → three cells hold a node, four are empty
  StageSpec sparse {.dur = {8.0, 8.0, 8.0},
                    .load = {20.0, 20.0, 80.0},
                    .times = {1.0, 9.0, 20.0},
                    .extra = R"(, "period_hours": 24)"};
  Case s {.stages = {sparse},
          .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  const auto rs = run(s, /*solve=*/false);
  const std::vector<int> three {0, 1, 2};
  CHECK(rs.stages.front().starts == three);
}

TEST_CASE(  // NOLINT
    "commitment_breaks - Lobatto closing node joins the last cell")
{
  using namespace commitment_breaks_test;
  // non-periodic day with a closing node at t = 24 (the period end)
  StageSpec st {.dur = {},
                .load = {},
                .times = {},
                .extra = R"(, "period_hours": 24, "periodic": false)"};
  for (int k = 0; k <= 24; ++k) {
    st.times.push_back(static_cast<double>(k));
    st.dur.push_back((k == 0 || k == 24) ? 0.5 : 1.0);
    st.load.push_back(20.0);
  }
  Case c {.stages = {st},
          .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  const auto r = run(c, /*solve=*/false);
  // nodes 0..3 | 4..7 | 8..10 | 11..14 | 15..17 | 18..23 | 24 (on the
  // cell [23.5, 24] together with nothing else)
  const std::vector<int> expected {0, 4, 8, 11, 15, 18, 24};
  CHECK(r.stages.front().starts == expected);
}

TEST_CASE(  // NOLINT
    "commitment_breaks - planning default and precedence in the LP")
{
  using namespace commitment_breaks_test;
  const std::vector<int> daily {0, 3, 7, 10, 14, 17, 23};
  // model_options default applies to a unit without its own grid
  Case d {
      .stages = {hourly(/*point=*/false, "")},
      .model_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  CHECK(run(d, /*solve=*/false).stages.front().starts == daily);
  // a unit's own commitment_period wins over the default
  Case p = d;
  p.uc_extra = R"(, "commitment_period": 2)";
  CHECK(run(p, /*solve=*/false).stages.front().starts.size() == 12);
  // the unit's own breaks win over the stage pattern
  Case u {.stages = {hourly(/*point=*/false, R"(, "commitment_breaks": [12])")},
          .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  CHECK(run(u, /*solve=*/false).stages.front().starts == daily);
  // the stage pattern wins over the default: one boundary at 12:00
  Case s = d;
  s.stages = {hourly(/*point=*/false, R"(, "commitment_breaks": [12])")};
  const std::vector<int> halves {0, 12};
  CHECK(run(s, /*solve=*/false).stages.front().starts == halves);
}

TEST_CASE(  // NOLINT
    "commitment_breaks - per-stage patterns (seasonal sunrise / sunset)")
{
  using namespace commitment_breaks_test;
  // stage 1 (summer): the study pattern; stage 2 (winter): sunrise and
  // sunset one hour later / earlier → different cells and v / w
  // boundaries per stage
  Case c {.stages = {
              hourly(/*point=*/false,
                     std::format(R"(, "commitment_breaks": {})", kBreaksJson)),
              hourly(/*point=*/false,
                     R"(, "commitment_breaks":
                                 [3.5, 8.5, 11.5, 14.5, 16.5, 23.5])")}};
  const auto r = run(c, /*solve=*/true);
  REQUIRE(r.stages.size() == 2);
  const std::vector<int> summer {0, 3, 7, 10, 14, 17, 23};
  const std::vector<int> winter {0, 3, 8, 11, 14, 16, 23};
  CHECK(r.stages[0].starts == summer);
  CHECK(r.stages[0].v_starts == summer);
  CHECK(r.stages[1].starts == winter);
  CHECK(r.stages[1].v_starts == winter);
  // Inter-stage continuity as on the existing grids: each stage's first
  // period starts from the unit's initial state (off here).  Peak load
  // at blocks 6..11; a cell is committed when its savings (720 per peak
  // block, 180 per base block) beat the 300/h no-load:
  //   summer: cells 3..6 (1 peak + 3 base: 1260 > 1200), 7..9 and
  //   10..13 (2 + 2: 1800 > 1200) on; winter: cells 3..7 (2 + 3:
  //   1980 > 1500), 8..10 and 11..13 (1 + 2: 1080 > 900) on
  const std::vector<int> summer_on {0, 1, 1, 1, 0, 0, 0};
  CHECK(on(r.stages[0]) == summer_on);
  const std::vector<int> winter_on {0, 1, 1, 1, 0, 0, 0};
  CHECK(on(r.stages[1]) == winter_on);
}

TEST_CASE(  // NOLINT
    "commitment_breaks - MIP: on in the peak cell only")
{
  using namespace commitment_breaks_test;
  // periodic point day from 12:00, peak load 80 at the samples 18..23
  // (nodes 6..11 = cell c2), 20 elsewhere:
  //   g1 on c2: 6·80·1 + 6·300 = 2280; g2 elsewhere: 18·20·10 = 3600
  const auto spec = hourly(
      /*point=*/true,
      R"(, "period_hours": 24, "start_hour": 12)",
      20.0,
      80.0,
      6,
      12);
  Case c {.stages = {spec},
          .uc_extra = std::format(R"(, "commitment_breaks": {},
                                     "cycle": "stage")",
                                  kBreaksJson)};
  const auto r = run(c);
  CHECK(r.obj == doctest::Approx(5880.0));
  const std::vector<int> peak_only {0, 0, 1, 0, 0, 0, 0};
  CHECK(on(r.stages.front()) == peak_only);
}

TEST_CASE(  // NOLINT
    "commitment_breaks - min up time counts the real cell hours")
{
  using namespace commitment_breaks_test;
  const auto spec = hourly(
      /*point=*/true,
      R"(, "period_hours": 24, "start_hour": 12)",
      20.0,
      80.0,
      6,
      12);
  // MUT 10 h: the peak cell c2 (6 h) + c3 (4 h) = 10 h; the cheapest
  // extension is c3 (4 base nodes · (300 + 20 − 200) = 480)
  Case c {.stages = {spec},
          .uc_extra = std::format(R"(, "commitment_breaks": {},
                                     "cycle": "stage", "min_up_time": 10)",
                                  kBreaksJson)};
  const auto r = run(c);
  CHECK(r.obj == doctest::Approx(5880.0 + 480.0));
  const std::vector<int> mut {0, 0, 1, 1, 0, 0, 0};
  CHECK(on(r.stages.front()) == mut);
  // MUT 6 h = the peak cell alone: no extension
  Case six = c;
  six.uc_extra = std::format(R"(, "commitment_breaks": {},
                                "cycle": "stage", "min_up_time": 6)",
                             kBreaksJson);
  CHECK(run(six).obj == doctest::Approx(5880.0));
  // relaxed: the LP bound never exceeds the MIP
  Case rel = c;
  rel.uc_extra += R"(, "relax": true)";
  const auto rr = run(rel);
  CHECK(rr.obj <= r.obj + 1e-6);
  CHECK(rr.stages.front().starts == r.stages.front().starts);
}

TEST_CASE(  // NOLINT
    "commitment_breaks - min down time counts the real cell hours")
{
  using namespace commitment_breaks_test;
  const auto spec = hourly(
      /*point=*/true,
      R"(, "period_hours": 24, "start_hour": 12)",
      20.0,
      80.0,
      6,
      12);
  // MDT 20 h > the 18 h off-peak: the unit cannot cycle within the day:
  // always off costs 6·80·10 = 4800 more than g1 at the peak minus its
  // no-load; always on: 24·300 + 6·80 + 18·20 = 8040 vs always off
  // 4800 + 3600 = 8400 → always on
  Case c {.stages = {spec},
          .uc_extra = std::format(R"(, "commitment_breaks": {},
                                     "cycle": "stage", "min_down_time": 20)",
                                  kBreaksJson)};
  const auto r = run(c);
  CHECK(r.obj == doctest::Approx(8040.0));
  const std::vector<int> all_on(7, 1);
  CHECK(on(r.stages.front()) == all_on);
  // MDT 18 h = the off-peak cells exactly: peak-only stays feasible
  Case ok = c;
  ok.uc_extra = std::format(R"(, "commitment_breaks": {},
                               "cycle": "stage", "min_down_time": 18)",
                            kBreaksJson);
  CHECK(run(ok).obj == doctest::Approx(5880.0));
}

TEST_CASE(  // NOLINT
    "commitment_breaks - ignored on non-chronological stages")
{
  using namespace commitment_breaks_test;
  // the builder writes chronological stages: flip it after parsing
  Case c {.stages = {hourly(/*point=*/false, "")},
          .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  auto pl = parse(c);
  pl.simulation.stage_array.front().chronological = false;
  PlanningLP plp(std::move(pl));
  auto& sys = plp.systems().front().front();
  const auto& uc = sys.elements<CommitmentLP>().front();
  const auto* cols = uc.find_status_cols(sys.scene().scenarios().front(),
                                         sys.phase().stages().front());
  REQUIRE(cols != nullptr);
  CHECK(cols->size() == 24);  // the block formulation: one u per block
}

// ── validation ───────────────────────────────────────────────────────

TEST_CASE("commitment_breaks - validation")  // NOLINT
{
  using namespace commitment_breaks_test;
  const auto errors_of = [](const Case& c)
  {
    auto pl = parse(c);
    return validate_planning(pl).errors;
  };
  const auto has = [](const auto& errs, std::string_view what)
  {
    return std::ranges::any_of(
        errs, [&](const auto& e) { return e.find(what) != std::string::npos; });
  };
  Case good {
      .stages = {hourly(/*point=*/false, "")},
      .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  CHECK_FALSE(has(errors_of(good), "commitment_breaks"));

  Case dec = good;
  dec.uc_extra = R"(, "commitment_breaks": [6, 3])";
  CHECK(has(errors_of(dec), "strictly increasing"));

  Case dup = good;
  dup.uc_extra = R"(, "commitment_breaks": [6, 6])";
  CHECK(has(errors_of(dup), "strictly increasing"));

  Case range = good;
  range.uc_extra = R"(, "commitment_breaks": [6, 24])";
  CHECK(has(errors_of(range), "[0, 24)"));
  range.uc_extra = R"(, "commitment_breaks": [-1, 6])";
  CHECK(has(errors_of(range), "[0, 24)"));

  Case both = good;
  both.uc_extra += R"(, "commitment_period": 2)";
  CHECK(has(errors_of(both), "mutually exclusive"));

  Case legacy = good;
  legacy.uc_extra += R"(, "commitment_grid": "cells")";
  CHECK(has(errors_of(legacy), "conflicts with commitment_breaks"));

  // overlay without a period is fine with a daily grid
  Case overlay = good;
  overlay.uc_extra += R"(, "commitment_grid": "overlay")";
  CHECK_FALSE(has(errors_of(overlay), "needs commitment_period"));

  Case model {.stages = {hourly(/*point=*/false, "")},
              .model_extra = R"(, "commitment_breaks": [])"};
  CHECK(has(errors_of(model), "at least one"));

  Case stage {
      .stages = {hourly(/*point=*/false, R"(, "commitment_breaks": [30])")}};
  CHECK(has(errors_of(stage), "Stage uid=1"));

  Case start {.stages = {hourly(/*point=*/false, R"(, "start_hour": -2)")}};
  CHECK(has(errors_of(start), "start_hour"));
}

TEST_CASE("commitment_breaks - JSON round trip")  // NOLINT
{
  using namespace commitment_breaks_test;
  Case c {.stages = {hourly(/*point=*/false,
                            R"(, "start_hour": 12,
                                 "commitment_breaks": [6, 18])")},
          .model_extra = R"(, "commitment_breaks": [12])",
          .uc_extra = std::format(R"(, "commitment_breaks": {})", kBreaksJson)};
  const auto pl = parse(c);
  CHECK(pl.system.commitment_array.front().commitment_breaks == study_breaks());
  const auto& st = pl.simulation.stage_array.front();
  CHECK(st.start_hour.value_or(0.0) == doctest::Approx(12.0));
  CHECK(st.commitment_breaks == std::vector<double> {6.0, 18.0});
  const auto& mb = pl.options.model_options.commitment_breaks;
  REQUIRE(mb.has_value());
  CHECK(mb.value_or(std::vector<double> {}) == std::vector<double> {12.0});
  // write and re-read
  const auto again = parse_planning_json(daw::json::to_json(pl));
  CHECK(again.system.commitment_array.front().commitment_breaks
        == study_breaks());
  CHECK(again.simulation.stage_array.front().commitment_breaks
        == std::vector<double> {6.0, 18.0});
  CHECK(again.options.model_options.commitment_breaks.value_or(
            std::vector<double> {})
        == std::vector<double> {12.0});
}
