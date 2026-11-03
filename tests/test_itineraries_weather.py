import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pymongo.errors import AutoReconnect

from app.core.cache import ResponseCache
from app.core.config import Settings, get_settings
from app.core.http_client import (
    ExternalAPIConnectionError,
    ExternalAPIResponseError,
    ExternalAPIStatusError,
    ExternalAPITimeoutError,
    get_http_client,
)
from app.integrations import open_meteo
from app.integrations.open_meteo import OpenMeteoClient
from app.main import app
from app.repositories.itinerary_repository import ItineraryRepository
from app.services import itinerary_planner
from app.services.itinerary_planner import ItineraryPlanner

TODAY = date(2026, 11, 3)


@pytest.fixture
async def weather_api(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "daily": {
                    "time": [request.url.params["start_date"]],
                    "temperature_2m_min": [18.5],
                    "temperature_2m_max": [27.2],
                    "weather_code": [3],
                    "precipitation_probability_max": [40],
                }
            },
        )

    monkeypatch.setattr(itinerary_planner, "today_in_sao_paulo", lambda: TODAY)
    monkeypatch.setattr(open_meteo, "_forecast_cache", ResponseCache(ttl=10800))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app.dependency_overrides[get_http_client] = lambda: http
        app.dependency_overrides[get_settings] = lambda: Settings(
            open_meteo_enabled=True
        )
        try:
            yield requests
        finally:
            app.dependency_overrides.pop(get_http_client, None)
            app.dependency_overrides.pop(get_settings, None)


@pytest.mark.parametrize("offset", [0, 1, 15])
async def test_weather_within_window(client, weather_api, offset):
    day = (TODAY + timedelta(days=offset)).isoformat()
    response = await client.get("/api/itineraries/weather", params={"date": day})
    assert response.status_code == 200
    assert response.json() == {
        "weather_forecast": {
            "date": day,
            "temperature_min": 18.5,
            "temperature_max": 27.2,
            "weather_code": 3,
            "precipitation_probability": 40,
        },
        "weather_warning": None,
    }
    assert len(weather_api) == 1
    params = weather_api[0].url.params
    assert params["latitude"] == "-23.5505"
    assert params["longitude"] == "-46.6333"
    assert params["timezone"] == "America/Sao_Paulo"


@pytest.mark.parametrize("offset", [-1, 16, 365])
async def test_out_of_window_skips_external_api(client, weather_api, offset):
    response = await client.get(
        "/api/itineraries/weather",
        params={"date": (TODAY + timedelta(days=offset)).isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["weather_forecast"] is None
    assert "janela" in response.json()["weather_warning"]
    assert weather_api == []


@pytest.mark.parametrize(
    "day",
    [None, "invalid", "2026-02-30", "20261103", "2026-11-03T00:00:00Z", "2026-1-3"],
)
async def test_invalid_date_returns_422(client, weather_api, day):
    response = await client.get(
        "/api/itineraries/weather", params={} if day is None else {"date": day}
    )
    assert response.status_code == 422
    assert weather_api == []


async def test_disabled_feature_is_optional(client, weather_api):
    app.dependency_overrides[get_settings] = lambda: Settings()
    response = await client.get("/api/itineraries/weather", params={"date": str(TODAY)})
    assert response.status_code == 200
    assert response.json()["weather_forecast"] is None
    assert response.json()["weather_warning"]
    assert weather_api == []


@pytest.mark.parametrize(
    "error_type",
    [
        ExternalAPIConnectionError,
        ExternalAPIResponseError,
        ExternalAPIStatusError,
        ExternalAPITimeoutError,
    ],
)
async def test_failure_preserves_weather_and_itinerary_routes(
    client, db, weather_api, monkeypatch, error_type, caplog
):
    monkeypatch.setattr(
        OpenMeteoClient,
        "get_weather_forecast",
        AsyncMock(side_effect=error_type("open_meteo", "secret upstream details")),
    )
    await db.itineraries.insert_one(
        {
            "_id": "tour",
            "user_id": "owner",
            "title": "Passeio",
            "date": str(TODAY),
            "stops": [{"place_id": "museum", "order": 1}],
        }
    )
    for path in [f"/api/itineraries/weather?date={TODAY}", "/api/itineraries/tour"]:
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json()["weather_forecast"] is None
        assert response.json()["weather_warning"]
        assert "secret" not in response.text
    assert response.json()["stops"] == [{"place_id": "museum", "order": 1}]
    assert "secret" not in caplog.text


async def test_slow_api_has_short_deadline(client, weather_api, monkeypatch):
    async def slow(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(OpenMeteoClient, "get_weather_forecast", slow)
    monkeypatch.setattr(itinerary_planner, "WEATHER_TIMEOUT_SECONDS", 0.001)
    response = await client.get(f"/api/itineraries/weather?date={TODAY}")
    assert response.status_code == 200
    assert response.json()["weather_forecast"] is None
    assert response.json()["weather_warning"]


async def test_itinerary_enrichment_reuses_cache_and_does_not_write(
    client, db, weather_api
):
    itinerary = {
        "_id": "tour",
        "user_id": "owner",
        "title": "Passeio",
        "date": str(TODAY),
        "stops": [{"place_id": "museum", "order": 1}],
        "preferences": {"budget": 50},
    }
    await db.itineraries.insert_one(itinerary)
    forecast = (await client.get(f"/api/itineraries/weather?date={TODAY}")).json()
    response = await client.get("/api/itineraries/tour")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "tour"
    assert payload["title"] == "Passeio"
    assert payload["stops"] == itinerary["stops"]
    assert payload["preferences"] == itinerary["preferences"]
    assert payload["weather_forecast"] == forecast["weather_forecast"]
    assert payload["weather_warning"] is None
    assert "user_id" not in payload
    assert len(weather_api) == 1
    assert await db.itineraries.find_one({"_id": "tour"}) == itinerary


@pytest.mark.parametrize("day", [None, "invalid", "2027-01-01", "2026-11-02"])
async def test_itinerary_without_forecast_keeps_stops(client, db, weather_api, day):
    await db.itineraries.insert_one(
        {
            "_id": "tour",
            "user_id": "owner",
            "title": "Passeio",
            "date": day,
            "stops": [],
            "weather_forecast": {"outdated": True},
        }
    )
    response = await client.get("/api/itineraries/tour")
    assert response.status_code == 200
    assert response.json()["stops"] == []
    assert response.json()["weather_forecast"] is None
    assert response.json()["weather_warning"]
    assert weather_api == []


async def test_missing_itinerary_returns_404_without_fetching_weather(
    client, weather_api
):
    response = await client.get("/api/itineraries/missing")
    assert response.status_code == 404
    assert weather_api == []


async def test_database_error_returns_503(client, weather_api, monkeypatch):
    monkeypatch.setattr(
        ItineraryRepository,
        "find_by_id",
        AsyncMock(side_effect=AutoReconnect("secret database details")),
    )
    response = await client.get("/api/itineraries/tour")
    assert response.status_code == 503
    assert "secret" not in response.text
    assert weather_api == []


async def test_enrichment_does_not_mutate_input():
    client = AsyncMock()
    planner = ItineraryPlanner(client)
    itinerary = {"title": "Undated", "stops": [], "weather_forecast": "stale"}
    result = await planner.attach_weather_forecast(itinerary)
    assert result["weather_forecast"] is None
    assert itinerary["weather_forecast"] == "stale"
    client.get_weather_forecast.assert_not_awaited()


async def test_weather_does_not_require_database(weather_api):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get(f"/api/itineraries/weather?date={TODAY}")
    assert response.status_code == 200
    assert response.json()["weather_forecast"] is not None


def test_openapi_exposes_weather_and_itinerary_read_contracts():
    paths = app.openapi()["paths"]
    weather = paths["/api/itineraries/weather"]["get"]
    assert [parameter["name"] for parameter in weather["parameters"]] == ["date"]
    assert weather["parameters"][0]["required"] is True
    assert weather["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/WeatherResponse")
    itinerary = paths["/api/itineraries/{itinerary_id}"]["get"]
    assert {"200", "404", "503"} <= itinerary["responses"].keys()
