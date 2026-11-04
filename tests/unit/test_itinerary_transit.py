import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zipfile import BadZipFile

import pytest
from pydantic import ValidationError

from app.core.http_client import ExternalAPIStatusError, ExternalAPITimeoutError
from app.integrations.sptrans import SPTransClient
from app.schemas.itinerary import BusLineSchema, TransitLegSchema
from app.services import itinerary_planner
from app.services.itinerary_planner import SAO_PAULO, ItineraryPlanner

DEPARTURE = datetime(2026, 11, 3, 8, tzinfo=SAO_PAULO)


def place(identity, lat=0, lng=0, **extra):
    return {
        "id": identity,
        "location": {"type": "Point", "coordinates": [lng, lat]},
        **extra,
    }


def connection(code="6500-10", duration=27.8, direction="0"):
    return {
        "route_id": code,
        "line": code,
        "headsign": "Terminal Bandeira",
        "direction_id": direction,
        "estimated_duration_minutes": duration,
    }


@pytest.fixture
def transit():
    client = AsyncMock(spec=SPTransClient)
    client.find_connections.return_value = [connection()]
    return client


def planner(transit=None):
    return ItineraryPlanner(AsyncMock(), transit_client=transit)


def test_transit_schema_serializes_nested_lines_and_integer_duration():
    leg = TransitLegSchema(
        from_place_id="a",
        to_place_id="b",
        suggested_lines=[
            BusLineSchema(
                route_id="6500-10",
                code="6500-10",
                headsign="Terminal Bandeira",
                direction_id="0",
            )
        ],
        estimated_duration_min=28,
    )
    assert leg.model_dump() == {
        "from_place_id": "a",
        "to_place_id": "b",
        "suggested_lines": [
            {
                "route_id": "6500-10",
                "code": "6500-10",
                "headsign": "Terminal Bandeira",
                "direction_id": "0",
            }
        ],
        "estimated_duration_min": 28,
    }


@pytest.mark.parametrize("duration", [-1, 1.5, True, "5"])
def test_transit_schema_rejects_invalid_duration(duration):
    with pytest.raises(ValidationError):
        TransitLegSchema(
            from_place_id="a",
            to_place_id="b",
            suggested_lines=[],
            estimated_duration_min=duration,
        )


async def test_pairs_preserve_order_and_map_geojson_longitude_before_latitude(transit):
    places = [
        place("c", -23.59, -46.67),
        place("b", -23.56, -46.65),
        place("a", -23.55, -46.63),
    ]
    result = await planner(transit).calculate_transit_legs(
        places, departure_at=DEPARTURE
    )
    assert [(leg.from_place_id, leg.to_place_id) for leg in result] == [
        ("c", "b"),
        ("b", "a"),
    ]
    assert all(leg.estimated_duration_min == 28 for leg in result)
    first, second = transit.find_connections.await_args_list
    assert first.args == (-23.59, -46.67, -23.56, -46.65)
    assert second.args == (-23.56, -46.65, -23.55, -46.63)
    assert first.kwargs["include_live"] is False
    assert second.kwargs["include_live"] is False


async def test_suggestions_sorted_by_duration_and_deduplicated_per_direction(transit):
    transit.find_connections.return_value = [
        connection("slow", 30),
        connection("fast", 12.1),
        connection("fast", 14),
        connection("fast", 16, direction="1"),
    ]
    legs = await planner(transit).calculate_transit_legs(
        [place("a"), place("b", lng=0.01)], departure_at=DEPARTURE
    )
    assert legs[0].estimated_duration_min == 13
    assert [(line.code, line.direction_id) for line in legs[0].suggested_lines] == [
        ("fast", "0"),
        ("fast", "1"),
        ("slow", "0"),
    ]


async def test_departure_moves_forward_with_travel_and_intermediate_visit(transit):
    transit.find_connections.return_value = [connection(duration=12.1)]
    places = [
        place("a"),
        place("b", lng=0.01, visit_duration_minutes=30),
        place("c", lng=0.02),
    ]
    await planner(transit).calculate_transit_legs(places, departure_at=DEPARTURE)
    calls = transit.find_connections.await_args_list
    assert calls[0].kwargs["departure_at"] == DEPARTURE
    assert calls[1].kwargs["departure_at"] == datetime(
        2026, 11, 3, 8, 43, tzinfo=SAO_PAULO
    )


async def test_utc_departure_is_converted_to_sao_paulo(transit):
    await planner(transit).calculate_transit_legs(
        [place("a"), place("b", lng=0.01)],
        departure_at=datetime(2026, 11, 3, 11, tzinfo=UTC),
    )
    actual = transit.find_connections.await_args.kwargs["departure_at"]
    assert actual.hour == 8
    assert actual.tzinfo == SAO_PAULO


async def test_default_departure_is_timezone_aware(transit):
    await planner(transit).calculate_transit_legs([place("a"), place("b", lng=0.01)])
    assert (
        transit.find_connections.await_args.kwargs["departure_at"].tzinfo == SAO_PAULO
    )


@pytest.mark.parametrize("places", [[], [place("a")]])
async def test_no_pairs_returns_empty_list_without_query(transit, places):
    assert await planner(transit).calculate_transit_legs(places) == []
    transit.find_connections.assert_not_awaited()


async def test_coincident_places_need_zero_travel_and_no_bus_query(transit):
    legs = await planner(transit).calculate_transit_legs([place("a"), place("b")])
    assert legs[0].estimated_duration_min == 0
    assert legs[0].suggested_lines == []
    transit.find_connections.assert_not_awaited()


@pytest.mark.parametrize("has_client", [False, True])
async def test_no_bus_connection_uses_explicit_empty_lines_and_walking_estimate(
    transit, has_client
):
    transit.find_connections.return_value = []
    # At the equator, 0.01 degree longitude is about 1.112 km: with the
    # documented walking assumptions, this takes about 18.1 -> 19 minutes.
    legs = await planner(transit if has_client else None).calculate_transit_legs(
        [place("a"), place("b", lng=0.01)], departure_at=DEPARTURE
    )
    assert legs[0].suggested_lines == []
    assert legs[0].estimated_duration_min == 19


@pytest.mark.parametrize(
    "failure",
    [
        ExternalAPITimeoutError("sptrans", "secret"),
        ExternalAPIStatusError("sptrans", "secret", status_code=503),
        ValueError("secret configuration"),
        FileNotFoundError("secret path"),
        BadZipFile("secret feed"),
    ],
)
async def test_service_failure_preserves_all_legs_without_leaking_details(
    transit, failure, caplog
):
    transit.find_connections.side_effect = failure
    legs = await planner(transit).calculate_transit_legs(
        [place("a"), place("b", lng=0.01), place("c", lng=0.02)], departure_at=DEPARTURE
    )
    assert len(legs) == 2
    assert all(
        not leg.suggested_lines and leg.estimated_duration_min == 19 for leg in legs
    )
    assert "secret" not in caplog.text


async def test_timeout_on_one_leg_does_not_prevent_the_next_leg(transit, monkeypatch):
    calls = 0

    async def answer(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.Event().wait()
        return [connection()]

    transit.find_connections.side_effect = answer
    monkeypatch.setattr(itinerary_planner, "TRANSIT_TIMEOUT_SECONDS", 0.001)
    legs = await planner(transit).calculate_transit_legs(
        [place("a"), place("b", lng=0.01), place("c", lng=0.02)], departure_at=DEPARTURE
    )
    assert legs[0].suggested_lines == []
    assert legs[1].suggested_lines[0].code == "6500-10"


@pytest.mark.parametrize(
    "bad_place",
    [
        None,
        {"id": "a"},
        place(""),
        place(" "),
        place("a", lat=91),
        place("a", lng=float("nan")),
        place("a", lat=True),
        {"id": "a", "location": {"type": "Polygon", "coordinates": [0, 0]}},
        {"id": "a", "location": {"type": "Point", "coordinates": [0, 0, 0]}},
        place("a", visit_duration_minutes=-1),
        place("a", visit_duration_minutes=True),
    ],
)
async def test_invalid_place_input_fails_before_querying_any_leg(transit, bad_place):
    with pytest.raises(ValueError):
        await planner(transit).calculate_transit_legs(
            [place("first"), place("second", lng=0.01), bad_place]
        )
    transit.find_connections.assert_not_awaited()


async def test_naive_departure_is_rejected(transit):
    with pytest.raises(ValueError, match="timezone"):
        await planner(transit).calculate_transit_legs(
            [place("a"), place("b", lng=0.01)], departure_at=datetime(2026, 11, 3, 8)
        )
    transit.find_connections.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"estimated_duration_minutes": 5},
        connection(duration=-1),
        connection(duration="5"),
        connection(duration=float("nan")),
        dict(connection(), line=""),
    ],
)
async def test_unusable_transit_response_uses_walking_fallback(transit, payload):
    transit.find_connections.return_value = [payload]
    legs = await planner(transit).calculate_transit_legs(
        [place("a"), place("b", lng=0.01)], departure_at=DEPARTURE
    )
    assert legs[0].suggested_lines == []
    assert legs[0].estimated_duration_min == 19


async def test_mongo_ids_are_supported_and_input_is_not_modified(transit):
    origin = place("a")
    origin["_id"] = origin.pop("id")
    places = [origin, place("b", lng=0.01)]
    original = deepcopy(places)
    legs = await planner(transit).calculate_transit_legs(places, departure_at=DEPARTURE)
    assert legs[0].from_place_id == "a"
    assert places == original
