/**
 * @file      test_lp_debug_writer_path.cpp
 * @brief     compress_lp_file cascade under a controlled PATH
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `compress_lp_file` shells out to whatever compressors happen to be on
 * PATH, so `test_lp_debug_writer.cpp` exercises a host-dependent subset
 * of the cascade.  These tests replace PATH with a temp dir holding fake
 * `gtopt_compress_lp` / `zstd` / `gzip` / `lz4` scripts (or nothing at
 * all) so every rung — external tool success, non-zero exit, unknown
 * output extension, binary-missing → inline libzstd / zlib fallback —
 * runs deterministically.  Helpers are prefixed `ldwp_` for unity builds.
 */

#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <random>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/lp_debug_writer.hpp>

using namespace gtopt;

namespace
{

/// Temp dir with a `bin/` subdir that replaces PATH for the guard's
/// lifetime; the original PATH is restored on destruction.
class LdwpPathSandbox
{
public:
  LdwpPathSandbox()
      : m_root_(std::filesystem::temp_directory_path()
                / ("gtopt_ldwp_"
                   + std::to_string(
                       static_cast<std::uint64_t>(std::random_device {}()))))
  {
    std::filesystem::create_directories(bin());
    if (const char* old = std::getenv("PATH"); old != nullptr) {  // NOLINT
      m_old_path_ = old;
    }
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    ::setenv("PATH", bin().c_str(), 1);
  }

  ~LdwpPathSandbox()
  {
    if (m_old_path_) {
      // NOLINTNEXTLINE(concurrency-mt-unsafe)
      ::setenv("PATH", m_old_path_->c_str(), 1);
    } else {
      // NOLINTNEXTLINE(concurrency-mt-unsafe)
      ::unsetenv("PATH");
    }
    std::error_code ec;
    std::filesystem::remove_all(m_root_, ec);
  }

  LdwpPathSandbox(const LdwpPathSandbox&) = delete;
  LdwpPathSandbox(LdwpPathSandbox&&) = delete;
  auto operator=(const LdwpPathSandbox&) -> LdwpPathSandbox& = delete;
  auto operator=(LdwpPathSandbox&&) -> LdwpPathSandbox& = delete;

  [[nodiscard]] auto bin() const -> std::filesystem::path
  {
    return m_root_ / "bin";
  }

  /// Install an executable `/bin/sh` script named @p name.
  void tool(const std::string& name, const std::string& body) const
  {
    const auto p = bin() / name;
    {
      std::ofstream out(p);
      out << "#!/bin/sh\n" << body << "\n";
    }
    std::filesystem::permissions(p,
                                 std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace);
  }

  /// Write a small LP file and return its path.
  [[nodiscard]] auto lp_file(const std::string& name = "m.lp") const
      -> std::string
  {
    const auto p = m_root_ / name;
    std::ofstream out(p);
    out << "Minimize\n obj: x\nSubject To\n c1: x >= 1\nEnd\n";
    return p.string();
  }

private:
  std::filesystem::path m_root_;
  std::optional<std::string> m_old_path_;
};

}  // namespace

TEST_CASE(  // NOLINT
    "compress_lp_file - gtopt_compress_lp produces a known extension")
{
  LdwpPathSandbox sb;
  // Last argument is the file; mimic a codec by appending ".xz".
  sb.tool("gtopt_compress_lp",
          R"(for f; do :; done; /bin/cat "$f" > "$f.xz" && /bin/rm "$f")");
  const auto lp = sb.lp_file();
  const auto out = compress_lp_file(lp, "xz");
  CHECK(out == lp + ".xz");
  CHECK(std::filesystem::exists(out));
  CHECK_FALSE(std::filesystem::exists(lp));
}

TEST_CASE(  // NOLINT
    "compress_lp_file - gtopt_compress_lp with unknown extension")
{
  LdwpPathSandbox sb;
  // Compresses to an extension the writer does not know, removing the
  // source: treated as success and the original path is returned.
  sb.tool("gtopt_compress_lp",
          R"(for f; do :; done; /bin/cat "$f" > "$f.weird" && /bin/rm "$f")");
  const auto lp = sb.lp_file();
  CHECK(compress_lp_file(lp, "") == lp);
  CHECK_FALSE(std::filesystem::exists(lp));
}

TEST_CASE(  // NOLINT
    "compress_lp_file - gtopt_compress_lp leaving source untouched")
{
  LdwpPathSandbox sb;
  // Exit 0 but no output at all: the cascade continues to inline zstd.
  sb.tool("gtopt_compress_lp", "exit 0");
  const auto lp = sb.lp_file();
  const auto out = compress_lp_file(lp, "");
  CHECK(out == lp + ".zst");
  CHECK(std::filesystem::exists(out));
}

TEST_CASE(  // NOLINT
    "compress_lp_file - failing gtopt_compress_lp falls through")
{
  LdwpPathSandbox sb;
  sb.tool("gtopt_compress_lp", "exit 3");
  const auto lp = sb.lp_file();
  // Explicit zstd, no zstd binary → inline libzstd.
  const auto out = compress_lp_file(lp, "zstd");
  CHECK(out == lp + ".zst");
  CHECK(std::filesystem::exists(out));
}

TEST_CASE("compress_lp_file - empty PATH uses the inline codecs")  // NOLINT
{
  LdwpPathSandbox sb;

  SUBCASE("gzip → inline zlib")
  {
    const auto lp = sb.lp_file();
    const auto out = compress_lp_file(lp, "gzip");
    CHECK(out == lp + ".gz");
    CHECK(std::filesystem::exists(out));
    CHECK_FALSE(std::filesystem::exists(lp));
  }

  SUBCASE("lz4 missing → warn, then inline zstd")
  {
    const auto lp = sb.lp_file();
    const auto out = compress_lp_file(lp, "lz4");
    CHECK(out == lp + ".zst");
  }

  SUBCASE("auto cascade → inline zstd")
  {
    const auto lp = sb.lp_file();
    CHECK(compress_lp_file(lp, "") == lp + ".zst");
  }
}

TEST_CASE("compress_lp_file - named codec binary on PATH")  // NOLINT
{
  LdwpPathSandbox sb;

  SUBCASE("lz4 binary succeeds")
  {
    sb.tool("lz4",
            R"(for f; do :; done; /bin/cat "$f" > "$f.lz4" && /bin/rm "$f")");
    const auto lp = sb.lp_file();
    CHECK(compress_lp_file(lp, "lz4") == lp + ".lz4");
  }

  SUBCASE("lz4 binary fails, zstd binary succeeds")
  {
    sb.tool("lz4", "exit 1");
    sb.tool("zstd",
            R"(for f; do :; done; /bin/cat "$f" > "$f.zst" && /bin/rm "$f")");
    const auto lp = sb.lp_file();
    CHECK(compress_lp_file(lp, "lz4") == lp + ".zst");
  }

  SUBCASE("gzip binary fails → inline zlib")
  {
    sb.tool("gzip", "exit 1");
    const auto lp = sb.lp_file();
    CHECK(compress_lp_file(lp, "gzip") == lp + ".gz");
  }

  SUBCASE("zstd binary exits 0 without output → inline libzstd")
  {
    sb.tool("zstd", "exit 0");
    const auto lp = sb.lp_file();
    CHECK(compress_lp_file(lp, "") == lp + ".zst");
  }
}

TEST_CASE(  // NOLINT
    "compress_lp_file - unreadable source exhausts the cascade")
{
  LdwpPathSandbox sb;
  sb.tool("gzip", "exit 1");
  const auto missing = (sb.bin() / "nope.lp").string();
  // Every rung fails; the original path is returned unchanged.
  CHECK(compress_lp_file(missing, "") == missing);
}
