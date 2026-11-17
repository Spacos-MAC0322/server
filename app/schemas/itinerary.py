"""Read contracts for itineraries and optional daily weather."""

import re
from datetime import date as Date
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.integrations.open_meteo import WeatherForecast


class ItineraryStopSchema(BaseModel):
    """A visit with optional planned times, including stops stored before planning."""

    place_id: str
    order: int = Field(strict=True)
    planned_start_time: str | None = Field(
        default=None,
        validation_alias=AliasChoices("planned_start_time", "arrival_time"),
    )
    planned_end_time: str | None = Field(
        default=None,
        validation_alias=AliasChoices("planned_end_time", "departure_time"),
    )
    conflict_warning: str | None = None


class ItinerarySchema(BaseModel):
    """Complete itinerary with owner and typed stops."""

    id: str = Field(validation_alias=AliasChoices("id", "_id"))
    user_id: str
    title: str
    date: str | None = None
    stops: list[ItineraryStopSchema]


class ItineraryValidationStop(ItineraryStopSchema):
    @field_validator("planned_start_time", "planned_end_time")
    @classmethod
    def validate_local_time(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(
            r"(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?", value
        ):
            raise ValueError("Informe um horário no formato HH:MM ou HH:MM:SS.")
        return value


class ItineraryValidationRequest(BaseModel):
    date: Date
    stops: list[ItineraryValidationStop]


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
