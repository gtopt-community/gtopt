// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <algorithm>
#include <format>
#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/json/json_options.hpp>
#include <gtopt/validate_planning.hpp>

using namespace gtopt;

namespace
{

[[nodiscard]] Planning make_base()
{
  Planning p;
  p.system.bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  p.simulation.block_array = {
      {
          .uid = Uid {1},
          .duration = 1.0,
      },
  };
  p.simulation.stage_array = {
      {
          .uid = Uid {1},
          .count_block = 1,
      },
  };
  return p;
}

[[nodiscard]] ValidationResult sanity(const Planning& p)
{
  ValidationResult r;
  check_model_sanity(r, p);
  return r;
}

/// Number of warnings that contain every needle.
[[nodiscard]] std::size_t count_with(const ValidationResult& r,
                                     std::initializer_list<std::string_view> n)
{
  return static_cast<std::size_t>(std::ranges::count_if(
      r.warnings,
      [&n](const std::string& w)
      {
        return std::ranges::all_of(
            n, [&w](std::string_view s) { return w.contains(s); });
      }));
}

}  // namespace

TEST_CASE("sanity - clean planning emits no sanity warning")  // NOLINT
{
  auto p = make_base();
  const auto r = sanity(p);
  CHECK(r.warnings.empty());
  CHECK(r.errors.empty());
}

TEST_CASE("sanity - water-right reward above the demand fail cost")  // NOLINT
{
  auto p = make_base();
  p.options.model_options.demand_fail_cost = 1000.0;
  p.system.flow_right_array = {
      {
          .uid = Uid {1},
          .name = "maule_105",
          .junction_a = Uid {1},
          .uvalue = 1100.0,
      },
      {
          .uid = Uid {2},
          .name = "neg_fcost",
          .junction_a = Uid {1},
          .fcost = -1500.0,
      },
      {
          .uid = Uid {3},
          .name = "cheap_reward",
          .junction_a = Uid {1},
          .uvalue = 900.0,
      },
  };

  SUBCASE("global fail cost — two offenders in one summary line")
  {
    const auto r = sanity(p);
    CHECK(r.errors.empty());
    REQUIRE(count_with(r, {"2 water-right reward(s)", "phantom reward"}) == 1);
    CHECK(count_with(r, {"maule_105", "uvalue=1100"}) == 1);
    CHECK(count_with(r, {"neg_fcost", "fcost=-1500"}) == 1);
    CHECK(count_with(r, {"cheap_reward"}) == 0);
  }

  SUBCASE("ratio option relaxes the threshold")
  {
    p.options.sanity_options.reward_fail_cost_ratio = 2.0;
    CHECK(count_with(sanity(p), {"water-right reward"}) == 0);
  }

  SUBCASE("per-demand fcost is the reference when no global cost")
  {
    p.options.model_options.demand_fail_cost.reset();
    p.system.demand_array = {
        {
            .uid = Uid {1},
            .name = "d1",
            .bus = Uid {1},
            .fcost = 800.0,
        },
    };
    CHECK(count_with(sanity(p), {"3 water-right reward(s)", "(800)"}) == 1);
  }

  SUBCASE("global hydro_use_value is a reward too")
  {
    p.system.flow_right_array.clear();
    p.options.model_options.hydro_use_value = 5000.0;
    CHECK(count_with(sanity(p), {"hydro_use_value=5000"}) == 1);
  }

  SUBCASE("no fail cost anywhere — nothing to compare against")
  {
    p.options.model_options.demand_fail_cost.reset();
    CHECK(count_with(sanity(p), {"water-right reward"}) == 0);
  }

  SUBCASE("inactive rights are skipped")
  {
    for (auto& fr : p.system.flow_right_array) {
      fr.active = Int {0};
    }
    CHECK(count_with(sanity(p), {"water-right reward"}) == 0);
  }
}

TEST_CASE(
    "sanity - Maule R3 reward above a coupled shortfall penalty")  // NOLINT
{
  // Maule Res. 105 (review R3): the delivery right rewards 1100 while the
  // district deficit right tied to it by `dist_retiro_Melado` costs 1000
  // (written as uvalue = -1000 on a target-0 right).  No demand fail cost
  // is involved: plp_2y sets demand_fail_cost = 0.
  auto p = make_base();
  p.system.flow_right_array = {
      {
          .uid = Uid {1005},
          .name = "maule_resolucion_105",
          .junction_a = Uid {1},
          .target = 0.0,
          .uvalue = 1100.0,
      },
      {
          .uid = Uid {1006},
          .name = "deficit_Melado",
          .target = 0.0,
          .uvalue = -1000.0,
      },
      {
          .uid = Uid {1007},
          .name = "retiro_Melado",
      },
      {
          .uid = Uid {1008},
          .name = "expensive_deficit",
          .fcost = 5000.0,
      },
  };
  p.system.user_constraint_array = {
      {
          .uid = Uid {1},
          .name = "dist_retiro_Melado",
          .expression = "flow_right('retiro_Melado').flow + "
                        "flow_right('deficit_Melado').flow = 0.1265 * "
                        "flow_right('maule_resolucion_105').flow",
      },
      {
          .uid = Uid {2},
          .name = "costly_link",
          .expression = "flow_right(1008).flow = "
                        "flow_right(\"maule_resolucion_105\").flow",
      },
  };

  const auto r = sanity(p);
  CHECK(r.errors.empty());
  REQUIRE(count_with(r, {"1 water-right reward(s)", "shortfall penalty"}) == 1);
  CHECK(count_with(r,
                   {"'maule_resolucion_105' uvalue=1100 vs 'deficit_Melado' "
                    "penalty 1000 (constraint 'dist_retiro_Melado')"})
        == 1);
  CHECK(count_with(r, {"expensive_deficit"}) == 0);

  SUBCASE("R3 fix: penalty raised to CostoRiegoNS + ValorRiego")
  {
    p.system.flow_right_array[1].uvalue = -2100.0;
    CHECK(count_with(sanity(p), {"shortfall penalty"}) == 0);
  }
  SUBCASE("inactive constraint does not couple")
  {
    p.system.user_constraint_array[0].active = false;
    CHECK(count_with(sanity(p), {"shortfall penalty"}) == 0);
  }
}

TEST_CASE("sanity - profiles outside [0, 1]")  // NOLINT
{
  auto p = make_base();
  p.system.generator_profile_array = {
      {
          .uid = Uid {1},
          .name = "solar_mw",
          .generator = Uid {1},
          .profile =
              std::vector<std::vector<std::vector<Real>>> {
                  {{0.0, 45.0, 80.0}},
              },
      },
      {
          .uid = Uid {2},
          .name = "wind_ok",
          .generator = Uid {2},
          .profile = 0.4,
      },
      {
          .uid = Uid {3},
          .name = "file_backed",
          .generator = Uid {3},
          .profile = FileSched {"GeneratorProfile/profile"},
      },
  };
  p.system.demand_profile_array = {
      {
          .uid = Uid {1},
          .name = "neg_load",
          .demand = Uid {1},
          .profile = -0.2,
      },
  };

  const auto r = sanity(p);
  REQUIRE(count_with(r, {"2 profile(s) with values outside [0, 1]"}) == 1);
  CHECK(count_with(r, {"GeneratorProfile 'solar_mw' range [0, 80]"}) == 1);
  CHECK(count_with(r, {"DemandProfile 'neg_load'"}) == 1);
  CHECK(count_with(r, {"wind_ok"}) == 0);
  CHECK(count_with(r, {"file_backed"}) == 0);

  SUBCASE("tolerance option")
  {
    p.system.demand_profile_array.clear();
    p.system.generator_profile_array = {
        {
            .uid = Uid {1},
            .name = "slightly_above",
            .generator = Uid {1},
            .profile = 1.05,
        },
    };
    CHECK(count_with(sanity(p), {"1 profile(s)"}) == 1);
    p.options.sanity_options.profile_tolerance = 0.1;
    CHECK(count_with(sanity(p), {"profile(s)"}) == 0);
  }
}

TEST_CASE("sanity - zero-capacity units with commitment or pmin")  // NOLINT
{
  auto p = make_base();
  p.system.generator_array = {
      {
          .uid = Uid {1},
          .name = "retired_uc",
          .bus = Uid {1},
          .pmax = 0.0,
      },
      {
          .uid = Uid {2},
          .name = "retired_pmin",
          .bus = Uid {1},
          .pmin = 10.0,
          .pmax = std::vector<std::vector<Real>> {{0.0, 0.0}},
      },
      {
          .uid = Uid {3},
          .name = "zero_capacity_simple",
          .bus = Uid {1},
          .capacity = 0.0,
      },
      {
          .uid = Uid {4},
          .name = "expandable",
          .bus = Uid {1},
          .capacity = 0.0,
          .expcap = 50.0,
      },
      {
          .uid = Uid {5},
          .name = "healthy_uc",
          .bus = Uid {1},
          .pmax = 100.0,
      },
      {
          .uid = Uid {6},
          .name = "retired_no_uc",
          .bus = Uid {1},
          .pmax = 0.0,
      },
  };
  p.system.commitment_array = {
      {
          .uid = Uid {1},
          .name = "uc1",
          .generator = Uid {1},
      },
      {
          .uid = Uid {2},
          .name = "uc_expandable",
          .generator = Name {"expandable"},
      },
      {
          .uid = Uid {5},
          .name = "uc5",
          .generator = Uid {5},
      },
  };
  p.system.simple_commitment_array = {
      {
          .uid = Uid {1},
          .name = "sc3",
          .generator = Name {"zero_capacity_simple"},
      },
  };
  p.system.battery_array = {
      {
          .uid = Uid {1},
          .name = "dead_bess",
          .pmax_charge = 0.0,
          .pmax_discharge = 0.0,
          .pmin_discharge = 5.0,
          .commitment = true,
      },
      {
          .uid = Uid {2},
          .name = "live_bess",
          .pmax_charge = 10.0,
          .pmax_discharge = 10.0,
          .commitment = true,
      },
  };

  const auto r = sanity(p);
  CHECK(r.errors.empty());
  REQUIRE(count_with(r, {"4 unit(s) with pmax = 0"}) == 1);
  CHECK(count_with(r, {"Generator 'retired_uc' (UC commitment)"}) == 1);
  CHECK(count_with(r, {"Generator 'retired_pmin' (pmin > 0)"}) == 1);
  CHECK(count_with(r, {"zero_capacity_simple' (simple commitment)"}) == 1);
  CHECK(
      count_with(
          r, {"Battery 'dead_bess' (commitment binaries, pmin_discharge > 0)"})
      == 1);
  CHECK(count_with(r, {"expandable'"}) == 0);
  CHECK(count_with(r, {"healthy_uc"}) == 0);
  CHECK(count_with(r, {"retired_no_uc"}) == 0);
  CHECK(count_with(r, {"live_bess"}) == 0);

  SUBCASE("max_offenders truncates the listing")
  {
    p.options.sanity_options.max_offenders = 1;
    CHECK(count_with(sanity(p), {"(+3 more, see debug log)"}) == 1);
  }
}

TEST_CASE("sanity - line impedance per kV class")  // NOLINT
{
  auto p = make_base();
  p.system.bus_array = {
      {
          .uid = Uid {1},
          .name = "a230",
          .voltage = 230.0,
      },
      {
          .uid = Uid {2},
          .name = "b230",
          .voltage = 230.0,
      },
      {
          .uid = Uid {3},
          .name = "c69",
          .voltage = 69.0,
      },
      {
          .uid = Uid {4},
          .name = "d69",
          .voltage = 69.0,
      },
  };
  // Six healthy 230 kV lines (x ≈ 0.02 p.u. on 100 MVA), one whose
  // impedance was divided by ~5000 (the Guatemala mistyped-kV symptom:
  // 692 kV instead of 69 kV in the Ω → p.u. conversion).
  Array<Line> lines;
  for (int i = 1; i <= 6; ++i) {
    lines.push_back({
        .uid = Uid {i},
        .name = std::format("l230_{}", i),
        .bus_a = Uid {1},
        .bus_b = Uid {2},
        .voltage = 230.0,
        .resistance = 1.0,
        .reactance = 10.0 + i,
    });
  }
  lines.push_back({
      .uid = Uid {7},
      .name = "l230_shrunk",
      .bus_a = Name {"a230"},
      .bus_b = Name {"b230"},
      .voltage = 230.0,
      .reactance = 10.0 / 5000.0,
  });
  // High X/R.
  lines.push_back({
      .uid = Uid {8},
      .name = "l230_xr",
      .bus_a = Uid {1},
      .bus_b = Uid {2},
      .voltage = 230.0,
      .resistance = 0.01,
      .reactance = 12.0,
  });
  // 69 kV line with X in % read as Ω on a 100 MVA base → x_pu ≈ 8.4.
  lines.push_back({
      .uid = Uid {9},
      .name = "l69_big",
      .bus_a = Uid {3},
      .bus_b = Uid {4},
      .voltage = 69.0,
      .reactance = 400.0,
  });
  // DC link (no reactance) and a transformer — never flagged here.
  lines.push_back({
      .uid = Uid {10},
      .name = "hvdc",
      .bus_a = Uid {1},
      .bus_b = Uid {3},
  });
  lines.push_back({
      .uid = Uid {11},
      .name = "tr230_69",
      .bus_a = Uid {1},
      .bus_b = Uid {3},
      .reactance = 0.1,
  });
  p.system.line_array = std::move(lines);

  const auto r = sanity(p);
  CHECK(r.errors.empty());
  CHECK(count_with(r, {"1 line(s) with X/R > 100", "l230_xr"}) == 1);
  CHECK(count_with(r, {"1 line(s) with reactance above 5 p.u.", "l69_big"})
        == 1);
  CHECK(count_with(r, {"1 line(s) whose |X|/V²", "l230_shrunk", "[230 kV]"})
        == 1);
  CHECK(count_with(r, {"hvdc"}) == 0);
  CHECK(count_with(r, {"tr230_69"}) == 0);

  SUBCASE("thresholds are options")
  {
    p.options.sanity_options.line_xr_max = 10000.0;
    p.options.sanity_options.line_x_pu_max = 100.0;
    p.options.sanity_options.line_x_outlier_factor = 1e6;
    const auto r2 = sanity(p);
    CHECK(count_with(r2, {"line(s)"}) == 0);
  }

  SUBCASE("small classes skip the outlier test")
  {
    p.options.sanity_options.line_x_min_class_size = 20;
    CHECK(count_with(sanity(p), {"class median"}) == 0);
  }
}

TEST_CASE("sanity - agreement reserve level without reservoir emin")  // NOLINT
{
  auto p = make_base();
  p.system.reservoir_array = {
      {
          .uid = Uid {1},
          .name = "laja",
          .junction = Uid {1},
      },
      {
          .uid = Uid {2},
          .name = "maule",
          .junction = Uid {2},
          .emin = 100.0,
      },
      {
          .uid = Uid {3},
          .name = "colbun",
          .junction = Uid {3},
          .emin = 0.0,
      },
  };
  const RightBoundRule laja_rule {
      .reservoir = Uid {1},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.0,
                  .constant = 10.0,
              },
              {
                  .volume = 1370.0,
                  .slope = 0.0,
                  .constant = 50.0,
              },
          },
  };
  p.system.flow_right_array = {
      {
          .uid = Uid {1},
          .name = "laja_riego",
          .junction_a = Uid {1},
          .bound_rule = laja_rule,
      },
      {
          .uid = Uid {2},
          .name = "maule_riego",
          .junction_a = Uid {2},
          .bound_rule =
              RightBoundRule {
                  .reservoir = Name {"maule"},
                  .segments =
                      {
                          {
                              .volume = 500.0,
                              .slope = 0.0,
                              .constant = 5.0,
                          },
                      },
              },
      },
  };
  p.system.volume_right_array = {
      {
          .uid = Uid {1},
          .name = "colbun_vr",
          .bound_rule =
              RightBoundRule {
                  .reservoir = Uid {3},
                  .segments =
                      {
                          {
                              .volume = 200.0,
                              .slope = 0.0,
                              .constant = 1.0,
                          },
                      },
              },
      },
      {
          .uid = Uid {2},
          .name = "month_axis",
          .bound_rule =
              RightBoundRule {
                  .reservoir = Uid {1},
                  .segments =
                      {
                          {
                              .volume = 4.0,
                              .slope = 0.0,
                              .constant = 1.0,
                          },
                      },
                  .axis = BoundRuleAxis::stage_month,
              },
      },
  };

  const auto r = sanity(p);
  CHECK(r.errors.empty());
  REQUIRE(count_with(r, {"2 reservoir(s) without emin"}) == 1);
  CHECK(count_with(r,
                   {"Reservoir 'laja' (reserve level 1370 hm³ in FlowRight "
                    "'laja_riego')"})
        == 1);
  CHECK(count_with(r, {"Reservoir 'colbun'", "VolumeRight 'colbun_vr'"}) == 1);
  CHECK(count_with(r, {"'maule'"}) == 0);
  CHECK(count_with(r, {"month_axis"}) == 0);
}

TEST_CASE("sanity - wired into validate_planning, warnings only")  // NOLINT
{
  auto p = make_base();
  p.options.model_options.demand_fail_cost = 1000.0;
  p.system.flow_right_array = {
      {
          .uid = Uid {1},
          .name = "fr",
          .junction_a = Uid {1},
          .uvalue = 1100.0,
      },
  };
  p.system.junction_array = {
      {
          .uid = Uid {1},
          .name = "j1",
      },
  };
  SUBCASE("enabled by default")
  {
    const auto r = validate_planning(p);
    CHECK(r.ok());
    CHECK(count_with(r, {"Sanity:", "phantom reward"}) == 1);
  }
  SUBCASE("enable=false silences every category")
  {
    p.options.sanity_options.enable = false;
    const auto r = validate_planning(p);
    CHECK(count_with(r, {"Sanity:"}) == 0);
  }
}

TEST_CASE("sanity_options - JSON round trip and merge")  // NOLINT
{
  constexpr std::string_view json = R"({
      "sanity_options": {
        "enable": true,
        "reward_fail_cost_ratio": 1.5,
        "profile_tolerance": 0.01,
        "line_x_outlier_factor": 20,
        "line_x_min_class_size": 8,
        "line_x_pu_max": 3,
        "line_xr_max": 60,
        "max_offenders": 3
      }
  })";
  const auto options = daw::json::from_json<PlanningOptions>(json);
  const auto& so = options.sanity_options;
  CHECK(so.effective_enable());
  CHECK(so.effective_reward_fail_cost_ratio() == doctest::Approx(1.5));
  CHECK(so.effective_profile_tolerance() == doctest::Approx(0.01));
  CHECK(so.effective_line_x_outlier_factor() == doctest::Approx(20.0));
  CHECK(so.effective_line_x_min_class_size() == 8);
  CHECK(so.effective_line_x_pu_max() == doctest::Approx(3.0));
  CHECK(so.effective_line_xr_max() == doctest::Approx(60.0));
  CHECK(so.effective_max_offenders() == 3);

  const auto out = daw::json::to_json(options);
  CHECK(out.contains("\"line_xr_max\":60"));

  SanityOptions defaults;
  CHECK(defaults.effective_line_xr_max()
        == doctest::Approx(SanityOptions::default_line_xr_max));
  defaults.merge(so);
  CHECK(defaults.effective_max_offenders() == 3);
  SanityOptions keep {
      .line_xr_max = 10.0,
  };
  keep.merge(SanityOptions {});  // unset fields never override
  CHECK(keep.effective_line_xr_max() == doctest::Approx(10.0));
  keep.merge(so);  // a set field overrides (merge_opt semantics)
  CHECK(keep.effective_line_xr_max() == doctest::Approx(60.0));
}
