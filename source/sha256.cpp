/**
 * @file      sha256.cpp
 * @brief     FIPS 180-4 SHA-256 (self-contained, no OpenSSL dependency)
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <format>
#include <iterator>
#include <span>
#include <string>
#include <string_view>

#include <gtopt/sha256.hpp>

namespace gtopt
{

std::string sha256_hex(std::span<const std::byte> data)
{
  static constexpr std::array<std::uint32_t, 64> k = {
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
      0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
      0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
      0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
      0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
      0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
      0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  };
  std::array<std::uint32_t, 8> h = {
      0x6a09e667,
      0xbb67ae85,
      0x3c6ef372,
      0xa54ff53a,
      0x510e527f,
      0x9b05688c,
      0x1f83d9ab,
      0x5be0cd19,
  };
  const auto process = [&h](std::span<const std::byte, 64> block)
  {
    const auto u = [&block](std::size_t i)
    { return std::to_integer<std::uint32_t>(block[i]); };
    std::array<std::uint32_t, 64> w {};
    for (std::size_t i = 0; i < 16; ++i) {
      w[i] = (u(i * 4) << 24U) | (u((i * 4) + 1) << 16U)
          | (u((i * 4) + 2) << 8U) | u((i * 4) + 3);
    }
    for (std::size_t i = 16; i < 64; ++i) {
      const auto s0 = std::rotr(w[i - 15], 7) ^ std::rotr(w[i - 15], 18)
          ^ (w[i - 15] >> 3U);
      const auto s1 =
          std::rotr(w[i - 2], 17) ^ std::rotr(w[i - 2], 19) ^ (w[i - 2] >> 10U);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    auto [a, b, c, d, e, f, g, hh] = h;
    for (std::size_t i = 0; i < 64; ++i) {
      const auto s1 = std::rotr(e, 6) ^ std::rotr(e, 11) ^ std::rotr(e, 25);
      const auto ch = (e & f) ^ (~e & g);
      const auto t1 = hh + s1 + ch + k[i] + w[i];
      const auto s0 = std::rotr(a, 2) ^ std::rotr(a, 13) ^ std::rotr(a, 22);
      const auto maj = (a & b) ^ (a & c) ^ (b & c);
      const auto t2 = s0 + maj;
      hh = g;
      g = f;
      f = e;
      e = d + t1;
      d = c;
      c = b;
      b = a;
      a = t1 + t2;
    }
    for (std::size_t i = 0; const auto v : {a, b, c, d, e, f, g, hh}) {
      h[i++] += v;
    }
  };

  const std::size_t full = data.size() / 64 * 64;
  for (std::size_t off = 0; off < full; off += 64) {
    process(data.subspan(off).first<64>());
  }
  // Tail: the remaining bytes, 0x80, zeros, then the 64-bit bit length.
  std::array<std::byte, 128> tail {};
  const auto rest = data.subspan(full);
  std::ranges::copy(rest, tail.begin());
  tail[rest.size()] = std::byte {0x80};
  const std::size_t tail_len = rest.size() + 9 <= 64 ? 64 : 128;
  const std::uint64_t bits = std::uint64_t {data.size()} * 8U;
  for (std::size_t i = 0; i < 8; ++i) {
    tail[tail_len - 1 - i] = static_cast<std::byte>(bits >> (i * 8U));
  }
  for (std::size_t off = 0; off < tail_len; off += 64) {
    process(std::span<const std::byte, 64>(tail.data() + off, 64));
  }

  std::string out;
  out.reserve(64);
  for (const auto v : h) {
    std::format_to(std::back_inserter(out), "{:08x}", v);
  }
  return out;
}

std::string sha256_hex(std::string_view data)
{
  return sha256_hex(std::as_bytes(std::span(data)));
}

}  // namespace gtopt
