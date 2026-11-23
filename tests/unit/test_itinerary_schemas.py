"""Write contracts for saving and replacing itineraries."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.itinerary import (
    MAX_STOPS,
    MAX_TITLE_LENGTH,
    ItineraryCreate,
    ItineraryResponse,
    ItinerarySummary,
    ItineraryUpdate,
)


def stop(order: int, start: str | None = None, end: str | None = None) -> dict:
    return {
        "place_id": f"place-{order}",
        "order": order,
        "planned_start_time": start,
        "planned_end_time": end,
    }


def test_create_strips_title_and_parses_date_and_stops():
    body = ItineraryCreate(
        title="  Sábado no centro  ",
        date="2026-12-05",
        stops=[stop(2, "14:00"), stop(1, "10:00", "11:30:00")],
    )
    assert body.title == "Sábado no centro"
    assert body.date == date(2026, 12, 5)
    assert [item.order for item in body.stops] == [2, 1]
    assert body.stops[1].planned_end_time == "11:30:00"


def test_create_allows_an_undated_itinerary_without_stops():
    body = ItineraryCreate(title="Ideias")
    assert body.date is None
    assert body.stops == []


def test_stops_accept_legacy_time_names():
    body = ItineraryCreate(
        title="Passeio",
        stops=[
            {
                "place_id": "p",
                "order": 1,
                "arrival_time": "10:00",
                "departure_time": "11:00",
            }
        ],
    )
    assert body.stops[0].planned_start_time == "10:00"
    assert body.stops[0].planned_end_time == "11:00"


def test_update_has_the_same_contract_as_create():
    body = ItineraryUpdate(title="Novo nome", stops=[stop(1)])
    assert isinstance(body, ItineraryCreate)
    assert body.title == "Novo nome"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"title": "   "}, "Informe um nome"),
        ({"title": ""}, "at least 1"),
        ({"title": "x" * (MAX_TITLE_LENGTH + 1)}, "at most"),
        ({}, "Field required"),
        ({"title": "x", "date": "05/12/2026"}, "valid date"),
        ({"title": "x", "stops": [stop(1), stop(1)]}, "ordem diferente"),
        ({"title": "x", "stops": [stop(1, "11:00", "10:00")]}, "depois do horário"),
        ({"title": "x", "stops": [stop(1, "10:00", "10:00:00")]}, "depois do horário"),
        ({"title": "x", "stops": [stop(1, "24:00")]}, "HH:MM"),
        ({"title": "x", "stops": [{"order": 1}]}, "Field required"),
        ({"title": "x", "stops": [{"place_id": "p", "order": "1"}]}, "integer"),
        (
            {"title": "x", "stops": [stop(i) for i in range(MAX_STOPS + 1)]},
            "at most",
        ),
    ],
)
def test_create_rejects_invalid_bodies(payload, message):
    with pytest.raises(ValidationError, match=message):
        ItineraryCreate(**payload)


def test_end_time_with_seconds_after_start_without_seconds_is_valid():
    body = ItineraryCreate(title="x", stops=[stop(1, "10:00", "10:00:01")])
    assert body.stops[0].planned_end_time == "10:00:01"


def test_conflict_warning_sent_by_client_is_parsed_but_not_trusted():
    body = ItineraryCreate(
        title="x",
        stops=[{"place_id": "p", "order": 1, "conflict_warning": "falso"}],
    )
    dumped = body.stops[0].model_dump(exclude={"conflict_warning"})
    assert "conflict_warning" not in dumped


@pytest.mark.parametrize("schema", [ItinerarySummary, ItineraryResponse])
def test_naive_mongo_timestamps_are_returned_as_utc(schema):
    naive = datetime(2026, 11, 23, 6, 9, 30, 410000)
    aware = naive.replace(tzinfo=UTC)
    parsed = schema(
        _id="abc",
        id="abc",
        title="Passeio",
        stop_count=0,
        stops=[],
        created_at=naive,
        updated_at=aware,
    )
    assert parsed.created_at == aware
    assert parsed.updated_at == aware
    dumped = parsed.model_dump(mode="json")
    assert dumped["created_at"] == dumped["updated_at"] == "2026-11-23T06:09:30.410000Z"


def test_timestamps_in_other_timezones_and_strings_are_kept():
    offset = datetime(2026, 11, 23, 3, 0, tzinfo=timezone(timedelta(hours=-3)))
    parsed = ItinerarySummary(
        _id="abc", title="x", stop_count=0, created_at=offset, updated_at="legado"
    )
    assert parsed.created_at == offset
    assert parsed.updated_at == "legado"


def test_summary_reads_mongo_id():
    summary = ItinerarySummary(_id="abc", title="Passeio", stop_count=3)
    assert summary.model_dump() == {
        "id": "abc",
        "title": "Passeio",
        "date": None,
        "stop_count": 3,
        "created_at": None,
        "updated_at": None,
    }


@pytest.mark.parametrize("schema", [ItineraryCreate, ItineraryUpdate])
def test_write_preserves_an_optional_event_id(schema):
    body = schema(
        title="Exposição", stops=[stop(1) | {"event_id": "exposicao"}, stop(2)]
    )
    assert body.stops[0].model_dump()["event_id"] == "exposicao"
    assert body.stops[1].event_id is None


@pytest.mark.parametrize("schema", [ItineraryCreate, ItineraryUpdate])
@pytest.mark.parametrize("order", [-(2**31), 2**31 - 1])
def test_write_accepts_bson_int_boundaries(schema, order):
    assert schema(title="x", stops=[stop(order)]).stops[0].order == order


@pytest.mark.parametrize("schema", [ItineraryCreate, ItineraryUpdate])
@pytest.mark.parametrize("order", [-(2**31) - 1, 2**31, -(2**63) - 1, 2**63])
def test_write_rejects_order_outside_bson_int_range(schema, order):
    with pytest.raises(ValidationError):
        schema(title="x", stops=[stop(order)])
