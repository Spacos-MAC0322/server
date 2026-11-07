from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from app.repositories.place_repository import PlaceRepository


def place(identity="one", *, name="Museu", keys=None):
    return {
        "_id": identity,
        "name": name,
        "source_keys": keys or [f"provider:{identity}"],
        "sources": [{"provider": "provider", "external_id": identity}],
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
    }


async def test_upsert_many_is_idempotent_and_updates_source_fields(db):
    repository = PlaceRepository(db)
    record = place()
    original = deepcopy(record)
    progress = []
    assert (
        await repository.upsert_many([record, record], on_progress=progress.append) == 2
    )
    assert progress == [1, 2]
    assert record == original
    assert await db.places.count_documents({}) == 1
    await repository.upsert_many([place(name="Nome novo")])
    saved = await db.places.find_one({})
    assert saved["name"] == "Nome novo"
    assert saved["publication_status"] == "review"
    assert len(saved["source_keys"]) == len(saved["sources"]) == 1


async def test_upsert_matches_existing_source_with_different_identity(db):
    repository = PlaceRepository(db)
    stored = place("merged", keys=["provider:one", "other:2"])
    stored["sources"].append({"provider": "other", "external_id": "2"})
    stored["publication_status"] = "published"
    await db.places.insert_one(stored)
    incoming = place(keys=["provider:one", "new:3"])
    incoming["sources"].append({"provider": "new", "external_id": "3"})
    await repository.upsert_many([incoming])
    saved = await db.places.find_one({})
    assert saved["_id"] == "merged"
    assert saved["publication_status"] == "published"
    assert set(saved["source_keys"]) == {"provider:one", "other:2", "new:3"}
    assert {s["provider"] for s in saved["sources"]} == {"provider", "other", "new"}
    assert await db.places.count_documents({}) == 1


async def test_curated_fields_survive_source_refresh(db):
    record = place(name="Curadoria")
    record["data_quality"] = {"detail_level": "curated"}
    await db.places.insert_one(record)
    incoming = place(name="Nome da fonte")
    incoming["sources"][0]["record_url"] = "https://example.org/update"
    await PlaceRepository(db).upsert_many([incoming])
    saved = await db.places.find_one({})
    assert saved["name"] == "Curadoria"
    assert saved["sources"][0]["record_url"] == "https://example.org/update"


async def test_conflicting_identities_require_manual_merge(db):
    await db.places.insert_many([place("one"), place("two")])
    incoming = place("one", keys=["provider:two"])
    with pytest.raises(ValueError, match="multiple places"):
        await PlaceRepository(db).upsert_many([incoming])
    assert await db.places.count_documents({}) == 2
    assert (await db.places.find_one({"_id": "one"}))["source_keys"] == ["provider:one"]


async def test_progress_preserves_completed_writes_on_failure(db):
    progress = []
    with pytest.raises(KeyError):
        await PlaceRepository(db).upsert_many(
            [place(), {}], on_progress=progress.append
        )
    assert progress == [1]
    assert await db.places.count_documents({}) == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"_id": " "},
        {"_id": 42},
        {"source_keys": []},
        {"source_keys": "key"},
        {"source_keys": [None]},
        {"sources": []},
        {"sources": [None]},
        {"sources": [{"provider": "test"}]},
    ],
)
async def test_invalid_identity_is_rejected_before_write(db, patch):
    record = place()
    record.update(patch)
    with pytest.raises(ValueError):
        await PlaceRepository(db).upsert_many([record])
    assert await db.places.count_documents({}) == 0


async def test_find_all_returns_all_statuses_and_missing_locations(db):
    repository = PlaceRepository(db)
    assert await repository.find_all() == []
    assert await repository.upsert_many([]) == 0
    for identity, status, location in [
        ("z", "hidden", None),
        ("a", "published", None),
        ("m", "review", None),
    ]:
        record = place(identity)
        record.update(publication_status=status, location=location)
        await db.places.insert_one(record)
    result = await repository.find_all()
    assert [r["_id"] for r in result] == ["a", "m", "z"]
    assert {r["publication_status"] for r in result} == {
        "hidden",
        "published",
        "review",
    }


@pytest.mark.parametrize(
    "bounds",
    [
        (-23.5, -46.7, -23.6, -46.5),  # reversed latitude
        (-23.7, -46.5, -23.5, -46.7),  # reversed longitude
        (-23.5, -46.7, -23.5, -46.5),  # empty height
        (-23.7, -46.5, -23.5, -46.5),  # empty width
        (-91, -46.7, -23.5, -46.5),
        (-23.7, -181, -23.5, -46.5),
        (float("nan"), -46.7, -23.5, -46.5),
        (-23.7, -46.7, float("inf"), -46.5),
        (False, -46.7, -23.5, -46.5),
        ("-23.7", -46.7, -23.5, -46.5),
        (-23.7, -170, -23.5, 170),  # no hemisphere-scale polygons
    ],
)
async def test_invalid_bounds_do_not_issue_query(bounds):
    database = MagicMock()
    with pytest.raises(ValueError):
        await PlaceRepository(database).find_in_bounds(*bounds)
    database.places.find.assert_not_called()
