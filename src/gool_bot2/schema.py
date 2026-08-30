from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class MatchSnapshot(BaseModel):
    """Canonical provider-independent live match snapshot."""

    match_id: str
    captured_at: datetime
    minute: float = Field(ge=0, le=130)
    period: int = Field(ge=1, le=2)

    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)

    home_shots: int | None = Field(default=None, ge=0)
    away_shots: int | None = Field(default=None, ge=0)
    home_shots_on_target: int | None = Field(default=None, ge=0)
    away_shots_on_target: int | None = Field(default=None, ge=0)
    home_corners: int | None = Field(default=None, ge=0)
    away_corners: int | None = Field(default=None, ge=0)
    home_red_cards: int | None = Field(default=None, ge=0)
    away_red_cards: int | None = Field(default=None, ge=0)
    home_dangerous_attacks: int | None = Field(default=None, ge=0)
    away_dangerous_attacks: int | None = Field(default=None, ge=0)
    home_possession: float | None = Field(default=None, ge=0, le=100)
    away_possession: float | None = Field(default=None, ge=0, le=100)

    source: str
