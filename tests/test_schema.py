from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import OperationFailure

from app.core import database


@pytest.fixture
def schema_database():
    mocked = MagicMock()
    mocked.list_collection_names = AsyncMock(return_value=[])
    mocked.create_collection = AsyncMock()
    mocked.command = AsyncMock()
    return mocked


async def test_creates_all_collections_with_strict_validators(schema_database):
    await database.ensure_collections(schema_database)

    calls = schema_database.create_collection.await_args_list
    assert {call.args[0] for call in calls} == {
        "places",
        "events",
        "territories",
        "place_curations",
        "sync_runs",
        "users",
        "itineraries",
        "user_places",
    }
    for call in calls:
        assert call.kwargs["validationLevel"] == "strict"
        assert call.kwargs["validationAction"] == "error"
        assert call.kwargs["validator"]["$jsonSchema"]["bsonType"] == "object"
        assert "indexes" not in call.kwargs
    schema_database.command.assert_not_awaited()


async def test_updates_existing_validators_without_recreating_collections(
    schema_database,
):
    schema_database.list_collection_names.return_value = [
        *database.COLLECTION_DEFINITIONS,
        "unrelated",
    ]

    await database.ensure_collections(schema_database)

    schema_database.create_collection.assert_not_awaited()
    calls = schema_database.command.await_args_list
    assert len(calls) == 8
    assert {call.args[1] for call in calls} == set(database.COLLECTION_DEFINITIONS)
    assert all(call.args[0] == "collMod" for call in calls)
    assert all("validator" in call.kwargs for call in calls)


async def test_concurrent_collection_creation_applies_validator(schema_database):
    schema_database.create_collection.side_effect = OperationFailure(
        "already exists", code=48
    )

    await database.ensure_collections(schema_database)

    assert schema_database.command.await_count == 8
    assert schema_database.command.await_args_list[0].args == ("collMod", "places")


async def test_creation_permission_failure_is_not_ignored(schema_database):
    schema_database.create_collection.side_effect = OperationFailure(
        "unauthorized", code=13
    )

    with pytest.raises(OperationFailure, match="unauthorized"):
        await database.ensure_collections(schema_database)

    schema_database.command.assert_not_awaited()


async def test_validator_update_failure_is_not_ignored(schema_database):
    schema_database.list_collection_names.return_value = ["places"]
    schema_database.command.side_effect = OperationFailure("unauthorized", code=13)

    with pytest.raises(OperationFailure, match="unauthorized"):
        await database.ensure_collections(schema_database)

    schema_database.create_collection.assert_not_awaited()


async def test_schema_failure_prevents_index_initialization(monkeypatch):
    collections = AsyncMock(side_effect=OperationFailure("invalid schema"))
    indexes = AsyncMock()
    monkeypatch.setattr(database, "ensure_collections", collections)
    monkeypatch.setattr(database, "ensure_indexes", indexes)

    with pytest.raises(OperationFailure, match="invalid schema"):
        await database.initialize_database(MagicMock())

    indexes.assert_not_awaited()


async def test_all_fourteen_model_indexes_are_created(db):
    await database.ensure_indexes(db)

    indexes = {
        name: await db[name].index_information()
        for name in database.COLLECTION_DEFINITIONS
    }
    assert sum(len(value) - 1 for value in indexes.values()) == 14
    assert indexes["territories"]["territories_geospatial"]["key"] == [
        ("geometry", "2dsphere")
    ]
    assert indexes["places"]["source_keys_unique"]["unique"] is True
    assert indexes["events"]["event_source_keys_unique"]["unique"] is True
    assert indexes["user_places"]["user_place_unique"]["key"] == [
        ("user_id", 1),
        ("place_id", 1),
    ]
