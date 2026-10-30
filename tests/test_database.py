from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request
from pymongo.errors import (
    DuplicateKeyError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from app.core import database
from app.main import app
from app.scripts.init_db import main as initialize_database


@pytest.fixture
def mongo_connection(db, monkeypatch):
    # Keep real in-memory collections; only simulate the network and client lifecycle.
    monkeypatch.setattr(db, "command", AsyncMock(return_value={"ok": 1}))
    client = MagicMock()
    client.__getitem__.return_value = db
    client.close = AsyncMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(database, "AsyncMongoClient", factory)
    # mongomock does not implement collection validators; test those separately.
    monkeypatch.setattr(database, "ensure_collections", AsyncMock())
    return client, factory


async def test_indexes_are_idempotent_and_preserve_data(db):
    await db.places.create_index("name", name="existing_index")
    await db.users.insert_one({"email": "ana@example.com"})

    await database.ensure_indexes(db)
    await database.ensure_indexes(db)

    places_indexes = await db.places.index_information()
    users_indexes = await db.users.index_information()
    assert places_indexes["location_2dsphere"]["key"] == [("location", "2dsphere")]
    assert users_indexes["email_unique"]["key"] == [("email", 1)]
    assert users_indexes["email_unique"]["unique"] is True
    assert "existing_index" in places_indexes
    assert len(users_indexes) == 2  # _id plus the unique email index
    assert await db.users.count_documents({"email": "ana@example.com"}) == 1


async def test_unique_email_rejects_duplicates(db):
    await database.ensure_indexes(db)
    await db.users.insert_one({"email": "ana@example.com"})

    with pytest.raises(DuplicateKeyError):
        await db.users.insert_one({"email": "ana@example.com"})

    await db.users.insert_one({"email": "bia@example.com"})
    assert await db.users.count_documents({}) == 2


async def test_existing_duplicates_fail_without_removing_users(db):
    await db.users.insert_many(
        [{"email": "ana@example.com"}, {"email": "ana@example.com"}]
    )

    with pytest.raises(DuplicateKeyError):
        await database.ensure_indexes(db)

    assert await db.users.count_documents({}) == 2


async def test_incompatible_index_is_not_replaced(db):
    await db.users.create_index("name", name="email_unique")

    with pytest.raises(OperationFailure):
        await database.ensure_indexes(db)

    indexes = await db.users.index_information()
    assert indexes["email_unique"]["key"] == [("name", 1)]


@pytest.mark.parametrize("custom_config", [False, True])
async def test_connection_settings_and_cleanup(
    db, mongo_connection, monkeypatch, custom_config
):
    client, factory = mongo_connection
    if custom_config:
        uri, name = "mongodb://example.test:27018", "custom_db"
        monkeypatch.setenv("MONGODB_URI", uri)
        monkeypatch.setenv("MONGODB_DB_NAME", name)
    else:
        uri, name = "mongodb://localhost:27017", "spacos"
        monkeypatch.delenv("MONGODB_URI", raising=False)
        monkeypatch.delenv("MONGODB_DB_NAME", raising=False)

    async with database.connect_database() as connected:
        assert connected is db
        factory.assert_called_once_with(uri, serverSelectionTimeoutMS=5000)
        client.__getitem__.assert_called_once_with(name)
        db.command.assert_awaited_once_with("ping")
        assert "email_unique" in await db.users.index_information()
        client.close.assert_not_awaited()

    client.close.assert_awaited_once()


async def test_lifespan_exposes_database_and_cleans_up(db, mongo_connection):
    client, _ = mongo_connection
    request = Request({"type": "http", "app": app})

    async with app.router.lifespan_context(app):
        assert database.get_database(request) is db
        assert "location_2dsphere" in await db.places.index_information()
        client.close.assert_not_awaited()

    assert not hasattr(app.state, "database")
    client.close.assert_awaited_once()


@pytest.mark.parametrize("failure", ["connection", "indexes"])
async def test_startup_failure_closes_client_and_stops_application(
    db, mongo_connection, failure
):
    client, _ = mongo_connection
    if failure == "connection":
        db.command.side_effect = ServerSelectionTimeoutError("MongoDB unavailable")
        expected_error = ServerSelectionTimeoutError
    else:
        await db.users.insert_many(
            [{"email": "ana@example.com"}, {"email": "ana@example.com"}]
        )
        expected_error = DuplicateKeyError

    with pytest.raises(expected_error):
        async with app.router.lifespan_context(app):
            pytest.fail("Application must not start when database initialization fails")

    assert not hasattr(app.state, "database")
    client.close.assert_awaited_once()


async def test_lifespan_closes_client_if_application_raises(mongo_connection):
    client, _ = mongo_connection

    with pytest.raises(RuntimeError, match="request failed"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("request failed")

    assert not hasattr(app.state, "database")
    client.close.assert_awaited_once()


async def test_initialization_script_can_be_rerun(db, mongo_connection, capsys):
    client, _ = mongo_connection

    await initialize_database()
    await initialize_database()

    output = capsys.readouterr().out
    assert output.count("configurados") == 2
    assert db.name in output
    assert "location_2dsphere" in await db.places.index_information()
    assert "email_unique" in await db.users.index_information()
    assert client.close.await_count == 2


async def test_initialization_script_propagates_failure(db, mongo_connection, capsys):
    client, _ = mongo_connection
    db.command.side_effect = ServerSelectionTimeoutError("MongoDB unavailable")

    with pytest.raises(ServerSelectionTimeoutError):
        await initialize_database()

    assert capsys.readouterr().out == ""
    client.close.assert_awaited_once()
