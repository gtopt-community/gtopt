// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Dynamic (plane-based) reserve requirement — the CEN (Chile) primary
// frequency reserve model, minuta GM-DPRO Nº05-2026 Anexo B:
//
//   (B.2) REQ_10s ≥ α_k − β_k·Gen[GW] − γ_k·Inercia[GVAs] + δ_k·PotDesc
//   (B.3) REQ_5m+ ≥ α − β·Gen[GW] + δ·MaxInj        (PLEXOS 2026 set)
//   (B.4) REQ_5m− ≥ α − β·Gen[GW] + δ·MaxLoad       (PLEXOS 2026-07 set)
//
// gtopt keeps the load term a PARAMETER (`load_term: "exogenous"`, the
// default): D = 1.05 (default factor) · Σ lmax (or a given `system_load`) goes
// to the row RHS, so no bus balance reaches the requirement rows and a marginal
// MWh of demand never changes REQ.  The PLEXOS-compat form
// (`load_term: "endogenous_generation"`, D = Σ gen − Σ BESS charge as a
// column) is kept only to demonstrate the negative-LMP mechanism: every
// MWh of demand earns a `β·μ/1000` credit in the LMP.
//
// Every expected value is hand-computed (1 h blocks, probability 1, no
// discounting, scale_objective = 1).

#include <algorithm>
#include <array>
#include <cmath>
#include <format>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_reserve_provision.hpp>
#include <gtopt/json/json_reserve_zone.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/validate_planning.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

namespace
{

/// (α, β, γ, δ) of the twelve CEN (B.2) planes, β in /GW, γ in /GVAs —
/// identical to the PLEXOS PCP `CPF_Up10Calculation_I..XII` set active
/// since 2026-03 (verified against the 2026-11-07 PCP database).
constexpr std::array<std::array<double, 4>, 12> kRdrCpf10s {{
    {516.000, 36.600, 12.300, 1.250},
    {339.464, 23.500, 9.986, 1.250},
    {315.500, 26.733, 5.000, 1.000},
    {184.750, 17.071, 3.886, 1.000},
    {249.160, 20.640, 2.414, 0.732},
    {149.557, 13.286, 1.819, 0.730},
    {468.500, 36.600, 13.300, 1.450},
    {291.964, 23.500, 10.986, 1.450},
    {280.500, 26.733, 6.000, 1.200},
    {149.750, 17.071, 4.886, 1.200},
    {219.160, 20.640, 3.164, 0.928},
    {117.843, 13.286, 2.569, 0.930},
}};

/// (B.3) as used by PLEXOS (2026): REQ ≥ 4.125 − 10.75·Gen + 1.0912·MaxInj.
constexpr double kRdrUp5Alpha = 4.125;
constexpr double kRdrUp5Beta = 10.75;
constexpr double kRdrUp5Delta = 1.0912;
/// (B.4) as used by PLEXOS (2026-07): REQ ≥ −80 − 4·Gen + 0.96·MaxLoad.
constexpr double kRdrDn5Alpha = -80.0;
constexpr double kRdrDn5Beta = 4.0;
constexpr double kRdrDn5Delta = 0.96;

/// gtopt plane JSON from CEN coefficients (β, γ per GW / GVAs → per MW).
std::string rdr_cen_plane(double alpha, double beta, double gamma, double delta)
{
  return std::format(R"({{"alpha": {}, "demand_coef": {}, "inertia_coef": {},)"
                     R"( "contingency_coef": {}}})",
                     alpha,
                     -beta / 1000.0,
                     -gamma / 1000.0,
                     delta);
}

std::string rdr_cpf10s_planes_json()
{
  std::string out = "[";
  for (const auto& [a, b, g, d] : kRdrCpf10s) {
    if (out.size() > 1) {
      out += ",";
    }
    out += rdr_cen_plane(a, b, g, d);
  }
  return out + "]";
}

/// Single-bus case assembled from raw JSON array bodies.
struct RdrCase
{
  int nblocks {1};
  bool chronological {false};  ///< needed by CommitmentLP
  std::string generators;
  std::string demands;
  std::string batteries;
  std::string reserve_zones;
  std::string reserve_provisions;
  std::string inertia_zones;
  std::string inertia_provisions;
  std::string simple_commitments;
  std::string commitments;
  /// `options.constraint_mode` (empty = the default, strict).
  std::string constraint_mode;

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
      "scale_objective": 1.0
    }}
  }},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {},
                      "chronological": {}}}],
    "scenario_array": [{{"uid": 1}}]
  }},
  "system": {{
    "name": "rdr",
    "bus_array": [{{"uid": 1, "name": "b1"}}]{}{}{}{}{}{}{}{}{}
  }}
}})",
        constraint_mode.empty()
            ? std::string {}
            : std::format(R"( "constraint_mode": "{}",)", constraint_mode),
        blocks,
        nblocks,
        chronological,
        arr("demand_array", demands),
        arr("generator_array", generators),
        arr("battery_array", batteries),
        arr("reserve_zone_array", reserve_zones),
        arr("reserve_provision_array", reserve_provisions),
        arr("inertia_zone_array", inertia_zones),
        arr("inertia_provision_array", inertia_provisions),
        arr("simple_commitment_array", simple_commitments),
        arr("commitment_array", commitments));
  }
};

/// RdrSolved case with name-based accessors (block index is 0-based).
struct RdrSolved
{
  std::unique_ptr<PlanningLP> plp;

  explicit RdrSolved(const RdrCase& c)
      : plp(std::make_unique<PlanningLP>(
            solver_test::with_mip_solver(parse_planning_json(c.str()))))
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

  [[nodiscard]] double col(std::optional<ColIndex> c) const
  {
    REQUIRE(c.has_value());
    return li().get_col_sol()[c.value_or(ColIndex {0})];
  }
  [[nodiscard]] double dual(std::optional<RowIndex> r) const
  {
    REQUIRE(r.has_value());
    return li().get_row_dual()[r.value_or(RowIndex {0})];
  }

  [[nodiscard]] double lambda(int b = 0) const
  {
    const auto& bus = sys().template elements<BusLP>().front();
    return li()
        .get_row_dual()[bus.balance_rows_at(scenario(), stage()).at(buid(b))];
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

  [[nodiscard]] double gen(std::string_view name, int b = 0) const
  {
    for (const auto& g : sys().template elements<GeneratorLP>()) {
      if (g.object().name == name) {
        return col(g.lookup_generation_col(scenario(), stage(), buid(b)));
      }
    }
    FAIL("generator not found");
    return 0.0;
  }

  [[nodiscard]] double ureq(std::string_view z, int b = 0) const
  {
    return col(zone(z).lookup_urequirement_col(scenario(), stage(), buid(b)));
  }
  [[nodiscard]] double dreq(std::string_view z, int b = 0) const
  {
    return col(zone(z).lookup_drequirement_col(scenario(), stage(), buid(b)));
  }
  /// Reserve price μ: dual of the provision-balance row (≥ 0 convention).
  [[nodiscard]] double umu(std::string_view z, int b = 0) const
  {
    const auto& rows = zone(z).urequirement_rows();
    const auto it = rows.find({scenario().uid(), stage().uid()});
    REQUIRE(it != rows.end());
    return std::abs(dual(it->second.at(buid(b))));
  }
  [[nodiscard]] double dmu(std::string_view z, int b = 0) const
  {
    const auto& rows = zone(z).drequirement_rows();
    const auto it = rows.find({scenario().uid(), stage().uid()});
    REQUIRE(it != rows.end());
    return std::abs(dual(it->second.at(buid(b))));
  }
  /// Per-block values of one ReserveZone output field (`<fname>_<sname>`)
  /// of zone @p z, read back through `OutputContext` (1 scenario/stage).
  [[nodiscard]] std::vector<double> zone_output(std::string_view z,
                                                std::string_view fname,
                                                std::string_view sname) const
  {
    OutputContext oc(sys().system_context(),
                     sys().linear_interface(),
                     sys().scene().uid(),
                     sys().phase().uid(),
                     sys().phase().is_continuous());
    REQUIRE(zone(z).add_to_output(oc));
    const OutputContext::ClassFieldName key {"ReserveZone", fname, sname};
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

  [[nodiscard]] std::vector<double> uplane_duals(std::string_view z,
                                                 int b = 0) const
  {
    std::vector<double> out;
    for (const auto r : zone(z).urplane_rows(scenario(), stage(), buid(b))) {
      out.push_back(std::abs(dual(r)));
    }
    return out;
  }
};

/// Solar-surplus fixture: 1 000 MW demand, 3 000 MW free solar (curtails)
/// and a 60 $/MWh CCGT that sells up reserve at 20 $/MW.  Block 2 has a
/// 4 000 MW demand so the CCGT is the marginal energy unit there.
RdrCase rdr_surplus_case(std::string_view load_term)
{
  RdrCase c;
  c.nblocks = 2;
  c.demands =
      R"({"uid": 1, "name": "d1", "bus": 1, "lmax": [[1000, 4000]],
          "capacity": 4000})";
  c.generators = R"(
      {"uid": 1, "name": "solar", "bus": 1, "gcost": 0, "capacity": 3000},
      {"uid": 2, "name": "ccgt", "bus": 1, "gcost": 60, "capacity": 2000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_ccgt", "generator": "ccgt",
          "reserve_zones": [1], "urmax": 2000, "ur_provision_factor": 1,
          "urcost": 20})";
  c.reserve_zones = std::format(
      R"({{"uid": 1, "name": "cpf10s", "urcost": 5000,
          "urreq_planes": [{{"alpha": 516, "demand_coef": -0.0366}}],
          "demand_load_factor": 1.025, "load_term": "{}"}})",
      load_term);
  return c;
}

}  // namespace

TEST_CASE("Dynamic reserve requirement: exogenous load term keeps LMPs clean")
// NOLINT
{
  // R = 516 − 0.0366 · 1.025 · lmax: 478.485 (block 1), 365.94 (block 2).
  // μ = 20 (provision bid) in both blocks.
  const RdrSolved s(rdr_surplus_case("exogenous"));

  CHECK(s.ureq("cpf10s", 0) == doctest::Approx(516.0 - (0.0366 * 1025.0)));
  CHECK(s.ureq("cpf10s", 1) == doctest::Approx(516.0 - (0.0366 * 4100.0)));
  CHECK(s.umu("cpf10s", 0) == doctest::Approx(20.0));
  CHECK(s.gen("solar", 0) == doctest::Approx(1000.0));

  // Surplus hour: curtailed solar is marginal → λ = 0, NOT negative.
  CHECK(s.lambda(0) == doctest::Approx(0.0).epsilon(1e-9));
  // Thermal hour: λ = energy-only marginal cost 60, no −β·μ term.
  CHECK(s.lambda(1) == doctest::Approx(60.0));
}

TEST_CASE("Dynamic reserve requirement: default demand_load_factor 1.05")
// NOLINT
{
  // Without `demand_load_factor` the exogenous D is 1.05 · lmax (the
  // conservative end of the CEN 1.025–1.05 ratio).
  auto c = rdr_surplus_case("exogenous");
  c.reserve_zones =
      R"({"uid": 1, "name": "cpf10s", "urcost": 5000,
          "urreq_planes": [{"alpha": 516, "demand_coef": -0.0366}]})";
  const RdrSolved s(c);
  CHECK(s.ureq("cpf10s", 0) == doctest::Approx(516.0 - (0.0366 * 1050.0)));
  CHECK(s.ureq("cpf10s", 1) == doctest::Approx(516.0 - (0.0366 * 4200.0)));
}

TEST_CASE("Dynamic reserve requirement: PLEXOS endogenous load term")  // NOLINT
{
  // D = 1.025 · Σ gen is a column: each MWh of demand raises D by 1.025
  // and lowers R by 0.0366 · 1.025, saving μ · 0.0375 = 0.75030 $/MWh —
  // the credit lands in the LMP: λ = c_marginal − β·lf·μ/1000.
  const RdrSolved s(rdr_surplus_case("endogenous_generation"));
  const double credit = 0.0366 * 1.025 * 20.0;

  CHECK(s.ureq("cpf10s", 0) == doctest::Approx(516.0 - (0.0366 * 1025.0)));
  CHECK(s.umu("cpf10s", 0) == doctest::Approx(20.0));
  CHECK(s.lambda(0) == doctest::Approx(-credit));  // −0.7503 < 0
  CHECK(s.lambda(1) == doctest::Approx(60.0 - credit));
}

TEST_CASE("Dynamic reserve requirement: contingency term prices the big unit")
// NOLINT
{
  // 300 MW demand; `big` (10 $/MWh) can serve it all; `small` costs 35.
  // (B.3) PLEXOS 2026: R = 4.125 − 0.01075·D + 1.0912·C, C ≥ p_big, p_small.
  // Each MW on `big` raises R by 1.0912 → costs 10.912 at μ = 10, so the
  // effective marginal cost of `big` is 20.912 < 35: big = 300, C = 300
  // and λ = 10 + 1.0912·10 = 20.912 — a legitimate contingency cost
  // (the unit IS the largest contingency), not a demand credit.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 300})";
  c.generators = R"(
      {"uid": 1, "name": "big", "bus": 1, "gcost": 10, "capacity": 400},
      {"uid": 2, "name": "small", "bus": 1, "gcost": 35, "capacity": 1000},
      {"uid": 3, "name": "res", "bus": 1, "gcost": 100, "capacity": 1000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_res", "generator": "res",
          "reserve_zones": [1], "urmax": 1000, "ur_provision_factor": 1,
          "urcost": 10})";
  const auto zone = [](std::string_view units)
  {
    return std::format(
        R"({{"uid": 1, "name": "cpf5m", "urcost": 5000,
            "urreq_planes": [{}], "demand_load_factor": 1.025,
            "contingency_generators": [{}]}})",
        rdr_cen_plane(kRdrUp5Alpha, kRdrUp5Beta, 0.0, kRdrUp5Delta),
        units);
  };

  SUBCASE("with the contingency set")
  {
    c.reserve_zones = zone(R"("big", "small")");
    const RdrSolved s(c);
    const double d = 1.025 * 300.0;
    CHECK(s.gen("big") == doctest::Approx(300.0));
    CHECK(s.col(s.zone("cpf5m").lookup_urcontingency_col(
              s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(300.0));
    CHECK(s.ureq("cpf5m")
          == doctest::Approx(kRdrUp5Alpha - (kRdrUp5Beta * d / 1000.0)
                             + (kRdrUp5Delta * 300.0)));
    CHECK(s.lambda() == doctest::Approx(10.0 + (kRdrUp5Delta * 10.0)));
  }

  SUBCASE("without contingency units the LMP is the energy cost")
  {
    // contingency_coef without candidates: strict rejects the plane
    // (review P0-1); normal mode drops the term with a warning.
    c.reserve_zones = zone("");
    CHECK_THROWS(PlanningLP(parse_planning_json(c.str())));
    c.constraint_mode = "normal";
    const RdrSolved s(c);
    CHECK(s.lambda() == doctest::Approx(10.0));
  }
}

TEST_CASE("Dynamic reserve requirement: inertia lowers the requirement")
// NOLINT
{
  // 1 000 MW demand served by free solar; `sync` (80 $/MWh, relaxed
  // SimpleCommitment, dispatch_pmin 100) contributes I = 200·min(p, 100)
  // MWs.  Plane (B.2) k=1: R = 516 − 0.0366·D − 0.0123·I.  Up reserve
  // costs 100 $/MW, so committing `sync` at 100 MW (8 000 $) saves
  // 0.0123·20 000·100 = 24 600 $: I = 20 000 MWs, R = 478.485 − 246.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 1000})";
  c.generators = R"(
      {"uid": 1, "name": "solar", "bus": 1, "gcost": 0, "capacity": 3000},
      {"uid": 2, "name": "sync", "bus": 1, "gcost": 80, "capacity": 300},
      {"uid": 3, "name": "res", "bus": 1, "gcost": 500, "capacity": 1000})";
  c.simple_commitments =
      R"({"uid": 1, "name": "uc_sync", "generator": "sync",
          "dispatch_pmin": 100, "relax": true})";
  c.inertia_zones = R"({"uid": 1, "name": "sen"})";
  c.inertia_provisions =
      R"({"uid": 1, "name": "ip_sync", "generator": "sync",
          "inertia_zones": ["sen"], "provision_factor": 200,
          "provision_max": 100})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_res", "generator": "res",
          "reserve_zones": [1], "urmax": 1000, "ur_provision_factor": 1,
          "urcost": 100})";
  const auto zone = [](std::string_view iz)
  {
    return std::format(
        R"({{"uid": 1, "name": "cpf10s", "urcost": 5000,
            "urreq_planes": [{{"alpha": 516, "demand_coef": -0.0366,
                               "inertia_coef": -0.0123}}],
            "demand_load_factor": 1.025{}}})",
        iz);
  };
  const double r0 = 516.0 - (0.0366 * 1025.0);

  SUBCASE("inertia term active")
  {
    // The dispatch-based (PLEXOS) form; the default "commitment" term
    // would count the SimpleCommitment status instead.
    c.reserve_zones =
        zone(R"(, "inertia_zone": "sen", "inertia_term": "endogenous")");
    const RdrSolved s(c);
    CHECK(s.gen("sync") == doctest::Approx(100.0));
    CHECK(s.col(s.zone("cpf10s").lookup_inertia_col(
              s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(20000.0));
    CHECK(s.ureq("cpf10s") == doctest::Approx(r0 - (0.0123 * 20000.0)));
    CHECK(s.lambda() == doctest::Approx(0.0).epsilon(1e-9));
  }

  SUBCASE("no inertia reference: requirement stays at the demand plane")
  {
    // inertia_coef without a source: strict rejects it (review P0-1).
    c.reserve_zones = zone("");
    CHECK_THROWS(PlanningLP(parse_planning_json(c.str())));
    c.constraint_mode = "normal";
    const RdrSolved s(c);
    CHECK(s.gen("sync") == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(s.ureq("cpf10s") == doctest::Approx(r0));
  }
}

TEST_CASE("Dynamic reserve requirement: max of the 12 CEN planes and floors")
// NOLINT
{
  // D = 9 000 MW (factor 1), I = 30 GVAs (must-run `sync`, pf 30),
  // C = 340 MW (must-run `big`).  R must equal max_k plane_k and only
  // the binding plane carries the reserve price.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 9000})";
  c.generators = R"(
      {"uid": 1, "name": "sync", "bus": 1, "gcost": 0, "pmin": 1000,
       "pmax": 1000, "capacity": 1000},
      {"uid": 2, "name": "big", "bus": 1, "gcost": 0, "pmin": 340,
       "pmax": 340, "capacity": 340},
      {"uid": 3, "name": "flex", "bus": 1, "gcost": 50, "capacity": 10000},
      {"uid": 4, "name": "res", "bus": 1, "gcost": 100, "capacity": 2000})";
  c.inertia_zones = R"({"uid": 1, "name": "sen"})";
  c.inertia_provisions =
      R"({"uid": 1, "name": "ip_sync", "generator": "sync",
          "inertia_zones": ["sen"], "provision_factor": 30,
          "provision_max": 1000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_res", "generator": "res",
          "reserve_zones": [1], "urmax": 2000, "ur_provision_factor": 1,
          "urcost": 10},
         {"uid": 2, "name": "rp_flex", "generator": "flex",
          "reserve_zones": [1], "drmax": 2000, "dr_provision_factor": 1,
          "drcost": 1})";

  double expected = -1e30;
  int binding = -1;
  for (std::size_t k = 0; k < kRdrCpf10s.size(); ++k) {
    const auto& [a, b, g, d] = kRdrCpf10s[k];
    const double v = a - (b * 9.0) - (g * 30.0) + (d * 340.0);
    if (v > expected) {
      expected = v;
      binding = static_cast<int>(k);
    }
  }

  SUBCASE("up: max of the planes")
  {
    c.reserve_zones = std::format(
        R"({{"uid": 1, "name": "cpf10s", "urcost": 5000,
            "urreq_planes": {}, "inertia_zone": "sen", "demand_load_factor": 1,
            "contingency_generators": ["big"]}})",
        rdr_cpf10s_planes_json());
    const RdrSolved s(c);
    CHECK(s.ureq("cpf10s") == doctest::Approx(expected));
    const auto duals = s.uplane_duals("cpf10s");
    REQUIRE(duals.size() == 12);
    for (int k = 0; k < 12; ++k) {
      CAPTURE(k);
      CHECK(duals[static_cast<std::size_t>(k)]
            == doctest::Approx(k == binding ? 10.0 : 0.0));
    }
  }

  SUBCASE("down: negative plane is floored at 0, then at drmin")
  {
    // (B.4) without a load contingency: −80 − 4·9 = −116 < 0.
    const auto zone = [](std::string_view extra)
    {
      return std::format(
          R"({{"uid": 1, "name": "cpf5m_dn", "drcost": 5000, "demand_load_factor": 1,
              "drreq_planes": [{}]{}}})",
          rdr_cen_plane(kRdrDn5Alpha, kRdrDn5Beta, 0.0, kRdrDn5Delta),
          extra);
    };
    // No contingency candidates: the δ·C term is dropped (normal mode).
    c.constraint_mode = "normal";
    {
      c.reserve_zones = zone("");
      const RdrSolved s(c);
      CHECK(s.dreq("cpf5m_dn") == doctest::Approx(0.0).epsilon(1e-9));
    }
    {
      c.reserve_zones = zone(R"(, "drmin": 25)");
      const RdrSolved s(c);
      CHECK(s.dreq("cpf5m_dn") == doctest::Approx(25.0));
    }
    {
      // MaxLoad ≥ load of `d1` (9 000): −116 + 0.96·9 000 = 8 524.
      c.reserve_zones = zone(R"(, "contingency_demands": ["d1"])");
      const RdrSolved s(c);
      CHECK(s.dreq("cpf5m_dn")
            == doctest::Approx(kRdrDn5Alpha - (kRdrDn5Beta * 9.0)
                               + (kRdrDn5Delta * 9000.0)));
    }
  }
}

TEST_CASE(
    "Dynamic reserve requirement: reproduces PLEXOS PCP 2026-11-07 h14 REQ")
// NOLINT
{
  // PLEXOS PCP of 2026-11-07, hour 14 (solution DB): Generation_SEN =
  // 9.64295728645983 GW, Inertia_SEN = 37.6974293818 GVAs,
  // MaxInjection_SEN = 300 MW, MaxLoad_SEN = 200 MW (a BESS charging).
  // PLEXOS requirements: CPF10s = 179.254695043454 (plane XI binding),
  // CPF5mUp = 227.823209170557, CPF5mDown = 73.4281708541607.
  // Feed gtopt the same Gen (exogenous `system_load`), inertia (must-run
  // unit, pf = 37.697 MWs/MW), MaxInj (must-run 300 MW unit) and MaxLoad
  // (BESS forced to charge 200 MW).  Each requirement has its own
  // provider so every REQ binds (a non-binding REQ column is degenerate:
  // any value above its planes is optimal).
  constexpr double kGen = 9642.95728645983;  // [MW]
  RdrCase c;
  c.demands =
      R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 9642.95728645983})";
  c.generators = R"(
      {"uid": 1, "name": "sync", "bus": 1, "gcost": 0, "pmin": 1000,
       "pmax": 1000, "capacity": 1000},
      {"uid": 2, "name": "big", "bus": 1, "gcost": 0, "pmin": 300,
       "pmax": 300, "capacity": 300},
      {"uid": 3, "name": "flex", "bus": 1, "gcost": 50, "capacity": 20000},
      {"uid": 4, "name": "res", "bus": 1, "gcost": 100, "capacity": 2000},
      {"uid": 5, "name": "res5", "bus": 1, "gcost": 100, "capacity": 2000})";
  c.batteries =
      R"({"uid": 1, "name": "bess", "bus": 1, "input_efficiency": 1.0,
          "output_efficiency": 1.0, "emin": 0, "emax": 10000,
          "capacity": 10000, "eini": 0, "pmax_charge": 200,
          "pmin_charge": 200, "pmax_discharge": 200,
          "use_state_variable": true, "daily_cycle": false})";
  c.inertia_zones = R"({"uid": 1, "name": "sen"})";
  c.inertia_provisions =
      R"({"uid": 1, "name": "ip_sync", "generator": "sync",
          "inertia_zones": ["sen"], "provision_factor": 37.6974293818,
          "provision_max": 1000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_res", "generator": "res",
          "reserve_zones": [1], "urmax": 2000, "ur_provision_factor": 1,
          "urcost": 10},
         {"uid": 2, "name": "rp_flex", "generator": "flex",
          "reserve_zones": [3], "drmax": 2000, "dr_provision_factor": 1,
          "drcost": 10},
         {"uid": 3, "name": "rp_res5", "generator": "res5",
          "reserve_zones": [2], "urmax": 2000, "ur_provision_factor": 1,
          "urcost": 12})";
  const auto zones = [&](std::string_view load)
  {
    return std::format(
        R"({{"uid": 1, "name": "cpf10s", "urcost": 5000,
            "urreq_planes": {0}, {1}, "inertia_zone": "sen",
            "contingency_generators": ["big"]}},
           {{"uid": 2, "name": "cpf5m_up", "urcost": 5000,
            "urreq_planes": [{2}], {1},
            "contingency_generators": ["big"]}},
           {{"uid": 3, "name": "cpf5m_dn", "drcost": 5000,
            "drreq_planes": [{3}], {1},
            "contingency_demands": ["bess_dem"]}})",
        rdr_cpf10s_planes_json(),
        load,
        rdr_cen_plane(kRdrUp5Alpha, kRdrUp5Beta, 0.0, kRdrUp5Delta),
        rdr_cen_plane(kRdrDn5Alpha, kRdrDn5Beta, 0.0, kRdrDn5Delta));
  };

  SUBCASE("exogenous load: same REQ as PLEXOS, energy-only LMP")
  {
    c.reserve_zones = zones(
        std::format(R"("system_load": {}, "load_term": "exogenous")", kGen));
    const RdrSolved s(c);
    CHECK(s.ureq("cpf10s") == doctest::Approx(179.254695043454));
    CHECK(s.ureq("cpf5m_up") == doctest::Approx(227.823209170557));
    CHECK(s.dreq("cpf5m_dn") == doctest::Approx(73.4281708541607));
    CHECK(s.uplane_duals("cpf10s")[10] == doctest::Approx(10.0));  // XI
    CHECK(s.lambda() == doctest::Approx(50.0));

    // Output: plane XI is reported as binding, its dual is the reserve
    // price, the other planes carry no dual; the requirement-row dual
    // is written too.
    const auto binding =
        s.zone_output("cpf10s", ReserveZoneLP::UrplaneBindingName, "sol");
    REQUIRE(binding.size() == 1);
    CHECK(binding[0] == doctest::Approx(11.0));
    const auto d11 = s.zone_output("cpf10s", "urplane11", "dual");
    REQUIRE(d11.size() == 1);
    CHECK(std::abs(d11[0]) == doctest::Approx(10.0));
    const auto d1 = s.zone_output("cpf10s", "urplane1", "dual");
    CHECK((d1.empty() || std::abs(d1[0]) < 1e-9));
    const auto req_dual =
        s.zone_output("cpf10s", ReserveZoneLP::UrequirementName, "dual");
    REQUIRE(req_dual.size() == 1);
    CHECK(std::abs(req_dual[0]) == doctest::Approx(10.0));
    const auto dn_binding =
        s.zone_output("cpf5m_dn", ReserveZoneLP::DrplaneBindingName, "sol");
    REQUIRE(dn_binding.size() == 1);
    CHECK(dn_binding[0] == doctest::Approx(1.0));
  }

  SUBCASE("PLEXOS-compat endogenous load: same REQ, LMP carries the credit")
  {
    // D = Σ gen − BESS charge = 9 642.957 + 200 − 200 at the optimum.
    c.reserve_zones = zones(R"("demand_load_factor": 1.0,
                 "load_term": "endogenous_generation")");
    const RdrSolved s(c);
    CHECK(s.ureq("cpf10s") == doctest::Approx(179.254695043454));
    CHECK(s.ureq("cpf5m_up") == doctest::Approx(227.823209170557));
    CHECK(s.dreq("cpf5m_dn") == doctest::Approx(73.4281708541607));
    // λ = 50 − Σ_req β_k·μ_k/1000 — the PLEXOS `Generation_Calculation`
    // dual (−0.366 $/MWh at this hour in the PCP itself).
    const double credit = ((kRdrCpf10s[10][1] * s.umu("cpf10s"))
                           + (kRdrUp5Beta * s.umu("cpf5m_up"))
                           + (kRdrDn5Beta * s.dmu("cpf5m_dn")))
        / 1000.0;
    CHECK(credit > 0.0);
    CHECK(s.lambda() == doctest::Approx(50.0 - credit));

    // The same credit read straight from the written plane duals:
    // Σ_k β_k·|urplane<k>_dual| / 1000 over the three requirements.
    double out_credit = 0.0;
    for (std::size_t k = 0; k < kRdrCpf10s.size(); ++k) {
      const auto d =
          s.zone_output("cpf10s", ReserveZoneLP::UrplaneNames.at(k), "dual");
      if (!d.empty()) {
        out_credit += kRdrCpf10s.at(k)[1] * std::abs(d[0]) / 1000.0;
      }
    }
    out_credit += kRdrUp5Beta
        * std::abs(s.zone_output("cpf5m_up", "urplane1", "dual").at(0))
        / 1000.0;
    out_credit += kRdrDn5Beta
        * std::abs(s.zone_output("cpf5m_dn", "drplane1", "dual").at(0))
        / 1000.0;
    CHECK(out_credit == doctest::Approx(credit));
  }
}

TEST_CASE("Dynamic reserve requirement: load_term validation")  // NOLINT
{
  auto c = rdr_surplus_case("bogus");
  CHECK_THROWS(RdrSolved {c});
}

TEST_CASE("Extended reserve provision: per-zone effectiveness factors")
// NOLINT
{
  // ONE up column shared by two requirements (minuta (A.3)/(A.4)) with
  // factors 0.5 and 0.8: 0.5·r ≥ 100 and 0.8·r ≥ 100 → r = 200.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 100})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 1000})";
  c.reserve_zones = R"(
      {"uid": 1, "name": "cpf5m", "urreq": 100},
      {"uid": 2, "name": "cpf10s", "urreq": 100})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp", "generator": "g1",
          "reserve_zones": [1, 2], "ur_zone_factors": [0.5, 0.8],
          "urmax": 1000, "ur_provision_factor": 1, "urcost": 1})";
  {
    const RdrSolved s(c);
    const auto& rp = s.provision("rp");
    CHECK(s.col(rp.lookup_up_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(200.0));
  }
  {
    // Without factors (missing = 1.0) one 100 MW column serves both.
    c.reserve_provisions =
        R"({"uid": 1, "name": "rp", "generator": "g1",
            "reserve_zones": [1, 2], "urmax": 1000,
            "ur_provision_factor": 1, "urcost": 1})";
    const RdrSolved s(c);
    const auto& rp = s.provision("rp");
    CHECK(s.col(rp.lookup_up_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(100.0));
  }
  {
    // A short factor list pads with 1.0: zone 2 counts r at full value,
    // so 0.5·r ≥ 100 alone binds (r = 200) and zone 2 is over-served.
    c.reserve_provisions =
        R"({"uid": 1, "name": "rp", "generator": "g1",
            "reserve_zones": [1, 2], "ur_zone_factors": [0.5],
            "urmax": 1000, "ur_provision_factor": 1, "urcost": 1})";
    const RdrSolved s(c);
    const auto& rp = s.provision("rp");
    CHECK(s.col(rp.lookup_up_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(200.0));
  }
}

TEST_CASE("Extended reserve provision: shared headroom across services")
// NOLINT
{
  // g1 (100 MW) serves 80 MW of load and offers CPF and CSF up reserve,
  // 15 MW each.  Per-provision headroom lets both reach 15 (p + r ≤ 100
  // twice); shared headroom p + r_cpf + r_csf ≤ 100 caps the sum at 20.
  const auto make = [](std::string_view shared)
  {
    RdrCase c;
    c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 80})";
    c.generators =
        R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100})";
    c.reserve_zones = R"(
        {"uid": 1, "name": "cpf", "urreq": 15, "urcost": 1000},
        {"uid": 2, "name": "csf", "urreq": 15, "urcost": 1000})";
    c.reserve_provisions = std::format(
        R"({{"uid": 1, "name": "rp_cpf", "generator": "g1",
            "reserve_zones": [1], "urmax": 100, "ur_provision_factor": 1,
            "urcost": 1{0}}},
           {{"uid": 2, "name": "rp_csf", "generator": "g1",
            "reserve_zones": [2], "urmax": 100, "ur_provision_factor": 1,
            "urcost": 1{0}}})",
        shared);
    return c;
  };
  const auto total = [](const RdrSolved& s)
  {
    return s.col(s.provision("rp_cpf").lookup_up_provision_col(
               s.scenario(), s.stage(), s.buid(0)))
        + s.col(s.provision("rp_csf").lookup_up_provision_col(
            s.scenario(), s.stage(), s.buid(0)));
  };
  {
    const RdrSolved s(make(""));
    CHECK(total(s) == doctest::Approx(30.0));
  }
  {
    const RdrSolved s(make(R"(, "shared_headroom": true)"));
    CHECK(total(s) == doctest::Approx(20.0));
    CHECK(s.gen("g1") == doctest::Approx(80.0));
  }
}

TEST_CASE("Extended reserve provision: symmetric up/down")  // NOLINT
{
  // Up requirement 50, down 10; symmetry forces r_dn = r_up = 50.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 200})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 500})";
  c.reserve_zones = R"({"uid": 1, "name": "cpf", "urreq": 50, "drreq": 10})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp", "generator": "g1", "reserve_zones": [1],
          "urmax": 100, "drmax": 100, "ur_provision_factor": 1,
          "dr_provision_factor": 1, "urcost": 1, "drcost": 1,
          "symmetric": true})";
  {
    const RdrSolved s(c);
    const auto& rp = s.provision("rp");
    CHECK(s.col(rp.lookup_up_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(50.0));
    CHECK(s.col(rp.lookup_dn_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(50.0));
  }
  {
    // Not symmetric: each side meets only its own requirement.
    c.reserve_provisions =
        R"({"uid": 1, "name": "rp", "generator": "g1", "reserve_zones": [1],
            "urmax": 100, "drmax": 100, "ur_provision_factor": 1,
            "dr_provision_factor": 1, "urcost": 1, "drcost": 1})";
    const RdrSolved s(c);
    const auto& rp = s.provision("rp");
    CHECK(s.col(rp.lookup_up_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(50.0));
    CHECK(s.col(rp.lookup_dn_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(10.0));
  }
}

TEST_CASE("Extended reserve provision: offline (cold) up reserve")  // NOLINT
{
  // `peaker` (relaxed Commitment, pmin 50) offers 100 MW of cold CTF
  // reserve: r ≤ 100·(1 − u).  It must stay OFF to offer it; when the
  // energy is needed it runs and its cold offer vanishes.
  const auto make = [](double load)
  {
    RdrCase c;
    c.chronological = true;
    c.demands = std::format(
        R"({{"uid": 1, "name": "d1", "bus": 1, "capacity": {}}})", load);
    c.generators = R"(
        {"uid": 1, "name": "base", "bus": 1, "gcost": 10, "capacity": 100},
        {"uid": 2, "name": "peaker", "bus": 1, "gcost": 90,
         "capacity": 100})";
    c.commitments =
        R"({"uid": 1, "name": "uc_peaker", "generator": "peaker",
            "pmin": 50, "relax": true, "initial_status": 0})";
    c.reserve_zones =
        R"({"uid": 1, "name": "ctf", "urreq": 80, "urcost": 1000})";
    c.reserve_provisions =
        R"({"uid": 1, "name": "rp_cold", "generator": "peaker",
            "reserve_zones": [1], "urmax": 100, "ur_provision_factor": 1,
            "urcost": 1, "offline": true})";
    return c;
  };
  {
    const RdrSolved s(make(100.0));
    const auto& rp = s.provision("rp_cold");
    CHECK(s.gen("peaker") == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(s.col(rp.lookup_up_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(80.0));
  }
  {
    // 200 MW load: peaker must run fully (u = 1) → no cold reserve.
    const RdrSolved s(make(200.0));
    const auto& rp = s.provision("rp_cold");
    CHECK(s.gen("peaker") == doctest::Approx(100.0));
    CHECK(s.col(rp.lookup_up_provision_col(s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(0.0).epsilon(1e-9));
  }
}

TEST_CASE("ReserveZone / ReserveProvision JSON: extended fields round-trip")
// NOLINT
{
  constexpr std::string_view zone_json = R"({
    "uid": 7, "name": "cpf10s", "urcost": 5000, "urmin": 0,
    "urreq_planes": [
      {"alpha": 516.0, "demand_coef": -0.0366, "inertia_coef": -0.0123,
       "contingency_coef": 1.25},
      {"alpha": [[1.0, 2.0]], "flow_coef": 1.0}
    ],
    "drreq_planes": [{"alpha": -80, "demand_coef": -0.004,
                      "contingency_coef": 0.96}],
    "load_term": "exogenous", "system_load": 9642.9,
    "demand_load_factor": 1.025, "demands": ["d1", 2],
    "inertia_zone": "sen", "contingency_generators": ["g1"],
    "contingency_demands": ["bess_dem"], "flow_lines": ["l1"]
  })";
  const auto rz = daw::json::from_json<ReserveZone>(zone_json);
  REQUIRE(rz.urreq_planes.size() == 2);
  REQUIRE(rz.drreq_planes.size() == 1);
  CHECK(std::get<double>(rz.urreq_planes[0].alpha.value_or(0.0))
        == doctest::Approx(516.0));
  CHECK(std::get<double>(rz.urreq_planes[1].flow_coef.value_or(0.0))
        == doctest::Approx(1.0));
  CHECK(rz.load_term.value_or("") == "exogenous");
  CHECK(rz.demand_load_factor.value_or(0.0) == doctest::Approx(1.025));
  CHECK(rz.demands.size() == 2);
  CHECK(rz.inertia_zone.has_value());
  CHECK(rz.contingency_generators.size() == 1);
  CHECK(rz.contingency_demands.size() == 1);
  CHECK(rz.flow_lines.size() == 1);
  CHECK(rz.has_dynamic_requirement());

  const auto back = daw::json::from_json<ReserveZone>(daw::json::to_json(rz));
  CHECK(back.urreq_planes == rz.urreq_planes);
  CHECK(back.drreq_planes == rz.drreq_planes);
  CHECK(back.demands == rz.demands);
  CHECK(back.flow_lines == rz.flow_lines);
  CHECK(back.load_term == rz.load_term);

  constexpr std::string_view prov_json = R"({
    "uid": 1, "name": "rp", "generator": "g1", "reserve_zones": [1, 2],
    "ur_zone_factors": [0.5, 0.8], "dr_zone_factors": [1.0],
    "shared_headroom": true, "offline": false, "symmetric": true
  })";
  const auto rp = daw::json::from_json<ReserveProvision>(prov_json);
  CHECK(rp.ur_zone_factors == std::vector<double> {0.5, 0.8});
  CHECK(rp.dr_zone_factors == std::vector<double> {1.0});
  CHECK(rp.shared_headroom.value_or(false));
  CHECK_FALSE(rp.offline.value_or(true));
  CHECK(rp.symmetric.value_or(false));
  const auto rp2 =
      daw::json::from_json<ReserveProvision>(daw::json::to_json(rp));
  CHECK(rp2.ur_zone_factors == rp.ur_zone_factors);
  CHECK(rp2.symmetric == rp.symmetric);

  // A plain zone stays a fixed-requirement zone.
  const auto plain =
      daw::json::from_json<ReserveZone>(R"({"uid": 1, "name": "z"})");
  CHECK_FALSE(plain.has_dynamic_requirement());
}

TEST_CASE("Dynamic reserve requirement: line-flow term (north special CPF)")
// NOLINT
{
  // PLEXOS `SpecialCPFNorth`: REQ_north ≥ 3 + max(0, Σ flow into the
  // north corridor).  Two planes {3} and {3 + 1·F} over line l12.
  // 300 MW flow → R = 303; μ = 5 → each MW shipped costs 5 more, so
  // λ2 = 10 + 5 while λ1 = 10 (a legitimate transmission-security cost).
  constexpr std::string_view json = R"({
  "options": {"annual_discount_rate": 0.0,
    "model_options": {"use_single_bus": false, "use_kirchhoff": true,
                      "demand_fail_cost": 1.0e5, "scale_objective": 1.0}},
  "simulation": {"block_array": [{"uid": 1, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
    "scenario_array": [{"uid": 1}]},
  "system": {"name": "rdr_flow",
    "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
    "line_array": [{"uid": 1, "name": "l12", "bus_a": 1, "bus_b": 2,
                    "reactance": 0.1, "tmax_ab": 1000, "tmax_ba": 1000,
                    "capacity": 1000}],
    "demand_array": [{"uid": 1, "name": "d2", "bus": 2, "capacity": 300}],
    "generator_array": [
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 1000},
      {"uid": 2, "name": "res", "bus": 2, "gcost": 100, "capacity": 1000}],
    "reserve_zone_array": [{"uid": 1, "name": "cpf_north", "urcost": 5000,
      "urreq_planes": [{"alpha": 3}, {"alpha": 3, "flow_coef": 1}],
      "flow_lines": ["l12"]}],
    "reserve_provision_array": [{"uid": 1, "name": "rp_res",
      "generator": "res", "reserve_zones": [1], "urmax": 1000,
      "ur_provision_factor": 1, "urcost": 5}]
  }})";
  PlanningLP plp(parse_planning_json(json));
  REQUIRE(plp.resolve().has_value());
  auto& sys = plp.systems().front().front();
  auto& li = sys.linear_interface();
  const auto& scenario = sys.scene().scenarios().front();
  const auto& stage = sys.phase().stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto& rz = sys.elements<ReserveZoneLP>().front();
  const auto r = rz.lookup_urequirement_col(scenario, stage, buid);
  REQUIRE(r.has_value());
  CHECK(li.get_col_sol()[r.value_or(ColIndex {0})] == doctest::Approx(303.0));
  const auto duals = li.get_row_dual();
  const auto& buses = sys.elements<BusLP>();
  const double l1 = duals[buses[0].balance_rows_at(scenario, stage).at(buid)];
  const double l2 = duals[buses[1].balance_rows_at(scenario, stage).at(buid)];
  CHECK(l1 == doctest::Approx(10.0));
  CHECK(l2 == doctest::Approx(15.0));
}

TEST_CASE("Dynamic reserve requirement: validate_planning references")
// NOLINT
{
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 10})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 1, "capacity": 100})";
  c.batteries =
      R"({"uid": 1, "name": "bess", "bus": 1, "emax": 10, "capacity": 10,
          "pmax_charge": 5, "pmax_discharge": 5})";
  SUBCASE("valid, incl. synthetic battery members")
  {
    c.reserve_zones =
        R"({"uid": 1, "name": "z", "urreq_planes": [{"alpha": 1}],
            "contingency_generators": ["g1", "bess_gen"],
            "contingency_demands": ["bess_dem"], "demands": ["d1"],
            "load_term": "exogenous"})";
    auto planning = parse_planning_json(c.str());
    const auto result = validate_planning(planning);
    CHECK(result.errors.empty());
  }
  SUBCASE("dangling references and bad load_term")
  {
    c.reserve_zones =
        R"({"uid": 1, "name": "z", "urreq_planes": [{"alpha": 1}],
            "contingency_generators": ["nope"],
            "contingency_demands": ["nope_dem"], "demands": [9],
            "flow_lines": ["l9"], "inertia_zone": "iz9",
            "load_term": "generation"})";
    auto planning = parse_planning_json(c.str());
    const auto result = validate_planning(planning);
    CHECK(result.errors.size() == 6);
  }
}

TEST_CASE("Dynamic reserve requirement: PLEXOS-style contingency groups")
// NOLINT
{
  // PLEXOS `NEHUENCO_1_GNL_Comparison`: MaxInj ≥ Σ modes + 8.4, plus the
  // common `limited_generation_group` (PMGD gen − BESS charge) added to
  // every candidate.  Plant "ccgt" = two modes at 100 + 150 MW, PMGD 40
  // MW, offset 8.4 → C = 298.4; the lone 200 MW unit (+ PMGD = 240) is
  // the other candidate.  Plane R ≥ 1·C binds.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 490})";
  c.generators = R"(
      {"uid": 1, "name": "ccgt_a", "bus": 1, "gcost": 0, "pmin": 100,
       "pmax": 100, "capacity": 100},
      {"uid": 2, "name": "ccgt_b", "bus": 1, "gcost": 0, "pmin": 150,
       "pmax": 150, "capacity": 150},
      {"uid": 3, "name": "pmgd", "bus": 1, "gcost": 0, "pmin": 40,
       "pmax": 40, "capacity": 40},
      {"uid": 4, "name": "solo", "bus": 1, "gcost": 0, "pmin": 200,
       "pmax": 200, "capacity": 200},
      {"uid": 5, "name": "res", "bus": 1, "gcost": 100, "capacity": 2000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_res", "generator": "res",
          "reserve_zones": [1], "urmax": 2000, "urcost": 5,
          "ur_provision_factor": 1})";
  c.reserve_zones =
      R"({"uid": 1, "name": "cpf", "urcost": 5000,
          "urreq_planes": [{"contingency_coef": 1}],
          "contingency_generators": ["solo"],
          "urcontingencies": [
            {"name": "NEHUENCO_1_GNL", "generators": ["ccgt_a", "ccgt_b",
             "pmgd"], "offset": 8.4},
            {"name": "SOLO+PMGD", "generators": ["solo", "pmgd"],
             "generator_coefs": [1, 1]}]})";
  const RdrSolved s(c);
  // max(200, 100 + 150 + 40 + 8.4, 200 + 40) = 298.4
  CHECK(s.col(s.zone("cpf").lookup_urcontingency_col(
            s.scenario(), s.stage(), s.buid(0)))
        == doctest::Approx(298.4));
  CHECK(s.ureq("cpf") == doctest::Approx(298.4));
}

TEST_CASE("Dynamic reserve requirement: contingency_includes_reserve")
// NOLINT
{
  // big (500 MW, free energy) serves the 300 MW load and can offer its
  // 200 MW headroom as cheap reserve (1 $/MW); "other" offers at 5.
  // R ≥ C ≥ p_big.  PLEXOS form: R = 300, big gives 200, other 100.
  // With the lost unit's reserve counted (C ≥ p_big + r_big), big's
  // reserve raises R one-for-one and is worthless: r_big = 0, other
  // carries all 300, R = 300.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 300})";
  c.generators = R"(
      {"uid": 1, "name": "big", "bus": 1, "gcost": 0, "capacity": 500},
      {"uid": 2, "name": "other", "bus": 1, "gcost": 100, "capacity": 1000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_big", "generator": "big",
          "reserve_zones": [1], "urmax": 500, "urcost": 1,
          "ur_provision_factor": 1},
         {"uid": 2, "name": "rp_other", "generator": "other",
          "reserve_zones": [1], "urmax": 1000, "urcost": 5,
          "ur_provision_factor": 1})";
  const auto zone = [](bool includes)
  {
    return std::format(
        R"({{"uid": 1, "name": "cpf", "urcost": 5000,
            "urreq_planes": [{{"contingency_coef": 1}}],
            "contingency_generators": ["big"],
            "contingency_includes_reserve": {}}})",
        includes);
  };
  const auto rup = [](const RdrSolved& s, std::string_view name)
  {
    return s.col(s.provision(name).lookup_up_provision_col(
        s.scenario(), s.stage(), s.buid(0)));
  };
  SUBCASE("PLEXOS default: C = p")
  {
    c.reserve_zones = zone(false);
    const RdrSolved s(c);
    CHECK(s.ureq("cpf") == doctest::Approx(300.0));
    CHECK(rup(s, "rp_big") == doctest::Approx(200.0));
    CHECK(rup(s, "rp_other") == doctest::Approx(100.0));
  }
  SUBCASE("lost unit's reserve counted: C = p + r")
  {
    c.reserve_zones = zone(true);
    const RdrSolved s(c);
    CHECK(s.ureq("cpf") == doctest::Approx(300.0));
    CHECK(rup(s, "rp_big") == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(rup(s, "rp_other") == doctest::Approx(300.0));
  }
}

TEST_CASE("Inertia requirement credits: Guacolda commitment steps")  // NOLINT
{
  // Norte Grande inertia minimum 13 400 MWs, relieved by 3 900 once 2
  // Guacolda units are committed and by 2 000 more at 4 (13 400 /
  // 9 500 / 7 500).  Three units are must-run (n = 3); the rest of the
  // inertia comes from "syn" (100 MWs per MW of its dispatch, 1 $/MW),
  // so 100·r_syn = 13 400 − relief:
  //   integer (exact step):      relief = 3 900            → r = 95
  //   continuous (PLEXOS form):  relief = 3 900·min(1, 3/2)
  //                                      + 2 000·min(1, 3/4) → r = 80
  //   no commitment:             relief = 0                → r = 134
  RdrCase c;
  c.chronological = true;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 300})";
  c.generators = R"(
      {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "pmin": 50,
       "pmax": 100, "capacity": 100},
      {"uid": 2, "name": "g2", "bus": 1, "gcost": 10, "pmin": 50,
       "pmax": 100, "capacity": 100},
      {"uid": 3, "name": "g3", "bus": 1, "gcost": 10, "pmin": 50,
       "pmax": 100, "capacity": 100},
      {"uid": 4, "name": "syn", "bus": 1, "gcost": 0, "capacity": 200})";
  const std::string commitments = R"(
      {"uid": 1, "name": "c1", "generator": "g1", "must_run": true},
      {"uid": 2, "name": "c2", "generator": "g2", "must_run": true},
      {"uid": 3, "name": "c3", "generator": "g3", "must_run": true})";
  c.inertia_provisions =
      R"({"uid": 1, "name": "ip_syn", "generator": "syn",
          "inertia_zones": ["ng"], "provision_factor": 100,
          "provision_max": 200, "cost": 1})";
  const auto zone = [](bool integer)
  {
    return std::format(
        R"({{"uid": 1, "name": "ng", "requirement": 13400,
            "requirement_credits": [
              {{"name": "guacolda2", "generators": ["g1", "g2", "g3"],
               "units": 2, "credit": 3900, "integer": {0}}},
              {{"name": "guacolda4", "generators": ["g1", "g2", "g3"],
               "units": 4, "credit": 2000, "integer": {0}}}]}})",
        integer);
  };
  const auto r_syn = [](const RdrSolved& s)
  {
    const auto& ip = s.sys().template elements<InertiaProvisionLP>().front();
    return s.col(ip.lookup_provision_col(s.scenario(), s.stage(), s.buid(0)));
  };
  SUBCASE("binary selector: exact step")
  {
    c.commitments = commitments;
    c.inertia_zones = zone(true);
    const RdrSolved s(c);
    CHECK(r_syn(s) == doctest::Approx(95.0));
  }
  SUBCASE("continuous selector (PLEXOS): linear relief")
  {
    c.commitments = commitments;
    c.inertia_zones = zone(false);
    const RdrSolved s(c);
    CHECK(r_syn(s) == doctest::Approx(80.0));
  }
  SUBCASE("SimpleCommitment status columns give the same relief")
  {
    c.simple_commitments = R"(
        {"uid": 1, "name": "s1", "generator": "g1", "must_run": true},
        {"uid": 2, "name": "s2", "generator": "g2", "must_run": true},
        {"uid": 3, "name": "s3", "generator": "g3", "must_run": true})";
    c.inertia_zones = zone(false);
    const RdrSolved s(c);
    CHECK(r_syn(s) == doctest::Approx(80.0));
  }
  SUBCASE("no commitment: strict mode rejects the unearnable credit")
  {
    // The `normal`-mode fallback (warning + exogenous requirement, r = 134)
    // is covered in test_reserve_coverage_gaps.cpp.
    c.inertia_zones = zone(true);
    CHECK_THROWS_WITH_AS(PlanningLP(parse_planning_json(c.str())),
                         doctest::Contains("no Commitment (no status column)"),
                         std::runtime_error);
  }
}

TEST_CASE("Reserve requirement_sense eq: equality balance with surplus")
// NOLINT
{
  // Provider "a" must offer at least `urmin` (a PLEXOS Min Provision).
  // "ge": the excess is free.  "eq" (PLEXOS `CPF_UpMinProvision`,
  // Sense 0, + VoRS − Slack): the excess goes to a surplus column priced
  // at `ursurplus_cost`; without that cost the provision is pinned.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 100})";
  c.generators = R"(
      {"uid": 1, "name": "a", "bus": 1, "gcost": 10, "capacity": 1000},
      {"uid": 2, "name": "b", "bus": 1, "gcost": 20, "capacity": 1000})";
  const auto provisions = [](double amin, double bmin)
  {
    return std::format(
        R"({{"uid": 1, "name": "rp_a", "generator": "a",
            "reserve_zones": [1], "urmax": 500, "urmin": {},
            "ur_provision_factor": 1, "urcost": 1}},
           {{"uid": 2, "name": "rp_b", "generator": "b",
            "reserve_zones": [1], "urmax": 500, "urmin": {},
            "ur_provision_factor": 1, "urcost": 2}})",
        amin,
        bmin);
  };
  const auto rup = [](const RdrSolved& s, std::string_view name)
  {
    return s.col(s.provision(name).lookup_up_provision_col(
        s.scenario(), s.stage(), s.buid(0)));
  };

  SUBCASE("fixed requirement, eq + surplus cost: excess is surplus")
  {
    c.reserve_provisions = provisions(150.0, 0.0);
    c.reserve_zones =
        R"({"uid": 1, "name": "csf", "urreq": 100, "urcost": 5000,
            "requirement_sense": "eq", "ursurplus_cost": 3})";
    const RdrSolved s(c);
    CHECK(rup(s, "rp_a") == doctest::Approx(150.0));
    const auto surplus = s.zone_output("csf", "ursurplus", "sol");
    REQUIRE(surplus.size() == 1);
    CHECK(surplus[0] == doctest::Approx(50.0));
  }
  SUBCASE("fixed hard requirement, eq: provision pinned to the requirement")
  {
    // 30 + 30 floors, requirement 100: total exactly 100 (a fills 70).
    c.reserve_provisions = provisions(30.0, 30.0);
    c.reserve_zones =
        R"({"uid": 1, "name": "csf", "urreq": 100,
            "requirement_sense": "eq"})";
    const RdrSolved s(c);
    CHECK(rup(s, "rp_a") + rup(s, "rp_b") == doctest::Approx(100.0));
    CHECK(rup(s, "rp_a") == doctest::Approx(70.0));
  }
  SUBCASE("planes requirement, eq: R itself absorbs the excess")
  {
    // R ≥ max(80) is a free column, so with `eq` the 120 MW floor
    // offer lifts R to 120 at no cost and the priced surplus stays 0 —
    // the same as PLEXOS, whose `CPF5mUp_Requirement` (cost 0) can rise
    // above its planes before `CPF5mUp_Slack` (1 $/MW) is used.  In
    // planes mode "eq" is therefore equivalent to "ge", and the dead
    // surplus column is not built (review P1-7; validate_planning warns).
    c.reserve_provisions = provisions(120.0, 0.0);
    c.reserve_zones =
        R"({"uid": 1, "name": "cpf", "urcost": 5000,
            "urreq_planes": [{"alpha": 80}],
            "requirement_sense": "eq", "ursurplus_cost": 1})";
    const RdrSolved s(c);
    CHECK(s.ureq("cpf") == doctest::Approx(120.0));
    CHECK(s.zone_output("cpf", "ursurplus", "sol").empty());
    const auto shortage = s.zone_output("cpf", "urshortage", "sol");
    CHECK((shortage.empty() || std::abs(shortage[0]) < 1e-9));
  }
  SUBCASE("ge (default): the excess needs no surplus column")
  {
    c.reserve_provisions = provisions(150.0, 0.0);
    c.reserve_zones =
        R"({"uid": 1, "name": "csf", "urreq": 100, "urcost": 5000})";
    const RdrSolved s(c);
    CHECK(rup(s, "rp_a") == doctest::Approx(150.0));
    CHECK(s.zone_output("csf", "ursurplus", "sol").empty());
  }
  SUBCASE("unknown sense throws")
  {
    c.reserve_provisions = provisions(0.0, 0.0);
    c.reserve_zones =
        R"({"uid": 1, "name": "csf", "urreq": 100,
            "requirement_sense": "le"})";
    CHECK_THROWS(RdrSolved {c});
  }
}

namespace
{
/// Two-bus north (N) → south (S) corridor for the north-special CPF.
/// N: solar with a 200 MW must-take floor, no load.  S: 300 MW load and a
/// 10 $/MWh gas unit.  The reserve provider sits at S (30 $/MW).
std::string rdr_corridor_case(std::string_view planes, bool with_flow)
{
  return std::format(
      R"({{
  "options": {{"annual_discount_rate": 0.0,
    "model_options": {{"use_single_bus": false, "use_kirchhoff": true,
                      "demand_fail_cost": 1.0e5, "scale_objective": 1.0}}}},
  "simulation": {{"block_array": [{{"uid": 1, "duration": 1}},
                                 {{"uid": 2, "duration": 1}}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": 1}},
                    {{"uid": 2, "first_block": 1, "count_block": 1}}],
    "scenario_array": [{{"uid": 1}}]}},
  "system": {{"name": "rdr_corridor",
    "bus_array": [{{"uid": 1, "name": "north"}}, {{"uid": 2, "name": "south"}}],
    "line_array": [{{"uid": 1, "name": "ns", "bus_a": 1, "bus_b": 2,
                    "reactance": 0.1, "tmax_ab": 1000, "tmax_ba": 1000,
                    "capacity": 1000}}],
    "demand_array": [{{"uid": 1, "name": "d_s", "bus": 2, "capacity": 300}}],
    "generator_array": [
      {{"uid": 1, "name": "solar_n", "bus": 1, "gcost": 0, "pmin": 200,
       "pmax": 1000, "capacity": 1000}},
      {{"uid": 2, "name": "gas_s", "bus": 2, "gcost": 10, "capacity": 1000}},
      {{"uid": 3, "name": "res_s", "bus": 2, "gcost": 100, "capacity": 1000}}],
    "reserve_zone_array": [{{"uid": 1, "name": "cpf_north", "urcost": 5000,
      "urreq_planes": {}{}}}],
    "reserve_provision_array": [{{"uid": 1, "name": "rp_res",
      "generator": "res_s", "reserve_zones": [1], "urmax": 1000,
      "ur_provision_factor": 1, "urcost": 30}}]
  }}}})",
      planes,
      with_flow ? R"(, "flow_lines": ["ns"])" : "");
}

struct RdrCorridor
{
  PlanningLP plp;
  explicit RdrCorridor(const std::string& json)
      : plp(parse_planning_json(json))
  {
    REQUIRE(plp.resolve().has_value());
  }
  [[nodiscard]] double lambda(std::size_t bus, std::size_t st)
  {
    auto& sys = plp.systems().front().front();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& stage = sys.phase().stages().at(st);
    const auto buid = stage.blocks().front().uid();
    return sys.linear_interface()
        .get_row_dual()[sys.elements<BusLP>()
                            .at(bus)
                            .balance_rows_at(scenario, stage)
                            .at(buid)];
  }
  [[nodiscard]] double req(std::size_t st)
  {
    auto& sys = plp.systems().front().front();
    const auto& scenario = sys.scene().scenarios().front();
    const auto& stage = sys.phase().stages().at(st);
    const auto buid = stage.blocks().front().uid();
    const auto r =
        sys.elements<ReserveZoneLP>().front().lookup_urequirement_col(
            scenario, stage, buid);
    REQUIRE(r.has_value());
    return sys.linear_interface().get_col_sol()[r.value_or(ColIndex {0})];
  }
};
}  // namespace

TEST_CASE("North special CPF: endogenous flow term vs exogenous schedule")
// NOLINT
{
  // Endogenous (PLEXOS SpecialCPFNorth chain, disabled in PCP): R ≥ 3 +
  // F_ns.  Each MW sent south raises R by 1 at μ = 30, more than the
  // 10 $/MWh it saves at S, so gas serves 100 MW and the 200 MW
  // must-take solar prices N at λ_S − μ = 10 − 30 = −20 $/MWh.
  // Exogenous: the same requirement with the expected flow (200) in a
  // per-stage alpha: R = 203, solar serves all, λ_N = λ_S = 0.
  SUBCASE("flow_coef (coupled): negative north price")
  {
    RdrCorridor s(rdr_corridor_case(
        R"([{"alpha": 3}, {"alpha": 3, "flow_coef": 1}])", /*with_flow=*/true));
    CHECK(s.req(0) == doctest::Approx(203.0));
    CHECK(s.lambda(1, 0) == doctest::Approx(10.0));
    CHECK(s.lambda(0, 0) == doctest::Approx(-20.0));
  }
  SUBCASE("exogenous expected flow in alpha: no spread")
  {
    RdrCorridor s(
        rdr_corridor_case(R"([{"alpha": 203}])", /*with_flow=*/false));
    CHECK(s.req(0) == doctest::Approx(203.0));
    CHECK(s.lambda(0, 0) == doctest::Approx(0.0));
    CHECK(s.lambda(1, 0) == doctest::Approx(0.0));
  }
  SUBCASE("validate_planning warns on flow_coef only")
  {
    auto coupled = parse_planning_json(rdr_corridor_case(
        R"([{"alpha": 3}, {"alpha": 3, "flow_coef": 1}])", /*with_flow=*/true));
    CHECK(validate_planning(coupled).warnings.size() == 1);
    auto exo = parse_planning_json(
        rdr_corridor_case(R"([{"alpha": 3}])", /*with_flow=*/false));
    CHECK(validate_planning(exo).warnings.empty());
  }
}

TEST_CASE("North special CPF: dated alpha schedule (time-varying RHS)")
// NOLINT
{
  // PLEXOS SpecialCPFNorth_Definition RHS 3 on one date, −19.36 on
  // another: a per-stage `alpha` schedule, no endogenous term.  Stage 1:
  // R = 103 (3 + 100 MW fixed corridor contingency); stage 2: −19.36 is
  // floored at 0.
  RdrCorridor s(rdr_corridor_case(R"([{"alpha": [[103], [-19.36]]}])",
                                  /*with_flow=*/false));
  CHECK(s.req(0) == doctest::Approx(103.0));
  CHECK(s.req(1) == doctest::Approx(0.0).epsilon(1e-9));
  CHECK(s.lambda(0, 0) == doctest::Approx(s.lambda(1, 0)));
}

TEST_CASE("Dynamic reserve requirement: explicit demands list for D")  // NOLINT
{
  // R = 100 − 0.1·D with D = 1.05 · Σ lmax over the listed demands.
  RdrCase c;
  c.demands = R"(
      {"uid": 1, "name": "d1", "bus": 1, "capacity": 100},
      {"uid": 2, "name": "d2", "bus": 1, "capacity": 50})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 1000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp", "generator": "g1", "reserve_zones": [1],
          "urmax": 1000, "ur_provision_factor": 1, "urcost": 1})";
  const auto zone = [](std::string_view demands)
  {
    return std::format(
        R"({{"uid": 1, "name": "cpf", "urcost": 5000,
            "urreq_planes": [{{"alpha": 100, "demand_coef": -0.1}}]{}}})",
        demands);
  };
  SUBCASE("listed: only d1")
  {
    c.reserve_zones = zone(R"(, "demands": ["d1"])");
    const RdrSolved s(c);
    CHECK(s.ureq("cpf") == doctest::Approx(100.0 - (0.1 * 1.05 * 100.0)));
  }
  SUBCASE("default: every demand")
  {
    c.reserve_zones = zone("");
    const RdrSolved s(c);
    CHECK(s.ureq("cpf") == doctest::Approx(100.0 - (0.1 * 1.05 * 150.0)));
  }
}

TEST_CASE("Dynamic reserve requirement: demand contingency group (down)")
// NOLINT
{
  // Down contingency = losing load: C_dn ≥ 1·d1 + 0.5·d2 = 125, and the
  // plane R_dn ≥ C_dn binds.  Without the group the plane is 0.
  RdrCase c;
  c.demands = R"(
      {"uid": 1, "name": "d1", "bus": 1, "capacity": 100},
      {"uid": 2, "name": "d2", "bus": 1, "capacity": 50})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 1000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp", "generator": "g1", "reserve_zones": [1],
          "drmax": 1000, "dr_provision_factor": 1, "drcost": 1})";
  SUBCASE("with the group")
  {
    c.reserve_zones =
        R"({"uid": 1, "name": "cpf_dn", "drcost": 5000,
            "drreq_planes": [{"contingency_coef": 1}],
            "drcontingencies": [{"name": "load_block",
              "demands": ["d1", "d2"], "demand_coefs": [1, 0.5]}]})";
    const RdrSolved s(c);
    CHECK(s.col(s.zone("cpf_dn").lookup_drcontingency_col(
              s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(125.0));
    CHECK(s.dreq("cpf_dn") == doctest::Approx(125.0));
  }
  SUBCASE("without contingencies: R_dn = 0")
  {
    c.reserve_zones =
        R"({"uid": 1, "name": "cpf_dn", "drcost": 5000,
            "drreq_planes": [{"contingency_coef": 1}]})";
    c.constraint_mode = "normal";
    const RdrSolved s(c);
    CHECK(std::abs(s.dreq("cpf_dn")) < 1e-9);
  }
}

TEST_CASE("Dynamic reserve requirement: line contingency group couples flow")
// NOLINT
{
  // A corridor contingency {lines: [ns], offset 3} is the same endogenous
  // coupling as a plane flow_coef: C ≥ F + 3 and R ≥ C give R = 203 and
  // the north price λ_S − μ = 10 − 30 = −20.
  RdrCorridor s(rdr_corridor_case(
      R"([{"contingency_coef": 1}], "urcontingencies": [{"name": "ns_corr",
          "lines": ["ns"], "line_coefs": [1], "offset": 3}])",
      /*with_flow=*/false));
  CHECK(s.req(0) == doctest::Approx(203.0));
  CHECK(s.lambda(0, 0) == doctest::Approx(-20.0));
  CHECK(s.lambda(1, 0) == doctest::Approx(10.0));
}

// ─── Plane inertia term: commitment / exogenous / endogenous ───────────────

namespace
{
/// 1 000 MW demand, 3 000 MW free hydro (curtails: it sets the price),
/// a 100 $/MW reserve unit.  The hydro carries an inertia provision
/// (pf = 10 MWs/MW, provision_max 2 000 MW → I_c = 20 000 MWs when on).
/// Plane: R = 516 − 0.0366·D − 0.0123·I, D = 1.05 · 1 000 (exogenous).
RdrCase rdr_inertia_term_case(std::string_view inertia_term,
                              std::string_view zone_extra,
                              std::string_view hydro_uc)
{
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 1000})";
  c.generators = R"(
      {"uid": 1, "name": "hydro", "bus": 1, "gcost": 0, "capacity": 3000},
      {"uid": 2, "name": "res", "bus": 1, "gcost": 500, "capacity": 1000})";
  c.simple_commitments = std::string {hydro_uc};
  c.inertia_zones = R"({"uid": 1, "name": "sen"})";
  c.inertia_provisions =
      R"({"uid": 1, "name": "ip_hydro", "generator": "hydro",
          "inertia_zones": ["sen"], "provision_factor": 10,
          "provision_max": 2000})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_res", "generator": "res",
          "reserve_zones": [1], "urmax": 1000, "ur_provision_factor": 1,
          "urcost": 100})";
  c.reserve_zones = std::format(
      R"({{"uid": 1, "name": "cpf10s", "urcost": 5000,
          "urreq_planes": [{{"alpha": 516, "demand_coef": -0.0366,
                             "inertia_coef": -0.0123}}],
          "inertia_zone": "sen", "inertia_term": "{}"{}}})",
      inertia_term,
      zone_extra);
  return c;
}

constexpr double kItR0 = 516.0 - (0.0366 * 1050.0);
constexpr std::string_view kItRelaxedUc =
    R"({"uid": 1, "name": "uc_hydro", "generator": "hydro", "relax": true})";
}  // namespace

TEST_CASE("CPF plane inertia term: commitment, exogenous, endogenous")
// NOLINT
{
  // The default is "commitment" (CEN minuta Anexo B.1: I = Σ I_c·u_c),
  // chosen on the empirical comparison recorded in the coordinator report:
  // commitment gave no more negative-LMP bus-hours than exogenous
  // (see the multi-bus comparison test below).
  SUBCASE("commitment (default): I = I_c·u, no LMP credit")
  {
    const RdrSolved s(rdr_inertia_term_case("commitment", "", kItRelaxedUc));
    CHECK(s.col(s.zone("cpf10s").lookup_inertia_col(
              s.scenario(), s.stage(), s.buid(0)))
          == doctest::Approx(20000.0));
    CHECK(s.ureq("cpf10s") == doctest::Approx(kItR0 - (0.0123 * 20000.0)));
    CHECK(s.lambda() == doctest::Approx(0.0).epsilon(1e-9));
  }
  SUBCASE("default inertia_term is commitment")
  {
    auto c = rdr_inertia_term_case("commitment", "", kItRelaxedUc);
    const auto needle = std::string {R"(, "inertia_term": "commitment")"};
    auto json = c.str();
    json.erase(json.find(needle), needle.size());
    PlanningLP plp(parse_planning_json(json));
    REQUIRE(plp.resolve().has_value());
    CHECK(plp.systems()
              .front()
              .front()
              .elements<ReserveZoneLP>()
              .front()
              .inertia_term()
          == ReserveZoneLP::InertiaTerm::commitment);
  }
  SUBCASE("commitment: an uncommitted unit counts only when forced on")
  {
    // No SimpleCommitment: hydro has no status column and pmin = 0 → 0.
    const RdrSolved s(rdr_inertia_term_case("commitment", "", ""));
    CHECK(s.ureq("cpf10s") == doctest::Approx(kItR0));
    CHECK(s.lambda() == doctest::Approx(0.0).epsilon(1e-9));
  }
  SUBCASE("exogenous with system_inertia: data on the RHS")
  {
    const RdrSolved s(rdr_inertia_term_case(
        "exogenous", R"(, "system_inertia": 15000)", kItRelaxedUc));
    CHECK(s.ureq("cpf10s") == doctest::Approx(kItR0 - (0.0123 * 15000.0)));
    CHECK_FALSE(s.zone("cpf10s")
                    .lookup_inertia_col(s.scenario(), s.stage(), s.buid(0))
                    .has_value());
    CHECK(s.lambda() == doctest::Approx(0.0).epsilon(1e-9));
  }
  SUBCASE("exogenous fallback: only must-run units count")
  {
    // Relaxed, not must-run: conservative I = 0.
    const RdrSolved s0(rdr_inertia_term_case("exogenous", "", kItRelaxedUc));
    CHECK(s0.ureq("cpf10s") == doctest::Approx(kItR0));
    // must_run pins u = 1: I = I_c.
    const RdrSolved s1(rdr_inertia_term_case(
        "exogenous",
        "",
        R"({"uid": 1, "name": "uc_hydro", "generator": "hydro",
            "relax": true, "must_run": true})"));
    CHECK(s1.ureq("cpf10s") == doctest::Approx(kItR0 - (0.0123 * 20000.0)));
    CHECK(s1.lambda() == doctest::Approx(0.0).epsilon(1e-9));
  }
  SUBCASE(
      "endogenous (PLEXOS-compat): dispatch inertia prices hydro "
      "negative")
  {
    // I = pf·r, r ≤ p = 1 000: I = 10 000, R = R0 − 123.  The coupling
    // row r ≤ p binds, so λ = 0 − pf·|γ|·μ = −10·0.0123·100 = −12.3.
    const RdrSolved s(rdr_inertia_term_case("endogenous", "", kItRelaxedUc));
    CHECK(s.ureq("cpf10s") == doctest::Approx(kItR0 - (0.0123 * 10000.0)));
    CHECK(s.lambda() == doctest::Approx(-12.3));
  }
  SUBCASE("unknown inertia_term throws")
  {
    CHECK_THROWS(RdrSolved {rdr_inertia_term_case("bogus", "", "")});
  }
}

// ─── validate_planning: plane support and dispatch coupling ────────────────

namespace
{
[[nodiscard]] ValidationResult rdr_validate(const RdrCase& c,
                                            std::string_view mode = "strict",
                                            std::string_view coupling = "")
{
  auto json = c.str();
  const std::string anchor = R"("annual_discount_rate": 0.0,)";
  json.replace(json.find(anchor),
               anchor.size(),
               std::format(R"("annual_discount_rate": 0.0,
                              "constraint_mode": "{}",)",
                           mode));
  if (!coupling.empty()) {
    const std::string mo = R"("model_options": {)";
    json.replace(json.find(mo),
                 mo.size(),
                 std::format(R"("model_options": {{ "reserve_coupling": "{}",)",
                             coupling));
  }
  auto planning = parse_planning_json(json);
  return validate_planning(planning);
}

[[nodiscard]] bool any_contains(const std::vector<std::string>& msgs,
                                std::string_view needle)
{
  return std::ranges::any_of(
      msgs, [&](const std::string& m) { return m.contains(needle); });
}
}  // namespace

TEST_CASE("Reserve planes validation: missing support and sign")  // NOLINT
{
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 10})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 1, "capacity": 100})";
  SUBCASE("inertia_coef without an inertia source")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z",
        "urreq_planes": [{"alpha": 10, "inertia_coef": -0.01}]})";
    CHECK(any_contains(rdr_validate(c).errors, "no inertia source"));
    const auto normal = rdr_validate(c, "normal");
    CHECK(normal.errors.empty());
    CHECK(any_contains(normal.warnings, "no inertia source"));
  }
  SUBCASE("inertia_coef with an exogenous system_inertia is supported")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z", "inertia_term": "exogenous",
        "system_inertia": 1000,
        "urreq_planes": [{"alpha": 10, "inertia_coef": -0.01}]})";
    CHECK(rdr_validate(c).errors.empty());
  }
  SUBCASE("contingency_coef without candidates")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z",
        "urreq_planes": [{"alpha": 10, "contingency_coef": 1.25}]})";
    CHECK(any_contains(rdr_validate(c).errors, "no up contingency"));
  }
  SUBCASE("flow_coef without flow_lines")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z",
        "urreq_planes": [{"alpha": 10, "flow_coef": 1}]})";
    CHECK(any_contains(rdr_validate(c).errors, "no flow_lines"));
  }
  SUBCASE("negative contingency_coef")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z",
        "contingency_generators": ["g1"],
        "urreq_planes": [{"alpha": 10, "contingency_coef": -1}]})";
    CHECK(any_contains(rdr_validate(c).errors, "must be >= 0"));
  }
  SUBCASE("eq sense with planes and a surplus cost")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z", "requirement_sense": "eq",
        "ursurplus_cost": 5, "urreq_planes": [{"alpha": 10}]})";
    const auto r = rdr_validate(c);
    CHECK(r.errors.empty());
    CHECK(any_contains(r.warnings, "absorbs any excess"));
  }
}

TEST_CASE("Reserve planes validation: dispatch coupling channels")  // NOLINT
{
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 10})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 1, "capacity": 100})";
  c.inertia_zones = R"({"uid": 1, "name": "sen"})";
  c.inertia_provisions =
      R"({"uid": 1, "name": "ip", "generator": "g1",
          "inertia_zones": ["sen"], "provision_factor": 1})";
  SUBCASE("clean default: exogenous load, commitment inertia, no UC")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z", "inertia_zone": "sen",
        "contingency_generators": ["g1"],
        "urreq_planes": [{"alpha": 10, "demand_coef": -0.03,
                          "inertia_coef": -0.01, "contingency_coef": 1}]})";
    const auto r = rdr_validate(c);
    CHECK(r.errors.empty());
    CHECK_FALSE(any_contains(r.warnings, "couples to dispatch"));
  }
  const auto* const coupled = R"({"uid": 1, "name": "z", "inertia_zone": "sen",
        "load_term": "endogenous_generation", "inertia_term": "endogenous",
        "contingency_generators": ["g1"],
        "contingency_includes_reserve": true,
        "urreq_planes": [{"alpha": 10, "demand_coef": -0.03,
                          "inertia_coef": -0.01, "contingency_coef": 1}]})";
  SUBCASE("warn (default) lists every channel")
  {
    c.reserve_zones = coupled;
    const auto r = rdr_validate(c);
    CHECK(r.errors.empty());
    CHECK(any_contains(r.warnings, "endogenous_generation"));
    CHECK(any_contains(r.warnings, "inertia_term 'endogenous'"));
    CHECK(any_contains(r.warnings, "contingency_includes_reserve"));
    CHECK(any_contains(r.warnings, "negative"));
    CHECK(any_contains(r.warnings, "§5.9"));
  }
  SUBCASE("commitment inertia couples only under a relaxed Commitment")
  {
    c.reserve_zones = R"({"uid": 1, "name": "z", "inertia_zone": "sen",
        "inertia_term": "commitment",
        "urreq_planes": [{"alpha": 10, "inertia_coef": -0.01}]})";
    c.commitments = R"({"uid": 1, "name": "uc", "generator": "g1",
        "pmin": 10, "relax": false})";
    CHECK_FALSE(any_contains(rdr_validate(c).warnings, "couples to dispatch"));
    c.commitments = R"({"uid": 1, "name": "uc", "generator": "g1",
        "pmin": 10, "relax": true})";
    const auto w = rdr_validate(c).warnings;
    CHECK(any_contains(w,
                       "inertia_term 'commitment' (the default) with a "
                       "relaxed"));
    CHECK(any_contains(w, "remedy: inertia_term \"exogenous\""));
  }
  SUBCASE("error rejects the case")
  {
    c.reserve_zones = coupled;
    CHECK(any_contains(rdr_validate(c, "strict", "error").errors,
                       "couples to dispatch"));
  }
  SUBCASE("off is silent")
  {
    c.reserve_zones = coupled;
    const auto r = rdr_validate(c, "strict", "off");
    CHECK(r.errors.empty());
    CHECK_FALSE(any_contains(r.warnings, "couples to dispatch"));
  }
  SUBCASE("bad option value")
  {
    c.reserve_zones = coupled;
    CHECK(any_contains(rdr_validate(c, "strict", "maybe").errors,
                       "reserve_coupling"));
  }
}

// ─── Native reserve-credit output ──────────────────────────────────────────

TEST_CASE("Reserve credit output: endogenous load term vs exogenous")
// NOLINT
{
  // rdr_surplus_case: one plane β = 0.0366/MW, lf = 1.025, CCGT sells up
  // reserve at μ = 20 $/MW.  Endogenous: every MWh generated lowers R by
  // 0.0366·1.025 MW → the LMP drops by 0.0366·1.025·20 = 0.75030 $/MWh.
  SUBCASE("endogenous: credit = Σ β·lf·μ = the LMP shift")
  {
    const RdrSolved s(rdr_surplus_case("endogenous_generation"));
    const auto credit =
        s.zone_output("cpf10s", ReserveZoneLP::ReserveCreditName, "sol");
    REQUIRE(credit.size() == 2);
    const double expect = -0.0366 * 1.025 * s.umu("cpf10s", 1);
    CHECK(credit[1] == doctest::Approx(expect));
    CHECK(credit[1] == doctest::Approx(-0.7503));
    // The CCGT is marginal in block 2: λ = 60 + credit.
    CHECK(s.lambda(1) == doctest::Approx(60.0 + credit[1]));
    // Transfer = credit · (D / lf) · Δt (1 h): D/lf = total generation.
    const auto transfer =
        s.zone_output("cpf10s", ReserveZoneLP::CreditTransferName, "sol");
    REQUIRE(transfer.size() == 2);
    CHECK(transfer[1] == doctest::Approx(credit[1] * 4000.0));
  }
  SUBCASE("exogenous (default): credit is 0")
  {
    const RdrSolved s(rdr_surplus_case("exogenous"));
    const auto credit =
        s.zone_output("cpf10s", ReserveZoneLP::ReserveCreditName, "sol");
    REQUIRE(credit.size() == 2);
    CHECK(credit[0] == doctest::Approx(0.0));
    CHECK(credit[1] == doctest::Approx(0.0));
    CHECK(s.lambda(1) == doctest::Approx(60.0));
  }
}

// ─── Review fixes (P1-2, P1-3, P1-4, P2-3, credit units) ───────────────────

TEST_CASE("Review: provision with zone factor 0 keeps its column")  // NOLINT
{
  // rp_g lists zone 1 with factor 0: it contributes nothing to that zone,
  // but its column exists, so a UserConstraint on it is not silently 0.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 50})";
  c.generators = R"(
      {"uid": 1, "name": "g", "bus": 1, "gcost": 10, "capacity": 100},
      {"uid": 2, "name": "res", "bus": 1, "gcost": 20, "capacity": 100})";
  c.reserve_zones = R"({"uid": 1, "name": "z", "urreq": 10, "urcost": 1000})";
  c.reserve_provisions = R"(
      {"uid": 1, "name": "rp_g", "generator": "g", "reserve_zones": [1],
       "urmax": 30, "ur_provision_factor": 1, "ur_zone_factors": [0]},
      {"uid": 2, "name": "rp_res", "generator": "res", "reserve_zones": [1],
       "urmax": 30, "ur_provision_factor": 1, "urcost": 1})";
  const RdrSolved s(c);
  const auto col = s.provision("rp_g").lookup_up_provision_col(
      s.scenario(), s.stage(), s.buid(0));
  CHECK(col.has_value());
  // The requirement is met by rp_res alone (factor 0 gives no relief).
  CHECK(s.col(s.provision("rp_res").lookup_up_provision_col(
            s.scenario(), s.stage(), s.buid(0)))
        == doctest::Approx(10.0));
}

TEST_CASE("Review: offline (cold) reserve validation")  // NOLINT
{
  RdrCase c;
  c.chronological = true;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 50})";
  c.generators = R"(
      {"uid": 1, "name": "base", "bus": 1, "gcost": 10, "capacity": 100},
      {"uid": 2, "name": "peaker", "bus": 1, "gcost": 90, "capacity": 100})";
  c.reserve_zones = R"({"uid": 1, "name": "ctf", "urreq": 10, "urcost": 1000})";
  SUBCASE("no commitment: error in strict, warning in normal")
  {
    c.reserve_provisions = R"({"uid": 1, "name": "rp_cold",
        "generator": "peaker", "reserve_zones": [1], "urmax": 100,
        "ur_provision_factor": 1, "offline": true})";
    CHECK(any_contains(rdr_validate(c).errors, "needs a Commitment"));
    const auto normal = rdr_validate(c, "normal");
    CHECK(any_contains(normal.warnings, "needs a Commitment"));
  }
  SUBCASE("urmin > 0 contradicts offline")
  {
    c.commitments = R"({"uid": 1, "name": "uc", "generator": "peaker",
        "relax": true, "initial_status": 0})";
    c.reserve_provisions = R"({"uid": 1, "name": "rp_cold",
        "generator": "peaker", "reserve_zones": [1], "urmax": 100,
        "urmin": 5, "ur_provision_factor": 1, "offline": true})";
    CHECK(any_contains(rdr_validate(c).errors, "urmin > 0"));
  }
  SUBCASE("committed offline provision is valid")
  {
    c.commitments = R"({"uid": 1, "name": "uc", "generator": "peaker",
        "relax": true, "initial_status": 0})";
    c.reserve_provisions = R"({"uid": 1, "name": "rp_cold",
        "generator": "peaker", "reserve_zones": [1], "urmax": 100,
        "ur_provision_factor": 1, "offline": true})";
    const auto r = rdr_validate(c);
    CHECK_FALSE(any_contains(r.errors, "offline"));
  }
}

TEST_CASE("Review: identical contingency candidates are deduplicated")
// NOLINT
{
  // Two identical `urcontingencies` entries (and a singleton equal to a
  // one-member group) give one row each kind, not duplicates.
  RdrCase c;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 100})";
  c.generators = R"(
      {"uid": 1, "name": "big", "bus": 1, "gcost": 0, "pmin": 80,
       "pmax": 80, "capacity": 80},
      {"uid": 2, "name": "res", "bus": 1, "gcost": 10, "capacity": 500})";
  c.reserve_provisions =
      R"({"uid": 1, "name": "rp_res", "generator": "res",
          "reserve_zones": [1], "urmax": 500, "urcost": 1,
          "ur_provision_factor": 1})";
  c.reserve_zones =
      R"({"uid": 1, "name": "cpf", "urcost": 5000,
          "urreq_planes": [{"contingency_coef": 1}],
          "contingency_generators": ["big"],
          "urcontingencies": [
            {"name": "A", "generators": ["big"]},
            {"name": "B", "generators": ["big"], "generator_coefs": [1]}]})";
  const RdrSolved s(c);
  CHECK(s.ureq("cpf") == doctest::Approx(80.0));
  int crows = 0;
  const auto& li = s.li();
  for (Index i = 0; i < li.get_numrows(); ++i) {
    const auto* lbl = li.row_label_at(RowIndex {i});
    if (lbl != nullptr
        && lbl->constraint_name == ReserveZoneLP::UrcontingencyName)
    {
      ++crows;
    }
  }
  CHECK(crows == 1);
}

TEST_CASE("Review: inertia credit units must be positive")  // NOLINT
{
  RdrCase c;
  c.chronological = true;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 50})";
  c.generators =
      R"({"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100})";
  c.commitments = R"({"uid": 1, "name": "c1", "generator": "g1",
                      "relax": true})";
  c.inertia_zones = R"({"uid": 1, "name": "ng", "requirement": 100,
      "requirement_credits": [{"name": "k", "generators": ["g1"],
                               "units": 0, "credit": 50}]})";
  CHECK_THROWS_WITH_AS(PlanningLP(parse_planning_json(c.str())),
                       doctest::Contains("units must be > 0"),
                       std::invalid_argument);
}

TEST_CASE("Review: converter without its charge demand does not abort")
// NOLINT
{
  // A converter whose charge demand is not in the LP (e.g. filtered by a
  // reduced network) must not abort a planes zone that builds its
  // demand set (review P1-2): the converter is skipped.
  RdrCase c = rdr_surplus_case("exogenous");
  auto json = c.str();
  const std::string anchor = R"("bus_array": [{"uid": 1, "name": "b1"}])";
  json.replace(
      json.find(anchor),
      anchor.size(),
      std::string {anchor} + R"(, "converter_array": [{"uid": 1, "name": "cv",
                        "battery": 77, "generator": 2, "demand": 99,
                        "capacity": 10}])");
  CHECK_NOTHROW(PlanningLP(parse_planning_json(json)));
}

TEST_CASE("Review: offline reserve with an unbounded ceiling is rejected")
// NOLINT
{
  // No urmax and a generator with no pmax / capacity: the big-M of
  // `r + urmax·u ≤ urmax` would be the 1e20+ sentinel.
  RdrCase c;
  c.chronological = true;
  c.demands = R"({"uid": 1, "name": "d1", "bus": 1, "capacity": 50})";
  c.generators = R"(
      {"uid": 1, "name": "base", "bus": 1, "gcost": 10, "capacity": 100},
      {"uid": 2, "name": "peaker", "bus": 1, "gcost": 90})";
  c.commitments = R"({"uid": 1, "name": "uc", "generator": "peaker",
      "relax": true, "initial_status": 0})";
  c.reserve_zones = R"({"uid": 1, "name": "ctf", "urreq": 10, "urcost": 1000})";
  c.reserve_provisions = R"({"uid": 1, "name": "rp_cold",
      "generator": "peaker", "reserve_zones": [1],
      "ur_provision_factor": 1, "offline": true})";
  CHECK_THROWS_WITH(PlanningLP(parse_planning_json(c.str())),
                    doctest::Contains("finite ceiling"));
}
