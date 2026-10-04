from gool_bot2.xbet_multisport_markets import prematch_market_lanes


def test_prematch_market_lanes_include_totals_handicap_and_two_way_moneyline():
    decoded = {
        "FULL_MATCH": {
            "match_total": [{"line": 5.5, "over": 1.8, "under": 2.0}],
            "home_total": [{"line": 2.5, "over": 1.9, "under": 1.9}],
            "away_total": [{"line": 2.5, "over": 2.0, "under": 1.8}],
            "handicap": [{"home_line": -1.5, "home": 1.95, "away_line": 1.5, "away": 1.85}],
            "moneyline": {
                "kind": "moneyline_2way",
                "home": 1.7,
                "away": 2.1,
                "variants": [{"kind": "moneyline_2way", "home": 1.7, "away": 2.1}],
            },
        }
    }
    lanes = prematch_market_lanes(decoded, "hockey")
    families = [x["market_family"] for x in lanes]
    assert "match_total" in families
    assert "home_total" in families
    assert "away_total" in families
    assert families.count("handicap") == 2
    assert families.count("moneyline") == 2
    assert any(x.get("selection") == "Ф1 -1.5" for x in lanes)
    assert any(x.get("selection") == "П1" for x in lanes)


def test_hockey_regulation_1x2_is_not_auto_lane_without_two_way_winner():
    decoded = {
        "FULL_MATCH": {
            "match_total": [],
            "home_total": [],
            "away_total": [],
            "handicap": [],
            "moneyline": {
                "kind": "regulation_1x2",
                "home": 2.1,
                "draw": 3.8,
                "away": 2.8,
                "variants": [{"kind": "regulation_1x2", "home": 2.1, "draw": 3.8, "away": 2.8}],
            },
        }
    }
    lanes = prematch_market_lanes(decoded, "hockey")
    assert not [x for x in lanes if x["market_family"] == "moneyline"]
