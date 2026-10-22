"""Standalone ORIGINAL GOOL football service (no trained models, no other sports).

Keeps production StorageLiveSnapshotCollector, Brain V3 memory+decision,
LIVE Brain V4, GOOL Football V5, and RobustXBetMarketCollector unchanged.
Only the orchestration and small Telegram/gateway interface are new.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import resource
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("GOOL_HOCKEY_ENABLED", "0")
os.environ.setdefault("GOOL_BASKETBALL_ENABLED", "0")
os.environ.setdefault("GOOL_MULTISPORT_ENABLED", "0")
os.environ.setdefault("GOOL_MULTISPORT_EMBEDDED_ENABLED", "0")
os.environ.setdefault("GOOL_BROWSER_ENABLE", "0")
os.environ.setdefault("GOOL_FOOTBALL_AUTOINSTALL", "0")
os.environ.setdefault("GOOL_GLOBAL_SUPER10_ENABLED", "0")
os.environ.setdefault("GOOL_BRAIN_V3_LEARNING_ENABLED", "0")
os.environ.setdefault("LIVE_DETAIL_WORKERS", "2")
os.environ.setdefault("LIVE_HISTORY_WORKERS", "1")
os.environ.setdefault("STORAGE_CLEANUP_EVERY_CYCLES", "2")
os.environ.setdefault("RUNTIME_DATA_DIR", str(ROOT / "football_original_data"))

def active() -> bool:
    return os.getenv("GOOL_ORIGINAL_FOOTBALL_MODE", "shadow").casefold() == "active"

def save_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")
    temp.replace(path)

def read_json(path: Path, default: object):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default

def tg_call(method: str, payload: dict, timeout: int = 12) -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {}
    req = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        print(f"FOOTBALL_ORIGINAL_TELEGRAM_ERROR method={method} {type(exc).__name__}", flush=True)
        return {}

def send_text(text: str, chat: str | None = None) -> bool:
    chat = chat or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat:
        return False
    return bool(tg_call("sendMessage", {
        "chat_id": str(chat), "text": text[:3900], "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).get("ok"))

def ram_mib() -> float:
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return round(peak / 1024 if sys.platform != "darwin" else peak / 1048576, 1)

class FootballOriginalService:
    def __init__(self, root: Path, *, collector=None, gateway=None, provider=None):
        self.root = root
        self.live_path = root / "live"
        self.live_path.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.live_path / "original_football_journal.json"
        self.journal = read_json(self.journal_path, [])
        self.audit_path = self.live_path / "original_football_audit.json"
        self.audit = read_json(self.audit_path, {})
        self.audit.setdefault("stats_scans", 0)
        self.audit.setdefault("history", {})
        self.audit.setdefault("price_rejections", {})
        self.audit.setdefault("last_live", [])
        self.audit.setdefault("last_prematch", [])
        self.audit.setdefault("brain", {})
        self.audit.setdefault("start", datetime.now(timezone.utc).isoformat())
        self.seen: set[str] = {
            str(x.get("entry_key")) for x in self.journal if x.get("entry_key")
        }
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.last_pre = 0.
        self.shadow_seen: set[str] = set()
        self._prev_shadow: list[dict] = []
        if collector is None:
            from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector
            collector = StorageLiveSnapshotCollector(
                data_dir=self.root / "raw" / "live", prefilter_threshold=50,
                secondary_interval_minutes=3,
            )
        self.collector = collector
        self.flashscore = provider or self.collector.flashscore
        if gateway is None:
            from gool_bot2.football_original_price_gateway import OriginalFootballPriceGateway
            gateway = OriginalFootballPriceGateway(root)
        self.gateway = gateway
        self._pending_records: list[dict] = []
        # Reuse the original collector with no replacement HTTP logic.
        original_append = self.collector._append

        def append_and_collect(row, now):
            original_append(row, now)
            if isinstance(row, dict):
                with self.lock:
                    self._pending_records.append(row)
        self.collector._append = append_and_collect

    @staticmethod
    def _quality(record: dict) -> float:
        from gool_bot2.match_context import provider_pair
        usable = sum(
            1 for key in ("shots", "shots_on_target", "corners", "xg",
                          "big_chances", "dangerous_attacks", "touches_box")
            if all(v is not None for v in provider_pair(record, key))
        )
        # This is only an observation-completeness measure, not a trained model
        # or artificial xG.
        return round(min(1.0, usable / 4.0), 3)

    def evaluate(self, record: dict, now: float | None = None) -> list[dict]:
        from gool_bot2.brain_v3_memory import build_brain_v3_memory, _HISTORY
        from gool_bot2.brain_v3_decision import evaluate_brain_v3
        from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
        match = record.get("match") or {}
        mid = str(match.get("flashscore_event_id") or "")
        minute = int(match.get("minute") or 0)
        if not mid:
            return []
        if match.get("is_finished"):
            _HISTORY.pop(mid, None)
            return []
        if not 1 <= minute <= 95 or match.get("is_halftime"):
            return []
        build_brain_v3_memory(record, mid)
        v3 = evaluate_brain_v3(record, data_quality=self._quality(record))
        v4 = evaluate_live_goals(record)
        qualified = []
        self.audit["brain"]["evaluated"] = int(self.audit["brain"].get("evaluated") or 0) + 1
        self.audit["brain"]["v3_" + str(v3.get("status"))] = int(
            self.audit["brain"].get("v3_" + str(v3.get("status"))) or 0) + 1
        if v3.get("status") == "BET" and v3.get("strategy") in ("goal_before_ht", "another_goal"):
            qualified.append({
                "event_id": mid, "home": match.get("home"), "away": match.get("away"),
                "league": match.get("league"), "minute": minute,
                "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
                "observed_at": now or time.time(), "decision": "BET",
                "market": "GOAL_BEFORE_HT" if v3["strategy"] == "goal_before_ht" else "ANOTHER_GOAL",
                "probability": float(v3.get("probability") or 0),
                "confidence": float(v3.get("probability") or 0),
                "confidence_required": max(0.62, float(v3.get("bet_min") or 0.70)),
                "brain": "ORIGINAL_BRAIN_V3",
            })
        for row in v4:
            if row.decision != "BET":
                continue
            if qualified and row.market == qualified[0]["market"]:
                continue
            qualified.append({
                "event_id": mid, "home": match.get("home"), "away": match.get("away"),
                "league": match.get("league"), "minute": minute,
                "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
                "observed_at": now or time.time(), "decision": "BET",
                "market": row.market, "probability": row.probability,
                "confidence": row.confidence,
                "confidence_required": .78, "brain": "ORIGINAL_LIVE_V4",
            })
        self.audit["brain"]["bet_candidates"] = int(self.audit["brain"].get("bet_candidates") or 0) + len(qualified)
        return qualified

    def _record_signal(self, item: dict) -> bool:
        entry_key = f"{item.get('phase')}|{item.get('event_id')}|{item.get('market')}|{item.get('selection')}"
        if entry_key in self.seen:
            return False
        result = dict(item)
        result["entry_key"] = entry_key
        result["captured_at"] = datetime.now(timezone.utc).isoformat()
        body = (
            f"⚽ <b>GOOL ORIGINAL · {html.escape(str(item.get('phase')))}</b>\n"
            f"{html.escape(str(item.get('home')))} — {html.escape(str(item.get('away')))}\n"
            f"{html.escape(str(item.get('selection')))} @ <b>{item.get('odd')}</b>\n"
            f"Источник: {html.escape(str(item.get('bookmaker')))} · "
            f"мозг: {html.escape(str(item.get('brain')))}"
        )
        if not active():
            return False
        if not send_text(body):
            return False
        with self.lock:
            self.seen.add(entry_key)
            self.journal.append(result)
            self.journal = self.journal[-2000:]
            save_json(self.journal_path, self.journal)
        return True

    def scan(self) -> dict:
        start = time.monotonic()
        self._pending_records = []
        details = self.collector.collect_once()
        with self.lock:
            records = list(self._pending_records)
            self._pending_records.clear()
        records.sort(key=lambda r: (str((r.get("match") or {}).get("flashscore_event_id") or ""),
                                    str(r.get("captured_at") or "")))
        bets: list[dict] = []
        for record in records:
            try:
                bets.extend(self.evaluate(record))
            except Exception as exc:
                self.audit["brain"]["errors"] = int(self.audit["brain"].get("errors") or 0) + 1
                print(f"GOOL_ORIGINAL_BRAIN_ERROR {type(exc).__name__}:{exc}", flush=True)
        self.audit["stats_scans"] += 1
        self.audit["collector"] = details
        self.audit["runtime_seconds"] = round(time.monotonic()-start, 1)
        self.audit["memory_mib"] = ram_mib()
        self.audit["last_live"] = [dict(x) for x in bets][-8:]
        if bets:
            try:
                collector = self.gateway._get_collector()
                _, index = collector._fetch_index()
                self.audit["odds_index_size"] = len(index)
                self.audit["odds_checks"] = int(self.audit.get("odds_checks") or 0) + 1
                # Only one 1xBet index request per scan for ALL qualifying bets.
                for row in bets[:5]:
                    priced = self.gateway.price(row, index, now=time.time())
                    if priced is None:
                        reason = self.gateway.last_reject_reason or "UNKNOWN"
                        d = self.audit["price_rejections"]
                        d[reason] = int(d.get(reason) or 0) + 1
                    else:
                        self.audit["priced"] = int(self.audit.get("priced") or 0) + 1
                        priced["brain"] = row["brain"]
                        if not active():
                            self._prev_shadow = [priced] + self._prev_shadow[:9]
                        self._record_signal(priced)
            except Exception as exc:
                self.audit["odds_errors"] = int(self.audit.get("odds_errors") or 0) + 1
                print(f"GOOL_ORIGINAL_ODDS_ERROR {type(exc).__name__}:{exc}", flush=True)
        save_json(self.audit_path, self.audit)
        print("GOOL_ORIGINAL_SCAN " + json.dumps({
            "collector":details, "bet":len(bets),
            "priced":self.audit.get("priced",0), "memory_mib":ram_mib(),
            "elapsed":self.audit["runtime_seconds"],
        },ensure_ascii=False), flush=True)
        return details

    def scan_prematch(self, *, now: float | None = None) -> int:
        from gool_bot2.flashscore_odds import fetch_event_odds
        from gool_bot2.football_original_v5_bridge import original_v5_picks
        now = time.time() if now is None else now
        if now - self.last_pre < max(600,int(os.getenv("GOOL_ORIGINAL_PRE_INTERVAL", "1800"))):
            return 0
        self.last_pre = now
        scanned = 0
        out: list[dict] = []
        try:
            upcoming = self.flashscore.scheduled_matches_for_day(0)
        except Exception as exc:
            print(f"GOOL_ORIGINAL_PRE_FEED_ERROR {type(exc).__name__}:{exc}", flush=True)
            return 0
        games = [m for m in upcoming if now+180 <= float((m.meta or {}).get("scheduled_start_ts") or 0) <= now+14400]
        cap = max(1,min(20,int(os.getenv("GOOL_ORIGINAL_PRE_MAX", "6"))))
        offset = int(self.audit.get("prematch_cursor") or 0) % max(1,len(games))
        selected = [games[(offset+i)%len(games)] for i in range(min(cap,len(games)))] if games else []
        self.audit["prematch_cursor"] = (offset+len(selected))%max(1,len(games))
        for match in selected:
            eid = str(match.provider_match_id)
            try:
                history = self.flashscore.fetch_match_history(eid,match.home,match.away,limit=10)
                quote = fetch_event_odds(eid,timeout=7)
                picks,info = original_v5_picks(
                    history=history or {},raw_odds=quote or {},event_id=eid,
                    home=match.home,away=match.away,league=match.league or "",
                    kickoff=float((match.meta or {}).get("scheduled_start_ts") or 0),
                )
                scanned += 1
                for pick in picks[:1]:
                    out.append(pick)
                    if not active():
                        self._prev_shadow = [pick] + self._prev_shadow[:9]
                    self._record_signal(pick)
            except Exception as exc:
                print(f"GOOL_ORIGINAL_PRE_ERROR event={eid} {type(exc).__name__}:{exc}",flush=True)
                self.audit["prematch_errors"] = int(self.audit.get("prematch_errors") or 0)+1
        self.audit["prematch_scans"] = int(self.audit.get("prematch_scans") or 0) + scanned
        self.audit["prematch_candidates"] = int(self.audit.get("prematch_candidates") or 0) + len(out)
        self.audit["last_prematch"] = out[-8:]
        save_json(self.audit_path,self.audit)
        return scanned

    def status(self) -> str:
        a=self.audit
        d=a.get("collector") or {}
        b=a.get("brain") or {}
        errors=", ".join(f"{html.escape(str(k))}={v}" for k,v in (a.get("price_rejections") or {}).items()) or "нет"
        return (
            "⚽ <b>GOOL · ОРИГИНАЛЬНЫЙ ФУТБОЛ · NO ML</b>\n"
            f"Режим: {('ACTIVE' if active() else 'SHADOW')}\n"
            f"Flashscore LIVE: {d.get('live',0)}; подробно: {d.get('detail',0)}/{d.get('entry_window',0)} "
            f"(охват {d.get('entry_window_coverage_pct',0)}%)\n"
            f"V3 анализов: {b.get('evaluated',0)} · BET: {b.get('v3_BET',0)}\n"
            f"V4+V3 кандидатов: {b.get('bet_candidates',0)} · 1xBet индекс: {a.get('odds_index_size',0)}\n"
            f"PREMATCH проверено: {a.get('prematch_scans',0)} · кандидатов V5: {a.get('prematch_candidates',0)}\n"
            f"Котировок подтверждено: {a.get('priced',0)} · отправлено: {len(self.journal)}\n"
            f"1xBet отклонения: {errors}\n"
            f"RAM пик: {a.get('memory_mib',0)} МБ · цикл: {a.get('runtime_seconds',0)} c\n"
            "Модели ML, хоккей, баскетбол и Chromium отключены."
        )

    def recent(self) -> str:
        recent=self._prev_shadow[-8:] or self.audit.get("last_prematch") or self.audit.get("last_live") or []
        parts=["⚽ <b>GOOL · последние кандидаты (не выданные ставки)</b>"]
        for item in recent[-8:]:
            parts.append(
                f"{html.escape(str(item.get('home')))} — {html.escape(str(item.get('away')))} · "
                f"{html.escape(str(item.get('selection') or item.get('market')))} @ {item.get('odd','—')}"
            )
        if len(parts)==1:
            parts.append("Кандидатов пока нет")
        return "\n".join(parts)

    def journal_text(self) -> str:
        parts=["📒 <b>GOOL · отправленные футбольные ставки</b>"]
        for item in self.journal[-12:]:
            parts.append(f"{html.escape(str(item.get('home')))} — {html.escape(str(item.get('away')))} · "
                         f"{html.escape(str(item.get('selection')))} @{item.get('odd')}")
        if len(parts)==1:
            parts.append("Отправленных ставок пока нет")
        return "\n".join(parts)

    def poll(self, offset: int) -> int:
        result=tg_call("getUpdates",{"offset":offset,"timeout":0,"limit":20},timeout=6)
        for row in result.get("result") or []:
            offset=max(offset,int(row.get("update_id") or 0)+1)
            message=row.get("message") or {}
            chat=str((message.get("chat") or {}).get("id") or "")
            owner=os.getenv("TELEGRAM_CHAT_ID","").strip()
            if owner and chat != owner:
                continue
            cmd=str(message.get("text") or "").split()[0].lower() if message.get("text") else ""
            if cmd in ("/start","/status","/audit","/football"):
                send_text(self.status(),chat)
            elif cmd in ("/prematch","/live","/candidates"):
                send_text(self.recent(),chat)
            elif cmd in ("/journal","/dayreport"):
                send_text(self.journal_text(),chat)
            elif cmd=="/stop":
                send_text("Для отключения рассылки: GOOL_ORIGINAL_FOOTBALL_MODE=shadow",chat)
        return offset

    def run(self, *, once: bool=False) -> None:
        if not once and not (os.getenv("TELEGRAM_CHAT_ID") and os.getenv("TELEGRAM_BOT_TOKEN")):
            raise SystemExit("GOOL_ORIGINAL_FOOTBALL_MISSING_TELEGRAM_CREDS")
        print(f"GOOL_ORIGINAL_FOOTBALL_NO_ML_STARTED mode={'active' if active() else 'shadow'}",flush=True)
        offset=int(read_json(self.live_path / "telegram_offset.json",0))
        from gool_bot2.storage_runtime import startup_cleanup
        startup_cleanup()
        try:
            while not self.stop_event.is_set():
                started=time.monotonic()
                try:
                    self.scan()
                except Exception as exc:
                    print(f"GOOL_ORIGINAL_COLLECTOR_ERROR {type(exc).__name__}:{exc}",flush=True)
                try:
                    self.scan_prematch()
                except Exception as exc:
                    print(f"GOOL_ORIGINAL_PRE_FAILURE {type(exc).__name__}:{exc}",flush=True)
                if once:
                    break
                end=started+max(60,int(os.getenv("GOOL_ORIGINAL_LIVE_INTERVAL","90")))
                while not self.stop_event.is_set() and time.monotonic()<end:
                    offset=self.poll(offset)
                    save_json(self.live_path/"telegram_offset.json",offset)
                    self.stop_event.wait(min(8,max(0.01,end-time.monotonic())))
        finally:
            self.collector.stop()

def smoke() -> None:
    from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector
    from gool_bot2.brain_v3_memory import build_brain_v3_memory
    from gool_bot2.brain_v3_decision import evaluate_brain_v3
    from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
    from gool_bot2.football_v5_production import production_v5_picks
    from gool_bot2.football_original_price_gateway import OriginalFootballPriceGateway
    assert all((StorageLiveSnapshotCollector, build_brain_v3_memory,
                evaluate_brain_v3,evaluate_live_goals,production_v5_picks,
                OriginalFootballPriceGateway))
    print("GOOL_ORIGINAL_NO_ML_SMOKE "+json.dumps({
        "ok":True,"peak_rss_mib":ram_mib(),"engines":["V3","V4","V5"],
        "trained_model_files":"none",
    }),flush=True)

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--smoke",action="store_true")
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    if args.smoke:
        smoke()
        return
    host=FootballOriginalService(Path(os.environ["RUNTIME_DATA_DIR"]))
    def stop(*_):
        host.stop_event.set()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    host.run(once=args.once)

if __name__=="__main__":
    main()
