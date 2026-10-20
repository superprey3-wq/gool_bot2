"""Offline contract tests: GOOL Lite now runs ONLY football."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gool_bot2.lite_football_service import FootballHost, LivePriceAdapter, _game_score


class FakeCollector:
    def __init__(self, *, score=(0, 0), quote=True):
        self.score = score
        self.quote = quote
        self.called = []

    def _game(self, event_id):
        self.called.append(("game", event_id))
        return {"SC": {"FS": {"S1": self.score[0], "S2": self.score[1]}}}

    def _markets_for_event(self, event_id, minute, current_total):
        self.called.append(("market", event_id))
        if not self.quote:
            return None
        return {"first_half_total": [{"line": 0.5, "over": 1.65, "under": 2.20}]}

    def _fetch_index(self):
        return ("live", [{"event_id": "123", "home": "Alpha FC", "away": "Beta FC"}])


def _decision():
    return {
        "event_id": "ABCDEFGH", "home": "Alpha FC", "away": "Beta FC",
        "league": "Test", "phase": "LIVE",
        "market": "GOAL_BEFORE_HT", "decision": "BET",
        "observed_at": time.time(), "minute": 28,
        "score": [0, 0], "probability": 0.82, "confidence": 0.88,
    }


def test_live_odds_accepts_same_game_and_score(tmp_path):
    index = [{"event_id": "123", "home": "Alpha FC", "away": "Beta FC"}]
    gateway = LivePriceAdapter(tmp_path, collector=FakeCollector())
    row = gateway.price(_decision(), index, now=time.time())
    assert row is not None
    assert row["market"] == "GOAL_BEFORE_HT"
    assert row["selection"] == "ТБ 0.5 (1-й тайм)"
    assert row["odd"] == 1.65
    assert row["bookmaker"] == "1xBet LIVE"


def test_live_odds_refuse_score_mismatch_or_missing_line(tmp_path):
    index = [{"event_id": "123", "home": "Alpha FC", "away": "Beta FC"}]
    assert LivePriceAdapter(tmp_path, FakeCollector(score=(1, 0))).price(
        _decision(), index, now=time.time()
    ) is None
    assert LivePriceAdapter(tmp_path, FakeCollector(quote=False)).price(
        _decision(), index, now=time.time()
    ) is None


def test_live_odds_refuse_wrong_teams_old_snapshot_and_no_index(tmp_path):
    gateway = LivePriceAdapter(tmp_path, FakeCollector())
    index = [{"event_id": "123", "home": "Other FC", "away": "Unrelated FC"}]
    assert gateway.price(_decision(), index, now=time.time()) is None
    old = _decision()
    old["observed_at"] -= 70
    assert gateway.price(old, [], now=time.time()) is None


class Provider:
    def event_states(self, ids):
        return {"ABCDEFGH": {"is_finished": True, "home_score": 2, "away_score": 1}}

    def fetch_goal_timeline(self, event_id):
        assert event_id == "ABCDEFGH"
        return [
            {"minute": 22, "period": "1H", "score": [1, 0]},
            {"minute": 68, "period": "2H", "score": [2, 0]},
            {"minute": 88, "period": "2H", "score": [2, 1]},
        ]


class FakeBrain:
    def __init__(self, root):
        self.state_path = root / "live" / "gool_lite_football_state.json"
        self.provider = Provider()

    def collect_once(self):
        return {
            "captured_at": "2026-10-20T00:00:00Z",
            "prematch": {"cached": True},
            "live": {"cached": True},
            "prematch_candidates": [],
        }


def make_host(tmp_path):
    return FootballHost(tmp_path, brain=FakeBrain(tmp_path), price_adapter=LivePriceAdapter(tmp_path, FakeCollector()))


def test_no_auto_delivery_in_shadow_and_deduplicate_when_active(tmp_path, monkeypatch):
    from gool_bot2 import telegram
    host = make_host(tmp_path)
    payload = {
        "phase": "PREMATCH", "event_id": "ABCDEFGH",
        "home": "Alpha FC", "away": "Beta FC", "selection": "ТБ 2.5",
        "market": "FT_OVER_2.5", "odd": 1.75, "bookmaker": "Test",
        "model_probability": 0.75, "expected_value": 0.31,
        "start_ts": time.time() + 3600,
    }
    sent = []
    monkeypatch.setattr(telegram, "broadcast", lambda text: sent.append(text) or 1)
    monkeypatch.delenv("GOOL_FOOTBALL_LITE_MODE", raising=False)
    assert not host._deliver(payload, now=time.time())
    monkeypatch.setenv("GOOL_FOOTBALL_LITE_MODE", "active")
    assert host._deliver(payload, now=time.time())
    assert not host._deliver(payload, now=time.time())
    assert len(sent) == 1
    assert len(make_host(tmp_path).journal) == 1  # idempotent across restarts


def test_official_settlement_first_half_and_full_time(tmp_path):
    h = make_host(tmp_path)
    finished = {"is_finished": True, "home_score": 2, "away_score": 1}
    rows = [
        {"phase": "PREMATCH", "market": "FT_OVER_2.5", "event_id": "ABCDEFGH"},
        {"phase": "PREMATCH", "market": "FT_UNDER_2.5", "event_id": "ABCDEFGH"},
        {"phase": "PREMATCH", "market": "1H_OVER_0.5", "event_id": "ABCDEFGH"},
        {"phase": "LIVE", "market": "GOAL_BEFORE_HT", "event_id": "ABCDEFGH",
         "minute": 11, "entry_total": 0},
        {"phase": "LIVE", "market": "ANOTHER_GOAL", "event_id": "ABCDEFGH",
         "entry_total": 2},
    ]
    got = [h._settle_one(row, finished, {}) for row in rows]
    assert got == ["won", "lost", "won", "won", "won"]


def test_dayreport_and_commands_only_football(tmp_path, monkeypatch):
    from gool_bot2 import telegram
    h = make_host(tmp_path)
    sent = []
    documents = []
    monkeypatch.setattr(telegram, "send_message", lambda cid, msg: sent.append(msg) or True)
    monkeypatch.setattr(telegram, "send_document", lambda cid, name, payload, **kwargs:
                        documents.append((name, payload)) or True)
    monkeypatch.setattr(telegram, "subscribe", lambda *args: True)
    for cmd in ("/start", "/status", "/prematch", "/live", "/journal", "/express", "/super10"):
        h.handle(cmd, 12345)
    h.handle("/dayreport", 12345)
    assert len(sent) == 7
    assert len(documents) == 1
    assert documents[0][0].endswith(".html")
    assert b"<html" in documents[0][1]
    assert "Хоккей" not in sent[0]
    assert "Баскетбол" not in sent[0]


def test_score_parser_matches_original_1xbet_score_epoch_semantics():
    # GetGameZip routinely omits zero-valued sides. The original GOOL
    # guards accept SC.FS={} as 0:0 and SC.FS={S1:3} as 3:0.
    from gool_bot2.xbet_score_epoch_guard import extract_xbet_score
    cases = [
        ({"SC": {"FS": {"S1": 0, "S2": 2}}}, (0, 2)),
        ({"SC": {"FS": {}}}, (0, 0)),
        ({"SC": {"FS": {"S1": 3}}}, (3, 0)),
        ({"SC": {"FS": {"S2": 1}}}, (0, 1)),
        ({"SC": {}}, None),
        ({"SC": {"FS": None}}, None),
    ]
    for game, expected in cases:
        assert _game_score(game) == expected
        assert extract_xbet_score(game) == expected


def test_live_real_1xbet_omitted_zero_score_gets_priced(tmp_path):
    class OriginalZeroSideCollector(FakeCollector):
        def _game(self, event_id):
            self.called.append(("game", event_id))
            return {"SC": {"FS": {}}}
    gateway = LivePriceAdapter(tmp_path, collector=OriginalZeroSideCollector())
    index = [{"event_id": "123", "home": "Alpha FC", "away": "Beta FC"}]
    row = gateway.price(_decision(), index, now=time.time())
    assert row is not None
    assert row["odd"] == 1.65


def test_result_notification_is_sent_once_and_persisted(tmp_path, monkeypatch):
    from gool_bot2 import telegram
    monkeypatch.setenv("GOOL_FOOTBALL_LITE_MODE", "active")
    h = make_host(tmp_path)
    h.journal = [{
        "event_id": "ABCDEFGH", "home": "Alpha FC", "away": "Beta FC",
        "phase": "PREMATCH", "market": "FT_OVER_2.5",
        "selection": "ТБ 2.5", "odd": 1.8, "result": "won",
        "created_ts": time.time(), "sent": True,
    }]
    h._save_journal()
    notices = []
    monkeypatch.setattr(telegram, "broadcast", lambda text: notices.append(text) or 1)
    h._announce_results(now=time.time())
    h._announce_results(now=time.time() + 61)
    assert len(notices) == 1
    assert "ЗАШЛО" in notices[0]
    assert make_host(tmp_path).journal[0]["result_notified"] is True


def test_daily_audit_shows_rejections_and_does_not_double_count_cached_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_FOOTBALL_LITE_MODE", "shadow")
    h = make_host(tmp_path)
    payload = {
        "prematch": {"captured_at": "2026-10-20T06:00:00Z",
                     "analysed": 5, "upcoming": 25, "priced": 0,
                     "filters": {"no_history": 3, "no_scenario": 2, "quote_empty": 0}},
        "live": {"captured_at": "2026-10-20T06:00:00Z",
                 "analysed": 4, "live_total": 18,
                 "filters": {"no_momentum": 2, "low_confidence": 2, "brain_bet": 0}},
    }
    h._track_scans(payload)
    h._track_scans(payload)
    text = h._format_audit()
    assert "нет сценария" in text.lower()
    assert "История недоступна: 3" in text
    assert "проверено: 5" in text
    assert "мозг BET: 0" in text
    h2 = make_host(tmp_path)
    assert h2.audit["prematch"]["analysed"] == 5
    assert h2.audit["live"]["analysed"] == 4


def test_lite_live_candidate_with_valid_recalibrated_confidence_reaches_price_check(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_FOOTBALL_LITE_MODE", "shadow")
    now = time.time()
    class CandidateBrain(FakeBrain):
        def collect_once(self):
            return {
                "captured_at": "2026-10-20T10:00:00Z",
                "prematch": {"cached": True},
                "prematch_candidates": [],
                "live": {"captured_at":"2026-10-20T10:00:00Z",
                         "decisions":[{"event_id":"ABCDEFGH", "decision":"BET",
                                       "confidence":.68, "confidence_required":.62,
                                       "observed_at":now}],
                         "filters":{"brain_bet":1}, "analysed":1},
            }
    class Adapter:
        def _get_collector(self):
            class Collector:
                def _fetch_index(self):
                    return "fresh", [{"event_id":"123", "home":"Alpha", "away":"Beta"}]
            return Collector()
        def price(self, decision, index, now):
            return {"event_id": "ABCDEFGH", "phase":"LIVE",
                    "selection":"ТБ 0.5", "odd":1.7, "model_probability":.81,
                    "bookmaker":"1xBet LIVE", "expected_value":.35}
    h = FootballHost(tmp_path, brain=CandidateBrain(tmp_path), price_adapter=Adapter())
    h.collect(now=now)
    assert h.audit["odds"]["priced"] == 1
    assert h.journal == []  # SHADOW must not create a sent signal


def test_audit_has_real_newlines_and_fotmob_breakdown(tmp_path):
    h = make_host(tmp_path)
    h.audit["live"] = {
        "scans": 6, "analysed": 27, "no_xg": 27,
        "fotmob_checked": 3, "fotmob_confirmed": 1,
    }
    msg = h._format_audit()
    assert "\\n" not in msg
    assert "\nLIVE\n" in msg
    assert "FotMob: проверок 3" in msg
    assert "подтвердил данные 1" in msg


def test_oddscheck_reports_index_without_claiming_live_odds(tmp_path, monkeypatch):
    from gool_bot2 import telegram
    h = make_host(tmp_path)
    notices = []
    monkeypatch.setattr(telegram, "send_message", lambda cid, msg: notices.append(msg) or True)
    h.handle("/oddscheck", "12345")
    assert len(notices) == 1
    assert "Найдено событий футбола: 1" in notices[0]
    assert "не проверка доступности LIVE-котировок" in notices[0]


def test_audit_separates_daily_counters_from_new_process_session(tmp_path):
    h = make_host(tmp_path)
    snapshot = {
        "prematch": {"captured_at": "2026-10-20T13:00:01Z", "analysed": 5,
                     "filters":{"priced": 1}},
        "live": {"captured_at": "2026-10-20T13:00:01Z", "analysed": 4,
                 "filters":{"scores365_checked":1, "secondary_diagnostic":1}},
    }
    h._track_scans(snapshot)
    assert h.session_audit["live"]["scores365_checked"] == 1
    assert h.session_audit["prematch"]["analysed"] == 5
    newer = make_host(tmp_path)
    assert newer.audit["live"]["scores365_checked"] == 1
    assert newer.session_audit["live"] == {}
    assert "После перезапуска" in newer._format_audit()


def test_rejected_live_odds_remember_safe_reason_without_raw_book_payload(tmp_path):
    index = [{"event_id": "123", "home": "Alpha FC", "away": "Beta FC"}]
    mismatch = LivePriceAdapter(tmp_path, FakeCollector(score=(2, 1)))
    assert mismatch.price(_decision(), index, now=time.time()) is None
    assert mismatch.last_reject_reason == "GAME_SCORE_MISMATCH"
    absent = LivePriceAdapter(tmp_path, FakeCollector(quote=False))
    assert absent.price(_decision(), index, now=time.time()) is None
    assert absent.last_reject_reason == "TARGET_LINE_MISSING"
    valid = LivePriceAdapter(tmp_path, FakeCollector())
    assert valid.price(_decision(), index, now=time.time()) is not None
    assert valid.last_reject_reason is None


def test_audit_reports_live_rejection_reason(tmp_path):
    h = make_host(tmp_path)
    h.audit["odds"] = {"index_checks": 1, "rejected": 1,
                       "reject_GAME_SCORE_MISMATCH": 1}
    assert "GAME_SCORE_MISMATCH=1" in h._format_audit()
