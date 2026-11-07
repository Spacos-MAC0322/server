from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import (
    ExternalServiceInvalidResponse,
    ExternalServiceTimeout,
    ExternalServiceUnavailable,
)
from app.integrations.mapas_culturais import MapasCulturaisClient, normalize_space
from app.services.sync_spaces import sync_spaces

BASE = "https://saas.mapas.cultura.gov.br"


@pytest.fixture
def record():
    return {
        "id": 8,
        "name": " Museu ",
        "status": 1,
        "En_Municipio": "SÃO PAULO",
        "endereco": "Rua A, São Paulo, SP",
        "location": {"longitude": "-46.63", "latitude": "-23.55"},
        "horario": "Terça a domingo, 10h–18h",
        "preco": "R$ 20; meia R$ 10",
        "type": {"id": 1, "name": "Museu"},
        "public": False,
        "updateTimestamp": {"date": "2026-10-01 10:00:00"},
    }


def adapter(handler):
    return MapasCulturaisClient(
        Settings(mapas_culturais_enabled=True, mapas_culturais_base_url=BASE),
        transport=httpx.MockTransport(handler),
    )


def test_normalize(record):
    place = normalize_space(record, BASE)
    assert place["name"] == "Museu"
    assert place["location"]["coordinates"] == [-46.63, -23.55]
    assert place["opening_hours"]["text"] == record["horario"]
    assert place["admission"]["price_text"] == record["preco"]
    assert place["admission"]["is_free"] is None
    assert place["categories"] == ["Museu"]
    assert place["public_access"] is None
    assert place["sources"][0]["updated_at"].tzinfo is not None


@pytest.mark.parametrize(
    "patch",
    [
        {"id": None},
        {"id": True},
        {"name": " "},
        {"status": 0},
        {"En_Municipio": "Guarulhos"},
        {"En_Municipio": None, "endereco": None},
    ],
)
def test_reject_unidentified_or_other_city(record, patch):
    record.update(patch)
    assert normalize_space(record, BASE) is None


@pytest.mark.parametrize(
    "location",
    [
        None,
        {},
        {"longitude": "nan", "latitude": "-23.5"},
        {"longitude": 0, "latitude": 0},
        {"longitude": "x", "latitude": "y"},
        {"longitude": -43.2, "latitude": -22.9},
    ],
)
def test_unknown_location_and_visit_information(record, location):
    record.update(
        location=location,
        horario=None,
        preco=None,
        type=None,
        updateTimestamp="invalid",
    )
    place = normalize_space(record, BASE)
    assert place["location"] is None
    assert place["admission"]["price_text"] is None
    assert place["opening_hours"]["text"] is None
    assert place["data_quality"]["stale_source"]


def test_address_fallback_and_instance_identity(record):
    record["En_Municipio"] = None
    record["type"] = "Museu"
    place = normalize_space(record, "https://example.org")
    assert place["_id"] == "mapas_culturais:example.org:space:8"


async def test_all_pages_even_when_server_caps_limit(record):
    pages = []

    def handler(request):
        page = int(request.url.params["@page"])
        pages.append(page)
        assert request.url.params["status"] == "EQ(1)"
        assert request.url.params["En_Municipio"] == "ILIKE(São Paulo)"
        row = dict(record, id=page)
        return httpx.Response(200, json=[row] if page < 3 else [])

    result = await adapter(handler).fetch_spaces()
    assert len(result) == 2
    assert pages == [1, 2, 3]


@pytest.mark.parametrize("payload", [{"error": "bad"}, [None], "html"])
async def test_invalid_payload(payload):
    with pytest.raises(ExternalServiceInvalidResponse):
        await adapter(lambda r: httpx.Response(200, json=payload)).fetch_spaces()


async def test_non_json():
    with pytest.raises(ExternalServiceInvalidResponse):
        await adapter(lambda r: httpx.Response(200, text="<html>")).fetch_spaces()


async def test_repeated_page(record):
    with pytest.raises(ExternalServiceInvalidResponse):
        await adapter(lambda r: httpx.Response(200, json=[record])).fetch_spaces()


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ReadTimeout("secret"), ExternalServiceTimeout),
        (httpx.ConnectError("secret"), ExternalServiceUnavailable),
    ],
)
async def test_network_errors(error, expected):
    def handler(request):
        raise error

    with pytest.raises(expected, match="mapas_culturais"):
        await adapter(handler).fetch_spaces()


async def test_http_error():
    with pytest.raises(ExternalServiceUnavailable):
        await adapter(lambda r: httpx.Response(403)).fetch_spaces()


async def test_disabled_and_invalid_page_size():
    with pytest.raises(ValueError, match="Enable"):
        await MapasCulturaisClient(Settings()).fetch_spaces()
    with pytest.raises(ValueError, match="page_size"):
        await adapter(None).fetch_spaces(page_size=0)


async def test_idempotent_sync_and_curated_preservation(db, record):
    place = normalize_space(record, BASE)
    first = await sync_spaces(db, spaces=[place], source="snapshot")
    assert first["status"] == "success"
    await db.places.update_one(
        {"_id": place["_id"]},
        {
            "$set": {
                "name": "Nome curado",
                "data_quality.detail_level": "curated",
                "publication_status": "published",
                "sources": place["sources"]
                + [{"provider": "other", "external_id": "2"}],
            }
        },
    )
    await sync_spaces(db, spaces=[deepcopy(place)])
    assert await db.places.count_documents({}) == 1
    saved = await db.places.find_one({})
    assert saved["name"] == "Nome curado"
    assert saved["publication_status"] == "published"
    assert len(saved["sources"]) == 2
    assert await db.sync_runs.count_documents({"status": "success"}) == 2


async def test_sync_failure_preserves_database(db, record):
    place = normalize_space(record, BASE)
    await sync_spaces(db, spaces=[place])
    client = AsyncMock()
    client.fetch_spaces.side_effect = ExternalServiceUnavailable("mapas_culturais")
    with pytest.raises(ExternalServiceUnavailable):
        await sync_spaces(db, client=client)
    assert await db.places.count_documents({}) == 1
    failed = await db.sync_runs.find_one({"status": "error"})
    assert failed["finished_at"]
    assert failed["errors"] == ["ExternalServiceUnavailable"]


async def test_partial_database_failure(db, record):
    place = normalize_space(record, BASE)
    with pytest.raises(KeyError):
        await sync_spaces(db, spaces=[place, {}])
    assert (await db.sync_runs.find_one({"status": "partial"}))["imported"] == 1
