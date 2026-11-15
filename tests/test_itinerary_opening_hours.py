import httpx
import pytest

from app.core.http_client import get_http_client
from app.main import app

WEEKLY = "Quarta a segunda, das 11h às 19h. Fechado às terças-feiras."
CLOSED = "O local estará fechado no horário planejado."


@pytest.fixture
async def offline_weather(monkeypatch):
    def unexpected_request(request):
        pytest.fail("The opening hours check must only use local data")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as http:
        monkeypatch.setitem(app.dependency_overrides, get_http_client, lambda: http)
        yield


@pytest.mark.parametrize(
    ("day", "start", "hours", "warning"),
    [
        ("2026-11-17", "12:00", WEEKLY, CLOSED),  # Tuesday
        ("2026-11-17", "12:00", "Fechado às terças", CLOSED),
        ("2026-11-18", "12:00", "Fechado às terças", None),
        ("2026-11-18", "10:59", WEEKLY, CLOSED),
        ("2026-11-18", "11:00", WEEKLY, None),
        ("2026-11-18", "19:00", WEEKLY, CLOSED),
        ("2026-11-22", "18:59:59", WEEKLY, None),
        ("2026-11-21", "12:00", "segunda à sexta - 9h ás 18h", CLOSED),
        ("2026-11-18", "12:00", "Diariamente, das 9h às 12h e das 13h às 18h", CLOSED),
        ("2026-11-18", "13:00", "Diariamente, das 9h às 12h e das 13h às 18h", None),
        ("2026-11-21", "01:30", "Sexta: 22:00 a 02:00", None),
        ("2026-11-21", "02:00", "Sexta: 22:00 a 02:00", CLOSED),
        (
            "2026-11-17",
            "12:00",
            "- 2ª feira: 09:00 às 18:00 - 3ª feira: 00:00 às 00:00",
            CLOSED,
        ),
        (
            "2026-11-21",
            "17:00",
            "Terça a sexta, das 10h às 17h\n"
            "Sábados, domingos e feriados, das 10h às 16h",
            CLOSED,
        ),
        ("2026-11-18", "12:00", "Por agendamento", None),
        ("2026-11-18", "12:00", "10h–18h", None),
        ("2026-11-18", "12:00", "-", None),
        ("2026-11-18", "12:00", "Quarta: 10h às 99h", None),
        ("2026-11-18", "12:00", None, None),
        ("2026-11-17", "24:00", WEEKLY, None),
        (None, "12:00", WEEKLY, None),
    ],
)
async def test_itinerary_checks_each_visit_against_weekly_hours(
    client, db, offline_weather, day, start, hours, warning
):
    await db.places.insert_one({"_id": "museum", "opening_hours": {"text": hours}})
    stops = [
        {"place_id": "museum", "order": 1, "planned_start_time": start},
        {"place_id": "missing", "order": 2, "planned_start_time": "12:00"},
        {"place_id": "museum", "order": 3},
    ]
    await db.itineraries.insert_one(
        {"_id": "route", "title": "Passeio", "date": day, "stops": stops}
    )

    response = await client.get("/api/itineraries/route")

    assert response.status_code == 200
    first, missing, unscheduled = response.json()["stops"]
    assert first.get("conflict_warning") == warning
    assert missing == stops[1]
    assert unscheduled == stops[2]
    assert (await db.itineraries.find_one({"_id": "route"}))["stops"] == stops


async def test_legacy_arrival_time_and_cleared_conflict(client, db, offline_weather):
    await db.places.insert_one({"_id": "museum", "opening_hours": {"text": WEEKLY}})
    await db.itineraries.insert_one(
        {
            "_id": "route",
            "title": "Passeio",
            "date": "2026-11-18",
            "stops": [
                {"place_id": "museum", "order": 1, "arrival_time": "09:00"},
                {
                    "place_id": "museum",
                    "order": 2,
                    "arrival_time": "09:00",
                    "planned_start_time": "12:00",
                    "conflict_warning": CLOSED,
                },
            ],
        }
    )
    response = await client.get("/api/itineraries/route")

    assert response.status_code == 200
    closed, open_stop = response.json()["stops"]
    assert closed["conflict_warning"] == CLOSED
    assert open_stop["conflict_warning"] is None
