// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_dlr_e2e.cpp — dynamic line rating on the shipped cases.
//
// Small e2e (ieee_14b, 24 h, Kirchhoff, congested l1_2 / l1_5):
//   * static baseline vs DLR on l1_2 (one weather-driven span) and l1_5
//     (three precomputed spans): favourable weather never costs more
//     than the static rating, adverse weather never less;
//   * min and rows span modes give the same objective;
//   * the DLR outputs are written;
//   * a 2-phase SDDP variant runs and matches its monolithic optimum.
//
// Larger integration (bat_4b_24, 24 h, battery, all five lines with three
// weather-driven spans and an hourly forecast): min vs rows objective and
// LP build + solve time.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <numbers>
#include <sstream>
#include <string>
#include <variant>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/line_dlr.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>

using namespace gtopt;

namespace test_line_dlr_e2e_ns
{

namespace
{

[[nodiscard]] std::filesystem::path case_file(std::string_view name)
{
  return std::filesystem::path {GTOPT_TEST_SHARE_DIR} / ".." / ".." / "cases"
      / name / (std::string(name) + ".json");
}

[[nodiscard]] Planning load_case(std::string_view name)
{
  const auto path = case_file(name);
  std::ifstream in(path);
  REQUIRE_MESSAGE(in.good(), "cannot open " << path.string());
  std::stringstream ss;
  ss << in.rdbuf();
  return parse_planning_json(ss.str());
}

[[nodiscard]] Line& line_named(Planning& p, std::string_view name)
{
  for (auto& l : p.system.line_array) {
    if (l.name == name) {
      return l;
    }
  }
  FAIL("line not found: " << name);
  return p.system.line_array.front();
}

[[nodiscard]] DlrConductor drake()
{
  return {
      .diameter = 0.02814,
      .r25 = 7.283e-5,
      .r75 = 8.688e-5,
      .emissivity = 0.8,
      .absorptivity = 0.8,
      .subconductors = 1,
  };
}

[[nodiscard]] double solve_obj(Planning p)
{
  PlanningLP planning_lp(std::move(p));
  const auto r = planning_lp.resolve();
  REQUIRE(r.has_value());
  REQUIRE(r.value() == 1);
  return planning_lp.systems()
      .front()
      .front()
      .linear_interface()
      .get_obj_value();
}

enum class Weather : std::uint8_t
{
  favourable,
  adverse,
};

/// ieee_14b with DLR on l1_2 (one weather-driven span) and l1_5 (three
/// precomputed spans).  `tmax` is lifted to twice the static rating and
/// acts only as an equipment cap: the DLR replaces the static rating.
[[nodiscard]] Planning ieee14_dlr(Weather w, std::string mode)
{
  auto p = load_case("ieee_14b");
  {
    auto& l = line_named(p, "l1_2");  // static 150 MW
    l.tmax_ab = 300.0;
    l.tmax_ba = 300.0;
    LineDlr dlr {
        .conductor = drake(),
        .tmax_conductor = 75.0,
        .voltage = 110.0,
        .span_mode = mode,
    };
    // Favourable: cold, windy night (1709 A → 325.6 MW, capped at 300).
    // Adverse: hot, sunny, wind along the span (478 A → 91.1 MW).
    const bool fav = w == Weather::favourable;
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1201,
            .name = "l1_2_rep",
            .azimuth = 0.0,
            .ambient_temperature = fav ? 10.0 : 40.0,
            .wind_speed = fav ? 3.0 : 0.61,
            .wind_direction = fav ? 90.0 : 0.0,
            .solar_irradiance = fav ? 0.0 : 997.7005431224591,
        },
    };
    l.dlr = dlr;
  }
  {
    auto& l = line_named(p, "l1_5");  // static 75 MW
    l.tmax_ab = 150.0;
    l.tmax_ba = 150.0;
    const double f = w == Weather::favourable ? 1.4 : 0.6;
    LineDlr dlr {
        .span_mode = mode,
    };
    dlr.critical_spans = {
        CriticalSpan {
            .uid = 1501,
            .rating = 75.0 * f,
        },
        CriticalSpan {
            .uid = 1502,
            .rating = 75.0 * f * 1.1,
        },
        CriticalSpan {
            .uid = 1503,
            .rating = 75.0 * f * 1.2,
        },
    };
    l.dlr = dlr;
  }
  return p;
}

/// Split a 1-stage, 24-block planning into 2 stages / 2 phases of 12
/// blocks, reshaping the [1][24] demand and [1][1][24] profile arrays.
void split_two_phases(Planning& p)
{
  p.simulation.stage_array = {
      {
          .uid = Uid {1},
          .first_block = 0,
          .count_block = 12,
      },
      {
          .uid = Uid {2},
          .first_block = 12,
          .count_block = 12,
      },
  };
  p.simulation.phase_array = {
      {
          .uid = Uid {1},
          .first_stage = 0,
          .count_stage = 1,
      },
      {
          .uid = Uid {2},
          .first_stage = 1,
          .count_stage = 1,
      },
  };
  using V2 = std::vector<std::vector<Real>>;
  using V3 = std::vector<std::vector<std::vector<Real>>>;
  for (auto& d : p.system.demand_array) {
    if (d.lmax) {
      if (auto* v = std::get_if<V2>(&d.lmax.value());
          v != nullptr && v->size() == 1)
      {
        const auto row = v->front();
        *v = V2 {
            {row.begin(), row.begin() + 12},
            {row.begin() + 12, row.end()},
        };
      }
    }
  }
  for (auto& gp : p.system.generator_profile_array) {
    if (auto* v = std::get_if<V3>(&gp.profile);
        v != nullptr && v->size() == 1 && v->front().size() == 1)
    {
      const auto row = v->front().front();
      if (row.size() == 24) {
        v->front() = V2 {
            {row.begin(), row.begin() + 12},
            {row.begin() + 12, row.end()},
        };
      }
    }
  }
}

}  // namespace

TEST_CASE("DLR e2e ieee_14b: favourable / adverse vs static")  // NOLINT
{
  const double obj_static = solve_obj(load_case("ieee_14b"));
  const double obj_fav = solve_obj(ieee14_dlr(Weather::favourable, "min"));
  const double obj_adv = solve_obj(ieee14_dlr(Weather::adverse, "min"));
  MESSAGE("ieee_14b obj static=" << obj_static << " favourable=" << obj_fav
                                 << " adverse=" << obj_adv);
  CHECK(obj_fav <= obj_static * (1.0 + 1e-9));
  CHECK(obj_adv >= obj_static * (1.0 - 1e-9));
  // The case is congested on both lines: the DLR moves the cost.
  CHECK(obj_fav < obj_static);
  CHECK(obj_adv > obj_static);

  SUBCASE("rows mode reaches the same optimum")
  {
    CHECK(solve_obj(ieee14_dlr(Weather::favourable, "rows"))
          == doctest::Approx(obj_fav).epsilon(1e-8));
    CHECK(solve_obj(ieee14_dlr(Weather::adverse, "rows"))
          == doctest::Approx(obj_adv).epsilon(1e-8));
  }
}

TEST_CASE("DLR e2e ieee_14b: outputs are written")  // NOLINT
{
  const auto out_dir =
      std::filesystem::temp_directory_path() / "gtopt_test_line_dlr_e2e";
  std::filesystem::remove_all(out_dir);
  auto p = ieee14_dlr(Weather::adverse, "rows");
  p.options.output_directory = out_dir.string();
  p.options.output_format = DataFormat::csv;
  PlanningLP planning_lp(std::move(p));
  const auto r = planning_lp.resolve();
  REQUIRE(r.has_value());
  planning_lp.write_out();

  const auto has = [&](std::string_view stem)
  {
    return std::ranges::any_of(
        std::filesystem::recursive_directory_iterator(out_dir / "Line"),
        [&](const auto& e)
        { return e.path().filename().string().starts_with(stem); });
  };
  CHECK(has("dlr_rating_sol"));
  CHECK(has("critical_span_sol"));
  CHECK(has("span_thermal_dual"));
  CHECK_FALSE(has("dlr_rating_emergency_sol"));
  std::filesystem::remove_all(out_dir);
}

TEST_CASE("DLR e2e ieee_14b: 2-phase SDDP")  // NOLINT
{
  const auto make = []
  {
    auto p = ieee14_dlr(Weather::adverse, "min");
    split_two_phases(p);
    return p;
  };
  // Monolithic reference over the same 2-stage horizon.
  auto mono = make();
  mono.simulation.phase_array.clear();
  const double obj_mono = solve_obj(std::move(mono));

  auto sddp_planning = make();
  sddp_planning.options.method = MethodType::sddp;
  PlanningLP planning_lp(std::move(sddp_planning));
  SDDPOptions opts;
  opts.max_iterations = 5;
  opts.convergence_tol = 1e-6;
  opts.enable_api = false;
  SDDPMethod sddp(planning_lp, opts);
  const auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  const auto& last = results->back();
  MESSAGE("SDDP ub=" << last.upper_bound << " lb=" << last.lower_bound
                     << " mono=" << obj_mono);
  CHECK(last.upper_bound == doctest::Approx(last.lower_bound).epsilon(1e-6));
  // No state couples the phases: SDDP equals the monolithic optimum.
  CHECK(last.upper_bound == doctest::Approx(obj_mono).epsilon(1e-6));
}

TEST_CASE("DLR integration bat_4b_24: hourly weather, min vs rows")  // NOLINT
{
  const auto make = [](std::string mode)
  {
    auto p = load_case("bat_4b_24");
    constexpr int nb = 24;
    std::vector<double> ta(nb);
    std::vector<double> ws(nb);
    std::vector<double> wd(nb);
    std::vector<double> si(nb);
    for (int h = 0; h < nb; ++h) {
      const double ang = 2.0 * std::numbers::pi * (h - 9) / 24.0;
      ta[h] = 22.0 + (10.0 * std::sin(ang));  // peak mid-afternoon
      ws[h] = 1.2 + (0.9 * std::cos(ang));
      wd[h] = 30.0 * h;  // veering wind
      si[h] = std::max(0.0, 1000.0 * std::sin(std::numbers::pi * (h - 6) / 12));
    }
    const auto stb = [](const std::vector<double>& v)
    {
      return OptSTBRealFieldSched {
          std::vector<std::vector<std::vector<Real>>> {{v}}};
    };
    for (auto& l : p.system.line_array) {
      LineDlr dlr {
          .conductor = drake(),
          .tmax_conductor = 75.0,
          .voltage = 66.0,
          .span_mode = mode,
      };
      for (int k = 0; k < 3; ++k) {
        dlr.critical_spans.push_back(CriticalSpan {
            .uid = (100 * l.uid) + k,
            .azimuth = 60.0 * k,
            .elevation = 200.0 * k,
            .ambient_temperature = stb(ta),
            .wind_speed = stb(ws),
            .wind_direction = stb(wd),
            .solar_irradiance = stb(si),
        });
      }
      l.dlr = dlr;
    }
    return p;
  };
  const auto timed = [&](std::string mode)
  {
    const auto t0 = std::chrono::steady_clock::now();
    const double obj = solve_obj(make(std::move(mode)));
    const auto dt =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - t0)
            .count();
    return std::pair {obj, dt};
  };
  const auto [obj_static, t_static] = [&]
  {
    const auto t0 = std::chrono::steady_clock::now();
    const double obj = solve_obj(load_case("bat_4b_24"));
    return std::pair {
        obj,
        std::chrono::duration<double>(std::chrono::steady_clock::now() - t0)
            .count()};
  }();
  const auto [obj_min, t_min] = timed("min");
  const auto [obj_rows, t_rows] = timed("rows");
  MESSAGE("bat_4b_24 obj static="
          << obj_static << " (" << t_static << " s) min=" << obj_min << " ("
          << t_min << " s) rows=" << obj_rows << " (" << t_rows << " s)");
  CHECK(obj_rows == doctest::Approx(obj_min).epsilon(1e-8));
  // The hourly DLR binds somewhere (the case is not unconstrained).
  CHECK(obj_min != doctest::Approx(obj_static).epsilon(1e-9));
}

}  // namespace test_line_dlr_e2e_ns
