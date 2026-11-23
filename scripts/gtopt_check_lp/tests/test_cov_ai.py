# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``gtopt_check_lp._ai`` with HTTP fully mocked."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from gtopt_check_lp import _ai


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self._data = json.dumps(body).encode()

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


def _capture(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> list[Any]:
    reqs: list[Any] = []

    def _urlopen(req: Any, timeout: int) -> _Resp:
        reqs.append(req)
        return _Resp(body)

    monkeypatch.setattr(_ai.urllib.request, "urlopen", _urlopen)
    return reqs


_CHOICES = {"choices": [{"message": {"content": " answer "}}]}


def test_unimplemented_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ai, "_AI_PROVIDERS", (*_ai._AI_PROVIDERS, "future"))  # noqa: SLF001
    ok, msg = _ai.query_ai("r", provider="future", model="m")
    assert not ok
    assert "not yet implemented" in msg


def test_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    reqs = _capture(monkeypatch, _CHOICES)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk")
    assert _ai.query_ai("r", provider="deepseek") == (True, "answer")
    assert reqs[0].full_url == "https://api.deepseek.com/v1/chat/completions"
    assert reqs[0].get_header("Authorization") == "Bearer dk"


def test_github(monkeypatch: pytest.MonkeyPatch) -> None:
    reqs = _capture(monkeypatch, _CHOICES)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "ok")
    monkeypatch.setenv("OPENAI_API_BASE", "https://proxy.invalid/")
    assert _ai.query_ai("r", provider="github") == (True, "answer")
    assert reqs[0].full_url == "https://proxy.invalid/chat/completions"
    assert reqs[0].get_header("Authorization") == "Bearer ok"


def test_local_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    reqs = _capture(monkeypatch, {"other": 1})
    monkeypatch.setenv("LOCAL_AI_URL", "http://127.0.0.1:1/v1/")
    ok, text = _ai.query_ai("r", provider="local", api_key="lk")
    assert ok
    assert json.loads(text) == {"other": 1}
    assert reqs[0].full_url == "http://127.0.0.1:1/v1/chat/completions"
    assert reqs[0].get_header("Authorization") == "Bearer lk"


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]


def test_http_retry_then_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    errors = [
        _http_error(429, b""),
        _http_error(500, json.dumps({"error": {"message": "boom"}}).encode()),
    ]

    def _urlopen(_req: Any, timeout: int) -> Any:
        raise errors.pop(0)

    monkeypatch.setattr(_ai.urllib.request, "urlopen", _urlopen)
    ok, msg = _ai._http_post_json("http://ai.invalid/u", {}, {}, 1)  # noqa: SLF001
    assert not ok
    assert msg == "HTTP 500 from AI API: boom"
    assert sleeps == [1]


def test_http_error_unparseable_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def _urlopen(_req: Any, timeout: int) -> Any:
        raise _http_error(400, b"not json")

    monkeypatch.setattr(_ai.urllib.request, "urlopen", _urlopen)
    ok, msg = _ai._http_post_json("http://ai.invalid/u", {}, {}, 1)  # noqa: SLF001
    assert not ok
    assert msg.startswith("HTTP 400 from AI API: HTTP Error 400")


def test_http_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def _urlopen(_req: Any, timeout: int) -> Any:
        raise _http_error(403, b"")

    monkeypatch.setattr(_ai.urllib.request, "urlopen", _urlopen)
    ok, msg = _ai._http_post_json("http://ai.invalid/u", {}, {}, 1, _max_retries=1)  # noqa: SLF001
    assert not ok
    assert msg.startswith("HTTP 403")
    calls = iter([_http_error(429, b""), _http_error(429, b"")])

    def _urlopen2(_req: Any, timeout: int) -> Any:
        raise next(calls)

    monkeypatch.setattr(_ai.urllib.request, "urlopen", _urlopen2)
    # Two attempts, both rate-limited: the last one is not retried.
    ok, msg = _ai._http_post_json("http://ai.invalid/u", {}, {}, 1, _max_retries=2)  # noqa: SLF001
    assert not ok
    assert msg.startswith("HTTP 429 from AI API")


def test_http_zero_retries() -> None:
    ok, msg = _ai._http_post_json("http://ai.invalid/u", {}, {}, 1, _max_retries=0)  # noqa: SLF001
    assert not ok
    assert msg == "AI API request failed after 0 retries: None"
