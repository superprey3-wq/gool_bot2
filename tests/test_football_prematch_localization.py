"""Display-only Russian translations of football PREMATCH coupon outcomes."""
import pytest

from gool_bot2.football_prematch_localization import football_selection_ru


@pytest.mark.parametrize("market,selection,expected", [
    ("asian_handicap", "home +2.5", "Ф1 (+2.5)"),
    ("asian_handicap", "away -1.5", "Ф2 (-1.5)"),
    ("european_handicap", "home +1.5", "Ф1 (+1.5)"),
    ("double_chance", "home or away", "12 — ничьей не будет"),
    ("double_chance", "draw or away", "Х2 — гости не проиграют"),
    ("double_chance", "home or draw", "1Х — хозяева не проиграют"),
    ("match_1x2", "home", "П1 — победа хозяев"),
    ("match_1x2", "draw", "Х — ничья"),
    ("match_1x2", "away", "П2 — победа гостей"),
    ("draw_no_bet", "home", "П1 — возврат при ничьей"),
    ("draw_no_bet", "away", "П2 — возврат при ничьей"),
    ("btts", "yes", "Обе забьют — Да"),
    ("btts", "no", "Обе забьют — Нет"),
    ("match_total", "over 2.5", "ТБ 2.5"),
    ("match_total", "under 1.5", "ТМ 1.5"),
    ("home_total", "under 0.5", "ИТМ1 0.5"),
    ("away_total", "over 1.5", "ИТБ2 1.5"),
    ("1H_OVER_UNDER", "over 0.5", "1-й тайм · ТБ 0.5"),
    ("2H_OVER_UNDER", "under 0.5", "2-й тайм · ТМ 0.5"),
    ("FT_UNDER_2.5", "FT_UNDER_2.5", "ТМ 2.5"),
    ("FT_OVER_2.5", "FT_OVER_2.5", "ТБ 2.5"),
    ("BTTS_YES", "BTTS_YES", "Обе забьют — Да"),
    ("BTTS_NO", "BTTS_NO", "Обе забьют — Нет"),
])
def test_football_prematch_russian_labels(market, selection, expected):
    assert football_selection_ru(market, selection) == expected


def test_ambiguous_selection_is_not_guessed_without_market_context():
    assert football_selection_ru("unknown_future_market", "yes") == "yes"
    assert football_selection_ru("unknown_future_market", "home") == "home"
    assert football_selection_ru("unknown_future_market", "away") == "away"


def test_existing_tournament_details_show_russian_without_repricing(monkeypatch):
    from gool_bot2 import tournament_express as tx
    from datetime import datetime
    from zoneinfo import ZoneInfo

    day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    group = {
        "key": "ucl", "league": "EUROPE: Champions League - League phase",
        "fixture_count": 3, "qualified_count": 3, "complete": True,
        "coverage_only_count": 3, "combined_odd": 2.2,
        "matches": [
            {
                "home": "A", "away": "B", "kickoff_ts": 2200000000.0,
                "status": "READY",
                "selection": {
                    "market": market, "selection": choice,
                    "odd": odd, "model_probability": .73, "break_even_probability": 1.0 / odd,
                    "qualification": "COVERAGE_ONLY",
                },
            }
            for market, choice, odd in (
                ("asian_handicap", "home +2.5", 1.30),
                ("double_chance", "draw or away", 1.35),
                ("btts", "yes", 1.31),
            )
        ],
    }
    monkeypatch.setattr(tx, "_today_state", lambda: {
        "date": day, "scan_status": "complete", "tournaments": [group],
    })
    text = tx.tournament_details("ucl")
    assert "Ф1 (+2.5)" in text
    assert "Х2 — гости не проиграют" in text
    assert "Обе забьют — Да" in text
    assert "home +2.5" not in text
    assert "draw or away" not in text
    assert "yes @" not in text


def test_journal_pick_remains_in_english_for_settlement():
    from gool_bot2.v4_prematch_delivery import prematch_row_from_pick
    from gool_bot2.v4_prematch_engine import PrematchPick
    from gool_bot2.v4_prematch_settlement import settle_prematch_pick

    pick = PrematchPick("e", "A", "B", "double_chance", "draw or away",
                        1.35, .75, .73, .80, "UEFA Champions League", 1800000000.0)
    journal = prematch_row_from_pick(pick)
    assert journal["selection"] == "draw or away"
    assert journal["market"] == "double_chance"
    assert settle_prematch_pick(journal, 0, 1) == "won"
    assert football_selection_ru(journal["market"], journal["selection"]) == "Х2 — гости не проиграют"
