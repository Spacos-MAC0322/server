from unittest.mock import AsyncMock

import pytest
from pymongo.errors import AutoReconnect

from app.main import app
from app.repositories.place_repository import PlaceRepository
from app.schemas.place import PlaceListQuery


def place(identity="pin", *, status="published", location=True):
    return {
        "_id": identity,
        "name": f"Local {identity}",
        "publication_status": status,
        "categories": ["Museu"],
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]}
        if location
        else None,
        "source_keys": [f"test:{identity}"],
        "sources": [{"provider": "test", "external_id": identity}],
        "admission": {"is_free": False},
        "accessibility": {"wheelchair": True},
    }


async def test_empty_listing_returns_json_array(client):
    response = await client.get("/api/places")
    assert response.status_code == 200
    assert response.json() == []


async def test_listing_returns_only_published_located_places_with_badges(client, db):
    records = [
        place("a"),
        place("b"),
        place("review", status="review"),
        place("hidden", status="hidden"),
        place("archived", status="archived"),
        place("unlocated", location=False),
        dict(place("badpoint"), data_quality={"location_valid": False}),
    ]
    missing = place("missing-status")
    missing.pop("publication_status")
    records.append(missing)
    await db.places.insert_many(records)
    await db.place_curations.insert_one(
        {
            "_id": "curation-a",
            "place_id": "a",
            "pet_friendly": True,
            "family_friendly": False,
            "evidence": [{"internal": "omit"}],
        }
    )
    response = await client.get(
        "/api/places", headers={"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert [row["id"] for row in payload] == ["a", "b"]
    assert set(payload[0]) == {"id", "name", "primary_category", "location", "badges"}
    assert payload[0]["location"]["coordinates"] == [-46.63, -23.55]
    assert payload[0]["primary_category"] == "Museu"
    assert payload[0]["badges"]["pet_friendly"] is True
    assert payload[0]["badges"]["family_friendly"] is False
    assert payload[0]["badges"]["is_free"] is False
    assert payload[0]["badges"]["wheelchair"] is True
    assert payload[1]["badges"]["pet_friendly"] is None
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_all_bounds_select_repository_geospatial_method(client, monkeypatch):
    method = AsyncMock(return_value=[place(), place("hidden", status="hidden")])
    monkeypatch.setattr(PlaceRepository, "find_in_bounds", method)
    response = await client.get(
        "/api/places",
        params={
            "sw_lat": -23.6,
            "sw_lng": -46.7,
            "ne_lat": -23.5,
            "ne_lng": -46.5,
        },
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["pin"]
    method.assert_awaited_once_with(-23.6, -46.7, -23.5, -46.5)


@pytest.mark.parametrize(
    "params",
    [
        {"sw_lat": -23.6},
        {"sw_lat": -23.6, "sw_lng": -46.7, "ne_lat": -23.5},
        {"sw_lat": "not-a-number", "sw_lng": -46.7, "ne_lat": -23.5, "ne_lng": -46.5},
        {"sw_lat": "nan", "sw_lng": -46.7, "ne_lat": -23.5, "ne_lng": -46.5},
        {"sw_lat": -23.6, "sw_lng": "inf", "ne_lat": -23.5, "ne_lng": -46.5},
        {"sw_lat": -91, "sw_lng": -46.7, "ne_lat": -23.5, "ne_lng": -46.5},
        {"sw_lat": -23.6, "sw_lng": -181, "ne_lat": -23.5, "ne_lng": -46.5},
        {"sw_lat": -23.5, "sw_lng": -46.7, "ne_lat": -23.6, "ne_lng": -46.5},
        {"sw_lat": -23.6, "sw_lng": -46.5, "ne_lat": -23.5, "ne_lng": -46.7},
        {"sw_lat": -23.5, "sw_lng": -46.7, "ne_lat": -23.5, "ne_lng": -46.5},
        {"sw_lat": -23.6, "sw_lng": -170, "ne_lat": -23.5, "ne_lng": 170},
        {"sw_lat": "", "sw_lng": -46.7, "ne_lat": -23.5, "ne_lng": -46.5},
    ],
)
async def test_invalid_bounds_return_422_without_querying_places(
    client, monkeypatch, params
):
    method = AsyncMock()
    monkeypatch.setattr(PlaceRepository, "find_in_bounds", method)
    response = await client.get("/api/places", params=params)
    assert response.status_code == 422
    assert "detail" in response.json()
    method.assert_not_awaited()


async def test_database_failure_returns_503_without_connection_details(
    client, monkeypatch
):
    monkeypatch.setattr(
        PlaceRepository,
        "find_all",
        AsyncMock(
            side_effect=AutoReconnect("mongodb://user:secret@private-host"),
        ),
    )
    response = await client.get("/api/places")
    assert response.status_code == 503
    assert "secret" not in response.text
    assert "private-host" not in response.text


async def test_empty_curation_batch_avoids_query(db, monkeypatch):
    method = AsyncMock()
    repository = PlaceRepository(db)
    monkeypatch.setattr(repository.curations, "find", method)
    assert await repository.find_curations([]) == {}
    method.assert_not_called()


def test_zero_coordinates_and_openapi_contract():
    query = PlaceListQuery(sw_lat=0, sw_lng=0, ne_lat=1, ne_lng=1)
    assert query.bounds == (0, 0, 1, 1)
    operation = app.openapi()["paths"]["/api/places"]["get"]
    assert {p["name"] for p in operation["parameters"]} == {
        "sw_lat",
        "sw_lng",
        "ne_lat",
        "ne_lng",
    }
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("/PlaceSummarySchema")
    assert "503" in operation["responses"]
