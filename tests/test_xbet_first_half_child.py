from __future__ import annotations

from pathlib import Path

from gool_bot2.xbet_market_robust import RobustXBetMarketCollector


def test_sparse_parent_fetches_first_half_child_game(monkeypatch, tmp_path: Path):
    parent = {
        "GE": [],
        "SG": [{"P": 1, "PN": "1st half", "I": "child-1"}],
    }
    child = {
        "GE": [{
            "G": 4,
            "E": [
                [
                    {"T": 9, "P": 0.5, "C": 1.75, "G": 4},
                    {"T": 9, "P": 1.0, "C": 3.10, "G": 4},
                ],
                [
                    {"T": 10, "P": 0.5, "C": 2.00, "G": 4},
                    {"T": 10, "P": 1.0, "C": 1.25, "G": 4},
                ],
            ],
        }],
    }
    collector = RobustXBetMarketCollector(tmp_path / "state.json", tmp_path / "history.jsonl")
    calls: list[str] = []

    def fake_game(event_id: str):
        calls.append(str(event_id))
        return parent if str(event_id) == "parent" else child if str(event_id) == "child-1" else None

    monkeypatch.setattr(collector, "_game", fake_game)

    markets = collector._markets_for_event("parent", minute=20, current_total=0)

    assert markets is not None
    assert calls == ["parent", "child-1"]
    assert markets["first_half_total"] == [{"line": 0.5, "over": 1.75, "under": 2.0}]


def test_second_half_does_not_fetch_first_half_child(monkeypatch, tmp_path: Path):
    parent = {"GE": [], "SG": [{"P": 1, "PN": "1st half", "I": "child-1"}]}
    collector = RobustXBetMarketCollector(tmp_path / "state.json", tmp_path / "history.jsonl")
    calls: list[str] = []

    def fake_game(event_id: str):
        calls.append(str(event_id))
        return parent

    monkeypatch.setattr(collector, "_game", fake_game)

    markets = collector._markets_for_event("parent", minute=55, current_total=0)

    assert markets is not None
    assert calls == ["parent"]
    assert markets["first_half_total"] == []
