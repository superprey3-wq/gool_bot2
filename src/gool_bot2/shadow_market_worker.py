from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import append_analysis, load_signal_journal, save_signal_journal
from .shadow_market_cards import render_shadow_market_card, render_shadow_market_result_card
from .shadow_markets import analyze_btts_shadow, analyze_team_goal_shadow
from .telegram import broadcast, broadcast_photo


def _match_id(record: dict[str, Any]) -> str:
    return str(((record.get("match") or {}).get("flashscore_event_id") or ""))


def _base(record: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "match_id": _match_id(record),
        "minute": int(match.get("minute") or 0),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "experimental_market": True,
    }


def _settle(record: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    mid = _match_id(record)
    if not mid:
        return []
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    finished = bool(match.get("is_finished"))
    minute = int(match.get("minute") or 0)
    settled: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("match_id") or "") != mid or str(row.get("result") or "pending") != "pending":
            continue
        head = str(row.get("head") or "")
        entry = row.get("score") or [0, 0]
        won = False
        if head == "both_teams_to_score":
            won = hs > 0 and aws > 0
        elif head == "team_to_score":
            side = str(row.get("selected_side") or "")
            won = (side == "home" and hs > int(entry[0] or 0)) or (side == "away" and aws > int(entry[1] or 0))
        if won or finished:
            row.update({
                "result": "won" if won else "lost",
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "settled_minute": minute,
                "settled_score": [hs, aws],
            })
            settled.append(row)
    return settled


def _notify_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("result_notified"):
            continue
        won = str(row.get("result") or "lost") == "won"
        head = str(row.get("head") or "")
        label = "ОБЕ ЗАБЬЮТ — ДА" if head == "both_teams_to_score" else "КОМАНДА ЗАБЬЁТ"
        icon = "✅" if won else "❌"
        try:
            sent = broadcast_photo(
                render_shadow_market_result_card(row),
                caption=f"{icon} <b>{label}</b> · {'ЗАШЁЛ' if won else 'НЕ ЗАШЁЛ'}",
            )
        except Exception as exc:
            print(f"EXPERIMENT_RESULT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
            sent = 0
        if sent == 0:
            score = row.get("settled_score") or row.get("score") or [0, 0]
            broadcast(
                f"{icon} <b>{label}</b> · {'ЗАШЁЛ' if won else 'НЕ ЗАШЁЛ'}\n"
                f"{row.get('home','?')} — {row.get('away','?')} · {row.get('settled_minute',0)}' · {score[0]}:{score[1]}"
            )
        row["result_notified"] = True


def _already_recorded(rows: list[dict[str, Any]], mid: str, head: str) -> bool:
    return any(str(r.get("match_id") or "") == mid and str(r.get("head") or "") == head for r in rows)


def _save_card(card_dir: Path, record: dict[str, Any], analysis: dict[str, Any]) -> tuple[str | None, bytes | None]:
    try:
        mid = _match_id(record)
        minute = int(((record.get("match") or {}).get("minute") or 0))
        png = render_shadow_market_card(record, analysis)
        path = card_dir / f"{analysis.get('head')}_{mid}_{minute}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return str(path), png
    except Exception as exc:
        print(f"EXPERIMENT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        return None, None


def _send_signal(record: dict[str, Any], result: dict[str, Any], png: bytes | None) -> int:
    match = record.get("match") or {}
    head = str(result.get("head") or "")
    confidence = result.get("confidence_score")
    strength = "—" if confidence is None else f"{float(confidence) * 100:.0f}/100"
    if head == "both_teams_to_score":
        label = "💜 ОБЕ ЗАБЬЮТ — ДА"
        detail = "Обе команды должны забить"
    else:
        label = "🔵 КОМАНДА ЗАБЬЁТ"
        detail = str(result.get("team") or "Команда")
    caption = f"{label} · сила {strength}\n<b>{detail}</b>"
    sent = 0
    if png is not None:
        try:
            sent = broadcast_photo(png, caption=caption)
        except Exception as exc:
            print(f"EXPERIMENT_SIGNAL_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
    if sent == 0:
        sent = broadcast(
            f"{label}\n{match.get('home','?')} — {match.get('away','?')}\n"
            f"{int(match.get('minute') or 0)}' · {int(match.get('home_score') or 0)}:{int(match.get('away_score') or 0)}\n"
            f"<b>{detail}</b> · сила {strength}"
        )
    return int(sent > 0)


def process_record(record: dict[str, Any], journal_path: Path, analysis_path: Path, card_dir: Path) -> int:
    mid = _match_id(record)
    if not mid:
        return 0
    rows = load_signal_journal(journal_path)
    settled = _settle(record, rows)
    if settled:
        _notify_results(settled)
        save_signal_journal(journal_path, rows)

    created = 0
    for analyzer in (analyze_btts_shadow, analyze_team_goal_shadow):
        result = analyzer(record)
        head = str(result.get("head") or "")
        append_analysis(analysis_path, {**_base(record), **result, "decision": "SIGNAL" if result.get("passed") else "WAIT"})
        if not result.get("passed") or _already_recorded(rows, mid, head):
            continue
        card_path, png = _save_card(card_dir, record, result)
        sent = _send_signal(record, result, png)
        row = {
            **_base(record),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "head": head,
            "result": "pending",
            "confidence_score": result.get("confidence_score"),
            "probability": result.get("confidence_score"),
            "pressure_score": result.get("pressure_score"),
            "selected_side": result.get("selected_side") or result.get("target_side"),
            "team": result.get("team"),
            "analysis": result,
            "card_path": card_path,
            "telegram_sent": bool(sent),
            "experimental_market": True,
        }
        rows.append(row)
        save_signal_journal(journal_path, rows)
        created += 1
        print(f"EXPERIMENT_SIGNAL head={head} match={mid} minute={row['minute']} team={row.get('team')} strength={row.get('confidence_score')} sent={sent}", flush=True)
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Active BTTS and team-to-score experimental markets")
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser.add_argument("--raw-dir", default=os.getenv("RAW_LIVE_DIR", str(runtime / "raw" / "live")))
    parser.add_argument("--journal", default=os.getenv("SHADOW_MARKET_JOURNAL", str(runtime / "live" / "gool_bot2_shadow_markets.json")))
    parser.add_argument("--analysis", default=os.getenv("SHADOW_MARKET_ANALYSIS", str(runtime / "live" / "gool_bot2_shadow_analysis.jsonl")))
    parser.add_argument("--cards", default=os.getenv("SHADOW_MARKET_CARDS", str(runtime / "live" / "shadow_cards")))
    parser.add_argument("--sleep", type=float, default=float(os.getenv("SHADOW_MARKET_SLEEP", "3")))
    args = parser.parse_args()
    raw_dir = Path(args.raw_dir)
    journal = Path(args.journal)
    analysis = Path(args.analysis)
    card_dir = Path(args.cards)
    offsets: dict[str, int] = {}
    print(f"EXPERIMENT_MARKETS started raw={raw_dir} journal={journal} analysis={analysis}", flush=True)
    while True:
        for path in sorted(raw_dir.glob("*.jsonl")):
            key = str(path)
            try:
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(offsets.get(key, 0))
                    for line in fh:
                        try:
                            record = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(record, dict):
                            process_record(record, journal, analysis, card_dir)
                    offsets[key] = fh.tell()
            except FileNotFoundError:
                continue
        time.sleep(max(0.5, args.sleep))


if __name__ == "__main__":
    main()
