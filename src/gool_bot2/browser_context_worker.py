from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .providers import Scores365Provider


RUNNING = True


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().casefold() in {"1", "true", "yes", "on"}


def _state_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return Path(os.getenv("GOOL_BROWSER_CONTEXT_PATH", str(runtime / "live" / "browser_context.json")))


def _raw_dir() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return Path(os.getenv("RAW_LIVE_DIR", str(runtime / "raw" / "live")))


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy_row(row: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, (int, float)) and value != 0:
            return True
        if isinstance(value, str) and value.strip().casefold() in {"true", "yes", "1", "big chance", "bigchance"}:
            return True
    return False


def _on_target(shot: dict[str, Any]) -> bool:
    if _truthy_row(shot, "isOnTarget", "onTarget"):
        return True
    text = " ".join(str(shot.get(k) or "") for k in ("result", "type", "description", "eventTypeName")).casefold()
    return any(token in text for token in ("goal", "saved", "save", "on target"))


def _inside_box(shot: dict[str, Any]) -> bool:
    if _truthy_row(shot, "isInsideBox", "insideBox", "isInBox"):
        return True
    text = " ".join(str(shot.get(k) or "") for k in ("area", "location", "shotArea", "description")).casefold()
    return "inside" in text and "box" in text


def _stats_from_game(game: dict[str, Any]) -> dict[str, list[float]]:
    home_id = (game.get("homeCompetitor") or {}).get("id")
    away_id = (game.get("awayCompetitor") or {}).get("id")
    shots = [0.0, 0.0]
    sot = [0.0, 0.0]
    inside = [0.0, 0.0]
    big = [0.0, 0.0]
    xg = [0.0, 0.0]
    xgot = [0.0, 0.0]
    high_xg = [0.0, 0.0]
    has_xg = False
    has_xgot = False
    events = ((game.get("chartEvents") or {}).get("events") or []) if isinstance(game, dict) else []
    for shot in events:
        if not isinstance(shot, dict):
            continue
        cid = shot.get("competitorId")
        if cid == home_id:
            side = 0
        elif cid == away_id:
            side = 1
        else:
            continue
        shots[side] += 1
        if _on_target(shot):
            sot[side] += 1
        if _inside_box(shot):
            inside[side] += 1
        if _truthy_row(shot, "isBigChance", "bigChance"):
            big[side] += 1
        xv = _num(shot.get("xg"))
        if xv is not None:
            xg[side] += max(0.0, xv)
            high_xg[side] += 1 if xv >= 0.20 else 0
            has_xg = True
        xgv = _num(shot.get("xgot"))
        if xgv is not None:
            xgot[side] += max(0.0, xgv)
            has_xgot = True
    stats: dict[str, list[float]] = {
        "shots": shots,
        "shotmap_shots": shots,
        "shots_on_target": sot,
        "shots_inside_box": inside,
        "big_chances": big,
        "high_xg_shots": high_xg,
    }
    if has_xg:
        stats["xg"] = [round(xg[0], 3), round(xg[1], 3)]
    if has_xgot:
        stats["xgot"] = [round(xgot[0], 3), round(xgot[1], 3)]
    return stats


def _trim_trends(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("trends") if isinstance(payload, dict) else None
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "id": row.get("id"),
            "lineTypeId": row.get("lineTypeId"),
            "text": str(row.get("text") or "")[:300],
            "cause": str(row.get("cause") or "")[:200],
            "betCTA": str(row.get("betCTA") or "")[:120],
            "percentage": _num(row.get("percentage")),
            "competitorIds": list(row.get("competitorIds") or [])[:4],
            "isGeneralGameBet": bool(row.get("isGeneralGameBet")),
        })
    return out[:40]


def _slug(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "match"


def _match_url(game: dict[str, Any]) -> str:
    home = game.get("homeCompetitor") or {}
    away = game.get("awayCompetitor") or {}
    comp_id = int(game.get("competitionId") or 0)
    comp_slug = str(game.get("competitionNameForURL") or game.get("competitionDisplayName") or "competition")
    comp_slug = _slug(comp_slug)
    hslug = str(home.get("nameForURL") or home.get("name") or "home")
    aslug = str(away.get("nameForURL") or away.get("name") or "away")
    hid = int(home.get("id") or 0)
    aid = int(away.get("id") or 0)
    return f"https://www.365scores.com/football/match/{comp_slug}-{comp_id}/{_slug(hslug)}-{_slug(aslug)}-{hid}-{aid}-{comp_id}"


def _read_tail(path: Path, max_bytes: int = 4_000_000) -> list[str]:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            start = max(0, size - max_bytes)
            handle.seek(start)
            data = handle.read()
        if start:
            _, _, data = data.partition(b"\n")
        return data.decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def _latest_records() -> list[dict[str, Any]]:
    paths = sorted(_raw_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime if p.exists() else 0)[-2:]
    latest: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line in _read_tail(path):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            match = row.get("match") or {}
            mid = str(match.get("flashscore_event_id") or "").strip()
            minute = int(match.get("minute") or 0)
            if not mid or minute <= 0 or bool(match.get("is_halftime")) or bool(match.get("is_finished")):
                continue
            if not (1 <= minute <= 35 or 46 <= minute <= 75):
                continue
            latest[mid] = row
    return list(latest.values())


def _provider_count(record: dict[str, Any], key: str) -> int:
    count = 0
    for provider in (record.get("providers") or {}).values():
        if isinstance(provider, dict) and key in (provider.get("stats") or {}):
            count += 1
    return count


def _priority(record: dict[str, Any]) -> tuple[int, int]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    total = int(match.get("home_score") or 0) + int(match.get("away_score") or 0)
    score = 0
    if bool((record.get("prefilter") or {}).get("candidate")):
        score += 5
    if 46 <= minute <= 75:
        score += 4
    if _provider_count(record, "xg") < 2:
        score += 3
    if _provider_count(record, "shots_on_target") < 2:
        score += 2
    if total >= 1:
        score += 1
    if minute >= 60:
        score += 1
    return score, minute


def _process_tree_rss_mb(root_pid: int) -> float:
    parent: dict[int, int] = {}
    rss: dict[int, int] = {}
    proc = Path("/proc")
    for child in proc.iterdir() if proc.exists() else []:
        if not child.name.isdigit():
            continue
        try:
            status = (child / "status").read_text("utf-8", errors="ignore")
        except OSError:
            continue
        ppid = 0
        kb = 0
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
            elif line.startswith("VmRSS:"):
                kb = int(line.split()[1])
        pid = int(child.name)
        parent[pid] = ppid
        rss[pid] = kb
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parent.items():
            if ppid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return round(sum(rss.get(pid, 0) for pid in selected) / 1024.0, 1)


def _load_state() -> dict[str, Any]:
    path = _state_path()
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("matches", {})
    return payload


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _prune(state: dict[str, Any]) -> None:
    rows = state.get("matches") or {}
    if not isinstance(rows, dict):
        state["matches"] = {}
        return
    cutoff = time.time() - max(300.0, float(os.getenv("GOOL_BROWSER_STATE_RETENTION_SECONDS", "900")))
    state["matches"] = {
        key: value for key, value in rows.items()
        if isinstance(value, dict) and float(value.get("captured_epoch") or 0) >= cutoff
    }


async def _browser_json(page: Any, url: str) -> tuple[int, Any]:
    """Fetch through Playwright's BrowserContext request session.

    BrowserContext.request shares the browser context's cookie storage but is not
    subject to the page's cross-origin fetch/CORS restrictions. This mirrors the
    web app's API traffic while remaining reliable in headless production.
    """
    response = await page.context.request.get(
        url,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.365scores.com/",
        },
        timeout=12_000,
    )
    status = int(response.status)
    try:
        payload = json.loads(await response.text())
    except Exception:
        payload = {}
    return status, payload


async def _collect_one(page: Any, provider: Scores365Provider, record: dict[str, Any]) -> dict[str, Any] | None:
    match = record.get("match") or {}
    home = str(match.get("home") or "").strip()
    away = str(match.get("away") or "").strip()
    hit, score = provider.find_match(home, away)
    if not isinstance(hit, dict) or not hit.get("id"):
        return None
    game_id = str(hit.get("id"))
    page_url = _match_url(hit)
    response = await page.goto(page_url, wait_until="domcontentloaded", timeout=35_000)
    await page.wait_for_timeout(2500)

    common = {"appTypeId": 5, "langId": 1, "timezoneName": "UTC", "userCountryId": 321}
    game_url = "https://webws.365scores.com/web/game/?" + urlencode({**common, "gameId": game_id, "topBookmaker": 103})
    trend_url = "https://webws.365scores.com/web/trends/?" + urlencode({**common, "games": game_id, "topBookmaker": 103})
    game_status, game_payload = await _browser_json(page, game_url)
    trend_status, trend_payload = await _browser_json(page, trend_url)
    game = (game_payload.get("game") or {}) if isinstance(game_payload, dict) else {}
    stats = _stats_from_game(game)
    trends = _trim_trends(trend_payload)
    body_terms: list[str] = []
    try:
        body = (await page.locator("body").inner_text(timeout=3000))[:120_000]
        low = body.casefold()
        for term in ("stats", "trends", "insights", "expected goals", "shots on target", "over 2.5", "both teams"):
            if term in low:
                body_terms.append(term)
    except Exception:
        pass
    return {
        "captured_epoch": time.time(),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "minute": int(match.get("minute") or 0),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "home": home,
        "away": away,
        "scores365_game_id": game_id,
        "match_score": round(float(score or 0.0), 3),
        "page_url": page.url,
        "page_status": None if response is None else int(response.status),
        "game_api_status": game_status,
        "trends_api_status": trend_status,
        "stats": stats,
        "trends": trends,
        "visible_terms": body_terms,
        "source": "playwright_chromium_365scores",
    }


async def run(interval: float) -> int:
    if not _truthy("GOOL_BROWSER_ENABLE", True):
        print("GOOL_BROWSER_CONTEXT disabled", flush=True)
        return 0
    from playwright.async_api import async_playwright

    provider = Scores365Provider()
    state = _load_state()
    max_matches = max(1, min(3, int(os.getenv("GOOL_BROWSER_MAX_MATCHES_PER_CYCLE", "2"))))
    cache_seconds = max(30.0, float(os.getenv("GOOL_BROWSER_MATCH_CACHE_SECONDS", "90")))
    max_rss = max(250.0, float(os.getenv("GOOL_BROWSER_MAX_RSS_MB", "550")))

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-background-networking"],
        )
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1180, "height": 820},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        )

        async def route_handler(route: Any) -> None:
            if route.request.resource_type in {"image", "media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_handler)
        page = await context.new_page()
        print(
            f"GOOL_BROWSER_CONTEXT started engine=chromium max_matches={max_matches} cache={cache_seconds:.0f}s max_rss={max_rss:.0f}MB",
            flush=True,
        )

        while RUNNING:
            started = time.monotonic()
            errors = 0
            processed = 0
            candidates = sorted(_latest_records(), key=_priority, reverse=True)
            now = time.time()
            for record in candidates:
                if processed >= max_matches:
                    break
                match = record.get("match") or {}
                mid = str(match.get("flashscore_event_id") or "").strip()
                old = (state.get("matches") or {}).get(mid) or {}
                if now - float(old.get("captured_epoch") or 0) < cache_seconds:
                    continue
                rss = _process_tree_rss_mb(os.getpid())
                if rss >= max_rss:
                    print(f"GOOL_BROWSER_GUARD rss={rss:.1f}MB limit={max_rss:.0f}MB action=skip_cycle", flush=True)
                    break
                try:
                    row = await _collect_one(page, provider, record)
                    if row is not None:
                        state.setdefault("matches", {})[mid] = row
                        processed += 1
                        print(
                            f"GOOL_BROWSER_MATCH match={mid} minute={row['minute']} score={row['score'][0]}:{row['score'][1]} "
                            f"stats={len(row.get('stats') or {})} trends={len(row.get('trends') or [])} "
                            f"api={row.get('game_api_status')}/{row.get('trends_api_status')}",
                            flush=True,
                        )
                except Exception as exc:
                    errors += 1
                    print(f"GOOL_BROWSER_MATCH_ERROR match={mid} error={type(exc).__name__}:{exc}", flush=True)
                    try:
                        await page.close()
                    except Exception:
                        pass
                    page = await context.new_page()

            _prune(state)
            rss = _process_tree_rss_mb(os.getpid())
            state["health"] = {
                "captured_epoch": time.time(),
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "running": True,
                "rss_mb": rss,
                "max_rss_mb": max_rss,
                "processed": processed,
                "candidates": len(candidates),
                "errors": errors,
                "cycle_ms": int((time.monotonic() - started) * 1000),
            }
            _write_state(state)
            print(
                f"GOOL_BROWSER_HEALTH rss={rss:.1f}MB candidates={len(candidates)} processed={processed} errors={errors} "
                f"cycle={time.monotonic() - started:.1f}s",
                flush=True,
            )
            await asyncio.sleep(max(5.0, interval))

        await context.close()
        await browser.close()
    return 0


def stop(*_: object) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=float(os.getenv("GOOL_BROWSER_INTERVAL_SECONDS", "30")))
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    raise SystemExit(asyncio.run(run(args.interval)))


if __name__ == "__main__":
    main()
