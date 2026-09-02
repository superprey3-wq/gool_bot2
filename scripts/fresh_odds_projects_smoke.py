from __future__ import annotations

import argparse
import asyncio
import json
import os
import posixpath
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _load_matches(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _flashscore_candidate_urls(event_id: str, meta: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    hs = str(meta.get("home_team_slug") or "").strip(" /")
    hi = str(meta.get("home_team_id") or "").strip(" /")
    aws = str(meta.get("away_team_slug") or "").strip(" /")
    ai = str(meta.get("away_team_id") or "").strip(" /")
    if hs and hi and aws and ai:
        # Current 2026 Flashscore match URLs identify both team profiles in the path
        # and use ?mid=<event id>. Try both participant orders because Flashscore's
        # canonical path order is not consistently home/away.
        for left, right in ((f"{hs}-{hi}", f"{aws}-{ai}"), (f"{aws}-{ai}", f"{hs}-{hi}")):
            urls.append(f"https://www.flashscore.com/match/football/{left}/{right}/?mid={event_id}")
    # Keep the event-id-only form because M3MONs documents it. It is tried last.
    urls.append(f"https://www.flashscore.com/match/football/{event_id}/")
    return list(dict.fromkeys(urls))


def discover_live(output: Path, limit: int) -> int:
    from gool_bot2.providers.flashscore import FlashscoreProvider

    provider = FlashscoreProvider()
    matches = provider.live_matches()
    rows: list[dict[str, Any]] = []
    for match in matches:
        minute = int(getattr(match, "minute", 0) or 0)
        if minute <= 0 or minute > 90:
            continue
        event_id = str(getattr(match, "provider_match_id", "") or "").strip()
        if not event_id:
            continue
        meta = dict(getattr(match, "meta", {}) or {})
        urls = _flashscore_candidate_urls(event_id, meta)
        rows.append(
            {
                "event_id": event_id,
                "home": str(getattr(match, "home", "") or ""),
                "away": str(getattr(match, "away", "") or ""),
                "league": str(getattr(match, "league", "") or ""),
                "minute": minute,
                "home_score": int(getattr(match, "home_score", 0) or 0),
                "away_score": int(getattr(match, "away_score", 0) or 0),
                "flashscore_url": urls[0],
                "flashscore_urls": urls,
                "flashscore_meta": {
                    "home_team_slug": meta.get("home_team_slug"),
                    "home_team_id": meta.get("home_team_id"),
                    "away_team_slug": meta.get("away_team_slug"),
                    "away_team_id": meta.get("away_team_id"),
                },
            }
        )

    # Odds are most useful while markets are still open. Prefer real mid-game
    # matches (10'-70') instead of the 80'-90' tail where books often suspend them.
    def rank(row: dict[str, Any]) -> tuple[Any, ...]:
        minute = int(row.get("minute") or 0)
        prime = 0 if 10 <= minute <= 70 else 1
        return (prime, abs(minute - 40), -minute, str(row.get("league") or ""), str(row.get("home") or ""))

    rows.sort(key=rank)
    rows = rows[: max(1, limit)]
    _write(output, rows)
    print(f"FRESH_ODDS_DISCOVER live={len(matches)} selected={len(rows)} output={output}")
    for row in rows:
        print(
            f"  {row['minute']:>2}' {row['home']} - {row['away']} "
            f"{row['home_score']}:{row['away_score']} id={row['event_id']} url={row['flashscore_url']}"
        )
    return 0 if rows else 2


def _m3_current_route_builder(event_url: str, odds_key: str) -> str:
    """Adapt M3MONs URL builder to Flashscore's current 2026 /full-time route."""
    parsed = urlparse(event_url)
    base = parsed.path.rstrip("/")
    new_path = posixpath.join(base, "odds", odds_key, "full-time") + "/"
    # Keep ?mid=... because current team-profile URLs use it to select the event.
    return urlunparse(parsed._replace(path=new_path, fragment=""))


def run_m3mons(matches_path: Path, source_dir: Path, output: Path, limit: int) -> int:
    sys.path.insert(0, str(source_dir))
    from flashscorescraper import scrape_odds
    from models.odds_filter import OddsFilter
    from utils.url_builder import FlashscoreUrlBuilder

    original_builder = FlashscoreUrlBuilder.build_odds_url
    rows: list[dict[str, Any]] = []
    use_playwright = os.getenv("M3MONS_PLAYWRIGHT", "0").strip().lower() in {"1", "true", "yes"}

    for match in _load_matches(matches_path)[:limit]:
        item: dict[str, Any] = {"match": match, "source": "M3MONs/FlashscoreScraper", "attempts": []}
        urls = list(match.get("flashscore_urls") or [match.get("flashscore_url")])
        best_payload: dict[str, Any] | None = None
        best_success: list[dict[str, Any]] = []

        attempts: list[tuple[str, str, bool]] = []
        for url in urls:
            if url:
                attempts.append((str(url), "curl", False))
        for url in urls:
            if url:
                attempts.append((str(url), "curl", True))
        if use_playwright and urls:
            attempts.append((str(urls[0]), "playwright", True))

        for url, engine, current_route in attempts:
            try:
                FlashscoreUrlBuilder.build_odds_url = staticmethod(
                    _m3_current_route_builder if current_route else original_builder
                )
                result = scrape_odds(
                    url,
                    sport="football",
                    engine=engine,
                    timeout=20,
                    odds_filter=OddsFilter(odds=["over-under", "both-teams-to-score"]),
                )
                payload = _jsonable(result)
                successful = [
                    x for x in payload.get("odds", [])
                    if isinstance(x, dict) and not x.get("error") and x.get("data")
                ]
                item["attempts"].append(
                    {
                        "url": url,
                        "engine": engine,
                        "current_route": current_route,
                        "successful_markets": len(successful),
                        "errors": [x.get("error") for x in payload.get("odds", []) if isinstance(x, dict) and x.get("error")],
                    }
                )
                if len(successful) > len(best_success):
                    best_payload = payload
                    best_success = successful
                if successful:
                    break
            except Exception as exc:
                item["attempts"].append(
                    {"url": url, "engine": engine, "current_route": current_route, "error": f"{type(exc).__name__}: {exc}"}
                )
                traceback.print_exc()
            finally:
                FlashscoreUrlBuilder.build_odds_url = staticmethod(original_builder)

        item.update(
            {
                "ok": bool(best_success),
                "source_ok": bool(best_success),
                "successful_markets": len(best_success),
                "payload": best_payload,
            }
        )
        rows.append(item)
        print(
            f"M3MONS match={match.get('home')} - {match.get('away')} "
            f"ok={item['ok']} markets={item['successful_markets']} attempts={len(item['attempts'])}"
        )
    _write(output, rows)
    return 0


async def _run_realine_async(matches: list[dict[str, Any]], source_dir: Path, limit: int) -> list[dict[str, Any]]:
    sys.path.insert(0, str(source_dir / "src"))
    from flashscore_bot.common_scraper import fetch_all_odds_data

    rows: list[dict[str, Any]] = []
    bet_types = {"over-under": True, "both-teams-to-score": True}
    for match in matches[:limit]:
        item: dict[str, Any] = {"match": match, "source": "realine0/flashscore-football-odds-scraper"}
        try:
            payload = await fetch_all_odds_data(str(match["event_id"]), ["bet365"], bet_types)
            flat = _jsonable(payload)
            has_values = bool(flat) if isinstance(flat, dict) else bool(flat)
            item.update({"ok": has_values, "source_ok": has_values, "odds_fields": len(flat) if isinstance(flat, dict) else 0, "payload": flat})
        except Exception as exc:
            item.update({"ok": False, "source_ok": False, "error": f"{type(exc).__name__}: {exc}"})
            traceback.print_exc()
        rows.append(item)
        print(
            f"REALINE match={match.get('home')} - {match.get('away')} "
            f"ok={item.get('ok')} fields={item.get('odds_fields', 0)} error={item.get('error', '-')}"
        )
    return rows


def run_realine(matches_path: Path, source_dir: Path, output: Path, limit: int) -> int:
    rows = asyncio.run(_run_realine_async(_load_matches(matches_path), source_dir, limit))
    _write(output, rows)
    return 0


def run_pinnacle(matches_path: Path, source_dir: Path, output: Path, limit: int) -> int:
    sys.path.insert(0, str(source_dir))
    import pinnacle_scraper as ps

    matches = _load_matches(matches_path)[:limit]
    targets = [(str(m.get("home") or ""), str(m.get("away") or "")) for m in matches]
    rows: list[dict[str, Any]] = []
    try:
        matchups, markets, related = ps.scrape_matches(targets)
    except Exception as exc:
        traceback.print_exc()
        rows = [
            {
                "match": match,
                "source": "ACHBIDHAN/Pinnacle_Football_Odds_Scraper",
                "ok": False,
                "source_ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            for match in matches
        ]
        _write(output, rows)
        return 0

    source_ok = bool(markets)
    source_meta = {
        "captured_matchups": len(matchups),
        "captured_market_rows": len(markets),
        "captured_related_main_matches": len(related),
    }
    for match in matches:
        item: dict[str, Any] = {
            "match": match,
            "source": "ACHBIDHAN/Pinnacle_Football_Odds_Scraper",
            "source_ok": source_ok,
            "source_meta": source_meta,
        }
        found = ps.find_matchup(matchups, str(match.get("home") or ""), str(match.get("away") or ""))
        if not found:
            item.update({"ok": False, "error": "match_not_found_on_pinnacle"})
        else:
            try:
                parsed = ps.parse_match(found, markets, related)
                item.update({"ok": bool(parsed.get("markets")), "payload": parsed})
            except Exception as exc:
                item.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        rows.append(item)
        print(
            f"PINNACLE match={match.get('home')} - {match.get('away')} "
            f"match_ok={item.get('ok')} source_ok={source_ok} error={item.get('error', '-')}"
        )
    _write(output, rows)
    return 0


def summarize(inputs: list[Path], output: Path) -> int:
    summary: dict[str, Any] = {"sources": {}, "best_sources": []}
    for path in inputs:
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        source = str(rows[0].get("source") if rows else path.stem)
        ok = sum(1 for row in rows if isinstance(row, dict) and row.get("ok"))
        source_ok = any(bool(row.get("source_ok")) for row in rows if isinstance(row, dict))
        total = len(rows)
        summary["sources"][source] = {
            "source_retrieved_odds": source_ok,
            "matched_online": ok,
            "tested_online": total,
            "match_coverage": (ok / total if total else 0.0),
        }
    summary["best_sources"] = [
        source
        for source, stats in sorted(
            summary["sources"].items(),
            key=lambda item: (
                not bool(item[1]["source_retrieved_odds"]),
                -float(item[1]["match_coverage"]),
                -int(item[1]["matched_online"]),
                item[0],
            ),
        )
        if stats["source_retrieved_odds"]
    ]
    _write(output, summary)
    print("FRESH_ODDS_SUMMARY")
    for source, stats in summary["sources"].items():
        print(
            f"  {source}: source_odds={stats['source_retrieved_odds']} "
            f"online={stats['matched_online']}/{stats['tested_online']} coverage={stats['match_coverage']:.0%}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test only fresh 2026 odds projects against current Flashscore live matches")
    parser.add_argument("mode", choices=["discover", "m3mons", "realine", "pinnacle", "summary"])
    parser.add_argument("--matches", type=Path, default=Path("fresh_odds_results/online_matches.json"))
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=int(os.getenv("FRESH_ODDS_MAX_MATCHES", "8")))
    parser.add_argument("--inputs", nargs="*", type=Path, default=[])
    args = parser.parse_args()

    if args.mode == "discover":
        return discover_live(args.output, args.limit)
    if args.mode == "m3mons":
        return run_m3mons(args.matches, args.source_dir or Path("fresh_sources/m3mons"), args.output, args.limit)
    if args.mode == "realine":
        return run_realine(args.matches, args.source_dir or Path("fresh_sources/realine"), args.output, args.limit)
    if args.mode == "pinnacle":
        return run_pinnacle(args.matches, args.source_dir or Path("fresh_sources/pinnacle"), args.output, args.limit)
    return summarize(args.inputs, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
