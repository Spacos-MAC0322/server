"""Public places API against real MongoDB; external HTTP is always mocked."""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.database import get_database, initialize_database
from app.integrations.mapas_culturais import MapasCulturaisClient
from app.main import app
from app.services.sync_spaces import sync_spaces

pytestmark = pytest.mark.integration


@pytest.fixture
async def places_client(real_db):
    await initialize_database(real_db)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_database] = lambda: real_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def test_places_api_reads_public_pins_with_real_bounds_and_curation(
    real_db, places_client
):
    for identity, lon, status, located in (
        ("inside", -46.63, "published", True),
        ("outside", -46.8, "published", True),
        ("hidden", -46.63, "hidden", True),
        ("review", -46.63, "review", True),
        ("unknown", -46.63, "published", False),
    ):
        await real_db.places.insert_one(
            {
                "_id": identity,
                "name": identity,
                "publication_status": status,
                "source_keys": [f"test:{identity}"],
                "sources": [{"provider": "test", "external_id": identity}],
                "location": {"type": "Point", "coordinates": [lon, -23.55]}
                if located
                else None,
            }
        )
    await real_db.place_curations.insert_one(
        {
            "_id": "inside-curation",
            "place_id": "inside",
            "family_friendly": True,
        }
    )
    response = await places_client.get("/api/places")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == ["inside", "outside"]
    response = await places_client.get(
        "/api/places",
        params={
            "sw_lat": -23.6,
            "sw_lng": -46.7,
            "ne_lat": -23.5,
            "ne_lng": -46.5,
        },
    )
    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == ["inside"]
    assert response.json()[0]["badges"]["family_friendly"] is True
    assert response.json()[0]["badges"]["pet_friendly"] is None
    assert set(response.json()[0]) == {
        "id",
        "name",
        "primary_category",
        "location",
        "badges",
    }
    empty = await places_client.get(
        "/api/places",
        params={
            "sw_lat": -24,
            "sw_lng": -47.2,
            "ne_lat": -23.9,
            "ne_lng": -47.1,
        },
    )
    assert empty.status_code == 200
    assert empty.json() == []


async def test_mocked_adapter_to_database_to_public_endpoint(real_db, places_client):
    # A fresh, isolated database starts with an empty public listing.
    assert (await places_client.get("/api/places")).json() == []
    raw = {
        "id": 41,
        "name": "  Centro Cultural  ",
        "status": 1,
        "En_Municipio": "São Paulo",
        "type": {"name": "Centro cultural"},
        "location": {"longitude": "-46.63", "latitude": "-23.55"},
        "horario": None,
        "preco": None,
    }
    adapter = MapasCulturaisClient(
        Settings(
            _env_file=None,
            mapas_culturais_enabled=True,
            mapas_culturais_base_url="https://saas.mapas.cultura.gov.br",
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=[raw] if request.url.params["@page"] == "1" else [],
            )
        ),
    )
    for _ in range(2):
        result = await sync_spaces(real_db, client=adapter)
        assert result["status"] == "success"
    assert await real_db.places.count_documents({}) == 1
    assert (await places_client.get("/api/places")).json() == []  # imported as review
    stored = await real_db.places.find_one({})
    assert stored["opening_hours"]["text"] is None
    assert stored["admission"]["price_text"] is None
    # Publish only this test fixture, never the developer's real dataset.
    await real_db.places.update_one(
        {"_id": stored["_id"]}, {"$set": {"publication_status": "published"}}
    )
    response = await places_client.get(
        "/api/places",
        params={
            "sw_lat": -23.6,
            "sw_lng": -46.7,
            "ne_lat": -23.5,
            "ne_lng": -46.5,
        },
    )
    assert response.status_code == 200
    (pin,) = response.json()
    assert pin["id"] == "mapas_saas:space:41"
    assert pin["name"] == "Centro Cultural"
    assert pin["primary_category"] == "Centro cultural"
    assert pin["location"]["coordinates"] == [-46.63, -23.55]
    assert all(value is None for value in pin["badges"].values())
