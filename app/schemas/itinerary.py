"""Read and write contracts for itineraries and optional daily weather."""

import re
from datetime import UTC, datetime
from datetime import date as Date
from typing import Annotated, Any

from pydantic import AfterValidator, AliasChoices, BaseModel, Field, field_validator

from app.integrations.open_meteo import WeatherForecast


def _as_utc(value: datetime | str | None) -> datetime | str | None:
    """MongoDB stores dates in UTC but reads them back without tzinfo."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# Serialized with an explicit offset ("...Z"), whether fresh or read from MongoDB.
StoredTimestamp = Annotated[datetime | str | None, AfterValidator(_as_utc)]


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


MAX_TITLE_LENGTH = 120
MAX_STOPS = 50


class ItineraryWriteStop(ItineraryValidationStop):
    """A persisted visit, including an optional link to a cultural event."""

    # The MongoDB validator requires BSON int (signed 32 bits), not long.
    order: int = Field(strict=True, ge=-(2**31), le=2**31 - 1)
    event_id: str | None = None


class ItineraryCreate(BaseModel):
    """Body to save an itinerary of the logged-in user.

    Stops keep the order given in the list; conflict_warning sent by the client
    is ignored, since the server recalculates it on every save.
    """

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    date: Date | None = None
    stops: list[ItineraryWriteStop] = Field(default_factory=list, max_length=MAX_STOPS)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Informe um nome para o roteiro.")
        return value

    @field_validator("stops")
    @classmethod
    def validate_stops(
        cls, stops: list[ItineraryWriteStop]
    ) -> list[ItineraryWriteStop]:
        orders = [stop.order for stop in stops]
        if len(set(orders)) != len(orders):
            raise ValueError("Cada parada precisa de uma ordem diferente.")
        for stop in stops:
            start, end = stop.planned_start_time, stop.planned_end_time
            # HH:MM and HH:MM:SS compare correctly as text once zero-padded.
            if start and end and _pad_seconds(end) <= _pad_seconds(start):
                raise ValueError(
                    "O horário de saída precisa ser depois do horário de chegada."
                )
        return stops


class ItineraryUpdate(ItineraryCreate):
    """PUT body: replaces title, date and every stop of the itinerary."""


class ItinerarySummary(BaseModel):
    """Itinerary entry in the logged-in user's list."""

    id: str = Field(validation_alias=AliasChoices("id", "_id"))
    title: str
    date: str | None = None
    stop_count: int = Field(ge=0)
    created_at: StoredTimestamp = None
    updated_at: StoredTimestamp = None


def _pad_seconds(value: str) -> str:
    return value if len(value) == 8 else f"{value}:00"


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
    created_at: StoredTimestamp = None
    updated_at: StoredTimestamp = None
