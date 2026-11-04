import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.http_client import ExternalAPIStatusError, get_http_client
from app.integrations.sptrans import SPTransClient
from app.main import app
from app.services import itinerary_planner

PARAMS = {
    "from_lat": -23.592938,
    "from_lng": -46.672727,
    "to_lat": -23.5600,
    "to_lng": -46.6550,
}
CONNECTION = {
    "route_id": "6500-10",
    "line": "6500-10",
    "headsign": "Terminal Bandeira",
    "direction_id": "0",
    "estimated_duration_minutes": 27.8,
}


@pytest.fixture
async def transit_api():
    def unexpected_request(request):
        pytest.fail("Transit estimates must not query Olho Vivo or Open-Meteo")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as http:
        app.dependency_overrides[get_http_client] = lambda: http
        app.dependency_overrides[get_settings] = lambda: Settings()
        try:
            # No lifespan or database dependency: the transit route needs neither.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as backend:
                yield backend
        finally:
            app.dependency_overrides.pop(get_http_client, None)
            app.dependency_overrides.pop(get_settings, None)


async def test_transit_returns_typed_leg_and_preserves_coordinate_order(
    transit_api, monkeypatch
):
    query = AsyncMock(return_value=[CONNECTION])
    monkeypatch.setattr(SPTransClient, "find_connections", query)
    response = await transit_api.get("/api/itineraries/transit", params=PARAMS)
    assert response.status_code == 200
    assert response.json() == {
        "from_place_id": "origin",
        "to_place_id": "destination",
        "suggested_lines": [
            {
                "route_id": "6500-10",
                "code": "6500-10",
                "headsign": "Terminal Bandeira",
                "direction_id": "0",
            }
        ],
        "estimated_duration_min": 28,
    }
    assert query.await_args.args == (-23.592938, -46.672727, -23.56, -46.655)
    assert query.await_args.kwargs["include_live"] is False
    assert query.await_args.kwargs["departure_at"].tzinfo is not None


@pytest.mark.parametrize("missing", list(PARAMS))
async def test_each_coordinate_is_required(transit_api, monkeypatch, missing):
    query = AsyncMock()
    monkeypatch.setattr(SPTransClient, "find_connections", query)
    params = {key: value for key, value in PARAMS.items() if key != missing}
    response = await transit_api.get("/api/itineraries/transit", params=params)
    assert response.status_code == 422
    query.assert_not_awaited()


@pytest.mark.parametrize(
    "key,value",
    [
        ("from_lat", -91),
        ("from_lat", 91),
        ("from_lng", -181),
        ("from_lng", 181),
        ("to_lat", -91),
        ("to_lat", 91),
        ("to_lng", -181),
        ("to_lng", 181),
        ("from_lat", "nan"),
        ("from_lng", "inf"),
        ("to_lat", "-inf"),
        ("to_lng", "NaN"),
        ("from_lat", "invalid"),
        ("to_lng", ""),
    ],
)
async def test_invalid_coordinates_return_422_without_transit_query(
    transit_api, monkeypatch, key, value
):
    query = AsyncMock()
    monkeypatch.setattr(SPTransClient, "find_connections", query)
    response = await transit_api.get(
        "/api/itineraries/transit", params={**PARAMS, key: value}
    )
    assert response.status_code == 422
    query.assert_not_awaited()


@pytest.mark.parametrize("point", [(0, 0), (90, 180), (-90, -180)])
async def test_same_coordinates_return_zero_without_bus_query(
    transit_api, monkeypatch, point
):
    query = AsyncMock()
    monkeypatch.setattr(SPTransClient, "find_connections", query)
    lat, lng = point
    response = await transit_api.get(
        "/api/itineraries/transit",
        params={
            "from_lat": lat,
            "from_lng": lng,
            "to_lat": lat,
            "to_lng": lng,
        },
    )
    assert response.status_code == 200
    assert response.json()["suggested_lines"] == []
    assert response.json()["estimated_duration_min"] == 0
    query.assert_not_awaited()


async def test_no_connection_returns_walking_estimate(transit_api, monkeypatch):
    monkeypatch.setattr(SPTransClient, "find_connections", AsyncMock(return_value=[]))
    response = await transit_api.get(
        "/api/itineraries/transit",
        params={
            "from_lat": 0,
            "from_lng": 0,
            "to_lat": 0,
            "to_lng": 0.01,
        },
    )
    assert response.status_code == 200
    assert response.json()["suggested_lines"] == []
    assert response.json()["estimated_duration_min"] == 19


async def test_missing_gtfs_configuration_keeps_route_available(transit_api):
    # The real service and adapter handle the disabled/unconfigured feed.
    response = await transit_api.get(
        "/api/itineraries/transit",
        params={
            "from_lat": 0,
            "from_lng": 0,
            "to_lat": 0,
            "to_lng": 0.01,
        },
    )
    assert response.status_code == 200
    assert response.json()["suggested_lines"] == []
    assert response.json()["estimated_duration_min"] == 19


async def test_transit_failure_preserves_response_without_exposing_details(
    transit_api, monkeypatch
):
    monkeypatch.setattr(
        SPTransClient,
        "find_connections",
        AsyncMock(
            side_effect=ExternalAPIStatusError(
                "sptrans", "secret upstream details", status_code=503
            )
        ),
    )
    response = await transit_api.get("/api/itineraries/transit", params=PARAMS)
    assert response.status_code == 200
    assert response.json()["suggested_lines"] == []
    assert response.json()["estimated_duration_min"] > 0
    assert "secret" not in response.text


async def test_slow_transit_query_uses_walking_fallback(transit_api, monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(SPTransClient, "find_connections", slow)
    monkeypatch.setattr(itinerary_planner, "TRANSIT_TIMEOUT_SECONDS", 0.001)
    response = await transit_api.get("/api/itineraries/transit", params=PARAMS)
    assert response.status_code == 200
    assert response.json()["suggested_lines"] == []
    assert response.json()["estimated_duration_min"] > 0


def test_openapi_documents_required_coordinates_and_typed_response():
    operation = app.openapi()["paths"]["/api/itineraries/transit"]["get"]
    params = operation["parameters"]
    assert {param["name"] for param in params} == set(PARAMS)
    for param in params:
        assert param["required"] is True
        limit = 90 if param["name"].endswith("lat") else 180
        assert param["schema"]["minimum"] == -limit
        assert param["schema"]["maximum"] == limit
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/TransitLegSchema")
    assert "422" in operation["responses"]
