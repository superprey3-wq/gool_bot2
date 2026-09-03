from __future__ import annotations

from pathlib import Path

from gool_bot2 import xbet_market_pressure as market
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector


def _payload(*events):
    return {"Value": [{"I": event_id, "O1": home, "O2": away} for event_id, home, away in events]}


def test_fetch_index_merges_partial_roots(monkeypatch, tmp_path: Path):
    roots = ["https://a/LiveFeed", "https://b/LiveFeed"]
    queries = ["q=1", "q=2"]
    monkeypatch.setattr(market, "ROOTS", roots)
    monkeypatch.setattr(market, "INDEX_QUERIES", queries)

    def fake_http(url: str, timeout: float = 8.0):
        if url == "https://a/LiveFeed/Get1x2_VZip?q=1":
            return _payload(("1", "Alpha", "Beta"), ("2", "Gamma", "Delta"))
        if url == "https://a/LiveFeed/Get1x2_VZip?q=2":
            return _payload(("2", "Gamma", "Delta"))
        if url == "https://b/LiveFeed/Get1x2_VZip?q=1":
            return _payload(("2", "Gamma", "Delta"), ("3", "Epsilon", "Zeta"))
        if url == "https://b/LiveFeed/Get1x2_VZip?q=2":
            return None
        return None

    monkeypatch.setattr(market, "_http_json", fake_http)
    collector = RobustXBetMarketCollector(tmp_path / "state.json", tmp_path / "history.jsonl")
    collector.active_root = roots[0]

    root, rows = collector._fetch_index()

    assert root == roots[0]
    assert {row["event_id"] for row in rows} == {"1", "2", "3"}
    assert collector._index_root_counts == {roots[0]: 2, roots[1]: 2}
    assert set(collector._event_roots["2"]) == set(roots)


def test_game_tries_event_source_root_before_other_mirrors(monkeypatch, tmp_path: Path):
    roots = ["https://a/LiveFeed", "https://b/LiveFeed", "https://c/LiveFeed"]
    monkeypatch.setattr(market, "ROOTS", roots)
    calls = []

    def fake_http(url: str, timeout: float = 8.0):
        calls.append(url)
        if url.startswith("https://b/LiveFeed/GetGameZip"):
            return {"Value": {"I": "42", "SC": {"FS": {"S1": 0, "S2": 0}}}}
        return None

    monkeypatch.setattr(market, "_http_json", fake_http)
    collector = RobustXBetMarketCollector(tmp_path / "state.json", tmp_path / "history.jsonl")
    collector.active_root = roots[0]
    collector._event_roots = {"42": [roots[1]]}

    game = collector._game("42")

    assert game is not None
    assert calls
    assert calls[0].startswith("https://b/LiveFeed/GetGameZip")
    assert collector.active_root == roots[1]
