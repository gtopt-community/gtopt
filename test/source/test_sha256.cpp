// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_sha256.cpp
 * @brief     sha256_hex against the FIPS 180-4 / NIST test vectors
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstddef>
#include <span>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/sha256.hpp>

using namespace gtopt;

TEST_CASE("sha256_hex - standard test vectors")  // NOLINT
{
  SUBCASE("empty")
  {
    CHECK(
        sha256_hex("")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  }
  SUBCASE("abc")
  {
    CHECK(
        sha256_hex("abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  }
  SUBCASE("56 bytes: the padding needs a second block")
  {
    CHECK(
        sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")
        == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  }
  SUBCASE("55 and 64 bytes")
  {
    CHECK(
        sha256_hex(std::string(55, 'a'))
        == "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318");
    CHECK(
        sha256_hex(std::string(64, 'a'))
        == "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb");
  }
  SUBCASE("896-bit message (two blocks)")
  {
    CHECK(
        sha256_hex("abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmn"
                   "hijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu")
        == "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1");
  }
  SUBCASE("one million 'a'")
  {
    CHECK(
        sha256_hex(std::string(1'000'000, 'a'))
        == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
  }
  SUBCASE("byte-span and string overloads agree")
  {
    const std::string s = "hello world";
    CHECK(sha256_hex(std::as_bytes(std::span(s))) == sha256_hex(s));
  }
}
