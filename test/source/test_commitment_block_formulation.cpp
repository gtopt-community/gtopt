// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_commitment_block_formulation.cpp
 * @brief     Commitment formulation follows the stage; simple_commitment alias
 * @date      2026-11-24
 *
 * A Commitment on a chronological stage builds the intertemporal model;
 * on a non-chronological stage it builds the per-block formulation (u per
 * block, ``g <= Pmax·u``, ``g >= Commitment.pmin·u``).  ``Commitment.pmin``
 * and ``Generator.pmin`` (always-on floor) mean the same in both.  The
 * deprecated ``simple_commitment_array`` folds into plain Commitments; its
 * ``dispatch_pmin`` becomes ``pmin`` and the generator's own ``pmin``,
 * which the legacy element made conditional, moves onto the commitment.
 */

#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/inertia_provision_lp.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>

using namespace gtopt;

namespace
{
/// Base (10 $/MWh, cap 60) and units "ga" (30 $/MWh) / "gb" (40 $/MWh),
/// pmax 100, on one bus.  @p demand is the per-block lmax JSON,
/// @p nblocks its length, @p extra extra system members (with trailing
/// comma).
[[nodiscard]] std::string uc_case(int nblocks,
                                  bool chrono,
                                  const std::string& demand,
                                  const std::string& a_pmin,
                                  const std::string& b_pmin,
                                  const std::string& extra)
{
  std::string blocks = "[";
  for (int b = 1; b <= nblocks; ++b) {
    blocks += (b > 1 ? ", " : "") + std::string {R"({"uid": )"}
        + std::to_string(b) + R"(, "duration": 1})";
  }
  blocks += "]";
  return R"({
  "options": {"annual_discount_rate": 0.0,
    "model_options": {"use_single_bus": true, "demand_fail_cost": 1.0e5,
                      "scale_objective": 1}},
  "simulation": {"block_array": )"
      + blocks + R"(,
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": )"
      + std::to_string(nblocks) + R"(, "chronological": )"
      + (chrono ? "true" : "false") + R"(}],
    "scenario_array": [{"uid": 1}]},
  "system": {"name": "uc_block", "bus_array": [{"uid": 1, "name": "b1"}],
    "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "lmax": )"
      + demand + R"(}],
    )" + extra
      + R"(
    "generator_array": [
      {"uid": 1, "name": "base", "bus": 1, "gcost": 10, "capacity": 60},
      {"uid": 2, "name": "ga", "bus": 1, "gcost": 30, "pmin": )"
      + a_pmin + R"(, "pmax": 100, "capacity": 100},
      {"uid": 3, "name": "gb", "bus": 1, "gcost": 40, "pmin": )"
      + b_pmin + R"(, "pmax": 100, "capacity": 100}]
  }
})";
}

[[nodiscard]] double solve_obj(const std::string& json)
{
  PlanningLP plp(parse_planning_json(json));
  REQUIRE(plp.resolve().has_value());
  return plp.systems().front().front().linear_interface().get_obj_value();
}

constexpr std::string_view legacy_sc = R"("simple_commitment_array": [
        {"uid": 1, "name": "sc_a", "generator": "ga", "dispatch_pmin": 50},
        {"uid": 2, "name": "sc_b", "generator": "gb"}],)";
}  // namespace

TEST_CASE("simple_commitment alias keeps the legacy results")  // NOLINT
{
  // Legacy SimpleCommitment: the element zeroed Generator.pmin, so
  //   ga ∈ {0} ∪ [dispatch_pmin = 50, 100],  gb ∈ {0} ∪ [pmin = 20, 100].
  // Block 1 (demand 100): base 50 + ga 50 = 500 + 1500 = 2000.
  // Block 2 (demand 10):  base 10 = 100, both units OFF.
  // Total 2100 = value of the pre-merge SimpleCommitmentLP (captured
  // before the merge).
  SUBCASE("non-chronological stage (block formulation)")
  {
    CHECK(solve_obj(uc_case(
              2, false, "[[100, 10]]", "30", "20", std::string {legacy_sc}))
          == doctest::Approx(2100.0));
  }
  SUBCASE("chronological stage (full formulation, no startup data)")
  {
    // u − u₋₁ − v + w = 0 with free v, w: same feasible dispatch.
    CHECK(solve_obj(uc_case(
              2, true, "[[100, 10]]", "30", "20", std::string {legacy_sc}))
          == doctest::Approx(2100.0));
  }
}

TEST_CASE("Commitment: Generator.pmin is always-on in both formulations")
{  // NOLINT
  // ga: Generator.pmin 20 (always on), Commitment.pmin 25.
  // Block 1 (100): base 60 + ga 40 = 600 + 1200 = 1800.
  // Block 2 (30):  ga must run (g >= 20, so u = 1, g >= 25): ga 25 +
  //                base 5 = 750 + 50 = 800.   Total 2600.
  const std::string uc =
      R"("commitment_array": [{"uid": 1, "name": "uc_a", "generator": "ga",
          "pmin": 25}],)";
  for (const bool chrono : {false, true}) {
    CAPTURE(chrono);
    CHECK(solve_obj(uc_case(2, chrono, "[[100, 30]]", "20", "0", uc))
          == doctest::Approx(2600.0));
  }
  // Legacy alias of the same data: the generator floor turns conditional,
  // block 2 is served by base alone (300):  1800 + 300 = 2100.
  CHECK(solve_obj(
            uc_case(2,
                    false,
                    "[[100, 30]]",
                    "20",
                    "0",
                    R"("simple_commitment_array": [{"uid": 1, "name": "sc_a",
                "generator": "ga", "dispatch_pmin": 25}],)"))
        == doctest::Approx(2100.0));
}

TEST_CASE("Commitment on a non-chronological stage is not ignored")  // NOLINT
{
  // Commitment.pmin 50 on ga, Generator.pmin 0, demand 100: committed ga
  // runs at >= 50, so base 50 + ga 50 = 2000 (without the commitment the
  // optimum is base 60 + ga 40 = 1800; the stage used to skip it).
  // Chronological-only data (startup / shutdown cost, ramp, min up time)
  // is ignored on the block formulation.
  for (const std::string& chrono_data :
       {std::string {}, std::string {R"(, "startup_cost": 1000,
          "shutdown_cost": 500, "ramp_up": 1, "min_up_time": 5)"}})
  {
    CAPTURE(chrono_data);
    const auto uc =
        R"("commitment_array": [{"uid": 1, "name": "uc_a", "generator": "ga",
          "pmin": 50)"
        + chrono_data + "}],";
    CHECK(solve_obj(uc_case(1, false, "[[100]]", "0", "0", uc))
          == doctest::Approx(2000.0));
  }
}

TEST_CASE("Commitment: no-load cost is charged per block on a block stage")
{  // NOLINT
  // Block stage, 2 blocks of 2 h and 3 h, demand 100 / 30.  ga: pmin 50,
  // noload_cost 7 $/h; gb idle.  The startup cost is ignored.
  //  Block 1 (2 h): base 50 + ga 50, u = 1:
  //      2·(500 + 1500) + 7·2 = 4014.
  //  Block 2 (3 h): base 30, ga OFF (u = 0, no no-load):  3·300 = 900.
  //  Total 4914 (without the no-load charge: 4900).
  auto json = uc_case(2,
                      /*chrono=*/false,
                      "[[100, 30]]",
                      "0",
                      "0",
                      R"("commitment_array": [{"uid": 1, "name": "uc_a",
          "generator": "ga", "pmin": 50, "noload_cost": 7,
          "startup_cost": 1000}],)");
  const std::string b1 = R"({"uid": 1, "duration": 1})";
  const std::string b2 = R"({"uid": 2, "duration": 1})";
  json.replace(json.find(b1), b1.size(), R"({"uid": 1, "duration": 2})");
  json.replace(json.find(b2), b2.size(), R"({"uid": 2, "duration": 3})");
  CHECK(solve_obj(json) == doctest::Approx(4914.0));
}

TEST_CASE(  // NOLINT
    "Commitment: chronological and block formulations agree on reserves and "
    "inertia")
{
  // Same case on a chronological stage (one period per block, no
  // chronological data besides no-load cost) and on a non-chronological
  // stage: identical objective and identical reserve / inertia
  // provisions.  ga carries Commitment.pmin 50, no-load cost 5 $/h, up /
  // down reserve and inertia (H·S = 500).
  const std::string extra = R"(
    "commitment_array": [{"uid": 1, "name": "uc_a", "generator": "ga",
                          "pmin": 50, "noload_cost": 5},
                         {"uid": 2, "name": "uc_b", "generator": "gb",
                          "pmin": 20}],
    "reserve_zone_array": [
      {"uid": 1, "name": "up", "urreq": 30, "urcost": 1000},
      {"uid": 2, "name": "dn", "drreq": 3, "drcost": 1000}],
    "reserve_provision_array": [
      {"uid": 1, "name": "rp_a", "generator": "ga", "reserve_zones": [1, 2],
       "urmax": 100, "drmax": 100, "ur_provision_factor": 1,
       "dr_provision_factor": 1, "urcost": 1, "drcost": 1},
      {"uid": 2, "name": "rp_b", "generator": "gb", "reserve_zones": [1],
       "urmax": 100, "ur_provision_factor": 1, "urcost": 2}],
    "inertia_zone_array": [{"uid": 1, "name": "iz", "requirement": 300,
                            "cost": 1000}],
    "inertia_provision_array": [{"uid": 1, "name": "ip_a",
      "generator": "ga", "inertia_zones": [1], "inertia_constant": 5,
      "rated_power": 100, "cost": 0.01}],)";

  struct Outcome
  {
    double obj {0.0};
    double r_up {0.0};
    double r_dn {0.0};
    double r_h {0.0};
  };
  const auto run = [&](bool chrono)
  {
    PlanningLP plp(parse_planning_json(
        uc_case(3, chrono, "[[100, 20, 70]]", "0", "0", extra)));
    REQUIRE(plp.resolve().has_value());
    auto& sys = plp.systems().front().front();
    const auto& li = sys.linear_interface();
    const auto sol = li.get_col_sol();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& stage = sys.phase().stages().front();
    Outcome o {.obj = li.get_obj_value()};
    for (const auto& block : stage.blocks()) {
      const auto buid = block.uid();
      for (const auto& rp : sys.elements<ReserveProvisionLP>()) {
        if (const auto c = rp.lookup_up_provision_col(scenario, stage, buid)) {
          o.r_up += sol[*c];
        }
        if (const auto c = rp.lookup_dn_provision_col(scenario, stage, buid)) {
          o.r_dn += sol[*c];
        }
      }
      for (const auto& ip : sys.elements<InertiaProvisionLP>()) {
        if (const auto c = ip.lookup_provision_col(scenario, stage, buid)) {
          o.r_h += sol[*c];
        }
      }
    }
    return o;
  };

  const auto chrono = run(true);
  const auto block = run(false);
  CHECK(block.obj == doctest::Approx(chrono.obj));
  CHECK(block.r_up == doctest::Approx(chrono.r_up));
  CHECK(block.r_dn == doctest::Approx(chrono.r_dn));
  CHECK(block.r_h == doctest::Approx(chrono.r_h));
  // Up reserve is fully served (3 × 30).  In block 2 (demand 20 <
  // Commitment.pmin 50) ga is OFF, so it offers no down reserve and no
  // inertia there: down 2 × 3 = 6; inertia FE = H·S / Commitment.pmin =
  // 500 / 50 = 10, so 30 MW covers 300 MWs in blocks 1 and 3 → 60.
  CHECK(chrono.r_up == doctest::Approx(90.0));
  CHECK(chrono.r_dn == doctest::Approx(6.0));
  CHECK(chrono.r_h == doctest::Approx(60.0));
}
