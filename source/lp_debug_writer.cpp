/**
 * @file      lp_debug_writer.cpp
 * @brief     Implementation of LpDebugWriter
 * @date      2026-05-01
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <string_view>
#include <vector>

#include <fcntl.h>  // O_RDONLY, O_WRONLY
#include <gtopt/linear_interface.hpp>
#include <gtopt/lp_debug_writer.hpp>
#include <gtopt/process_util.hpp>
#include <gtopt/work_pool.hpp>
#include <spawn.h>  // posix_spawn
#include <spdlog/spdlog.h>
#include <sys/wait.h>  // waitpid
#include <unistd.h>  // STDIN_FILENO
#include <zlib.h>
#include <zstd.h>

namespace gtopt
{

namespace
{

/// RAII wrapper for posix_spawn_file_actions_t.
struct LpSpawnActions
{
  posix_spawn_file_actions_t value {};
  LpSpawnActions() noexcept { ::posix_spawn_file_actions_init(&value); }
  ~LpSpawnActions() noexcept { ::posix_spawn_file_actions_destroy(&value); }
  LpSpawnActions(const LpSpawnActions&) = delete;
  LpSpawnActions(LpSpawnActions&&) = delete;
  auto operator=(const LpSpawnActions&) -> LpSpawnActions& = delete;
  auto operator=(LpSpawnActions&&) -> LpSpawnActions& = delete;
};

/// Run the executable @p bin with the argument vector @p args (no shell is
/// involved: every element reaches the child verbatim).  stdin/stdout/
/// stderr are bound to /dev/null.  Returns true when the child exits 0.
bool run_external(const std::string& bin,
                  const std::vector<std::string>& args) noexcept
{
  try {
    std::vector<std::string> arg_strings;
    arg_strings.reserve(args.size() + 1);
    arg_strings.push_back(bin);
    arg_strings.insert(arg_strings.end(), args.begin(), args.end());

    std::vector<char*> argv;
    argv.reserve(arg_strings.size() + 1);
    for (auto& a : arg_strings) {
      argv.push_back(a.data());
    }
    argv.push_back(nullptr);

    LpSpawnActions fa;
    for (const int fd : {STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO}) {
      const int flags = (fd == STDIN_FILENO) ? O_RDONLY : O_WRONLY;
      if (::posix_spawn_file_actions_addopen(
              &fa.value, fd, "/dev/null", flags, 0)
          != 0)
      {
        return false;
      }
    }

    pid_t pid = 0;
    if (::posix_spawn(&pid,
                      bin.c_str(),
                      &fa.value,
                      nullptr,
                      argv.data(),
                      spawn_environment())
        != 0)
    {
      return false;
    }
    int status = 0;
    while (::waitpid(pid, &status, 0) < 0) {
      if (errno != EINTR) {
        return false;
      }
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
  } catch (...) {
    return false;
  }
}

/// Absolute form of @p path, so it can never be mistaken for a command-line
/// option by the external tool (a relative path may start with '-').
[[nodiscard]] std::string absolute_arg(const std::string& path)
{
  std::error_code ec;
  auto abs = std::filesystem::absolute(path, ec);
  return ec ? path : abs.string();
}

/// Codec table — maps codec names to binary commands and output extensions.
/// `flags` are the tool options placed before the (absolute) file path.
struct CodecInfo
{
  std::string_view name;
  std::array<std::string_view, 4> flags;
  std::string_view ext;
};
constexpr std::array<CodecInfo, 5> kCodecs {
    {
        {.name = "gzip", .flags = {"-f"}, .ext = ".gz"},
        {.name = "zstd", .flags = {"--rm", "-f", "-q"}, .ext = ".zst"},
        {.name = "lz4", .flags = {"-m", "-f", "--rm", "-q"}, .ext = ".lz4"},
        {.name = "bzip2", .flags = {"-f"}, .ext = ".bz2"},
        {.name = "xz", .flags = {"-f"}, .ext = ".xz"},
    },
};

/// Run codec @p entry on @p src_path through its external binary.
/// Returns the compressed path on success, empty otherwise.
[[nodiscard]] std::string run_codec_binary(const CodecInfo& entry,
                                           const std::string& src_path)
{
  const auto bin = find_trusted_executable(entry.name);
  if (bin.empty()) {
    spdlog::debug("LpDebugWriter: {} binary not found", entry.name);
    return {};
  }
  std::vector<std::string> args;
  for (const auto flag : entry.flags) {
    if (!flag.empty()) {
      args.emplace_back(flag);
    }
  }
  args.push_back(absolute_arg(src_path));
  if (run_external(bin, args)) {
    std::string out = src_path + std::string(entry.ext);
    if (std::filesystem::exists(out)) {
      spdlog::debug("LpDebugWriter: compressed {} via {} → {}",
                    src_path,
                    entry.name,
                    out);
      return out;
    }
  }
  spdlog::debug("LpDebugWriter: {} failed for {}", entry.name, src_path);
  return {};
}

/// Table entry for a codec known to be in kCodecs.
[[nodiscard]] const CodecInfo& codec_entry(std::string_view name) noexcept
{
  const auto* const it = std::ranges::find(kCodecs, name, &CodecInfo::name);
  return (it != kCodecs.end()) ? *it : kCodecs.front();
}

/// Try a named codec directly.  Returns the compressed path on success, or
/// an empty string when the binary is unavailable or the command fails.
/// Falls back to inline libzstd for "zstd" and inline zlib for "gzip".
std::string try_codec(const std::string& src_path, const std::string& codec)
{
  const auto* const it = std::ranges::find(kCodecs, codec, &CodecInfo::name);
  if (it == kCodecs.end()) {
    return {};  // codec not in table
  }

  if (auto out = run_codec_binary(*it, src_path); !out.empty()) {
    return out;
  }

  // Named binary unavailable or failed — try inline library fallback.
  if (codec == "zstd") {
    const auto result = zstd_lp_file_inline(src_path);
    if (!result.empty()) {
      spdlog::debug(
          "LpDebugWriter: compressed {} via inline libzstd (zstd binary "
          "unavailable)",
          src_path);
      return result;
    }
  }
  if (codec == "gzip") {
    const auto result = gzip_lp_file_inline(src_path);
    if (!result.empty()) {
      spdlog::debug(
          "LpDebugWriter: compressed {} via inline zlib (gzip binary "
          "unavailable)",
          src_path);
      return result;
    }
  }
  return {};  // codec found in table but could not compress
}

/// Try calling gtopt_compress_lp with an optional --codec suggestion.
/// Returns the result path on success, empty string otherwise.
std::string try_gtopt_compress_lp(const std::string& src_path,
                                  const std::string& codec_hint)
{
  const auto bin = find_trusted_executable("gtopt_compress_lp");
  if (bin.empty()) {
    return {};
  }

  // argv: gtopt_compress_lp --quiet [--codec <hint>] <absolute path>
  std::vector<std::string> args {"--quiet"};
  if (!codec_hint.empty()) {
    args.emplace_back("--codec");
    args.push_back(codec_hint);
  }
  args.push_back(absolute_arg(src_path));

  if (!run_external(bin, args)) {
    spdlog::debug("LpDebugWriter: gtopt_compress_lp returned non-zero for {}",
                  src_path);
    return {};
  }

  // Look for the compressed output (any known extension).
  for (const char* ext : {".gz", ".zst", ".lz4", ".bz2", ".xz"}) {
    const std::string candidate = src_path + ext;
    if (std::filesystem::exists(candidate)) {
      spdlog::debug("LpDebugWriter: compressed {} via gtopt_compress_lp → {}",
                    src_path,
                    candidate);
      return candidate;
    }
  }
  // Compressed in-place with an unknown extension or already removed.
  if (!std::filesystem::exists(src_path)) {
    return src_path;  // original gone → success even without known extension
  }
  return {};
}

}  // namespace

/// Zstd compression level: 3 is the library default, offering a good
/// balance between speed and ratio for LP debug files.
constexpr int kZstdCompressionLevel = 3;

std::string gzip_lp_file_inline(const std::string& src_path)
{
  std::string gz_path = src_path + ".gz";

  std::ifstream src(src_path, std::ios::binary);
  if (!src.is_open()) {
    spdlog::warn("LpDebugWriter: cannot open {} for gzip compression",
                 src_path);
    return {};
  }

  gzFile gz = gzopen(gz_path.c_str(), "wb");
  if (gz == nullptr) {
    spdlog::warn("LpDebugWriter: cannot create gzip file {}", gz_path);
    return {};
  }

  constexpr std::size_t kBufSize = 65536;
  std::array<char, kBufSize> buf {};
  bool ok = true;
  while (src) {
    src.read(buf.data(), static_cast<std::streamsize>(kBufSize));
    const auto n = static_cast<unsigned int>(src.gcount());
    if (n > 0) {
      if (gzwrite(gz, buf.data(), n) == 0) {
        ok = false;
        break;
      }
    }
  }
  gzclose(gz);

  if (!ok) {
    spdlog::warn("LpDebugWriter: gzip write error for {}", gz_path);
    std::error_code rm_ec;
    std::filesystem::remove(gz_path, rm_ec);
    return {};
  }

  src.close();
  std::error_code ec;
  std::filesystem::remove(src_path, ec);
  if (ec) {
    spdlog::warn(
        "LpDebugWriter: could not remove {}: {}", src_path, ec.message());
  }

  return gz_path;
}

std::string zstd_lp_file_inline(const std::string& src_path)
{
  std::string zst_path = src_path + ".zst";

  std::ifstream src(src_path, std::ios::binary | std::ios::ate);
  if (!src.is_open()) {
    spdlog::warn("LpDebugWriter: cannot open {} for zstd compression",
                 src_path);
    return {};
  }

  const auto file_size = static_cast<std::size_t>(src.tellg());
  src.seekg(0, std::ios::beg);

  std::vector<char> input(file_size);
  if (!src.read(input.data(), static_cast<std::streamsize>(file_size))) {
    spdlog::warn("LpDebugWriter: cannot read {} for zstd compression",
                 src_path);
    return {};
  }
  src.close();

  const auto bound = ZSTD_compressBound(file_size);
  std::vector<char> output(bound);

  const auto compressed_size = ZSTD_compress(
      output.data(), bound, input.data(), file_size, kZstdCompressionLevel);

  if (ZSTD_isError(compressed_size) != 0U) {
    spdlog::warn("LpDebugWriter: zstd compress error for {}: {}",
                 src_path,
                 ZSTD_getErrorName(compressed_size));
    return {};
  }

  std::ofstream out(zst_path, std::ios::binary);
  if (!out.is_open()) {
    spdlog::warn("LpDebugWriter: cannot create zstd file {}", zst_path);
    return {};
  }
  out.write(output.data(), static_cast<std::streamsize>(compressed_size));
  out.close();

  std::error_code ec;
  std::filesystem::remove(src_path, ec);
  if (ec) {
    spdlog::warn(
        "LpDebugWriter: could not remove {}: {}", src_path, ec.message());
  }

  return zst_path;
}

std::string compress_lp_file(const std::string& src_path,
                             const std::string& lp_compression)
{
  // "none" → caller should not have called us, but handle gracefully.
  if (lp_compression == "none") {
    return src_path;
  }

  // ── 1. gtopt_compress_lp (user-configured, any codec) ────────────────────
  {
    const auto result = try_gtopt_compress_lp(src_path, lp_compression);
    if (!result.empty()) {
      return result;
    }
  }

  // ── 2. Named codec — try directly when gtopt_compress_lp not available ───
  if (!lp_compression.empty()) {
    const auto result = try_codec(src_path, lp_compression);
    if (!result.empty()) {
      return result;
    }
    // Codec not recognised or binary missing → warn and continue cascade.
    if (lp_compression != "zstd" && lp_compression != "gzip") {
      spdlog::warn(
          "LpDebugWriter: codec '{}' unavailable for {}; falling back to zstd",
          lp_compression,
          src_path);
    }
  }

  // ── 3. zstd binary ───────────────────────────────────────────────────────
  if (auto out = run_codec_binary(codec_entry("zstd"), src_path); !out.empty())
  {
    return out;
  }

  // ── 4. Inline libzstd — statically linked, always available ──────────────
  {
    const auto result = zstd_lp_file_inline(src_path);
    if (!result.empty()) {
      spdlog::debug("LpDebugWriter: compressed {} via inline libzstd",
                    src_path);
      return result;
    }
  }

  // ── 5. gzip binary ───────────────────────────────────────────────────────
  if (auto out = run_codec_binary(codec_entry("gzip"), src_path); !out.empty())
  {
    return out;
  }

  // ── 6. Inline zlib — secondary fallback ──────────────────────────────────
  {
    const auto result = gzip_lp_file_inline(src_path);
    if (!result.empty()) {
      spdlog::debug("LpDebugWriter: compressed {} via inline zlib", src_path);
      return result;
    }
  }

  // ── 7. Give up — keep the plain .lp file ─────────────────────────────────
  spdlog::warn(
      "LpDebugWriter: no compression available for {}; keeping plain .lp",
      src_path);
  return src_path;
}

LpDebugWriter::LpDebugWriter(std::string directory,
                             std::string compression,
                             AdaptiveWorkPool* pool)
    : m_directory_(std::move(directory))
    , m_compression_(std::move(compression))
    , m_pool_(pool)
{
}

bool LpDebugWriter::is_active() const noexcept
{
  return !m_directory_.empty();
}

void LpDebugWriter::ensure_directory()
{
  if (!m_dir_created_) {
    // Never throw from a diagnostic dump: an unwritable directory only
    // makes the following write fail with a warning.
    std::error_code ec;
    std::filesystem::create_directories(m_directory_, ec);
    if (ec) {
      spdlog::warn("LpDebugWriter: cannot create directory {}: {}",
                   m_directory_,
                   ec.message());
    }
    m_dir_created_ = true;
  }
}

void LpDebugWriter::write(const LinearInterface& li,
                          const std::string& filepath_stem)
{
  if (!is_active()) {
    return;
  }
  ensure_directory();
  auto result = li.write_lp(filepath_stem);
  if (!result) {
    spdlog::warn("{}", result.error().message);
    return;
  }
  const auto& lp_path = *result;
  spdlog::debug("LpDebugWriter: saved LP to {}", lp_path);
  compress_async(lp_path);
}

void LpDebugWriter::compress_async(const std::string& lp_path)
{
  // Empty, "none", and "uncompressed" all mean: keep the plain .lp file.
  // A non-empty codec string triggers compression.
  if (m_compression_.empty() || m_compression_ == "none"
      || m_compression_ == "uncompressed")
  {
    return;
  }

  if (m_pool_ != nullptr) {
    // Assign lowest priority to compress tasks (they don't block solving)
    const TaskRequirements kCompressReq {
        .priority = TaskPriority::Low,
        .priority_key = 0,
        .name = {},
    };
    auto fut = m_pool_->submit([lp_path, compr = m_compression_]
                               { return compress_lp_file(lp_path, compr); },
                               kCompressReq);
    if (fut.has_value()) {
      m_compress_futures_.push_back(std::move(*fut));
      return;
    }
  }
  (void)compress_lp_file(lp_path, m_compression_);
}

void LpDebugWriter::drain()
{
  for (auto& f : m_compress_futures_) {
    (void)f.get();
  }
  m_compress_futures_.clear();
}

namespace
{

/// Case-insensitive equality of one trimmed token against `wanted`.
[[nodiscard]] constexpr bool tok_eq_ci(std::string_view tok,
                                       std::string_view wanted) noexcept
{
  if (tok.size() != wanted.size()) {
    return false;
  }
  for (std::size_t i = 0; i < tok.size(); ++i) {
    const auto a = static_cast<unsigned char>(tok[i]);
    const auto b = static_cast<unsigned char>(wanted[i]);
    const auto al =
        (a >= 'A' && a <= 'Z') ? static_cast<unsigned char>(a + 32U) : a;
    const auto bl =
        (b >= 'A' && b <= 'Z') ? static_cast<unsigned char>(b + 32U) : b;
    if (al != bl) {
      return false;
    }
  }
  return true;
}

[[nodiscard]] constexpr std::string_view trim_ws(std::string_view s) noexcept
{
  while (!s.empty()
         && (s.front() == ' ' || s.front() == '\t' || s.front() == '\r'
             || s.front() == '\n'))
  {
    s.remove_prefix(1);
  }
  while (!s.empty()
         && (s.back() == ' ' || s.back() == '\t' || s.back() == '\r'
             || s.back() == '\n'))
  {
    s.remove_suffix(1);
  }
  return s;
}

[[nodiscard]] std::string_view pass_token(LpDebugPass which) noexcept
{
  switch (which) {
    case LpDebugPass::forward:
      return "forward";
    case LpDebugPass::backward:
      return "backward";
    case LpDebugPass::aperture:
      return "aperture";
  }
  return "";
}

}  // namespace

bool lp_debug_passes_includes(std::string_view passes,
                              LpDebugPass which) noexcept
{
  // Empty / unset → legacy default "forward,aperture" (backward NOT included)
  if (trim_ws(passes).empty()) {
    return which == LpDebugPass::forward || which == LpDebugPass::aperture;
  }
  const auto wanted = pass_token(which);
  std::string_view rest = passes;
  while (!rest.empty()) {
    const auto comma = rest.find(',');
    const auto raw =
        (comma == std::string_view::npos) ? rest : rest.substr(0, comma);
    const auto tok = trim_ws(raw);
    if (tok_eq_ci(tok, "all")) {
      return true;
    }
    if (tok_eq_ci(tok, wanted)) {
      return true;
    }
    if (comma == std::string_view::npos) {
      break;
    }
    rest.remove_prefix(comma + 1);
  }
  return false;
}

}  // namespace gtopt
