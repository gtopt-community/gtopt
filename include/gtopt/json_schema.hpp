/**
 * @file      json_schema.hpp
 * @brief     JSON Schema dump for the gtopt input data model
 * @date      2026-06-08
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Provides a single diagnostic entry point that renders a JSON Schema
 * (draft 2020-12) for the gtopt input data model directly from the
 * daw_json_link ``json_data_contract`` mappings.  No input file is
 * needed — the schema is generated purely from the compile-time type
 * descriptions, mirroring the ``--list-dialects`` "dump-and-exit"
 * diagnostic.
 *
 * CLI integration
 * ---------------
 * Invoked by the ``--json-schema`` flag of the gtopt binary:
 *   - ``gtopt --json-schema``          dumps the full ``Planning`` input
 *     scheme (options, simulation, system and every element type).
 *   - ``gtopt --json-schema options``  only ``PlanningOptions``.
 *   - ``gtopt --json-schema gas_pipe`` any contract type by its
 *     snake_case name (or its C++ name, case-insensitive).
 *
 * The output holds one ``$defs`` entry per contract type reachable from
 * the selected root (properties in contract order, ``required`` = the
 * non-nullable members, ``additionalProperties: false``) and ``$ref``
 * links between them; field schedules render as ``oneOf`` of a scalar,
 * nested arrays and a file-name string.  ``scripts/gtopt_shared``'s
 * contract snapshot and the igtopt template generator consume it.
 */

#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace gtopt
{

/**
 * @brief Render the JSON Schema of a known top-level gtopt input type.
 *
 * @param type_name  Selects the root type.  An empty name yields the full
 *                   ``Planning`` scheme; ``"options"`` selects
 *                   ``PlanningOptions``; any other contract type is
 *                   selected by snake_case or C++ name
 *                   (case-insensitive), e.g. ``"gas_pipe"``.
 * @return The JSON Schema as a string.  For an UNKNOWN @p type_name, an
 *         error listing the known names is printed to ``stderr`` and an
 *         empty string is returned (mirrors the ``--list-dialects``
 *         unknown-filter behaviour).
 */
[[nodiscard]] std::string dump_json_schema(std::string_view type_name = {});

/**
 * @brief The selector names accepted by ``dump_json_schema``:
 *        ``planning`` (the default) and ``options`` first, then every
 *        contract type in snake_case, sorted.
 *        Exposed for diagnostics and tests.
 */
[[nodiscard]] const std::vector<std::string_view>& known_schema_names();

}  // namespace gtopt
