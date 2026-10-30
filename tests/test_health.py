from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_health_is_public_and_does_not_require_database_startup():
    # Deliberately omit lifespan and the database fixture: no MongoDB is needed.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}
