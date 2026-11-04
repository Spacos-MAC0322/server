"""Itinerary weather and travel estimates, independent of persistence."""

import asyncio
import logging
import math
from collections.abc import Mapping, Sequence
from datetime import date as Date
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any
from zipfile import BadZipFile
from zoneinfo import ZoneInfo

from app.core.http_client import ExternalAPIError
from app.integrations.open_meteo import OpenMeteoClient
from app.integrations.sptrans import SPTransClient
from app.integrations.sptrans_gtfs import distance_m, validate_coordinates
from app.repositories.itinerary_repository import ItineraryRepository
from app.schemas.itinerary import BusLineSchema, TransitLegSchema, WeatherResponse

logger = logging.getLogger(__name__)
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
SAO_PAULO_LAT = -23.5505
SAO_PAULO_LNG = -46.6333
# Open-Meteo offers up to 16 calendar days, including today.
FORECAST_DAYS = 16
WEATHER_TIMEOUT_SECONDS = 5
TRANSIT_TIMEOUT_SECONDS = 5


def today_in_sao_paulo() -> Date:
    return datetime.now(SAO_PAULO).date()


class ItineraryPlanner:
    def __init__(
        self,
        weather_client: OpenMeteoClient,
        *,
        transit_client: SPTransClient | None = None,
    ):
        self.weather_client = weather_client
        self.transit_client = transit_client

    async def calculate_transit_legs(
        self,
        places: Sequence[Mapping[str, Any]],
        *,
        departure_at: datetime | None = None,
    ) -> list[TransitLegSchema]:
        """Estimate each consecutive pair of resolved places, in supplied order.

        Each place must have id (or MongoDB _id) and a GeoJSON Point location
        with [longitude, latitude]. Resolve itinerary stop place_ids beforehand.
        departure_at is departure from the first place; intermediate places may
        provide visit_duration_minutes, included before the next travel leg.

        Bus options use the existing GTFS adapter with include_live=False. The
        fastest option determines duration, rounded up to whole minutes. With
        no connection, no client or unavailable GTFS, suggested_lines is empty
        and duration estimates walking (1.3 times straight-line distance at
        4.8 km/h). Invalid place input raises ValueError before any query.
        """
        if len(places) < 2:
            return []
        resolved = [_transit_place(place) for place in places]
        cursor = departure_at or datetime.now(SAO_PAULO)
        if cursor.tzinfo is None or cursor.utcoffset() is None:
            raise ValueError("departure_at must include a timezone")
        cursor = cursor.astimezone(SAO_PAULO)
        legs = []
        for origin, destination in pairwise(resolved):
            origin_id, origin_lat, origin_lng, _ = origin
            destination_id, destination_lat, destination_lng, visit_minutes = (
                destination
            )
            distance = distance_m(
                origin_lat, origin_lng, destination_lat, destination_lng
            )
            duration = math.ceil(distance * 1.3 / 80)
            lines = []
            if distance > 0 and self.transit_client is not None:
                try:
                    async with asyncio.timeout(TRANSIT_TIMEOUT_SECONDS):
                        connections = await self.transit_client.find_connections(
                            origin_lat,
                            origin_lng,
                            destination_lat,
                            destination_lng,
                            departure_at=cursor,
                            include_live=False,
                        )
                    if connections:
                        lines, duration = _transit_options(connections)
                except (
                    ExternalAPIError,
                    TimeoutError,
                    OSError,
                    BadZipFile,
                    ValueError,
                    KeyError,
                    TypeError,
                ) as error:
                    logger.warning(
                        "Itinerary transit unavailable: %s", type(error).__name__
                    )
            legs.append(
                TransitLegSchema(
                    from_place_id=origin_id,
                    to_place_id=destination_id,
                    suggested_lines=lines,
                    estimated_duration_min=duration,
                )
            )
            cursor += timedelta(minutes=duration + visit_minutes)
        return legs

    async def get_weather(self, date: Date) -> WeatherResponse:
        """Keep optional enrichment within a short deadline, including retries."""
        days_ahead = (date - today_in_sao_paulo()).days
        if not 0 <= days_ahead < FORECAST_DAYS:
            return WeatherResponse(
                weather_warning="Data fora da janela de previsão do tempo."
            )
        if not self.weather_client.settings.open_meteo_enabled:
            return WeatherResponse(
                weather_warning="Previsão do tempo temporariamente indisponível."
            )
        try:
            async with asyncio.timeout(WEATHER_TIMEOUT_SECONDS):
                forecast = await self.weather_client.get_weather_forecast(
                    date, SAO_PAULO_LAT, SAO_PAULO_LNG
                )
            return WeatherResponse(weather_forecast=forecast)
        except (ExternalAPIError, TimeoutError) as error:
            # Raw provider messages can contain URLs or upstream response details.
            logger.warning("Itinerary weather unavailable: %s", type(error).__name__)
            return WeatherResponse(
                weather_warning="Previsão do tempo temporariamente indisponível."
            )

    async def attach_weather_forecast(self, itinerary: dict) -> dict:
        """Return an enriched copy; forecasts are never persisted in MongoDB."""
        try:
            day = Date.fromisoformat(itinerary["date"])
        except (KeyError, ValueError, TypeError):
            weather = WeatherResponse(
                weather_warning="Roteiro sem data válida para consultar a previsão."
            )
        else:
            weather = await self.get_weather(day)
        return {**itinerary, **weather.model_dump()}

    async def get_itinerary(
        self, itinerary_id: str, repository: ItineraryRepository
    ) -> dict | None:
        itinerary = await repository.find_by_id(itinerary_id)
        if itinerary is None:
            return None
        itinerary = await self.attach_weather_forecast(itinerary)
        itinerary["id"] = itinerary.pop("_id")
        return itinerary


def _transit_place(place: Mapping[str, Any]) -> tuple[str, float, float, int]:
    if not isinstance(place, Mapping):
        raise ValueError("Each place must be a mapping with id and location")
    try:
        identity = place.get("id", place.get("_id"))
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("A nonblank place id is required")
        location = place["location"]
        if location["type"] != "Point" or len(location["coordinates"]) != 2:
            raise ValueError("A GeoJSON Point is required")
        lng, lat = location["coordinates"]
        validate_coordinates(lat, lng)
        visit = place.get("visit_duration_minutes")
        visit = 0 if visit is None else visit
        if type(visit) is not int or visit < 0:
            raise ValueError("visit_duration_minutes must be a nonnegative integer")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "Each place requires a valid id, Point and visit duration"
        ) from error
    return identity, lat, lng, visit


def _transit_options(connections: list[dict]) -> tuple[list[BusLineSchema], int]:
    options = []
    for connection in connections:
        duration = connection["estimated_duration_minutes"]
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise ValueError("Invalid transit duration")
        line = BusLineSchema(
            route_id=connection["route_id"],
            code=connection["line"],
            headsign=connection.get("headsign", ""),
            direction_id=connection.get("direction_id"),
        )
        options.append((duration, line))
    options.sort(key=lambda option: option[0])
    lines = {}
    for _, line in options:
        lines.setdefault((line.route_id, line.direction_id), line)
    return list(lines.values()), math.ceil(options[0][0])
