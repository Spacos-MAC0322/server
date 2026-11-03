"""Read contracts for itineraries and optional daily weather."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.integrations.open_meteo import WeatherForecast


class WeatherResponse(BaseModel):
    weather_forecast: WeatherForecast | None = None
    weather_warning: str | None = None


class ItineraryResponse(WeatherResponse):
    id: str
    title: str
    date: str | None = None
    stops: list[dict[str, Any]]
    preferences: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | str | None = None
