from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse the battle-tested fuzzy matcher and strict quote-token extractor from the
# existing GOOL live audit.  Importing from scripts keeps this experiment isolated
# from the production package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bookmaker_variants_audit as legacy  # noqa: E402

TARGETS = [
    {
        "key": "sport_sebaco_ferretti",
        "home": "Sport Sebaco",
        "away": "Ferretti",
        "country": "Nicaragua",
        "league": "Liga Primera Apertura",
        "home_aliases": ["sport sebaco", "sebaco"],
        "away_aliases": ["ferretti", "walter ferretti", "cd walter ferretti"],
    },
    {
        "key": "olimpia_guarani",
        "home": "Olimpia Asuncion",
        "away": "Guarani",
        "country": "Paraguay",
        "league": "Copa de Primera Clausura",
        "home_aliases": ["olimpia", "olimpia asuncion", "club olimpia"],
        "away_aliases": ["guarani", "club guarani"],
    },
    {
        "key": "deportivo_cuenca_guayaquil_city",
        "home": "Deportivo Cuenca",
        "away": "Guayaquil City",
        "country": "Ecuador",
        "league": "Liga Pro",
        "home_aliases": ["deportivo cuenca", "cuenca"],
        "away_aliases": ["guayaquil city", "guayaquil city fc"],
    },
]

GROUPS = [
    "pinnacle.sports",
    "sportsbet.sports",
    "pointsbet.sports",
    "tab.sports",
    "tab.discovery",
    "fanduel.sportsbook",
    "entain.graphql",
    "betfair.exchange",
    "betfair.navigation",
    "betfair.inplay",
    "dabble.sport",
]

SOURCES = [
    "kambi_unibet",
    "pinnacle_arcadia",
    "sportsbet",
    "pointsbet",
    "tab",
    "fanduel",
    "entain_ladbrokes",
    "betfair_web",
    "dabble",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return jsonable(vars(value))
    return str(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def payload_of(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return jsonable(structured)
    content = getattr(result, "content", None) or []
    if content:
        text = getattr(content[0], "text", None)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return {"text": text}
    return None


def blocked_error(text: str) -> bool:
    s = str(text or "").casefold()
    return any(
        marker in s
        for marker in (
            "403",
            "451",
            "forbidden",
            "cloudflare",
            "geo",
            "location",
            "region restricted",
            "access denied",
            "connection reset",
            "akamai",
        )
    )


async def call_tool(mcp: Any, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        res = await mcp.call_tool(name, args or {})
        return {"state": "ok", "payload": payload_of(res), "error": None}
    except Exception as exc:  # noqa: BLE001 — this is a diagnostics experiment
        text = f"{type(exc).__name__}: {exc}"
        return {"state": "blocked" if blocked_error(text) else "error", "payload": None, "error": text[:1200]}


def fs_rows() -> list[dict[str, Any]]:
    return legacy.flashscore_rows()


def target_score(row: dict[str, Any], target: dict[str, Any]) -> float:
    direct = (legacy.sim(row.get("home"), target["home"]) + legacy.sim(row.get("away"), target["away"])) / 2
    reverse = (legacy.sim(row.get("home"), target["away"]) + legacy.sim(row.get("away"), target["home"])) / 2 - 0.02
    return max(direct, reverse)


def select_fs_targets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    out: dict[str, dict[str, Any] | None] = {}
    for target in TARGETS:
        ranked = sorted(((target_score(row, target), row) for row in rows), key=lambda x: x[0], reverse=True)
        out[target["key"]] = ranked[0][1] if ranked and ranked[0][0] >= 0.66 else None
    return out


def aliases_match_text(text: str, target: dict[str, Any]) -> bool:
    n = legacy.norm(text)
    if not n:
        return False
    home = max((legacy.sim(alias, n) if len(n.split()) <= 7 else (0.95 if legacy.norm(alias) in n else 0.0)) for alias in target["home_aliases"])
    away = max((legacy.sim(alias, n) if len(n.split()) <= 7 else (0.95 if legacy.norm(alias) in n else 0.0)) for alias in target["away_aliases"])
    return home >= 0.72 and away >= 0.72


def deep_target_node(node: Any, target: dict[str, Any], depth: int = 0) -> dict[str, Any] | None:
    if depth > 14:
        return None
    if isinstance(node, dict):
        # Prefer the deepest/smallest object that contains both participants.
        for value in node.values():
            hit = deep_target_node(value, target, depth + 1)
            if hit is not None:
                return hit
        try:
            text = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(node)
        if len(text) <= 900_000 and aliases_match_text(text, target):
            return node
    elif isinstance(node, list):
        for item in node:
            hit = deep_target_node(item, target, depth + 1)
            if hit is not None:
                return hit
    return None


def event_id_from_node(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    for key in (
        "eventId",
        "eventID",
        "event_id",
        "fixtureId",
        "fixtureID",
        "masterEventId",
        "MasterEventId",
        "key",
        "id",
        "nodeId",
    ):
        value = node.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def exact_candidate(match: dict[str, Any], target: dict[str, Any], data: Any) -> dict[str, Any] | None:
    candidates = legacy.extract_candidates(data)
    candidate, score, reversed_order = legacy.best_candidate(match, candidates)
    if candidate is not None:
        return {**candidate, "mapping_score": round(score, 4), "reversed_order": reversed_order, "node": None}
    node = deep_target_node(data, target)
    if node is None:
        return None
    quotes = sorted(set(legacy.quote_tokens(node)))
    return {
        "home": target["home"],
        "away": target["away"],
        "event_id": event_id_from_node(node),
        "quote_tokens": quotes,
        "quote_count": len(quotes),
        "mapping_score": None,
        "reversed_order": False,
        "node": node,
    }


def quote_result(candidate: dict[str, Any] | None, detail_payload: Any = None, *, source_state: str = "ok", error: str | None = None) -> dict[str, Any]:
    if source_state == "blocked":
        return {"status": "source_blocked", "error": error}
    if source_state == "error":
        return {"status": "source_error", "error": error}
    if candidate is None:
        return {"status": "not_found"}
    quotes = []
    if detail_payload is not None:
        quotes.extend(legacy.quote_tokens(detail_payload))
    quotes.extend(candidate.get("quote_tokens") or [])
    quotes = sorted(set(quotes))[:240]
    return {
        "status": "matched" if quotes else "matched_no_quotes",
        "book_event_id": candidate.get("event_id"),
        "book_home": candidate.get("home"),
        "book_away": candidate.get("away"),
        "mapping_score": candidate.get("mapping_score"),
        "quote_count": len(quotes),
        "quote_tokens": quotes,
        "fingerprint": "|".join(quotes),
    }


def source_shell(state: str, error: str | None, rows: dict[str, Any], diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": state, "error": error, "matches": rows, "diagnostics": diagnostics or {}}


async def source_kambi(fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    result = await asyncio.to_thread(legacy.fetch_source, "kambi_unibet")
    rows: dict[str, Any] = {}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        candidate, score, rev = legacy.best_candidate(match, list(result.get("candidates") or []))
        if candidate is None:
            state = result.get("state")
            rows[target["key"]] = quote_result(None, source_state=state, error=json.dumps(result.get("attempts"), ensure_ascii=False)[:900])
            continue
        candidate = {**candidate, "mapping_score": round(score, 4), "reversed_order": rev}
        rows[target["key"]] = quote_result(candidate)
    return source_shell(str(result.get("state") or "error"), None, rows, {"chosen_url": result.get("chosen_url"), "attempts": result.get("attempts")})


async def source_pinnacle(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    live = await call_tool(mcp, "pinnacle_sports_live")
    if live["state"] != "ok":
        return source_shell(live["state"], live["error"], {t["key"]: quote_result(None, source_state=live["state"], error=live["error"]) for t in TARGETS})
    sports = live["payload"] if isinstance(live["payload"], list) else []
    soccer = next((s for s in sports if "soccer" in legacy.norm(s.get("name")) or "football" in legacy.norm(s.get("name"))), None)
    if soccer is None:
        return source_shell("ok", None, {t["key"]: {"status": "not_found"} for t in TARGETS}, {"sports_live": sports})
    matchups = await call_tool(mcp, "pinnacle_sport_matchups_live", {"sportId": soccer.get("id")})
    if matchups["state"] != "ok":
        return source_shell(matchups["state"], matchups["error"], {t["key"]: quote_result(None, source_state=matchups["state"], error=matchups["error"]) for t in TARGETS})
    rows: dict[str, Any] = {}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        cand = exact_candidate(match, target, matchups["payload"])
        detail = None
        if cand and str(cand.get("event_id") or "").isdigit():
            market = await call_tool(mcp, "pinnacle_matchup_markets", {"matchupId": int(cand["event_id"])})
            if market["state"] == "ok":
                detail = market["payload"]
        rows[target["key"]] = quote_result(cand, detail)
    return source_shell("ok", None, rows, {"live_matchup_count": len(matchups["payload"] or []) if isinstance(matchups["payload"], list) else None})


async def source_sportsbet(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    feed = await call_tool(mcp, "sportsbet_bet_live", {"birType": "BETLIVE", "excludeNonLiveEvents": True})
    if feed["state"] != "ok":
        return source_shell(feed["state"], feed["error"], {t["key"]: quote_result(None, source_state=feed["state"], error=feed["error"]) for t in TARGETS})
    rows: dict[str, Any] = {}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        cand = exact_candidate(match, target, feed["payload"])
        detail = None
        eid = str(cand.get("event_id") or "") if cand else ""
        if eid and re.fullmatch(r"\d+", eid):
            market = await call_tool(mcp, "sportsbet_event_markets", {"eventId": int(eid)})
            if market["state"] == "ok":
                detail = market["payload"]
        rows[target["key"]] = quote_result(cand, detail)
    return source_shell("ok", None, rows)


def pointsbet_locales(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("locales"), list):
        return [x for x in payload["locales"] if isinstance(x, dict)]
    return []


async def source_pointsbet(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    comps_call = await call_tool(mcp, "pointsbet_sport_competitions", {"sportKey": "soccer"})
    if comps_call["state"] != "ok":
        return source_shell(comps_call["state"], comps_call["error"], {t["key"]: quote_result(None, source_state=comps_call["state"], error=comps_call["error"]) for t in TARGETS})
    locales = pointsbet_locales(comps_call["payload"])
    rows: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {"locales": len(locales), "attempted_competitions": {}}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        ranked_locales = sorted(locales, key=lambda loc: legacy.sim(target["country"], loc.get("name")), reverse=True)
        selected = [loc for loc in ranked_locales if legacy.sim(target["country"], loc.get("name")) >= 0.58][:3]
        competitions: list[dict[str, Any]] = []
        for loc in selected:
            competitions.extend([x for x in loc.get("competitions", []) if isinstance(x, dict)])
        if not competitions:
            competitions = [x for loc in ranked_locales[:8] for x in loc.get("competitions", []) if isinstance(x, dict)]
        competitions = sorted(
            competitions,
            key=lambda c: max(legacy.sim(target["league"], c.get("name")), legacy.sim(target["country"], c.get("name"))),
            reverse=True,
        )[:12]
        found = None
        detail = None
        attempts = []
        for comp in competitions:
            key = comp.get("key")
            if key is None:
                continue
            attempts.append({"key": key, "name": comp.get("name")})
            ev = await call_tool(mcp, "pointsbet_competition_events", {"competitionKey": key, "page": 1})
            if ev["state"] != "ok":
                continue
            cand = exact_candidate(match, target, ev["payload"])
            if cand is not None:
                found = cand
                eid = str(cand.get("event_id") or "")
                if eid:
                    full = await call_tool(mcp, "pointsbet_event", {"eventKey": eid})
                    if full["state"] == "ok":
                        detail = full["payload"]
                break
        diagnostics["attempted_competitions"][target["key"]] = attempts
        rows[target["key"]] = quote_result(found, detail)
    return source_shell("ok", None, rows, diagnostics)


def collect_named_dicts(node: Any, key: str = "name", out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if out is None:
        out = []
    if len(out) > 1500:
        return out
    if isinstance(node, dict):
        if isinstance(node.get(key), str):
            out.append(node)
        for value in node.values():
            collect_named_dicts(value, key, out)
    elif isinstance(node, list):
        for item in node:
            collect_named_dicts(item, key, out)
    return out


async def source_tab(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    live = await call_tool(mcp, "tab_live_events_summary")
    if live["state"] != "ok":
        return source_shell(live["state"], live["error"], {t["key"]: quote_result(None, source_state=live["state"], error=live["error"]) for t in TARGETS})
    sports = await call_tool(mcp, "tab_sports")
    sport_name = "Soccer"
    if sports["state"] == "ok":
        named = collect_named_dicts(sports["payload"])
        soccer = next((x for x in named if "soccer" in legacy.norm(x.get("name"))), None)
        if soccer:
            sport_name = soccer.get("name") or sport_name
    sport_page = await call_tool(mcp, "tab_sport", {"sport": sport_name})
    competition_names = []
    if sport_page["state"] == "ok":
        competition_names = list(dict.fromkeys(str(x.get("name")) for x in collect_named_dicts(sport_page["payload"]) if x.get("name")))
    rows: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {"sport": sport_name, "competition_candidates": {}}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        found = exact_candidate(match, target, live["payload"])
        detail = None
        ranked = sorted(
            competition_names,
            key=lambda name: max(legacy.sim(target["league"], name), legacy.sim(target["country"], name)),
            reverse=True,
        )[:12]
        diagnostics["competition_candidates"][target["key"]] = ranked
        # The live summary is useful for discovery; when it lacks prices, resolve the
        # competition page and then call the lean markets endpoint by exact match name.
        if found is None or not found.get("quote_tokens"):
            for competition in ranked:
                comp = await call_tool(mcp, "tab_competition", {"sport": sport_name, "competition": competition})
                if comp["state"] != "ok":
                    continue
                cand = exact_candidate(match, target, comp["payload"])
                if cand is None:
                    continue
                found = cand
                node = cand.get("node") if isinstance(cand.get("node"), dict) else None
                match_name = None
                if node:
                    match_name = node.get("name")
                if not match_name:
                    pair = f"{cand.get('home') or match['home']} v {cand.get('away') or match['away']}"
                    match_name = pair
                mk = await call_tool(
                    mcp,
                    "tab_match_markets",
                    {"sport": sport_name, "competition": competition, "match": match_name},
                )
                if mk["state"] == "ok":
                    detail = mk["payload"]
                break
        rows[target["key"]] = quote_result(found, detail)
    return source_shell("ok", None, rows, diagnostics)


async def source_fanduel(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    page = await call_tool(mcp, "fanduel_sb_call", {"operation": "content_page", "query_params": {"customPageId": "soccer"}})
    if page["state"] != "ok":
        return source_shell(page["state"], page["error"], {t["key"]: quote_result(None, source_state=page["state"], error=page["error"]) for t in TARGETS})
    rows: dict[str, Any] = {}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        cand = exact_candidate(match, target, page["payload"])
        detail = None
        eid = str(cand.get("event_id") or "") if cand else ""
        if eid:
            ev = await call_tool(mcp, "fanduel_sb_call", {"operation": "event_page", "query_params": {"eventId": eid}})
            if ev["state"] == "ok":
                detail = ev["payload"]
        rows[target["key"]] = quote_result(cand, detail)
    return source_shell("ok", None, rows)


async def source_entain(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    screen = await call_tool(mcp, "entain_graphql_call", {"operation": "SportingInPlayScreen", "variables": {"excludeCategoryIds": []}})
    if screen["state"] != "ok":
        return source_shell(screen["state"], screen["error"], {t["key"]: quote_result(None, source_state=screen["state"], error=screen["error"]) for t in TARGETS})
    rows: dict[str, Any] = {}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        cand = exact_candidate(match, target, screen["payload"])
        detail = None
        eid = str(cand.get("event_id") or "") if cand else ""
        if eid:
            if not eid.startswith("SportingEvent:") and re.fullmatch(r"[0-9a-fA-F-]{32,36}", eid):
                eid = "SportingEvent:" + eid
            ev = await call_tool(
                mcp,
                "entain_graphql_call",
                {"operation": "SportingEventScreen", "variables": {"id": eid, "includeInfoHub": False, "includeWidgets": False}},
            )
            if ev["state"] == "ok":
                detail = ev["payload"]
        rows[target["key"]] = quote_result(cand, detail)
    return source_shell("ok", None, rows)


async def source_betfair(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    nav = await call_tool(
        mcp,
        "betfair_navigation",
        {"nodeIds": ["EVENT_TYPE:1"], "attachments": ["MENU", "EVENT", "MARKET"], "maxOutDistance": 4, "maxResults": 2200},
    )
    if nav["state"] != "ok":
        return source_shell(nav["state"], nav["error"], {t["key"]: quote_result(None, source_state=nav["state"], error=nav["error"]) for t in TARGETS})
    rows: dict[str, Any] = {}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        cand = exact_candidate(match, target, nav["payload"])
        detail = None
        eid = str(cand.get("event_id") or "") if cand else ""
        if eid.startswith("EVENT:"):
            ev_id = eid.split(":", 1)[1]
            mk = await call_tool(mcp, "betfair_markets_by_event", {"eventIds": [ev_id]})
            if mk["state"] == "ok":
                detail = mk["payload"]
        elif eid.startswith("MARKET:"):
            market_id = eid.split(":", 1)[1]
            mk = await call_tool(mcp, "betfair_market_prices", {"marketIds": [market_id]})
            if mk["state"] == "ok":
                detail = mk["payload"]
        rows[target["key"]] = quote_result(cand, detail)
    return source_shell("ok", None, rows)


def competition_rank(target: dict[str, Any], comp: dict[str, Any]) -> float:
    name = str(comp.get("name") or "")
    country = target["country"]
    league = target["league"]
    score = max(legacy.sim(name, country), legacy.sim(name, league))
    n = legacy.norm(name)
    for token in legacy.norm(country + " " + league).split():
        if len(token) >= 4 and token in n:
            score += 0.08
    return score


async def source_dabble(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    active = await call_tool(mcp, "dabble_active_competitions")
    if active["state"] != "ok":
        return source_shell(active["state"], active["error"], {t["key"]: quote_result(None, source_state=active["state"], error=active["error"]) for t in TARGETS})
    root = active["payload"] if isinstance(active["payload"], dict) else {}
    comps = root.get("data", {}).get("activeCompetitions", []) if isinstance(root.get("data"), dict) else []
    football = [c for c in comps if isinstance(c, dict) and "football" in legacy.norm(c.get("sportName"))]
    rows: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {"active_competitions": len(comps), "football_competitions": len(football), "attempted": {}}
    for target in TARGETS:
        match = fs_targets.get(target["key"])
        if match is None:
            rows[target["key"]] = {"status": "flashscore_not_live"}
            continue
        ranked = sorted(football, key=lambda c: competition_rank(target, c), reverse=True)[:10]
        diagnostics["attempted"][target["key"]] = [{"id": c.get("id"), "name": c.get("name")} for c in ranked]
        found = None
        detail = None
        for comp in ranked:
            cid = comp.get("id")
            if not cid:
                continue
            fx = await call_tool(mcp, "dabble_competition_fixtures", {"competitionId": cid, "includeInPlay": True})
            if fx["state"] != "ok":
                continue
            cand = exact_candidate(match, target, fx["payload"])
            if cand is None:
                continue
            found = cand
            fixture_id = str(cand.get("event_id") or "")
            if fixture_id:
                fd = await call_tool(mcp, "dabble_fixture_details", {"fixtureId": fixture_id})
                if fd["state"] == "ok":
                    detail = fd["payload"]
            break
        rows[target["key"]] = quote_result(found, detail)
    return source_shell("ok", None, rows, diagnostics)


async def capture_sources(mcp: Any, fs_targets: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    # Sequential on purpose: TAB/PointsBet/Entain have rate limits / anti-bot layers.
    result: dict[str, Any] = {}
    result["kambi_unibet"] = await source_kambi(fs_targets)
    result["pinnacle_arcadia"] = await source_pinnacle(mcp, fs_targets)
    result["sportsbet"] = await source_sportsbet(mcp, fs_targets)
    result["pointsbet"] = await source_pointsbet(mcp, fs_targets)
    result["tab"] = await source_tab(mcp, fs_targets)
    result["fanduel"] = await source_fanduel(mcp, fs_targets)
    result["entain_ladbrokes"] = await source_entain(mcp, fs_targets)
    result["betfair_web"] = await source_betfair(mcp, fs_targets)
    result["dabble"] = await source_dabble(mcp, fs_targets)
    return result


def fs_state(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"live": False}
    return {
        "live": True,
        "event_id": row.get("event_id"),
        "home": row.get("home"),
        "away": row.get("away"),
        "league": row.get("league"),
        "minute": row.get("minute"),
        "home_score": row.get("home_score"),
        "away_score": row.get("away_score"),
    }


def changed_quote_tokens(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    quoted = [r for r in rows if r.get("fingerprint")]
    if len(quoted) < 2:
        return []
    first = set(quoted[0].get("quote_tokens") or [])
    last = set(quoted[-1].get("quote_tokens") or [])
    removed = sorted(first - last)[:20]
    added = sorted(last - first)[:20]
    return [{"removed": removed, "added": added}] if removed or added else []


def summarize(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    out_events = []
    source_summary: dict[str, dict[str, int]] = {s: {} for s in SOURCES}
    for target in TARGETS:
        key = target["key"]
        states = [{"snapshot": s["snapshot"], "captured_at": s["captured_at"], **s["flashscore"][key]} for s in snapshots]
        live_states = [s for s in states if s.get("live")]
        progress = len({(s.get("minute"), s.get("home_score"), s.get("away_score")) for s in live_states}) > 1
        sources = {}
        for source in SOURCES:
            rows = []
            source_states = []
            for snap in snapshots:
                sp = snap["sources"].get(source, {})
                source_states.append(sp.get("state"))
                row = dict((sp.get("matches") or {}).get(key) or {"status": "not_found"})
                row.update({"snapshot": snap["snapshot"], "captured_at": snap["captured_at"]})
                rows.append(row)
            quoted = [r for r in rows if r.get("status") == "matched" and r.get("fingerprint")]
            fps = [r["fingerprint"] for r in quoted]
            odds_changed = len(set(fps)) > 1
            if len(quoted) >= 2 and odds_changed and progress:
                verdict = "PROVEN"
            elif len(quoted) >= 2 and odds_changed:
                verdict = "ODDS_CHANGED_NO_FS_PROGRESS"
            elif quoted:
                verdict = "FOUND_NO_CHANGE"
            elif any(r.get("status") == "matched_no_quotes" for r in rows):
                verdict = "MATCHED_NO_QUOTES"
            elif source_states and all(x == "blocked" for x in source_states):
                verdict = "BLOCKED_GEO"
            elif source_states and all(x == "error" for x in source_states):
                verdict = "SOURCE_ERROR"
            elif all(r.get("status") == "flashscore_not_live" for r in rows):
                verdict = "FLASH_SCORE_NOT_LIVE"
            else:
                verdict = "NOT_FOUND"
            source_summary[source][verdict] = source_summary[source].get(verdict, 0) + 1
            sources[source] = {
                "verdict": verdict,
                "flashscore_progress": progress,
                "matched_quote_snapshots": len(quoted),
                "odds_changed": odds_changed,
                "source_states": source_states,
                "changed_quote_tokens": changed_quote_tokens(rows),
                "snapshots": [
                    {
                        **{k: r.get(k) for k in ("snapshot", "captured_at", "status", "book_event_id", "book_home", "book_away", "mapping_score", "quote_count", "error")},
                        "quote_sample": list(r.get("quote_tokens") or [])[:18],
                    }
                    for r in rows
                ],
            }
        out_events.append({
            "target": {k: target[k] for k in ("key", "home", "away", "country", "league")},
            "flashscore_progress": progress,
            "flashscore_states": states,
            "sources": sources,
        })
    return {
        "generated_at": utc_now(),
        "strict_rule": "PROVEN requires >=2 quote-bearing snapshots, Flashscore minute/score progress, and a changed bookmaker quote fingerprint for the same matched event.",
        "snapshot_count": len(snapshots),
        "targets": len(TARGETS),
        "source_summary": source_summary,
        "events": out_events,
    }


async def run(args: argparse.Namespace) -> int:
    from sportsdata_mcp.config import Config
    from sportsdata_mcp.server import build_server

    args.output_dir.mkdir(parents=True, exist_ok=True)
    mcp, registry = build_server(Config(enabled_groups=GROUPS))
    snapshots: list[dict[str, Any]] = []
    try:
        tools = sorted(t.name for t in await mcp.list_tools())
        write_json(args.output_dir / "registered_tools.json", tools)
        for idx in range(1, max(1, args.snapshots) + 1):
            captured_at = utc_now()
            all_live = await asyncio.to_thread(fs_rows)
            selected = select_fs_targets(all_live)
            flashscore = {key: fs_state(row) for key, row in selected.items()}
            print(f"TARGET_AUDIT snapshot={idx}/{args.snapshots} at={captured_at} flashscore_total={len(all_live)}", flush=True)
            for target in TARGETS:
                state = flashscore[target["key"]]
                print(f"  FS {target['home']} - {target['away']}: live={state.get('live')} minute={state.get('minute')} score={state.get('home_score')}:{state.get('away_score')}", flush=True)
            sources = await capture_sources(mcp, selected)
            for source in SOURCES:
                compact = {key: (sources[source].get("matches") or {}).get(key, {}).get("status") for key in flashscore}
                print(f"  {source}: state={sources[source].get('state')} matches={compact}", flush=True)
            snap = {"snapshot": idx, "captured_at": captured_at, "flashscore": flashscore, "sources": sources}
            snapshots.append(snap)
            write_json(args.output_dir / "snapshots" / f"snapshot_{idx}.json", snap)
            if idx < args.snapshots:
                await asyncio.sleep(max(1, args.interval))
    finally:
        await registry.aclose()

    summary = summarize(snapshots)
    write_json(args.output_dir / "audit.json", snapshots)
    write_json(args.output_dir / "summary.json", summary)
    print("TARGET_AUDIT_FINAL " + json.dumps({"source_summary": summary["source_summary"]}, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("targeted_new_sources_audit"))
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
