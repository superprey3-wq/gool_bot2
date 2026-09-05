"""Football data providers used by gool_bot2."""

from .flashscore import FlashscoreProvider
from .flashscore_stats_guard import install as install_flashscore_stats_guard
from .flashscore_incident_guard import install as install_flashscore_incident_guard

# Flashscore publishes cumulative Match stats and period splits under the same
# stat ids. Keep the section-aware parser and then install the real incident
# grammar (IA=side, IE/IK=event type) plus the red-card stat mapping.
install_flashscore_stats_guard()
install_flashscore_incident_guard()

from .fotmob import FotMobProvider
from .scores365 import Scores365Provider
from .secondary_live_guard import install as install_secondary_live_guard
from .fotmob_freshness_guard import install as install_fotmob_freshness_guard
from .scores365_prematch_guard import install as install_scores365_prematch_guard

# FotMob and 365Scores expose useful live data through endpoints that refresh at
# different speeds. Normalize those endpoints without changing ProviderMatch.
install_secondary_live_guard()
# Enrich the 365 prematch layer with real historical HT/FT splits from the game
# ids exposed in recentMatches. It also carries current HT score and trend flags.
install_scores365_prematch_guard()
# The daily FotMob list is deliberately cached for discovery. Timing must come
# from the faster matchDetails header, not that slower discovery snapshot.
install_fotmob_freshness_guard()

from .fusion import FootballDataFusion

__all__ = [
    "FlashscoreProvider",
    "FotMobProvider",
    "Scores365Provider",
    "FootballDataFusion",
]
