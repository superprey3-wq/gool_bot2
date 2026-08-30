from __future__ import annotations

from gool_bot2.live_probe import find_flashscore_match
from gool_bot2.providers.common import ProviderMatch


class FakeFlashscore:
    def live_matches(self):
        return [
            ProviderMatch(
                provider="flashscore",
                provider_match_id="abc12345",
                home="Rodina Moscow",
                away="Baltika",
                minute=31,
                home_score=0,
                away_score=1,
                league="Russia: Premier League",
            ),
            ProviderMatch(
                provider="flashscore",
                provider_match_id="xyz67890",
                home="Other Club",
                away="Different Club",
                minute=50,
                home_score=1,
                away_score=1,
            ),
        ]


def test_find_flashscore_match_accepts_short_team_query():
    match, score = find_flashscore_match("Rodina", "Baltika", provider=FakeFlashscore())
    assert match is not None
    assert match.provider_match_id == "abc12345"
    assert score >= 0.62


def test_find_flashscore_match_rejects_unrelated_pair():
    match, _ = find_flashscore_match("Barcelona", "Sevilla", provider=FakeFlashscore())
    assert match is None
