/**
 * @file      reserve_zone.hpp
 * @brief     Defines the ReserveZone structure for spinning-reserve modeling
 * @date      Thu Apr  3 10:32:46 2025
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * This module defines the ReserveZone structure which represents a regional
 * spinning-reserve requirement in the LP formulation. Each zone specifies
 * up/down reserve requirements and shortage penalty costs.
 *
 * Besides the fixed `urreq` / `drreq` schedules, a zone may define a
 * *dynamic* requirement as the maximum of K affine planes
 * (`urreq_planes` / `drreq_planes`) in the system demand, the system
 * inertia and the largest single contingency — the CEN (Chile) primary
 * frequency reserve (CPF) model of the ISSCC / ECFyDR studies:
 *
 *   R ≥ alpha_k + demand_coef_k·D + inertia_coef_k·I
 *         + contingency_coef_k·C            ∀ k
 *   R ≥ max(0, req, min)                    (floor)
 *   Σ pf·prov + shortage ≥ R                (shortage priced by urcost)
 *
 * `D` is the EXOGENOUS system demand `demand_load_factor · Σ lmax`, so
 * the demand term lives on the row RHS and never enters the bus prices.
 * `I` (inertia, from an @ref InertiaZone's provisions) and `C` (largest
 * listed injection / load) are endogenous LP columns.
 *
 * ### JSON Example
 * ```json
 * {
 *   "uid": 1, "name": "cpf_up", "urcost": 5000,
 *   "urreq_planes": [
 *     {"alpha": 516.0, "demand_coef": -0.0366,
 *      "inertia_coef": -0.0123, "contingency_coef": 1.25},
 *     {"alpha": 339.464, "demand_coef": -0.0235,
 *      "inertia_coef": -0.009986, "contingency_coef": 1.25}
 *   ],
 *   "demand_load_factor": 1.025,
 *   "inertia_zone": "sen",
 *   "contingency_generators": ["ccgt1", "ccgt2"]
 * }
 * ```
 */

#pragma once

#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>

namespace gtopt
{

/**
 * @struct ReserveRequirementPlane
 * @brief One affine plane of a dynamic reserve requirement
 *
 * The plane value is
 *
 *   alpha + demand_coef·D + inertia_coef·I + contingency_coef·C   [MW]
 *
 * with `D` the system demand [MW], `I` the system inertia [MWs] and `C`
 * the largest single contingency [MW].  Coefficients are plain linear
 * factors in gtopt units: a CEN plane written as
 * `α − β·D[GW] − γ·I[GVAs] + δ·C[MW]` maps to `alpha = α`,
 * `demand_coef = −β/1000`, `inertia_coef = −γ/1000`,
 * `contingency_coef = δ`.  Unset coefficients are 0.  Every coefficient
 * is a per-(stage, block) schedule (scalar, 2-D array or file), so dated
 * coefficient sets (PLEXOS "Date From / Date To") map to stages.
 */
struct ReserveRequirementPlane
{
  OptTBRealFieldSched alpha {};  ///< Constant term [MW]
  OptTBRealFieldSched demand_coef {};  ///< System-load coefficient [MW/MW]
  OptTBRealFieldSched inertia_coef {};  ///< Inertia coefficient [MW/MWs]
  OptTBRealFieldSched
      contingency_coef {};  ///< Contingency coefficient [MW/MW] (≥ 0)
  OptTBRealFieldSched flow_coef {};  ///< Coefficient on the signed flow
                                     ///< sum of `flow_lines` [MW/MW]

  [[nodiscard]] bool operator==(const ReserveRequirementPlane&) const = default;
};

/**
 * @struct ReserveContingency
 * @brief One contingency candidate of a dynamic requirement
 *
 * Mirrors a PLEXOS `<unit>_Comparison` row: the largest-contingency
 * column satisfies
 *
 *   C ≥ Σ a_g·p_g + Σ a_d·load_d + Σ a_l·flow_l + offset
 *
 * so a plant with several modes (`NEHUENCO_1_GNL`: 21 PLEXOS generators),
 * a common aggregate added to every candidate (`limited_generation_group`
 * = Σ PMGD generation − BESS charge), a line (`TxJadresic`) and a
 * constant (`RHS 8.4`) are all one candidate.  Missing coefficients = 1.
 */
struct ReserveContingency
{
  OptName name {};  ///< Optional label (provenance)
  Array<SingleId> generators {};  ///< Generators whose output is lost
  Array<Real> generator_coefs {};  ///< Parallel to `generators` (default 1)
  Array<SingleId> demands {};  ///< Demands whose load is lost (down)
  Array<Real> demand_coefs {};  ///< Parallel to `demands` (default 1)
  Array<SingleId> lines {};  ///< Lines whose signed A→B flow is lost
  Array<Real> line_coefs {};  ///< Parallel to `lines` (default 1)
  OptTBRealFieldSched offset {};  ///< Constant term [MW]

  [[nodiscard]] bool operator==(const ReserveContingency&) const = default;
};

/**
 * @struct ReserveZone
 * @brief Regional spinning-reserve requirement zone
 *
 * A reserve zone aggregates up/down reserve requirements for a set of buses.
 * Generators contribute to zones via @ref ReserveProvision entries.
 * If requirements are not met, the shortage is penalized at `urcost`/`drcost`.
 *
 * @see ReserveProvision for generator-to-zone linkage
 * @see ReserveZoneLP for the LP formulation
 */
struct ReserveZone
{
  /// Canonical class-name constant used in LP row labels and config
  /// fields like `VariableScale::class_name`.  Single source of truth —
  /// `ReserveZoneLP` exposes no separate `ClassName` member; callers
  /// reach the constant via `ReserveZone::class_name` directly (or
  /// `ReserveZoneLP::Element::class_name` in generic contexts).
  static constexpr LPClassName class_name {"ReserveZone"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status
  OptName type {};  ///< Optional element type/category tag
  OptName description {};  ///< Optional free-text description (e.g. conversion
                           ///< provenance)

  OptTBRealFieldSched urreq {};  ///< Up-reserve requirement schedule [MW]
  OptTBRealFieldSched drreq {};  ///< Down-reserve requirement schedule [MW]
  OptTBRealFieldSched urmin {};  ///< Up-reserve Min-Provision floor [MW].  A
                                 ///< static lower bound on total provided
                                 ///< up-reserve (PLEXOS ``Min Provision``):
                                 ///< the LP enforces ``Σ pf·prov ≥
                                 ///< max(urreq, urmin)``.  Kept separate from
                                 ///< ``urreq`` so the time-varying requirement
                                 ///< schedule mirrors PLEXOS's reported RHS
                                 ///< instead of being raised by the floor.
  OptTBRealFieldSched drmin {};  ///< Down-reserve Min-Provision floor [MW].
                                 ///< Mirrors ``urmin`` for the down direction.
  OptTBRealFieldSched urcost {};  ///< Up-reserve shortage penalty [$/MW]
                                  ///< per-(stage, block); accepts a scalar
                                  ///< (broadcasts), a 2-D nested array, or
                                  ///< a file-backed schedule.
  OptTBRealFieldSched drcost {};  ///< Down-reserve shortage penalty [$/MW]
                                  ///< per-(stage, block); same shapes as
                                  ///< ``urcost``.

  /// @name Dynamic (plane-based) requirement
  /// When `urreq_planes` (`drreq_planes`) is non-empty the up (down)
  /// requirement becomes an LP column `R ≥ plane_k ∀k`, floored by
  /// `max(0, urreq, urmin)` (resp. `drreq`, `drmin`); see
  /// @ref ReserveRequirementPlane.
  /// @{
  Array<ReserveRequirementPlane> urreq_planes {};  ///< Up-requirement planes
  Array<ReserveRequirementPlane> drreq_planes {};  ///< Down-requirement planes
  /// System-load term `D` of the planes: `"exogenous"` (default) — a
  /// PARAMETER, `D = system_load` or `demand_load_factor · Σ lmax`, put on
  /// the row RHS so the bus balances never reach the requirement;
  /// `"endogenous_generation"` — PLEXOS-compat only: `D = factor ·
  /// (Σ generation − Σ battery charge)` as an LP column, which leaks a
  /// `demand_coef · μ` credit into every LMP (negative prices).
  OptName load_term {};
  OptTBRealFieldSched system_load {};  ///< Given system load D [MW]
                                       ///< (overrides Σ lmax; exogenous)
  OptReal demand_load_factor {};  ///< D = factor · Σ lmax (default 1.05,
                                  ///< conservative; CEN PCP ratio
                                  ///< 1.025–1.05; endogenous form: 1)
  Array<SingleId> demands {};  ///< Demands summed into D (empty = every
                               ///< demand except battery-charge demands)
  OptSingleId inertia_zone {};  ///< InertiaZone whose provisions give I
  /// How the plane inertia term `I` [MWs] is formed (only the CPF
  /// requirement planes; an InertiaZone's own minimum-inertia requirement
  /// is always commitment-based):
  ///  - `"commitment"` (default, CEN minuta Anexo B.1): `I = Σ_c I_c·u_c`
  ///    over the inertia provisions of `inertia_zone`, `u_c` the unit's
  ///    Commitment status, `I_c` its inertia when on
  ///    (`H·S`, or `provision_factor · min(provision_max, pmax)`).  A unit
  ///    with no status column counts only when it is forced on
  ///    (`pmin > 0`).  With a relaxed Commitment it can price a bus
  ///    negative (u rises with pmin·u generation): validate_planning warns,
  ///    remedy "exogenous".
  ///  - `"exogenous"`: `I` is DATA — `system_inertia`, or, when
  ///    that is unset, the conservative must-run sum (units whose status
  ///    is pinned on, or uncommitted units with `pmin > 0`).  No dispatch
  ///    or commitment column reaches the planes.
  ///  - `"endogenous"` (PLEXOS-compat): `I = Σ pf·r`, `r ≤ p` — the
  ///    dispatch-based inertia provision.  Couples dispatch into the
  ///    requirement (a zero-cost unit can price negative).
  OptName inertia_term {};
  OptTBRealFieldSched system_inertia {};  ///< Given system inertia I [MWs]
                                          ///< (`inertia_term: "exogenous"`)
  Array<SingleId> contingency_generators {};  ///< Up contingency set:
                                              ///< C_up ≥ p_g
  Array<SingleId> contingency_demands {};  ///< Down contingency set (loads,
                                           ///< `<bess>_dem` charge):
                                           ///< C_dn ≥ load_d
  Array<SingleId> flow_lines {};  ///< Lines whose signed flow sum enters
                                  ///< the planes through `flow_coef`
  /// General up / down contingency candidates (PLEXOS `*_Comparison`);
  /// combined with the singleton `contingency_generators` /
  /// `contingency_demands` entries.
  Array<ReserveContingency> urcontingencies {};
  Array<ReserveContingency> drcontingencies {};
  /// When true the lost unit's own reserve is lost with it:
  /// `C ≥ a_g·(p_g + r_g)` for every provision `r_g` of a contingency
  /// generator into THIS zone (up rows use its up provision, down rows
  /// its down provision).  Default false = PLEXOS PCP (`*_Comparison`
  /// rows carry no provision term).
  OptBool contingency_includes_reserve {};
  /// @}

  /// Sense of the provision balance: `"ge"` (default, `Σ pf·prov ≥ R`)
  /// or `"eq"` (`Σ pf·prov = R`, PLEXOS `CPF_UpMinProvision` Sense 0).
  /// With `"eq"` a surplus column priced at `ursurplus_cost` /
  /// `drsurplus_cost` relaxes the upper side when the cost is set;
  /// without it the provision is pinned to the requirement.  The
  /// shortage side stays priced by `urcost` / `drcost`.
  OptName requirement_sense {};
  OptTBRealFieldSched ursurplus_cost {};  ///< Up surplus penalty [$/MW]
  OptTBRealFieldSched drsurplus_cost {};  ///< Down surplus penalty [$/MW]

  /// True when either direction uses a plane-based requirement.
  [[nodiscard]] bool has_dynamic_requirement() const noexcept
  {
    return !urreq_planes.empty() || !drreq_planes.empty();
  }
};

}  // namespace gtopt
