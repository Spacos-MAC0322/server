"""Read contracts for itineraries and optional daily weather."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.integrations.open_meteo import WeatherForecast


class BusLineSchema(BaseModel):
    """GTFS bus route and direction; route_id is not an Olho Vivo line code."""

    route_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    headsign: str = ""
    direction_id: str | None = None


class TransitLegSchema(BaseModel):
    """Travel between consecutive places; empty lines means estimated walking."""

    from_place_id: str = Field(min_length=1)
    to_place_id: str = Field(min_length=1)
    suggested_lines: list[BusLineSchema]
    estimated_duration_min: int = Field(ge=0, strict=True)


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
