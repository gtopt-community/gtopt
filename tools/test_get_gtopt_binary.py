# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the CI-artifact download path of ``tools/get_gtopt_binary.py``.

Only artifacts of successful ``push`` runs of ``ubuntu.yml`` on master may be
downloaded (never ``pull_request`` runs, which can build fork code), and the
ZIP must match the artifact's SHA-256 digest.  The GitHub API is mocked.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import get_gtopt_binary as ggb  # pylint: disable=wrong-import-position

REPO = "owner/gtopt"


REPO_ID = 1234
FORK_ID = 5678


def _run(run_id, *, event="push", branch="master", conclusion="success", head=REPO_ID):
    return {
        "id": run_id,
        "event": event,
        "head_branch": branch,
        "conclusion": conclusion,
        "head_sha": f"sha{run_id}",
        "repository": {"id": REPO_ID},
        "head_repository": {"id": head},
    }


def _artifact(art_id, run_id, *, expired=False, branch="master", sha=None, digest=None):
    return {
        "id": art_id,
        "name": ggb._ARTIFACT_NAME,  # pylint: disable=protected-access
        "expired": expired,
        "digest": digest,
        "workflow_run": {
            "id": run_id,
            "repository_id": REPO_ID,
            "head_repository_id": REPO_ID,
            "head_branch": branch,
            "head_sha": sha if sha is not None else f"sha{run_id}",
        },
    }


class FakeApi:
    """Stand-in for ``_api_get_json`` serving canned runs/artifacts."""

    def __init__(self, runs, artifacts_by_run):
        self.runs = runs
        self.artifacts_by_run = artifacts_by_run
        self.paths: list[str] = []

    def __call__(self, gh, token, path):
        del gh, token
        self.paths.append(path)
        if "/actions/workflows/" in path:
            return {"workflow_runs": self.runs}
        run_id = int(path.split("/actions/runs/")[1].split("/")[0])
        return {"artifacts": self.artifacts_by_run.get(run_id, [])}


def _resolve(api):
    with mock.patch.object(ggb, "_api_get_json", api):
        return ggb._resolve_artifact(None, "tok", REPO, ggb._ARTIFACT_NAME)  # pylint: disable=protected-access


class TestResolveArtifact:
    def test_queries_master_push_runs_of_ubuntu_workflow(self):
        api = FakeApi([_run(1)], {1: [_artifact(11, 1)]})
        assert _resolve(api)["id"] == 11
        query = api.paths[0]
        assert query.startswith(f"repos/{REPO}/actions/workflows/ubuntu.yml/runs?")
        assert "branch=master" in query
        assert "event=push" in query
        assert "status=success" in query
        assert api.paths[1] == (
            f"repos/{REPO}/actions/runs/1/artifacts?name={ggb._ARTIFACT_NAME}"  # pylint: disable=protected-access
        )

    @pytest.mark.parametrize(
        "bad_run",
        [
            _run(9, event="pull_request"),
            _run(9, event="pull_request_target"),
            _run(9, branch="feature"),
            _run(9, conclusion="failure"),
            _run(9, head=FORK_ID),
            {**_run(9), "repository": None, "head_repository": None},
        ],
    )
    def test_untrusted_runs_skipped_even_if_api_returns_them(self, bad_run):
        """The client re-checks the filter; a newer untrusted run is ignored."""
        api = FakeApi(
            [bad_run, _run(1)], {9: [_artifact(99, 9)], 1: [_artifact(11, 1)]}
        )
        assert _resolve(api)["id"] == 11
        assert not any("/runs/9/" in p for p in api.paths)

    def test_expired_artifact_falls_back_to_older_run(self):
        api = FakeApi(
            [_run(2), _run(1)],
            {2: [_artifact(22, 2, expired=True)], 1: [_artifact(11, 1)]},
        )
        assert _resolve(api)["id"] == 11

    @pytest.mark.parametrize(
        "bad_artifact",
        [
            _artifact(22, 3),  # points at another run
            _artifact(22, 2, branch="pr-branch"),
            _artifact(22, 2, sha="other"),
            {**_artifact(22, 2), "name": "something-else"},
            {**_artifact(22, 2), "workflow_run": None},
            {
                **_artifact(22, 2),
                "workflow_run": {
                    **_artifact(22, 2)["workflow_run"],
                    "head_repository_id": FORK_ID,
                },
            },
        ],
    )
    def test_artifact_must_belong_to_the_trusted_run(self, bad_artifact):
        api = FakeApi([_run(2), _run(1)], {2: [bad_artifact], 1: [_artifact(11, 1)]})
        assert _resolve(api)["id"] == 11

    def test_no_trusted_artifact_raises(self):
        api = FakeApi([_run(9, event="pull_request")], {9: [_artifact(99, 9)]})
        with pytest.raises(RuntimeError, match="push run of ubuntu.yml on master"):
            _resolve(api)

    def test_no_runs_raises(self):
        with pytest.raises(RuntimeError, match="No non-expired artifact"):
            _resolve(FakeApi([], {}))


class TestApiGetJson:
    def test_gh_cli_path(self):
        done = subprocess.CompletedProcess([], 0, stdout='{"a": 1}', stderr="")
        with mock.patch.object(ggb.subprocess, "run", return_value=done) as run:
            assert ggb._api_get_json("/usr/bin/gh", "", "repos/x/y") == {"a": 1}  # pylint: disable=protected-access
        assert run.call_args.args[0] == ["/usr/bin/gh", "api", "repos/x/y"]

    def test_gh_cli_failure(self):
        err = subprocess.CalledProcessError(1, "gh", stderr="boom")
        with (
            mock.patch.object(ggb.subprocess, "run", side_effect=err),
            pytest.raises(RuntimeError, match="boom"),
        ):
            ggb._api_get_json("/usr/bin/gh", "", "repos/x/y")  # pylint: disable=protected-access

    def test_token_path_keeps_token_off_redirects(self):
        seen = {}

        def fake_urlopen(req, timeout):
            del timeout
            seen["req"] = req
            return io.BytesIO(b'{"ok": true}')

        with mock.patch.object(ggb.urllib.request, "urlopen", fake_urlopen):
            assert ggb._api_get_json(None, "tok", "repos/x/y") == {"ok": True}  # pylint: disable=protected-access
        req = seen["req"]
        assert req.full_url == "https://api.github.com/repos/x/y"
        assert req.unredirected_hdrs["Authorization"] == "Bearer tok"
        assert "Authorization" not in req.headers


class TestVerifyDigest:
    def _zip(self, tmp_path, payload=b"zipbytes"):
        path = tmp_path / "a.zip"
        path.write_bytes(payload)
        return path, hashlib.sha256(payload).hexdigest()

    def test_match(self, tmp_path):
        path, hexd = self._zip(tmp_path)
        ggb._verify_artifact_digest(path, f"sha256:{hexd.upper()}")  # pylint: disable=protected-access
        assert path.exists()

    def test_mismatch_deletes_and_raises(self, tmp_path):
        path, _ = self._zip(tmp_path)
        with pytest.raises(RuntimeError, match="digest mismatch"):
            ggb._verify_artifact_digest(path, "sha256:" + "0" * 64)  # pylint: disable=protected-access
        assert not path.exists()

    @pytest.mark.parametrize("digest", ["md5:abc", "sha256:", "garbage"])
    def test_unsupported_format_raises(self, tmp_path, digest):
        path, _ = self._zip(tmp_path)
        with pytest.raises(RuntimeError, match="Unsupported artifact digest"):
            ggb._verify_artifact_digest(path, digest)  # pylint: disable=protected-access
        assert not path.exists()

    @pytest.mark.parametrize("digest", [None, ""])
    def test_missing_digest_only_warns(self, tmp_path, caplog, digest):
        path, _ = self._zip(tmp_path)
        ggb._verify_artifact_digest(path, digest)  # pylint: disable=protected-access
        assert "no digest" in caplog.text
        assert path.exists()


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("gtopt", "#!/bin/sh\n")
        zf.writestr("libgtopt_solver_highs.so", "")
    return buf.getvalue()


class TestDownloadGtoptFromCi:
    def _run_download(self, tmp_path, monkeypatch, digest_of):
        payload = _zip_bytes()
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        artifact = _artifact(11, 1, digest=digest_of(payload))
        api = FakeApi([_run(1)], {1: [artifact]})
        downloads = []

        def fake_download(gh, token, repo, artifact_id, zip_path):
            del gh, token
            downloads.append((repo, artifact_id))
            zip_path.write_bytes(payload)

        with (
            mock.patch.object(ggb.shutil, "which", return_value=None),
            mock.patch.object(ggb, "_api_get_json", api),
            mock.patch.object(ggb, "_download_artifact_zip", fake_download),
        ):
            result = ggb.download_gtopt_from_ci(tmp_path / "bin", repo=REPO)
        return result, downloads

    def test_downloads_verified_trusted_artifact(self, tmp_path, monkeypatch):
        result, downloads = self._run_download(
            tmp_path, monkeypatch, lambda p: "sha256:" + hashlib.sha256(p).hexdigest()
        )
        assert downloads == [(REPO, "11")]
        assert result == tmp_path / "bin" / "gtopt"
        assert result.stat().st_mode & 0o111

    def test_digest_mismatch_aborts_before_extraction(self, tmp_path, monkeypatch):
        with pytest.raises(RuntimeError, match="digest mismatch"):
            self._run_download(tmp_path, monkeypatch, lambda p: "sha256:" + "0" * 64)
        assert not (tmp_path / "bin" / "gtopt").exists()


def test_download_zip_token_path_keeps_token_off_redirects(tmp_path):
    seen = {}

    def fake_urlopen(req, timeout):
        del timeout
        seen["req"] = req
        return io.BytesIO(b"zip")

    with mock.patch.object(ggb.urllib.request, "urlopen", fake_urlopen):
        ggb._download_artifact_zip(None, "tok", REPO, "11", tmp_path / "a.zip")  # pylint: disable=protected-access
    assert (tmp_path / "a.zip").read_bytes() == b"zip"
    assert seen["req"].unredirected_hdrs["Authorization"] == "Bearer tok"
    assert "Authorization" not in seen["req"].headers
