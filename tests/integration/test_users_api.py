"""The /api/users/me/favorites endpoints on in-memory MongoDB."""

from unittest.mock import AsyncMock

import pytest
from pymongo.errors import AutoReconnect

from app.core.database import ensure_indexes
from app.repositories.user_repository import UserRepository

ANA = {"name": "Ana", "email": "ana@example.com", "password": "senha-segura"}


def place_doc(identity: str, *, status: str = "published", name: str = "Museu"):
    return {
        "_id": identity,
        "name": name,
        "publication_status": status,
        "categories": ["Museu"],
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
        "source_keys": [f"test:{identity}"],
        "sources": [{"provider": "test", "external_id": identity}],
        "admission": {"is_free": True},
        "accessibility": {"wheelchair": True},
    }


@pytest.fixture(autouse=True)
async def indexes(db) -> None:
    await ensure_indexes(db)


async def auth_headers(client, **overrides) -> dict[str, str]:
    payload = ANA | overrides
    await client.post("/api/auth/register", json=payload)
    response = await client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_favorites_require_authentication(client):
    assert (await client.get("/api/users/me/favorites")).status_code == 401
    assert (await client.post("/api/users/me/favorites/p1")).status_code == 401
    assert (await client.delete("/api/users/me/favorites/p1")).status_code == 401


async def test_add_list_and_remove_favorites_populated_with_place_summary(client, db):
    headers = await auth_headers(client)
    await db.places.insert_many(
        [
            place_doc("mapas_saas:space:31", name="Casa de Cultura do Butantã"),
            place_doc("mapas_saas:space:42", name="Centro Cultural São Paulo"),
        ]
    )
    await db.place_curations.insert_one(
        {
            "_id": "c-31",
            "place_id": "mapas_saas:space:31",
            "hidden_gem": True,
            "family_friendly": True,
        }
    )

    initial = await client.get("/api/users/me/favorites", headers=headers)
    assert initial.status_code == 200
    assert initial.json() == []

    post1 = await client.post(
        "/api/users/me/favorites/mapas_saas%3Aspace%3A31", headers=headers
    )
    assert post1.status_code == 201
    created = post1.json()
    assert created == {
        "id": "mapas_saas:space:31",
        "name": "Casa de Cultura do Butantã",
        "primary_category": "Museu",
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
        "badges": {
            "is_free": True,
            "wheelchair": True,
            "pet_friendly": None,
            "family_friendly": True,
            "lgbtqia_inclusion": None,
            "hidden_gem": True,
        },
    }

    post2 = await client.post(
        "/api/users/me/favorites/mapas_saas%3Aspace%3A42", headers=headers
    )
    assert post2.status_code == 201

    listed = await client.get("/api/users/me/favorites", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [
        "mapas_saas:space:31",
        "mapas_saas:space:42",
    ]

    deleted = await client.delete(
        "/api/users/me/favorites/mapas_saas%3Aspace%3A31", headers=headers
    )
    assert deleted.status_code == 204
    assert deleted.content == b""

    listed_after = await client.get("/api/users/me/favorites", headers=headers)
    assert [item["id"] for item in listed_after.json()] == ["mapas_saas:space:42"]


async def test_add_favorite_returns_404_when_place_missing_or_unpublished(client, db):
    headers = await auth_headers(client)
    await db.places.insert_one(place_doc("review-place", status="review"))

    for pid in ("nao-existe", "review-place"):
        res = await client.post(f"/api/users/me/favorites/{pid}", headers=headers)
        assert res.status_code == 404
        assert res.json()["detail"] == "Espaço não encontrado."


async def test_favorites_return_503_on_database_error(client, monkeypatch):
    headers = await auth_headers(client)

    monkeypatch.setattr(
        UserRepository,
        "list_favorites",
        AsyncMock(side_effect=AutoReconnect("db down")),
    )
    assert (
        await client.get("/api/users/me/favorites", headers=headers)
    ).status_code == 503

    monkeypatch.setattr(
        UserRepository,
        "add_favorite",
        AsyncMock(side_effect=AutoReconnect("db down")),
    )
    assert (
        await client.post("/api/users/me/favorites/p1", headers=headers)
    ).status_code == 503

    monkeypatch.setattr(
        UserRepository,
        "remove_favorite",
        AsyncMock(side_effect=AutoReconnect("db down")),
    )
    assert (
        await client.delete("/api/users/me/favorites/p1", headers=headers)
    ).status_code == 503
