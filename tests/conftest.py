from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockDatabase

from app.core.config import DatabaseSettings, Settings, StartupSettings
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
