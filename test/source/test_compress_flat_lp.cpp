// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_compress_flat_lp.cpp
 * @brief     Unit tests for compress_flat_lp / decompress_flat_lp round-trips
 * @date      2026-05-12
 *
 * Tests:
 *   1. Round-trip: compress → decompress → original data matched (zstd)
 *   2. Round-trip: lz4 codec (if available)
 *   3. Round-trip: uncompressed codec
 *   4. Round-trip: gzip codec
 *   5. Empty LP (zero cols, zero rows) → round-trip
 *   6. Trivial LP (1 col, 1 row) → round-trip
 *   7. LP with scaled columns → scale vectors preserved
 *   8. clear_flat_lp_vectors removes numeric vectors
 *   9. compress with none codec → empty buffer
 */

#include <algorithm>
#include <cstring>
#include <limits>
#include <memory>
#include <numeric>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/linear_problem.hpp>
#include <gtopt/memory_compress.hpp>
#include <gtopt/planning_enums.hpp>

using namespace gtopt;
// NOLINTBEGIN(bugprone-misplaced-widening-cast,
// modernize-use-ranges,readability-math-missing-parentheses)

namespace
{

/// Build a synthetic FlatLinearProblem for compression testing.
/// Creates a small LP: 2 cols, 2 rows, with a few non-zeros.
[[nodiscard]] auto make_tiny_flp() -> FlatLinearProblem
{
  //   min  x0 + 2*x1
  //   s.t. x0 >= 0         (r0)
  //        x1 >= 0         (r1)
  FlatLinearProblem flp {};
  flp.ncols = 2;
  flp.nrows = 2;
  flp.name = "tiny_test";

  // Column-major sparse: matbeg[i] = start of column i in matind/matval
  // col 0: 1 non-zero at row 0, coeff 1.0
  // col 1: 1 non-zero at row 1, coeff 1.0
  flp.matbeg = {0, 1, 2};
  flp.matind = {0, 1};
  flp.matval = {1.0, 1.0};

  // Bounds
  flp.collb = {0.0, 0.0};
  flp.colub = {1e30, 1e30};

  // Objective
  flp.objval = {1.0, 2.0};

  // Row bounds: >= 0, i.e. rowlb=0, rowub=inf
  flp.rowlb = {0.0, 0.0};
  flp.rowub = {1e30, 1e30};

  // No integer variables
  flp.colint = {};

  // Scales
  flp.col_scales = {1.0, 1.0};
  flp.row_scales = {1.0, 1.0};

  flp.stats_nnz = 2;

  return flp;
}

/// Build a larger synthetic FlatLinearProblem with realistic data.
[[nodiscard]] auto make_realistic_flp() -> FlatLinearProblem
{
  constexpr int32_t ncols = 50;
  constexpr int32_t nrows = 30;

  FlatLinearProblem flp {};
  flp.ncols = ncols;
  flp.nrows = nrows;
  flp.name = "realistic_test";

  // Each column has ~3 non-zeros on average
  flp.matbeg.reserve(static_cast<size_t>(ncols + 1));
  flp.matind.reserve(static_cast<size_t>(ncols * 3));
  flp.matval.reserve(static_cast<size_t>(ncols * 3));

  int32_t nnz = 0;
  flp.matbeg.push_back(0);
  for (int32_t col = 0; col < ncols; ++col) {
    for (int32_t k = 0; k < 3; ++k) {
      const int32_t row = (col * 7 + k * 13) % nrows;
      const double val = static_cast<double>((col + 1) * (k + 1)) * 0.1;
      flp.matind.push_back(row);
      flp.matval.push_back(val);
      ++nnz;
    }
    flp.matbeg.push_back(nnz);
  }

  flp.collb.resize(static_cast<size_t>(ncols), 0.0);
  flp.colub.resize(static_cast<size_t>(ncols), 1e30);
  flp.objval.resize(static_cast<size_t>(ncols));
  std::iota(flp.objval.begin(), flp.objval.end(), 1.0);

  flp.rowlb.resize(static_cast<size_t>(nrows), 0.0);
  flp.rowub.resize(static_cast<size_t>(nrows), 1e30);

  flp.colint = {};
  flp.col_scales.resize(static_cast<size_t>(ncols), 1.0);
  flp.row_scales.resize(static_cast<size_t>(nrows), 1.0);

  flp.stats_nnz = static_cast<size_t>(nnz);

  return flp;
}

/// Verify two FlatLinearProblem objects have identical numeric content.
void verify_flp_equal(const FlatLinearProblem& a, const FlatLinearProblem& b)
{
  REQUIRE(a.ncols == b.ncols);
  REQUIRE(a.nrows == b.nrows);
  REQUIRE(a.matbeg.size() == b.matbeg.size());
  REQUIRE(a.matind.size() == b.matind.size());
  REQUIRE(a.matval.size() == b.matval.size());

  for (size_t i = 0; i < a.matbeg.size(); ++i) {
    CHECK(a.matbeg[i] == b.matbeg[i]);
  }
  for (size_t i = 0; i < a.matind.size(); ++i) {
    CHECK(a.matind[i] == b.matind[i]);
  }
  for (size_t i = 0; i < a.matval.size(); ++i) {
    CHECK(a.matval[i] == doctest::Approx(b.matval[i]));
  }

  REQUIRE(a.collb.size() == b.collb.size());
  REQUIRE(a.colub.size() == b.colub.size());
  REQUIRE(a.objval.size() == b.objval.size());
  REQUIRE(a.rowlb.size() == b.rowlb.size());
  REQUIRE(a.rowub.size() == b.rowub.size());

  for (size_t i = 0; i < a.collb.size(); ++i) {
    CHECK(a.collb[i] == doctest::Approx(b.collb[i]));
  }
  for (size_t i = 0; i < a.colub.size(); ++i) {
    CHECK(a.colub[i] == doctest::Approx(b.colub[i]));
  }
  for (size_t i = 0; i < a.objval.size(); ++i) {
    CHECK(a.objval[i] == doctest::Approx(b.objval[i]));
  }
  for (size_t i = 0; i < a.rowlb.size(); ++i) {
    CHECK(a.rowlb[i] == doctest::Approx(b.rowlb[i]));
  }
  for (size_t i = 0; i < a.rowub.size(); ++i) {
    CHECK(a.rowub[i] == doctest::Approx(b.rowub[i]));
  }

  // Scale vectors
  if (!a.col_scales.empty()) {
    REQUIRE(a.col_scales.size() == b.col_scales.size());
    for (size_t i = 0; i < a.col_scales.size(); ++i) {
      CHECK(a.col_scales[i] == doctest::Approx(b.col_scales[i]));
    }
  }
  if (!a.row_scales.empty()) {
    REQUIRE(a.row_scales.size() == b.row_scales.size());
    for (size_t i = 0; i < a.row_scales.size(); ++i) {
      CHECK(a.row_scales[i] == doctest::Approx(b.row_scales[i]));
    }
  }

  // Integer variables
  REQUIRE(a.colint.size() == b.colint.size());
  for (size_t i = 0; i < a.colint.size(); ++i) {
    CHECK(a.colint[i] == b.colint[i]);
  }
}

/// Make a deep copy of numeric vectors without copying the whole struct
/// (metadata-only fields like col_labels_meta that may not be serialised).
[[nodiscard]] auto clone_numeric_vectors(const FlatLinearProblem& src)
    -> FlatLinearProblem
{
  FlatLinearProblem dst {};
  dst.ncols = src.ncols;
  dst.nrows = src.nrows;
  dst.name = src.name;

  dst.matbeg = src.matbeg;
  dst.matind = src.matind;
  dst.matval = src.matval;
  dst.collb = src.collb;
  dst.colub = src.colub;
  dst.objval = src.objval;
  dst.rowlb = src.rowlb;
  dst.rowub = src.rowub;
  dst.colint = src.colint;
  dst.col_scales = src.col_scales;
  dst.row_scales = src.row_scales;
  dst.stats_nnz = src.stats_nnz;
  dst.stats_zeroed = src.stats_zeroed;
  dst.stats_max_abs = src.stats_max_abs;
  dst.stats_min_abs = src.stats_min_abs;
  dst.equilibration_method = src.equilibration_method;
  dst.scale_objective = src.scale_objective;

  return dst;
}

}  // namespace

TEST_CASE("compress_flat_lp — round-trip with tiny LP (zstd)")
{
  auto original = make_tiny_flp();
  auto expected = clone_numeric_vectors(original);

  const auto buf = compress_flat_lp(original, CompressionCodec::zstd);
  REQUIRE_FALSE(buf.empty());

  decompress_flat_lp(original, buf);

  verify_flp_equal(original, expected);
}

TEST_CASE("compress_flat_lp — round-trip with realistic LP (zstd)")
{
  auto original = make_realistic_flp();
  auto expected = clone_numeric_vectors(original);

  const auto buf = compress_flat_lp(original, CompressionCodec::zstd);
  REQUIRE_FALSE(buf.empty());

  decompress_flat_lp(original, buf);

  verify_flp_equal(original, expected);
}

TEST_CASE("compress_flat_lp — round-trip with lz4 codec")
{
  if (!is_codec_available(CompressionCodec::lz4)) {
    MESSAGE("lz4 codec not available — skipping");
    return;
  }

  auto original = make_tiny_flp();
  auto expected = clone_numeric_vectors(original);

  const auto buf = compress_flat_lp(original, CompressionCodec::lz4);
  REQUIRE_FALSE(buf.empty());

  decompress_flat_lp(original, buf);

  verify_flp_equal(original, expected);
}

TEST_CASE("compress_flat_lp — round-trip with uncompressed codec")
{
  auto original = make_tiny_flp();
  // compress_flat_lp returns empty buffer for uncompressed codec
  const auto buf = compress_flat_lp(original, CompressionCodec::uncompressed);
  CHECK(buf.empty());
}

TEST_CASE("compress_flat_lp — round-trip with gzip codec")
{
  auto original = make_tiny_flp();
  auto expected = clone_numeric_vectors(original);

  const auto buf = compress_flat_lp(original, CompressionCodec::gzip);
  REQUIRE_FALSE(buf.empty());

  decompress_flat_lp(original, buf);

  verify_flp_equal(original, expected);
}

TEST_CASE("compress_flat_lp — uncompressed codec returns empty buffer")
{
  auto original = make_tiny_flp();

  const auto buf = compress_flat_lp(original, CompressionCodec::uncompressed);
  CHECK(buf.empty());
}

TEST_CASE("compress_flat_lp — empty LP round-trip")
{
  FlatLinearProblem flp {};
  flp.ncols = 0;
  flp.nrows = 0;
  flp.name = "empty";

  // total_bytes == 0 → compress_flat_lp returns empty buffer
  const auto buf = compress_flat_lp(flp, CompressionCodec::zstd);
  CHECK(buf.empty());
}

TEST_CASE("compress_flat_lp — three round-trips on same FLP")
{
  auto original = make_tiny_flp();
  auto expected = clone_numeric_vectors(original);

  for (int i = 0; i < 3; ++i) {
    const auto buf = compress_flat_lp(original, CompressionCodec::zstd);
    REQUIRE_FALSE(buf.empty());

    decompress_flat_lp(original, buf);
    verify_flp_equal(original, expected);
  }
}

TEST_CASE("compress_flat_lp — scaled LP preserves col_scales and row_scales")
{
  auto original = make_tiny_flp();
  original.col_scales = {0.5, 2.0};
  original.row_scales = {3.0, 0.25};
  auto expected = clone_numeric_vectors(original);

  const auto buf = compress_flat_lp(original, CompressionCodec::zstd);
  REQUIRE_FALSE(buf.empty());

  decompress_flat_lp(original, buf);

  verify_flp_equal(original, expected);
}

TEST_CASE("compress_flat_lp — flp with integer variables round-trip")
{
  FlatLinearProblem flp {};
  flp.ncols = 3;
  flp.nrows = 1;
  flp.name = "mip_test";

  flp.matbeg = {0, 1, 2, 3};
  flp.matind = {0, 0, 0};
  flp.matval = {1.0, 2.0, 3.0};
  flp.collb = {0.0, 0.0, 0.0};
  flp.colub = {1.0, 1.0, 1.0};
  flp.objval = {1.0, 1.0, 1.0};
  flp.rowlb = {5.0};
  flp.rowub = {5.0};
  flp.colint = {0, 1};  // columns 0 and 1 are integer
  flp.col_scales = {1.0, 1.0, 1.0};
  flp.row_scales = {1.0};

  auto expected = clone_numeric_vectors(flp);

  const auto buf = compress_flat_lp(flp, CompressionCodec::zstd);
  REQUIRE_FALSE(buf.empty());

  decompress_flat_lp(flp, buf);

  verify_flp_equal(flp, expected);
}

TEST_CASE("clear_flat_lp_vectors removes numeric vectors")
{
  auto flp = make_tiny_flp();

  clear_flat_lp_vectors(flp);

  CHECK(flp.matbeg.empty());
  CHECK(flp.matind.empty());
  CHECK(flp.matval.empty());
  CHECK(flp.collb.empty());
  CHECK(flp.colub.empty());
  CHECK(flp.objval.empty());
  CHECK(flp.rowlb.empty());
  CHECK(flp.rowub.empty());
  CHECK(flp.colint.empty());
  CHECK(flp.col_scales.empty());
  CHECK(flp.row_scales.empty());

  // Metadata preserved
  CHECK(flp.ncols == 2);
  CHECK(flp.nrows == 2);
  CHECK(flp.name == "tiny_test");
}

// Regression guard for the `vec = {}` trap: assigning an empty
// initializer_list clears a vector's SIZE but keeps its CAPACITY, so the
// "freed" numeric arrays stayed resident alongside the lz4 buffer (~half the
// compress-mode flat-LP RSS).  `empty()` is true either way — only
// `capacity() == 0` proves the buffer was actually returned to the allocator.
TEST_CASE(
    "compress_flat_lp / clear_flat_lp_vectors release CAPACITY")  // NOLINT
{
  const auto check_all_freed = [](const FlatLinearProblem& flp)
  {
    CHECK(flp.matbeg.capacity() == 0);
    CHECK(flp.matind.capacity() == 0);
    CHECK(flp.matval.capacity() == 0);
    CHECK(flp.collb.capacity() == 0);
    CHECK(flp.colub.capacity() == 0);
    CHECK(flp.objval.capacity() == 0);
    CHECK(flp.rowlb.capacity() == 0);
    CHECK(flp.rowub.capacity() == 0);
    CHECK(flp.colint.capacity() == 0);
    CHECK(flp.col_scales.capacity() == 0);
    CHECK(flp.row_scales.capacity() == 0);
  };

  SUBCASE("compress_flat_lp frees the source numeric vectors")
  {
    auto flp = make_realistic_flp();
    REQUIRE(flp.matval.capacity() > 0);  // precondition: real buffers exist
    REQUIRE(flp.matind.capacity() > 0);

    const auto buf = compress_flat_lp(flp, CompressionCodec::lz4);
    REQUIRE_FALSE(buf.empty());

    check_all_freed(flp);
  }

  SUBCASE("clear_flat_lp_vectors frees the numeric vectors")
  {
    auto flp = make_realistic_flp();
    REQUIRE(flp.matval.capacity() > 0);
    REQUIRE(flp.matind.capacity() > 0);

    clear_flat_lp_vectors(flp);

    check_all_freed(flp);
  }
}

TEST_CASE("compress_flat_lp — snappy codec if available")
{
  if (!is_codec_available(CompressionCodec::snappy)) {
    MESSAGE("snappy codec not available — skipping");
    return;
  }

  auto original = make_tiny_flp();
  auto expected = clone_numeric_vectors(original);

  const auto buf = compress_flat_lp(original, CompressionCodec::snappy);
  REQUIRE_FALSE(buf.empty());

  decompress_flat_lp(original, buf);

  verify_flp_equal(original, expected);
}

TEST_CASE("compress_flat_lp with double round-trip through CompressedBuffer")
{
  // Compress, then reconstruct via CompressedBuffer, then decompress
  auto flp = make_tiny_flp();
  auto expected = clone_numeric_vectors(flp);

  const auto buf = compress_flat_lp(flp, CompressionCodec::zstd);
  REQUIRE_FALSE(buf.empty());

  // The CompressedBuffer carries metadata for exact reconstruction
  CompressedBuffer cb {
      .data = buf.data,
      .original_size = buf.original_size,
      .codec = buf.codec,
      .flp_nnz = buf.flp_nnz,
      .flp_colint_count = buf.flp_colint_count,
      .flp_col_scales_size = buf.flp_col_scales_size,
      .flp_row_scales_size = buf.flp_row_scales_size,
  };

  decompress_flat_lp(flp, cb);

  verify_flp_equal(flp, expected);
}

namespace
{
/// Byte-wise equality of every numeric vector (bit-exact, NaN-safe).
template<typename T>
[[nodiscard]] bool same_bytes(const std::vector<T>& a, const std::vector<T>& b)
{
  return a.size() == b.size()
      && (a.empty()
          || std::memcmp(a.data(), b.data(), a.size() * sizeof(T)) == 0);
}

[[nodiscard]] bool flp_bit_equal(const FlatLinearProblem& a,
                                 const FlatLinearProblem& b)
{
  return same_bytes(a.matbeg, b.matbeg) && same_bytes(a.matind, b.matind)
      && same_bytes(a.colint, b.colint) && same_bytes(a.matval, b.matval)
      && same_bytes(a.collb, b.collb) && same_bytes(a.colub, b.colub)
      && same_bytes(a.objval, b.objval) && same_bytes(a.rowlb, b.rowlb)
      && same_bytes(a.rowub, b.rowub) && same_bytes(a.col_scales, b.col_scales)
      && same_bytes(a.row_scales, b.row_scales);
}
}  // namespace

TEST_CASE(
    "compress_flat_lp — cross-scene dedup round-trips bit-exact")  // NOLINT
{
  const auto codec = select_codec(CompressionCodec::auto_select);
  auto registry = std::make_shared<FlatLpDedupRegistry>();

  // Scene A, scene B (a few bounds differ: inflows), scene C identical
  // to A — the shape measured across the scenes of one phase.
  auto a = make_realistic_flp();
  auto b = make_realistic_flp();
  b.collb[3] = 1.25;
  b.colub[7] = 42.0;
  b.rowlb[5] = -std::numeric_limits<double>::quiet_NaN();
  auto c = make_realistic_flp();
  const auto a0 = a;
  const auto b0 = b;
  const auto c0 = c;

  const auto ca = compress_flat_lp(a, codec, registry.get(), "k0_p1");
  const auto cb = compress_flat_lp(b, codec, registry.get(), "k0_p1");
  const auto cc = compress_flat_lp(c, codec, registry.get(), "k0_p1");
  CHECK(registry->live_bases() == 1);

  // All three share one base; identical cells store no delta at all.
  REQUIRE(ca.xor_base != nullptr);
  CHECK(cb.xor_base == ca.xor_base);
  CHECK(cc.xor_base == ca.xor_base);
  CHECK(ca.data.empty());
  CHECK(cc.data.empty());
  CHECK_FALSE(cb.data.empty());
  CHECK(cb.data.size() < ca.xor_base->data.size());
  CHECK_FALSE(ca.empty());
  CHECK_FALSE(cc.empty());

  decompress_flat_lp(a, ca);
  decompress_flat_lp(b, cb);
  decompress_flat_lp(c, cc);
  CHECK(flp_bit_equal(a, a0));
  CHECK(flp_bit_equal(b, b0));
  CHECK(flp_bit_equal(c, c0));
}

TEST_CASE("FlatLpDedupRegistry — size mismatch and key isolation")  // NOLINT
{
  const auto codec = select_codec(CompressionCodec::auto_select);
  FlatLpDedupRegistry registry;

  const std::vector<char> x(4096, 'x');
  std::vector<char> y(4096, 'x');
  y[100] = 'y';
  const std::vector<char> z(1000, 'z');

  const auto bx = registry.compress("p1", x, codec);
  const auto by = registry.compress("p1", y, codec);
  const auto bz = registry.compress("p1", z, codec);  // different size
  const auto bo = registry.compress("p2", y, codec);  // other key

  CHECK(bz.xor_base == nullptr);  // plain fallback
  CHECK(bo.xor_base != bx.xor_base);
  CHECK(bx.decompress_data() == x);
  CHECK(by.decompress_data() == y);
  CHECK(bz.decompress_data() == z);
  CHECK(bo.decompress_data() == y);
  CHECK(registry.live_bases() == 2);
}

TEST_CASE("FlatLpDedupRegistry — base freed with its last cell")  // NOLINT
{
  const auto codec = select_codec(CompressionCodec::auto_select);
  FlatLpDedupRegistry registry;
  const std::vector<char> x(2048, 'a');
  {
    const auto b1 = registry.compress("p", x, codec);
    CHECK(registry.live_bases() == 1);
    CHECK(b1.decompress_data() == x);
  }
  CHECK(registry.live_bases() == 0);
  // A later cell re-publishes a fresh base for the same key.
  const auto b2 = registry.compress("p", x, codec);
  CHECK(registry.live_bases() == 1);
  CHECK(b2.decompress_data() == x);
}

TEST_CASE(
    "compress_flat_lp — zstd1 round trip bit-exact, plain and dedup")  // NOLINT
{
  for (const bool dedup : {false, true}) {
    CAPTURE(dedup);
    auto registry = std::make_shared<FlatLpDedupRegistry>();
    auto a = make_realistic_flp();
    auto b = make_realistic_flp();
    b.colub[2] = 7.5;
    b.rowub[4] = 3.25;
    const auto a0 = a;
    const auto b0 = b;
    auto* reg = dedup ? registry.get() : nullptr;
    const auto ca = compress_flat_lp(a, CompressionCodec::zstd, reg, "k0_p2");
    const auto cb = compress_flat_lp(b, CompressionCodec::zstd, reg, "k0_p2");
    CHECK(ca.codec == CompressionCodec::zstd);
    CHECK(cb.codec == CompressionCodec::zstd);
    CHECK((ca.xor_base != nullptr) == dedup);
    decompress_flat_lp(a, ca);
    decompress_flat_lp(b, cb);
    CHECK(flp_bit_equal(a, a0));
    CHECK(flp_bit_equal(b, b0));
  }
}

TEST_CASE(
    "compress_flat_lp — default codec is zstd, lz4 override honoured")  // NOLINT
{
  auto a = make_realistic_flp();
  const auto a0 = a;
  const auto ca = compress_flat_lp(a, CompressionCodec::auto_select);
  CHECK(ca.codec == CompressionCodec::zstd);
  decompress_flat_lp(a, ca);
  CHECK(flp_bit_equal(a, a0));

  if (is_codec_available(CompressionCodec::lz4)) {
    auto b = make_realistic_flp();
    const auto cb = compress_flat_lp(b, CompressionCodec::lz4);
    CHECK(cb.codec == CompressionCodec::lz4);
    decompress_flat_lp(b, cb);
    CHECK(flp_bit_equal(b, a0));
  }
}

// NOLINTEND(bugprone-misplaced-widening-cast,
// modernize-use-ranges,readability-math-missing-parentheses)
