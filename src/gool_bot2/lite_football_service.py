"""Football-only GOOL Lite service for constrained Python hosting.

One process; no numpy/sklearn/playwright or multisport workers.
Signals are opt-in (GOOL_FOOTBALL_LITE_MODE=active).
LIVE signals require fresh 1xBet odds AND identical Flashscore/1xBet score.
"""
from __future__ import annotations

import html
import json
import os
import resource
import signal
import tempfile
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from . import telegram
from .lite_football import LiteFootballWorker, _load, _save
from .providers.common import pair_score, norm_team

TZ = ZoneInfo("Europe/Moscow")
KEY = "GOOL_FOOTBALL_LITE_MODE"


def is_active() -> bool:
    return os.getenv(KEY, "shadow").lower() == "active"


def _numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _game_score(game: dict[str, Any]) -> tuple[int, int] | None:
    """Same exact score parser as the original GOOL 1xBet epoch guard.

    1xBet often omits zero-score sides: SC.FS={} really means 0:0.
    Rejecting absent S1/S2 was a Lite-only regression that could suppress
    live odds even though the original bookmaker pipeline accepted the game.
    """
    from .xbet_score_epoch_guard import extract_xbet_score
    return extract_xbet_score(game)


def _index_match_scores(fixture: Any, index: list[dict[str, Any]]) -> list[tuple[float, float, float, dict[str, Any]]]:
    """Rank ordered 1xBet names; the original GOOL accepted pair_score >= .68.

    Averages alone can confuse a namesake with an unrelated opponent.
    Unlike pair_score (which allows reversed sides), LIVE needs home and away
    aligned to the separately verified score. Never switch team orientation.
    """
    h = norm_team(str(fixture.home))
    a = norm_team(str(fixture.away))
    if not h or not a:
        return []
    rows: list[tuple[float, float, float, dict[str, Any]]] = []
    for item in index or []:
        other_h = norm_team(str(item.get("home") or ""))
        other_a = norm_team(str(item.get("away") or ""))
        if not other_h or not other_a:
            continue
        hs = SequenceMatcher(None, h, other_h).ratio()
        aw = SequenceMatcher(None, a, other_a).ratio()
        rows.append(((hs + aw) / 2.0, hs, aw, item))
    return sorted(rows, key=lambda item: item[0], reverse=True)


def _select_live_index_match(fixture: Any, index: list[dict[str, Any]],
                             collector: Any) -> tuple[dict[str, Any] | None, str]:
    """Use original collector mapping + distinct-team/ambiguity safeguards."""
    scored = _index_match_scores(fixture, index)
    if not scored:
        return None, "MATCH_NOT_FOUND"
    # Reuse old GOOL _map which has its own stable match-id cache and 0.68
    # lower bound. Do not accept reverse fixtures or ambiguous namesakes.
    if callable(getattr(collector, "_map", None)):
        event_id = collector._map(fixture, index)
        if not event_id:
            return None, "NAME_MISMATCH"
        selected = next((v for v in scored if str(v[3].get("event_id")) == str(event_id)), None)
        if selected is None:
            return None, "MATCH_NOT_FOUND"
    else:
        selected = scored[0]
    avg, hs, aw, match = selected
    min_score = max(.68, min(.88, float(os.getenv("XBET_MATCH_MIN_SCORE", ".68"))))
    if avg < min_score or min(hs, aw) < .57:
        return None, "NAME_MISMATCH"
    # The old pair_score can overrate names sharing a city but not the club
    # (Manchester United vs Manchester City), or a youth/reserve variant.
    def side_ok(original: str, bookmaker: str) -> bool:
        left, right = norm_team(original), norm_team(bookmaker)
        lparts, rparts = left.split(), right.split()
        if not lparts or not rparts:
            return False
        # Team name categories that must match on both sides, including
        # "II", "B", academy, women and age groups. NFKD normalization
        # strips "Women/W", so inspect both raw and cleaned forms.
        import re
        suffix = r"(?<![a-z0-9])(u(?:17|18|19|20|21|22|23)|ii|reserves?|women|w|femenino|femenil|youth)(?![a-z0-9])"
        if set(re.findall(suffix, original.casefold())) != set(re.findall(suffix, bookmaker.casefold())):
            return False
        if SequenceMatcher(None, left, right).ratio() >= .93:
            return True
        if len(lparts) >= 2 and len(rparts) >= 2:
            if SequenceMatcher(None, lparts[-1], rparts[-1]).ratio() < .60:
                return False
        return True
    if not side_ok(fixture.home, str(match["home"])) or not side_ok(fixture.away, str(match["away"])):
        return None, "NAME_SIDE_MISMATCH"
    # Disallow near-ties even with a matching score: the other fixture can
    # coincidentally be at the same 0:0 or 1:0.
    challengers = [row for row in scored if str(row[3].get("event_id")) != str(match.get("event_id"))]
    if challengers and challengers[0][0] >= avg - .07:
        return None, "MATCH_AMBIGUOUS"
    return match, "MATCH_OK"


class LivePriceAdapter:
    """Look up only promising live matches; never price from a stale index alone."""

    def __init__(self, data_root: Path, collector: Any = None):
        self.collector = collector
        self.data_root = data_root
        self.last_reject_reason: str | None = None
        self.last_match_debug: dict[str, Any] | None = None

    def _get_collector(self):
        if self.collector is None:
            from .xbet_market_robust import RobustXBetMarketCollector
            root = self.data_root / "live"
            self.collector = RobustXBetMarketCollector(
                root / "football_lite_xbet_unused_state.json",
                root / "football_lite_xbet_unused_history.jsonl",
            )
        return self.collector

    def _reject(self, reason: str) -> None:
        # Do not log potentially sensitive raw bookmaker payloads.
        self.last_reject_reason = reason
        return None

    def price(self, decision: dict[str, Any], index: list[dict[str, Any]], *, now: float) -> dict[str, Any] | None:
        """Fail closed unless bookmaker identity, score and both live prices agree."""
        self.last_reject_reason = None
        self.last_match_debug = None
        if decision.get("decision") != "BET":
            return self._reject("NO_BRAIN_BET")
        if now - _numeric(decision.get("observed_at")) > 60:
            return self._reject("STALE_DECISION")
        scores = decision.get("score") or []
        if len(scores) != 2:
            return self._reject("SCORE_MISSING")
        try:
            scoreboard = (int(scores[0]), int(scores[1]))
        except (ValueError, TypeError):
            return self._reject("SCORE_INVALID")
        c = self._get_collector()
        fixture = SimpleNamespace(
            provider_match_id=str(decision.get("event_id") or ""),
            home=str(decision.get("home") or ""),
            away=str(decision.get("away") or ""),
        )
        best, match_reason = _select_live_index_match(fixture, index, c)
        if best is None:
            scored = _index_match_scores(fixture, index)
            top = scored[0] if scored else None
            self.last_match_debug = {
                "reason": match_reason,
                "flashscore": f"{fixture.home} — {fixture.away}"[:140],
                "closest_1xbet": (f"{top[3].get('home')} — {top[3].get('away')}"[:140]
                                 if top else "нет в индексе"),
                "similarity": round(top[0], 3) if top else 0,
                "index_rows": len(index),
            }
            return self._reject(match_reason)
        book_id = str(best.get("event_id") or "")
        if not book_id:
            return self._reject("BOOK_ID_MISSING")
        # Every quote and score are fetched anew. Never trust historical index odds.
        game = c._game(book_id)
        if not isinstance(game, dict) or _game_score(game) != scoreboard:
            return self._reject("GAME_SCORE_MISMATCH")
        minute = int(decision.get("minute") or 0)
        market = str(decision.get("market") or "")
        if market == "GOAL_BEFORE_HT":
            if not (10 <= minute <= 45):
                return self._reject("WRONG_1H_MINUTE")
            # The old market collector decodes the embedded first-half subgame
            # in the SAME verified GetGameZip response. Lite needlessly
            # fetched the event again, mixing two score/market snapshots.
            from .xbet_market_robust import decode_standard_markets
            lines = decode_standard_markets(game).get("first_half_total") or []
            if not lines:
                # Some 1xBet events expose the half as a separate child game.
                lines = (c._markets_for_event(book_id, minute, sum(scoreboard)) or {}).get("first_half_total") or []
            family = "first_half_total"
        elif market == "ANOTHER_GOAL":
            if not (46 <= minute <= 85):
                return self._reject("WRONG_2H_MINUTE")
            from .xbet_market_robust import decode_standard_markets
            lines = decode_standard_markets(game).get("match_total") or []
            family = "match_total"
        else:
            return self._reject("UNSUPPORTED_MARKET")
        target = float(sum(scoreboard)) + 0.5
        matching = next((row for row in lines
                         if abs(_numeric(row.get("line"), -999.0) - target) < 0.001), None)
        if not matching:
            return self._reject("TARGET_LINE_MISSING")
        over = _numeric(matching.get("over"))
        under = _numeric(matching.get("under"))
        if not (1.40 <= over <= 3.25 and under > 1.01):
            return self._reject("ODDS_PAIR_INVALID")
        fair = (1 / over) / ((1 / over) + (1 / under))
        p = _numeric(decision.get("probability"))
        confidence = _numeric(decision.get("confidence"))
        required_confidence = max(0.62, float(decision.get("confidence_required") or 0.78))
        # Original Brain V3 has a separately validated STRONG_LIVE route
        # (typically 0.64-0.69). Lite's fixed p>=.70 used to veto it
        # AFTER the original V3 had already approved BET. Keep the original
        # decision's guarded bet_min; do NOT relax bookmaker/value checks.
        if decision.get("brain") == "ORIGINAL_BRAIN_V3":
            min_probability = max(0.62, required_confidence)
        else:
            min_probability = 0.70
        if (p < min_probability or confidence < required_confidence
                or p - fair < 0.06 or p * over - 1 < 0.05):
            return self._reject("MODEL_VALUE_GATE")
        return {
            "event_id": fixture.provider_match_id,
            "book_event_id": book_id,
            "home": fixture.home, "away": fixture.away,
            "league": str(decision.get("league") or ""),
            "phase": "LIVE", "market": market, "market_family": family,
            "line": target, "selection": f"ТБ {target:g}" + (" (1-й тайм)" if minute <= 45 else ""),
            "odd": round(over, 3), "bookmaker": "1xBet LIVE",
            "score": list(scoreboard), "minute": minute,
            "observed_at": now, "entry_total": sum(scoreboard),
            "model_probability": round(p, 4), "market_probability": round(fair, 4),
            "confidence": confidence, "edge": round(p - fair, 4),
            "expected_value": round(p * over - 1, 4),
        }


class FootballHost:
    def __init__(self, root: Path, *, brain: Any = None, price_adapter: Any = None):
        self.root = Path(root)
        self.live = self.root / "live"
        self.live.mkdir(parents=True, exist_ok=True)
        self.brain = brain if brain is not None else LiteFootballWorker(self.root)
        self.journal_path = self.live / "gool_lite_football_journal.json"
        self.shadow_journal_path = self.live / "gool_lite_football_shadow_journal.json"
        self.audit_path = self.live / "gool_lite_football_audit.json"
        self.audit = _load(self.audit_path, {})
        if self.audit.get("date") != datetime.now(TZ).strftime("%Y-%m-%d"):
            self.audit = {"date": datetime.now(TZ).strftime("%Y-%m-%d"), "prematch": {}, "live": {}, "odds": {},
                          "last_prematch_scan": "", "last_live_scan": "", "prematch_candidates": []}
        self.offset_path = self.live / "football_telegram_offset.txt"
        self.journal = _load(self.journal_path, [])
        self.shadow_journal = _load(self.shadow_journal_path, [])
        if not isinstance(self.shadow_journal, list):
            self.shadow_journal = []
        if not isinstance(self.journal, list):
            self.journal = []
        self.price_adapter = price_adapter if price_adapter is not None else LivePriceAdapter(self.root)
        self.last_state: dict[str, Any] = dict(_load(self.brain.state_path, {}))
        self._next_poll = 0.0
        self._next_live_odds = 0.0
        self._next_settle = 0.0
        self._next_notice = 0.0
        # Daily audit survives restarts. Separate in-memory counters make
        # post-deploy checks unambiguous and never combine old and new code.
        self.session_started_at = datetime.now(TZ).strftime("%H:%M МСК")
        self.session_audit: dict[str, dict[str, int]] = {
            "prematch": {}, "live": {}, "odds": {},
        }

    def _save_journal(self):
        # Bound storage by age and number; never discard pending bets.
        now = time.time()
        finished = [r for r in self.journal
                    if r.get("result") != "pending"
                    and now - _numeric(r.get("created_ts")) < 21 * 86400]
        pending = [r for r in self.journal if r.get("result") == "pending"]
        self.journal = (finished[-550:] + pending[-250:])
        _save(self.journal_path, self.journal)

    def _record_shadow(self, item: dict[str, Any], *, now: float) -> None:
        # Never present a SHADOW observation as a placed or sent bet.
        event_id = str(item.get("event_id") or "")
        phase = str(item.get("phase") or "")
        if any(str(r.get("event_id")) == event_id and r.get("phase") == phase
               for r in self.shadow_journal):
            return
        row = {
            **item, "created_ts": now,
            "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "result": "pending", "sent": False, "origin": "shadow_verified",
        }
        self.shadow_journal.append(row)
        self.shadow_journal = self.shadow_journal[-250:]
        _save(self.shadow_journal_path, self.shadow_journal)
        print(f"GOOL_SHADOW_VERIFIED phase={phase} event={event_id}", flush=True)

    def _deliver(self, item: dict[str, Any], *, now: float) -> bool:
        event_id = str(item.get("event_id") or "")
        phase = str(item.get("phase") or "")
        # Only one entry per fixture per phase, including restarts.
        if not event_id or any(
            r.get("event_id") == event_id and r.get("phase") == phase
            for r in self.journal
        ):
            return False
        if phase == "PREMATCH" and _numeric(item.get("start_ts")) <= now + 60:
            return False
        if not is_active():
            self._record_shadow(item, now=now)
            return False
        bet = {**item, "created_ts": now, "result": "pending",
               "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
               "origin": "football_lite_" + phase.lower()}
        text = (
            f"⚽ <b>GOOL Lite · {html.escape(phase)}</b>\n"
            f"<b>{html.escape(str(bet.get('home')))} — {html.escape(str(bet.get('away')))}</b>\n"
            f"🏆 {html.escape(str(bet.get('league') or 'Футбол'))}\n"
            f"🎯 <b>{html.escape(str(bet.get('selection') or ''))} @ {bet.get('odd')}</b>\n"
            f"📊 P≈{100 * _numeric(bet.get('model_probability')):.1f}% · "
            f"EV≈{100 * _numeric(bet.get('expected_value')):+.1f}% (не калибровано)\n"
            f"Источник: {html.escape(str(bet.get('bookmaker') or ''))}"
        )
        sent = telegram.broadcast(text)
        if sent < 1:
            return False
        bet["sent"] = True
        self.journal.append(bet)
        self._save_journal()
        print(f"GOOL_FOOTBALL_LITE_SENT phase={phase} event={event_id}", flush=True)
        return True

    def _track_scans(self, result: dict[str, Any]) -> None:
        """Persist bounded daily audit without storing raw bookmaker feeds."""
        date_now = datetime.now(TZ).strftime("%Y-%m-%d")
        if self.audit.get("date") != date_now:
            self.audit = {"date": date_now, "prematch": {}, "live": {}, "odds": {},
                          "last_prematch_scan": "", "last_live_scan": "", "prematch_candidates": []}
        for part in ("prematch", "live"):
            detail = result.get(part) or {}
            when = detail.get("captured_at")
            marker = "last_" + part + "_scan"
            if not when or when == self.audit.get(marker):
                continue
            self.audit[marker] = when
            for bucket in (self.audit.setdefault(part, {}),
                           self.session_audit.setdefault(part, {})):
                bucket["scans"] = int(bucket.get("scans") or 0) + 1
                for key in ("upcoming", "selected", "analysed", "priced",
                            "errors", "live_total", "eligible"):
                    if key in detail:
                        bucket[key] = int(bucket.get(key) or 0) + int(detail.get(key) or 0)
                for key, value in (detail.get("filters") or {}).items():
                    bucket[key] = int(bucket.get(key) or 0) + int(value or 0)
        if (result.get("prematch") or {}).get("captured_at"):
            rows = self.audit.setdefault("prematch_candidates", [])
            seen = {str(row.get("event_id") or "") for row in rows}
            # Audit only counts new event IDs; the same candidate can recur
            # across PREMATCH scans and daily 'priced' is not distinct bets.
            seen_all = {str(e) for e in self.audit.get("prematch_unique_ids") or []}
            for row in result.get("prematch_candidates") or []:
                match_id = str(row.get("event_id") or "")
                if match_id:
                    seen_all.add(match_id)
                if match_id and match_id not in seen:
                    rows.append({key: row.get(key) for key in (
                        "event_id", "home", "away", "selection", "odd", "start_ts")})
                    seen.add(match_id)
            self.audit["prematch_candidates"] = rows[-10:]
            self.audit["prematch_unique_ids"] = sorted(seen_all)[-1000:]
            self.audit["prematch_unique"] = len(self.audit["prematch_unique_ids"])
        _save(self.audit_path, self.audit)

    def _count_odds(self, key: str) -> None:
        for bucket in (self.audit.setdefault("odds", {}),
                       self.session_audit.setdefault("odds", {})):
            bucket[key] = int(bucket.get(key) or 0) + 1
        _save(self.audit_path, self.audit)

    def _format_audit(self) -> str:
        pre = self.audit.get("prematch") or {}
        live = self.audit.get("live") or {}
        odds = self.audit.get("odds") or {}
        return (
            f"🩺 <b>GOOL · {self.audit.get('date', '?')} · Футбол</b>\n"
            f"Режим: {html.escape(os.getenv(KEY, 'shadow').upper())}\n"
            f"🆕 После перезапуска ({self.session_started_at}): "
            f"PRE {self.session_audit['prematch'].get('analysed',0)}, "
            f"LIVE {self.session_audit['live'].get('analysed',0)}; "
            f"365Scores {self.session_audit['live'].get('scores365_checked',0)} "
            f"(подтв. {self.session_audit['live'].get('scores365_confirmed',0)}), "
            f"FotMob {self.session_audit['live'].get('fotmob_checked',0)}, "
            f"V4 BET {self.session_audit['live'].get('brain_bet',0)}, "
            f"V3 BET {self.session_audit['live'].get('v3_bet',0)}\n"            f"После перезапуска · LIVE без xG: {self.session_audit['live'].get('no_xg',0)}, "
            f"с реальным xG: {self.session_audit['live'].get('sources_with_xg',0)}, "
            f"нет динамики: {self.session_audit['live'].get('no_momentum',0)}, "
            f"низкая уверенность: {self.session_audit['live'].get('low_confidence',0)}; "
            f"1xBet проверок: {self.session_audit['odds'].get('index_checks',0)}, "
            f"отказов: {self.session_audit['odds'].get('rejected',0)}\n"
            "PREMATCH\n"
            f"Сканов: {pre.get('scans',0)} · в окне: {pre.get('upcoming',0)} · проверено: {pre.get('analysed',0)}\n"
            f"Не хватает истории матчей: {pre.get('no_history',0)} · "
            f"нет котировок: {pre.get('quote_empty',0)}\n"
            f"Не прошли фильтры: {pre.get('no_value',0)} · положительных сканов: {pre.get('priced',0)} · "
            f"уникальных матчей (с начала новой проверки): {self.audit.get('prematch_unique',0)}\n"
            f"PREMATCH F01–F05: совпадений {pre.get('scenario_matches',0)} · "
            f"с одобрением V5 {pre.get('prematch_scene_approved',0)} · "
            f"нет данных {pre.get('prematch_scene_no_data',0)}\n"
            "LIVE\n"
            f"Сканов: {live.get('scans',0)} · матчей в ленте: {live.get('live_total',0)} · проверено: {live.get('analysed',0)}\n"
            f"Нет статистики: {live.get('no_stats',0)} · нет динамики: {live.get('no_momentum',0)}\n"
            f"Слабая активность: {live.get('insufficient_activity',0)} · уверенность ниже порога: {live.get('low_confidence',0)}\n"
            f"Вероятность ниже порога: {live.get('low_probability',0)} · V4 BET: {live.get('brain_bet',0)}\n"
            f"LIVE L01–L05 (Brain V3): проверено {live.get('v3_evaluated',0)} · READY {live.get('v3_ready',0)} · "
            f"BET {live.get('v3_bet',0)} · сценариев MATCH {live.get('live_scenario_match',0)}\n"
            f"V4 Policy: проверено {live.get('v4_policy_reviewed',0)} · "
            f"STRONG {live.get('v4_policy_strong',0)} · "
            f"недостаточно данных {live.get('v4_policy_poor_data',0)}\n"
            f"V3: не хватает 5-минутной истории {live.get('v3_no_5m',0)} · "
            f"мало снимков {live.get('v3_memory_insufficient',0)} · "
            f"низкое качество данных {live.get('v3_low_quality',0)}\n"            f"V3 production: STRONG_LIVE {live.get('v3_strong_live',0)} · "
            f"контраргумент ТМ {live.get('v3_under_block',0)} · "
            f"ждёт подтверждения {live.get('v3_selection_wait',0)} · "
            f"отсеяно финальным отбором {live.get('v3_selection_block',0)}\n"
            f"Без xG: {live.get('no_xg',0)} · нет решения: {live.get('no_decision',0)}\n"
            f"FotMob: проверок {live.get('fotmob_checked',0)} · подтвердил данные {live.get('fotmob_confirmed',0)}\n"
            f"365Scores: проверок {live.get('scores365_checked',0)} · подтвердил матч {live.get('scores365_confirmed',0)} · "
            f"из них c xG {live.get('scores365_with_xg',0)} · без xG {live.get('scores365_without_xg',0)} · "
            f"ошибки {live.get('scores365_error',0)}\n"
            f"Доп. проверка: из давления {live.get('secondary_pressure',0)} · диагностических {live.get('secondary_diagnostic',0)}\n"            f"Источник данных: повторно использовано подтверждений {live.get('secondary_cache_used',0)} · "
            f"динамика старого сборщика {live.get('momentum_old_collector',0)} · "
            f"перезапущена после разрыва/гола {live.get('momentum_reset',0)} · "
            f"слишком большой интервал {live.get('momentum_gap',0)}\n"
            "КОЭФФИЦИЕНТЫ LIVE\n"
            f"Проверок: {odds.get('index_checks',0)} · пустая линия: {odds.get('index_empty',0)}\n"
            f"Котировки подтверждены: {odds.get('priced',0)} · отклонены: {odds.get('rejected',0)}\n"
            f"Причины отказов 1xBet: {', '.join(str(k[7:])+'='+str(v) for k,v in sorted(odds.items()) if k.startswith('reject_')) or 'ещё нет'}\n"            + "".join(
                f"Сопоставление: {html.escape(str(row.get('flashscore')))} / "
                f"1xBet: {html.escape(str(row.get('closest_1xbet')))} "
                f"(совпадение {row.get('similarity')}, в индексе {row.get('index_rows')})\n"
                for row in (self.audit.get("index_match_debug") or [])[-2:]
            )

            f"Отправлено сигналов: {len(self.journal)} · "
            f"SHADOW (не отправлялись): {len(self.shadow_journal)}\n"
            "В SHADOW рассылка отключена. Причины отсева показаны выше."
            + "".join(
                f"\nНайден: {html.escape(str(r.get('home')))} — "
                f"{html.escape(str(r.get('away')))} · "
                f"{html.escape(str(r.get('selection')))} @{r.get('odd')} "
                "(исторический кандидат, не текущая ставка)"
                for r in (self.audit.get("prematch_candidates") or [])[-3:]
            )
        )

    def collect(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        result = self.brain.collect_once()
        self.last_state = dict(result)
        self._track_scans(result)
        pre = result.get("prematch") or {}
        if not pre.get("cached"):
            for candidate in result.get("prematch_candidates") or []:
                self._deliver(dict(candidate), now=now)
        live = result.get("live") or {}
        candidates = [
            d for d in live.get("decisions") or []
            if d.get("decision") == "BET"
            and _numeric(d.get("confidence")) >= max(
                0.62, _numeric(d.get("confidence_required"), .78)
            )
            and not any(r.get("event_id") == d.get("event_id") and r.get("phase") == "LIVE"
                        for r in self.journal)
            and (is_active() or not any(
                r.get("event_id") == d.get("event_id") and r.get("phase") == "LIVE"
                for r in self.shadow_journal
            ))
        ]
        # Original V3 has its own LIVE scenarios and may independently approve
        # the same fixture as V4. Price at most one decision per match, prioritizing
        # the original authoritative V3 so no duplicate correlated calls/cards.
        candidates.sort(key=lambda d: (
            0 if d.get("brain") == "ORIGINAL_BRAIN_V3"
            else 1 if d.get("brain") == "ORIGINAL_LIVE_V4"
            else 2
        ))
        deduped: list[dict[str, Any]] = []
        seen_live: set[str] = set()
        for decision in candidates:
            key = str(decision.get("event_id") or "")
            if key and key not in seen_live:
                deduped.append(decision)
                seen_live.add(key)
        candidates = deduped
        # At most one bookmaker index per qualifying cycle, no constant scraping.
        if candidates and now >= self._next_live_odds:
            self._next_live_odds = now + 150
            try:
                collector = self.price_adapter._get_collector()
                _, index = collector._fetch_index()
                self._count_odds("index_checks")
                if not index:
                    self._count_odds("index_empty")
                for decision in candidates[:2]:
                    priced = self.price_adapter.price(decision, index, now=now)
                    if priced:
                        self._count_odds("priced")
                        self._deliver(priced, now=now)
                    else:
                        self._count_odds("rejected")
                        why = str(getattr(self.price_adapter, "last_reject_reason", None) or "UNKNOWN")
                        if why not in {"MODEL_VALUE_GATE", "ODDS_PAIR_INVALID", "TARGET_LINE_MISSING",
                                       "GAME_SCORE_MISMATCH", "NAME_MISMATCH", "NAME_SIDE_MISMATCH", "MATCH_AMBIGUOUS", "MATCH_NOT_FOUND",
                                       "STALE_DECISION", "UNSUPPORTED_MARKET", "SCORE_MISSING",
                                       "SCORE_INVALID", "BOOK_ID_MISSING", "GAME_UNAVAILABLE",
                                       "WRONG_1H_MINUTE", "WRONG_2H_MINUTE", "NO_BRAIN_BET"}:
                            why = "UNKNOWN"
                        self._count_odds("reject_" + why)
                        info = getattr(self.price_adapter, "last_match_debug", None)
                        if why in {"NAME_MISMATCH", "NAME_SIDE_MISMATCH", "MATCH_AMBIGUOUS", "MATCH_NOT_FOUND"} and isinstance(info, dict):
                            self.audit["index_match_debug"] = (
                                [*self.audit.get("index_match_debug", []), info][-3:]
                            )
                            _save(self.audit_path, self.audit)
                        print(f"GOOL_LITE_LIVE_ODDS_REJECT reason={why}", flush=True)
            except Exception as exc:
                print(f"GOOL_LITE_LIVE_ODDS_ERROR {type(exc).__name__}:{exc}", flush=True)
        return result

    def reconcile(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        rows = [r for r in [*self.journal, *self.shadow_journal]
                if r.get("result") == "pending"]
        if not rows or now < self._next_settle:
            self._announce_results(now=now)
            return 0
        self._next_settle = now + 300
        try:
            states = self.brain.provider.event_states({r.get("event_id") for r in rows})
        except Exception as exc:
            print(f"GOOL_LITE_SETTLE_FEED_ERROR {type(exc).__name__}:{exc}", flush=True)
            return 0
        changed = 0
        timelines: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            state = (states or {}).get(str(r.get("event_id") or "")) or {}
            if not state:
                continue
            outcome = self._settle_one(r, state, timelines)
            if outcome:
                r["result"] = outcome
                r["settled_at"] = datetime.fromtimestamp(now, timezone.utc).isoformat()
                changed += 1
        if changed:
            self._save_journal()
            _save(self.shadow_journal_path, self.shadow_journal[-250:])
            print(f"GOOL_LITE_SETTLED count={changed}", flush=True)
        self._announce_results(now=now)
        return changed

    def _announce_results(self, *, now: float) -> None:
        """Notify each settled wager once; do not turn shadow analysis into messages."""
        if not is_active() or now < self._next_notice:
            return
        self._next_notice = now + 60
        changed = False
        for row in self.journal:
            if (row.get("result") not in ("won", "lost")
                    or row.get("result_notified")
                    or not row.get("sent")):
                continue
            label = "✅ ЗАШЛО" if row["result"] == "won" else "❌ НЕ ЗАШЛО"
            message = (
                f"{label} · ФУТБОЛ\\n"
                f"{html.escape(str(row.get('home')))} — {html.escape(str(row.get('away')))}\\n"
                f"{html.escape(str(row.get('selection')))} @{row.get('odd')}\\n"
                f"{html.escape(str(row.get('phase')))}"
            )
            if telegram.broadcast(message) > 0:
                row["result_notified"] = True
                changed = True
        if changed:
            self._save_journal()

    def _settle_one(self, r: dict[str, Any], state: dict[str, Any],
                    timelines: dict[str, list[dict[str, Any]]]) -> str | None:
        phase = str(r.get("phase") or "")
        kind = str(r.get("market") or "")
        finished = bool(state.get("is_finished"))
        total = int(state.get("home_score") or 0) + int(state.get("away_score") or 0)
        if phase == "LIVE" and kind == "ANOTHER_GOAL":
            baseline = int(r.get("entry_total") or 0)
            if total < baseline:
                return None  # mismatched/restarted scoreboard
            if total > baseline:
                return "won"
            return "lost" if finished else None
        if phase == "PREMATCH" and finished:
            # Preserve the unchanged original Football V5 market/selection IDs.
            # Do not settle them through the old Lite FT_OVER_2.5 alias.
            choice = str(r.get("original_selection") or "").casefold().strip()
            if kind in {"match_total", "home_total", "away_total"}:
                parts = choice.split()
                if len(parts) != 2 or parts[0] not in {"over", "under"}:
                    return None
                try:
                    line = float(parts[1])
                except (TypeError, ValueError):
                    return None
                home_goals = int(state.get("home_score") or 0)
                away_goals = int(state.get("away_score") or 0)
                goals = total if kind == "match_total" else home_goals if kind == "home_total" else away_goals
                if goals == line:
                    return "push"
                return "won" if (goals > line) == (parts[0] == "over") else "lost"
            if kind == "btts":
                if choice not in {"yes", "no"}:
                    return None
                happened = int(state.get("home_score") or 0) > 0 and int(state.get("away_score") or 0) > 0
                return "won" if happened == (choice == "yes") else "lost"
            if kind == "match_1x2":
                if choice not in {"home", "draw", "away"}:
                    return None
                hg, ag = int(state.get("home_score") or 0), int(state.get("away_score") or 0)
                result = "home" if hg > ag else "away" if hg < ag else "draw"
                return "won" if choice == result else "lost"
            if kind == "FT_OVER_2.5":
                return "won" if total >= 3 else "lost"
            if kind == "FT_UNDER_2.5":
                return "won" if total <= 2 else "lost"
            if kind == "BTTS_YES":
                return "won" if int(state.get("home_score") or 0) > 0 and int(state.get("away_score") or 0) > 0 else "lost"
            if kind == "BTTS_NO":
                return "lost" if int(state.get("home_score") or 0) > 0 and int(state.get("away_score") or 0) > 0 else "won"
        if kind not in ("1H_OVER_0.5", "GOAL_BEFORE_HT") or not finished:
            return None
        event_id = str(r.get("event_id") or "")
        if event_id not in timelines:
            try:
                timelines[event_id] = self.brain.provider.fetch_goal_timeline(event_id)
            except Exception:
                timelines[event_id] = []
        goals = timelines[event_id]
        # For 1H settlement, ensure goal feed covers all recorded FT goals.
        if total and len(goals) != total:
            return None
        first = [g for g in goals if g.get("period") == "1H"]
        if kind == "1H_OVER_0.5":
            return "won" if first else "lost"
        baseline = int(r.get("entry_total") or 0)
        entry_min = int(r.get("minute") or 0)
        if any(int(g.get("minute") or 0) > entry_min
               and sum(g.get("score") or []) > baseline for g in first):
            return "won"
        return "lost"

    def _format_status(self) -> str:
        pre = self.last_state.get("prematch") or {}
        live = self.last_state.get("live") or {}
        return (
            "⚽ <b>GOOL · оригинальный футбол V5 / LIVE V3+V4</b>\n"
            f"Режим: {os.getenv(KEY, 'shadow').upper()}\n"
            f"PREMATCH V5 · просмотрено {pre.get('analysed', 0)} · "
            f"кандидатов по истории и коэффициентам {pre.get('priced', 0)}\n"
            f"LIVE · матчей {live.get('live_total', 0)} · "
            f"разобрано {live.get('analysed', 0)}\n"
            f"Журнал · {len(self.journal)} отправленных сигналов\n"
            f"За день PREMATCH: {int((self.audit.get('prematch') or {}).get('priced') or 0)} положительных сканов; "
            f"F01–F05 совпадений {(self.audit.get('prematch') or {}).get('scenario_matches',0)}\n"
            f"За день LIVE: V3 BET {(self.audit.get('live') or {}).get('v3_bet',0)} · "
            f"V4 BET {(self.audit.get('live') or {}).get('brain_bet',0)} · "
            f"V4 Policy STRONG {(self.audit.get('live') or {}).get('v4_policy_strong',0)}\n"
            f"SHADOW-журнал: {len(self.shadow_journal)} проверенных кандидатов · /shadowjournal\n"
            "Причины отсутствия сигналов: /audit. LIVE требует актуальных котировок 1xBet."
        )

    def _journal_rows(self, *, phase: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.journal if phase is None or r.get("phase") == phase]

    def _summary(self, rows: list[dict[str, Any]]) -> str:
        won = sum(r.get("result") == "won" for r in rows)
        lost = sum(r.get("result") == "lost" for r in rows)
        pending = sum(r.get("result") == "pending" for r in rows)
        pnl = sum((_numeric(r.get("odd")) - 1 if r.get("result") == "won" else -1)
                  for r in rows if r.get("result") in ("won", "lost"))
        return f"{len(rows)} ставок · ✅{won} ❌{lost} ⏳{pending} · P/L {pnl:+.2f}u"

    def _format_journal(self, phase: str | None = None) -> str:
        rows = self._journal_rows(phase=phase)
        msg = f"📒 <b>GOOL Футбол · {html.escape(phase or 'весь журнал')}</b>\n{self._summary(rows)}"
        for r in rows[-12:]:
            msg += (
                f"\n{html.escape(str(r.get('home')))} — {html.escape(str(r.get('away')))}"
                f" · {html.escape(str(r.get('selection')))} @{r.get('odd')}"
                f" {'✅' if r.get('result')=='won' else '❌' if r.get('result')=='lost' else '⏳'}"
            )
        return msg[:3900]

    def _format_shadow_journal(self) -> str:
        rows = self.shadow_journal
        result = f"🔎 <b>GOOL · проверенные SHADOW-кандидаты: {len(rows)}</b>\n"
        result += "Это наблюдения с реальными котировками, а не отправленные ставки.\n"
        result += self._summary(rows)
        for row in rows[-12:]:
            marker = {"won": "✅", "lost": "❌", "push": "↩️"}.get(row.get("result"), "⏳")
            result += (f"\n{marker} {html.escape(str(row.get('home')))} — "
                       f"{html.escape(str(row.get('away')))} · "
                       f"{html.escape(str(row.get('selection')))} @{row.get('odd')} "
                       f"({html.escape(str(row.get('phase')))})")
        return result[:3900]

    def _format_prematch(self) -> str:
        rows = self.last_state.get("prematch_candidates") or []
        msg = "🟡 <b>Футбол PREMATCH · кандидаты (не гарантированные ставки)</b>"
        if not rows:
            return msg + "\nПока нет отобранных котировок."
        for row in rows[:8]:
            msg += (f"\n{html.escape(str(row.get('home')))} — "
                    f"{html.escape(str(row.get('away')))} · "
                    f"{html.escape(str(row.get('selection')))} @{row.get('odd')}")
        return msg[:3900]

    def _format_live(self) -> str:
        rows = (self.last_state.get("live") or {}).get("decisions") or []
        msg = "🔴 <b>Футбол LIVE · последние проверки</b>"
        for row in rows[:8]:
            msg += (f"\n{html.escape(str(row.get('match')))} · {row.get('minute')}′"
                    f" {row.get('market')} {row.get('decision')}")
        return msg if rows else msg + "\nПока нет анализируемых матчей."

    def _format_parlay(self) -> str:
        # Independent fixtures only. No forced express when there are no legs.
        current = [r for r in self.last_state.get("prematch_candidates") or []
                   if _numeric(r.get("odd")) >= 1.4 and _numeric(r.get("model_probability")) >= .74
                   and _numeric(r.get("start_ts")) > time.time() + 300]
        unique: dict[str, dict[str, Any]] = {}
        for r in current:
            unique.setdefault(str(r.get("event_id")), r)
        legs = list(unique.values())[:2]
        if len(legs) != 2:
            return "🔗 Футбол · пока нет двух разных подходящих матчей для экспресса."
        odd = _numeric(legs[0].get("odd")) * _numeric(legs[1].get("odd"))
        return ("🔗 <b>Пробный экспресс · футбол</b>\n"
                + "\n".join(f"{html.escape(str(r.get('home')))} — {html.escape(str(r.get('away')))}: "
                            f"{html.escape(str(r.get('selection')))} @{r.get('odd')}" for r in legs)
                + f"\nОбщий коэффициент ≈{odd:.2f}\n⚠️ Вероятности не откалиброваны.")

    def _report_bytes(self, *, day: str | None = None) -> tuple[str, bytes]:
        date_str = day or datetime.now(TZ).strftime("%Y-%m-%d")
        rows = [r for r in self.journal if
                datetime.fromtimestamp(_numeric(r.get("created_ts")), TZ).strftime("%Y-%m-%d") == date_str]
        content = [
            "<!DOCTYPE html><html lang='ru'><meta charset='UTF-8'>"
            "<title>GOOL Футбол: отчёт</title><style>"
            "body{font:16px sans-serif;max-width:1050px;margin:30px auto;padding:0 15px}"
            "table{border-collapse:collapse;width:100%}"
            "td,th{padding:8px;border-bottom:1px solid #aaa;text-align:left}"
            "tr:nth-child(even){background:#eee}</style>",
            f"<h1>GOOL Футбол · {html.escape(date_str)}</h1>",
            f"<p>{html.escape(self._summary(rows))}</p>",
            "<p>Официальный результат может поступить с задержкой. Результаты не гарантируют прибыль.</p>",
            "<table><tr><th>Матч</th><th>Тип</th><th>Рынок</th>"
            "<th>Коэф.</th><th>Результат</th></tr>",
        ]
        for r in rows:
            content.append("<tr>" + "".join(
                f"<td>{html.escape(str(x))}</td>" for x in (
                    str(r.get("home")) + " — " + str(r.get("away")),
                    r.get("phase"), r.get("selection"),
                    r.get("odd"), r.get("result"),
                )
            ) + "</tr>")
        content.append("</table></html>")
        return f"GOOL_FOOTBALL_{date_str}.html", "".join(content).encode("utf-8")

    def handle(self, command: str, chat_id: int | str) -> None:
        cmd = command.split(maxsplit=1)[0].split("@", 1)[0].lower() if command else ""
        if cmd == "/start":
            telegram.subscribe(chat_id)
            response = "⚽ GOOL Lite · Футбол. Команды: /status /audit /shadowjournal /oddscheck /prematch /live /journal /express /dayreport /stop"
        elif cmd == "/stop":
            telegram.unsubscribe(chat_id)
            response = "🔕 Уведомления отключены."
        elif cmd in ("/status", "/football"):
            response = self._format_status()
        elif cmd in ("/audit", "/debug", "/diagnostics"):
            response = self._format_audit()
        elif cmd == "/oddscheck":
            try:
                collector = self.price_adapter._get_collector()
                _, rows = collector._fetch_index()
                self._count_odds("manual_index_checks")
                source = str(getattr(collector, "_index_source", "неизвестен"))
                cached = bool(getattr(collector, "_index_cache_used", False))
                response = (
                    "⚽ <b>GOOL · проверка источника 1xBet</b>\n"
                    f"Найдено событий футбола: {len(rows or [])}\n"
                    f"Источник: {html.escape(source)} · кэш: {'да' if cached else 'нет'}\n"
                    "Это тест списка матчей, не проверка доступности LIVE-котировок."
                )
            except Exception as exc:
                response = (
                    f"⚠️ 1xBet: {html.escape(type(exc).__name__)}\n"
                    "Смотри консоль; котировки пока не подтверждены."
                )
                print(f"GOOL_LITE_ODDSCHECK_ERROR {type(exc).__name__}:{exc}", flush=True)
        elif cmd in ("/prematch", "/footballprematch"):
            response = self._format_prematch()
        elif cmd in ("/live", "/footballlive"):
            response = self._format_live()
        elif cmd in ("/journal", "/footballjournal"):
            response = self._format_journal()
        elif cmd in ("/shadowjournal", "/shadow", "/testjournal"):
            response = self._format_shadow_journal()
        elif cmd in ("/prematchjournal", "/livejournal"):
            response = self._format_journal("PREMATCH" if cmd == "/prematchjournal" else "LIVE")
        elif cmd in ("/express", "/parlays"):
            response = self._format_parlay()
        elif cmd in ("/dayreport", "/fullreport"):
            filename, data = self._report_bytes()
            telegram.send_document(chat_id, filename, data, caption="GOOL Футбол · отчёт за сегодня",
                                   content_type="text/html")
            return
        elif cmd in ("/super10", "/super"):
            response = "SUPER 10 временно недоступен: запущен только футбол. Нужны 10 разных проверенных матчей."
        else:
            response = "⚽ Команды: /status /audit /prematch /live /journal /express /dayreport /stop"
        telegram.send_message(chat_id, response[:3900])

    def poll(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        if now < self._next_poll or not os.getenv("TELEGRAM_BOT_TOKEN"):
            return 0
        self._next_poll = now + 12
        offset = int(_numeric(_load_offset(self.offset_path)))
        resp = telegram._api_call(
            "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["message"]}, timeout=7
        )
        if not isinstance(resp, dict) or not resp.get("ok"):
            return 0
        changed = 0
        for item in resp.get("result") or []:
            offset = max(offset, int(item.get("update_id") or 0) + 1)
            # Commit offset before dispatch: restart must not duplicate action.
            _save_offset(self.offset_path, offset)
            message = item.get("message") or {}
            cid = (message.get("chat") or {}).get("id")
            if cid is None:
                continue
            self.handle(str(message.get("text") or ""), cid)
            changed += 1
        return changed


def _load_offset(path: Path) -> str:
    try:
        return path.read_text("utf8")
    except OSError:
        return "0"


def _save_offset(path: Path, value: int):
    path.write_text(str(value), encoding="utf8")


def run() -> None:
    root = Path(os.getenv("RUNTIME_DATA_DIR", "/home/container/gool-lite-data"))
    root.mkdir(parents=True, exist_ok=True)
    service = FootballHost(root)
    stopping = False

    def shutdown(*_: Any):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print("GOOL_FOOTBALL_ONLY_STARTED " + json.dumps({
        "mode": os.getenv(KEY, "shadow"), "sport": "football",
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }), flush=True)
    next_collect = 0.0
    while not stopping:
        now = time.time()
        if now >= next_collect:
            next_collect = now + max(90, int(os.getenv("GOOL_LITE_CYCLE_SECONDS", "120")))
            try:
                service.collect(now=now)
            except Exception as exc:
                print(f"GOOL_FOOTBALL_COLLECT_ERROR {type(exc).__name__}:{exc}", flush=True)
            print("GOOL_FOOTBALL_HEALTH " + json.dumps({
                "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                "journal": len(service.journal),
            }), flush=True)
        try:
            service.reconcile()
            service.poll()
        except Exception as exc:
            print(f"GOOL_FOOTBALL_TELEGRAM_ERROR {type(exc).__name__}:{exc}", flush=True)
        time.sleep(2)


def smoke() -> None:
    """Offline construction only; no Telegram or provider requests."""
    os.environ["GOOL_FOOTBALL_LITE_MODE"] = "shadow"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    with tempfile.TemporaryDirectory(prefix="gool-football-smoke-") as folder:
        service = FootballHost(Path(folder))
        assert service.brain.state_path.parent == Path(folder) / "live"
        service.price_adapter._get_collector()  # price decoder lazy import and startup memory
        assert "футбол" in service._format_status().casefold()
        print("GOOL_FOOTBALL_SMOKE " + json.dumps({
            "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            "ok": True,
        }), flush=True)
