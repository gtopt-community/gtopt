/**
 * @file      model_options.hpp
 * @brief     Power system model configuration for LP construction
 * @date      2026-03-24
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#pragma once

#include <gtopt/fail_segment.hpp>
#include <gtopt/field_sched.hpp>
#include <gtopt/transition_integrality.hpp>
#include <gtopt/utils.hpp>

namespace gtopt
{

/**
 * @brief Power system model configuration for LP construction.
 *
 * Groups all options that affect how the mathematical model is
 * formulated: network topology, Kirchhoff constraints, line losses,
 * scaling factors, penalty costs, and discount rate.
 *
 * Used both as a global sub-object in Options (`model_options`) and
 * as per-level overrides in CascadeLevel (`model_options`).
 * When used in a cascade level, only set fields override the global;
 * absent fields inherit from the global configuration.
 */
struct ModelOptions
{
  /// Collapse the network to a single bus (copper-plate model).
  OptBool use_single_bus {};
  /// Apply DC Kirchhoff voltage-law constraints.
  OptBool use_kirchhoff {};
  /// Kirchhoff Voltage Law formulation: `"node_angle"` (B–θ) or
  /// `"cycle_basis"` (loop-flow, default).  See KirchhoffMode enum.
  /// When unset, defaults to `"cycle_basis"` — strictly smaller LP for
  /// meshed grids (no theta column per bus, fewer KVL rows).  Override
  /// with `"node_angle"` for cases with per-stage topology changes or
  /// phase-shift transformers.
  OptName kirchhoff_mode {};
  /// @deprecated Use `line_losses_mode` instead.
  OptBool use_line_losses {};
  /// Line losses model selection.  See LineLossesMode enum for values:
  /// `"none"`, `"linear"`, `"piecewise"`, `"bidirectional"`, `"adaptive"`,
  /// `"dynamic"`.  When unset, defaults to `"adaptive"`.
  OptName line_losses_mode {};
  /// Minimum bus voltage [kV] below which Kirchhoff is not applied.
  OptReal kirchhoff_threshold {};
  /// Per-unit reactance floor `|X/V²|` below which a transmission
  /// line's flow term is dropped from the Kirchhoff matrix, capping the
  /// spread of `x_τ = τ·X/V²` coefficients and dropping kappa.  With
  /// `tiny_reactance_mode = "tie"` (default) such a line becomes a
  /// zero-impedance tie (θ_a − θ_b = φ); with `"dc"` (legacy)
  /// `PlanningLP::validate_line_reactance` rewrites its reactance to
  /// `0.0` and the line is a "DC line" (no Kirchhoff coupling).  Default (when
  /// unset): `1e-6` p.u. — conservative enough that real transmission lines
  /// (`x_pu ≥ 1e-3`) and real distribution lines/cables (`x_pu ≥ 1e-4`)
  /// are never falsely promoted, while V-vs-kV unit-typo lines
  /// (`x_pu ≈ 1e-7…1e-9`) and HVDC/phase-shifter sentinels (`X = 0`)
  /// are caught.  The effective floor is raised to
  /// `tiny_reactance_rel_threshold × median(|X/V²|)` when larger; set both
  /// to `0.0` to disable promotion entirely.
  OptReal dc_line_reactance_threshold {};
  /// Treatment of lines whose reactance is set but is `0` or has
  /// `|X/V²|` below `dc_line_reactance_threshold`: `"tie"` (default)
  /// keeps them as zero-impedance ties (KVL `θ_a − θ_b = φ` without the
  /// flow term: the two bus angles are forced equal) or `"dc"` (legacy)
  /// turns them into DC links without KVL coupling.  See
  /// TinyReactanceMode.  Lines with no `reactance` are DC links in both
  /// modes.
  OptName tiny_reactance_mode {};
  /// Relative tie floor: a line whose `|X/V²|` is below
  /// `tiny_reactance_rel_threshold × median(|X/V²|)` (median over every
  /// line with a nonzero reactance) is treated exactly like one below
  /// the absolute `dc_line_reactance_threshold` — a zero-impedance tie
  /// (or a DC link under `tiny_reactance_mode = "dc"`).  The effective
  /// floor is `max(dc_line_reactance_threshold, rel × median)`; it is
  /// unit-system independent (per-unit, Ω/kV, PLEXOS `V = 10`) and
  /// catches placeholder "ideal connection" reactances (e.g. PLEXOS
  /// `X = 1e-4` p.u. on generator step-up links) sitting right at the
  /// absolute floor.  Default (when unset): `0.01` — a tie then shifts
  /// the flow split of any parallel path by at most ~1 %.  Set to `0`
  /// for the legacy absolute-only floor.
  OptReal tiny_reactance_rel_threshold {};
  /// Losses on zero-impedance ties.  `false` (default): a tie line is
  /// lossless — its `resistance` / `lossfactor` are dropped by
  /// `PlanningLP::validate_line_reactance`, so no loss column, segment,
  /// tangent or link row is built for it (a tie's `R` is as degenerate
  /// as its `X`, and its loss rows would only stamp `R/V²`-sized
  /// coefficients).  `true`: legacy, the tie keeps its loss model.
  OptBool tie_line_losses {};
  /// Per-unit resistance floor `|R/V²|` below which a transmission line's
  /// quadratic losses are dropped (the line is treated as lossless).  Mirrors
  /// `dc_line_reactance_threshold`: `PlanningLP::validate_line_resistance`
  /// rewrites the line's resistance schedule to scalar `0.0`, so the loss
  /// assembler (`line_losses`) takes its `R ≤ 0 → lossless` path and omits the
  /// loss-PWL entirely (both the loss-pricing row AND the `line_loss*_link`
  /// row).  This removes the huge `loss-link` coefficients (`∝ 1/(R/V²)`) that
  /// very-low-R lines — transformers, busbar segments, or V-vs-kV unit-typo
  /// lines (e.g. `R/V² ≈ 5e-8`) — otherwise inject as the largest matrix
  /// entries, blowing up `--no-scale` kappa.  Default (when unset): `1e-7`
  /// p.u. — one decade BELOW the reactance floor because real lines run lower
  /// in `R/V²` than `X/V²` (EHV lines have `R ≪ X`; a real 220 kV `R = 0.01 Ω`
  /// line is at `R/V² = 2e-7`), so `1e-7` spares genuine lossy lines while
  /// catching the degenerate ones.  Set to `0.0` to disable.
  OptReal dc_line_resistance_threshold {};
  /// Number of piecewise-linear segments for quadratic line losses.
  OptInt loss_segments {};
  /// Divisor for all objective coefficients (numerical stability).
  OptReal scale_objective {};
  /// Voltage-angle enlargement: θ_LP = scale_theta · θ (rad), so `1` =
  /// radians and `1000` = milliradians.  When unset (and `auto_scale`),
  /// `1 / (3 · median(X/V²))`: 1/median would match the median line's
  /// KVL coefficients, and the extra ≈√D = 3 accounts for θ accumulating
  /// ~D line drops to the reference bus.
  /// Unset with `auto_scale = false` ⇒ `1`.  NOTE: before 2026-11 the
  /// meaning was inverted (θ = θ_LP · scale_theta); values < 1 now shrink
  /// θ and trigger a load-time warning.
  OptReal scale_theta {};
  /// Row-scale factor for the line-loss linking constraint
  /// (`loss − Σ loss_k · seg_k = 0`).  When unset, `auto_scale_loss_link`
  /// computes a power-of-10 factor from `median(R/V²)` so the smallest
  /// segment coefficient `seg_width · R · 1 / V²` is lifted into a
  /// numerically tractable range.  Set explicitly to override; set to
  /// `1.0` to disable row scaling.  Has no effect on `linear`,
  /// `piecewise_direct`, or `none` loss modes.
  OptReal scale_loss_link {};
  /// Global per-MWh cost on the per-direction loss columns
  /// (``loss_p``/``loss_n`` in ``piecewise``/``bidirectional`` modes).
  /// Strictly breaks the LP-relax bidirectional-flow degeneracy: among
  /// all primal-feasible solutions sharing the same net dispatch, the
  /// LP picks the one with single-direction flow (``fn = 0`` for net
  /// flow ``f = fp − fn ≥ 0``).  The $/MWh value is block-duration
  /// weighted like any energy cost.  Unset resolves to 0.1 $/MWh
  /// (``PlanningOptionsLP::default_loss_cost_eps``); ``0`` disables it.
  /// Per-line ``Line.loss_cost_eps`` overrides this global default.
  /// Inert for ``none``, ``linear`` and ``piecewise_direct`` (no loss
  /// column).  In ``tangent_signed_flow`` ε prices the LOSS column
  /// (the abs-flow proxy ``v`` gets the ``loss_flow_abs_cost``
  /// degeneracy pin when ε > 0), and ε > 0 is REQUIRED when
  /// ``loss_secant_segments > 1`` without SOS2.  Sized
  /// ≥ ½·|worst credible bus-dual pair-sum| it is the pure-LP
  /// arbitrage guard, at loss-scaled (≈2λ·ε) incidence; see the
  /// sizing guidance at ``Line.loss_cost_eps`` (line.hpp).
  OptReal loss_cost_eps {};
  /// Global per-MWh cost [$/MWh, real cost units before
  /// ``scale_objective``] on the ``tangent_signed_flow`` abs-flow proxy
  /// columns ``v_l`` (``Σ v_l ≥ |f|``).  A degeneracy pin: it makes
  /// ``Σ v_l = |f|`` at the LP optimum, which keeps the secant chord
  /// upper bound on the loss anchored.  Its LMP footprint is bounded:
  /// at most ≈ ``loss_flow_abs_cost`` $/MWh of extra price difference
  /// across each lossy line, in the flow direction.  Unset resolves to
  /// ``PlanningOptionsLP::default_loss_flow_abs_cost`` (0.001 $/MWh);
  /// ``0`` disables the pin; ``1e-6`` reproduces the pre-option
  /// internal constant exactly.  Applied only when the line's resolved
  /// ``loss_cost_eps > 0`` (an explicit ε = 0 keeps the legacy no-pin
  /// formulation).  Per-line ``Line.loss_flow_abs_cost`` overrides.
  /// Inert outside ``tangent_signed_flow``.
  OptReal loss_flow_abs_cost {};
  /// Global price [$/MWh] on the loss carried by the OVERFLOW tier of a
  /// decoupled ``loss_envelope`` (flow between the envelope and the flow
  /// cap).  Each overflow |f| column / secant segment / λ breakpoint
  /// past the envelope is charged ``loss_overflow_cost × (its marginal
  /// loss)``, so the tier costs exactly ``loss_overflow_cost`` per MWh
  /// of loss it carries above ``R·envelope²/V²``.  Without it the tier
  /// is a free energy sink of up to ``R·fmax²/V²`` MW per line whenever
  /// a bus price goes negative (any pure-LP hull that reaches ±fmax on
  /// the quadratic contains ``(0, R·fmax²/V²)``).  Unset resolves to the
  /// case's global ``demand_fail_cost`` (per-demand ``fcost`` is not
  /// consulted; itself 1000 when unset), or to 1000 $/MWh with a warning when
  /// it is 0;
  /// ``0`` disables the guard.  Per-line ``Line.loss_overflow_cost``
  /// overrides.  Inert when the flow cap does not exceed the envelope.
  OptReal loss_overflow_cost {};
  /// Global default for ``Line.loss_secant_segments`` (issue #504).
  /// When ``> 1`` and ``loss_use_sos2`` is enabled, the
  /// ``tangent_signed_flow`` loss model emits ``L`` segment columns
  /// per (line, block) with an SOS2 ordering constraint, tightening
  /// the loss upper bound from a single secant to a piecewise chord.
  /// Per-line ``Line.loss_secant_segments`` overrides this default.
  /// Unset → ``1`` (single-secant chord, current production behaviour).
  OptInt loss_secant_segments {};
  /// Global default for ``Line.loss_use_sos2`` (issue #504).
  /// Toggles SOS2 enforcement on the ``L`` secant-segment columns
  /// emitted by the ``tangent_signed_flow`` model when
  /// ``loss_secant_segments > 1``.  REQUIRES a MIP-capable LP
  /// backend with native SOS2 (CPLEX / Gurobi / HiGHS ≥ 1.6);
  /// the LP build raises a structured error if SOS2 is requested
  /// with an unsupporting backend.  Per-line override beats this
  /// global.  Unset → false.
  OptBool loss_use_sos2 {};
  /// Ordered-fill cap on PWL loss segments (default `true`): every flow
  /// segment column `seg_k` (and the `|f|` segment columns `v_l` of
  /// `tangent_signed_flow`) whose breakpoint interval `[b_{k−1}, b_k]`
  /// reaches past the line's enforced flow cap `tmax` gets the upper
  /// bound `max(0, min(w_k, tmax − b_{k−1}))` — the most it carries in
  /// the physical (ordered) fill at `|f| ≤ tmax`, so the segments
  /// together book at most the ordered secant at `tmax` (`R·tmax²/V²`
  /// when `tmax` is a breakpoint).  A valid tightening cut: it
  /// never removes a physical operating point and only bites when the
  /// segments cover more than `tmax` (a `loss_envelope` wider than the
  /// flow cap, or the `fp + fn` circulation of `piecewise`).  It limits
  /// non-physical loss inflation; it is not an arbitrage fix.  `false`
  /// restores the legacy width-only bounds.
  OptBool loss_segment_bound {};
  /// Bound for voltage-angle variables: `θ ∈ [−theta_max, +theta_max]`.
  /// When unset, `PlanningLP::auto_scale_theta` computes it as
  /// `Σ_l tmax_l · x_τ_l` (a topology-aware upper bound on the
  /// largest possible θ spread between any two buses) so the bound
  /// never artificially caps line flows below their `tmax`.  The
  /// historical hardcoded `2π` default is preserved as a fallback when
  /// `auto_scale=false`.
  OptReal theta_max {};
  /// Enable per-element automatic scaling (reservoir energy/flow, LNG
  /// terminal energy, bus theta) that `PlanningLP` computes at
  /// construction time.  When unset or true, the default heuristics
  /// (adaptive emax/fmax → power-of-10 scale, median x_τ for
  /// scale_theta) run.  When set to false (typically via the
  /// `--no-scale` CLI flag), all three auto-scale passes are
  /// skipped so LP coefficients stay in raw physical units —
  /// useful for debug / coefficient validation, at the cost of
  /// much higher solver kappa.
  OptBool auto_scale {};
  /// Penalty cost for unserved demand [$/MWh].
  OptReal demand_fail_cost {};
  /// Default stepwise demand-failure (VoLL) curve for demands that
  /// define neither ``fcost`` nor ``fail_segments``.  Takes precedence
  /// over ``demand_fail_cost``.  Segment costs are scalars.
  std::optional<Array<ScalarFailSegment>> demand_fail_segments {};
  /// Penalty cost for unserved spinning-reserve [$/MWh].
  OptReal reserve_shortage_cost {};
  /// Default penalty cost for unmet hydro rights [$/m³].
  /// Per-element `fail_cost` overrides this global default.
  OptReal hydro_spill_cost {};
  /// Default value (benefit) of exercising hydro rights [$/m³].
  /// Per-element `use_value` overrides this global default.
  OptReal hydro_use_value {};
  /// Penalty cost for state variable violations in SDDP elastic filter
  /// [$/MWh].  Used as fallback when a reservoir (or other storage element)
  /// does not define its own `scost`.  Converted to physical units using
  /// the element's `mean_production_factor`.
  OptReal state_violation_cost {};

  /// Demand-failure substitution with RHS shift (renamed from the
  /// legacy `demand_option_c` per §11.10 of
  /// `docs/analysis/naming-conventions.md`; both Options A and C
  /// substitute `fail = lmax − load`, but Option C *additionally*
  /// shifts the `+fail_cost·ecost·lmax` baseline off `obj_constant`
  /// onto the bus-balance and capacity rows — the new name
  /// describes that distinguishing behaviour rather than referencing
  /// an internal code label).  The legacy `demand_option_c` JSON
  /// key is accepted as an alias via the naming-dialects registry.
  ///
  /// When false (default): demand_lp emits the column as
  /// `load ∈ [0, lmax]` with cost = `−fail_cost × ecost` and folds
  /// the `+fail_cost × ecost × lmax` baseline into
  /// `lp.add_obj_constant(...)` (Option A — current behaviour).
  ///
  /// When true: column is `neg_fail = load − lmax ∈ [−lmax, 0]`,
  /// `obj_constant` stays at 0, and the baseline is absorbed by RHS
  /// shifts on the bus-balance row (`+(1+loss)·lmax`) and capacity
  /// row (`+lmax`).  The AMPL resolver receives a per-block offset
  /// so user constraints referencing `demand.load` keep their
  /// physical meaning (resolved as `col + lmax`).
  ///
  /// Option C eliminates the ~$105 B obj_constant baseline visible
  /// on CEN-scale runs — `get_obj_value()` matches
  /// `get_obj_value_raw() × scale_objective` exactly with no
  /// large-magnitude cancellation noise.  Demands whose load column
  /// is coupled by another element (a Converter's battery-charge
  /// demand, a Pump's demand) always stay on Option A: those rows
  /// need the column to carry `load`.
  OptBool demand_fail_rhs_shift {};

  /// Phase range expression controlling which phases use LP relaxation
  /// (all integer/binary variables become continuous).
  /// Syntax: `"all"`, `"none"` (default), `"0"`, `"1,3:5,8:"`, `":3"`.
  /// Sets `phase.continuous = true` on matching phases at LP setup time.
  /// Settable per cascade level or globally via
  /// `--set model_options.continuous_phases="all"`.
  OptName continuous_phases {};

  /// Name of the naming dialect to enforce on input and output.
  ///
  /// When set, the alias→canonical canonicalization (input) emits a
  /// once-per-alias warning whenever an input key matches an alias whose
  /// `dialect` tag differs from this value — useful for catching
  /// mixed-dialect input.  The reverse output rename (canonical → alias)
  /// is also driven by this field: the JSON writer rewrites canonical
  /// keys to the chosen dialect's aliases when emitting `planning.json`
  /// (parquet column rename is deferred to a follow-up).
  ///
  /// Recognised values are the `dialect` tags in
  /// `share/gtopt/naming_dialects.json` (e.g. `"gtopt"`, `"plp"`,
  /// `"sddp"`, `"plexos"`, `"pypsa"`, `"pandapower"`).  Unset (default)
  /// preserves the pre-feature behaviour: every alias is accepted on
  /// input, every key is emitted in canonical form on output.
  ///
  /// Settable via `--naming-dialect <name>` or
  /// `--set model_options.naming_dialect=<name>`.
  OptName naming_dialect {};

  /// Objective formulation switch (issue #519).
  ///
  ///   * ``"cost"`` (default, or unset) — minimize the standard
  ///     dispatch cost: Σ (generator gcost + fuel·heat_rate·gen +
  ///     demand_fail·fail + line_overload·slack + … etc.).
  ///   * ``"emissions"`` — minimize weighted emissions.  Only the
  ///     generator dispatch slope (``gcost`` + fuel·heat_rate on the
  ///     ``generation`` column) is zeroed; each ``EmissionSource``
  ///     then adds ``price × weight × (1 − capture) × rate`` onto
  ///     that column, with ``price`` = the zone's ``price`` when set,
  ///     else 1.0 (tCO2eq).  Every other cost term is KEPT: piecewise
  ///     heat-rate segment slack costs (``heat_rate_segments``),
  ///     CCS ``capture_cost`` adders, demand-fail / reserve /
  ///     line-overload / soft-pmin / emission-cap slack penalties,
  ///     commitment costs and CAPEX — so the objective is emissions
  ///     plus those residual terms, not pure tonnage.  By LP duality,
  ///     the resulting ``Reservoir/water_value_dual`` and
  ///     ``Battery/energy_dual`` parquets then carry the carbon
  ///     opportunity cost of stored water / energy (tCO2eq per
  ///     MWh-equivalent) directly — the theoretically correct
  ///     answer to the "storage marginal emission" question that
  ///     ``gtopt_marginal_units`` currently approximates with a
  ///     merit-ladder consequential-MOER heuristic.  Used as the
  ///     second pass of the two-solve workflow orchestrated by
  ///     ``scripts/gtopt_emission_dual/`` (see issue #519).
  ///
  /// LP shape is identical between modes; only objective
  /// coefficients change.  See ``GeneratorLP::add_to_lp`` (slope
  /// zeroing) and ``EmissionSourceLP::add_to_lp`` (per-tonne adder).
  ///
  /// Settable via ``--set model_options.objective_mode=<name>``
  /// (there is no dedicated CLI flag).
  OptName objective_mode {};

  /// Whether to enforce the per-stage `emin` floor as a HARD lower bound
  /// on the reservoir's stage-end volume (`efin =
  /// reservoir_energy_<last_block>`) and on the stage-start volume
  /// (`reservoir_sini`).
  ///
  /// `true` (default): both columns get `lowb = stage_emin`.  The floor is a
  /// hard constraint at the inter-stage handoff state, giving the strictest
  /// volume-constraint enforcement.  Intra-stage blocks still use `lowb = 0`
  /// (see `storage_lp.hpp`) so the energy-balance row keeps PLP-style
  /// headroom mid-stage.
  ///
  /// `false` (opt-out, PLP-style): both columns have `lowb = 0`.  The floor
  /// is not enforced as a constraint; SDDP convergence is responsible for
  /// keeping the trajectory above `emin`.  Matches PLP's per-stage LP where
  /// `ve<u>` is `Free` mid-stage and only `vf<u>` (future volume) has the
  /// `vmin` lower bound.  Use this if iter-0 of an SDDP cascade
  /// over-constrains the forward pass when state vars cluster at the floor.
  OptBool strict_storage_emin {};

  /// Assembly-time elimination of provably-zero LP columns/rows at the
  /// SOURCE element (e.g. the generator skips its generation column when
  /// `pmax == pmin == 0`; the reservoir skips a zero extraction column;
  /// reserve/inertia provisions skip a zero-ceiling column; the inertia
  /// zone skips a zero-requirement row).  Default `false`.
  ///
  /// DEFAULT OFF — the un-reduced LP is the honest model and a strong
  /// presolve reduces it to the same core.  Enable with
  /// `--lp-reduction` (or `model_options.lp_reduction = true`) for the
  /// weak-presolve backends (CLP / CBC / HiGHS) that benefit from the
  /// smaller LP up front — they will not otherwise strip the provably
  /// zero columns, and a serial-barrier solver (HiGHS IPM) factorizes the
  /// bigger matrix on one core.
  ///
  /// NOT strictly optimum-neutral: the reservoir / provision / requirement
  /// skips are pure `[0,0]` columns or `Σ≥0` rows and never move the
  /// optimum, but the GENERATOR skip cascades into dropping a commitment
  /// `u/v/w` for the pmax==pmin==0 unit — a real `[0,1]` binary that lets
  /// the unit be committed (must-run noload / synchronous-condenser
  /// inertia).  Enabling reduction therefore encodes "no generation column
  /// ⟹ no commitment ⟹ no ancillary participation" and can shift the
  /// optimum slightly (+$20k / 0.004% on 20260517, ~94% of it a
  /// demand-failure / reserve-shortage penalty slack).  Leaving it OFF
  /// keeps the faithful model.  Consumers never need this flag — the
  /// switch lives ONLY at the source.
  OptBool lp_reduction {};

  /// Relax the per-bus power-balance equality (`Σ generation·(1−lf) ±
  /// flows = Σ demand·(1+lf)`) to a `≥` inequality (`generation ≥
  /// demand`), i.e. free disposal of surplus: over-generation is allowed
  /// and the excess is discarded at zero cost.  Default `false` (strict
  /// balance).
  ///
  /// Reserve and inertia PRODUCTION are already `≥ requirement`
  /// (`reserve_zone_lp` / `inertia_zone_lp` emit `greater_equal` rows), so
  /// this flag only affects the bus balance.  Water balances, KVL, and
  /// commitment-logic equalities are NOT relaxed (they are physical /
  /// structural, not supply-demand balances).
  ///
  /// Marginal-theory note: with `≥`, a bus dual is `≥ 0` (over-supplied
  /// buses price at 0), removing the negative-LMP artefacts that a strict
  /// `=` balance produces under must-run/ancillary over-production.  Use
  /// with care — it changes the reported LMPs and lets the model spill
  /// generation for free.  `--set model_options.allow_oversupply=true`.
  OptBool allow_oversupply {};

  /// Planning-wide default self-discharge for every `Battery` whose own
  /// `annual_standing_loss` is ABSENT [p.u./year, linear convention: hourly
  /// drain = annual_standing_loss / 8760].  When unset, resolves to
  /// `PlanningOptionsLP::default_battery_annual_standing_loss` = `0.48`
  /// p.u./year (4 %/month × 12, the lithium-ion cell + BMS self-discharge; see
  /// `docs/input-data.md` → Battery for the references).  An explicit
  /// per-battery `annual_standing_loss` (including `0`) always wins.  Set to
  /// `0`
  /// (`--set model_options.battery_annual_standing_loss=0`) to restore the
  /// legacy lossless default for a whole case.  Applies to `Battery` only —
  /// Reservoir, LngTerminal and the other storages keep their own
  /// `annual_standing_loss` default of `0`.
  OptReal battery_annual_standing_loss {};

  /// Emit the battery power-envelope valid inequality
  /// `p_charge/Pc_max + p_discharge/Pd_max ≤ 1` per block on every
  /// battery converter (`Converter/power_envelope` rows).  Default
  /// `true`.  It cuts simultaneous full-power charge + discharge, the
  /// LP vertex a lossy battery uses to dump surplus energy; partial
  /// simultaneous operation inside the envelope remains LP-feasible.
  /// Set `false` to reproduce the pre-envelope LP.
  OptBool battery_power_envelope {};

  /// How `validate_planning` treats a ReserveZone requirement that depends
  /// on endogenous (dispatch) quantities — `load_term:
  /// "endogenous_generation"`, plane `flow_coef` on `flow_lines`,
  /// contingency candidates with lines (signed flow) or
  /// `contingency_includes_reserve`, `inertia_term: "endogenous"`.  Such a
  /// coupling puts `β·μ` (plane coefficient × reserve price) into the LMP
  /// of every bus that reaches the coupled column, which can price buses
  /// negative.  `"warn"` (default) logs a warning, `"error"` rejects the
  /// case, `"off"` is silent.
  OptName reserve_coupling {};

  /// How nodal prices (bus balance duals, reduced costs) are recovered
  /// after a MIP (unit-commitment) solve.  The reported dispatch is the
  /// MIP solution in every mode; only the duals change.  Values (see
  /// `PricingMode`, `include/gtopt/pricing_mode.hpp`):
  ///   * `"fixed"` (default) — fix every integer at the MIP incumbent and
  ///     re-solve the LP (the classic restricted / "IP" pricing).
  ///   * `"relaxed"` — duals of the LP relaxation of the full MIP (all
  ///     integers continuous in their bounds), solved as a separate
  ///     pricing pass after the MIP.  Approximates convex-hull pricing
  ///     when the UC formulation is tight.
  ///   * `"fast_start"` — integers fixed at the incumbent EXCEPT the
  ///     commitment u/v/w columns of fast-start units, which are relaxed
  ///     to continuous [0,1] (PJM/NYISO "integer relaxation" fast-start
  ///     pricing).
  /// A pricing LP that is not optimal falls back to `"fixed"` prices
  /// (warned and recorded in `pricing.csv`).  CLI: `--pricing-mode`.
  OptName pricing_mode {};

  /// Auto rule threshold [h] for `Commitment.fast_start`: a commitment
  /// without an explicit `fast_start` is fast-start when both
  /// `min_up_time` and `min_down_time` (unset = 0) are `<=` this value.
  /// gtopt has no startup-time / notification-time field, so the rule
  /// uses min up/down only.  Default 1 h.
  OptReal fast_start_max_hours {};
  /// Ramp-rate discretisation for `Commitment.ramp_up` / `ramp_down`:
  ///  - `"backward"` (default, block and point stages): the interval
  ///    slope `−RD·u_k·h_k − SD·w_k ≤ p_k − p_{k−1} ≤ RU·u_{k−1}·h_k +
  ///    SU·v_k` with `h_k` the block length (block stages) or the node
  ///    spacing `t_k − t_{k−1}` (point stages; cyclic stages wrap
  ///    `h_0 = t_0 + H − t_{N−1}`).
  ///  - `"central2"` (opt-in, not recommended): second-order derivative
  ///    on the node grid `t_b`: interior `P'(t_k) = [h₋²P_{k+1} −
  ///    h₊²P_{k−1} + (h₊² − h₋²)P_k] / (h₊h₋(h₊ + h₋))`.  It is a convex
  ///    combination of the two adjacent slopes, so a zig-zag (with the
  ///    cyclic wrap, the checkerboard null vector) passes the rows with
  ///    arbitrarily large interval slopes.  A warning is issued.
  OptName ramp_formula {};

  /// `central2` only: treat the stage as a cycle (the first and last nodes
  /// use the wrapped neighbour across the period end, `period_hours`)
  /// instead of the one-sided end differences.  Default false.
  OptBool ramp_wrap {};

  /// Unit-commitment formulation on chronological block stages:
  ///  - `"energy_block"` (default): the tight three-binary model on
  ///    block-average power (C2 generation bounds, C4/C5 ramps between
  ///    block averages).
  ///  - `"power_based"`: Morales-España et al. power-based UC — the
  ///    power at every block boundary is a column, each block energy is
  ///    the trapezoid of its two boundary powers (read by the bus
  ///    balance, costs, fuel, emissions and storage through the
  ///    generation column), ramps bound the instantaneous power,
  ///    start-up / shut-down trajectories carry their energy and the
  ///    reserves are power-capacity and ramp-capability reserves.
  ///    Binaries stay on the (uniform, >= 1 h) commitment grid.
  /// `Commitment.uc_formulation` overrides it per unit.  See
  /// docs/unit-commitment.md §4.6.
  OptName uc_formulation {};

  /// Storage-balance time integration on (possibly non-uniform) node grids:
  ///  - `"block"` (default): `e_b = e_{b−1} + f_b·Δt_b` (block weight).
  ///    `"cc"` / `"clenshaw_curtis"` are aliases: on a Chebyshev node grid
  ///    the block weights are the Clenshaw–Curtis weights (node 0 carries
  ///    `w_0 + w_N`), so the closed-period ΣΔe is exactly the CC
  ///    quadrature of the net flow — the one that integrates energy and
  ///    cost.  As sparse as a block balance, positive weights.
  ///  - `"trapezoid"`: 2 coefficients per row, the increment over
  ///    `[t_{k−1}, t_k]` is `(t_k − t_{k−1})/2·(f_{k−1} + f_k)`; exact for
  ///    linear flows; columns corrected as below.
  ///  - `"lg4"` / `"lg6"`: banded local Lagrange rule on the
  ///    node times (`Block.time`, default block midpoints): the increment
  ///    over `[t_{k−1}, t_k]` integrates the cubic / quintic interpolant of
  ///    the flows on the 4 / 6 nearest nodes, columns corrected so the
  ///    period total equals the block-weight (Clenshaw–Curtis) quadrature;
  ///    `e_k` is then the level AT node `t_k` (last one at the period end).
  ///  - `"lg4_pos"` / `"lg6_pos"` (recommended high-order rule): same,
  ///    negative weights clipped to 0, then a balanced row / column
  ///    scaling keeps every weight ≥ 0 with column sums = block weights
  ///    and row sums = interval lengths.
  ///  - `"lg4"` / `"lg6"` with signed weights are NOT recommended for an
  ///    LP: negative weights shift energy between intervals (phantom
  ///    shifts) and invalidate the charge / discharge envelope (built
  ///    from max(D, 0) when used).
  /// Only the storage's own charge / discharge / drain flows are banded.
  /// Resolution per storage element: the element's own
  /// `storage_integration` → `reservoir_storage_integration` (reservoirs)
  /// / `battery_storage_integration` (batteries and the other energy
  /// storages) → this global value → the built-in per-type default
  /// (`lg4_pos` for reservoirs, `block` otherwise).  Block stages always
  /// use the block recursion.
  OptName storage_integration {};

  /// Point-stage storage integration of reservoirs (see
  /// `storage_integration`).  Unset: the global value, else `lg4_pos` —
  /// a reservoir's intermediate-level error times its accumulated
  /// production factor can be as large as a battery's whole generation.
  OptName reservoir_storage_integration {};

  /// Point-stage storage integration of batteries and the other energy
  /// storages (hydrogen, thermal, ammonia).  Unset: the global value,
  /// else `block`.
  OptName battery_storage_integration {};

  /// Planning-level default of `Commitment.long_transition` ("on" /
  /// "off" / "no_uc") for cyclic units whose min up / down time or start
  /// window exceeds the typical period.  Explicit only: every affected
  /// unit still gets a warning.  Unset: undeclared units are an error.
  OptName long_transition {};

  /// Default integrality of the commitment startup / shutdown columns
  /// (v / w); `Commitment.transition_integrality` overrides it per unit.
  /// See `TransitionIntegrality` (transition_integrality.hpp):
  ///   * `"auto"` (default) — binary (exact);
  ///   * `"continuous"` — always continuous in [0, 1] (not exact with
  ///     start / stop ramps);
  ///   * `"binary"` — always binary.
  /// The status u is always integer on a MIP build; `relax`,
  /// `continuous_phases` and `--no-mip` relax u, v, w together.
  OptName transition_integrality {};

  /// REMOVED 2026-11-26 — the commitment rows `v <= u` / `w <= 1 - u`
  /// no longer exist.  The key is still parsed (bool or name) so old
  /// inputs load; `validate_planning` warns once and it is ignored.
  OptBoolOrName transition_status_rows {};

  /// Default integrality of the hot / warm / cold startup-tier selectors;
  /// `Commitment.startup_tier_integrality` overrides it per unit.
  /// `"continuous"` (default: the tier rows are totally unimodular once
  /// v / w are integral) or `"integer"`.  See `StartupTierIntegrality`.
  OptName startup_tier_integrality {};

  /// How a line's thermal limit (`tmax_ab` / `tmax_ba`, and the
  /// installed capacity of expandable lines) interacts with its loss
  /// variable.  See `LineThermalLimitMode` (line_enums.hpp):
  ///   * `"flow"` (default) — the limit applies to the flow variable
  ///     only (no extra constraint; historical behaviour);
  ///   * `"sending"` — the sending-end power, i.e. the flow plus the
  ///     share of the loss the line's `loss_allocation_mode` books at
  ///     the sending bus, stays within the limit;
  ///   * `"full"` — flow plus the whole loss stays within the limit
  ///     (`f + p_loss ≤ tmax` per direction).
  /// Per-line `Line.line_thermal_limit_mode` overrides this default.
  OptName line_thermal_limit_mode {};

  /// Radial-line detection: `"off"` (default; no detection, LP
  /// unchanged), `"report"` (detect radial lines and their structural
  /// flow direction and log them; LP unchanged) or `"structural"`
  /// (also apply the certified one-way and dead-end lines).  See
  /// `LineDirectionMode` (line_enums.hpp) and `line_direction.hpp`.
  /// Per-line `Line.flow_direction` overrides it.
  OptName line_direction_mode {};

  /// What a user-constraint `<attr>_prev` term reads at the first block
  /// of a stage whose predecessor is in the same phase.  See
  /// `PrevStageLink` (planning_enums.hpp):
  ///   * `"stage"` (default) — the previous stage's last block;
  ///   * `"none"` — the constant 0 (legacy behaviour).
  OptName prev_stage_link {};

  /// Global switch for the dynamic line rating (`Line.dlr`).  Default
  /// (unset) `true`: every line that carries a `dlr` block is limited by
  /// its critical-span ratings.  `false` ignores every `dlr` block (the
  /// lines keep their static `tmax`) — handy for static-vs-DLR runs.
  OptBool use_line_dlr {};

  /// Default `Line.dlr.limit_mode`: `"replace"` (the DLR rating replaces
  /// the conductor rating, or `max_rating` without one) or `"min"`
  /// (`min(static, DLR)`).  Unset: `replace` on lines with a
  /// `conductor_rating_*`, `min` otherwise.
  OptName dlr_limit_mode {};

  /// Default `Line.overload_slack_bound`: `"thermal"` (default;
  /// `min(hard − normal, max_overload_ratio × normal)`), `"ratio"` or
  /// `"unbounded"`.
  OptName overload_slack_bound {};
  /// Default `Line.max_overload_ratio` (default 1.5: the slack is at most
  /// +150 % of the normal threshold, flow ≤ 2.5 × normal — a guard rail
  /// well above reported DLR gains: +10–25 % typical, ~40 % at windy
  /// sites, 50–72 % outliers).
  OptReal max_overload_ratio {};

  void merge(const ModelOptions& opts)
  {
    merge_opt(use_single_bus, opts.use_single_bus);
    merge_opt(use_kirchhoff, opts.use_kirchhoff);
    merge_opt(kirchhoff_mode, opts.kirchhoff_mode);
    merge_opt(use_line_losses, opts.use_line_losses);
    merge_opt(line_losses_mode, opts.line_losses_mode);
    merge_opt(kirchhoff_threshold, opts.kirchhoff_threshold);
    merge_opt(dc_line_reactance_threshold, opts.dc_line_reactance_threshold);
    merge_opt(tiny_reactance_mode, opts.tiny_reactance_mode);
    merge_opt(tiny_reactance_rel_threshold, opts.tiny_reactance_rel_threshold);
    merge_opt(tie_line_losses, opts.tie_line_losses);
    merge_opt(dc_line_resistance_threshold, opts.dc_line_resistance_threshold);
    merge_opt(loss_segments, opts.loss_segments);
    merge_opt(scale_objective, opts.scale_objective);
    merge_opt(scale_theta, opts.scale_theta);
    merge_opt(scale_loss_link, opts.scale_loss_link);
    merge_opt(loss_cost_eps, opts.loss_cost_eps);
    merge_opt(loss_flow_abs_cost, opts.loss_flow_abs_cost);
    merge_opt(loss_overflow_cost, opts.loss_overflow_cost);
    merge_opt(loss_secant_segments, opts.loss_secant_segments);
    merge_opt(loss_use_sos2, opts.loss_use_sos2);
    merge_opt(loss_segment_bound, opts.loss_segment_bound);
    merge_opt(theta_max, opts.theta_max);
    merge_opt(auto_scale, opts.auto_scale);
    merge_opt(demand_fail_cost, opts.demand_fail_cost);
    merge_opt(demand_fail_segments, opts.demand_fail_segments);
    merge_opt(reserve_shortage_cost, opts.reserve_shortage_cost);
    merge_opt(hydro_spill_cost, opts.hydro_spill_cost);
    merge_opt(hydro_use_value, opts.hydro_use_value);
    merge_opt(state_violation_cost, opts.state_violation_cost);
    merge_opt(demand_fail_rhs_shift, opts.demand_fail_rhs_shift);
    merge_opt(continuous_phases, opts.continuous_phases);
    merge_opt(naming_dialect, opts.naming_dialect);
    merge_opt(objective_mode, opts.objective_mode);
    merge_opt(strict_storage_emin, opts.strict_storage_emin);
    merge_opt(lp_reduction, opts.lp_reduction);
    merge_opt(allow_oversupply, opts.allow_oversupply);
    merge_opt(battery_annual_standing_loss, opts.battery_annual_standing_loss);
    merge_opt(battery_power_envelope, opts.battery_power_envelope);
    merge_opt(reserve_coupling, opts.reserve_coupling);
    merge_opt(pricing_mode, opts.pricing_mode);
    merge_opt(fast_start_max_hours, opts.fast_start_max_hours);
    merge_opt(transition_integrality, opts.transition_integrality);
    merge_opt(transition_status_rows, opts.transition_status_rows);
    merge_opt(startup_tier_integrality, opts.startup_tier_integrality);
    merge_opt(line_thermal_limit_mode, opts.line_thermal_limit_mode);
    merge_opt(line_direction_mode, opts.line_direction_mode);
    merge_opt(prev_stage_link, opts.prev_stage_link);
    merge_opt(use_line_dlr, opts.use_line_dlr);
    merge_opt(dlr_limit_mode, opts.dlr_limit_mode);
    merge_opt(overload_slack_bound, opts.overload_slack_bound);
    merge_opt(max_overload_ratio, opts.max_overload_ratio);
    merge_opt(ramp_formula, opts.ramp_formula);
    merge_opt(ramp_wrap, opts.ramp_wrap);
    merge_opt(uc_formulation, opts.uc_formulation);
    merge_opt(storage_integration, opts.storage_integration);
    merge_opt(reservoir_storage_integration,
              opts.reservoir_storage_integration);
    merge_opt(battery_storage_integration, opts.battery_storage_integration);
    merge_opt(long_transition, opts.long_transition);
  }

  /// True if any field is set.
  [[nodiscard]] bool has_any() const noexcept
  {
    return use_single_bus.has_value() || use_kirchhoff.has_value()
        || kirchhoff_mode.has_value() || use_line_losses.has_value()
        || line_losses_mode.has_value() || kirchhoff_threshold.has_value()
        || dc_line_reactance_threshold.has_value()
        || tiny_reactance_mode.has_value()
        || tiny_reactance_rel_threshold.has_value()
        || tie_line_losses.has_value()
        || dc_line_resistance_threshold.has_value() || loss_segments.has_value()
        || scale_objective.has_value() || scale_theta.has_value()
        || scale_loss_link.has_value() || loss_cost_eps.has_value()
        || loss_flow_abs_cost.has_value() || loss_overflow_cost.has_value()
        || loss_secant_segments.has_value() || loss_use_sos2.has_value()
        || loss_segment_bound.has_value() || theta_max.has_value()
        || demand_fail_cost.has_value() || demand_fail_segments.has_value()
        || reserve_shortage_cost.has_value() || hydro_spill_cost.has_value()
        || hydro_use_value.has_value() || state_violation_cost.has_value()
        || demand_fail_rhs_shift.has_value() || continuous_phases.has_value()
        || naming_dialect.has_value() || objective_mode.has_value()
        || strict_storage_emin.has_value() || lp_reduction.has_value()
        || allow_oversupply.has_value()
        || battery_annual_standing_loss.has_value()
        || battery_power_envelope.has_value() || reserve_coupling.has_value()
        || pricing_mode.has_value() || fast_start_max_hours.has_value()
        || transition_integrality.has_value()
        || transition_status_rows.has_value()
        || startup_tier_integrality.has_value()
        || line_thermal_limit_mode.has_value() || prev_stage_link.has_value()
        || line_direction_mode.has_value() || use_line_dlr.has_value()
        || dlr_limit_mode.has_value() || overload_slack_bound.has_value()
        || max_overload_ratio.has_value() || ramp_formula.has_value()
        || ramp_wrap.has_value() || uc_formulation.has_value()
        || storage_integration.has_value()
        || reservoir_storage_integration.has_value()
        || battery_storage_integration.has_value()
        || long_transition.has_value();
  }

  /// True iff every field set in `other` has an equal value in `*this`.
  /// Fields that `other` leaves unset are ignored.  Semantically: "applying
  /// `other` as an override on top of `*this` would not change anything".
  [[nodiscard]] bool covers(const ModelOptions& other) const
  {
    const auto covers_opt = [](const auto& self, const auto& override_val)
    { return !override_val.has_value() || self == override_val; };
    return covers_opt(use_single_bus, other.use_single_bus)
        && covers_opt(use_kirchhoff, other.use_kirchhoff)
        && covers_opt(kirchhoff_mode, other.kirchhoff_mode)
        && covers_opt(use_line_losses, other.use_line_losses)
        && covers_opt(line_losses_mode, other.line_losses_mode)
        && covers_opt(kirchhoff_threshold, other.kirchhoff_threshold)
        && covers_opt(dc_line_reactance_threshold,
                      other.dc_line_reactance_threshold)
        && covers_opt(tiny_reactance_mode, other.tiny_reactance_mode)
        && covers_opt(tiny_reactance_rel_threshold,
                      other.tiny_reactance_rel_threshold)
        && covers_opt(tie_line_losses, other.tie_line_losses)
        && covers_opt(dc_line_resistance_threshold,
                      other.dc_line_resistance_threshold)
        && covers_opt(loss_segments, other.loss_segments)
        && covers_opt(scale_objective, other.scale_objective)
        && covers_opt(scale_theta, other.scale_theta)
        && covers_opt(scale_loss_link, other.scale_loss_link)
        && covers_opt(loss_cost_eps, other.loss_cost_eps)
        && covers_opt(loss_flow_abs_cost, other.loss_flow_abs_cost)
        && covers_opt(loss_overflow_cost, other.loss_overflow_cost)
        && covers_opt(loss_secant_segments, other.loss_secant_segments)
        && covers_opt(loss_use_sos2, other.loss_use_sos2)
        && covers_opt(loss_segment_bound, other.loss_segment_bound)
        && covers_opt(theta_max, other.theta_max)
        && covers_opt(demand_fail_cost, other.demand_fail_cost)
        && covers_opt(demand_fail_segments, other.demand_fail_segments)
        && covers_opt(reserve_shortage_cost, other.reserve_shortage_cost)
        && covers_opt(hydro_spill_cost, other.hydro_spill_cost)
        && covers_opt(hydro_use_value, other.hydro_use_value)
        && covers_opt(state_violation_cost, other.state_violation_cost)
        && covers_opt(demand_fail_rhs_shift, other.demand_fail_rhs_shift)
        && covers_opt(continuous_phases, other.continuous_phases)
        && covers_opt(naming_dialect, other.naming_dialect)
        && covers_opt(objective_mode, other.objective_mode)
        && covers_opt(strict_storage_emin, other.strict_storage_emin)
        && covers_opt(lp_reduction, other.lp_reduction)
        && covers_opt(allow_oversupply, other.allow_oversupply)
        && covers_opt(battery_annual_standing_loss,
                      other.battery_annual_standing_loss)
        && covers_opt(battery_power_envelope, other.battery_power_envelope)
        && covers_opt(reserve_coupling, other.reserve_coupling)
        && covers_opt(pricing_mode, other.pricing_mode)
        && covers_opt(fast_start_max_hours, other.fast_start_max_hours)
        && covers_opt(transition_integrality, other.transition_integrality)
        && covers_opt(transition_status_rows, other.transition_status_rows)
        && covers_opt(startup_tier_integrality, other.startup_tier_integrality)
        && covers_opt(line_thermal_limit_mode, other.line_thermal_limit_mode)
        && covers_opt(line_direction_mode, other.line_direction_mode)
        && covers_opt(prev_stage_link, other.prev_stage_link)
        && covers_opt(use_line_dlr, other.use_line_dlr)
        && covers_opt(dlr_limit_mode, other.dlr_limit_mode)
        && covers_opt(overload_slack_bound, other.overload_slack_bound)
        && covers_opt(max_overload_ratio, other.max_overload_ratio)
        && covers_opt(ramp_formula, other.ramp_formula)
        && covers_opt(ramp_wrap, other.ramp_wrap)
        && covers_opt(uc_formulation, other.uc_formulation)
        && covers_opt(storage_integration, other.storage_integration)
        && covers_opt(reservoir_storage_integration,
                      other.reservoir_storage_integration)
        && covers_opt(battery_storage_integration,
                      other.battery_storage_integration)
        && covers_opt(long_transition, other.long_transition);
  }
};

}  // namespace gtopt
