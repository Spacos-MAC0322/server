import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockDatabase
from pymongo import AsyncMongoClient

from app.core.config import (
    DatabaseSettings,
    Settings,
    StartupSettings,
    get_database_settings,
)
from app.core.database import get_database
from app.main import app


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, request):
    # Unit tests must not consume developer credentials or a local .env file.
    if request.node.get_closest_marker("integration"):
        return
    for cls in (DatabaseSettings, StartupSettings, Settings):
        monkeypatch.setitem(cls.model_config, "env_file", None)
    for name, field in Settings.model_fields.items():
        monkeypatch.delenv(name.upper(), raising=False)
        alias = field.validation_alias
        if isinstance(alias, str):
            monkeypatch.delenv(alias, raising=False)
        elif alias is not None:
            for choice in alias.choices:
                monkeypatch.delenv(choice, raising=False)


@pytest.fixture
async def mongo_client() -> AsyncIterator[AsyncMongoMockClient]:
    # Function-scoped: every test gets a fresh in-memory MongoDB.
    client = AsyncMongoMockClient()
    yield client
    client.close()


@pytest.fixture
async def db(mongo_client: AsyncMongoMockClient) -> AsyncMongoMockDatabase:
    return mongo_client["test_db"]


@pytest.fixture
async def client(db: AsyncMongoMockDatabase) -> AsyncIterator[AsyncClient]:
    # ASGITransport does not run lifespan; unit tests use an isolated database.
    app.dependency_overrides[get_database] = lambda: db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def real_db():
    uri = get_database_settings().mongodb_test_uri
    if not uri:
        if os.environ.get("CI", "").lower() == "true":
            pytest.fail("CI requires MONGODB_TEST_URI; integration tests must run")
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
