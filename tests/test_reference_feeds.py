from gool_bot2.reference_feeds import PinnacleReference, american_to_decimal


def test_american_to_decimal():
    assert american_to_decimal(-200) == 1.5
    assert american_to_decimal(120) == 2.2
    assert american_to_decimal(0) is None


def test_pinnacle_decimal_prices_keeps_market_shape():
    rows = PinnacleReference.decimal_prices([
        {"key": "s;0;m", "prices": [{"designation": "home", "price": -125}, {"designation": "away", "price": 110}]}
    ])
    assert rows[0]["key"] == "s;0;m"
    assert rows[0]["prices"][0]["decimal"] == 1.8
    assert rows[0]["prices"][1]["decimal"] == 2.1
