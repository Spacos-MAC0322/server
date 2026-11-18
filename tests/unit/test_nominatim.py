"""Nominatim adapter: request contract, result mapping, cache and throttle."""

import httpx
import pytest

from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.http_client import (
    USER_AGENT,
    ExternalAPIResponseError,
    ExternalAPIStatusError,
)
from app.integrations.nominatim import (
    NominatimClient,
    Throttle,
    normalize_query,
    parse_candidates,
)

MASP = {
    "lat": "-23.5614",
    "lon": "-46.6559",
    "name": "Museu de Arte de São Paulo",
    "importance": 0.498,
    "address": {
        "road": "Avenida Paulista",
        "house_number": "1578",
        "suburb": "Bela Vista",
    },
}
STREET = {
    "lat": "-23.5677",
    "lon": "-46.6930",
    "name": "Rua dos Pinheiros",
    "importance": 0.0534,
    "address": {"road": "Rua dos Pinheiros", "city_district": "Pinheiros"},
}


@pytest.fixture
async def nominatim(clock, delays):
    clients = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)
        clock.now += delay

    def build(handler, **settings):
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": USER_AGENT},
        )
        clients.append(http)
        return NominatimClient(
            http,
            Settings(nominatim_enabled=True, **settings),
            cache=ResponseCache(ttl=60),
            throttle=Throttle(1.0, clock=clock, sleep=record_delay),
        )

    yield build
    for http in clients:
        await http.aclose()


def respond(*payload, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, json=list(payload))

    return handler


async def test_search_sends_the_documented_request(nominatim):
    requests = []
    client = nominatim(respond(MASP, requests=requests), nominatim_email="a@b.org")

    await client.search("  MASP  ")

    (request,) = requests
    assert str(request.url.copy_with(query=None)) == (
        "https://nominatim.openstreetmap.org/search"
    )
    assert dict(request.url.params) == {
        "q": "masp",
        "format": "jsonv2",
        "addressdetails": "1",
        "limit": "8",
        "countrycodes": "br",
        "accept-language": "pt-BR",
        "viewbox": "-47.2,-23.1,-46.1,-24.2",
        "bounded": "1",
        "email": "a@b.org",
    }
    assert request.headers["User-Agent"] == USER_AGENT


async def test_email_is_omitted_when_not_configured(nominatim):
    requests = []
    await nominatim(respond(requests=requests)).search("MASP")
    assert "email" not in requests[0].url.params


async def test_results_become_labelled_candidates(nominatim):
    candidates = await nominatim(respond(MASP, STREET)).search("pinheiros")

    assert candidates == [
        {
            "label": "Museu de Arte de São Paulo",
            "neighborhood": "Bela Vista",
            "lat": -23.5614,
            "lng": -46.6559,
            "importance": 0.498,
        },
        {
            "label": "Rua dos Pinheiros",
            "neighborhood": "Pinheiros",
            "lat": -23.5677,
            "lng": -46.693,
            "importance": 0.0534,
        },
    ]


@pytest.mark.parametrize(
    ("item", "label"),
    [
        (
            {"address": {"road": "Rua Augusta", "house_number": "1500"}},
            "Rua Augusta, 1500",
        ),
        ({"name": "Rua Augusta", "address": {"road": "Rua Augusta"}}, "Rua Augusta"),
        ({"display_name": "Liberdade, São Paulo, Brasil"}, "Liberdade"),
    ],
)
def test_label_falls_back_to_road_then_display_name(item, label):
    (candidate,) = parse_candidates([{"lat": "-23.55", "lon": "-46.63", **item}])
    assert candidate["label"] == label
    assert candidate["neighborhood"] is None
    assert candidate["importance"] == 0.0


@pytest.mark.parametrize(
    "item",
    [
        "not an object",
        {"lon": "-46.63", "name": "Sem latitude"},
        {"lat": "nan", "lon": "-46.63", "name": "Não finito"},
        {"lat": "abc", "lon": "-46.63", "name": "Texto"},
        {"lat": True, "lon": "-46.63", "name": "Booleano"},
        {"lat": "-22.90", "lon": "-43.20", "name": "Rio de Janeiro"},
        {"lat": "-23.55", "lon": "-46.63", "address": {}},
    ],
)
def test_results_that_cannot_be_placed_are_dropped(item):
    assert parse_candidates([item]) == []


async def test_non_list_payload_is_an_invalid_response(nominatim):
    client = nominatim(lambda request: httpx.Response(200, json={"error": "x"}))
    with pytest.raises(ExternalAPIResponseError):
        await client.search("MASP")


async def test_same_query_is_answered_from_cache(nominatim):
    requests = []
    client = nominatim(respond(MASP, requests=requests))

    first = await client.search("Avenida  Paulista")
    second = await client.search("avenida paulista ")

    assert first == second
    assert len(requests) == 1


async def test_upstream_calls_are_at_least_one_second_apart(nominatim, clock, delays):
    client = nominatim(respond(MASP))

    await client.search("primeira")
    clock.now += 0.25
    await client.search("segunda")
    clock.now += 5
    await client.search("terceira")

    assert delays == [0.75]


async def test_upstream_failure_is_not_cached(nominatim):
    replies = [httpx.Response(500), httpx.Response(200, json=[MASP])]
    client = nominatim(lambda request: replies.pop(0))

    with pytest.raises(ExternalAPIStatusError):
        await client.search("MASP")
    assert len(await client.search("MASP")) == 1


async def test_disabled_feature_and_blank_query_fail_before_any_request(nominatim):
    requests = []
    client = nominatim(respond(requests=requests))
    with pytest.raises(ValueError, match="blank"):
        await client.search("   ")

    client.settings = Settings()
    with pytest.raises(ValueError, match="NOMINATIM_ENABLED"):
        await client.search("MASP")
    assert requests == []


def test_normalize_query_collapses_spaces_and_case():
    assert normalize_query("  Rua\tdos   PINHEIROS ") == "rua dos pinheiros"
