/**
 * @file      validate_planning_sanity.cpp
 * @brief     Data-quality (sanity) warnings for a parsed Planning object
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Implements `check_model_sanity()`: checks on data that is legal (the LP
 * builds and solves) but almost certainly wrong.  They never add errors.
 * Each category emits ONE summary warning (count + top offenders); the
 * per-element details go to the debug log.
 *
 * Only inline values are inspected: file-backed schedules (`FileSched`)
 * resolve at LP-build time and are skipped, as everywhere else in
 * validate_planning.  The Python converters run the same checks on the
 * written Parquet files (`gtopt_shared.sanity_report`).
 */

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <format>
#include <map>
#include <optional>
#include <ranges>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

#include <gtopt/field_sched.hpp>
#include <gtopt/validate_planning.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// [min, max] over the inline values of a field schedule.
struct Extrema
{
  double min;
  double max;
};

/// Extrema of every inline value of a FieldSched variant.  Returns
/// `nullopt` for file-backed schedules and empty vectors.
template<typename FieldSched>
[[nodiscard]] std::optional<Extrema> field_extrema(const FieldSched& fs)
{
  std::optional<Extrema> ext;
  const auto scan = [&ext](this const auto& self, const auto& val) -> void
  {
    using T = std::remove_cvref_t<decltype(val)>;
    if constexpr (std::is_arithmetic_v<T>) {
      const auto v = static_cast<double>(val);
      if (ext) {
        ext->min = std::min(ext->min, v);
        ext->max = std::max(ext->max, v);
      } else {
        ext = Extrema {
            .min = v,
            .max = v,
        };
      }
    } else if constexpr (std::is_same_v<T, FileSched>) {
      // Resolved at LP-build time — unknown here.
    } else {
      for (const auto& sub : val) {
        self(sub);
      }
    }
  };
  std::visit(scan, fs);
  return ext;
}

template<typename OptFieldSched>
[[nodiscard]] std::optional<Extrema> opt_field_extrema(const OptFieldSched& opt)
{
  return opt ? field_extrema(*opt) : std::nullopt;
}

/// True when an element is explicitly deactivated at file scope.
[[nodiscard]] bool is_inactive(const OptActive& active) noexcept
{
  if (!active) {
    return false;
  }
  const auto* flag = std::get_if<Int>(&*active);
  return flag != nullptr && *flag == 0;
}

/// True when `sid` names the element with `uid` / `name`.
[[nodiscard]] bool refers_to(const SingleId& sid,
                             Uid uid,
                             std::string_view name) noexcept
{
  if (const auto* u = std::get_if<Uid>(&sid)) {
    return *u == uid;
  }
  const auto* n = std::get_if<Name>(&sid);
  return n != nullptr && *n == name;
}

/// Top-N offender collector: keeps the count and the N highest-scoring
/// labels for one sanity category.
class Offenders
{
public:
  explicit Offenders(std::size_t max_listed) noexcept
      : m_max_listed_(max_listed)
  {
  }

  void add(double score, std::string label)
  {
    spdlog::debug("Sanity: {}", label);
    ++m_count_;
    m_items_.emplace_back(score, std::move(label));
  }

  [[nodiscard]] std::size_t count() const noexcept { return m_count_; }

  /// "a; b; c (+N more)" — highest score first.
  [[nodiscard]] std::string top() const
  {
    auto items = m_items_;
    std::ranges::stable_sort(
        items, [](const auto& a, const auto& b) { return a.first > b.first; });
    std::string out;
    const auto n = std::min(items.size(), m_max_listed_);
    for (std::size_t i = 0; i < n; ++i) {
      if (i > 0) {
        out += "; ";
      }
      out += items[i].second;
    }
    if (items.size() > n) {
      out += std::format(" (+{} more, see debug log)", items.size() - n);
    }
    return out;
  }

  /// Push one summary warning when there is at least one offender.
  void summarise(ValidationResult& result, std::string_view headline) const
  {
    if (m_count_ == 0) {
      return;
    }
    result.warnings.push_back(
        std::format("Sanity: {} — top: {}", headline, top()));
  }

private:
  std::size_t m_max_listed_;
  std::size_t m_count_ {0};
  std::vector<std::pair<double, std::string>> m_items_;
};

// ── (a) water-right rewards vs the demand fail cost ─────────────────────────

/// The deficit cost a reward competes with: the global
/// `demand_fail_cost`, else the cheapest inline per-demand `fcost`.
[[nodiscard]] std::optional<double> reference_fail_cost(
    const Planning& planning)
{
  if (const auto g = planning.options.model_options.demand_fail_cost;
      g && *g > 0.0)
  {
    return g;
  }
  std::optional<double> best;
  for (const auto& d : planning.system.demand_array) {
    if (const auto ext = opt_field_extrema(d.fcost); ext && ext->min > 0.0) {
      best = best ? std::min(*best, ext->min) : ext->min;
    }
  }
  return best;
}

void check_reward_vs_fail_cost(ValidationResult& result,
                               const Planning& planning,
                               const SanityOptions& opts,
                               std::size_t max_listed)
{
  const auto fail_cost = reference_fail_cost(planning);
  if (!fail_cost) {
    return;
  }
  const double limit = opts.effective_reward_fail_cost_ratio() * *fail_cost;
  Offenders off {max_listed};

  for (const auto& fr : planning.system.flow_right_array) {
    if (is_inactive(fr.active)) {
      continue;
    }
    if (const auto ext = opt_field_extrema(fr.uvalue); ext && ext->max > limit)
    {
      off.add(ext->max,
              std::format("FlowRight '{}' uvalue={:g}", fr.name, ext->max));
    }
    if (const auto ext = opt_field_extrema(fr.fcost); ext && -ext->min > limit)
    {
      off.add(-ext->min,
              std::format("FlowRight '{}' fcost={:g}", fr.name, ext->min));
    }
  }
  if (const auto huv = planning.options.model_options.hydro_use_value;
      huv && *huv > limit)
  {
    off.add(*huv,
            std::format("model_options.hydro_use_value={:g} (default uvalue "
                        "of every right)",
                        *huv));
  }

  off.summarise(result,
                std::format("{} water-right reward(s) above {:g} × demand fail "
                            "cost ({:g}) — the LP gains by shedding load to "
                            "collect them (phantom reward)",
                            off.count(),
                            opts.effective_reward_fail_cost_ratio(),
                            *fail_cost));
}

/// True when a user-constraint expression references FlowRight `fr`.
[[nodiscard]] bool mentions_flow_right(std::string_view expr,
                                       const FlowRight& fr)
{
  return expr.contains(std::format("flow_right('{}')", fr.name))
      || expr.contains(std::format("flow_right(\"{}\")", fr.name))
      || expr.contains(std::format("flow_right({})", fr.uid));
}

/// Shortfall penalty of a right: `fcost > 0` or a negative `uvalue`
/// (a penalty written as a negative use value, e.g. PLP's
/// CostoRiegoNS on a `target = 0` deficit right).
[[nodiscard]] std::optional<double> right_penalty(const FlowRight& fr)
{
  std::optional<double> pen;
  if (const auto ext = opt_field_extrema(fr.fcost); ext && ext->min > 0.0) {
    pen = ext->min;
  }
  if (const auto ext = opt_field_extrema(fr.uvalue); ext && ext->max < 0.0) {
    pen = pen ? std::min(*pen, -ext->max) : -ext->max;
  }
  return pen;
}

/// A rewarded right (`uvalue > 0`) coupled by a user constraint to a
/// penalised right whose penalty is below the reward: the LP raises the
/// rewarded flow and books the matching amount as shortfall on the
/// penalised right, pocketing the difference without moving water.
/// Maule Res. 105 (review R3): `maule_resolucion_105` rewards 1100 while
/// `deficit_Melado` (tied by `dist_retiro_Melado`) costs 1000 — about
/// -2.1 G$ of phantom reward on plp_2y.
void check_reward_vs_coupled_penalty(ValidationResult& result,
                                     const System& sys,
                                     const SanityOptions& opts,
                                     std::size_t max_listed)
{
  struct Rewarded
  {
    const FlowRight* right;
    double reward;
  };
  std::vector<Rewarded> rewarded;
  for (const auto& fr : sys.flow_right_array) {
    if (is_inactive(fr.active)) {
      continue;
    }
    if (const auto ext = opt_field_extrema(fr.uvalue); ext && ext->max > 0.0) {
      rewarded.push_back({
          .right = &fr,
          .reward = ext->max,
      });
    }
  }
  if (rewarded.empty() || sys.user_constraint_array.empty()) {
    return;
  }
  const double ratio = opts.effective_reward_fail_cost_ratio();
  Offenders off {max_listed};
  for (const auto& uc : sys.user_constraint_array) {
    if (!uc.active.value_or(true)) {
      continue;
    }
    for (const auto& rw : rewarded) {
      if (!mentions_flow_right(uc.expression, *rw.right)) {
        continue;
      }
      for (const auto& fr : sys.flow_right_array) {
        if (&fr == rw.right || is_inactive(fr.active)) {
          continue;
        }
        const auto pen = right_penalty(fr);
        if (!pen || rw.reward <= ratio * *pen
            || !mentions_flow_right(uc.expression, fr))
        {
          continue;
        }
        off.add(rw.reward - *pen,
                std::format("FlowRight '{}' uvalue={:g} vs '{}' penalty {:g} "
                            "(constraint '{}')",
                            rw.right->name,
                            rw.reward,
                            fr.name,
                            *pen,
                            uc.name));
      }
    }
  }
  off.summarise(result,
                std::format("{} water-right reward(s) above {:g} × the "
                            "shortfall penalty of a right coupled to them by "
                            "a user constraint — the LP books shortfall to "
                            "collect the reward (phantom reward)",
                            off.count(),
                            ratio));
}

// ── (b) per-unit profiles outside [0, 1] ────────────────────────────────────

void check_profiles(ValidationResult& result,
                    const System& sys,
                    const SanityOptions& opts,
                    std::size_t max_listed)
{
  const double tol = opts.effective_profile_tolerance();
  Offenders off {max_listed};
  const auto probe =
      [&](std::string_view cls, std::string_view name, const auto& profile)
  {
    const auto ext = field_extrema(profile);
    if (!ext || (ext->max <= 1.0 + tol && ext->min >= -tol)) {
      return;
    }
    const double score = std::max(ext->max - 1.0, -ext->min);
    off.add(score,
            std::format(
                "{} '{}' range [{:g}, {:g}]", cls, name, ext->min, ext->max));
  };
  for (const auto& gp : sys.generator_profile_array) {
    if (!is_inactive(gp.active)) {
      probe("GeneratorProfile", gp.name, gp.profile);
    }
  }
  for (const auto& dp : sys.demand_profile_array) {
    if (!is_inactive(dp.active)) {
      probe("DemandProfile", dp.name, dp.profile);
    }
  }
  for (const auto& cp : sys.capacity_profile_array) {
    if (!is_inactive(cp.active)) {
      probe("CapacityProfile", cp.name, cp.profile);
    }
  }
  off.summarise(
      result,
      std::format("{} profile(s) with values outside [0, 1] (per-unit "
                  "semantics: a capacity / load factor)",
                  off.count()));
}

// ── (c) zero-capacity units that still carry UC binaries / pmin ─────────────

/// True when the inline schedule exists and is <= 0 everywhere.
template<typename OptFieldSched>
[[nodiscard]] bool all_zero(const OptFieldSched& opt)
{
  const auto ext = opt_field_extrema(opt);
  return ext && ext->max <= 0.0;
}

template<typename OptFieldSched>
[[nodiscard]] bool any_positive(const OptFieldSched& opt)
{
  const auto ext = opt_field_extrema(opt);
  return ext && ext->max > 0.0;
}

void check_zero_capacity_units(ValidationResult& result,
                               const System& sys,
                               std::size_t max_listed)
{
  Offenders off {max_listed};

  for (const auto& gen : sys.generator_array) {
    if (is_inactive(gen.active)) {
      continue;
    }
    const bool expandable = any_positive(gen.expcap);
    const bool zero = all_zero(gen.pmax)
        || (!gen.pmax && all_zero(gen.capacity) && !expandable);
    if (!zero) {
      continue;
    }
    std::vector<std::string_view> why;
    const bool committed =
        std::ranges::any_of(sys.commitment_array,
                            [&gen](const Commitment& c)
                            {
                              return !is_inactive(c.active)
                                  && refers_to(c.generator, gen.uid, gen.name);
                            });
    if (committed) {
      why.emplace_back("UC commitment");
    }
    const bool simple_committed =
        std::ranges::any_of(sys.simple_commitment_array,
                            [&gen](const SimpleCommitment& c)
                            {
                              return !is_inactive(c.active)
                                  && refers_to(c.generator, gen.uid, gen.name);
                            });
    if (simple_committed) {
      why.emplace_back("simple commitment");
    }
    if (any_positive(gen.pmin)) {
      why.emplace_back("pmin > 0");
    }
    if (!why.empty()) {
      off.add(static_cast<double>(why.size()),
              std::format("Generator '{}' ({})",
                          gen.name,
                          std::views::join_with(why, std::string_view {", "})
                              | std::ranges::to<std::string>()));
    }
  }

  for (const auto& bat : sys.battery_array) {
    if (is_inactive(bat.active)) {
      continue;
    }
    if (!all_zero(bat.pmax_charge) || !all_zero(bat.pmax_discharge)) {
      continue;
    }
    std::vector<std::string_view> why;
    if (bat.commitment.value_or(false)) {
      why.emplace_back("commitment binaries");
    }
    if (any_positive(bat.pmin_charge)) {
      why.emplace_back("pmin_charge > 0");
    }
    if (any_positive(bat.pmin_discharge)) {
      why.emplace_back("pmin_discharge > 0");
    }
    if (!why.empty()) {
      off.add(static_cast<double>(why.size()),
              std::format("Battery '{}' ({})",
                          bat.name,
                          std::views::join_with(why, std::string_view {", "})
                              | std::ranges::to<std::string>()));
    }
  }

  off.summarise(result,
                std::format("{} unit(s) with pmax = 0 over the whole horizon "
                            "that still carry commitment binaries or a "
                            "pmin > 0 (dead binaries / infeasible floor)",
                            off.count()));
}

// ── (d) implausible line impedance ──────────────────────────────────────────

/// Representative magnitude of an inline schedule (max |v|).
template<typename OptFieldSched>
[[nodiscard]] std::optional<double> magnitude(const OptFieldSched& opt)
{
  const auto ext = opt_field_extrema(opt);
  if (!ext) {
    return std::nullopt;
  }
  return std::max(std::abs(ext->min), std::abs(ext->max));
}

struct LineImpedance
{
  std::string_view name;
  std::string kv_class;
  double x_eff;  ///< |X| / V² — the quantity the LP and the tie test use
};

/// kV class of a line: "transformer" when both bus voltages are known and
/// differ, else the bus (or line) voltage.  1.0 is the per-unit
/// placeholder and counts as unknown.
[[nodiscard]] std::string kv_class_label(std::optional<double> va,
                                         std::optional<double> vb,
                                         std::optional<double> vline)
{
  const auto known = [](std::optional<double> v)
  { return v && *v > 1.0 ? v : std::nullopt; };
  va = known(va);
  vb = known(vb);
  vline = known(vline);
  if (va && vb && std::abs(*va - *vb) > 0.05 * std::max(*va, *vb)) {
    return "transformer";
  }
  const auto kv = va.or_else([&] { return vb; }).or_else([&] { return vline; });
  if (!kv) {
    return "p.u.";
  }
  return std::format("{:g} kV", std::round(*kv * 10.0) / 10.0);
}

[[nodiscard]] double median_of(std::vector<double> v)
{
  const auto mid = v.size() / 2;
  std::ranges::nth_element(v, v.begin() + static_cast<std::ptrdiff_t>(mid));
  return v[mid];
}

void check_line_impedance(ValidationResult& result,
                          const System& sys,
                          const SanityOptions& opts,
                          std::size_t max_listed)
{
  std::unordered_map<Uid, std::optional<double>> kv_by_uid;
  std::unordered_map<std::string_view, std::optional<double>> kv_by_name;
  for (const auto& bus : sys.bus_array) {
    kv_by_uid.emplace(bus.uid, bus.voltage);
    kv_by_name.emplace(std::string_view {bus.name}, bus.voltage);
  }
  const auto bus_kv = [&](const SingleId& sid) -> std::optional<double>
  {
    if (const auto* u = std::get_if<Uid>(&sid)) {
      const auto it = kv_by_uid.find(*u);
      return it != kv_by_uid.end() ? it->second : std::nullopt;
    }
    const auto it = kv_by_name.find(std::string_view {std::get<Name>(sid)});
    return it != kv_by_name.end() ? it->second : std::nullopt;
  };

  const double xr_max = opts.effective_line_xr_max();
  const double x_pu_max = opts.effective_line_x_pu_max();
  Offenders xr_off {max_listed};
  Offenders xpu_off {max_listed};
  std::vector<LineImpedance> lines;

  for (const auto& line : sys.line_array) {
    if (is_inactive(line.active)) {
      continue;
    }
    const auto x = magnitude(line.reactance);
    if (!x || *x <= 0.0) {
      continue;  // DC link or zero-impedance tie — handled at LP build
    }
    const auto r = magnitude(line.resistance);
    if (r && *r > 0.0 && *x / *r > xr_max) {
      xr_off.add(*x / *r,
                 std::format("Line '{}' X/R={:.3g}", line.name, *x / *r));
    }
    const auto vline = magnitude(line.voltage);
    const double v = (vline && *vline > 0.0) ? *vline : 1.0;
    const double x_eff = *x / (v * v);
    if (vline && *vline > 1.0) {
      const double x_pu = 100.0 * x_eff;  // 100 MVA base
      if (x_pu > x_pu_max) {
        xpu_off.add(
            x_pu,
            std::format(
                "Line '{}' x={:.3g} p.u. at {:g} kV", line.name, x_pu, *vline));
      }
    }
    lines.push_back({
        .name = line.name,
        .kv_class =
            kv_class_label(bus_kv(line.bus_a), bus_kv(line.bus_b), vline),
        .x_eff = x_eff,
    });
  }

  // Per-kV-class robust outliers: |X|/V² far from the class median.
  const double factor = opts.effective_line_x_outlier_factor();
  const auto min_size = static_cast<std::size_t>(
      std::max(opts.effective_line_x_min_class_size(), Int {2}));
  std::map<std::string_view, std::vector<const LineImpedance*>> by_class;
  for (const auto& li : lines) {
    by_class[li.kv_class].push_back(&li);
  }
  Offenders out_off {max_listed};
  for (const auto& [cls, members] : by_class) {
    if (members.size() < min_size) {
      continue;
    }
    const double med = median_of(
        members
        | std::views::transform([](const auto* li) { return li->x_eff; })
        | std::ranges::to<std::vector>());
    if (med <= 0.0) {
      continue;
    }
    for (const auto* li : members) {
      const double ratio = li->x_eff / med;
      if (ratio > factor || ratio < 1.0 / factor) {
        out_off.add(std::max(ratio, 1.0 / ratio),
                    std::format("Line '{}' [{}] |X|/V²={:.3g} = {:.3g}× class "
                                "median {:.3g}",
                                li->name,
                                cls,
                                li->x_eff,
                                ratio,
                                med));
      }
    }
  }

  xr_off.summarise(
      result,
      std::format("{} line(s) with X/R > {:g}", xr_off.count(), xr_max));
  xpu_off.summarise(result,
                    std::format("{} line(s) with reactance above {:g} p.u. "
                                "(100 MVA base) for their kV — check the "
                                "impedance units (Ω vs % vs p.u.)",
                                xpu_off.count(),
                                x_pu_max));
  out_off.summarise(
      result,
      std::format("{} line(s) whose |X|/V² is more than {:g}× off the median "
                  "of their kV class — mistyped kV or impedance units",
                  out_off.count(),
                  factor));
}

// ── (e) agreement reserve levels on reservoirs without emin ────────────────

void check_reserve_levels(ValidationResult& result,
                          const System& sys,
                          std::size_t max_listed)
{
  // reservoir index -> (highest reserve level, referencing rights)
  std::map<std::size_t, std::pair<double, std::vector<std::string>>> refs;
  const auto note = [&](std::string_view cls,
                        std::string_view name,
                        const std::optional<RightBoundRule>& rule)
  {
    if (!rule || rule->axis != BoundRuleAxis::reservoir_volume) {
      return;
    }
    double level = 0.0;
    for (const auto& seg : rule->segments) {
      level = std::max(level, seg.volume);
    }
    if (level <= 0.0) {
      return;
    }
    const auto it = std::ranges::find_if(
        sys.reservoir_array,
        [&rule](const Reservoir& r)
        { return refers_to(rule->reservoir, r.uid, r.name); });
    if (it == sys.reservoir_array.end()) {
      return;  // dangling reference — reported by the integrity check
    }
    const auto idx = static_cast<std::size_t>(
        std::distance(sys.reservoir_array.begin(), it));
    auto& [lvl, who] = refs[idx];
    lvl = std::max(lvl, level);
    who.push_back(std::format("{} '{}'", cls, name));
  };
  for (const auto& fr : sys.flow_right_array) {
    if (!is_inactive(fr.active)) {
      note("FlowRight", fr.name, fr.bound_rule);
    }
  }
  for (const auto& vr : sys.volume_right_array) {
    if (!is_inactive(vr.active)) {
      note("VolumeRight", vr.name, vr.bound_rule);
    }
  }

  Offenders off {max_listed};
  for (const auto& [idx, entry] : refs) {
    const auto& res = sys.reservoir_array[idx];
    if (is_inactive(res.active) || any_positive(res.emin)
        || (res.emin && !opt_field_extrema(res.emin)))
    {
      continue;  // has a floor (or a file-backed one we cannot inspect)
    }
    const auto& [level, who] = entry;
    off.add(level,
            std::format("Reservoir '{}' (reserve level {:g} hm³ in {})",
                        res.name,
                        level,
                        std::views::join_with(who, std::string_view {", "})
                            | std::ranges::to<std::string>()));
  }
  off.summarise(result,
                std::format("{} reservoir(s) without emin whose volume drives "
                            "an agreement bound_rule with a reserve level — "
                            "the LP may draw them below the agreement floor",
                            off.count()));
}

}  // namespace

void check_model_sanity(ValidationResult& result, const Planning& planning)
{
  const auto& opts = planning.options.sanity_options;
  if (!opts.effective_enable()) {
    return;
  }
  const auto max_listed = static_cast<std::size_t>(
      std::max(opts.effective_max_offenders(), Int {1}));
  check_reward_vs_fail_cost(result, planning, opts, max_listed);
  check_reward_vs_coupled_penalty(result, planning.system, opts, max_listed);
  check_profiles(result, planning.system, opts, max_listed);
  check_zero_capacity_units(result, planning.system, max_listed);
  check_line_impedance(result, planning.system, opts, max_listed);
  check_reserve_levels(result, planning.system, max_listed);
}

}  // namespace gtopt
