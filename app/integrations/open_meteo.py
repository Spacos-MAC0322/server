"""Daily Open-Meteo forecasts, fetched on demand and cached for three hours."""

import json
import math
from datetime import date as Date
from datetime import datetime
from typing import TypedDict

import httpx

from app.core.cache import ResponseCache, make_cache_key
from app.core.config import Settings, get_settings
from app.core.http_client import (
    ExternalAPIClient,
    ExternalAPIResponseError,
    create_http_client,
)

SERVICE = "open_meteo"
FORECAST_PATH = "/v1/forecast"
FORECAST_CACHE_TTL = 3 * 3600
_forecast_cache = ResponseCache(ttl=FORECAST_CACHE_TTL)
_FIELDS = {
    "temperature_min": "temperature_2m_min",
    "temperature_max": "temperature_2m_max",
    "weather_code": "weather_code",
    "precipitation_probability": "precipitation_probability_max",
}


class WeatherForecast(TypedDict):
    date: str
    temperature_min: float
    temperature_max: float
    weather_code: int
    precipitation_probability: float


def _normalize_forecast(data: object, day: str) -> WeatherForecast:
    try:
        daily = data["daily"]
        if daily["time"] != [day]:
            raise ValueError("Unexpected forecast date")
        values = {}
        for name, field in _FIELDS.items():
            series = daily[field]
            if not isinstance(series, list) or len(series) != 1:
                raise ValueError("Expected one daily value")
            value = series[0]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Expected a finite number")
            values[name] = value
        if (
            values["temperature_min"] > values["temperature_max"]
            or not 0 <= values["precipitation_probability"] <= 100
            or type(values["weather_code"]) is not int
            or values["weather_code"]
            not in {
                0,
                1,
                2,
                3,
                45,
                48,
                51,
                53,
                55,
                56,
                57,
                61,
                63,
                65,
                66,
                67,
                71,
                73,
                75,
                77,
                80,
                81,
                82,
                85,
                86,
                95,
                96,
                99,
            }
        ):
            raise ValueError("Invalid daily values")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ExternalAPIResponseError(
            SERVICE, "Forecast response is missing valid daily weather data"
        ) from error
    return {"date": day, **values}


class OpenMeteoClient:
    """Reuse the application's HTTP client; this adapter never closes it."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings | None = None,
        *,
        cache: ResponseCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache if cache is not None else _forecast_cache
        self.api = ExternalAPIClient(
            client,
            service=SERVICE,
            base_url=str(self.settings.open_meteo_base_url),
            timeout=self.settings.external_api_timeout_seconds,
        )

    async def get_weather_forecast(
        self, date: Date | str, lat: float, lng: float
    ) -> WeatherForecast:
        """Return Celsius, WMO code and maximum daily rain probability (0-100).

        `date` accepts a date object or YYYY-MM-DD. Daily boundaries use São Paulo
        time. Dates outside the provider's available range raise ExternalAPIError,
        as do network failures; callers can show weather as unavailable.
        """
        if not self.settings.open_meteo_enabled:
            raise ValueError("Enable OPEN_METEO_ENABLED before fetching weather")
        if isinstance(date, str):
            parsed = Date.fromisoformat(date)
            if date != parsed.isoformat():
                raise ValueError("date must use YYYY-MM-DD")
            date = parsed
        if not isinstance(date, Date) or isinstance(date, datetime):
            raise ValueError("date must be a date or YYYY-MM-DD string")
        for name, value, limit in (("lat", lat, 90), ("lng", lng, 180)):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not -limit <= value <= limit
            ):
                raise ValueError(f"{name} must be a finite coordinate within ±{limit}")
        day = date.isoformat()
        params = {
            "latitude": lat,
            "longitude": lng,
            "start_date": day,
            "end_date": day,
            "timezone": "America/Sao_Paulo",
            "temperature_unit": "celsius",
            "daily": ",".join(_FIELDS.values()),
        }
        url = f"{str(self.settings.open_meteo_base_url).rstrip('/')}{FORECAST_PATH}"
        key = make_cache_key(url, params)
        if (cached := self.cache.get(key)) is not None:
            return json.loads(cached)
        data = await self.api.get_json(FORECAST_PATH, params=params)
        forecast = _normalize_forecast(data, day)
        # Store only validated forecasts, so bad upstream responses can recover.
        self.cache.set(key, json.dumps(forecast).encode())
        return forecast


async def get_weather_forecast(
    date: Date | str,
    lat: float,
    lng: float,
    *,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> WeatherForecast:
    """Convenience entry point; pass the shared HTTP client inside the application.

    Example: await get_weather_forecast("2026-11-04", -23.55, -46.63).
    Requires OPEN_METEO_ENABLED=true. The optional client remains caller-owned.
    """
    if client is not None:
        return await OpenMeteoClient(client, settings).get_weather_forecast(
            date, lat, lng
        )
    async with create_http_client() as http:
        return await OpenMeteoClient(http, settings).get_weather_forecast(
            date, lat, lng
        )
