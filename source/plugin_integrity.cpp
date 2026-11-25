/**
 * @file      plugin_integrity.cpp
 * @brief     Verify that a solver plugin is the one built with this gtopt
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <algorithm>
#include <array>
#include <bit>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <format>
#include <fstream>
#include <iterator>
#include <span>
#include <utility>

#include <elf.h>
#include <gtopt/plugin_integrity.hpp>
#include <gtopt/sha256.hpp>
#include <link.h>
#include <spdlog/spdlog.h>

namespace gtopt
{

namespace
{

/// Upper bounds on what we read from a file (defensive: a corrupt header
/// must not make us allocate gigabytes).
constexpr std::uint64_t k_max_note_bytes = std::uint64_t {1} << 20U;
constexpr std::uint64_t k_max_strtab_bytes = std::uint64_t {64} << 20U;
constexpr std::uint64_t k_max_dynamic_bytes = std::uint64_t {1} << 20U;

constexpr std::size_t align_up(std::size_t v, std::size_t a) noexcept
{
  return (v + a - 1) / a * a;
}

std::string to_hex(std::span<const unsigned char> bytes)
{
  static constexpr std::string_view digits = "0123456789abcdef";
  std::string out;
  out.reserve(bytes.size() * 2);
  for (const auto b : bytes) {
    out.push_back(digits[(b >> 4U) & 0xFU]);
    out.push_back(digits[b & 0xFU]);
  }
  return out;
}

/// Scan a PT_NOTE payload for the GNU build-id note.
std::string find_gnu_build_id(std::span<const unsigned char> notes,
                              std::size_t align)
{
  align = std::max<std::size_t>(align, 4);
  std::size_t off = 0;
  while (off + sizeof(Elf64_Nhdr) <= notes.size()) {
    Elf64_Nhdr nh {};
    std::memcpy(&nh, notes.subspan(off).data(), sizeof(nh));
    off += sizeof(nh);
    const std::size_t name_end = off + nh.n_namesz;
    const std::size_t desc_off = align_up(name_end, align);
    const std::size_t desc_end = desc_off + nh.n_descsz;
    if (name_end > notes.size() || desc_end > notes.size()) {
      break;
    }
    const auto name = notes.subspan(off, nh.n_namesz);
    if (nh.n_type == NT_GNU_BUILD_ID && nh.n_namesz == 4
        && std::memcmp(name.data(), "GNU", 4) == 0 && nh.n_descsz > 0)
    {
      return to_hex(notes.subspan(desc_off, nh.n_descsz));
    }
    off = align_up(desc_end, align);
  }
  return {};
}

bool read_at(std::ifstream& in,
             std::uint64_t offset,
             void* dst,
             std::size_t size)
{
  in.clear();
  in.seekg(static_cast<std::streamoff>(offset));
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  in.read(reinterpret_cast<char*>(dst), static_cast<std::streamsize>(size));
  return in.good() || std::cmp_equal(in.gcount(), size);
}

std::vector<std::string> split_path_list(std::string_view list)
{
  std::vector<std::string> out;
  while (!list.empty()) {
    const auto pos = list.find(':');
    const auto item = list.substr(0, pos);
    if (!item.empty()) {
      out.emplace_back(item);
    }
    if (pos == std::string_view::npos) {
      break;
    }
    list.remove_prefix(pos + 1);
  }
  return out;
}

IntegrityDecision make(IntegrityVerdict v, std::string status, std::string msg)
{
  return IntegrityDecision {
      .verdict = v,
      .status = std::move(status),
      .message = std::move(msg),
  };
}

}  // namespace

// ── ELF inspection ─────────────────────────────────────────────────────────

std::expected<ElfIdentity, std::string> read_elf_identity(
    const std::filesystem::path& path)
{
  if constexpr (std::endian::native != std::endian::little) {
    return std::unexpected("ELF inspection supports little-endian hosts only");
  }
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    return std::unexpected(std::format("cannot open {}", path.string()));
  }
  Elf64_Ehdr eh {};
  static constexpr std::array<unsigned char, SELFMAG> elf_magic {
      ELFMAG0,
      ELFMAG1,
      ELFMAG2,
      ELFMAG3,
  };
  if (!read_at(in, 0, &eh, sizeof(eh))
      || !std::ranges::equal(std::span(eh.e_ident).first<SELFMAG>(), elf_magic))
  {
    return std::unexpected(std::format("{} is not an ELF file", path.string()));
  }
  if (eh.e_ident[EI_CLASS] != ELFCLASS64 || eh.e_ident[EI_DATA] != ELFDATA2LSB
      || eh.e_phentsize != sizeof(Elf64_Phdr))
  {
    return std::unexpected(
        std::format("{} is not a 64-bit little-endian ELF", path.string()));
  }

  std::vector<Elf64_Phdr> phdrs(eh.e_phnum);
  if (eh.e_phnum == 0
      || !read_at(
          in, eh.e_phoff, phdrs.data(), phdrs.size() * sizeof(Elf64_Phdr)))
  {
    return std::unexpected(
        std::format("{}: cannot read program headers", path.string()));
  }

  ElfIdentity id;

  // Build-id from the PT_NOTE segments.
  for (const auto& ph : phdrs) {
    if (ph.p_type != PT_NOTE || ph.p_filesz == 0
        || ph.p_filesz > k_max_note_bytes)
    {
      continue;
    }
    std::vector<unsigned char> buf(ph.p_filesz);
    if (!read_at(in, ph.p_offset, buf.data(), buf.size())) {
      continue;
    }
    id.build_id = find_gnu_build_id(buf, ph.p_align);
    if (!id.build_id.empty()) {
      break;
    }
  }

  // SONAME / RUNPATH from PT_DYNAMIC (string table located via PT_LOAD).
  const auto dyn_it = std::ranges::find_if(
      phdrs, [](const Elf64_Phdr& ph) { return ph.p_type == PT_DYNAMIC; });
  if (dyn_it == phdrs.end() || dyn_it->p_filesz > k_max_dynamic_bytes) {
    return id;
  }
  std::vector<Elf64_Dyn> dyn(dyn_it->p_filesz / sizeof(Elf64_Dyn));
  if (!read_at(
          in, dyn_it->p_offset, dyn.data(), dyn.size() * sizeof(Elf64_Dyn)))
  {
    return id;
  }
  std::uint64_t strtab_vaddr = 0;
  std::uint64_t strsz = 0;
  std::optional<std::uint64_t> soname_off;
  std::optional<std::uint64_t> runpath_off;
  std::optional<std::uint64_t> rpath_off;
  // Elf64_Dyn::d_un is a union by the ELF ABI: which member is valid
  // depends on d_tag, checked by the switch.
  // NOLINTBEGIN(cppcoreguidelines-pro-type-union-access)
  for (const auto& d : dyn) {
    if (d.d_tag == DT_NULL) {
      break;
    }
    switch (d.d_tag) {
      case DT_STRTAB:
        strtab_vaddr = d.d_un.d_ptr;
        break;
      case DT_STRSZ:
        strsz = d.d_un.d_val;
        break;
      case DT_SONAME:
        soname_off = d.d_un.d_val;
        break;
      case DT_RUNPATH:
        runpath_off = d.d_un.d_val;
        break;
      case DT_RPATH:
        rpath_off = d.d_un.d_val;
        break;
      default:
        break;
    }
  }
  // NOLINTEND(cppcoreguidelines-pro-type-union-access)
  if (strsz == 0 || strsz > k_max_strtab_bytes) {
    return id;
  }
  const auto load_it = std::ranges::find_if(
      phdrs,
      [strtab_vaddr](const Elf64_Phdr& ph)
      {
        return ph.p_type == PT_LOAD && strtab_vaddr >= ph.p_vaddr
            && strtab_vaddr < ph.p_vaddr + ph.p_filesz;
      });
  if (load_it == phdrs.end()) {
    return id;
  }
  std::string strtab(strsz, '\0');
  if (!read_at(in,
               strtab_vaddr - load_it->p_vaddr + load_it->p_offset,
               strtab.data(),
               strtab.size()))
  {
    return id;
  }
  const auto str_at = [&strtab](std::uint64_t off) -> std::string
  {
    if (off >= strtab.size()) {
      return {};
    }
    return {strtab.c_str() + off};  // NOLINT
  };
  if (soname_off) {
    id.soname = str_at(*soname_off);
  }
  if (runpath_off) {
    id.runpath = split_path_list(str_at(*runpath_off));
  } else if (rpath_off) {
    id.runpath = split_path_list(str_at(*rpath_off));
    id.runpath_is_rpath = true;
  }
  return id;
}

namespace
{

struct FindCtx
{
  std::uintptr_t addr {};
  std::optional<LoadedObject> found;
  bool first {true};
};

int find_object_cb(dl_phdr_info* info, std::size_t /*size*/, void* data)
{
  auto* ctx = static_cast<FindCtx*>(data);
  const bool is_main = std::exchange(ctx->first, false);
  const std::span phdrs(info->dlpi_phdr, info->dlpi_phnum);
  const bool contains = std::ranges::any_of(
      phdrs,
      [ctx, info](const ElfW(Phdr) & ph)
      {
        if (ph.p_type != PT_LOAD) {
          return false;
        }
        const auto start = info->dlpi_addr + ph.p_vaddr;
        return ctx->addr >= start && ctx->addr < start + ph.p_memsz;
      });
  if (!contains) {
    return 0;
  }
  LoadedObject obj;
  obj.is_main_program = is_main;
  for (const auto& ph : phdrs) {
    if (ph.p_type != PT_NOTE || ph.p_memsz == 0) {
      continue;
    }
    // The loader hands us the segment as an address (load bias + vaddr).
    const auto* base =
        // NOLINTNEXTLINE(performance-no-int-to-ptr,cppcoreguidelines-pro-type-reinterpret-cast)
        reinterpret_cast<const unsigned char*>(info->dlpi_addr + ph.p_vaddr);
    obj.build_id = find_gnu_build_id(std::span(base, ph.p_memsz), ph.p_align);
    if (!obj.build_id.empty()) {
      break;
    }
  }
  const std::string_view name =
      (info->dlpi_name != nullptr) ? info->dlpi_name : "";
  const std::filesystem::path p = (is_main || name.empty())
      ? std::filesystem::path("/proc/self/exe")
      : std::filesystem::path(name);
  std::error_code ec;
  auto canon = std::filesystem::canonical(p, ec);
  obj.path = ec ? p : canon;
  ctx->found = std::move(obj);
  return 1;
}

}  // namespace

std::optional<LoadedObject> find_loaded_object(const void* address)
{
  if (address == nullptr) {
    return std::nullopt;
  }
  FindCtx ctx;
  ctx.addr = std::bit_cast<std::uintptr_t>(address);
  ::dl_iterate_phdr(find_object_cb, &ctx);
  return std::move(ctx.found);
}

std::string short_build_id(std::string_view build_id)
{
  return std::string(build_id.substr(0, 12));
}

// ── Build-id manifest ──────────────────────────────────────────────────────

std::optional<std::string> parse_build_id_manifest(std::string_view text)
{
  const auto first = text.find_first_not_of(" \t\r\n");
  if (first == std::string_view::npos) {
    return std::nullopt;
  }
  text.remove_prefix(first);
  const auto end = text.find_first_of(" \t\r\n");
  const auto token = text.substr(0, end);
  // A GNU build-id is at least 8 bytes (16 hex digits) in practice; the
  // shortest the linkers emit is --build-id=fast/md5 (16 bytes).
  if (token.size() < 16 || token.size() % 2 != 0
      || !std::ranges::all_of(
          token, [](unsigned char c) { return std::isxdigit(c) != 0; }))
  {
    return std::nullopt;
  }
  std::string id(token);
  std::ranges::transform(
      id, id.begin(), [](unsigned char c) { return std::tolower(c); });
  return id;
}

std::optional<std::string> read_build_id_manifest(
    const std::filesystem::path& plugin_path)
{
  auto manifest = plugin_path;
  manifest += k_build_id_manifest_suffix;
  std::ifstream in(manifest);
  if (!in) {
    return std::nullopt;
  }
  std::string line;
  std::getline(in, line);
  return parse_build_id_manifest(line);
}

// ── Policy ─────────────────────────────────────────────────────────────────

std::optional<PluginCheckMode> parse_plugin_check_mode(std::string_view text)
{
  std::string s(text);
  std::ranges::transform(
      s, s.begin(), [](unsigned char c) { return std::tolower(c); });
  if (s == "warning" || s == "warn") {
    return PluginCheckMode::warning;
  }
  if (s == "error") {
    return PluginCheckMode::error;
  }
  return std::nullopt;
}

std::optional<PluginCheckMode> plugin_check_mode_from_env()
{
  // NOLINTNEXTLINE(concurrency-mt-unsafe)
  const char* v = std::getenv(std::string(k_plugin_check_env).c_str());
  if (v == nullptr || *v == '\0') {
    return std::nullopt;
  }
  if (const auto mode = parse_plugin_check_mode(v)) {
    return mode;
  }
  SPDLOG_WARN("{}='{}' is not 'warning' or 'error'; using the default '{}'",
              k_plugin_check_env,
              v,
              to_string(k_default_plugin_check_mode));
  return std::nullopt;
}

PluginIntegrityPolicy PluginIntegrityPolicy::from_env()
{
  return {.mode = plugin_check_mode_from_env().value_or(
              k_default_plugin_check_mode)};
}

std::string describe_plugin_check(PluginCheckMode mode,
                                  PluginCheckSource source)
{
  if (source == PluginCheckSource::compiled_default) {
    return std::format(
        "plugin check: {} ({})", to_string(mode), to_string(source));
  }
  return std::format("plugin check: {} ({}; compiled default: {})",
                     to_string(mode),
                     to_string(source),
                     to_string(k_default_plugin_check_mode));
}

IntegrityDecision integrity_finding(std::string status,
                                    std::string message,
                                    const PluginIntegrityPolicy& policy)
{
  const bool refuse = policy.mode == PluginCheckMode::error;
  message += refuse ? std::format(
                          " {}; use --plugin-check=warning or "
                          "GTOPT_PLUGIN_CHECK=warning to load it anyway).",
                          k_plugin_refused_marker)
                    : " Loading it anyway (use --plugin-check=error or "
                      "GTOPT_PLUGIN_CHECK=error to refuse it).";
  return make(refuse ? IntegrityVerdict::refuse : IntegrityVerdict::warn,
              std::move(status),
              std::move(message));
}

IntegrityDecision check_plugin_build_id(
    std::string_view plugin,
    std::optional<std::string_view> manifest_id,
    std::optional<std::string_view> file_id,
    const PluginIntegrityPolicy& policy)
{
  if (!file_id || file_id->empty()) {
    return integrity_finding(
        "no build-id",
        std::format("Plugin {} has no ELF GNU build-id note; its identity "
                    "cannot be verified.",
                    plugin),
        policy);
  }
  if (!manifest_id) {
    return integrity_finding(
        "no manifest",
        std::format("Plugin {} has no build-id manifest ({}{}); its identity "
                    "cannot be verified.",
                    plugin,
                    plugin,
                    k_build_id_manifest_suffix),
        policy);
  }
  if (*manifest_id == *file_id) {
    return make(IntegrityVerdict::ok, "match", {});
  }
  return integrity_finding(
      "MISMATCH",
      std::format("Plugin {} build-id {} does not match its manifest ({}): "
                  "the .so was replaced or copied without its manifest; "
                  "reinstall or rebuild it.",
                  plugin,
                  short_build_id(*file_id),
                  short_build_id(*manifest_id)),
      policy);
}

IntegrityDecision check_plugin_build_tag(
    std::string_view plugin,
    std::string_view host_tag,
    std::optional<std::string_view> plugin_tag,
    const PluginIntegrityPolicy& policy)
{
  if (!plugin_tag) {
    return integrity_finding(
        "unknown",
        std::format("Plugin {} does not report a build tag (built by an "
                    "older gtopt); cannot tell which build it belongs to.",
                    plugin),
        policy);
  }
  if (*plugin_tag == host_tag) {
    return make(IntegrityVerdict::ok, "match", {});
  }
  return integrity_finding(
      "differs",
      std::format("Plugin {} comes from another gtopt build (plugin tag "
                  "'{}', this binary '{}'): another build tree or a stale "
                  "install; set GTOPT_PLUGIN_DIR to this build's plugins.",
                  plugin,
                  *plugin_tag,
                  host_tag),
      policy);
}

std::string SolverVersion::str() const
{
  if (!known()) {
    return "unknown";
  }
  if (patch < 0) {
    return std::format("{}.{}", major, minor);
  }
  return std::format("{}.{}.{}", major, minor, patch);
}

IntegrityDecision check_solver_version(std::string_view solver,
                                       const SolverVersion& compiled,
                                       const SolverVersion& runtime,
                                       bool statically_linked,
                                       std::string_view library_path,
                                       const PluginIntegrityPolicy& policy)
{
  const auto where =
      library_path.empty() ? std::string_view {"(unknown path)"} : library_path;
  if (!compiled.known() || !runtime.known()) {
    return make(
        IntegrityVerdict::ok, statically_linked ? "static" : "unchecked", {});
  }
  if (compiled.major != runtime.major || compiled.minor != runtime.minor) {
    return integrity_finding(
        "MISMATCH",
        std::format("Solver '{}': plugin was compiled against {} {} but the "
                    "loaded library {} is {}: a different solver library is "
                    "shadowing the one the plugin was built with (check "
                    "LD_LIBRARY_PATH / ~/.local/lib) or the plugin is stale.",
                    solver,
                    solver,
                    compiled.str(),
                    where,
                    runtime.str()),
        policy);
  }
  if (compiled.patch >= 0 && runtime.patch >= 0
      && compiled.patch != runtime.patch)
  {
    return make(IntegrityVerdict::ok,
                "patch differs",
                std::format("Solver '{}': compiled against {}, running {} "
                            "from {} (patch-level difference only).",
                            solver,
                            compiled.str(),
                            runtime.str(),
                            where));
  }
  return make(IntegrityVerdict::ok, "match", {});
}

std::string solver_allow_unpinned_env(std::string_view solver)
{
  std::string up(solver);
  std::ranges::transform(
      up, up.begin(), [](unsigned char c) { return std::toupper(c); });
  return std::format("GTOPT_{}_ALLOW_UNPINNED", up);
}

namespace
{

/// A failed pin / identity check: warn when an override applies (naming
/// it), else refuse with the ways to override it.
IntegrityDecision overridable_finding(std::string_view solver,
                                      std::string status,
                                      std::string message,
                                      const VersionPinOverrides& overrides,
                                      const PluginIntegrityPolicy& policy,
                                      bool identity = true)
{
  const auto env = solver_allow_unpinned_env(solver);
  std::string reason;
  if (overrides.env_allow_unpinned) {
    reason = std::format("${} is set", env);
  } else if (overrides.built_allow_unpinned) {
    reason = std::format("the plugin was built with -D{}=ON", env);
  } else if (policy.mode == PluginCheckMode::warning) {
    reason = "plugin check mode 'warning'";
  }
  if (!reason.empty()) {
    message += std::format(" Loading it anyway ({}).", reason);
    return make(IntegrityVerdict::warn, std::move(status), std::move(message));
  }
  // Only the pin honours the build flag; the identity check passes
  // overrides without it, and must not suggest it.
  message += std::format(
      " {}; set {}=1{} or use --plugin-check=warning to load it anyway).",
      k_plugin_refused_marker,
      env,
      identity ? std::string {} : std::format(", rebuild with -D{}=ON,", env));
  return make(IntegrityVerdict::refuse, std::move(status), std::move(message));
}

}  // namespace

std::optional<bool> parse_env_flag(std::string_view text)
{
  std::string s(text);
  std::ranges::transform(
      s, s.begin(), [](unsigned char c) { return std::tolower(c); });
  if (s == "1" || s == "true" || s == "yes" || s == "on") {
    return true;
  }
  if (s.empty() || s == "0" || s == "false" || s == "no" || s == "off") {
    return false;
  }
  return std::nullopt;
}

bool solver_allow_unpinned_from_env(std::string_view solver)
{
  const auto name = solver_allow_unpinned_env(solver);
  // NOLINTNEXTLINE(concurrency-mt-unsafe)
  const char* v = std::getenv(name.c_str());
  if (v == nullptr) {
    return false;
  }
  const auto flag = parse_env_flag(v);
  if (!flag) {
    SPDLOG_WARN(
        "Ignoring {}='{}': expected 1/true/yes/on or 0/false/no/off", name, v);
  }
  return flag.value_or(false);
}

IntegrityDecision check_solver_version_pin(std::string_view solver,
                                           const SolverVersion& pinned,
                                           const SolverVersion& runtime,
                                           std::string_view library_path,
                                           const VersionPinOverrides& overrides,
                                           const PluginIntegrityPolicy& policy)
{
  if (!pinned.known()) {
    return make(IntegrityVerdict::ok, {}, {});
  }
  const bool verifiable =
      runtime.known() && (pinned.patch < 0 || runtime.patch >= 0);
  if (verifiable && runtime.major == pinned.major
      && runtime.minor == pinned.minor
      && (pinned.patch < 0 || runtime.patch == pinned.patch))
  {
    return make(IntegrityVerdict::ok, "pinned", {});
  }

  const auto where =
      library_path.empty() ? std::string_view {"(unknown path)"} : library_path;
  std::string up(solver);
  std::ranges::transform(
      up, up.begin(), [](unsigned char c) { return std::toupper(c); });
  std::string message = verifiable
      ? std::format(
            "Solver '{}': the loaded library {} is {} {}, but this "
            "gtopt pins {} {} (the release CI builds and the goldens "
            "are generated with; another release can end on another "
            "optimal vertex of a degenerate LP and change duals and "
            "costs).",
            solver,
            where,
            solver,
            runtime.str(),
            solver,
            pinned.str())
      : std::format(
            "Solver '{}': the loaded library {} does not report its "
            "version, so the pin to {} {} cannot be verified.",
            solver,
            where,
            solver,
            pinned.str());
  message += std::format(
      " Fix: rebuild gtopt against a {} {} prefix (-D{}_ROOT_DIR=, e.g. "
      "~/opt/{}-{}) and do not shadow that library through LD_LIBRARY_PATH.",
      solver,
      pinned.str(),
      up,
      solver,
      pinned.str());
  return overridable_finding(solver,
                             verifiable ? "UNPINNED" : "pin unverified",
                             std::move(message),
                             overrides,
                             policy,
                             /*identity=*/false);
}

const LinkedLibraryRecord* match_linked_library(
    const std::vector<LinkedLibraryRecord>& records,
    const LoadedLibraryIdentity& loaded)
{
  if (!loaded.soname.empty()) {
    const auto it =
        std::ranges::find(records, loaded.soname, &LinkedLibraryRecord::soname);
    if (it != records.end()) {
      return &*it;
    }
  }
  const auto name = loaded.path.filename();
  if (name.empty()) {
    return nullptr;
  }
  const auto it = std::ranges::find_if(records,
                                       [&name](const LinkedLibraryRecord& r)
                                       { return r.path.filename() == name; });
  return it != records.end() ? &*it : nullptr;
}

std::string sha256_file(const std::filesystem::path& path)
{
  std::error_code ec;
  const auto size = std::filesystem::file_size(path, ec);
  if (ec) {
    return {};
  }
  std::ifstream in(path, std::ios::binary);
  std::string data(size, '\0');
  if (!in || !in.read(data.data(), static_cast<std::streamsize>(size))) {
    return {};
  }
  return sha256_hex(data);
}

IntegrityDecision check_solver_library_identity(
    std::string_view solver,
    const std::vector<LinkedLibraryRecord>* records,
    const LoadedLibraryIdentity& loaded,
    bool statically_linked,
    const VersionPinOverrides& pin_overrides,
    const PluginIntegrityPolicy& policy)
{
  // A build that accepts an unpinned VERSION (-DGTOPT_<S>_ALLOW_UNPINNED=ON,
  // as install.sh builds) still has to run with the very library it
  // linked: only the env override and the warning mode relax identity.
  const VersionPinOverrides overrides {
      .env_allow_unpinned = pin_overrides.env_allow_unpinned,
  };
  if (statically_linked) {
    return make(IntegrityVerdict::ok, "static", {});
  }
  if (records == nullptr || loaded.path.empty()) {
    return make(IntegrityVerdict::ok, "unchecked", {});
  }
  const auto describe_loaded = [&loaded]
  {
    return std::format(
        "{} (soname {}, {})",
        loaded.path.string(),
        loaded.soname.empty() ? std::string("none") : loaded.soname,
        loaded.build_id.empty() ? std::string("no build-id")
                                : "build-id " + loaded.build_id);
  };
  const auto env_hint = std::format(
      "LD_LIBRARY_PATH={}, LD_PRELOAD={}",
      loaded.ld_library_path.empty() ? "<unset>" : loaded.ld_library_path,
      loaded.ld_preload.empty() ? "<unset>" : loaded.ld_preload);

  const auto* expected = match_linked_library(*records, loaded);
  if (expected == nullptr) {
    std::string linked;
    for (const auto& r : *records) {
      linked += std::format(
          "{}{} ({})", linked.empty() ? "" : ", ", r.path.string(), r.soname);
    }
    return overridable_finding(
        solver,
        "FOREIGN",
        std::format(
            "Solver '{}': the solver code in use comes from {}, which is not "
            "a library the plugin was linked against ({}); a foreign library "
            "is interposing the solver ({}). Fix: remove it from "
            "LD_PRELOAD / LD_LIBRARY_PATH.",
            solver,
            describe_loaded(),
            linked.empty() ? std::string("none recorded") : linked,
            env_hint),
        overrides,
        policy);
  }

  const auto expected_id = !expected->build_id.empty()
      ? "build-id " + expected->build_id
      : "sha256 " + expected->sha256;
  std::string_view loaded_value;
  std::string_view expected_value;
  if (!expected->build_id.empty()) {
    loaded_value = loaded.build_id;
    expected_value = expected->build_id;
  } else if (!expected->sha256.empty()) {
    loaded_value = loaded.sha256;
    expected_value = expected->sha256;
  } else {
    return make(IntegrityVerdict::ok, "unchecked", {});
  }
  const bool full_hash =
      !expected->build_id.empty() && !expected->sha256.empty();
  if (!loaded_value.empty() && loaded_value == expected_value) {
    if (!full_hash) {
      return make(IntegrityVerdict::ok, "verified", {});
    }
    // Full-library hash (GTOPT_SOLVER_LIB_FULL_HASH): the build-id note can
    // be copied into a different file; the content hash cannot.
    if (loaded.sha256 == expected->sha256) {
      return make(IntegrityVerdict::ok, "verified, sha256 verified", {});
    }
    const bool unread = loaded.sha256.empty();
    return overridable_finding(
        solver,
        unread ? "sha256 unverified" : "SHA256 MISMATCH",
        std::format(
            "Solver '{}': the loaded library {} has the build-id of the "
            "library the plugin was built against ({}) but {} (expected "
            "sha256 {}): its content differs -- a modified or forged copy "
            "({}). Fix: restore {}, or rebuild gtopt against the library you "
            "want to use.",
            solver,
            describe_loaded(),
            expected->path.string(),
            unread ? std::string("its content cannot be read")
                   : "sha256 " + loaded.sha256,
            expected->sha256,
            env_hint,
            expected->path.string()),
        overrides,
        policy);
  }
  const bool unverified = loaded_value.empty();
  return overridable_finding(
      solver,
      unverified ? "unverified" : "FOREIGN",
      std::format(
          "Solver '{}': the loaded library {} is not the library the plugin "
          "was built against ({}, {}){}; a foreign or replaced solver library "
          "({}). Fix: remove it from LD_LIBRARY_PATH / LD_PRELOAD, restore "
          "{}, or rebuild gtopt against the library you want to use.",
          solver,
          describe_loaded(),
          expected->path.string(),
          expected_id,
          unverified ? ": its identity cannot be read" : "",
          env_hint,
          expected->path.string()),
      overrides,
      policy);
}

IntegrityDecision check_runpath_shadowing(
    std::string_view solver,
    const std::filesystem::path& loaded_library,
    const std::filesystem::path& runpath_library,
    std::string_view ld_library_path,
    const PluginIntegrityPolicy& policy)
{
  if (runpath_library.empty() || runpath_library == loaded_library) {
    return make(IntegrityVerdict::ok, "ok", {});
  }
  return integrity_finding(
      "shadowed",
      std::format("Solver '{}': loaded {} but the plugin's RUNPATH points to "
                  "{} (LD_LIBRARY_PATH={}).",
                  solver,
                  loaded_library.string(),
                  runpath_library.string(),
                  ld_library_path.empty() ? std::string_view {"<unset>"}
                                          : ld_library_path),
      policy);
}

std::filesystem::path resolve_in_runpath(
    std::string_view soname,
    const std::vector<std::string>& runpath,
    const std::filesystem::path& origin)
{
  if (soname.empty()) {
    return {};
  }
  for (auto dir : runpath) {
    for (const std::string_view token : {"${ORIGIN}", "$ORIGIN"}) {
      for (auto pos = dir.find(token); pos != std::string::npos;
           pos = dir.find(token))
      {
        dir.replace(pos, token.size(), origin.string());
      }
    }
    const auto candidate = std::filesystem::path(dir) / soname;
    std::error_code ec;
    if (std::filesystem::exists(candidate, ec)) {
      auto canon = std::filesystem::canonical(candidate, ec);
      return ec ? candidate : canon;
    }
  }
  return {};
}

std::string describe_solver_library(const SolverLibraryReport& lib)
{
  if (!lib.reported) {
    return "solver library not reported (plugin predates build info)";
  }
  std::string out = std::format("runtime {}, compiled {} ({})",
                                lib.runtime.str(),
                                lib.compiled.str(),
                                lib.version_check.status);
  if (lib.pinned.known()) {
    out += std::format(", pin {} ({})", lib.pinned.str(), lib.pin_check.status);
  }
  if (lib.statically_linked) {
    out += ", linked statically into the plugin";
  } else if (!lib.library_path.empty()) {
    out += std::format(", {}", lib.library_path.string());
  }
  if (!lib.statically_linked) {
    const auto& id = lib.identity_check.status;
    const bool show_id = !id.empty() && id != "unchecked";
    if (!lib.library_build_id.empty()) {
      out += std::format(" [build-id {}{}{}]",
                         short_build_id(lib.library_build_id),
                         show_id ? ", " : "",
                         show_id ? id : std::string {});
    } else if (show_id) {
      out += std::format(" [{}]", id);
    }
  }
  if (!lib.runpath_library.empty()) {
    out += std::format(" -- SHADOWS RUNPATH {}", lib.runpath_library.string());
  }
  return out;
}

}  // namespace gtopt
