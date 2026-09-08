from __future__ import annotations

from datetime import datetime, timezone


def _runner(role: str, back: float, lay: float, matched_for: float, matched_against: float) -> dict:
    return {
        "outcome": role,
        "label": role,
        "best_back": {"odds": back, "odd": back},
        "best_lay": {"odds": lay, "odd": lay},
        "selection_matched_ready": True,
        "matched_for_gbp": matched_for,
        "matched_against_gbp": matched_against,
        "back_depth_gbp": 500.0,
        "lay_depth_gbp": 400.0,
    }


def test_money_board_shows_real_money_by_1x2_and_total(monkeypatch) -> None:
    from gool_bot2 import money_menu

    now = datetime.now(timezone.utc)
    p1 = _runner("P1", 1.80, 1.82, 12000, 9000)
    draw = _runner("X", 4.20, 4.30, 8000, 7000)
    p2 = _runner("P2", 5.00, 5.20, 6000, 5500)
    over = _runner("TB", 1.90, 1.92, 11000, 10000)
    under = _runner("TM", 2.02, 2.04, 9500, 9000)
    state = {
        "captured_at": now.isoformat(),
        "available": True,
        "tracked_events": 1,
        "tracked_markets": 2,
        "tracked_total_markets": 1,
        "events_with_totals": 1,
        "valid_match_odds_labels": 1,
        "events": [
            {
                "event_id": "100",
                "name": "Home v Away",
                "home": "Home",
                "away": "Away",
                "start": now.isoformat(),
                "in_running": False,
                "matched_gbp": 54800.0,
                "runners": [p1, draw, p2],
                "flow": {"ready": True, "level": "NEUTRAL"},
                "totals": {
                    "FT:2.5": {
                        "period": "FT",
                        "line": 2.5,
                        "matched_gbp": 30000.0,
                        "volume": 30000.0,
                        "over": over,
                        "under": under,
                    }
                },
            }
        ],
    }
    monkeypatch.setattr(money_menu, "load_betdaq_state", lambda: state)
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")

    text = money_menu.money_text()

    assert "Match Odds matched <b>£54.8k</b>" in text
    assert "FOR matched: P1 £12.0k · X £8.0k · P2 £6.0k" in text
    assert "AGAINST matched: P1 £9.0k · X £7.0k · P2 £5.5k" in text
    assert "Главный тотал <b>2.5</b>" in text
    assert "ТБ £11.0k · ТМ £9.5k" in text
    assert "Стакан Back/Lay: P1 £500/£400" in text
