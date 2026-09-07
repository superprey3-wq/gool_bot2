from __future__ import annotations

import json
import urllib.error

import pytest

from gool_bot2 import matchbook_auth, matchbook_pagination
from gool_bot2 import matchbook_exchange as exchange


def _reset_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATCHBOOK_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("MATCHBOOK_USERNAME", raising=False)
    monkeypatch.delenv("MATCHBOOK_PASSWORD", raising=False)
    matchbook_auth._SESSION_TOKEN = ""
    matchbook_auth._SESSION_CREATED_AT = 0.0


def test_403_without_auth_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_session(monkeypatch)

    def forbidden(request, timeout=20):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(matchbook_auth.urllib.request, "urlopen", forbidden)
    with pytest.raises(matchbook_auth.MatchbookAuthError, match="matchbook_auth_required status=403"):
        matchbook_auth._request_json("https://api.matchbook.com/edge/rest/events", "test-agent")


def test_session_token_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_session(monkeypatch)
    monkeypatch.setenv("MATCHBOOK_SESSION_TOKEN", "token-123")
    seen = {}

    class Response:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"events":[]}'

    def ok(request, timeout=20):
        seen.update({str(k).lower(): v for k, v in request.header_items()})
        return Response()

    monkeypatch.setattr(matchbook_auth.urllib.request, "urlopen", ok)
    payload = matchbook_auth._request_json("https://api.matchbook.com/edge/rest/events", "test-agent")
    assert payload == {"events": []}
    assert seen.get("session-token") == "token-123"


def test_paginated_page_uses_shared_auth_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_request(url: str, user_agent: str, *, retry_auth: bool = True):
        seen["url"] = url
        seen["ua"] = user_agent
        return {"events": []}

    monkeypatch.setattr(matchbook_auth, "_request_json", fake_request)
    payload = matchbook_pagination._page_payload(2, 100)

    assert payload == {"events": []}
    assert "offset=100" in seen["url"]
    assert "session-token" not in seen["url"].lower()
    assert seen["ua"]


def test_optional_matchbook_auth_guard_clears_stale_exchange_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The old Matchbook network path is optional now; install its guard explicitly.

    Production is routed to anonymous BETDAQ, so package import intentionally no
    longer installs Matchbook auth. This unit test scopes the legacy patch and
    restores the module afterwards to avoid contaminating the BETDAQ test suite.
    """
    _reset_session(monkeypatch)
    state_path = tmp_path / "matchbook.json"
    state_path.write_text('{"events":[{"event_id":"stale"}]}', encoding="utf-8")

    original_fetch = exchange._fetch_events
    original_collect = exchange.MatchbookExchangeCollector.collect_once
    original_installed = matchbook_auth._INSTALLED

    def fail(*args, **kwargs):
        raise matchbook_auth.MatchbookAuthError("matchbook_auth_required status=403")

    monkeypatch.setattr(matchbook_auth, "_request_json", fail)
    try:
        matchbook_auth._INSTALLED = False
        matchbook_auth.install_matchbook_auth()
        collector = exchange.MatchbookExchangeCollector(state_path)
        with pytest.raises(matchbook_auth.MatchbookAuthError):
            collector.collect_once()

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["available"] is False
        assert payload["events"] == []
        assert "auth_required" in payload["error"]
    finally:
        exchange._fetch_events = original_fetch
        exchange.MatchbookExchangeCollector.collect_once = original_collect
        matchbook_auth._INSTALLED = original_installed
