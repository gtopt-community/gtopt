// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sddp_cut_exactness.cpp
 * @brief     SDDP cuts are formulation-exact under every LP scaling
 * @date      2026-11-22
 *
 * Small hydrothermal systems whose future-cost functions are derived BY
 * HAND, so every Benders cut gtopt produces can be checked against the
 * exact value function in physical units ($ and $/hm³):
 *
 *  - validity: every optimality cut `α ≥ a + Σ βᵢ·vᵢ` lies below the true
 *    cost-to-go V(v) on the whole state box (no overshoot, LB ≤ optimum);
 *  - exactness: at the (unique) optimal policy v* the active cut touches
 *    V, `a + β·v* = V(v*)`, and its slopes are the analytic water values
 *    `βᵢ = −ρᵢ·λ` (λ = marginal price, $/MWh; ρᵢ = MWh/hm³);
 *  - invariance: LB, UB, the active cuts and the policy are identical for
 *    `equilibration_method ∈ {none, row_max, ruiz}`, `scale_objective`,
 *    `scale_theta` and explicit reservoir `variable_scales` — and equal
 *    the analytic and the single-LP (monolithic) optimum;
 *  - persistence: cuts saved under one equilibration and re-loaded under
 *    another reproduce the converged LB on the first forward pass;
 *  - boundary cuts: a terminal FCF built from gtopt's own cuts reproduces
 *    the full-horizon optimum; a foreign (overshooting) FCF yields the
 *    same LB, UB and LB − UB under every equilibration.  In both cases
 *    the reported `LB − UB` equals the terminal FCF term the UB omits
 *    (`Φ(v_T) − c̄`, §9 of docs/formulation/sddp-cut-validity.md), i.e. a
 *    negative gap is an accounting property of boundary cuts, not of
 *    scaling.
 *
 * Fixture conventions: stages of `kDur` = 1000 h split into two blocks
 * (400 h + 600 h, same MW data in both) and one stage per SDDP phase; `ρ`
 * MWh/hm³ (production factor `ρ · 0.0036` MW per m³/s); no inflow after
 * stage 1 unless stated, so the water of all remaining stages is a
 * single pool and the cost-to-go is the thermal cost minus the most
 * expensive E = Σρᵢvᵢ MWh it can displace.  Per-block costs are
 * identical within a stage, so (by convexity) the block split never
 * changes a stage's cost and the hand formulas use the 10 h totals.
 */

#include <algorithm>
#include <array>
#include <cmath>
#include <filesystem>
#include <format>
#include <fstream>
#include <functional>
#include <limits>
#include <optional>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/monolithic_method.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/sddp_types.hpp>

using namespace gtopt;

namespace
{

constexpr double kDur = 1000.0;  ///< stage duration [h]
/// First of the two blocks of every stage [h].  Long, unequal blocks
/// (flow coefficients fcr·d = 1.44 and 2.16 in the water balances, as in
/// monthly PLP stages) make the last-block `efin` (source) and the
/// first-block `sini` (dependent) columns sit in rows with different
/// norms, so ruiz gives them different scales — the bug trigger.
constexpr double kDurA = 400.0;
constexpr double kFcr = 0.0036;  ///< hm³ per (m³/s · h)
constexpr double kFailCost = 5000.0;  ///< demand fail cost [$/MWh]
constexpr double kBig = 1.0e6;
constexpr int kRsvUid = 400;  ///< reservoir uid base

constexpr std::array<std::string_view, 3> kEquilibrations {
    "none",
    "row_max",
    "ruiz",
};

// ─── Case description ──────────────────────────────────────────────────

struct Thermal
{
  std::string name;
  std::string bus;
  std::vector<double> gcost;  ///< per stage [$/MWh]
  std::vector<double> pmax;  ///< per stage [MW]
};

struct Hydro
{
  std::string name;
  std::string bus;
  double rho;  ///< MWh/hm³
  double vmax;  ///< emax [hm³]
  double v0;  ///< eini [hm³]
  std::vector<double> inflow;  ///< per stage [hm³ over the stage]
  std::vector<double> pmax;  ///< per stage turbine capacity [MW]
  bool spill {false};  ///< free spill waterway to the drain
  /// Per-scenario inflows (empty = `inflow` in every scenario).
  std::vector<std::vector<double>> inflow_by_scenario {};
};

struct Load
{
  std::string bus;
  std::vector<double> mw;  ///< per stage
};

struct Line
{
  std::string bus_a;
  std::string bus_b;
  double tmax;
};

struct Case
{
  int stages {2};
  std::vector<std::string> buses {"b1"};
  std::vector<Thermal> thermals {};
  std::vector<Hydro> hydros {};
  std::vector<Load> loads {};
  std::vector<Line> lines {};
  /// Stages per SDDP phase (empty = one stage per phase).
  std::vector<int> phase_stages {};
  /// Scenario probabilities, one scene per scenario (empty = 1 scenario).
  std::vector<double> probs {};
};

[[nodiscard]] auto phase_sizes(const Case& c) -> std::vector<int>
{
  return c.phase_stages.empty()
      ? std::vector<int>(static_cast<std::size_t>(c.stages), 1)
      : c.phase_stages;
}

/// LP-scaling knobs that must NOT change any physical result.
struct Knobs
{
  std::string_view eq {"none"};
  double scale_objective {1000.0};
  std::optional<double> scale_theta {};
  /// Explicit, very different `Reservoir.energy` scales per reservoir.
  bool explicit_scales {false};
  /// Boundary (future-cost) cut CSV; empty = none.
  std::string fcf_file {};
  bool mean_shift {false};
  std::string_view fcf_mode {"combined"};
  /// Backward pass over one aperture per scenario (needs `probs`).
  bool apertures {false};
  CutSharingMode cut_sharing {CutSharingMode::none};
};

[[nodiscard]] auto stage_sched(const std::vector<double>& v) -> std::string
{
  std::string s = "[";
  for (const double x : v) {
    s += std::format("[{},{}],", x, x);  // same value in both blocks
  }
  s.back() = ']';
  return s;
}

/// `phases == 1`: every stage in one phase (single LP, monolithic);
/// otherwise one stage per phase (SDDP).
[[nodiscard]] auto make_json(const Case& c, const Knobs& k, bool single_lp)
    -> std::string
{
  const bool network = c.buses.size() > 1;
  std::string blocks;
  std::string stages;
  std::string phases;
  for (int s = 0; s < c.stages; ++s) {
    blocks +=
        std::format(R"({{"uid":{},"duration":{}}},{{"uid":{},"duration":{}}},)",
                    (2 * s) + 1,
                    kDurA,
                    (2 * s) + 2,
                    kDur - kDurA);
    stages += std::format(
        R"({{"uid":{},"first_block":{},"count_block":2}},)", s + 1, 2 * s);
  }
  if (!single_lp) {
    int first = 0;
    int uid = 1;
    for (const int n : phase_sizes(c)) {
      std::string aps;
      if (k.apertures) {
        for (std::size_t sc = 0; sc < c.probs.size(); ++sc) {
          aps += std::format("{},", sc + 1);
        }
        if (!aps.empty()) {
          aps.pop_back();
        }
        aps = std::format(R"(,"apertures":[{}])", aps);
      }
      phases +=
          std::format(R"({{"uid":{},"first_stage":{},"count_stage":{}{}}},)",
                      uid++,
                      first,
                      n,
                      aps);
      first += n;
    }
  } else {
    phases = std::format(R"({{"uid":1,"first_stage":0,"count_stage":{}}},)",
                         c.stages);
  }
  blocks.pop_back();
  stages.pop_back();
  phases.pop_back();

  std::string buses;
  for (const auto& b : c.buses) {
    buses += std::format(
        R"({{"uid":{},"name":"{}"}},)", 1 + (&b - c.buses.data()), b);
  }
  buses.pop_back();

  std::string gens;
  int guid = 1;
  for (const auto& t : c.thermals) {
    gens +=
        std::format(R"({{"uid":{},"name":"{}","bus":"{}","gcost":{},"pmax":{},)"
                    R"("capacity":{}}},)",
                    guid++,
                    t.name,
                    t.bus,
                    stage_sched(t.gcost),
                    stage_sched(t.pmax),
                    std::ranges::max(t.pmax));
  }

  std::string junctions;
  std::string waterways;
  std::string turbines;
  std::string reservoirs;
  std::string flows;
  std::string vscales;
  for (std::size_t i = 0; i < c.hydros.size(); ++i) {
    const auto& h = c.hydros[i];
    const auto ii = static_cast<int>(i);
    const double pf = h.rho * kFcr;  // MW per m³/s
    gens += std::format(
        R"({{"uid":{},"name":"h_{}","bus":"{}","gcost":0.0,"pmax":{},)"
        R"("capacity":{}}},)",
        50 + ii,
        h.name,
        h.bus,
        stage_sched(h.pmax),
        std::ranges::max(h.pmax));
    junctions += std::format(
        R"({{"uid":{},"name":"j_{}"}},{{"uid":{},"name":"d_{}","drain":true}},)",
        100 + (2 * ii),
        h.name,
        101 + (2 * ii),
        h.name);
    waterways += std::format(
        R"({{"uid":{},"name":"w_{}","junction_a":{},"junction_b":{},)"
        R"("fmin":0.0,"fmax":{}}},)",
        200 + ii,
        h.name,
        100 + (2 * ii),
        101 + (2 * ii),
        kBig);
    if (h.spill) {
      waterways += std::format(
          R"({{"uid":{},"name":"s_{}","junction_a":{},"junction_b":{},)"
          R"("fmin":0.0,"fmax":{}}},)",
          250 + ii,
          h.name,
          100 + (2 * ii),
          101 + (2 * ii),
          kBig);
    }
    turbines +=
        std::format(R"({{"uid":{},"name":"t_{}","waterway":{},"generator":{},)"
                    R"("production_factor":{}}},)",
                    300 + ii,
                    h.name,
                    200 + ii,
                    50 + ii,
                    pf);
    reservoirs += std::format(
        R"({{"uid":{},"name":"{}","junction":{},"capacity":{},"emin":0.0,)"
        R"("emax":{},"eini":{},"fmin":{},"fmax":{},)"
        R"("flow_conversion_rate":{}}},)",
        kRsvUid + ii,
        h.name,
        100 + (2 * ii),
        h.vmax,
        h.vmax,
        h.v0,
        -kBig,
        kBig,
        kFcr);
    std::string discharge;
    for (std::size_t sc = 0; sc < std::max<std::size_t>(1, c.probs.size());
         ++sc)
    {
      const auto& vols =
          h.inflow_by_scenario.empty() ? h.inflow : h.inflow_by_scenario.at(sc);
      std::vector<double> q;
      q.reserve(vols.size());
      for (const double vol : vols) {
        q.push_back(vol / (kDur * kFcr));  // hm³ over the stage → m³/s
      }
      discharge += stage_sched(q) + ",";
    }
    discharge.pop_back();
    flows +=
        std::format(R"({{"uid":{},"name":"in_{}","direction":1,"junction":{},)"
                    R"("discharge":[{}]}},)",
                    500 + ii,
                    h.name,
                    100 + (2 * ii),
                    discharge);
    // 1e1 and 1e-2: three decades apart, so the source (efin) and
    // dependent (sini) columns of different reservoirs carry very
    // different scales even before ruiz multiplies its own factors on.
    vscales += std::format(
        R"({{"class_name":"Reservoir","variable":"energy","uid":{},)"
        R"("scale":{}}},)",
        kRsvUid + ii,
        (i % 2 == 0) ? 10.0 : 0.01);
  }
  gens.pop_back();

  std::string loads;
  for (std::size_t i = 0; i < c.loads.size(); ++i) {
    loads +=
        std::format(R"({{"uid":{},"name":"load{}","bus":"{}","lmax":{}}},)",
                    i + 1,
                    i + 1,
                    c.loads[i].bus,
                    stage_sched(c.loads[i].mw));
  }
  loads.pop_back();

  std::string lines;
  for (std::size_t i = 0; i < c.lines.size(); ++i) {
    const auto& l = c.lines[i];
    lines += std::format(R"({{"uid":{},"name":"l{}","bus_a":"{}","bus_b":"{}",)"
                         R"("reactance":0.001,"tmax_ab":{},"tmax_ba":{}}},)",
                         i + 1,
                         i + 1,
                         l.bus_a,
                         l.bus_b,
                         l.tmax,
                         l.tmax);
  }
  if (!lines.empty()) {
    lines.pop_back();
  }

  std::string hydro_arrays;
  if (!c.hydros.empty()) {
    junctions.pop_back();
    waterways.pop_back();
    turbines.pop_back();
    reservoirs.pop_back();
    flows.pop_back();
    hydro_arrays = std::format(
        R"(,"junction_array":[{}],"waterway_array":[{}],)"
        R"("reservoir_array":[{}],"turbine_array":[{}],"flow_array":[{}])",
        junctions,
        waterways,
        reservoirs,
        turbines,
        flows);
  }
  if (!vscales.empty()) {
    vscales.pop_back();
  }

  std::string model = std::format(
      R"("use_single_bus":{},"use_kirchhoff":{},"demand_fail_cost":{},)"
      R"("scale_objective":{})",
      network ? "false" : "true",
      network ? "true" : "false",
      kFailCost,
      k.scale_objective);
  if (k.scale_theta) {
    model += std::format(R"(,"scale_theta":{})", *k.scale_theta);
  }
  const std::string extra_options = k.explicit_scales
      ? std::format(R"(,"variable_scales":[{}])", vscales)
      : std::string {};
  const std::string fcf = k.fcf_file.empty()
      ? std::string {}
      : std::format(
            R"(,"future_cost_array":[{{"uid":1,"name":"fcf","cuts_file":"{}",)"
            R"("mean_shift":{},"mode":"{}"}}])",
            k.fcf_file,
            k.mean_shift ? "true" : "false",
            k.fcf_mode);

  std::string scenarios = R"({"uid":1})";
  if (!c.probs.empty()) {
    scenarios.clear();
    std::string scenes;
    for (std::size_t sc = 0; sc < c.probs.size(); ++sc) {
      scenarios += std::format(
          R"({{"uid":{},"probability_factor":{}}},)", sc + 1, c.probs[sc]);
      scenes +=
          std::format(R"({{"uid":{},"first_scenario":{},"count_scenario":1}},)",
                      sc + 1,
                      sc);
    }
    scenarios.pop_back();
    scenes.pop_back();
    scenarios += std::format(R"(],"scene_array":[{})", scenes);
    if (k.apertures && !single_lp) {
      std::string aps;
      for (std::size_t sc = 0; sc < c.probs.size(); ++sc) {
        aps += std::format(
            R"({{"uid":{},"source_scenario":{},"probability_factor":{}}},)",
            sc + 1,
            sc + 1,
            c.probs[sc]);
      }
      aps.pop_back();
      scenarios += std::format(R"(],"aperture_array":[{})", aps);
    }
  }

  return std::format(
      R"({{"options":{{"method":"{}","model_options":{{{}}},)"
      R"("lp_matrix_options":{{"equilibration_method":"{}"}}{}}},)"
      R"("simulation":{{"block_array":[{}],"stage_array":[{}],)"
      R"("scenario_array":[{}],"phase_array":[{}]}},)"
      R"("system":{{"name":"cut_exact","bus_array":[{}],)"
      R"("generator_array":[{}],"demand_array":[{}]{}{}{}}}}})",
      single_lp ? "monolithic" : "sddp",
      model,
      k.eq,
      extra_options,
      blocks,
      stages,
      scenarios,
      phases,
      buses,
      gens,
      loads,
      lines.empty() ? std::string {}
                    : std::format(R"(,"line_array":[{}])", lines),
      hydro_arrays,
      fcf);
}

// ─── Analytic value functions ─────────────────────────────────────────

/// Thermal energy block dispatched when no hydro is available.
struct Unit
{
  double cost;  ///< $/MWh
  double mwh;
};

/// Pooled cost-to-go: thermal cost of `units` after E MWh of free hydro
/// displaced the most expensive MWh (water can be carried to any later
/// stage at no loss, so displacement is greedy by price).
[[nodiscard]] auto pooled_cost(std::vector<Unit> units, double energy) -> double
{
  std::ranges::sort(units, std::greater {}, &Unit::cost);
  double total = 0.0;
  double rem = std::max(energy, 0.0);
  for (const auto& u : units) {
    const double take = std::min(rem, u.mwh);
    total += u.cost * (u.mwh - take);
    rem -= take;
  }
  return total;
}

/// Energy breakpoints of `pooled_cost` (0 and every cumulative sum).
[[nodiscard]] auto pooled_breaks(std::vector<Unit> units) -> std::vector<double>
{
  std::ranges::sort(units, std::greater {}, &Unit::cost);
  std::vector<double> b {0.0};
  double acc = 0.0;
  for (const auto& u : units) {
    acc += u.mwh;
    b.push_back(acc);
  }
  return b;
}

/// A cost-to-go V(v) of the reservoir volumes, given as V(v) = f(Σρᵢvᵢ)
/// with f convex piecewise-linear (breakpoints `breaks` in MWh), plus
/// the state box.
struct ValueFn
{
  std::function<double(double)> f {};  ///< of stored energy E [MWh]
  std::vector<double> breaks {};  ///< kinks of f [MWh]
  std::vector<double> rho {};  ///< per reservoir
  std::vector<double> vmax {};  ///< per reservoir

  [[nodiscard]] auto energy(const std::vector<double>& v) const -> double
  {
    double e = 0.0;
    for (std::size_t i = 0; i < v.size(); ++i) {
      e += rho[i] * v[i];
    }
    return e;
  }
};

/// Physical optimality cut `α ≥ a + Σ βᵢ vᵢ` (one β per reservoir).
struct Cut
{
  double a {};
  std::vector<double> beta {};
  int iteration {};

  [[nodiscard]] auto at(const std::vector<double>& v) const -> double
  {
    double r = a;
    for (std::size_t i = 0; i < v.size(); ++i) {
      r += beta[i] * v[i];
    }
    return r;
  }
};

/// max over the state box of `cut(v) − V(v)`.  `cut − V` is concave and
/// piecewise linear on the subdivision of the box by the hyperplanes
/// Σρᵢvᵢ = break, so its maximum is attained at a vertex of that
/// subdivision: box corners and the intersections of each hyperplane
/// with the box edges (exact for 1 and 2 reservoirs).
[[nodiscard]] auto max_overshoot(const Cut& cut, const ValueFn& vf) -> double
{
  const auto n = vf.rho.size();
  REQUIRE((n == 1 || n == 2));
  std::vector<std::vector<double>> pts;
  if (n == 1) {
    pts.push_back({0.0});
    pts.push_back({vf.vmax[0]});
    for (const double e : vf.breaks) {
      const double v = e / vf.rho[0];
      if (v > 0.0 && v < vf.vmax[0]) {
        pts.push_back({v});
      }
    }
  } else {
    for (const double x : {0.0, vf.vmax[0]}) {
      for (const double y : {0.0, vf.vmax[1]}) {
        pts.push_back({x, y});
      }
    }
    for (const double e : vf.breaks) {
      for (std::size_t fix = 0; fix < 2; ++fix) {
        const std::size_t free = 1 - fix;
        for (const double vfix : {0.0, vf.vmax[fix]}) {
          const double vfree = (e - (vf.rho[fix] * vfix)) / vf.rho[free];
          if (vfree > 0.0 && vfree < vf.vmax[free]) {
            std::vector<double> p(2);
            p[fix] = vfix;
            p[free] = vfree;
            pts.push_back(p);
          }
        }
      }
    }
  }
  double worst = -std::numeric_limits<double>::infinity();
  for (const auto& p : pts) {
    worst = std::max(worst, cut.at(p) - vf.f(vf.energy(p)));
  }
  return worst;
}

// ─── Running gtopt ────────────────────────────────────────────────────

/// Single-LP optimum of the whole horizon (no Benders cuts involved).
[[nodiscard]] auto monolithic_optimum(const Case& c, const Knobs& k) -> double
{
  PlanningLP plp(parse_planning_json(make_json(c, k, /*single_lp=*/true)));
  MonolithicMethod solver;
  auto res = solver.solve(plp, {});
  REQUIRE(res.has_value());
  double obj = 0.0;  // Σ over scenes (each folds its probability)
  const auto num_scenes = plp.simulation().scene_count();
  for (Index s = 0; s < static_cast<Index>(num_scenes); ++s) {
    obj += plp.system(SceneIndex {s}, first_phase_index())
               .linear_interface()
               .get_obj_value();
  }
  return obj;
}

struct SddpRun
{
  std::vector<SDDPIterationResult> iters;
  /// Optimality cuts per source phase (physical, from the cut store).
  std::vector<std::vector<Cut>> cuts;
  /// Final policy: efin per (phase, reservoir) [hm³].
  std::vector<std::vector<double>> efin;
  double alpha_offset {0.0};  ///< mean-shift c̄ (scene 0)
  int scale_mismatches {0};  ///< state links with s_src != s_dep
  /// Max |LP readback − stored| over cut rhs / coefficients (relative).
  double max_readback_err {0.0};
  /// Cut coefficients landing on neither α nor a phase-state column.
  int stray_coefs {0};
};

[[nodiscard]] auto sddp_options(int max_iterations) -> SDDPOptions
{
  SDDPOptions opts;
  opts.max_iterations = max_iterations;
  opts.convergence_tol = 1.0e-9;
  opts.cut_sharing = CutSharingMode::none;
  opts.enable_api = false;
  return opts;
}

/// Reservoir index of an `efin` state key, or nullopt.
template<typename Key>
[[nodiscard]] auto rsv_index(const Key& key, std::size_t n)
    -> std::optional<std::size_t>
{
  if (key.col_name != "efin" || key.uid < kRsvUid) {
    return std::nullopt;
  }
  const auto idx = static_cast<std::size_t>(key.uid - kRsvUid);
  return idx < n ? std::optional {idx} : std::nullopt;
}

[[nodiscard]] auto collect(const PlanningLP& plp,
                           const SDDPMethod& sddp,
                           const Case& c,
                           SceneIndex scene = first_scene_index()) -> SddpRun
{
  SddpRun out;
  const auto& sim = plp.simulation();
  const auto nr = c.hydros.size();
  const auto cuts = sddp.stored_cuts();
  const auto np = phase_sizes(c).size();
  out.cuts.resize(np);
  out.efin.assign(np, std::vector<double>(nr, 0.0));
  out.alpha_offset = sddp.scene_alpha_offset(scene);

  for (std::size_t p = 0; p < np; ++p) {
    const PhaseIndex phase {static_cast<Index>(p)};
    const auto& li = plp.system(scene, phase).linear_interface();
    const auto& svars = sim.state_variables(scene, phase);
    // The phase's state is the LAST stage's efin (earlier stages of a
    // multi-stage phase register their own, purely internal, efin).
    const auto last_stage = sim.phases()[phase].stages().back().uid();

    // efin column per reservoir + final policy + src/dep scale check.
    std::vector<ColIndex> col(nr, ColIndex {unknown_index});
    for (const auto& [key, svar] : svars) {
      const auto idx = rsv_index(key, nr);
      if (!idx || key.stage_uid != last_stage) {
        continue;
      }
      col[*idx] = svar.col();
      out.efin[p][*idx] = li.get_col_sol()[svar.col()];
      const double s_src = li.get_col_scale(svar.col());
      for (const auto& dep : svar.dependent_variables()) {
        const double s_dep = plp.system(scene, dep.phase_index())
                                 .linear_interface()
                                 .get_col_scale(dep.col());
        if (std::abs(s_src - s_dep) > 1e-9 * std::abs(s_src)) {
          ++out.scale_mismatches;
        }
      }
    }

    const auto phase_uid = sim.uid_of(phase);
    for (const auto& sc : cuts) {
      if (sc.type != CutType::Optimality || sc.phase_uid != phase_uid
          || sc.scene_uid != sim.uid_of(scene))
      {
        continue;
      }
      const auto* alpha = find_alpha_state_var(sim, scene, phase);
      REQUIRE(alpha != nullptr);
      double alpha_coef = 0.0;
      for (const auto& [cc, coef] : sc.coefficients) {
        if (cc == alpha->col()) {
          alpha_coef = coef;
        }
      }
      REQUIRE(alpha_coef > 0.0);
      // Row: α·w + Σ cᵢ vᵢ ≥ rhs  ⇒  α ≥ rhs/w − Σ (cᵢ/w) vᵢ.
      Cut cut {
          .a = sc.rhs / alpha_coef,
          .beta = std::vector<double>(nr, 0.0),
          .iteration = static_cast<int>(sc.iteration_index.value_of()),
      };
      const auto rows = li.get_row_low();
      const double rhs_lp = rows[sc.row];
      out.max_readback_err =
          std::max(out.max_readback_err,
                   std::abs(rhs_lp - sc.rhs) / std::max(1.0, std::abs(sc.rhs)));
      for (const auto& [cc, coef] : sc.coefficients) {
        const bool is_state = std::ranges::contains(col, cc);
        if (!is_state && cc != alpha->col() && coef != 0.0) {
          ++out.stray_coefs;  // a coefficient on a non-state column
        }
        for (std::size_t i = 0; i < nr; ++i) {
          if (cc == col[i]) {
            cut.beta[i] = -coef / alpha_coef;
            const double coef_lp = li.get_coeff(sc.row, cc);
            out.max_readback_err = std::max(
                out.max_readback_err,
                std::abs(coef_lp - coef) / std::max(1.0, std::abs(coef)));
          }
        }
      }
      out.cuts[p].push_back(std::move(cut));
    }
  }
  return out;
}

[[nodiscard]] auto run_sddp(const Case& c,
                            const Knobs& k,
                            int max_iterations = 30) -> SddpRun
{
  PlanningLP plp(parse_planning_json(make_json(c, k, /*single_lp=*/false)));
  auto opts = sddp_options(max_iterations);
  opts.cut_sharing = k.cut_sharing;
  SDDPMethod sddp(plp, opts);
  auto results = sddp.solve();
  REQUIRE(results.has_value());
  REQUIRE_FALSE(results->empty());
  auto out = collect(plp, sddp, c);
  out.iters = std::move(*results);
  return out;
}

/// Cut of `cuts` with the largest value at `v` (the active one).
[[nodiscard]] auto active_cut(const std::vector<Cut>& cuts,
                              const std::vector<double>& v) -> const Cut&
{
  REQUIRE_FALSE(cuts.empty());
  const Cut* best = cuts.data();
  for (const auto& c : cuts) {
    if (c.at(v) > best->at(v)) {
      best = &c;
    }
  }
  return *best;
}

/// Expected (analytic) shape of one SDDP-relevant phase.
struct PhaseTruth
{
  ValueFn vf;  ///< true cost-to-go of the NEXT phases, function of efin
  std::vector<double> v_star;  ///< optimal efin (empty = not unique)
  double e_star {};  ///< optimal stored energy Σρᵢvᵢ* [MWh]
  double lambda {};  ///< unique marginal price at e_star [$/MWh]
};

/// Check every phase's cuts against the analytic value function.
void check_cuts(const SddpRun& run,
                const std::vector<PhaseTruth>& truth,
                double opt)
{
  const double tol = 1e-7 * std::max(1.0, std::abs(opt));
  for (std::size_t p = 0; p < truth.size(); ++p) {
    const auto& t = truth[p];
    const auto& cuts = run.cuts[p];
    CAPTURE(p);
    REQUIRE_FALSE(cuts.empty());
    // 1. validity: no cut overshoots the true cost-to-go anywhere.
    for (const auto& cut : cuts) {
      CAPTURE(cut.iteration);
      CAPTURE(cut.a);
      CHECK(max_overshoot(cut, t.vf) <= tol);
    }
    // 2. exactness at the optimal policy: the active cut touches V and
    //    its slopes are the analytic water values −ρᵢ·λ.
    std::vector<double> v = run.efin[p];
    CHECK(t.vf.energy(v) == doctest::Approx(t.e_star).epsilon(1e-7));
    for (std::size_t i = 0; i < t.v_star.size(); ++i) {
      CHECK(v[i] == doctest::Approx(t.v_star[i]).epsilon(1e-7));
    }
    const auto& act = active_cut(cuts, v);
    CAPTURE(act.iteration);
    CHECK(act.at(v) == doctest::Approx(t.vf.f(t.e_star)).epsilon(1e-8));
    for (std::size_t i = 0; i < act.beta.size(); ++i) {
      CAPTURE(i);
      // A reservoir pinned at emin can legally carry a steeper slope
      // (normal cone of v ≥ 0); interior reservoirs must be exact.
      if (v[i] > 1e-6 * t.vf.vmax[i]) {
        CHECK(act.beta[i]
              == doctest::Approx(-t.vf.rho[i] * t.lambda).epsilon(1e-8));
      } else {
        CHECK(act.beta[i] <= -t.vf.rho[i] * t.lambda * (1.0 - 1e-8));
      }
    }
  }
}

/// Converged SDDP must equal the analytic optimum, never overshoot it on
/// any iteration, and read its cuts back from the LP unchanged.
///
/// `first_valid_lb`: iterations before it are exempt from `LB ≤ opt`.
/// Iteration 0 always solves the first phase with the α bootstrap pin
/// `α = 0`; that is a valid lower bound only when every cost-to-go is
/// ≥ 0 (assumption A2 of docs/formulation/sddp-cut-validity.md §9).
void check_bounds(const SddpRun& run, double opt, int first_valid_lb = 0)
{
  REQUIRE_FALSE(run.iters.empty());
  const auto& last = run.iters.back();
  CAPTURE(last.lower_bound);
  CAPTURE(last.upper_bound);
  CHECK(last.converged);
  CHECK(last.lower_bound == doctest::Approx(opt).epsilon(1e-8));
  CHECK(last.upper_bound == doctest::Approx(opt).epsilon(1e-8));
  for (const auto& it : run.iters) {
    const auto iter = static_cast<int>(it.iteration_index.value_of());
    CAPTURE(iter);
    if (iter >= first_valid_lb) {
      CHECK(it.lower_bound <= opt + (1e-8 * std::max(1.0, std::abs(opt))));
    }
    CHECK(it.upper_bound >= opt - (1e-8 * std::max(1.0, std::abs(opt))));
  }
  CHECK(run.max_readback_err < 1e-9);
  CHECK(run.stray_coefs == 0);
}

/// The 1-2 reservoir sweep: every (equilibration × scale_objective ×
/// explicit-scales) combination must give the same exact answer.
void sweep(const Case& c,
           const std::vector<PhaseTruth>& truth,
           double opt,
           const std::vector<std::optional<double>>& thetas = {std::nullopt},
           int first_valid_lb = 0)
{
  std::vector<Cut> ref_active;
  for (const auto eq : kEquilibrations) {
    for (const double so : {1.0, 1000.0}) {
      for (const bool xs : {false, true}) {
        for (const auto& th : thetas) {
          const Knobs k {
              .eq = eq,
              .scale_objective = so,
              .scale_theta = th,
              .explicit_scales = xs,
          };
          CAPTURE(eq);
          CAPTURE(so);
          CAPTURE(xs);
          CAPTURE(th.value_or(-1.0));
          CHECK(monolithic_optimum(c, k) == doctest::Approx(opt).epsilon(1e-8));
          const auto run = run_sddp(c, k);
          if (eq == "ruiz" && !xs) {
            // The bug trigger: src (efin) and dep (sini) scales differ.
            // (With explicit energy scales ruiz settles both columns at
            // the same factor, so the guard is on the auto-scale runs.)
            CHECK(run.scale_mismatches > 0);
          }
          check_bounds(run, opt, first_valid_lb);
          check_cuts(run, truth, opt);
          // Invariance of the active cut at v* across all knobs.
          for (std::size_t p = 0; p < truth.size(); ++p) {
            const auto& act = active_cut(run.cuts[p], run.efin[p]);
            if (ref_active.size() <= p) {
              ref_active.push_back(act);
              continue;
            }
            CHECK(act.a == doctest::Approx(ref_active[p].a).epsilon(1e-8));
            for (std::size_t i = 0; i < act.beta.size(); ++i) {
              if (run.efin[p][i] > 1e-6 * c.hydros[i].vmax) {
                CHECK(act.beta[i]
                      == doctest::Approx(ref_active[p].beta[i]).epsilon(1e-8));
              }
            }
          }
        }
      }
    }
  }
}

// ─── Case P: pooled single-bus, 3 phases ──────────────────────────────
//
//   stage 1: D=100 MW, thermal 40 $/MWh ≤50 MW, 200 $/MWh ≤1000 MW
//   stage 2: D=100 MW, 20 ≤30, 60 ≤40, 150 ≤1000
//   stage 3: D=100 MW, 30 ≤20, 90 ≤50, 120 ≤1000
//
//   water: E0 = 150 MW·kDur (no inflows)
// Energies below in GWh (= MW · 1000 h), costs in k$.
// Dispatched thermal energy without hydro:
//   s1: 50@200, 50@40     s2: 30@150, 40@60, 30@20
//   s3: 30@120, 50@90, 20@30
// Greedy displacement of E0 = 150: 50@200(s1) + 30@150(s2) +
// 30@120(s3) + 40 of 50@90(s3) — the boundary falls strictly inside
// the 90 $/MWh block, so the policy and every water value are unique:
//   E after phase 1 = 100, V₂(E) slope −90 $/MWh at 100;
//   E after phase 2 =  70, V₃(E) slope −90 $/MWh at 70;
//   cost: s1 50·40 = 2000, s2 40·60 + 30·20 = 3000,
//         s3 10·90 + 20·30 = 1500  ⇒  optimum 6500 k$ = 6.5e6 $.

[[nodiscard]] auto s1_units() -> std::vector<Unit>
{
  return {
      {.cost = 200.0, .mwh = 50.0 * kDur},
      {.cost = 40.0, .mwh = 50.0 * kDur},
  };
}
[[nodiscard]] auto s2_units() -> std::vector<Unit>
{
  return {
      {.cost = 150.0, .mwh = 30.0 * kDur},
      {.cost = 60.0, .mwh = 40.0 * kDur},
      {.cost = 20.0, .mwh = 30.0 * kDur},
  };
}
[[nodiscard]] auto s3_units() -> std::vector<Unit>
{
  return {
      {.cost = 120.0, .mwh = 30.0 * kDur},
      {.cost = 90.0, .mwh = 50.0 * kDur},
      {.cost = 30.0, .mwh = 20.0 * kDur},
  };
}
constexpr double kOptP = 6500.0 * kDur;

[[nodiscard]] auto join(std::vector<Unit> a, const std::vector<Unit>& b)
    -> std::vector<Unit>
{
  a.insert(a.end(), b.begin(), b.end());
  return a;
}

[[nodiscard]] auto pooled_vf(const std::vector<Unit>& units,
                             const std::vector<Hydro>& hydros) -> ValueFn
{
  ValueFn vf {
      .f = [units](double e) { return pooled_cost(units, e); },
      .breaks = pooled_breaks(units),
  };
  for (const auto& h : hydros) {
    vf.rho.push_back(h.rho);
    vf.vmax.push_back(h.vmax);
  }
  return vf;
}

[[nodiscard]] auto make_pooled_case(std::vector<Hydro> hydros) -> Case
{
  return Case {
      .stages = 3,
      .thermals =
          {
              {.name = "tA",
               .bus = "b1",
               .gcost = {40.0, 20.0, 30.0},
               .pmax = {50.0, 30.0, 20.0}},
              {.name = "tB",
               .bus = "b1",
               .gcost = {200.0, 60.0, 90.0},
               .pmax = {1000.0, 40.0, 50.0}},
              {.name = "tC",
               .bus = "b1",
               .gcost = {200.0, 150.0, 120.0},
               .pmax = {0.0, 1000.0, 1000.0}},
          },
      .hydros = std::move(hydros),
      .loads = {{.bus = "b1", .mw = {100.0, 100.0, 100.0}}},
  };
}

[[nodiscard]] auto pooled_truth(const Case& c,
                                const std::vector<double>& v1,
                                const std::vector<double>& v2)
    -> std::vector<PhaseTruth>
{
  return {
      PhaseTruth {
          .vf = pooled_vf(join(s2_units(), s3_units()), c.hydros),
          .v_star = v1,
          .e_star = 100.0 * kDur,
          .lambda = 90.0,
      },
      PhaseTruth {
          .vf = pooled_vf(s3_units(), c.hydros),
          .v_star = v2,
          .e_star = 70.0 * kDur,
          .lambda = 90.0,
      },
  };
}

}  // namespace

TEST_CASE("SDDP cut exactness - analytic pooled value function")  // NOLINT
{
  // Self-check of the hand derivation used by every case below.
  CHECK(
      pooled_cost(join(join(s1_units(), s2_units()), s3_units()), 150.0 * kDur)
      == doctest::Approx(kOptP));
  CHECK(pooled_cost(join(s2_units(), s3_units()), 100.0 * kDur)
        == doctest::Approx(4500.0 * kDur));
  CHECK(pooled_cost(s3_units(), 70.0 * kDur) == doctest::Approx(1500.0 * kDur));
  // slope at 100 / 70 GWh is −90 $/MWh (strictly inside the 90 block)
  CHECK(pooled_cost(join(s2_units(), s3_units()), (100.0 * kDur) + 1.0)
            - pooled_cost(join(s2_units(), s3_units()), 100.0 * kDur)
        == doctest::Approx(-90.0));
  CHECK(pooled_cost(s3_units(), (70.0 * kDur) - 1.0)
            - pooled_cost(s3_units(), 70.0 * kDur)
        == doctest::Approx(90.0));
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - 1 reservoir, 3 phases, chained state")
{
  // ρ = 250 MWh/hm³: E0 = 150 GWh ⇒ v0 = 600 hm³;
  // v₁* = 100 GWh/ρ = 400 hm³, v₂* = 70 GWh/ρ = 280 hm³;
  // water value at both: 90·250 = 22500 $/hm³.  The phase-1 cut supports
  // V₂ which itself contains phase 2's α (cuts from phase 3), so this
  // also checks a state linked across phases.
  const auto c = make_pooled_case({{
      .name = "R1",
      .bus = "b1",
      .rho = 250.0,
      .vmax = 1000.0,
      .v0 = 600.0,
      .inflow = {0.0, 0.0, 0.0},
      .pmax = {1000.0, 1000.0, 1000.0},
  }});
  sweep(c, pooled_truth(c, {400.0}, {280.0}), kOptP);
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - 2 reservoirs of very different size and head")
{
  // R1: ρ = 250 MWh/hm³, 400 hm³ (100 GWh); R2: ρ = 40000 MWh/hm³,
  // 1.25 hm³ (50 GWh).  The split of the pooled energy between them is
  // not unique, so v* is checked through E = Σρᵢvᵢ; water values are
  // 90·ρᵢ = 22500 and 3.6e6 $/hm³ — 2.2 decades apart, so ruiz assigns
  // the two reservoirs' efin/sini columns very different factors.
  const auto c = make_pooled_case({
      {
          .name = "R1",
          .bus = "b1",
          .rho = 250.0,
          .vmax = 1000.0,
          .v0 = 400.0,
          .inflow = {0.0, 0.0, 0.0},
          .pmax = {1000.0, 1000.0, 1000.0},
      },
      {
          .name = "R2",
          .bus = "b1",
          .rho = 40000.0,
          .vmax = 3.0,
          .v0 = 1.25,
          .inflow = {0.0, 0.0, 0.0},
          .pmax = {1000.0, 1000.0, 1000.0},
      },
  });
  sweep(c, pooled_truth(c, {}, {}), kOptP);
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - 2 scenes, probability-folded cuts")
{
  // Two scenes with probabilities 0.3 / 0.7; scene 2 gets 20 hm³ more
  // inflow in stage 1 (E0 = 155 GWh): 50@200 + 30@150 + 30@120 + 45 of
  // the 50@90 block ⇒ still strictly inside the 90 block, v₁* = 105
  // GWh/ρ = 420 hm³, v₂* = 75 GWh/ρ = 300 hm³, optimum 6050 k$
  // (s1 2000, s2 3000, s3 5·90 + 20·30 = 1050).  Each scene LP folds its
  // probability into the objective, so its cuts are p_s·V_s: slopes
  // −p_s·ρ·90.  Expected bound Σ p_s·opt_s = 0.3·6500 + 0.7·6050 k$.
  auto c = make_pooled_case({{
      .name = "R1",
      .bus = "b1",
      .rho = 250.0,
      .vmax = 1000.0,
      .v0 = 600.0,
      .inflow = {0.0, 0.0, 0.0},
      .pmax = {1000.0, 1000.0, 1000.0},
      .inflow_by_scenario = {{0.0, 0.0, 0.0}, {20.0, 0.0, 0.0}},
  }});
  c.probs = {0.3, 0.7};
  constexpr double kOptB = 6050.0 * kDur;
  CHECK(
      pooled_cost(join(join(s1_units(), s2_units()), s3_units()), 155.0 * kDur)
      == doctest::Approx(kOptB));
  const double opt = (0.3 * kOptP) + (0.7 * kOptB);

  const auto weighted = [](std::vector<PhaseTruth> t, double p)
  {
    for (auto& x : t) {
      x.vf.f = [f = x.vf.f, p](double e) { return p * f(e); };
      x.lambda *= p;
    }
    return t;
  };
  auto truth_b = pooled_truth(c, {420.0}, {300.0});
  truth_b[0].e_star = 105.0 * kDur;
  truth_b[1].e_star = 75.0 * kDur;
  const std::array truths {
      weighted(pooled_truth(c, {400.0}, {280.0}), 0.3),
      weighted(truth_b, 0.7),
  };

  for (const auto eq : kEquilibrations) {
    for (const double so : {1.0, 1000.0}) {
      CAPTURE(eq);
      CAPTURE(so);
      const Knobs k {.eq = eq, .scale_objective = so};
      CHECK(monolithic_optimum(c, k) == doctest::Approx(opt).epsilon(1e-8));
      PlanningLP plp(parse_planning_json(make_json(c, k, /*single_lp=*/false)));
      SDDPMethod sddp(plp, sddp_options(30));
      auto res = sddp.solve();
      REQUIRE(res.has_value());
      for (std::size_t s = 0; s < 2; ++s) {
        CAPTURE(s);
        auto run = collect(plp, sddp, c, SceneIndex {static_cast<Index>(s)});
        run.iters = *res;
        check_bounds(run, opt);
        check_cuts(run, truths[s], opt);
      }
    }
  }
}

namespace
{

// ─── Case F: source state at its upper bound (full reservoir) ──────────
//
//   stage 1: D=100 MW, thermal 10 $/MWh; inflow 300 hm³; v0 = 200 hm³;
//            emax = 300 hm³; free spill.  Water is worth 10 now and
//            ≥ 20 later, so phase 1 fills the reservoir (efin = emax,
//            binding) and turbines the 200 hm³ surplus (50 GWh).
//   stage 2: D=100 MW, units 30@150, 40@60, 30@20 GWh (as s2 above).
//            E = 300·250 = 75 GWh ⇒ inside the 20 block:
//            V₂ = 25 GWh left at 20 $/MWh = 500 k$; slope −20 $/MWh
//            = −5000 $/hm³.
//   optimum = 10·50 + 500 = 1000 k$.

[[nodiscard]] auto make_full_case() -> Case
{
  return Case {
      .stages = 2,
      .thermals =
          {
              {.name = "tL",
               .bus = "b1",
               .gcost = {10.0, 500.0},
               .pmax = {1000.0, 0.0}},
              {.name = "tA",
               .bus = "b1",
               .gcost = {20.0, 20.0},
               .pmax = {0.0, 30.0}},
              {.name = "tB",
               .bus = "b1",
               .gcost = {60.0, 60.0},
               .pmax = {0.0, 40.0}},
              {.name = "tC",
               .bus = "b1",
               .gcost = {150.0, 150.0},
               .pmax = {0.0, 1000.0}},
          },
      .hydros = {{
          .name = "R1",
          .bus = "b1",
          .rho = 250.0,
          .vmax = 300.0,
          .v0 = 200.0,
          .inflow = {300.0, 0.0},
          .pmax = {1000.0, 1000.0},
          .spill = true,
      }},
      .loads = {{.bus = "b1", .mw = {100.0, 100.0}}},
  };
}

// ─── Case N: negative water value + binding emax in the target phase ───
//
//   stage 1: D=100 MW, thermal 50 $/MWh; turbine ≤ 25 MW (binding,
//            25 GWh = 100 hm³); v0 = 300 hm³, inflow 60 hm³
//            ⇒ efin = 300 + 60 − 100 = 260 hm³.
//   stage 2: D=100 MW served by a must-take unit at −30 $/MWh
//            (≤1000 MW); inflow 150 hm³; emax = 400 hm³; NO spill.
//            Ending ≤ emax forces turbining F = ρ(v+150−400)⁺ MWh,
//            which displaces −30 $/MWh energy (GWh, k$):
//              V₂(v) = −30·(100 − F(v))  ⇒  slope +30ρ = +7500 $/hm³
//            for v > 250 (negative water value), 0 below.
//   v* = 260: F = 2.5 GWh, V₂ = −2925 k$; stage 1 = 50·75 = 3750 k$;
//   optimum = 825 k$.  Phase 2 ends at emax (binding upper bound).

[[nodiscard]] auto make_negative_case() -> Case
{
  return Case {
      .stages = 2,
      .thermals =
          {
              {.name = "tC",
               .bus = "b1",
               .gcost = {50.0, 50.0},
               .pmax = {1000.0, 1000.0}},
              {.name = "tN",
               .bus = "b1",
               .gcost = {-30.0, -30.0},
               .pmax = {0.0, 1000.0}},
          },
      .hydros = {{
          .name = "R1",
          .bus = "b1",
          .rho = 250.0,
          .vmax = 400.0,
          .v0 = 300.0,
          .inflow = {60.0, 150.0},
          .pmax = {25.0, 1000.0},
      }},
      .loads = {{.bus = "b1", .mw = {100.0, 100.0}}},
  };
}

// ─── Case K: 3-bus Kirchhoff network, congested line ───────────────────
//
//   b1: hydro (ρ = 250).  b2: thermal g2 (40 $/MWh stage 1, 60 stage 2).
//   b3: thermal g3 100 $/MWh, load 50 MW (stage 1), 300 MW (stage 2).
//   Triangle, equal reactances; l13 limited to F = 110 MW.
//   PTDFs to b3: inj@1 → f13 = 2/3, inj@2 → f13 = 1/3.
//   Stage 2 with hydro H MW (H = E/kDur):
//     H ≤ 30:      uncongested, g2 marginal         dV/dH = −60
//     30 ≤ H ≤ 165: f13 = (2H + g2)/3 = F binding,
//                  g2 = 3F − 2H, g3 = D − 3F + H    dV/dH = −120 + 100 = −20
//                  (LMP₁ = 2·60 − 100 = 20 $/MWh)
//     H ≥ 165:     g2 = 0, extra hydro cannot reach b3   slope 0
//   Stage 1: uncongested, hydro displaces g2 at 40 (up to 50 MW).
//   Water W = 120 MW·stage (E0 = 120 GWh ⇒ v0 = 480 hm³):
//     30 → stage 2 @60, 50 → stage 1 @40, 40 → stage 2 @20,
//     H₂ = 70 (inside 30..165) ⇒ v* = 70 GWh/ρ = 280 hm³,
//     water value 20·250 = 5000 $/hm³ — set by Kirchhoff + congestion.
//   V₂(H=70) = kDur·(100·300 − 120·110 − 20·70) = 15400 k$;
//   stage 1 = 0.

[[nodiscard]] auto network_v2(double e) -> double
{
  const double h = std::clamp(e / kDur, 0.0, 165.0);
  const double per_h = (h <= 30.0)
      ? 60.0 * (300.0 - h)
      : (100.0 * 300.0) - (120.0 * 110.0) - (20.0 * h);
  return kDur * per_h;
}

[[nodiscard]] auto make_network_case() -> Case
{
  return Case {
      .stages = 2,
      .buses = {"b1", "b2", "b3"},
      .thermals =
          {
              {.name = "g2",
               .bus = "b2",
               .gcost = {40.0, 60.0},
               .pmax = {1000.0, 1000.0}},
              {.name = "g3",
               .bus = "b3",
               .gcost = {100.0, 100.0},
               .pmax = {1000.0, 1000.0}},
          },
      .hydros = {{
          .name = "R1",
          .bus = "b1",
          .rho = 250.0,
          .vmax = 1000.0,
          .v0 = 480.0,
          .inflow = {0.0, 0.0},
          .pmax = {1000.0, 1000.0},
      }},
      .loads = {{.bus = "b3", .mw = {50.0, 300.0}}},
      .lines =
          {
              {.bus_a = "b1", .bus_b = "b2", .tmax = 10000.0},
              {.bus_a = "b1", .bus_b = "b3", .tmax = 110.0},
              {.bus_a = "b2", .bus_b = "b3", .tmax = 10000.0},
          },
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "SDDP cut exactness - source state at emax (full reservoir)")
{
  const auto c = make_full_case();
  const std::vector<PhaseTruth> truth {{
      .vf = pooled_vf(s2_units(), c.hydros),
      .v_star = {300.0},
      .e_star = 75.0 * kDur,
      .lambda = 20.0,
  }};
  CHECK(pooled_cost(s2_units(), 75.0 * kDur) == doctest::Approx(500.0 * kDur));
  sweep(c, truth, 1000.0 * kDur);
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - negative water value, binding emax downstream")
{
  const auto c = make_negative_case();
  // V₂(E) with E = ρ·v: F = (E − ρ·250)⁺ = (E − 62.5 GWh)⁺.
  const std::vector<PhaseTruth> truth {{
      .vf =
          {
              .f =
                  [](double e)
              {
                return -30.0
                    * ((100.0 * kDur) - std::max(0.0, e - (62.5 * kDur)));
              },
              .breaks = {62.5 * kDur},
              .rho = {250.0},
              .vmax = {400.0},
          },
      .v_star = {260.0},
      .e_star = 65.0 * kDur,
      .lambda = -30.0,  // β = −ρλ = +7500 $/hm³ (more water costs more)
  }};
  // Iteration 0 runs phase 1 with the α bootstrap pin α = 0, above the
  // true cost-to-go V₂(v*) = −2925 k$ (stage costs are negative here, so
  // assumption A2 fails); LB = 3750 k$ > opt is expected there only.
  // From iteration 1 on, the first cut must already floor α below 0.
  sweep(c, truth, 825.0 * kDur, {std::nullopt}, /*first_valid_lb=*/1);
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - 3-bus Kirchhoff network, any scale_theta")
{
  const auto c = make_network_case();
  CHECK(network_v2(70.0 * kDur) == doctest::Approx(15400.0 * kDur));
  CHECK(network_v2(30.0 * kDur) == doctest::Approx(kDur * 60.0 * 270.0));
  const std::vector<PhaseTruth> truth {{
      .vf =
          {
              .f = network_v2,
              .breaks = {0.0, 30.0 * kDur, 165.0 * kDur},
              .rho = {250.0},
              .vmax = {1000.0},
          },
      .v_star = {280.0},
      .e_star = 70.0 * kDur,
      .lambda = 20.0,
  }};
  sweep(c,
        truth,
        15400.0 * kDur,
        {std::nullopt, std::optional {1.0}, std::optional {1000.0}});
}

// ─── Persistence: save under one scaling, load under another ───────────
//
// Two phase layouts of Case P: one stage per phase, and phase 1 holding
// stages 1-2 (its state is the stage-2 efin, and the stage-1 efin is a
// purely internal column of the same reservoir/uid).  The truth for the
// multi-stage layout is V₃ at v₂* = 280 hm³ (λ = 90).

TEST_CASE(  // NOLINT
    "SDDP cut exactness - saved cuts reload exactly under any equilibration")
{
  auto c = make_pooled_case({{
      .name = "R1",
      .bus = "b1",
      .rho = 250.0,
      .vmax = 1000.0,
      .v0 = 600.0,
      .inflow = {0.0, 0.0, 0.0},
      .pmax = {1000.0, 1000.0, 1000.0},
  }});
  const auto dir =
      std::filesystem::temp_directory_path() / "gtopt_test_cut_exactness";
  std::filesystem::create_directories(dir);

  for (const auto& layout : {std::vector<int> {}, std::vector<int> {2, 1}}) {
    c.phase_stages = layout;
    const auto truth = layout.empty()
        ? pooled_truth(c, {400.0}, {280.0})
        : std::vector<PhaseTruth> {pooled_truth(c, {}, {280.0})[1]};
    CAPTURE(layout.size());
    for (const auto eq_save : kEquilibrations) {
      const auto file =
          (dir / std::format("cuts_{}_{}.parquet", eq_save, layout.size()))
              .string();
      std::filesystem::remove(file);
      {
        const Knobs k {.eq = eq_save, .scale_objective = 1000.0};
        CAPTURE(eq_save);
        const auto run = [&]
        {
          PlanningLP plp(
              parse_planning_json(make_json(c, k, /*single_lp=*/false)));
          SDDPMethod sddp(plp, sddp_options(30));
          auto res = sddp.solve();
          REQUIRE(res.has_value());
          REQUIRE(sddp.save_cuts(file).has_value());
          auto r = collect(plp, sddp, c);
          r.iters = std::move(*res);
          return r;
        }();
        check_bounds(run, kOptP);
        check_cuts(run, truth, kOptP);
      }
      for (const auto eq_load : kEquilibrations) {
        for (const double so : {1.0, 1000.0}) {
          CAPTURE(eq_save);
          CAPTURE(eq_load);
          CAPTURE(so);
          const Knobs k {
              .eq = eq_load,
              .scale_objective = so,
              .explicit_scales = true,
          };
          PlanningLP plp(
              parse_planning_json(make_json(c, k, /*single_lp=*/false)));
          auto opts = sddp_options(1);
          opts.cuts_input_file = file;
          opts.recovery_mode = RecoveryMode::cuts;
          SDDPMethod sddp(plp, opts);
          auto res = sddp.solve();
          REQUIRE(res.has_value());
          REQUIRE_FALSE(res->empty());
          // The first forward pass already runs on the converged cut set.
          CHECK(res->front().lower_bound
                == doctest::Approx(kOptP).epsilon(1e-8));
          CHECK(res->front().upper_bound
                == doctest::Approx(kOptP).epsilon(1e-8));
          const auto run = collect(plp, sddp, c);
          check_cuts(run, truth, kOptP);
          CHECK(run.max_readback_err < 1e-9);
          // Every reloaded coefficient sits on the phase's state column.
          CHECK(run.stray_coefs == 0);
        }
      }
    }
  }
  std::filesystem::remove_all(dir);
}

// ─── Boundary (terminal) cuts ─────────────────────────────────────────

namespace
{

/// 2-stage truncation of the pooled case (stages 1-2 of Case P).
[[nodiscard]] auto truncate_to_two(Case c) -> Case
{
  c.stages = 2;
  for (auto& t : c.thermals) {
    t.gcost.resize(2);
    t.pmax.resize(2);
  }
  for (auto& h : c.hydros) {
    h.inflow.resize(2);
    h.pmax.resize(2);
  }
  for (auto& l : c.loads) {
    l.mw.resize(2);
  }
  return c;
}

/// Boundary-cut CSV: `α ≥ rhs + Σ coeffᵢ · efinᵢ` (one row per cut).
void write_fcf(const std::string& path,
               const Case& c,
               const std::vector<Cut>& cuts)
{
  std::ofstream ofs(path);
  ofs << "iteration,scene,rhs";
  for (const auto& h : c.hydros) {
    ofs << "," << h.name;
  }
  ofs << "\n";
  int it = 1;
  for (const auto& cut : cuts) {
    ofs << std::format("{},1,{:.17g}", it++, cut.a);
    for (const double b : cut.beta) {
      ofs << std::format(",{:.17g}", b);
    }
    ofs << "\n";
  }
}

[[nodiscard]] auto fcf_value(const std::vector<Cut>& cuts,
                             const std::vector<double>& v) -> double
{
  double best = -std::numeric_limits<double>::infinity();
  for (const auto& c : cuts) {
    best = std::max(best, c.at(v));
  }
  return best;
}

struct BoundaryOutcome
{
  double lb {};
  double ub {};
  double phi_end {};  ///< Φ(v_T) at the final policy
  double c_bar {};  ///< α-rebase offset
  std::vector<double> lbs {};  ///< per-iteration LB
};

[[nodiscard]] auto run_with_fcf(const Case& c,
                                const Knobs& k,
                                const std::vector<Cut>& fcf) -> BoundaryOutcome
{
  // gap < 0 never counts as converged, so run a fixed budget.
  auto run = run_sddp(c, k, 12);
  BoundaryOutcome out {
      .lb = run.iters.back().lower_bound,
      .ub = run.iters.back().upper_bound,
      .phi_end = fcf_value(fcf, run.efin.back()),
      .c_bar = run.alpha_offset,
  };
  for (const auto& it : run.iters) {
    out.lbs.push_back(it.lower_bound);
  }
  return out;
}

}  // namespace

TEST_CASE(  // NOLINT
    "SDDP cut exactness - boundary cuts from the same system")
{
  // Train the full 3-phase case, take its phase-2 cuts (supports of V₃
  // on efin₂) as the terminal FCF of the 2-phase truncation.  Because
  // those cuts include the exact support at v₂* = 280 hm³, the
  // truncated problem's optimum is the full-horizon optimum 6500 k$.
  const auto full = make_pooled_case({{
      .name = "R1",
      .bus = "b1",
      .rho = 250.0,
      .vmax = 1000.0,
      .v0 = 600.0,
      .inflow = {0.0, 0.0, 0.0},
      .pmax = {1000.0, 1000.0, 1000.0},
  }});
  const auto trained = run_sddp(full, Knobs {.eq = "none"});
  REQUIRE(trained.iters.back().converged);
  const auto& fcf = trained.cuts[1];
  REQUIRE_FALSE(fcf.empty());

  const auto two = truncate_to_two(full);
  const auto file = (std::filesystem::temp_directory_path()
                     / "gtopt_test_cut_exactness_fcf_self.csv")
                        .string();
  write_fcf(file, two, fcf);

  // Phase costs of the optimal policy: 2000 + 3000 k$; Φ(v₂* = 280) =
  // V₃(70 GWh) = 1500 k$.
  constexpr double phase_costs = 5000.0 * kDur;
  constexpr double phi_star = 1500.0 * kDur;

  for (const auto eq : kEquilibrations) {
    for (const bool ms : {false, true}) {
      for (const bool xs : {false, true}) {
        CAPTURE(eq);
        CAPTURE(ms);
        CAPTURE(xs);
        const Knobs k {
            .eq = eq,
            .explicit_scales = xs,
            .fcf_file = file,
            .mean_shift = ms,
        };
        CHECK(monolithic_optimum(two, k)
              == doctest::Approx(kOptP).epsilon(1e-8));
        const auto o = run_with_fcf(two, k, fcf);
        CAPTURE(o.lb);
        CAPTURE(o.ub);
        CAPTURE(o.phi_end);
        CAPTURE(o.c_bar);
        // LB reaches the full-horizon optimum and never exceeds it.
        CHECK(o.lb == doctest::Approx(kOptP).epsilon(1e-8));
        for (const double lb : o.lbs) {
          CHECK(lb <= kOptP * (1.0 + 1e-9));
        }
        // The terminal state is the full-horizon optimum's.
        CHECK(o.phi_end == doctest::Approx(phi_star).epsilon(1e-8));
        // UB omits the terminal FCF (α stripped) and, with mean_shift,
        // carries c̄ instead:  UB = Σ phase costs + c̄.
        CHECK(o.ub == doctest::Approx(phase_costs + o.c_bar).epsilon(1e-8));
        // Hence the reported gap is exactly the terminal accounting
        // term, LB − UB = Φ(v_T) − c̄ — positive (LB > UB) whenever the
        // FCF at the final volume exceeds c̄, with perfectly valid,
        // self-generated cuts.
        CHECK(o.lb - o.ub
              == doctest::Approx(o.phi_end - o.c_bar).epsilon(1e-8));
        if (!ms) {
          CHECK(o.c_bar == 0.0);
          CHECK(o.lb > o.ub);
        }
      }
    }
  }
  std::filesystem::remove(file);
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - foreign overshooting boundary cuts")
{
  // A terminal FCF that does NOT come from this system (it values the
  // stored water far above the truncated stage 3's real prices).  It
  // makes the policy hoard water and lifts LB above both the true
  // full-horizon optimum (65000) and the reported UB.
  // The whole effect is the cuts', not the LP scaling's: LB, UB and
  // LB − UB are identical under every equilibration and scale.
  const auto two = truncate_to_two(make_pooled_case({{
      .name = "R1",
      .bus = "b1",
      .rho = 250.0,
      .vmax = 1000.0,
      .v0 = 600.0,
      .inflow = {0.0, 0.0, 0.0},
      .pmax = {1000.0, 1000.0, 1000.0},
  }}));
  const std::vector<Cut> fcf {
      {.a = 4.0e7, .beta = {-50000.0}},
      {.a = 2.5e7, .beta = {-10000.0}},
  };
  const auto file = (std::filesystem::temp_directory_path()
                     / "gtopt_test_cut_exactness_fcf_foreign.csv")
                        .string();
  write_fcf(file, two, fcf);

  // Φ(v) = max(4e7 − 50000v, 2.5e7 − 10000v) [$]: the cuts cross at
  // v = 375 hm³; above it the terminal water value is 10000 $/hm³
  // (40 $/MWh), below it 50000 $/hm³ (200 $/MWh).  Using E GWh in
  // stages 1-2 (greedy, 50@200, 30@150, 40@60, 50@40, 30@20):
  //   E ≤ 56.25 (v_T ≥ 375): saves 200 / 150 vs 40 terminal ⇒ use;
  //   beyond: saves ≤ 150 vs 200 terminal ⇒ stop.
  // v_T = 375, phase costs = 19500 − 50·200 − 6.25·150 = 8562.5 k$,
  // Φ(375) = 21250 k$, optimum 29812.5 k$ (≫ the true 6500 k$).
  constexpr double opt = 29812.5 * kDur;
  constexpr double phase_costs = 8562.5 * kDur;
  constexpr double phi_end = 21250.0 * kDur;
  CHECK(pooled_cost(join(s1_units(), s2_units()), 56.25 * kDur)
            + fcf_value(fcf, {375.0})
        == doctest::Approx(opt));
  // Brute-force cross-check over every kink of the 1-D policy.
  for (const double e : {0.0, 50.0, 56.25, 80.0, 120.0, 150.0}) {
    CHECK(pooled_cost(join(s1_units(), s2_units()), e * kDur)
              + fcf_value(fcf, {600.0 - (e * kDur / 250.0)})
          >= opt - 1e-6);
  }

  std::optional<BoundaryOutcome> ref;
  for (const auto eq : kEquilibrations) {
    for (const double so : {1.0, 1000.0}) {
      for (const bool xs : {false, true}) {
        CAPTURE(eq);
        CAPTURE(so);
        CAPTURE(xs);
        const Knobs k {
            .eq = eq,
            .scale_objective = so,
            .explicit_scales = xs,
            .fcf_file = file,
        };
        CHECK(monolithic_optimum(two, k) == doctest::Approx(opt).epsilon(1e-8));
        const auto o = run_with_fcf(two, k, fcf);
        CAPTURE(o.lb);
        CAPTURE(o.ub);
        CHECK(o.lb == doctest::Approx(opt).epsilon(1e-8));
        CHECK(o.lb > kOptP);  // above the true full-horizon optimum
        CHECK(o.lb > o.ub);  // the negative gap
        CHECK(o.ub == doctest::Approx(phase_costs).epsilon(1e-8));
        CHECK(o.phi_end == doctest::Approx(phi_end).epsilon(1e-8));
        CHECK(o.lb - o.ub == doctest::Approx(o.phi_end).epsilon(1e-8));
        if (!ref) {
          ref = o;
          continue;
        }
        CHECK(o.lb == doctest::Approx(ref->lb).epsilon(1e-9));
        CHECK(o.ub == doctest::Approx(ref->ub).epsilon(1e-9));
        CHECK(o.phi_end == doctest::Approx(ref->phi_end).epsilon(1e-9));
      }
    }
  }
  std::filesystem::remove(file);
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - phi_expectation boundary cuts, any layout")
{
  // Foreign FCF over two reservoirs whose coefficients span 8 decades
  // (−0.03 … −3e6 $/hm³, like PLP exports), in both terminal modes
  // (single α / per-plane φ_j) and with or without the α mean shift,
  // on a 1-stage-per-phase and a multi-stage LAST phase.  The LB must
  // equal the monolithic optimum with the same FCF, and LB / UB must be
  // identical under every equilibration and scale_objective.
  for (const auto& layout :
       {std::vector<int> {1, 1, 1}, std::vector<int> {1, 2}})
  {
    auto c = make_pooled_case({
        {
            .name = "R1",
            .bus = "b1",
            .rho = 250.0,
            .vmax = 1000.0,
            .v0 = 400.0,
            .inflow = {0.0, 0.0, 0.0},
            .pmax = {1000.0, 1000.0, 1000.0},
        },
        {
            .name = "R2",
            .bus = "b1",
            .rho = 40000.0,
            .vmax = 3.0,
            .v0 = 1.25,
            .inflow = {0.0, 0.0, 0.0},
            .pmax = {1000.0, 1000.0, 1000.0},
        },
    });
    c.phase_stages = layout;
    const auto file = (std::filesystem::temp_directory_path()
                       / "gtopt_test_cut_exactness_fcf_phi.csv")
                          .string();
    {
      std::ofstream ofs(file);
      ofs << "iteration,scene,rhs,R1,R2\n";
      ofs << "1,1,4.0e7,-50000,-0.03\n";
      ofs << "1,1,2.5e7,-10000,-2.0e6\n";
      ofs << "1,2,3.0e7,-30000,-1.5e6\n";
      ofs << "1,2,1.0e7,-0.05,-3.0e6\n";
    }
    for (const std::string_view mode : {"combined", "phi_expectation"}) {
      for (const bool ms : {false, true}) {
        std::optional<BoundaryOutcome> ref;
        for (const auto eq : kEquilibrations) {
          for (const double so : {1.0, 1000.0}) {
            const Knobs k {
                .eq = eq,
                .scale_objective = so,
                .fcf_file = file,
                .mean_shift = ms,
                .fcf_mode = mode,
            };
            const auto run = run_sddp(c, k, 15);
            const double lb = run.iters.back().lower_bound;
            const double ub = run.iters.back().upper_bound;
            const double mono = monolithic_optimum(c, k);
            CAPTURE(layout.size());
            CAPTURE(mode);
            CAPTURE(ms);
            CAPTURE(eq);
            CAPTURE(so);
            CAPTURE(lb);
            CAPTURE(ub);
            CHECK(lb == doctest::Approx(mono).epsilon(1e-8));
            if (!ref) {
              ref = BoundaryOutcome {.lb = lb, .ub = ub};
              continue;
            }
            CHECK(lb == doctest::Approx(ref->lb).epsilon(1e-8));
            CHECK(ub == doctest::Approx(ref->ub).epsilon(1e-8));
          }
        }
      }
    }
    std::filesystem::remove(file);
  }
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - boundary cuts with apertures and multicut")
{
  // Two scenes with different inflows, the same 8-decade foreign FCF,
  // backward pass with/without one aperture per scenario, cut sharing
  // none / multicut, both terminal modes, mean shift on/off.  Each
  // configuration must report the same LB and UB under every
  // equilibration and scale_objective (the cl5y ruiz-vs-row_max LB
  // divergence is NOT reproduced by any of these paths).
  auto c = make_pooled_case({
      {
          .name = "R1",
          .bus = "b1",
          .rho = 250.0,
          .vmax = 1000.0,
          .v0 = 400.0,
          .inflow = {0.0, 0.0, 0.0},
          .pmax = {1000.0, 1000.0, 1000.0},
          .inflow_by_scenario = {{0.0, 0.0, 0.0}, {10.0, 15.0, 5.0}},
      },
      {
          .name = "R2",
          .bus = "b1",
          .rho = 40000.0,
          .vmax = 3.0,
          .v0 = 1.25,
          .inflow = {0.0, 0.0, 0.0},
          .pmax = {1000.0, 1000.0, 1000.0},
          .inflow_by_scenario = {{0.0, 0.0, 0.0}, {0.1, 0.0, 0.2}},
      },
  });
  c.probs = {0.4, 0.6};
  const auto file = (std::filesystem::temp_directory_path()
                     / "gtopt_test_cut_exactness_fcf_phi2.csv")
                        .string();
  {
    std::ofstream ofs(file);
    ofs << "iteration,scene,rhs,R1,R2\n";
    ofs << "1,1,4.0e7,-50000,-0.03\n";
    ofs << "1,1,2.5e7,-10000,-2.0e6\n";
    ofs << "1,2,3.0e7,-30000,-1.5e6\n";
    ofs << "1,2,1.0e7,-0.05,-3.0e6\n";
  }
  for (const bool ap : {false, true}) {
    for (const auto cs : {CutSharingMode::none, CutSharingMode::multicut}) {
      for (const std::string_view mode : {"combined", "phi_expectation"}) {
        for (const bool ms : {false, true}) {
          std::optional<BoundaryOutcome> ref;
          for (const auto eq : kEquilibrations) {
            for (const double so : {1.0, 1000.0}) {
              const Knobs k {
                  .eq = eq,
                  .scale_objective = so,
                  .fcf_file = file,
                  .mean_shift = ms,
                  .fcf_mode = mode,
                  .apertures = ap,
                  .cut_sharing = cs,
              };
              const auto run = run_sddp(c, k, 15);
              const double lb = run.iters.back().lower_bound;
              const double ub = run.iters.back().upper_bound;
              CAPTURE(ap);
              CAPTURE(static_cast<int>(cs));
              CAPTURE(mode);
              CAPTURE(ms);
              CAPTURE(eq);
              CAPTURE(so);
              if (!ref) {
                ref = BoundaryOutcome {.lb = lb, .ub = ub};
                continue;
              }
              CHECK(lb == doctest::Approx(ref->lb).epsilon(1e-8));
              CHECK(ub == doctest::Approx(ref->ub).epsilon(1e-8));
            }
          }
        }
      }
    }
  }
  std::filesystem::remove(file);
}

TEST_CASE(  // NOLINT
    "SDDP cut exactness - multi-stage later phase keeps the storage chain")
{
  // Regression for `SimulationLP::prev_stage`: a phase-local stage index
  // was used to index the GLOBAL stage array, so the second stage of a
  // later multi-stage phase (layout {1, 2}) restarted from `eini` = v0
  // instead of continuing the phase's own efin — free water: LB = UB =
  // 1.4e6 instead of 6.5e6 (and 2.54e7 instead of 3.85e7 with the FCF).
  auto c = make_pooled_case({{
      .name = "R1",
      .bus = "b1",
      .rho = 250.0,
      .vmax = 1000.0,
      .v0 = 600.0,
      .inflow = {0.0, 0.0, 0.0},
      .pmax = {1000.0, 1000.0, 1000.0},
  }});
  const auto file = (std::filesystem::temp_directory_path()
                     / "gtopt_test_cut_exactness_fcf_ms.csv")
                        .string();
  {
    std::ofstream ofs(file);
    ofs << "iteration,scene,rhs,R1\n";
    ofs << "1,1,4.0e7,-50000\n";
    ofs << "1,1,2.5e7,-10000\n";
  }
  for (const auto& layout : {std::vector<int> {1, 1, 1},
                             std::vector<int> {1, 2},
                             std::vector<int> {2, 1}})
  {
    c.phase_stages = layout;
    for (const bool fcf : {false, true}) {
      // With the FCF (Φ slope 200 $/MWh below v = 375 hm³, 40 above):
      // 56.25 GWh displace 50@200 + 6.25@150 (thermal 28200 − 10937.5 =
      // 17262.5 k$) and Φ(375) = 21250 k$ ⇒ 38512.5 k$ on every layout.
      const double opt = fcf ? 38512.5 * kDur : kOptP;
      for (const auto eq : kEquilibrations) {
        CAPTURE(layout.size());
        CAPTURE(layout.front());
        CAPTURE(fcf);
        CAPTURE(eq);
        const Knobs k {
            .eq = eq,
            .fcf_file = fcf ? file : std::string {},
        };
        const auto run = run_sddp(c, k, 15);
        CHECK(monolithic_optimum(c, k) == doctest::Approx(opt).epsilon(1e-8));
        CHECK(run.iters.back().lower_bound
              == doctest::Approx(opt).epsilon(1e-8));
        if (!fcf) {
          CHECK(run.iters.back().upper_bound
                == doctest::Approx(opt).epsilon(1e-8));
        }
      }
    }
  }
  std::filesystem::remove(file);
}
