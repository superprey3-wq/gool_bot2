from __future__ import annotations

import argparse
import difflib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

SOURCE_URLS: dict[str, list[str]] = {
    "kambi_unibet": [
        "https://eu-offering-api.kambicdn.com/offering/v2018/ub/listView/all/all/all/all/in-play.json?lang=en_GB&market=GB&channel_id=1&useCombined=true",
        "https://ap.offering-api.kambicdn.com/offering/v2018/ubau/listView/all/all/all/all/in-play.json?lang=en_AU&market=AU&channel_id=1&useCombined=true",
    ],
    "bet365": [
        "http://124.156.195.77:8365/b365/soccer/test/oneHd2allEv/C1-G15?lang=en",
    ],
    "betano": [
        "https://www.betano.de/api/sport/fussball?req=la,s,stnf,c,mb",
        "https://www.betano.pt/api/sport/futebol?req=la,s,stnf,c,mb",
        "https://betano.bet.br/api/sport/futebol?req=la,s,stnf,c,mb",
        "https://www.betano.com/api/sport/football?req=la,s,stnf,c,mb",
    ],
}

BLOCK_MARKERS = (
    "cloudflare",
    "access denied",
    "captcha",
    "attention required",
    "forbidden",
    "geo blocked",
    "geoblocked",
    "unavailable in your location",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def norm(value: Any) -> str:
    s = str(value or "").casefold().replace("&", " and ")
    s = re.sub(r"\b(fc|cf|sc|afc|fk|sk|club|football|futbol|fussball|deportivo|calcio)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def sim(a: Any, b: Any) -> float:
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.93
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def scalar_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "participantName", "teamName", "shortName", "label"):
            if value.get(key):
                return str(value[key]).strip()
    return ""


def split_pair(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    for sep in (" v ", " vs ", " vs. ", " - ", " – ", " — ", " @ "):
        if sep in text:
            left, right = text.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return None


def direct_pair(node: dict[str, Any]) -> tuple[str, str] | None:
    home_keys = ("homeName", "home", "homeTeam", "home_team", "team1", "competitor1", "participant1")
    away_keys = ("awayName", "away", "awayTeam", "away_team", "team2", "competitor2", "participant2")
    home = next((scalar_name(node.get(k)) for k in home_keys if scalar_name(node.get(k))), "")
    away = next((scalar_name(node.get(k)) for k in away_keys if scalar_name(node.get(k))), "")
    if home and away:
        return home, away

    participants = node.get("participants") or node.get("competitors")
    if isinstance(participants, list) and len(participants) >= 2:
        names = [scalar_name(x) for x in participants[:4]]
        names = [x for x in names if x]
        if len(names) >= 2:
            return names[0], names[1]

    for key in ("shortName", "eventName", "event", "name", "NA", "label", "title"):
        value = node.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("shortName")
        pair = split_pair(value)
        if pair:
            return pair
    return None


def is_odds_key(key: str) -> bool:
    k = key.casefold().replace("_", "")
    return any(token in k for token in ("odds", "odd", "price", "decimal", "fractional"))


def numeric_price(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    if not (1.0 < abs(num) <= 2_000_000):
        return None
    if num.is_integer():
        return str(int(num))
    return f"{num:.6f}".rstrip("0").rstrip(".")


def quote_tokens(node: Any, path: str = "", odds_context: bool = False, out: list[str] | None = None) -> list[str]:
    if out is None:
        out = []
    if len(out) >= 120:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            ctx = odds_context or is_odds_key(str(key))
            if ctx and not isinstance(value, (dict, list)):
                price = numeric_price(value)
                if price is not None:
                    out.append(f"{child}={price}")
            quote_tokens(value, child, ctx, out)
    elif isinstance(node, list):
        for idx, value in enumerate(node[:150]):
            quote_tokens(value, f"{path}[{idx}]", odds_context, out)
    elif odds_context:
        price = numeric_price(node)
        if price is not None:
            out.append(f"{path}={price}")
    return out


def extract_candidates(payload: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pair = direct_pair(node)
            if pair:
                quotes = sorted(set(quote_tokens(node)))
                event_id = ""
                for key in ("eventId", "event_id", "fixtureId", "fixture_id", "id", "OI"):
                    if node.get(key) is not None:
                        event_id = str(node.get(key))
                        break
                candidates.append(
                    {
                        "home": pair[0],
                        "away": pair[1],
                        "event_id": event_id,
                        "quote_tokens": quotes,
                        "quote_count": len(quotes),
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (norm(item["home"]), norm(item["away"]), str(item.get("event_id") or ""))
        previous = unique.get(key)
        if previous is None or int(item.get("quote_count") or 0) > int(previous.get("quote_count") or 0):
            unique[key] = item
    return list(unique.values())


def http_json(url: str) -> dict[str, Any]:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if "kambicdn.com" in url:
        headers.update({"Origin": "https://www.unibet.com/", "Referer": "https://www.unibet.com/"})
    elif "betano" in url:
        headers.update({"Referer": url.split("/api/", 1)[0] + "/"})

    req = urllib.request.Request(url, headers=headers)
    status = 0
    body = ""
    content_type = ""
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
            content_type = str(response.headers.get("content-type") or "")
            body = response.read(5_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        content_type = str(exc.headers.get("content-type") or "") if exc.headers else ""
        try:
            body = exc.read(200_000).decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
    except Exception as exc:
        return {"url": url, "status": 0, "ok": False, "blocked": False, "error": f"{type(exc).__name__}: {exc}"}

    lowered = body[:100_000].casefold()
    blocked = status in {401, 403, 451} or any(marker in lowered for marker in BLOCK_MARKERS)
    payload = None
    error = None
    if body:
        try:
            payload = json.loads(body)
        except Exception as exc:
            error = f"json_decode:{type(exc).__name__}"
    candidates = extract_candidates(payload) if payload is not None else []
    quoted = sum(1 for item in candidates if int(item.get("quote_count") or 0) > 0)
    return {
        "url": url,
        "status": status,
        "content_type": content_type,
        "ok": status == 200 and payload is not None,
        "blocked": blocked,
        "error": error,
        "candidate_count": len(candidates),
        "quoted_candidate_count": quoted,
        "candidates": candidates,
    }


def fetch_source(source: str) -> dict[str, Any]:
    attempts = [http_json(url) for url in SOURCE_URLS[source]]
    usable = [x for x in attempts if x.get("ok")]
    chosen = None
    if usable:
        chosen = max(
            usable,
            key=lambda x: (int(x.get("quoted_candidate_count") or 0), int(x.get("candidate_count") or 0)),
        )
    state = "ok" if chosen else ("blocked" if attempts and all(x.get("blocked") for x in attempts) else "error")
    return {
        "source": source,
        "state": state,
        "chosen_url": chosen.get("url") if chosen else None,
        "candidates": chosen.get("candidates", []) if chosen else [],
        "attempts": [{k: v for k, v in attempt.items() if k != "candidates"} for attempt in attempts],
    }


def best_candidate(match: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, bool]:
    best_item: dict[str, Any] | None = None
    best_score = 0.0
    reversed_order = False
    for item in candidates:
        direct_h = sim(match.get("home"), item.get("home"))
        direct_a = sim(match.get("away"), item.get("away"))
        direct = (direct_h + direct_a) / 2
        reverse_h = sim(match.get("home"), item.get("away"))
        reverse_a = sim(match.get("away"), item.get("home"))
        reverse = (reverse_h + reverse_a) / 2 - 0.015
        score, rev = (reverse, True) if reverse > direct else (direct, False)
        if score > best_score:
            best_score = score
            best_item = item
            reversed_order = rev
    if best_item is None or best_score < 0.68:
        return None, best_score, reversed_order
    return best_item, best_score, reversed_order


def flashscore_rows() -> list[dict[str, Any]]:
    from gool_bot2.providers.flashscore import FlashscoreProvider

    matches = FlashscoreProvider().live_matches()
    rows: list[dict[str, Any]] = []
    for match in matches:
        event_id = str(getattr(match, "provider_match_id", "") or "").strip()
        if not event_id:
            continue
        rows.append(
            {
                "event_id": event_id,
                "home": str(getattr(match, "home", "") or ""),
                "away": str(getattr(match, "away", "") or ""),
                "league": str(getattr(match, "league", "") or ""),
                "minute": int(getattr(match, "minute", 0) or 0),
                "home_score": int(getattr(match, "home_score", 0) or 0),
                "away_score": int(getattr(match, "away_score", 0) or 0),
            }
        )
    return rows


def source_match_rows(matches: list[dict[str, Any]], source_result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates = list(source_result.get("candidates") or [])
    for match in matches:
        candidate, score, reversed_order = best_candidate(match, candidates)
        if candidate is None:
            status = "source_blocked" if source_result.get("state") == "blocked" else (
                "source_error" if source_result.get("state") == "error" else "not_found"
            )
            rows.append(
                {
                    "event_id": match["event_id"],
                    "home": match["home"],
                    "away": match["away"],
                    "status": status,
                    "mapping_score": round(score, 4),
                }
            )
            continue
        quotes = list(candidate.get("quote_tokens") or [])
        rows.append(
            {
                "event_id": match["event_id"],
                "home": match["home"],
                "away": match["away"],
                "status": "matched" if quotes else "matched_no_quotes",
                "mapping_score": round(score, 4),
                "reversed_order": reversed_order,
                "book_event_id": candidate.get("event_id"),
                "book_home": candidate.get("home"),
                "book_away": candidate.get("away"),
                "quote_count": len(quotes),
                "quote_tokens": quotes,
                "fingerprint": "|".join(quotes),
            }
        )
    return rows


def summarize(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    event_meta: dict[str, dict[str, Any]] = {}
    by_event_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
    source_states: dict[str, list[str]] = {source: [] for source in SOURCE_URLS}

    for snap in snapshots:
        for match in snap.get("flashscore", []):
            eid = str(match.get("event_id") or "")
            event_meta.setdefault(eid, {"home": match.get("home"), "away": match.get("away"), "league": match.get("league")})
            event_meta[eid].setdefault("states", []).append(
                {
                    "snapshot": snap.get("snapshot"),
                    "captured_at": snap.get("captured_at"),
                    "minute": match.get("minute"),
                    "home_score": match.get("home_score"),
                    "away_score": match.get("away_score"),
                }
            )
        for source, source_payload in (snap.get("sources") or {}).items():
            source_states.setdefault(source, []).append(str(source_payload.get("state") or ""))
            for row in source_payload.get("matches", []):
                by_event_source.setdefault((str(row.get("event_id") or ""), source), []).append(
                    {"snapshot": snap.get("snapshot"), "captured_at": snap.get("captured_at"), **row}
                )

    events: list[dict[str, Any]] = []
    verdict_counts: dict[str, int] = {}
    source_summary: dict[str, dict[str, int]] = {source: {} for source in SOURCE_URLS}

    for event_id, meta in sorted(event_meta.items(), key=lambda x: (str(x[1].get("league") or ""), str(x[1].get("home") or ""))):
        states = list(meta.get("states") or [])
        progress = len({(s.get("minute"), s.get("home_score"), s.get("away_score")) for s in states}) > 1
        source_rows: dict[str, Any] = {}
        for source in SOURCE_URLS:
            rows = by_event_source.get((event_id, source), [])
            matched = [r for r in rows if r.get("status") in {"matched", "matched_no_quotes"}]
            fingerprints = [str(r.get("fingerprint") or "") for r in matched if str(r.get("fingerprint") or "")]
            odds_changed = len(set(fingerprints)) > 1
            if odds_changed and progress:
                verdict = "PROVEN"
            elif odds_changed:
                verdict = "ODDS_CHANGED_NO_FS_PROGRESS"
            elif matched:
                verdict = "FOUND_NO_CHANGE"
            elif rows and all(r.get("status") == "source_blocked" for r in rows):
                verdict = "BLOCKED_GEO"
            elif rows and all(r.get("status") == "source_error" for r in rows):
                verdict = "SOURCE_ERROR"
            else:
                verdict = "NOT_FOUND"
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            source_summary[source][verdict] = source_summary[source].get(verdict, 0) + 1
            source_rows[source] = {
                "verdict": verdict,
                "odds_changed": odds_changed,
                "flashscore_progress": progress,
                "snapshots": rows,
            }
        events.append({"event_id": event_id, **{k: v for k, v in meta.items() if k != "states"}, "flashscore_states": states, "sources": source_rows})

    return {
        "generated_at": utc_now(),
        "snapshot_count": len(snapshots),
        "unique_flashscore_live_matches": len(event_meta),
        "source_states": source_states,
        "source_summary": source_summary,
        "verdict_counts": verdict_counts,
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Kambi/Unibet, Bet365 and Betano against every GOOL Flashscore live match")
    parser.add_argument("--output-dir", type=Path, default=Path("live_bookmaker_audit"))
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--interval", type=int, default=45, help="seconds between snapshots")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshots: list[dict[str, Any]] = []

    for index in range(1, max(1, args.snapshots) + 1):
        captured_at = utc_now()
        matches = flashscore_rows()[: max(1, args.limit)]
        print(f"SNAPSHOT {index}/{args.snapshots} at={captured_at} flashscore_live={len(matches)}", flush=True)
        sources: dict[str, Any] = {}
        for source in SOURCE_URLS:
            result = fetch_source(source)
            match_rows = source_match_rows(matches, result)
            sources[source] = {
                "state": result.get("state"),
                "chosen_url": result.get("chosen_url"),
                "attempts": result.get("attempts"),
                "matches": match_rows,
            }
            matched = sum(1 for row in match_rows if row.get("status") == "matched")
            print(
                f"  {source}: state={result.get('state')} candidates={len(result.get('candidates') or [])} "
                f"matched_with_quotes={matched}/{len(matches)}",
                flush=True,
            )
        snapshot = {"snapshot": index, "captured_at": captured_at, "flashscore": matches, "sources": sources}
        snapshots.append(snapshot)
        write_json(args.output_dir / "snapshots" / f"snapshot_{index}.json", snapshot)
        if index < args.snapshots:
            time.sleep(max(1, args.interval))

    summary = summarize(snapshots)
    write_json(args.output_dir / "audit.json", snapshots)
    write_json(args.output_dir / "summary.json", summary)
    print("FINAL_SUMMARY " + json.dumps({k: summary[k] for k in ("snapshot_count", "unique_flashscore_live_matches", "source_states", "source_summary", "verdict_counts")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
