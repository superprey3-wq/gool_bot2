from __future__ import annotations

import argparse
import difflib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

REMOTE_URL = "http://124.156.195.77:8365/b365/soccer/test/oneHd2allEv/C1-G15?lang=en"
SOURCE = "joe-bring/bet365-scraper"


def load(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return value if isinstance(value, list) else []


def norm(value: Any) -> str:
    s = str(value or "").lower().replace("&", " and ")
    s = re.sub(r"\b(fc|cf|sc|afc|fk|sk|club|football|futbol)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def sim(a: Any, b: Any) -> float:
    a0, b0 = norm(a), norm(b)
    if not a0 or not b0:
        return 0.0
    if a0 == b0:
        return 1.0
    if a0 in b0 or b0 in a0:
        return 0.93
    return difflib.SequenceMatcher(None, a0, b0).ratio()


def split_event_name(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    for sep in (" v ", " vs ", " - ", " – ", " — "):
        if sep in text:
            left, right = text.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return None


def odds_count(value: Any, key: str = "") -> int:
    count = 0
    if isinstance(value, dict):
        for k, v in value.items():
            lk = str(k).lower()
            if lk in {"odds", "odd", "price", "decimalvalue", "decimal", "od"}:
                if isinstance(v, (int, float)) and 1 < float(v) < 10000:
                    count += 1
                elif isinstance(v, str):
                    try:
                        n = float(v)
                    except Exception:
                        n = 0.0
                    if 1 < n < 10000:
                        count += 1
            count += odds_count(v, lk)
    elif isinstance(value, list):
        for item in value:
            count += odds_count(item, key)
    return count


def extract_candidates(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            event_name = node.get("event") or node.get("NA") or node.get("name")
            pair = split_event_name(event_name)
            if pair:
                markets = node.get("markets") or node.get("odds") or []
                out.append(
                    {
                        "home": pair[0],
                        "away": pair[1],
                        "event": event_name,
                        "fixtureId": node.get("fixtureId") or node.get("OI") or node.get("id"),
                        "market_count": len(markets) if isinstance(markets, list) else 0,
                        "price_count": odds_count(markets),
                        "raw": node,
                    }
                )
            else:
                home = node.get("home") or node.get("homeTeam") or node.get("home_name")
                away = node.get("away") or node.get("awayTeam") or node.get("away_name")
                if isinstance(home, dict):
                    home = home.get("name")
                if isinstance(away, dict):
                    away = away.get("name")
                if home and away:
                    markets = node.get("markets") or node.get("odds") or []
                    out.append(
                        {
                            "home": str(home),
                            "away": str(away),
                            "event": f"{home} v {away}",
                            "fixtureId": node.get("fixtureId") or node.get("OI") or node.get("id"),
                            "market_count": len(markets) if isinstance(markets, list) else 0,
                            "price_count": odds_count(markets),
                            "raw": node,
                        }
                    )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in out:
        key = (norm(item.get("home")), norm(item.get("away")), str(item.get("fixtureId") or ""))
        prev = unique.get(key)
        if prev is None or int(item.get("price_count") or 0) > int(prev.get("price_count") or 0):
            unique[key] = item
    return list(unique.values())


def best(match: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best_item = None
    best_score = 0.0
    for item in candidates:
        hs = sim(match.get("home"), item.get("home"))
        aw = sim(match.get("away"), item.get("away"))
        score = (hs + aw) / 2
        if score > best_score and hs >= 0.5 and aw >= 0.5:
            best_score = score
            best_item = item
    return (best_item, best_score) if best_score >= 0.69 else (None, best_score)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default=REMOTE_URL)
    args = parser.parse_args()

    matches = load(args.matches)
    payload: Any = None
    source_error = None
    status = 0
    try:
        req = urllib.request.Request(args.url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
    except Exception as exc:
        source_error = f"{type(exc).__name__}: {exc}"

    candidates = extract_candidates(payload) if payload is not None else []
    print(f"BET365_PROJECT_REMOTE status={status} candidates={len(candidates)} error={source_error or '-'}")

    rows: list[dict[str, Any]] = []
    any_prices = any(int(c.get("price_count") or 0) > 0 for c in candidates)
    for i, match in enumerate(matches, 1):
        item, score = best(match, candidates)
        row: dict[str, Any] = {
            "source": SOURCE,
            "match": match,
            "source_ok": bool(payload is not None and any_prices),
            "remote_http_status": status,
            "remote_candidate_events": len(candidates),
        }
        if item is None:
            row.update({"ok": False, "error": source_error or "match_not_found_in_bet365_feed", "mapping_score": round(score, 4)})
        else:
            row.update(
                {
                    "ok": int(item.get("price_count") or 0) > 0,
                    "mapping_score": round(score, 4),
                    "bet365_event": {
                        "home": item.get("home"),
                        "away": item.get("away"),
                        "fixtureId": item.get("fixtureId"),
                        "market_count": item.get("market_count"),
                        "price_count": item.get("price_count"),
                    },
                    "error": None if int(item.get("price_count") or 0) > 0 else "matched_but_no_prices",
                }
            )
        rows.append(row)
        print(
            f"BET365 {i}/{len(matches)} {match.get('home')} - {match.get('away')} "
            f"ok={row.get('ok')} map={row.get('mapping_score', 0)} prices={(row.get('bet365_event') or {}).get('price_count', 0)}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    matched = sum(1 for r in rows if r.get("ok"))
    print(f"BET365_ALL_LIVE matched={matched}/{len(rows)} coverage={(matched / len(rows) if rows else 0):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
