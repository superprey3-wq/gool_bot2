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
