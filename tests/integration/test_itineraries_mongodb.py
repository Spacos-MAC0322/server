"""Itinerary writes against a real MongoDB with the strict collection validator."""

import pytest

from app.core.database import initialize_database
from app.repositories.itinerary_repository import ItineraryRepository

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
