from pathlib import Path

from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker, SPORTS


def _total_game(line):
    return {
        "GE": [{"E": [[
            {"G": 4, "T": 9, "P": line, "C": 1.85},
            {"G": 4, "T": 10, "P": line, "C": 1.90},
        ]]}]
    }


def test_market_tree_fetches_only_requested_live_scopes(tmp_path: Path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    calls = []
    q1, q2, q3, q4, h1, h2 = "q1", "q2", "q3", "q4", "h1", "h2"
    game = _total_game(160.5)
    game["SG"] = [
        {"I": q1, "PN": "1st quarter"},
        {"I": q2, "PN": "2nd quarter"},
        {"I": q3, "PN": "3rd quarter"},
        {"I": q4, "PN": "4th quarter"},
        {"I": h1, "PN": "1 Half"},
        {"I": h2, "PN": "2 Half"},
    ]

    def fake(sub_id, cfg, prematch):
        calls.append(sub_id)
        return _total_game(40.5 if sub_id.startswith("q") else 80.5)

    monkeypatch.setattr(worker, "_cached_subgame", fake)
    decoded, meta = worker._market_tree(
        game,
        SPORTS["basketball"],
        prematch=False,
        wanted_scopes={"FULL_MATCH", "QUARTER_2", "FIRST_HALF"},
    )
    assert set(decoded) == {"FULL_MATCH", "QUARTER_2", "FIRST_HALF"}
    assert set(calls) == {q2, h1}
    assert meta["coverage"]["QUARTER_2"]["match_total_lines"] == 1


def test_prematch_market_tree_still_fetches_all_scheduled_scopes(tmp_path: Path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    calls = []
    game = _total_game(5.5)
    game["SG"] = [
        {"I": "p1", "PN": "1st period"},
        {"I": "p2", "PN": "2nd period"},
        {"I": "p3", "PN": "3rd period"},
    ]

    def fake(sub_id, cfg, prematch):
        calls.append(sub_id)
        return _total_game(1.5)

    monkeypatch.setattr(worker, "_cached_subgame", fake)
    decoded, _ = worker._market_tree(game, SPORTS["hockey"], prematch=True)
    assert {"FULL_MATCH", "PERIOD_1", "PERIOD_2", "PERIOD_3"}.issubset(decoded)
    assert set(calls) == {"p1", "p2", "p3"}
