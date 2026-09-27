from gool_bot2.football_model_challengers import poisson_profile_challenger, total_probability


def test_poisson_challenger_returns_normalized_probabilities():
    forecast = poisson_profile_challenger({"full_match": {
        "home_expected_goals": 1.8, "away_expected_goals": 1.1,
    }})
    assert forecast is not None
    assert forecast.name == "poisson_profile_v1"
    assert abs(forecast.home + forecast.draw + forecast.away - 1.0) < 1e-9
    assert forecast.home > forecast.away


def test_poisson_challenger_total_probability_is_complementary():
    forecast = poisson_profile_challenger({"full_match": {
        "home_expected_goals": 1.5, "away_expected_goals": 1.2,
    }})
    assert forecast is not None
    over = total_probability(forecast, 2.5, True)
    under = total_probability(forecast, 2.5, False)
    assert abs(over + under - 1.0) < 1e-9


def test_poisson_challenger_does_not_need_bookmaker_market():
    forecast = poisson_profile_challenger({"full_match": {
        "home_expected_goals": 1.2, "away_expected_goals": 0.8,
    }})
    assert forecast is not None
