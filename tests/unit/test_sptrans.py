import asyncio
import csv
import io
from dataclasses import replace
from datetime import datetime
from zipfile import ZipFile

import httpx
import pytest

from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.http_client import (
    ExternalAPIConnectionError,
    ExternalAPIResponseError,
    ExternalAPIStatusError,
    ExternalAPITimeoutError,
)
from app.integrations.sptrans import SPTransAuthenticationError, SPTransClient
from app.integrations.sptrans_gtfs import SAO_PAULO, GTFSNetwork

TOKEN = "unit-test-token"
BASE = "https://api.test/v2.1"
STOP = {"cp": 1, "np": "Parada A", "ed": "Rua A", "py": -23.55, "px": -46.63}
LINE = {
    "cl": 1273,
    "lt": "8000",
    "tl": 10,
    "sl": 1,
    "tp": "Principal",
    "ts": "Secundário",
}
PREDICTION = {
    "hr": "08:00",
    "p": {
        "cp": 1,
        "l": [
            {
                "cl": 1273,
                "c": "8000-10",
                "sl": 1,
                "vs": [{"p": 123, "t": "08:05", "a": True}],
            }
        ],
    },
}


@pytest.fixture
async def adapter():
    opened = []

    def build(handler, **kwargs):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        opened.append(http)
        kwargs.setdefault(
            "settings",
            Settings(
                sptrans_enabled=True,
                sptrans_base_url=BASE,
                sptrans_token=TOKEN,
            ),
        )
        client = SPTransClient(http, **kwargs)
        client.api._retry = replace(client.api._retry, max_attempts=1)
        return client

    yield build
    for http in opened:
        await http.aclose()


@pytest.fixture
def feed(tmp_path):
    def build(
        *, frequencies=None, times=None, exceptions=None, calendar=True, trip_patch=None
    ):
        tables = {
            "routes.txt": [
                {"route_id": "R", "route_short_name": "8000-10", "route_type": "3"}
            ],
            "trips.txt": [
                {
                    "route_id": "R",
                    "trip_id": "T",
                    "service_id": "weekday",
                    "direction_id": "0",
                    "trip_headsign": "Destino B",
                    **(trip_patch or {}),
                }
            ],
            "stops.txt": [
                {
                    "stop_id": "1",
                    "stop_name": "A",
                    "stop_lat": "-23.55",
                    "stop_lon": "-46.63",
                },
                {
                    "stop_id": "2",
                    "stop_name": "B",
                    "stop_lat": "-23.56",
                    "stop_lon": "-46.63",
                },
            ],
            "stop_times.txt": times
            if times is not None
            else [
                {
                    "trip_id": "T",
                    "stop_id": "2",
                    "stop_sequence": "20",
                    "arrival_time": "08:20:00",
                    "departure_time": "08:20:00",
                },
                {
                    "trip_id": "T",
                    "stop_id": "1",
                    "stop_sequence": "10",
                    "arrival_time": "08:00:00",
                    "departure_time": "08:00:00",
                },
            ],
        }
        if calendar:
            tables["calendar.txt"] = [
                {
                    "service_id": "weekday",
                    "start_date": "20260101",
                    "end_date": "20261231",
                    "monday": "1",
                    "tuesday": "1",
                    "wednesday": "1",
                    "thursday": "1",
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                }
            ]
        if frequencies is not None:
            tables["frequencies.txt"] = frequencies
        if exceptions is not None:
            tables["calendar_dates.txt"] = exceptions
        path = tmp_path / "gtfs.zip"
        with ZipFile(path, "w") as archive:
            for name, rows in tables.items():
                if not rows:
                    continue
                stream = io.StringIO()
                fields = list(dict.fromkeys(key for row in rows for key in row))
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
                archive.writestr(name, "\ufeff" + stream.getvalue())
        return path

    return build


def departure(hour=7, minute=55, day=3):
    return datetime(2026, 11, day, hour, minute, tzinfo=SAO_PAULO)


def connections(network, **kwargs):
    kwargs.setdefault("departure_at", departure())
    return network.find_connections(-23.55, -46.63, -23.56, -46.63, **kwargs)


async def test_authentication_keeps_cookie_and_queries_use_version_prefix(adapter):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/Login/Autenticar"):
            assert request.method == "POST"
            assert request.url.params["token"] == TOKEN
            return httpx.Response(
                200, json=True, headers={"Set-Cookie": "auth=session; Path=/"}
            )
        assert request.headers["cookie"] == "auth=session"
        assert request.url.path == "/v2.1/Linha/Buscar"
        assert request.url.params["termosBusca"] == "8000"
        return httpx.Response(200, json=[LINE])

    client = adapter(handler)
    result = await client.search_lines("8000")
    assert result == [
        {
            "line_id": 1273,
            "code": "8000-10",
            "direction": 1,
            "primary_terminal": "Principal",
            "secondary_terminal": "Secundário",
        }
    ]
    assert await client.authenticate() is True
    result[0]["code"] = "mutated"
    assert (await client.search_lines("8000"))[0]["code"] == "8000-10"
    assert len(calls) == 2


async def test_concurrent_login_is_only_sent_once(adapter):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=True)

    client = adapter(handler)
    assert all(await asyncio.gather(*(client.authenticate() for _ in range(5))))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload,error",
    [
        (False, SPTransAuthenticationError),
        ("true", ExternalAPIResponseError),
        ({}, ExternalAPIResponseError),
        (None, ExternalAPIResponseError),
    ],
)
async def test_rejected_or_invalid_authentication_does_not_query_data(
    adapter, payload, error, caplog
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    with pytest.raises(error) as caught:
        await adapter(handler).search_lines("8000")
    assert len(requests) == 1
    assert TOKEN not in str(caught.value)
    assert TOKEN not in caplog.text


async def test_non_json_authentication(adapter):
    with pytest.raises(ExternalAPIResponseError):
        await adapter(lambda r: httpx.Response(200, text="html")).authenticate()


@pytest.mark.parametrize("repeat_401", [False, True])
async def test_expired_session_gets_one_reauthentication(adapter, repeat_401):
    logins = []
    queries = []

    def handler(request):
        if request.method == "POST":
            logins.append(request)
            return httpx.Response(200, json=True)
        queries.append(request)
        return (
            httpx.Response(401)
            if len(queries) == 1 or repeat_401
            else httpx.Response(200, json=[])
        )

    client = adapter(handler)
    if repeat_401:
        with pytest.raises(ExternalAPIStatusError):
            await client.search_lines("8000")
    else:
        assert await client.search_lines("8000") == []
    assert len(logins) == len(queries) == 2


@pytest.mark.parametrize(
    "method,arg,path,params",
    [
        ("search_stops", "Rua A", "/Parada/Buscar", {"termosBusca": "Rua A"}),
        (
            "get_line_stops",
            1273,
            "/Parada/BuscarParadasPorLinha",
            {"codigoLinha": "1273"},
        ),
    ],
)
async def test_stop_queries(adapter, method, arg, path, params):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=True)
        assert request.url.path == "/v2.1" + path
        assert dict(request.url.params) == params
        return httpx.Response(200, json=[STOP])

    assert await getattr(adapter(handler), method)(arg) == [
        {
            "stop_id": "1",
            "name": "Parada A",
            "address": "Rua A",
            "latitude": -23.55,
            "longitude": -46.63,
        }
    ]


async def test_arrival_predictions_parse_provider_times_without_inventing_trip_duration(
    adapter,
):
    client = adapter(
        lambda r: httpx.Response(200, json=True if r.method == "POST" else PREDICTION)
    )
    result = await client.get_arrival_predictions(1)
    assert result["reference_time"] == "08:00"
    assert result["lines"][0]["vehicles"] == [
        {"vehicle_id": "123", "expected_arrival": "08:05", "accessible": True}
    ]


async def test_no_live_arrivals_is_valid(adapter):
    client = adapter(
        lambda r: httpx.Response(
            200, json=True if r.method == "POST" else {"hr": "08:00", "p": None}
        )
    )
    assert (await client.get_arrival_predictions(1))["lines"] == []


@pytest.mark.parametrize(
    "method,payload",
    [
        ("search_lines", {}),
        ("search_lines", [dict(LINE, sl=True)]),
        ("search_stops", [dict(STOP, py=True)]),
        ("search_stops", [dict(STOP, cp=True)]),
        ("search_stops", [dict(STOP, px=181)]),
        ("search_stops", [None]),
        ("get_arrival_predictions", {"hr": "08:00", "p": {"cp": 2, "l": []}}),
        ("get_arrival_predictions", {"hr": "invalid", "p": None}),
        (
            "get_arrival_predictions",
            {"hr": "08:00", "p": {"cp": 1, "l": [{"vs": None}]}},
        ),
    ],
)
async def test_invalid_api_responses_are_rejected(adapter, method, payload):
    client = adapter(
        lambda r: httpx.Response(200, json=True if r.method == "POST" else payload)
    )
    with pytest.raises(ExternalAPIResponseError):
        await getattr(client, method)(
            1 if method == "get_arrival_predictions" else "query"
        )


async def test_invalid_data_is_not_cached(adapter):
    payload = {}
    client = adapter(
        lambda r: httpx.Response(200, json=True if r.method == "POST" else payload)
    )
    with pytest.raises(ExternalAPIResponseError):
        await client.search_stops("Rua A")
    payload = [STOP]
    assert (await client.search_stops("Rua A"))[0]["stop_id"] == "1"


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ReadTimeout("timeout"), ExternalAPITimeoutError),
        (httpx.ConnectError("offline"), ExternalAPIConnectionError),
    ],
)
async def test_network_failures(adapter, error, expected):
    def handler(request):
        raise error

    with pytest.raises(expected):
        await adapter(handler).authenticate()


@pytest.mark.parametrize("status", [403, 429, 503])
async def test_http_errors(adapter, status):
    client = adapter(
        lambda r: (
            httpx.Response(200, json=True)
            if r.method == "POST"
            else httpx.Response(status)
        )
    )
    with pytest.raises(ExternalAPIStatusError) as caught:
        await client.search_lines("8000")
    assert caught.value.status_code == status


async def test_live_cache_expires(adapter, clock):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=True if request.method == "POST" else [LINE])

    client = adapter(handler, cache=ResponseCache(ttl=30, timer=clock))
    await client.search_lines("8000")
    clock.now = 29
    await client.search_lines("8000")
    assert len(requests) == 2
    clock.now = 30
    await client.search_lines("8000")
    assert len(requests) == 3


async def test_disabled_client_does_not_authenticate(adapter):
    def handler(request):
        pytest.fail("Disabled client must not send a request")

    with pytest.raises(ValueError, match="SPTRANS_ENABLED"):
        await adapter(handler, settings=Settings()).authenticate()


def test_direct_journey_uses_gtfs_sequence_and_calendar(feed):
    network = GTFSNetwork.from_zip(feed())
    result = connections(network)
    assert len(result) == 1
    assert result[0]["line"] == "8000-10"
    assert result[0]["origin_stop"]["stop_id"] == "1"
    assert result[0]["destination_stop"]["stop_id"] == "2"
    assert result[0]["waiting_minutes"] == 5
    assert result[0]["ride_minutes"] == 20
    assert result[0]["estimated_duration_minutes"] == 25
    assert (
        network.find_connections(
            -23.56, -46.63, -23.55, -46.63, departure_at=departure()
        )
        == []
    )
    assert connections(network, departure_at=departure(day=7)) == []
    assert connections(network, departure_at=departure(hour=9)) == []


@pytest.mark.parametrize("exact,wait", [("0", 5), ("1", 4)])
def test_frequency_templates_are_not_treated_as_scheduled_departures(feed, exact, wait):
    network = GTFSNetwork.from_zip(
        feed(
            frequencies=[
                {
                    "trip_id": "T",
                    "start_time": "07:00:00",
                    "end_time": "10:00:00",
                    "headway_secs": "600",
                    "exact_times": exact,
                }
            ]
        )
    )
    result = connections(network, departure_at=departure(minute=56))
    assert result[0]["waiting_minutes"] == wait
    assert result[0]["estimated_duration_minutes"] == wait + 20
    assert result[0]["estimate_source"] == "gtfs_frequency"
    assert connections(network, departure_at=departure(hour=10, minute=0)) == []


def test_walks_are_included_in_total_estimate(feed):
    network = GTFSNetwork.from_zip(feed())
    result = network.find_connections(
        -23.5505, -46.63, -23.5605, -46.63, departure_at=departure()
    )
    journey = result[0]
    assert journey["walking_minutes"] > 0
    assert journey["estimated_duration_minutes"] == pytest.approx(
        sum(
            journey[key]
            for key in ("walking_minutes", "waiting_minutes", "ride_minutes")
        ),
        abs=0.2,
    )
    assert network.find_nearby_stops(0, 0) == []


def test_calendar_exceptions_and_feed_without_weekly_calendar(feed):
    network = GTFSNetwork.from_zip(
        feed(
            exceptions=[
                {"service_id": "weekday", "date": "20261103", "exception_type": "2"},
                {"service_id": "weekday", "date": "20261107", "exception_type": "1"},
            ]
        )
    )
    assert connections(network) == []
    assert connections(network, departure_at=departure(day=7))
    network = GTFSNetwork.from_zip(
        feed(
            calendar=False,
            exceptions=[
                {"service_id": "weekday", "date": "20261103", "exception_type": "1"}
            ],
        )
    )
    assert connections(network)
    with pytest.raises(ValueError, match="calendar"):
        GTFSNetwork.from_zip(feed(calendar=False))


def test_service_past_midnight_uses_previous_service_day(feed):
    network = GTFSNetwork.from_zip(
        feed(
            times=[
                {
                    "trip_id": "T",
                    "stop_id": "1",
                    "stop_sequence": "1",
                    "arrival_time": "25:05:00",
                    "departure_time": "25:05:00",
                },
                {
                    "trip_id": "T",
                    "stop_id": "2",
                    "stop_sequence": "2",
                    "arrival_time": "25:25:00",
                    "departure_time": "25:25:00",
                },
            ]
        )
    )
    result = connections(network, departure_at=departure(day=4, hour=1, minute=0))
    assert result[0]["waiting_minutes"] == 5
    assert result[0]["estimated_duration_minutes"] == 25


@pytest.mark.parametrize("field", ["pickup_type", "drop_off_type"])
def test_pickup_dropoff_restrictions_prevent_false_connections(feed, field):
    rows = [
        {
            "trip_id": "T",
            "stop_id": "1",
            "stop_sequence": "1",
            "arrival_time": "08:00:00",
            "departure_time": "08:00:00",
        },
        {
            "trip_id": "T",
            "stop_id": "2",
            "stop_sequence": "2",
            "arrival_time": "08:20:00",
            "departure_time": "08:20:00",
        },
    ]
    rows[0 if field == "pickup_type" else 1][field] = "1"
    assert connections(GTFSNetwork.from_zip(feed(times=rows))) == []


@pytest.mark.parametrize("args", [(float("nan"), 0), (True, 0), (91, 0), (0, 181)])
def test_invalid_coordinates(feed, args):
    with pytest.raises(ValueError):
        GTFSNetwork.from_zip(feed()).find_nearby_stops(*args)


@pytest.mark.parametrize(
    "options",
    [
        {"radius_m": 0},
        {"radius_m": float("inf")},
        {"limit": 0},
        {"limit": True},
        {"departure_at": datetime(2026, 11, 3)},
    ],
)
def test_invalid_planning_options(feed, options):
    with pytest.raises(ValueError):
        connections(GTFSNetwork.from_zip(feed()), **options)


async def test_offline_journey_survives_auth_failure(adapter, feed):
    client = adapter(
        lambda r: httpx.Response(200, json=False), network=GTFSNetwork.from_zip(feed())
    )
    result = await client.find_connections(
        -23.55, -46.63, -23.56, -46.63, departure_at=departure()
    )
    assert result[0]["estimated_duration_minutes"] == 25
    assert result[0]["arrival_predictions"] is None
    assert "indisponíveis" in result[0]["warnings"][-1]


@pytest.mark.parametrize("enabled,live", [(False, True), (True, False)])
async def test_local_estimates_do_not_require_live_credentials(
    adapter, feed, enabled, live
):
    def handler(request):
        pytest.fail("No live request expected")

    settings = (
        Settings()
        if not enabled
        else Settings(sptrans_enabled=True, sptrans_base_url=BASE, sptrans_token=TOKEN)
    )
    client = adapter(handler, settings=settings, network=GTFSNetwork.from_zip(feed()))
    result = await client.find_connections(
        -23.55, -46.63, -23.56, -46.63, departure_at=departure(), include_live=live
    )
    assert result[0]["ride_minutes"] == 20


async def test_live_predictions_supplement_gtfs_estimate(adapter, feed):
    client = adapter(
        lambda r: httpx.Response(200, json=True if r.method == "POST" else PREDICTION),
        network=GTFSNetwork.from_zip(feed()),
    )
    result = await client.find_connections(
        -23.55, -46.63, -23.56, -46.63, departure_at=departure()
    )
    assert result[0]["arrival_predictions"]["lines"][0]["direction"] == 1
    assert result[0]["estimated_duration_minutes"] == 25


async def test_configured_gtfs_loads_lazily_once_and_can_find_nearby_stops(
    adapter, feed
):
    client = adapter(None, settings=Settings(sptrans_gtfs_path=feed()))
    assert (await client.find_nearby_stops(-23.55, -46.63))[0]["stop_id"] == "1"
    first = client.network
    await client.find_nearby_stops(-23.56, -46.63)
    assert client.network is first
    with pytest.raises(ValueError, match="SPTRANS_GTFS_PATH"):
        await adapter(None).find_nearby_stops(-23.55, -46.63)


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout("timeout"), httpx.ConnectError("offline"), 503]
)
async def test_journey_estimate_survives_api_transport_failures(adapter, feed, failure):
    def handler(request):
        if isinstance(failure, int):
            return httpx.Response(failure)
        raise failure

    client = adapter(handler, network=GTFSNetwork.from_zip(feed()))
    result = await client.find_connections(
        -23.55, -46.63, -23.56, -46.63, departure_at=departure()
    )
    assert result[0]["ride_minutes"] == 20
    assert result[0]["arrival_predictions"] is None
    assert "indisponíveis" in result[0]["warnings"][-1]


def test_walking_time_can_make_a_departure_unreachable(feed):
    network = GTFSNetwork.from_zip(feed())
    assert (
        network.find_connections(
            -23.552,
            -46.63,
            -23.56,
            -46.63,
            departure_at=departure(hour=7, minute=59),
        )
        == []
    )
