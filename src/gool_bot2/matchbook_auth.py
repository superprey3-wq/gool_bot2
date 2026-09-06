from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from typing import Any, Callable

LOGIN_URL = "https://api.matchbook.com/bpapi/rest/security/session"
_INSTALLED = False
_SESSION_TOKEN = ""
_SESSION_CREATED_AT = 0.0

class MatchbookAuthError(RuntimeError):
    pass

def _configured_token() -> str:
    return str(os.getenv("MATCHBOOK_SESSION_TOKEN", "")).strip()

def _credentials() -> tuple[str, str]:
    return str(os.getenv("MATCHBOOK_USERNAME", "")).strip(), str(os.getenv("MATCHBOOK_PASSWORD", "")).strip()

def _extract_token(payload: Any, headers: Any) -> str:
    for key in ("session-token", "session_token", "sessionToken"):
        if isinstance(payload, dict) and payload.get(key):
            return str(payload[key]).strip()
    try:
        token = str(headers.get("session-token") or "").strip()
        if token:
            return token
        raw_cookie = str(headers.get("Set-Cookie") or "")
    except Exception:
        raw_cookie = ""
    if raw_cookie:
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
            if "session-token" in cookie:
                return str(cookie["session-token"].value).strip()
        except Exception:
            pass
    return ""

def _login() -> str:
    global _SESSION_TOKEN, _SESSION_CREATED_AT
    username, password = _credentials()
    if not username or not password:
        raise MatchbookAuthError("matchbook_auth_required configure MATCHBOOK_SESSION_TOKEN or MATCHBOOK_USERNAME/MATCHBOOK_PASSWORD")
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(LOGIN_URL, data=body, method="POST", headers={"Accept":"application/json","Content-Type":"application/json","User-Agent":"Mozilla/5.0 GOOL-Matchbook/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            token = _extract_token(payload, response.headers)
    except urllib.error.HTTPError as exc:
        raise MatchbookAuthError(f"matchbook_login_http_{int(exc.code)}") from exc
    except Exception as exc:
        raise MatchbookAuthError(f"matchbook_login_failed:{type(exc).__name__}") from exc
    if not token:
        raise MatchbookAuthError("matchbook_login_no_session_token")
    _SESSION_TOKEN = token
    _SESSION_CREATED_AT = time.time()
    return token

def _session_token(force_refresh: bool = False) -> str:
    global _SESSION_TOKEN, _SESSION_CREATED_AT
    configured = _configured_token()
    if configured:
        return configured
    if force_refresh:
        _SESSION_TOKEN = ""
        _SESSION_CREATED_AT = 0.0
    if _SESSION_TOKEN and time.time() - _SESSION_CREATED_AT < 5.5 * 3600.0:
        return _SESSION_TOKEN
    username, password = _credentials()
    if username and password:
        return _login()
    return ""

def _request_json(url: str, user_agent: str, *, retry_auth: bool = True) -> dict[str, Any]:
    token = _session_token()
    headers = {"User-Agent": user_agent, "Accept": "application/json", "Accept-Encoding": "identity"}
    if token:
        headers["session-token"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        if int(exc.code) in {401, 403}:
            username, password = _credentials()
            if retry_auth and username and password and not _configured_token():
                _session_token(force_refresh=True)
                return _request_json(url, user_agent, retry_auth=False)
            if not token:
                raise MatchbookAuthError(f"matchbook_auth_required status={int(exc.code)} configure MATCHBOOK_SESSION_TOKEN or MATCHBOOK_USERNAME/MATCHBOOK_PASSWORD") from exc
            raise MatchbookAuthError(f"matchbook_api_permission_denied status={int(exc.code)} verify API access/session-token") from exc
        raise

def install_matchbook_auth() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import matchbook_exchange as exchange
    original_collect: Callable[..., dict[str, Any]] = exchange.MatchbookExchangeCollector.collect_once
    def fetch_events_authenticated() -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"tag-url-names":"soccer","states":"open,suspended","exchange-type":"back-lay","odds-type":"DECIMAL","include-prices":"true","price-depth":exchange._orderbook_depth(),"price-mode":"expanded","currency":"GBP","minimum-liquidity":1,"include-event-participants":"true","markets-limit":40,"per-page":100})
        payload = _request_json(f"{exchange.MATCHBOOK_EVENTS_URL}?{params}", exchange.UA)
        rows: list[dict[str, Any]] = []
        for event in payload.get("events") or []:
            if isinstance(event, dict):
                decoded = exchange.decode_event(event)
                if decoded is not None:
                    rows.append(decoded)
        return rows
    def collect_with_auth_guard(self: Any) -> dict[str, Any]:
        try:
            payload = original_collect(self)
            payload["authenticated"] = bool(_session_token())
            return payload
        except MatchbookAuthError as exc:
            unavailable = {"captured_at": exchange._now_iso(), "source":"matchbook_exchange", "available":False, "authenticated":bool(_session_token()), "error":str(exc), "events":[]}
            exchange._atomic_write(self.state_path, unavailable)
            raise
    exchange._fetch_events = fetch_events_authenticated
    exchange.MatchbookExchangeCollector.collect_once = collect_with_auth_guard
    _INSTALLED = True
    mode = "session_token" if _configured_token() else "credentials" if all(_credentials()) else "not_configured"
    print(f"GOOL_MATCHBOOK_AUTH installed mode={mode}", flush=True)

__all__ = ["MatchbookAuthError", "install_matchbook_auth"]
