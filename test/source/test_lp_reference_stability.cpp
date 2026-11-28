// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_lp_reference_stability.cpp
 * @brief     `row_at` / `col_at` references survive `add_row` / `add_col`
 *
 * Element builders take `auto& row = lp.row_at(i)` and keep stamping
 * through it while other code adds rows (`LineLP` around
 * `line_losses::add_block`, `DemandLP` around its capacity rows).  With
 * the rows in a `std::vector`, the first reallocation left the
 * reference dangling and the later stamps were silently lost — e.g. the
 * reverse `flown` / `lossn` of one line in one block, which made HiGHS
 * and CPLEX return different optima for the same case.  Both tests below
 * fail on the `std::vector` storage.  `LinearProblem`
 * now stores rows and columns in a `StableVector`.
 */

#include <iterator>
#include <optional>
#include <ranges>
#include <stdexcept>
#include <string>
#include <tuple>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/line.hpp>
#include <gtopt/line_losses.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/stable_vector.hpp>

using namespace gtopt;

static_assert(std::ranges::random_access_range<StableVector<int>>);
static_assert(std::ranges::sized_range<StableVector<int>>);
static_assert(std::ranges::random_access_range<const StableVector<int>>);

TEST_CASE("StableVector keeps references across growth")  // NOLINT
{
  StableVector<std::string> v;
  CHECK(v.empty());
  v.emplace_back("first");
  auto& first = v[0];
  const auto* addr = &first;
  for (int i = 1; i < 20'000; ++i) {
    v.emplace_back(std::to_string(i));
  }
  CHECK(&v[0] == addr);
  first = "changed";
  CHECK(v.at(0) == "changed");
  CHECK(v.size() == 20'000);
  CHECK(v.back() == "19999");
  // every chunk boundary maps back to the right element
  for (const std::size_t i : {63UZ, 64UZ, 191UZ, 192UZ, 4031UZ, 19'999UZ}) {
    CAPTURE(i);
    CHECK(v[i] == (i == 0 ? std::string {"changed"} : std::to_string(i)));
  }
  CHECK_THROWS_AS((void)v.at(20'000), std::out_of_range);

  SUBCASE("iteration and ranges")
  {
    std::size_t n = 0;
    for (const auto& s : v) {
      CHECK(s == v[n]);
      ++n;
    }
    CHECK(n == v.size());
    CHECK(std::ranges::distance(v.begin(), v.end()) == 20'000);
    CHECK(*(v.begin() + 100) == "100");
    CHECK((v.end() - 1)->size() == 5);
  }

  SUBCASE("a copy is stable too")
  {
    StableVector<std::string> copy {v};
    auto& c0 = copy[0];
    for (int i = 0; i < 50'000; ++i) {
      copy.emplace_back("x");
    }
    CHECK(&copy[0] == &c0);
    CHECK(copy[0] == "changed");
    CHECK(v.size() == 20'000);
  }

  SUBCASE("reserve and clear")
  {
    StableVector<int> w;
    w.reserve(1000);
    CHECK(w.capacity() >= 1000);
    CHECK(w.empty());
    w.push_back(1);
    w.clear();
    CHECK(w.empty());
    CHECK(w.capacity() == 0);
  }
}

TEST_CASE("LinearProblem row_at/col_at survive add_row/add_col")  // NOLINT
{
  LinearProblem lp;
  const auto c0 = lp.add_col({.lowb = 0, .uppb = 1});
  const auto r0 = lp.add_row(SparseRow {}.equal(0));
  auto& row = lp.row_at(r0);
  auto& col = lp.col_at(c0);
  // Force many reallocations of a vector-backed storage.
  for (int i = 0; i < 10'000; ++i) {
    const auto c = lp.add_col({.lowb = 0, .uppb = 1});
    auto r = SparseRow {}.less_equal(1);
    r[c] = 1.0;
    std::ignore = lp.add_row(std::move(r));
  }
  row[c0] = 2.5;  // stamped through the reference taken before the growth
  col.uppb = 7.0;
  CHECK(lp.get_coeff(r0, c0) == doctest::Approx(2.5));
  CHECK(lp.col_at(c0).uppb == doctest::Approx(7.0));
}

TEST_CASE("line_losses::add_block stamps survive a row reallocation")  // NOLINT
{
  // The balance rows are taken by reference (as `LineLP` does) from a
  // row storage that is exactly full: the first row `add_block` adds
  // reallocates a `std::vector` (libstdc++ grows 1, 2, 4, ... 64), and
  // every later stamp — the reverse `flown` and its loss — went to
  // freed memory.
  const auto json = std::string {R"({
    "options": {"model_options": {"use_single_bus": false}},
    "simulation": {
      "block_array": [{"uid": 1, "duration": 1}],
      "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
      "scenario_array": [{"uid": 1, "probability_factor": 1}]
    },
    "system": {"name": "s", "bus_array": [{"uid": 1, "name": "a"}]}
  })"};
  auto planning = daw::json::from_json<Planning>(json, StrictParsePolicy);
  const PlanningOptionsLP options_lp(planning.options);
  const SimulationLP simulation_lp(planning.simulation, options_lp);
  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto& block = stage.blocks().front();

  for (const auto mode :
       {
           LineLossesMode::piecewise,
           LineLossesMode::bidirectional,
       })
  {
    CAPTURE(static_cast<int>(mode));
    LinearProblem lp;
    const auto ra = lp.add_row(SparseRow {}.equal(0));
    const auto rb = lp.add_row(SparseRow {}.equal(0));
    while (lp.get_numrows() < 64) {  // fill to the storage capacity
      std::ignore = lp.add_row(SparseRow {}.equal(0));
    }
    const Line line {};
    const auto config = line_losses::make_config(mode,
                                                 line,
                                                 LossAllocationMode::receiver,
                                                 /*lossfactor=*/0.0,
                                                 /*resistance=*/0.1,
                                                 /*voltage=*/10.0,
                                                 /*loss_segments=*/8,
                                                 /*fmax=*/20.0);
    auto& brow_a = lp.row_at(ra);
    auto& brow_b = lp.row_at(rb);
    const auto res = line_losses::add_block(config,
                                            scenario,
                                            stage,
                                            block,
                                            lp,
                                            brow_a,
                                            brow_b,
                                            20.0,
                                            20.0,
                                            0.0,
                                            std::nullopt,
                                            Uid {1});
    REQUIRE(res.fp_col.has_value());
    REQUIRE(res.fn_col.has_value());
    CHECK(lp.get_numrows() > 64);
    const auto fp = res.fp_col.value_or(ColIndex {0});
    const auto fn = res.fn_col.value_or(ColIndex {0});
    CHECK(lp.get_coeff(ra, fp) == doctest::Approx(-1.0));
    CHECK(lp.get_coeff(rb, fp) == doctest::Approx(1.0));
    // the reverse direction is stamped after the forward rows were added
    CHECK(lp.get_coeff(ra, fn) == doctest::Approx(1.0));
    CHECK(lp.get_coeff(rb, fn) == doctest::Approx(-1.0));
  }
}
