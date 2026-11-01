from collections.abc import AsyncIterator, Callable

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockDatabase

from app.core.database import get_database
from app.core.http_client import ExternalAPIClient, create_http_client
from app.main import app

from .http_support import BASE_URL, SERVICE


class Clock:
    """Manually advanced time source, so expiry tests never wait."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def delays() -> list[float]:
    return []


@pytest.fixture
async def connect(delays: list[float]) -> AsyncIterator[Callable]:
    """Build clients whose traffic is scripted and whose waits are only recorded."""
    opened: list[AsyncClient] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    def build(upstream: Callable, **options) -> ExternalAPIClient:
        http = create_http_client(transport=MockTransport(upstream))
        opened.append(http)
        options.setdefault("base_url", BASE_URL)
        return ExternalAPIClient(http, SERVICE, sleep=record_delay, **options)

    yield build
    for http in opened:
        await http.aclose()


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
