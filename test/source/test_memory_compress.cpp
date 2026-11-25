// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_memory_compress.cpp
 * @brief     Unit tests for in-memory compress/decompress round-trips
 * @date      2026-04-23
 * @author    Marcelo Matus
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <cstring>
#include <numeric>
#include <random>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/memory_compress.hpp>

using namespace gtopt;

namespace  // NOLINT
{
// Build a deterministic byte payload of a given size.
auto make_payload(std::size_t n) -> std::vector<char>
{
  std::vector<char> buf(n);
  for (std::size_t i = 0; i < n; ++i) {
    buf[i] = static_cast<char>(i & 0xFF);
  }
  return buf;
}
}  // namespace

TEST_CASE("is_codec_available — uncompressed is always true")  // NOLINT
{
  CHECK(is_codec_available(CompressionCodec::uncompressed));
}

TEST_CASE("is_codec_available — every enum value classified")  // NOLINT
{
  // The dispatcher's switch covers every `CompressionCodec` value;
  // exhaustive enum coverage prevents a future addition from
  // silently falling through the default `return false`.
  // Always-available codecs:
  CHECK(is_codec_available(CompressionCodec::auto_select));
  CHECK(is_codec_available(CompressionCodec::uncompressed));
  CHECK(is_codec_available(CompressionCodec::zstd));
  CHECK(is_codec_available(CompressionCodec::gzip));
  // Optional codecs (compile-time gated): just assert the call is
  // total — true or false depending on `GTOPT_HAS_LZ4` /
  // `GTOPT_HAS_SNAPPY`, but the call must not throw or assert.
  (void)is_codec_available(CompressionCodec::lz4);
  (void)is_codec_available(CompressionCodec::snappy);
  // File-only codecs (not for in-memory use):
  CHECK_FALSE(is_codec_available(CompressionCodec::bzip2));
  CHECK_FALSE(is_codec_available(CompressionCodec::xz));
  CHECK_FALSE(is_codec_available(CompressionCodec::brotli));
  CHECK_FALSE(is_codec_available(CompressionCodec::lzo));
}

TEST_CASE("codec_name returns non-empty string for known codecs")  // NOLINT
{
  CHECK_FALSE(codec_name(CompressionCodec::uncompressed).empty());
  CHECK_FALSE(codec_name(CompressionCodec::zstd).empty());
  CHECK_FALSE(codec_name(CompressionCodec::lz4).empty());
}

TEST_CASE("codec_name — exact spellings for every enum value")  // NOLINT
{
  // Lock the canonical lower-case names.  Downstream tools (logs,
  // JSON output, the planning JSON parser's `memory_codec` /
  // `output_compression` field) consume these strings verbatim, so a
  // typo here would silently break configuration round-trip.
  CHECK(codec_name(CompressionCodec::auto_select) == "auto");
  CHECK(codec_name(CompressionCodec::uncompressed) == "none");
  CHECK(codec_name(CompressionCodec::lz4) == "lz4");
  CHECK(codec_name(CompressionCodec::snappy) == "snappy");
  CHECK(codec_name(CompressionCodec::zstd) == "zstd");
  CHECK(codec_name(CompressionCodec::gzip) == "gzip");
  CHECK(codec_name(CompressionCodec::bzip2) == "bzip2");
  CHECK(codec_name(CompressionCodec::xz) == "xz");
  CHECK(codec_name(CompressionCodec::brotli) == "brotli");
  CHECK(codec_name(CompressionCodec::lzo) == "lzo");
}

TEST_CASE("select_codec falls back to an available codec")  // NOLINT
{
  const auto c = select_codec(CompressionCodec::zstd);
  CHECK(is_codec_available(c));
}

TEST_CASE("select_codec — auto_select resolves to zstd")  // NOLINT
{
  // In-memory default: zstd level 1 (best ratio on real flat-LP
  // snapshots at negligible decompress cost).  lz4 stays selectable.
  CHECK(select_codec(CompressionCodec::auto_select) == CompressionCodec::zstd);
  if (is_codec_available(CompressionCodec::lz4)) {
    CHECK(select_codec(CompressionCodec::lz4) == CompressionCodec::lz4);
  }
}

TEST_CASE("select_codec — file-only codec falls back to zstd")  // NOLINT
{
  // Asking for a file-only codec (bzip2/xz/brotli/lzo) for in-memory
  // use must downgrade to the always-available zstd so the caller
  // never gets a non-functional codec.
  CHECK(select_codec(CompressionCodec::bzip2) == CompressionCodec::zstd);
  CHECK(select_codec(CompressionCodec::xz) == CompressionCodec::zstd);
  CHECK(select_codec(CompressionCodec::brotli) == CompressionCodec::zstd);
  CHECK(select_codec(CompressionCodec::lzo) == CompressionCodec::zstd);
}

TEST_CASE(
    "select_codec returns uncompressed when requested and available")  // NOLINT
{
  const auto c = select_codec(CompressionCodec::uncompressed);
  CHECK(c == CompressionCodec::uncompressed);
}

TEST_CASE("compress/decompress round-trip — uncompressed")  // NOLINT
{
  const auto payload = make_payload(256);
  const auto compressed = compress(payload, CompressionCodec::uncompressed);
  const auto restored =
      decompress(compressed, payload.size(), CompressionCodec::uncompressed);
  CHECK(restored == payload);
}

TEST_CASE("compress/decompress round-trip — zstd")  // NOLINT
{
  if (!is_codec_available(CompressionCodec::zstd)) {
    MESSAGE("zstd not available, skipping");
    return;
  }
  const auto payload = make_payload(4096);
  const auto compressed = compress(payload, CompressionCodec::zstd);
  const auto restored =
      decompress(compressed, payload.size(), CompressionCodec::zstd);
  CHECK(restored == payload);
  // zstd should actually compress a repetitive payload
  CHECK(compressed.size() < payload.size());
}

TEST_CASE("compress/decompress round-trip — lz4")  // NOLINT
{
  if (!is_codec_available(CompressionCodec::lz4)) {
    MESSAGE("lz4 not available, skipping");
    return;
  }
  const auto payload = make_payload(4096);
  const auto compressed = compress(payload, CompressionCodec::lz4);
  const auto restored =
      decompress(compressed, payload.size(), CompressionCodec::lz4);
  CHECK(restored == payload);
}

TEST_CASE("CompressedBuffer default is empty")  // NOLINT
{
  const CompressedBuffer buf {};
  CHECK(buf.empty());
  CHECK(buf.original_size == 0);
  CHECK(buf.codec == CompressionCodec::uncompressed);
}

TEST_CASE("compress_buffer / decompress_data round-trip")  // NOLINT
{
  const auto payload = make_payload(512);
  const auto codec = select_codec(CompressionCodec::zstd);
  const auto cbuf = compress_buffer(payload, codec);
  CHECK_FALSE(cbuf.empty());
  CHECK(cbuf.original_size == payload.size());
  CHECK(cbuf.codec == codec);

  const auto restored = cbuf.decompress_data();
  CHECK(restored == payload);
}

TEST_CASE("compress_buffer with small payload")  // NOLINT
{
  const std::vector<char> payload {'h', 'e', 'l', 'l', 'o'};
  const auto codec = select_codec(CompressionCodec::uncompressed);
  const auto cbuf = compress_buffer(payload, codec);
  CHECK(cbuf.original_size == 5);
  const auto restored = cbuf.decompress_data();
  CHECK(restored == payload);
}

// ── Compression-stats accumulators ──────────────────────────────────────────

TEST_CASE(  // NOLINT
    "compression stats — empty after reset, populated after one round-trip")
{
  reset_compression_stats();
  const auto fresh = get_compression_stats();
  CHECK(fresh.empty());

  // One zstd compress + decompress round-trip.
  const std::vector<char> payload(4096, 'a');
  const auto comp =
      compress({payload.data(), payload.size()}, CompressionCodec::zstd);
  const auto decomp = decompress(
      {comp.data(), comp.size()}, payload.size(), CompressionCodec::zstd);
  CHECK(decomp == payload);

  const auto stats = get_compression_stats();
  REQUIRE(stats.size() == 1);
  CHECK(stats[0].codec == CompressionCodec::zstd);
  CHECK(stats[0].n_compress == 1);
  CHECK(stats[0].n_decompress == 1);
  CHECK(stats[0].compress_in_bytes == payload.size());
  CHECK(stats[0].compress_out_bytes == comp.size());
  CHECK(stats[0].decompress_in_bytes == comp.size());
  CHECK(stats[0].decompress_out_bytes == payload.size());

  // Cleanup so subsequent test cases don't see leftover counters.
  reset_compression_stats();
}

TEST_CASE(  // NOLINT
    "compression stats — separate codecs accumulate independently")
{
  reset_compression_stats();
  const std::vector<char> payload(2048, 'x');

  // zstd round-trip
  const auto cz =
      compress({payload.data(), payload.size()}, CompressionCodec::zstd);
  std::ignore = decompress(
      {cz.data(), cz.size()}, payload.size(), CompressionCodec::zstd);

  // Uncompressed round-trip (always available)
  const auto cu = compress({payload.data(), payload.size()},
                           CompressionCodec::uncompressed);
  std::ignore = decompress(
      {cu.data(), cu.size()}, payload.size(), CompressionCodec::uncompressed);

  const auto stats = get_compression_stats();
  REQUIRE(stats.size() == 2);
  bool saw_zstd = false;
  bool saw_uncomp = false;
  for (const auto& s : stats) {
    if (s.codec == CompressionCodec::zstd) {
      saw_zstd = true;
      CHECK(s.n_compress == 1);
      CHECK(s.n_decompress == 1);
    } else if (s.codec == CompressionCodec::uncompressed) {
      saw_uncomp = true;
      CHECK(s.n_compress == 1);
      CHECK(s.n_decompress == 1);
    }
  }
  CHECK(saw_zstd);
  CHECK(saw_uncomp);

  reset_compression_stats();
}

TEST_CASE(  // NOLINT
    "compression stats — log_compression_stats is silent when nothing used")
{
  reset_compression_stats();
  // Must not throw and must not segfault on empty state.
  log_compression_stats();
  CHECK(get_compression_stats().empty());
}

TEST_CASE(  // NOLINT
    "compression stats — reset clears counters back to zero")
{
  reset_compression_stats();
  const std::vector<char> payload(1024, 'q');
  const auto c =
      compress({payload.data(), payload.size()}, CompressionCodec::zstd);
  std::ignore =
      decompress({c.data(), c.size()}, payload.size(), CompressionCodec::zstd);
  REQUIRE_FALSE(get_compression_stats().empty());

  reset_compression_stats();
  CHECK(get_compression_stats().empty());
}

TEST_CASE("CompressedBuffer — per-buffer codec tag drives restore")  // NOLINT
{
  // Buffers compressed with different codecs restore through the same
  // `decompress_data()` call: the codec is read from each buffer, not
  // from the current default.  A dedup delta may also use a codec
  // different from its base.
  std::vector<char> raw(8192);
  for (std::size_t i = 0; i < raw.size(); ++i) {
    raw[i] = static_cast<char>((i * 31U) % 97U);
  }
  const auto z = compress_buffer(raw, CompressionCodec::zstd);
  CHECK(z.codec == CompressionCodec::zstd);
  CHECK(z.decompress_data() == raw);

  if (is_codec_available(CompressionCodec::lz4)) {
    const auto l = compress_buffer(raw, CompressionCodec::lz4);
    CHECK(l.codec == CompressionCodec::lz4);
    CHECK(l.decompress_data() == raw);

    FlatLpDedupRegistry registry;
    auto other = raw;
    other[17] = 'q';
    const auto base = registry.compress("p", raw, CompressionCodec::lz4);
    const auto delta = registry.compress("p", other, CompressionCodec::zstd);
    REQUIRE(delta.xor_base != nullptr);
    CHECK(delta.xor_base->codec == CompressionCodec::lz4);
    CHECK(delta.codec == CompressionCodec::zstd);
    CHECK(base.decompress_data() == raw);
    CHECK(delta.decompress_data() == other);
  }
}

namespace  // NOLINT
{

constexpr std::array kInMemoryCodecs {
    CompressionCodec::zstd,
    CompressionCodec::gzip,
    CompressionCodec::lz4,
    CompressionCodec::snappy,
};

auto random_bytes(std::size_t n, unsigned seed) -> std::vector<char>
{
  std::mt19937 rng(seed);
  std::uniform_int_distribution<int> dist(0, 255);
  std::vector<char> v(n);
  for (auto& c : v) {
    c = static_cast<char>(dist(rng));
  }
  return v;
}

}  // namespace

TEST_CASE(  // NOLINT
    "compress — result capacity is the compressed size, not the bound")
{
  // A highly compressible 4 MiB payload: the worst-case bound is ~4 MiB,
  // the real output a few KiB.  The returned buffer must not keep the
  // bound as capacity (it did: vector(bound) + resize(rc)), otherwise
  // every "compressed" low-memory snapshot stays as large as the raw one.
  const std::vector<char> raw(std::size_t {4} << 20U, 'x');
  for (const auto codec : kInMemoryCodecs) {
    if (!is_codec_available(codec)) {
      continue;
    }
    CAPTURE(static_cast<int>(codec));
    const auto out = compress(raw, codec);
    REQUIRE_FALSE(out.empty());
    CHECK(out.size() < raw.size() / 16);
    CHECK(out.capacity() == out.size());
    CHECK(decompress(out, raw.size(), codec) == raw);
  }
}

TEST_CASE("compress — byte-exact round trip across input shapes")  // NOLINT
{
  const std::vector<std::pair<std::string, std::vector<char>>> inputs {
      {
          "empty",
          {},
      },
      {
          "one byte",
          {
              'z',
          },
      },
      {
          "tiny",
          make_payload(7),
      },
      {
          "incompressible random 64 KiB",
          random_bytes(std::size_t {64} << 10U, 7),
      },
      {
          "large patterned 8 MiB",
          make_payload(std::size_t {8} << 20U),
      },
      {
          "large random 2 MiB",
          random_bytes(std::size_t {2} << 20U, 11),
      },
  };
  for (const auto codec : kInMemoryCodecs) {
    if (!is_codec_available(codec)) {
      continue;
    }
    for (const auto& [name, raw] : inputs) {
      CAPTURE(static_cast<int>(codec));
      CAPTURE(name);
      const auto out = compress(raw, codec);
      // Exact-size result: never more capacity than bytes produced.
      CHECK(out.capacity() == out.size());
      const auto back = decompress(out, raw.size(), codec);
      CHECK(back.size() == raw.size());
      CHECK(back == raw);
    }
  }
}

TEST_CASE("compress_buffer — codec tag and original size preserved")  // NOLINT
{
  const auto raw = random_bytes(std::size_t {256} << 10U, 3);
  for (const auto codec : kInMemoryCodecs) {
    if (!is_codec_available(codec)) {
      continue;
    }
    CAPTURE(static_cast<int>(codec));
    const auto buf = compress_buffer(raw, codec);
    CHECK(buf.codec == codec);
    CHECK(buf.original_size == raw.size());
    CHECK(buf.xor_base == nullptr);
    CHECK(buf.data.capacity() == buf.data.size());
    CHECK(buf.decompress_data() == raw);
  }
}

TEST_CASE("FlatLpDedupRegistry — XOR delta round trips")  // NOLINT
{
  // Sibling cells: same length, a few bytes differ (the inflow bounds).
  const auto base = random_bytes(std::size_t {512} << 10U, 5);
  auto sibling = base;
  sibling[10] = static_cast<char>(sibling[10] ^ 0x5A);
  sibling[200000] = static_cast<char>(sibling[200000] ^ 0x01);
  const auto& identical = base;
  // Different length: must fall back to a plain buffer.
  auto longer = base;
  longer.push_back('q');
  const std::vector<char> shorter(base.begin(), base.end() - 100);

  for (const auto codec : kInMemoryCodecs) {
    if (!is_codec_available(codec)) {
      continue;
    }
    CAPTURE(static_cast<int>(codec));
    FlatLpDedupRegistry reg;
    const auto b0 = reg.compress("k", base, codec);
    const auto b1 = reg.compress("k", sibling, codec);
    const auto b2 = reg.compress("k", identical, codec);
    const auto b3 = reg.compress("k", longer, codec);
    const auto b4 = reg.compress("k", shorter, codec);

    CHECK(b0.decompress_data() == base);
    CHECK(b1.decompress_data() == sibling);
    CHECK(b2.decompress_data() == identical);
    CHECK(b3.decompress_data() == longer);
    CHECK(b4.decompress_data() == shorter);

    // The sibling is stored as a delta against the shared base and is
    // tiny; identical content stores no bytes at all.
    CHECK(b1.xor_base != nullptr);
    CHECK(b1.data.size() < base.size() / 16);
    CHECK(b1.data.capacity() == b1.data.size());
    CHECK(b2.xor_base != nullptr);
    CHECK(b2.data.empty());
    // Length mismatch → plain, self-contained buffer.
    CHECK(b3.xor_base == nullptr);
    CHECK(b4.xor_base == nullptr);
    CHECK(b3.codec == codec);
    CHECK(reg.live_bases() == 1);
  }
}

TEST_CASE("FlatLpDedupRegistry — bases are per key and freed")  // NOLINT
{
  const auto a = random_bytes(4096, 1);
  const auto b = random_bytes(4096, 2);
  FlatLpDedupRegistry reg;
  {
    const auto ba = reg.compress("p1", a, CompressionCodec::zstd);
    const auto bb = reg.compress("p2", b, CompressionCodec::zstd);
    CHECK(reg.live_bases() == 2);
    // Mutating a decompressed copy never leaks into a sibling.
    auto copy = ba.decompress_data();
    copy[0] = static_cast<char>(copy[0] ^ 0x7F);
    CHECK(ba.decompress_data() == a);
    CHECK(bb.decompress_data() == b);
  }
  // Weakly held: once every buffer referencing a base is gone, so is it.
  CHECK(reg.live_bases() == 0);
}
