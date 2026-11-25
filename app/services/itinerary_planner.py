"""Itinerary weather, travel estimates and opening hours checks."""

import asyncio
import logging
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date as Date
from datetime import datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zipfile import BadZipFile
from zoneinfo import ZoneInfo

from app.core.geo import distance_m
from app.core.http_client import ExternalAPIError
from app.integrations.open_meteo import OpenMeteoClient
from app.integrations.sptrans import SPTransClient
from app.integrations.sptrans_gtfs import validate_coordinates
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
CLOSED_WARNING = "O local estará fechado no horário planejado."
WEEKDAYS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")
DAY = rf"(?:{'|'.join(WEEKDAYS)})s?(?:[- ]feiras?)?\b"
DAY_LIST = rf"{DAY}(?:(?:\s*,\s*|\s+(?:a|ate|e)\s+){DAY})*"
DAY_SELECTOR = rf"(?:{DAY_LIST}|diariamente|todos os dias)"
CLOCK = r"\d{1,2}(?::\d{2}|h\d{0,2})?"
TIME_RANGE = re.compile(rf"({CLOCK})\s*(?:as|a|-)\s*({CLOCK})(?:\s+horas?)?")
HOURS_CLAUSE = re.compile(
    rf"(?:de\s+)?(?P<days>{DAY_SELECTOR})(?:\s+e feriados)?"
    r"\s*[:, -]*\s*(?:das\s+)?(?P<hours>.+)"
)
CLOSED_CLAUSE = re.compile(
    rf"(?P<days>{DAY_SELECTOR})\s*[:-]\s*fechad[oa]s?|"
    rf"fechad[oa]s?\s+(?:as?\s+)?(?P<closed_days>{DAY_SELECTOR})"
)


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
        self, itinerary_id: str, user_id: str, repository: ItineraryRepository
    ) -> dict | None:
        """The user's itinerary with fresh conflicts and weather; None otherwise."""
        itinerary = await repository.find_owned(itinerary_id, user_id)
        if itinerary is None:
            return None
        itinerary = await self.attach_opening_hours_conflicts(itinerary, repository)
        return await self.present_itinerary(itinerary)

    async def present_itinerary(self, itinerary: dict) -> dict:
        """Response shape for a stored itinerary: weather attached, `_id` as id."""
        itinerary = await self.attach_weather_forecast(itinerary)
        itinerary["id"] = itinerary.pop("_id")
        return itinerary

    async def attach_opening_hours_conflicts(
        self, itinerary: dict, repository: ItineraryRepository
    ) -> dict:
        """Evaluate visit start times in São Paulo using the weekly schedule."""
        try:
            day = Date.fromisoformat(itinerary["date"])
        except (KeyError, ValueError, TypeError):
            return itinerary

        stops = itinerary["stops"]
        visits = [
            _visit_minutes(stop.get("planned_start_time", stop.get("arrival_time")))
            for stop in stops
        ]
        place_ids = {
            stop["place_id"]
            for stop, start in zip(stops, visits, strict=True)
            if start is not None and isinstance(stop.get("place_id"), str)
        }
        if not place_ids:
            return itinerary

        hours_by_place = await repository.find_opening_hours(place_ids)
        schedules = {
            place_id: _weekly_hours(hours.get("text"))
            for place_id, hours in hours_by_place.items()
        }
        checked_stops = []
        weekday = day.weekday()
        for stop, start in zip(stops, visits, strict=True):
            schedule = schedules.get(stop.get("place_id"))
            if start is not None and schedule is not None and weekday in schedule:
                is_open = any(begin <= start < end for begin, end in schedule[weekday])
                if not is_open:
                    stop = {**stop, "conflict_warning": CLOSED_WARNING}
                elif stop.get("conflict_warning") == CLOSED_WARNING:
                    stop = {**stop, "conflict_warning": None}
            checked_stops.append(stop)
        return {**itinerary, "stops": checked_stops}


def _visit_minutes(value: object) -> float | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value):
        return None
    try:
        clock = time.fromisoformat(value)
    except ValueError:
        return None
    return clock.hour * 60 + clock.minute + clock.second / 60


def _clock_minutes(value: str) -> int | None:
    hour, _, minute = value.replace("h", ":").partition(":")
    hours, minutes = int(hour), int(minute or 0)
    if (hours < 24 and minutes < 60) or (hours, minutes) == (24, 0):
        return hours * 60 + minutes
    return None


def _weekdays(selector: str) -> set[int]:
    if selector in ("diariamente", "todos os dias"):
        return set(range(7))
    days = set()
    for item in re.split(r"\s*,\s*|\s+e\s+", selector):
        names = re.findall(rf"({'|'.join(WEEKDAYS)})", item)
        first, last = WEEKDAYS.index(names[0]), WEEKDAYS.index(names[-1])
        days.update((first + offset) % 7 for offset in range((last - first) % 7 + 1))
    return days


def _weekly_hours(text: object) -> dict[int, list[tuple[int, int]]] | None:
    """Read common Mapas Culturais schedules, omitting ambiguous text."""
    if not isinstance(text, str) or not text.strip():
        return None
    normalized = (
        "".join(
            char
            for char in unicodedata.normalize("NFD", text.lower())
            if not unicodedata.combining(char)
        )
        .replace("–", "-")
        .replace("—", "-")
    )
    normalized = re.sub(
        r"\b([2-6])ª\s*feira\b",
        lambda match: WEEKDAYS[int(match[1]) - 2],
        normalized,
    )
    clauses = [
        clause
        for raw in re.split(rf"[.;\n]+|\s+-\s+(?={DAY})", normalized.strip())
        if (clause := raw.strip(" -()\t"))
    ]
    if not clauses:
        return None
    hours: dict[int, list[tuple[int, int]]] = {}
    closed = set()
    for clause in clauses:
        if match := CLOSED_CLAUSE.fullmatch(clause):
            closed.update(_weekdays(match["days"] or match["closed_days"]))
            continue
        match = HOURS_CLAUSE.fullmatch(clause)
        if match is None:
            return None
        if not hours:
            hours = {index: [] for index in range(len(WEEKDAYS))}
        days = _weekdays(match["days"])
        for period in re.split(r"\s*,\s*|\s+e\s+(?:das\s+)?", match["hours"].strip()):
            interval = TIME_RANGE.fullmatch(period)
            if interval is None:
                return None
            begin, end = map(_clock_minutes, interval.groups())
            if begin is None or end is None or begin == 1440:
                return None
            if begin == end:
                if begin != 0:
                    return None
                continue  # Mapas Culturais uses 00:00–00:00 on closed days.
            for weekday in days:
                if end < begin:
                    hours[weekday].append((begin, 1440))
                    hours[(weekday + 1) % 7].append((0, end))
                else:
                    hours[weekday].append((begin, end))
    for weekday in closed:
        hours[weekday] = []
    return hours


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
