/**
 * @file      line_direction.hpp
 * @brief     Radial-line detection and structural flow-direction certificate
 * @date      2026-11-26
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Pure-graph utilities (no LP / SystemContext coupling) behind
 * `model_options.line_direction_mode` and `Line.flow_direction = "auto"`.
 *
 * ### Definitions
 *
 * The **transport graph** is the multigraph over buses whose edges are the
 * lines active in ANY stage of the LP being built (AC, DC links, ties and
 * expansion candidates alike; self-loops dropped).  A **radial corridor**
 * is a bus pair whose parallel circuits together form a cut: a bridge of
 * the simple graph obtained by collapsing parallel circuits.  Because a
 * bridge of this union graph stays a bridge in every per-stage / per-block
 * sub-graph (and its cut-off side only loses elements there), the
 * certificate below is valid for every stage, block and scenario of the
 * LP — line outages, commitment and stage activity can only shrink it.
 *
 * Every bus carries the injection-sign classes of the elements attached
 * to it (`BusInjection`): `withdraw` (demand: served load ≥ 0, gas
 * compressor drive, …), `inject` (generator with `pmin ≥ 0`) or `both`
 * (anything that can do either — a generator with `pmin < 0`, a
 * carrier converter on the bus, or an element the caller cannot
 * classify).  A side of a radial corridor is
 *
 *   - `empty`  — no element at all;
 *   - `load`   — only `withdraw` elements;
 *   - `gen`    — only `inject` elements;
 *   - `mixed`  — anything else.
 *
 * Verdict per circuit of a radial corridor:
 *
 *   - **dead end** when either side is `empty`, or both sides are `load`
 *     (or both `gen`): the only non-circulating feasible flow is 0, so
 *     the line can be dropped;
 *   - **one-way toward** a `load` side, or **away from** a `gen` side;
 *   - **both** otherwise (a `mixed` side can reverse the flow).
 *
 * The subtree balance `Σ inj(S) + inflow(S) − losses(S) = 0` fixes the
 * sign of the NET flow of every non-circulating feasible point; the
 * one-way restriction removes only simultaneous two-way flow
 * (`fp > 0 ∧ fn > 0`), which with non-negative losses and transmission
 * cost is never profitable unless the LP values disposing of energy
 * (negative prices / must-run surplus without spill) — the fictitious
 * loss-arbitrage sink.
 *
 * Multi-circuit corridors are certified only when the caller marks every
 * circuit `kvl_coupled` (AC with positive reactance under an active
 * Kirchhoff formulation): the 2-cycle KVL rows then force the same sign
 * on all circuits.  Otherwise a circulation between the circuits would
 * be feasible, so the corridor keeps `both` (it is still reported as
 * radial).
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace gtopt::line_direction
{

/// Injection-sign class flags of one bus (OR of its elements).
struct BusInjection
{
  bool withdraw {false};  ///< some element can only withdraw (load)
  bool inject {false};  ///< some element can only inject (generation)
  bool both {false};  ///< some element can do either / is unknown
};

/// One circuit of the transport graph.
struct Edge
{
  std::size_t bus_a {};  ///< 0-based bus index of the `bus_a` end
  std::size_t bus_b {};  ///< 0-based bus index of the `bus_b` end
  /// Parallel circuits share one sign under KVL (AC, positive reactance,
  /// Kirchhoff active).  Required on every circuit of a multi-circuit
  /// corridor for that corridor to be certified.
  bool kvl_coupled {false};
};

/// Class of one side of a radial corridor.
enum class SideClass : std::uint8_t
{
  empty = 0,
  load = 1,
  gen = 2,
  mixed = 3,
};

/// Structural verdict of one circuit.
enum class Verdict : std::uint8_t
{
  both = 0,  ///< not certified (meshed, or a mixed radial side)
  ab = 1,  ///< certified one-way bus_a → bus_b
  ba = 2,  ///< certified one-way bus_b → bus_a
  dead_end = 3,  ///< certified zero flow (removable)
};

/// Per-circuit classification.
struct LineClass
{
  bool radial {false};  ///< circuit of a radial (bridge) corridor
  Verdict verdict {Verdict::both};
  SideClass side_a {SideClass::mixed};  ///< class of the bus_a side
  SideClass side_b {SideClass::mixed};  ///< class of the bus_b side
};

/// Decision applied to one line in the LP (stored in `SystemContext`).
struct LineDecision
{
  Verdict verdict {Verdict::both};  ///< flow direction applied
};

/// Aggregate counts for logging / tests.
struct Summary
{
  std::size_t lines {0};
  std::size_t radial {0};
  std::size_t one_way {0};
  std::size_t dead_end {0};
  std::size_t mixed {0};  ///< radial but not certified
};

[[nodiscard]] constexpr std::string_view verdict_name(Verdict v) noexcept
{
  switch (v) {
    case Verdict::ab:
      return "ab";
    case Verdict::ba:
      return "ba";
    case Verdict::dead_end:
      return "dead_end";
    case Verdict::both:
      break;
  }
  return "both";
}

/// Class of a side from its aggregated injection flags.
[[nodiscard]] constexpr SideClass side_class(const BusInjection& f) noexcept
{
  if (f.both || (f.withdraw && f.inject)) {
    return SideClass::mixed;
  }
  if (f.withdraw) {
    return SideClass::load;
  }
  if (f.inject) {
    return SideClass::gen;
  }
  return SideClass::empty;
}

/// Verdict of a radial circuit from the classes of its two sides.
[[nodiscard]] constexpr Verdict side_verdict(SideClass a, SideClass b) noexcept
{
  if (a == SideClass::empty || b == SideClass::empty) {
    return Verdict::dead_end;
  }
  if ((a == SideClass::load && b == SideClass::load)
      || (a == SideClass::gen && b == SideClass::gen))
  {
    return Verdict::dead_end;
  }
  if (b == SideClass::load || a == SideClass::gen) {
    return Verdict::ab;
  }
  if (a == SideClass::load || b == SideClass::gen) {
    return Verdict::ba;
  }
  return Verdict::both;
}

/// Mark each circuit that belongs to a radial (bridge) corridor.
/// Parallel circuits between the same bus pair are collapsed first, so
/// a 2-circuit corridor that is a cut is radial (both circuits marked).
/// Self-loops are never radial.  O(V + E), iterative (no recursion).
[[nodiscard]] std::vector<bool> find_radial(std::size_t n_buses,
                                            std::span<const Edge> edges);

/// Full classification of every circuit (see the file comment).
/// `buses.size()` is the bus count; edges reference indices into it.
[[nodiscard]] std::vector<LineClass> classify(
    std::span<const BusInjection> buses, std::span<const Edge> edges);

/// Count radial / one-way / dead-end / mixed circuits.
[[nodiscard]] Summary summarize(std::span<const LineClass> classes) noexcept;

/// Element ids referenced by `<element_type>(...)` refs in a
/// user-constraint expression (e.g. `line("L1").flow`,
/// `sum(line("L1", "uid:7").flow)`).  Sets `all` when a reference is not
/// a plain quoted id list (`line(all)`, a filter, a set name) — the
/// caller must then treat every element of the type as referenced.
/// Case-insensitive on the element type; `gas_line(` / `pipeline(` do
/// not match `line(`.
struct ElementRefs
{
  std::vector<std::string> ids {};
  bool all {false};
};
void collect_element_refs(std::string_view expression,
                          std::string_view element_type,
                          ElementRefs& refs);

}  // namespace gtopt::line_direction
