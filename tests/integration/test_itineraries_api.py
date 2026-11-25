"""Saving, listing, replacing and deleting itineraries through the API."""

from unittest.mock import AsyncMock

import httpx
import pytest
from pymongo.errors import AutoReconnect

from app.core.http_client import get_http_client
from app.core.security import create_access_token
from app.main import app
from app.repositories.itinerary_repository import ItineraryRepository
from app.repositories.user_repository import UserRepository

ANA = {"name": "Ana", "email": "ana@example.com", "password": "senha-segura"}
BRUNO = {"name": "Bruno", "email": "bruno@example.com", "password": "senha-segura"}
WEEKLY = "Quarta a segunda, das 11h às 19h. Fechado às terças-feiras."
CLOSED = "O local estará fechado no horário planejado."
TUESDAY = "2026-11-17"
WEDNESDAY = "2026-11-18"


def place_doc(identity: str, *, status: str = "published", hours: str = WEEKLY):
    return {
        "_id": identity,
        "name": identity,
        "publication_status": status,
        "opening_hours": {"text": hours},
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
        "source_keys": [f"test:{identity}"],
    }


@pytest.fixture(autouse=True)
async def offline_http(monkeypatch):
    """Weather stays disabled; any outbound request is a test failure."""

    def unexpected_request(request):
        pytest.fail(f"Unexpected external request: {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as http:
        monkeypatch.setitem(app.dependency_overrides, get_http_client, lambda: http)
        yield


@pytest.fixture
async def places(db) -> None:
    await db.places.insert_many(
        [
            place_doc("museu"),
            place_doc("teatro"),
            place_doc("rascunho", status="draft"),
        ]
    )


async def auth_headers(client, user=ANA) -> dict[str, str]:
    await client.post("/api/auth/register", json=user)
    response = await client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def body(**overrides) -> dict:
    return {
        "title": "Sábado no centro",
        "date": WEDNESDAY,
        "stops": [
            {"place_id": "museu", "order": 1, "planned_start_time": "12:00"},
            {"place_id": "teatro", "order": 2, "planned_start_time": "15:00"},
        ],
    } | overrides


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/itineraries"),
        ("GET", "/api/itineraries"),
        ("GET", "/api/itineraries/any"),
        ("PUT", "/api/itineraries/any"),
        ("DELETE", "/api/itineraries/any"),
    ],
)
async def test_itinerary_routes_require_authentication(client, method, path):
    response = await client.request(method, path, json=body())
    assert response.status_code == 401


async def test_create_list_get_replace_and_delete(client, db, places):
    headers = await auth_headers(client)

    created = await client.post("/api/itineraries", json=body(), headers=headers)
    assert created.status_code == 201
    payload = created.json()
    itinerary_id = payload["id"]
    assert payload["title"] == "Sábado no centro"
    assert payload["date"] == WEDNESDAY
    assert [stop["place_id"] for stop in payload["stops"]] == ["museu", "teatro"]
    assert payload["weather_forecast"] is None
    assert payload["weather_warning"]
    assert "user_id" not in payload and "_id" not in payload

    listed = await client.get("/api/itineraries", headers=headers)
    assert listed.status_code == 200
    assert [
        (item["id"], item["title"], item["stop_count"]) for item in listed.json()
    ] == [(itinerary_id, "Sábado no centro", 2)]

    fetched = await client.get(f"/api/itineraries/{itinerary_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["stops"] == payload["stops"]

    reordered = body(
        title="Sábado invertido",
        date=None,
        stops=[
            {"place_id": "teatro", "order": 1},
            {"place_id": "museu", "order": 2},
        ],
    )
    replaced = await client.put(
        f"/api/itineraries/{itinerary_id}", json=reordered, headers=headers
    )
    assert replaced.status_code == 200
    assert replaced.json()["id"] == itinerary_id
    assert replaced.json()["title"] == "Sábado invertido"
    assert replaced.json()["date"] is None
    assert [stop["place_id"] for stop in replaced.json()["stops"]] == [
        "teatro",
        "museu",
    ]
    stored = await db.itineraries.find_one({"_id": itinerary_id})
    assert stored["created_at"] <= stored["updated_at"]

    deleted = await client.delete(f"/api/itineraries/{itinerary_id}", headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    gone = await client.get(f"/api/itineraries/{itinerary_id}", headers=headers)
    assert gone.status_code == 404
    assert (await client.get("/api/itineraries", headers=headers)).json() == []


async def test_every_response_returns_timestamps_in_utc(client, places):
    headers = await auth_headers(client)
    created = (
        await client.post("/api/itineraries", json=body(), headers=headers)
    ).json()
    path = f"/api/itineraries/{created['id']}"
    responses = [
        created,
        (await client.get("/api/itineraries", headers=headers)).json()[0],
        (await client.get(path, headers=headers)).json(),
        (await client.put(path, json=body(), headers=headers)).json(),
    ]
    for payload in responses:
        for field in ("created_at", "updated_at"):
            assert payload[field].endswith("Z"), (field, payload[field])
    assert len({payload["created_at"] for payload in responses}) == 1


async def test_stops_are_returned_in_order_whatever_the_list_order(client, places):
    headers = await auth_headers(client)
    response = await client.post(
        "/api/itineraries",
        json=body(
            stops=[
                {"place_id": "teatro", "order": 2},
                {"place_id": "museu", "order": 1},
            ]
        ),
        headers=headers,
    )
    assert [stop["order"] for stop in response.json()["stops"]] == [1, 2]


async def test_saving_recalculates_conflict_warnings(client, db, places):
    headers = await auth_headers(client)
    draft = body(
        date=TUESDAY,
        stops=[
            {
                "place_id": "museu",
                "order": 1,
                "planned_start_time": "12:00",
            },
            {
                "place_id": "teatro",
                "order": 2,
                "conflict_warning": "Aviso inventado pelo cliente",
            },
        ],
    )

    created = await client.post("/api/itineraries", json=draft, headers=headers)
    closed, unscheduled = created.json()["stops"]
    assert closed["conflict_warning"] == CLOSED
    assert unscheduled["conflict_warning"] is None
    stored = await db.itineraries.find_one({"_id": created.json()["id"]})
    assert [stop["conflict_warning"] for stop in stored["stops"]] == [CLOSED, None]

    # Moving the visit to Wednesday clears the stored warning.
    replaced = await client.put(
        f"/api/itineraries/{created.json()['id']}",
        json=draft | {"date": WEDNESDAY},
        headers=headers,
    )
    assert [stop["conflict_warning"] for stop in replaced.json()["stops"]] == [
        None,
        None,
    ]


async def test_itineraries_are_isolated_per_user(client, places):
    ana = await auth_headers(client)
    bruno = await auth_headers(client, BRUNO)
    created = await client.post("/api/itineraries", json=body(), headers=ana)
    path = f"/api/itineraries/{created.json()['id']}"

    assert (await client.get("/api/itineraries", headers=bruno)).json() == []
    assert (await client.get(path, headers=bruno)).status_code == 404
    assert (
        await client.put(path, json=body(title="Invadido"), headers=bruno)
    ).status_code == 404
    assert (await client.delete(path, headers=bruno)).status_code == 404

    still_there = await client.get(path, headers=ana)
    assert still_there.status_code == 200
    assert still_there.json()["title"] == "Sábado no centro"


async def test_missing_itinerary_returns_404(client, places):
    headers = await auth_headers(client)
    path = "/api/itineraries/missing"
    assert (await client.put(path, json=body(), headers=headers)).status_code == 404
    assert (await client.delete(path, headers=headers)).status_code == 404


@pytest.mark.parametrize("place_id", ["rascunho", "inexistente"])
async def test_unpublished_or_unknown_places_are_rejected(client, db, places, place_id):
    headers = await auth_headers(client)
    draft = body(stops=[{"place_id": place_id, "order": 1}])

    created = await client.post("/api/itineraries", json=draft, headers=headers)
    assert created.status_code == 422
    assert place_id in created.json()["detail"]
    assert await db.itineraries.count_documents({}) == 0

    saved = await client.post("/api/itineraries", json=body(), headers=headers)
    replaced = await client.put(
        f"/api/itineraries/{saved.json()['id']}", json=draft, headers=headers
    )
    assert replaced.status_code == 422


@pytest.mark.parametrize(
    "invalid",
    [
        {"title": "  "},
        {"date": "17/11/2026"},
        {"stops": [{"place_id": "museu", "order": 1, "planned_start_time": "9h"}]},
        {
            "stops": [
                {"place_id": "museu", "order": 1},
                {"place_id": "teatro", "order": 1},
            ]
        },
    ],
)
async def test_invalid_bodies_are_rejected(client, db, places, invalid):
    headers = await auth_headers(client)
    response = await client.post(
        "/api/itineraries", json=body(**invalid), headers=headers
    )
    assert response.status_code == 422
    assert await db.itineraries.count_documents({}) == 0


@pytest.mark.parametrize(
    ("method", "path", "patched", "detail"),
    [
        ("POST", "/api/itineraries", "create", "salvar"),
        ("GET", "/api/itineraries", "list_by_user", "consultar os roteiros"),
        ("GET", "/api/itineraries/any", "find_owned", "consultar o roteiro"),
        ("PUT", "/api/itineraries/any", "find_owned", "salvar"),
        ("DELETE", "/api/itineraries/any", "delete_owned", "excluir"),
    ],
)
async def test_database_errors_return_503_without_details(
    client, places, monkeypatch, method, path, patched, detail
):
    headers = await auth_headers(client)
    monkeypatch.setattr(
        ItineraryRepository,
        patched,
        AsyncMock(side_effect=AutoReconnect("secret database details")),
    )
    response = await client.request(method, path, json=body(), headers=headers)
    assert response.status_code == 503
    assert detail in response.json()["detail"]
    assert "secret" not in response.text


async def test_replace_returns_404_if_deleted_while_saving(client, places, monkeypatch):
    headers = await auth_headers(client)
    created = await client.post("/api/itineraries", json=body(), headers=headers)
    monkeypatch.setattr(
        ItineraryRepository, "replace_owned", AsyncMock(return_value=None)
    )
    response = await client.put(
        f"/api/itineraries/{created.json()['id']}", json=body(), headers=headers
    )
    assert response.status_code == 404


def test_openapi_documents_itinerary_write_routes():
    paths = app.openapi()["paths"]
    collection = paths["/api/itineraries"]
    item = paths["/api/itineraries/{itinerary_id}"]
    assert {"201", "401", "422", "503"} <= collection["post"]["responses"].keys()
    assert {"200", "401", "503"} <= collection["get"]["responses"].keys()
    assert {"200", "401", "404", "422", "503"} <= item["put"]["responses"].keys()
    assert {"204", "401", "404", "503"} <= item["delete"]["responses"].keys()


async def test_create_and_edit_keep_the_event_association(client, db, places):
    headers = await auth_headers(client)
    draft = body(stops=[{"place_id": "museu", "order": 1, "event_id": "exposicao"}])
    created = await client.post("/api/itineraries", json=draft, headers=headers)
    assert created.status_code == 201
    assert created.json()["stops"][0]["event_id"] == "exposicao"
    path = f"/api/itineraries/{created.json()['id']}"
    fetched = (await client.get(path, headers=headers)).json()

    replaced = await client.put(
        path, json=fetched | {"title": "Novo título"}, headers=headers
    )

    assert replaced.status_code == 200
    assert replaced.json()["stops"][0]["event_id"] == "exposicao"
    stored = await db.itineraries.find_one({"_id": created.json()["id"]})
    assert stored["stops"][0]["event_id"] == "exposicao"


@pytest.mark.parametrize("order", [-(2**31) - 1, 2**31, -(2**63) - 1, 2**63])
async def test_out_of_range_order_returns_422_without_writing(
    client, db, places, order
):
    headers = await auth_headers(client)
    created = await client.post("/api/itineraries", json=body(), headers=headers)
    itinerary_id = created.json()["id"]
    before = await db.itineraries.find_one({"_id": itinerary_id})
    draft = body(stops=[{"place_id": "museu", "order": order}])
    for method, path in [
        ("POST", "/api/itineraries"),
        ("PUT", f"/api/itineraries/{itinerary_id}"),
    ]:
        response = await client.request(method, path, json=draft, headers=headers)
        assert response.status_code == 422
    assert await db.itineraries.count_documents({}) == 1
    assert await db.itineraries.find_one({"_id": itinerary_id}) == before


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/itineraries"),
        ("GET", "/api/itineraries"),
        ("GET", "/api/itineraries/any"),
        ("PUT", "/api/itineraries/any"),
        ("DELETE", "/api/itineraries/any"),
    ],
)
async def test_authentication_database_failure_returns_503(
    client, monkeypatch, method, path
):
    token, _ = create_access_token("ana")
    monkeypatch.setattr(
        UserRepository,
        "get_by_id",
        AsyncMock(side_effect=AutoReconnect("secret database details")),
    )
    response = await client.request(
        method,
        path,
        json=body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503
    assert "secret" not in response.text
