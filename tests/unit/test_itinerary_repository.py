"""ItineraryRepository writes and owner-scoped reads, on an in-memory MongoDB."""

import pytest

from app.repositories.itinerary_repository import ItineraryRepository


@pytest.fixture
def repository(db) -> ItineraryRepository:
    return ItineraryRepository(db)


async def create(repository, user_id="ana", title="Passeio", date=None, stops=()):
    return await repository.create(
        user_id=user_id, title=title, date=date, stops=list(stops)
    )


async def test_create_stores_stops_in_visit_order_with_known_fields(repository, db):
    created = await create(
        repository,
        date="2026-12-05",
        stops=[
            {"place_id": "b", "order": 2, "planned_start_time": "14:00"},
            {
                "place_id": "a",
                "order": 1,
                "planned_start_time": "10:00",
                "planned_end_time": "11:00",
                "conflict_warning": "Fechado",
                "unexpected": "dropped",
            },
        ],
    )

    stored = await db.itineraries.find_one({"_id": created["_id"]})
    assert stored["user_id"] == "ana"
    assert stored["title"] == "Passeio"
    assert stored["date"] == "2026-12-05"
    assert stored["preferences"] == {}
    assert stored["stops"] == [
        {
            "place_id": "a",
            "order": 1,
            "planned_start_time": "10:00",
            "planned_end_time": "11:00",
            "conflict_warning": "Fechado",
        },
        {
            "place_id": "b",
            "order": 2,
            "planned_start_time": "14:00",
            "planned_end_time": None,
            "conflict_warning": None,
        },
    ]
    assert created["created_at"] == created["updated_at"]
    assert created["created_at"].microsecond % 1000 == 0


async def test_create_generates_distinct_string_ids(repository):
    first = await create(repository)
    second = await create(repository)
    assert isinstance(first["_id"], str)
    assert first["_id"] != second["_id"]


async def test_list_by_user_returns_only_own_summaries_ordered_by_date(repository):
    later = await create(repository, title="Domingo", date="2026-12-06")
    sooner = await create(
        repository,
        title="Sábado",
        date="2026-12-05",
        stops=[{"place_id": "a", "order": 1}, {"place_id": "b", "order": 2}],
    )
    undated = await create(repository, title="Ideias")
    await create(repository, user_id="bruno", title="Do Bruno")

    summaries = await repository.list_by_user("ana")

    assert [item["_id"] for item in summaries] == [
        undated["_id"],
        sooner["_id"],
        later["_id"],
    ]
    assert [item["stop_count"] for item in summaries] == [0, 2, 0]
    assert all("stops" not in item and "user_id" not in item for item in summaries)
    assert await repository.list_by_user("nobody") == []


async def test_find_owned_hides_other_users_itineraries(repository):
    created = await create(repository)
    assert (await repository.find_owned(created["_id"], "ana"))["title"] == "Passeio"
    assert await repository.find_owned(created["_id"], "bruno") is None
    assert await repository.find_owned("missing", "ana") is None


async def test_replace_owned_replaces_content_and_keeps_owner_and_creation(
    repository, db
):
    created = await create(
        repository,
        date="2026-12-05",
        stops=[{"place_id": "a", "order": 1}, {"place_id": "b", "order": 2}],
    )
    await db.itineraries.update_one(
        {"_id": created["_id"]}, {"$set": {"preferences": {"budget": 50}}}
    )

    replaced = await repository.replace_owned(
        created["_id"],
        "ana",
        title="Novo",
        date=None,
        stops=[{"place_id": "b", "order": 1}],
    )

    assert replaced["title"] == "Novo"
    assert replaced["date"] is None
    assert [stop["place_id"] for stop in replaced["stops"]] == ["b"]
    assert replaced["user_id"] == "ana"
    assert replaced["preferences"] == {"budget": 50}
    # MongoDB hands dates back without tzinfo; they are stored in UTC.
    assert replaced["created_at"] == created["created_at"].replace(tzinfo=None)
    assert replaced["updated_at"] >= created["updated_at"].replace(tzinfo=None)


async def test_replace_owned_ignores_other_users(repository, db):
    created = await create(repository)
    result = await repository.replace_owned(
        created["_id"], "bruno", title="Invadido", date=None, stops=[]
    )
    assert result is None
    assert (await db.itineraries.find_one({"_id": created["_id"]}))["title"] == (
        "Passeio"
    )


async def test_delete_owned_only_removes_own_itinerary_once(repository, db):
    created = await create(repository)
    assert await repository.delete_owned(created["_id"], "bruno") is False
    assert await repository.delete_owned(created["_id"], "ana") is True
    assert await repository.delete_owned(created["_id"], "ana") is False
    assert await db.itineraries.count_documents({}) == 0


async def test_find_missing_places_requires_published_places(repository, db):
    await db.places.insert_many(
        [
            {"_id": "open", "publication_status": "published"},
            {"_id": "draft", "publication_status": "draft"},
        ]
    )
    assert await repository.find_missing_places(["open", "draft", "gone"]) == {
        "draft",
        "gone",
    }
    assert await repository.find_missing_places(["open", "open"]) == set()
    assert await repository.find_missing_places([]) == set()
