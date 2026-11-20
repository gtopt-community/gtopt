# Releasing GTOpt

This page is for maintainers.  A release is a `vX.Y.Z` tag on `master`
of [gtopt-community/gtopt](https://github.com/gtopt-community/gtopt).
Pushing the tag runs [`.github/workflows/release.yml`](../.github/workflows/release.yml),
which builds the binaries and creates a **draft** GitHub Release.  A
maintainer reviews the draft and publishes it.

Versions follow [Semantic Versioning](https://semver.org/): bump the
patch number for fixes only, the minor number for new features that keep
existing inputs working, and the major number when inputs or outputs
change incompatibly.

## What a release contains

| Asset | Contents |
|---|---|
| `gtopt-X.Y.Z-linux-x86_64.tar.zst`, `.tar.gz` | `bin/gtopt`, the HiGHS and COIN-OR CBC/CLP plugins in `lib/gtopt/plugins/`, all non-system shared libraries in `lib/` (Arrow/Parquet, HiGHS, COIN-OR, spdlog, jemalloc, ...), `share/gtopt/` data and the `c0` example, licence files and `README-binary.txt` |
| `gtopt-X.Y.Z-linux-x86_64-debug.tar.zst` | Split debug info (line tables) for `bin/gtopt` and the plugins |
| `gtopt_scripts-X.Y.Z-py3-none-any.whl`, `gtopt_scripts-X.Y.Z.tar.gz` | The Python tools from `scripts/` |
| `SHA256SUMS` | Checksums of all of the above |

The binary is built on Ubuntu 24.04 with GCC 14 at `-O2`.  It needs
glibc 2.39 or newer, the GCC 14 C++ runtime and OpenSSL 3 (not bundled, so
it keeps receiving security updates), so it runs on Ubuntu
24.04+ and Debian 13+.  The tree is relocatable: every RUNPATH is
`$ORIGIN`-relative.  Commercial solver plugins (CPLEX, Gurobi, MindOpt)
and cuOpt/SCIP are never packaged.  The packaging script removes any
plugin that is not on its allowlist.

## Steps

### 1. Bump the version

On a branch off `master`, set the new version in:

- `CMakeLists.txt`: `project(gtopt VERSION X.Y.Z ...)`.  This sets
  `GTOPT_VERSION`, which `gtopt --version` prints.
- `scripts/pyproject.toml`: `version = "X.Y.Z"`.  The Python tools print
  this version with `--version`.
- `guiservice/pyproject.toml` and `webservice/package.json` (plus the
  two top-level `version` fields in `webservice/package-lock.json`),
  which track the product version.
- `CITATION.cff`: `version` and `date-released`.

The release workflow fails if the tag, `CMakeLists.txt` and
`scripts/pyproject.toml` do not agree.

### 2. Update the changelog

In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`.
Add a new empty `## [Unreleased]` above it.  At the bottom, update the
compare links:

```markdown
[Unreleased]: https://github.com/gtopt-community/gtopt/compare/vX.Y.Z...HEAD
[X.Y.Z]: https://github.com/gtopt-community/gtopt/compare/vPREVIOUS...vX.Y.Z
```

The first release, 1.1.0, links to its tag instead of a compare range.
The body of the `## [X.Y.Z]` section becomes the release notes.

### 3. Dry run (optional, recommended)

Push the branch, then run the workflow by hand.  This builds, smoke-tests
and uploads everything as a workflow artifact
(`release-dry-run-vX.Y.Z`).  Nothing is published.

```bash
gh workflow run release.yml --repo gtopt-community/gtopt \
  --ref <branch> -f tag=vX.Y.Z
gh run watch --repo gtopt-community/gtopt
```

To reproduce the packaging locally, build `standalone/` with the
configure flags from the workflow's `configure` step, then run:

```bash
tools/release/package_linux.sh --build-dir <build> --version X.Y.Z --out-dir dist
tools/release/smoke_test.sh dist/gtopt-X.Y.Z-linux-x86_64.tar.zst X.Y.Z
```

The packaging script needs `patchelf`.

### 4. Merge and tag

Merge the branch into `master`.  Then create an annotated tag on the merge
commit and push it:

```bash
git switch master && git pull
git tag -a vX.Y.Z -m "GTOpt X.Y.Z"
git push origin vX.Y.Z
```

### 5. CI builds the draft

The tag push runs `release.yml`:

1. `prepare` checks that the tag, `CMakeLists.txt`,
   `scripts/pyproject.toml` and the `CHANGELOG.md` section agree.
2. `linux-x86_64` builds HiGHS (cached) and gtopt (Release, `-O2 -g1`),
   then runs `tools/release/package_linux.sh`.  That script installs
   gtopt, bundles the shared libraries, splits the debug info, strips the
   binaries, rewrites the RUNPATHs and builds the archives.  Next,
   `tools/release/smoke_test.sh` unpacks each archive into a clean
   directory and runs `gtopt --version` and `gtopt --solvers`.  It also
   solves `c0` with HiGHS and CLP and checks the objective.
3. `python` builds the sdist and wheel from `scripts/` and checks that
   the wheel installs.
4. `release` writes `SHA256SUMS` and extracts the release notes from
   `CHANGELOG.md`.  It then runs `gh release create --draft` with all the
   assets.  This is the only job with `contents: write`.

### 6. Review and publish

On the GitHub Releases page, open the draft and check:

- the notes, which you can edit in place;
- the asset list;
- one archive, by downloading it and running `README-binary.txt`'s
  quick start on a clean Ubuntu 24.04 machine or container.

Then click **Publish release**.  To withdraw a broken draft, delete it
and the tag (`git push origin :refs/tags/vX.Y.Z`), fix the problem on
`master`, and tag again.  Never move a tag after its release has been
published.  Release a new patch version instead.
