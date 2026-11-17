from unittest.mock import AsyncMock

from pymongo.errors import AutoReconnect

from app.repositories.place_repository import PlaceRepository


def place(identity, **fields):
    return {
        "_id": identity,
        "name": f"Local {identity}",
        "publication_status": "published",
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
        **fields,
    }


async def test_discover_returns_only_eligible_places_and_interleaves_zones(client, db):
    await db.places.insert_many(
        [
            place("a-popular", region_zone="Centro"),
            place("b-east-independent", region_zone="Leste", is_independent=True),
            place("c-south", region_zone="Sul"),
            place(
                "d-east-community",
                region_zone="Leste",
                community_center=True,
                is_independent=True,
            ),
            place("hidden", is_independent=True, publication_status="hidden"),
            place("unlocated", is_independent=True, location=None),
            place(
                "invalid", is_independent=True, data_quality={"location_valid": False}
            ),
        ]
    )
    await db.place_curations.insert_one({"place_id": "c-south", "hidden_gem": True})

    response = await client.get("/api/search/discover")

    assert response.status_code == 200
    places = response.json()
    assert [result["id"] for result in places] == [
        "d-east-community",
        "c-south",
        "b-east-independent",
    ]
    assert places[1]["badges"]["hidden_gem"] is True
    assert "publication_status" not in places[0]


async def test_discover_returns_empty_array_without_curated_places(client, db):
    await db.places.insert_one(place("unscored", region_zone="Oeste"))

    response = await client.get("/api/search/discover")

    assert response.status_code == 200
    assert response.json() == []


async def test_discover_returns_friendly_error_when_database_is_unavailable(
    client, monkeypatch
):
    monkeypatch.setattr(
        PlaceRepository,
        "find_all",
        AsyncMock(side_effect=AutoReconnect("private database error")),
    )

    response = await client.get("/api/search/discover")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Não foi possível consultar a seleção de espaços no momento."
    }
