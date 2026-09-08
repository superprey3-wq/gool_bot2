from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gool_bot2.betdaq_selection_alerts import SelectionPushTracker, alert_text


def _runner(
    sid: int,
    *,
    outcome: str = "",
    label: str,
    back: float,
    lay: float,
    matched_for: float,
    matched_against: float = 100.0,
    back_depth: float = 200.0,
    lay_depth: float = 180.0,
) -> dict:
    return {
        "id": str(sid),
        "outcome": outcome,
        "label": label,
        "selection_matched_ready": True,
        "matched_for_gbp": matched_for,
        "matched_against_gbp": matched_against,
        "back_depth_gbp": back_depth,
        "lay_depth_gbp": lay_depth,
        "best_back": {"odds": back},
        "best_lay": {"odds": lay},
    }


def _state(
    captured_at: datetime,
    *,
    p1_for: float = 200.0,
    p1_back: float = 2.00,
    p1_lay: float = 2.04,
    tb_for: float = 200.0,
    tb_back: float = 2.00,
    tb_lay: float = 2.04,
    second_tb: tuple[float, float, float] | None = None,
) -> dict:
    p1 = _runner(1, outcome="P1", label="Real Madrid", back=p1_back, lay=p1_lay, matched_for=p1_for)
    draw = _runner(2, outcome="X", label="Draw", back=4.0, lay=4.1, matched_for=100.0)
    p2 = _runner(3, outcome="P2", label="Inter", back=5.0, lay=5.2, matched_for=100.0)
    over = _runner(11, label="Over 2.5", back=tb_back, lay=tb_lay, matched_for=tb_for)
    under = _runner(12, label="Under 2.5", back=1.90, lay=1.94, matched_for=100.0)
    totals = {
        "FT:2.5": {
            "id": "20",
            "period": "FT",
            "line": 2.5,
            "over": over,
            "under": under,
        }
    }
    if second_tb is not None:
        matched, back, lay = second_tb
        totals["FT:3.5"] = {
            "id": "21",
            "period": "FT",
            "line": 3.5,
            "over": _runner(13, label="Over 3.5", back=back, lay=lay, matched_for=matched),
            "under": _runner(14, label="Under 3.5", back=1.50, lay=1.54, matched_for=100.0),
        }
    return {
        "captured_at": captured_at.isoformat(),
        "events": [
            {
                "id": "1",
                "event_id": "1",
                "name": "Real Madrid v Inter",
                "home": "Real Madrid",
                "away": "Inter",
                "in_running": False,
                "match_odds": {"id": "10", "labels_valid": True, "runners": [p1, draw, p2]},
                "totals": totals,
            }
        ],
    }


def test_p1_selection_push_requires_real_matched_and_price_confirmation(monkeypatch):
    monkeypatch.setenv("BETDAQ_SELECTION_PUSH_COOLDOWN_SECONDS", "180")
    tracker = SelectionPushTracker()
    t0 = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)

    assert tracker.evaluate(_state(t0)) == []
    alerts = tracker.evaluate(_state(t0 + timedelta(seconds=16), p1_for=1050.0, p1_back=1.80, p1_lay=1.83))

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["selection"] == "P1"
    assert alert["label"] == "П1 Real Madrid"
    assert alert["delta_for_gbp"] == 850.0
    assert alert["level"] == "EXTREME_SELECTION_FLOW"
    assert alert["implied_delta_pp"] > 2.5


def test_money_without_price_shortening_does_not_alert():
    tracker = SelectionPushTracker()
    t0 = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    tracker.evaluate(_state(t0))

    alerts = tracker.evaluate(_state(t0 + timedelta(seconds=16), p1_for=1200.0, p1_back=2.00, p1_lay=2.04))
    assert alerts == []


def test_price_without_selection_matched_delta_does_not_alert():
    tracker = SelectionPushTracker()
    t0 = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    tracker.evaluate(_state(t0))

    alerts = tracker.evaluate(_state(t0 + timedelta(seconds=16), p1_for=200.0, p1_back=1.75, p1_lay=1.79))
    assert alerts == []


def test_total_tb_push_is_detected_and_rendered_in_russian():
    tracker = SelectionPushTracker()
    t0 = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    tracker.evaluate(_state(t0))

    alerts = tracker.evaluate(
        _state(t0 + timedelta(seconds=16), tb_for=950.0, tb_back=1.78, tb_lay=1.82)
    )
    assert len(alerts) == 1
    assert alerts[0]["selection"] == "TB"
    assert alerts[0]["label"] == "ТБ 2.5"

    text = alert_text(alerts[0])
    assert "BETDAQ ПРОГРУЗ" in text
    assert "ТБ 2.5" in text
    assert "FOR matched" in text
    assert "+£750" in text


def test_group_cooldown_suppresses_repeat_push(monkeypatch):
    monkeypatch.setenv("BETDAQ_SELECTION_PUSH_COOLDOWN_SECONDS", "180")
    tracker = SelectionPushTracker()
    t0 = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    tracker.evaluate(_state(t0))
    assert tracker.evaluate(_state(t0 + timedelta(seconds=16), p1_for=1050.0, p1_back=1.80, p1_lay=1.83))

    repeated = tracker.evaluate(_state(t0 + timedelta(seconds=32), p1_for=1400.0, p1_back=1.70, p1_lay=1.73))
    assert repeated == []


def test_correlated_total_lines_emit_only_strongest_line():
    tracker = SelectionPushTracker()
    t0 = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    tracker.evaluate(_state(t0, second_tb=(200.0, 2.40, 2.46)))

    alerts = tracker.evaluate(
        _state(
            t0 + timedelta(seconds=16),
            tb_for=900.0,
            tb_back=1.80,
            tb_lay=1.84,
            second_tb=(1200.0, 1.90, 1.94),
        )
    )
    tb_alerts = [row for row in alerts if row["selection"] == "TB"]
    assert len(tb_alerts) == 1
    assert tb_alerts[0]["label"] == "ТБ 3.5"
