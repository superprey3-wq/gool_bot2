from __future__ import annotations

import json
import os
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .match_context import provider_pair, xg_or_proxy_pair


_METRICS = (
    ("xG", "xg"),
    ("shots", "shots"),
    ("SOT", "shots_on_target"),
    ("big", "big_chances"),
    ("box", "touches_box"),
    ("danger", "dangerous_attacks"),
    ("corners", "corners"),
)


def _pct(n: int, total: int) -> str:
    return "—" if total <= 0 else f"{100.0 * n / total:.1f}%"


def _load_recent_raw(raw_dir: Path, limit: int = 2500) -> list[dict[str, Any]]:
    files = sorted(raw_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
    buf: deque[str] = deque(maxlen=max(100, int(limit)))
    # Read older file first so the deque finishes with the newest rows overall.
    for path in reversed(files):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        buf.append(line)
        except OSError:
            continue
    rows: list[dict[str, Any]] = []
    for line in buf:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and isinstance(row.get("match"), dict):
            rows.append(row)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def live_data_check_text(runtime: Path | None = None) -> str:
    runtime = runtime or Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw_dir = Path(os.getenv("RAW_LIVE_DIR", str(runtime / "raw" / "live")))
    rows = _load_recent_raw(raw_dir)

    live_rows = [
        row for row in rows
        if not bool((row.get("match") or {}).get("is_finished"))
        and int((row.get("match") or {}).get("minute") or 0) > 0
        and not row.get("collector_error")
    ]
    unique = {
        str((row.get("match") or {}).get("flashscore_event_id") or "")
        for row in live_rows
        if str((row.get("match") or {}).get("flashscore_event_id") or "")
    }

    metric_counts: dict[str, int] = {}
    for label, key in _METRICS:
        count = 0
        for row in live_rows:
            try:
                h, a = provider_pair(row, key)
            except Exception:
                h = a = None
            if h is not None and a is not None:
                count += 1
        metric_counts[label] = count

    real_xg = 0
    proxy_xg = 0
    unavailable_xg = 0
    momentum_any = 0
    momentum_ready = 0
    momentum_5m = 0
    source_counts: Counter[str] = Counter()

    for row in live_rows:
        try:
            _h, _a, source, _evidence = xg_or_proxy_pair(row)
        except Exception:
            source = "unavailable"
        if source == "provider_xg":
            real_xg += 1
        elif source == "attack_proxy":
            proxy_xg += 1
        else:
            unavailable_xg += 1

        momentum = row.get("live_momentum") or {}
        if momentum:
            momentum_any += 1
        try:
            if float(momentum.get("minutes_in_epoch") or 0.0) >= 5.0:
                momentum_ready += 1
        except (TypeError, ValueError):
            pass
        if any(str(k).endswith("_last_5m") for k in momentum):
            momentum_5m += 1

        for name, payload in (row.get("providers") or {}).items():
            if isinstance(payload, dict) and isinstance(payload.get("stats"), dict) and payload.get("stats"):
                source_counts[str(name)] += 1

    xbet_state_path = Path(os.getenv("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    xbet_state = _read_json(xbet_state_path)
    xbet_matches = xbet_state.get("matches") or {}
    if not isinstance(xbet_matches, dict):
        xbet_matches = {}
    xbet_with_markets = sum(
        1 for row in xbet_matches.values()
        if isinstance(row, dict) and isinstance(row.get("markets"), dict) and bool(row.get("markets"))
    )

    memory_state_path = Path(os.getenv("XBET_MARKET_MEMORY_STATE", str(runtime / "live" / "market_memory_state.json")))
    memory_state = _read_json(memory_state_path)
    memory_matches = memory_state.get("matches") or {}
    if not isinstance(memory_matches, dict):
        memory_matches = {}
    phases = Counter(str((row or {}).get("phase") or "?").upper() for row in memory_matches.values() if isinstance(row, dict))

    latest = max(
        (str(row.get("captured_at") or "") for row in live_rows),
        default="—",
    )

    out = [
        "🩺 <b>LIVE DATA CHECK</b>",
        f"Снимков проверено: <b>{len(live_rows)}</b> · матчей: <b>{len(unique)}</b>",
        f"Последний LIVE-снимок: <code>{latest}</code>",
        "",
        "<b>Статистика в реальных LIVE-снимках</b>",
    ]
    for label, _key in _METRICS:
        n = metric_counts[label]
        out.append(f"{label}: <b>{n}</b>/{len(live_rows)} · {_pct(n, len(live_rows))}")

    out += [
        "",
        "<b>xG-слой</b>",
        f"реальный provider xG: <b>{real_xg}</b>",
        f"attack proxy: <b>{proxy_xg}</b>",
        f"нет достаточных данных: <b>{unavailable_xg}</b>",
        "",
        "<b>Momentum</b>",
        f"поле live_momentum: <b>{momentum_any}</b>/{len(live_rows)}",
        f"эпоха ≥5 мин: <b>{momentum_ready}</b>",
        f"есть реальные 5m delta: <b>{momentum_5m}</b>",
        "",
        "<b>Источники со stats</b>",
        ", ".join(f"{k}:{v}" for k, v in source_counts.most_common()) or "нет",
        "",
        "<b>1xBet LIVE</b>",
        f"state matches: <b>{len(xbet_matches)}</b> · с рынками: <b>{xbet_with_markets}</b>",
        f"last state: <code>{xbet_state.get('captured_at') or '—'}</code>",
        "",
        "<b>Market Memory</b>",
        f"матчей: <b>{len(memory_matches)}</b> · PREMATCH {phases.get('PREMATCH',0)} · LIVE {phases.get('LIVE',0)}",
        f"last memory: <code>{memory_state.get('captured_at') or '—'}</code>",
    ]
    if not rows:
        out += [
            "",
            "⚠️ RAW LIVE-файлы не найдены или пусты. Это уже означает, что LIVE collector на этом сервере не записывает данные в ожидаемую папку.",
        ]
    return "\n".join(out)


__all__ = ["live_data_check_text"]
