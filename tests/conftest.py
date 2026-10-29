from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockDatabase

from app.main import app


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
    # Once the app exposes a DB dependency, inject the mock here:
    # app.dependency_overrides[get_database] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
