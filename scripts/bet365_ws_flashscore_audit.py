from __future__ import annotations

import argparse
import difflib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(value: Any) -> str:
    text = str(value or "").casefold().replace("&", " and ")
    text = re.sub(r"\b(fc|cf|sc|afc|fk|sk|club|football|futbol)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def sim(a: Any, b: Any) -> float:
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.93
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def split_pair(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    for sep in (" v ", " vs ", " vs. ", " - ", " – ", " — ", " @ "):
        if sep in text:
            left, right = text.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return None


def scalar_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "teamName", "participantName", "shortName", "label"):
            if value.get(key):
                return str(value[key]).strip()
    return ""


def pair_from_event(node: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("event", "shortName", "name", "NA", "title"):
        pair = split_pair(node.get(key))
        if pair:
            return pair
    home = scalar_name(node.get("home") or node.get("homeTeam") or node.get("team1"))
    away = scalar_name(node.get("away") or node.get("awayTeam") or node.get("team2"))
    return (home, away) if home and away else None


def odds_tokens(value: Any, path: str = "", out: list[str] | None = None) -> list[str]:
    if out is None:
        out = []
    if len(out) >= 500:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            p = f"{path}.{key}" if path else str(key)
            lk = str(key).casefold()
            if any(token in lk for token in ("odds", "odd", "price", "decimal")) and not isinstance(child, (dict, list)):
                try:
                    num = float(str(child))
                except Exception:
                    num = 0.0
                if 1.0 < abs(num) < 2_000_000:
                    out.append(f"{p}={child}")
            odds_tokens(child, p, out)
    elif isinstance(value, list):
        for idx, child in enumerate(value[:300]):
            odds_tokens(child, f"{path}[{idx}]", out)
    return out


def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fs_rows() -> list[dict[str, Any]]:
    from gool_bot2.providers.flashscore import FlashscoreProvider

    rows = []
    for match in FlashscoreProvider().live_matches():
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


def candidates(payload: Any) -> list[dict[str, Any]]:
    events = payload if isinstance(payload, list) else []
    out: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        pair = pair_from_event(event)
        if not pair:
            continue
        tokens = sorted(set(odds_tokens(event.get("markets") or event)))
        out.append(
            {
                "home": pair[0],
                "away": pair[1],
                "fixture_id": event.get("fixtureId") or event.get("OI") or event.get("id"),
                "score": event.get("score"),
                "time": event.get("time"),
                "period": event.get("period"),
                "quote_tokens": tokens,
                "fingerprint": "|".join(tokens),
            }
        )
    return out


def best(match: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best_row = None
    best_score = 0.0
    for row in rows:
        direct = (sim(match.get("home"), row.get("home")) + sim(match.get("away"), row.get("away"))) / 2
        reverse = (sim(match.get("home"), row.get("away")) + sim(match.get("away"), row.get("home"))) / 2 - 0.015
        score = max(direct, reverse)
        if score > best_score:
            best_score = score
            best_row = row
    if best_score < 0.68:
        return None, best_score
    return best_row, best_score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8485/live?sport=1")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--snapshots", type=int, default=3)
    ap.add_argument("--interval", type=int, default=35)
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()

    snapshots: list[dict[str, Any]] = []
    for index in range(1, max(1, args.snapshots) + 1):
        captured_at = now()
        fs = fs_rows()[: max(1, args.limit)]
        source_error = None
        raw: Any = []
        try:
            raw = get_json(args.api)
        except Exception as exc:
            source_error = f"{type(exc).__name__}: {exc}"
        b365 = candidates(raw)
        matched = []
        for match in fs:
            row, score = best(match, b365)
            if row is None:
                matched.append({"event_id": match["event_id"], "status": "not_found", "mapping_score": round(score, 4)})
            else:
                has_quotes = bool(row.get("quote_tokens"))
                matched.append(
                    {
                        "event_id": match["event_id"],
                        "status": "matched" if has_quotes else "matched_no_quotes",
                        "mapping_score": round(score, 4),
                        "book_event": row,
                        "fingerprint": row.get("fingerprint") or "",
                    }
                )
        snapshots.append(
            {
                "snapshot": index,
                "captured_at": captured_at,
                "source_error": source_error,
                "flashscore": fs,
                "bet365_event_count": len(b365),
                "matches": matched,
            }
        )
        print(f"BET365_WS snapshot={index} fs={len(fs)} bet365={len(b365)} error={source_error or '-'}", flush=True)
        if index < args.snapshots:
            time.sleep(max(1, args.interval))

    event_meta: dict[str, dict[str, Any]] = {}
    match_rows: dict[str, list[dict[str, Any]]] = {}
    for snap in snapshots:
        for match in snap["flashscore"]:
            eid = match["event_id"]
            meta = event_meta.setdefault(eid, {"home": match["home"], "away": match["away"], "league": match["league"], "states": []})
            meta["states"].append({k: match[k] for k in ("minute", "home_score", "away_score")} | {"snapshot": snap["snapshot"], "captured_at": snap["captured_at"]})
        for row in snap["matches"]:
            match_rows.setdefault(row["event_id"], []).append({"snapshot": snap["snapshot"], **row})

    events = []
    counts: dict[str, int] = {}
    for eid, meta in event_meta.items():
        rows = match_rows.get(eid, [])
        quoted = [r for r in rows if r.get("status") == "matched"]
        no_quotes = [r for r in rows if r.get("status") == "matched_no_quotes"]
        fps = [r.get("fingerprint") for r in quoted if r.get("fingerprint")]
        odds_changed = len(set(fps)) > 1
        states = meta["states"]
        fs_progress = len({(s["minute"], s["home_score"], s["away_score"]) for s in states}) > 1
        if odds_changed and fs_progress:
            verdict = "PROVEN"
        elif odds_changed:
            verdict = "ODDS_CHANGED_NO_FS_PROGRESS"
        elif quoted:
            verdict = "FOUND_NO_CHANGE"
        elif no_quotes:
            verdict = "MATCHED_NO_QUOTES"
        else:
            verdict = "NOT_FOUND"
        counts[verdict] = counts.get(verdict, 0) + 1
        events.append({"event_id": eid, "home": meta["home"], "away": meta["away"], "league": meta["league"], "flashscore_states": states, "verdict": verdict, "odds_changed": odds_changed, "flashscore_progress": fs_progress, "snapshots": rows})

    result = {
        "generated_at": now(),
        "method": "joe-bring/bet365-scraper Chrome extension -> OVInPlay WebSocket -> local /live",
        "snapshot_count": len(snapshots),
        "unique_flashscore_live_matches": len(event_meta),
        "bet365_event_counts": [s["bet365_event_count"] for s in snapshots],
        "source_errors": [s["source_error"] for s in snapshots],
        "verdict_counts": counts,
        "events": events,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("BET365_WS_FINAL " + json.dumps({k: result[k] for k in ("unique_flashscore_live_matches", "bet365_event_counts", "source_errors", "verdict_counts")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
