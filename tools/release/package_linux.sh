#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# Package a configured + built gtopt standalone tree into a relocatable
# binary release:
#
#   gtopt-<version>-linux-x86_64/
#     bin/gtopt
#     lib/libspdlog.so.*, lib/<bundled shared libs>
#     lib/gtopt/plugins/libgtopt_solver_{osi,highs}.so
#     share/gtopt/{naming,unit}_dialects.json
#     share/gtopt/examples/c0/
#     share/doc/gtopt/third_party/<lib>.copyright
#     LICENSE* LICENSING.md THIRD_PARTY_NOTICES.md ... README-binary.txt
#
# Every shared library the binary or the open solver plugins need, except
# the glibc core, the GCC runtime (libstdc++ / libgcc_s) and OpenSSL 3,
# which every supported distribution ships, is copied into lib/ and given an
# `$ORIGIN` RUNPATH, so the tarball runs without Apache Arrow, HiGHS or
# COIN-OR installed.  Commercial solver plugins are never packaged.
#
# Debug info is split into a separate tarball
# (gtopt-<version>-linux-x86_64-debug.tar.zst) and linked back with
# .gnu_debuglink.
#
# Usage:
#   tools/release/package_linux.sh --build-dir BUILD --version 1.1.0 \
#       --out-dir dist [--highs-license /path/to/HiGHS/LICENSE.txt]
#
# Requires: cmake, patchelf, objcopy/strip (binutils), ldd, tar, zstd,
# gzip; dpkg (optional, for third-party copyright files).

# $ORIGIN in single quotes is literal on purpose (expanded by ld.so).
# shellcheck disable=SC2016

set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '==> %s\n' "$*"; }

build_dir="" version="" out_dir="" highs_license=""
while (($#)); do
  case $1 in
    --build-dir) build_dir=$2; shift 2 ;;
    --version) version=$2; shift 2 ;;
    --out-dir) out_dir=$2; shift 2 ;;
    --highs-license) highs_license=$2; shift 2 ;;
    *) die "unknown option: $1" ;;
  esac
done
[[ -n $build_dir && -n $version && -n $out_dir ]] ||
  die "usage: $0 --build-dir DIR --version X.Y.Z --out-dir DIR [--highs-license FILE]"

for tool in cmake patchelf objcopy strip ldd tar zstd gzip; do
  command -v "$tool" >/dev/null || die "missing tool: $tool"
done

src_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(cd "$build_dir" && pwd)
mkdir -p "$out_dir"
out_dir=$(cd "$out_dir" && pwd)

name="gtopt-${version}-linux-x86_64"
work=$(mktemp -d "${TMPDIR:-/tmp}/gtopt-pkg-XXXXXX")
trap 'rm -rf "$work"' EXIT
stage="$work/$name"
dbg="$work/${name}-debug"

# Open-source plugins allowed in the binary release.  Anything else that
# the build happened to produce (CPLEX, Gurobi, MindOpt, cuOpt, SCIP) is
# dropped: commercial solvers are never redistributed.
allowed_plugins=(libgtopt_solver_osi.so libgtopt_solver_highs.so)

# Libraries every target system provides; never bundled.  OpenSSL
# (libssl3/libssl3t64, pulled in by Arrow) stays a system dependency so it
# keeps receiving distribution security updates.
system_lib_re='^(linux-vdso|ld-linux|libc|libm|libmvec|libdl|libpthread|librt|libresolv|libutil|libstdc\+\+|libgcc_s|libssl|libcrypto)\.so'

say "Installing into staging tree"
cmake --install "$build_dir" --prefix "$stage" >/dev/null

[[ -x $stage/bin/gtopt ]] || die "cmake --install did not produce bin/gtopt"

# Drop anything a release does not need: headers, CMake package files,
# static libs, and non-allowlisted plugins.
rm -rf "${stage:?}/include" "${stage:?}/lib/cmake" "${stage:?}/lib/pkgconfig" \
  "${stage:?}/share/cmake" "${stage:?}/lib64"
find "$stage" -name '*.a' -delete
shopt -s nullglob
for plugin in "$stage"/lib/gtopt/plugins/*.so; do
  base=$(basename "$plugin")
  keep=0
  for ok in "${allowed_plugins[@]}"; do [[ $base == "$ok" ]] && keep=1; done
  if ((keep == 0)); then
    say "Removing non-redistributable plugin $base"
    rm -f "$plugin"
  fi
done
for ok in "${allowed_plugins[@]}"; do
  [[ -f $stage/lib/gtopt/plugins/$ok ]] || die "plugin $ok was not built"
done

say "Bundling shared library dependencies"
mkdir -p "$stage/share/doc/gtopt/third_party"
declare -A seen_pkg=()
bundle_from() {
  local elf=$1 line soname path
  while IFS= read -r line; do
    # "  libfoo.so.1 => /path/libfoo.so.1 (0x...)"
    [[ $line == *"=>"* ]] || continue
    soname=$(awk '{print $1}' <<<"$line")
    path=$(awk '{print $3}' <<<"$line")
    [[ $path == "not" ]] && die "$elf: unresolved dependency $soname"
    [[ $soname =~ $system_lib_re ]] && continue
    [[ -e $stage/lib/$soname ]] && continue
    cp -L "$path" "$stage/lib/$soname"
    chmod 0644 "$stage/lib/$soname"
    printf '    %-34s <- %s\n' "$soname" "$path"
    # Ship the Debian copyright file of the package that owns the lib.
    if command -v dpkg >/dev/null; then
      local pkg
      pkg=$(dpkg -S "$(readlink -f "$path")" 2>/dev/null | head -1 | cut -d: -f1) || true
      if [[ -z $pkg ]]; then
        pkg=$(dpkg -S "$path" 2>/dev/null | head -1 | cut -d: -f1) || true
      fi
      if [[ -n $pkg && -z ${seen_pkg[$pkg]:-} && -r /usr/share/doc/$pkg/copyright ]]; then
        cp "/usr/share/doc/$pkg/copyright" \
          "$stage/share/doc/gtopt/third_party/$pkg.copyright"
        seen_pkg[$pkg]=1
      fi
    fi
  done < <(LD_LIBRARY_PATH="$stage/lib" ldd "$elf")
}
# ldd lists the full transitive closure, so one pass per root suffices.
bundle_from "$stage/bin/gtopt"
for plugin in "$stage"/lib/gtopt/plugins/*.so; do
  bundle_from "$plugin"
done

# Licences of libraries that do not come from a Debian package.
if [[ -n $highs_license && -r $highs_license ]]; then
  cp "$highs_license" "$stage/share/doc/gtopt/third_party/HiGHS.LICENSE.txt"
fi
spdlog_src=$(sed -n 's/^spdlog_SOURCE_DIR:[A-Z]*=//p' "$build_dir/CMakeCache.txt")
if [[ -n $spdlog_src && -r $spdlog_src/LICENSE ]]; then
  cp "$spdlog_src/LICENSE" "$stage/share/doc/gtopt/third_party/spdlog.LICENSE"
else
  die "spdlog LICENSE not found (spdlog_SOURCE_DIR='$spdlog_src')"
fi

say "Splitting debug info and stripping"
mkdir -p "$dbg"
split_debug() {
  local elf=$1 rel dfile
  rel=${elf#"$stage"/}
  dfile="$dbg/$rel.debug"
  mkdir -p "$(dirname "$dfile")"
  objcopy --only-keep-debug --compress-debug-sections "$elf" "$dfile"
  if [[ $elf == */bin/* ]]; then
    strip --strip-all "$elf"
  else
    strip --strip-unneeded "$elf"
  fi
  objcopy --add-gnu-debuglink="$dfile" "$elf"
}
split_debug "$stage/bin/gtopt"
for plugin in "$stage"/lib/gtopt/plugins/*.so; do split_debug "$plugin"; done
# Bundled third-party libs: strip only (their debug info is in the distro
# -dbgsym packages).
for lib in "$stage"/lib/*.so*; do
  [[ -L $lib ]] || strip --strip-unneeded "$lib" 2>/dev/null || true
done

# Clean RUNPATHs (after stripping: patchelf output must not be re-stripped).
# This drops the absolute build-host directories that
# configure_install_rpath() recorded.
say "Rewriting RUNPATHs to \$ORIGIN-relative"
patchelf --set-rpath '$ORIGIN/../lib' "$stage/bin/gtopt"
for plugin in "$stage"/lib/gtopt/plugins/*.so; do
  patchelf --set-rpath '$ORIGIN:$ORIGIN/../..' "$plugin"
done
for lib in "$stage"/lib/*.so*; do
  [[ -L $lib ]] || patchelf --set-rpath '$ORIGIN' "$lib"
done

say "Verifying the staged tree resolves without the build host"
for elf in "$stage/bin/gtopt" "$stage"/lib/gtopt/plugins/*.so; do
  if env -u LD_LIBRARY_PATH ldd "$elf" | grep -q 'not found'; then
    env -u LD_LIBRARY_PATH ldd "$elf" | grep 'not found' >&2
    die "$(basename "$elf") has unresolved libraries"
  fi
done

say "Adding licences, docs and the c0 example"
for f in LICENSE LICENSE-APACHE-2.0 LICENSE-EXCEPTION.md LICENSE-COMMERCIAL.md \
  LICENSING.md THIRD_PARTY_NOTICES.md TRADEMARKS.md CHANGELOG.md README.md \
  CITATION.cff; do
  [[ -f $src_dir/$f ]] && cp "$src_dir/$f" "$stage/"
done
mkdir -p "$stage/share/gtopt/examples"
cp -r "$src_dir/cases/c0" "$stage/share/gtopt/examples/c0"
rm -rf "$stage/share/gtopt/examples/c0/output"

glibc=$(objdump -T "$stage/bin/gtopt" "$stage"/lib/*.so* \
  "$stage"/lib/gtopt/plugins/*.so 2>/dev/null |
  grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)
glibcxx=$(objdump -T "$stage/bin/gtopt" "$stage"/lib/*.so* \
  "$stage"/lib/gtopt/plugins/*.so 2>/dev/null |
  grep -o 'GLIBCXX_[0-9.]*' | sort -uV | tail -1)
bundled=$(find "$stage/lib" -maxdepth 1 -name '*.so*' -printf '  %f\n' | sort)
sed -e "s|@VERSION@|$version|g" -e "s|@GLIBC@|${glibc#GLIBC_}|g" \
  -e "s|@GLIBCXX@|${glibcxx#GLIBCXX_}|g" \
  -e "s|@GIT_REVISION@|$(git -C "$src_dir" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)|g" \
  "$src_dir/tools/release/README-binary.txt.in" >"$stage/README-binary.txt"
{
  echo
  echo "Bundled shared libraries (lib/):"
  echo "$bundled"
} >>"$stage/README-binary.txt"

say "Creating archives"
(
  cd "$work"
  tar --owner=0 --group=0 --numeric-owner --sort=name -cf "$work/$name.tar" "$name"
  zstd -q -19 -T0 -f "$work/$name.tar" -o "$out_dir/$name.tar.zst"
  gzip -9 -n -c "$work/$name.tar" >"$out_dir/$name.tar.gz"
  tar --owner=0 --group=0 --numeric-owner --sort=name -cf - "${name}-debug" |
    zstd -q -19 -T0 -f -o "$out_dir/${name}-debug.tar.zst"
)

say "Done: GLIBC requirement ${glibc}, ${glibcxx}"
ls -l "$out_dir"/"$name"*
