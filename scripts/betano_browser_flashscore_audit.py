from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BETANO_URLS = [
    "https://www.betano.de/live/",
    "https://www.betano.pt/live/",
    "https://www.betano.bet.br/ao-vivo/",
    "https://www.betano.com/live/",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(value: Any) -> str:
    text = str(value or "").casefold().replace("&", " and ")
    text = re.sub(r"\b(fc|cf|sc|afc|fk|sk|club|football|futbol|fussball|deportivo|calcio)\b", " ", text)
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
    home = scalar_name(node.get("home") or node.get("homeTeam") or node.get("team1") or node.get("participant1"))
    away = scalar_name(node.get("away") or node.get("awayTeam") or node.get("team2") or node.get("participant2"))
    if home and away:
        return home, away
    participants = node.get("participants") or node.get("competitors")
    if isinstance(participants, list) and len(participants) >= 2:
        names = [scalar_name(x) for x in participants[:4]]
        names = [x for x in names if x]
        if len(names) >= 2:
            return names[0], names[1]
    for key in ("shortName", "eventName", "event", "name", "title", "label"):
        pair = split_pair(node.get(key))
        if pair:
            return pair
    return None


def quote_tokens(node: Any, path: str = "", price_context: bool = False, out: list[str] | None = None) -> list[str]:
    if out is None:
        out = []
    if len(out) >= 500:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            lk = str(key).casefold().replace("_", "")
            ctx = price_context or any(token in lk for token in ("odds", "odd", "price", "decimal"))
            if ctx and not isinstance(value, (dict, list)):
                try:
                    n = float(str(value))
                except Exception:
                    n = 0.0
                if 1.0 < abs(n) < 2_000_000:
                    out.append(f"{child}={value}")
            quote_tokens(value, child, ctx, out)
    elif isinstance(node, list):
        for i, value in enumerate(node[:300]):
            quote_tokens(value, f"{path}[{i}]", price_context, out)
    return out


def extract_candidates(payloads: list[Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pair = direct_pair(node)
            if pair:
                tokens = sorted(set(quote_tokens(node)))
                event_id = ""
                for key in ("eventId", "event_id", "fixtureId", "fixture_id", "id"):
                    if node.get(key) is not None:
                        event_id = str(node.get(key))
                        break
                found.append(
                    {
                        "home": pair[0],
                        "away": pair[1],
                        "event_id": event_id,
                        "quote_tokens": tokens,
                        "fingerprint": "|".join(tokens),
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for payload in payloads:
        walk(payload)

    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in found:
        key = (norm(item["home"]), norm(item["away"]), item.get("event_id") or "")
        previous = unique.get(key)
        if previous is None or len(item["quote_tokens"]) > len(previous["quote_tokens"]):
            unique[key] = item
    return list(unique.values())


def fs_rows() -> list[dict[str, Any]]:
    from gool_bot2.providers.flashscore import FlashscoreProvider

    rows = []
    for match in FlashscoreProvider().live_matches():
        event_id = str(getattr(match, "provider_match_id", "") or "").strip()
        if event_id:
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


def best(match: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    winner = None
    best_score = 0.0
    for item in candidates:
        direct = (sim(match.get("home"), item.get("home")) + sim(match.get("away"), item.get("away"))) / 2
        reverse = (sim(match.get("home"), item.get("away")) + sim(match.get("away"), item.get("home"))) / 2 - 0.015
        score = max(direct, reverse)
        if score > best_score:
            winner = item
            best_score = score
    return (winner, best_score) if winner is not None and best_score >= 0.68 else (None, best_score)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    snapshots: list[dict[str, Any]] = []
    browser_meta: dict[str, Any] = {"chosen_url": None, "attempts": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        )
        page = await context.new_page()

        chosen = None
        for url in BETANO_URLS:
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)
                title = await page.title()
                html = (await page.content())[:100000].casefold()
                status = response.status if response else 0
                blocked = status in {401, 403, 451} or any(x in html for x in ("access denied", "captcha", "cloudflare", "unavailable in your location"))
                browser_meta["attempts"].append({"url": url, "status": status, "title": title, "blocked": blocked})
                if status and status < 500 and not blocked:
                    chosen = url
                    break
            except Exception as exc:
                browser_meta["attempts"].append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
        browser_meta["chosen_url"] = chosen

        if chosen is None:
            await browser.close()
            return {"generated_at": utc_now(), "browser": browser_meta, "snapshots": [], "summary": {"SOURCE_ERROR": 1}}

        for index in range(1, max(1, args.snapshots) + 1):
            captured_at = utc_now()
            payloads: list[Any] = []
            response_urls: list[str] = []
            ws_frames: list[str] = []
            tasks: set[asyncio.Task[Any]] = set()

            async def capture_response(response: Any) -> None:
                try:
                    ct = str((await response.all_headers()).get("content-type") or "").casefold()
                    if "json" not in ct:
                        return
                    body = await response.body()
                    if not body or len(body) > 5_000_000:
                        return
                    payload = json.loads(body.decode("utf-8", errors="replace"))
                    payloads.append(payload)
                    response_urls.append(response.url)
                except Exception:
                    return

            def on_response(response: Any) -> None:
                task = asyncio.create_task(capture_response(response))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

            def on_ws(ws: Any) -> None:
                def frame(payload: Any) -> None:
                    if len(ws_frames) >= 300:
                        return
                    if isinstance(payload, bytes):
                        text = payload.decode("utf-8", errors="replace")
                    else:
                        text = str(payload)
                    ws_frames.append(text[:200000])
                    stripped = text.strip()
                    if stripped.startswith("{") or stripped.startswith("["):
                        try:
                            payloads.append(json.loads(stripped))
                        except Exception:
                            pass
                ws.on("framereceived", frame)

            page.on("response", on_response)
            page.on("websocket", on_ws)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await page.wait_for_timeout(max(8000, args.capture_seconds * 1000))
            if tasks:
                await asyncio.gather(*list(tasks), return_exceptions=True)
            page.remove_listener("response", on_response)
            page.remove_listener("websocket", on_ws)

            fs = fs_rows()[: max(1, args.limit)]
            cands = extract_candidates(payloads)
            rows = []
            for match in fs:
                cand, score = best(match, cands)
                if cand is None:
                    rows.append({"event_id": match["event_id"], "status": "not_found", "mapping_score": round(score, 4)})
                else:
                    quoted = bool(cand.get("quote_tokens"))
                    rows.append({"event_id": match["event_id"], "status": "matched" if quoted else "matched_no_quotes", "mapping_score": round(score, 4), "book_event": cand, "fingerprint": cand.get("fingerprint") or ""})

            snapshots.append(
                {
                    "snapshot": index,
                    "captured_at": captured_at,
                    "flashscore": fs,
                    "candidate_count": len(cands),
                    "json_payload_count": len(payloads),
                    "response_urls": sorted(set(response_urls))[:100],
                    "websocket_frame_count": len(ws_frames),
                    "matches": rows,
                }
            )
            print(f"BETANO_BROWSER snapshot={index} fs={len(fs)} candidates={len(cands)} json={len(payloads)} ws={len(ws_frames)}", flush=True)
            if index < args.snapshots:
                await page.wait_for_timeout(max(1, args.interval) * 1000)

        await browser.close()

    event_meta: dict[str, dict[str, Any]] = {}
    source_rows: dict[str, list[dict[str, Any]]] = {}
    for snap in snapshots:
        for match in snap["flashscore"]:
            eid = match["event_id"]
            meta = event_meta.setdefault(eid, {"home": match["home"], "away": match["away"], "league": match["league"], "states": []})
            meta["states"].append({"snapshot": snap["snapshot"], "captured_at": snap["captured_at"], "minute": match["minute"], "home_score": match["home_score"], "away_score": match["away_score"]})
        for row in snap["matches"]:
            source_rows.setdefault(row["event_id"], []).append({"snapshot": snap["snapshot"], **row})

    verdict_counts: dict[str, int] = {}
    events = []
    for eid, meta in event_meta.items():
        rows = source_rows.get(eid, [])
        quoted = [r for r in rows if r.get("status") == "matched"]
        no_quotes = [r for r in rows if r.get("status") == "matched_no_quotes"]
        fps = [str(r.get("fingerprint") or "") for r in quoted if str(r.get("fingerprint") or "")]
        odds_changed = len(set(fps)) > 1
        fs_progress = len({(s["minute"], s["home_score"], s["away_score"]) for s in meta["states"]}) > 1
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
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        events.append({"event_id": eid, "home": meta["home"], "away": meta["away"], "league": meta["league"], "flashscore_states": meta["states"], "verdict": verdict, "odds_changed": odds_changed, "flashscore_progress": fs_progress, "snapshots": rows})

    return {
        "generated_at": utc_now(),
        "method": "Betano live page Chromium network JSON/WebSocket capture",
        "browser": browser_meta,
        "snapshot_count": len(snapshots),
        "unique_flashscore_live_matches": len(event_meta),
        "snapshot_diagnostics": [{k: s[k] for k in ("snapshot", "captured_at", "candidate_count", "json_payload_count", "websocket_frame_count", "response_urls")} for s in snapshots],
        "verdict_counts": verdict_counts,
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--interval", type=int, default=25)
    parser.add_argument("--capture-seconds", type=int, default=10)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("BETANO_BROWSER_FINAL " + json.dumps({k: result.get(k) for k in ("browser", "snapshot_count", "unique_flashscore_live_matches", "verdict_counts")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
