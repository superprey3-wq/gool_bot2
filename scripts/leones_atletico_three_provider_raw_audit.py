from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from gool_bot2.archive_flashscore import FlashscoreArchiveBackfill, discover_seed_from_results_url
from gool_bot2.providers.common import UA, http_json, http_text, pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider, MASTER_PATHS, _fields
from gool_bot2.providers.fotmob import BASES as FOTMOB_BASES, FotMobProvider
from gool_bot2.providers.scores365 import BASE as SCORES365_BASE

HOME = "Leones"
AWAY = "Atletico FC"
TARGET_DATE = "2026-09-04"
FOTMOB_DATE = "20260904"
SCORES365_DATE = "04/09/2026"
FLASH_RESULTS_URL = "https://www.flashscore.com/football/colombia/primera-b/results/"
OUT = Path("artifacts/leones_atletico_three_provider_raw")


def save_text(name: str, value: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(value or "", encoding="utf-8")


def save_json(name: str, value: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def team_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("competitorName") or value.get("teamName") or "").strip()
    return str(value or "").strip()


def match_score(home: str, away: str) -> float:
    return pair_score(HOME, AWAY, str(home or ""), str(away or ""))


def scan_flash_master(fs: FlashscoreProvider) -> tuple[str | None, dict[str, Any] | None, dict[str, str]]:
    best_id: str | None = None
    best: dict[str, Any] | None = None
    raw_by_path: dict[str, str] = {}
    best_score = 0.0
    for path in MASTER_PATHS:
        body = fs._feed(path)
        raw_by_path[path] = body
        league = ""
        for chunk in (body or "").split("~"):
            if chunk.startswith("ZA÷"):
                league = _fields(chunk).get("ZA", "").strip()
                continue
            if not chunk.startswith("AA÷"):
                continue
            event_id, sep, rest = chunk[3:].partition("¬")
            if not sep:
                continue
            f = _fields(rest)
            home = (f.get("AE") or f.get("CX") or "").strip()
            away = (f.get("AF") or "").strip()
            score = match_score(home, away)
            if score <= best_score:
                continue
            kickoff_ts = 0
            try:
                kickoff_ts = int(float(str(f.get("AD") or 0)))
            except (TypeError, ValueError):
                pass
            kickoff = datetime.fromtimestamp(kickoff_ts, tz=timezone.utc).isoformat() if kickoff_ts else None
            best_score = score
            best_id = event_id
            best = {
                "source": "master",
                "path": path,
                "event_id": event_id,
                "home": home,
                "away": away,
                "league": league,
                "pair_score": round(score, 4),
                "kickoff": kickoff,
                "fields": f,
                "raw_chunk": chunk,
            }
    return best_id if best_score >= 0.72 else None, best if best_score >= 0.72 else None, raw_by_path


def flashscore_raw(report: dict[str, Any]) -> None:
    fs = FlashscoreProvider()
    info: dict[str, Any] = {"provider": "flashscore", "event_id": None, "errors": []}
    event_id, found, master_raw = scan_flash_master(fs)
    info["master_match"] = found
    for path, body in master_raw.items():
        # Save the full raw day/master feed too: useful for reverse engineering fields.
        save_text(f"flashscore_master_{path}.txt", body)

    if not event_id:
        try:
            seed = discover_seed_from_results_url(FLASH_RESULTS_URL)
            info["season_seed"] = {"tournament_id": seed.tournament_id, "season_id": seed.season_id}
            archive = FlashscoreArchiveBackfill(provider=fs, delay_seconds=0)
            refs = archive.season_matches(seed)
            candidates = []
            for ref in refs:
                score = match_score(ref.home, ref.away)
                if score >= 0.72:
                    candidates.append({
                        "event_id": ref.match_id,
                        "home": ref.home,
                        "away": ref.away,
                        "kickoff": ref.kickoff_at.isoformat(),
                        "league": ref.league,
                        "final_score": [ref.home_score, ref.away_score],
                        "pair_score": round(score, 4),
                    })
            info["season_candidates"] = candidates
            if candidates:
                candidates.sort(key=lambda r: (str(r["kickoff"]).startswith(TARGET_DATE), float(r["pair_score"])), reverse=True)
                event_id = str(candidates[0]["event_id"])
        except Exception as exc:
            info["errors"].append(f"season_discovery:{type(exc).__name__}:{exc}")

    info["event_id"] = event_id
    if event_id:
        feeds = {
            "stats": f"df_st_1_{event_id}",
            "summary_incidents": f"df_sui_1_{event_id}",
            "h2h": f"df_hh_1_{event_id}",
        }
        raw_feeds: dict[str, dict[str, Any]] = {}
        for label, path in feeds.items():
            try:
                body = fs._feed(path)
                save_text(f"flashscore_{label}_{event_id}.txt", body)
                raw_feeds[label] = {"path": path, "bytes": len(body.encode("utf-8")), "nonempty": bool(body)}
            except Exception as exc:
                raw_feeds[label] = {"path": path, "error": f"{type(exc).__name__}:{exc}"}
        info["raw_feeds"] = raw_feeds
        try:
            info["adapter_stats"] = fs.fetch_stats(event_id)
        except Exception as exc:
            info["errors"].append(f"adapter_stats:{type(exc).__name__}:{exc}")
        try:
            info["adapter_goal_timeline"] = fs.fetch_goal_timeline(event_id)
        except Exception as exc:
            info["errors"].append(f"goal_timeline:{type(exc).__name__}:{exc}")
        try:
            code, page = http_text(
                f"https://www.flashscore.com/match/{event_id}/",
                headers={"User-Agent": UA, "Accept": "text/html,*/*", "Referer": "https://www.flashscore.com/"},
                timeout=15,
            )
            save_text(f"flashscore_match_page_{event_id}.html", page)
            info["match_page_http"] = code
            info["match_page_bytes"] = len(page.encode("utf-8"))
        except Exception as exc:
            info["errors"].append(f"match_page:{type(exc).__name__}:{exc}")
    report["flashscore"] = info


def fotmob_raw(report: dict[str, Any]) -> None:
    fm = FotMobProvider()
    info: dict[str, Any] = {"provider": "fotmob", "match_id": None, "errors": []}
    try:
        rows = fm._rows(FOTMOB_DATE)
        save_json("fotmob_daily_matches.json", rows)
        ranked = []
        for row in rows:
            home = team_name(row.get("home") or row.get("homeName"))
            away = team_name(row.get("away") or row.get("awayName"))
            score = match_score(home, away)
            if score >= 0.60:
                ranked.append({"pair_score": score, "row": row})
        ranked.sort(key=lambda x: float(x["pair_score"]), reverse=True)
        info["candidate_count"] = len(ranked)
        info["candidates"] = [
            {
                "pair_score": round(float(item["pair_score"]), 4),
                "id": item["row"].get("id") or item["row"].get("matchId"),
                "home": team_name(item["row"].get("home") or item["row"].get("homeName")),
                "away": team_name(item["row"].get("away") or item["row"].get("awayName")),
                "status": item["row"].get("status"),
                "statusId": item["row"].get("statusId"),
                "utcTime": item["row"].get("utcTime"),
            }
            for item in ranked[:10]
        ]
        if ranked and float(ranked[0]["pair_score"]) >= 0.72:
            row = ranked[0]["row"]
            match_id = str(row.get("id") or row.get("matchId") or "")
            info["match_id"] = match_id
            save_json("fotmob_match_row.json", row)
            if match_id:
                detail = fm._detail(match_id)
                save_json(f"fotmob_match_details_{match_id}.json", detail)
                info["detail_top_keys"] = sorted(detail.keys()) if isinstance(detail, dict) else []
                info["detail_bytes"] = len(json.dumps(detail, ensure_ascii=False, default=str).encode("utf-8"))
                content = detail.get("content") or {} if isinstance(detail, dict) else {}
                shotmap = content.get("shotmap") or {} if isinstance(content, dict) else {}
                shots = shotmap.get("shots") or [] if isinstance(shotmap, dict) else []
                info["shot_count"] = len(shots) if isinstance(shots, list) else 0
                info["content_keys"] = sorted(content.keys()) if isinstance(content, dict) else []
                info["has_momentum_key"] = "momentum" in json.dumps(detail, ensure_ascii=False).lower()
                info["has_lineup_key"] = "lineup" in json.dumps(detail, ensure_ascii=False).lower()
                info["has_rating_key"] = "rating" in json.dumps(detail, ensure_ascii=False).lower()
    except Exception as exc:
        info["errors"].append(f"{type(exc).__name__}:{exc}")
    report["fotmob"] = info


def scores365_get(path: str, params: dict[str, Any]) -> tuple[int, Any]:
    url = SCORES365_BASE + "/" + path.lstrip("/") + "?" + urlencode(params)
    return http_json(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": "https://www.365scores.com/"},
        timeout=15,
    )


def scores365_raw(report: dict[str, Any]) -> None:
    info: dict[str, Any] = {"provider": "365scores", "game_id": None, "errors": []}
    base_params = {
        "appTypeId": 5,
        "langId": 1,
        "timezoneName": "Etc/UTC",
        "userCountryId": -1,
        "sports": 1,
        "startDate": SCORES365_DATE,
        "endDate": SCORES365_DATE,
    }
    try:
        code, data = scores365_get("games/", base_params)
        if code != 200 or not isinstance(data, dict):
            # Some versions also accept ISO dates; keep a fallback in the audit.
            iso = dict(base_params)
            iso["startDate"] = TARGET_DATE
            iso["endDate"] = TARGET_DATE
            code, data = scores365_get("games/", iso)
        save_json("365scores_games_date.json", {"http": code, "payload": data})
        games = data.get("games") or [] if isinstance(data, dict) else []
        ranked = []
        for game in games:
            if not isinstance(game, dict):
                continue
            home = team_name(game.get("homeCompetitor") or game.get("home"))
            away = team_name(game.get("awayCompetitor") or game.get("away"))
            score = match_score(home, away)
            if score >= 0.60:
                ranked.append({"pair_score": score, "game": game})
        ranked.sort(key=lambda x: float(x["pair_score"]), reverse=True)
        info["http_games"] = code
        info["games_count"] = len(games)
        info["candidates"] = [
            {
                "pair_score": round(float(item["pair_score"]), 4),
                "id": item["game"].get("id"),
                "home": team_name(item["game"].get("homeCompetitor") or item["game"].get("home")),
                "away": team_name(item["game"].get("awayCompetitor") or item["game"].get("away")),
                "statusGroup": item["game"].get("statusGroup"),
                "startTime": item["game"].get("startTime"),
            }
            for item in ranked[:10]
        ]
        if ranked and float(ranked[0]["pair_score"]) >= 0.72:
            game = ranked[0]["game"]
            game_id = str(game.get("id") or "")
            info["game_id"] = game_id
            save_json("365scores_match_row.json", game)
            matchup_id = game.get("matchupId") or game.get("matchup") or None
            params = {
                "appTypeId": 5,
                "langId": 1,
                "timezoneName": "Etc/UTC",
                "userCountryId": -1,
                "gameId": game_id,
                "topBookmaker": 14,
            }
            if matchup_id not in (None, ""):
                params["matchupId"] = matchup_id
            dcode, detail = scores365_get("game/", params)
            save_json(f"365scores_game_detail_{game_id}.json", {"http": dcode, "payload": detail})
            info["detail_http"] = dcode
            game_detail = detail.get("game") or {} if isinstance(detail, dict) else {}
            info["detail_top_keys"] = sorted(game_detail.keys()) if isinstance(game_detail, dict) else []
            info["detail_bytes"] = len(json.dumps(game_detail, ensure_ascii=False, default=str).encode("utf-8"))
            scode, stats = scores365_get(
                "game/stats/",
                {
                    "appTypeId": 5,
                    "langId": 1,
                    "timezoneName": "Etc/UTC",
                    "userCountryId": -1,
                    "games": game_id,
                },
            )
            save_json(f"365scores_game_stats_{game_id}.json", {"http": scode, "payload": stats})
            info["stats_http"] = scode
            info["stats_top_keys"] = sorted(stats.keys()) if isinstance(stats, dict) else []
            chart = game_detail.get("chartEvents") or {} if isinstance(game_detail, dict) else {}
            info["chart_event_count"] = len(chart.get("events") or []) if isinstance(chart, dict) else 0
            info["event_count"] = len(game_detail.get("events") or []) if isinstance(game_detail, dict) else 0
            info["member_count"] = len(game_detail.get("members") or []) if isinstance(game_detail, dict) else 0
    except Exception as exc:
        info["errors"].append(f"{type(exc).__name__}:{exc}")
    report["365scores"] = info


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": {"home": HOME, "away": AWAY, "date": TARGET_DATE},
    }
    flashscore_raw(report)
    fotmob_raw(report)
    scores365_raw(report)
    save_json("report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str), flush=True)
    print("ARTIFACT_FILES", flush=True)
    for path in sorted(OUT.glob("*")):
        print(f"{path.name}\t{path.stat().st_size}", flush=True)


if __name__ == "__main__":
    main()
