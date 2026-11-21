import pytest
from pymongo.errors import AutoReconnect

from app.core.config import Settings
from app.core.http_client import ExternalAPIStatusError
from app.integrations.geosampa import Subprefecture
from app.jobs.enrich_places import enrich_places
from app.repositories.territory_repository import TerritoryRepository
from app.services import place_enrichment
from app.services.place_enrichment import (
    TerritoryLocator,
    backfill_update,
    enrich_place,
    load_territory_locator,
)


def area(code, name, zone, lng, lat, size=0.1) -> Subprefecture:
    ring = [
        [lng, lat],
        [lng + size, lat],
        [lng + size, lat + size],
        [lng, lat + size],
        [lng, lat],
    ]
    return {
        "code": code,
        "name": name,
        "zone": zone,
        "region": zone,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


CENTRO = area("09", "Sé", "Centro", -46.70, -23.60)
LESTE = area("25", "Mooca", "Leste", -46.60, -23.60)
LOCATOR = TerritoryLocator([CENTRO, LESTE])
SE_SOURCE = {
    "provider": "geosampa",
    "external_id": "subprefeitura:09",
    "layer": "geoportal:subprefeitura",
}
ENABLED = Settings(geosampa_enabled=True)


def place(lng=-46.65, lat=-23.55, **fields):
    return {
        "_id": "mapas_saas:space:1",
        "name": "Casa",
        "location": {"type": "Point", "coordinates": [lng, lat]},
        "sources": [{"provider": "mapas_saas", "external_id": "1"}],
        **fields,
    }


def test_locator_finds_the_containing_subprefecture():
    assert LOCATOR.locate(-23.55, -46.65)["name"] == "Sé"
    assert LOCATOR.locate(-23.55, -46.55)["name"] == "Mooca"
    assert LOCATOR.locate(-23.80, -46.65) is None


def test_enrich_adds_zone_subprefecture_and_source():
    enriched = enrich_place(place(territory={"district": "Sé"}), LOCATOR)
    assert enriched["region_zone"] == "Centro"
    assert enriched["territory"] == {
        "district": "Sé",
        "subprefecture": "Sé",
        "region": "Centro",
    }
    assert enriched["sources"][-1] == SE_SOURCE
    assert enriched["sources"][0]["provider"] == "mapas_saas"


def test_enrich_replaces_an_outdated_subprefecture_source():
    old = dict(SE_SOURCE, external_id="subprefeitura:25")
    enriched = enrich_place(place(sources=[old]), LOCATOR)
    assert enriched["sources"] == [SE_SOURCE]


@pytest.mark.parametrize(
    "item",
    [
        place(lat=-23.9),
        dict(place(), location=None),
        dict(place(), location={"type": "Point", "coordinates": ["x", "y"]}),
    ],
)
def test_enrich_leaves_unlocated_places_unchanged(item):
    assert enrich_place(item, LOCATOR) is item
    assert enrich_place(place(), None)["sources"] == place()["sources"]


def test_backfill_fills_only_empty_fields():
    stored = place(
        territory={"subprefecture": "Curated name"},
        admission={"price_text": "Entrada gratuita", "is_free": None},
    )
    assert backfill_update(stored, LOCATOR) == {
        "$set": {
            "region_zone": "Centro",
            "territory.region": "Centro",
            "admission.is_free": True,
            "admission.price_min": 0.0,
            "admission.price_max": 0.0,
        },
        "$addToSet": {"sources": SE_SOURCE},
    }


def test_backfill_keeps_existing_zone_and_price():
    stored = place(
        region_zone="Sul",
        admission={"price_text": "R$ 20", "is_free": False},
    )
    assert backfill_update(stored, LOCATOR) is None


def test_backfill_prices_without_boundaries():
    stored = place(admission={"price_text": "R$ 10 a R$ 30"})
    assert backfill_update(stored, None) == {
        "$set": {
            "admission.is_free": False,
            "admission.price_min": 10.0,
            "admission.price_max": 30.0,
        }
    }


async def fetched(**_) -> list[Subprefecture]:
    return [CENTRO, LESTE]


async def failing(**_) -> list[Subprefecture]:
    raise ExternalAPIStatusError("geosampa", "HTTP 503")


async def test_locator_from_geosampa_is_stored_for_later(db):
    locator, status = await load_territory_locator(db, settings=ENABLED, fetch=fetched)
    assert status == "geosampa"
    assert locator.locate(-23.55, -46.65)["code"] == "09"
    stored = await TerritoryRepository(db).find_subprefectures()
    assert stored == [CENTRO, LESTE]
    document = await db.territories.find_one({"_id": "geosampa:subprefeitura:09"})
    assert document["kind"] == "subprefecture"
    assert document["sources"] == [SE_SOURCE]


async def test_unavailable_geosampa_falls_back_to_stored_boundaries(db):
    await TerritoryRepository(db).save_subprefectures([CENTRO])
    locator, status = await load_territory_locator(db, settings=ENABLED, fetch=failing)
    assert status == "stored"
    assert locator.locate(-23.55, -46.65)["zone"] == "Centro"


async def test_storage_failure_keeps_fresh_boundaries(db, monkeypatch):
    async def broken(self, items):
        raise AutoReconnect("down")

    monkeypatch.setattr(TerritoryRepository, "save_subprefectures", broken)
    locator, status = await load_territory_locator(db, settings=ENABLED, fetch=fetched)
    assert status == "geosampa"
    assert locator is not None


@pytest.mark.parametrize(
    ("settings", "expected"),
    [(ENABLED, "unavailable"), (Settings(), "disabled")],
)
async def test_without_any_boundaries(db, settings, expected):
    assert await load_territory_locator(db, settings=settings, fetch=failing) == (
        None,
        expected,
    )


async def test_backfill_job_updates_once(db, monkeypatch):
    monkeypatch.setattr(place_enrichment, "get_settings", lambda: ENABLED)
    monkeypatch.setattr(place_enrichment, "fetch_subprefectures", fetched)
    await db.places.insert_many(
        [
            place(admission={"price_text": "R$ 15"}),
            dict(place(lng=-46.55), _id="b", region_zone="Leste"),
            dict(place(lat=-23.9), _id="c"),
        ]
    )

    first = await enrich_places(db)
    second = await enrich_places(db)

    assert first == {
        "checked": 3,
        "updated": 1,
        "zones": 1,
        "prices": 1,
        "territory": "geosampa",
    }
    assert second["updated"] == 0
    stored = await db.places.find_one({"_id": "mapas_saas:space:1"})
    assert stored["region_zone"] == "Centro"
    assert stored["territory"] == {"subprefecture": "Sé", "region": "Centro"}
    assert stored["admission"]["price_min"] == 15.0
    assert stored["sources"][-1] == SE_SOURCE
