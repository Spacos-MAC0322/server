from httpx import AsyncClient
from mongomock_motor import AsyncMongoMockDatabase


async def test_get_template(client: AsyncClient):
    response = await client.get("/template/")

    assert response.status_code == 200
    assert response.json() == {}


async def test_db_fixture_is_isolated(db: AsyncMongoMockDatabase):
    assert await db.items.count_documents({}) == 0

    await db.items.insert_one({"name": "foo"})

    doc = await db.items.find_one({"name": "foo"})
    assert doc is not None
    assert doc["name"] == "foo"
