/**
 * @file      emission_lp.cpp
 * @brief     Implementation of EmissionLP (passive pollutant tag)
 * @date      2026-05-18
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <gtopt/emission_lp.hpp>
#include <gtopt/system_context.hpp>

namespace gtopt
{

EmissionLP::EmissionLP(const Emission& emission, const InputContext& ic)
    : Base(emission, ic, Element::class_name)
{
}

}  // namespace gtopt
