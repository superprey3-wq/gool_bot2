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
    probability = _num(row.get("model_probability"))
    quality = max(0.0, min(1.0, _num(row.get("data_quality"))))
    strength = max(0.0, min(1.0, _num(row.get("strength")) / 100.0))
    edge_norm = max(0.0, min(1.0, _num(row.get("edge")) / 0.15))
    return probability * 0.55 + quality * 0.20 + strength * 0.15 + edge_norm * 0.10


def eligible_candidates(*, now_ts: float | None = None) -> list[dict[str, Any]]:
    now = float(now_ts or time.time())
    payload = _read_json(pool_path(), {})
    sources = payload.get("sources") if isinstance(payload, dict) else {}
    if not isinstance(sources, dict):
        return []

    min_odd = _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_ODD"), 1.15)
    max_odd = _num(os.getenv("GOOL_GLOBAL_SUPER10_MAX_ODD"), 1.65)
    min_probability = _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_PROBABILITY"), 0.72)
    min_edge = _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_EDGE"), 0.055)
    min_ev = _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_EV"), 0.02)
    min_quality = _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_QUALITY"), 0.60)
    min_strength = _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_STRENGTH"), 76.0)
    min_lead = max(0.0, _num(os.getenv("GOOL_GLOBAL_SUPER10_MIN_LEAD_SECONDS"), 300.0))
    horizon = max(3600.0, _num(os.getenv("GOOL_GLOBAL_SUPER10_HORIZON_SECONDS"), 36 * 3600.0))
    ttl = max(600.0, _num(os.getenv("GOOL_GLOBAL_SUPER10_SOURCE_TTL_SECONDS"), 6 * 3600.0))

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
            # Football's own SUPER pool is already quality-filtered at 0.80.
            # Hockey/basketball carry Brain R and data quality; require both.
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
            -_num(row.get("odd")),
        ) > (
            _num(previous.get("global_super_score")),
            _num(previous.get("model_probability")),
            -_num(previous.get("odd")),
        ):
            best[key] = row
    return sorted(
        best.values(),
        key=lambda row: (
            _num(row.get("global_super_score")),
            _num(row.get("model_probability")),
            -_num(row.get("odd")),
        ),
        reverse=True,
    )


def build_global_super10(*, now_ts: float | None = None) -> dict[str, Any] | None:
    rows = eligible_candidates(now_ts=now_ts)
    target = max(2, int(_num(os.getenv("GOOL_GLOBAL_SUPER10_LEGS"), 10)))
    max_per_sport = max(1, int(_num(os.getenv("GOOL_GLOBAL_SUPER10_MAX_PER_SPORT"), 6)))
    require_all = _truthy("GOOL_GLOBAL_SUPER10_REQUIRE_ALL_SPORTS", True)

    by_sport = {sport: [row for row in rows if row.get("sport") == sport] for sport in SPORTS}
    if require_all and any(not by_sport[sport] for sport in SPORTS):
        return None

    chosen: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    counts = {sport: 0 for sport in SPORTS}

    # A GLOBAL SUPER must really be cross-sport. Seed it with the best qualified
    # leg from each sport, but never relax thresholds to force a weak leg.
    if require_all:
        for sport in SPORTS:
            row = by_sport[sport][0]
            key = (sport, str(row.get("event_id") or ""))
            chosen.append(row)
            seen.add(key)
            counts[sport] += 1

    for row in rows:
        if len(chosen) >= target:
            break
        sport = str(row.get("sport") or "")
        key = (sport, str(row.get("event_id") or ""))
        if key in seen or counts.get(sport, 0) >= max_per_sport:
            continue
        chosen.append(row)
        seen.add(key)
        counts[sport] = counts.get(sport, 0) + 1

    if len(chosen) < target:
        return None

    chosen = sorted(chosen[:target], key=lambda row: _num(row.get("start_ts")))
    combined_odds = math.prod(_num(row.get("odd"), 1.0) for row in chosen)
    combined_probability = math.prod(_num(row.get("model_probability"), 0.0) for row in chosen)
    return {
        "kind": "GLOBAL_SUPER",
        "result": "pending",
        "legs": chosen,
        "odd": round(combined_odds, 4),
        "effective_odd": round(combined_odds, 4),
        "combined_odds": round(combined_odds, 4),
        "probability": round(combined_probability, 8),
        "combined_probability": round(combined_probability, 8),
        "sport_counts": counts,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _append_history(ticket: dict[str, Any], day: str) -> None:
    path = history_path()
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.append({"day": day, **ticket})
    _write_json(path, rows[-120:])


def maybe_deliver_global_super10(*, delivery_enabled: bool) -> dict[str, Any]:
    if not enabled():
        return {"status": "disabled"}
    if not delivery_enabled:
        return {"status": "shadow"}

    path = sent_path()
    with _locked(path):
        now = time.time()
        day = _moscow_day(now)
        sent = _read_json(path, {})
        if isinstance(sent, dict) and str(sent.get("day") or "") == day and sent.get("sent"):
            return {"status": "already_sent", "day": day}

        ticket = build_global_super10(now_ts=now)
        if ticket is None:
            return {"status": "not_ready", "day": day, "eligible": len(eligible_candidates(now_ts=now))}

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
        }
