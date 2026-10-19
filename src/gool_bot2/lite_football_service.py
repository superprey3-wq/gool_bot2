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
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from . import telegram
from .lite_football import LiteFootballWorker, _load, _save
from .providers.common import pair_score

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
    score = game.get("SC") or {}
    children = (score.get("FS") or {}, score, game)
    for block in children:
        if not isinstance(block, dict):
            continue
        try:
            if block.get("S1") is not None and block.get("S2") is not None:
                return (int(float(block["S1"])), int(float(block["S2"])))
        except (ValueError, TypeError):
            continue
    return None


class LivePriceAdapter:
    """Look up only promising live matches; never price from a stale index alone."""

    def __init__(self, data_root: Path, collector: Any = None):
        self.collector = collector
        self.data_root = data_root

    def _get_collector(self):
        if self.collector is None:
            from .xbet_market_robust import RobustXBetMarketCollector
            root = self.data_root / "live"
            self.collector = RobustXBetMarketCollector(
                root / "football_lite_xbet_unused_state.json",
                root / "football_lite_xbet_unused_history.jsonl",
            )
        return self.collector

    def price(self, decision: dict[str, Any], index: list[dict[str, Any]], *, now: float) -> dict[str, Any] | None:
        """Fail closed unless bookmaker identity, score and both live prices agree."""
        if decision.get("decision") != "BET":
            return None
        if now - _numeric(decision.get("observed_at")) > 60:
            return None
        scores = decision.get("score") or []
        if len(scores) != 2:
            return None
        try:
            scoreboard = (int(scores[0]), int(scores[1]))
        except (ValueError, TypeError):
            return None
        c = self._get_collector()
        fixture = SimpleNamespace(
            provider_match_id=str(decision.get("event_id") or ""),
            home=str(decision.get("home") or ""),
            away=str(decision.get("away") or ""),
        )
        best = max(
            (index or []),
            key=lambda row: pair_score(
                fixture.home, fixture.away, str(row.get("home") or ""), str(row.get("away") or "")
            ),
            default=None,
        )
        if not best:
            return None
        sim = pair_score(fixture.home, fixture.away, str(best["home"]), str(best["away"]))
        if sim < 0.88:
            return None
        book_id = str(best.get("event_id") or "")
        if not book_id:
            return None
        # Every quote and score are fetched anew. Never trust historical index odds.
        game = c._game(book_id)
        if not isinstance(game, dict) or _game_score(game) != scoreboard:
            return None
        minute = int(decision.get("minute") or 0)
        market = str(decision.get("market") or "")
        if market == "GOAL_BEFORE_HT":
            if not (10 <= minute <= 45):
                return None
            lines = (c._markets_for_event(book_id, minute, sum(scoreboard)) or {}).get("first_half_total") or []
            family = "first_half_total"
        elif market == "ANOTHER_GOAL":
            if not (46 <= minute <= 85):
                return None
            from .xbet_market_robust import decode_standard_markets
            lines = decode_standard_markets(game).get("match_total") or []
            family = "match_total"
        else:
            return None
        target = float(sum(scoreboard)) + 0.5
        matching = next((row for row in lines
                         if abs(_numeric(row.get("line"), -999.0) - target) < 0.001), None)
        if not matching:
            return None
        over = _numeric(matching.get("over"))
        under = _numeric(matching.get("under"))
        if not (1.40 <= over <= 3.25 and under > 1.01):
            return None
        fair = (1 / over) / ((1 / over) + (1 / under))
        p = _numeric(decision.get("probability"))
        confidence = _numeric(decision.get("confidence"))
        if p < 0.70 or confidence < 0.78 or p - fair < 0.06 or p * over - 1 < 0.05:
            return None
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
        self.offset_path = self.live / "football_telegram_offset.txt"
        self.journal = _load(self.journal_path, [])
        if not isinstance(self.journal, list):
            self.journal = []
        self.price_adapter = price_adapter if price_adapter is not None else LivePriceAdapter(self.root)
        self.last_state: dict[str, Any] = dict(_load(self.brain.state_path, {}))
        self._next_poll = 0.0
        self._next_live_odds = 0.0
        self._next_settle = 0.0
        self._next_notice = 0.0

    def _save_journal(self):
        # Bound storage by age and number; never discard pending bets.
        now = time.time()
        finished = [r for r in self.journal
                    if r.get("result") != "pending"
                    and now - _numeric(r.get("created_ts")) < 21 * 86400]
        pending = [r for r in self.journal if r.get("result") == "pending"]
        self.journal = (finished[-550:] + pending[-250:])
        _save(self.journal_path, self.journal)

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

    def collect(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        result = self.brain.collect_once()
        self.last_state = dict(result)
        pre = result.get("prematch") or {}
        if not pre.get("cached"):
            for candidate in result.get("prematch_candidates") or []:
                self._deliver(dict(candidate), now=now)
        live = result.get("live") or {}
        candidates = [
            d for d in live.get("decisions") or []
            if d.get("decision") == "BET"
            and _numeric(d.get("confidence")) >= 0.78
            and not any(r.get("event_id") == d.get("event_id") and r.get("phase") == "LIVE"
                        for r in self.journal)
        ]
        # At most one bookmaker index per qualifying cycle, no constant scraping.
        if candidates and now >= self._next_live_odds:
            self._next_live_odds = now + 150
            try:
                collector = self.price_adapter._get_collector()
                _, index = collector._fetch_index()
                for decision in candidates[:2]:
                    priced = self.price_adapter.price(decision, index, now=now)
                    if priced:
                        self._deliver(priced, now=now)
            except Exception as exc:
                print(f"GOOL_LITE_LIVE_ODDS_ERROR {type(exc).__name__}:{exc}", flush=True)
        return result

    def reconcile(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        rows = [r for r in self.journal if r.get("result") == "pending"]
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
            "⚽ <b>GOOL Lite · только футбол</b>\n"
            f"Режим: {os.getenv(KEY, 'shadow').upper()}\n"
            f"PREMATCH · просмотрено {pre.get('analysed', 0)} · "
            f"сценариев {pre.get('scenario_matches', 0)} · котировок {pre.get('priced', 0)}\n"
            f"LIVE · матчей {live.get('live_total', 0)} · "
            f"разобрано {live.get('analysed', 0)}\n"
            f"Журнал · {len(self.journal)} сигналов\n"
            "Гол/моменты LIVE требуют подтверждения котировок 1xBet."
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
            response = "⚽ GOOL Lite · Футбол. Команды: /status /prematch /live /journal /express /dayreport /stop"
        elif cmd == "/stop":
            telegram.unsubscribe(chat_id)
            response = "🔕 Уведомления отключены."
        elif cmd in ("/status", "/football"):
            response = self._format_status()
        elif cmd in ("/prematch", "/footballprematch"):
            response = self._format_prematch()
        elif cmd in ("/live", "/footballlive"):
            response = self._format_live()
        elif cmd in ("/journal", "/footballjournal"):
            response = self._format_journal()
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
            response = "⚽ Команды: /status /prematch /live /journal /express /dayreport /stop"
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
