"""Football data providers used by gool_bot2."""

from .flashscore import FlashscoreProvider
from .fotmob import FotMobProvider
from .scores365 import Scores365Provider
from .fusion import FootballDataFusion

__all__ = [
    "FlashscoreProvider",
    "FotMobProvider",
    "Scores365Provider",
    "FootballDataFusion",
]
