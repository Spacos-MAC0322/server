from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import httpx
import pytest

from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.http_client import (
    ExternalAPIConnectionError,
    ExternalAPIResponseError,
    ExternalAPIStatusError,
    ExternalAPITimeoutError,
)
from app.integrations import open_meteo
from app.integrations.open_meteo import OpenMeteoClient, get_weather_forecast

DAY = "2026-11-04"
LAT, LNG = -23.55, -46.63
PAYLOAD = {
    "daily": {
        "time": [DAY],
        "temperature_2m_min": [17.2],
        "temperature_2m_max": [28.5],
        "weather_code": [61],
        "precipitation_probability_max": [80],
    }
}
EXPECTED = {
    "date": DAY,
    "temperature_min": 17.2,
    "temperature_max": 28.5,
    "weather_code": 61,
    "precipitation_probability": 80,
}


@pytest.fixture
async def adapter():
    clients = []

    def build(handler, **kwargs):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        kwargs.setdefault("settings", Settings(open_meteo_enabled=True))
        kwargs.setdefault("cache", ResponseCache(ttl=3 * 3600))
        result = OpenMeteoClient(http, **kwargs)
        result.api._retry = replace(result.api._retry, max_attempts=1)
        return result

    yield build
    for http in clients:
        await http.aclose()


@pytest.mark.parametrize("day", [DAY, date.fromisoformat(DAY)])
async def test_daily_forecast_and_request(adapter, day):
    def handler(request):
        assert str(request.url.copy_with(query=None)) == (
            "https://api.open-meteo.com/v1/forecast"
        )
        assert dict(request.url.params) == {
            "latitude": str(LAT),
            "longitude": str(LNG),
            "start_date": DAY,
            "end_date": DAY,
            "timezone": "America/Sao_Paulo",
            "temperature_unit": "celsius",
            "daily": (
                "temperature_2m_min,temperature_2m_max,weather_code,"
                "precipitation_probability_max"
            ),
        }
        assert request.extensions["timeout"]["read"] == 10
        return httpx.Response(200, json=PAYLOAD)

    assert await adapter(handler).get_weather_forecast(day, LAT, LNG) == EXPECTED


async def test_cache_is_private_and_expires(adapter, clock):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=PAYLOAD)

    client = adapter(handler, cache=ResponseCache(ttl=10800, timer=clock))
    first = await client.get_weather_forecast(DAY, LAT, LNG)
    first["temperature_min"] = -999
    clock.now = 10799
    assert await client.get_weather_forecast(DAY, LAT, LNG) == EXPECTED
    assert len(calls) == 1
    clock.now = 10800
    assert await client.get_weather_forecast(DAY, LAT, LNG) == EXPECTED
    assert len(calls) == 2


async def test_cache_keys_include_date_coordinates_and_provider(adapter):
    calls = []

    def handler(request):
        calls.append(request)
        body = deepcopy(PAYLOAD)
        body["daily"]["time"] = [request.url.params["start_date"]]
        return httpx.Response(200, json=body)

    cache = ResponseCache(ttl=10800)
    client = adapter(handler, cache=cache)
    await client.get_weather_forecast(DAY, LAT, LNG)
    await client.get_weather_forecast("2026-11-05", LAT, LNG)
    await client.get_weather_forecast(DAY, -23.6, LNG)
    await client.get_weather_forecast(DAY, LAT, -46.7)
    other = adapter(
        handler,
        cache=cache,
        settings=Settings(
            open_meteo_enabled=True, open_meteo_base_url="https://test.io"
        ),
    )
    await other.get_weather_forecast(DAY, LAT, LNG)
    assert len(calls) == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("time", []),
        ("time", ["2026-11-05"]),
        ("temperature_2m_min", []),
        ("temperature_2m_min", [None]),
        ("temperature_2m_min", [40]),
        ("temperature_2m_max", ["28"]),
        ("temperature_2m_max", [True]),
        ("weather_code", [999]),
        ("weather_code", [61.5]),
        ("precipitation_probability_max", [-1]),
        ("precipitation_probability_max", [101]),
        ("precipitation_probability_max", [10, 20]),
    ],
)
async def test_invalid_values_are_not_cached(adapter, field, value):
    body = deepcopy(PAYLOAD)
    body["daily"][field] = value
    client = adapter(lambda r: httpx.Response(200, json=body))
    with pytest.raises(ExternalAPIResponseError, match="open_meteo"):
        await client.get_weather_forecast(DAY, LAT, LNG)
    body["daily"][field] = PAYLOAD["daily"][field]
    assert await client.get_weather_forecast(DAY, LAT, LNG) == EXPECTED


@pytest.mark.parametrize("body", [None, [], {}, {"daily": None}, {"daily": {}}])
async def test_missing_daily_data(adapter, body):
    client = adapter(lambda r: httpx.Response(200, json=body))
    with pytest.raises(ExternalAPIResponseError):
        await client.get_weather_forecast(DAY, LAT, LNG)


@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"daily":{"time":["2026-11-04"],"temperature_2m_min":[NaN]}}'],
)
async def test_invalid_json_or_non_finite_value(adapter, body):
    client = adapter(lambda r: httpx.Response(200, content=body))
    with pytest.raises(ExternalAPIResponseError):
        await client.get_weather_forecast(DAY, LAT, LNG)


@pytest.mark.parametrize("status", [400, 429, 503])
async def test_http_failure(adapter, status):
    client = adapter(lambda r: httpx.Response(status))
    with pytest.raises(ExternalAPIStatusError) as caught:
        await client.get_weather_forecast(DAY, LAT, LNG)
    assert caught.value.status_code == status
    assert caught.value.service == "open_meteo"


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ReadTimeout("timeout"), ExternalAPITimeoutError),
        (httpx.ConnectError("offline"), ExternalAPIConnectionError),
    ],
)
async def test_network_failure(adapter, error, expected):
    def handler(request):
        raise error

    with pytest.raises(expected, match="open_meteo"):
        await adapter(handler).get_weather_forecast(DAY, LAT, LNG)


@pytest.mark.parametrize(
    "day,lat,lng",
    [
        ("invalid", LAT, LNG),
        ("20261104", LAT, LNG),
        (None, LAT, LNG),
        (datetime(2026, 11, 4), LAT, LNG),
        (DAY, 91, LNG),
        (DAY, LAT, -181),
        (DAY, float("nan"), LNG),
        (DAY, LAT, float("inf")),
        (DAY, True, LNG),
        (DAY, LAT, "-46.63"),
    ],
)
async def test_invalid_inputs_do_not_call_upstream(adapter, day, lat, lng):
    def handler(request):
        pytest.fail("Invalid input must not reach the API")

    with pytest.raises(ValueError):
        await adapter(handler).get_weather_forecast(day, lat, lng)


async def test_disabled_integration(adapter):
    def handler(request):
        pytest.fail("Disabled integration must not reach the API")

    with pytest.raises(ValueError, match="OPEN_METEO_ENABLED"):
        await adapter(handler, settings=Settings()).get_weather_forecast(DAY, LAT, LNG)


async def test_convenience_entry_point_and_client_ownership(monkeypatch):
    monkeypatch.setattr(open_meteo, "_forecast_cache", ResponseCache(ttl=10800))
    settings = Settings(open_meteo_enabled=True)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD))
    )
    async with http:
        assert (
            await get_weather_forecast(DAY, LAT, LNG, client=http, settings=settings)
            == EXPECTED
        )
        assert not http.is_closed
    owned = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD))
    )
    monkeypatch.setattr(open_meteo, "create_http_client", lambda: owned)
    assert await get_weather_forecast(DAY, LAT, LNG, settings=settings) == EXPECTED
    assert owned.is_closed
