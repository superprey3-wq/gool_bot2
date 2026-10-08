from __future__ import annotations

import fcntl
import json
import math
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from . import telegram
from .v4_prematch_card import render_v4_parlay_card

SPORTS = ("football", "hockey", "basketball")
SPORT_ICON = {"football": "⚽", "hockey": "🏒", "basketball": "🏀"}


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().casefold() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _truthy("GOOL_GLOBAL_SUPER10_ENABLED", True)


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def pool_path() -> Path:
    raw = os.getenv("GOOL_GLOBAL_SUPER10_POOL_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_global_super10_pool.json"


def sent_path() -> Path:
    raw = os.getenv("GOOL_GLOBAL_SUPER10_SENT_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_global_super10_sent.json"


def history_path() -> Path:
    raw = os.getenv("GOOL_GLOBAL_SUPER10_HISTORY_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_global_super10_history.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


@contextmanager
def _locked(path: Path):
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _moscow_day(ts: float | None = None) -> str:
    try:
        tz = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        tz = timezone.utc
    return datetime.fromtimestamp(float(ts or time.time()), tz).strftime("%Y-%m-%d")


def _scheduled_label(ts: float) -> str:
    try:
        tz = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        tz = timezone.utc
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m %H:%M МСК")


def _normalize_candidate(row: dict[str, Any], sport: str) -> dict[str, Any] | None:
    if sport not in SPORTS:
        return None
    event_id = str(row.get("flashscore_event_id") or row.get("event_id") or row.get("match_id") or "").strip()
    if not event_id:
        return None
    start_ts = _num(row.get("start_ts") or row.get("scheduled_start_ts") or row.get("kickoff_ts"), 0.0)
    if start_ts <= 0.0:
        return None

    odd = _num(row.get("odd"), 0.0)
    model_p = _num(
        row.get("model_probability")
        if row.get("model_probability") is not None
        else row.get("fair_probability")
        if row.get("fair_probability") is not None
        else row.get("probability"),
        0.0,
    )
    market_p = _num(row.get("market_probability"), 0.0)
    edge = _num(row.get("edge"), model_p - market_p if market_p > 0 else 0.0)
    quality = _num(row.get("data_quality"), 0.0)
    strength = _num(row.get("strength"), model_p * 100.0)

    meta = dict(row.get("flashscore_meta") or {})
    for key in (
        "home_logo_file", "away_logo_file",
        "home_team_id", "away_team_id",
        "home_team_slug", "away_team_slug",
        "home_logo_url", "away_logo_url",
    ):
        if row.get(key) and not meta.get(key):
            meta[key] = row.get(key)

    return {
        "sport": sport,
        "event_id": event_id,
        "book_event_id": str(row.get("book_event_id") or row.get("event_id") or ""),
        "home": str(row.get("home") or "?"),
        "away": str(row.get("away") or "?"),
        "league": str(row.get("league") or "").strip() or sport.upper(),
        "selection": str(row.get("selection") or row.get("market") or "?"),
        "market": str(row.get("market") or row.get("market_family") or ""),
        "market_family": str(row.get("market_family") or row.get("market") or ""),
        "scope": str(row.get("scope") or "FULL_MATCH"),
        "direction": str(row.get("direction") or ""),
        "selection_side": str(row.get("selection_side") or ""),
        "line": (
            None
            if row.get("line") is None or str(row.get("line")).strip() == ""
            else _num(row.get("line"), 0.0)
        ),
        "moneyline_kind": str(row.get("moneyline_kind") or ""),
        "odd": round(odd, 4),
        "model_probability": round(model_p, 6),
        "market_probability": round(market_p, 6),
        "edge": round(edge, 6),
        "data_quality": round(quality, 4),
        "strength": round(strength, 2),
        "expected_value": round(model_p * odd - 1.0, 6),
        "start_ts": start_ts,
        "scheduled_start": str(row.get("scheduled_start") or _scheduled_label(start_ts)),
        "flashscore_meta": meta,
        "source_entry_id": str(row.get("entry_id") or ""),
        "parlay_safe": bool(row.get("parlay_safe") or sport == "football"),
    }


def football_rows_from_picks(picks: Iterable[Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pick in picks:
        base = dict(meta.get(str(getattr(pick, "event_id", ""))) or {})
        candidate_meta = base.get("candidate_meta") or {}
        if isinstance(candidate_meta, dict):
            specific = candidate_meta.get(f"{getattr(pick, 'market', '')}|{getattr(pick, 'selection', '')}")
            if isinstance(specific, dict):
                base.update(specific)
        rows.append({
            "sport": "football",
            "event_id": str(getattr(pick, "event_id", "")),
            "home": str(getattr(pick, "home", "?")),
            "away": str(getattr(pick, "away", "?")),
            "league": str(getattr(pick, "league", "") or "FOOTBALL"),
            "selection": str(getattr(pick, "selection", "?")),
            "market": str(getattr(pick, "market", "")),
            "odd": _num(getattr(pick, "odds", 0.0), 0.0),
            "model_probability": _num(getattr(pick, "model_probability", 0.0), 0.0),
            "market_probability": _num(getattr(pick, "market_probability", 0.0), 0.0),
            "edge": _num(getattr(pick, "edge", 0.0), 0.0),
            "data_quality": _num(getattr(pick, "data_quality", 0.0), 0.0),
            "strength": _num(getattr(pick, "model_probability", 0.0), 0.0) * 100.0,
            "start_ts": _num(getattr(pick, "kickoff_ts", 0.0), 0.0),
            "flashscore_meta": dict(base.get("flashscore_meta") or {}),
            "parlay_safe": True,
        })
    return rows


def publish_candidates(sport: str, rows: Iterable[dict[str, Any]]) -> int:
    if sport not in SPORTS:
        return 0
    normalized = []
    for row in rows:
        value = _normalize_candidate(dict(row), sport)
        if value is not None:
            normalized.append(value)

    path = pool_path()
    with _locked(path):
        payload = _read_json(path, {})
        if not isinstance(payload, dict):
            payload = {}
        sources = payload.get("sources")
        if not isinstance(sources, dict):
            sources = {}
        sources[sport] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "candidates": normalized[:120],
        }
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sources": sources,
        }
        _write_json(path, payload)
    return len(normalized)


def _candidate_score(row: dict[str, Any]) -> float:
    """Cross-sport reliability score used only for ranking safe SUPER legs.

    Odds, EV and market price are intentionally excluded from the ranking.
    They remain eligibility guards, while final ordering is confidence-first
    across football, hockey and basketball.
    """
    probability = max(0.0, min(1.0, _num(row.get("model_probability"))))
    quality = max(0.0, min(1.0, _num(row.get("data_quality"))))
    strength = max(0.0, min(1.0, _num(row.get("strength")) / 100.0))
    return probability * 0.75 + quality * 0.20 + strength * 0.05


def _eligible_candidates(
    *,
    now_ts: float | None = None,
    min_odd: float,
    max_odd: float,
    min_probability: float,
    min_edge: float,
    min_ev: float,
    min_quality: float,
    min_strength: float,
) -> list[dict[str, Any]]:
    now = float(now_ts or time.time())
    payload = _read_json(pool_path(), {})
    sources = payload.get("sources") if isinstance(payload, dict) else {}
    if not isinstance(sources, dict):
        return []

    min_lead = max(0.0, _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_LEAD_SECONDS"), 300.0))
    horizon = max(3600.0, _num(os.getenv("GOOL_GLOBAL_SUPER10_HORIZON_SECONDS"), 36 * 3600.0))
    # Source snapshots should live as long as their still-future fixtures. The
    # old 6h TTL could expire football before hockey/basketball had enough legs.
    ttl = max(600.0, _num(os.getenv("GOOL_GLOBAL_SUPER10_SOURCE_TTL_SECONDS"), 36 * 3600.0))

    out: list[dict[str, Any]] = []
    for sport in SPORTS:
        source = sources.get(sport) or {}
        updated = str(source.get("updated_at") or "")
        if updated:
            try:
                stamp = datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
                if now - stamp > ttl:
                    continue
            except Exception:
                pass
        for raw in source.get("candidates") or []:
            row = dict(raw)
            if not bool(row.get("parlay_safe") or sport == "football"):
                continue
            start_ts = _num(row.get("start_ts"), 0.0)
            if start_ts <= now + min_lead or start_ts - now > horizon:
                continue
            odd = _num(row.get("odd"))
            p = _num(row.get("model_probability"))
            edge = _num(row.get("edge"))
            quality = _num(row.get("data_quality"))
            strength = _num(row.get("strength"))
            ev = _num(row.get("expected_value"), p * odd - 1.0)
            if not (min_odd <= odd <= max_odd):
                continue
            if p < min_probability or edge < min_edge or ev < min_ev:
                continue
            if quality < min_quality:
                continue
            if sport != "football" and strength < min_strength:
                continue
            row["global_super_score"] = round(_candidate_score(row), 6)
            out.append(row)

    # Keep one safest market per fixture.
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in out:
        key = (str(row.get("sport") or ""), str(row.get("event_id") or ""))
        previous = best.get(key)
        if previous is None or (
            _num(row.get("global_super_score")),
            _num(row.get("model_probability")),
            _num(row.get("data_quality")),
            _num(row.get("strength")),
        ) > (
            _num(previous.get("global_super_score")),
            _num(previous.get("model_probability")),
            _num(previous.get("data_quality")),
            _num(previous.get("strength")),
        ):
            best[key] = row
    return sorted(
        best.values(),
        key=lambda row: (
            _num(row.get("global_super_score")),
            _num(row.get("model_probability")),
            _num(row.get("data_quality")),
            _num(row.get("strength")),
        ),
        reverse=True,
    )


def eligible_candidates(*, now_ts: float | None = None) -> list[dict[str, Any]]:
    """Strict GLOBAL SUPER pool."""
    return _eligible_candidates(
        now_ts=now_ts,
        min_odd=_num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_ODD"), 1.15),
        max_odd=_num(os.getenv("GOOL_GLOBAL_SUPER10_MAX_ODD"), 1.70),
        min_probability=_num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_PROBABILITY"), 0.72),
        min_edge=_num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_EDGE"), 0.055),
        min_ev=_num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_EV"), 0.02),
        min_quality=_num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_QUALITY"), 0.60),
        min_strength=_num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_STRENGTH"), 76.0),
    )


def reserve_candidates(*, now_ts: float | None = None) -> list[dict[str, Any]]:
    """Safe reserve pool used only when strict legs are fewer than 10.

    Reserve candidates are still Brain/parlay-safe selections. They are not
    arbitrary padding: probability/edge/quality gates remain in place.
    """
    return _eligible_candidates(
        now_ts=now_ts,
        min_odd=_num(os.getenv("GOOL_GLOBAL_SUPER10_RESERVE_MIN_ODD"), 1.15),
        max_odd=_num(os.getenv("GOOL_GLOBAL_SUPER10_RESERVE_MAX_ODD"), 1.70),
        min_probability=_num(os.getenv("GOOL_GLOBAL_SUPER10_RESERVE_MIN_PROBABILITY"), 0.68),
        min_edge=_num(os.getenv("GOOL_GLOBAL_SUPER10_RESERVE_MIN_EDGE"), 0.055),
        min_ev=_num(os.getenv("GOOL_GLOBAL_SUPER10_RESERVE_MIN_EV"), 0.01),
        min_quality=_num(os.getenv("GOOL_GLOBAL_SUPER10_RESERVE_MIN_QUALITY"), 0.55),
        min_strength=_num(os.getenv("GOOL_GLOBAL_SUPER10_RESERVE_MIN_STRENGTH"), 74.0),
    )


def readiness_snapshot(*, now_ts: float | None = None) -> dict[str, Any]:
    strict = eligible_candidates(now_ts=now_ts)
    reserve = reserve_candidates(now_ts=now_ts)
    strict_keys = {(str(row.get("sport") or ""), str(row.get("event_id") or "")) for row in strict}
    merged = [*strict]
    merged.extend(
        row for row in reserve
        if (str(row.get("sport") or ""), str(row.get("event_id") or "")) not in strict_keys
    )
    target = max(2, int(_num(os.getenv("GOOL_GLOBAL_SUPER10_LEGS"), 10)))
    strict_by = {sport: sum(1 for row in strict if row.get("sport") == sport) for sport in SPORTS}
    merged_by = {sport: sum(1 for row in merged if row.get("sport") == sport) for sport in SPORTS}
    return {
        "target": target,
        "strict": len(strict),
        "reserve_extra": max(0, len(merged) - len(strict)),
        "available": len(merged),
        "strict_by_sport": strict_by,
        "available_by_sport": merged_by,
        "missing_sports": [sport for sport in SPORTS if merged_by.get(sport, 0) <= 0],
        "need_more": max(0, target - len(merged)),
    }


def build_global_super10(*, now_ts: float | None = None) -> dict[str, Any] | None:
    strict = eligible_candidates(now_ts=now_ts)
    reserve = reserve_candidates(now_ts=now_ts)
    target = max(2, int(_num(os.getenv("GOOL_GLOBAL_SUPER10_LEGS"), 10)))
    max_per_sport = max(1, int(_num(os.getenv("GOOL_GLOBAL_SUPER10_MAX_PER_SPORT"), 6)))
    require_all = _truthy("GOOL_GLOBAL_SUPER10_REQUIRE_ALL_SPORTS", True)

    strict_keys = {(str(row.get("sport") or ""), str(row.get("event_id") or "")) for row in strict}
    reserve_only = [
        row for row in reserve
        if (str(row.get("sport") or ""), str(row.get("event_id") or "")) not in strict_keys
    ]
    tier_by_key = {
        (str(row.get("sport") or ""), str(row.get("event_id") or "")): "strict"
        for row in strict
    }
    tier_by_key.update({
        (str(row.get("sport") or ""), str(row.get("event_id") or "")): "reserve"
        for row in reserve_only
    })

    # Strict/reserve controls eligibility only. Once a leg is safe enough to
    # enter the mixed pool, rank it only by cross-sport reliability.
    all_safe = sorted(
        [*strict, *reserve_only],
        key=lambda row: (
            _num(row.get("global_super_score")),
            _num(row.get("model_probability")),
            _num(row.get("data_quality")),
            _num(row.get("strength")),
        ),
        reverse=True,
    )
    by_sport = {
        sport: [row for row in all_safe if row.get("sport") == sport]
        for sport in SPORTS
    }
    if require_all and any(not by_sport[sport] for sport in SPORTS):
        return None

    chosen: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    counts = {sport: 0 for sport in SPORTS}
    strict_count = 0
    reserve_count = 0

    def add(row: dict[str, Any]) -> bool:
        nonlocal strict_count, reserve_count
        sport = str(row.get("sport") or "")
        key = (sport, str(row.get("event_id") or ""))
        if not sport or not key[1] or key in seen or counts.get(sport, 0) >= max_per_sport:
            return False
        tier = tier_by_key.get(key, "reserve")
        value = dict(row)
        value["super_tier"] = tier
        value["super_confidence_score"] = round(_candidate_score(row) * 100.0, 1)
        chosen.append(value)
        seen.add(key)
        counts[sport] = counts.get(sport, 0) + 1
        if tier == "strict":
            strict_count += 1
        else:
            reserve_count += 1
        return True

    # Keep the three-sport product contract: take the single safest leg from
    # each sport first, then fill every remaining slot globally by reliability.
    if require_all:
        for sport in SPORTS:
            add(by_sport[sport][0])

    for row in all_safe:
        if len(chosen) >= target:
            break
        add(row)

    if len(chosen) < target:
        return None

    chosen = sorted(chosen[:target], key=lambda row: _num(row.get("start_ts")))
    combined_odds = math.prod(_num(row.get("odd"), 1.0) for row in chosen)
    combined_probability = math.prod(_num(row.get("model_probability"), 0.0) for row in chosen)
    avg_confidence = (
        sum(_candidate_score(row) for row in chosen) / len(chosen)
        if chosen else 0.0
    )
    return {
        "kind": "GLOBAL_SUPER",
        "result": "pending",
        "legs": chosen,
        "odd": round(combined_odds, 4),
        "effective_odd": round(combined_odds, 4),
        "combined_odds": round(combined_odds, 4),
        "probability": round(combined_probability, 8),
        "combined_probability": round(combined_probability, 8),
        "average_confidence_score": round(avg_confidence * 100.0, 1),
        "sport_counts": counts,
        "strict_legs": strict_count,
        "reserve_legs": reserve_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _append_history(ticket: dict[str, Any], day: str) -> None:
    path = history_path()
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.append({"day": day, **ticket})
    _write_json(path, rows[-120:])



def _football_half_time_score(provider: Any, event_id: str) -> tuple[int, int]:
    """Reconstruct HT from confirmed goal incidents, including 45+ stoppage time."""
    try:
        goals = list(provider.fetch_goal_timeline(event_id) or [])
    except Exception:
        goals = []
    home = away = 0
    for goal in goals:
        try:
            base = int(goal.get("base_minute") if goal.get("base_minute") is not None else goal.get("minute") or 0)
        except (TypeError, ValueError):
            continue
        if base > 45:
            continue
        try:
            home = int(goal.get("home") if goal.get("home") is not None else home)
            away = int(goal.get("away") if goal.get("away") is not None else away)
        except (TypeError, ValueError):
            continue
    return home, away


def _settle_super_leg(provider: Any, leg: dict[str, Any], state: dict[str, Any]) -> str | None:
    if not bool(state.get("is_finished")):
        return None
    sport = str(leg.get("sport") or "")
    try:
        home_score = int(state.get("home_score"))
        away_score = int(state.get("away_score"))
    except (TypeError, ValueError):
        return None

    if sport == "football":
        from .v4_prematch_settlement import settle_prematch_pick

        market = str(leg.get("market") or "")
        family = str(leg.get("market_family") or "")
        needs_half = (
            family in {"first_half_total", "second_half_total"}
            or market.upper().startswith(("1H_", "2H_"))
            or "1-й тайм" in str(leg.get("selection") or "")
            or "2-й тайм" in str(leg.get("selection") or "")
        )
        half = _football_half_time_score(provider, str(leg.get("event_id") or "")) if needs_half else None
        return settle_prematch_pick(
            leg,
            home_score,
            away_score,
            half_time_score=half,
        )

    if sport not in {"hockey", "basketball"}:
        return None
    from .xbet_multisport_steam import settle_multisport_pick

    scope = str(leg.get("scope") or "FULL_MATCH")
    if scope == "FULL_MATCH":
        score = (home_score, away_score)
    else:
        try:
            segments = provider.fetch_segment_scores(str(leg.get("event_id") or ""), sport) or {}
        except Exception:
            segments = {}
        pair = segments.get(scope)
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return None
        try:
            score = (int(pair[0]), int(pair[1]))
        except (TypeError, ValueError):
            return None
    return settle_multisport_pick(leg, score[0], score[1])


def _settle_super_ticket(provider: Any, ticket: dict[str, Any], states: dict[str, dict[str, Any]]) -> bool:
    if str(ticket.get("result") or "pending").lower() in {"won", "lost", "void", "push"}:
        return False
    legs = [dict(leg) for leg in (ticket.get("legs") or []) if isinstance(leg, dict)]
    if not legs:
        return False

    changed = False
    for leg in legs:
        if str(leg.get("result") or "pending").lower() in {"won", "lost", "void", "push"}:
            continue
        event_id = str(leg.get("event_id") or "")
        state = states.get(event_id) or {}
        result = _settle_super_leg(provider, leg, state)
        if result is None:
            continue
        leg["result"] = result
        leg["settled_at"] = datetime.now(timezone.utc).isoformat()
        leg["settled_score"] = [state.get("home_score"), state.get("away_score")]
        changed = True

    ticket["legs"] = legs
    results = [str(leg.get("result") or "pending").lower() for leg in legs]
    # Do not publish a final SUPER result while the card still contains WAIT
    # legs. Even if one leg has already lost, the result card is only final
    # after every displayed leg is settled.
    if any(result == "pending" for result in results):
        return changed
    if any(result == "lost" for result in results):
        ticket["result"] = "lost"
        ticket["profit_units"] = -1.0
        ticket["settled_at"] = datetime.now(timezone.utc).isoformat()
        return True
    if all(result in {"void", "push"} for result in results):
        ticket["result"] = "void"
        ticket["effective_odd"] = 1.0
        ticket["profit_units"] = 0.0
    else:
        effective = 1.0
        for leg in legs:
            if str(leg.get("result") or "") == "won":
                effective *= max(1.0, _num(leg.get("odd"), 1.0))
        ticket["result"] = "won"
        ticket["effective_odd"] = round(effective, 4)
        ticket["profit_units"] = round(effective - 1.0, 4)
    ticket["settled_at"] = datetime.now(timezone.utc).isoformat()
    return True



def _repair_legacy_super_ticket(ticket: dict[str, Any]) -> bool:
    """Repair tickets created while missing football total lines were stored as 0.0."""
    changed = False
    legs = [dict(leg) for leg in (ticket.get("legs") or []) if isinstance(leg, dict)]
    if not legs:
        return False

    total_families = {
        "match_total",
        "home_total",
        "away_total",
        "first_half_total",
        "second_half_total",
    }
    for leg in legs:
        if str(leg.get("sport") or "").casefold() != "football":
            continue
        family = str(leg.get("market_family") or leg.get("market") or "").casefold()
        selection = str(leg.get("selection") or "").casefold()
        try:
            line = float(leg.get("line"))
        except (TypeError, ValueError):
            line = None
        looks_like_total = family in total_families or any(
            token in selection for token in ("тб", "тм", "over", "under", "больше", "меньше")
        )
        if looks_like_total and line == 0.0:
            leg["line"] = None
            # Re-open a result calculated against the bogus 0.0 line so the
            # next reconciliation settles it from the selection text.
            if str(leg.get("result") or "pending").lower() in {"won", "lost", "push", "void"}:
                leg["result"] = "pending"
                for key in ("settled_at", "settled_score", "settled_minute"):
                    leg.pop(key, None)
            changed = True

    results = [str(leg.get("result") or "pending").lower() for leg in legs]
    if any(result == "pending" for result in results) and str(ticket.get("result") or "pending").lower() in {"won", "lost", "void", "push"}:
        ticket["result"] = "pending"
        for key in ("settled_at", "profit_units", "effective_odd"):
            ticket.pop(key, None)
        changed = True

    if changed:
        ticket["legs"] = legs
    return changed



def _ticket_all_legs_final(ticket: dict[str, Any]) -> bool:
    legs = [leg for leg in (ticket.get("legs") or []) if isinstance(leg, dict)]
    if not legs:
        return False
    final = {"won", "lost", "push", "void"}
    return all(str(leg.get("result") or "pending").lower() in final for leg in legs)


def reconcile_global_super10(*, deliver_result: bool = False) -> dict[str, Any]:
    """Settle sent GLOBAL SUPER legs from authoritative Flashscore results."""
    path = history_path()
    with _locked(path):
        rows = _read_json(path, [])
        if not isinstance(rows, list) or not rows:
            return {"changed": 0, "settled": 0}

        repaired = 0
        for ticket in rows:
            if isinstance(ticket, dict) and _repair_legacy_super_ticket(ticket):
                repaired += 1

        pending_ids = {
            str(leg.get("event_id") or "")
            for ticket in rows
            if isinstance(ticket, dict) and str(ticket.get("result") or "pending").lower() == "pending"
            for leg in (ticket.get("legs") or [])
            if isinstance(leg, dict)
            and str(leg.get("result") or "pending").lower() == "pending"
            and str(leg.get("event_id") or "")
        }
        if not pending_ids:
            if repaired:
                _write_json(path, rows[-120:])
            return {"changed": repaired, "settled": 0}

        from .providers.flashscore import FlashscoreProvider

        provider = FlashscoreProvider()
        states = provider.event_states(pending_ids)
        changed = repaired
        settled = 0
        newly_settled: list[dict[str, Any]] = []
        for ticket in rows:
            if not isinstance(ticket, dict):
                continue
            before = str(ticket.get("result") or "pending").lower()
            if _settle_super_ticket(provider, ticket, states):
                changed += 1
            after = str(ticket.get("result") or "pending").lower()
            if before == "pending" and after in {"won", "lost", "void", "push"}:
                settled += 1
                newly_settled.append(ticket)
        if changed:
            _write_json(path, rows[-120:])

        # Keep the interactive "sent today" snapshot aligned with history.
        sent_file = sent_path()
        sent = _read_json(sent_file, {})
        if isinstance(sent, dict) and isinstance(sent.get("ticket"), dict):
            ticket = sent["ticket"]
            signature = str(ticket.get("created_at") or "")
            matching = next(
                (
                    row for row in reversed(rows)
                    if isinstance(row, dict)
                    and str(row.get("created_at") or "") == signature
                ),
                None,
            )
            if matching is not None:
                sent["ticket"] = dict(matching)
                sent["result"] = matching.get("result")
                _write_json(sent_file, sent)

    delivered = 0
    if deliver_result:
        for ticket in newly_settled:
            # Final report is allowed only after the LAST leg is settled.
            if not _ticket_all_legs_final(ticket):
                continue
            if bool(ticket.get("result_telegram_sent")):
                continue
            try:
                png = render_v4_parlay_card(ticket, result=True)
                result = str(ticket.get("result") or "void").lower()
                icon = {"won": "✅", "lost": "❌", "void": "↩️", "push": "↩️"}.get(result, "ℹ️")
                sent_count = int(telegram.broadcast_photo(
                    png,
                    caption=(
                        f"{icon} <b>SUPER 10 · ИТОГ</b> · {result.upper()}\n"
                        "✅ Все 10 матчей завершены"
                    ),
                ) or 0)
            except Exception:
                sent_count = 0
            if sent_count > 0:
                ticket["result_telegram_sent"] = True
                ticket["result_telegram_sent_at"] = datetime.now(timezone.utc).isoformat()
                delivered += sent_count
        if newly_settled:
            with _locked(path):
                current = _read_json(path, [])
                by_created = {
                    str(ticket.get("created_at") or ""): ticket
                    for ticket in newly_settled
                    if str(ticket.get("created_at") or "")
                }
                dirty = False
                for row in current if isinstance(current, list) else []:
                    key = str(row.get("created_at") or "") if isinstance(row, dict) else ""
                    if key in by_created and bool(by_created[key].get("result_telegram_sent")):
                        row.update({
                            "result_telegram_sent": True,
                            "result_telegram_sent_at": by_created[key].get("result_telegram_sent_at"),
                        })
                        dirty = True
                if dirty:
                    _write_json(path, current[-120:])
    return {"changed": changed, "settled": settled, "delivered": delivered}


def maybe_deliver_global_super10(*, delivery_enabled: bool) -> dict[str, Any]:
    reconcile_global_super10(deliver_result=bool(delivery_enabled))
    if not enabled():
        return {"status": "disabled"}
    if not delivery_enabled:
        return {"status": "shadow", **readiness_snapshot()}

    path = sent_path()
    with _locked(path):
        now = time.time()
        day = _moscow_day(now)
        sent = _read_json(path, {})
        if isinstance(sent, dict) and str(sent.get("day") or "") == day and sent.get("sent"):
            return {"status": "already_sent", "day": day}

        ticket = build_global_super10(now_ts=now)
        if ticket is None:
            return {"status": "not_ready", "day": day, **readiness_snapshot(now_ts=now)}

        # Final kickoff guard under the delivery lock.
        min_lead = max(0.0, _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_LEAD_SECONDS"), 300.0))
        if any(_num(leg.get("start_ts")) <= time.time() + min_lead for leg in ticket["legs"]):
            return {"status": "stale_before_send", "day": day}

        png = render_v4_parlay_card(ticket)
        counts = ticket.get("sport_counts") or {}
        caption = (
            "🌐 <b>SUPER 10 · ФУТБОЛ + ХОККЕЙ + БАСКЕТБОЛ</b>\n"
            f"⚽ {int(counts.get('football') or 0)} · "
            f"🏒 {int(counts.get('hockey') or 0)} · "
            f"🏀 {int(counts.get('basketball') or 0)}\n"
            f"Ноги: строгие <b>{int(ticket.get('strict_legs') or 0)}</b> · резерв <b>{int(ticket.get('reserve_legs') or 0)}</b>\n"
            f"Общий кэф: <b>{_num(ticket.get('combined_odds')):.2f}</b>"
        )
        delivered = int(telegram.broadcast_photo(png, caption=caption) or 0)
        if delivered <= 0:
            return {"status": "send_failed", "day": day}

        record = {
            "day": day,
            "sent": True,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "delivery_count": delivered,
            "ticket": ticket,
        }
        _write_json(path, record)
        _append_history(ticket, day)
        return {
            "status": "sent",
            "day": day,
            "delivery_count": delivered,
            "combined_odds": ticket["combined_odds"],
            "sport_counts": counts,
            "strict_legs": int(ticket.get("strict_legs") or 0),
            "reserve_legs": int(ticket.get("reserve_legs") or 0),
        }
