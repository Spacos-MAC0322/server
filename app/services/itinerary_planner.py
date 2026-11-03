"""Enrich itinerary reads with optional weather, independent of persistence."""

import asyncio
import logging
from datetime import date as Date
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.http_client import ExternalAPIError
from app.integrations.open_meteo import OpenMeteoClient
from app.repositories.itinerary_repository import ItineraryRepository
from app.schemas.itinerary import WeatherResponse

logger = logging.getLogger(__name__)
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
SAO_PAULO_LAT = -23.5505
SAO_PAULO_LNG = -46.6333
# Open-Meteo offers up to 16 calendar days, including today.
FORECAST_DAYS = 16
WEATHER_TIMEOUT_SECONDS = 5


def today_in_sao_paulo() -> Date:
    return datetime.now(SAO_PAULO).date()


class ItineraryPlanner:
    def __init__(self, weather_client: OpenMeteoClient):
        self.weather_client = weather_client

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
