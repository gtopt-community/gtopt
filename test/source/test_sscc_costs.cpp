// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// SSCC (ancillary-service) costs and usage accounting.
//
// Phase 1: reserve costs per service.
//
// One generator offering CPF / CSF / CTF is modelled with ONE
// ReserveProvision PER SERVICE (one zone each), all flagged
// `shared_headroom`, so that
//
//   * every service carries its own direct provision cost (urcost /
//     drcost of its provision, $/MW·h), and
//   * the services share one headroom pair
//       p + Σ_s r↑_s ≤ Pmax(·u),   p − Σ_s r↓_s ≥ Pmin(·u).
//
// A single provision listing several zones has ONE column per direction
// that counts towards every listed requirement at the same time (nested
// products, minuta (A.3)–(A.12)); its cost is paid once.  Per-zone cost
// arrays would therefore have no meaning there and are not provided.
//
// Every expected value is hand-computed (1 h blocks, probability 1, no
// discounting, scale_objective = 1).

#include <algorithm>
#include <cmath>
#include <format>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

#include "lp_names_helpers.hpp"
#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

/// Single-bus case assembled from raw JSON array bodies.
struct SsccCase
{
  int nblocks {1};
  bool chronological {false};  ///< needed by CommitmentLP
  std::string generators;
  std::string demands;
  std::string batteries;
  std::string reserve_zones;
  std::string reserve_provisions;
  std::string commitments;
  std::string fuels;
  std::string model_options;  ///< extra `model_options` members
  std::string options;  ///< extra top-level `options` members

  [[nodiscard]] std::string str() const
  {
    std::string blocks;
    for (int b = 1; b <= nblocks; ++b) {
      blocks +=
          std::format(R"({}{{"uid": {}, "duration": 1}})", b > 1 ? "," : "", b);
    }
    const auto arr = [](std::string_view key, const std::string& body)
    {
      return body.empty() ? std::string {}
                          : std::format(R"(, "{}": [{}])", key, body);
    };
    return std::format(
        R"({{
  "options": {{
    "annual_discount_rate": 0.0,{}
    "model_options": {{
      "use_single_bus": true,
      "demand_fail_cost": 1.0e5,
      "scale_objective": 1.0{}
    }}
  }},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": {}}}],
    "scenario_array": [{{"uid": 1}}]
  }},
  "system": {{
    "name": "sscc",
    "bus_array": [{{"uid": 1, "name": "b1"}}]{}{}{}{}{}{}{}
  }}
}})",
        options.empty() ? std::string {} : std::format(" {},", options),
        model_options.empty() ? std::string {}
                              : std::format(", {}", model_options),
        blocks,
        nblocks,
        chronological,
        arr("demand_array", demands),
        arr("generator_array", generators),
        arr("battery_array", batteries),
        arr("reserve_zone_array", reserve_zones),
        arr("reserve_provision_array", reserve_provisions),
        arr("commitment_array", commitments),
        arr("fuel_array", fuels));
  }
};

/// Solved SsccCase with name-based accessors (block index 0-based).
struct SsccSolved
{
  std::unique_ptr<PlanningLP> plp;

  explicit SsccSolved(const SsccCase& c)
      : plp(std::make_unique<PlanningLP>(
            solver_test::with_mip_solver(parse_planning_json(c.str())),
            test_support::names_on_flat_opts()))
  {
    auto status = plp->resolve();
    REQUIRE(status.has_value());
  }

  [[nodiscard]] auto& sys() const { return plp->systems().front().front(); }
  [[nodiscard]] auto& li() const { return sys().linear_interface(); }
  [[nodiscard]] const auto& scenario() const
  {
    return sys().scene().scenarios().front();
  }
  [[nodiscard]] const auto& stage() const
  {
    return sys().phase().stages().front();
  }
  [[nodiscard]] BlockUid buid(int b) const
  {
    return stage().blocks().at(static_cast<std::size_t>(b)).uid();
  }
  [[nodiscard]] double obj() const { return li().get_obj_value(); }

  [[nodiscard]] double col(std::optional<ColIndex> c) const
  {
    REQUIRE(c.has_value());
    return li().get_col_sol()[c.value_or(ColIndex {0})];
  }
  /// Column value, 0 when the column does not exist (elided / gated).
  [[nodiscard]] double col_or0(std::optional<ColIndex> c) const
  {
    return c ? li().get_col_sol()[*c] : 0.0;
  }

  [[nodiscard]] const ReserveZoneLP& zone(std::string_view name) const
  {
    for (const auto& z : sys().template elements<ReserveZoneLP>()) {
      if (z.object().name == name) {
        return z;
      }
    }
    FAIL("zone not found");
    return sys().template elements<ReserveZoneLP>().front();
  }

  [[nodiscard]] const ReserveProvisionLP& provision(std::string_view name) const
  {
    for (const auto& p : sys().template elements<ReserveProvisionLP>()) {
      if (p.object().name == name) {
        return p;
      }
    }
    FAIL("provision not found");
    return sys().template elements<ReserveProvisionLP>().front();
  }

  [[nodiscard]] double up(std::string_view prov, int b = 0) const
  {
    return col_or0(
        provision(prov).lookup_up_provision_col(scenario(), stage(), buid(b)));
  }
  [[nodiscard]] double dn(std::string_view prov, int b = 0) const
  {
    return col_or0(
        provision(prov).lookup_dn_provision_col(scenario(), stage(), buid(b)));
  }

  [[nodiscard]] double gen(std::string_view name, int b = 0) const
  {
    for (const auto& g : sys().template elements<GeneratorLP>()) {
      if (g.object().name == name) {
        return col_or0(g.lookup_generation_col(scenario(), stage(), buid(b)));
      }
    }
    FAIL("generator not found");
    return 0.0;
  }

  /// Reserve price of zone @p z (|dual| of the requirement row).
  [[nodiscard]] double umu(std::string_view z, int b = 0) const
  {
    const auto& rows = zone(z).urequirement_rows();
    const auto it = rows.find({scenario().uid(), stage().uid()});
    REQUIRE(it != rows.end());
    return std::abs(li().get_row_dual()[it->second.at(buid(b))]);
  }
  [[nodiscard]] double dmu(std::string_view z, int b = 0) const
  {
    const auto& rows = zone(z).drequirement_rows();
    const auto it = rows.find({scenario().uid(), stage().uid()});
    REQUIRE(it != rows.end());
    return std::abs(li().get_row_dual()[it->second.at(buid(b))]);
  }
  /// Served up / down requirement of zone @p z.
  [[nodiscard]] double ureq(std::string_view z, int b = 0) const
  {
    return col(zone(z).lookup_urequirement_col(scenario(), stage(), buid(b)));
  }
  [[nodiscard]] double dreq(std::string_view z, int b = 0) const
  {
    return col(zone(z).lookup_drequirement_col(scenario(), stage(), buid(b)));
  }

  [[nodiscard]] const GeneratorLP& generator(std::string_view name) const
  {
    for (const auto& g : sys().template elements<GeneratorLP>()) {
      if (g.object().name == name) {
        return g;
      }
    }
    FAIL("generator not found");
    return sys().template elements<GeneratorLP>().front();
  }

  /// Per-block values of one output stream `<fname>_<sname>` of element
  /// @p e (class @p cname), read back through `OutputContext` (one
  /// scenario / stage, so the slot is the block index).  Empty when the
  /// stream was not emitted.
  template<typename ElementLP>
  [[nodiscard]] std::vector<double> output_of(
      const ElementLP& e,
      std::string_view cname,
      std::string_view fname,
      std::string_view sname = "sol") const
  {
    OutputContext oc(sys().system_context(),
                     sys().linear_interface(),
                     sys().scene().uid(),
                     sys().phase().uid(),
                     sys().phase().is_continuous());
    REQUIRE(e.add_to_output(oc));
    const OutputContext::ClassFieldName key {cname, fname, sname};
    const auto it = oc.fields().find(key);
    if (it == oc.fields().end() || it->second.empty()) {
      return {};
    }
    const auto& entries = it->second.front().entries;
    std::vector<double> values(entries.empty() ? 0 : entries.back().first + 1,
                               0.0);
    for (const auto& [slot, value] : entries) {
      values[slot] = value;
    }
    return values;
  }
};

/// Two-service fixture on one unit `g1` (100 MW, 10 $/MWh) plus an
/// expensive backstop `peak` (200 $/MWh, no reserve).  CPF and CSF up
/// requirements of 10 MW each (shortage 1000 $/MW·h).  @p load sets the
/// dispatch of g1; @p cpf_cost / @p csf_cost are the per-service costs.
SsccCase two_service_case(
    double load,
    double cpf_cost,
    double csf_cost,
    std::string_view shared = R"(, "shared_headroom": true)")
{
  SsccCase c;
  c.demands = std::format(
      R"({{"uid": 1, "name": "d1", "bus": 1, "capacity": {}}})", load);
  c.generators = R"(
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100},
      {"uid": 2, "name": "peak", "bus": 1, "gcost": 200, "capacity": 100})";
  c.reserve_zones = R"(
      {"uid": 1, "name": "cpf", "urreq": 10, "urcost": 1000},
      {"uid": 2, "name": "csf", "urreq": 10, "urcost": 1000})";
  c.reserve_provisions = std::format(
      R"({{"uid": 1, "name": "g1_cpf", "generator": "g1",
          "reserve_zones": ["cpf"], "urmax": 100, "ur_provision_factor": 1,
          "urcost": {0}{2}}},
         {{"uid": 2, "name": "g1_csf", "generator": "g1",
          "reserve_zones": ["csf"], "urmax": 100, "ur_provision_factor": 1,
          "urcost": {1}{2}}})",
      cpf_cost,
      csf_cost,
      shared);
  return c;
}

}  // namespace

TEST_CASE("SSCC per-service cost: one unit prices CPF and CSF apart")  // NOLINT
{
  // Load 50: g1 has 50 MW of slack, no opportunity cost.  Each zone's
  // price is exactly the direct cost of ITS service on g1.
  const SsccSolved s(two_service_case(50.0, 2.0, 7.0));
  CHECK(s.up("g1_cpf") == doctest::Approx(10.0));
  CHECK(s.up("g1_csf") == doctest::Approx(10.0));
  CHECK(s.umu("cpf") == doctest::Approx(2.0));
  CHECK(s.umu("csf") == doctest::Approx(7.0));
  // Objective = energy + Σ_service cost_s · r_s.
  CHECK(s.obj()
        == doctest::Approx((50.0 * 10.0) + (10.0 * 2.0) + (10.0 * 7.0)));

  SUBCASE("swapping the costs swaps the prices (invariant)")
  {
    const SsccSolved t(two_service_case(50.0, 7.0, 2.0));
    CHECK(t.umu("cpf") == doctest::Approx(7.0));
    CHECK(t.umu("csf") == doctest::Approx(2.0));
    CHECK(t.obj() == doctest::Approx(s.obj()));
  }
}

TEST_CASE("SSCC per-service cost: services share one headroom")  // NOLINT
{
  // Load 95 with only 5 MW slack on g1: the shared row
  // p + r_cpf + r_csf ≤ 100 forces g1 down to 80 and the 200 $/MWh
  // peaker covers 15 MW.  Both services then carry the SAME opportunity
  // cost (200 − 10) on top of their own direct cost.
  const SsccSolved s(two_service_case(95.0, 2.0, 7.0));
  CHECK(s.gen("g1") + s.up("g1_cpf") + s.up("g1_csf")
        <= doctest::Approx(100.0));
  CHECK(s.gen("g1") == doctest::Approx(80.0));
  CHECK(s.gen("peak") == doctest::Approx(15.0));
  CHECK(s.umu("cpf") == doctest::Approx(2.0 + 190.0));
  CHECK(s.umu("csf") == doctest::Approx(7.0 + 190.0));
  // The price gap between services is exactly the cost gap.
  CHECK(s.umu("csf") - s.umu("cpf") == doctest::Approx(7.0 - 2.0));
  CHECK(s.obj()
        == doctest::Approx((80.0 * 10.0) + (15.0 * 200.0) + (10.0 * 2.0)
                           + (10.0 * 7.0)));

  SUBCASE("without shared_headroom each service sees the full headroom")
  {
    // Per-provision rows p + r_s ≤ 100: g1 keeps 90 + 5 (double counts).
    const SsccSolved t(two_service_case(95.0, 2.0, 7.0, ""));
    CHECK(t.gen("g1") == doctest::Approx(90.0));
    CHECK(t.gen("g1") + t.up("g1_cpf") + t.up("g1_csf") > 100.0 + 1e-6);
  }
}

TEST_CASE("SSCC per-service cost: committed footroom shared, gated by u")
// NOLINT
{
  // g1 (pmin 40 via Commitment, 100 MW) offers CPF− and CSF− down
  // reserve, 25 MW each requested.  Shared footroom
  // p − r_cpf − r_csf ≥ 40·u with p = 80 → Σ r ≤ 40: 10 MW short.
  const auto make = [](double load, std::string_view shared)
  {
    SsccCase c;
    c.chronological = true;
    c.demands = std::format(
        R"({{"uid": 1, "name": "d1", "bus": 1, "capacity": {}}})", load);
    c.generators = R"(
        {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100},
        {"uid": 2, "name": "peak", "bus": 1, "gcost": 200, "capacity": 100})";
    c.commitments =
        R"({"uid": 1, "name": "uc_g1", "generator": "g1", "pmin": 40,
            "initial_status": 1})";
    c.reserve_zones = R"(
        {"uid": 1, "name": "cpf", "drreq": 25, "drcost": 50},
        {"uid": 2, "name": "csf", "drreq": 25, "drcost": 50})";
    c.reserve_provisions = std::format(
        R"({{"uid": 1, "name": "g1_cpf", "generator": "g1",
            "reserve_zones": ["cpf"], "drmax": 100, "dr_provision_factor": 1,
            "drcost": 1{0}}},
           {{"uid": 2, "name": "g1_csf", "generator": "g1",
            "reserve_zones": ["csf"], "drmax": 100, "dr_provision_factor": 1,
            "drcost": 3{0}}})",
        shared);
    return c;
  };

  {
    const SsccSolved s(make(80.0, R"(, "shared_headroom": true)"));
    CHECK(s.gen("g1") == doctest::Approx(80.0));
    CHECK(s.dn("g1_cpf") + s.dn("g1_csf") == doctest::Approx(40.0));
    CHECK(s.gen("g1") - s.dn("g1_cpf") - s.dn("g1_csf")
          >= doctest::Approx(40.0));
    // The cheaper service is filled first: CPF− 25 (1 $), CSF− 15 (3 $).
    CHECK(s.dn("g1_cpf") == doctest::Approx(25.0));
    CHECK(s.dn("g1_csf") == doctest::Approx(15.0));
    CHECK(s.dreq("csf") == doctest::Approx(15.0));
  }
  {
    // Per-provision footroom double counts: both services fully served.
    const SsccSolved s(make(80.0, ""));
    CHECK(s.dn("g1_cpf") == doctest::Approx(25.0));
    CHECK(s.dn("g1_csf") == doctest::Approx(25.0));
  }
}

TEST_CASE("SSCC per-service cost: offline unit offers no reserve")  // NOLINT
{
  // Two services on a committed unit that is cheaper to keep OFF:
  // `peak` serves the load at 20 $/MWh, g1 (pmin 50, 90 $/MWh) would
  // have to run at ≥ 50 MW to offer anything; reserve shortage is cheap
  // (5 $/MW·h), so g1 stays off and every service shows u-gated zeros.
  SsccCase c;
  c.chronological = true;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 60})";
  c.generators = R"(
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 90, "capacity": 100},
      {"uid": 2, "name": "peak", "bus": 1, "gcost": 20, "capacity": 100})";
  c.commitments =
      R"({"uid": 1, "name": "uc_g1", "generator": "g1", "pmin": 50,
          "initial_status": 0})";
  c.reserve_zones = R"(
      {"uid": 1, "name": "cpf", "urreq": 10, "urcost": 5, "drreq": 10,
       "drcost": 5},
      {"uid": 2, "name": "csf", "urreq": 10, "urcost": 5})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "g1_cpf", "generator": "g1",
          "reserve_zones": ["cpf"], "urmax": 100, "drmax": 100,
          "ur_provision_factor": 1, "dr_provision_factor": 1,
          "urcost": 1, "drcost": 1, "shared_headroom": true},
         {"uid": 2, "name": "g1_csf", "generator": "g1",
          "reserve_zones": ["csf"], "urmax": 100, "ur_provision_factor": 1,
          "urcost": 1, "shared_headroom": true})";
  const SsccSolved s(c);
  CHECK(s.gen("g1") == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(s.up("g1_cpf") == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(s.up("g1_csf") == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(s.dn("g1_cpf") == doctest::Approx(0.0).epsilon(1e-9));
}

TEST_CASE("SSCC per-service cost: multi-zone provision is priced once")
// NOLINT
{
  // ONE provision listing both zones: its single up column counts for
  // CPF and CSF at the same time (nested products) and its cost is paid
  // once — 10 MW · 4 $, not 2 · 10 · 4.
  SsccCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 50})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100})";
  c.reserve_zones = R"(
      {"uid": 1, "name": "cpf", "urreq": 10, "urcost": 1000},
      {"uid": 2, "name": "csf", "urreq": 10, "urcost": 1000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "g1_both", "generator": "g1",
          "reserve_zones": ["cpf", "csf"], "urmax": 100,
          "ur_provision_factor": 1, "urcost": 4})";
  const SsccSolved s(c);
  CHECK(s.up("g1_both") == doctest::Approx(10.0));
  CHECK(s.obj() == doctest::Approx((50.0 * 10.0) + (10.0 * 4.0)));
  // The 4 $ cost is split between the two (degenerate) zone prices.
  CHECK(s.umu("cpf") + s.umu("csf") == doctest::Approx(4.0));
}

TEST_CASE("SSCC per-service cost: negative reserve cost is rejected")  // NOLINT
{
  SUBCASE("validate_planning flags inline negative urcost / drcost")
  {
    auto c = two_service_case(50.0, -1.0, 7.0);
    auto planning = parse_planning_json(c.str());
    const auto vr = validate_planning(planning);
    REQUIRE_FALSE(vr.errors.empty());
    bool found = false;
    for (const auto& e : vr.errors) {
      found = found || (e.contains("g1_cpf") && e.contains("urcost"));
    }
    CHECK(found);
  }
  SUBCASE("negative drcost too")
  {
    SsccCase c;
    c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 50})";
    c.generators =
        R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100})";
    c.reserve_zones = R"({"uid": 1, "name": "cpf", "drreq": 10})";
    c.reserve_provisions =
        R"({"uid": 1, "name": "g1_cpf", "generator": "g1",
            "reserve_zones": ["cpf"], "drmax": 100,
            "dr_provision_factor": 1, "drcost": [[-0.5]]})";
    auto planning = parse_planning_json(c.str());
    const auto vr = validate_planning(planning);
    bool found = false;
    for (const auto& e : vr.errors) {
      found = found || (e.contains("g1_cpf") && e.contains("drcost"));
    }
    CHECK(found);
  }
  SUBCASE("the LP builder rejects it as well (file schedules)")
  {
    // Bypass validate_planning: build the LP directly.
    auto c = two_service_case(50.0, -1.0, 7.0);
    CHECK_THROWS_WITH(PlanningLP(parse_planning_json(c.str())),
                      doctest::Contains("must be finite and >= 0"));
  }
  SUBCASE("zero cost stays legal")
  {
    auto c = two_service_case(50.0, 0.0, 0.0);
    auto planning = parse_planning_json(c.str());
    const auto vr = validate_planning(planning);
    for (const auto& e : vr.errors) {
      CHECK_FALSE(e.contains("urcost"));
    }
  }
}

// ─── Phase 2: usage accounting outputs (`output_usage_metrics`) ─────────

namespace
{

/// Chronological 4-block case: g1 (10 $/MWh) follows the load
/// @p lmax; `peak` (200 $/MWh) only backs it up.
SsccCase usage_case(std::string_view lmax,
                    bool usage,
                    bool chronological = true)
{
  SsccCase c;
  c.nblocks = 4;
  c.chronological = chronological;
  c.demands = std::format(
      R"({{"uid": 1, "name": "d1", "bus": 1, "lmax": [{}], "capacity": 200}})",
      lmax);
  c.generators = R"(
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100},
      {"uid": 2, "name": "peak", "bus": 1, "gcost": 200, "capacity": 100})";
  if (usage) {
    c.options = R"("output_usage_metrics": true)";
  }
  return c;
}

}  // namespace

TEST_CASE("SSCC usage metrics: operating hours and ramp mileage")  // NOLINT
{
  const SsccSolved s(usage_case("[50, 80, 0, 30]", /*usage=*/true));
  const auto& g1 = s.generator("g1");
  const auto on = s.output_of(g1, "Generator", "usage_on");
  const auto ramp = s.output_of(g1, "Generator", "usage_ramp");
  REQUIRE(on.size() == 4);
  REQUIRE(ramp.size() == 4);
  CHECK(on[0] == doctest::Approx(1.0));
  CHECK(on[1] == doctest::Approx(1.0));
  CHECK(on[2] == doctest::Approx(0.0));
  CHECK(on[3] == doctest::Approx(1.0));
  // |Δp|: the first block has no predecessor in the cell.
  CHECK(ramp[0] == doctest::Approx(0.0));
  CHECK(ramp[1] == doctest::Approx(30.0));
  CHECK(ramp[2] == doctest::Approx(80.0));
  CHECK(ramp[3] == doctest::Approx(30.0));

  SUBCASE("invariants: indicator in {0,1}, mileage = Σ|Δp| ≥ |p_T − p_0|")
  {
    double total = 0.0;
    for (std::size_t b = 0; b < on.size(); ++b) {
      CHECK((on[b] == 0.0 || on[b] == 1.0));
      CHECK(ramp[b] >= 0.0);
      total += ramp[b];
      const double p = s.gen("g1", static_cast<int>(b));
      CHECK(on[b] == doctest::Approx(p > 1e-6 ? 1.0 : 0.0));
    }
    CHECK(total == doctest::Approx(140.0));
    CHECK(total >= std::abs(s.gen("g1", 3) - s.gen("g1", 0)));
  }
  SUBCASE("the backstop unit never runs: all-zero indicator")
  {
    const auto pon = s.output_of(s.generator("peak"), "Generator", "usage_on");
    for (const auto v : pon) {
      CHECK(v == doctest::Approx(0.0));
    }
  }
}

TEST_CASE("SSCC usage metrics: off by default, no LP change")  // NOLINT
{
  const SsccSolved off(usage_case("[50, 80, 0, 30]", /*usage=*/false));
  const SsccSolved on(usage_case("[50, 80, 0, 30]", /*usage=*/true));
  CHECK(off.output_of(off.generator("g1"), "Generator", "usage_on").empty());
  CHECK(off.output_of(off.generator("g1"), "Generator", "usage_ramp").empty());
  // Output-only option: identical objective and LP size.
  CHECK(on.obj() == doctest::Approx(off.obj()));
  CHECK(on.li().get_numcols() == off.li().get_numcols());
  CHECK(on.li().get_numrows() == off.li().get_numrows());
}

TEST_CASE("SSCC usage metrics: no ramp mileage on non-chronological stages")
// NOLINT
{
  const SsccSolved s(
      usage_case("[50, 80, 0, 30]", /*usage=*/true, /*chronological=*/false));
  const auto& g1 = s.generator("g1");
  CHECK(s.output_of(g1, "Generator", "usage_ramp").empty());
  CHECK(s.output_of(g1, "Generator", "usage_on").size() == 4);
}

TEST_CASE("SSCC usage metrics: hours at the committed minimum")  // NOLINT
{
  auto c = usage_case("[40, 80, 60, 40]", /*usage=*/true);
  c.commitments =
      R"({"uid": 1, "name": "uc_g1", "generator": "g1", "pmin": 40,
          "initial_status": 1})";
  const SsccSolved s(c);
  const auto& uc = s.sys().template elements<CommitmentLP>().front();
  const auto pmin = s.output_of(uc, "Commitment", "usage_pmin");
  const auto at_pmin = s.output_of(uc, "Commitment", "usage_at_pmin");
  REQUIRE(pmin.size() == 4);
  REQUIRE(at_pmin.size() == 4);
  for (const auto v : pmin) {
    CHECK(v == doctest::Approx(40.0));
  }
  CHECK(at_pmin[0] == doctest::Approx(1.0));
  CHECK(at_pmin[1] == doctest::Approx(0.0));
  CHECK(at_pmin[2] == doctest::Approx(0.0));
  CHECK(at_pmin[3] == doctest::Approx(1.0));
  // at_pmin implies on.
  const auto on = s.output_of(s.generator("g1"), "Generator", "usage_on");
  REQUIRE(on.size() == 4);
  for (std::size_t b = 0; b < at_pmin.size(); ++b) {
    CHECK(at_pmin[b] <= on[b] + 1e-12);
  }
}

// ─── Phase 3: known SSCC variable-cost adders ───────────────────────────

namespace
{

/// g1 (10 $/MWh, 100 MW) serves 50 MW and holds 10 MW of up and 10 MW of
/// down reserve through ONE provision (`urcost` = `drcost` = 1) carrying
/// the extra JSON members @p extra.  Headroom never binds, so each zone
/// price equals the provision's full objective coefficient.
SsccCase adder_case(std::string_view extra)
{
  SsccCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 50})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100})";
  c.reserve_zones =
      R"({"uid": 1, "name": "cpf", "urreq": 10, "urcost": 1000,
          "drreq": 10, "drcost": 1000})";
  c.reserve_provisions = std::format(
      R"({{"uid": 1, "name": "rp", "generator": "g1",
          "reserve_zones": ["cpf"], "urmax": 100, "drmax": 100,
          "ur_provision_factor": 1, "dr_provision_factor": 1,
          "urcost": 1, "drcost": 1{}}})",
      extra);
  return c;
}

/// Sum of one ReserveProvision `$`/h stream (0 when absent).
double stream_sum(const SsccSolved& s, std::string_view fname)
{
  double total = 0.0;
  for (const auto v : s.output_of(s.provision("rp"), "ReserveProvision", fname))
  {
    total += v;
  }
  return total;
}

}  // namespace

TEST_CASE("SSCC known costs: off by default")  // NOLINT
{
  const SsccSolved s(adder_case(""));
  CHECK(s.umu("cpf") == doctest::Approx(1.0));
  CHECK(s.dmu("cpf") == doctest::Approx(1.0));
  CHECK(s.obj() == doctest::Approx((50.0 * 10.0) + 10.0 + 10.0));
  for (const auto* f : {"up_partload_cost",
                        "up_activation_cost",
                        "up_degradation_cost",
                        "dn_partload_cost",
                        "dn_activation_cost",
                        "dn_degradation_cost"})
  {
    CHECK(s.output_of(s.provision("rp"), "ReserveProvision", f).empty());
  }
}

TEST_CASE("SSCC known costs: part-load penalty")  // NOLINT
{
  const SsccSolved s(adder_case(R"(, "ur_partload_cost": 2,
      "dr_partload_cost": 0.5)"));
  CHECK(s.umu("cpf") == doctest::Approx(1.0 + 2.0));
  CHECK(s.dmu("cpf") == doctest::Approx(1.0 + 0.5));
  CHECK(stream_sum(s, "up_partload_cost") == doctest::Approx(2.0 * 10.0));
  CHECK(stream_sum(s, "dn_partload_cost") == doctest::Approx(0.5 * 10.0));
  CHECK(s.obj() == doctest::Approx(500.0 + (10.0 * 3.0) + (10.0 * 1.5)));
}

TEST_CASE("SSCC known costs: activation energy defaults to the SRMC (up)")
// NOLINT
{
  // γ = 0.1, no ur_activation_cost: the deployed energy costs the
  // generator SRMC (10 $/MWh) → +1 $/MW·h.  Down: no default (0).
  const SsccSolved s(adder_case(R"(, "ur_activation_factor": 0.1,
      "dr_activation_factor": 0.2)"));
  CHECK(s.umu("cpf") == doctest::Approx(1.0 + (0.1 * 10.0)));
  CHECK(s.dmu("cpf") == doctest::Approx(1.0));
  CHECK(stream_sum(s, "up_activation_cost") == doctest::Approx(1.0 * 10.0));
  CHECK(stream_sum(s, "dn_activation_cost") == doctest::Approx(0.0));

  SUBCASE("explicit activation costs override the default")
  {
    const SsccSolved t(adder_case(R"(, "ur_activation_factor": 0.1,
        "ur_activation_cost": 50, "dr_activation_factor": 0.2,
        "dr_activation_cost": 30)"));
    CHECK(t.umu("cpf") == doctest::Approx(1.0 + 5.0));
    CHECK(t.dmu("cpf") == doctest::Approx(1.0 + 6.0));
    CHECK(stream_sum(t, "up_activation_cost") == doctest::Approx(50.0));
    CHECK(stream_sum(t, "dn_activation_cost") == doctest::Approx(60.0));
  }
  SUBCASE("a fuel-priced unit uses its fuel SRMC")
  {
    // Fuel price 8 × heat rate 2 + VOM 4 = SRMC 20 → γ 0.5 → +10 $/MW·h.
    auto c = adder_case(R"(, "ur_activation_factor": 0.5)");
    c.generators =
        R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 4, "capacity": 100,
            "fuel": "gas", "heat_rate": 2})";
    c.fuels = R"({"uid": 1, "name": "gas", "price": 8})";
    const SsccSolved t(c);
    const auto& g = t.generator("g1");
    CHECK(g.srmc_at(t.scenario(), t.stage(), t.buid(0)).value_or(-1.0)
          == doctest::Approx(20.0));
    CHECK(t.umu("cpf") == doctest::Approx(1.0 + 10.0));
    CHECK(stream_sum(t, "up_activation_cost") == doctest::Approx(100.0));
  }
}

TEST_CASE("SSCC known costs: degradation of deployed throughput")  // NOLINT
{
  // γ = 0.1 of deployed energy at 20 $/MWh of degradation → +2 $/MW·h on
  // top of the activation energy cost (SRMC 10 → +1).
  const SsccSolved s(adder_case(R"(, "ur_activation_factor": 0.1,
      "ur_degradation_cost": 20)"));
  CHECK(s.umu("cpf") == doctest::Approx(1.0 + 1.0 + 2.0));
  CHECK(stream_sum(s, "up_degradation_cost") == doctest::Approx(20.0));
  CHECK(stream_sum(s, "up_activation_cost") == doctest::Approx(10.0));

  SUBCASE("without γ the degradation cost has nothing to apply to")
  {
    const SsccSolved t(adder_case(R"(, "ur_degradation_cost": 20)"));
    CHECK(t.umu("cpf") == doctest::Approx(1.0));
    CHECK(stream_sum(t, "up_degradation_cost") == doctest::Approx(0.0));
  }
}

TEST_CASE("SSCC known costs: terms add up to the column cost (invariant)")
// NOLINT
{
  const SsccSolved base(adder_case(""));
  const SsccSolved s(adder_case(R"(, "ur_partload_cost": 0.7,
      "ur_activation_factor": 0.3, "ur_activation_cost": 12,
      "ur_degradation_cost": 4, "dr_partload_cost": 0.2,
      "dr_activation_factor": 0.05, "dr_activation_cost": 8)"));
  const double known = stream_sum(s, "up_partload_cost")
      + stream_sum(s, "up_activation_cost")
      + stream_sum(s, "up_degradation_cost") + stream_sum(s, "dn_partload_cost")
      + stream_sum(s, "dn_activation_cost")
      + stream_sum(s, "dn_degradation_cost");
  // Same dispatch and reserve: the objective grows by the known costs.
  CHECK(s.obj() - base.obj() == doctest::Approx(known));
  CHECK(known
        == doctest::Approx((10.0 * (0.7 + (0.3 * (12.0 + 4.0))))
                           + (10.0 * (0.2 + (0.05 * 8.0)))));
}

TEST_CASE("SSCC known costs: guard rails")  // NOLINT
{
  const auto errors_of = [](std::string_view extra)
  {
    auto planning = parse_planning_json(adder_case(extra).str());
    return validate_planning(planning).errors;
  };
  const auto has = [](const auto& errs, std::string_view needle)
  {
    return std::ranges::any_of(
        errs, [&](const auto& e) { return e.contains(needle); });
  };
  CHECK(has(errors_of(R"(, "ur_activation_factor": 1.5)"),
            "ur_activation_factor"));
  CHECK(has(errors_of(R"(, "dr_activation_factor": -0.1)"),
            "dr_activation_factor"));
  CHECK(has(errors_of(R"(, "ur_partload_cost": -1)"), "ur_partload_cost"));
  CHECK(
      has(errors_of(R"(, "dr_degradation_cost": -1)"), "dr_degradation_cost"));
  CHECK(has(errors_of(R"(, "ur_activation_cost": -3)"), "ur_activation_cost"));
  CHECK(errors_of(R"(, "ur_activation_factor": 1.0)").empty());
  CHECK_THROWS_WITH(PlanningLP(parse_planning_json(
                        adder_case(R"(, "ur_activation_factor": 2)").str())),
                    doctest::Contains("activation factor"));
}

// ─── Phase 4: maintenance calendar (Generator.outage_fraction) ──────────

namespace
{

/// g1 (10 $/MWh, 100 MW, extra members @p outage) and a backup g2
/// (1000 $/MWh) serve @p load MW.
SsccCase outage_case(double load, std::string_view outage)
{
  SsccCase c;
  c.demands = std::format(
      R"({{"uid": 1, "name": "d1", "bus": 1, "capacity": {}}})", load);
  c.generators = std::format(
      R"({{"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100{}}},
         {{"uid": 2, "name": "g2", "bus": 1, "gcost": 1000, "capacity": 200}})",
      outage);
  return c;
}

/// g1 alone serves 20 MW; zone `cpf` asks 40 MW of up reserve (shortage
/// 1000 $/MW·h) offered by g1 with urmax 40 (and @p urmin).  g1 carries
/// the extra members @p outage; @p mode is `outage_reserve_mode`.
SsccCase outage_reserve_case(std::string_view outage,
                             std::string_view mode,
                             double urmin = 0.0)
{
  SsccCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 20})";
  c.generators = std::format(
      R"({{"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100{}}})",
      outage);
  c.reserve_zones = R"({"uid": 1, "name": "cpf", "urreq": 40, "urcost": 1000})";
  c.reserve_provisions = std::format(
      R"({{"uid": 1, "name": "rp", "generator": "g1",
          "reserve_zones": ["cpf"], "urmax": 40, "urmin": {},
          "ur_provision_factor": 1, "urcost": 1}})",
      urmin);
  if (!mode.empty()) {
    c.model_options = std::format(R"("outage_reserve_mode": "{}")", mode);
  }
  return c;
}

}  // namespace

TEST_CASE("SSCC outage: unset is the legacy model")  // NOLINT
{
  const SsccSolved a(outage_case(90.0, ""));
  const SsccSolved b(outage_case(90.0, R"(, "outage_fraction": 0)"));
  CHECK(a.gen("g1") == doctest::Approx(90.0));
  CHECK(b.gen("g1") == doctest::Approx(90.0));
  CHECK(a.obj() == doctest::Approx(b.obj()));
  CHECK(a.output_of(a.generator("g1"), "Generator", "outage_fraction").empty());
  const auto f = b.output_of(b.generator("g1"), "Generator", "outage_fraction");
  REQUIRE(f.size() == 1);
  CHECK(f.front() == doctest::Approx(0.0));
}

TEST_CASE("SSCC outage: available output scales by 1 - f")  // NOLINT
{
  const SsccSolved s(outage_case(90.0, R"(, "outage_fraction": 0.25)"));
  CHECK(s.gen("g1") == doctest::Approx(75.0));
  CHECK(s.gen("g2") == doctest::Approx(15.0));
  CHECK(s.obj() == doctest::Approx((75.0 * 10.0) + (15.0 * 1000.0)));
  const auto f = s.output_of(s.generator("g1"), "Generator", "outage_fraction");
  REQUIRE(f.size() == 1);
  CHECK(f.front() == doctest::Approx(0.25));
  CHECK(s.generator("g1").available_fraction_at(
            s.scenario(), s.stage(), s.buid(0))
        == doctest::Approx(0.75));

  SUBCASE("pmin scales too")
  {
    // pmin 40 at f = 0.5 → floor 20: g1 serves a 25 MW load alone.
    const SsccSolved t(
        outage_case(25.0, R"(, "pmin": 40, "outage_fraction": 0.5)"));
    CHECK(t.gen("g1") == doctest::Approx(25.0));
    CHECK(t.gen("g2") == doctest::Approx(0.0));
  }
  SUBCASE("a full outage removes the unit")
  {
    const SsccSolved t(outage_case(90.0, R"(, "outage_fraction": 1)"));
    CHECK(t.gen("g1") == doctest::Approx(0.0));
    CHECK(t.gen("g2") == doctest::Approx(90.0));
  }
}

TEST_CASE("SSCC outage: per-block schedule (monthly calendar)")  // NOLINT
{
  auto c = outage_case(90.0, R"(, "outage_fraction": [[0, 0.5, 0, 1]])");
  c.nblocks = 4;
  const SsccSolved s(c);
  CHECK(s.gen("g1", 0) == doctest::Approx(90.0));
  CHECK(s.gen("g1", 1) == doctest::Approx(50.0));
  CHECK(s.gen("g1", 2) == doctest::Approx(90.0));
  CHECK(s.gen("g1", 3) == doctest::Approx(0.0));
  for (int b = 0; b < 4; ++b) {
    // Invariant: the load is always served.
    CHECK(s.gen("g1", b) + s.gen("g2", b) == doctest::Approx(90.0));
  }
}

TEST_CASE("SSCC outage: expansion capacity row is derated")  // NOLINT
{
  // g1 has no installed capacity; one cheap 100 MW module.  At f = 0.5
  // the capacity row is gen ≤ 0.5·capacity → g1 ≤ 50.
  auto c = outage_case(80.0, "");
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 0,
          "expcap": 100, "expmod": 1, "annual_capcost": 1,
          "outage_fraction": 0.5},
         {"uid": 2, "name": "g2", "bus": 1, "gcost": 1000, "capacity": 200})";
  const SsccSolved s(c);
  CHECK(s.gen("g1") == doctest::Approx(50.0));
  CHECK(s.gen("g2") == doctest::Approx(30.0));
}

TEST_CASE("SSCC outage: outage_reserve_mode")  // NOLINT
{
  // Without outage: up = urmax = 40 (headroom 80).
  const SsccSolved base(outage_reserve_case("", ""));
  CHECK(base.up("rp") == doctest::Approx(40.0));

  // f = 0.5 → available 50, gen 20, headroom 30.
  const std::string_view half = R"(, "outage_fraction": 0.5)";
  SUBCASE("none (default): only the headroom shrinks")
  {
    const SsccSolved s(outage_reserve_case(half, ""));
    CHECK(s.up("rp") == doctest::Approx(30.0));
    const SsccSolved t(outage_reserve_case(half, "none"));
    CHECK(t.up("rp") == doctest::Approx(30.0));
    CHECK(s.obj() == doctest::Approx(t.obj()));
  }
  SUBCASE("proportional: urmax scales by 1 - f")
  {
    const SsccSolved s(outage_reserve_case(half, "proportional"));
    CHECK(s.up("rp") == doctest::Approx(20.0));
  }
  SUBCASE("exclude: no reserve while in maintenance")
  {
    const SsccSolved s(outage_reserve_case(half, "exclude"));
    CHECK(s.up("rp") == doctest::Approx(0.0));
    CHECK(s.gen("g1") == doctest::Approx(20.0));
  }
  SUBCASE("exclude clamps a provision floor (no infeasibility)")
  {
    const SsccSolved s(outage_reserve_case(half, "exclude", 10.0));
    CHECK(s.up("rp") == doctest::Approx(0.0));
  }
  SUBCASE("modes are inert without an outage")
  {
    for (const auto* mode : {"none", "proportional", "exclude"}) {
      const SsccSolved s(outage_reserve_case("", mode));
      CHECK(s.up("rp") == doctest::Approx(40.0));
      CHECK(s.obj() == doctest::Approx(base.obj()));
    }
  }
}

TEST_CASE("SSCC outage: guard rails")  // NOLINT
{
  const auto errors_of = [](const SsccCase& c)
  {
    auto planning = parse_planning_json(c.str());
    return validate_planning(planning).errors;
  };
  const auto has = [](const auto& errs, std::string_view needle)
  {
    return std::ranges::any_of(
        errs, [&](const auto& e) { return e.contains(needle); });
  };
  CHECK(has(errors_of(outage_case(10.0, R"(, "outage_fraction": 1.5)")),
            "outage_fraction"));
  CHECK(has(errors_of(outage_case(10.0, R"(, "outage_fraction": -0.1)")),
            "outage_fraction"));
  CHECK(errors_of(outage_case(10.0, R"(, "outage_fraction": 1)")).empty());
  CHECK(
      has(errors_of(outage_reserve_case("", "bogus")), "outage_reserve_mode"));
  CHECK_THROWS_WITH(PlanningLP(parse_planning_json(
                        outage_case(10.0, R"(, "outage_fraction": 2)").str())),
                    doctest::Contains("outage_fraction"));
}

// ─── Operation wear adders on Commitment ────────────────────────────────

namespace
{

/// Chronological 4-block case: g1 (10 $/MWh, 100 MW, committed floor
/// 40 MW) and the backstop `peak` (200 $/MWh); g1's commitment carries
/// the extra JSON members @p wear.
SsccCase wear_case(std::string_view lmax,
                   std::string_view wear,
                   double initial_status = 1.0)
{
  auto c = usage_case(lmax, /*usage=*/false);
  c.commitments = std::format(
      R"({{"uid": 1, "name": "uc_g1", "generator": "g1", "pmin": 40,
          "initial_status": {}{}}})",
      initial_status,
      wear);
  return c;
}

double wear_sum(const SsccSolved& s, std::string_view fname)
{
  const auto& uc = s.sys().template elements<CommitmentLP>().front();
  double total = 0.0;
  for (const auto v : s.output_of(uc, "Commitment", fname)) {
    total += v;
  }
  return total;
}

}  // namespace

TEST_CASE("SSCC operation wear: off by default")  // NOLINT
{
  const SsccSolved s(wear_case("[40, 80, 60, 40]", ""));
  const auto& uc = s.sys().template elements<CommitmentLP>().front();
  CHECK(s.output_of(uc, "Commitment", "startup_wear_cost").empty());
  CHECK(s.output_of(uc, "Commitment", "operation_wear_cost").empty());
  // explicit zeros build the same LP
  const SsccSolved z(wear_case(
      "[40, 80, 60, 40]",
      R"(, "startup_wear_cost": 0, "wear_hour_cost": 0, "wear_load_cost": 0)"));
  CHECK(z.obj() == doctest::Approx(s.obj()));
  CHECK(z.li().get_numcols() == s.li().get_numcols());
}

TEST_CASE("SSCC operation wear: hourly and load-dependent terms")  // NOLINT
{
  const SsccSolved base(wear_case("[40, 80, 60, 40]", ""));

  SUBCASE("a per committed hour")
  {
    const SsccSolved s(
        wear_case("[40, 80, 60, 40]", R"(, "wear_hour_cost": 5)"));
    CHECK(s.obj() - base.obj() == doctest::Approx(4 * 5.0));
    CHECK(wear_sum(s, "operation_wear_cost") == doctest::Approx(20.0));
  }
  SUBCASE("b per MWh above the floor")
  {
    const SsccSolved s(
        wear_case("[40, 80, 60, 40]", R"(, "wear_load_cost": 1)"));
    const auto& uc = s.sys().template elements<CommitmentLP>().front();
    const auto w = s.output_of(uc, "Commitment", "operation_wear_cost");
    REQUIRE(w.size() == 4);
    CHECK(w[0] == doctest::Approx(0.0));
    CHECK(w[1] == doctest::Approx(40.0));
    CHECK(w[2] == doctest::Approx(20.0));
    CHECK(w[3] == doctest::Approx(0.0));
    CHECK(s.obj() - base.obj() == doctest::Approx(60.0));
  }
  SUBCASE("b < 0: low-load penalty, zero at Pmax")
  {
    // a + b·(Pmax − Pmin) = 30 − 0.5·60 = 0: valid
    const SsccSolved s(
        wear_case("[40, 80, 60, 40]",
                  R"(, "wear_hour_cost": 30, "wear_load_cost": -0.5)"));
    const auto& uc = s.sys().template elements<CommitmentLP>().front();
    const auto w = s.output_of(uc, "Commitment", "operation_wear_cost");
    REQUIRE(w.size() == 4);
    CHECK(w[0] == doctest::Approx(30.0));
    CHECK(w[1] == doctest::Approx(10.0));
    CHECK(w[2] == doctest::Approx(20.0));
    CHECK(w[3] == doctest::Approx(30.0));
    for (const auto v : w) {
      CHECK(v >= -1e-9);
    }
    // invariant: the stream is exactly the objective increase
    CHECK(s.obj() - base.obj()
          == doctest::Approx(wear_sum(s, "operation_wear_cost")));
  }
  SUBCASE("relaxed commitment: same linear cost, stream = objective delta")
  {
    const SsccSolved rb(wear_case("[40, 80, 60, 40]", R"(, "relax": true)"));
    const SsccSolved r(wear_case(
        "[40, 80, 60, 40]",
        R"(, "relax": true, "wear_hour_cost": 5, "wear_load_cost": 1)"));
    CHECK(r.obj() - rb.obj()
          == doctest::Approx(wear_sum(r, "operation_wear_cost")));
    CHECK(r.obj() - rb.obj() == doctest::Approx(20.0 + 60.0));
  }
  SUBCASE("non-chronological stage: the hourly terms still apply")
  {
    auto c = wear_case("[40, 80, 60, 40]", R"(, "wear_hour_cost": 5)");
    c.chronological = false;
    auto cb = wear_case("[40, 80, 60, 40]", "");
    cb.chronological = false;
    const SsccSolved s(c);
    const SsccSolved b(cb);
    CHECK(s.obj() - b.obj() == doctest::Approx(20.0));
  }
}

TEST_CASE("SSCC operation wear: start wear is split and steers dispatch")
// NOLINT
{
  // g1 starts offline; serving 2 x 50 MWh saves 2·50·190 = 19 000 $
  // against the backstop.
  const SsccSolved base(wear_case("[0, 50, 50, 0]", "", 0.0));
  REQUIRE(base.gen("g1", 1) == doctest::Approx(50.0));

  SUBCASE("a modest start wear is paid once and reported apart")
  {
    const SsccSolved s(
        wear_case("[0, 50, 50, 0]",
                  R"(, "startup_cost": 50, "startup_wear_cost": 100)",
                  0.0));
    const SsccSolved c(
        wear_case("[0, 50, 50, 0]", R"(, "startup_cost": 50)", 0.0));
    CHECK(s.obj() - c.obj() == doctest::Approx(100.0));
    CHECK(wear_sum(s, "startup_wear_cost") == doctest::Approx(100.0));
    CHECK(s.gen("g1", 1) == doctest::Approx(50.0));
  }
  SUBCASE("a start wear above the saving keeps the unit off")
  {
    const SsccSolved s(
        wear_case("[0, 50, 50, 0]", R"(, "startup_wear_cost": 30000)", 0.0));
    CHECK(s.gen("g1", 1) == doctest::Approx(0.0));
    CHECK(s.gen("peak", 1) == doctest::Approx(50.0));
    CHECK(wear_sum(s, "startup_wear_cost") == doctest::Approx(0.0));
  }
  SUBCASE("with hot / warm / cold tiers the wear still lands on v")
  {
    const std::string tiers =
        R"(, "hot_start_cost": 10, "warm_start_cost": 20,
            "cold_start_cost": 30, "hot_start_time": 1,
            "cold_start_time": 5, "initial_hours": 10)";
    const SsccSolved c(wear_case("[0, 50, 50, 0]", tiers, 0.0));
    const SsccSolved s(wear_case(
        "[0, 50, 50, 0]", tiers + R"(, "startup_wear_cost": 100)", 0.0));
    CHECK(s.obj() - c.obj() == doctest::Approx(100.0));
  }
}

TEST_CASE("SSCC operation wear: expansion-compatible")  // NOLINT
{
  // g1 built by expansion (one 100 MW module); the wear terms use the
  // expanded upper bound for the Pmax check and stay linear.
  auto c = wear_case("[40, 80, 60, 40]",
                     R"(, "wear_hour_cost": 5, "wear_load_cost": 1)");
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 0,
          "expcap": 100, "expmod": 1, "annual_capcost": 1},
         {"uid": 2, "name": "peak", "bus": 1, "gcost": 200, "capacity": 100})";
  const SsccSolved s(c);
  CHECK(s.gen("g1", 1) == doctest::Approx(80.0));
  CHECK(wear_sum(s, "operation_wear_cost") == doctest::Approx(20.0 + 60.0));
}

TEST_CASE("SSCC operation wear: guard rails")  // NOLINT
{
  const auto errors_of = [](std::string_view wear)
  {
    auto planning =
        parse_planning_json(wear_case("[40, 80, 60, 40]", wear).str());
    return validate_planning(planning).errors;
  };
  const auto has = [](const auto& errs, std::string_view needle)
  {
    return std::ranges::any_of(
        errs, [&](const auto& e) { return e.contains(needle); });
  };
  CHECK(has(errors_of(R"(, "startup_wear_cost": -1)"), "startup_wear_cost"));
  CHECK(has(errors_of(R"(, "wear_hour_cost": -1)"), "wear_hour_cost"));
  // a negative b alone passes validation; the LP checks it at Pmax
  CHECK(errors_of(R"(, "wear_load_cost": -0.1, "wear_hour_cost": 10)").empty());
  // 10 − 0.5·60 < 0 at Pmax
  CHECK_THROWS_WITH(
      PlanningLP(parse_planning_json(
          wear_case("[40, 80, 60, 40]",
                    R"(, "wear_hour_cost": 10, "wear_load_cost": -0.5)")
              .str())),
      doctest::Contains("wear_load_cost"));
  CHECK_THROWS_WITH(
      PlanningLP(parse_planning_json(
          wear_case("[0, 50, 50, 0]", R"(, "startup_wear_cost": -5)", 0.0)
              .str())),
      doctest::Contains("startup_wear_cost"));
}
