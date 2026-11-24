/**
 * @file      gas_enums.hpp
 * @brief     Enumerations for the gas-network element configuration
 * @date      Mon Nov 23 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Named enums (``enum_from_name`` / ``enum_name``) for the string
 * selectors of ``GasPipe`` and ``GasCompressor``.  The JSON fields stay
 * ``OptName``; the data structs expose ``*_enum()`` accessors (same
 * pattern as ``Line::line_losses_mode_enum``) that return the default
 * when the field is unset and ``std::nullopt`` for an unknown name.
 */

#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <span>

#include <gtopt/basic_types.hpp>
#include <gtopt/enum_option.hpp>

namespace gtopt
{

// ─── GasWeymouthMode ────────────────────────────────────────────────

/**
 * @brief Pressure-flow model of a ``GasPipe`` (``weymouth_mode``).
 *
 * - `radial` (default): fixed direction a → b, Weymouth tangent outer
 *   approximation (exact on radial networks).
 * - `linearized`: signed flow, one equality per block linearised at a
 *   reference flow f₀ (meshed networks; refined by the gas SLP).
 * - `none`: capacity-only arc a → b, no pressure coupling.
 */
enum class GasWeymouthMode : uint8_t
{
  radial = 0,
  linearized = 1,
  none = 2,
};

inline constexpr auto gas_weymouth_mode_entries =
    std::to_array<EnumEntry<GasWeymouthMode>>({
        {.name = "radial", .value = GasWeymouthMode::radial},
        {.name = "linearized", .value = GasWeymouthMode::linearized},
        {.name = "none", .value = GasWeymouthMode::none},
    });

[[nodiscard]] constexpr auto enum_entries(GasWeymouthMode /*tag*/) noexcept
{
  return std::span {gas_weymouth_mode_entries};
}

// ─── GasLinearization ───────────────────────────────────────────────

/**
 * @brief Linearisation of a ``weymouth_mode: linearized`` pipe
 *        (``linearization``).
 *
 * - `secant` (default): π_a − e^s π_b = K_e |f₀| f.
 * - `tangent`: π_a − e^s π_b = 2 K_e |f₀| f − K_e f₀ |f₀|.
 */
enum class GasLinearization : uint8_t
{
  secant = 0,
  tangent = 1,
};

inline constexpr auto gas_linearization_entries =
    std::to_array<EnumEntry<GasLinearization>>({
        {.name = "secant", .value = GasLinearization::secant},
        {.name = "tangent", .value = GasLinearization::tangent},
    });

[[nodiscard]] constexpr auto enum_entries(GasLinearization /*tag*/) noexcept
{
  return std::span {gas_linearization_entries};
}

// ─── GasCompressorFuelModel ─────────────────────────────────────────

/**
 * @brief Fuel model of a ``GasCompressor`` (``fuel_model``).
 *
 * - `fixed_fraction` (default): LC = γ_ff Ψ.
 * - `saldarriaga`: Taylor planes of LC = γ (β^α − 1) Ψ.
 * - `none`: no fuel.
 */
enum class GasCompressorFuelModel : uint8_t
{
  none = 0,
  fixed_fraction = 1,
  saldarriaga = 2,
};

inline constexpr auto gas_compressor_fuel_model_entries =
    std::to_array<EnumEntry<GasCompressorFuelModel>>({
        {.name = "none", .value = GasCompressorFuelModel::none},
        {.name = "fixed_fraction",
         .value = GasCompressorFuelModel::fixed_fraction},
        {.name = "saldarriaga", .value = GasCompressorFuelModel::saldarriaga},
    });

[[nodiscard]] constexpr auto enum_entries(
    GasCompressorFuelModel /*tag*/) noexcept
{
  return std::span {gas_compressor_fuel_model_entries};
}

// ─── GasCompressorDrive ─────────────────────────────────────────────

/**
 * @brief Driver of a ``GasCompressor`` (``drive``).
 *
 * - `gas` (default): the fuel is drawn from the suction node.
 * - `electric`: the fuel equivalent is drawn as power from ``bus``.
 */
enum class GasCompressorDrive : uint8_t
{
  gas = 0,
  electric = 1,
};

inline constexpr auto gas_compressor_drive_entries =
    std::to_array<EnumEntry<GasCompressorDrive>>({
        {.name = "gas", .value = GasCompressorDrive::gas},
        {.name = "electric", .value = GasCompressorDrive::electric},
    });

[[nodiscard]] constexpr auto enum_entries(GasCompressorDrive /*tag*/) noexcept
{
  return std::span {gas_compressor_drive_entries};
}

}  // namespace gtopt

namespace gtopt::gas
{

// ─── TangentLayout ──────────────────────────────────────────────────

/// Tangent layouts of the Weymouth outer approximation
/// (``GasPipe.tangent_layout``).
enum class TangentLayout : uint8_t
{
  /// ``t_k = (2k − 1) F / (2N)`` — uniform squared-pressure error.
  uniform = 0,
  /// Breakpoints uniform in ``θ = asin(√K f / P)`` — uniform relative
  /// outlet-pressure error (Saldarriaga's pipe model 3 adapted to π).
  relative_pressure = 1,
};

inline constexpr auto tangent_layout_entries =
    std::to_array<EnumEntry<TangentLayout>>({
        {.name = "uniform", .value = TangentLayout::uniform},
        {.name = "relative_pressure",
         .value = TangentLayout::relative_pressure},
    });

[[nodiscard]] constexpr auto enum_entries(TangentLayout /*tag*/) noexcept
{
  return std::span {tangent_layout_entries};
}

}  // namespace gtopt::gas

namespace gtopt
{

/// Parses an optional selector: ``fallback`` when unset, the enumerator
/// when the name is known, ``std::nullopt`` for an unknown name.
template<NamedEnum E>
[[nodiscard]] constexpr auto gas_enum_or(const OptName& name,
                                         E fallback) noexcept
    -> std::optional<E>
{
  if (!name) {
    return fallback;
  }
  return enum_from_name<E>(*name);
}

}  // namespace gtopt
