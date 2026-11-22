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

## Versions between releases

`master` carries the **next** version with a development marker as soon
as a release is out.  After `vX.Y.Z` it is:

| File | Value |
|---|---|
| `CMakeLists.txt` | `project(gtopt VERSION X.(Y+1).0 ...)` and `set(GTOPT_VERSION_SUFFIX "-dev")` |
| `scripts/pyproject.toml` | `version = "X.(Y+1).0.dev0"` (PEP 440) |

CMake's `project(VERSION)` accepts only numbers, so the marker lives in
the separate `GTOPT_VERSION_SUFFIX` variable; `GTOPT_VERSION` (and
`gtopt --version`) is the number followed by the suffix.  The release
workflow opens the PR that makes this change (see step 7).

Every build also embeds the `git describe --always --dirty` of its
source tree.  `gtopt --version`, the `--help` header and the
`starting gtopt` log line print both, for example
`1.2.0-dev (v1.1.0-23-g0123456789ab)`.  Only a clean build of exactly
the release tag prints the bare version, `1.1.0`.  A build from a
source tree without git (e.g. a tarball) prints the version alone.  The
same version and revision go into each run's `solution.csv` and
`solver_status.json` as `gtopt_version` / `gtopt_revision`.

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

- `CMakeLists.txt`: `project(gtopt VERSION X.Y.Z ...)`, and clear the
  development marker: `set(GTOPT_VERSION_SUFFIX "")`.  Together they set
  `GTOPT_VERSION`, which `gtopt --version` prints.
- `scripts/pyproject.toml`: `version = "X.Y.Z"` (drop the `.devN`).  The
  Python tools print this version with `--version`.
- `guiservice/pyproject.toml` and `webservice/package.json` (plus the
  two top-level `version` fields in `webservice/package-lock.json`),
  which track the product version.
- `CITATION.cff`: `version` and `date-released`.

The release workflow fails if the tag, `CMakeLists.txt` and
`scripts/pyproject.toml` do not agree, or if `GTOPT_VERSION_SUFFIX` is
not empty.

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

Push a branch named `release/vX.Y.Z`: the push itself starts a dry run
labelled `vX.Y.Z`.  Any other branch can be dry-run by hand once the
workflow is on the default branch.  Either way the workflow builds,
smoke-tests and uploads everything as a workflow artifact
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
   `scripts/pyproject.toml` and the `CHANGELOG.md` section agree, and that
   `GTOPT_VERSION_SUFFIX` is empty.
2. `linux-x86_64` builds HiGHS (cached) and gtopt (Release, `-O2 -g1`),
   then runs `tools/release/package_linux.sh`.  That script installs
   gtopt, bundles the shared libraries, splits the debug info, strips the
   binaries, rewrites the RUNPATHs and builds the archives.  Next,
   `tools/release/smoke_test.sh` unpacks each archive into a clean
   directory and runs `gtopt --version` (which must print exactly
   `X.Y.Z` on a tag build: the job checks out the full history so the
   embedded `git describe` sees the tag) and `gtopt --solvers`.  It also
   solves `c0` with HiGHS and CLP and checks the objective.
3. `python` builds the sdist and wheel from `scripts/` and checks that
   the wheel installs.
4. `release` writes `SHA256SUMS` and extracts the release notes from
   `CHANGELOG.md`.  It then runs `gh release create --draft` with all the
   assets.
5. `bump-dev` opens the next development cycle (step 7).  It and
   `release` are the only jobs with write permissions.

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

### 7. Merge the development-version PR

Once the draft exists, the `bump-dev` job opens a pull request
`chore/dev-X.(Y+1).0` against `master`.  It sets `CMakeLists.txt` to
`VERSION X.(Y+1).0` with `GTOPT_VERSION_SUFFIX "-dev"` and
`scripts/pyproject.toml` to `X.(Y+1).0.dev0`.  Pull requests opened with
the workflow's `GITHUB_TOKEN` do not start CI, so close and reopen it to
run the checks, then merge it.  Builds from `master` then report e.g.
`X.(Y+1).0-dev (vX.Y.Z-3-g0123456789ab)`.

The job skips the PR when `master` no longer carries `X.Y.Z` (it was
bumped by hand, or this is a patch release from an older line).  It needs
the repository setting *Settings → Actions → General → Allow GitHub
Actions to create and approve pull requests*; without it, make the same
change by hand.  The next release starts again at step 1 and sets the
final version.
