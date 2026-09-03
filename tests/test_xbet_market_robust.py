from __future__ import annotations

from pathlib import Path

from gool_bot2 import xbet_market_pressure as market
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector, decode_standard_markets


def _payload(*events):
    return {"Value": [{"I": event_id, "O1": home, "O2": away} for event_id, home, away in events]}


def test_decode_standard_markets_keeps_x5_and_real_first_half_subgame():
    game = {
        "GE": [{
            "G": 4,
            "E": [
                [
                    {"T": 9, "P": 1.5, "C": 1.40, "G": 4},
                    {"T": 9, "P": 2.0, "C": 1.85, "G": 4},
                    {"T": 9, "P": 2.5, "C": 2.40, "G": 4},
                ],
                [
                    {"T": 10, "P": 1.5, "C": 2.70, "G": 4},
                    {"T": 10, "P": 2.0, "C": 1.80, "G": 4},
                    {"T": 10, "P": 2.5, "C": 1.45, "G": 4},
                ],
            ],
        }],
        "SG": [
            {
                "P": 1,
                "PN": "1st half",
                "GE": [{
                    "G": 4,
                    "E": [
                        [
                            {"T": 9, "P": 0.5, "C": 1.28, "G": 4},
                            {"T": 9, "P": 1.0, "C": 2.10, "G": 4},
                            {"T": 9, "P": 1.5, "C": 3.20, "G": 4},
                        ],
                        [
                            {"T": 10, "P": 0.5, "C": 3.40, "G": 4},
                            {"T": 10, "P": 1.0, "C": 1.62, "G": 4},
                            {"T": 10, "P": 1.5, "C": 1.30, "G": 4},
                        ],
                    ],
                }],
            },
            {
                "P": 2,
                "PN": "2nd half",
                "GE": [{
                    "G": 4,
                    "E": [[{"T": 9, "P": 0.5, "C": 1.50, "G": 4}], [{"T": 10, "P": 0.5, "C": 2.20, "G": 4}]],
                }],
            },
        ],
    }

    decoded = decode_standard_markets(game)

    assert [row["line"] for row in decoded["match_total"]] == [1.5, 2.5]
    assert [row["line"] for row in decoded["first_half_total"]] == [0.5, 1.5]
    assert all(row["line"] != 1.0 for row in decoded["first_half_total"])
    assert decoded["first_half_total"][0]["over"] == 1.28
    assert decoded["first_half_total"][0]["under"] == 3.40


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
    assert collector._index_cache_used is False


def test_empty_index_cycle_reuses_recent_ids_but_not_old_market_data(monkeypatch, tmp_path: Path):
    roots = ["https://a/LiveFeed", "https://b/LiveFeed"]
    monkeypatch.setattr(market, "ROOTS", roots)
    monkeypatch.setattr(market, "INDEX_QUERIES", ["q=1"])
    monkeypatch.setenv("XBET_INDEX_CACHE_SECONDS", "45")
    phase = {"empty": False}

    def fake_http(url: str, timeout: float = 8.0):
        if "Get1x2_VZip" in url:
            if phase["empty"]:
                return {"Value": []}
            return _payload(("42", "Alpha", "Beta"))
        if "GetGameZip" in url:
            return {"Value": {"I": "42", "SC": {"FS": {"S1": 0, "S2": 0}}, "GE": []}}
        return None

    monkeypatch.setattr(market, "_http_json", fake_http)
    collector = RobustXBetMarketCollector(tmp_path / "state.json", tmp_path / "history.jsonl")

    _, first = collector._fetch_index()
    phase["empty"] = True
    root, second = collector._fetch_index()

    assert {row["event_id"] for row in first} == {"42"}
    assert {row["event_id"] for row in second} == {"42"}
    assert root is not None
    assert collector._index_cache_used is True
    assert collector._index_cache_age_seconds is not None
    assert collector._game("42") is not None


def test_expired_index_cache_is_not_reused(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(market, "ROOTS", ["https://a/LiveFeed"])
    monkeypatch.setattr(market, "INDEX_QUERIES", ["q=1"])
    monkeypatch.setenv("XBET_INDEX_CACHE_SECONDS", "0")
    monkeypatch.setattr(market, "_http_json", lambda *args, **kwargs: {"Value": []})
    collector = RobustXBetMarketCollector(tmp_path / "state.json", tmp_path / "history.jsonl")
    collector._last_index_rows = [{"event_id": "old", "home": "Old", "away": "Game", "root": "https://a/LiveFeed"}]
    collector._last_index_at = 1.0

    root, rows = collector._fetch_index()

    assert root is None
    assert rows == []
    assert collector._index_cache_used is False


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
