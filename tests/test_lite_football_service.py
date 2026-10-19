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


def test_score_parser_requires_verified_team_scores():
    assert _game_score({"SC": {"FS": {"S1": 0, "S2": 2}}}) == (0, 2)
    assert _game_score({"SC": {}}) is None
