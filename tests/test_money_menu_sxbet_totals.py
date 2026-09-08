from __future__ import annotations

from datetime import datetime, timezone


def test_money_board_renders_sxbet_tb_tm_when_betdaq_is_down(monkeypatch) -> None:
    from gool_bot2 import money_menu, sxbet_public, sxbet_totals

    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        money_menu,
        "load_betdaq_state",
        lambda: {"captured_at": now, "available": False, "error": "RuntimeError:betdaq_no_relevant_markets"},
    )
    monkeypatch.setattr(
        sxbet_public,
        "fetch_sxbet_state",
        lambda **_: {
            "captured_at": now,
            "available": True,
            "markets_seen": 3,
            "orders_seen": 0,
            "events": [],
        },
    )
    monkeypatch.setattr(
        sxbet_totals,
        "fetch_sxbet_totals_state",
        lambda **_: {
            "captured_at": now,
            "available": True,
            "markets_seen": 1,
            "orders_seen": 2,
            "events": [
                {
                    "event_id": "E1",
                    "name": "Home — Away",
                    "line": 2.5,
                    "liquidity_usdc": 850.0,
                    "outcomes": {
                        "TB": {"decimal_odd": 1.82, "available_usdc": 450.0},
                        "TM": {"decimal_odd": 2.05, "available_usdc": 400.0},
                    },
                    "flow": {
                        "ready": True,
                        "level": "LIQUIDITY_PUSH",
                        "outcome": "TB",
                        "old_odd": 1.90,
                        "new_odd": 1.82,
                        "implied_delta_pp": 1.2,
                        "liquidity_delta_usdc": 80.0,
                        "relative_liquidity_pct": 12.0,
                    },
                }
            ],
        },
    )

    text = money_menu.money_text()

    assert "SX TOTALS · ТБ/ТМ" in text
    assert "Тотал <b>2.5</b>" in text
    assert "TB 1.82" in text
    assert "TM 2.05" in text
    assert "SX тотал: <b>TB</b>" in text
    assert "betdaq_no_relevant_markets" in text
