# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for the NEOS client with the network fully mocked."""

from __future__ import annotations

import xmlrpc.client
from pathlib import Path
from typing import Any

import pytest

from gtopt_check_lp import _neos as nm


@pytest.fixture(name="lp")
def _lp(tmp_path: Path) -> Path:
    path = tmp_path / "m.lp"
    path.write_text("Minimize\n obj: x\nEnd\n", encoding="utf-8")
    return path


class _Proxy:
    """Scriptable stand-in for ``xmlrpc.client.ServerProxy``."""

    def __init__(self, **behaviour: Any) -> None:
        self.b = behaviour
        self.statuses = list(behaviour.get("statuses", []))

    def ping(self) -> str:
        return self.b.get("ping", "NeosServer is alive\n")

    def submitJob(self, _xml: str) -> Any:  # noqa: N802
        res = self.b.get("submit")
        if isinstance(res, BaseException):
            raise res
        return res

    def getJobStatus(self, _job: int, _pw: str) -> str:  # noqa: N802
        res = self.statuses.pop(0)
        if isinstance(res, BaseException):
            raise res
        return res

    def getFinalResults(self, _job: int, _pw: str) -> Any:  # noqa: N802
        res = self.b.get("final")
        if isinstance(res, BaseException):
            raise res
        return res


def _client(timeout: int = 10, **behaviour: Any) -> nm.NeosClient:
    client = nm.NeosClient(url="https://neos.invalid:3333", timeout=timeout)
    client._proxy = _Proxy(**behaviour)  # type: ignore[assignment]  # noqa: SLF001
    return client


# ---------------------------------------------------------------------------
# connectivity helpers
# ---------------------------------------------------------------------------


class _Ctx:
    def __enter__(self) -> _Ctx:
        return self

    def __exit__(self, *_a: object) -> None:
        return None


def test_check_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nm.urllib.request, "urlopen", lambda *_a, **_k: _Ctx())
    assert nm._check_internet(timeout=1) is True  # noqa: SLF001

    def _fail(*_a: Any, **_k: Any) -> Any:
        raise OSError("down")

    monkeypatch.setattr(nm.urllib.request, "urlopen", _fail)
    assert nm._check_internet(timeout=1) is False  # noqa: SLF001


@pytest.mark.parametrize(
    ("internet", "error", "expected"),
    [
        (False, OSError("refused"), "No internet connectivity"),
        (True, OSError("timed out"), "slow or unreachable"),
        (True, OSError("refused"), "NEOS server returned an error"),
    ],
)
def test_diagnose_network_error(
    lp: Path,
    monkeypatch: pytest.MonkeyPatch,
    internet: bool,
    error: Exception,
    expected: str,
) -> None:
    monkeypatch.setattr(nm, "_check_internet", lambda timeout=5: internet)
    monkeypatch.setattr(nm, "_NEOS_LP_SIZE_WARN", 1)
    msg = nm._diagnose_network_error("u", lp, error)  # noqa: SLF001
    assert expected in msg
    assert "MiB" in msg


def test_diagnose_network_error_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nm, "_check_internet", lambda timeout=5: False)
    msg = nm._diagnose_network_error("u", tmp_path / "no.lp", OSError("x"))  # noqa: SLF001
    assert "MiB" not in msg


def test_timeout_transport_sets_socket_timeout() -> None:
    conn = nm._TimeoutTransport(timeout=7).make_connection("example.invalid")  # noqa: SLF001
    assert conn.timeout == 7


def test_get_proxy_is_cached() -> None:
    client = nm.NeosClient(url="https://neos.invalid:3333", timeout=3)
    proxy = client._get_proxy()  # noqa: SLF001
    assert client._get_proxy() is proxy  # noqa: SLF001


# ---------------------------------------------------------------------------
# submit_lp / wait_for_result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ([0, "bad"], "NEOS returned error"),
        ("weird", "Unexpected NEOS response"),
        (xmlrpc.client.Fault(1, "nope"), "XML-RPC fault"),
    ],
)
def test_submit_lp_errors(lp: Path, result: Any, expected: str) -> None:
    job, err = _client(submit=result).submit_lp(lp, "a@b")
    assert job is None
    assert expected in str(err)


def test_submit_lp_network_error(lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nm, "_check_internet", lambda timeout=5: False)
    job, err = _client(submit=OSError("conn reset")).submit_lp(lp, "a@b")
    assert job is None
    assert "Network error" in str(err)


@pytest.mark.parametrize(
    ("final", "expected"),
    [
        (xmlrpc.client.Binary(b"binary out"), "binary out"),
        (b"bytes out", "bytes out"),
        ("str out", "str out"),
    ],
)
def test_wait_done_variants(final: Any, expected: str) -> None:
    client = _client(statuses=["Running", "Running", "Done"], final=final)
    assert client.wait_for_result(1, "pw", poll_interval=0) == (True, expected)


def test_wait_errors() -> None:
    ok, msg = _client(statuses=[RuntimeError("poll")]).wait_for_result(1, "pw", 0)
    assert not ok
    assert "Error polling NEOS: poll" in msg
    ok, msg = _client(statuses=["Done"], final=RuntimeError("fetch")).wait_for_result(
        1, "pw", 0
    )
    assert not ok
    assert "Error retrieving NEOS results" in msg
    ok, msg = _client(statuses=["Unknown"]).wait_for_result(2, "pw", 0)
    assert msg == "NEOS job 2 ended with status: Unknown"


# ---------------------------------------------------------------------------
# submit_and_wait
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("internet", [True, False])
def test_submit_and_wait_ping_fails(
    lp: Path, monkeypatch: pytest.MonkeyPatch, internet: bool
) -> None:
    monkeypatch.setattr(nm, "_check_internet", lambda timeout=5: internet)
    monkeypatch.setattr(nm, "_NEOS_LP_SIZE_WARN", 1)
    ok, msg = _client(ping="dead").submit_and_wait(lp, "a@b")
    assert not ok
    assert "Cannot reach NEOS server" in msg
    assert ("No internet" in msg) is (not internet)
    assert "MiB" in msg


def test_submit_and_wait_ping_fails_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nm, "_check_internet", lambda timeout=5: True)
    ok, msg = _client(ping="dead").submit_and_wait(tmp_path / "no.lp", "a@b")
    assert not ok
    assert "MiB" not in msg


def test_submit_and_wait_submit_error(
    lp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok, msg = _client(submit="weird").submit_and_wait(lp, "a@b")
    assert not ok
    assert "Unexpected NEOS response" in msg
    assert "Submitting to NEOS" in capsys.readouterr().out


def test_submit_and_wait_success(lp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    client = _client(submit=[42, "pw"], statuses=["Done"], final="conflict")
    assert client.submit_and_wait(lp, "a@b", poll_interval=0) == (True, "conflict")
    assert "NEOS job 42 submitted" in capsys.readouterr().out
