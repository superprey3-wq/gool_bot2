from __future__ import annotations

import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


UA = "GOOL-exchange-money-flow-audit/1.1"
TIMEOUT = 15


def _body_excerpt(data: bytes, limit: int = 12000) -> str:
    return data.decode("utf-8", errors="replace")[:limit]


def _http(
    label: str,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    quiet: bool = False,
) -> tuple[int | None, bytes, dict[str, str]]:
    if not quiet:
        print(f"\n=== {label} ===")
        print(f"URL: {url}")
    req_headers = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            payload = response.read()
            response_headers = dict(response.headers.items())
            if not quiet:
                print(f"HTTP: {response.status}")
                print(f"Content-Type: {response_headers.get('Content-Type', '')}")
            return int(response.status), payload, response_headers
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        if not quiet:
            print(f"HTTP: {exc.code}")
            print(f"Content-Type: {response_headers.get('Content-Type', '')}")
            print("Body excerpt:")
            print(_body_excerpt(payload, 4000))
        return int(exc.code), payload, response_headers
    except Exception as exc:
        if not quiet:
            print(f"ERROR: {type(exc).__name__}: {exc}")
        return None, b"", {}


def _json_get(label: str, url: str, *, quiet: bool = False) -> dict[str, Any] | list[Any] | None:
    status, payload, _ = _http(label, url, quiet=quiet)
    if status != 200:
        return None
    try:
        return json.loads(payload)
    except Exception as exc:
        if not quiet:
            print(f"JSON_ERROR: {type(exc).__name__}: {exc}")
        return None


def _walk_price_rows(value: Any, path: str = "root") -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        keys = {str(k).lower() for k in value.keys()}
        if "available-amount" in keys:
            out.append((path, value))
        for key, child in value.items():
            out.extend(_walk_price_rows(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            out.extend(_walk_price_rows(child, f"{path}[{idx}]"))
    return out


def _events(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        rows = data.get("events")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        event = data.get("event")
        if isinstance(event, dict):
            return [event]
        if "id" in data and ("markets" in data or "sport-id" in data):
            return [data]
    return []


def _prices(runner: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    back: list[dict[str, Any]] = []
    lay: list[dict[str, Any]] = []
    for row in runner.get("prices") or []:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "").lower()
        if side == "back":
            back.append(row)
        elif side == "lay":
            lay.append(row)
    back.sort(key=lambda row: float(row.get("odds") or 0.0), reverse=True)
    lay.sort(key=lambda row: float(row.get("odds") or 9999.0))
    return back, lay


def _market_summary(event: dict[str, Any]) -> dict[str, Any]:
    markets: list[dict[str, Any]] = [row for row in (event.get("markets") or []) if isinstance(row, dict)]
    preferred = [
        market for market in markets
        if str(market.get("market-type") or "").lower() in {"one_x_two", "total", "over_under"}
        or any(token in str(market.get("name") or "").lower() for token in ("match odds", "total goals", "over/under", "over under"))
    ]
    if not preferred:
        preferred = markets
    preferred.sort(key=lambda market: float(market.get("volume") or 0.0), reverse=True)
    out: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "event_id": event.get("id"),
        "event_name": event.get("name"),
        "event_volume": float(event.get("volume") or 0.0),
        "in_running": bool(event.get("in-running-flag")),
        "markets": {},
    }
    for market in preferred[:6]:
        runners_out: dict[str, Any] = {}
        for runner in market.get("runners") or []:
            if not isinstance(runner, dict):
                continue
            back, lay = _prices(runner)
            back_amount = sum(float(row.get("available-amount") or 0.0) for row in back)
            lay_amount = sum(float(row.get("available-amount") or 0.0) for row in lay)
            runners_out[str(runner.get("name") or runner.get("id"))] = {
                "runner_volume": float(runner.get("volume") or 0.0),
                "best_back": float(back[0].get("odds") or 0.0) if back else None,
                "best_back_amount": float(back[0].get("available-amount") or 0.0) if back else 0.0,
                "best_lay": float(lay[0].get("odds") or 0.0) if lay else None,
                "best_lay_amount": float(lay[0].get("available-amount") or 0.0) if lay else 0.0,
                "back_depth_amount": round(back_amount, 2),
                "lay_depth_amount": round(lay_amount, 2),
            }
        out["markets"][str(market.get("id"))] = {
            "name": market.get("name"),
            "market_type": market.get("market-type"),
            "market_volume": float(market.get("volume") or 0.0),
            "runners": runners_out,
        }
    return out


def _print_snapshot(snapshot: dict[str, Any], index: int) -> None:
    print(f"\nMATCHBOOK_FOOTBALL_SNAPSHOT_{index}: {snapshot.get('event_name')} in_running={snapshot.get('in_running')} event_volume={snapshot.get('event_volume'):.2f}")
    for market in snapshot.get("markets", {}).values():
        print(f"  MARKET {market.get('name')} type={market.get('market_type')} volume={market.get('market_volume'):.2f}")
        for name, runner in list((market.get("runners") or {}).items())[:6]:
            print(
                "    RUNNER "
                f"{name}: vol={runner.get('runner_volume'):.2f} "
                f"BACK={runner.get('best_back')} £{runner.get('best_back_amount'):.2f} "
                f"LAY={runner.get('best_lay')} £{runner.get('best_lay_amount'):.2f} "
                f"depth_back=£{runner.get('back_depth_amount'):.2f} depth_lay=£{runner.get('lay_depth_amount'):.2f}"
            )


def _print_delta(a: dict[str, Any], b: dict[str, Any], seconds: int) -> None:
    print(f"\nMATCHBOOK_FLOW_DELTA +{seconds}s: event_volume {a.get('event_volume'):.2f} -> {b.get('event_volume'):.2f} Δ={b.get('event_volume', 0)-a.get('event_volume', 0):+.2f}")
    a_markets = a.get("markets") or {}
    b_markets = b.get("markets") or {}
    for market_id, bm in b_markets.items():
        am = a_markets.get(market_id) or {}
        if not am:
            continue
        delta = float(bm.get("market_volume") or 0.0) - float(am.get("market_volume") or 0.0)
        changed = delta != 0.0
        runner_changes: list[str] = []
        for name, br in (bm.get("runners") or {}).items():
            ar = (am.get("runners") or {}).get(name) or {}
            fields = []
            for key in ("runner_volume", "best_back", "best_lay", "back_depth_amount", "lay_depth_amount"):
                if br.get(key) != ar.get(key):
                    fields.append(f"{key}:{ar.get(key)}->{br.get(key)}")
            if fields:
                changed = True
                runner_changes.append(f"{name}[{', '.join(fields)}]")
        if changed:
            print(f"  FLOW {bm.get('name')}: market_volume Δ={delta:+.2f}")
            for line in runner_changes[:6]:
                print(f"    {line}")


def probe_matchbook() -> None:
    popular_params = urllib.parse.urlencode({
        "exchange-type": "back-lay",
        "odds-type": "DECIMAL",
        "price-depth": 3,
        "price-mode": "expanded",
        "currency": "GBP",
        "minimum-liquidity": 2,
        "old-format": "true",
    })
    popular = _json_get("MATCHBOOK popular-markets / order book", f"https://api.matchbook.com/edge/rest/popular-markets?{popular_params}")
    if popular is None:
        print("MATCHBOOK_RESULT: no unauthenticated order-book response")
        return
    rows = _walk_price_rows(popular)
    serialized = json.dumps(popular, ensure_ascii=False)
    print(f"Price/liquidity rows found: {len(rows)}")
    print(f"contains_available_amount={'available-amount' in serialized}")
    print(f"contains_volume_field={'\"volume\"' in serialized}")

    sports = _json_get("MATCHBOOK sport lookup", "https://api.matchbook.com/edge/rest/lookups/sports?per-page=100")
    soccer_id = 15
    if isinstance(sports, dict):
        for sport in sports.get("sports") or []:
            if isinstance(sport, dict) and str(sport.get("name") or "").lower() in {"soccer", "football"}:
                soccer_id = int(sport.get("id") or soccer_id)
                print(f"MATCHBOOK_SOCCER_ID={soccer_id}")
                break

    params = urllib.parse.urlencode({
        "sport-ids": soccer_id,
        "states": "open",
        "include-prices": "true",
        "price-depth": 3,
        "price-mode": "expanded",
        "currency": "GBP",
        "minimum-liquidity": 2,
        "include-event-participants": "true",
        "markets-limit": 50,
        "per-page": 200,
    })
    data = _json_get("MATCHBOOK football events with prices", f"https://api.matchbook.com/edge/rest/events?{params}")
    football = _events(data)
    live = [event for event in football if bool(event.get("in-running-flag")) and bool(event.get("allow-live-betting", True))]
    candidates = live or football
    print(f"MATCHBOOK_FOOTBALL_EVENTS={len(football)} live={len(live)}")
    if not candidates:
        print("MATCHBOOK_FOOTBALL_RESULT: no open football event available in this snapshot")
        return
    candidates.sort(key=lambda event: float(event.get("volume") or 0.0), reverse=True)
    target = candidates[0]
    event_id = target.get("id")
    print(f"MATCHBOOK_TARGET: id={event_id} name={target.get('name')} in_running={target.get('in-running-flag')} volume={float(target.get('volume') or 0.0):.2f}")

    event_params = urllib.parse.urlencode({
        "include-prices": "true",
        "price-depth": 3,
        "price-mode": "expanded",
        "currency": "GBP",
        "minimum-liquidity": 2,
    })
    event_url = f"https://api.matchbook.com/edge/rest/events/{event_id}?{event_params}"
    snapshots: list[dict[str, Any]] = []
    for index in range(1, 4):
        detail = _json_get(f"MATCHBOOK football snapshot {index}", event_url, quiet=True)
        events = _events(detail)
        if not events:
            print(f"MATCHBOOK_SNAPSHOT_{index}: unavailable")
            break
        snapshot = _market_summary(events[0])
        snapshots.append(snapshot)
        _print_snapshot(snapshot, index)
        if index < 3:
            time.sleep(8)
    for index in range(1, len(snapshots)):
        _print_delta(snapshots[index - 1], snapshots[index], 8)

    matched_url = "https://api.matchbook.com/edge/rest/v2/matched-bets/aggregated?" + urllib.parse.urlencode({"event-ids": event_id, "per-page": 5, "aggregation-type": "summary"})
    status, payload, _ = _http("MATCHBOOK aggregated matched-bets without session", matched_url)
    print(f"MATCHBOOK_MATCHED_BETS_RESULT: HTTP={status} excerpt={_body_excerpt(payload, 500) if payload else ''}")


def probe_betfair() -> None:
    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    request_payload = json.dumps({"jsonrpc": "2.0", "method": "SportsAPING/v1.0/listEventTypes", "params": {"filter": {}}, "id": 1}).encode("utf-8")
    status, payload, _ = _http("BETFAIR API-NG without credentials", url, method="POST", headers={"Content-Type": "application/json"}, body=request_payload)
    print(f"BETFAIR_RESULT: HTTP {status}; live market/traded volume requires App Key + session")
    if payload:
        print(_body_excerpt(payload, 500))


def probe_betdaq() -> None:
    status, _, _ = _http("BETDAQ ReadOnlyService WSDL", "https://api.betdaq.com/v2.0/ReadOnlyService.asmx?WSDL")
    print(f"BETDAQ_RESULT: WSDL_HTTP={status}")


def probe_smarkets() -> None:
    print("\n=== SMARKETS streaming endpoint TCP connectivity ===")
    host, port = "api.smarkets.com", 3701
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        print(f"DNS addresses: {sorted({item[4][0] for item in addresses})}")
        raw = socket.create_connection((host, port), timeout=TIMEOUT)
        print("TCP_CONNECT: success")
        try:
            context = ssl.create_default_context()
            tls = context.wrap_socket(raw, server_hostname=host)
            print(f"TLS_CONNECT: success; version={tls.version()}")
            tls.close()
        except Exception as exc:
            print(f"TLS_CONNECT: {type(exc).__name__}: {exc}")
    except Exception as exc:
        print(f"TCP_CONNECT: {type(exc).__name__}: {exc}")
    print("SMARKETS_RESULT: order-book protocol requires API onboarding/login")


def main() -> int:
    print("GOOL EXCHANGE MONEY FLOW PROBE")
    print(f"captured_at={datetime.now(timezone.utc).isoformat()}")
    print(f"python={sys.version.split()[0]}")
    probe_matchbook()
    probe_betfair()
    probe_betdaq()
    probe_smarkets()
    print("\nDONE: diagnostic only; no bets/orders were placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
