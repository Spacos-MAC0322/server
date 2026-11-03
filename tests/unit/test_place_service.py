"""Exercise the service with repository mocks and the adapter with HTTP mocks."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pymongo.errors import AutoReconnect

from app.core.config import Settings
from app.integrations.mapas_culturais import MapasCulturaisClient
from app.repositories.place_repository import PlaceRepository
from app.schemas.place import PlaceListQuery, PlaceSummarySchema
from app.services.place import PlaceService


@pytest.fixture
def service_and_repository():
    repository = MagicMock(spec=PlaceRepository)
    repository.find_all = AsyncMock(return_value=[])
    repository.find_in_bounds = AsyncMock(return_value=[])
    repository.find_curations = AsyncMock(return_value={})
    service = PlaceService(MagicMock())
    service.repository = repository
    return service, repository


def document(identity="a", *, status="published", located=True):
    return {
        "_id": identity,
        "name": "Museu",
        "publication_status": status,
        "categories": ["Museu"],
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]}
        if located
        else None,
    }


async def test_service_filters_visibility_and_preserves_public_response(
    service_and_repository,
):
    service, repository = service_and_repository
    repository.find_all.return_value = [
        document(),
        document("review", status="review"),
        document("hidden", status="hidden"),
        document("archived", status="archived"),
        document("unknown", status=None),
        document("unlocated", located=False),
        dict(document("invalid"), data_quality={"location_valid": False}),
    ]
    result = await service.list_places()
    assert len(result) == 1
    assert isinstance(result[0], PlaceSummarySchema)
    assert result[0].id == "a"
    assert result[0].badges.pet_friendly is None
    assert "publication_status" not in result[0].model_dump()
    repository.find_all.assert_awaited_once()
    repository.find_in_bounds.assert_not_awaited()


async def test_service_batches_curations_only_for_visible_places(
    service_and_repository,
):
    service, repository = service_and_repository
    repository.find_all.return_value = [
        document("a"),
        document("b"),
        document("h", status="hidden"),
    ]
    repository.find_curations.return_value = {
        "a": {"place_id": "a", "hidden_gem": True, "family_friendly": False},
    }
    summaries = await service.list_places()
    assert list(repository.find_curations.await_args.args[0]) == ["a", "b"]
    assert repository.find_curations.await_count == 1
    assert summaries[0].badges.hidden_gem is True
    assert summaries[0].badges.family_friendly is False
    assert summaries[1].badges.hidden_gem is None


async def test_service_uses_map_bounds_even_at_zero(service_and_repository):
    service, repository = service_and_repository
    repository.find_in_bounds.return_value = [document()]
    query = PlaceListQuery(sw_lat=0, sw_lng=0, ne_lat=1, ne_lng=1)
    assert len(await service.list_places(query)) == 1
    repository.find_in_bounds.assert_awaited_once_with(0, 0, 1, 1)
    repository.find_all.assert_not_awaited()


async def test_service_empty_result(service_and_repository):
    service, _ = service_and_repository
    assert await service.list_places() == []


async def test_service_propagates_database_failure_to_router(service_and_repository):
    service, repository = service_and_repository
    repository.find_all.side_effect = AutoReconnect("test failure")
    with pytest.raises(AutoReconnect):
        await service.list_places()
    repository.find_curations.assert_not_awaited()


async def test_adapter_normalizes_mocked_http_response_for_the_project_schema():
    raw = {
        "id": 31,
        "name": "  Museu de teste  ",
        "status": 1,
        "En_Municipio": "SÃO PAULO",
        "endereco": "Rua A, São Paulo, SP",
        "location": {"longitude": "-46.63", "latitude": "-23.55"},
        "type": {"id": 1, "name": "Museu"},
        "horario": "10h–18h",
        "preco": "R$ 20",
        "public": False,
    }
    pages = []

    def handler(request):
        page = request.url.params["@page"]
        pages.append(page)
        rows = [
            raw,
            dict(raw, id=32, horario=None, preco=None),
            dict(raw, id=33, En_Municipio="Guarulhos"),
        ]
        return httpx.Response(200, json=rows if page == "1" else [])

    adapter = MapasCulturaisClient(
        Settings(
            mapas_culturais_enabled=True,
            mapas_culturais_base_url="https://saas.mapas.cultura.gov.br",
        ),
        transport=httpx.MockTransport(handler),
    )
    places = await adapter.fetch_spaces()
    assert pages == ["1", "2"]
    assert len(places) == 2
    first, unknown = places
    assert first["_id"] == "mapas_saas:space:31"
    assert first["name"] == "Museu de teste"
    assert first["location"]["coordinates"] == [-46.63, -23.55]
    assert first["address"]["full"] == "Rua A, São Paulo, SP"
    assert first["categories"] == ["Museu"]
    assert first["opening_hours"] == {"text": "10h–18h", "verified": False}
    assert first["admission"]["price_text"] == "R$ 20"
    assert first["admission"]["is_free"] is None
    assert first["public_access"] is None
    assert first["publication_status"] == "review"
    assert unknown["opening_hours"]["text"] is None
    assert unknown["admission"]["price_text"] is None
    assert PlaceSummarySchema.from_mongo(first).primary_category == "Museu"
