// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_coverage_gaps_memory_compress.cpp
 * @brief     In-memory codec failure paths used by low_memory restore
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Coverage audit 2026-11-22: every decompress failure branch in
 * memory_compress.cpp (zstd 150-154, gzip 194-197, lz4 228-231, snappy
 * 254-257) and the file-I/O-only codec rejection had zero hits.  These
 * are the only guards between a corrupted `low_memory = compress` LP
 * snapshot and a silently garbage LP after `load_flat`.
 */

#include <algorithm>
#include <cstddef>
#include <numeric>
#include <stdexcept>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/linear_problem.hpp>
#include <gtopt/memory_compress.hpp>
#include <gtopt/planning_enums.hpp>

using namespace gtopt;

namespace test_coverage_gaps_memory_compress_ns
{
namespace
{

[[nodiscard]] std::vector<char> sample_payload(std::size_t n)
{
  std::vector<char> v(n);
  for (std::size_t i = 0; i < n; ++i) {
    v[i] = static_cast<char>((i * 31U) % 7U);  // compressible pattern
  }
  return v;
}

}  // namespace
}  // namespace test_coverage_gaps_memory_compress_ns

using test_coverage_gaps_memory_compress_ns::sample_payload;

TEST_CASE("memory_compress decompress rejects corrupted input")
{
  const auto payload = sample_payload(4096);

  for (const auto codec :
       {
           CompressionCodec::zstd,
           CompressionCodec::gzip,
           CompressionCodec::lz4,
           CompressionCodec::snappy,
       })
  {
    CAPTURE(codec_name(codec));
    if (!is_codec_available(codec)) {
      continue;
    }
    auto packed = compress(payload, codec);
    REQUIRE_FALSE(packed.empty());

    // round trip is exact
    CHECK(decompress(packed, payload.size(), codec) == payload);

    // garbage bytes throw instead of returning data.  Keep the first two
    // bytes (zlib header / snappy length varint) so the codec gets past
    // its header and the declared length still fits the output buffer —
    // snappy trusts that varint (see the report: decompress_snappy does
    // not validate it against original_size).
    std::vector<char> garbage(packed.size(), '\x7f');
    garbage[0] = packed[0];
    garbage[1] = packed[1];
    CHECK_THROWS_AS(
        static_cast<void>(decompress(garbage, payload.size(), codec)),
        std::runtime_error);

    // truncated stream throws
    packed.resize(packed.size() / 2);
    CHECK_THROWS_AS(
        static_cast<void>(decompress(packed, payload.size(), codec)),
        std::runtime_error);
  }
}

TEST_CASE("memory_compress rejects file-I/O-only codecs for in-memory use")
{
  const auto payload = sample_payload(64);
  for (const auto codec :
       {
           CompressionCodec::bzip2,
           CompressionCodec::xz,
           CompressionCodec::brotli,
           CompressionCodec::lzo,
       })
  {
    CAPTURE(codec_name(codec));
    CHECK_THROWS_AS(static_cast<void>(compress(payload, codec)),
                    std::runtime_error);
    CHECK_THROWS_AS(
        static_cast<void>(decompress(payload, payload.size(), codec)),
        std::runtime_error);
  }
}

TEST_CASE("memory_compress select_codec resolves auto_select")
{
  const auto picked = select_codec(CompressionCodec::auto_select);
  CHECK(picked != CompressionCodec::auto_select);
  CHECK(is_codec_available(picked));
  // A concrete codec passes through unchanged.
  CHECK(select_codec(CompressionCodec::zstd) == CompressionCodec::zstd);

  // auto_select round-trips through compress/decompress.
  const auto payload = sample_payload(1024);
  const auto packed = compress(payload, CompressionCodec::auto_select);
  CHECK(decompress(packed, payload.size(), CompressionCodec::auto_select)
        == payload);
}

// Regression for a gap found by the 2026-11-22 coverage audit: decompress()
// used to size the output to `original_size` and never check how many
// bytes the codec actually produced, so a mismatch was silently
// zero-padded (or, for snappy, a larger embedded length overran the
// buffer).  Every in-memory codec must now reject both directions.
TEST_CASE("memory_compress decompress rejects an original_size mismatch")
{
  const auto payload = sample_payload(1000);
  for (const auto codec :
       {
           CompressionCodec::uncompressed,
           CompressionCodec::zstd,
           CompressionCodec::gzip,
           CompressionCodec::lz4,
           CompressionCodec::snappy,
       })
  {
    CAPTURE(codec_name(codec));
    if (codec != CompressionCodec::uncompressed && !is_codec_available(codec)) {
      continue;
    }
    const auto packed = codec == CompressionCodec::uncompressed
        ? payload
        : compress(payload, codec);

    CHECK(decompress(packed, payload.size(), codec) == payload);
    // Declared size too large: the stream ends early.
    CHECK_THROWS_AS(
        static_cast<void>(decompress(packed, payload.size() * 2, codec)),
        std::runtime_error);
    // Declared size too small: the stream does not fit.
    CHECK_THROWS_AS(
        static_cast<void>(decompress(packed, payload.size() / 2, codec)),
        std::runtime_error);
  }
}

TEST_CASE("decompress_flat_lp rejects a buffer that disagrees with metadata")
{
  FlatLinearProblem flp;
  flp.ncols = 2;
  flp.nrows = 1;
  flp.matbeg = {0, 1, 2};
  flp.matind = {0, 0};
  flp.matval = {1.0, 2.0};
  flp.collb = {0.0, 0.0};
  flp.colub = {10.0, 20.0};
  flp.objval = {1.0, 1.0};
  flp.rowlb = {0.0};
  flp.rowub = {5.0};

  auto buf = compress_flat_lp(flp, CompressionCodec::zstd);
  REQUIRE_FALSE(buf.empty());
  REQUIRE(flp.matbeg.empty());

  SUBCASE("round trip restores the vectors")
  {
    decompress_flat_lp(flp, buf);
    CHECK(flp.matbeg.size() == 3);
    CHECK(flp.colub.size() == 2);
    CHECK(flp.colub.back() == doctest::Approx(20.0));
  }

  SUBCASE("metadata mismatch throws and leaves the vectors empty")
  {
    buf.flp_nnz += 1;  // metadata now expects 12 more bytes
    CHECK_THROWS_AS(decompress_flat_lp(flp, buf), std::runtime_error);
    CHECK(flp.matbeg.empty());
    CHECK(flp.matval.empty());
  }

  SUBCASE("corrupt stream throws and leaves the vectors empty")
  {
    std::ranges::fill(buf.data, '\x7f');
    CHECK_THROWS_AS(decompress_flat_lp(flp, buf), std::runtime_error);
    CHECK(flp.matbeg.empty());
  }
}

TEST_CASE("xor-delta restore rejects a corrupt delta or a mismatched base")
{
  const auto codec = CompressionCodec::zstd;
  FlatLpDedupRegistry registry;
  const std::vector<char> x(4096, 'x');
  std::vector<char> y(4096, 'x');
  y[100] = 'y';

  const auto bx = registry.compress("p1", x, codec);
  const auto by = registry.compress("p1", y, codec);
  REQUIRE(by.xor_base != nullptr);
  REQUIRE_FALSE(by.data.empty());
  CHECK(by.decompress_data() == y);

  SUBCASE("corrupt delta stream throws")
  {
    auto bad = by;
    std::ranges::fill(bad.data, '\x7f');
    CHECK_THROWS_AS(static_cast<void>(bad.decompress_data()),
                    std::runtime_error);
  }

  SUBCASE("delta whose recorded size disagrees with the base throws")
  {
    auto bad = by;
    bad.original_size = y.size() / 2;
    CHECK_THROWS_AS(static_cast<void>(bad.decompress_data()),
                    std::runtime_error);

    // Identity delta (no data) over a base of another size.
    auto identity = bx;
    REQUIRE(identity.data.empty());
    identity.original_size = x.size() * 2;
    CHECK_THROWS_AS(static_cast<void>(identity.decompress_data()),
                    std::runtime_error);
  }
}
