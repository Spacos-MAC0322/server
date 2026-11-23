"""Itinerary writes against a real MongoDB with the strict collection validator."""

import httpx
import pytest

from app.core.database import get_database, initialize_database
from app.core.http_client import get_http_client
from app.core.security import get_current_user
from app.main import app
from app.repositories.itinerary_repository import ItineraryRepository
from app.schemas.itinerary import ItineraryCreate

pytestmark = pytest.mark.integration


async def test_created_and_replaced_itineraries_pass_the_validator(real_db):
    await initialize_database(real_db)
    await real_db.places.insert_one(
        {"_id": "museu", "publication_status": "published", "source_keys": ["t:1"]},
        bypass_document_validation=True,
    )
    repository = ItineraryRepository(real_db)

    created = await repository.create(
        user_id="ana",
        title="Sábado",
        date="2026-12-05",
        stops=[
            {
                "place_id": "museu",
                "order": 1,
                "planned_start_time": "10:00",
                "planned_end_time": "11:00",
                "conflict_warning": "O local estará fechado no horário planejado.",
            }
        ],
    )
    replaced = await repository.replace_owned(
        created["_id"], "ana", title="Domingo", date=None, stops=[]
    )

    assert replaced["title"] == "Domingo"
    assert replaced["created_at"] == created["created_at"].replace(tzinfo=None)
    assert await repository.find_missing_places(["museu", "outro"]) == {"outro"}
    assert [item["stop_count"] for item in await repository.list_by_user("ana")] == [0]
    assert await repository.delete_owned(created["_id"], "ana") is True


@pytest.mark.parametrize("order", [-(2**31), 2**31 - 1])
async def test_write_contract_boundaries_and_event_pass_mongo_validator(real_db, order):
    await initialize_database(real_db)
    body = ItineraryCreate(
        title="Exposição",
        stops=[{"place_id": "museu", "order": order, "event_id": "exposicao"}],
    )
    repository = ItineraryRepository(real_db)
    created = await repository.create(
        user_id="ana",
        title=body.title,
        date=None,
        stops=[stop.model_dump() for stop in body.stops],
    )
    stored = await repository.find_owned(created["_id"], "ana")
    assert stored["stops"][0]["order"] == order
    assert stored["stops"][0]["event_id"] == "exposicao"


async def test_edit_legacy_event_and_reject_overflow_through_api(
    real_db, client, monkeypatch
):
    await initialize_database(real_db)
    await real_db.places.insert_one(
        {"_id": "museu", "publication_status": "published"},
        bypass_document_validation=True,
    )
    await real_db.itineraries.insert_one(
        {
            "_id": "legado",
            "user_id": "ana",
            "title": "Exposição",
            "date": None,
            "stops": [
                {
                    "place_id": "museu",
                    "order": 1,
                    "event_id": "exposicao",
                    "arrival_time": "10:00",
                    "departure_time": "11:00",
                }
            ],
        }
    )
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: real_db)
    monkeypatch.setitem(
        app.dependency_overrides, get_current_user, lambda: {"_id": "ana"}
    )

    def no_network(request):
        pytest.fail("Unexpected external request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as http:
        monkeypatch.setitem(app.dependency_overrides, get_http_client, lambda: http)
        fetched = (await client.get("/api/itineraries/legado")).json()
        response = await client.put(
            "/api/itineraries/legado", json=fetched | {"title": "Novo título"}
        )
        assert response.status_code == 200
        saved = await real_db.itineraries.find_one({"_id": "legado"})
        assert saved["stops"][0]["event_id"] == "exposicao"
        assert saved["stops"][0]["planned_start_time"] == "10:00"
        assert saved["stops"][0]["planned_end_time"] == "11:00"

        for order in [-(2**31) - 1, 2**31, -(2**63) - 1, 2**63]:
            invalid = {
                "title": "Inválido",
                "stops": [{"place_id": "museu", "order": order}],
            }
            for method, path in [
                ("POST", "/api/itineraries"),
                ("PUT", "/api/itineraries/legado"),
            ]:
                rejected = await client.request(method, path, json=invalid)
                assert rejected.status_code == 422
        assert await real_db.itineraries.count_documents({}) == 1
        assert await real_db.itineraries.find_one({"_id": "legado"}) == saved
