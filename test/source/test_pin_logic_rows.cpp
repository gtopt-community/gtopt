// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_pin_logic_rows.cpp
 * @brief     `SparseRow::pin_scale` / `LpMatrixOptions::pin_logic_rows`:
 *            pure commitment-logic rows keep row factor 1 under row_max,
 *            Ruiz and the incremental `add_row` path.
 * @date      2026-11-26
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstdint>
#include <limits>
#include <string_view>
#include <tuple>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_equilibration.hpp>
#include <gtopt/lp_matrix_enums.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>

using namespace gtopt;

namespace
{
constexpr double kInfinity = std::numeric_limits<double>::infinity();

// Two rows over three columns:
//   row 0 (structural): 1000·x0 + 1·x1      (x0 a MW column)
//   row 1 (logic):        9·x1 − 1·x2       (x1, x2 binary-like)
struct SmallMatrix
{
  std::vector<std::int32_t> matbeg {0, 1, 3, 4};
  std::vector<std::int32_t> matind {0, 0, 1, 1};
  std::vector<double> matval {1000.0, 1.0, 9.0, -1.0};
  std::vector<double> rowlb {-kInfinity, -kInfinity};
  std::vector<double> rowub {10.0, 4.0};
  std::vector<double> collb {0.0, 0.0, 0.0};
  std::vector<double> colub {kInfinity, 1.0, 1.0};
  std::vector<double> objval {1.0, 1.0, 1.0};
  std::vector<double> col_scales {1.0, 1.0, 1.0};
};

}  // namespace

TEST_CASE("apply_row_max_equilibration keeps rowpin rows at factor 1")
{
  SmallMatrix m;
  const std::vector<std::int32_t> rowpin {1};
  const auto rs = apply_row_max_equilibration(
      m.matind, m.matval, m.rowlb, m.rowub, kInfinity, rowpin);
  REQUIRE(rs.size() == 2);
  CHECK(rs[0] == doctest::Approx(1000.0));  // unpinned row balanced
  CHECK(rs[1] == 1.0);  // pinned row untouched
  CHECK(m.matval[2] == 9.0);
  CHECK(m.matval[3] == -1.0);
  CHECK(m.rowub[1] == 4.0);

  SUBCASE("without rowpin the same row is normalised (old behaviour)")
  {
    SmallMatrix m2;
    const auto rs2 = apply_row_max_equilibration(
        m2.matind, m2.matval, m2.rowlb, m2.rowub, kInfinity);
    CHECK(rs2[1] == doctest::Approx(9.0));
    CHECK(m2.matval[2] == doctest::Approx(1.0));
  }
}

TEST_CASE("apply_ruiz_scaling keeps rowpin rows at factor 1")
{
  SmallMatrix m;
  const std::vector<std::int32_t> colpin {1, 2};
  const std::vector<std::int32_t> rowpin {1};
  const auto rs = apply_ruiz_scaling(m.matbeg,
                                     m.matind,
                                     m.matval,
                                     m.rowlb,
                                     m.rowub,
                                     m.collb,
                                     m.colub,
                                     m.objval,
                                     m.col_scales,
                                     colpin,
                                     kInfinity,
                                     FastSqrtMethod::std_sqrt,
                                     10,
                                     1e-6,
                                     rowpin);
  REQUIRE(rs.size() == 2);
  CHECK(rs[1] == 1.0);
  CHECK(m.matval[2] == 9.0);  // pinned row, pinned cols: unchanged
  CHECK(m.matval[3] == -1.0);
  CHECK(m.rowub[1] == 4.0);
  CHECK(rs[0] != 1.0);  // the structural row is still balanced

  SUBCASE("column scales still apply inside a pinned row")
  {
    // Unpin x1: Ruiz may now scale it; the pinned row's x1 entry must
    // carry exactly that column factor (row factor 1), consistent with
    // the same column's entry in the structural row.
    SmallMatrix m2;
    const std::vector<std::int32_t> colpin2 {2};
    const auto rs2 = apply_ruiz_scaling(m2.matbeg,
                                        m2.matind,
                                        m2.matval,
                                        m2.rowlb,
                                        m2.rowub,
                                        m2.collb,
                                        m2.colub,
                                        m2.objval,
                                        m2.col_scales,
                                        colpin2,
                                        kInfinity,
                                        FastSqrtMethod::std_sqrt,
                                        10,
                                        1e-6,
                                        rowpin);
    CHECK(rs2[1] == 1.0);
    // matval = a · col_scale / row_scale for every entry.
    CHECK(m2.matval[2] == doctest::Approx(9.0 * m2.col_scales[1]));
    CHECK(m2.matval[1] == doctest::Approx(1.0 * m2.col_scales[1] / rs2[0]));
    CHECK(m2.matval[3] == -1.0);  // x2 pinned
  }
}

TEST_CASE("LinearProblem::flatten honours SparseRow::pin_scale")  // NOLINT
{
  const auto build = [](bool pin_option, LpEquilibrationMethod method)
  {
    LinearProblem lp("pin_rows");
    const auto p = lp.add_col(SparseCol {
        .uppb = 500.0,
    });
    const auto u = lp.add_col(SparseCol {
        .uppb = 1.0,
        .pin_scale = true,
    });
    const auto v = lp.add_col(SparseCol {
        .uppb = 1.0,
        .pin_scale = true,
    });
    auto gen = SparseRow {}.less_equal(0.0);
    gen[p] = 1.0;
    gen[u] = -300.0;
    std::ignore = lp.add_row(std::move(gen));
    auto mut =
        SparseRow {
            .pin_scale = true,
        }
            .greater_equal(0.0);
    mut[u] = 1.0;
    mut[v] = -9.0;
    std::ignore = lp.add_row(std::move(mut));
    return lp.flatten({
        .equilibration_method = method,
        .pin_logic_rows = pin_option,
    });
  };

  // Opt-in: unset means off.
  static_assert(!LpMatrixOptions::default_pin_logic_rows);
  CHECK_FALSE(LinearInterface {}.pin_logic_rows());

  for (const auto method :
       {LpEquilibrationMethod::row_max, LpEquilibrationMethod::ruiz})
  {
    CAPTURE(method);
    const auto on = build(true, method);
    REQUIRE(on.row_scales.size() == 2);
    CHECK(on.row_scales[1] == 1.0);
    CHECK(on.pin_logic_rows);
    const auto off = build(false, method);
    REQUIRE(off.row_scales.size() == 2);
    CHECK(off.row_scales[1] != 1.0);  // old behaviour: balanced
    CHECK_FALSE(off.pin_logic_rows);
    // The structural row is equilibrated identically either way under
    // row_max (rows are independent there).
    if (method == LpEquilibrationMethod::row_max) {
      CHECK(on.row_scales[0] == doctest::Approx(off.row_scales[0]));
    }
  }
}

TEST_CASE("LinearInterface::add_row honours SparseRow::pin_scale")  // NOLINT
{
  const auto add = [](bool pin_option, bool pin_row)
  {
    LinearInterface li;
    const auto c0 = li.add_col(SparseCol {
        .uppb = 1.0,
    });
    const auto c1 = li.add_col(SparseCol {
        .uppb = 1.0,
    });
    li.save_base_numrows();
    li.set_col_scale(c1, 2.0);
    li.set_equilibration_method(LpEquilibrationMethod::ruiz);
    li.set_pin_logic_rows(pin_option);
    SparseRow row {
        .pin_scale = pin_row,
    };
    row[c0] = 4.0;
    row[c1] = -1.0;  // LP = -2 after the column scale
    row.lowb = -kInfinity;
    row.uppb = 3.0;
    const auto idx = li.add_row(row);
    return std::tuple {li.get_row_scale(idx),
                       li.get_coeff_raw(idx, c0),
                       li.get_coeff_raw(idx, c1),
                       li.get_row_upp_raw()[static_cast<std::size_t>(idx)]};
  };

  SUBCASE("pinned row: column scale folded, no row normalisation")
  {
    const auto [rs, a0, a1, ub] = add(true, true);
    CHECK(rs == doctest::Approx(1.0));
    CHECK(a0 == doctest::Approx(4.0));
    CHECK(a1 == doctest::Approx(-2.0));
    CHECK(ub == doctest::Approx(3.0));
  }
  SUBCASE("option off: the row is normalised as before")
  {
    const auto [rs, a0, a1, ub] = add(false, true);
    CHECK(rs == doctest::Approx(4.0));
    CHECK(a0 == doctest::Approx(1.0));
    CHECK(a1 == doctest::Approx(-0.5));
    CHECK(ub == doctest::Approx(0.75));
  }
  SUBCASE("untagged row: normalised")
  {
    const auto [rs, a0, a1, ub] = add(true, false);
    CHECK(rs == doctest::Approx(4.0));
    CHECK(a0 == doctest::Approx(1.0));
  }

  SUBCASE("bulk add_rows path")
  {
    LinearInterface li;
    const auto c0 = li.add_col(SparseCol {
        .uppb = 1.0,
    });
    li.save_base_numrows();
    li.set_equilibration_method(LpEquilibrationMethod::row_max);
    li.set_pin_logic_rows(/*pin=*/true);  // opt-in (default off)
    SparseRow pinned {
        .pin_scale = true,
    };
    pinned[c0] = 5.0;
    pinned.lowb = -kInfinity;
    pinned.uppb = 5.0;
    SparseRow plain {};
    plain[c0] = 5.0;
    plain.lowb = -kInfinity;
    plain.uppb = 5.0;
    const std::vector<SparseRow> rows {pinned, plain};
    const auto first = li.get_numrows();
    li.add_rows(rows);
    CHECK(li.get_row_scale(RowIndex {first}) == doctest::Approx(1.0));
    CHECK(li.get_row_scale(RowIndex {first + 1}) == doctest::Approx(5.0));
  }
}

namespace
{
// Small UC system: two committed units with min up / down, start-up
// tiers and max starts, on a 2-bus
// Kirchhoff network, LP-relaxed so the duals are defined.
Planning make_uc_planning(bool pin_rows)
{
  auto planning = daw::json::from_json<Planning>(R"json(
{
  "options": {
    "annual_discount_rate": 0.0,
    "model_options": {
      "use_single_bus": false, "use_kirchhoff": true,
      "demand_fail_cost": 1000,
      "continuous_phases": "all"
    },
    "lp_matrix_options": {"equilibration_method": "ruiz"}
  },
  "simulation": {
    "block_array": [ {"uid": 1, "duration": 1}, {"uid": 2, "duration": 1},
                     {"uid": 3, "duration": 1}, {"uid": 4, "duration": 1},
                     {"uid": 5, "duration": 1}, {"uid": 6, "duration": 1} ],
    "stage_array": [ {"uid": 1, "first_block": 0, "count_block": 6,
                      "chronological": true} ],
    "scenario_array": [ {"uid": 1, "probability_factor": 1} ]
  },
  "system": {
    "name": "pin_rows_uc",
    "bus_array": [ {"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"} ],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": "b1", "pmax": 200, "gcost": 10,
       "capacity": 200},
      {"uid": 2, "name": "g2", "bus": "b2", "pmax": 150, "gcost": 40,
       "capacity": 150} ],
    "demand_array": [ {"uid": 1, "name": "d2", "bus": "b2",
                       "lmax": [[60, 180, 220, 90, 40, 150]]} ],
    "line_array": [ {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
                     "reactance": 0.05, "tmax_ab": 120, "tmax_ba": 120} ],
    "commitment_array": [
      {"uid": 1, "name": "c1", "generator": "g1", "startup_cost": 300,
       "noload_cost": 20, "min_up_time": 3, "min_down_time": 2,
       "initial_status": 0, "pmin": 50, "max_starts": 2},
      {"uid": 2, "name": "c2", "generator": "g2", "noload_cost": 5,
       "initial_status": 1, "pmin": 20, "hot_start_cost": 30,
       "warm_start_cost": 60, "cold_start_cost": 90, "hot_start_time": 1,
       "cold_start_time": 3} ]
  }
}
)json");
  planning.options.lp_matrix_options.pin_logic_rows = pin_rows;
  return planning;
}
}  // namespace

TEST_CASE(  // NOLINT
    "pin_logic_rows: commitment logic rows keep scale 1, physical duals "
    "unchanged")
{
  struct Result
  {
    double obj {0.0};
    std::vector<double> duals;
    std::vector<double> scales;
  };
  const auto solve = [](bool pin_rows)
  {
    PlanningLP planning_lp(make_uc_planning(pin_rows));
    const auto rc = planning_lp.resolve();
    REQUIRE(rc.has_value());
    auto&& li = planning_lp.systems().front().front().linear_interface();
    REQUIRE(li.pin_logic_rows() == pin_rows);
    Result r;
    r.obj = li.get_obj_value();
    const auto nrows = static_cast<std::size_t>(li.get_numrows());
    const auto duals = li.get_row_dual();
    for (std::size_t i = 0; i < nrows; ++i) {
      r.duals.push_back(duals[i]);
      r.scales.push_back(li.get_row_scale(RowIndex {static_cast<Index>(i)}));
    }
    return r;
  };

  const auto on = solve(true);
  const auto off = solve(false);
  CHECK(on.obj == doctest::Approx(off.obj).epsilon(1e-9));
  REQUIRE(on.duals.size() == off.duals.size());
  int rescaled_logic_rows = 0;
  for (std::size_t i = 0; i < on.duals.size(); ++i) {
    CAPTURE(i);
    // Physical duals do not depend on the row scaling.
    CHECK(on.duals[i] == doctest::Approx(off.duals[i]).epsilon(1e-6));
    if (on.scales[i] != off.scales[i]) {
      // Only pinned rows change: they sit at exactly 1 when pinned.
      CHECK(on.scales[i] == 1.0);
      ++rescaled_logic_rows;
    }
  }
  // Every pinned row is ±1 on pinned (scale 1) u / v / w / tier
  // columns, so Ruiz already leaves it at 1: the option changes no scale
  // here.  (Min up / down rows, which carry the window length, are not
  // pinned.)  The flatten / add_row subcases above show the flag acting.
  CHECK(rescaled_logic_rows == 0);
}
