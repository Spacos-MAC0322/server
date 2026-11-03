"""Opt-in checks against a real MongoDB, using an isolated temporary database."""

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure, WriteError

from app.core.config import Settings, get_database_settings
from app.core.database import COLLECTION_DEFINITIONS, initialize_database
from app.integrations.mapas_culturais import MapasCulturaisClient, normalize_space
from app.jobs import sync_places as job
from app.services.sync_spaces import sync_spaces

pytestmark = pytest.mark.integration


async def test_places_job_consumes_adapter_with_shared_mongo_schema(
    real_db, monkeypatch
):
    @asynccontextmanager
    async def connect():
        await initialize_database(real_db)
        yield real_db

    monkeypatch.setattr(job, "connect_database", connect)
    raw = {
        "id": 19,
        "name": "Museu do pipeline",
        "status": 1,
        "En_Municipio": "São Paulo",
        "location": {"longitude": "-46.63", "latitude": "-23.55"},
    }
    client = MapasCulturaisClient(
        Settings(
            mapas_culturais_enabled=True,
            mapas_culturais_base_url="https://saas.mapas.cultura.gov.br",
        ),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json=[raw] if r.url.params["@page"] == "1" else [],
            )
        ),
    )
    for _ in range(2):
        result = await job.sync_places(client=client)
        assert result["imported"] == 1
    assert await real_db.places.count_documents({}) == 1
    options = await real_db.places.options()
    assert options["validator"] == COLLECTION_DEFINITIONS["places"]["validator"]
    saved = await real_db.places.find_one(
        {
            "location": {
                "$near": {
                    "$geometry": {"type": "Point", "coordinates": [-46.63, -23.55]},
                    "$maxDistance": 100,
                }
            }
        }
    )
    assert saved["_id"] == "mapas_saas:space:19"
    with pytest.raises(WriteError):
        await real_db.places.update_one({"_id": saved["_id"]}, {"$set": {"name": 42}})


async def test_real_sync_schema_upsert_and_geospatial_index(real_db):
    await initialize_database(real_db)
    place = normalize_space(
        {
            "id": 8,
            "name": "Museu de teste",
            "status": 1,
            "En_Municipio": "São Paulo",
            "location": {"longitude": -46.63, "latitude": -23.55},
        },
        "https://saas.mapas.cultura.gov.br",
    )
    await sync_spaces(real_db, spaces=[place])
    await sync_spaces(real_db, spaces=[place])
    assert await real_db.places.count_documents({}) == 1
    saved = await real_db.places.find_one(
        {
            "location": {
                "$near": {
                    "$geometry": place["location"],
                    "$maxDistance": 100,
                }
            }
        }
    )
    assert saved["source_keys"] == [place["_id"]]
    assert await real_db.sync_runs.count_documents({"status": "success"}) == 2


@pytest.fixture
async def real_db():
    uri = get_database_settings().mongodb_test_uri
    if not uri:
        pytest.skip("Set MONGODB_TEST_URI to run real MongoDB integration tests")
    async with AsyncMongoClient(
        uri.get_secret_value(), serverSelectionTimeoutMS=5000
    ) as client:
        name = f"spacos_test_{uuid4().hex}"
        assert name not in await client.list_database_names()
        try:
            yield client[name]
        finally:
            await client.drop_database(name)


async def test_full_schema_and_indexes_match_the_shared_model(real_db):
    await initialize_database(real_db)

    assert set(await real_db.list_collection_names()) == set(COLLECTION_DEFINITIONS)
    async for collection in await real_db.list_collections():
        definition = COLLECTION_DEFINITIONS[collection["name"]]
        assert collection["options"]["validator"] == definition["validator"]
        assert collection["options"]["validationLevel"] == "strict"
        assert collection["options"]["validationAction"] == "error"
        indexes = await real_db[collection["name"]].index_information()
        for index in definition["indexes"]:
            actual = indexes[index["name"]]
            assert actual["key"] == [tuple(key) for key in index["key"]]
            assert actual.get("unique", False) == index.get("unique", False)

    for name in COLLECTION_DEFINITIONS:
        with pytest.raises(WriteError) as error:
            await real_db[name].insert_one({"_id": "missing-required-fields"})
        assert error.value.code == 121  # DocumentValidationFailure


async def test_existing_collections_gain_validators_without_losing_data(real_db):
    await real_db.users.insert_one(
        {"_id": "user-1", "email": "ana@example.com", "password_hash": "test"}
    )
    await real_db.users.create_index("name", name="existing_index")
    await real_db.unrelated.insert_one({"_id": "keep"})

    await initialize_database(real_db)
    await initialize_database(real_db)

    assert await real_db.users.count_documents({}) == 1
    assert await real_db.unrelated.find_one({"_id": "keep"}) is not None
    assert "existing_index" in await real_db.users.index_information()
    with pytest.raises(WriteError) as error:
        await real_db.users.update_one({"_id": "user-1"}, {"$set": {"email": 123}})
    assert error.value.code == 121
    assert (await real_db.users.find_one({"_id": "user-1"}))[
        "email"
    ] == "ana@example.com"


async def test_duplicate_email_and_geospatial_query_with_valid_documents(real_db):
    await initialize_database(real_db)
    await real_db.users.insert_one(
        {"_id": "user-1", "email": "ana@example.com", "password_hash": "test"}
    )
    with pytest.raises(DuplicateKeyError):
        await real_db.users.insert_one(
            {"_id": "user-2", "email": "ana@example.com", "password_hash": "test"}
        )

    location = {"type": "Point", "coordinates": [-46.6333, -23.5505]}
    await real_db.places.insert_one(
        {
            "_id": "place-1",
            "name": "Local de teste",
            "location": location,
            "source_keys": ["test:1"],
            "sources": [{"provider": "test", "external_id": "1"}],
        }
    )
    place = await real_db.places.find_one(
        {"location": {"$near": {"$geometry": location, "$maxDistance": 1000}}}
    )
    assert place["_id"] == "place-1"


async def test_existing_duplicate_emails_stop_initialization_without_deletion(real_db):
    await real_db.users.insert_many(
        [
            {"_id": "user-1", "email": "ana@example.com", "password_hash": "test"},
            {"_id": "user-2", "email": "ana@example.com", "password_hash": "test"},
        ]
    )

    with pytest.raises(OperationFailure) as error:
        await initialize_database(real_db)

    assert error.value.code == 11000
    assert await real_db.users.count_documents({}) == 2
