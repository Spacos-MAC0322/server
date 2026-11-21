"""Filter combinations of GET /api/search against real MongoDB (#61).

Real MongoDB covers what mongomock cannot: map area and radius combined with
the other filters, and the regex engine used for accents and categories.
"""

import pytest

from . import test_places_api

pytestmark = pytest.mark.integration
places_client = test_places_api.places_client

# Around Praça da Sé; "far" places sit about 9 km east.
CENTER_LAT, CENTER_LNG = -23.5503, -46.6339


def space(identity, name, *, lng=CENTER_LNG, lat=CENTER_LAT, **fields):
    return {
        "_id": identity,
        "name": name,
        "source_keys": [identity],
        "sources": [{"provider": "test", "external_id": identity}],
        "publication_status": "published",
        "location": {"type": "Point", "coordinates": [lng, lat]},
        **fields,
    }


FREE = {"is_free": True, "price_min": 0, "price_max": 0, "currency": "BRL"}


def paid(low, high=None):
    return {"is_free": False, "price_min": low, "price_max": high or low}


@pytest.fixture
async def city(real_db):
    await real_db.places.insert_many(
        [
            space(
                "museu-centro-gratis",
                "Museu da Língua",
                categories=["Museu Público"],
                region_zone="Centro",
                admission=FREE,
                address={"neighborhood": "Luz"},
            ),
            space(
                "galeria-centro-paga",
                "Galeria Paulistana",
                lng=-46.6345,
                categories=["Galeria de arte"],
                region_zone="Centro",
                admission=paid(30),
            ),
            space(
                "teatro-centro-gratis",
                "Teatro Municipal",
                lng=-46.6380,
                categories=["Teatro Público"],
                region_zone="Centro",
                admission=FREE,
            ),
            space(
                "ponto-leste-barato",
                "Ponto de Cultura São Mateus",
                lng=-46.55,
                categories=["Ponto de Cultura"],
                region_zone="Leste",
                territory={"subprefecture": "São Mateus", "region": "Leste"},
                admission=paid(10, 20),
            ),
            space(
                "biblioteca-preco-desconhecido",
                "Biblioteca Mário de Andrade",
                lng=-46.6420,
                categories=["Biblioteca Pública"],
                region_zone="Centro",
                admission={"price_text": "Consulte"},
            ),
            space(
                "museu-oculto",
                "Museu em revisão",
                categories=["Museu"],
                region_zone="Centro",
                admission=FREE,
                publication_status="review",
            ),
        ]
    )


async def ids(client, query):
    response = await client.get(f"/api/search?{query}")
    assert response.status_code == 200, response.text
    return [place["id"] for place in response.json()]


async def test_art_in_the_center_with_free_entry(places_client, city):
    assert await ids(places_client, "categories=arte&regions=Centro&is_free=true") == [
        "museu-centro-gratis"
    ]


async def test_groups_and_zones_are_alternatives_within_each_list(places_client, city):
    found = await ids(
        places_client,
        "categories=arte&categories=teatro&regions=Centro&regions=Leste",
    )
    assert sorted(found) == [
        "galeria-centro-paga",
        "museu-centro-gratis",
        "ponto-leste-barato",
        "teatro-centro-gratis",
    ]


async def test_budget_includes_free_places_and_skips_unknown_prices(
    places_client, city
):
    assert sorted(await ids(places_client, "max_price=15")) == [
        "museu-centro-gratis",
        "ponto-leste-barato",
        "teatro-centro-gratis",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sao mateus", ["ponto-leste-barato"]),
        ("SÃO", ["ponto-leste-barato"]),
        ("mario andrade", ["biblioteca-preco-desconhecido"]),
        ("lingua luz", ["museu-centro-gratis"]),
        ("leste", ["ponto-leste-barato"]),
        ("inexistente", []),
    ],
)
async def test_text_ignores_accents_and_needs_every_word(
    places_client, city, text, expected
):
    assert await ids(places_client, f"q={text}") == expected


async def test_radius_with_filters_comes_nearest_first(places_client, city):
    found = await ids(
        places_client,
        f"lat={CENTER_LAT}&lng={CENTER_LNG}&radius_km=1&regions=Centro",
    )
    assert found == [
        "museu-centro-gratis",
        "galeria-centro-paga",
        "teatro-centro-gratis",
        "biblioteca-preco-desconhecido",
    ]
    free_nearby = await ids(
        places_client,
        f"lat={CENTER_LAT}&lng={CENTER_LNG}&radius_km=1&is_free=true",
    )
    assert free_nearby == ["museu-centro-gratis", "teatro-centro-gratis"]


async def test_map_area_with_filters_is_alphabetical(places_client, city):
    found = await ids(
        places_client,
        "sw_lat=-23.6&sw_lng=-46.7&ne_lat=-23.5&ne_lng=-46.6&categories=arte",
    )
    assert found == ["galeria-centro-paga", "museu-centro-gratis"]


async def test_filter_options_on_real_data(places_client, city):
    response = await places_client.get("/api/search/filters")
    body = response.json()
    counts = {group["id"]: group["count"] for group in body["groups"]}
    assert counts["arte"] == 3  # museu, galeria e ponto de cultura publicados
    assert counts["teatro"] == 1
    assert counts["leitura"] == 1
    assert counts["gastronomia"] == 0
    # Todos publicados com preço conhecido, menos a biblioteca ("Consulte").
    assert body["priced_places"] == 4


async def test_groups_also_match_the_name(places_client, real_db):
    await real_db.places.insert_one(
        space(
            "samba",
            "Escola de Samba Águia de Ouro",
            categories=["Ponto de Cultura"],
            description={"short": "Quadra de ensaios"},
        )
    )
    assert await ids(places_client, "categories=musica") == ["samba"]
