from __future__ import annotations

import json
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


UA = "GOOL-exchange-money-flow-audit/1.0"
TIMEOUT = 15


def _body_excerpt(data: bytes, limit: int = 12000) -> str:
    text = data.decode("utf-8", errors="replace")
    return text[:limit]


def _http(
    label: str,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int | None, bytes, dict[str, str]]:
    print(f"\n=== {label} ===")
    print(f"URL: {url}")
    req_headers = {"User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            payload = response.read()
            response_headers = dict(response.headers.items())
            print(f"HTTP: {response.status}")
            print(f"Content-Type: {response_headers.get('Content-Type', '')}")
            return int(response.status), payload, response_headers
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        print(f"HTTP: {exc.code}")
        print(f"Content-Type: {response_headers.get('Content-Type', '')}")
        print("Body excerpt:")
        print(_body_excerpt(payload, 4000))
        return int(exc.code), payload, response_headers
    except Exception as exc:  # diagnostic probe
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return None, b"", {}


def _walk_price_rows(value: Any, path: str = "root") -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        keys = {str(k).lower() for k in value.keys()}
        if (
            {"odds", "available-amount"}.issubset(keys)
            or {"price", "size"}.issubset(keys)
            or "available-amount" in keys
        ):
            out.append((path, value))
        for key, child in value.items():
            out.extend(_walk_price_rows(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            out.extend(_walk_price_rows(child, f"{path}[{idx}]"))
    return out


def probe_matchbook() -> None:
    params = urllib.parse.urlencode(
        {
            "exchange-type": "back-lay",
            "odds-type": "DECIMAL",
            "price-depth": 3,
            "price-mode": "expanded",
            "currency": "GBP",
            "minimum-liquidity": 2,
            "old-format": "true",
        }
    )
    url = f"https://api.matchbook.com/edge/rest/popular-markets?{params}"
    status, payload, _ = _http("MATCHBOOK popular-markets / order book", url)
    if status != 200:
        print("MATCHBOOK_RESULT: no unauthenticated order-book response")
        return
    try:
        data = json.loads(payload)
    except Exception as exc:
        print(f"MATCHBOOK_JSON_ERROR: {type(exc).__name__}: {exc}")
        print(_body_excerpt(payload, 8000))
        return

    if isinstance(data, dict):
        print(f"Top-level keys: {sorted(data.keys())}")
    elif isinstance(data, list):
        print(f"Top-level list length: {len(data)}")

    rows = _walk_price_rows(data)
    print(f"Price/liquidity rows found: {len(rows)}")
    for path, row in rows[:20]:
        safe = {
            k: v
            for k, v in row.items()
            if str(k).lower() in {"odds", "available-amount", "side", "price", "size", "type"}
        }
        print(f"PRICE_ROW {path}: {json.dumps(safe, ensure_ascii=False)}")

    serialized = json.dumps(data, ensure_ascii=False)
    print(f"contains_available_amount={'available-amount' in serialized}")
    print(f"contains_matched={'matched' in serialized.lower()}")
    print("JSON excerpt:")
    print(serialized[:12000])


def probe_betfair() -> None:
    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    request_payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listEventTypes",
            "params": {"filter": {}},
            "id": 1,
        }
    ).encode("utf-8")
    status, payload, _ = _http(
        "BETFAIR API-NG without credentials",
        url,
        method="POST",
        headers={"Content-Type": "application/json"},
        body=request_payload,
    )
    if payload:
        print("Betfair response excerpt:")
        print(_body_excerpt(payload, 5000))
    print(f"BETFAIR_RESULT: HTTP {status}; live market/traded volume requires App Key + session")


def probe_betdaq() -> None:
    wsdl = "https://api.betdaq.com/v2.0/ReadOnlyService.asmx?WSDL"
    status, payload, _ = _http("BETDAQ ReadOnlyService WSDL", wsdl)
    if status == 200:
        text = _body_excerpt(payload, 6000)
        print(f"WSDL has GetPrices={'GetPrices' in text}")
        print(f"WSDL has ListSelectionTrades={'ListSelectionTrades' in text}")

    soap = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<soap:Envelope xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\" xmlns:soap=\"http://schemas.xmlsoap.org/soap/envelope/\">
  <soap:Header>
    <ExternalApiHeader version=\"decimal\" languageCode=\"en\" username=\"\" password=\"\" applicationIdentifier=\"\" xmlns=\"http://www.GlobalBettingExchange.com/ExternalAPI/\" />
  </soap:Header>
  <soap:Body>
    <ListTopLevelEvents xmlns=\"http://www.GlobalBettingExchange.com/ExternalAPI/\">
      <listTopLevelEventsRequest WantPlayMarkets=\"false\" />
    </ListTopLevelEvents>
  </soap:Body>
</soap:Envelope>""".encode("utf-8")
    status2, payload2, _ = _http(
        "BETDAQ ListTopLevelEvents without credentials",
        "https://api.betdaq.com/v2.0/ReadOnlyService.asmx",
        method="POST",
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://www.GlobalBettingExchange.com/ExternalAPI/ListTopLevelEvents"',
        },
        body=soap,
    )
    if payload2:
        print("BETDAQ SOAP response excerpt:")
        print(_body_excerpt(payload2, 6000))
    print(f"BETDAQ_RESULT: WSDL_HTTP={status}; unauth_SOAP_HTTP={status2}")


def probe_smarkets() -> None:
    print("\n=== SMARKETS streaming endpoint TCP connectivity ===")
    host, port = "api.smarkets.com", 3701
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        print(f"DNS addresses: {sorted({item[4][0] for item in addresses})}")
    except Exception as exc:
        print(f"DNS_ERROR: {type(exc).__name__}: {exc}")
        return

    try:
        raw = socket.create_connection((host, port), timeout=TIMEOUT)
        print("TCP_CONNECT: success")
        try:
            context = ssl.create_default_context()
            tls = context.wrap_socket(raw, server_hostname=host)
            print(f"TLS_CONNECT: success; version={tls.version()}; cipher={tls.cipher()[0] if tls.cipher() else ''}")
            tls.close()
        except Exception as exc:
            print(f"TLS_CONNECT: {type(exc).__name__}: {exc}")
            try:
                raw.close()
            except Exception:
                pass
    except Exception as exc:
        print(f"TCP_CONNECT: {type(exc).__name__}: {exc}")
    print("SMARKETS_RESULT: endpoint connectivity tested; order-book protocol requires API onboarding/login")


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
