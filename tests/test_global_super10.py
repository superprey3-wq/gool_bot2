import time
from pathlib import Path

import gool_bot2.global_super10 as gs


def _candidate(sport: str, idx: int, now: float, *, odd: float = 1.50, p: float = 0.78):
    return {
        "sport": sport,
        "event_id": f"{sport}-{idx}",
        "home": f"{sport} home {idx}",
        "away": f"{sport} away {idx}",
        "league": f"{sport} league",
        "selection": "SAFE PICK",
        "market_family": "match_total",
        "scope": "FULL_MATCH",
        "odd": odd,
        "model_probability": p,
        "market_probability": 0.60,
        "edge": p - 0.60,
        "data_quality": 0.85,
        "strength": 84,
        "start_ts": now + 3600 + idx * 120,
        "parlay_safe": True,
    }


def _paths(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_POOL_PATH", str(tmp_path / "pool.json"))
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_SENT_PATH", str(tmp_path / "sent.json"))
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_ENABLED", "1")
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_REQUIRE_ALL_SPORTS", "1")


def test_global_super10_combines_all_three_sports_without_weak_padding(tmp_path: Path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    now = time.time()
    gs.publish_candidates("football", [_candidate("football", i, now) for i in range(1, 5)])
    gs.publish_candidates("hockey", [_candidate("hockey", i, now) for i in range(1, 4)])
    gs.publish_candidates("basketball", [_candidate("basketball", i, now) for i in range(1, 4)])

    ticket = gs.build_global_super10(now_ts=now)

    assert ticket is not None
    assert len(ticket["legs"]) == 10
    assert ticket["sport_counts"]["football"] == 4
    assert ticket["sport_counts"]["hockey"] == 3
    assert ticket["sport_counts"]["basketball"] == 3
    assert len({(x["sport"], x["event_id"]) for x in ticket["legs"]}) == 10
    assert all(x["model_probability"] >= 0.72 for x in ticket["legs"])


def test_global_super10_requires_a_qualified_leg_from_each_sport(tmp_path: Path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    now = time.time()
    gs.publish_candidates("football", [_candidate("football", i, now) for i in range(1, 7)])
    gs.publish_candidates("hockey", [_candidate("hockey", i, now) for i in range(1, 6)])
    gs.publish_candidates("basketball", [])

    assert gs.build_global_super10(now_ts=now) is None


def test_global_super10_drops_started_or_imminent_matches(tmp_path: Path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_MIN_LEAD_SECONDS", "300")
    now = time.time()
    football = [_candidate("football", i, now) for i in range(1, 5)]
    football.append({**_candidate("football", 99, now), "start_ts": now + 120})
    gs.publish_candidates("football", football)
    gs.publish_candidates("hockey", [_candidate("hockey", i, now) for i in range(1, 4)])
    gs.publish_candidates("basketball", [_candidate("basketball", i, now) for i in range(1, 4)])

    eligible = gs.eligible_candidates(now_ts=now)

    assert all(row["event_id"] != "football-99" for row in eligible)
    assert gs.build_global_super10(now_ts=now) is not None


def test_global_super10_sends_only_once_per_day(tmp_path: Path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    now = time.time()
    gs.publish_candidates("football", [_candidate("football", i, now) for i in range(1, 5)])
    gs.publish_candidates("hockey", [_candidate("hockey", i, now) for i in range(1, 4)])
    gs.publish_candidates("basketball", [_candidate("basketball", i, now) for i in range(1, 4)])

    sent = []
    monkeypatch.setattr(gs, "render_v4_parlay_card", lambda _row: b"PNG")
    monkeypatch.setattr(gs.telegram, "broadcast_photo", lambda png, caption="": sent.append((png, caption)) or 1)

    first = gs.maybe_deliver_global_super10(delivery_enabled=True)
    second = gs.maybe_deliver_global_super10(delivery_enabled=True)

    assert first["status"] == "sent"
    assert second["status"] == "already_sent"
    assert len(sent) == 1
    assert "ФУТБОЛ + ХОККЕЙ + БАСКЕТБОЛ" in sent[0][1]



def test_global_super10_uses_safe_reserve_to_complete_ten(tmp_path: Path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    now = time.time()

    # Six strict legs plus four safe-reserve legs. Old logic returned None;
    # adaptive SUPER10 should complete the 10 without arbitrary weak padding.
    gs.publish_candidates("football", [_candidate("football", i, now) for i in range(1, 5)])
    gs.publish_candidates("hockey", [
        _candidate("hockey", 1, now),
        _candidate("hockey", 2, now),
        {**_candidate("hockey", 3, now, odd=1.62, p=0.69), "data_quality": 0.58, "strength": 75},
    ])
    gs.publish_candidates("basketball", [
        {**_candidate("basketball", i, now, odd=1.62, p=0.69), "data_quality": 0.58, "strength": 75}
        for i in range(1, 4)
    ])

    ticket = gs.build_global_super10(now_ts=now)

    assert ticket is not None
    assert len(ticket["legs"]) == 10
    assert ticket["strict_legs"] == 6
    assert ticket["reserve_legs"] == 4
    assert all(leg["super_tier"] in {"strict", "reserve"} for leg in ticket["legs"])
    assert all(leg["model_probability"] >= 0.68 for leg in ticket["legs"])


def test_global_super10_source_snapshot_survives_old_six_hour_gap(tmp_path: Path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone

    _paths(tmp_path, monkeypatch)
    now = time.time()
    gs.publish_candidates("football", [_candidate("football", i, now) for i in range(1, 5)])
    gs.publish_candidates("hockey", [_candidate("hockey", i, now) for i in range(1, 4)])
    gs.publish_candidates("basketball", [_candidate("basketball", i, now) for i in range(1, 4)])

    pool = json.loads(gs.pool_path().read_text("utf-8"))
    old = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    for source in pool["sources"].values():
        source["updated_at"] = old
    gs.pool_path().write_text(json.dumps(pool), encoding="utf-8")

    # Candidate fixtures are still future. The source should not expire merely
    # because its publisher last ran more than six hours ago.
    assert gs.build_global_super10(now_ts=now) is not None


def test_global_super10_not_ready_reports_per_sport_counts(tmp_path: Path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    now = time.time()
    gs.publish_candidates("football", [_candidate("football", i, now) for i in range(1, 4)])
    gs.publish_candidates("hockey", [_candidate("hockey", i, now) for i in range(1, 3)])
    gs.publish_candidates("basketball", [_candidate("basketball", 1, now)])

    monkeypatch.setattr(gs, "time", gs.time)
    status = gs.maybe_deliver_global_super10(delivery_enabled=True)

    assert status["status"] == "not_ready"
    assert status["target"] == 10
    assert status["available"] == 6
    assert status["need_more"] == 4
    assert status["available_by_sport"] == {"football": 3, "hockey": 2, "basketball": 1}


def test_global_super10_reconciles_cross_sport_results(tmp_path: Path, monkeypatch):
    import json
    from gool_bot2.providers import flashscore as flashscore_module

    _paths(tmp_path, monkeypatch)
    ticket = {
        "kind": "GLOBAL_SUPER",
        "result": "pending",
        "created_at": "2026-10-08T00:00:00+00:00",
        "legs": [
            {
                "sport": "football",
                "event_id": "FOOT0001",
                "market": "match_total",
                "market_family": "match_total",
                "selection": "ТБ 2.5",
                "odd": 1.50,
                "result": "pending",
            },
            {
                "sport": "basketball",
                "event_id": "BASK0001",
                "scope": "QUARTER_2",
                "market_family": "match_total",
                "selection": "2-я четверть: ТБ 40.5",
                "direction": "over",
                "line": 40.5,
                "odd": 1.60,
                "result": "pending",
            },
            {
                "sport": "hockey",
                "event_id": "HOCK0001",
                "scope": "PERIOD_1",
                "market_family": "match_total",
                "selection": "1-й период: ТМ 1.5",
                "direction": "under",
                "line": 1.5,
                "odd": 1.70,
                "result": "pending",
            },
        ],
    }
    gs.history_path().write_text(json.dumps([ticket]), "utf-8")
    gs.sent_path().write_text(json.dumps({"day": "2026-10-08", "sent": True, "ticket": ticket}), "utf-8")

    class FakeFlashscore:
        def event_states(self, ids):
            return {
                "FOOT0001": {"is_finished": True, "home_score": 2, "away_score": 1},
                "BASK0001": {"is_finished": True, "home_score": 84, "away_score": 80},
                "HOCK0001": {"is_finished": True, "home_score": 3, "away_score": 2},
            }

        def fetch_segment_scores(self, event_id, sport):
            if event_id == "BASK0001":
                return {"QUARTER_2": [21, 22]}
            if event_id == "HOCK0001":
                return {"PERIOD_1": [1, 0]}
            return {}

        def fetch_goal_timeline(self, event_id):
            return []

    monkeypatch.setattr(flashscore_module, "FlashscoreProvider", FakeFlashscore)

    result = gs.reconcile_global_super10(deliver_result=False)

    assert result["settled"] == 1
    stored = json.loads(gs.history_path().read_text("utf-8"))[0]
    assert stored["result"] == "won"
    assert [leg["result"] for leg in stored["legs"]] == ["won", "won", "won"]
    assert stored["effective_odd"] == 4.08
    sent = json.loads(gs.sent_path().read_text("utf-8"))
    assert sent["ticket"]["result"] == "won"


def test_super10_football_first_half_settlement_counts_stoppage_time(tmp_path: Path, monkeypatch):
    class FakeProvider:
        def fetch_goal_timeline(self, event_id):
            return [
                {"base_minute": 12, "minute": 12, "home": 1, "away": 0},
                {"base_minute": 45, "minute": 47, "home": 1, "away": 1},
                {"base_minute": 70, "minute": 70, "home": 2, "away": 1},
            ]

    leg = {
        "sport": "football",
        "event_id": "FOOTHT01",
        "market": "1H_OVER_UNDER",
        "market_family": "first_half_total",
        "selection": "over 1.5",
        "odd": 1.80,
    }
    state = {"is_finished": True, "home_score": 2, "away_score": 1}

    assert gs._settle_super_leg(FakeProvider(), leg, state) == "won"


def test_candidate_score_is_independent_of_odds():
    base = {
        "sport": "football",
        "model_probability": 0.80,
        "data_quality": 0.85,
        "strength": 82,
        "edge": 0.08,
    }
    low = {**base, "odd": 1.20}
    high = {**base, "odd": 1.68}
    assert gs._candidate_score(low) == gs._candidate_score(high)


def test_global_super10_prefers_more_confident_reserve_over_weaker_strict(tmp_path: Path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    now = time.time()

    def row(sport, event_id, p, quality, strength, tier_hint):
        value = _candidate(sport, event_id, now, odd=1.50, p=p)
        value["event_id"] = f"{sport}-{event_id}"
        value["data_quality"] = quality
        value["strength"] = strength
        value["global_super_score"] = gs._candidate_score(value)
        value["_tier_hint"] = tier_hint
        return value

    strict = [
        row("football", 1, 0.78, 0.85, 82, "strict"),
        row("hockey", 1, 0.77, 0.84, 82, "strict"),
        row("basketball", 1, 0.76, 0.84, 81, "strict"),
    ]
    # Seven ordinary strict fillers, one deliberately weaker than the reserve.
    strict.extend(
        row("football" if i % 2 == 0 else "hockey", 10+i, 0.73 + i*0.002, 0.80, 78, "strict")
        for i in range(6)
    )
    strong_reserve = row("basketball", 99, 0.86, 0.59, 90, "reserve")

    monkeypatch.setattr(gs, "eligible_candidates", lambda now_ts=None: sorted(strict, key=lambda x: x["global_super_score"], reverse=True))
    monkeypatch.setattr(gs, "reserve_candidates", lambda now_ts=None: sorted([*strict, strong_reserve], key=lambda x: x["global_super_score"], reverse=True))
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_MAX_PER_SPORT", "6")

    ticket = gs.build_global_super10(now_ts=now)

    assert ticket is not None
    ids = {leg["event_id"] for leg in ticket["legs"]}
    assert "basketball-99" in ids
    reserve_leg = next(leg for leg in ticket["legs"] if leg["event_id"] == "basketball-99")
    assert reserve_leg["super_tier"] == "reserve"
    assert reserve_leg["super_confidence_score"] > 0


def test_super10_missing_football_total_line_is_not_coerced_to_zero():
    row = gs._normalize_candidate(
        {
            "event_id": "f-total-1",
            "home": "Tractor",
            "away": "Esteghlal",
            "market": "home_total",
            "selection": "ИТМ1 1.5",
            "odd": 1.39,
            "model_probability": 0.80,
            "market_probability": 0.65,
            "edge": 0.15,
            "data_quality": 0.85,
            "strength": 84,
            "start_ts": time.time() + 3600,
        },
        "football",
    )
    assert row is not None
    assert row["line"] is None


def test_super10_individual_under_1_5_at_one_goal_is_won():
    class Provider:
        def fetch_goal_timeline(self, event_id):
            return []

    leg = {
        "sport": "football",
        "event_id": "f-total-2",
        "market": "home_total",
        "market_family": "home_total",
        "selection": "ИТМ1 1.5",
        "line": None,
    }
    state = {"is_finished": True, "home_score": 1, "away_score": 1}
    assert gs._settle_super_leg(Provider(), leg, state) == "won"


def test_super_ticket_waits_for_all_legs_before_final_lost():
    class Provider:
        def fetch_goal_timeline(self, event_id):
            return []

    ticket = {
        "result": "pending",
        "legs": [
            {
                "sport": "football",
                "event_id": "done",
                "market": "match_total",
                "market_family": "match_total",
                "selection": "ТБ 2.5",
                "line": None,
                "result": "pending",
            },
            {
                "sport": "football",
                "event_id": "waiting",
                "market": "match_total",
                "market_family": "match_total",
                "selection": "ТБ 2.5",
                "line": None,
                "result": "pending",
            },
        ],
    }
    states = {
        "done": {"is_finished": True, "home_score": 0, "away_score": 0},
        "waiting": {"is_finished": False, "home_score": 0, "away_score": 0},
    }
    assert gs._settle_super_ticket(Provider(), ticket, states) is True
    assert ticket["legs"][0]["result"] == "lost"
    assert ticket["legs"][1]["result"] == "pending"
    assert ticket["result"] == "pending"


def test_reconcile_repairs_legacy_zero_line_and_premature_lost(tmp_path: Path, monkeypatch):
    import json
    from gool_bot2.providers import flashscore as flashscore_module

    _paths(tmp_path, monkeypatch)
    ticket = {
        "kind": "GLOBAL_SUPER",
        "result": "lost",
        "settled_at": "2026-10-08T15:00:00+00:00",
        "profit_units": -1.0,
        "created_at": "2026-10-08T12:00:00+00:00",
        "legs": [
            {
                "sport": "football",
                "event_id": "tractor",
                "market": "home_total",
                "market_family": "home_total",
                "selection": "ИТМ1 1.5",
                "line": 0.0,
                "odd": 1.39,
                "result": "lost",
                "settled_score": [1, 1],
            },
            {
                "sport": "hockey",
                "event_id": "later",
                "scope": "FULL_MATCH",
                "market_family": "match_total",
                "selection": "ТБ 4.5",
                "line": 4.5,
                "direction": "over",
                "odd": 1.50,
                "result": "pending",
            },
        ],
    }
    gs.history_path().write_text(json.dumps([ticket]), "utf-8")
    gs.sent_path().write_text(json.dumps({"day": "2026-10-08", "sent": True, "ticket": ticket}), "utf-8")

    class FakeFlashscore:
        def event_states(self, ids):
            return {
                "tractor": {"is_finished": True, "home_score": 1, "away_score": 1},
                "later": {"is_finished": False, "home_score": 0, "away_score": 0},
            }

        def fetch_goal_timeline(self, event_id):
            return []

    monkeypatch.setattr(flashscore_module, "FlashscoreProvider", FakeFlashscore)

    result = gs.reconcile_global_super10(deliver_result=False)

    stored = json.loads(gs.history_path().read_text("utf-8"))[0]
    assert result["changed"] >= 1
    assert stored["result"] == "pending"
    assert stored["legs"][0]["line"] is None
    assert stored["legs"][0]["result"] == "won"
    assert stored["legs"][1]["result"] == "pending"


def test_super10_result_report_is_sent_only_after_last_match_finishes(tmp_path: Path, monkeypatch):
    import json
    from gool_bot2.providers import flashscore as flashscore_module

    _paths(tmp_path, monkeypatch)
    ticket = {
        "kind": "GLOBAL_SUPER",
        "result": "pending",
        "created_at": "2026-10-08T12:00:00+00:00",
        "legs": [
            {
                "sport": "football",
                "event_id": f"f{i}",
                "market": "match_total",
                "market_family": "match_total",
                "selection": "ТБ 0.5",
                "line": 0.5,
                "odd": 1.20,
                "result": "pending",
            }
            for i in range(10)
        ],
    }
    gs.history_path().write_text(json.dumps([ticket]), "utf-8")
    gs.sent_path().write_text(
        json.dumps({"day": "2026-10-08", "sent": True, "ticket": ticket}),
        "utf-8",
    )

    state = {"last_finished": False}

    class FakeFlashscore:
        def event_states(self, ids):
            out = {}
            for event_id in ids:
                index = int(str(event_id)[1:])
                finished = index < 9 or state["last_finished"]
                out[event_id] = {
                    "is_finished": finished,
                    "home_score": 1 if finished else 0,
                    "away_score": 0,
                }
            return out

        def fetch_goal_timeline(self, event_id):
            return []

    sent = []
    monkeypatch.setattr(flashscore_module, "FlashscoreProvider", FakeFlashscore)
    monkeypatch.setattr(gs, "render_v4_parlay_card", lambda row, result=False: b"PNG")
    monkeypatch.setattr(
        gs.telegram,
        "broadcast_photo",
        lambda png, caption="": sent.append(caption) or 1,
    )

    first = gs.reconcile_global_super10(deliver_result=True)
    stored = json.loads(gs.history_path().read_text("utf-8"))[0]
    assert first["delivered"] == 0
    assert stored["result"] == "pending"
    assert [leg["result"] for leg in stored["legs"]].count("won") == 9
    assert stored["legs"][9]["result"] == "pending"
    assert sent == []

    state["last_finished"] = True
    second = gs.reconcile_global_super10(deliver_result=True)
    stored = json.loads(gs.history_path().read_text("utf-8"))[0]
    assert second["settled"] == 1
    assert second["delivered"] == 1
    assert stored["result"] == "won"
    assert all(leg["result"] == "won" for leg in stored["legs"])
    assert len(sent) == 1
    assert "Все 10 матчей завершены" in sent[0]

    third = gs.reconcile_global_super10(deliver_result=True)
    assert third["delivered"] == 0
    assert len(sent) == 1
