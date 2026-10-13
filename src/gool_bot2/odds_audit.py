"""Retrospective breakeven diagnostics from REAL settled GOOL PREMATCH singles.

This report does not equate past realized hit rates with calibrated predictive
probabilities, or claim that any odds floor is intrinsically profitable.
"""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_delivery import was_publicly_sent
from .multisport_journal import load_journal as load_multisport_journal


BANDS = ((1.0, 1.4), (1.4, 1.6), (1.6, 1.8), (1.8, 2.0), (2.0, 2.5), (2.5, 1000.0))


def wilson_lower(wins: int, games: int, z: float = 1.96) -> float:
    if games <= 0:
        return 0.0
    p = wins / games
    q = 1.0 + z * z / games
    mean = p + z * z / (2 * games)
    delta = z * math.sqrt(p * (1.0 - p) / games + z * z / (4 * games * games))
    return max(0.0, (mean - delta) / q)


def _bucket(odd: float) -> str | None:
    for a, b in BANDS:
        if a <= odd < b:
            return f"{a:.2f}–{b:.2f}" if b < 100 else f"{a:.2f}+"
    return None


def _prematch(row: dict[str, Any]) -> bool:
    origin = str(row.get("origin") or "").lower()
    phase = str(row.get("phase") or "").upper()
    if "parlay" in origin or str(row.get("market_family") or "") == "parlay":
        return False
    # The explicit lifecycle takes precedence over a legacy origin label.
    if phase and phase != "PREMATCH":
        return False
    return phase == "PREMATCH" or origin in {"prematch", "prematch_value", "multisport_prematch"}


def _odds(row: dict[str, Any]) -> float:
    try:
        return float(row.get("effective_odd") or row.get("settled_odd") or row.get("odd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def odds_audit_rows(football: list[dict[str, Any]], multisport: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for row in football + multisport:
        if not _prematch(row):
            continue
        result = str(row.get("result") or "").lower()
        if result not in {"won", "lost"}:
            continue
        odd = _odds(row)
        if not math.isfinite(odd) or odd <= 1.0:
            continue
        band = _bucket(odd)
        if band is None:
            continue
        sport = str(row.get("sport") or "football").lower()
        if sport not in {"football", "hockey", "basketball"}:
            continue
        groups[(sport, band)].append((result, odd))
    data = []
    for (sport, band), bets in sorted(groups.items()):
        wins = sum(result == "won" for result, _ in bets)
        count = len(bets)
        mean_odd = sum(odd for _, odd in bets) / count
        profit = sum((odd - 1.0) if result == "won" else -1.0 for result, odd in bets)
        hit_rate = wins / count
        # For heterogeneous odds this is descriptive; realized unit ROI
        # remains the exact sample breakeven test.
        approx_break_even = 1.0 / mean_odd
        conservative = wilson_lower(wins, count)
        data.append({
            "sport": sport, "band": band, "bets": count, "wins": wins,
            "hit_rate": hit_rate, "mean_odd": mean_odd,
            "approx_break_even": approx_break_even,
            "wilson_lower": conservative, "profit": profit,
            "roi": profit / count,
            "sample_sufficient": count >= 100,
            "confidence_above_breakeven": count >= 100 and conservative > approx_break_even,
        })
    return data


def odds_audit_text(football_path: Path | None = None, multisport_path: Path | None = None) -> str:
    if football_path is None:
        from .multi_menu import journal_path
        football_path = journal_path()
    if multisport_path is None:
        from .multisport_menu import journal_path
        multisport_path = journal_path()
    football = [
        {**row, "sport": "football"} for row in load_signal_journal(football_path)
        if was_publicly_sent(row) and not bool(row.get("public_duplicate"))
    ]
    multisport = [
        row for row in load_multisport_journal(multisport_path)
        if bool(row.get("telegram_sent"))
    ]
    data = odds_audit_rows(football, multisport)
    lines = ["💰 <b>GOOL · АУДИТ БЕЗУБЫТОЧНОСТИ PREMATCH</b>",
             "Только рассчитанные и реально отправленные ординары; ставки с возвратом исключены.",
             "ROI рассчитан по фактическим выигрышным/проигрышным котировкам в каждой группе."]
    if not data:
        lines.append("⏳ Пока нет рассчитанных ставок для анализа.")
        return "\n".join(lines)
    labels = {"football": "⚽ ФУТБОЛ", "hockey": "🏒 ХОККЕЙ", "basketball": "🏀 БАСКЕТБОЛ"}
    for sport in ("football", "hockey", "basketball"):
        parts = [r for r in data if r["sport"] == sport]
        if not parts:
            continue
        lines.append("\n<b>" + labels[sport] + "</b>")
        for r in parts:
            verdict = "✅" if r["confidence_above_breakeven"] else ("🧪" if r["sample_sufficient"] else "⏳")
            lines.append(
                f"{verdict} {r['band']} · N={r['bets']} · W={r['wins']} · ROI {r['roi']*100:+.1f}%\n"
                f"  ср.кэф {r['mean_odd']:.2f} · факт {r['hit_rate']*100:.1f}% · безуб. ≈{r['approx_break_even']*100:.1f}%\n"
                f"  нижняя 95% граница проходимости {r['wilson_lower']*100:.1f}%"
            )
    lines.append("\n⚠️ N<100: данных мало. Даже N≥100 не доказывает будущую прибыль: нужны раздельные рынки, проверка калибровки и хронологический тест.")
    lines.append("Выбор 1.70 — стартовый ценовой фильтр, НЕ доказанный оптимум.")
    return "\n".join(lines)[:3900]
