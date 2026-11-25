/**
 * @file      linear_interface_labels.cpp
 * @brief     LinearInterface label synthesis, compression, and write_lp.
 * @date      2026-04-26
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Extracted from `linear_interface.cpp` (the "Names & LP file output"
 * section).  Contains the on-demand label generator
 * (`generate_labels_from_maps`), its caching driver
 * (`materialize_labels`), the low-memory label-meta compress/decompress
 * pair, the duplicate-detection index rebuild, and the `write_lp`
 * entry point.
 */

#include <cerrno>
#include <expected>
#include <filesystem>
#include <format>
#include <fstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#include <gtopt/error.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_interface_labels_codec.hpp>
#include <gtopt/lp_file_format.hpp>
#include <gtopt/lp_name_spill.hpp>
#include <gtopt/memory_compress.hpp>
#include <gtopt/utils.hpp>
#include <spdlog/spdlog.h>

namespace gtopt
{

void LinearInterface::generate_labels_from_maps(
    std::vector<std::string>& col_names,
    std::vector<std::string>& row_names) const
{
  // Lazy-lazy decompression: if `release_backend` stashed the
  // metadata into compressed buffers, rehydrate it now on first
  // read.  No-op in modes that never compressed.
  ensure_labels_meta_decompressed();

  // Spill-store reload: when names were kept under low-memory and the
  // structural label metadata was spilled to the async store + dropped from
  // the snapshot (so `load_flat` left the live vectors empty), repopulate them
  // now from the store (cached after the first hit, so the repeated
  // forward/backward/aperture error-LP dumps for one cell touch disk once).
  // No-op when no store is attached — the live vectors are authoritative.
  // Runs BEFORE the loops so the structural labels resolve to real names.
  // (`col_label_at` indexes post-flatten metadata at
  // `i - flatten_col_count()`, the structural size installed by `load_flat`,
  // so the boundary no longer depends on whether this reload happened.)
  if (m_labels_.name_store != nullptr && !m_labels_.spill_key.empty()
      && m_labels_.col_labels_meta->empty()
      && m_labels_.row_labels_meta->empty())
  {
    if (const LpLabelMeta* meta =
            m_labels_.name_store->load(m_labels_.spill_key))
    {
      m_labels_.col_labels_meta =
          std::make_shared<const std::vector<SparseColLabel>>(meta->col_labels);
      m_labels_.row_labels_meta =
          std::make_shared<const std::vector<SparseRowLabel>>(meta->row_labels);
    }
  }

  // Forced-all LabelMaker: `m_label_maker_` is whatever flatten
  // captured (typically `none`).  Here we want real labels always,
  // synthesised on demand from metadata.
  const LabelMaker writer_labels {LpNamesLevel::all};

  const auto ncols = static_cast<size_t>(m_backend_->get_num_cols());
  col_names.assign(ncols, std::string {});

  // Keep the formatted-name cache sized with the live LP so
  // subsequent `generate_labels_from_maps` calls (after `add_col`
  // added new entries) hit the cache for already-formatted cols and
  // only format the tail.
  auto& cin = detach_for_write(m_labels_.col_index_to_name);
  if (cin.size() < ncols) {
    cin.resize(ncols);
  }

  for (size_t i = 0; i < ncols; ++i) {
    const ColIndex ci {i};

    // Cache hit: label was populated by a legacy `add_col(string, …)`
    // overload (user-supplied name) or by a prior call to this method.
    if (!cin[ci].empty()) {
      col_names[i] = cin[ci];
      continue;
    }

    // Cache miss: synthesise from metadata and cache the result so
    // subsequent calls (repeat `write_lp`) avoid re-formatting.
    //
    // Three-tier lookup:
    //   1. Frozen flatten-side `m_labels_.col_labels_meta` (load_flat).
    //   2. Per-instance `m_labels_.post_flatten_col_labels_meta`        (cuts,
    //      slacks, alpha, cascade elastic — every post-`load_flat`
    //      add_col tracked here by `track_col_label_meta`).
    //   3. Per-clone-local `m_post_clone_col_metas_`             (only
    //      populated by `add_col_disposable` on a shallow clone — the
    //      elastic-filter slack / fixing-row inserts).
    SparseColLabel meta;
    const auto* col_meta = col_label_at(ColIndex {i});
    if (col_meta != nullptr) {
      meta = *col_meta;
    } else {
      // Linear scan over per-clone extras (≤ 20 entries in practice).
      const auto target = ColIndex {i};
      auto it = std::ranges::find(m_post_clone_col_metas_,
                                  target,
                                  &std::pair<ColIndex, SparseColLabel>::first);
      if (it == m_post_clone_col_metas_.end()) {
        // Labels intentionally dropped (low-memory build without name
        // output): the entire frozen label vector is empty.  Emit a generic
        // positional name instead of throwing — only `write_lp` / dumps
        // consume these, and a generic name is acceptable there.
        if (!m_labels_.col_labels_meta || m_labels_.col_labels_meta->empty()) {
          cin[ci] = std::format("c{}", i);
          col_names[i] = cin[ci];
          continue;
        }
        throw std::logic_error(std::format(
            "LinearInterface::generate_labels_from_maps: col {} has no "
            "entry in m_labels_.col_labels_meta (frozen size {}) / "
            "m_labels_.post_flatten_col_labels_meta (size {}) or "
            "m_post_clone_col_metas_ (size {}) — col was added without "
            "metadata tracking.",
            i,
            flatten_col_count(),
            m_labels_.post_flatten_col_labels_meta.size(),
            m_post_clone_col_metas_.size()));
      }
      meta = it->second;
    }
    SparseCol view {};
    view.class_name = meta.class_name;
    view.variable_name = meta.variable_name;
    view.variable_uid = meta.variable_uid;
    view.context = meta.context;
    auto label = writer_labels.make_col_label(view);
    if (label.empty()) {
      spdlog::error(
          "LinearInterface::generate_labels_from_maps: col {} metadata: "
          "class_name='{}' variable_name='{}' variable_uid={}",
          i,
          meta.class_name,
          meta.variable_name,
          meta.variable_uid);
      throw std::logic_error(
          std::format("LinearInterface::generate_labels_from_maps: col {} has "
                      "metadata without a class_name (unlabelable).",
                      i));
    }
    cin[ci] = label;  // cache in index→name
    auto& cn = detach_for_write(m_labels_.col_names);
    if (auto [it, inserted] = cn.try_emplace(label, ci); !inserted) {
      throw std::runtime_error(std::format(
          "LinearInterface: duplicate col metadata — label '{}' synthesised "
          "by col {} was already emitted by col {}.  Two cols share "
          "(class_name, variable_name, uid, context).",
          label,
          i,
          it->second));
    }
    col_names[i] = std::move(label);
  }

  const auto nrows = static_cast<size_t>(m_backend_->get_num_rows());
  row_names.assign(nrows, std::string {});
  auto& rin = detach_for_write(m_labels_.row_index_to_name);
  if (rin.size() < nrows) {
    rin.resize(nrows);
  }

  for (size_t i = 0; i < nrows; ++i) {
    const RowIndex ri {i};
    if (!rin[ri].empty()) {
      row_names[i] = rin[ri];
      continue;
    }
    // Three-tier lookup (same shape as the col side above):
    //   1. Frozen flatten-side `m_labels_.row_labels_meta`.
    //   2. Per-instance `m_labels_.post_flatten_row_labels_meta` (cut rows,
    //      cascade elastic constraints, …).
    //   3. Per-clone-local `m_post_clone_row_metas_` (disposable inserts).
    SparseRowLabel meta;
    const auto* row_meta = row_label_at(RowIndex {i});
    if (row_meta != nullptr) {
      meta = *row_meta;
    } else {
      const auto target = RowIndex {i};
      auto it = std::ranges::find(m_post_clone_row_metas_,
                                  target,
                                  &std::pair<RowIndex, SparseRowLabel>::first);
      if (it == m_post_clone_row_metas_.end()) {
        // Labels intentionally dropped (low-memory build without name
        // output) — generic positional name rather than throw (see col side).
        if (!m_labels_.row_labels_meta || m_labels_.row_labels_meta->empty()) {
          rin[ri] = std::format("r{}", i);
          row_names[i] = rin[ri];
          continue;
        }
        throw std::logic_error(std::format(
            "LinearInterface::generate_labels_from_maps: row {} has no "
            "entry in m_labels_.row_labels_meta (frozen size {}) / "
            "m_labels_.post_flatten_row_labels_meta (size {}) or "
            "m_post_clone_row_metas_ (size {}) — row was added without "
            "metadata tracking.",
            i,
            flatten_row_count(),
            m_labels_.post_flatten_row_labels_meta.size(),
            m_post_clone_row_metas_.size()));
      }
      meta = it->second;
    }
    SparseRow view {};
    view.class_name = meta.class_name;
    view.constraint_name = meta.constraint_name;
    view.variable_uid = meta.variable_uid;
    view.context = meta.context;
    auto label = writer_labels.make_row_label(view);
    if (label.empty()) {
      throw std::logic_error(
          std::format("LinearInterface::generate_labels_from_maps: row {} has "
                      "metadata without a class_name (unlabelable).",
                      i));
    }
    rin[ri] = label;  // cache in index→name
    auto& rn = detach_for_write(m_labels_.row_names);
    if (auto [it, inserted] = rn.try_emplace(label, ri); !inserted) {
      throw std::runtime_error(std::format(
          "LinearInterface: duplicate row metadata — label '{}' synthesised "
          "by row {} was already emitted by row {}.  Two rows share "
          "(class_name, constraint_name, uid, context).",
          label,
          i,
          it->second));
    }
    row_names[i] = std::move(label);
  }
}

void LinearInterface::materialize_labels() const
{
  // Trigger `generate_labels_from_maps` so caches
  // (`m_labels_.col_index_to_name` / `m_labels_.row_index_to_name`) and
  // name→index maps (`m_labels_.col_names` / `m_labels_.row_names`) are
  // populated.  Discards the returned vectors — callers that need them directly
  // should call `generate_labels_from_maps` themselves.
  std::vector<std::string> col_names;
  std::vector<std::string> row_names;
  generate_labels_from_maps(col_names, row_names);
}

void LinearInterface::compress_labels_meta_if_needed()
{
  if (m_low_memory_mode_ == LowMemoryMode::off) {
    return;
  }
  // Drop the frozen flatten-side label metadata vectors to free
  // memory between release_backend and the next load_flat.  They are
  // repopulated by load_flat from the snapshot's `flat_lp.col_labels_meta` /
  // `row_labels_meta` — OR, when the metadata was spilled to the async
  // `LpNameSpillStore` and dropped from the snapshot (names + low-memory),
  // by the store reload at the top of `generate_labels_from_maps` (the only
  // write_lp consumer).  Either way the canonical copy lives elsewhere
  // (snapshot or store), so there is no need to serialise the labels into a
  // separate compressed buffer here.
  //
  // Post-flatten vectors (cuts, alpha, cascade elastic) stay
  // populated: they are tiny and survive across cycles in
  // m_post_flatten_*_meta_index_, which is per-instance and not
  // reseeded by load_flat.
  //
  // Drop this LI's reference only: the vectors may be aliased with the
  // snapshot's `FlatLpMeta` or shared with clones, which keep theirs.
  if (!m_labels_.col_labels_meta->empty()) {
    m_labels_.col_labels_meta =
        std::make_shared<const std::vector<SparseColLabel>>();
  }
  if (!m_labels_.row_labels_meta->empty()) {
    m_labels_.row_labels_meta =
        std::make_shared<const std::vector<SparseRowLabel>>();
  }
}

void LinearInterface::ensure_labels_meta_decompressed() const
{
  // No-op now that compress_labels_meta_if_needed() no longer
  // serialises into m_labels_.col_labels_meta_compressed / m_row_labels_
  // meta_compressed_.  The live vectors are repopulated directly by
  // load_flat from the snapshot, which is the only path that
  // produces a loaded backend — and write_lp (the only consumer of
  // generate_labels_from_maps) requires a loaded backend.  Kept as
  // a deliberate no-op to preserve the call site in
  // generate_labels_from_maps in case a future change reintroduces
  // a compressed-buffer fallback for some reason.
}

void LinearInterface::push_names_to_solver() const
{
  std::vector<std::string> col_names;
  std::vector<std::string> row_names;
  generate_labels_from_maps(col_names, row_names);
  m_backend_->push_names(col_names, row_names);
}

namespace
{

/// Dispatch to the backend writer for `format`.  Never throws: a native
/// writer that throws (CoinUtils throws `CoinError`, which is not a
/// `std::exception`, when it cannot open the file) counts as a failed
/// write.  On failure, a partial file left behind (disk full, I/O error)
/// is removed so that no truncated model is mistaken for a good one.
[[nodiscard]] bool backend_write(SolverBackend& backend,
                                 const std::string& stem,
                                 LpFileFormat format)
{
  const auto path = lp_file_path(stem, format);
  // Open the target ourselves first.  Native writers handle an fopen
  // failure badly (MindOpt 2.3's MDOwrite double-frees and aborts the
  // process), so a path that cannot be created never reaches them.
  {
    const std::ofstream probe(path, std::ios::out | std::ios::trunc);
    if (!probe.is_open()) {
      spdlog::warn("cannot create {}: {}",
                   path,
                   std::error_code(errno, std::generic_category()).message());
      return false;
    }
  }
  bool ok = false;
  try {
    ok = backend.write_lp(stem.c_str(), format);
  } catch (const std::exception& e) {
    spdlog::warn("writing {} failed: {}", path, e.what());
  } catch (...) {
    spdlog::warn("writing {} failed with an unknown exception", path);
  }
  std::error_code ec;
  if (ok) {
    // Trust the file, not the return code: some native writers report
    // success without checking fopen/fwrite.  A missing or empty file is
    // never a valid model.
    const auto size = std::filesystem::file_size(path, ec);
    ok = !ec && size > 0;
  }
  if (!ok) {
    std::filesystem::remove(path, ec);
  }
  return ok;
}

}  // namespace

auto LinearInterface::write_lp(const std::string& filename,
                               LpFileFormat format,
                               LpFormatFallback fallback) const
    -> std::expected<std::string, Error>
{
  if (filename.empty()) {
    return std::string {};
  }

  // Names may be missing entirely (flatten ran without
  // `LpMatrixOptions::{col,row}_with_names`) or populated only for a
  // prefix of cols/rows.  `push_names_to_solver` fills the gaps with
  // generic `c<index>` / `r<index>` labels so the backend always
  // receives a fully-named LP.  Real gtopt names (e.g.
  // `Bus.theta.s0.p0.b0`) still require names enabled at flatten
  // time — run with `--lp-debug` or the equivalent
  // `LpMatrixOptions{col_with_names, row_with_names} = true` for
  // those.  The generic fallback guarantees `write_lp` never fails
  // on a well-formed backend, which is a hard requirement for the
  // SDDP error-LP dump path.
  //
  // Label generation throws on malformed metadata (a column without a
  // class name); a model file is a by-product, so report it as an error
  // instead of letting the exception escape (the SDDP / lp_debug dump
  // callers do not catch).
  try {
    push_names_to_solver();
  } catch (const std::exception& e) {
    drop_formatted_label_caches();
    return std::unexpected(Error {
        .code = ErrorCode::SolverError,
        .message =
            std::format("cannot write '{}': names for the model file: {}",
                        filename,
                        e.what()),
    });
  }

  auto written = format;
  bool ok = backend_write(*m_backend_, filename, format);
  if (!ok && fallback == LpFormatFallback::other_format) {
    written = other_lp_file_format(format);
    ok = backend_write(*m_backend_, filename, written);
    if (ok) {
      spdlog::warn(
          "solver backend '{}' could not write '{}{}'; wrote '{}{}' instead",
          solver_name(),
          filename,
          lp_file_extension(format),
          filename,
          lp_file_extension(written));
    }
  }

  // Drop the formatted-label caches now that write_lp has consumed
  // them.  Memoization is unhelpful here: in production SDDP
  // write_lp runs at most a few times per cell (debug dumps /
  // error-LP capture), and on long runs the caches accumulate ~50k
  // strings × ncells of dead weight.  Next write_lp regenerates
  // from the still-compressed label metadata — ~milliseconds per
  // cell, paid only when actually requested.
  drop_formatted_label_caches();

  if (!ok) {
    return std::unexpected(Error {
        .code = ErrorCode::SolverError,
        .message = std::format(
            "cannot write '{}{}': solver backend '{}' does not support the "
            "'{}' format or the file could not be created (check that the "
            "directory exists and is writable){}",
            filename,
            lp_file_extension(format),
            solver_name(),
            lp_file_format_name(format),
            fallback == LpFormatFallback::strict
                ? std::format("; try --lp-format {}",
                              lp_file_format_name(other_lp_file_format(format)))
                : std::string {}),
    });
  }
  return lp_file_path(filename, written);
}

}  // namespace gtopt
