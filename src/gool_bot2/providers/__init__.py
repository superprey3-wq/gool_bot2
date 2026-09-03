"""Football data providers used by gool_bot2."""

from .flashscore import FlashscoreProvider
from .flashscore_stats_guard import install as install_flashscore_stats_guard

# Flashscore publishes cumulative Match stats and period splits under the same
# stat ids. Install the section-aware parser before any consumer starts reading
# live stats so 1st/2nd-half rows cannot overwrite the full-match totals.
install_flashscore_stats_guard()

from .fotmob import FotMobProvider
from .scores365 import Scores365Provider
from .fusion import FootballDataFusion

__all__ = [
    "FlashscoreProvider",
    "FotMobProvider",
    "Scores365Provider",
    "FootballDataFusion",
]
