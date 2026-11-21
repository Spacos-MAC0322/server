from unittest.mock import AsyncMock

import pytest
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


async def test_discover_includes_neighborhood_and_short_description(client, db):
    await db.places.insert_many(
        [
            place(
                "with-texts",
                is_independent=True,
                address={"full": "Rua A, 1", "neighborhood": "Butantã"},
                description={"short": "Oficinas e saraus.", "long": "Texto longo."},
            ),
            place("without-texts", is_independent=True, address=None),
        ]
    )

    response = await client.get("/api/search/discover")

    assert response.status_code == 200
    cards = {result["id"]: result for result in response.json()}
    assert cards["with-texts"]["neighborhood"] == "Butantã"
    assert cards["with-texts"]["short_description"] == "Oficinas e saraus."
    assert "address" not in cards["with-texts"]
    assert "description" not in cards["with-texts"]
    assert cards["without-texts"]["neighborhood"] is None
    assert cards["without-texts"]["short_description"] is None


async def test_map_pins_keep_the_lean_summary(client, db):
    await db.places.insert_one(
        place(
            "pin",
            address={"neighborhood": "Butantã"},
            description={"short": "Oficinas e saraus."},
        )
    )

    response = await client.get("/api/places")

    assert response.status_code == 200
    (pin,) = response.json()
    assert "neighborhood" not in pin
    assert "short_description" not in pin


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


async def test_search_combines_repeated_list_parameters(client, db):
    await db.places.insert_many(
        [
            place(
                "museu-sul",
                name="Museu do Ipiranga",
                categories=["Museu Público"],
                region_zone="Sul",
                address={"neighborhood": "Ipiranga"},
                description={"short": "Acervo histórico."},
            ),
            place("cine-leste", categories=["Sala de cinema"], region_zone="Leste"),
            place("teatro-sul", categories=["Teatro Público"], region_zone="Sul"),
            place("museu-norte", categories=["Museu"], region_zone="Norte"),
        ]
    )

    response = await client.get(
        "/api/search?categories=arte&categories=cinema&regions=Sul&regions=Leste"
    )

    assert response.status_code == 200
    results = response.json()
    assert [result["id"] for result in results] == ["cine-leste", "museu-sul"]
    museu = results[1]
    assert museu["neighborhood"] == "Ipiranga"
    assert museu["short_description"] == "Acervo histórico."
    assert museu["location"]["coordinates"] == [-46.63, -23.55]
    assert "publication_status" not in museu


async def test_search_passes_the_map_area_to_the_repository(client, monkeypatch):
    lookup = AsyncMock(return_value=[place("perto")])
    monkeypatch.setattr(PlaceRepository, "find_matching", lookup)

    near = await client.get("/api/search?lat=-23.55&lng=-46.63&radius_km=2&q=casa")
    bounds = await client.get(
        "/api/search?sw_lat=-23.6&sw_lng=-46.7&ne_lat=-23.5&ne_lng=-46.6"
    )

    assert near.status_code == bounds.status_code == 200
    assert [result["id"] for result in near.json()] == ["perto"]
    assert lookup.await_args_list[0].kwargs == {
        "bounds": None,
        "near": (-23.55, -46.63, 2.0),
    }
    assert lookup.await_args_list[1].kwargs == {
        "bounds": (-23.6, -46.7, -23.5, -46.6),
        "near": None,
    }


async def test_filter_options_with_group_counts_and_known_prices(client, db):
    await db.places.insert_many(
        [
            place("a", categories=["Biblioteca Pública"]),
            place("b", categories=["Ponto de Leitura"], publication_status="review"),
        ]
    )

    response = await client.get("/api/search/filters")

    assert response.status_code == 200
    body = response.json()
    assert body["priced_places"] == 0
    groups = {group["id"]: group for group in body["groups"]}
    assert groups["leitura"] == {
        "id": "leitura",
        "label": "Bibliotecas e leitura",
        "count": 1,
    }
    assert groups["parques"]["count"] == 0


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("categories=arte&categories=nao-existe", "nao-existe"),
        ("lat=-23.5&lng=-46.6", "radius_km"),
        ("regions=Zona%20Sul", "regions"),
        ("max_price=-5", "max_price"),
        ("sw_lat=-23.6&sw_lng=-46.7", "juntos"),
    ],
)
async def test_search_rejects_invalid_filters(client, query, message):
    response = await client.get(f"/api/search?{query}")

    assert response.status_code == 422
    assert message in str(response.json()["detail"])


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/api/search", "Não foi possível buscar os espaços no momento."),
        (
            "/api/search/filters",
            "Não foi possível consultar as opções de filtro no momento.",
        ),
    ],
)
async def test_search_returns_friendly_error_when_database_is_unavailable(
    client, monkeypatch, path, detail
):
    monkeypatch.setattr(
        PlaceRepository,
        "find_matching",
        AsyncMock(side_effect=AutoReconnect("mongodb://private-database")),
    )

    response = await client.get(path)

    assert response.status_code == 503
    assert response.json() == {"detail": detail}
