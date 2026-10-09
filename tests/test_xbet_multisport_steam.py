from __future__ import annotations

from gool_bot2.providers.flashscore import FlashscoreProvider

import urllib.parse

from gool_bot2.xbet_multisport_steam import (
    SPORTS,
    MultiSportSteamWorker,
    parse_flashscore_events,
    detect_live_segment_stats,
    select_prematch_primary,
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
    multisport_scope_is_complete,
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


def test_multisport_settlement_honours_displayed_total_when_legacy_direction_disagrees():
    # Exact Puerto Montt class of bug: the user saw OVER, but an old row saved
    # technical direction=under before the label/direction invariant existed.
    assert settle_multisport_pick({
        "market_family": "match_total",
        "selection": "ТБ 152.5",
        "direction": "under",
        "line": 152.5,
    }, 93, 80) == "won"
    assert settle_multisport_pick({
        "market_family": "home_total",
        "selection": "ИТБ1 76",
        "direction": "under",
        "line": 76,
    }, 93, 80) == "won"


def test_record_signal_rebuilds_total_label_from_brain_direction(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    row = {
        "phase": "PREMATCH",
        "origin": "multisport_prematch",
        "event_id": "xb-puerto",
        "flashscore_event_id": "FS-PUERTO",
        "home": "Puerto Montt",
        "away": "Ancud",
        "league": "Chile",
        "scope": "FULL_MATCH",
        "market_family": "home_total",
        "selection": "ИТБ1 76",
        "line": 76,
        "start_ts": 9999999999,
    }
    signal = {
        "direction": "under",
        "line": 76,
        "odd": 1.73,
        "fair_probability": .55,
        "metric_delta": 3.0,
        "probability_delta_pp": 4.0,
        "line_delta": -2.0,
        "moves": 3,
        "strength": 82.0,
        "start": {"line": 78, "under": 1.90},
    }

    recorded, _ = worker._record_signal(row, signal, SPORTS["basketball"])

    assert recorded is True
    saved = json.loads(worker.journal_path.read_text("utf-8"))[0]
    assert saved["direction"] == "under"
    assert saved["selection"] == "ИТМ1 76"


def test_settle_repairs_already_final_legacy_basketball_result(tmp_path, monkeypatch):
    import json
    from gool_bot2.multisport_journal import save_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    save_journal(worker.journal_path, [{
        "sport": "basketball",
        "phase": "PREMATCH",
        "origin": "multisport_prematch",
        "event_id": "xb-puerto",
        "flashscore_event_id": "FS-PUERTO",
        "home": "Puerto Montt",
        "away": "Ancud",
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "selection": "ТБ 152.5",
        "direction": "under",
        "line": 152.5,
        "odd": 1.73,
        "result": "lost",
        "profit_units": -1.0,
    }])

    changed = worker._settle(SPORTS["basketball"], {
        "FS-PUERTO": {
            "flashscore_event_id": "FS-PUERTO",
            "coarse_status": "3",
            "score": [93, 80],
        }
    })

    assert changed == 1
    saved = json.loads(worker.journal_path.read_text("utf-8"))[0]
    assert saved["direction"] == "over"
    assert saved["result"] == "won"
    assert saved["profit_units"] == 0.73
    assert saved["settled_score"] == [93, 80]
    assert saved["settlement_correction"] == "displayed_selection_direction_mismatch"


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

    def fake_prepare(cfg):
        calls.append(("brain", cfg.key))
        return {
            "fs_today": [],
            "fs_live": [{"flashscore_event_id": f"{cfg.key}-fs"}],
            "live_analysis": [{
                "flashscore_event_id": f"{cfg.key}-fs",
                "brain_state": "PASS",
                "brain_score": 80,
            }],
            "live_candidates": [{"flashscore_event_id": f"{cfg.key}-fs"}],
            "prematch_candidates": [],
        }

    def fake_index(cfg):
        calls.append(("index", cfg.key))
        return [{"I": f"{cfg.key}-1", "O1": "A", "O2": "B"}]

    def fake_pre(cfg):
        calls.append(("pre", cfg.key))
        return []

    def fake_scan(cfg, *, prepared=None, xbet_live_prefetched=None, xbet_prematch_prefetched=None):
        calls.append(("scan", cfg.key, len(xbet_live_prefetched or [])))
        return {
            "flashscore_live": len((prepared or {}).get("fs_live") or []),
            "live_brain_candidates": len((prepared or {}).get("live_candidates") or []),
            "xbet_live": len(xbet_live_prefetched or []), "mapped": 0,
            "decoded": 0, "score_mismatch": 0, "market_decode_failed": 0,
            "detected": 0, "prematch_detected": 0, "flashscore_prematch": 0,
            "prematch_brain_candidates": 0, "prematch_decoded": 0,
            "delivered": 0, "prematch_delivered": 0, "settled": 0,
            "xbet_diag": {"ok": True},
        }

    monkeypatch.setattr(worker, "_prepare_flashscore_sport", fake_prepare)
    monkeypatch.setattr(worker, "_xbet_index", fake_index)
    monkeypatch.setattr(worker, "_xbet_prematch_index", fake_pre)
    monkeypatch.setattr(worker, "_scan_sport", fake_scan)
    worker.collect_once()

    first_index = next(i for i, row in enumerate(calls) if row[0] == "index")
    assert ("brain", "hockey") in calls[:first_index]
    assert ("brain", "basketball") in calls[:first_index]
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


def test_basketball_strong_identity_allows_only_small_live_score_lag(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_SCORE_DRIFT_MIN_MATCH", "0.80")
    monkeypatch.setenv("GOOL_BASKETBALL_SCORE_DRIFT_STRONG_MATCH", "0.92")
    assert _score_sync_allowed(SPORTS["basketball"], (90, 82), (85, 79), 0.97)
    # 12+7 points of provider drift is far too stale for LIVE total pricing.
    assert not _score_sync_allowed(SPORTS["basketball"], (90, 82), (78, 75), 0.97)
    assert not _score_sync_allowed(SPORTS["basketball"], (90, 82), (85, 79), 0.88)


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

    legacy_calls = []
    v3_calls = []

    def fake_http(url, timeout=8.0):
        legacy_calls.append(url)
        return None

    def fake_v3(host, path, ordered_query, timeout=8.0):
        v3_calls.append((host, path, ordered_query))
        if path.endswith("/gameEvents"):
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
    monkeypatch.setattr(steam, "_sport_v3_json", fake_v3)
    monkeypatch.setenv("GOOL_MULTISPORT_SUBGAME_ROOT_ATTEMPTS", "1")
    monkeypatch.setenv("GOOL_BASKETBALL_V3_SUBGAME_FALLBACK", "1")

    worker = MultiSportSteamWorker(tmp_path)
    game = worker._subgame_game("123", SPORTS["basketball"], prematch=False)
    assert game["_market_source"] == "main-live-feed-v3"
    assert any("GetGameZip" in url for url in legacy_calls)
    assert any(path.endswith("/gameEvents") for _, path, _ in v3_calls)
    decoded = __import__("gool_bot2.xbet_multisport_markets", fromlist=["decode_core_markets"]).decode_core_markets(
        game, "basketball", scope="QUARTER_1"
    )
    assert decoded["match_total"][0]["line"] == 39.5


def test_v3_adapter_carries_timer_period_scores_and_subgames():
    game = _v3_to_legacy_market_game({
        "id": 123,
        "scores": {
            "scoreOpp1": 44,
            "scoreOpp2": 41,
            "currentPeriodName": "3rd quarter",
            "timer": {"timeSec": 1325},
            "periodScores": [
                {"period": 1, "scoreOpp1": 18, "scoreOpp2": 20, "periodNameFull": "1st quarter"},
                {"period": 2, "scoreOpp1": 21, "scoreOpp2": 18, "periodNameFull": "2nd quarter"},
                {"period": 3, "scoreOpp1": 5, "scoreOpp2": 3, "periodNameFull": "3rd quarter"},
            ],
        },
        "subGamesForMainGame": [
            {"id": 901, "period": 3, "subGameName": "3rd quarter", "eventGroups": []},
        ],
        "eventGroups": [],
    }, "123")
    assert game["SC"]["TS"] == 1325
    assert game["SC"]["CPS"] == "3rd quarter"
    assert game["SC"]["PS"][2]["Value"]["NF"] == "3rd quarter"
    assert game["SG"][0]["I"] == "901"
    assert game["SG"][0]["PN"] == "3rd quarter"


def test_xbet_index_uses_v3_games1x2_when_legacy_index_is_empty(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(steam, "_sport_http_json", lambda *args, **kwargs: None)

    calls = []
    def fake_v3(host, path, ordered_query, timeout=8.0):
        calls.append((host, path, ordered_query))
        if path.endswith("/games1x2"):
            return [
                {
                    "id": 777,
                    "sport": {"id": 3, "name": "Basketball"},
                    "liga": {"id": 55, "name": "Test League"},
                    "opponent1": {"fullName": "Alpha"},
                    "opponent2": {"fullName": "Beta"},
                    "scores": {"scoreOpp1": 30, "scoreOpp2": 28},
                },
                {
                    "id": 778,
                    "sport": {"id": 2, "name": "Ice Hockey"},
                    "opponent1": {"fullName": "Wrong Sport"},
                    "opponent2": {"fullName": "Ignored"},
                },
            ]
        return None

    monkeypatch.setattr(steam, "_sport_v3_json", fake_v3)
    rows = worker._xbet_index(SPORTS["basketball"])
    assert len(rows) == 1
    assert rows[0]["I"] == "777"
    assert rows[0]["O1"] == "Alpha"
    assert rows[0]["O2"] == "Beta"
    assert rows[0]["_v3_index"] is True
    assert worker._index_diag["basketball"]["source"] == "v3_games1x2"
    assert any(path.endswith("/games1x2") for _, path, _ in calls)


def test_parent_game_uses_v3_gameevents_when_legacy_getgamezip_fails(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(steam, "_sport_http_json", lambda *args, **kwargs: None)

    def fake_v3(host, path, ordered_query, timeout=8.0):
        if path.endswith("/gameEvents"):
            return {
                "id": 777,
                "scores": {
                    "scoreOpp1": 30,
                    "scoreOpp2": 28,
                    "currentPeriodName": "2nd quarter",
                    "timer": {"timeSec": 745},
                },
                "eventGroups": [
                    {"groupId": 17, "events": [[
                        {"type": 9, "parameter": 81.5, "cf": 1.84},
                        {"type": 10, "parameter": 81.5, "cf": 1.90},
                    ]]},
                ],
                "subGamesForMainGame": [
                    {"id": 990, "period": 2, "subGameName": "2nd quarter", "eventGroups": []},
                ],
            }
        return None

    monkeypatch.setattr(steam, "_sport_v3_json", fake_v3)
    game = worker._game("777", SPORTS["basketball"])
    assert game is not None
    assert game["_market_source"] == "main-live-feed-v3"
    assert game["SC"]["FS"] == {"S1": 30, "S2": 28}
    assert game["SG"][0]["PN"] == "2nd quarter"
    decoded = __import__("gool_bot2.xbet_multisport_markets", fromlist=["decode_core_markets"]).decode_core_markets(
        game, "basketball"
    )
    assert decoded["match_total"][0]["line"] == 81.5


def test_v3_embedded_subgame_market_decodes_without_extra_fetch(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    game = _v3_to_legacy_market_game({
        "id": 123,
        "scores": {
            "scoreOpp1": 44,
            "scoreOpp2": 41,
            "currentPeriodName": "3rd quarter",
        },
        "eventGroups": [],
        "subGamesForMainGame": [{
            "id": 901,
            "period": 3,
            "subGameName": "3rd quarter",
            "eventGroups": [{
                "groupId": 17,
                "events": [[
                    {"type": 9, "cf": 1.84, "parameter": 39.5},
                    {"type": 10, "cf": 1.90, "parameter": 39.5},
                ]],
            }],
        }],
    }, "123")
    monkeypatch.setattr(worker, "_cached_subgame", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network fetch should not happen")))
    decoded, meta = worker._market_tree(
        game,
        SPORTS["basketball"],
        prematch=False,
        wanted_scopes={"QUARTER_3"},
    )
    assert decoded["QUARTER_3"]["match_total"][0]["line"] == 39.5
    assert meta["subgame_fetch"]["QUARTER_3"] == "embedded_v3"


def test_prematch_index_uses_current_get1x2_zip_when_legacy_is_empty(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(steam, "_sport_http_json", lambda *args, **kwargs: None)

    def fake_v3(host, path, ordered_query, timeout=8.0):
        if path.endswith("/Get1x2_Zip"):
            return {
                "Value": [{
                    "I": 321,
                    "O1E": "Alpha",
                    "O2E": "Beta",
                    "SI": 3,
                    "L": "Test League",
                }]
            }
        return None

    monkeypatch.setattr(steam, "_sport_v3_json", fake_v3)
    rows = worker._xbet_prematch_index(SPORTS["basketball"])
    assert len(rows) == 1
    assert rows[0]["I"] == 321
    assert rows[0]["O1"] == "Alpha"
    assert rows[0]["O2"] == "Beta"
    assert rows[0]["_current_linefeed"] is True
    assert worker._prematch_index_diag["basketball"]["source"] == "current_linefeed_get1x2_zip"


def test_prematch_game_uses_current_getgamezip_when_legacy_is_empty(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(steam, "_sport_http_json", lambda *args, **kwargs: None)

    def fake_v3(host, path, ordered_query, timeout=8.0):
        if path.endswith("/GetGameZip"):
            return {
                "Value": {
                    "I": 321,
                    "O1": "Alpha",
                    "O2": "Beta",
                    "AE": [{
                        "G": 17,
                        "ME": [
                            {"T": 9, "P": 165.5, "C": 1.86},
                            {"T": 10, "P": 165.5, "C": 1.88},
                        ],
                    }],
                }
            }
        return None

    monkeypatch.setattr(steam, "_sport_v3_json", fake_v3)
    game = worker._prematch_game("321", SPORTS["basketball"])
    assert game is not None
    decoded = __import__("gool_bot2.xbet_multisport_markets", fromlist=["decode_core_markets"]).decode_core_markets(
        game, "basketball"
    )
    assert decoded["match_total"][0]["line"] == 165.5


def test_hockey_basketball_live_index_profile_uses_gr70_country71_first(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    for key in ("hockey", "basketball"):
        query = urllib.parse.parse_qs(worker._xbet_queries(SPORTS[key])[0])
        assert query["sports"] == [str(SPORTS[key].sport_id)]
        assert query["country"] == ["71"]
        assert query["gr"] == ["70"]
        assert query["mode"] == ["4"]


def test_hockey_basketball_prematch_profile_uses_gr70_country71_first(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    for key in ("hockey", "basketball"):
        query = urllib.parse.parse_qs(worker._xbet_prematch_queries(SPORTS[key])[0])
        assert query["sports"] == [str(SPORTS[key].sport_id)]
        assert query["country"] == ["71"]
        assert query["gr"] == ["70"]
        assert query["tf"] == ["2200000"]
        assert query["tz"] == ["5"]


def test_multisport_getgamezip_uses_team_sport_profile(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    worker = MultiSportSteamWorker(tmp_path)
    urls = []
    def fake_http(url, timeout=8.0):
        urls.append(url)
        return {"Value": {"I": 123, "O1": "A", "O2": "B", "AE": [{"G": 4, "ME": []}]}}

    monkeypatch.setattr(steam, "_sport_http_json", fake_http)
    game = worker._game("123", SPORTS["basketball"])
    assert game is not None
    parsed = urllib.parse.urlsplit(urls[0])
    query = urllib.parse.parse_qs(parsed.query)
    assert query["country"] == ["71"]
    assert query["fcountry"] == ["71"]
    assert query["gr"] == ["70"]
    assert query["marketType"] == ["1"]
    assert query["isNewBuilder"] == ["true"]
    assert query["countevents"] == ["500"]


def test_team_sport_exact_live_index_profile_is_first(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    worker = MultiSportSteamWorker(tmp_path)
    exact_calls = []
    generic_calls = []

    def fake_exact(url, timeout=8.0):
        exact_calls.append(url)
        return {
            "Value": [{
                "I": 4242,
                "O1": "Alpha",
                "O2": "Beta",
                "L": "League",
            }]
        }

    monkeypatch.setattr(steam, "_team_sport_exact_json", fake_exact)
    monkeypatch.setattr(steam, "_sport_http_json", lambda *args, **kwargs: generic_calls.append(args[0]) or None)

    rows = worker._xbet_index(SPORTS["basketball"])
    assert len(rows) == 1
    assert rows[0]["I"] == 4242
    assert worker._index_diag["basketball"]["source"] == "github_team_sport_exact"
    assert generic_calls == []
    assert exact_calls
    url = exact_calls[0]
    assert "sports=3" in url
    assert "count=50" in url
    assert "gr=70" in url
    assert "country=71" in url
    assert "/LiveFeed/Get1x2_VZip?" in url


def test_team_sport_exact_game_profile_before_generic(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    worker = MultiSportSteamWorker(tmp_path)
    exact_calls = []
    generic_calls = []

    def fake_exact(url, timeout=8.0):
        exact_calls.append(url)
        return {"Value": {"I": 4242, "O1": "Alpha", "O2": "Beta", "GE": [{"G": 4, "E": []}]}}

    monkeypatch.setattr(steam, "_team_sport_exact_json", fake_exact)
    monkeypatch.setattr(steam, "_sport_http_json", lambda *args, **kwargs: generic_calls.append(args[0]) or None)

    game = worker._game("4242", SPORTS["hockey"])
    assert game is not None
    assert game["I"] == 4242
    assert generic_calls == []
    url = exact_calls[0]
    assert "/LiveFeed/GetGameZip?" in url
    assert "country=71" in url
    assert "fcountry=71" in url
    assert "gr=70" in url
    assert "countevents=500" in url


def test_select_prematch_primary_does_not_force_family_diversity_over_confidence(monkeypatch):
    candidates = [
        ({"market_family": "handicap"}, {"strength": 94, "model_probability": .64, "fair_probability": .64, "data_quality": .80}),
        ({"market_family": "match_total"}, {"strength": 90, "model_probability": .61, "fair_probability": .61, "data_quality": .90}),
    ]
    row, signal = select_prematch_primary(candidates, ["handicap", "handicap", "handicap"])
    assert row["market_family"] == "handicap"
    assert signal["model_probability"] == .64


def test_select_prematch_primary_prefers_probability_over_strength():
    candidates = [
        ({"market_family": "handicap"}, {"strength": 98, "model_probability": .62, "fair_probability": .62, "data_quality": .80}),
        ({"market_family": "match_total"}, {"strength": 84, "model_probability": .72, "fair_probability": .72, "data_quality": .80}),
    ]
    row, signal = select_prematch_primary(candidates, [])
    assert row["market_family"] == "match_total"
    assert signal["model_probability"] == .72


def test_select_prematch_primary_uses_quality_as_probability_tiebreak():
    candidates = [
        ({"market_family": "handicap"}, {"strength": 94, "model_probability": .66, "fair_probability": .66, "data_quality": .72}),
        ({"market_family": "match_total"}, {"strength": 90, "model_probability": .66, "fair_probability": .66, "data_quality": .91}),
    ]
    row, signal = select_prematch_primary(candidates, ["match_total", "handicap"])
    assert row["market_family"] == "match_total"
    assert signal["data_quality"] == .91


def test_live_snapshot_uses_flashscore_stats_not_xbet_stat_subgames(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    game = {
        "I": "101",
        "O1": "Boston Bruins",
        "O2": "New York Rangers",
        "SC": {"FS": {"S1": 1, "S2": 0}, "CPS": "2nd period", "TS": 360},
        "GE": [{"G": 4, "E": [[
            {"T": 9, "P": 2.5, "C": 1.85},
            {"T": 10, "P": 2.5, "C": 1.95},
        ]]}],
        "SG": [{
            "I": "sg2", "PN": "2nd period",
            "AE": [{"G": 4, "ME": [
                {"T": 9, "P": 1.5, "C": 1.82},
                {"T": 10, "P": 1.5, "C": 1.98},
            ]}],
        }],
    }
    fs = {
        "flashscore_event_id": "Ab12Cd34",
        "home": "Boston Bruins",
        "away": "New York Rangers",
        "score": [1, 0],
        "league": "NHL",
    }
    monkeypatch.setattr(worker, "_game", lambda *args, **kwargs: game)
    monkeypatch.setattr(
        worker._flashscore,
        "fetch_stats_detailed",
        lambda event_id: {
            "sections": {
                "PERIOD_2": {
                    "stats": {
                        "shots_on_goal": {"home": 14.0, "away": 11.0, "home_attempts": None, "away_attempts": None},
                        "shots": {"home": 20.0, "away": 18.0, "home_attempts": None, "away_attempts": None},
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        worker,
        "_hockey_segment_stats",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("1xBet stat subgames must not be used")),
    )
    row, error = worker._snapshot({"I": "101"}, fs, False, 0.99, SPORTS["hockey"])
    assert error is None
    assert row is not None
    assert row["live_game_stats"]["source"] == "flashscore"
    assert row["live_game_stats"]["shots_on_goal"] == [14, 11]


def test_prematch_snapshot_loads_flashscore_form_and_h2h(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    now = __import__("time").time()
    game = {
        "I": "201",
        "O1": "Denver Nuggets",
        "O2": "Utah Jazz",
        "GE": [{"G": 4, "E": [[
            {"T": 9, "P": 220.5, "C": 1.88},
            {"T": 10, "P": 220.5, "C": 1.92},
        ]]}],
    }
    fs = {
        "flashscore_event_id": "Cd34Ef56",
        "home": "Denver Nuggets",
        "away": "Utah Jazz",
        "score": [0, 0],
        "league": "NBA",
        "start_ts": now + 3600,
    }
    monkeypatch.setattr(worker, "_prematch_game", lambda *args, **kwargs: game)
    monkeypatch.setattr(worker, "_market_tree", lambda *args, **kwargs: ({
        "FULL_MATCH": {
            "scope": "FULL_MATCH",
            "match_total": [{"line": 220.5, "over": 1.88, "under": 1.92}],
            "home_total": [], "away_total": [], "handicap": [], "moneyline": {}, "raw": [{"G": 4, "T": 9}],
        }
    }, {"coverage": {}, "unknown_market_catalog": [], "subgame_fetch": {}}))
    context = {
        "source": "flashscore_h2h",
        "home_recent": [{"event_id": "h1", "home": "Denver Nuggets", "away": "A", "home_score": 120, "away_score": 110}],
        "away_recent": [{"event_id": "a1", "home": "B", "away": "Utah Jazz", "home_score": 105, "away_score": 112}],
        "h2h": [{"event_id": "x1", "home": "Denver Nuggets", "away": "Utah Jazz", "home_score": 118, "away_score": 115}],
    }
    monkeypatch.setattr(worker._flashscore, "fetch_match_history", lambda *args, **kwargs: context)
    row, error = worker._prematch_snapshot({"I": "201"}, fs, False, 0.98, SPORTS["basketball"])
    assert error is None
    assert row is not None
    assert row["prematch_context"]["source"] == "flashscore_h2h"
    assert len(row["prematch_context"]["h2h"]) == 1


def test_prematch_history_support_can_raise_total_signal_strength(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    ctx = {
        "home_recent": [
            {"event_id": "1", "home_score": 120, "away_score": 115},
            {"event_id": "2", "home_score": 118, "away_score": 112},
        ],
        "away_recent": [
            {"event_id": "3", "home_score": 116, "away_score": 114},
        ],
        "h2h": [],
    }
    lane = {"market_family": "match_total", "line": 220.5}
    signal = {"direction": "over", "line": 220.5}
    support = worker._prematch_history_support(ctx, lane, signal)
    assert support > 0
    assert support <= 3.0


def test_sport_context_features_calculates_rest_back_to_back_and_totals(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    start = 2_000_000_000
    ctx = {
        "home_recent": [
            {"event_id":"h1","home":"Home","away":"X","home_score":118,"away_score":112,"timestamp":start-20*3600},
            {"event_id":"h2","home":"Y","away":"Home","home_score":110,"away_score":116,"timestamp":start-3*86400},
        ],
        "away_recent": [
            {"event_id":"a1","home":"Away","away":"Z","home_score":108,"away_score":104,"timestamp":start-2*86400},
            {"event_id":"a2","home":"Q","away":"Away","home_score":106,"away_score":111,"timestamp":start-5*86400},
        ],
        "home_at_home": [],
        "away_away": [],
        "h2h": [{"event_id":"x1","home":"Home","away":"Away","home_score":120,"away_score":117,"timestamp":start-10*86400}],
    }
    fs = {"home":"Home","away":"Away","start_ts":start}
    feat = worker._sport_context_features(ctx, fs, SPORTS["basketball"])
    assert feat["home_back_to_back"] is True
    assert feat["away_back_to_back"] is False
    assert feat["home_rest_days"] < feat["away_rest_days"]
    assert feat["recent_total_avg"] > 200
    assert feat["h2h_total_avg"] == 237


def test_prematch_history_support_prefers_over_when_recent_totals_are_high(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    ctx = {
        "home_recent": [
            {"event_id":"1","home_score":120,"away_score":115},
            {"event_id":"2","home_score":118,"away_score":114},
        ],
        "away_recent": [
            {"event_id":"3","home_score":117,"away_score":116},
        ],
        "h2h": [{"event_id":"4","home_score":121,"away_score":119}],
    }
    lane = {"market_family":"match_total","line":220.5}
    over = worker._prematch_history_support(ctx, lane, {"direction":"over","line":220.5})
    under = worker._prematch_history_support(ctx, lane, {"direction":"under","line":220.5})
    assert over > 0
    assert under < 0


def test_hockey_live_brain_falls_back_to_score_clock_without_flashscore_stats(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_ELAPSED_SECONDS", "30")
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_CLOCK_DELTA_SECONDS", "20")
    monkeypatch.setenv("GOOL_HOCKEY_LIVE_SEGMENT_MIN_STAT_EDGE", "0.2")
    rows = [
        {"ts":100.0,"clock_seconds":300.0,"score":[0,0],"line":2.5,"over":1.95,"under":1.80,"probability":0.48,"scope":"PERIOD_2","market_family":"match_total","league":"KHL","live_game_stats":{}},
        {"ts":160.0,"clock_seconds":360.0,"score":[0,0],"line":2.5,"over":1.95,"under":1.80,"probability":0.48,"scope":"PERIOD_2","market_family":"match_total","league":"KHL","live_game_stats":{}},
        {"ts":220.0,"clock_seconds":420.0,"score":[0,0],"line":2.5,"over":1.95,"under":1.80,"probability":0.48,"scope":"PERIOD_2","market_family":"match_total","league":"KHL","live_game_stats":{}},
        {"ts":280.0,"clock_seconds":480.0,"score":[0,0],"line":2.5,"over":1.95,"under":1.80,"probability":0.48,"scope":"PERIOD_2","market_family":"match_total","league":"KHL","live_game_stats":{}},
    ]
    signal = detect_live_segment_stats(rows, SPORTS["hockey"], now=280, score_changed_at=None)
    assert signal is not None
    assert signal["brain_mode"] == "segment_stats"
    assert signal["hockey_pressure"] == {}


def test_flashscore_detailed_stats_parses_periods_quarters_and_attempts():
    body = (
        "HA÷1st period~"
        "SF÷Match statistics~"
        "SD÷901¬SG÷Shots on goal¬SH÷12¬SI÷9~"
        "SD÷902¬SG÷Powerplay goals¬SH÷1¬SI÷0~"
        "HA÷2nd quarter~"
        "SF÷Shooting~"
        "SD÷903¬SG÷Field goals¬SH÷10/24¬SI÷9/22~"
        "SD÷904¬SG÷3-point field goals¬SH÷4/11¬SI÷3/10~"
        "SF÷Other~"
        "SD÷905¬SG÷Turnovers¬SH÷5¬SI÷7~"
    )
    parsed = FlashscoreProvider.parse_stats_detailed(body)
    assert "PERIOD_1" in parsed["sections"]
    assert "QUARTER_2" in parsed["sections"]
    hockey = parsed["sections"]["PERIOD_1"]["stats"]
    assert hockey["shots_on_goal"]["home"] == 12.0
    assert hockey["powerplay_goals"]["away"] == 0.0
    basket = parsed["sections"]["QUARTER_2"]["stats"]
    assert basket["field_goals"]["home_made"] == 10.0
    assert basket["field_goals"]["home_attempts"] == 24.0
    assert basket["three_point_field_goals"]["away_attempts"] == 10.0
    assert basket["turnovers"]["away"] == 7.0


def test_flashscore_event_parser_keeps_segment_score_parts():
    body = (
        "ZA÷NBA~"
        "AA÷Ab12Cd34¬AB÷2¬AC÷6¬AE÷Home¬AF÷Away¬AG÷56¬AH÷51"
        "¬BA÷28¬BB÷25¬BC÷28¬BD÷26~"
    )
    rows = parse_flashscore_events(body)
    assert len(rows) == 1
    assert rows[0]["score_parts"] == [[28, 25], [28, 26]]


def test_prematch_one_match_one_pick_across_market_families(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    cfg = SPORTS["basketball"]
    base = {
        "event_id": "xbet-1",
        "flashscore_event_id": "FS123456",
        "home": "Puerto Montt",
        "away": "Ancud",
        "league": "Chile: LNB",
        "phase": "PREMATCH",
        "start_ts": 2_000_000_000,
    }
    first_row = {**base, "scope": "FULL_MATCH", "market_family": "home_total", "selection": "ИТБ1 76"}
    first_signal = {
        "direction": "over", "line": 76.0, "odd": 1.73, "selection": "ИТБ1 76",
        "strength": 100.0, "metric_delta": 1.0, "probability_delta_pp": 3.0,
        "line_delta": 2.0, "moves": 3, "fair_probability": .56, "extreme": False,
    }
    second_row = {**base, "event_id": "xbet-rotated-2", "scope": "FULL_MATCH", "market_family": "match_total", "selection": "ТБ 152.5"}
    second_signal = {**first_signal, "line": 152.5, "selection": "ТБ 152.5"}
    third_row = {**base, "event_id": "xbet-rotated-3", "scope": "FULL_MATCH", "market_family": "away_total", "selection": "ИТБ2 76"}
    third_signal = {**first_signal, "line": 76.0, "selection": "ИТБ2 76"}

    assert worker._record_signal(first_row, first_signal, cfg)[0] is True
    assert worker._record_signal(second_row, second_signal, cfg)[0] is False
    assert worker._record_signal(third_row, third_signal, cfg)[0] is False

    rows = __import__("gool_bot2.multisport_journal", fromlist=["load_journal"]).load_journal(worker.journal_path)
    prematch = [row for row in rows if row.get("phase") == "PREMATCH" and row.get("flashscore_event_id") == "FS123456"]
    assert len(prematch) == 1
    assert prematch[0]["selection"] == "ИТБ1 76"


def test_hockey_primary_no_longer_uses_family_bias():
    candidates = [
        ({"market_family": "handicap"}, {"strength": 100, "model_probability": .61, "fair_probability": .61, "data_quality": .80}),
        ({"market_family": "match_total"}, {"strength": 95, "model_probability": .57, "fair_probability": .57, "data_quality": .90}),
    ]
    row, signal = select_prematch_primary(candidates, [], "hockey")
    assert row["market_family"] == "handicap"
    assert signal["model_probability"] == .61


def test_hockey_primary_still_uses_strength_after_probability_and_quality():
    candidates = [
        ({"market_family": "handicap"}, {"strength": 100, "model_probability": .61, "fair_probability": .61, "data_quality": .80}),
        ({"market_family": "match_total"}, {"strength": 84, "model_probability": .61, "fair_probability": .61, "data_quality": .80}),
    ]
    row, signal = select_prematch_primary(candidates, [], "hockey")
    assert row["market_family"] == "handicap"
    assert signal["strength"] == 100


def test_hockey_settlement_sends_result_card_once(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam
    from gool_bot2.multisport_journal import save_journal, load_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    monkeypatch.setenv("XBET_MULTISPORT_CARDS_ENABLED", "1")
    worker = MultiSportSteamWorker(tmp_path)
    save_journal(worker.journal_path, [{
        "entry_id": "hockey:prematch:FSHOCKEY",
        "journal_version": 2,
        "sport": "hockey",
        "phase": "PREMATCH",
        "origin": "multisport_prematch",
        "event_id": "xbet-h1",
        "flashscore_event_id": "FSHOCKEY",
        "home": "SKA",
        "away": "CSKA",
        "league": "KHL",
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "selection": "ТБ 5.5",
        "direction": "over",
        "line": 5.5,
        "odd": 1.80,
        "result": "pending",
        "profit_units": 0.0,
    }])

    sent = []
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": sent.append((png, caption)) or 1)
    states = {"FSHOCKEY": {"coarse_status": "3", "score": [4, 2]}}

    assert worker._settle(SPORTS["hockey"], states) == 1
    assert len(sent) == 1
    assert sent[0][0].startswith(b"\x89PNG\r\n\x1a\n")
    assert sent[0][1] == ""

    row = load_journal(worker.journal_path)[0]
    assert row["result"] == "won"
    assert row["result_card_sent"] is True
    assert row.get("result_card_sent_at")

    assert worker._settle(SPORTS["hockey"], states) == 0
    assert len(sent) == 1

def test_live_hockey_period_settlement_uses_entire_final_period_not_goals_after_signal(tmp_path, monkeypatch):
    import json
    from gool_bot2.multisport_journal import save_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    save_journal(worker.journal_path, [{
        "sport": "hockey",
        "phase": "LIVE",
        "event_id": "HEND-1",
        "flashscore_event_id": "FS-HEND",
        "home": "Henderson Silver Knights",
        "away": "Bakersfield Condors",
        "scope": "PERIOD_3",
        "market_family": "match_total",
        "selection": "3-й период: ТМ 1.5",
        "direction": "under",
        "line": 1.5,
        "odd": 1.77,
        # One P3 goal already existed when the signal was sent.
        "score": [1, 0],
        "result": "pending",
        "profit_units": 0.0,
    }])

    changed = worker._settle(SPORTS["hockey"], {
        "FS-HEND": {
            "flashscore_event_id": "FS-HEND",
            "coarse_status": "3",
            "score": [1, 3],
            # P3 finished 0:2, so total P3 = 2 and U1.5 loses.
            "score_parts": [[0, 1], [1, 0], [0, 2]],
        }
    })

    assert changed == 1
    saved = json.loads(worker.journal_path.read_text("utf-8"))[0]
    assert saved["settled_score"] == [0, 2]
    assert saved["result"] == "lost"
    assert saved["profit_units"] == -1.0


def test_basketball_full_match_total_settlement_includes_overtime(tmp_path, monkeypatch):
    import json
    from gool_bot2.multisport_journal import save_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    save_journal(worker.journal_path, [{
        "sport": "basketball",
        "phase": "PREMATCH",
        "event_id": "B-OT-1",
        "flashscore_event_id": "FS-BOT",
        "home": "Home",
        "away": "Away",
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "selection": "ТБ 195.5",
        "direction": "over",
        "line": 195.5,
        "odd": 1.90,
        "result": "pending",
    }])

    worker._settle(SPORTS["basketball"], {
        "FS-BOT": {
            "flashscore_event_id": "FS-BOT",
            "coarse_status": "3",
            # Regulation was 88:88; OT made it 101:98.
            "score": [101, 98],
            "score_parts": [[25,22],[20,25],[23,22],[20,19],[13,10]],
        }
    })

    saved = json.loads(worker.journal_path.read_text("utf-8"))[0]
    assert saved["settled_match_score"] == [101, 98]
    assert saved["settled_score"] == [101, 98]
    assert saved["result"] == "won"


def test_hockey_two_way_full_match_moneyline_uses_final_score_after_overtime(tmp_path, monkeypatch):
    import json
    from gool_bot2.multisport_journal import save_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    save_journal(worker.journal_path, [{
        "sport": "hockey",
        "phase": "PREMATCH",
        "event_id": "H-OT-1",
        "flashscore_event_id": "FS-HOT",
        "home": "Home",
        "away": "Away",
        "scope": "FULL_MATCH",
        "market_family": "moneyline",
        "selection": "П1",
        "selection_side": "home",
        "moneyline_kind": "moneyline_2way",
        "line": 0,
        "odd": 1.85,
        "result": "pending",
    }])

    worker._settle(SPORTS["hockey"], {
        "FS-HOT": {
            "flashscore_event_id": "FS-HOT",
            "coarse_status": "3",
            # 2:2 after regulation, home wins in OT.
            "score": [3, 2],
            "score_parts": [[1,1],[1,0],[0,1],[1,0]],
        }
    })

    saved = json.loads(worker.journal_path.read_text("utf-8"))[0]
    assert saved["settled_score"] == [3, 2]
    assert saved["result"] == "won"

def test_hockey_first_period_bet_settles_when_second_period_has_started(tmp_path, monkeypatch):
    import json
    from gool_bot2.multisport_journal import save_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    save_journal(worker.journal_path, [{
        "entry_id": "hockey:live:H1:PERIOD_1:match_total",
        "sport": "hockey",
        "phase": "LIVE",
        "event_id": "H1",
        "flashscore_event_id": "FSH1",
        "home": "Belye Medvedi",
        "away": "Omskie Yastreby",
        "scope": "PERIOD_1",
        "market_family": "match_total",
        "selection": "1-й период: ТБ 1.5",
        "direction": "over",
        "line": 1.5,
        "odd": 1.75,
        "result": "pending",
        "profit_units": 0.0,
    }])

    changed = worker._settle(SPORTS["hockey"], {
        "FSH1": {
            "flashscore_event_id": "FSH1",
            "coarse_status": "2",
            "status_code": "27",
            "league": "RUSSIA: MHL",
            "score": [1, 1],
            "score_parts": [[0, 1], [1, 0]],
        }
    })

    assert changed == 1
    saved = json.loads(worker.journal_path.read_text("utf-8"))[0]
    assert saved["settled_score"] == [0, 1]
    assert saved["result"] == "lost"
    assert saved["profit_units"] == -1.0

def test_scope_completion_prefers_brain_scope_over_raw_numeric_ac():
    fs = {
        "coarse_status": "2",
        "status_code": "15",
        "scope": "PERIOD_2",
        "period": "2-й период",
    }

    assert multisport_scope_is_complete(fs, "hockey", "PERIOD_1") is True
    assert multisport_scope_is_complete(fs, "hockey", "PERIOD_2") is False



def test_active_signal_delivery_failure_is_not_journaled_and_retries(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    worker = MultiSportSteamWorker(tmp_path)
    outcomes = iter([0, 1])
    monkeypatch.setattr(worker, "_deliver", lambda row, signal, cfg: next(outcomes))

    row = {
        "phase": "LIVE",
        "origin": "multisport_live",
        "event_id": "xb-retry-1",
        "flashscore_event_id": "FS-RETRY-1",
        "home": "Retry Home",
        "away": "Retry Away",
        "league": "Test",
        "scope": "QUARTER_2",
        "market_family": "match_total",
        "selection": "2-я четверть: ТМ 40.5",
        "line": 40.5,
        "score": [30, 28],
        "match_score": [30, 28],
        "period": "2-я четверть",
        "clock_seconds": 240,
    }
    signal = {
        "brain_mode": "basketball_live_v2",
        "direction": "under",
        "line": 40.5,
        "odd": 1.85,
        "fair_probability": 0.62,
        "model_probability": 0.62,
        "market_probability": 0.54,
        "edge": 0.08,
        "strength": 80.0,
    }

    first = worker._record_signal(row, signal, SPORTS["basketball"])
    assert first == (False, 0)
    assert not worker.journal_path.exists()

    second = worker._record_signal(row, signal, SPORTS["basketball"])
    assert second == (True, 1)
    saved = json.loads(worker.journal_path.read_text("utf-8"))
    assert len(saved) == 1
    assert saved[0]["telegram_sent"] is True


def test_multisport_live_card_failure_falls_back_to_text(monkeypatch, tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setenv("XBET_MULTISPORT_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("XBET_MULTISPORT_CARDS_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_TEXT_FALLBACK_ENABLED", "1")

    monkeypatch.setattr(
        "gool_bot2.xbet_multisport_steam.render_basketball_live_card",
        lambda row, signal, cfg: b"png",
    )
    monkeypatch.setattr(
        "gool_bot2.xbet_multisport_steam.telegram.broadcast_photo",
        lambda png, caption="": 0,
    )
    sent_text = []
    monkeypatch.setattr(
        "gool_bot2.xbet_multisport_steam.telegram.broadcast",
        lambda message: sent_text.append(message) or 1,
    )

    row = {
        "phase": "LIVE",
        "home": "Home",
        "away": "Away",
        "league": "Test",
        "score": [40, 38],
        "match_score": [40, 38],
        "period": "2-я четверть",
        "clock_seconds": 300,
        "scope": "QUARTER_2",
        "market_family": "match_total",
    }
    signal = {
        "direction": "over",
        "line": 42.5,
        "odd": 1.90,
        "strength": 80,
    }

    sent = worker._deliver(row, signal, SPORTS["basketball"])

    assert sent == 1
    assert len(sent_text) == 1
    assert "GOOL MULTI · LIVE · BASKETBALL" in sent_text[0]



def test_hockey_live_any_existing_pick_blocks_second_market_family(tmp_path):
    import json

    worker = MultiSportSteamWorker(tmp_path)
    worker.journal_path.parent.mkdir(parents=True, exist_ok=True)
    worker.journal_path.write_text(
        json.dumps([
            {
                "sport": "hockey",
                "phase": "LIVE",
                "event_id": "xh1",
                "flashscore_event_id": "fh1",
                "scope": "PERIOD_2",
                "market_family": "match_total",
            }
        ]),
        "utf-8",
    )

    assert worker._already_seen(
        "hockey", "xh1", "LIVE", "FULL_MATCH", "home_total", "fh1"
    ) is True
    assert worker._already_seen(
        "hockey", "xh1", "LIVE", "PERIOD_3", "match_total", "fh1"
    ) is True


def test_basketball_live_correlation_groups_keep_quarter_and_one_full_projection(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)

    full_match = worker._live_correlation_group("basketball", "FULL_MATCH", "match_total")
    full_home = worker._live_correlation_group("basketball", "FULL_MATCH", "home_total")
    full_away = worker._live_correlation_group("basketball", "FULL_MATCH", "away_total")
    q2 = worker._live_correlation_group("basketball", "QUARTER_2", "match_total")
    q3 = worker._live_correlation_group("basketball", "QUARTER_3", "match_total")

    assert full_match == full_home == full_away
    assert q2 != full_match
    assert q3 != q2


def test_basketball_live_full_projection_seen_blocks_second_full_family(tmp_path):
    import json

    worker = MultiSportSteamWorker(tmp_path)
    worker.journal_path.parent.mkdir(parents=True, exist_ok=True)
    worker.journal_path.write_text(
        json.dumps([
            {
                "sport": "basketball",
                "phase": "LIVE",
                "event_id": "xb1",
                "flashscore_event_id": "fs1",
                "scope": "FULL_MATCH",
                "market_family": "match_total",
            }
        ]),
        "utf-8",
    )

    assert worker._already_seen(
        "basketball",
        "xb1",
        "LIVE",
        "FULL_MATCH",
        "home_total",
        "fs1",
    ) is True
    assert worker._already_seen(
        "basketball",
        "xb1",
        "LIVE",
        "QUARTER_2",
        "match_total",
        "fs1",
    ) is True


def test_basketball_live_quarter_pick_count_caps_match_at_two_quarters(tmp_path):
    import json

    worker = MultiSportSteamWorker(tmp_path)
    worker.journal_path.parent.mkdir(parents=True, exist_ok=True)
    worker.journal_path.write_text(
        json.dumps([
            {
                "sport": "basketball",
                "phase": "LIVE",
                "event_id": "xb1",
                "flashscore_event_id": "fs1",
                "scope": "QUARTER_1",
                "market_family": "match_total",
            },
            {
                "sport": "basketball",
                "phase": "LIVE",
                "event_id": "xb1",
                "flashscore_event_id": "fs1",
                "scope": "QUARTER_3",
                "market_family": "match_total",
            },
            # Must not count: full-match/team-total legacy row.
            {
                "sport": "basketball",
                "phase": "LIVE",
                "event_id": "xb1",
                "flashscore_event_id": "fs1",
                "scope": "FULL_MATCH",
                "market_family": "away_total",
            },
        ]),
        "utf-8",
    )

    assert worker._basketball_live_quarter_pick_count("xb1", "fs1") == 2


def test_basketball_live_one_match_one_signal_even_across_quarters(tmp_path):
    import json

    worker = MultiSportSteamWorker(tmp_path)
    worker.journal_path.parent.mkdir(parents=True, exist_ok=True)
    worker.journal_path.write_text(
        json.dumps([
            {
                "sport": "basketball",
                "phase": "LIVE",
                "event_id": "xb1",
                "flashscore_event_id": "fs1",
                "scope": "QUARTER_1",
                "market_family": "match_total",
            }
        ]),
        "utf-8",
    )

    assert worker._already_seen(
        "basketball", "xb1", "LIVE", "QUARTER_1", "match_total", "fs1"
    ) is True
    assert worker._already_seen(
        "basketball", "xb1", "LIVE", "QUARTER_2", "match_total", "fs1"
    ) is True
    assert worker._basketball_live_quarter_pick_count("xb1", "fs1") == 1


def test_prematch_single_prefers_safer_lower_odd_when_model_probability_is_higher():
    candidates = [
        (
            {"market_family": "match_total", "line": 150.5, "over": 1.90},
            {"selection": "ТБ 150.5", "odd": 1.90, "model_probability": 0.62, "fair_probability": 0.62, "data_quality": 0.86, "strength": 90, "edge": 0.08},
        ),
        (
            {"market_family": "match_total", "line": 140.5, "over": 1.52},
            {"selection": "ТБ 140.5", "odd": 1.52, "model_probability": 0.78, "fair_probability": 0.78, "data_quality": 0.86, "strength": 88, "edge": 0.07},
        ),
    ]
    row, signal = select_prematch_primary(candidates, [])
    assert row["line"] == 140.5
    assert signal["odd"] == 1.52
    assert signal["model_probability"] == 0.78
