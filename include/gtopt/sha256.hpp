/**
 * @file      sha256.hpp
 * @brief     FIPS 180-4 SHA-256 (self-contained, no OpenSSL dependency)
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * One implementation shared by the LP fingerprint (lp_fingerprint.cpp) and
 * the solver-library identity check (plugin_integrity.cpp).  The result
 * equals `sha256sum`, Python's hashlib.sha256 and CMake's file(SHA256).
 */

#pragma once

#include <cstddef>
#include <span>
#include <string>
#include <string_view>

namespace gtopt
{

/// SHA-256 of @p data as 64 lowercase hex characters.
[[nodiscard]] std::string sha256_hex(std::span<const std::byte> data);

/// SHA-256 of the bytes of @p data as 64 lowercase hex characters.
[[nodiscard]] std::string sha256_hex(std::string_view data);

}  // namespace gtopt
