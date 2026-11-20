#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#
# Smoke-test a gtopt binary release tarball in a clean directory:
#   - the tree resolves all shared libraries with an empty LD_LIBRARY_PATH,
#   - `gtopt --version` reports the expected version,
#   - `gtopt --solvers` lists HiGHS, CLP and CBC,
#   - the bundled c0 example solves to optimality with HiGHS and CLP,
#     matching the reference objective.
#
# Usage: tools/release/smoke_test.sh <gtopt-X.Y.Z-linux-x86_64.tar.zst> X.Y.Z

set -euo pipefail
ulimit -c unlimited || true

tarball=$(readlink -f "$1")
version=$2
name=$(basename "$tarball")
name=${name%.tar.zst}
name=${name%.tar.gz}

work=$(mktemp -d "${TMPDIR:-/tmp}/gtopt-smoke-XXXXXX")
trap 'rm -rf "$work"' EXIT
cd "$work"
case $tarball in
  *.tar.zst) zstd -dc "$tarball" | tar -xf - ;;
  *.tar.gz) tar -xzf "$tarball" ;;
  *) echo "unsupported archive: $tarball" >&2; exit 1 ;;
esac
root="$work/$name"
gtopt="$root/bin/gtopt"

# A minimal environment: no LD_LIBRARY_PATH, no GTOPT_* overrides.
clean_env=(env -i HOME="$work" PATH=/usr/bin:/bin TMPDIR="$work")

echo "== ldd"
for elf in "$gtopt" "$root"/lib/gtopt/plugins/*.so; do
  if "${clean_env[@]}" ldd "$elf" | grep 'not found'; then
    echo "FAIL: unresolved libraries in $elf" >&2
    exit 1
  fi
done
echo "all libraries resolve"

echo "== gtopt --version"
out=$("${clean_env[@]}" "$gtopt" --version)
echo "$out"
grep -q "$version" <<<"$out" || { echo "FAIL: version $version not reported" >&2; exit 1; }

echo "== gtopt --solvers"
out=$("${clean_env[@]}" "$gtopt" --solvers 2>&1)
echo "$out"
for s in highs clp cbc; do
  grep -qiw "$s" <<<"$out" || { echo "FAIL: solver $s not listed" >&2; exit 1; }
done
for s in cplex gurobi mindopt cuopt; do
  if compgen -G "$root/lib/gtopt/plugins/*$s*" >/dev/null; then
    echo "FAIL: commercial plugin $s packaged" >&2
    exit 1
  fi
done

expected=23163.424133184093
for solver in highs clp; do
  echo "== solve c0 with $solver"
  rm -rf "$work/c0"
  cp -r "$root/share/gtopt/examples/c0" "$work/c0"
  (cd "$work/c0" &&
    "${clean_env[@]}" "$gtopt" system_c0.json --solver "$solver" \
      --set output_directory=out)
  cat "$work/c0/out/solution.csv"
  python3 - "$work/c0/out/solution.csv" "$expected" <<'EOF'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
assert rows and rows[0]["status_name"] == "optimal", rows
obj, ref = float(rows[0]["obj_value"]), float(sys.argv[2])
assert abs(obj - ref) <= 1e-6 * abs(ref), (obj, ref)
print(f"objective {obj} matches reference {ref}")
EOF
done
echo "SMOKE TEST PASSED"
