from __future__ import annotations

from gool_bot2.xbet_multisport_steam import (
    SPORTS,
    MultiSportSteamWorker,
    _balanced_total,
    _metric,
    _score,
    _score_candidates,
    _score_sync_allowed,
    _segment_clock_seconds,
    _v3_to_legacy_market_game,
    detect_prematch_steam,
    detect_steam,
    settle_multisport_pick,
)


def test_score_reads_livefeed_final_score_shape():
    game = {"SC": {"FS": {"S1": 3, "S2": 2}}}
    assert _score(game) == (3, 2)


def test_balanced_total_uses_main_total_pair():
    game = {
        "GE": [
            {
                "E": [
                    [
                        {"G": 4, "T": 9, "P": 5.5, "C": 1.82},
                        {"G": 4, "T": 10, "P": 5.5, "C": 2.02},
                        {"G": 4, "T": 9, "P": 6.5, "C": 2.55},
                        {"G": 4, "T": 10, "P": 6.5, "C": 1.48},
                    ]
                ]
            }
        ]
    }
    row = _balanced_total(game)
    assert row is not None
    assert row["line"] == 5.5
    assert 0.50 < row["probability"] < 0.55


def test_remaining_total_metric_does_not_jump_just_because_score_changed():
    cfg = SPORTS["hockey"]
    before = {"line": 6.5, "probability": 0.50}
    after = {"line": 7.5, "probability": 0.50}
    assert _metric(before, (1, 1), cfg) == _metric(after, (2, 1), cfg)


def _rows(metrics, *, step=10.0, line=5.5, probability=0.52, over=1.82):
    return [
        {
            "ts": idx * step,
            "metric": metric,
            "probability": probability + idx * 0.01,
            "line": line + idx * 0.1,
            "over": over - idx * 0.03,
            "under": 2.05 + idx * 0.04,
        }
        for idx, metric in enumerate(metrics)
    ]


def test_hockey_strong_future_total_pressure_emits_signal():
    cfg = SPORTS["hockey"]
    rows = _rows([4.50, 4.66, 4.84, 5.05])
    signal = detect_steam(rows, cfg, now=30.0, score_changed_at=None)
    assert signal is not None
    assert signal["metric_delta"] >= cfg.min_metric_delta
    assert signal["moves"] >= cfg.min_moves


def test_hockey_recent_goal_repricing_is_guarded():
    cfg = SPORTS["hockey"]
    rows = _rows([4.50, 4.66, 4.84, 5.05])
    assert detect_steam(rows, cfg, now=30.0, score_changed_at=20.0) is None


def test_basketball_requires_material_remaining_total_shift():
    cfg = SPORTS["basketball"]
    weak = _rows([92.0, 92.5, 93.0, 93.4])
    strong = _rows([92.0, 93.2, 94.4, 96.0])
    assert detect_steam(weak, cfg, now=30.0, score_changed_at=None) is None
    signal = detect_steam(strong, cfg, now=30.0, score_changed_at=None)
    assert signal is not None
    assert signal["metric_delta"] >= cfg.min_metric_delta


def test_basketball_under_steam_is_supported():
    cfg = SPORTS["basketball"]
    rows = [
        {"ts": 0, "metric": 100.0, "probability": .54, "line": 220.5, "over": 1.78, "under": 2.02},
        {"ts": 10, "metric": 98.7, "probability": .52, "line": 219.5, "over": 1.88, "under": 1.90},
        {"ts": 20, "metric": 97.3, "probability": .49, "line": 218.5, "over": 2.02, "under": 1.78},
        {"ts": 30, "metric": 95.5, "probability": .46, "line": 216.5, "over": 2.20, "under": 1.66},
    ]
    signal = detect_steam(rows, cfg, now=30.0, score_changed_at=None)
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["odd"] == 1.66
    assert signal["probability_delta_pp"] > 0


def test_multisport_settlement_handles_over_under_and_void():
    assert settle_multisport_pick({"direction": "over", "line": 5.5}, 3, 3) == "won"
    assert settle_multisport_pick({"direction": "under", "line": 6.5}, 3, 2) == "won"
    assert settle_multisport_pick({"direction": "over", "line": 6.0}, 3, 3) == "void"


def test_hockey_prematch_line_move_emits_signal():
    cfg = SPORTS["hockey"]
    rows = [
        {"ts": 0, "metric": 6.00, "probability": .50, "line": 6.0, "over": 1.90, "under": 1.90},
        {"ts": 35, "metric": 6.22, "probability": .52, "line": 6.0, "over": 1.82, "under": 2.00},
        {"ts": 70, "metric": 6.55, "probability": .54, "line": 6.5, "over": 1.72, "under": 2.12},
    ]
    signal = detect_prematch_steam(rows, cfg, now=70.0)
    assert signal is not None
    assert signal["phase"] == "PREMATCH"
    assert signal["direction"] == "over"


def test_same_match_can_have_prematch_and_live_journal_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = __import__("gool_bot2.xbet_multisport_steam", fromlist=["MultiSportSteamWorker"]).MultiSportSteamWorker(tmp_path)
    cfg = SPORTS["hockey"]
    signal = {
        "direction": "over", "line": 6.5, "odd": 1.8, "fair_probability": .55,
        "metric_delta": .6, "probability_delta_pp": 4.0, "line_delta": .5,
        "moves": 3, "strength": 82.0, "extreme": False,
    }
    base = {
        "event_id": "101", "flashscore_event_id": "ABCDEFGH", "home": "A", "away": "B",
        "league": "L", "flashscore_match_score": .95,
    }
    prematch = {**base, "phase": "PREMATCH", "origin": "multisport_prematch", "start_ts": 9999999999}
    live = {**base, "phase": "LIVE", "origin": "multisport_live", "score": [1, 0], "period": "2nd", "clock_seconds": 100}
    assert worker._record_signal(prematch, signal, cfg)[0] is True
    assert worker._record_signal(live, signal, cfg)[0] is True
    rows = __import__("json").loads(worker.journal_path.read_text("utf-8"))
    assert {row["phase"] for row in rows} == {"PREMATCH", "LIVE"}



def test_basketball_quarter_uses_short_scope_threshold_scale():
    cfg = SPORTS["basketball"]
    rows = [
        {"ts": 0, "scope": "QUARTER_2", "market_family": "match_total", "metric": 40.0, "probability": .50, "line": 40.5, "over": 1.90, "under": 1.90},
        {"ts": 10, "scope": "QUARTER_2", "market_family": "match_total", "metric": 40.7, "probability": .515, "line": 41.0, "over": 1.84, "under": 1.96},
        {"ts": 20, "scope": "QUARTER_2", "market_family": "match_total", "metric": 41.4, "probability": .53, "line": 41.5, "over": 1.78, "under": 2.04},
        {"ts": 30, "scope": "QUARTER_2", "market_family": "match_total", "metric": 42.0, "probability": .545, "line": 42.0, "over": 1.70, "under": 2.12},
    ]
    signal = detect_steam(rows, cfg, now=30.0, score_changed_at=None)
    assert signal is not None
    assert signal["threshold_scale"] < 1.0
    assert signal["direction"] == "over"


def test_hockey_period_prematch_uses_period_scale():
    cfg = SPORTS["hockey"]
    rows = [
        {"ts": 0, "scope": "PERIOD_2", "market_family": "match_total", "metric": 1.50, "probability": .50, "line": 1.5, "over": 1.90, "under": 1.90},
        {"ts": 35, "scope": "PERIOD_2", "market_family": "match_total", "metric": 1.64, "probability": .52, "line": 1.5, "over": 1.82, "under": 2.00},
        {"ts": 70, "scope": "PERIOD_2", "market_family": "match_total", "metric": 1.82, "probability": .54, "line": 2.0, "over": 1.72, "under": 2.12},
    ]
    signal = detect_prematch_steam(rows, cfg, now=70.0)
    assert signal is not None
    assert signal["threshold_scale"] < 1.0


def test_team_total_settlement_uses_only_selected_team():
    assert settle_multisport_pick({"market_family": "home_total", "direction": "over", "line": 2.5}, 3, 8) == "won"
    assert settle_multisport_pick({"market_family": "away_total", "direction": "under", "line": 4.5}, 9, 4) == "won"



def test_live_line_move_can_confirm_signal_when_devig_probability_stays_flat():
    cfg = SPORTS["basketball"]
    rows = [
        {"ts": 0, "scope": "FULL_MATCH", "market_family": "match_total", "metric": 160.5, "probability": .50, "line": 160.5, "over": 1.90, "under": 1.90},
        {"ts": 10, "scope": "FULL_MATCH", "market_family": "match_total", "metric": 162.0, "probability": .50, "line": 162.0, "over": 1.90, "under": 1.90},
        {"ts": 20, "scope": "FULL_MATCH", "market_family": "match_total", "metric": 163.0, "probability": .50, "line": 163.0, "over": 1.90, "under": 1.90},
        {"ts": 30, "scope": "FULL_MATCH", "market_family": "match_total", "metric": 164.5, "probability": .50, "line": 164.5, "over": 1.90, "under": 1.90},
    ]
    signal = detect_steam(rows, cfg, now=30.0, score_changed_at=None)
    assert signal is not None
    assert signal["direction"] == "over"
    assert signal["line_delta"] == 4.0
    assert signal["probability_delta_pp"] == 0.0



def test_persisted_state_index_fallback_keeps_identity_not_odds(tmp_path):
    from datetime import datetime, timezone
    import json

    worker = MultiSportSteamWorker(tmp_path)
    worker.state_path.parent.mkdir(parents=True, exist_ok=True)
    worker.state_path.write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "sports": {
            "hockey": {
                "matches": [{
                    "event_id": "hx1",
                    "home": "SKA",
                    "away": "CSKA",
                    "line": 99.5,
                    "over": 9.99,
                    "under": 1.01,
                }],
                "prematch_matches": [{
                    "event_id": "hp1",
                    "home": "Dynamo",
                    "away": "Spartak",
                    "line": 88.5,
                }],
            }
        },
    }), encoding="utf-8")

    live = worker._state_index_fallback(SPORTS["hockey"], prematch=False)
    pre = worker._state_index_fallback(SPORTS["hockey"], prematch=True)
    assert live == [{"I": "hx1", "O1": "SKA", "O2": "CSKA", "_state_cache": True}]
    assert pre == [{"I": "hp1", "O1": "Dynamo", "O2": "Spartak", "_state_cache": True}]
    assert "line" not in live[0]
    assert "over" not in live[0]



def test_hockey_segment_stats_reads_actual_subgame_scores(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    game = {
        "SG": [
            {"I": 101, "PN": "2nd period", "P": 2, "TG": "Shots On Goal"},
            {"I": 102, "PN": "2nd period", "P": 2, "TG": "2-Minute Penalties"},
            {"I": 103, "PN": "2nd period", "P": 2, "TG": "Powerplay goals"},
            {"I": 104, "PN": "1st period", "P": 1, "TG": "Shots On Goal"},
        ]
    }
    payloads = {
        "101": {"SC": {"FS": {"S1": 14, "S2": 11}}},
        "102": {"SC": {"FS": {"S1": 2, "S2": 1}}},
        "103": {"SC": {"FS": {"S1": 1, "S2": 0}}},
    }
    monkeypatch.setattr(worker, "_cached_subgame", lambda sub_id, cfg, prematch=False: payloads.get(str(sub_id), {}))
    stats = worker._hockey_segment_stats(game, SPORTS["hockey"], current_period="2nd period")
    assert stats["scope"] == "PERIOD_2"
    assert stats["shots_on_goal"] == [14, 11]
    assert stats["penalties_2m"] == [2, 1]
    assert stats["powerplay_goals"] == [1, 0]



def test_collect_prefetches_both_live_indexes_before_sport_scans(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    calls = []

    def fake_index(cfg):
        calls.append(("index", cfg.key))
        return [{"I": f"{cfg.key}-1", "O1": "A", "O2": "B"}]

    def fake_pre(cfg):
        calls.append(("pre", cfg.key))
        return []

    def fake_scan(cfg, *, xbet_live_prefetched=None, xbet_prematch_prefetched=None):
        calls.append(("scan", cfg.key, len(xbet_live_prefetched or [])))
        return {
            "flashscore_live": 0, "xbet_live": len(xbet_live_prefetched or []), "mapped": 0,
            "decoded": 0, "score_mismatch": 0, "market_decode_failed": 0,
            "detected": 0, "prematch_detected": 0, "flashscore_prematch": 0,
            "prematch_decoded": 0, "delivered": 0, "prematch_delivered": 0,
            "settled": 0, "xbet_diag": {"ok": True},
        }

    monkeypatch.setattr(worker, "_xbet_index", fake_index)
    monkeypatch.setattr(worker, "_xbet_prematch_index", fake_pre)
    monkeypatch.setattr(worker, "_scan_sport", fake_scan)
    worker.collect_once()

    first_scan = next(i for i, row in enumerate(calls) if row[0] == "scan")
    assert ("index", "hockey") in calls[:first_scan]
    assert ("index", "basketball") in calls[:first_scan]
    scan_rows = [row for row in calls if row[0] == "scan"]
    assert ("scan", "hockey", 1) in scan_rows
    assert ("scan", "basketball", 1) in scan_rows


def test_basketball_live_queries_have_fallback_profiles(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    queries = worker._xbet_queries(SPORTS["basketball"])
    assert any("sports=3" in q and "antisports=188" in q and "partner=51" in q for q in queries)
    assert any("sports=3" in q and "country=153" in q and "mobi=true" in q for q in queries)
    assert any("sports=3" in q and "country=" not in q for q in queries)


def test_score_reads_current_v3_live_shape():
    game = {"scores": {"scoreOpp1": 81, "scoreOpp2": 67}}
    assert _score(game) == (81, 67)


def test_basketball_score_candidates_can_rebuild_full_score_from_quarters():
    game = {
        "SC": {
            "FS": {"S1": 50, "S2": 50},
            "PS": [
                {"Key": 1, "Value": {"S1": 15, "S2": 16, "NF": "1st quarter"}},
                {"Key": 2, "Value": {"S1": 16, "S2": 16, "NF": "2nd quarter"}},
                {"Key": 3, "Value": {"S1": 28, "S2": 22, "NF": "3rd quarter"}},
                {"Key": 4, "Value": {"S1": 22, "S2": 13, "NF": "4th quarter"}},
            ],
        }
    }
    assert (81, 67) in _score_candidates(game, SPORTS["basketball"])


def test_basketball_score_sync_allows_only_bounded_provider_lag(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_SCORE_DRIFT_MIN_MATCH", "0.80")
    assert _score_sync_allowed(SPORTS["basketball"], (81, 67), (79, 65), 0.94)
    assert not _score_sync_allowed(SPORTS["basketball"], (81, 67), (60, 40), 0.94)
    assert not _score_sync_allowed(SPORTS["basketball"], (81, 67), (79, 65), 0.70)


def test_basketball_strong_identity_allows_fast_multi_possession_score_lag(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_SCORE_DRIFT_MIN_MATCH", "0.80")
    monkeypatch.setenv("GOOL_BASKETBALL_SCORE_DRIFT_STRONG_MATCH", "0.92")
    assert _score_sync_allowed(SPORTS["basketball"], (90, 82), (78, 75), 0.97)
    assert not _score_sync_allowed(SPORTS["basketball"], (90, 82), (78, 75), 0.88)


def test_basketball_cumulative_clock_is_converted_to_current_quarter():
    game = {"SC": {"TS": 2204}}
    # 36:44 elapsed in a 4x10 game => 6:44 elapsed in Q4.
    assert _segment_clock_seconds(
        game,
        SPORTS["basketball"],
        period="4th quarter",
        league="Italy: Serie A",
    ) == 404


def test_basketball_q2_boundary_clock_starts_from_zero():
    game = {"SC": {"TS": 600}}
    assert _segment_clock_seconds(
        game,
        SPORTS["basketball"],
        period="2nd quarter",
        league="Kosovo: Superliga",
    ) == 0


def test_v3_gameevents_adapter_builds_decoder_compatible_ae():
    payload = {
        "id": 123,
        "scores": {"scoreOpp1": 21, "scoreOpp2": 18, "currentPeriodName": "2nd quarter"},
        "eventGroups": [
            {"groupId": 17, "events": [[
                {"type": 9, "cf": 1.84, "parameter": 41.5},
                {"type": 10, "cf": 1.90, "parameter": 41.5},
            ]]},
            {"groupId": 15, "events": [[
                {"type": 11, "cf": 1.86, "parameter": 20.5},
                {"type": 12, "cf": 1.88, "parameter": 20.5},
            ]]},
            {"groupId": 62, "events": [[
                {"type": 13, "cf": 1.91, "parameter": 20.5},
                {"type": 14, "cf": 1.82, "parameter": 20.5},
            ]]},
        ],
    }
    game = _v3_to_legacy_market_game(payload, "123")
    assert game["_market_source"] == "main-live-feed-v3"
    assert game["SC"]["FS"] == {"S1": 21, "S2": 18}
    decoded = __import__("gool_bot2.xbet_multisport_markets", fromlist=["decode_core_markets"]).decode_core_markets(
        game, "basketball", scope="QUARTER_2"
    )
    assert decoded["match_total"][0]["line"] == 41.5
    assert decoded["home_total"][0]["line"] == 20.5
    assert decoded["away_total"][0]["line"] == 20.5


def test_v3_adapter_keeps_zero_parameter_off_moneyline():
    game = _v3_to_legacy_market_game({
        "id": 7,
        "eventGroups": [{"groupId": 102, "events": [[
            {"type": 501, "cf": 1.70, "parameter": 0},
            {"type": 502, "cf": 2.10, "parameter": 0},
        ]]}],
    }, "7")
    decoded = __import__("gool_bot2.xbet_multisport_markets", fromlist=["decode_core_markets"]).decode_core_markets(
        game, "basketball"
    )
    assert decoded["moneyline"]["home"] == 1.70
    assert decoded["moneyline"]["away"] == 2.10


def test_basketball_subgame_uses_v3_only_after_legacy_failure(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    calls = []
    def fake_http(url, timeout=8.0):
        calls.append(url)
        if "GetGameZip" in url:
            return None
        if "/main-live-feed/v3/gameEvents" in url:
            return {
                "id": 123,
                "scores": {"scoreOpp1": 14, "scoreOpp2": 12, "currentPeriodName": "1st quarter"},
                "eventGroups": [{"groupId": 17, "events": [[
                    {"type": 9, "cf": 1.84, "parameter": 39.5},
                    {"type": 10, "cf": 1.90, "parameter": 39.5},
                ]]}],
            }
        return None

    monkeypatch.setattr(steam, "_sport_http_json", fake_http)
    monkeypatch.setenv("GOOL_MULTISPORT_SUBGAME_ROOT_ATTEMPTS", "1")
    monkeypatch.setenv("GOOL_BASKETBALL_V3_SUBGAME_FALLBACK", "1")

    worker = MultiSportSteamWorker(tmp_path)
    game = worker._subgame_game("123", SPORTS["basketball"], prematch=False)
    assert game["_market_source"] == "main-live-feed-v3"
    assert any("GetGameZip" in url for url in calls)
    assert any("/main-live-feed/v3/gameEvents" in url for url in calls)
    decoded = __import__("gool_bot2.xbet_multisport_markets", fromlist=["decode_core_markets"]).decode_core_markets(
        game, "basketball", scope="QUARTER_1"
    )
    assert decoded["match_total"][0]["line"] == 39.5
