from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import append_analysis
from .live_multi_all_markets import LiveAllMarketDecision, analyze_live_all_markets


_LAST_ANALYSIS: dict[str, tuple[int, int, int, str]] = {}


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def shadow_journal_path() -> Path:
    raw = os.getenv("GOOL_LIVE_MULTI_SHADOW_JOURNAL", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_live_multi_shadow_journal.json"


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text("utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _save(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _match_id(record: dict[str, Any]) -> str:
    return str(((record.get("match") or {}).get("flashscore_event_id") or ""))


def _halftime_score(record: dict[str, Any]) -> tuple[int, int] | None:
    match = record.get("match") or {}
    for hk, ak in (
        ("halftime_home_score", "halftime_away_score"),
        ("ht_home_score", "ht_away_score"),
    ):
        try:
            if match.get(hk) is not None and match.get(ak) is not None:
                return int(match[hk]), int(match[ak])
        except (TypeError, ValueError):
            pass
    for provider in ("365scores", "flashscore", "fotmob"):
        meta = (((record.get("providers") or {}).get(provider) or {}).get("meta") or {})
        raw = meta.get("halftime_score")
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                return int(raw[0]), int(raw[1])
            except (TypeError, ValueError):
                pass
    if bool(match.get("is_halftime")):
        return int(match.get("home_score") or 0), int(match.get("away_score") or 0)
    return None


def _settle_one(row: dict[str, Any], record: dict[str, Any]) -> bool:
    match = record.get("match") or {}
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    family = str(row.get("family") or "")
    selection = str(row.get("selection") or "")
    line = row.get("line")
    settled: bool | None = None

    try:
        numeric_line = None if line is None else float(line)
    except (TypeError, ValueError):
        numeric_line = None

    if family == "match_total" and numeric_line is not None:
        total = hs + aws
        if selection == "over" and total > numeric_line:
            settled = True
        elif selection == "under" and total > numeric_line:
            settled = False
        elif finished:
            settled = total > numeric_line if selection == "over" else total < numeric_line
    elif family == "home_total" and numeric_line is not None:
        if selection == "over" and hs > numeric_line:
            settled = True
        elif selection == "under" and hs > numeric_line:
            settled = False
        elif finished:
            settled = hs > numeric_line if selection == "over" else hs < numeric_line
    elif family == "away_total" and numeric_line is not None:
        if selection == "over" and aws > numeric_line:
            settled = True
        elif selection == "under" and aws > numeric_line:
            settled = False
        elif finished:
            settled = aws > numeric_line if selection == "over" else aws < numeric_line
    elif family == "first_half_total" and numeric_line is not None:
        ht = _halftime_score(record)
        if ht is not None and (bool(match.get("is_halftime")) or minute > 45 or finished):
            total = ht[0] + ht[1]
            settled = total > numeric_line if selection == "over" else total < numeric_line
        else:
            total = hs + aws
            if minute <= 45 and selection == "over" and total > numeric_line:
                settled = True
            elif minute <= 45 and selection == "under" and total > numeric_line:
                settled = False
    elif family == "btts":
        both = hs > 0 and aws > 0
        if selection == "yes" and both:
            settled = True
        elif selection == "no" and both:
            settled = False
        elif finished:
            settled = both if selection == "yes" else not both
    elif family == "match_1x2" and finished:
        outcome = "home" if hs > aws else "away" if aws > hs else "draw"
        settled = selection == outcome

    if settled is None:
        return False

    odds = float(row.get("odds") or 0.0)
    row["result"] = "won" if settled else "lost"
    row["settled_at"] = datetime.now(timezone.utc).isoformat()
    row["settled_minute"] = minute
    row["settled_score"] = [hs, aws]
    row["profit_units"] = round((odds - 1.0) if settled else -1.0, 4)
    return True


def _settle_pending(path: Path, record: dict[str, Any]) -> int:
    mid = _match_id(record)
    if not mid:
        return 0
    rows = _load(path)
    changed = 0
    for row in rows:
        if str(row.get("match_id") or "") != mid:
            continue
        if str(row.get("result") or "pending") != "pending":
            continue
        changed += int(_settle_one(row, record))
    if changed:
        _save(path, rows)
    return changed


def _already_has_pick(rows: list[dict[str, Any]], match_id: str) -> bool:
    # Shadow experiment deliberately records at most one pick per fixture.
    # This is stricter than the future production policy and guarantees that
    # contradictory LIVE opinions can never appear as separate recommendations.
    return any(str(row.get("match_id") or "") == match_id for row in rows)


def _analysis_snapshot(record: dict[str, Any], decision: LiveAllMarketDecision) -> dict[str, Any]:
    match = record.get("match") or {}
    winner = decision.winner
    return {
        "type": "live_multi_all_markets_shadow",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "match_id": _match_id(record),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": int(match.get("minute") or 0),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "decision": decision.status,
        "reason": decision.reason,
        "winner": None if winner is None else winner.to_dict(),
        "candidate_count": len(decision.candidates),
        "eligible_count": sum(1 for row in decision.candidates if row.eligible),
        "model_context": decision.model_context,
        # Candidates stay in diagnostics only. There is intentionally no
        # public "alternative" field: one match has one final verdict.
        "candidates": [row.to_dict() for row in decision.candidates[:12]],
    }


def observe_live_multi_all_markets_shadow(
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    analysis_path: Path,
) -> LiveAllMarketDecision | None:
    if str(os.getenv("GOOL_LIVE_MULTI_ALL_MARKETS_SHADOW", "1")).strip().casefold() in {"0", "false", "no", "off"}:
        return None
    mid = _match_id(record)
    if not mid:
        return None

    path = shadow_journal_path()
    settled = _settle_pending(path, record)
    if settled:
        print(f"GOOL_LIVE_MULTI_ALL_SHADOW_SETTLED match={mid} count={settled}", flush=True)

    decision = analyze_live_all_markets(record, market_row)
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    winner_key = "-" if decision.winner is None else f"{decision.winner.family}:{decision.winner.selection}:{decision.winner.line}"
    marker = (minute, hs, aws, winner_key)
    if _LAST_ANALYSIS.get(mid) != marker:
        append_analysis(analysis_path, _analysis_snapshot(record, decision))
        _LAST_ANALYSIS[mid] = marker

    if decision.status != "BET" or decision.winner is None:
        return decision

    rows = _load(path)
    if _already_has_pick(rows, mid):
        return decision

    winner = decision.winner
    row = {
        "entry_id": f"live-multi-shadow:{mid}",
        "origin": "live_multi_all_markets_shadow",
        "match_id": mid,
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": minute,
        "score": [hs, aws],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "family": winner.family,
        "selection": winner.selection,
        "label": winner.label,
        "line": winner.line,
        "odds": winner.odds,
        "model_probability": winner.model_probability,
        "market_probability": winner.market_probability,
        "edge": winner.edge,
        "expected_value": winner.expected_value,
        "rating": winner.rating,
        "thesis": winner.thesis,
        "result": "pending",
        "telegram_sent": False,
        "shadow_only": True,
        "decision_reason": decision.reason,
        "model_context": decision.model_context,
    }
    rows.append(row)
    _save(path, rows)
    print(
        f"GOOL_LIVE_MULTI_ALL_SHADOW_PICK match={mid} minute={minute} score={hs}:{aws} "
        f"market={winner.label} odd={winner.odds:.2f} p={winner.model_probability:.3f} "
        f"edge={winner.edge*100:+.1f}pp rating={winner.rating:.1f} telegram=off",
        flush=True,
    )
    return decision


def live_multi_shadow_report_text(path: Path | None = None) -> str:
    rows = _load(path or shadow_journal_path())
    if not rows:
        return (
            "🧪 <b>LIVE MULTI SHADOW</b>\n\n"
            "Пока нет зафиксированных shadow-выборов. Публичная отправка отключена."
        )

    settled = [row for row in rows if str(row.get("result") or "") in {"won", "lost"}]
    pending = [row for row in rows if str(row.get("result") or "pending") == "pending"]
    won = sum(1 for row in settled if row.get("result") == "won")
    lost = sum(1 for row in settled if row.get("result") == "lost")
    profit = sum(float(row.get("profit_units") or 0.0) for row in settled)
    roi = (profit / len(settled) * 100.0) if settled else 0.0

    families: dict[str, list[dict[str, Any]]] = {}
    for row in settled:
        families.setdefault(str(row.get("family") or "?"), []).append(row)

    labels = {
        "match_total": "Тотал матча",
        "home_total": "ИТ хозяев",
        "away_total": "ИТ гостей",
        "first_half_total": "Тотал 1-го тайма",
        "btts": "Обе забьют",
        "match_1x2": "1X2",
    }
    parts = [
        "🧪 <b>LIVE MULTI SHADOW · ВСЕ РЫНКИ</b>",
        "Публичная отправка: <b>ВЫКЛ</b> · один матч = один shadow-вердикт",
        (
            f"Всего: <b>{len(rows)}</b> · рассчитано: <b>{len(settled)}</b> · ⏳ {len(pending)}\n"
            f"✅ {won} · ❌ {lost} · проход {(won/len(settled)*100 if settled else 0):.1f}% · "
            f"P/L {profit:+.2f}u · ROI {roi:+.1f}%"
        ),
    ]
    if families:
        lines = []
        for family, items in sorted(families.items(), key=lambda pair: len(pair[1]), reverse=True):
            fw = sum(1 for row in items if row.get("result") == "won")
            fp = sum(float(row.get("profit_units") or 0.0) for row in items)
            lines.append(
                f"• {labels.get(family, family)}: {len(items)} · ✅ {fw} · ❌ {len(items)-fw} · P/L {fp:+.2f}u"
            )
        parts.append("<b>По рынкам</b>\n" + "\n".join(lines))

    recent = rows[-6:]
    if recent:
        lines = []
        for row in reversed(recent):
            icon = "✅" if row.get("result") == "won" else "❌" if row.get("result") == "lost" else "⏳"
            score = row.get("score") or [0, 0]
            lines.append(
                f"{icon} {row.get('home','?')} — {row.get('away','?')} · {row.get('minute',0)}' {score[0]}:{score[1]}\n"
                f"↳ {row.get('label','?')} @ {float(row.get('odds') or 0):.2f} · "
                f"p {float(row.get('model_probability') or 0)*100:.1f}% · edge {float(row.get('edge') or 0)*100:+.1f}пп"
            )
        parts.append("<b>Последние решения</b>\n" + "\n".join(lines))

    return "\n\n".join(parts)


__all__ = [
    "observe_live_multi_all_markets_shadow",
    "live_multi_shadow_report_text",
    "shadow_journal_path",
]
