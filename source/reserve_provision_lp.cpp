#include <algorithm>
#include <cmath>
#include <expected>
#include <format>
#include <functional>
#include <optional>
#include <ranges>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include <gtopt/converter_lp.hpp>
#include <gtopt/error.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_build_error.hpp>
#include <gtopt/output_context.hpp>
#include <gtopt/reserve_provision_lp.hpp>
#include <gtopt/reserve_zone_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <spdlog/spdlog.h>

namespace
{

using namespace gtopt;

/// Per-block SSCC known variable-cost adders [$/MW·h held].
struct SsccAdders
{
  double partload {0.0};
  double activation {0.0};  ///< γ · activation energy cost
  double degradation {0.0};  ///< γ · degradation cost
  [[nodiscard]] constexpr double total() const noexcept
  {
    return partload + activation + degradation;
  }
};

/// Resolve the adders of one direction at (stage, block).  Every term
/// is off unless its field is set; `activation_cost` unset with γ set
/// reads `energy_cost_at(buid)` (generator SRMC) for up reserve.  Each
/// input must be finite and ≥ 0, γ ≤ 1 (guard rails: a negative adder
/// would pay the LP to hold reserve).
std::expected<SsccAdders, std::string> sscc_cost_adders(
    const ReserveProvisionLP::Provision& rp,
    StageUid suid,
    BlockUid buid,
    const auto& energy_cost_at)
{
  const auto valid = [](std::optional<double> v) -> bool
  { return !v || (std::isfinite(*v) && *v >= 0.0); };
  const auto partload = rp.partload_cost.optval(suid, buid);
  const auto gamma = rp.activation_factor.optval(suid, buid);
  const auto act = rp.activation_cost.optval(suid, buid);
  const auto deg = rp.degradation_cost.optval(suid, buid);
  if (!valid(partload) || !valid(act) || !valid(deg)) {
    return std::unexpected(std::string {
        "partload / activation / degradation costs must be finite and >= 0"});
  }
  if (gamma && (!std::isfinite(*gamma) || *gamma < 0.0 || *gamma > 1.0)) {
    return std::unexpected(
        std::format("activation factor = {} must be in [0, 1]", *gamma));
  }
  SsccAdders out {.partload = partload.value_or(0.0)};
  const double g = gamma.value_or(0.0);
  if (g > 0.0) {
    // A negative SRMC (e.g. a must-take credit) never makes the
    // activation adder negative.
    double energy_cost = 0.0;
    if (act) {
      energy_cost = *act;
    } else if (rp.activation_cost_is_srmc) {
      energy_cost = std::max(0.0, energy_cost_at(buid));
    }
    out.activation = g * energy_cost;
    out.degradation = g * deg.value_or(0.0);
  }
  return out;
}

/// Build the provision column + coupling rows for a single direction
/// (up or down) and inject the `stage_provision_factor × prov_col`
/// coefficient into **every** active reserve zone's requirement row.
///
/// The previous design called this helper **once per zone** and let
/// each call create its own `prov_col` for the same (provision, block).
/// That worked only when the provision referenced a single zone; with
/// two or more zones it tripped `LinearProblem::add_col`'s duplicate-
/// metadata check (class + variable + uid identical across calls) and
/// silently dropped every zone past the first.  The refactor mirrors
/// `InertiaProvisionLP::add_to_lp`: one column per (provision, block)
/// created outside the zone loop, then per-zone coefficient injection
/// inside.
std::expected<void, Error> add_provision(
    const std::string_view cname,
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp,
    const std::vector<BlockLP>& blocks,
    auto capacity_col,
    const BIndexHolder<ColIndex>& generation_cols,
    const Uid uid,
    ReserveProvisionLP::Provision& rp,
    const std::string_view pname,
    const std::string_view cap_name,
    std::span<const ElementIndex<ReserveZoneLP>> reserve_zone_indexes,
    const SystemContext& sc,
    auto get_requirement_rows,
    auto provision_row,
    auto energy_cost_at,
    auto outage_rmax_scale_at)
{
  // `provision_factor` / `cost` / `capacity_factor` are now
  // per-(stage, block).  An *empty* provision_factor at every block of
  // the stage still means "no provision wired"; we short-circuit when
  // none of the blocks have a positive factor.  Per-block resolution
  // happens inside the block loop below.
  if (!rp.provision_factor.has_value()) {
    return {};
  }
  const auto use_capacity_field =
      capacity_col && rp.capacity_factor.has_value();

  const auto st_k = std::tuple {scenario.uid(), stage.uid()};

  // Pre-filter: collect per-(s,t) requirement-row maps for every
  // **active** zone that has at least one row entry for this
  // (scenario, stage).  If no zone qualifies, skip creating any
  // cols/rows for this provision — preserves the early-out from
  // the previous per-zone helper.
  // Each zone carries its own effectiveness factor (`*_zone_factors`,
  // minuta (A.8)–(A.12)); the provision column is shared by all zones.
  std::vector<std::pair<const BIndexHolder<RowIndex>*, double>> req_row_maps;
  req_row_maps.reserve(reserve_zone_indexes.size());
  for (std::size_t zi = 0; zi < reserve_zone_indexes.size(); ++zi) {
    auto&& rz = sc.element(reserve_zone_indexes[zi]);
    if (!rz.is_active(stage)) {
      continue;
    }
    // A zone factor of 0 keeps the provision column (UserConstraints may
    // reference it) but stamps nothing into that zone's requirement row.
    const double zone_factor =
        zi < rp.zone_factors.size() ? rp.zone_factors[zi] : 1.0;
    const auto& requirement_rows = get_requirement_rows(rz);
    const auto rows_it = requirement_rows.find(st_k);
    if (rows_it != requirement_rows.end() && !rows_it->second.empty()) {
      req_row_maps.emplace_back(&rows_it->second, zone_factor);
    }
  }
  if (req_row_maps.empty()) {
    return {};
  }

  auto& prov_cols = rp.provision_cols[st_k];
  auto& prov_rows = rp.provision_rows[st_k];
  auto& cap_rows = rp.capacity_rows[st_k];
  map_reserve(prov_cols, blocks.size());
  map_reserve(prov_rows, blocks.size());
  map_reserve(cap_rows, blocks.size());

  for (const auto& block : blocks) {
    const auto buid = block.uid();

    // Resolve the per-(stage, block) provision factor.  When the
    // provision_factor cell at this block is unset OR ≤ 0, the
    // provision is inactive for the block — skip it (preserves the
    // legacy per-stage short-circuit one block at a time).
    const auto block_provision_factor =
        rp.provision_factor.optval(stage.uid(), buid);
    if (!block_provision_factor || (block_provision_factor.value() <= 0.0)) {
      continue;
    }

    const auto block_cost = rp.cost.optval(stage.uid(), buid).value_or(0.0);
    // Guard rail: the reserve bid cost is the DIRECT provision cost
    // (wear, regulation VOM) of holding 1 MW for 1 h — never negative.
    // A negative coefficient would make the LP buy reserve for its own
    // sake (unbounded up to `rmax`) and credit the energy price through
    // the shared headroom.  `validate_planning` rejects inline values;
    // this catches file-backed schedules resolved at load time.
    if (block_cost < 0.0 || !std::isfinite(block_cost)) {
      return std::unexpected(Error {
          .code = ErrorCode::InvalidInput,
          .message = std::format(
              "{} cost = {} at stage {} block {} must be finite and >= 0 "
              "(urcost / drcost are direct provision costs)",
              pname,
              block_cost,
              stage.uid(),
              buid),
      });
    }
    // SSCC known variable-cost adders [$/MW·h held] (each off unless
    // set): part-load penalty + γ·(activation energy cost + degradation).
    const auto adders = sscc_cost_adders(rp, stage.uid(), buid, energy_cost_at);
    if (!adders) {
      return std::unexpected(Error {
          .code = ErrorCode::InvalidInput,
          .message = std::format("{} at stage {} block {}: {}",
                                 pname,
                                 stage.uid(),
                                 buid,
                                 adders.error()),
      });
    }
    const auto block_capacity_factor =
        rp.capacity_factor.optval(stage.uid(), buid);
    const bool use_capacity = use_capacity_field && block_capacity_factor;

    //
    // create the provision col and row when needed and if possible, i.e.,
    // if there is a rmax provision defined for the stage and block
    //
    const auto gcol_it = generation_cols.find(buid);
    if (gcol_it == generation_cols.end()) {
      continue;
    }
    const auto gcol = gcol_it->second;
    auto block_rmax = rp.max.optval(stage.uid(), buid);
    const bool explicit_rmax = block_rmax.has_value();
    if (!block_rmax) {
      // No explicit provision max for this (stage, block).  A wired
      // provision (it reached here past the positive-provision_factor
      // gate above, and its generator HAS a generation column — the
      // `gcol_it == end()` guard above already skipped the no-gcol case)
      // can offer up to the generator's own headroom, so fall back to the
      // gcol upper bound regardless of whether a separate
      // `capacity_factor` row is wired.  The previous `else continue`
      // SILENTLY DROPPED the provision column when `max` was unset and no
      // `capacity_factor` was present — even though the generator has
      // capacity (e.g. a BESS with `ur_provision_factor` but no explicit
      // `urmax`).  That dropped column is referenced by reserve-zone
      // requirements and UserConstraints (`csf_rs_*`), so silently
      // removing it rewrote those constraints and made the LP infeasible
      // (PLEXOS 20260517: `csf_rs_min_bat_del_desierto` lost its
      // `uprovision` term → forced a committed unit OFF → infeasible).
      block_rmax = lp.get_col_uppb(gcol);
    }
    // PLEXOS Min Provision floor — when set, clamp the provision col's
    // lower bound to the per-block value.  This seeds the floor as an
    // unconditional ``provision ≥ urmin`` (the col ``lowb``).  For a
    // generator that HAS a (simple) commitment, the commitment element
    // then rewrites it into the commitment-CONDITIONAL linkage row
    // ``provision − urmin·u ≥ 0`` and resets ``lowb = 0`` (see
    // ``gate_reserve_provisions_by_status``), so the floor only binds
    // when the unit is committed (``u = 1``) — matching PLEXOS's "Min
    // Provision gated by Available Units" semantic.  This mechanism
    // is exercised when ``urmin``/``drmin`` is set on the input.
    // NOTE: the plexos2gtopt converter currently does NOT emit urmin/drmin
    // — even gated on commitment and clamped to the per-block urmax cap, a
    // flat PLEXOS Min Provision can exceed the *dynamic* generator headroom
    // (a committed unit pinned near pmax by Fixed Load has no spare capacity
    // for up-reserve), making the LP infeasible.  Faithfully reproducing
    // PLEXOS's energy+reserve co-optimisation needs a soft (penalised-slack)
    // reserve floor rather than this hard ``lowb`` — future work.
    auto block_rmin = rp.min.optval(stage.uid(), buid).value_or(0.0);

    // Maintenance outage (`Generator.outage_fraction`) under
    // `outage_reserve_mode`: scale (`proportional`) or zero (`exclude`)
    // the provision ceiling.  The gcol-uppb fallback above is already
    // the outage-scaled pmax, so only an explicit `max` scales; a zero
    // scale always zeroes.  The floor never exceeds the ceiling.
    if (const double scale = outage_rmax_scale_at(buid); scale < 1.0) {
      if (scale <= 0.0) {
        block_rmax = 0.0;
      } else if (explicit_rmax) {
        block_rmax = *block_rmax * scale;
      }
      block_rmin = std::min(block_rmin, *block_rmax);
    }

    // LP-size: when both bounds collapse to zero the provision column is
    // fixed at 0 — the capacity row ``cap_factor · cap − prov ≥ 0``
    // reduces to ``cap ≥ 0`` (always true), the provision-bound row is
    // already implied by the generation column's own bounds, and the
    // ``pf · prov`` coefficient stamped into every zone requirement row
    // is identically 0.  Skip the fixed-zero column and its rows.
    // Write-out rule: an absent provision column reads 0 (no reserve
    // provided this block).
    if (sc.options().lp_reduction() && block_rmax.value() == 0.0
        && block_rmin == 0.0)
    {
      continue;
    }

    const auto prov_col = lp.add_col({
        .lowb = block_rmin,
        .uppb = block_rmax.value(),
        .cost = CostHelper::block_ecost(
            scenario, stage, block, block_cost + adders->total()),
        .class_name = cname,
        .variable_name = pname,
        .variable_uid = uid,
        .context = make_block_context(scenario.uid(), stage.uid(), block.uid()),
    });

    prov_cols[buid] = prov_col;
    if (rp.partload_cost.has_value()) {
      rp.partload_coefs[st_k][buid] = adders->partload;
    }
    if (rp.activation_factor.has_value()) {
      rp.activation_coefs[st_k][buid] = adders->activation;
      if (rp.degradation_cost.has_value()) {
        rp.degradation_coefs[st_k][buid] = adders->degradation;
      }
    }

    if (use_capacity) {
      auto crow =
          SparseRow {
              .class_name = cname,
              .constraint_name = cap_name,
              .variable_uid = uid,
              .context =
                  make_block_context(scenario.uid(), stage.uid(), block.uid()),
          }
              .greater_equal(0);
      crow[capacity_col.value()] = block_capacity_factor.value();
      crow[prov_col] = -1;
      cap_rows[buid] = lp.add_row(std::move(crow));
    }

    if (auto prow = provision_row(
            gcol,
            prov_col,
            buid,
            make_block_context(scenario.uid(), stage.uid(), block.uid())))
    {
      prov_rows[buid] = lp.add_row(std::move(*prow));
    }

    //
    // Inject the provision coefficient into every active zone's
    // requirement-balance row for this block.
    //
    for (const auto& [req_rows_ptr, zone_factor] : req_row_maps) {
      if (zone_factor == 0.0) {
        continue;
      }
      const auto req_row_it = req_rows_ptr->find(buid);
      if (req_row_it != req_rows_ptr->end()) {
        lp.set_coeff(req_row_it->second,
                     prov_col,
                     block_provision_factor.value() * zone_factor);
      }
    }
  }

  return {};
}

auto make_rzone_indexes(const InputContext& ic,
                        std::span<const SingleId> rzones)
{
  // Typed-array form post-2026-05-16: each `SingleId` in `rzones` is
  // resolved directly via `ic.element_index`.  No string splitting,
  // no Uid-vs-name discriminator, no per-element heap allocation.
  // Replaces the colon/comma-delimited `String` parser that lived
  // here previously (`split` + `is_uid` + `str2uid` +
  // `RZoneId{std::string(...)}` materialisation per zone).
  using RZoneId = ObjectSingleId<ReserveZoneLP>;
  return std::ranges::to<std::vector>(
      rzones
      | std::views::transform([&](const SingleId& rz)
                              { return ic.element_index(RZoneId {rz}); }));
}

}  // namespace

namespace gtopt
{

ReserveProvisionLP::Provision::Provision(const InputContext& ic,
                                         std::string_view cname,
                                         const Id& id,
                                         auto&& rmax,
                                         auto&& rmin,
                                         auto&& rcost,
                                         auto&& rcapf,
                                         auto&& rprof)
    : max(ic, cname, id, std::forward<decltype(rmax)>(rmax))
    , min(ic, cname, id, std::forward<decltype(rmin)>(rmin))
    , cost(ic, cname, id, std::forward<decltype(rcost)>(rcost))
    , capacity_factor(ic, cname, id, std::forward<decltype(rcapf)>(rcapf))
    , provision_factor(ic, cname, id, std::forward<decltype(rprof)>(rprof))
{
}

ReserveProvisionLP::ReserveProvisionLP(
    const ReserveProvision& preserve_provision, const InputContext& ic)
    : Base(preserve_provision, ic, Element::class_name)
    , up(ic,
         Element::class_name,
         id(),
         std::move(reserve_provision().urmax),
         std::move(reserve_provision().urmin),
         std::move(reserve_provision().urcost),
         std::move(reserve_provision().ur_capacity_factor),
         std::move(reserve_provision().ur_provision_factor))
    , dp(ic,
         Element::class_name,
         id(),
         std::move(reserve_provision().drmax),
         std::move(reserve_provision().drmin),
         std::move(reserve_provision().drcost),
         std::move(reserve_provision().dr_capacity_factor),
         std::move(reserve_provision().dr_provision_factor))
    , generator_index(ic.element_index(generator_sid()))
    , reserve_zone_indexes(
          make_rzone_indexes(ic, reserve_provision().reserve_zones))
    , shared_headroom_(reserve_provision().shared_headroom.value_or(false))
    , offline_(reserve_provision().offline.value_or(false))
    , symmetric_(reserve_provision().symmetric.value_or(false))
    , up_response_caps_(
          OptTBRealSched {ic,
                          Element::class_name,
                          id(),
                          std::move(reserve_provision().urmax_response)},
          OptTBRealSched {ic,
                          Element::class_name,
                          id(),
                          std::move(reserve_provision().urmax_charge_response)})
    , dn_response_caps_(
          OptTBRealSched {ic,
                          Element::class_name,
                          id(),
                          std::move(reserve_provision().drmax_response)},
          OptTBRealSched {ic,
                          Element::class_name,
                          id(),
                          std::move(reserve_provision().drmax_charge_response)})
    , up_energy_usage_(ic,
                       Element::class_name,
                       id(),
                       std::move(reserve_provision().ur_energy_usage))
    , dn_energy_usage_(ic,
                       Element::class_name,
                       id(),
                       std::move(reserve_provision().dr_energy_usage))
{
  up.zone_factors = std::exchange(reserve_provision().ur_zone_factors, {});
  dp.zone_factors = std::exchange(reserve_provision().dr_zone_factors, {});

  // SSCC known variable-cost adders (default off).
  auto&& rpo = reserve_provision();
  const auto sched = [&](OptTBRealFieldSched& f)
  { return OptTBRealSched {ic, Element::class_name, id(), std::move(f)}; };
  up.partload_cost = sched(rpo.ur_partload_cost);
  dp.partload_cost = sched(rpo.dr_partload_cost);
  up.activation_factor = sched(rpo.ur_activation_factor);
  dp.activation_factor = sched(rpo.dr_activation_factor);
  up.activation_cost = sched(rpo.ur_activation_cost);
  dp.activation_cost = sched(rpo.dr_activation_cost);
  up.degradation_cost = sched(rpo.ur_degradation_cost);
  dp.degradation_cost = sched(rpo.dr_degradation_cost);
  up.activation_cost_is_srmc = true;
}

bool ReserveProvisionLP::add_to_lp(const SystemContext& sc,
                                   const ScenarioLP& scenario,
                                   const StageLP& stage,
                                   LinearProblem& lp)
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  static constexpr auto ampl_name = Element::class_name.snake_case();

  if (!is_active(stage)) {
    return true;
  }

  auto&& generator_lp = sc.element(generator_index);
  if (!generator_lp.is_active(stage)) {
    return true;
  }

  // No try/catch: any failure propagates to the add_to_lp driver, which
  // reports it with the element context (lp_build_error.hpp).
  {
    // Use the tolerant lookup variant: when every block of the
    // (scenario, stage) was elided by the P1 zero-pmax optimization
    // (gen column never created — pmax=0 hours), the outer key in
    // ``generation_cols`` is absent.  ``generation_cols_at`` would
    // throw ``flat_map::at`` in that case (verified 2026-05-22 on the
    // CEN PCP daily bundle when ``--use-plexos-gen-cap`` forced
    // hydro generators with zero PLEXOS dispatch to pmax_profile=0
    // every block).  ``lookup_generation_cols`` returns an empty
    // BIndexHolder via ``find_or_empty_inner``, so the per-block
    // ``generation_cols.find(buid)`` checks below safely iterate
    // zero times and the reserve constraints simply aren't emitted
    // for this generator at this (scenario, stage).  This is the
    // correct behaviour: with no dispatchable capacity available,
    // there is no reserve capability to provide either.
    auto&& generation_cols =
        generator_lp.lookup_generation_cols(scenario, stage);

    const auto [opt_capacity, capacity_col] =
        generator_lp.capacity_and_col(stage, lp);

    // Battery discharge generator in native BESS-reserve mode
    // (``Battery.reserve_sustain_hours`` set): the linking ConverterLP
    // emits the aggregated power-headroom / SoC rows over all of this
    // battery's provisions, so the generic per-provision generator rows
    // (``g + r_up <= Pd`` and ``g - r_dn >= 0``) are skipped.  The latter
    // would force the battery to DISCHARGE to offer down reserve.
    const bool native_bess = is_native_bess_reserve_generator(sc, generator_lp);

    // Shared headroom (minuta (A.23)–(A.24)): the first provision of
    // this generator flagged `shared_headroom` owns the headroom row;
    // later ones stamp their column into it instead of adding a row.
    // Provisions are built in element order, so an earlier owner's row
    // already exists here.  CommitmentLP later gates the owner's row
    // with `u` exactly once.
    const auto shared_owner_row = [&](BlockUid buid,
                                      bool upward) -> std::optional<RowIndex>
    {
      if (!shared_headroom_) {
        return std::nullopt;
      }
      for (const auto& other : sc.elements<ReserveProvisionLP>()) {
        if (&other == this) {
          break;
        }
        if (!other.shared_headroom()
            || other.generator_index != generator_index)
        {
          continue;
        }
        if (auto row = upward
                ? other.lookup_up_provision_row(scenario, stage, buid)
                : other.lookup_dn_provision_row(scenario, stage, buid))
        {
          return row;
        }
      }
      return std::nullopt;
    };

    auto uprov_row = [&](auto gcol, auto rcol, BlockUid buid, auto context)
        -> std::optional<SparseRow>
    {
      // Offline (cold) reserve uses no online headroom; CommitmentLP
      // gates it with `r ≤ urmax·(1 − u)`.
      if (native_bess || offline_) {
        return std::nullopt;
      }
      if (const auto owner = shared_owner_row(buid, true)) {
        lp.set_coeff(*owner, rcol, 1.0);
        return std::nullopt;
      }
      auto rrow =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveProvisionLP::UprovisionName,
              .variable_uid = uid(),
              .context = std::move(context),
          }
              .less_equal(lp.get_col_uppb(gcol));
      rrow[gcol] = 1;
      rrow[rcol] = 1;

      if (capacity_col) {
        // `gen + r ≤ (1 − f)·capacity` under a maintenance outage.
        rrow[*capacity_col] =
            -generator_lp.available_fraction_at(scenario, stage, buid);
        return rrow.less_equal(0.0);
      }
      return rrow;
    };

    auto dprov_row = [&](auto gcol, auto rcol, BlockUid buid, auto context)
        -> std::optional<SparseRow>
    {
      if (native_bess) {
        return std::nullopt;
      }
      if (const auto owner = shared_owner_row(buid, false)) {
        lp.set_coeff(*owner, rcol, -1.0);
        return std::nullopt;
      }
      auto rrow =
          SparseRow {
              .class_name = cname,
              .constraint_name = ReserveProvisionLP::DprovisionName,
              .variable_uid = uid(),
              .context = std::move(context),
          }
              .greater_equal(lp.get_col_lowb(gcol));
      rrow[gcol] = 1;
      rrow[rcol] = -1;
      return rrow;
    };

    const auto& blocks = stage.blocks();

    // Generator SRMC (fuel + VOM, $/MWh) of the block: the default
    // activation energy cost of up reserve (`ur_activation_cost` unset).
    const auto energy_cost_at = [&](BlockUid buid) -> double
    { return generator_lp.srmc_at(scenario, stage, buid).value_or(0.0); };

    // Provision-ceiling multiplier under a maintenance outage
    // (`model_options.outage_reserve_mode`, default `none` = 1).
    const auto outage_mode = sc.options().outage_reserve_mode();
    const auto outage_rmax_scale_at = [&](BlockUid buid) -> double
    {
      const double avail =
          generator_lp.available_fraction_at(scenario, stage, buid);
      if (avail >= 1.0) {
        return 1.0;
      }
      switch (outage_mode) {
        case OutageReserveMode::proportional:
          return avail;
        case OutageReserveMode::exclude:
          return 0.0;
        case OutageReserveMode::none:
          break;
      }
      return 1.0;
    };

    // `add_provision` now consumes the full zone list and creates one
    // column per (provision, block) — the per-zone work is folded
    // inside its block loop.  See the docstring above for why the
    // outer zone loop disappeared.
    if (auto res = add_provision(
            cname,
            scenario,
            stage,
            lp,
            blocks,
            capacity_col,
            generation_cols,
            uid(),
            up,
            UprovisionName,
            UcapacityName,
            std::span<const ElementIndex<ReserveZoneLP>>(reserve_zone_indexes),
            sc,
            [](const auto& rz) -> const auto&
            { return rz.urequirement_rows(); },
            uprov_row,
            energy_cost_at,
            outage_rmax_scale_at);
        !res)
    {
      fail_lp(*this,
              scenario,
              stage,
              {
                  .field = "up_provision",
                  .detail = std::format("add_provision (uprov) failed: {}",
                                        res.error().message),
              });
    }
    if (auto res = add_provision(
            cname,
            scenario,
            stage,
            lp,
            blocks,
            capacity_col,
            generation_cols,
            uid(),
            dp,
            DprovisionName,
            DcapacityName,
            std::span<const ElementIndex<ReserveZoneLP>>(reserve_zone_indexes),
            sc,
            [](const auto& rz) -> const auto&
            { return rz.drequirement_rows(); },
            dprov_row,
            energy_cost_at,
            outage_rmax_scale_at);
        !res)
    {
      fail_lp(*this,
              scenario,
              stage,
              {
                  .field = "dn_provision",
                  .detail = std::format("add_provision (dprov) failed: {}",
                                        res.error().message),
              });
    }

    // Lost-unit reserve (`contingency_includes_reserve`): in every zone
    // of this provision, the contingency rows that contain this
    // generator with coefficient a also get −a·r, i.e. C ≥ a·(p + r).
    for (const auto& zi : reserve_zone_indexes) {
      const auto& rz = sc.element(zi);
      if (!rz.is_active(stage) || !rz.contingency_includes_reserve()) {
        continue;
      }
      for (const auto& block : blocks) {
        const auto buid = block.uid();
        for (const bool upward : {true, false}) {
          const auto rcol = upward
              ? lookup_up_provision_col(scenario, stage, buid)
              : lookup_dn_provision_col(scenario, stage, buid);
          if (!rcol) {
            continue;
          }
          for (const auto& ref :
               rz.contingency_gen_refs(upward, scenario, stage, buid))
          {
            if (ref.generator == generator_index) {
              lp.set_coeff(ref.row, *rcol, -ref.coef);
            }
          }
        }
      }
    }

    // Symmetric provision (minuta (A.13)): r_up − r_dn = 0.
    if (symmetric_) {
      const auto st = std::tuple {scenario.uid(), stage.uid()};
      for (const auto& block : blocks) {
        const auto buid = block.uid();
        const auto ucol = lookup_up_provision_col(scenario, stage, buid);
        const auto dcol = lookup_dn_provision_col(scenario, stage, buid);
        if (!ucol || !dcol) {
          continue;
        }
        auto srow = SparseRow {
            .class_name = cname,
            .constraint_name = SymmetryName,
            .variable_uid = uid(),
            .context = make_block_context(scenario.uid(), stage.uid(), buid),
        };
        srow[*ucol] = 1.0;
        srow[*dcol] = -1.0;
        symmetry_rows_[st][buid] = lp.add_row(std::move(srow.equal(0.0)));
      }
    }
  }

  // Register PAMPL-visible provision columns under the canonical
  // `up` / `dn` spelling.
  const auto st_key = std::tuple {scenario.uid(), stage.uid()};
  if (const auto it = up.provision_cols.find(st_key);
      it != up.provision_cols.end() && !it->second.empty())
  {
    sc.add_ampl_variable(ampl_name, uid(), UpName, scenario, stage, it->second);
  }
  if (const auto it = dp.provision_cols.find(st_key);
      it != dp.provision_cols.end() && !it->second.empty())
  {
    sc.add_ampl_variable(ampl_name, uid(), DnName, scenario, stage, it->second);
  }

  return true;
}

bool ReserveProvisionLP::add_to_output(OutputContext& out) const
{
  static constexpr std::string_view cname = Element::class_name.full_name();
  const auto pid = id();

  out.add_col_sol(cname, UprovisionName, pid, up.provision_cols);
  out.add_col_cost(cname, UprovisionName, pid, up.provision_cols);
  out.add_row_dual(cname, UprovisionName, pid, up.provision_rows);
  out.add_row_dual(cname, UcapacityName, pid, up.capacity_rows);

  out.add_col_sol(cname, DprovisionName, pid, dp.provision_cols);
  out.add_col_cost(cname, DprovisionName, pid, dp.provision_cols);
  out.add_row_dual(cname, DprovisionName, pid, dp.provision_rows);
  out.add_row_dual(cname, DcapacityName, pid, dp.capacity_rows);
  if (!symmetry_rows_.empty()) {
    out.add_row_dual(cname, SymmetryName, pid, symmetry_rows_);
  }

  // SSCC known variable costs [$/h] = applied adder × provision, one
  // stream per term (only for the terms that are set).
  const auto cost_stream = [&](std::string_view fname,
                               const STBIndexHolder<double>& coefs,
                               const STBIndexHolder<ColIndex>& cols)
  {
    if (coefs.empty()) {
      return;
    }
    STBIndexHolder<double> values;
    for (const auto& [st_key, bcoefs] : coefs) {
      const auto cit = cols.find(st_key);
      if (cit == cols.end()) {
        continue;
      }
      auto& vst = values[st_key];
      for (const auto& [buid, coef] : bcoefs) {
        if (const auto it = cit->second.find(buid); it != cit->second.end()) {
          vst[buid] = coef * out.primal(it->second);
        }
      }
    }
    out.add_col_sol_values(cname, fname, pid, values);
  };
  cost_stream(UpPartloadCostName, up.partload_coefs, up.provision_cols);
  cost_stream(UpActivationCostName, up.activation_coefs, up.provision_cols);
  cost_stream(UpDegradationCostName, up.degradation_coefs, up.provision_cols);
  cost_stream(DnPartloadCostName, dp.partload_coefs, dp.provision_cols);
  cost_stream(DnActivationCostName, dp.activation_coefs, dp.provision_cols);
  cost_stream(DnDegradationCostName, dp.degradation_coefs, dp.provision_cols);

  return true;
}

void gate_reserve_provisions_by_status(
    const SystemContext& sc,
    const ScenarioLP& scenario,
    const StageLP& stage,
    LinearProblem& lp,
    ElementIndex<GeneratorLP> gen_index,
    const BIndexHolder<ColIndex>& status_cols,
    const BIndexHolder<ColIndex>& generation_cols,
    std::optional<ColIndex> gen_capacity_col,
    const std::function<double(BlockUid, double)>& committed_floor)
{
  const auto& blocks = stage.blocks();
  for (const auto& rprov : sc.elements<ReserveProvisionLP>()) {
    // Pair by resolved element index: a raw SingleId compare missed
    // `5` vs `"g5"` (same generator) and skipped the u-gating.
    if (sc.element_index(rprov.generator_sid()) != gen_index) {
      continue;
    }
    for (const auto& block : blocks) {
      const auto buid = block.uid();
      const auto ucol_it = status_cols.find(buid);
      if (ucol_it == status_cols.end()) {
        continue;
      }
      const auto ucol = ucol_it->second;

      // Offline (cold) up reserve, minuta (A.19): offered only while
      // the unit is OFF — `r_up ≤ urmax · (1 − u)`, i.e.
      // `r_up + urmax · u ≤ urmax`.  It has no headroom row.
      if (rprov.offline()) {
        if (const auto up_col =
                rprov.lookup_up_provision_col(scenario, stage, buid))
        {
          // Big-M = the provision's physical ceiling.  An unbounded
          // ceiling (no urmax, unbounded generator) would put a 1e20+
          // coefficient into the row: reject it.
          const auto urmax = lp.get_col_uppb(*up_col);
          if (!std::isfinite(urmax) || urmax >= 1e19
              || is_infinity(urmax, lp.infinity()))
          {
            throw std::runtime_error(std::format(
                "ReserveProvision '{}': offline (cold) up reserve needs a "
                "finite ceiling (set urmax or a finite generator pmax); "
                "got {}",
                rprov.object().name,
                urmax));
          }
          auto orow =
              SparseRow {
                  .class_name = ReserveProvisionLP::Element::class_name,
                  .constraint_name = ReserveProvisionLP::OfflineUpProvisionName,
                  .variable_uid = rprov.uid(),
                  .context = make_block_context(
                      scenario.uid(), stage.uid(), block.uid()),
              }
                  .less_equal(urmax);
          orow[*up_col] = 1.0;
          orow[ucol] = urmax;
          std::ignore = lp.add_row(std::move(orow));
        }
      }

      // Modify up-provision row: add -Pmax on u, change RHS to 0
      if (const auto urow =
              rprov.lookup_up_provision_row(scenario, stage, buid))
      {
        if (gen_capacity_col) {
          // Expandable generator: the headroom row is already
          // `g + r − capacity ≤ 0` (RHS 0, no Pmax to move onto u —
          // pre-fix this stamped `−0·u` and an off-line unit still
          // offered reserve).  Keep that row and add a separate
          // u-gated copy without the capacity term:
          //   g + Σ r − Pmax·u ≤ 0,   Pmax = gen column upper bound
          // (the same big-M as the C2 `gen_upper` row).
          const auto gcol_it = generation_cols.find(buid);
          if (gcol_it != generation_cols.end()) {
            const auto gen_pmax = lp.get_col_uppb(gcol_it->second);
            auto grow =
                SparseRow {
                    .class_name = ReserveProvisionLP::Element::class_name,
                    .constraint_name =
                        ReserveProvisionLP::CommitUpProvisionName,
                    .variable_uid = rprov.uid(),
                    .context = make_block_context(
                        scenario.uid(), stage.uid(), block.uid()),
                }
                    .less_equal(0.0);
            for (const auto& [col, coef] : lp.row_at(*urow).cmap) {
              if (col != *gen_capacity_col) {
                grow[col] = coef;
              }
            }
            grow[ucol] = -gen_pmax;
            std::ignore = lp.add_row(std::move(grow));
          }
        } else {
          auto& row = lp.row_at(*urow);
          const auto pmax = row.uppb;  // original Pmax was stored as uppb
          lp.set_coeff(*urow, ucol, -pmax);
          row.uppb = 0.0;
        }
      }

      // Modify down-provision row: add -Pmin on u, change RHS to 0
      if (const auto drow =
              rprov.lookup_dn_provision_row(scenario, stage, buid))
      {
        auto& row = lp.row_at(*drow);
        // Floor below which down reserve cannot be offered: the
        // committed minimum, chosen by the caller.
        const auto pmin = committed_floor(buid, row.lowb);
        lp.set_coeff(*drow, ucol, -pmin);
        row.lowb = 0.0;
      }

      // ── PLEXOS Min Provision linkage (urmin/drmin) ──
      //
      // The commitment elements run after ReserveProvisionLP, so the
      // provision rows/columns already exist here; this helper is the
      // single place that gates them (CommitmentLP calls it on both
      // its chronological and block formulations).
      //
      // ReserveProvisionLP::add_to_lp creates the provision column
      // with ``lowb = urmin`` as a hard floor (when PLEXOS
      // ``ReserveGenerators.Min Provision`` is set on the
      // membership).  Per PLEXOS docs (ReserveGenerators.MinProvision)
      // the floor is gated by ``Available Units`` — i.e. should
      // collapse to 0 when the gen is not committed.  Here, since
      // this gen has a (simple) commitment, transform the hard col-lowb
      // into a conditional linkage row:
      //   provision_col - urmin·u >= 0
      // and reset the col lowb to 0 so the LP only enforces the
      // floor when u = 1.
      if (const auto up_col_opt =
              rprov.lookup_up_provision_col(scenario, stage, buid))
      {
        const auto up_col = *up_col_opt;
        auto& col = lp.col_at(up_col);
        if (col.lowb > 0.0) {
          const auto urmin = col.lowb;
          auto urow =
              SparseRow {
                  .class_name = ReserveProvisionLP::Element::class_name,
                  .constraint_name = ReserveProvisionLP::MinUpProvisionName,
                  .variable_uid = rprov.uid(),
                  .context = make_block_context(
                      scenario.uid(), stage.uid(), block.uid()),
              }
                  .greater_equal(0);
          urow[up_col] = 1.0;
          urow[ucol] = -urmin;
          std::ignore = lp.add_row(std::move(urow));
          col.lowb = 0.0;
        }
      }
      if (const auto dn_col_opt =
              rprov.lookup_dn_provision_col(scenario, stage, buid))
      {
        const auto dn_col = *dn_col_opt;
        auto& col = lp.col_at(dn_col);
        if (col.lowb > 0.0) {
          const auto drmin = col.lowb;
          auto drow =
              SparseRow {
                  .class_name = ReserveProvisionLP::Element::class_name,
                  .constraint_name = ReserveProvisionLP::MinDnProvisionName,
                  .variable_uid = rprov.uid(),
                  .context = make_block_context(
                      scenario.uid(), stage.uid(), block.uid()),
              }
                  .greater_equal(0);
          drow[dn_col] = 1.0;
          drow[ucol] = -drmin;
          std::ignore = lp.add_row(std::move(drow));
          col.lowb = 0.0;
        }
      }
    }
  }
}

}  // namespace gtopt
